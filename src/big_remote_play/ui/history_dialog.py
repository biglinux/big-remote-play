"""History: streaming sessions that really started, kept only on this computer."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # type: ignore

from big_remote_play.private_network.history import RETENTION_CHOICES, SessionHistory, SessionRecord
from big_remote_play.utils.i18n import _

from .components import content_dialog, note
from .network_common import RowGroup, Worker, confirm, loading_row, message_row

PROVIDER_NAMES = {"tailscale": "Tailscale", "headscale": "Headscale", "zerotier": "ZeroTier"}


def provider_name(provider: str) -> str:
    return PROVIDER_NAMES.get(provider) or {"lan": _("Local network"), "manual": _("Address typed by hand")}.get(provider, provider)


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return _("Duration unknown")
    minutes = int(round(seconds / 60))
    if minutes < 1:
        return _("Less than a minute")
    hours, rest = divmod(minutes, 60)
    if hours:
        return _("{hours} h {minutes} min").format(hours=hours, minutes=rest)
    return _("{minutes} min").format(minutes=minutes)


def day_label(day: date, today: date | None = None) -> str:
    today = today or date.today()
    if day == today:
        return _("Today")
    if (today - day).days == 1:
        return _("Yesterday")
    return day.strftime("%x")


def session_title(record: SessionRecord) -> str:
    return record.host_name or record.host_address


def session_subtitle(record: SessionRecord) -> str:
    start = datetime.fromtimestamp(record.started_at).strftime("%H:%M")
    span = f"{start} – {datetime.fromtimestamp(record.ended_at).strftime('%H:%M')}" if record.ended_at else start
    parts = [provider_name(record.provider), span, format_duration(record.duration_seconds)]
    requested = " · ".join(value for value in (record.resolution, f"{record.fps} FPS" if record.fps else "", record.codec) if value)
    if requested:
        parts.append(_("Requested: {settings}").format(settings=requested))
    return " · ".join(part for part in parts if part)


class HistoryDialog:
    def __init__(self, parent: Gtk.Widget, history: SessionHistory, *, on_reconnect: Callable[[SessionRecord], None] | None = None, show_toast: Callable[[str], None] | None = None) -> None:
        self.parent = parent
        self.history = history
        self.on_reconnect = on_reconnect
        self.show_toast = show_toast or (lambda _message: None)
        self.worker = Worker()
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content.append(note(_("Only on this computer. Nothing is sent anywhere, and no password, key or code is stored."), "brp-document-open-recent-symbolic"))

        settings = Adw.PreferencesGroup()
        self.retention = Adw.ComboRow(title=_("Keep history"), use_markup=False)
        self.retention.set_model(Gtk.StringList.new([_("30 days"), _("90 days"), _("Always")]))
        self._syncing = True
        self.retention.connect("notify::selected", self._on_retention)
        settings.add(self.retention)
        clear = Adw.ButtonRow(title=_("Clear history"))
        clear.add_css_class("destructive-action")
        clear.connect("activated", lambda _row: self._confirm_clear())
        settings.add(clear)
        content.append(settings)

        self.sessions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content.append(self.sessions)
        self.dialog = content_dialog(_("History"), content, description=_("Connections from this computer that really started streaming."), width=640, height=620)
        self.dialog.connect("closed", lambda *_args: self.worker.close())

    def present(self) -> None:
        self.dialog.present(self.parent)
        self.refresh()

    def refresh(self) -> None:
        self._set_sessions([self._group(None, [loading_row(_("Loading history…"))])])
        self.worker.submit(lambda: (self.history.sessions(), self.history.retention_days), self._apply)

    def _group(self, title: str | None, rows: list[Gtk.Widget]) -> Adw.PreferencesGroup:
        group = RowGroup(title=GLib.markup_escape_text(title) if title else "")
        group.replace(rows)
        return group

    def _set_sessions(self, groups: list[Gtk.Widget]) -> None:
        while child := self.sessions.get_first_child():
            self.sessions.remove(child)
        for group in groups:
            self.sessions.append(group)

    def _apply(self, loaded) -> None:
        records, retention = loaded
        self._syncing = True
        self.retention.set_selected(RETENTION_CHOICES.index(retention) if retention in RETENTION_CHOICES else 1)
        self._syncing = False
        if not records:
            self._set_sessions([self._group(None, [message_row(_("No connections yet"), _("Connections appear here after a stream starts."), "brp-document-open-recent-symbolic")])])
            return
        groups: dict[date, list[Gtk.Widget]] = {}
        for record in records:
            groups.setdefault(datetime.fromtimestamp(record.started_at).date(), []).append(self._row(record))
        self._set_sessions([self._group(day_label(day), rows) for day, rows in groups.items()])

    def _row(self, record: SessionRecord) -> Adw.ActionRow:
        row = Adw.ActionRow(title=session_title(record), subtitle=session_subtitle(record), use_markup=False)
        row.set_subtitle_lines(0)
        reconnect = self.on_reconnect
        if reconnect is not None:
            button = Gtk.Button(label=_("Connect again"), valign=Gtk.Align.CENTER)
            button.update_property([Gtk.AccessibleProperty.DESCRIPTION], [_("Connect to {name} again").format(name=session_title(record))])

            def on_clicked(_button, item=record) -> None:
                self.dialog.close()
                reconnect(item)

            button.connect("clicked", on_clicked)
            row.add_suffix(button)
        return row

    def _on_retention(self, *_args) -> None:
        if self._syncing:
            return
        days = RETENTION_CHOICES[self.retention.get_selected()]
        self.worker.submit(lambda: self.history.set_retention(days), lambda _value: self.refresh(), keep_previous=True)

    def _cleared(self, _value: object) -> None:
        self.show_toast(_("History cleared"))
        self.refresh()

    def _confirm_clear(self) -> None:
        confirm(
            self.dialog,
            _("Clear the connection history?"),
            _("Every saved connection is removed from this computer. Paired devices and network settings are not affected."),
            _("Clear history"),
            lambda: self.worker.submit(self.history.clear, self._cleared, keep_previous=True),
        )
