"""Host input priority: this computer's own mouse and keyboard win.

Sunshine turns the other device's keyboard and mouse into virtual input
devices (uinput). The desktop reads them like physical ones, so two people
move one pointer. While this helper runs, local activity on a *physical*
keyboard or pointer pauses the virtual keyboard and mouse: held keys and
buttons are released into them first (no stuck key reaches the game), then
an exclusive grab (``EVIOCGRAB``) keeps their events away from the desktop.
After ``delay`` seconds without local activity the grab is dropped and the
other device has control again. Controllers are never touched.

The helper is a separate process, like the Game Window capture. It reads its
delay as one JSON line on stdin, answers with JSON lines on stdout (``ready``,
``paused``, ``resumed``, ``devices``, ``unavailable``) and exits when stdin
closes; the kernel drops a grab when its holder exits, so the other device
can never stay locked out. It never reports which key was pressed, only that
a pause started or ended. See docs/host-input-priority.md.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import ctypes
import ctypes.util
from dataclasses import dataclass
import errno
import fcntl
import json
import logging
import os
from pathlib import Path
import select
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time

_log = logging.getLogger("big-remoteplay")

SPEC_VERSION = 1
DEFAULT_DELAY = 5
MIN_DELAY, MAX_DELAY = 1, 30
# The choices offered in Preferences (seconds).
DELAY_CHOICES = (1, 2, 3, 5, 10, 15, 20, 30)

# Sunshine's virtual keyboard, mice, touch screen and pen: libvirtualhid
# (Sunshine 2026.5 and newer) and inputtino (older releases).
REMOTE_NAMES = frozenset(
    {
        "libvirtualhid Keyboard",
        "libvirtualhid Mouse",
        "libvirtualhid Mouse (Absolute)",
        "libvirtualhid Touchscreen",
        "libvirtualhid Pen Tablet",
        "Keyboard passthrough",
        "Mouse passthrough",
        "Mouse passthrough (absolute)",
        "Touch passthrough",
        "Pen passthrough",
    }
)
# Other devices Sunshine creates (controllers, their touchpads and motion
# sensors). They are neither paused nor counted as local activity.
_SUNSHINE_MARKERS = ("libvirtualhid", "passthrough", "Sunshine ")

LAUNCHER = "/usr/share/big-remote-play/scripts/input-priority-helper.sh"

# linux/input-event-codes.h
EV_SYN, EV_KEY, EV_REL, EV_ABS = 0x00, 0x01, 0x02, 0x03
SYN_REPORT = 0
KEY_MAX = 0x2FF
REL_X, REL_Y = 0x00, 0x01
ABS_X, ABS_Y = 0x00, 0x01
KEY_ENTER, KEY_A, KEY_Z, KEY_SPACE = 28, 30, 44, 57
BTN_JOYSTICK, BTN_GAMEPAD = 0x120, 0x130
BTN_LEFT, BTN_TOOL_PEN, BTN_TOOL_FINGER, BTN_TOUCH, BTN_STYLUS = 0x110, 0x140, 0x145, 0x14A, 0x14B

EVIOCGRAB = 0x40044590
_KEY_BYTES = (KEY_MAX + 1) // 8
# EVIOCGKEY(len) = _IOC(_IOC_READ, 'E', 0x18, len)
EVIOCGKEY = (2 << 30) | (_KEY_BYTES << 16) | (ord("E") << 8) | 0x18
_EVENT = struct.Struct("llHHi")  # struct input_event (native timeval)
_BITS_PER_LONG = ctypes.sizeof(ctypes.c_long) * 8


# ── which devices are whose ───────────────────────────────────────────────


@dataclass(frozen=True)
class DeviceInfo:
    """One evdev node as sysfs describes it (readable without permissions)."""

    node: str  # /dev/input/eventN
    name: str
    virtual: bool  # lives under /sys/devices/virtual (uinput, uhid)
    ev: int = 0
    key: int = 0
    rel: int = 0
    abs: int = 0


def parse_bitmap(text: str, bits_per_long: int = _BITS_PER_LONG) -> int:
    """A sysfs capability bitmap ("10000 0 1f") as one integer."""
    value = 0
    for word in text.split():
        try:
            value = (value << bits_per_long) | int(word, 16)
        except ValueError:
            return 0
    return value


def _has(bitmap: int, *codes: int) -> bool:
    return all(bitmap >> code & 1 for code in codes)


def _any_in(bitmap: int, first: int, last: int) -> bool:
    return any(bitmap >> code & 1 for code in range(first, last + 1))


def classify(device: DeviceInfo) -> str:
    """``remote`` (the other device's), ``local`` (a person here) or ``ignored``."""
    if device.virtual and device.name in REMOTE_NAMES:
        return "remote"
    if device.virtual and any(marker in device.name for marker in _SUNSHINE_MARKERS):
        return "ignored"
    keys = device.key if device.ev >> EV_KEY & 1 else 0
    keyboard = _has(keys, KEY_A, KEY_Z, KEY_SPACE, KEY_ENTER)
    if keyboard:
        return "local"
    if _any_in(keys, BTN_JOYSTICK, BTN_GAMEPAD + 0x0F):
        return "ignored"  # a controller (games read those directly)
    relative = device.ev >> EV_REL & 1 and _has(device.rel, REL_X, REL_Y)
    absolute = device.ev >> EV_ABS & 1 and _has(device.abs, ABS_X, ABS_Y) and any(_has(keys, code) for code in (BTN_LEFT, BTN_TOUCH, BTN_TOOL_FINGER, BTN_STYLUS, BTN_TOOL_PEN))
    return "local" if relative or absolute else "ignored"


def read_device(event_name: str, *, sys_root: Path = Path("/sys/class/input"), dev_root: Path = Path("/dev/input")) -> DeviceInfo | None:
    """sysfs facts about ``eventN``; ``None`` when it is gone or malformed."""
    if not event_name.startswith("event") or not event_name[5:].isdigit():
        return None
    device = sys_root / event_name / "device"
    try:
        name = (device / "name").read_text(encoding="utf-8", errors="replace").strip()
        location = str(device.resolve())
        caps = {key: parse_bitmap((device / "capabilities" / key).read_text(encoding="ascii", errors="replace")) for key in ("ev", "key", "rel", "abs")}
    except OSError:
        return None
    return DeviceInfo(str(dev_root / event_name), name, "/devices/virtual/" in location, caps["ev"], caps["key"], caps["rel"], caps["abs"])


def list_devices(*, sys_root: Path = Path("/sys/class/input"), dev_root: Path = Path("/dev/input")) -> list[DeviceInfo]:
    try:
        names = sorted(entry.name for entry in sys_root.iterdir() if entry.name.startswith("event"))
    except OSError:
        return []
    found = (read_device(name, sys_root=sys_root, dev_root=dev_root) for name in names)
    return [device for device in found if device is not None]


def devices_accessible(devices: Iterable[DeviceInfo], *, access: Callable[[str, int], bool] = os.access) -> bool:
    """Whether this user can read local devices and grab Sunshine's (``input`` group)."""
    wanted = [(device.node, os.R_OK | os.W_OK if classify(device) == "remote" else os.R_OK) for device in devices if classify(device) != "ignored"]
    return bool(wanted) and all(access(node, mode) for node, mode in wanted)


# ── the decision ──────────────────────────────────────────────────────────


def clamp_delay(value: object) -> int:
    try:
        number = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return DEFAULT_DELAY
    return max(MIN_DELAY, min(MAX_DELAY, number))


class Arbiter:
    """Who has the mouse and keyboard. Pure: the caller supplies the time."""

    def __init__(self, delay: int = DEFAULT_DELAY) -> None:
        self.delay = clamp_delay(delay)
        self.paused = False
        self._last_local = 0.0

    @property
    def deadline(self) -> float:
        return self._last_local + self.delay

    def local_activity(self, now: float) -> bool:
        """Note local use; ``True`` when it starts a pause."""
        self._last_local = now
        if self.paused:
            return False
        self.paused = True
        return True

    def expire(self, now: float) -> bool:
        """``True`` when the pause ends now."""
        if self.paused and now >= self.deadline:
            self.paused = False
            return True
        return False

    def wait(self, now: float) -> float | None:
        """Seconds until something can change by itself (``None``: nothing will)."""
        return max(0.0, self.deadline - now) if self.paused else None

    def set_delay(self, delay: object) -> None:
        self.delay = clamp_delay(delay)


# ── the kernel ────────────────────────────────────────────────────────────


class Evdev:
    """The few evdev calls the helper makes. Tests replace it."""

    def open(self, node: str, *, write: bool) -> int:
        return os.open(node, (os.O_RDWR if write else os.O_RDONLY) | os.O_NONBLOCK | os.O_CLOEXEC)

    def close(self, fd: int) -> None:
        try:
            os.close(fd)
        except OSError:
            pass

    def grab(self, fd: int, on: bool) -> None:
        fcntl.ioctl(fd, EVIOCGRAB, 1 if on else 0)

    def pressed(self, fd: int) -> list[int]:
        """Keys and buttons the device holds down now (kernel state)."""
        state = bytearray(_KEY_BYTES)
        fcntl.ioctl(fd, EVIOCGKEY, state)
        return [code for code in range(_KEY_BYTES * 8) if state[code // 8] >> (code % 8) & 1]

    def release(self, fd: int, codes: Iterable[int]) -> None:
        """Write key/button releases into the device, then one report."""
        payload = b"".join(_EVENT.pack(0, 0, EV_KEY, code, 0) for code in codes)
        if payload:
            os.write(fd, payload + _EVENT.pack(0, 0, EV_SYN, SYN_REPORT, 0))

    def read(self, fd: int) -> list[tuple[int, int, int]]:
        """Pending events as ``(type, code, value)``; ``OSError`` when unplugged."""
        events = []
        while True:
            try:
                data = os.read(fd, _EVENT.size * 64)
            except BlockingIOError:
                return events
            if not data:
                return events
            for offset in range(0, len(data) - _EVENT.size + 1, _EVENT.size):
                _sec, _usec, kind, code, value = _EVENT.unpack_from(data, offset)
                events.append((kind, code, value))
            if len(data) < _EVENT.size * 64:
                return events


def is_activity(events: Iterable[tuple[int, int, int]]) -> bool:
    """A key, button, movement, scroll or touch (not sync, LEDs or timestamps)."""
    for kind, _code, value in events:
        if kind in (EV_KEY, EV_ABS) or (kind == EV_REL and value != 0):
            return True
    return False


# ── one session ───────────────────────────────────────────────────────────


class PrioritySession:
    """Devices, the arbiter and the grab, without the waiting loop."""

    RETRIES = 3

    def __init__(self, delay: int, *, evdev: Evdev | None = None, emit: Callable[[dict], None] = lambda _message: None, scan: Callable[[], list[DeviceInfo]] | None = None) -> None:
        self.arbiter = Arbiter(delay)
        self.evdev = evdev or Evdev()
        self.emit = emit
        self.scan = scan or (lambda: list_devices())
        self.remote: dict[str, int] = {}  # node → fd (read/write)
        self.local: dict[str, int] = {}  # node → fd (read only)
        self.denied: set[str] = set()
        self._counts: tuple[int, int] | None = None

    # -- devices --------------------------------------------------------

    def refresh_devices(self) -> None:
        """Open new devices, forget removed ones; a new Sunshine device joins a pause in progress."""
        seen = {device.node: classify(device) for device in self.scan()}
        for table in (self.remote, self.local):
            for node in [node for node in table if seen.get(node) is None]:
                self.evdev.close(table.pop(node))
        self.denied &= set(seen)
        for node, kind in seen.items():
            if kind == "ignored" or node in self.remote or node in self.local:
                continue
            try:
                fd = self.evdev.open(node, write=kind == "remote")
            except OSError as error:
                if error.errno in (errno.EACCES, errno.EPERM):
                    self.denied.add(node)
                continue
            self.denied.discard(node)
            if kind == "remote":
                self.remote[node] = fd
                if self.arbiter.paused:
                    self._pause_device(fd)
            else:
                self.local[node] = fd
        counts = (len(self.remote), len(self.local))
        if counts != self._counts:
            self._counts = counts
            message: dict[str, object] = {"event": "devices", "remote": counts[0], "local": counts[1]}
            if self.denied:
                message["denied"] = len(self.denied)
            self.emit(message)

    def fds(self) -> list[int]:
        return [*self.local.values(), *self.remote.values()]

    # -- pause and resume -----------------------------------------------

    def _pause_device(self, fd: int) -> None:
        """Release what is held where the desktop sees it, then take the device."""
        try:
            for _attempt in range(self.RETRIES):
                self.evdev.release(fd, self.evdev.pressed(fd))
                self.evdev.grab(fd, True)
                late = self.evdev.pressed(fd)
                if not late:
                    return
                # Pressed between reading and grabbing: the desktop saw the
                # press, so its release must reach the desktop too.
                self.evdev.grab(fd, False)
        except OSError:
            pass

    def _resume_device(self, fd: int) -> None:
        """Forget what was pressed during the pause (only we saw it), then give the device back.

        Without this the kernel would drop the next press of such a key: it
        still believes the key is down.
        """
        try:
            for _attempt in range(self.RETRIES):
                self.evdev.release(fd, self.evdev.pressed(fd))
                self._drain(fd)
                if not self.evdev.pressed(fd):
                    break
            self.evdev.grab(fd, False)
        except OSError:
            pass

    def _drain(self, fd: int) -> None:
        try:
            self.evdev.read(fd)
        except OSError:
            pass

    def on_local_activity(self, now: float) -> None:
        if self.arbiter.local_activity(now):
            for fd in self.remote.values():
                self._pause_device(fd)
            self.emit({"event": "paused", "delay": self.arbiter.delay})

    def on_timeout(self, now: float) -> None:
        if self.arbiter.expire(now):
            for fd in self.remote.values():
                self._resume_device(fd)
            self.emit({"event": "resumed"})

    def set_delay(self, delay: object) -> None:
        self.arbiter.set_delay(delay)

    # -- readable devices -----------------------------------------------

    def on_readable(self, fd: int, now: float) -> None:
        local = next((node for node, value in self.local.items() if value == fd), None)
        try:
            events = self.evdev.read(fd)
        except OSError:
            # Unplugged: the next scan forgets it.
            self._forget(fd)
            return
        if local is not None and is_activity(events):
            self.on_local_activity(now)
        # Sunshine's events while paused are read and dropped (only we get them).

    def drop_invalid(self) -> None:
        for fd in self.fds():
            try:
                os.fstat(fd)
            except OSError:
                self._forget(fd)

    def _forget(self, fd: int) -> None:
        for table in (self.remote, self.local):
            for node in [node for node, value in table.items() if value == fd]:
                table.pop(node)
        self.evdev.close(fd)

    def close(self) -> None:
        """Give the other device its keyboard and mouse back, then let go."""
        if self.arbiter.paused:
            self.arbiter.paused = False
            for fd in self.remote.values():
                self._resume_device(fd)
            self.emit({"event": "resumed"})
        for fd in self.fds():
            self.evdev.close(fd)
        self.remote.clear()
        self.local.clear()


# ── the helper process ────────────────────────────────────────────────────


class _Inotify:
    """New, removed and re-permissioned nodes under /dev/input."""

    IN_ATTRIB, IN_CREATE, IN_DELETE = 0x4, 0x100, 0x200
    IN_NONBLOCK, IN_CLOEXEC = 0o4000, 0o2000000

    def __init__(self, path: str = "/dev/input") -> None:
        self.fd = -1
        name = ctypes.util.find_library("c")
        try:
            libc = ctypes.CDLL(name or "libc.so.6", use_errno=True)
            fd = libc.inotify_init1(self.IN_NONBLOCK | self.IN_CLOEXEC)
            if fd >= 0 and libc.inotify_add_watch(fd, path.encode(), self.IN_ATTRIB | self.IN_CREATE | self.IN_DELETE) >= 0:
                self.fd = fd
            elif fd >= 0:
                os.close(fd)
        except (OSError, AttributeError):
            self.fd = -1

    def drain(self) -> None:
        try:
            while os.read(self.fd, 4096):
                pass
        except OSError:
            pass


RESCAN_DELAY = 0.5  # udev sets permissions on new nodes shortly after they appear
FALLBACK_RESCAN = 5.0  # without inotify
MAX_CONTROL_LINE = 4096


class _Stop(Exception):
    """A signal asked the helper to give the devices back and exit."""


class _Control:
    """JSON lines from Big Remote Play, read unbuffered from one descriptor."""

    def __init__(self, fd: int) -> None:
        self.fd = fd
        self.buffer = b""
        self.queue: list[object] = []
        self.closed = False

    def feed(self) -> None:
        chunk = os.read(self.fd, 4096)
        if not chunk:
            self.closed = True
            return
        self.buffer += chunk
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            try:
                self.queue.append(json.loads(line or b"null"))
            except ValueError:
                continue
        if len(self.buffer) > MAX_CONTROL_LINE:
            self.buffer = b""  # not ours: nothing sends lines this long

    def take(self) -> list[object]:
        messages, self.queue = self.queue, []
        return messages

    def first(self, timeout: float = 10.0) -> object:
        """The spec; anything sent right behind it stays queued for the loop."""
        deadline = time.monotonic() + timeout
        while not self.queue and not self.closed and time.monotonic() < deadline:
            ready, _w, _x = select.select([self.fd], [], [], max(0.0, deadline - time.monotonic()))
            if ready:
                self.feed()
        return self.queue.pop(0) if self.queue else None


def run(stdin=sys.stdin, stdout=sys.stdout) -> int:
    def emit(message: dict) -> None:
        try:
            stdout.write(json.dumps(message) + "\n")
            stdout.flush()
        except (OSError, ValueError):
            pass

    control = _Control(stdin.fileno())
    spec = control.first()
    if not isinstance(spec, dict) or spec.get("version") != SPEC_VERSION:
        emit({"event": "unavailable", "reason": "invalid-spec"})
        return 1
    session = PrioritySession(clamp_delay(spec.get("delay")), emit=emit)
    session.refresh_devices()
    if session.denied and (not session.remote or not session.local):
        emit({"event": "unavailable", "reason": "permission"})
        session.close()
        return 1
    emit({"event": "ready", "remote": len(session.remote), "local": len(session.local)})

    def on_signal(_signum, _frame) -> None:
        raise _Stop()

    if threading.current_thread() is threading.main_thread():  # always, as its own process
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(signum, on_signal)
    watcher = _Inotify()
    rescan_at: float | None = None

    def apply_control() -> None:
        for message in control.take():
            if isinstance(message, dict) and "delay" in message:
                session.set_delay(message["delay"])

    try:
        apply_control()
        while True:
            now = time.monotonic()
            waits = [value for value in (session.arbiter.wait(now), None if rescan_at is None else max(0.0, rescan_at - now)) if value is not None]
            if watcher.fd < 0:
                waits.append(FALLBACK_RESCAN)
            watched = [control.fd, *session.fds(), *([watcher.fd] if watcher.fd >= 0 else [])]
            try:
                readable, _w, _x = select.select(watched, [], [], min(waits) if waits else None)
            except (OSError, ValueError):
                # A device vanished between listing and waiting.
                session.drop_invalid()
                session.refresh_devices()
                continue
            now = time.monotonic()
            for fd in readable:
                if fd == control.fd:
                    control.feed()
                    apply_control()
                    if control.closed:
                        return 0  # Big Remote Play stopped sharing or went away
                elif fd == watcher.fd:
                    watcher.drain()
                    rescan_at = now + RESCAN_DELAY
                else:
                    session.on_readable(fd, now)
            if (rescan_at is not None and now >= rescan_at) or (watcher.fd < 0 and not readable):
                rescan_at = None
                session.refresh_devices()
            session.on_timeout(now)
    except _Stop:
        return 0
    finally:
        session.close()
        if watcher.fd >= 0:
            os.close(watcher.fd)


# ── used by the application ───────────────────────────────────────────────


def helper_argv(
    *, devices: Iterable[DeviceInfo] | None = None, which: Callable[[str], str | None] = shutil.which, launcher: str = LAUNCHER, exists: Callable[[str], bool] = os.path.exists
) -> list[str] | None:
    """How to start the helper: as this user when the devices allow it, else
    through PolicyKit as the installed program; ``None`` when neither works."""
    listed = list_devices() if devices is None else list(devices)
    if devices_accessible(listed):
        return [sys.executable, "-I", str(Path(__file__).resolve()), "run"]
    if exists(launcher) and which("pkexec"):
        return ["pkexec", launcher]
    return None


def has_remote_devices(devices: Iterable[DeviceInfo] | None = None) -> bool:
    return any(classify(device) == "remote" for device in (list_devices() if devices is None else devices))


class PriorityProcess:
    """The running helper, seen from the application (no GTK here).

    ``on_event`` is called on a reader thread for every message, and once with
    ``{"event": "stopped"}`` when the helper ends; the UI marshals it.
    """

    def __init__(self, delay: int, on_event: Callable[[Mapping[str, object]], None], *, argv: list[str] | None = None, popen: Callable[..., subprocess.Popen] = subprocess.Popen) -> None:
        self.delay = clamp_delay(delay)
        self.on_event = on_event
        self.argv = argv
        self.popen = popen
        self.process: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def start(self) -> bool:
        argv = self.argv if self.argv is not None else helper_argv()
        if argv is None:
            self.on_event({"event": "unavailable", "reason": "permission"})
            return False
        try:
            process = self.popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1, start_new_session=True)
        except OSError as error:
            _log.warning("Host input priority: could not start the helper (%s)", error)
            self.on_event({"event": "unavailable", "reason": "start-failed"})
            return False
        with self._lock:
            self.process = process
        self._send({"version": SPEC_VERSION, "delay": self.delay})
        threading.Thread(target=self._read, args=(process,), daemon=True).start()
        return True

    def _send(self, message: Mapping[str, object]) -> None:
        with self._lock:
            stream = self.process.stdin if self.process is not None else None
            if stream is None:
                return
            try:
                stream.write(json.dumps(dict(message)) + "\n")
                stream.flush()
            except (OSError, ValueError):
                pass

    def set_delay(self, delay: object) -> None:
        self.delay = clamp_delay(delay)
        self._send({"delay": self.delay})

    def _read(self, process: subprocess.Popen) -> None:
        stream = process.stdout
        ready = False
        if stream is not None:
            for line in stream:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if isinstance(message, dict) and isinstance(message.get("event"), str):
                    ready = ready or message["event"] == "ready"
                    self.on_event(message)
        code = process.wait()
        if not ready and code in (126, 127):
            # pkexec: not authorized, or the dialog was dismissed.
            self.on_event({"event": "unavailable", "reason": "not-authorized"})
        self.on_event({"event": "stopped", "code": code})

    def stop(self) -> None:
        """Close the helper's input: it gives the devices back and exits.

        A helper started through PolicyKit runs as root and cannot be
        signalled by this user; end of input is the one way to stop it.
        """
        with self._lock:
            process, self.process = self.process, None
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass

        def reap() -> None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    process.terminate()
                except (OSError, PermissionError):
                    pass

        threading.Thread(target=reap, daemon=True).start()

    @property
    def running(self) -> bool:
        with self._lock:
            return self.process is not None and self.process.poll() is None


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["run"]:
        return run()
    sys.stderr.write("usage: input_priority.py run < spec.json\n")
    return 2


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    sys.exit(main())
