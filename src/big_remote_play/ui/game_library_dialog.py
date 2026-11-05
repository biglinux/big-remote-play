"""Share → Preferences → Game Library: installed games offered in Moonlight.

The search for installed games is local and works without sharing. Adding
and removing go through ``GameLibrary``, which uses Sunshine's API while
sharing and the library file otherwise; the page says which applies.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # type: ignore  # noqa: E402

from big_remote_play.host.game_library import FILE, PROTECTED_APPS, SERVER, Change, GameLibrary, LibraryState  # noqa: E402
from big_remote_play.utils.game_detector import Game, GameDetector, in_library  # noqa: E402
from big_remote_play.utils.i18n import _, ngettext  # noqa: E402

from .components import create_icon_widget  # noqa: E402
from .network_common import Worker  # noqa: E402

_FILTERS = ("", "steam", "lutris", "heroic")


def _validated_command(text: str) -> list[str]:
    import shlex

    try:
        argv = shlex.split(text)
    except ValueError:
        return []
    return argv


class GameLibraryDialog(Adw.Dialog):
    def __init__(self, library: GameLibrary, detector: GameDetector, *, toast: Callable[[str], object] | None = None):
        super().__init__(title=_("Game Library"))
        self.add_css_class("brp-dialog")
        self.set_content_width(640)
        self.set_content_height(680)
        self.set_size_request(320, 300)
        self.library = library
        self.detector = detector
        self._toast = toast
        self._games: list[Game] = []
        self._state = LibraryState((), FILE)
        self._present: set[str] = set()
        self._checks: dict[str, Gtk.CheckButton] = {}
        self._library_worker = Worker()
        self._games_worker = Worker()
        self._change_worker = Worker()
        self.connect("closed", lambda *_a: self.close_workers())

        self._toast_overlay = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        self._toast_overlay.set_child(page)
        toolbar.set_content(self._toast_overlay)

        # Bottom bar: the one primary action of this sheet.
        bottom = Gtk.Box(spacing=8, margin_top=8, margin_bottom=8, margin_start=12, margin_end=12)
        self.manual_button = Gtk.Button(label=_("Add Manually…"))
        self.manual_button.connect("clicked", lambda *_a: self._open_manual())
        self.add_button = Gtk.Button(label=_("Add Selected"), hexpand=True, halign=Gtk.Align.END, sensitive=False)
        self.add_button.add_css_class("suggested-action")
        self.add_button.add_css_class("pill")
        self.add_button.connect("clicked", lambda *_a: self._add_selected())
        bottom.append(self.manual_button)
        bottom.append(self.add_button)
        toolbar.add_bottom_bar(bottom)
        self.set_child(toolbar)

        # In Moonlight now.
        self.library_group = Adw.PreferencesGroup(title=_("In Moonlight"))
        self.library_group.set_description(_("Loading…"))
        page.add(self.library_group)
        self._library_rows: list[Gtk.Widget] = []

        # Installed on this computer.
        self.games_group = Adw.PreferencesGroup(title=_("Installed Games"))
        self.games_group.set_description(_("Searching Steam, Lutris and Heroic…"))
        refresh = Gtk.Button(valign=Gtk.Align.CENTER, tooltip_text=_("Search Again"))
        refresh.set_child(create_icon_widget("brp-view-refresh-symbolic", size=16))
        refresh.add_css_class("flat")
        refresh.update_property([Gtk.AccessibleProperty.LABEL], [_("Search Again")])
        refresh.connect("clicked", lambda *_a: self.search_games())
        self.games_group.set_header_suffix(refresh)
        page.add(self.games_group)

        controls = Gtk.Box(spacing=8, margin_bottom=8)
        self.search_entry = Gtk.SearchEntry(hexpand=True, placeholder_text=_("Search games"))
        self.search_entry.update_property([Gtk.AccessibleProperty.LABEL], [_("Search games")])
        self.search_entry.connect("search-changed", lambda *_a: self._render_games())
        # TRANSLATORS: filter of the game list; the others are launcher names.
        self.filter_dropdown = Gtk.DropDown.new_from_strings([_("All"), "Steam", "Lutris", "Heroic"])
        self.filter_dropdown.update_property([Gtk.AccessibleProperty.LABEL], [_("Launcher")])
        self.filter_dropdown.connect("notify::selected", lambda *_a: self._render_games())
        controls.append(self.search_entry)
        controls.append(self.filter_dropdown)
        self.games_group.add(controls)
        self.games_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.games_list.add_css_class("boxed-list")
        self.games_group.add(self.games_list)

        self.refresh_library()
        self.search_games()

    # ── life cycle ──────────────────────────────────────────────────────
    def close_workers(self) -> None:
        for worker in (self._library_worker, self._games_worker, self._change_worker):
            worker.close()

    def _notify(self, text: str) -> None:
        self._toast_overlay.add_toast(Adw.Toast(title=text))

    # ── the library ─────────────────────────────────────────────────────
    def refresh_library(self) -> None:
        self._library_worker.submit(self.library.read, self._show_library, failed=lambda _e: self._show_library(LibraryState((), FILE, readable=False)))

    def _show_library(self, state: LibraryState) -> None:
        self._state = state
        for row in self._library_rows:
            self.library_group.remove(row)
        self._library_rows = []
        if not state.readable:
            self.library_group.set_description(_("The game library file could not be read. It was left as it is; fix or remove it and try again."))
        elif state.source == SERVER:
            self.library_group.set_description(_("Changes appear in Moonlight right away."))
        else:
            self.library_group.set_description(_("Sharing is off. Changes appear in Moonlight the next time you share."))
        apps = [app for app in state.apps if app.get("name")]
        if state.readable and not apps:
            row = Adw.ActionRow(title=_("No games yet"), subtitle=_("Choose installed games below."))
            self.library_group.add(row)
            self._library_rows.append(row)
        for app in apps:
            row = Adw.ActionRow(title=str(app.get("name")), use_markup=False)
            row.set_title_lines(1)
            if app.get("name") in PROTECTED_APPS:
                row.set_subtitle(_("The whole screen"))
            else:
                remove = Gtk.Button(valign=Gtk.Align.CENTER, tooltip_text=_("Remove from Moonlight"))
                remove.set_child(create_icon_widget("brp-trash-symbolic", size=16))
                remove.add_css_class("flat")
                # TRANSLATORS: {name} is a game; the game itself stays installed.
                remove.update_property(
                    [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION], [_("Remove {name} from Moonlight").format(name=app.get("name")), _("The game stays installed.")]
                )
                remove.connect("clicked", lambda _b, item=app: self._remove(item))
                row.add_suffix(remove)
            self.library_group.add(row)
            self._library_rows.append(row)
        self._present = in_library(self._games, list(state.apps))
        self._render_games()

    # ── installed games ─────────────────────────────────────────────────
    def search_games(self) -> None:
        self.games_group.set_description(_("Searching Steam, Lutris and Heroic…"))
        self._games_worker.submit(self.detector.games, self._show_games, failed=lambda _e: self._show_games([]))

    def _show_games(self, games: list[Game]) -> None:
        self._games = games
        self._present = in_library(games, list(self._state.apps))
        count = len(games)
        if count:
            self.games_group.set_description(ngettext("{count} game found on this computer.", "{count} games found on this computer.", count).format(count=count))
        else:
            self.games_group.set_description(_("No installed games were found in Steam (every library), Lutris or Heroic. Games added by hand can be added with Add Manually."))
        self._render_games()

    def _visible_games(self) -> list[Game]:
        query = self.search_entry.get_text().strip().casefold()
        selected = self.filter_dropdown.get_selected()
        launcher = _FILTERS[selected] if 0 <= selected < len(_FILTERS) else ""
        return [game for game in self._games if (not launcher or game.launcher == launcher) and (not query or query in game.name.casefold())]

    def _render_games(self) -> None:
        checked = {key for key, check in self._checks.items() if check.get_active()}
        self.games_list.remove_all()
        self._checks = {}
        visible = self._visible_games()
        if self._games and not visible:
            self.games_list.append(Adw.ActionRow(title=_("No game matches the search")))
        for game in visible:
            row = Adw.ActionRow(title=game.name, use_markup=False)
            platform = f"{game.platform} · {game.store}" if game.store else game.platform
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            check.update_property([Gtk.AccessibleProperty.LABEL], [game.name])
            if game.key in self._present:
                # TRANSLATORS: {platform} is Steam, Lutris or Heroic (with its store).
                row.set_subtitle(_("{platform} · Already in Moonlight").format(platform=platform))
                check.set_active(True)
                check.set_sensitive(False)
            else:
                row.set_subtitle(platform)
                check.set_active(game.key in checked)
                check.connect("toggled", lambda *_a: self._update_add_button())
                self._checks[game.key] = check
                row.set_activatable_widget(check)
            row.add_prefix(check)
            self.games_list.append(row)
        self._update_add_button()

    def _selected(self) -> list[Game]:
        keys = {key for key, check in self._checks.items() if check.get_active()}
        return [game for game in self._games if game.key in keys]

    def _update_add_button(self) -> None:
        count = len(self._selected())
        self.add_button.set_sensitive(count > 0 and self._state.readable)
        self.add_button.set_label(ngettext("Add {count} Game", "Add {count} Games", count).format(count=count) if count else _("Add Selected"))

    # ── changes ─────────────────────────────────────────────────────────
    def _after_change(self, change: Change, success: str) -> None:
        if not change.ok:
            self._notify(_("The game library was not changed. It was left as it was."))
        else:
            self._notify(success if change.source == SERVER else _("{message} It appears in Moonlight the next time you share.").format(message=success))
        self.refresh_library()

    def _add_selected(self) -> None:
        games = self._selected()
        if not games:
            return
        self.add_button.set_sensitive(False)
        self._change_worker.submit(
            lambda: self.library.add(games),
            lambda change: self._after_change(change, ngettext("{count} game added.", "{count} games added.", change.count).format(count=change.count)),
            failed=lambda _e: self._after_change(Change(False), ""),
        )

    def _remove(self, app: dict) -> None:
        self._change_worker.submit(
            lambda: self.library.remove(app),
            lambda change: self._after_change(change, _("{name} was removed from Moonlight.").format(name=app.get("name"))),
            failed=lambda _e: self._after_change(Change(False), ""),
        )

    def _open_manual(self) -> None:
        dialog = Adw.AlertDialog(heading=_("Add a Game Manually"), body=_("Type the name shown in Moonlight and the command that starts the game."))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        group = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        group.add_css_class("boxed-list")
        name_row = Adw.EntryRow(title=_("Name"))
        cmd_row = Adw.EntryRow(title=_("Command"))
        group.append(name_row)
        group.append(cmd_row)
        problem = Gtk.Label(xalign=0, wrap=True, visible=False)
        problem.add_css_class("error")
        box.append(group)
        box.append(problem)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("add", _("Add"))
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("add")
        dialog.set_close_response("cancel")
        dialog.set_response_enabled("add", False)

        def validate(*_args) -> None:
            name, argv = name_row.get_text().strip(), _validated_command(cmd_row.get_text().strip())
            bad_command = bool(cmd_row.get_text().strip()) and not argv
            problem.set_visible(bad_command)
            problem.set_label(_("The command is not valid. Check its quotation marks.") if bad_command else "")
            dialog.set_response_enabled("add", bool(name) and bool(argv))

        name_row.connect("changed", validate)
        cmd_row.connect("changed", validate)

        def answered(_dialog, response: str) -> None:
            if response != "add":
                return
            name, argv = name_row.get_text().strip(), _validated_command(cmd_row.get_text().strip())
            if name and argv:
                self._change_worker.submit(
                    lambda: self.library.add_manual(name, argv),
                    lambda change: self._after_change(change, _("{name} was added.").format(name=name)),
                    failed=lambda _e: self._after_change(Change(False), ""),
                )

        dialog.connect("response", answered)
        dialog.present(self)
