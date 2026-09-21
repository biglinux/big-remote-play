"""Static guards for the adaptive premium shell and primary user journeys."""

from pathlib import Path


MAIN = Path("src/big_remote_play/ui/main_window.py")
HOST = Path("src/big_remote_play/ui/host_view.py")
GUEST = Path("src/big_remote_play/ui/guest_view.py")
STYLE = Path("usr/share/big-remote-play/ui/style.css")


def test_application_menu_preserves_button_accessibility_roles() -> None:
    import pytest
    from big_remote_play.ui.main_window import MainWindow
    from gi.repository import Adw, Gdk, Gtk

    if Gdk.Display.get_default() is None:
        pytest.skip("GTK display required")
    Adw.init()
    menu = MainWindow._create_header_menu_button(None)
    # Appearance section, backup/restore, about.
    assert menu.get_popover().get_menu_model().get_n_items() == 3
    assert Gtk.Button(label="Start sharing").get_accessible_role() == Gtk.AccessibleRole.BUTTON


def test_shell_has_product_identity_dynamic_context_and_system_readiness() -> None:
    source = MAIN.read_text()
    stylesheet = STYLE.read_text()

    # The sidebar header carries the title alone; the app icon belongs to the
    # shell, so drawing the logo next to it duplicated the same icon.
    assert 'header.set_title_widget(Adw.WindowTitle(title="Big Remote Play"))' in source
    assert "create_logo_widget" not in source
    assert "self.content_title = Adw.WindowTitle()" in source
    assert "self.header_title_stack = Gtk.Stack()" in source
    assert "header.set_title_widget(self.header_title_stack)" in source
    assert 'def _set_header_title(self, title: str, subtitle: str = "")' in source
    assert "self.content_title.set_title(title)" in source
    assert "self.content_title.set_subtitle(subtitle)" in source

    assert 'status_list.add_css_class("boxed-list")' in source
    assert "service-icon-frame" in source

    # Typography remains owned by libadwaita instead of hard-coded pixel sizes.
    assert "font-size" not in stylesheet
    assert ".sidebar-title" not in stylesheet
    assert ".header-title" not in stylesheet


def test_home_is_a_two_role_decision_without_repeating_the_same_instructions() -> None:
    source = MAIN.read_text()
    welcome = source.split("def create_welcome_page", 1)[1].split("def create_action_card", 1)[0]
    factory = source.split("def _create_home_network_guide", 1)[1].split("def create_welcome_page", 1)[0]
    assert '_("PLAY TOGETHER")' in welcome
    assert "connect the computers to the same network" in welcome
    assert "connect all computers to the same virtual private network" in welcome
    assert welcome.index("main_box.append(self.home_network_action)") < welcome.index("main_box.append(cards_box)")
    assert 'create_logo_widget("big-remote-play", 92)' not in welcome
    assert "self.host_card" in welcome and "self.guest_card" in welcome
    assert "How it works" not in welcome
    assert "Adw.ExpanderRow" not in welcome
    assert '"brp-host-symbolic"' in welcome
    assert '"brp-client-symbolic"' in welcome
    assert "boxed_rows(" in factory and "action_row(" in factory
    assert "Adw.Banner" not in factory
    assert "Set up a virtual private network" in factory
    assert "border-radius: 16px" in STYLE.read_text()
    assert ".role-card:focus-visible" in STYLE.read_text()


def test_missing_dependencies_are_explained_without_blocking_first_use() -> None:
    source = MAIN.read_text()
    dependency_block = source.split("def update_dependency_ui", 1)[1].split(
        "def setup_content",
        1,
    )[0]
    update_status_block = source.split("def update_status", 1)[1].split(
        "def show_toast",
        1,
    )[0]

    assert "self._set_role_card_state" in dependency_block
    # A card whose component is missing names the action it performs, rather
    # than stating a condition and leaving the next step to be guessed.
    card_block = source.split("def _set_role_card_state", 1)[1].split("def _activate_role", 1)[0]
    assert 'label.set_label(_("Needs {}").format(component_name))' in card_block
    assert "Installation needed" not in card_block
    assert "self.host_card.set_sensitive(True)" in dependency_block
    assert "self.guest_card.set_sensitive(True)" in dependency_block
    assert "set_sensitive(False)" not in dependency_block
    assert "MessageDialog" not in update_status_block
    assert "def _activate_role" in source
    role_block = source.split("def _activate_role", 1)[1].split("def on_nav_selected", 1)[0]
    assert "self._service_installed.get(service_id) is not False" in role_block
    assert 'dialog = Adw.AlertDialog(heading=_("Installation needed"))' in role_block
    assert 'dialog.set_body(_("Install {}").format(component_name))' in role_block
    assert 'dialog.add_response("install", _("Install"))' in role_block
    assert "InstallerWindow(parent=self" in role_block


def test_adaptive_layout_stacks_content_and_opens_compact_windows_on_content() -> None:
    source = MAIN.read_text()

    assert 'Adw.BreakpointCondition.parse("max-width: 980sp")' in source
    assert 'Adw.BreakpointCondition.parse("max-width: 720sp")' in source
    assert "def add_compact_layout_setters" in source
    assert 'breakpoint.add_setter(self.welcome_cards_box, "orientation", Gtk.Orientation.VERTICAL)' in source
    assert 'breakpoint.add_setter(self.host_view.overview_hero, "orientation", Gtk.Orientation.VERTICAL)' in source
    # The compact composition is explicitly inherited by the narrower breakpoint.
    assert "add_compact_layout_setters(compact)" in source
    assert "add_compact_layout_setters(narrow)" in source
    assert 'narrow.add_setter(self.split_view, "collapsed", True)' in source
    assert "self.split_view.set_show_content(True)" in source
    assert 'narrow.add_setter(self.welcome_main_box, "margin-start", 12)' in source
    assert 'narrow.add_setter(self.welcome_main_box, "margin-end", 12)' in source


def test_window_size_is_persistent_and_safely_bounded() -> None:
    source = MAIN.read_text()
    app_source = Path("src/big_remote_play/app.py").read_text()

    assert "DEFAULT_WINDOW_WIDTH = 1100" in source
    assert "DEFAULT_WINDOW_HEIGHT = 720" in source
    assert "MIN_SAVED_WINDOW_WIDTH = 360" in source
    assert "MAX_SAVED_WINDOW_WIDTH = 5120" in source
    assert "self._restore_window_size()" in source
    assert "self.set_default_size(width, height)" in source
    assert 'saved_window_config["width"]' in source
    assert 'saved_window_config["height"]' in source
    assert 'self.config.set("window", saved_window_config)' in source
    assert "self._save_window_size()" in source
    assert "MainWindow(application=self, config=self.config)" in app_source
    assert "self.window._shutdown_resources()" in app_source


def test_buttons_do_not_use_pill_shape_for_primary_flows() -> None:
    stylesheet = STYLE.read_text()

    assert "button.pill" not in stylesheet
    for path in [MAIN, HOST, GUEST, Path("src/big_remote_play/ui/private_network_view.py")]:
        assert 'add_css_class("pill")' not in path.read_text()


def test_context_switchers_share_the_native_headerbar_and_adapt_to_a_bottom_bar() -> None:
    source = MAIN.read_text()

    assert "self.header_title_stack = Gtk.Stack()" in source
    assert "self.header_view_switcher = Adw.ViewSwitcher()" in source
    assert "self.header_view_switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)" in source
    assert "header.set_title_widget(self.header_title_stack)" in source
    assert '"host": self.host_view.view_stack' in source
    assert '"network": self.network_navigation_stack' in source
    # Connect is a single page now, so it has a plain title instead of a
    # switcher over methods that were never parallel choices.
    assert "method_stack" not in source
    assert 'self._set_header_title(_("Connect"), _("Play from another PC"))' in source
    assert "self.compact_view_switcher = Adw.ViewSwitcherBar()" in source
    assert "toolbar.add_bottom_bar(self.compact_view_switcher)" in source
    assert 'narrow.connect("apply", self._on_compact_header_apply)' in source
    assert 'narrow.connect("unapply", self._on_compact_header_unapply)' in source
    assert "Adw.ViewSwitcherTitle" not in source

    # Private Network has three labelled, icon-backed destinations in the same
    # header model instead of a custom icon-only row below the headerbar.
    assert '(_("My network"), "create_private", "brp-network-setup-symbolic")' in source
    assert '(_("Join a network"), "connect_private", "brp-network-connect-symbolic")' in source
    assert '(_("Change service"), "vpn_selector", "brp-provider-switch-symbolic")' in source
    assert "self.context_stack" not in source
    assert "network_toolbar" not in source
    assert "host_header_switcher" not in source
    assert "guest_header_switcher" not in source


def test_primary_sections_do_not_repeat_shell_page_headers_inside_content() -> None:
    stylesheet = STYLE.read_text()

    # The shared decorative factories (page headers, step strips, helper cards,
    # difficulty pills, comparison tables) have no caller left and are gone.
    assert not Path("src/big_remote_play/utils/widgets.py").exists()
    assert ".page-header" not in stylesheet
    assert ".page-title" not in stylesheet
    assert ".page-subtitle" not in stylesheet

    for path in [HOST, GUEST, MAIN, Path("src/big_remote_play/ui/private_network_view.py")]:
        assert "create_page_header" not in path.read_text()


def test_host_overview_places_source_before_primary_action_with_state_visible() -> None:
    source = HOST.read_text()
    stylesheet = STYLE.read_text()

    assert 'hero.add_css_class("session-hero")' in source
    assert "self.overview_hero = hero" in source
    assert "self.overview_hero_actions" in source
    assert 'self.overview_state_label.add_css_class("state-pill")' in source
    assert 'self.overview_state_label.add_css_class("offline")' in source
    assert 'self.overview_state_label.add_css_class("online")' in source
    assert "self.overview_hero_actions.append(self.overview_start_button)" not in source
    assert source.index("self.share_controls.append(game_group)") < source.index("self.share_controls.append(self.overview_start_button)")
    assert "overview_body.append(self.overview_start_button)" not in source
    assert ".session-hero" in stylesheet
    assert ".state-pill.online" in stylesheet
    assert ".state-pill.offline" in stylesheet


def test_sharing_button_icon_follows_the_action_it_performs() -> None:
    # While sharing, the button stops it; a play glyph there promised the
    # opposite of what pressing it does.
    source = HOST.read_text()
    assert 'self.overview_start_icon = create_icon_widget("media-playback-start-symbolic", size=16)' in source
    stop_block = source.split('self.overview_start_label.set_label(_("Stop sharing"))', 1)[1].split("\n\n", 1)[0]
    assert 'set_icon(self.overview_start_icon, "media-playback-stop-symbolic")' in stop_block
    start_block = source.split('self.overview_start_label.set_label(_("Start sharing"))', 1)[1].split("\n\n", 1)[0]
    assert 'set_icon(self.overview_start_icon, "media-playback-start-symbolic")' in start_block


def test_share_and_access_keep_native_view_stacks_and_distinct_task_icons() -> None:
    host_source = HOST.read_text()
    guest_source = GUEST.read_text()
    stylesheet = STYLE.read_text()

    assert 'self.view_stack.add_titled_with_icon(overview_page, "overview", _("Overview"), "brp-host-symbolic")' in host_source
    assert 'self.view_stack.add_titled_with_icon(config_page, "config", _("Preferences"), "brp-preferences-symbolic")' in host_source
    assert 'self.view_stack.add_titled_with_icon(support_page, "support", _("Support"), "brp-support-symbolic")' in host_source
    assert "brp-preferences-symbolic" != "brp-support-symbolic"
    assert "support_page.append(self.summary_box)" in host_source
    assert "support_page.append(server_tools_group)" in host_source
    assert "support_page.append(advanced_tools_group)" in host_source
    assert 'server_tools_group.add_css_class("brp-rounded-group")' in host_source
    assert 'advanced_tools_group.add_css_class("brp-rounded-group")' in host_source
    assert 'self.summary_box.add_css_class("brp-rounded-group")' in host_source
    assert ".brp-rounded-group list.boxed-list" in stylesheet
    assert "overview_body.append(self._create_paired_devices_overview())" in host_source
    assert "content.append(self.view_stack)" in host_source
    assert "create_stack_tab_strip(self.view_stack" not in host_source
    assert "create_stack_tab_strip(self.method_stack" not in guest_source
    assert ".server-section-panel" not in stylesheet
    assert ".section-header-tabs" not in stylesheet


def test_private_network_tabs_do_not_duplicate_wizard_stepper_or_headerbar() -> None:
    source = Path("src/big_remote_play/ui/private_network_view.py").read_text()
    connect_build = source.split("class ConnectPage", 1)[1].split("def _present_history", 1)[0]

    assert "create_wizard_stepper" not in connect_build
    assert "toolbar.add_top_bar(header)" not in connect_build
    # One row of tabs, in the headerbar. The join page no longer carries a
    # second switcher whose "Connect" repeated the headerbar's own destination,
    # nor a Status tab listing the devices "My network" already lists.
    assert "create_stack_tab_strip" not in source
    assert "Adw.ViewStack" not in connect_build
    assert "_peers_list" not in source
