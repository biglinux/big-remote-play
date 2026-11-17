"""Opening links hands the window to the launcher (Wayland activation)."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from big_remote_play.utils import uri  # noqa: E402


class Launcher:
    made: list = []

    def __init__(self, address):
        self.address = address
        self.parent = "unset"

    @classmethod
    def new(cls, address):
        launcher = cls(address)
        cls.made.append(launcher)
        return launcher

    def launch(self, parent, cancellable, callback):
        self.parent = parent


def test_a_link_opens_through_the_uri_launcher_with_its_window(monkeypatch):
    Launcher.made = []
    monkeypatch.setattr(uri.Gtk, "UriLauncher", Launcher)
    window = Gtk.Window()
    button = Gtk.Button()
    window.set_child(button)
    uri.open_uri(button, "https://example.com/help")
    uri.open_uri(None, "https://example.com/other")
    assert [item.address for item in Launcher.made] == ["https://example.com/help", "https://example.com/other"]
    assert Launcher.made[0].parent is window and Launcher.made[1].parent is None
    window.destroy()
