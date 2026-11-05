"""Connect → Image settings → Open on: the screen of this computer.

Verified on a real three-monitor X11 desktop: Moonlight logged "Qt UI screen
is at (6880,0) ... SDL found matching display 1" and the borderless stream
filled the 2560 px screen chosen with Qt's -qwindowgeometry.
"""

from __future__ import annotations

from types import SimpleNamespace

from big_remote_play.guest import moonlight_client
from big_remote_play.guest.local_screens import LocalScreen, find_screen, local_screens, placement_supported, primary_connector


class _Monitor:
    def __init__(self, connector, x, width, height, model="LG"):
        self.connector, self.area, self.model = connector, SimpleNamespace(x=x, y=0, width=width, height=height), model

    def get_connector(self):
        return self.connector

    def get_geometry(self):
        return self.area

    def get_scale_factor(self):
        return 1

    def get_manufacturer(self):
        return "GSM"

    def get_model(self):
        return self.model


class _Display:
    def __init__(self, monitors):
        self.items = monitors

    def get_monitors(self):
        return SimpleNamespace(get_n_items=lambda: len(self.items), get_item=lambda index: self.items[index])


DISPLAY = _Display([_Monitor("HDMI-A-0", 0, 3440, 1440), _Monitor("DisplayPort-0", 3440, 3440, 1440), _Monitor("DisplayPort-1", 6880, 2560, 1080), _Monitor("bad;name", 9440, 800, 600)])


def test_screens_come_with_position_size_and_the_main_one():
    screens = local_screens(DISPLAY, primary="DisplayPort-0")
    assert [(s.connector, s.x, s.width, s.primary) for s in screens] == [("HDMI-A-0", 0, 3440, False), ("DisplayPort-0", 3440, 3440, True), ("DisplayPort-1", 6880, 2560, False)]


def test_the_window_is_placed_inside_the_chosen_screen():
    screen = find_screen(local_screens(DISPLAY, primary=""), "DisplayPort-1")
    assert screen is not None and screen.geometry() == "1280x720+7520+180"
    assert find_screen([], "DisplayPort-1") is None


def test_only_x11_sessions_place_the_window():
    assert placement_supported({"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"})
    assert not placement_supported({"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"})
    assert not placement_supported({"XDG_SESSION_TYPE": "tty"})


def test_the_main_screen_is_read_from_xrandr():
    out = "Monitors: 2\n 0: +*DisplayPort-0 3440/800x1440/334+3440+0  DisplayPort-0\n 1: +HDMI-A-0 3440/800x1440/334+0+0  HDMI-A-0\n"
    assert primary_connector(run=lambda *a, **k: SimpleNamespace(stdout=out)) == "DisplayPort-0"


def _commands(monkeypatch, **options):
    commands = []

    class Process:
        stdout = iter(())

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(moonlight_client.subprocess, "Popen", lambda argv, **k: commands.append(argv) or Process())
    client = moonlight_client.MoonlightClient()
    client.moonlight_cmd = "moonlight"
    client.connect("192.0.2.10", **options)
    return commands[0]


def test_moonlight_opens_on_the_chosen_screen(monkeypatch):
    argv = _commands(monkeypatch, window_geometry="1280x720+7520+180", display_mode="fullscreen")
    assert argv[:4] == ["moonlight", "-qwindowgeometry", "1280x720+7520+180", "stream"]
    assert argv[argv.index("--display-mode") + 1] == "fullscreen"


def test_automatic_or_malformed_placement_adds_nothing(monkeypatch):
    assert _commands(monkeypatch)[:2] == ["moonlight", "stream"]
    assert "-qwindowgeometry" not in _commands(monkeypatch, window_geometry="--evil")


# ── the row in Connect ──────────────────────────────────────────────────
from test_ui_task_flows import ui as _ui_fixture  # noqa: E402

ui = _ui_fixture


def _screens():
    return [LocalScreen("HDMI-A-0", "", 0, 0, 3440, 1440), LocalScreen("DisplayPort-1", "", 6880, 0, 2560, 1080, primary=True)]


def test_the_chosen_screen_is_saved_and_used_for_the_next_connection(ui, monkeypatch):
    from big_remote_play.guest import local_screens as module

    monkeypatch.setattr(module, "placement_supported", lambda environ=None: True)
    monkeypatch.setattr(module, "local_screens", lambda display=None, primary=None: _screens())
    guest = ui.guest_view
    guest.refresh_local_screens()
    assert guest.local_screen_row.get_sensitive()
    guest.local_screen_row.set_selected(2)
    assert guest.config.get("guest")["local_screen"] == "DisplayPort-1"
    assert guest.local_window_geometry() == "1280x720+7520+180"
    # The screen is unplugged: Automatic now, the choice kept for later.
    monkeypatch.setattr(module, "local_screens", lambda display=None, primary=None: _screens()[:1])
    guest.refresh_local_screens()
    assert guest.local_screen_row.get_selected() == 0 and guest.local_window_geometry() == ""
    assert guest.config.get("guest")["local_screen"] == "DisplayPort-1"


def test_wayland_explains_instead_of_offering_a_choice(ui, monkeypatch):
    from big_remote_play.guest import local_screens as module

    monkeypatch.setattr(module, "placement_supported", lambda environ=None: False)
    guest = ui.guest_view
    guest.refresh_local_screens()
    assert not guest.local_screen_row.get_sensitive() and guest.local_window_geometry() == ""
