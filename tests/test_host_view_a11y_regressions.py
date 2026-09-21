"""Static a11y guards for the Server page."""

from pathlib import Path


def test_management_rows_are_native_accessible_action_rows() -> None:
    source = Path("src/big_remote_play/ui/host_view.py").read_text()
    management_block = source.split("server_tools_group = Adw.PreferencesGroup()", 1)[1].split(
        "def _create_overview_action_button",
        1,
    )[0]

    assert "Adw.ActionRow" in management_block
    assert "set_activatable(True)" in management_block
    assert 'connect("activated", callback)' in management_block
    assert "Gtk.AccessibleProperty.LABEL" in management_block
    assert "Gtk.AccessibleProperty.DESCRIPTION" in management_block
    assert "Gtk.Button()" not in management_block


def test_server_secret_icon_buttons_have_accessible_names() -> None:
    source = Path("src/big_remote_play/ui/host_view.py").read_text()
    components = Path("src/big_remote_play/ui/components.py").read_text()
    masked_row_block = source.split("def create_masked_row(", 1)[1].split("def toggle_field_visibility", 1)[0]

    # The shared helper supplies both the visible tooltip and the AT-SPI name;
    # this avoids divergent one-off implementations for icon-only controls.
    assert masked_row_block.count("name_icon_button(") == 2
    assert "Gtk.AccessibleProperty.LABEL" in components
    assert "Gtk.AccessibleProperty.DESCRIPTION" in components
    assert '"Reveal {}"' in masked_row_block
    assert '"Copy {}"' in masked_row_block


def test_copy_pin_button_has_accessible_name() -> None:
    source = Path("src/big_remote_play/ui/host_view.py").read_text()
    pin_block = source.split('copy_btn.set_tooltip_text(_("Copy code"))', 1)[1].split(
        'copy_btn.connect("clicked"',
        1,
    )[0]

    assert "Gtk.AccessibleProperty.LABEL" in pin_block
    assert "Gtk.AccessibleProperty.DESCRIPTION" in pin_block
    assert '"Copy code"' in pin_block


def test_pairing_is_numbered_steps_with_one_code_to_type() -> None:
    """Two four-digit codes in opposite directions confused everyone; the page
    now numbers the steps and keeps the discovery code as the fallback."""
    source = Path("src/big_remote_play/ui/host_view.py").read_text()
    assert "Open Connect on the other computer" in source
    assert "Pairing code shown on the other PC" in source
    assert "This PC did not appear in the list?" in source
    assert "def pair_with_entered_pin" in source
    # The full form is the exception, not the routine path.
    assert "self.open_pin_dialog(None, prefill_pin=pin)" in source


def test_server_page_explains_network_requirement_for_guests() -> None:
    source = Path("src/big_remote_play/ui/host_view.py").read_text()
    assert "Share from this PC" in source
    assert "Playing over the internet?" in source
    assert "Same home network? Skip this step." in source
    assert "For different networks, set up internet play." in source
    assert "Open Private Network setup" in source


def test_server_network_details_are_collapsed_diagnostics() -> None:
    source = Path("src/big_remote_play/ui/host_view.py").read_text()
    assert "self.diagnostics_expander = Adw.ExpanderRow()" in source
    assert 'self.diagnostics_expander.set_title(_("Connection information"))' in source
    assert "automatic discovery fails" in source
    assert "self.diagnostics_expander.set_expanded(False)" in source
    assert "self.diagnostics_expander.add_row(row)" in source


def test_paired_devices_are_visible_on_server_overview_page() -> None:
    source = Path("src/big_remote_play/ui/host_view.py").read_text()

    assert "def _create_paired_devices_overview" in source
    assert "Can connect without a new PIN" in source
    assert "Blocked until enabled again" in source
    assert "overview_body.append(self._create_paired_devices_overview())" in source
    assert "open_paired_devices_dialog" in source
    assert "Open paired device management" in source


def test_service_dialog_falls_back_to_the_sunshine_manager_without_a_unit() -> None:
    # Sunshine ships without a systemd unit on some builds; systemctl then has
    # no unit name to act on and the Start button did nothing.
    source = Path("src/big_remote_play/ui/main_window.py").read_text()
    assert 'if sid == "sunshine" and current_type == "service" and not has_unit:' in source
    assert "self._run_sunshine_action(action, dialog)" in source
    assert 'if meta["type"] == "service" and has_unit:' in source  # no Enable/Disable without a unit
