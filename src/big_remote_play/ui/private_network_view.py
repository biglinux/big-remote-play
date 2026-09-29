import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
import json, os, re, subprocess, threading, time
from gi.repository import Adw, Gdk, GLib, Gtk  # type: ignore
import logging

_log = logging.getLogger("big-remoteplay")
from big_remote_play.utils.i18n import _
from big_remote_play.utils.icons import create_icon_widget
from big_remote_play import paths
from big_remote_play.private_network.models import ConnectionState, ProviderId
from big_remote_play.private_network.redaction import redact
from big_remote_play.utils.secure_io import secure_write_text
from big_remote_play.utils.secret_store import SecretKey, SecretStore, SecretStoreUnavailable
from big_remote_play.utils.script_protocol import parse_script_line
from big_remote_play.utils.uri import open_uri
from big_remote_play.utils.vpn_accounts import VPNAccountManager, valid_zerotier_network_id
from .components import content_dialog, action_row, boxed_rows, intro, note, name_icon_button
from .network_common import copy_row


def show_simple_instructions(parent, title_text, items):
    """Premium-style step-by-step instructions dialog.

    `items` are tuples of (group_title, row_title, row_subtitle, icon,
    btn_label, btn_url); the last three are optional.
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

        row = Adw.ActionRow(use_markup=False)
        row.set_title(r_title)
        row.set_subtitle(r_subtitle)
        row.set_title_lines(0)
        row.set_subtitle_lines(0)
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


# ─── Config ───────────────────────────────────────────────────────────────────
# "Previous networks" remembers how this PC joined a network (server address or
# Network ID) so the form can be filled again. It never stores a credential:
# an auth key is used once, and administrative tokens live in API access.
# ``HISTORY_FILE`` stays None so the path follows the current configuration
# directory; tests may point it at a file.
HISTORY_FILE: str | None = None
_SECRET_STORE = SecretStore()
_HISTORY_SECRET_KEYS = {"auth_key", "api_key", "cf_token"}
# Only what identifies a network is written; older fields such as public or
# local IP addresses are not carried forward.
_HISTORY_FIELDS = {"id", "timestamp", "vpn", "domain", "network_id", "name", "web_ui"}

VPN_META = {
    "headscale": {
        "name": "Headscale",
        "icon": "brp-headscale-symbolic",
        "connect_title": _("Connect to Headscale Network"),
        "connect_desc": _("Enter the server domain and auth key provided by the administrator."),
    },
    "tailscale": {
        "name": "Tailscale",
        "icon": "brp-tailscale-symbolic",
        "connect_title": _("Connect to Tailscale Network"),
        "connect_desc": _("Join Tailscale with browser login, or use an auth key if someone gave you one."),
    },
    "zerotier": {
        "name": "ZeroTier",
        "icon": "brp-zerotier-symbolic",
        "connect_title": _("Connect to ZeroTier Network"),
        "connect_desc": _("Enter the 16-character Network ID to join a ZeroTier network."),
    },
}


def _history_path() -> str:
    return HISTORY_FILE or str(paths.CONFIG_DIR / "private_network" / "history.json")


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
            command = getattr(system_check, "zerotier_cmd", None)
            raw = command() if callable(command) else None
            argv = [str(part) for part in raw] if isinstance(raw, (list, tuple)) else ["zerotier-cli"]
            result = subprocess.run([*argv, "-j", "listnetworks"], capture_output=True, text=True, timeout=10)
            networks = json.loads(result.stdout) if result.returncode == 0 else []
            return isinstance(networks, list) and any(isinstance(item, dict) and item.get("status") == "OK" for item in networks)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        # Never turn a CLI failure or pending authorization into a ready state.
        pass
    return False


def _load_history():
    try:
        path = _history_path()
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f).get("history", [])
    except Exception:
        pass
    return []


def _write_history(history) -> None:
    secure_write_text(_history_path(), json.dumps({"history": [_without_secrets(entry) for entry in history]}, indent=2))


def _without_secrets(entry: dict) -> dict:
    """Keep what identifies a network; drop every credential."""
    return {key: value for key, value in entry.items() if key in _HISTORY_FIELDS}


def _clear_history_secrets(entry: dict) -> None:
    """Remove keyring items that older versions linked from a history entry."""
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
    clean = _without_secrets(entry)
    identity = _history_identity(clean)

    # Same provider + domain/network: refresh the existing entry instead of
    # appending a duplicate (e.g. repeated Tailscale "Default Login" sign-ins).
    for index, existing in enumerate(history):
        if _history_identity(existing) == identity:
            merged = _without_secrets(dict(existing))
            merged.update(clean)
            merged["id"] = existing.get("id")
            merged["timestamp"] = timestamp
            history[index] = merged
            _clear_history_secrets(existing)
            _write_history(history)
            return merged["id"]

    new_id = max((h.get("id", 0) for h in history), default=0) + 1
    clean["id"] = new_id
    clean["timestamp"] = timestamp
    history.append(clean)
    _write_history(history)
    return new_id


def _delete_history(entry_id):
    kept = []
    for entry in _load_history():
        if entry.get("id") == entry_id:
            _clear_history_secrets(entry)
        else:
            kept.append(entry)
    _write_history(kept)


def _update_history(entry_id, updated_entry):
    """Update a specific history entry by its ID."""
    history = _load_history()
    for i, h in enumerate(history):
        if h.get("id") == entry_id:
            merged = _without_secrets(dict(updated_entry))
            merged["id"] = entry_id
            merged["timestamp"] = h.get("timestamp", time.strftime("%Y-%m-%d %H:%M:%S"))
            _clear_history_secrets(h)
            history[i] = merged
            break
    _write_history(history)


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


def run_helper_script(script_name, inputs, *, on_text, on_phase, on_done):
    """Run a bundled privileged helper and stream its BRP_* protocol.

    Callbacks are delivered on the GTK main loop. ``on_done(code, captured)``
    always runs exactly once, also when the helper cannot be started.
    """
    script = _get_script(script_name)

    def run():
        try:
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
            proc_stdout = proc.stdout
            if proc_stdout is None:
                raise OSError("Network helper has no output pipe")
            for line in proc_stdout:
                kind = parse_script_line(line)
                if kind[0] == "data":
                    if kind[2]:
                        captured[kind[1]] = kind[2]
                elif kind[0] == "phase":
                    GLib.idle_add(on_phase, kind[1])
                elif kind[1]:
                    GLib.idle_add(on_text, redact(kind[1]))
            code = proc.wait()
            GLib.idle_add(on_done, code, captured)
        except (OSError, ValueError) as error:
            _log.error("Could not run network helper: %s", error)
            GLib.idle_add(on_done, 127, {})

    threading.Thread(target=run, daemon=True).start()


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
        GLib.idle_add(self._append_idle, redact(text))

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
    def __init__(self, on_show_log=None, *, log_label: str | None = None):
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
            accessible_label = log_label or _("Installation Log")
            btn.set_tooltip_text(accessible_label)
            btn.update_property([Gtk.AccessibleProperty.LABEL], [accessible_label])
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


def _show_log_window(parent, log_view: LogView, title: str) -> None:
    previous = log_view.get_parent()
    if isinstance(previous, Adw.ToolbarView):
        previous.set_content(None)  # the log moves to the new window
    dialog = Adw.Window(transient_for=parent)
    dialog.set_modal(False)
    dialog.set_title(title)
    dialog.set_default_size(700, 500)
    tv = Adw.ToolbarView()
    tv.add_top_bar(Adw.HeaderBar())
    tv.set_content(log_view)
    dialog.set_content(tv)
    dialog.present()


# ─── INSTALL SECTION ──────────────────────────────────────────────────────────
class InstallSection(Gtk.Box):
    """Explicit installation of a missing VPN client (never automatic)."""

    _NAMES = {"tailscale": "Tailscale", "zerotier": "ZeroTier", "headscale": "Tailscale"}
    _HELP = {"tailscale": "https://tailscale.com/download", "zerotier": "https://www.zerotier.com/download/", "headscale": "https://tailscale.com/download"}

    def __init__(self, vpn_id, main_window, *, on_installed=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.vpn_id = vpn_id
        self.main_window = main_window
        self._on_installed = on_installed or (lambda: None)
        self._name = self._NAMES.get(vpn_id, vpn_id)
        self.append(note(_("{} is not installed yet. Install it below to continue (asks for your password).").format(self._name), "dialog-warning-symbolic"))
        self._progress = ProgressRow(on_show_log=lambda: _show_log_window(self.main_window, self._log, _("Installation Log")))
        self._log = LogView()
        self.append(self._progress)
        self._spinner = Adw.Spinner()
        self._spinner.set_visible(False)
        if self.main_window.system_check.has_pacman():
            self._btn_install = Gtk.Button(halign=Gtk.Align.CENTER)
            inner = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
            inner.append(self._spinner)
            inner.append(Gtk.Label(label=_("Install {}").format(self._name)))
            self._btn_install.set_child(inner)
            self._btn_install.update_property([Gtk.AccessibleProperty.LABEL], [_("Install {}").format(self._name)])
            self._btn_install.connect("clicked", self._on_install_clicked)
        else:
            self._btn_install = Gtk.Button(label=_("How to install {}").format(self._name), halign=Gtk.Align.CENTER)
            self._btn_install.connect("clicked", lambda b: open_uri(b, self._HELP.get(self.vpn_id, self._HELP["tailscale"])))
        self._btn_install.add_css_class("suggested-action")
        self._btn_install.set_size_request(220, 48)
        self.append(self._btn_install)

    def _on_install_clicked(self, _btn) -> None:
        self._btn_install.set_sensitive(False)
        self._spinner.set_visible(True)
        self._progress.update(0.05, _("Installing..."))
        self._log.clear()

        def done(code, captured):
            if captured.get("INSTALL_RESULT") == "ok" and code == 0:
                self.main_window.show_toast(_("{} installed").format(self._name))
                if hasattr(self.main_window, "check_system"):
                    self.main_window.check_system()
                self._on_installed()
            else:
                self._spinner.set_visible(False)
                self._btn_install.set_sensitive(True)
                self.main_window.show_toast(_("Installation failed. Check the log."))
            return False

        def on_text(text):
            self._log.append(text)
            self._progress.update(self._phase, text[:80])
            return False

        def on_phase(phase):
            self._phase = phase
            self._progress.update(phase, "")
            return False

        self._phase = 0.05
        # Headscale computers join with the regular Tailscale client.
        provider = "tailscale" if self.vpn_id == "headscale" else self.vpn_id
        run_helper_script("install-vpn.sh", [provider + "\n"], on_text=on_text, on_phase=on_phase, on_done=done)


# ─── CONNECT PAGE ─────────────────────────────────────────────────────────────
class ConnectPage(Adw.Bin):
    """Join a network with one provider: browser sign-in, key or Network ID."""

    ZEROTIER_JOIN_WAIT = 20.0

    def __init__(self, vpn_id, main_window, add_account=False):
        super().__init__()
        self.vpn_id = vpn_id
        self.vpn = VPN_META[vpn_id]
        self.main_window = main_window
        self.advanced = bool(getattr(main_window, "network_advanced_mode", False))
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
        self._open_dashboard.set_visible(connected)
        if connected:
            self._approval_group.set_visible(False)
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

        if self.advanced:
            heading_title = self.vpn["connect_title"]
            heading_description = self.vpn["connect_desc"]
        else:
            heading_title = _("Connect this computer")
            heading_description = {
                "zerotier": _("Type the network code you received."),
                "tailscale": _("Sign in with your browser. Use the same account on all your devices."),
                "headscale": _("Type the address of your server. Its administrator approves this computer."),
            }.get(self.vpn_id, self.vpn["connect_desc"])
        heading = intro(heading_title, heading_description, self.vpn["icon"])
        self._c_title = heading.get_last_child().get_first_child()
        self._c_description = heading.get_last_child().get_last_child()
        conn_box.append(heading)

        # Everything needed to join, hidden as one block once this PC is on the
        # network: a form asking to sign in again contradicts the page above it.
        self._connect_form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        conn_box.append(self._connect_form)
        fields_group = Adw.PreferencesGroup()
        fields_group.set_title(_("Connection Details") if self.advanced else _("What you need"))
        self._build_connect_fields(fields_group)
        if self.vpn_id != "tailscale":
            self._connect_form.append(fields_group)
        self._c_progress = ProgressRow(on_show_log=self._show_connect_log, log_label=_("Connection Log"))
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

        # ZeroTier: joined, but the network owner has not approved this PC yet.
        self._approval_group = Adw.PreferencesGroup(title=_("Waiting for approval"))
        self._approval_group.set_description(_("This computer joined the network. The network owner must approve it before it can reach the other computers."))
        self._approval_rows = []
        self._approval_group.set_visible(False)
        self._connect_form.append(self._approval_group)

        if self.advanced:
            self._connect_form.append(
                boxed_rows(action_row(_("About this VPN"), _("How to use the selected private network service."), "brp-dialog-information-symbolic", lambda: self._on_instructions_clicked(None)))
            )
        self._return_to_game = boxed_rows(action_row(_("Ready to play?"), self.main_window.network_return_label(), "brp-client-symbolic", self.main_window.return_from_network))
        self._return_to_game.set_visible(False)
        conn_box.append(self._return_to_game)
        self._open_dashboard = boxed_rows(
            action_row(_("My network"), _("Devices, private address and how to add another computer."), "brp-network-setup-symbolic", lambda: self.main_window.navigate_to("create_private"))
        )
        self._open_dashboard.set_visible(False)
        conn_box.append(self._open_dashboard)

        extras = Adw.PreferencesGroup(title=_("More options"))
        self._hist_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._history_dialog = content_dialog(
            _("Previous Networks"),
            self._hist_list,
            description=_("Networks this PC joined before."),
        )
        extras.add(action_row(_("Previous Networks"), _("Reuse connection details saved by Big Remote Play"), "brp-document-open-recent-symbolic", self._present_history))
        if self.advanced:
            extras.add(
                action_row(
                    _("Manage accounts and networks"),
                    _("Switch saved accounts or leave networks without deleting online accounts."),
                    "brp-accounts-symbolic",
                    getattr(self.main_window, "show_vpn_accounts", lambda: None),
                )
            )
            extras.add(action_row(_("API access"), _("Credentials that let Big Remote Play manage this network."), "brp-dialog-password-symbolic", self._present_api_access))
        conn_box.append(extras)

        conn_clamp = Adw.Clamp(maximum_size=800, tightening_threshold=560)
        conn_clamp.set_child(conn_box)
        conn_scroll = Gtk.ScrolledWindow(vexpand=True)
        conn_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        conn_scroll.set_child(conn_clamp)

        toolbar.set_content(conn_scroll)
        self.set_child(toolbar)

    def _show_hosting_guide(self) -> None:
        from .connection_guides import build_headscale_hosting_dialog

        build_headscale_hosting_dialog().present(self)

    def _present_history(self) -> None:
        self._refresh_history()
        self._history_dialog.present(self)

    def _present_api_access(self) -> None:
        from big_remote_play.private_network.service import default_service
        from .api_access_dialog import ApiAccessDialog

        ApiAccessDialog(self, default_service(), show_toast=self.main_window.show_toast, focus=self.vpn_id).present()

    def _on_instructions_clicked(self, btn):
        if self.vpn_id == "headscale":
            self._show_headscale_instructions()
        elif self.vpn_id == "tailscale":
            self._show_tailscale_instructions()
        elif self.vpn_id == "zerotier":
            self._show_zerotier_instructions()

    def _build_connect_fields(self, group):
        if self.vpn_id == "headscale":
            self._e_domain = Adw.EntryRow(title=_("Server Domain (e.g. vpn.example.com)") if self.advanced else _("Server address"))
            self._e_key = Adw.PasswordEntryRow(title=_("Auth Key"))
            group.add(self._e_domain)
            self._headscale_key_expander = Adw.ExpanderRow(title=_("Advanced options"), subtitle=_("Auth Key"), use_markup=False)
            self._headscale_key_expander.add_row(self._e_key)
            self._headscale_key_expander.set_visible(self.advanced)
            group.add(self._headscale_key_expander)
            group.add(
                Adw.ActionRow(
                    title=_("Without an auth key, a sign-in link opens"),
                    subtitle=_("The server administrator then approves this computer."),
                    use_markup=False,
                )
            )
            group.add(action_row(_("I don't have a server yet"), _("How to run Headscale on your own server, step by step."), "brp-network-server-symbolic", self._show_hosting_guide))

        elif self.vpn_id == "tailscale":
            self._e_server = Adw.EntryRow(title=_("Login Server (leave empty for tailscale.com)"))
            self._e_key = Adw.PasswordEntryRow(title=_("Auth Key"))
            self._auth_key_expander = Adw.ExpanderRow(title=_("Advanced options"), subtitle=_("Auth Key"))
            self._auth_key_expander.add_row(self._e_key)
            self._auth_key_expander.set_visible(self.advanced)
            group.set_title("")
            group.add(self._auth_key_expander)

        elif self.vpn_id == "zerotier":
            self._e_netid = Adw.EntryRow(title=_("Network ID (16 characters)") if self.advanced else _("Network code"))
            self._e_netid.set_tooltip_text(_("e.g. a1b2c3d4e5f6a7b8"))
            group.add(self._e_netid)
            if self.advanced:
                link = Adw.ActionRow(
                    title=_("Find Network ID"),
                    subtitle=_("Ask the network owner, or look in ZeroTier Central → Networks"),
                )
                link.add_prefix(create_icon_widget("brp-address-symbolic", size=18))
                btn = Gtk.Button(label=_("Open"))
                btn.set_valign(Gtk.Align.CENTER)
                btn.connect("clicked", lambda b: open_uri(b, "https://my.zerotier.com/network"))
                link.add_suffix(btn)
                group.add(link)
            else:
                hint = Adw.ActionRow(title=_("The person who created the network gives you this code."), use_markup=False)
                hint.set_title_lines(0)
                hint.add_prefix(create_icon_widget("brp-dialog-information-symbolic", size=18))
                group.add(hint)

        self._prefill_from_history()
        if self.vpn_id == "tailscale":
            self._auth_key_expander.set_expanded(bool(self._e_key.get_text()))

    def _prefill_from_history(self):
        """Pre-fill non-secret fields with the last network used with this VPN."""
        history = _load_history()
        last_entry = next((h for h in reversed(history) if h.get("vpn") == self.vpn_id), None)
        if not last_entry:
            return
        self._fill_from_entry(self, last_entry)

    @staticmethod
    def _fill_from_entry(page, entry) -> None:
        vpn_id = entry.get("vpn")
        if vpn_id == "headscale" and hasattr(page, "_e_domain"):
            page._e_domain.set_text(entry.get("domain", ""))
        elif vpn_id == "tailscale" and hasattr(page, "_e_server"):
            page._e_server.set_text(entry.get("domain", "") if entry.get("domain") != "tailscale.com" else "")
        elif vpn_id == "zerotier" and hasattr(page, "_e_netid"):
            page._e_netid.set_text(entry.get("network_id", ""))

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
            if not domain:
                self._e_domain.add_css_class("error")
                self._e_domain.grab_focus()
                self.main_window.show_toast(_("Enter the Headscale server address."))
                self._c_done(False)
                return
            self._e_domain.remove_css_class("error")
            self._connect_tailnet(login_server=domain, auth_key=self._e_key.get_text().strip())
            return

        if self.vpn_id == "tailscale":
            self._connect_tailnet(login_server=self._e_server.get_text(), auth_key=self._e_key.get_text())
            return

        if self.vpn_id == "zerotier":
            # People copy the code in groups ("a1b2 c3d4 …" or with dashes).
            nid = "".join(self._e_netid.get_text().split()).replace("-", "") if hasattr(self, "_e_netid") else ""
            if not valid_zerotier_network_id(nid):
                self._e_netid.add_css_class("error")
                self._e_netid.grab_focus()
                self.main_window.show_toast(_("Enter a 16-character hexadecimal Network ID.") if self.advanced else _("This code is not complete. Check it and try again."))
                self._c_done(False)
                return
            self._e_netid.remove_css_class("error")
            self._join_zerotier(nid.lower())
            return

        self._c_done(False)
        self.main_window.show_toast(_("Select a network service and try again."))

    def _join_zerotier(self, network_id: str) -> None:
        """Join with zerotier-cli, then wait for the service's own verdict."""
        manager = VPNAccountManager(self.main_window.system_check)
        wait = self.ZEROTIER_JOIN_WAIT

        def run():
            result = manager.join_zerotier_network(network_id, allow_privileged=True)
            if result.returncode != 0:
                GLib.idle_add(self._report_connect_output, redact(result.stderr or result.stdout or _("Could not join the network.")))
                GLib.idle_add(self._c_done, False)
                return
            GLib.idle_add(self._report_connect_output, _("Joined. Waiting for the network to confirm this computer…"))
            deadline = time.monotonic() + wait
            status = ""
            while time.monotonic() < deadline:
                listing = manager.list_zerotier_networks(allow_privileged=False)
                network = next((item for item in listing.networks if item.network_id == network_id), None)
                status = network.status.upper() if network else status
                if network is not None and network.ready and network.assigned_addresses:
                    GLib.idle_add(self._c_done, True)
                    return
                if status == "ACCESS_DENIED":
                    break
                time.sleep(2)
            node = manager.zerotier_info()
            if status in ("ACCESS_DENIED", "REQUESTING_CONFIGURATION", "OK"):
                GLib.idle_add(self._awaiting_approval, network_id, node.address)
            else:
                GLib.idle_add(self._report_connect_output, f"ZeroTier status: {status or 'unknown'}")
                GLib.idle_add(self._c_done, False)

        threading.Thread(target=run, daemon=True).start()

    def _awaiting_approval(self, network_id: str, node_id: str) -> bool:
        """Joined, not yet approved: not a failure, and not a connection either."""
        self._btn_connect.set_sensitive(True)
        self._c_spinner.stop()
        self._c_spinner.set_visible(False)
        self._c_lbl.set_label(_("Check again"))
        self._c_progress.update(0.8, _("Waiting for the network owner to approve this computer."))
        for row in self._approval_rows:
            self._approval_group.remove(row)
        self._approval_rows = []
        if node_id:
            self._approval_rows.append(
                copy_row(
                    _("Node ID of this computer") if self.advanced else _("Code for this computer"),
                    node_id,
                    icon="brp-dialog-password-symbolic",
                    toast=self.main_window.show_toast,
                )
            )
        self._approval_rows.append(copy_row(_("Network ID") if self.advanced else _("Network code"), network_id, icon="brp-zerotier-symbolic", toast=self.main_window.show_toast))
        for row in self._approval_rows:
            self._approval_group.add(row)
        self._approval_group.set_visible(True)
        _save_history({"vpn": "zerotier", "network_id": network_id})
        self.main_window.show_toast(_("Waiting for approval"))
        return False

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
        self._c_progress.update(self._c_phase, redact(line)[:80])
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

            # Remember how this network was joined; never the key used to do it.
            entry = {"vpn": self.vpn_id}
            if self.vpn_id == "headscale":
                entry["domain"] = self._e_domain.get_text().strip()
            elif self.vpn_id == "tailscale":
                entry["domain"] = self._e_server.get_text().strip() or "tailscale.com"
            elif self.vpn_id == "zerotier":
                entry["network_id"] = self._e_netid.get_text().strip().lower()
            _save_history(entry)
            if hasattr(self, "_e_key"):
                self._e_key.set_text("")

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
            grp.set_title(GLib.markup_escape_text(f"{vpn_name} – {domain}"))
            grp.set_description(GLib.markup_escape_text(str(ts)))

            header_box = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
            header_box.append(create_icon_widget(vpn_icon, size=20))
            sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
            sep.set_margin_top(4)
            sep.set_margin_bottom(4)
            header_box.append(sep)

            btn_conn = Gtk.Button()
            btn_conn.set_child(create_icon_widget("brp-network-transmit-receive-symbolic", size=14))
            btn_conn.add_css_class("flat")
            name_icon_button(btn_conn, _("Reconnect"), _("Reconnect using this saved network"))
            btn_conn.connect("clicked", lambda b, e=entry: self._reconnect_from_history(e))
            header_box.append(btn_conn)

            btn_edit = Gtk.Button()
            btn_edit.set_child(create_icon_widget("brp-edit-symbolic", size=14))
            btn_edit.add_css_class("flat")
            name_icon_button(btn_edit, _("Edit"), _("Edit this saved network"))
            btn_edit.connect("clicked", lambda b, e=entry: self._edit_history_entry(e))
            header_box.append(btn_edit)

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
                (_("Web UI"), "web_ui", "brp-help-browser-symbolic"),
            ]:
                val = str(entry.get(key, "") or "")
                if not val:
                    continue
                grp.add(copy_row(label, val, icon=icon, toast=self.main_window.show_toast))

            self._hist_list.append(grp)

    def _reconnect_from_history(self, entry):
        vpn_id = entry.get("vpn", self.vpn_id)
        vpn_name = VPN_META.get(vpn_id, {}).get("name", vpn_id)
        # The filled form is on the page behind this sheet.
        self._history_dialog.close()

        if hasattr(self.main_window, "_apply_vpn_selection"):
            self.main_window._apply_vpn_selection(vpn_id)
            GLib.idle_add(lambda: self.main_window.navigate_to("connect_private"))

            def _fill_form():
                view = getattr(self.main_window, "connect_private_view", None)
                page = view.get_child() if view is not None and hasattr(view, "get_child") else None
                if page is not None:
                    self._fill_from_entry(page, entry)
                self.main_window.show_toast(_("Form filled for {}").format(vpn_name))
                return False

            GLib.timeout_add(300, _fill_form)
        else:
            self._fill_from_entry(self, entry)
            self.main_window.show_toast(_("Form filled for {}").format(vpn_name))

    def _edit_history_entry(self, entry):
        """Open a dialog to edit the non-secret fields of a history entry."""
        vpn_id = str(entry.get("vpn") or "headscale")
        vpn_name = str(VPN_META.get(vpn_id, {}).get("name") or vpn_id)

        grp = Adw.PreferencesGroup()
        grp.set_title(_("Connection Details"))
        grp.set_header_suffix(create_icon_widget(VPN_META.get(vpn_id, {}).get("icon", "brp-network-private-symbolic"), size=20))

        edit_fields = {}
        if vpn_id in ("headscale", "tailscale"):
            e_domain = Adw.EntryRow(title=_("Domain") if vpn_id == "headscale" else _("Login Server"))
            e_domain.set_text(entry.get("domain", "") if entry.get("domain") != "tailscale.com" else "")
            grp.add(e_domain)
            edit_fields["domain"] = e_domain
        elif vpn_id == "zerotier":
            e_netid = Adw.EntryRow(title=_("Network ID"))
            e_netid.set_text(entry.get("network_id", ""))
            grp.add(e_netid)
            edit_fields["network_id"] = e_netid
        if entry.get("web_ui"):
            e_webui = Adw.EntryRow(title=_("Web UI"))
            e_webui.set_text(entry.get("web_ui", ""))
            grp.add(e_webui)
            edit_fields["web_ui"] = e_webui

        dialog = Adw.AlertDialog(heading=_("Edit – {}").format(vpn_name))
        dialog.set_extra_child(grp)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("save", _("Save"))
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")

        def on_response(_dialog, response):
            if response != "save":
                return
            updated = {"vpn": vpn_id}
            for key, widget in edit_fields.items():
                val = widget.get_text().strip()
                if key == "network_id" and val and not valid_zerotier_network_id(val):
                    self.main_window.show_toast(_("Enter a 16-character hexadecimal Network ID."))
                    return
                if val:
                    updated[key] = val
                elif key == "domain" and vpn_id == "tailscale":
                    updated[key] = "tailscale.com"
            _update_history(entry.get("id"), updated)
            self._refresh_history()
            self.main_window.show_toast(_("Entry updated"))

        dialog.connect("response", on_response)
        dialog.present(self.main_window)

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
        _show_log_window(self.main_window, self._c_log, _("Connection Log"))

    def _show_headscale_instructions(self):
        self._show_simple_instructions(
            _("Join a Headscale VPN"),
            [
                (
                    _("Step 1: Get credentials"),
                    _("Server address"),
                    _("Ask the network administrator for the server address and, optionally, a one-time auth key."),
                    "brp-network-server-symbolic",
                    None,
                    None,
                ),
                (_("Step 2: Enter details"), _("Fill in the form"), _("Paste the server address and the key in the fields on this page."), "brp-edit-copy-symbolic", None, None),
                (
                    _("Step 3: Connect"),
                    _("Approve if needed"),
                    _("Without a key, a sign-in link opens; the administrator approves this computer on the server."),
                    "brp-view-refresh-symbolic",
                    None,
                    None,
                ),
            ],
        )

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
                (_("2. Join"), _("Enter ID"), _("Enter the ID on this page and choose Connect."), "brp-edit-copy-symbolic", None, None),
                (
                    _("3. Authorization"),
                    _("Wait for Approval"),
                    _("The network owner must approve this computer, in Big Remote Play or in ZeroTier Central."),
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
        self.main_window.show_toast(_("Copied"))


# ─── MAIN VIEW ────────────────────────────────────────────────────────────────
class PrivateNetworkView(Adw.Bin):
    """Entry point: the network dashboard ("create") or the join page."""

    def __init__(self, main_window, mode="create", vpn_provider="headscale", add_account=False):
        super().__init__()
        self.main_window = main_window
        self.mode = mode
        self.vpn_provider = vpn_provider if vpn_provider in VPN_META else "headscale"

        if mode == "create":
            from .network_dashboard import NetworkDashboardPage

            page = NetworkDashboardPage(self.vpn_provider, main_window)
        else:
            page = ConnectPage(self.vpn_provider, main_window, add_account=add_account)

        self.set_child(page)


__all__ = ["ConnectPage", "PrivateNetworkView", "InstallSection", "provider_connected", "ProviderId", "ConnectionState"]
