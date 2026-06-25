"""Static a11y guards for the host file browser dialog."""

from pathlib import Path


def test_host_browser_entries_are_real_accessible_buttons() -> None:
    source = Path("src/big_remote_play/ui/host_view.py").read_text()
    browse_block = source.split("def _browse_populate", 1)[1].split(
        "def _browse_pick",
        1,
    )[0]

    assert "_create_browse_button(" in browse_block
    assert "Gtk.AccessibleProperty.LABEL" in source
    assert "Gtk.AccessibleProperty.DESCRIPTION" in source
    assert "Adw.ActionRow(title=name)" not in browse_block
    assert 'Adw.ActionRow(title=_("Up one level"))' not in browse_block
    assert "set_activatable(True)" not in browse_block
