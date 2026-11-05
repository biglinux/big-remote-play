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
from .components import name_icon_button, icon_tile
from .service_status_card import ServiceStatusCard, provider_presentation, streaming_presentation, unknown_presentation
from .task_activity import Activity, activity_text, connect_activity, network_activity, pill_class, share_activity
from big_remote_play.private_network.models import ProviderStatus
from big_remote_play.utils.config import Config
from big_remote_play.utils.network import NetworkDiscovery
from big_remote_play.utils.system_check import SystemCheck
from big_remote_play.utils.icons import create_icon_widget, create_logo_widget
from big_remote_play.utils.i18n import _, pgettext
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
    "headscale": {
        "name": "HEADSCALE",
        "full_name": "Headscale",
        "description": _("Use your own Headscale server. Advanced setup."),
        "icon": "brp-headscale-symbolic",
        "type": "network",
    },
}

# Home remains a stable starting point; direct task destinations are shortcuts.
BASE_NAVIGATION_PAGES = {
    # TRANSLATORS: with context "navigation", Share and Connect name the two
    # main pages: the computer that shares a game and the one that connects to
    # it. Buttons that perform the action use the same words without context.
    "host": {"name": pgettext("navigation", "Share"), "icon": "brp-host-symbolic", "description": _("Run the game on this PC")},
    "guest": {"name": pgettext("navigation", "Connect"), "icon": "brp-client-symbolic", "description": _("Play from another PC")},
    "vpn_selector": {"name": _("Connect your devices"), "icon": "brp-network-private-symbolic", "description": _("For PCs in different houses")},
}
# The tasks whose sidebar entry carries a Running/Stopped state.
ACTIVITY_PAGES = ("host", "guest", "vpn_selector")

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
        self._network_polling = False
        self._network_poll_ticks = 0
        self._network_statuses: dict[str, ProviderStatus] = {}
        self._vpn_choice = load_vpn_choice()  # None if not yet chosen
        self.provider_page = None
        self.zerotier_network_id = ""  # the ZeroTier network the card and the ZeroTier page show
        self.network_advanced_mode = bool(self.config.get("network_advanced_mode", False))
        self._nav_page_by_row: dict[Gtk.ListBoxRow, str] = {}
        self._service_by_row: dict[Gtk.Widget, str] = {}
        self._status_rows: dict[str, ServiceStatusCard] = {}
        # Installed and running are probed by separate passes; a row shows one
        # combined state, so both halves are kept until they can be merged.
        self._service_installed: dict[str, bool] = {}
        self._service_running: dict[str, bool] = {}
        self._service_full_name: dict[str, str] = {}
        self._role_card_ui: dict[str, dict[str, Gtk.Widget]] = {}
        # The state shown next to Share, Connect and Connect your devices.
        self._nav_state_labels: dict[str, Gtk.Label] = {}
        self._task_activity: dict[str, Activity] = {}
        self._activity_ticks = 0

        self._install_window_actions()
        self.setup_ui()
        # Reopening must not remove the guide after a task was chosen once.
        self.navigate_to("welcome")
        self._status_timer_id = None
        self.check_system()
        # After the window is up: never on the startup critical path.
        GLib.timeout_add_seconds(3, self._secure_legacy_network_files)

        # Connect close signal
        self.connect("close-request", self.on_close_request)
        self.connect("notify::is-active", self._on_active_changed)

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
            breakpoint.add_setter(self.home_logo, "halign", Gtk.Align.START)
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
        # At phone-like widths the words and the two decisions come first.
        narrow.add_setter(self.home_logo, "pixel-size", 64)
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
        self.nav_list.set_header_func(self._nav_header)
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

    # Home · Share, Connect · Connect your devices: three kinds of place.
    _NAV_SECTION_STARTS = frozenset({"host", "vpn_selector"})

    def _nav_header(self, row: Gtk.ListBoxRow, before: Gtk.ListBoxRow | None) -> None:
        """A thin separator before each group; no extra words, no tree."""
        if before is None or self._nav_page_by_row.get(row) not in self._NAV_SECTION_STARTS:
            row.set_header(None)
            return
        if row.get_header() is None:
            separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            separator.add_css_class("brp-nav-separator")
            row.set_header(separator)

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
        self._nav_state_labels.clear()
        for pid, info in self._navigation_pages().items():
            self.nav_list.append(self.create_nav_row(pid, info))
        self._task_activity.clear()
        self._refresh_task_activity()
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

        # Running or Stopped, in words: the task's real state, whatever page is open.
        if page_id in ACTIVITY_PAGES:
            # The row's description announces the state; the label is not read twice.
            state = Gtk.Label(label=activity_text(Activity.CHECKING), valign=Gtk.Align.CENTER, halign=Gtk.Align.END, accessible_role=Gtk.AccessibleRole.PRESENTATION)
            state.set_wrap(True)
            state.set_max_width_chars(12)
            state.set_justify(Gtk.Justification.CENTER)
            for css_class in ("caption", "state-pill", "brp-nav-state", "offline"):
                state.add_css_class(css_class)
            box.append(state)
            self._nav_state_labels[page_id] = state

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

    # Streaming first, then every secure-connection method, in a fixed order.
    _STREAMING_SERVICES = ("sunshine", "moonlight")
    _NETWORK_SERVICES = ("tailscale", "zerotier", "headscale")

    def create_status_footer(self):
        """Service cards for the task on screen: its streaming component, then
        every private-network method with its real state."""
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        footer.set_margin_start(12)
        footer.set_margin_end(12)
        footer.set_margin_top(8)
        footer.set_margin_bottom(12)

        status_list = Gtk.ListBox()
        status_list.set_selection_mode(Gtk.SelectionMode.NONE)
        status_list.add_css_class("brp-service-list")
        status_list.set_header_func(self._service_header)
        # TRANSLATORS: accessible name of the list of streaming and private-network cards.
        status_list.update_property([Gtk.AccessibleProperty.LABEL], [_("Services")])
        self.service_list = status_list

        for service_id, label_text in (("sunshine", "Sunshine"), ("moonlight", "Moonlight"), ("tailscale", "Tailscale"), ("zerotier", "ZeroTier"), ("headscale", "Headscale")):
            meta = SERVICE_METADATA.get(service_id, {})
            row = ServiceStatusCard(
                service_id,
                label_text,
                meta.get("icon", "brp-service-symbolic"),
                meta.get("description", ""),
                primary=service_id in self._STREAMING_SERVICES,
            )
            row.set_activatable(True)
            self._service_by_row[row] = service_id
            row.connect("activated", lambda _r, sid=service_id: self.on_service_clicked(sid))
            self._service_full_name[service_id] = meta.get("full_name", label_text)
            self._status_rows[service_id] = row
            status_list.append(row)

        footer.append(status_list)
        self._filter_status_rows()
        return footer

    def _service_header(self, row: Gtk.ListBoxRow, before: Gtk.ListBoxRow | None) -> None:
        """“Streaming” above Sunshine/Moonlight, “Secure connection” above the VPNs."""
        service_id = self._service_by_row.get(row)
        if service_id in self._STREAMING_SERVICES:
            # TRANSLATORS: sidebar heading above the Sunshine or Moonlight card.
            text = _("Streaming")
        elif service_id in self._NETWORK_SERVICES and self._service_by_row.get(before) not in self._NETWORK_SERVICES:
            # TRANSLATORS: sidebar heading above the Tailscale, ZeroTier and Headscale cards.
            text = _("Secure connection")
        else:
            row.set_header(None)
            return
        header = row.get_header()
        if isinstance(header, Gtk.Label) and header.get_label() == text:
            return
        label = Gtk.Label(label=text, xalign=0, accessible_role=Gtk.AccessibleRole.HEADING)
        label.add_css_class("caption-heading")
        label.add_css_class("brp-service-heading")
        row.set_header(label)

    def _relevant_service_ids(self) -> list[str]:
        # Every method keeps its card while it is off, so turning one on or off
        # changes that card in place instead of adding or removing a row.
        if self.current_page in ("host", "guest"):
            return ["sunshine" if self.current_page == "host" else "moonlight", *self._NETWORK_SERVICES]
        # Connect your devices shows the same cards as Share and Connect: the
        # streaming component of the remembered task and the three methods.
        if self.current_page == "vpn_selector":
            return ["sunshine" if self._summary_role() == "host" else "moonlight", *self._NETWORK_SERVICES]
        # Home shows no service cards: the states sit next to the tasks above.
        return []

    def _filter_status_rows(self) -> None:
        """Show the streaming component of the task and the network methods."""
        relevant = set(self._relevant_service_ids())
        for service_id, row in self._status_rows.items():
            row.set_visible(service_id in relevant)
        service_list = getattr(self, "service_list", None)
        if service_list is not None:
            service_list.invalidate_headers()

    def _summary_role(self) -> str:
        """The task whose streaming card Connect your devices shows."""
        role = getattr(self, "_home_role", None) or self.config.get("home_role") or self._chosen_role()
        if role in ("host", "guest"):
            return role
        # Never chosen: the component this computer already has.
        if self._service_installed.get("sunshine") is not True and self._service_installed.get("moonlight") is True:
            return "guest"
        return "host"

    def _refresh_task_activity(self) -> None:
        """Share, Connect and Connect your devices: Running or Stopped, from real state."""
        host = getattr(self, "host_view", None)
        guest = getattr(self, "guest_view", None)
        activities = {
            "host": share_activity(
                transition=getattr(host, "sharing_transition", None),
                sharing=bool(getattr(host, "is_hosting", False)),
                server_running=self._service_running.get("sunshine"),
            ),
            "guest": connect_activity(
                connecting=bool(getattr(guest, "is_connecting", False)),
                streaming=bool(getattr(guest, "is_connected", False)),
                client_running=self._service_running.get("moonlight"),
            ),
            "vpn_selector": network_activity(
                self._network_statuses.values() if self._network_statuses else None,
                read_failed=getattr(self, "_network_read_failed", False),
            ),
        }
        pages = self._navigation_pages()
        for page_id, activity in activities.items():
            label = self._nav_state_labels.get(page_id)
            if label is None or self._task_activity.get(page_id) is activity:
                continue
            self._task_activity[page_id] = activity
            label.set_label(activity_text(activity))
            for css_class in ("online", "offline", "starting"):
                label.remove_css_class(css_class)
            label.add_css_class(pill_class(activity))
            row = label.get_ancestor(Gtk.ListBoxRow)
            if row is not None:
                info = pages.get(page_id, {})
                # TRANSLATORS: accessible description of a sidebar entry: {description} explains the task, {state} is Running, Stopped…
                description = _("{description}. {state}").format(description=info.get("description", ""), state=activity_text(activity))
                row.update_property([Gtk.AccessibleProperty.DESCRIPTION], [description])

    def _on_task_state_changed(self, *, from_share: bool = True) -> None:
        """Share or Connect changed state in this window: say so at once, then confirm with a probe."""
        host = getattr(self, "host_view", None)
        if from_share and host is not None and getattr(host, "sharing_transition", None) is None:
            # A finished start or stop is what the Sunshine probe would now report.
            self._service_running["sunshine"] = bool(host.is_hosting)
            self._refresh_service_state("sunshine")
        self._refresh_task_activity()
        if getattr(self, "_status_timer_id", None) is not None:  # not while the window opens or closes
            self._probe_streaming(list(self._STREAMING_SERVICES))

    def _refresh_network_card(self, service_id: str) -> None:
        row = self._status_rows.get(service_id)
        status = self._network_statuses.get(service_id)
        if row is not None and status is not None:
            row.set_presentation(provider_presentation(status))

    def _refresh_service_state(self, service_id: str) -> None:
        """One state per card, from the installed and running probes.

        Both halves are probed separately; until both are known the card keeps
        saying “Checking...”. The words carry the state, so it survives without
        colour vision and is what a screen reader announces."""
        if service_id in self._NETWORK_SERVICES:
            self._refresh_network_card(service_id)
            return
        row = self._status_rows.get(service_id)
        if row is None or self._service_installed.get(service_id) is None:
            return
        row.set_presentation(streaming_presentation(service_id, self._service_installed.get(service_id), self._service_running.get(service_id)))
        self._refresh_task_activity()

    def update_server_status(self, run_sun, run_moon, run_tailscale, run_zt=False):
        for service_id, running in [("sunshine", run_sun), ("moonlight", run_moon), ("tailscale", run_tailscale), ("zerotier", run_zt)]:
            self._service_running[service_id] = running
            self._refresh_service_state(service_id)

    def update_dependency_ui(self, has_sun, has_moon, has_tailscale, has_zt=False):
        status_items = [
            ("sunshine", "host", has_sun, "Sunshine"),
            ("moonlight", "guest", has_moon, "Moonlight"),
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
        name_icon_button(self.home_back_button, _("Back"), _("Back to the previous question"))
        self.home_back_button.add_css_class("flat")
        self.home_back_button.connect("clicked", lambda _button: self.home_navigation.pop())
        header.pack_start(self.home_back_button)
        # Inside Connect your devices: back from a method's page to the list.
        self.network_back_button = Gtk.Button(icon_name="go-previous-symbolic", visible=False)
        name_icon_button(self.network_back_button, _("Back"), _("Back to the previous page"))
        self.network_back_button.add_css_class("flat")
        self.network_back_button.connect("clicked", lambda _button: self.network_navigation.pop())
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
        self.host_view.add_state_listener(self._on_task_state_changed)
        # Connect says nothing about Sunshine: keep its probed state.
        self.guest_view.add_state_listener(lambda: self._on_task_state_changed(from_share=False))

        self.vpn_selector_page = self.create_vpn_selector_page()
        self.content_stack.add_named(self.vpn_selector_page, "vpn_selector")

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

        # Share and Connect each keep the routine task on the first tab and
        # everything technical on a named second one.
        self.header_context_stacks: dict[str, Adw.ViewStack] = {
            "host": self.host_view.view_stack,
            "guest": self.guest_view.view_stack,
        }
        self.header_context_specs = {
            "host": (pgettext("navigation", "Share"), _("Run the game on this PC"), _("Sharing sections")),
            "guest": (pgettext("navigation", "Connect"), _("Play from another PC"), _("Connect sections")),
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
        # Appearance is one choice with four values: a radio section in the
        # menu shows the current one, instead of a window built for it.
        appearance = Gio.Menu()
        # TRANSLATORS: Gamer is the proper name of the violet/cyan appearance preset.
        gamer_label = _("Gamer")
        for label, value in ((gamer_label, "gamer"), (_("Automatic"), "auto"), (_("Light"), "light"), (_("Dark"), "dark")):
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
        """**Connect your devices**: the methods first; each method is a page pushed here."""
        from .remote_connection import RemoteConnectionPage

        self.remote_connection_page = RemoteConnectionPage(self)
        self.network_navigation = Adw.NavigationView()
        self.network_navigation.add(Adw.NavigationPage(child=self.remote_connection_page, title=_("Connect your devices"), tag="providers"))
        self.network_navigation.connect("notify::visible-page", lambda *_args: self._on_network_page_changed())
        return self.network_navigation

    def _on_network_page_changed(self) -> None:
        """The header follows the page: the list, a method (with its tabs) or one of its steps."""
        if not hasattr(self, "network_back_button"):
            return
        on_network = self.current_page == "vpn_selector"
        page = self.network_navigation.get_visible_page()
        nested = page is not None and page.get_tag() != "providers"
        self.network_back_button.set_visible(on_network and nested)
        self.content_headerbar.set_show_back_button(not (on_network and nested))
        if not on_network:
            return
        from .provider_page import ProviderPage, state_text

        if isinstance(page, ProviderPage):
            self.provider_page = page
            subtitle = state_text(page.state) if page.status is not None else _("Checking…")
            self.header_context_stacks["network"] = page.view_stack
            self.header_context_specs["network"] = (page.provider.display_name, subtitle, _("{name} sections").format(name=page.provider.display_name))
            self._set_header_context("network")
        elif nested and page is not None:
            from .network_common import in_stack

            method = self.provider_page
            owner = method.provider.display_name if method is not None and in_stack(self.network_navigation, method) else ""
            self._set_header_title(page.get_title(), owner)
        else:
            self._set_header_title(_("Connect your devices"), "")

    def set_network_advanced_mode(self, advanced: bool) -> None:
        """Remember the disclosure level; a method's dialog follows it when opened again."""
        advanced = bool(advanced)
        if advanced == self.network_advanced_mode:
            return
        self.network_advanced_mode = advanced
        self.config.set("network_advanced_mode", advanced)

    def _network_pages(self) -> Adw.NavigationView | None:
        """Connect your devices' navigation while it is on screen: its tools open there as pages, not dialogs."""
        navigation = getattr(self, "network_navigation", None)
        return navigation if self.current_page == "vpn_selector" else None

    def _show_guide(self, build) -> None:
        from .network_common import push_page

        navigation = self._network_pages()
        if navigation is None:
            build().present(self)
        else:
            push_page(navigation, build(as_page=True))

    def show_api_access(self, *, focus: str = "", on_changed: Callable[[], None] | None = None) -> None:
        from big_remote_play.private_network.service import default_service
        from .api_access_dialog import ApiAccessDialog

        ApiAccessDialog(self, default_service(), show_toast=self.show_toast, on_changed=on_changed, focus=focus or self._vpn_choice or "", navigation=self._network_pages()).present()

    def show_internet_check(self) -> None:
        from .connection_guides import build_internet_check_dialog

        self._show_guide(build_internet_check_dialog)

    def show_direct_internet_guide(self) -> None:
        from .connection_guides import build_direct_internet_dialog

        self._show_guide(build_direct_internet_dialog)

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
            navigation=self._network_pages(),
        )
        self._vpn_accounts_dialog = dialog
        dialog.present()

    # Page names of earlier versions: Set up (join) and the method's devices.
    _PROVIDER_VIEWS = {"connect_private": "setup", "create_private": "devices", "overview": "devices"}

    def _apply_vpn_selection(self, provider_id, *, add_account: bool = False, destination: str = "connect_private", auto_start: bool = False, prefill: dict | None = None):
        """Open a method's page: ``connect_private`` → its Set up step, otherwise its devices."""
        return self.open_provider(provider_id, view=self._PROVIDER_VIEWS.get(destination, "devices"), add_account=add_account, auto_start=auto_start, prefill=prefill)

    def open_provider(self, provider_id: str, *, view: str = "devices", add_account: bool = False, auto_start: bool = False, prefill: dict | None = None):
        """A method's page (**Devices | Advanced**) inside this window, under Connect your devices.

        Methods are independent: opening one never turns another off. The
        page offers **Back to Share/Connect** when the person came from there.
        """
        if provider_id not in VPN_PROVIDERS:
            return None
        from big_remote_play.private_network.models import ProviderId

        from .provider_page import ProviderPage

        if self.current_page in ("host", "guest"):
            self._network_return_page = self.current_page
        # Only remembered as the method last opened (older page names use it).
        self._vpn_choice = provider_id
        save_vpn_choice(provider_id)
        self.navigate_to("vpn_selector")
        self.network_navigation.pop_to_tag("providers")
        page = ProviderPage(self, ProviderId(provider_id), self.network_navigation)
        self.provider_page = page
        self.network_navigation.push(page)
        if view == "setup":
            page.show_setup(auto_start=auto_start, add_account=add_account, prefill=prefill)
        elif view == "advanced":
            page.view_stack.set_visible_child_name("advanced")
        self._on_network_page_changed()
        return page

    def finish_provider(self) -> None:
        """A method works: back to Share or Connect, or to the internet page with its next steps."""
        target = self._network_return_page if self._network_return_page in ("host", "guest") else "vpn_selector"
        if target == "vpn_selector":
            self.network_navigation.pop_to_tag("providers")
        if self.current_page != target:
            self.navigate_to(target)
        else:
            self._refresh_private_network_status()

    # ─────────────────────────────────────────────────────────────────────────
    #  WELCOME PAGE
    # ─────────────────────────────────────────────────────────────────────────

    def return_from_network(self) -> None:
        self.navigate_to(self._network_return_page)

    def network_return_label(self) -> str:
        if self._network_return_page == "welcome":
            return _("Back to Home")
        return pgettext("navigation", "Share") if self._network_return_page == "host" else pgettext("navigation", "Connect")

    def _go_to_private_network_setup(self) -> None:
        if self.current_page == "welcome":
            self._network_return_page = "welcome"
        self.navigate_to("vpn_selector")

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

    def create_welcome_page(self) -> Adw.NavigationView:
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.add_css_class("welcome-page")
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, valign=Gtk.Align.START)
        for edge in ("top", "bottom", "start", "end"):
            getattr(main_box, f"set_margin_{edge}")(24)
        self.welcome_main_box = main_box

        # One hero: who we are, what it does, and the recommended first step.
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        hero.add_css_class("welcome-hero")
        hero_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        self.home_hero_content = hero_content
        # The application's own logo, rasterized for HiDPI; its name is the headline's job.
        self.home_logo = create_logo_widget("big-remote-play", 104, css_class="brp-home-logo")
        self.home_logo.set_valign(Gtk.Align.CENTER)
        self.home_logo.set_halign(Gtk.Align.CENTER)
        hero_content.append(self.home_logo)
        hero_copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True, valign=Gtk.Align.CENTER)
        headline = Gtk.Label(label=_("Your games. Any screen. Anywhere."), xalign=0, wrap=True)
        headline.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        headline.add_css_class("title-1")
        headline.set_accessible_role(Gtk.AccessibleRole.HEADING)
        hero_copy.append(headline)
        description = Gtk.Label(label=_("Play on another screen using this computer, or invite a friend to play with you."), xalign=0, wrap=True)
        description.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        description.add_css_class("dim-label")
        hero_copy.append(description)

        # The easiest way to begin lives in the hero: two questions, then
        # everything this computer needs, then the right page.
        self.guided_setup_button = Gtk.Button(halign=Gtk.Align.START)
        self.guided_setup_button.add_css_class("suggested-action")
        self.guided_setup_button.add_css_class("brp-primary")
        self.guided_setup_button.add_css_class("brp-hero-cta")
        cta = Gtk.Box(spacing=8)
        cta.append(create_icon_widget("brp-guided-setup-symbolic", size=16))
        cta_label = Gtk.Label(label=_("Start with the guided setup"), wrap=True, max_width_chars=34)
        cta_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        cta_label.set_natural_wrap_mode(Gtk.NaturalWrapMode.NONE)
        cta.append(cta_label)
        self.guided_setup_button.set_child(cta)
        self.guided_setup_button.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            [_("Start with the guided setup"), _("Answer two questions. Big Remote Play prepares this computer and opens the right page.")],
        )
        self.guided_setup_button.connect("clicked", lambda _button: self.start_guided_setup())
        self.guided_setup_button.set_margin_top(8)
        hero_copy.append(self.guided_setup_button)
        hero_content.append(hero_copy)
        hero.append(hero_content)
        main_box.append(hero)

        self.home_question_label = Gtk.Label(label=_("Or choose what you want to do"), xalign=0, wrap=True)
        self.home_question_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.home_question_label.add_css_class("title-3")
        self.home_question_label.set_accessible_role(Gtk.AccessibleRole.HEADING)
        main_box.append(self.home_question_label)

        cards_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16, homogeneous=True)
        self.welcome_cards_box = cards_box
        self.host_card = self.create_action_card(
            "host",
            pgettext("navigation", "Share"),
            _("This computer runs the game. Share a game or your whole desktop."),
            "brp-host-symbolic",
            "Sunshine",
            lambda: self._select_home_role("host"),
        )
        self.guest_card = self.create_action_card(
            "guest",
            pgettext("navigation", "Connect"),
            _("Play on this device. Connect to the computer running the game."),
            "brp-client-symbolic",
            "Moonlight",
            lambda: self._select_home_role("guest"),
        )
        cards_box.append(self.host_card)
        cards_box.append(self.guest_card)
        main_box.append(cards_box)
        # Playing over the internet is a permanent sidebar destination and a
        # question of the guided setup; Home does not repeat it a third time.
        clamp = Adw.Clamp(maximum_size=860, tightening_threshold=620)
        clamp.set_child(main_box)
        scroll.set_child(clamp)

        # A single landing page: each action goes straight to its task.
        self.home_navigation = Adw.NavigationView(hhomogeneous=False, vhomogeneous=False)
        self.home_navigation.add(Adw.NavigationPage(child=scroll, title=_("Home"), tag="choices"))
        self.home_navigation.connect("notify::visible-page", self._on_home_page_changed)
        return self.home_navigation

    def start_guided_setup(self) -> None:
        """Ask two questions on Home, then open the task that fits."""
        from .guided_setup import GuidedSetup

        if self.current_page != "welcome":
            self.navigate_to("welcome")
        self.guided_setup = GuidedSetup(self)
        self.guided_setup.start()

    def _on_home_page_changed(self, *_args) -> None:
        if not hasattr(self, "content_title"):
            return
        page = self.home_navigation.get_visible_page()
        guided = page is not None and page.get_tag() != "choices"
        self.home_back_button.set_visible(guided and self.current_page == "welcome")
        # One back arrow at a time: inside the guide it means "previous question".
        self.content_headerbar.set_show_back_button(not (guided and self.current_page == "welcome"))
        if self.current_page != "welcome":
            return
        if guided and page is not None:
            self._set_header_title(page.get_title(), _("Home"))
            return
        self._set_header_title(_("Home"), "Big Remote Play")
        # A returning person lands on their task; a first visit on the guided start.
        role = getattr(self, "_home_role", None) or self.config.get("home_role")
        target = {"host": self.host_card, "guest": self.guest_card}.get(role, self.guided_setup_button)
        target.grab_focus()

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
        copy.append(Gtk.Box(vexpand=True))
        state = Gtk.Box(spacing=6, halign=Gtk.Align.START)
        state.add_css_class("readiness-pill")
        dot = create_icon_widget("brp-media-record-symbolic", size=8)
        dot.add_css_class("status-dot")
        state.append(dot)
        label = Gtk.Label(label=_("Checking…"), wrap=True, xalign=0)
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
            # A missing component is explained before opening its task, in
            # words; the product name stays in the tooltip and the installer.
            label.set_label(_("Needs a quick installation"))
            ui["button"].set_tooltip_text(_("Install {name}").format(name=component_name))

    def _activate_role(self, target: str, service_id: str, component_name: str) -> None:
        """Enter a role, or offer the one action needed to make it usable.

        A "not installed" seen earlier is never trusted on its own: the
        component may have been installed since, here or in another program.
        It is looked up again first, and only a component that is still
        missing opens the installer.
        """
        if self._service_installed.get(service_id) is not False:
            self.navigate_to(target)
            return
        from big_remote_play.utils import dependencies
        from .network_common import Worker

        ids = dependencies.ROLE_COMPONENTS[target]

        def checked(states) -> None:
            self._apply_component_states(states)
            if self._service_installed.get(service_id) is not False:
                self.navigate_to(target)
            else:
                self._offer_installation(target)

        self._role_worker = getattr(self, "_role_worker", None) or Worker()
        self._role_worker.submit(lambda: dependencies.check(ids), checked, failed=lambda _error: self._offer_installation(target))

    def _offer_installation(self, target: str) -> None:
        """Install what the task needs, then open it: no second click."""
        from big_remote_play.utils import dependencies
        from .dependency_installer import InstallDialog

        if target == "host":
            description = _("To share this computer, Big Remote Play needs one more program. It installs it for you after asking for your password once.")
        else:
            description = _("To play from this computer, Big Remote Play needs one more program. It installs it for you after asking for your password once.")

        def ready() -> None:
            self.check_system()
            self.navigate_to(target)

        dialog = InstallDialog(dependencies.ROLE_COMPONENTS[target], title=_("Let's get this computer ready"), description=description, on_ready=ready)
        self._install_dialog = dialog
        dialog.present(self)

    def _apply_component_states(self, states) -> None:
        """Fresh installed probes into every place that shows them."""
        names = {"sunshine": ("host", "Sunshine"), "moonlight": ("guest", "Moonlight")}
        for state in states:
            self._service_installed[state.id] = state.installed
            self._refresh_service_state(state.id)
            if state.id in names:
                self._set_role_card_state(names[state.id][0], names[state.id][1], state.installed)

    def _on_active_changed(self, *_args) -> None:
        """Coming back to the window: what was installed meanwhile is seen."""
        if not self.is_active():
            return
        now = GLib.get_monotonic_time()
        if now - getattr(self, "_last_focus_check", 0) < 10_000_000:
            return
        self._last_focus_check = now
        self.check_system()
        self._refresh_private_network_status()

    # Task -> (component that must exist, its name in the install prompt).
    _ROLE_COMPONENTS = {"host": ("sunshine", "Sunshine"), "guest": ("moonlight", "Moonlight")}

    def on_nav_selected(self, _lb: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if row and hasattr(self, "content_stack"):
            pid = self._nav_page_by_row.get(row)
            if pid:
                if pid == "welcome":
                    self.home_navigation.pop_to_tag("choices")
                if pid == "vpn_selector":
                    # The sidebar always starts at the list of methods.
                    self.network_navigation.pop_to_tag("providers")
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
        if pid in ("create_private", "connect_private"):
            # A method's Set up and Details are views of its dialog, not pages.
            if self._vpn_choice in VPN_PROVIDERS:
                self._apply_vpn_selection(self._vpn_choice, destination=pid)
                return
            pid = "vpn_selector"
        if self.content_stack.get_child_by_name(pid) is None:
            return

        network_page = pid == "vpn_selector"
        if network_page and self.current_page in ("host", "guest"):
            self._network_return_page = self.current_page
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
        self.network_back_button.set_visible(False)
        self.content_headerbar.set_show_back_button(True)
        self._remember_role(pid)
        self._filter_status_rows()
        self._refresh_private_network_status()

        if pid == "host":
            self._set_header_context("host")
        elif pid == "guest":
            self._set_header_context("guest")
        elif pid == "welcome":
            self._on_home_page_changed()
        elif pid == "vpn_selector":
            self._on_network_page_changed()
        if self.split_view.get_collapsed():
            self.split_view.set_show_content(True)

    def check_system(self):
        def check():
            h_sun = self.system_check.has_sunshine()
            h_moon = self.system_check.has_moonlight()
            h_tail = self.system_check.has_tailscale()
            h_zt = self.system_check.has_zerotier()

            r_sun = self.system_check.is_sunshine_running()
            r_moon = self.system_check.is_moonlight_running()
            r_tail = self.system_check.is_tailscale_running()
            r_zt = self.system_check.is_zerotier_running()

            def finish_system_check():
                self.update_status(h_sun, h_moon)
                self.update_server_status(r_sun, r_moon, r_tail, r_zt)
                self.update_dependency_ui(h_sun, h_moon, h_tail, h_zt)
                return False

            GLib.idle_add(finish_system_check)

        threading.Thread(target=check, daemon=True).start()
        if self._status_timer_id is None:
            self._status_timer_id = GLib.timeout_add_seconds(3, self.p_check)

    def _refresh_private_network_status(self) -> None:
        """Fetch provider membership off the GTK thread, with no overlap.

        Every page needs it: the sidebar says whether Connect your devices is running."""
        if self._network_polling:
            return
        self._network_polling = True

        def finish(statuses):
            self._network_polling = False
            if self._status_timer_id is None:
                return False  # the window is closing
            if statuses is not None:
                self._network_read_failed = False
                self._network_statuses = {status.provider.value: status for status in statuses}
                for provider in self._NETWORK_SERVICES:
                    self._refresh_network_card(provider)
            elif not self._network_statuses:
                # Never leave the cards on "Checking…" after a failed read.
                self._network_read_failed = True
                for provider in self._NETWORK_SERVICES:
                    self._status_rows[provider].set_presentation(unknown_presentation())
            self._refresh_task_activity()
            return False

        def check():
            statuses = None
            try:
                from big_remote_play.private_network.service import default_service

                statuses = default_service().overview()
            except Exception:
                _log.exception("Cannot refresh private network status")
            finally:
                GLib.idle_add(finish, statuses)

        threading.Thread(target=check, daemon=True).start()

    def p_check(self):
        """Refresh what the sidebar shows, cheaply.

        The streaming card on screen is probed every 3 s; Sunshine and
        Moonlight for the Share and Connect states every third tick (9 s), as
        starts and stops made in this window arrive at once through
        ``_on_task_state_changed``. A window in the background probes nothing.
        """
        self._network_poll_ticks += 1
        # A window in the background polls nothing; focusing it reads again.
        if self._network_poll_ticks >= 2 and (self.is_active() or not self._network_statuses):
            self._network_poll_ticks = 0
            self._refresh_private_network_status()
        self._activity_ticks = (self._activity_ticks + 1) % 3
        known = all(service_id in self._service_running for service_id in self._STREAMING_SERVICES)
        wanted: list[str]
        if self._activity_ticks == 0 or not known:
            wanted = list(self._STREAMING_SERVICES)
        else:
            wanted = [service_id for service_id in self._relevant_service_ids() if service_id in self._STREAMING_SERVICES]
        if not self.is_active() and known:
            wanted = []  # in the background nothing is probed again
        self._probe_streaming(wanted)
        return True

    def _probe_streaming(self, wanted: list[str]) -> None:
        """Whether Sunshine and Moonlight run, off the GTK thread, one probe at a time."""
        if not wanted or self._polling_status:
            return
        probes = {
            "sunshine": self.system_check.is_sunshine_running,
            "moonlight": self.system_check.is_moonlight_running,
        }
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
            message = _("Action {action} sent to {service}").format(action=action, service=SERVICE_METADATA["sunshine"]["name"]) if ok else (detail or _("Sunshine is not running."))
            GLib.idle_add(self.show_toast, message)
            GLib.idle_add(self.check_system)

        threading.Thread(target=work, daemon=True).start()

    def on_service_clicked(self, service_id, probe_result=None):
        """Open service control dialog"""
        meta = SERVICE_METADATA.get(service_id)
        if not meta:
            return

        if service_id in ("tailscale", "zerotier", "headscale"):
            # Its own dialog over Share or Connect: the task stays where it was.
            self.open_provider(service_id)
            return

        if probe_result is None:

            def probe_service():
                running_checks = {
                    "sunshine": self.system_check.is_sunshine_running,
                    "moonlight": self.system_check.is_moonlight_running,
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
                GLib.idle_add(
                    self.on_service_clicked,
                    service_id,
                    (is_running, is_enabled, has_unit),
                )

            threading.Thread(target=probe_service, daemon=True).start()
            return

        is_running, is_enabled, has_unit = probe_result

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

        def run_cmd(action, sid=service_id):
            m = SERVICE_METADATA[sid]
            cmd = []

            def find_moonlight():
                for b in ["moonlight-qt", "moonlight"]:
                    if shutil.which(b):
                        return b
                return None

            current_type = m["type"]
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
                    name = m["name"]
                    self.show_toast(_("Action {action} sent to {service}").format(action=action, service=name))
                    dialog.destroy()
                    GLib.timeout_add(1000, self.check_system)
                except Exception as e:
                    self.show_toast(_("Error executing command: {error}").format(error=e))

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

        content.append(actions)

        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        tv.add_top_bar(hb)
        tv.set_content(content)
        dialog.set_content(tv)
        dialog.present()
