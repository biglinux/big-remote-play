"""Guards for the low-cognitive-load icon and accessibility pass."""

from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "usr/share/big-remote-play/icons"


def test_custom_task_icons_exist_and_are_distinct() -> None:
    names = (
        "brp-host-symbolic.svg",
        "brp-client-symbolic.svg",
        "brp-service-symbolic.svg",
        "brp-diagnostics-symbolic.svg",
        "brp-library-symbolic.svg",
        "brp-support-symbolic.svg",
        "brp-preferences-symbolic.svg",
        "brp-network-setup-symbolic.svg",
        "brp-network-connect-symbolic.svg",
        "brp-provider-switch-symbolic.svg",
        "brp-address-symbolic.svg",
        "brp-firewall-symbolic.svg",
    )
    contents = []
    for name in names:
        path = ICONS / name
        assert path.is_file(), name
        ET.parse(path)
        contents.append(path.read_text(encoding="utf-8"))
    assert len(contents) == len(set(contents))


def test_symbolic_icons_are_fill_only() -> None:
    # GTK 4.20+ recolours symbolic icons by forcing the theme foreground as
    # `fill` on every shape and drops `stroke`, so a stroked outline renders as
    # a solid blob. Bundled symbolics must ship expanded outlines.
    for path in ICONS.glob("brp-*-symbolic.svg"):
        text = path.read_text(encoding="utf-8")
        assert "stroke" not in text, path.name


def test_bundled_symbolic_names_are_namespaced() -> None:
    # A bundled icon sharing a standard name (computer-symbolic, …) always
    # loses to the user's icon theme, so the artwork would never be shown.
    for path in ICONS.glob("*-symbolic.svg"):
        assert path.name.startswith("brp-"), path.name


def test_previously_duplicated_symbolics_have_distinct_geometry() -> None:
    pairs = (
        ("brp-preferences-system-symbolic.svg", "brp-emblem-system-symbolic.svg"),
        ("brp-computer-symbolic.svg", "brp-network-wired-symbolic.svg"),
        ("brp-computer-symbolic.svg", "brp-video-display-symbolic.svg"),
        ("brp-computer-symbolic.svg", "brp-network-server-symbolic.svg"),
        ("brp-audio-speakers-symbolic.svg", "brp-audio-volume-medium-symbolic.svg"),
    )
    for left, right in pairs:
        assert (ICONS / left).read_text() != (ICONS / right).read_text(), (left, right)


def test_icon_factory_marks_reinforcement_images_as_presentation() -> None:
    source = (ROOT / "src/big_remote_play/utils/icons.py").read_text()
    assert source.count("Gtk.AccessibleRole.PRESENTATION") >= 2


def test_icon_only_buttons_receive_explicit_accessible_names() -> None:
    components = (ROOT / "src/big_remote_play/ui/components.py").read_text()
    private = (ROOT / "src/big_remote_play/ui/private_network_view.py").read_text()
    host = (ROOT / "src/big_remote_play/ui/host_view.py").read_text()
    guest = (ROOT / "src/big_remote_play/ui/guest_view.py").read_text()
    assert "def name_icon_button" in components
    assert "name_icon_button(btn_conn" in private
    assert "name_icon_button(btn_edit" in private
    assert "name_icon_button(btn_del" in private
    assert "name_icon_button(reset_btn" in host
    assert "name_icon_button(reset_btn" in guest


def test_discovered_hosts_use_native_single_selection_and_auto_select_first() -> None:
    source = (ROOT / "src/big_remote_play/ui/guest_view.py").read_text()
    assert "Gtk.SelectionMode.SINGLE" in source
    assert 'connect("row-selected", self._on_host_row_selected)' in source
    # First result by default; a background refresh keeps the chosen one.
    assert "selected = keep_row or first_row" in source
    assert "self.hosts_list.select_row(selected)" in source
    assert "Gtk.CheckButton" not in source.split("def create_host_row_custom", 1)[1].split("def create_manual_page", 1)[0]


def test_pairing_pin_is_four_digits_everywhere_in_primary_ui() -> None:
    host = (ROOT / "src/big_remote_play/ui/host_view.py").read_text()
    guest = (ROOT / "src/big_remote_play/ui/guest_view.py").read_text()
    assert "MOONLIGHT_PAIRING_PIN_LENGTH" in host
    assert "BRP_DISCOVERY_CODE_LENGTH" in guest
    assert "This only finds the computer; it does not pair it." in guest
    assert "Search code" in guest
    assert "Enter exactly four digits" in guest
    assert "six digits" not in host.lower()
    assert "six digits" not in guest.lower()


def test_current_libadwaita_alert_dialogs_replace_deprecated_message_dialogs() -> None:
    for path in (ROOT / "src/big_remote_play/ui").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "Adw.MessageDialog" not in source, path.name


def test_pairing_pin_rejects_overlong_paste_instead_of_silently_truncating() -> None:
    host = (ROOT / "src/big_remote_play/ui/host_view.py").read_text()
    guest = (ROOT / "src/big_remote_play/ui/guest_view.py").read_text()
    assert "set_max_length(MOONLIGHT_PAIRING_PIN_LENGTH)" not in host
    assert "set_max_length(MOONLIGHT_PAIRING_PIN_LENGTH)" not in guest


def test_installer_is_compact_until_terminal_is_needed() -> None:
    source = (ROOT / "src/big_remote_play/ui/installer_window.py").read_text()
    assert "self.set_default_size(720, 500)" in source
    install_block = source.split("def _on_install", 1)[1].split("def _on_close", 1)[0]
    assert "self.set_default_size(720, 620)" in install_block
    assert install_block.index("self.set_default_size(720, 620)") < install_block.index("self.frame.set_visible(True)")


def test_sparse_preferences_and_quality_dialogs_avoid_excess_empty_space() -> None:
    components = (ROOT / "src/big_remote_play/ui/components.py").read_text()
    guest = (ROOT / "src/big_remote_play/ui/guest_view.py").read_text()
    preferences = (ROOT / "src/big_remote_play/ui/preferences.py").read_text()
    assert "height: int = 520" in components
    assert "dialog.set_content_height(height)" in components
    assert "height: int = 660" in components  # sidebar sheets
    assert "height=600," in guest
    assert "height=640," in guest
    assert "height=440," in guest
    # Backup/restore is one short page, not a multi-page preferences window.
    assert "self.set_default_size(640, 520)" in preferences


def test_host_tabs_use_distinct_semantic_icons() -> None:
    source = (ROOT / "src/big_remote_play/ui/host_view.py").read_text()
    assert 'add_titled_with_icon(overview_page, "overview", _("Overview"), "brp-host-symbolic")' in source
    assert 'add_titled_with_icon(config_page, "config", _("Preferences"), "brp-preferences-symbolic")' in source
    assert 'add_titled_with_icon(support_page, "support", _("Support"), "brp-support-symbolic")' in source
    for left, right in (
        ("brp-preferences-symbolic.svg", "brp-support-symbolic.svg"),
        ("brp-network-setup-symbolic.svg", "brp-network-connect-symbolic.svg"),
        ("brp-network-connect-symbolic.svg", "brp-provider-switch-symbolic.svg"),
    ):
        assert (ICONS / left).read_text() != (ICONS / right).read_text(), (left, right)


def test_support_groups_keep_rounded_boxed_list_treatment() -> None:
    host = (ROOT / "src/big_remote_play/ui/host_view.py").read_text()
    css = (ROOT / "usr/share/big-remote-play/ui/style.css").read_text()
    assert host.count('add_css_class("brp-rounded-group")') >= 3
    assert ".brp-rounded-group list.boxed-list" in css


def test_discovery_empty_state_is_text_first_without_large_search_artwork() -> None:
    source = (ROOT / "src/big_remote_play/ui/guest_view.py").read_text()
    block = source.split("def _build_discover_empty_state", 1)[1].split("def create_discover_page", 1)[0]
    assert 'label=_("No game PC found yet")' in block
    assert 'label=_("Search again")' in block
    assert "brp-system-search-symbolic" not in block
    assert "large=True" not in block


def test_private_network_tabs_are_labelled_icon_backed_headerbar_views() -> None:
    source = (ROOT / "src/big_remote_play/ui/main_window.py").read_text()
    assert "self.network_navigation_stack = Adw.ViewStack()" in source
    assert '(_("My network"), "create_private", "brp-network-setup-symbolic")' in source
    assert '(_("Join a network"), "connect_private", "brp-network-connect-symbolic")' in source
    assert '(_("Change service"), "vpn_selector", "brp-provider-switch-symbolic")' in source
    assert 'self.header_title_stack.add_named(self.header_view_switcher, "switcher")' in source
    assert "header.set_title_widget(self.header_title_stack)" in source
    assert "Adw.ViewSwitcherTitle" not in source
    assert "network_toolbar" not in source
    assert "self.context_stack" not in source


def test_every_symbolic_svg_uses_the_gtk_symbolic_foreground_palette() -> None:
    """Symbolic assets use GTK's canonical foreground marker only.

    ``#2e3436`` is not a fixed rendered colour when the asset is loaded through
    GtkIconTheme: GTK recognises it as the symbolic foreground and recolours it
    for light, dark, selected and high-contrast states.
    """
    import re

    for path in sorted(ICONS.glob("*-symbolic.svg")):
        source = path.read_text(encoding="utf-8")
        colors = {color.lower() for color in re.findall(r"#[0-9a-fA-F]{3,8}", source)}
        assert colors == {"#2e3436"}, (path.name, colors)
        assert "currentColor" not in source, path.name


def test_symbolic_icon_factory_uses_gtk_icon_theme() -> None:
    source = (ROOT / "src/big_remote_play/utils/icons.py").read_text(encoding="utf-8")
    assert 'if icon_name.endswith("-symbolic"):' in source
    assert "Gtk.Image.new_from_icon_name(icon_name)" in source
    assert "theme.set_search_path(desired)" in source
    assert "image_widget.set_from_icon_name(icon_name)" in source


def test_icon_colour_hierarchy_is_explicit_and_consistent() -> None:
    components = (ROOT / "src/big_remote_play/ui/components.py").read_text(encoding="utf-8")
    guest = (ROOT / "src/big_remote_play/ui/guest_view.py").read_text(encoding="utf-8")
    main = (ROOT / "src/big_remote_play/ui/main_window.py").read_text(encoding="utf-8")

    # Routine rows default to a monochrome native prefix; accent tiles must be
    # requested explicitly for a major choice.
    assert 'icon_style: Literal["plain", "tile"] = "plain"' in components
    assert 'if icon_style == "tile":' in components
    assert 'css_class="brp-row-icon"' in components
    assert 'icon_style="tile"' in main  # VPN provider selection

    # Adjacent client utility rows now share one visual treatment and use the
    # concrete keyboard metaphor instead of a generic help glyph.
    assert 'set_row_icon(help_row, "brp-input-keyboard-symbolic")' in guest


def test_main_content_headerbar_uses_flat_toolbar_style() -> None:
    source = (ROOT / "src/big_remote_play/ui/main_window.py").read_text(encoding="utf-8")
    setup = source.split("def setup_content", 1)[1].split("def _create_header_menu_button", 1)[0]
    assert "toolbar.set_top_bar_style(Adw.ToolbarStyle.FLAT)" in setup
    assert "Adw.ToolbarStyle.RAISED_BORDER" not in setup


def test_every_bundled_symbolic_is_loaded_as_a_symbolic_paintable() -> None:
    import pytest

    try:
        from gi.repository import Gdk, Gtk
    except ModuleNotFoundError:
        pytest.skip("PyGObject is unavailable in this environment")

    if Gdk.Display.get_default() is None:
        pytest.skip("GTK display required")

    from big_remote_play.utils.icons import _ensure_icon_search_paths

    theme = _ensure_icon_search_paths()
    assert theme is not None
    for path in sorted(ICONS.glob("*-symbolic.svg")):
        name = path.stem
        paintable = theme.lookup_icon(
            name,
            None,
            24,
            1,
            Gtk.TextDirection.NONE,
            Gtk.IconLookupFlags.NONE,
        )
        assert paintable is not None, name
        assert paintable.is_symbolic(), name
