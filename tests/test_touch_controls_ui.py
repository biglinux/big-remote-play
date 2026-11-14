"""Share → Preferences → Touch controls: settings, checks and the controller's lifetime."""

from __future__ import annotations

import time

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib  # noqa: E402

from big_remote_play.host import touch_control as backend  # noqa: E402
from big_remote_play.ui.touch_controls import TouchControls, check_text, mode_description, mode_label  # noqa: E402

from test_ui_task_flows import ui as _ui_fixture  # noqa: E402

ui = _ui_fixture


def wait(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class FakeKeyboard:
    """Stands in for big-keyboard: records what Big Remote Play asks of it."""

    log: list[tuple] = []

    def __init__(self, running=True, supports=True):
        self._running = running
        self._supports = supports

    def running(self):
        return self._running

    def start(self):
        FakeKeyboard.log.append(("start",))
        self._running = True
        return True

    def status(self):
        return backend.ControllerStatus(installed=True, running=self._running, supports_remote_touch=self._supports, visible=False)

    def configure(self, *, remote_touch, output):
        FakeKeyboard.log.append(("configure", remote_touch, output))

    def show(self, visible):
        FakeKeyboard.log.append(("show", visible))

    def edit(self):
        FakeKeyboard.log.append(("edit",))

    def release(self):
        FakeKeyboard.log.append(("release",))


@pytest.fixture
def touch(ui):
    FakeKeyboard.log = []
    saved: list[bool] = []
    context = {"sharing": True, "game_window": False, "output": "DP-1", "sunshine_config": {}}
    widget = TouchControls(on_changed=lambda: saved.append(True), context=lambda: context, keyboard_factory=FakeKeyboard)
    widget._saved = saved  # type: ignore[attr-defined]
    widget._ctx = context  # type: ignore[attr-defined]
    yield widget
    widget.close()


def test_defaults_keep_the_previous_behaviour(touch) -> None:
    touch.load({})
    assert touch.values() == {"touch_mode": "automatic", "touch_open_on_connect": True}
    assert not touch.open_row.get_visible()  # only for controller modes
    assert not touch._saved  # loading is not a change


def test_settings_round_trip_and_mode_explains_itself(touch) -> None:
    touch.load({"touch_mode": "gamepad", "touch_open_on_connect": False})
    assert touch.values() == {"touch_mode": "gamepad", "touch_open_on_connect": False}
    assert touch.mode_row.get_subtitle() == mode_description("gamepad")
    assert touch.open_row.get_visible()
    touch.mode_row.set_selected(backend.MODES.index("mouse"))
    assert touch._saved and touch.values()["touch_mode"] == "mouse"
    assert not touch.open_row.get_visible()


def test_every_mode_and_check_has_words(touch) -> None:
    for mode in backend.MODES:
        assert mode_label(mode) and mode_description(mode)
    keys = ["sunshine_touch", "touchscreen", "desktop_touch", "controller", "controller_version", "uinput", "game_window", "remote_state"]
    for key in keys:
        for outcome in (backend.OK, backend.WARN, backend.FAIL, backend.INFO):
            title, words = check_text(backend.Check(key, outcome, "x"))
            assert title and words and title != key


def test_first_device_opens_the_controller_on_the_shared_screen_and_last_one_releases(touch) -> None:
    touch.load({"touch_mode": "gamepad"})
    touch.on_connections(1)
    assert wait(lambda: ("show", True) in FakeKeyboard.log)
    assert ("configure", "exclusive", "DP-1") in FakeKeyboard.log
    assert FakeKeyboard.log.index(("configure", "exclusive", "DP-1")) < FakeKeyboard.log.index(("show", True))
    touch.on_connections(2)
    touch.on_connections(1)  # one of two left: its fingers are let go
    assert wait(lambda: ("release",) in FakeKeyboard.log)
    FakeKeyboard.log.clear()
    touch.on_connections(0)
    assert wait(lambda: ("show", False) in FakeKeyboard.log)
    assert FakeKeyboard.log.index(("release",)) < FakeKeyboard.log.index(("show", False))


def test_mouse_mode_never_opens_the_controller(touch) -> None:
    touch.load({"touch_mode": "mouse"})
    touch.on_connections(1)
    wait(lambda: False, timeout=0.3)
    assert not any(entry[0] == "show" for entry in FakeKeyboard.log)


def test_open_on_connect_can_be_turned_off(touch) -> None:
    touch.load({"touch_mode": "hybrid", "touch_open_on_connect": False})
    touch.on_connections(1)
    wait(lambda: False, timeout=0.3)
    assert ("show", True) not in FakeKeyboard.log


def test_buttons_drive_the_controller(touch) -> None:
    touch.load({"touch_mode": "hybrid"})
    touch.buttons["edit"].emit("clicked")
    assert wait(lambda: ("edit",) in FakeKeyboard.log)
    assert ("configure", "shared", "DP-1") in FakeKeyboard.log
    touch.buttons["release"].emit("clicked")
    assert wait(lambda: ("release",) in FakeKeyboard.log)


def test_checks_are_shown_in_words_with_a_summary(touch, monkeypatch) -> None:
    monkeypatch.setattr(backend, "sunshine_touchscreens", lambda *a, **k: [])
    monkeypatch.setattr(backend, "uinput_writable", lambda *a, **k: True)
    touch.load({"touch_mode": "gamepad"})
    touch.refresh()
    assert wait(lambda: touch.summary_row.get_title() in ("Ready to play", "Not ready yet") and touch._check_rows)
    titles = [row.get_title() for row in touch._check_rows]
    assert "Touch from the phone (Sunshine)" in titles and "Virtual controller" in titles
    assert touch.summary_row.get_title() == "Ready to play"


def test_a_missing_controller_is_offered_through_the_shared_installer(ui, monkeypatch) -> None:
    from big_remote_play.utils import dependencies

    class Missing(FakeKeyboard):
        def status(self):
            return backend.ControllerStatus(installed=False)

    monkeypatch.setattr(backend, "sunshine_touchscreens", lambda *a, **k: [])
    widget = TouchControls(on_changed=lambda: None, context=lambda: {"sunshine_config": {}}, keyboard_factory=Missing)
    widget.load({"touch_mode": "gamepad"})
    widget.refresh()
    assert wait(lambda: widget.install_button.get_visible())
    assert not widget.buttons["open"].get_sensitive()
    component = dependencies.COMPONENTS["big_keyboard"]
    assert component.package == "big-keyboard" and component.executables == ("big-keyboard",)
    widget.close()


def test_every_check_icon_exists_in_the_theme(ui) -> None:
    """A missing icon draws a broken image next to each check."""
    from gi.repository import Gdk

    if Gdk.Display.get_default() is None:
        pytest.skip("GTK display required")
    from big_remote_play.ui import touch_controls
    from big_remote_play.utils.icons import _ensure_icon_search_paths

    theme = _ensure_icon_search_paths()
    assert theme is not None
    for name in {*touch_controls._ICONS.values(), "brp-emblem-ok-symbolic", "dialog-warning-symbolic"}:
        assert theme.has_icon(name), name
