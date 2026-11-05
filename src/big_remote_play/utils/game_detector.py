"""Games installed through Steam, Lutris and Heroic.

Only games that are really installed are returned: a Steam manifest marked
fully installed whose files exist, a Lutris game marked installed, a Heroic
game in the store's own *installed* list whose folder exists. Catalog
entries, tools (Proton, Steam Linux Runtime, redistributables), DLC and
games on a disk that is not mounted are left out.

Each game is launched through its launcher with an ID that was validated
here (digits for Steam and Lutris, a short token for Heroic), never with a
command built from a name or path read from disk.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

_log = logging.getLogger("big-remoteplay")

STEAM_FLATPAK = "com.valvesoftware.Steam"
LUTRIS_FLATPAK = "net.lutris.Lutris"
HEROIC_FLATPAK = "com.heroicgameslauncher.hgl"

# Steam: StateFlags bit for "fully installed" (an update pending keeps it).
_STEAM_INSTALLED = 4
_STEAM_ID = re.compile(r"^\d{1,10}$")
# Folders under steamapps/common that hold tools, not games.
_STEAM_TOOL_DIR = re.compile(r"^(Proton\b.*|SteamLinuxRuntime.*|Steamworks Shared|Steam Controller Configs)$", re.IGNORECASE)
_STEAM_TOOL_IDS = frozenset({"228980", "1070560", "1391110", "1628350", "4183110", "1493710", "2180100", "1826330", "1580130"})
_HEROIC_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_HEROIC_RUNNERS = ("legendary", "gog", "nile", "sideload")
_HEROIC_SKIP = frozenset({"gog-redist"})


@dataclass(frozen=True)
class Game:
    """One installed game and the argv that starts it."""

    launcher: str  # "steam" | "lutris" | "heroic"
    game_id: str
    name: str
    argv: tuple[str, ...]
    runner: str = ""  # Heroic's store: legendary (Epic), gog, nile (Amazon), sideload
    install_dir: str = ""
    extra: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def key(self) -> str:
        """Stable identity, the same across scans and in Sunshine's library."""
        return f"{self.launcher}:{self.runner + ':' if self.runner else ''}{self.game_id}"

    @property
    def platform(self) -> str:
        return {"steam": "Steam", "lutris": "Lutris", "heroic": "Heroic"}[self.launcher]

    @property
    def store(self) -> str:
        """The store behind Heroic (technical identifier, shown untranslated)."""
        return {"legendary": "Epic Games", "gog": "GOG", "nile": "Amazon Games", "sideload": ""}.get(self.runner, "")

    @property
    def cmd(self) -> str:
        """The argv as one string; every part is free of spaces and quotes."""
        return " ".join(self.argv)

    def as_dict(self) -> dict:
        return {"name": self.name, "id": self.game_id, "platform": self.platform, "cmd": self.cmd, "argv": list(self.argv), "icon": self.launcher, "key": self.key, "runner": self.runner}


# ── small parsers ───────────────────────────────────────────────────────
_VDF_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])|//[^\n]*')


def parse_vdf(text: str) -> dict:
    """Valve's KeyValues text format (libraryfolders.vdf, appmanifest_*.acf)."""
    root: dict = {}
    stack = [root]
    key: str | None = None
    for match in _VDF_TOKEN.finditer(text):
        quoted, brace = match.group(1), match.group(2)
        if brace == "{":
            child: dict = {}
            stack[-1][key or ""] = child
            stack.append(child)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        elif quoted is not None:
            value = re.sub(r"\\(.)", r"\1", quoted)
            if key is None:
                key = value
            else:
                stack[-1][key] = value
                key = None
    return root


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        _log.warning("Cannot read game list %s: %s", path, exc)
        return None


def _flatpak_installed(app_id: str, home: Path) -> bool:
    return any((base / "app" / app_id).is_dir() for base in (home / ".local/share/flatpak", Path("/var/lib/flatpak")))


def _safe_word(text: str) -> bool:
    return bool(text) and not any(ch.isspace() or ch in "\"'\\" for ch in text)


# ── detector ────────────────────────────────────────────────────────────
class GameDetector:
    """Detects installed games; every method is safe to call from a worker."""

    def __init__(self, home: Path | None = None, which=shutil.which):
        self.home = home or Path.home()
        self._which = which

    def _launcher(self, binary: str, flatpak_id: str, *, flatpak_preferred: bool = False) -> tuple[str, ...] | None:
        native = self._which(binary)
        flatpak = _flatpak_installed(flatpak_id, self.home) and self._which("flatpak")
        if flatpak and (flatpak_preferred or not native):
            return ("flatpak", "run", flatpak_id)
        if native:
            return (binary,)
        return None

    # The old interface returned dicts; Share and Game Window still read them.
    def detect_all(self) -> list[dict]:
        return [game.as_dict() for game in self.games()]

    def detect_steam(self) -> list[dict]:
        return [game.as_dict() for game in self.steam_games()]

    def detect_lutris(self) -> list[dict]:
        return [game.as_dict() for game in self.lutris_games()]

    def detect_heroic(self) -> list[dict]:
        return [game.as_dict() for game in self.heroic_games()]

    def games(self) -> list[Game]:
        found: list[Game] = []
        for scan in (self.steam_games, self.lutris_games, self.heroic_games):
            try:
                found.extend(scan())
            except Exception as exc:  # one broken launcher never hides the others
                _log.warning("Game detection failed in %s: %s", scan.__name__, exc)
        return sorted(found, key=lambda game: (game.name.casefold(), game.key))

    # ── Steam ───────────────────────────────────────────────────────────
    def steam_roots(self) -> list[tuple[Path, bool]]:
        """Steam data folders, native and Flatpak (``True``), each once."""
        candidates = [
            (self.home / ".local/share/Steam", False),
            (self.home / ".steam/steam", False),
            (self.home / ".steam/root", False),
            (self.home / ".var/app" / STEAM_FLATPAK / ".local/share/Steam", True),
            (self.home / ".var/app" / STEAM_FLATPAK / "data/Steam", True),
        ]
        roots: list[tuple[Path, bool]] = []
        seen: set[Path] = set()
        for path, flatpak in candidates:
            try:
                real = path.resolve()
            except OSError:
                continue
            if real in seen or not (real / "steamapps").is_dir():
                continue
            seen.add(real)
            roots.append((real, flatpak))
        return roots

    def steam_libraries(self) -> Iterator[tuple[Path, bool]]:
        """Every ``steamapps`` folder Steam knows, on any disk, each once."""
        seen: set[Path] = set()
        for root, flatpak in self.steam_roots():
            folders = [root]
            try:
                data = parse_vdf((root / "steamapps/libraryfolders.vdf").read_text(encoding="utf-8", errors="replace"))
            except FileNotFoundError:
                data = {}
            except OSError as exc:
                _log.warning("Cannot read Steam library list: %s", exc)
                data = {}
            libraries = data.get("libraryfolders") or data.get("LibraryFolders") or {}
            for entry in libraries.values() if isinstance(libraries, dict) else ():
                path = entry.get("path") if isinstance(entry, dict) else entry
                if isinstance(path, str) and path.startswith("/"):
                    folders.append(Path(path))
            for folder in folders:
                steamapps = folder / "steamapps"
                try:
                    real = steamapps.resolve()
                    if real in seen or not real.is_dir():
                        continue  # a removable disk that is not mounted
                except OSError:
                    continue
                seen.add(real)
                yield real, flatpak

    def steam_games(self) -> list[Game]:
        launchers = {False: self._launcher("steam", STEAM_FLATPAK), True: ("flatpak", "run", STEAM_FLATPAK) if self._which("flatpak") else None}
        games: dict[str, Game] = {}
        for steamapps, flatpak in self.steam_libraries():
            launcher = launchers[flatpak] or launchers[not flatpak]
            if launcher is None:
                continue
            for manifest in sorted(steamapps.glob("appmanifest_*.acf")):
                try:
                    state = parse_vdf(manifest.read_text(encoding="utf-8", errors="replace")).get("AppState", {})
                except OSError as exc:
                    _log.warning("Cannot read %s: %s", manifest.name, exc)
                    continue
                app_id, name, installdir = str(state.get("appid", "")), str(state.get("name", "")).strip(), str(state.get("installdir", ""))
                try:
                    flags = int(state.get("StateFlags", "0"))
                except ValueError:
                    flags = 0
                if not _STEAM_ID.fullmatch(app_id) or not name or app_id in games:
                    continue
                if app_id in _STEAM_TOOL_IDS or _STEAM_TOOL_DIR.match(installdir):
                    continue
                folder = steamapps / "common" / installdir
                if not flags & _STEAM_INSTALLED or not installdir or not folder.is_dir():
                    continue
                games[app_id] = Game("steam", app_id, name, (*launcher, f"steam://rungameid/{app_id}"), install_dir=str(folder))
        return list(games.values())

    # ── Lutris ──────────────────────────────────────────────────────────
    def lutris_games(self) -> list[Game]:
        sources = [
            (self.home / ".local/share/lutris/pga.db", self._launcher("lutris", LUTRIS_FLATPAK)),
            (self.home / ".var/app" / LUTRIS_FLATPAK / "data/lutris/pga.db", ("flatpak", "run", LUTRIS_FLATPAK) if self._which("flatpak") else None),
        ]
        games: dict[str, Game] = {}
        for database, launcher in sources:
            if launcher is None or not database.is_file():
                continue
            try:
                with sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=2) as connection:
                    rows = connection.execute("SELECT id, name, slug, directory FROM games WHERE installed = 1").fetchall()
            except sqlite3.Error as exc:
                _log.warning("Cannot read the Lutris library: %s", exc)
                continue
            for game_id, name, slug, directory in rows:
                game_id = str(game_id)
                if not game_id.isdigit() or not name or game_id in games:
                    continue
                if directory and not Path(directory).expanduser().exists():
                    continue
                games[game_id] = Game("lutris", game_id, str(name), (*launcher, f"lutris:rungameid/{game_id}"), install_dir=str(directory or ""), extra={"slug": slug or ""})
        return list(games.values())

    # ── Heroic ──────────────────────────────────────────────────────────
    def heroic_roots(self) -> list[tuple[Path, bool]]:
        roots = [(self.home / ".config/heroic", False), (self.home / ".var/app" / HEROIC_FLATPAK / "config/heroic", True)]
        return [(path, flatpak) for path, flatpak in roots if path.is_dir()]

    def heroic_games(self) -> list[Game]:
        games: dict[str, Game] = {}
        for root, flatpak in self.heroic_roots():
            launcher = ("flatpak", "run", HEROIC_FLATPAK) if flatpak and self._which("flatpak") else self._launcher("heroic", HEROIC_FLATPAK)
            if launcher is None:
                launcher = ("xdg-open",) if self._which("xdg-open") else None
            if launcher is None:
                continue
            for runner, app_name, title, install_path in self._heroic_installed(root):
                if not _HEROIC_ID.fullmatch(app_name) or app_name in _HEROIC_SKIP:
                    continue
                if install_path and not Path(install_path).exists():
                    continue
                game = Game("heroic", app_name, title or app_name, (*launcher, f"heroic://launch?appName={app_name}&runner={runner}"), runner=runner, install_dir=install_path)
                games.setdefault(game.key, game)
        return list(games.values())

    def _heroic_titles(self, root: Path, *files: str) -> dict[str, str]:
        titles: dict[str, str] = {}
        for name in files:
            data = _read_json(root / name)
            items = (data.get("games") or data.get("library") or []) if isinstance(data, dict) else data if isinstance(data, list) else []
            for item in items:
                if isinstance(item, dict):
                    key = str(item.get("app_name") or item.get("appName") or "")
                    if key and item.get("title"):
                        titles.setdefault(key, str(item["title"]))
        return titles

    def _heroic_installed(self, root: Path) -> Iterator[tuple[str, str, str, str]]:
        """(runner, app name, title, install path) from each store's own list."""
        epic = _read_json(root / "legendaryConfig/legendary/installed.json")
        if isinstance(epic, dict):
            for app_name, item in epic.items():
                if isinstance(item, dict) and not item.get("is_dlc"):
                    yield "legendary", str(app_name), str(item.get("title") or ""), str(item.get("install_path") or "")

        gog = _read_json(root / "gog_store/installed.json")
        if isinstance(gog, dict) and isinstance(gog.get("installed"), list):
            titles = self._heroic_titles(root, "store_cache/gog_library.json", "gog_store/library.json")
            for item in gog["installed"]:
                if isinstance(item, dict) and not item.get("is_dlc"):
                    app_name = str(item.get("appName") or "")
                    yield "gog", app_name, titles.get(app_name, ""), str(item.get("install_path") or "")

        amazon = _read_json(root / "nile_config/nile/installed.json")
        if isinstance(amazon, list):
            titles = self._heroic_titles(root, "store_cache/nile_library.json")
            for item in amazon:
                if isinstance(item, dict):
                    app_name = str(item.get("id") or "")
                    yield "nile", app_name, titles.get(app_name, ""), str(item.get("path") or "")

        sideload = _read_json(root / "sideload_apps/library.json")
        items = sideload.get("games") if isinstance(sideload, dict) else None
        for item in items if isinstance(items, list) else ():
            if isinstance(item, dict) and item.get("is_installed"):
                install = item.get("install")
                executable = str(install.get("executable") or "") if isinstance(install, dict) else ""
                yield "sideload", str(item.get("app_name") or ""), str(item.get("title") or ""), executable


# ── Sunshine's library ──────────────────────────────────────────────────
_KEY_PATTERNS = (
    (re.compile(r"steam://rungameid/(\d{1,10})"), lambda m: f"steam:{m.group(1)}"),
    (re.compile(r"lutris:rungameid/(\d+)"), lambda m: f"lutris:{m.group(1)}"),
    (re.compile(r"heroic://launch\?appName=([A-Za-z0-9._-]+)&runner=(\w+)"), lambda m: f"heroic:{m.group(2)}:{m.group(1)}"),
    (re.compile(r"heroic://launch/(\w+)/([A-Za-z0-9._-]+)"), lambda m: f"heroic:{m.group(1)}:{m.group(2)}"),
)


def app_game_key(app: dict) -> str | None:
    """The game a Sunshine app starts, read from its commands."""
    commands = [app.get("cmd")]
    detached = app.get("detached")
    commands += detached if isinstance(detached, list) else []
    for command in commands:
        if not isinstance(command, str):
            continue
        for pattern, key in _KEY_PATTERNS:
            match = pattern.search(command)
            if match:
                return key(match)
    return None


def sunshine_entry(game: Game) -> dict:
    """A Sunshine app that starts ``game`` through its launcher.

    Launchers hand the game to their running window and exit at once, so the
    command is *detached* (as Sunshine's own Steam example does) and Sunshine
    does not end the session when the launcher process ends.
    """
    if not all(_safe_word(part) for part in game.argv):
        raise ValueError("unsafe launch command")
    return {"name": game.name, "cmd": "", "detached": [f"setsid {game.cmd}"], "image-path": "", "output": "", "auto-detach": "true"}


def in_library(games: list[Game], apps: list[dict]) -> set[str]:
    """Keys of ``games`` that Sunshine already offers (by command, then by name)."""
    keys = {key for key in (app_game_key(app) for app in apps if isinstance(app, dict)) if key}
    names = {str(app.get("name", "")).casefold() for app in apps if isinstance(app, dict)}
    return {game.key for game in games if game.key in keys or game.name.casefold() in names}
