"""Explicit, task-scoped component installation with an interactive terminal."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
try:
    gi.require_version("Vte", "3.91")
    from gi.repository import Vte  # type: ignore

    HAS_VTE = True
except (ValueError, ImportError):
    Vte = None
    HAS_VTE = False
from gi.repository import Gtk, Adw, GLib, Gio  # type: ignore
import os
import shlex
import shutil
import subprocess
from big_remote_play.utils.i18n import _
from .components import intro, note

_INSTALL_PACKAGES = ("sunshine", "moonlight-qt")


def _installer_argv(packages: tuple[str, ...] = _INSTALL_PACKAGES) -> list[str] | None:
    """Only reviewed packages; no --noconfirm, no shell interpolation."""
    if not packages or any(package not in _INSTALL_PACKAGES for package in packages):
        raise ValueError("Unsupported installation package")
    for helper in ("yay", "paru"):
        if shutil.which(helper):
            return [helper, "-S", "--needed", *packages]
    if shutil.which("pacman"):
        return ["pkexec", "pacman", "-S", "--needed", *packages]
    return None


class InstallerWindow(Adw.Window):
    def __init__(self, parent=None, on_success=None, packages=_INSTALL_PACKAGES):
        super().__init__(transient_for=parent, modal=True)
        self.packages = tuple(packages)
        _installer_argv(self.packages)  # validate, never execute on construction
        self.on_success_callback = on_success
        self._running = False
        self._external = False
        self._success_reported = False
        self.set_title(_("Install Dependencies"))
        self.set_default_size(720, 500)
        self.add_css_class("brp-dialog")
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        for edge in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{edge}")(20)
        box.append(intro(_("Prepare this PC"), _("Install only the components needed for your task."), "brp-cloud-symbolic"))
        group = Adw.PreferencesGroup()
        descriptions = {"sunshine": _("Sends your game to the other PC."), "moonlight-qt": _("Receives the game from the other PC.")}
        for package in self.packages:
            group.add(Adw.ActionRow(title="Sunshine" if package == "sunshine" else "Moonlight", subtitle=descriptions[package], use_markup=False))
        box.append(group)
        box.append(note(_("Review the package transaction in the terminal. Your system may ask for a password.")))
        self.frame = Gtk.Frame(vexpand=True, visible=False)
        self.frame.add_css_class("brp-terminal")
        self.frame.set_size_request(-1, 180)
        box.append(self.frame)
        # VTE is initialized only after an authorized installation starts.
        if not HAS_VTE:
            self.textview = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
            scrolled = Gtk.ScrolledWindow(vexpand=True)
            scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scrolled.set_child(self.textview)
            self.frame.set_child(scrolled)
        self.status_label = Gtk.Label(label=_("Ready to install"), wrap=True, xalign=0)
        box.append(self.status_label)
        actions = Gtk.Box(spacing=12, halign=Gtk.Align.END)
        self.close_btn = Gtk.Button(label=_("Close"))
        self.close_btn.connect("clicked", lambda _button: self.close())
        self.install_btn = Gtk.Button(label=_("Install"))
        self.install_btn.add_css_class("suggested-action")
        self.install_btn.connect("clicked", self._on_install)
        actions.append(self.close_btn)
        actions.append(self.install_btn)
        box.append(actions)
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(box)
        toolbar.set_content(scroll)
        self.set_content(toolbar)
        self.connect("close-request", self._on_close)

    def _on_install(self, _button):
        if self._external:
            self._check_external_result()
            return
        self.set_default_size(720, 620)
        self.frame.set_visible(True)
        self.install_btn.set_sensitive(False)
        if HAS_VTE:
            self.start_installation()
        else:
            self.start_external_installation()

    def _on_close(self, _window):
        if self._running:
            self.status_label.set_text(_("Finish or cancel the package transaction in the terminal before closing."))
            return True
        return False

    def _set_fallback_text(self, text):
        if hasattr(self, "textview"):
            self.textview.get_buffer().set_text(text)

    def start_external_installation(self):
        self._running = False
        argv = _installer_argv(self.packages)
        if argv is None:
            self.frame.set_visible(False)
            text = _("Install these packages with your distribution's tools:\n{}").format("\n".join(self.packages))
            self.status_label.set_text(text)
            self._set_fallback_text(text)
            self.install_btn.set_visible(False)
            return
        script = f"{shlex.join(argv)}; echo; echo {shlex.quote(_('Done! Press Enter to close...'))}; read"
        for terminal in (("konsole", "-e"), ("gnome-terminal", "--"), ("xfce4-terminal", "-x"), ("xterm", "-e")):
            if not shutil.which(terminal[0]):
                continue
            try:
                subprocess.Popen([*terminal, "bash", "-c", script])
            except OSError:
                continue
            self._external = True
            self.status_label.set_text(_("Complete the installation in the external terminal, then check again."))
            self.install_btn.set_label(_("Check again"))
            self.install_btn.set_sensitive(True)
            return
        self.status_label.set_text(_("No terminal found. Run this command:\n{}").format(shlex.join(argv)))
        self.install_btn.set_sensitive(True)

    def start_installation(self):
        if Vte is None:
            self.start_external_installation()
            return
        argv = _installer_argv(self.packages)
        if argv is None:
            self.start_external_installation()
            return
        if not hasattr(self, "terminal"):
            self.terminal = Vte.Terminal()
            self.terminal.set_scrollback_lines(1000)
            self.terminal.connect("child-exited", self.on_process_exit)
            self.frame.set_child(self.terminal)
        self._running = True
        self.status_label.set_text(_("Installing {}...").format(", ".join(self.packages)))

        def on_spawn_done(_terminal, _pid, error, _data):
            if error:
                self._running = False
                self.status_label.set_text(_("Error: {}").format(error))
                self.start_external_installation()

        try:
            self.terminal.spawn_async(Vte.PtyFlags.DEFAULT, None, argv, None, GLib.SpawnFlags.SEARCH_PATH, None, -1, Gio.Cancellable(), on_spawn_done, None)
        except Exception:
            self._running = False
            self.start_external_installation()

    def _check_external_result(self):
        from big_remote_play.utils.system_check import SystemCheck

        check = SystemCheck()
        checks = {"sunshine": check.has_sunshine, "moonlight-qt": check.has_moonlight}
        if all(checks[package]() for package in self.packages):
            self.on_success()
        else:
            self.status_label.set_text(_("Components are still missing. Finish the installation, then check again."))

    def on_process_exit(self, _terminal, status):
        self._running = False
        if os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0:
            self.on_success()
        else:
            self.on_failure(os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1)

    def on_success(self):
        self._running = False
        self.status_label.set_text(_("Installation completed successfully!"))
        self.status_label.remove_css_class("error")
        self.status_label.add_css_class("success")
        self.install_btn.set_visible(False)
        self.close_btn.set_label(_("Finish"))
        self.close_btn.add_css_class("suggested-action")
        if self.on_success_callback and not self._success_reported:
            self._success_reported = True
            self.on_success_callback()

    def on_failure(self, code):
        self._running = False
        self.status_label.set_text(_("Installation failed. Exit code: {}").format(code))
        self.status_label.add_css_class("error")
        self.install_btn.set_label(_("Try Again"))
        self.install_btn.set_sensitive(True)
