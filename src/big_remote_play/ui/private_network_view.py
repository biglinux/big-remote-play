import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
import json, os, re, subprocess, threading, time
import urllib.error
import urllib.request
from gi.repository import Adw, Gdk, GLib, Gtk  # type: ignore
import logging

_log = logging.getLogger("big-remoteplay")
from big_remote_play.utils.i18n import _
from big_remote_play.utils.icons import create_icon_widget
from big_remote_play import paths
from big_remote_play.utils.secure_io import secure_write_text
from big_remote_play.utils.secret_store import SecretKey, SecretStore, SecretStoreUnavailable, new_secret_id
from big_remote_play.utils.script_protocol import parse_script_line
from big_remote_play.utils.uri import open_uri
from big_remote_play.utils.vpn_accounts import VPNAccountManager
from .components import content_dialog, action_row, boxed_rows, intro, note, name_icon_button


def show_simple_instructions(parent, title_text, items):
    """Premium-style step-by-step instructions dialog.

    Shared by the Create and Connect pages. `items` are tuples of
    (group_title, row_title, row_subtitle, icon, btn_label, btn_url); the last
    three are optional.
    """
    dialog = Adw.Window(transient_for=parent)
    dialog.set_modal(True)
    dialog.add_css_class("brp-dialog")
    dialog.set_title(title_text)
    dialog.set_default_size(680, 600)

    toolbar_view = Adw.ToolbarView()
    hb = Adw.HeaderBar()
    hb.set_title_widget(Adw.WindowTitle.new(title_text, _("Step-by-step guide")))
    toolbar_view.add_top_bar(hb)

    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_vexpand(True)

    clamp = Adw.Clamp()
    clamp.set_maximum_size(600)
    for m in ["top", "bottom", "start", "end"]:
        getattr(clamp, f"set_margin_{m}")(24)

    main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
    for item in items:
        g_title = item[0]
        r_title = item[1]
        r_subtitle = item[2] if len(item) > 2 else ""
        icon = item[3] if len(item) > 3 else "brp-dialog-information-symbolic"
        btn_label = item[4] if len(item) > 4 else None
        btn_url = item[5] if len(item) > 5 else None

        group = Adw.PreferencesGroup()
        group.set_title(g_title)

        row = Adw.ActionRow()
        row.set_title(r_title)
        row.set_subtitle(r_subtitle)
        row.add_prefix(create_icon_widget(icon, size=22))

        if btn_label and btn_url:
            btn = Gtk.Button(label=btn_label)
            btn.add_css_class("suggested-action")
            btn.set_valign(Gtk.Align.CENTER)
            btn.connect("clicked", lambda b, u=btn_url: open_uri(b, u))
            row.add_suffix(btn)

        group.add(row)
        main_box.append(group)

    clamp.set_child(main_box)
    scroll.set_child(clamp)
    toolbar_view.set_content(scroll)
    dialog.set_content(toolbar_view)
    dialog.present()


_ZT_API_BASE = "https://api.zerotier.com/api/v1"


def _zt_api_get(path: str, token: str, timeout: float = 10.0) -> object | None:
    """GET the ZeroTier Central API with the token in an HTTP header.

    Uses urllib (not `curl`): the token stays in process memory instead of the
    argv exposed via /proc/<pid>/cmdline. Returns parsed JSON, or None on any
    transport/parse failure.
    """
    if not token:
        return None
    request = urllib.request.Request(f"{_ZT_API_BASE}{path}", headers={"Authorization": f"token {token}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310  # nosec B310 (fixed https host, not user-controlled)
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


# ─── Config ───────────────────────────────────────────────────────────────────
# Canonical config dir lives in paths.CONFIG_DIR; the pre-2.0 -> 2.0 directory
# rename is handled once at app startup (paths.migrate_legacy_config_dir), never
# as an import side effect here.
HISTORY_FILE = str(paths.CONFIG_DIR / "private_network" / "history.json")
LEGACY_ZT_TOKEN_FILE = str(paths.CONFIG_DIR / "zerotier" / "api_token.txt")
_SECRET_STORE = SecretStore()
_ZEROTIER_TOKEN_KEY = SecretKey("zerotier", "api_token", "default")
_HISTORY_SECRET_KEYS = {"auth_key", "api_key"}

VPN_META = {
    "headscale": {
        "name": "Headscale",
        "icon": "brp-headscale-symbolic",
        "color": "#3584e4",
        "create_title": _("Create Headscale Server"),
        "create_desc": _("Create your own private network server. Advanced; requires Docker and DNS."),
        "connect_title": _("Connect to Headscale Network"),
        "connect_desc": _("Enter the server domain and auth key provided by the administrator."),
        "script": "create-network_headscale.sh",
    },
    "tailscale": {
        "name": "Tailscale",
        "icon": "brp-tailscale-symbolic",
        "color": "#26a269",
        "create_title": _("Login to Tailscale"),
        "create_desc": _("Easiest path: sign in with a browser; no server required."),
        "connect_title": _("Connect to Tailscale Network"),
        "connect_desc": _("Join Tailscale with browser login, or use an auth key if someone gave you one."),
    },
    "zerotier": {
        "name": "ZeroTier",
        "icon": "brp-zerotier-symbolic",
        "color": "#e5a50a",
        "create_title": _("Create ZeroTier Network"),
        "create_desc": _("Create a private network using a ZeroTier API token."),
        "connect_title": _("Connect to ZeroTier Network"),
        "connect_desc": _("Enter the 16-character Network ID to join a ZeroTier network."),
        "script": "create-network_zerotier.sh",
    },
}


def provider_connected(vpn_id: str, system_check) -> bool:
    """True when this PC is already on the provider's private network.

    Both the "my network" page and the join page ask this: two pages showing
    different answers to the same question is how a connected PC ended up being
    asked to sign in again.
    """
    try:
        if vpn_id in ("tailscale", "headscale"):
            return VPNAccountManager(system_check).tailscale_backend_state() == "Running"
        if vpn_id == "zerotier":
            result = subprocess.run(["zerotier-cli", "-j", "listnetworks"], capture_output=True, text=True, timeout=10)
            networks = json.loads(result.stdout) if result.returncode == 0 else []
            return isinstance(networks, list) and any(isinstance(item, dict) and item.get("status") == "OK" for item in networks)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        # Never turn a CLI failure or pending authorization into a ready state.
        pass
    return False


def _load_history():
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE) as f:
                return json.load(f).get("history", [])
    except Exception:
        pass
    return []


def _secret_label(provider: str, kind: str) -> str:
    return f"Big Remote Play {provider} {kind.replace('_', ' ')}"


def _history_secret_key(entry: dict, key: str) -> SecretKey | None:
    refs = entry.get("secret_refs", {})
    if not isinstance(refs, dict):
        return None
    secret_id = refs.get(key)
    if not secret_id:
        return None
    return SecretKey(str(entry.get("vpn", "private-network")), key, str(secret_id))


def _entry_secret(entry: dict, key: str) -> str:
    secret_key = _history_secret_key(entry, key)
    if secret_key:
        try:
            value = _SECRET_STORE.lookup(secret_key)
            if value:
                return value
        except SecretStoreUnavailable:
            return ""
    # Compatibility for old history files. New writes never persist this.
    return str(entry.get(key, ""))


def _store_history_secrets(entry: dict) -> dict:
    sanitized = dict(entry)
    refs = dict(sanitized.get("secret_refs", {}) or {})
    provider = str(sanitized.get("vpn", "private-network"))

    for key in _HISTORY_SECRET_KEYS:
        value = str(sanitized.pop(key, "") or "")
        if not value:
            continue
        secret_id = str(refs.get(key) or new_secret_id())
        secret_key = SecretKey(provider, key, secret_id)
        try:
            _SECRET_STORE.store(secret_key, value, _secret_label(provider, key))
            refs[key] = secret_id
        except SecretStoreUnavailable:
            refs.pop(key, None)

    if refs:
        sanitized["secret_refs"] = refs
    else:
        sanitized.pop("secret_refs", None)
    return sanitized


def _clear_history_secrets(entry: dict) -> None:
    refs = entry.get("secret_refs", {})
    if not isinstance(refs, dict):
        return
    provider = str(entry.get("vpn", "private-network"))
    for key, secret_id in refs.items():
        try:
            _SECRET_STORE.clear(SecretKey(provider, str(key), str(secret_id)))
        except SecretStoreUnavailable:
            pass


def _history_identity(entry: dict) -> tuple[str, str, str]:
    return (str(entry.get("vpn", "")), str(entry.get("domain", "")), str(entry.get("network_id", "")))


def _save_history(entry):
    history = _load_history()
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    identity = _history_identity(entry)

    # Same provider + domain/network: refresh the existing entry instead of
    # appending a duplicate (e.g. repeated Tailscale "Default Login" sign-ins).
    for index, existing in enumerate(history):
        if _history_identity(existing) == identity:
            merged = dict(existing)
            merged.update(entry)
            merged["id"] = existing.get("id")
            merged["timestamp"] = timestamp
            merged["secret_refs"] = existing.get("secret_refs", {})
            history[index] = _store_history_secrets(merged)
            secure_write_text(HISTORY_FILE, json.dumps({"history": history}, indent=2))
            return merged["id"]

    new_id = max((h.get("id", 0) for h in history), default=0) + 1
    history_entry = dict(entry)
    history_entry["id"] = new_id
    history_entry["timestamp"] = timestamp
    history.append(_store_history_secrets(history_entry))
    # History stores only metadata plus keyring references.
    secure_write_text(HISTORY_FILE, json.dumps({"history": history}, indent=2))
    return new_id


def _delete_history(entry_id):
    history = _load_history()
    kept = []
    for entry in history:
        if entry.get("id") == entry_id:
            _clear_history_secrets(entry)
        else:
            kept.append(entry)
    secure_write_text(HISTORY_FILE, json.dumps({"history": kept}, indent=2))


def _update_history(entry_id, updated_entry):
    """Update a specific history entry by its ID."""
    history = _load_history()
    for i, h in enumerate(history):
        if h.get("id") == entry_id:
            # Preserve id and timestamp, update the rest
            merged = dict(updated_entry)
            merged["id"] = entry_id
            merged["timestamp"] = h.get("timestamp", time.strftime("%Y-%m-%d %H:%M:%S"))
            merged["secret_refs"] = h.get("secret_refs", {})
            history[i] = _store_history_secrets(merged)
            break
    secure_write_text(HISTORY_FILE, json.dumps({"history": history}, indent=2))


def _get_zerotier_api_token() -> str:
    try:
        token = _SECRET_STORE.lookup(_ZEROTIER_TOKEN_KEY)
        if token:
            return token
    except SecretStoreUnavailable:
        return ""

    if os.path.exists(LEGACY_ZT_TOKEN_FILE):
        try:
            token = open(LEGACY_ZT_TOKEN_FILE).read().strip()
        except Exception:
            token = ""
        if token:
            try:
                _set_zerotier_api_token(token)
                os.remove(LEGACY_ZT_TOKEN_FILE)
                return token
            except (OSError, SecretStoreUnavailable):
                return ""
    return ""


def _set_zerotier_api_token(token: str) -> None:
    _SECRET_STORE.store(_ZEROTIER_TOKEN_KEY, token, _secret_label("zerotier", "api_token"))


def _clear_zerotier_api_token() -> None:
    try:
        _SECRET_STORE.clear(_ZEROTIER_TOKEN_KEY)
    except SecretStoreUnavailable:
        pass
    try:
        if os.path.exists(LEGACY_ZT_TOKEN_FILE):
            os.remove(LEGACY_ZT_TOKEN_FILE)
    except OSError:
        pass


def _has_zerotier_api_token() -> bool:
    return bool(_get_zerotier_api_token())


def _get_script(name: str) -> str:
    return paths.script_path(name)


def _localized_helper_command(script: str) -> list[str]:
    """Run a privileged helper with the user's message locale intact.

    BRP_DATA/BRP_PHASE markers are locale-independent, so human-facing output
    does not need to be forced back to English. ``pkexec`` sanitizes the
    environment; ``env`` restores only the locale variables required by gettext.
    """
    command = ["pkexec", "/usr/bin/env"]
    for key in ("LANG", "LANGUAGE", "LC_MESSAGES"):
        value = os.environ.get(key, "").strip()
        if value:
            command.append(f"{key}={value}")
    command.append(f"TEXTDOMAINDIR={paths.LOCALE_DIR}")
    command.append(script)
    return command


# ─── Terminal Log Widget ───────────────────────────────────────────────────────
class LogView(Gtk.ScrolledWindow):
    def __init__(self):
        super().__init__()
        self.set_vexpand(True)
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self._tv = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self._tv.add_css_class("card")
        self.set_child(self._tv)
        buf = self._tv.get_buffer()
        table = buf.get_tag_table()
        for name, color in [("green", "#2ec27e"), ("blue", "#3584e4"), ("yellow", "#f5c211"), ("red", "#ed333b"), ("cyan", "#33c7de")]:
            t = Gtk.TextTag(name=name)
            t.set_property("foreground", color)
            table.add(t)
        bold = Gtk.TextTag(name="bold")
        bold.set_property("weight", 700)
        table.add(bold)

    def clear(self):
        self._tv.get_buffer().set_text("")

    def append(self, text):
        GLib.idle_add(self._append_idle, text)

    def _append_idle(self, text):
        buf = self._tv.get_buffer()
        ansi = re.compile(r"(\x1b\[[0-9;]*[mK])")
        parts = ansi.split(text)
        tags = []
        for p in parts:
            if p.startswith("\x1b["):
                if p == "\x1b[0m":
                    tags = []
                elif "0;32" in p:
                    tags = ["green"]
                elif "0;34" in p:
                    tags = ["blue"]
                elif "1;33" in p:
                    tags = ["yellow"]
                elif "0;31" in p:
                    tags = ["red"]
                elif "0;36" in p:
                    tags = ["cyan"]
                elif "1;" in p:
                    tags = ["bold"]
            elif p:
                buf.insert_with_tags_by_name(buf.get_end_iter(), p, *tags)
        buf.insert(buf.get_end_iter(), "\n")
        self._tv.scroll_to_mark(buf.get_insert(), 0.0, True, 0.5, 1.0)


# ─── Progress Bar ──────────────────────────────────────────────────────────────
class ProgressRow(Gtk.Box):
    def __init__(self, on_show_log=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.set_visible(False)
        self._status = Gtk.Label(label="", halign=Gtk.Align.START, wrap=True, xalign=0)
        self._status.add_css_class("caption")
        self.append(self._status)
        row = Gtk.Box(spacing=8)
        self.add_css_class("brp-progress")
        self._bar = Gtk.ProgressBar(hexpand=True, valign=Gtk.Align.CENTER)
        self._bar.update_property([Gtk.AccessibleProperty.LABEL], [_("Connection progress")])
        row.append(self._bar)
        self._pct = Gtk.Label(label="0%")
        self._pct.add_css_class("caption-heading")
        self._pct.add_css_class("accent")
        self._pct.set_size_request(38, -1)
        row.append(self._pct)
        if on_show_log:
            btn = Gtk.Button()
            btn.set_child(create_icon_widget("brp-diagnostics-symbolic", size=16))
            btn.add_css_class("flat")
            btn.add_css_class("circular")
            log_label = _("Installation Log")
            btn.set_tooltip_text(log_label)
            btn.update_property([Gtk.AccessibleProperty.LABEL], [log_label])
            btn.connect("clicked", lambda b: on_show_log())
            row.append(btn)
        self.append(row)

    def update(self, fraction, status=""):
        GLib.idle_add(self._set, fraction, status)

    def _set(self, fraction, status):
        self.set_visible(True)
        fraction = max(0.0, min(1.0, float(fraction)))
        self._bar.set_fraction(fraction)
        self._pct.set_text(f"{int(fraction * 100)}%")
        if status:
            self._status.set_text(status)


# ─── CREATE PAGE ──────────────────────────────────────────────────────────────
class CreatePage(Gtk.Box):
    """
    Create/Login page for a specific VPN provider.
    Shows form → runs script → shows network list + logout button.
    """

    def __init__(self, vpn_id, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.vpn_id = vpn_id
        self.vpn = VPN_META[vpn_id]
        self.main_window = main_window
        self._proc = None
        self._logged_in = False
        self._build()

        # CLI discovery belongs off the GTK thread; no ten-second stall on entry.
        def probe():
            logged_in = self._check_logged_in()
            GLib.idle_add(self._apply_logged_in, logged_in)

        threading.Thread(target=probe, daemon=True).start()

    def _apply_logged_in(self, logged_in):
        if logged_in and not self._logged_in and self.get_root() is not None:
            self._logged_in = True
            child = self.get_first_child()
            if child:
                self.remove(child)
            self._build()
        return False

    def _check_logged_in(self):
        return provider_connected(self.vpn_id, self.main_window.system_check)

    def _build(self):
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        clamp = Adw.Clamp(maximum_size=800, tightening_threshold=560)
        for m in ["top", "bottom", "start", "end"]:
            getattr(clamp, f"set_margin_{m}")(24)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)

        heading = intro(
            _("Devices on this private network") if self._logged_in else self.vpn["create_title"],
            _("Connect both computers to the same private network. Then use the game PC’s private address under Connect.") if self._logged_in else self.vpn["create_desc"],
            self.vpn["icon"],
        )
        self._title = heading.get_last_child().get_first_child()
        self._description = heading.get_last_child().get_last_child()
        content.append(heading)
        if self.vpn_id == "headscale" and not self._logged_in:
            content.append(note(_("Advanced setup: requires Docker, a domain and Cloudflare credentials."), "brp-network-private-symbolic"))

        # Install status (above the form): is the VPN package present?
        self._install_status = self._build_install_status()
        self._install_status.set_visible(not self._logged_in)
        content.append(self._install_status)

        installed = self._logged_in or self._is_vpn_installed()
        # Signing in to Tailscale is the same act on both pages, so this one
        # points at the join page instead of asking for the auth key twice.
        self._defers_to_join = self.vpn_id == "tailscale" and not self._logged_in and installed

        # Form group — only meaningful once the package exists.
        self._form_group = Adw.PreferencesGroup()
        self._form_group.set_title(_("Configuration"))
        self._form_rows = []
        if installed and not self._defers_to_join:
            self._build_form()
            self._form_group.set_visible(not self._logged_in)
            content.append(self._form_group)
        elif self._defers_to_join:
            content.append(
                boxed_rows(
                    action_row(
                        _("This PC is not on a private network yet"),
                        _("Join a network"),
                        "brp-network-connect-symbolic",
                        lambda: self.main_window.navigate_to("connect_private"),
                    )
                )
            )

        # Progress
        self._progress = ProgressRow(on_show_log=self._show_log)
        content.append(self._progress)

        # Log view (hidden in scrolled, shown in dialog)
        self._log = LogView()

        # Buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        btn_box.set_halign(Gtk.Align.CENTER)
        btn_box.set_margin_top(8)
        btn_box.set_margin_bottom(16)

        self._spinner = Gtk.Spinner()
        self._spinner.set_visible(False)

        if not installed:
            # Missing package: the only action is an explicit install (pacman) or
            # manual guidance. Connect options appear only after it is installed.
            self._build_install_buttons(btn_box)
        else:
            # Connect/login actions only make sense when not already connected;
            # once logged in, the page shows the network list + Logout instead.
            if not self._logged_in and not self._defers_to_join:
                self._action_lbl = Gtk.Label(label=self._action_label(), wrap=True)
                btn_inner = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
                btn_inner.append(self._spinner)
                btn_inner.append(self._action_lbl)

                self._btn_action = Gtk.Button()
                self._btn_action.add_css_class("suggested-action")
                self._btn_action.set_size_request(200, 48)
                self._btn_action.set_child(btn_inner)
                self._btn_action.connect("clicked", self._on_action)
                btn_box.append(self._btn_action)
                self._btn_instr = Gtk.Button(label=_("About this VPN"))
                self._btn_instr.add_css_class("flat")
                self._btn_instr.connect("clicked", self._on_instructions_clicked)
                btn_box.append(self._btn_instr)

            # Once connected the page is a success page: the next step is
            # playing. The guide and sign-out are kept as quiet rows below it
            # instead of a red button competing with the way back to the game.
            if self.vpn_id == "zerotier":
                disconnect_title = _("Stop ZeroTier temporarily")
                disconnect_subtitle = _("Disconnect every ZeroTier network on this computer")
            else:
                disconnect_title = _("Disconnect temporarily")
                disconnect_subtitle = _("Keep saved accounts so you can reconnect without signing in again")
            self._btn_logout = action_row(
                disconnect_title,
                disconnect_subtitle,
                "brp-network-offline-symbolic",
                lambda: self._on_logout(self._btn_logout),
            )
            self._maintenance_group = boxed_rows(
                action_row(
                    _("Manage accounts and networks"),
                    _("Switch saved Tailscale or Headscale accounts, and manage ZeroTier networks."),
                    "brp-accounts-symbolic",
                    getattr(self.main_window, "show_vpn_accounts", lambda: None),
                ),
                action_row(_("About this VPN"), _("How to use the selected private network service."), "brp-dialog-information-symbolic", lambda: self._on_instructions_clicked(None)),
                self._btn_logout,
            )
            self._maintenance_group.set_visible(self._logged_in)

        # Network list (shown after login for zerotier/tailscale)
        self._networks_group = Adw.PreferencesGroup()
        self._networks_group.set_visible(False)
        self._network_rows = []  # track rows added via .add() so we can remove them safely
        content.append(self._networks_group)
        # Connected, this box holds nothing: its margins would leave a gap
        # between the device list and the way back to the game.
        btn_box.set_visible(btn_box.get_first_child() is not None)
        content.append(btn_box)
        self._return_to_game = boxed_rows(action_row(_("Ready to play?"), self.main_window.network_return_label(), "brp-client-symbolic", self.main_window.return_from_network))
        self._return_to_game.set_visible(self._logged_in)
        content.append(self._return_to_game)
        if installed:
            content.append(self._maintenance_group)

        clamp.set_child(content)
        scroll.set_child(clamp)
        self.append(scroll)

        if self._logged_in:
            GLib.idle_add(self._refresh_networks)

    def _vpn_dependency(self):
        """(check_callable, friendly_name) for this provider's required package."""
        sc = self.main_window.system_check
        deps = {
            "tailscale": (sc.has_tailscale, "Tailscale"),
            "zerotier": (sc.has_zerotier, "ZeroTier"),
            "headscale": (sc.has_docker, "Docker"),
        }
        return deps.get(self.vpn_id, (lambda: True, self.vpn_id))

    def _is_vpn_installed(self) -> bool:
        check, _name = self._vpn_dependency()
        try:
            return bool(check())
        except Exception:
            # Detection failed; don't block the user (the script is idempotent).
            return True

    def _build_install_status(self) -> Gtk.Widget:
        """Row above the form telling the user whether the VPN package is
        installed; if not, that continuing will install it (asks for password)."""
        _check, name = self._vpn_dependency()
        installed = self._is_vpn_installed()
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.add_css_class("brp-note")
        row.set_margin_top(4)
        row.set_margin_bottom(4)
        if installed:
            icon = create_icon_widget("brp-emblem-ok-symbolic", size=18)
            icon.add_css_class("success")
            text = _("{} is installed.").format(name)
        else:
            icon = create_icon_widget("dialog-warning-symbolic", size=18)
            icon.add_css_class("warning")
            text = _("{} is not installed yet. Install it below to continue (asks for your password).").format(name)
        icon.set_valign(Gtk.Align.CENTER)
        row.append(icon)
        lbl = Gtk.Label(label=text)
        lbl.set_wrap(True)
        lbl.set_xalign(0)
        lbl.set_hexpand(True)
        if installed:
            lbl.add_css_class("dim-label")
        row.append(lbl)
        return row

    def _install_help_url(self) -> str:
        return {
            "tailscale": "https://tailscale.com/download",
            "zerotier": "https://www.zerotier.com/download/",
            "headscale": "https://docs.docker.com/engine/install/",
        }.get(self.vpn_id, "https://tailscale.com/download")

    def _build_install_buttons(self, btn_box) -> None:
        """When the package is missing: a single explicit Install action (pacman),
        or — where pacman is unavailable and no Flatpak exists — a link to the
        provider's official install page. No connect options are shown yet."""
        _check, name = self._vpn_dependency()
        if self.main_window.system_check.has_pacman():
            self._btn_install = Gtk.Button()
            self._btn_install.add_css_class("suggested-action")
            self._btn_install.set_size_request(220, 48)
            inner = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
            inner.append(self._spinner)
            inner.append(Gtk.Label(label=_("Install {}").format(name)))
            self._btn_install.set_child(inner)
            self._btn_install.update_property([Gtk.AccessibleProperty.LABEL], [_("Install {}").format(name)])
            self._btn_install.connect("clicked", self._on_install_clicked)
        else:
            self._btn_install = Gtk.Button(label=_("How to install {}").format(name))
            self._btn_install.add_css_class("suggested-action")
            self._btn_install.set_size_request(220, 48)
            self._btn_install.update_property([Gtk.AccessibleProperty.LABEL], [_("How to install {}").format(name)])
            self._btn_install.connect("clicked", lambda b: open_uri(b, self._install_help_url()))
        btn_box.append(self._btn_install)

        self._btn_instr = Gtk.Button(label=_("About this VPN"))
        self._btn_instr.add_css_class("flat")
        self._btn_instr.connect("clicked", self._on_instructions_clicked)
        btn_box.append(self._btn_instr)

    def _on_install_clicked(self, btn) -> None:
        self._btn_install.set_sensitive(False)
        self._spinner.set_visible(True)
        self._spinner.start()
        self._progress.update(0.05, _("Installing..."))
        self._log.clear()
        _check, name = self._vpn_dependency()

        def done(code, captured):
            if captured.get("INSTALL_RESULT") == "ok" and code == 0:
                self.main_window.show_toast(_("{} installed").format(name))
                if hasattr(self.main_window, "check_system"):
                    self.main_window.check_system()
                self._rebuild()
            else:
                self._spinner.stop()
                self._spinner.set_visible(False)
                self._btn_install.set_sensitive(True)
                self.main_window.show_toast(_("Installation failed. Check the log."))

        self._run_script("install-vpn.sh", [self.vpn_id + "\n"], done)

    def _rebuild(self) -> None:
        """Tear down and rebuild the page (e.g. after a successful install, to
        switch from the install-only view to the connect view)."""
        child = self.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.remove(child)
            child = nxt
        self._logged_in = False
        self._build()

        def probe():
            GLib.idle_add(self._apply_logged_in, self._check_logged_in())

        threading.Thread(target=probe, daemon=True).start()

    def _action_label(self):
        # Tailscale never reaches here: its sign-in lives on the join page.
        if self.vpn_id == "zerotier":
            return _("Save Token & Create Network")
        return _("Install Server")

    def _on_instructions_clicked(self, btn):
        if self.vpn_id == "headscale":
            self._show_headscale_instructions()
        elif self.vpn_id == "tailscale":
            self._show_tailscale_instructions()
        elif self.vpn_id == "zerotier":
            self._show_zerotier_instructions()

    def _build_form(self):
        for r in self._form_rows:
            self._form_group.remove(r)
        self._form_rows.clear()

        if self.vpn_id == "headscale":
            self._e_domain = Adw.EntryRow(title=_("Domain (e.g. vpn.ruscher.org)"))
            self._e_zone = Adw.EntryRow(title=_("Cloudflare Zone ID"))
            self._e_token = Adw.PasswordEntryRow(title=_("Cloudflare API Token"))
            self._form_group.add(self._e_domain)
            self._form_group.add(self._e_zone)
            self._form_group.add(self._e_token)

            self._form_rows.extend([self._e_domain, self._e_zone, self._e_token])

        elif self.vpn_id == "zerotier":
            saved = _get_zerotier_api_token()
            self._e_zt_token = Adw.PasswordEntryRow(title=_("ZeroTier API Token"))
            if saved:
                self._e_zt_token.set_text(saved)
            self._e_zt_name = Adw.EntryRow(title=_("Network Name"))
            self._e_zt_name.set_text("my-game-network")
            self._form_group.add(self._e_zt_name)
            self._form_group.add(self._e_zt_token)
            self._form_rows.extend([self._e_zt_token, self._e_zt_name])
            link = Adw.ActionRow(title=_("Get API Token"), subtitle=_("my.zerotier.com → Account → API Access Tokens"))
            link.add_prefix(create_icon_widget("brp-cloud-symbolic", size=18))
            btn = Gtk.Button(label=_("Open"))
            btn.set_valign(Gtk.Align.CENTER)
            btn.connect("clicked", lambda b: open_uri(b, "https://my.zerotier.com"))
            link.add_suffix(btn)
            self._form_group.add(link)
            self._form_rows.append(link)

    def _on_action(self, btn):
        self._btn_action.set_sensitive(False)
        self._spinner.set_visible(True)
        self._spinner.start()
        self._progress.update(0.05, _("Starting..."))
        self._log.clear()

        if self.vpn_id == "headscale":
            self._run_headscale_create()
        elif self.vpn_id == "zerotier":
            self._run_zerotier_create()

    def _run_script(self, script_name, inputs, on_done):
        script = _get_script(script_name)
        try:
            # Bundled scripts already ship 0755; this only re-asserts the mode in a
            # dev tree. Installed under root-owned /usr/share it raises and is
            # ignored, so it can never loosen a packaged file.
            os.chmod(script, 0o755)  # nosec B103 (0755 is the correct mode for a shared system script)
        except Exception:
            pass

        def run():
            try:
                # Machine-readable BRP_* markers stay in ASCII while the helper's
                # explanations follow the user's language.
                proc = subprocess.Popen(_localized_helper_command(script), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                proc_stdin = proc.stdin
                if proc_stdin is not None:
                    for s in inputs:
                        try:
                            proc_stdin.write(s)
                            proc_stdin.flush()
                        except Exception:
                            break

                captured = {}
                phase = 0.1
                proc_stdout = proc.stdout
                if proc_stdout is None:
                    raise OSError("Network helper has no output pipe")
                for line in proc_stdout:
                    kind = parse_script_line(line)
                    if kind[0] == "data":
                        if kind[2]:
                            captured[kind[1]] = kind[2]
                            # The script runs as root (pkexec) and cannot open a
                            # browser; open the auth URL in the user's session here.
                            if kind[1] == "LOGIN_URL":
                                GLib.idle_add(open_uri, self, kind[2])
                    elif kind[0] == "phase":
                        phase = kind[1]
                        self._progress.update(phase, "")
                    elif kind[1]:
                        self._log.append(kind[1])
                        self._progress.update(phase, kind[1][:80])

                code = proc.wait()
                GLib.idle_add(on_done, code, captured)
            except (OSError, ValueError) as error:
                _log.error("Could not run network helper: %s", error)
                GLib.idle_add(on_done, 127, {})

        threading.Thread(target=run, daemon=True).start()

    def _run_headscale_create(self):
        d = self._e_domain.get_text().strip()
        z = self._e_zone.get_text().strip()
        t = self._e_token.get_text().strip()
        if not d or not z or not t:
            self.main_window.show_toast(_("All fields are required"))
            self._finish_action()
            return

        inputs = ["1\n", f"{d}\n", f"{z}\n", f"{t}\n", "n\n"]

        def done(code, data):
            self._finish_action()
            if code == 0:
                self._progress.update(1.0, _("Server installed"))
                data.setdefault("vpn", "headscale")
                data.setdefault("domain", d)
                _save_history(data)
                self.main_window.show_toast(_("Headscale server installed!"))
                self._show_access_info(data)
            else:
                self._progress.update(0, _("Connection failed"))
                self.main_window.show_toast(_("Installation failed. Check the log."))

        self._run_script("create-network_headscale.sh", inputs, done)

    def _hide_connect_buttons(self) -> None:
        """After a successful connect/login, the login actions no longer apply."""
        self._form_group.set_visible(False)
        self._install_status.set_visible(False)
        self._title.set_label(_("Devices on this private network"))
        self._description.set_label(_("Connect both computers to the same private network. Then use the game PC’s private address under Connect."))
        self._btn_action.set_visible(False)

    def _run_zerotier_create(self):
        token = self._e_zt_token.get_text().strip() if hasattr(self, "_e_zt_token") else ""
        name = self._e_zt_name.get_text().strip() if hasattr(self, "_e_zt_name") else "my-network"
        if not token:
            self.main_window.show_toast(_("API Token is required"))
            self._finish_action()
            return
        try:
            _set_zerotier_api_token(token)
        except SecretStoreUnavailable:
            self.main_window.show_toast(_("System keyring is unavailable. Token was not saved."))
            self._finish_action()
            return

        inputs = ["1\n", f"{token}\n", f"{name}\n", "\n"]

        def done(code, data):
            self._finish_action()
            if code == 0:
                self._progress.update(1.0, _("Network created"))
                self._logged_in = True
                self._hide_connect_buttons()
                self._maintenance_group.set_visible(True)
                data["vpn"] = "zerotier"
                data["name"] = name
                _save_history(data)
                network_id = str(data.get("network_id") or "")
                if network_id:
                    VPNAccountManager(self.main_window.system_check).set_zerotier_name(network_id, name)
                self.main_window.show_toast(_("ZeroTier network created!"))
                self._refresh_networks()
                self._show_access_info(data)
            else:
                self._progress.update(0, _("Connection failed"))
                self.main_window.show_toast(_("Network creation failed"))

        self._run_script("create-network_zerotier.sh", inputs, done)

    def _finish_action(self):
        GLib.idle_add(self._do_finish)

    def _do_finish(self):
        self._btn_action.set_sensitive(True)
        self._spinner.stop()
        self._spinner.set_visible(False)

    def _show_headscale_instructions(self):
        from .connection_guides import build_headscale_hosting_dialog

        build_headscale_hosting_dialog().present(self.main_window)

    def _show_tailscale_instructions(self):
        self._show_simple_instructions(
            _("Tailscale Instructions"),
            [
                (
                    _("1. Create Account"),
                    _("Tailscale Registration"),
                    _("Create a free account, then sign in on each PC that will play together."),
                    "brp-tailscale-symbolic",
                    _("Open Site"),
                    "https://login.tailscale.com",
                ),
                (
                    _("2. Login on each PC"),
                    _("Link this device"),
                    _("Use the browser sign-in button on both computers so they join the same Private Network."),
                    "brp-network-connect-symbolic",
                    None,
                    None,
                ),
                (
                    _("3. Return to Connect"),
                    _("Find the game PC"),
                    _("After both computers are on Tailscale, the game PC can appear automatically and search-code discovery can work."),
                    "brp-client-symbolic",
                    _("Open Panel"),
                    "https://login.tailscale.com/admin/machines",
                ),
            ],
        )

    def _show_zerotier_instructions(self):
        self._show_simple_instructions(
            _("ZeroTier Instructions"),
            [
                (_("1. Create Account"), _("Access ZeroTier Central"), _("Sign up to create and manage your virtual networks."), "brp-zerotier-symbolic", _("Open Portal"), "https://my.zerotier.com"),
                (
                    _("2. Authentication"),
                    _("Generate API Token"),
                    _("Go to 'Account Settings' and create a new 'API Access Token'."),
                    "brp-view-reveal-symbolic",
                    _("Generate Token"),
                    "https://my.zerotier.com/account",
                ),
                (_("3. Configuration"), _("Link Network"), _("Paste the Token in the matching field and click 'Save Token &amp; Create Network'."), "brp-edit-copy-symbolic", None, None),
                (
                    _("4. Administration"),
                    _("Authorize Members"),
                    _("Click your network's Network ID to manage and authorize new devices."),
                    "brp-network-server-symbolic",
                    _("My Networks"),
                    "https://my.zerotier.com/network",
                ),
            ],
        )

    def _show_simple_instructions(self, title_text, items):
        show_simple_instructions(self.main_window, title_text, items)

    def _on_logout(self, btn):
        """Confirm first: disconnecting can interrupt an active game session."""
        if self.vpn_id == "zerotier":
            heading = _("Stop ZeroTier temporarily?")
            body = _("Every ZeroTier network on this computer will disconnect. Saved network memberships are kept.")
        else:
            heading = _("Disconnect temporarily?")
            body = _("The active VPN connection will stop. Saved accounts are kept so you can reconnect later.")
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("leave", _("Disconnect"))
        dialog.set_response_appearance("leave", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _dialog, response: self._run_logout(btn) if response == "leave" else None)
        # Present against this page: it resolves to the window that owns it,
        # and works before the page has been added to one.
        dialog.present(self)

    def _run_logout(self, btn):
        btn.set_sensitive(False)
        self.main_window.show_toast(_("Disconnecting..."))
        manager = VPNAccountManager(self.main_window.system_check)

        def run():
            try:
                if self.vpn_id == "zerotier":
                    success = subprocess.run(["pkexec", "/usr/bin/systemctl", "stop", "zerotier-one"], timeout=30).returncode == 0
                else:
                    # ``down`` preserves every saved profile. ``logout`` would
                    # expire the node key and force authentication again.
                    success = manager.pause_tailscale().returncode == 0
            except (OSError, subprocess.SubprocessError):
                success = False
            GLib.idle_add(self._finish_logout, success)

        threading.Thread(target=run, daemon=True).start()

    def _finish_logout(self, success):
        if not success:
            self._btn_logout.set_sensitive(True)
            self.main_window.show_toast(_("Operation failed"))
            return False
        # Disconnecting must not erase API credentials or saved memberships.
        self._rebuild()
        return False

    def _refresh_networks(self):
        threading.Thread(target=self._fetch_networks, daemon=True).start()

    def _fetch_networks(self):
        # Dispatch on the provider — the Tailscale peer list and the ZeroTier
        # network list are unrelated sources. Always populate at the end (even
        # with an empty list) so the "Your Networks" group updates.
        if self.vpn_id == "zerotier":
            rows = self._fetch_zerotier_rows()
        else:  # tailscale / headscale share the tailscale status output
            rows = self._fetch_tailscale_rows()
        GLib.idle_add(self._populate_networks, rows)

    @staticmethod
    def _fetch_tailscale_rows() -> list[dict]:
        rows: list[dict] = []
        try:
            r = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                return rows
            data = json.loads(r.stdout)
        except (subprocess.SubprocessError, OSError, ValueError):
            return rows

        self_node = data.get("Self", {})
        rows.append(
            {
                "title": self_node.get("DNSName", "This device").split(".")[0],
                "subtitle": ", ".join(self_node.get("TailscaleIPs", [])),
                "icon": "brp-computer-symbolic",
                "is_self": True,
            }
        )
        for key, peer in data.get("Peer", {}).items():
            rows.append(
                {
                    "title": peer.get("DNSName", key).split(".")[0],
                    "subtitle": ", ".join(peer.get("TailscaleIPs", [])),
                    "icon": "brp-client-symbolic",
                    "online": peer.get("Online", False),
                }
            )
        return rows

    @staticmethod
    def _fetch_zerotier_rows() -> list[dict]:
        rows: list[dict] = []
        token = _get_zerotier_api_token()
        if not token:
            return rows
        networks = _zt_api_get("/network", token)
        if not isinstance(networks, list):
            return rows
        for net in networks:
            if not isinstance(net, dict):
                continue
            nid = net.get("id", "")
            nname = net.get("config", {}).get("name", nid)
            rows.append({"title": nname, "subtitle": f"ID: {nid}", "icon": "brp-zerotier-symbolic", "network_id": nid})
        return rows

    def _populate_networks(self, rows):
        # Remove only the rows we explicitly added (avoids touching internal layout children)
        for r in self._network_rows:
            self._networks_group.remove(r)
        self._network_rows.clear()

        if not rows:
            row = Adw.ActionRow(title=_("No networks found"), subtitle=_("Connect or create a network first"))
            row.add_prefix(create_icon_widget("brp-network-offline-symbolic", size=18))
            self._networks_group.add(row)
            self._network_rows.append(row)
        else:
            for r in rows:
                row = Adw.ActionRow(title=r["title"], subtitle=r.get("subtitle", ""), use_markup=False)
                row.add_prefix(create_icon_widget(r.get("icon", self.vpn["icon"]), size=18))
                if r.get("is_self"):
                    badge = Gtk.Label(label=_("This device"))
                    badge.add_css_class("caption")
                    badge.add_css_class("dim-label")
                    row.add_suffix(badge)
                elif "online" in r:
                    status = Gtk.Label(label=_("Online") if r["online"] else _("Offline"))
                    status.add_css_class("caption")
                    row.add_suffix(status)
                self._networks_group.add(row)
                self._network_rows.append(row)

        self._networks_group.set_visible(True)

    def _show_log(self):
        dialog = Adw.Window(transient_for=self.main_window)
        dialog.set_modal(False)
        dialog.set_title(_("Installation Log"))
        dialog.set_default_size(700, 500)
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        tv.add_top_bar(hb)
        tv.set_content(self._log)
        dialog.set_content(tv)
        dialog.present()

    def _show_access_info(self, data):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        tv.add_top_bar(hb)
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        clamp = Adw.Clamp(maximum_size=560)
        for m in ["top", "bottom", "start", "end"]:
            getattr(clamp, f"set_margin_{m}")(16)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        grp = Adw.PreferencesGroup()
        grp.set_title(_("Connection ready"))
        for key, label, icon in [
            ("domain", _("Domain"), "brp-network-server-symbolic"),
            ("network_id", _("Network ID"), "brp-address-symbolic"),
            ("auth_key", _("Auth Key (Friends)"), "key-symbolic"),
            ("web_ui", _("Web Interface"), "web-browser-symbolic"),
        ]:
            val = data.get(key, "")
            if not val:
                continue
            row = Adw.ActionRow(title=label, subtitle=val, use_markup=False)
            row.add_prefix(create_icon_widget(icon, size=16))
            btn = Gtk.Button()
            btn.set_child(create_icon_widget("brp-edit-copy-symbolic", size=14))
            btn.add_css_class("flat")
            btn.set_valign(Gtk.Align.CENTER)
            btn.set_tooltip_text(_("Copy"))
            btn.update_property([Gtk.AccessibleProperty.LABEL], [_("Copy")])
            btn.connect("clicked", lambda b, v=val: self._copy(v))
            row.add_suffix(btn)
            grp.add(row)
        content.append(grp)

        clamp.set_child(content)
        scroll.set_child(clamp)
        tv.set_content(scroll)

        # Action Buttons at bottom
        actions_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12, halign=Gtk.Align.CENTER)
        actions_box.set_margin_top(16)
        actions_box.set_margin_bottom(16)

        def on_save_file(b):
            msg = "\n".join([f"{k}: {data.get(k)}" for k in data if data.get(k)])
            dialog_file = Gtk.FileDialog(title=_("Save Network Information"))
            dialog_file.set_initial_name("network_info.txt")

            def on_save_finish(source, result):
                try:
                    file_handle = dialog_file.save_finish(result)
                    if file_handle:
                        path = file_handle.get_path()
                        if path:
                            with open(path, "w") as f:
                                f.write(msg)
                            self.main_window.show_toast(_("Saved to file!"))
                except Exception as e:
                    _log.error(f"Error saving: {e}")

            dialog_file.save(dialog, None, on_save_finish)

        def on_share(b):
            msg = _("VPN Network Details:\n\n")
            for k, l, _icon in [("domain", _("Domain"), ""), ("network_id", _("Network ID"), ""), ("auth_key", _("Auth Key"), "")]:
                if data.get(k):
                    msg += f"{l}: {data.get(k)}\n"

            import urllib.parse

            body = urllib.parse.quote(msg)
            open_uri(b, f"mailto:?subject=VPN Network Info&body={body}")
            self.main_window.show_toast(_("Opening mail client..."))

        btn_save = Gtk.Button(label=_("Save"))
        btn_save.add_css_class("suggested-action")
        btn_save.connect("clicked", lambda b: self.main_window.show_toast(_("Saved to history!")))

        btn_file = Gtk.Button()
        btn_file_box = Gtk.Box(spacing=8)
        btn_file_box.append(create_icon_widget("brp-folder-open-symbolic", size=16))
        btn_file_box.append(Gtk.Label(label=_("Save to File")))
        btn_file.set_child(btn_file_box)
        btn_file.connect("clicked", on_save_file)

        btn_share = Gtk.Button()
        btn_share_box = Gtk.Box(spacing=8)
        btn_share_box.append(create_icon_widget("brp-open-menu-symbolic", size=16))
        btn_share_box.append(Gtk.Label(label=_("Share")))
        btn_share.set_child(btn_share_box)
        btn_share.connect("clicked", on_share)

        actions_box.set_margin_top(14)
        actions_box.set_margin_bottom(14)
        actions_box.append(btn_save)
        actions_box.append(btn_file)
        actions_box.append(btn_share)

        # Add actions to bottom bar of ToolbarView
        tv.add_bottom_bar(actions_box)
        box.append(tv)

        dialog = Adw.Window(transient_for=self.main_window)
        dialog.add_css_class("brp-dialog")
        dialog.set_modal(True)
        dialog.set_title(_("Network Information"))
        dialog.set_default_size(540, 500)
        dialog.set_content(box)
        dialog.present()

    def _copy(self, text):
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(text)
        self.main_window.show_toast(_("Copied!"))


# ─── CONNECT PAGE ─────────────────────────────────────────────────────────────
class ConnectPage(Adw.Bin):
    """
    Tabbed page: Connect | Status | Previous Networks
    Works for all 3 VPN providers independently.
    """

    def __init__(self, vpn_id, main_window, add_account=False):
        super().__init__()
        self.vpn_id = vpn_id
        self.vpn = VPN_META[vpn_id]
        self.main_window = main_window
        # "Add another account" is a deliberate second sign-in: the page must
        # keep its form even though this PC is already on a network.
        self._add_account = add_account
        self._fetching = False
        self._build()

        if add_account:
            return

        # The CLI probe blocks, so it runs off the GTK thread and only then
        # decides whether this page is a form or a statement of the current state.
        def probe():
            connected = provider_connected(self.vpn_id, self.main_window.system_check)
            GLib.idle_add(self._apply_connected, connected)

        threading.Thread(target=probe, daemon=True).start()

    def _apply_connected(self, connected: bool) -> bool:
        """Already on the network: say so instead of asking to sign in again.

        The heading states it and the existing "Ready to play?" row is the only
        thing left to do, so no extra banner is added to say the same twice.
        """
        self._connect_form.set_visible(not connected)
        self._return_to_game.set_visible(connected)
        if connected:
            self._c_title.set_label(_("Already connected"))
            self._c_description.set_label(_("This PC is on the {} private network.").format(self.vpn["name"]))
        return False

    def _build(self):
        toolbar = Adw.ToolbarView()

        conn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        conn_box.set_margin_top(24)
        conn_box.set_margin_bottom(24)
        conn_box.set_margin_start(16)
        conn_box.set_margin_end(16)

        heading = intro(self.vpn["connect_title"], self.vpn["connect_desc"], self.vpn["icon"])
        self._c_title = heading.get_last_child().get_first_child()
        self._c_description = heading.get_last_child().get_last_child()
        conn_box.append(heading)

        # Everything needed to join, hidden as one block once this PC is on the
        # network: a form asking to sign in again contradicts the page above it.
        self._connect_form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        conn_box.append(self._connect_form)
        fields_group = Adw.PreferencesGroup()
        fields_group.set_title(_("Connection Details"))
        self._build_connect_fields(fields_group)
        if self.vpn_id != "tailscale":
            self._connect_form.append(fields_group)
        self._c_progress = ProgressRow(on_show_log=self._show_connect_log)
        self._connect_form.append(self._c_progress)
        self._c_log = LogView()

        btn_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, halign=Gtk.Align.CENTER, margin_top=8)
        self._c_spinner = Gtk.Spinner()
        self._c_spinner.set_visible(False)
        self._c_lbl = Gtk.Label(label=_("Sign in with browser") if self.vpn_id == "tailscale" else _("Connect"), wrap=True)
        if self.vpn_id == "tailscale":
            self._e_key.connect("changed", lambda row: self._c_lbl.set_label(_("Connect") if row.get_text().strip() else _("Sign in with browser")))
        inner = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        inner.append(self._c_spinner)
        inner.append(self._c_lbl)
        self._btn_connect = Gtk.Button()
        self._btn_connect.add_css_class("suggested-action")
        self._btn_connect.add_css_class("brp-primary")
        self._btn_connect.set_size_request(220, 48)
        self._btn_connect.set_child(inner)
        self._btn_connect.connect("clicked", self._on_connect)
        btn_row.append(self._btn_connect)

        self._connect_form.append(btn_row)
        if self.vpn_id == "tailscale":
            # The novice path is browser sign-in, not an empty technical field.
            self._connect_form.append(fields_group)
        self._connect_form.append(
            boxed_rows(action_row(_("About this VPN"), _("How to use the selected private network service."), "brp-dialog-information-symbolic", lambda: self._on_instructions_clicked(None)))
        )
        self._return_to_game = boxed_rows(action_row(_("Ready to play?"), self.main_window.network_return_label(), "brp-client-symbolic", self.main_window.return_from_network))
        self._return_to_game.set_visible(False)
        conn_box.append(self._return_to_game)

        # The devices on the network are shown by the "My network" page in the
        # headerbar; a Status tab here answered the same question in a second
        # place, under a second row of tabs. The remaining extras are rows.
        extras = Adw.PreferencesGroup()
        self._hist_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._history_dialog = content_dialog(
            _("Previous Networks"),
            self._hist_list,
            description=_("Networks this PC joined before."),
        )
        extras.add(
            action_row(
                _("Manage accounts and networks"),
                _("Switch saved accounts or leave networks without deleting online accounts."),
                "brp-accounts-symbolic",
                getattr(self.main_window, "show_vpn_accounts", lambda: None),
            )
        )
        extras.add(action_row(_("Previous Networks"), _("Reuse connection details saved by Big Remote Play"), "brp-document-open-recent-symbolic", self._present_history))
        if self.vpn_id == "zerotier":
            extras.add(action_row(_("ZeroTier API Token"), _("Manage API Token"), "brp-dialog-password-symbolic", lambda: self._prompt_api_token()))
            # Where to get a token is help for someone who has none; it goes
            # away once one is stored. Reading the keyring is a D-Bus call that
            # can take seconds, so it never happens while the page is built.
            self._token_help_row = action_row(_("Create API Access Token"), "my.zerotier.com", "brp-cloud-symbolic", lambda: open_uri(self, "https://my.zerotier.com/account"))
            self._token_help_row.set_visible(False)
            extras.add(self._token_help_row)

            def probe_token():
                missing = not _has_zerotier_api_token()
                GLib.idle_add(self._token_help_row.set_visible, missing)

            threading.Thread(target=probe_token, daemon=True).start()
        conn_box.append(extras)

        conn_clamp = Adw.Clamp(maximum_size=800, tightening_threshold=560)
        conn_clamp.set_child(conn_box)
        conn_scroll = Gtk.ScrolledWindow(vexpand=True)
        conn_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        conn_scroll.set_child(conn_clamp)

        toolbar.set_content(conn_scroll)
        self.set_child(toolbar)

    def _present_history(self) -> None:
        self._refresh_history()
        self._history_dialog.present(self)

    def _on_instructions_clicked(self, btn):
        if self.vpn_id == "headscale":
            self._show_headscale_instructions()
        elif self.vpn_id == "tailscale":
            self._show_tailscale_instructions()
        elif self.vpn_id == "zerotier":
            self._show_zerotier_instructions()

    def _build_connect_fields(self, group):
        if self.vpn_id == "headscale":
            self._e_domain = Adw.EntryRow(title=_("Server Domain (e.g. vpn.ruscher.org)"))
            self._e_key = Adw.PasswordEntryRow(title=_("Auth Key"))
            group.add(self._e_domain)
            group.add(self._e_key)

        elif self.vpn_id == "tailscale":
            self._e_server = Adw.EntryRow(title=_("Login Server (leave empty for tailscale.com)"))
            self._e_key = Adw.PasswordEntryRow(title=_("Auth Key"))
            self._auth_key_expander = Adw.ExpanderRow(title=_("Advanced options"), subtitle=_("Auth Key"))
            self._auth_key_expander.add_row(self._e_key)
            group.set_title("")
            group.add(self._auth_key_expander)

        elif self.vpn_id == "zerotier":
            self._e_netid = Adw.EntryRow(title=_("Network ID (16 characters)"))
            self._e_netid.set_tooltip_text(_("e.g. a1b2c3d4e5f6a7b8"))
            group.add(self._e_netid)
            link = Adw.ActionRow(title=_("Find Network ID"), subtitle=_("my.zerotier.com → Networks"))
            link.add_prefix(create_icon_widget("brp-address-symbolic", size=18))
            btn = Gtk.Button(label=_("Open"))
            btn.set_valign(Gtk.Align.CENTER)
            btn.connect("clicked", lambda b: open_uri(b, "https://my.zerotier.com/network"))
            link.add_suffix(btn)
            group.add(link)

        self._prefill_from_history()
        if self.vpn_id == "tailscale":
            self._auth_key_expander.set_expanded(bool(self._e_key.get_text()))

    def _prefill_from_history(self):
        """Pre-fill fields with the last successful connection for this VPN."""
        history = _load_history()
        # Find the latest entry for this vpn
        last_entry = next((h for h in reversed(history) if h.get("vpn") == self.vpn_id), None)
        if not last_entry:
            return

        if self.vpn_id == "headscale":
            if hasattr(self, "_e_domain"):
                self._e_domain.set_text(last_entry.get("domain", ""))
            if hasattr(self, "_e_key"):
                self._e_key.set_text(_entry_secret(last_entry, "auth_key"))
        elif self.vpn_id == "tailscale":
            if hasattr(self, "_e_server"):
                self._e_server.set_text(last_entry.get("domain", "") if last_entry.get("domain") != "tailscale.com" else "")
            if hasattr(self, "_e_key"):
                self._e_key.set_text(_entry_secret(last_entry, "auth_key"))
        elif self.vpn_id == "zerotier":
            if hasattr(self, "_e_netid"):
                self._e_netid.set_text(last_entry.get("network_id", ""))

    def _prompt_api_token(self, btn=None):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        lbl = Gtk.Label(label=_("Enter your API Token to view managed devices."))
        lbl.set_wrap(True)
        box.append(lbl)

        entry = Adw.PasswordEntryRow(title=_("API Token"))
        saved_token = _get_zerotier_api_token()
        if saved_token:
            entry.set_text(saved_token)

        grp = Adw.PreferencesGroup()
        grp.add(entry)
        box.append(grp)

        btn_box = Gtk.Box(spacing=12)
        btn_box.set_halign(Gtk.Align.CENTER)

        btn_save = Gtk.Button(label=_("Save & Refresh"))
        btn_save.add_css_class("suggested-action")

        btn_close = Gtk.Button(label=_("Close"))

        def on_save(btn):
            token = entry.get_text().strip()
            if token:
                try:
                    _set_zerotier_api_token(token)
                    self.main_window.show_toast(_("Token saved in the system keyring"))
                    self._token_help_row.set_visible(False)
                except SecretStoreUnavailable:
                    self.main_window.show_toast(_("System keyring is unavailable. Token was not saved."))
                    return
            dialog.close()

        btn_save.connect("clicked", on_save)
        btn_close.connect("clicked", lambda b: dialog.close())

        btn_box.append(btn_save)
        btn_box.append(btn_close)
        box.append(btn_box)

        dialog = content_dialog(_("ZeroTier API Token"), box, width=440, height=340)
        dialog.present(self)

    def _on_connect(self, btn):
        self._return_to_game.set_visible(False)
        self._btn_connect.set_sensitive(False)
        self._c_spinner.set_visible(True)
        self._c_spinner.start()
        self._c_lbl.set_label(_("Connecting..."))
        self._c_phase = 0.05
        self._c_progress.update(self._c_phase, _("Starting..."))
        self._c_log.clear()

        # Tailscale and Headscale are the same daemon: both join through the
        # documented CLI (`tailscale up`), not through a privileged menu script.
        if self.vpn_id == "headscale":
            domain = self._e_domain.get_text().strip()
            key = self._e_key.get_text().strip()
            if not domain or not key:
                self.main_window.show_toast(_("Domain and Auth Key required"))
                self._c_done(False)
                return
            self._connect_tailnet(login_server=domain, auth_key=key)
            return

        if self.vpn_id == "tailscale":
            self._connect_tailnet(login_server=self._e_server.get_text(), auth_key=self._e_key.get_text())
            return

        if self.vpn_id == "zerotier":
            nid = self._e_netid.get_text().strip() if hasattr(self, "_e_netid") else ""
            if re.fullmatch(r"[0-9a-fA-F]{16}", nid) is None:
                self._e_netid.add_css_class("error")
                self._e_netid.grab_focus()
                self.main_window.show_toast(_("Enter a 16-character hexadecimal Network ID."))
                self._c_done(False)
                return
            self._e_netid.remove_css_class("error")
            inputs = ["2\n", f"{nid}\n"]
            script = "create-network_zerotier.sh"
        else:
            self._c_done(False)
            self.main_window.show_toast(_("Select a network service and try again."))
            return

        def run():
            try:
                spath = _get_script(script)
                try:
                    # See _run_script: re-asserts the shipped 0755 in a dev tree only.
                    os.chmod(spath, 0o755)  # nosec B103 (0755 is the correct mode for a shared system script)
                except Exception:
                    pass
                # Parse only locale-independent BRP_* markers; display the helper's
                # translated prose to the user.
                proc = subprocess.Popen(_localized_helper_command(spath), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                proc_stdin = proc.stdin
                if proc_stdin is not None:
                    for s in inputs:
                        try:
                            proc_stdin.write(s)
                            proc_stdin.flush()
                        except Exception:
                            break
                phase = 0.1
                proc_stdout = proc.stdout
                if proc_stdout is None:
                    raise OSError("Network helper has no output pipe")
                for line in proc_stdout:
                    kind = parse_script_line(line)
                    if kind[0] == "data":
                        if kind[1] == "LOGIN_URL" and kind[2]:
                            GLib.idle_add(open_uri, kind[2])
                        continue
                    if kind[0] == "phase":
                        phase = kind[1]
                        self._c_progress.update(phase, "")
                    elif kind[0] == "text" and kind[1]:
                        self._c_log.append(kind[1])
                        self._c_progress.update(phase, kind[1][:80])
                code = proc.wait()
                # The helper exiting cleanly is not membership: ask the client.
                joined = code == 0 and provider_connected(self.vpn_id, self.main_window.system_check)
                GLib.idle_add(lambda: self._c_done(joined))
            except (OSError, ValueError) as error:
                _log.error("Could not run network helper: %s", error)
                GLib.idle_add(self._c_done, False)

        threading.Thread(target=run, daemon=True).start()

    def _connect_tailnet(self, *, login_server: str, auth_key: str) -> None:
        """Join a Tailscale/Headscale tailnet and report the daemon's verdict."""
        manager = VPNAccountManager(self.main_window.system_check)

        def run():
            connection = manager.connect_tailscale(
                login_server=login_server,
                auth_key=auth_key,
                add_account=self._add_account,
                on_auth_url=lambda url: GLib.idle_add(self._open_login_url, url),
                on_output=lambda line: GLib.idle_add(self._report_connect_output, line),
            )
            GLib.idle_add(self._c_done, connection.connected, connection)

        threading.Thread(target=run, daemon=True).start()

    def _open_login_url(self, url: str) -> bool:
        """Hand the sign-in URL to the user's browser, raised to the front."""
        self._c_phase = 0.6
        self._c_progress.update(self._c_phase, _("Complete the sign-in in your browser."))
        self._c_log.append(url)
        open_uri(self, url)
        return False

    def _report_connect_output(self, line: str) -> bool:
        self._c_phase = max(getattr(self, "_c_phase", 0.05), 0.3)
        self._c_log.append(line)
        self._c_progress.update(self._c_phase, line[:80])
        return False

    def _c_done(self, success, connection=None):
        self._btn_connect.set_sensitive(True)
        self._c_spinner.stop()
        self._c_spinner.set_visible(False)
        if success:
            self._c_progress.update(1.0, _("Connected successfully!"))
            # The join form has nothing left to ask: leaving its button on screen
            # invites a second sign-in to a network this PC is already on. This is
            # the same state a PC that was already connected opens the page in.
            self._apply_connected(True)

            # Save ALL filled fields to history
            entry = {"vpn": self.vpn_id}
            if self.vpn_id == "headscale":
                entry["domain"] = self._e_domain.get_text().strip()
                if hasattr(self, "_e_key"):
                    key_val = self._e_key.get_text().strip()
                    if key_val:
                        entry["auth_key"] = key_val
            elif self.vpn_id == "tailscale":
                entry["domain"] = self._e_server.get_text().strip() or "tailscale.com"
                if hasattr(self, "_e_key"):
                    key_val = self._e_key.get_text().strip()
                    if key_val:
                        entry["auth_key"] = key_val
            elif self.vpn_id == "zerotier":
                entry["network_id"] = self._e_netid.get_text().strip()
            _save_history(entry)

            if self.vpn_id in ("tailscale", "headscale"):
                provider = self.vpn_id
                login_server = entry.get("domain", "") if provider == "headscale" else ""

                def remember_profile() -> None:
                    manager = VPNAccountManager(self.main_window.system_check)
                    profiles = manager.list_tailscale_profiles()
                    selected = profiles.selected
                    if selected is not None:
                        manager.set_tailscale_metadata(
                            selected.profile_id,
                            provider=provider,
                            login_server=str(login_server),
                        )

                threading.Thread(target=remember_profile, daemon=True).start()

            self.main_window.show_toast(_("Connected successfully!"))
            self._return_to_game.set_visible(True)
        else:
            # A pending browser sign-in is not a failure of this PC's setup, and
            # telling people to "try again" when they simply have not finished
            # signing in sends them round the same loop.
            awaiting = connection is not None and connection.awaiting_authentication
            message = _("Sign-in was not completed in the browser.") if awaiting else _("Connection failed")
            self._return_to_game.set_visible(False)
            self._c_progress.update(0, message)
            self._c_lbl.set_label(_("Try Again"))
            self.main_window.show_toast(message)

    def _refresh_history(self):
        # Clear all children from the hist_list (Gtk.Box — safe to iterate directly)
        child = self._hist_list.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self._hist_list.remove(child)
            child = next_child

        history = _load_history()
        if not history:
            sp = Adw.StatusPage(title=_("No previous networks"), icon_name="brp-document-open-recent-symbolic", description=_("Your created/connected networks will appear here."))
            self._hist_list.append(sp)
            return

        for entry in reversed(history):
            vpn_id = entry.get("vpn", "headscale")
            vpn_meta = VPN_META.get(vpn_id, {})
            vpn_name = vpn_meta.get("name", vpn_id)
            vpn_icon = vpn_meta.get("icon", "brp-network-private-symbolic")
            domain = entry.get("domain") or entry.get("network_id") or "?"
            ts = entry.get("timestamp", "")

            grp = Adw.PreferencesGroup()
            grp.set_title(f"{vpn_name} – {domain}")
            grp.set_description(ts)

            # VPN provider logo + Action buttons in header
            header_box = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)

            # VPN Logo on the left side of header
            logo = create_icon_widget(vpn_icon, size=20)
            header_box.append(logo)

            # Separator
            sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
            sep.set_margin_top(4)
            sep.set_margin_bottom(4)
            header_box.append(sep)

            # Reconnect button
            btn_conn = Gtk.Button()
            btn_conn.set_child(create_icon_widget("brp-network-transmit-receive-symbolic", size=14))
            btn_conn.add_css_class("flat")
            name_icon_button(btn_conn, _("Reconnect"), _("Reconnect using this saved network"))
            btn_conn.connect("clicked", lambda b, e=entry: self._reconnect_from_history(e))
            header_box.append(btn_conn)

            # Edit button
            btn_edit = Gtk.Button()
            btn_edit.set_child(create_icon_widget("brp-edit-symbolic", size=14))
            btn_edit.add_css_class("flat")
            name_icon_button(btn_edit, _("Edit"), _("Edit this saved network"))
            btn_edit.connect("clicked", lambda b, e=entry: self._edit_history_entry(e))
            header_box.append(btn_edit)

            # Delete button
            btn_del = Gtk.Button()
            btn_del.set_child(create_icon_widget("brp-trash-symbolic", size=14))
            btn_del.add_css_class("flat")
            btn_del.add_css_class("destructive-action")
            name_icon_button(btn_del, _("Delete"), _("Delete this saved network"))
            btn_del.connect("clicked", lambda b, e=entry: self._delete_history_entry(e))
            header_box.append(btn_del)

            grp.set_header_suffix(header_box)

            for label, key, icon in [
                (_("Domain"), "domain", "brp-cloud-symbolic"),
                (_("Network ID"), "network_id", "brp-address-symbolic"),
                (_("Auth Key"), "auth_key", "brp-view-reveal-symbolic"),
                (_("Web UI"), "web_ui", "brp-help-browser-symbolic"),
                (_("VPN"), "vpn", "brp-network-private-symbolic"),
            ]:
                is_secret = key in _HISTORY_SECRET_KEYS
                val = _entry_secret(entry, key) if is_secret else entry.get(key, "")
                if not val:
                    continue
                subtitle = _("Stored in system keyring") if is_secret and _history_secret_key(entry, key) else val
                row = Adw.ActionRow(title=label, subtitle=subtitle, use_markup=False)
                row.add_prefix(create_icon_widget(icon, size=14))
                btn_c = Gtk.Button()
                btn_c.set_child(create_icon_widget("brp-edit-copy-symbolic", size=12))
                btn_c.add_css_class("flat")
                btn_c.set_valign(Gtk.Align.CENTER)
                name_icon_button(btn_c, _("Copy {}").format(label))
                btn_c.connect("clicked", lambda b, v=val: self._copy(v))
                row.add_suffix(btn_c)
                grp.add(row)

            self._hist_list.append(grp)

    def _reconnect_from_history(self, entry):
        vpn_id = entry.get("vpn", self.vpn_id)
        vpn_name = VPN_META.get(vpn_id, {}).get("name", vpn_id)
        # The filled form is on the page behind this sheet.
        self._history_dialog.close()

        # Navigate to the respective VPN provider's Connect page in main_window
        if hasattr(self.main_window, "_apply_vpn_selection"):
            self.main_window._apply_vpn_selection(vpn_id)
            # After applying, navigate to connect_private and switch to connect tab
            GLib.idle_add(lambda: self.main_window.navigate_to("connect_private"))

            # Fill the form fields after the new view is built
            def _fill_form():
                if hasattr(self.main_window, "connect_private_view"):
                    view = self.main_window.connect_private_view
                    # ConnectPage is the child of PrivateNetworkView (Adw.Bin)
                    page = view.get_child() if hasattr(view, "get_child") else None
                    if page is not None:
                        if vpn_id == "headscale":
                            if hasattr(page, "_e_domain"):
                                page._e_domain.set_text(entry.get("domain", ""))
                            if hasattr(page, "_e_key"):
                                page._e_key.set_text(_entry_secret(entry, "auth_key"))
                        elif vpn_id == "tailscale":
                            if hasattr(page, "_e_server"):
                                page._e_server.set_text(entry.get("domain", "") if entry.get("domain") != "tailscale.com" else "")
                            if hasattr(page, "_e_key"):
                                page._e_key.set_text(_entry_secret(entry, "auth_key"))
                        elif vpn_id == "zerotier":
                            if hasattr(page, "_e_netid"):
                                page._e_netid.set_text(entry.get("network_id", ""))
                self.main_window.show_toast(_("Form filled for {}").format(vpn_name))

            GLib.timeout_add(300, _fill_form)
        else:
            # Fallback: fill this page's own form
            if vpn_id == "headscale" and hasattr(self, "_e_domain"):
                self._e_domain.set_text(entry.get("domain", ""))
                if hasattr(self, "_e_key"):
                    self._e_key.set_text(_entry_secret(entry, "auth_key"))
            elif vpn_id == "tailscale" and hasattr(self, "_e_server"):
                self._e_server.set_text(entry.get("domain", "") if entry.get("domain") != "tailscale.com" else "")
                if hasattr(self, "_e_key"):
                    self._e_key.set_text(_entry_secret(entry, "auth_key"))
            elif vpn_id == "zerotier" and hasattr(self, "_e_netid"):
                self._e_netid.set_text(entry.get("network_id", ""))
            self.main_window.show_toast(_("Form filled for {}").format(vpn_name))

    def _edit_history_entry(self, entry):
        """Open a dialog to edit the fields of a history entry."""
        vpn_id = str(entry.get("vpn") or "headscale")
        vpn_name = str(VPN_META.get(vpn_id, {}).get("name") or vpn_id)

        dialog = Adw.Window(transient_for=self.main_window)
        dialog.add_css_class("brp-dialog")
        dialog.set_modal(True)
        dialog.set_title(_("Edit – {}").format(vpn_name))
        dialog.set_default_size(450, 350)

        toolbar_view = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        hb.set_title_widget(Adw.WindowTitle.new(_("Edit Network Entry"), vpn_name))
        toolbar_view.add_top_bar(hb)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        box.set_margin_start(24)
        box.set_margin_end(24)

        grp = Adw.PreferencesGroup()
        grp.set_title(_("Connection Details"))
        grp.set_header_suffix(create_icon_widget(VPN_META.get(vpn_id, {}).get("icon", "brp-network-private-symbolic"), size=20))

        edit_fields = {}

        if vpn_id == "headscale":
            e_domain = Adw.EntryRow(title=_("Domain"))
            e_domain.set_text(entry.get("domain", ""))
            grp.add(e_domain)
            edit_fields["domain"] = e_domain

            e_key = Adw.PasswordEntryRow(title=_("Auth Key"))
            e_key.set_text(_entry_secret(entry, "auth_key"))
            grp.add(e_key)
            edit_fields["auth_key"] = e_key

        elif vpn_id == "tailscale":
            e_domain = Adw.EntryRow(title=_("Login Server"))
            e_domain.set_text(entry.get("domain", "") if entry.get("domain") != "tailscale.com" else "")
            grp.add(e_domain)
            edit_fields["domain"] = e_domain

            e_key = Adw.PasswordEntryRow(title=_("Auth Key"))
            e_key.set_text(_entry_secret(entry, "auth_key"))
            grp.add(e_key)
            edit_fields["auth_key"] = e_key

        elif vpn_id == "zerotier":
            e_netid = Adw.EntryRow(title=_("Network ID"))
            e_netid.set_text(entry.get("network_id", ""))
            grp.add(e_netid)
            edit_fields["network_id"] = e_netid

        # Web UI (read-only display, editable)
        if entry.get("web_ui"):
            e_webui = Adw.EntryRow(title=_("Web UI"))
            e_webui.set_text(entry.get("web_ui", ""))
            grp.add(e_webui)
            edit_fields["web_ui"] = e_webui

        box.append(grp)

        # Buttons
        btn_box = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, margin_top=8)

        btn_save = Gtk.Button(label=_("Save"))
        btn_save.add_css_class("suggested-action")
        btn_save.set_size_request(140, 40)

        btn_cancel = Gtk.Button(label=_("Cancel"))
        btn_cancel.set_size_request(140, 40)

        def on_save(b):
            updated = {"vpn": vpn_id}
            for key, widget in edit_fields.items():
                val = widget.get_text().strip()
                if val:
                    updated[key] = val
                elif key == "domain" and vpn_id == "tailscale":
                    updated[key] = "tailscale.com"
            _update_history(entry.get("id"), updated)
            self._refresh_history()
            self.main_window.show_toast(_("Entry updated"))
            dialog.close()

        btn_save.connect("clicked", on_save)
        btn_cancel.connect("clicked", lambda b: dialog.close())

        btn_box.append(btn_save)
        btn_box.append(btn_cancel)
        box.append(btn_box)

        toolbar_view.set_content(box)
        dialog.set_content(toolbar_view)
        dialog.present()

    def _delete_history_entry(self, entry):
        d = Adw.AlertDialog(heading=_("Delete entry?"), body=_("Remove this network from history?"))
        d.add_response("cancel", _("Cancel"))
        d.add_response("delete", _("Delete"))
        d.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_default_response("cancel")
        d.set_close_response("cancel")

        def on_resp(_dlg, resp):
            if resp == "delete":
                _delete_history(entry.get("id"))
                self._refresh_history()

        d.connect("response", on_resp)
        d.present(self.main_window)

    def _show_connect_log(self):
        dialog = Adw.Window(transient_for=self.main_window)
        dialog.set_modal(False)
        dialog.set_title(_("Connection Log"))
        dialog.set_default_size(700, 500)
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        tv.add_top_bar(hb)
        tv.set_content(self._c_log)
        dialog.set_content(tv)
        dialog.present()

    def _show_headscale_instructions(self):
        """Show step-by-step instructions in a dialog using Adw.ToolbarView."""

        # Create the window
        dialog = Adw.Window(transient_for=self.main_window)
        dialog.add_css_class("brp-dialog")
        dialog.set_modal(True)
        dialog.set_title(_("Join a Headscale VPN"))
        dialog.set_default_size(700, 650)

        # ToolbarView
        toolbar_view = Adw.ToolbarView()

        # Header bar
        hb = Adw.HeaderBar()
        hb.set_title_widget(Adw.WindowTitle.new(_("Join a Headscale VPN"), _("Step-by-step guide to join a private network")))
        toolbar_view.add_top_bar(hb)

        # Scrollable content
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(650)
        for m in ["top", "bottom", "start", "end"]:
            getattr(clamp, f"set_margin_{m}")(16)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)

        # ════════════════════════════════════════════
        #  HOW TO CONNECT
        # ════════════════════════════════════════════
        g1 = Adw.PreferencesGroup()
        g1.set_title(_("Steps to join Headscale"))

        r1_1 = Adw.ActionRow()
        r1_1.set_title(_("Step 1: Get credentials"))
        r1_1.set_subtitle(_("Ask the network administrator for the Server Domain and Auth Key."))
        r1_1.add_prefix(create_icon_widget("brp-view-reveal-symbolic", size=20))
        g1.add(r1_1)

        r1_2 = Adw.ActionRow()
        r1_2.set_title(_("Step 2: Enter details"))
        r1_2.set_subtitle(_("Paste the Domain and Key in the fields on the previous tab."))
        r1_2.add_prefix(create_icon_widget("brp-edit-copy-symbolic", size=20))
        g1.add(r1_2)

        r1_3 = Adw.ActionRow()
        r1_3.set_title(_("Step 3: Connect"))
        r1_3.set_subtitle(_("Click 'Establish Connection' and wait for the success message."))
        r1_3.add_prefix(create_icon_widget("brp-view-refresh-symbolic", size=20))
        g1.add(r1_3)

        main_box.append(g1)

        # Set content
        clamp.set_child(main_box)
        scroll.set_child(clamp)
        toolbar_view.set_content(scroll)

        dialog.set_content(toolbar_view)
        dialog.present()

    def _show_tailscale_instructions(self):
        self._show_simple_instructions(
            _("Tailscale Instructions"),
            [
                (
                    _("1. Create Account"),
                    _("Access Tailscale"),
                    _("All participants need to sign in so their PCs join the same Private Network."),
                    "brp-tailscale-symbolic",
                    _("Create Account"),
                    "https://login.tailscale.com",
                ),
                (_("2. Invitation"), _("Request Access"), _("The administrator must share the node or invite your email to the network."), "brp-text-x-generic-symbolic", None, None),
                (_("3. Connect"), _("Establish Connection"), _("Once both computers are on the same private network, return to Connect to find the game PC."), "brp-view-refresh-symbolic", None, None),
            ],
        )

    def _show_zerotier_instructions(self):
        self._show_simple_instructions(
            _("ZeroTier Instructions"),
            [
                (_("1. Identification"), _("Get Network ID"), _("Request the 16-character ID from the network owner."), "brp-preferences-other-symbolic", None, None),
                (_("2. Join"), _("Enter ID"), _("Enter the ID on the 'Connect' tab and click 'Establish Connection'."), "brp-edit-copy-symbolic", None, None),
                (
                    _("3. Authorization"),
                    _("Wait for Approval"),
                    _("The administrator must check the 'Auth' option for your PC in their panel."),
                    "brp-network-idle-symbolic",
                    _("ZT Panel"),
                    "https://my.zerotier.com",
                ),
            ],
        )

    def _show_simple_instructions(self, title_text, items):
        show_simple_instructions(self.main_window, title_text, items)

    def _copy(self, text):
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(text)
        self.main_window.show_toast(_("Copied!"))


# ─── MAIN VIEW (Wraps Create + Connect in a Stack) ────────────────────────────
class PrivateNetworkView(Adw.Bin):
    """
    Entry point. Shows either the CreatePage or ConnectPage based on `mode`.
    """

    def __init__(self, main_window, mode="create", vpn_provider="headscale", add_account=False):
        super().__init__()
        self.main_window = main_window
        self.mode = mode
        self.vpn_provider = vpn_provider if vpn_provider in VPN_META else "headscale"

        if mode == "create":
            page = CreatePage(self.vpn_provider, main_window)
        else:
            page = ConnectPage(self.vpn_provider, main_window, add_account=add_account)

        self.set_child(page)
