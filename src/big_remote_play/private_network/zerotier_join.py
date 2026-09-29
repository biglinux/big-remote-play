"""Joining a ZeroTier network, as a small state machine.

``zerotier-cli join`` only *asks* to join: the network controller then sends
a configuration, refuses this computer until its owner authorizes it, or says
the network does not exist. The answer arrives seconds (or, for approval,
minutes) later in ``listnetworks``. This module turns that asynchronous
exchange into one :class:`JoinPhase` at a time.

Nothing here reads translated text. Results come from exit codes, the CLI's
protocol line (``<HTTP status> <command> <body>``, e.g. ``0 join connection
failed`` when the service cannot be reached) and the JSON the service returns.
No GTK and no threads: the caller runs :meth:`ZeroTierJoiner.join` in a worker
and receives each new state through ``report``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from enum import Enum
import json
import re
import time

from big_remote_play.utils.vpn_accounts import CommandResult, VPNAccountManager, ZeroTierNetwork, valid_zerotier_network_id

from .redaction import redact

ZEROTIER_UNIT = "zerotier-one"
# Owners usually approve within minutes; the page keeps checking by itself
# after this first, faster wait.
JOIN_WAIT_SECONDS = 45.0
POLL_SECONDS = 1.5
SERVICE_START_WAIT_SECONDS = 15.0
_CLI_STATUS_RE = re.compile(r"^\s*(\d{1,3})\s+([a-z]+)\b")


class JoinPhase(str, Enum):
    CHECKING = "checking"
    NOT_INSTALLED = "not_installed"
    STARTING_SERVICE = "starting_service"
    SERVICE_STOPPED = "service_stopped"  # not running and could not be started
    ASKING_PERMISSION = "asking_permission"
    NEEDS_PERMISSION = "needs_permission"  # zerotier-cli may not talk to the service
    INVALID_ID = "invalid_id"
    JOINING = "joining"
    WAITING_CONFIGURATION = "waiting_configuration"  # REQUESTING_CONFIGURATION
    NODE_OFFLINE = "node_offline"  # the service cannot reach ZeroTier on the internet
    WAITING_AUTHORIZATION = "waiting_authorization"  # ACCESS_DENIED
    WAITING_ADDRESS = "waiting_address"  # authorized, no address assigned yet
    CONNECTED = "connected"
    NOT_FOUND = "not_found"
    PORT_ERROR = "port_error"  # the virtual network adapter could not be created
    CLIENT_TOO_OLD = "client_too_old"
    SIGN_IN_REQUIRED = "sign_in_required"  # AUTHENTICATION_REQUIRED (SSO networks)
    NOT_JOINED = "not_joined"  # this computer is not a member (left, or never joined)
    FAILED = "failed"

    @property
    def waiting(self) -> bool:
        """The controller has not decided yet; checking again can change it."""
        return self in _WAITING

    @property
    def busy(self) -> bool:
        return self in (JoinPhase.CHECKING, JoinPhase.STARTING_SERVICE, JoinPhase.ASKING_PERMISSION, JoinPhase.JOINING)

    @property
    def problem(self) -> bool:
        return not (self.waiting or self.busy or self is JoinPhase.CONNECTED)


_WAITING = frozenset({JoinPhase.WAITING_CONFIGURATION, JoinPhase.NODE_OFFLINE, JoinPhase.WAITING_AUTHORIZATION, JoinPhase.WAITING_ADDRESS})

# listnetworks "status" (ZeroTier node/Network.hpp) → phase.
_STATUS_PHASE = {
    "REQUESTING_CONFIGURATION": JoinPhase.WAITING_CONFIGURATION,
    "ACCESS_DENIED": JoinPhase.WAITING_AUTHORIZATION,
    "NOT_FOUND": JoinPhase.NOT_FOUND,
    "PORT_ERROR": JoinPhase.PORT_ERROR,
    "CLIENT_TOO_OLD": JoinPhase.CLIENT_TOO_OLD,
    "AUTHENTICATION_REQUIRED": JoinPhase.SIGN_IN_REQUIRED,
}


@dataclass(frozen=True)
class PeerSummary:
    total: int = 0
    direct: int = 0
    relayed: int = 0
    best_latency_ms: int | None = None


@dataclass(frozen=True)
class JoinSnapshot:
    """Everything the page shows about one join attempt; never a secret."""

    phase: JoinPhase
    network_id: str = ""
    node_id: str = ""
    node_online: bool | None = None
    service_running: bool | None = None
    network_name: str = ""
    raw_status: str = ""
    addresses: tuple[str, ...] = ()
    device: str = ""
    join_result: str = ""
    detail: str = ""
    peers: PeerSummary | None = None
    version: str = ""
    newly_joined: bool = False

    def technical_lines(self) -> list[str]:
        """What a helper needs to see, in stable English keys (not translated)."""

        def online(value: bool | None, yes: str, no: str) -> str:
            return "unknown" if value is None else (yes if value else no)

        lines = [
            "Provider: ZeroTier",
            f"Network: {self.network_id or '-'}",
            f"Node: {self.node_id or 'unknown'}",
            f"Service: {online(self.service_running, 'running', 'stopped')}",
            f"Daemon: {online(self.node_online, 'ONLINE', 'OFFLINE')}",
        ]
        if self.version:
            lines.append(f"Version: {self.version}")
        if self.join_result:
            lines.append(f"Join result: {self.join_result}")
        lines.append(f"Network state: {self.raw_status or '-'}")
        lines.append(f"Phase: {self.phase.value}")
        if self.addresses:
            lines.append(f"Addresses: {', '.join(self.addresses)}")
        if self.device:
            lines.append(f"Interface: {self.device}")
        if self.peers is not None:
            peers = self.peers
            latency = f", best latency {peers.best_latency_ms} ms" if peers.best_latency_ms is not None else ""
            lines.append(f"Peers: {peers.total} ({peers.direct} direct, {peers.relayed} relayed{latency})")
        if self.detail:
            lines.append(f"Detail: {redact(self.detail)[:300]}")
        return lines


def cli_status(output: str) -> tuple[int, str] | None:
    """``(status, command)`` from zerotier-cli's protocol line, if it printed one."""
    for line in (output or "").splitlines():
        match = _CLI_STATUS_RE.match(line)
        if match:
            return int(match.group(1)), match.group(2)
    return None


def phase_for_failed_command(result: CommandResult) -> JoinPhase:
    """Why a zerotier-cli command did not succeed (never by reading prose)."""
    if result.returncode == 127:
        return JoinPhase.NOT_INSTALLED
    status = cli_status(f"{result.stdout}\n{result.stderr}")
    if status is not None:
        code = status[0]
        if code == 0:  # the CLI could not open a connection to the service
            return JoinPhase.SERVICE_STOPPED
        if code in (401, 403):
            return JoinPhase.NEEDS_PERMISSION
        if code == 400:
            return JoinPhase.INVALID_ID
    if result.returncode == 126:  # pkexec: authorization refused or dismissed
        return JoinPhase.NEEDS_PERMISSION
    return JoinPhase.FAILED


def phase_for_network(network: ZeroTierNetwork | None, *, node_online: bool | None = None) -> JoinPhase:
    if network is None:
        return JoinPhase.NOT_JOINED
    status = network.status.upper()
    if status == "OK":
        return JoinPhase.CONNECTED if network.assigned_addresses else JoinPhase.WAITING_ADDRESS
    phase = _STATUS_PHASE.get(status, JoinPhase.FAILED)
    if phase is JoinPhase.WAITING_CONFIGURATION and node_online is False:
        # No controller can answer while the node itself is offline.
        return JoinPhase.NODE_OFFLINE
    return phase


def network_from_join_output(stdout: str) -> ZeroTierNetwork | None:
    """The network object ``zerotier-cli -j join`` prints on success."""
    try:
        payload = json.loads(stdout or "")
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    network_id = str(payload.get("nwid") or payload.get("id") or "").lower()
    if not valid_zerotier_network_id(network_id):
        return None
    addresses = payload.get("assignedAddresses")
    return ZeroTierNetwork(
        network_id=network_id,
        name=str(payload.get("name") or ""),
        status=str(payload.get("status") or "REQUESTING_CONFIGURATION"),
        assigned_addresses=tuple(str(value) for value in addresses) if isinstance(addresses, list) else (),
        device=str(payload.get("portDeviceName") or ""),
        network_type=str(payload.get("type") or ""),
    )


def controller_of(network_id: str) -> str:
    """A network's controller node: the first 10 hex digits of its ID."""
    return (network_id or "").lower()[:10]


def summarize_peers(payload: object, *, controllers: Iterable[str] = ()) -> PeerSummary:
    """Other computers (``LEAF`` peers) and whether each has a direct path.

    ZeroTier's infrastructure is not counted: ``PLANET``/``MOON`` roots, and
    the network controllers, which are also listed as ``LEAF`` (seen for
    real: on a computer with no other device online, every leaf was a
    controller). A leaf without an active path is reached through a relay.
    """
    excluded = {controller.lower() for controller in controllers}
    if not isinstance(payload, list):
        return PeerSummary()
    total = direct = 0
    latencies: list[int] = []
    for peer in payload:
        if not isinstance(peer, dict) or str(peer.get("role") or "").upper() != "LEAF":
            continue
        if str(peer.get("address") or "").lower() in excluded:
            continue
        total += 1
        raw_paths = peer.get("paths")
        paths = raw_paths if isinstance(raw_paths, list) else []
        if any(isinstance(path, dict) and path.get("active") and not path.get("expired") for path in paths):
            direct += 1
        latency = peer.get("latency")
        if isinstance(latency, int) and latency >= 0:
            latencies.append(latency)
    return PeerSummary(total, direct, total - direct, min(latencies) if latencies else None)


class ZeroTierJoiner:
    """Join, then follow the controller's answer until it is decided.

    Everything external is injected: the account manager (which owns every
    zerotier-cli call and the one privileged start), the installed/running
    probes, the clock and ``sleep``.
    """

    def __init__(
        self,
        manager: VPNAccountManager,
        *,
        installed: Callable[[], bool],
        service_running: Callable[[], bool],
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.manager = manager
        self._installed = installed
        self._service_running = service_running
        self._sleep = sleep
        self._clock = clock

    # ── reading ─────────────────────────────────────────────────────────────
    def check(self, network_id: str, *, with_peers: bool = True) -> JoinSnapshot:
        """The current state of one network, without joining or changing anything."""
        network_id = (network_id or "").strip().lower()
        base = JoinSnapshot(JoinPhase.CHECKING, network_id=network_id)
        if not self._safe(self._installed):
            return replace(base, phase=JoinPhase.NOT_INSTALLED, service_running=False)
        if not self._safe(self._service_running):
            return replace(base, phase=JoinPhase.SERVICE_STOPPED, service_running=False)
        base = replace(base, service_running=True)
        node = self.manager.zerotier_info()
        if node.needs_privilege:
            return replace(base, phase=JoinPhase.NEEDS_PERMISSION, detail=node.error)
        if not node.address and node.error:
            return replace(base, phase=phase_for_failed_command(CommandResult(1, node.error)), detail=node.error)
        base = replace(base, node_id=node.address, node_online=node.online, version=node.version)
        listing = self.manager.list_zerotier_networks(allow_privileged=False)
        if listing.needs_privilege:
            return replace(base, phase=JoinPhase.NEEDS_PERMISSION, detail=listing.error)
        if listing.error:
            return replace(base, phase=phase_for_failed_command(CommandResult(1, listing.error)), detail=listing.error)
        network = next((item for item in listing.networks if item.network_id == network_id), None)
        controllers = [controller_of(item.network_id) for item in listing.networks]
        return self._with_network(replace(base, peers=self._peers(controllers) if with_peers else None), network)

    def _peers(self, controllers: Iterable[str] = ()) -> PeerSummary | None:
        lister = getattr(self.manager, "list_zerotier_peers", None)
        if not callable(lister):
            return None
        try:
            return summarize_peers(lister(), controllers=controllers)
        except Exception:  # diagnostics never break the join
            return None

    @staticmethod
    def _with_network(snapshot: JoinSnapshot, network: ZeroTierNetwork | None) -> JoinSnapshot:
        if network is None:
            return replace(snapshot, phase=JoinPhase.NOT_JOINED, raw_status="")
        return replace(
            snapshot,
            phase=phase_for_network(network, node_online=snapshot.node_online),
            raw_status=network.status.upper(),
            network_name=network.display_name if network.display_name != network.network_id else "",
            addresses=tuple(address.split("/")[0] for address in network.assigned_addresses),
            device=network.device,
        )

    @staticmethod
    def _safe(probe: Callable[[], bool]) -> bool:
        try:
            return bool(probe())
        except Exception:
            return False

    # ── joining ─────────────────────────────────────────────────────────────
    def join(
        self,
        network_id: str,
        *,
        report: Callable[[JoinSnapshot], None] = lambda _snapshot: None,
        cancelled: Callable[[], bool] = lambda: False,
        wait: float = JOIN_WAIT_SECONDS,
    ) -> JoinSnapshot:
        network_id = "".join((network_id or "").split()).replace("-", "").lower()
        snapshot = JoinSnapshot(JoinPhase.CHECKING, network_id=network_id)
        if not valid_zerotier_network_id(network_id):
            return replace(snapshot, phase=JoinPhase.INVALID_ID)
        if not self._safe(self._installed):
            return replace(snapshot, phase=JoinPhase.NOT_INSTALLED, service_running=False)
        report(snapshot)

        if not self._safe(self._service_running):
            # The person asked to join; the service is the part that was off.
            # This is the one privileged step, and PolicyKit asks first.
            snapshot = replace(snapshot, phase=JoinPhase.STARTING_SERVICE, service_running=False)
            report(snapshot)
            started = self.manager.start_service(ZEROTIER_UNIT)
            if started.returncode != 0:
                return replace(snapshot, phase=JoinPhase.SERVICE_STOPPED, detail=(started.stderr or started.stdout).strip())
            if not self._wait_for_service(cancelled):
                if not self._safe(self._service_running):
                    return replace(snapshot, phase=JoinPhase.SERVICE_STOPPED, detail=(started.stderr or started.stdout).strip())
                # systemd runs it, yet the CLI never reached it: not "stopped".
                return replace(snapshot, phase=JoinPhase.FAILED, service_running=True, detail="zerotier-one is running but did not answer zerotier-cli")
        snapshot = replace(snapshot, service_running=True)

        node = self.manager.zerotier_info()
        if node.needs_privilege:
            # Reading the network state needs this user to reach the service.
            # Allowing it once is better than a password prompt on every check.
            report(replace(snapshot, phase=JoinPhase.ASKING_PERMISSION))
            granted = self.manager.grant_zerotier_user_access()
            node = self.manager.zerotier_info() if granted.returncode == 0 else node
            if node.needs_privilege or not node.address:
                return replace(snapshot, phase=JoinPhase.NEEDS_PERMISSION, detail=(granted.stderr or node.error).strip())
        elif not node.address and node.error:
            return replace(snapshot, phase=phase_for_failed_command(CommandResult(1, node.error)), detail=node.error)
        snapshot = replace(snapshot, node_id=node.address, node_online=node.online, version=node.version)
        if cancelled():
            return snapshot

        before = self.manager.list_zerotier_networks(allow_privileged=False)
        already_member = any(item.network_id == network_id for item in before.networks)
        snapshot = replace(snapshot, phase=JoinPhase.JOINING, newly_joined=not already_member)
        report(snapshot)
        result = self.manager.join_zerotier_network(network_id, allow_privileged=True)
        first_line = next((line.strip() for line in f"{result.stdout}\n{result.stderr}".splitlines() if line.strip()), "")
        if result.returncode != 0:
            return replace(snapshot, phase=phase_for_failed_command(result), join_result=redact(first_line)[:160], detail=redact(result.stderr or result.stdout))
        joined = network_from_join_output(result.stdout)
        # With -j a successful join prints the network object, not a status line.
        snapshot = replace(snapshot, join_result="accepted" if joined is not None or first_line.startswith("{") else redact(first_line)[:160])
        if joined is not None:
            snapshot = self._with_network(snapshot, joined)
            report(snapshot)
        return self._follow(snapshot, report=report, cancelled=cancelled, wait=wait)

    def _wait_for_service(self, cancelled: Callable[[], bool]) -> bool:
        """Until the service answers, or runs but refuses this user (asked next)."""
        deadline = self._clock() + SERVICE_START_WAIT_SECONDS
        while self._clock() < deadline and not cancelled():
            node = self.manager.zerotier_info()
            if node.address:
                return True
            # Right after a first start this user cannot read the service
            # token yet; that is a permission to ask for, not a stopped service.
            if node.needs_privilege and self._safe(self._service_running):
                return True
            self._sleep(0.5)
        return False

    def _follow(self, snapshot: JoinSnapshot, *, report: Callable[[JoinSnapshot], None], cancelled: Callable[[], bool], wait: float) -> JoinSnapshot:
        """Read listnetworks until the controller has answered (or ``wait`` ends)."""
        deadline = self._clock() + wait
        while not cancelled():
            current = self.check(snapshot.network_id, with_peers=False)
            current = replace(current, join_result=snapshot.join_result, newly_joined=snapshot.newly_joined)
            if current.phase is JoinPhase.NOT_JOINED:
                # listnetworks can lag the join by a moment.
                current = replace(current, phase=JoinPhase.WAITING_CONFIGURATION)
            if current.phase is not snapshot.phase or current.addresses != snapshot.addresses:
                report(current)
            snapshot = current
            phase = snapshot.phase
            if phase is JoinPhase.CONNECTED or phase is JoinPhase.WAITING_AUTHORIZATION or phase.problem:
                break
            if self._clock() >= deadline:
                break
            self._sleep(POLL_SECONDS)
        if snapshot.phase is JoinPhase.NOT_FOUND and snapshot.newly_joined:
            # A mistyped code must not leave a dead membership behind.
            self.manager.leave_zerotier_network(snapshot.network_id, allow_privileged=False)
        return replace(snapshot, peers=self._peers([controller_of(snapshot.network_id)]))


__all__ = [
    "JOIN_WAIT_SECONDS",
    "JoinPhase",
    "JoinSnapshot",
    "PeerSummary",
    "ZeroTierJoiner",
    "cli_status",
    "controller_of",
    "network_from_join_output",
    "phase_for_failed_command",
    "phase_for_network",
    "summarize_peers",
]
