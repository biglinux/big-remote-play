"""Open game windows that can be shared on their own.

Three different things, kept apart on purpose:

- an *installed* game (``utils.game_detector``) may not be running;
- a *running* game is a process tree (Steam → Proton → ``game.exe``);
- a *capturable window* is one top-level window of that tree.

This module lists capturable windows (KWin scripting on Wayland, EWMH on X11),
works out which ones belong to games from their process tree, and gives each
one a friendly name. Every decision is a pure function of plain data, so it is
tested without a desktop.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile

_log = logging.getLogger("big-remoteplay")

APP_ID = "br.com.biglinux.remoteplay"
KWIN_UUID_RE = re.compile(r"^\{[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}$")
_DBUS_UNIQUE_RE = re.compile(r"^:[0-9]+\.[0-9]+$")
_STEAM_APP_ID_RE = re.compile(r"^[1-9][0-9]{0,9}$")
# Steam gives every game window (Proton included) the class "steam_app_<AppID>".
_STEAM_CLASS_RE = re.compile(r"^steam_app_([1-9][0-9]{0,9})$")

# Processes whose own windows are desktop infrastructure or launchers, never a
# game picture. A game launched *by* Steam/Lutris/Heroic has its own process,
# so it is not affected. Lower-case names: process name, WM class or app id.
_NEVER_GAMES = frozenset(
    {
        # KDE Plasma shell and system tools
        "plasmashell",
        "org.kde.plasmashell",
        "krunner",
        "org.kde.krunner",
        "systemsettings",
        "org.kde.systemsettings",
        "kwin_wayland",
        "kwin_x11",
        "ksmserver",
        "ksmserver-logout-greeter",
        "polkit-kde-authentication-agent-1",
        "org.kde.polkit-kde-authentication-agent-1",
        "xdg-desktop-portal-kde",
        "org.freedesktop.impl.portal.desktop.kde",
        "xdg-desktop-portal-gtk",
        "xwaylandvideobridge",
        "org.kde.spectacle",
        # Launchers and their helpers (the game window is another process)
        "steam",
        "steamwebhelper",
        "steam.exe",
        "lutris",
        "net.lutris.lutris",
        "heroic",
        "com.heroicgameslauncher.hgl",
        # Streaming itself
        "sunshine",
        "dev.lizardbyte.app.sunshine",
        "moonlight",
        "com.moonlight_stream.moonlight",
        "big-remote-play",
        APP_ID,
    }
)
# Parents that mean "this program was started as a game".
_STEAM_PARENTS = frozenset({"steam", "reaper", "pressure-vessel", "pv-bwrap", "srt-bwrap", "steam-runtime-l"})
_GAME_FOLDERS = ("/steamapps/common/", "/games/", "/Games/", "/usr/games/", "/.local/share/lutris/", "/Heroic/")
_ENV_KEYS = ("SteamAppId", "SteamGameId", "STEAM_COMPAT_APP_ID", "STEAM_COMPAT_DATA_PATH", "LUTRIS_GAME_UUID", "HEROIC_APP_NAME", "GAMESCOPE_WAYLAND_DISPLAY")
_MAX_CHAIN = 16


@dataclass(frozen=True)
class RawWindow:
    """One top-level window as the window system reports it."""

    backend: str  # "kwin" or "x11"
    handle: str  # KWin UUID or X11 window id (hex)
    title: str
    pid: int
    app_id: str = ""  # desktop file id (Wayland) or WM_CLASS class
    wm_class: str = ""
    width: int = 0  # picture size in logical pixels, without decoration
    height: int = 0
    decoration: tuple[int, int, int, int] = (0, 0, 0, 0)  # left, top, right, bottom
    scale: float = 1.0
    output: str = ""
    normal: bool = True
    minimized: bool = False
    fullscreen: bool = False


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int
    name: str  # kernel command name (``comm``)
    exe: str = ""
    argv0: str = ""
    cmdline: str = ""
    env: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Launch:
    """How the window's program was started, from its process tree."""

    launcher: str = ""  # "steam", "lutris", "heroic" or ""
    steam_app_id: str = ""
    proton: bool = False
    wine: bool = False
    gamescope: bool = False
    executable: str = ""
    in_game_folder: bool = False


@dataclass(frozen=True)
class GameWindow:
    """A window that can be offered for Game Window sharing."""

    window: RawWindow
    name: str
    launch: Launch
    is_game: bool
    icon_name: str = ""

    @property
    def key(self) -> str:
        """Identifies this window for this session only (ids are not reused across logins)."""
        return f"{self.window.backend}:{self.window.handle}"

    @property
    def identity(self) -> str:
        """Survives restarts: used only to pre-select the same game again."""
        if self.launch.steam_app_id:
            return f"steam:{self.launch.steam_app_id}"
        if self.window.app_id:
            return f"app:{self.window.app_id.lower()}"
        if self.launch.executable:
            return f"exe:{self.launch.executable.lower()}"
        return ""

    def tags(self) -> list[str]:
        """Stable technical words (brand names), most important first."""
        tags = []
        if self.launch.launcher:
            tags.append({"steam": "Steam", "lutris": "Lutris", "heroic": "Heroic"}[self.launch.launcher])
        if self.launch.proton:
            tags.append("Proton")
        elif self.launch.wine:
            tags.append("Wine")
        if self.launch.gamescope:
            tags.append("Gamescope")
        return tags

    def details(self) -> str:
        """Technical line for tooltips and logs (no personal data)."""
        parts = [f"pid {self.window.pid}" if self.window.pid else "", self.launch.executable, self.window.app_id]
        if self.launch.steam_app_id:
            parts.append(f"Steam AppID {self.launch.steam_app_id}")
        return " · ".join(part for part in parts if part)


# ── process tree ──────────────────────────────────────────────────────────


def read_process(pid: int, proc: Path = Path("/proc")) -> ProcessInfo | None:
    """What ``/proc`` says about one process; ``None`` if it is gone or not ours."""
    root = proc / str(int(pid))
    try:
        stat = (root / "stat").read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None
    name = stat[stat.find("(") + 1 : stat.rfind(")")]
    fields = stat[stat.rfind(")") + 2 :].split()
    ppid = int(fields[1]) if len(fields) > 1 and fields[1].isdigit() else 0
    try:
        exe = os.readlink(root / "exe")
    except OSError:
        exe = ""
    try:
        raw = (root / "cmdline").read_bytes()[:8192]
    except OSError:
        raw = b""
    argv = [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]
    env: dict[str, str] = {}
    try:
        # Only the few launcher markers are kept; nothing else leaves this function.
        for entry in (root / "environ").read_bytes()[:262144].split(b"\0"):
            key, sep, value = entry.partition(b"=")
            if sep and key.decode("ascii", "replace") in _ENV_KEYS:
                env[key.decode()] = value.decode("utf-8", "replace")[:256]
    except OSError:
        pass
    return ProcessInfo(pid=int(pid), ppid=ppid, name=name, exe=exe, argv0=argv[0] if argv else "", cmdline=" ".join(argv)[:4096], env=env)


def process_chain(pid: int, read: Callable[[int], ProcessInfo | None]) -> list[ProcessInfo]:
    """The process and its ancestors, nearest first (bounded, loop-safe)."""
    chain: list[ProcessInfo] = []
    seen: set[int] = set()
    current = pid
    while current > 1 and current not in seen and len(chain) < _MAX_CHAIN:
        seen.add(current)
        info = read(current)
        if info is None:
            break
        chain.append(info)
        current = info.ppid
    return chain


def children_of(pid: int, table: Iterable[ProcessInfo]) -> list[ProcessInfo]:
    """Descendants of ``pid`` (a Gamescope window's game runs below it)."""
    by_parent: dict[int, list[ProcessInfo]] = {}
    for info in table:
        by_parent.setdefault(info.ppid, []).append(info)
    found, pending = [], [pid]
    while pending and len(found) < 256:
        for child in by_parent.get(pending.pop(), []):
            found.append(child)
            pending.append(child.pid)
    return found


# Wrappers that run one game and nothing else: everything below them is the game.
_GAME_WRAPPERS = ("pv-adverb", "pv-bwrap", "srt-bwrap", "pressure-vessel", "gamescope", "gamemoderun", "umu-run", "lutris-wrapper")


def game_audio_root(chain: Sequence[ProcessInfo]) -> int:
    """The process whose whole subtree is the game, from the window's process up.

    Steam starts each game under its own ``reaper`` (``SteamLaunch AppId=…``);
    Lutris under one ``lutris-wrapper``. Otherwise Wine/Proton and runtime
    wrappers belong to the game, and the first other ancestor (a launcher,
    a shell, the desktop) does not: a native game is its own root.
    """
    if not chain:
        return 0
    for info in chain:
        if info.name == "reaper" and "SteamLaunch" in info.cmdline:
            return info.pid
        if info.name.startswith("lutris-wrapper"):
            return info.pid
    root = chain[0]
    for info in chain[1:]:
        if not (_is_wine_process(info) or info.name.lower().startswith(_GAME_WRAPPERS)):
            break
        root = info
    return root.pid


def process_parents(proc: Path = Path("/proc")) -> dict[int, int]:
    """``{pid: parent pid}`` from ``/proc/*/stat`` only: cheap enough for every audio check."""
    parents: dict[int, int] = {}
    for entry in proc.iterdir() if proc.is_dir() else []:
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fields = stat[stat.rfind(")") + 2 :].split()
        if len(fields) > 1 and fields[1].isdigit():
            parents[int(entry.name)] = int(fields[1])
    return parents


def game_process_family(window_pid: int, *, read: Callable[[int], ProcessInfo | None] = read_process, parents: Callable[[], Mapping[int, int]] = process_parents) -> frozenset[int]:
    """The window's process, the game's root and everything started below it."""
    if window_pid <= 1:
        return frozenset()
    root = game_audio_root(process_chain(window_pid, read)) or window_pid
    by_parent: dict[int, list[int]] = {}
    for pid, ppid in parents().items():
        by_parent.setdefault(ppid, []).append(pid)
    family, pending = {root, window_pid}, [root]
    while pending and len(family) < 4096:
        for child in by_parent.get(pending.pop(), []):
            if child not in family:
                family.add(child)
                pending.append(child)
    return frozenset(family)


def all_processes(proc: Path = Path("/proc")) -> list[ProcessInfo]:
    table = []
    for entry in proc.iterdir() if proc.is_dir() else []:
        if entry.name.isdigit():
            info = read_process(int(entry.name), proc)
            if info is not None:
                table.append(info)
    return table


def _base(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _is_wine_process(info: ProcessInfo) -> bool:
    exe = _base(info.exe).lower()
    return exe.startswith("wine") or info.name.lower().endswith(".exe") or _base(info.argv0).lower().endswith(".exe")


def classify_launch(chain: Sequence[ProcessInfo], descendants: Sequence[ProcessInfo] = ()) -> Launch:
    """Launcher, compatibility layer and executable from a process tree."""
    if not chain:
        return Launch()
    own = chain[0]
    everyone = [*chain, *descendants]
    names = [info.name.lower() for info in chain]
    steam_id = ""
    for info in everyone:
        for key in ("SteamAppId", "STEAM_COMPAT_APP_ID", "SteamGameId"):
            value = info.env.get(key, "")
            if _STEAM_APP_ID_RE.fullmatch(value) and int(value) < 2**31:
                steam_id = value
                break
        if steam_id:
            break
    gamescope = own.name.lower().startswith("gamescope") or any(name.startswith("gamescope") for name in names[1:]) or any("GAMESCOPE_WAYLAND_DISPLAY" in info.env for info in chain)
    # For a Gamescope window, the game is the Wine/native program below it.
    game = own
    if own.name.lower().startswith("gamescope"):
        candidates = [info for info in descendants if _is_wine_process(info)] or [
            info for info in descendants if not info.name.lower().startswith(("gamescope", "steam", "reaper", "pv-", "srt-", "pressure"))
        ]
        game = candidates[-1] if candidates else own
    wine = any(_is_wine_process(info) for info in (game, own))
    proton = wine and (any("STEAM_COMPAT_DATA_PATH" in info.env for info in everyone) or any("/proton" in info.cmdline.lower() for info in everyone))
    if steam_id or any(name in _STEAM_PARENTS for name in names[1:]):
        launcher = "steam"
    elif any("lutris" in name for name in names[1:]) or any("LUTRIS_GAME_UUID" in info.env for info in everyone):
        launcher = "lutris"
    elif any(name.startswith("heroic") for name in names[1:]) or any("HEROIC_APP_NAME" in info.env for info in everyone):
        launcher = "heroic"
    else:
        launcher = ""
    executable = _base(game.argv0) if wine and _base(game.argv0).lower().endswith(".exe") else (game.name if wine and game.name.lower().endswith(".exe") else _base(game.exe) or game.name)
    folder = any(marker in path for info in (game, own) for path in (info.exe, info.cmdline) for marker in _GAME_FOLDERS)
    return Launch(launcher=launcher, steam_app_id=steam_id, proton=proton, wine=wine, gamescope=gamescope, executable=executable, in_game_folder=folder)


# ── classification ────────────────────────────────────────────────────────


def exclusion_reason(window: RawWindow, process: ProcessInfo | None, *, own_pid: int) -> str:
    """Why a window can never be offered ("" when it can). Stable, for logs and tests."""
    if not window.normal:
        return "not-an-app-window"
    if window.minimized:
        return "minimized"
    if window.width < 64 or window.height < 64:
        return "too-small"
    if window.pid and window.pid == own_pid:
        return "this-app"
    names = {window.app_id.lower(), window.wm_class.lower()}
    if process is not None:
        names |= {process.name.lower(), _base(process.exe).lower()}
    if names & _NEVER_GAMES:
        return "desktop-or-launcher"
    return ""


def game_reason(window: RawWindow, launch: Launch, categories: Callable[[str], Iterable[str]]) -> str:
    """Why a window counts as a game ("" when nothing says so)."""
    if launch.steam_app_id or launch.launcher:
        return "launcher"
    if launch.proton or launch.wine:
        return "windows-game"
    if launch.gamescope:
        return "gamescope"
    if window.app_id and "Game" in set(categories(window.app_id)):
        return "desktop-category"
    if launch.in_game_folder:
        return "game-folder"
    return ""


def friendly_name(window: RawWindow, launch: Launch, steam_names: Mapping[str, str], display_name: Callable[[str], str]) -> str:
    if launch.steam_app_id and steam_names.get(launch.steam_app_id):
        return steam_names[launch.steam_app_id]
    if window.app_id and not (launch.wine or launch.gamescope):
        name = display_name(window.app_id)
        if name:
            return name
    return window.title.strip() or launch.executable or window.app_id or window.wm_class


def build_game_windows(
    raw: Sequence[RawWindow],
    *,
    read: Callable[[int], ProcessInfo | None],
    table: Callable[[], Sequence[ProcessInfo]] = lambda: (),
    steam_names: Mapping[str, str] | None = None,
    categories: Callable[[str], Iterable[str]] = lambda _app_id: (),
    display_name: Callable[[str], str] = lambda _app_id: "",
    icon_for: Callable[[RawWindow, Launch], str] = lambda _window, _launch: "",
    include_other_windows: bool = False,
    own_pid: int | None = None,
) -> list[GameWindow]:
    """Offerable windows, games first, each once."""
    own = os.getpid() if own_pid is None else own_pid
    steam_names = steam_names or {}
    found: list[GameWindow] = []
    processes: Sequence[ProcessInfo] | None = None
    seen: set[str] = set()
    for window in raw:
        key = f"{window.backend}:{window.handle}"
        if key in seen:
            continue
        seen.add(key)
        chain = process_chain(window.pid, read) if window.pid > 0 else []
        own_process = chain[0] if chain else None
        reason = exclusion_reason(window, own_process, own_pid=own)
        if reason:
            _log.debug("Game Window: skipped %s (%s)", key, reason)
            continue
        descendants: list[ProcessInfo] = []
        if own_process is not None and own_process.name.lower().startswith("gamescope"):
            if processes is None:
                processes = table()
            descendants = children_of(own_process.pid, processes)
        launch = classify_launch(chain, descendants)
        steam_class = _STEAM_CLASS_RE.fullmatch(window.app_id) or _STEAM_CLASS_RE.fullmatch(window.wm_class)
        if steam_class and not launch.steam_app_id:
            launch = replace(launch, steam_app_id=steam_class.group(1), launcher="steam")
        is_game = bool(game_reason(window, launch, categories))
        if not is_game and not include_other_windows:
            continue
        name = friendly_name(window, launch, steam_names, display_name)
        found.append(GameWindow(window=window, name=name, launch=launch, is_game=is_game, icon_name=icon_for(window, launch)))
    found.sort(key=lambda item: (not item.is_game, item.name.casefold()))
    return found


def reselect(previous_key: str, identity: str, windows: Sequence[GameWindow]) -> GameWindow | None:
    """The window to keep selected after a refresh.

    The same window wins. Otherwise a saved identity is used only when exactly
    one open window has it: two copies of a game are never guessed between.
    """
    same = next((item for item in windows if item.key == previous_key), None) if previous_key else None
    if same is not None:
        return same
    if identity:
        matches = [item for item in windows if item.identity == identity]
        if len(matches) == 1:
            return matches[0]
    return None


# ── listing: KWin (Wayland) ───────────────────────────────────────────────


class WindowListUnavailable(RuntimeError):
    """The window system could not be asked (not KDE, KWin busy, no X server)."""


_KWIN_SCRIPT = r"""
const found = [];
for (const w of workspace.stackingOrder) {
    const f = w.frameGeometry, c = w.clientGeometry;
    found.push({
        id: String(w.internalId), caption: String(w.caption || ""), pid: Number(w.pid || 0),
        desktopFile: String(w.desktopFileName || ""), resourceClass: String(w.resourceClass || ""),
        normal: !!w.normalWindow && !w.specialWindow && !w.skipTaskbar && !w.popupWindow && !w.notification,
        minimized: !!w.minimized, fullScreen: !!w.fullScreen,
        output: w.output ? String(w.output.name) : "", scale: w.output ? Number(w.output.devicePixelRatio || 1) : 1,
        frame: [f.x, f.y, f.width, f.height], client: [c.x, c.y, c.width, c.height]
    });
}
callDBus("%SERVICE%", "/br/com/biglinux/remoteplay/Windows", "br.com.biglinux.remoteplay.Windows", "Report", JSON.stringify(found));
"""
_KWIN_REPLY_XML = "<node><interface name='br.com.biglinux.remoteplay.Windows'><method name='Report'><arg type='s' direction='in'/></method></interface></node>"


def parse_kwin_windows(payload: str) -> list[RawWindow]:
    """The KWin script's JSON → windows. Anything malformed is skipped."""
    try:
        items = json.loads(payload)
    except ValueError:
        return []
    windows = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not KWIN_UUID_RE.fullmatch(str(item.get("id") or "")):
            continue
        try:
            fx, fy, fw, fh = (float(value) for value in item["frame"])
            cx, cy, cw, ch = (float(value) for value in item["client"])
            scale = max(0.5, min(4.0, float(item.get("scale") or 1)))
            pid = int(item.get("pid") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        decoration = tuple(max(0, round(value)) for value in (cx - fx, cy - fy, (fx + fw) - (cx + cw), (fy + fh) - (cy + ch)))
        windows.append(
            RawWindow(
                backend="kwin",
                handle=str(item["id"]),
                title=str(item.get("caption") or "")[:256],
                pid=max(0, pid),
                app_id=str(item.get("desktopFile") or item.get("resourceClass") or "")[:128],
                wm_class=str(item.get("resourceClass") or "")[:128],
                width=max(0, round(cw)),
                height=max(0, round(ch)),
                decoration=decoration,  # type: ignore[arg-type]
                scale=scale,
                output=str(item.get("output") or "")[:64],
                normal=bool(item.get("normal")),
                minimized=bool(item.get("minimized")),
                fullscreen=bool(item.get("fullScreen")),
            )
        )
    return windows


def _run_kwin_script(script: str, timeout_ms: int = 4000) -> str:
    """Run a short-lived KWin script and return the text it reports.

    KWin scripting needs no special permission. KWin calls back a private
    object on this process's own D-Bus connection (only KWin's answer is
    accepted); the script is always unloaded afterwards.
    """
    from gi.repository import Gio, GLib  # type: ignore

    context = GLib.MainContext.new()
    context.push_thread_default()
    connection = None
    plugin = f"brp-windows-{secrets.token_hex(6)}"
    script_path = None
    try:
        address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
        connection = Gio.DBusConnection.new_for_address_sync(address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
        service = connection.get_unique_name() or ""
        if not _DBUS_UNIQUE_RE.fullmatch(service):
            raise WindowListUnavailable("no D-Bus name")
        loop = GLib.MainLoop.new(context, False)
        reply: dict[str, str] = {}

        def on_call(_connection, sender, _path, _interface, _method, parameters, invocation) -> None:
            # Only KWin may answer: anything else on the bus is ignored.
            if sender == owner:
                reply["payload"] = parameters.unpack()[0]
                loop.quit()
            invocation.return_value(None)

        owner = connection.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "GetNameOwner", GLib.Variant("(s)", ("org.kde.KWin",)), GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, 2000, None
        ).unpack()[0]
        # register_object_with_closures2 exists since GLib 2.84; older PyGObject has only the first.
        register = getattr(connection, "register_object_with_closures2", None) or connection.register_object
        registration = register("/br/com/biglinux/remoteplay/Windows", Gio.DBusNodeInfo.new_for_xml(_KWIN_REPLY_XML).interfaces[0], on_call, None, None)
        folder = _private_dir()
        with tempfile.NamedTemporaryFile("w", suffix=".js", dir=folder, delete=False, encoding="utf-8") as handle:
            handle.write(script.replace("%SERVICE%", service))
            script_path = handle.name
        script_id = connection.call_sync(
            "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", "loadScript", GLib.Variant("(ss)", (script_path, plugin)), GLib.VariantType("(i)"), Gio.DBusCallFlags.NONE, 3000, None
        ).unpack()[0]
        if script_id < 0:
            raise WindowListUnavailable("KWin refused the script")
        connection.call_sync("org.kde.KWin", f"/Scripting/Script{script_id}", "org.kde.kwin.Script", "run", None, None, Gio.DBusCallFlags.NONE, 3000, None)
        timeout = GLib.timeout_source_new(timeout_ms)
        timeout.set_callback(lambda *_args: loop.quit() or False)
        timeout.attach(context)
        loop.run()
        timeout.destroy()
        connection.unregister_object(registration)
        if "payload" not in reply:
            raise WindowListUnavailable("KWin did not answer")
        return reply["payload"]
    except GLib.Error as error:
        raise WindowListUnavailable(error.message) from error
    finally:
        if connection is not None:
            try:
                connection.call_sync("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", "unloadScript", GLib.Variant("(s)", (plugin,)), None, Gio.DBusCallFlags.NONE, 2000, None)
            except GLib.Error:
                pass
            connection.close_sync(None)
        if script_path:
            Path(script_path).unlink(missing_ok=True)
        context.pop_thread_default()


def kwin_windows(timeout_ms: int = 4000) -> list[RawWindow]:
    """The windows KWin manages now (Wayland and X11 KWin alike)."""
    return parse_kwin_windows(_run_kwin_script(_KWIN_SCRIPT, timeout_ms))


_KWIN_ACTIVATE_SCRIPT = r"""
let found = "0";
for (const w of workspace.windowList()) {
    if (String(w.internalId) === "%UUID%" && !w.minimized) {
        workspace.activeWindow = w;
        found = "1";
    }
}
callDBus("%SERVICE%", "/br/com/biglinux/remoteplay/Windows", "br.com.biglinux.remoteplay.Windows", "Report", found);
"""


def activate_kwin_window(uuid: str) -> bool:
    """Give the shared game the keyboard: Sunshine's input goes to the active window."""
    if not KWIN_UUID_RE.fullmatch(uuid):
        return False
    try:
        return _run_kwin_script(_KWIN_ACTIVATE_SCRIPT.replace("%UUID%", uuid)) == "1"
    except WindowListUnavailable:
        return False


def kwin_window_info(uuid: str, connection=None) -> dict | None:
    """``getWindowInfo`` for one window: ``{}`` when it is gone, ``None`` if KWin cannot be asked."""
    from gi.repository import Gio, GLib  # type: ignore

    if not KWIN_UUID_RE.fullmatch(uuid):
        return {}
    try:
        bus = connection or Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync("org.kde.KWin", "/KWin", "org.kde.KWin", "getWindowInfo", GLib.Variant("(s)", (uuid,)), GLib.VariantType("(a{sv})"), Gio.DBusCallFlags.NONE, 2000, None)
    except GLib.Error:
        return None
    return dict(reply.unpack()[0])


def kwin_compositing_active(connection=None) -> bool | None:
    """KWin's own compositing state (``None`` when KWin is not reachable)."""
    from gi.repository import Gio, GLib  # type: ignore

    try:
        bus = connection or Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            "org.kde.KWin",
            "/Compositor",
            "org.freedesktop.DBus.Properties",
            "Get",
            GLib.Variant("(ss)", ("org.kde.kwin.Compositing", "active")),
            GLib.VariantType("(v)"),
            Gio.DBusCallFlags.NONE,
            2000,
            None,
        )
    except GLib.Error:
        return None
    return bool(reply.unpack()[0])


def _private_dir() -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    folder = Path(runtime) / "big-remote-play"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return str(folder)


# ── listing: X11 ──────────────────────────────────────────────────────────

_XID_RE = re.compile(r"^0x[0-9a-f]{1,8}$")


def x11_windows(display: str | None = None) -> list[RawWindow]:
    from big_remote_play.host import x11_windows as x11

    listed = x11.list_windows(display if display is not None else os.environ.get("DISPLAY"))
    if listed is None:
        raise WindowListUnavailable("X server not reachable")
    return [
        RawWindow(
            backend="x11",
            handle=hex(item.xid),
            title=item.title[:256],
            pid=max(0, item.pid),
            app_id=item.wm_class[:128],
            wm_class=item.wm_class[:128],
            width=item.width,
            height=item.height,
            normal=item.normal and item.viewable and not item.skip_taskbar,
            minimized=item.minimized,
            fullscreen=item.fullscreen,
        )
        for item in listed
    ]


# ── what this session supports ────────────────────────────────────────────


@dataclass(frozen=True)
class CaptureSupport:
    backend: str  # "kwin", "x11" or ""
    problem: str = ""  # "" when available; else a stable code
    missing: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return not self.problem


# GStreamer element → the Arch package that ships it (the name shown to the user).
_ELEMENT_PACKAGES = {
    "pipewiresrc": "gst-plugin-pipewire",
    "waylandsink": "gst-plugins-bad",
    "ximagesrc": "gst-plugins-good",
    "videocrop": "gst-plugins-good",
    "videoconvert": "gst-plugins-base",
}


def session_backend(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    if env.get("XDG_SESSION_TYPE") == "wayland" or (env.get("WAYLAND_DISPLAY") and env.get("XDG_SESSION_TYPE") != "x11"):
        return "kwin" if "KDE" in env.get("XDG_CURRENT_DESKTOP", "").upper().split(":") else ""
    return "x11" if env.get("DISPLAY") else ""


# Sunshine reads the private game screen with its KWin capture method, which
# first shipped in Sunshine v2026.516. An older Sunshine starts without any
# video source: devices connect, get sound and wait for a picture forever.
SUNSHINE_KWIN_CAPTURE = (2026, 516)
_SUNSHINE_VERSION_RE = re.compile(r"Sunshine version: v?(\d{4})\.(\d{1,4})\.")


def sunshine_version(*, which: Callable[[str], str | None] = shutil.which, run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> tuple[int, int] | None:
    """``(year, month*100+day)`` of the installed Sunshine, or ``None`` if unknown."""
    tool = which("sunshine")
    if not tool:
        return None
    try:
        result = run([tool, "--version"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    match = _SUNSHINE_VERSION_RE.search(f"{result.stdout or ''}\n{result.stderr or ''}")
    return (int(match.group(1)), int(match.group(2))) if match else None


def capture_support(
    env: Mapping[str, str] | None = None,
    *,
    which: Callable[[str], str | None] = shutil.which,
    has_element: Callable[[str], bool] | None = None,
    sandboxed: bool | None = None,
    version: Callable[[], tuple[int, int] | None] | None = None,
) -> CaptureSupport:
    """Whether Game Window can work in this session, and what is missing."""
    backend = session_backend(env)
    if sandboxed if sandboxed is not None else Path("/.flatpak-info").exists():
        return CaptureSupport(backend, "sandboxed")
    if not backend:
        return CaptureSupport("", "unsupported-session")
    installed = (version or (lambda: sunshine_version(which=which)))()
    if installed is not None and installed < SUNSHINE_KWIN_CAPTURE:
        return CaptureSupport(backend, "sunshine-too-old")
    missing = []
    if not which("kwin_wayland"):
        missing.append("kwin")
    check = has_element or _gst_element_exists
    elements = ("pipewiresrc", "videocrop", "waylandsink") if backend == "kwin" else ("ximagesrc", "videoconvert", "waylandsink")
    missing += sorted({_ELEMENT_PACKAGES[name] for name in elements if not check(name)})
    return CaptureSupport(backend, "missing-packages" if missing else "", tuple(missing))


def _gst_element_exists(name: str) -> bool:
    """Ask the same GStreamer the capture helper uses (no external tool)."""
    try:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst  # type: ignore
    except (ImportError, ValueError):
        return False
    ok, _argv = Gst.init_check(None)
    return bool(ok) and Gst.ElementFactory.find(name) is not None


def list_raw_windows(backend: str) -> list[RawWindow]:
    if backend == "kwin":
        return kwin_windows()
    if backend == "x11":
        return x11_windows()
    raise WindowListUnavailable("unsupported session")


def refresh(backend: str, *, include_other_windows: bool = False, steam_names: Mapping[str, str] | None = None) -> list[GameWindow]:
    """List offerable windows now (blocking: run it off the GTK thread)."""
    raw = list_raw_windows(backend)
    windows = build_game_windows(
        raw,
        read=read_process,
        table=all_processes,
        steam_names=steam_names,
        categories=_desktop_categories,
        display_name=_desktop_name,
        icon_for=_icon_for,
        include_other_windows=include_other_windows,
    )
    _log.info("Game Window: %d capturable window(s), %d game(s)", len(windows), sum(item.is_game for item in windows))
    return windows


def with_steam_names(windows: Sequence[GameWindow], steam_names: Mapping[str, str]) -> list[GameWindow]:
    return [replace(item, name=steam_names.get(item.launch.steam_app_id, item.name)) for item in windows]


def _desktop_app_info_class():
    import gi

    try:
        gi.require_version("GioUnix", "2.0")
        from gi.repository import GioUnix  # type: ignore

        return GioUnix.DesktopAppInfo
    except (ImportError, ValueError, AttributeError):
        from gi.repository import Gio  # type: ignore

        return Gio.DesktopAppInfo


def _desktop_info(app_id: str):
    if not app_id or "/" in app_id:
        return None
    desktop_app_info = _desktop_app_info_class()
    for candidate in (app_id, app_id.lower()):
        try:
            info = desktop_app_info.new(f"{candidate}.desktop")
        except TypeError:
            info = None
        if info is not None:
            return info
    return None


def _desktop_categories(app_id: str) -> list[str]:
    info = _desktop_info(app_id)
    return [part for part in (info.get_categories() or "").split(";") if part] if info else []


def _desktop_name(app_id: str) -> str:
    info = _desktop_info(app_id)
    return info.get_display_name() or "" if info else ""


def _icon_for(window: RawWindow, launch: Launch) -> str:
    """A themed icon name: the game's own, else the app's (UI checks it exists)."""
    if launch.steam_app_id:
        return f"steam_icon_{launch.steam_app_id}"
    info = _desktop_info(window.app_id)
    icon = info.get_icon() if info else None
    names = icon.get_names() if icon is not None and hasattr(icon, "get_names") else []
    return names[0] if names else ""
