"""A device asking to pair is shown at once, answered here, and never approved by itself.

Sunshine, the keyring and the clock are faked; no request leaves the test and
no PIN is ever written anywhere but to the fake Sunshine.
"""

from __future__ import annotations

import time

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib  # noqa: E402

from big_remote_play.host.pairing_requests import RequestTracker, countdown_text, friendly_name, valid_pin  # noqa: E402
from big_remote_play.host.sunshine_manager import PendingPairing, PinResult  # noqa: E402
from big_remote_play.ui.pairing_prompt import PairingRequestDialog  # noqa: E402

from test_ui_task_flows import ui as _ui_fixture  # noqa: E402

ui = _ui_fixture
ID_A = "a" * 32
ID_B = "b" * 32


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


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


# ── the tracker (no GTK) ──────────────────────────────────────────────────


def test_no_request_means_nothing_to_show():
    tracker = RequestTracker()
    events = tracker.update([])
    assert events.new == events.gone == events.expired == ()
    assert tracker.oldest() is None


def test_a_new_request_is_announced_once_and_expires_after_its_timeout():
    clock = Clock()
    tracker = RequestTracker(timeout=120, clock=clock)
    request = [PendingPairing(ID_A, "Living room", "192.168.1.30")]
    assert [item.name for item in tracker.update(request).new] == ["Living room"]
    clock.now += 60
    events = tracker.update(request)
    assert events.new == () and events.expired == ()
    assert tracker.requests[ID_A].remaining(clock.now) == 60
    clock.now += 61
    assert [item.pairing_id for item in tracker.update(request).expired] == [ID_A]
    assert ID_A not in tracker.requests


def test_a_request_the_device_abandoned_is_gone():
    tracker = RequestTracker()
    tracker.update([PendingPairing(ID_A, "Laptop", "10.0.0.2")])
    events = tracker.update([])
    assert [item.pairing_id for item in events.gone] == [ID_A]


def test_moonlights_placeholder_name_is_replaced_by_what_the_network_knows():
    assert friendly_name(PendingPairing(ID_A, "Bedroom PC", "10.0.0.2")) == "Bedroom PC"
    assert friendly_name(PendingPairing(ID_A, "roth", "10.0.0.2"), lambda address: "bedroom-pc") == "bedroom-pc"
    assert friendly_name(PendingPairing(ID_A, "", "::ffff:10.0.0.2"), lambda address: "") == "Device at 10.0.0.2"
    assert friendly_name(PendingPairing(ID_A, "roth", ""), None) == "Another computer"


@pytest.mark.parametrize("typed,expected", [("1234", "1234"), (" 12 34 ", "1234"), ("12-34", "1234"), ("123", ""), ("12345", ""), ("12a4", ""), ("１２３４", "")])
def test_a_pin_is_four_ascii_digits_spaces_and_dashes_ignored(typed, expected):
    assert valid_pin(typed) == expected


def test_countdown_reads_like_a_clock():
    assert countdown_text(102) == "This request expires in 1:42"
    assert countdown_text(0) == "This request expires in 0:00"


# ── on the Share page ─────────────────────────────────────────────────────


class FakeSunshine:
    def __init__(self):
        self.pending: list[PendingPairing] = []
        self.status = 200
        self.sent: list[tuple[str, str | None, str | None]] = []
        self.cancelled: list[str] = []

    def running_app_id(self, timeout=2.0):
        return 0

    def pending_pairings(self, auth=None):
        return (list(self.pending), self.status) if self.status == 200 else (None, self.status)

    def discard_abandoned_pairings(self, auth=None, **kwargs):
        return 0

    def cancel_pairing(self, pairing_id, auth=None):
        self.cancelled.append(pairing_id)
        self.pending = [item for item in self.pending if item.pairing_id != pairing_id]
        return True

    def send_pin(self, pin, name=None, auth=None, pairing_id=None):
        self.sent.append((pin, name, pairing_id))
        self.pending = [item for item in self.pending if item.pairing_id != pairing_id]
        return PinResult(True, 200, "PIN sent successfully")


@pytest.fixture
def host(ui, monkeypatch):
    h = ui.host_view
    fake = FakeSunshine()
    for name in ("running_app_id", "pending_pairings", "discard_abandoned_pairings", "cancel_pairing", "send_pin"):
        monkeypatch.setattr(h.sunshine, name, getattr(fake, name))
    monkeypatch.setattr(h, "_get_sunshine_creds", lambda: ("admin", "secret"))
    monkeypatch.setattr(h.perf_monitor, "_display_name", lambda address: f"pc-{address}")
    toasts: list[str] = []
    monkeypatch.setattr(h, "show_toast", toasts.append)
    h.is_hosting = True
    h.fake, h.toasts = fake, toasts
    yield h
    h._end_pair_requests()
    h.is_hosting = False


def check(host):
    host._pair_busy_checking = False
    host._check_pairing_busy()
    wait_for(lambda: not host._pair_busy_checking)


def test_a_waiting_device_opens_the_request_with_the_pin_field_ready(host):
    host.fake.pending = [PendingPairing(ID_A, "Living room", "192.168.1.30")]
    check(host)
    assert wait_for(lambda: isinstance(host._pair_dialog, PairingRequestDialog))
    dialog = host._pair_dialog
    assert "Living room" in dialog.get_body()
    assert not dialog.get_response_enabled("approve")  # nothing typed yet
    assert host.pair_requests_group.get_visible()  # and a card stays on the page
    assert "expires in" in dialog.countdown.get_label()


def test_approving_sends_the_typed_pin_for_that_request_only(host):
    host.fake.pending = [PendingPairing(ID_A, "Living room", "192.168.1.30"), PendingPairing(ID_B, "Laptop", "192.168.1.31")]
    check(host)
    assert wait_for(lambda: host._pair_dialog is not None)
    dialog = host._pair_dialog
    dialog.entry.set_text("12 34")  # pasted with a space
    assert dialog.get_response_enabled("approve")
    dialog.entry.emit("entry-activated")  # Enter approves
    assert wait_for(lambda: host.fake.sent)
    assert host.fake.sent == [("1234", "Living room", ID_A)]
    assert wait_for(lambda: any("Living room can now play" in toast for toast in host.toasts))


def test_an_invalid_pin_is_explained_and_never_sent(host):
    host.fake.pending = [PendingPairing(ID_A, "Living room", "192.168.1.30")]
    check(host)
    assert wait_for(lambda: host._pair_dialog is not None)
    dialog = host._pair_dialog
    dialog.entry.set_text("12345")
    assert dialog.error.get_visible() and not dialog.get_response_enabled("approve")
    dialog.entry.emit("entry-activated")
    wait_for(lambda: False, timeout=0.2)
    assert host.fake.sent == []
    assert host._pair_dialog is dialog  # it stays open for a correction


def test_rejecting_cancels_the_request_in_sunshine(host):
    host.fake.pending = [PendingPairing(ID_A, "Living room", "192.168.1.30")]
    check(host)
    assert wait_for(lambda: host._pair_dialog is not None)
    host._pair_dialog.emit("response", "reject")
    assert wait_for(lambda: host.fake.cancelled == [ID_A])
    assert host.fake.sent == []
    assert not host.pair_requests_group.get_visible()


def test_an_unanswered_request_expires_and_is_cancelled(host, monkeypatch):
    clock = Clock()
    host._pair_tracker.clock = clock
    host.fake.pending = [PendingPairing(ID_A, "Living room", "192.168.1.30")]
    check(host)
    assert wait_for(lambda: host._pair_dialog is not None)
    clock.now += 121
    check(host)
    assert wait_for(lambda: host.fake.cancelled == [ID_A])
    assert host._pair_dialog is None
    assert any("expired" in toast for toast in host.toasts)


def test_a_device_that_stops_waiting_closes_its_request(host):
    host.fake.pending = [PendingPairing(ID_A, "Living room", "192.168.1.30")]
    check(host)
    assert wait_for(lambda: host._pair_dialog is not None)
    host.fake.pending = []
    check(host)
    assert wait_for(lambda: host._pair_dialog is None)
    assert any("stopped waiting" in toast for toast in host.toasts)


def test_nothing_is_shown_while_sunshine_does_not_answer(host):
    host.fake.status = 0
    host.fake.pending = [PendingPairing(ID_A, "Living room", "192.168.1.30")]
    check(host)
    assert host._pair_dialog is None and not host.pair_requests_group.get_visible()


def test_a_request_on_screen_survives_a_moment_when_sunshine_does_not_answer(host):
    host.fake.pending = [PendingPairing(ID_A, "Living room", "192.168.1.30")]
    check(host)
    assert wait_for(lambda: host._pair_dialog is not None)
    dialog = host._pair_dialog
    host.fake.status = 0  # unreadable is not "nobody waits"
    check(host)
    assert host._pair_dialog is dialog and host.pair_requests_group.get_visible()
    assert not any("stopped waiting" in toast for toast in host.toasts)


def test_stopping_sharing_clears_every_request(host):
    host.fake.pending = [PendingPairing(ID_A, "Living room", "192.168.1.30")]
    check(host)
    assert wait_for(lambda: host._pair_dialog is not None)
    host.is_hosting = False
    host.sync_ui_state()
    assert host._pair_dialog is None and not host.pair_requests_group.get_visible()
    assert host.fake.sent == []


def test_placeholder_names_use_the_name_the_network_gives(host):
    host.fake.pending = [PendingPairing(ID_A, "roth", "192.168.1.30")]
    check(host)
    assert wait_for(lambda: host._pair_dialog is not None)
    assert host._pair_dialog.request.name == "pc-192.168.1.30"


# ── Sunshine without a user yet ───────────────────────────────────────────


def test_a_first_sunshine_gets_a_user_kept_in_the_keyring(ui, monkeypatch):
    import big_remote_play.ui.host_view as hv
    from big_remote_play.utils import secret_store

    host = ui.host_view
    created, saved = [], []
    monkeypatch.setattr(host.sunshine, "pending_pairings", lambda auth=None: (None, 307))
    monkeypatch.setattr(host.sunshine, "create_user", lambda user, password: created.append((user, len(password))) or (True, ""))
    monkeypatch.setattr(hv, "save_sunshine_credentials", lambda user, password, **kwargs: saved.append(user))
    monkeypatch.setattr(secret_store.SecretStore, "is_available", lambda self: True)
    credentials = host._create_sunshine_credentials()
    assert credentials is not None and created and saved == [credentials[0]]
    assert created[0][1] >= 24  # a long random password, never shown or logged
    assert host._create_sunshine_credentials() is None  # once per session


@pytest.mark.parametrize("status,keyring", [(401, True), (200, True), (0, True), (307, False)])
def test_an_existing_sunshine_user_is_never_replaced(ui, monkeypatch, status, keyring):
    from big_remote_play.utils import secret_store

    host = ui.host_view
    created = []
    monkeypatch.setattr(host.sunshine, "pending_pairings", lambda auth=None: (None, status))
    monkeypatch.setattr(host.sunshine, "create_user", lambda user, password: created.append(user) or (True, ""))
    monkeypatch.setattr(secret_store.SecretStore, "is_available", lambda self: keyring)
    assert host._create_sunshine_credentials() is None
    assert created == []
