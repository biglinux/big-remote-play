"""Minimal JSON-over-HTTPS client shared by the provider REST APIs.

Security properties, all covered by tests:

* credentials travel only in request headers, never in the URL or argv;
* redirects are refused, so an ``Authorization`` header can never be replayed
  to another host;
* only ``https`` is accepted, except ``http`` to a loopback address when the
  caller explicitly allows it (a Headscale server on the same computer);
* every request has a timeout and a response-size limit;
* error details are redacted before they reach a caller.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
import ipaddress
import json
import socket
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .redaction import redact, redact_values

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
DEFAULT_TIMEOUT = 10.0
USER_AGENT = "BigRemotePlay"


class ApiErrorKind(str, Enum):
    AUTH = "auth"  # 401: credential missing, wrong, expired or revoked
    FORBIDDEN = "forbidden"  # 403: credential valid, permission (scope) missing
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    BAD_REQUEST = "bad_request"
    UNAVAILABLE = "unavailable"  # DNS, refused connection, TLS failure, 5xx
    TIMEOUT = "timeout"
    INVALID_RESPONSE = "invalid_response"
    REDIRECT = "redirect"
    INSECURE_URL = "insecure_url"


@dataclass(frozen=True)
class ApiResult:
    ok: bool
    status: int = 0
    data: Any = None
    error: ApiErrorKind | None = None
    detail: str = ""

    @classmethod
    def failure(cls, error: ApiErrorKind, detail: str = "", status: int = 0) -> "ApiResult":
        return cls(False, status=status, error=error, detail=redact(detail)[:300])


# (method, url, headers, body, timeout) -> (status, body bytes)
Transport = Callable[[str, str, Mapping[str, str], bytes | None, float], tuple[int, bytes]]


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 - urllib API
        return None  # urllib then raises HTTPError with the 3xx status


_OPENER = urllib.request.build_opener(_RefuseRedirects())


def _default_transport(method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
    try:
        with _OPENER.open(request, timeout=timeout) as response:  # nosec B310 - scheme validated by normalize_base_url
            return int(response.status), response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        try:
            payload = error.read(MAX_RESPONSE_BYTES + 1)
        except OSError:
            payload = b""
        return int(error.code), payload


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def normalize_base_url(value: str, *, allow_http_loopback: bool = False) -> str:
    """Validate an API origin and return it without a trailing slash.

    Raises ``ValueError`` for anything that is not a plain https origin
    (optionally with a path prefix): credentials in the URL, a query string,
    whitespace, or an unencrypted scheme to a non-loopback host.
    """
    text = (value or "").strip()
    if not text or any(character.isspace() or ord(character) < 32 for character in text):
        raise ValueError("invalid URL")
    candidate = text if "://" in text else f"https://{text}"
    parsed = urllib.parse.urlsplit(candidate)
    host = parsed.hostname or ""
    if not host or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("invalid URL")
    if parsed.scheme == "http":
        if not (allow_http_loopback and _is_loopback(host)):
            raise ValueError("insecure URL")
    elif parsed.scheme != "https":
        raise ValueError("invalid URL")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("invalid URL") from error
    if port is not None and not 1 <= port <= 65_535:
        raise ValueError("invalid URL")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))


def path_segment(value: object) -> str:
    """Percent-encode one URL path segment (``/`` and ``..`` cannot escape)."""
    text = str(value)
    if text in {"", ".", ".."}:
        raise ValueError("invalid path segment")
    return urllib.parse.quote(text, safe="")


class JsonHttpClient:
    """Send JSON requests to one API origin with caller-supplied auth headers."""

    def __init__(
        self,
        base_url: str,
        *,
        auth_headers: Callable[[], Mapping[str, str]] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: Transport | None = None,
        allow_http_loopback: bool = False,
    ) -> None:
        self.base_url = normalize_base_url(base_url, allow_http_loopback=allow_http_loopback)
        self._auth_headers = auth_headers or (lambda: {})
        self.timeout = timeout
        self._transport = transport or _default_transport

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, object] | None = None,
        json_body: Any = None,
        form: Mapping[str, str] | None = None,
        authenticated: bool = True,
    ) -> ApiResult:
        if not path.startswith("/"):
            path = "/" + path
        url = self.base_url + path
        if query:
            pairs = [(key, str(value)) for key, value in query.items() if value is not None]
            url += "?" + urllib.parse.urlencode(pairs)
        headers: dict[str, str] = {"Accept": "application/json", "User-Agent": USER_AGENT}
        body: bytes | None = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif form is not None:
            body = urllib.parse.urlencode(dict(form)).encode("ascii")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        secrets: list[str] = []
        if authenticated:
            try:
                auth = dict(self._auth_headers())
            except Exception as error:  # a credential lookup failure is an auth problem
                return ApiResult.failure(ApiErrorKind.AUTH, str(error))
            headers.update(auth)
            # Servers sometimes quote what they rejected; never pass it on.
            secrets = [part for value in auth.values() for part in str(value).split()]
        if form is not None:
            secrets.extend(str(value) for value in form.values())
        try:
            status, payload = self._transport(method, url, headers, body, self.timeout)
        except (socket.timeout, TimeoutError):
            return ApiResult.failure(ApiErrorKind.TIMEOUT)
        except urllib.error.URLError as error:
            reason = getattr(error, "reason", error)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                return ApiResult.failure(ApiErrorKind.TIMEOUT)
            return ApiResult.failure(ApiErrorKind.UNAVAILABLE, str(reason))
        except OSError as error:
            return ApiResult.failure(ApiErrorKind.UNAVAILABLE, str(error))
        result = self._interpret(status, payload)
        if result.detail and secrets:
            return ApiResult(result.ok, result.status, result.data, result.error, redact_values(result.detail, secrets))
        return result

    def get(self, path: str, **kwargs: Any) -> ApiResult:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> ApiResult:
        return self.request("POST", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> ApiResult:
        return self.request("DELETE", path, **kwargs)

    @staticmethod
    def _interpret(status: int, payload: bytes) -> ApiResult:
        if len(payload) > MAX_RESPONSE_BYTES:
            return ApiResult.failure(ApiErrorKind.INVALID_RESPONSE, "response too large", status)
        data: Any = None
        text = payload.decode("utf-8", errors="replace").strip()
        if text:
            try:
                data = json.loads(text)
            except ValueError:
                data = None
        if 200 <= status < 300:
            if text and data is None:
                return ApiResult.failure(ApiErrorKind.INVALID_RESPONSE, "response is not JSON", status)
            return ApiResult(True, status=status, data=data)
        detail = ""
        if isinstance(data, dict):
            detail = str(data.get("message") or data.get("error") or data.get("detail") or "")
        kind = {
            401: ApiErrorKind.AUTH,
            403: ApiErrorKind.FORBIDDEN,
            404: ApiErrorKind.NOT_FOUND,
            429: ApiErrorKind.RATE_LIMITED,
        }.get(status)
        if kind is None:
            if 300 <= status < 400:
                kind = ApiErrorKind.REDIRECT
            elif 400 <= status < 500:
                kind = ApiErrorKind.BAD_REQUEST
            else:
                kind = ApiErrorKind.UNAVAILABLE
        return ApiResult.failure(kind, detail, status)
