"""Share → Preferences → Audio: the Microphone and Voice calls switches.

Real GTK rows and the real settings file in a temporary home; the sound
server is never touched. The routing behind the switches is covered in
test_voice_calls_audio.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

from big_remote_play.ui.host_view import HostView  # noqa: E402
from big_remote_play.utils.audio import AudioStatus, CapturePlan  # noqa: E402
from big_remote_play.utils.config import Config  # noqa: E402

from test_ui_task_flows import drain, ui as _ui_fixture  # noqa: E402

ui = _ui_fixture


def saved_host_settings() -> dict:
    settings = Config().get("host", {})
    return settings if isinstance(settings, dict) else {}


def flush(host: HostView) -> None:
    host._flush_save_host_settings()
    drain()


def test_both_are_real_switches_and_off_by_default(ui):
    host = ui.host_view
    for row in (host.audio_microphone_row, host.audio_calls_row):
        assert isinstance(row, Adw.SwitchRow)
        assert not row.get_active()
        assert row.get_title()  # the switch is named by its row for screen readers
    assert host.audio_microphone_row.is_ancestor(host) and host.audio_calls_row.is_ancestor(host)


def test_choices_are_saved_and_restored_after_reopening(ui):
    host = ui.host_view
    host.audio_microphone_row.set_active(True)
    host.audio_calls_row.set_active(True)
    flush(host)
    settings = saved_host_settings()
    assert settings["audio_send_microphone"] is True and settings["audio_send_calls"] is True

    reopened = HostView()
    try:
        assert reopened.audio_microphone_row.get_active() and reopened.audio_calls_row.get_active()
    finally:
        reopened.cleanup()

    host.audio_microphone_row.set_active(False)
    flush(host)
    assert saved_host_settings()["audio_send_microphone"] is False
    reopened = HostView()
    try:
        assert not reopened.audio_microphone_row.get_active() and reopened.audio_calls_row.get_active()
    finally:
        reopened.cleanup()


def test_only_an_explicit_true_turns_a_choice_on(ui):
    Config().set("host", {**saved_host_settings(), "audio_send_microphone": "yes", "audio_send_calls": 1})
    reopened = HostView()
    try:
        assert not reopened.audio_microphone_row.get_active() and not reopened.audio_calls_row.get_active()
    finally:
        reopened.cleanup()


def test_the_next_sharing_session_starts_with_the_choices(ui, monkeypatch):
    host = ui.host_view
    monkeypatch.setattr(host, "_resolve_game_launch_info", lambda: None)
    host.audio_microphone_row.set_active(True)
    config = host._collect_hosting_config()
    assert config["audio_send_microphone"] is True and config["audio_send_calls"] is False


class FakeSession:
    def __init__(self) -> None:
        self.options: list[tuple[bool, bool]] = []

    def set_options(self, *, send_microphone: bool, send_calls: bool) -> None:
        self.options.append((send_microphone, send_calls))


class FakeWatcher:
    def __init__(self) -> None:
        self.requests = 0

    def request_check(self) -> None:
        self.requests += 1

    def stop(self) -> None:
        pass


def test_a_change_while_sharing_applies_at_once(ui):
    host = ui.host_view
    session, watcher = FakeSession(), FakeWatcher()
    host.audio_session, host.audio_watcher = session, watcher
    try:
        host.audio_microphone_row.set_active(True)
        host.audio_calls_row.set_active(True)
        assert session.options == [(True, False), (True, True)]
        assert watcher.requests == 2  # reconciled now, not at the next start
    finally:
        host.audio_session = host.audio_watcher = None


def test_the_rows_say_what_happens_in_each_state(ui):
    host = ui.host_view
    off = host.audio_microphone_row.get_subtitle()
    host.audio_microphone_row.set_active(True)
    on = host.audio_microphone_row.get_subtitle()
    assert off and on and off != on

    host.is_hosting = True
    try:
        plan = CapturePlan(None, None, "no-output")
        host._apply_audio_status(AudioStatus(plan, send_microphone=True, microphone_in_mix="USB Headset"), host._audio_generation)
        assert "USB Headset" in host.audio_microphone_row.get_subtitle()
        host._apply_audio_status(AudioStatus(plan, send_microphone=True, notes=("microphone-missing",)), host._audio_generation)
        missing = host.audio_microphone_row.get_subtitle()
        assert missing not in (on, off)
    finally:
        host.is_hosting = False

    calls_off = host.audio_calls_row.get_subtitle()
    host.audio_calls_row.set_active(True)
    assert host.audio_calls_row.get_subtitle() != calls_off
