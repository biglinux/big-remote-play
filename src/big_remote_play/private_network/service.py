"""The facade the UI uses for everything private-network related.

Every method blocks (CLI calls, HTTP, D-Bus to the keyring) and must be called
from a worker thread. Results are plain data from :mod:`.models`; the UI never
sees a subprocess, an HTTP header or a credential.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
import logging
from types import SimpleNamespace

from big_remote_play.integration_contracts import SUNSHINE_DEFAULT_BASE_PORT
from big_remote_play.utils.secret_store import SecretStoreUnavailable
from big_remote_play.utils.system_check import SystemCheck
from big_remote_play.utils.vpn_accounts import CommandResult, VPNAccountManager

from . import tailscale as tailscale_provider
from . import zerotier as zerotier_provider
from .credentials import CredentialKind, CredentialStore
from .diagnostics import NetworkFacts, local_network_facts, probe_sunshine, valid_host
from .headscale_api import HeadscaleApi
from .history import SessionHistory
from .http import ApiErrorKind, ApiResult, Transport, normalize_base_url
from .tailscale_api import TailscaleApi
from .zerotier_api import ZeroTierCentral
from .models import ConnectionState, HostCandidate, HostDiagnosis, PeerDevice, ProviderCapabilities, ProviderId, ProviderStatus

_log = logging.getLogger("big-remoteplay")


def _installed(check: Callable[[], bool]) -> bool:
    try:
        return bool(check())
    except Exception:
        return False


_PROVIDER_ORDER = {
    ProviderId.TAILSCALE: 0,
    ProviderId.ZEROTIER: 1,
    ProviderId.HEADSCALE: 2,
}


def recommended_status(statuses: Iterable[ProviderStatus], preferred: ProviderId | None = None) -> ProviderStatus:
    """Choose the least disruptive connection method for the simple UI.

    A working connection always wins.  Otherwise an installed method that only
    needs recovery or sign-in wins over installing something new.  ``preferred``
    breaks ties but never replaces a connection that already works.
    """
    available = list(statuses)
    if not available:
        return ProviderStatus(ProviderId.TAILSCALE, ConnectionState.UNAVAILABLE, installed=False, recovery=None)

    def readiness(status: ProviderStatus) -> tuple[int, int, int]:
        if status.connected:
            level = 0
        elif status.installed and (status.recovery is not None or status.state is not ConnectionState.UNAVAILABLE):
            level = 1
        elif status.installed:
            level = 2
        else:
            level = 3
        preferred_rank = 0 if status.provider is preferred else 1
        return level, preferred_rank, _PROVIDER_ORDER[status.provider]

    return min(available, key=readiness)


class PrivateNetworkService:
    def __init__(
        self,
        system_check: SystemCheck | None = None,
        *,
        manager: VPNAccountManager | None = None,
        credentials: CredentialStore | None = None,
        history: SessionHistory | None = None,
        transport: Transport | None = None,
        sunshine_probe: Callable[..., object] = probe_sunshine,
        network_facts: Callable[[], NetworkFacts] = local_network_facts,
    ) -> None:
        self.system_check = system_check or SystemCheck()
        self.manager = manager or VPNAccountManager(self.system_check)
        self.credentials = credentials or CredentialStore()
        self.history = history or SessionHistory()
        self._transport = transport
        self._probe_sunshine = sunshine_probe
        self._network_facts = network_facts

    # ── credentials ────────────────────────────────────────────────────────
    def has_credential(self, kind: CredentialKind, scope: str = "default") -> bool:
        try:
            return self.credentials.info(kind, scope) is not None
        except SecretStoreUnavailable:
            return False

    def capabilities(self, provider: ProviderId) -> ProviderCapabilities:
        if provider is ProviderId.ZEROTIER:
            return zerotier_provider.capabilities(api_configured=self.has_credential(CredentialKind.ZEROTIER_API_TOKEN))
        if provider is ProviderId.HEADSCALE:
            server = self.headscale_server()
            return tailscale_provider.capabilities(api_configured=bool(server) and self.has_credential(CredentialKind.HEADSCALE_API_KEY, server), provider=provider)
        configured = self.has_credential(CredentialKind.TAILSCALE_API_TOKEN) or self.has_credential(CredentialKind.TAILSCALE_OAUTH_CLIENT)
        return tailscale_provider.capabilities(api_configured=configured)

    # ── status ─────────────────────────────────────────────────────────────
    def _tailscale_cli(self) -> tailscale_provider.TailscaleCli:
        return tailscale_provider.TailscaleCli(self.system_check.tailscale_cmd(), self.manager._run)

    def _tailnet_owner(self, status: ProviderStatus) -> ProviderId:
        """Which product the *active* tailscaled profile belongs to.

        Both products use one daemon. In order of authority: the provider Big
        Remote Play recorded when it joined; the client's own control URL
        (``*.tailscale.com`` is the Tailscale service, anything else is a
        self-hosted Headscale); finally the MagicDNS suffix.
        """
        try:
            selected = self.manager.list_tailscale_profiles().selected
            recorded = self.manager._metadata()["tailscale_profiles"].get(selected.profile_id, {}) if selected else {}
        except Exception:
            recorded = {}
        explicit = recorded.get("provider") if isinstance(recorded, dict) else None
        if explicit in ("tailscale", "headscale"):
            return ProviderId(explicit)
        control = self._tailscale_cli().control_url()
        if control:
            return ProviderId.TAILSCALE if tailscale_provider.is_tailscale_control(control) else ProviderId.HEADSCALE
        suffix = (status.network_name or "").lower()
        dns = status.self_device.dns_name.lower() if status.self_device else ""
        if suffix.endswith("ts.net") or dns.endswith(".ts.net"):
            return ProviderId.TAILSCALE
        return ProviderId.HEADSCALE if suffix and status.connected else ProviderId.TAILSCALE

    def headscale_server(self) -> str:
        """The Headscale server this computer uses: saved profile, else the client's control URL."""
        try:
            profiles = self.manager.list_tailscale_profiles().profiles
        except Exception:
            profiles = ()
        chosen = next((p for p in profiles if p.provider == "headscale" and p.selected), None) or next((p for p in profiles if p.provider == "headscale"), None)
        if chosen is not None and chosen.login_server:
            return chosen.login_server
        control = self._tailscale_cli().control_url()
        return control if control and not tailscale_provider.is_tailscale_control(control) else ""

    def status(self, provider: ProviderId) -> ProviderStatus:
        if provider is ProviderId.ZEROTIER:
            installed = _installed(self.system_check.has_zerotier)
            running = installed and _installed(self.system_check.is_zerotier_running)
            return zerotier_provider.status(self.manager, installed=installed, service_running=running)
        if not _installed(self.system_check.has_tailscale):
            return ProviderStatus(provider, ConnectionState.UNAVAILABLE, installed=False)
        status = self._tailscale_cli().status(provider)
        if status.state in (ConnectionState.UNAVAILABLE, ConnectionState.ERROR):
            return status
        owner = self._tailnet_owner(status)
        if owner is provider:
            return status
        # The daemon is busy with the other product: this one is simply not in use.
        return ProviderStatus(provider, ConnectionState.DISCONNECTED, technical_detail=f"tailscaled is using {owner.display_name}")

    def overview(self) -> list[ProviderStatus]:
        with ThreadPoolExecutor(max_workers=3) as pool:
            return list(pool.map(self.status, (ProviderId.TAILSCALE, ProviderId.ZEROTIER, ProviderId.HEADSCALE)))

    # ── Share: where can others reach this computer? ───────────────────────
    def share_endpoints(self, statuses: Iterable[ProviderStatus] | None = None) -> list[tuple[ProviderId, PeerDevice]]:
        endpoints: list[tuple[ProviderId, PeerDevice]] = []
        for status in statuses if statuses is not None else self.overview():
            if status.connected and status.self_device is not None and status.reachable_address:
                endpoints.append((status.provider, status.self_device))
        return endpoints

    # ── Connect: which computers could be the game PC? ─────────────────────
    def candidate_hosts(self, statuses: Iterable[ProviderStatus] | None = None, *, port: int = SUNSHINE_DEFAULT_BASE_PORT) -> list[HostCandidate]:
        """Peers reported by connected providers, in the provider priority order.

        Only the providers' own knowledge is used: no subnet is scanned. The
        address is the one the provider assigned, so it is usable by Moonlight
        through the overlay, and MagicDNS names are kept as an alternative.
        """
        candidates: list[HostCandidate] = []
        seen: set[str] = set()
        for status in statuses if statuses is not None else self.overview():
            if not status.connected:
                continue
            for peer in status.peers:
                address = peer.best_address
                if not address or address in seen or not valid_host(address):
                    continue
                seen.add(address)
                alternatives = tuple(value for value in (*peer.addresses, peer.dns_name) if value and value != address)
                candidates.append(
                    HostCandidate(
                        name=peer.name or address,
                        address=address,
                        port=port,
                        provider=status.provider.value,
                        source="provider",
                        online=peer.online,
                        dns_name=peer.dns_name,
                        addresses=alternatives,
                    )
                )
        candidates.sort(key=lambda candidate: (candidate.online is not True,))
        return candidates

    def sunshine_ready(self, address: str, port: int = SUNSHINE_DEFAULT_BASE_PORT) -> bool:
        """Does Sunshine answer at this address? One bounded TCP/HTTP check."""
        probe = self._probe_sunshine(address, port)
        return bool(getattr(probe, "answered", False) or getattr(probe, "listening", False))

    def recent_hosts(self, limit: int = 5) -> list[HostCandidate]:
        return self.history.recent_hosts(limit)

    # ── Diagnosis ──────────────────────────────────────────────────────────
    def diagnose(self, candidate: HostCandidate, statuses: Iterable[ProviderStatus] | None = None) -> HostDiagnosis:
        details: list[str] = []
        provider = ProviderId(candidate.provider) if candidate.provider in {p.value for p in ProviderId} else None
        network_ok: bool | None = None
        host_online: bool | None = None
        path = None
        if provider is not None:
            status = next((s for s in statuses if s.provider is provider), None) if statuses is not None else self.status(provider)
            network_ok = bool(status and status.connected)
            details.append(f"{provider.display_name}: {status.state.value if status else 'unknown'}")
            if not network_ok:
                return HostDiagnosis(network_ok=False, problem="network_down", details=tuple(details))
            peer = next((p for p in status.peers if candidate.address in p.addresses or p.dns_name == candidate.dns_name), None) if status else None
            if peer is not None:
                host_online = bool(peer.online)
                if peer.expired:
                    details.append("peer key expired")
            if provider in (ProviderId.TAILSCALE, ProviderId.HEADSCALE) and host_online is not False:
                path = self._tailscale_cli().ping(candidate.address)
                details.append(f"path: {path.kind}" + (f" ({path.relay})" if path.relay else "") + (f", {path.latency_ms:.0f} ms" if path.latency_ms is not None else ""))
                if path.reachable:
                    host_online = True
        probe = self._probe_sunshine(candidate.address, candidate.port)
        sunshine_ok = bool(getattr(probe, "answered", False) or getattr(probe, "listening", False))
        details.append(f"sunshine {candidate.port}/tcp: {'answers' if sunshine_ok else 'no answer'}")
        if sunshine_ok:
            host_online = True
        problem = ""
        if not sunshine_ok:
            problem = "host_offline" if host_online is False else "sunshine_missing"
        return HostDiagnosis(network_ok=network_ok, host_online=host_online, sunshine_ok=sunshine_ok, path=path, problem=problem, details=tuple(details))

    # ── Administrative APIs (None when no credential is configured) ────────
    def zerotier_central(self) -> ZeroTierCentral | None:
        token = self.credentials.secret(CredentialKind.ZEROTIER_API_TOKEN)
        if not token:
            return None
        info = self.credentials.info(CredentialKind.ZEROTIER_API_TOKEN)
        return ZeroTierCentral(token, flavor=info.note if info else "", transport=self._transport)

    def tailscale_api(self) -> TailscaleApi | None:
        token = self.credentials.secret(CredentialKind.TAILSCALE_API_TOKEN)
        if token:
            return TailscaleApi(api_token=token, transport=self._transport)
        client_id, secret = self.credentials.oauth_client()
        if client_id and secret:
            info = self.credentials.info(CredentialKind.TAILSCALE_OAUTH_CLIENT)
            tags = info.tags if info else ()
            return TailscaleApi(oauth_client_id=client_id, oauth_client_secret=secret, oauth_tags=tags, transport=self._transport)
        return None

    def headscale_api(self, server: str = "") -> HeadscaleApi | None:
        server = server or self.headscale_server()
        if not server:
            return None
        try:
            origin = normalize_base_url(server, allow_http_loopback=True)
        except ValueError:
            return None
        return HeadscaleApi(origin, self.credentials.secret(CredentialKind.HEADSCALE_API_KEY, origin), transport=self._transport)

    def test_credential(self, kind: CredentialKind, scope: str = "default") -> ApiResult:
        """Contact the provider with a saved credential; no data is changed."""
        if kind is CredentialKind.ZEROTIER_API_TOKEN:
            central = self.zerotier_central()
            if central is None:
                return ApiResult.failure(ApiErrorKind.AUTH, "no credential")
            result = central.detect()
            if result.ok:
                self.credentials.set_note(kind, scope, central.flavor)
            return result
        if kind is CredentialKind.HEADSCALE_API_KEY:
            api = self.headscale_api(scope)
            return api.test() if api is not None else ApiResult.failure(ApiErrorKind.AUTH, "no credential")
        api = self.tailscale_api()
        return api.test() if api is not None else ApiResult.failure(ApiErrorKind.AUTH, "no credential")

    # ── one-click recovery for the simple interface ─────────────────────────
    def internet_available(self) -> bool:
        """Does any physical interface have an address? No external lookup."""
        facts = self._network_facts()
        return facts.stack != "none" or bool(facts.overlay_interfaces)

    def turn_on(self, provider: ProviderId) -> bool:
        """Reconnect a signed-in client that was switched off."""
        if provider is ProviderId.ZEROTIER:
            return self.start_service(provider)
        return self.manager.resume_tailscale().connected

    def start_service(self, provider: ProviderId) -> bool:
        """Start the provider's background service (PolicyKit asks once)."""
        unit = "zerotier-one" if provider is ProviderId.ZEROTIER else "tailscaled"
        return self.manager.start_service(unit).returncode == 0

    # ── ZeroTier local actions ─────────────────────────────────────────────
    def join_zerotier(self, network_id: str) -> CommandResult:
        return self.manager.join_zerotier_network(network_id, allow_privileged=True)

    def leave_zerotier(self, network_id: str) -> CommandResult:
        return self.manager.leave_zerotier_network(network_id, allow_privileged=True)

    def grant_zerotier_access(self) -> CommandResult:
        return self.manager.grant_zerotier_user_access()


class OfflinePrivateNetworkService(PrivateNetworkService):
    """A service with no VPN client, no keyring and no network access.

    Used by the test suite as the default so that no UI test can reach a real
    daemon, keyring or API; individual tests install richer fakes.
    """

    def __init__(self) -> None:
        from big_remote_play.utils.secret_store import InMemorySecretBackend, SecretStore

        offline = SimpleNamespace(
            tailscale_cmd=lambda: ["tailscale"],
            zerotier_cmd=lambda: ["zerotier-cli"],
            has_tailscale=lambda: False,
            has_zerotier=lambda: False,
            is_zerotier_running=lambda: False,
        )
        super().__init__(
            offline,  # type: ignore[arg-type]
            manager=VPNAccountManager(offline, runner=lambda argv, timeout=15: CommandResult(127, "", "offline")),  # type: ignore[arg-type]
            credentials=CredentialStore(SecretStore(InMemorySecretBackend())),
            sunshine_probe=lambda *args, **kwargs: SimpleNamespace(listening=False, answered=False, hostname=""),
            network_facts=lambda: NetworkFacts(ipv4=("192.0.2.10",)),
        )


_default_factory: Callable[[], PrivateNetworkService] = PrivateNetworkService


def default_service() -> PrivateNetworkService:
    """The service the UI uses. Tests replace the factory with a hermetic fake."""
    return _default_factory()


def set_default_factory(factory: Callable[[], PrivateNetworkService]) -> None:
    global _default_factory
    _default_factory = factory
