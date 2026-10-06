"""Share → Preferences → Host input priority, and its live state while sharing.

The setting, the one row in **Connected now** that says who has the mouse
and keyboard, and the helper process (``host/input_priority.py``) that does
the work. Helper messages arrive on a reader thread and are applied on the
GTK main loop; results for a helper that was replaced or stopped are dropped.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import logging
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # type: ignore

from big_remote_play.host import input_priority as backend
from big_remote_play.utils.i18n import _, ngettext

from .components import set_row_icon

_log = logging.getLogger("big-remoteplay")

# Several transitions in a row (someone tapping a key every few seconds) show
# one on-screen message, not a stream of them.
OSD_MIN_INTERVAL = 4.0


def delay_label(seconds: int) -> str:
    return ngettext("{count} second", "{count} seconds", seconds).format(count=seconds)


def unavailable_text(reason: str) -> str:
    if reason in ("permission", "not-authorized"):
        return _("Big Remote Play is not allowed to notice when this computer's mouse and keyboard are used. Installing Big Remote Play from its package fixes this.")
    if reason == "no-devices":
        return _("Sunshine's mouse and keyboard were not found. Stop and start sharing again.")
    return _("It could not start. Stop and start sharing again.")


class HostInputPriority:
    """The preference rows, the status row and the helper's lifetime."""

    def __init__(self, *, on_changed: Callable[[], None], process_factory: Callable[..., backend.PriorityProcess] = backend.PriorityProcess) -> None:
        self._on_changed = on_changed
        self._process_factory = process_factory
        self._process: backend.PriorityProcess | None = None
        self._generation = 0
        self._loading = False
        self._hosting = False
        self._source = ""
        self.state = "off"  # off | starting | guest | host | unavailable
        self.reason = ""
        self._remote_devices = -1
        self._last_osd = 0.0
        self._build()

    # ── widgets ────────────────────────────────────────────────────────────
    def _build(self) -> None:
        self.group = Adw.PreferencesGroup(title=_("Host input priority"), description=_("When you use this computer's mouse or keyboard, guests temporarily stop controlling them."))
        self.switch_row = Adw.SwitchRow(title=_("Host has priority"), use_markup=False)
        self.switch_row.set_subtitle(_("Controllers are never paused."))
        self.switch_row.set_subtitle_lines(0)
        set_row_icon(self.switch_row, "brp-input-keyboard-symbolic")
        self.switch_row.connect("notify::active", self._on_switch)
        self.group.add(self.switch_row)

        self.delay_row = Adw.ComboRow(title=_("Guest control resumes after"), use_markup=False)
        self.delay_row.set_title_lines(0)
        # A subtitle would squeeze the chosen value ("5 seconds") on narrow windows.
        self.delay_row.set_tooltip_text(_("Counted from the last time this computer's mouse or keyboard was used."))
        self.delay_row.update_property([Gtk.AccessibleProperty.DESCRIPTION], [_("Counted from the last time this computer's mouse or keyboard was used.")])
        self.delay_row.set_model(Gtk.StringList.new([delay_label(value) for value in backend.DELAY_CHOICES]))
        self.delay_row.set_selected(backend.DELAY_CHOICES.index(backend.DEFAULT_DELAY))
        self.delay_row.connect("notify::selected", self._on_delay)
        self.group.add(self.delay_row)

        self.problem_row = Adw.ActionRow(title=_("Host input priority is not working"), use_markup=False)
        self.problem_row.set_subtitle_lines(0)
        set_row_icon(self.problem_row, "dialog-warning-symbolic")
        self.problem_row.set_visible(False)
        self.group.add(self.problem_row)

        # Lives in Share → Overview → Connected now, below the devices.
        self.status_row = Adw.ActionRow(use_markup=False)
        self.status_row.set_subtitle_lines(0)
        set_row_icon(self.status_row, "brp-input-keyboard-symbolic")
        self.status_row.set_accessible_role(Gtk.AccessibleRole.STATUS)
        self.status_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, margin_top=12)
        self.status_box.add_css_class("boxed-list")
        self.status_box.add_css_class("brp-boxed")
        self.status_box.append(self.status_row)
        self.status_box.set_visible(False)
        self._sync_rows()

    # ── settings ───────────────────────────────────────────────────────────
    @property
    def enabled(self) -> bool:
        return self.switch_row.get_active()

    @property
    def delay(self) -> int:
        index = self.delay_row.get_selected()
        return backend.DELAY_CHOICES[index] if 0 <= index < len(backend.DELAY_CHOICES) else backend.DEFAULT_DELAY

    def load(self, settings: Mapping[str, object]) -> None:
        """Off by default: an update never changes how input behaved before."""
        self._loading = True
        try:
            self.switch_row.set_active(settings.get("input_priority") is True)
            saved = backend.clamp_delay(settings.get("input_priority_delay", backend.DEFAULT_DELAY))
            nearest = min(backend.DELAY_CHOICES, key=lambda value: abs(value - saved))
            self.delay_row.set_selected(backend.DELAY_CHOICES.index(nearest))
        finally:
            self._loading = False
        self._sync_rows()
        self._apply()

    def values(self) -> dict[str, object]:
        return {"input_priority": self.enabled, "input_priority_delay": self.delay}

    def _on_switch(self, *_args) -> None:
        self._sync_rows()
        if not self._loading:
            self._on_changed()
            self._apply()

    def _on_delay(self, *_args) -> None:
        if self._loading:
            return
        self._on_changed()
        if self._process is not None:
            self._process.set_delay(self.delay)
        self._sync_rows()

    # ── sharing ────────────────────────────────────────────────────────────
    def sync(self, *, hosting: bool, source: str) -> None:
        """Called when sharing starts, stops or adopts a running server."""
        self._hosting = hosting
        self._source = source
        self._apply()

    def _apply(self) -> None:
        wanted = self._hosting and self.enabled
        if wanted and self._process is None:
            self._start()
        elif not wanted and self._process is not None:
            self._stop()
        self._sync_rows()

    def _start(self) -> None:
        self._generation += 1
        generation = self._generation
        self.state, self.reason, self._remote_devices = "starting", "", -1

        def on_event(message: Mapping[str, object]) -> None:
            GLib.idle_add(self._on_event, generation, dict(message))

        self._process = self._process_factory(self.delay, on_event)
        _log.info("Host input priority: on (%d s)", self.delay)
        self._process.start()

    def _stop(self) -> None:
        process, self._process = self._process, None
        self._generation += 1
        self.state, self.reason = "off", ""
        if process is not None:
            process.stop()
            _log.info("Host input priority: off")

    def close(self) -> None:
        """The window is closing: the other device gets full control back."""
        self._hosting = False
        self._stop()

    # ── helper messages (GTK main loop) ────────────────────────────────────
    def _on_event(self, generation: int, message: dict) -> bool:
        if generation != self._generation:
            return False  # from a helper that was stopped or replaced
        event = message.get("event")
        if event == "ready":
            self.state = "guest"
            self._remote_devices = int(message.get("remote") or 0)
            _log.info("Host input priority: ready (%d Sunshine input device(s), %d local)", self._remote_devices, int(message.get("local") or 0))
        elif event == "devices":
            self._remote_devices = int(message.get("remote") or 0)
        elif event == "paused":
            self.state = "host"
            _log.info("Host input priority: local input detected; guest keyboard/mouse suspended for %d s", self.delay)
            self._show_osd(_("Mouse and keyboard are being used on the host PC"))
        elif event == "resumed":
            if self.state == "host":
                _log.info("Host input priority: guest input restored after local inactivity")
                self._show_osd(_("You can control the mouse and keyboard again"))
            self.state = "guest"
        elif event == "unavailable":
            self.state, self.reason = "unavailable", str(message.get("reason") or "")
            _log.warning("Host input priority: not available (%s)", self.reason)
        elif event == "stopped":
            self._process = None
            if self.state != "unavailable":
                # Ended without being asked (crash): the kernel already gave
                # the devices back. Say so instead of claiming it still works.
                self.state, self.reason = "unavailable", "start-failed"
                _log.warning("Host input priority: the helper ended (exit %s)", message.get("code"))
        self._sync_rows()
        return False

    def _show_osd(self, text: str) -> None:
        """Full Desktop on KDE: the message is drawn on the shared screen, so
        the other person sees it in their stream too. Game Window shows only
        the game, so nothing is drawn there."""
        now = time.monotonic()
        if self._source != "desktop" or now - self._last_osd < OSD_MIN_INTERVAL:
            return
        self._last_osd = now
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call(
                "org.kde.plasmashell",
                "/org/kde/osdService",
                "org.kde.osdService",
                "showText",
                GLib.Variant("(ss)", ("input-keyboard", text)),
                None,
                Gio.DBusCallFlags.NO_AUTO_START,
                1000,
                None,
                None,
            )
        except GLib.Error:
            pass  # not KDE Plasma: the row in Share still says it

    # ── words ──────────────────────────────────────────────────────────────
    def _sync_rows(self) -> None:
        self.delay_row.set_visible(self.enabled)
        problem = self.enabled and self.state == "unavailable"
        self.problem_row.set_visible(problem)
        if problem:
            self.problem_row.set_subtitle(unavailable_text(self.reason))
        self.status_box.set_visible(self._hosting and self.enabled)
        if self.state == "host":
            title = _("You're controlling this PC")
            subtitle = ngettext(
                "Guests get the mouse and keyboard back {count} second after you stop using them.",
                "Guests get the mouse and keyboard back {count} seconds after you stop using them.",
                self.delay,
            ).format(count=self.delay)
        elif self.state == "guest" and self._remote_devices == 0:
            title, subtitle = _("Host has priority"), _("Waiting for Sunshine's mouse and keyboard…")
        elif self.state == "guest":
            title, subtitle = _("Guests can use the mouse and keyboard"), _("Using this computer's own mouse or keyboard pauses them.")
        elif self.state == "unavailable":
            title, subtitle = _("Host input priority is not working"), unavailable_text(self.reason)
        else:
            title, subtitle = _("Host has priority"), _("Starting…")
        self.status_row.set_title(title)
        self.status_row.set_subtitle(subtitle)
        self.status_row.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION], [title, subtitle])


__all__ = ["HostInputPriority", "delay_label", "unavailable_text"]
