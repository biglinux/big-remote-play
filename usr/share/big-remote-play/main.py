import sys, os, gi, locale, gettext
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib, Gdk
from pathlib import Path
from ui.main_window import MainWindow
from utils.config import Config
from utils.logger import Logger

from utils.i18n import _

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
print(f"DEBUG: BASE_DIR: {BASE_DIR}")
sys.path.insert(0, BASE_DIR)
print(f"DEBUG: sys.path[0]: {sys.path[0]}")
ICONS_DIR = os.path.join(BASE_DIR, "icons")

class BigRemotePlayApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id='br.com.biglinux.remoteplay', flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.config = Config()
        self.logger = Logger()
        self.window = None
        
    def do_activate(self):
        if not self.window: self.window = MainWindow(application=self)
        self.window.present()
        
    def do_startup(self):
        Adw.Application.do_startup(self)
        self.setup_icon()
        self.setup_actions()
        self.setup_theme()
        
    def setup_actions(self):
        actions = [('quit', lambda *_: self.quit()), ('about', self.show_about), ('preferences', self.show_preferences)]
        for name, callback in actions:
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', callback)
            self.add_action(action)
            
    def setup_theme(self):
        sm = Adw.StyleManager.get_default(); theme = self.config.get('theme', 'auto')
        sm.set_color_scheme(Adw.ColorScheme.FORCE_DARK if theme == 'dark' else Adw.ColorScheme.FORCE_LIGHT if theme == 'light' else Adw.ColorScheme.DEFAULT)
        self.load_custom_css()
    
    def setup_icon(self):
        if os.path.exists(ICONS_DIR):
            it = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
            it.add_search_path(ICONS_DIR)
            self.logger.info(_("Icon loaded"))
            
    def load_custom_css(self):
        cp = Gtk.CssProvider(); cp_path = Path(__file__).parent / 'ui' / 'style.css'
        if cp_path.exists(): cp.load_from_path(str(cp_path)); Gtk.StyleContext.add_provider_for_display(self.window.get_display() if self.window else Gdk.Display.get_default(), cp, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def show_about(self, *args):
        story = _("The Story Behind the Project\n\n"
                  "Big Remote Play was born from a real story of friendship, determination, and the passion for Free Software.\n\n"
                  "Alessandro e Silva Xavier (known as Alessandro) and Alexasandro Pacheco Feliciano (known as Pacheco) wanted to play games together on BigLinux using a feature that only existed on proprietary platforms like Steam Remote Play and GeForce NOW. The problem? These systems are proprietary, locked to their own ecosystems. If a game wasn't available on their platform, it was nearly impossible to play remotely with friends.\n\n"
                  "Refusing to accept this limitation, Alessandro and Pacheco embarked on a journey of countless attempts and extensive research. After trying many different approaches, they finally found a working solution by combining multiple free software programs — including Sunshine, Moonlight, scripts, and VPN tools. They had achieved what the proprietary platforms kept locked behind their walls, and the best part: it was Free Software and multi-platform!\n\n"
                  "Excited by their success, they started sharing their achievement during their live streams, which generated tremendous enthusiasm from the community. However, there was a catch — the setup was complicated. It required configuring multiple separate solutions: Sunshine, Moonlight, custom scripts, VPN connections... it was a lot for anyone to handle.\n\n"
                  "That's when a friend decided to step in and help develop a unified application to simplify the entire process. And so, Big Remote Play was born! 🎉\n\n"
                  "An all-in-one application that integrates everything you need for remote cooperative gaming — no proprietary platforms, no restrictions, no limits on which games you can play.")

        about = Adw.AboutWindow(
            transient_for=self.window,
            application_name='Big Remote Play',
            application_icon='big-remote-play',
            developer_name='BigLinux Team',
            version='1.1.1',
            developers=['Rafael Ruscher <rruscher@gmail.com>', 'Alexasandro Pacheco Feliciano <@pachecogameroficial>', 'Alessandro e Silva Xavier <@alessandro741>'],
            copyright='© 2026 BigLinux',
            license_type=Gtk.License.GPL_3_0,
            website='https://github.com/biglinux/',
            issue_url='https://github.com/biglinux/big-remote-play/issues',
            comments=story,
        )
        about.add_link("System-infotech", "https://www.youtube.com/@System-infotech")
        about.add_link("Youtube (Project Story)", "https://www.youtube.com/watch?v=D2l9o_wXW5M")
        print(f"DEBUG: About Dialog Version: {about.get_version()}")
        about.present()
        
    def show_preferences(self, *args):
        from ui.preferences import PreferencesWindow
        pref_win = PreferencesWindow(transient_for=self.window, config=self.config)
        
        # Reload GuestView settings when preferences close
        def on_close(*_):
            if hasattr(self.window, 'guest_view') and hasattr(self.window.guest_view, 'load_guest_settings'):
                print("DEBUG: Reloading GuestView settings from Preferences")
                self.window.guest_view.load_guest_settings()
            
            if hasattr(self.window, 'host_view') and hasattr(self.window.host_view, 'load_settings'):
                print("DEBUG: Reloading HostView settings from Preferences")
                # Reload config from file first if needed
                if hasattr(self.window.host_view, 'config') and hasattr(self.window.host_view.config, 'load'):
                    self.window.host_view.config.load()
                self.window.host_view.load_settings()
        
        pref_win.connect('close-request', on_close)
        pref_win.present()
        
    def do_shutdown(self):
        try: Adw.Application.do_shutdown(self)
        except: pass
        os._exit(0)

def main():
    import signal
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    return BigRemotePlayApp().run(sys.argv)

if __name__ == '__main__':
    sys.exit(main())
