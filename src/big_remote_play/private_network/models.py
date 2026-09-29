"""Plain data models shared by the private-network providers and the UI.

State is modelled explicitly: a provider reports one :class:`ConnectionState`
instead of a handful of unrelated booleans, and the UI renders that state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import ipaddress


class ProviderId(str, Enum):
    TAILSCALE = "tailscale"
    HEADSCALE = "headscale"
    ZEROTIER = "zerotier"

    @property
    def display_name(self) -> str:
        # Product names are trademarks and are never translated.
        return {"tailscale": "Tailscale", "headscale": "Headscale", "zerotier": "ZeroTier"}[self.value]


class ConnectionState(str, Enum):
    """What this computer's membership in a private network looks like now."""

    UNAVAILABLE = "unavailable"  # client not installed, or its service is missing
    DISCONNECTED = "disconnected"  # installed and usable, not on a network
    NEEDS_AUTHENTICATION = "needs_authentication"  # sign-in required
    CONNECTING = "connecting"  # the client is still starting or configuring
    NEEDS_AUTHORIZATION = "needs_authorization"  # joined, waiting for an administrator
    CONNECTED = "connected"  # the client itself reports a working network
    ERROR = "error"  # the client answered with something unusable


class Recovery(str, Enum):
    """The single most useful next step for a problem state."""

    INSTALL = "install"
    START_SERVICE = "start_service"
    SIGN_IN = "sign_in"
    AUTHORIZE_DEVICE = "authorize_device"
    UPDATE_CREDENTIAL = "update_credential"
    RECONNECT = "reconnect"
    JOIN_NETWORK = "join_network"
    GRANT_ACCESS = "grant_access"  # let this desktop user control the local client


@dataclass(frozen=True)
class ProviderCapabilities:
    """What Big Remote Play can really do with a provider in its current setup.

    The UI shows an action only when its capability is true. Values that need
    an administrative credential are computed with the credential's presence,
    so an unconfigured API never produces a button that can only fail.
    """

    can_create_network: bool = False
    can_join_network: bool = False
    can_leave_network: bool = False
    can_invite_user: bool = False
    can_share_machine: bool = False
    can_authorize_member: bool = False
    can_create_auth_key: bool = False
    can_use_api: bool = False
    can_manage_devices: bool = False
    can_remove_device: bool = False
    can_self_host: bool = False
    can_switch_account: bool = False
    can_measure_path: bool = False


@dataclass(frozen=True)
class PeerDevice:
    """One computer on a private network, as the provider reports it."""

    name: str
    addresses: tuple[str, ...] = ()
    provider: str = ""
    dns_name: str = ""
    online: bool | None = None
    os: str = ""
    is_self: bool = False
    node_id: str = ""
    authorized: bool | None = None
    last_seen: str = ""
    user: str = ""
    # The provider still lists the device, but its sign-in expired: it cannot
    # be reached until someone signs in again on that device.
    expired: bool = False

    @property
    def ipv4(self) -> str:
        return next((address for address in self.addresses if _is_ipv4(address)), "")

    @property
    def ipv6(self) -> str:
        return next((address for address in self.addresses if _is_ipv6(address)), "")

    @property
    def best_address(self) -> str:
        """The address to hand to Moonlight: IPv4 first, then IPv6, then DNS."""
        return self.ipv4 or self.ipv6 or self.dns_name


@dataclass(frozen=True)
class ProviderStatus:
    """A snapshot of one provider on this computer."""

    provider: ProviderId
    state: ConnectionState
    installed: bool = True
    network_name: str = ""
    network_id: str = ""
    account: str = ""
    self_device: PeerDevice | None = None
    peers: tuple[PeerDevice, ...] = ()
    technical_detail: str = ""
    auth_url: str = ""
    recovery: Recovery | None = None
    networks: tuple["OverlayNetwork", ...] = ()

    @property
    def connected(self) -> bool:
        return self.state is ConnectionState.CONNECTED

    @property
    def online_peers(self) -> tuple[PeerDevice, ...]:
        return tuple(peer for peer in self.peers if peer.online)

    @property
    def device_count(self) -> int:
        """Devices this PC can see, itself included."""
        return len(self.peers) + (1 if self.self_device is not None else 0)

    @property
    def reachable_address(self) -> str:
        """This computer's private address, or ``""`` when not connected.

        Only a connected provider has an address another computer can use;
        a stale address from a stopped client is worse than none.
        """
        if not self.connected or self.self_device is None:
            return ""
        return self.self_device.best_address


@dataclass(frozen=True)
class OverlayNetwork:
    """A network this computer is a member of (ZeroTier can have several)."""

    network_id: str
    name: str
    state: ConnectionState
    addresses: tuple[str, ...] = ()
    network_type: str = ""
    raw_status: str = ""


@dataclass(frozen=True)
class HostCandidate:
    """A computer Connect can offer, with where the suggestion came from."""

    name: str
    address: str
    port: int
    provider: str = "lan"  # tailscale | headscale | zerotier | lan | manual
    source: str = "provider"  # provider | history
    online: bool | None = None
    dns_name: str = ""
    addresses: tuple[str, ...] = ()
    last_connected: float | None = None  # epoch seconds, from the session history

    def as_host(self) -> dict:
        """The dictionary shape GuestView already connects with."""
        return {
            "name": self.name,
            "ip": self.address,
            "port": self.port,
            "provider": self.provider,
            "source": self.source,
            "addresses": [address for address in self.addresses if address != self.address],
            "dns_name": self.dns_name,
        }


@dataclass(frozen=True)
class PathReport:
    """How packets travel to a peer, when the provider can tell."""

    reachable: bool
    kind: str = "unknown"  # direct | relay | peer_relay | unknown
    latency_ms: float | None = None
    relay: str = ""
    endpoint: str = ""


@dataclass(frozen=True)
class HostDiagnosis:
    """Result of the quick checks run before offering Connect."""

    network_ok: bool | None = None
    host_online: bool | None = None
    sunshine_ok: bool | None = None
    path: PathReport | None = None
    problem: str = ""  # "" | network_down | host_offline | sunshine_missing | timeout
    details: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ready(self) -> bool:
        return bool(self.sunshine_ok) and not self.problem


def _is_ipv4(value: str) -> bool:
    try:
        return isinstance(ipaddress.ip_address(value.split("%")[0]), ipaddress.IPv4Address)
    except ValueError:
        return False


def _is_ipv6(value: str) -> bool:
    try:
        return isinstance(ipaddress.ip_address(value.split("%")[0]), ipaddress.IPv6Address)
    except ValueError:
        return False
