import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Gdk, Adw, GLib  # type: ignore
import logging

_log = logging.getLogger("big-remoteplay")

from pathlib import Path
import os

from big_remote_play import paths
from big_remote_play.utils.i18n import _
from big_remote_play.utils.icons import create_icon_widget
from .components import sidebar_dialog
from big_remote_play.utils.secure_io import secure_write_text
from big_remote_play.utils.sunshine_credentials import LEGACY_SECRET_KEYS, load_sunshine_credentials
from html import unescape
from big_remote_play.utils.system_check import SystemCheck
from big_remote_play.host.sunshine_manager import SunshineHost
from big_remote_play.utils.uri import open_path


class SunshineConfigManager:
    def __init__(self):
        self.config_dir = paths.SUNSHINE_CONFIG_DIR
        self.config_file = self.config_dir / "sunshine.conf"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config = {}
        self.load()

    def load(self):
        self.config = {}
        if self.config_file.exists():
            try:
                with open(self.config_file, "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if "=" in line:
                            parts = line.split("=", 1)
                            if len(parts) == 2:
                                self.config[parts[0].strip()] = parts[1].strip()
            except Exception as e:
                _log.error(f"Error loading Sunshine config: {e}")

    def save(self) -> None:
        try:
            settings = {key: value for key, value in self.config.items() if key not in LEGACY_SECRET_KEYS}
            body = "".join(f"{key} = {value}\n" for key, value in settings.items())
            secure_write_text(str(self.config_file), body)
        except Exception as e:
            _log.error(f"Error saving Sunshine config: {e}")

    def get(self, key, default=None):
        return self.config.get(key, str(default))

    def set(self, key, value):
        self.load()  # Another settings page may have changed unrelated keys.
        self.config[key] = str(value)
        self.save()

    def update(self, values: dict) -> None:
        """Set many keys and persist once (one file write, not one per key)."""
        self.load()
        for key, value in values.items():
            self.config[key] = str(value)
        self.save()


class SunshineSettings:
    """Builds the advanced server settings sheet and owns its config access.

    There are enough settings that a single scrolling page put every category
    behind an expander. The sheet uses a category sidebar instead, so each page
    stays short and the current category is always visible.
    """

    def __init__(self, main_config=None):
        self.main_config = main_config
        self.config = SunshineConfigManager()
        # Shares the same config dir as SunshineHost; used for live API apply/reload.
        self.sunshine = SunshineHost()

    def build_dialog(self) -> Adw.Dialog:
        pages = [(title, icon, self._build_page(description, fill)) for title, icon, description, fill in self._categories()]
        return sidebar_dialog(_("Advanced server settings"), pages)

    def _categories(self):
        return (
            # Explain which page owns each setting; the host must not claim to
            # set the resolution or frame rate requested by a client.
            (
                _("General"),
                "brp-preferences-symbolic",
                _("Expert settings. The sharing page controls capture, sound and the bitrate limit; the connecting PC controls resolution and frame rate."),
                lambda group: self._add_options(group, self.get_general_options()),
            ),
            (_("Input"), "brp-input-keyboard-symbolic", _("Keyboard, mouse and gamepad"), lambda group: self._add_options(group, self.get_input_options())),
            (_("Audio/Video"), "brp-video-display-symbolic", _("Capture, compression and stream limits"), lambda group: self._add_options(group, self.get_av_options())),
            (_("Network"), "brp-network-workgroup-symbolic", _("Ports and connectivity"), lambda group: self._add_options(group, self.get_network_options())),
            (_("Advanced"), "brp-preferences-other-symbolic", _("Advanced options"), lambda group: self._add_options(group, self.get_advanced_options())),
            (_("Config Files"), "brp-document-properties-symbolic", _("Configuration files and logs"), self.setup_config_files_tab),
            (_("Apply or reload"), "brp-service-symbolic", _("Push these settings to Sunshine and restart it (no manual stop)."), self.setup_maintenance_tab),
            (_("NVIDIA NVENC"), "brp-quality-symbolic", _("NVIDIA Encoder"), lambda group: self._add_options(group, self.get_nvenc_options())),
            (_("Intel QuickSync"), "brp-quality-symbolic", _("Intel Encoder"), lambda group: self._add_options(group, self.get_qsv_options())),
            (_("AMD AMF"), "brp-quality-symbolic", _("AMD Encoder"), lambda group: self._add_options(group, self.get_amf_options())),
            (_("VideoToolbox"), "brp-quality-symbolic", _("Apple Encoder"), lambda group: self._add_options(group, self.get_vt_options())),
            (_("VA-API"), "brp-quality-symbolic", _("VA-API Encoder (Linux)"), lambda group: self._add_options(group, self.get_vaapi_options())),
            (_("Software"), "brp-quality-symbolic", _("CPU Encoding"), lambda group: self._add_options(group, self.get_software_options())),
        )

    def _build_page(self, description, fill):
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(description=description)
        fill(group)
        page.add(group)
        return page

    def _add_options(self, group, options):
        for option in options:
            row = self.create_option_row(option)
            if row:
                group.add(row)

    def create_option_row(self, opt):
        # Unpack option tuple
        # Now supports optional description: (key, label, type, default, choices, description)
        if len(opt) == 6:
            key, label, type_, default, choices, description = opt
        else:
            key, label, type_, default, choices = opt
            description = None

        current_val = self.config.get(key, default)

        row = None
        if type_ == "switch":
            row = Adw.SwitchRow(use_markup=False)
            row.set_title(label)
            active = current_val.lower() in ("true", "enabled", "1", "on")
            row.set_active(active)

            def on_switch_change(w, p):
                val = str(w.get_active()).lower()
                self.config.set(key, val)

                # Sync 'stream_audio' with Main Config Host Audio
                if key == "stream_audio" and self.main_config:
                    h = self.main_config.get("host", {})
                    h["audio"] = w.get_active()
                    self.main_config.set("host", h)

            row.connect("notify::active", on_switch_change)

        elif type_ == "password":
            row = Adw.PasswordEntryRow(use_markup=False)
            row.set_title(label)
            row.set_text(str(current_val))
            row.connect("changed", lambda w: self.config.set(key, w.get_text()))

        elif type_ == "entry":
            row = Adw.EntryRow(use_markup=False)
            row.set_title(label)
            row.set_text(str(current_val))
            row.connect("changed", lambda w: self.config.set(key, w.get_text()))

        elif type_ == "spin":
            lower = -1 if key == "back_button_timeout" else 0
            row = Adw.SpinRow.new_with_range(lower, 100000, 1)
            row.set_use_markup(False)
            row.set_title(label)
            try:
                val = float(current_val)
            except (TypeError, ValueError):
                val = float(default) if default else 0
            row.set_value(val)
            row.connect("notify::value", lambda widget, _p: self.config.set(key, int(widget.get_value())))

        elif type_ == "combo":
            row = Adw.ComboRow(use_markup=False)
            row.set_title(label)

            # Check if choices are strings or tuples (key, label)
            if choices and isinstance(choices[0], tuple):
                display_values = [c[1] for c in choices]
                keys = [c[0] for c in choices]
            else:
                display_values = choices
                keys = choices

            model = Gtk.StringList()
            for c in display_values:
                model.append(c)
            row.set_model(model)

            # Find index
            try:
                # current_val should be the key
                idx = 0
                if current_val in keys:
                    idx = keys.index(current_val)
            except Exception:
                idx = 0
            row.set_selected(idx)

            row.connect("notify::selected", lambda w, p, k=keys, key_name=key: self.config.set(key_name, k[w.get_selected()]))

        if row and description:
            if isinstance(row, Adw.ActionRow):
                row.set_subtitle(unescape(description))
            else:
                row.set_tooltip_text(description)

        return row

    def setup_config_files_tab(self, group):
        # Determine paths
        # Sunshine defaults usually:
        # apps.json: alongside sunshine.conf or in config_dir
        # sunshine.log: in config_dir
        # credentials: in config_dir (often credentials.json)
        # pkey: sunshine.key (in config_dir)
        # cert: sunshine.cert (in config_dir)
        # state: sunshine_state.json (in config_dir)

        # We can check specific config keys if they exist, otherwise assume defaults

        def get_path(key, default_filename):
            base = self.config.config_dir
            val = self.config.get(key)
            if val and val != str(None):
                p = Path(val)
                if p.is_absolute():
                    return p
                else:
                    return base / p
            return base / default_filename

        files = [
            (_("Apps File"), get_path("apps_file", "apps.json"), _("The file that stores Sunshine's current app list.")),
            (_("Credentials File"), get_path("credentials_file", "credentials.json"), _("Store Username/Password separately from Sunshine's state file.")),
            (_("Logfile Path"), get_path("log_path", "sunshine.log"), _("The file that stores Sunshine's current logs.")),
            (
                _("Private Key"),
                get_path("pkey", "sunshine.key"),
                _("The private key used for the web UI and Moonlight client pairing. For best compatibility, this should be an RSA-2048 private key."),
            ),
            (
                _("Certificate"),
                get_path("cert", "sunshine.cert"),
                _("The certificate used for the web UI and Moonlight client pairing. For best compatibility, this should have an RSA-2048 public key."),
            ),
            (_("State File"), get_path("state_file", "sunshine_state.json"), _("The file that stores Sunshine's current state.")),
            (_("Configuration File"), self.config.config_file, _("The main configuration file for Sunshine.")),
        ]

        for name, path, desc in files:
            row = Adw.ActionRow(use_markup=False)
            row.set_title(name)
            row.set_subtitle(str(path))
            row.set_tooltip_text(desc)

            # Check if file exists
            if not path.exists():
                row.add_css_class("error")
                row.add_prefix(create_icon_widget("brp-preferences-other-symbolic", size=16))

            btn = Gtk.Button()
            btn.set_child(create_icon_widget("brp-folder-open-symbolic", size=16))
            btn.set_valign(Gtk.Align.CENTER)
            btn.add_css_class("flat")
            open_label = _("Open")
            btn.set_tooltip_text(open_label)
            btn.update_property([Gtk.AccessibleProperty.LABEL], [open_label])
            btn.connect("clicked", lambda _, p=path: self.open_file(p))

            btn.set_sensitive(path.exists())
            row.set_use_markup(False)
            row.set_subtitle_lines(2)

            row.add_suffix(btn)
            group.add(row)

    def open_file(self, path):
        # Gtk.show_uri (not a shell xdg-open) for correct Wayland activation.
        try:
            open_path(self, path)
        except Exception:
            pass

    def setup_maintenance_tab(self, group):
        """
        Setup maintenance actions
        """
        sys_check = SystemCheck()
        runtime_ok, runtime_detail = sys_check.check_sunshine_runtime()

        row = Adw.ActionRow(use_markup=False)
        row.set_title(_("Sunshine Runtime"))
        if runtime_ok:
            row.set_subtitle(runtime_detail or _("Sunshine is available."))
            row.add_prefix(create_icon_widget("brp-service-symbolic", size=24))
        else:
            row.set_subtitle(runtime_detail or _("Sunshine is not available."))
            row.add_css_class("error")
            row.add_prefix(create_icon_widget("brp-network-offline-symbolic", size=24))
        group.add(row)

        # Live config via the Sunshine API (only meaningful while it runs). The
        # file (SunshineConfigManager) stays the pre-start source of truth.
        live_row = Adw.ActionRow(use_markup=False)
        live_row.set_title(_("Apply to Running Server"))
        live_row.set_subtitle(_("Push these settings to Sunshine and restart it (no manual stop)."))
        apply_btn = Gtk.Button(label=_("Apply"))
        apply_btn.add_css_class("suggested-action")
        apply_btn.set_valign(Gtk.Align.CENTER)
        apply_btn.connect("clicked", self.on_apply_live_clicked)
        live_row.add_suffix(apply_btn)
        group.add(live_row)

        reload_row = Adw.ActionRow(use_markup=False)
        reload_row.set_title(_("Reload from Running Server"))
        reload_row.set_subtitle(_("Read the server's current configuration into these settings."))
        reload_btn = Gtk.Button(label=_("Reload"))
        reload_btn.set_valign(Gtk.Align.CENTER)
        reload_btn.connect("clicked", self.on_reload_live_clicked)
        reload_row.add_suffix(reload_btn)
        group.add(reload_row)

        # NOTE: server password change/reset lives in the body (Server →
        # Management → Server Password). Not duplicated here.

    def _api_auth(self) -> tuple[str, str] | None:
        return load_sunshine_credentials(conf_path=self.config.config_file)

    def _toast(self, widget, message):
        root = widget.get_root()
        if root and hasattr(root, "add_toast"):
            root.add_toast(Adw.Toast.new(message))
        elif root and hasattr(root, "show_toast"):
            root.show_toast(message)
        return False

    def on_apply_live_clicked(self, btn: Gtk.Widget) -> None:
        if not self.sunshine.is_running():
            self._toast(btn, _("Sunshine is not running. Settings are saved to file."))
            return
        import threading

        auth = self._api_auth()
        # Credentials are managed separately; do not push them as config keys.
        settings = {k: v for k, v in self.config.config.items() if k not in LEGACY_SECRET_KEYS}

        def work() -> None:
            ok = self.sunshine.save_config(settings, auth=auth)
            if ok:
                self.sunshine.restart_via_api(auth=auth)
            GLib.idle_add(self._toast, btn, _("Settings applied; server restarting.") if ok else _("Failed to apply settings (check credentials)."))

        threading.Thread(target=work, daemon=True).start()

    def on_reload_live_clicked(self, btn):
        if not self.sunshine.is_running():
            self._toast(btn, _("Sunshine is not running."))
            return
        import threading

        auth = self._api_auth()

        def work():
            cfg = self.sunshine.get_config(auth=auth)
            GLib.idle_add(self._apply_reloaded_config, cfg, btn)

        threading.Thread(target=work, daemon=True).start()

    def _apply_reloaded_config(self, cfg, btn):
        if not cfg:
            self._toast(btn, _("Could not read server configuration."))
            return False
        for key, value in cfg.items():
            if key in ("status", "platform", "version"):
                continue
            self.config.config[key] = str(value)
        self.config.save()
        self._toast(btn, _("Configuration reloaded. Reopen preferences to see updated values."))
        return False

    def get_general_options(self):
        return [
            (
                "locale",
                _("Locale"),
                "combo",
                "en",
                ["bg", "cs", "de", "en", "en_GB", "en_US", "es", "fr", "hu", "it", "ja", "ko", "pl", "pt", "pt_BR", "ru", "sv", "tr", "uk", "vi", "zh", "zh_TW"],
                _("The locale used for Sunshine's user interface."),
            ),
            ("notify_pre_releases", _("Pre-Release Notifications"), "switch", "false", None, _("Notify when new pre-release versions of Sunshine are available")),
            ("system_tray", _("Enable System Tray"), "switch", "true", None, _("Show icon in system tray and display desktop notifications")),
        ]

    def get_network_options(self):
        return [
            ("bind_address", _("Bind Address"), "entry", "0.0.0.0", None, _("IP address to bind the service to")),  # nosec B104 (Sunshine's documented default shown in a user-editable entry, not a bind)
            ("external_ip", _("External IP"), "entry", "", None, _("If no external IP address is provided, Sunshine detects it automatically.")),
            (
                "csrf_allowed_origins",
                _("Trusted Web UI Origins"),
                "entry",
                "",
                None,
                _("Comma-separated trusted HTTPS origins allowed to call Sunshine state-changing API endpoints. Leave empty to use Sunshine's built-in localhost defaults."),
            ),
            (
                "lan_encryption_mode",
                _("LAN Encryption"),
                "combo",
                "0",
                [("0", _("Disabled")), ("1", _("Mode 1")), ("2", _("Mode 2"))],
                _("This determines when encryption will be used when streaming over your local network. Encryption can reduce streaming performance, particularly on less powerful hosts and clients."),
            ),
            (
                "wan_encryption_mode",
                _("WAN Encryption"),
                "combo",
                "1",
                [("0", _("Disabled")), ("1", _("Mode 1")), ("2", _("Mode 2"))],
                _("This determines when encryption will be used when streaming over the Internet. Encryption can reduce streaming performance, particularly on less powerful hosts and clients."),
            ),
            ("ping_timeout", _("Ping Timeout (ms)"), "spin", "10000", None, _("How long to wait in milliseconds for data from Moonlight before shutting down the stream")),
            (
                "packetsize",
                _("Packet Size"),
                "spin",
                "0",
                None,
                _("Limit UDP packet size to avoid fragmentation on low-MTU VPN links. Use 0 for Sunshine's default."),
            ),
        ]

    def get_input_options(self):
        return [
            ("controller", _("Enable Gamepad Input"), "switch", "true", None, _("Allows guests to control the host system with a gamepad / controller")),
            ("motion_as_ds4", _("Motion as DS4"), "switch", "true", None, _("Emulate motion controls as DS4")),
            ("touchpad_as_ds4", _("Touchpad as DS4"), "switch", "true", None, _("Emulate touchpad as DS4")),
            ("ds4_back_as_touchpad_click", _("Back Button as Touchpad Click"), "switch", "true", None, _("Use Back button for touchpad click on DS4")),
            ("ds5_inputtino_randomize_mac", _("Randomize MAC (DS5)"), "switch", "true", None, _("Randomize virtual MAC address for DS5")),
            ("back_button_timeout", _("Home/Guide Timeout (ms)"), "spin", "-1", None, _("Hold Back/Select to emulate the Guide button. Use a value < 0 to disable.")),
            ("keyboard", _("Enable Keyboard Input"), "switch", "true", None, _("Allows guests to control the host system with the keyboard")),
            ("mouse", _("Enable Mouse Input"), "switch", "true", None, _("Allows guests to control the host system with the mouse")),
            ("always_send_scancodes", _("Always Send Scancodes"), "switch", "true", None, _("Always send raw key scancodes")),
            ("key_rightalt_to_key_win", _("Map Right Alt to Windows"), "switch", "false", None, _("Make Sunshine think the Right Alt key is the Windows key")),
            ("high_resolution_scrolling", _("High Resolution Scrolling"), "switch", "true", None, _("Pass through high resolution scroll events from Moonlight clients")),
        ]

    def get_monitors(self):
        monitors = []
        is_wayland = os.environ.get("XDG_SESSION_TYPE") == "wayland"
        try:
            display = Gdk.Display.get_default()
            if display:
                monitor_list = display.get_monitors()
                for i in range(monitor_list.get_n_items()):
                    monitor = monitor_list.get_item(i)
                    if monitor is None:
                        continue
                    name = monitor.get_connector()
                    if name:
                        manufacturer = monitor.get_manufacturer() or ""
                        model = monitor.get_model() or ""
                        label_parts = []
                        if manufacturer:
                            label_parts.append(manufacturer)
                        if model:
                            label_parts.append(model)
                        label = " ".join(label_parts) if label_parts else "Monitor"

                        # Value logic: Wayland uses 0, 1, 2... | X11 uses HDMI-A-1, etc.
                        val = str(i) if is_wayland else name
                        full_label = f"{label} ({name})"
                        monitors.append((full_label, val))
        except Exception as e:
            _log.error(f"Error getting monitors: {e}")

        if not monitors:
            return [("auto", _("Auto / Primary"))]

        return monitors

    def get_av_options(self):
        # Capture, sink and bitrate have one owner: the sharing page. Offering
        # them here too silently lost edits on the next start/save.
        return [
            (
                "minimum_fps_target",
                _("Minimum FPS Target"),
                "spin",
                "0",
                None,
                _("Minimum capture refresh target. Zero lets Sunshine choose it. This does not set the stream frame rate."),
            ),
        ]

    def get_advanced_options(self):
        return [
            (
                "qp",
                _("Quantization Parameter"),
                "spin",
                "28",
                None,
                _("Some devices may not support Constant Bit Rate. For those devices, QP is used instead. Higher value means more compression, but less quality."),
            ),
            (
                "capture",
                _("Force a Specific Capture Method"),
                "combo",
                "auto",
                [("auto", _("Autodetect (Recommended)")), ("nvfbc", "NvFBC"), ("wlr", "wlroots"), ("kms", "KMS"), ("x11", "X11"), ("portal", "XDG Desktop Portal")],
                _("In automatic mode, Sunshine uses the first method that works. NvFBC requires patched NVIDIA drivers."),
            ),
        ]

    def get_nvenc_options(self):
        return [
            ("nvenc_spatial_aq", _("Spatial AQ"), "switch", "false", None, _("Assign higher QP values to flat regions of the video. Recommended to enable when streaming at lower bitrates.")),
            (
                "nvenc_vbv_increase",
                _("Single-frame VBV/HRD percentage increase"),
                "spin",
                "0",
                None,
                _(
                    "By default sunshine uses single-frame VBV/HRD, which means any encoded video frame size is not expected to exceed requested bitrate divided by requested frame rate. Relaxing this restriction can be beneficial and act as low-latency variable bitrate, but may also lead to packet loss if the network doesn't have buffer headroom to handle bitrate spikes. Maximum accepted value is 400, which corresponds to 5x increased encoded video frame upper size limit."
                ),
            ),
            (
                "nvenc_h264_cavlc",
                _("Prefer CAVLC over CABAC in H.264"),
                "switch",
                "false",
                None,
                _("Simpler form of entropy coding. CAVLC needs around 10% more bitrate for same quality. Only relevant for really old decoding devices."),
            ),
        ]

    def get_qsv_options(self):
        return [
            ("qsv_preset", _("QuickSync Preset"), "combo", "medium", ["veryfast", "faster", "fast", "medium", "slow", "slower", "slowest"], _("Performance preset")),
            ("qsv_coder", _("QuickSync Coder (H264)"), "combo", "auto", [("auto", _("Auto")), ("cabac", "CABAC"), ("cavlc", "CAVLC")], _("Entropy coding mode")),
            ("qsv_slow_hevc", _("Allow Slow HEVC Encoding"), "switch", "false", None, _("This can enable HEVC encoding on older Intel GPUs, at the cost of higher GPU usage and worse performance.")),
        ]

    def get_amf_options(self):
        return [
            (
                "amd_usage",
                _("AMF Usage"),
                "combo",
                "ultralowlatency",
                [
                    ("transcoding", "Transcoding"),
                    ("webcam", "Webcam"),
                    ("lowlatency_high_quality", "Low Latency High Quality"),
                    ("lowlatency", "Low Latency"),
                    ("ultralowlatency", "Ultra Low Latency"),
                ],
                _(
                    "This sets the base encoding profile. All options presented below will override a subset of the usage profile, but there are additional hidden settings applied that cannot be configured elsewhere."
                ),
            ),
            (
                "amd_rc",
                _("AMF Rate Control"),
                "combo",
                "vbr_latency",
                [("cbr", "CBR"), ("cqp", "CQP"), ("vbr_latency", "VBR Latency"), ("vbr_peak", "VBR Peak")],
                _(
                    "This controls the rate control method to ensure we are not exceeding the client bitrate target. 'cqp' is not suitable for bitrate targeting, and other options besides 'vbr_latency' depend on HRD Enforcement to help constrain bitrate overflows."
                ),
            ),
            (
                "amd_enforce_hrd",
                _("AMF Hypothetical Reference Decoder (HRD) Enforcement"),
                "switch",
                "false",
                None,
                _(
                    "Increases the constraints on rate control to meet HRD model requirements. This greatly reduces bitrate overflows, but may cause encoding artifacts or reduced quality on certain cards."
                ),
            ),
            ("amd_preanalysis", _("AMF Preanalysis"), "switch", "false", None, _("This enables rate-control preanalysis, which may increase quality at the expense of increased encoding latency.")),
            (
                "amd_vbaq",
                _("AMF Variance Based Adaptive Quantization (VBAQ)"),
                "switch",
                "true",
                None,
                _(
                    "The human visual system is typically less sensitive to artifacts in highly textured areas. In VBAQ mode, pixel variance is used to indicate the complexity of spatial textures, allowing the encoder to allocate more bits to smoother areas. Enabling this feature leads to improvements in subjective visual quality with some content."
                ),
            ),
            (
                "amd_coder",
                _("AMF Coder (H264)"),
                "combo",
                "auto",
                [("auto", _("Auto")), ("cabac", "CABAC"), ("cavlc", "CAVLC")],
                _("Allows you to select the entropy encoding to prioritize quality or encoding speed. H.264 only."),
            ),
        ]

    def get_vt_options(self):
        return [
            ("vt_coder", _("VideoToolbox Coder"), "combo", "auto", [("auto", _("Auto")), ("cabac", "CABAC"), ("cavlc", "CAVLC")], _("Entropy coding mode")),
            (
                "vt_software",
                _("VideoToolbox Software Encoding"),
                "combo",
                "auto",
                [("auto", _("Auto")), ("disabled", _("Disabled")), ("allowed", _("Allowed")), ("forced", _("Forced"))],
                _("Allow fallback to software encoding"),
            ),
            ("vt_realtime", _("VideoToolbox Realtime Encoding"), "switch", "true", None, _("Realtime encoding priority")),
        ]

    def get_vaapi_options(self):
        return [
            (
                "vaapi_strict_rc_buffer",
                _("Strictly enforce frame bitrate limits for H.264/HEVC on AMD GPUs"),
                "switch",
                "false",
                None,
                _("Enabling this option can avoid dropped frames over the network during scene changes, but video quality may be reduced during motion."),
            ),
        ]

    def get_software_options(self):
        return [
            (
                "sw_tune",
                _("SW Tune"),
                "combo",
                "zerolatency",
                [("film", "Film"), ("animation", "Animation"), ("grain", "Grain"), ("stillimage", "Still Image"), ("fastdecode", "Fast Decode"), ("zerolatency", "Zero Latency")],
                _("Tuning options, which are applied after the preset. Defaults to zerolatency."),
            ),
        ]
