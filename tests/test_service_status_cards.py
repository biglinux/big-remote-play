"""Service cards: Sunshine/Moonlight and every private-network method.

The presentation functions are pure; the GTK tests drive the real sidebar
through the window's own refresh path with a fake private-network service.
No VPN client, Sunshine or Moonlight runs.
"""

from __future__ import annotations

from pathlib import Path
import re
import time
from types import SimpleNamespace

import pytest
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from big_remote_play.private_network import service as service_module  # noqa: E402
from big_remote_play.private_network.models import ConnectionState, OverlayNetwork, PeerDevice, ProviderId, ProviderStatus, Recovery  # noqa: E402
from big_remote_play.ui.main_window import MainWindow  # noqa: E402
from big_remote_play.ui.network_common import state_label  # noqa: E402
from big_remote_play.ui.service_status_card import checking_presentation, provider_presentation, streaming_presentation  # noqa: E402

from test_private_network_ui import HubService  # noqa: E402
from test_ui_task_flows import drain, ui as _ui_fixture  # noqa: E402

ui = _ui_fixture

STYLESHEETS = ("usr/share/big-remote-play/ui/style.css", "usr/share/big-remote-play/ui/gamer.css")
NETWORK = ("tailscale", "zerotier", "headscale")


def status(provider: ProviderId, state: ConnectionState, **kwargs) -> ProviderStatus:
    return ProviderStatus(provider, state, installed=kwargs.pop("installed", state is not ConnectionState.UNAVAILABLE), **kwargs)


def off(provider: ProviderId) -> ProviderStatus:
    return status(provider, ConnectionState.DISCONNECTED)


TAILSCALE_ON = status(
    ProviderId.TAILSCALE,
    ConnectionState.CONNECTED,
    # A personal tailnet is named after the account; neither may be shown.
    network_name="player@example.com",
    account="player@example.com",
    self_device=PeerDevice("game-pc", ("100.64.0.1",), is_self=True),
    peers=(PeerDevice("notebook", ("100.64.0.2",), online=True), PeerDevice("tv", ("100.64.0.3",), online=False)),
)
ZEROTIER_ON = status(
    ProviderId.ZEROTIER,
    ConnectionState.CONNECTED,
    network_name="games",
    self_device=PeerDevice("game-pc", ("10.147.17.5",), is_self=True),
    networks=(OverlayNetwork("8056c2e21c000001", "games", ConnectionState.CONNECTED, ("10.147.17.5",)),),
)
HEADSCALE_ON = status(ProviderId.HEADSCALE, ConnectionState.CONNECTED, self_device=PeerDevice("game-pc", ("100.64.0.9",), is_self=True))


# ── presentation (pure) ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "state,tone",
    [
        (ConnectionState.DISCONNECTED, "inactive"),
        (ConnectionState.NEEDS_AUTHENTICATION, "attention"),
        (ConnectionState.CONNECTING, "starting"),
        (ConnectionState.NEEDS_AUTHORIZATION, "attention"),
        (ConnectionState.CONNECTED, "active"),
        (ConnectionState.ERROR, "error"),
    ],
)
def test_provider_state_uses_the_network_pages_words_and_one_tone(state, tone):
    presentation = provider_presentation(status(ProviderId.TAILSCALE, state))
    assert presentation.tone == tone
    assert presentation.text == state_label(state)


@pytest.mark.parametrize(
    "provider_status,tone",
    [
        (ProviderStatus(ProviderId.ZEROTIER, ConnectionState.UNAVAILABLE, installed=False, recovery=Recovery.INSTALL), "missing"),
        (ProviderStatus(ProviderId.ZEROTIER, ConnectionState.UNAVAILABLE, recovery=Recovery.START_SERVICE), "inactive"),
        (ProviderStatus(ProviderId.ZEROTIER, ConnectionState.ERROR, recovery=Recovery.GRANT_ACCESS), "attention"),
    ],
)
def test_recovery_states_are_named_by_the_step_they_need(provider_status, tone):
    presentation = provider_presentation(provider_status)
    assert presentation.tone == tone
    # Not the generic "Needs attention" / "Not available" of the raw state.
    assert presentation.text != state_label(provider_status.state)


def test_connected_details_are_real_and_never_private():
    tailscale = provider_presentation(TAILSCALE_ON)
    assert "1" in tailscale.detail  # one peer online, the other is offline
    zerotier = provider_presentation(ZEROTIER_ON)
    assert zerotier.detail == "games"
    for presentation in (tailscale, zerotier, provider_presentation(HEADSCALE_ON)):
        shown = presentation.text + presentation.detail
        assert "@" not in shown
        assert re.search(r"\d+\.\d+\.\d+\.\d+", shown) is None
    # Nothing is claimed for a method that is not connected.
    assert provider_presentation(off(ProviderId.TAILSCALE)).detail == ""


@pytest.mark.parametrize(
    "service_id,installed,running,tone",
    [
        ("sunshine", None, None, "checking"),
        ("sunshine", False, False, "missing"),
        ("sunshine", True, False, "inactive"),
        ("sunshine", True, True, "active"),
        ("moonlight", False, False, "missing"),
        ("moonlight", True, False, "ready"),
        ("moonlight", True, True, "active"),
    ],
)
def test_streaming_state_comes_only_from_the_probes(service_id, installed, running, tone):
    assert streaming_presentation(service_id, installed, running).tone == tone


def test_ready_moonlight_and_stopped_sunshine_are_worded_differently():
    assert streaming_presentation("moonlight", True, False).text != streaming_presentation("sunshine", True, False).text


# ── which cards a task shows ────────────────────────────────────────────


@pytest.mark.parametrize("page,streaming", [("host", "sunshine"), ("guest", "moonlight")])
@pytest.mark.parametrize("vpn_choice", [None, "tailscale", "zerotier"])
def test_a_task_shows_its_streaming_card_and_every_network_method(page, streaming, vpn_choice):
    window = SimpleNamespace(current_page=page, _vpn_choice=vpn_choice, _network_statuses={}, _NETWORK_SERVICES=MainWindow._NETWORK_SERVICES)
    assert MainWindow._relevant_service_ids(window) == [streaming, *NETWORK]


def test_home_shows_two_indicators_not_component_cards():
    window = SimpleNamespace(current_page="welcome", _NETWORK_SERVICES=MainWindow._NETWORK_SERVICES)
    assert MainWindow._relevant_service_ids(window) == ["summary-streaming", "summary-network"]


@pytest.mark.parametrize("role,streaming", [("host", "sunshine"), ("guest", "moonlight")])
def test_connect_your_devices_shows_the_same_cards_as_share_and_connect(role, streaming):
    window = SimpleNamespace(current_page="vpn_selector", _NETWORK_SERVICES=MainWindow._NETWORK_SERVICES, _summary_role=lambda: role)
    assert MainWindow._relevant_service_ids(window) == [streaming, *NETWORK]


@pytest.mark.parametrize("page", ["host", "guest"])
def test_vpn_card_opens_the_methods_own_dialog_over_the_task(page):
    """Like Sunshine's and Moonlight's cards: a dialog, and the task stays on screen."""
    calls = []
    window = SimpleNamespace(current_page=page, open_provider=lambda provider, **kwargs: calls.append((provider, kwargs)))
    MainWindow.on_service_clicked(window, "headscale")
    assert calls == [("headscale", {})]


# ── the real sidebar ────────────────────────────────────────────────────


@pytest.fixture
def live(ui, monkeypatch):
    """The window with its periodic refresh marked alive, as after startup."""
    monkeypatch.setattr(ui, "_status_timer_id", GLib.timeout_add_seconds(3600, lambda: True))
    return ui


def settle(ui) -> None:
    """Let the read in flight (navigation starts one) deliver its result."""
    deadline = GLib.get_monotonic_time() + 3_000_000
    while ui._network_polling and GLib.get_monotonic_time() < deadline:
        GLib.MainContext.default().iteration(False)
    drain()


def refresh_network(ui, *statuses) -> None:
    settle(ui)
    service_module.set_default_factory(lambda: HubService(list(statuses)))
    ui._refresh_private_network_status()
    settle(ui)


def visible_cards(ui) -> list[str]:
    return [service_id for service_id, row in ui._status_rows.items() if row.get_visible()]


def card(ui, service_id):
    return ui._status_rows[service_id]


@pytest.mark.parametrize("page,streaming", [("host", "sunshine"), ("guest", "moonlight")])
@pytest.mark.parametrize("active", [None, ProviderId.TAILSCALE, ProviderId.ZEROTIER, ProviderId.HEADSCALE])
def test_task_cards_show_the_real_state_of_every_method(live, page, streaming, active):
    connected = {ProviderId.TAILSCALE: TAILSCALE_ON, ProviderId.ZEROTIER: ZEROTIER_ON, ProviderId.HEADSCALE: HEADSCALE_ON}
    live.navigate_to(page)
    refresh_network(live, *(connected[p] if p is active else off(p) for p in ProviderId))
    assert visible_cards(live) == [streaming, *NETWORK]
    for provider in ProviderId:
        row = card(live, provider.value)
        expected = "active" if provider is active else "inactive"
        assert row.tone == expected
        assert row.has_css_class(expected)
        assert row.get_subtitle().split("\n")[0] == state_label(ConnectionState.CONNECTED if provider is active else ConnectionState.DISCONNECTED)


def test_more_than_one_connected_method_is_shown_as_it_is(live):
    live.navigate_to("guest")
    refresh_network(live, TAILSCALE_ON, ZEROTIER_ON, off(ProviderId.HEADSCALE))
    assert [card(live, p).tone for p in NETWORK] == ["active", "active", "inactive"]


def test_a_card_changes_in_place_while_the_screen_stays_open(live):
    live.navigate_to("host")
    refresh_network(live, off(ProviderId.TAILSCALE), off(ProviderId.ZEROTIER), off(ProviderId.HEADSCALE))
    row = card(live, "tailscale")
    before = visible_cards(live)
    seen = []
    for state in (ConnectionState.CONNECTING, ConnectionState.CONNECTED, ConnectionState.DISCONNECTED):
        refresh_network(live, TAILSCALE_ON if state is ConnectionState.CONNECTED else status(ProviderId.TAILSCALE, state), off(ProviderId.ZEROTIER), off(ProviderId.HEADSCALE))
        assert card(live, "tailscale") is row  # the same widget, not a new row
        assert visible_cards(live) == before  # nothing appears or disappears
        seen.append(row.tone)
    assert seen == ["starting", "active", "inactive"]
    assert not row.has_css_class("active")


@pytest.mark.parametrize(
    "provider_status,tone",
    [
        (ProviderStatus(ProviderId.TAILSCALE, ConnectionState.UNAVAILABLE, installed=False), "missing"),
        (ProviderStatus(ProviderId.TAILSCALE, ConnectionState.NEEDS_AUTHENTICATION), "attention"),
        (ProviderStatus(ProviderId.TAILSCALE, ConnectionState.ERROR, technical_detail="invalid status JSON"), "error"),
    ],
)
def test_problem_states_keep_their_card_with_words(live, provider_status, tone):
    live.navigate_to("guest")
    refresh_network(live, provider_status, off(ProviderId.ZEROTIER), off(ProviderId.HEADSCALE))
    row = card(live, "tailscale")
    assert row.get_visible()
    assert row.tone == tone
    assert row.get_subtitle()


def test_a_failed_read_says_so_instead_of_checking_forever(live, monkeypatch):
    live.navigate_to("host")
    checking = checking_presentation().text

    class Broken(HubService):
        def overview(self):
            raise RuntimeError("tailscaled did not answer")

    settle(live)
    live._network_statuses = {}
    for provider in NETWORK:
        card(live, provider).set_presentation(checking_presentation())
    service_module.set_default_factory(lambda: Broken())
    live._refresh_private_network_status()
    settle(live)
    for provider in NETWORK:
        row = card(live, provider)
        assert row.get_visible()
        assert row.get_subtitle() != checking
        assert row.tone == "inactive"


def test_statuses_arriving_after_close_are_dropped(live):
    live.navigate_to("guest")
    settle(live)
    for provider in NETWORK:
        card(live, provider).set_presentation(checking_presentation())
    live._status_timer_id = None  # what shutdown does
    refresh_network(live, TAILSCALE_ON, off(ProviderId.ZEROTIER), off(ProviderId.HEADSCALE))
    assert card(live, "tailscale").tone == "checking"


def test_switching_task_swaps_only_the_streaming_card(live):
    live.navigate_to("host")
    refresh_network(live, TAILSCALE_ON, off(ProviderId.ZEROTIER), off(ProviderId.HEADSCALE))
    live.update_server_status(True, False, True)
    assert card(live, "sunshine").tone == "active"
    live.navigate_to("guest")
    drain()
    assert visible_cards(live) == ["moonlight", *NETWORK]
    assert card(live, "moonlight").tone == "ready"
    assert card(live, "tailscale").tone == "active"  # kept while the next read runs


def test_cards_are_grouped_under_two_headings(live):
    live.navigate_to("guest")
    drain()
    headers = {}
    for service_id, row in live._status_rows.items():
        header = row.get_header()
        headers[service_id] = header.get_label() if isinstance(header, Gtk.Label) else None
    assert headers["moonlight"] and headers["tailscale"]
    assert headers["moonlight"] != headers["tailscale"]
    assert headers["zerotier"] is None and headers["headscale"] is None


def test_home_keeps_only_the_streaming_and_secure_connection_indicators(live):
    live.navigate_to("guest")
    live.navigate_to("welcome")
    assert visible_cards(live) == ["summary-streaming", "summary-network"]


def test_indicators_say_the_streaming_state_and_the_connection_in_use(live):
    live._home_role = "host"
    live.update_dependency_ui(True, True, True, True)
    live.update_server_status(True, False, False)
    refresh_network(live, status(ProviderId.TAILSCALE, ConnectionState.CONNECTED, peers=()), off(ProviderId.ZEROTIER), off(ProviderId.HEADSCALE))
    live.navigate_to("welcome")
    settle(live)
    streaming = card(live, "summary-streaming").presentation
    network = card(live, "summary-network").presentation
    assert (streaming.text, streaming.tone) == ("Sunshine · Running", "active")
    assert (network.text, network.tone) == ("Tailscale · Connected", "active")


def test_secure_connection_indicator_names_the_next_step_when_nothing_is_connected(live):
    refresh_network(live, off(ProviderId.TAILSCALE), off(ProviderId.ZEROTIER), off(ProviderId.HEADSCALE))
    live.navigate_to("welcome")
    settle(live)
    network = card(live, "summary-network").presentation
    assert network.tone != "active" and "Connected" not in network.text


def test_activating_an_indicator_opens_its_task(live):
    live._home_role = "guest"
    live.update_dependency_ui(True, True, True, True)
    live.navigate_to("welcome")
    card(live, "summary-streaming").emit("activated")
    drain()
    assert live.current_page == "guest"
    live.navigate_to("welcome")
    card(live, "summary-network").emit("activated")
    drain()
    assert live.current_page == "vpn_selector"


def test_connection_method_uses_the_same_state_words(ui):
    from big_remote_play.ui.remote_connection import RemoteConnectionPage

    signed_out = ProviderStatus(ProviderId.TAILSCALE, ConnectionState.NEEDS_AUTHENTICATION)
    statuses = [signed_out, ZEROTIER_ON, off(ProviderId.HEADSCALE)]
    page = RemoteConnectionPage(ui, service_factory=lambda: HubService(statuses))
    page.refresh()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not all(card.state is not None for card in page.cards.values()):
        drain()
        time.sleep(0.01)
    from big_remote_play.private_network.device_list import simple_state

    for provider_status in statuses:
        # Each method keeps its own state; several can be connected at once.
        assert page.cards[provider_status.provider].state == simple_state(provider_status)


# ── stylesheet contracts ────────────────────────────────────────────────


def _rules(css: str):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        yield [part.strip() for part in selectors.split(",")], body


@pytest.mark.parametrize("path", STYLESHEETS)
def test_stylesheet_parses_without_errors(path):
    errors = []
    provider = Gtk.CssProvider()
    provider.connect("parsing-error", lambda _provider, _section, error: errors.append(error.message))
    provider.load_from_path(path)
    assert errors == []


@pytest.mark.parametrize("path", STYLESHEETS)
def test_choice_and_service_focus_ring_is_keyboard_only(path):
    """A card focused for you (after a click or a page change) keeps its thin
    border; only keyboard focus draws the ring."""
    for selectors, body in _rules(Path(path).read_text()):
        for selector in selectors:
            if (".role-card" in selector or "brp-service-card" in selector) and ":focus" in selector:
                assert ":focus-visible" in selector, selector
                assert "outline: 3px" not in body
            if (".role-card" in selector or "brp-service-card" in selector) and "high-contrast" not in selector:
                assert "border: 2px" not in body and "border-width: 2px" not in body, selector


def test_state_motion_is_short_one_shot_and_can_be_reduced():
    css = Path(STYLESHEETS[0]).read_text()
    for path in STYLESHEETS:
        assert "infinite" not in Path(path).read_text()
    for duration in re.findall(r"(?:transition|animation):[^;]*?(\d+)ms", css):
        assert int(duration) <= 420
    reduced = css.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert "transition: none" in reduced and "animation: none" in reduced
    assert ".role-card" in reduced and "brp-service-card" in reduced


def test_the_connect_your_devices_sidebar_lists_every_method_with_its_own_state(live):
    live._home_role = "guest"
    live.update_dependency_ui(True, True, True, True)
    live.navigate_to("vpn_selector")
    refresh_network(live, off(ProviderId.TAILSCALE), status(ProviderId.ZEROTIER, ConnectionState.CONNECTED, peers=()), status(ProviderId.HEADSCALE, ConnectionState.CONNECTED, peers=()))
    visible = [service_id for service_id, row in live._status_rows.items() if row.get_visible()]
    assert visible == ["moonlight", "tailscale", "zerotier", "headscale"]
    assert card(live, "headscale").presentation.text == "Connected"
    assert card(live, "tailscale").presentation.text != "Connected"
