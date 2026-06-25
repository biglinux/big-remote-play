"""Static guards for the premium layout elements mirrored from the mockups."""

import re
from pathlib import Path


def test_main_content_pages_use_shared_page_headers() -> None:
    for path in [
        Path("src/big_remote_play/ui/host_view.py"),
        Path("src/big_remote_play/ui/guest_view.py"),
        Path("src/big_remote_play/ui/main_window.py"),
        Path("src/big_remote_play/ui/private_network_view.py"),
    ]:
        assert "create_page_header(" in path.read_text()


def test_sidebar_keeps_brand_identity_and_richer_service_rows() -> None:
    source = Path("src/big_remote_play/ui/main_window.py").read_text()

    assert "_create_sidebar_identity" in source
    assert re.search(r"create_logo_widget\([\"']big-remote-play[\"'], 48\)", source)
    assert "service-status-row" in source
    assert "service-icon-frame" in source


def test_home_does_not_repeat_sidebar_branding() -> None:
    source = Path("src/big_remote_play/ui/main_window.py").read_text()
    welcome_page = source.split("def create_welcome_page", 1)[1].split(
        "def create_action_card",
        1,
    )[0]
    stylesheet = Path("usr/share/big-remote-play/ui/style.css").read_text()

    assert "create_logo_widget('big-remote-play', 72)" not in welcome_page
    assert "hero-title" not in welcome_page
    assert "hero-subtitle" not in welcome_page
    assert "Play cooperatively over the local network or the internet" not in welcome_page
    assert ".hero-title" not in stylesheet
    assert ".hero-subtitle" not in stylesheet
