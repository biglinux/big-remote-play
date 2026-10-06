"""Headscale control-server REST client (``/api/v1``, checked against 0.29.4).

Two different keys exist and must never be confused:

* an **API key** authenticates Big Remote Play to the administrative API
  (``Authorization: Bearer …``). It is stored in the keyring;
* a **pre-auth key** registers one computer on the network. It is created
  here on request, shown once, and never stored.

Version notes: ``/version`` exists since 0.27; pre-auth keys take the numeric
user ID since 0.26; ``/api/v1/routes`` was removed in 0.26 in favour of
``POST /api/v1/node/{id}/approve_routes``. IDs are serialised as strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Any

from .http import ApiErrorKind, ApiResult, JsonHttpClient, Transport, path_segment

# 0.29 prints ``…/register/hskey-authreq-<24 URL-safe characters>``; older
# servers used other identifiers, which are accepted as typed.
_REGISTRATION_KEY_RE = re.compile(r"(?:/register/)?(?P<key>hskey-authreq-[A-Za-z0-9_-]{24}|(?:nodekey:|mkey:)?[A-Za-z0-9_-]{8,128})/?$")


@dataclass(frozen=True)
class HeadscaleUser:
    user_id: str
    name: str
    display_name: str = ""


@dataclass(frozen=True)
class HeadscaleNode:
    node_id: str
    name: str
    user: str
    addresses: tuple[str, ...]
    online: bool | None = None
    last_seen: str = ""
    available_routes: tuple[str, ...] = ()
    approved_routes: tuple[str, ...] = ()


_AUTH_ID_RE = re.compile(r"hskey-authreq-[A-Za-z0-9_-]{24}")
_BARE_AUTH_ID_RE = re.compile(r"[A-Za-z0-9_-]{24}")


def registration_key(text: str) -> str:
    """The registration code in whatever the other device shows.

    Accepted: the whole command Headscale suggests (``headscale auth register
    --auth-id hskey-authreq-… --user USERNAME``), the sign-in link
    (``…/register/<code>``), the code itself, or only its 24 characters after
    ``hskey-authreq-`` (what someone types from a phone screen). Older
    servers' identifiers are accepted as typed.
    """
    value = (text or "").strip()
    found = _AUTH_ID_RE.search(value)
    if found:
        return found.group(0)
    if _BARE_AUTH_ID_RE.fullmatch(value):
        return f"hskey-authreq-{value}"
    if any(character.isspace() for character in value):
        return ""
    match = _REGISTRATION_KEY_RE.search(value)
    return match.group("key") if match else ""


def registered_name(result: ApiResult) -> str:
    """The name of the node a successful registration created."""
    node = result.data.get("node") if result.ok and isinstance(result.data, dict) else None
    if not isinstance(node, dict):
        return ""
    return str(node.get("givenName") or node.get("name") or "")


class HeadscaleApi:
    def __init__(self, server_url: str, api_key: str = "", *, transport: Transport | None = None, timeout: float = 10.0) -> None:
        self._api_key = api_key
        # A server on this computer may listen on plain http://127.0.0.1.
        self._client = JsonHttpClient(server_url, auth_headers=lambda: {"Authorization": f"Bearer {self._api_key}"}, transport=transport, timeout=timeout, allow_http_loopback=True)
        self.server_url = self._client.base_url

    # ── unauthenticated ────────────────────────────────────────────────────
    def health(self) -> ApiResult:
        return self._client.get("/health", authenticated=False)

    def version(self) -> tuple[str, ApiResult]:
        result = self._client.get("/version", authenticated=False)
        version = str(result.data.get("version") or "") if result.ok and isinstance(result.data, dict) else ""
        return version, result

    # ── authenticated ──────────────────────────────────────────────────────
    def test(self) -> ApiResult:
        if not self._api_key:
            return ApiResult.failure(ApiErrorKind.AUTH, "no API key")
        return self._client.get("/api/v1/user")

    def users(self) -> tuple[list[HeadscaleUser], ApiResult]:
        result = self._client.get("/api/v1/user")
        items = result.data.get("users", []) if result.ok and isinstance(result.data, dict) else []
        users = [HeadscaleUser(str(item.get("id")), str(item.get("name") or ""), str(item.get("displayName") or "")) for item in items if isinstance(item, dict) and item.get("id") is not None]
        return users, result

    def create_user(self, name: str) -> tuple[HeadscaleUser | None, ApiResult]:
        """Create a user (a namespace before 0.23); its devices form one network."""
        result = self._client.post("/api/v1/user", json_body={"name": name})
        item = result.data.get("user") if result.ok and isinstance(result.data, dict) else None
        if not isinstance(item, dict) or item.get("id") is None:
            return None, result
        return HeadscaleUser(str(item.get("id")), str(item.get("name") or ""), str(item.get("displayName") or "")), result

    def nodes(self) -> tuple[list[HeadscaleNode], ApiResult]:
        result = self._client.get("/api/v1/node")
        items = result.data.get("nodes", []) if result.ok and isinstance(result.data, dict) else []
        return [node for node in (self._node(item) for item in items) if node is not None], result

    @staticmethod
    def _node(item: Any) -> HeadscaleNode | None:
        if not isinstance(item, dict) or item.get("id") is None:
            return None
        raw_user = item.get("user")
        user: dict = raw_user if isinstance(raw_user, dict) else {}
        return HeadscaleNode(
            node_id=str(item["id"]),
            name=str(item.get("givenName") or item.get("name") or ""),
            user=str(user.get("name") or ""),
            addresses=tuple(str(address) for address in item.get("ipAddresses") or [] if address),
            online=item.get("online") if isinstance(item.get("online"), bool) else None,
            last_seen=str(item.get("lastSeen") or ""),
            available_routes=tuple(str(route) for route in item.get("availableRoutes") or []),
            approved_routes=tuple(str(route) for route in item.get("approvedRoutes") or []),
        )

    def create_preauth_key(self, user_id: str, *, reusable: bool = False, ephemeral: bool = False, hours: int = 1) -> tuple[str, ApiResult]:
        """A single-use key that expires in ``hours`` (default one hour)."""
        if not str(user_id).isdigit():
            return "", ApiResult.failure(ApiErrorKind.BAD_REQUEST, "invalid user id")
        expiration = (datetime.now(timezone.utc) + timedelta(hours=max(1, min(int(hours), 24 * 90)))).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = {"user": str(user_id), "reusable": bool(reusable), "ephemeral": bool(ephemeral), "expiration": expiration}
        result = self._client.post("/api/v1/preauthkey", json_body=body)
        created = result.data.get("preAuthKey") if result.ok and isinstance(result.data, dict) else None
        return (str(created.get("key") or "") if isinstance(created, dict) else ""), result

    def register_node(self, user_name: str, key: str) -> ApiResult:
        """Approve a computer that is waiting at the browser sign-in step.

        ``user_name`` is the user *name* (pre-auth keys take the numeric ID).
        ``/api/v1/auth/register`` is the current endpoint; servers without it
        answer 404 and get the older ``/api/v1/node/register`` form.
        """
        clean = registration_key(key)
        if not clean or not user_name:
            return ApiResult.failure(ApiErrorKind.BAD_REQUEST, "invalid registration key")
        result = self._client.post("/api/v1/auth/register", json_body={"user": user_name, "authId": clean})
        if result.error is ApiErrorKind.NOT_FOUND:
            result = self._client.post("/api/v1/node/register", query={"user": user_name, "key": clean})
        return result

    def delete_node(self, node_id: str) -> ApiResult:
        return self._client.delete(f"/api/v1/node/{path_segment(node_id)}")

    def expire_node(self, node_id: str) -> ApiResult:
        return self._client.post(f"/api/v1/node/{path_segment(node_id)}/expire")

    def approve_routes(self, node_id: str, routes: list[str]) -> ApiResult:
        return self._client.post(f"/api/v1/node/{path_segment(node_id)}/approve_routes", json_body={"routes": list(routes)})
