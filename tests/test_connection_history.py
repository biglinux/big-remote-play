"""Share → Support → Connection history: stored sessions, the 7-day chart, its empty state.

The history file lives in a temporary directory and the clock is fixed; the
chart is drawn into an off-screen Cairo surface, so a drawing error fails the
test instead of leaving an empty card.
"""

from __future__ import annotations

from datetime import datetime
import json
import time

import pytest

from big_remote_play.host.share_history import MIN_SECONDS, RETENTION_DAYS, ShareHistory

NOW = datetime(2026, 10, 1, 20, 0, 0).timestamp()


def history(tmp_path, now=NOW):
    return ShareHistory(tmp_path / "shared.json", clock=lambda: now)


def test_empty_history_has_seven_days_of_nothing(tmp_path):
    summary = history(tmp_path).summary()
    assert [day.sessions for day in summary.days] == [0] * 7
    assert summary.days[-1].day == datetime.fromtimestamp(NOW).date()
    assert not summary.has_history and summary.last_connection is None and summary.average_seconds is None


def test_one_session_is_counted_on_its_local_day_with_its_duration(tmp_path):
    store = history(tmp_path)
    session = store.start("Living room", started_at=NOW - 3600)
    store.finish(session, ended_at=NOW - 600)
    summary = store.summary()
    assert summary.sessions == 1 and summary.days[-1].sessions == 1
    assert summary.total_seconds == 3000 and summary.average_seconds == 3000
    assert summary.last_connection == NOW - 600


def test_several_sessions_over_several_days(tmp_path):
    store = history(tmp_path)
    for days_ago, minutes in ((0, 30), (0, 10), (2, 60), (6, 5), (8, 99)):
        start = NOW - days_ago * 86400 - 7200
        store.finish(store.start("PC", started_at=start), ended_at=start + minutes * 60)
    summary = store.summary()
    assert [day.sessions for day in summary.days] == [1, 0, 0, 0, 1, 0, 2]  # 8 days ago is outside the week
    assert summary.sessions == 4
    assert summary.total_seconds == (30 + 10 + 60 + 5) * 60
    assert summary.has_history


def test_a_session_of_a_few_seconds_is_not_kept(tmp_path):
    store = history(tmp_path)
    store.finish(store.start("PC", started_at=NOW - 2), ended_at=NOW - 2 + MIN_SECONDS / 2)
    assert store.sessions() == []


def test_a_session_still_open_counts_without_inventing_a_duration(tmp_path):
    store = history(tmp_path)
    store.start("PC", started_at=NOW - 60)
    summary = store.summary()
    assert summary.sessions == 1 and summary.total_seconds == 0 and summary.average_seconds is None


def test_malformed_future_and_old_entries_are_skipped_not_rewritten(tmp_path):
    path = tmp_path / "shared.json"
    entries = [
        {"id": "ok", "started_at": NOW - 100, "ended_at": NOW - 50, "device": "Good"},
        {"id": "text", "started_at": "yesterday"},
        "not an object",
        {"id": "future", "started_at": NOW + 86400},
        {"id": "old", "started_at": NOW - (RETENTION_DAYS + 1) * 86400, "ended_at": NOW - RETENTION_DAYS * 86400},
        {"id": "backwards", "started_at": NOW - 100, "ended_at": NOW - 200},
    ]
    path.write_text(json.dumps({"version": 1, "sessions": entries}))
    sessions = ShareHistory(path, clock=lambda: NOW).sessions()
    assert [session.id for session in sessions] == ["ok", "backwards"]
    assert sessions[1].ended_at is None  # an impossible end is not trusted


def test_an_unreadable_history_file_is_left_alone(tmp_path):
    path = tmp_path / "shared.json"
    path.write_text("{ not json")
    store = ShareHistory(path, clock=lambda: NOW)
    assert store.start("PC") == ""
    assert path.read_text() == "{ not json"
    assert store.summary().has_history is False


def test_the_file_is_private_and_holds_no_address(tmp_path):
    store = history(tmp_path)
    store.finish(store.start("Alice's PC", started_at=NOW - 300), ended_at=NOW)
    path = tmp_path / "shared.json"
    assert path.stat().st_mode & 0o777 == 0o600
    stored = json.loads(path.read_text())["sessions"][0]
    assert set(stored) == {"id", "started_at", "device", "ended_at"}


# ── the chart ─────────────────────────────────────────────────────────────

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from test_ui_task_flows import ui as _ui_fixture  # noqa: E402

ui = _ui_fixture


def draw(widget, width=420, height=150):
    import cairo

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    context = cairo.Context(surface)
    draw_func = getattr(widget, "_draw", None) or widget._on_draw
    draw_func(widget, context, width, height)
    surface.flush()
    return bytes(surface.get_data())


@pytest.mark.parametrize("sessions", [0, 1, 25])
@pytest.mark.parametrize("scheme", [Adw.ColorScheme.FORCE_LIGHT, Adw.ColorScheme.FORCE_DARK])
def test_the_chart_draws_every_case_in_both_themes(ui, tmp_path, sessions, scheme):
    from big_remote_play.ui.connection_history import DayBars

    Adw.StyleManager.get_default().set_color_scheme(scheme)
    try:
        store = history(tmp_path)
        for index in range(sessions):
            start = NOW - (index % 7) * 86400 - 3600
            store.finish(store.start("PC", started_at=start), ended_at=start + 600)
        bars = DayBars()
        bars.show_summary(store.summary())
        pixels = draw(bars)
        assert not bars._failed
        assert any(pixels)  # something was drawn: at least the baseline and labels
    finally:
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.DEFAULT)


def test_the_chart_is_described_in_words(ui, tmp_path):
    from big_remote_play.ui.connection_history import DayBars, weekday_label

    store = history(tmp_path)
    store.finish(store.start("PC", started_at=NOW - 3600), ended_at=NOW - 60)
    bars = DayBars()
    summary = store.summary()
    bars.show_summary(summary)
    today = weekday_label(summary.days[-1].day)
    assert today and today != summary.days[-1].day.isoformat()


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_with_no_history_the_card_says_so_instead_of_an_empty_chart(ui, tmp_path):
    from big_remote_play.ui.connection_history import ShareHistoryCard

    card = ShareHistoryCard(history(tmp_path), clock=lambda: NOW)
    card.refresh()
    assert wait_for(lambda: card.stack.get_visible_child_name() == "empty")
    assert not card.clear_row.get_visible()


def test_with_history_the_card_shows_the_week_and_its_totals(ui, tmp_path):
    from big_remote_play.ui.connection_history import ShareHistoryCard

    store = history(tmp_path)
    store.finish(store.start("PC", started_at=NOW - 7200), ended_at=NOW - 3600)
    card = ShareHistoryCard(store, clock=lambda: NOW)
    card.refresh()
    assert wait_for(lambda: card.stack.get_visible_child_name() == "chart")
    assert card.values["sessions"].get_label() == "1"
    assert card.values["total"].get_label() == "1 h 0 min"
    assert card.values["last"].get_label().startswith("Today at")


def test_share_records_a_session_from_connected_now_and_ends_it(ui, tmp_path, monkeypatch):
    from big_remote_play.utils.connection_health import ConnectionInfo

    host = ui.host_view
    store = ShareHistory(tmp_path / "shared.json")
    host.share_history_card.history = store
    started = time.time() - 120
    host._record_history([ConnectionInfo("Living room", "192.168.1.30", started_at=started)])
    assert wait_for(lambda: len(store.sessions()) == 1 and all(host._history_sessions.values()))
    host._record_history([])
    assert wait_for(lambda: store.sessions()[0].ended_at is not None)
    assert store.sessions()[0].device == "Living room"


def test_the_live_latency_chart_says_why_it_has_no_line(ui):
    from big_remote_play.utils.connection_health import ConnectionInfo, Health, Quality

    monitor = ui.host_view.perf_monitor
    monitor.update_timer_active = True
    try:
        monitor.update_connections([ConnectionInfo("Phone", "10.0.0.5", health=Health(Quality.NO_RESPONSE))])
        assert "does not answer pings" in monitor.chart._message
        monitor.update_connections([])
        assert "Nobody is playing" in monitor.chart._message
        pixels = draw(monitor.chart, 300, 160)
        assert any(pixels) and not monitor.chart._draw_failed
    finally:
        monitor.update_timer_active = False
