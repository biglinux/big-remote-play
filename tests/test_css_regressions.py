"""CSS regressions that affect GTK/libadwaita widget internals."""

from pathlib import Path


def test_stylesheet_does_not_pad_libadwaita_bottom_bar_revealer() -> None:
    stylesheet = Path("usr/share/big-remote-play/ui/style.css").read_text()

    assert ".network-info-actions" in stylesheet
    assert ".bottom-bar" not in stylesheet
