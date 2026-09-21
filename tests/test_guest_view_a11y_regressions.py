"""Static a11y guards for the Connect to Server page."""

from pathlib import Path


def test_connect_settings_are_split_into_native_activatable_rows() -> None:
    source = Path("src/big_remote_play/ui/guest_view.py").read_text()
    for name in ("image_row", "audio_settings_row", "input_settings_row", "host_connection_row"):
        assert f"self.{name} = action_row(" in source
    assert "lambda: self.image_dialog.present(self)" in source
    assert "lambda: self.audio_dialog.present(self)" in source
    assert "lambda: self.input_dialog.present(self)" in source
    assert "lambda: self.host_connection_dialog.present(self)" in source
    assert "open_advanced_client_settings" not in source


def test_shortcuts_title_escapes_pango_markup_and_whole_row_activates() -> None:
    source = Path("src/big_remote_play/ui/guest_view.py").read_text()
    block = source.split('help_title = _("Shortcuts & Instructions")', 1)[1].split('# "Automatic" reads this screen', 1)[0]

    assert "GLib.markup_escape_text(help_title)" in block
    assert "help_row.set_activatable(True)" in block
    assert 'help_row.connect("activated"' in block
    assert 'create_icon_widget("go-next-symbolic"' in block
