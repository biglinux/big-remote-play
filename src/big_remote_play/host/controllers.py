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
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
import re

INPUT_DEVICES = Path("/proc/bus/input/devices")
VIRTUAL_INPUT_DEVICES = (Path("/dev/uinput"), Path("/dev/uhid"))

_JOYSTICK_HANDLER = re.compile(r"\bjs\d+\b")


@dataclass(frozen=True)
class ControllerReport:
    local: tuple[str, ...] = ()  # names of controllers plugged into this computer
    unusable: tuple[str, ...] = ()  # device nodes Sunshine cannot open, e.g. "/dev/uhid"


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


def unusable_devices(paths=VIRTUAL_INPUT_DEVICES, access: Callable[[str, int], bool] = os.access) -> list[str]:
    """Device nodes this user cannot open for reading and writing."""
    return [str(path) for path in paths if not access(str(path), os.R_OK | os.W_OK)]


def controller_report(read: Callable[[Path], str] = lambda path: path.read_text(encoding="utf-8", errors="replace")) -> ControllerReport:
    try:
        devices = read(INPUT_DEVICES)
    except OSError:
        devices = ""
    return ControllerReport(tuple(local_controllers(devices)), tuple(unusable_devices()))
