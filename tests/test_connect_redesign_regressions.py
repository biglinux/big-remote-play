"""Static source guards for the Connect page + Rede Privada novice redesign."""

from pathlib import Path

GUEST = Path("src/big_remote_play/ui/guest_view.py")
MAIN = Path("src/big_remote_play/ui/main_window.py")
PNV = Path("src/big_remote_play/ui/private_network_view.py")


def test_host_scroll_has_breathing_room_and_no_tall_min() -> None:
    src = GUEST.read_text()
    # The host list scroller must not force a tall empty box, and the list must
    # have vertical margins so the boxed-list card corners are not clipped.
    assert "host_scroll.set_min_content_height(120)" in src
    assert "self.hosts_list.set_margin_top(6)" in src
    assert "self.hosts_list.set_margin_bottom(6)" in src


def test_empty_state_routes_novice_to_real_unlocks() -> None:
    src = GUEST.read_text()
    assert "_build_discover_empty_state" in src
    # Re-scan, Private Network, PIN, Manual all reachable from the empty state.
    assert 'navigate_to("vpn_selector")' in src
    assert 'self.method_stack.set_visible_child_name("pin")' in src
    assert 'self.method_stack.set_visible_child_name("manual")' in src


def test_empty_state_buttons_have_accessible_labels() -> None:
    src = GUEST.read_text()
    # The empty-state builder must give its action buttons accessible names
    # (icon/short-label buttons have no inferable AT-SPI name).
    block = src.split("def _build_discover_empty_state", 1)[1].split("def create_discover_page", 1)[0]
    assert "update_property([Gtk.AccessibleProperty.LABEL]" in block


def test_discover_is_single_column_with_guidance() -> None:
    src = GUEST.read_text()
    # Side helper-card column removed from the discover page.
    assert "create_helper_card(" not in src
    # Fixed automatic-discovery guidance subtitle present.
    assert "appears here automatically" in src


def test_client_settings_collapsed_behind_quality_expander() -> None:
    src = GUEST.read_text()
    assert "Adw.ExpanderRow" in src
    assert "_quality_summary" in src
    assert "Adjust quality" in src


def test_vpn_selector_has_role_framing_and_collapsed_comparison() -> None:
    src = MAIN.read_text()
    assert "Gtk.Expander" in src  # comparison table is collapsible
    assert "the one with the game creates" in src
