import gi
gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gio
import threading
from .host_view import HostView
from .guest_view import GuestView
from .installer_window import InstallerWindow
from utils.network import NetworkDiscovery
from utils.system_check import SystemCheck
from utils.icons import create_icon_widget, set_icon
from utils.i18n import _
import os
import subprocess
import shutil

# Service Definitions
SERVICE_METADATA = {
    'sunshine': {
        'name': 'SUNSHINE',
        'full_name': _('Sunshine Game Stream Host'),
        'description': _('High-performance game stream host. Required to share your games.'),
        'type': 'service',
        'unit': 'sunshine.service',
        'user': True # Run with systemctl --user
    },
    'moonlight': {
        'name': 'MOONLIGHT',
        'full_name': _('Moonlight Game Stream Client'),
        'description': _('Game stream client. Required to connect to other hosts.'),
        'type': 'app',
        'bin': 'moonlight-qt'
    },
    'docker': {
        'name': 'DOCKER',
        'full_name': _('Docker Engine'),
        'description': _('Container platform. Required for the private network server.'),
        'type': 'service',
        'unit': 'docker.service',
        'user': False
    },
    'tailscale': {
        'name': 'TAILSCALE',
        'full_name': _('Tailscale / Headscale'),
        'description': _('Mesh VPN service. Required for private network connectivity.'),
        'type': 'service',
        'unit': 'tailscaled.service',
        'user': False
    }
}

# Navigation Categories
NAVIGATION_PAGES = {
    'welcome': {
        'name': _('Home'),
        'icon': 'go-home-symbolic',
        'description': _('Home Page')
    },
    'host': {
        'name': _('Host Server'),
        'icon': 'network-server-symbolic',
        'description': _('Share your games')
    },
    'guest': {
        'name': _('Connect to Server'),
        'icon': 'network-workgroup-symbolic',
        'description': _('Connect to a host')
    },
    'section_private': {
        'name': _('Private Network'),
        'type': 'separator'
    },
    'create_private': {
        'name': _('Create Private Network'),
        'icon': 'network-wired-symbolic',
        'description': _('Setup Headscale server')
    },
    'connect_private': {
        'name': _('Connect to Private Network'),
        'icon': 'network-vpn-symbolic',
        'description': _('Join a private network')
    }
}

class MainWindow(Adw.ApplicationWindow):
    """Main window with modern side navigation"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
        self.set_title('Big Remote Play')
        self.set_default_size(950, 650)
        
        self.system_check = SystemCheck()
        self.network = NetworkDiscovery()
        
        # Current State
        self.current_page = 'welcome'
        
        self.setup_ui()
        self.check_system()
        
        # Connect close signal
        self.connect('close-request', self.on_close_request)
        
    def on_close_request(self, window):
        try:
            if hasattr(self, 'host_view'): self.host_view.cleanup()
            if hasattr(self, 'guest_view'): self.guest_view.cleanup()
        except: pass
        return False
        
    def setup_ui(self):
        self.toast_overlay = Adw.ToastOverlay(); self.set_content(self.toast_overlay)
        self.split_view = Adw.NavigationSplitView(); self.toast_overlay.set_child(self.split_view)
        self.setup_sidebar(); self.setup_content()
        self.split_view.set_min_sidebar_width(220); self.split_view.set_max_sidebar_width(280)
        
    def setup_sidebar(self):
        tb = Adw.ToolbarView(); hb = Adw.HeaderBar()
        btn = Gtk.Button(); btn.set_child(create_icon_widget('big-remote-play')); btn.add_css_class('flat')
        btn.connect('clicked', lambda b: self.get_application().activate_action('about', None))
        hb.pack_start(btn)
        
        # Title on the left
        title_lbl = Gtk.Label(label="Big Remote Play")
        title_lbl.add_css_class("heading")
        hb.pack_start(title_lbl)
        
        # Empty center
        hb.set_title_widget(Adw.WindowTitle.new('', ''))
        tb.add_top_bar(hb); main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL); main.set_vexpand(True)
        scroll = Gtk.ScrolledWindow(); scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC); scroll.set_vexpand(True)
        self.nav_list = Gtk.ListBox(); self.nav_list.add_css_class('navigation-sidebar')
        self.nav_list.connect('row-selected', self.on_nav_selected)
        for pid, info in NAVIGATION_PAGES.items():
            if info.get('type') == 'separator':
                sep_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                sep_box.set_margin_top(12)
                sep_box.set_margin_bottom(4)
                sep_box.set_margin_start(12)
                
                label = Gtk.Label(label=info['name'])
                label.add_css_class('caption')
                label.add_css_class('dim-label')
                label.set_halign(Gtk.Align.START)
                sep_box.append(label)
                
                row = Gtk.ListBoxRow()
                row.set_child(sep_box)
                row.set_activatable(False)
                row.set_selectable(False)
                self.nav_list.append(row)
            else:
                self.nav_list.append(self.create_nav_row(pid, info))
        if r := self.nav_list.get_row_at_index(0): self.nav_list.select_row(r)
        scroll.set_child(self.nav_list); main.append(scroll); main.append(self.create_status_footer())
        tb.set_content(main); self.split_view.set_sidebar(Adw.NavigationPage.new(tb, 'Navigation'))
        
    def create_nav_row(self, page_id: str, page_info: dict) -> Gtk.ListBoxRow:
        """Creates navigation row in sidebar"""
        row = Gtk.ListBoxRow()
        row.page_id = page_id
        row.add_css_class('category-row')
        
        # Content Box
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        
        # Icon
        icon = create_icon_widget(page_info['icon'], size=20, css_class='category-icon')
        box.append(icon)
        
        # Label
        label = Gtk.Label(label=page_info['name'])
        label.set_halign(Gtk.Align.START)
        label.set_hexpand(True)
        label.add_css_class('category-label')
        box.append(label)
        
        row.set_child(box)
        return row
    
    def create_status_footer(self):
        """Creates footer with server status"""
        # Main Container
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        footer.set_margin_start(12)
        footer.set_margin_end(12)
        footer.set_margin_top(8)
        footer.set_margin_bottom(12)
        footer.set_spacing(8)
        
        # Separator
        separator = Gtk.Separator()
        separator.set_margin_bottom(8)
        footer.append(separator)
        
        # Label "Service Status"
        status_title = Gtk.Label(label=_('Service Status'))
        status_title.add_css_class('caption')
        status_title.add_css_class('dim-label')
        status_title.set_halign(Gtk.Align.START)
        status_title.set_margin_bottom(4)
        footer.append(status_title)
        
        # Card container
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        card.add_css_class("info-card")
        
        # Status Rows helper
        def add_status_row(container, label_text, dot_attr, lbl_attr, service_id):
            row = Gtk.Box(spacing=10)
            row.add_css_class("info-row")
            
            # Click Gesture
            click = Gtk.GestureClick()
            click.connect("pressed", lambda g, n, x, y, sid=service_id: self.on_service_clicked(sid))
            row.add_controller(click)

            box_key = Gtk.Box(spacing=8)
            box_key.set_hexpand(True)
            dot = create_icon_widget('media-record-symbolic', size=10, css_class=['status-dot', 'status-offline'])
            setattr(self, dot_attr, dot)
            box_key.append(dot)
            lbl_key = Gtk.Label(label=label_text)
            lbl_key.add_css_class('info-key')
            box_key.append(lbl_key)
            row.append(box_key)
            lbl_status = Gtk.Label(label=_('Checking...'))
            lbl_status.add_css_class('info-value')
            lbl_status.set_halign(Gtk.Align.END)
            setattr(self, lbl_attr, lbl_status)
            row.append(lbl_status)
            container.append(row)

        add_status_row(card, 'SUNSHINE', 'sunshine_dot', 'lbl_sunshine_status', 'sunshine')
        add_status_row(card, 'MOONLIGHT', 'moonlight_dot', 'lbl_moonlight_status', 'moonlight')
        add_status_row(card, 'DOCKER', 'docker_dot', 'lbl_docker_status', 'docker')
        add_status_row(card, 'TAILSCALE', 'tailscale_dot', 'lbl_tailscale_status', 'tailscale')
        
        footer.append(card)
        return footer
        
    def update_server_status(self, has_sun, has_moon, has_docker, has_tailscale):
        for dot, has in [
            (self.sunshine_dot, has_sun), 
            (self.moonlight_dot, has_moon),
            (self.docker_dot, has_docker),
            (self.tailscale_dot, has_tailscale)
        ]:
            dot.remove_css_class('status-online')
            dot.remove_css_class('status-offline')
            dot.add_css_class('status-online' if has else 'status-offline')

    def update_dependency_ui(self, has_sun, has_moon, has_docker, has_tailscale):
        status_items = [
            (self.lbl_sunshine_status, self.host_card, has_sun, 'Sunshine'),
            (self.lbl_moonlight_status, self.guest_card, has_moon, 'Moonlight'),
            (self.lbl_docker_status, None, has_docker, 'Docker'),
            (self.lbl_tailscale_status, None, has_tailscale, 'Tailscale')
        ]
        
        for lbl, card, has, name in status_items:
            status_text = _("Installed") if has else _("Missing")
            lbl.set_markup(f'<span color="{"#2ec27e" if has else "#e01b24"}">{status_text}</span>')
            
            if card:
                tooltip = ""
                if not has:
                    action = _("host") if name == 'Sunshine' else _("connect")
                    tooltip = _("Need to install {} to {}").format(name, action)
                card.set_sensitive(has)
                card.set_tooltip_text(tooltip)

        
    def setup_content(self):
        ct = Adw.ToolbarView(); hb = Adw.HeaderBar(); m = Gio.Menu()
        m.append(_('Preferences'), 'app.preferences'); m.append(_('About'), 'app.about')
        hb.pack_end(Gtk.MenuButton(icon_name='open-menu-symbolic', menu_model=m))
        hb.set_title_widget(Adw.WindowTitle.new('', ''))
        ct.add_top_bar(hb); self.content_stack = Gtk.Stack()
        self.content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE); self.content_stack.set_transition_duration(200)
        self.content_stack.add_named(self.create_welcome_page(), 'welcome')
        self.host_view = HostView(); self.content_stack.add_named(self.host_view, 'host')
        self.guest_view = GuestView(); self.content_stack.add_named(self.guest_view, 'guest')
        
        # Private Network Views
        from .private_network_view import PrivateNetworkView
        self.create_private_view = PrivateNetworkView(self, mode='create')
        self.connect_private_view = PrivateNetworkView(self, mode='connect')
        self.content_stack.add_named(self.create_private_view, 'create_private')
        self.content_stack.add_named(self.connect_private_view, 'connect_private')
        
        ct.set_content(self.content_stack); self.split_view.set_content(Adw.NavigationPage.new(ct, 'Big Remote Play'))
        
    def create_welcome_page(self):
        scroll = Gtk.ScrolledWindow(); scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC); scroll.set_vexpand(True)
        
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main_box.set_valign(Gtk.Align.CENTER)
        main_box.set_halign(Gtk.Align.CENTER)
        main_box.set_margin_top(40)
        main_box.set_margin_bottom(40)
        
        # Simple Logo
        logo_img = create_icon_widget('big-remote-play', size=128)
        logo_img.set_halign(Gtk.Align.CENTER)
        logo_img.set_valign(Gtk.Align.CENTER)
        logo_img.set_margin_bottom(20)
        main_box.append(logo_img)
        
        # Titles
        title = Gtk.Label(label='Big Remote Play')
        title.add_css_class('hero-title')
        title.add_css_class('animate-fade')
        main_box.append(title)
        
        subtitle = Gtk.Label(label=_('Play cooperatively over the local network'))
        subtitle.add_css_class('hero-subtitle')
        subtitle.add_css_class('animate-fade')
        subtitle.add_css_class('delay-1')
        main_box.append(subtitle)
        
        # Spacer
        spacer = Gtk.Box()
        spacer.set_size_request(-1, 32)
        main_box.append(spacer)
        
        # Cards
        cards_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        cards_box.set_halign(Gtk.Align.CENTER)
        cards_box.add_css_class('animate-fade')
        cards_box.add_css_class('delay-2')
        
        self.host_card = self.create_action_card(
            _('Host Server'), 
            _('Share your games with other players on the network'), 
            'network-server-symbolic', 
            'suggested-action', 
            lambda: self.navigate_to('host')
        )
        
        self.guest_card = self.create_action_card(
            _('Connect to Server'), 
            _('Connect to a game server on the network'), 
            'network-workgroup-symbolic', 
            'suggested-action', 
            lambda: self.navigate_to('guest')
        )
        
        cards_box.append(self.host_card)
        cards_box.append(self.guest_card)
        main_box.append(cards_box)
        
        # Footer info
        il = Gtk.Label()
        il.set_markup(_('<span size="small">Based on <b>Sunshine</b> and <b>Moonlight</b></span>'))
        il.add_css_class('dim-label')
        il.add_css_class('animate-fade')
        il.add_css_class('delay-3')
        il.set_margin_top(32)
        main_box.append(il)
        
        scroll.set_child(main_box)
        return scroll
        
    def create_action_card(self, title, desc, icon, cls, cb):
        btn = Gtk.Button()
        btn.add_css_class('action-card')
        if cls: btn.add_css_class(cls)
        btn.set_size_request(280, 200)
        btn.connect('clicked', lambda b: cb())
        
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        for m in ['top', 'bottom', 'start', 'end']: getattr(box, f'set_margin_{m}')(20)
        
        img = create_icon_widget(icon, size=52)
        if 'suggested-action' in cls:
            img.add_css_class('accent')
        box.append(img)
        
        tl = Gtk.Label(label=title)
        tl.add_css_class('title-3')
        tl.set_wrap(True)
        tl.set_justify(Gtk.Justification.CENTER)
        box.append(tl)
        
        dl = Gtk.Label(label=desc)
        dl.add_css_class('caption')
        dl.add_css_class('dim-label')
        dl.set_wrap(True)
        dl.set_max_width_chars(30)
        dl.set_justify(Gtk.Justification.CENTER)
        box.append(dl)
        
        btn.set_child(box)
        return btn
        
    def on_nav_selected(self, lb, row):
        if not row: return
        pid = getattr(row, 'page_id', None)
        if not pid: return
        
        # Update visual style active state
        c = self.nav_list.get_first_child()
        while c:
            c.remove_css_class('active-category')
            c = c.get_next_sibling()
        row.add_css_class('active-category')
        
        if not hasattr(self, 'content_stack'):
            print(f"DEBUG: stack not ready for {pid}")
            return
        
        # Switch content
        print(f"DEBUG: Switching to {pid}")
        if self.content_stack.get_visible_child_name() != pid:
            self.content_stack.set_visible_child_name(pid)
            self.current_page = pid
        else:
            print(f"DEBUG: Already on {pid}")

    def navigate_to(self, pid):
        # Programmatic navigation: find row and select it
        # This will trigger on_nav_selected via the signal
        r = self.nav_list.get_first_child()
        while r:
            if getattr(r, 'page_id', None) == pid:
                self.nav_list.select_row(r)
                break
            r = r.get_next_sibling()
        
    def check_system(self):
        def check():
            h_sun = self.system_check.has_sunshine()
            h_moon = self.system_check.has_moonlight()
            h_docker = self.system_check.has_docker()
            h_tail = self.system_check.has_tailscale()
            
            r_sun = self.system_check.is_sunshine_running()
            r_moon = self.system_check.is_moonlight_running()
            r_docker = self.system_check.is_docker_running()
            r_tail = self.system_check.is_tailscale_running()
            
            GLib.idle_add(lambda: (
                self.update_status(h_sun, h_moon),
                self.update_server_status(r_sun, r_moon, r_docker, r_tail),
                self.update_dependency_ui(h_sun, h_moon, h_docker, h_tail)
            ))
        threading.Thread(target=check, daemon=True).start()
        GLib.timeout_add_seconds(3, self.p_check)

    def p_check(self):
        def check():
            r_sun = self.system_check.is_sunshine_running()
            r_moon = self.system_check.is_moonlight_running()
            r_docker = self.system_check.is_docker_running()
            r_tail = self.system_check.is_tailscale_running()
            GLib.idle_add(self.update_server_status, r_sun, r_moon, r_docker, r_tail)
            
        threading.Thread(target=check, daemon=True).start()
        return True

        
    def update_status(self, h_sun, h_moon): (self.show_missing_dialog() if not h_sun and not h_moon else None)
    def show_missing_dialog(self):
        d = Adw.MessageDialog.new(self); d.set_heading(_('Missing Components')); d.set_body(_('Sunshine and Moonlight are required. Install now?'))
        d.add_response('cancel', _('Cancel')); d.add_response('install', _('Install')); d.set_response_appearance('install', Adw.ResponseAppearance.SUGGESTED)
        d.connect('response', lambda dlg, r: (InstallerWindow(parent=self, on_success=self.check_system).present() if r == 'install' else None)); d.present()
    def show_toast(self, m): (self.toast_overlay.add_toast(Adw.Toast.new(m)) if hasattr(self, 'toast_overlay') else print(m))

    def on_service_clicked(self, service_id):
        """Open service control dialog"""
        meta = SERVICE_METADATA.get(service_id)
        if not meta: return

        # Check current states
        is_running = False
        is_enabled = False
        
        if service_id == 'sunshine': is_running = self.system_check.is_sunshine_running()
        elif service_id == 'moonlight': is_running = self.system_check.is_moonlight_running()
        elif service_id == 'docker': is_running = self.system_check.is_docker_running()
        elif service_id == 'tailscale': is_running = self.system_check.is_tailscale_running()
        
        if meta['type'] == 'service':
            cmd = ['systemctl']
            if meta.get('user'): cmd.append('--user')
            cmd.extend(['is-enabled', '--quiet', meta['unit']])
            is_enabled = subprocess.run(cmd).returncode == 0

        # Create Dialog
        dialog = Adw.Window(transient_for=self)
        dialog.set_modal(True)
        dialog.set_title(meta['full_name'])
        dialog.set_default_size(400, -1)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        content.set_margin_top(24); content.set_margin_bottom(24); content.set_margin_start(24); content.set_margin_end(24)

        icon = create_icon_widget('preferences-system-symbolic', size=48)
        icon.set_halign(Gtk.Align.CENTER)
        content.append(icon)

        title = Gtk.Label(label=meta['full_name'])
        title.add_css_class('title-2')
        content.append(title)

        desc = Gtk.Label(label=meta['description'])
        desc.set_wrap(True); desc.set_justify(Gtk.Justification.CENTER)
        desc.add_css_class('dim-label')
        content.append(desc)

        status_box = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
        dot = create_icon_widget('media-record-symbolic', size=12, css_class=['status-dot', 'status-online' if is_running else 'status-offline'])
        status_box.append(dot)
        status_lbl = Gtk.Label(label=_("Running") if is_running else _("Stopped"))
        status_box.append(status_lbl)
        content.append(status_box)

        # Buttons
        actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        
        def run_cmd(action, sid=service_id):
            m = SERVICE_METADATA[sid]
            cmd = []
            
            # Helper to find moonlight binary
            def find_moonlight():
                for b in ['moonlight-qt', 'moonlight']:
                    if shutil.which(b): return b
                return None

            if m['type'] == 'service':
                cmd = ["bigsudo", "systemctl"]
                if m.get('user'): 
                    cmd = ["systemctl", "--user"]
                cmd.extend([action, m['unit']])
            else:
                # App type
                bin_name = m['bin']
                if sid == 'moonlight':
                    found = find_moonlight()
                    if not found and action in ['start', 'restart']:
                        self.show_toast(_("Moonlight not found"))
                        return
                    bin_name = found or bin_name
                
                if action == 'start': 
                    cmd = [bin_name]
                elif action == 'stop': 
                    cmd = ["pkill", "-x", bin_name]
                elif action == 'restart':
                    try: subprocess.run(["pkill", "-x", bin_name], check=False)
                    except: pass
                    cmd = [bin_name]
            
            if cmd:
                try:
                    subprocess.Popen(cmd)
                    self.show_toast(_("Action {} sent to {}").format(action, m['name']))
                    dialog.destroy()
                    GLib.timeout_add(1000, self.check_system)
                except Exception as e:
                    self.show_toast(_("Error executing command: {}").format(e))

        # Start / Stop
        btn_main = Gtk.Button(label=_("Stop") if is_running else _("Start"))
        btn_main.add_css_class("suggested-action" if not is_running else "destructive-action")
        btn_main.connect("clicked", lambda b: run_cmd("stop" if is_running else "start"))
        actions.append(btn_main)

        # Restart
        btn_restart = Gtk.Button(label=_("Restart"))
        btn_restart.connect("clicked", lambda b: run_cmd("restart"))
        actions.append(btn_restart)

        # Enable / Disable (only for services)
        if meta['type'] == 'service':
            btn_enable = Gtk.Button(label=_("Disable") if is_enabled else _("Enable"))
            btn_enable.connect("clicked", lambda b: run_cmd("disable" if is_enabled else "enable"))
            actions.append(btn_enable)

        content.append(actions)
        
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        tv.add_top_bar(hb)
        tv.set_content(content)
        dialog.set_content(tv)
        dialog.present()
