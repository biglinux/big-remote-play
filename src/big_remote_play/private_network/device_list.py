"""One device list per connection method, in one shape the UI can draw.

Tailscale, Headscale and ZeroTier report devices differently: the local
client knows its peers (Tailscale, Headscale) or only its own membership
(ZeroTier), and an administrative API, when configured, knows every member and
can remove one. These pure functions turn each source into
:class:`ProviderDevice` rows for *one* provider (and, for ZeroTier, one
network) — lists of different methods are never merged, so an address from
one network is never offered for another.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
import time

from .models import ConnectionState, PeerDevice, ProviderId, ProviderStatus

TAILSCALE_MACHINES = "https://login.tailscale.com/admin/machines"
ZEROTIER_NETWORK = "https://my.zerotier.com/network/{network_id}"


@dataclass(frozen=True)
class ProviderDevice:
    """A computer of one provider's network, as the UI shows it."""

    id: str  # what the provider's API removes (device id, node id, member id); "" when unknown
    name: str
    address: str  # the private address to copy; "" when the provider has none for it
    online: bool | None = None
    last_seen: float | None = None  # epoch seconds, when the provider says
    is_local: bool = False
    removable: bool = False
    os: str = ""
    waiting: bool = False  # joined, not approved yet


@dataclass(frozen=True)
class NetworkChoice:
    network_id: str
    name: str


@dataclass(frozen=True)
class DeviceListing:
    """The devices of one provider (one ZeroTier network), and what can be done with them."""

    provider: ProviderId
    devices: tuple[ProviderDevice, ...] = ()
    can_remove: bool = False
    complete: bool = True  # False: only this computer is known locally (ZeroTier without API)
    manage_url: str = ""  # the official page where devices are managed without an API
    networks: tuple[NetworkChoice, ...] = ()  # ZeroTier: every network this computer is in
    network_id: str = ""  # ZeroTier: the network listed
    problem: str = ""  # an API error sentence, already safe to show

    @property
    def local(self) -> ProviderDevice | None:
        return next((device for device in self.devices if device.is_local), None)


def parse_timestamp(value: str) -> float | None:
    """RFC 3339 as both APIs write it (``2026-10-06T12:00:00Z``), else ``None``."""
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    if "." in text:
        head, _dot, tail = text.partition(".")
        digits = "".join(ch for ch in tail if ch.isdigit())
        zone = tail[len(digits) :]
        text = f"{head}.{digits[:6]}{zone}"
    try:
        from datetime import datetime

        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _ordered(devices: Iterable[ProviderDevice]) -> tuple[ProviderDevice, ...]:
    """This computer first, then online devices, then the rest, by name."""
    return tuple(sorted(devices, key=lambda d: (not d.is_local, d.online is not True, d.name.casefold())))


def _peer(peer: PeerDevice, *, local: bool = False, device_id: str = "", removable: bool = False, last_seen: float | None = None) -> ProviderDevice:
    return ProviderDevice(
        id=device_id,
        name=peer.name or peer.best_address,
        address=peer.ipv4 or peer.ipv6,
        online=True if local else (False if peer.expired else peer.online),
        last_seen=last_seen,
        is_local=local,
        removable=removable and not local,
        os=peer.os,
        waiting=peer.authorized is False,
    )


def tailnet_listing(status: ProviderStatus, api_devices: Sequence | None = None, *, api_problem: str = "") -> DeviceListing:
    """Tailscale or Headscale: the local client's peers, with the API's ids when configured.

    ``api_devices`` are Tailscale ``ApiDevice`` or Headscale ``HeadscaleNode``
    objects; a device can be removed only when the API knows it.
    """
    provider = status.provider
    if not status.connected:
        return DeviceListing(provider, manage_url=TAILSCALE_MACHINES if provider is ProviderId.TAILSCALE else "", problem=api_problem)
    by_node: dict[str, object] = {}
    by_address: dict[str, object] = {}
    for item in api_devices or ():
        node_id = str(getattr(item, "node_id", "") or "")
        if node_id:
            by_node[node_id] = item
        for address in getattr(item, "addresses", ()) or ():
            by_address[address] = item

    def match(peer: PeerDevice):
        found = by_node.get(peer.node_id) if peer.node_id else None
        return found or next((by_address[a] for a in peer.addresses if a in by_address), None)

    def api_id(item) -> str:
        return str(getattr(item, "device_id", "") or getattr(item, "node_id", "") or "")

    devices = []
    if status.self_device is not None:
        devices.append(_peer(status.self_device, local=True))
    for peer in status.peers:
        item = match(peer)
        seen = parse_timestamp(str(getattr(item, "last_seen", "") or "")) if item is not None else None
        devices.append(_peer(peer, device_id=api_id(item) if item is not None else "", removable=item is not None, last_seen=seen))
    can_remove = api_devices is not None
    return DeviceListing(provider, _ordered(devices), can_remove=can_remove, manage_url=TAILSCALE_MACHINES if provider is ProviderId.TAILSCALE else "", problem=api_problem)


def zerotier_listing(status: ProviderStatus, network_id: str = "", members: Sequence | None = None, *, api_problem: str = "", now: float | None = None) -> DeviceListing:
    """One ZeroTier network: its members from Central when an API token is
    configured, otherwise only this computer (the local service knows no members)."""
    networks = tuple(NetworkChoice(network.network_id, network.name or network.network_id) for network in status.networks)
    chosen = network_id if any(choice.network_id == network_id for choice in networks) else (networks[0].network_id if networks else "")
    url = ZEROTIER_NETWORK.format(network_id=chosen) if chosen else "https://my.zerotier.com"
    if not chosen:
        return DeviceListing(ProviderId.ZEROTIER, manage_url=url, networks=networks, problem=api_problem)
    membership = next(network for network in status.networks if network.network_id == chosen)
    own_node = status.self_device.node_id if status.self_device else ""
    own = ProviderDevice(
        id=own_node,
        name=(status.self_device.name if status.self_device else "") or own_node,
        address=next((a for a in membership.addresses if ":" not in a), "") or (membership.addresses[0] if membership.addresses else ""),
        online=membership.state is ConnectionState.CONNECTED,
        is_local=True,
        waiting=membership.state is ConnectionState.NEEDS_AUTHORIZATION,
    )
    if members is None:
        return DeviceListing(ProviderId.ZEROTIER, (own,), complete=False, manage_url=url, networks=networks, network_id=chosen, problem=api_problem)
    clock = now if now is not None else time.time()
    devices = [own]
    for member in members:
        if member.node_id == own_node or getattr(member, "rejected", False):
            continue
        addresses = tuple(getattr(member, "ip_assignments", ()) or ())
        devices.append(
            ProviderDevice(
                id=member.node_id,
                name=member.name or member.node_id,
                address=next((a for a in addresses if ":" not in a), "") or (addresses[0] if addresses else ""),
                online=member.recently_seen(clock) if member.authorized else None,
                last_seen=member.last_seen,
                removable=True,
                waiting=not member.authorized,
            )
        )
    return DeviceListing(ProviderId.ZEROTIER, _ordered(devices), can_remove=True, manage_url=url, networks=networks, network_id=chosen, problem=api_problem)


@dataclass(frozen=True)
class SimpleState:
    """A provider's state in the five words of the simple interface."""

    key: str  # not_installed | off | connecting | connected | attention
    detail: str = field(default="")  # a stable code for the sentence under it


def simple_state(status: ProviderStatus) -> SimpleState:
    """Five states for people; the precise one stays in ``status``.

    A running service is not a connection: only the client reporting a working
    network is ``connected``.
    """
    from .models import Recovery

    if not status.installed or status.recovery is Recovery.INSTALL:
        return SimpleState("not_installed")
    if status.state is ConnectionState.CONNECTED:
        return SimpleState("connected")
    if status.state is ConnectionState.CONNECTING:
        return SimpleState("connecting")
    if status.recovery is Recovery.GRANT_ACCESS:
        return SimpleState("attention", "permission")
    if status.state is ConnectionState.NEEDS_AUTHENTICATION:
        return SimpleState("attention", "sign_in")
    if status.state is ConnectionState.NEEDS_AUTHORIZATION:
        return SimpleState("attention", "approval")
    if status.state is ConnectionState.ERROR:
        return SimpleState("attention", "error")
    if status.recovery is Recovery.START_SERVICE or status.recovery is Recovery.RECONNECT:
        return SimpleState("off", "stopped")
    if status.technical_detail.startswith("tailscaled is using"):
        return SimpleState("off", "other_product")
    return SimpleState("off", "not_set_up")


__all__ = ["DeviceListing", "NetworkChoice", "ProviderDevice", "SimpleState", "parse_timestamp", "simple_state", "tailnet_listing", "zerotier_listing"]
