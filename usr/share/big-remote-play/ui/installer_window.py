import gi
gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')
try: gi.require_version('Vte', '3.91'); from gi.repository import Vte; HAS_VTE = True
except: HAS_VTE = False
from gi.repository import Gtk, Adw, GLib, Gio
import subprocess, os, shutil
from utils.i18n import _

class InstallerWindow(Adw.Window):
    """Dependency installation window"""
    
    def __init__(self, parent=None, on_success=None):
        super().__init__(transient_for=parent)
        
        self.on_success_callback = on_success
        
        self.set_title(_('Install Dependencies'))
        self.set_default_size(700, 500)
        self.set_modal(True)
        
        # Main content
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        self.set_content(box)
        
        # Header
        header = Gtk.Label(label=_('Installing required components...'))
        header.add_css_class('title-2')
        box.append(header)
        
        # Terminal/Log area frame
        frame = Gtk.Frame()
        frame.set_vexpand(True)
        box.append(frame)
        
        if HAS_VTE:
            self.terminal = Vte.Terminal()
            self.terminal.set_scrollback_lines(1000)
            self.terminal.connect('child-exited', self.on_process_exit)
            frame.set_child(self.terminal)
            
            # Start installation directly
            GLib.idle_add(self.start_installation)
        else:
            # Fallback text view
            scrolled = Gtk.ScrolledWindow()
            self.textview = Gtk.TextView()
            self.textview.set_editable(False)
            self.textview.set_monospace(True)
            self.textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            
            buffer = self.textview.get_buffer()
            buffer.set_text(_("Embedded terminal not available (vte4 not found).\n"
                          "Opening external terminal for installation...\n"
                          "Please wait for the installation to finish in the other window."))
            
            scrolled.set_child(self.textview)
            frame.set_child(scrolled)
            
            # Start external installation
            GLib.idle_add(self.start_external_installation)
        
        # Status/Action area
        self.status_label = Gtk.Label(label=_('Starting...'))
        box.append(self.status_label)
        
        self.close_btn = Gtk.Button(label=_('Cancel'))
        self.close_btn.add_css_class('destructive-action')
        self.close_btn.connect('clicked', lambda b: self.close())
        box.append(self.close_btn)

    def start_external_installation(self):
        cmd = "yay -S --noconfirm --needed sunshine moonlight-qt vte4"
        done_msg = _("Done! Press Enter to close...")
        sc = f'{cmd}; echo "\n{done_msg}"; read'
        terms = [(['konsole', '-e', 'bash', '-c', sc]), (['gnome-terminal', '--', 'bash', '-c', sc]), (['xfce4-terminal', '-x', 'bash', '-c', sc]), (['xterm', '-e', 'bash', '-c', sc])]
        started = False
        for t in terms:
            if shutil.which(t[0]):
                try: subprocess.Popen(t); started = True; break
                except: continue
        if started: self.status_label.set_text(_('Running in external terminal. Restart after completion.')); self.close_btn.set_label(_('Close')); self.close_btn.remove_css_class('destructive-action'); self.close_btn.add_css_class('suggested-action')
        else: self.status_label.set_text(_('Error: No terminal detected.')); self.textview.get_buffer().set_text(_("Execute manually:\n{}").format(cmd))
        
    def start_installation(self):
        self.status_label.set_text(_('Installing...'))
        try:
            self.terminal.spawn_async(Vte.PtyFlags.DEFAULT, None, ['yay', '-S', '--noconfirm', '--needed', 'sunshine', 'moonlight-qt'], None, GLib.SpawnFlags.SEARCH_PATH, None, -1, Gio.Cancellable(), lambda t, p, e, u: (GLib.idle_add(lambda: self.status_label.set_text(_("Error: {}").format(e))) if e else None, GLib.idle_add(self.start_external_installation) if e else None), None)
        except Exception as e: self.status_label.set_text(_('Embedded terminal error: {}. Trying external...').format(e)); GLib.idle_add(self.start_external_installation)

    def on_process_exit(self, terminal, status):
        # status is the exit code
        # VTE usage might return a complex status, need to extract exit code
        # Usually it matches waitpid status
        
        if os.WIFEXITED(status):
            exit_code = os.WEXITSTATUS(status)
            if exit_code == 0:
                self.on_success()
            else:
                self.on_failure(exit_code)
        else:
            self.on_failure(-1)
            
    def on_success(self):
        self.status_label.set_text(_('Installation completed successfully!'))
        self.status_label.remove_css_class('error')
        self.status_label.add_css_class('success')
        
        self.close_btn.set_label(_('Finish'))
        self.close_btn.remove_css_class('destructive-action')
        self.close_btn.add_css_class('suggested-action')
        
        # Update behavior: Close button now just closes, functionality is done
        
        # Trigger parent update
        if self.on_success_callback:
            self.on_success_callback()
            
    def on_failure(self, code):
        self.status_label.set_text(_('Installation failed. Exit code: {}').format(code))
        self.status_label.add_css_class('error')
        self.close_btn.set_label(_('Close'))
