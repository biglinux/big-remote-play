"""Connect your devices: three independent methods, each an ordinary page.

No real VPN client, API or keyring is reached: services are fakes built on
the offline service, and APIs are scripted objects.
"""

from __future__ import annotations

from types import SimpleNamespace
import time

import pytest

from big_remote_play.private_network import service as service_module
from big_remote_play.private_network.device_list import ProviderDevice, simple_state, tailnet_listing, zerotier_listing
from big_remote_play.private_network.http import ApiResult
from big_remote_play.private_network.models import ConnectionState as S, OverlayNetwork, PeerDevice, ProviderCapabilities, ProviderId as P, ProviderStatus, Recovery as R
from big_remote_play.private_network.service import PrivateNetworkService
from big_remote_play.private_network.zerotier_api import CentralMember
from big_remote_play.utils.vpn_accounts import CommandResult

ME_TS = PeerDevice("game-pc", ("100.64.0.1",), is_self=True, node_id="nSELF", dns_name="game-pc.tail0.ts.net")
TS_CONNECTED = ProviderStatus(
    P.TAILSCALE,
    S.CONNECTED,
    self_device=ME_TS,
    network_name="family.ts.net",
    peers=(PeerDevice("notebook", ("100.64.0.2",), online=True, node_id="nNOTE"), PeerDevice("living-room", ("100.64.0.3",), online=False, node_id="nLIVE")),
)
TS_OFF = ProviderStatus(P.TAILSCALE, S.DISCONNECTED, recovery=R.RECONNECT)
TS_RUNNING_NOT_SIGNED_IN = ProviderStatus(P.TAILSCALE, S.NEEDS_AUTHENTICATION)
TS_MISSING = ProviderStatus(P.TAILSCALE, S.UNAVAILABLE, installed=False, recovery=R.INSTALL)
ZT_NETWORKS = (
    OverlayNetwork("8056c2e21c000001", "Home Gaming", S.CONNECTED, ("10.147.17.5",)),
    OverlayNetwork("8056c2e21c000002", "Work", S.CONNECTED, ("10.200.0.5",)),
)
ZT_CONNECTED = ProviderStatus(P.ZEROTIER, S.CONNECTED, network_id="8056c2e21c000001", self_device=PeerDevice("game-pc", ("10.147.17.5",), is_self=True, node_id="aaaaaaaaaa"), networks=ZT_NETWORKS)
HS_OFF = ProviderStatus(P.HEADSCALE, S.DISCONNECTED)


# ── the five words ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "key"),
    [
        (TS_MISSING, "not_installed"),
        (TS_OFF, "off"),
        (ProviderStatus(P.TAILSCALE, S.UNAVAILABLE, recovery=R.START_SERVICE), "off"),
        (ProviderStatus(P.TAILSCALE, S.CONNECTING), "connecting"),
        (TS_CONNECTED, "connected"),
        # The service runs, but nobody is signed in: never "connected".
        (TS_RUNNING_NOT_SIGNED_IN, "attention"),
        (ProviderStatus(P.ZEROTIER, S.ERROR, recovery=R.GRANT_ACCESS), "attention"),
        (ProviderStatus(P.ZEROTIER, S.NEEDS_AUTHORIZATION), "attention"),
        (HS_OFF, "off"),
    ],
)
def test_each_state_becomes_one_of_five_words(status, key):
    assert simple_state(status).key == key


# ── device lists: one provider at a time ─────────────────────────────────


def api_device(device_id, node_id, last_seen=""):
    return SimpleNamespace(device_id=device_id, node_id=node_id, addresses=(), last_seen=last_seen)


def test_tailnet_list_marks_this_device_and_removes_only_with_the_api():
    without = tailnet_listing(TS_CONNECTED)
    assert [(d.name, d.is_local, d.online, d.address) for d in without.devices] == [
        ("game-pc", True, True, "100.64.0.1"),
        ("notebook", False, True, "100.64.0.2"),
        ("living-room", False, False, "100.64.0.3"),
    ]
    assert not without.can_remove and not any(d.removable for d in without.devices)
    assert without.manage_url.startswith("https://login.tailscale.com/")

    with_api = tailnet_listing(TS_CONNECTED, [api_device("d-note", "nNOTE"), api_device("d-live", "nLIVE", "2026-10-06T10:00:00Z")])
    removable = {d.name: (d.removable, d.id) for d in with_api.devices}
    assert removable == {"game-pc": (False, ""), "notebook": (True, "d-note"), "living-room": (True, "d-live")}
    assert next(d for d in with_api.devices if d.name == "living-room").last_seen is not None


def test_a_method_that_is_not_connected_lists_nothing():
    assert tailnet_listing(TS_OFF).devices == ()


def test_zerotier_lists_one_network_and_only_this_computer_without_the_api():
    listing = zerotier_listing(ZT_CONNECTED)
    assert [n.name for n in listing.networks] == ["Home Gaming", "Work"]
    assert listing.network_id == "8056c2e21c000001"
    assert [(d.name, d.is_local, d.address) for d in listing.devices] == [("game-pc", True, "10.147.17.5")]
    assert not listing.complete and listing.manage_url.endswith("8056c2e21c000001")

    work = zerotier_listing(ZT_CONNECTED, "8056c2e21c000002")
    assert work.devices[0].address == "10.200.0.5"  # this network's address, not the other one's


def test_zerotier_members_of_one_network_with_approval_and_last_seen():
    now = 1_000_000.0
    members = [
        CentralMember("aaaaaaaaaa", "game-pc", True, ("10.147.17.5",), last_seen=now),
        CentralMember("bbbbbbbbbb", "Notebook", True, ("10.147.17.6",), last_seen=now - 60),
        CentralMember("cccccccccc", "Old laptop", True, ("10.147.17.7",), last_seen=now - 7200),
        CentralMember("dddddddddd", "", False),
        CentralMember("eeeeeeeeee", "Rejected", False, rejected=True),
    ]
    listing = zerotier_listing(ZT_CONNECTED, "8056c2e21c000001", members, now=now)
    rows = {d.name: d for d in listing.devices}
    assert set(rows) == {"game-pc", "Notebook", "Old laptop", "dddddddddd"}
    assert rows["game-pc"].is_local and not rows["game-pc"].removable
    assert rows["Notebook"].online is True and rows["Old laptop"].online is False
    assert rows["dddddddddd"].waiting and rows["Notebook"].removable and listing.complete


def test_device_rows_say_this_device_state_ip_and_last_seen():
    from big_remote_play.ui.provider_page import device_lines, last_seen_text

    now = 10_000.0
    assert device_lines(ProviderDevice("x", "game-pc", "100.64.0.1", True, is_local=True)) == ["This device · Online", "100.64.0.1"]
    assert device_lines(ProviderDevice("y", "tv", "100.64.0.3", False, last_seen=now - 7200), now=now) == ["Offline", "100.64.0.3", "Last seen 2 hours ago"]
    assert last_seen_text(now - 30, now=now) == "Last seen just now"
    assert last_seen_text(now - 3 * 86400, now=now) == "Last seen 3 days ago"


@pytest.mark.parametrize(
    ("status", "action"),
    [
        (TS_MISSING, "install"),
        (TS_OFF, "start"),
        (TS_CONNECTED, "stop"),
        (TS_RUNNING_NOT_SIGNED_IN, "sign_in"),
        (HS_OFF, "setup"),
        (ProviderStatus(P.ZEROTIER, S.ERROR, recovery=R.GRANT_ACCESS), "allow"),
        (ProviderStatus(P.ZEROTIER, S.ERROR), "retry"),
    ],
)
def test_the_connection_section_offers_one_action_per_state(status, action):
    from big_remote_play.ui.provider_page import connection_words

    assert connection_words(status.provider, status, simple_state(status))[2] == action


# ── the service: devices, removal and Tailscale/Headscale sharing one app ──


class FakeTailscaleApi:
    def __init__(self):
        self.calls = []

    def list_devices(self):
        return [api_device("d-note", "nNOTE"), api_device("d-live", "nLIVE")], ApiResult(True, 200)

    def delete_device(self, device_id):
        self.calls.append(("delete", device_id))
        return ApiResult(True, 200)


class FakeCentral:
    def __init__(self):
        self.calls = []

    def list_members(self, network_id):
        self.calls.append(("list", network_id))
        return [CentralMember("aaaaaaaaaa", "game-pc", True, ("10.147.17.5",)), CentralMember("bbbbbbbbbb", f"member-of-{network_id[-1]}", True, ("10.147.17.6",))], ApiResult(True, 200)

    def remove_member(self, network_id, node_id):
        self.calls.append(("remove", network_id, node_id))
        return ApiResult(True, 200)


class Service(service_module.OfflinePrivateNetworkService):
    """The real device_listing/remove_device/start/stop over scripted statuses."""

    def __init__(self, statuses=(), *, tailscale_api=None, central=None, after=None):
        super().__init__()
        self._statuses = {status.provider: status for status in statuses}
        self._ts_api = tailscale_api
        self._central = central
        self.after = after or {}
        self.calls = []

    def status(self, provider):
        return self._statuses.get(provider, ProviderStatus(provider, S.DISCONNECTED))

    def overview(self):
        return [self.status(provider) for provider in (P.TAILSCALE, P.ZEROTIER, P.HEADSCALE)]

    def capabilities(self, provider):
        return ProviderCapabilities(can_remove_device=self._ts_api is not None or self._central is not None)

    def tailscale_api(self):
        return self._ts_api

    def zerotier_central(self):
        return self._central

    def headscale_server(self):
        return "https://vpn.example.test"

    def internet_available(self):
        return True

    def _act(self, name, provider):
        self.calls.append((name, provider))
        if provider in self.after:
            self._statuses[provider] = self.after[provider]
        return True

    def start(self, provider):
        return self._act("start", provider)

    def stop(self, provider):
        return self._act("stop", provider)

    def link_sample(self, status):
        from big_remote_play.utils.connection_health import LinkSample

        return LinkSample(18.0, "direct", "notebook")


def test_two_methods_connected_at_once_keep_their_own_devices():
    service = Service([TS_CONNECTED, ZT_CONNECTED], tailscale_api=FakeTailscaleApi(), central=FakeCentral())
    tailscale = service.device_listing(P.TAILSCALE)
    zerotier = service.device_listing(P.ZEROTIER, "8056c2e21c000002")
    assert {d.address for d in tailscale.devices} == {"100.64.0.1", "100.64.0.2", "100.64.0.3"}
    assert {d.name for d in zerotier.devices} == {"game-pc", "member-of-2"}
    assert not {d.address for d in tailscale.devices} & {d.address for d in zerotier.devices}
    assert service.zerotier_central().calls == [("list", "8056c2e21c000002")]  # only the network shown


def test_removal_goes_to_the_api_of_that_method_and_network():
    tailscale, central = FakeTailscaleApi(), FakeCentral()
    service = Service([TS_CONNECTED, ZT_CONNECTED], tailscale_api=tailscale, central=central)
    assert service.remove_device(P.TAILSCALE, "d-note").ok
    assert service.remove_device(P.ZEROTIER, "bbbbbbbbbb", "8056c2e21c000001").ok
    assert tailscale.calls == [("delete", "d-note")] and central.calls == [("remove", "8056c2e21c000001", "bbbbbbbbbb")]
    assert not Service([TS_CONNECTED]).remove_device(P.TAILSCALE, "d-note").ok  # no API: nothing pretended


class Profiles:
    """VPNAccountManager stand-in: saved Tailscale-app accounts and switching."""

    def __init__(self, selected="ts1"):
        self.selected_id = selected
        self.calls = []

    def list_tailscale_profiles(self):
        profiles = (
            SimpleNamespace(profile_id="ts1", provider="tailscale", login_server="", selected=self.selected_id == "ts1"),
            SimpleNamespace(profile_id="hs1", provider="headscale", login_server="https://vpn.example.test", selected=self.selected_id == "hs1"),
        )
        return SimpleNamespace(profiles=profiles, selected=next(p for p in profiles if p.selected))

    def switch_tailscale_profile(self, profile_id):
        self.calls.append(("switch", profile_id))
        self.selected_id = profile_id
        return CommandResult(0)


def test_headscale_with_a_saved_account_is_off_and_start_switches_the_tailscale_app():
    facade = SimpleNamespace(manager=Profiles(), calls=[])
    facade._saved_profile = lambda provider: PrivateNetworkService._saved_profile(facade, provider)
    off = ProviderStatus(P.HEADSCALE, S.DISCONNECTED, technical_detail="tailscaled is using Tailscale", recovery=R.RECONNECT)
    states = [off, ProviderStatus(P.HEADSCALE, S.CONNECTED)]
    facade.status = lambda provider: states.pop(0) if len(states) > 1 else states[0]
    facade.start_service = lambda provider: True
    facade.turn_on = lambda provider: facade.calls.append(("up", provider)) or True
    assert simple_state(off).key == "off"
    assert PrivateNetworkService.start(facade, P.HEADSCALE) is True  # type: ignore[arg-type]
    assert facade.manager.calls == [("switch", "hs1")]


# ── the pages ─────────────────────────────────────────────────────────────

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from test_ui_task_flows import drain, ui as _ui_fixture  # noqa: E402

ui = _ui_fixture


def wait(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        drain()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def texts(widget) -> str:
    found, stack = [], [widget]
    while stack:
        current = stack.pop()
        for getter in ("get_label", "get_title", "get_subtitle"):
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


def buttons(widget, label):
    found, stack = [], [widget]
    while stack:
        current = stack.pop()
        if isinstance(current, Gtk.Button) and current.get_label() == label:
            found.append(current)
        child = current.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return found


def open_page(ui, service, provider="tailscale", *, from_page="vpn_selector"):
    service_module.set_default_factory(lambda: service)
    ui.navigate_to(from_page)
    page = ui.open_provider(provider)
    assert wait(lambda: page.status is not None and page.listing is not None)
    return page


def test_the_list_shows_three_independent_methods_and_opens_pages_not_dialogs(ui):
    service = Service([TS_CONNECTED, ZT_CONNECTED])
    service_module.set_default_factory(lambda: service)
    ui.navigate_to("vpn_selector")
    hub = ui.remote_connection_page
    hub.refresh()
    assert wait(lambda: all(card.state is not None for card in hub.cards.values()))
    assert {provider: card.state.key for provider, card in hub.cards.items()} == {P.TAILSCALE: "connected", P.ZEROTIER: "connected", P.HEADSCALE: "off"}
    assert "Recommended" in texts(hub.cards[P.TAILSCALE]) and "Advanced" in texts(hub.cards[P.HEADSCALE])
    assert "8056c2e21c000001" not in texts(hub)  # no network ids, tokens or addresses here

    hub.cards[P.ZEROTIER].open_button.emit("clicked")
    page = ui.network_navigation.get_visible_page()
    assert page.provider is P.ZEROTIER and ui.current_page == "vpn_selector"
    assert ui.get_visible_dialog() is None
    # The header carries the page's tabs, and Back returns to the list.
    assert ui.header_view_switcher.get_stack() is page.view_stack
    assert [page.view_stack.get_pages().get_item(i).get_title() for i in range(2)] == ["Devices", "Advanced"]
    assert ui.network_back_button.get_visible()
    ui.network_back_button.emit("clicked")
    assert wait(lambda: ui.network_navigation.get_visible_page().get_tag() == "providers")
    assert not ui.network_back_button.get_visible()


def test_tailscale_page_lists_its_devices_with_this_device_and_copy_ip(ui, monkeypatch):
    import big_remote_play.ui.provider_page as module

    copied = []
    monkeypatch.setattr(module, "copy_to_clipboard", lambda widget, value, toast=None: copied.append(value))
    page = open_page(ui, Service([TS_CONNECTED, ZT_CONNECTED]))
    devices = texts(page.devices)
    assert "This device · Online" in devices and "notebook" in devices and "living-room" in devices
    assert "10.147.17.5" not in devices  # never ZeroTier's addresses
    buttons(page.devices, "Copy IP")[-1].emit("clicked")
    assert copied and copied[0].startswith("100.64.0.")
    assert page.add_button.get_visible()
    assert page.connection_label.get_label() == "Stop"


def test_remove_is_in_the_menu_and_asks_first_only_when_the_api_can_do_it(ui, monkeypatch):
    api = FakeTailscaleApi()
    page = open_page(ui, Service([TS_CONNECTED], tailscale_api=api))
    notebook = next(row for row in page.devices.dynamic_rows if getattr(row, "_brp_device", None) and row._brp_device.name == "notebook")
    menu = next(widget for widget in [notebook, *_children(notebook)] if isinstance(widget, Gtk.MenuButton))
    assert "Remove device" in texts(menu.get_popover())
    assert "Remove device" not in [b.get_label() for b in buttons(page.devices, "Remove device") if b.get_mapped()]  # not exposed on the row
    presented = []
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda self, parent: presented.append(self))
    page.confirm_remove(notebook._brp_device)
    assert presented and presented[0].get_heading() == "Remove notebook?" and api.calls == []
    presented[0].emit("response", "remove")
    assert wait(lambda: api.calls == [("delete", "d-note")])


def _children(widget):
    stack, found = [widget], []
    while stack:
        current = stack.pop()
        child = current.get_first_child()
        while child is not None:
            found.append(child)
            stack.append(child)
            child = child.get_next_sibling()
    return found


def test_without_api_there_is_no_remove_only_manage_devices(ui):
    page = open_page(ui, Service([TS_CONNECTED]))
    notebook = next(row for row in page.devices.dynamic_rows if getattr(row, "_brp_device", None) and row._brp_device.name == "notebook")
    menu = next(widget for widget in _children(notebook) if isinstance(widget, Gtk.MenuButton))
    content = texts(menu.get_popover())
    assert "Remove device" not in content and "Manage devices" in content


def test_zerotier_page_has_a_network_choice_and_never_shows_tailscale_devices(ui):
    page = open_page(ui, Service([TS_CONNECTED, ZT_CONNECTED], central=FakeCentral()), "zerotier")
    assert page.network_row.get_visible()
    assert "member-of-1" in texts(page.devices) and "notebook" not in texts(page.devices)
    page.network_row.set_selected(1)
    assert wait(lambda: page.listing is not None and page.listing.network_id == "8056c2e21c000002")
    assert "member-of-2" in texts(page.devices) and "member-of-1" not in texts(page.devices)


def test_zerotier_without_api_explains_the_website_instead_of_an_empty_list(ui):
    page = open_page(ui, Service([ZT_CONNECTED]), "zerotier")
    content = texts(page.devices)
    assert "listed on the ZeroTier website" in content and "No other device yet" not in content
    assert buttons(page.devices, "Manage devices")


def test_off_starts_and_on_stops_after_a_confirmation(ui, monkeypatch):
    import big_remote_play.ui.network_common as common

    service = Service([TS_OFF], after={P.TAILSCALE: TS_CONNECTED})
    page = open_page(ui, service)
    assert page.connection_label.get_label() == "Start" and not page.add_button.get_visible()
    page.connection_button.emit("clicked")
    assert wait(lambda: service.calls == [("start", P.TAILSCALE)])
    assert wait(lambda: page.connection_label.get_label() == "Stop")

    asked = []
    monkeypatch.setattr(common, "confirm", lambda parent, heading, body, action, run: asked.append(heading) or run())
    service.after = {P.TAILSCALE: TS_OFF}
    page.connection_button.emit("clicked")
    assert asked == ["Stop Tailscale?"]
    assert wait(lambda: service.calls[-1] == ("stop", P.TAILSCALE))
    assert wait(lambda: page.connection_label.get_label() == "Start")


def test_not_installed_offers_install_and_continue_then_goes_on_by_itself(ui):
    page = open_page(ui, Service([TS_MISSING]))
    assert page.install_slot.get_visible() and not page.connection_button.get_visible()
    page._after_install()
    assert wait(lambda: ui.network_navigation.get_visible_page() is page.setup_page)


def test_sign_in_is_a_page_in_the_window_and_starts_the_browser_sign_in(ui, monkeypatch):
    import big_remote_play.ui.private_network_view as pnv

    signed_in = []
    monkeypatch.setattr(pnv.ConnectPage, "_connect_tailnet", lambda self, **kwargs: signed_in.append(kwargs))
    page = open_page(ui, Service([TS_RUNNING_NOT_SIGNED_IN]))
    assert page.connection_label.get_label() == "Sign in"
    page.connection_button.emit("clicked")
    assert wait(lambda: bool(signed_in))
    assert ui.network_navigation.get_visible_page() is page.setup_page and ui.get_visible_dialog() is None


def test_technical_details_live_in_advanced(ui):
    page = open_page(ui, Service([ZT_CONNECTED]), "zerotier")
    devices_tab = texts(page.view_stack.get_child_by_name("devices"))
    advanced_tab = texts(page.view_stack.get_child_by_name("advanced"))
    for technical in ("8056c2e21c000001", "aaaaaaaaaa", "Network ID", "Node ID"):
        assert technical not in devices_tab
    assert "8056c2e21c000001" in advanced_tab and "Node ID" in advanced_tab


def test_advanced_measures_the_connection_only_while_it_is_shown(ui, monkeypatch):
    monkeypatch.setattr("big_remote_play.utils.connection_health.read_interface_bytes", lambda prefixes: (1_000_000, 500_000))
    page = open_page(ui, Service([TS_CONNECTED]))
    assert not page._quality_timer
    page.view_stack.set_visible_child_name("advanced")
    assert page.quality.get_visible() and page._quality_timer
    for _ in range(3):
        page._sample_quality()
        wait(lambda: not page._quality_busy)
    assert page.quality_rows["stability"].get_subtitle() == "Stable · 18 ms"
    assert page.quality_rows["path"].get_subtitle() == "Direct connection"
    page.view_stack.set_visible_child_name("devices")
    assert not page._quality_timer


@pytest.mark.parametrize(("from_page", "label"), [("host", "Back to Share"), ("guest", "Back to Connect")])
def test_once_connected_it_returns_to_the_task_it_came_from(ui, from_page, label):
    page = open_page(ui, Service([TS_CONNECTED]), from_page=from_page)
    assert page.ready.get_visible()
    back = buttons(page.ready, label)
    assert back
    back[0].emit("clicked")
    assert wait(lambda: ui.current_page == from_page)


def test_a_problem_is_named_and_a_disconnected_method_shows_no_devices(ui):
    page = open_page(ui, Service([ProviderStatus(P.TAILSCALE, S.ERROR, technical_detail="BackendState: InUseOtherUser")]))
    assert page.state.key == "attention" and page.connection_label.get_label() == "Try again"
    assert "BackendState" not in texts(page.view_stack.get_child_by_name("devices"))
    assert "BackendState" in texts(page.view_stack.get_child_by_name("advanced"))
    off = open_page(ui, Service([TS_OFF]))
    assert "Devices appear here once Tailscale is on" in texts(off.devices)


def test_the_share_card_of_a_method_opens_its_page(ui):
    service_module.set_default_factory(lambda: Service([TS_CONNECTED]))
    ui.navigate_to("host")
    ui.on_service_clicked("zerotier")
    assert ui.current_page == "vpn_selector" and ui.provider_page.provider is P.ZEROTIER
    assert ui._network_return_page == "host"


# ── kept from the former network details page and method dialog ─────────────


class Facade:
    """PrivateNetworkService.start/stop over scripted statuses and a manager."""

    def __init__(self, statuses, *, started=True):
        self.statuses = list(statuses)
        self.calls: list = []
        self.started = started
        self.manager = SimpleNamespace(stop_service=lambda unit: self.calls.append(("stop_service", unit)) or CommandResult(0), pause_tailscale=lambda: self.calls.append("down") or CommandResult(0))

    def status(self, provider):
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

    def start_service(self, provider):
        self.calls.append(("start_service", provider))
        return self.started

    def turn_on(self, provider):
        self.calls.append(("turn_on", provider))
        return True


def test_start_starts_the_service_then_turns_a_signed_in_connection_on():
    facade = Facade([ProviderStatus(P.TAILSCALE, S.UNAVAILABLE, recovery=R.START_SERVICE), TS_OFF])
    assert PrivateNetworkService.start(facade, P.TAILSCALE) is True  # type: ignore[arg-type]
    assert facade.calls == [("start_service", P.TAILSCALE), ("turn_on", P.TAILSCALE)]
    signed_out = Facade([TS_RUNNING_NOT_SIGNED_IN])
    assert PrivateNetworkService.start(signed_out, P.TAILSCALE) is True  # type: ignore[arg-type]
    assert signed_out.calls == []  # Start never signs in by itself


def test_stop_keeps_tailscale_accounts_and_stops_zerotiers_service_and_never_the_other_product():
    tailscale = Facade([TS_CONNECTED])
    assert PrivateNetworkService.stop(tailscale, P.TAILSCALE) is True  # type: ignore[arg-type]
    assert tailscale.calls == ["down"]
    zerotier = Facade([ZT_CONNECTED])
    assert PrivateNetworkService.stop(zerotier, P.ZEROTIER) is True  # type: ignore[arg-type]
    assert zerotier.calls == [("stop_service", "zerotier-one")]
    headscale = Facade([HS_OFF])
    assert PrivateNetworkService.stop(headscale, P.HEADSCALE) is False  # type: ignore[arg-type]
    assert headscale.calls == []


def test_tailscale_stop_is_down_never_logout():
    from big_remote_play.utils.vpn_accounts import VPNAccountManager

    commands: list = []
    manager = VPNAccountManager(SimpleNamespace(tailscale_cmd=lambda: ["tailscale"]), runner=lambda argv, timeout=15: commands.append(list(argv)) or CommandResult(0))
    facade = SimpleNamespace(manager=manager, status=lambda provider: TS_CONNECTED)
    assert PrivateNetworkService.stop(facade, P.TAILSCALE)  # type: ignore[arg-type]
    assert ["tailscale", "down"] in commands and not any("logout" in command for command in commands)


def test_stopping_a_service_is_allowlisted_and_skipped_when_already_stopped():
    from big_remote_play.utils.vpn_accounts import VPNAccountManager

    commands: list = []
    manager = VPNAccountManager(SimpleNamespace(tailscale_cmd=lambda: ["tailscale"]), runner=lambda argv, timeout=15: commands.append(list(argv)) or CommandResult(0))
    assert manager.stop_service("sshd").returncode == 2 and commands == []
    manager.stop_service("zerotier-one")
    assert commands[-1] == ["pkexec", "/usr/bin/systemctl", "stop", "zerotier-one"]
    commands.clear()
    inactive = VPNAccountManager(SimpleNamespace(tailscale_cmd=lambda: ["tailscale"]), runner=lambda argv, timeout=15: commands.append(list(argv)) or CommandResult(3))
    assert inactive.stop_service("tailscaled").returncode == 0 and all(command[0] != "pkexec" for command in commands)


def test_a_stopped_client_shows_no_stale_address_and_its_detail_stays_in_advanced(ui):
    stopped = ProviderStatus(P.TAILSCALE, S.DISCONNECTED, self_device=ME_TS, recovery=R.RECONNECT, technical_detail="BackendState: Stopped")
    page = open_page(ui, Service([stopped]))
    assert "100.64.0.1" not in texts(page.view_stack.get_child_by_name("devices"))
    assert "BackendState: Stopped" in texts(page.view_stack.get_child_by_name("advanced"))


def test_permission_is_asked_only_when_the_person_chooses_allow(ui):
    granted = []
    service = Service([ProviderStatus(P.ZEROTIER, S.ERROR, recovery=R.GRANT_ACCESS)])
    service.grant_zerotier_access = lambda: granted.append(True) or CommandResult(0)
    page = open_page(ui, service, "zerotier")
    assert page.connection_label.get_label() == "Allow" and not granted
    page.connection_button.emit("clicked")
    assert wait(lambda: granted == [True])


def test_waiting_for_approval_says_so_and_keeps_the_code_in_advanced(ui):
    waiting = ProviderStatus(
        P.ZEROTIER,
        S.NEEDS_AUTHORIZATION,
        network_id="8056c2e21c000001",
        self_device=PeerDevice("pc", (), is_self=True, node_id="a1b2c3d4e5"),
        networks=(OverlayNetwork("8056c2e21c000001", "games", S.NEEDS_AUTHORIZATION),),
    )
    page = open_page(ui, Service([waiting]), "zerotier")
    assert "approve this computer" in page.connection_row.get_subtitle()
    advanced = texts(page.view_stack.get_child_by_name("advanced"))
    assert "a1b2c3d4e5" in advanced and "8056c2e21c000001" in advanced


def test_names_from_the_network_are_never_markup(ui):
    evil = ProviderStatus(P.TAILSCALE, S.CONNECTED, self_device=PeerDevice("<i>pc</i>", ("100.64.0.1",), is_self=True), peers=(PeerDevice("<b>Evil</b> & Co", ("100.64.0.9",), online=True),))
    page = open_page(ui, Service([evil]))
    rows = [row for row in page.devices.dynamic_rows if isinstance(row, Adw.ActionRow)]
    assert any(row.get_title() == "<b>Evil</b> & Co" for row in rows)
    assert all(not row.get_use_markup() for row in rows)


# ── tools of a method are pages too ──────────────────────────────────────────


@pytest.fixture
def quiet_tools(monkeypatch):
    """No interface, keyring or VPN client is read by the tool pages."""
    import big_remote_play.private_network.diagnostics as diagnostics
    import big_remote_play.ui.api_access_dialog as api_access
    import big_remote_play.ui.vpn_accounts_dialog as accounts

    monkeypatch.setattr(diagnostics, "local_network_facts", lambda: SimpleNamespace(stack="none", cgnat_suspected=False, overlay_interfaces=(), ipv4=()))
    monkeypatch.setattr(diagnostics, "active_firewall", lambda: "")
    monkeypatch.setattr(api_access.ApiAccessDialog, "refresh", lambda self: None)
    monkeypatch.setattr(accounts.VPNAccountsDialog, "refresh", lambda self, **_kwargs: None)


def test_advanced_tools_open_as_pages_with_back_never_as_dialogs(ui, quiet_tools):
    page = open_page(ui, Service([TS_CONNECTED]), "headscale")
    navigation = ui.network_navigation
    for open_tool, title in (
        (page._api_access, "API access"),
        (page.main_window.show_vpn_accounts, "VPN accounts and networks"),
        (page._network_check, "Internet, router and firewall"),
        (page.show_setup, "Set up Headscale"),
    ):
        open_tool()
        shown = navigation.get_visible_page()
        assert shown is not page and shown.get_title() == title
        assert ui.get_visible_dialog() is None
        assert ui.network_back_button.get_visible()
        ui.network_back_button.emit("clicked")
        assert wait(lambda: navigation.get_visible_page() is page)


def test_the_list_opens_its_guides_as_pages_without_naming_a_method(ui, quiet_tools):
    open_page(ui, Service([TS_CONNECTED]))  # a method opened earlier must not title the guide
    ui.network_navigation.pop_to_tag("providers")
    ui.show_internet_check()
    shown = ui.network_navigation.get_visible_page()
    assert shown.get_title() == "Internet, router and firewall" and ui.get_visible_dialog() is None
    assert ui.content_title.get_subtitle() == ""


def test_a_tool_page_stops_its_work_when_navigation_jumps_past_it(ui):
    from big_remote_play.ui.network_common import push_page

    open_page(ui, Service([TS_CONNECTED]))
    closed = []
    tool = Adw.NavigationPage(title="tool", child=Gtk.Label())
    push_page(ui.network_navigation, tool, on_closed=lambda: closed.append(True))
    ui.network_navigation.push(Adw.NavigationPage(title="deeper", child=Gtk.Label()))
    ui.network_navigation.pop()
    assert closed == []
    ui.network_navigation.pop_to_tag("providers")
    assert closed == [True]


def test_set_up_steps_and_previous_networks_are_pages_and_return_to_the_form(ui, quiet_tools):
    page = open_page(ui, Service([TS_RUNNING_NOT_SIGNED_IN]))
    page.show_setup()
    navigation = ui.network_navigation
    connect = page.setup_page.get_child()
    connect._show_tailscale_instructions()
    assert navigation.get_visible_page().get_title() == "Tailscale Instructions" and ui.get_visible_dialog() is None
    navigation.pop()
    connect._present_history()
    assert navigation.get_visible_page().get_tag() == "previous-networks"
    connect._history_close()  # what choosing a saved network does
    assert wait(lambda: navigation.get_visible_page() is page.setup_page)


def test_a_device_can_be_renamed_here_and_checked_through_connect(ui, monkeypatch, tmp_path):
    from big_remote_play.private_network.devices import DevicePreferences

    page = open_page(ui, Service([TS_CONNECTED]))
    page.preferences = DevicePreferences(tmp_path / "devices.json")
    notebook = next(row._brp_device for row in page.devices.dynamic_rows if getattr(row, "_brp_device", None) and row._brp_device.name == "notebook")
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda self, parent: None)
    dialog = page.rename(notebook)
    dialog.get_extra_child().set_text("Sala <b>")
    dialog.emit("response", "save")
    titles = [row.get_title() for row in page.devices.dynamic_rows if getattr(row, "_brp_device", None)]
    assert "Sala <b>" in titles and "notebook" not in titles  # only this computer's name, never markup
    assert DevicePreferences(tmp_path / "devices.json").display_name("100.64.0.2", "") == "Sala <b>"

    checked = []
    monkeypatch.setattr(ui.guest_view, "diagnose_host", lambda host: checked.append(host))
    page.check_connection(notebook)
    assert checked and checked[0]["ip"] == "100.64.0.2" and checked[0]["provider"] == "tailscale"
