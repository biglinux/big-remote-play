"""Game Window detection: which open windows are games, named how, kept how.

Process trees and window lists are synthetic; no desktop or /proc of the
developer machine is read.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from big_remote_play.host import game_windows as gw
from big_remote_play.host.game_windows import ProcessInfo, RawWindow

OWN_PID = 99999
UUID = "{11111111-2222-3333-4444-555555555555}"


def uuid(n: int) -> str:
    return "{%08d-2222-3333-4444-555555555555}" % n


def window(n: int, pid: int, *, title: str = "", app_id: str = "", **extra) -> RawWindow:
    fields = {"width": 1280, "height": 720, "normal": True}
    fields.update(extra)
    return RawWindow(backend="kwin", handle=uuid(n), title=title, pid=pid, app_id=app_id, wm_class=app_id, **fields)


def tree(*processes: ProcessInfo):
    table = {info.pid: info for info in processes}
    return table.get, lambda: list(table.values())


def proc(pid: int, ppid: int, name: str, *, exe: str = "", argv0: str = "", cmdline: str = "", **env: str) -> ProcessInfo:
    return ProcessInfo(pid=pid, ppid=ppid, name=name, exe=exe or f"/usr/bin/{name}", argv0=argv0 or name, cmdline=cmdline or name, env=env)


STEAM_CLIENT = proc(100, 1, "steam", exe="/home/u/.local/share/Steam/ubuntu12_32/steam")
REAPER = proc(110, 100, "reaper", cmdline="reaper SteamLaunch AppId=750920 -- /home/u/.local/share/Steam/steamapps/common/Proton 9.0/proton waitforexitandrun game.exe")
PROTON = proc(
    120, 110, "python3", exe="/usr/bin/python3", cmdline="python3 /home/u/.local/share/Steam/steamapps/common/Proton 9.0/proton waitforexitandrun", SteamAppId="750920", STEAM_COMPAT_DATA_PATH="/x"
)
GAME_EXE = proc(
    130, 120, "SOTTR.exe", exe="/home/u/.local/share/Steam/steamapps/common/Proton 9.0/files/bin/wine64-preloader", argv0="Z:\\games\\SOTTR.exe", SteamAppId="750920", STEAM_COMPAT_DATA_PATH="/x"
)
STEAM_NAMES = {"750920": "Shadow of the Tomb Raider"}


def build(raw, read, table=lambda: [], **kwargs):
    kwargs.setdefault("own_pid", OWN_PID)
    return gw.build_game_windows(raw, read=read, table=table, **kwargs)


def test_no_windows_lists_nothing():
    read, table = tree()
    assert build([], read, table) == []


def test_proton_game_is_named_from_the_steam_library_and_tagged():
    read, table = tree(STEAM_CLIENT, REAPER, PROTON, GAME_EXE)
    [item] = build([window(1, 130, title="Shadow of the Tomb Raider v1.0 build 298")], read, table, steam_names=STEAM_NAMES)
    assert item.name == "Shadow of the Tomb Raider"
    assert item.is_game
    assert item.tags() == ["Steam", "Proton"]
    assert item.launch.steam_app_id == "750920"
    assert item.launch.executable == "SOTTR.exe"
    assert item.identity == "steam:750920"


def test_steam_client_window_is_never_offered_even_with_all_windows():
    read, table = tree(STEAM_CLIENT)
    assert build([window(1, 100, title="Steam", app_id="steam")], read, table, include_other_windows=True) == []


@pytest.mark.parametrize(
    "app_id, name",
    [
        ("net.lutris.Lutris", "lutris"),
        ("com.heroicgameslauncher.hgl", "heroic"),
        ("org.kde.plasmashell", "plasmashell"),
        ("br.com.biglinux.remoteplay", "python3.14"),
        ("dev.lizardbyte.app.Sunshine", "sunshine"),
        ("com.moonlight_stream.Moonlight", "moonlight"),
        ("org.kde.krunner", "krunner"),
        ("org.kde.systemsettings", "systemsettings"),
    ],
)
def test_launchers_desktop_and_streaming_windows_are_never_offered(app_id, name):
    read, table = tree(proc(200, 1, name))
    assert build([window(1, 200, title="x", app_id=app_id)], read, table, include_other_windows=True) == []


def test_this_application_is_never_offered_by_pid():
    read, table = tree(proc(OWN_PID, 1, "python3"))
    assert build([window(1, OWN_PID, title="Anything", app_id="anything")], read, table, include_other_windows=True) == []


@pytest.mark.parametrize(
    "flags, reason",
    [
        ({"normal": False}, "not-an-app-window"),
        ({"minimized": True}, "minimized"),
        ({"width": 40}, "too-small"),
    ],
)
def test_dialogs_minimized_and_tiny_windows_are_skipped(flags, reason):
    raw = window(1, 300, title="Game", **flags)
    assert gw.exclusion_reason(raw, proc(300, 1, "game"), own_pid=OWN_PID) == reason


def test_native_game_is_found_by_its_desktop_category_and_named_by_it():
    read, table = tree(proc(400, 1, "supertuxkart"))
    [item] = build(
        [window(1, 400, title="SuperTuxKart 1.4", app_id="supertuxkart")],
        read,
        table,
        categories=lambda app_id: ["Game", "ArcadeGame"] if app_id == "supertuxkart" else [],
        display_name=lambda app_id: "SuperTuxKart",
    )
    assert item.is_game and item.name == "SuperTuxKart" and item.tags() == []


def test_ordinary_apps_appear_only_when_all_windows_are_asked_for():
    read, table = tree(proc(500, 1, "firefox"), proc(501, 1, "konsole"))
    raw = [window(1, 500, title="Web", app_id="firefox"), window(2, 501, title="Terminal", app_id="org.kde.konsole")]
    assert build(raw, read, table) == []
    listed = build(raw, read, table, include_other_windows=True)
    assert {item.window.app_id for item in listed} == {"firefox", "org.kde.konsole"}
    assert not any(item.is_game for item in listed)


def test_games_are_listed_before_other_windows():
    read, table = tree(proc(500, 1, "firefox"), proc(600, 1, "game.exe", exe="/usr/bin/wine64-preloader", argv0="C:\\game.exe"))
    listed = build([window(1, 500, title="A browser", app_id="firefox"), window(2, 600, title="Zed game")], read, table, include_other_windows=True)
    assert [item.is_game for item in listed] == [True, False]


def test_gamescope_window_finds_the_game_below_it():
    read, table = tree(
        STEAM_CLIENT,
        proc(700, 100, "reaper"),
        proc(710, 700, "gamescope", SteamAppId="1091500"),
        proc(720, 710, "python3", cmdline="python3 /x/Proton/proton waitforexitandrun", STEAM_COMPAT_DATA_PATH="/c"),
        proc(730, 720, "Cyberpunk2077.e", exe="/x/wine64-preloader", argv0="Z:\\Cyberpunk2077.exe", SteamAppId="1091500", STEAM_COMPAT_DATA_PATH="/c"),
    )
    [item] = build([window(1, 710, title="gamescope", app_id="gamescope")], read, table, steam_names={"1091500": "Cyberpunk 2077"})
    assert item.name == "Cyberpunk 2077"
    assert item.tags() == ["Steam", "Proton", "Gamescope"]


def test_lutris_wine_game_is_tagged_without_steam():
    read, table = tree(proc(800, 1, "lutris", exe="/usr/bin/python3"), proc(810, 800, "wineserver"), proc(820, 800, "Game.exe", exe="/opt/wine/bin/wine64-preloader", argv0="C:\\Game.exe"))
    [item] = build([window(1, 820, title="My Game")], read, table)
    assert item.tags() == ["Lutris", "Wine"]
    assert item.name == "My Game"


def test_title_change_keeps_the_window_selected():
    read, table = tree(proc(900, 1, "game.exe", exe="/usr/bin/wine64-preloader"))
    before = build([window(1, 900, title="Loading…")], read, table)
    after = build([window(1, 900, title="Main menu")], read, table)
    assert gw.reselect(before[0].key, "", after) is after[0]


def test_closed_window_is_not_kept_and_a_restarted_game_is_found_by_identity():
    read, table = tree(STEAM_CLIENT, REAPER, PROTON, GAME_EXE)
    first = build([window(1, 130, title="SOTTR")], read, table, steam_names=STEAM_NAMES)[0]
    # The game restarted: new window, new PID, same Steam game.
    restarted = ProcessInfo(pid=131, ppid=120, name="SOTTR.exe", exe=GAME_EXE.exe, argv0=GAME_EXE.argv0, env=GAME_EXE.env)
    read, table = tree(STEAM_CLIENT, REAPER, PROTON, restarted)
    again = build([window(2, 131, title="SOTTR")], read, table, steam_names=STEAM_NAMES)
    assert gw.reselect(first.key, "", again) is None
    assert gw.reselect(first.key, first.identity, again) is again[0]


def test_two_copies_of_a_game_are_never_guessed_between():
    read, table = tree(proc(1000, 1, "game.exe", exe="/usr/bin/wine64-preloader", SteamAppId="42"), proc(1001, 1, "game.exe", exe="/usr/bin/wine64-preloader", SteamAppId="42"))
    listed = build([window(1, 1000, title="A"), window(2, 1001, title="B")], read, table)
    assert gw.reselect("", "steam:42", listed) is None


def test_the_same_window_is_listed_once():
    read, table = tree(proc(300, 1, "game.exe", exe="/usr/bin/wine64-preloader"))
    raw = window(1, 300, title="Game")
    assert len(build([raw, raw], read, table)) == 1


def test_process_chain_is_bounded_and_survives_loops():
    read = {5: proc(5, 6, "a"), 6: proc(6, 5, "b")}.get
    assert [info.pid for info in gw.process_chain(5, read)] == [5, 6]
    deep = {pid: proc(pid, pid + 1, "p") for pid in range(2, 100)}.get
    assert len(gw.process_chain(2, deep)) == 16


def test_kwin_answer_is_parsed_defensively():
    payload = json.dumps(
        [
            {
                "id": UUID,
                "caption": "Game",
                "pid": 42,
                "desktopFile": "steam_app_1",
                "resourceClass": "steam_app_1",
                "normal": True,
                "frame": [0, 0, 1282, 752],
                "client": [1, 30, 1280, 720],
                "scale": 1.5,
            },
            {"id": "not-a-uuid", "caption": "x", "frame": [0, 0, 1, 1], "client": [0, 0, 1, 1]},
            {"id": uuid(2), "caption": "broken", "frame": "nope"},
            "garbage",
        ]
    )
    [parsed] = gw.parse_kwin_windows(payload)
    assert (parsed.width, parsed.height, parsed.decoration, parsed.scale) == (1280, 720, (1, 30, 1, 2), 1.5)
    assert gw.parse_kwin_windows("{not json") == []


def test_capture_support_reports_what_is_missing():
    env = {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "KDE"}
    ok = gw.capture_support(env, which=lambda name: f"/usr/bin/{name}", has_element=lambda name: True, sandboxed=False, version=lambda: (2026, 914))
    assert ok.available and ok.backend == "kwin"
    missing = gw.capture_support(env, which=lambda name: None, has_element=lambda name: name != "pipewiresrc", sandboxed=False, version=lambda: None)
    assert missing.problem == "missing-packages" and set(missing.missing) == {"kwin", "gst-plugin-pipewire"}
    gnome = gw.capture_support(
        {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "w", "XDG_CURRENT_DESKTOP": "GNOME"}, which=lambda n: "/x", has_element=lambda n: True, sandboxed=False, version=lambda: None
    )
    assert gnome.problem == "unsupported-session"
    x11 = gw.capture_support({"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}, which=lambda n: "/x", has_element=lambda n: True, sandboxed=False, version=lambda: None)
    assert x11.available and x11.backend == "x11"
    assert gw.capture_support(env, which=lambda n: "/x", has_element=lambda n: True, sandboxed=True).problem == "sandboxed"


def test_only_launcher_markers_are_read_from_a_process_environment(tmp_path):
    root = tmp_path / "4242"
    root.mkdir()
    (root / "stat").write_text("4242 (game.exe) S 1 4242 4242 0 -1 0")
    (root / "cmdline").write_bytes(b"Z:\\game.exe\0")
    (root / "environ").write_bytes(b"SteamAppId=730\0API_TOKEN=secret\0HOME=/home/u\0")
    info = gw.read_process(4242, proc=tmp_path)
    assert info is not None
    assert info.env == {"SteamAppId": "730"}
    assert info.name == "game.exe" and info.ppid == 1


def test_steam_window_class_names_the_game_when_its_environment_is_unreadable():
    read, table = tree(proc(1100, 1, "Game.exe", exe="/x/wine64-preloader", argv0="Z:\\Game.exe"))
    [item] = build([window(1, 1100, title="GAME", app_id="steam_app_381210")], read, table, steam_names={"381210": "Dead by Daylight"})
    assert item.name == "Dead by Daylight" and item.identity == "steam:381210" and item.tags()[0] == "Steam"
    # A non-Steam launch through Proton carries AppID 0: never a Steam game.
    [other] = build([window(2, 1100, title="Notepad", app_id="steam_app_0")], read, table)
    assert other.launch.steam_app_id == "" and other.name == "Notepad"


def test_a_sunshine_without_kwin_capture_is_reported_instead_of_streaming_nothing():
    env = {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "KDE"}
    common = {"which": lambda name: f"/usr/bin/{name}", "has_element": lambda name: True, "sandboxed": False}
    assert gw.capture_support(env, version=lambda: (2025, 924), **common).problem == "sunshine-too-old"
    assert gw.capture_support(env, version=lambda: (2026, 430), **common).problem == "sunshine-too-old"
    assert gw.capture_support(env, version=lambda: (2026, 516), **common).available
    assert gw.capture_support(env, version=lambda: (2026, 1001), **common).available


def test_sunshine_version_is_read_from_its_own_output():
    def run(argv, **kwargs):
        assert argv == ["/usr/bin/sunshine", "--version"]
        return subprocess.CompletedProcess(argv, 0, "[2026-09-30 14:33:13.188]: Info: Sunshine version: 2026.914.233613 commit: 63d35f7\n", "")

    assert gw.sunshine_version(which=lambda name: f"/usr/bin/{name}", run=run) == (2026, 914)
    old = lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "", "Info: Sunshine version: v2025.924.154138\n")  # noqa: E731
    assert gw.sunshine_version(which=lambda name: "/usr/bin/sunshine", run=old) == (2025, 924)
    assert gw.sunshine_version(which=lambda name: None) is None
