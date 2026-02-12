import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GLib
import subprocess, random, string, json, socket, os
from pathlib import Path
from utils.game_detector import GameDetector

from utils.config import Config
import subprocess, os, tempfile, threading
from gi.repository import GLib
from utils.i18n import _
from utils.icons import create_icon_widget
class HostView(Gtk.Box):
    def __init__(self):
        print("DEBUG: HostView.__init__ called")
        self.loading_settings = True
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.config = Config()
        self.is_hosting = False
        self.process = None # Initialize to avoid AttributeError
        self.pin_code = None
        
        from host.sunshine_manager import SunshineHost
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
        
    def detect_monitors(self):
        monitors = [(_('Automatic'), 'auto')]
        try:
            out = subprocess.check_output(['xrandr', '--current'], text=True, stderr=subprocess.STDOUT)
            for l in out.split('\n'):
                if ' connected' in l:
                    p = l.split()
                    if p:
                        name = p[0]; res = ""
                        for x in p:
                            if 'x' in x and '+' in x: res = f" ({x.split('+')[0]})"; break
                        monitors.append((f"Monitor: {name}{res}", name))
        except:
            try:
                out = subprocess.check_output(['xrandr', '--listactivemonitors'], text=True)
                for l in out.strip().split('\n')[1:]:
                    p = l.split()
                    if p: monitors.append((f"Monitor: {p[-1]}", p[-1]))
            except: pass
        try:
            from pathlib import Path
            for p in Path('/sys/class/drm').glob('card*-*'):
                if (p/'status').exists() and (p/'status').read_text().strip() == 'connected':
                    n = p.name.split('-', 1)[1]
                    if not any(n in m[1] for m in monitors): monitors.append((f"Monitor: {n}", n))
        except: pass
        return monitors

    def detect_gpus(self):
        gpus = []
        try:
            lspci = subprocess.check_output(['lspci'], text=True).lower()
            if 'nvidia' in lspci:
                try:
                    subprocess.check_call(['nvidia-smi'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    gpus.append({'label': 'NVENC (NVIDIA)', 'encoder': 'nvenc', 'adapter': 'auto'})
                except: pass
            if 'intel' in lspci: gpus.append({'label': 'VAAPI (Intel Quicksync)', 'encoder': 'vaapi', 'adapter': '/dev/dri/renderD128'})
        except: pass
        try:
            from pathlib import Path
            if Path('/dev/dri').exists():
                for node in sorted(list(Path('/dev/dri').glob('renderD*'))):
                    if not any(str(node) == g['adapter'] for g in gpus):
                        gpus.append({'label': f"VAAPI (Adapter {node.name})", 'encoder': 'vaapi', 'adapter': str(node)})
        except: pass
        gpus.extend([{'label':'Vulkan (Exp)', 'encoder':'vulkan', 'adapter':'auto'}, {'label':'Software', 'encoder':'software', 'adapter':'auto'}])
        return gpus
        
    def setup_ui(self):

        clamp = Adw.Clamp()
        clamp.set_maximum_size(800)
        for margin in ['top', 'bottom', 'start', 'end']:
            getattr(clamp, f'set_margin_{margin}')(24)
        
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        
        self.loading_bar = Gtk.ProgressBar()
        self.loading_bar.add_css_class('osd')
        self.loading_bar.set_visible(False)
        content.append(self.loading_bar)
        
        from .performance_monitor import PerformanceMonitor
        self.perf_monitor = PerformanceMonitor(sunshine=self.sunshine)
        self.perf_monitor.set_visible(True)
        self.perf_monitor.set_connection_status("Localhost", _("Sunshine Offline"), False)
        
        self.header = Adw.PreferencesGroup()
        self.header.set_header_suffix(create_icon_widget('network-server-symbolic', size=24))
        self.header.set_title(_('Host Server'))
        self.header.set_description(_('Configure and share your game for friends to connect'))
        
        
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
        self.custom_app_expander.add_row(self.custom_cmd_entry)
        game_group.add(self.custom_app_expander)
        
        self.streaming_expander = Adw.ExpanderRow()
        self.streaming_expander.set_title(_('Streaming Settings'))
        self.streaming_expander.set_subtitle(_('Quality and Players'))
        self.streaming_expander.set_icon_name('preferences-desktop-display-symbolic')
        
        self.quality_row = Adw.ComboRow()
        self.quality_row.set_title(_('Streaming Quality'))
        self.quality_row.set_subtitle(_('Higher quality = higher bandwidth usage'))
        
        quality_model = Gtk.StringList()
        for q in [_('Low (720p 30fps)'), _('Medium (1080p 30fps)'), _('High (1080p 60fps)'), _('Ultra (1440p 60fps)'), _('Max (4K 60fps)')]:
            quality_model.append(q)
        self.quality_row.set_model(quality_model)
        self.quality_row.set_selected(2)
        self.streaming_expander.add_row(self.quality_row)
        
        self.players_row = Adw.SpinRow()
        self.players_row.set_title(_('Max Players'))
        self.players_row.set_subtitle(_('Maximum number of simultaneous connections'))
        self.players_row.set_adjustment(Gtk.Adjustment(value=2, lower=1, upper=8, step_increment=1, page_increment=1))
        self.players_row.set_digits(0)
        self.streaming_expander.add_row(self.players_row)
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

        # 2. Streaming Switch
        self.streaming_audio_row = Adw.SwitchRow()
        self.streaming_audio_row.set_title(_('Audio Streaming'))
        self.streaming_audio_row.set_subtitle(_('Enable audio on client (Sound on Host + Guest)'))
        self.streaming_audio_row.set_active(True)
        self.streaming_audio_row.connect('notify::active', self.on_streaming_toggled)
        audio_group.add(self.streaming_audio_row)
        
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
        
        self.input_row = Adw.SwitchRow()
        self.input_row.set_title(_('Share Controls'))
        self.input_row.set_subtitle(_('Allow guests to control the game'))
        self.input_row.set_active(True)
        self.advanced_expander.add_row(self.input_row)
        
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
        
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        button_box.set_halign(Gtk.Align.CENTER)
        button_box.set_margin_top(12)
        button_box.set_margin_bottom(24)
        
        self.start_button = Gtk.Button()
        self.start_button.add_css_class('pill')
        self.start_button.add_css_class('suggested-action')
        self.start_button.set_size_request(200, 50)
        
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_box.set_halign(Gtk.Align.CENTER)
        self.start_btn_spinner = Gtk.Spinner()
        self.start_btn_spinner.set_visible(False)
        self.start_btn_label = Gtk.Label(label=_('Start Server'))
        btn_box.append(self.start_btn_spinner)
        btn_box.append(self.start_btn_label)
        self.start_button.set_child(btn_box)
        
        self.start_button.connect('clicked', self.toggle_hosting)
        
        self.configure_button = Gtk.Button(label=_('Configure Sunshine'))
        self.configure_button.add_css_class('pill')
        self.configure_button.set_size_request(200, 50)
        self.configure_button.connect('clicked', self.open_sunshine_config)
        
        button_box.append(self.start_button)
        button_box.append(self.configure_button)
        
        self.pin_button = Gtk.Button(label=_('Enter PIN'))
        self.pin_button.add_css_class('pill')
        self.pin_button.add_css_class('success-action')
        self.pin_button.set_size_request(180, -1)
        self.pin_button.set_visible(False)
        self.pin_button.connect('clicked', self.open_pin_dialog)
        button_box.append(self.pin_button)
        
        self.create_summary_box()
        
        # View Switcher and Stack
        self.view_stack = Adw.ViewStack()
        self.view_stack.set_vexpand(True)
        
        # 1. Information Page
        info_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        info_page.append(self.summary_box)
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
        view_switcher = Adw.ViewSwitcher()
        view_switcher.set_stack(self.view_stack)
        view_switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        view_switcher.set_halign(Gtk.Align.CENTER)
        view_switcher.set_margin_top(12)
        view_switcher.set_margin_bottom(12)
        
        content.append(self.header)
        content.append(self.perf_monitor)
        content.append(button_box)
        content.append(view_switcher)
        content.append(self.view_stack)
        
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

    def _get_sunshine_conf_path(self):
        from pathlib import Path
        return Path.home() / '.config' / 'big-remoteplay' / 'sunshine' / 'sunshine.conf'

    def _get_sunshine_creds(self):
        conf_file = self._get_sunshine_conf_path()
        if not conf_file.exists(): return None
        
        user = None
        password = None
        
        try:
            with open(conf_file, 'r') as f:
                for line in f:
                    if '=' in line:
                        key, val = line.strip().split('=', 1)
                        key = key.strip()
                        val = val.strip()
                        if key == 'sunshine_user': user = val
                        elif key == 'sunshine_password': password = val
            
            if user and password:
                return (user, password)
        except: pass
        return None

    def _save_sunshine_creds(self, user, password):
        conf_file = self._get_sunshine_conf_path()
        lines = []
        if conf_file.exists():
            try:
                with open(conf_file, 'r') as f:
                    lines = f.readlines()
            except: pass
        
        new_lines = []
        found_user = False
        found_pass = False
        
        for line in lines:
            if '=' in line:
                key, _ = line.split('=', 1)
                key = key.strip()
                if key == 'sunshine_user':
                    new_lines.append(f"sunshine_user = {user}\n")
                    found_user = True
                    continue
                elif key == 'sunshine_password':
                    new_lines.append(f"sunshine_password = {password}\n")
                    found_pass = True
                    continue
            new_lines.append(line)
            
        if not found_user: new_lines.append(f"sunshine_user = {user}\n")
        if not found_pass: new_lines.append(f"sunshine_password = {password}\n")

        # Configs required for API and operation
        required = {
            "credentials": f"sunshine:{password}",
            "log_level": "2",
            "port": "47989",
            "webserver": "0.0.0.0",
            "enable_api_endpoints": "true"
        }
        
        final_lines = []
        existing_keys = set()
        
        # Process existing + updated user/pass lines
        for line in new_lines:
            if '=' in line:
                key = line.split('=')[0].strip()
                if key in required:
                    final_lines.append(f"{key} = {required[key]}\n")
                    existing_keys.add(key)
                    continue
            final_lines.append(line)
            
        # Append missing required configs
        for k, v in required.items():
            if k not in existing_keys:
                final_lines.append(f"{k} = {v}\n")
        
        try:
            conf_file.parent.mkdir(parents=True, exist_ok=True)
            with open(conf_file, 'w') as f:
                f.writelines(final_lines)
            print(f"DEBUG: Saved Sunshine credentials and config to {conf_file}")
        except Exception as e:
            print(f"Error saving Sunshine creds: {e}")

    def _ensure_sunshine_config(self):
        """Ensures sunshine.conf has required API settings"""
        conf_file = self._get_sunshine_conf_path()
        if not conf_file.exists(): return
        
        try:
            lines = []
            with open(conf_file, 'r') as f:
                lines = f.readlines()
            
            config_map = {}
            for line in lines:
                if '=' in line:
                    k, v = line.split('=', 1)
                    config_map[k.strip()] = v.strip()
            
            pwd = config_map.get('sunshine_password', '')
            
            # If we have a password but no credentials line or missing API config
            updates_needed = False
            required = {
                "log_level": "2",
                "port": "47989",
                "webserver": "0.0.0.0",
                "enable_api_endpoints": "true"
            }
            if pwd and 'credentials' not in config_map:
                 required['credentials'] = f"sunshine:{pwd}"
            
            for k, v in required.items():
                if k not in config_map:
                    updates_needed = True
            
            if updates_needed:
                print("DEBUG: Updating sunshine.conf with missing API settings...")
                final_lines = lines.copy()
                if final_lines and not final_lines[-1].endswith('\n'):
                    final_lines[-1] += '\n'
                    
                for k, v in required.items():
                    if k not in config_map:
                        final_lines.append(f"{k} = {v}\n")
                
                with open(conf_file, 'w') as f:
                    f.writelines(final_lines)
                print("DEBUG: sunshine.conf updated successfully.")
                
        except Exception as e:
            print(f"Error ensuring sunshine config: {e}")

    def open_pin_dialog(self, _widget):
        self._ensure_sunshine_config() # Ensure config before trying to use API
        dialog = Adw.MessageDialog(
            heading=_("Insert PIN"), 
            body=_("Enter the PIN displayed on the client device (Moonlight).")
        )
        dialog.set_transient_for(self.get_root())
        
        # Grupo de preferências para conter os campos
        grp = Adw.PreferencesGroup()
        
        pin_row = Adw.EntryRow(title=_("PIN"))
        # pin_row.set_input_purpose(Gtk.InputPurpose.NUMBER) 
        
        name_row = Adw.EntryRow(title=_("Device Name (Optional)"))
        
        grp.add(pin_row)
        grp.add(name_row)
        
        dialog.set_extra_child(grp)
        
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("ok", _("Send"))
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        
        def on_response(d, r):
            if r == "ok":
                pin = pin_row.get_text().strip()
                if not pin: return
                
                # Try with stored credentials first
                auth = self._get_sunshine_creds()
                success, msg = self.sunshine.send_pin(pin, auth=auth)
                
                if success:
                    self.show_toast(_("PIN sent successfully"))
                elif "Authentication Failed" in msg or "Falha de Autenticação" in msg or "401" in msg:
                    # Se falhar autenticação, pedir credenciais
                    self.open_sunshine_auth_dialog(pin)
                elif "307" in msg:
                    # Se retornar Redirect 307, significa que não tem usuário criado
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
        dialog.set_transient_for(self.get_root())
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("open", _("Open Configuration"))
        dialog.set_response_appearance("open", Adw.ResponseAppearance.SUGGESTED)
        
        def on_resp(d, r):
            if r == "open":
                import webbrowser
                webbrowser.open("https://localhost:47990")
        
        dialog.connect("response", on_resp)
        dialog.present()
        
    def open_create_user_dialog(self, pin_retry):
        # This seems unused given the web prompt above, but keping for reference or alternative flow
        dialog = Adw.MessageDialog(
            heading=_("Create Sunshine User"), 
            body=_("Define a username and password for Sunshine.")
        )
        dialog.set_transient_for(self.get_root())
        
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
        
    def open_sunshine_auth_dialog(self, pin_to_retry: str):
        # Prefill with existing if available (for correction)
        creds = self._get_sunshine_creds()
        curr_user = creds[0] if creds else "admin"
        
        dialog = Adw.MessageDialog(
            heading=_("Sunshine Authentication"), 
            body=_("Sunshine requires login. Enter your credentials (default: admin / password created during installation).")
        )
        dialog.set_transient_for(self.get_root())
        
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
                success, msg = self.sunshine.send_pin(pin_to_retry, auth=(user, pwd))
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
        self.summary_box.set_header_suffix(create_icon_widget('dialog-information-symbolic', size=24))
        self.summary_box.set_visible(False); self.field_widgets = {}
        for l, k, i, r in [('Host', 'hostname', 'computer-symbolic', True), ('IPv4', 'ipv4', 'network-wired-symbolic', True), ('IPv6', 'ipv6', 'network-wired-symbolic', True), ('IPv4 Global', 'ipv4_global', 'network-transmit-receive-symbolic', True), ('IPv6 Global', 'ipv6_global', 'network-transmit-receive-symbolic', True)]: self.create_masked_row(l, k, i, r)

    def on_streaming_toggled(self, row, param):
        is_active = row.get_active()
        self.audio_mixer_expander.set_visible(is_active)
        # If hosting, apply dynamically (optional, may require restart)
        if self.is_hosting:
             self.show_toast(_("Restart server to apply complex audio changes."))

    def load_audio_outputs(self):
        try:
            from utils.audio import AudioManager
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
        
        # If hosting and audio active, need to reconfigure loopback
        if self.is_hosting and self.streaming_audio_row.get_active():
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
            # Locate the script relative to this file
            # src/ui/host_view.py -> src/ui -> src -> root -> scripts
            script_path = Path(__file__).parent.parent / 'scripts' / 'configure_firewall.sh'
            
            if not script_path.exists():
                script_path = Path("/usr/share/big-remote-play-together/scripts/configure_firewall.sh")
            
            if not script_path.exists():
                self.show_error_dialog(_("Error"), f"Script not found: {script_path}")
                return

            # Run with pkexec
            cmd = ['pkexec', str(script_path)]
            
            def on_done(ok, out):
                if ok: self.show_toast(_("Success: {}").format(out.strip()))
                else: self.show_error_dialog(_("Firewall Error"), out if out else _("Execution failed or cancelled."))
            
            def run():
                try:
                    res = subprocess.run(cmd, capture_output=True, text=True)
                    GLib.idle_add(on_done, res.returncode == 0, res.stdout + res.stderr)
                except Exception as e:
                    GLib.idle_add(on_done, False, str(e))
                    
            threading.Thread(target=run, daemon=True).start()
            
        except Exception as e:
            self.show_toast(_("Error executing script: {}").format(e))

    def create_masked_row(self, title, key, icon_name='text-x-generic-symbolic', default_revealed=False):
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
        copy_btn = Gtk.Button()
        copy_btn.set_child(create_icon_widget('edit-copy-symbolic', size=16))
        copy_btn.add_css_class('flat')
        
        box.append(value_lbl); box.append(eye_btn); box.append(copy_btn)
        row.add_suffix(box)
        self.summary_box.add(row)
        
        self.field_widgets[key] = {'label': value_lbl, 'real_value': '', 'revealed': default_revealed, 'btn_eye': eye_btn}
        eye_btn.connect('clicked', lambda b: self.toggle_field_visibility(key))
        copy_btn.connect('clicked', lambda b: self.copy_field_value(key))
        
    def toggle_field_visibility(self, key):
        field = self.field_widgets[key]
        field['revealed'] = not field['revealed']
        field['btn_eye'].set_child(create_icon_widget('view-conceal-symbolic' if field['revealed'] else 'view-reveal-symbolic', size=16))
        field['label'].set_text(field['real_value'] if field['revealed'] else '••••••')
            
    def copy_field_value(self, key):
        if val := self.field_widgets[key]['real_value']:
             self.get_root().get_clipboard().set(val); self.show_toast(_("Copied!"))

    def toggle_hosting(self, button):
        print("DEBUG: HostView.toggle_hosting called")
        self.show_toast(_("Clicked Start Server..."))
        self.start_button.set_sensitive(False)
        self.start_btn_spinner.set_visible(True)
        self.start_btn_spinner.start()
        
        # Defer action slightly to allow UI to paint
        GLib.timeout_add(100, self._perform_toggle_hosting)

    def _perform_toggle_hosting(self):
        print(f"DEBUG: HostView._perform_toggle_hosting - is_hosting: {self.is_hosting}")
        if self.is_hosting: self.stop_hosting()
        else: self.start_hosting()
        return False
            
    def sync_ui_state(self):
        if self.is_hosting:
            self.header.set_description(_('Server Active - Guests can now connect'))
            self.perf_monitor.set_connection_status("Sunshine", _("Active - Waiting for Connections"), True)
            self.perf_monitor.start_monitoring()
            
            # Button State: Hosting -> Stop
            self.start_btn_label.set_label(_('Stop'))
            self.start_button.remove_css_class('suggested-action')
            self.start_button.add_css_class('destructive-action')
            self.start_btn_spinner.set_visible(False)
            self.start_btn_spinner.stop()
            
            self.header.set_visible(True)
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
            self.header.set_description(_('Configure and share your game for friends to connect'))
            self.perf_monitor.set_connection_status("Sunshine", _("Inactive"), False)
            self.perf_monitor.stop_monitoring()
            self.header.set_visible(True)
            
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
            self.start_button.remove_css_class('destructive-action')
            self.start_button.add_css_class('suggested-action')
            self.start_btn_spinner.set_visible(False)
            self.start_btn_spinner.stop()
            
            self.configure_button.set_sensitive(False)
            self.configure_button.remove_css_class('suggested-action')
            for r in [self.game_mode_row, self.hardware_expander, self.streaming_expander, self.advanced_expander]: r.set_sensitive(True)

    def populate_summary_fields(self):
        import socket, threading
        from utils.network import NetworkDiscovery
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
        # Force routing:
        # Apps in self.private_audio_apps -> Host Sink (Local Only)
        # Others -> SunshineGameSink (Stream + Host)
        if not self.is_hosting or not self.audio_mixer_expander.get_visible(): return True
        if not hasattr(self, 'active_host_sink') or not self.active_host_sink: return True
        
        shared_sink = "SunshineGameSink"
        private_sink = self.active_host_sink
        
        if hasattr(self, 'audio_manager'):
            try:
                # 1. Default Sink Enforcer (Fix for Guest Connect)
                # Check if default sink was incorrectly changed by Sunshine (e.g. sink-sunshine-stereo)
                current_default = self.audio_manager.get_default_sink()
                if current_default and current_default != private_sink:
                    # If current sink is an unwanted Sunshine virtual sink, restore Host sink
                    if "sunshine" in current_default.lower() and "stereo" in current_default.lower():
                        print(f"Enforcer: Audio hijack detected ({current_default}). Restoring to {private_sink}")
                        self.audio_manager.set_default_sink(private_sink)

                apps = self.audio_manager.get_apps()
                for app in apps:
                    app_id = app['id']
                    name = app.get('name', '')
                    
                    # Double check to avoid loops (including GameSink and Loopback)
                    # DO NOT move loopback itself nor monitor
                    # ALSO DO NOT move Moonlight (Client) to avoid infinite feedback loop if running locally
                    if 'sunshine' in name.lower() or 'loopback' in name.lower() or 'moonlight' in name.lower(): continue

                    # Define target
                    target = private_sink if name in self.private_audio_apps else shared_sink
                    
                    # Check current sink
                    current_sink = app.get('sink_name', '')
                    
                    # If app already on target, ignore
                    if current_sink == target: continue

                    # Move logic
                    if target == shared_sink:
                        # Want to move to GameSink
                        # But CAREFUL: If SunshineGameSink is null-sink and muted at end, user complains.
                        # But we already configured Loopback.
                        print(f"Enforcer: Moving {name} to Stream ({target})")
                        self.audio_manager.move_app(app_id, target)
                    else:
                        # Want to move to Private (Hardware)
                        print(f"Enforcer: Moving {name} to Local ({target})")
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
        print("DEBUG: HostView.start_hosting called")
        self.loading_bar.set_visible(True); self.loading_bar.pulse()
            
        try:
            if self.sunshine.is_running():
                self.sunshine.stop()
                import time; time.sleep(1)
            
            self.pin_code = ''.join(random.choices(string.digits, k=6))
            from utils.network import NetworkDiscovery
            self.stop_pin_listener = NetworkDiscovery().start_pin_listener(self.pin_code, socket.gethostname())
            
            mode_idx = self.game_mode_row.get_selected()
            apps_config = []
            
            if mode_idx in [1, 2]:
                idx = self.game_list_row.get_selected()
                if idx != Gtk.INVALID_LIST_POSITION:
                    plat = {1: 'Steam', 2: 'Lutris'}[mode_idx]
                    games = self.detected_games.get(plat, [])
                    if 0 <= idx < len(games):
                        apps_config.append({"name": games[idx]['name'], "cmd": games[idx]['cmd'], "detached": True})
            elif mode_idx == 3:
                name = self.custom_name_entry.get_text(); cmd = self.custom_cmd_entry.get_text()
                if name and cmd: apps_config.append({"name": name, "cmd": cmd, "detached": True})
            
            if apps_config: self.sunshine.update_apps(apps_config)
            else:
                if mode_idx == 0: self.sunshine.update_apps([{"name": "Desktop", "detached": True, "cmd": "sleep infinity"}])
                     
            quality_map = {0: (5000, 30), 1: (10000, 30), 2: (20000, 60), 3: (30000, 60), 4: (40000, 60)}
            bitrate, fps = quality_map.get(self.quality_row.get_selected(), (20000, 60))
            
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
            if self.streaming_audio_row.get_active():
                sunshine_config['audio'] = 'pulse'
                
                # Identify Host Sink
                host_sink_idx = self.audio_output_row.get_selected()
                if self.audio_devices and 0 <= host_sink_idx < len(self.audio_devices):
                    host_sink = self.audio_devices[host_sink_idx]['name']
                else:
                    host_sink = self.audio_manager.get_default_sink()
                
                # Store it for the enforcer
                self.active_host_sink = host_sink

                # Enable Host+Guest Streaming
                if self.audio_manager:
                    # Returns True if success
                    if self.audio_manager.enable_streaming_audio(host_sink):
                        # Ensure we use the sink name for Sunshine to capture from its monitor
                        sunshine_config['audio_sink'] = "SunshineGameSink"
                        
                        print(f"DEBUG: Configured Sunshine audio_sink to: {sunshine_config['audio_sink']}")
                        
                        # Start Mixer UI updates
                        self.start_audio_mixer_refresh()

                        # Default: Restore Host Sink after 500ms so user controls local volume,
                        # while Enforcer routes games to SunshineGameSink.
                        GLib.timeout_add(500, lambda: (self.audio_manager.set_default_sink(host_sink), self.show_toast(f"Padrão restaurado: {host_sink}"))[1])
                    else:
                         print("Failed to enable streaming sinks, falling back to default")
                         self.show_toast(_("Failed to create Virtual Audio"))
                         self.audio_manager.disable_streaming_audio(None)
            else:
                sunshine_config['audio'] = 'none' # Disable audio streaming per requirement
                if self.audio_manager:
                    # Ensure cleanly disabled
                    self.audio_manager.disable_streaming_audio(None)
                self.stop_audio_mixer_refresh()

            platforms = ['auto', 'wayland', 'x11', 'kms']
            platform = platforms[self.platform_row.get_selected()]
            if platform == 'auto':
                session = os.environ.get('XDG_SESSION_TYPE', '').lower()
                platform = 'wayland' if session == 'wayland' else 'x11'
            sunshine_config['platform'] = platform


            if platform != 'wayland':
                monitor_idx = self.monitor_row.get_selected()
                if 0 < monitor_idx < len(self.available_monitors):
                    mon_name = self.available_monitors[monitor_idx][1]
                    if mon_name != 'auto':
                        sunshine_config['output_name'] = mon_name
            
            # If Wayland, do NOT set output_name.
            # Sunshine uses Portals (Pipewire) which asks user to choose or uses default.
            # Setting output_name on Wayland often causes "Monitor not found" errors.
            if selected_gpu_info['encoder'] == 'vaapi' and selected_gpu_info['adapter'] != 'auto':
                sunshine_config['adapter_name'] = selected_gpu_info['adapter']
            
            if platform == 'wayland':
                sunshine_config['wayland.display'] = os.environ.get('WAYLAND_DISPLAY', 'wayland-0')
            if platform == 'x11' and self.monitor_row.get_selected() == 0:
                sunshine_config['output_name'] = ':0'
            
            self.sunshine.configure(sunshine_config)
            if self.sunshine.start():
                self.sync_ui_state()
                
                self.is_hosting = True
                
                # Update final UI state
                self.sync_ui_state()
                self.show_toast(_('Server started'))
            else:
                self.is_hosting = False
                self.sync_ui_state()
                
                # Check for specific errors in log
                error_msg = _("Check logs for details.")
                fix_cmd = None
                
                try:
                    log_path = self.sunshine.config_dir / 'sunshine.log'
                    if log_path.exists():
                        with open(log_path, 'r') as f:
                            lines = f.readlines()
                            for line in lines[-10:]:
                                if "error while loading shared libraries" in line and "libicuuc.so.76" in line:
                                    lib = "libicuuc.so.76"
                                    error_msg = _("Missing library: {}\n\nWould you like to try to fix it automatically?").format(lib)
                                    
                                    # Locate the fix script
                                    script_path = Path(__file__).parent.parent / 'scripts' / 'fix_sunshine_libs.sh'
                                    if script_path.exists():
                                        fix_cmd = ['pkexec', str(script_path)]
                                    break
                                elif "error while loading shared libraries" in line:
                                    lib = line.split("error while loading shared libraries:")[1].split(":")[0].strip()
                                    error_msg = _("Missing library: {}\n\nPlease check your Sunshine installation.").format(lib)
                                    break
                                elif "Address already in use" in line:
                                    error_msg = _("Port already in use. Check if another instance is running.")
                                    break
                except: pass
                
                dialog = Adw.MessageDialog.new(self.get_root(), _("Failed to start"), _("The server failed to start.\n{}").format(error_msg))
                if fix_cmd:
                    dialog.add_response("fix", _("Fix Automatically"))
                dialog.add_response("close", _("Close"))
                
                def on_dialog_response(dialog, response):
                    if response == "fix" and fix_cmd:
                        try:
                            subprocess.Popen(fix_cmd)
                        except Exception as e:
                            self.show_error_dialog(_("Error"), f"Failed to run fix script: {e}")
                            
                dialog.connect("response", on_dialog_response)
                dialog.present()
            
        except Exception as e:
            self.show_error_dialog(_('Error'), str(e))
            self.is_hosting = False
            self.sync_ui_state() # Revert state
        finally: 
            self.loading_bar.set_visible(False)
            self.start_button.set_sensitive(True) # Reabilitar botão
            self.start_btn_spinner.stop()
            self.start_btn_spinner.set_visible(False)
        
    def stop_hosting(self, b=None):
        print("DEBUG: HostView.stop_hosting called")
        self.show_toast(_("Stopping server..."))
        self.loading_bar.set_visible(True); self.loading_bar.pulse()
        self.audio_mixer_expander.set_visible(self.streaming_audio_row.get_active())
        self.stop_audio_mixer_refresh()
        
        if hasattr(self, 'stop_pin_listener') and self.stop_pin_listener:
            try: self.stop_pin_listener()
            except: pass
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
        
        # Hard kill fallback
        subprocess.run(['pkill', '-9', 'sunshine'], stderr=subprocess.DEVNULL)
            
        self.is_hosting = False
        self.sync_ui_state()
        self.loading_bar.set_visible(False)
        self.start_button.set_sensitive(True) 
        self.start_btn_spinner.stop()
        self.start_btn_spinner.set_visible(False)
        self.show_toast(_('Server stopped'))
        
    def update_status_info(self):
        sunshine_running = self.check_process_running('sunshine')
        
        # If it was supposed to be hosting but sunshine is not running
        if self.is_hosting and not sunshine_running:
             print("DEBUG: Sunshine crashed or was stopped externally. Updating UI.")
             self.is_hosting = False
             self.sync_ui_state()
             self.show_toast(_("Sunshine stopped unexpectedly"))
             return True

        if not self.is_hosting: return True
        
        if hasattr(self, 'sunshine_val'):
            self.sunshine_val.set_markup('<span color="#2ec27e">On-line</span>' if sunshine_running else '<span color="#e01b24">Parado</span>')
        ipv4, ipv6 = self.get_ip_addresses()
        self.update_field('ipv4', ipv4); self.update_field('ipv6', ipv6)
        return True
        
    def check_process_running(self, process_name):
        try:
            subprocess.check_output(["pgrep", "-x", process_name])
            return True
        except: return False
            
    def get_ip_addresses(self):
        ipv4 = ipv6 = "None"
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("1.1.1.1", 80)); ipv4 = s.getsockname()[0]
        except: pass
        try:
            res = subprocess.run(['ip', '-j', 'addr'], capture_output=True, text=True)
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
        except: pass

        
        # No longer wrapping in brackets as per user feedback
            
        return ipv4, ipv6
        
    def show_error_dialog(self, title, message):
        dialog = Adw.MessageDialog.new(self.get_root(), title, message)
        dialog.add_response('ok', 'OK')
        dialog.present()
    
    def show_toast(self, message):
        window = self.get_root()
        if hasattr(window, 'show_toast'): window.show_toast(message)
        else: print(f"Toast: {message}")
        
    def open_sunshine_config(self, button):
        subprocess.Popen(['xdg-open', 'https://localhost:47990'])

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
        h.update({
            'mode_idx': self.game_mode_row.get_selected(),
            'custom_name': self.custom_name_entry.get_text(),
            'custom_cmd': self.custom_cmd_entry.get_text(),
            'quality_idx': self.quality_row.get_selected(),
            'players': int(self.players_row.get_value()),
            'monitor_idx': self.monitor_row.get_selected(),
            'gpu_idx': self.gpu_row.get_selected(),
            'platform_idx': self.platform_row.get_selected(),
            'audio': self.streaming_audio_row.get_active(), # Now maps to Streaming Audio
            'audio_output_idx': self.audio_output_row.get_selected(),
            'input_sharing': self.input_row.get_active(),
            'upnp': self.upnp_row.get_active(),
            'ipv6': self.ipv6_row.get_active(),
            'webui_anyone': self.webui_anyone_row.get_active()
        })
        self.config.set('host', h)

    def load_settings(self):
        self.loading_settings = True
        try:
            h = self.config.get('host', {})
            if not h: return
            self.game_mode_row.set_selected(h.get('mode_idx', 0))
            self.custom_name_entry.set_text(h.get('custom_name', ''))
            self.custom_cmd_entry.set_text(h.get('custom_cmd', ''))
            self.quality_row.set_selected(h.get('quality_idx', 2))
            self.players_row.set_value(h.get('players', 2))
            self.monitor_row.set_selected(h.get('monitor_idx', 0))
            self.gpu_row.set_selected(h.get('gpu_idx', 0))
            self.platform_row.set_selected(h.get('platform_idx', 0))
            
            # Audio
            streaming_active = h.get('audio', True)
            self.streaming_audio_row.set_active(streaming_active)
            self.audio_mixer_expander.set_visible(streaming_active)
            
            self.audio_output_row.set_selected(h.get('audio_output_idx', 0))
            
            self.input_row.set_active(h.get('input_sharing', True))
            self.upnp_row.set_active(h.get('upnp', True))
            self.ipv6_row.set_active(h.get('ipv6', True))
            self.webui_anyone_row.set_active(h.get('webui_anyone', False))
        finally:
            self.loading_settings = False

    def connect_settings_signals(self):
        for r in [self.game_mode_row, self.quality_row, self.monitor_row, self.gpu_row, self.platform_row, self.audio_output_row]:
            r.connect('notify::selected', self.save_host_settings)
        for r in [self.streaming_audio_row, self.input_row, self.upnp_row, self.ipv6_row, self.webui_anyone_row]:
            r.connect('notify::active', self.save_host_settings)
        for r in [self.custom_name_entry, self.custom_cmd_entry]:
            r.connect('notify::text', self.save_host_settings)
        self.players_row.get_adjustment().connect('value-changed', self.save_host_settings)

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
        if self.is_hosting: self.stop_hosting()
        if hasattr(self, 'stop_pin_listener'): self.stop_pin_listener()
        if hasattr(self, 'audio_manager'): self.audio_manager.cleanup()
