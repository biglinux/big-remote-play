"""A Sunshine start during a Game Window share never sees the desktop.

Restart, the password reset and the service dialog call ``start()`` with no
screen. With KWin capture configured for Game Window, Sunshine would then come
back on the desktop's compositor and stream the private desktop under the
Game Window label.
"""

from __future__ import annotations

from big_remote_play.host import sunshine_manager, window_capture
from big_remote_play.host.sunshine_manager import SunshineHost


class _Process:
    pid = 4242

    def poll(self):
        return None


def _host(tmp_path, monkeypatch, state):
    started: list[dict[str, str]] = []

    def popen(cmd, **kwargs):
        started.append(kwargs["env"])
        return _Process()

    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(sunshine_manager, "sunshine_executable", lambda: "/usr/bin/sunshine")
    monkeypatch.setattr(sunshine_manager.subprocess, "Popen", popen)
    monkeypatch.setattr(window_capture, "read_state", lambda path=None: state)
    monkeypatch.setattr(window_capture, "helper_running", lambda s=None: bool(state))
    host = SunshineHost(cdir=tmp_path)
    monkeypatch.setattr(host, "is_running", lambda: False)
    monkeypatch.setattr(host, "_wait_for_startup", lambda: None)
    return host, started


def test_a_restart_during_a_game_window_share_stays_on_the_private_screen(tmp_path, monkeypatch):
    state = {"state": "running", "pid": 1, "socket": "brp-game-7f3a"}
    host, started = _host(tmp_path, monkeypatch, state)
    ok, _ = host.start()
    assert ok
    assert [env["WAYLAND_DISPLAY"] for env in started] == ["brp-game-7f3a"]
    assert "WAYLAND_SOCKET" not in started[0]


def test_without_its_private_screen_the_server_is_not_started_at_all(tmp_path, monkeypatch):
    host, started = _host(tmp_path, monkeypatch, {"state": "running", "pid": 1, "socket": ""})
    ok, message = host.start()
    assert not ok and message
    assert started == []


def test_without_a_share_the_server_sees_the_desktop_as_before(tmp_path, monkeypatch):
    host, started = _host(tmp_path, monkeypatch, None)
    ok, _ = host.start()
    assert ok
    assert started[0]["WAYLAND_DISPLAY"] == "wayland-0"


def test_settings_are_not_pushed_to_the_running_server_during_a_game_window_share(monkeypatch):
    from big_remote_play.ui import sunshine_preferences

    monkeypatch.setattr(sunshine_manager, "game_window_screen", lambda: (True, "brp-game-7f3a"))
    assert sunshine_preferences.live_apply_blocked()
    monkeypatch.setattr(sunshine_manager, "game_window_screen", lambda: (False, None))
    assert sunshine_preferences.live_apply_blocked() is None
