"""Share → Preferences → Touch controls.

What a phone's fingers do on this computer (mouse, direct touch or the Big WM
Virtual Keyboard's on-screen controller), the controller's buttons and the
checks that say whether it is ready. The decisions and checks live in
``host/touch_control.py``; this module only shows them. Blocking work (D-Bus,
``/sys``) runs on a worker; results are applied on the GTK main loop and
dropped when stale.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # type: ignore

from big_remote_play.host import touch_control as backend
from big_remote_play.utils.i18n import _

from .components import preferences_dialog, set_row_icon
from .network_common import Worker


def mode_label(mode: str) -> str:
    return {
        backend.AUTOMATIC: _("Automatic"),
        backend.MOUSE: _("Mouse"),
        backend.TOUCH: _("Direct touch"),
        backend.GAMEPAD: _("On-screen controller"),
        backend.HYBRID: _("Controller and touch"),
    }[backend.normalize_mode(mode)]


def mode_description(mode: str) -> str:
    return {
        backend.AUTOMATIC: _("Sunshine's own setting decides. While the on-screen controller is open, the phone's fingers press its buttons."),
        backend.MOUSE: _("Fingers move the pointer and click. Sunshine does not offer touch to the phone."),
        backend.TOUCH: _("Each finger touches this computer's screen, where the desktop accepts touch."),
        backend.GAMEPAD: _("Fingers press the on-screen controller's buttons, several at once. Nothing becomes a mouse click."),
        backend.HYBRID: _("Fingers on the controller press its buttons; elsewhere they touch the desktop."),
    }[backend.normalize_mode(mode)]


def check_text(check: backend.Check) -> tuple[str, str]:
    """Title and words for one check (state in words, not only an icon)."""
    ok, outcome = check.outcome == backend.OK, check.outcome
    if check.key == "sunshine_touch":
        title = _("Touch from the phone (Sunshine)")
        if outcome == backend.OK:
            return title, _("Turned on")
        if outcome == backend.FAIL:
            return title, _("Off. It is turned on the next time sharing starts.")
        if outcome == backend.WARN:
            return title, _("On. It is turned off the next time sharing starts.")
        return title, _("Off: fingers work as a mouse")
    if check.key == "touchscreen":
        title = _("Phone's touch on this computer")
        return title, (_("Present ({device})").format(device=check.detail) if ok else _("Appears when a phone that sends touch starts playing"))
    if check.key == "desktop_touch":
        title = _("Desktop")
        return title, (_("Accepts touch") if ok else _("This desktop ({desktop}) does not accept touch. Use the on-screen controller.").format(desktop=check.detail))
    if check.key == "controller":
        title = _("On-screen controller")
        if ok:
            return title, _("Big WM Virtual Keyboard found")
        return title, _("Install Big WM Virtual Keyboard (package {package})").format(package="big-keyboard")
    if check.key == "controller_version":
        return _("On-screen controller"), _("Update Big WM Virtual Keyboard: this version cannot read touch from the phone")
    if check.key == "uinput":
        title = _("Virtual controller")
        return title, (_("Games can see it") if ok else _("No access to {device}. Install Sunshine's udev rules or steam-devices.").format(device="/dev/uinput"))
    if check.key == "game_window":
        return _("Game Window"), _("The phone sees only the game, not this controller. Use Moonlight's own on-screen controls.")
    if check.key == "remote_state":
        title = _("Controller receiving touch")
        if ok:
            return title, _("Yes")
        if check.detail == "waiting":
            return title, _("Waiting for a phone that sends touch")
        return title, _("Not receiving touch ({reason})").format(reason=check.detail)
    return check.key, check.detail


_ICONS = {backend.OK: "brp-emblem-ok-symbolic", backend.WARN: "dialog-warning-symbolic", backend.FAIL: "dialog-error-symbolic", backend.INFO: "brp-dialog-information-symbolic"}


class TouchControls:
    """The sheet, its settings and the controller's lifetime while sharing.

    ``context`` returns the facts only the host view knows, on the main
    thread: ``{"sharing", "game_window", "output", "sunshine_config"}``.
    """

    def __init__(self, *, on_changed: Callable[[], None], context: Callable[[], Mapping[str, object]], keyboard_factory: Callable[[], backend.BigKeyboard] = backend.BigKeyboard) -> None:
        self._on_changed = on_changed
        self._context = context
        self._keyboard_factory = keyboard_factory
        self._worker = Worker()
        self._actions = Worker()
        self._loading = False
        self._connected = 0
        self.dialog: Adw.PreferencesDialog | None = None
        self._build()

    # ── widgets ────────────────────────────────────────────────────────────
    def _build(self) -> None:
        self.mode_group = Adw.PreferencesGroup(description=_("How fingers on a phone or tablet control this computer. Moonlight's settings on the phone are not changed."))
        self.mode_row = Adw.ComboRow(title=_("What touch does"), use_markup=False)
        self.mode_row.set_model(Gtk.StringList.new([mode_label(mode) for mode in backend.MODES]))
        self.mode_row.set_subtitle_lines(0)
        set_row_icon(self.mode_row, "brp-phone-symbolic")
        self.mode_row.connect("notify::selected", self._on_mode)
        self.mode_group.add(self.mode_row)

        self.controller_group = Adw.PreferencesGroup(title=_("On-screen controller"), description=_("Big WM Virtual Keyboard draws it on the shared screen; games see an ordinary controller."))
        self.open_row = Adw.SwitchRow(title=_("Open when a device starts playing"), use_markup=False)
        self.open_row.set_active(True)
        self.open_row.connect("notify::active", lambda *_args: None if self._loading else self._on_changed())
        self.controller_group.add(self.open_row)
        # Wraps at compact widths instead of widening the sheet.
        buttons = Adw.WrapBox(child_spacing=8, line_spacing=8, margin_top=12)
        self.buttons: dict[str, Gtk.Button] = {}
        for key, label in (("open", _("Open")), ("close", _("Close")), ("edit", _("Edit layout")), ("release", _("Release all buttons"))):
            button = Gtk.Button(label=label)
            button.connect("clicked", lambda _button, action=key: self.run_action(action))
            buttons.append(button)
            self.buttons[key] = button
        self.buttons["open"].add_css_class("suggested-action")
        self.controller_group.add(buttons)
        # Missing program: installed in place by the shared installer, never in a terminal.
        self.install_button = Gtk.Button(label=_("Install"), valign=Gtk.Align.CENTER, visible=False)
        self.install_button.add_css_class("suggested-action")
        self.install_button.connect("clicked", lambda _button: self._install())
        self.controller_group.set_header_suffix(self.install_button)

        self.checks_group = Adw.PreferencesGroup(title=_("Compatibility"), description=_("Checked on this computer now."))
        refresh = Gtk.Button(label=_("Check again"), valign=Gtk.Align.CENTER)
        refresh.connect("clicked", lambda _button: self.refresh())
        self.checks_group.set_header_suffix(refresh)
        self.summary_row = Adw.ActionRow(title=_("Checking…"), use_markup=False)
        self.summary_row.set_accessible_role(Gtk.AccessibleRole.STATUS)
        self.checks_group.add(self.summary_row)
        self._check_rows: list[Adw.ActionRow] = []

        self.phone_group = Adw.PreferencesGroup(title=_("Moonlight on the phone"))
        for title, subtitle in (
            (
                _("Moonlight's on-screen controls"),
                _("Works with the official Moonlight: turn on Show on-screen controls in its settings. The buttons are drawn on the phone and several work at once."),
            ),
            (
                _("This computer's controller on the phone"),
                _(
                    "Needs a Moonlight that sends touch; the official Android app does not yet. Install Big Remote Play Controller, the Big Remote Play build, and turn on its setting Send touch to the computer."
                ),
            ),
            (_("One controller at a time"), _("With both, the game sees two controllers. Close the one you are not using.")),
        ):
            row = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False)
            row.set_subtitle_lines(0)
            set_row_icon(row, "brp-input-gaming-symbolic")
            self.phone_group.add(row)
        self._sync_mode_widgets()

    def present(self, parent: Gtk.Widget) -> None:
        dialog = self.dialog
        if dialog is None:
            dialog = self.dialog = preferences_dialog(_("Touch controls"), [self.mode_group, self.controller_group, self.checks_group, self.phone_group], height=680)
            dialog.connect("closed", lambda *_args: self._worker.cancel())
        dialog.present(parent)
        self.refresh()

    # ── settings ───────────────────────────────────────────────────────────
    @property
    def mode(self) -> str:
        index = self.mode_row.get_selected()
        return backend.MODES[index] if 0 <= index < len(backend.MODES) else backend.DEFAULT_MODE

    def load(self, settings: Mapping[str, object]) -> None:
        self._loading = True
        try:
            self.mode_row.set_selected(backend.MODES.index(backend.normalize_mode(settings.get("touch_mode"))))
            self.open_row.set_active(settings.get("touch_open_on_connect") is not False)
        finally:
            self._loading = False
        self._sync_mode_widgets()

    def values(self) -> dict[str, object]:
        return {"touch_mode": self.mode, "touch_open_on_connect": self.open_row.get_active()}

    def _on_mode(self, *_args) -> None:
        self._sync_mode_widgets()
        if not self._loading:
            self._on_changed()
            self.refresh()

    def _sync_mode_widgets(self) -> None:
        self.mode_row.set_subtitle(mode_description(self.mode))
        self.open_row.set_visible(backend.uses_controller(self.mode))

    # ── checks ─────────────────────────────────────────────────────────────
    def refresh(self) -> None:
        context = dict(self._context())
        mode = self.mode
        raw = context.get("sunshine_config")
        sunshine_config = {str(key): str(value) for key, value in raw.items()} if isinstance(raw, Mapping) else {}
        self.summary_row.set_title(_("Checking…"))

        def work():
            facts = backend.gather(mode, sunshine_config, sharing=bool(context.get("sharing")), game_window=bool(context.get("game_window")), keyboard=self._keyboard_factory())
            return facts, backend.checks(facts)

        self._worker.submit(work, self._show_checks, failed=lambda error: self.summary_row.set_title(_("Could not check: {error}").format(error=error)))

    def _show_checks(self, result) -> None:
        facts, checks = result
        for row in self._check_rows:
            self.checks_group.remove(row)
        self._check_rows = []
        for check in checks:
            title, subtitle = check_text(check)
            row = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False)
            row.set_subtitle_lines(0)
            set_row_icon(row, _ICONS.get(check.outcome, "brp-dialog-information-symbolic"))
            self.checks_group.add(row)
            self._check_rows.append(row)
        ready = backend.ready(checks)
        self.summary_row.set_title(_("Ready to play") if ready else _("Not ready yet"))
        set_row_icon(self.summary_row, "brp-emblem-ok-symbolic" if ready else "dialog-warning-symbolic")
        controller = facts.controller
        self.install_button.set_visible(not controller.installed)
        usable = controller.installed and (not controller.running or controller.supports_remote_touch)
        for key in ("open", "close", "edit", "release"):
            self.buttons[key].set_sensitive(usable)

    def _install(self) -> None:
        from .dependency_installer import InstallDialog

        dialog = InstallDialog(
            ("big_keyboard",),
            title=_("Let's get this computer ready"),
            description=_("For the on-screen controller, Big Remote Play installs one more program after asking for your password once."),
            on_ready=self.refresh,
        )
        dialog.present(self.dialog or self.mode_row)

    # ── the controller ─────────────────────────────────────────────────────
    def run_action(self, action: str) -> None:
        """A button or the sharing lifecycle: open, close, edit or release."""
        context = dict(self._context())
        mode = self.mode
        output = str(context.get("output") or "")
        keyboard = self._keyboard_factory()

        def work():
            if action == "release":
                if keyboard.running():
                    keyboard.release()
                return None
            if action == "close":
                if keyboard.running():
                    keyboard.release()
                    keyboard.show(False)
                return None
            if not keyboard.start():
                raise RuntimeError(_("Big WM Virtual Keyboard did not start"))
            keyboard.configure(remote_touch=backend.remote_touch_mode(mode), output=output)
            if action == "edit":
                keyboard.edit()
            else:
                keyboard.show(True)
            return None

        def failed(error: BaseException) -> None:
            self.summary_row.set_title(_("Could not use the on-screen controller: {error}").format(error=error))

        self._actions.submit(work, lambda _result: self.refresh() if self.dialog is not None and self.dialog.get_mapped() else None, failed=failed, keep_previous=True)

    def on_connections(self, count: int) -> None:
        """Devices playing now (from the live monitor, main loop).

        First device in a controller mode → open the controller (when chosen).
        Last device gone → release every button: Sunshine keeps the phone's
        touches held after a dropped connection, so the controller must let go.
        """
        before, self._connected = self._connected, count
        if count and not before and backend.uses_controller(self.mode) and self.open_row.get_active():
            self.run_action("open")
        elif before and not count and backend.uses_controller(self.mode):
            self.run_action("close")
        elif count < before:
            self.run_action("release")

    def close(self) -> None:
        self._worker.close()
        self._actions.close()
