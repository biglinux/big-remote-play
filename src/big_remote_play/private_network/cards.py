"""What each card of **Connect your devices** shows, from one provider only.

Pure functions over :class:`ProviderStatus` and :class:`DeviceListing`; the
service gathers them, the UI draws them. A card never borrows a value from
another provider: Tailscale's tailnet, ZeroTier's network and Headscale's
server each come from their own status.

Counting rule, the same for the three providers: **total** is every device of
that network *including this computer*; **online** counts this computer too
while its connection works. A count that could not be read is ``None`` and the
card says so — it is never shown as zero.
"""

from __future__ import annotations

from dataclasses import dataclass
import urllib.parse

from .device_list import DeviceListing, SimpleState, simple_state
from .models import ConnectionState, ProviderId, ProviderStatus, Recovery


@dataclass(frozen=True)
class DeviceCount:
    total: int | None = None
    online: int | None = None
    problem: str = ""  # "" | api_missing (ZeroTier without a token) | api_failed | not_connected

    @property
    def known(self) -> bool:
        return self.total is not None and self.online is not None


@dataclass(frozen=True)
class CardSummary:
    provider: ProviderId
    state: SimpleState
    switch_on: bool  # the connection is active and usable, not merely a running service
    configured: bool  # signed in / joined / has a saved account: the switch can turn it on directly
    network: str = ""  # tailnet or ZeroTier network name; for Headscale the server's host
    address: str = ""  # this computer's private address on that network
    devices: DeviceCount = DeviceCount(problem="not_connected")
    more_networks: int = 0  # ZeroTier: other networks this computer is also in
    other_active: ProviderId | None = None  # Tailscale/Headscale: the other one owns the shared app now
    setup_incomplete: bool = False  # Headscale: the setup wizard was started and not finished


def server_host(url: str) -> str:
    """``https://gaming.example.com`` → ``gaming.example.com``."""
    try:
        return urllib.parse.urlsplit(url if "://" in url else f"https://{url}").hostname or ""
    except ValueError:
        return ""


def _tailnet_count(status: ProviderStatus) -> DeviceCount:
    if not status.connected or status.self_device is None:
        return DeviceCount(problem="not_connected")
    online = sum(1 for peer in status.peers if peer.online)
    return DeviceCount(total=len(status.peers) + 1, online=online + 1)


def _zerotier_count(listing: DeviceListing | None) -> DeviceCount:
    if listing is None:
        return DeviceCount(problem="api_failed")
    if listing.problem:
        return DeviceCount(problem="api_failed")
    if not listing.complete:
        return DeviceCount(problem="api_missing")
    if not listing.devices:
        return DeviceCount(problem="not_connected")
    return DeviceCount(total=len(listing.devices), online=sum(1 for device in listing.devices if device.online))


def configured(status: ProviderStatus, *, saved_profile: bool = False) -> bool:
    """Is there something the switch can turn on without a setup step?"""
    if not status.installed:
        return False
    if status.provider is ProviderId.ZEROTIER:
        return bool(status.networks)
    if status.connected or status.state in (ConnectionState.CONNECTING, ConnectionState.NEEDS_AUTHORIZATION):
        return True
    if status.state is ConnectionState.NEEDS_AUTHENTICATION:
        return False
    return saved_profile or status.recovery in (Recovery.RECONNECT, Recovery.START_SERVICE)


def summarize(
    status: ProviderStatus,
    *,
    listing: DeviceListing | None = None,
    network_id: str = "",
    headscale_server: str = "",
    saved_profile: bool = False,
    setup_incomplete: bool = False,
) -> CardSummary:
    state = simple_state(status)
    other: ProviderId | None = None
    if status.technical_detail.startswith("tailscaled is using"):
        other = ProviderId.HEADSCALE if status.provider is ProviderId.TAILSCALE else ProviderId.TAILSCALE
    if status.provider is ProviderId.ZEROTIER:
        networks = list(status.networks)
        chosen = next((network for network in networks if network.network_id == network_id), None) or next(
            (network for network in networks if network.network_id == status.network_id), networks[0] if networks else None
        )
        name = (chosen.name or chosen.network_id) if chosen is not None else ""
        address = ""
        if chosen is not None and chosen.state is ConnectionState.CONNECTED:
            address = next((item for item in chosen.addresses if ":" not in item), "") or (chosen.addresses[0] if chosen.addresses else "")
        devices = _zerotier_count(listing) if status.connected else DeviceCount(problem="not_connected")
        return CardSummary(
            status.provider,
            state,
            switch_on=status.connected,
            configured=configured(status),
            network=name,
            address=address if status.connected else "",
            devices=devices,
            more_networks=max(0, len(networks) - 1),
        )
    if status.provider is ProviderId.HEADSCALE:
        network = server_host(headscale_server)
    else:
        network = status.network_name if other is None else ""
    return CardSummary(
        status.provider,
        state,
        switch_on=status.connected,
        configured=configured(status, saved_profile=saved_profile),
        network=network,
        address=status.reachable_address,
        devices=_tailnet_count(status),
        other_active=other,
        setup_incomplete=setup_incomplete and not status.connected,
    )


__all__ = ["CardSummary", "DeviceCount", "configured", "server_host", "summarize"]
