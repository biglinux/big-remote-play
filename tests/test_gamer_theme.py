"""Gamer appearance: menu, persistence and removable CSS provider."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # type: ignore  # noqa: E402

from big_remote_play import paths  # noqa: E402
from big_remote_play.app import BigRemotePlayApp  # noqa: E402
from big_remote_play.ui.main_window import MainWindow  # noqa: E402


def test_gamer_stylesheet_is_a_loadable_separate_resource() -> None:
    assert paths.GAMER_CSS == paths.DATA_DIR / "ui" / "gamer.css"
    assert paths.GAMER_CSS.is_file()
    assert paths.GAMER_CSS != paths.STYLE_CSS

    provider = Gtk.CssProvider()
    provider.load_from_path(str(paths.GAMER_CSS))

    stylesheet = paths.GAMER_CSS.read_text()
    assert "@define-color brp_gamer_violet " in stylesheet
    assert "var(" not in stylesheet
    assert "@media" not in stylesheet
    assert "transition:" not in stylesheet
    assert "animation:" not in stylesheet


def test_unknown_theme_values_fall_back_safely() -> None:
    assert BigRemotePlayApp._normalize_theme("gamer") == "gamer"
    assert BigRemotePlayApp._normalize_theme("auto") == "auto"
    assert BigRemotePlayApp._normalize_theme("light") == "light"
    assert BigRemotePlayApp._normalize_theme("dark") == "dark"
    assert BigRemotePlayApp._normalize_theme("future-theme") == "auto"
    assert BigRemotePlayApp._normalize_theme(None) == "auto"


def test_appearance_menu_order_and_action_targets() -> None:
    if Gdk.Display.get_default() is None:
        pytest.skip("GTK display required")
    Adw.init()
    button = MainWindow._create_header_menu_button(None)
    root = button.get_popover().get_menu_model()
    appearance = root.get_item_link(0, Gio.MENU_LINK_SECTION)
    assert appearance is not None

    labels = []
    targets = []
    for index in range(appearance.get_n_items()):
        labels.append(appearance.get_item_attribute_value(index, Gio.MENU_ATTRIBUTE_LABEL, GLib.VariantType.new("s")).get_string())
        targets.append(appearance.get_item_attribute_value(index, Gio.MENU_ATTRIBUTE_TARGET, GLib.VariantType.new("s")).get_string())

    assert labels == ["Gamer", "Automatic", "Light", "Dark"]
    assert targets == ["gamer", "auto", "light", "dark"]


def test_gamer_provider_lifecycle_and_persistence(tmp_path, monkeypatch) -> None:
    if Gdk.Display.get_default() is None:
        pytest.skip("GTK display required")
    Adw.init()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    app = BigRemotePlayApp()
    manager = Adw.StyleManager.get_default()
    manager.set_color_scheme(Adw.ColorScheme.DEFAULT)
    try:
        app.config.set("theme", "gamer")
        app.setup_actions()
        app.setup_theme()

        action = app.lookup_action("theme")
        assert action.get_state().get_string() == "gamer"
        assert manager.get_color_scheme() == Adw.ColorScheme.FORCE_DARK
        assert app._gamer_css_provider is not None

        first_provider = app._gamer_css_provider
        app.on_theme_action(action, GLib.Variant.new_string("gamer"))
        assert app._gamer_css_provider is first_provider

        app.on_theme_action(action, GLib.Variant.new_string("light"))
        assert app._gamer_css_provider is None
        assert manager.get_color_scheme() == Adw.ColorScheme.FORCE_LIGHT

        app.on_theme_action(action, GLib.Variant.new_string("gamer"))
        assert app._gamer_css_provider is not None
        assert app._gamer_css_provider is not first_provider

        app.on_theme_action(action, GLib.Variant.new_string("auto"))
        assert app._gamer_css_provider is None
        assert manager.get_color_scheme() == Adw.ColorScheme.DEFAULT
        saved = json.loads((tmp_path / "config" / "big-remote-play" / "config.json").read_text())
        assert saved["theme"] == "auto"
    finally:
        app._remove_theme_providers()
        manager.set_color_scheme(Adw.ColorScheme.DEFAULT)


def test_config_persists_gamer_for_the_next_start(fake_home: Path) -> None:
    from big_remote_play.utils.config import Config

    Config().set("theme", "gamer")
    assert Config().get("theme") == "gamer"


def test_high_contrast_suspends_the_gamer_overlay(tmp_path) -> None:
    if Gdk.Display.get_default() is None:
        pytest.skip("GTK display required")
    script = """
import gi
gi.require_version("Adw", "1")
from gi.repository import Adw
from big_remote_play.app import BigRemotePlayApp

Adw.init()
app = BigRemotePlayApp()
try:
    app.config.set("theme", "gamer")
    app.setup_theme()
    assert Adw.StyleManager.get_default().get_high_contrast()
    assert app._selected_theme == "gamer"
    assert app._gamer_css_provider is None
finally:
    app._remove_theme_providers()
"""
    environment = os.environ.copy()
    environment["ADW_DEBUG_HIGH_CONTRAST"] = "1"
    environment["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    environment["PYTHONPATH"] = str(Path.cwd() / "src")
    result = subprocess.run([sys.executable, "-c", script], env=environment, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
