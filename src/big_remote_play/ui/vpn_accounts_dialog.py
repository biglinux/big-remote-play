"""Adaptive VPN account and network management UI."""

from __future__ import annotations

from collections.abc import Callable
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # type: ignore

from big_remote_play.utils.i18n import _
from big_remote_play.utils.icons import create_icon_widget
from big_remote_play.utils.vpn_accounts import TailscaleProfile, VPNAccountManager, ZeroTierNetwork
from .components import content_dialog, name_icon_button


class VPNAccountsDialog:
    """List the accounts/networks that the installed VPN clients actually know."""

    def __init__(
        self,
        parent: Gtk.Window,
        manager: VPNAccountManager,
        *,
        on_add_tailscale: Callable[[], None],
        on_add_headscale: Callable[[], None],
        on_join_zerotier: Callable[[], None],
        show_toast: Callable[[str], None],
    ) -> None:
        self.parent = parent
        self.manager = manager
        self.on_add_tailscale = on_add_tailscale
        self.on_add_headscale = on_add_headscale
        self.on_join_zerotier = on_join_zerotier
        self.show_toast = show_toast
        self._generation = 0

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        self.tailscale_group = Adw.PreferencesGroup(
            title=_("Tailscale and Headscale accounts"),
            description=_("Only one of these accounts can be active at a time on this computer."),
        )
        self.zerotier_group = Adw.PreferencesGroup(
            title=_("ZeroTier networks"),
            description=_("ZeroTier can keep several networks connected at the same time."),
        )
        self.content.append(self.tailscale_group)
        self.content.append(self.zerotier_group)

        actions = Adw.PreferencesGroup(title=_("Add an account or network"))
        actions.add(self._action_row(_("Add Tailscale account"), _("Sign in with another Tailscale account"), "brp-tailscale-symbolic", self._add_tailscale))
        actions.add(self._action_row(_("Add Headscale account"), _("Connect to another Headscale server"), "brp-headscale-symbolic", self._add_headscale))
        actions.add(self._action_row(_("Join a ZeroTier network"), _("Enter another 16-character Network ID"), "brp-zerotier-symbolic", self._join_zerotier))
        self.content.append(actions)

        self.dialog = content_dialog(
            _("VPN accounts and networks"),
            self.content,
            description=_("Accounts are kept by the VPN clients. Big Remote Play stores only the optional names shown here."),
            width=760,
            height=620,
        )
        self.dialog.connect("closed", self._on_closed)

    def present(self) -> None:
        self.dialog.present(self.parent)
        self.refresh()

    def _on_closed(self, *_args) -> None:
        self._generation += 1

    @staticmethod
    def _action_row(title: str, subtitle: str, icon: str, callback: Callable[[], None]) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title, subtitle=subtitle, activatable=True, use_markup=False)
        row.add_prefix(create_icon_widget(icon, size=18))
        row.add_suffix(create_icon_widget("go-next-symbolic", size=16))
        row.connect("activated", lambda _row: callback())
        return row

    def _add_tailscale(self) -> None:
        self.dialog.close()
        self.on_add_tailscale()

    def _add_headscale(self) -> None:
        self.dialog.close()
        self.on_add_headscale()

    def _join_zerotier(self) -> None:
        self.dialog.close()
        self.on_join_zerotier()

    def refresh(self, *, privileged_zerotier: bool = False) -> None:
        self._generation += 1
        generation = self._generation
        self._replace_group_with_loading(self.tailscale_group, _("Loading accounts…"))
        self._replace_group_with_loading(self.zerotier_group, _("Loading networks…"))

        def load() -> None:
            profiles = self.manager.list_tailscale_profiles()
            networks = self.manager.list_zerotier_networks(allow_privileged=privileged_zerotier)
            GLib.idle_add(self._apply_results, generation, profiles, networks)

        threading.Thread(target=load, daemon=True).start()

    @staticmethod
    def _clear_group(group: Adw.PreferencesGroup) -> None:
        rows = getattr(group, "_brp_rows", [])
        for row in rows:
            group.remove(row)
        group._brp_rows = []

    def _append(self, group: Adw.PreferencesGroup, row: Gtk.Widget) -> None:
        group.add(row)
        rows = getattr(group, "_brp_rows", [])
        rows.append(row)
        group._brp_rows = rows

    def _replace_group_with_loading(self, group: Adw.PreferencesGroup, label: str) -> None:
        self._clear_group(group)
        row = Adw.ActionRow(title=label)
        spinner = Gtk.Spinner(spinning=True, valign=Gtk.Align.CENTER)
        row.add_prefix(spinner)
        self._append(group, row)

    def _apply_results(self, generation, profiles, networks) -> bool:
        if generation != self._generation:
            return False
        self._clear_group(self.tailscale_group)
        self._clear_group(self.zerotier_group)

        if not profiles.profiles:
            subtitle = profiles.error or _("No saved Tailscale or Headscale account was found on this computer.")
            self._append(self.tailscale_group, Adw.ActionRow(title=_("No account found"), subtitle=subtitle, use_markup=False))
        else:
            for profile in profiles.profiles:
                self._append(self.tailscale_group, self._tailscale_row(profile, profiles.switching_supported))
            if not profiles.switching_supported and len(profiles.profiles) == 1:
                self._append(
                    self.tailscale_group,
                    Adw.ActionRow(
                        title=_("Account switching is unavailable"),
                        subtitle=_("This Tailscale version can show the active account, but cannot list or switch saved accounts."),
                        use_markup=False,
                    ),
                )

        if networks.needs_privilege:
            row = Adw.ActionRow(
                title=_("Permission is required to list ZeroTier networks"),
                subtitle=_("Grant access once to read the networks configured by the ZeroTier service."),
                use_markup=False,
            )
            button = Gtk.Button(label=_("Grant access"), valign=Gtk.Align.CENTER)
            button.connect("clicked", lambda _button: self.refresh(privileged_zerotier=True))
            row.add_suffix(button)
            row.set_activatable_widget(button)
            self._append(self.zerotier_group, row)
        elif not networks.networks:
            subtitle = networks.error or _("This computer has not joined a ZeroTier network yet.")
            self._append(self.zerotier_group, Adw.ActionRow(title=_("No network found"), subtitle=subtitle, use_markup=False))
        else:
            for network in networks.networks:
                self._append(self.zerotier_group, self._zerotier_row(network))
        return False

    def _tailscale_row(self, profile: TailscaleProfile, switching_supported: bool) -> Adw.ActionRow:
        provider = "Headscale" if profile.provider == "headscale" else "Tailscale"
        details = [value for value in (provider, profile.account, profile.tailnet) if value]
        row = Adw.ActionRow(title=profile.display_name, subtitle=" · ".join(details), use_markup=False)
        row.add_prefix(create_icon_widget("brp-headscale-symbolic" if profile.provider == "headscale" else "brp-tailscale-symbolic", size=18))

        if profile.selected:
            badge = Gtk.Label(label=_("Active"), valign=Gtk.Align.CENTER)
            badge.add_css_class("accent")
            badge.add_css_class("caption")
            row.add_suffix(badge)
        elif switching_supported:
            switch = Gtk.Button(label=_("Switch"), valign=Gtk.Align.CENTER)
            switch.connect("clicked", lambda _button, item=profile: self._run_profile_switch(item))
            row.add_suffix(switch)

        rename = Gtk.Button(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER)
        rename.add_css_class("flat")
        name_icon_button(rename, _("Rename locally"))
        rename.connect("clicked", lambda _button, item=profile: self._rename_tailscale(item))
        row.add_suffix(rename)

        if profile.removable and switching_supported:
            remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
            remove.add_css_class("flat")
            name_icon_button(remove, _("Remove account from this computer"))
            remove.connect("clicked", lambda _button, item=profile: self._confirm_remove_profile(item))
            row.add_suffix(remove)
        return row

    def _zerotier_row(self, network: ZeroTierNetwork) -> Adw.ActionRow:
        addresses = ", ".join(network.assigned_addresses)
        status = self._zerotier_status_label(network)
        subtitle = " · ".join(value for value in (status, addresses, network.network_id) if value)
        row = Adw.ActionRow(title=network.display_name, subtitle=subtitle, use_markup=False)
        row.add_prefix(create_icon_widget("brp-zerotier-symbolic", size=18))

        rename = Gtk.Button(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER)
        rename.add_css_class("flat")
        name_icon_button(rename, _("Rename locally"))
        rename.connect("clicked", lambda _button, item=network: self._rename_zerotier(item))
        row.add_suffix(rename)

        leave = Gtk.Button(icon_name="network-offline-symbolic", valign=Gtk.Align.CENTER)
        leave.add_css_class("flat")
        name_icon_button(leave, _("Leave this ZeroTier network"))
        leave.connect("clicked", lambda _button, item=network: self._confirm_leave_network(item))
        row.add_suffix(leave)
        return row

    @staticmethod
    def _zerotier_status_label(network: ZeroTierNetwork) -> str:
        status = network.status.upper()
        labels = {
            "OK": _("Connected"),
            "ACCESS_DENIED": _("Waiting for authorization"),
            "REQUESTING_CONFIGURATION": _("Requesting configuration"),
            "NOT_FOUND": _("Network not found"),
            "PORT_ERROR": _("ZeroTier service error"),
        }
        return labels.get(status, _("Status: {}").format(status or _("Unknown")))

    def _run_profile_switch(self, profile: TailscaleProfile) -> None:
        self.show_toast(_("Switching to {}…").format(profile.display_name))

        def run() -> None:
            result = self.manager.switch_tailscale_profile(profile.profile_id)
            GLib.idle_add(self._operation_finished, result.returncode == 0, _("Account switched"), result.stderr or result.stdout)

        threading.Thread(target=run, daemon=True).start()

    def _confirm_remove_profile(self, profile: TailscaleProfile) -> None:
        dialog = Adw.AlertDialog(
            heading=_("Remove this account from this computer?"),
            body=_("The online account is not deleted. You can add it again by signing in."),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("remove", _("Remove"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _dialog, response: self._remove_profile(profile) if response == "remove" else None)
        dialog.present(self.parent)

    def _remove_profile(self, profile: TailscaleProfile) -> None:
        def run() -> None:
            result = self.manager.remove_tailscale_profile(profile.profile_id)
            GLib.idle_add(self._operation_finished, result.returncode == 0, _("Account removed from this computer"), result.stderr or result.stdout)

        threading.Thread(target=run, daemon=True).start()

    def _confirm_leave_network(self, network: ZeroTierNetwork) -> None:
        dialog = Adw.AlertDialog(
            heading=_("Leave this ZeroTier network?"),
            body=_("This computer will no longer see devices on this network until it joins again."),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("leave", _("Leave network"))
        dialog.set_response_appearance("leave", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _dialog, response: self._leave_network(network) if response == "leave" else None)
        dialog.present(self.parent)

    def _leave_network(self, network: ZeroTierNetwork) -> None:
        def run() -> None:
            result = self.manager.leave_zerotier_network(network.network_id)
            GLib.idle_add(self._operation_finished, result.returncode == 0, _("ZeroTier network left"), result.stderr or result.stdout)

        threading.Thread(target=run, daemon=True).start()

    def _operation_finished(self, success: bool, success_message: str, detail: str) -> bool:
        self.show_toast(success_message if success else (_("Operation failed: {}").format(detail.strip() or _("Unknown error"))))
        self.refresh()
        return False

    def _rename_tailscale(self, profile: TailscaleProfile) -> None:
        self._rename_dialog(
            _("Rename account locally"),
            profile.display_name,
            lambda value: self.manager.set_tailscale_metadata(profile.profile_id, friendly_name=value),
        )

    def _rename_zerotier(self, network: ZeroTierNetwork) -> None:
        self._rename_dialog(
            _("Rename network locally"),
            network.display_name,
            lambda value: self.manager.set_zerotier_name(network.network_id, value),
        )

    def _rename_dialog(self, title: str, current: str, save: Callable[[str], None]) -> None:
        entry = Adw.EntryRow(title=_("Name shown in Big Remote Play"))
        entry.set_text(current)
        group = Adw.PreferencesGroup()
        group.add(entry)
        dialog = content_dialog(title, group, description=_("This changes only the name shown by Big Remote Play."), width=440, height=360)
        button = Gtk.Button(label=_("Save"), halign=Gtk.Align.CENTER)
        button.add_css_class("suggested-action")
        group.add(button)

        def on_save(_button) -> None:
            save(entry.get_text())
            dialog.close()
            self.refresh()

        button.connect("clicked", on_save)
        dialog.present(self.parent)
