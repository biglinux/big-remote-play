"""“We need to install a few things”: the one installation interface.

``ComponentChecklist`` shows what a task needs with a check or a cross and its
state in words, offers one button that installs exactly what is missing and,
once everything is there, calls ``on_ready`` so the flow continues by itself.
It is used by the task prompt, the guided setup and the private-network
pages, so an installation looks and behaves the same everywhere.

The work runs in :mod:`big_remote_play.utils.dependencies` on a worker; the
widget only shows its result. One installation runs at a time in the whole
application: a second button waits instead of starting another transaction.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango  # type: ignore

from big_remote_play.utils import dependencies
from big_remote_play.utils.i18n import _, ngettext
from big_remote_play.utils.icons import create_icon_widget, set_icon

from .network_common import Worker

# One package transaction at a time, whichever page started it.
_INSTALLING = threading.Lock()


def installation_running() -> bool:
    return _INSTALLING.locked()


class ComponentChecklist(Gtk.Box):
    """What a task needs, what is missing, and one button to install it."""

    __gtype_name__ = "BrpComponentChecklist"

    def __init__(
        self,
        component_ids: Sequence[str],
        *,
        on_ready: Callable[[], object] | None = None,
        install_label: str = "",
        show_heading: bool = True,
        on_busy: Callable[[bool], object] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.add_css_class("brp-checklist")
        self.component_ids = tuple(dict.fromkeys(component_ids))
        self.on_ready = on_ready
        self.on_busy = on_busy
        self.states: list[dependencies.ComponentState] = []
        self.busy = False
        self.ready = False
        # True once this checklist installed something: only then does a
        # caller continue with steps that change something (a sign-in).
        self.installed_here = False
        self._ready_reported = False
        self._worker = Worker()
        self._install_label = install_label or _("Install what's needed")

        self.heading = Gtk.Label(xalign=0, wrap=True, visible=show_heading)
        self.heading.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.heading.add_css_class("title-3")
        self.append(self.heading)

        self.rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.rows.add_css_class("boxed-list")
        self.rows.add_css_class("brp-boxed")
        self.rows.update_property([Gtk.AccessibleProperty.LABEL], [_("Components")])
        self._row_widgets: dict[str, tuple[Adw.ActionRow, Gtk.Image, Gtk.Label]] = {}
        for component_id in self.component_ids:
            component = dependencies.COMPONENTS[component_id]
            row = Adw.ActionRow(title=component.name, subtitle=dependencies.purpose(component_id), use_markup=False)
            row.set_subtitle_lines(0)
            icon = create_icon_widget("brp-media-record-symbolic", size=16, css_class="brp-check-icon")
            icon.set_valign(Gtk.Align.CENTER)
            row.add_prefix(icon)
            state = Gtk.Label(label=_("Checking…"), valign=Gtk.Align.CENTER, wrap=True, justify=Gtk.Justification.RIGHT, max_width_chars=16)
            # Short words: wrap only when there is really no room.
            state.set_natural_wrap_mode(Gtk.NaturalWrapMode.NONE)
            state.add_css_class("caption-heading")
            row.add_suffix(state)
            self.rows.append(row)
            self._row_widgets[component_id] = (row, icon, state)
        self.append(self.rows)

        # The live region: a screen reader hears each step as it happens.
        self.status = Gtk.Label(xalign=0, wrap=True, visible=False)
        self.status.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.status.set_accessible_role(Gtk.AccessibleRole.STATUS)
        self.append(self.status)

        self.button = Gtk.Button(halign=Gtk.Align.START, visible=False)
        self.button.add_css_class("suggested-action")
        self.button.add_css_class("brp-primary")
        inner = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        self.spinner = Adw.Spinner(visible=False)
        self.button_label = Gtk.Label(label=self._install_label, wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=32)
        self.button_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.button_label.set_natural_wrap_mode(Gtk.NaturalWrapMode.NONE)
        inner.append(self.spinner)
        inner.append(self.button_label)
        self.button.set_child(inner)
        self.button.connect("clicked", lambda _button: self.install())
        self.append(self.button)

        # Package names, the method and the package manager's own words: for
        # someone helping, never needed to install.
        self.details = Gtk.Expander(label=_("Technical details"), visible=False)
        details_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.details_summary = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.details_summary.add_css_class("caption")
        details_box.append(self.details_summary)
        self.log = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.log.update_property([Gtk.AccessibleProperty.LABEL], [_("Installation log")])
        log_scroll = Gtk.ScrolledWindow(min_content_height=120, max_content_height=220, propagate_natural_height=True)
        log_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        log_scroll.set_child(self.log)
        log_scroll.add_css_class("brp-terminal")
        details_box.append(log_scroll)
        self.details.set_child(details_box)
        self.append(self.details)

        # Removed from the window (page replaced, window closed): nothing it
        # started may continue a flow afterwards.
        self.connect("unrealize", lambda *_args: self.close())
        self.refresh()

    # ── checking ───────────────────────────────────────────────────────────
    def refresh(self) -> None:
        """Look again at this computer (worker), then show what is missing."""
        if self.busy:
            return
        self.heading.set_label(_("Checking this computer…"))
        for _row, icon, state in self._row_widgets.values():
            state.set_label(_("Checking…"))
            set_icon(icon, "brp-media-record-symbolic")
        ids = self.component_ids
        self._worker.submit(lambda: dependencies.check(ids), self._show_states, failed=lambda _error: self._show_states([]))

    def _show_states(self, states: list[dependencies.ComponentState]) -> None:
        known = {state.id: state for state in states}
        self.states = [known[component_id] for component_id in self.component_ids if component_id in known]
        for component_id, (row, icon, label) in self._row_widgets.items():
            state = known.get(component_id)
            for css in ("success", "warning"):
                icon.remove_css_class(css)
            if state is None:
                label.set_label(_("State unknown"))
                set_icon(icon, "brp-media-record-symbolic")
                row.update_property([Gtk.AccessibleProperty.DESCRIPTION], [_("State unknown")])
                continue
            words = _("Installed") if state.installed else _("Not installed")
            label.set_label(words)
            set_icon(icon, "brp-emblem-ok-symbolic" if state.installed else "dialog-warning-symbolic")
            icon.add_css_class("success" if state.installed else "warning")
            row.update_property([Gtk.AccessibleProperty.DESCRIPTION], [f"{words}. {dependencies.purpose(component_id)}"])
        absent = dependencies.missing(self.states) if len(self.states) == len(self.component_ids) else list(self.component_ids)
        self.ready = not absent
        if self.ready:
            self.heading.set_label(_("Everything is ready"))
            self.button.set_visible(False)
            self._report_ready()
            return
        self.heading.set_label(
            ngettext(
                "{count} component is needed to continue.",
                "{count} components are needed to continue.",
                len(absent),
            ).format(count=len(absent))
        )
        self.button.set_visible(True)
        self.button.set_sensitive(not installation_running())
        self.button_label.set_label(self._install_label)
        if installation_running():
            self._say(_("Another installation is running. Wait until it finishes."))

    # ── installing ─────────────────────────────────────────────────────────
    def install(self) -> None:
        if self.busy or self.ready:
            return
        targets = dependencies.missing(self.states) or list(self.component_ids)
        if not _INSTALLING.acquire(blocking=False):
            self._say(_("Another installation is running. Wait until it finishes."))
            return
        self._set_busy(True)
        self.button.set_sensitive(False)
        self.spinner.set_visible(True)
        self.button_label.set_label(_("Installing…"))
        self._say(_("Confirm with your password in the window that opens."))
        self.log.get_buffer().set_text("")
        self.details.set_visible(True)
        generation = self._worker.generation

        def add_line(text: str) -> bool:
            buffer = self.log.get_buffer()
            buffer.insert(buffer.get_end_iter(), text + "\n")
            if not text.lower().startswith(("error", "warning")):
                self._say(_("Installing…"))
            return False

        def work():
            plan = dependencies.plan_for(targets)
            GLib.idle_add(self._show_plan, plan)
            if plan is None:
                return None
            return dependencies.install(plan, on_line=lambda line: GLib.idle_add(add_line, line))

        def finish(outcome) -> bool:
            _INSTALLING.release()
            self._set_busy(False)
            self.spinner.set_visible(False)
            if generation != self._worker.generation:
                return False  # the page went away; the next visit looks again
            self._installed(outcome, targets)
            return False

        def run() -> None:
            try:
                outcome = work()
            except Exception as error:  # reported in words; the next refresh looks again
                GLib.idle_add(add_line, str(error))
                outcome = None
            GLib.idle_add(finish, outcome)

        threading.Thread(target=run, daemon=True).start()

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        if self.on_busy is not None:
            self.on_busy(busy)

    def _show_plan(self, plan: dependencies.InstallPlan | None) -> bool:
        if plan is None:
            packages = ", ".join(dependencies.COMPONENTS[component_id].package for component_id in self.component_ids)
            self.details_summary.set_label(_("Packages: {packages}").format(packages=packages))
            return False
        method = "Pamac" if plan.method == "pamac" else "pacman (PolicyKit)"
        self.details_summary.set_label(_("Packages: {packages}").format(packages=", ".join(plan.packages)) + "\n" + _("Installed with: {method}").format(method=method))
        return False

    def _installed(self, outcome: dependencies.InstallOutcome | None, targets: Sequence[str]) -> None:
        if outcome is None:
            packages = ", ".join(dependencies.COMPONENTS[component_id].package for component_id in targets)
            self._say(_("Big Remote Play cannot install programs on this system. Install these packages with your software center: {packages}").format(packages=packages))
            self.button.set_visible(False)
            return
        self._show_states(list(outcome.states))
        if outcome.ok:
            self.installed_here = True
            if outcome.service_failed:
                self._say(_("Installed. Its background service did not start yet; the next step starts it again."))
            else:
                self._say(_("All set!"))
            return
        self.button.set_sensitive(True)
        self.button_label.set_label(_("Try again"))
        if outcome.cancelled:
            self._say(_("The installation was cancelled. Nothing was changed."))
        elif outcome.unavailable:
            self._say(
                _("{packages} is not in this system's repositories. On Arch Linux it comes from the AUR or from its own project's package: install it that way, then try again.").format(
                    packages=", ".join(outcome.unavailable)
                )
            )
        else:
            self._say(_("The installation did not finish. Check your internet connection and try again. The technical details say what happened."))

    def _say(self, text: str) -> None:
        self.status.set_label(text)
        self.status.set_visible(bool(text))

    def _report_ready(self) -> None:
        if self._ready_reported or self.on_ready is None:
            return
        self._ready_reported = True
        callback = self.on_ready

        def report() -> bool:
            # A page closed meanwhile continues nothing.
            if not self._worker_closed:
                callback()
            return False

        # After this paint: the person sees the checks turn green first.
        GLib.timeout_add(600, report)

    @property
    def _worker_closed(self) -> bool:
        return self._worker._closed

    def close(self) -> None:
        self._worker.close()


class InstallDialog(Adw.Dialog):
    """The prompt a task shows when something it needs is missing."""

    __gtype_name__ = "BrpInstallDialog"

    def __init__(self, component_ids: Sequence[str], *, title: str, description: str, on_ready: Callable[[], object] | None = None) -> None:
        super().__init__(title=title)
        self.add_css_class("brp-dialog")
        self.set_content_width(560)
        self.set_follows_content_size(True)
        self._continue = on_ready
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        for edge in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{edge}")(20)
        intro = Gtk.Label(label=description, xalign=0, wrap=True)
        intro.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        intro.add_css_class("dim-label")
        box.append(intro)
        # A package transaction cannot be abandoned half-way from here.
        self.checklist = ComponentChecklist(component_ids, on_ready=self._ready, on_busy=lambda busy: self.set_can_close(not busy))
        box.append(self.checklist)
        toolbar.set_content(box)
        self.set_child(toolbar)
        self.connect("closed", lambda *_args: self.checklist.close())

    def _ready(self) -> None:
        callback, self._continue = self._continue, None
        self.force_close()
        if callback is not None:
            callback()


__all__ = ["ComponentChecklist", "InstallDialog", "installation_running"]
