"""Share one game window as a private screen that holds nothing else.

Sunshine only captures whole screens (outputs). Game Window gives it a screen
of its own: a headless compositor (``kwin_wayland --virtual``) in which the
only client is a mirror of the chosen window. The desktop, panels,
notifications and every other window do not exist there, so they cannot be
streamed, whatever happens on the real screen.

The window's picture comes from the desktop's own window capture:

- Wayland (KDE): the ScreenCast portal with the *window* source type. KWin
  renders just that window off-screen into a PipeWire DMA-BUF stream; windows
  above it are not part of it. KDE asks the person to confirm the window the
  first time and restores it silently afterwards.
- X11: the window's own contents (``ximagesrc xid=``). This is only private
  while a compositing manager keeps windows off-screen, so capture stops as
  soon as compositing is suspended.

This file runs as a separate helper process (like Sunshine, it outlives the
window). It stops itself, and says why, when the window closes, the capture
fails or its screen goes away. It never falls back to the desktop.

Protocol: the spec arrives as one JSON object on stdin; the helper answers
with one JSON line on stdout (``ready`` or ``failed``) and keeps a state file
in the private runtime directory until it exits.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import ctypes
import ctypes.util
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import re
import secrets
import select
import shutil
import signal
import subprocess
import sys
import time

_log = logging.getLogger("big-remoteplay")

SPEC_VERSION = 1
_UUID_RE = re.compile(r"^\{[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}$")
_XID_RE = re.compile(r"^0x[0-9a-f]{1,8}$")
_X_DISPLAY_RE = re.compile(r"^[A-Za-z0-9._-]{0,64}:[0-9]{1,4}(\.[0-9]{1,2})?$")
_SOCKET_RE = re.compile(r"^brp-game-[0-9a-f]{12}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_IDENTITY_RE = re.compile(r"^(steam|app|exe):[^\s/]{1,160}$")
_RENDER_NODE_RE = re.compile(r"^/dev/dri/renderD[0-9]{3}$")
MAX_WIDTH, MAX_HEIGHT = 7680, 4320
READY_TIMEOUT = 200.0  # the portal waits for the person to confirm the window
PORTAL_TIMEOUT = 180.0
COMPOSITOR_TIMEOUT = 15.0
WATCH_INTERVAL_MS = 1000
STALL_SECONDS = 5
STOP_GRACE = 5.0
HOLD_SECONDS = 20  # the empty private screen waits this long for Sunshine to be stopped

# Why a session ended. The UI maps these codes to sentences.
REASONS = (
    "stopped",
    "window-closed",
    "window-unknown",
    "compositing-off",
    "capture-failed",
    "compositor-exited",
    "portal-closed",
    "portal-cancelled",
    "portal-unavailable",
    "different-window",
    "compositor-failed",
    "invalid-spec",
)


class CaptureError(RuntimeError):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason if reason in REASONS else "capture-failed"
        self.detail = detail


# ── the spec ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Spec:
    backend: str  # "kwin" or "x11"
    handle: str  # KWin UUID or X11 window id
    width: int  # picture size in logical pixels (no decoration)
    height: int
    scale: float = 1.0
    decoration: tuple[int, int, int, int] = (0, 0, 0, 0)  # left, top, right, bottom (logical)
    identity: str = ""
    display: str = ""  # X11 display name (x11 only)
    fps: int = 60  # X11 polling rate; Wayland follows the game
    name: str = ""  # shown by the application while sharing (private runtime state only)

    @classmethod
    def from_mapping(cls, data: object) -> "Spec":
        if not isinstance(data, dict) or data.get("version") != SPEC_VERSION:
            raise CaptureError("invalid-spec", "unsupported spec")
        backend = data.get("backend")
        handle = str(data.get("handle") or "")
        if backend == "kwin":
            if not _UUID_RE.fullmatch(handle):
                raise CaptureError("invalid-spec", "bad window id")
        elif backend == "x11":
            if not _XID_RE.fullmatch(handle) or not _X_DISPLAY_RE.fullmatch(str(data.get("display") or "")):
                raise CaptureError("invalid-spec", "bad X11 window or display")
        else:
            raise CaptureError("invalid-spec", "bad backend")
        try:
            width, height = int(data["width"]), int(data["height"])
            scale = float(data.get("scale") or 1.0)
            decoration = tuple(int(value) for value in data.get("decoration") or (0, 0, 0, 0))
            fps = int(data.get("fps") or 60)
        except (KeyError, TypeError, ValueError) as error:
            raise CaptureError("invalid-spec", "bad size") from error
        if not (64 <= width <= MAX_WIDTH and 64 <= height <= MAX_HEIGHT) or not 0.5 <= scale <= 4.0:
            raise CaptureError("invalid-spec", "bad size")
        if len(decoration) != 4 or any(not 0 <= value <= 512 for value in decoration):
            raise CaptureError("invalid-spec", "bad decoration")
        identity = str(data.get("identity") or "")
        if identity and not _IDENTITY_RE.fullmatch(identity):
            identity = ""
        name = "".join(char for char in str(data.get("name") or "") if char.isprintable())[:200]
        return cls(backend, handle, width, height, scale, decoration, identity, str(data.get("display") or ""), max(15, min(fps, 144)), name)  # type: ignore[arg-type]

    def to_mapping(self) -> dict[str, object]:
        return {
            "version": SPEC_VERSION,
            "backend": self.backend,
            "handle": self.handle,
            "width": self.width,
            "height": self.height,
            "scale": self.scale,
            "decoration": list(self.decoration),
            "identity": self.identity,
            "display": self.display,
            "fps": self.fps,
            "name": self.name,
        }


def screen_size(spec: Spec) -> tuple[int, int]:
    """The private screen: the game's picture in device pixels, even, bounded.

    The same size as the picture means the mirror is never scaled; Sunshine
    then scales once, to what the device asked for.
    """
    scale = spec.scale if spec.backend == "kwin" else 1.0

    def fit(value: float, limit: int, step: int) -> int:
        return max(64, min(limit, int(round(value)) // step * step))

    # Width in steps of 8: a high refresh mode for the screen is generated
    # with CVT timings, whose width is always a multiple of 8.
    return fit(spec.width * scale, MAX_WIDTH, 8), fit(spec.height * scale, MAX_HEIGHT, 2)


FAST_REFRESH_MHZ = 240000


def fast_mode_commands(payload: object, width: int, height: int, tool: str) -> list[list[str]]:
    """``kscreen-doctor`` calls that give the private screen a 240 Hz mode.

    The virtual screen repaints at its refresh rate; at the default 60 Hz a
    game frame waits up to 16.7 ms (8.3 ms on average) before Sunshine can
    capture it, at 240 Hz about 2 ms. Nothing else changes.
    """
    from big_remote_play.host.stream_display import parse_outputs

    outputs = [output for output in parse_outputs(payload) if output.enabled]
    if len(outputs) != 1:
        return []
    output = outputs[0]
    fast = next((mode for mode in output.modes if (mode.width, mode.height) == (width, height) and mode.refresh >= 200), None)
    if fast is None:
        return [[tool, f"output.{output.name}.addCustomMode.{width}.{height}.{FAST_REFRESH_MHZ}.full"]]
    if fast.mode_id != output.mode_id:
        return [[tool, f"output.{output.name}.mode.{fast.mode_id}"]]
    return []


def compositor_argv(socket: str, width: int, height: int, *, which: Callable[[str], str | None] = shutil.which) -> list[str]:
    """The isolated screen. Its own D-Bus keeps it from taking desktop services."""
    if not _SOCKET_RE.fullmatch(socket):
        raise CaptureError("invalid-spec", "bad socket name")
    kwin = which("kwin_wayland")
    if not kwin:
        raise CaptureError("compositor-failed", "kwin_wayland not found")
    argv = [kwin, "--virtual", "--no-lockscreen", "--no-global-shortcuts", "--no-kactivities", "--socket", socket, "--width", str(width), "--height", str(height)]
    session_bus = which("dbus-run-session")
    return [session_bus, "--", *argv] if session_bus else argv


def compositor_env(base: Mapping[str, str]) -> dict[str, str]:
    """No path back to the desktop: no display, no session bus, normal permission checks."""
    return {key: value for key, value in base.items() if key not in ("WAYLAND_DISPLAY", "WAYLAND_SOCKET", "DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "KWIN_WAYLAND_NO_PERMISSION_CHECKS")}


def mirror_description(spec: Spec, socket: str, *, node: int = 0, fd: int = -1) -> str:
    """GStreamer pipeline text; every value is validated first (no free text)."""
    if not _SOCKET_RE.fullmatch(socket):
        raise CaptureError("invalid-spec", "bad socket name")
    sink = f"waylandsink name=sink display={socket} fullscreen=true sync=false"
    if spec.backend == "kwin":
        if fd < 0 or node <= 0:
            raise CaptureError("capture-failed", "no PipeWire stream")
        # DMA-BUF all the way: videocrop only attaches crop metadata that the
        # compositor applies (the title bar is not sent), nothing is copied.
        return f"pipewiresrc name=source fd={int(fd)} path={int(node)} always-copy=false ! videocrop name=crop ! {sink}"
    xid = int(spec.handle, 16)
    return f"ximagesrc name=source display-name={spec.display} xid={xid} use-damage=false show-pointer=true ! video/x-raw,framerate={spec.fps}/1 ! videoconvert ! {sink}"


# ── the GPU the private screen renders on ─────────────────────────────────


class _DrmDevice(ctypes.Structure):
    _fields_ = [("nodes", ctypes.POINTER(ctypes.c_char_p)), ("available_nodes", ctypes.c_int), ("bustype", ctypes.c_int)]


_DRM_NODE_RENDER = 2


def isolated_render_node(*, load: Callable[[], object | None] | None = None) -> str | None:
    """The render node KWin's virtual backend picks: libdrm's first device.

    Sunshine must encode on that same GPU (``adapter_name``): a DMA-BUF from
    one GPU cannot be imported by the other's encoder (seen for real: an
    AMD iGPU + dGPU machine got a black stream).
    """
    lib = load() if load is not None else _load_libdrm()
    if lib is None:
        return None
    try:
        count = lib.drmGetDevices2(0, None, 0)  # type: ignore[attr-defined]
        if count <= 0:
            return None
        devices = (ctypes.POINTER(_DrmDevice) * count)()
        found = lib.drmGetDevices2(0, devices, count)  # type: ignore[attr-defined]
        try:
            for index in range(max(0, found)):
                device = devices[index].contents
                if device.available_nodes & (1 << _DRM_NODE_RENDER):
                    path = device.nodes[_DRM_NODE_RENDER].decode("utf-8", "replace")
                    return path if _RENDER_NODE_RE.fullmatch(path) else None
        finally:
            lib.drmFreeDevices(devices, found)  # type: ignore[attr-defined]
    except (OSError, ValueError, AttributeError):
        return None
    return None


def _load_libdrm():
    name = ctypes.util.find_library("drm") or "libdrm.so.2"
    try:
        lib = ctypes.CDLL(name)
    except OSError:
        return None
    lib.drmGetDevices2.restype = ctypes.c_int
    lib.drmGetDevices2.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int]
    lib.drmFreeDevices.argtypes = [ctypes.c_void_p, ctypes.c_int]
    return lib


# ── private files ─────────────────────────────────────────────────────────


def runtime_dir() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / "big-remote-play"


def state_file() -> Path:
    return runtime_dir() / "game-window.json"


def read_state(path: Path | None = None) -> dict[str, object] | None:
    try:
        data = json.loads((path or state_file()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_state(state: Mapping[str, object], path: Path | None = None) -> None:
    from big_remote_play.utils.secure_io import secure_write_text

    target = path or state_file()
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    secure_write_text(str(target), json.dumps(dict(state)))


def clear_state(path: Path | None = None) -> None:
    (path or state_file()).unlink(missing_ok=True)


def _tokens_file() -> Path:
    from big_remote_play import paths

    return paths.CONFIG_DIR / "game-window-portal.json"


def load_restore_token(identity: str, path: Path | None = None) -> str:
    """The portal's permission to show the same game again without asking."""
    if not identity:
        return ""
    try:
        data = json.loads((path or _tokens_file()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    token = data.get(identity) if isinstance(data, dict) else None
    return token if isinstance(token, str) and _TOKEN_RE.fullmatch(token) else ""


def save_restore_token(identity: str, token: str, path: Path | None = None) -> None:
    from big_remote_play.utils.secure_io import secure_write_text

    if not identity or not _TOKEN_RE.fullmatch(token or ""):
        return
    target = path or _tokens_file()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data[identity] = token
    # A handful of games at most; the oldest entries go first.
    kept = dict(list(data.items())[-64:])
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    secure_write_text(str(target), json.dumps(kept))


# ── the helper process ────────────────────────────────────────────────────


class _Portal:
    """ScreenCast portal session for one window (Wayland)."""

    SOURCE_WINDOW = 2
    CURSOR_HIDDEN, CURSOR_EMBEDDED = 1, 2
    PERSIST_UNTIL_REVOKED = 2

    def __init__(self, Gio, GLib, on_closed: Callable[[], None]) -> None:
        self.Gio, self.GLib = Gio, GLib
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.sender = (self.bus.get_unique_name() or "")[1:].replace(".", "_")
        self.session = ""
        self._on_closed = on_closed
        self._closed_subscription = 0

    def _properties(self) -> dict:
        reply = self.bus.call_sync(
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.DBus.Properties",
            "GetAll",
            self.GLib.Variant("(s)", ("org.freedesktop.portal.ScreenCast",)),
            None,
            self.Gio.DBusCallFlags.NONE,
            5000,
            None,
        )
        return dict(reply.unpack()[0])

    def _request(self, method: str, signature: str, args: list, timeout: float) -> tuple[int, dict]:
        GLib, Gio = self.GLib, self.Gio
        token = "brp" + secrets.token_hex(6)
        path = f"/org/freedesktop/portal/desktop/request/{self.sender}/{token}"
        result: dict[str, object] = {}
        loop = GLib.MainLoop()

        def on_response(_connection, _sender, _path, _interface, _signal, parameters) -> None:
            result["code"], result["results"] = parameters.unpack()
            loop.quit()

        subscription = self.bus.signal_subscribe("org.freedesktop.portal.Desktop", "org.freedesktop.portal.Request", "Response", path, None, Gio.DBusSignalFlags.NONE, on_response)
        args[-1]["handle_token"] = GLib.Variant("s", token)
        try:
            self.bus.call_sync(
                "org.freedesktop.portal.Desktop",
                "/org/freedesktop/portal/desktop",
                "org.freedesktop.portal.ScreenCast",
                method,
                GLib.Variant(signature, tuple(args)),
                None,
                Gio.DBusCallFlags.NONE,
                10000,
                None,
            )
            timer = GLib.timeout_add(int(timeout * 1000), lambda: loop.quit() or False)
            loop.run()
            if "code" not in result:
                raise CaptureError("portal-cancelled", "no answer from the portal")
            GLib.source_remove(timer)
        finally:
            self.bus.signal_unsubscribe(subscription)
        return int(result["code"]), dict(result["results"])  # type: ignore[arg-type]

    def open(self, restore_token: str) -> tuple[int, int, str]:
        """``(node id, PipeWire fd, new restore token)`` for the window the person confirms."""
        GLib = self.GLib
        try:
            properties = self._properties()
        except GLib.Error as error:
            raise CaptureError("portal-unavailable", error.message) from error
        if not int(properties.get("AvailableSourceTypes", 0)) & self.SOURCE_WINDOW:
            raise CaptureError("portal-unavailable", "the portal cannot share a single window")
        cursor = self.CURSOR_EMBEDDED if int(properties.get("AvailableCursorModes", 0)) & self.CURSOR_EMBEDDED else self.CURSOR_HIDDEN
        try:
            code, results = self._request("CreateSession", "(a{sv})", [{"session_handle_token": GLib.Variant("s", "brps" + secrets.token_hex(6))}], 30)
            if code != 0:
                raise CaptureError("portal-unavailable", "session refused")
            self.session = str(results.get("session_handle") or "")
            self._closed_subscription = self.bus.signal_subscribe(
                "org.freedesktop.portal.Desktop", "org.freedesktop.portal.Session", "Closed", self.session, None, self.Gio.DBusSignalFlags.NONE, lambda *_args: self._on_closed()
            )
            options = {
                "types": GLib.Variant("u", self.SOURCE_WINDOW),
                "multiple": GLib.Variant("b", False),
                "cursor_mode": GLib.Variant("u", cursor),
                "persist_mode": GLib.Variant("u", self.PERSIST_UNTIL_REVOKED),
            }
            if restore_token:
                options["restore_token"] = GLib.Variant("s", restore_token)
            code, _results = self._request("SelectSources", "(oa{sv})", [self.session, options], 30)
            if code != 0:
                raise CaptureError("portal-unavailable", "sources refused")
            code, results = self._request("Start", "(osa{sv})", [self.session, "", {}], PORTAL_TIMEOUT)
            if code != 0:
                raise CaptureError("portal-cancelled", "window not confirmed")
            streams = results.get("streams") or []
            if len(streams) != 1 or int(dict(streams[0][1]).get("source_type", self.SOURCE_WINDOW)) != self.SOURCE_WINDOW:
                raise CaptureError("different-window", "the portal did not return one window")
            node = int(streams[0][0])
            reply, fds = self.bus.call_with_unix_fd_list_sync(
                "org.freedesktop.portal.Desktop",
                "/org/freedesktop/portal/desktop",
                "org.freedesktop.portal.ScreenCast",
                "OpenPipeWireRemote",
                GLib.Variant("(oa{sv})", (self.session, {})),
                GLib.VariantType("(h)"),
                self.Gio.DBusCallFlags.NONE,
                10000,
                None,
                None,
            )
            fd = fds.get(reply.unpack()[0])
        except GLib.Error as error:
            raise CaptureError("portal-unavailable", error.message) from error
        token = str(results.get("restore_token") or "")
        return node, fd, token if _TOKEN_RE.fullmatch(token) else ""

    def close(self) -> None:
        if self._closed_subscription:
            self.bus.signal_unsubscribe(self._closed_subscription)
            self._closed_subscription = 0
        if self.session:
            try:
                self.bus.call_sync("org.freedesktop.portal.Desktop", self.session, "org.freedesktop.portal.Session", "Close", None, None, self.Gio.DBusCallFlags.NONE, 3000, None)
            except self.GLib.Error:
                pass
            self.session = ""


def aspect_matches(expected: tuple[float, float], actual: tuple[int, int], tolerance: float = 0.04) -> bool:
    """Whether the stream has the chosen window's shape (catches a different window picked in the dialog)."""
    if not all(expected) or not all(actual):
        return True
    wanted = expected[0] / expected[1]
    return abs(actual[0] / actual[1] - wanted) <= wanted * tolerance


def crop_for(spec: Spec, buffer_width: int, decorated: bool) -> tuple[int, int, int, int]:
    """Title bar and borders in buffer pixels (none for fullscreen/borderless)."""
    if spec.backend != "kwin" or not decorated or not any(spec.decoration):
        return (0, 0, 0, 0)
    frame_width = spec.width + spec.decoration[0] + spec.decoration[2]
    scale = buffer_width / frame_width if frame_width else spec.scale
    return tuple(int(round(value * scale)) for value in spec.decoration)  # type: ignore[return-value]


class CaptureSession:
    """Everything one Game Window session owns, torn down in one place."""

    def __init__(self, spec: Spec, *, emit: Callable[[dict], None]) -> None:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gio, GLib, Gst  # type: ignore

        self.Gio, self.GLib, self.Gst = Gio, GLib, Gst
        Gst.init(None)
        self.spec = spec
        self.emit = emit
        self.socket = f"brp-game-{secrets.token_hex(6)}"
        self.loop = GLib.MainLoop()
        self.portal: _Portal | None = None
        self.compositor: subprocess.Popen | None = None
        self.pipeline = None
        self.ended = False
        self.reason = "stopped"
        self.started_at = time.time()
        self._decorated = True
        self._buffer_width = self._buffer_height = 0
        self._kwin_misses = 0
        self._fd = -1
        # Pictures the window has sent: tells a still or stopped game from a broken capture.
        self._frames = 0
        self._frames_seen = 0
        self._still_since = time.monotonic()
        self._stall_reported = False

    # -- start ------------------------------------------------------------

    def start(self) -> None:
        node = 0
        token = ""
        if self.spec.backend == "kwin":
            self.portal = _Portal(self.Gio, self.GLib, lambda: self.end("portal-closed"))
            node, self._fd, token = self.portal.open(load_restore_token(self.spec.identity))
        else:
            self._check_x11()
        width, height = screen_size(self.spec)
        self._start_compositor(width, height)
        self._use_fast_refresh(width, height)
        self._start_mirror(node)
        if self.spec.backend == "kwin":
            self._verify_window()
            if token:
                save_restore_token(self.spec.identity, token)
        self._activate_game()
        render_node = isolated_render_node()
        write_state(self._state("running", render_node=render_node or "", width=width, height=height))
        _log.info("Game Window: sharing %s window on private screen %s (%dx%d, GPU %s)", self.spec.backend, self.socket, width, height, render_node or "default")
        self.emit({"state": "ready", "socket": self.socket, "render_node": render_node or "", "width": width, "height": height})

    def _activate_game(self) -> None:
        """Give the game the keyboard: Sunshine types into the active window,
        which is Big Remote Play itself right after Start sharing."""
        from big_remote_play.host import game_windows, x11_windows

        if self.spec.backend == "kwin":
            activated = game_windows.activate_kwin_window(self.spec.handle)
        else:
            activated = x11_windows.activate_window(self.spec.display, int(self.spec.handle, 16))
        if not activated:
            _log.info("Game Window: could not bring the game to the front; click it once so it receives the keyboard")

    def _check_x11(self) -> None:
        from big_remote_play.host import x11_windows

        if not x11_windows.window_alive(self.spec.display, int(self.spec.handle, 16)):
            raise CaptureError("window-closed")
        if not self._x11_private():
            raise CaptureError("compositing-off")

    def _x11_private(self) -> bool:
        from big_remote_play.host import x11_windows
        from big_remote_play.host.game_windows import kwin_compositing_active

        return bool(x11_windows.compositing_active(self.spec.display)) and kwin_compositing_active() is not False

    def _start_compositor(self, width: int, height: int) -> None:
        log = runtime_dir() / "game-window-screen.log"
        log.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with open(log, "w", encoding="utf-8") as handle:
            os.chmod(log, 0o600)
            self.compositor = subprocess.Popen(
                compositor_argv(self.socket, width, height), env=compositor_env(os.environ), stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True
            )
        socket_path = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}") / self.socket
        deadline = time.monotonic() + COMPOSITOR_TIMEOUT
        while time.monotonic() < deadline:
            if self.compositor.poll() is not None:
                raise CaptureError("compositor-failed", f"exit {self.compositor.returncode}")
            if socket_path.exists():
                return
            time.sleep(0.05)
        raise CaptureError("compositor-failed", "the private screen did not start")

    def _use_fast_refresh(self, width: int, height: int) -> None:
        """Best effort: without kscreen-doctor the screen stays at 60 Hz."""
        tool = shutil.which("kscreen-doctor")
        if not tool:
            return
        env = {**compositor_env(os.environ), "WAYLAND_DISPLAY": self.socket}
        # Adding the mode, then selecting it, needs two rounds.
        for _round in range(3):
            try:
                listed = subprocess.run([tool, "-j"], env=env, capture_output=True, text=True, timeout=10, check=False)
                commands = fast_mode_commands(json.loads(listed.stdout or "null"), width, height, tool)
                if not commands:
                    break
                for argv in commands:
                    subprocess.run(argv, env=env, capture_output=True, text=True, timeout=10, check=False)
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                _log.info("Game Window: private screen stays at 60 Hz (%s)", error)
                return

    def _start_mirror(self, node: int) -> None:
        Gst = self.Gst
        try:
            self.pipeline = Gst.parse_launch(mirror_description(self.spec, self.socket, node=node, fd=self._fd))
        except self.GLib.Error as error:
            raise CaptureError("capture-failed", error.message) from error
        # The pipeline owns the PipeWire connection from here on.
        self._fd = -1
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", lambda _bus, message: self.end("capture-failed", message.parse_error()[0].message))
        bus.connect("message::eos", lambda *_args: self.end("capture-failed", "stream ended"))
        bus.connect("message::warning", lambda _bus, message: _log.warning("Game Window: mirror warning: %s", message.parse_warning()[0].message))
        source = self.pipeline.get_by_name("source")
        pad = source.get_static_pad("src")
        pad.connect("notify::caps", self._on_caps)
        pad.add_probe(Gst.PadProbeType.BUFFER, self._count_frame)
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise CaptureError("capture-failed", "the mirror did not start")

    def _count_frame(self, _pad, _info):
        self._frames += 1
        return self.Gst.PadProbeReturn.OK

    def _on_caps(self, pad, _pspec) -> None:
        caps = pad.get_current_caps()
        if caps is None or caps.get_size() == 0:
            return
        _log.info("Game Window: window picture %s", caps.to_string()[:300])
        structure = caps.get_structure(0)
        ok_w, width = structure.get_int("width")
        ok_h, height = structure.get_int("height")
        if ok_w and ok_h:
            self._buffer_width, self._buffer_height = width, height
            self.GLib.idle_add(self._apply_crop)

    def _apply_crop(self) -> bool:
        crop = self.pipeline.get_by_name("crop") if self.pipeline is not None else None
        if crop is not None and self._buffer_width:
            left, top, right, bottom = crop_for(self.spec, self._buffer_width, self._decorated)
            for name, value in (("left", left), ("top", top), ("right", right), ("bottom", bottom)):
                if crop.get_property(name) != value:
                    crop.set_property(name, value)
        return False

    def _verify_window(self) -> None:
        """Wait for the first picture and check it is the chosen window's shape."""
        deadline = time.monotonic() + 5.0
        context = self.GLib.MainContext.default()
        while not self._buffer_width and time.monotonic() < deadline and not self.ended:
            context.iteration(False)
            time.sleep(0.02)
        if self.ended:
            raise CaptureError(self.reason)
        spec = self.spec
        expected = ((spec.width + spec.decoration[0] + spec.decoration[2]) * spec.scale, (spec.height + spec.decoration[1] + spec.decoration[3]) * spec.scale)
        if self._buffer_width and not aspect_matches(expected, (self._buffer_width, self._buffer_height)):
            raise CaptureError("different-window", f"stream {self._buffer_width}x{self._buffer_height}, window {expected[0]:.0f}x{expected[1]:.0f}")

    # -- watch ------------------------------------------------------------

    def watch(self) -> None:
        GLib = self.GLib
        GLib.timeout_add(WATCH_INTERVAL_MS, self._check)
        try:
            import gi

            gi.require_version("GLibUnix", "2.0")
            from gi.repository import GLibUnix  # type: ignore

            add_signal = GLibUnix.signal_add
        except (ImportError, ValueError, AttributeError):
            add_signal = GLib.unix_signal_add  # GLib < 2.80
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            add_signal(GLib.PRIORITY_HIGH, signum, self._on_signal)
        if not self.ended:
            self.loop.run()

    def _on_signal(self) -> bool:
        if self.ended:
            self._quit()
        else:
            self.end("stopped")
        return True

    def _note_frames(self, now: float) -> None:
        """One line when the game stops sending pictures, one when it resumes."""
        if self._frames != self._frames_seen:
            if self._stall_reported:
                _log.info("Game Window: the game is sending pictures again")
            self._frames_seen, self._still_since, self._stall_reported = self._frames, now, False
        elif not self._stall_reported and now - self._still_since >= STALL_SECONDS:
            self._stall_reported = True
            if self._frames:
                _log.warning("Game Window: no new picture from the game for %d s (minimized, paused or not drawing); the last one stays on screen", STALL_SECONDS)
            else:
                _log.warning("Game Window: the game has sent no picture in %d s; the other device sees a black screen", STALL_SECONDS)

    def _check(self) -> bool:
        if self.ended:
            return False
        self._note_frames(time.monotonic())
        if self.compositor is not None and self.compositor.poll() is not None:
            self.end("compositor-exited")
            return False
        if self.spec.backend == "kwin":
            from big_remote_play.host.game_windows import kwin_window_info

            info = kwin_window_info(self.spec.handle)
            if info is None:
                # KWin briefly busy: three silent seconds in a row end the session.
                self._kwin_misses += 1
                if self._kwin_misses >= 3:
                    self.end("window-unknown")
                    return False
                return True
            self._kwin_misses = 0
            if not info:
                self.end("window-closed")
                return False
            decorated = not (bool(info.get("fullscreen")) or bool(info.get("noBorder")))
            if decorated != self._decorated:
                self._decorated = decorated
                self._apply_crop()
        else:
            from big_remote_play.host import x11_windows

            alive = x11_windows.window_alive(self.spec.display, int(self.spec.handle, 16))
            if not alive:
                self.end("window-closed")
                return False
            if not self._x11_private():
                self.end("compositing-off")
                return False
        return True

    # -- stop -------------------------------------------------------------

    def end(self, reason: str, detail: str = "") -> None:
        """Stop showing the game at once; keep the now empty screen briefly.

        The picture stops immediately (the private screen turns black: nothing
        else exists there). The screen itself stays for a few seconds so the
        application can stop Sunshine cleanly, which restores audio; without
        the application it goes after ``HOLD_SECONDS`` anyway.
        """
        if self.ended:
            return
        self.ended = True
        self.reason = reason if reason in REASONS else "capture-failed"
        if self.reason != "stopped":
            _log.warning("Game Window: the game picture stopped (%s); nothing else is shown. %s", self.reason, detail[:200])
        seconds = max(1.0, time.time() - self.started_at)
        _log.info("Game Window: %d pictures from the game in %.0f s (%.1f per second)", self._frames, seconds, self._frames / seconds)
        self.stop_picture()
        write_state(self._state("ended", reason=self.reason, ended_at=time.time()))
        if self.reason == "stopped" or self.compositor is None:
            self._quit()
        else:
            self.GLib.timeout_add_seconds(HOLD_SECONDS, self._quit)

    def _state(self, state: str, **extra: object) -> dict[str, object]:
        return {
            "version": 1,
            "state": state,
            "pid": os.getpid(),
            "pgid": self.compositor.pid if self.compositor is not None else 0,
            "socket": self.socket,
            "name": self.spec.name,
            "started_at": self.started_at,
            **extra,
        }

    def _quit(self) -> bool:
        if self.loop.is_running():
            self.loop.quit()
        return False

    def stop_picture(self) -> None:
        if self.pipeline is not None:
            self.pipeline.set_state(self.Gst.State.NULL)
            self.pipeline = None
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1
        if self.portal is not None:
            self.portal.close()
            self.portal = None

    def teardown(self) -> None:
        self.stop_picture()
        if self.compositor is not None:
            stop_process_group(self.compositor.pid, wait=self.compositor.wait)
            self.compositor = None


def stop_process_group(pid: int, *, wait: Callable[..., object] | None = None, grace: float = STOP_GRACE) -> None:
    """End a process group started with ``start_new_session`` (TERM, then KILL)."""
    if pid <= 1:
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        try:
            if wait is not None:
                wait(timeout=0.1)
                return
            os.killpg(pid, 0)
        except subprocess.TimeoutExpired:
            continue
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run(stdin=sys.stdin, stdout=sys.stdout) -> int:
    def emit(message: dict) -> None:
        stdout.write(json.dumps(message) + "\n")
        stdout.flush()

    try:
        spec = Spec.from_mapping(json.loads(stdin.readline() or "null"))
    except (CaptureError, ValueError) as error:
        emit({"state": "failed", "reason": getattr(error, "reason", "invalid-spec")})
        return 1
    session = None
    try:
        session = CaptureSession(spec, emit=emit)
        session.start()
    except CaptureError as error:
        if session is not None:
            session.teardown()
        _log.warning("Game Window: could not start (%s) %s", error.reason, error.detail[:200])
        write_state({"version": 1, "state": "ended", "reason": error.reason, "pid": os.getpid(), "ended_at": time.time()})
        emit({"state": "failed", "reason": error.reason})
        return 1
    except Exception as error:  # a broken GStreamer/GLib setup must still clean up
        if session is not None:
            session.teardown()
        _log.error("Game Window: unexpected failure: %s", error)
        write_state({"version": 1, "state": "ended", "reason": "capture-failed", "pid": os.getpid(), "ended_at": time.time()})
        emit({"state": "failed", "reason": "capture-failed"})
        return 1
    try:
        session.watch()
    finally:
        session.teardown()
        state = read_state()
        if not state or state.get("pid") != os.getpid() or state.get("state") == "running":
            write_state({"version": 1, "state": "ended", "reason": session.reason, "pid": os.getpid(), "ended_at": time.time()})
    return 0


# ── used by the application ───────────────────────────────────────────────


@dataclass(frozen=True)
class Started:
    pid: int
    socket: str
    render_node: str
    width: int
    height: int


def helper_argv() -> list[str]:
    return [sys.executable, "-I", str(Path(__file__).resolve()), "run"]


def start_helper(spec: Spec, *, timeout: float = READY_TIMEOUT, popen: Callable[..., subprocess.Popen] = subprocess.Popen) -> tuple[Started, subprocess.Popen]:
    """Start the helper and wait until the private screen shows the game.

    Blocking (the person may be confirming the window): call it from a worker.
    """
    stop_helper()  # never two private screens
    process = popen(helper_argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None, text=True, start_new_session=True)
    try:
        if process.stdin is not None:
            process.stdin.write(json.dumps(spec.to_mapping()) + "\n")
            process.stdin.close()
    except (OSError, ValueError):
        pass
    line = _read_line(process, timeout)
    try:
        message = json.loads(line) if line else {}
    except ValueError:
        message = {}
    if not isinstance(message, dict) or message.get("state") != "ready":
        stop_process_group(process.pid, wait=process.wait)
        reason = str(message.get("reason") or "capture-failed") if isinstance(message, dict) else "capture-failed"
        if not line and process.poll() is None:
            reason = "portal-cancelled"
        raise CaptureError(reason)
    socket = str(message.get("socket") or "")
    render_node = str(message.get("render_node") or "")
    if not _SOCKET_RE.fullmatch(socket) or (render_node and not _RENDER_NODE_RE.fullmatch(render_node)):
        stop_process_group(process.pid, wait=process.wait)
        raise CaptureError("capture-failed", "unexpected answer")
    return Started(process.pid, socket, render_node, int(message.get("width") or 0), int(message.get("height") or 0)), process


def _read_line(process: subprocess.Popen, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    stream = process.stdout
    if stream is None:
        return ""
    while time.monotonic() < deadline:
        ready, _w, _x = select.select([stream], [], [], 0.25)
        if ready:
            return stream.readline().strip()
        if process.poll() is not None:
            return stream.readline().strip()
    return ""


def helper_running(state: Mapping[str, object] | None = None) -> bool:
    state = read_state() if state is None else state
    if not state or state.get("state") != "running":
        return False
    pid = state.get("pid")
    return isinstance(pid, int) and _is_helper(pid)


def _is_helper(pid: int) -> bool:
    try:
        cmdline = Path(f"/proc/{int(pid)}/cmdline").read_bytes()
    except (OSError, ValueError):
        return False
    return b"window_capture.py" in cmdline


def _is_private_screen(pid: int, socket: str) -> bool:
    if not _SOCKET_RE.fullmatch(socket):
        return False
    try:
        return socket.encode() in Path(f"/proc/{int(pid)}/cmdline").read_bytes().split(b"\0")
    except (OSError, ValueError):
        return False


def stop_helper(state: Mapping[str, object] | None = None) -> None:
    """Stop the running helper (it tears everything down); clean up after a crash."""
    state = read_state() if state is None else state
    if not state:
        return
    pid, pgid = state.get("pid"), state.get("pgid")
    if isinstance(pid, int) and _is_helper(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + STOP_GRACE
        while time.monotonic() < deadline and _is_helper(pid):
            time.sleep(0.1)
        if _is_helper(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    # A helper killed outright leaves its private screen behind. The group is
    # ended only while its leader is still that screen (a PID can be reused).
    socket = str(state.get("socket") or "")
    if isinstance(pgid, int) and pgid > 1 and _is_private_screen(pgid, socket):
        stop_process_group(pgid)
    if state.get("state") == "running":
        write_state({"version": 1, "state": "ended", "reason": "stopped", "pid": pid if isinstance(pid, int) else 0, "ended_at": time.time()})


def _log_to_app_file() -> None:
    """Write to Big Remote Play's daily log, where the application writes.

    Started from the desktop menu, this process's stderr ends in the journal,
    out of reach of anyone looking at the application's log for a report.
    """
    try:
        from big_remote_play.utils.logger import Logger

        Logger()
    except Exception:  # a log that cannot be opened must not stop sharing
        logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    _log_to_app_file()
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["run"]:
        return run()
    sys.stderr.write("usage: window_capture.py run < spec.json\n")
    return 2


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    sys.exit(main())
