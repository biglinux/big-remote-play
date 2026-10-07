"""Can the controller of the person connecting reach games here? (read-only)

Sunshine gives each controller of the other computer a virtual controller on
this one, through ``/dev/uinput`` or ``/dev/uhid``. Two things stop it from
reaching a game, and neither shows on the other computer:

* the virtual controller cannot be created: this user cannot open those
  devices. Sunshine's package loads ``uhid`` at boot and its udev rule opens
  it to the ``input`` group, so this happens mainly after installing or
  updating Sunshine, before the next restart;
* a controller plugged into this computer is found first. A game that reads
  one controller takes the first one, and the virtual controller is always
  created after this computer's own.

Virtual devices (Sunshine's own, Steam Input's) live under
``/devices/virtual`` in sysfs; physical controllers, USB or Bluetooth, never do.

Sunshine's log says what happened in the current connection: each controller
of the other computer it created ("Gamepad N will be …"), or that it could
not create one. No line at all means the other computer sent no controller:
not connected there, not recognised by Moonlight, or Moonlight's window was
not in front when it started.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import datetime as _dt
import os
from pathlib import Path
import re

INPUT_DEVICES = Path("/proc/bus/input/devices")
VIRTUAL_INPUT_DEVICES = (Path("/dev/uinput"), Path("/dev/uhid"))

_JOYSTICK_HANDLER = re.compile(r"\bjs\d+\b")
# Sunshine 2026: "[2026-10-05 15:54:44.218]: Info: Gamepad 0 will be Sunshine
# (libvirtualhid) X-Box 360 Controller (manual selection)".
_ARRIVED = re.compile(r"Gamepad \d+ will be (.+?) \((?:manual selection|auto-selected[^)]*)\)")
_DEVICE_FAILURE = re.compile(r"failed to (?:open|create) [^\n]*(?:uinput|uhid)|virtualhid-not-available", re.IGNORECASE)
_STAMP = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)")
SUNSHINE_LOG_TAIL = 256 * 1024


@dataclass(frozen=True)
class ConnectionControllers:
    """What Sunshine's log says about the current connection's controllers."""

    connected_at: _dt.datetime | None  # local time of the last CLIENT CONNECTED
    arrived: tuple[str, ...] = ()  # the virtual controllers Sunshine created
    failed: bool = False  # Sunshine could not create a controller


@dataclass(frozen=True)
class ControllerReport:
    local: tuple[str, ...] = ()  # names of controllers plugged into this computer
    unusable: tuple[str, ...] = ()  # device nodes Sunshine cannot open, e.g. "/dev/uhid"
    connection: ConnectionControllers | None = None  # None: nobody is connected
    disabled: bool = False  # "controller = disabled" in Sunshine's settings


def connection_controllers(log_text: str) -> ConnectionControllers | None:
    """The current connection in Sunshine's log, or ``None`` when nobody is connected."""
    start = log_text.rfind("CLIENT CONNECTED")
    if start < 0 or log_text.find("CLIENT DISCONNECTED", start) >= 0:
        return None
    line_start = log_text.rfind("\n", 0, start) + 1
    stamp = _STAMP.match(log_text[line_start:start])
    connected_at = _dt.datetime.strptime(stamp.group(1), "%Y-%m-%d %H:%M:%S") if stamp else None
    segment = log_text[start:]
    return ConnectionControllers(
        connected_at,
        tuple(dict.fromkeys(m.group(1).strip() for m in _ARRIVED.finditer(segment))),
        bool(_DEVICE_FAILURE.search(segment)),
    )


def controllers_disabled(conf_text: str) -> bool:
    """Whether Sunshine's settings turn the other computer's controllers off."""
    for line in conf_text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "controller":
            return value.strip().lower() in ("disabled", "false", "0", "off")
    return False


def _tail(path: Path) -> str:
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        handle.seek(max(0, handle.tell() - SUNSHINE_LOG_TAIL))
        return handle.read().decode("utf-8", errors="replace")


def local_controllers(devices_text: str) -> list[str]:
    """Physical controllers in ``/proc/bus/input/devices``, in the kernel's order."""
    found: list[str] = []
    for block in re.split(r"\n\s*\n", devices_text or ""):
        name = sysfs = handlers = ""
        for line in block.splitlines():
            key, _, value = line.partition(": ")
            if key == "N":
                name = value.partition("=")[2].strip().strip('"')
            elif key == "S":
                sysfs = value.partition("=")[2].strip()
            elif key == "H":
                handlers = value.partition("=")[2]
        if not _JOYSTICK_HANDLER.search(handlers) or not sysfs or sysfs.startswith("/devices/virtual/"):
            continue
        found.append(name or "?")
    return found


def unusable_devices(paths=VIRTUAL_INPUT_DEVICES, access: Callable[[str, int], bool] = os.access, sandboxed: bool | None = None) -> list[str]:
    """Device nodes this user cannot open for reading and writing.

    Inside Flatpak the host's device nodes are not visible, so nothing can be
    said about them from here (Sunshine itself runs outside the sandbox)."""
    if sandboxed if sandboxed is not None else os.path.exists("/.flatpak-info"):
        return []
    return [str(path) for path in paths if not access(str(path), os.R_OK | os.W_OK)]


def controller_report(
    read: Callable[[Path], str] = lambda path: path.read_text(encoding="utf-8", errors="replace"),
    sunshine_dir: Path | None = None,
) -> ControllerReport:
    try:
        devices = read(INPUT_DEVICES)
    except OSError:
        devices = ""
    if sunshine_dir is None:
        from big_remote_play import paths

        sunshine_dir = paths.SUNSHINE_CONFIG_DIR
    try:
        connection = connection_controllers(_tail(sunshine_dir / "sunshine.log"))
    except OSError:
        connection = None
    try:
        disabled = controllers_disabled(read(sunshine_dir / "sunshine.conf"))
    except OSError:
        disabled = False
    return ControllerReport(tuple(local_controllers(devices)), tuple(unusable_devices()), connection, disabled)
