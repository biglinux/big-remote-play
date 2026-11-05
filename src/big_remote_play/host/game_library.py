"""Sunshine's game library: what Moonlight offers, read and changed safely.

While sharing, Sunshine serves the apps it loaded at start; changes then go
through its API so Moonlight sees them at once. When sharing is off, or the
API does not accept this app's password, the library file is edited
directly (atomically, keeping everything else): Sunshine reads the file
again when the next change is saved and when it starts.

Searching for games never needs Sunshine: it is done by ``GameDetector``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from big_remote_play.utils.game_detector import Game, app_game_key, sunshine_entry

# Where the apps came from / went to.
SERVER = "server"  # Sunshine's API: Moonlight sees the change now
FILE = "file"  # the library file: Moonlight sees it the next time sharing starts

PROTECTED_APPS = frozenset({"Desktop"})


@dataclass(frozen=True)
class LibraryState:
    apps: tuple[dict, ...]
    source: str
    readable: bool = True


@dataclass(frozen=True)
class Change:
    ok: bool
    count: int = 0
    source: str = FILE


def same_app(left: dict, right: dict) -> bool:
    keys = ("name", "cmd", "detached")
    return all(left.get(key) == right.get(key) for key in keys)


class GameLibrary:
    """Runs in a worker: it may wait for the keyring and Sunshine's API."""

    def __init__(self, sunshine, credentials: Callable[[], tuple[str, str] | None]):
        self.sunshine = sunshine
        self.credentials = credentials

    def _server_apps(self) -> tuple[tuple[str, str] | None, list[dict] | None]:
        if not self.sunshine.is_running():
            return None, None
        auth = self.credentials()
        _status, apps = self.sunshine.apps_with_status(auth=auth)
        return auth, apps

    def read(self) -> LibraryState:
        _auth, apps = self._server_apps()
        if apps is not None:
            return LibraryState(tuple(apps), SERVER)
        apps = self.sunshine.read_apps_file()
        return LibraryState(tuple(apps or ()), FILE, readable=apps is not None)

    def add(self, games: list[Game]) -> Change:
        auth, apps = self._server_apps()
        current = apps if apps is not None else self.sunshine.read_apps_file()
        if current is None:
            return Change(False)
        known_keys = {app_game_key(app) for app in current}
        known_names = {str(app.get("name", "")).casefold() for app in current}
        entries = []
        for game in games:
            if game.key in known_keys or game.name.casefold() in known_names:
                continue
            entries.append(sunshine_entry(game))
            known_keys.add(game.key)
            known_names.add(game.name.casefold())
        if not entries:
            return Change(True, 0, SERVER if apps is not None else FILE)
        if apps is not None:
            added = sum(1 for entry in entries if self.sunshine.add_app(entry, auth=auth))
            if added == len(entries):
                return Change(True, added, SERVER)
            # Partly refused: what is missing goes to the file, read again below.
            return self._add_to_file(entries, already=added)
        return self._add_to_file(entries)

    def _add_to_file(self, entries: list[dict], already: int = 0) -> Change:
        added = 0

        def change(apps: list) -> bool:
            nonlocal added
            for entry in entries:
                if not any(isinstance(app, dict) and same_app(app, entry) for app in apps):
                    apps.append(entry)
                    added += 1
            return added > 0

        ok = self.sunshine.edit_apps_file(change)
        return Change(ok, added + already if ok else already, FILE)

    def add_manual(self, name: str, argv: list[str]) -> Change:
        """An app the person typed: ``argv`` was already split and validated."""
        import shlex

        entry = {"name": name, "cmd": shlex.join(argv), "image-path": "", "output": ""}
        auth, apps = self._server_apps()
        if apps is not None and self.sunshine.add_app(entry, auth=auth):
            return Change(True, 1, SERVER)
        return self._add_to_file([entry])

    def remove(self, app: dict) -> Change:
        if app.get("name") in PROTECTED_APPS:
            return Change(False)
        auth, apps = self._server_apps()
        if apps is not None:
            # Sunshine addresses apps by position: find it in the list as it is now.
            index = next((i for i, current in enumerate(apps) if same_app(current, app)), None)
            if index is not None and self.sunshine.delete_app(index, auth=auth):
                return Change(True, 1, SERVER)
        removed = 0

        def change(items: list) -> bool:
            nonlocal removed
            for position, current in enumerate(items):
                if isinstance(current, dict) and same_app(current, app):
                    del items[position]
                    removed = 1
                    return True
            return False

        ok = self.sunshine.edit_apps_file(change)
        return Change(ok and removed == 1, removed, FILE)
