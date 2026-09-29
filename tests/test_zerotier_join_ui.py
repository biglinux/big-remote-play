"""The ZeroTier join panel in real GTK, driven by a scripted ZeroTier service.

The same panel is on the ZeroTier connection page and in the guided setup,
so these tests cover both. Nothing reaches zerotier-cli.
"""

from __future__ import annotations

import time

import pytest
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from big_remote_play.private_network.zerotier_join import JoinPhase, ZeroTierJoiner  # noqa: E402
from big_remote_play.ui import zerotier_join as panel_module  # noqa: E402
from big_remote_play.utils.vpn_accounts import CommandResult  # noqa: E402

from test_zerotier_join import NETWORK, NODE, FakeZeroTier  # noqa: E402


@pytest.fixture(autouse=True)
def display(monkeypatch, tmp_path):
    if Gdk.Display.get_default() is None:
        pytest.skip("Requires a GTK display")
    Adw.init()
    import big_remote_play.ui.private_network_view as pnv

    monkeypatch.setattr(pnv, "HISTORY_FILE", str(tmp_path / "history.json"))
    # The controller answers at once in tests; the page must still wait politely.
    monkeypatch.setattr("big_remote_play.private_network.zerotier_join.POLL_SECONDS", 0.01)


class Window:
    system_check = None

    def __init__(self):
        self.toasts = []

    def show_toast(self, text):
        self.toasts.append(text)


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def make_panel(service, **kwargs):
    joiner = ZeroTierJoiner(service, installed=service.installed, service_running=service.service_running)  # type: ignore[arg-type]
    panel = panel_module.ZeroTierJoinPanel(Window(), joiner_factory=lambda: joiner, **kwargs)
    window = Gtk.Window(child=panel)
    window.present()
    return panel, window


def phase(panel):
    return panel.snapshot.phase if panel.snapshot else None


def test_joining_shows_progress_then_connected(monkeypatch):
    service = FakeZeroTier(["REQUESTING_CONFIGURATION", "OK"])
    connected = []
    panel, window = make_panel(service, on_connected=connected.append)
    panel.entry.set_text("8056 C2E2 1C00 0001")
    panel.button.emit("clicked")
    assert wait_for(lambda: connected)
    assert phase(panel) is JoinPhase.CONNECTED
    assert panel.title.get_label() == "Connected"
    assert not panel.button.get_visible()  # nothing left to press here
    window.destroy()


def test_waiting_for_authorization_shows_the_node_id_and_never_says_failed():
    service = FakeZeroTier(["ACCESS_DENIED"])
    panel, window = make_panel(service)
    panel.join(NETWORK)
    assert wait_for(lambda: phase(panel) is JoinPhase.WAITING_AUTHORIZATION)
    assert panel.title.get_label() == "Waiting for authorization"
    assert "failed" not in panel.body.get_label().lower()
    assert panel.device_group.get_visible()
    subtitles = [row.get_subtitle() for row in panel._device_rows]
    assert NODE in subtitles
    assert panel.website.get_visible()
    assert panel.button_label.get_label() == "Check again"
    assert panel._timer  # it keeps checking by itself while on screen
    window.destroy()


def test_authorization_given_later_is_picked_up_without_joining_again(monkeypatch):
    monkeypatch.setattr(panel_module, "FAST_CHECK_SECONDS", 1)
    service = FakeZeroTier(["ACCESS_DENIED"])
    connected = []
    panel, window = make_panel(service, on_connected=connected.append)
    panel.join(NETWORK)
    assert wait_for(lambda: phase(panel) is JoinPhase.WAITING_AUTHORIZATION)
    service.timeline, service.reads = ["OK"], 0  # the owner approves it
    assert wait_for(lambda: connected, timeout=4)
    assert [call for call in service.calls if call[0] == "join"] == [("join", NETWORK)]
    assert not panel._timer  # checking stopped once connected
    window.destroy()


def test_checking_stops_when_the_panel_leaves_the_screen():
    panel, window = make_panel(FakeZeroTier(["ACCESS_DENIED"]))
    panel.join(NETWORK)
    assert wait_for(lambda: phase(panel) is JoinPhase.WAITING_AUTHORIZATION and panel._timer)
    window.set_visible(False)
    assert wait_for(lambda: not panel._timer)
    window.destroy()


@pytest.mark.parametrize(
    "service, expected, title",
    [
        (FakeZeroTier(["NOT_FOUND"]), JoinPhase.NOT_FOUND, "Network code not found"),
        (FakeZeroTier(join_result=CommandResult(1, "500 join error")), JoinPhase.FAILED, "We could not join this network"),
    ],
)
def test_problems_are_named_and_offer_to_try_again(service, expected, title):
    panel, window = make_panel(service)
    panel.join(NETWORK)
    assert wait_for(lambda: phase(panel) is expected)
    assert panel.title.get_label() == title
    assert panel.button_label.get_label() == "Try again"
    assert panel.details_list.get_visible()
    assert f"Network: {NETWORK}" in panel.details_label.get_label()
    window.destroy()


def test_a_stopped_service_is_started_as_part_of_joining():
    service = FakeZeroTier(["OK"], running=False)
    panel, window = make_panel(service)
    panel.join(NETWORK)
    assert wait_for(lambda: phase(panel) is JoinPhase.CONNECTED)
    assert ("start", "zerotier-one") in service.calls
    window.destroy()


def test_an_incomplete_code_is_said_at_once_and_runs_nothing():
    service = FakeZeroTier()
    panel, window = make_panel(service)
    panel.entry.set_text("8056c2e2")
    panel.button.emit("clicked")
    assert phase(panel) is JoinPhase.INVALID_ID  # synchronously
    assert panel.entry.has_css_class("error")
    assert service.calls == []
    window.destroy()


def test_every_state_has_words_and_one_action():
    for value in JoinPhase:
        snapshot = panel_module.JoinSnapshot(value, network_id=NETWORK)
        icon, _tone, title, body = panel_module.join_words(snapshot)
        assert icon and title and body
        assert panel_module.action_for(value)
