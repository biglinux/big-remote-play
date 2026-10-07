"""A Headscale server: is it there, is it healthy, and setting one up here.

GTK-free and blocking: call from a worker.

* :func:`check_server` answers "can this computer use that server?" step by
  step — the name resolves, the port answers, HTTPS is valid, Headscale
  answers ``/health`` — so the interface can say which step failed in words.
* :class:`LocalServer` runs the reviewed PolicyKit helper that configures
  Headscale behind Caddy (automatic HTTPS) on this computer. The helper
  refuses to overwrite a Headscale or Caddy configuration it did not write.
* :class:`SetupStore` keeps the wizard's progress so it can continue after a
  restart. It holds choices and verified facts only, never a key.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
import http.client
import ipaddress
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import time
import urllib.parse

from big_remote_play.utils.script_protocol import parse_script_line
from big_remote_play.utils.secure_io import secure_write_text

from .diagnostics import tcp_outcome, valid_host
from .dns_check import normalize_domain

HELPER = "/usr/share/big-remote-play/scripts/headscale-server-helper.sh"
LOCAL_API = "http://127.0.0.1:8080"
DEFAULT_USER = "big-remote-play"
TIMEOUT = 6.0


# ── addresses ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ServerAddress:
    host: str  # a domain (ASCII) or an IP address, without brackets
    port: int = 443

    @property
    def is_ip(self) -> bool:
        try:
            ipaddress.ip_address(self.host)
            return True
        except ValueError:
            return False

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"https://{host}" if self.port == 443 else f"https://{host}:{self.port}"


def parse_server_address(text: str) -> ServerAddress:
    """``headscale.example.com``, ``https://…``, ``203.0.113.10`` or ``[2001:db8::1]:8443``.

    Raises ``ValueError`` for anything else, including ``http://`` (a
    Headscale server for other computers must use HTTPS).
    """
    value = (text or "").strip()
    if not value or any(character.isspace() for character in value):
        raise ValueError("empty address")
    candidate = value if "://" in value else f"https://{value}"
    parts = urllib.parse.urlsplit(candidate)
    if parts.scheme != "https" or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise ValueError("not an https server address")
    host = parts.hostname or ""
    try:
        port = parts.port or 443
    except ValueError as error:
        raise ValueError("invalid port") from error
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return ServerAddress(normalize_domain(host), port)
    if address.is_unspecified or address.is_multicast:
        raise ValueError("unusable address")
    return ServerAddress(str(address), port)


# ── checking a server ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class ServerCheck:
    """Each step is ``ok``, ``failed`` or ``skipped`` (not reached)."""

    address: ServerAddress | None
    dns: str = "skipped"
    port: str = "skipped"
    https: str = "skipped"
    headscale: str = "skipped"
    problem: str = ""  # "" | invalid | dns | refused | timeout | unreachable | tls_invalid | tls_failed | not_headscale | error
    resolved: tuple[str, ...] = ()
    version: str = ""
    certificate_issuer: str = ""
    certificate_expires: str = ""
    details: tuple[str, ...] = ()

    @property
    def healthy(self) -> bool:
        return self.headscale == "ok"


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS to ``host`` (certificate and SNI) through another IP address.

    Used to check a server on this computer by its public name without
    depending on the router sending this computer's own traffic back to it.
    """

    def __init__(self, host: str, port: int, *, connect_host: str, context: ssl.SSLContext, timeout: float) -> None:
        super().__init__(host, port, context=context, timeout=timeout)
        self._connect_host = connect_host
        self._tls_context = context

    def connect(self) -> None:  # noqa: D401 - http.client API
        sock = socket.create_connection((self._connect_host, self.port), self.timeout)
        # An IP address here is matched against the certificate's IP entries.
        self.sock = self._tls_context.wrap_socket(sock, server_hostname=self.host)


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _issuer(cert: dict) -> str:
    for entry in cert.get("issuer") or ():
        for key, value in entry:
            if key in ("organizationName", "commonName"):
                return str(value)
    return ""


def check_server(
    text: str,
    *,
    connect_host: str = "",
    resolve: Callable[[str, int], Sequence[str]] | None = None,
    port_check: Callable[[str, int], str] | None = None,
    https_get: Callable[[ServerAddress, str, str], tuple[int, bytes, dict]] | None = None,
) -> ServerCheck:
    """Can a Tailscale client use this Headscale server? Read-only, bounded.

    ``connect_host`` checks a server on this computer (``127.0.0.1``) by its
    public name; the certificate is still verified for that name, so a
    success also proves the certificate authority reached it from outside.
    """
    try:
        address = parse_server_address(text)
    except ValueError as error:
        return ServerCheck(None, problem="invalid", details=(str(error),))
    details: list[str] = [f"server: {address.url}"]
    target = connect_host
    resolved: tuple[str, ...] = ()
    if address.is_ip:
        dns = "skipped"
        target = target or address.host
    else:
        try:
            resolved = tuple(dict.fromkeys((resolve or _resolve)(address.host, address.port)))
        except OSError as error:
            details.append(f"dns: {error}")
            return ServerCheck(address, dns="failed", problem="dns", details=tuple(details))
        if not resolved:
            return ServerCheck(address, dns="failed", problem="dns", details=tuple(details))
        dns = "ok"
        details.append("dns: " + ", ".join(resolved))
        target = target or resolved[0]
    outcome = (port_check or tcp_outcome)(target, address.port)
    details.append(f"tcp {target}:{address.port}: {outcome}")
    if outcome != "open":
        problem = outcome if outcome in ("refused", "timeout", "unreachable") else "error"
        return ServerCheck(address, dns=dns, port="failed", problem=problem, resolved=resolved, details=tuple(details))
    try:
        status, body, cert = (https_get or _https_get)(address, "/health", target)
    except ssl.SSLCertVerificationError as error:
        details.append(f"tls: {getattr(error, 'verify_message', '') or error}")
        return ServerCheck(address, dns=dns, port="ok", https="failed", problem="tls_invalid", resolved=resolved, details=tuple(details))
    except (ssl.SSLError, ConnectionResetError) as error:
        details.append(f"tls: {error}")
        return ServerCheck(address, dns=dns, port="ok", https="failed", problem="tls_failed", resolved=resolved, details=tuple(details))
    except (OSError, http.client.HTTPException) as error:
        details.append(f"https: {error}")
        return ServerCheck(address, dns=dns, port="ok", https="failed", problem="error", resolved=resolved, details=tuple(details))
    issuer = _issuer(cert)
    expires = str(cert.get("notAfter") or "")
    details.append(f"https: certificate from {issuer or 'unknown'}, valid until {expires or 'unknown'}")
    details.append(f"/health: HTTP {status}")
    healthy = False
    if 200 <= status < 300:
        try:
            healthy = json.loads(body.decode("utf-8", errors="replace") or "{}").get("status") == "pass"
        except (ValueError, AttributeError):
            healthy = False
    if not healthy:
        return ServerCheck(
            address, dns=dns, port="ok", https="ok", headscale="failed", problem="not_headscale", resolved=resolved, certificate_issuer=issuer, certificate_expires=expires, details=tuple(details)
        )
    version = ""
    try:
        status, body, _cert = (https_get or _https_get)(address, "/version", target)
        if 200 <= status < 300:
            version = str(json.loads(body.decode("utf-8", errors="replace")).get("version") or "")
    except (OSError, ValueError, AttributeError, http.client.HTTPException, ssl.SSLError):
        version = ""
    if version:
        details.append(f"version: {version}")
    return ServerCheck(address, dns=dns, port="ok", https="ok", headscale="ok", resolved=resolved, version=version, certificate_issuer=issuer, certificate_expires=expires, details=tuple(details))


def _resolve(host: str, port: int) -> list[str]:
    return [str(info[4][0]) for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


def _https_get(address: ServerAddress, path: str, connect_host: str) -> tuple[int, bytes, dict]:
    context = ssl.create_default_context()
    connection = _PinnedHTTPSConnection(address.host, address.port, connect_host=connect_host or address.host, context=context, timeout=TIMEOUT)
    try:
        connection.request("GET", path, headers={"User-Agent": "BigRemotePlay", "Accept": "application/json"})
        response = connection.getresponse()
        body = response.read(64 * 1024)
        cert = connection.sock.getpeercert() if connection.sock is not None else {}
        return response.status, body, cert or {}
    finally:
        connection.close()


# ── a server on this computer ──────────────────────────────────────────────


@dataclass(frozen=True)
class HelperResult:
    ok: bool
    result: str = ""  # the helper's RESULT marker: ok | not_installed | other_server | …
    data: dict = field(default_factory=dict)  # non-secret markers
    secret: str = ""  # API_KEY, kept out of ``data`` and of every log line
    lines: tuple[str, ...] = ()


class LocalServer:
    """Headscale + Caddy on this computer, through the PolicyKit helper."""

    COMMANDS = ("configure", "firewall", "create-user", "create-apikey", "status", "unconfigure", "hosts-pin", "hosts-unpin")

    def __init__(self, *, helper: str = HELPER, popen: Callable[..., subprocess.Popen] = subprocess.Popen, exists: Callable[[str], bool] = os.path.exists) -> None:
        self.helper = helper
        self._popen = popen
        self._exists = exists

    @property
    def available(self) -> bool:
        """The helper is installed (it is not run from a source checkout)."""
        return self._exists(self.helper)

    def argv(self, command: str, *args: str) -> list[str]:
        if command not in self.COMMANDS:
            raise ValueError(f"unknown helper command: {command}")
        return ["pkexec", self.helper, command, *args]

    def run(self, command: str, *args: str, on_text: Callable[[str], None] | None = None, on_phase: Callable[[float], None] | None = None) -> HelperResult:
        if not self.available:
            return HelperResult(False, "not_available")
        try:
            process = self._popen(self.argv(command, *args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True)
        except OSError as error:
            return HelperResult(False, "error", lines=(str(error),))
        data: dict[str, str] = {}
        secret = ""
        lines: list[str] = []
        assert process.stdout is not None
        for raw in process.stdout:
            kind = parse_script_line(raw)
            if kind[0] == "data":
                if kind[1] == "API_KEY":
                    secret = kind[2]  # never logged, never shown
                else:
                    data[kind[1]] = kind[2]
            elif kind[0] == "phase":
                if on_phase is not None:
                    on_phase(kind[1])
            elif kind[1]:
                lines.append(kind[1])
                if on_text is not None:
                    on_text(kind[1])
        code = process.wait()
        result = data.get("RESULT", "")
        if code in (126, 127):  # pkexec: not authorized, or the dialog was dismissed
            return HelperResult(False, "not_authorized", data, lines=tuple(lines))
        return HelperResult(code == 0 and result == "ok", result or ("ok" if code == 0 else "failed"), data, secret, tuple(lines))


# ── progress of the setup wizard ───────────────────────────────────────────

STEPS = ("server", "domain", "dns", "https", "headscale", "this_computer", "another_device")


@dataclass(frozen=True)
class SetupProgress:
    """What the person chose and what was verified. Never a key or password."""

    mode: str = ""  # "" | this_computer | another_server
    address_kind: str = ""  # "" | domain | ip
    has_domain: bool | None = None
    dns_manager: str = ""  # "" | cloudflare | other
    registrar: str = ""  # "" | digitalplat
    domain: str = ""  # host name of the server, ASCII
    server_ip: str = ""  # where the A record points: this computer's public IPv4 or the server's
    server_ipv6: str = ""  # optional AAAA, only when IPv6 really reaches this computer
    nameservers: tuple[str, ...] = ()  # the two Cloudflare showed, when the person pasted them
    user: str = ""  # Headscale user that owns this computer
    local_listen: str = ""  # this computer's server: where Headscale listens (the helper picks a free port)
    # "Another server" whose name leads to this very computer (a container,
    # another service here): checked and joined through 127.0.0.1, because a
    # router without hairpin NAT answers this computer's own public address itself.
    on_this_computer: bool = False
    done: dict = field(default_factory=dict)  # step → epoch seconds when it was verified

    @property
    def server_url(self) -> str:
        host = self.domain or self.server_ip
        if not host:
            return ""
        return f"https://[{host}]" if ":" in host else f"https://{host}"

    @property
    def local_api(self) -> str:
        """The local server's own API address (loopback, plain HTTP).

        The helper only ever listens on ``127.0.0.1:<port>``; anything else in
        the saved progress is ignored, so the API key never goes in clear text
        to another host.
        """
        host, _, port = self.local_listen.rpartition(":")
        if host == "127.0.0.1" and port.isdigit() and 0 < int(port) < 65536:
            return f"http://127.0.0.1:{int(port)}"
        return LOCAL_API

    @property
    def steps(self) -> tuple[str, ...]:
        """The steps that apply to the choices made so far."""
        if self.address_kind == "ip":
            return tuple(step for step in STEPS if step not in ("domain", "dns"))
        return STEPS

    def is_done(self, step: str) -> bool:
        return step in self.done

    @property
    def next_step(self) -> str:
        return next((step for step in self.steps if step not in self.done), "")

    @property
    def started(self) -> bool:
        return bool(self.mode)

    @property
    def complete(self) -> bool:
        """Everything but inviting another device: that one is optional."""
        return all(step in self.done for step in self.steps if step != "another_device")

    def mark(self, step: str, *, now: float | None = None) -> "SetupProgress":
        if step not in STEPS:
            raise ValueError(step)
        return replace(self, done={**self.done, step: now if now is not None else time.time()})

    def unmark(self, *steps: str) -> "SetupProgress":
        return replace(self, done={key: value for key, value in self.done.items() if key not in steps})


_FIELDS = {name for name in SetupProgress.__dataclass_fields__}


class SetupStore:
    """``headscale-setup.json`` (0600) in Big Remote Play's settings folder."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        if self._path is not None:
            return self._path
        from big_remote_play import paths

        return Path(paths.CONFIG_DIR) / "headscale-setup.json"

    def load(self) -> SetupProgress:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return SetupProgress()
        if not isinstance(payload, dict):
            return SetupProgress()
        values = {key: value for key, value in payload.items() if key in _FIELDS}
        try:
            progress = SetupProgress(**values)
        except TypeError:
            return SetupProgress()
        done = {str(key): float(value) for key, value in (progress.done or {}).items() if key in STEPS and isinstance(value, (int, float))}
        has_domain = progress.has_domain if isinstance(progress.has_domain, bool) else None
        return replace(progress, nameservers=tuple(str(name) for name in progress.nameservers or ()), done=done, has_domain=has_domain)

    def save(self, progress: SetupProgress) -> None:
        payload = asdict(progress)
        payload["nameservers"] = list(progress.nameservers)
        secure_write_text(str(self.path), json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def web_ports_in_use(runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> tuple[int, ...]:
    """Ports 80/443 that another program already serves (read-only, as this user).

    HTTPS on this computer needs both for Caddy. When Caddy's own service is
    running they are taken by it and a site can be added; otherwise any
    listener there is someone else (a web server, a container).
    """
    try:
        if runner(["systemctl", "is-active", "--quiet", "caddy.service"], timeout=3, check=False).returncode == 0:
            return ()
        result = runner(["ss", "-ltnH"], capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.SubprocessError):
        return ()
    busy: set[int] = set()
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        port = parts[3].rsplit(":", 1)[-1]
        if port in ("80", "443"):
            busy.add(int(port))
    return tuple(sorted(busy))


def valid_user_name(name: str) -> bool:
    text = (name or "").strip()
    return bool(text) and len(text) <= 63 and text[0].isalnum() and all(character.isalnum() or character in "._-" for character in text) and text.isascii() and text == text.lower()


def preauth_key_kind(text: str) -> str:
    """``preauth`` for a key that registers a computer, ``api`` for an
    administrative key (never accepted where a pre-auth key is asked),
    ``invalid`` otherwise."""
    value = (text or "").strip()
    if not value or any(character.isspace() for character in value) or len(value) > 256:
        return "invalid"
    if value.startswith("hskey-api-"):
        return "api"
    if value.startswith(("hskey-auth-", "tskey-auth-")):
        return "preauth"
    # Before 0.28 pre-auth keys were 48 hexadecimal characters.
    if len(value) >= 32 and all(character in "0123456789abcdef" for character in value):
        return "preauth"
    return "invalid"


__all__ = [
    "DEFAULT_USER",
    "LOCAL_API",
    "HelperResult",
    "LocalServer",
    "STEPS",
    "ServerAddress",
    "ServerCheck",
    "SetupProgress",
    "SetupStore",
    "check_server",
    "parse_server_address",
    "preauth_key_kind",
    "valid_host",
    "valid_user_name",
    "web_ports_in_use",
]
