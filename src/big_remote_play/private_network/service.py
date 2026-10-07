"""The facade the UI uses for everything private-network related.

Every method blocks (CLI calls, HTTP, D-Bus to the keyring) and must be called
from a worker thread. Results are plain data from :mod:`.models`; the UI never
sees a subprocess, an HTTP header or a credential.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
import logging
import time
from dataclasses import replace
from types import SimpleNamespace

from big_remote_play.integration_contracts import SUNSHINE_DEFAULT_BASE_PORT
from big_remote_play.utils.connection_health import LinkSample, ping_once
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
from . import device_list
from .cards import CardSummary, summarize
from .device_list import DeviceListing
from .models import ConnectionState, HostCandidate, HostDiagnosis, PeerDevice, ProviderCapabilities, ProviderId, ProviderStatus, Recovery

_log = logging.getLogger("big-remoteplay")


def _api_problem(result: ApiResult) -> str:
    """A short, secret-free reason for the UI (the UI words it)."""
    return result.error.value if result.error is not None else "failed"


# systemctl returns once the unit runs; the client answers a moment later.
SERVICE_ANSWER_SECONDS = 15.0

# The cards refresh every few seconds; ZeroTier Central is asked at most this often.
ZEROTIER_COUNT_SECONDS = 60.0
_zerotier_counts: dict[str, tuple[float, DeviceListing]] = {}


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
        self._ping: Callable[[str], float | None] = ping_once
        self._sleep: Callable[[float], None] = time.sleep
        self._clock: Callable[[], float] = time.monotonic

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

        Both products use one daemon. The control server the client really
        uses decides: ``*.tailscale.com`` is the Tailscale service, anything
        else is a self-hosted Headscale. What Big Remote Play recorded when it
        joined is only the fallback when the client cannot say (and it is
        corrected when it contradicts the client); the MagicDNS suffix is the
        last resort.
        """
        try:
            selected = self.manager.list_tailscale_profiles().selected
            recorded = self.manager._metadata()["tailscale_profiles"].get(selected.profile_id, {}) if selected else {}
        except Exception:
            selected, recorded = None, {}
        explicit = recorded.get("provider") if isinstance(recorded, dict) else None
        control = self._tailscale_cli().control_url()
        if control:
            owner = ProviderId.TAILSCALE if tailscale_provider.is_tailscale_control(control) else ProviderId.HEADSCALE
            if selected is not None and explicit != owner.value:
                # A profile joined through the wrong page (a custom login server
                # typed on the Tailscale page) is recorded right from now on.
                try:
                    self.manager.set_tailscale_metadata(selected.profile_id, provider=owner.value, login_server=control if owner is ProviderId.HEADSCALE else "")
                except Exception:
                    _log.debug("could not correct the recorded product of the Tailscale profile")
            return owner
        if explicit in ("tailscale", "headscale"):
            return ProviderId(explicit)
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
        # The daemon is busy with the other product: this one is simply not in
        # use. With an account of its own saved, Start switches to it.
        saved = self._saved_profile(provider)
        return ProviderStatus(provider, ConnectionState.DISCONNECTED, technical_detail=f"tailscaled is using {owner.display_name}", recovery=Recovery.RECONNECT if saved else None)

    def _saved_profile(self, provider: ProviderId) -> str:
        """A saved Tailscale-app account of ``provider`` (``""`` when none)."""
        try:
            profiles = self.manager.list_tailscale_profiles().profiles
        except Exception:
            return ""
        wanted = provider.value
        found = next((p for p in profiles if p.provider == wanted), None)
        if found is None and provider is ProviderId.TAILSCALE:
            # Accounts joined before Big Remote Play recorded the product.
            found = next((p for p in profiles if not p.provider and not p.login_server), None)
        return found.profile_id if found is not None else ""

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
        outcome = str(getattr(probe, "tcp", "") or "")
        details.append(f"sunshine {candidate.port}/tcp: {'answers' if sunshine_ok else 'no answer'}" + (f" ({outcome})" if outcome and not sunshine_ok else ""))
        if sunshine_ok:
            host_online = True
        problem = ""
        if not sunshine_ok:
            if outcome == "refused":
                # The computer itself answered: nothing listens on the port.
                host_online, problem = True, "sunshine_missing"
            elif outcome in ("timeout", "unreachable") and host_online is not False and self._ping(candidate.address) is not None:
                # It answers a ping but not the port: a firewall filters it.
                details.append("ping: answers")
                host_online, problem = True, "firewall"
            else:
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
        """Start the provider's background service (PolicyKit asks once).

        True once its client answers, so the next reading shows the real next
        step (sign in, allow access, join) instead of a service still starting.
        """
        unit = "zerotier-one" if provider is ProviderId.ZEROTIER else "tailscaled"
        started = self.manager.start_service(unit)
        if started.returncode != 0:
            return False
        deadline = self._clock() + SERVICE_ANSWER_SECONDS
        while not self._service_answers(provider):
            if self._clock() >= deadline:
                _log.warning("%s started but its client did not answer", unit)
                return False
            self._sleep(0.5)
        return True

    def start(self, provider: ProviderId) -> bool:
        """Start: the service if it is stopped, then a signed-in connection that was switched off.

        Never signs in or joins anything: that is the method's Set up step.
        """
        status = self.status(provider)
        if status.recovery is Recovery.START_SERVICE:
            if not self.start_service(provider):
                return False
            status = self.status(provider)
        if status.recovery is Recovery.RECONNECT and status.technical_detail.startswith("tailscaled is using"):
            # Tailscale and Headscale share the Tailscale app: switch to this one's account.
            profile = self._saved_profile(provider)
            if not profile or self.manager.switch_tailscale_profile(profile).returncode != 0:
                return False
            status = self.status(provider)
            if status.connected:
                return True
        if status.recovery is Recovery.RECONNECT:
            return self.turn_on(provider)
        return status.state is not ConnectionState.UNAVAILABLE

    def stop(self, provider: ProviderId) -> bool:
        """Stop: Tailscale/Headscale disconnect and stay signed in (``tailscale down``,
        no password); ZeroTier's service stops, which disconnects all its networks."""
        if provider is ProviderId.ZEROTIER:
            return self.manager.stop_service("zerotier-one").returncode == 0
        if self.status(provider).state not in (ConnectionState.CONNECTED, ConnectionState.CONNECTING, ConnectionState.NEEDS_AUTHORIZATION):
            # The daemon belongs to the other product (Tailscale vs Headscale): not ours to stop.
            return False
        return self.manager.pause_tailscale().returncode == 0

    # ── devices of one provider ────────────────────────────────────────────
    def device_listing(self, provider: ProviderId, network_id: str = "") -> DeviceListing:
        """This provider's devices only (one ZeroTier network at a time), never mixed."""
        status = self.status(provider)
        if provider is ProviderId.ZEROTIER:
            return self._zerotier_listing(status, network_id)
        api_devices = None
        problem = ""
        if status.connected:
            if provider is ProviderId.HEADSCALE:
                api = self.headscale_api() if self.capabilities(provider).can_manage_devices else None
                if api is not None:
                    nodes, result = api.nodes()
                    api_devices, problem = (nodes, "") if result.ok else (None, _api_problem(result))
            else:
                api = self.tailscale_api()
                if api is not None:
                    found, result = api.list_devices()
                    api_devices, problem = (found, "") if result.ok else (None, _api_problem(result))
        return device_list.tailnet_listing(status, api_devices, api_problem=problem)

    def _zerotier_listing(self, status: ProviderStatus, network_id: str = "") -> DeviceListing:
        listing = device_list.zerotier_listing(status, network_id)
        central = self.zerotier_central()
        if central is None or not listing.network_id:
            return listing
        members, result = central.list_members(listing.network_id)
        if not result.ok:
            return replace(listing, problem=_api_problem(result))
        return device_list.zerotier_listing(status, listing.network_id, members)

    # ── the three cards of Connect your devices ────────────────────────────
    def card_summaries(self, zerotier_network: str = "") -> list[CardSummary]:
        """One summary per provider, each built from that provider alone."""
        from .headscale_server import SetupStore

        statuses = self.overview()
        try:
            setup = SetupStore().load()
        except Exception:
            setup = None
        cards: list[CardSummary] = []
        for status in statuses:
            if status.provider is ProviderId.ZEROTIER:
                listing = self._zerotier_card_listing(status, zerotier_network) if status.connected else None
                cards.append(summarize(status, listing=listing, network_id=zerotier_network))
            elif status.provider is ProviderId.HEADSCALE:
                server = self.headscale_server() if status.installed else ""
                started = setup is not None and setup.started and not setup.complete
                cards.append(summarize(status, headscale_server=server, saved_profile=bool(self._saved_profile(status.provider)), setup_incomplete=started))
            else:
                cards.append(summarize(status, saved_profile=bool(self._saved_profile(status.provider))))
        return cards

    def _zerotier_card_listing(self, status: ProviderStatus, network_id: str) -> DeviceListing:
        """Central's member list, remembered for a minute; never asked without a token."""
        try:
            has_token = self.has_credential(CredentialKind.ZEROTIER_API_TOKEN)
        except Exception:
            has_token = False
        if not has_token:
            return device_list.zerotier_listing(status, network_id)
        chosen = device_list.zerotier_listing(status, network_id).network_id
        cached = _zerotier_counts.get(chosen)
        if cached is not None and self._clock() - cached[0] < ZEROTIER_COUNT_SECONDS:
            return cached[1]
        listing = self._zerotier_listing(status, chosen)
        if not listing.problem:
            _zerotier_counts[chosen] = (self._clock(), listing)
        return listing

    def remove_device(self, provider: ProviderId, device_id: str, network_id: str = "") -> ApiResult:
        """Remove one device through the provider's API (only offered when it is configured)."""
        if provider is ProviderId.ZEROTIER:
            central = self.zerotier_central()
            return central.remove_member(network_id, device_id) if central is not None else ApiResult(False, error=ApiErrorKind.AUTH)
        if provider is ProviderId.HEADSCALE:
            api = self.headscale_api()
            return api.delete_node(device_id) if api is not None else ApiResult(False, error=ApiErrorKind.AUTH)
        api = self.tailscale_api()
        return api.delete_device(device_id) if api is not None else ApiResult(False, error=ApiErrorKind.AUTH)

    def _service_answers(self, provider: ProviderId) -> bool:
        try:
            if provider is ProviderId.ZEROTIER:
                node = self.manager.zerotier_info()
                # Refusing this user proves the service answers; access is the next step.
                return bool(node.address) or node.needs_privilege
            return self._tailscale_cli().status(provider).recovery is not Recovery.START_SERVICE
        except Exception:
            return False

    # ── ZeroTier local actions ─────────────────────────────────────────────
    def link_sample(self, status: ProviderStatus) -> LinkSample:
        """How well this private network reaches the other devices, right now.

        Tailscale/Headscale: one ``tailscale ping`` of an online device (the
        client answers it on any system and says direct or relay). ZeroTier:
        the latencies ZeroTier itself keeps for its peers, so nothing is sent.
        """
        if status.provider is ProviderId.ZEROTIER:
            from .zerotier_join import controller_of, summarize_peers

            controllers = [controller_of(network.network_id) for network in status.networks] or [controller_of(status.network_id)]
            summary = summarize_peers(self.manager.list_zerotier_peers(), controllers=controllers)
            if summary.total == 0:
                return LinkSample(path="none")
            path = "direct" if summary.direct else "relay"
            return LinkSample(float(summary.best_latency_ms) if summary.best_latency_ms is not None else None, path)
        peer = next((item for item in status.online_peers if not item.is_self and item.best_address), None)
        if peer is None:
            return LinkSample(path="none")
        report = self._tailscale_cli().ping(peer.best_address, count=1, timeout_seconds=2)
        kind = report.kind if report.kind in ("direct", "relay") else ("relay" if report.kind == "peer_relay" else "unknown")
        return LinkSample(report.latency_ms if report.reachable else None, kind, peer.name)

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
        self._ping = lambda address: None


_default_factory: Callable[[], PrivateNetworkService] = PrivateNetworkService


def default_service() -> PrivateNetworkService:
    """The service the UI uses. Tests replace the factory with a hermetic fake."""
    return _default_factory()


def set_default_factory(factory: Callable[[], PrivateNetworkService]) -> None:
    global _default_factory
    _default_factory = factory
