"""URI/file opening with correct Wayland activation.

A bare `xdg-open` subprocess receives no activation token, so under Wayland the
compositor's focus-stealing prevention opens the target app in the background.
`Gtk.UriLauncher` with the requesting toplevel as parent hands the compositor a
proper activation token, so the browser/handler is raised to the foreground
(``Gtk.show_uri`` did the same and is deprecated since GTK 4.10).
"""

import logging

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib  # type: ignore  # noqa: E402

logger = logging.getLogger(__name__)


def _toplevel(widget):
    root = widget.get_root() if widget is not None else None
    return root if isinstance(root, Gtk.Window) else None


def open_uri(widget, uri: str) -> None:
    """Open an http(s)/file URI, transferring focus to the handler app."""
    launcher = Gtk.UriLauncher.new(uri)

    def finished(source, result) -> None:
        try:
            source.launch_finish(result)
        except GLib.Error as error:  # no handler, or the person dismissed the chooser
            logger.info("could not open %s: %s", uri.split("?", 1)[0], error.message)

    launcher.launch(_toplevel(widget), None, finished)


def open_path(widget, path) -> None:
    """Open a local filesystem path (converted to a file:// URI)."""
    open_uri(widget, GLib.filename_to_uri(str(path), None))
