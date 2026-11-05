"""The screens of this computer, for where Moonlight opens.

Moonlight has no setting for the screen: it creates the stream window on the
screen of its own (Qt) window (logged as "Qt UI screen is at (x,y)" then
"SDL found matching display N"). On X11 the window can be placed with Qt's
standard ``-qwindowgeometry`` option, so the stream opens on the chosen
screen; verified on a three-monitor X11 desktop in fullscreen and borderless
modes. Wayland lets the desktop place windows, so there the choice is not
offered (the desktop opens Moonlight on the screen in use).
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

_CONNECTOR = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


@dataclass(frozen=True)
class LocalScreen:
    connector: str
    model: str
    x: int  # X11 coordinates (device pixels)
    y: int
    width: int
    height: int
    primary: bool = False

    def geometry(self) -> str:
        """``-qwindowgeometry`` value: a window inside this screen."""
        width, height = min(1280, self.width), min(720, self.height)
        return f"{width}x{height}+{self.x + (self.width - width) // 2}+{self.y + (self.height - height) // 2}"


def placement_supported(environ: dict | None = None) -> bool:
    """Can this session place Moonlight's window on a chosen screen?"""
    env = os.environ if environ is None else environ
    return env.get("XDG_SESSION_TYPE") != "wayland" and not env.get("WAYLAND_DISPLAY") and bool(env.get("DISPLAY"))


def primary_connector(run=subprocess.run) -> str:
    """The X11 primary output (``xrandr --listmonitors`` marks it with ``*``)."""
    try:
        output = run(["xrandr", "--listmonitors"], capture_output=True, text=True, timeout=3, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1].startswith("+*"):
            return parts[-1]
    return ""


def local_screens(display=None, primary: str | None = None) -> list[LocalScreen]:
    """Connected screens from GDK (main thread), in GDK's order."""
    if display is None:
        import gi

        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk  # type: ignore

        display = Gdk.Display.get_default()
    if display is None:
        return []
    primary = primary_connector() if primary is None else primary
    screens: list[LocalScreen] = []
    monitors = display.get_monitors()
    for index in range(monitors.get_n_items()):
        monitor = monitors.get_item(index)
        connector = (monitor.get_connector() if monitor is not None else "") or ""
        if not _CONNECTOR.fullmatch(connector):
            continue
        area = monitor.get_geometry()
        scale = monitor.get_scale_factor() or 1
        model = " ".join(part for part in (monitor.get_manufacturer() or "", monitor.get_model() or "") if part)
        screens.append(LocalScreen(connector, model, area.x * scale, area.y * scale, area.width * scale, area.height * scale, connector == primary))
    return screens


def find_screen(screens: list[LocalScreen], connector: str) -> LocalScreen | None:
    return next((screen for screen in screens if screen.connector == connector), None) if connector else None
