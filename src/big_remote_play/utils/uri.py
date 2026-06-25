"""URI/file opening with correct Wayland activation.

A bare `xdg-open` subprocess receives no activation token, so under Wayland the
compositor's focus-stealing prevention opens the target app in the background.
`Gtk.show_uri()` with the requesting toplevel as launcher hands the compositor a
proper activation token, so the browser/handler is raised to the foreground.
"""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gdk, GLib  # type: ignore  # noqa: E402


def _toplevel(widget):
    root = widget.get_root() if widget is not None else None
    return root if isinstance(root, Gtk.Window) else None


def open_uri(widget, uri: str) -> None:
    """Open an http(s)/file URI, transferring focus to the handler app."""
    Gtk.show_uri(_toplevel(widget), uri, Gdk.CURRENT_TIME)


def open_path(widget, path) -> None:
    """Open a local filesystem path (converted to a file:// URI)."""
    open_uri(widget, GLib.filename_to_uri(str(path), None))
