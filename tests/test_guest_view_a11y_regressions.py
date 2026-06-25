"""Static a11y guards for the Connect to Server page."""

from pathlib import Path


def test_advanced_client_settings_is_a_real_accessible_button() -> None:
    source = Path("src/big_remote_play/ui/guest_view.py").read_text()
    advanced_block = source.split("advanced_title = _('Advanced client settings')", 1)[1].split(
        "content.append(self.switcher_box)",
        1,
    )[0]

    assert "Gtk.Button()" in advanced_block
    assert "Gtk.AccessibleProperty.LABEL" in advanced_block
    assert "Gtk.AccessibleProperty.DESCRIPTION" in advanced_block
    assert "Adw.ActionRow" not in advanced_block
    assert "set_activatable(True)" not in advanced_block
