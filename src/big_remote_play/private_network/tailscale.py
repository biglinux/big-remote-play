"""Tailscale (and Headscale, which uses the same client) status and diagnostics.

The CLI remains the source of truth. ``tailscale status --json`` decides the
connection state; only ``BackendState == "Running"`` is reported as connected.
Connecting, account switching and sign-in stay in
:class:`big_remote_play.utils.vpn_accounts.VPNAccountManager`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import json
import re
from typing import Any

from big_remote_play.utils.vpn_accounts import CommandResult

from .diagnostics import valid_host
from .models import ConnectionState, PathReport, PeerDevice, ProviderCapabilities, ProviderId, ProviderStatus, Recovery

Runner = Callable[..., CommandResult]

# BackendState values from ipn/backend.go.
_STATE_MAP = {
    "Running": ConnectionState.CONNECTED,
    "Starting": ConnectionState.CONNECTING,
    "NeedsLogin": ConnectionState.NEEDS_AUTHENTICATION,
    "NeedsMachineAuth": ConnectionState.NEEDS_AUTHORIZATION,
    "Stopped": ConnectionState.DISCONNECTED,
    "NoState": ConnectionState.CONNECTING,
    "InUseOtherUser": ConnectionState.ERROR,
}

# The latency is a Go duration: "0s", "523µs", "12ms", "1.2s", "1m2s".
_PONG_RE = re.compile(r"pong from (?P<name>\S+) \((?P<ip>[^)]+)\)(?: via (?P<via>.+?))? in (?P<duration>(?:[0-9.]+(?:ns|us|µs|μs|ms|s|m|h))+)")
_DURATION_PART_RE = re.compile(r"([0-9.]+)(ns|us|µs|μs|ms|s|m|h)")
_UNIT_MS = {"ns": 1e-6, "us": 1e-3, "µs": 1e-3, "μs": 1e-3, "ms": 1.0, "s": 1000.0, "m": 60_000.0, "h": 3_600_000.0}


def go_duration_ms(text: str) -> float:
    """Milliseconds in a Go duration string such as ``1.5ms`` or ``1m2s``."""
    return sum(float(value) * _UNIT_MS[unit] for value, unit in _DURATION_PART_RE.findall(text))


_DERP_RE = re.compile(r"DERP\((?P<region>[^)]*)\)")


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _short_name(node: dict[str, Any]) -> str:
    dns = _text(node.get("DNSName")).rstrip(".")
    return _text(node.get("HostName")) or (dns.split(".")[0] if dns else "")


def _device(node: dict[str, Any], provider: str, *, is_self: bool = False, users: dict[str, Any] | None = None) -> PeerDevice:
    addresses = tuple(address for address in node.get("TailscaleIPs") or [] if isinstance(address, str))
    user = ""
    if users is not None:
        entry = users.get(str(node.get("UserID"))) or {}
        user = _text(entry.get("LoginName")) if isinstance(entry, dict) else ""
    expired = bool(node.get("Expired"))
    return PeerDevice(
        name=_short_name(node) or (addresses[0] if addresses else ""),
        addresses=addresses,
        provider=provider,
        dns_name=_text(node.get("DNSName")).rstrip("."),
        online=False if expired else (bool(node.get("Online")) if "Online" in node else None),
        expired=expired,
        os=_text(node.get("OS")),
        is_self=is_self,
        node_id=_text(node.get("ID")),
        last_seen=_text(node.get("LastSeen")),
        user=user,
    )


def parse_status(payload: str, *, provider: ProviderId = ProviderId.TAILSCALE) -> ProviderStatus:
    """Turn ``tailscale status --json`` into a :class:`ProviderStatus`."""
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return ProviderStatus(provider, ConnectionState.ERROR, technical_detail="invalid status JSON")
    if not isinstance(data, dict):
        return ProviderStatus(provider, ConnectionState.ERROR, technical_detail="invalid status JSON")

    backend = _text(data.get("BackendState"))
    state = _STATE_MAP.get(backend, ConnectionState.ERROR)
    auth_url = _text(data.get("AuthURL"))
    raw_users = data.get("User")
    users = raw_users if isinstance(raw_users, dict) else {}
    raw_self = data.get("Self")
    self_node = raw_self if isinstance(raw_self, dict) else None
    self_device = _device(self_node, provider.value, is_self=True, users=users) if self_node else None

    peers: list[PeerDevice] = []
    raw_peers = data.get("Peer")
    for node in (raw_peers or {}).values() if isinstance(raw_peers, dict) else ():
        if isinstance(node, dict):
            peers.append(_device(node, provider.value, users=users))
    peers.sort(key=lambda peer: (not peer.online, peer.name.lower()))

    raw_tailnet = data.get("CurrentTailnet")
    tailnet = raw_tailnet if isinstance(raw_tailnet, dict) else {}
    network_name = _text(tailnet.get("Name")) or _text(data.get("MagicDNSSuffix"))
    account = self_device.user if self_device else ""

    recovery = {
        ConnectionState.NEEDS_AUTHENTICATION: Recovery.SIGN_IN,
        ConnectionState.DISCONNECTED: Recovery.RECONNECT,
        ConnectionState.NEEDS_AUTHORIZATION: Recovery.AUTHORIZE_DEVICE,
    }.get(state)
    health = [message for message in data.get("Health") or [] if isinstance(message, str)]
    detail = f"BackendState: {backend or 'unknown'}"
    if health:
        detail += "\n" + "\n".join(health[:5])
    return ProviderStatus(
        provider,
        state,
        network_name=network_name,
        account=account,
        self_device=self_device,
        # A stopped client still lists the peers it knew; they are not reachable.
        peers=tuple(peers) if state is ConnectionState.CONNECTED else (),
        technical_detail=detail,
        auth_url=auth_url if state is ConnectionState.NEEDS_AUTHENTICATION else "",
        recovery=recovery,
    )


def parse_ping(output: str, returncode: int = 0) -> PathReport:
    """Interpret ``tailscale ping`` output; the last pong describes the path."""
    matches = list(_PONG_RE.finditer(output or ""))
    if not matches:
        return PathReport(False)
    last = matches[-1]
    via = (last.group("via") or "").strip()
    latency = go_duration_ms(last.group("duration"))
    derp = _DERP_RE.search(via)
    if derp:
        return PathReport(True, "relay", latency, relay=derp.group("region"))
    if via.lower().startswith("peer-relay"):
        return PathReport(True, "peer_relay", latency, endpoint=via)
    return PathReport(True, "direct" if via else "unknown", latency, endpoint=via)


def is_tailscale_control(url: str) -> bool:
    """True for Tailscale's own coordination servers (``*.tailscale.com``)."""
    import urllib.parse

    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host == "tailscale.com" or host.endswith(".tailscale.com")


TAILSCALE_CAPABILITIES_BASE = dict(
    can_join_network=True,
    can_leave_network=True,
    can_switch_account=True,
    can_measure_path=True,
)


def capabilities(*, api_configured: bool, provider: ProviderId = ProviderId.TAILSCALE) -> ProviderCapabilities:
    if provider is ProviderId.HEADSCALE:
        return ProviderCapabilities(
            **TAILSCALE_CAPABILITIES_BASE,
            can_self_host=True,
            can_use_api=api_configured,
            can_create_auth_key=api_configured,
            can_manage_devices=api_configured,
            can_remove_device=api_configured,
            can_authorize_member=api_configured,
        )
    return ProviderCapabilities(
        **TAILSCALE_CAPABILITIES_BASE,
        can_use_api=api_configured,
        can_invite_user=api_configured,
        can_share_machine=api_configured,
        can_create_auth_key=api_configured,
        can_manage_devices=api_configured,
        can_remove_device=api_configured,
        can_authorize_member=api_configured,
    )


class TailscaleCli:
    """Read-only Tailscale CLI queries with bounded timeouts."""

    def __init__(self, command: Sequence[str], runner: Runner) -> None:
        self._command = list(command)
        self._runner = runner

    def status(self, provider: ProviderId = ProviderId.TAILSCALE) -> ProviderStatus:
        result = self._runner([*self._command, "status", "--json"], timeout=10)
        if result.returncode != 0 and not result.stdout.strip().startswith("{"):
            text = f"{result.stdout}\n{result.stderr}".lower()
            if "failed to connect" in text or "connect: no such file" in text or "tailscaled" in text:
                return ProviderStatus(provider, ConnectionState.UNAVAILABLE, technical_detail="tailscaled is not running", recovery=Recovery.START_SERVICE)
            if result.returncode == 127:
                return ProviderStatus(provider, ConnectionState.UNAVAILABLE, installed=False, recovery=Recovery.INSTALL)
            return ProviderStatus(provider, ConnectionState.ERROR, technical_detail=(result.stderr or result.stdout).strip()[:300])
        return parse_status(result.stdout, provider=provider)

    def control_url(self) -> str:
        """The control server this client uses (``""`` when it cannot be read).

        ``debug prefs`` is the only CLI output that names it. Its format is not
        a stable interface, so any failure simply returns nothing.
        """
        result = self._runner([*self._command, "debug", "prefs"], timeout=10)
        if result.returncode != 0:
            return ""
        try:
            prefs = json.loads(result.stdout)
        except (TypeError, ValueError):
            return ""
        return _text(prefs.get("ControlURL")) if isinstance(prefs, dict) else ""

    def ping(self, target: str, *, count: int = 3, timeout_seconds: int = 3) -> PathReport:
        """Ping a peer through the tailnet (never an ICMP ping of the internet)."""
        if not valid_host(target):
            return PathReport(False)
        argv = [*self._command, "ping", f"--c={int(count)}", f"--timeout={int(timeout_seconds)}s", target]
        result = self._runner(argv, timeout=count * timeout_seconds + 5)
        return parse_ping(result.stdout + "\n" + result.stderr, result.returncode)
