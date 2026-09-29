from __future__ import annotations

from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gio, Pango  # type: ignore
import logging

_log = logging.getLogger("big-remoteplay")

import threading
import json
import os
from .host_view import HostView
from .guest_view import GuestView
from .installer_window import InstallerWindow
from .components import name_icon_button, action_row, boxed_rows, icon_tile
from big_remote_play.utils.config import Config
from big_remote_play.utils.network import NetworkDiscovery
from big_remote_play.utils.system_check import SystemCheck
from big_remote_play.utils.icons import create_icon_widget
from big_remote_play.utils.i18n import _
from big_remote_play.utils.secure_io import secure_write_text
from big_remote_play import paths
import subprocess
import shutil


# ─── VPN Provider Config ───────────────────────────────────────────────────
VPN_CONFIG_FILE = str(paths.CONFIG_DIR / "vpn_choice.json")

DEFAULT_WINDOW_WIDTH = 1100
DEFAULT_WINDOW_HEIGHT = 720
MIN_SAVED_WINDOW_WIDTH = 360
MIN_SAVED_WINDOW_HEIGHT = 480
MAX_SAVED_WINDOW_WIDTH = 5120
MAX_SAVED_WINDOW_HEIGHT = 3200

VPN_PROVIDERS = {
    "headscale": {
        "name": "Headscale",
        "icon": "brp-headscale-symbolic",
        "description": _("Use your own Headscale server. Advanced setup."),
        "color": "#3584e4",
    },
    "tailscale": {
        "name": "Tailscale",
        "icon": "brp-tailscale-symbolic",
        "description": _("Recommended for most people. Sign in with your browser."),
        "color": "#26a269",
    },
    "zerotier": {
        "name": "ZeroTier",
        "icon": "brp-zerotier-symbolic",
        "description": _("Join an existing network with its Network ID."),
        "color": "#e5a50a",
    },
}

# Service Definitions
SERVICE_METADATA = {
    "sunshine": {
        "name": "SUNSHINE",
        "full_name": _("Sunshine Game Stream Host"),
        "description": _("Sends your game to the other PC."),
        "icon": "brp-host-symbolic",
        "type": "service",
        "unit": "sunshine.service",
        "user": True,
    },
    "moonlight": {
        "name": "MOONLIGHT",
        "full_name": _("Moonlight Game Stream Client"),
        "description": _("Receives the game from the other PC."),
        "icon": "brp-client-symbolic",
        "type": "app",
        "bin": "moonlight-qt",
    },
    "docker": {
        "name": "DOCKER",
        "full_name": _("Docker Engine"),
        "description": _("Only needed to run your own Headscale server."),
        "icon": "brp-service-symbolic",
        "type": "service",
        "unit": "docker.service",
        "user": False,
    },
    "tailscale": {
        "name": "TAILSCALE",
        "full_name": _("Tailscale"),
        "description": _("Connects distant PCs as if they were in the same house."),
        "icon": "brp-tailscale-symbolic",
        "type": "service",
        "unit": "tailscaled.service",
        "user": False,
    },
    "zerotier": {
        "name": "ZEROTIER",
        "full_name": _("ZeroTier"),
        "description": _("Another way to connect distant PCs."),
        "icon": "brp-zerotier-symbolic",
        "type": "service",
        "unit": "zerotier-one.service",
        "user": False,
    },
}

# Home remains a stable starting point; direct task destinations are shortcuts.
BASE_NAVIGATION_PAGES = {
    "host": {"name": _("Share"), "icon": "brp-host-symbolic", "description": _("Run the game on this PC")},
    "guest": {"name": _("Connect"), "icon": "brp-client-symbolic", "description": _("Play from another PC")},
    "vpn_selector": {"name": _("Play over the internet"), "icon": "brp-network-private-symbolic", "description": _("For PCs in different houses")},
}
WELCOME_NAVIGATION_PAGE = {"welcome": {"name": _("Home"), "icon": "brp-go-home-symbolic", "description": _("Home Page")}}


def load_vpn_choice():
    """Load saved VPN provider choice. Returns None if not set."""
    try:
        if os.path.exists(VPN_CONFIG_FILE):
            with open(VPN_CONFIG_FILE, "r") as f:
                data = json.load(f)
                choice = data.get("vpn_provider")
                if choice in VPN_PROVIDERS:
                    return choice
    except Exception:
        pass
    return None


def save_vpn_choice(provider_id):
    """Persist VPN provider choice."""
    secure_write_text(VPN_CONFIG_FILE, json.dumps({"vpn_provider": provider_id}, indent=2))


class MainWindow(Adw.ApplicationWindow):
    """Main window with modern side navigation"""

    def __init__(self, config: Config | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        manager = Adw.StyleManager.get_default()

        def sync_contrast(*_args):
            if manager.get_high_contrast():
                self.add_css_class("high-contrast")
            else:
                self.remove_css_class("high-contrast")

        manager.connect_object("notify::high-contrast", sync_contrast, self)
        sync_contrast()

        self.set_title("Big Remote Play")
        self.add_css_class("brp-window")
        self.config = config or Config()
        self._restore_window_size()

        self.system_check = SystemCheck()
        self.network = NetworkDiscovery()

        # Current State
        self.current_page = "welcome"
        self._network_return_page = "guest"
        self._polling_status = False
        self._vpn_choice = load_vpn_choice()  # None if not yet chosen
        self._vpn_add_account = False
        self.network_advanced_mode = bool(self.config.get("network_advanced_mode", False))
        self._nav_page_by_row: dict[Gtk.ListBoxRow, str] = {}
        self._service_by_row: dict[Gtk.Widget, str] = {}
        self._status_dots: dict[str, Gtk.Widget] = {}
        self._status_rows: dict[str, Adw.ActionRow] = {}
        # Installed and running are probed by separate passes; a row shows one
        # combined state, so both halves are kept until they can be merged.
        self._service_installed: dict[str, bool] = {}
        self._service_running: dict[str, bool] = {}
        self._service_full_name: dict[str, str] = {}
        self._role_card_ui: dict[str, dict[str, Gtk.Widget]] = {}

        self._install_window_actions()
        self.setup_ui()
        # Reopening must not remove the guide after a task was chosen once.
        self.navigate_to("welcome")
        self._status_timer_id = None
        self.check_system()
        # After the window is up: never on the startup critical path.
        GLib.timeout_add_seconds(2, self._refresh_home_network_status)
        GLib.timeout_add_seconds(3, self._secure_legacy_network_files)

        # Connect close signal
        self.connect("close-request", self.on_close_request)

    @staticmethod
    def _coerce_window_dimension(
        saved_dimension: object,
        fallback_dimension: int,
        minimum_dimension: int,
        maximum_dimension: int,
    ) -> int:
        if isinstance(saved_dimension, bool) or not isinstance(saved_dimension, (int, float, str)):
            return fallback_dimension
        try:
            dimension = int(saved_dimension)
        except (TypeError, ValueError):
            return fallback_dimension
        return max(minimum_dimension, min(dimension, maximum_dimension))

    def _get_saved_window_config(self) -> dict[str, object]:
        saved_window_config = self.config.get("window", {})
        if isinstance(saved_window_config, dict):
            return dict(saved_window_config)
        return {}

    def _restore_window_size(self) -> None:
        saved_window_config = self._get_saved_window_config()
        width = self._coerce_window_dimension(
            saved_window_config.get("width"),
            DEFAULT_WINDOW_WIDTH,
            MIN_SAVED_WINDOW_WIDTH,
            MAX_SAVED_WINDOW_WIDTH,
        )
        height = self._coerce_window_dimension(
            saved_window_config.get("height"),
            DEFAULT_WINDOW_HEIGHT,
            MIN_SAVED_WINDOW_HEIGHT,
            MAX_SAVED_WINDOW_HEIGHT,
        )
        self.set_default_size(width, height)

    def _save_window_size(self) -> None:
        width = self.get_width()
        height = self.get_height()
        if width <= 0 or height <= 0:
            return

        saved_window_config = self._get_saved_window_config()
        saved_window_config["width"] = self._coerce_window_dimension(
            width,
            DEFAULT_WINDOW_WIDTH,
            MIN_SAVED_WINDOW_WIDTH,
            MAX_SAVED_WINDOW_WIDTH,
        )
        saved_window_config["height"] = self._coerce_window_dimension(
            height,
            DEFAULT_WINDOW_HEIGHT,
            MIN_SAVED_WINDOW_HEIGHT,
            MAX_SAVED_WINDOW_HEIGHT,
        )
        self.config.set("window", saved_window_config)

    def _shutdown_resources(self) -> None:
        """Flush local settings once, including application-menu Quit."""
        if getattr(self, "_resources_closed", False):
            return
        self._resources_closed = True
        try:
            self._save_window_size()
        except Exception:
            _log.exception("Could not save window size on shutdown")
        if self._status_timer_id:
            GLib.source_remove(self._status_timer_id)
            self._status_timer_id = None
        # One adapter failing cleanup must not prevent the other from flushing
        # its pending settings or cancelling in-flight UI operations.
        for name in ("host_view", "guest_view"):
            view = getattr(self, name, None)
            if view is not None:
                try:
                    view.cleanup()
                except Exception:
                    _log.exception("Could not clean up %s", name)

    def on_close_request(self, _window: Adw.ApplicationWindow) -> bool:
        self._shutdown_resources()
        return False

    def setup_ui(self):
        self.set_size_request(360, 480)
        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)
        self.split_view = Adw.NavigationSplitView()
        self.toast_overlay.set_child(self.split_view)
        self.setup_sidebar()
        self.setup_content()
        self.split_view.set_sidebar_width_fraction(0.23)
        self.split_view.set_min_sidebar_width(248)
        self.split_view.set_max_sidebar_width(300)
        # A compact launch must reveal the selected page, not strand the user in
        # the navigation sidebar. In wide mode this property is inert.
        self.split_view.set_show_content(True)

        def add_compact_layout_setters(breakpoint: Adw.Breakpoint) -> None:
            breakpoint.add_setter(self.welcome_cards_box, "orientation", Gtk.Orientation.VERTICAL)
            breakpoint.add_setter(self.home_hero_content, "orientation", Gtk.Orientation.VERTICAL)
            breakpoint.add_setter(self.home_device_flow, "halign", Gtk.Align.START)
            breakpoint.add_setter(self.home_benefits, "orientation", Gtk.Orientation.VERTICAL)
            for role in self._role_card_ui.values():
                breakpoint.add_setter(role["content"], "orientation", Gtk.Orientation.HORIZONTAL)
            breakpoint.add_setter(self.host_view.overview_hero, "orientation", Gtk.Orientation.VERTICAL)
            breakpoint.add_setter(self.host_view.overview_hero_actions, "halign", Gtk.Align.FILL)
            breakpoint.add_setter(self.host_view.overview_start_button, "halign", Gtk.Align.FILL)

        # ApplicationWindow activates one matching breakpoint at a time. Repeat
        # the compact composition in the narrower breakpoint so it inherits the
        # same card/hero layout instead of reverting to the desktop arrangement.
        compact = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 880sp"))
        add_compact_layout_setters(compact)
        # Large text can exhaust the tab labels' width while the sidebar still
        # fits. Move tabs before ellipsizing their task names, not only when
        # the whole split view collapses.
        compact.connect("apply", self._on_compact_header_apply)
        compact.connect("unapply", self._on_compact_header_unapply)
        self.add_breakpoint(compact)

        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720sp"))
        add_compact_layout_setters(narrow)
        narrow.add_setter(self.split_view, "collapsed", True)
        narrow.add_setter(self.welcome_main_box, "margin-start", 12)
        narrow.add_setter(self.welcome_main_box, "margin-end", 12)
        # At phone-like widths the explanation remains in words and the two
        # decisions move above decorative/product-benefit visuals.
        narrow.add_setter(self.home_device_flow, "visible", False)
        narrow.add_setter(self.home_benefits, "visible", False)
        for margin in ("margin-top", "margin-bottom", "margin-start", "margin-end"):
            narrow.add_setter(self.guest_view.content_clamp, margin, 12)
        narrow.connect("apply", self._on_compact_header_apply)
        narrow.connect("unapply", self._on_compact_header_unapply)
        self.add_breakpoint(narrow)

    def _install_window_actions(self):
        nav_action = Gio.SimpleAction.new("navigate", GLib.VariantType.new("s"))
        nav_action.connect("activate", self._on_navigate_action)
        self.add_action(nav_action)

    def _on_navigate_action(self, _action, parameter):
        if parameter is None:
            return
        self.navigate_to(parameter.get_string())

    def setup_sidebar(self):
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        # Adwaita header bars carry the window title alone. The app icon is the
        # shell's job (window list, decoration), so drawing the logo here only
        # duplicated it.
        header.set_title_widget(Adw.WindowTitle(title="Big Remote Play"))
        toolbar.add_top_bar(header)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main.add_css_class("sidebar-content")
        main.set_vexpand(True)
        main.set_margin_top(8)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        self.nav_list = Gtk.ListBox()
        self.nav_list.set_vexpand(False)
        self.nav_list.set_margin_start(8)
        self.nav_list.set_margin_end(8)
        self.nav_list.add_css_class("navigation-sidebar")
        self.nav_list.connect("row-selected", self.on_nav_selected)
        self._refresh_nav_list()

        # Navigation and status scroll as one resilient column. The expanding
        # spacer keeps system readiness anchored near the bottom on tall screens.
        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        main.append(self.nav_list)
        main.append(spacer)
        main.append(self.create_status_footer())
        scroll.set_child(main)
        toolbar.set_content(scroll)
        self.split_view.set_sidebar(Adw.NavigationPage.new(toolbar, _("Navigation")))

    def _navigation_pages(self) -> dict[str, dict]:
        """Keep Home reachable for beginners and returning users alike."""
        return {**WELCOME_NAVIGATION_PAGE, **BASE_NAVIGATION_PAGES}

    def _chosen_role(self) -> str | None:
        role = self.config.get("last_page")
        return role if role in self._ROLE_COMPONENTS else None

    def _remember_role(self, page_id: str) -> None:
        """Remember the task without removing the guided starting point."""
        if page_id not in self._ROLE_COMPONENTS or self.config.get("last_page") == page_id:
            return
        first_choice = self._chosen_role() is None
        self.config.set("last_page", page_id)
        if first_choice:
            self._refresh_nav_list(select=page_id)

    def _refresh_nav_list(self, select: str | None = None):
        while child := self.nav_list.get_first_child():
            self.nav_list.remove(child)
        self._nav_page_by_row.clear()
        for pid, info in self._navigation_pages().items():
            self.nav_list.append(self.create_nav_row(pid, info))
        self.nav_list.handler_block_by_func(self.on_nav_selected)
        try:
            for row, page in self._nav_page_by_row.items():
                if page == (select or self.current_page):
                    self.nav_list.select_row(row)
                    break
            else:
                self.nav_list.select_row(self.nav_list.get_first_child())
        finally:
            self.nav_list.handler_unblock_by_func(self.on_nav_selected)

    def create_nav_row(self, page_id: str, page_info: dict) -> Gtk.ListBoxRow:
        """Creates navigation row in sidebar"""
        row = Gtk.ListBoxRow()
        self._nav_page_by_row[row] = page_id
        row.set_focusable(True)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(6)
        box.set_margin_bottom(6)

        icon = create_icon_widget(page_info["icon"], size=20)
        box.append(icon)

        label = Gtk.Label(label=page_info["name"])
        label.set_halign(Gtk.Align.START)
        label.set_hexpand(True)
        label.set_wrap(True)
        label.set_xalign(0)
        box.append(label)

        # Badge showing selected VPN name
        if badge_text := page_info.get("badge"):
            badge = Gtk.Label(label=badge_text)
            badge.add_css_class("caption")
            badge.add_css_class("dim-label")
            badge.set_halign(Gtk.Align.END)
            box.append(badge)

        # The row itself is the control. A Gtk.Button inside would draw button
        # chrome and Adwaita's bold button text, which is not how a navigation
        # sidebar looks; the ListBox already owns hover, selection and keyboard
        # activation.
        row.set_child(box)
        row.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            [page_info["name"], page_info.get("description", "")],
        )
        return row

    def create_status_footer(self):
        """The services this path needs, each with its own state, shown directly.

        Only the services relevant to the chosen path appear (one or two), so a
        summary row would hide the answer behind a click without adding
        anything the rows do not already say in words."""
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        footer.set_margin_start(12)
        footer.set_margin_end(12)
        footer.set_margin_top(8)
        footer.set_margin_bottom(12)

        status_list = Gtk.ListBox()
        status_list.set_selection_mode(Gtk.SelectionMode.NONE)
        status_list.add_css_class("boxed-list")
        self._service_label: dict[str, str] = {}

        def add_status_row(label_text: str, service_id: str) -> None:
            meta = SERVICE_METADATA.get(service_id, {})
            row = Adw.ActionRow(title=label_text, subtitle=_("Checking..."))
            row.set_activatable(True)
            self._service_by_row[row] = service_id
            row.connect("activated", lambda _r, sid=service_id: self.on_service_clicked(sid))

            icon_frame = Gtk.Box()
            icon_frame.add_css_class("service-icon-frame")
            service_icon = create_icon_widget(meta.get("icon", "brp-service-symbolic"), size=18)
            service_icon.set_valign(Gtk.Align.CENTER)
            for margin in ("top", "bottom", "start", "end"):
                getattr(service_icon, f"set_margin_{margin}")(6)
            icon_frame.append(service_icon)
            row.add_prefix(icon_frame)

            dot = create_icon_widget("brp-media-record-symbolic", size=9, css_class=["status-dot"])
            dot.set_valign(Gtk.Align.CENTER)
            self._status_dots[service_id] = dot
            row.add_suffix(dot)

            description = meta.get("description", "")
            if description:
                row.set_tooltip_text(description)
            self._service_full_name[service_id] = meta.get("full_name", label_text)
            self._service_label[service_id] = label_text
            self._status_rows[service_id] = row
            status_list.append(row)

        add_status_row("Sunshine", "sunshine")
        add_status_row("Moonlight", "moonlight")
        add_status_row("Docker", "docker")
        add_status_row("Tailscale", "tailscale")
        add_status_row("ZeroTier", "zerotier")

        footer.append(status_list)
        self._filter_status_rows()
        return footer

    def _relevant_service_ids(self) -> list[str]:
        # Home is a doorway, not a component dashboard. A missing dependency is
        # explained on its task card; service details belong to the chosen page.
        role = self.current_page if self.current_page in ("host", "guest") else None
        relevant = ["sunshine" if role == "host" else "moonlight"] if role in ("host", "guest") else []
        # Network pages say the connection state in words on the page itself;
        # a daemon's "Running" beside "Turned off" would contradict it.
        return relevant

    def _filter_status_rows(self) -> None:
        """Show only the services needed by the selected path."""
        relevant = set(self._relevant_service_ids())
        for service_id, row in self._status_rows.items():
            row.set_visible(service_id in relevant)

    def _refresh_service_state(self, service_id: str) -> None:
        """Show one state per row: the row subtitle says it, the dot colours it.

        The dot used to carry "running" while a separate label carried
        "installed", so a stopped service read as a green "Installed" beside a
        red dot. Both now follow the same state, and the words are the carrier
        so the state survives without colour vision or with a screen reader."""
        installed = self._service_installed.get(service_id)
        if installed is None:
            return  # still checking; the row keeps its "checking" subtitle
        if not installed:
            text, dot_state = _("Missing"), "status-offline"
        elif self._service_running.get(service_id):
            text, dot_state = _("Running"), "status-online"
        else:
            text, dot_state = _("Stopped"), "status-idle"

        row = self._status_rows.get(service_id)
        if row:
            # The subtitle is the state: AT-SPI exposes it as a label inside the
            # row, so a screen reader reads "SUNSHINE, Stopped" off the row
            # itself and the dot only colours what the words already say.
            row.set_subtitle(text)

        dot = self._status_dots.get(service_id)
        if dot:
            for css in ("status-online", "status-idle", "status-offline"):
                dot.remove_css_class(css)
            dot.add_css_class(dot_state)

    def update_server_status(self, run_sun, run_moon, run_docker, run_tailscale, run_zt=False):
        for service_id, running in [("sunshine", run_sun), ("moonlight", run_moon), ("docker", run_docker), ("tailscale", run_tailscale), ("zerotier", run_zt)]:
            self._service_running[service_id] = running
            self._refresh_service_state(service_id)

    def update_dependency_ui(self, has_sun, has_moon, has_docker, has_tailscale, has_zt=False):
        status_items = [
            ("sunshine", "host", has_sun, "Sunshine"),
            ("moonlight", "guest", has_moon, "Moonlight"),
            ("docker", None, has_docker, "Docker"),
            ("tailscale", None, has_tailscale, "Tailscale"),
            ("zerotier", None, has_zt, "ZeroTier"),
        ]

        for service_id, role_id, installed, component_name in status_items:
            self._service_installed[service_id] = installed
            self._refresh_service_state(service_id)
            if role_id is not None:
                self._set_role_card_state(role_id, component_name, installed)

        # Missing software is explained in place instead of disabling the user's
        # first choices or interrupting startup with a modal dialog.
        self.host_card.set_sensitive(True)
        self.guest_card.set_sensitive(True)

    def setup_content(self) -> None:
        toolbar = Adw.ToolbarView()
        toolbar.set_top_bar_style(Adw.ToolbarStyle.FLAT)

        header = Adw.HeaderBar()
        header.set_centering_policy(Adw.CenteringPolicy.STRICT)
        header.pack_end(self._create_header_menu_button())
        self.home_back_button = Gtk.Button(icon_name="go-previous-symbolic", visible=False)
        name_icon_button(self.home_back_button, _("Back to Home"))
        self.home_back_button.add_css_class("flat")
        self.home_back_button.connect("clicked", lambda _button: self.home_navigation.pop())
        header.pack_start(self.home_back_button)
        # Network sub-pages (sign-in, network details) return to the hub.
        self.network_back_button = Gtk.Button(icon_name="go-previous-symbolic", visible=False)
        name_icon_button(self.network_back_button, _("Back"), _("Back to Connect your devices"))
        self.network_back_button.add_css_class("flat")
        self.network_back_button.connect("clicked", lambda _button: self.navigate_to("vpn_selector"))
        header.pack_start(self.network_back_button)
        self.content_headerbar = header
        toolbar.add_top_bar(header)

        self.content_stack = Gtk.Stack()
        self.content_stack.set_hhomogeneous(False)
        self.content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.content_stack.set_transition_duration(180)
        self.content_stack.add_named(self.create_welcome_page(), "welcome")

        self.host_view = HostView()
        self.content_stack.add_named(self.host_view, "host")
        self.guest_view = GuestView()
        self.content_stack.add_named(self.guest_view, "guest")

        self.vpn_selector_page = self.create_vpn_selector_page()
        self.content_stack.add_named(self.vpn_selector_page, "vpn_selector")
        self.create_private_view = None
        self.connect_private_view = None

        # Keep every task switcher in the native headerbar.  On compact widths,
        # the same ViewStack moves to a ViewSwitcherBar at the bottom, following
        # the current libadwaita pattern without the deprecated ViewSwitcherTitle.
        self.content_title = Adw.WindowTitle()
        self.header_view_switcher = Adw.ViewSwitcher()
        self.header_view_switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        self.header_view_switcher.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Navigation")],
        )

        self.header_title_stack = Gtk.Stack()
        self.header_title_stack.set_hhomogeneous(False)
        self.header_title_stack.set_vhomogeneous(False)
        self.header_title_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.header_title_stack.set_transition_duration(120)
        self.header_title_stack.add_named(self.content_title, "title")
        self.header_title_stack.add_named(self.header_view_switcher, "switcher")

        # Connect is one page with one question, so it has no switcher: its
        # header carries the plain title like Home does.
        self.header_context_stacks: dict[str, Adw.ViewStack] = {
            "host": self.host_view.view_stack,
        }
        self.header_context_specs = {
            "host": (_("Share"), _("Run the game on this PC"), _("Sharing sections")),
        }

        header.set_title_widget(self.header_title_stack)
        self.compact_view_switcher = Adw.ViewSwitcherBar()
        self.compact_view_switcher.set_reveal(False)
        toolbar.add_bottom_bar(self.compact_view_switcher)
        self._header_context: str | None = None
        self._header_is_compact = False

        toolbar.set_content(self.content_stack)
        self.split_view.set_content(Adw.NavigationPage.new(toolbar, "Big Remote Play"))
        self._set_header_title(_("Home"), "Big Remote Play")

    def _create_header_menu_button(self) -> Gtk.MenuButton:
        menu_button = Gtk.MenuButton(icon_name="brp-open-menu-symbolic")
        menu_button.set_tooltip_text(_("Application menu"))
        menu_button.update_property([Gtk.AccessibleProperty.LABEL], [_("Application menu")])
        menu = Gio.Menu()
        # Appearance is one choice with three values: a radio section in the
        # menu shows the current one, instead of a window built for it.
        appearance = Gio.Menu()
        for label, value in ((_("Automatic"), "auto"), (_("Light"), "light"), (_("Dark"), "dark")):
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value("app.theme", GLib.Variant.new_string(value))
            appearance.append_item(item)
        menu.append_section(_("Appearance"), appearance)
        menu.append(_("Backup and restore"), "app.preferences")
        menu.append(_("About"), "app.about")
        menu_button.set_popover(Gtk.PopoverMenu.new_from_model(menu))
        return menu_button

    def _set_header_title(self, title: str, subtitle: str = "") -> None:
        self.content_title.set_title(title)
        self.content_title.set_subtitle(subtitle)
        self._set_header_context(None)

    def _set_header_context(
        self,
        context: str | None,
        *,
        title: str | None = None,
        subtitle: str | None = None,
    ) -> None:
        self._header_context = context
        if context is None:
            self.header_title_stack.set_visible_child_name("title")
            self.compact_view_switcher.set_reveal(False)
            return

        default_title, default_subtitle, accessible_label = self.header_context_specs[context]
        title = title or default_title
        subtitle = subtitle or default_subtitle
        stack = self.header_context_stacks[context]
        self.content_title.set_title(title)
        self.content_title.set_subtitle(subtitle)
        self.header_view_switcher.set_stack(stack)
        self.header_view_switcher.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            [accessible_label, subtitle],
        )
        self.compact_view_switcher.set_stack(stack)
        self.header_title_stack.set_visible_child_name("title" if self._header_is_compact else "switcher")
        self.compact_view_switcher.set_reveal(self._header_is_compact)

    def _on_compact_header_apply(self, *_args) -> None:
        self._header_is_compact = True
        self.content_headerbar.set_centering_policy(Adw.CenteringPolicy.LOOSE)
        if self._header_context is not None:
            self.header_title_stack.set_visible_child_name("title")
            self.compact_view_switcher.set_reveal(True)

    def _on_compact_header_unapply(self, *_args) -> None:
        self._header_is_compact = False
        self.content_headerbar.set_centering_policy(Adw.CenteringPolicy.STRICT)
        if self._header_context is not None:
            self.header_title_stack.set_visible_child_name("switcher")
        self.compact_view_switcher.set_reveal(False)

    # ─────────────────────────────────────────────────────────────────────────
    #  VPN SELECTOR PAGE
    # ─────────────────────────────────────────────────────────────────────────

    def create_vpn_selector_page(self):
        """“Connect your devices”: status, tasks, devices and Advanced mode."""
        from .remote_connection import RemoteConnectionPage

        self.remote_connection_page = RemoteConnectionPage(self)
        return self.remote_connection_page

    def set_network_advanced_mode(self, advanced: bool) -> None:
        """Remember the disclosure level; network pages follow it when reopened."""
        advanced = bool(advanced)
        if advanced == self.network_advanced_mode:
            return
        self.network_advanced_mode = advanced
        self.config.set("network_advanced_mode", advanced)
        for name in ("create_private", "connect_private"):
            old = self.content_stack.get_child_by_name(name)
            if old is not None:
                self.content_stack.remove(old)

    def show_api_access(self) -> None:
        from big_remote_play.private_network.service import default_service
        from .api_access_dialog import ApiAccessDialog

        ApiAccessDialog(self, default_service(), show_toast=self.show_toast, focus=self._vpn_choice or "").present()

    def show_internet_check(self) -> None:
        from .connection_guides import build_internet_check_dialog

        build_internet_check_dialog().present(self)

    def show_direct_internet_guide(self) -> None:
        from .connection_guides import build_direct_internet_dialog

        build_direct_internet_dialog().present(self)

    def show_vpn_accounts(self) -> None:
        from big_remote_play.utils.vpn_accounts import VPNAccountManager
        from .vpn_accounts_dialog import VPNAccountsDialog

        def select(provider: str) -> None:
            # These rows exist to add a second account, so the join page must
            # offer its form even when this PC is already on a network.
            self._apply_vpn_selection(provider, add_account=True)

        dialog = VPNAccountsDialog(
            self,
            VPNAccountManager(self.system_check),
            on_add_tailscale=lambda: select("tailscale"),
            on_add_headscale=lambda: select("headscale"),
            on_join_zerotier=lambda: select("zerotier"),
            show_toast=self.show_toast,
        )
        self._vpn_accounts_dialog = dialog
        dialog.present()

    def _apply_vpn_selection(self, provider_id, *, add_account: bool = False, destination: str = "connect_private"):
        self._vpn_choice = provider_id
        self._vpn_add_account = add_account
        save_vpn_choice(provider_id)
        for name in ("create_private", "connect_private"):
            old = self.content_stack.get_child_by_name(name)
            if old:
                self.content_stack.remove(old)
        self._filter_status_rows()
        self.navigate_to(destination)

    def _ensure_private_view(self, name: str) -> None:
        if self.content_stack.get_child_by_name(name) is not None:
            return
        provider = self._vpn_choice
        if not isinstance(provider, str) or provider not in VPN_PROVIDERS:
            return
        from .private_network_view import PrivateNetworkView

        view = PrivateNetworkView(self, mode="create" if name == "create_private" else "connect", vpn_provider=provider, add_account=self._vpn_add_account)
        setattr(self, "create_private_view" if name == "create_private" else "connect_private_view", view)
        self.content_stack.add_named(view, name)

    # ─────────────────────────────────────────────────────────────────────────
    #  WELCOME PAGE
    # ─────────────────────────────────────────────────────────────────────────

    def return_from_network(self) -> None:
        self.navigate_to(self._network_return_page)

    def network_return_label(self) -> str:
        if self._network_return_page == "welcome":
            return _("Back to Home")
        return _("Share") if self._network_return_page == "host" else _("Connect")

    def _go_to_private_network_setup(self) -> None:
        if self.current_page == "welcome":
            self._network_return_page = "welcome"
        self.navigate_to("vpn_selector")

    def _create_home_network_guide(self) -> Gtk.Widget:
        return boxed_rows(
            action_row(
                _("Play over the internet"),
                _("For computers in different houses. Not needed on the same home network."),
                "brp-network-private-symbolic",
                self._go_to_private_network_setup,
            )
        )

    def _secure_legacy_network_files(self) -> bool:
        """Move credentials that older versions left in plain JSON to the keyring."""

        def run() -> None:
            from big_remote_play.private_network.legacy import migrate_legacy_secrets

            try:
                migrate_legacy_secrets()
            except Exception as error:  # never break startup over a cleanup
                _log.warning("Legacy network files were not migrated: %s", error)

        threading.Thread(target=run, daemon=True).start()
        return False

    def _refresh_home_network_status(self) -> bool:
        """One quiet line on Home: is internet play ready? Read-only, lazy."""
        from big_remote_play.private_network.service import default_service
        from .network_common import Worker

        found = self.home_network_action.get_first_child() if getattr(self, "home_network_action", None) else None
        if not isinstance(found, Adw.ActionRow):
            return False
        row: Adw.ActionRow = found

        def apply(statuses) -> None:
            from big_remote_play.private_network.plan import PlanKind, plan_connection
            from .remote_connection import plan_words

            plan = plan_connection(statuses)
            if plan.kind is not PlanKind.INSTALL:
                # The same sentence the page shows, so both never disagree.
                row.set_subtitle(plan_words(plan)[2])

        self._home_status_worker = Worker()
        self._home_status_worker.submit(lambda: default_service().overview(), apply)
        return False

    @staticmethod
    def _create_home_benefit(text: str, icon_name: str) -> Gtk.Widget:
        benefit = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        benefit.add_css_class("home-benefit")
        icon = create_icon_widget(icon_name, size=16)
        icon.set_valign(Gtk.Align.CENTER)
        benefit.append(icon)
        label = Gtk.Label(label=text, xalign=0, wrap=True, hexpand=True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        benefit.append(label)
        return benefit

    def _create_home_stream_diagram(self) -> Gtk.Widget:
        flow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, valign=Gtk.Align.CENTER)
        flow.add_css_class("home-stream-flow")
        flow.set_halign(Gtk.Align.END)
        flow.append(icon_tile("brp-host-symbolic", tone="accent"))
        flow.append(create_icon_widget("go-next-symbolic", size=16))
        destinations = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for icon_name in ("brp-computer-symbolic", "brp-video-display-symbolic", "brp-client-symbolic"):
            destinations.append(icon_tile(icon_name, tone="guest"))
        flow.append(destinations)
        return flow

    @staticmethod
    def _create_role_flow(role_id: str) -> Gtk.Widget:
        icons = ("brp-host-symbolic", "brp-network-transmit-receive-symbolic") if role_id == "host" else ("brp-network-transmit-receive-symbolic", "brp-client-symbolic")
        flow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, halign=Gtk.Align.START)
        flow.add_css_class("role-flow")
        for index, icon_name in enumerate(icons):
            node = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            node.add_css_class("role-flow-node")
            node.append(create_icon_widget(icon_name, size=18))
            flow.append(node)
            if index == 0:
                flow.append(create_icon_widget("go-next-symbolic", size=14))
        return flow

    def create_welcome_page(self) -> Adw.NavigationView:
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.add_css_class("welcome-page")
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, valign=Gtk.Align.START)
        for edge in ("top", "bottom", "start", "end"):
            getattr(main_box, f"set_margin_{edge}")(24)
        self.welcome_main_box = main_box

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        hero.add_css_class("welcome-hero")
        hero_description = _("Play with friends far away, or use the power of your computer on another compatible device.")
        hero_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        self.home_hero_content = hero_content
        hero_copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True)
        eyebrow = Gtk.Label(label="Big Remote Play", xalign=0, wrap=True)
        eyebrow.add_css_class("caption-heading")
        eyebrow.add_css_class("accent")
        hero_copy.append(eyebrow)
        headline = Gtk.Label(label=_("Your games. Any screen. Anywhere."), xalign=0, wrap=True)
        headline.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        headline.add_css_class("title-1")
        hero_copy.append(headline)
        description = Gtk.Label(label=hero_description, xalign=0, wrap=True)
        description.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        description.add_css_class("dim-label")
        hero_copy.append(description)
        hero_content.append(hero_copy)
        self.home_device_flow = self._create_home_stream_diagram()
        hero_content.append(self.home_device_flow)
        hero.append(hero_content)

        benefits = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, homogeneous=True)
        self.home_benefits = benefits
        benefits.add_css_class("home-benefits")
        for text, icon_name in (
            (_("Play together, from anywhere"), "brp-client-symbolic"),
            (_("Use your gaming PC from anywhere"), "brp-host-symbolic"),
            (_("Play on another screen"), "brp-video-display-symbolic"),
        ):
            benefits.append(self._create_home_benefit(text, icon_name))
        hero.append(benefits)
        main_box.append(hero)

        self.home_question_label = Gtk.Label(label=_("What do you want to do?"), xalign=0, wrap=True)
        self.home_question_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.home_question_label.add_css_class("title-2")
        main_box.append(self.home_question_label)

        cards_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16, homogeneous=True)
        self.welcome_cards_box = cards_box
        self.host_card = self.create_action_card(
            "host",
            _("Share"),
            _("This computer runs the game. Share a game or your whole desktop."),
            "brp-host-symbolic",
            "Sunshine",
            lambda: self._select_home_role("host"),
        )
        self.guest_card = self.create_action_card(
            "guest",
            _("Connect"),
            _("Play on this device. Connect to the computer running the game."),
            "brp-client-symbolic",
            "Moonlight",
            lambda: self._select_home_role("guest"),
        )
        cards_box.append(self.host_card)
        cards_box.append(self.guest_card)
        main_box.append(cards_box)

        network_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        network_section.set_margin_top(4)
        self.home_network_section = network_section
        network_title = Gtk.Label(label=_("Playing over the internet?"), xalign=0, wrap=True)
        network_title.add_css_class("heading")
        network_section.append(network_title)
        self.home_network_action = self._create_home_network_guide()
        network_section.append(self.home_network_action)
        main_box.append(network_section)
        clamp = Adw.Clamp(maximum_size=860, tightening_threshold=620)
        clamp.set_child(main_box)
        scroll.set_child(clamp)

        # A single landing page: each action goes straight to its task.
        self.home_navigation = Adw.NavigationView(hhomogeneous=False, vhomogeneous=False)
        self.home_navigation.add(Adw.NavigationPage(child=scroll, title=_("Home"), tag="choices"))
        return self.home_navigation

    def _on_home_page_changed(self, *_args) -> None:
        if not hasattr(self, "content_title"):
            return
        self.home_back_button.set_visible(False)
        self.content_headerbar.set_show_back_button(True)
        if self.current_page == "welcome":
            self._set_header_title(_("Home"), "Big Remote Play")
            (self.guest_card if getattr(self, "_home_role", "host") == "guest" else self.host_card).grab_focus()

    def create_action_card(
        self,
        role_id: str,
        title: str,
        description: str,
        icon_name: str,
        component_name: str,
        callback: Callable[[], None],
    ) -> Gtk.Button:
        button = Gtk.Button(hexpand=True)
        button.add_css_class("role-card")
        button.add_css_class("role-" + role_id)
        button.connect("clicked", lambda _button: callback())
        button.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION], [title, description])
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True)
        tile = icon_tile(icon_name, large=True, tone="guest" if role_id == "guest" else "accent")
        tile.set_halign(Gtk.Align.START)
        tile.set_valign(Gtk.Align.START)
        content.append(tile)
        copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, hexpand=True)
        content.append(copy)
        title_box = Gtk.Box(spacing=10, hexpand=True)
        copy.append(title_box)
        title_label = Gtk.Label(label=title, xalign=0, wrap=True)
        title_label.add_css_class("title-2")
        title_label.set_hexpand(True)
        title_box.append(title_label)
        selection = create_icon_widget("go-next-symbolic", size=16)
        title_box.append(selection)
        description_label = Gtk.Label(label=description, xalign=0, wrap=True)
        description_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        description_label.add_css_class("dim-label")
        copy.append(description_label)
        copy.append(self._create_role_flow(role_id))
        copy.append(Gtk.Box(vexpand=True))
        state = Gtk.Box(spacing=6, halign=Gtk.Align.START)
        state.add_css_class("readiness-pill")
        dot = create_icon_widget("brp-media-record-symbolic", size=8)
        dot.add_css_class("status-dot")
        state.append(dot)
        label = Gtk.Label(label=_("Checking..."), wrap=True, xalign=0)
        label.add_css_class("caption")
        state.append(label)
        copy.append(state)
        self._role_card_ui[role_id] = {"button": button, "state": state, "dot": dot, "label": label, "selection": selection, "content": content}
        button.set_child(content)
        return button

    def _select_home_role(self, role_id: str, *, remember: bool = True) -> None:
        """Open the chosen task, without an intermediate guide or service start."""
        if role_id not in self._ROLE_COMPONENTS:
            return
        self._home_role = role_id
        if remember:
            self.config.set("home_role", role_id)
        self._activate_role(role_id, *self._ROLE_COMPONENTS[role_id])
        if role_id == "host" and self.current_page == "host":
            self.host_view.view_stack.set_visible_child_name("overview")

    def _set_role_card_state(self, role_id: str, component_name: str, installed: bool) -> None:
        ui = self._role_card_ui.get(role_id)
        if ui is None:
            return

        state = ui["state"]
        dot = ui["dot"]
        label = ui["label"]
        for css_class in ("ready", "needs-setup"):
            state.remove_css_class(css_class)
        for css_class in ("status-online", "status-offline"):
            dot.remove_css_class(css_class)

        if installed:
            # Working components stay out of the way. The task itself is the
            # focus; technical names only appear when the user must act.
            state.set_visible(False)
            ui["button"].set_tooltip_text(None)
        else:
            state.set_visible(True)
            state.add_css_class("needs-setup")
            dot.add_css_class("status-offline")
            # A missing component is explained before opening its task.
            label.set_label(_("Needs {}").format(component_name))
            ui["button"].set_tooltip_text(_("Install {}").format(component_name))

    def _activate_role(self, target: str, service_id: str, component_name: str) -> None:
        """Enter a role, or offer the one action needed to make it usable."""
        if self._service_installed.get(service_id) is not False:
            self.navigate_to(target)
            return

        dialog = Adw.AlertDialog(heading=_("Installation needed"))
        dialog.set_body(_("Install {}").format(component_name))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("install", _("Install"))
        dialog.set_default_response("install")
        dialog.set_close_response("cancel")
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)

        def on_response(_dialog: Adw.AlertDialog, response: str) -> None:
            if response != "install":
                return

            def installation_finished() -> None:
                self.check_system()
                self.navigate_to(target)

            InstallerWindow(parent=self, on_success=installation_finished, packages=("sunshine" if target == "host" else "moonlight-qt",)).present()

        dialog.connect("response", on_response)
        dialog.present(self)

    # Task -> (component that must exist, its name in the install prompt).
    _ROLE_COMPONENTS = {"host": ("sunshine", "Sunshine"), "guest": ("moonlight", "Moonlight")}

    def on_nav_selected(self, _lb: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if row and hasattr(self, "content_stack"):
            pid = self._nav_page_by_row.get(row)
            if pid:
                if pid == "welcome":
                    self.home_navigation.pop_to_tag("choices")
                if pid == "vpn_selector" and self._vpn_choice:
                    pid = "create_private"
                # The home cards offered to install what a task needs; reaching
                # the same task from the sidebar must offer it too.
                component = self._ROLE_COMPONENTS.get(pid)
                if component is not None:
                    self._activate_role(pid, *component)
                    return
                self.navigate_to(pid)

    def navigate_to(self, pid: str) -> None:
        if pid == "change_vpn":
            pid = "vpn_selector"
        if pid in ("create_private", "connect_private") and not self._vpn_choice:
            pid = "vpn_selector"
        if pid in ("create_private", "connect_private"):
            if self.current_page in ("host", "guest"):
                self._network_return_page = self.current_page
            self._ensure_private_view(pid)
        if self.content_stack.get_child_by_name(pid) is None:
            return

        network_page = pid in ("vpn_selector", "create_private", "connect_private")
        if network_page and self.current_page in ("host", "guest"):
            self._network_return_page = self.current_page
        if pid in ("create_private", "connect_private"):
            view = self.content_stack.get_child_by_name(pid)
            page = view.get_child() if isinstance(view, Adw.Bin) else None
            guide = getattr(page, "_return_to_game", None)
            return_row = guide.get_first_child() if guide is not None else None
            if return_row is not None and isinstance(return_row, Adw.ActionRow):
                return_row.set_subtitle(self.network_return_label())
        sidebar_pid = "vpn_selector" if network_page else pid
        self.nav_list.handler_block_by_func(self.on_nav_selected)
        try:
            for row, page in self._nav_page_by_row.items():
                if page == sidebar_pid:
                    self.nav_list.select_row(row)
                    break
        finally:
            self.nav_list.handler_unblock_by_func(self.on_nav_selected)

        self.content_stack.set_visible_child_name(pid)
        self.current_page = pid
        self.home_back_button.set_visible(False)
        self.network_back_button.set_visible(pid in ("create_private", "connect_private"))
        self.content_headerbar.set_show_back_button(True)
        self._remember_role(pid)
        self._filter_status_rows()

        if pid == "host":
            self._set_header_context("host")
        elif pid == "guest":
            self._set_header_title(_("Connect"), _("Play from another PC"))
        elif pid == "welcome":
            self._on_home_page_changed()
        elif pid == "vpn_selector":
            self._set_header_title(_("Play over the internet"), _("Connect your devices"))
        else:
            # The task is the title; the method is only the subtitle.
            provider = VPN_PROVIDERS[self._vpn_choice]["name"] if self._vpn_choice else ""
            title = _("Network details") if pid == "create_private" else _("Set up the connection")
            self._set_header_title(title, provider)
        if self.split_view.get_collapsed():
            self.split_view.set_show_content(True)

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
        if self._status_timer_id is None:
            self._status_timer_id = GLib.timeout_add_seconds(3, self.p_check)

    def p_check(self):
        """Refresh only the service rows the current page shows.

        Home and the network pages show none, so they start no process at all;
        Share and Connect check one service each.
        """
        if self._polling_status:
            return True
        probes = {
            "sunshine": self.system_check.is_sunshine_running,
            "moonlight": self.system_check.is_moonlight_running,
            "docker": self.system_check.is_docker_running,
            "tailscale": self.system_check.is_tailscale_running,
            "zerotier": self.system_check.is_zerotier_running,
        }
        wanted = [service_id for service_id in self._relevant_service_ids() if service_id in probes]
        if not wanted:
            return True
        self._polling_status = True

        def finish(states):
            self._polling_status = False
            if self._status_timer_id is not None and states is not None:
                for service_id, running in states.items():
                    self._service_running[service_id] = running
                    self._refresh_service_state(service_id)
            return False

        def check():
            states = None
            try:
                states = {service_id: probes[service_id]() for service_id in wanted}
            except Exception:
                _log.exception("Cannot refresh service status")
            finally:
                GLib.idle_add(finish, states)

        threading.Thread(target=check, daemon=True).start()
        return True

    def update_status(self, h_sun, h_moon):
        # System readiness is reflected in the role cards and sidebar. Startup
        # stays interruption-free; installation is offered when the user chooses
        # a role that needs it.
        return None

    def show_toast(self, m):
        if hasattr(self, "toast_overlay"):
            # Messages can quote network, device or account names: never markup.
            toast = Adw.Toast.new(m)
            toast.set_use_markup(False)
            self.toast_overlay.add_toast(toast)
        else:
            _log.info(m)

    # ─────────────────────────────────────────────────────────────────────────
    #  SERVICE CONTROL DIALOG
    # ─────────────────────────────────────────────────────────────────────────

    def _run_sunshine_action(self, action: str, dialog: Gtk.Window) -> None:
        """Start/stop/restart Sunshine through its own manager, off the UI thread."""
        from big_remote_play.host.sunshine_manager import SunshineHost

        dialog.destroy()

        def work() -> None:
            server = SunshineHost()
            if action == "stop":
                server.stop()
                ok, detail = True, None
            elif action == "restart":
                ok, detail = server.restart()
            else:
                ok, detail = server.start()
            message = _("Action {} sent to {}").format(action, SERVICE_METADATA["sunshine"]["name"]) if ok else (detail or _("Sunshine is not running."))
            GLib.idle_add(self.show_toast, message)
            GLib.idle_add(self.check_system)

        threading.Thread(target=work, daemon=True).start()

    def on_service_clicked(self, service_id, probe_result=None):
        """Open service control dialog"""
        meta = SERVICE_METADATA.get(service_id)
        if not meta:
            return

        if probe_result is None:

            def probe_service():
                running_checks = {
                    "sunshine": self.system_check.is_sunshine_running,
                    "moonlight": self.system_check.is_moonlight_running,
                    "docker": self.system_check.is_docker_running,
                    "tailscale": self.system_check.is_tailscale_running,
                    "zerotier": self.system_check.is_zerotier_running,
                }
                is_running = running_checks[service_id]()
                is_enabled = False
                has_unit = False
                if meta["type"] == "service":
                    base = ["systemctl"]
                    if meta.get("user"):
                        base.append("--user")
                    try:
                        # Some builds ship Sunshine without a systemd unit; the
                        # start/enable buttons would silently do nothing there.
                        has_unit = subprocess.run([*base, "cat", meta["unit"]], capture_output=True, timeout=5).returncode == 0
                        if has_unit:
                            is_enabled = subprocess.run([*base, "is-enabled", "--quiet", meta["unit"]], timeout=5).returncode == 0
                    except (OSError, subprocess.SubprocessError):
                        pass
                containers_running = self.system_check.are_containers_running() if service_id == "docker" else False
                GLib.idle_add(
                    self.on_service_clicked,
                    service_id,
                    (is_running, is_enabled, containers_running, has_unit),
                )

            threading.Thread(target=probe_service, daemon=True).start()
            return

        is_running, is_enabled, containers_running, has_unit = probe_result

        dialog = Adw.Window(transient_for=self)
        dialog.add_css_class("brp-dialog")
        dialog.set_modal(True)
        dialog.set_title(meta["full_name"])
        dialog.set_default_size(400, -1)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        content.set_margin_top(24)
        content.set_margin_bottom(24)
        content.set_margin_start(24)
        content.set_margin_end(24)

        icon = create_icon_widget(meta.get("icon", "brp-service-symbolic"), size=48)
        icon.set_halign(Gtk.Align.CENTER)
        content.append(icon)

        title = Gtk.Label(label=meta["full_name"], wrap=True, justify=Gtk.Justification.CENTER)
        title.add_css_class("title-2")
        content.append(title)

        desc = Gtk.Label(label=meta["description"])
        desc.set_wrap(True)
        desc.set_justify(Gtk.Justification.CENTER)
        desc.add_css_class("dim-label")
        content.append(desc)

        status_box = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
        dot = create_icon_widget("brp-media-record-symbolic", size=12, css_class=["status-dot", "status-online" if is_running else "status-offline"])
        status_box.append(dot)
        status_lbl = Gtk.Label(label=_("Running") if is_running else _("Stopped"))
        status_box.append(status_lbl)
        content.append(status_box)

        actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        def run_cmd(action, sid=service_id, force_type=None):
            m = SERVICE_METADATA[sid]
            cmd = []

            def find_moonlight():
                for b in ["moonlight-qt", "moonlight"]:
                    if shutil.which(b):
                        return b
                return None

            current_type = force_type or m["type"]
            # Some builds ship Sunshine without a systemd unit. There, drive the
            # server the way the rest of the app does instead of sending
            # systemctl a unit name it cannot resolve.
            if sid == "sunshine" and current_type == "service" and not has_unit:
                self._run_sunshine_action(action, dialog)
                return

            if current_type == "service":
                cmd = ["pkexec", "/usr/bin/systemctl"]
                if m.get("user"):
                    cmd = ["systemctl", "--user"]
                cmd.append(action)
                cmd.append(m["unit"])
                if sid == "docker" and action == "stop":
                    cmd.append("docker.socket")
            elif current_type == "containers":
                if action == "start":
                    cmd = ["docker", "start", "caddy", "headscale"]
                elif action == "stop":
                    cmd = ["docker", "stop", "caddy", "headscale"]
                elif action == "restart":
                    cmd = ["docker", "restart", "caddy", "headscale"]
            else:
                bin_name = m["bin"]
                if sid == "moonlight":
                    found = find_moonlight()
                    if not found and action in ["start", "restart"]:
                        self.show_toast(_("Moonlight not found"))
                        return
                    bin_name = found or bin_name

                if action == "start":
                    cmd = [bin_name]
                elif action == "stop":
                    cmd = ["pkill", "-x", bin_name]
                elif action == "restart":
                    try:
                        subprocess.run(["pkill", "-x", bin_name], check=False, timeout=10)
                    except Exception:
                        pass
                    cmd = [bin_name]

            if cmd:
                try:
                    subprocess.Popen(cmd)
                    name = _("Containers") if current_type == "containers" else m["name"]
                    self.show_toast(_("Action {} sent to {}").format(action, name))
                    dialog.destroy()
                    GLib.timeout_add(1000, self.check_system)
                except Exception as e:
                    self.show_toast(_("Error executing command: {}").format(e))

        btn_main = Gtk.Button(label=_("Stop") if is_running else _("Start"))
        btn_main.add_css_class("suggested-action" if not is_running else "destructive-action")
        btn_main.connect("clicked", lambda b: run_cmd("stop" if is_running else "start"))
        actions.append(btn_main)

        # Restarting something that is stopped is just starting it, and the
        # button above already does that.
        if is_running:
            btn_restart = Gtk.Button(label=_("Restart"))
            btn_restart.connect("clicked", lambda b: run_cmd("restart"))
            actions.append(btn_restart)

        if meta["type"] == "service" and has_unit:
            btn_enable = Gtk.Button(label=_("Disable") if is_enabled else _("Enable"))
            btn_enable.connect("clicked", lambda b: run_cmd("disable" if is_enabled else "enable"))
            actions.append(btn_enable)

        if service_id == "docker":
            actions.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

            cont_running = containers_running
            cont_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

            cont_header = Gtk.Box(spacing=8)
            cont_header.set_halign(Gtk.Align.CENTER)
            cont_header.append(create_icon_widget("brp-service-symbolic", size=16))
            cont_header.append(Gtk.Label(label=_("Private Network Containers")))
            cont_box.append(cont_header)

            c_status_box = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
            c_dot = create_icon_widget("brp-media-record-symbolic", size=12, css_class=["status-dot", "status-online" if cont_running else "status-offline"])
            c_status_box.append(c_dot)
            c_status_lbl = Gtk.Label(label=_("Running (Caddy + Headscale)") if cont_running else _("Stopped"))
            c_status_box.append(c_status_lbl)
            cont_box.append(c_status_box)

            c_btn_main = Gtk.Button(label=_("Stop Containers") if cont_running else _("Start Containers"))
            c_btn_main.add_css_class("suggested-action" if not cont_running else "destructive-action")
            c_btn_main.connect("clicked", lambda b: run_cmd("stop" if cont_running else "start", force_type="containers"))
            cont_box.append(c_btn_main)

            if cont_running:
                c_btn_restart = Gtk.Button(label=_("Restart Containers"))
                c_btn_restart.connect("clicked", lambda b: run_cmd("restart", force_type="containers"))
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
