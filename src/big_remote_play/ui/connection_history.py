"""Support → Connection history: how this computer was used to play, lately.

The numbers come from :mod:`big_remote_play.host.share_history`, read on a
worker. The bars are drawn with Cairo in the theme's own colours (accent and
foreground, so light, dark and high contrast all work); every value is also a
native label, and the chart's accessible description reads the days in
words, so nothing depends on seeing the drawing.
"""

from __future__ import annotations

from collections.abc import Callable
import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # type: ignore

from big_remote_play.host.share_history import HistorySummary, ShareHistory
from big_remote_play.utils.i18n import _, ngettext

from .history_dialog import format_duration
from .network_common import Worker, confirm

_log = logging.getLogger("big-remoteplay")


def weekday_label(day) -> str:
    """The day's short name in the person's language (GLib follows the locale)."""
    stamp = GLib.DateTime.new_local(day.year, day.month, day.day, 12, 0, 0)
    return stamp.format("%a") if stamp is not None else day.isoformat()


def last_connection_text(timestamp: float | None, now: float | None = None) -> str:
    from .remote_connection import when_text

    return when_text(timestamp, now=now) if timestamp else _("Never")


class DayBars(Gtk.DrawingArea):
    """One bar per day; the height is the number of sessions."""

    __gtype_name__ = "BrpDayBars"

    def __init__(self) -> None:
        super().__init__()
        self.summary: HistorySummary | None = None
        self._failed = False
        self.set_content_height(150)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)
        self.set_accessible_role(Gtk.AccessibleRole.IMG)

    def show_summary(self, summary: HistorySummary) -> None:
        self.summary = summary
        days = ", ".join(ngettext("{day}: {count} session", "{day}: {count} sessions", item.sessions).format(day=weekday_label(item.day), count=item.sessions) for item in summary.days)
        self.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION], [_("Connections in the last 7 days"), days])
        self.queue_draw()

    def _colors(self) -> tuple[Gdk.RGBA, Gdk.RGBA]:
        foreground = self.get_color()
        accent: Gdk.RGBA | None = None
        manager = Adw.StyleManager.get_default()
        getter = getattr(manager, "get_accent_color_rgba", None)
        if callable(getter):
            try:
                accent = getter()
            except Exception:  # an older libadwaita: the foreground still draws
                accent = None
        if accent is None or manager.get_high_contrast():
            accent = foreground
        return foreground, accent

    def _draw(self, _area, cr, width: int, height: int) -> None:
        summary = self.summary
        if summary is None or not summary.days or width <= 0 or height <= 0:
            return
        try:
            foreground, accent = self._colors()
            layout_height = 18
            top, bottom = 20, layout_height + 6
            chart = max(1, height - top - bottom)
            count = len(summary.days)
            slot = width / count
            bar = max(6.0, min(36.0, slot * 0.55))
            peak = max((item.sessions for item in summary.days), default=0) or 1
            # Baseline, in the foreground at low alpha: visible in every theme.
            cr.set_source_rgba(foreground.red, foreground.green, foreground.blue, 0.25)
            cr.set_line_width(1)
            cr.move_to(0, top + chart + 0.5)
            cr.line_to(width, top + chart + 0.5)
            cr.stroke()
            for index, item in enumerate(summary.days):
                center = slot * index + slot / 2
                value = chart * item.sessions / peak
                if item.sessions:
                    cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.9)
                    _rounded_top(cr, center - bar / 2, top + chart - value, bar, value, min(6.0, bar / 2))
                    cr.fill()
                self._text(cr, str(item.sessions), center, top + chart - value - 4, foreground, alpha=0.9 if item.sessions else 0.45, above=True)
                self._text(cr, weekday_label(item.day), center, height - 4, foreground, alpha=0.75)
            self._failed = False
        except Exception:
            # Logged once; the same numbers stay readable as labels beside it.
            if not self._failed:
                _log.exception("Could not draw the connection history chart")
            self._failed = True

    def _text(self, cr, text: str, center: float, baseline: float, color, *, alpha: float = 1.0, above: bool = False) -> None:
        layout = self.create_pango_layout(text)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        _ink, logical = layout.get_pixel_extents()
        cr.set_source_rgba(color.red, color.green, color.blue, alpha)
        cr.move_to(center - logical.width / 2, baseline - logical.height)
        from gi.repository import PangoCairo  # type: ignore

        PangoCairo.show_layout(cr, layout)


def _rounded_top(cr, x: float, y: float, width: float, height: float, radius: float) -> None:
    radius = max(0.0, min(radius, height, width / 2))
    cr.new_path()
    cr.move_to(x, y + height)
    cr.line_to(x, y + radius)
    cr.arc(x + radius, y + radius, radius, 3.14159265, 3 * 3.14159265 / 2)
    cr.line_to(x + width - radius, y)
    cr.arc(x + width - radius, y + radius, radius, 3 * 3.14159265 / 2, 0)
    cr.line_to(x + width, y + height)
    cr.close_path()


class ShareHistoryCard(Adw.PreferencesGroup):
    """Last 7 days, a few totals, or a plain sentence when there is nothing yet."""

    __gtype_name__ = "BrpShareHistoryCard"

    def __init__(self, history: ShareHistory | None = None, *, clock: Callable[[], float] | None = None) -> None:
        super().__init__(title=_("Connection history"), description=_("Devices that played on this computer, kept only here."))
        self.history = history or ShareHistory()
        self._clock = clock
        self._worker = Worker()
        self.summary: HistorySummary | None = None

        self.stack = Gtk.Stack()
        self.stack.set_vhomogeneous(False)
        self.stack.set_hhomogeneous(False)

        loading = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        loading.append(Adw.Spinner())
        loading.append(Gtk.Label(label=_("Loading history…")))
        self.stack.add_named(loading, "loading")

        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        empty.add_css_class("brp-empty")
        title = Gtk.Label(label=_("No connection history yet."), wrap=True)
        title.add_css_class("heading")
        empty.append(title)
        body = Gtk.Label(label=_("Your recent sessions will appear here."), wrap=True)
        body.add_css_class("dim-label")
        empty.append(body)
        self.stack.add_named(empty, "empty")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.add_css_class("card")
        content.add_css_class("padded")
        period = Gtk.Label(label=_("Last 7 days"), xalign=0)
        period.add_css_class("caption-heading")
        content.append(period)
        self.bars = DayBars()
        content.append(self.bars)
        tiles = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, min_children_per_line=2, max_children_per_line=4, column_spacing=8, row_spacing=8)
        self.values: dict[str, Gtk.Label] = {}
        for key, label in (
            ("sessions", _("Sessions")),
            ("total", _("Time played")),
            ("average", _("Average session length")),
            ("last", _("Last connection")),
        ):
            tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            tile.add_css_class("brp-metric")
            name = Gtk.Label(label=label, xalign=0, wrap=True)
            name.add_css_class("caption")
            name.add_css_class("dim-label")
            value = Gtk.Label(label="—", xalign=0, wrap=True)
            value.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            value.add_css_class("heading")
            tile.append(name)
            tile.append(value)
            tiles.append(tile)
            tile.get_parent().set_focusable(False)
            self.values[key] = value
        content.append(tiles)
        self.clear_row = Gtk.Button(label=_("Clear history"), halign=Gtk.Align.END)
        self.clear_row.add_css_class("flat")
        self.clear_row.add_css_class("destructive-action")
        self.clear_row.connect("clicked", lambda _button: self._confirm_clear())
        content.append(self.clear_row)
        self.stack.add_named(content, "chart")
        self.add(self.stack)
        self.connect("map", lambda *_args: self.refresh())
        self.connect("unrealize", lambda *_args: self._worker.cancel())

    def refresh(self) -> None:
        if self.summary is None:
            self.stack.set_visible_child_name("loading")
        clock = self._clock
        self._worker.submit(lambda: self.history.summary(now=clock() if clock else None), self._show, failed=lambda _error: self._show(None))

    def _show(self, summary: HistorySummary | None) -> None:
        self.summary = summary
        if summary is None or not summary.has_history:
            self.stack.set_visible_child_name("empty")
            self.clear_row.set_visible(False)
            return
        self.clear_row.set_visible(True)
        self.bars.show_summary(summary)
        self.values["sessions"].set_label(str(summary.sessions))
        self.values["total"].set_label(format_duration(summary.total_seconds) if summary.sessions else "—")
        self.values["average"].set_label(format_duration(summary.average_seconds) if summary.average_seconds is not None else "—")
        self.values["last"].set_label(last_connection_text(summary.last_connection, now=self._clock() if self._clock else None))
        self.stack.set_visible_child_name("chart")

    def _confirm_clear(self) -> None:
        confirm(
            self,
            _("Clear the connection history?"),
            _("Every saved connection is removed from this computer. Paired devices and network settings are not affected."),
            _("Clear history"),
            lambda: self._worker.submit(self.history.clear, lambda _value: self.refresh(), keep_previous=True),
        )


__all__ = ["DayBars", "ShareHistoryCard", "last_connection_text", "weekday_label"]
