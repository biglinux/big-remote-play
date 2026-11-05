"""Share → Support: check this computer, then solve a common problem.

The checks read real facts on a worker (see :mod:`big_remote_play.host.health_checks`)
only when Support is opened or *Check again* is pressed: nothing runs in the
background while the page is hidden. Every result is stated in words; a
button appears only for a safe action, and nothing is repaired by itself.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # type: ignore

from big_remote_play.host import health_checks as hc
from big_remote_play.utils.i18n import _

from .components import set_row_icon
from .network_common import Worker

_STATE_ICONS = {
    hc.OK: "brp-emblem-ok-symbolic",
    hc.WARNING: "dialog-warning-symbolic",
    hc.PROBLEM: "dialog-warning-symbolic",
    hc.INFO: "brp-dialog-information-symbolic",
}
_STATE_TONES = {hc.OK: "online", hc.WARNING: "attention", hc.PROBLEM: "attention", hc.INFO: "offline"}


@dataclass(frozen=True)
class Problem:
    key: str
    title: str
    steps: tuple[str, ...]
    checks: tuple[str, ...]  # the related rows of Check this computer
    actions: tuple[tuple[str, str], ...] = ()  # (action id, button label)


def problems() -> tuple[Problem, ...]:
    """Common problems in everyday words, each with what to try in order."""
    return (
        Problem(
            "connect",
            _("I cannot connect"),
            (
                _("Both computers must be on the same network, or on the same private network (Tailscale, Headscale or ZeroTier)."),
                _("Sharing must be on here: the button on Overview says Stop sharing."),
                _("If the other computer does not list this one, use “This PC did not appear in the list?” on Overview."),
            ),
            ("sharing", "firewall"),
            ((hc.ACTION_START, _("Start sharing")), (hc.ACTION_ALLOW_FIREWALL, _("Allow in firewall"))),
        ),
        Problem(
            "pin_missing",
            _("The PIN does not appear"),
            (
                _("On the other device, open Moonlight and choose this computer: Moonlight shows the PIN there, never here."),
                _("Moonlight pairs only when nobody is playing. If someone is, end the stream for everyone first."),
                _("When a device asks to pair, Big Remote Play opens the request by itself. You can also type the PIN on Overview."),
            ),
            ("sharing", "keyring"),
            ((hc.ACTION_PAIR, _("Type a PIN")),),
        ),
        Problem(
            "pin_refused",
            _("The PIN is not accepted"),
            (
                _("Type the four digits exactly as Moonlight shows them, then press Enter."),
                _("After a wrong PIN, Moonlight shows a new one: start pairing again there and type the new PIN."),
                _("If Sunshine rejected the saved password, enter the current one first."),
            ),
            ("keyring",),
            ((hc.ACTION_ENTER_PASSWORD, _("Enter password")), (hc.ACTION_PAIR, _("Type a PIN"))),
        ),
        Problem(
            "no_picture",
            _("There is no picture"),
            (
                _("Choose another screen in What to share, or Automatic."),
                _("In Preferences → Picture and performance, set Capture method to Automatic."),
                _("The server log says why a capture could not open."),
            ),
            ("session", "picture"),
            ((hc.ACTION_OPEN_LOG, _("Open server log")),),
        ),
        Problem(
            "no_sound",
            _("There is no sound"),
            (
                _("Preferences → Sound has Test, which plays a tone and checks that it reaches the shared sound."),
                _("On the other device, check Moonlight's volume and that its audio is not muted."),
            ),
            (),
            ((hc.ACTION_TEST_AUDIO, _("Test sound")),),
        ),
        Problem(
            "controller",
            _("My controller does not work"),
            (
                _("Connect the controller to the other device before starting, and keep Moonlight's window in front."),
                _("A controller plugged into this computer is found first by many games: unplug it while the other person plays."),
            ),
            ("input",),
        ),
        Problem(
            "pointer",
            _("The mouse pointer is missing"),
            (
                _("If the pointer moves things but is not visible, the screen capture did not draw it. Check this computer shows whether another capture method draws it."),
                _(
                    "On a phone the only pointer is the one drawn into the picture. In an X11 session Big Remote Play uses X11 screen capture, which draws it; after changing Capture Method, start sharing again."
                ),
                _("In Moonlight on a computer, Ctrl+Alt+Shift+N asks Sunshine to draw the pointer again; pressed by accident, it hides it."),
                _("With Moonlight's “Optimize mouse for remote desktop”, Ctrl+Alt+Shift+C shows the other device's own pointer."),
                _("If the pointer does not move at all, the remote mouse cannot reach this computer: see the row about mouse, keyboard and controllers."),
            ),
            ("pointer", "input", "session"),
            ((hc.ACTION_FIX_POINTER, _("Fix pointer")),),
        ),
        Problem(
            "slow",
            _("The connection is slow"),
            (
                _("Use a cable or 5 GHz Wi-Fi on both computers when possible."),
                _("On the other device, lower the resolution or the video bitrate in Moonlight."),
                _("Connection monitoring below shows the measured response time of each device."),
            ),
            ("picture",),
        ),
    )


class SupportCenter(Gtk.Box):
    """Check this computer and Common problems, at the top of Support."""

    __gtype_name__ = "BrpSupportCenter"

    def __init__(self, *, gather: Callable[[], hc.Facts], actions: dict[str, Callable[[], object]]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        self._gather = gather
        self._actions = actions
        self._worker = Worker()
        self._facts: hc.Facts | None = None
        self._rows: list[Gtk.Widget] = []

        self.checks_group = Adw.PreferencesGroup(title=_("Check this computer"), description=_("Read-only checks. Nothing is changed unless you press a button."))
        self.recheck_button = Gtk.Button(label=_("Check again"), valign=Gtk.Align.CENTER)
        self.recheck_button.add_css_class("flat")
        self.recheck_button.connect("clicked", lambda _button: self.refresh())
        self.checks_spinner = Adw.Spinner(visible=False, valign=Gtk.Align.CENTER)
        suffix = Gtk.Box(spacing=8)
        suffix.append(self.checks_spinner)
        suffix.append(self.recheck_button)
        self.checks_group.set_header_suffix(suffix)
        self.checks_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.checks_list.add_css_class("boxed-list")
        self.checks_list.add_css_class("brp-boxed")
        self.checks_list.set_accessible_role(Gtk.AccessibleRole.LIST)
        self.checks_group.add(self.checks_list)
        self._placeholder(_("Open this page to check"), _("The checks run when Support is shown."))
        self.append(self.checks_group)

        self.problems_group = Adw.PreferencesGroup(title=_("Common problems"), description=_("Choose what happens to see what to try, with this computer's current state."))
        for problem in problems():
            row = Adw.ActionRow(title=problem.title, activatable=True, use_markup=False)
            row.set_title_lines(0)
            row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            row.connect("activated", lambda _row, item=problem: self.open_problem(item))
            self.problems_group.add(row)
        self.append(self.problems_group)

    # ── checks ────────────────────────────────────────────────────────────
    def refresh(self) -> None:
        self.recheck_button.set_sensitive(False)
        self.checks_spinner.set_visible(True)
        self._worker.submit(self._gather, self._show, failed=self._failed)

    def cancel(self) -> None:
        self._worker.cancel()
        self.recheck_button.set_sensitive(True)
        self.checks_spinner.set_visible(False)

    def close(self) -> None:
        self._worker.close()

    def _failed(self, _error: BaseException) -> None:
        self.recheck_button.set_sensitive(True)
        self.checks_spinner.set_visible(False)
        self._placeholder(_("The checks could not run"), _("Press Check again. If it keeps failing, the details are in the application log."))

    def _clear(self) -> None:
        while child := self.checks_list.get_first_child():
            self.checks_list.remove(child)

    def _placeholder(self, title: str, subtitle: str) -> None:
        self._clear()
        row = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False)
        row.set_subtitle_lines(0)
        self.checks_list.append(row)

    def _show(self, facts: hc.Facts) -> None:
        self._facts = facts
        self.recheck_button.set_sensitive(True)
        self.checks_spinner.set_visible(False)
        self._clear()
        for check in hc.checks(facts):
            self.checks_list.append(self._check_row(check))

    def _check_row(self, check: hc.Check) -> Adw.ActionRow:
        row = Adw.ActionRow(title=check.title, subtitle=check.detail, use_markup=False)
        row.set_title_lines(0)
        row.set_subtitle_lines(0)
        set_row_icon(row, _STATE_ICONS.get(check.state, "brp-dialog-information-symbolic"))
        state = Gtk.Label(label=hc.state_label(check.state), valign=Gtk.Align.CENTER)
        state.add_css_class("state-pill")
        state.add_css_class(_STATE_TONES.get(check.state, "offline"))
        row.add_suffix(state)
        callback = self._actions.get(check.action)
        if check.action and callback is not None:
            button = Gtk.Button(label=check.action_label, valign=Gtk.Align.CENTER)
            button.update_property([Gtk.AccessibleProperty.DESCRIPTION], [check.title])
            button.connect("clicked", lambda _button, run=callback: run())
            row.add_suffix(button)
        row.update_property([Gtk.AccessibleProperty.DESCRIPTION], [f"{hc.state_label(check.state)}: {check.detail}"])
        return row

    # ── common problems ──────────────────────────────────────────────────
    def open_problem(self, problem: Problem) -> Adw.Dialog:
        dialog = Adw.Dialog(title=problem.title)
        dialog.set_content_width(520)
        dialog.add_css_class("brp-dialog")
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        for edge in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{edge}")(20)
        steps = Adw.PreferencesGroup(title=_("What to try"))
        for number, text in enumerate(problem.steps, start=1):
            row = Adw.ActionRow(title=text, use_markup=False)
            row.set_title_lines(0)
            badge = Gtk.Label(label=str(number), valign=Gtk.Align.CENTER)
            badge.add_css_class("brp-step-number")
            row.add_prefix(badge)
            steps.add(row)
        box.append(steps)
        related = [check for check in hc.checks(self._facts) if check.key in problem.checks] if self._facts is not None else []
        if related:
            state = Adw.PreferencesGroup(title=_("On this computer now"))
            for check in related:
                state.add(self._check_row(check))
            box.append(state)
        elif problem.checks:
            hint = Gtk.Label(label=_("Open Check this computer first to see the current state here."), xalign=0, wrap=True)
            hint.add_css_class("dim-label")
            box.append(hint)
        buttons = Gtk.Box(spacing=12, halign=Gtk.Align.END)
        for action, label in problem.actions:
            callback = self._actions.get(action)
            if callback is None:
                continue
            button = Gtk.Button(label=label)
            button.connect("clicked", lambda _button, run=callback: (dialog.close(), run()))
            buttons.append(button)
        if buttons.get_first_child() is not None:
            box.append(buttons)
        scroll = Gtk.ScrolledWindow(vexpand=True, propagate_natural_height=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(box)
        toolbar.set_content(scroll)
        dialog.set_child(toolbar)
        dialog.present(self)
        return dialog


__all__ = ["Problem", "SupportCenter", "problems"]
