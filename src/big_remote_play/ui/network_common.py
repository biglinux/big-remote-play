"""Shared pieces of the private-network UI: workers, wording and copy rows.

Wording lives here so every page names a state, an error or an action the
same way. Technical identifiers (BackendState, HTTP status, Node ID) are kept
for a secondary "Technical details" line and never replace the sentence.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
import threading
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # type: ignore

from big_remote_play.private_network.diagnostics import classify_latency
from big_remote_play.private_network.http import ApiErrorKind, ApiResult
from big_remote_play.private_network.models import ConnectionState, PathReport, ProviderId, Recovery
from big_remote_play.private_network.redaction import redact
from big_remote_play.utils.i18n import _
from big_remote_play.utils.icons import create_icon_widget

from .components import name_icon_button

_log = logging.getLogger("big-remoteplay")

PROVIDER_ICONS = {
    ProviderId.TAILSCALE: "brp-tailscale-symbolic",
    ProviderId.HEADSCALE: "brp-headscale-symbolic",
    ProviderId.ZEROTIER: "brp-zerotier-symbolic",
}


class Worker:
    """Run blocking calls off the GTK thread and deliver only current results.

    ``submit`` bumps a generation; a result from an older generation, or one
    that arrives after :meth:`cancel` (page closed, navigation), is dropped.
    Callbacks always run on the GTK main loop.
    """

    def __init__(self) -> None:
        self._generation = 0
        self._closed = False

    @property
    def generation(self) -> int:
        return self._generation

    def cancel(self) -> None:
        self._generation += 1

    def close(self) -> None:
        self._closed = True
        self.cancel()

    def submit(self, work: Callable[[], Any], done: Callable[[Any], object], *, failed: Callable[[BaseException], object] | None = None, keep_previous: bool = False) -> int:
        if not keep_previous:
            self._generation += 1
        generation = self._generation

        def deliver(callback: Callable[..., object], value: Any) -> bool:
            if not self._closed and generation == self._generation:
                callback(value)
            return False

        def run() -> None:
            try:
                value = work()
            except Exception as error:  # reported, never raised into a thread
                _log.warning("Private-network task failed: %s", redact(error))
                if failed is not None:
                    GLib.idle_add(deliver, failed, error)
                return
            GLib.idle_add(deliver, done, value)

        threading.Thread(target=run, daemon=True).start()
        return generation


# ── wording ───────────────────────────────────────────────────────────────


def state_label(state: ConnectionState) -> str:
    return {
        ConnectionState.CONNECTED: _("Connected"),
        ConnectionState.DISCONNECTED: _("Not connected"),
        ConnectionState.CONNECTING: _("Setting up"),
        ConnectionState.NEEDS_AUTHENTICATION: _("Sign-in needed"),
        ConnectionState.NEEDS_AUTHORIZATION: _("Waiting for approval"),
        ConnectionState.UNAVAILABLE: _("Not available"),
        ConnectionState.ERROR: _("Needs attention"),
    }[state]


def state_tone(state: ConnectionState) -> str:
    """CSS tone for the state pill; the label always carries the meaning."""
    if state is ConnectionState.CONNECTED:
        return "online"
    if state in (ConnectionState.ERROR, ConnectionState.NEEDS_AUTHENTICATION, ConnectionState.NEEDS_AUTHORIZATION):
        return "attention"
    return "offline"


def state_icon(state: ConnectionState) -> str:
    if state is ConnectionState.CONNECTED:
        return "brp-emblem-ok-symbolic"
    if state is ConnectionState.CONNECTING:
        return "brp-network-idle-symbolic"
    if state in (ConnectionState.ERROR, ConnectionState.NEEDS_AUTHENTICATION, ConnectionState.NEEDS_AUTHORIZATION):
        return "dialog-warning-symbolic"
    return "brp-network-offline-symbolic"


def state_sentence(provider: ProviderId, state: ConnectionState, recovery: Recovery | None = None) -> str:
    name = provider.display_name
    if recovery is Recovery.GRANT_ACCESS:
        return _("{name} is installed, but Big Remote Play needs your permission to read its networks.").format(name=name)
    if recovery is Recovery.START_SERVICE:
        return _("The {name} service is not running on this computer.").format(name=name)
    if recovery is Recovery.INSTALL or state is ConnectionState.UNAVAILABLE:
        return _("{name} is not installed on this computer.").format(name=name)
    return {
        ConnectionState.CONNECTED: _("This computer is on your {name} network.").format(name=name),
        ConnectionState.DISCONNECTED: _("This computer is not on a {name} network right now.").format(name=name),
        ConnectionState.CONNECTING: _("{name} is still setting up the connection.").format(name=name),
        ConnectionState.NEEDS_AUTHENTICATION: _("Sign in to {name} to continue.").format(name=name),
        ConnectionState.NEEDS_AUTHORIZATION: _("The network owner still needs to approve this computer.").format(name=name),
        ConnectionState.ERROR: _("{name} reported a problem.").format(name=name),
    }[state]


def simple_state_sentence(state: ConnectionState, recovery: Recovery | None = None) -> str:
    """Human wording for the default UI without exposing provider internals."""
    if recovery is Recovery.GRANT_ACCESS:
        return _("Allow")
    if recovery is Recovery.START_SERVICE:
        return _("Start service")
    if recovery is Recovery.INSTALL or state is ConnectionState.UNAVAILABLE:
        return _("Installation needed")
    return {
        ConnectionState.CONNECTED: _("Ready to connect"),
        ConnectionState.DISCONNECTED: _("Not connected"),
        ConnectionState.CONNECTING: _("Setting up"),
        ConnectionState.NEEDS_AUTHENTICATION: _("Sign in"),
        ConnectionState.NEEDS_AUTHORIZATION: _("The network owner still needs to approve this computer."),
        ConnectionState.ERROR: _("Needs attention"),
    }[state]


def recovery_label(recovery: Recovery) -> str:
    return {
        Recovery.INSTALL: _("Install"),
        Recovery.START_SERVICE: _("Start service"),
        Recovery.SIGN_IN: _("Sign in"),
        Recovery.AUTHORIZE_DEVICE: _("How to approve"),
        Recovery.UPDATE_CREDENTIAL: _("Update credential"),
        Recovery.RECONNECT: _("Reconnect"),
        Recovery.JOIN_NETWORK: _("Connect this computer"),
        Recovery.GRANT_ACCESS: _("Allow"),
    }[recovery]


def api_error_message(result: ApiResult) -> str:
    """One sentence for a failed API call; the detail stays technical."""
    return {
        ApiErrorKind.AUTH: _("The credential was refused. It may be wrong, expired or revoked."),
        ApiErrorKind.FORBIDDEN: _("The credential works but does not allow this action."),
        ApiErrorKind.NOT_FOUND: _("The server did not find what was requested."),
        ApiErrorKind.RATE_LIMITED: _("The service asked to wait before trying again."),
        ApiErrorKind.BAD_REQUEST: _("The service did not accept the request."),
        ApiErrorKind.UNAVAILABLE: _("The service could not be reached. Check the internet connection and the server address."),
        ApiErrorKind.TIMEOUT: _("The operation took longer than expected."),
        ApiErrorKind.INVALID_RESPONSE: _("The service answered with something unexpected."),
        ApiErrorKind.REDIRECT: _("The server tried to redirect the request, which is refused for safety."),
        ApiErrorKind.INSECURE_URL: _("Only secure https addresses are accepted."),
    }.get(result.error or ApiErrorKind.INVALID_RESPONSE, _("The operation failed."))


def technical_detail(result: ApiResult) -> str:
    parts = [f"HTTP {result.status}" if result.status else "", result.error.value if result.error else "", redact(result.detail)]
    return " · ".join(part for part in parts if part)


def path_summary(path: PathReport | None) -> tuple[str, str]:
    """(title, subtitle) describing how packets reach the other computer."""
    if path is None or not path.reachable:
        return _("Route not measured"), _("The connection type could not be determined.")
    latency = f"{path.latency_ms:.0f} ms" if path.latency_ms is not None else ""
    quality = {"excellent": _("Excellent"), "good": _("Good"), "high": _("High"), "unknown": ""}[classify_latency(path.latency_ms)]
    measured = " · ".join(value for value in (latency, quality) if value)
    if path.kind == "direct":
        return _("Direct connection"), _("Best performance.") + (f" {measured}" if measured else "")
    if path.kind in ("relay", "peer_relay"):
        return _("Relayed connection"), _("Traffic passes through a relay server, which can add latency.") + (f" {measured}" if measured else "")
    return _("Connection works"), measured


# ── widgets ───────────────────────────────────────────────────────────────


def copy_to_clipboard(widget: Gtk.Widget, text: str, toast: Callable[[str], None] | None = None) -> None:
    display = widget.get_display() if widget is not None else Gdk.Display.get_default()
    if display is None:
        return
    display.get_clipboard().set(text)
    if toast is not None:
        toast(_("Copied"))
    if isinstance(widget, Gtk.Button):
        _show_copied(widget)


def _show_copied(button: Gtk.Button) -> None:
    """The copy button itself turns into a check for a moment."""
    if getattr(button, "_brp_copied_source", 0):
        return
    previous = button.get_child()
    button.set_child(create_icon_widget("brp-emblem-ok-symbolic", size=16))
    button.set_tooltip_text(_("Copied"))

    def restore() -> bool:
        button._brp_copied_source = 0
        button.set_child(previous)
        button.set_tooltip_text(_("Copy"))
        return False

    button._brp_copied_source = GLib.timeout_add(1500, restore)


def copy_row(title: str, value: str, *, icon: str = "brp-address-symbolic", toast: Callable[[str], None] | None = None, subtitle: str = "") -> Adw.ActionRow:
    """A row showing a value someone may need to send to another computer."""
    row = Adw.ActionRow(title=title, subtitle=subtitle or value, use_markup=False)
    row.set_subtitle_selectable(True)
    row.set_subtitle_lines(0)
    image = create_icon_widget(icon, size=18, css_class="brp-row-icon")
    image.set_valign(Gtk.Align.CENTER)
    row.add_prefix(image)
    button = Gtk.Button(valign=Gtk.Align.CENTER)
    button.set_child(create_icon_widget("brp-edit-copy-symbolic", size=16))
    button.add_css_class("flat")
    name_icon_button(button, _("Copy"), _("Copy {name} to the clipboard").format(name=title))
    button.connect("clicked", lambda widget: copy_to_clipboard(widget, value, toast))
    row.add_suffix(button)
    row._brp_copy_button = button
    return row


def state_pill(state: ConnectionState) -> Gtk.Box:
    """State in words with a matching icon; colour is only reinforcement."""
    box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
    box.add_css_class("state-pill")
    box.add_css_class(state_tone(state))
    icon = create_icon_widget(state_icon(state), size=14)
    box.append(icon)
    label = Gtk.Label(label=state_label(state), wrap=True, xalign=0)
    label.add_css_class("caption-heading")
    box.append(label)
    box.update_property([Gtk.AccessibleProperty.LABEL], [state_label(state)])
    return box


def loading_row(title: str) -> Adw.ActionRow:
    row = Adw.ActionRow(title=title, use_markup=False)
    spinner = Adw.Spinner()
    spinner.set_size_request(16, 16)
    row.add_prefix(spinner)
    return row


def message_row(title: str, subtitle: str = "", icon: str = "brp-dialog-information-symbolic") -> Adw.ActionRow:
    row = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False)
    row.set_title_lines(0)
    row.set_subtitle_lines(0)
    image = create_icon_widget(icon, size=18, css_class="brp-row-icon")
    image.set_valign(Gtk.Align.CENTER)
    row.add_prefix(image)
    return row


class RowGroup(Adw.PreferencesGroup):
    """A preferences group whose dynamic rows can be replaced safely."""

    __gtype_name__ = "BrpRowGroup"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._rows: list[Gtk.Widget] = []

    def replace(self, rows: list[Gtk.Widget]) -> None:
        for row in self._rows:
            self.remove(row)
        self._rows = []
        for row in rows:
            self.add(row)
            self._rows.append(row)

    @property
    def dynamic_rows(self) -> list[Gtk.Widget]:
        return list(self._rows)


def confirm(parent: Gtk.Widget, heading: str, body: str, action_label: str, on_confirm: Callable[[], object], *, destructive: bool = True) -> Adw.AlertDialog:
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.set_body_use_markup(False)
    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("confirm", action_label)
    dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE if destructive else Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")
    dialog.connect("response", lambda _dialog, response: on_confirm() if response == "confirm" else None)
    dialog.present(parent)
    return dialog


# Credential prefixes that must never be turned into a QR code: a photo of the
# screen would be enough to copy them.
_NO_QR_PREFIXES = ("tskey-", "hskey-")


def qr_code(text: str, *, size: int = 220, description: str = "") -> Gtk.Widget:
    """A scannable QR code for ``text``: dark modules on white, quiet zone included.

    The colours are fixed (not themed) because phone cameras need dark-on-light
    contrast; the surrounding card keeps the app's theme.
    """
    from big_remote_play.utils import qr

    if text.startswith(_NO_QR_PREFIXES):
        raise ValueError("credentials are never shown as a QR code")
    matrix = qr.encode(text)
    modules = len(matrix)
    quiet = 4

    def draw(_area, cr, width, height) -> None:
        side = min(width, height)
        scale = side / (modules + 2 * quiet)
        left = (width - side) / 2
        top = (height - side) / 2
        cr.set_source_rgb(1, 1, 1)
        cr.rectangle(left, top, side, side)
        cr.fill()
        cr.set_source_rgb(0, 0, 0)
        for y, row in enumerate(matrix):
            for x, dark in enumerate(row):
                if dark:
                    cr.rectangle(left + (x + quiet) * scale, top + (y + quiet) * scale, scale + 0.5, scale + 0.5)
        cr.fill()

    area = Gtk.DrawingArea(content_width=size, content_height=size, halign=Gtk.Align.CENTER)
    area.set_draw_func(draw)
    area.set_accessible_role(Gtk.AccessibleRole.IMG)
    area.update_property([Gtk.AccessibleProperty.LABEL], [description or _("QR code")])
    area._brp_qr_text = text
    return area


def show_qr_dialog(parent: Gtk.Widget, title: str, text: str, *, hint: str = "") -> Adw.Dialog:
    """Show ``text`` as a large QR code with the same text below it."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin_top=12, margin_bottom=24, margin_start=24, margin_end=24)
    box.append(qr_code(text, size=260, description=_("QR code for {name}").format(name=title)))
    label = Gtk.Label(label=text, selectable=True, wrap=True, justify=Gtk.Justification.CENTER)
    label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    label.add_css_class("monospace")
    box.append(label)
    caption = Gtk.Label(label=hint or _("Scan it with the camera of the other device."), wrap=True, justify=Gtk.Justification.CENTER)
    caption.add_css_class("dim-label")
    box.append(caption)
    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar())
    toolbar.set_content(box)
    dialog = Adw.Dialog(title=title, child=toolbar)
    dialog.add_css_class("brp-dialog")
    dialog.set_content_width(380)
    dialog.present(parent)
    return dialog


def add_qr_button(row: Adw.ActionRow, title: str, text: str, *, hint: str = "") -> Gtk.Button:
    """Add a labelled "QR code" button to a copy row."""
    button = Gtk.Button(label=_("QR code"), valign=Gtk.Align.CENTER)
    button.add_css_class("flat")
    button.update_property([Gtk.AccessibleProperty.DESCRIPTION], [_("Show {name} as a QR code").format(name=title)])
    button.connect("clicked", lambda widget: show_qr_dialog(widget, title, text, hint=hint))
    row.add_suffix(button)
    return button
