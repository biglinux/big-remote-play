"""GTK behaviour of the private-network pages with a simulated service.

No VPN client, keyring, API or Sunshine is contacted: every boundary is a
fake installed through ``private_network.service.set_default_factory`` or
passed to the dialog directly.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from big_remote_play.private_network import service as service_module  # noqa: E402
from big_remote_play.private_network.credentials import CredentialKind, CredentialStore  # noqa: E402
from big_remote_play.private_network.history import SessionHistory  # noqa: E402
from big_remote_play.private_network.http import ApiErrorKind, ApiResult  # noqa: E402
from big_remote_play.private_network.models import ConnectionState, OverlayNetwork, PeerDevice, ProviderCapabilities, ProviderId, ProviderStatus  # noqa: E402
from big_remote_play.private_network.zerotier_api import CentralMember  # noqa: E402
from big_remote_play.utils.secret_store import InMemorySecretBackend, SecretStore  # noqa: E402

FAKE_TOKEN = "tskey-api-kFAKE1CNTRL-abcdefghijklmnop3FxQ"


@pytest.fixture(autouse=True)
def display():
    if Gdk.Display.get_default() is None:
        pytest.skip("Requires a GTK display")
    Adw.init()
    Gtk.Settings.get_default().set_property("gtk-enable-animations", False)


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def texts(widget):
    found = []
    stack = [widget]
    while stack:
        current = stack.pop()
        for getter in ("get_label", "get_title", "get_subtitle", "get_text"):
            value = getattr(current, getter, None)
            if callable(value):
                try:
                    result = value()
                except TypeError:
                    continue
                if isinstance(result, str) and result:
                    found.append(result)
        child = current.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return " | ".join(found)


def dialog_root(dialog):
    return dialog.get_child()


def flow_window():
    """A window whose content is a navigation view, like Connect your devices."""
    window = Adw.ApplicationWindow()
    navigation = Adw.NavigationView()
    navigation.add(Adw.NavigationPage(child=Gtk.Label(label="root"), title="root", tag="providers"))
    window.set_content(navigation)
    window.present()
    return window, navigation


class FakeCentral:
    flavor = "legacy"

    def __init__(self, members):
        self.members = members
        self.calls = []

    def list_members(self, network_id):
        return list(self.members), ApiResult(True, 200)

    def set_authorized(self, network_id, node_id, authorized):
        self.calls.append(("authorize", network_id, node_id, authorized))
        self.members = [CentralMember(m.node_id, m.name, authorized if m.node_id == node_id else m.authorized) for m in self.members]
        return ApiResult(True, 200)

    def remove_member(self, network_id, node_id):
        self.calls.append(("remove", network_id, node_id))
        return ApiResult(True, 200)


class FakeTailscaleApi:
    can_invite = True

    def __init__(self):
        self.calls = []

    def device_for_node(self, node_id):
        return SimpleNamespace(device_id="dev1"), ApiResult(True, 200)

    def share_device(self, device_id, email=""):
        self.calls.append(("share", device_id, email))
        return "https://login.tailscale.com/admin/invite/simulated", ApiResult(True, 200)

    def invite_user(self, email, role="member"):
        self.calls.append(("invite", email))
        return "https://login.tailscale.com/admin/invite/user", ApiResult(True, 200)

    def list_devices(self):
        return [], ApiResult(True, 200)


class FakeService(service_module.OfflinePrivateNetworkService):
    def __init__(self, statuses=(), capabilities=None, central=None, tailscale_api=None, history=None):
        super().__init__()
        self._statuses = {status.provider: status for status in statuses}
        self._capabilities = capabilities or ProviderCapabilities(can_join_network=True)
        self._central = central
        self._ts_api = tailscale_api
        self.credentials = CredentialStore(SecretStore(InMemorySecretBackend()))
        if history is not None:
            self.history = history

    def status(self, provider):
        return self._statuses.get(provider, ProviderStatus(provider, ConnectionState.DISCONNECTED))

    def overview(self):
        return [self.status(provider) for provider in ProviderId]

    def capabilities(self, provider):
        return self._capabilities

    def zerotier_central(self):
        return self._central

    def tailscale_api(self):
        return self._ts_api

    def sunshine_ready(self, address, port=47989):
        return address == "100.64.0.2"


TS_CONNECTED = ProviderStatus(
    ProviderId.TAILSCALE,
    ConnectionState.CONNECTED,
    self_device=PeerDevice("game-pc", ("100.64.0.1",), provider="tailscale", dns_name="game-pc.tail0.ts.net", is_self=True, node_id="nSELF"),
    peers=(PeerDevice("notebook", ("100.64.0.2",), provider="tailscale", online=True), PeerDevice("tv", ("100.64.0.3",), provider="tailscale", online=False)),
)
ZT_CONNECTED = ProviderStatus(
    ProviderId.ZEROTIER,
    ConnectionState.CONNECTED,
    network_id="8056c2e21c000001",
    self_device=PeerDevice("game-pc", ("10.147.17.5",), provider="zerotier", is_self=True, node_id="aaaaaaaaaa"),
    networks=(OverlayNetwork("8056c2e21c000001", "games", ConnectionState.CONNECTED, ("10.147.17.5",)),),
)


# ── API access ────────────────────────────────────────────────────────────


def test_api_access_saves_to_keyring_clears_the_field_and_never_shows_the_secret():
    from big_remote_play.ui.api_access_dialog import ApiAccessDialog

    service = FakeService()
    toasts = []
    window = Adw.ApplicationWindow()
    window.present()
    dialog = ApiAccessDialog(window, service, show_toast=toasts.append)
    dialog.present()
    assert wait_for(lambda: "Not configured" in texts(dialog_root(dialog.dialog)))
    assert dialog.ts_token.get_text() == ""  # never pre-filled
    dialog.ts_token.set_text(FAKE_TOKEN)
    dialog._save_tailscale()
    assert wait_for(lambda: service.credentials.secret(CredentialKind.TAILSCALE_API_TOKEN) == FAKE_TOKEN)
    assert wait_for(lambda: dialog.ts_token.get_text() == "")
    assert wait_for(lambda: "tskey-api-••••••••3FxQ" in texts(dialog_root(dialog.dialog)))
    assert FAKE_TOKEN not in texts(dialog_root(dialog.dialog))
    window.close()


def test_api_access_refuses_an_auth_key_and_explains_the_difference():
    from big_remote_play.ui.api_access_dialog import ApiAccessDialog

    service = FakeService()
    toasts = []
    window = Adw.ApplicationWindow()
    window.present()
    dialog = ApiAccessDialog(window, service, show_toast=toasts.append)
    dialog.present()
    dialog.zt_token.set_text("tskey-auth-kWRONG1234567-xyz")
    dialog._save(CredentialKind.ZEROTIER_API_TOKEN, dialog.zt_token)
    assert wait_for(lambda: bool(toasts))
    assert "auth key" in toasts[-1].lower()
    assert service.credentials.secret(CredentialKind.ZEROTIER_API_TOKEN) == ""
    window.close()


# ── Manage network / Add device ───────────────────────────────────────────


def test_zerotier_member_can_be_approved_and_removal_needs_confirmation(monkeypatch):
    from big_remote_play.ui import network_common
    from big_remote_play.ui.network_devices import ManageNetworkFlow

    central = FakeCentral([CentralMember("bbbbbbbbbb", "Notebook", False), CentralMember("cccccccccc", "Desk", True)])
    service = FakeService([ZT_CONNECTED], ProviderCapabilities(can_authorize_member=True, can_remove_device=True), central=central)
    window, navigation = flow_window()
    dialog = ManageNetworkFlow(navigation, navigation, service, ZT_CONNECTED, show_toast=lambda _m: None)
    dialog.present()
    assert navigation.get_visible_page() is dialog.root  # a page in the window, not a dialog
    assert wait_for(lambda: "Notebook" in texts(dialog.devices))
    assert "Waiting for approval" in texts(dialog.devices)
    approve = next(button for button in _buttons(dialog.devices) if button.get_label() == "Approve")
    approve.emit("clicked")
    assert wait_for(lambda: ("authorize", "8056c2e21c000001", "bbbbbbbbbb", True) in central.calls)

    presented = []
    monkeypatch.setattr(network_common.Adw.AlertDialog, "present", lambda self, parent: presented.append(self))
    assert wait_for(lambda: any(button.get_tooltip_text() == "Remove device" for button in _buttons(dialog.devices)))
    remove = next(button for button in _buttons(dialog.devices) if button.get_tooltip_text() == "Remove device")
    remove.emit("clicked")
    assert presented and not [call for call in central.calls if call[0] == "remove"]
    presented[-1].emit("response", "confirm")
    assert wait_for(lambda: [call for call in central.calls if call[0] == "remove"])
    window.close()


def _buttons(widget):
    stack, found = [widget], []
    while stack:
        current = stack.pop()
        if isinstance(current, Gtk.Button):
            found.append(current)
        child = current.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return found


def test_share_this_computer_without_api_explains_the_console_and_sends_nothing():
    from big_remote_play.ui.network_devices import AddDeviceFlow

    api = FakeTailscaleApi()
    service = FakeService([TS_CONNECTED], ProviderCapabilities(can_join_network=True), tailscale_api=api)
    window, navigation = flow_window()
    dialog = AddDeviceFlow(navigation, navigation, service, TS_CONNECTED, show_toast=lambda _m: None)
    dialog.present()
    assert wait_for(lambda: "Share this computer" in texts(dialog.root_box))
    assert "Add a person to my network" in texts(dialog.root_box)  # the two concepts stay distinct
    page = dialog._share_page()
    assert "Open Tailscale admin console" in texts(page)
    assert api.calls == []
    window.close()


def test_share_this_computer_with_api_creates_a_device_invite_not_a_tailnet_invite():
    from big_remote_play.ui.network_devices import AddDeviceFlow

    api = FakeTailscaleApi()
    service = FakeService([TS_CONNECTED], ProviderCapabilities(can_share_machine=True, can_invite_user=True), tailscale_api=api)
    window, navigation = flow_window()
    dialog = AddDeviceFlow(navigation, navigation, service, TS_CONNECTED, show_toast=lambda _m: None)
    dialog.present()
    assert wait_for(lambda: dialog.capabilities is not None and dialog.capabilities.can_share_machine)
    page = dialog._share_page()
    entry = next(widget for widget in _walk(page) if isinstance(widget, Adw.EntryRow))
    entry.set_text("not-an-email")
    entry.emit("entry-activated")
    assert api.calls == []
    entry.set_text("friend@example.test")
    entry.emit("entry-activated")
    assert wait_for(lambda: api.calls == [("share", "dev1", "friend@example.test")])
    assert wait_for(lambda: "https://login.tailscale.com/admin/invite/simulated" in texts(page))
    window.close()


def test_add_device_keeps_terminal_commands_in_advanced_mode():
    from big_remote_play.ui.network_devices import AddDeviceFlow

    service = FakeService([TS_CONNECTED], ProviderCapabilities(can_create_auth_key=True))
    window, navigation = flow_window()
    simple = AddDeviceFlow(navigation, navigation, service, TS_CONNECTED, show_toast=lambda _m: None)
    assert "Linux command" not in texts(simple._own_computer_page())
    advanced = AddDeviceFlow(navigation, navigation, service, TS_CONNECTED, show_toast=lambda _m: None, advanced=True)
    assert "Linux command" in texts(advanced._own_computer_page())
    window.close()


def _walk(widget):
    stack = [widget]
    while stack:
        current = stack.pop()
        yield current
        child = current.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()


# ── History ───────────────────────────────────────────────────────────────


def test_history_dialog_groups_sessions_and_clears_only_after_confirmation(tmp_path, monkeypatch):
    from big_remote_play.ui import network_common
    from big_remote_play.ui.history_dialog import HistoryDialog

    history = SessionHistory(tmp_path / "sessions.json")
    session = history.start(host_name="Desk <b>PC</b>", host_address="100.64.0.2", port=47989, provider="tailscale", resolution="1920x1080", fps=60)
    history.finish(session)
    window = Adw.ApplicationWindow()
    window.present()
    dialog = HistoryDialog(window, history, on_reconnect=lambda record: None)
    dialog.present()
    assert wait_for(lambda: "Desk <b>PC</b>" in texts(dialog.sessions))
    assert "Tailscale" in texts(dialog.sessions) and "Today" in texts(dialog.sessions)
    presented = []
    monkeypatch.setattr(network_common.Adw.AlertDialog, "present", lambda self, parent: presented.append(self))
    dialog._confirm_clear()
    assert history.sessions()  # nothing removed before confirming
    presented[-1].emit("response", "confirm")
    assert wait_for(lambda: history.sessions() == [])
    window.close()


# ── Connect and Share integration ─────────────────────────────────────────


from test_ui_task_flows import ui as _ui_fixture  # noqa: E402  (the hermetic window fixture)

ui = _ui_fixture


def test_connect_lists_private_network_peers_with_readiness(ui, tmp_path):
    service = FakeService([TS_CONNECTED], history=SessionHistory(tmp_path / "s.json"))
    service_module.set_default_factory(lambda: service)
    guest = ui.guest_view
    guest.update_hosts_list([{"name": "lan-pc", "ip": "192.168.1.5", "port": 47989}])
    guest.refresh_private_hosts()
    assert wait_for(lambda: len(guest.listed_hosts()) == 3)
    hosts = guest.listed_hosts()
    cards = [texts(guest.host_card(index)) for index in range(3)]
    assert hosts[0]["name"] == "lan-pc"  # local discovery keeps its place
    assert "Tailscale" in cards[1] and "Sharing found" in cards[1]
    assert "Offline" in cards[2]
    assert hosts[1]["provider"] == "tailscale"
    # The address is technical: in the tooltip, not on the card.
    assert "192.168.1.5" not in cards[0] and "192.168.1.5" in guest.host_card(0).get_tooltip_text()


def test_connect_records_only_streams_that_started_and_offers_them_again(ui, tmp_path):
    history = SessionHistory(tmp_path / "s.json")
    service = FakeService([TS_CONNECTED], history=history)
    service_module.set_default_factory(lambda: service)
    guest = ui.guest_view
    guest.moonlight = SimpleNamespace(wait_for_stream=lambda timeout: True, process=None)
    guest._record_session_start({"name": "notebook", "ip": "100.64.0.2", "port": 47989, "provider": "tailscale"}, {"resolution": "1920x1080", "fps": "60", "bitrate": 20000})
    assert wait_for(lambda: bool(guest._session_id))
    guest.moonlight.process = None
    guest._record_session_end()
    assert wait_for(lambda: history.sessions() and history.sessions()[0].ended_at is not None)
    record = history.sessions()[0]
    assert record.provider == "tailscale" and record.resolution == "1920x1080" and record.fps == 60
    assert wait_for(lambda: guest.recent_group.get_visible())
    assert "notebook" in texts(guest.recent_group)


def test_a_moonlight_window_without_a_stream_is_not_recorded(ui, tmp_path):
    """Seen for real: Moonlight stays open on an error while nothing streams."""
    history = SessionHistory(tmp_path / "s.json")
    service_module.set_default_factory(lambda: FakeService([TS_CONNECTED], history=history))
    guest = ui.guest_view
    guest.moonlight = SimpleNamespace(wait_for_stream=lambda timeout: False, process=None)
    guest._record_session_start({"name": "notebook", "ip": "100.64.0.2", "port": 47989, "provider": "tailscale"}, {"resolution": "1920x1080", "fps": "60", "bitrate": 20000})
    wait_for(lambda: False, timeout=0.5)
    assert guest._session_id == "" and history.sessions() == []


def test_share_shows_the_private_address_only_while_sharing(ui):
    service = FakeService([TS_CONNECTED])
    service_module.set_default_factory(lambda: service)
    host = ui.host_view
    host.is_hosting = True
    host._refresh_internet_access()
    assert wait_for(lambda: host.internet_access_group.get_visible())
    content = texts(host.internet_access_group)
    assert "game-pc.tail0.ts.net" in content and "100.64.0.1" in content
    host.is_hosting = False
    host.sync_ui_state()
    assert not host.internet_access_group.get_visible()


class HubService(FakeService):
    """FakeService that records the one-click actions the hub may take."""

    def __init__(self, statuses=(), *, internet=True, recent=(), after_action=None, **kwargs):
        super().__init__(statuses, **kwargs)
        self.internet = internet
        self.recent = list(recent)
        self.after_action = after_action
        self.calls = []

    def internet_available(self):
        return self.internet

    def recent_hosts(self, limit=5):
        return self.recent[:limit]

    def _act(self, name, provider):
        self.calls.append((name, provider))
        if self.after_action is not None:
            self._statuses[provider] = self.after_action
        return True

    def turn_on(self, provider):
        return self._act("turn_on", provider)

    def start_service(self, provider):
        return self._act("start_service", provider)


def test_simple_join_form_uses_network_code_and_hides_auth_keys(ui):
    from big_remote_play.ui.private_network_view import ConnectPage

    zerotier = ConnectPage("zerotier", ui)
    assert zerotier._e_netid.get_title() == "Network code"
    tailscale = ConnectPage("tailscale", ui)
    assert not tailscale._auth_key_expander.get_visible()
    log_buttons = [button for button in _buttons(tailscale) if button.get_tooltip_text()]
    assert any(button.get_tooltip_text() == "Connection Log" for button in log_buttons)


def test_advanced_join_form_restores_exact_provider_controls(ui):
    from big_remote_play.ui.private_network_view import ConnectPage

    ui.network_advanced_mode = True
    zerotier = ConnectPage("zerotier", ui)
    assert zerotier._e_netid.get_title() == "Network ID (16 characters)"
    tailscale = ConnectPage("tailscale", ui)
    assert tailscale._auth_key_expander.get_visible()
    headscale = ConnectPage("headscale", ui)
    assert headscale._headscale_key_expander.get_visible()


def test_api_error_is_explained_without_leaking_detail_secrets():
    from big_remote_play.ui.network_common import api_error_message, technical_detail

    result = ApiResult.failure(ApiErrorKind.AUTH, "token tskey-api-kSECRET-x rejected", 401)
    assert "refused" in api_error_message(result)
    assert "SECRET" not in technical_detail(result)


def test_zerotier_add_device_offers_the_network_id_as_text_and_qr_code():
    from big_remote_play.ui.network_devices import AddDeviceFlow

    service = FakeService([ZT_CONNECTED], ProviderCapabilities(can_join_network=True))
    window, navigation = flow_window()
    dialog = AddDeviceFlow(navigation, navigation, service, ZT_CONNECTED, show_toast=lambda _m: None)
    dialog.present()
    # Shown in groups of four to read aloud; the QR code and the copy keep the exact ID.
    assert wait_for(lambda: "8056 c2e2 1c00 0001" in texts(dialog.root_box))
    qr_buttons = [button for button in _buttons(dialog.root_box) if button.get_label() == "QR code"]
    assert len(qr_buttons) == 1
    qr_buttons[0].emit("clicked")
    shown = window.get_visible_dialog()
    assert shown is not None and any(getattr(widget, "_brp_qr_text", "") == "8056c2e21c000001" for widget in _walk(shown))
    shown.close()
    window.close()


class FakeMoonlight:
    def __init__(self, streams: bool):
        self.streams = streams
        self.connected_to = None
        self.process = None
        self.disconnected = False

    def connect(self, ip, **kwargs):
        self.connected_to = ip
        return True

    def wait_for_stream(self, timeout):
        return self.streams

    def is_connected(self):
        return self.connected_to is not None and not self.disconnected

    def disconnect(self):
        self.disconnected = True
        return True

    def pair(self, *args, **kwargs):
        return False


@pytest.mark.parametrize(
    "answers,certificate,streams,expected",
    [
        (False, "", False, "unreachable"),
        (True, "", False, "pairing"),
        (True, "@ByteArray(CERT)", True, "streaming"),
        (True, "@ByteArray(CERT)", False, "pair_again"),
    ],
)
def test_connect_decides_with_fast_real_signals(ui, monkeypatch, answers, certificate, streams, expected):
    """Reachability from /serverinfo, pairing from Moonlight's stored certificate,
    success only when video starts — never from the slow `moonlight list`."""
    import big_remote_play.private_network.diagnostics as diagnostics
    import big_remote_play.utils.moonlight_config as moonlight_config

    monkeypatch.setattr(
        diagnostics, "probe_sunshine", lambda address, port, **kw: SimpleNamespace(answered=answers, listening=answers, uniqueid="UUID-1" if answers else "", tcp="open" if answers else "timeout")
    )
    monkeypatch.setattr(moonlight_config, "paired_host_certificate", lambda address, paths=None, uuid="": certificate if uuid in ("", "UUID-1") else "")
    guest = ui.guest_view
    guest.moonlight = FakeMoonlight(streams)
    outcomes = []
    # A private-network host offers Diagnose along with the explanation.
    monkeypatch.setattr(guest, "show_diagnosable_error", lambda host, message: outcomes.append("unreachable"))
    monkeypatch.setattr(guest, "start_pairing_flow", lambda host, attempt=None: outcomes.append("pairing"))
    monkeypatch.setattr(guest, "show_pair_again_dialog", lambda host: outcomes.append("pair_again"))
    monkeypatch.setattr(guest, "_record_session_start", lambda host, requested: outcomes.append("streaming"))
    guest.connect_to_host({"name": "desk", "ip": "100.64.0.2", "port": 47989, "provider": "tailscale"})
    assert wait_for(lambda: bool(outcomes))
    assert outcomes == [expected]
    if expected == "pair_again":
        assert guest.moonlight.disconnected  # the error window is closed, not left behind


def test_status_polling_checks_only_the_services_the_page_shows(ui, monkeypatch):
    """The sidebar used to start five probes every 3 s on every page."""
    probed = []
    for name in ("sunshine", "moonlight", "tailscale", "zerotier"):
        monkeypatch.setattr(ui.system_check, f"is_{name}_running", lambda n=name: probed.append(n) or True)
    ui._status_timer_id = ui._status_timer_id or 1
    ui._home_role = "guest"
    ui.navigate_to("vpn_selector")
    ui._polling_status = False
    ui.p_check()
    # Only the Streaming indicator's component; the network methods come
    # from the private-network overview, not from process probes.
    assert wait_for(lambda: probed == ["moonlight"])
    assert wait_for(lambda: not ui._polling_status)
    ui.navigate_to("host")
    ui._polling_status = False
    ui.p_check()
    assert wait_for(lambda: probed == ["moonlight", "sunshine"])
    assert wait_for(lambda: not ui._polling_status)


def test_simple_join_pages_ask_for_one_thing_in_plain_words(ui):
    import big_remote_play.ui.private_network_view as pnv

    zerotier = pnv.ConnectPage("zerotier", ui)
    content = texts(zerotier)
    assert "Type the network code you received." in content
    assert "The person who created the network gives you this code." in content
    assert "ZeroTier Central" not in content  # the website route is an Advanced-mode detail
    zerotier._e_netid.set_text("8056 c2e2-1c00 0001")
    joined = []
    zerotier._join_zerotier = joined.append
    zerotier._on_connect(None)
    assert joined == ["8056c2e21c000001"]  # spaces and dashes from a copied code are ignored

    headscale = pnv.ConnectPage("headscale", ui)
    assert "I don't have a server yet" in texts(headscale)


def test_a_failed_pairing_explains_and_offers_to_try_again(ui, monkeypatch):
    """After Sunshine refused a retry ("a pairing session already exists"), the
    game PC clears the abandoned attempt within seconds; trying again works."""
    guest = ui.guest_view
    attempts = []

    class Moonlight(FakeMoonlight):
        def pair(self, host_ip, on_pin_callback=None, *, port=47989, cancel_event=None):
            attempts.append(host_ip)
            if on_pin_callback:
                on_pin_callback("1234")
            return False

    guest.moonlight = Moonlight(False)
    guest.start_pairing_flow({"name": "desk", "ip": "100.64.0.2", "port": 47989})
    assert wait_for(lambda: ui.get_visible_dialog() is not None and ui.get_visible_dialog().has_response("retry"))
    dialog = ui.get_visible_dialog()
    assert "already exists" in dialog.get_body() and "Share" in dialog.get_body()
    dialog.emit("response", "retry")
    dialog.close()
    assert wait_for(lambda: len(attempts) == 2)


# ── a missing client is installed before signing in ───────────────────────


def _tailscale_missing(monkeypatch, installed: set[str]):
    from big_remote_play.utils import dependencies

    monkeypatch.setattr(dependencies, "_AUDIT", lambda ids: [dependencies.ComponentState(i, i in installed) for i in ids])
    plan = dependencies.InstallPlan("pamac", ("pamac", "install", "--no-confirm", "tailscale"), ("tailscale",), ("tailscale",), ("tailscaled",))
    monkeypatch.setattr(dependencies, "_PLAN", lambda ids: plan)
    started: list[str] = []

    def run(plan, *, on_line=None, start_unit=None, probe=None):
        installed.update(plan.components)
        for unit in plan.units:
            started.append(unit)
        return dependencies.InstallOutcome(tuple(probe(plan.components)), 0)

    monkeypatch.setattr(dependencies, "_RUN", run)
    return started


def test_browser_sign_in_is_not_offered_while_tailscale_is_missing(ui, monkeypatch):
    import big_remote_play.ui.private_network_view as pnv

    _tailscale_missing(monkeypatch, set())
    page = pnv.ConnectPage("tailscale", ui)
    assert wait_for(lambda: page._install_slot.get_visible())
    assert not page._connect_form.get_visible()
    content = texts(page._install_slot)
    assert "Tailscale is not installed yet" in content and "Install and continue" in content and "Not now" in content


def test_after_installing_tailscale_the_sign_in_starts_by_itself(ui, monkeypatch):
    import big_remote_play.ui.private_network_view as pnv

    started = _tailscale_missing(monkeypatch, set())
    signed_in = []
    monkeypatch.setattr(pnv.ConnectPage, "_connect_tailnet", lambda self, **kwargs: signed_in.append(kwargs))
    page = pnv.ConnectPage("tailscale", ui)
    assert wait_for(lambda: page._install_slot.get_visible())
    section = page._install_slot.get_first_child()
    assert wait_for(lambda: section.checklist.button.get_visible())
    section.checklist.button.emit("clicked")
    assert wait_for(lambda: bool(signed_in), timeout=5)
    assert started == ["tailscaled"]  # its service was started for the sign-in
    assert page._connect_form.get_visible() and not page._install_slot.get_visible()
    assert len(signed_in) == 1
