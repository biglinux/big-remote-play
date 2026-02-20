import gi
gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gio
import threading
import json
import os
from .host_view import HostView
from .guest_view import GuestView
from .installer_window import InstallerWindow
from utils.network import NetworkDiscovery
from utils.system_check import SystemCheck
from utils.icons import create_icon_widget, set_icon
from utils.i18n import _
import subprocess
import shutil

# ─── VPN Provider Config ───────────────────────────────────────────────────
VPN_CONFIG_FILE = os.path.expanduser("~/.config/big-remoteplay/vpn_choice.json")

VPN_PROVIDERS = {
    'headscale': {
        'name': 'Headscale',
        'icon': 'headscale-symbolic',
        'description': _('Self-hosted VPN server with Cloudflare DNS. Full control.'),
        'color': '#3584e4',
        'script_create': 'create-network_headscale.sh',
        'script_connect': 'create-network_headscale.sh',
    },
    'tailscale': {
        'name': 'Tailscale',
        'icon': 'tailscale-symbolic',
        'description': _('Easy mesh VPN. No server required. Free tier available.'),
        'color': '#26a269',
        'script_create': 'create-network_tailscale.sh',
        'script_connect': 'create-network_tailscale.sh',
    },
    'zerotier': {
        'name': 'ZeroTier',
        'icon': 'zerotier-symbolic',
        'description': _('Flexible virtual network. Works through NAT and firewalls.'),
        'color': '#e5a50a',
        'script_create': 'create-network_zerotier.sh',
        'script_connect': 'create-network_zerotier.sh',
    },
}

# Service Definitions
SERVICE_METADATA = {
    'sunshine': {
        'name': 'SUNSHINE',
        'full_name': _('Sunshine Game Stream Host'),
        'description': _('High-performance game stream host. Required to share your games.'),
        'type': 'service',
        'unit': 'sunshine.service',
        'user': True
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
        'full_name': _('Tailscale'),
        'description': _('Mesh VPN service. Required for Tailscale connectivity.'),
        'type': 'service',
        'unit': 'tailscaled.service',
        'user': False
    },
    'zerotier': {
        'name': 'ZEROTIER',
        'full_name': _('ZeroTier'),
        'description': _('Virtual network service. Required for ZeroTier connectivity.'),
        'type': 'service',
        'unit': 'zerotier-one.service',
        'user': False
    }
}

# Navigation Categories – built dynamically based on VPN choice
BASE_NAVIGATION_PAGES = {
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
}


def load_vpn_choice():
    """Load saved VPN provider choice. Returns None if not set."""
    try:
        if os.path.exists(VPN_CONFIG_FILE):
            with open(VPN_CONFIG_FILE, 'r') as f:
                data = json.load(f)
                choice = data.get('vpn_provider')
                if choice in VPN_PROVIDERS:
                    return choice
    except Exception:
        pass
    return None


def save_vpn_choice(provider_id):
    """Persist VPN provider choice."""
    os.makedirs(os.path.dirname(VPN_CONFIG_FILE), exist_ok=True)
    with open(VPN_CONFIG_FILE, 'w') as f:
        json.dump({'vpn_provider': provider_id}, f)


class MainWindow(Adw.ApplicationWindow):
    """Main window with modern side navigation"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.set_title('Big Remote Play')
        self.set_default_size(950, 720)

        self.system_check = SystemCheck()
        self.network = NetworkDiscovery()

        # Current State
        self.current_page = 'welcome'
        self._vpn_choice = load_vpn_choice()  # None if not yet chosen

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

    def _build_navigation_pages(self):
        """Build the navigation page list based on current VPN choice."""
        pages = dict(BASE_NAVIGATION_PAGES)
        if self._vpn_choice:
            vpn_info = VPN_PROVIDERS[self._vpn_choice]
            pages['create_private'] = {
                'name': _('Create Private Network'),
                'icon': vpn_info['icon'],
                'description': _('{} - Setup server').format(vpn_info['name']),
                'badge': vpn_info['name'],
            }
            pages['connect_private'] = {
                'name': _('Connect to Private Network'),
                'icon': vpn_info['icon'],
                'description': _('{} - Join network').format(vpn_info['name']),
                'badge': vpn_info['name'],
            }
            pages['change_vpn'] = {
                'name': _('Change VPN'),
                'icon': 'network-private-symbolic',
                'description': _('Switch provider'),
            }
        else:
            # Single "connect" entry that leads to VPN selector
            pages['vpn_selector'] = {
                'name': _('Select VPN'),
                'icon': 'network-private-symbolic',
                'description': _('Choose your VPN provider'),
            }
        return pages

    def setup_sidebar(self):
        tb = Adw.ToolbarView(); hb = Adw.HeaderBar()
        btn = Gtk.Button(); btn.set_child(create_icon_widget('big-remote-play')); btn.add_css_class('flat')
        btn.connect('clicked', lambda b: self.get_application().activate_action('about', None))
        hb.pack_start(btn)

        title_lbl = Gtk.Label(label="Big Remote Play")
        title_lbl.add_css_class("heading")
        hb.pack_start(title_lbl)

        hb.set_title_widget(Adw.WindowTitle.new('', ''))
        tb.add_top_bar(hb); main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL); main.set_vexpand(True)
        scroll = Gtk.ScrolledWindow(); scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC); scroll.set_vexpand(True)
        self.nav_list = Gtk.ListBox(); self.nav_list.add_css_class('navigation-sidebar')
        self.nav_list.connect('row-selected', self.on_nav_selected)

        self._refresh_nav_list()

        scroll.set_child(self.nav_list); main.append(scroll); main.append(self.create_status_footer())
        tb.set_content(main); self.split_view.set_sidebar(Adw.NavigationPage.new(tb, 'Navigation'))

    def _refresh_nav_list(self):
        """Rebuild the navigation list based on VPN choice."""
        # Clear existing rows
        while child := self.nav_list.get_first_child():
            self.nav_list.remove(child)

        nav_pages = self._build_navigation_pages()
        for pid, info in nav_pages.items():
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

        if r := self.nav_list.get_row_at_index(0):
            self.nav_list.select_row(r)

    def create_nav_row(self, page_id: str, page_info: dict) -> Gtk.ListBoxRow:
        """Creates navigation row in sidebar"""
        row = Gtk.ListBoxRow()
        row.page_id = page_id
        row.add_css_class('category-row')

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(6)
        box.set_margin_bottom(6)

        icon = create_icon_widget(page_info['icon'], size=20, css_class='category-icon')
        box.append(icon)

        label = Gtk.Label(label=page_info['name'])
        label.set_halign(Gtk.Align.START)
        label.set_hexpand(True)
        label.add_css_class('category-label')
        box.append(label)

        # Badge showing selected VPN name
        if badge_text := page_info.get('badge'):
            badge = Gtk.Label(label=badge_text)
            badge.add_css_class('caption')
            badge.add_css_class('dim-label')
            badge.set_halign(Gtk.Align.END)
            box.append(badge)

        row.set_child(box)
        return row

    def create_status_footer(self):
        """Creates footer with server status"""
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        footer.set_margin_start(12)
        footer.set_margin_end(12)
        footer.set_margin_top(8)
        footer.set_margin_bottom(12)
        footer.set_spacing(8)

        separator = Gtk.Separator()
        separator.set_margin_bottom(8)
        footer.append(separator)

        status_title = Gtk.Label(label=_('Service Status'))
        status_title.add_css_class('caption')
        status_title.add_css_class('dim-label')
        status_title.set_halign(Gtk.Align.START)
        status_title.set_margin_bottom(4)
        footer.append(status_title)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        card.add_css_class("info-card")
        self.status_card = card

        def add_status_row(container, label_text, dot_attr, lbl_attr, service_id):
            row = Gtk.Box(spacing=10)
            row.add_css_class("info-row")
            row.service_id = service_id

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
        add_status_row(card, 'ZEROTIER', 'zerotier_dot', 'lbl_zerotier_status', 'zerotier')

        footer.append(card)
        self._filter_status_rows()
        return footer

    def _filter_status_rows(self):
        """Show only relevant services based on VPN choice."""
        if not hasattr(self, 'status_card'): return
        
        vpn = self._vpn_choice
        visible_services = ['sunshine', 'moonlight']
        
        if vpn == 'headscale':
            visible_services.extend(['docker', 'tailscale'])
        elif vpn == 'tailscale':
            visible_services.append('tailscale')
        elif vpn == 'zerotier':
            visible_services.append('zerotier')
            
        child = self.status_card.get_first_child()
        while child:
            sid = getattr(child, 'service_id', None)
            if sid:
                child.set_visible(sid in visible_services)
            child = child.get_next_sibling()

    def update_server_status(self, has_sun, has_moon, has_docker, has_tailscale, has_zt=False):
        for dot, has in [
            (self.sunshine_dot, has_sun),
            (self.moonlight_dot, has_moon),
            (self.docker_dot, has_docker),
            (self.tailscale_dot, has_tailscale),
            (getattr(self, 'zerotier_dot', None), has_zt)
        ]:
            if not dot: continue
            dot.remove_css_class('status-online')
            dot.remove_css_class('status-offline')
            dot.add_css_class('status-online' if has else 'status-offline')

    def update_dependency_ui(self, has_sun, has_moon, has_docker, has_tailscale, has_zt=False):
        status_items = [
            (self.lbl_sunshine_status, self.host_card, has_sun, 'Sunshine'),
            (self.lbl_moonlight_status, self.guest_card, has_moon, 'Moonlight'),
            (self.lbl_docker_status, None, has_docker, 'Docker'),
            (self.lbl_tailscale_status, None, has_tailscale, 'Tailscale'),
            (getattr(self, 'lbl_zerotier_status', None), None, has_zt, 'ZeroTier')
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
        self.content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.content_stack.set_transition_duration(200)
        self.content_stack.add_named(self.create_welcome_page(), 'welcome')
        self.host_view = HostView(); self.content_stack.add_named(self.host_view, 'host')
        self.guest_view = GuestView(); self.content_stack.add_named(self.guest_view, 'guest')

        # VPN Selector page (shown when no VPN is chosen yet)
        self.vpn_selector_page = self.create_vpn_selector_page()
        self.content_stack.add_named(self.vpn_selector_page, 'vpn_selector')

        # Private Network Views (Headscale/Tailscale/ZeroTier)
        from .private_network_view import PrivateNetworkView
        vpn = self._vpn_choice or 'headscale'
        self.create_private_view = PrivateNetworkView(self, mode='create', vpn_provider=vpn)
        self.connect_private_view = PrivateNetworkView(self, mode='connect', vpn_provider=vpn)
        self.content_stack.add_named(self.create_private_view, 'create_private')
        self.content_stack.add_named(self.connect_private_view, 'connect_private')

        ct.set_content(self.content_stack)
        self.split_view.set_content(Adw.NavigationPage.new(ct, 'Big Remote Play'))

    # ─────────────────────────────────────────────────────────────────────────
    #  VPN SELECTOR PAGE
    # ─────────────────────────────────────────────────────────────────────────

    def create_vpn_selector_page(self):
        """Full-page VPN provider selector shown when no VPN is chosen."""
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(900)
        for m in ['top', 'bottom', 'start', 'end']:
            getattr(clamp, f'set_margin_{m}')(32)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=32)

        # Header
        header_group = Adw.PreferencesGroup()
        header_group.set_title(_('Choose Your VPN Provider'))
        header_group.set_header_suffix(create_icon_widget('network-private-symbolic', size=18))
        header_group.set_description(
            _('Select a VPN solution to create or join a Private Network. '
              'Your choice will be saved and shown in the sidebar menu.')
        )
        box.append(header_group)

        # Cards row
        cards_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        cards_box.set_halign(Gtk.Align.CENTER)
        cards_box.set_homogeneous(True)

        for pid, info in VPN_PROVIDERS.items():
            card = self._create_vpn_card(pid, info)
            cards_box.append(card)

        box.append(cards_box)

        # Comparison table
        compare_group = Adw.PreferencesGroup()
        compare_group.set_title(_('Quick Comparison'))
        compare_group.set_header_suffix(create_icon_widget('preferences-other-symbolic', size=18))
        box.append(compare_group)

        comparisons = [
            ('Headscale', _('Self-hosted'), _('Full control, needs domain + Cloudflare'), _('Advanced'), '#3584e4'),
            ('Tailscale', _('Cloud (free)'), _('Easiest setup, works out of the box'), _('Beginner'), '#26a269'),
            ('ZeroTier', _('Cloud (free)'), _('Flexible, works through NAT'), _('Intermediate'), '#e5a50a'),
        ]
        for name, host_type, desc, level, color in comparisons:
            row = Adw.ActionRow()
            row.set_title(name)
            row.set_subtitle(f'{host_type} · {desc} · {_("Level")}: {level}')
            dot = create_icon_widget('media-record-symbolic', size=14)
            dot.add_css_class('status-dot')
            dot.add_css_class('status-online')
            row.add_prefix(dot)
            compare_group.add(row)

        clamp.set_child(box)
        scroll.set_child(clamp)
        return scroll

    def _create_vpn_card(self, provider_id: str, info: dict) -> Gtk.Button:
        """Create a VPN option card button."""
        btn = Gtk.Button()
        btn.add_css_class('action-card')
        btn.add_css_class('suggested-action')
        btn.set_size_request(240, 220)
        btn.connect('clicked', lambda b, pid=provider_id: self._on_vpn_selected(pid))

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        for m in ['top', 'bottom', 'start', 'end']:
            getattr(box, f'set_margin_{m}')(20)

        # Icon
        icon = create_icon_widget(info['icon'], size=52)
        icon.add_css_class('accent')
        box.append(icon)

        # Name
        name_lbl = Gtk.Label(label=info['name'])
        name_lbl.add_css_class('title-2')
        box.append(name_lbl)

        # Description
        desc_lbl = Gtk.Label(label=info['description'])
        desc_lbl.add_css_class('caption')
        desc_lbl.add_css_class('dim-label')
        desc_lbl.set_wrap(True)
        desc_lbl.set_max_width_chars(28)
        desc_lbl.set_justify(Gtk.Justification.CENTER)
        box.append(desc_lbl)

        # "Choose" label
        choose_lbl = Gtk.Label(label=_('Choose →'))
        choose_lbl.add_css_class('caption-heading')
        box.append(choose_lbl)

        btn.set_child(box)
        return btn

    def _on_vpn_selected(self, provider_id: str):
        """Handle VPN provider selection."""
        old_vpn = self._vpn_choice
        
        if old_vpn and old_vpn != provider_id:
            dialog = Adw.MessageDialog(
                transient_for=self,
                heading=_("Switch VPN Provider?"),
                body=_("You are switching from {} to {}. Do you want to disconnect from {}?").format(
                    VPN_PROVIDERS[old_vpn]['name'], 
                    VPN_PROVIDERS[provider_id]['name'],
                    VPN_PROVIDERS[old_vpn]['name']
                )
            )
            dialog.add_response("keep", _("Keep Connected"))
            dialog.add_response("disconnect", _("Disconnect previous"))
            dialog.set_response_appearance("disconnect", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("keep")
            
            def on_resp(dlg, resp):
                if resp == "disconnect":
                    self._disconnect_vpn(old_vpn)
                self._apply_vpn_selection(provider_id)
                
            dialog.connect("response", on_resp)
            dialog.present()
        else:
            self._apply_vpn_selection(provider_id)

    def _disconnect_vpn(self, vpn_id):
        """Disconnect from a specific VPN provider."""
        self.show_toast(_("Disconnecting from {}...").format(VPN_PROVIDERS[vpn_id]['name']))
        def run():
            if vpn_id in ('headscale', 'tailscale'):
                subprocess.run(["bigsudo", "tailscale", "logout"])
            elif vpn_id == 'zerotier':
                # Local ZT disconnection is a bit trickier, 
                # usually means leaving all networks or stopping the service
                # For simplicity, we can try to leave networks found in history or just stop service
                subprocess.run(["bigsudo", "systemctl", "stop", "zerotier-one"])
            GLib.idle_add(lambda: self.show_toast(_("{} disconnected").format(VPN_PROVIDERS[vpn_id]['name'])))
        threading.Thread(target=run, daemon=True).start()

    def _apply_vpn_selection(self, provider_id):
        self._vpn_choice = provider_id
        save_vpn_choice(provider_id)

        # Rebuild private network views with the chosen provider
        from .private_network_view import PrivateNetworkView

        # Remove old views if they exist
        for page_name in ['create_private', 'connect_private']:
            old = self.content_stack.get_child_by_name(page_name)
            if old:
                self.content_stack.remove(old)

        self.create_private_view = PrivateNetworkView(self, mode='create', vpn_provider=provider_id)
        self.connect_private_view = PrivateNetworkView(self, mode='connect', vpn_provider=provider_id)
        self.content_stack.add_named(self.create_private_view, 'create_private')
        self.content_stack.add_named(self.connect_private_view, 'connect_private')

        # Refresh sidebar
        self._refresh_nav_list()
        self._filter_status_rows()

        # Navigate to the create page
        vpn_name = VPN_PROVIDERS[provider_id]['name']
        self.show_toast(_('{} selected! Setting up private network...').format(vpn_name))
        GLib.idle_add(lambda: self.navigate_to('create_private'))

    def reset_vpn_choice(self):
        """Clear VPN choice and show selector again."""
        self._vpn_choice = None
        try:
            if os.path.exists(VPN_CONFIG_FILE):
                os.remove(VPN_CONFIG_FILE)
        except Exception:
            pass
        self._refresh_nav_list()
        self.navigate_to('vpn_selector')

    # ─────────────────────────────────────────────────────────────────────────
    #  WELCOME PAGE
    # ─────────────────────────────────────────────────────────────────────────

    def create_welcome_page(self):
        scroll = Gtk.ScrolledWindow(); scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC); scroll.set_vexpand(True)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main_box.set_valign(Gtk.Align.CENTER)
        main_box.set_halign(Gtk.Align.CENTER)
        main_box.set_margin_top(40)
        main_box.set_margin_bottom(40)

        logo_img = create_icon_widget('big-remote-play', size=128)
        logo_img.set_halign(Gtk.Align.CENTER)
        logo_img.set_valign(Gtk.Align.CENTER)
        logo_img.set_margin_bottom(20)
        main_box.append(logo_img)

        title = Gtk.Label(label='Big Remote Play')
        title.add_css_class('hero-title')
        title.add_css_class('animate-fade')
        main_box.append(title)

        subtitle = Gtk.Label(label=_('Play cooperatively over the local network'))
        subtitle.add_css_class('hero-subtitle')
        subtitle.add_css_class('animate-fade')
        subtitle.add_css_class('delay-1')
        main_box.append(subtitle)

        spacer = Gtk.Box()
        spacer.set_size_request(-1, 32)
        main_box.append(spacer)

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
        btn.set_size_request(280, 220)
        btn.set_hexpand(False)
        btn.set_vexpand(False)
        btn.connect('clicked', lambda b: cb())

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_hexpand(False)
        box.set_vexpand(False)
        for m in ['top', 'bottom', 'start', 'end']: getattr(box, f'set_margin_{m}')(24)

        img = create_icon_widget(icon, size=64)
        img.set_size_request(64, 64)
        img.set_hexpand(False)
        img.set_vexpand(False)
        img.set_valign(Gtk.Align.CENTER)
        img.set_halign(Gtk.Align.CENTER)
        
        if 'suggested-action' in cls:
            img.add_css_class('accent')
        box.append(img)

        tl = Gtk.Label(label=title)
        tl.add_css_class('title-3')
        tl.set_wrap(True)
        tl.set_justify(Gtk.Justification.CENTER)
        tl.set_hexpand(False)
        box.append(tl)

        dl = Gtk.Label(label=desc)
        dl.add_css_class('caption')
        dl.add_css_class('dim-label')
        dl.set_wrap(True)
        dl.set_max_width_chars(25)
        dl.set_justify(Gtk.Justification.CENTER)
        dl.set_hexpand(False)
        box.append(dl)

        btn.set_child(box)
        return btn

    # ─────────────────────────────────────────────────────────────────────────
    #  NAVIGATION
    # ─────────────────────────────────────────────────────────────────────────

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

        # If no VPN is set yet and user clicks vpn_selector, show the selector
        actual_pid = pid
        
        if pid == 'change_vpn':
            self.reset_vpn_choice()
            return

        if pid == 'vpn_selector' or (pid in ('create_private', 'connect_private') and not self._vpn_choice):
            actual_pid = 'vpn_selector'

        print(f"DEBUG: Switching to {actual_pid}")
        if self.content_stack.get_visible_child_name() != actual_pid:
            self.content_stack.set_visible_child_name(actual_pid)
            self.current_page = actual_pid
        else:
            print(f"DEBUG: Already on {actual_pid}")

    def navigate_to(self, pid):
        """Programmatic navigation: find row and select it"""
        r = self.nav_list.get_first_child()
        while r:
            if getattr(r, 'page_id', None) == pid:
                self.nav_list.select_row(r)
                break
            r = r.get_next_sibling()

    # ─────────────────────────────────────────────────────────────────────────
    #  SYSTEM CHECK
    # ─────────────────────────────────────────────────────────────────────────

    def check_system(self):
        def check():
            h_sun = self.system_check.has_sunshine()
            h_moon = self.system_check.has_moonlight()
            h_docker = self.system_check.has_docker()
            h_tail = self.system_check.has_tailscale()
            h_zt = self.system_check.has_zerotier()

            r_sun = self.system_check.is_sunshine_running()
            r_moon = self.system_check.is_moonlight_running()
            r_docker = self.system_check.is_docker_running()
            r_tail = self.system_check.is_tailscale_running()
            r_zt = self.system_check.is_zerotier_running()

            GLib.idle_add(lambda: (
                self.update_status(h_sun, h_moon),
                self.update_server_status(r_sun, r_moon, r_docker, r_tail, r_zt),
                self.update_dependency_ui(h_sun, h_moon, h_docker, h_tail, h_zt)
            ))
        threading.Thread(target=check, daemon=True).start()
        GLib.timeout_add_seconds(3, self.p_check)

    def p_check(self):
        def check():
            r_sun = self.system_check.is_sunshine_running()
            r_moon = self.system_check.is_moonlight_running()
            r_docker = self.system_check.is_docker_running()
            r_tail = self.system_check.is_tailscale_running()
            r_zt = self.system_check.is_zerotier_running()
            GLib.idle_add(self.update_server_status, r_sun, r_moon, r_docker, r_tail, r_zt)

        threading.Thread(target=check, daemon=True).start()
        return True

    def update_status(self, h_sun, h_moon): (self.show_missing_dialog() if not h_sun and not h_moon else None)

    def show_missing_dialog(self):
        d = Adw.MessageDialog.new(self); d.set_heading(_('Missing Components')); d.set_body(_('Sunshine and Moonlight are required. Install now?'))
        d.add_response('cancel', _('Cancel')); d.add_response('install', _('Install')); d.set_response_appearance('install', Adw.ResponseAppearance.SUGGESTED)
        d.connect('response', lambda dlg, r: (InstallerWindow(parent=self, on_success=self.check_system).present() if r == 'install' else None)); d.present()

    def show_toast(self, m): (self.toast_overlay.add_toast(Adw.Toast.new(m)) if hasattr(self, 'toast_overlay') else print(m))

    # ─────────────────────────────────────────────────────────────────────────
    #  SERVICE CONTROL DIALOG
    # ─────────────────────────────────────────────────────────────────────────

    def on_service_clicked(self, service_id):
        """Open service control dialog"""
        meta = SERVICE_METADATA.get(service_id)
        if not meta: return

        is_running = False
        is_enabled = False

        if service_id == 'sunshine': is_running = self.system_check.is_sunshine_running()
        elif service_id == 'moonlight': is_running = self.system_check.is_moonlight_running()
        elif service_id == 'docker': is_running = self.system_check.is_docker_running()
        elif service_id == 'tailscale': is_running = self.system_check.is_tailscale_running()
        elif service_id == 'zerotier': is_running = self.system_check.is_zerotier_running()

        if meta['type'] == 'service':
            cmd = ['systemctl']
            if meta.get('user'): cmd.append('--user')
            cmd.extend(['is-enabled', '--quiet', meta['unit']])
            is_enabled = subprocess.run(cmd).returncode == 0

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

        actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        def run_cmd(action, sid=service_id, force_type=None):
            m = SERVICE_METADATA[sid]
            cmd = []

            def find_moonlight():
                for b in ['moonlight-qt', 'moonlight']:
                    if shutil.which(b): return b
                return None

            current_type = force_type or m['type']

            if current_type == 'service':
                cmd = ["bigsudo", "systemctl"]
                if m.get('user'):
                    cmd = ["systemctl", "--user"]
                cmd.append(action)
                cmd.append(m['unit'])
                if sid == 'docker' and action == 'stop':
                    cmd.append('docker.socket')
            elif current_type == 'containers':
                if action == 'start':
                    cmd = ['docker', 'start', 'caddy', 'headscale']
                elif action == 'stop':
                    cmd = ['docker', 'stop', 'caddy', 'headscale']
                elif action == 'restart':
                    cmd = ['docker', 'restart', 'caddy', 'headscale']
            else:
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
                    name = _("Containers") if current_type == 'containers' else m['name']
                    self.show_toast(_("Action {} sent to {}").format(action, name))
                    dialog.destroy()
                    GLib.timeout_add(1000, self.check_system)
                except Exception as e:
                    self.show_toast(_("Error executing command: {}").format(e))

        btn_main = Gtk.Button(label=_("Stop") if is_running else _("Start"))
        btn_main.add_css_class("suggested-action" if not is_running else "destructive-action")
        btn_main.connect("clicked", lambda b: run_cmd("stop" if is_running else "start"))
        actions.append(btn_main)

        btn_restart = Gtk.Button(label=_("Restart"))
        btn_restart.connect("clicked", lambda b: run_cmd("restart"))
        actions.append(btn_restart)

        if meta['type'] == 'service':
            btn_enable = Gtk.Button(label=_("Disable") if is_enabled else _("Enable"))
            btn_enable.connect("clicked", lambda b: run_cmd("disable" if is_enabled else "enable"))
            actions.append(btn_enable)

        if service_id == 'docker':
            actions.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

            cont_running = self.system_check.are_containers_running()
            cont_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

            cont_header = Gtk.Box(spacing=8)
            cont_header.set_halign(Gtk.Align.CENTER)
            cont_header.append(create_icon_widget('network-wired-symbolic', size=16))
            cont_header.append(Gtk.Label(label=_("Private Network Containers")))
            cont_box.append(cont_header)

            c_status_box = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
            c_dot = create_icon_widget('media-record-symbolic', size=12,
                css_class=['status-dot', 'status-online' if cont_running else 'status-offline'])
            c_status_box.append(c_dot)
            c_status_lbl = Gtk.Label(label=_("Running (Caddy + Headscale)") if cont_running else _("Stopped"))
            c_status_box.append(c_status_lbl)
            cont_box.append(c_status_box)

            c_btn_main = Gtk.Button(label=_("Stop Containers") if cont_running else _("Start Containers"))
            c_btn_main.add_css_class("suggested-action" if not cont_running else "destructive-action")
            c_btn_main.connect("clicked", lambda b: run_cmd("stop" if cont_running else "start", force_type='containers'))
            cont_box.append(c_btn_main)

            c_btn_restart = Gtk.Button(label=_("Restart Containers"))
            c_btn_restart.connect("clicked", lambda b: run_cmd("restart", force_type='containers'))
            cont_box.append(c_btn_restart)

            cont_box.set_sensitive(is_running)
            actions.append(cont_box)

        content.append(actions)

        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        tv.add_top_bar(hb)
        tv.set_content(content)
        dialog.set_content(tv)
        dialog.present()
