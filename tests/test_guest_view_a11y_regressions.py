"""Static a11y guards for the Connect to Server page."""

import re
from pathlib import Path


def test_advanced_client_settings_is_a_real_accessible_button() -> None:
    source = Path("src/big_remote_play/ui/guest_view.py").read_text()
    match = re.search(
        r"advanced_title = _\([\"']Advanced client settings[\"']\)(?P<block>.*?)content\.append\(self\.switcher_box\)",
        source,
        re.S,
    )
    assert match is not None
    advanced_block = match.group("block")

    assert "Gtk.Button()" in advanced_block
    assert "Gtk.AccessibleProperty.LABEL" in advanced_block
    assert "Gtk.AccessibleProperty.DESCRIPTION" in advanced_block
    assert "Adw.ActionRow" not in advanced_block
    assert "set_activatable(True)" not in advanced_block
