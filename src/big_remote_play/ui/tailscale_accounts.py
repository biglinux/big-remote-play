"""The Tailscale accounts and tailnets this computer knows, on the Tailscale page.

Tailscale keeps one profile per account and tailnet; only one is active.
The group says which one in words, switches between them, starts a sign-in
for another tailnet and, when the active profile has no operator, asks for
permission once instead of showing the CLI's "use sudo" advice.
"""

from __future__ import annotations

from collections.abc import Callable
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # type: ignore

from big_remote_play.utils.i18n import _
from big_remote_play.utils.icons import create_icon_widget
from big_remote_play.utils.vpn_accounts import TailscaleProfile, TailscaleProfiles, VPNAccountManager


class TailscaleAccountsGroup(Adw.PreferencesGroup):
    def __init__(self, manager_factory: Callable[[], VPNAccountManager], *, on_another_tailnet: Callable[[], None], on_changed: Callable[[], None], show_toast: Callable[[str], None]) -> None:
        super().__init__(title=_("Tailscale accounts and tailnets"), description=_("Only one is active at a time. Switch to use another tailnet."))
        self._manager_factory = manager_factory
        self._on_changed = on_changed
        self._show_toast = show_toast
        self._generation = 0
        self._rows: list[Gtk.Widget] = []
        self._busy = False
        self.another_row = Adw.ActionRow(
            title=_("Use another tailnet"),
            subtitle=_("For a friend's tailnet you were invited to, or another account. Tailscale asks which tailnet to use; the current one stays saved."),
            activatable=True,
            use_markup=False,
        )
        self.another_row.set_subtitle_lines(0)
        self.another_row.add_prefix(create_icon_widget("brp-accounts-symbolic", size=18))
        self.another_row.add_suffix(create_icon_widget("go-next-symbolic", size=16))
        self.another_row.connect("activated", lambda _row: on_another_tailnet())
        self.add(self.another_row)

    # -- loading -----------------------------------------------------------

    def refresh(self) -> None:
        self._generation += 1
        generation = self._generation
        self._replace([self._message_row(_("Loading accounts…"), "")])

        def load() -> None:
            try:
                profiles = self._manager_factory().list_tailscale_profiles()
            except Exception as error:  # a broken CLI must not leave the spinner
                profiles = TailscaleProfiles((), switching_supported=False, error=str(error)[:200])
            GLib.idle_add(self._show, generation, profiles)

        threading.Thread(target=load, daemon=True).start()

    def close(self) -> None:
        self._generation += 1

    def _show(self, generation: int, profiles: TailscaleProfiles) -> bool:
        if generation != self._generation:
            return False
        if profiles.needs_permission:
            row = self._message_row(_("Permission is needed to see your Tailscale accounts"), _("Allow it once. Your password is requested; afterwards Tailscale works without it."))
            button = Gtk.Button(label=_("Allow"), valign=Gtk.Align.CENTER)
            button.add_css_class("suggested-action")
            button.connect("clicked", lambda _button: self._run(lambda manager: manager.grant_tailscale_operator(), _("Tailscale works without your password now")))
            row.add_suffix(button)
            row.set_activatable_widget(button)
            self._replace([row])
        elif not profiles.profiles:
            self._replace([self._message_row(_("No account found"), _("No saved Tailscale or Headscale account was found on this computer."))])
        else:
            self._replace([self._profile_row(profile, profiles.switching_supported) for profile in profiles.profiles])
        return False

    # -- rows --------------------------------------------------------------

    @staticmethod
    def _message_row(title: str, subtitle: str) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False)
        row.set_title_lines(0)
        row.set_subtitle_lines(0)
        return row

    def _profile_row(self, profile: TailscaleProfile, switching_supported: bool) -> Adw.ActionRow:
        signed_in = bool(profile.account or profile.tailnet)
        title = profile.display_name if signed_in else _("Not signed in")
        details = [value for value in (profile.account, profile.tailnet) if value and value != title]
        subtitle = " · ".join(details) if signed_in else _("Sign-in was not finished on this account.")
        row = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False)
        row.set_subtitle_lines(0)
        row.add_prefix(create_icon_widget("brp-headscale-symbolic" if profile.provider == "headscale" else "brp-tailscale-symbolic", size=18))
        row._brp_profile_id = profile.profile_id
        if profile.selected:
            badge = Gtk.Label(label=_("Active"), valign=Gtk.Align.CENTER)
            badge.add_css_class("accent")
            badge.add_css_class("caption")
            row.add_suffix(badge)
        elif switching_supported:
            button = Gtk.Button(label=_("Switch"), valign=Gtk.Align.CENTER)
            button.update_property([Gtk.AccessibleProperty.LABEL], [_("Switch to {}").format(title)])
            button.connect("clicked", lambda _button, item=profile, name=title: self._switch(item, name))
            row.add_suffix(button)
            row.set_activatable_widget(button)
        return row

    def _replace(self, rows: list[Gtk.Widget]) -> None:
        for row in self._rows:
            self.remove(row)
        self.remove(self.another_row)
        self._rows = rows
        for row in rows:
            self.add(row)
        self.add(self.another_row)

    # -- actions -----------------------------------------------------------

    def _switch(self, profile: TailscaleProfile, name: str) -> None:
        self._show_toast(_("Switching to {}…").format(name))
        self._run(lambda manager: manager.switch_tailscale_profile(profile.profile_id), _("Account switched"))

    def _run(self, action: Callable[[VPNAccountManager], object], done_text: str) -> None:
        if self._busy:
            return
        self._busy = True
        self.set_sensitive(False)
        generation = self._generation

        def work() -> None:
            try:
                result = action(self._manager_factory())
                ok = getattr(result, "returncode", 1) == 0
                detail = (getattr(result, "stderr", "") or getattr(result, "stdout", "") or "").strip()
            except Exception as error:
                ok, detail = False, str(error)
            GLib.idle_add(self._finished, generation, ok, done_text, detail)

        threading.Thread(target=work, daemon=True).start()

    def _finished(self, generation: int, ok: bool, done_text: str, detail: str) -> bool:
        self._busy = False
        self.set_sensitive(True)
        if generation != self._generation:
            return False
        if ok and done_text:
            self._show_toast(done_text)
        elif not ok:
            self._show_toast(_("Tailscale did not accept the change. Try again."))
        self.refresh()
        self._on_changed()
        return False
