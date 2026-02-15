import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
import json
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

try:
    gi.require_version("Vte", "3.91")
    from gi.repository import Vte

    HAS_VTE = True
except:
    HAS_VTE = False
from utils.i18n import _
from utils.icons import create_icon_widget


class AccessInfoWidget(Gtk.Box):
    """Widget to display access information after installation.
    Fixed header + scrollable content + fixed buttons at the bottom."""

    def __init__(self, data, on_save_cb, main_window=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.data = data
        self.on_save_cb = on_save_cb
        self.main_window = main_window

        # Extract clean domain
        raw_url = data.get('api_url') or data.get('web_ui', '')
        self.clean_domain = raw_url.replace('http://', '').replace('https://', '').split('/')[0].strip()
        if not self.clean_domain:
            self.clean_domain = data.get('public_ip', '')
        self.auth_key = data.get('auth_key', '')

        # ═══════════════════════════════════════
        #  FIXED HEADER (top)
        # ═══════════════════════════════════════
        header_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        header_box.set_margin_top(16)
        header_box.set_margin_start(20)
        header_box.set_margin_end(20)
        header_box.set_margin_bottom(8)

        title = Gtk.Label(label=_('🎮 Network Created Successfully!'))
        title.add_css_class('title-2')
        header_box.append(title)

        subtitle = Gtk.Label(
            label=_('Share the info below with your friends so they can join '
                    'via "Connect to Private Network" and play together!')
        )
        subtitle.set_wrap(True)
        subtitle.add_css_class('dim-label')
        subtitle.add_css_class('caption')
        subtitle.set_margin_top(4)
        header_box.append(subtitle)

        self.append(header_box)
        self.append(Gtk.Separator())

        # ═══════════════════════════════════════
        #  SCROLLABLE CONTENT (middle)
        # ═══════════════════════════════════════
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(600)
        for m in ['top', 'bottom', 'start', 'end']:
            getattr(clamp, f'set_margin_{m}')(12)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        # ── SHARE WITH FRIENDS ──
        share_group = Adw.PreferencesGroup()
        share_group.set_title(_('📤 Share with Your Friends'))
        share_group.set_description(
            _('Your friends only need these 2 items to join your network.')
        )
        content.append(share_group)

        domain_row = Adw.ActionRow()
        domain_row.set_title(_('Server Domain'))
        domain_row.set_subtitle(self.clean_domain)
        domain_row.add_css_class('property')
        domain_row.add_prefix(create_icon_widget('network-server-symbolic', size=20))
        btn_copy_domain = Gtk.Button()
        btn_copy_domain.set_child(create_icon_widget('edit-copy-symbolic', size=16))
        btn_copy_domain.add_css_class('flat')
        btn_copy_domain.set_valign(Gtk.Align.CENTER)
        btn_copy_domain.set_tooltip_text(_('Copy domain'))
        btn_copy_domain.connect('clicked', lambda b: self._copy_to_clipboard(self.clean_domain))
        domain_row.add_suffix(btn_copy_domain)
        share_group.add(domain_row)

        auth_row = Adw.ActionRow()
        auth_row.set_title(_('Auth Key (Friends)'))
        auth_row.set_subtitle(self.auth_key)
        auth_row.add_css_class('property')
        auth_row.add_prefix(create_icon_widget('key-symbolic', size=20))
        btn_copy_key = Gtk.Button()
        btn_copy_key.set_child(create_icon_widget('edit-copy-symbolic', size=16))
        btn_copy_key.add_css_class('flat')
        btn_copy_key.set_valign(Gtk.Align.CENTER)
        btn_copy_key.set_tooltip_text(_('Copy auth key'))
        btn_copy_key.connect('clicked', lambda b: self._copy_to_clipboard(self.auth_key))
        auth_row.add_suffix(btn_copy_key)
        share_group.add(auth_row)

        # ── HOW TO CONNECT ──
        how_group = Adw.PreferencesGroup()
        how_group.set_title(_('📋 How to Connect'))
        how_group.set_description(
            _('Both you AND your friends must connect!')
        )
        content.append(how_group)

        steps = [
            (_('1. Open "Connect to Private Network"'), _('In the sidebar menu'), 'go-next-symbolic'),
            (_('2. Enter domain and Auth Key'), _('Paste the data shown above'), 'edit-paste-symbolic'),
            (_('3. Click "Establish Connection"'), _('Play together over the internet!'), 'media-playback-start-symbolic'),
        ]
        for step_title, step_sub, step_icon in steps:
            sr = Adw.ActionRow()
            sr.set_title(step_title)
            sr.set_subtitle(step_sub)
            sr.add_prefix(create_icon_widget(step_icon, size=16))
            how_group.add(sr)

        # ── SERVER DETAILS ──
        details_group = Adw.PreferencesGroup()
        details_group.set_title(_('🔧 Server Details'))
        content.append(details_group)

        detail_fields = [
            ('web_ui', _('Web Interface'), 'external-link-symbolic'),
            ('api_url', _('API URL'), 'network-server-symbolic'),
            ('public_ip', _('Public IP'), 'network-workgroup-symbolic'),
            ('local_ip', _('Local IP'), 'network-local-symbolic'),
            ('api_key', _('API Key (Admin UI)'), 'dialog-password-symbolic'),
        ]
        for key, label, icon in detail_fields:
            val = data.get(key, '')
            if not val:
                continue
            row = Adw.ActionRow(title=label, subtitle=val)
            row.add_prefix(create_icon_widget(icon, size=16))
            btn_copy = Gtk.Button()
            btn_copy.set_child(create_icon_widget('edit-copy-symbolic', size=16))
            btn_copy.add_css_class('flat')
            btn_copy.set_valign(Gtk.Align.CENTER)
            btn_copy.connect('clicked', lambda b, v=val: self._copy_to_clipboard(v))
            row.add_suffix(btn_copy)
            if 'http' in val:
                btn_open = Gtk.Button()
                btn_open.set_child(create_icon_widget('external-link-symbolic', size=16))
                btn_open.add_css_class('flat')
                btn_open.set_valign(Gtk.Align.CENTER)
                btn_open.connect('clicked', lambda b, v=val: os.system(f'xdg-open {v}'))
                row.add_suffix(btn_open)
            details_group.add(row)

        clamp.set_child(content)
        scroll.set_child(clamp)
        self.append(scroll)

        # ═══════════════════════════════════════
        #  FIXED BUTTONS (bottom)
        # ═══════════════════════════════════════
        self.append(Gtk.Separator())

        btn_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        btn_area.set_margin_top(12)
        btn_area.set_margin_bottom(16)
        btn_area.set_margin_start(20)
        btn_area.set_margin_end(20)

        # Row 1: Save and Finish (primary action)
        btn_finish = Gtk.Button(label=_('Save and Finish'))
        btn_finish.add_css_class('suggested-action')
        btn_finish.add_css_class('pill')
        btn_finish.set_size_request(-1, 42)
        btn_finish.connect('clicked', self.on_save_clicked)
        btn_area.append(btn_finish)

        # Row 2: Save to File + Share
        row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row2.set_homogeneous(True)

        btn_save_file = Gtk.Button()
        save_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        save_box.set_halign(Gtk.Align.CENTER)
        save_box.append(create_icon_widget('document-save-symbolic', size=16))
        save_box.append(Gtk.Label(label=_('Save to File')))
        btn_save_file.set_child(save_box)
        btn_save_file.add_css_class('pill')
        btn_save_file.set_size_request(-1, 38)
        btn_save_file.connect('clicked', self._on_save_to_file)
        row2.append(btn_save_file)

        btn_share = Gtk.Button()
        share_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        share_box.set_halign(Gtk.Align.CENTER)
        share_box.append(create_icon_widget('emblem-shared-symbolic', size=16))
        share_box.append(Gtk.Label(label=_('Share')))
        btn_share.set_child(share_box)
        btn_share.add_css_class('pill')
        btn_share.set_size_request(-1, 38)
        btn_share.connect('clicked', self._on_share)
        row2.append(btn_share)

        btn_area.append(row2)

        # Reminder
        reminder = Gtk.Label(
            label=_('This info is saved in "Connect to Private Network" → "Previous Networks"')
        )
        reminder.set_wrap(True)
        reminder.add_css_class('caption')
        reminder.add_css_class('dim-label')
        btn_area.append(reminder)

        self.append(btn_area)

    def _get_share_text(self):
        """Generate formatted text for saving/sharing."""
        d = self.data
        text = (
            f"🎮 Private Network - Connection Info\n"
            f"{'=' * 42}\n\n"
            f"📤 SHARE WITH YOUR FRIEND:\n"
            f"  Server Domain: {self.clean_domain}\n"
            f"  Auth Key:      {self.auth_key}\n\n"
            f"📋 HOW TO CONNECT:\n"
            f"  1. Open Big Remote Play\n"
            f"  2. Go to 'Connect to Private Network'\n"
            f"  3. Enter the Domain and Auth Key above\n"
            f"  4. Click 'Establish Connection'\n"
            f"  5. Play together! 🎮\n\n"
            f"🔧 SERVER DETAILS:\n"
            f"  Web Interface: {d.get('web_ui', '-')}\n"
            f"  API URL:       {d.get('api_url', '-')}\n"
            f"  Public IP:     {d.get('public_ip', '-')}\n"
            f"  Local IP:      {d.get('local_ip', '-')}\n"
            f"  API Key (UI):  {d.get('api_key', '-')}\n"
            f"  Auth Key:      {d.get('auth_key', '-')}\n"
        )
        return text

    def _on_save_to_file(self, btn):
        """Save connection info to a .txt file."""
        dialog = Gtk.FileDialog(title=_('Save Connection Info'))
        dialog.set_initial_name(f'private_network_{self.clean_domain.replace(".", "_")}.txt')

        parent = self.main_window or self.get_root()

        def on_save_response(dialog, result):
            try:
                file = dialog.save_finish(result)
                if file:
                    path = file.get_path()
                    with open(path, 'w') as f:
                        f.write(self._get_share_text())
                    if self.main_window and hasattr(self.main_window, 'show_toast'):
                        self.main_window.show_toast(_('File saved: {}').format(path))
            except Exception as e:
                if self.main_window and hasattr(self.main_window, 'show_toast'):
                    self.main_window.show_toast(_('Error saving: {}').format(e))

        dialog.save(parent, None, on_save_response)

    def _on_share(self, btn):
        """Share connection info via clipboard (for pasting in apps/social media)."""
        text = self._get_share_text()
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(text)
        if self.main_window and hasattr(self.main_window, 'show_toast'):
            self.main_window.show_toast(_('Connection info copied! Paste it in any app to share.'))

    def _copy_to_clipboard(self, text):
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(text)

    def on_save_clicked(self, btn):
        self.on_save_cb(self.data)


class PrivateNetworkView(Adw.Bin):
    """View for managing private network (Headscale) - Single page create + Connect"""

    def __init__(self, main_window, mode="create"):
        super().__init__()
        self.main_window = main_window
        self.mode = mode
        self.public_ip = "..."

        self._worker_running = False
        self._worker_thread = None
        self._ping_executor = ThreadPoolExecutor(max_workers=10)

        self.install_data = {}

        self._connect_phase = 0
        self._connect_phases = [
            _('Preparing connection...'),
            _('Checking dependencies...'),
            _('Configuring Tailscale client...'),
            _('Logging in to network...'),
            _('Verifying connection...'),
            _('Connection established!'),
        ]

        self.setup_ui()
        self.set_mode(mode)

    def setup_ui(self):
        self.main_stack = Gtk.Stack()
        self.main_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.set_child(self.main_stack)

        # Create Page (Single Page)
        self.create_page = self.setup_create_page()
        self.main_stack.add_named(self.create_page, "create")

        # Connect Page
        self.connect_page = self.setup_connect_page()
        self.main_stack.add_named(self.connect_page, "connect")

    def set_mode(self, mode):
        self.mode = mode
        self.main_stack.set_visible_child_name(mode)

        if mode == "connect":
            self._start_worker()
        else:
            self._stop_worker()

    # ─────────────────────────────────────────────
    #  CREATE PAGE (Single Page)
    # ─────────────────────────────────────────────

    def setup_create_page(self):
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(800)
        for margin in ['top', 'bottom', 'start', 'end']:
            getattr(clamp, f'set_margin_{margin}')(24)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)

        # ── Header (same style as Host Server) ──
        self.header = Adw.PreferencesGroup()
        self.header.set_header_suffix(create_icon_widget('network-wired-symbolic', size=24))
        self.header.set_title(_('Create Private Network'))
        self.header.set_description(
            _('Set up your own Headscale VPN server with automatic DNS configuration via Cloudflare. '
              'This allows you and your friends to connect securely over the internet.')
        )
        content.append(self.header)

        # ── Input Fields ──
        input_group = Adw.PreferencesGroup()
        input_group.set_title(_('Server Configuration'))
        input_group.set_description(_('Enter your Cloudflare credentials to automate the installation'))

        self.entry_domain = Adw.EntryRow(title=_("Enter your domain"))
        self.entry_domain.set_tooltip_text(_("e.g.: myserver.us.kg"))

        self.entry_zone = Adw.EntryRow(title=_("Zone ID"))
        self.entry_zone.set_tooltip_text(_("Found in Cloudflare Overview page"))

        self.entry_token = Adw.PasswordEntryRow(title=_("API Token"))
        self.entry_token.set_tooltip_text(_("Token with DNS Edit permission"))

        input_group.add(self.entry_domain)
        input_group.add(self.entry_zone)
        input_group.add(self.entry_token)
        content.append(input_group)

        # ── Progress Area (hidden by default) ──
        self.create_progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.create_progress_box.set_visible(False)

        self.create_progress_status = Gtk.Label(label="")
        self.create_progress_status.add_css_class("caption")
        self.create_progress_status.set_halign(Gtk.Align.START)
        self.create_progress_box.append(self.create_progress_status)

        # Horizontal box: LevelBar + info button
        level_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        level_row.set_valign(Gtk.Align.CENTER)

        self.create_level_bar = Gtk.LevelBar()
        self.create_level_bar.set_min_value(0)
        self.create_level_bar.set_max_value(1.0)
        self.create_level_bar.set_value(0)
        self.create_level_bar.set_hexpand(True)
        self.create_level_bar.set_size_request(-1, 16)
        # Remove default level offsets and add custom ones
        self.create_level_bar.remove_offset_value(Gtk.LEVEL_BAR_OFFSET_LOW)
        self.create_level_bar.remove_offset_value(Gtk.LEVEL_BAR_OFFSET_HIGH)
        self.create_level_bar.remove_offset_value(Gtk.LEVEL_BAR_OFFSET_FULL)
        self.create_level_bar.add_offset_value('install-low', 0.25)
        self.create_level_bar.add_offset_value('install-mid', 0.50)
        self.create_level_bar.add_offset_value('install-high', 0.75)
        self.create_level_bar.add_offset_value('install-full', 1.0)

        # Make level bar clickable to open terminal dialog
        click_gesture = Gtk.GestureClick()
        click_gesture.connect("pressed", lambda g, n, x, y: self._open_terminal_dialog("create"))
        self.create_level_bar.add_controller(click_gesture)
        try:
            self.create_level_bar.set_cursor(Gdk.Cursor.new_from_name("pointer", None))
        except TypeError:
            self.create_level_bar.set_cursor(Gdk.Cursor.new_from_name("pointer"))
        level_row.append(self.create_level_bar)

        # Percentage label inside/after the bar
        self.create_progress_percent = Gtk.Label(label='0%')
        self.create_progress_percent.add_css_class('caption-heading')
        self.create_progress_percent.set_size_request(40, -1)
        level_row.append(self.create_progress_percent)

        # Info button to re-open Access Information
        self.create_btn_info = Gtk.Button()
        self.create_btn_info.set_child(create_icon_widget('dialog-information-symbolic', size=18))
        self.create_btn_info.add_css_class('flat')
        self.create_btn_info.add_css_class('circular')
        self.create_btn_info.set_tooltip_text(_('View access information'))
        self.create_btn_info.set_visible(False)
        self.create_btn_info.connect('clicked', lambda b: self.show_success_dialog())
        level_row.append(self.create_btn_info)

        self.create_progress_box.append(level_row)
        content.append(self.create_progress_box)

        # ── Terminal TextView (lives inside the dialog, created once) ──
        self.create_text_view = Gtk.TextView(editable=False, monospace=True)
        self.create_text_view.add_css_class("card")
        self.create_text_view.set_size_request(-1, 400)
        self.create_text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)

        # Setup tags for ANSI colors
        buffer = self.create_text_view.get_buffer()
        table = buffer.get_tag_table()
        ansi_colors = {
            "green": "#2ec27e",
            "blue": "#3584e4",
            "yellow": "#f5c211",
            "red": "#ed333b",
            "cyan": "#33c7de",
            "bold": None,
        }
        for name, color in ansi_colors.items():
            tag = Gtk.TextTag(name=name)
            if color:
                tag.set_property("foreground", color)
            if name == "bold":
                tag.set_property("weight", 700)
            table.add(tag)

        self._terminal_dialog = None
        self._install_phase = 0
        self._install_phases = [
            _('Preparing environment...'),
            _('Installing Docker...'),
            _('Configuring Headscale...'),
            _('Setting up Caddy reverse proxy...'),
            _('Configuring DNS records...'),
            _('Generating access keys...'),
            _('Finalizing installation...'),
        ]

        # ── Buttons ──
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        button_box.set_halign(Gtk.Align.CENTER)
        button_box.set_margin_top(12)
        button_box.set_margin_bottom(24)

        self.btn_install = Gtk.Button()
        self.btn_install.add_css_class('pill')
        self.btn_install.add_css_class('suggested-action')
        self.btn_install.set_size_request(200, 50)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_box.set_halign(Gtk.Align.CENTER)
        self.install_spinner = Gtk.Spinner()
        self.install_spinner.set_visible(False)
        self.install_label = Gtk.Label(label=_('Install Server'))
        btn_box.append(self.install_spinner)
        btn_box.append(self.install_label)
        self.btn_install.set_child(btn_box)
        self.btn_install.connect('clicked', self.on_install_clicked)

        self.btn_instructions = Gtk.Button(label=_('Instructions'))
        self.btn_instructions.add_css_class('pill')
        self.btn_instructions.set_size_request(200, 50)
        self.btn_instructions.connect('clicked', self.show_instructions_dialog)

        button_box.append(self.btn_install)
        button_box.append(self.btn_instructions)
        content.append(button_box)

        clamp.set_child(content)
        scroll.set_child(clamp)
        return scroll

    def on_install_clicked(self, btn):
        domain = self.entry_domain.get_text().strip()
        zone = self.entry_zone.get_text().strip()
        token = self.entry_token.get_text().strip()

        if not domain:
            self.main_window.show_toast(_("Domain is required"))
            return
        if not zone:
            self.main_window.show_toast(_("Zone ID is required"))
            return
        if not token:
            self.main_window.show_toast(_("API Token is required"))
            return

        self.run_install()

    # ─────────────────────────────────────────────
    #  INSTRUCTIONS DIALOG (Adw.ToolbarView)
    # ─────────────────────────────────────────────

    def show_instructions_dialog(self, btn):
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
        hb.set_title_widget(Adw.WindowTitle.new(
            _("Setup Instructions"),
            _("Step-by-step guide to create your private network")
        ))
        toolbar_view.add_top_bar(hb)

        # Scrollable content
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(650)
        for m in ['top', 'bottom', 'start', 'end']:
            getattr(clamp, f'set_margin_{m}')(16)

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
        r1_1.add_prefix(create_icon_widget("web-browser-symbolic", size=20))

        btn_digitalplat = Gtk.Button(label=_("Open Site"))
        btn_digitalplat.add_css_class("pill")
        btn_digitalplat.add_css_class("suggested-action")
        btn_digitalplat.set_valign(Gtk.Align.CENTER)
        btn_digitalplat.connect(
            "clicked", lambda b: os.system("xdg-open https://dash.domain.digitalplat.org/")
        )
        r1_1.add_suffix(btn_digitalplat)
        g1.add(r1_1)

        r1_2 = Adw.ActionRow()
        r1_2.set_title(_("Steps at DigitalPlat"))
        r1_2.set_subtitle(
            _("1. Create an account or login\n"
              "2. Choose a .us.kg domain\n"
              "3. Complete the simple registration\n"
              "4. Write down the domain you obtained (e.g.: myserver.us.kg)")
        )
        r1_2.add_prefix(create_icon_widget("dialog-information-symbolic", size=20))
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
        r2_1.add_prefix(create_icon_widget("web-browser-symbolic", size=20))

        btn_cloudflare = Gtk.Button(label=_("Open Cloudflare"))
        btn_cloudflare.add_css_class("pill")
        btn_cloudflare.add_css_class("suggested-action")
        btn_cloudflare.set_valign(Gtk.Align.CENTER)
        btn_cloudflare.connect(
            "clicked", lambda b: os.system("xdg-open https://dash.cloudflare.com/")
        )
        r2_1.add_suffix(btn_cloudflare)
        g2.add(r2_1)

        r2_2 = Adw.ActionRow()
        r2_2.set_title(_("Setup Steps"))
        r2_2.set_subtitle(
            _("1. Click 'Add a site' → Enter your domain\n"
              "2. Choose the FREE plan\n"
              "3. Write down the 2 nameservers provided\n"
              "4. Go back to your domain provider (DigitalPlat)\n"
              "5. Replace the DNS with Cloudflare's nameservers\n"
              "6. Wait for DNS propagation (may take a few minutes)")
        )
        r2_2.add_prefix(create_icon_widget("dialog-information-symbolic", size=20))
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
            lambda b: os.system(
                "xdg-open https://dash.domain.digitalplat.org/panel/main?page=%2Fpanel%2Fdomains"
            ),
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
        r3_1.set_subtitle(
            _("1. In Cloudflare, click on your domain\n"
              "2. Scroll down to the 'API' section on the right\n"
              "3. Copy the 'Zone ID' value")
        )
        r3_1.add_prefix(create_icon_widget("dialog-password-symbolic", size=20))
        g3.add(r3_1)

        r3_2 = Adw.ActionRow()
        r3_2.set_title(_("API Token"))
        r3_2.set_subtitle(
            _("1. Click 'Get your API token' (below Zone ID)\n"
              "2. Click 'Create Token'\n"
              "3. Use template: 'Edit zone DNS' → 'Use template'\n"
              "4. Configure:\n"
              "   • Token name: VPN-Token\n"
              "   • Permissions: Zone - DNS - Edit\n"
              "   • Zone: Select your domain\n"
              "5. Click 'Continue to summary' → 'Create Token'\n"
              "6. Copy token IMMEDIATELY (shown only once!)")
        )
        r3_2.add_prefix(create_icon_widget("key-symbolic", size=20))
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

        btn_copy_ip = Gtk.Button()
        btn_copy_ip.set_child(create_icon_widget("edit-copy-symbolic", size=16))
        btn_copy_ip.add_css_class("flat")
        btn_copy_ip.set_valign(Gtk.Align.CENTER)
        btn_copy_ip.set_tooltip_text(_("Copy IP"))
        btn_copy_ip.connect(
            "clicked", lambda b: self._copy_to_clipboard(self.public_ip)
        )

        ip_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ip_box.append(self.instructions_ip_label)
        ip_box.append(btn_copy_ip)
        r4_ip.add_suffix(ip_box)
        g4.add(r4_ip)

        # Fetch IP in background
        def fetch_ip():
            try:
                ip = subprocess.check_output(
                    ["curl", "-s", "ipinfo.io/ip"], timeout=10
                ).decode().strip()
                self.public_ip = ip
                GLib.idle_add(self.instructions_ip_label.set_label, ip)
            except:
                self.public_ip = _("Error")
                GLib.idle_add(self.instructions_ip_label.set_label, _("Error"))

        threading.Thread(target=fetch_ip, daemon=True).start()

        r4_a = Adw.ActionRow()
        r4_a.set_title(_("A Record"))
        r4_a.set_subtitle(
            _("In Cloudflare → DNS → Records → 'Add record':\n"
              "• Type: A\n"
              "• Name: @\n"
              "• Content: YOUR-PUBLIC-IP (shown above)\n"
              "• Proxy: OFF (gray cloud — IMPORTANT!)")
        )
        r4_a.add_prefix(create_icon_widget("network-server-symbolic", size=20))
        g4.add(r4_a)

        r4_cname = Adw.ActionRow()
        r4_cname.set_title(_("CNAME Record"))
        r4_cname.set_subtitle(
            _("Add another record:\n"
              "• Type: CNAME\n"
              "• Name: www\n"
              "• Target: @\n"
              "• Proxy: OFF")
        )
        r4_cname.add_prefix(create_icon_widget("network-server-symbolic", size=20))
        g4.add(r4_cname)

        main_box.append(g4)

        # ════════════════════════════════════════════
        #  STEP 5: Configure Router
        # ════════════════════════════════════════════
        g5 = Adw.PreferencesGroup()
        g5.set_title(_("5. Configure Router (Port Forwarding)"))
        g5.set_description(_("Open the required ports on your router"))

        r5_1 = Adw.ActionRow()
        r5_1.set_title(_("Access Your Router"))
        r5_1.set_subtitle(
            _("1. Open your browser and go to 192.168.1.1 (or your router's IP)\n"
              "2. Find 'Port Forwarding' or 'NAT' settings")
        )
        r5_1.add_prefix(create_icon_widget("network-wired-symbolic", size=20))
        g5.add(r5_1)

        ports_data = [
            ("8080/TCP", _("Web Interface and API")),
            ("9443/TCP", _("Admin Panel (Headscale UI)")),
            ("41641/UDP", _("Peer-to-peer VPN data")),
        ]
        for port, desc_text in ports_data:
            pr = Adw.ActionRow()
            pr.set_title(f"Port {port}")
            pr.set_subtitle(f"{desc_text} → {_('Forward to your local IP')}")
            pr.add_prefix(create_icon_widget("network-transmit-receive-symbolic", size=20))
            g5.add(pr)

        r5_tip = Adw.ActionRow()
        r5_tip.set_title(_("Find your local IP"))
        r5_tip.set_subtitle(_("Run 'hostname -I' in a terminal to find your local IP address"))
        r5_tip.add_prefix(create_icon_widget("utilities-terminal-symbolic", size=20))
        g5.add(r5_tip)

        main_box.append(g5)

        # ════════════════════════════════════════════
        #  QUICK SUMMARY
        # ════════════════════════════════════════════
        g_summary = Adw.PreferencesGroup()
        g_summary.set_title(_("✅ Quick Summary"))
        g_summary.set_description(_("Checklist before clicking 'Install Server'"))

        summary_steps = [
            _("1. Register free domain at DigitalPlat"),
            _("2. Change DNS to Cloudflare nameservers"),
            _("3. Get Zone ID + Create API Token"),
            _("4. Create A record with your IP (proxy OFF)"),
            _("5. Open ports 8080, 9443, 41641 on router"),
        ]
        for step in summary_steps:
            sr = Adw.ActionRow()
            sr.set_title(step)
            sr.add_prefix(create_icon_widget("emblem-ok-symbolic", size=16))
            g_summary.add(sr)

        main_box.append(g_summary)

        # Set content
        clamp.set_child(main_box)
        scroll.set_child(clamp)
        toolbar_view.set_content(scroll)

        dialog.set_content(toolbar_view)
        dialog.present()

    # ─────────────────────────────────────────────
    #  CONNECT PAGE
    # ─────────────────────────────────────────────

    def setup_connect_page(self):
        self.connect_toolbar_view = Adw.ToolbarView()
        self.connect_stack = Adw.ViewStack()

        # Connection Form Tab
        conn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        conn_box.set_margin_top(30)
        conn_box.set_margin_bottom(30)
        conn_box.set_margin_start(40)
        conn_box.set_margin_end(40)

        # ── Header ──
        conn_header = Adw.PreferencesGroup()
        conn_header.set_header_suffix(create_icon_widget('network-vpn-symbolic', size=24))
        conn_header.set_title(_("Connect to Private Network"))
        conn_header.set_description(
            _("Join an existing Headscale private network. You need a Server Domain and an Auth Key provided by the network administrator.")
        )
        conn_box.append(conn_header)

        # ── Group ──
        group = Adw.PreferencesGroup()
        group.set_title(_("Connection Details"))
        self.entry_connect_domain = Adw.EntryRow(title=_("Server Domain"))
        self.entry_auth_key = Adw.PasswordEntryRow(title=_("Auth Key"))

        # Load saved values
        app = self.main_window.get_application()
        if hasattr(app, "config"):
            saved_domain = app.config.get("private_network_domain", "")
            saved_key = app.config.get("private_network_key", "")
            self.entry_connect_domain.set_text(saved_domain)
            self.entry_auth_key.set_text(saved_key)

        # Connect signals to save on change
        self.entry_connect_domain.connect("changed", self._on_domain_changed)
        self.entry_auth_key.connect("changed", self._on_key_changed)

        group.add(self.entry_connect_domain)
        group.add(self.entry_auth_key)
        conn_box.append(group)

        # ── Progress Area (Connect) ──
        self.connect_progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.connect_progress_box.set_visible(False)

        self.connect_progress_status = Gtk.Label(label="")
        self.connect_progress_status.add_css_class("caption")
        self.connect_progress_status.set_halign(Gtk.Align.START)
        self.connect_progress_box.append(self.connect_progress_status)

        level_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        level_row.set_valign(Gtk.Align.CENTER)

        self.connect_level_bar = Gtk.LevelBar()
        self.connect_level_bar.set_min_value(0)
        self.connect_level_bar.set_max_value(1.0)
        self.connect_level_bar.set_value(0)
        self.connect_level_bar.set_hexpand(True)
        self.connect_level_bar.set_size_request(-1, 16)
        self.connect_level_bar.remove_offset_value(Gtk.LEVEL_BAR_OFFSET_LOW)
        self.connect_level_bar.remove_offset_value(Gtk.LEVEL_BAR_OFFSET_HIGH)
        self.connect_level_bar.remove_offset_value(Gtk.LEVEL_BAR_OFFSET_FULL)
        self.connect_level_bar.add_offset_value('install-low', 0.25)
        self.connect_level_bar.add_offset_value('install-mid', 0.50)
        self.connect_level_bar.add_offset_value('install-high', 0.75)
        self.connect_level_bar.add_offset_value('install-full', 1.0)

        click_gesture = Gtk.GestureClick()
        click_gesture.connect("pressed", lambda g, n, x, y: self._open_terminal_dialog("connect"))
        self.connect_level_bar.add_controller(click_gesture)
        try:
            self.connect_level_bar.set_cursor(Gdk.Cursor.new_from_name("pointer", None))
        except TypeError:
            self.connect_level_bar.set_cursor(Gdk.Cursor.new_from_name("pointer"))
        level_row.append(self.connect_level_bar)

        self.connect_progress_percent = Gtk.Label(label='0%')
        self.connect_progress_percent.add_css_class('caption-heading')
        self.connect_progress_percent.set_size_request(40, -1)
        level_row.append(self.connect_progress_percent)

        self.connect_progress_box.append(level_row)
        conn_box.append(self.connect_progress_box)

        # ── Buttons ──
        self.btn_connect = Gtk.Button()
        self.btn_connect.add_css_class("pill")
        self.btn_connect.add_css_class("suggested-action")
        self.btn_connect.set_halign(Gtk.Align.CENTER)
        self.btn_connect.set_size_request(240, 50)
        
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_box.set_halign(Gtk.Align.CENTER)
        self.connect_spinner = Gtk.Spinner()
        self.connect_spinner.set_visible(False)
        self.connect_label = Gtk.Label(label=_("Establish Connection"))
        btn_box.append(self.connect_spinner)
        btn_box.append(self.connect_label)
        self.btn_connect.set_child(btn_box)
        self.btn_connect.connect("clicked", self.on_connect_clicked)
        conn_box.append(self.btn_connect)

        # Terminal view for connect (dialog use)
        self.connect_log_view = Gtk.TextView(editable=False, monospace=True)
        self.connect_log_view.add_css_class("card")
        self.connect_log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        # Use existing tags table if possible, or new one
        conn_buffer = self.connect_log_view.get_buffer()
        # Tags are shared if we use the same table, but here it's separate text views.
        # I'll just repeat the tags setup later or ensure they are present.
        conn_table = conn_buffer.get_tag_table()
        ansi_colors = {
            "green": "#2ec27e", "blue": "#3584e4", "yellow": "#f5c211", "red": "#ed333b", "cyan": "#33c7de", "bold": None,
        }
        for name, color in ansi_colors.items():
            tag = Gtk.TextTag(name=name)
            if color: tag.set_property("foreground", color)
            if name == "bold": tag.set_property("weight", 700)
            conn_table.add(tag)

        page_conn = self.connect_stack.add_titled(conn_box, "connection", _("Connect"))
        page_conn.set_icon_name("network-server-symbolic")

        # Network Status Tab
        net_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        net_box.set_margin_top(12)
        net_box.set_margin_bottom(12)
        net_box.set_margin_start(12)
        net_box.set_margin_end(12)

        net_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        net_title = Gtk.Label(label=_("Network Devices"))
        net_title.add_css_class("title-4")
        net_header.append(net_title)

        btn_refresh = Gtk.Button()
        btn_refresh.set_child(create_icon_widget("view-refresh-symbolic", size=16))
        btn_refresh.add_css_class("flat")
        btn_refresh.connect("clicked", lambda b: self.refresh_peers())
        btn_refresh.set_halign(Gtk.Align.END)
        btn_refresh.set_hexpand(True)
        net_header.append(btn_refresh)
        net_box.append(net_header)

        # Columns: IP, Host, User, OS, Connection/Relay, Stats (Tx/Rx), Ping
        self.peers_model = Gtk.ListStore(str, str, str, str, str, str, str)
        self.peers_tree = Gtk.TreeView(model=self.peers_model)

        cols = [
            _("IP"),
            _("Host"),
            _("User"),
            _("System"),
            _("Connection"),
            _("Traffic (Tx/Rx)"),
            _("Ping"),
        ]
        for i, col_title in enumerate(cols):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(col_title, renderer, text=i)
            if i == 6:
                renderer.set_property("xalign", 0.5)
            column.set_resizable(True)
            column.set_expand(True if i in [1, 4] else False)
            self.peers_tree.append_column(column)

        scroll_tree = Gtk.ScrolledWindow(vexpand=True)
        scroll_tree.set_child(self.peers_tree)
        scroll_tree.add_css_class("card")
        net_box.append(scroll_tree)

        page_net = self.connect_stack.add_titled(net_box, "network", _("Status"))
        page_net.set_icon_name("network-transmit-receive-symbolic")

        # Previous Networks Tab
        self.history_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        self.history_box.set_margin_top(30)
        self.history_box.set_margin_bottom(30)
        self.history_box.set_margin_start(40)
        self.history_box.set_margin_end(40)

        history_scroll = Gtk.ScrolledWindow(vexpand=True)
        self.history_list_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12
        )
        history_scroll.set_child(self.history_list_box)
        self.history_box.append(history_scroll)

        page_hist = self.connect_stack.add_titled(
            self.history_box, "history", _("Previous Networks")
        )
        page_hist.set_icon_name("document-open-recent-symbolic")

        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        switcher = Adw.ViewSwitcher(stack=self.connect_stack)
        header.set_title_widget(switcher)
        self.connect_toolbar_view.add_top_bar(header)
        self.connect_toolbar_view.set_content(self.connect_stack)

        self.connect_stack.connect(
            "notify::visible-child-name", self.on_connect_stack_changed
        )

        if self.mode == "connect":
            self._start_worker()
            self.refresh_history_ui()

        return self.connect_toolbar_view

    # ─────────────────────────────────────────────
    #  HISTORY
    # ─────────────────────────────────────────────

    def refresh_history_ui(self):
        """Load history from JSON and populate the list box"""
        while child := self.history_list_box.get_first_child():
            self.history_list_box.remove(child)

        config_dir = os.path.expanduser("~/.config/big-remoteplay/private_network")
        history_file = os.path.join(config_dir, "private_network.json")

        if not os.path.exists(history_file):
            status = Adw.StatusPage(
                title=_("No History"),
                icon_name="document-open-recent-symbolic",
                description=_("Your created networks will appear here."),
            )
            self.history_list_box.append(status)
            return

        try:
            with open(history_file, "r") as f:
                data = json.load(f)
                history = data.get("history", [])
        except:
            history = []

        if not history:
            status = Adw.StatusPage(
                title=_("History is Empty"), icon_name="document-open-recent-symbolic"
            )
            self.history_list_box.append(status)
            return

        for entry in reversed(history):
            raw_url = entry.get("api_url") or entry.get("web_ui", "")
            clean_domain = (
                raw_url.replace("http://", "").replace("https://", "").strip("/")
            )
            entry_id = entry.get("id", "?")
            timestamp = entry.get("timestamp", "")

            group = Adw.PreferencesGroup()
            group.set_title(f"ID: {entry_id} - {clean_domain}")
            group.set_description(timestamp)

            # Management Buttons in the Header Suffix
            header_box = Gtk.Box(spacing=6)

            btn_reconnect = Gtk.Button()
            btn_reconnect.set_child(
                create_icon_widget(
                    "network-vpn-symbolic", size=16, css_class=["blue-icon"]
                )
            )
            btn_reconnect.add_css_class("flat")
            btn_reconnect.add_css_class("suggested-action")
            btn_reconnect.set_tooltip_text(_("Reconnect"))
            btn_reconnect.connect(
                "clicked", lambda b, e=entry: self.reconnect_from_history(e)
            )
            header_box.append(btn_reconnect)

            btn_save = Gtk.Button()
            btn_save.set_child(create_icon_widget("document-save-symbolic", size=16))
            btn_save.add_css_class("flat")
            btn_save.set_tooltip_text(_("Save to TXT"))
            btn_save.connect("clicked", lambda b, e=entry: self.save_entry_to_txt(e))
            header_box.append(btn_save)

            btn_delete = Gtk.Button()
            btn_delete.set_child(create_icon_widget("user-trash-symbolic", size=16))
            btn_delete.add_css_class("flat")
            btn_delete.add_css_class("destructive-action")
            btn_delete.set_tooltip_text(_("Delete from history"))
            btn_delete.connect(
                "clicked", lambda b, e=entry: self.confirm_delete_history(e)
            )
            header_box.append(btn_delete)

            group.set_header_suffix(header_box)

            # Domain Row
            domain_row = Adw.ActionRow(title=_("Domain"), subtitle=clean_domain)
            domain_row.add_prefix(
                create_icon_widget("network-server-symbolic", size=16)
            )

            btn_copy_dom = Gtk.Button()
            btn_copy_dom.set_child(create_icon_widget("edit-copy-symbolic", size=16))
            btn_copy_dom.add_css_class("flat")
            btn_copy_dom.set_tooltip_text(_("Copy Domain"))
            btn_copy_dom.connect(
                "clicked", lambda b, v=clean_domain: self._copy_to_clipboard(v)
            )
            domain_row.add_suffix(btn_copy_dom)
            group.add(domain_row)

            # Masked row helper
            def add_masked_row(grp, title, value, icon):
                masked_val = "••••••••••••••••"
                row = Adw.ActionRow(title=title, subtitle=masked_val)
                row.add_prefix(create_icon_widget(icon, size=16))

                btn_view = Gtk.Button()
                btn_view.set_child(create_icon_widget("view-reveal-symbolic", size=16))
                btn_view.add_css_class("flat")
                btn_view.set_valign(Gtk.Align.CENTER)

                def toggle_view(btn, r, val):
                    is_masked = r.get_subtitle() == "••••••••••••••••"
                    r.set_subtitle(val if is_masked else "••••••••••••••••")
                    btn.set_child(
                        create_icon_widget(
                            "view-reveal-symbolic"
                            if not is_masked
                            else "view-conceal-symbolic",
                            size=16,
                        )
                    )

                btn_view.connect("clicked", toggle_view, row, value)
                row.add_suffix(btn_view)

                btn_copy = Gtk.Button()
                btn_copy.set_child(create_icon_widget("edit-copy-symbolic", size=16))
                btn_copy.add_css_class("flat")
                btn_copy.set_valign(Gtk.Align.CENTER)
                btn_copy.connect(
                    "clicked", lambda b, v=value: self._copy_to_clipboard(v)
                )
                row.add_suffix(btn_copy)

                grp.add(row)

            add_masked_row(
                group,
                _("API Key"),
                entry.get("api_key", ""),
                "dialog-password-symbolic",
            )
            add_masked_row(
                group,
                _("Auth Key (Friends)"),
                entry.get("auth_key", ""),
                "key-symbolic",
            )

            self.history_list_box.append(group)

    def reconnect_from_history(self, entry):
        """Auto-fill connection form and trigger connection"""
        domain = (
            entry.get("api_url", "")
            .replace("http://", "")
            .replace("https://", "")
            .split("/")[0]
        )
        auth_key = entry.get("auth_key", "")

        if not domain or not auth_key:
            self.main_window.show_toast(_("Incomplete data in history"))
            return

        self.entry_connect_domain.set_text(domain)
        self.entry_auth_key.set_text(auth_key)
        self.connect_stack.set_visible_child_name("connection")
        self.on_connect_clicked(None)

    def save_entry_to_txt(self, entry):
        """Save access info to a .txt file for sharing"""
        dialog = Gtk.FileDialog(title=_("Save Network Information"))
        dialog.set_initial_name(f"network_info_{entry.get('id')}.txt")

        def on_save_response(dialog, result):
            try:
                file = dialog.save_finish(result)
                if file:
                    path = file.get_path()
                    content = (
                        f"ACCESS INFORMATION (ID: {entry.get('id')})\n"
                        f"Date: {entry.get('timestamp')}\n"
                        f"-----------------------------------\n"
                        f"Web Interface: {entry.get('web_ui')}\n"
                        f"API URL: {entry.get('api_url')}\n"
                        f"Public IP: {entry.get('public_ip')}\n"
                        f"Local IP: {entry.get('local_ip')}\n\n"
                        f"CREDENTIALS\n"
                        f"API Key: {entry.get('api_key')}\n"
                        f"Auth Key (Friends): {entry.get('auth_key')}\n"
                    )
                    with open(path, "w") as f:
                        f.write(content)
                    self.main_window.show_toast(_("File saved!"))
            except Exception as e:
                self.main_window.show_toast(_("Error saving: {}").format(e))

        dialog.save(self.main_window, None, on_save_response)

    def confirm_delete_history(self, entry):
        """Confirm before removing history entry"""
        dialog = Adw.MessageDialog(
            transient_for=self.main_window,
            heading=_("Delete History?"),
            body=_("Are you sure you want to remove ID {} from history?").format(
                entry.get("id")
            ),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(dlg, response):
            if response == "delete":
                self.delete_history_id(entry.get("id"))

        dialog.connect("response", on_response)
        dialog.present()

    def delete_history_id(self, entry_id):
        """Remove entry from JSON history"""
        config_dir = os.path.expanduser("~/.config/big-remoteplay/private_network")
        history_file = os.path.join(config_dir, "private_network.json")

        try:
            with open(history_file, "r") as f:
                data = json.load(f)
                history = data.get("history", [])

            new_history = [e for e in history if e.get("id") != entry_id]

            with open(history_file, "w") as f:
                json.dump({"history": new_history}, f, indent=4)

            self.refresh_history_ui()
            self.main_window.show_toast(_("Deleted from history"))
        except Exception as e:
            self.main_window.show_toast(_("Error deleting: {}").format(e))

    # ─────────────────────────────────────────────
    #  CALLBACKS & HELPERS
    # ─────────────────────────────────────────────

    def on_connect_stack_changed(self, stack, param):
        name = stack.get_visible_child_name()
        if name == "network":
            self.refresh_peers()
        elif name == "history":
            self.refresh_history_ui()

    def _on_domain_changed(self, entry):
        domain = entry.get_text()
        app = self.main_window.get_application()
        if hasattr(app, "config"):
            app.config.set("private_network_domain", domain)

    def _copy_to_clipboard(self, text):
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(text)
        self.main_window.show_toast(_("Copied: {}").format(text))

    def _on_key_changed(self, entry):
        app = self.main_window.get_application()
        if hasattr(app, "config"):
            app.config.set("private_network_key", entry.get_text())

    def _start_worker(self):
        if self._worker_running:
            return
        self._worker_running = True
        self._worker_thread = threading.Thread(target=self._status_worker, daemon=True)
        self._worker_thread.start()

    def _stop_worker(self):
        self._worker_running = False

    def _status_worker(self):
        while self._worker_running:
            if self.mode == "connect":
                if (
                    hasattr(self, "connect_stack")
                    and self.connect_stack.get_visible_child_name() == "network"
                ):
                    self._get_tailscale_status()

            for _ in range(30):
                if not self._worker_running:
                    break
                time.sleep(0.1)

    def refresh_peers(self):
        threading.Thread(target=self._get_tailscale_status, daemon=True).start()
        return True

    def _get_tailscale_status(self):
        if hasattr(self, "_fetching_status") and self._fetching_status:
            return
        self._fetching_status = True

        try:
            ts_path = shutil.which("tailscale") or "/usr/bin/tailscale"
            if not os.path.exists(ts_path):
                self._fetching_status = False
                return

            result = subprocess.run([ts_path, "status"], capture_output=True, text=True)
            if result.returncode != 0:
                self._fetching_status = False
                return

            nodes = []
            for line in result.stdout.splitlines():
                if not line or line.startswith("IP"):
                    continue

                parts = line.split()
                if len(parts) >= 4:
                    ip = parts[0]
                    host = parts[1]
                    user = parts[2]
                    os_sys = parts[3]
                    status_raw = " ".join(parts[4:]) if len(parts) > 4 else "-"

                    conn_type = "inactive"
                    conn_detail = "-"
                    online_status = "offline"

                    if "active" in status_raw.lower():
                        online_status = "online"
                    if "offline" in status_raw.lower():
                        online_status = "offline"

                    if "direct" in status_raw.lower():
                        conn_type = "direct"
                        match_addr = re.search(r"direct ([\d\.:]*)", status_raw)
                        conn_detail = match_addr.group(1) if match_addr else "direct"
                    elif "relay" in status_raw.lower():
                        conn_type = "relay"
                        match_relay = re.search(r'relay "([^"]*)"', status_raw)
                        conn_detail = match_relay.group(1) if match_relay else "relay"

                    tx_match = re.search(r"tx (\d+)", status_raw)
                    rx_match = re.search(r"rx (\d+)", status_raw)
                    tx = tx_match.group(1) if tx_match else "0"
                    rx = rx_match.group(1) if rx_match else "0"

                    traffic = f"↑{tx} ↓{rx}"
                    connection = f"{online_status} ({conn_type}: {conn_detail})"

                    nodes.append(
                        {
                            "ip": ip,
                            "host": host,
                            "user": user,
                            "os": os_sys,
                            "connection": connection,
                            "traffic": traffic,
                            "ping": "...",
                        }
                    )

            ui_data = []
            for n in nodes:
                ui_data.append(
                    (
                        n["ip"],
                        n["host"],
                        n["user"],
                        n["os"],
                        n["connection"],
                        n["traffic"],
                        n["ping"],
                    )
                )

            GLib.idle_add(self._update_peers_ui, ui_data)

            def do_ping_and_update(idx, node):
                ip = node["ip"]
                if not ip or ip == "-":
                    return

                ping_val = "-"
                try:
                    res = subprocess.run(
                        ["ping", "-c", "2", "-W", "1", "-n", ip],
                        capture_output=True,
                        text=True,
                    )
                    if res.returncode == 0:
                        summary_match = re.search(
                            r"min/avg/max/mdev\s*=\s*[\d.]+/([\d.]+)/", res.stdout
                        )
                        if summary_match:
                            ping_val = f"{summary_match.group(1)} ms"
                        else:
                            time_match = re.search(r"time=([\d.,]+)\s*ms", res.stdout)
                            if time_match:
                                ping_val = f"{time_match.group(1).replace(',', '.')} ms"
                except:
                    pass

                GLib.idle_add(self._update_single_ping, idx, ping_val)

            for i, node in enumerate(nodes):
                self._ping_executor.submit(do_ping_and_update, i, node)

        except:
            pass
        finally:
            self._fetching_status = False

    def _update_single_ping(self, idx, ping_val):
        try:
            if idx < self.peers_model.iter_n_children():
                it = self.peers_model.get_iter_from_string(str(idx))
                if it:
                    self.peers_model.set_value(it, 6, ping_val)
        except:
            pass

    def _update_peers_ui(self, peers):
        self.peers_model.clear()
        for p in peers:
            self.peers_model.append(p)

    # ─────────────────────────────────────────────
    #  TERMINAL DIALOG & LOGGING
    # ─────────────────────────────────────────────

    def _open_terminal_dialog(self, context="create"):
        """Open Adw.Dialog (bottom-sheet) with live terminal output."""
        if self._terminal_dialog is not None:
            # Dialog already open, just present it
            try:
                self._terminal_dialog.present(self.main_window)
                return
            except:
                self._terminal_dialog = None

        # Build the dialog content
        tv = Adw.ToolbarView()

        hb = Adw.HeaderBar()
        title = _('Installation Log') if context == "create" else _('Connection Log')
        phases = self._install_phases if context == "create" else self._connect_phases
        phase_idx = self._install_phase if context == "create" else self._connect_phase
        
        hb.set_title_widget(Adw.WindowTitle.new(
            title,
            phases[min(phase_idx, len(phases) - 1)]
        ))
        tv.add_top_bar(hb)

        # Scrollable terminal
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_min_content_height(400)

        # Re-parent text_view: remove from old parent if any
        log_view = self.create_text_view if context == "create" else self.connect_log_view
        old_parent = log_view.get_parent()
        if old_parent is not None:
            if hasattr(old_parent, 'set_child'):
                old_parent.set_child(None)
            elif hasattr(old_parent, 'remove'):
                old_parent.remove(log_view)
        scroll.set_child(log_view)

        tv.set_content(scroll)

        # Use Adw.Dialog if available (libadwaita 1.5+), otherwise fallback to Adw.Window
        if hasattr(Adw, 'Dialog'):
            dialog = Adw.Dialog()
            dialog.set_title(_('Installation Log'))
            dialog.set_content_width(700)
            dialog.set_content_height(500)
            # Set bottom-sheet presentation if available
            if hasattr(dialog, 'set_presentation_mode'):
                dialog.set_presentation_mode(Adw.DialogPresentationMode.BOTTOM_SHEET)
            dialog.set_child(tv)
            dialog.connect('closed', self._on_terminal_dialog_closed)
            dialog.present(self.main_window)
            self._terminal_dialog = dialog
        else:
            dialog = Adw.Window(transient_for=self.main_window)
            dialog.set_modal(False)
            dialog.set_title(_('Installation Log'))
            dialog.set_default_size(700, 500)
            dialog.set_content(tv)
            dialog.connect('close-request', lambda w: self._on_terminal_dialog_closed_win(w))
            dialog.present()
            self._terminal_dialog = dialog

    def _on_terminal_dialog_closed(self, dialog):
        """Handle Adw.Dialog closed."""
        self._terminal_dialog = None

    def _on_terminal_dialog_closed_win(self, window):
        """Handle Adw.Window close."""
        self._terminal_dialog = None
        return False

    def _update_progress(self, phase_idx, text=None, context="create"):
        """Update progress bar fraction and status text."""
        if context == "create":
            self._install_phase = phase_idx
            total = len(self._install_phases)
            phase_text = text or self._install_phases[min(phase_idx, total - 1)]
        else:
            self._connect_phase = phase_idx
            total = len(self._connect_phases)
            phase_text = text or self._connect_phases[min(phase_idx, total - 1)]
            
        fraction = min(phase_idx / total, 1.0)
        GLib.idle_add(self._set_progress_ui, fraction, phase_text, context)

    def _set_progress_ui(self, fraction, status_text, context="create"):
        if context == "create":
            self.create_level_bar.set_value(fraction)
            self.create_progress_status.set_text(status_text)
            self.create_progress_percent.set_text(f'{int(fraction * 100)}%')
        else:
            self.connect_level_bar.set_value(fraction)
            self.connect_progress_status.set_text(status_text)
            self.connect_progress_percent.set_text(f'{int(fraction * 100)}%')
        # Also update dialog header if open
        if self._terminal_dialog is not None:
            try:
                if hasattr(Adw, 'Dialog') and isinstance(self._terminal_dialog, Adw.Dialog):
                    child = self._terminal_dialog.get_child()
                else:
                    child = self._terminal_dialog.get_content()
                if child and hasattr(child, 'get_top_bars'):
                    pass  # HeaderBar title updates are complex here
            except:
                pass

    def _detect_install_phase(self, clean_line):
        """Detect current installation phase from script output."""
        lower = clean_line.lower()
        if any(k in lower for k in ['docker', 'container', 'pulling']):
            return 1
        elif any(k in lower for k in ['headscale', 'headplane']):
            return 2
        elif any(k in lower for k in ['caddy', 'reverse proxy', 'caddyfile']):
            return 3
        elif any(k in lower for k in ['dns', 'cloudflare', 'zone', 'record']):
            return 4
        elif any(k in lower for k in ['api key', 'auth key', 'preauthkey', 'chave']):
            return 5
        elif any(k in lower for k in ['finaliz', 'concluí', 'success', 'complete', 'pronto']):
            return 6
        return None

    def _detect_connect_phase(self, clean_line):
        """Detect current connection phase from script output."""
        lower = clean_line.lower()
        if any(k in lower for k in ['tailscale', 'dnf', 'apt', 'install']):
            return 2
        elif any(k in lower for k in ['login', 'up', 'auth']):
            return 3
        elif any(k in lower for k in ['verif', 'ping', 'status']):
            return 4
        elif any(k in lower for k in ['success', 'concluí', 'pronto', 'established']):
            return 5
        return None

    def log(self, text, view=None):
        if view is None:
            view = self.create_text_view
        GLib.idle_add(self._log_idle, text, view)

    def _apply_ansi_tags(self, buffer, text):
        ansi_escape = re.compile(r"(\x1b\[[0-9;]*[mK])")
        parts = ansi_escape.split(text)

        current_tags = []
        for part in parts:
            if part.startswith("\x1b["):
                if part == "\x1b[0m":
                    current_tags = []
                elif "0;32" in part:
                    current_tags = ["green"]
                elif "0;34" in part:
                    current_tags = ["blue"]
                elif "1;33" in part:
                    current_tags = ["yellow"]
                elif "0;31" in part:
                    current_tags = ["red"]
                elif "0;36" in part:
                    current_tags = ["cyan"]
                elif "1;" in part:
                    current_tags.append("bold")
            else:
                if part:
                    buffer.insert_with_tags_by_name(
                        buffer.get_end_iter(), part, *current_tags
                    )

    def _log_idle(self, text, view):
        buf = view.get_buffer()
        self._apply_ansi_tags(buf, text + "\n")
        mark = buf.get_insert()
        view.scroll_to_mark(mark, 0.0, True, 0.5, 1.0)

    # ─────────────────────────────────────────────
    #  INSTALLATION
    # ─────────────────────────────────────────────

    def run_install(self):
        self.btn_install.set_sensitive(False)
        self.install_spinner.set_visible(True)
        self.install_spinner.start()
        self.install_label.set_label(_("Installing..."))

        # Show progress area and reset
        self.create_progress_box.set_visible(True)
        self.create_btn_info.set_visible(False)
        self._install_phase = 0
        self.create_level_bar.set_value(0.0)
        self.create_progress_status.set_text(self._install_phases[0])
        self.create_progress_percent.set_text('0%')
        self.create_text_view.get_buffer().set_text("")

        domain = self.entry_domain.get_text().strip()
        zone = self.entry_zone.get_text().strip()
        token = self.entry_token.get_text().strip()

        def thread_func():
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            script_path = os.path.join(
                base_dir, "scripts", "create-network_headscale.sh"
            )
            if not os.path.exists(script_path):
                script_path = "/usr/share/big-remote-play/scripts/create-network_headscale.sh"

            cmd = ["bigsudo", script_path]
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            inputs = ["1\n", f"{domain}\n", f"{zone}\n", f"{token}\n", "n\n"]
            for s in inputs:
                process.stdin.write(s)
                process.stdin.flush()

            self.install_data = {}
            while True:
                line = process.stdout.readline()
                if not line:
                    break
                clean_line = re.sub(r"\x1b\[[0-9;]*[mK]", "", line).strip()

                # Detect phase and update progress
                detected = self._detect_install_phase(clean_line)
                if detected is not None and detected > self._install_phase:
                    self._update_progress(detected)

                # Capture information (support both PT-BR and EN script output)
                if "Interface Web:" in clean_line or "Web Interface:" in clean_line:
                    self.install_data["web_ui"] = clean_line.split(":", 1)[1].strip()
                elif "URL da API:" in clean_line or "API URL:" in clean_line:
                    self.install_data["api_url"] = clean_line.split(":", 1)[1].strip()
                elif (
                    "Seu IP Público:" in clean_line
                    or "Public IP:" in clean_line
                    or "Your Public IP:" in clean_line
                ):
                    self.install_data["public_ip"] = clean_line.split(":", 1)[1].strip()
                elif (
                    "IP Local do Servidor:" in clean_line
                    or "Local IP:" in clean_line
                    or "Server Local IP:" in clean_line
                ):
                    self.install_data["local_ip"] = clean_line.split(":", 1)[1].strip()
                elif (
                    "API Key (para UI):" in clean_line
                    or "API Key (UI):" in clean_line
                    or "API Key:" in clean_line
                ):
                    self.install_data["api_key"] = clean_line.split(":", 1)[1].strip()
                elif (
                    "Chave para Amigos:" in clean_line
                    or "Auth Key (Friends):" in clean_line
                    or "Friends Key:" in clean_line
                ):
                    self.install_data["auth_key"] = clean_line.split(":", 1)[1].strip()

                self.log(line.strip())
            process.wait()
            GLib.idle_add(self.on_install_finished, process.returncode)

        threading.Thread(target=thread_func, daemon=True).start()

    def on_install_finished(self, code):
        self.install_spinner.stop()
        self.install_spinner.set_visible(False)

        if code == 0:
            self._update_progress(len(self._install_phases), _('✅ Installation complete!'), "create")
            self.create_level_bar.set_value(1.0)
            self.create_progress_percent.set_text('100%')
            self.create_btn_info.set_visible(True)
            self.main_window.show_toast(_("Success!"))
            self.install_label.set_label(_("Install Server"))
            self.show_success_dialog()
        else:
            self._update_progress(self._install_phase, _('❌ Installation failed'), "create")
            self.main_window.show_toast(_("Installation failed"))
            self.install_label.set_label(_("Try Again"))

        self.btn_install.set_sensitive(True)

    def show_success_dialog(self):
        """Show the access information dialog with proper sizing."""
        content = AccessInfoWidget(self.install_data, self.save_history, self.main_window)

        if hasattr(Adw, 'Dialog'):
            dialog = Adw.Dialog()
            dialog.set_title(_('Network Established'))
            dialog.set_content_width(550)
            dialog.set_content_height(600)
            dialog.set_child(content)
            dialog.present(self.main_window)
            self.current_dialog = dialog
        else:
            dialog = Adw.Window(transient_for=self.main_window)
            dialog.set_modal(True)
            dialog.set_title(_('Network Established'))
            dialog.set_default_size(550, 600)

            tv = Adw.ToolbarView()
            hb = Adw.HeaderBar()
            tv.add_top_bar(hb)
            tv.set_content(content)
            dialog.set_content(tv)
            dialog.present()
            self.current_dialog = dialog

    def save_history(self, data):
        """Save installation to private_network.json"""
        if hasattr(self, "current_dialog"):
            if hasattr(self.current_dialog, "close"):
                self.current_dialog.close()
            else:
                self.current_dialog.destroy()

        config_dir = os.path.expanduser("~/.config/big-remoteplay/private_network")
        os.makedirs(config_dir, exist_ok=True)
        history_file = os.path.join(config_dir, "private_network.json")

        history = []
        if os.path.exists(history_file):
            try:
                with open(history_file, "r") as f:
                    content = json.load(f)
                    history = content.get("history", [])
            except:
                pass

        new_id = 1
        if history:
            ids = [h.get("id", 0) for h in history]
            new_id = max(ids) + 1

        entry = data.copy()
        entry["id"] = new_id
        entry["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
        history.append(entry)

        try:
            with open(history_file, "w") as f:
                json.dump({"history": history}, f, indent=4)
            self.main_window.show_toast(_("Configuration saved to history"))
            self.refresh_history_ui()
        except Exception as e:
            self.main_window.show_toast(_("Error saving history: {}").format(e))

    def on_connect_clicked(self, btn):
        domain = self.entry_connect_domain.get_text().strip()
        key = self.entry_auth_key.get_text().strip()
        if not domain or not key:
            self.main_window.show_toast(_("Fill all fields"))
            return

        self.btn_connect.set_sensitive(False)
        self.connect_spinner.set_visible(True)
        self.connect_spinner.start()
        self.connect_label.set_label(_("Connecting..."))

        # Reset Progress UI
        self.connect_progress_box.set_visible(True)
        self._connect_phase = 0
        self.connect_level_bar.set_value(0.0)
        self.connect_progress_status.set_text(self._connect_phases[0])
        self.connect_progress_percent.set_text('0%')
        self.connect_log_view.get_buffer().set_text("")

        def thread_func():
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            script_path = os.path.join(base_dir, "scripts", "create-network_headscale.sh")
            if not os.path.exists(script_path):
                script_path = "/usr/share/big-remote-play/scripts/create-network_headscale.sh"
            
            cmd = ["bigsudo", script_path]
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            
            # Send inputs: option 2 (connect), domain, key, and 'n' for any further prompt
            inputs = ["2\n", f"{domain}\n", f"{key}\n", "n\n"]
            for s in inputs:
                process.stdin.write(s)
                process.stdin.flush()

            while True:
                line = process.stdout.readline()
                if not line:
                    break
                clean_line = line.strip()
                if clean_line:
                    self.log(clean_line, view=self.connect_log_view)
                    
                    # Detect phase
                    new_phase = self._detect_connect_phase(clean_line)
                    if new_phase is not None and new_phase > self._connect_phase:
                        self._update_progress(new_phase, context="connect")

            code = process.wait()
            GLib.idle_add(self._on_connect_finished, code)

        threading.Thread(target=thread_func, daemon=True).start()

    def _on_connect_finished(self, code):
        self.connect_spinner.stop()
        self.connect_spinner.set_visible(False)
        self.btn_connect.set_sensitive(True)

        if code == 0:
            self._update_progress(len(self._connect_phases), _('✅ Connection established!'), "connect")
            self.connect_level_bar.set_value(1.0)
            self.connect_progress_percent.set_text('100%')
            self.main_window.show_toast(_("Connected successfully!"))
            self.connect_label.set_label(_("Establish Connection"))
            # Switch to status tab after a small delay
            GLib.timeout_add(1500, lambda: self.connect_stack.set_visible_child_name("network"))
        else:
            self._update_progress(self._connect_phase, _('❌ Connection failed'), "connect")
            self.main_window.show_toast(_("Connection failed"))
            self.connect_label.set_label(_("Try Again"))
