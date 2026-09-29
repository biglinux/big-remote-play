"""Pairing while Sunshine has a stream open: say why, and offer to end it.

Moonlight on TVs and phones refuses to pair while Sunshine reports an open
app ("The computer is currently in a game"), even after everyone
disconnected. Sunshine and the keyring are faked; no request leaves the test.
"""

from __future__ import annotations

import time

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib  # noqa: E402

from big_remote_play.host.sunshine_manager import PIN_NONE_WAITING, PinResult  # noqa: E402

from test_ui_task_flows import ui as _ui_fixture  # noqa: E402  (the hermetic window fixture)

ui = _ui_fixture


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


class FakeSunshine:
    """The open app as Sunshine reports it, and what was asked of it."""

    def __init__(self, app_id=881448767, closes=True):
        self.app_id = app_id
        self.closes = closes
        self.closed_with = []

    def running_app_id(self, timeout=2.0):
        return self.app_id

    def close_app(self, auth=None):
        self.closed_with.append(auth)
        if self.closes:
            self.app_id = 0
        return self.closes

    def send_pin(self, pin, name=None, auth=None, pairing_id=None):
        return PinResult(False, PIN_NONE_WAITING, "No computer is waiting.")


@pytest.fixture
def host(ui, monkeypatch):
    h = ui.host_view
    fake = FakeSunshine()
    for name in ("running_app_id", "close_app", "send_pin"):
        monkeypatch.setattr(h.sunshine, name, getattr(fake, name))
    monkeypatch.setattr(h, "_get_sunshine_creds", lambda: ("admin", "secret"))
    toasts = []
    monkeypatch.setattr(h, "show_toast", toasts.append)
    h.is_hosting = True
    h.fake, h.toasts = fake, toasts
    yield h
    h.is_hosting = False


def test_the_pairing_step_shows_an_open_stream_only_while_sharing(host):
    host._check_pairing_busy()
    assert wait_for(host.pair_busy_row.get_visible)
    assert host.end_for_everyone_row.get_visible() and host.end_for_everyone_row.get_sensitive()
    host.fake.app_id = 0  # everyone left and Sunshine closed the app
    host._check_pairing_busy()
    assert wait_for(lambda: not host.pair_busy_row.get_visible())
    assert not host.end_for_everyone_row.get_visible()
    host.fake.app_id = 7
    host.is_hosting = False
    host._check_pairing_busy()
    assert not host.pair_busy_row.get_visible()


def test_an_unknown_state_does_not_claim_a_stream_is_open(host):
    host.fake.app_id = None
    host._check_pairing_busy()
    wait_for(lambda: not getattr(host, "_pair_busy_checking", False))
    assert not host.pair_busy_row.get_visible()


def test_no_device_waiting_because_of_an_open_stream_offers_to_end_it(ui, host):
    host.pair_entry.set_text("1234")
    host.pair_with_entered_pin()
    assert wait_for(lambda: ui.get_visible_dialog() is not None)
    dialog = ui.get_visible_dialog()
    assert dialog.has_response("end") and dialog.get_default_response() == "cancel"  # never ends by accident
    assert "currently in a game" in dialog.get_body()
    assert host.fake.closed_with == []
    dialog.emit("response", "end")
    dialog.close()
    assert wait_for(lambda: host.toasts)
    assert host.fake.closed_with == [("admin", "secret")]
    assert not host.pair_busy_row.get_visible()
    assert host.pair_entry.get_text() == ""  # the new device shows a new code


def test_cancelling_leaves_everyone_playing(ui, host):
    host._confirm_end_for_everyone()
    dialog = ui.get_visible_dialog()
    dialog.emit("response", "cancel")
    dialog.close()
    GLib.MainContext.default().iteration(False)
    assert host.fake.closed_with == []


def test_without_an_open_stream_the_plain_explanation_stays(ui, host):
    host.fake.app_id = 0
    host.pair_entry.set_text("1234")
    host.pair_with_entered_pin()
    assert wait_for(lambda: ui.get_visible_dialog() is not None)
    assert not ui.get_visible_dialog().has_response("end")


def test_a_stream_sunshine_does_not_close_is_reported(ui, host):
    host.fake.closes = False
    host._show_pairing_busy(True)
    host._end_for_everyone()
    assert wait_for(lambda: ui.get_visible_dialog() is not None)
    assert not ui.get_visible_dialog().has_response("end")  # an error, not the offer again
    assert host.pair_busy_row.get_visible() and host.end_for_everyone_row.get_sensitive()
    assert host.toasts == []


def test_ending_without_saved_credentials_does_not_call_sunshine(ui, host, monkeypatch):
    monkeypatch.setattr(host, "_get_sunshine_creds", lambda: None)
    host._end_for_everyone()
    assert wait_for(lambda: ui.get_visible_dialog() is not None)
    assert host.fake.closed_with == []
