import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
import json, os, re, subprocess, threading, time, shutil
from gi.repository import Adw, Gdk, GLib, Gtk  # type: ignore
from big_remote_play.utils.i18n import _
from big_remote_play.utils.icons import create_icon_widget
from big_remote_play.utils.widgets import create_helper_card, create_page_header, create_stack_tab_strip
from big_remote_play import paths
from big_remote_play.utils.secure_io import secure_write_text
from big_remote_play.utils.secret_store import SecretKey, SecretStore, SecretStoreUnavailable, new_secret_id
from big_remote_play.utils.script_protocol import parse_script_line


def open_uri(uri: str) -> None:
    """Open a URI in the default handler without a shell (no injection risk)"""
    subprocess.Popen(["xdg-open", uri], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ─── Config ───────────────────────────────────────────────────────────────────
_OLD_CFG = os.path.expanduser("~/.config/big-remoteplay")
_NEW_CFG = os.path.expanduser("~/.config/big-remote-play")
if os.path.exists(_OLD_CFG) and not os.path.exists(_NEW_CFG):
    try:
        shutil.move(_OLD_CFG, _NEW_CFG)
    except Exception:
        pass

HISTORY_FILE = os.path.join(_NEW_CFG, "private_network/history.json")
LEGACY_ZT_TOKEN_FILE = os.path.join(_NEW_CFG, "zerotier/api_token.txt")
_SECRET_STORE = SecretStore()
_ZEROTIER_TOKEN_KEY = SecretKey("zerotier", "api_token", "default")
_HISTORY_SECRET_KEYS = {"auth_key", "api_key"}

VPN_META = {
    "headscale": {
        "name": "Headscale",
        "icon": "headscale-symbolic",
        "color": "#3584e4",
        "create_title": _("Create Headscale Server"),
        "create_desc": _("Set up your own private VPN server using Docker + Cloudflare DNS."),
        "connect_title": _("Connect to Headscale Network"),
        "connect_desc": _("Enter the server domain and auth key provided by the administrator."),
        "script": "create-network_headscale.sh",
    },
    "tailscale": {
        "name": "Tailscale",
        "icon": "tailscale-symbolic",
        "color": "#26a269",
        "create_title": _("Login to Tailscale"),
        "create_desc": _("Login to your Tailscale account. No server required."),
        "connect_title": _("Connect to Tailscale Network"),
        "connect_desc": _("Enter your auth key or login server to join a Tailscale network."),
        "script": "create-network_tailscale.sh",
    },
    "zerotier": {
        "name": "ZeroTier",
        "icon": "zerotier-symbolic",
        "color": "#e5a50a",
        "create_title": _("Create ZeroTier Network"),
        "create_desc": _("Create a new ZeroTier virtual network using your API token."),
        "connect_title": _("Connect to ZeroTier Network"),
        "connect_desc": _("Enter the 16-character Network ID to join a ZeroTier network."),
        "script": "create-network_zerotier.sh",
    },
}


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


def _save_history(entry):
    history = _load_history()
    new_id = max((h.get("id", 0) for h in history), default=0) + 1
    history_entry = dict(entry)
    history_entry["id"] = new_id
    history_entry["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
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
        self._status = Gtk.Label(label="", halign=Gtk.Align.START)
        self._status.add_css_class("caption")
        self.append(self._status)
        row = Gtk.Box(spacing=8)
        self._bar = Gtk.LevelBar(min_value=0, max_value=1, hexpand=True)
        self._bar.set_size_request(-1, 14)
        self._bar.remove_offset_value(Gtk.LEVEL_BAR_OFFSET_LOW)
        self._bar.remove_offset_value(Gtk.LEVEL_BAR_OFFSET_HIGH)
        self._bar.remove_offset_value(Gtk.LEVEL_BAR_OFFSET_FULL)
        row.append(self._bar)
        self._pct = Gtk.Label(label="0%")
        self._pct.add_css_class("caption-heading")
        self._pct.set_size_request(38, -1)
        row.append(self._pct)
        if on_show_log:
            btn = Gtk.Button()
            btn.set_child(create_icon_widget("preferences-system-symbolic", size=16))
            btn.add_css_class("flat")
            btn.add_css_class("circular")
            btn.connect("clicked", lambda b: on_show_log())
            row.append(btn)
        self.append(row)

    def update(self, fraction, status=""):
        GLib.idle_add(self._set, fraction, status)

    def _set(self, fraction, status):
        self.set_visible(True)
        self._bar.set_value(fraction)
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
        self._logged_in = self._check_logged_in()
        self._build()

    def _check_logged_in(self):
        if self.vpn_id in ("tailscale", "headscale"):
            try:
                r = subprocess.run(["tailscale", "status"], capture_output=True, text=True, timeout=10)
                # If logged in and has a valid IP/DNS
                return r.returncode == 0 and "Logged out" not in r.stdout
            except Exception:
                return False
        if self.vpn_id == "zerotier":
            if _has_zerotier_api_token():
                return True
            try:
                r = subprocess.run(["zerotier-cli", "listnetworks"], capture_output=True, text=True, timeout=10)
                return r.returncode == 0 and "200 listnetworks <nwid>" in r.stdout.lower() or len(r.stdout.splitlines()) > 1
            except Exception:
                return False
        return False

    def _build(self):
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        clamp = Adw.Clamp(maximum_size=780)
        for m in ["top", "bottom", "start", "end"]:
            getattr(clamp, f"set_margin_{m}")(24)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)

        content.append(
            create_page_header(
                self.vpn["create_title"],
                self.vpn["create_desc"],
                self.vpn["icon"],
            )
        )

        # Form group
        self._form_group = Adw.PreferencesGroup()
        self._form_group.set_title(_("Configuration"))
        self._form_rows = []
        self._build_form()
        content.append(self._form_group)

        # Progress
        self._progress = ProgressRow(on_show_log=self._show_log)
        content.append(self._progress)

        # Log view (hidden in scrolled, shown in dialog)
        self._log = LogView()

        # Buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_box.set_halign(Gtk.Align.CENTER)
        btn_box.set_margin_top(8)
        btn_box.set_margin_bottom(16)

        self._spinner = Gtk.Spinner()
        self._spinner.set_visible(False)
        self._action_lbl = Gtk.Label(label=self._action_label())
        btn_inner = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        btn_inner.append(self._spinner)
        btn_inner.append(self._action_lbl)

        self._btn_action = Gtk.Button()
        self._btn_action.add_css_class("pill")
        self._btn_action.add_css_class("suggested-action")
        self._btn_action.set_size_request(200, 48)
        self._btn_action.set_child(btn_inner)
        self._btn_action.connect("clicked", self._on_action)
        btn_box.append(self._btn_action)

        self._btn_instr = Gtk.Button(label=_("Instructions"))
        self._btn_instr.add_css_class("pill")
        self._btn_instr.set_size_request(180, 48)
        self._btn_instr.connect("clicked", self._on_instructions_clicked)
        btn_box.append(self._btn_instr)

        self._btn_logout = Gtk.Button(label=_("Logout / Disconnect"))
        self._btn_logout.add_css_class("pill")
        self._btn_logout.add_css_class("destructive-action")
        self._btn_logout.set_size_request(180, 48)
        self._btn_logout.connect("clicked", self._on_logout)
        self._btn_logout.set_visible(self._logged_in)
        btn_box.append(self._btn_logout)

        content.append(btn_box)

        # Network list (shown after login for zerotier/tailscale)
        self._networks_group = Adw.PreferencesGroup()
        self._networks_group.set_title(_("Your Networks"))
        self._networks_group.set_visible(False)
        self._network_rows = []  # track rows added via .add() so we can remove them safely
        content.append(self._networks_group)

        clamp.set_child(content)
        scroll.set_child(clamp)
        self.append(scroll)

        if self._logged_in:
            GLib.idle_add(self._refresh_networks)

    def _action_label(self):
        if self.vpn_id == "tailscale":
            return _("Login / Connect")
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

        elif self.vpn_id == "tailscale":
            self._e_authkey = Adw.PasswordEntryRow(title=_("Auth Key (optional – leave empty for browser login)"))
            self._form_group.add(self._e_authkey)
            self._form_rows.append(self._e_authkey)
            link = Adw.ActionRow(title=_("Get auth key"), subtitle=_("login.tailscale.com/admin/settings/keys"))
            link.add_prefix(create_icon_widget("network-wired-symbolic", size=18))
            btn = Gtk.Button(label=_("Open"))
            btn.add_css_class("flat")
            btn.set_valign(Gtk.Align.CENTER)
            btn.connect("clicked", lambda b: open_uri("https://login.tailscale.com/admin/settings/keys"))
            link.add_suffix(btn)
            self._form_group.add(link)
            self._form_rows.append(link)

        elif self.vpn_id == "zerotier":
            saved = _get_zerotier_api_token()
            self._e_zt_token = Adw.PasswordEntryRow(title=_("ZeroTier API Token"))
            if saved:
                self._e_zt_token.set_text(saved)
            self._e_zt_name = Adw.EntryRow(title=_("Network Name"))
            self._e_zt_name.set_text("my-game-network")
            self._form_group.add(self._e_zt_token)
            self._form_group.add(self._e_zt_name)
            self._form_rows.extend([self._e_zt_token, self._e_zt_name])
            link = Adw.ActionRow(title=_("Get API Token"), subtitle=_("my.zerotier.com → Account → API Access Tokens"))
            link.add_prefix(create_icon_widget("network-wired-symbolic", size=18))
            btn = Gtk.Button(label=_("Open"))
            btn.add_css_class("flat")
            btn.set_valign(Gtk.Align.CENTER)
            btn.connect("clicked", lambda b: open_uri("https://my.zerotier.com"))
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
        elif self.vpn_id == "tailscale":
            self._run_tailscale_login()
        elif self.vpn_id == "zerotier":
            self._run_zerotier_create()

    def _run_script(self, script_name, inputs, on_done):
        script = _get_script(script_name)
        try:
            os.chmod(script, 0o755)
        except Exception:
            pass

        def run():
            # LC_ALL=C / LANGUAGE= force the script's gettext prose back to its
            # English msgid; data is parsed from locale-independent BRP_* markers.
            # `env` is passed through bigsudo so it survives privilege elevation.
            proc = subprocess.Popen(["bigsudo", "env", "LC_ALL=C", "LANGUAGE=", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
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
                return
            for line in proc_stdout:
                kind = parse_script_line(line)
                if kind[0] == "data":
                    if kind[2]:
                        captured[kind[1]] = kind[2]
                elif kind[0] == "phase":
                    phase = kind[1]
                    self._progress.update(phase, "")
                elif kind[1]:
                    self._log.append(kind[1])
                    self._progress.update(phase, kind[1][:80])

            code = proc.wait()
            GLib.idle_add(on_done, code, captured)

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
                self._progress.update(1.0, _("✅ Server installed!"))
                data.setdefault("vpn", "headscale")
                data.setdefault("domain", d)
                _save_history(data)
                self.main_window.show_toast(_("Headscale server installed!"))
                self._show_access_info(data)
            else:
                self._progress.update(0, _("❌ Failed"))
                self.main_window.show_toast(_("Installation failed. Check the log."))

        self._run_script("create-network_headscale.sh", inputs, done)

    def _run_tailscale_login(self):
        key = self._e_authkey.get_text().strip() if hasattr(self, "_e_authkey") else ""
        # Script needs: 1 (Login), 2 (Auth Key), the Key, then Enter to clear prompt, then 0 to exit.
        if key:
            inputs = ["1\n", "2\n", f"{key}\n", "\n", "0\n"]
        else:
            inputs = ["1\n", "1\n", "\n", "0\n"]

        def done(code, data):
            self._finish_action()
            if code == 0:
                self._progress.update(1.0, _("✅ Connected!"))
                self._logged_in = True
                self._btn_logout.set_visible(True)
                _save_history({"vpn": "tailscale", "domain": "Default Login"})
                self.main_window.show_toast(_("Tailscale connected!"))
                self._refresh_networks()
            else:
                self._progress.update(0, _("❌ Failed"))
                self.main_window.show_toast(_("Login failed"))

        self._run_script("create-network_tailscale.sh", inputs, done)

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
                self._progress.update(1.0, _("✅ Network created!"))
                self._logged_in = True
                self._btn_logout.set_visible(True)
                data["vpn"] = "zerotier"
                data["name"] = name
                _save_history(data)
                self.main_window.show_toast(_("ZeroTier network created!"))
                self._refresh_networks()
                self._show_access_info(data)
            else:
                self._progress.update(0, _("❌ Failed"))
                self.main_window.show_toast(_("Network creation failed"))

        self._run_script("create-network_zerotier.sh", inputs, done)

    def _finish_action(self):
        GLib.idle_add(self._do_finish)

    def _do_finish(self):
        self._btn_action.set_sensitive(True)
        self._spinner.stop()
        self._spinner.set_visible(False)

    def _show_headscale_instructions(self):
        """Show step-by-step instructions in a dialog using Adw.ToolbarView."""

        # Create the window
        dialog = Adw.Window(transient_for=self.main_window)
        dialog.set_modal(True)
        dialog.set_title(_("Setup Instructions"))
        dialog.set_default_size(700, 650)

        # ToolbarView
        toolbar_view = Adw.ToolbarView()

        # Header bar
        hb = Adw.HeaderBar()
        hb.set_title_widget(Adw.WindowTitle.new(_("Setup Instructions"), _("Step-by-step guide to create your private network")))
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
        #  STEP 1: Register Free Domain
        # ════════════════════════════════════════════
        g1 = Adw.PreferencesGroup()
        g1.set_title(_("1. Register a Free Domain"))
        g1.set_description(_("Get a free .us.kg domain to use with your server"))

        r1_1 = Adw.ActionRow()
        r1_1.set_title(_("Access DigitalPlat Domain"))
        r1_1.set_subtitle(_("Register and get your free domain (e.g.: myserver.us.kg)"))
        r1_1.add_prefix(create_icon_widget("network-wired-symbolic", size=20))

        btn_digitalplat = Gtk.Button(label=_("Open Site"))
        btn_digitalplat.add_css_class("pill")
        btn_digitalplat.add_css_class("suggested-action")
        btn_digitalplat.set_valign(Gtk.Align.CENTER)
        btn_digitalplat.connect("clicked", lambda b: open_uri("https://dash.domain.digitalplat.org/"))
        r1_1.add_suffix(btn_digitalplat)
        g1.add(r1_1)

        r1_2 = Adw.ActionRow()
        r1_2.set_title(_("Steps at DigitalPlat"))
        r1_2.set_subtitle(_("1. Create an account or login\n2. Choose a .us.kg domain\n3. Complete the simple registration\n4. Write down the domain you obtained (e.g.: myserver.us.kg)"))
        r1_2.add_prefix(create_icon_widget("preferences-other-symbolic", size=20))
        g1.add(r1_2)

        main_box.append(g1)

        # ════════════════════════════════════════════
        #  STEP 2: Configure Cloudflare
        # ════════════════════════════════════════════
        g2 = Adw.PreferencesGroup()
        g2.set_title(_("2. Configure Cloudflare"))
        g2.set_description(_("Point your domain to Cloudflare for DNS management"))

        r2_1 = Adw.ActionRow()
        r2_1.set_title(_("Access Cloudflare Dashboard"))
        r2_1.set_subtitle(_("Create a free account and add your domain"))
        r2_1.add_prefix(create_icon_widget("network-wired-symbolic", size=20))

        btn_cloudflare = Gtk.Button(label=_("Open Cloudflare"))
        btn_cloudflare.add_css_class("pill")
        btn_cloudflare.add_css_class("suggested-action")
        btn_cloudflare.set_valign(Gtk.Align.CENTER)
        btn_cloudflare.connect("clicked", lambda b: open_uri("https://dash.cloudflare.com/"))
        r2_1.add_suffix(btn_cloudflare)
        g2.add(r2_1)

        r2_2 = Adw.ActionRow()
        r2_2.set_title(_("Setup Steps"))
        r2_2.set_subtitle(
            _(
                "1. Click 'Add a site' → Enter your domain\n"
                "2. Choose the FREE plan\n"
                "3. Write down the 2 nameservers provided\n"
                "4. Go back to your domain provider (DigitalPlat)\n"
                "5. Replace the DNS with Cloudflare's nameservers\n"
                "6. Wait for DNS propagation (may take a few minutes)"
            )
        )
        r2_2.add_prefix(create_icon_widget("preferences-other-symbolic", size=20))
        g2.add(r2_2)

        # Button to go back to domain panel for NS update
        r2_3 = Adw.ActionRow()
        r2_3.set_title(_("Update Nameservers"))
        r2_3.set_subtitle(_("Open domain panel to change NS records"))
        r2_3.add_prefix(create_icon_widget("preferences-system-symbolic", size=20))

        btn_domain_panel = Gtk.Button(label=_("Domain Panel"))
        btn_domain_panel.add_css_class("pill")
        btn_domain_panel.set_valign(Gtk.Align.CENTER)
        btn_domain_panel.connect(
            "clicked",
            lambda b: open_uri("https://dash.domain.digitalplat.org/panel/main?page=%2Fpanel%2Fdomains"),
        )
        r2_3.add_suffix(btn_domain_panel)
        g2.add(r2_3)

        main_box.append(g2)

        # ════════════════════════════════════════════
        #  STEP 3: Get API Credentials
        # ════════════════════════════════════════════
        g3 = Adw.PreferencesGroup()
        g3.set_title(_("3. Get API Credentials"))
        g3.set_description(_("Obtain your Zone ID and API Token from Cloudflare"))

        r3_1 = Adw.ActionRow()
        r3_1.set_title(_("Zone ID"))
        r3_1.set_subtitle(_("1. In Cloudflare, click on your domain\n2. Scroll down to the 'API' section on the right\n3. Copy the 'Zone ID' value"))
        r3_1.add_prefix(create_icon_widget("view-conceal-symbolic", size=20))
        g3.add(r3_1)

        r3_2 = Adw.ActionRow()
        r3_2.set_title(_("API Token"))
        r3_2.set_subtitle(
            _(
                "1. Click 'Get your API token' (below Zone ID)\n"
                "2. Click 'Create Token'\n"
                "3. Use template: 'Edit zone DNS' → 'Use template'\n"
                "4. Configure:\n"
                "   • Token name: VPN-Token\n"
                "   • Permissions: Zone - DNS - Edit\n"
                "   • Zone: Select your domain\n"
                "5. Click 'Continue to summary' → 'Create Token'\n"
                "6. Copy token IMMEDIATELY (shown only once!)"
            )
        )
        r3_2.add_prefix(create_icon_widget("view-reveal-symbolic", size=20))
        g3.add(r3_2)

        main_box.append(g3)

        # ════════════════════════════════════════════
        #  STEP 4: Configure DNS in Cloudflare
        # ════════════════════════════════════════════
        g4 = Adw.PreferencesGroup()
        g4.set_title(_("4. Configure DNS Records in Cloudflare"))
        g4.set_description(_("Create DNS records pointing to your public IP"))

        # Public IP row with live fetch
        r4_ip = Adw.ActionRow()
        r4_ip.set_title(_("Your Public IP"))
        r4_ip.set_subtitle(_("Use this IP for the A record below"))
        r4_ip.add_prefix(create_icon_widget("network-workgroup-symbolic", size=20))

        self.instructions_ip_label = Gtk.Label(label=_("Loading..."))
        self.instructions_ip_label.add_css_class("title-4")
        self.instructions_ip_label.set_selectable(True)

        def copy_ip(b):
            txt = self.instructions_ip_label.get_label()
            display = Gdk.Display.get_default()
            if display is not None:
                display.get_clipboard().set(txt)
            self.main_window.show_toast(_("Copied!"))

        btn_copy_ip = Gtk.Button()
        btn_copy_ip.set_child(create_icon_widget("edit-copy-symbolic", size=16))
        btn_copy_ip.add_css_class("flat")
        btn_copy_ip.set_valign(Gtk.Align.CENTER)
        btn_copy_ip.set_tooltip_text(_("Copy IP"))
        btn_copy_ip.connect("clicked", copy_ip)

        ip_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ip_box.append(self.instructions_ip_label)
        ip_box.append(btn_copy_ip)
        r4_ip.add_suffix(ip_box)
        g4.add(r4_ip)

        # Fetch IP in background
        def fetch_ip():
            try:
                ip = subprocess.check_output(["curl", "-s", "ipinfo.io/ip"], timeout=10).decode().strip()
                GLib.idle_add(self.instructions_ip_label.set_label, ip)
            except Exception:
                GLib.idle_add(self.instructions_ip_label.set_label, _("Error"))

        threading.Thread(target=fetch_ip, daemon=True).start()

        r4_a = Adw.ActionRow()
        r4_a.set_title(_("A Record"))
        r4_a.set_subtitle(_("In Cloudflare → DNS → Records → 'Add record':\n• Type: A\n• Name: @\n• Content: YOUR-PUBLIC-IP (shown above)\n• Proxy: OFF (gray cloud — IMPORTANT!)"))
        r4_a.add_prefix(create_icon_widget("network-server-symbolic", size=20))
        g4.add(r4_a)

        r4_cname = Adw.ActionRow()
        r4_cname.set_title(_("CNAME Record"))
        r4_cname.set_subtitle(_("Add another record:\n• Type: CNAME\n• Name: www\n• Target: @\n• Proxy: OFF"))
        r4_cname.add_prefix(create_icon_widget("network-server-symbolic", size=20))
        g4.add(r4_cname)

        main_box.append(g4)

        # ════════════════════════════════════════════
        #  STEP 5: Configure Router
        # ════════════════════════════════════════════
        group5 = Adw.PreferencesGroup()
        group5.set_title(_("5. Configure Router (Port Forwarding)"))
        group5.set_description(_("Open ports 80, 443, and 41641. The installation script will attempt to configure these automatically via UPnP."))

        r5_1 = Adw.ActionRow()
        r5_1.set_title(_("Access Your Router"))
        r5_1.set_subtitle(_("1. Open your browser and go to 192.168.1.1 (or your router's IP)\n2. Find 'Port Forwarding' or 'NAT' settings"))
        r5_1.add_prefix(create_icon_widget("network-wired-symbolic", size=20))
        group5.add(r5_1)

        ports_data = [
            ("80/TCP", _("TLS certificate challenge and HTTP redirect")),
            ("443/TCP", _("Secure Headscale API and web interface")),
            ("41641/UDP", _("Peer-to-peer VPN data")),
        ]
        for port, desc_text in ports_data:
            pr = Adw.ActionRow()
            pr.set_title(f"Port {port}")
            pr.set_subtitle(f"{desc_text} → {_('Forward to your local IP')}")
            pr.add_prefix(create_icon_widget("network-transmit-receive-symbolic", size=20))
            group5.add(pr)

        r5_tip = Adw.ActionRow()
        r5_tip.set_title(_("Find your local IP"))
        r5_tip.set_subtitle(_("Run 'hostname -I' in a terminal to find your local IP address"))
        r5_tip.add_prefix(create_icon_widget("preferences-system-symbolic", size=20))
        group5.add(r5_tip)

        main_box.append(group5)

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
                    _("Tailscale Registration"),
                    _("Create your free account to start managing your VPN mesh."),
                    "network-wired-symbolic",
                    _("Open Site"),
                    "https://login.tailscale.com",
                ),
                (_("2. Login on Host"), _("Link this device"), _("Use the 'Login / Connect' button on the previous tab to authorize this PC."), "network-wired-symbolic", None, None),
                (
                    _("3. Admin Panel"),
                    _("Manage Machines"),
                    _("View and authorize the machines connected to your network in the official panel."),
                    "preferences-system-symbolic",
                    _("Open Panel"),
                    "https://login.tailscale.com/admin/machines",
                ),
            ],
        )

    def _show_zerotier_instructions(self):
        self._show_simple_instructions(
            _("ZeroTier Instructions"),
            [
                (_("1. Create Account"), _("Access ZeroTier Central"), _("Sign up to create and manage your virtual networks."), "network-wired-symbolic", _("Open Portal"), "https://my.zerotier.com"),
                (
                    _("2. Authentication"),
                    _("Generate API Token"),
                    _("Go to 'Account Settings' and create a new 'API Access Token'."),
                    "view-reveal-symbolic",
                    _("Generate Token"),
                    "https://my.zerotier.com/account",
                ),
                (_("3. Configuration"), _("Link Network"), _("Paste the Token in the matching field and click 'Save Token &amp; Create Network'."), "edit-copy-symbolic", None, None),
                (
                    _("4. Administration"),
                    _("Authorize Members"),
                    _("Click your network's Network ID to manage and authorize new devices."),
                    "network-server-symbolic",
                    _("My Networks"),
                    "https://my.zerotier.com/network",
                ),
            ],
        )

    def _show_simple_instructions(self, title_text, items):
        """Show instructions using the premium Adwaita style."""
        dialog = Adw.Window(transient_for=self.main_window)
        dialog.set_modal(True)
        dialog.set_title(title_text)
        dialog.set_default_size(680, 600)

        # ToolbarView
        toolbar_view = Adw.ToolbarView()

        # Header bar
        hb = Adw.HeaderBar()
        hb.set_title_widget(Adw.WindowTitle.new(title_text, _("Step-by-step guide")))
        toolbar_view.add_top_bar(hb)

        # Scrollable content
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(600)
        for m in ["top", "bottom", "start", "end"]:
            getattr(clamp, f"set_margin_{m}")(24)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)

        for item in items:
            # item tuple: (group_title, row_title, row_subtitle, icon, btn_label, btn_url)
            g_title = item[0]
            r_title = item[1]
            r_subtitle = item[2] if len(item) > 2 else ""
            icon = item[3] if len(item) > 3 else "dialog-information-symbolic"
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
                btn.add_css_class("pill")
                btn.add_css_class("suggested-action")
                btn.set_valign(Gtk.Align.CENTER)
                btn.connect("clicked", lambda b, u=btn_url: open_uri(u))
                row.add_suffix(btn)

            group.add(row)
            main_box.append(group)

        clamp.set_child(main_box)
        scroll.set_child(clamp)
        toolbar_view.set_content(scroll)

        dialog.set_content(toolbar_view)
        dialog.present()

    def _on_logout(self, btn):
        self.main_window.show_toast(_("Disconnecting..."))
        if self.vpn_id == "tailscale":

            def do_logout():
                subprocess.run(["bigsudo", "tailscale", "logout"], timeout=30)

                def finish_logout():
                    self.main_window.show_toast(_("Tailscale disconnected"))
                    self._btn_logout.set_visible(False)
                    self._networks_group.set_visible(False)
                    self._refresh_networks()
                    return False

                GLib.idle_add(finish_logout)

            threading.Thread(target=do_logout, daemon=True).start()
        elif self.vpn_id == "zerotier":
            _clear_zerotier_api_token()
            self._logged_in = False
            self._btn_logout.set_visible(False)
            self._networks_group.set_visible(False)
            self.main_window.show_toast(_("ZeroTier token removed"))

            def do_stop():
                subprocess.run(["bigsudo", "systemctl", "stop", "zerotier-one"], timeout=30)
                GLib.idle_add(self._refresh_networks)

            threading.Thread(target=do_stop, daemon=True).start()
        elif self.vpn_id == "headscale":

            def do_logout():
                subprocess.run(["bigsudo", "tailscale", "logout"], timeout=30)

                def finish_logout():
                    self.main_window.show_toast(_("Disconnected from Headscale"))
                    self._btn_logout.set_visible(False)
                    self._networks_group.set_visible(False)
                    self._refresh_networks()
                    return False

                GLib.idle_add(finish_logout)

            threading.Thread(target=do_logout, daemon=True).start()

    def _refresh_networks(self):
        threading.Thread(target=self._fetch_networks, daemon=True).start()

    def _fetch_networks(self):
        rows = []
        if self.vpn_id == "tailscale":
            try:
                r = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=10)
                if r.returncode == 0:
                    data = json.loads(r.stdout)
                    self_node = data.get("Self", {})
                    rows.append(
                        {"title": self_node.get("DNSName", "This device").split(".")[0], "subtitle": ", ".join(self_node.get("TailscaleIPs", [])), "icon": "computer-symbolic", "is_self": True}
                    )
                    for k, peer in data.get("Peer", {}).items():
                        rows.append(
                            {
                                "title": peer.get("DNSName", k).split(".")[0],
                                "subtitle": ", ".join(peer.get("TailscaleIPs", [])),
                                "icon": "network-workgroup-symbolic",
                                "online": peer.get("Online", False),
                            }
                        )
            except Exception:
                pass

            try:
                token = _get_zerotier_api_token()
                if not token:
                    return
                r = subprocess.run(["curl", "-sL", "-H", f"Authorization: token {token}", "https://api.zerotier.com/api/v1/network"], capture_output=True, text=True, timeout=10)
                stdout = r.stdout.strip()
                networks = json.loads(stdout) if (stdout and stdout.startswith("[")) else []
                for net in networks if isinstance(networks, list) else []:
                    nid = net.get("id", "")
                    nname = net.get("config", {}).get("name", nid)
                    rows.append({"title": nname, "subtitle": f"ID: {nid}", "icon": "network-wired-symbolic", "network_id": nid})
            except Exception:
                pass

        GLib.idle_add(self._populate_networks, rows)

    def _populate_networks(self, rows):
        # Remove only the rows we explicitly added (avoids touching internal layout children)
        for r in self._network_rows:
            self._networks_group.remove(r)
        self._network_rows.clear()

        if not rows:
            row = Adw.ActionRow(title=_("No networks found"), subtitle=_("Connect or create a network first"))
            row.add_prefix(create_icon_widget("preferences-other-symbolic", size=18))
            self._networks_group.add(row)
            self._network_rows.append(row)
        else:
            for r in rows:
                row = Adw.ActionRow(title=r["title"], subtitle=r.get("subtitle", ""))
                row.add_prefix(create_icon_widget("preferences-other-symbolic", size=18))
                if r.get("is_self"):
                    badge = Gtk.Label(label=_("This device"))
                    badge.add_css_class("caption")
                    badge.add_css_class("dim-label")
                    row.add_suffix(badge)
                elif "online" in r:
                    dot = create_icon_widget("media-record-symbolic", size=10)
                    dot.add_css_class("status-online" if r["online"] else "status-offline")
                    row.add_suffix(dot)
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
        grp.set_title(_("🎉 Success! Share with friends:"))
        for key, label, icon in [
            ("domain", _("Domain"), "network-server-symbolic"),
            ("network_id", _("Network ID"), "network-wired-symbolic"),
            ("auth_key", _("Auth Key (Friends)"), "key-symbolic"),
            ("web_ui", _("Web Interface"), "web-browser-symbolic"),
        ]:
            val = data.get(key, "")
            if not val:
                continue
            row = Adw.ActionRow(title=label, subtitle=val)
            row.add_prefix(create_icon_widget(icon, size=16))
            btn = Gtk.Button()
            btn.set_child(create_icon_widget("edit-copy-symbolic", size=14))
            btn.add_css_class("flat")
            btn.set_valign(Gtk.Align.CENTER)
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
                    print(f"Error saving: {e}")

            dialog_file.save(dialog, None, on_save_finish)

        def on_share(b):
            msg = _("VPN Network Details:\n\n")
            for k, l, _icon in [("domain", _("Domain"), ""), ("network_id", _("Network ID"), ""), ("auth_key", _("Auth Key"), "")]:
                if data.get(k):
                    msg += f"{l}: {data.get(k)}\n"

            import urllib.parse

            body = urllib.parse.quote(msg)
            open_uri(f"mailto:?subject=VPN Network Info&body={body}")
            self.main_window.show_toast(_("Opening mail client..."))

        btn_save = Gtk.Button(label=_("Save"))
        btn_save.add_css_class("pill")
        btn_save.add_css_class("suggested-action")
        btn_save.connect("clicked", lambda b: self.main_window.show_toast(_("Saved to history!")))

        btn_file = Gtk.Button()
        btn_file_box = Gtk.Box(spacing=8)
        btn_file_box.append(create_icon_widget("folder-open-symbolic", size=16))
        btn_file_box.append(Gtk.Label(label=_("Save to File")))
        btn_file.set_child(btn_file_box)
        btn_file.add_css_class("pill")
        btn_file.connect("clicked", on_save_file)

        btn_share = Gtk.Button()
        btn_share_box = Gtk.Box(spacing=8)
        btn_share_box.append(create_icon_widget("open-menu-symbolic", size=16))
        btn_share_box.append(Gtk.Label(label=_("Share")))
        btn_share.set_child(btn_share_box)
        btn_share.add_css_class("pill")
        btn_share.connect("clicked", on_share)

        actions_box.add_css_class("network-info-actions")
        actions_box.append(btn_save)
        actions_box.append(btn_file)
        actions_box.append(btn_share)

        # Add actions to bottom bar of ToolbarView
        tv.add_bottom_bar(actions_box)
        box.append(tv)

        dialog = Adw.Window(transient_for=self.main_window)
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

    def __init__(self, vpn_id, main_window):
        super().__init__()
        self.vpn_id = vpn_id
        self.vpn = VPN_META[vpn_id]
        self.main_window = main_window
        self._fetching = False
        self._build()

    def _build(self):
        toolbar = Adw.ToolbarView()
        stack = Adw.ViewStack()
        stack.set_vexpand(True)

        # ── Tab 1: Connect ──
        conn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        conn_box.set_margin_top(24)
        conn_box.set_margin_bottom(24)
        conn_box.set_margin_start(32)
        conn_box.set_margin_end(32)

        conn_box.append(
            create_page_header(
                self.vpn["connect_title"],
                self.vpn["connect_desc"],
                self.vpn["icon"],
            )
        )

        # Two columns: connection fields (left) + "what you'll need" helper (right).
        fields_group = Adw.PreferencesGroup()
        fields_group.set_title(_("Connection Details"))
        fields_group.set_hexpand(True)
        self._build_connect_fields(fields_group)

        helper = create_helper_card(
            _("What you'll need"),
            "dialog-question-symbolic",
            [
                ("network-server-symbolic", _("Server domain"), _("The address of your VPN server, e.g. vpn.yourcompany.com.")),
                ("dialog-password-symbolic", _("Auth key"), _("A key generated on the server to connect this device.")),
                ("network-wireless-symbolic", _("Working internet"), _("An internet connection is required to establish the link.")),
            ],
        )
        helper.set_valign(Gtk.Align.START)
        helper.set_size_request(280, -1)

        columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        columns.append(fields_group)
        columns.append(helper)
        conn_box.append(columns)

        # Privacy reassurance banner (credentials stay local).
        privacy = Gtk.Label()
        privacy.set_markup(_('<span size="small">Your credentials stay only on this device and are used exclusively to authenticate on the private network.</span>'))
        privacy.add_css_class("dim-label")
        privacy.set_wrap(True)
        privacy.set_halign(Gtk.Align.START)
        conn_box.append(privacy)

        self._c_progress = ProgressRow(on_show_log=self._show_connect_log)
        conn_box.append(self._c_progress)
        self._c_log = LogView()

        btn_row = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, margin_top=8)
        self._c_spinner = Gtk.Spinner()
        self._c_spinner.set_visible(False)
        self._c_lbl = Gtk.Label(label=_("Establish Connection"))
        inner = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        inner.append(self._c_spinner)
        inner.append(self._c_lbl)
        self._btn_connect = Gtk.Button()
        self._btn_connect.add_css_class("pill")
        self._btn_connect.add_css_class("suggested-action")
        self._btn_connect.set_size_request(220, 48)
        self._btn_connect.set_child(inner)
        self._btn_connect.connect("clicked", self._on_connect)
        btn_row.append(self._btn_connect)

        self._btn_instr = Gtk.Button(label=_("Instructions"))
        self._btn_instr.add_css_class("pill")
        self._btn_instr.set_size_request(180, 48)
        self._btn_instr.connect("clicked", self._on_instructions_clicked)
        btn_row.append(self._btn_instr)

        conn_box.append(btn_row)

        p1 = stack.add_titled(conn_box, "connect", _("Connect"))
        p1.set_icon_name("network-server-symbolic")

        # ── Tab 2: Status ──
        status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        status_box.set_margin_top(12)
        status_box.set_margin_bottom(12)
        status_box.set_margin_start(12)
        status_box.set_margin_end(12)

        hdr2 = Gtk.Box()
        t2 = Gtk.Label(label=_("Network Devices"))
        t2.add_css_class("title-4")
        hdr2.append(t2)
        btn_ref = Gtk.Button()
        btn_ref.set_child(create_icon_widget("view-refresh-symbolic", size=16))
        btn_ref.add_css_class("flat")
        btn_ref.set_halign(Gtk.Align.END)
        btn_ref.set_hexpand(True)
        btn_ref.connect("clicked", lambda b: self._refresh_status())

        hdr2.append(btn_ref)
        status_box.append(hdr2)

        self._status_banner = Adw.Banner(title=_("Set API Token to see all network devices"))
        self._status_banner.set_revealed(False)
        self._status_banner.set_button_label(_("Set Token"))
        self._status_banner.connect("button-clicked", lambda b: self._prompt_api_token())
        status_box.append(self._status_banner)

        self._peers_store = Gtk.ListStore(str, str, str, str, str, str, str)
        self._peers_tree = Gtk.TreeView(model=self._peers_store)
        cols = [_("Auth"), _("ID"), _("Name"), _("Managed IP"), _("Last Seen"), _("Version"), _("Physical IP")]
        for i, col in enumerate(cols):
            rend = Gtk.CellRendererText()
            c = Gtk.TreeViewColumn(col, rend, text=i)
            c.set_resizable(True)
            c.set_expand(i in [2, 3])  # Expand Name and IP
            self._peers_tree.append_column(c)

        scroll_tree = Gtk.ScrolledWindow(vexpand=True)
        scroll_tree.set_child(self._peers_tree)
        scroll_tree.add_css_class("card")
        status_box.append(scroll_tree)

        # Prominent API Token buttons
        self._btn_api_box = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, margin_top=12)
        self._btn_api_box.set_visible(self.vpn_id == "zerotier")

        self._btn_api_token = Gtk.Button(label=_("Set ZeroTier API Token"))
        self._btn_api_token.add_css_class("suggested-action")
        self._btn_api_token.add_css_class("pill")
        self._btn_api_token.connect("clicked", self._prompt_api_token)
        self._btn_api_box.append(self._btn_api_token)

        self._btn_get_token = Gtk.Button(label=_("Create API Access Token"))
        self._btn_get_token.add_css_class("pill")
        self._btn_get_token.connect("clicked", lambda b: open_uri("https://my.zerotier.com/account"))
        self._btn_api_box.append(self._btn_get_token)

        status_box.append(self._btn_api_box)

        p2 = stack.add_titled(status_box, "status", _("Status"))
        p2.set_icon_name("network-transmit-receive-symbolic")

        # ── Tab 3: Previous Networks ──
        self._hist_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        hist_scroll = Gtk.ScrolledWindow(vexpand=True)
        self._hist_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._hist_list.set_margin_top(16)
        self._hist_list.set_margin_bottom(16)
        self._hist_list.set_margin_start(24)
        self._hist_list.set_margin_end(24)
        hist_scroll.set_child(self._hist_list)
        self._hist_box.append(hist_scroll)

        p3 = stack.add_titled(self._hist_box, "history", _("Previous Networks"))
        p3.set_icon_name("document-open-recent-symbolic")

        stack.connect("notify::visible-child-name", self._on_tab_changed)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_top(16)
        content.set_vexpand(True)
        content.append(create_stack_tab_strip(stack, _("Private network sections")))
        content.append(stack)
        toolbar.set_content(content)
        self._stack = stack
        self.set_child(toolbar)

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
            group.add(self._e_server)
            group.add(self._e_key)

        elif self.vpn_id == "zerotier":
            self._e_netid = Adw.EntryRow(title=_("Network ID (16 characters)"))
            self._e_netid.set_tooltip_text(_("e.g. a1b2c3d4e5f6a7b8"))
            group.add(self._e_netid)
            link = Adw.ActionRow(title=_("Find Network ID"), subtitle=_("my.zerotier.com → Networks"))
            link.add_prefix(create_icon_widget("network-wired-symbolic", size=18))
            btn = Gtk.Button(label=_("Open"))
            btn.add_css_class("flat")
            btn.set_valign(Gtk.Align.CENTER)
            btn.connect("clicked", lambda b: open_uri("https://my.zerotier.com/network"))
            link.add_suffix(btn)
            group.add(link)

        self._prefill_from_history()

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
        dialog = Adw.Window(transient_for=self.main_window)
        dialog.set_modal(True)
        dialog.set_title(_("ZeroTier API Token"))
        dialog.set_default_size(400, 250)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)

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
        btn_save.add_css_class("pill")

        btn_close = Gtk.Button(label=_("Close"))
        btn_close.add_css_class("pill")

        def on_save(btn):
            token = entry.get_text().strip()
            if token:
                try:
                    _set_zerotier_api_token(token)
                    self.main_window.show_toast(_("Token saved in the system keyring"))
                    self._refresh_status()
                except SecretStoreUnavailable:
                    self.main_window.show_toast(_("System keyring is unavailable. Token was not saved."))
            dialog.close()

        btn_save.connect("clicked", on_save)
        btn_close.connect("clicked", lambda b: dialog.close())

        btn_box.append(btn_save)
        btn_box.append(btn_close)
        box.append(btn_box)

        dialog.set_content(box)
        dialog.present()

    def _on_connect(self, btn):
        self._btn_connect.set_sensitive(False)
        self._c_spinner.set_visible(True)
        self._c_spinner.start()
        self._c_lbl.set_label(_("Connecting..."))
        self._c_progress.update(0.05, _("Starting..."))
        self._c_log.clear()

        if self.vpn_id == "headscale":
            domain = self._e_domain.get_text().strip()
            key = self._e_key.get_text().strip()
            if not domain or not key:
                self.main_window.show_toast(_("Domain and Auth Key required"))
                self._c_done(False)
                return
            inputs = ["2\n", f"{domain}\n", f"{key}\n", "n\n"]
            script = "create-network_headscale.sh"

        elif self.vpn_id == "tailscale":
            key = self._e_key.get_text().strip() if hasattr(self, "_e_key") else ""
            if not key:
                self.main_window.show_toast(_("Auth Key is required"))
                self._c_done(False)
                return
            # Using Login option (1) then Auth Key option (2)
            # Future: support a custom server (self._e_server) in the script
            inputs = ["1\n", "2\n", f"{key}\n", "\n", "0\n"]
            script = "create-network_tailscale.sh"

        elif self.vpn_id == "zerotier":
            nid = self._e_netid.get_text().strip() if hasattr(self, "_e_netid") else ""
            if not nid:
                self.main_window.show_toast(_("Network ID is required"))
                self._c_done(False)
                return
            inputs = ["2\n", f"{nid}\n"]
            script = "create-network_zerotier.sh"

        def run():
            spath = _get_script(script)
            try:
                os.chmod(spath, 0o755)
            except Exception:
                pass
            # LC_ALL=C: parse locale-independent BRP_* markers, not localized prose.
            proc = subprocess.Popen(["bigsudo", "env", "LC_ALL=C", "LANGUAGE=", spath], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
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
                return
            for line in proc_stdout:
                kind = parse_script_line(line)
                if kind[0] == "data":
                    continue
                if kind[0] == "phase":
                    phase = kind[1]
                    self._c_progress.update(phase, "")
                elif kind[0] == "text" and kind[1]:
                    self._c_log.append(kind[1])
                    self._c_progress.update(phase, kind[1][:80])
            code = proc.wait()
            GLib.idle_add(lambda: self._c_done(code == 0))

        threading.Thread(target=run, daemon=True).start()

    def _c_done(self, success):
        self._btn_connect.set_sensitive(True)
        self._c_spinner.stop()
        self._c_spinner.set_visible(False)
        if success:
            self._c_progress.update(1.0, _("✅ Connected!"))
            self._c_lbl.set_label(_("Establish Connection"))

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

            self.main_window.show_toast(_("Connected successfully!"))
            GLib.timeout_add(1200, lambda: self._stack.set_visible_child_name("status"))
        else:
            self._c_progress.update(0, _("❌ Failed"))
            self._c_lbl.set_label(_("Try Again"))
            self.main_window.show_toast(_("Connection failed"))

    def _on_tab_changed(self, stack, pspec):
        name = stack.get_visible_child_name()
        if name == "status":
            self._refresh_status()
        elif name == "history":
            self._refresh_history()

    def _refresh_status(self):
        if self._fetching:
            return

        # Show banner if ZeroTier and no token
        if self.vpn_id == "zerotier":
            self._status_banner.set_revealed(not _has_zerotier_api_token())
        else:
            self._status_banner.set_revealed(False)

        self._fetching = True
        threading.Thread(target=self._fetch_status, daemon=True).start()

    def _fetch_status(self):
        rows = []
        try:
            now = time.time() * 1000

            def fmt_time(ts):
                if not ts:
                    return ""
                try:
                    ts = float(ts)
                except (ValueError, TypeError):
                    return str(ts)
                diff = (now - ts) / 1000
                if diff < 60:
                    return _("Just now")
                if diff < 3600:
                    return f"{int(diff / 60)} min"
                if diff < 86400:
                    return f"{int(diff / 3600)} hr"
                return f"{int(diff / 86400)} days"

            if self.vpn_id in ("headscale", "tailscale"):
                r = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=10)
                if r.returncode == 0:
                    data = json.loads(r.stdout)
                    self_node = data.get("Self", {})
                    ips = ", ".join(self_node.get("TailscaleIPs", []))
                    name = self_node.get("DNSName", "").split(".")[0] or "This device"
                    # Auth, ID, Name, IP, LastSeen, Ver, Phys
                    rows.append(("✅", _("Self"), name, ips, _("Online"), "", ""))

                    for peer in data.get("Peer", {}).values():
                        ip = ", ".join(peer.get("TailscaleIPs", []))
                        h = peer.get("DNSName", "").split(".")[0]
                        uid = str(peer.get("UserID", ""))
                        # Tailscale JSON LastHandshake is ISO str, complex to parse without datetime.
                        # Simplifying for now:
                        last = _("Online") if peer.get("Online") else _("Offline")
                        rows.append(("✅", uid, h, ip, last, "", ""))

            elif self.vpn_id == "zerotier":
                token = _get_zerotier_api_token()
                if token:
                    net_id_input = self._e_netid.get_text().strip()
                    if net_id_input:
                        # If we have a manual ID, focus only on it
                        networks = [{"id": net_id_input}]
                    else:
                        # Otherwise, list every network for this token
                        nets_r = subprocess.run(
                            ["curl", "-sL", "-m", "10", "-H", f"Authorization: token {token}", "https://api.zerotier.com/api/v1/network"], capture_output=True, text=True, timeout=10
                        )
                        nets_stdout = nets_r.stdout.strip()
                        networks = json.loads(nets_stdout) if (nets_stdout and nets_stdout.startswith("[")) else []

                    for net in networks if isinstance(networks, list) else []:
                        nid = net.get("id", "")
                        mem_r = subprocess.run(
                            ["curl", "-sL", "-m", "10", "-H", f"Authorization: token {token}", f"https://api.zerotier.com/api/v1/network/{nid}/member"], capture_output=True, text=True, timeout=10
                        )
                        mem_stdout = mem_r.stdout.strip()
                        members = []
                        if mem_stdout and mem_stdout.startswith("["):
                            try:
                                members = json.loads(mem_stdout)
                            except Exception:
                                pass

                        for m in members if isinstance(members, list) else []:
                            cfg = m.get("config", {})
                            mid = m.get("nodeId", "")
                            name = m.get("name") or m.get("description") or mid
                            ip_list = cfg.get("ipAssignments") or []
                            ip = ip_list[0] if ip_list else ""
                            authorized = cfg.get("authorized", False)
                            auth_icon = "✅" if authorized else "❌"
                            last_seen = m.get("lastSeen", 0)
                            seen_str = fmt_time(last_seen) if last_seen else ""
                            if m.get("online", False):
                                seen_str = _("Online")
                            ver = m.get("clientVersion", "")
                            phys = m.get("physicalAddress", "")
                            rows.append((auth_icon, mid, name, ip, seen_str, ver, phys))

                # If rows still empty, try CLI fallbacks
                if not rows:
                    r = subprocess.run(["zerotier-cli", "-j", "listnetworks"], capture_output=True, text=True, timeout=10)
                    if r.returncode == 0 and r.stdout.strip():
                        try:
                            nets = json.loads(r.stdout)
                            for net in nets:
                                n_id = net.get("id", "")
                                n_name = net.get("name", "")
                                n_status = net.get("status", "")
                                n_ips = ", ".join(net.get("assignedAddresses", []))
                                rows.append(("❓", n_id, n_name, n_ips, n_status, "", ""))
                        except Exception:
                            pass

                # If STILL empty, show peers (last resort)
                if not rows:
                    rp = subprocess.run(["zerotier-cli", "listpeers"], capture_output=True, text=True, timeout=10)
                    if rp.returncode == 0:
                        for line in rp.stdout.splitlines()[1:]:
                            p = line.split()
                            if len(p) >= 3:
                                # <ztaddr> <ver> <role> <lat> <link> <lastTX> <lastRX> <path>
                                rows.append(("🌐", p[0], _("Peer"), p[7] if len(p) > 7 else "", p[4], p[1], ""))
        except Exception as e:
            print(f"Status fetch error: {e}")
        finally:
            self._fetching = False

        GLib.idle_add(self._update_status_ui, rows)

    def _update_status_ui(self, rows):
        self._peers_store.clear()
        if not rows:
            if self.vpn_id == "zerotier" and not _has_zerotier_api_token():
                self._peers_store.append(("ℹ️", _("API Token Missing"), _("See banner above"), "", "", "", ""))
            else:
                self._peers_store.append(("ℹ️", _("No devices found"), _("Try refreshing..."), "", "", "", ""))
        else:
            for r in rows:
                self._peers_store.append(r)

    def _refresh_history(self):
        # Clear all children from the hist_list (Gtk.Box — safe to iterate directly)
        child = self._hist_list.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self._hist_list.remove(child)
            child = next_child

        history = _load_history()
        if not history:
            sp = Adw.StatusPage(title=_("No previous networks"), icon_name="document-open-recent-symbolic", description=_("Your created/connected networks will appear here."))
            self._hist_list.append(sp)
            return

        for entry in reversed(history):
            vpn_id = entry.get("vpn", "headscale")
            vpn_meta = VPN_META.get(vpn_id, {})
            vpn_name = vpn_meta.get("name", vpn_id)
            vpn_icon = vpn_meta.get("icon", "network-wired-symbolic")
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
            btn_conn.set_child(create_icon_widget("network-transmit-receive-symbolic", size=14))
            btn_conn.add_css_class("flat")
            btn_conn.set_tooltip_text(_("Reconnect"))
            btn_conn.connect("clicked", lambda b, e=entry: self._reconnect_from_history(e))
            header_box.append(btn_conn)

            # Edit button
            btn_edit = Gtk.Button()
            btn_edit.set_child(create_icon_widget("edit-symbolic", size=14))
            btn_edit.add_css_class("flat")
            btn_edit.set_tooltip_text(_("Edit"))
            btn_edit.connect("clicked", lambda b, e=entry: self._edit_history_entry(e))
            header_box.append(btn_edit)

            # Delete button
            btn_del = Gtk.Button()
            btn_del.set_child(create_icon_widget("trash-symbolic", size=14))
            btn_del.add_css_class("flat")
            btn_del.add_css_class("destructive-action")
            btn_del.set_tooltip_text(_("Delete"))
            btn_del.connect("clicked", lambda b, e=entry: self._delete_history_entry(e))
            header_box.append(btn_del)

            grp.set_header_suffix(header_box)

            for label, key, icon in [
                (_("Domain"), "domain", "network-wired-symbolic"),
                (_("Network ID"), "network_id", "network-wired-symbolic"),
                (_("Auth Key"), "auth_key", "view-reveal-symbolic"),
                (_("Web UI"), "web_ui", "network-wired-symbolic"),
                (_("VPN"), "vpn", "network-wired-symbolic"),
            ]:
                is_secret = key in _HISTORY_SECRET_KEYS
                val = _entry_secret(entry, key) if is_secret else entry.get(key, "")
                if not val:
                    continue
                subtitle = _("Stored in system keyring") if is_secret and _history_secret_key(entry, key) else val
                row = Adw.ActionRow(title=label, subtitle=subtitle)
                row.add_prefix(create_icon_widget(icon, size=14))
                btn_c = Gtk.Button()
                btn_c.set_child(create_icon_widget("edit-copy-symbolic", size=12))
                btn_c.add_css_class("flat")
                btn_c.set_valign(Gtk.Align.CENTER)
                btn_c.connect("clicked", lambda b, v=val: self._copy(v))
                row.add_suffix(btn_c)
                grp.add(row)

            self._hist_list.append(grp)

    def _reconnect_from_history(self, entry):
        vpn_id = entry.get("vpn", self.vpn_id)
        vpn_name = VPN_META.get(vpn_id, {}).get("name", vpn_id)

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
                    if page and hasattr(page, "_stack"):
                        page._stack.set_visible_child_name("connect")
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
            # Fallback: just switch to connect tab locally
            self._stack.set_visible_child_name("connect")
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
        grp.set_header_suffix(create_icon_widget(VPN_META.get(vpn_id, {}).get("icon", "network-wired-symbolic"), size=20))

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
        btn_save.add_css_class("pill")
        btn_save.set_size_request(140, 40)

        btn_cancel = Gtk.Button(label=_("Cancel"))
        btn_cancel.add_css_class("pill")
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
        d = Adw.MessageDialog(transient_for=self.main_window, heading=_("Delete entry?"), body=_("Remove this network from history?"))
        d.add_response("cancel", _("Cancel"))
        d.add_response("delete", _("Delete"))
        d.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_resp(dlg, resp):
            if resp == "delete":
                _delete_history(entry.get("id"))
                self._refresh_history()

        d.connect("response", on_resp)
        d.present()

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
        dialog.set_modal(True)
        dialog.set_title(_("Setup Instructions"))
        dialog.set_default_size(700, 650)

        # ToolbarView
        toolbar_view = Adw.ToolbarView()

        # Header bar
        hb = Adw.HeaderBar()
        hb.set_title_widget(Adw.WindowTitle.new(_("Setup Instructions"), _("Step-by-step guide to join a private network")))
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
        r1_1.add_prefix(create_icon_widget("view-reveal-symbolic", size=20))
        g1.add(r1_1)

        r1_2 = Adw.ActionRow()
        r1_2.set_title(_("Step 2: Enter details"))
        r1_2.set_subtitle(_("Paste the Domain and Key in the fields on the previous tab."))
        r1_2.add_prefix(create_icon_widget("edit-copy-symbolic", size=20))
        g1.add(r1_2)

        r1_3 = Adw.ActionRow()
        r1_3.set_title(_("Step 3: Connect"))
        r1_3.set_subtitle(_("Click 'Establish Connection' and wait for the success message."))
        r1_3.add_prefix(create_icon_widget("view-refresh-symbolic", size=20))
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
                    _("All participants need an account (Google, GitHub, etc)."),
                    "network-wired-symbolic",
                    _("Create Account"),
                    "https://login.tailscale.com",
                ),
                (_("2. Invitation"), _("Request Access"), _("The administrator must share the node or invite your email to the network."), "text-x-generic-symbolic", None, None),
                (_("3. Connect"), _("Establish Connection"), _("Once the invitation is accepted, use the previous tab to connect."), "view-refresh-symbolic", None, None),
            ],
        )

    def _show_zerotier_instructions(self):
        self._show_simple_instructions(
            _("ZeroTier Instructions"),
            [
                (_("1. Identification"), _("Get Network ID"), _("Request the 16-character ID from the network owner."), "preferences-other-symbolic", None, None),
                (_("2. Join"), _("Enter ID"), _("Enter the ID on the 'Connect' tab and click 'Establish Connection'."), "edit-copy-symbolic", None, None),
                (
                    _("3. Authorization"),
                    _("Wait for Approval"),
                    _("The administrator must check the 'Auth' option for your PC in their panel."),
                    "network-idle-symbolic",
                    _("ZT Panel"),
                    "https://my.zerotier.com",
                ),
            ],
        )

    def _show_simple_instructions(self, title_text, items):
        """Show instructions using the premium Adwaita style."""
        dialog = Adw.Window(transient_for=self.main_window)
        dialog.set_modal(True)
        dialog.set_title(title_text)
        dialog.set_default_size(680, 600)

        # ToolbarView
        toolbar_view = Adw.ToolbarView()

        # Header bar
        hb = Adw.HeaderBar()
        hb.set_title_widget(Adw.WindowTitle.new(title_text, _("Step-by-step guide")))
        toolbar_view.add_top_bar(hb)

        # Scrollable content
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(600)
        for m in ["top", "bottom", "start", "end"]:
            getattr(clamp, f"set_margin_{m}")(24)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)

        for item in items:
            # item tuple: (group_title, row_title, row_subtitle, icon, btn_label, btn_url)
            g_title = item[0]
            r_title = item[1]
            r_subtitle = item[2] if len(item) > 2 else ""
            icon = item[3] if len(item) > 3 else "dialog-information-symbolic"
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
                btn.add_css_class("pill")
                btn.add_css_class("suggested-action")
                btn.set_valign(Gtk.Align.CENTER)
                btn.connect("clicked", lambda b, u=btn_url: open_uri(u))
                row.add_suffix(btn)

            group.add(row)
            main_box.append(group)

        clamp.set_child(main_box)
        scroll.set_child(clamp)
        toolbar_view.set_content(scroll)

        dialog.set_content(toolbar_view)
        dialog.present()

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

    def __init__(self, main_window, mode="create", vpn_provider="headscale"):
        super().__init__()
        self.main_window = main_window
        self.mode = mode
        self.vpn_provider = vpn_provider if vpn_provider in VPN_META else "headscale"

        if mode == "create":
            page = CreatePage(self.vpn_provider, main_window)
        else:
            page = ConnectPage(self.vpn_provider, main_window)

        self.set_child(page)
