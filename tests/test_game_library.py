"""Share → Preferences → Game Library.

Seen on a real game PC: Steam games on a second disk, a Heroic (Epic) game
and Proton/Steam Linux Runtime tools. The old search read the wrong Heroic
files, listed tools by name luck, added every game at once and only opened
while sharing.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from big_remote_play.host.game_library import FILE, SERVER, GameLibrary
from big_remote_play.utils.game_detector import GameDetector, app_game_key, in_library, parse_vdf, sunshine_entry


def _which(available: set[str]):
    return lambda name: f"/usr/bin/{name}" if name in available else None


def _steam_app(steamapps: Path, app_id: str, name: str, installdir: str, flags: int = 4, files: bool = True) -> None:
    steamapps.mkdir(parents=True, exist_ok=True)
    (steamapps / f"appmanifest_{app_id}.acf").write_text(f'"AppState"\n{{\n\t"appid"\t\t"{app_id}"\n\t"name"\t\t"{name}"\n\t"StateFlags"\t\t"{flags}"\n\t"installdir"\t\t"{installdir}"\n}}\n')
    if files:
        (steamapps / "common" / installdir).mkdir(parents=True, exist_ok=True)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    root = home / ".local/share/Steam"
    other = tmp_path / "disk2/SteamLibrary"
    _steam_app(root / "steamapps", "985890", "Streets of Rage 4", "Streets of Rage 4")
    _steam_app(root / "steamapps", "1628350", "Steam Linux Runtime 3.0 (sniper)", "SteamLinuxRuntime_sniper")
    _steam_app(root / "steamapps", "3658110", "Proton 10.0", "Proton 10.0")
    _steam_app(root / "steamapps", "228980", "Steamworks Common Redistributables", "Steamworks Shared")
    _steam_app(root / "steamapps", "111", "Half Downloaded", "Half", flags=1026)
    _steam_app(root / "steamapps", "222", "Files Deleted", "Gone", files=False)
    # A real game whose title has a tool's word in it.
    _steam_app(other / "steamapps", "333", "Runtime Error: The Game", "Runtime Error")
    _steam_app(other / "steamapps", "782330", "DOOM Eternal", "DOOMEternal")
    (root / "steamapps/libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n\t"1"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n\t"2"\n\t{\n\t\t"path"\t\t"/run/media/nobody/Not Mounted/SteamLibrary"\n\t}\n}\n'
        % (root, other)
    )
    heroic = home / ".config/heroic"
    (heroic / "legendaryConfig/legendary").mkdir(parents=True)
    tmnt = tmp_path / "games/TMNT"
    tmnt.mkdir(parents=True)
    (heroic / "legendaryConfig/legendary/installed.json").write_text(
        json.dumps(
            {
                "16a7b366b94c4cf2843dd0dfda4bbd19": {"title": "TMNT: Shredder's Revenge", "install_path": str(tmnt), "is_dlc": False},
                "dlc1": {"title": "A DLC", "install_path": str(tmnt), "is_dlc": True},
            }
        )
    )
    (heroic / "gog_store").mkdir()
    (heroic / "gog_store/installed.json").write_text(json.dumps({"installed": [{"appName": "1207658924", "install_path": str(tmnt)}, {"appName": "gog-redist", "install_path": str(tmnt)}]}))
    (heroic / "store_cache").mkdir()
    (heroic / "store_cache/gog_library.json").write_text(json.dumps({"games": [{"app_name": "1207658924", "title": "Unreal Tournament"}], "cache_time": 1}))
    # Catalog only: never shown as installed.
    (heroic / "store_cache/legendary_library.json").write_text(json.dumps({"library": [{"app_name": "catalog", "title": "Not Installed", "is_installed": False}]}))
    lutris = home / ".local/share/lutris"
    lutris.mkdir(parents=True)
    with sqlite3.connect(lutris / "pga.db") as db:
        db.execute("CREATE TABLE games (id INTEGER, name TEXT, slug TEXT, runner TEXT, directory TEXT, installed INTEGER)")
        db.execute("INSERT INTO games VALUES (7, 'SuperTuxKart', 'supertuxkart', 'linux', '', 1)")
        db.execute("INSERT INTO games VALUES (8, 'Removed Game', 'removed', 'wine', '', 0)")
    return home


def test_only_installed_games_are_found_on_every_library(home):
    games = GameDetector(home, which=_which({"steam", "lutris", "heroic"})).games()
    names = [game.name for game in games]
    assert names == ["DOOM Eternal", "Runtime Error: The Game", "Streets of Rage 4", "SuperTuxKart", "TMNT: Shredder's Revenge", "Unreal Tournament"]


def test_each_game_starts_through_its_launcher_with_a_validated_id(home):
    games = {game.name: game for game in GameDetector(home, which=_which({"steam", "lutris", "heroic"})).games()}
    assert games["DOOM Eternal"].argv == ("steam", "steam://rungameid/782330")
    assert games["SuperTuxKart"].argv == ("lutris", "lutris:rungameid/7")
    assert games["Unreal Tournament"].argv == ("heroic", "heroic://launch?appName=1207658924&runner=gog")
    assert games["TMNT: Shredder's Revenge"].store == "Epic Games"


def test_flatpak_steam_is_found_and_started_through_flatpak(tmp_path):
    home = tmp_path / "home"
    _steam_app(home / ".var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps", "985890", "Streets of Rage 4", "SOR4")
    (home / ".local/share/flatpak/app/com.valvesoftware.Steam").mkdir(parents=True)
    games = GameDetector(home, which=_which({"flatpak"})).games()
    assert [game.argv for game in games] == [("flatpak", "run", "com.valvesoftware.Steam", "steam://rungameid/985890")]


def test_no_launcher_means_no_game_that_cannot_start(home):
    assert [game.launcher for game in GameDetector(home, which=_which(set())).games()] == []


def test_vdf_escapes_and_spaces_in_paths():
    data = parse_vdf('"libraryfolders" { "0" { "path" "/run/media/me/Novo volume/Steam\\\\Lib" } }')
    assert data["libraryfolders"]["0"]["path"] == "/run/media/me/Novo volume/Steam\\Lib"


# ── Sunshine's library ──────────────────────────────────────────────────
class FakeSunshine:
    def __init__(self, tmp_path: Path, running: bool = False, api_status: int = 200):
        self.file = tmp_path / "apps.json"
        self.file.write_text(
            json.dumps({"env": {"PATH": "$(PATH):/opt"}, "apps": [{"name": "Desktop", "cmd": ""}, {"name": "My Emulator", "cmd": "retroarch", "prep-cmd": [{"do": "x", "undo": "y"}]}]})
        )
        self.running = running
        self.api_status = api_status
        self.api_adds: list[dict] = []
        self.api_deletes: list[int] = []

    def is_running(self):
        return self.running

    def apps_with_status(self, auth=None):
        if self.api_status != 200:
            return self.api_status, None
        return 200, self.read_apps_file()

    def add_app(self, entry, auth=None):
        self.api_adds.append(entry)
        return self.edit_apps_file(lambda apps: apps.append(entry) or True)

    def delete_app(self, index, auth=None):
        self.api_deletes.append(index)
        return self.edit_apps_file(lambda apps: bool(apps.pop(index)))

    def read_apps_file(self):
        try:
            return json.loads(self.file.read_text())["apps"]
        except (ValueError, KeyError, TypeError):
            return None

    def edit_apps_file(self, change):
        from big_remote_play.host.sunshine_manager import SunshineHost

        host = SunshineHost.__new__(SunshineHost)
        host.apps_file = lambda: self.file
        return SunshineHost.edit_apps_file(host, change)


def _games(home):
    return GameDetector(home, which=_which({"steam", "lutris", "heroic"})).games()


def test_games_are_added_without_sharing_and_everything_else_is_kept(tmp_path, home):
    sunshine = FakeSunshine(tmp_path)
    library = GameLibrary(sunshine, lambda: None)
    doom = [game for game in _games(home) if game.name == "DOOM Eternal"]
    change = library.add(doom * 2)
    assert (change.ok, change.count, change.source) == (True, 1, FILE)
    data = json.loads(sunshine.file.read_text())
    assert data["env"] == {"PATH": "$(PATH):/opt"}
    assert data["apps"][1]["prep-cmd"] == [{"do": "x", "undo": "y"}]
    assert data["apps"][2]["detached"] == ["setsid steam steam://rungameid/782330"]
    # Adding again changes nothing.
    assert library.add(doom).count == 0 and len(json.loads(sunshine.file.read_text())["apps"]) == 3


def test_while_sharing_games_go_through_sunshine(tmp_path, home):
    sunshine = FakeSunshine(tmp_path, running=True)
    change = GameLibrary(sunshine, lambda: ("u", "p")).add(_games(home)[:2])
    assert (change.ok, change.count, change.source) == (True, 2, SERVER)
    assert len(sunshine.api_adds) == 2


def test_a_refused_password_still_saves_the_games_for_the_next_share(tmp_path, home):
    sunshine = FakeSunshine(tmp_path, running=True, api_status=401)
    change = GameLibrary(sunshine, lambda: None).add(_games(home)[:1])
    assert (change.ok, change.source) == (True, FILE) and sunshine.api_adds == []


def test_an_unreadable_library_is_never_replaced(tmp_path, home):
    sunshine = FakeSunshine(tmp_path)
    sunshine.file.write_text("{broken")
    assert GameLibrary(sunshine, lambda: None).add(_games(home)).ok is False
    assert sunshine.file.read_text() == "{broken"


def test_removing_keeps_the_game_installed_and_desktop_in_place(tmp_path, home):
    sunshine = FakeSunshine(tmp_path, running=True)
    library = GameLibrary(sunshine, lambda: None)
    assert library.remove({"name": "Desktop", "cmd": ""}).ok is False
    assert library.remove({"name": "My Emulator", "cmd": "retroarch"}).ok
    assert sunshine.api_deletes == [1]
    assert [app["name"] for app in json.loads(sunshine.file.read_text())["apps"]] == ["Desktop"]


def test_games_already_in_moonlight_are_recognised_by_their_command(home):
    games = _games(home)
    doom = next(game for game in games if game.name == "DOOM Eternal")
    entry = sunshine_entry(doom) | {"name": "DOOM (renamed by me)"}
    assert app_game_key(entry) == "steam:782330"
    assert in_library(games, [entry]) == {"steam:782330"}
    assert app_game_key({"name": "x", "detached": ["setsid steam steam://rungameid/12"]}) == "steam:12"


# ── the dialog ──────────────────────────────────────────────────────────
import time  # noqa: E402

from test_ui_task_flows import drain, ui as _ui_fixture  # noqa: E402

ui = _ui_fixture


def _wait(condition, seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        drain()
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_the_library_opens_without_sharing_and_adds_only_the_chosen_games(ui, monkeypatch, tmp_path, home):
    from big_remote_play.host.game_library import GameLibrary as Library
    from big_remote_play.ui.game_library_dialog import GameLibraryDialog

    sunshine = FakeSunshine(tmp_path)
    dialog = GameLibraryDialog(Library(sunshine, lambda: None), GameDetector(home, which=_which({"steam", "lutris", "heroic"})))
    try:
        assert _wait(lambda: dialog._games and dialog._library_rows)
        assert len(dialog._games) == 6
        assert not dialog.add_button.get_sensitive()
        dialog.search_entry.set_text("doom")
        assert _wait(lambda: list(dialog._checks) == ["steam:782330"])
        dialog._checks["steam:782330"].set_active(True)
        assert dialog.add_button.get_sensitive()
        dialog.add_button.emit("clicked")
        assert _wait(lambda: "steam:782330" in dialog._present)
        names = [app["name"] for app in json.loads(sunshine.file.read_text())["apps"]]
        assert names == ["Desktop", "My Emulator", "DOOM Eternal"]
    finally:
        dialog.close_workers()
        dialog.force_close()
