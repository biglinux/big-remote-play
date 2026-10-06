"""Manage network (devices and memberships) and the Add device assistant.

Both are ordinary pages pushed on the navigation of **Connect your devices**,
never dialogs. Every action shown here maps to a capability the provider
really has in the current setup. Without an API credential the pages explain
the official web console path instead of pretending an invitation was sent.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # type: ignore

from big_remote_play.private_network.headscale_api import HeadscaleApi, registered_name, registration_key
from big_remote_play.private_network.http import ApiErrorKind, ApiResult
from big_remote_play.private_network.models import ConnectionState, PeerDevice, ProviderCapabilities, ProviderId, ProviderStatus
from big_remote_play.private_network.service import PrivateNetworkService
from big_remote_play.private_network.tailscale_api import ADMIN_CONSOLE, valid_email
from big_remote_play.private_network.zerotier_api import CentralMember
from big_remote_play.utils.i18n import _
from big_remote_play.utils.icons import create_icon_widget
from big_remote_play.utils.uri import open_uri

from .components import action_row, name_icon_button, note
from .network_common import RowGroup, Worker, add_qr_button, api_error_message, confirm, copy_row, in_stack, copy_to_clipboard, loading_row, message_row, technical_detail

ZEROTIER_DOWNLOAD = "https://www.zerotier.com/download/"
TAILSCALE_DOWNLOAD = "https://tailscale.com/download"
MEMBER_POLL_SECONDS = 5
MEMBER_POLL_LIMIT = 60  # five minutes of waiting, then an explicit refresh


def page(title: str, child: Gtk.Widget, *, tag: str = "") -> Adw.NavigationPage:
    """A scrolling page of the main window's navigation (its header bar is the window's)."""
    scroll = Gtk.ScrolledWindow(vexpand=True)
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    clamp = Adw.Clamp(maximum_size=760, tightening_threshold=520)
    for edge in ("top", "bottom", "start", "end"):
        getattr(clamp, f"set_margin_{edge}")(24)
    clamp.set_child(child)
    scroll.set_child(clamp)
    result = Adw.NavigationPage(title=title, child=scroll)
    if tag:
        result.set_tag(tag)
    return result


_page = page


def _column(*widgets: Gtk.Widget) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
    for widget in widgets:
        box.append(widget)
    return box


class _Flow:
    """Pages pushed on a navigation view; work stops when the first one is popped."""

    def __init__(self, parent: Gtk.Widget, navigation: Adw.NavigationView, root: Adw.NavigationPage) -> None:
        self.parent = parent
        self.navigation = navigation
        self.root = root
        self.worker = Worker()
        self._popped = 0

    def present(self) -> None:
        self.navigation.push(self.root)
        self._popped = self.navigation.connect("popped", self._on_popped)

    def _on_popped(self, _navigation, _popped: Adw.NavigationPage) -> None:
        # pop_to_page() past several pages reports only the visible one.
        if not in_stack(self.navigation, self.root):
            self.navigation.disconnect(self._popped)
            self._closed()

    def _closed(self) -> None:
        self.worker.close()


def _steps(title: str, steps: list[tuple[str, str]]) -> Adw.PreferencesGroup:
    group = Adw.PreferencesGroup(title=title)
    for index, (heading, detail) in enumerate(steps, start=1):
        row = Adw.ActionRow(title=f"{index}. {heading}", subtitle=detail, use_markup=False)
        row.set_title_lines(0)
        row.set_subtitle_lines(0)
        group.add(row)
    return group


def _button(label: str, callback: Callable[[Gtk.Button], None], *, suggested: bool = False) -> Gtk.Button:
    button = Gtk.Button(label=label, halign=Gtk.Align.CENTER)
    if suggested:
        button.add_css_class("suggested-action")
    button.connect("clicked", callback)
    return button


def _tailscale(service: PrivateNetworkService):
    """The configured API, or an error the worker reports as a failed action."""
    api = service.tailscale_api()
    if api is None:
        raise RuntimeError("Tailscale API access is not configured")
    return api


def _central(service: PrivateNetworkService):
    api = service.zerotier_central()
    if api is None:
        raise RuntimeError("ZeroTier API access is not configured")
    return api


def _headscale(service: PrivateNetworkService):
    api = service.headscale_api()
    if api is None:
        raise RuntimeError("Headscale API access is not configured")
    return api


def device_status_text(peer: PeerDevice) -> str:
    if peer.expired:
        return _("Sign-in expired")
    if peer.authorized is False:
        return _("Waiting for approval")
    if peer.online is True:
        return _("Online")
    if peer.online is False:
        return _("Offline")
    return _("Status unknown")


def member_status_text(member: CentralMember) -> str:
    if member.rejected:
        return _("Rejected")
    if not member.authorized:
        return _("Waiting for approval")
    return _("Seen in the last few minutes") if member.recently_seen() else _("Not seen recently")


class ManageNetworkFlow(_Flow):
    """Every device of the network with its approvals, and this computer's memberships (Advanced)."""

    def __init__(
        self,
        parent: Gtk.Widget,
        navigation: Adw.NavigationView,
        service: PrivateNetworkService,
        status: ProviderStatus,
        *,
        show_toast: Callable[[str], None],
        on_changed: Callable[[], None] | None = None,
        advanced: bool = True,
    ) -> None:
        self.service = service
        self.status = status
        self.show_toast = show_toast
        self.on_changed = on_changed or (lambda: None)
        self.advanced = advanced
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.devices = RowGroup(title=_("Devices"))
        refresh = Gtk.Button(icon_name="brp-view-refresh-symbolic", valign=Gtk.Align.CENTER)
        refresh.add_css_class("flat")
        name_icon_button(refresh, _("Refresh"), _("Read the device list again"))
        refresh.connect("clicked", lambda _button: self.refresh())
        self.devices.set_header_suffix(refresh)
        self.membership = RowGroup(title=_("This computer"))
        self.content.append(self.membership)
        self.content.append(self.devices)
        super().__init__(parent, navigation, _page(_("Manage network"), self.content, tag="manage-network"))

    def present(self) -> None:
        super().present()
        self.refresh()

    def refresh(self) -> None:
        self.devices.replace([loading_row(_("Loading devices…"))])
        provider = self.status.provider

        def load():
            status = self.service.status(provider)
            data: dict = {"status": status, "api": None, "result": None}
            if provider is ProviderId.ZEROTIER:
                central = self.service.zerotier_central()
                if central is not None and status.networks:
                    members = {}
                    for network in status.networks:
                        members[network.network_id], data["result"] = central.list_members(network.network_id)
                    data["api"] = members
            elif provider is ProviderId.HEADSCALE:
                api = self.service.headscale_api()
                if api is not None and self.service.capabilities(provider).can_manage_devices:
                    data["api"], data["result"] = api.nodes()
            else:
                api = self.service.tailscale_api()
                if api is not None:
                    data["api"], data["result"] = api.list_devices()
            return data

        self.worker.submit(load, self._apply, failed=lambda _error: self.devices.replace([message_row(_("The device list could not be read."), "", "dialog-warning-symbolic")]))

    def _apply(self, data: dict) -> None:
        status: ProviderStatus = data["status"]
        self.status = status
        result: ApiResult | None = data["result"]
        self._apply_membership(status)
        rows: list[Gtk.Widget] = []
        if result is not None and not result.ok:
            rows.append(message_row(api_error_message(result), technical_detail(result), "dialog-warning-symbolic"))
        if status.provider is ProviderId.ZEROTIER:
            rows.extend(self._zerotier_rows(status, data["api"]))
        elif status.provider is ProviderId.HEADSCALE and data["api"]:
            rows.extend(self._headscale_rows(data["api"]))
        else:
            rows.extend(self._tailnet_rows(status, data["api"] or []))
        if not rows:
            rows.append(message_row(_("No other device yet"), _("Use Add device to bring another computer to this network."), "brp-computer-symbolic"))
        self.devices.replace(rows)

    # ── this computer ──────────────────────────────────────────────────────
    def _apply_membership(self, status: ProviderStatus) -> None:
        rows: list[Gtk.Widget] = []
        me = status.self_device
        if me is not None and status.reachable_address:
            rows.append(copy_row(_("Private address"), status.reachable_address, toast=self.show_toast))
            if me.dns_name:
                rows.append(copy_row(_("Computer name on the network"), me.dns_name, icon="brp-computer-symbolic", toast=self.show_toast))
        if status.provider is ProviderId.ZEROTIER:
            if me is not None and me.node_id:
                rows.append(copy_row(_("Node ID") if self.advanced else _("Code for this computer"), me.node_id, icon="brp-dialog-password-symbolic", toast=self.show_toast, subtitle=me.node_id))
            for network in status.networks:
                label = _("Network ID") if self.advanced else _("Network code")
                row = copy_row(network.name or network.network_id, network.network_id, icon="brp-zerotier-symbolic", toast=self.show_toast, subtitle=f"{label} {network.network_id}")
                leave = Gtk.Button(label=_("Leave network"), valign=Gtk.Align.CENTER)
                leave.add_css_class("flat")
                leave.update_property([Gtk.AccessibleProperty.DESCRIPTION], [_("This computer stops using this network. The network itself is not deleted.")])
                leave.connect("clicked", lambda _button, net=network: self._confirm_leave(net.network_id, net.name))
                row.add_suffix(leave)
                rows.append(row)
        self.membership.replace(rows or [message_row(_("This computer is not connected to this network."), "", "brp-network-offline-symbolic")])

    def _confirm_leave(self, network_id: str, name: str) -> None:
        confirm(
            self.parent,
            _("Leave this network?"),
            _("This computer will stop seeing the devices on “{name}” until it joins again. The network is not deleted.").format(name=name or network_id),
            _("Leave network"),
            lambda: self.worker.submit(lambda: self.service.leave_zerotier(network_id), lambda result: self._after(result.returncode == 0, _("Network left")), keep_previous=True),
        )

    # ── device rows ────────────────────────────────────────────────────────
    def _peer_row(self, peer: PeerDevice) -> Adw.ActionRow:
        details = (device_status_text(peer), peer.best_address, peer.os) if self.advanced else (device_status_text(peer),)
        detail = " · ".join(value for value in details if value)
        row = Adw.ActionRow(title=peer.name or peer.best_address, subtitle=detail, use_markup=False)
        row.set_subtitle_lines(0)
        icon = create_icon_widget("brp-computer-symbolic", size=18, css_class="brp-row-icon")
        icon.set_valign(Gtk.Align.CENTER)
        row.add_prefix(icon)
        if peer.best_address:
            copy = Gtk.Button(icon_name="brp-edit-copy-symbolic", valign=Gtk.Align.CENTER)
            copy.add_css_class("flat")
            name_icon_button(copy, _("Copy address"), _("Copy the private address of {name}").format(name=peer.name))
            copy.connect("clicked", lambda button, value=peer.best_address: copy_to_clipboard(button, value, self.show_toast))
            row.add_suffix(copy)
        return row

    def _tailnet_rows(self, status: ProviderStatus, api_devices: list) -> list[Gtk.Widget]:
        by_node = {device.node_id: device for device in api_devices}
        rows: list[Gtk.Widget] = []
        for peer in status.peers:
            device = by_node.get(peer.node_id)
            if device is not None and not device.authorized:
                peer = PeerDevice(**{**peer.__dict__, "authorized": False})
            row = self._peer_row(peer)
            if device is not None:
                if not device.authorized:
                    approve = Gtk.Button(label=_("Approve"), valign=Gtk.Align.CENTER)
                    approve.add_css_class("suggested-action")
                    approve.connect("clicked", lambda _button, item=device: self._run_api(lambda: _tailscale(self.service).authorize_device(item.device_id), _("Device approved")))
                    row.add_suffix(approve)
                remove = Gtk.Button(icon_name="brp-trash-symbolic", valign=Gtk.Align.CENTER)
                remove.add_css_class("flat")
                name_icon_button(remove, _("Remove device"), _("Remove {name} from the network").format(name=peer.name))
                remove.connect("clicked", lambda _button, item=device, name=peer.name: self._confirm_remove_device(name, lambda: _tailscale(self.service).delete_device(item.device_id)))
                row.add_suffix(remove)
            rows.append(row)
        return rows

    def _headscale_rows(self, nodes: list) -> list[Gtk.Widget]:
        rows: list[Gtk.Widget] = []
        own = set(self.status.self_device.addresses) if self.status.self_device else set()
        for node in nodes:
            if own & set(node.addresses):
                continue
            peer = PeerDevice(name=node.name, addresses=node.addresses, online=node.online, user=node.user, provider="headscale")
            row = self._peer_row(peer)
            pending = [route for route in node.available_routes if route not in node.approved_routes]
            if pending:
                routes = Gtk.Button(label=_("Approve routes"), valign=Gtk.Align.CENTER)
                routes.update_property([Gtk.AccessibleProperty.DESCRIPTION], [", ".join(pending)])
                routes.set_tooltip_text(", ".join(pending))
                routes.connect("clicked", lambda _button, item=node: self._run_api(lambda: _headscale(self.service).approve_routes(item.node_id, list(item.available_routes)), _("Routes approved")))
                row.add_suffix(routes)
            remove = Gtk.Button(icon_name="brp-trash-symbolic", valign=Gtk.Align.CENTER)
            remove.add_css_class("flat")
            name_icon_button(remove, _("Remove device"), _("Remove {name} from the network").format(name=node.name))
            remove.connect("clicked", lambda _button, item=node: self._confirm_remove_device(item.name, lambda: _headscale(self.service).delete_node(item.node_id)))
            row.add_suffix(remove)
            rows.append(row)
        return rows

    def _zerotier_rows(self, status: ProviderStatus, members_by_network: dict | None) -> list[Gtk.Widget]:
        if members_by_network is None:
            if not status.networks:
                return []
            return [message_row(_("Add a ZeroTier API token to see and approve members"), _("Without it, members are approved on the ZeroTier website."), "brp-dialog-password-symbolic")]
        own = status.self_device.node_id if status.self_device else ""
        rows: list[Gtk.Widget] = []
        for network_id, members in members_by_network.items():
            for member in members:
                title = member.name or member.node_id
                if member.node_id == own:
                    title = _("{name} (this computer)").format(name=title)
                subtitle = " · ".join(value for value in (member_status_text(member), ", ".join(member.ip_assignments), member.node_id) if value)
                row = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False)
                row.set_subtitle_lines(0)
                if not member.authorized:
                    approve = Gtk.Button(label=_("Approve"), valign=Gtk.Align.CENTER)
                    approve.add_css_class("suggested-action")
                    approve.connect("clicked", lambda _button, nid=network_id, item=member: self._run_api(lambda: _central(self.service).set_authorized(nid, item.node_id, True), _("Device approved")))
                    row.add_suffix(approve)
                elif member.node_id != own:
                    revoke = Gtk.Button(label=_("Revoke access"), valign=Gtk.Align.CENTER)
                    revoke.add_css_class("flat")
                    revoke.connect(
                        "clicked",
                        lambda _button, nid=network_id, item=member: confirm(
                            self.parent,
                            _("Revoke access?"),
                            _("{name} stays in the member list but can no longer use the network.").format(name=item.name or item.node_id),
                            _("Revoke access"),
                            lambda: self._run_api(lambda: _central(self.service).set_authorized(nid, item.node_id, False), _("Access revoked")),
                        ),
                    )
                    row.add_suffix(revoke)
                if member.node_id != own:
                    remove = Gtk.Button(icon_name="brp-trash-symbolic", valign=Gtk.Align.CENTER)
                    remove.add_css_class("flat")
                    name_icon_button(remove, _("Remove device"), _("Remove {name} from the network").format(name=member.name or member.node_id))
                    remove.connect(
                        "clicked",
                        lambda _button, nid=network_id, item=member: self._confirm_remove_device(item.name or item.node_id, lambda: _central(self.service).remove_member(nid, item.node_id)),
                    )
                    row.add_suffix(remove)
                rows.append(row)
        return rows

    def _confirm_remove_device(self, name: str, action: Callable[[], ApiResult]) -> None:
        confirm(
            self.parent,
            _("Remove this device?"),
            _("{name} will be removed from the network and must be added again to return.").format(name=name),
            _("Remove device"),
            lambda: self._run_api(action, _("Device removed")),
        )

    def _run_api(self, action: Callable[[], ApiResult], success: str) -> None:
        def done(result: ApiResult) -> None:
            if result.ok:
                self._after(True, success)
            else:
                self.show_toast(api_error_message(result))
                self.refresh()

        self.worker.submit(action, done, failed=lambda _error: self.show_toast(_("The operation failed.")), keep_previous=True)

    def _after(self, ok: bool, message: str) -> None:
        self.show_toast(message if ok else _("The operation failed."))
        self.refresh()
        self.on_changed()


class ApprovalGroup(RowGroup):
    """**Approve a device**: the code a device shows at the Headscale sign-in.

    Headscale cannot list devices that are waiting, so the person types or
    pastes what the other device shows — the whole line, the link or only the
    code — and Big Remote Play approves it for the network's user through the
    API. Nothing is typed in a terminal.
    """

    __gtype_name__ = "BrpApprovalGroup"

    def __init__(self, api_factory: Callable[[], HeadscaleApi | None], *, preferred_user: str = "", on_done: Callable[[str], object] | None = None) -> None:
        super().__init__(title=_("Approve a device"), description=_("On the other device, Tailscale shows a code that starts with hskey-authreq-. Type or paste it here."))
        self.api_factory = api_factory
        self.preferred_user = preferred_user
        self.on_done = on_done or (lambda _name: None)
        self.worker = Worker()
        self.entry = Adw.EntryRow(title=_("Code shown on the other device"), use_markup=False)
        self.entry.connect("entry-activated", lambda _row: self.approve())
        self.button = Gtk.Button(label=_("Approve"), valign=Gtk.Align.CENTER)
        self.button.add_css_class("suggested-action")
        self.button.connect("clicked", lambda _button: self.approve())
        self.entry.add_suffix(self.button)
        self.add(self.entry)
        self.connect("unrealize", lambda *_args: self.worker.close())

    def approve(self) -> None:
        key = registration_key(self.entry.get_text())
        if not key:
            self.entry.add_css_class("error")
            self.replace([message_row(_("This is not a code from the other device."), _("It starts with hskey-authreq- and has 24 more letters and numbers."), "dialog-warning-symbolic")])
            return
        self.entry.remove_css_class("error")
        self.button.set_sensitive(False)
        self.replace([loading_row(_("Approving…"))])
        preferred = self.preferred_user

        def work():
            api = self.api_factory()
            if api is None:
                return "", None
            users, result = api.users()
            if not result.ok:
                return "", result
            user = next((item for item in users if item.name == preferred), users[0] if users else None)
            if user is None:
                return "", ApiResult.failure(ApiErrorKind.NOT_FOUND, "no user")
            result = api.register_node(user.name, key)
            return registered_name(result), result

        def done(value) -> None:
            name, result = value
            self.button.set_sensitive(True)
            if result is not None and result.ok:
                self.entry.set_text("")
                shown = name or _("The device")
                self.replace([message_row(_("{name} was added to your network.").format(name=shown), "", "brp-emblem-ok-symbolic")])
                self.on_done(shown)
                return
            self.replace(
                [
                    message_row(
                        _("The code was not accepted."),
                        _("It may have expired: start the sign-in again on the other device and use the new code."),
                        "dialog-warning-symbolic",
                    )
                ]
            )

        self.worker.submit(work, done, failed=lambda _error: done(("", None)))


class AddDeviceFlow(_Flow):
    """Explain and, where the API allows it, perform adding another device."""

    def __init__(
        self,
        parent: Gtk.Widget,
        navigation: Adw.NavigationView,
        service: PrivateNetworkService,
        status: ProviderStatus,
        *,
        show_toast: Callable[[str], None],
        on_changed: Callable[[], None] | None = None,
        advanced: bool = False,
        network_id: str = "",
    ) -> None:
        self.service = service
        self.status = status
        self.show_toast = show_toast
        self.on_changed = on_changed or (lambda: None)
        self.advanced = advanced
        self.network_id = network_id
        self.capabilities = ProviderCapabilities()
        self._poll_source = 0
        self._polls = 0
        self.root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.root_box.append(loading_row(_("Checking what this network allows…")))
        super().__init__(parent, navigation, _page(_("Add another device"), self.root_box, tag="add-device"))

    def present(self) -> None:
        super().present()

        def load():
            capabilities = self.service.capabilities(self.status.provider)
            server = self.service.headscale_server() if self.status.provider is ProviderId.HEADSCALE else ""
            users = []
            if self.status.provider is ProviderId.HEADSCALE and capabilities.can_use_api:
                api = self.service.headscale_api(server)
                users = api.users()[0] if api is not None else []
            return capabilities, server, users

        self.worker.submit(load, self._build, failed=lambda _error: self._build((None, "", [])))

    def _closed(self) -> None:
        super()._closed()
        if self._poll_source:
            GLib.source_remove(self._poll_source)
            self._poll_source = 0

    def _set_root(self, *widgets: Gtk.Widget) -> None:
        while child := self.root_box.get_first_child():
            self.root_box.remove(child)
        for widget in widgets:
            self.root_box.append(widget)

    def _build(self, loaded) -> None:
        capabilities, server, users = loaded
        self.capabilities = capabilities or ProviderCapabilities()
        provider = self.status.provider
        if provider is ProviderId.ZEROTIER:
            self._build_zerotier()
        elif provider is ProviderId.HEADSCALE:
            self._build_headscale(server, users)
        else:
            self._build_tailscale()

    # ── ZeroTier ───────────────────────────────────────────────────────────
    def _build_zerotier(self) -> None:
        networks = [network for network in self.status.networks if network.state is ConnectionState.CONNECTED] or list(self.status.networks)
        if not networks:
            self._set_root(message_row(_("Join or create a network first"), _("Then come back to add the other computer."), "brp-network-offline-symbolic"))
            return
        # The network chosen on the ZeroTier page, never another one.
        network = next((item for item in networks if item.network_id == self.network_id), networks[0])
        steps = _steps(
            _("On the other computer"),
            [
                (_("Install ZeroTier"), _("Download it from the official website, or use Big Remote Play on that computer.")),
                (_("Join this network"), _("Enter the ID on this page and choose Connect.") if self.advanced else _("In Big Remote Play, open Connect your devices → ZeroTier and type this code.")),
                *(
                    [(_("Wait for it to appear"), _("It shows up in the list below within a minute.")), (_("Approve the device"), _("A new device cannot use the network until it is approved."))]
                    if self.capabilities.can_authorize_member
                    else [(_("Approve the device"), _("Open the ZeroTier website below and allow the new device. It cannot use the network before that."))]
                ),
            ],
        )
        ids = Adw.PreferencesGroup()
        network_label = _("Network ID") if self.advanced else _("Network code")
        # Shown in groups of four so it can be read aloud; copied without spaces.
        readable = network.network_id if self.advanced else " ".join(network.network_id[i : i + 4] for i in range(0, len(network.network_id), 4))
        network_row = copy_row(network_label, network.network_id, icon="brp-zerotier-symbolic", toast=self.show_toast, subtitle=readable)
        add_qr_button(network_row, network_label, network.network_id)
        ids.add(network_row)
        download = _button(_("ZeroTier download page"), lambda button: open_uri(button, ZEROTIER_DOWNLOAD))
        download.add_css_class("flat")
        widgets: list[Gtk.Widget] = [ids, steps, download]
        if self.capabilities.can_authorize_member:
            self.waiting = RowGroup(title=_("Waiting for approval"), description=_("New devices appear here while this page is open."))
            self.waiting.replace([loading_row(_("Looking for new devices…"))])
            widgets.append(self.waiting)
            self._network_id = network.network_id
            self._poll_members()
            self._poll_source = GLib.timeout_add_seconds(MEMBER_POLL_SECONDS, self._poll_members)
        else:
            if self.advanced:
                widgets.append(note(_("Approve it on the ZeroTier website, or add a ZeroTier API token under API access to approve it here."), "brp-dialog-password-symbolic"))
            console = _button(
                _("Open ZeroTier Central") if self.advanced else _("Approve on the ZeroTier website"), lambda button: open_uri(button, f"https://my.zerotier.com/network/{network.network_id}")
            )
            widgets.append(console)
        self._set_root(*widgets)

    def _poll_members(self) -> bool:
        self._polls += 1
        if self._polls > MEMBER_POLL_LIMIT:
            self.waiting.replace([message_row(_("Stopped looking for new devices"), _("Go back and choose Add device again to keep looking."), "brp-network-idle-symbolic")])
            self._poll_source = 0
            return False
        network_id = self._network_id

        def load():
            central = self.service.zerotier_central()
            return central.list_members(network_id) if central is not None else ([], ApiResult(False))

        def apply(loaded) -> None:
            members, result = loaded
            if not result.ok:
                self.waiting.replace([message_row(api_error_message(result), technical_detail(result), "dialog-warning-symbolic")])
                return
            pending = [member for member in members if not member.authorized and not member.rejected]
            if not pending:
                self.waiting.replace([loading_row(_("No device is waiting yet…"))])
                return
            rows = []
            for member in pending:
                row = Adw.ActionRow(title=member.name or member.node_id, subtitle=f"{_('Node ID')} {member.node_id}", use_markup=False)
                approve = Gtk.Button(label=_("Approve"), valign=Gtk.Align.CENTER)
                approve.add_css_class("suggested-action")
                approve.connect("clicked", lambda _button, item=member: self._approve_member(item))
                row.add_suffix(approve)
                rows.append(row)
            self.waiting.replace(rows)

        self.worker.submit(load, apply, keep_previous=True)
        return True

    def _approve_member(self, member: CentralMember) -> None:
        network_id = self._network_id

        def done(result: ApiResult) -> None:
            self.show_toast(_("Device approved") if result.ok else api_error_message(result))
            self.on_changed()
            self._poll_members()

        self.worker.submit(lambda: _central(self.service).set_authorized(network_id, member.node_id, True), done, keep_previous=True)

    # ── Tailscale ──────────────────────────────────────────────────────────
    def _build_tailscale(self) -> None:
        share = action_row(
            _("Share this computer"),
            _("Recommended for playing with a friend. Your friend gets access to this computer only."),
            "brp-host-symbolic",
            lambda: self.navigation.push(self._share_page()),
            icon_style="tile",
        )
        person = action_row(
            _("Add a person to my network"),
            _("The person joins your private network and can see the devices you allow."),
            "brp-accounts-symbolic",
            lambda: self.navigation.push(self._invite_page()),
        )
        mine = action_row(
            _("Add another computer of mine"),
            _("Sign in on that computer with your own account."),
            "brp-computer-symbolic",
            lambda: self.navigation.push(self._own_computer_page()),
        )
        group = Adw.PreferencesGroup(title=_("Who is the other computer for?"))
        for row in (share, person, mine):
            group.add(row)
        self._set_root(group)

    def _email_form(self, title: str, button_label: str, *, required: bool, on_submit: Callable[[str], None]) -> tuple[Adw.PreferencesGroup, Gtk.Button]:
        group = Adw.PreferencesGroup(title=title)
        entry = Adw.EntryRow(title=_("Email (optional)") if not required else _("Email"), use_markup=False)
        entry.set_input_purpose(Gtk.InputPurpose.EMAIL)
        group.add(entry)
        button = _button(button_label, lambda _button: submit(), suggested=True)

        def submit() -> None:
            email = entry.get_text().strip()
            if (required or email) and not valid_email(email):
                entry.add_css_class("error")
                entry.grab_focus()
                self.show_toast(_("Enter a valid email address."))
                return
            entry.remove_css_class("error")
            on_submit(email)

        entry.connect("entry-activated", lambda _entry: submit())
        return group, button

    def _invite_result(self, box: Gtk.Box, url: str, result: ApiResult) -> None:
        if result.ok and url:
            group = Adw.PreferencesGroup(title=_("Invitation created"), description=_("Send this link to the person. It works only for them and can be revoked in the Tailscale console."))
            link_row = copy_row(_("Invitation link"), url, icon="brp-network-transmit-receive-symbolic", toast=self.show_toast)
            add_qr_button(link_row, _("Invitation link"), url, hint=_("Scan it with the phone of the person you are inviting."))
            group.add(link_row)
            box.append(group)
            self.show_toast(_("Invitation created"))
        else:
            box.append(message_row(api_error_message(result), technical_detail(result), "dialog-warning-symbolic"))

    def _console_fallback(self, text: str, url: str) -> list[Gtk.Widget]:
        return [
            note(text, "brp-dialog-information-symbolic"),
            _button(_("Open Tailscale admin console"), lambda button: open_uri(button, url), suggested=True),
            note(_("To do this from Big Remote Play, add a Tailscale API access token under API access."), "brp-dialog-password-symbolic"),
        ]

    def _share_page(self) -> Adw.NavigationPage:
        box = _column(note(_("Your friend installs Tailscale, opens the link and signs in with their own account. They will see only this computer."), "brp-host-symbolic"))
        if self.capabilities.can_share_machine:
            node_id = self.status.self_device.node_id if self.status.self_device else ""

            def submit(email: str) -> None:
                def run():
                    api = _tailscale(self.service)
                    device, result = api.device_for_node(node_id)
                    if device is None:
                        return "", result if not result.ok else ApiResult(False, error=ApiErrorKind.NOT_FOUND, detail=_("This computer is not in the Tailscale device list yet."))
                    return api.share_device(device.device_id, email=email)

                self.worker.submit(run, lambda value: self._invite_result(box, *value), keep_previous=True)

            form, button = self._email_form(_("Invitation"), _("Create invitation"), required=False, on_submit=submit)
            box.append(form)
            box.append(button)
        else:
            for widget in self._console_fallback(_("In the admin console, open Machines, choose this computer, then “Share…”."), f"{ADMIN_CONSOLE}/machines"):
                box.append(widget)
        return _page(_("Share this computer"), box)

    def _invite_page(self) -> Adw.NavigationPage:
        box = _column(
            note(_("The person becomes a member of your tailnet. Use this for your own family or team; to play with a friend, sharing one computer is usually enough."), "brp-accounts-symbolic")
        )
        if self.capabilities.can_invite_user:

            def submit(email: str) -> None:
                self.worker.submit(lambda: _tailscale(self.service).invite_user(email), lambda value: self._invite_result(box, *value), keep_previous=True)

            form, button = self._email_form(_("Invitation"), _("Send invitation"), required=True, on_submit=submit)
            box.append(form)
            box.append(button)
        else:
            for widget in self._console_fallback(_("In the admin console, open Users and choose “Invite users”."), f"{ADMIN_CONSOLE}/users"):
                box.append(widget)
        return _page(_("Add a person to my network"), box)

    def _own_computer_page(self) -> Adw.NavigationPage:
        steps = _steps(
            _("On the other computer"),
            [
                (_("Install Tailscale"), _("Use Big Remote Play on that computer, or the official download page.")),
                (_("Sign in with the same account"), _("Sign in on that computer with your own account.")),
                (_("Done"), _("The computer appears in this network's device list.")),
            ],
        )
        box = _column(steps, _button(_("Tailscale download page"), lambda button: open_uri(button, TAILSCALE_DOWNLOAD)))
        if self.advanced:
            commands = Adw.PreferencesGroup(title=_("Linux command"))
            commands.add(copy_row(_("Command"), "sudo tailscale up", icon="brp-text-x-generic-symbolic", toast=self.show_toast))
            box.append(commands)
        if self.advanced and self.capabilities.can_create_auth_key:
            box.append(self._auth_key_group(lambda: lambda: _tailscale(self.service).create_auth_key(), "sudo tailscale up --auth-key=file:$HOME/tailscale-key"))
        return _page(_("Add another computer of mine"), box)

    def _auth_key_group(self, prepare: Callable[[], Callable[[], tuple[str, ApiResult]]], command: str) -> Gtk.Widget:
        """A one-time key, shown once; the command reads it from a file.

        ``prepare`` runs on the GTK thread (it may read widgets) and returns
        the blocking call that the worker then performs.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        expander = Adw.ExpanderRow(title=_("Without a browser (advanced)"), subtitle=_("Create a one-time sign-in key that expires in one hour."), use_markup=False)
        group = Adw.PreferencesGroup()
        group.add(expander)
        box.append(group)
        result_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        make = Gtk.Button(label=_("Create one-time key"), valign=Gtk.Align.CENTER)

        def done(value) -> None:
            key, result = value
            while child := result_box.get_first_child():
                result_box.remove(child)
            if not (result.ok and key):
                result_box.append(message_row(api_error_message(result), technical_detail(result), "dialog-warning-symbolic"))
                return
            shown = Adw.PreferencesGroup(
                title=_("Key created"), description=_("It is shown only now and is not stored. Save it in a file named in the command, run the command, then delete the file.")
            )
            shown.add(copy_row(_("One-time key"), key, icon="brp-dialog-password-symbolic", toast=self.show_toast, subtitle=_("Hidden. Use Copy.")))
            shown.add(copy_row(_("Command"), command, icon="brp-text-x-generic-symbolic", toast=self.show_toast))
            result_box.append(shown)
            make.set_sensitive(False)

        make.connect("clicked", lambda _button: self.worker.submit(prepare(), done, keep_previous=True))
        row = Adw.ActionRow(title=_("Single use, pre-approved, expires in one hour"), use_markup=False)
        row.add_suffix(make)
        expander.add_row(row)
        box.append(result_box)
        return box

    # ── Headscale ──────────────────────────────────────────────────────────
    def _build_headscale(self, server: str, users: list) -> None:
        server = server or "https://vpn.example.com"
        join_command = f"sudo tailscale up --login-server={server}"
        steps = _steps(
            _("On the other computer"),
            [
                (_("Install Tailscale"), _("Headscale uses the regular Tailscale client.")),
                (_("Connect to this server"), _("In Big Remote Play, open Connect your devices → Headscale and enter this server address.")),
                (_("Approve the sign-in"), _("That computer shows a sign-in link. Paste it here to approve it, or approve it on the server.")),
            ],
        )
        ids = Adw.PreferencesGroup()
        server_row = copy_row(_("Server address"), server, icon="brp-network-server-symbolic", toast=self.show_toast)
        add_qr_button(server_row, _("Server address"), server)
        ids.add(server_row)
        if self.advanced:
            ids.add(copy_row(_("Command"), join_command, icon="brp-text-x-generic-symbolic", toast=self.show_toast))
        widgets: list[Gtk.Widget] = [ids, steps]
        if self.capabilities.can_authorize_member and users:
            owner = Adw.PreferencesGroup(title=_("New computers belong to"))
            self._hs_user = self._user_combo(users)
            owner.add(self._hs_user)
            widgets.append(owner)
            widgets.append(self._headscale_register(users))
            if self.advanced:
                widgets.append(self._auth_key_group(lambda: self._headscale_key(users), f"sudo tailscale up --login-server={server} --auth-key=file:$HOME/headscale-key"))
        else:
            widgets.append(
                note(
                    _("On the server, approve with “headscale auth register --auth-id ID --user USER”, or add a Headscale API key under API access to approve here."),
                    "brp-dialog-password-symbolic",
                )
            )
        self._set_root(*widgets)

    def _user_combo(self, users: list) -> Adw.ComboRow:
        combo = Adw.ComboRow(title=_("User"), use_markup=False)
        combo.set_model(Gtk.StringList.new([user.display_name or user.name for user in users]))
        return combo

    def _headscale_register(self, users: list) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=_("Approve a waiting computer"))
        entry = Adw.EntryRow(title=_("Sign-in link shown on that computer"), use_markup=False)
        group.add(entry)
        approve = Gtk.Button(label=_("Approve"), valign=Gtk.Align.CENTER)
        approve.add_css_class("suggested-action")

        def submit(_button=None) -> None:
            key = registration_key(entry.get_text())
            if not key:
                entry.add_css_class("error")
                self.show_toast(_("Paste the whole sign-in link shown on the other computer."))
                return
            entry.remove_css_class("error")
            user = users[self._hs_user.get_selected()].name

            def done(result: ApiResult) -> None:
                if result.ok:
                    entry.set_text("")
                    self.show_toast(_("Device approved"))
                    self.on_changed()
                else:
                    self.show_toast(api_error_message(result))

            self.worker.submit(lambda: _headscale(self.service).register_node(user, key), done, keep_previous=True)

        approve.connect("clicked", submit)
        entry.connect("entry-activated", lambda _entry: submit())
        entry.add_suffix(approve)
        return group

    def _headscale_key(self, users: list) -> Callable[[], tuple[str, ApiResult]]:
        user_id = users[self._hs_user.get_selected()].user_id  # read on the GTK thread
        return lambda: _headscale(self.service).create_preauth_key(user_id)
