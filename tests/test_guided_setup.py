"""Guided setup, the sidebar groups and the live connection cards, in real GTK.

The private-network service is a fake installed through the service factory;
no VPN client, Sunshine or Moonlight runs.
"""

from __future__ import annotations

import time

import pytest
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Gtk  # noqa: E402

from big_remote_play.private_network import service as service_module  # noqa: E402
from big_remote_play.private_network.models import ConnectionState, ProviderId, ProviderStatus, Recovery  # noqa: E402
from big_remote_play.utils.connection_health import ConnectionInfo, Health, LinkSample, Quality, Transport  # noqa: E402

from test_private_network_ui import TS_CONNECTED, ZT_CONNECTED, HubService, texts  # noqa: E402
from test_ui_task_flows import drain, ui as _ui_fixture  # noqa: E402

ui = _ui_fixture


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


def visible_tag(ui):
    page = ui.home_navigation.get_visible_page()
    return page.get_tag() if page is not None else None


def cards(ui):
    """The choice cards on the visible guided page, by title."""
    page = ui.home_navigation.get_visible_page()
    found = {}
    stack = [page]
    while stack:
        widget = stack.pop()
        if isinstance(widget, Gtk.Button) and widget.has_css_class("brp-guided-choice"):
            found[widget._brp_title] = widget
        child = widget.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return found


def buttons(ui, label):
    page = ui.home_navigation.get_visible_page()
    stack, found = [page], []
    while stack:
        widget = stack.pop()
        if isinstance(widget, Gtk.Button) and widget.get_label() == label and widget.get_visible():
            found.append(widget)
        child = widget.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return found


def start(ui, service=None):
    service_module.set_default_factory(lambda: service or HubService())
    ui.guided_setup_button.emit("clicked")
    drain()
    assert visible_tag(ui) == "guided-role"


def choose(ui, title):
    options = cards(ui)
    assert title in options, list(options)
    options[title].emit("clicked")
    drain()


# ── the guide ─────────────────────────────────────────────────────────────


def test_home_offers_the_guided_setup_first(ui):
    """The recommended start lives in the hero, before the two task cards."""
    assert ui.guided_setup_button.is_ancestor(ui.welcome_main_box)
    children = []
    child = ui.welcome_main_box.get_first_child()
    while child is not None:
        children.append(child)
        child = child.get_next_sibling()
    hero = next(child for child in children if ui.guided_setup_button.is_ancestor(child))
    assert hero.has_css_class("welcome-hero")
    assert children.index(hero) < children.index(ui.welcome_cards_box)
    assert ui.guided_setup_button.has_css_class("suggested-action")


@pytest.mark.parametrize("role, title", [("host", "Share my game"), ("guest", "Connect to another computer")])
def test_same_network_goes_straight_to_the_task_without_any_vpn(ui, role, title):
    service = HubService()
    start(ui, service)
    choose(ui, title)
    assert visible_tag(ui) == "guided-place"
    assert "Simplest" in texts(ui.home_navigation.get_visible_page())
    choose(ui, "On the same network")
    # This computer is checked first; with everything there the guide goes on by itself.
    assert wait_for(lambda: ui.current_page == role)
    assert visible_tag(ui) == "choices"  # Home is clean for the next visit


def test_internet_with_a_ready_connection_uses_it_and_returns_to_the_task(ui):
    start(ui, HubService([ZT_CONNECTED]))
    choose(ui, "Share my game")
    choose(ui, "Somewhere else")
    assert wait_for(lambda: bool(buttons(ui, "Use this connection")))
    content = texts(ui.home_navigation.get_visible_page())
    assert "We found a connection that is ready" in content and "ZeroTier" in content and "games" in content
    assert buttons(ui, "Choose another option")
    buttons(ui, "Use this connection")[0].emit("clicked")
    drain()
    assert ui.current_page == "host"
    assert ui._network_return_page == "host"


def test_internet_without_any_connection_asks_how_with_a_recommendation(ui):
    start(ui, HubService())
    choose(ui, "Connect to another computer")
    choose(ui, "Somewhere else")
    assert wait_for(lambda: visible_tag(ui) == "guided-method")
    options = cards(ui)
    assert set(options) == {"Tailscale", "I already use ZeroTier", "Advanced options"}
    assert "Recommended" in texts(options["Tailscale"])
    options["Tailscale"].emit("clicked")
    drain()
    assert ui.current_page == "connect_private" and ui._vpn_choice == "tailscale"
    assert ui._network_return_page == "guest"  # "Ready to play?" leads back to Connect


def test_a_connection_that_needs_one_step_hands_over_to_the_internet_page(ui):
    off = ProviderStatus(ProviderId.TAILSCALE, ConnectionState.DISCONNECTED, recovery=Recovery.RECONNECT)
    start(ui, HubService([off]))
    choose(ui, "Share my game")
    choose(ui, "Somewhere else")
    assert wait_for(lambda: "Your secure connection is turned off" in texts(ui.home_navigation.get_visible_page()))
    buttons(ui, "Continue")[0].emit("clicked")
    drain()
    assert ui.current_page == "vpn_selector"


def test_zerotier_in_the_guide_asks_only_for_the_code(ui, monkeypatch):
    monkeypatch.setattr(ui.system_check, "has_zerotier", lambda: True)
    start(ui, HubService())
    choose(ui, "Connect to another computer")
    choose(ui, "Somewhere else")
    assert wait_for(lambda: visible_tag(ui) == "guided-method")
    choose(ui, "I already use ZeroTier")
    page = ui.home_navigation.get_visible_page()
    content = texts(page)
    assert "Did you receive a network code?" in content
    assert "Network code" in content and "Join network" in content
    for technical in ("Node ID", "API", "CIDR", "Network ID"):
        assert technical not in content
    panel = ui.guided_setup._zerotier_panel
    assert panel is not None and panel.is_ancestor(page)


def test_without_a_zerotier_code_the_guide_explains_how_to_create_a_network(ui, monkeypatch):
    import big_remote_play.ui.private_network_view as pnv

    shown = []
    monkeypatch.setattr(pnv, "show_simple_instructions", lambda parent, title, items: shown.append((title, items)))
    monkeypatch.setattr(ui.system_check, "has_zerotier", lambda: True)
    start(ui, HubService())
    choose(ui, "Connect to another computer")
    choose(ui, "Somewhere else")
    assert wait_for(lambda: visible_tag(ui) == "guided-method")
    choose(ui, "I already use ZeroTier")
    buttons(ui, "I don't have a code")[0].emit("clicked")
    assert wait_for(lambda: ui.get_visible_dialog() is not None)
    dialog = ui.get_visible_dialog()
    assert dialog.has_response("create") and dialog.has_response("tailscale")
    dialog.emit("response", "create")
    dialog.close()
    assert [title for title, _items in shown] == ["Create a ZeroTier network"]
    steps = shown[0][1]
    assert len(steps) == 4 and steps[0][5] == "https://my.zerotier.com"  # the one place a network is created
    assert visible_tag(ui) == "guided-zerotier"  # the code field stays open behind the steps


def test_the_guide_installs_what_the_task_needs_and_continues_by_itself(ui, monkeypatch):
    from big_remote_play.utils import dependencies

    installed: set[str] = set()
    monkeypatch.setattr(dependencies, "_AUDIT", lambda ids: [dependencies.ComponentState(i, i in installed) for i in ids])
    plan = dependencies.InstallPlan("pamac", ("pamac", "install", "--no-confirm", "moonlight-qt"), ("moonlight",), ("moonlight-qt",), ())
    monkeypatch.setattr(dependencies, "_PLAN", lambda ids: plan)

    def run(plan, *, on_line=None, start_unit=None, probe=None):
        on_line("Transaction successfully finished.")
        installed.update(plan.components)
        return dependencies.InstallOutcome(tuple(probe(plan.components)), 0)

    monkeypatch.setattr(dependencies, "_RUN", run)
    start(ui)
    choose(ui, "Connect to another computer")
    choose(ui, "On the same network")
    assert visible_tag(ui) == "guided-ready"
    checklist = ui.guided_setup.checklist
    assert wait_for(lambda: checklist.button.get_visible())
    content = texts(ui.home_navigation.get_visible_page())
    assert "Moonlight" in content and "Not installed" in content
    assert ui.current_page == "welcome"  # nothing happens before the button
    checklist.button.emit("clicked")
    assert wait_for(lambda: ui.current_page == "guest", timeout=5)


def test_a_failed_installation_stays_on_the_page_with_a_retry(ui, monkeypatch):
    from big_remote_play.utils import dependencies

    monkeypatch.setattr(dependencies, "_AUDIT", lambda ids: [dependencies.ComponentState(i, False) for i in ids])
    plan = dependencies.InstallPlan("pamac", ("pamac", "install", "--no-confirm", "sunshine-bin"), ("sunshine",), ("sunshine-bin",), ())
    monkeypatch.setattr(dependencies, "_PLAN", lambda ids: plan)
    monkeypatch.setattr(dependencies, "_RUN", lambda plan, **kwargs: dependencies.InstallOutcome(tuple(kwargs["probe"](plan.components)), 1))
    start(ui)
    choose(ui, "Share my game")
    choose(ui, "On the same network")
    checklist = ui.guided_setup.checklist
    assert wait_for(lambda: checklist.button.get_visible())
    checklist.button.emit("clicked")
    assert wait_for(lambda: checklist.button.get_sensitive() and not checklist.busy)
    assert checklist.button_label.get_label() == "Try again"
    assert visible_tag(ui) == "guided-ready" and ui.current_page == "welcome"


def test_back_goes_one_question_back_and_restarting_is_clean(ui):
    start(ui)
    choose(ui, "Share my game")
    assert ui.home_back_button.get_visible()
    assert ui.content_title.get_title() == "Guided setup"
    ui.home_back_button.emit("clicked")
    assert wait_for(lambda: visible_tag(ui) == "guided-role")
    ui.home_back_button.emit("clicked")
    assert wait_for(lambda: visible_tag(ui) == "choices")
    assert not ui.home_back_button.get_visible()
    start(ui)  # closing and reopening starts from the first question
    ui.navigate_to("guest")
    ui.navigate_to("welcome")
    assert not ui.home_back_button.get_visible() or visible_tag(ui) != "choices"


# ── sidebar ──────────────────────────────────────────────────────────────


def test_sidebar_groups_are_separated_quietly(ui):
    rows = {page: row for row, page in ui._nav_page_by_row.items()}
    assert rows["welcome"].get_header() is None
    assert isinstance(rows["host"].get_header(), Gtk.Separator)
    assert rows["guest"].get_header() is None
    assert isinstance(rows["vpn_selector"].get_header(), Gtk.Separator)


def test_sidebar_internet_item_always_opens_the_internet_page(ui):
    ui._apply_vpn_selection("zerotier", destination="create_private")
    ui.navigate_to("welcome")
    row = next(row for row, page in ui._nav_page_by_row.items() if page == "vpn_selector")
    ui.nav_list.select_row(None)
    ui.nav_list.select_row(row)
    drain()
    assert ui.current_page == "vpn_selector"


# ── Connect: the computer being played on ────────────────────────────────


def test_connect_shows_one_compact_card_while_playing(ui):
    guest = ui.guest_view
    guest.current_host_ctx = {"type": "auto", "host": {"name": "PC Gamer", "ip": "192.168.56.50"}}
    guest.moonlight.connected_host = "192.168.56.50"
    guest._show_session_card()
    assert guest.session_box.get_visible()
    assert guest.session_card.name.get_label() == "PC Gamer"
    assert guest.session_card.quality.label.get_label() == "Measuring…"
    guest.is_connected = True
    guest.perf_monitor.update_connections([ConnectionInfo("PC Gamer", "192.168.56.50", Transport.LOCAL, Health(Quality.EXCELLENT, 2.0, 0.4, 0.0, 6))])
    card = guest.session_card
    assert card.quality.label.get_label() == "Excellent"
    assert card.latency.get_label() == "2 ms"
    assert card.transport.label.get_label() == "Local network"
    from big_remote_play.ui.connection_cards import summary_words

    assert summary_words(card.info) == "PC Gamer, Excellent, 2 ms, Local network"  # what a screen reader hears
    guest.perf_monitor.stop_monitoring()


# ── Share: connected now is not paired ───────────────────────────────────


def test_share_lists_devices_connected_now_apart_from_paired_ones(ui):
    host = ui.host_view
    host.perf_monitor.update_timer_active = True
    host.perf_monitor.update_connections(
        [
            ConnectionInfo("Notebook", "100.64.0.2", Transport.ZEROTIER, Health(Quality.EXCELLENT, 12.0, 1.0, 0.0, 6)),
            ConnectionInfo("TV Sala", "192.168.56.60", Transport.LOCAL, Health(Quality.GOOD, 48.0, 3.0, 0.0, 6)),
        ]
    )
    content = texts(host.connected_devices_group)
    for expected in ("Notebook", "Excellent", "12 ms", "ZeroTier", "TV Sala", "Good", "48 ms", "Local network"):
        assert expected in content
    assert host.connected_devices_group is not host.paired_devices_overview_group
    host.perf_monitor.update_connections([])
    assert "No one is playing yet" in texts(host.connected_devices_group)
    host.perf_monitor.update_timer_active = False


# ── Play over the internet: the connection in use ────────────────────────


class LinkService(HubService):
    def __init__(self, statuses, samples):
        super().__init__(statuses)
        self.samples = list(samples)

    def link_sample(self, status):
        return self.samples.pop(0) if len(self.samples) > 1 else self.samples[0]


def test_internet_page_shows_the_connection_in_use_with_measured_stability(ui, monkeypatch):
    from test_private_network_ui import hub

    monkeypatch.setattr("big_remote_play.ui.remote_connection.read_interface_bytes", lambda prefixes: (1_000_000, 500_000))
    page = hub(ui, LinkService([TS_CONNECTED], [LinkSample(18.0, "direct", "notebook")]))
    assert wait_for(lambda: page.link_card.get_visible())
    assert page.link_title.get_label() == "Tailscale"
    for _ in range(3):
        page._sample_link()
        wait_for(lambda: not page._link_busy)
    values = page.link_values
    assert values["stability"].get_label() == "Stable · 18 ms"
    assert values["path"].get_label() == "Direct connection"
    assert "Mbps" in values["traffic"].get_label()
    assert page._link_timer  # measuring while on screen
    ui.navigate_to("host")
    assert wait_for(lambda: not page._link_timer)  # and never after leaving


def test_internet_page_says_when_there_is_nobody_to_measure(ui):
    from test_private_network_ui import hub

    page = hub(ui, LinkService([ZT_CONNECTED], [LinkSample(path="none")]))
    assert wait_for(lambda: page.link_card.get_visible())
    assert wait_for(lambda: page.link_values["stability"].get_label() == "No other device online to measure")


def test_internet_page_hides_the_card_when_not_connected(ui):
    from test_private_network_ui import hub

    off = ProviderStatus(ProviderId.TAILSCALE, ConnectionState.DISCONNECTED, recovery=Recovery.RECONNECT)
    page = hub(ui, LinkService([off], [LinkSample(path="none")]))
    assert wait_for(lambda: page.plan.kind.value == "turned_off")
    assert not page.link_card.get_visible() and not page._link_timer


# ── ZeroTier: being on one network never hides joining another ───────────


def test_zerotier_join_form_stays_when_already_on_a_network(ui, monkeypatch):
    import big_remote_play.ui.private_network_view as pnv

    page = pnv.ConnectPage("zerotier", ui)
    page._apply_connected(True)
    assert page._connect_form.get_visible()
    assert page._zt_panel.entry.get_visible()
    assert "join another one" in page._c_description.get_label()
