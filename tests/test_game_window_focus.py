"""Game Window: the game is brought to the front again when a device starts playing.

Approving the device happens in Big Remote Play's window, which then holds the
focus: Sunshine would send the other person's keys there, and many games slow
down or mute themselves while they are not the active window.
"""

from __future__ import annotations

import time

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import GLib  # noqa: E402

from big_remote_play.host import window_capture  # noqa: E402
from big_remote_play.utils.connection_health import ConnectionInfo  # noqa: E402

from test_ui_task_flows import ui as _ui_fixture  # noqa: E402

ui = _ui_fixture


def wait(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@pytest.fixture
def activations(ui, monkeypatch):
    calls: list = []
    monkeypatch.setattr(window_capture, "activate_game", lambda spec: calls.append(spec) or True)
    host = ui.host_view
    host.is_hosting = True
    yield calls
    host.is_hosting = False
    host._game_window_session = None


def player(name="Living room"):
    return ConnectionInfo(name, "192.168.1.30", started_at=time.time())


def test_a_new_player_brings_the_game_to_the_front(ui, activations):
    host = ui.host_view
    host._game_window_session = {"name": "SuperTuxKart", "spec": "game-spec"}
    host._refocus_game_when_someone_joins([player()])
    assert wait(lambda: activations == ["game-spec"])
    host._refocus_game_when_someone_joins([player()])  # the same player: nothing more
    host._refocus_game_when_someone_joins([])  # leaving: nothing
    host._refocus_game_when_someone_joins([player(), player("Laptop")])  # a second player
    assert wait(lambda: activations == ["game-spec", "game-spec"])


def test_full_desktop_never_moves_the_focus(ui, activations):
    host = ui.host_view
    host._game_window_session = None
    host._refocus_game_when_someone_joins([player()])
    time.sleep(0.1)
    assert activations == []


def test_activation_uses_the_backend_of_the_shared_window(monkeypatch):
    from big_remote_play.host import game_windows, x11_windows

    calls = []
    monkeypatch.setattr(game_windows, "activate_kwin_window", lambda handle: calls.append(("kwin", handle)) or True)
    monkeypatch.setattr(x11_windows, "activate_window", lambda display, xid: calls.append(("x11", display, xid)) or False)
    kwin = type("S", (), {"backend": "kwin", "handle": "{uuid}", "display": ""})()
    x11 = type("S", (), {"backend": "x11", "handle": "0x2a", "display": ":0"})()
    assert window_capture.activate_game(kwin) is True
    assert window_capture.activate_game(x11) is False  # reported, not raised
    assert calls == [("kwin", "{uuid}"), ("x11", ":0", 42)]
