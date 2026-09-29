"""ZeroTier status on this computer, built from the local ``zerotier-cli``.

Membership and the managed addresses come from the local service; network
administration (members, authorization, creation) needs ZeroTier Central and
lives in :mod:`.zerotier_api`.
"""

from __future__ import annotations

from big_remote_play.utils.vpn_accounts import VPNAccountManager, ZeroTierNetwork

from .models import ConnectionState, OverlayNetwork, PeerDevice, ProviderCapabilities, ProviderId, ProviderStatus, Recovery

# Status strings from zerotier-cli listnetworks (node/Network.hpp).
_STATE = {
    "OK": ConnectionState.CONNECTED,
    "REQUESTING_CONFIGURATION": ConnectionState.CONNECTING,
    "ACCESS_DENIED": ConnectionState.NEEDS_AUTHORIZATION,
    "NOT_FOUND": ConnectionState.ERROR,
    "PORT_ERROR": ConnectionState.ERROR,
    "CLIENT_TOO_OLD": ConnectionState.ERROR,
    "AUTHENTICATION_REQUIRED": ConnectionState.NEEDS_AUTHENTICATION,
}


def network_state(network: ZeroTierNetwork) -> ConnectionState:
    state = _STATE.get(network.status.upper(), ConnectionState.ERROR)
    if state is ConnectionState.CONNECTED and not network.assigned_addresses:
        # Authorized but without a managed IP: nothing can reach this PC yet.
        return ConnectionState.NEEDS_AUTHORIZATION
    return state


def managed_ips(network: ZeroTierNetwork) -> tuple[str, ...]:
    """Assigned addresses without their prefix length (``10.1.2.3/24`` → ``10.1.2.3``)."""
    return tuple(address.split("/")[0] for address in network.assigned_addresses if address)


def capabilities(*, api_configured: bool) -> ProviderCapabilities:
    return ProviderCapabilities(
        can_join_network=True,
        can_leave_network=True,
        can_create_network=api_configured,
        can_authorize_member=api_configured,
        can_manage_devices=api_configured,
        can_remove_device=api_configured,
        can_use_api=api_configured,
    )


def status(manager: VPNAccountManager, *, installed: bool = True, service_running: bool = True) -> ProviderStatus:
    if not installed:
        return ProviderStatus(ProviderId.ZEROTIER, ConnectionState.UNAVAILABLE, installed=False, recovery=Recovery.INSTALL)
    if not service_running:
        return ProviderStatus(ProviderId.ZEROTIER, ConnectionState.UNAVAILABLE, technical_detail="zerotier-one is not running", recovery=Recovery.START_SERVICE)
    listing = manager.list_zerotier_networks(allow_privileged=False)
    if listing.needs_privilege:
        return ProviderStatus(
            ProviderId.ZEROTIER,
            ConnectionState.ERROR,
            technical_detail="zerotier-cli: authtoken.secret is not readable by this user",
            recovery=Recovery.GRANT_ACCESS,
        )
    if listing.error:
        return ProviderStatus(ProviderId.ZEROTIER, ConnectionState.ERROR, technical_detail=listing.error[:300])
    node = manager.zerotier_info()
    networks = tuple(
        OverlayNetwork(
            network_id=network.network_id,
            name=network.display_name,
            state=network_state(network),
            addresses=managed_ips(network),
            network_type=network.network_type,
            raw_status=network.status,
        )
        for network in listing.networks
    )
    if not networks:
        return ProviderStatus(ProviderId.ZEROTIER, ConnectionState.DISCONNECTED, recovery=Recovery.JOIN_NETWORK, self_device=_self(node.address, ()), technical_detail=_detail(node.address, ()))
    ready = [network for network in networks if network.state is ConnectionState.CONNECTED]
    primary = ready[0] if ready else networks[0]
    state = primary.state
    recovery = Recovery.AUTHORIZE_DEVICE if state is ConnectionState.NEEDS_AUTHORIZATION else None
    return ProviderStatus(
        ProviderId.ZEROTIER,
        state,
        network_name=primary.name,
        network_id=primary.network_id,
        self_device=_self(node.address, primary.addresses),
        networks=networks,
        technical_detail=_detail(node.address, networks),
        recovery=recovery,
    )


def _self(node_id: str, addresses: tuple[str, ...]) -> PeerDevice:
    import socket

    return PeerDevice(name=socket.gethostname(), addresses=addresses, provider="zerotier", online=True, is_self=True, node_id=node_id)


def _detail(node_id: str, networks: tuple[OverlayNetwork, ...]) -> str:
    lines = [f"Node ID: {node_id or 'unknown'}"]
    lines.extend(f"{network.network_id}: {network.raw_status}" for network in networks)
    return "\n".join(lines)
