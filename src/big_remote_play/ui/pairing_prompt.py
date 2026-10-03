"""“A computer wants to connect”: the request, where nobody can miss it.

When a device starts pairing, Share shows a dialog at once with the PIN field
focused, and keeps a card on the page for as long as the request waits, with
its countdown. Approve sends the PIN the person typed to this computer's
Sunshine for that one request; Reject cancels it. Nothing here approves a
device by itself.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango  # type: ignore

from big_remote_play.host.pairing_requests import PairRequest, countdown_text, valid_pin
from big_remote_play.utils.i18n import _

from .components import set_row_icon


class PairingRequestDialog(Adw.AlertDialog):
    """Ask for the PIN the other computer shows; approve, reject or decide later."""

    __gtype_name__ = "BrpPairingRequestDialog"

    def __init__(self, request: PairRequest, *, on_approve: Callable[[str], object], on_reject: Callable[[], object], clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__(heading=_("New connection request"))
        self.request = request
        self._clock = clock
        self._on_approve = on_approve
        self._on_reject = on_reject
        self._timer = 0
        # TRANSLATORS: {name} is the name of the computer that wants to pair with this one.
        self.set_body(_("{name} wants to connect to this computer. Type the PIN shown on its screen.").format(name=request.name))
        self.set_body_use_markup(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        group = Adw.PreferencesGroup()
        self.entry = Adw.EntryRow(title=_("PIN"))
        self.entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
        self.entry.add_css_class("brp-code-entry")
        self.entry.update_property([Gtk.AccessibleProperty.DESCRIPTION], [_("The four digits shown by Moonlight on the other computer.")])
        self.entry.connect("changed", lambda _entry: self._validate())
        self.entry.connect("entry-activated", lambda _entry: self._approve_from_keyboard())
        group.add(self.entry)
        box.append(group)
        self.error = Gtk.Label(label=_("The PIN has four digits, as shown on the other computer."), xalign=0, wrap=True, visible=False)
        self.error.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.error.add_css_class("error")
        self.error.set_accessible_role(Gtk.AccessibleRole.ALERT)
        box.append(self.error)
        self.countdown = Gtk.Label(xalign=0, wrap=True)
        self.countdown.add_css_class("caption")
        self.countdown.add_css_class("dim-label")
        box.append(self.countdown)
        self.set_extra_child(box)
        self.add_response("later", _("Not now"))
        self.add_response("reject", _("Reject"))
        self.add_response("approve", _("Approve"))
        self.set_response_appearance("reject", Adw.ResponseAppearance.DESTRUCTIVE)
        self.set_response_appearance("approve", Adw.ResponseAppearance.SUGGESTED)
        self.set_default_response("approve")
        self.set_close_response("later")
        self.set_response_enabled("approve", False)
        self.connect("response", self._on_response)
        self.connect("closed", lambda *_args: self._stop_timer())
        self._tick()
        self._timer = GLib.timeout_add_seconds(1, self._tick)

    def present_for(self, parent: Gtk.Widget) -> None:
        self.present(parent)
        GLib.idle_add(lambda: self.entry.grab_focus() and False)

    def _validate(self) -> str:
        text = self.entry.get_text()
        pin = valid_pin(text)
        self.set_response_enabled("approve", bool(pin))
        compact = "".join(character for character in text if character not in " -\t")
        # Only complain once there is enough to be wrong.
        wrong = bool(compact) and not pin and (len(compact) >= 4 or not compact.isdigit())
        self.error.set_visible(wrong)
        if wrong:
            self.entry.add_css_class("error")
        else:
            self.entry.remove_css_class("error")
        return pin

    def _approve_from_keyboard(self) -> None:
        if self._validate():
            self._on_response(self, "approve")
            self.force_close()
        else:
            self.error.set_visible(True)
            self.entry.add_css_class("error")

    def _on_response(self, _dialog, response: str) -> None:
        if getattr(self, "_answered", False):
            return
        if response == "approve":
            pin = valid_pin(self.entry.get_text())
            if not pin:
                return
            self._answered = True
            self._on_approve(pin)
        elif response == "reject":
            self._answered = True
            self._on_reject()

    def _tick(self) -> bool:
        remaining = self.request.remaining(self._clock())
        self.countdown.set_label(countdown_text(remaining))
        if remaining <= 0:
            self._timer = 0
            self.force_close()
            return False
        return True

    def _stop_timer(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0


class PairingRequestsGroup(Adw.PreferencesGroup):
    """Every waiting request on the Share page, until it is answered or expires."""

    __gtype_name__ = "BrpPairingRequestsGroup"

    def __init__(self, *, on_approve: Callable[[PairRequest], object], on_reject: Callable[[PairRequest], object], clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__(title=_("Connection request"))
        self.add_css_class("brp-request-group")
        self._on_approve = on_approve
        self._on_reject = on_reject
        self._clock = clock
        self._rows: dict[str, tuple[Adw.ActionRow, PairRequest]] = {}
        self._timer = 0
        self.set_visible(False)

    def show_requests(self, requests: Iterable[PairRequest]) -> None:
        wanted = {request.pairing_id: request for request in requests}
        for pairing_id, (row, _request) in list(self._rows.items()):
            if pairing_id not in wanted:
                self.remove(row)
                del self._rows[pairing_id]
        for pairing_id, request in sorted(wanted.items(), key=lambda item: item[1].first_seen):
            if pairing_id in self._rows:
                continue
            row = Adw.ActionRow(title=_("{name} wants to connect").format(name=request.name), use_markup=False)
            row.set_title_lines(0)
            row.set_subtitle_lines(0)
            set_row_icon(row, "brp-computer-symbolic")
            reject = Gtk.Button(label=_("Reject"), valign=Gtk.Align.CENTER)
            reject.add_css_class("flat")
            reject.connect("clicked", lambda _button, item=request: self._on_reject(item))
            approve = Gtk.Button(label=_("Approve"), valign=Gtk.Align.CENTER)
            approve.add_css_class("suggested-action")
            approve.update_property([Gtk.AccessibleProperty.DESCRIPTION], [_("Type the PIN shown on {name}").format(name=request.name)])
            approve.connect("clicked", lambda _button, item=request: self._on_approve(item))
            row.add_suffix(reject)
            row.add_suffix(approve)
            row.set_activatable_widget(approve)
            self.add(row)
            self._rows[pairing_id] = (row, request)
        self.set_visible(bool(self._rows))
        self._tick()
        if self._rows and not self._timer:
            self._timer = GLib.timeout_add_seconds(1, self._tick)

    def _tick(self) -> bool:
        if not self._rows:
            self._timer = 0
            return False
        now = self._clock()
        for row, request in self._rows.values():
            row.set_subtitle(countdown_text(request.remaining(now)))
        return True

    def clear(self) -> None:
        self.show_requests(())


__all__ = ["PairingRequestDialog", "PairingRequestsGroup"]
