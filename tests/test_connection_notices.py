"""A desktop notification when a device starts playing on this computer.

The sessions come from Share's Connected now list (Sunshine's own log plus the
stream's handshake address); here they are synthetic. Nothing is sent to a
real notification server: the window's delivery is replaced at the boundary.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from big_remote_play.host.connection_notices import MAX_NAME_LENGTH, ConnectionNotices, clean_name, session_key
from big_remote_play.host.sunshine_sessions import SessionTracker
from big_remote_play.utils.connection_health import ConnectionInfo, Transport

from test_ui_task_flows import drain, ui as _ui_fixture  # noqa: F401

ui = _ui_fixture

TV = ConnectionInfo("Living Room TV", "100.92.14.8", Transport.TAILSCALE, started_at=1000.0)
LAPTOP = ConnectionInfo("Rafael's Laptop", "192.0.2.45", Transport.LOCAL, started_at=1010.0)


def test_a_new_session_is_announced_with_its_name_connection_and_address():
    notices = ConnectionNotices()
    [notice] = notices.update([TV])
    assert notice.device_name == "Living Room TV"
    assert notice.transport is Transport.TAILSCALE
    assert notice.address == "100.92.14.8"


def test_a_session_that_stays_connected_is_announced_once():
    notices = ConnectionNotices()
    assert len(notices.update([TV])) == 1
    for _poll in range(20):  # a minute of updates every 3 s
        assert notices.update([TV]) == []


def test_a_second_device_is_announced_without_repeating_the_first():
    notices = ConnectionNotices()
    notices.update([TV])
    [notice] = notices.update([TV, LAPTOP])
    assert notice.device_name == "Rafael's Laptop" and notice.transport is Transport.LOCAL


def test_reconnecting_is_a_new_session_and_is_announced_again():
    notices = ConnectionNotices()
    notices.update([TV])
    assert notices.update([]) == []  # it disconnected
    again = ConnectionInfo(TV.device_name, TV.address, TV.transport, started_at=1200.0)
    assert len(notices.update([again])) == 1


def test_the_same_device_reconnecting_between_two_updates_is_still_new():
    """The tracker gives every session its own start, even from the same address."""
    notices = ConnectionNotices()
    notices.update([TV])
    again = ConnectionInfo(TV.device_name, TV.address, TV.transport, started_at=1003.0)
    assert len(notices.update([again])) == 1


def test_stopping_sharing_forgets_every_session():
    notices = ConnectionNotices()
    notices.update([TV])
    notices.clear()
    assert len(notices.update([TV])) == 1


def test_missing_name_address_and_path_do_not_break_anything():
    notices = ConnectionNotices()
    [notice] = notices.update([ConnectionInfo("", "", Transport.UNKNOWN, started_at=5.0)])
    assert notice.device_name == "" and notice.address == "" and notice.transport is Transport.UNKNOWN
    # No start time: not a session the tracker reported, nothing to announce.
    assert notices.update([ConnectionInfo("TV", "192.0.2.9", started_at=None)]) == []


def test_placeholder_names_are_not_shown_as_a_device_name():
    notices = ConnectionNotices()
    [notice] = notices.update([ConnectionInfo("Connected device", "", started_at=7.0, name_known=False)])
    assert notice.device_name == ""


def test_a_real_name_is_shown_even_if_it_reads_like_a_placeholder():
    # Whether a name is real is a flag, never a comparison with translated words.
    notices = ConnectionNotices()
    [notice] = notices.update([ConnectionInfo("Connected device", "192.0.2.7", started_at=7.0)])
    assert notice.device_name == "Connected device"


def test_the_monitor_marks_a_session_without_a_found_name(ui, monkeypatch):
    import big_remote_play.ui.performance_monitor as monitor_module

    class Session:
        preexisting = False

    monitor = ui.host_view.perf_monitor
    names = {"192.0.2.7": "Living Room TV", "192.0.2.8": ""}
    monkeypatch.setattr(monitor, "_known_name", lambda address: names.get(address, ""))
    monkeypatch.setattr(monitor, "_targets", lambda: [(address, monitor._display_name(address), 5.0 + index, Session()) for index, address in enumerate(names)])
    monkeypatch.setattr(monitor, "_session_details", lambda session, newest: ("", ()))
    monkeypatch.setattr(monitor, "_transport", lambda address, now: Transport.LOCAL)
    monkeypatch.setattr(monitor_module, "ping_once", lambda address: None)
    while not monitor._data_queue.empty():
        monitor._data_queue.get_nowait()
    monitor._fetch_and_process_data()
    named, unnamed = monitor._data_queue.get_nowait()
    assert named.name_known and named.device_name == "Living Room TV"
    assert not unnamed.name_known and unnamed.device_name  # still labelled on screen
    assert [notice.device_name for notice in ConnectionNotices().update([named, unnamed])] == ["Living Room TV", ""]


def test_sessions_already_playing_when_watching_began_are_not_announced():
    notices = ConnectionNotices()
    old = ConnectionInfo("TV", "192.0.2.9", Transport.LOCAL, started_at=1.0, preexisting=True)
    assert notices.update([old]) == []
    assert notices.update([old]) == []
    assert len(notices.update([old, LAPTOP])) == 1


def test_untrusted_names_are_one_short_plain_line():
    assert clean_name("TV\nIP: 10.0.0.1\x07") == "TV IP: 10.0.0.1"
    assert clean_name("‮evil​ name") == "evil name"  # bidirectional override and zero-width space dropped
    assert clean_name("<b>TV</b>") == "<b>TV</b>"  # kept as text; the title is never markup
    assert len(clean_name("x" * 500)) == MAX_NAME_LENGTH
    assert clean_name(None) == "" and clean_name(42) == ""


def test_only_literal_addresses_are_kept():
    assert session_key(ConnectionInfo("TV", "gaming-pc.local", started_at=3.0)) == ("", 3.0)
    assert session_key(ConnectionInfo("TV", "fe80::1%eth0", started_at=3.0)) == ("fe80::1", 3.0)
    assert session_key(ConnectionInfo("TV", "192.0.2.1", started_at="bad")) is None  # type: ignore[arg-type]


def test_the_tracker_marks_sessions_found_on_its_first_read_as_preexisting(tmp_path):
    log = tmp_path / "sunshine.log"
    log.write_text("Sunshine version: v2026\nCLIENT CONNECTED\n")
    tracker = SessionTracker(log, ss=lambda argv: "")
    [first] = tracker.poll()
    assert first.preexisting
    with log.open("a") as handle:
        handle.write("CLIENT CONNECTED\n")
    sessions = tracker.poll()
    assert [session.preexisting for session in sessions] == [True, False]


# ── Share delivers one notification per new session ─────────────────────


def _delivered(ui, monkeypatch):
    sent = []
    monkeypatch.setattr(ui.host_view, "_deliver_notification", lambda notification_id, title, body: sent.append((notification_id, title, body)))
    return sent


def test_share_sends_a_notification_for_a_new_device(ui, monkeypatch):
    sent = _delivered(ui, monkeypatch)
    ui.host_view._show_connected_devices([TV])
    [(notification_id, title, body)] = sent
    assert "Living Room TV" in title
    assert "Tailscale" in body and "100.92.14.8" in body
    ui.host_view._show_connected_devices([TV])
    assert len(sent) == 1  # the same session again: nothing more
    ui.host_view._show_connected_devices([TV, LAPTOP])
    assert len(sent) == 2 and sent[1][0] != notification_id
    assert "192.0.2.45" in sent[1][2]


def test_a_device_without_a_known_address_is_announced_without_one(ui, monkeypatch):
    sent = _delivered(ui, monkeypatch)
    ui.host_view._show_connected_devices([ConnectionInfo("Connected device", "", started_at=9.0, name_known=False)])
    [(_id, title, body)] = sent
    assert "Connected device" not in title  # a placeholder is not a name
    assert body  # still says what happened


def test_notifications_do_not_write_names_or_addresses_to_the_log(ui, monkeypatch, caplog):
    _delivered(ui, monkeypatch)
    with caplog.at_level("DEBUG"):
        ui.host_view._show_connected_devices([TV])
    assert "100.92.14.8" not in caplog.text and "Living Room TV" not in caplog.text


def test_the_notification_goes_through_the_application(ui, monkeypatch):
    sent = []
    application = ui.get_application()
    if application is None:
        # The test window has no application: delivery is skipped, not a crash.
        ui.host_view._deliver_notification("brp-device-connected-1", "TV connected", "IP address: 192.0.2.1")
        return
    monkeypatch.setattr(application, "send_notification", lambda notification_id, notification: sent.append(notification_id))
    ui.host_view._deliver_notification("brp-device-connected-1", "TV connected", "IP address: 192.0.2.1")
    assert sent == ["brp-device-connected-1"]
    drain()
