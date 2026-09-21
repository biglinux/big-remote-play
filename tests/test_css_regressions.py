"""CSS regressions that affect GTK/libadwaita widget internals."""

from pathlib import Path


def test_stylesheet_does_not_pad_libadwaita_bottom_bar_revealer() -> None:
    stylesheet = Path("usr/share/big-remote-play/ui/style.css").read_text()

    assert ".bottom-bar" not in stylesheet
    # No rule may restyle a stock widget by element name.
    for element in ("entry", "levelbar", "button.card", "headerbar", "listview", "row {"):
        assert f"\n{element}" not in stylesheet


def test_stylesheet_does_not_use_negative_letter_spacing() -> None:
    stylesheet = Path("usr/share/big-remote-play/ui/style.css").read_text()

    assert "letter-spacing: -" not in stylesheet
