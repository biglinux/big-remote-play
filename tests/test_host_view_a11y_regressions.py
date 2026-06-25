"""Static a11y guards for the Server page."""

from pathlib import Path


def test_management_rows_are_real_accessible_buttons() -> None:
    source = Path("src/big_remote_play/ui/host_view.py").read_text()
    management_block = source.split("manage_group = Adw.PreferencesGroup()", 1)[1].split(
        "# Getting-started tips",
        1,
    )[0]

    assert "Gtk.Button()" in management_block
    assert "Gtk.AccessibleProperty.LABEL" in management_block
    assert "Gtk.AccessibleProperty.DESCRIPTION" in management_block
    assert "Adw.ActionRow" not in management_block
    assert "set_activatable(True)" not in management_block


def test_server_secret_icon_buttons_have_accessible_names() -> None:
    source = Path("src/big_remote_play/ui/host_view.py").read_text()
    masked_row_block = source.split("def create_masked_row(", 1)[1].split("def toggle_field_visibility", 1)[0]

    assert "Gtk.AccessibleProperty.LABEL" in masked_row_block
    assert "Gtk.AccessibleProperty.DESCRIPTION" in masked_row_block
    assert '"Reveal {}"' in masked_row_block
    assert '"Copy {}"' in masked_row_block


def test_copy_pin_button_has_accessible_name() -> None:
    source = Path("src/big_remote_play/ui/host_view.py").read_text()
    pin_block = source.split('copy_btn.set_tooltip_text(_("Copy PIN"))', 1)[1].split(
        'copy_btn.connect("clicked"',
        1,
    )[0]

    assert "Gtk.AccessibleProperty.LABEL" in pin_block
    assert "Gtk.AccessibleProperty.DESCRIPTION" in pin_block
    assert '"Copy PIN"' in pin_block
