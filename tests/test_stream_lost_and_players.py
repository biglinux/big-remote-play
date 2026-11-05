"""Playing with several people over a private network.

Seen on a real game night (one KDE game PC, two players over ZeroTier):
Sunshine refused the second player (its "channels" is 1 by default), and a
player whose internet carried ~5 Mbps got the automatic 12 Mbps; Moonlight
ended with "Connection terminated: -1" and the app said nothing.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

from big_remote_play.guest.moonlight_client import MoonlightClient, StreamEnd  # noqa: E402
from big_remote_play.ui.sunshine_preferences import CONNECTED_CLIENTS, connected_clients  # noqa: E402
from test_ui_task_flows import drain, ui as _ui_fixture  # noqa: E402

ui = _ui_fixture

# Real moonlight-common-c lines from the failing stream.
CONGESTED = "00:00:12 - SDL Info (0): Control message took over 10 ms to send (net latency: 372 ms | packet loss: 0.000000%)"
TERMINATED = "00:00:17 - SDL Error (0): Connection terminated: -1"


# ── several players ──────────────────────────────────────────────────────
def test_sunshine_lets_several_devices_play_at_once():
    assert int(connected_clients(None)) >= 2
    assert connected_clients("1") == str(CONNECTED_CLIENTS)
    assert connected_clients("not a number") == str(CONNECTED_CLIENTS)


def test_a_higher_limit_chosen_by_the_person_is_kept():
    assert connected_clients("8") == "8"


def test_starting_to_share_writes_the_player_limit(ui):
    assert int(ui.host_view._build_sunshine_config()["channels"]) >= 2


# ── a stream that ended by itself ────────────────────────────────────────
def test_moonlight_reports_a_stream_ended_by_the_network_once():
    client = MoonlightClient()
    client.note_line("00:00:03 - SDL Info (0): Starting video stream...")
    assert client.take_stream_end() is None
    client.note_line(CONGESTED)
    client.note_line(TERMINATED)
    assert client.take_stream_end() == StreamEnd(code=-1, peak_latency_ms=372)
    assert client.take_stream_end() is None


def test_a_stream_end_without_delay_messages_has_no_delay():
    client = MoonlightClient()
    client.note_line(TERMINATED)
    assert client.take_stream_end() == StreamEnd(code=-1, peak_latency_ms=0)


def _responses(dialog: Adw.AlertDialog) -> list[str]:
    return [response for response in ("close", "lower") if dialog.has_response(response)]


def test_a_congested_network_offers_to_reconnect_at_half_the_rate(ui, monkeypatch):
    guest = ui.guest_view
    reconnects = []
    monkeypatch.setattr(guest, "check_reconnect", lambda: reconnects.append(guest.bitrate_scale.get_value()))
    guest.bitrate_scale.set_value(12.0)
    dialog = guest.show_stream_lost(StreamEnd(-1, 372))
    try:
        assert _responses(dialog) == ["close", "lower"]
        assert dialog.get_default_response() == "lower"
        assert "372" in dialog.get_body()
        dialog.emit("response", "lower")
        drain()
        assert reconnects == [6.0]
        settings = guest.config.get("guest", {})
        assert settings["auto_quality"] is False
    finally:
        dialog.force_close()


def test_a_silent_network_explains_the_outage_and_keeps_the_rate(ui, monkeypatch):
    guest = ui.guest_view
    reconnects = []
    monkeypatch.setattr(guest, "check_reconnect", lambda: reconnects.append(True))
    guest.bitrate_scale.set_value(12.0)
    dialog = guest.show_stream_lost(StreamEnd(-1, 0))
    try:
        assert dialog.get_default_response() == "close"
        dialog.emit("response", "close")
        drain()
        assert reconnects == [] and guest.bitrate_scale.get_value() == 12.0
    finally:
        dialog.force_close()


def test_the_lowest_rate_is_not_lowered_further(ui):
    guest = ui.guest_view
    guest.bitrate_scale.set_value(2.0)
    dialog = guest.show_stream_lost(StreamEnd(-1, 900))
    try:
        assert _responses(dialog) == ["close"]
    finally:
        dialog.force_close()


def test_the_running_session_announces_the_stream_end(ui, monkeypatch):
    guest = ui.guest_view
    shown = []
    monkeypatch.setattr(guest, "show_stream_lost", shown.append)
    monkeypatch.setattr(guest.moonlight, "is_connected", lambda: True)
    guest.moonlight.note_line(TERMINATED)
    guest.monitor_connection()
    guest.monitor_connection()
    assert shown == [StreamEnd(-1, 0)]
