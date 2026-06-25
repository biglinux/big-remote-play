import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from collections.abc import Callable
from gi.repository import Gtk, Gdk, Adw, GLib  # type: ignore
import subprocess, random, string, json, socket, os, time
import shlex
from pathlib import Path
from big_remote_play.utils.game_detector import GameDetector

from big_remote_play.utils.config import Config
import threading
from big_remote_play.utils.i18n import _
from big_remote_play.utils.icons import create_icon_widget, set_icon
from big_remote_play.utils.widgets import MetricTile, create_page_header
from big_remote_play import paths
from big_remote_play.utils.secret_store import SecretStoreUnavailable
from big_remote_play.utils.sunshine_credentials import ensure_sunshine_api_config, load_sunshine_credentials, save_sunshine_credentials
from big_remote_play.utils.uri import open_uri, open_path


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
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.config = Config()
        self.is_hosting = False
        self.process = None # Initialize to avoid AttributeError
        self.pin_code = None
        self.private_audio_apps = set()
        self.audio_devices = []
        self.active_host_sink = ""
        self.stop_pin_listener = None
        self._uptime_timer_id = None
        self._hosting_started_at = None
        
        from big_remote_play.host.sunshine_manager import SunshineHost
        self.sunshine = SunshineHost(Path.home() / '.config' / 'big-remoteplay' / 'sunshine')
        
        if self.sunshine.is_running():
            self.is_hosting = True
            
        self.available_monitors = self.detect_monitors()
        self.available_gpus = self.detect_gpus()
        self.setup_ui()
        
        self.game_detector = GameDetector()
        self.detected_games = {'Steam': [], 'Lutris': []}
        self.load_settings()
        self.connect_settings_signals()
        self.loading_settings = False
        
        # Ensure config is correct (API enabled)
        if hasattr(self, '_ensure_sunshine_config'):
             self._ensure_sunshine_config()
             
        self.sync_ui_state()

    def _root_window(self):
        root = self.get_root()
        return root if isinstance(root, Gtk.Window) else None
        
    def detect_monitors(self):
        monitors = [(_('Automatic'), 'auto')]
        is_wayland = os.environ.get('XDG_SESSION_TYPE') == 'wayland'
        
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
                        if manufacturer: label_parts.append(manufacturer)
                        if model: label_parts.append(model)
                        label = " ".join(label_parts) if label_parts else "Monitor"
                        
                        # Value logic: Wayland uses 0, 1, 2... | X11 uses HDMI-A-1, etc.
                        val = str(i) if is_wayland else conn
                        full_label = f"{label} ({conn})"
                        monitors.append((full_label, val))
                        names.append(conn)
        except Exception as e:
            print(f"Error detecting GDK monitors: {e}")

        # Fallback for X11/DRM if GDK didn't find everything
        if not is_wayland:
            # Xrandr (Reinforcement for X11)
            try:
                res = subprocess.check_output(["xrandr", "--listmonitors"], text=True, timeout=5)
                for n in _parse_xrandr_monitor_names(res):
                    if n and n not in names:
                        monitors.append((f"Display ({n})", n))
                        names.append(n)
            except Exception: pass

            # DRM (Reinforcement for KMS/DRM)
            try:
                from pathlib import Path
                for p in Path('/sys/class/drm').glob('card*-*'):
                    if (p/'status').exists() and (p/'status').read_text().strip() == 'connected':
                        name = p.name.split('-', 1)[1]
                        if name not in names:
                            monitors.append((f"DRM Display ({name})", name))
                            names.append(name)
            except Exception: pass
            
        return monitors

    def detect_gpus(self):
        gpus = []
        try:
            lspci = subprocess.check_output(['lspci'], text=True, timeout=5).lower()
            if 'nvidia' in lspci:
                try:
                    subprocess.check_call(['nvidia-smi'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                    gpus.append({'label': 'NVENC (NVIDIA)', 'encoder': 'nvenc', 'adapter': 'auto'})
                except Exception: pass
            if 'intel' in lspci: gpus.append({'label': 'VAAPI (Intel Quicksync)', 'encoder': 'vaapi', 'adapter': '/dev/dri/renderD128'})
        except Exception: pass
        try:
            from pathlib import Path
            if Path('/dev/dri').exists():
                for node in sorted(list(Path('/dev/dri').glob('renderD*'))):
                    if not any(str(node) == g['adapter'] for g in gpus):
                        gpus.append({'label': f"VAAPI (Adapter {node.name})", 'encoder': 'vaapi', 'adapter': str(node)})
        except Exception: pass
        gpus.extend([{'label':'Vulkan (Exp)', 'encoder':'vulkan', 'adapter':'auto'}, {'label':'Software', 'encoder':'software', 'adapter':'auto'}])
        return gpus
        
    def setup_ui(self):

        clamp = Adw.Clamp()
        clamp.set_maximum_size(1040)
        clamp.set_valign(Gtk.Align.START)
        for margin in ['top', 'bottom', 'start', 'end']:
            getattr(clamp, f'set_margin_{margin}')(20)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)

        self.loading_bar = Gtk.ProgressBar()
        self.loading_bar.add_css_class('osd')
        self.loading_bar.set_visible(False)
        content.append(self.loading_bar)

        content.append(create_page_header(_('Server'), icon_name='network-server-symbolic'))

        from .performance_monitor import PerformanceMonitor
        self.perf_monitor = PerformanceMonitor(sunshine=self.sunshine)
        self.perf_monitor.set_visible(True)
        self.perf_monitor.set_connection_status("Localhost", _("Sunshine Offline"), False)


        game_group = Adw.PreferencesGroup()
        game_group.set_title(_('Game Configuration'))
        
        reset_btn = Gtk.Button()
        reset_btn.set_child(create_icon_widget("edit-undo-symbolic", size=16))
        reset_btn.add_css_class("flat")
        reset_btn.set_tooltip_text(_("Reset to Defaults"))
        reset_btn.connect("clicked", self.on_reset_clicked)
        game_group.set_header_suffix(reset_btn)
        
        self.game_mode_row = Adw.ComboRow(); self.game_mode_row.set_title(_('Game Mode')); self.game_mode_row.set_subtitle(_('Select game source'))
        modes = Gtk.StringList()
        for m in [_('Full Desktop'), 'Steam', 'Lutris', _('Custom App')]: modes.append(m)
        self.game_mode_row.set_model(modes); self.game_mode_row.set_selected(0); self.game_mode_row.connect('notify::selected', self.on_game_mode_changed); game_group.add(self.game_mode_row)
        
        self.platform_games_expander = Adw.ExpanderRow()
        self.platform_games_expander.set_title(_("Game Selection"))
        self.platform_games_expander.set_subtitle(_("Choose game from list"))
        self.platform_games_expander.set_visible(False)
        
        self.game_list_row = Adw.ComboRow()
        self.game_list_row.set_title(_('Select Game'))
        self.game_list_row.set_subtitle(_('Choose game from list'))
        self.game_list_model = Gtk.StringList()
        self.game_list_row.set_model(self.game_list_model)
        self.platform_games_expander.add_row(self.game_list_row)
        game_group.add(self.platform_games_expander)
        
        self.custom_app_expander = Adw.ExpanderRow()
        self.custom_app_expander.set_title(_("Application Details"))
        self.custom_app_expander.set_subtitle(_("Configure name and command"))
        self.custom_app_expander.set_visible(False)
        
        self.custom_name_entry = Adw.EntryRow()
        self.custom_name_entry.set_title(_('Application Name'))
        self.custom_app_expander.add_row(self.custom_name_entry)
        
        self.custom_cmd_entry = Adw.EntryRow()
        self.custom_cmd_entry.set_title(_('Command'))
        browse_btn = Gtk.Button()
        browse_btn.set_child(create_icon_widget("folder-open-symbolic", size=16))
        browse_btn.add_css_class("flat")
        browse_btn.set_valign(Gtk.Align.CENTER)
        browse_btn.set_tooltip_text(_("Browse host for executable"))
        browse_btn.update_property([Gtk.AccessibleProperty.LABEL], [_("Browse host filesystem")])
        browse_btn.connect("clicked", lambda b: self.open_host_browse_dialog())
        self.custom_cmd_entry.add_suffix(browse_btn)
        self.custom_app_expander.add_row(self.custom_cmd_entry)
        game_group.add(self.custom_app_expander)
        
        self.streaming_expander = Adw.ExpanderRow()
        self.streaming_expander.set_title(_('Streaming Settings'))
        self.streaming_expander.set_subtitle(_('Quality and Players'))
        self.streaming_expander.set_icon_name('preferences-desktop-display-symbolic')
        
        # Resolution Row
        self.resolution_row = Adw.ComboRow()
        self.resolution_row.set_title(_("Resolution"))
        self.resolution_row.set_subtitle(_("Stream resolution"))
        res_model = Gtk.StringList()
        for res in ["720p", "1080p", "1440p", "4K", _("Custom")]:
            res_model.append(res)
        self.resolution_row.set_model(res_model)
        self.resolution_row.set_selected(1) # Default 1080p
        self.streaming_expander.add_row(self.resolution_row)
        
        # FPS Row
        self.fps_row = Adw.ComboRow()
        self.fps_row.set_title(_("Frame Rate (FPS)"))
        self.fps_row.set_subtitle(_("Frames per second"))
        fps_model = Gtk.StringList()
        for fps in ["30", "60", "120", "144", _("Custom")]:
            fps_model.append(fps)
        self.fps_row.set_model(fps_model)
        self.fps_row.set_selected(1) # Default 60
        self.streaming_expander.add_row(self.fps_row)
        
        # Bandwidth Row
        self.bandwidth_row = Adw.SpinRow()
        self.bandwidth_row.set_title(_("Bandwidth Limit (Mbps)"))
        self.bandwidth_row.set_subtitle(_("Max bitrate (0 = Unlimited)"))
        
        # Use simple numeric adjustment
        adj = Gtk.Adjustment(value=0, lower=0, upper=500, step_increment=5, page_increment=10)
        self.bandwidth_row.set_adjustment(adj)
        self.streaming_expander.add_row(self.bandwidth_row)

        game_group.add(self.streaming_expander)
        
        self.hardware_expander = Adw.ExpanderRow()
        self.hardware_expander.set_title(_('Hardware and Capture'))
        self.hardware_expander.set_subtitle(_('Monitor, GPU, and Capture Method'))
        self.hardware_expander.set_icon_name('video-display-symbolic')

        self.monitor_row = Adw.ComboRow()
        self.monitor_row.set_title(_('Monitor / Display'))
        self.monitor_row.set_subtitle(_('Select the display to capture'))
        monitor_model = Gtk.StringList()
        for label, _val in self.available_monitors: monitor_model.append(label)
        self.monitor_row.set_model(monitor_model)
        self.monitor_row.set_selected(0)
        self.hardware_expander.add_row(self.monitor_row)
        
        self.gpu_row = Adw.ComboRow()
        self.gpu_row.set_title(_('Graphics Card / Encoder'))
        self.gpu_row.set_subtitle(_('Choose hardware for video encoding'))
        gpu_model = Gtk.StringList()
        for gpu_info in self.available_gpus: gpu_model.append(gpu_info['label'])
        self.gpu_row.set_model(gpu_model)
        self.gpu_row.set_selected(0)
        self.hardware_expander.add_row(self.gpu_row)
        
        self.platform_row = Adw.ComboRow()
        self.platform_row.set_title(_('Capture Method'))
        self.platform_row.set_subtitle(_('Wayland (recommended), X11 (legacy), or KMS (direct)'))
        platform_model = Gtk.StringList()
        for p in [_('Automatic'), 'Wayland', 'X11', _('KMS (Direct)')]: platform_model.append(p)
        self.platform_row.set_model(platform_model)
        import os
        session_type = os.environ.get('XDG_SESSION_TYPE', '').lower()
        self.platform_row.set_selected(1 if session_type == 'wayland' else 2 if session_type == 'x11' else 0)
        self.hardware_expander.add_row(self.platform_row)
        
        # New "Performance" settings as requested
        self.codecs_row = Adw.SwitchRow()
        self.codecs_row.set_title(_("Efficient Codecs (HEVC/AV1)"))
        self.codecs_row.set_subtitle(_("Enable H.265/AV1 for better quality at lower bitrate (Requires support)"))
        self.codecs_row.set_active(True)
        self.hardware_expander.add_row(self.codecs_row)
        
        self.optimization_row = Adw.ComboRow()
        self.optimization_row.set_title(_("Optimization Mode"))
        self.optimization_row.set_subtitle(_("Balance between responsiveness and image quality"))
        opt_model = Gtk.StringList()
        opt_model.append(_("Low Latency (Fastest)"))
        opt_model.append(_("Balanced (Default)"))
        opt_model.append(_("High Quality (Best Image)"))
        self.optimization_row.set_model(opt_model)
        self.optimization_row.set_selected(1) # Balanced default
        self.hardware_expander.add_row(self.optimization_row)
        
        self.wifi_row = Adw.SwitchRow()
        self.wifi_row.set_title(_("Wi-Fi / Unstable Network Mode"))
        self.wifi_row.set_subtitle(_("Increases error correction (FEC) to prevent glitches"))
        self.wifi_row.set_active(False)
        self.hardware_expander.add_row(self.wifi_row)
        
        game_group.add(self.hardware_expander)
        
        # --- Audio Group ---
        audio_group = Adw.PreferencesGroup()
        audio_group.set_title(_('Audio'))
        audio_group.set_description(_('Sound settings'))

        # 1. Host Output (Always visible, serves as the "Host" part of Host+Guest)
        self.audio_output_row = Adw.ComboRow()
        self.audio_output_row.set_title(_('Host Audio Output'))
        self.audio_output_row.set_subtitle(_('Where YOU will hear the game sound'))
        self.audio_output_row.set_icon_name('audio-speakers-symbolic')
        self.audio_output_row.connect('notify::selected', self.on_audio_output_changed)
        audio_group.add(self.audio_output_row)

        # 2. Audio Mode ComboRow
        self.audio_mode_row = Adw.ComboRow()
        self.audio_mode_row.set_title(_('Audio Output Mode'))
        self.audio_mode_row.set_subtitle(_('Determine where the game sound will be played'))
        self.audio_mode_row.set_icon_name('audio-volume-medium-symbolic')
        
        mode_model = Gtk.StringList()
        mode_model.append(_('Automatic'))    # Index 0
        mode_model.append(_('Guest'))        # Index 1
        mode_model.append(_('Host'))         # Index 2
        mode_model.append(_('Guest + Host')) # Index 3
        
        self.audio_mode_row.set_model(mode_model)
        self.audio_mode_row.connect('notify::selected', self.on_audio_mode_changed)
        audio_group.add(self.audio_mode_row)


        
        # 3. Mixer (Only if Streaming is Enabled)
        self.audio_mixer_expander = Adw.ExpanderRow()
        self.audio_mixer_expander.set_title(_("Audio Mixer (Sources)"))
        self.audio_mixer_expander.set_subtitle(_("Manage audio sources"))
        self.audio_mixer_expander.set_icon_name('audio-volume-high-symbolic')
        self.audio_mixer_expander.set_visible(True) # Visibility controlled by switch
        
        audio_group.add(self.audio_mixer_expander)
        
        self.load_audio_outputs()
        
        self.advanced_expander = Adw.ExpanderRow()
        self.advanced_expander.set_title(_('Advanced Settings'))
        self.advanced_expander.set_subtitle(_('Input, Network, and Access'))
        self.advanced_expander.set_icon_name('preferences-system-symbolic')
        
        self.upnp_row = Adw.SwitchRow()
        self.upnp_row.set_title(_('Automatic UPnP'))
        self.upnp_row.set_subtitle(_('Automatically configure router ports'))
        self.upnp_row.set_active(True)
        self.advanced_expander.add_row(self.upnp_row)

        self.ipv6_row = Adw.SwitchRow()
        self.ipv6_row.set_title(_('Address Family (IPv4 + IPv6)'))
        self.ipv6_row.set_subtitle(_('Enable simultaneous IPv4 and IPv6 support on server'))
        self.ipv6_row.set_active(True)
        self.advanced_expander.add_row(self.ipv6_row)
        
        self.webui_anyone_row = Adw.SwitchRow()
        self.webui_anyone_row.set_title(_('Origin Web UI Allowed (WAN)'))
        self.webui_anyone_row.set_subtitle(_('Allows anyone to access the web interface (Anyone may access Web UI)'))
        self.webui_anyone_row.set_active(False)
        self.advanced_expander.add_row(self.webui_anyone_row)
        
        self.firewall_row = Adw.ActionRow()
        self.firewall_row.set_title(_("Configure Firewall (IPv6)"))
        self.firewall_row.set_subtitle(_("Open TCP/UDP ports required for external connection"))
        self.firewall_row.set_icon_name('network-workgroup-symbolic')
        
        fw_btn = Gtk.Button(label=_("Configure"))
        fw_btn.connect("clicked", self.on_configure_firewall_clicked)
        fw_btn.set_valign(Gtk.Align.CENTER)
        self.firewall_row.add_suffix(fw_btn)
        self.advanced_expander.add_row(self.firewall_row)
        
        game_group.add(self.advanced_expander)
        
        # Normal-sized header actions (live in the window header bar on the Server
        # page; the title is dropped there in favour of these).
        def _icon_label(icon_name, label_widget):
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            box.set_halign(Gtk.Align.CENTER)
            box.append(create_icon_widget(icon_name, size=14))
            box.append(label_widget)
            return box

        self.start_button = Gtk.Button()
        self.start_button.add_css_class('suggested-action')
        sb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        sb.set_halign(Gtk.Align.CENTER)
        self.start_btn_spinner = Gtk.Spinner()
        self.start_btn_spinner.set_visible(False)
        self.start_btn_icon = create_icon_widget('media-playback-start-symbolic', size=14)
        self.start_btn_label = Gtk.Label(label=_('Start Server'))
        sb.append(self.start_btn_spinner)
        sb.append(self.start_btn_icon)
        sb.append(self.start_btn_label)
        self.start_button.set_child(sb)
        self.start_button.connect('clicked', self.toggle_hosting)

        self.configure_button = Gtk.Button()
        self.configure_button.set_child(_icon_label('emblem-system-symbolic', Gtk.Label(label=_('Configure'))))
        self.configure_button.set_tooltip_text(_('Configure Sunshine'))
        self.configure_button.connect('clicked', self.open_sunshine_config)

        self.pin_button = Gtk.Button()
        self.pin_button.set_child(_icon_label('network-wireless-symbolic', Gtk.Label(label=_('Generate PIN'))))
        self.pin_button.set_tooltip_text(_('Generate a PIN for a guest'))
        self.pin_button.set_visible(False)
        self.pin_button.connect('clicked', self.open_pin_dialog)

        # Standalone box reparented into the header bar by the main window.
        self.header_action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.header_action_box.add_css_class('header-actions')
        for b in (self.start_button, self.configure_button, self.pin_button):
            self.header_action_box.append(b)

        self.create_summary_box()
        
        # View Switcher and Stack
        self.view_stack = Adw.ViewStack()
        self.view_stack.set_vexpand(False)
        
        # 1. Information Page — two columns (mockup 05): info + helper | management.
        info_left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        info_left.set_hexpand(True)
        info_left.append(self.summary_box)

        manage_group = Adw.PreferencesGroup()
        manage_group.set_title(_("Management"))
        manage_group.add_css_class('compact-rows')

        def _add_management_button(title: str, subtitle: str, icon_name: str, callback: Callable[[Gtk.Widget], None]) -> None:
            button = Gtk.Button()
            button.add_css_class("flat")
            button.add_css_class("management-row-button")
            button.set_halign(Gtk.Align.FILL)
            button.set_hexpand(True)
            button.connect("clicked", callback)
            button.update_property(
                [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
                [title, subtitle],
            )

            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.set_margin_top(10)
            row.set_margin_bottom(10)
            row.set_margin_start(14)
            row.set_margin_end(14)

            icon = create_icon_widget(icon_name, size=20)
            icon.set_valign(Gtk.Align.CENTER)
            row.append(icon)

            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            text.set_hexpand(True)
            title_label = Gtk.Label(label=title)
            title_label.add_css_class("management-row-title")
            title_label.set_halign(Gtk.Align.START)
            title_label.set_xalign(0)
            text.append(title_label)
            subtitle_label = Gtk.Label(label=subtitle)
            subtitle_label.add_css_class("management-row-subtitle")
            subtitle_label.set_halign(Gtk.Align.START)
            subtitle_label.set_xalign(0)
            subtitle_label.set_wrap(True)
            text.append(subtitle_label)
            row.append(text)

            arrow = create_icon_widget("go-next-symbolic", size=16)
            arrow.set_valign(Gtk.Align.CENTER)
            row.append(arrow)

            button.set_child(row)
            manage_group.add(button)

        _add_management_button(
            _("Paired Devices"),
            _("View, disable or remove paired clients"),
            "network-workgroup-symbolic",
            self.open_paired_devices_dialog,
        )
        _add_management_button(
            _("Sunshine Logs"),
            _("View the Sunshine server log"),
            "text-x-generic-symbolic",
            self.open_logs_dialog,
        )
        _add_management_button(
            _("Game Library"),
            _("Manage games shown to guests in Moonlight"),
            "applications-games-symbolic",
            self.open_game_library_dialog,
        )
        _add_management_button(
            _("Server Password"),
            _("Change or reset the Sunshine login (if you forgot it)"),
            "dialog-password-symbolic",
            self.open_password_dialog,
        )
        _add_management_button(
            _("Advanced server settings"),
            _("Server tuning, codecs, network and library fix"),
            "preferences-system-symbolic",
            self.open_advanced_settings,
        )

        # Getting-started tips, two side by side (no heading).
        def _tip(icon_name, title, desc):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.set_hexpand(True)
            row.add_css_class('helper-row')
            ic = create_icon_widget(icon_name, size=18)
            ic.add_css_class('accent')
            ic.set_valign(Gtk.Align.START)
            ic.set_margin_top(2)
            row.append(ic)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            text.set_hexpand(True)
            t = Gtk.Label(label=title)
            t.add_css_class('caption-heading')
            t.set_halign(Gtk.Align.START)
            t.set_wrap(True)
            text.append(t)
            d = Gtk.Label(label=desc)
            d.add_css_class('caption')
            d.add_css_class('dim-label')
            d.set_halign(Gtk.Align.START)
            d.set_wrap(True)
            d.set_max_width_chars(34)
            text.append(d)
            row.append(text)
            return row

        tips = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        tips.add_css_class('helper-card')
        tips.set_homogeneous(True)
        tips.append(_tip('dialog-password-symbolic', _('Share your PIN or address'),
                         _('Share your PIN code or the server address so friends can connect.')))
        tips.append(_tip('security-high-symbolic', _('Keep it secure'),
                         _('Keep your server and network secure. Use a VPN when sharing games.')))

        info_left.set_valign(Gtk.Align.START)
        info_right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        info_right.set_hexpand(True)
        info_right.set_valign(Gtk.Align.START)
        info_right.append(manage_group)

        # Two info columns on top, the tips banner spanning the full width below.
        info_columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        info_columns.append(info_left)
        info_columns.append(info_right)

        info_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        info_page.append(info_columns)
        info_page.append(tips)
        self.view_stack.add_titled_with_icon(info_page, "info", _("Information"), "dialog-information-symbolic")
        
        # 2. Configuration Page
        config_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        config_page.append(game_group)
        self.view_stack.add_titled_with_icon(config_page, "config", _("Game"), "preferences-system-symbolic")
        
        # 3. Audio Page
        audio_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        audio_page.append(audio_group)
        self.view_stack.add_titled_with_icon(audio_page, "audio", _("Audio"), "audio-x-generic-symbolic")

        # 4. PIN Code Page
        pin_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        pin_group = Adw.PreferencesGroup()
        pin_group.set_title(_("Use PIN Code to Connect"))
        pin_group.set_description(_("Share this with the guest. Local network is required."))
        
        pin_row = Adw.ActionRow()
        pin_row.set_title(_("PIN Code"))
        pin_row.set_icon_name("dialog-password-symbolic")
        
        self.pin_display_label = Gtk.Label(label="000000")
        self.pin_display_label.add_css_class("title-1")
        self.pin_display_label.set_selectable(True)
        
        copy_btn = Gtk.Button()
        copy_btn.set_child(create_icon_widget("edit-copy-symbolic", size=16))
        copy_btn.add_css_class("flat")
        copy_btn.set_valign(Gtk.Align.CENTER)
        copy_btn.set_tooltip_text(_("Copy PIN"))
        copy_btn.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            [_("Copy PIN"), _("Copy PIN code to clipboard")],
        )
        copy_btn.connect("clicked", lambda b: self.copy_field_value('pin'))
        
        pin_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        pin_box.append(self.pin_display_label)
        pin_box.append(copy_btn)
        
        pin_row.add_suffix(pin_box)
        pin_group.add(pin_row)
        pin_page.append(pin_group)
        self.view_stack.add_titled_with_icon(pin_page, "pin_code", _("PIN Code"), "dialog-password-symbolic")
        self.pin_page = pin_page
        self.pin_stack_page = self.view_stack.get_page(pin_page)
        self.pin_stack_page.set_visible(True) # Always visible
        self.pin_page.set_sensitive(False) # But blocked by default
        
        # Register PIN for updates (it was removed from summary_box)
        self.field_widgets['pin'] = {
            'label': self.pin_display_label,
            'real_value': '',
            'revealed': True
        }

        # Switcher setup
        view_switcher = Adw.InlineViewSwitcher()
        view_switcher.set_stack(self.view_stack)
        view_switcher.set_display_mode(Adw.InlineViewSwitcherDisplayMode.BOTH)
        view_switcher.set_halign(Gtk.Align.CENTER)
        view_switcher.set_margin_top(12)
        view_switcher.set_margin_bottom(12)
        
        # Status card (mockup 05): left state block + right metric tiles.
        # perf_monitor stays as a hidden data engine (its polling feeds the tiles).
        self.perf_monitor.set_visible(False)
        status_card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        status_card.add_css_class('info-card')

        # Left: circular icon + status text + subtitle + uptime chip.
        left_block = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        left_block.set_valign(Gtk.Align.CENTER)
        circle = Gtk.Box(); circle.add_css_class('status-icon-circle')
        circle.append(create_icon_widget('weather-clear-symbolic', size=26))
        circle.set_valign(Gtk.Align.CENTER)
        left_block.append(circle)

        text_block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        text_block.set_valign(Gtk.Align.CENTER)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.host_status_dot = create_icon_widget(
            'media-record-symbolic', size=12, css_class=['status-dot', 'status-offline'])
        self.host_status_dot.set_valign(Gtk.Align.CENTER)
        head.append(self.host_status_dot)
        self.host_status_label = Gtk.Label(label=_('Sunshine offline'))
        self.host_status_label.add_css_class('title-4')
        self.host_status_label.set_halign(Gtk.Align.START)
        head.append(self.host_status_label)
        text_block.append(head)
        self.host_status_subtitle = Gtk.Label(label=_('Start the server to stream.'))
        self.host_status_subtitle.add_css_class('caption')
        self.host_status_subtitle.add_css_class('dim-label')
        self.host_status_subtitle.set_halign(Gtk.Align.START)
        text_block.append(self.host_status_subtitle)
        self.host_uptime_label = Gtk.Label(label='')
        self.host_uptime_label.add_css_class('metric-chip')
        self.host_uptime_label.add_css_class('caption')
        self.host_uptime_label.set_halign(Gtk.Align.START)
        self.host_uptime_label.set_visible(False)
        text_block.append(self.host_uptime_label)
        left_block.append(text_block)
        status_card.append(left_block)

        # Right: three live metric tiles (hidden until hosting).
        self.tile_lat = MetricTile('network-transmit-receive-symbolic', _('Latency'),
                                   'metric-orange', (1.0, 0.45, 0.0, 1.0))
        self.tile_fps = MetricTile('video-display-symbolic', _('FPS'),
                                   'metric-green', (0.13, 0.76, 0.42, 1.0))
        self.tile_bw = MetricTile('network-wireless-symbolic', _('Bandwidth'),
                                  'metric-blue', (0.21, 0.52, 0.89, 1.0))
        self.host_metrics_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        self.host_metrics_box.set_halign(Gtk.Align.END)
        self.host_metrics_box.set_hexpand(True)
        for tile in (self.tile_lat, self.tile_fps, self.tile_bw):
            tile.set_size_request(118, -1)
            self.host_metrics_box.append(tile)
        self.host_metrics_box.set_visible(False)
        status_card.append(self.host_metrics_box)

        # Footer security reminder (mockup 05).
        footer_note = Gtk.Label()
        footer_note.set_markup(_(
            '<span size="small">Keep your server and network secure. '
            'Use a VPN when sharing games.</span>'))
        footer_note.add_css_class('dim-label')
        footer_note.set_halign(Gtk.Align.CENTER)
        footer_note.set_margin_top(12)

        content.append(status_card)
        content.append(view_switcher)
        content.append(self.view_stack)
        content.append(footer_note)

        clamp.set_child(content)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(clamp)
        self.append(scroll)
        
        self.start_audio_watchdog()

    def start_audio_watchdog(self):
        # Watchdog simplified or removed, as flow is explicitly controlled
        pass

    def _check_audio_state(self):
        return True

    def _get_sunshine_conf_path(self) -> Path:
        from pathlib import Path
        return Path.home() / '.config' / 'big-remoteplay' / 'sunshine' / 'sunshine.conf'

    def _get_sunshine_creds(self) -> tuple[str, str] | None:
        return load_sunshine_credentials(conf_path=self._get_sunshine_conf_path())

    def _save_sunshine_creds(self, user: str, password: str) -> bool:
        try:
            save_sunshine_credentials(user, password, conf_path=self._get_sunshine_conf_path())
            return True
        except SecretStoreUnavailable:
            self.show_toast(_("System keyring is unavailable. Password was not saved."))
        except Exception as e:
            print(f"Error saving Sunshine credentials: {e}")
        return False

    def _ensure_sunshine_config(self) -> None:
        """Ensures sunshine.conf has required API settings"""
        try:
            ensure_sunshine_api_config(conf_path=self._get_sunshine_conf_path())
        except Exception as e:
            print(f"Error ensuring sunshine config: {e}")

    def open_pin_dialog(self, _widget: Gtk.Widget) -> None:
        self._ensure_sunshine_config() # Ensure config before trying to use API
        
        # Load saved credentials from the system keyring when available.
        saved_creds = self._get_sunshine_creds()
        saved_user = saved_creds[0] if saved_creds else ''
        saved_pass = saved_creds[1] if saved_creds else ''
        
        dialog = Adw.MessageDialog(
            heading=_("Insert PIN"), 
            body=_("Enter the PIN displayed on the client device (Moonlight).")
        )
        dialog.set_transient_for(self._root_window())
        
        # Preferences group holding the fields
        grp = Adw.PreferencesGroup()
        
        pin_row = Adw.EntryRow(title=_("PIN"))
        
        name_row = Adw.EntryRow(title=_("Device Name"))
        name_row.set_text(socket.gethostname())
        
        user_row = Adw.EntryRow(title=_("Sunshine User"))
        if saved_user: user_row.set_text(saved_user)
        
        pass_row = Adw.PasswordEntryRow(title=_("Sunshine Password"))
        if saved_pass: pass_row.set_text(saved_pass)
        
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
        
        def on_response(d, r):
            if r == "ok":
                pin = pin_row.get_text().strip()
                device_name = name_row.get_text().strip()
                u = user_row.get_text().strip()
                p = pass_row.get_text().strip()
                save = save_chk.get_active()

                if not pin: return
                
                # Update saved credentials if requested
                if save and u and p:
                    self._save_sunshine_creds(u, p)
                
                auth = (u, p) if (u and p) else None
                success, msg = self.sunshine.send_pin(pin, name=device_name, auth=auth)
                
                if success:
                    self.show_toast(_("PIN sent successfully"))
                elif "Authentication Failed" in msg or "Falha de Autenticação" in msg or "401" in msg:
                    self.show_error_dialog(_("Authentication Failed"), _("Invalid username or password."))
                elif "307" in msg:
                    # A 307 Redirect means no user has been created yet
                    self.prompt_create_user(pin)
                else:
                    self.show_error_dialog(_("PIN Error"), msg)
                
        dialog.connect("response", on_response)
        dialog.present()
        
    def prompt_create_user(self, pin_retry):
        dialog = Adw.MessageDialog(
            heading=_("User Not Found"), 
            body=_("No user has been created in Sunshine. It is necessary to configure a user through the browser.")
        )
        dialog.set_transient_for(self._root_window())
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("open", _("Open Configuration"))
        dialog.set_response_appearance("open", Adw.ResponseAppearance.SUGGESTED)
        
        def on_resp(d, r):
            if r == "open":
                open_uri(self, "https://localhost:47990")

        dialog.connect("response", on_resp)
        dialog.present()
        
    def open_create_user_dialog(self, pin_retry):
        # This seems unused given the web prompt above, but keping for reference or alternative flow
        dialog = Adw.MessageDialog(
            heading=_("Create Sunshine User"), 
            body=_("Define a username and password for Sunshine.")
        )
        dialog.set_transient_for(self._root_window())
        
        grp = Adw.PreferencesGroup()
        user_row = Adw.EntryRow(title=_("New User"))
        user_row.set_text("admin")
        pass_row = Adw.PasswordEntryRow(title=_("New Password"))
        
        grp.add(user_row); grp.add(pass_row)
        dialog.set_extra_child(grp)
        
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("save", _("Save and Continue"))
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        
        def on_create(d, r):
            if r == "save":
                user = user_row.get_text().strip()
                pwd = pass_row.get_text().strip()
                if not user or not pwd: return
                
                success, msg = self.sunshine.create_user(user, pwd)
                if success:
                    self.show_toast(_("User created!"))
                    # Save creds since we just created them
                    self._save_sunshine_creds(user, pwd)
                    
                    ok, p_msg = self.sunshine.send_pin(pin_retry, auth=(user, pwd))
                    if ok: self.show_toast(_("PIN sent successfully"))
                    else: self.show_error_dialog(_("Error sending PIN after creation"), p_msg)
                else:
                    self.show_error_dialog(_("Error creating user"), msg)
                    
        dialog.connect("response", on_create)
        dialog.present()
        
    def open_sunshine_auth_dialog(self, pin_to_retry: str, device_name: str | None = None):
        # Prefill with existing if available (for correction)
        creds = self._get_sunshine_creds()
        curr_user = creds[0] if creds else "admin"
        
        dialog = Adw.MessageDialog(
            heading=_("Sunshine Authentication"), 
            body=_("Sunshine requires login. Enter your credentials (default: admin / password created during installation).")
        )
        dialog.set_transient_for(self._root_window())
        
        grp = Adw.PreferencesGroup()
        user_row = Adw.EntryRow(title=_("Username"))
        user_row.set_text(curr_user)
        pass_row = Adw.PasswordEntryRow(title=_("Password"))
        
        grp.add(user_row); grp.add(pass_row)
        dialog.set_extra_child(grp)
        
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("login", _("Confirm"))
        dialog.set_response_appearance("login", Adw.ResponseAppearance.SUGGESTED)
        
        def on_auth_resp(d, r):
            if r == "login":
                user = user_row.get_text().strip()
                pwd = pass_row.get_text().strip()
                if not user or not pwd: return
                
                # Tentar novamente com credenciais
                success, msg = self.sunshine.send_pin(pin_to_retry, name=device_name, auth=(user, pwd))
                if success: 
                    self.show_toast(_("PIN sent successfully"))
                    # Save credentials on success
                    self._save_sunshine_creds(user, pwd)
                else: 
                    self.show_error_dialog(_("Failed with credentials"), msg)
                
        dialog.connect("response", on_auth_resp)
        dialog.present()

    def create_summary_box(self):
        self.summary_box = Adw.PreferencesGroup()
        self.summary_box.set_title(_("Server Information"))
        self.summary_box.add_css_class('compact-rows')
        self.summary_box.set_visible(True); self.field_widgets = {}
        for l, k, i, r in [('Host', 'hostname', 'computer-symbolic', True), ('IPv4', 'ipv4', 'network-wired-symbolic', False), ('IPv6', 'ipv6', 'network-wired-symbolic', False), ('IPv4 Global', 'ipv4_global', 'network-transmit-receive-symbolic', False), ('IPv6 Global', 'ipv6_global', 'network-transmit-receive-symbolic', False)]: self.create_masked_row(l, k, i, r)

    def on_audio_mode_changed(self, row, param):
        if getattr(self, 'loading_settings', False): return
        
        idx = row.get_selected()
        # Mode Guest (1), Automatic (0) or Guest+Host (3) means mixer should be visible
        show_mixer = idx in [0, 1, 3]
        self.audio_mixer_expander.set_visible(show_mixer)
        
        if self.is_hosting:
            self._run_audio_enforcer()
        self.save_host_settings()


    def load_audio_outputs(self):

        try:
            from big_remote_play.utils.audio import AudioManager
            if not hasattr(self, 'audio_manager'):
                self.audio_manager = AudioManager()
            
            devices = self.audio_manager.get_passive_sinks()
            self.audio_devices = devices
            
            model = Gtk.StringList()
            if not devices:
                model.append(_("System Default"))
            else:
                for dev in devices:
                    model.append(dev.get('description', dev.get('name', 'Unknown')))
            
            self.audio_output_row.set_model(model)
            # Try to keep selection if possible, or use config
            h = self.config.get('host', {})
            if not isinstance(h, dict):
                h = {}
            self.audio_output_row.set_selected(h.get('audio_output_idx', 0))
            
        except Exception as e:
            print(f"Error loading audio: {e}")
            model = Gtk.StringList()
            model.append(_("Error loading audio"))
            self.audio_output_row.set_model(model)

    def on_audio_output_changed(self, row, param):
        if getattr(self, 'loading_settings', False): return
        
        idx = row.get_selected()
        if self.audio_devices and 0 <= idx < len(self.audio_devices):
            new_sink = self.audio_devices[idx]['name']
        else:
            new_sink = self.audio_manager.get_default_sink()
        if not new_sink:
            self.save_host_settings()
            return
        
        # If hosting and audio active, need to reconfigure loopback
        if self.is_hosting and self.audio_mode_row.get_selected() in [0, 1, 3]:
            if hasattr(self, 'active_host_sink') and self.active_host_sink != new_sink:
                print(f"Changing host output in real-time to: {new_sink}")
                self.active_host_sink = new_sink
                # Restart audio streaming to change loopback destination
                self.audio_manager.enable_streaming_audio(new_sink)
                self.show_toast(_("Output changed to: {}").format(new_sink))
        
        self.save_host_settings()

    def on_configure_firewall_clicked(self, _widget):
        self.show_toast(_("Configuring firewall... (Password may be requested)"))
        
        try:
            # Resolve the bundled script (installed /usr/share path, dev fallback)
            script_path = paths.script_path('configure_firewall.sh')

            if not os.path.exists(script_path):
                self.show_error_dialog(_("Error"), f"Script not found: {script_path}")
                return

            # Run with pkexec
            cmd = ['pkexec', script_path]
            
            def on_done(ok, out):
                if ok: self.show_toast(_("Success: {}").format(out.strip()))
                else: self.show_error_dialog(_("Firewall Error"), out if out else _("Execution failed or cancelled."))
            
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

    def create_masked_row(self, title: str, key: str, icon_name: str = 'text-x-generic-symbolic', default_revealed: bool = False) -> None:
        row = Adw.ActionRow()
        row.set_title(title)
        row.add_prefix(create_icon_widget(icon_name, size=16))
        
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_valign(Gtk.Align.CENTER)
        
        value_lbl = Gtk.Label(label='••••••' if not default_revealed else '')
        value_lbl.set_margin_end(8)
        
        eye_btn = Gtk.Button()
        eye_btn.set_child(create_icon_widget('view-reveal-symbolic' if not default_revealed else 'view-conceal-symbolic', size=16))
        eye_btn.add_css_class('flat')
        eye_btn.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            [
                _("Hide {}").format(title) if default_revealed else _("Reveal {}").format(title),
                _("Show or hide the {} value").format(title),
            ],
        )
        copy_btn = Gtk.Button()
        copy_btn.set_child(create_icon_widget('edit-copy-symbolic', size=16))
        copy_btn.add_css_class('flat')
        copy_btn.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            [_("Copy {}").format(title), _("Copy the {} value to clipboard").format(title)],
        )
        
        box.append(value_lbl); box.append(eye_btn); box.append(copy_btn)
        row.add_suffix(box)
        self.summary_box.add(row)
        
        self.field_widgets[key] = {'label': value_lbl, 'real_value': '', 'revealed': default_revealed, 'btn_eye': eye_btn, 'title': title}
        eye_btn.connect('clicked', lambda b: self.toggle_field_visibility(key))
        copy_btn.connect('clicked', lambda b: self.copy_field_value(key))
        
    def toggle_field_visibility(self, key: str) -> None:
        field = self.field_widgets[key]
        field['revealed'] = not field['revealed']
        title = field.get('title', _("value"))
        field['btn_eye'].set_child(create_icon_widget('view-conceal-symbolic' if field['revealed'] else 'view-reveal-symbolic', size=16))
        field['btn_eye'].update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Hide {}").format(title) if field['revealed'] else _("Reveal {}").format(title)],
        )
        field['label'].set_text(field['real_value'] if field['revealed'] else '••••••')
            
    def copy_field_value(self, key):
        if val := self.field_widgets[key]['real_value']:
             display = Gdk.Display.get_default()
             if display is not None:
                 display.get_clipboard().set(val)
             self.show_toast(_("Copied!"))

    def toggle_hosting(self, button):
        self.show_toast(_("Clicked Start Server..."))
        self.start_button.set_sensitive(False)
        self.start_btn_spinner.set_visible(True)
        self.start_btn_spinner.start()
        
        # Defer action slightly to allow UI to paint
        GLib.timeout_add(100, self._perform_toggle_hosting)

    def _perform_toggle_hosting(self):
        if self.is_hosting: self.stop_hosting()
        else: self.start_hosting()
        return False
            
    def sync_ui_state(self):
        if self.is_hosting:
            self.perf_monitor.set_connection_status("Sunshine", _("Active - Waiting for Connections"), True)
            self.perf_monitor.start_monitoring()

            # Reveal the live metric tiles; update the status subtitle.
            if hasattr(self, 'host_metrics_box'):
                self.host_metrics_box.set_visible(True)
                self.host_status_subtitle.set_label(_('Ready to receive connections.'))
                self._show_share_hint()

            # Status card header: active state + uptime baseline + 1s ticker.
            if getattr(self, '_hosting_started_at', None) is None:
                self._hosting_started_at = time.monotonic()
            if hasattr(self, 'host_status_label'):
                self.host_status_label.set_label(_('Sunshine active'))
                self.host_status_dot.remove_css_class('status-offline')
                self.host_status_dot.add_css_class('status-online')
                self.host_uptime_label.set_visible(True)
                self._tick_uptime()
                if getattr(self, '_uptime_timer_id', None) is None:
                    self._uptime_timer_id = GLib.timeout_add_seconds(1, self._tick_uptime)

            # Button State: Hosting -> Stop
            self.start_btn_label.set_label(_('Stop server'))
            if hasattr(self, 'start_btn_icon'): set_icon(self.start_btn_icon, 'media-playback-stop-symbolic')
            self.start_button.remove_css_class('suggested-action')
            self.start_button.add_css_class('destructive-action')
            self.start_btn_spinner.set_visible(False)
            self.start_btn_spinner.stop()

            self.configure_button.set_sensitive(True)
            self.configure_button.add_css_class('suggested-action')
            for r in [self.game_mode_row, self.hardware_expander, self.streaming_expander, self.advanced_expander]: r.set_sensitive(False)
            
            if hasattr(self, 'pin_button'): self.pin_button.set_visible(True)
            if hasattr(self, 'summary_box'):
                self.summary_box.set_visible(True)
                self.populate_summary_fields()
            
            # Enable PIN tab when hosting
            if hasattr(self, 'pin_page'):
                self.pin_page.set_sensitive(True)
                self.pin_stack_page.set_icon_name("dialog-password-symbolic")
        else:
            self.perf_monitor.set_connection_status("Sunshine", _("Inactive"), False)
            self.perf_monitor.stop_monitoring()

            # Hide the metric tiles; the status block shows the offline prompt.
            if hasattr(self, 'host_metrics_box'):
                self.host_metrics_box.set_visible(False)
                self.host_status_subtitle.set_label(_('Start the server to stream.'))

            self._hosting_started_at = None
            uptime_timer_id = self._uptime_timer_id
            if uptime_timer_id is not None:
                GLib.source_remove(uptime_timer_id)
                self._uptime_timer_id = None
            if hasattr(self, 'host_status_label'):
                self.host_status_label.set_label(_('Sunshine offline'))
                self.host_status_dot.remove_css_class('status-online')
                self.host_status_dot.add_css_class('status-offline')
                self.host_uptime_label.set_visible(False)
            
            if hasattr(self, 'pin_button'): self.pin_button.set_visible(False)
            if hasattr(self, 'summary_box'):
                self.summary_box.set_visible(True)
                self.populate_summary_fields()
            
            # Disable PIN tab when not hosting
            if hasattr(self, 'pin_page'):
                self.pin_page.set_sensitive(False)
                self.pin_stack_page.set_icon_name("changes-prevent-symbolic")
                
                # Switch to info tab if we were on PIN tab and it's now blocked
                if self.view_stack.get_visible_child_name() == "pin_code":
                    self.view_stack.set_visible_child_name("info")
            
            # Button State: Stopped -> Start
            self.start_btn_label.set_label(_('Start Server'))
            if hasattr(self, 'start_btn_icon'): set_icon(self.start_btn_icon, 'media-playback-start-symbolic')
            self.start_button.remove_css_class('destructive-action')
            self.start_button.add_css_class('suggested-action')
            self.start_btn_spinner.set_visible(False)
            self.start_btn_spinner.stop()
            
            self.configure_button.set_sensitive(False)
            self.configure_button.remove_css_class('suggested-action')
            for r in [self.game_mode_row, self.hardware_expander, self.streaming_expander, self.advanced_expander]: r.set_sensitive(True)

    def populate_summary_fields(self):
        import socket, threading
        from big_remote_play.utils.network import NetworkDiscovery
        self.update_field('hostname', socket.gethostname())
        if self.pin_code: self.update_field('pin', self.pin_code)
        ipv4, ipv6 = self.get_ip_addresses()
        self.update_field('ipv4', ipv4); self.update_field('ipv6', ipv6)
        
        def fetch_globals():
            net = NetworkDiscovery()
            g_ipv4 = net.get_global_ipv4(); g_ipv6 = net.get_global_ipv6()
            
            # Wrap IPv6 in brackets for compatibility
            if g_ipv6 and g_ipv6 != "None" and ':' in g_ipv6 and not g_ipv6.startswith('['):
                g_ipv6 = f"[{g_ipv6}]"
                
            GLib.idle_add(self.update_field, 'ipv4_global', g_ipv4)
            GLib.idle_add(self.update_field, 'ipv6_global', g_ipv6)
        threading.Thread(target=fetch_globals, daemon=True).start()
        
    def update_field(self, key, value):
        if key in self.field_widgets:
            self.field_widgets[key]['real_value'] = value
            if self.field_widgets[key]['revealed']: self.field_widgets[key]['label'].set_text(value)

    def start_audio_mixer_refresh(self):
        self.stop_audio_mixer_refresh()
        self.private_audio_apps = set() # Track names of private apps (unchecked in UI)
        self.mixer_source_id = GLib.timeout_add(2000, self._refresh_audio_mixer_ui)
        self.enforcer_source_id = GLib.timeout_add(1000, self._run_audio_enforcer)
        self._refresh_audio_mixer_ui()
        return True

    def stop_audio_mixer_refresh(self):
        if hasattr(self, 'mixer_source_id'):
            GLib.source_remove(self.mixer_source_id)
            del self.mixer_source_id
        if hasattr(self, 'enforcer_source_id'):
            GLib.source_remove(self.enforcer_source_id)
            del self.enforcer_source_id

    def _run_audio_enforcer(self):
        if not self.is_hosting: return True
        if not hasattr(self, 'active_host_sink') or not self.active_host_sink: return True
        
        shared_sink = "SunshineGameSink"
        private_sink = self.active_host_sink
        
        # Determine behavior based on Audio Mode Selection
        # 0: Automatic, 1: Guest, 2: Host, 3: Guest + Host
        mode_idx = self.audio_mode_row.get_selected()
        
        streaming_enabled = mode_idx in [0, 1, 3]
        
        # Audio Monitoring logic
        should_monitor = False
        if mode_idx == 3: # Guest + Host
            should_monitor = True
        elif mode_idx == 2: # Host Only
            should_monitor = True # Doesn't matter much as everything moves to private_sink
            streaming_enabled = False
        elif mode_idx == 1: # Guest Only
            should_monitor = False
        elif mode_idx == 0: # Automatic
            # Check for localhost guest
            has_localhost = False
            if hasattr(self, 'perf_monitor'):
                for guest in getattr(self.perf_monitor, '_known_devices', {}).values():
                     if guest.get('status') == 'active' or (time.time() - guest.get('last_seen', 0) < 5):
                          ip = guest.get('ip', '')
                          if ip in ['127.0.0.1', '::1', 'localhost']:
                               has_localhost = True; break
            should_monitor = not has_localhost

        if hasattr(self, 'audio_manager'):
            try:
                # Update loopback
                if not hasattr(self, '_last_monitor_state') or self._last_monitor_state != should_monitor:
                    self.audio_manager.set_host_monitoring(private_sink, should_monitor)
                    self._last_monitor_state = should_monitor

                # Default sink hijack fix
                current_default = self.audio_manager.get_default_sink()
                if current_default and current_default != private_sink:
                    if "sunshine" in current_default.lower() and "stereo" in current_default.lower():
                        self.audio_manager.set_default_sink(private_sink)

                apps = self.audio_manager.get_apps()
                for app in apps:
                    app_id, name = app['id'], app.get('name', '')
                    if 'sunshine' in name.lower() or 'loopback' in name.lower() or 'moonlight' in name.lower(): continue
                    
                    target = private_sink if (not streaming_enabled or name in self.private_audio_apps) else shared_sink
                    if app.get('sink_name', '') != target:
                        print(f"Enforcer: Moving {name} -> {target}")
                        self.audio_manager.move_app(app_id, target)
            except Exception as e:
                print(f"Enforcer Error: {e}")
        return True



    def _refresh_audio_mixer_ui(self):
        if not self.audio_mixer_expander.get_visible(): return True
        if not hasattr(self, 'audio_manager'): return True
        
        apps = self.audio_manager.get_apps()
        seen_ids = set()
        
        if not hasattr(self, 'mixer_rows'): self.mixer_rows = {}
        
        for app in apps:
            app_id = app['id']
            app_name = app.get('name', 'App')
            seen_ids.add(app_id)
            
            # Default state: Active (Shared) unless explicitly set to Private
            is_shared = (app_name not in self.private_audio_apps)
            
            if app_id in self.mixer_rows:
                row = self.mixer_rows[app_id]
                # Avoid signal loop
                if row.get_active() != is_shared:
                    row.disconnect_by_func(self._on_app_toggled)
                    row.set_active(is_shared)
                    row.connect('notify::active', self._on_app_toggled, app_name)
                
                row.set_subtitle(_("Host + Guest") if is_shared else _("Host Only"))
            else:
                row = Adw.SwitchRow()
                row.set_title(app_name)
                row.set_subtitle(_("Host + Guest") if is_shared else _("Host Only"))
                if app.get('icon'): row.set_icon_name(app['icon'])
                row.set_active(is_shared)
                row.connect('notify::active', self._on_app_toggled, app_name)
                self.audio_mixer_expander.add_row(row)
                self.mixer_rows[app_id] = row
                
        # Cleanup
        to_remove = [aid for aid in self.mixer_rows if aid not in seen_ids]
        for aid in to_remove:
            self.audio_mixer_expander.remove(self.mixer_rows[aid])
            del self.mixer_rows[aid]
            
        return True

    def _on_app_toggled(self, row, param, app_name):
        is_shared = row.get_active()
        if is_shared:
            if app_name in self.private_audio_apps:
                self.private_audio_apps.remove(app_name)
        else:
            self.private_audio_apps.add(app_name)
            
        row.set_subtitle(_("Host + Guest") if is_shared else _("Host Only"))
        self._run_audio_enforcer()
             
    def start_hosting(self, b=None):
        self.loading_bar.set_visible(True); self.loading_bar.pulse()
            
        try:
            if self.sunshine.is_running():
                self.sunshine.stop()
                import time; time.sleep(1)
            
            self.pin_code = ''.join(random.choices(string.digits, k=6))
            from big_remote_play.utils.network import NetworkDiscovery
            self.stop_pin_listener = NetworkDiscovery().start_pin_listener(self.pin_code, socket.gethostname())
            
            mode_idx = self.game_mode_row.get_selected()
            
            # Always use Desktop mode in apps.json - we launch games directly
            self.sunshine.update_apps([{"name": "Desktop", "output": "", "cmd": "", "detached": ["sleep infinity"]}])
            
            # Store game launch info to execute AFTER Sunshine starts
            self._game_launch_info = None
            self._game_processes = []
            
            if mode_idx == 1:  # Steam
                idx = self.game_list_row.get_selected()
                if idx != Gtk.INVALID_LIST_POSITION:
                    games = self.detected_games.get('Steam', [])
                    if 0 <= idx < len(games):
                        game = games[idx]
                        app_id = game.get('id', '')
                        self._game_launch_info = {
                            'type': 'steam',
                            'app_id': app_id,
                            'name': game['name']
                        }
            elif mode_idx == 2:  # Lutris
                idx = self.game_list_row.get_selected()
                if idx != Gtk.INVALID_LIST_POSITION:
                    games = self.detected_games.get('Lutris', [])
                    if 0 <= idx < len(games):
                        game = games[idx]
                        self._game_launch_info = {
                            'type': 'lutris',
                            'cmd': game['cmd'],
                            'name': game['name']
                        }
            elif mode_idx == 3:  # Custom App
                name = self.custom_name_entry.get_text(); cmd = self.custom_cmd_entry.get_text()
                if name and cmd:
                    self._game_launch_info = {
                        'type': 'custom',
                        'cmd': cmd,
                        'name': name
                    }
                     
            # Determine FPS
            fps_idx = self.fps_row.get_selected()
            fps_map = {0: 30, 1: 60, 2: 120, 3: 144, 4: 60} # Custom defaults to 60
            fps = fps_map.get(fps_idx, 60)
            
            # Determine Bitrate (Kbps)
            # Sunshine uses min_bitrate in config, but we can pass 'bitrate' (CBR target) here if needed.
            # However, Sunshine v0.20+ generally prefers min_bitrate in config for VBR floor.
            # If bandwidth_row > 0, set bitrate. Else default.
            bw_mbps = self.bandwidth_row.get_value()
            bitrate = int(bw_mbps * 1000) if bw_mbps > 0 else 20000 # Default 20Mbps if unlim
            
            selected_gpu_info = self.available_gpus[self.gpu_row.get_selected()]
            sunshine_config = {
                'sunshine_name': socket.gethostname(),
                'encoder': selected_gpu_info['encoder'], 'bitrate': bitrate, 'fps': fps,
                'videocodec': 'h264', 'gamepad': 'x360', 'min_threads': 4, 
                'min_log_level': 2, # Info level to see connections
                'channels': 2, # Force Stereo
                'pkey': 'pkey.pem', 'cert': 'cert.pem', 
                'upnp': 'enabled' if self.upnp_row.get_active() else 'disabled',
                'address_family': 'both' if self.ipv6_row.get_active() else 'ipv4',
                'origin_web_ui_allowed': 'wan' if self.webui_anyone_row.get_active() else 'lan',
                'webserver': '0.0.0.0',
                'enable_api_endpoints': 'true',
                'port': '47989'
            }
            

            # Audio Configuration
            # Audio Configuration - ALWAYS setup PulseAudio infrastructure
            # This allows toggling streaming on/off without restarting server or destroying sinks
            sunshine_config['audio'] = 'pulse'
            
            # Identify Host Sink
            host_sink_idx = self.audio_output_row.get_selected()
            if hasattr(self, 'audio_devices') and 0 <= host_sink_idx < len(self.audio_devices):
                host_sink = self.audio_devices[host_sink_idx]['name']
            else:
                host_sink = self.audio_manager.get_default_sink()
            
            # PROTECT AGAINST SELF-LOOP IF DEFAULT SINK IS STILL VIRTUAL
            if host_sink and (host_sink == "SunshineGameSink" or self.audio_manager.is_virtual(host_sink)):
                print(f"WARNING: Host sink '{host_sink}' is virtual. finding fallback hardware sink.")
                hw_sinks = self.audio_manager.get_passive_sinks()
                if hw_sinks:
                    host_sink = hw_sinks[0]['name'] # or 'id' depending on get_passive_sinks return
                    # usually get_passive_sinks returns dict with 'name' (id) and description
                    # wait, check utils/audio.py get_passive_sinks returns dict with 'name' -> PA Name
                    
            if not host_sink:
                print("WARNING: No hardware audio sink found, disabling audio streaming.")
                sunshine_config['audio'] = 'none'
            else:
                # Store it for the enforcer
                self.active_host_sink = host_sink

            # Enable Host+Guest Streaming (Create Sink)
            if host_sink and self.audio_manager:
                # Determine loopback based on Mode
                mode_idx = self.audio_mode_row.get_selected()
                # If mode is Guest (1), guest_only is True
                # If mode is Guest+Host (3), guest_only is False
                # If mode is Automatic (0), we start with guest_only=False (will be auto-muted by enforcer if needed)
                guest_only = (mode_idx == 1)
                
                if self.audio_manager.enable_streaming_audio(host_sink, guest_only=guest_only):
                    sunshine_config['audio_sink'] = "SunshineGameSink"
                    
                    self._last_monitor_state = not guest_only
                    self.start_audio_mixer_refresh()

                    GLib.timeout_add(500, lambda: (self.audio_manager.set_default_sink(host_sink), self.show_toast(_("Host output restored: {}").format(host_sink)))[1])
                else:


                     print("Failed to enable streaming sinks, falling back to default")
                     self.show_toast(_("Failed to create Virtual Audio"))
                     # Fallback to none if creation failed
                     sunshine_config['audio'] = 'none' 
                     self.audio_manager.disable_streaming_audio(None)

            platforms = ['auto', 'wayland', 'x11', 'kms']
            platform = platforms[self.platform_row.get_selected()]
            if platform == 'auto':
                session = os.environ.get('XDG_SESSION_TYPE', '').lower()
                platform = 'wayland' if session == 'wayland' else 'x11'
            sunshine_config['platform'] = platform


            # Set output_name if a specific monitor is selected, others set to None to remove from config
            sunshine_config['output_name'] = None
            monitor_idx = self.monitor_row.get_selected()
            if 0 < monitor_idx < len(self.available_monitors):
                mon_name = self.available_monitors[monitor_idx][1]
                if mon_name != 'auto':
                    sunshine_config['output_name'] = mon_name
            
            # Set adapter_name only if specific adapter chosen
            sunshine_config['adapter_name'] = None
            if selected_gpu_info['encoder'] == 'vaapi' and selected_gpu_info['adapter'] != 'auto':
                sunshine_config['adapter_name'] = selected_gpu_info['adapter']
            
            if platform == 'wayland':
                sunshine_config['wayland.display'] = os.environ.get('WAYLAND_DISPLAY', 'wayland-0')
            if platform == 'x11' and (not sunshine_config['output_name']):
                sunshine_config['output_name'] = ':0'
            
            self.sunshine.configure(sunshine_config)
            success, msg = self.sunshine.start()
            
            if success:
                self.is_hosting = True
                self.sync_ui_state()
                self.show_toast(_('Server started'))
                
                # DIRECT LAUNCH: Open game/platform immediately
                self._launch_game_direct()
            else:
                self.is_hosting = False
                self.sync_ui_state()
                self.show_start_error_dialog(msg)
            
        except Exception as e:
            self.show_error_dialog(_('Error'), str(e))
            self.is_hosting = False
            self.sync_ui_state() # Revert state
        finally: 
            self.loading_bar.set_visible(False)
            self.start_button.set_sensitive(True) # Re-enable button
            self.start_btn_spinner.stop()
            self.start_btn_spinner.set_visible(False)
        
    def _launch_game_direct(self):
        """Directly launch game/platform via subprocess - radical approach"""
        info = getattr(self, '_game_launch_info', None)
        if not info:
            print("Game Mode: Desktop (no game to launch)")
            return
        
        if not hasattr(self, '_game_processes'):
            self._game_processes = []
            
        env = os.environ.copy()
        
        try:
            if info['type'] == 'steam':
                app_id = info['app_id']
                game_name = info['name']
                print(f"DIRECT LAUNCH: Steam Big Picture + {game_name} (ID: {app_id})")
                
                # 1. Open Steam Big Picture Mode
                p1 = subprocess.Popen(
                    ['steam', 'steam://open/bigpicture'],
                    env=env, start_new_session=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                self._game_processes.append(p1)
                self.show_toast(_("Opening Steam Big Picture..."))
                
                # 2. Launch the game after a delay (give Big Picture time to open)
                def _delayed_game_launch():
                    import time
                    time.sleep(4)
                    try:
                        p2 = subprocess.Popen(
                            ['steam', f'steam://rungameid/{app_id}'],
                            env=env, start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                        )
                        self._game_processes.append(p2)
                        GLib.idle_add(self.show_toast, _("Launching {}...").format(game_name))
                        print(f"DIRECT LAUNCH: Game {game_name} launched (PID: {p2.pid})")
                    except Exception as e:
                        print(f"Error launching game: {e}")
                        GLib.idle_add(self.show_toast, _("Error launching game: {}").format(e))
                
                threading.Thread(target=_delayed_game_launch, daemon=True).start()
                    
            elif info['type'] == 'lutris':
                cmd = info['cmd']
                game_name = info['name']
                print(f"DIRECT LAUNCH: Lutris - {game_name} ({cmd})")
                argv = _split_launch_command(cmd)
                if not argv:
                    self.show_toast(_("Invalid launch command"))
                    return
                
                p = subprocess.Popen(
                    argv,
                    env=env, start_new_session=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                self._game_processes.append(p)
                self.show_toast(_("Launching {}...").format(game_name))
                    
            elif info['type'] == 'custom':
                cmd = info['cmd']
                game_name = info['name']
                print(f"DIRECT LAUNCH: Custom - {game_name} ({cmd})")
                argv = _split_launch_command(cmd)
                if not argv:
                    self.show_toast(_("Invalid launch command"))
                    return
                
                p = subprocess.Popen(
                    argv,
                    env=env, start_new_session=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                self._game_processes.append(p)
                self.show_toast(_("Launching {}...").format(game_name))
                    
        except Exception as e:
            print(f"Error in _launch_game_direct: {e}")
            self.show_toast(_("Error launching game: {}").format(e))
    
    def _stop_game_direct(self):
        """Kill any directly launched game processes"""
        info = getattr(self, '_game_launch_info', None)
        
        # Close Steam Big Picture if we opened it
        if info and info.get('type') == 'steam':
            try:
                subprocess.Popen(
                    ['steam', 'steam://close/bigpicture'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                print("Closing Steam Big Picture")
            except Exception: pass
        
        # Kill tracked processes
        for p in getattr(self, '_game_processes', []):
            try:
                if p.poll() is None:  # Still running
                    import signal
                    os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            except Exception: pass
        
        self._game_processes = []
        self._game_launch_info = None

    def stop_hosting(self, b=None) -> None:
        self.show_toast(_("Stopping server..."))
        self.loading_bar.set_visible(True); self.loading_bar.pulse()
        
        # Stop directly launched games FIRST
        self._stop_game_direct()
        # Update mixer visibility based on mode
        self.audio_mixer_expander.set_visible(self.audio_mode_row.get_selected() in [0, 1, 3])
        self.stop_audio_mixer_refresh()
        
        if hasattr(self, 'stop_pin_listener') and self.stop_pin_listener:
            try: self.stop_pin_listener()
            except Exception: pass
            self.stop_pin_listener = None
            
        # Restore audio configuration
        if hasattr(self, 'audio_manager') and hasattr(self, 'active_host_sink') and self.active_host_sink:
            try:
                self.audio_manager.disable_streaming_audio(self.active_host_sink)
            except Exception as e:
                print(f"Error restoring audio: {e}")
            
        try:
            self.sunshine.stop()
        except Exception as e:
            print(f"Error stopping Sunshine: {e}")
            
        self.is_hosting = False
        self.sync_ui_state()
        self.loading_bar.set_visible(False)
        self.start_button.set_sensitive(True) 
        self.start_btn_spinner.stop()
        self.start_btn_spinner.set_visible(False)
        self.show_toast(_('Server stopped'))
        
    def _show_share_hint(self):
        """Once hosting, tell the host exactly what to give friends (LAN address)."""
        def work():
            ipv4, _ipv6 = self.get_ip_addresses()
            GLib.idle_add(self._set_share_hint, ipv4)
        threading.Thread(target=work, daemon=True).start()

    def _set_share_hint(self, ipv4):
        if not self.is_hosting or not hasattr(self, 'host_status_subtitle'):
            return False
        if ipv4 and ipv4 != "None":
            # Plain next step: what to hand a friend so they can connect.
            self.host_status_subtitle.set_label(
                _('Friends connect to this PC at {} — or use a PIN code.').format(ipv4))
        return False

    def _tick_uptime(self):
        """Refresh the status-card uptime chip ("Server active for HH:MM:SS")."""
        started = getattr(self, '_hosting_started_at', None)
        if started is None or not hasattr(self, 'host_uptime_label'):
            self._uptime_timer_id = None
            return False
        elapsed = int(time.monotonic() - started)
        hh, rem = divmod(elapsed, 3600)
        mm, ss = divmod(rem, 60)
        self.host_uptime_label.set_label(
            _('Server active for {:02d}:{:02d}:{:02d}').format(hh, mm, ss))
        self._refresh_metric_tiles()
        return True

    def _refresh_metric_tiles(self):
        """Pull live values from the perf monitor into the status-card tiles."""
        chart = getattr(self.perf_monitor, 'chart', None)
        if chart is None or not hasattr(self, 'tile_lat'):
            return
        history = list(chart._history)
        if not history:
            return

        def norm(values, ceiling):
            top = max(1.0, ceiling)
            return [v / top for v in values]

        self.tile_lat.update(
            norm([p.latency for p in history], chart.max_latency), chart._cur_latency_text)
        self.tile_fps.update(
            norm([p.fps for p in history], chart.max_fps), chart._cur_fps_text)
        self.tile_bw.update(
            norm([p.bandwidth for p in history], chart.max_bandwidth), chart._cur_bw_text)

    def update_status_info(self):
        sunshine_running = self.check_process_running('sunshine')
        
        # If it was supposed to be hosting but sunshine is not running
        if self.is_hosting and not sunshine_running:
             self.is_hosting = False
             self.sync_ui_state()
             self.show_toast(_("Sunshine stopped unexpectedly"))
             return True

        if not self.is_hosting: return True

        sunshine_val = getattr(self, 'sunshine_val', None)
        if sunshine_val is not None:
            status_text = _("Online") if sunshine_running else _("Stopped")
            color = "#2ec27e" if sunshine_running else "#e01b24"
            sunshine_val.set_markup(f'<span color="{color}">{status_text}</span>')
        ipv4, ipv6 = self.get_ip_addresses()
        self.update_field('ipv4', ipv4); self.update_field('ipv6', ipv6)
        return True
        
    def check_process_running(self, process_name):
        try:
            subprocess.check_output(["pgrep", "-x", process_name], timeout=5)
            return True
        except Exception: return False
            
    def get_ip_addresses(self):
        ipv4 = ipv6 = "None"
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("1.1.1.1", 80)); ipv4 = s.getsockname()[0]
        except Exception: pass
        try:
            res = subprocess.run(['ip', '-j', 'addr'], capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                for iface in json.loads(res.stdout):
                    name = iface['ifname']
                    # Pular interfaces de loopback, desligadas ou virtuais conhecidas
                    if name == 'lo' or 'UP' not in iface['flags']: continue
                    if any(x in name for x in ['docker', 'veth', 'virbr', 'vboxnet', 'tailscale', 'zerotier', 'br-']): continue
                    for addr in iface.get('addr_info', []):
                        if addr['family'] == 'inet':
                            if ipv4 == "None": ipv4 = addr['local']
                        elif addr['family'] == 'inet6':
                            # Prioritize global but accept link-local
                            if addr.get('scope') == 'global':
                                ipv6 = addr['local']
                                break # Found global, stop searching for this interface
                            elif ipv6 == "None":
                                # Fallback to link-local with scope ID
                                ipv6 = f"{addr['local']}%{name}"
        except Exception: pass

        
        # No longer wrapping in brackets as per user feedback
            
        return ipv4, ipv6
        
    def show_start_error_dialog(self, message):
        if not message: message = _("Check logs for details.")
        
        body = _("Sunshine failed to start.\n\nError: {}\n\nIf this is a dependency issue (missing libraries), try the 'Fix Dependencies' button.").format(message)
        
        # Use simple MessageDialog constructor for custom response handling if needed, 
        # or simplified new() if we connect signal later.
        dialog = Adw.MessageDialog(heading=_("Server Failed to Start"), body=body)
        dialog.set_transient_for(self._root_window())
        dialog.add_response("cancel", _("Close"))
        dialog.add_response("logs", _("View Logs"))
        dialog.add_response("fix", _("Fix Dependencies"))
        
        dialog.set_response_appearance("fix", Adw.ResponseAppearance.SUGGESTED)
        
        def on_response(d, r):
            if r == "logs":
                try:
                    log_path = self.sunshine.config_dir / 'sunshine.log'
                    open_path(self, log_path)
                except Exception: pass
            elif r == "fix":
                self.open_advanced_settings()
                    
        dialog.connect("response", on_response)
        dialog.present()

    def show_error_dialog(self, title, message):
        dialog = Adw.MessageDialog.new(self._root_window(), title, message)
        dialog.add_response('ok', 'OK')
        dialog.present()
    
    def show_toast(self, message):
        show_toast = getattr(self.get_root(), 'show_toast', None)
        if callable(show_toast): show_toast(message)
        else: print(f"Toast: {message}")
        
    def open_sunshine_config(self, button):
        # Gtk.show_uri (not xdg-open) so the browser is raised under Wayland.
        open_uri(self, 'https://localhost:47990')

    # --- Paired devices (Sunshine clients API) ---------------------------

    def open_paired_devices_dialog(self, _widget):
        dialog = Adw.MessageDialog(
            heading=_("Paired Devices"),
            body=_("Devices paired with Sunshine. Disable to block access without "
                   "re-pairing; remove to revoke (a new PIN will be required)."),
        )
        dialog.set_transient_for(self._root_window())
        dialog.add_response("close", _("Close"))
        dialog.add_response("unpair_all", _("Remove All"))
        dialog.set_response_appearance("unpair_all", Adw.ResponseAppearance.DESTRUCTIVE)

        group = Adw.PreferencesGroup()
        self._device_rows = []
        loading = Adw.ActionRow(title=_("Loading…"))
        group.add(loading)
        self._device_rows.append(loading)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(260)
        scroll.set_child(group)
        dialog.set_extra_child(scroll)

        def on_response(d, r):
            if r == "unpair_all":
                self._unpair_all_devices()
        dialog.connect("response", on_response)

        self._devices_dialog_group = group
        self._refresh_paired_devices()
        dialog.present()

    def _refresh_paired_devices(self):
        if getattr(self, "_devices_dialog_group", None) is None:
            return
        import threading
        auth = self._get_sunshine_creds()

        def work():
            clients = self.sunshine.list_clients(auth=auth)
            GLib.idle_add(self._populate_paired_devices, clients)

        threading.Thread(target=work, daemon=True).start()

    def _populate_paired_devices(self, clients):
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
            uuid = client.get("uuid", "")
            name = client.get("name") or _("Unknown device")
            enabled = bool(client.get("enabled", True))
            row = Adw.ActionRow(title=name, subtitle=uuid)

            switch = Gtk.Switch()
            switch.set_valign(Gtk.Align.CENTER)
            switch.set_active(enabled)
            switch.update_property([Gtk.AccessibleProperty.LABEL], [_("Device enabled")])
            switch.connect("notify::active", self._on_device_toggle, uuid)
            row.add_suffix(switch)

            remove = Gtk.Button()
            remove.set_child(create_icon_widget("user-trash-symbolic", size=16))
            remove.add_css_class("flat")
            remove.set_valign(Gtk.Align.CENTER)
            remove.set_tooltip_text(_("Remove device"))
            remove.update_property([Gtk.AccessibleProperty.LABEL], [_("Remove device")])
            remove.connect("clicked", self._on_device_remove, uuid, name)
            row.add_suffix(remove)

            group.add(row)
            self._device_rows.append(row)
        return False

    def _on_device_toggle(self, switch, _pspec, uuid):
        enabled = switch.get_active()
        import threading
        auth = self._get_sunshine_creds()

        def work():
            if not self.sunshine.set_client_enabled(uuid, enabled, auth=auth):
                GLib.idle_add(self.show_toast, _("Failed to update device"))

        threading.Thread(target=work, daemon=True).start()

    def _on_device_remove(self, _button, uuid, name):
        confirm = Adw.MessageDialog(
            heading=_("Remove Device"),
            body=_("Remove “{}”? It will need to pair again with a new PIN.").format(name),
        )
        confirm.set_transient_for(self._root_window())
        confirm.add_response("cancel", _("Cancel"))
        confirm.add_response("remove", _("Remove"))
        confirm.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_resp(d, r):
            if r == "remove":
                import threading
                auth = self._get_sunshine_creds()

                def work():
                    ok = self.sunshine.unpair_client(uuid, auth=auth)
                    GLib.idle_add(self._after_device_change, ok)

                threading.Thread(target=work, daemon=True).start()

        confirm.connect("response", on_resp)
        confirm.present()

    def _unpair_all_devices(self):
        import threading
        auth = self._get_sunshine_creds()

        def work():
            ok = self.sunshine.unpair_all_clients(auth=auth)
            GLib.idle_add(self.show_toast,
                          _("Devices updated") if ok else _("Operation failed"))

        threading.Thread(target=work, daemon=True).start()

    def _after_device_change(self, ok):
        self.show_toast(_("Devices updated") if ok else _("Operation failed"))
        self._refresh_paired_devices()
        return False

    # --- Sunshine logs (logs API) ----------------------------------------

    def open_logs_dialog(self, _widget):
        dialog = Adw.MessageDialog(heading=_("Sunshine Logs"), body="")
        dialog.set_transient_for(self._root_window())
        dialog.add_response("close", _("Close"))

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.set_halign(Gtk.Align.END)

        refresh = Gtk.Button()
        refresh.set_child(create_icon_widget("view-refresh-symbolic", size=16))
        refresh.add_css_class("flat")
        refresh.set_tooltip_text(_("Refresh"))
        refresh.update_property([Gtk.AccessibleProperty.LABEL], [_("Refresh logs")])
        refresh.connect("clicked", lambda b: self._refresh_logs())
        toolbar.append(refresh)

        copy = Gtk.Button()
        copy.set_child(create_icon_widget("edit-copy-symbolic", size=16))
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
        scroll.set_min_content_width(520)
        scroll.set_vexpand(True)
        scroll.set_child(textview)

        box.append(toolbar)
        box.append(scroll)
        dialog.set_extra_child(box)
        self._refresh_logs()
        dialog.present()

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
        tv.get_buffer().set_text(
            text or _("No logs available (Sunshine not running or no credentials)."))
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
            self.show_error_dialog(
                _("Server Not Running"),
                _("Start the server first to manage the game library."))
            return

        dialog = Adw.MessageDialog(
            heading=_("Game Library"),
            body=_("Games offered to guests in Moonlight. Add your detected games "
                   "or remove entries."),
        )
        dialog.set_transient_for(self._root_window())
        dialog.add_response("close", _("Close"))

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
        scroll.set_min_content_width(420)
        scroll.set_vexpand(True)
        scroll.set_child(group)

        box.append(toolbar)
        box.append(scroll)
        dialog.set_extra_child(box)

        self._library_group = group
        self._refresh_game_library()
        dialog.present()

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
            row = Adw.ActionRow(title=name)
            cmd = app.get("cmd")
            if cmd:
                row.set_subtitle(cmd)
            # Desktop is the fallback target; do not let the user delete it.
            if name != "Desktop":
                remove = Gtk.Button()
                remove.set_child(create_icon_widget("user-trash-symbolic", size=16))
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
        self.show_toast(_("Added {} game(s)").format(added))
        self._refresh_game_library()
        return False

    def _after_library_change(self, ok):
        self.show_toast(_("Library updated") if ok else _("Operation failed"))
        self._refresh_game_library()
        return False

    # --- Host file browser (browse API) ----------------------------------

    def open_host_browse_dialog(self):
        if not self.sunshine.is_running():
            self.show_error_dialog(
                _("Server Not Running"),
                _("Start the server first to browse the host filesystem."))
            return

        dialog = Adw.MessageDialog(heading=_("Select Executable"), body="")
        dialog.set_transient_for(self._root_window())
        dialog.add_response("close", _("Close"))
        self._browse_dialog = dialog

        group = Adw.PreferencesGroup()
        self._browse_group = group
        self._browse_rows = []

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(360)
        scroll.set_min_content_width(480)
        scroll.set_child(group)
        dialog.set_extra_child(scroll)

        import os
        self._browse_load(os.path.expanduser("~"))
        dialog.present()

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
        callback: Callable[[Gtk.Widget], None],
    ) -> Gtk.Button:
        button = Gtk.Button()
        button.add_css_class("flat")
        button.add_css_class("file-browser-row-button")
        button.set_halign(Gtk.Align.FILL)
        button.set_hexpand(True)
        button.connect("clicked", callback)
        button.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            [title, description],
        )

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_margin_top(10)
        row.set_margin_bottom(10)
        row.set_margin_start(14)
        row.set_margin_end(14)

        icon = create_icon_widget(icon_name, size=16)
        icon.set_valign(Gtk.Align.CENTER)
        row.append(icon)

        label = Gtk.Label(label=title)
        label.set_halign(Gtk.Align.START)
        label.set_xalign(0)
        label.set_hexpand(True)
        label.set_wrap(True)
        row.append(label)

        button.set_child(row)
        return button

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
                lambda _button, p=parent: self._browse_load(p),
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
                    "folder-symbolic",
                    _("Open folder"),
                    lambda _button, p=epath: self._browse_load(p),
                )
            else:
                row = self._create_browse_button(
                    name,
                    "application-x-executable-symbolic",
                    _("Select executable"),
                    lambda _button, p=epath: self._browse_pick(p),
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
        from big_remote_play.ui.sunshine_preferences import SunshinePreferencesPage
        win = Adw.PreferencesWindow()
        win.set_transient_for(self._root_window())
        win.set_modal(True)
        win.set_title(_("Advanced server settings"))
        win.add(SunshinePreferencesPage(main_config=self.config))
        win.present()

    def open_password_dialog(self, _widget):
        dialog = Adw.MessageDialog(
            heading=_("Server Password"),
            body=_("Set the username and password used to manage the Sunshine "
                   "server. If you forgot the current password, switch on "
                   "“I forgot the current password” to reset it."),
        )
        dialog.set_transient_for(self._root_window())

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
        for r in (forgot_row, cur_user_row, cur_pass_row,
                  new_user_row, new_pass_row, confirm_row):
            grp.add(r)
        dialog.set_extra_child(grp)

        def on_forgot(switch, _pspec):
            reset = switch.get_active()
            cur_user_row.set_sensitive(not reset)
            cur_pass_row.set_sensitive(not reset)
        forgot_row.connect("notify::active", on_forgot)

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("save", _("Save"))
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)

        def on_resp(d, response):
            if response != "save":
                return
            new_user = new_user_row.get_text().strip()
            new_pass = new_pass_row.get_text()
            if not new_user or not new_pass:
                self.show_toast(_("Username and password cannot be empty."))
                return
            if new_pass != confirm_row.get_text():
                self.show_toast(_("New passwords do not match."))
                return
            if forgot_row.get_active():
                self._reset_password_async(new_user, new_pass)
            else:
                current = (cur_user_row.get_text().strip(), cur_pass_row.get_text())
                self._change_password_async(new_user, new_pass, current)

        dialog.connect("response", on_resp)
        dialog.present()

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

    def on_game_mode_changed(self, row, param):
        idx = row.get_selected()
        self.platform_games_expander.set_visible(idx in [1, 2])
        self.platform_games_expander.set_expanded(idx in [1, 2])
        self.custom_app_expander.set_visible(idx == 3)
        self.custom_app_expander.set_expanded(idx == 3)
        if idx in [1, 2]:
            plat = {1: 'Steam', 2: 'Lutris'}[idx]
            self.platform_games_expander.set_title(f"{plat} Games")
            self.populate_game_list(idx)
            
    def populate_game_list(self, mode_idx):
        plat = {1: 'Steam', 2: 'Lutris'}.get(mode_idx)
        if not plat: return
        if not self.detected_games[plat]:
             if plat == 'Steam': self.detected_games['Steam'] = self.game_detector.detect_steam()
             elif plat == 'Lutris': self.detected_games['Lutris'] = self.game_detector.detect_lutris()
        games = self.detected_games[plat]
        new_model = Gtk.StringList()
        if not games: new_model.append(f"No games found on {plat}")
        else:
            for game in games: new_model.append(game['name'])
        self.game_list_row.set_model(new_model)

    def save_host_settings(self, *args):
        if getattr(self, 'loading_settings', False): return
        h = self.config.get('host', {})
        if not isinstance(h, dict):
            h = {}
        h.update({
            'mode_idx': self.game_mode_row.get_selected(),
            'game_list_idx': self.game_list_row.get_selected(),
            'custom_name': self.custom_name_entry.get_text(),
            'custom_cmd': self.custom_cmd_entry.get_text(),
            
            # New Separate Settings
            'resolution_idx': self.resolution_row.get_selected(),
            'fps_idx': self.fps_row.get_selected(),
            'bandwidth_mbps': self.bandwidth_row.get_value(),
            'monitor_idx': self.monitor_row.get_selected(),
            'gpu_idx': self.gpu_row.get_selected(),
            'platform_idx': self.platform_row.get_selected(),
            'audio_mode': self.audio_mode_row.get_selected(),
            'audio_output_idx': self.audio_output_row.get_selected(),
            'upnp': self.upnp_row.get_active(),

            'ipv6': self.ipv6_row.get_active(),
            'webui_anyone': self.webui_anyone_row.get_active(),
            # New settings
            'efficient_codecs': self.codecs_row.get_active(),
            'optimization_mode': self.optimization_row.get_selected(),
            'wifi_mode': self.wifi_row.get_active()
        })

        self.config.set('host', h)
        
        # Update monitor target FPS live
        fps_idx = self.fps_row.get_selected()
        fps_val = {0: 30, 1: 60, 2: 120, 3: 144, 4: 60}.get(fps_idx, 60.0)
        self.perf_monitor.set_target_fps(fps_val)
        
        # Update monitor target Bandwidth live
        self.perf_monitor.set_target_bandwidth(self.bandwidth_row.get_value())
        
        # Sync to Sunshine Config
        try:
            from big_remote_play.ui.sunshine_preferences import SunshineConfigManager
            scm = SunshineConfigManager()
            
            # Map Host Settings -> Sunshine Settings
            scm.set('upnp', 'enabled' if self.upnp_row.get_active() else 'disabled')
            scm.set('address_family', 'both' if self.ipv6_row.get_active() else 'ipv4')
            scm.set('origin_web_ui_allowed', 'wan' if self.webui_anyone_row.get_active() else 'lan')
            streaming_active = self.audio_mode_row.get_selected() in [0, 1, 3]
            scm.set('stream_audio', 'true' if streaming_active else 'false')
            
            # Map Codecs
            # If enabled -> advertised(1). If disabled -> disabled(0)
            codec_val = '1' if self.codecs_row.get_active() else '0'
            scm.set('hevc_mode', codec_val)
            scm.set('av1_mode', codec_val)
            
            # Map Wi-Fi Mode (FEC)
            # Enabled -> 20%. Disabled -> 5%
            scm.set('fec_percentage', '20' if self.wifi_row.get_active() else '5')
            
            # Map Optimization Mode
            # 0=Low Latency, 1=Balanced, 2=High Quality
            opt_idx = self.optimization_row.get_selected()
            if opt_idx == 0: # Low Latency
                scm.set('nvenc_preset', '1') # P1
                scm.set('amd_quality', 'speed')
                scm.set('sw_preset', 'ultrafast')
                scm.set('nvenc_twopass', 'disabled')
            elif opt_idx == 2: # High Quality
                scm.set('nvenc_preset', '7') # P7
                scm.set('amd_quality', 'quality')
                scm.set('sw_preset', 'medium')
                scm.set('nvenc_twopass', 'quarter_res')
            else: # Balanced
                scm.set('nvenc_preset', '4') # P4
                scm.set('amd_quality', 'balanced')
                scm.set('sw_preset', 'veryfast')
                scm.set('nvenc_twopass', 'disabled') # Or quarter_res depending on preference
            
            # Map Bandwidth to min_bitrate
            # 0 = Unlimited (default 0 or very high)
            bw = int(self.bandwidth_row.get_value() * 1000) # Mbps -> Kbps
            scm.set('min_bitrate', str(bw) if bw > 0 else '0')
                
        except Exception as e:
            print(f"Error syncing to Sunshine config: {e}")

    def load_settings(self):
        self.loading_settings = True
        try:
            # Sync from Sunshine Config first
            try:
                from big_remote_play.ui.sunshine_preferences import SunshineConfigManager
                scm = SunshineConfigManager()
                
                # Update Host Config based on Sunshine Config (Source of Truth for these fields)
                h = self.config.get('host', {})
                if not isinstance(h, dict):
                    h = {}
                h['upnp'] = scm.get('upnp', 'enabled') == 'enabled'
                h['ipv6'] = scm.get('address_family', 'both') == 'both'
                h['webui_anyone'] = scm.get('origin_web_ui_allowed', 'lan') == 'wan'
                h['audio'] = scm.get('stream_audio', 'true').lower() == 'true'
                
                # Reverse Map Codecs
                # If hevc_mode >= 1 OR av1_mode >= 1 -> Enabled
                hevc = scm.get('hevc_mode', '0')
                av1 = scm.get('av1_mode', '0')
                h['efficient_codecs'] = (hevc != '0' or av1 != '0')
                
                # Reverse Map Wi-Fi (FEC)
                # If FEC >= 15 -> Enabled
                fec = int(scm.get('fec_percentage', '10'))
                h['wifi_mode'] = (fec >= 15)
                
                # Reverse Map Optimization
                # Heuristic based on nvenc_preset
                nv_preset = scm.get('nvenc_preset', '4')
                if nv_preset in ['1', '2']: h['optimization_mode'] = 0 # Low Latency
                elif nv_preset in ['5', '6', '7']: h['optimization_mode'] = 2 # High Quality
                else: h['optimization_mode'] = 1 # Balanced
                
                # Reverse Map Bandwidth
                bw_kbps = int(scm.get('min_bitrate', '0'))
                h['bandwidth_mbps'] = bw_kbps / 1000.0
                
                self.config.set('host', h)
            except Exception as e:
                print(f"Error syncing from Sunshine config: {e}") 


            h = self.config.get('host', {})
            if not isinstance(h, dict):
                h = {}
            if not h: return
            self.game_mode_row.set_selected(h.get('mode_idx', 0))
            # Restore game list selection after populating
            mode_idx = h.get('mode_idx', 0)
            if mode_idx in [1, 2]:
                self.populate_game_list(mode_idx)
                game_list_idx = h.get('game_list_idx', 0)
                if game_list_idx is not None:
                    self.game_list_row.set_selected(game_list_idx)
            self.custom_name_entry.set_text(h.get('custom_name', ''))
            self.custom_cmd_entry.set_text(h.get('custom_cmd', ''))
            
            # New Separate Settings
            self.resolution_row.set_selected(h.get('resolution_idx', 1)) # Default 1080p
            fps_idx = h.get('fps_idx', 1)
            self.fps_row.set_selected(fps_idx) 
            
            # Update monitor target FPS
            fps_val = {0: 30, 1: 60, 2: 120, 3: 144, 4: 60}.get(fps_idx, 60.0)
            self.perf_monitor.set_target_fps(fps_val)
            
            bw_val = h.get('bandwidth_mbps', 0)
            self.bandwidth_row.set_value(bw_val) 
            self.perf_monitor.set_target_bandwidth(bw_val)
            
            self.monitor_row.set_selected(h.get('monitor_idx', 0))
            self.gpu_row.set_selected(h.get('gpu_idx', 0))
            self.platform_row.set_selected(h.get('platform_idx', 0))
            
            # Audio Mode
            audio_mode = h.get('audio_mode', 0)
            self.audio_mode_row.set_selected(audio_mode)
            show_mixer = audio_mode in [0, 1, 3]
            self.audio_mixer_expander.set_visible(show_mixer)


            
            self.audio_output_row.set_selected(h.get('audio_output_idx', 0))
            
            self.upnp_row.set_active(h.get('upnp', True))
            self.ipv6_row.set_active(h.get('ipv6', True))
            self.webui_anyone_row.set_active(h.get('webui_anyone', False))
            
            # New settings
            self.codecs_row.set_active(h.get('efficient_codecs', True))
            self.optimization_row.set_selected(h.get('optimization_mode', 1))
            self.wifi_row.set_active(h.get('wifi_mode', False))
        finally:
            self.loading_settings = False

    def connect_settings_signals(self):
        for r in [self.upnp_row, self.ipv6_row, self.webui_anyone_row, self.codecs_row, self.wifi_row]:
            r.connect('notify::active', self.save_host_settings)

        for r in [self.audio_mode_row, self.game_mode_row, self.game_list_row, self.monitor_row, self.gpu_row, self.platform_row, self.audio_output_row, self.optimization_row, self.resolution_row, self.fps_row]:
            r.connect('notify::selected', self.save_host_settings)


        for r in [self.bandwidth_row]:
            r.connect('notify::value', self.save_host_settings)
        for r in [self.custom_name_entry, self.custom_cmd_entry]:
            r.connect('notify::text', self.save_host_settings)

    def on_reset_clicked(self, button):
        diag = Adw.MessageDialog(heading=_('Restore Defaults'), body=_('Do you want to restore default settings?'))
        diag.add_response('cancel', _('Cancel')); diag.add_response('reset', _('Restore'))
        diag.set_response_appearance('reset', Adw.ResponseAppearance.DESTRUCTIVE)
        def on_resp(d, r):
            if r == 'reset': self.reset_to_defaults()
        diag.connect('response', on_resp); diag.present()

    def reset_to_defaults(self):
        self.config.set('host', self.config.default_config()['host'])
        self.load_settings(); self.show_toast(_("Settings Restored"))

    def cleanup(self):
        if hasattr(self, 'perf_monitor'): self.perf_monitor.stop_monitoring()
        stop_pin_listener = self.stop_pin_listener
        if callable(stop_pin_listener):
            stop_pin_listener()
        uptime_timer_id = self._uptime_timer_id
        if uptime_timer_id is not None:
            GLib.source_remove(uptime_timer_id)
            self._uptime_timer_id = None
        
        # Only cleanup audio if we are NOT hosting, because Sunshine depends on these sinks.
        # If we are hosting, the user expects the stream to continue working.
        # This also avoids the feedback loop (microfonia) when the app is closed while Moonlight/Sunshine are active.
        if not self.is_hosting:
            if hasattr(self, 'audio_manager'): self.audio_manager.cleanup()
