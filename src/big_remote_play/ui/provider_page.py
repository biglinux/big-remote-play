"""One connection method as an ordinary page: **Devices | Advanced**.

Tailscale, ZeroTier and Headscale use the same page, in the same order:

1. the method, its state in words;
2. **Connection**: Start or Stop (or the one step that is missing);
3. **Devices**: this method's devices only, with Online/Offline, the address
   and **Copy IP**; **⋮ → Remove device** only where the provider's API can do
   it, otherwise **Manage devices** on the official website;
4. **+ Add device**, a guided page;
5. **Advanced**: everything technical.

Pages are pushed on the navigation of **Connect your devices** inside the main
window; nothing here is a dialog except short confirmations. Blocking calls
run on :class:`Worker` and stale results are dropped.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import time
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # type: ignore

from big_remote_play.private_network.device_list import DeviceListing, ProviderDevice, SimpleState, simple_state
from big_remote_play.private_network.models import ProviderId, ProviderStatus
from big_remote_play.utils.i18n import _, ngettext, pgettext
from big_remote_play.utils.icons import create_icon_widget
from big_remote_play.utils.uri import open_uri

from .components import action_row, icon_tile
from .network_common import PROVIDER_ICONS, RowGroup, Worker, copy_row, copy_to_clipboard, loading_row, message_row

if TYPE_CHECKING:
    from big_remote_play.private_network.service import PrivateNetworkService

REFRESH_SECONDS = 6


# ── words ────────────────────────────────────────────────────────────────


def method_summary(provider: ProviderId) -> str:
    return {
        ProviderId.TAILSCALE: _("Easy way to connect your devices from anywhere."),
        ProviderId.ZEROTIER: _("Connect devices using a ZeroTier network."),
        ProviderId.HEADSCALE: _("Use your own private Headscale server."),
    }[provider]


def method_tag(provider: ProviderId) -> str:
    if provider is ProviderId.TAILSCALE:
        # TRANSLATORS: shown next to Tailscale: the method suggested for most people.
        return _("Recommended")
    if provider is ProviderId.HEADSCALE:
        # TRANSLATORS: shown next to Headscale: a method for experienced users with their own server.
        return pgettext("connection method", "Advanced")
    return ""


STATE_WORDS = {
    "not_installed": ("brp-media-record-symbolic", "offline"),
    "off": ("brp-media-record-symbolic", "offline"),
    "connecting": ("brp-network-idle-symbolic", "attention"),
    "connected": ("brp-emblem-ok-symbolic", "online"),
    "attention": ("dialog-warning-symbolic", "attention"),
}


def state_text(state: SimpleState) -> str:
    return {
        "not_installed": _("Not installed"),
        # TRANSLATORS: state of a connection method that is not running on this computer.
        "off": pgettext("connection state", "Off"),
        "connecting": _("Connecting…"),
        "connected": _("Connected"),
        "attention": _("Needs attention"),
    }[state.key]


def last_seen_text(timestamp: float | None, *, now: float | None = None) -> str:
    if not timestamp:
        return ""
    minutes = max(0, int(((now if now is not None else time.time()) - timestamp) // 60))
    if minutes < 2:
        return _("Last seen just now")
    if minutes < 60:
        return ngettext("Last seen {count} minute ago", "Last seen {count} minutes ago", minutes).format(count=minutes)
    hours = minutes // 60
    if hours < 24:
        return ngettext("Last seen {count} hour ago", "Last seen {count} hours ago", hours).format(count=hours)
    days = hours // 24
    return ngettext("Last seen {count} day ago", "Last seen {count} days ago", days).format(count=days)


def device_lines(device: ProviderDevice, *, now: float | None = None) -> list[str]:
    """What a device row says under its name, in order."""
    lines: list[str] = []
    state = _("Waiting for approval") if device.waiting else (_("Online") if device.online else (_("Offline") if device.online is False else _("Status unknown")))
    lines.append(_("This device · {state}").format(state=state) if device.is_local else state)
    if device.address:
        lines.append(device.address)
    if device.online is not True and not device.is_local:
        seen = last_seen_text(device.last_seen, now=now)
        if seen:
            lines.append(seen)
    return lines


def connection_words(provider: ProviderId, status: ProviderStatus, state: SimpleState) -> tuple[str, str, str]:
    """``(sentence, detail, action)`` of the Connection section.

    ``action`` is a stable id: install | start | stop | setup | sign_in | allow | retry.
    """
    name = provider.display_name
    if state.key == "not_installed":
        return _("{name} is not installed").format(name=name), _("Big Remote Play installs it for you. Your password may be requested."), "install"
    if state.key == "connected":
        return _("{name} is on").format(name=name), _("This computer is connected."), "stop"
    if state.key == "connecting":
        return _("{name} is connecting…").format(name=name), _("This takes a few seconds."), "stop"
    if state.detail == "approval":
        return _("{name} is on").format(name=name), _("The owner of the network still needs to approve this computer."), "stop"
    if state.detail == "sign_in":
        return _("Sign in to finish"), _("Your browser opens. Use the same account on all your devices."), "sign_in"
    if state.detail == "permission":
        return _("Big Remote Play needs your permission"), _("Allow it once to see and use your networks. Your password may be requested."), "allow"
    if state.detail == "error":
        return _("{name} reported a problem").format(name=name), _("Try again. The details are in Advanced."), "retry"
    if state.detail == "stopped":
        if status.technical_detail.startswith("tailscaled is using"):
            other = ProviderId.HEADSCALE if provider is ProviderId.TAILSCALE else ProviderId.TAILSCALE
            return _("{name} is off").format(name=name), _("Starting it turns {other} off: both use the Tailscale app on this computer.").format(other=other.display_name), "start"
        return _("{name} is off").format(name=name), _("Start it to play with your other devices."), "start"
    if state.detail == "other_product":
        other = ProviderId.HEADSCALE if provider is ProviderId.TAILSCALE else ProviderId.TAILSCALE
        return (
            _("{name} is not set up yet").format(name=name),
            _("The Tailscale app on this computer is used by {other} now. Setting up {name} switches it.").format(other=other.display_name, name=name),
            "setup",
        )
    if provider is ProviderId.ZEROTIER:
        return _("{name} is not set up yet").format(name=name), _("Join a network with the code you received, or create one."), "setup"
    return _("{name} is not set up yet").format(name=name), _("It takes about a minute."), "setup"


def action_label(action: str, provider: ProviderId) -> str:
    return {
        "start": _("Start"),
        "stop": _("Stop"),
        "setup": _("Join a network") if provider is ProviderId.ZEROTIER else _("Set up"),
        "sign_in": _("Sign in"),
        "allow": _("Allow"),
        "retry": _("Try again"),
    }.get(action, "")


def state_badge(state: SimpleState) -> Gtk.Box:
    """The state in words with an icon; colour only repeats it."""
    icon_name, tone = STATE_WORDS[state.key]
    box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER, halign=Gtk.Align.START)
    box.add_css_class("state-pill")
    box.add_css_class(tone)
    box.append(create_icon_widget(icon_name, size=14))
    label = Gtk.Label(label=state_text(state), wrap=True, xalign=0)
    label.add_css_class("caption-heading")
    box.append(label)
    box.update_property([Gtk.AccessibleProperty.LABEL], [state_text(state)])
    return box


def _menu_button(description: str, items: list[tuple[str, Callable[[], object]]]) -> Gtk.MenuButton:
    button = Gtk.MenuButton(icon_name="view-more-symbolic", valign=Gtk.Align.CENTER)
    button.add_css_class("flat")
    button.set_tooltip_text(_("More actions"))
    button.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION], [_("More actions"), description])
    popover = Gtk.Popover()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
    for label, callback in items:
        item = Gtk.Button(label=label)
        item.add_css_class("flat")
        item.get_child().set_xalign(0)

        def activate(_button, run=callback) -> None:
            popover.popdown()
            run()

        item.connect("clicked", activate)
        box.append(item)
    popover.set_child(box)
    button.set_popover(popover)
    return button


def _scrolled(child: Gtk.Widget) -> Gtk.ScrolledWindow:
    clamp = Adw.Clamp(maximum_size=760, tightening_threshold=520)
    for edge in ("top", "bottom", "start", "end"):
        getattr(clamp, f"set_margin_{edge}")(24)
    clamp.set_child(child)
    scroll = Gtk.ScrolledWindow(vexpand=True)
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_child(clamp)
    return scroll


# ── the page ─────────────────────────────────────────────────────────────


class ProviderPage(Adw.NavigationPage):
    """Devices and Advanced of one connection method, inside the main window."""

    __gtype_name__ = "BrpProviderPage"

    def __init__(self, main_window, provider: ProviderId, navigation: Adw.NavigationView, *, service_factory: "Callable[[], PrivateNetworkService] | None" = None) -> None:
        super().__init__(title=provider.display_name, tag=provider.value)
        from big_remote_play.private_network.service import default_service

        self.main_window = main_window
        self.provider = provider
        self.navigation = navigation
        self.service_factory = service_factory or default_service
        self.status: ProviderStatus | None = None
        self.state = SimpleState("off")
        self.listing: DeviceListing | None = None
        self.network_id = ""
        self._busy = False
        self._timer = 0
        self._status_worker = Worker()
        self._devices_worker = Worker()
        self._action_worker = Worker()
        self.setup_page: Adw.NavigationPage | None = None
        self.add_device_flow = None
        self._built_advanced_for: tuple | None = None
        from big_remote_play.private_network.devices import DevicePreferences

        self.preferences = DevicePreferences()  # names given on this computer only
        self.view_stack = Adw.ViewStack()
        self.view_stack.add_titled_with_icon(_scrolled(self._build_devices()), "devices", _("Devices"), "brp-network-workgroup-symbolic")
        advanced = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        advanced.append(self._build_quality())
        self.advanced_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        advanced.append(self.advanced_box)
        self.view_stack.add_titled_with_icon(_scrolled(advanced), "advanced", pgettext("connection method view", "Advanced"), "brp-preferences-symbolic")
        self.view_stack.connect("notify::visible-child-name", lambda *_args: self._sync_quality())
        self.set_child(self.view_stack)
        self._render()
        self.connect("shown", lambda *_args: self._on_shown())
        self.connect("hidden", lambda *_args: (self._stop_timer(), self._stop_quality()))
        self.connect("unrealize", lambda *_args: self._cancel())

    # ── layout ─────────────────────────────────────────────────────────────
    def _build_devices(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        head = Gtk.Box(spacing=16)
        head.add_css_class("brp-intro")
        head.append(icon_tile(PROVIDER_ICONS[self.provider], large=True))
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True, valign=Gtk.Align.CENTER)
        name = Gtk.Label(label=self.provider.display_name, xalign=0, wrap=True, accessible_role=Gtk.AccessibleRole.HEADING)
        name.add_css_class("title-1")
        texts.append(name)
        self.badge_slot = Gtk.Box()
        texts.append(self.badge_slot)
        head.append(texts)
        box.append(head)

        # Connection: one sentence and one button.
        self.connection = Adw.PreferencesGroup(title=_("Connection"))
        self.connection_row = Adw.ActionRow(use_markup=False)
        self.connection_row.set_title_lines(0)
        self.connection_row.set_subtitle_lines(0)
        self.connection_row.set_accessible_role(Gtk.AccessibleRole.STATUS)
        self.connection_button = Gtk.Button(valign=Gtk.Align.CENTER)
        inner = Gtk.Box(spacing=8)
        self.connection_spinner = Adw.Spinner(visible=False)
        self.connection_label = Gtk.Label()
        inner.append(self.connection_spinner)
        inner.append(self.connection_label)
        self.connection_button.set_child(inner)
        self.connection_button.connect("clicked", lambda _button: self._run_connection_action())
        self.connection_row.add_suffix(self.connection_button)
        self.connection.add(self.connection_row)
        self.install_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.connection)
        box.append(self.install_slot)

        # Ready to play: where the person came from.
        self.ready = Adw.PreferencesGroup(title=_("Ready"))
        self.ready_row = Adw.ActionRow(title=_("Your devices can now find each other in Big Remote Play."), use_markup=False)
        self.ready_row.set_title_lines(0)
        self.ready_row.add_prefix(create_icon_widget("brp-emblem-ok-symbolic", size=18, css_class="brp-row-icon"))
        self.ready.add(self.ready_row)
        self.ready_buttons = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=2, min_children_per_line=1, homogeneous=False, halign=Gtk.Align.START, margin_top=12)
        self.ready_buttons.set_column_spacing(12)
        self.ready_buttons.set_row_spacing(8)
        self.ready.add(self.ready_buttons)
        box.append(self.ready)

        # Devices of this method only.
        self.network_row = Adw.ComboRow(title=_("Network"), use_markup=False)
        self.network_row.connect("notify::selected", lambda *_args: self._on_network_selected())
        self._network_ids: list[str] = []
        self.devices = RowGroup(title=_("Devices"))
        refresh = Gtk.Button(icon_name="brp-view-refresh-symbolic", valign=Gtk.Align.CENTER)
        refresh.add_css_class("flat")
        refresh.set_tooltip_text(_("Refresh"))
        refresh.update_property([Gtk.AccessibleProperty.LABEL], [_("Refresh devices")])
        refresh.connect("clicked", lambda _button: self.refresh())
        self.devices.set_header_suffix(refresh)
        self.devices.add(self.network_row)
        box.append(self.devices)

        # Headscale: a phone or computer waiting at the sign-in is approved
        # here with the code it shows (only when the API key works).
        self.approval = None
        if self.provider is ProviderId.HEADSCALE:
            from .network_devices import ApprovalGroup

            self.approval = ApprovalGroup(self._headscale_api, preferred_user=self._headscale_user(), on_done=lambda _name: self.refresh_devices())
            self.approval.set_visible(False)
            box.append(self.approval)

        self.add_button = Gtk.Button(halign=Gtk.Align.CENTER)
        add_inner = Gtk.Box(spacing=8)
        add_inner.append(create_icon_widget("list-add-symbolic", size=16))
        add_inner.append(Gtk.Label(label=_("Add device")))
        self.add_button.set_child(add_inner)
        self.add_button.add_css_class("suggested-action")
        self.add_button.add_css_class("pill")
        self.add_button.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION], [_("Add device"), _("Connect another PC, laptop or device to this network.")])
        self.add_button.connect("clicked", lambda _button: self.show_add_device())
        box.append(self.add_button)
        return box

    def _build_quality(self) -> Gtk.Widget:
        """The connection in use: traffic now, stability and path (measured, not configured)."""
        self.quality = Adw.PreferencesGroup(title=_("Connection quality"), description=_("Traffic is what this computer sends and receives through the secure connection now, not its maximum speed."))
        self.quality_rows: dict[str, Adw.ActionRow] = {}
        for key, title in (("traffic", _("In use now")), ("stability", pgettext("connection quality", "Network")), ("path", _("Path"))):
            row = Adw.ActionRow(title=title, subtitle=_("Measuring…"), use_markup=False)
            row.set_subtitle_lines(0)
            row.add_css_class("property")
            self.quality.add(row)
            self.quality_rows[key] = row
        self.quality.set_visible(False)
        self._quality_timer = 0
        self._quality_busy = False
        self._quality_worker = Worker()
        from big_remote_play.utils.connection_health import LatencyWindow

        self._latency = LatencyWindow()
        self._traffic: tuple[float, int, int] | None = None
        return self.quality

    def _sync_quality(self) -> None:
        """Measured only while Advanced is on screen and the method is connected."""
        connected = self.status is not None and self.status.connected
        self.quality.set_visible(connected)
        wanted = connected and self.get_mapped() and self.view_stack.get_visible_child_name() == "advanced"
        if wanted and not self._quality_timer:
            from big_remote_play.utils.connection_health import SAMPLE_INTERVAL_SECONDS

            self._sample_quality()
            self._quality_timer = GLib.timeout_add_seconds(SAMPLE_INTERVAL_SECONDS, self._quality_tick)
        elif not wanted:
            self._stop_quality()

    def _quality_tick(self) -> bool:
        if not (self.get_mapped() and self.view_stack.get_visible_child_name() == "advanced" and self.status is not None and self.status.connected):
            self._quality_timer = 0
            return False
        self._sample_quality()
        return True

    def _stop_quality(self) -> None:
        if self._quality_timer:
            GLib.source_remove(self._quality_timer)
            self._quality_timer = 0
        self._quality_worker.cancel()
        self._quality_busy = False

    def _sample_quality(self) -> None:
        status = self.status
        if status is None or self._quality_busy:
            return
        self._quality_busy = True
        prefixes = ("zt",) if self.provider is ProviderId.ZEROTIER else ("tailscale",)

        def work():
            from big_remote_play.utils.connection_health import read_interface_bytes

            sample = self.service_factory().link_sample(status)
            counters = read_interface_bytes(prefixes)
            return sample, (time.monotonic(), *counters) if counters is not None else None

        def done(result) -> None:
            self._quality_busy = False
            self._show_quality(*result)

        def failed(_error) -> None:
            self._quality_busy = False

        self._quality_worker.submit(work, done, failed=failed)

    def _show_quality(self, sample, traffic) -> None:
        from big_remote_play.utils.connection_health import traffic_mbps

        rows = self.quality_rows
        if traffic is not None:
            rates = traffic_mbps(self._traffic, traffic)
            self._traffic = traffic
            if rates is not None:
                rows["traffic"].set_subtitle(_("↓ {down} Mbps · ↑ {up} Mbps").format(down=f"{rates[0]:.1f}", up=f"{rates[1]:.1f}"))
        else:
            rows["traffic"].set_subtitle(_("Not available"))
        if sample.path == "none":
            rows["stability"].set_subtitle(_("No other device online to measure"))
            rows["path"].set_visible(False)
            return
        health = self._latency.add(sample.latency_ms)
        text = _("Measuring…") if health.stable is None else (_("Stable") if health.stable else _("Unstable"))
        if health.latency_ms is not None:
            text = _("{state} · {ms} ms").format(state=text, ms=f"{health.latency_ms:.0f}")
        rows["stability"].set_subtitle(text)
        path = {"direct": _("Direct connection"), "relay": _("Through a relay server, which can be slower")}.get(sample.path, "")
        rows["path"].set_subtitle(path)
        rows["path"].set_visible(bool(path))

    # ── state ──────────────────────────────────────────────────────────────
    def _on_shown(self) -> None:
        self.refresh()
        if not self._timer:
            self._timer = GLib.timeout_add_seconds(REFRESH_SECONDS, self._tick)

    def _tick(self) -> bool:
        if not self.get_mapped():
            self._timer = 0
            return False
        if not self._busy:
            self.refresh(devices=False)
        return True

    def _stop_timer(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _cancel(self) -> None:
        self._stop_timer()
        for worker in (self._status_worker, self._devices_worker, self._action_worker):
            worker.cancel()

    def refresh(self, *, devices: bool = True) -> None:
        provider = self.provider

        def failed(_error) -> None:
            self.main_window.show_toast(_("The state of {name} could not be read.").format(name=provider.display_name))

        self._status_worker.submit(lambda: self.service_factory().status(provider), self._apply_status, failed=failed)
        if devices:
            self.refresh_devices()

    def refresh_devices(self) -> None:
        provider, network_id = self.provider, self.network_id
        if self.listing is None:
            self.devices.replace([loading_row(_("Looking for devices…"))])
        self._devices_worker.submit(
            lambda: self.service_factory().device_listing(provider, network_id),
            self._apply_listing,
            failed=lambda _error: self.devices.replace([message_row(_("The device list could not be read."), _("Select Refresh to try again."), "dialog-warning-symbolic")]),
        )

    def _apply_status(self, status: ProviderStatus) -> None:
        was_connected = self.status is not None and self.status.connected
        self.status = status
        self.state = simple_state(status)
        self._render()
        if status.connected != was_connected:
            self.refresh_devices()
        refresh_header = getattr(self.main_window, "_on_network_page_changed", None)
        if callable(refresh_header):
            refresh_header()

    def _apply_listing(self, listing: DeviceListing) -> None:
        self.listing = listing
        self.network_id = listing.network_id
        self._render_networks(listing)
        self._render_devices(listing)
        self._render_add()

    # ── rendering ──────────────────────────────────────────────────────────
    def _render(self) -> None:
        child = self.badge_slot.get_first_child()
        if child is not None:
            self.badge_slot.remove(child)
        if self.status is None:
            badge = Gtk.Label(label=_("Checking…"), xalign=0)
            badge.add_css_class("dim-label")
            self.badge_slot.append(badge)
            self.connection_row.set_title(_("Checking this computer…"))
            self.connection_row.set_subtitle("")
            self.connection_button.set_visible(False)
            self.ready.set_visible(False)
            self.add_button.set_visible(False)
            self._render_advanced()
            return
        self.badge_slot.append(state_badge(self.state))
        sentence, detail, action = connection_words(self.provider, self.status, self.state)
        self.connection_row.set_title(sentence)
        self.connection_row.set_subtitle(detail)
        self._action = action
        label = action_label(action, self.provider)
        self.connection_button.set_visible(bool(label) and action != "install")
        self.connection_label.set_label(label)
        for css in ("suggested-action", "destructive-action"):
            self.connection_button.remove_css_class(css)
        if action != "stop":
            self.connection_button.add_css_class("suggested-action")
        self.connection_button.set_sensitive(not self._busy)
        self.connection_button.update_property([Gtk.AccessibleProperty.DESCRIPTION], [f"{sentence}. {detail}"])
        self._render_install(action == "install")
        self._sync_quality()
        self._render_ready()
        self._render_add()
        self._render_advanced()

    def _render_install(self, needed: bool) -> None:
        child = self.install_slot.get_first_child()
        if not needed:
            if child is not None:
                self.install_slot.remove(child)
            self.install_slot.set_visible(False)
            return
        self.install_slot.set_visible(True)
        if child is not None:
            return
        from .private_network_view import InstallSection

        self.install_slot.append(InstallSection(self.provider.value, self.main_window, on_installed=self._after_install, on_found=self.refresh, install_label=_("Install and continue")))

    def _after_install(self) -> None:
        """Installed: go on by itself to the step that needed it."""
        self.refresh()
        self.show_setup(auto_start=self.provider is ProviderId.TAILSCALE)

    def _render_ready(self) -> None:
        connected = self.status is not None and self.status.connected
        self.ready.set_visible(connected)
        while child := self.ready_buttons.get_first_child():
            self.ready_buttons.remove(child)
        if not connected:
            return
        target = getattr(self.main_window, "_network_return_page", "")
        choices = [target] if target in ("host", "guest") else ["host", "guest"]
        for page in choices:
            label = (_("Back to Share") if page == "host" else _("Back to Connect")) if target == page else (_("Go to Share") if page == "host" else _("Go to Connect"))
            button = Gtk.Button(label=label)
            if len(choices) == 1:
                button.add_css_class("suggested-action")
            button.connect("clicked", lambda _button, p=page: self._go_to(p))
            self.ready_buttons.append(button)
        for child in list(self._flow_children(self.ready_buttons)):
            child.set_focusable(False)

    @staticmethod
    def _flow_children(flow: Gtk.FlowBox):
        child = flow.get_first_child()
        while child is not None:
            yield child
            child = child.get_next_sibling()

    def _go_to(self, page: str) -> None:
        self.main_window._network_return_page = page
        finish = getattr(self.main_window, "finish_provider", None)
        if callable(finish):
            finish()

    def _render_networks(self, listing: DeviceListing) -> None:
        ids = [choice.network_id for choice in listing.networks]
        show = len(ids) > 1
        self.network_row.set_visible(show)
        if ids != self._network_ids:
            self._network_ids = ids
            self.network_row.set_model(Gtk.StringList.new([choice.name for choice in listing.networks]))
        if listing.network_id in ids and self.network_row.get_selected() != ids.index(listing.network_id):
            self.network_row.set_selected(ids.index(listing.network_id))

    def _on_network_selected(self) -> None:
        index = self.network_row.get_selected()
        if 0 <= index < len(self._network_ids) and self._network_ids[index] != self.network_id:
            self.network_id = self._network_ids[index]
            # The ZeroTier card follows the network chosen here.
            self.main_window.zerotier_network_id = self.network_id
            self.listing = None
            self.refresh_devices()

    def _render_devices(self, listing: DeviceListing) -> None:
        rows: list[Gtk.Widget] = []
        if listing.problem:
            rows.append(message_row(_("The full device list could not be read"), _("Showing what this computer knows. Check API access in Advanced."), "dialog-warning-symbolic"))
        rows.extend(self.device_row(device, listing) for device in listing.devices)
        connected = self.status is not None and self.status.connected
        if not listing.devices:
            if connected or listing.network_id:
                rows.append(message_row(_("No other device yet"), _("Use Add device to connect another computer."), "brp-computer-symbolic"))
            else:
                rows.append(message_row(_("No devices to show"), _("Devices appear here once {name} is on.").format(name=self.provider.display_name), "brp-network-offline-symbolic"))
        elif not listing.complete:
            row = message_row(_("Your other devices are listed on the ZeroTier website"), _("To see and remove them here, add a ZeroTier API token in Advanced."), "brp-network-workgroup-symbolic")
            if listing.manage_url:
                button = Gtk.Button(label=_("Manage devices"), valign=Gtk.Align.CENTER)
                button.connect("clicked", lambda widget: open_uri(widget, listing.manage_url))
                row.add_suffix(button)
            rows.append(row)
        elif connected and len(listing.devices) == 1:
            rows.append(message_row(_("No other device yet"), _("Use Add device to connect another computer."), "brp-computer-symbolic"))
        self.devices.replace(rows)

    def device_row(self, device: ProviderDevice, listing: DeviceListing) -> Adw.ActionRow:
        if device.address and not device.is_local:
            try:
                device = replace(device, name=self.preferences.display_name(device.address, device.name))
            except (OSError, ValueError):
                pass
        row = Adw.ActionRow(title=device.name, subtitle="\n".join(device_lines(device)), use_markup=False)
        row.set_subtitle_lines(0)
        row.set_title_lines(0)
        icon = create_icon_widget("brp-computer-symbolic", size=18, css_class="brp-row-icon")
        icon.set_valign(Gtk.Align.CENTER)
        row.add_prefix(icon)
        if device.address:
            copy = Gtk.Button(label=_("Copy IP"), valign=Gtk.Align.CENTER)
            copy.add_css_class("flat")
            copy.update_property(
                [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION], [_("Copy IP"), _("Copy the address of {name}: {address}").format(name=device.name, address=device.address)]
            )
            copy.connect("clicked", lambda button, value=device.address: copy_to_clipboard(button, value, self.main_window.show_toast))
            row.add_suffix(copy)
        if not device.is_local:
            items: list[tuple[str, Callable[[], object]]] = []
            if device.address and device.online:
                items.append((_("Play on this computer"), lambda d=device: self.play_on(d)))
            if device.address:
                items.append((_("Check connection"), lambda d=device: self.check_connection(d)))
                items.append((_("Rename"), lambda d=device: self.rename(d)))
            if device.removable and device.id:
                items.append((_("Remove device"), lambda d=device: self.confirm_remove(d)))
            elif listing.manage_url:
                items.append((_("Manage devices"), lambda: open_uri(self, listing.manage_url)))
            if items:
                row.add_suffix(_menu_button(_("Actions for {name}").format(name=device.name), items))
        row._brp_device = device  # type: ignore[attr-defined]
        return row

    def play_on(self, device: ProviderDevice) -> None:
        """Connect opens and connects to that computer through this method's address."""
        guest = getattr(self.main_window, "guest_view", None)
        if guest is None:
            return
        self.main_window._network_return_page = "guest"
        self.main_window.navigate_to("guest")
        guest.connect_to_host(self._host(device))

    def _host(self, device: ProviderDevice) -> dict:
        from big_remote_play.integration_contracts import SUNSHINE_DEFAULT_BASE_PORT
        from big_remote_play.private_network.models import HostCandidate

        return HostCandidate(device.name, device.address, SUNSHINE_DEFAULT_BASE_PORT, provider=self.provider.value).as_host()

    def check_connection(self, device: ProviderDevice) -> None:
        """Connect's read-only checks of that computer: the private network, the computer and Sunshine."""
        guest = getattr(self.main_window, "guest_view", None)
        if guest is not None:
            guest.diagnose_host(self._host(device))

    def rename(self, device: ProviderDevice) -> Adw.AlertDialog:
        """A name only this computer uses; the provider's name is unchanged."""
        dialog = Adw.AlertDialog(heading=_("Rename device"), body=_("Only this computer uses the new name."))
        dialog.set_body_use_markup(False)
        entry = Gtk.Entry(text=device.name, activates_default=True)
        entry.update_property([Gtk.AccessibleProperty.LABEL], [_("Name")])
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("save", _("Save"))
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")

        def respond(_dialog, response) -> None:
            if response != "save":
                return
            try:
                self.preferences.rename(device.address, entry.get_text())
            except (OSError, ValueError):
                self.main_window.show_toast(_("Could not save the change."))
                return
            if self.listing is not None:
                self._render_devices(self.listing)

        dialog.connect("response", respond)
        dialog.present(self)
        return dialog

    def _render_add(self) -> None:
        status = self.status
        joined = status is not None and (status.connected or (self.provider is ProviderId.ZEROTIER and bool(status.networks)))
        self.add_button.set_visible(joined)
        if self.approval is not None:
            self.approval.set_visible(joined and self.listing is not None and self.listing.can_remove)

    def _headscale_api(self):
        """The Headscale API with the saved key (called on a worker)."""
        return self.service_factory().headscale_api()

    @staticmethod
    def _headscale_user() -> str:
        try:
            from big_remote_play.private_network.headscale_server import SetupStore

            return SetupStore().load().user
        except Exception:
            return ""

    # ── actions ────────────────────────────────────────────────────────────
    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.connection_button.set_sensitive(not busy)
        self.connection_spinner.set_visible(busy)

    def _run_connection_action(self) -> None:
        action = getattr(self, "_action", "")
        if self._busy:
            return
        if action == "start":
            self._background(
                lambda service: service.start(self.provider), _("{name} is on").format(name=self.provider.display_name), _("{name} could not be started.").format(name=self.provider.display_name)
            )
        elif action == "stop":
            self.confirm_stop()
        elif action == "allow":
            self._background(lambda service: service.grant_zerotier_access().returncode == 0, _("Permission granted"), _("Permission was not granted."))
        elif action == "setup":
            self.show_setup()
        elif action == "sign_in":
            self.show_setup(auto_start=self.provider is ProviderId.TAILSCALE)
        else:
            self.refresh()

    def confirm_stop(self) -> None:
        from .network_common import confirm

        if self.provider is ProviderId.ZEROTIER:
            body = _("Every ZeroTier network on this computer will disconnect. Saved network memberships are kept.")
        else:
            body = _("The active VPN connection will stop. Saved accounts are kept so you can reconnect later.")
        name = self.provider.display_name
        confirm(
            self,
            _("Stop {name}?").format(name=name),
            body,
            _("Stop"),
            lambda: self._background(lambda service: service.stop(self.provider), _("{name} is off").format(name=name), _("{name} could not be stopped.").format(name=name)),
        )

    def confirm_remove(self, device: ProviderDevice) -> None:
        dialog = Adw.AlertDialog(heading=_("Remove {name}?").format(name=device.name), body=_("It will need to be added again before it can connect."))
        dialog.set_body_use_markup(False)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("remove", _("Remove"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _dialog, response: self.remove(device) if response == "remove" else None)
        dialog.present(self)
        return dialog

    def remove(self, device: ProviderDevice) -> None:
        provider, network_id = self.provider, self.network_id

        def done(result) -> None:
            from .network_common import api_error_message

            self.main_window.show_toast(_("{name} was removed").format(name=device.name) if result.ok else api_error_message(result))
            self.refresh_devices()

        self._action_worker.submit(
            lambda: self.service_factory().remove_device(provider, device.id, network_id), done, failed=lambda _error: self.main_window.show_toast(_("The operation failed.")), keep_previous=True
        )

    def _background(self, work: "Callable[[PrivateNetworkService], bool]", success: str, failure: str) -> None:
        self._set_busy(True)

        def done(ok) -> None:
            self._set_busy(False)
            self.main_window.show_toast(success if ok else failure)
            self.changed()

        def failed(_error) -> None:
            self._set_busy(False)
            self.main_window.show_toast(failure)
            self.changed()

        self._action_worker.submit(lambda: bool(work(self.service_factory())), done, failed=failed)

    def show_add_device(self) -> None:
        if self.status is None:
            return
        from .network_devices import AddDeviceFlow

        self.add_device_flow = AddDeviceFlow(
            self, self.navigation, self.service_factory(), self.status, show_toast=self.main_window.show_toast, on_changed=self.refresh_devices, network_id=self.network_id
        )
        self.add_device_flow.present()

    def show_manage(self) -> None:
        if self.status is None:
            return
        from .network_devices import ManageNetworkFlow

        ManageNetworkFlow(self, self.navigation, self.service_factory(), self.status, show_toast=self.main_window.show_toast, on_changed=self.refresh).present()

    # ── navigator for the Set up page (ConnectPage) ────────────────────────
    def show_setup(self, *, auto_start: bool = False, add_account: bool = False, prefill: dict | None = None) -> None:
        from .private_network_view import ConnectPage

        if self.navigation.get_visible_page() is not self:
            self.navigation.pop_to_page(self)
        if self.provider is ProviderId.HEADSCALE:
            # Headscale has its own wizard: where the server runs, its address,
            # DNS, HTTPS, then this computer. It continues where it stopped.
            from .headscale_wizard import HeadscaleWizard

            server = str((prefill or {}).get("domain") or "")
            self.wizard = HeadscaleWizard(self.main_window, self.navigation, service_factory=self.service_factory, on_finished=self.changed, on_restart=self.show_setup)
            self.setup_page = self.wizard.present(server=server)
            return
        connect = ConnectPage(self.provider.value, self.main_window, add_account=add_account, auto_start=auto_start, navigator=self, prefill=prefill)
        title = _("Join a network") if self.provider is ProviderId.ZEROTIER else _("Set up {name}").format(name=self.provider.display_name)
        self.setup_page = Adw.NavigationPage(title=title, child=connect, tag="setup")
        self.navigation.push(self.setup_page)

    def show_details(self) -> None:
        """Back to this method's devices."""
        self.navigation.pop_to_page(self)
        self.view_stack.set_visible_child_name("devices")
        self.refresh()

    def finish(self) -> None:
        self.navigation.pop_to_page(self)
        finish = getattr(self.main_window, "finish_provider", None)
        if callable(finish):
            finish()

    def finish_label(self) -> str:
        label = getattr(self.main_window, "network_return_label", None)
        return str(label()) if callable(label) else ""

    def open_task(self, page: str) -> None:
        self._go_to(page)

    def changed(self) -> None:
        self.refresh()
        refresh = getattr(self.main_window, "_refresh_private_network_status", None)
        if callable(refresh):
            refresh()

    # ── Advanced ───────────────────────────────────────────────────────────
    def _render_advanced(self) -> None:
        status = self.status
        # Rebuilt when what it shows changes, not when a device goes online.
        key = None
        if status is not None:
            me = status.self_device
            key = (
                status.state,
                status.recovery,
                status.reachable_address,
                status.network_name,
                status.account,
                status.technical_detail,
                tuple((n.network_id, n.raw_status) for n in status.networks),
                me.addresses if me else (),
                me.node_id if me else "",
            )
        if key == self._built_advanced_for:
            return
        self._built_advanced_for = key
        while child := self.advanced_box.get_first_child():
            self.advanced_box.remove(child)
        for widget in self._advanced_groups():
            self.advanced_box.append(widget)

    def _advanced_groups(self) -> list[Gtk.Widget]:
        toast = self.main_window.show_toast
        status = self.status
        groups: list[Gtk.Widget] = []
        info = Adw.PreferencesGroup(title=_("This computer on the network"))
        me = status.self_device if status is not None else None
        if status is not None and status.reachable_address:
            info.add(copy_row(_("Private address"), status.reachable_address, toast=toast))
        if me is not None:
            for address in me.addresses:
                if address != (status.reachable_address if status else ""):
                    info.add(copy_row(_("Other address"), address, toast=toast))
            if me.dns_name:
                info.add(copy_row(_("Name on the network"), me.dns_name, icon="brp-network-server-symbolic", toast=toast))
            if me.node_id:
                info.add(copy_row(_("Node ID"), me.node_id, icon="brp-dialog-password-symbolic", toast=toast))
        if status is not None and status.network_name:
            info.add(copy_row(_("Network name"), status.network_name, icon="brp-network-workgroup-symbolic", toast=toast))
        if status is not None and status.account:
            info.add(copy_row(_("Account"), status.account, icon="brp-accounts-symbolic", toast=toast))
        if status is not None:
            for network in status.networks:
                info.add(
                    copy_row(
                        _("Network ID"),
                        network.network_id,
                        icon="brp-zerotier-symbolic",
                        toast=toast,
                        subtitle=" · ".join(part for part in (network.network_id, network.name, network.raw_status) if part),
                    )
                )
        detail = Adw.ActionRow(title=_("Service details"), subtitle=(status.technical_detail if status is not None else "") or _("No details reported."), use_markup=False)
        detail.set_subtitle_lines(0)
        detail.set_subtitle_selectable(True)
        info.add(detail)
        groups.append(info)
        if self.provider is ProviderId.HEADSCALE:
            groups.append(self._headscale_server_group())

        manage = Adw.PreferencesGroup(title=_("Manage"))
        manage.add(action_row(_("Manage network"), _("Every device with approvals, leaving a network and removing members."), "brp-network-workgroup-symbolic", self.show_manage))
        if self.provider is ProviderId.ZEROTIER:
            manage.add(action_row(_("Join another network"), _("This computer can be in several ZeroTier networks."), "brp-network-connect-symbolic", lambda: self.show_setup()))
            manage.add(action_row(_("Create a network"), _("A new private ZeroTier network. Needs a ZeroTier API token."), "brp-network-setup-symbolic", self._create_zerotier_network))
        else:
            manage.add(action_row(_("Use another account"), _("Sign in again with another account or server."), "brp-accounts-symbolic", lambda: self.show_setup(add_account=True)))
        manage.add(action_row(_("API access"), _("Credentials that let Big Remote Play list and remove devices."), "brp-dialog-password-symbolic", self._api_access))
        manage.add(action_row(_("Accounts and networks"), _("Switch saved accounts or leave networks without deleting online accounts."), "brp-accounts-symbolic", self.main_window.show_vpn_accounts))
        groups.append(manage)

        if self.provider is not ProviderId.ZEROTIER:
            from .tailscale_accounts import TailscaleAccountsGroup

            accounts = TailscaleAccountsGroup(
                lambda: self.service_factory().manager,
                on_another_tailnet=lambda: self.show_setup(add_account=True),
                on_changed=self.changed,
                show_toast=toast,
            )
            groups.append(accounts)
            accounts.refresh()

        help_group = Adw.PreferencesGroup(title=_("Help"))
        help_group.add(action_row(_("Internet, router and firewall"), _("CGNAT, IPv6, UPnP and when a private network is the better choice."), "brp-firewall-symbolic", self._network_check))
        if self.provider is ProviderId.HEADSCALE:
            help_group.add(action_row(_("Set up Headscale"), _("Step by step: where the server runs, its address, HTTPS and this computer."), "brp-network-server-symbolic", lambda: self.show_setup()))
        if self.provider is not ProviderId.ZEROTIER:
            shared = Adw.ActionRow(
                title=_("Tailscale and Headscale share the Tailscale app"), subtitle=_("Only one of them can be on at a time on this computer. ZeroTier works alongside either."), use_markup=False
            )
            shared.set_title_lines(0)
            shared.set_subtitle_lines(0)
            help_group.add(shared)
        groups.append(help_group)
        return groups

    # ── Headscale server (Advanced) ────────────────────────────────────────
    def _headscale_server_group(self) -> Gtk.Widget:
        """The server's technical side, read in the background; Test connection in words."""
        group = RowGroup(title=_("Headscale server"), description=_("Technical details. Day-to-day use does not need them."))
        group.replace([loading_row(_("Reading the server details…"))])
        self.headscale_group = group

        def work():
            from big_remote_play.private_network.headscale_server import SetupStore

            service = self.service_factory()
            try:
                server = service.headscale_server()
            except Exception:
                server = ""
            try:
                progress = SetupStore().load()
            except Exception:
                progress = None
            local = progress is not None and progress.mode == "this_computer" and bool(progress.server_url) and (not server or server == progress.server_url)
            server = server or (progress.server_url if progress is not None else "")
            units = {}
            if local:
                import subprocess

                for unit in ("headscale", "caddy"):
                    try:
                        units[unit] = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=3, check=False).stdout.strip() or "unknown"
                    except (OSError, subprocess.SubprocessError):
                        units[unit] = "unknown"
            return server, local, units, progress

        self._action_worker.submit(
            work, self._show_headscale_server, failed=lambda _error: group.replace([message_row(_("The server details could not be read."), "", "dialog-warning-symbolic")]), keep_previous=True
        )
        return group

    def _show_headscale_server(self, result) -> None:
        server, local, units, progress = result
        toast = self.main_window.show_toast
        rows: list[Gtk.Widget] = []
        self._headscale_url = server
        self._headscale_local = local
        if server:
            rows.append(copy_row(_("Server address"), server, icon="brp-network-server-symbolic", toast=toast))
            rows.append(self._fact_row("server_url", server))
            rows.append(copy_row(_("API endpoint"), f"{server}/api/v1", icon="brp-text-x-generic-symbolic", toast=toast))
            rows.append(action_row(_("Test connection"), _("Checks the name, the port, HTTPS and Headscale itself."), "brp-view-refresh-symbolic", self.test_headscale))
        if progress is not None and progress.started and not progress.complete:
            rows.append(action_row(_("Continue setup"), _("The Headscale setup is not finished."), "brp-network-server-symbolic", lambda: self.show_setup()))
        if local:
            facts = (
                ("listen_addr", "127.0.0.1:8080"),
                (_("Configuration file"), "/etc/headscale/config.yaml"),
                (_("HTTPS (Caddy)"), "/etc/caddy/conf.d/big-remote-play-headscale.caddy"),
                (_("Database"), "/var/lib/headscale/db.sqlite"),
                ("DERP", _("Tailscale's public relays (UDP 3478 not used)")),
                ("systemd", ", ".join(f"{unit}: {state}" for unit, state in units.items())),
            )
            rows.extend(self._fact_row(title, value) for title, value in facts)
            rows.append(action_row(_("Server log"), _("The last lines Headscale wrote."), "brp-text-x-generic-symbolic", self._headscale_log))
            rows.append(
                action_row(
                    _("Remove the server setup"), _("Restores Headscale's original settings and stops it. Your devices stay listed on the server."), "user-trash-symbolic", self._confirm_unconfigure
                )
            )
        if not rows:
            rows.append(message_row(_("No Headscale server set up yet."), "", "brp-network-server-symbolic"))
            rows.append(action_row(_("Set up Headscale"), _("Step by step: where the server runs, its address, HTTPS and this computer."), "brp-network-server-symbolic", lambda: self.show_setup()))
        self._headscale_rows = rows
        self.headscale_group.replace(rows)

    @staticmethod
    def _fact_row(title: str, value: str) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title, subtitle=value, use_markup=False)
        row.set_subtitle_selectable(True)
        row.set_subtitle_lines(0)
        return row

    def test_headscale(self) -> None:
        """Test connection: one human sentence, the technical reason underneath."""
        from big_remote_play.private_network.headscale_server import check_server

        from .headscale_wizard import server_problem_words

        url = getattr(self, "_headscale_url", "")
        connect_host = "127.0.0.1" if getattr(self, "_headscale_local", False) else ""
        if not url:
            return
        base = list(getattr(self, "_headscale_rows", []))
        self.headscale_group.replace([*base, loading_row(_("Testing the connection…"))])

        def done(check) -> None:
            if check.healthy:
                title = _("The Headscale server works.")
                subtitle = " · ".join(part for part in (check.version and _("Headscale {version}").format(version=check.version), check.certificate_issuer, check.certificate_expires) if part)
                icon = "brp-emblem-ok-symbolic"
            else:
                title, subtitle = server_problem_words(check)
                icon = "dialog-warning-symbolic"
            expander = Adw.ExpanderRow(title=_("Technical details"), use_markup=False)
            detail = Adw.ActionRow(title="\n".join(check.details) or _("No details reported."), use_markup=False)
            detail.set_title_lines(0)
            detail.set_title_selectable(True)
            expander.add_row(detail)
            self.test_outcome = title
            self.headscale_group.replace([*base, message_row(title, subtitle, icon), expander])

        self._action_worker.submit(lambda: check_server(url, connect_host=connect_host), done, keep_previous=True)

    def _headscale_log(self) -> None:
        import subprocess

        from .components import content_page
        from .network_common import push_page

        label = Gtk.Label(label=_("Reading…"), xalign=0, wrap=True, selectable=True)
        label.add_css_class("monospace")
        push_page(self.navigation, content_page(_("Server log"), label, description=_("journalctl -u headscale"), tag="headscale-log"))

        def work():
            try:
                result = subprocess.run(["journalctl", "-u", "headscale", "-n", "80", "--no-pager", "-o", "short-iso"], capture_output=True, text=True, timeout=5, check=False)
            except (OSError, subprocess.SubprocessError):
                return ""
            return result.stdout.strip()

        def done(text: str) -> None:
            from big_remote_play.private_network.redaction import redact

            label.set_label(redact(text) if text else _("The log could not be read. Your user may need to be in the systemd-journal group."))

        self._action_worker.submit(work, done, keep_previous=True)

    def _confirm_unconfigure(self) -> None:
        from .network_common import confirm

        def run() -> None:
            def work():
                from big_remote_play.private_network.headscale_server import LocalServer, SetupStore

                result = LocalServer().run("unconfigure")
                if result.ok:
                    SetupStore().clear()
                return result.ok

            self._action_worker.submit(
                work, lambda ok: (self.main_window.show_toast(_("The server setup was removed.") if ok else _("The server setup could not be removed.")), self.changed()), keep_previous=True
            )

        confirm(self, _("Remove the server setup?"), _("Headscale's original settings come back and it stops. Devices on this server will no longer connect."), _("Remove"), run)

    # Pages of Connect your devices, pushed by the main window.
    def _api_access(self) -> None:
        self.main_window.show_api_access(focus=self.provider.value, on_changed=self.refresh)

    def _network_check(self) -> None:
        self.main_window.show_internet_check()

    def _create_zerotier_network(self) -> None:
        entry = Adw.EntryRow(title=_("Network name"), use_markup=False)
        entry.set_text(_("Gaming network"))
        group = Adw.PreferencesGroup(description=_("The network is private: every new device must be approved. This computer joins and is approved automatically."))
        group.add(entry)
        dialog = Adw.AlertDialog(heading=_("Create a ZeroTier network"))
        dialog.set_extra_child(group)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("create", _("Create network"))
        dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("create")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _dialog, response: self._run_create(entry.get_text()) if response == "create" else None)
        dialog.present(self)

    def _run_create(self, name: str) -> None:
        from big_remote_play.private_network.diagnostics import local_network_facts
        from big_remote_play.private_network.zerotier_api import choose_subnet

        from .network_common import api_error_message

        def work():
            service = self.service_factory()
            central = service.zerotier_central()
            if central is None:
                return None, None
            facts = local_network_facts()
            status = service.status(ProviderId.ZEROTIER)
            in_use = [*facts.ipv4, *(address for network in status.networks for address in network.addresses)]
            network, result = central.create_network(name, choose_subnet([f"{address}/24" for address in in_use]))
            if network is None:
                return None, result
            joined = service.join_zerotier(network.network_id)
            node = service.manager.zerotier_info()
            if joined.returncode == 0 and node.address:
                time.sleep(2)  # joining registers the member; approving it is then allowed
                central.set_authorized(network.network_id, node.address, True)
            service.manager.set_zerotier_name(network.network_id, network.name or name)
            return network, result

        def done(value) -> None:
            network, result = value
            if network is None:
                self.main_window.show_toast(api_error_message(result) if result is not None else _("Add a ZeroTier API token first."))
            else:
                self.main_window.show_toast(_("Network created"))
            self.changed()

        self._action_worker.submit(work, done, failed=lambda _error: self.main_window.show_toast(_("The network could not be created.")), keep_previous=True)


__all__ = ["ProviderPage", "connection_words", "device_lines", "last_seen_text", "method_summary", "method_tag", "state_text"]
