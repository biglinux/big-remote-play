from __future__ import annotations

import gi
gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gio  # type: ignore
import threading
import json
import os
from .host_view import HostView
from .guest_view import GuestView
from .installer_window import InstallerWindow
from big_remote_play.utils.network import NetworkDiscovery
from big_remote_play.utils.system_check import SystemCheck
from big_remote_play.utils.icons import create_icon_widget, create_logo_widget
from big_remote_play.utils.widgets import (
    create_steps_strip,
    create_difficulty_pill,
    create_comparison_table,
)
from big_remote_play.utils.i18n import _
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
        'name': _('Server'),
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
        self._nav_page_by_row = {}
        self._service_by_row = {}
        self._status_dots = {}
        self._status_labels = {}

        self._install_window_actions()
        self.setup_ui()
        self.check_system()

        # Connect close signal
        self.connect('close-request', self.on_close_request)

    def on_close_request(self, window):
        try:
            if hasattr(self, 'host_view'): self.host_view.cleanup()
            if hasattr(self, 'guest_view'): self.guest_view.cleanup()
        except Exception: pass
        return False

    def setup_ui(self):
        self.toast_overlay = Adw.ToastOverlay(); self.set_content(self.toast_overlay)
        self.split_view = Adw.NavigationSplitView(); self.toast_overlay.set_child(self.split_view)
        self.setup_sidebar(); self.setup_content()
        self.split_view.set_min_sidebar_width(220); self.split_view.set_max_sidebar_width(280)

    def _install_window_actions(self):
        nav_action = Gio.SimpleAction.new("navigate", GLib.VariantType.new("s"))
        nav_action.connect("activate", self._on_navigate_action)
        self.add_action(nav_action)

    def _on_navigate_action(self, _action, parameter):
        if parameter is None:
            return
        self.navigate_to(parameter.get_string())

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
        # No sidebar header bar — the navigation list starts at the very top.
        # (About stays reachable from the content header menu.)
        tb = Adw.ToolbarView()
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL); main.set_vexpand(True)
        main.set_margin_top(8)
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
        self._nav_page_by_row.clear()

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
        self._nav_page_by_row[row] = page_id
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

        button = Gtk.Button()
        button.add_css_class('flat')
        button.set_hexpand(True)
        button.set_halign(Gtk.Align.FILL)
        button.set_action_name("win.navigate")
        button.set_action_target_value(GLib.Variant("s", page_id))
        button.set_child(box)
        button.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            [page_info['name'], page_info.get('description', '')],
        )

        row.set_child(button)
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
            # Real button: exposes button role, keyboard activation and an action
            # to AT-SPI (the old Gtk.Box + GestureClick exposed none).
            row = Gtk.Button()
            row.add_css_class("info-row")
            row.add_css_class("flat")
            self._service_by_row[row] = service_id
            row.connect("clicked", lambda b, sid=service_id: self.on_service_clicked(sid))

            content = Gtk.Box(spacing=10)
            box_key = Gtk.Box(spacing=8)
            box_key.set_hexpand(True)
            dot = create_icon_widget('media-record-symbolic', size=10, css_class=['status-dot', 'status-offline'])
            self._status_dots[service_id] = dot
            setattr(self, dot_attr, dot)
            box_key.append(dot)
            lbl_key = Gtk.Label(label=label_text)
            lbl_key.add_css_class('info-key')
            box_key.append(lbl_key)
            content.append(box_key)
            lbl_status = Gtk.Label(label=_('Checking...'))
            lbl_status.add_css_class('info-value')
            lbl_status.set_halign(Gtk.Align.END)
            self._status_labels[service_id] = lbl_status
            setattr(self, lbl_attr, lbl_status)
            content.append(lbl_status)
            row.set_child(content)
            # Plain-language explanation of each engine for new users.
            meta = SERVICE_METADATA.get(service_id, {})
            desc = meta.get('description', '')
            if desc:
                row.set_tooltip_text(desc)
            row.update_property(
                [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
                [label_text, desc])
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
            sid = self._service_by_row.get(child)
            if sid:
                child.set_visible(sid in visible_services)
            child = child.get_next_sibling()

    def update_server_status(self, has_sun, has_moon, has_docker, has_tailscale, has_zt=False):
        for service_id, has in [
            ('sunshine', has_sun),
            ('moonlight', has_moon),
            ('docker', has_docker),
            ('tailscale', has_tailscale),
            ('zerotier', has_zt)
        ]:
            dot = self._status_dots.get(service_id)
            if not dot: continue
            dot.remove_css_class('status-online')
            dot.remove_css_class('status-offline')
            dot.add_css_class('status-online' if has else 'status-offline')

    def update_dependency_ui(self, has_sun, has_moon, has_docker, has_tailscale, has_zt=False):
        status_items = [
            ('sunshine', self.host_card, has_sun, 'Sunshine'),
            ('moonlight', self.guest_card, has_moon, 'Moonlight'),
            ('docker', None, has_docker, 'Docker'),
            ('tailscale', None, has_tailscale, 'Tailscale'),
            ('zerotier', None, has_zt, 'ZeroTier')
        ]

        for service_id, card, has, name in status_items:
            lbl = self._status_labels.get(service_id)
            if not lbl:
                continue
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
        ct = Adw.ToolbarView(); hb = Adw.HeaderBar()
        hb.pack_end(self._create_header_menu_button())

        # Dynamic title reflecting the current section (filled in on_nav_selected).
        self.content_headerbar = hb
        self.content_title = Adw.WindowTitle.new(_('Home'), _('Play together, from anywhere'))
        hb.set_title_widget(self.content_title)

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

    def _create_header_menu_button(self):
        menu_button = Gtk.MenuButton(icon_name='open-menu-symbolic')
        menu_button.update_property([Gtk.AccessibleProperty.LABEL], [_('Application menu')])

        popover = Gtk.Popover()
        menu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        menu_box.set_margin_top(6)
        menu_box.set_margin_bottom(6)
        menu_box.set_margin_start(6)
        menu_box.set_margin_end(6)

        for label, action_name in (
            (_('Preferences'), 'preferences'),
            (_('About'), 'about'),
        ):
            button = Gtk.Button(label=label)
            button.add_css_class('flat')
            button.set_hexpand(True)
            button.set_halign(Gtk.Align.FILL)
            button.connect(
                'clicked',
                lambda _button, menu_popover=popover, name=action_name: self._activate_app_menu_action(menu_popover, name),
            )
            button.update_property([Gtk.AccessibleProperty.LABEL], [label])
            menu_box.append(button)

        popover.set_child(menu_box)
        menu_button.set_popover(popover)
        return menu_button

    def _activate_app_menu_action(self, popover, action_name):
        popover.popdown()
        app = self.get_application()
        if app is not None:
            app.activate_action(action_name, None)

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
        clamp.set_valign(Gtk.Align.START)
        for m in ['top', 'bottom', 'start', 'end']:
            getattr(clamp, f'set_margin_{m}')(24)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)

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
        cards_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        cards_box.set_halign(Gtk.Align.CENTER)
        cards_box.set_homogeneous(True)

        for pid, info in VPN_PROVIDERS.items():
            card = self._create_vpn_card(pid, info)
            cards_box.append(card)

        box.append(cards_box)

        # Comparison table (real grid, mockup 04)
        compare_group = Adw.PreferencesGroup()
        compare_group.set_title(_('Quick Comparison'))
        compare_group.set_header_suffix(create_icon_widget('preferences-other-symbolic', size=18))
        # Brand names (column headers) are not translated.
        compare_group.add(create_comparison_table(
            ['Tailscale', 'ZeroTier', 'Headscale'],
            [
                (_('Setup difficulty'), [_('Beginner'), _('Intermediate'), _('Advanced')]),
                (_('Hosting model'), [_('Cloud (free)'), _('Cloud (free)'), _('Self-hosted')]),
                (_('Best for'), [_('Personal use and friends'),
                                 _('Flexible networks and teams'),
                                 _('Corporate environments')]),
            ],
        ))
        box.append(compare_group)

        # "How it works" strip: Install → Authenticate → Play.
        steps_group = Adw.PreferencesGroup()
        steps_group.add(create_steps_strip([
            ('document-save-symbolic', _('Install'),
             _('Install the chosen VPN provider on your device.')),
            ('system-users-symbolic', _('Authenticate'),
             _('Log in and authorize your devices on the VPN.')),
            ('input-gaming-symbolic', _('Play'),
             _('Connect to the server and play with your friends!')),
        ]))
        box.append(steps_group)

        clamp.set_child(box)
        scroll.set_child(clamp)
        return scroll

    def _create_vpn_card(self, provider_id: str, info: dict) -> Gtk.Button:
        """Create a VPN option card button."""
        btn = Gtk.Button()
        btn.add_css_class('action-card')
        # 'card-accent' keeps readable dark text (unlike 'suggested-action',
        # which forces white text on the near-white tint).
        btn.add_css_class('card-accent')
        btn.set_size_request(220, 190)
        btn.connect('clicked', lambda b, pid=provider_id: self._on_vpn_selected(pid))
        btn.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            [info['name'], info['description']],
        )

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        for m in ['top', 'bottom', 'start', 'end']:
            getattr(box, f'set_margin_{m}')(16)

        # Icon
        icon = create_icon_widget(info['icon'], size=44)
        icon.add_css_class('accent')
        box.append(icon)

        # Name
        name_lbl = Gtk.Label(label=info['name'])
        name_lbl.add_css_class('title-3')
        box.append(name_lbl)

        # Description
        desc_lbl = Gtk.Label(label=info['description'])
        desc_lbl.add_css_class('caption')
        desc_lbl.add_css_class('dim-label')
        desc_lbl.set_wrap(True)
        desc_lbl.set_max_width_chars(24)
        desc_lbl.set_justify(Gtk.Justification.CENTER)
        box.append(desc_lbl)

        # Difficulty pill (mockup 04): Tailscale=beginner ... Headscale=advanced.
        levels = {'tailscale': 'beginner', 'zerotier': 'intermediate', 'headscale': 'advanced'}
        box.append(create_difficulty_pill(levels.get(provider_id, 'intermediate')))

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
                subprocess.run(["bigsudo", "tailscale", "logout"], timeout=30)
            elif vpn_id == 'zerotier':
                # Local ZT disconnection is a bit trickier, 
                # usually means leaving all networks or stopping the service
                # For simplicity, we can try to leave networks found in history or just stop service
                subprocess.run(["bigsudo", "systemctl", "stop", "zerotier-one"], timeout=30)
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

        # Centred vertically so short content distributes the slack top and bottom
        # (no empty "footer" band at the bottom); compact so it still fits.
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main_box.set_valign(Gtk.Align.CENTER)
        main_box.set_halign(Gtk.Align.CENTER)
        main_box.set_margin_top(24)
        main_box.set_margin_bottom(24)

        logo_img = create_logo_widget('big-remote-play', 72)
        logo_img.set_halign(Gtk.Align.CENTER)
        logo_img.set_valign(Gtk.Align.CENTER)
        logo_img.set_margin_bottom(8)
        main_box.append(logo_img)

        title = Gtk.Label(label='Big Remote Play')
        title.add_css_class('hero-title')
        title.add_css_class('animate-fade')
        main_box.append(title)

        subtitle = Gtk.Label(label=_('Play cooperatively over the local network or the internet'))
        subtitle.add_css_class('hero-subtitle')
        subtitle.add_css_class('animate-fade')
        subtitle.add_css_class('delay-1')
        main_box.append(subtitle)

        # Frame the decision so a first-time user knows what the two cards mean.
        prompt = Gtk.Label(label=_('What do you want to do?'))
        prompt.add_css_class('title-4')
        prompt.add_css_class('animate-fade')
        prompt.add_css_class('delay-1')
        prompt.set_margin_top(20)
        prompt.set_margin_bottom(12)
        main_box.append(prompt)

        cards_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        cards_box.set_halign(Gtk.Align.CENTER)
        cards_box.add_css_class('animate-fade')
        cards_box.add_css_class('delay-2')

        # Host is the primary action (this PC shares the game); mark it visually.
        self.host_card = self.create_action_card(
            _('Share the game'),
            _('Run the game on THIS PC and let friends connect to play together'),
            'network-server-symbolic',
            lambda: self.navigate_to('host'),
            primary=True,
            badge=_('Recommended'),
            cta=_('Start sharing →'),
        )

        self.guest_card = self.create_action_card(
            _('Join a game'),
            _("Connect to a friend's PC over the network and play remotely"),
            'network-workgroup-symbolic',
            lambda: self.navigate_to('guest'),
            cta=_('Connect now →'),
        )

        cards_box.append(self.host_card)
        cards_box.append(self.guest_card)
        main_box.append(cards_box)

        # "How it works" strip, framed to plain-language 3 steps (mockup 02).
        steps = create_steps_strip([
            ('network-server-symbolic',
             _('Start or connect'),
             _('Start a server on this PC or connect to an existing one.')),
            ('system-users-symbolic',
             _('Invite friends'),
             _('Share the PIN code or the server address.')),
            ('input-gaming-symbolic',
             _('Play together'),
             _('Everyone connects and plays remotely.')),
        ])
        steps.set_size_request(760, -1)
        steps.set_halign(Gtk.Align.CENTER)
        steps.set_margin_top(20)
        main_box.append(steps)

        scroll.set_child(main_box)
        return scroll

    def create_action_card(self, title, desc, icon, cb, primary=False, badge=None, cta=None):
        btn = Gtk.Button()
        btn.add_css_class('action-card')
        # 'card-accent' tints + accent-borders the card while keeping the normal
        # (dark) foreground, unlike 'suggested-action' which forces white text.
        if primary:
            btn.add_css_class('card-accent')
        btn.set_size_request(270, 188)
        btn.set_hexpand(False)
        btn.set_vexpand(False)
        btn.connect('clicked', lambda b: cb())
        # Multi-label child: GTK can't infer a name, so set it (title + description).
        btn.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            [title, desc],
        )

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_hexpand(False)
        box.set_vexpand(False)
        for m in ['top', 'bottom', 'start', 'end']: getattr(box, f'set_margin_{m}')(16)

        if badge:
            bl = Gtk.Label(label=badge)
            bl.add_css_class('card-badge')
            bl.set_halign(Gtk.Align.CENTER)
            box.append(bl)

        img = create_icon_widget(icon, size=44)
        img.set_size_request(44, 44)
        img.set_hexpand(False)
        img.set_vexpand(False)
        img.set_valign(Gtk.Align.CENTER)
        img.set_halign(Gtk.Align.CENTER)
        if primary:
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
        dl.set_max_width_chars(26)
        dl.set_justify(Gtk.Justification.CENTER)
        dl.set_hexpand(False)
        box.append(dl)

        # Call-to-action link inside the card (mockup: "Start hosting →").
        if cta:
            cl = Gtk.Label(label=cta)
            cl.add_css_class('caption-heading')
            cl.set_margin_top(4)
            box.append(cl)

        btn.set_child(box)
        return btn

    # ─────────────────────────────────────────────────────────────────────────
    #  NAVIGATION
    # ─────────────────────────────────────────────────────────────────────────

    def on_nav_selected(self, lb, row):
        if not row: return
        pid = self._nav_page_by_row.get(row)
        if not pid: return

        # Update visual style active state
        c = self.nav_list.get_first_child()
        while c:
            c.remove_css_class('active-category')
            c = c.get_next_sibling()
        row.add_css_class('active-category')

        if not hasattr(self, 'content_stack'):
            return

        # If no VPN is set yet and user clicks vpn_selector, show the selector
        actual_pid = pid
        
        if pid == 'change_vpn':
            self.reset_vpn_choice()
            return

        if pid == 'vpn_selector' or (pid in ('create_private', 'connect_private') and not self._vpn_choice):
            actual_pid = 'vpn_selector'

        if self.content_stack.get_visible_child_name() != actual_pid:
            self.content_stack.set_visible_child_name(actual_pid)
            self.current_page = actual_pid

        # Server page: drop the header title, show the host action buttons there.
        # Every other page keeps the dynamic section title.
        if hasattr(self, 'content_headerbar'):
            if actual_pid == 'host' and hasattr(self.host_view, 'header_action_box'):
                self.content_headerbar.set_title_widget(self.host_view.header_action_box)
            else:
                self.content_headerbar.set_title_widget(self.content_title)
                info = self._build_navigation_pages().get(actual_pid) or self._build_navigation_pages().get(pid)
                if info:
                    self.content_title.set_title(info.get('name', 'Big Remote Play'))
                    self.content_title.set_subtitle(info.get('description', ''))
    def navigate_to(self, pid):
        """Programmatic navigation: find row and select it"""
        r = self.nav_list.get_first_child()
        while r:
            if isinstance(r, Gtk.ListBoxRow) and self._nav_page_by_row.get(r) == pid:
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

            def finish_system_check():
                self.update_status(h_sun, h_moon)
                self.update_server_status(r_sun, r_moon, r_docker, r_tail, r_zt)
                self.update_dependency_ui(h_sun, h_moon, h_docker, h_tail, h_zt)
                return False

            GLib.idle_add(finish_system_check)
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
            try:
                is_enabled = subprocess.run(cmd, timeout=5).returncode == 0
            except Exception:
                is_enabled = False

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
                    try: subprocess.run(["pkill", "-x", bin_name], check=False, timeout=10)
                    except Exception: pass
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
