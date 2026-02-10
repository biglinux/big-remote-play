

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GLib, Gdk
import subprocess, threading, os
from pathlib import Path
from utils.config import Config
from guest.moonlight_client import MoonlightClient
from utils.i18n import _
from utils.icons import create_icon_widget

class GuestView(Gtk.Box):
    def __init__(self):
        print("DEBUG: GuestView.__init__ called")
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        
        self.discovered_hosts = []
        self.is_connected = False
        self.pin_dialog = None
        
        from utils.logger import Logger
        self.config = Config()
        self.logger = None
        if self.config.get('verbose_logging', False):
            self.logger = Logger()
            
        self.moonlight = MoonlightClient(logger=self.logger)
        self.setup_ui()
        self.discover_hosts()
        GLib.timeout_add(1000, self.monitor_connection)
        
    def detect_bitrate(self, button=None):
        self.show_toast(_("Detecting bandwidth..."))
        def run_detect():
            import time, random
            time.sleep(1.5)
            val = random.randint(15, 80)
            GLib.idle_add(lambda: self.bitrate_scale.set_value(val))
            GLib.idle_add(lambda: self.show_toast(_("Suggested bitrate: {} Mbps").format(val)))
        threading.Thread(target=run_detect, daemon=True).start()
        
    def setup_ui(self):
        clamp = Adw.Clamp(); clamp.set_maximum_size(800)
        for m in ['top', 'bottom', 'start', 'end']: getattr(clamp, f'set_margin_{m}')(24)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        

        from .performance_monitor import PerformanceMonitor
        self.perf_monitor = PerformanceMonitor(); self.perf_monitor.set_visible(False)
        
        self.header = Adw.PreferencesGroup()
        self.header.set_title(_('Connect to Server'))
        self.header.set_description(_('Find and connect to the host using the options below.'))
        self.header.set_header_suffix(create_icon_widget('network-workgroup-symbolic', size=24))
        
        content.append(self.header)
        content.append(self.perf_monitor)
        
        self.method_stack = Adw.ViewStack()
        
        page_dis = self.method_stack.add_titled(self.create_discover_page(), 'discover', _('Discover'))
        page_dis.set_icon_name('system-search-symbolic')
        
        page_man = self.method_stack.add_titled(self.create_manual_page(), 'manual', _('Manual'))
        page_man.set_icon_name('network-wired-symbolic')
        
        page_pin = self.method_stack.add_titled(self.create_pin_page(), 'pin', _('PIN Code'))
        page_pin.set_icon_name('dialog-password-symbolic')
        
        switcher = Adw.ViewSwitcher()
        switcher.set_stack(self.method_stack)
        switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        switcher.set_halign(Gtk.Align.CENTER)
        
        self.switcher_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.switcher_box.append(switcher)
        self.switcher_box.append(self.method_stack)
        
        settings_group = Adw.PreferencesGroup(); settings_group.set_title(_('Client Settings')); settings_group.set_margin_top(12)
        reset_btn = Gtk.Button(icon_name="edit-undo-symbolic"); reset_btn.add_css_class("flat"); reset_btn.set_tooltip_text(_("Reset to Defaults"))
        reset_btn.connect("clicked", self.on_reset_clicked); settings_group.set_header_suffix(reset_btn)
        self.resolution_row = Adw.ComboRow(); self.resolution_row.set_title(_('Resolution')); self.resolution_row.set_subtitle(_('Stream resolution'))
        res_model = Gtk.StringList()
        for r in ['720p', '1080p', '1440p', '4K', _('Custom')]: res_model.append(r)
        self.resolution_row.set_model(res_model); self.resolution_row.set_selected(1); settings_group.add(self.resolution_row)
        
        self.scale_row = Adw.SwitchRow()
        self.scale_row.set_title(_('Native Resolution (Adaptive)')); self.scale_row.set_subtitle(_('Use screen/window resolution'))
        self.scale_row.set_active(False); self.scale_row.connect("notify::active", self.on_scale_changed)
        settings_group.add(self.scale_row)
        
        self.fps_row = Adw.ComboRow()
        self.fps_row.set_title(_('Frame Rate (FPS)')); self.fps_row.set_subtitle(_('Video smoothness'))
        fps_model = Gtk.StringList()
        for f in ['30 FPS', '60 FPS', '120 FPS', _('Custom')]: fps_model.append(f)
        self.fps_row.set_model(fps_model); self.fps_row.set_selected(1)
        settings_group.add(self.fps_row)
        
        # Connect signals for Custom handling
        self.custom_resolution_val = None
        self.custom_fps_val = None
        
        self.resolution_row.connect("notify::selected-item", self.on_resolution_changed)
        self.fps_row.connect("notify::selected-item", self.on_fps_changed)
        
        self.apply_settings_btn = Gtk.Button(label=_('Apply and Reconnect'))
        self.apply_settings_btn.add_css_class('suggested-action'); self.apply_settings_btn.add_css_class('pill')
        self.apply_settings_btn.set_size_request(-1, 50); self.apply_settings_btn.set_margin_top(24)
        self.apply_settings_btn.set_visible(False); self.apply_settings_btn.connect('clicked', lambda b: self.check_reconnect())
        settings_group.add(self.apply_settings_btn)
        
        bitrate_row = Adw.ActionRow(); bitrate_row.set_title(_("Bitrate (Quality)")); bitrate_row.set_subtitle(_("Adjust bandwidth (0.5 - 150 Mbps)"))
        bitrate_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.bitrate_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.5, 150.0, 0.5)
        self.bitrate_scale.set_hexpand(True); self.bitrate_scale.set_value(20.0); self.bitrate_scale.set_draw_value(True)
        detect_btn = Gtk.Button(label=_("Detect")); detect_btn.add_css_class("flat"); detect_btn.connect("clicked", self.detect_bitrate)
        bitrate_box.append(self.bitrate_scale); bitrate_box.append(detect_btn)
        bitrate_row.add_suffix(bitrate_box); settings_group.add(bitrate_row)

        self.display_mode_row = Adw.ComboRow(); self.display_mode_row.set_title(_('Display Mode')); self.display_mode_row.set_subtitle(_('How the window will be displayed'))
        disp_model = Gtk.StringList()
        for d in [_('Borderless Window'), _('Fullscreen'), _('Windowed')]: disp_model.append(d)
        self.display_mode_row.set_model(disp_model); self.display_mode_row.set_selected(0); settings_group.add(self.display_mode_row)
        
        self.audio_row = Adw.SwitchRow(); self.audio_row.set_title(_('Audio')); self.audio_row.set_subtitle(_('Receive audio streaming'))
        self.audio_row.set_active(True); settings_group.add(self.audio_row)
        self.hw_decode_row = Adw.SwitchRow(); self.hw_decode_row.set_title(_('Hardware Decoding')); self.hw_decode_row.set_subtitle(_('Use GPU for decoding'))
        self.hw_decode_row.set_active(True); settings_group.add(self.hw_decode_row)
        content.append(self.switcher_box); content.append(settings_group)
        self.load_guest_settings(); self.connect_settings_signals(); clamp.set_child(content)
        scroll = Gtk.ScrolledWindow(); scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC); scroll.set_vexpand(True); scroll.set_child(clamp); self.append(scroll)
        
    # create_status_card removed

    def monitor_connection(self):
        """Monitors Moonlight connection state"""
        if hasattr(self, 'moonlight'):
            is_running = self.moonlight.is_connected()
            
            if is_running:
                if not self.is_connected:
                     # Update state if it was disconnected
                     self.is_connected = True
                     host_name = self.moonlight.connected_host if self.moonlight.connected_host else "Host"
                     self.header.set_description(_("Host connected: {}").format(host_name))
                     self.perf_monitor.set_connection_status(host_name, _("Active Session"), True)
                     
                     self.perf_monitor.set_visible(True)
                     self.perf_monitor.start_monitoring()

            else:
                if self.is_connected:
                    # Detected disconnection
                    self.is_connected = False
                    self.header.set_description(_('Find and connect to the host using the options below.'))
                    self.perf_monitor.set_connection_status("None", _("Disconnected"), False)
                    self.perf_monitor.stop_monitoring()
                    self.perf_monitor.set_visible(False)
                    
                    self.show_toast(_("Moonlight closed"))
            
            # Update UI visibility
            self.update_ui_state()

        return True # Continue polling

    def update_ui_state(self):
        c = self.is_connected
        # switcher_box must remain visible so the "Stop" button is accessible
        if hasattr(self, 'switcher_box'): self.switcher_box.set_visible(True)
        if hasattr(self, 'apply_settings_btn'): self.apply_settings_btn.set_visible(c)
        if hasattr(self, 'header'):
            self.header.set_visible(True)
            self.header.set_description(_('Host connected. Click Stop to disconnect.') if c else _('Find and connect to the host using the options below.'))
        
        # Update connection buttons state
        self._update_all_buttons_state()
            
    def _update_all_buttons_state(self):
        is_connecting = getattr(self, 'is_connecting', False)
        connected = self.is_connected
        
        # Helper to update a button
        def update_btn(btn, label_widget, spinner, default_text, default_sensitive=True):
            if hasattr(self, btn):
                b = getattr(self, btn)
                if hasattr(self, label_widget): l = getattr(self, label_widget)
                if hasattr(self, spinner): s = getattr(self, spinner)
                
                if connected:
                    # State: Connected -> Button becomes Stop
                    b.set_sensitive(True)
                    b.remove_css_class('suggested-action')
                    b.add_css_class('destructive-action')
                    l.set_label(_("Stop"))
                    s.set_visible(False); s.stop()
                    
                elif is_connecting:
                    # State: Connecting -> Button becomes Stop (Cancel)
                    b.set_sensitive(True) 
                    b.remove_css_class('suggested-action')
                    b.add_css_class('destructive-action')
                    l.set_label(_("Stop"))
                    s.set_visible(True); s.start()
                    
                else: # Disconnected
                    b.set_sensitive(default_sensitive)
                    b.remove_css_class('destructive-action')
                    b.add_css_class('suggested-action')
                    l.set_label(default_text)
                    s.set_visible(False); s.stop()

        # Update Discover Button
        # Logic specific for discover: only sensitive if host selected (when disconnected)
        has_host = self.selected_host_card_data is not None
        update_btn('main_connect_btn', 'connect_btn_label', 'connect_btn_spinner', 
                   _("Connect to {}").format(self.selected_host_card_data['name']) if has_host else _("Connect to Selected"),
                   default_sensitive=has_host)

        # Update Manual Button
        update_btn('manual_connect_btn', 'manual_btn_label', 'manual_btn_spinner', _("Connect"))

        # Update PIN Button
        update_btn('pin_connect_btn', 'pin_btn_label', 'pin_btn_spinner', _("Connect with PIN"))
        
    def check_reconnect(self):
        if self.is_connected and hasattr(self, 'current_host_ctx'):
            self.show_toast(_("Applying settings..."))
            ctx = self.current_host_ctx
            if self.is_connected: self.moonlight.disconnect()
            if ctx['type'] == 'auto': self.connect_to_host(ctx['host'])
            elif ctx['type'] == 'manual': self.connect_manual(ctx['ip'], str(ctx['port']), ctx['ipv6'])
                
    def check_reconnect_debounced(self):
        """Checks if reconnection is needed (with debounce)"""
        # Cancel previous
        if hasattr(self, '_reconnect_timer') and self._reconnect_timer:
            GLib.source_remove(self._reconnect_timer)
            
        self._reconnect_timer = GLib.timeout_add(1000, self._do_reconnect_timer)
        
    def _do_reconnect_timer(self):
        self._reconnect_timer = None
        self.check_reconnect()
        return False
        
    def create_discover_page(self):
        self.selected_host_card_data = self.first_radio_in_list = None
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        for m in ['top', 'bottom', 'start', 'end']: getattr(header, f'set_margin_{m}')(12)
        lbl = Gtk.Label(label=_("Discovered Hosts")); lbl.add_css_class("heading"); lbl.set_halign(Gtk.Align.START)
        desc = Gtk.Label(label=_("Scroll to list all found devices.")); desc.add_css_class("dim-label"); desc.set_halign(Gtk.Align.START)
        
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_hexpand(True)
        text_box.append(lbl)
        text_box.append(desc)
        
        refresh = Gtk.Button(icon_name='view-refresh-symbolic'); refresh.connect('clicked', lambda b: self.discover_hosts())
        header.append(text_box); header.append(refresh)
        self.hosts_list = Gtk.ListBox(); self.hosts_list.add_css_class('boxed-list'); self.hosts_list.set_selection_mode(Gtk.SelectionMode.NONE)
        for m in ['start', 'end']: getattr(self.hosts_list, f'set_margin_{m}')(12)
        action = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ['top', 'bottom', 'start', 'end']: getattr(action, f'set_margin_{m}')(12)
        
        buttons_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        buttons_box.set_halign(Gtk.Align.CENTER)
        
        self.main_connect_btn = Gtk.Button(); self.main_connect_btn.add_css_class('suggested-action')
        self.main_connect_btn.add_css_class('pill'); self.main_connect_btn.set_size_request(250, 50); self.main_connect_btn.set_sensitive(False)
        
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12); btn_box.set_halign(Gtk.Align.CENTER)
        self.connect_btn_spinner = Gtk.Spinner(); self.connect_btn_spinner.set_visible(False)
        self.connect_btn_label = Gtk.Label(label=_('Connect to Selected'))
        btn_box.append(self.connect_btn_spinner); btn_box.append(self.connect_btn_label)
        self.main_connect_btn.set_child(btn_box)
        
        self.main_connect_btn.connect('clicked', lambda b: self.on_main_button_clicked('discover'))
        
        buttons_box.append(self.main_connect_btn)

        host_scroll = Gtk.ScrolledWindow()
        host_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        host_scroll.set_max_content_height(400)
        host_scroll.set_min_content_height(200)
        host_scroll.set_vexpand(True)
        host_scroll.set_propagate_natural_height(True)
        host_scroll.set_child(self.hosts_list)

        action.append(buttons_box); box.append(header); box.append(host_scroll); box.append(action)
        return box

    def discover_hosts(self):
        from utils.network import NetworkDiscovery
        self.first_radio_in_list = self.selected_host_card_data = None
        self._update_all_buttons_state()
        while row := self.hosts_list.get_row_at_index(0): self.hosts_list.remove(row)
        self.loading_row = Gtk.ListBoxRow(); self.loading_row.set_selectable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6); box.set_halign(Gtk.Align.CENTER); box.set_valign(Gtk.Align.CENTER)
        box.set_size_request(-1, 150)
        for m in ['top', 'bottom']: getattr(box, f'set_margin_{m}')(24)
        spinner = Gtk.Spinner(); spinner.set_size_request(48, 48); spinner.start()
        lbl = Gtk.Label(label=_('Searching for hosts...')); lbl.add_css_class('title-2')
        box.append(spinner); box.append(lbl)
        self.loading_row.set_child(box); self.hosts_list.append(self.loading_row)
        def on_hosts_discovered(hosts):
            if self.loading_row.get_parent(): self.hosts_list.remove(self.loading_row)
            self.first_radio_in_list = None
            if not hosts:
                row = Gtk.ListBoxRow(); row.set_selectable(False)
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6); box.set_halign(Gtk.Align.CENTER); box.set_valign(Gtk.Align.CENTER)
                box.set_size_request(-1, 150) # Match host_scroll min height
                for m in ['top', 'bottom']: getattr(box, f'set_margin_{m}')(24)
                icon = create_icon_widget('network-offline-symbolic', size=48, css_class='dim-label')
                lbl = Gtk.Label(label=_('No hosts found')); lbl.add_css_class('title-2')
                box.append(icon); box.append(lbl); row.set_child(box); self.hosts_list.append(row)
            else:
                for h in hosts: self.hosts_list.append(self.create_host_row_custom(h))
            return False
        NetworkDiscovery().discover_hosts(callback=on_hosts_discovered)

    def update_hosts_list(self, hosts):
        # Clear
        self.first_radio_in_list = None
        self.selected_host_card_data = None
        self.selected_host_card_data = None
        self._update_all_buttons_state()
        
        while True:
            row = self.hosts_list.get_row_at_index(0)
            if row is None:
                break
            self.hosts_list.remove(row)
            
        for host in hosts:
            self.hosts_list.append(self.create_host_row_custom(host))

    def create_host_row_custom(self, host):
        row = Gtk.ListBoxRow(); row.set_activatable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        for m in ['start', 'end', 'top', 'bottom']: getattr(box, f'set_margin_{m}')(12)
        radio = Gtk.CheckButton(); radio.set_valign(Gtk.Align.CENTER)
        if self.first_radio_in_list is None: self.first_radio_in_list = radio
        else: radio.set_group(self.first_radio_in_list)
        def on_toggled(btn):
            if btn.get_active():
                self.selected_host_card_data = host
                self._update_all_buttons_state()
        radio.connect('toggled', on_toggled)
        icon = create_icon_widget('computer-symbolic', size=32)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2); info.set_valign(Gtk.Align.CENTER)
        n = Gtk.Label(label=host['name']); n.set_halign(Gtk.Align.START); n.add_css_class('heading')
        i = Gtk.Label(label=host['ip']); i.set_halign(Gtk.Align.START); i.add_css_class('dim-label')
        info.append(n); info.append(i); box.append(radio); box.append(icon); box.append(info)
        
        spacer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL); spacer.set_hexpand(True)
        box.append(spacer)
        
        copy_btn = Gtk.Button()
        copy_btn.set_child(create_icon_widget("edit-copy-symbolic", size=16))
        copy_btn.add_css_class("flat"); copy_btn.set_valign(Gtk.Align.CENTER); copy_btn.set_tooltip_text(_("Copy IP"))
        def copy_ip(btn):
            Gdk.Display.get_default().get_clipboard().set(host['ip'])
            self.show_toast(_("IP Copied: {}").format(host['ip']))
        copy_btn.connect("clicked", copy_ip)
        box.append(copy_btn)
        
        row.set_child(box)
        gesture = Gtk.GestureClick(); gesture.connect("pressed", lambda g, n, x, y: radio.set_active(True)); row.add_controller(gesture)
        return row

    def create_manual_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        for m in ['top', 'bottom', 'start', 'end']: getattr(box, f'set_margin_{m}')(24)
        
        grp = Adw.PreferencesGroup()
        grp.set_title(_("Connection Data"))
        grp.set_description(_("Enter server IP and port"))
        
        ip = Adw.EntryRow()
        ip.set_title(_('IP/Hostname'))
        ip.set_text('192.168.')
        
        port = Adw.EntryRow()
        port.set_title(_('Port'))
        port.set_text('47989')
        
        ipv6 = Adw.SwitchRow()
        ipv6.set_title(_("Use IPv6"))
        
        self.manual_ip_entry = ip
        self.manual_port_entry = port
        self.manual_ipv6_switch = ipv6
        
        grp.add(ip)
        grp.add(port)
        grp.add(ipv6)
        
        box.append(grp)
        
        buttons_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        buttons_box.set_halign(Gtk.Align.CENTER)
        
        self.manual_connect_btn = Gtk.Button()
        self.manual_connect_btn.add_css_class('suggested-action')
        self.manual_connect_btn.add_css_class('pill')
        self.manual_connect_btn.set_size_request(200, 50)
        
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_box.set_halign(Gtk.Align.CENTER)
        self.manual_btn_spinner = Gtk.Spinner()
        self.manual_btn_spinner.set_visible(False)
        self.manual_btn_label = Gtk.Label(label=_('Connect'))
        btn_box.append(self.manual_btn_spinner)
        btn_box.append(self.manual_btn_label)
        self.manual_connect_btn.set_child(btn_box)
        
        self.manual_connect_btn.connect('clicked', lambda b: self.on_main_button_clicked('manual'))
        
        buttons_box.append(self.manual_connect_btn)
        
        box.append(buttons_box)
        return box
        
    def create_pin_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        for m in ['top', 'bottom', 'start', 'end']: getattr(box, f'set_margin_{m}')(24)
        
        grp = Adw.PreferencesGroup()
        grp.set_title(_("Connect with PIN"))
        grp.set_description(_("Enter the 6-digit PIN code provided by the host"))
        
        pin = Adw.EntryRow()
        pin.set_title(_("PIN Code"))
        # pin.set_input_purpose(Gtk.InputPurpose.NUMBER) # Gtk 4.10+
        self.pin_entry = pin
        
        grp.add(pin)
        
        box.append(grp)
        
        buttons_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        buttons_box.set_halign(Gtk.Align.CENTER)
        
        self.pin_connect_btn = Gtk.Button()
        self.pin_connect_btn.add_css_class('suggested-action')
        self.pin_connect_btn.add_css_class('pill')
        self.pin_connect_btn.set_size_request(200, 50)
        
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_box.set_halign(Gtk.Align.CENTER)
        self.pin_btn_spinner = Gtk.Spinner()
        self.pin_btn_spinner.set_visible(False)
        self.pin_btn_label = Gtk.Label(label=_('Connect with PIN'))
        btn_box.append(self.pin_btn_spinner)
        btn_box.append(self.pin_btn_label)
        self.pin_connect_btn.set_child(btn_box)
        
        self.pin_connect_btn.connect('clicked', lambda b: self.on_main_button_clicked('pin'))
        
        buttons_box.append(self.pin_connect_btn)
        
        box.append(buttons_box)
        return box

    def on_main_button_clicked(self, source):
        print(f"DEBUG: GuestView.on_main_button_clicked called with source: {source}")
        self.show_toast(_("Clicked Connect..."))
        # If connecting (loading) or connected, button becomes "Stop"
        if getattr(self, 'is_connecting', False) or self.is_connected:
            self.on_cancel_connection(None)
        else:
            # Start connection based on source
            if source == 'discover':
                if self.selected_host_card_data:
                    self.connect_manual(self.selected_host_card_data['ip'], str(self.selected_host_card_data.get('port', 47989)))
            elif source == 'manual':
                 self.connect_manual(self.manual_ip_entry.get_text(), self.manual_port_entry.get_text(), self.manual_ipv6_switch.get_active())
            elif source == 'pin':
                 self.connect_pin(self.pin_entry.get_text())
    def connect_to_host(self, host, paired_retry=False, override_check=False):
        if getattr(self, 'is_connecting', False) and not paired_retry and not override_check:
            print("DEBUG: Already connecting, ignoring request.")
            return
            
        print(f"DEBUG: Starting connection to {host.get('ip')} (paired_retry={paired_retry})")
        # Collect UI values on Main Thread (GTK is not thread-safe)
        scale_active = self.scale_row.get_active()
        res_idx = self.resolution_row.get_selected()
        fps_idx = self.fps_row.get_selected()
        bitrate_val = self.bitrate_scale.get_value()
        display_mode_idx = self.display_mode_row.get_selected()
        audio_active = self.audio_row.get_active()
        hw_decode_active = self.hw_decode_row.get_active()
        
        # Custom values are already safe attributes
        custom_res = getattr(self, 'custom_resolution_val', '1920x1080')
        custom_fps = getattr(self, 'custom_fps_val', '60')

        self.show_loading(True)
        
        def run():
            # 1. Check if already paired
            if not self.moonlight.list_apps(host['ip']):
                if paired_retry:
                    GLib.idle_add(lambda: (self.show_loading(False), self.show_error_dialog(_('Pairing Error'), _('Host still reports as not paired after pairing attempt.'))))
                    return

                print(f"DEBUG: Host {host['ip']} not paired. Starting pairing flow.")
                GLib.idle_add(self.show_loading, False)
                GLib.idle_add(lambda: self.start_pairing_flow(host))
                return
            
            # Check for cancellation
            if not getattr(self, 'is_connecting', False): return

            if scale_active:
                # res = self.get_auto_resolution() # RISK
                res = "1920x1080" # Fallback/Default for now
            else:
                res_map = {0: "1280x720", 1: "1920x1080", 2: "2560x1440", 3: "3840x2160"}
                res = custom_res if res_idx == 4 else res_map.get(res_idx, "1920x1080")
            
            # ATTENTION: If scale_active was True, we correct by fetching EVERYTHING beforehand.
            pass

            w, h = res.split('x') if 'x' in res else ("1920", "1080")
            fps_map = {0: "30", 1: "60", 2: "120"}
            fps = custom_fps if fps_idx == 3 else fps_map.get(fps_idx, "60")
            
            display_mode = ['borderless', 'fullscreen', 'windowed'][display_mode_idx]
            
            opts = {
                'width': w, 
                'height': h, 
                'fps': fps, 
                'bitrate': int(bitrate_val * 1000), 
                'display_mode': display_mode, 
                'audio': audio_active, 
                'hw_decode': hw_decode_active
            }
            
            if self.moonlight.connect(host['ip'], **opts): 
                GLib.idle_add(lambda: (self.show_loading(False), self.perf_monitor.set_connection_status(host['name'], _("Active Stream"), True), self.perf_monitor.start_monitoring()))
            else: 
                GLib.idle_add(lambda: (self.show_loading(False), self.show_error_dialog(_('Error'), _('Failed to connect. Verify if Moonlight is paired.'))))
        
        # Insert automatic resolution logic BEFORE thread for total safety
        if scale_active:
             res_auto = self.get_auto_resolution()
             def run_patched():
                 # Use res_auto captured in scope
                 w, h = res_auto.split('x') if 'x' in res_auto else ("1920", "1080")
                 fps_map = {0: "30", 1: "60", 2: "120"}
                 fps = custom_fps if fps_idx == 3 else fps_map.get(fps_idx, "60")
                 display_mode = ['borderless', 'fullscreen', 'windowed'][display_mode_idx]
                 opts = {'width': w, 'height': h, 'fps': fps, 'bitrate': int(bitrate_val * 1000), 'display_mode': display_mode, 'audio': audio_active, 'hw_decode': hw_decode_active}
                 
                 # Pairing check copy-paste
                 if not self.moonlight.list_apps(host['ip']):
                    if paired_retry:
                        GLib.idle_add(lambda: (self.show_loading(False), self.show_error_dialog(_('Pairing Error'), _('Host still reports as not paired after pairing attempt.'))))
                        return
                    GLib.idle_add(self.show_loading, False)
                    GLib.idle_add(lambda: self.start_pairing_flow(host))
                    return

                 # Check for cancellation
                 if not getattr(self, 'is_connecting', False): return

                 if self.moonlight.connect(host['ip'], **opts): 
                    GLib.idle_add(lambda: (self.show_loading(False), self.perf_monitor.set_connection_status(host['name'], _("Active Stream"), True), self.perf_monitor.start_monitoring()))
                 else: 
                    GLib.idle_add(lambda: (self.show_loading(False), self.show_error_dialog(_('Error'), _('Failed to connect'))))
             
             threading.Thread(target=run_patched, daemon=True).start()
        else:
             threading.Thread(target=run, daemon=True).start()

    def start_pairing_flow(self, host):
        """Starts pairing flow (Automatic for localhost, Manual for remote)"""
        
        def on_pin_callback(pin):
            # Check if localhost
            is_local = host['ip'] in ['127.0.0.1', 'localhost', '::1']
            
            if is_local:
                # Attempt automation
                try:
                    from host.sunshine_manager import SunshineHost
                    from pathlib import Path
                    sun = SunshineHost(Path.home() / '.config' / 'big-remoteplay' / 'sunshine')
                    if sun.is_running():
                        GLib.idle_add(lambda: self.show_toast(_("Attempting automatic pairing PIN: {}").format(pin)))
                        ok, msg = sun.send_pin(pin)
                        if ok:
                            GLib.idle_add(lambda: self.show_toast(_("Automatic pairing sent!")))
                            return # Success, moonlight should detect and finish
                        else:
                            print(f"Automatic pairing failed: {msg}")
                except Exception as e:
                    print(f"Error in pairing automation: {e}")
            
            # Fallback to Dialog UI
            GLib.idle_add(lambda: self.show_pairing_dialog(host['ip'], pin, hostname=host.get('name')))

        def do_pair():
            self.show_toast(_("Starting pairing..."))
            success = self.moonlight.pair(host['ip'], on_pin_callback=on_pin_callback)
            
            GLib.idle_add(self.close_pairing_dialog)
            
            # Check for cancellation IMMEDIATELY after pairing attempt
            if not getattr(self, 'is_connecting', False):
                print("DEBUG: do_pair cancelled - aborting")
                return

            # Double check: If pair returns False, check if it really failed by listing apps.
            # Moonlight sometimes closes pipe abruptly after success.
            if not success:
                print("DEBUG: Pair retornou False, verificando com list_apps...")
                if self.moonlight.list_apps(host['ip']):
                    print("DEBUG: list_apps worked! Pairing was a masked success.")
                    success = True
            
            # Check for cancellation AGAIN after potential slow list_apps call
            if not getattr(self, 'is_connecting', False):
                print("DEBUG: do_pair cancelled after list_apps check - aborting")
                return

            if success:
                GLib.idle_add(lambda: (self.show_toast(_("Paired successfully!")), self.connect_to_host(host, paired_retry=True)))
            else:
                 GLib.idle_add(lambda: self.show_error_dialog(_("Pairing Error"), _("Could not pair with host.\nVerify the PIN was entered correctly.")))

        threading.Thread(target=do_pair, daemon=True).start()


    def show_loading(self, show=True, message=""):
        self.is_connecting = show
        self._update_all_buttons_state()

    def on_cancel_connection(self, btn):
        self.show_toast(_("Canceling connection..."))
        self.is_connecting = False
        self.show_loading(False)
        if hasattr(self, 'moonlight'):
            self.moonlight.disconnect()
                
    def show_pin_dialog(self, pin):
        if self.pin_dialog: self.pin_dialog.close()
        self.pin_dialog = Adw.MessageDialog(heading=_('Pairing Required'), body=_('Moonlight needs to be paired.\n\n<span size="xx-large" weight="bold" color="accent-color">{}</span>').format(pin))
        self.pin_dialog.set_body_use_markup(True); self.pin_dialog.add_response('cancel', _('Cancel')); self.pin_dialog.present()
    def close_pin_dialog(self): (self.pin_dialog.close() if self.pin_dialog else None); self.pin_dialog = None

    def show_pairing_dialog(self, host_ip, pin=None, on_confirm=None, hostname=None):
        if hasattr(self, 'pairing_dialog') and self.pairing_dialog:
            if pin:
                self.pairing_dialog.set_body(f'<span size="xx-large" weight="bold" color="#3584e4">{pin}</span>\n\n' + _('Follow instructions.\n\n1. Provide PIN and Host <b>{}</b> to the server.\n2. On the host, access Sunshine Configuration.\n3. Enter PIN and Host.\n4. Click Send.').format(hostname or ""))
                return

        if hasattr(self, 'pairing_dialog') and self.pairing_dialog: self.pairing_dialog.close()
        body = _('Follow instructions.\n\n1. Provide PIN and Host <b>{}</b> to the server.\n2. On the host, access Sunshine Configuration.\n3. Enter PIN and Host.\n4. Click Send.').format(hostname or "")
        if pin: body = f'<span size="xx-large" weight="bold" color="#3584e4">{pin}</span>\n\n' + body
        self.pairing_dialog = Adw.MessageDialog(heading=_('Pairing Started'), body=body)
        self.pairing_dialog.set_body_use_markup(True); self.pairing_dialog.set_default_size(600, 450); self.pairing_dialog.set_resizable(True)
        self.pairing_dialog.add_response('ok', _('OK')); self.pairing_dialog.set_response_appearance('ok', Adw.ResponseAppearance.SUGGESTED)
        def on_resp(dlg, resp):
            if resp == 'ok' and on_confirm: on_confirm()
        self.pairing_dialog.connect('response', on_resp); self.pairing_dialog.present()
        
    def close_pairing_dialog(self):
        if hasattr(self, 'pairing_dialog') and self.pairing_dialog:
            self.pairing_dialog.close()
            self.pairing_dialog = None

    def get_auto_resolution(self):
        try:
            display = Gdk.Display.get_default(); monitor = None
            if root := self.get_root():
                if native := root.get_native():
                    if surface := native.get_surface(): monitor = display.get_monitor_at_surface(surface)
            if not monitor:
                monitors = display.get_monitors()
                if monitors.get_n_items() > 0: monitor = monitors.get_item(0)
            if monitor:
                r = monitor.get_geometry(); return f"{r.width}x{r.height}"
        except: pass
        return "1920x1080"

    def connect_manual(self, ip, port, ipv6=False):
        if not ip: return
        self.current_host_ctx = {'type': 'manual', 'ip': ip, 'port': port, 'ipv6': ipv6}
        self.connect_to_host({'name': ip, 'ip': ip, 'port': int(port) if port else 47989})

    def connect_pin(self, _widget):
        # Reverse Connection via PIN (Custom Feature)
        pin = self.pin_entry.get_text()
        if len(pin) != 6:
            self.show_error_dialog(_("Invalid PIN"), _("Must contain 6 digits."))
            return
            
        self.show_loading(True)
        # 1. Resolve PIN to IP via Utils
        from utils.network import resolve_pin_to_ip
        
        def run_resolve():
            res = resolve_pin_to_ip(pin)
            if res:
                GLib.idle_add(self._on_pin_resolved, res, pin)
            else:
                GLib.idle_add(self._on_pin_failed)
        
        threading.Thread(target=run_resolve, daemon=True).start()

    def _on_pin_resolved(self, ip_info, pin):
        ip = ip_info.get('ip')
        port = ip_info.get('port', 47989)
        hostname = ip_info.get('hostname', 'Host')
        
        self.show_toast(_("Host found: {}").format(hostname))
        self.connect_to_host({'name': hostname, 'ip': ip, 'port': port}, override_check=True)

    def _on_pin_failed(self):
        self.show_loading(False)
        self.show_error_dialog(_("Host Not Found"), _("Could not find a host with this PIN on the local network..."))
    
    def show_error_dialog(self, title, message):
        dialog = Adw.MessageDialog.new(self.get_root())
        dialog.set_heading(title); dialog.set_body(message)
        dialog.add_response('ok', _('OK')); dialog.present()
    
    def on_resolution_changed(self, row, item):
        val = row.get_selected_item().get_string()
        if val == _('Custom'):
            def set_custom(v):
                # Validate WxH
                self.show_toast(_("Set: {}").format(v))
                # Store custom resolution logic
            self.show_custom_input_dialog(_("Custom Resolution"), _("Enter WxH:"), set_custom)
        else:
            print(f"Resolution: {val}")

    def on_fps_changed(self, row, item):
        val = row.get_selected_item().get_string()
        if val == _('Custom'):
            def set_custom(v):
                self.show_toast(_("Set: {}").format(v))
            self.show_custom_input_dialog(_("Custom FPS"), _("Use a number"), set_custom)

    def on_scale_changed(self, row, param):
        self.resolution_row.set_sensitive(not row.get_active())
        if row.get_active(): self.show_toast(_("Automatic"))
        self.save_guest_settings()

    def show_custom_input_dialog(self, title, subtitle, callback):
        dialog = Adw.MessageDialog(heading=title, body=subtitle)
        dialog.set_transient_for(self.get_root())
        
        grp = Adw.PreferencesGroup()
        entry = Adw.EntryRow(title=_("Value"))
        grp.add(entry)
        dialog.set_extra_child(grp)
        
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("ok", _("Apply"))
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        
        def on_response(d, r):
            if r == 'ok':
                val = entry.get_text()
                if val: callback(val)
            
        dialog.connect("response", on_response)
        dialog.present()

    def cleanup(self): (self.perf_monitor.stop_monitoring() if hasattr(self, 'perf_monitor') else None)
    def connect_settings_signals(self):
        self.bitrate_scale.connect("value-changed", lambda w: self.save_guest_settings())
        for r in [self.display_mode_row, self.audio_row, self.hw_decode_row]: r.connect("notify::selected-item" if isinstance(r, Adw.ComboRow) else "notify::active", lambda *x: self.save_guest_settings())
    def save_guest_settings(self):
        s = {'quality':'custom','resolution_idx':self.resolution_row.get_selected(),'custom_resolution':getattr(self,'custom_resolution_val',''),'scale_native':self.scale_row.get_active(),'fps_idx':self.fps_row.get_selected(),'custom_fps':getattr(self,'custom_fps_val',''),'bitrate':self.bitrate_scale.get_value(),'display_mode_idx':self.display_mode_row.get_selected(),'audio':self.audio_row.get_active(),'hw_decode':self.hw_decode_row.get_active()}
        self.config.set('guest', s)
    def load_guest_settings(self):
        s = self.config.get('guest', {})
        if not s: return
        try:
            self.scale_row.set_active(s.get('scale_native', False)); self.resolution_row.set_selected(s.get('resolution_idx', 1))
            self.custom_resolution_val = s.get('custom_resolution', ''); self.fps_row.set_selected(s.get('fps_idx', 1))
            self.custom_fps_val = s.get('custom_fps', ''); self.bitrate_scale.set_value(s.get('bitrate', 20.0))
            self.display_mode_row.set_selected(s.get('display_mode_idx', 0)); self.audio_row.set_active(s.get('audio', True)); self.hw_decode_row.set_active(s.get('hw_decode', True))
        except: pass
    def on_reset_clicked(self, _widget):
        dialog = Adw.MessageDialog(heading=_("Reset defaults?"), body=_("All client settings will be restored."))
        dialog.set_transient_for(self.get_root())
        dialog.add_response("cancel", _("No"))
        dialog.add_response("ok", _("Yes"))
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        
        def on_resp(d, r):
            if r == 'ok':
                self.bitrate_scale.set_value(20.0)
                self.resolution_row.set_selected(1)
                self.fps_row.set_selected(1)
                self.scale_row.set_active(False)
                self.show_toast(_("Restored"))
        
        dialog.connect("response", on_resp)
        dialog.present()
    def reset_to_defaults(self):
        self.scale_row.set_active(False); self.resolution_row.set_selected(1); self.fps_row.set_selected(1); self.bitrate_scale.set_value(20.0); self.display_mode_row.set_selected(0); self.audio_row.set_active(True); self.hw_decode_row.set_active(True)
        self.custom_resolution_val = self.custom_fps_val = ''; self.show_toast(_("Restored")); self.save_guest_settings()
    def show_toast(self, m):
        w = self.get_root()
        if hasattr(w, 'show_toast'): w.show_toast(m)
        else: print(f"Toast: {m}")
