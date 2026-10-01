"""My network: the state of one provider on this computer, and what to do next.

The page renders exactly one :class:`ConnectionState` at a time. The status is
loaded in a worker when the page is shown or refreshed; nothing is installed,
started, joined or changed unless the person chooses the action.
"""

from __future__ import annotations

import socket
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Pango  # type: ignore

from big_remote_play.private_network.diagnostics import local_network_facts
from big_remote_play.private_network.models import ConnectionState, ProviderCapabilities, ProviderId, ProviderStatus, Recovery
from big_remote_play.private_network.service import default_service
from big_remote_play.private_network.zerotier_api import choose_subnet
from big_remote_play.utils.i18n import _, ngettext
from big_remote_play.utils.uri import open_uri

from .components import action_row, boxed_rows, icon_tile, note
from .network_common import PROVIDER_ICONS, RowGroup, Worker, api_error_message, confirm, copy_row, message_row, recovery_label, simple_state_sentence, state_pill, state_sentence, technical_detail

TAILSCALE_MACHINES = "https://login.tailscale.com/admin/machines"


class NetworkDashboardPage(Gtk.Box):
    def __init__(self, vpn_id: str, main_window) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.vpn_id = vpn_id
        self.provider = ProviderId(vpn_id)
        self.main_window = main_window
        # Real application windows always provide the preference. Keeping the
        # legacy full view for small embedders that do not know about it avoids
        # silently removing administration controls from third-party callers.
        self.advanced = bool(getattr(main_window, "network_advanced_mode", True))
        self.service = default_service()
        self.worker = Worker()
        self.status: ProviderStatus | None = None
        self.capabilities = ProviderCapabilities()
        self._build()
        self.connect("map", lambda *_args: self.refresh())
        self.connect("unrealize", lambda *_args: self.worker.cancel())

    # ── skeleton ───────────────────────────────────────────────────────────
    def _build(self) -> None:
        self.banner = Adw.Banner()
        self.banner.set_revealed(False)
        self.banner.connect("button-clicked", lambda _banner: self._run_recovery())
        self.append(self.banner)

        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        clamp = Adw.Clamp(maximum_size=800, tightening_threshold=560)
        for edge in ("top", "bottom", "start", "end"):
            getattr(clamp, f"set_margin_{edge}")(24)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)

        header = Gtk.Box(spacing=16)
        header.add_css_class("brp-intro")
        header.append(icon_tile(PROVIDER_ICONS[self.provider], large=True))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True, valign=Gtk.Align.CENTER)
        self._title = Gtk.Label(label=self.provider.display_name if self.advanced else _("Network details"), xalign=0, wrap=True)
        self._title.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._title.add_css_class("title-2")
        self._description = Gtk.Label(label=_("Checking this computer…"), xalign=0, wrap=True)
        self._description.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._description.add_css_class("dim-label")
        self._pill_slot = Gtk.Box()
        text.append(self._title)
        text.append(self._description)
        text.append(self._pill_slot)
        header.append(text)
        refresh = Gtk.Button(icon_name="brp-view-refresh-symbolic", valign=Gtk.Align.START)
        refresh.add_css_class("flat")
        refresh.set_tooltip_text(_("Refresh"))
        refresh.update_property([Gtk.AccessibleProperty.LABEL], [_("Refresh network status")])
        refresh.connect("clicked", lambda _button: self.refresh())
        header.append(refresh)
        content.append(header)

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        content.append(self.body)
        self.body.append(self._spinner_box(_("Reading the network status…")))

        self._return_to_game = boxed_rows(action_row(_("Ready to play?"), self.main_window.network_return_label(), "brp-client-symbolic", self.main_window.return_from_network))
        self._return_to_game.set_visible(False)
        content.append(self._return_to_game)

        self._maintenance_group = RowGroup(title=_("More options"))
        content.append(self._maintenance_group)

        clamp.set_child(content)
        scroll.set_child(clamp)
        self.append(scroll)

    @staticmethod
    def _spinner_box(text: str) -> Gtk.Widget:
        box = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, margin_top=24, margin_bottom=24)
        spinner = Adw.Spinner()
        spinner.set_size_request(24, 24)
        box.append(spinner)
        box.append(Gtk.Label(label=text, wrap=True))
        return box

    def _set_body(self, *widgets: Gtk.Widget) -> None:
        while child := self.body.get_first_child():
            self.body.remove(child)
        for widget in widgets:
            self.body.append(widget)

    def _toast(self, message: str) -> None:
        self.main_window.show_toast(message)

    # ── loading ────────────────────────────────────────────────────────────
    def refresh(self) -> None:
        provider = self.provider

        def load():
            return self.service.status(provider), self.service.capabilities(provider)

        self.worker.submit(load, self._apply, failed=lambda _error: self._apply((ProviderStatus(provider, ConnectionState.ERROR, technical_detail="status failed"), ProviderCapabilities())))

    def _apply(self, loaded) -> None:
        status, capabilities = loaded
        self.status = status
        self.capabilities = capabilities
        if (old_pill := self._pill_slot.get_first_child()) is not None:
            self._pill_slot.remove(old_pill)
        self._pill_slot.append(state_pill(status.state))
        self._title.set_label((status.network_name or self.provider.display_name) if self.advanced else _("Network details"))
        self._description.set_label(state_sentence(self.provider, status.state, status.recovery) if self.advanced else simple_state_sentence(status.state, status.recovery))
        self._update_banner(status)
        builders = {
            ConnectionState.CONNECTED: self._connected,
            ConnectionState.NEEDS_AUTHORIZATION: self._needs_authorization,
            ConnectionState.UNAVAILABLE: self._unavailable,
            ConnectionState.ERROR: self._error,
        }
        builder = builders.get(status.state, self._not_connected)
        self._set_body(*builder(status))
        self._return_to_game.set_visible(status.connected)
        self._build_maintenance(status)

    def _update_banner(self, status: ProviderStatus) -> None:
        recovery = status.recovery
        visible = recovery in (Recovery.GRANT_ACCESS, Recovery.START_SERVICE, Recovery.SIGN_IN)
        if visible and recovery is not None:
            self.banner.set_title(state_sentence(self.provider, status.state, recovery) if self.advanced else simple_state_sentence(status.state, recovery))
            self.banner.set_button_label(recovery_label(recovery))
        self.banner.set_revealed(visible)

    def _technical(self, status: ProviderStatus) -> Gtk.Widget:
        group = Adw.PreferencesGroup()
        expander = Adw.ExpanderRow(title=_("Technical details"), use_markup=False)
        row = Adw.ActionRow(title=status.technical_detail or _("No details reported."), use_markup=False)
        row.set_title_lines(0)
        row.set_title_selectable(True)
        expander.add_row(row)
        group.add(expander)
        group.set_visible(self.advanced)
        return group

    # ── states ─────────────────────────────────────────────────────────────
    def _connected(self, status: ProviderStatus) -> list[Gtk.Widget]:
        me = status.self_device
        info = Adw.PreferencesGroup(title=_("This computer"))
        info.add(copy_row(_("Name"), (me.name if me else "") or socket.gethostname(), icon="brp-computer-symbolic", toast=self._toast))
        if status.reachable_address:
            info.add(copy_row(_("Private address"), status.reachable_address, toast=self._toast))
        if self.advanced and me is not None and me.dns_name:
            info.add(copy_row(_("Name on the network"), me.dns_name, icon="brp-network-server-symbolic", toast=self._toast))
        if self.provider is ProviderId.ZEROTIER and status.network_id:
            info.add(copy_row(_("Network ID") if self.advanced else _("Network code"), status.network_id, icon="brp-zerotier-symbolic", toast=self._toast))
        devices = action_row(
            _("Devices"),
            _("{count} online").format(count=len(status.online_peers)) if status.peers else _("Only this computer so far"),
            "brp-network-workgroup-symbolic",
            self._manage,
        )
        info.add(devices)

        share = Gtk.Button(label=_("Share this PC"))
        share.add_css_class("suggested-action")
        share.set_size_request(180, 44)
        share.update_property([Gtk.AccessibleProperty.DESCRIPTION], [_("Open Share to start sharing a game from this computer")])
        share.connect("clicked", lambda _button: self.main_window.navigate_to("host"))
        add = Gtk.Button(label=_("Add device"))
        add.set_size_request(160, 44)
        add.connect("clicked", lambda _button: self._add_device())
        manage = Gtk.Button(label=_("Manage network") if self.advanced else _("Devices"))
        manage.set_size_request(160, 44)
        manage.connect("clicked", lambda _button: self._manage())
        # Wrap the three actions on narrow windows instead of clipping them.
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3, min_children_per_line=1, homogeneous=False, halign=Gtk.Align.CENTER)
        flow.set_column_spacing(12)
        flow.set_row_spacing(12)
        for button in (share, add, manage):
            flow.append(button)
        widgets: list[Gtk.Widget] = [info, flow]
        if self.advanced and self.provider is ProviderId.ZEROTIER and len(status.networks) > 1:
            widgets.append(
                note(
                    ngettext(
                        "This computer is in {count} ZeroTier network. Manage network lists it.",
                        "This computer is in {count} ZeroTier networks. Manage network lists them all.",
                        len(status.networks),
                    ).format(count=len(status.networks))
                )
            )
        widgets.append(self._technical(status))
        return widgets

    def _not_connected(self, status: ProviderStatus) -> list[Gtk.Widget]:
        group = Adw.PreferencesGroup()
        if status.state is ConnectionState.NEEDS_AUTHENTICATION and status.auth_url:
            group.add(action_row(_("Finish signing in"), _("Open the sign-in page again in your browser."), "brp-network-connect-symbolic", lambda: open_uri(self, status.auth_url)))
        group.add(
            action_row(
                _("Connect this computer"),
                _("Join an existing network or sign in."),
                "brp-network-connect-symbolic",
                lambda: self.main_window.navigate_to("connect_private"),
                icon_style="tile",
            )
        )
        if self.provider is ProviderId.ZEROTIER:
            if self.capabilities.can_create_network:
                group.add(action_row(_("Create a network"), _("A new private ZeroTier network, ready for your computers."), "brp-network-setup-symbolic", self._create_zerotier_network))
            else:
                group.add(
                    action_row(
                        _("Create a network"),
                        _("A new private ZeroTier network, ready for your computers.") if not self.advanced else _("Needs a ZeroTier API token. Add one under API access."),
                        "brp-network-setup-symbolic" if not self.advanced else "brp-dialog-password-symbolic",
                        (lambda: open_uri(self, "https://my.zerotier.com/network")) if not self.advanced else (lambda: self._api_access()),
                    )
                )
        if self.provider is ProviderId.HEADSCALE:
            group.add(action_row(_("Set up your own server"), _("What you need and the steps to run Headscale on a Linux server."), "brp-network-server-symbolic", self._headscale_guide))
        return [group, self._technical(status)]

    def _needs_authorization(self, status: ProviderStatus) -> list[Gtk.Widget]:
        group = Adw.PreferencesGroup(title=_("Waiting for approval"))
        me = status.self_device
        if self.provider is ProviderId.ZEROTIER:
            if me is not None and me.node_id:
                group.add(copy_row(_("Node ID of this computer") if self.advanced else _("Code for this computer"), me.node_id, icon="brp-dialog-password-symbolic", toast=self._toast))
            group.add(
                message_row(
                    _("Send the Node ID to the network owner") if self.advanced else _("Waiting for approval"),
                    _("They approve it in ZeroTier Central or in Big Remote Play. This page updates when you refresh.")
                    if self.advanced
                    else _("The network owner still needs to approve this computer."),
                    "brp-accounts-symbolic",
                )
            )
            if status.network_id:
                group.add(copy_row(_("Network ID") if self.advanced else _("Network code"), status.network_id, icon="brp-zerotier-symbolic", toast=self._toast))
        else:
            group.add(message_row(_("An administrator must approve this computer"), _("Device approval is enabled for this tailnet. It is done in the admin console."), "brp-accounts-symbolic"))
            if self.provider is ProviderId.TAILSCALE:
                group.add(action_row(_("Open the admin console"), "login.tailscale.com", "brp-help-browser-symbolic", lambda: open_uri(self, TAILSCALE_MACHINES)))
        return [group, self._technical(status)]

    def _unavailable(self, status: ProviderStatus) -> list[Gtk.Widget]:
        from .private_network_view import InstallSection

        if not status.installed or status.recovery is Recovery.INSTALL:
            return [InstallSection(self.vpn_id, self.main_window, on_installed=self.refresh)]
        group = Adw.PreferencesGroup()
        group.add(
            message_row(
                state_sentence(self.provider, status.state, status.recovery) if self.advanced else simple_state_sentence(status.state, status.recovery),
                _("Starting it asks for your password once."),
                "brp-service-symbolic",
            )
        )
        start = Gtk.Button(label=recovery_label(Recovery.START_SERVICE), halign=Gtk.Align.CENTER)
        start.add_css_class("suggested-action")
        start.connect("clicked", lambda _button: self._start_service())
        return [group, start, self._technical(status)]

    def _error(self, status: ProviderStatus) -> list[Gtk.Widget]:
        if status.recovery is Recovery.GRANT_ACCESS:
            group = Adw.PreferencesGroup(title=_("Allow Big Remote Play to read ZeroTier"))
            group.add(
                message_row(
                    _("ZeroTier keeps its control key readable only by the system."),
                    _("Allowing copies that key to a private file in your home folder, as ZeroTier documents for desktop users. After that, networks can be listed and joined without a password."),
                    "brp-dialog-password-symbolic",
                )
            )
            allow = Gtk.Button(label=_("Allow"), halign=Gtk.Align.CENTER)
            allow.add_css_class("suggested-action")
            allow.connect("clicked", lambda _button: self._grant_access())
            return [group, allow, self._technical(status)]
        retry = Gtk.Button(label=_("Try again"), halign=Gtk.Align.CENTER)
        retry.connect("clicked", lambda _button: self.refresh())
        return [
            message_row(
                state_sentence(self.provider, status.state) if self.advanced else simple_state_sentence(status.state),
                _("Try again. If it keeps happening, check the technical details."),
                "dialog-warning-symbolic",
            ),
            retry,
            self._technical(status),
        ]

    def _build_maintenance(self, status: ProviderStatus) -> None:
        rows: list[Gtk.Widget] = []
        if self.advanced:
            rows.extend(
                [
                    action_row(_("API access"), _("Credentials that let Big Remote Play manage this network."), "brp-dialog-password-symbolic", self._api_access),
                    action_row(
                        _("Accounts and networks"),
                        _("Switch Tailscale or Headscale accounts, and see every ZeroTier network."),
                        "brp-accounts-symbolic",
                        getattr(self.main_window, "show_vpn_accounts", lambda: None),
                    ),
                    action_row(_("Internet, router and firewall"), _("CGNAT, IPv6, UPnP and when a VPN is the better choice."), "brp-firewall-symbolic", self._network_check),
                ]
            )
        if status.connected:
            if self.provider is ProviderId.ZEROTIER:
                title, subtitle = _("Stop ZeroTier temporarily"), _("Disconnect every ZeroTier network on this computer")
            else:
                title, subtitle = _("Disconnect temporarily"), _("Keep saved accounts so you can reconnect without signing in again")
            self._btn_logout = action_row(title, subtitle, "brp-network-offline-symbolic", lambda: self._on_logout(self._btn_logout))
            rows.append(self._btn_logout)
        self._maintenance_group.replace(rows)
        self._maintenance_group.set_visible(bool(rows))

    # ── actions ────────────────────────────────────────────────────────────
    def _run_recovery(self) -> None:
        recovery = self.status.recovery if self.status else None
        if recovery is Recovery.GRANT_ACCESS:
            self._grant_access()
        elif recovery is Recovery.START_SERVICE:
            self._start_service()
        elif recovery is Recovery.SIGN_IN:
            if self.status and self.status.auth_url:
                open_uri(self, self.status.auth_url)
            else:
                self.main_window.navigate_to("connect_private")

    def _grant_access(self) -> None:
        self._toast(_("Asking for permission…"))

        def done(result) -> None:
            self._toast(_("Access allowed") if result.returncode == 0 else _("Permission was not granted."))
            self.refresh()

        self.worker.submit(self.service.grant_zerotier_access, done, keep_previous=True)

    def _start_service(self) -> None:
        unit = "zerotier-one" if self.provider is ProviderId.ZEROTIER else "tailscaled"

        def done(result) -> None:
            self._toast(_("Service started") if result.returncode == 0 else _("The service could not be started."))
            self.refresh()

        self.worker.submit(lambda: self.service.manager.start_service(unit), done, keep_previous=True)

    def _api_access(self) -> None:
        from .api_access_dialog import ApiAccessDialog

        ApiAccessDialog(self, self.service, show_toast=self._toast, on_changed=self.refresh, focus=self.vpn_id).present()

    def _add_device(self) -> None:
        from .network_devices import AddDeviceDialog

        if self.status is not None:
            AddDeviceDialog(self, self.service, self.status, show_toast=self._toast, on_changed=self.refresh, advanced=self.advanced).present()

    def _manage(self) -> None:
        from .network_devices import ManageNetworkDialog

        if self.status is not None:
            ManageNetworkDialog(self, self.service, self.status, show_toast=self._toast, on_changed=self.refresh, advanced=self.advanced).present()

    def _network_check(self) -> None:
        from .connection_guides import build_internet_check_dialog

        build_internet_check_dialog().present(self)

    def _headscale_guide(self) -> None:
        from .connection_guides import build_headscale_hosting_dialog

        build_headscale_hosting_dialog().present(self)

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
        self._set_body(self._spinner_box(_("Creating the network…")))

        def work():
            central = self.service.zerotier_central()
            if central is None:
                return None, None
            facts = local_network_facts()
            status = self.service.status(ProviderId.ZEROTIER)
            in_use = [*facts.ipv4, *(address for network in status.networks for address in network.addresses)]
            network, result = central.create_network(name, choose_subnet([f"{address}/24" for address in in_use]))
            if network is None:
                return None, result
            joined = self.service.join_zerotier(network.network_id)
            node = self.service.manager.zerotier_info()
            if joined.returncode == 0 and node.address:
                # Joining first registers the member; approving it is then allowed.
                time.sleep(2)
                central.set_authorized(network.network_id, node.address, True)
            self.service.manager.set_zerotier_name(network.network_id, network.name or name)
            return network, result

        def done(value) -> None:
            network, result = value
            if network is None:
                self._toast(api_error_message(result) if result is not None else _("Add a ZeroTier API token first."))
                if result is not None:
                    self._set_body(message_row(api_error_message(result), technical_detail(result), "dialog-warning-symbolic"))
            else:
                self._toast(_("Network created"))
            self.refresh()

        def failed(_error: BaseException) -> None:
            self._toast(_("The network could not be created."))
            self.refresh()

        self.worker.submit(work, done, failed=failed)

    def _on_logout(self, btn) -> None:
        """Confirm first: disconnecting can interrupt an active game session."""
        if self.provider is ProviderId.ZEROTIER:
            heading = _("Stop ZeroTier temporarily?")
            body = _("Every ZeroTier network on this computer will disconnect. Saved network memberships are kept.")
        else:
            heading = _("Disconnect temporarily?")
            body = _("The active VPN connection will stop. Saved accounts are kept so you can reconnect later.")
        confirm(self, heading, body, _("Disconnect"), lambda: self._run_logout(btn))

    def _run_logout(self, btn) -> None:
        btn.set_sensitive(False)
        self._toast(_("Disconnecting…"))

        def run():
            if self.provider is ProviderId.ZEROTIER:
                import subprocess

                try:
                    return subprocess.run(["pkexec", "/usr/bin/systemctl", "stop", "zerotier-one"], timeout=60, check=False).returncode == 0
                except (OSError, subprocess.SubprocessError):
                    return False
            # ``down`` preserves every saved profile; ``logout`` would expire the key.
            return self.service.manager.pause_tailscale().returncode == 0

        self.worker.submit(run, self._finish_logout, failed=lambda _error: self._finish_logout(False), keep_previous=True)

    def _finish_logout(self, success: bool) -> bool:
        if not success:
            self._btn_logout.set_sensitive(True)
            self._toast(_("Operation failed"))
            return False
        # Disconnecting must not erase API credentials or saved memberships.
        self.refresh()
        return False
