"""“Connect your devices”: the one page for playing over the internet.

The page talks about the task. It looks at what already works on this
computer, says it in one sentence and offers one primary button; the network
product appears only as “Using …” and, in Advanced mode, with its details.
Every blocking call runs on a :class:`Worker` and only current results are
applied, so leaving the page or pressing a button twice is always safe.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import time
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango  # type: ignore

from big_remote_play.integration_contracts import SUNSHINE_DEFAULT_BASE_PORT
from big_remote_play.private_network.devices import DevicePreferences, device_key
from big_remote_play.private_network.models import ConnectionState, HostCandidate, PeerDevice, ProviderId
from big_remote_play.private_network.plan import ConnectionPlan, PlanKind, plan_connection
from big_remote_play.utils.connection_health import SAMPLE_INTERVAL_SECONDS, LatencyWindow, LinkSample, read_interface_bytes, traffic_mbps
from big_remote_play.utils.i18n import _
from big_remote_play.utils.icons import create_icon_widget, set_icon

from .components import action_row, content_dialog, icon_tile, intro

if TYPE_CHECKING:
    from big_remote_play.private_network.service import PrivateNetworkService

from .network_common import PROVIDER_ICONS, RowGroup, Worker, confirm, copy_row, message_row, state_pill
from .service_status_card import PILL_TONES, checking_presentation, provider_presentation

# Plain descriptions for the method dialog; the product name is the title.
METHOD_DESCRIPTIONS = {
    ProviderId.TAILSCALE: lambda: _("Easy to set up and great for connecting your own devices."),
    ProviderId.ZEROTIER: lambda: _("Creates a private network between several devices."),
    ProviderId.HEADSCALE: lambda: _("For people who run their own network server."),
}


def plan_words(plan: ConnectionPlan) -> tuple[str, str, str, str, str]:
    """``(icon, tone, title, body, action)`` for the status card; ``action`` may be ``""``."""
    kind = plan.kind
    if kind is PlanKind.CHECKING:
        return "brp-network-idle-symbolic", "offline", _("Checking this computer…"), _("Looking for a connection that already works."), ""
    if kind is PlanKind.OFFLINE:
        return "brp-network-offline-symbolic", "attention", _("This computer is not connected to the internet"), _("Connect to Wi-Fi or a network cable, then check again."), _("Check again")
    if kind is PlanKind.READY:
        count = plan.online_devices
        body = _("Your other devices can now connect to this computer.") if count == 0 else _("Devices online now: {count}").format(count=count)
        return "brp-emblem-ok-symbolic", "online", _("Ready to play over the internet"), body, _("Add a device or invite a player")
    if kind is PlanKind.WAITING:
        return "brp-network-idle-symbolic", "attention", _("Waiting for approval"), _("The owner of the network needs to allow this computer. Then it connects by itself."), _("Check again")
    if kind is PlanKind.TURNED_OFF:
        return "brp-network-offline-symbolic", "offline", _("Your secure connection is turned off"), _("This computer is already set up. Turn it on to play over the internet."), _("Turn on")
    if kind is PlanKind.SERVICE_STOPPED:
        return "dialog-warning-symbolic", "attention", _("The connection service is stopped"), _("Big Remote Play can start it for you. Your password may be requested."), _("Fix")
    if kind is PlanKind.ALLOW:
        return "dialog-warning-symbolic", "attention", _("Big Remote Play needs your permission"), _("Allow it once to see and use your networks. Your password may be requested."), _("Allow")
    if kind is PlanKind.SIGN_IN:
        return "brp-accounts-symbolic", "attention", _("Sign in to connect this computer"), _("Your browser opens. Use the same account on all your devices."), _("Sign in")
    if kind is PlanKind.INSTALL:
        return "brp-network-private-symbolic", "offline", _("One component is needed"), _("Big Remote Play installs it for you. Your password may be requested."), _("Install")
    if kind is PlanKind.PROBLEM:
        return "dialog-warning-symbolic", "attention", _("We need to fix a setting"), _("Open the connection details to see what is wrong."), _("See details")
    return "brp-network-private-symbolic", "offline", _("Set up a secure connection"), _("It takes about a minute. Then add your other devices."), _("Set up")


def when_text(timestamp: float | None, *, now: float | None = None) -> str:
    """“Today at 20:42”, “Yesterday at 20:42” or a local date."""
    if not timestamp:
        return ""
    moment = GLib.DateTime.new_from_unix_local(int(timestamp))
    today = GLib.DateTime.new_from_unix_local(int(now if now is not None else time.time()))
    clock = moment.format("%H:%M") or ""
    days = (
        GLib.DateTime.new_local(today.get_year(), today.get_month(), today.get_day_of_month(), 0, 0, 0).to_unix()
        - GLib.DateTime.new_local(moment.get_year(), moment.get_month(), moment.get_day_of_month(), 0, 0, 0).to_unix()
    ) // 86400
    if days == 0:
        return _("Today at {time}").format(time=clock)
    if days == 1:
        return _("Yesterday at {time}").format(time=clock)
    return moment.format("%x") or ""


def peer_state_text(peer: PeerDevice) -> str:
    if peer.expired:
        return _("Needs to sign in again")
    if peer.online is True:
        return _("Online")
    if peer.online is False:
        return _("Offline")
    return ""


def peer_icon(peer: PeerDevice) -> str:
    system = (peer.os or "").lower()
    if system in ("android", "ios", "iphone", "ipados"):
        return "phone-symbolic"
    if system in ("tvos", "androidtv"):
        return "brp-video-display-symbolic"
    return "brp-computer-symbolic"


def _menu_button(description: str, items: Sequence[tuple[str, Callable[[], object]]]) -> Gtk.MenuButton:
    """A ⋮ button whose popover lists plain actions (keyboard and screen-reader friendly)."""
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


def _favorite_button(name: str, favorite: bool, toggle: Callable[[bool], None]) -> Gtk.ToggleButton:
    button = Gtk.ToggleButton(active=favorite, valign=Gtk.Align.CENTER)
    button.add_css_class("flat")
    button.set_child(create_icon_widget("starred-symbolic" if favorite else "non-starred-symbolic", size=16))
    label = _("Remove {name} from favorites").format(name=name) if favorite else _("Add {name} to favorites").format(name=name)
    button.set_tooltip_text(_("Favorite"))
    button.update_property([Gtk.AccessibleProperty.LABEL], [label])
    button.connect("toggled", lambda widget: toggle(widget.get_active()))
    return button


class RemoteConnectionPage(Gtk.ScrolledWindow):
    """The “Play over the internet” page."""

    __gtype_name__ = "BrpRemoteConnectionPage"

    def __init__(self, main_window, *, service_factory: "Callable[[], PrivateNetworkService] | None" = None, preferences: DevicePreferences | None = None) -> None:
        super().__init__(vexpand=True)
        from big_remote_play.private_network.service import default_service

        self.main_window = main_window
        self.service_factory = service_factory or default_service
        self.preferences = preferences or DevicePreferences()
        self.plan = plan_connection(None)
        self._statuses: list = []
        self._status_worker = Worker()
        self._action_worker = Worker()
        self._recent_worker = Worker()
        self._link_worker = Worker()
        self._link_timer = 0
        self._link_busy = False
        self._link_provider: ProviderId | None = None
        self._link_window = LatencyWindow()
        self._traffic: tuple[float, int, int] | None = None
        self._busy = False
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._build()
        self._render_plan()
        self.connect("map", lambda *_args: self.refresh())
        self.connect("unmap", lambda *_args: self._stop_link())
        self.connect("unrealize", lambda *_args: self._cancel())

    # ── layout ──────────────────────────────────────────────────────────────
    def _build(self) -> None:
        clamp = Adw.Clamp(maximum_size=820, tightening_threshold=560)
        for edge in ("top", "bottom", "start", "end"):
            getattr(clamp, f"set_margin_{edge}")(24)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        box.append(intro(_("Connect your devices"), _("Play or use this computer from anywhere, with a secure connection."), "brp-network-private-symbolic"))

        # Status card: one sentence, one primary action.
        self.card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.card.add_css_class("network-hero")
        top = Gtk.Box(spacing=16)
        self.card_icon = Gtk.Box()
        top.append(self.card_icon)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True, valign=Gtk.Align.CENTER)
        self.card_title = Gtk.Label(xalign=0, wrap=True)
        self.card_title.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.card_title.add_css_class("title-3")
        self.card_body = Gtk.Label(xalign=0, wrap=True)
        self.card_body.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.card_body.add_css_class("dim-label")
        texts.append(self.card_title)
        texts.append(self.card_body)
        top.append(texts)
        self.card.append(top)
        # Wraps onto a second line in narrow windows instead of clipping.
        actions = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=2, min_children_per_line=1, homogeneous=False, halign=Gtk.Align.START)
        actions.set_column_spacing(12)
        actions.set_row_spacing(8)
        self.primary = Gtk.Button()
        self.primary.add_css_class("suggested-action")
        self.primary.add_css_class("brp-primary")
        inner = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        self.primary_spinner = Adw.Spinner(visible=False)
        # Long translations wrap inside the button instead of widening the page.
        self.primary_label = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.primary_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.primary_label.set_natural_wrap_mode(Gtk.NaturalWrapMode.NONE)
        inner.append(self.primary_spinner)
        inner.append(self.primary_label)
        self.primary.set_child(inner)
        self.primary.set_size_request(180, -1)
        self.primary.connect("clicked", lambda _button: self._run_primary())
        actions.append(self.primary)
        # "Using Tailscale" is information; "Change" is the only control.
        self.method_box = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
        self.method_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.method_label.add_css_class("caption")
        self.method_label.add_css_class("dim-label")
        self.method_box.append(self.method_label)
        self.method_button = Gtk.Button(label=_("Change"), valign=Gtk.Align.CENTER)
        self.method_button.add_css_class("flat")
        self.method_button.connect("clicked", lambda _button: self.show_methods())
        self.method_box.append(self.method_button)
        actions.append(self.method_box)
        for child in (self.primary.get_parent(), self.method_box.get_parent()):
            child.set_focusable(False)  # the buttons inside are the tab stops
        self.card.append(actions)
        self.install_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.card.append(self.install_slot)
        # Live region: a screen reader hears the new status when it changes.
        self.card.set_accessible_role(Gtk.AccessibleRole.STATUS)
        box.append(self.card)
        box.append(self._build_link_card())

        # Share and Connect are in the sidebar, always one click away; once
        # this computer is ready the page says where to go next instead.
        self.tasks = Adw.PreferencesGroup(title=_("Ready to play?"))
        self.share_row = action_row(_("Share this computer"), _("Let another device play on this computer."), "brp-host-symbolic", lambda: self.main_window.navigate_to("host"))
        self.connect_row = action_row(_("Connect to another computer"), _("Play on a computer that is already set up."), "brp-client-symbolic", lambda: self.main_window.navigate_to("guest"))
        for row in (self.share_row, self.connect_row):
            self.tasks.add(row)
        self.tasks.set_visible(False)
        box.append(self.tasks)

        self.devices = RowGroup(title=_("My devices"))
        box.append(self.devices)
        self.recent = RowGroup(title=_("Recent connections"))
        box.append(self.recent)

        mode = Adw.PreferencesGroup()
        self.advanced_row = Adw.SwitchRow(title=_("Advanced mode"), subtitle=_("Shows network details and tools for experienced users."), use_markup=False)
        self.advanced_row.set_active(bool(getattr(self.main_window, "network_advanced_mode", False)))
        self.advanced_row.connect("notify::active", self._advanced_changed)
        mode.add(self.advanced_row)
        box.append(mode)

        self.advanced_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        self.methods_group = RowGroup(title=_("Connection methods"), description=_("Choose a method to see its sign-in, network and device details."))
        self.advanced_box.append(self.methods_group)
        self.details_group = RowGroup(title=_("This network"))
        self.advanced_box.append(self.details_group)
        tools = Adw.PreferencesGroup(title=_("Tools"))
        window = self.main_window
        for title, subtitle, icon, callback in (
            (_("Accounts and networks"), _("Switch Tailscale or Headscale accounts, and manage several ZeroTier networks."), "brp-accounts-symbolic", getattr(window, "show_vpn_accounts", None)),
            (_("API access"), _("Credentials that let Big Remote Play manage your networks."), "brp-dialog-password-symbolic", getattr(window, "show_api_access", None)),
            (_("Internet, router and firewall"), _("CGNAT, IPv6, UPnP and when a private network is the better choice."), "brp-firewall-symbolic", getattr(window, "show_internet_check", None)),
            (
                _("Without a private network"),
                _("Direct connection with a domain and router ports. Greater exposure to the internet."),
                "brp-address-symbolic",
                getattr(window, "show_direct_internet_guide", None),
            ),
        ):
            if callable(callback):

                def open_tool(run: Callable[[], object] = callback) -> None:
                    run()

                tools.add(action_row(title, subtitle, icon, open_tool))
        self.advanced_box.append(tools)
        box.append(self.advanced_box)
        self._sync_advanced()

        clamp.set_child(box)
        self.set_child(clamp)

    def _build_link_card(self) -> Gtk.Widget:
        """The connection in use: provider, state, traffic now and stability."""
        card = self.link_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, visible=False)
        card.add_css_class("card")
        card.add_css_class("padded")
        card.set_accessible_role(Gtk.AccessibleRole.STATUS)
        top = Gtk.Box(spacing=12)
        self.link_icon = create_icon_widget("brp-network-private-symbolic", size=24)
        self.link_icon.set_valign(Gtk.Align.CENTER)
        top.append(self.link_icon)
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, valign=Gtk.Align.CENTER)
        self.link_title = Gtk.Label(xalign=0, wrap=True)
        self.link_title.add_css_class("heading")
        self.link_network = Gtk.Label(xalign=0, wrap=True)
        self.link_network.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.link_network.add_css_class("caption")
        self.link_network.add_css_class("dim-label")
        names.append(self.link_title)
        names.append(self.link_network)
        top.append(names)
        self.link_state = Gtk.Box()
        self.link_state.set_valign(Gtk.Align.CENTER)
        top.append(self.link_state)
        card.append(top)
        self.link_values: dict[str, Gtk.Label] = {}
        for key, title in (("traffic", _("In use now")), ("stability", _("Network")), ("path", _("Path"))):
            line = Gtk.Box(spacing=12)
            name = Gtk.Label(label=title, xalign=0)
            name.add_css_class("dim-label")
            name.set_size_request(110, -1)
            value = Gtk.Label(xalign=0, wrap=True, hexpand=True)
            value.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            value.add_css_class("numeric")
            line.append(name)
            line.append(value)
            card.append(line)
            self.link_values[key] = value
            value._brp_line = line  # type: ignore[attr-defined]
        note = Gtk.Label(label=_("Traffic is what this computer sends and receives through the secure connection now, not its maximum speed."), xalign=0, wrap=True)
        note.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        note.add_css_class("caption")
        note.add_css_class("dim-label")
        card.append(note)
        return card

    # ── data ───────────────────────────────────────────────────────────────
    def _cancel(self) -> None:
        self._stop_link()
        for worker in (self._status_worker, self._recent_worker, self._link_worker):
            worker.cancel()

    def refresh(self) -> None:
        """Read every method's state (off the GTK thread) and redraw."""
        preferred = self._preferred()
        self.refresh_recent()

        def load():
            service = self.service_factory()
            statuses = service.overview()
            connected = any(status.connected for status in statuses)
            internet = True if connected else service.internet_available()
            return statuses, internet

        def apply(result) -> None:
            statuses, internet = result
            self._statuses = list(statuses)
            self.plan = plan_connection(statuses, internet=internet, preferred=preferred)
            self._render_plan()

        def failed(_error) -> None:
            self.plan = ConnectionPlan(PlanKind.PROBLEM, preferred or ProviderId.TAILSCALE)
            self._render_plan()

        self._status_worker.submit(load, apply, failed=failed)

    def refresh_recent(self) -> None:
        def load():
            return self.service_factory().recent_hosts(5), self.preferences.all()

        self._recent_worker.submit(load, lambda result: self._render_recent(*result), failed=lambda _error: self._render_recent([], {}))

    def _preferred(self) -> ProviderId | None:
        try:
            return ProviderId(self.main_window._vpn_choice) if getattr(self.main_window, "_vpn_choice", None) else None
        except ValueError:
            return None

    # ── rendering ──────────────────────────────────────────────────────────
    def _render_plan(self) -> None:
        plan = self.plan
        icon, tone, title, body, action = plan_words(plan)
        child = self.card_icon.get_first_child()
        if child is not None:
            self.card_icon.remove(child)
        self.card_icon.append(icon_tile(icon, large=True, tone="guest" if tone == "online" else "accent"))
        self.card_title.set_label(title)
        self.card_body.set_label(body)
        self.card.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION], [title, body])
        self.primary_label.set_label(action)
        self.primary.get_parent().set_visible(bool(action) and plan.kind is not PlanKind.INSTALL)
        self.primary.set_visible(bool(action) and plan.kind is not PlanKind.INSTALL)
        self.primary.set_sensitive(not self._busy)
        checking = plan.kind is PlanKind.CHECKING
        self.method_box.get_parent().set_visible(not checking and plan.kind is not PlanKind.OFFLINE)
        self.method_label.set_label(_("Using {method}").format(method=plan.provider.display_name))
        self.method_button.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION], [_("Change method"), _("Now using {method}.").format(method=plan.provider.display_name)])
        # One next step at a time: where to play appears once the connection works.
        self.tasks.set_visible(plan.kind is PlanKind.READY)
        self._render_install()
        self._render_devices()
        self._render_advanced()
        self._render_link()

    def _render_install(self) -> None:
        child = self.install_slot.get_first_child()
        while child is not None:
            self.install_slot.remove(child)
            child = self.install_slot.get_first_child()
        if self.plan.kind is not PlanKind.INSTALL:
            return
        from .private_network_view import InstallSection

        self.install_slot.append(InstallSection(self.plan.provider.value, self.main_window, on_installed=self._continue_after_install, on_found=self.refresh))

    def _continue_after_install(self) -> None:
        """Installed from here: go on to the step that needed it, by itself."""
        provider = self.plan.provider
        if provider is ProviderId.TAILSCALE:
            self.main_window._apply_vpn_selection(provider.value, destination="connect_private", auto_start=True)
        elif provider is ProviderId.ZEROTIER:
            self.main_window._apply_vpn_selection(provider.value, destination="connect_private")
        else:
            self.refresh()

    def _render_devices(self) -> None:
        status = self.plan.status if self.plan.kind is PlanKind.READY else None
        self.devices.set_visible(status is not None)
        if status is None:
            self.devices.replace([])
            return
        favorites = self.preferences.all()

        def order(peer: PeerDevice) -> tuple[bool, bool, str]:
            pref = favorites.get(device_key(peer.best_address))
            return (not (pref is not None and pref.favorite), peer.online is not True, (peer.name or "").lower())

        peers = sorted(status.peers, key=order)
        if not peers:
            if status.provider is ProviderId.ZEROTIER:
                # The local ZeroTier service knows its networks, not their members.
                empty = message_row(
                    _("Your devices are listed on the ZeroTier website"), _("To see them here, add a ZeroTier API token under Advanced mode → API access."), "brp-network-connect-symbolic"
                )
            else:
                empty = message_row(_("No other device yet"), _("Add your phone, notebook, TV or another computer."), "brp-network-connect-symbolic")
            add = Gtk.Button(label=_("Add a device"), valign=Gtk.Align.CENTER)
            add.connect("clicked", lambda _button: self.show_add_device())
            empty.add_suffix(add)
            self.devices.replace([empty])
            return
        self.devices.replace([self._device_row(peer, status.provider) for peer in peers[:12]])

    def _device_row(self, peer: PeerDevice, provider: ProviderId) -> Adw.ActionRow:
        address = peer.best_address
        name = self.preferences.display_name(address, peer.name or address)
        row = Adw.ActionRow(title=name, subtitle=peer_state_text(peer), use_markup=False)
        row.set_subtitle_lines(0)
        image = create_icon_widget(peer_icon(peer), size=18, css_class="brp-row-icon")
        image.set_valign(Gtk.Align.CENTER)
        row.add_prefix(image)
        host = HostCandidate(name, address, SUNSHINE_DEFAULT_BASE_PORT, provider=provider.value, online=peer.online, dns_name=peer.dns_name, addresses=peer.addresses)
        if address:
            row.add_suffix(_favorite_button(name, self.preferences.get(address).favorite, lambda on, a=address: self._set_favorite(a, on)))
        items = [(_("Details"), lambda: self._show_peer_details(peer, provider, name)), (_("Rename"), lambda: self._rename(address, name))]
        if address:
            items.insert(0, (_("Connect"), lambda: self._connect(host)))
            items.append((_("Check connection"), lambda: self._diagnose(host)))
        row.add_suffix(_menu_button(_("Actions for {name}").format(name=name), items))
        return row

    def _render_recent(self, hosts: list[HostCandidate], favorites: dict) -> None:
        if not hosts:
            self.recent.replace([message_row(_("No connections yet"), _("Your connections appear here after you start playing with Big Remote Play."), "brp-document-open-recent-symbolic")])
            return

        def is_favorite(host: HostCandidate) -> bool:
            pref = favorites.get(device_key(host.address))
            return pref is not None and pref.favorite

        ordered = sorted(hosts, key=lambda host: not is_favorite(host))
        self.recent.replace([self._recent_row(host, favorites) for host in ordered])

    def _recent_row(self, host: HostCandidate, favorites: dict) -> Adw.ActionRow:
        pref = favorites.get(device_key(host.address))
        name = pref.name if pref is not None and pref.name else host.name
        when = when_text(host.last_connected)
        row = Adw.ActionRow(title=name, subtitle=_("Last connection: {when}").format(when=when) if when else "", use_markup=False)
        row.set_subtitle_lines(0)
        image = create_icon_widget("brp-computer-symbolic", size=18, css_class="brp-row-icon")
        image.set_valign(Gtk.Align.CENTER)
        row.add_prefix(image)
        favorite = bool(pref is not None and pref.favorite)
        row.add_suffix(_favorite_button(name, favorite, lambda on, a=host.address: self._set_favorite(a, on)))
        connect = Gtk.Button(label=_("Connect"), valign=Gtk.Align.CENTER)
        connect.update_property([Gtk.AccessibleProperty.DESCRIPTION], [_("Connect to {name} again").format(name=name)])
        connect.connect("clicked", lambda _button: self._connect(host))
        row.add_suffix(connect)
        row.add_suffix(
            _menu_button(
                _("Actions for {name}").format(name=name),
                [
                    (_("Rename"), lambda: self._rename(host.address, name)),
                    (_("Check connection"), lambda: self._diagnose(host)),
                    (_("Remove from recent connections"), lambda: self._forget(host, name)),
                ],
            )
        )
        return row

    def _render_advanced(self) -> None:
        if not self.advanced_row.get_active():
            return
        rows = []
        for status in self._statuses:
            description = METHOD_DESCRIPTIONS[status.provider]()
            rows.append(
                action_row(
                    status.provider.display_name, description, PROVIDER_ICONS[status.provider], lambda p=status.provider: self._open_method(p), icon_style="tile", suffix=state_pill(status.state)
                )
            )
        self.methods_group.replace(rows)
        details = []
        status = self.plan.status
        if status is not None and status.connected:
            if status.network_name or status.network_id:
                details.append(copy_row(_("Network"), status.network_name or status.network_id, icon="brp-network-workgroup-symbolic", toast=self.main_window.show_toast))
            if status.reachable_address:
                details.append(copy_row(_("This computer's address"), status.reachable_address, toast=self.main_window.show_toast))
            details.append(
                action_row(
                    _("Network details"),
                    _("Everything this method reports, and its maintenance actions."),
                    "brp-network-setup-symbolic",
                    lambda: self.main_window._apply_vpn_selection(status.provider.value, destination="create_private"),
                )
            )
            details.append(action_row(_("Manage network"), _("Every device on the network, approvals and removal."), "brp-network-workgroup-symbolic", self.show_manage))
        self.details_group.replace(details)
        self.details_group.set_visible(bool(details))

    def _advanced_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        setter = getattr(self.main_window, "set_network_advanced_mode", None)
        if callable(setter):
            setter(bool(row.get_active()))
        self._sync_advanced()
        self._render_advanced()

    def _sync_advanced(self) -> None:
        self.advanced_box.set_visible(self.advanced_row.get_active())

    # ── actions ────────────────────────────────────────────────────────────
    # ── the connection in use ──────────────────────────────────────────────
    def _render_link(self) -> None:
        status = self.plan.status if self.plan.kind is PlanKind.READY else None
        self.link_card.set_visible(status is not None)
        if status is None:
            self._stop_link()
            return
        if status.provider is not self._link_provider:
            self._link_provider = status.provider
            self._link_window.clear()
            self._traffic = None
            for value in self.link_values.values():
                value.set_label(_("Measuring…"))
            self.link_values["path"]._brp_line.set_visible(False)  # type: ignore[attr-defined]
        set_icon(self.link_icon, PROVIDER_ICONS[status.provider])
        self.link_title.set_label(status.provider.display_name)
        network = status.network_name or status.account
        self.link_network.set_label(network)
        self.link_network.set_visible(bool(network))
        child = self.link_state.get_first_child()
        if child is not None:
            self.link_state.remove(child)
        self.link_state.append(state_pill(ConnectionState.CONNECTED))
        if self.get_mapped() and not self._link_timer:
            self._sample_link()
            self._link_timer = GLib.timeout_add_seconds(SAMPLE_INTERVAL_SECONDS, self._link_tick)

    def _link_tick(self) -> bool:
        if not self.get_mapped() or self.plan.kind is not PlanKind.READY:
            self._link_timer = 0
            return False
        self._sample_link()
        return True

    def _stop_link(self) -> None:
        if self._link_timer:
            GLib.source_remove(self._link_timer)
            self._link_timer = 0
        self._link_worker.cancel()
        self._link_busy = False

    def _sample_link(self) -> None:
        status = self.plan.status
        if status is None or self._link_busy:
            return
        self._link_busy = True
        prefixes = ("zt",) if status.provider is ProviderId.ZEROTIER else ("tailscale",)

        def work():
            sample = self.service_factory().link_sample(status)
            counters = read_interface_bytes(prefixes)
            return sample, (time.monotonic(), *counters) if counters is not None else None

        def done(result) -> None:
            self._link_busy = False
            sample, traffic = result
            self._show_link(sample, traffic)

        def failed(_error) -> None:
            self._link_busy = False

        self._link_worker.submit(work, done, failed=failed)

    def _show_link(self, sample: LinkSample, traffic: tuple[float, int, int] | None) -> None:
        values = self.link_values
        if traffic is not None:
            rates = traffic_mbps(self._traffic, traffic)
            self._traffic = traffic
            if rates is not None:
                values["traffic"].set_label(_("↓ {down} Mbps · ↑ {up} Mbps").format(down=f"{rates[0]:.1f}", up=f"{rates[1]:.1f}"))
        else:
            values["traffic"].set_label(_("Not available"))
        if sample.path == "none":
            values["stability"].set_label(_("No other device online to measure"))
            values["path"]._brp_line.set_visible(False)  # type: ignore[attr-defined]
            return
        health = self._link_window.add(sample.latency_ms)
        if health.stable is None:
            text = _("Measuring…")
        else:
            text = _("Stable") if health.stable else _("Unstable")
        if health.latency_ms is not None:
            text = _("{state} · {ms} ms").format(state=text, ms=f"{health.latency_ms:.0f}")
        values["stability"].set_label(text)
        path = {"direct": _("Direct connection"), "relay": _("Through a relay server, which can be slower")}.get(sample.path, "")
        values["path"].set_label(path)
        values["path"]._brp_line.set_visible(bool(path))  # type: ignore[attr-defined]
        self.link_card.update_property([Gtk.AccessibleProperty.DESCRIPTION], [f"{values['traffic'].get_label()}. {text}. {path}"])

    def _set_busy(self, busy: bool, label: str = "") -> None:
        self._busy = busy
        self.primary.set_sensitive(not busy)
        self.primary_spinner.set_visible(busy)
        if busy and label:
            self.primary_label.set_label(label)

    def _run_primary(self) -> None:
        kind, provider = self.plan.kind, self.plan.provider
        if self._busy:
            return
        if kind in (PlanKind.OFFLINE, PlanKind.WAITING):
            self.refresh()
        elif kind is PlanKind.READY:
            self.show_add_device()
        elif kind is PlanKind.TURNED_OFF:
            self._background(lambda service: service.turn_on(provider), _("Turning on…"), _("Connected"), _("Could not turn it on. Check the connection details."))
        elif kind is PlanKind.SERVICE_STOPPED:
            self._background(lambda service: service.start_service(provider), _("Starting…"), _("The service is running"), _("The service did not start."))
        elif kind is PlanKind.ALLOW:
            self._background(lambda service: service.grant_zerotier_access().returncode == 0, _("Waiting for permission…"), _("Permission granted"), _("Permission was not granted."))
        elif kind in (PlanKind.SIGN_IN, PlanKind.SET_UP):
            # "Sign in" opens the browser sign-in at once, not a page with a
            # second "Sign in" button.
            self.main_window._apply_vpn_selection(provider.value, destination="connect_private", auto_start=kind is PlanKind.SIGN_IN and provider is ProviderId.TAILSCALE)
        elif kind is PlanKind.PROBLEM:
            self.main_window._apply_vpn_selection(provider.value, destination="create_private")

    def _background(self, work: "Callable[[PrivateNetworkService], bool]", busy: str, success: str, failure: str) -> None:
        self._set_busy(True, busy)

        def done(ok) -> None:
            self._set_busy(False)
            self.main_window.show_toast(success if ok else failure)
            self.refresh()

        def failed(_error) -> None:
            self._set_busy(False)
            self.main_window.show_toast(failure)
            self.refresh()

        self._action_worker.submit(lambda: bool(work(self.service_factory())), done, failed=failed)

    def _connected_status(self):
        status = self.plan.status
        return status if status is not None and status.connected else None

    def show_add_device(self):
        status = self._connected_status()
        if status is None:
            return None
        from .network_devices import AddDeviceDialog

        dialog = AddDeviceDialog(self, self.service_factory(), status, show_toast=self.main_window.show_toast, on_changed=self.refresh, advanced=self.advanced_row.get_active())
        dialog.present()
        return dialog

    def show_manage(self) -> None:
        status = self._connected_status()
        if status is None:
            return
        from .network_devices import ManageNetworkDialog

        ManageNetworkDialog(self, self.service_factory(), status, show_toast=self.main_window.show_toast, on_changed=self.refresh, advanced=True).present()

    def show_methods(self) -> Adw.Dialog:
        """Change method: three plain choices, each with its state in words."""
        group = Adw.PreferencesGroup()
        by_provider = {status.provider: status for status in self._statuses}
        for provider in (ProviderId.TAILSCALE, ProviderId.ZEROTIER, ProviderId.HEADSCALE):
            status = by_provider.get(provider)
            # The same words as the service cards in Share and Connect.
            presentation = provider_presentation(status) if status is not None else checking_presentation()
            title = provider.display_name if provider is not ProviderId.HEADSCALE else _("{name} (advanced)").format(name=provider.display_name)
            pill = Gtk.Label(label=presentation.text, valign=Gtk.Align.CENTER, wrap=True)
            pill.add_css_class("state-pill")
            pill.add_css_class("caption")
            pill.add_css_class(PILL_TONES.get(presentation.tone, "offline"))

            def choose(p: ProviderId = provider) -> None:
                dialog.close()
                self._open_method(p)

            row = action_row(title, METHOD_DESCRIPTIONS[provider](), PROVIDER_ICONS[provider], choose, icon_style="tile", suffix=pill)
            group.add(row)
        dialog = content_dialog(_("Connection method"), group, description=_("Automatic picks the one that already works on this computer. Choose another only if you need it."), width=560, height=480)
        dialog.present(self)
        return dialog

    def _open_method(self, provider: ProviderId) -> None:
        status = next((s for s in self._statuses if s.provider is provider), None)
        destination = "create_private" if status is not None and status.connected else "connect_private"
        self.main_window._apply_vpn_selection(provider.value, destination=destination)

    def _connect(self, host: HostCandidate) -> None:
        guest = getattr(self.main_window, "guest_view", None)
        if guest is None:
            return
        self.main_window.navigate_to("guest")
        guest.connect_to_host(host.as_host())

    def _diagnose(self, host: HostCandidate) -> None:
        guest = getattr(self.main_window, "guest_view", None)
        if guest is not None:
            guest.diagnose_host(host.as_host())

    def _set_favorite(self, address: str, favorite: bool) -> None:
        try:
            self.preferences.set_favorite(address, favorite)
        except (OSError, ValueError):
            self.main_window.show_toast(_("Could not save the change."))
            return
        self.refresh_recent()
        self._render_devices()

    def _rename(self, address: str, current: str) -> None:
        if not address:
            return
        dialog = Adw.AlertDialog(heading=_("Rename device"), body=_("Only this computer uses the new name."))
        dialog.set_body_use_markup(False)
        entry = Gtk.Entry(text=current, activates_default=True)
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
                self.preferences.rename(address, entry.get_text())
            except (OSError, ValueError):
                self.main_window.show_toast(_("Could not save the change."))
                return
            self.refresh_recent()
            self._render_devices()

        dialog.connect("response", respond)
        dialog.present(self)

    def _forget(self, host: HostCandidate, name: str) -> None:
        def run() -> None:
            def work():
                removed = self.service_factory().history.forget_host(host.address, host.port)
                self.preferences.forget(host.address)
                return removed

            self._action_worker.submit(work, lambda _removed: self.refresh_recent(), failed=lambda _error: self.main_window.show_toast(_("Could not save the change.")), keep_previous=True)

        confirm(self, _("Remove {name}?").format(name=name), _("It disappears from recent connections on this computer. The other computer is not changed."), _("Remove"), run)

    def _show_peer_details(self, peer: PeerDevice, provider: ProviderId, name: str) -> None:
        group = Adw.PreferencesGroup()
        toast = self.main_window.show_toast
        group.add(message_row(peer_state_text(peer) or _("State unknown"), provider.display_name, peer_icon(peer)))
        for address in peer.addresses:
            group.add(copy_row(_("Address"), address, toast=toast))
        if peer.dns_name:
            group.add(copy_row(_("Name on the network"), peer.dns_name, icon="brp-network-workgroup-symbolic", toast=toast))
        if peer.os:
            group.add(message_row(_("System"), peer.os, "brp-computer-symbolic"))
        if peer.node_id and self.advanced_row.get_active():
            group.add(copy_row(_("Device ID"), peer.node_id, icon="brp-text-x-generic-symbolic", toast=toast))
        content_dialog(name, group, width=520, height=440).present(self)
