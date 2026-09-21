"""Host browser must use shared native, keyboard-activatable rows."""

from pathlib import Path


def test_host_browser_entries_use_native_actions_with_bound_targets() -> None:
    source = Path("src/big_remote_play/ui/host_view.py").read_text()
    block = source.split("def _create_browse_button", 1)[1].split("def _browse_populate", 1)[0]
    assert ") -> Adw.ActionRow:" in block
    assert "return action_row(title, description, icon_name, lambda: on_activate(target))" in block
    assert "Gtk.GestureClick" not in block
