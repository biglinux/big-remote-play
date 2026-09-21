from __future__ import annotations

import sys, os, gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, Gdk, GLib  # type: ignore
from big_remote_play.ui.main_window import MainWindow
from big_remote_play.utils.config import Config
from big_remote_play.utils.logger import Logger
from big_remote_play import __version__, paths

from big_remote_play.utils.i18n import _

ICONS_DIR = str(paths.ICONS_DIR)
IMG_DIR = str(paths.IMG_DIR)


class BigRemotePlayApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="br.com.biglinux.remoteplay", flags=Gio.ApplicationFlags.FLAGS_NONE)
        # Reconcile the pre-2.0 config dir BEFORE anything reads/writes config.
        paths.migrate_legacy_config_dir()
        self.config = Config()
        self.logger = Logger()
        self.window = None

    def do_activate(self):
        if not self.window:
            self.window = MainWindow(application=self, config=self.config)
        self.window.present()

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.setup_icon()
        self.setup_actions()
        self.setup_theme()

    def setup_actions(self):
        actions = [("quit", lambda *_: self.quit()), ("about", self.show_about), ("preferences", self.show_preferences)]
        for name, callback in actions:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

        # Appearance is a stateful choice, so the menu can show which one is on.
        theme = Gio.SimpleAction.new_stateful("theme", GLib.VariantType.new("s"), GLib.Variant.new_string(str(self.config.get("theme", "auto"))))
        theme.connect("activate", self.on_theme_action)
        self.add_action(theme)

    def on_theme_action(self, action, value):
        theme = value.get_string()
        action.set_state(value)
        self.config.set("theme", theme)
        scheme = {"dark": Adw.ColorScheme.FORCE_DARK, "light": Adw.ColorScheme.FORCE_LIGHT}.get(theme, Adw.ColorScheme.DEFAULT)
        Adw.StyleManager.get_default().set_color_scheme(scheme)

    def setup_theme(self):
        sm = Adw.StyleManager.get_default()
        theme = self.config.get("theme", "auto")
        sm.set_color_scheme(Adw.ColorScheme.FORCE_DARK if theme == "dark" else Adw.ColorScheme.FORCE_LIGHT if theme == "light" else Adw.ColorScheme.DEFAULT)
        self.load_custom_css()

    def setup_icon(self):
        display = Gdk.Display.get_default()
        if display is None:
            self.logger.error(_("Could not load icon theme: no GTK display"))
            return
        it = Gtk.IconTheme.get_for_display(display)
        current_paths = list(it.get_search_path())
        bundled_paths = [path for path in (ICONS_DIR, IMG_DIR) if os.path.exists(path)]
        # Bundled artwork is the app's icon vocabulary. Put it first so a
        # same-named system icon cannot silently change the visual language.
        it.set_search_path(bundled_paths + [path for path in current_paths if path not in bundled_paths])
        Gtk.Window.set_default_icon_name(self._application_icon_name())
        self.logger.info(_("Icon and image paths added"))

    @staticmethod
    def _application_icon_name() -> str:
        """Keep installed identity, with the bundled logo for source checkouts."""
        display = Gdk.Display.get_default()
        if display is not None:
            theme = Gtk.IconTheme.get_for_display(display)
            if theme.has_icon("br.com.biglinux.remoteplay"):
                return "br.com.biglinux.remoteplay"
        return "big-remote-play"

    def load_custom_css(self):
        cp = Gtk.CssProvider()
        cp_path = paths.STYLE_CSS
        display = self.window.get_display() if self.window else Gdk.Display.get_default()
        if cp_path.exists() and display is not None:
            cp.load_from_path(str(cp_path))
            Gtk.StyleContext.add_provider_for_display(display, cp, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def show_about(self, *_args):
        story = _(
            "The Story Behind the Project\n\n"
            "Big Remote Play was born from a real story of friendship, determination, and the passion for Free Software.\n\n"
            "Alessandro e Silva Xavier (known as Alessandro) and Alexasandro Pacheco Feliciano (known as Pacheco) wanted to play games together on BigLinux using a feature that only existed on proprietary platforms like Steam Remote Play and GeForce NOW. The problem? These systems are proprietary, locked to their own ecosystems. If a game wasn't available on their platform, it was nearly impossible to play remotely with friends.\n\n"
            "Refusing to accept this limitation, Alessandro and Pacheco embarked on a journey of countless attempts and extensive research. After trying many different approaches, they finally found a working solution by combining multiple free software programs — including Sunshine, Moonlight, scripts, and VPN tools. They had achieved what the proprietary platforms kept locked behind their walls, and the best part: it was Free Software and multi-platform!\n\n"
            "Excited by their success, they started sharing their achievement during their live streams, which generated tremendous enthusiasm from the community. However, there was a catch — the setup was complicated. It required configuring multiple separate solutions: Sunshine, Moonlight, custom scripts, VPN connections... it was a lot for anyone to handle.\n\n"
            "That's when a friend decided to step in and help develop a unified application to simplify the entire process. And so, Big Remote Play was born! 🎉\n\n"
            "An all-in-one application that integrates everything you need for remote cooperative gaming — no proprietary platforms, no restrictions, no limits on which games you can play."
        )

        about = Adw.AboutWindow(
            transient_for=self.window,
            application_name="Big Remote Play",
            application_icon=self._application_icon_name(),
            developer_name="BigLinux Team",
            version=__version__,
            developers=["Rafael Ruscher <rruscher@gmail.com>", "Alexasandro Pacheco Feliciano <@pachecogameroficial>", "Alessandro e Silva Xavier <@alessandro741>"],
            copyright="© 2026 BigLinux",
            license_type=Gtk.License.GPL_3_0,
            website="https://github.com/biglinux/",
            issue_url="https://github.com/biglinux/big-remote-play/issues",
            comments=_("Play together, from anywhere"),
        )
        about.add_css_class("brp-dialog")

        def open_story(_about, uri):
            if uri != "brp:story":
                return False
            dialog = Adw.Dialog(title=_("Project Story"), content_width=640, content_height=600)
            dialog.add_css_class("brp-dialog")
            toolbar = Adw.ToolbarView()
            toolbar.add_top_bar(Adw.HeaderBar())
            label = Gtk.Label(label=story, wrap=True, xalign=0, selectable=True)
            for edge in ("top", "bottom", "start", "end"):
                getattr(label, f"set_margin_{edge}")(24)
            scroll = Gtk.ScrolledWindow(vexpand=True)
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_child(label)
            toolbar.set_content(scroll)
            dialog.set_child(toolbar)
            dialog.present(about)
            return True

        about.connect("activate-link", open_story)
        about.add_link(_("Project Story"), "brp:story")
        about.add_link("System-infotech", "https://www.youtube.com/@System-infotech")
        about.add_link("Youtube (Project Story)", "https://www.youtube.com/watch?v=D2l9o_wXW5M")
        about.present()

    def show_preferences(self, *_args, tab=None):
        window = self.window
        if window is None:
            return
        from big_remote_play.ui.preferences import PreferencesWindow

        pref_win = PreferencesWindow(transient_for=window, config=self.config, initial_tab=tab)

        # Reload GuestView settings when preferences close
        def on_close(*_):
            if hasattr(window, "guest_view") and hasattr(window.guest_view, "load_guest_settings"):
                window.guest_view.load_guest_settings()

            if hasattr(window, "host_view") and hasattr(window.host_view, "load_settings"):
                # Reload config from file first if needed
                if hasattr(window.host_view, "config") and hasattr(window.host_view.config, "load"):
                    window.host_view.config.load()
                window.host_view.load_settings()

        pref_win.connect("close-request", on_close)
        pref_win.present()

    def do_shutdown(self):
        # app.quit() does not emit the window's close-request signal. Share
        # the idempotent cleanup path so menu Quit flushes debounced edits too.
        try:
            if self.window is not None:
                self.window._shutdown_resources()
        finally:
            Adw.Application.do_shutdown(self)
        # Return through GApplication.run(): os._exit(0) would bypass Python
        # cleanup and turn every shutdown into an unconditionally successful
        # process exit. An existing Sunshine session is intentionally retained
        # by HostView.cleanup(), as with closing the window normally.


def main():
    import signal

    signal.signal(signal.SIGINT, signal.SIG_DFL)
    # Without this the AT-SPI/process name is "__main__.py" (from `python -m`).
    GLib.set_prgname("big-remote-play")
    GLib.set_application_name("Big Remote Play")
    return BigRemotePlayApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
