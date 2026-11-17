"""Touch from a phone or tablet: mouse, direct touch or an on-screen controller.

Moonlight on a phone turns fingers into a mouse by default. A client that
sends *native* touch (``LiSendTouchEvent``) makes Sunshine create a virtual
touchscreen on this computer, ``libvirtualhid Touchscreen``: true multi-touch,
one contact per finger, positions normalised to the shared picture.

The Big WM Virtual Keyboard (``big-keyboard``) draws an on-screen controller
and turns those contacts into presses on a virtual gamepad (uinput) that games
see like any controller: several buttons at once, sticks, triggers. It reads
the touchscreen straight from the kernel, so it works even where the desktop
does not deliver touch (wlmaker) and nothing becomes a mouse click.

This module holds the decisions and the checks, without GTK:

- what each mode changes (Sunshine's ``native_pen_touch``, whether Host input
  priority may pause the touchscreen, the controller's remote-touch mode);
- the compatibility checks, from real evidence (files, devices, D-Bus);
- a small D-Bus client for ``big-keyboard`` (``org.gtk.Actions``).

Nothing here changes the phone: Moonlight's settings stay the person's own.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import shutil
from typing import Any

_log = logging.getLogger("big-remoteplay")

# ── modes ─────────────────────────────────────────────────────────────────

AUTOMATIC, MOUSE, TOUCH, GAMEPAD, HYBRID = "automatic", "mouse", "touch", "gamepad", "hybrid"
MODES = (AUTOMATIC, MOUSE, TOUCH, GAMEPAD, HYBRID)
DEFAULT_MODE = AUTOMATIC

# Sunshine's virtual touchscreen (libvirtualhid). The older inputtino name
# ("Touch passthrough") normalised to the whole desktop and is not used.
SUNSHINE_TOUCHSCREEN = "libvirtualhid Touchscreen"


def normalize_mode(value: object) -> str:
    return value if isinstance(value, str) and value in MODES else DEFAULT_MODE


def uses_controller(mode: str) -> bool:
    """The on-screen controller takes the phone's fingers."""
    return normalize_mode(mode) in (GAMEPAD, HYBRID)


def sunshine_settings(mode: str) -> dict[str, str]:
    """Sunshine keys this mode needs; Automatic keeps the person's own choice.

    ``native_pen_touch = disabled`` makes Sunshine stop offering touch, so a
    client that could send it falls back to its mouse emulation.
    """
    mode = normalize_mode(mode)
    if mode == MOUSE:
        return {"native_pen_touch": "disabled"}
    if mode in (TOUCH, GAMEPAD, HYBRID):
        return {"native_pen_touch": "enabled"}
    return {}


# Desktops whose compositor drops touch devices. wlmaker 0.8 ignores
# WLR_INPUT_DEVICE_TOUCH (src/input/manager.c); measured on 2026-11-17: a
# phone sending native touch moved nothing at all there, while Sunshine's
# relative and absolute mice reached it.
TOUCHLESS_DESKTOPS = frozenset({"wlmaker"})


def desktop_ignores_touch(env: Mapping[str, str] | None = None) -> bool:
    return desktop_name(env)[1] in TOUCHLESS_DESKTOPS


def effective_mode(mode: str, *, ignores_touch: bool, controller_installed: bool) -> str:
    """What Automatic means on this desktop; every other mode is the person's choice.

    Where the desktop drops touch, a phone's fingers must go somewhere that
    works: with the on-screen controller installed, the controller and the
    keyboard (touching elsewhere moves the pointer, see keyboard_with_controller);
    without it, Sunshine stops offering touch and Moonlight sends a mouse.
    """
    mode = normalize_mode(mode)
    if mode != AUTOMATIC or not ignores_touch:
        return mode
    return HYBRID if controller_installed else MOUSE


def keyboard_with_controller(mode: str, *, ignores_touch: bool) -> bool:
    """Controller and touch on a desktop without touch: big-keyboard opens its
    keyboard beside the controller, and touches outside both move the pointer
    (its RemoteMouse=auto: direct touch with keyboard and controller open)."""
    return normalize_mode(mode) == HYBRID and ignores_touch


def remote_touch_mode(mode: str, *, ignores_touch: bool = False) -> str:
    """``big-keyboard`` remote-touch mode: exclusive keeps every finger on the
    controller (nothing becomes a click); shared lets touches outside it reach
    the desktop where the desktop accepts touch. Where it does not, the
    controller keeps the fingers and turns the others into the pointer, also
    with everything hidden ("mouse", big-keyboard 0.2.3+; older ones keep
    "exclusive", see :meth:`BigKeyboard.configure`)."""
    mode = normalize_mode(mode)
    if mode == HYBRID and ignores_touch:
        # "mouse": exclusive that keeps reading the phone with the keyboard and
        # the controller hidden, every finger then being the pointer. Reported
        # 2026-11-17: hiding both on wlmaker stopped the phone's mouse.
        return "mouse"
    return {GAMEPAD: "exclusive", HYBRID: "shared", TOUCH: "off", MOUSE: "off"}.get(mode, "exclusive")


def priority_pauses_touch(mode: str) -> bool:
    """Host input priority pauses the guest's mouse and keyboard. When touch
    drives the controller it is a controller, and controllers are never paused."""
    return not uses_controller(mode)


# ── evidence ──────────────────────────────────────────────────────────────


def native_touch_enabled(config: Mapping[str, str]) -> bool:
    """Sunshine's ``native_pen_touch`` (enabled when absent)."""
    value = str(config.get("native_pen_touch", "enabled")).strip().lower()
    return value not in ("disabled", "false", "0", "off", "no")


def sunshine_touchscreens(sys_root: Path = Path("/sys/class/input")) -> list[str]:
    """``/dev/input/eventN`` of Sunshine's virtual touchscreens present now."""
    found = []
    try:
        entries = sorted(sys_root.glob("event*"), key=lambda path: int(path.name[5:]) if path.name[5:].isdigit() else 0)
    except OSError:
        return []
    for entry in entries:
        try:
            name = (entry / "device" / "name").read_text().strip()
        except OSError:
            continue
        if "libvirtualhid" in name and name.endswith("Touchscreen"):
            found.append(f"/dev/input/{entry.name}")
    return found


def uinput_writable(access: Callable[[str, int], bool] = os.access) -> bool:
    """The virtual gamepad needs ``/dev/uinput`` (Sunshine's udev rule grants it)."""
    return access("/dev/uinput", os.W_OK)


def desktop_name(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    """(session type, desktop) from the environment, lower case."""
    env = os.environ if env is None else env
    session = env.get("XDG_SESSION_TYPE", "").lower() or ("wayland" if env.get("WAYLAND_DISPLAY") else "x11" if env.get("DISPLAY") else "")
    desktop = env.get("XDG_CURRENT_DESKTOP", "").split(":")[0].lower()
    return session, desktop


# ── big-keyboard over D-Bus ───────────────────────────────────────────────

BUS_NAME = "io.github.big_windowmaker.BigKeyboard"
OBJECT_PATH = "/io/github/big_windowmaker/BigKeyboard"
ACTIONS = "org.gtk.Actions"
# Remote touch needs these actions (big-keyboard with remote touch support).
REQUIRED_ACTIONS = ("set-gamepad", "remote-touch", "overlay-output", "remote-touch-status")


@dataclass(frozen=True)
class ControllerStatus:
    """What big-keyboard reports. ``installed`` and ``running`` are facts;
    the rest is empty when it is not running."""

    installed: bool
    running: bool = False
    supports_remote_touch: bool = False
    visible: bool = False
    remote_touch: str = ""
    remote_state: str = ""  # off idle waiting active shared busy denied error
    output: str = ""


class BigKeyboard:
    """Talks to the running ``big-keyboard`` through ``org.gtk.Actions``.

    Blocking calls: use from a worker thread, never the GTK main loop.
    """

    def __init__(self, *, bus: Any = None, which: Callable[[str], str | None] = shutil.which, timeout_ms: int = 1500) -> None:
        self._bus: Any = bus
        self._which = which
        self._timeout = timeout_ms

    def path(self) -> str | None:
        return self._which("big-keyboard")

    def _connection(self):
        if self._bus is None:
            from gi.repository import Gio  # type: ignore

            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        return self._bus

    def _call(self, method: str, args, reply: str | None):
        from gi.repository import Gio, GLib  # type: ignore

        return self._connection().call_sync(
            BUS_NAME,
            OBJECT_PATH,
            ACTIONS,
            method,
            args,
            GLib.VariantType(reply) if reply else None,
            Gio.DBusCallFlags.NO_AUTO_START,
            self._timeout,
            None,
        )

    def running(self) -> bool:
        from gi.repository import GLib  # type: ignore

        try:
            reply = self._connection().call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner", GLib.Variant("(s)", (BUS_NAME,)), GLib.VariantType("(b)"), 0, self._timeout, None
            )
            return bool(reply.unpack()[0])
        except Exception as error:  # no session bus
            _log.debug("big-keyboard: %s", error)
            return False

    def status(self) -> ControllerStatus:
        if self.path() is None:
            return ControllerStatus(installed=False)
        if not self.running():
            return ControllerStatus(installed=True)
        try:
            described = self._call("DescribeAll", None, "(a{s(bgav)})").unpack()[0]
        except Exception as error:
            _log.warning("big-keyboard: cannot read its state: %s", error)
            return ControllerStatus(installed=True, running=True)
        return parse_actions(described)

    def _activate(self, name: str, parameter=None) -> None:
        from gi.repository import GLib  # type: ignore

        params = [parameter] if parameter is not None else []
        self._call("Activate", GLib.Variant("(sava{sv})", (name, params, {})), None)

    def _set_state(self, name: str, value) -> None:
        from gi.repository import GLib  # type: ignore

        self._call("SetState", GLib.Variant("(sva{sv})", (name, value, {})), None)

    def start(self) -> bool:
        """Start the resident process (it is normally started with the session)."""
        path = self.path()
        if path is None:
            return False
        if self.running():
            return True
        import subprocess
        import time

        subprocess.Popen([path, "--daemon"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if self.running():
                return True
            time.sleep(0.1)
        return False

    def _state(self, name: str):
        """The current state of one action, or None."""
        from gi.repository import GLib  # type: ignore

        try:
            _enabled, _type, state = self._call("Describe", GLib.Variant("(s)", (name,)), "((bgav))").unpack()[0]
        except Exception as error:
            _log.debug("big-keyboard: %s", error)
            return None
        return state[0] if state else None

    def configure(self, *, remote_touch: str, output: str) -> str:
        """Set the remote-touch mode and the screen; returns the mode in use.

        An older big-keyboard ignores "mouse" (its state does not change):
        then "exclusive", the closest mode it has, is set instead.
        """
        from gi.repository import GLib  # type: ignore

        used = ""
        if remote_touch in ("off", "exclusive", "shared", "mouse"):
            self._set_state("remote-touch", GLib.Variant("s", remote_touch))
            used = remote_touch
            if remote_touch == "mouse" and self._state("remote-touch") != "mouse":
                self._set_state("remote-touch", GLib.Variant("s", "exclusive"))
                used = "exclusive"
        if valid_output(output):
            self._set_state("overlay-output", GLib.Variant("s", output))
        return used

    def show_keyboard(self, visible: bool) -> None:
        """The keyboard itself (big-keyboard's show/hide actions)."""
        self._activate("show" if visible else "hide")

    def show(self, visible: bool) -> None:
        from gi.repository import GLib  # type: ignore

        self._activate("set-gamepad", GLib.Variant("b", visible))

    def edit(self) -> None:
        self._activate("edit-gamepad")

    def release(self) -> None:
        """Let go of every button and finger (a session ended)."""
        self._activate("release")


def valid_output(name: str) -> bool:
    return isinstance(name, str) and len(name) < 32 and all(char.isascii() and (char.isalnum() or char in "-_.") for char in name)


def parse_actions(described: Mapping[str, object]) -> ControllerStatus:
    """``DescribeAll`` → status; unknown or missing actions mean an older big-keyboard."""

    def state(name: str) -> object:
        entry = described.get(name)
        if isinstance(entry, (tuple, list)) and len(entry) == 3 and entry[2]:
            return entry[2][0]
        return None

    supports = all(name in described for name in REQUIRED_ACTIONS)
    mode = state("mode")
    return ControllerStatus(
        installed=True,
        running=True,
        supports_remote_touch=supports,
        visible=mode in ("gamepad", "hybrid"),
        remote_touch=str(state("remote-touch") or ""),
        remote_state=str(state("remote-touch-status") or ""),
        output=str(state("overlay-output") or ""),
    )


# ── readiness ─────────────────────────────────────────────────────────────

# Check outcomes: ok, warn (works with a limit), fail (blocks), info.
OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"


@dataclass(frozen=True)
class Check:
    key: str
    outcome: str
    detail: str = ""  # a technical identifier (device node, desktop), never prose


@dataclass(frozen=True)
class Facts:
    mode: str
    sunshine_config: Mapping[str, str]
    touchscreens: tuple[str, ...]
    uinput: bool
    controller: ControllerStatus
    session: str
    desktop: str
    sharing: bool
    game_window: bool


def gather(mode: str, sunshine_config: Mapping[str, str], *, sharing: bool, game_window: bool, keyboard: BigKeyboard | None = None, env: Mapping[str, str] | None = None) -> Facts:
    """Blocking: read every fact once (worker thread)."""
    session, desktop = desktop_name(env)
    return Facts(
        mode=normalize_mode(mode),
        sunshine_config=dict(sunshine_config),
        touchscreens=tuple(sunshine_touchscreens()),
        uinput=uinput_writable(),
        controller=(keyboard or BigKeyboard()).status(),
        session=session,
        desktop=desktop,
        sharing=sharing,
        game_window=game_window,
    )


def checks(facts: Facts) -> list[Check]:
    """What works and what is missing, in the order a person fixes it."""
    result: list[Check] = []
    native = native_touch_enabled(facts.sunshine_config)
    if facts.mode == MOUSE:
        result.append(Check("sunshine_touch", INFO if not native else WARN))
        return result
    result.append(Check("sunshine_touch", OK if native else FAIL))
    if facts.touchscreens:
        result.append(Check("touchscreen", OK, ", ".join(facts.touchscreens)))
    else:
        # Sunshine creates it when a device connects.
        result.append(Check("touchscreen", INFO))
    if facts.mode == TOUCH:
        # Touch goes to the desktop: wlmaker does not deliver touch at all.
        result.append(Check("desktop_touch", FAIL if facts.desktop == "wlmaker" else OK, facts.desktop or facts.session))
        return result
    controller = facts.controller
    if not controller.installed:
        result.append(Check("controller", FAIL if uses_controller(facts.mode) else INFO))
    elif controller.running and not controller.supports_remote_touch:
        result.append(Check("controller_version", FAIL))
    else:
        result.append(Check("controller", OK))
    result.append(Check("uinput", OK if facts.uinput else FAIL))
    if facts.game_window:
        result.append(Check("game_window", WARN))
    if controller.running and controller.supports_remote_touch and controller.visible:
        state = controller.remote_state
        outcome = OK if state in ("active", "shared") else WARN if state in ("waiting", "busy") else FAIL if state in ("denied", "error") else INFO
        result.append(Check("remote_state", outcome, state))
    return result


def ready(result: list[Check]) -> bool:
    return not any(check.outcome == FAIL for check in result)
