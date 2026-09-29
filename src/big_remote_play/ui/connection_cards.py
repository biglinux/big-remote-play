"""Compact cards for "who am I playing with, and how good is the link?".

The same card is used for the computer Connect is streaming from and for each
device streaming from Share. Reading order is the visual order: the device
name, then the quality in words (with an icon; colour only reinforces it),
then the round trip in ms, then how it is reached (local network or which
private network). Everything else is behind **Details**.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Pango  # type: ignore

from big_remote_play.utils.connection_health import ConnectionInfo, Health, Quality, Transport
from big_remote_play.utils.i18n import _
from big_remote_play.utils.icons import create_icon_widget, set_icon

from .components import icon_tile, name_icon_button

TRANSPORT_ICONS = {
    Transport.LOCAL: "brp-network-wired-symbolic",
    Transport.ZEROTIER: "brp-zerotier-symbolic",
    Transport.TAILSCALE: "brp-tailscale-symbolic",
    Transport.HEADSCALE: "brp-headscale-symbolic",
    Transport.DIRECT_INTERNET: "brp-cloud-symbolic",
    Transport.UNKNOWN: "brp-network-idle-symbolic",
}
_TONES = {
    Quality.EXCELLENT: "online",
    Quality.GOOD: "online",
    Quality.POOR: "attention",
    Quality.UNSTABLE: "attention",
    Quality.NO_RESPONSE: "attention",
    Quality.MEASURING: "offline",
}


def quality_words(quality: Quality) -> str:
    return {
        Quality.MEASURING: _("Measuring…"),
        Quality.EXCELLENT: _("Excellent"),
        Quality.GOOD: _("Good"),
        Quality.POOR: _("Poor"),
        Quality.UNSTABLE: _("Unstable"),
        Quality.NO_RESPONSE: _("Not responding"),
    }[quality]


def transport_words(transport: Transport) -> str:
    return {
        Transport.LOCAL: _("Local network"),
        Transport.ZEROTIER: "ZeroTier",
        Transport.TAILSCALE: "Tailscale",
        Transport.HEADSCALE: "Headscale",
        Transport.DIRECT_INTERNET: _("Internet"),
        Transport.UNKNOWN: _("Checking the path…"),
    }[transport]


def latency_words(health: Health) -> str:
    return _("{ms} ms").format(ms=f"{health.latency_ms:.0f}") if health.latency_ms is not None else ""


def warning_words(code: str, detail: str) -> str:
    """A fixable problem in words, with what to do about it."""
    if code == "scaled":
        client, _sep, screen = detail.partition("|")
        return _(
            "This device asked for {client}; this screen is {screen}, so the picture is scaled and small text loses detail. Use the same resolution on the device, or choose one in “Screen resolution while sharing”."
        ).format(client=client, screen=screen)
    if code == "hdr_as_sdr":
        return _("This screen is in HDR and this device uses SDR: colors look washed out there. Turn on “Correct colors of HDR screens”.")
    return ""


def summary_words(info: ConnectionInfo) -> str:
    """One sentence for screen readers: name, quality, latency, path."""
    parts = [info.device_name, quality_words(info.health.quality)]
    latency = latency_words(info.health)
    if latency:
        parts.append(latency)
    if info.transport is not Transport.UNKNOWN:
        parts.append(transport_words(info.transport))
    return ", ".join(parts)


class QualityBadge(Gtk.Box):
    """A dot and a word: "● Excellent". The word carries the meaning."""

    def __init__(self) -> None:
        super().__init__(spacing=6, valign=Gtk.Align.CENTER)
        self.add_css_class("state-pill")
        self.add_css_class("brp-quality-badge")
        self._icon = create_icon_widget("brp-media-record-symbolic", size=10)
        self.append(self._icon)
        self.label = Gtk.Label(xalign=0)
        self.label.add_css_class("caption-heading")
        self.append(self.label)
        self.set(Health(Quality.MEASURING))

    def set(self, health: Health) -> None:
        for tone in set(_TONES.values()):
            self.remove_css_class(tone)
        self.add_css_class(_TONES[health.quality])
        words = quality_words(health.quality)
        self.label.set_label(words)
        self.update_property([Gtk.AccessibleProperty.LABEL], [_("Connection quality: {quality}").format(quality=words)])


class TransportBadge(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(spacing=6, valign=Gtk.Align.CENTER)
        self._icon = create_icon_widget(TRANSPORT_ICONS[Transport.UNKNOWN], size=14)
        self.append(self._icon)
        self.label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.label.add_css_class("caption")
        self.append(self.label)
        self.add_css_class("dim-label")
        self.set(Transport.UNKNOWN)

    def set(self, transport: Transport) -> None:
        set_icon(self._icon, TRANSPORT_ICONS[transport])
        self.label.set_label(transport_words(transport))


class DeviceConnectionCard(Gtk.Box):
    """Name · quality · ms · path, with the numbers behind **Details**."""

    def __init__(self, info: ConnectionInfo | None = None) -> None:
        super().__init__(spacing=14)
        self.add_css_class("brp-connection-card")
        self.set_accessible_role(Gtk.AccessibleRole.GROUP)
        self._tile = Gtk.Box(valign=Gtk.Align.CENTER)
        self._tile.append(icon_tile("brp-computer-symbolic"))
        self.append(self._tile)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True, valign=Gtk.Align.CENTER)
        self.name = Gtk.Label(xalign=0, wrap=True)
        self.name.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.name.add_css_class("heading")
        texts.append(self.name)
        # Wraps under the name in narrow windows instead of clipping.
        line = Adw.WrapBox(child_spacing=14, line_spacing=6)
        self.quality = QualityBadge()
        self.latency = Gtk.Label(xalign=0, valign=Gtk.Align.CENTER)
        self.latency.add_css_class("numeric")
        self.latency.add_css_class("heading")
        self.transport = TransportBadge()
        for widget in (self.quality, self.latency, self.transport):
            line.append(widget)
        texts.append(line)
        # Fixable problems are shown, not hidden behind Details.
        self.warning = Gtk.Label(xalign=0, wrap=True, visible=False)
        self.warning.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.warning.add_css_class("caption")
        self.warning.add_css_class("warning")
        texts.append(self.warning)
        self.append(texts)

        self.details_button = Gtk.MenuButton(icon_name="brp-dialog-information-symbolic", valign=Gtk.Align.CENTER)
        self.details_button.add_css_class("flat")
        name_icon_button(self.details_button, _("Details"), _("Address, variation and lost replies"))
        self.details_label = Gtk.Label(xalign=0, wrap=True, selectable=True, max_width_chars=40)
        for edge in ("top", "bottom", "start", "end"):
            getattr(self.details_label, f"set_margin_{edge}")(12)
        self.details_button.set_popover(Gtk.Popover(child=self.details_label))
        self.append(self.details_button)
        self.info: ConnectionInfo | None = None
        if info is not None:
            self.update(info)

    def update(self, info: ConnectionInfo) -> None:
        self.info = info
        self.name.set_label(info.device_name)
        self.quality.set(info.health)
        latency = latency_words(info.health)
        self.latency.set_label(latency)
        self.latency.set_visible(bool(latency))
        self.transport.set(info.transport)
        health = info.health
        rows = [_("Address: {address}").format(address=info.address or _("not identified"))]
        if health.jitter_ms is not None:
            rows.append(_("Variation (jitter): {ms} ms").format(ms=f"{health.jitter_ms:.0f}"))
        if health.loss_percent is not None:
            rows.append(_("Lost replies: {percent}%").format(percent=f"{health.loss_percent:.0f}"))
        rows.append(_("Measurements: {count}").format(count=health.samples))
        if info.video:
            rows.append(_("Video: {details}").format(details=info.video))
        rows.append(_("Measured by this computer with one ping every few seconds."))
        self.details_label.set_label("\n".join(rows))
        messages = [warning_words(code, detail) for code, detail in info.warnings]
        self.warning.set_label("\n".join(message for message in messages if message))
        self.warning.set_visible(any(messages))
        self.update_property([Gtk.AccessibleProperty.LABEL], [summary_words(info)])


__all__ = ["DeviceConnectionCard", "QualityBadge", "TransportBadge", "TRANSPORT_ICONS", "latency_words", "quality_words", "summary_words", "transport_words"]
