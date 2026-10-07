"""Service cards: one component for streaming and secure-connection services.

Sunshine, Moonlight, Tailscale, ZeroTier and Headscale share one card. The
card never decides a state. ``streaming_presentation`` and
``provider_presentation`` translate what the probes and the private-network
backend already report into words, a tone and an optional second line; the
card only draws that. A card changes state in place and never disappears
because a service stopped, so the list keeps its shape.
"""

from __future__ import annotations

from dataclasses import dataclass

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # type: ignore

from big_remote_play.private_network.models import ConnectionState, ProviderId, ProviderStatus, Recovery
from big_remote_play.utils.i18n import _
from big_remote_play.utils.icons import set_icon

from .components import create_icon_widget, icon_tile
from .network_common import state_label

# One CSS modifier per tone; the words always carry the meaning.
TONES = ("checking", "active", "starting", "ready", "inactive", "missing", "attention", "error")

_TONE_ICONS = {
    "active": ("brp-emblem-ok-symbolic", 16),
    "starting": ("brp-network-idle-symbolic", 16),
    "attention": ("dialog-warning-symbolic", 16),
    "error": ("dialog-warning-symbolic", 16),
}
_IDLE_ICON = ("brp-media-record-symbolic", 8)

# The same tones as the round state pills used by Connection method.
PILL_TONES = {"active": "online", "attention": "attention", "error": "attention"}


@dataclass(frozen=True)
class CardPresentation:
    text: str
    tone: str
    detail: str = ""


def checking_presentation() -> CardPresentation:
    return CardPresentation(_("Checking…"), "checking")


def unknown_presentation() -> CardPresentation:
    """The state could not be read; nothing is claimed about the service."""
    return CardPresentation(_("State unknown"), "inactive")


def streaming_presentation(service_id: str, installed: bool | None, running: bool | None) -> CardPresentation:
    """Sunshine or Moonlight, from the installed and running probes only."""
    if installed is None:
        return checking_presentation()
    if not installed:
        return CardPresentation(_("Not installed"), "missing")
    if running:
        return CardPresentation(_("Running"), "active")
    # Moonlight is an app that opens when a game starts; closed means ready.
    if service_id == "moonlight":
        # TRANSLATORS: state of the Moonlight app: installed and able to start a stream.
        return CardPresentation(_("Ready"), "ready")
    return CardPresentation(_("Stopped"), "inactive")


_STATE_TONES = {
    ConnectionState.CONNECTED: "active",
    ConnectionState.CONNECTING: "starting",
    ConnectionState.NEEDS_AUTHENTICATION: "attention",
    ConnectionState.NEEDS_AUTHORIZATION: "attention",
    ConnectionState.ERROR: "error",
    ConnectionState.DISCONNECTED: "inactive",
    ConnectionState.UNAVAILABLE: "missing",
}


def provider_presentation(status: ProviderStatus) -> CardPresentation:
    """A private-network provider in the same words every network page uses."""
    if not status.installed or status.recovery is Recovery.INSTALL:
        return CardPresentation(_("Not installed"), "missing")
    if status.recovery is Recovery.START_SERVICE:
        return CardPresentation(_("Stopped"), "inactive")
    if status.recovery is Recovery.GRANT_ACCESS:
        # TRANSLATORS: state of a private-network method; the desktop user must allow access once.
        return CardPresentation(_("Permission needed"), "attention")
    return CardPresentation(state_label(status.state), _STATE_TONES[status.state], _provider_detail(status))


def _provider_detail(status: ProviderStatus) -> str:
    """Only what the provider really knows and is safe to keep on screen.

    A Tailscale tailnet is often named after the account's e-mail address,
    and private addresses do not belong on a permanently visible card.
    """
    if not status.connected:
        return ""
    if status.provider is ProviderId.ZEROTIER:
        # The local ZeroTier service knows its networks, not their members.
        return status.network_name
    return _("Devices online now: {count}").format(count=len(status.online_peers))


class ServiceStatusCard(Adw.ActionRow):
    """A service, its state in words and one icon; activating opens its page."""

    def __init__(self, service_id: str, title: str, icon_name: str, description: str = "", *, primary: bool = False, tag: str = "", show_description: bool = False):
        super().__init__(title=title, use_markup=False)
        self.service_id = service_id
        self.description = description
        self.tag = tag
        # Where there is room (the internet page), the one-line summary is shown too.
        self.show_description = show_description
        self.presentation: CardPresentation | None = None
        self.set_subtitle_lines(0)
        self.add_css_class("brp-service-card")
        if primary:
            self.add_css_class("primary")

        # The same icon tile as the Connection method choices.
        self.add_prefix(icon_tile(icon_name))
        if tag:
            # "Recommended" / "Advanced": a hint, read before the state.
            badge = Gtk.Label(label=tag, valign=Gtk.Align.CENTER)
            badge.add_css_class("state-pill")
            badge.add_css_class("caption")
            badge.add_css_class("offline")
            self.add_suffix(badge)
        self.state_icon = create_icon_widget(_IDLE_ICON[0], size=_IDLE_ICON[1], css_class="brp-service-state")
        self.state_icon.set_valign(Gtk.Align.CENTER)
        self.add_suffix(self.state_icon)
        if description:
            self.set_tooltip_text(description)
        self.set_presentation(checking_presentation())

    @property
    def tone(self) -> str:
        return self.presentation.tone if self.presentation is not None else ""

    def set_presentation(self, presentation: CardPresentation) -> None:
        if presentation == self.presentation:
            return  # a periodic probe must not restart the state transition
        previous = self.presentation
        self.presentation = presentation
        lines = [presentation.text, presentation.detail, self.description if self.show_description else ""]
        self.set_subtitle("\n".join(line for line in lines if line))
        if previous is None or previous.tone != presentation.tone:
            for tone in TONES:
                self.remove_css_class(tone)
            self.add_css_class(presentation.tone)
            name, size = _TONE_ICONS.get(presentation.tone, _IDLE_ICON)
            set_icon(self.state_icon, name)
            self.state_icon.set_pixel_size(size)
        parts = [self.tag, presentation.text, presentation.detail, self.description]
        self.update_property([Gtk.AccessibleProperty.DESCRIPTION], [". ".join(part for part in parts if part)])


__all__ = [
    "CardPresentation",
    "PILL_TONES",
    "ServiceStatusCard",
    "TONES",
    "checking_presentation",
    "provider_presentation",
    "streaming_presentation",
    "unknown_presentation",
]
