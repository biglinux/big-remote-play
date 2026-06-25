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
