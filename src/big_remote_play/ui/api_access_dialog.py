"""API access: administrative credentials for each private-network provider.

The dialog never displays a saved secret: only its kind, a masked hint and
whether it works. Entry fields hide their text, are never pre-filled and are
cleared as soon as the credential is saved.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # type: ignore

from big_remote_play.private_network.credentials import CredentialError, CredentialInfo, CredentialKind
from big_remote_play.private_network.http import ApiResult, normalize_base_url
from big_remote_play.private_network.service import PrivateNetworkService
from big_remote_play.private_network.tailscale_api import valid_tags
from big_remote_play.utils.i18n import _
from big_remote_play.utils.secret_store import SecretStoreUnavailable
from big_remote_play.utils.uri import open_uri

from .components import content_dialog, note
from .network_common import RowGroup, Worker, api_error_message, confirm, loading_row, message_row, technical_detail

ZEROTIER_TOKEN_HELP = "https://docs.zerotier.com/tokens/"
TAILSCALE_KEYS_PAGE = "https://login.tailscale.com/admin/settings/keys"
TAILSCALE_OAUTH_PAGE = "https://tailscale.com/kb/1215/oauth-clients"
HEADSCALE_API_DOCS = "https://headscale.net/stable/ref/api/"


def credential_error_message(error: CredentialError) -> str:
    return {
        "empty": _("Paste the credential first."),
        "too_long": _("This text is too long to be a credential."),
        "whitespace": _("A credential cannot contain spaces or line breaks."),
        "auth_key": _("This is an auth key. An auth key adds a computer to a network; it cannot manage the network. Create an API access token instead."),
        "wrong_kind": _("This credential is of a different type than the one selected."),
    }.get(error.reason, _("This credential could not be saved."))


class ApiAccessDialog:
    def __init__(self, parent: Gtk.Widget, service: PrivateNetworkService, *, show_toast: Callable[[str], None], on_changed: Callable[[], None] | None = None, focus: str = "") -> None:
        self.parent = parent
        self.service = service
        self.show_toast = show_toast
        self.on_changed = on_changed or (lambda: None)
        self.worker = Worker()
        self._headscale_scope = ""

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        content.append(
            note(
                _(
                    "An API access credential lets Big Remote Play manage your network: list devices, approve them and create invitations. It is different from an auth key, which only adds one computer to a network."
                ),
                "brp-dialog-password-symbolic",
            )
        )
        self.zerotier_group = self._zerotier_section()
        self.tailscale_group = self._tailscale_section()
        self.headscale_group = self._headscale_section()
        # The provider the person came from comes first; the others follow.
        sections = {"tailscale": self.tailscale_group, "zerotier": self.zerotier_group, "headscale": self.headscale_group}
        first = sections.pop(focus, None)
        for group in ([first] if first is not None else []) + list(sections.values()):
            content.append(group)
        self.dialog = content_dialog(_("API access"), content, description=_("Credentials are kept in the system keyring. They are never shown again after saving."), width=720, height=680)
        self.dialog.connect("closed", lambda *_args: self.worker.close())

    def present(self) -> None:
        self.dialog.present(self.parent)
        self.refresh()

    # ── sections ───────────────────────────────────────────────────────────
    def _entry(self, title: str, *, secret: bool) -> Adw.EntryRow:
        row = Adw.PasswordEntryRow(title=title) if secret else Adw.EntryRow(title=title)
        row.set_use_markup(False)
        return row

    def _buttons(self, group: RowGroup, kind: CredentialKind, save: Callable[[], None]) -> tuple[Gtk.Button, Gtk.Button, Gtk.Button, Gtk.Button]:
        box = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=8)
        save_button = Gtk.Button(label=_("Save"))
        save_button.add_css_class("suggested-action")
        save_button.connect("clicked", lambda _button: save())
        test_button = Gtk.Button(label=_("Test connection"))
        test_button.connect("clicked", lambda _button: self._test(kind))
        replace_button = Gtk.Button(label=_("Replace credential"))
        remove_button = Gtk.Button(label=_("Remove"))
        remove_button.add_css_class("destructive-action")
        remove_button.connect("clicked", lambda _button: self._confirm_remove(kind))
        for button in (test_button, replace_button, remove_button, save_button):
            box.append(button)
        group._brp_buttons = box
        return save_button, test_button, replace_button, remove_button

    def _zerotier_section(self) -> RowGroup:
        group = RowGroup(title="ZeroTier", description=_("Lets Big Remote Play create networks and approve members."))
        self.zt_status = RowGroup()
        self.zt_token = self._entry(_("ZeroTier API token"), secret=True)
        self.zt_form = Adw.PreferencesGroup()
        self.zt_form.add(self.zt_token)
        help_row = message_row(
            _("Where to create a token"),
            _("Legacy Central: my.zerotier.com → Account → API Access Tokens. New Central: Service accounts → API tokens (paid plans)."),
            "brp-help-browser-symbolic",
        )
        help_button = Gtk.Button(label=_("Open guide"), valign=Gtk.Align.CENTER)
        help_button.connect("clicked", lambda button: open_uri(button, ZEROTIER_TOKEN_HELP))
        help_row.add_suffix(help_button)
        self.zt_form.add(help_row)
        self.zt_buttons = self._buttons(group, CredentialKind.ZEROTIER_API_TOKEN, lambda: self._save(CredentialKind.ZEROTIER_API_TOKEN, self.zt_token))
        self.zt_buttons[2].connect("clicked", lambda _button: self._show_form(CredentialKind.ZEROTIER_API_TOKEN, True))
        self._assemble(group, self.zt_status, self.zt_form)
        return group

    def _tailscale_section(self) -> RowGroup:
        group = RowGroup(title="Tailscale", description=_("Lets Big Remote Play list devices, share this computer and invite people."))
        self.ts_status = RowGroup()
        self.ts_form = Adw.PreferencesGroup()
        self.ts_kind = Adw.ComboRow(title=_("Type"), use_markup=False)
        self.ts_kind.set_model(Gtk.StringList.new([_("API access token"), _("OAuth client")]))
        self.ts_kind_help = message_row("", "", "brp-dialog-information-symbolic")
        self.ts_token = self._entry(_("API access token"), secret=True)
        self.ts_client_id = self._entry(_("OAuth client ID"), secret=False)
        self.ts_client_secret = self._entry(_("OAuth client secret"), secret=True)
        self.ts_tags = self._entry(_("Tags for new devices (for example tag:gaming)"), secret=False)
        for row in (self.ts_kind, self.ts_kind_help, self.ts_token, self.ts_client_id, self.ts_client_secret, self.ts_tags):
            self.ts_form.add(row)
        links = message_row(_("Where to create it"), _("Tailscale admin console → Settings → Keys, or Trust credentials for OAuth clients."), "brp-help-browser-symbolic")
        open_keys = Gtk.Button(label=_("Open console"), valign=Gtk.Align.CENTER)
        open_keys.connect("clicked", lambda button: open_uri(button, TAILSCALE_OAUTH_PAGE if self.ts_kind.get_selected() == 1 else TAILSCALE_KEYS_PAGE))
        links.add_suffix(open_keys)
        self.ts_form.add(links)
        self.ts_kind.connect("notify::selected", lambda *_args: self._sync_tailscale_kind())
        self._sync_tailscale_kind()
        self.ts_buttons = self._buttons(group, CredentialKind.TAILSCALE_API_TOKEN, self._save_tailscale)
        self.ts_buttons[2].connect("clicked", lambda _button: self._show_form(CredentialKind.TAILSCALE_API_TOKEN, True))
        self._assemble(group, self.ts_status, self.ts_form)
        return group

    def _sync_tailscale_kind(self) -> None:
        oauth = self.ts_kind.get_selected() == 1
        self.ts_token.set_visible(not oauth)
        for row in (self.ts_client_id, self.ts_client_secret, self.ts_tags):
            row.set_visible(oauth)
        if oauth:
            self.ts_kind_help.set_title(_("Least privilege"))
            self.ts_kind_help.set_subtitle(_("Choose only the scopes you need, such as devices and auth keys. Tailscale does not allow OAuth clients to send invitations or share a single computer."))
        else:
            self.ts_kind_help.set_title(_("Belongs to your user"))
            self.ts_kind_help.set_subtitle(_("Required to share this computer or invite people. It expires after the period you choose (1 to 90 days)."))

    def _headscale_section(self) -> RowGroup:
        group = RowGroup(title="Headscale", description=_("Lets Big Remote Play read users and devices and create one-time pre-auth keys on your server."))
        self.hs_status = RowGroup()
        self.hs_form = Adw.PreferencesGroup()
        self.hs_server = self._entry(_("Server address"), secret=False)
        self.hs_server.set_tooltip_text("https://vpn.example.com")
        self.hs_key = self._entry(_("Headscale API key"), secret=True)
        self.hs_form.add(self.hs_server)
        self.hs_form.add(self.hs_key)
        help_row = message_row(
            _("Create the API key on the server"),
            _("Run “headscale apikeys create --expiration 90d” on the server and paste the result. A pre-auth key is something else: it registers one computer."),
            "brp-help-browser-symbolic",
        )
        help_button = Gtk.Button(label=_("Open guide"), valign=Gtk.Align.CENTER)
        help_button.connect("clicked", lambda button: open_uri(button, HEADSCALE_API_DOCS))
        help_row.add_suffix(help_button)
        self.hs_form.add(help_row)
        self.hs_buttons = self._buttons(group, CredentialKind.HEADSCALE_API_KEY, self._save_headscale)
        self.hs_buttons[2].connect("clicked", lambda _button: self._show_form(CredentialKind.HEADSCALE_API_KEY, True))
        self._assemble(group, self.hs_status, self.hs_form)
        return group

    @staticmethod
    def _assemble(group: RowGroup, status: RowGroup, form: Adw.PreferencesGroup) -> None:
        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        wrapper.append(status)
        wrapper.append(form)
        wrapper.append(group._brp_buttons)
        group.add(wrapper)

    # ── state ──────────────────────────────────────────────────────────────
    def _widgets(self, kind: CredentialKind):
        if kind is CredentialKind.ZEROTIER_API_TOKEN:
            return self.zt_status, self.zt_form, self.zt_buttons
        if kind is CredentialKind.HEADSCALE_API_KEY:
            return self.hs_status, self.hs_form, self.hs_buttons
        return self.ts_status, self.ts_form, self.ts_buttons

    def _show_form(self, kind: CredentialKind, visible: bool) -> None:
        _status, form, (save, test, replace, remove) = self._widgets(kind)
        form.set_visible(visible)
        save.set_visible(visible)
        replace.set_visible(not visible)

    def refresh(self) -> None:
        for kind in (CredentialKind.ZEROTIER_API_TOKEN, CredentialKind.TAILSCALE_API_TOKEN, CredentialKind.HEADSCALE_API_KEY):
            self._widgets(kind)[0].replace([loading_row(_("Checking the keyring…"))])

        def load():
            server = self.service.headscale_server()
            scope = ""
            if server:
                try:
                    scope = normalize_base_url(server, allow_http_loopback=True)
                except ValueError:
                    scope = ""
            store = self.service.credentials
            return {
                "zerotier": store.info(CredentialKind.ZEROTIER_API_TOKEN),
                "tailscale": store.info(CredentialKind.TAILSCALE_API_TOKEN) or store.info(CredentialKind.TAILSCALE_OAUTH_CLIENT),
                "headscale": store.info(CredentialKind.HEADSCALE_API_KEY, scope) if scope else None,
                "headscale_scope": scope or next(iter(store.scopes(CredentialKind.HEADSCALE_API_KEY)), ""),
            }

        self.worker.submit(load, self._apply, failed=self._keyring_failed)

    def _keyring_failed(self, error: BaseException) -> None:
        message = _("The system keyring is locked or unavailable. Unlock it and try again.") if isinstance(error, SecretStoreUnavailable) else _("The saved credentials could not be read.")
        for kind in (CredentialKind.ZEROTIER_API_TOKEN, CredentialKind.TAILSCALE_API_TOKEN, CredentialKind.HEADSCALE_API_KEY):
            status, form, (save, test, replace, remove) = self._widgets(kind)
            status.replace([message_row(message, "", "dialog-warning-symbolic")])
            for button in (save, test, replace, remove):
                button.set_sensitive(False)
            form.set_visible(False)

    def _apply(self, infos: dict) -> None:
        self._headscale_scope = infos.get("headscale_scope") or ""
        if self._headscale_scope and not self.hs_server.get_text():
            self.hs_server.set_text(self._headscale_scope)
        for kind, info in ((CredentialKind.ZEROTIER_API_TOKEN, infos["zerotier"]), (CredentialKind.TAILSCALE_API_TOKEN, infos["tailscale"]), (CredentialKind.HEADSCALE_API_KEY, infos["headscale"])):
            status, _form, (save, test, replace, remove) = self._widgets(kind)
            for button in (save, test, replace, remove):
                button.set_sensitive(True)
            configured = info is not None
            status.replace([self._status_row(kind, info)])
            test.set_visible(configured)
            remove.set_visible(configured)
            self._show_form(kind, not configured)

    def _status_row(self, kind: CredentialKind, info: CredentialInfo | None) -> Adw.ActionRow:
        if info is None:
            return message_row(_("Not configured"), _("Everything else keeps working without it."), "brp-network-offline-symbolic")
        kind_name = {
            CredentialKind.ZEROTIER_API_TOKEN: _("API token"),
            CredentialKind.TAILSCALE_API_TOKEN: _("API access token"),
            CredentialKind.TAILSCALE_OAUTH_CLIENT: _("OAuth client"),
            CredentialKind.HEADSCALE_API_KEY: _("API key"),
        }[info.kind]
        flavor = {"legacy": _("Legacy Central"), "central": _("New Central")}.get(info.note, "")
        details = " · ".join(value for value in (kind_name, flavor, info.hint, info.scope if info.kind is CredentialKind.HEADSCALE_API_KEY else "") if value)
        return message_row(_("Configured"), details, "brp-emblem-ok-symbolic")

    # ── actions ────────────────────────────────────────────────────────────
    def _finish_save(self, entries: list[Adw.EntryRow], message: str) -> None:
        for entry in entries:
            if isinstance(entry, Adw.PasswordEntryRow):
                entry.set_text("")
        self.show_toast(message)
        self.refresh()
        self.on_changed()

    def _save_failed(self, error: BaseException) -> None:
        if isinstance(error, CredentialError):
            self.show_toast(credential_error_message(error))
        elif isinstance(error, SecretStoreUnavailable):
            self.show_toast(_("The system keyring is locked or unavailable. Nothing was saved."))
        else:
            self.show_toast(_("The credential could not be saved."))

    def _save(self, kind: CredentialKind, entry: Adw.EntryRow, *, scope: str = "default") -> None:
        value = entry.get_text()
        self.worker.submit(lambda: self.service.credentials.save(kind, value, scope=scope), lambda _info: self._finish_save([entry], _("Saved in the system keyring")), failed=self._save_failed)

    def _save_tailscale(self) -> None:
        if self.ts_kind.get_selected() == 0:
            self._save(CredentialKind.TAILSCALE_API_TOKEN, self.ts_token)
            return
        client_id = self.ts_client_id.get_text().strip()
        secret = self.ts_client_secret.get_text()
        tags = tuple(tag.strip() for tag in self.ts_tags.get_text().replace(",", " ").split() if tag.strip())
        if tags and not valid_tags(tags):
            self.show_toast(_("Tags look like tag:name, using lowercase letters, digits and hyphens."))
            return

        def save():
            self.service.credentials.save(CredentialKind.TAILSCALE_OAUTH_CLIENT, secret, client_id=client_id, tags=tags)
            # One Tailscale credential at a time keeps it obvious which is used.
            self.service.credentials.remove(CredentialKind.TAILSCALE_API_TOKEN)

        self.worker.submit(save, lambda _value: self._finish_save([self.ts_client_secret], _("Saved in the system keyring")), failed=self._save_failed)

    def _save_headscale(self) -> None:
        try:
            scope = normalize_base_url(self.hs_server.get_text(), allow_http_loopback=True)
        except ValueError:
            self.show_toast(_("Enter the full https address of the Headscale server."))
            self.hs_server.grab_focus()
            return
        self._save(CredentialKind.HEADSCALE_API_KEY, self.hs_key, scope=scope)

    def _scope(self, kind: CredentialKind) -> str:
        return self._headscale_scope if kind is CredentialKind.HEADSCALE_API_KEY else "default"

    def _test(self, kind: CredentialKind) -> None:
        status, *_rest = self._widgets(kind)
        status.replace([loading_row(_("Contacting the service…"))])
        scope = self._scope(kind)

        def run() -> ApiResult:
            if kind is CredentialKind.TAILSCALE_API_TOKEN and not self.service.credentials.secret(kind):
                return self.service.test_credential(CredentialKind.TAILSCALE_OAUTH_CLIENT)
            return self.service.test_credential(kind, scope)

        self.worker.submit(run, lambda result: self._tested(kind, result), failed=lambda _error: self._tested(kind, None))

    def _tested(self, kind: CredentialKind, result: ApiResult | None) -> None:
        status, *_rest = self._widgets(kind)
        if result is not None and result.ok:
            status.replace([message_row(_("Connection works"), _("The service accepted the credential."), "brp-emblem-ok-symbolic")])
            self.show_toast(_("Connection works"))
            return
        sentence = api_error_message(result) if result is not None else _("The operation failed.")
        detail = technical_detail(result) if result is not None else ""
        row = message_row(_("Needs attention"), sentence + ("\n" + _("Technical details: {detail}").format(detail=detail) if detail else ""), "dialog-warning-symbolic")
        status.replace([row])

    def _confirm_remove(self, kind: CredentialKind) -> None:
        scope = self._scope(kind)

        def remove():
            if kind is CredentialKind.TAILSCALE_API_TOKEN:
                self.service.credentials.remove(CredentialKind.TAILSCALE_OAUTH_CLIENT)
            self.service.credentials.remove(kind, scope)

        confirm(
            self.dialog,
            _("Remove this credential?"),
            _("Big Remote Play will stop managing this network. The credential is not revoked at the provider; revoke it there if it may have leaked."),
            _("Remove credential"),
            lambda: self.worker.submit(remove, lambda _value: self._finish_save([], _("Credential removed")), failed=self._save_failed),
        )
