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


def test_content_headerbar_does_not_duplicate_page_titles() -> None:
    source = Path("src/big_remote_play/ui/main_window.py").read_text()

    assert "self.content_title" not in source
    assert "Adw.WindowTitle.new" not in source
    assert "set_title_widget(None)" in source


def test_operational_pages_use_compact_headers_without_trivial_subtitles() -> None:
    host_source = Path("src/big_remote_play/ui/host_view.py").read_text()
    guest_source = Path("src/big_remote_play/ui/guest_view.py").read_text()
    widget_source = Path("src/big_remote_play/utils/widgets.py").read_text()

    assert "subtitle: str | None = None" in widget_source
    assert "create_icon_widget(icon_name, size=24)" in widget_source
    assert "_('Share your games')" not in host_source
    assert "_('Connect to a host')" not in guest_source


def test_stack_tabs_use_shared_accessible_tab_strip() -> None:
    widget_source = Path("src/big_remote_play/utils/widgets.py").read_text()
    stylesheet = Path("usr/share/big-remote-play/ui/style.css").read_text()

    assert "def create_stack_tab_strip(" in widget_source
    assert "Adw.InlineViewSwitcher()" in widget_source
    assert 'switcher.add_css_class("round")' in widget_source
    assert "Gtk.AccessibleProperty.LABEL" in widget_source
    assert ".tab-strip" in stylesheet
    assert ".tab-strip:focus-within" in stylesheet

    for path in [
        Path("src/big_remote_play/ui/host_view.py"),
        Path("src/big_remote_play/ui/guest_view.py"),
        Path("src/big_remote_play/ui/private_network_view.py"),
    ]:
        source = path.read_text()
        assert "create_stack_tab_strip(" in source
        assert "Adw.InlineViewSwitcher()" not in source


def test_private_network_tabs_do_not_duplicate_wizard_stepper_or_headerbar() -> None:
    source = Path("src/big_remote_play/ui/private_network_view.py").read_text()
    connect_build = source.split("def _build(self):", 2)[2].split("def _on_instructions_clicked", 1)[0]

    assert "create_wizard_stepper" not in connect_build
    assert "toolbar.add_top_bar(header)" not in connect_build
    assert "create_stack_tab_strip(stack" in connect_build
