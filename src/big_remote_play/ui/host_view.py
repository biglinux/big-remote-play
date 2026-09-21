import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from collections.abc import Callable
from gi.repository import Gtk, Gdk, Adw, GLib, Pango  # type: ignore
import logging

_log = logging.getLogger("big-remoteplay")

import subprocess, random, string, json, socket, os, time
import shlex
from pathlib import Path
from big_remote_play.utils.game_detector import GameDetector

from big_remote_play.utils import auto_quality
from big_remote_play.utils.config import Config
import threading
from big_remote_play.utils.i18n import _
from big_remote_play.utils.icons import create_icon_widget, set_icon
from big_remote_play.integration_contracts import MOONLIGHT_PAIRING_PIN_LENGTH, BRP_DISCOVERY_CODE_LENGTH
from big_remote_play import paths
from big_remote_play.utils.secret_store import SecretStoreUnavailable
from big_remote_play.utils.sunshine_credentials import ensure_sunshine_api_config, load_sunshine_credentials, save_sunshine_credentials
from big_remote_play.utils.uri import open_uri, open_path
from .components import action_row, boxed_rows, intro, preferences_dialog, set_row_icon, content_dialog, name_icon_button


# Host FPS combo index -> value (index 4 "Custom" falls back to 60).
_HOST_FPS_BY_INDEX = {0: 30, 1: 60, 2: 120, 3: 144, 4: 60}
_HOST_FPS_INDEX_BY_VALUE = {30: 0, 60: 1, 120: 2, 144: 3}


def _parse_xrandr_monitor_names(output: str) -> list[str]:
    names: list[str] = []
    for line in output.splitlines()[1:]:
        parts = line.split()
        if parts:
            names.append(parts[-1])
    return names


def _split_launch_command(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return []


class HostView(Gtk.Box):
    def __init__(self):
        self.loading_settings = True
        self._closed = False
        self._fetching_global_ips = False
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.config = Config()
        self.is_hosting = False
        self.process = None  # Initialize to avoid AttributeError
        self.pin_code = None
        self.private_audio_apps = set()
        self.audio_devices = []
        self.active_host_sink = ""
        self._audio_routing_active = False
        self._active_audio_mode = 0
        self.stop_pin_listener = None
        self._uptime_timer_id = None
        self._save_timer_id = None
        self._hosting_started_at = None

        from big_remote_play.host.sunshine_manager import SunshineHost

        self.sunshine = SunshineHost(paths.SUNSHINE_CONFIG_DIR)

        if self.sunshine.is_running():
            self.is_hosting = True

        self.available_monitors = self.detect_monitors()
        self.available_gpus = self.detect_gpus()
        self.setup_ui()

        self.game_detector = GameDetector()
        self.detected_games = {"Steam": [], "Lutris": []}
        self._auto_signature = ""
        self.load_settings()
        self.connect_settings_signals()
        self.loading_settings = False
        # Detection runs after the saved values are in place, so it only writes
        # when the user wants automatic settings and the hardware moved.
        self._apply_auto_quality()

        # Ensure config is correct (API enabled)
        if hasattr(self, "_ensure_sunshine_config"):
            self._ensure_sunshine_config()

        self.sync_ui_state()

    def _root_window(self):
        root = self.get_root()
        return root if isinstance(root, Gtk.Window) else None

    def _go_to_private_network(self) -> None:
        root = self._root_window()
        setup = getattr(root, "_go_to_private_network_setup", None)
        if callable(setup):
            setup()

    def detect_monitors(self):
        monitors = [(_("Automatic"), "auto")]
        is_wayland = os.environ.get("XDG_SESSION_TYPE") == "wayland"

        # Method: GDK (Most consistent for labels and Wayland indices)
        names = []
        try:
            display = Gdk.Display.get_default()
            if display:
                monitor_list = display.get_monitors()
                for i in range(monitor_list.get_n_items()):
                    monitor = monitor_list.get_item(i)
                    if monitor is None:
                        continue
                    conn = monitor.get_connector()
                    if conn:
                        manufacturer = monitor.get_manufacturer() or ""
                        model = monitor.get_model() or ""
                        label_parts = []
                        if manufacturer:
                            label_parts.append(manufacturer)
                        if model:
                            label_parts.append(model)
                        label = " ".join(label_parts) if label_parts else "Monitor"

                        # Connector names are stable across reorderings; GDK indices
                        # need not match Sunshine's capture backend indices.
                        val = conn
                        full_label = f"{label} ({conn})"
                        monitors.append((full_label, val))
                        names.append(conn)
        except Exception as e:
            _log.error(f"Error detecting GDK monitors: {e}")

        # Fallback for X11/DRM if GDK didn't find everything
        if not is_wayland:
            # Xrandr (Reinforcement for X11)
            try:
                res = subprocess.check_output(["xrandr", "--listmonitors"], text=True, timeout=5)
                for n in _parse_xrandr_monitor_names(res):
                    if n and n not in names:
                        monitors.append((f"Display ({n})", n))
                        names.append(n)
            except Exception:
                pass

            # DRM (Reinforcement for KMS/DRM)
            try:
                from pathlib import Path

                for p in Path("/sys/class/drm").glob("card*-*"):
                    if (p / "status").exists() and (p / "status").read_text().strip() == "connected":
                        name = p.name.split("-", 1)[1]
                        if name not in names:
                            monitors.append((f"DRM Display ({name})", name))
                            names.append(name)
            except Exception:
                pass

        return monitors

    def detect_gpus(self):
        gpus = []
        try:
            lspci = subprocess.check_output(["lspci"], text=True, timeout=5).lower()
            if "nvidia" in lspci:
                try:
                    subprocess.check_call(["nvidia-smi"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                    gpus.append({"label": "NVENC (NVIDIA)", "encoder": "nvenc", "adapter": "auto"})
                except Exception:
                    pass
            if "intel" in lspci:
                gpus.append({"label": "VAAPI (Intel Quicksync)", "encoder": "vaapi", "adapter": "/dev/dri/renderD128"})
        except Exception:
            pass
        try:
            from pathlib import Path

            if Path("/dev/dri").exists():
                for node in sorted(list(Path("/dev/dri").glob("renderD*"))):
                    if not any(str(node) == g["adapter"] for g in gpus):
                        gpus.append({"label": f"VAAPI (Adapter {node.name})", "encoder": "vaapi", "adapter": str(node)})
        except Exception:
            pass
        gpus.extend([{"label": "Vulkan (Exp)", "encoder": "vulkan", "adapter": "auto"}, {"label": "Software", "encoder": "software", "adapter": "auto"}])
        # Append rather than prepend to preserve older saved GPU indices.
        gpus.append({"label": _("Automatic"), "encoder": "auto", "adapter": "auto"})
        return gpus

    def setup_ui(self) -> None:

        clamp = Adw.Clamp()
        clamp.set_maximum_size(820)
        self.content_clamp = clamp
        clamp.set_valign(Gtk.Align.START)
        for margin in ["top", "bottom", "start", "end"]:
            getattr(clamp, f"set_margin_{margin}")(20)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)

        self.loading_bar = Gtk.ProgressBar()
        self.loading_bar.add_css_class("osd")
        self.loading_bar.set_visible(False)
        content.append(self.loading_bar)

        from .performance_monitor import PerformanceMonitor

        self.perf_monitor = PerformanceMonitor(sunshine=self.sunshine)
        self.perf_monitor.set_visible(True)
        self.perf_monitor.set_connection_status("Localhost", _("Sunshine Offline"), False)

        game_group = Adw.PreferencesGroup()
        self.game_group = game_group
        game_group.set_title(_("1. Choose what to share"))
        game_group.set_description(_("Sharing the whole screen also shows notifications and other open windows."))

        reset_btn = Gtk.Button()
        reset_btn.set_child(create_icon_widget("brp-edit-undo-symbolic", size=16))
        reset_btn.add_css_class("flat")
        name_icon_button(reset_btn, _("Reset to Defaults"))
        reset_btn.connect("clicked", self.on_reset_clicked)
        self.settings_reset_button = reset_btn

        self.game_mode_row = Adw.ComboRow()
        self.game_mode_row.set_title(_("Source"))
        self.game_mode_row.set_use_subtitle(True)
        modes = Gtk.StringList()
        for m in [_("Full Desktop"), "Steam", "Lutris", _("Custom App")]:
            modes.append(m)
        self.game_mode_row.set_model(modes)
        self.game_mode_row.set_selected(0)
        self.game_mode_row.connect("notify::selected", self.on_game_mode_changed)
        game_group.add(self.game_mode_row)

        self.platform_games_expander = Adw.ExpanderRow()
        self.platform_games_expander.set_title(_("Game Selection"))
        self.platform_games_expander.set_subtitle(_("Choose game from list"))
        self.platform_games_expander.set_visible(False)

        self.game_list_row = Adw.ComboRow()
        self.game_list_row.set_title(_("Select Game"))
        self.game_list_row.set_subtitle(_("Choose game from list"))
        self.game_list_model = Gtk.StringList()
        self.game_list_row.set_model(self.game_list_model)
        self.platform_games_expander.add_row(self.game_list_row)
        game_group.add(self.platform_games_expander)

        self.custom_app_expander = Adw.ExpanderRow()
        self.custom_app_expander.set_title(_("Application Details"))
        self.custom_app_expander.set_subtitle(_("Configure name and command"))
        self.custom_app_expander.set_visible(False)

        self.custom_name_entry = Adw.EntryRow()
        self.custom_name_entry.set_title(_("Application Name"))
        self.custom_app_expander.add_row(self.custom_name_entry)

        self.custom_cmd_entry = Adw.EntryRow()
        self.custom_cmd_entry.set_title(_("Command"))
        browse_btn = Gtk.Button()
        browse_btn.set_child(create_icon_widget("brp-folder-open-symbolic", size=16))
        browse_btn.add_css_class("flat")
        browse_btn.set_valign(Gtk.Align.CENTER)
        browse_btn.set_tooltip_text(_("Browse this computer for an executable"))
        browse_btn.update_property([Gtk.AccessibleProperty.LABEL], [_("Browse files on this computer")])
        browse_btn.connect("clicked", lambda b: self.open_host_browse_dialog())
        self.custom_cmd_entry.add_suffix(browse_btn)
        self.custom_app_expander.add_row(self.custom_cmd_entry)
        game_group.add(self.custom_app_expander)

        self.streaming_group = Adw.PreferencesGroup(title=_("Picture"))

        # Automatic first: the detected values are the ones most people should
        # keep, and every manual row below stays visible but locked while it is
        # on, so nothing is hidden and nothing invites a pointless decision.
        self.auto_quality_row = Adw.SwitchRow()
        self.auto_quality_row.set_title(_("Automatic capture and encoding"))
        self.auto_quality_row.set_subtitle(_("Checking..."))
        self.auto_quality_row.set_active(True)
        self.streaming_group.add(self.auto_quality_row)

        self.redetect_row = Adw.ActionRow(title=_("Reapply automatic settings"), subtitle=_("Keeps your screen choice and video bitrate ceiling."))
        set_row_icon(self.redetect_row, "brp-view-refresh-symbolic")
        redetect_button = Gtk.Button(label=_("Apply"), valign=Gtk.Align.CENTER)
        redetect_button.connect("clicked", lambda _b: self._apply_auto_quality(force=True))
        self.redetect_row.add_suffix(redetect_button)
        self.redetect_row.set_activatable_widget(redetect_button)
        self.auto_status_row = Adw.ActionRow(title=_("Configured for the next sharing session"), use_markup=False)
        self.auto_status_row.set_subtitle_lines(0)
        self.streaming_group.add(self.auto_status_row)

        # Sunshine takes no stream resolution: the guest asks for the picture it
        # wants, so a resolution control here decided nothing.

        # FPS Row
        self.fps_row = Adw.ComboRow()
        self.fps_row.set_title(_("Frame Rate (FPS)"))
        self.fps_row.set_subtitle(_("Frames per second"))
        fps_model = Gtk.StringList()
        for fps in ["30", "60", "120", "144", _("Custom")]:
            fps_model.append(fps)
        self.fps_row.set_model(fps_model)
        self.fps_row.set_selected(1)  # Default 60
        # Sunshine does not accept a fixed stream FPS here: Moonlight requests it.
        # Keep the old value in memory for legacy config/monitor compatibility,
        # but never show a control that cannot change the stream.

        # Bandwidth Row
        self.bandwidth_row = Adw.SpinRow()
        self.bandwidth_row.set_title(_("Maximum video bitrate (Mbps)"))
        self.bandwidth_row.set_subtitle(_("0 follows the other computer’s request. This limits video, not total network traffic."))

        # Use simple numeric adjustment
        adj = Gtk.Adjustment(value=0, lower=0, upper=500, step_increment=5, page_increment=10)
        self.bandwidth_row.set_adjustment(adj)
        self.streaming_group.add(self.bandwidth_row)

        self.hardware_group = Adw.PreferencesGroup(title=_("Hardware and Capture"), description=_("Monitor, GPU, and Capture Method"))

        self.monitor_row = Adw.ComboRow()
        self.monitor_row.set_title(_("Monitor / Display"))
        self.monitor_row.set_subtitle(_("Select the display to capture"))
        monitor_model = Gtk.StringList()
        for label, _val in self.available_monitors:
            monitor_model.append(label)
        self.monitor_row.set_model(monitor_model)
        self.monitor_row.set_selected(0)
        self.hardware_group.add(self.monitor_row)

        self.gpu_row = Adw.ComboRow()
        self.gpu_row.set_title(_("Graphics Card / Encoder"))
        self.gpu_row.set_subtitle(_("Choose hardware for video encoding"))
        gpu_model = Gtk.StringList()
        for gpu_info in self.available_gpus:
            gpu_model.append(gpu_info["label"])
        self.gpu_row.set_model(gpu_model)
        self.gpu_row.set_selected(0)
        self.hardware_group.add(self.gpu_row)

        self.platform_row = Adw.ComboRow()
        self.platform_row.set_title(_("Capture Method"))
        self.platform_row.set_subtitle(_("Automatic works across desktops. Select a specific method only for troubleshooting."))
        platform_model = Gtk.StringList()
        self._capture_values = ("", "wlr", "x11", "kms", "kwin", "nvfbc", "portal")
        for p in [_("Automatic"), "Wayland / wlroots", "X11", _("KMS (Direct)"), "KDE / KWin", "NVIDIA / NvFBC", _("Portal (screen picker)")]:
            platform_model.append(p)
        self.platform_row.set_model(platform_model)
        self.platform_row.set_selected(0)
        self.hardware_group.add(self.platform_row)

        # New "Performance" settings as requested
        self.codecs_row = Adw.SwitchRow()
        self.codecs_row.set_title(_("Efficient video compression"))
        self.codecs_row.set_subtitle(_("Allow HEVC/AV1 when supported. The other computer chooses a compatible codec."))
        self.codecs_row.set_active(True)
        self.hardware_group.add(self.codecs_row)

        self.optimization_row = Adw.ComboRow()
        self.optimization_row.set_title(_("Priority"))
        self.optimization_row.set_subtitle(_("Faster response, or better picture"))
        opt_model = Gtk.StringList()
        opt_model.append(_("Low Latency (Fastest)"))
        opt_model.append(_("Balanced (Default)"))
        opt_model.append(_("High Quality (Best Image)"))
        self.optimization_row.set_model(opt_model)
        self.optimization_row.set_selected(1)  # Balanced default
        self.hardware_group.add(self.optimization_row)

        self.wifi_row = Adw.SwitchRow()
        self.wifi_row.set_title(_("Unstable network mode"))
        self.wifi_row.set_subtitle(_("Adds error-correction data (FEC). Uses more bandwidth and does not fix a slow connection."))
        self.wifi_row.set_active(False)
        self.hardware_group.add(self.wifi_row)
        self.hardware_group.add(self.redetect_row)

        # --- Audio Group ---
        audio_group = Adw.PreferencesGroup()
        audio_group.set_title(_("Audio"))
        audio_group.set_description(_("By default, share the sound you already hear without changing this computer’s output. A connecting client can still request that Sunshine mute the game PC."))

        # 1. Host Output (Always visible, serves as the "Host" part of Host+Guest)
        self.audio_output_row = Adw.ComboRow()
        self.audio_output_row.set_title(_("Audio output"))
        self.audio_output_row.set_tooltip_text(_("Keep the current output, or explicitly select a device to enable audio routing."))
        self.audio_output_row.set_use_subtitle(True)
        self.audio_output_row.set_subtitle_lines(0)
        set_row_icon(self.audio_output_row, "brp-audio-speakers-symbolic")
        self.audio_output_row.connect("notify::selected", self.on_audio_output_changed)
        audio_group.add(self.audio_output_row)

        # 2. Audio Mode ComboRow
        self.audio_mode_row = Adw.ComboRow()
        self.audio_mode_row.set_title(_("Where the sound plays"))
        self.audio_mode_row.set_tooltip_text(_("Available only after choosing an output device. Changes apply the next time you start sharing."))
        self.audio_mode_row.set_use_subtitle(True)
        self.audio_mode_row.set_subtitle_lines(0)
        set_row_icon(self.audio_mode_row, "brp-audio-volume-medium-symbolic")

        mode_model = Gtk.StringList()
        mode_model.append(_("Keep local playback"))  # Index 0
        mode_model.append(_("Other computer"))  # Index 1
        mode_model.append(_("This computer"))  # Index 2
        mode_model.append(_("Both computers"))  # Index 3

        self.audio_mode_row.set_model(mode_model)
        self.audio_mode_row.connect("notify::selected", self.on_audio_mode_changed)
        audio_group.add(self.audio_mode_row)

        # 3. Mixer (Only if Streaming is Enabled)
        self.audio_mixer_group = Adw.PreferencesGroup(title=_("Audio Mixer (Sources)"), description=_("Manage audio sources"))
        self.mixer_empty_row = Adw.ActionRow(
            title=_("No audio sources yet"),
            subtitle=_("Start sharing and play sound in an app to see it here."),
            use_markup=False,
        )
        self.mixer_empty_row.set_subtitle_lines(0)
        self.audio_mixer_group.add(self.mixer_empty_row)

        self.load_audio_outputs()

        self.advanced_group = Adw.PreferencesGroup(title=_("Advanced Settings"), description=_("Input, Network, and Access"))

        self.upnp_row = Adw.SwitchRow()
        self.upnp_row.set_title(_("Try automatic port forwarding (UPnP)"))
        self.upnp_row.set_subtitle(_("Sunshine asks a compatible router when it starts. Enabling this does not confirm that the ports opened."))
        self.upnp_row.set_active(False)
        self.advanced_group.add(self.upnp_row)
        upnp_help = Adw.ActionRow(
            title=_("For direct internet access only"),
            subtitle=_(
                "Keep off for local play or a VPN. UPnP must be enabled on the router; it does not bypass CGNAT, double NAT or the computer’s firewall. Restart sharing and test from another network."
            ),
            use_markup=False,
        )
        upnp_help.set_subtitle_lines(0)
        self.advanced_group.add(upnp_help)
        self.advanced_group.add(action_row(_("Connect without a VPN"), _("Domain, router ports and security precautions."), "brp-address-symbolic", self._show_direct_internet_guide))

        self.ipv6_row = Adw.SwitchRow()
        self.ipv6_row.set_title(_("Also use IPv6"))
        self.ipv6_row.set_subtitle(_("Enable simultaneous IPv4 and IPv6 support on server"))
        self.ipv6_row.set_active(False)
        self.advanced_group.add(self.ipv6_row)

        self.webui_anyone_row = Adw.SwitchRow()
        self.webui_anyone_row.set_title(_("Allow internet access to administration"))
        self.webui_anyone_row.set_subtitle(_("Keep off for normal play. If enabled together with UPnP, Sunshine may also open the administration port on the router."))
        self.webui_anyone_row.set_active(False)
        self.advanced_group.add(self.webui_anyone_row)

        self.firewall_row = Adw.ActionRow()
        self.firewall_row.set_title(_("Configure Firewall (IPv6)"))
        self.firewall_row.set_subtitle(_("Open TCP/UDP ports required for external connection"))
        set_row_icon(self.firewall_row, "brp-firewall-symbolic")

        fw_btn = Gtk.Button(label=_("Configure"))
        fw_btn.connect("clicked", self.on_configure_firewall_clicked)
        fw_btn.set_valign(Gtk.Align.CENTER)
        self.firewall_row.add_suffix(fw_btn)
        self.advanced_group.add(self.firewall_row)

        self.create_summary_box()

        # View Switcher and Stack
        self.view_stack = Adw.ViewStack()
        self.view_stack.set_hhomogeneous(False)
        self.view_stack.set_vexpand(False)

        server_tools_group = Adw.PreferencesGroup()
        server_tools_group.add_css_class("brp-rounded-group")
        server_tools_group.set_title(_("Server tools"))
        server_tools_group.set_description(_("Use these when you need to inspect or manage Sunshine."))
        advanced_tools_group = Adw.PreferencesGroup()
        advanced_tools_group.add_css_class("brp-rounded-group")
        advanced_tools_group.set_title(_("Advanced administration"))
        advanced_tools_group.set_description(_("Security and expert settings that are rarely needed."))

        def _add_management_button(group: Adw.PreferencesGroup, title: str, subtitle: str, icon_name: str, callback: Callable[[Gtk.Widget], None]) -> None:
            # Use a native PreferencesRow instead of placing a Gtk.Button inside
            # the group.  This lets libadwaita own the grouped-list shape, hover,
            # focus and keyboard activation, and gives assistive technologies one
            # coherent row rather than a button containing duplicate text labels.
            row = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False)
            set_row_icon(row, icon_name)
            row.set_activatable(True)
            row.connect("activated", callback)
            row.update_property(
                [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
                [title, subtitle],
            )

            arrow = create_icon_widget("go-next-symbolic", size=16)
            arrow.set_valign(Gtk.Align.CENTER)
            row.add_suffix(arrow)
            group.add(row)

        _add_management_button(
            server_tools_group,
            _("Server control panel"),
            _("Advanced settings in your browser (Sunshine)"),
            "brp-host-symbolic",
            self.open_sunshine_config,
        )
        _add_management_button(
            server_tools_group,
            _("Server log"),
            _("What the server recorded, for troubleshooting"),
            "brp-diagnostics-symbolic",
            self.open_logs_dialog,
        )
        _add_management_button(
            server_tools_group,
            _("Game Library"),
            _("Manage games shown on the other computer"),
            "brp-library-symbolic",
            self.open_game_library_dialog,
        )
        _add_management_button(
            advanced_tools_group,
            _("Server password"),
            _("Used by this app to talk to the server"),
            "brp-dialog-password-symbolic",
            self.open_password_dialog,
        )
        _add_management_button(
            advanced_tools_group,
            _("Advanced server settings"),
            _("Codecs, network, capture and recovery tools"),
            "brp-preferences-symbolic",
            self.open_advanced_settings,
        )

        def _create_overview_action_button(label: str, callback: Callable[[Gtk.Widget], None], primary: bool = False) -> Gtk.Button:
            button = Gtk.Button()
            button.update_property([Gtk.AccessibleProperty.LABEL], [label])
            button.set_valign(Gtk.Align.CENTER)
            if primary:
                button.add_css_class("suggested-action")
            button.connect("clicked", callback)
            return button

        self.overview_start_button = _create_overview_action_button(_("Start sharing"), self.toggle_hosting, primary=True)
        overview_start_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, halign=Gtk.Align.CENTER)
        self.overview_start_icon = create_icon_widget("media-playback-start-symbolic", size=16)
        overview_start_content.append(self.overview_start_icon)
        self.overview_start_label = Gtk.Label(label=_("Start sharing"))
        overview_start_content.append(self.overview_start_label)
        self.overview_start_button.set_child(overview_start_content)
        self.overview_start_button.set_tooltip_text(_("Start sharing"))
        self.overview_start_button.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            [_("Start sharing"), _("Start sharing the game from this PC")],
        )

        self.private_network_button = _create_overview_action_button(_("Set up Private Network"), lambda _b: self._go_to_private_network())
        self.private_network_button.set_child(create_icon_widget("go-next-symbolic", size=16))
        self.private_network_button.set_tooltip_text(_("Set up Private Network"))
        self.private_network_button.update_property([Gtk.AccessibleProperty.LABEL], [_("Open Private Network setup")])

        # A single premium session surface keeps the role, live state and primary
        # action together. It becomes vertical through the window breakpoint,
        # without duplicating controls or changing keyboard order.
        hero = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        hero.add_css_class("session-hero")
        hero.set_halign(Gtk.Align.FILL)
        hero.set_hexpand(True)
        self.overview_hero = hero

        hero_identity = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        hero_identity.set_hexpand(True)
        hero_identity.set_valign(Gtk.Align.CENTER)

        hero_chip = Gtk.Box()
        hero_chip.add_css_class("hero-icon-chip")
        hero_chip.set_valign(Gtk.Align.CENTER)
        hero_icon = create_icon_widget("brp-host-symbolic", size=28)
        for margin in ("top", "bottom", "start", "end"):
            getattr(hero_icon, f"set_margin_{margin}")(10)
        hero_chip.append(hero_icon)
        hero_identity.append(hero_chip)

        hero_copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        hero_copy.set_hexpand(True)
        hero_copy.set_valign(Gtk.Align.CENTER)

        hero_title = Gtk.Label(label=_("Share from this PC"))
        hero_title.add_css_class("title-2")
        hero_title.set_halign(Gtk.Align.START)
        hero_title.set_xalign(0)
        hero_title.set_wrap(True)
        hero_copy.append(hero_title)

        self.overview_status_label = Gtk.Label(label=_("Choose a source, then start sharing."))
        self.overview_status_label.add_css_class("dim-label")
        self.overview_status_label.set_halign(Gtk.Align.START)
        self.overview_status_label.set_xalign(0)
        self.overview_status_label.set_wrap(True)
        hero_copy.append(self.overview_status_label)
        hero_identity.append(hero_copy)
        hero.append(hero_identity)

        self.overview_hero_actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.overview_hero_actions.set_halign(Gtk.Align.END)
        self.overview_hero_actions.set_valign(Gtk.Align.CENTER)

        self.overview_state_label = Gtk.Label(label=_("Stopped"))
        self.overview_state_label.add_css_class("state-pill")
        self.overview_state_label.add_css_class("offline")
        self.overview_state_label.set_halign(Gtk.Align.END)
        self.overview_hero_actions.append(self.overview_state_label)

        self.overview_start_button.set_halign(Gtk.Align.FILL)
        self.overview_start_button.set_hexpand(True)
        self.overview_start_button.set_size_request(168, 44)
        # The primary action follows its input instead of preceding it.
        self.overview_start_button.add_css_class("brp-primary")
        hero.append(self.overview_hero_actions)

        # Two four-digit codes used to sit side by side pointing in opposite
        # directions: the one this PC announces for discovery, and the one the
        # other PC shows to pair. The steps are now numbered and only the code
        # the person has to type is on the routine path.
        pin_group = Adw.PreferencesGroup()
        self.guest_access_group = pin_group
        pin_group.set_title(_("3. Connect the other PC"))
        pin_group.set_description(_("Open Connect on the other PC, then approve its code here. This is needed only the first time."))

        first_step = Adw.ActionRow(
            title=_("Open Connect on the other computer"),
            subtitle=_("Choose this computer from the list."),
            use_markup=False,
        )
        first_step.set_title_lines(0)
        first_step.set_subtitle_lines(0)
        set_row_icon(first_step, "brp-client-symbolic")
        pin_group.add(first_step)

        self.pair_entry = Adw.EntryRow(title=_("Pairing code shown on the other PC"))
        self.pair_entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
        self.pair_entry.add_css_class("brp-code-entry")
        self.pair_entry.update_property(
            [Gtk.AccessibleProperty.DESCRIPTION],
            [_("Enter the four digits shown by Moonlight on the other computer.")],
        )
        self.guest_pair_button = _create_overview_action_button(_("Pair"), lambda _button: self.pair_with_entered_pin())
        self.guest_pair_button.set_label(_("Pair"))
        self.guest_pair_button.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            [_("Pair this device"), _("Send the code shown on the other PC to finish pairing")],
        )
        self.pair_entry.add_suffix(self.guest_pair_button)
        self.pair_entry.connect("entry-activated", lambda _row: self.pair_with_entered_pin())
        pin_group.add(self.pair_entry)

        # The discovery code is the fallback, and says so.
        self.pin_display_label = Gtk.Label(label="—" * BRP_DISCOVERY_CODE_LENGTH)
        self.pin_display_label.add_css_class("monospace")
        self.pin_display_label.set_selectable(True)

        copy_btn = Gtk.Button()
        copy_btn.set_child(create_icon_widget("brp-edit-copy-symbolic", size=16))
        copy_btn.add_css_class("flat")
        copy_btn.set_valign(Gtk.Align.CENTER)
        copy_btn.set_tooltip_text(_("Copy code"))
        copy_btn.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            [_("Copy code"), _("Copy this PC's discovery code to the clipboard")],
        )
        copy_btn.connect("clicked", lambda b: self.copy_field_value("pin"))

        fallback_row = Adw.ActionRow(
            title=_("Search code"),
            subtitle=_("In Big Remote Play on the other PC, choose Find by code. This is not the pairing code."),
            use_markup=False,
        )
        fallback_row.set_title_lines(0)
        fallback_row.set_subtitle_lines(0)
        set_row_icon(fallback_row, "brp-dialog-password-symbolic")
        pin_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        pin_box.append(self.pin_display_label)
        pin_box.append(copy_btn)
        fallback_row.add_suffix(pin_box)
        fallback = Adw.ExpanderRow(title=_("This PC did not appear in the list?"), use_markup=False)
        fallback.add_row(fallback_row)
        pin_group.add(fallback)

        network_row = Adw.ActionRow(
            title=_("Playing over the internet?"),
            subtitle=_("Same home network? Skip this step. For different networks, set up internet play."),
        )
        set_row_icon(network_row, "brp-network-private-symbolic")
        network_row.add_suffix(self.private_network_button)
        network_row.set_activatable_widget(self.private_network_button)
        network_group = Adw.PreferencesGroup()
        network_group.add(network_row)

        # Register PIN for updates.
        self.field_widgets["pin"] = {"label": self.pin_display_label, "real_value": "", "revealed": True}

        overview_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        overview_body.append(hero)
        self.share_controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.share_controls.add_css_class("brp-session-panel")
        self.share_controls.append(game_group)
        # The picture settings are stated on the page that starts the stream, so
        # nobody has to open a sheet to learn what they are about to send.
        self.quality_summary_row = action_row(
            _("Image and capture"),
            _("Checking..."),
            "brp-quality-symbolic",
            self._open_quality_sheet,
        )
        self.quality_summary_box = boxed_rows(self.quality_summary_row)
        self.share_controls.append(self.quality_summary_box)

        # While sharing, these controls decide nothing: the session is running
        # with the values it started with. The page states what is being sent
        # instead of showing a form that cannot be applied.
        self.session_summary_row = Adw.ActionRow(title=_("Sharing now"), subtitle=_("Checking..."), use_markup=False)
        self.session_summary_row.set_title_lines(0)
        self.session_summary_row.set_subtitle_lines(0)
        set_row_icon(self.session_summary_row, "brp-host-symbolic")
        self.session_summary_box = boxed_rows(self.session_summary_row)
        self.session_summary_box.set_visible(False)
        self.share_controls.append(self.session_summary_box)
        self.overview_start_button.set_halign(Gtk.Align.FILL)
        start_heading = Gtk.Label(label=_("2. Start sharing"), xalign=0)
        self.start_heading = start_heading
        start_heading.add_css_class("heading")
        self.share_controls.append(start_heading)
        self.share_controls.append(self.overview_start_button)
        overview_body.append(self.share_controls)
        overview_body.append(pin_group)
        overview_body.append(self._create_paired_devices_overview())
        overview_body.append(network_group)
        overview_page = Adw.Clamp(maximum_size=820, tightening_threshold=560)
        overview_page.set_child(overview_body)
        self.view_stack.add_titled_with_icon(overview_page, "overview", _("Overview"), "brp-host-symbolic")

        # Everyday settings first. Detailed controls stay available in searchable
        # native sheets without inflating the routine sharing page.
        config_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        config_page.append(intro(_("Sound and access on this computer"), _("Image and capture settings are on Overview, next to Start sharing."), "brp-preferences-symbolic"))
        audio_group.set_header_suffix(self.settings_reset_button)
        config_page.append(audio_group)
        settings_links = Adw.PreferencesGroup(title=_("Additional settings"))
        self.quality_sheet = preferences_dialog(
            _("Image and capture"),
            [self.streaming_group, self.hardware_group],
            description=_(
                "This computer captures and encodes the game. Under Connect → Image, the other computer requests resolution, frame rate and video bitrate. The bitrate ceiling here limits a higher request; 0 adds no ceiling. Codec and HDR also depend on both computers. Changes apply when sharing starts again."
            ),
            height=640,
        )
        self.streaming_group.set_title(_("Automatic settings and video limit"))
        self.hardware_group.set_title(_("Screen, graphics card and encoding"))
        self.hardware_group.set_description(_("Automatic options below are chosen by Sunshine when sharing starts, not measurements of an active stream."))
        self.settings_dialogs = {_("Image and capture"): self.quality_sheet}
        sections = (
            (self.audio_mixer_group, _("Audio Mixer (Sources)"), _("Optional per-app routing. Requires an explicitly chosen output and a new sharing session."), "brp-audio-speakers-symbolic", 400),
            (self.advanced_group, _("Network and access"), _("Keep router port forwarding off when using a VPN. Changes apply when sharing starts again."), "brp-network-private-symbolic", 600),
        )
        for group, title, description, icon, height in sections:
            group.set_title("")
            group.set_description("")
            sheet = preferences_dialog(title, [group], description=description, height=height)
            self.settings_dialogs[title] = sheet
            link = action_row(title, description, icon, lambda d=sheet: d.present(self))
            if group is self.audio_mixer_group:
                self.audio_mixer_link = link
            settings_links.add(link)
        config_page.append(settings_links)
        self.view_stack.add_titled_with_icon(config_page, "config", _("Preferences"), "brp-preferences-symbolic")

        support_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        support_page.append(intro(_("Solve sharing problems"), _("Check the connection first. Open server tools only when needed."), "brp-support-symbolic"))
        # The identical network row already sits on Overview, where sharing starts.
        support_page.append(self.perf_monitor)
        support_page.append(self.summary_box)
        support_page.append(server_tools_group)
        support_page.append(advanced_tools_group)
        support_page.append(self._create_app_diagnostics_group())
        self.view_stack.add_titled_with_icon(support_page, "support", _("Support"), "brp-support-symbolic")

        self.perf_monitor.set_visible(False)
        content.append(self.view_stack)

        clamp.set_child(content)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(clamp)
        self.append(scroll)

    def _create_app_diagnostics_group(self) -> Adw.PreferencesGroup:
        """Log switch, log cleanup and the config path, beside the other
        troubleshooting tools instead of in a separate settings window."""
        from big_remote_play.utils.logger import Logger

        group = Adw.PreferencesGroup()
        group.add_css_class("brp-rounded-group")
        group.set_title(_("Application diagnostics"))
        group.set_description(_("Only needed when reporting a problem."))

        verbose_row = Adw.SwitchRow(title=_("Detailed Logs"), subtitle=_("Enable verbose logging for debugging"))
        verbose_row.set_active(bool(self.config.get("verbose_logging", False)))

        def on_verbose(row, _param):
            enabled = row.get_active()
            self.config.set("verbose_logging", enabled)
            logger = Logger(force_new=True)
            logger.set_verbose(enabled)

        verbose_row.connect("notify::active", on_verbose)
        group.add(verbose_row)

        clear_row = Adw.ActionRow(title=_("Clear Logs"), subtitle=_("Remove old log files"))
        clear_button = Gtk.Button(label=_("Clear"), valign=Gtk.Align.CENTER)
        clear_button.add_css_class("destructive-action")
        clear_button.connect("clicked", lambda _button: (Logger().clear_old_logs(), self.show_toast(_("Old log files have been removed."))))
        clear_row.add_suffix(clear_button)
        clear_row.set_activatable_widget(clear_button)
        group.add(clear_row)

        path_row = Adw.ActionRow(title=_("Configuration Directory"), subtitle=str(paths.CONFIG_DIR), use_markup=False)
        path_row.set_subtitle_lines(2)
        copy_button = Gtk.Button(valign=Gtk.Align.CENTER)
        copy_button.set_child(create_icon_widget("brp-edit-copy-symbolic", size=16))
        copy_button.add_css_class("flat")
        name_icon_button(copy_button, _("Copy Path"))
        copy_button.connect("clicked", lambda _button: self._copy_text(str(paths.CONFIG_DIR), _("Path copied!")))
        path_row.add_suffix(copy_button)
        path_row.set_activatable_widget(copy_button)
        group.add(path_row)
        return group

    def _copy_text(self, text: str, message: str) -> None:
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(text)
        self.show_toast(message)

    # ── Automatic quality ────────────────────────────────────────────────────

    def _open_quality_sheet(self) -> None:
        sheet = getattr(self, "quality_sheet", None)
        if sheet is not None:
            sheet.present(self)

    def _monitor_metrics(self) -> tuple[int, int, int]:
        """Pixel size and refresh rate of the display this PC will capture."""
        try:
            display = Gdk.Display.get_default()
            monitors = display.get_monitors() if display is not None else None
            monitor = monitors.get_item(0) if monitors is not None and monitors.get_n_items() else None
            if monitor is not None:
                area = monitor.get_geometry()
                scale = monitor.get_scale_factor() or 1
                # get_refresh_rate() is in milli-Hz.
                return area.width * scale, area.height * scale, round((monitor.get_refresh_rate() or 60000) / 1000)
        except Exception as exc:
            _log.debug(f"Cannot read monitor metrics: {exc}")
        return 1920, 1080, 60

    def _apply_auto_quality(self, force: bool = False) -> None:
        """Derive the picture settings from this machine, once per hardware change."""
        width, height, refresh = self._monitor_metrics()
        wireless = auto_quality.wireless_link()
        current_signature = auto_quality.signature(
            "sunshine-native-auto-v2",
            auto_quality.encoder_index(self.available_gpus),
            len(self.available_gpus),
            width,
            height,
            refresh,
            wireless,
        )
        settings = self.config.get("host", {})
        settings = settings if isinstance(settings, dict) else {}
        unchanged = settings.get("auto_signature") == current_signature
        if not force and (not self.auto_quality_row.get_active() or unchanged):
            self._auto_signature = settings.get("auto_signature", current_signature)
            self._sync_quality_controls()
            return

        defaults = auto_quality.host_defaults(gpus=self.available_gpus, refresh_hz=refresh, height=height, wireless=wireless)
        self._auto_signature = current_signature
        self.gpu_row.set_selected(next((i for i, gpu in enumerate(self.available_gpus) if gpu["encoder"] == "auto"), defaults["gpu_index"]))
        # Sunshine probes the actual encoder and codec capabilities. Let the
        # connecting PC request picture settings; a host display does not define
        # the guest's bandwidth ceiling or stream frame rate.
        # The video ceiling is an independent user limit, even in automatic mode.
        self.optimization_row.set_selected(1)
        self.codecs_row.set_active(True)
        self.wifi_row.set_active(defaults["wifi_mode"])
        self.platform_row.set_selected(0)  # Automatic: the session type decides.
        self._sync_quality_controls()
        self._schedule_save_host_settings()

    def _quality_summary(self) -> str:
        limit = self.bandwidth_row.get_value()
        cap = _("No video bitrate ceiling") if limit <= 0 else _("Video limit: {mbps:g} Mbps").format(mbps=limit)
        mode = _("Automatic capture and encoding") if self.auto_quality_row.get_active() else _("Manual capture and encoding")
        return _("{mode} · {limit}").format(mode=mode, limit=cap)

    @staticmethod
    def _choice_text(row) -> str:
        item = row.get_selected_item()
        return item.get_string() if item is not None else _("Automatic")

    def _sync_quality_controls(self) -> None:
        """Display configured values without claiming a live hardware measurement."""
        automatic = self.auto_quality_row.get_active()
        for row in (self.fps_row, self.gpu_row, self.platform_row, self.codecs_row, self.wifi_row, self.optimization_row):
            row.set_sensitive(not automatic)
        self.bandwidth_row.set_sensitive(True)
        self.redetect_row.set_visible(automatic)
        self.auto_quality_row.set_subtitle(_("Sunshine chooses compatible capture and encoding at startup. You can still set a video bitrate ceiling."))
        configured = [
            _("Screen: {value}").format(value=self._choice_text(self.monitor_row)),
            _("Graphics card: {value}").format(value=self._choice_text(self.gpu_row)),
            _("Capture: {value}").format(value=self._choice_text(self.platform_row)),
            _("Compression: HEVC/AV1 when supported") if self.codecs_row.get_active() else _("Compression: H.264 only"),
            _("Encoding priority: {value}").format(value=self._choice_text(self.optimization_row)),
            _("Error correction: {value}%").format(value=30 if self.wifi_row.get_active() else 20),
        ]
        self.auto_status_row.set_subtitle("\n".join(configured))
        self.quality_summary_row.set_subtitle(self._quality_summary())

    def _show_direct_internet_guide(self) -> None:
        from .connection_guides import build_direct_internet_dialog

        build_direct_internet_dialog().present(self)

    def _on_auto_quality_toggled(self, *_args) -> None:
        if getattr(self, "loading_settings", False):
            return
        if self.auto_quality_row.get_active():
            self._apply_auto_quality(force=True)
        else:
            self._sync_quality_controls()
            self._schedule_save_host_settings()

    def _get_sunshine_conf_path(self) -> Path:
        return paths.SUNSHINE_CONF

    def _get_sunshine_creds(self) -> tuple[str, str] | None:
        return load_sunshine_credentials(conf_path=self._get_sunshine_conf_path())

    def _save_sunshine_creds(self, user: str, password: str) -> bool:
        try:
            save_sunshine_credentials(user, password, conf_path=self._get_sunshine_conf_path())
            return True
        except SecretStoreUnavailable:
            self.show_toast(_("System keyring is unavailable. Password was not saved."))
        except Exception as e:
            _log.error(f"Error saving Sunshine credentials: {e}")
        return False

    def _ensure_sunshine_config(self) -> None:
        """Ensures sunshine.conf has required API settings"""
        try:
            ensure_sunshine_api_config(conf_path=self._get_sunshine_conf_path())
        except Exception as e:
            _log.error(f"Error ensuring sunshine config: {e}")

    def pair_with_entered_pin(self) -> None:
        """Finish pairing from the numbered step, without a dialog in the way.

        Only the cases that genuinely need more input — no stored Sunshine
        credentials, or the server rejecting them — fall back to the full form.
        """
        pin = self.pair_entry.get_text().strip()
        if len(pin) != MOONLIGHT_PAIRING_PIN_LENGTH or not pin.isascii() or not pin.isdigit():
            self.pair_entry.add_css_class("error")
            self.pair_entry.grab_focus()
            self.show_toast(_("Enter exactly four digits."))
            return
        self.pair_entry.remove_css_class("error")

        if getattr(self, "_pairing_busy", False):
            return
        self._pairing_busy = True
        self.guest_pair_button.set_sensitive(False)

        def finish(credentials, result, error):
            self._pairing_busy = False
            if getattr(self, "_closed", False):
                return False
            self.guest_pair_button.set_sensitive(self.is_hosting)
            if error:
                self.show_error_dialog(_("Pairing failed"), error)
            elif not credentials:
                self.open_pin_dialog(None, prefill_pin=pin)
            elif result.ok:
                self.pair_entry.set_text("")
                self.show_toast(_("The other PC is paired."))
                self._refresh_paired_devices()
            elif result.status == 401:
                self.show_toast(_("Sunshine rejected the saved password."))
                self.open_pin_dialog(None, prefill_pin=pin)
            elif result.status == 307:
                self.prompt_create_user(pin)
            else:
                self.show_error_dialog(_("Pairing failed"), result.message)
            return False

        def submit():
            try:
                self._ensure_sunshine_config()
                credentials = self._get_sunshine_creds()
                result = self.sunshine.send_pin(pin, name=_("Other computer"), auth=credentials) if credentials else None
                GLib.idle_add(finish, credentials, result, "")
            except Exception as exc:
                GLib.idle_add(finish, None, None, str(exc))

        threading.Thread(target=submit, daemon=True).start()

    def open_pin_dialog(self, _widget: Gtk.Widget | None, prefill_pin: str = "") -> None:
        self._ensure_sunshine_config()  # Ensure config before trying to use API

        # Load saved credentials from the system keyring when available.
        saved_creds = self._get_sunshine_creds()
        saved_user = saved_creds[0] if saved_creds else ""
        saved_pass = saved_creds[1] if saved_creds else ""

        dialog = Adw.AlertDialog(heading=_("Insert PIN"), body=_("Enter the PIN displayed by Moonlight on the other computer."))

        # Preferences group holding the fields
        grp = Adw.PreferencesGroup()

        # Named for whose PIN it is: the guest page has a field with the same
        # four digits meaning the opposite direction.
        pin_row = Adw.EntryRow(title=_("PIN shown by Moonlight"))
        pin_row.set_text(prefill_pin)
        pin_row.set_input_purpose(Gtk.InputPurpose.DIGITS)
        pin_row.update_property(
            [Gtk.AccessibleProperty.DESCRIPTION],
            [_("Enter the four digits shown by Moonlight on the other computer.")],
        )

        name_row = Adw.EntryRow(title=_("Device Name"))
        name_row.set_text(socket.gethostname())

        user_row = Adw.EntryRow(title=_("Sunshine User"))
        if saved_user:
            user_row.set_text(saved_user)

        pass_row = Adw.PasswordEntryRow(title=_("Sunshine Password"))
        if saved_pass:
            pass_row.set_text(saved_pass)

        save_chk = Adw.SwitchRow(title=_("Save Password"))
        save_chk.set_subtitle(_("Save credentials to the system keyring"))
        save_chk.set_active(bool(saved_user and saved_pass))

        grp.add(pin_row)
        grp.add(name_row)
        grp.add(user_row)
        grp.add(pass_row)
        grp.add(save_chk)

        dialog.set_extra_child(grp)

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("ok", _("Send"))
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response("cancel")

        def on_response(d, r):
            if r == "ok":
                pin = pin_row.get_text().strip()
                device_name = name_row.get_text().strip()
                u = user_row.get_text().strip()
                p = pass_row.get_text().strip()
                save = save_chk.get_active()

                if len(pin) != MOONLIGHT_PAIRING_PIN_LENGTH or not pin.isascii() or not pin.isdigit():
                    self.show_error_dialog(_("Invalid PIN"), _("Enter exactly four digits."))
                    pin_row.grab_focus()
                    return

                # Update saved credentials if requested
                if save and u and p:
                    self._save_sunshine_creds(u, p)

                auth = (u, p) if (u and p) else None
                result = self.sunshine.send_pin(pin, name=device_name, auth=auth)

                if result.ok:
                    self.show_toast(_("PIN sent successfully"))
                    self._refresh_paired_devices()
                elif result.status == 401:
                    self.show_error_dialog(_("Authentication Failed"), _("Invalid username or password."))
                elif result.status == 307:
                    # 307 Redirect: no admin user has been created yet.
                    self.prompt_create_user(pin)
                else:
                    self.show_error_dialog(_("PIN Error"), result.message)

        dialog.connect("response", on_response)
        dialog.present(self)

    def prompt_create_user(self, _pin_retry):
        dialog = Adw.AlertDialog(heading=_("User Not Found"), body=_("No Sunshine user exists. Configure one in the browser."))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("open", _("Open Configuration"))
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.set_response_appearance("open", Adw.ResponseAppearance.SUGGESTED)

        def on_resp(d, r):
            if r == "open":
                open_uri(self, self.sunshine.web_ui_url)

        dialog.connect("response", on_resp)
        dialog.present(self)

    def _create_paired_devices_overview(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        group.set_title(_("Paired devices"))
        self.paired_devices_overview_group = group
        self._paired_devices_overview_rows: list[Gtk.Widget] = []
        self._set_paired_devices_overview_message(
            _("Start sharing to show paired devices"),
            _("Paired devices appear here."),
        )
        return group

    def _clear_paired_devices_overview(self) -> None:
        group = getattr(self, "paired_devices_overview_group", None)
        if group is None:
            return
        for row in getattr(self, "_paired_devices_overview_rows", []):
            group.remove(row)
        self._paired_devices_overview_rows = []

    def _add_paired_devices_overview_row(self, row: Gtk.Widget) -> None:
        group = getattr(self, "paired_devices_overview_group", None)
        if group is None:
            return
        group.add(row)
        self._paired_devices_overview_rows.append(row)

    def _set_paired_devices_overview_message(self, title: str, subtitle: str) -> bool:
        self._clear_paired_devices_overview()
        row = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False)
        set_row_icon(row, "brp-client-symbolic")
        self._add_paired_devices_overview_row(row)
        return False

    def _add_manage_paired_devices_row(self) -> None:
        row = Adw.ActionRow(
            title=_("Manage paired devices"),
            subtitle=_("Disable or remove devices that should no longer connect."),
        )
        set_row_icon(row, "brp-preferences-symbolic")

        manage_button = Gtk.Button(label=_("Manage"))
        manage_button.set_valign(Gtk.Align.CENTER)
        manage_button.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            [_("Manage paired devices"), _("Open paired device management")],
        )
        manage_button.connect("clicked", self.open_paired_devices_dialog)
        row.add_suffix(manage_button)
        row.set_activatable_widget(manage_button)
        self._add_paired_devices_overview_row(row)

    def _populate_paired_devices_overview(self, clients: list[dict[str, object]]) -> bool:
        self._clear_paired_devices_overview()

        if not clients:
            row = Adw.ActionRow(
                title=_("No paired devices yet"),
                subtitle=_("After step 2, the other PC appears here."),
            )
            set_row_icon(row, "brp-client-symbolic")
            self._add_paired_devices_overview_row(row)
            return False

        for client in clients[:3]:
            name = str(client.get("name") or _("Unknown device"))
            enabled = bool(client.get("enabled", True))
            row = Adw.ActionRow(title=name, subtitle=_("Can connect without a new PIN") if enabled else _("Blocked until enabled again"))
            set_row_icon(row, "brp-computer-symbolic")

            status = Gtk.Label(label=_("Allowed") if enabled else _("Blocked"))
            status.add_css_class("caption")
            status.add_css_class("heading")
            status.add_css_class("success" if enabled else "error")
            status.set_valign(Gtk.Align.CENTER)
            row.add_suffix(status)
            self._add_paired_devices_overview_row(row)

        remaining = len(clients) - 3
        if remaining > 0:
            row = Adw.ActionRow(
                title=_("{} more paired devices").format(remaining),
                subtitle=_("Open management to view all paired devices."),
            )
            set_row_icon(row, "view-more-symbolic")
            self._add_paired_devices_overview_row(row)

        self._add_manage_paired_devices_row()
        return False

    def create_summary_box(self):
        self.summary_box = Adw.PreferencesGroup()
        self.summary_box.add_css_class("brp-rounded-group")
        self.summary_box.set_visible(True)
        self.diagnostics_expander = Adw.ExpanderRow()
        self.diagnostics_expander.set_title(_("Connection information"))
        self.diagnostics_expander.set_subtitle(_("Advanced: IP addresses for manual connection when automatic discovery fails."))
        self.diagnostics_expander.set_expanded(False)
        self.summary_box.add(self.diagnostics_expander)
        self.field_widgets = {}
        # Local LAN addresses are low-sensitivity and need sharing to connect, so
        # they show in clear; global (public) addresses stay masked behind the eye.
        for l, k, i, r in [
            ("Host", "hostname", "brp-computer-symbolic", True),
            ("IPv4", "ipv4", "brp-address-symbolic", True),
            ("IPv6", "ipv6", "brp-address-symbolic", True),
            ("IPv4 Global", "ipv4_global", "brp-network-transmit-receive-symbolic", False),
            ("IPv6 Global", "ipv6_global", "brp-network-transmit-receive-symbolic", False),
        ]:
            self.create_masked_row(l, k, i, r)
        self.diagnostics_expander.connect(
            "notify::expanded",
            lambda row, _pspec: self.populate_summary_fields() if row.get_expanded() else None,
        )

    def _selected_audio_sink(self) -> str:
        names = getattr(self, "_audio_choice_names", [""])
        index = self.audio_output_row.get_selected()
        return names[index] if 0 <= index < len(names) else ""

    def _sync_audio_controls(self) -> None:
        explicit = bool(self._selected_audio_sink())
        self.audio_mode_row.set_sensitive(explicit)
        self.audio_mode_row.set_visible(explicit)
        if hasattr(self, "audio_mixer_link"):
            self.audio_mixer_link.set_visible(explicit)
            self.audio_mixer_link.set_sensitive(self.is_hosting and self._audio_routing_active)

    def on_audio_mode_changed(self, row, _param):
        if self.loading_settings:
            return
        self._sync_audio_controls()
        if self.is_hosting:
            self.show_toast(_("Audio changes apply the next time you start sharing. The current output is unchanged."))
        self._schedule_save_host_settings()

    def load_audio_outputs(self):
        from big_remote_play.utils.audio import AudioManager

        if not hasattr(self, "audio_manager"):
            self.audio_manager = AudioManager()
        was_loading = self.loading_settings
        self.loading_settings = True
        try:
            self.audio_devices = self.audio_manager.get_passive_sinks()
            settings = self.config.get("host", {})
            desired = settings.get("audio_output_name", "") if isinstance(settings, dict) else ""
            if not isinstance(desired, str):
                desired = ""
            self._audio_choice_names = [""] + [dev["name"] for dev in self.audio_devices]
            labels = [_("Keep the current system output (recommended)")] + [dev.get("description") or dev["name"] for dev in self.audio_devices]
            if desired and desired not in self._audio_choice_names:
                # Never silently redirect to the first remaining device.
                self._audio_choice_names.append(desired)
                labels.append(_("Unavailable: {device}").format(device=desired))
            self.audio_output_row.set_model(Gtk.StringList.new(labels))
            self.audio_output_row.set_selected(self._audio_choice_names.index(desired))
            self._sync_audio_controls()
        finally:
            self.loading_settings = was_loading

    def on_audio_output_changed(self, row, _param):
        if self.loading_settings:
            return
        self._sync_audio_controls()
        if self.is_hosting:
            self.show_toast(_("Audio changes apply the next time you start sharing. The current output is unchanged."))
        self._schedule_save_host_settings()

    def on_configure_firewall_clicked(self, _widget):
        self.show_toast(_("Configuring firewall... (Password may be requested)"))

        try:
            # Resolve the bundled script (installed /usr/share path, dev fallback)
            script_path = paths.script_path("configure_firewall.sh")

            if not os.path.exists(script_path):
                self.show_error_dialog(_("Error"), f"Script not found: {script_path}")
                return

            # Run with pkexec
            cmd = ["pkexec", script_path, str(self.sunshine.api_port - 1)]

            def on_done(ok, out):
                if ok:
                    self.show_toast(_("Success: {}").format(out.strip()))
                else:
                    self.show_error_dialog(_("Firewall Error"), out if out else _("Execution failed or cancelled."))

            def run():
                try:
                    # No timeout: pkexec blocks on the polkit auth dialog (user-paced)
                    res = subprocess.run(cmd, capture_output=True, text=True)
                    GLib.idle_add(on_done, res.returncode == 0, res.stdout + res.stderr)
                except Exception as e:
                    GLib.idle_add(on_done, False, str(e))

            threading.Thread(target=run, daemon=True).start()

        except Exception as e:
            self.show_toast(_("Error executing script: {}").format(e))

    def create_masked_row(self, title: str, key: str, icon_name: str = "brp-text-x-generic-symbolic", default_revealed: bool = False) -> None:
        row = Adw.ActionRow()
        row.set_title(title)
        row.add_prefix(create_icon_widget(icon_name, size=16))

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_valign(Gtk.Align.CENTER)

        value_lbl = Gtk.Label(label="••••••" if not default_revealed else "")
        value_lbl.set_margin_end(8)
        value_lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        value_lbl.set_max_width_chars(18)
        value_lbl.set_width_chars(1)

        eye_btn = Gtk.Button()
        eye_btn.set_child(create_icon_widget("brp-view-reveal-symbolic" if not default_revealed else "brp-view-conceal-symbolic", size=16))
        eye_btn.add_css_class("flat")
        name_icon_button(
            eye_btn,
            _("Hide {}").format(title) if default_revealed else _("Reveal {}").format(title),
            _("Show or hide the {} value").format(title),
        )
        copy_btn = Gtk.Button()
        copy_btn.set_child(create_icon_widget("brp-edit-copy-symbolic", size=16))
        copy_btn.add_css_class("flat")
        name_icon_button(
            copy_btn,
            _("Copy {}").format(title),
            _("Copy the {} value to clipboard").format(title),
        )

        box.append(value_lbl)
        box.append(eye_btn)
        box.append(copy_btn)
        row.add_suffix(box)
        self.diagnostics_expander.add_row(row)

        self.field_widgets[key] = {"label": value_lbl, "real_value": "", "revealed": default_revealed, "btn_eye": eye_btn, "title": title}
        eye_btn.connect("clicked", lambda b: self.toggle_field_visibility(key))
        copy_btn.connect("clicked", lambda b: self.copy_field_value(key))

    def toggle_field_visibility(self, key: str) -> None:
        field = self.field_widgets[key]
        field["revealed"] = not field["revealed"]
        title = field.get("title", _("value"))
        field["btn_eye"].set_child(create_icon_widget("brp-view-conceal-symbolic" if field["revealed"] else "brp-view-reveal-symbolic", size=16))
        action_label = _("Hide {}").format(title) if field["revealed"] else _("Reveal {}").format(title)
        field["btn_eye"].set_tooltip_text(action_label)
        field["btn_eye"].update_property([Gtk.AccessibleProperty.LABEL], [action_label])
        field["label"].set_text(field["real_value"] if field["revealed"] else "••••••")

    def copy_field_value(self, key):
        if val := self.field_widgets[key]["real_value"]:
            display = Gdk.Display.get_default()
            if display is not None:
                display.get_clipboard().set(val)
            self.show_toast(_("Copied!"))

    def toggle_hosting(self, button: Gtk.Widget) -> None:
        self.show_toast(_("Stop sharing") if self.is_hosting else _("Starting game sharing..."))
        if hasattr(self, "overview_start_button"):
            self.overview_start_button.set_sensitive(False)

        # Defer action slightly to allow UI to paint
        GLib.timeout_add(100, self._perform_toggle_hosting)

    def _perform_toggle_hosting(self) -> bool:
        if self.is_hosting:
            self.stop_hosting()
        else:
            self.start_hosting()
        return False

    def _describe_session(self) -> str:
        """What this PC is sending right now, in one line."""
        source = self.game_mode_row.get_subtitle() or self._selected_source_name()
        return _("{source} · {quality}").format(source=source, quality=self._quality_summary())

    def _selected_source_name(self) -> str:
        item = self.game_mode_row.get_selected_item()
        get_string = getattr(item, "get_string", None)
        return str(get_string()) if callable(get_string) else _("Full Desktop")

    def sync_ui_state(self) -> None:
        self.perf_monitor.set_visible(self.is_hosting)
        self.start_heading.set_visible(not self.is_hosting)
        self.guest_access_group.set_visible(self.is_hosting)
        self.paired_devices_overview_group.set_visible(self.is_hosting)
        # Running: state, not a form. Stopped: the choices that start it.
        for widget in (self.game_group, self.quality_summary_box):
            widget.set_visible(not self.is_hosting)
        self.session_summary_box.set_visible(self.is_hosting)
        if self.is_hosting:
            self.session_summary_row.set_subtitle(self._describe_session())
            self.perf_monitor.set_connection_status("Sunshine", _("Active - Waiting for Connections"), True)
            self.perf_monitor.start_monitoring()

            # Button State: Hosting -> Stop
            if hasattr(self, "overview_start_button"):
                self.overview_start_label.set_label(_("Stop sharing"))
                set_icon(self.overview_start_icon, "media-playback-stop-symbolic")
                self.overview_start_button.set_tooltip_text(_("Stop sharing"))
                self.overview_start_button.remove_css_class("suggested-action")
                self.overview_start_button.add_css_class("destructive-action")
                self.overview_start_button.set_sensitive(True)
                self.overview_start_button.update_property(
                    [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
                    [_("Stop sharing"), _("Stop sharing the game from this PC")],
                )
            if hasattr(self, "overview_status_label"):
                self.overview_status_label.set_label(_("Active - Waiting for Connections"))
            if hasattr(self, "overview_state_label"):
                self.overview_state_label.set_label(_("Running"))
                self.overview_state_label.remove_css_class("offline")
                self.overview_state_label.add_css_class("online")
            if hasattr(self, "guest_pair_button"):
                self.guest_pair_button.set_sensitive(True)

            for r in [self.game_mode_row, self.hardware_group, self.streaming_group, self.advanced_group]:
                r.set_sensitive(False)

            if hasattr(self, "summary_box"):
                self.summary_box.set_visible(True)
                self.populate_summary_fields()

            self._refresh_paired_devices()
        else:
            self.perf_monitor.set_connection_status("Sunshine", _("Inactive"), False)
            self.perf_monitor.stop_monitoring()
            self.pin_code = None
            if hasattr(self, "overview_status_label"):
                self.overview_status_label.set_label(_("Choose a source, then start sharing."))
            if hasattr(self, "overview_state_label"):
                self.overview_state_label.set_label(_("Stopped"))
                self.overview_state_label.remove_css_class("online")
                self.overview_state_label.add_css_class("offline")

            self._hosting_started_at = None
            uptime_timer_id = self._uptime_timer_id
            if uptime_timer_id is not None:
                GLib.source_remove(uptime_timer_id)
                self._uptime_timer_id = None

            if hasattr(self, "field_widgets") and "pin" in self.field_widgets:
                self.field_widgets["pin"]["real_value"] = ""
                self.pin_display_label.set_text("—" * MOONLIGHT_PAIRING_PIN_LENGTH)
            if hasattr(self, "summary_box"):
                self.summary_box.set_visible(True)
                self.populate_summary_fields()

            # Button State: Stopped -> Start
            if hasattr(self, "overview_start_button"):
                self.overview_start_label.set_label(_("Start sharing"))
                set_icon(self.overview_start_icon, "media-playback-start-symbolic")
                self.overview_start_button.set_tooltip_text(_("Start sharing"))
                self.overview_start_button.remove_css_class("destructive-action")
                self.overview_start_button.add_css_class("suggested-action")
                self.overview_start_button.set_sensitive(True)
                self.overview_start_button.update_property(
                    [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
                    [_("Start sharing"), _("Start sharing the game from this PC")],
                )
            if hasattr(self, "guest_pair_button"):
                self.guest_pair_button.set_sensitive(False)

            for r in [self.game_mode_row, self.hardware_group, self.streaming_group, self.advanced_group]:
                r.set_sensitive(True)
            self._set_paired_devices_overview_message(
                _("Start sharing to show paired devices"),
                _("Paired devices appear here."),
            )

    def populate_summary_fields(self):
        import socket, threading
        from big_remote_play.utils.network import NetworkDiscovery

        self.update_field("hostname", socket.gethostname())
        if self.pin_code:
            self.update_field("pin", self.pin_code)
        ipv4, ipv6 = self.get_ip_addresses()
        self.update_field("ipv4", ipv4)
        self.update_field("ipv6", ipv6)

        # Public-IP services are diagnostic tools, not a startup dependency.
        # Fetch only when that disclosure is opened, with one worker at a time.
        if not self.diagnostics_expander.get_expanded() or self._fetching_global_ips:
            return
        self._fetching_global_ips = True

        def finish(g_ipv4, g_ipv6):
            self._fetching_global_ips = False
            self.update_field("ipv4_global", g_ipv4)
            self.update_field("ipv6_global", g_ipv6)
            return False

        def fetch_globals():
            g_ipv4, g_ipv6 = "", ""
            try:
                net = NetworkDiscovery()
                g_ipv4 = net.get_global_ipv4()
                g_ipv6 = net.get_global_ipv6()
                if g_ipv6 and ":" in g_ipv6 and not g_ipv6.startswith("["):
                    g_ipv6 = f"[{g_ipv6}]"
            finally:
                GLib.idle_add(finish, g_ipv4, g_ipv6)

        threading.Thread(target=fetch_globals, daemon=True).start()

    def update_field(self, key, value):
        if key in self.field_widgets:
            # Empty/"None" means the value could not be determined — show it as such.
            if not value or value == "None":
                value = _("Unavailable")
            self.field_widgets[key]["real_value"] = value
            if self.field_widgets[key]["revealed"]:
                self.field_widgets[key]["label"].set_text(value)

    def start_audio_mixer_refresh(self):
        self.stop_audio_mixer_refresh()
        self.private_audio_apps = set()  # Track names of private apps (unchecked in UI)
        self.mixer_source_id = GLib.timeout_add(2000, self._refresh_audio_mixer_ui)
        self.enforcer_source_id = GLib.timeout_add(1000, self._run_audio_enforcer)
        self._refresh_audio_mixer_ui()
        return True

    def stop_audio_mixer_refresh(self):
        if hasattr(self, "mixer_source_id"):
            GLib.source_remove(self.mixer_source_id)
            del self.mixer_source_id
        if hasattr(self, "enforcer_source_id"):
            GLib.source_remove(self.enforcer_source_id)
            del self.enforcer_source_id

    def _run_audio_enforcer(self):
        if not self.is_hosting:
            return True
        if not hasattr(self, "active_host_sink") or not self.active_host_sink:
            return True
        if not hasattr(self, "audio_manager") or not self._audio_routing_active:
            return True
        # A prior enforcer pass is still running its pactl calls; skip this tick.
        if getattr(self, "_enforcer_busy", False):
            return True

        shared_sink = "SunshineGameSink"
        private_sink = self.active_host_sink

        # GTK / cross-object reads happen on the main thread; snapshots are passed
        # to the worker so the pactl calls never block the UI.
        # 0: Keep local playback, 1: Guest, 2: Host, 3: Guest + Host
        mode_idx = self._active_audio_mode
        private_apps = set(self.private_audio_apps)

        streaming_enabled = mode_idx in [0, 1, 3]
        should_monitor = False
        if mode_idx == 3:  # Guest + Host
            should_monitor = True
        elif mode_idx == 2:  # Host Only — everything moves to the private sink
            should_monitor = True
            streaming_enabled = False
        elif mode_idx == 1:  # Guest Only
            should_monitor = False
        elif mode_idx == 0:  # Explicit routing keeps local playback unless asked otherwise.
            should_monitor = True

        self._enforcer_busy = True

        def work():
            try:
                if not self._audio_routing_active or self._closed:
                    return
                if not hasattr(self, "_last_monitor_state") or self._last_monitor_state != should_monitor:
                    self.audio_manager.set_host_monitoring(private_sink, should_monitor)
                    self._last_monitor_state = should_monitor

                for app in self.audio_manager.get_apps():
                    if not self._audio_routing_active or self._closed:
                        break
                    app_id, name = app["id"], app.get("name", "")
                    if "sunshine" in name.lower() or "loopback" in name.lower() or "moonlight" in name.lower():
                        continue
                    target = private_sink if (not streaming_enabled or name in private_apps) else shared_sink
                    if app.get("sink_name", "") != target:
                        self.audio_manager.move_app(app_id, target)
            except Exception as e:
                _log.error(f"Enforcer Error: {e}")
            finally:
                self._enforcer_busy = False

        threading.Thread(target=work, daemon=True).start()
        return True

    def _refresh_audio_mixer_ui(self):
        if not self.audio_mixer_link.get_sensitive():
            return True
        if not hasattr(self, "audio_manager"):
            return True
        if getattr(self, "_mixer_busy", False):
            return True

        self._mixer_busy = True

        def work():
            try:
                apps = self.audio_manager.get_apps()
            except Exception:
                apps = []
            GLib.idle_add(self._apply_mixer_apps, apps)

        threading.Thread(target=work, daemon=True).start()
        return True

    def _apply_mixer_apps(self, apps) -> bool:
        self._mixer_busy = False
        self.mixer_empty_row.set_visible(not apps)
        seen_ids = set()

        if not hasattr(self, "mixer_rows"):
            self.mixer_rows: dict[str, Adw.SwitchRow] = {}

        for app in apps:
            app_id = app["id"]
            app_name = app.get("name", "App")
            seen_ids.add(app_id)

            # Default state: Active (Shared) unless explicitly set to Private
            is_shared = app_name not in self.private_audio_apps

            if app_id in self.mixer_rows:
                row = self.mixer_rows[app_id]
                # Avoid signal loop
                if row.get_active() != is_shared:
                    row.disconnect_by_func(self._on_app_toggled)
                    row.set_active(is_shared)
                    row.connect("notify::active", self._on_app_toggled, app_name)

                row.set_subtitle(_("Both computers") if is_shared else _("This computer only"))
            else:
                row = Adw.SwitchRow(use_markup=False)
                row.set_title(app_name)
                row.set_subtitle(_("Both computers") if is_shared else _("This computer only"))
                if app.get("icon"):
                    set_row_icon(row, app["icon"])
                row.set_active(is_shared)
                row.connect("notify::active", self._on_app_toggled, app_name)
                self.audio_mixer_group.add(row)
                self.mixer_rows[app_id] = row

        # Cleanup
        to_remove = [aid for aid in self.mixer_rows if aid not in seen_ids]
        for aid in to_remove:
            self.audio_mixer_group.remove(self.mixer_rows[aid])
            del self.mixer_rows[aid]

        return False

    def _on_app_toggled(self, row, _param, app_name):
        is_shared = row.get_active()
        if is_shared:
            if app_name in self.private_audio_apps:
                self.private_audio_apps.remove(app_name)
        else:
            self.private_audio_apps.add(app_name)

        row.set_subtitle(_("Both computers") if is_shared else _("This computer only"))
        self._run_audio_enforcer()

    def start_hosting(self, b=None):
        self.loading_bar.set_visible(True)
        self.loading_bar.pulse()
        # Read all GTK-bound values on the main thread, then run the blocking
        # stop/audio/start sequence (~4s of subprocess + sleeps) off it.
        try:
            cfg = self._collect_hosting_config()
        except Exception as e:
            self._on_hosting_error(str(e))
            return
        threading.Thread(target=self._run_start_hosting, args=(cfg,), daemon=True).start()

    def _resolve_game_launch_info(self) -> dict | None:
        """Read the selected game/app into a launch descriptor, or None (Desktop)."""
        mode_idx = self.game_mode_row.get_selected()
        if mode_idx in (1, 2):  # Steam / Lutris
            platform = "Steam" if mode_idx == 1 else "Lutris"
            idx = self.game_list_row.get_selected()
            games = self.detected_games.get(platform, [])
            if idx != Gtk.INVALID_LIST_POSITION and 0 <= idx < len(games):
                game = games[idx]
                if mode_idx == 1:
                    return {"type": "steam", "app_id": game.get("id", ""), "name": game["name"]}
                return {"type": "lutris", "cmd": game["cmd"], "name": game["name"]}
        elif mode_idx == 3:  # Custom App
            name, cmd = self.custom_name_entry.get_text().strip(), self.custom_cmd_entry.get_text().strip()
            if name and cmd:
                return {"type": "custom", "cmd": cmd, "name": name}
        return None

    def _encoding_settings(self) -> dict:
        """One mapping for saved settings and the worker's startup snapshot."""
        codecs = "0" if self.codecs_row.get_active() else "1"
        presets = {
            0: {"nvenc_preset": "1", "amd_quality": "speed", "sw_preset": "ultrafast"},
            1: {"nvenc_preset": "4", "amd_quality": "balanced", "sw_preset": "veryfast"},
            2: {"nvenc_preset": "7", "amd_quality": "quality", "sw_preset": "medium"},
        }
        return {
            "hevc_mode": codecs,
            "av1_mode": codecs,
            "fec_percentage": "30" if self.wifi_row.get_active() else "20",
            "nvenc_twopass": "quarter_res",
            **presets.get(self.optimization_row.get_selected(), presets[1]),
        }

    def _build_sunshine_config(self) -> dict:
        """Assemble the sunshine.conf mapping from the current widget state."""
        bw_mbps = self.bandwidth_row.get_value()
        index = self.gpu_row.get_selected()
        gpu = self.available_gpus[index] if 0 <= index < len(self.available_gpus) else {"encoder": "auto", "adapter": "auto"}
        config = {
            **self._encoding_settings(),
            "encoder": "" if gpu["encoder"] == "auto" else gpu["encoder"],
            "max_bitrate": int(bw_mbps * 1000),
            "upnp": "enabled" if self.upnp_row.get_active() else "disabled",
            "address_family": "both" if self.ipv6_row.get_active() else "ipv4",
            "origin_web_ui_allowed": "wan" if self.webui_anyone_row.get_active() else "lan",
        }
        index = self.platform_row.get_selected()
        platform = self._capture_values[index] if 0 <= index < len(self._capture_values) else ""
        config["capture"] = platform or None

        config["output_name"] = None
        monitor_idx = self.monitor_row.get_selected()
        if 0 < monitor_idx < len(self.available_monitors):
            mon_name = self.available_monitors[monitor_idx][1]
            if mon_name != "auto":
                config["output_name"] = mon_name

        config["adapter_name"] = gpu["adapter"] if gpu["encoder"] == "vaapi" and gpu["adapter"] != "auto" else None
        return config

    def _collect_hosting_config(self) -> dict:
        """Main-thread snapshot of every widget value the start sequence needs."""
        self.pin_code = "".join(random.choices(string.digits, k=BRP_DISCOVERY_CODE_LENGTH))
        self._game_launch_info = self._resolve_game_launch_info()
        if self.game_mode_row.get_selected() != 0 and self._game_launch_info is None:
            raise ValueError(_("Select a game or complete the app name and command. To share the whole screen instead, choose Full Desktop."))
        if self._game_launch_info and self._game_launch_info["type"] in ("custom", "lutris") and not _split_launch_command(self._game_launch_info["cmd"]):
            raise ValueError(_("The app command is not valid. Check its quotation marks and try again."))
        self._game_processes: list[subprocess.Popen[bytes]] = []
        return {
            "sunshine_config": self._build_sunshine_config(),
            "pin_code": self.pin_code,
            "audio_output_name": self._selected_audio_sink(),
            "audio_devices": list(getattr(self, "audio_devices", [])),
            "audio_mode": self.audio_mode_row.get_selected() if self._selected_audio_sink() else 0,
            "guest_only": bool(self._selected_audio_sink()) and self.audio_mode_row.get_selected() == 1,
        }

    def _run_start_hosting(self, cfg: dict) -> None:
        """Worker: stop any running server, set up audio, configure and start."""
        sunshine_config = cfg["sunshine_config"]
        try:
            if self._closed:
                return
            if self.sunshine.is_running():
                self.sunshine.stop()
                time.sleep(1)

            from big_remote_play.utils.network import NetworkDiscovery

            self.stop_pin_listener = NetworkDiscovery().start_pin_listener(cfg["pin_code"], socket.gethostname())

            # Always Desktop in apps.json — games are launched directly.
            if not self.sunshine.ensure_desktop_app():
                raise RuntimeError(_("Could not update the game library. Existing games were preserved."))

            host_sink = cfg.get("audio_output_name", "")
            mode = cfg.get("audio_mode", 0)
            guest_only = bool(host_sink) and mode == 1
            audio_ok = False
            self.active_host_sink = ""
            self._audio_routing_active = False
            self._active_audio_mode = mode
            sunshine_config["stream_audio"] = "disabled" if host_sink and mode == 2 else "enabled"
            sunshine_config["audio_sink"] = None
            sunshine_config["virtual_sink"] = None
            if host_sink:
                available = {dev["name"] for dev in cfg["audio_devices"]}
                if host_sink not in available:
                    raise RuntimeError(_("The selected audio output is unavailable. Choose the current system output or reconnect the device."))
                if mode != 2:
                    self.active_host_sink = host_sink
                    if not self.audio_manager.enable_streaming_audio(host_sink, guest_only=guest_only):
                        raise RuntimeError(_("Could not set up the selected audio routing. Choose the current system output to share without changing devices."))
                    sunshine_config["audio_sink"] = "SunshineGameSink"
                    self._audio_routing_active = True
                    audio_ok = True
            # Default mode makes no pactl writes, creates no virtual sink and
            # never moves applications. Sunshine captures the system output.

            if self._closed:
                self._rollback_start()
                return
            if not self.sunshine.configure(sunshine_config):
                raise OSError(_("Could not save the server configuration."))
            success, msg = self.sunshine.start()
            if self._closed:
                if success:
                    self.sunshine.stop()
                self._rollback_start()
                return
            if not success:
                self._rollback_start()
            GLib.idle_add(
                self._on_hosting_started,
                {"success": success, "msg": msg, "host_sink": host_sink, "guest_only": guest_only, "audio_ok": audio_ok},
            )
        except Exception as e:
            self._rollback_start()
            GLib.idle_add(self._on_hosting_error, str(e))

    def _rollback_start(self) -> None:
        """Undo resources acquired by a failed start, on the start worker."""
        listener, self.stop_pin_listener = self.stop_pin_listener, None
        if callable(listener):
            listener()
        if self._audio_routing_active:
            try:
                self.audio_manager.disable_streaming_audio(None)
            except Exception as exc:
                _log.warning("Could not restore audio after failed start: %s", exc)
            self._audio_routing_active = False
            self.active_host_sink = ""

    def _on_hosting_started(self, result: dict) -> bool:
        if self._closed:
            return False
        self.loading_bar.set_visible(False)
        if hasattr(self, "overview_start_button"):
            self.overview_start_button.set_sensitive(True)

        if not result["success"]:
            self.is_hosting = False
            self.sync_ui_state()
            self.show_start_error_dialog(result["msg"])
            return False

        self.is_hosting = True
        if result["audio_ok"]:
            self._last_monitor_state = not result["guest_only"]
            self.start_audio_mixer_refresh()
        self._sync_audio_controls()

        self.sync_ui_state()
        self.show_toast(_("Server started"))
        self._launch_game_direct()
        return False

    def _on_hosting_error(self, message: str) -> bool:
        if self._closed:
            return False
        self.loading_bar.set_visible(False)
        if hasattr(self, "overview_start_button"):
            self.overview_start_button.set_sensitive(True)
        self.show_error_dialog(_("Error"), message)
        self.is_hosting = False
        self.sync_ui_state()
        return False

    def _launch_game_direct(self):
        """Directly launch game/platform via subprocess - radical approach"""
        info = getattr(self, "_game_launch_info", None)
        if not info:
            _log.debug("Game Mode: Desktop (no game to launch)")
            return

        if not hasattr(self, "_game_processes"):
            self._game_processes = []

        env = os.environ.copy()

        try:
            if info["type"] == "steam":
                app_id = info["app_id"]
                game_name = info["name"]
                _log.debug(f"DIRECT LAUNCH: Steam Big Picture + {game_name} (ID: {app_id})")

                # 1. Open Steam Big Picture Mode
                p1 = subprocess.Popen(["steam", "steam://open/bigpicture"], env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._game_processes.append(p1)
                self.show_toast(_("Opening Steam Big Picture..."))

                # 2. Launch the game after a delay (give Big Picture time to open)
                def _delayed_game_launch():
                    import time

                    time.sleep(4)
                    try:
                        p2 = subprocess.Popen(["steam", f"steam://rungameid/{app_id}"], env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        self._game_processes.append(p2)
                        GLib.idle_add(self.show_toast, _("Launching {}...").format(game_name))
                        _log.debug(f"DIRECT LAUNCH: Game {game_name} launched (PID: {p2.pid})")
                    except Exception as e:
                        _log.error(f"Error launching game: {e}")
                        GLib.idle_add(self.show_toast, _("Error launching game: {}").format(e))

                threading.Thread(target=_delayed_game_launch, daemon=True).start()

            elif info["type"] == "lutris":
                cmd = info["cmd"]
                game_name = info["name"]
                _log.debug(f"DIRECT LAUNCH: Lutris - {game_name} ({cmd})")
                argv = _split_launch_command(cmd)
                if not argv:
                    self.show_toast(_("Invalid launch command"))
                    return

                p = subprocess.Popen(argv, env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._game_processes.append(p)
                self.show_toast(_("Launching {}...").format(game_name))

            elif info["type"] == "custom":
                cmd = info["cmd"]
                game_name = info["name"]
                _log.debug(f"DIRECT LAUNCH: Custom - {game_name} ({cmd})")
                argv = _split_launch_command(cmd)
                if not argv:
                    self.show_toast(_("Invalid launch command"))
                    return

                p = subprocess.Popen(argv, env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._game_processes.append(p)
                self.show_toast(_("Launching {}...").format(game_name))

        except Exception as e:
            _log.error(f"Error in _launch_game_direct: {e}")
            self.show_toast(_("Error launching game: {}").format(e))

    def _stop_game_direct(self):
        """Kill any directly launched game processes"""
        info = getattr(self, "_game_launch_info", None)

        # Close Steam Big Picture if we opened it
        if info and info.get("type") == "steam":
            try:
                subprocess.Popen(["steam", "steam://close/bigpicture"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                _log.debug("Closing Steam Big Picture")
            except Exception:
                pass

        # Kill tracked processes
        for p in getattr(self, "_game_processes", []):
            try:
                if p.poll() is None:  # Still running
                    import signal

                    os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            except Exception:
                pass

        self._game_processes = []
        self._game_launch_info = None

    def stop_hosting(self, b=None) -> None:
        self.show_toast(_("Stopping server..."))
        self.loading_bar.set_visible(True)
        self.loading_bar.pulse()

        # Main-thread teardown: kill launched games, stop the GLib timers, drop
        # the PIN listener. The blocking audio-restore + server stop go to a worker.
        self._stop_game_direct()
        self.audio_mixer_link.set_sensitive(False)
        self.stop_audio_mixer_refresh()

        if hasattr(self, "stop_pin_listener") and self.stop_pin_listener:
            try:
                self.stop_pin_listener()
            except Exception:
                pass
            self.stop_pin_listener = None

        host_sink = getattr(self, "active_host_sink", "") if hasattr(self, "audio_manager") else ""
        threading.Thread(target=self._run_stop_hosting, args=(host_sink,), daemon=True).start()

    def _run_stop_hosting(self, host_sink: str) -> None:
        routing_owned = self._audio_routing_active
        self._audio_routing_active = False
        try:
            self.sunshine.stop()
        except Exception as exc:
            _log.error("Error stopping Sunshine: %s", exc)
        if routing_owned:
            try:
                self.audio_manager.disable_streaming_audio(None)
            except Exception as exc:
                _log.error("Error restoring audio: %s", exc)
        self.active_host_sink = ""
        GLib.idle_add(self._on_hosting_stopped)

    def _on_hosting_stopped(self) -> bool:
        self.is_hosting = False
        self.sync_ui_state()
        self.loading_bar.set_visible(False)
        if hasattr(self, "overview_start_button"):
            self.overview_start_button.set_sensitive(True)
        self.show_toast(_("Sharing stopped"))
        return False

    def get_ip_addresses(self):
        ipv4 = ipv6 = "None"
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("1.1.1.1", 80))
                ipv4 = s.getsockname()[0]
        except Exception:
            pass
        try:
            res = subprocess.run(["ip", "-j", "addr"], capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                for iface in json.loads(res.stdout):
                    name = iface["ifname"]
                    # Pular interfaces de loopback, desligadas ou virtuais conhecidas
                    if name == "lo" or "UP" not in iface["flags"]:
                        continue
                    if any(x in name for x in ["docker", "veth", "virbr", "vboxnet", "tailscale", "zerotier", "br-"]):
                        continue
                    for addr in iface.get("addr_info", []):
                        if addr["family"] == "inet":
                            if ipv4 == "None":
                                ipv4 = addr["local"]
                        elif addr["family"] == "inet6":
                            # Prioritize global but accept link-local
                            if addr.get("scope") == "global":
                                ipv6 = addr["local"]
                                break  # Found global, stop searching for this interface
                            elif ipv6 == "None":
                                # Fallback to link-local with scope ID
                                ipv6 = f"{addr['local']}%{name}"
        except Exception:
            pass

        # No longer wrapping in brackets as per user feedback

        return ipv4, ipv6

    def show_start_error_dialog(self, message):
        if not message:
            message = _("Check logs for details.")

        body = _("Sunshine failed to start.\n\nError: {}\n\nIf this is a dependency issue (missing libraries), try the 'Fix Dependencies' button.").format(message)

        dialog = Adw.AlertDialog(heading=_("Server Failed to Start"), body=body)
        dialog.add_response("cancel", _("Close"))
        dialog.add_response("logs", _("View Logs"))
        dialog.add_response("fix", _("Fix Dependencies"))

        dialog.set_response_appearance("fix", Adw.ResponseAppearance.SUGGESTED)

        def on_response(d, r):
            if r == "logs":
                try:
                    log_path = self.sunshine.config_dir / "sunshine.log"
                    open_path(self, log_path)
                except Exception:
                    pass
            elif r == "fix":
                self.open_advanced_settings()

        dialog.connect("response", on_response)
        dialog.present(self)

    def show_error_dialog(self, title, message):
        dialog = Adw.AlertDialog(heading=title, body=message)
        dialog.add_response("ok", "OK")
        dialog.present(self)

    def show_toast(self, message):
        show_toast = getattr(self.get_root(), "show_toast", None)
        if callable(show_toast):
            show_toast(message)
        else:
            _log.info(f"Toast: {message}")

    def open_sunshine_config(self, button):
        # The web panel is served by Sunshine itself: opening it while the
        # server is stopped lands the browser on a connection error.
        if not self.sunshine.is_running():
            self.show_toast(_("Sunshine is not running."))
            return
        # Gtk.show_uri (not xdg-open) so the browser is raised under Wayland.
        open_uri(self, self.sunshine.web_ui_url)

    # --- Paired devices (Sunshine clients API) ---------------------------

    def open_paired_devices_dialog(self, _widget: Gtk.Widget) -> None:
        group = Adw.PreferencesGroup()
        self._device_rows = []
        loading = Adw.ActionRow(title=_("Loading…"))
        group.add(loading)
        self._device_rows.append(loading)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.append(group)
        remove_all = Gtk.Button(label=_("Remove All"), halign=Gtk.Align.END)
        remove_all.add_css_class("destructive-action")
        box.append(remove_all)
        description = _("Devices paired with Sunshine. Disable to block access without re-pairing; remove to revoke (a new PIN will be required).")
        dialog = content_dialog(_("Paired Devices"), box, description=description)

        def confirm_remove(_button):
            confirm = Adw.AlertDialog(heading=_("Remove All"), body=description)
            confirm.add_response("cancel", _("Cancel"))
            confirm.add_response("remove", _("Remove All"))
            confirm.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
            confirm.set_default_response("cancel")
            confirm.set_close_response("cancel")
            confirm.connect("response", lambda _d, response: self._unpair_all_devices() if response == "remove" else None)
            confirm.present(self)

        remove_all.connect("clicked", confirm_remove)
        dialog.connect("closed", lambda _d: setattr(self, "_devices_dialog_group", None))
        self._devices_dialog_group = group
        self._refresh_paired_devices()
        dialog.present(self)

    def _refresh_paired_devices(self) -> None:
        has_dialog = getattr(self, "_devices_dialog_group", None) is not None
        has_overview = getattr(self, "paired_devices_overview_group", None) is not None
        if not has_dialog and not has_overview:
            return
        import threading

        auth = self._get_sunshine_creds()
        if has_overview:
            self._set_paired_devices_overview_message(
                _("Loading paired devices…"),
                _("Checking which devices are already allowed."),
            )

        def work():
            clients = self.sunshine.list_clients(auth=auth)
            GLib.idle_add(self._populate_paired_devices_overview, clients)
            GLib.idle_add(self._populate_paired_devices, clients)

        threading.Thread(target=work, daemon=True).start()

    def _populate_paired_devices(self, clients: list[dict[str, object]]) -> bool:
        group = getattr(self, "_devices_dialog_group", None)
        if group is None:
            return False
        for row in getattr(self, "_device_rows", []):
            group.remove(row)
        self._device_rows = []

        if not clients:
            empty = Adw.ActionRow(title=_("No paired devices"))
            group.add(empty)
            self._device_rows.append(empty)
            return False

        for client in clients:
            uuid = str(client.get("uuid", ""))
            name = str(client.get("name") or _("Unknown device"))
            enabled = bool(client.get("enabled", True))
            row = Adw.ActionRow(title=name, subtitle=uuid, use_markup=False)
            row.set_title_lines(0)

            switch = Gtk.Switch()
            switch.set_valign(Gtk.Align.CENTER)
            switch.set_active(enabled)
            switch.update_property([Gtk.AccessibleProperty.LABEL], [_("Device enabled")])
            switch.connect("notify::active", self._on_device_toggle, uuid)
            row.add_suffix(switch)

            remove = Gtk.Button()
            remove.set_child(create_icon_widget("brp-trash-symbolic", size=16))
            remove.add_css_class("flat")
            remove.set_valign(Gtk.Align.CENTER)
            remove.set_tooltip_text(_("Remove device"))
            remove.update_property([Gtk.AccessibleProperty.LABEL], [_("Remove device")])
            remove.connect("clicked", self._on_device_remove, uuid, name)
            row.add_suffix(remove)

            group.add(row)
            self._device_rows.append(row)
        return False

    def _on_device_toggle(self, switch: Gtk.Switch, _pspec: object, uuid: str) -> None:
        enabled = switch.get_active()
        import threading

        auth = self._get_sunshine_creds()

        def work():
            if self.sunshine.set_client_enabled(uuid, enabled, auth=auth):
                GLib.idle_add(self._after_device_change, True)
            else:
                GLib.idle_add(self.show_toast, _("Failed to update device"))

        threading.Thread(target=work, daemon=True).start()

    def _on_device_remove(self, _button: Gtk.Button, uuid: str, name: str) -> None:
        confirm = Adw.AlertDialog(
            heading=_("Remove Device"),
            body=_("Remove “{}”? It will need to pair again with a new PIN.").format(name),
        )
        confirm.add_response("cancel", _("Cancel"))
        confirm.add_response("remove", _("Remove"))
        confirm.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        confirm.set_default_response("cancel")
        confirm.set_close_response("cancel")

        def on_resp(d, r):
            if r == "remove":
                import threading

                auth = self._get_sunshine_creds()

                def work():
                    ok = self.sunshine.unpair_client(uuid, auth=auth)
                    GLib.idle_add(self._after_device_change, ok)

                threading.Thread(target=work, daemon=True).start()

        confirm.connect("response", on_resp)
        confirm.present(self)

    def _unpair_all_devices(self) -> None:
        import threading

        auth = self._get_sunshine_creds()

        def work():
            ok = self.sunshine.unpair_all_clients(auth=auth)
            GLib.idle_add(self._after_device_change, ok)

        threading.Thread(target=work, daemon=True).start()

    def _after_device_change(self, ok: bool) -> bool:
        self.show_toast(_("Devices updated") if ok else _("Operation failed"))
        self._refresh_paired_devices()
        return False

    # --- Sunshine logs (logs API) ----------------------------------------

    def open_logs_dialog(self, _widget):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.set_halign(Gtk.Align.END)

        refresh = Gtk.Button()
        refresh.set_child(create_icon_widget("brp-view-refresh-symbolic", size=16))
        refresh.add_css_class("flat")
        refresh.set_tooltip_text(_("Refresh"))
        refresh.update_property([Gtk.AccessibleProperty.LABEL], [_("Refresh logs")])
        refresh.connect("clicked", lambda b: self._refresh_logs())
        toolbar.append(refresh)

        copy = Gtk.Button()
        copy.set_child(create_icon_widget("brp-edit-copy-symbolic", size=16))
        copy.add_css_class("flat")
        copy.set_tooltip_text(_("Copy"))
        copy.update_property([Gtk.AccessibleProperty.LABEL], [_("Copy logs")])
        copy.connect("clicked", lambda b: self._copy_logs())
        toolbar.append(copy)

        textview = Gtk.TextView()
        textview.set_editable(False)
        textview.set_monospace(True)
        textview.set_cursor_visible(False)
        self._logs_textview = textview

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(360)
        scroll.set_vexpand(True)
        scroll.set_child(textview)

        box.append(toolbar)
        box.append(scroll)
        dialog = content_dialog(_("Sunshine Logs"), box, height=560)
        self._refresh_logs()
        dialog.present(self)

    def _refresh_logs(self):
        tv = getattr(self, "_logs_textview", None)
        if tv is None:
            return
        tv.get_buffer().set_text(_("Loading…"))
        import threading

        auth = self._get_sunshine_creds()

        def work():
            text = self.sunshine.get_logs(auth=auth)
            GLib.idle_add(self._set_logs_text, text)

        threading.Thread(target=work, daemon=True).start()

    def _set_logs_text(self, text):
        tv = getattr(self, "_logs_textview", None)
        if tv is None:
            return False
        tv.get_buffer().set_text(text or _("No logs available (Sunshine not running or no credentials)."))
        return False

    def _copy_logs(self):
        tv = getattr(self, "_logs_textview", None)
        if tv is None:
            return
        buf = tv.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(text)
        self.show_toast(_("Copied!"))

    # --- Game library (Sunshine apps API) --------------------------------

    def open_game_library_dialog(self, _widget):
        if not self.sunshine.is_running():
            self.show_error_dialog(_("Sharing is off"), _("Start sharing first to manage the game library."))
            return

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.set_halign(Gtk.Align.END)
        add_btn = Gtk.Button(label=_("Add Detected Games"))
        add_btn.add_css_class("suggested-action")
        add_btn.connect("clicked", lambda b: self._add_detected_games())
        toolbar.append(add_btn)

        group = Adw.PreferencesGroup()
        self._library_rows = []
        loading = Adw.ActionRow(title=_("Loading…"))
        group.add(loading)
        self._library_rows.append(loading)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(280)
        scroll.set_vexpand(True)
        scroll.set_child(group)

        box.append(toolbar)
        box.append(scroll)
        dialog = content_dialog(_("Game Library"), box, description=_("Games shown on the other computer in Moonlight. Add detected games or remove entries."))
        self._library_group = group
        self._refresh_game_library()
        dialog.present(self)

    def _refresh_game_library(self):
        if getattr(self, "_library_group", None) is None:
            return
        import threading

        auth = self._get_sunshine_creds()

        def work():
            apps = self.sunshine.get_apps(auth=auth)
            GLib.idle_add(self._populate_game_library, apps)

        threading.Thread(target=work, daemon=True).start()

    def _populate_game_library(self, apps):
        group = getattr(self, "_library_group", None)
        if group is None:
            return False
        for row in getattr(self, "_library_rows", []):
            group.remove(row)
        self._library_rows = []

        if not apps:
            empty = Adw.ActionRow(title=_("No apps configured"))
            group.add(empty)
            self._library_rows.append(empty)
            return False

        # Sunshine identifies apps by their position in the list (DELETE
        # /api/apps/{index}); the entries themselves carry no index field.
        for index, app in enumerate(apps):
            name = app.get("name") or _("Unnamed")
            row = Adw.ActionRow(title=name, use_markup=False)
            row.set_title_lines(0)
            row.set_subtitle_lines(2)
            cmd = app.get("cmd")
            if cmd:
                row.set_subtitle(cmd)
            # Desktop is the fallback target; do not let the user delete it.
            if name != "Desktop":
                remove = Gtk.Button()
                remove.set_child(create_icon_widget("brp-trash-symbolic", size=16))
                remove.add_css_class("flat")
                remove.set_valign(Gtk.Align.CENTER)
                remove.set_tooltip_text(_("Remove app"))
                remove.update_property([Gtk.AccessibleProperty.LABEL], [_("Remove app")])
                remove.connect("clicked", self._on_library_remove, index)
                row.add_suffix(remove)
            group.add(row)
            self._library_rows.append(row)
        return False

    def _on_library_remove(self, _button, index):
        import threading

        auth = self._get_sunshine_creds()

        def work():
            ok = self.sunshine.delete_app(index, auth=auth)
            GLib.idle_add(self._after_library_change, ok)

        threading.Thread(target=work, daemon=True).start()

    def _add_detected_games(self):
        import threading

        auth = self._get_sunshine_creds()

        def work():
            games = self.game_detector.detect_all()
            existing = {a.get("name") for a in self.sunshine.get_apps(auth=auth)}
            added = 0
            for game in games:
                if game["name"] in existing:
                    continue
                entry = {
                    "name": game["name"],
                    "cmd": game.get("cmd", ""),
                    "output": "",
                    "image-path": "",
                    "detached": [],
                }
                if self.sunshine.add_app(entry, auth=auth):
                    added += 1
            GLib.idle_add(self._after_library_add, added)

        threading.Thread(target=work, daemon=True).start()

    def _after_library_add(self, added):
        self.show_toast(_("Games added: {}").format(added))
        self._refresh_game_library()
        return False

    def _after_library_change(self, ok):
        self.show_toast(_("Library updated") if ok else _("Operation failed"))
        self._refresh_game_library()
        return False

    # --- Host file browser (browse API) ----------------------------------

    def open_host_browse_dialog(self):
        if not self.sunshine.is_running():
            self.show_error_dialog(_("Sharing is off"), _("Start sharing first to browse this PC."))
            return

        group = Adw.PreferencesGroup()
        self._browse_group = group
        self._browse_rows = []

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(360)
        scroll.set_child(group)
        dialog = content_dialog(_("Select Executable"), scroll)
        self._browse_dialog = dialog

        import os

        self._browse_load(os.path.expanduser("~"))
        dialog.present(self)

    def _browse_load(self, path: str) -> None:
        if getattr(self, "_browse_group", None) is None:
            return
        import threading

        auth = self._get_sunshine_creds()

        def work() -> None:
            data = self.sunshine.browse(path, "any", auth=auth)
            GLib.idle_add(self._browse_populate, data)

        threading.Thread(target=work, daemon=True).start()

    def _create_browse_button(
        self,
        title: str,
        icon_name: str,
        description: str,
        on_activate: Callable[[str], None],
        target: str,
    ) -> Adw.ActionRow:
        # Native rows share the rounded group, keyboard focus and plain-text
        # semantics. Each callback closes over its own target, not the loop value.
        return action_row(title, description, icon_name, lambda: on_activate(target))

    def _browse_populate(self, data: dict | None) -> bool:
        group = getattr(self, "_browse_group", None)
        if group is None:
            return False
        for row in getattr(self, "_browse_rows", []):
            group.remove(row)
        self._browse_rows = []

        if not data:
            empty = Adw.ActionRow(title=_("Cannot browse (no access or credentials)"))
            group.add(empty)
            self._browse_rows.append(empty)
            return False

        group.set_title(data.get("path", ""))
        parent = data.get("parent")
        if parent:
            up = self._create_browse_button(
                _("Up one level"),
                "go-up-symbolic",
                _("Open parent folder"),
                self._browse_load,
                parent,
            )
            group.add(up)
            self._browse_rows.append(up)

        for entry in data.get("entries", []):
            name = entry.get("name", "")
            etype = entry.get("type", "")
            epath = entry.get("path", "")
            if etype == "directory":
                row = self._create_browse_button(
                    name,
                    "brp-folder-open-symbolic",
                    _("Open folder"),
                    self._browse_load,
                    epath,
                )
            else:
                row = self._create_browse_button(
                    name,
                    "application-x-executable-symbolic",
                    _("Select executable"),
                    self._browse_pick,
                    epath,
                )
            group.add(row)
            self._browse_rows.append(row)
        return False

    def _browse_pick(self, path: str) -> None:
        self.custom_cmd_entry.set_text(path)
        if getattr(self, "_browse_dialog", None) is not None:
            self._browse_dialog.close()
        self.show_toast(_("Selected: {}").format(path))

    # --- Server password (change / reset) --------------------------------

    def open_advanced_settings(self, _widget=None):
        """Full Sunshine server tuning + library fix, opened from the task itself."""
        from big_remote_play.ui.sunshine_preferences import SunshineSettings

        SunshineSettings(main_config=self.config).build_dialog().present(self)

    def open_password_dialog(self, _widget):
        # A form belongs in an adaptive sheet, not in a confirmation dialog.
        # Invalid input keeps the form open so credentials never need retyping.
        dialog = Adw.Dialog(title=_("Server Password"))
        dialog.set_content_width(480)
        dialog.set_content_height(610)
        dialog.add_css_class("brp-dialog")
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        for edge in ("top", "bottom", "start", "end"):
            getattr(content, f"set_margin_{edge}")(20)
        description = Gtk.Label(
            label=_("Set the username and password used to manage the Sunshine server. If you forgot the current password, switch on “I forgot the current password” to reset it."),
            wrap=True,
            xalign=0,
        )
        description.add_css_class("dim-label")
        content.append(description)
        creds = self._get_sunshine_creds()
        default_user = creds[0] if creds else "sunshine"
        grp = Adw.PreferencesGroup()
        forgot_row = Adw.SwitchRow(title=_("I forgot the current password"))
        forgot_row.set_subtitle(_("Reset directly; the server will restart"))
        cur_user_row = Adw.EntryRow(title=_("Current Username"))
        cur_user_row.set_text(default_user)
        cur_pass_row = Adw.PasswordEntryRow(title=_("Current Password"))
        if creds:
            cur_pass_row.set_text(creds[1])
        new_user_row = Adw.EntryRow(title=_("New Username"))
        new_user_row.set_text(default_user)
        new_pass_row = Adw.PasswordEntryRow(title=_("New Password"))
        confirm_row = Adw.PasswordEntryRow(title=_("Confirm New Password"))
        forgot_row.set_title_lines(0)
        forgot_row.set_subtitle_lines(0)
        for row in (forgot_row, cur_user_row, cur_pass_row, new_user_row, new_pass_row, confirm_row):
            row.set_use_markup(False)
            grp.add(row)
        content.append(grp)
        error = Gtk.Label(wrap=True, xalign=0, visible=False)
        error.add_css_class("error")
        content.append(error)

        def on_forgot(switch, _pspec):
            reset = switch.get_active()
            cur_user_row.set_sensitive(not reset)
            cur_pass_row.set_sensitive(not reset)

        forgot_row.connect("notify::active", on_forgot)
        actions = Gtk.Box(spacing=12, halign=Gtk.Align.END)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda _button: dialog.close())
        save = Gtk.Button(label=_("Save"))
        save.add_css_class("suggested-action")
        actions.append(cancel)
        actions.append(save)
        content.append(actions)
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(content)
        toolbar.set_content(scroll)
        dialog.set_child(toolbar)
        dialog.set_default_widget(save)

        def on_save(_button):
            new_user = new_user_row.get_text().strip()
            new_pass = new_pass_row.get_text()
            if not new_user or not new_pass:
                error.set_label(_("Username and password cannot be empty."))
                error.set_visible(True)
                (new_user_row if not new_user else new_pass_row).grab_focus()
                return
            if new_pass != confirm_row.get_text():
                error.set_label(_("New passwords do not match."))
                error.set_visible(True)
                confirm_row.grab_focus()
                return
            if forgot_row.get_active():
                self._reset_password_async(new_user, new_pass)
            else:
                current = (cur_user_row.get_text().strip(), cur_pass_row.get_text())
                self._change_password_async(new_user, new_pass, current)
            dialog.close()

        save.connect("clicked", on_save)
        dialog.present(self)

    def _change_password_async(self, new_user, new_password, current):
        import threading

        def work():
            ok, message = self.sunshine.set_credentials(new_user, new_password, current=current)
            if ok:
                self._save_sunshine_creds(new_user, new_password)
            GLib.idle_add(self.show_toast, message)

        threading.Thread(target=work, daemon=True).start()

    def _reset_password_async(self, new_user, new_password):
        import threading

        def work():
            ok, message = self.sunshine.reset_credentials(new_user, new_password)
            if ok:
                self._save_sunshine_creds(new_user, new_password)
                if self.sunshine.is_running():
                    self.sunshine.restart()
            GLib.idle_add(self.show_toast, message)

        threading.Thread(target=work, daemon=True).start()

    def on_game_mode_changed(self, row, _param):
        idx = row.get_selected()
        self.platform_games_expander.set_visible(idx in [1, 2])
        self.platform_games_expander.set_expanded(idx in [1, 2])
        self.custom_app_expander.set_visible(idx == 3)
        self.custom_app_expander.set_expanded(idx == 3)
        if idx in [1, 2]:
            plat = {1: "Steam", 2: "Lutris"}[idx]
            self.platform_games_expander.set_title(f"{plat} Games")
            self.populate_game_list(idx)

    def populate_game_list(self, mode_idx):
        plat = {1: "Steam", 2: "Lutris"}.get(mode_idx)
        if not plat:
            return
        if not self.detected_games[plat]:
            if plat == "Steam":
                self.detected_games["Steam"] = self.game_detector.detect_steam()
            elif plat == "Lutris":
                self.detected_games["Lutris"] = self.game_detector.detect_lutris()
        games = self.detected_games[plat]
        new_model = Gtk.StringList()
        if not games:
            new_model.append(f"No games found on {plat}")
        else:
            for game in games:
                new_model.append(game["name"])
        self.game_list_row.set_model(new_model)

    def save_host_settings(self, *_args):
        if getattr(self, "loading_settings", False):
            return
        h = self.config.get("host", {})
        if not isinstance(h, dict):
            h = {}
        # No selection yields INVALID_LIST_POSITION (4294967295); persist 0 so it
        # round-trips through set_selected() cleanly on the next load.
        game_list_idx = self.game_list_row.get_selected()
        if game_list_idx == Gtk.INVALID_LIST_POSITION:
            game_list_idx = 0
        h.update(
            {
                "mode_idx": self.game_mode_row.get_selected(),
                "game_list_idx": game_list_idx,
                "custom_name": self.custom_name_entry.get_text(),
                "custom_cmd": self.custom_cmd_entry.get_text(),
                # New Separate Settings
                "auto_quality": self.auto_quality_row.get_active(),
                "auto_signature": self._auto_signature,
                "fps_idx": self.fps_row.get_selected(),
                "bandwidth_mbps": self.bandwidth_row.get_value(),
                "monitor_idx": self.monitor_row.get_selected(),
                "gpu_idx": self.gpu_row.get_selected(),
                "platform_idx": self.platform_row.get_selected(),
                "audio_mode": self.audio_mode_row.get_selected(),
                "audio_output_name": self._selected_audio_sink(),
                "upnp": self.upnp_row.get_active(),
                "ipv6": self.ipv6_row.get_active(),
                "webui_anyone": self.webui_anyone_row.get_active(),
                # New settings
                "efficient_codecs": self.codecs_row.get_active(),
                "optimization_mode": self.optimization_row.get_selected(),
                "wifi_mode": self.wifi_row.get_active(),
            }
        )

        self.config.set("host", h)

        # The host cannot infer the client-requested frame rate.
        self.perf_monitor.set_target_fps(0)  # Client FPS cannot be inferred on the host.

        # Update monitor target Bandwidth live
        self.perf_monitor.set_target_bandwidth(self.bandwidth_row.get_value())

        # Sync to Sunshine Config — build the full mapping, then write once.
        try:
            from big_remote_play.ui.sunshine_preferences import SunshineConfigManager

            scm = SunshineConfigManager()

            bw = int(self.bandwidth_row.get_value() * 1000)  # Mbps -> Kbps

            sunshine_settings = {
                "upnp": "enabled" if self.upnp_row.get_active() else "disabled",
                "address_family": "both" if self.ipv6_row.get_active() else "ipv4",
                "origin_web_ui_allowed": "wan" if self.webui_anyone_row.get_active() else "lan",
                "stream_audio": "disabled" if self._selected_audio_sink() and self.audio_mode_row.get_selected() == 2 else "enabled",
                "max_bitrate": str(bw) if bw > 0 else "0",
                **self._encoding_settings(),
            }
            scm.update(sunshine_settings)

        except Exception as e:
            _log.error(f"Error syncing to Sunshine config: {e}")

    @staticmethod
    def _select_saved_index(row, value, fallback=0) -> None:
        model = row.get_model()
        count = model.get_n_items() if model is not None else 0
        index = value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < count else fallback
        row.set_selected(index if index < count else Gtk.INVALID_LIST_POSITION)

    def load_settings(self):
        self.loading_settings = True
        try:
            # Sync from Sunshine Config first
            try:
                from big_remote_play.ui.sunshine_preferences import SunshineConfigManager

                scm = SunshineConfigManager()

                # Update Host Config based on Sunshine Config (Source of Truth for these fields)
                h = self.config.get("host", {})
                if not isinstance(h, dict):
                    h = {}
                h["upnp"] = scm.get("upnp", "disabled") == "enabled"
                h["ipv6"] = scm.get("address_family", "ipv4") == "both"
                h["webui_anyone"] = scm.get("origin_web_ui_allowed", "lan") == "wan"
                h["audio"] = scm.get("stream_audio", "enabled").lower() in ("true", "enabled", "1")

                # Reverse Map Codecs
                # 1 disables advertising; 0 means automatic capability detection.
                hevc = scm.get("hevc_mode", "0")
                av1 = scm.get("av1_mode", "0")
                h["efficient_codecs"] = hevc != "1" or av1 != "1"

                # Reverse Map Wi-Fi (FEC)
                # Values above the normal 20% setting mean extra correction.
                fec = int(scm.get("fec_percentage", "20"))
                h["wifi_mode"] = fec > 20

                # Reverse Map Optimization
                # Heuristic based on nvenc_preset
                nv_preset = scm.get("nvenc_preset", "4")
                if nv_preset in ["1", "2"]:
                    h["optimization_mode"] = 0  # Low Latency
                elif nv_preset in ["5", "6", "7"]:
                    h["optimization_mode"] = 2  # High Quality
                else:
                    h["optimization_mode"] = 1  # Balanced

                # Reverse Map Bandwidth
                bw_kbps = int(scm.get("max_bitrate", "0"))
                h["bandwidth_mbps"] = bw_kbps / 1000.0

                self.config.set("host", h)
            except Exception as e:
                _log.error(f"Error syncing from Sunshine config: {e}")

            h = self.config.get("host", {})
            if not isinstance(h, dict):
                h = {}
            if not h:
                return
            self._select_saved_index(self.game_mode_row, h.get("mode_idx", 0))
            # Restore game list selection after populating
            mode_idx = h.get("mode_idx", 0)
            if mode_idx in [1, 2]:
                self.populate_game_list(mode_idx)
                game_list_idx = h.get("game_list_idx", 0)
                model = self.game_list_row.get_model()
                count = model.get_n_items() if model is not None else 0
                if isinstance(game_list_idx, int) and 0 <= game_list_idx < count:
                    self.game_list_row.set_selected(game_list_idx)
            self.custom_name_entry.set_text(h.get("custom_name", ""))
            self.custom_cmd_entry.set_text(h.get("custom_cmd", ""))

            # New Separate Settings
            # "fps_idx" only exists in a configuration a previous version wrote,
            # so an updating user keeps the picture they had chosen by hand.
            self.auto_quality_row.set_active(bool(h.get("auto_quality", "fps_idx" not in h)))
            self._auto_signature = str(h.get("auto_signature", ""))
            fps_idx = h.get("fps_idx", 1)
            self._select_saved_index(self.fps_row, fps_idx, 1)

            # Update monitor target FPS
            self.perf_monitor.set_target_fps(0)  # Client FPS cannot be inferred on the host.

            bw_val = h.get("bandwidth_mbps", 0)
            self.bandwidth_row.set_value(bw_val)
            self.perf_monitor.set_target_bandwidth(bw_val)

            self._select_saved_index(self.monitor_row, h.get("monitor_idx", 0))
            self._select_saved_index(self.gpu_row, h.get("gpu_idx", 0))
            self._select_saved_index(self.platform_row, h.get("platform_idx", 0))

            # Audio Mode
            audio_mode = h.get("audio_mode", 0)
            self._select_saved_index(self.audio_mode_row, audio_mode)
            desired_output = h.get("audio_output_name", "")
            if desired_output in self._audio_choice_names:
                self.audio_output_row.set_selected(self._audio_choice_names.index(desired_output))
            self._sync_audio_controls()

            self.upnp_row.set_active(h.get("upnp", False))
            self.ipv6_row.set_active(h.get("ipv6", False))
            self.webui_anyone_row.set_active(h.get("webui_anyone", False))

            # New settings
            self.codecs_row.set_active(h.get("efficient_codecs", True))
            self.optimization_row.set_selected(h.get("optimization_mode", 1))
            self.wifi_row.set_active(h.get("wifi_mode", False))
        finally:
            self.loading_settings = False

    def connect_settings_signals(self):
        for r in [self.upnp_row, self.ipv6_row, self.webui_anyone_row, self.codecs_row, self.wifi_row]:
            r.connect("notify::active", self._schedule_save_host_settings)

        for r in [
            self.audio_mode_row,
            self.game_mode_row,
            self.game_list_row,
            self.monitor_row,
            self.gpu_row,
            self.platform_row,
            self.audio_output_row,
            self.optimization_row,
            self.fps_row,
        ]:
            r.connect("notify::selected", self._schedule_save_host_settings)

        for r in [self.bandwidth_row]:
            r.connect("notify::value", self._schedule_save_host_settings)

        self.auto_quality_row.connect("notify::active", self._on_auto_quality_toggled)
        # The page summary follows whatever the rows currently say, automatic or not.
        for row, signal in (
            (self.fps_row, "notify::selected"),
            (self.gpu_row, "notify::selected"),
            (self.bandwidth_row, "notify::value"),
            (self.monitor_row, "notify::selected"),
            (self.platform_row, "notify::selected"),
            (self.optimization_row, "notify::selected"),
            (self.codecs_row, "notify::active"),
            (self.wifi_row, "notify::active"),
        ):
            row.connect(signal, lambda *_a: self._sync_quality_controls())
        for r in [self.custom_name_entry, self.custom_cmd_entry]:
            r.connect("notify::text", self._schedule_save_host_settings)

    def _schedule_save_host_settings(self, *_args):
        """Coalesce a burst of row-change signals into a single settings write."""
        if getattr(self, "loading_settings", False):
            return
        if self._save_timer_id is not None:
            GLib.source_remove(self._save_timer_id)
        self._save_timer_id = GLib.timeout_add(300, self._flush_save_host_settings)

    def _flush_save_host_settings(self) -> bool:
        self._save_timer_id = None
        self.save_host_settings()
        return False

    def on_reset_clicked(self, button):
        diag = Adw.AlertDialog(heading=_("Restore Defaults"), body=_("Do you want to restore default settings?"))
        diag.add_response("cancel", _("Cancel"))
        diag.add_response("reset", _("Restore"))
        diag.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        diag.set_default_response("cancel")
        diag.set_close_response("cancel")

        def on_resp(d, r):
            if r == "reset":
                self.reset_to_defaults()

        diag.connect("response", on_resp)
        diag.present(self)

    def reset_to_defaults(self):
        self.config.set("host", self.config.default_config()["host"])
        self.load_settings()
        self.show_toast(_("Settings Restored"))

    def cleanup(self):
        self._closed = True
        if hasattr(self, "perf_monitor"):
            self.perf_monitor.stop_monitoring()
        stop_pin_listener = self.stop_pin_listener
        if callable(stop_pin_listener):
            stop_pin_listener()
        uptime_timer_id = self._uptime_timer_id
        if uptime_timer_id is not None:
            GLib.source_remove(uptime_timer_id)
            self._uptime_timer_id = None

        if self._save_timer_id is not None:
            # Flush a pending debounced save so edits aren't lost on close.
            GLib.source_remove(self._save_timer_id)
            self._save_timer_id = None
            self.save_host_settings()

        # Only cleanup audio if we are NOT hosting, because Sunshine depends on these sinks.
        # If we are hosting, the user expects the stream to continue working.
        # This also avoids the feedback loop (microfonia) when the app is closed while Moonlight/Sunshine are active.
        if not self.is_hosting:
            if hasattr(self, "audio_manager"):
                self.audio_manager.cleanup()
