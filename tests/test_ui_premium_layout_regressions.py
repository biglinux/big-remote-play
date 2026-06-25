"""Static guards for the premium layout elements mirrored from the mockups."""

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
    assert "create_logo_widget('big-remote-play', 48)" in source
    assert "service-status-row" in source
    assert "service-icon-frame" in source
