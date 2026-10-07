"""Share → Preferences → Host input priority, and its row in Connected now."""

from __future__ import annotations

import time

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib  # noqa: E402

from big_remote_play.host import input_priority as backend  # noqa: E402
from big_remote_play.ui.input_priority import HostInputPriority  # noqa: E402

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


class FakeProcess:
    instances: list["FakeProcess"] = []

    def __init__(self, delay, on_event):
        self.delay = delay
        self.on_event = on_event
        self.started = self.stopped = False
        self.delays: list[int] = []
        FakeProcess.instances.append(self)

    def start(self):
        self.started = True
        return True

    def set_delay(self, delay):
        self.delays.append(delay)

    def stop(self):
        self.stopped = True


@pytest.fixture
def controls():
    FakeProcess.instances.clear()
    saved = []
    widget = HostInputPriority(on_changed=lambda: saved.append(True), process_factory=FakeProcess)
    widget._saved = saved  # type: ignore[attr-defined]
    yield widget
    widget.close()


def test_off_by_default_so_an_update_changes_nothing(controls) -> None:
    controls.load({})
    assert controls.values() == {"input_priority": False, "input_priority_delay": 5}
    assert not controls.delay_row.get_visible()
    controls.sync(hosting=True, source="desktop")
    assert not FakeProcess.instances and not controls.status_box.get_visible()


def test_saved_values_come_back_and_odd_delays_snap_to_a_choice(controls) -> None:
    controls.load({"input_priority": True, "input_priority_delay": 12})
    assert controls.values() == {"input_priority": True, "input_priority_delay": 10}
    assert controls.delay_row.get_visible()
    controls.load({"input_priority": "yes", "input_priority_delay": 999})
    assert controls.values() == {"input_priority": False, "input_priority_delay": 30}
    assert not controls._saved  # loading is not a change to save


def test_switching_on_while_sharing_starts_at_once_and_off_stops(controls) -> None:
    controls.load({})
    controls.sync(hosting=True, source="desktop")
    controls.switch_row.set_active(True)
    assert controls._saved and len(FakeProcess.instances) == 1
    process = FakeProcess.instances[0]
    assert process.started and process.delay == 5
    controls.delay_row.set_selected(backend.DELAY_CHOICES.index(10))
    assert process.delays == [10]
    controls.switch_row.set_active(False)
    assert process.stopped and not controls.status_box.get_visible()


def test_sharing_starting_and_stopping_drives_the_helper(controls) -> None:
    controls.load({"input_priority": True})
    assert not FakeProcess.instances  # not sharing yet
    controls.sync(hosting=True, source="game_window")
    process = FakeProcess.instances[0]
    assert controls.status_box.get_visible()
    controls.sync(hosting=False, source="game_window")
    assert process.stopped and not controls.status_box.get_visible()


def test_the_status_row_says_who_has_the_mouse_and_keyboard(controls) -> None:
    controls.load({"input_priority": True})
    controls.sync(hosting=True, source="game_window")
    process = FakeProcess.instances[0]
    assert controls.state == "starting"

    process.on_event({"event": "ready", "remote": 3, "local": 2})
    assert wait(lambda: controls.state == "guest")
    guest_title = controls.status_row.get_title()

    process.on_event({"event": "paused", "delay": 5})
    assert wait(lambda: controls.state == "host")
    assert controls.status_row.get_title() not in ("", guest_title)

    process.on_event({"event": "resumed"})
    assert wait(lambda: controls.state == "guest")
    assert controls.status_row.get_title() == guest_title


def test_a_problem_is_explained_in_preferences_and_in_share(controls) -> None:
    controls.load({"input_priority": True})
    controls.sync(hosting=True, source="desktop")
    FakeProcess.instances[0].on_event({"event": "unavailable", "reason": "permission"})
    assert wait(lambda: controls.state == "unavailable")
    assert controls.problem_row.get_visible() and controls.problem_row.get_subtitle()
    assert controls.status_row.get_subtitle() == controls.problem_row.get_subtitle()


def test_a_helper_that_ends_by_itself_is_not_shown_as_working(controls) -> None:
    controls.load({"input_priority": True})
    controls.sync(hosting=True, source="desktop")
    FakeProcess.instances[0].on_event({"event": "ready", "remote": 3, "local": 2})
    FakeProcess.instances[0].on_event({"event": "stopped", "code": -9})
    assert wait(lambda: controls.state == "unavailable")


def test_messages_from_a_stopped_helper_are_ignored(controls) -> None:
    controls.load({"input_priority": True})
    controls.sync(hosting=True, source="desktop")
    old = FakeProcess.instances[0]
    controls.sync(hosting=False, source="desktop")
    controls.sync(hosting=True, source="desktop")
    old.on_event({"event": "paused", "delay": 5})
    assert not wait(lambda: controls.state == "host", timeout=0.3)


def test_the_on_screen_message_is_only_for_full_desktop_and_not_repeated(controls, monkeypatch) -> None:
    shown: list[str] = []
    monkeypatch.setattr(controls, "_show_osd", lambda text, original=controls._show_osd: (shown.append(text), original(text)))
    controls.load({"input_priority": True})
    controls.sync(hosting=True, source="game_window")
    process = FakeProcess.instances[0]
    process.on_event({"event": "paused", "delay": 5})
    assert wait(lambda: controls.state == "host")
    assert controls._last_osd == 0.0  # Game Window: the other person sees only the game

    controls.sync(hosting=True, source="desktop")
    process.on_event({"event": "resumed"})
    assert wait(lambda: controls.state == "guest")
    first = controls._last_osd
    assert first > 0.0
    process.on_event({"event": "paused", "delay": 5})
    assert wait(lambda: controls.state == "host")
    assert controls._last_osd == first  # a second message seconds later is skipped


# ── in the real Share page ────────────────────────────────────────────────


def test_share_preferences_offer_it_and_save_it_with_the_other_host_settings(ui) -> None:
    host = ui.host_view
    controls = host.input_priority
    assert controls.group.get_parent() is not None
    assert controls.status_box.get_parent() is not None
    controls.switch_row.set_active(True)
    controls.delay_row.set_selected(backend.DELAY_CHOICES.index(3))
    host.save_host_settings()
    saved = host.config.get("host")
    assert saved["input_priority"] is True and saved["input_priority_delay"] == 3
    controls.switch_row.set_active(False)
    host.load_settings()
    assert controls.enabled and controls.delay == 3
    controls.switch_row.set_active(False)
