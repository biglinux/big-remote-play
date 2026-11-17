"""Ready-made entries in Moonlight: Desktop Low, Steam Big Picture, one per screen.

Moonlight lists the apps Sunshine offers. Besides **Desktop** (always there)
Big Remote Play can offer:

- **Desktop Low** — the same screen with a lower bitrate ceiling, for slow or
  mobile connections;
- **Steam Big Picture** — opens Steam's TV interface when the stream starts
  and closes it when the stream ends;
- **01 - Monitor**, **02 - Monitor**, … — one entry per screen, numbered as
  **Identify screens** numbers them.

Stock Sunshine (checked on 2026.914) captures one screen and caps the bitrate
for the whole server, read once at start: an app cannot choose either, so two
of these entries are only offered when the installed Sunshine reads the
per-app keys ``output-name`` (the screen) and ``max-bitrate`` (Kbps). Big
Remote Play's Sunshine build adds them; see ``docs/moonlight-entries.md``.

Entries made here carry :data:`MARKER` so they are recognised later: they are
replaced or removed as the settings change, while apps the person made — even
one with the same name — are never touched.

GTK-free; :func:`sunshine_supports_app_overrides` only reads the executable.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shlex

MARKER = "brp-entry"
DESKTOP_LOW = "desktop-low"
STEAM_BIG_PICTURE = "steam-big-picture"
MONITOR_PREFIX = "monitor:"

# Entry names are what Moonlight lists; like app names they are not translated.
DESKTOP_NAME = "Desktop"
DESKTOP_LOW_NAME = "Desktop Low"
STEAM_BIG_PICTURE_NAME = "Steam Big Picture"

# Bitrate ceilings offered for Desktop Low, in Kbps. 3 Mbit/s keeps a 720p
# desktop readable and fits most mobile connections.
LOW_BITRATES_KBPS = (1500, 3000, 5000, 8000)
DEFAULT_LOW_KBPS = 3000

SETTINGS_KEY = "moonlight_entries"
DEFAULT_SETTINGS = {"desktop_low": True, "steam_big_picture": True, "monitors": True, "low_kbps": DEFAULT_LOW_KBPS}

_CONNECTOR = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_FEATURE_KEYS = (b"\x00output-name\x00", b"\x00max-bitrate\x00")
_support_cache: dict[tuple[str, int, int], bool] = {}


@dataclass(frozen=True)
class Screen:
    connector: str  # the name Sunshine captures by (kernel connector)
    label: str  # what a person calls it ("LG ULTRAWIDE")


def settings_from(config_value: object) -> dict:
    """The saved choices, with defaults for anything missing or malformed."""
    settings = dict(DEFAULT_SETTINGS)
    if isinstance(config_value, dict):
        for key in ("desktop_low", "steam_big_picture", "monitors"):
            if isinstance(config_value.get(key), bool):
                settings[key] = config_value[key]
        if config_value.get("low_kbps") in LOW_BITRATES_KBPS:
            settings["low_kbps"] = config_value["low_kbps"]
    return settings


def monitor_entry_name(number: int) -> str:
    """``01 - Monitor``: the number matches Identify screens."""
    return f"{number:02d} - Monitor"


def sunshine_supports_app_overrides(executable: str | os.PathLike | None) -> bool:
    """Whether this Sunshine reads ``output-name`` and ``max-bitrate`` per app.

    Sunshine has no feature query, so the executable is searched for both key
    names (as NUL-terminated strings, the way the compiler stores them). The
    answer is cached per file version.
    """
    if not executable:
        return False
    try:
        path = Path(executable).resolve()
        stat = path.stat()
    except OSError:
        return False
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key in _support_cache:
        return _support_cache[key]
    found = {needle: False for needle in _FEATURE_KEYS}
    tail = b""
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1 << 20):
                window = tail + chunk
                for needle in found:
                    if not found[needle] and needle in window:
                        found[needle] = True
                if all(found.values()):
                    break
                tail = window[-32:]
    except OSError:
        return False
    supported = all(found.values())
    _support_cache[key] = supported
    return supported


def _steam_command(launcher: Sequence[str]) -> str:
    return shlex.join(["setsid", *launcher])


def desktop_low_entry(kbps: int) -> dict:
    return {"name": DESKTOP_LOW_NAME, "cmd": "", "image-path": "desktop.png", "max-bitrate": int(kbps), MARKER: DESKTOP_LOW}


def steam_big_picture_entry(launcher: Sequence[str]) -> dict:
    """Steam's TV interface; closed again when the stream ends (Sunshine's own recipe)."""
    base = _steam_command(launcher)
    return {
        "name": STEAM_BIG_PICTURE_NAME,
        "cmd": "",
        "detached": [f"{base} steam://open/bigpicture"],
        "prep-cmd": [{"do": "", "undo": f"{base} steam://close/bigpicture", "elevated": False}],
        "image-path": "steam.png",
        MARKER: STEAM_BIG_PICTURE,
    }


def stream_display_prep(global_prep_cmd: str | None, connector: str) -> tuple[list[dict], bool]:
    """The global preparation commands, with the screen helper aimed at ``connector``.

    Returns ``(commands, ok)``. Big Remote Play's screen helper (HDR → SDR,
    client resolution) is in ``global_prep_cmd`` with the shared screen as
    ``--output``; for another screen's entry it must prepare that screen
    instead. The other global commands are kept as they are and in order.
    ``ok`` is False when the value cannot be read: the entry then keeps the
    global commands untouched.
    """
    from big_remote_play.host.stream_display import MARKER as HELPER

    if not global_prep_cmd:
        return [], True
    try:
        commands = json.loads(global_prep_cmd)
    except ValueError:
        return [], False
    if not isinstance(commands, list):
        return [], False
    result: list[dict] = []
    for item in commands:
        if not isinstance(item, dict):
            return [], False
        do = str(item.get("do", ""))
        if HELPER in do:
            try:
                argv = shlex.split(do)
            except ValueError:
                return [], False
            if "--output" in argv:
                position = argv.index("--output")
                if position + 1 < len(argv):
                    argv[position + 1] = connector
            else:
                at = argv.index("prepare") + 1 if "prepare" in argv else len(argv)
                argv[at:at] = ["--output", connector]
            item = {**item, "do": shlex.join(argv)}
        result.append({"do": str(item.get("do", "")), "undo": str(item.get("undo", "")), "elevated": bool(item.get("elevated", False))})
    return result, True


def monitor_entry(number: int, screen: Screen, global_prep_cmd: str | None = None) -> dict | None:
    if _CONNECTOR.fullmatch(screen.connector or "") is None:
        return None
    entry: dict = {"name": monitor_entry_name(number), "cmd": "", "image-path": "desktop.png", "output-name": screen.connector, MARKER: MONITOR_PREFIX + screen.connector}
    commands, ok = stream_display_prep(global_prep_cmd, screen.connector)
    if ok and commands:
        entry["exclude-global-prep-cmd"] = True
        entry["prep-cmd"] = commands
    return entry


def wanted_entries(
    settings: dict,
    *,
    supported: bool,
    steam_launcher: Sequence[str] | None,
    screens: Sequence[Screen],
    global_prep_cmd: str | None = None,
) -> list[dict]:
    """Every entry Big Remote Play should offer now, in Moonlight's order."""
    settings = settings_from(settings)
    entries: list[dict] = []
    if supported and settings["desktop_low"]:
        entries.append(desktop_low_entry(settings["low_kbps"]))
    if steam_launcher and settings["steam_big_picture"]:
        entries.append(steam_big_picture_entry(steam_launcher))
    # One screen needs no per-screen entry: Desktop already is that screen.
    if supported and settings["monitors"] and len(screens) > 1:
        for number, screen in enumerate(screens, start=1):
            entry = monitor_entry(number, screen, global_prep_cmd)
            if entry is not None:
                entries.append(entry)
    return entries


@dataclass(frozen=True)
class Plan:
    remove: tuple[dict, ...]  # ours, no longer wanted or out of date
    add: tuple[dict, ...]  # ours, missing
    skipped: tuple[str, ...]  # names a person's own app already uses

    @property
    def changes(self) -> bool:
        return bool(self.remove or self.add)


def _comparable(app: dict) -> dict:
    # Sunshine adds its own fields (an id, a uuid, defaults); compare ours only.
    keys = ("name", "cmd", "detached", "prep-cmd", "image-path", "output-name", "max-bitrate", "exclude-global-prep-cmd", MARKER)
    return {key: app.get(key) for key in keys if key in app}


def plan_entries(current: Iterable[dict], wanted: Iterable[dict]) -> Plan:
    """What to remove and add so the library offers exactly ``wanted`` of ours."""
    current = [app for app in current if isinstance(app, dict)]
    wanted = list(wanted)
    ours = {str(app.get(MARKER)): app for app in current if app.get(MARKER)}
    theirs = {str(app.get("name", "")).casefold() for app in current if not app.get(MARKER)}
    wanted_by_marker = {str(entry[MARKER]): entry for entry in wanted}
    remove: list[dict] = []
    add: list[dict] = []
    skipped: list[str] = []
    for marker, app in ours.items():
        entry = wanted_by_marker.get(marker)
        if entry is None or _comparable(app) != _comparable(entry):
            remove.append(app)
    for marker, entry in wanted_by_marker.items():
        if entry["name"].casefold() in theirs:
            # The person's own app keeps its name; ours is not added beside it.
            skipped.append(entry["name"])
            continue
        if marker not in ours or ours[marker] in remove:
            add.append(entry)
    return Plan(tuple(remove), tuple(add), tuple(skipped))


def apply_to_list(apps: list, plan: Plan) -> bool:
    """Apply ``plan`` to a library list in place (file edits); True when it changed."""
    changed = False
    for app in plan.remove:
        for position, current in enumerate(apps):
            if isinstance(current, dict) and current.get(MARKER) == app.get(MARKER):
                del apps[position]
                changed = True
                break
    for entry in plan.add:
        apps.append(dict(entry))
        changed = True
    return changed


__all__ = [
    "DEFAULT_LOW_KBPS",
    "DEFAULT_SETTINGS",
    "LOW_BITRATES_KBPS",
    "MARKER",
    "Plan",
    "SETTINGS_KEY",
    "Screen",
    "apply_to_list",
    "monitor_entry_name",
    "plan_entries",
    "settings_from",
    "stream_display_prep",
    "sunshine_supports_app_overrides",
    "wanted_entries",
]
