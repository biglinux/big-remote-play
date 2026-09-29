"""Join a ZeroTier network with a code: one field, one button, a clear state.

Used by the ZeroTier connection page and by the guided setup, so both show the
same words for the same answer from the network. All work runs in a worker
(:class:`~big_remote_play.private_network.zerotier_join.ZeroTierJoiner`);
this module only renders :class:`JoinSnapshot` values on the GTK thread.
While the network owner has not decided yet, the panel checks again by itself
at a slow pace, and only while it is on screen.
"""

from __future__ import annotations

from collections.abc import Callable
import socket
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango  # type: ignore

from big_remote_play.private_network.zerotier_join import JoinPhase, JoinSnapshot, ZeroTierJoiner
from big_remote_play.utils.i18n import _
from big_remote_play.utils.vpn_accounts import valid_zerotier_network_id
from big_remote_play.utils.uri import open_uri

from .components import icon_tile, name_icon_button
from .network_common import Worker, copy_row, copy_to_clipboard

EXAMPLE_CODE = "8056c2e21c000001"
# Checking again while the owner decides: often at first, then calmly.
FAST_CHECK_SECONDS = 5
SLOW_CHECK_SECONDS = 15
FAST_CHECKS = 24  # two minutes


def join_words(snapshot: JoinSnapshot, *, advanced: bool = False, slow: bool = False) -> tuple[str, str, str, str]:
    """``(icon, tone, title, body)`` for one state, in words a child can follow.

    ``slow``: the network has not answered after the first wait. A mistyped
    code usually ends here, because no controller exists to say "not found".
    """
    phase = snapshot.phase
    name = snapshot.network_name or snapshot.network_id
    if phase is JoinPhase.CHECKING:
        return "brp-network-idle-symbolic", "offline", _("Checking ZeroTier…"), _("This takes a few seconds.")
    if phase is JoinPhase.STARTING_SERVICE:
        return "brp-network-idle-symbolic", "offline", _("Starting ZeroTier…"), _("Your password may be requested.")
    if phase is JoinPhase.ASKING_PERMISSION:
        return "brp-dialog-password-symbolic", "attention", _("Asking for permission…"), _("Allow it once so Big Remote Play can follow the connection. Your password may be requested.")
    if phase is JoinPhase.JOINING:
        return "brp-network-idle-symbolic", "offline", _("Joining the network…"), _("Sending the request to the network.")
    if phase is JoinPhase.WAITING_CONFIGURATION and slow:
        return (
            "brp-network-idle-symbolic",
            "attention",
            _("The network has not answered yet"),
            _("This is taking longer than usual. Check that the code is right. If it is, the network may be offline; this computer keeps trying."),
        )
    if phase is JoinPhase.WAITING_CONFIGURATION:
        return "brp-network-idle-symbolic", "offline", _("Connecting to the network…"), _("ZeroTier is receiving the network settings.")
    if phase is JoinPhase.NODE_OFFLINE:
        body = _("Check the internet connection of this computer.")
        if advanced:
            body = _("The ZeroTier service cannot reach the internet. Check the connection, or a firewall blocking UDP port 9993.")
        return "brp-network-offline-symbolic", "attention", _("ZeroTier cannot reach the internet"), body
    if phase is JoinPhase.WAITING_AUTHORIZATION:
        return (
            "brp-emblem-ok-symbolic",
            "attention",
            _("Waiting for authorization"),
            _("The request to join was sent. Now the network owner needs to authorize this computer. It connects by itself as soon as they do."),
        )
    if phase is JoinPhase.WAITING_ADDRESS:
        return "brp-network-idle-symbolic", "attention", _("Almost there"), _("This computer is authorized, but the network has not given it an address yet. The network owner can assign one.")
    if phase is JoinPhase.CONNECTED:
        return "brp-emblem-ok-symbolic", "online", _("Connected"), _("This computer is on the network {name}.").format(name=name)
    if phase is JoinPhase.NOT_FOUND:
        return "dialog-warning-symbolic", "attention", _("Network code not found"), _("Check the code and try again. If it is right, the network may have been deleted.")
    if phase is JoinPhase.INVALID_ID:
        return "dialog-warning-symbolic", "attention", _("This code is not complete"), _("A network code has 16 letters and numbers, like {example}.").format(example=EXAMPLE_CODE)
    if phase is JoinPhase.SERVICE_STOPPED:
        return "dialog-warning-symbolic", "attention", _("ZeroTier is not running"), _("Big Remote Play can start it for you. Your password may be requested.")
    if phase is JoinPhase.NEEDS_PERMISSION:
        return "dialog-warning-symbolic", "attention", _("Big Remote Play needs your permission"), _("Allow it once to use ZeroTier on this computer. Your password may be requested.")
    if phase is JoinPhase.NOT_INSTALLED:
        return "brp-network-private-symbolic", "offline", _("ZeroTier is not installed"), _("Install it first, then join the network.")
    if phase is JoinPhase.PORT_ERROR:
        return "dialog-warning-symbolic", "attention", _("ZeroTier could not create its network adapter"), _("Restart the computer and try again. If it keeps happening, reinstall ZeroTier.")
    if phase is JoinPhase.CLIENT_TOO_OLD:
        return "dialog-warning-symbolic", "attention", _("ZeroTier needs an update"), _("This network needs a newer version of ZeroTier. Update the system and try again.")
    if phase is JoinPhase.SIGN_IN_REQUIRED:
        return "brp-accounts-symbolic", "attention", _("This network needs a sign-in"), _("Its owner uses a sign-in page. Sign in with the ZeroTier app, then check again.")
    if phase is JoinPhase.NOT_JOINED:
        return "brp-network-offline-symbolic", "offline", _("This computer is not on the network"), _("Join again to send a new request.")
    return "dialog-warning-symbolic", "attention", _("We could not join this network"), _("Try again. Technical details show what ZeroTier answered.")


def action_for(phase: JoinPhase) -> str:
    """The label of the one primary button for a state."""
    if phase.waiting:
        return _("Check again")
    if phase is JoinPhase.SERVICE_STOPPED:
        return _("Start ZeroTier")
    if phase is JoinPhase.NEEDS_PERMISSION:
        return _("Allow")
    if phase in (JoinPhase.FAILED, JoinPhase.NOT_FOUND, JoinPhase.PORT_ERROR, JoinPhase.CLIENT_TOO_OLD, JoinPhase.NOT_JOINED):
        return _("Try again")
    return _("Join network")


class ZeroTierJoinPanel(Gtk.Box):
    """Code entry, join button and the live state of one join.

    ``on_connected`` runs once when the network is ready; ``joiner_factory``
    exists for tests (the default talks to the real zerotier-cli).
    """

    def __init__(
        self,
        main_window,
        *,
        advanced: bool = False,
        on_connected: Callable[[JoinSnapshot], object] | None = None,
        on_change: Callable[[JoinSnapshot], object] | None = None,
        joiner_factory: Callable[[], ZeroTierJoiner] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.main_window = main_window
        self.advanced = advanced
        self._on_connected = on_connected
        self._on_change = on_change
        self._slow: set[str] = set()  # networks that did not answer during the first wait
        self._joiner_factory = joiner_factory or self._default_joiner
        self._worker = Worker()
        self._cancel = threading.Event()
        self._timer = 0
        self._checks = 0
        self.snapshot: JoinSnapshot | None = None
        self._build()
        self.connect("map", lambda *_args: self._resume_checking())
        self.connect("unmap", lambda *_args: self._stop_checking())
        self.connect("destroy", lambda *_args: self.close())

    def _default_joiner(self) -> ZeroTierJoiner:
        from big_remote_play.utils.vpn_accounts import VPNAccountManager

        check = self.main_window.system_check
        return ZeroTierJoiner(VPNAccountManager(check), installed=check.has_zerotier, service_running=check.is_zerotier_running)

    # ── layout ──────────────────────────────────────────────────────────────
    def _build(self) -> None:
        group = self.group = Adw.PreferencesGroup()
        self.entry = Adw.EntryRow(title=_("Network ID (16 characters)") if self.advanced else _("Network code"), use_markup=False)
        self.entry.set_input_hints(Gtk.InputHints.NO_SPELLCHECK | Gtk.InputHints.UPPERCASE_CHARS)
        self.entry.connect("entry-activated", lambda *_args: self.join())
        self.entry.connect("changed", lambda *_args: self.entry.remove_css_class("error"))
        info = Gtk.MenuButton(icon_name="brp-dialog-information-symbolic", valign=Gtk.Align.CENTER)
        info.add_css_class("flat")
        name_icon_button(info, _("What is the network code?"))
        help_text = Gtk.Label(
            label=_("A code of 16 letters and numbers sent by the owner of the network. Example: {example}").format(example=EXAMPLE_CODE),
            wrap=True,
            max_width_chars=36,
            xalign=0,
        )
        for edge in ("top", "bottom", "start", "end"):
            getattr(help_text, f"set_margin_{edge}")(12)
        popover = Gtk.Popover(child=help_text)
        info.set_popover(popover)
        self.entry.add_suffix(info)
        group.add(self.entry)
        self.append(group)

        self.button = Gtk.Button(halign=Gtk.Align.CENTER)
        self.button.add_css_class("suggested-action")
        self.button.add_css_class("brp-primary")
        self.button.set_size_request(220, 48)
        inner = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        self.spinner = Adw.Spinner(visible=False)
        self.button_label = Gtk.Label(label=_("Join network"))
        inner.append(self.spinner)
        inner.append(self.button_label)
        self.button.set_child(inner)
        self.button.connect("clicked", lambda _button: self._primary())
        self.append(self.button)

        # The state, read aloud when it changes.
        self.card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, visible=False)
        self.card.add_css_class("network-hero")
        self.card.set_accessible_role(Gtk.AccessibleRole.STATUS)
        top = Gtk.Box(spacing=16)
        self.card_icon = Gtk.Box()
        top.append(self.card_icon)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True, valign=Gtk.Align.CENTER)
        self.title = Gtk.Label(xalign=0, wrap=True)
        self.title.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.title.add_css_class("title-3")
        self.body = Gtk.Label(xalign=0, wrap=True)
        self.body.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.body.add_css_class("dim-label")
        texts.append(self.title)
        texts.append(self.body)
        top.append(texts)
        self.card.append(top)

        # Waiting for the owner: what they need to find this computer.
        self.device_group = Adw.PreferencesGroup(title=_("Your device"), visible=False)
        self._device_rows: list[Gtk.Widget] = []
        self.card.append(self.device_group)
        self.website = Gtk.Button(label=_("Approve on the ZeroTier website"), halign=Gtk.Align.START, visible=False)
        self.website.add_css_class("flat")
        self.website.update_property([Gtk.AccessibleProperty.DESCRIPTION], [_("For the network owner: opens ZeroTier Central in the browser.")])
        self.website.connect("clicked", lambda button: open_uri(button, f"https://my.zerotier.com/network/{self.snapshot.network_id if self.snapshot else ''}"))
        self.card.append(self.website)
        # Undo a request that should not stay open (wrong code, changed mind).
        self.leave = Gtk.Button(label=_("Cancel the request"), halign=Gtk.Align.START, visible=False)
        self.leave.add_css_class("flat")
        self.leave.update_property([Gtk.AccessibleProperty.DESCRIPTION], [_("This computer leaves the network. You can join again later with the same code.")])
        self.leave.connect("clicked", lambda _button: self.cancel_request())
        self.card.append(self.leave)
        self.append(self.card)

        self.details = Adw.ExpanderRow(title=_("Technical details"), subtitle=_("What ZeroTier answered, to share with someone helping you."), use_markup=False)
        self.details_label = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.details_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.details_label.add_css_class("monospace")
        for edge in ("top", "bottom", "start", "end"):
            getattr(self.details_label, f"set_margin_{edge}")(12)
        details_row = Gtk.ListBoxRow(activatable=False, child=self.details_label)
        self.details.add_row(details_row)
        copy_details = Gtk.Button(icon_name="brp-edit-copy-symbolic", valign=Gtk.Align.CENTER)
        copy_details.add_css_class("flat")
        name_icon_button(copy_details, _("Copy technical details"))
        copy_details.connect("clicked", lambda button: copy_to_clipboard(button, self.details_label.get_text(), self.main_window.show_toast))
        self.details.add_suffix(copy_details)
        self.details_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, visible=False)
        self.details_list.add_css_class("boxed-list")
        self.details_list.append(self.details)
        self.append(self.details_list)

    # ── actions ─────────────────────────────────────────────────────────────
    @property
    def network_id(self) -> str:
        return "".join(self.entry.get_text().split()).replace("-", "").lower()

    def set_code(self, code: str) -> None:
        self.entry.set_text(code or "")

    def _primary(self) -> None:
        snapshot = self.snapshot
        if snapshot is not None and snapshot.phase.waiting and snapshot.network_id == self.network_id:
            self.check_now()
        else:
            self.join()

    def join(self, network_id: str | None = None) -> None:
        if network_id is not None:
            self.entry.set_text(network_id)
        code = self.network_id
        self._stop_checking()
        self._cancel.set()
        if not valid_zerotier_network_id(code):
            # Said at once, before any command runs.
            self._worker.cancel()
            self._render(JoinSnapshot(JoinPhase.INVALID_ID, network_id=code))
            return
        cancel = self._cancel = threading.Event()
        generation = self._worker.generation + 1

        def report(snapshot: JoinSnapshot) -> None:
            def show() -> bool:
                if self._worker.generation == generation:
                    self._render(snapshot)
                return False

            GLib.idle_add(show)

        self._render(JoinSnapshot(JoinPhase.CHECKING, network_id=code))
        self._worker.submit(lambda: self._joiner_factory().join(code, report=report, cancelled=cancel.is_set), self._finished, failed=self._crashed)

    def check_now(self) -> None:
        snapshot = self.snapshot
        if snapshot is None or not snapshot.network_id:
            return
        network_id = snapshot.network_id
        self._set_busy(True)
        self._worker.submit(lambda: self._joiner_factory().check(network_id), self._finished, failed=self._crashed)

    def cancel_request(self) -> None:
        snapshot = self.snapshot
        if snapshot is None or not snapshot.network_id:
            return
        network_id = snapshot.network_id
        self._stop_checking()
        self._set_busy(True)
        joiner = self._joiner_factory()

        def work() -> JoinSnapshot:
            joiner.manager.leave_zerotier_network(network_id, allow_privileged=True)
            return joiner.check(network_id, with_peers=False)

        def done(snapshot: JoinSnapshot) -> None:
            self._slow.discard(network_id)
            self._finished(snapshot)
            if snapshot.phase is JoinPhase.NOT_JOINED:
                self.main_window.show_toast(_("Request cancelled"))

        self._worker.submit(work, done, failed=self._crashed)

    def _finished(self, snapshot: JoinSnapshot) -> None:
        if snapshot.phase is JoinPhase.WAITING_CONFIGURATION and snapshot.join_result:
            # The join's own wait ended without an answer from the network.
            self._slow.add(snapshot.network_id)
        self._render(snapshot)
        if snapshot.phase is JoinPhase.CONNECTED:
            self._stop_checking()
            from big_remote_play.ui.private_network_view import _save_history

            _save_history({"vpn": "zerotier", "network_id": snapshot.network_id})
            if self._on_connected is not None:
                self._on_connected(snapshot)
        elif snapshot.phase.waiting:
            if snapshot.phase is JoinPhase.WAITING_AUTHORIZATION:
                from big_remote_play.ui.private_network_view import _save_history

                # Remembered so the code is there next time; the request stays open.
                _save_history({"vpn": "zerotier", "network_id": snapshot.network_id})
            self._schedule_check()
        else:
            self._stop_checking()

    def _crashed(self, error: BaseException) -> None:
        self._render(JoinSnapshot(JoinPhase.FAILED, network_id=self.network_id, detail=str(error)))

    # ── checking again by itself ───────────────────────────────────────────
    def _schedule_check(self) -> None:
        self._stop_checking()
        if not self.get_mapped():
            return
        seconds = FAST_CHECK_SECONDS if self._checks < FAST_CHECKS else SLOW_CHECK_SECONDS
        self._timer = GLib.timeout_add_seconds(seconds, self._tick)

    def _tick(self) -> bool:
        self._timer = 0
        self._checks += 1
        self.check_now()
        return False

    def _stop_checking(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _resume_checking(self) -> None:
        if self.snapshot is not None and self.snapshot.phase.waiting and not self._timer:
            self._schedule_check()

    def close(self) -> None:
        self._stop_checking()
        self._cancel.set()
        self._worker.close()

    # ── rendering ──────────────────────────────────────────────────────────
    def _set_busy(self, busy: bool) -> None:
        self.spinner.set_visible(busy)
        self.button.set_sensitive(not busy)
        self.entry.set_sensitive(not busy)

    def _render(self, snapshot: JoinSnapshot) -> None:
        self.snapshot = snapshot
        phase = snapshot.phase
        icon, tone, title, body = join_words(snapshot, advanced=self.advanced, slow=snapshot.network_id in self._slow)
        child = self.card_icon.get_first_child()
        if child is not None:
            self.card_icon.remove(child)
        self.card_icon.append(icon_tile(icon, large=True, tone="guest" if tone == "online" else "accent"))
        self.title.set_label(title)
        self.body.set_label(body)
        self.card.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION], [title, body])
        self.card.set_visible(True)
        self._set_busy(phase.busy)
        if phase is JoinPhase.INVALID_ID:
            self.entry.add_css_class("error")
            self.entry.grab_focus()
        self.button_label.set_label(action_for(phase) if not phase.busy else title)
        self.button.update_property([Gtk.AccessibleProperty.LABEL], [self.button_label.get_label()])
        # Connected: nothing is left to press here; the caller offers the next step.
        self.button.set_visible(phase is not JoinPhase.CONNECTED)
        self._render_device(snapshot)
        self.website.set_visible(phase is JoinPhase.WAITING_AUTHORIZATION)
        self.leave.set_visible(phase is JoinPhase.WAITING_AUTHORIZATION or (phase is JoinPhase.WAITING_CONFIGURATION and snapshot.network_id in self._slow))
        lines = snapshot.technical_lines()
        self.details_label.set_label("\n".join(lines))
        self.details_list.set_visible(not phase.busy)
        if self._on_change is not None:
            self._on_change(snapshot)

    def _render_device(self, snapshot: JoinSnapshot) -> None:
        for row in self._device_rows:
            self.device_group.remove(row)
        self._device_rows = []
        show = snapshot.phase in (JoinPhase.WAITING_AUTHORIZATION, JoinPhase.WAITING_ADDRESS) and bool(snapshot.node_id)
        self.device_group.set_visible(show)
        if not show:
            return
        name = Adw.ActionRow(title=_("This computer"), subtitle=socket.gethostname(), use_markup=False)
        name.add_css_class("property")
        rows: list[Gtk.Widget] = [name, copy_row(_("Node ID"), snapshot.node_id, icon="brp-dialog-password-symbolic", toast=self.main_window.show_toast)]
        for row in rows:
            self.device_group.add(row)
        self._device_rows = rows


__all__ = ["ZeroTierJoinPanel", "action_for", "join_words"]
