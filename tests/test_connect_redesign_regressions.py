"""Static source guards for the Connect page + Rede Privada novice redesign."""

from pathlib import Path

GUEST = Path("src/big_remote_play/ui/guest_view.py")
MAIN = Path("src/big_remote_play/ui/main_window.py")
PNV = Path("src/big_remote_play/ui/private_network_view.py")
STYLE = Path("usr/share/big-remote-play/ui/style.css")


def test_empty_state_routes_novice_to_real_unlocks() -> None:
    src = GUEST.read_text()
    assert "_build_discover_empty_state" in src
    # Re-scan, Private Network, PIN, Manual all reachable from the empty state.
    assert "_go_to_private_network_setup" in src
    assert 'self.present_other_ways("pin")' in src
    assert 'self.present_other_ways("ip")' in src


def test_empty_state_rows_are_named_by_title_and_subtitle() -> None:
    src = GUEST.read_text()
    block = src.split("def _build_discover_empty_state", 1)[1].split("def create_discover_page", 1)[0]
    # Adw.ActionRow exposes title and subtitle to AT-SPI, so each way out is
    # named without an explicit accessible label.
    assert "self.search_code_row = action_row(" in src
    assert "self.address_row = action_row(" in src
    assert "action_row(" not in block
    factory = Path("src/big_remote_play/ui/components.py").read_text()
    assert "activatable=True, use_markup=False" in factory
    assert 'row.connect("activated"' in factory


def test_quality_choice_starts_at_automatic_inside_the_image_dialog() -> None:
    src = GUEST.read_text()
    assert "self.profile_row = Adw.ComboRow(" in src
    assert "content.append(self.profile_row)" not in src
    assert "self.image_dialog = preferences_dialog(" in src
    assert "self.audio_dialog = preferences_dialog(" in src
    assert "self.input_dialog = preferences_dialog(" in src
    assert "self.host_connection_dialog = preferences_dialog(" in src
    assert "sidebar_dialog(" not in src
    assert "open_advanced_client_settings" not in src
    assert "_quality_summary" in src


def test_service_status_buttons_do_not_shadow_vpn_card_names() -> None:
    src = MAIN.read_text()
    card = Path("src/big_remote_play/ui/service_status_card.py").read_text()
    assert "label_text, desc])" not in src
    # One state per row, spelled out for screen readers instead of colour-only.
    assert "Service status: {}" not in src
    # The reusable card keeps its state in the ActionRow subtitle and AT-SPI
    # description (behaviour: tests/test_service_status_cards.py); the state
    # icon is only redundant visual reinforcement.
    assert "self.set_subtitle(" in card
    assert "Gtk.AccessibleProperty.DESCRIPTION" in card
    assert 'status_list.add_css_class("brp-service-list")' in src
    assert "streaming_presentation(" in src
    assert "provider_presentation(status)" in src
    assert "_refresh_service_state" in src


def test_tailscale_browser_login_is_offered_once() -> None:
    # Signing in is the same act on both private-network pages, so only the
    # join page carries it; "My network" points there instead of repeating
    # the auth key field.
    src = PNV.read_text()
    assert "Sign in with browser" in src
    provider_page = Path("src/big_remote_play/ui/provider_page.py").read_text()
    assert "Auth Key" not in provider_page
    assert "def show_setup" in provider_page
    assert "_run_tailscale_login" not in src


def test_pin_copy_explains_network_dependency_not_specific_vpn() -> None:
    guest_src = GUEST.read_text()
    network_src = Path("src/big_remote_play/utils/network.py").read_text()
    assert "This only finds the computer; it does not pair it." in guest_src
    assert "WHO_HAS_PIN" in network_src
    assert "<broadcast>" in network_src


def test_install_supports_pacman_and_flatpak_detection() -> None:
    sc = Path("src/big_remote_play/utils/system_check.py").read_text()
    assert "def has_pacman" in sc
    assert "flatpak_app_id" in sc
    assert "def tailscale_cmd" in sc
    # has_tailscale / has_zerotier recognise a Flatpak install too.
    assert 'flatpak_app_id("tailscale")' in sc
    assert 'flatpak_app_id("zerotier")' in sc


def test_tailscale_browser_login_opens_url_as_user_not_root() -> None:
    pnv = PNV.read_text()
    # The URL reaches the user's session through Gtk.show_uri (Wayland
    # activation), never through a bare xdg-open subprocess.
    assert "from big_remote_play.utils.uri import open_uri" in pnv
    assert "xdg-open" not in pnv
    assert "open_uri(self, url)" in pnv


def test_no_bigsudo_uses_pkexec_for_cross_distro() -> None:
    accounts = Path("src/big_remote_play/utils/vpn_accounts.py")
    for p in (MAIN, PNV, accounts):
        src = p.read_text()
        assert "bigsudo" not in src, f"{p} still uses bigsudo"
    assert "pkexec" in accounts.read_text()
    # Privilege-elevation scripts no longer reference the BigLinux-only helper.
    for script in Path("usr/share/big-remote-play/scripts").glob("*.sh"):
        assert "bigsudo" not in script.read_text(), script.name
