"""Why the other device may not see this computer's mouse pointer (read-only).

GTK-free. In Moonlight's normal (game) mouse mode the other device hides its
own pointer and shows only the one this computer draws into the video, so a
pointer missing from the picture means the *capture* lost it; the mouse
itself still works.

With ``capture`` left automatic, Sunshine tries NvFBC, wlroots, **KMS**, X11,
KWin and the portal, in that order. Its package gives ``/usr/bin/sunshine``
the capability that KMS needs, so on KDE Plasma and GNOME (Wayland and X11
alike) it normally ends up on KMS. KMS reads the hardware cursor plane of the
graphics driver separately and draws it into the frame; when that fails,
Sunshine says so in its log, and the stream has no pointer:

* "No KMS cursor plane found. Cursor may not be displayed while streaming!"
* "Atomic mode-setting must be enabled to capture the cursor!"
* "Unsupported cursor format" / "Unsupported non-linear cursor modifier"
* "Failed to map cursor FB" / "Failed to mmap cursor FB"

(Sunshine 2026.914, ``src/platform/linux/kmsgrab.cpp``.) KWin's own screen
cast and the desktop portal ask the compositor to draw the pointer into the
picture instead, and X11 capture draws it with XFixes. So the fix is to pick
the capture of this desktop, never to change the desktop's own cursor
settings (``KWIN_FORCE_SW_CURSOR``, ``MUTTER_DEBUG_DISABLE_HW_CURSORS``),
which would affect the whole session.

Separately, a pointer that does not *move* is a different problem: Sunshine
could not create its virtual mouse (``/dev/uinput`` not writable for this
user) — see :func:`big_remote_play.host.controllers.unusable_devices`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path

# What Sunshine writes when the cursor cannot be captured with KMS.
CURSOR_LOST_MARKERS = (
    "No KMS cursor plane found",
    "Atomic mode-setting must be enabled to capture the cursor",
    "Unsupported cursor format",
    "Unsupported non-linear cursor modifier",
    "Failed to map cursor FB",
    "Failed to mmap cursor FB",
)
# "Screencasting with KMS" etc.: the capture Sunshine really used.
_BACKENDS = {
    "Screencasting with KMS": "kms",
    "Screencasting with NvFBC": "nvfbc",
    "Screencasting with Wayland's protocol": "wlr",
    "Screencasting with X11": "x11",
    "Screencasting with XDG portal": "portal",
    "Screencasting with KWin ScreenCast": "kwin",
}
LOG_TAIL_BYTES = 256 * 1024
# The run started but no picture can ever be sent (every encoder failed,
# usually because no capture could open). Seen on a hybrid Intel/NVIDIA
# notebook when the desktop's screen-sharing questions were answered after
# Sunshine had already given up its capture permission (2026-11-04).
NO_PICTURE_MARKERS = (
    "Unable to find display or encoder during startup",
    "Video failed to find working encoder",
)


@dataclass(frozen=True)
class CursorReport:
    backend: str = ""  # the capture Sunshine used last ("" when not known)
    problem: str = ""  # the log line that says the pointer is lost, or ""
    recommended: str = ""  # a ``capture`` value that draws the pointer, or ""
    no_picture: bool = False  # this run of Sunshine found no capture or encoder

    @property
    def lost(self) -> bool:
        return bool(self.problem)


def desktop_session(environ: Mapping[str, str] | None = None) -> tuple[str, str]:
    """``(session_type, desktop)`` such as ``("wayland", "kde")``; empty when unknown."""
    env = os.environ if environ is None else environ
    session = (env.get("XDG_SESSION_TYPE") or "").strip().lower()
    if not session:
        session = "wayland" if env.get("WAYLAND_DISPLAY") else "x11" if env.get("DISPLAY") else ""
    names = (env.get("XDG_CURRENT_DESKTOP") or env.get("XDG_SESSION_DESKTOP") or "").lower()
    desktop = "kde" if "kde" in names or "plasma" in names else "gnome" if "gnome" in names else names.split(":", 1)[0]
    return session, desktop


def capture_with_pointer(session: str, desktop: str) -> str:
    """The capture of this desktop that draws the pointer into the picture."""
    if session == "wayland" and desktop == "kde":
        return "kwin"
    if session == "wayland":
        return "portal"  # GNOME and other portal desktops: approved once in its picker
    if session == "x11":
        return "x11"
    return ""


def read_cursor_report(log_text: str, *, session: str, desktop: str, configured: str = "") -> CursorReport:
    """What the latest Sunshine run says about the pointer, and what to use instead.

    Only the current run counts (Sunshine appends every run to the same log,
    each starting with "Sunshine version:"), and within it only the lines
    after the last "Screencasting with …" choice, so a problem of an earlier
    capture or run is not reported after it was fixed.
    """
    run = log_text.rfind("Sunshine version:")
    if run > 0:
        log_text = log_text[run:]
    no_picture = any(marker in log_text for marker in NO_PICTURE_MARKERS)
    if no_picture:
        return CursorReport(no_picture=True)
    backend, start = "", -1
    for marker, name in _BACKENDS.items():
        position = log_text.rfind(marker)
        if position > start:
            backend, start = name, position
    if start < 0:
        return CursorReport()
    tail = log_text[start:]
    problem = next((marker for marker in CURSOR_LOST_MARKERS if marker in tail), "")
    if not problem or backend != "kms":
        return CursorReport(backend, problem)
    recommended = capture_with_pointer(session, desktop)
    if recommended in ("", "kms") or recommended == configured:
        recommended = ""
    return CursorReport(backend, problem, recommended)


def cursor_report(log_path: Path, *, configured: str = "", environ: Mapping[str, str] | None = None) -> CursorReport:
    """Read the end of Sunshine's log (worker thread). Unreadable: nothing is claimed."""
    try:
        with log_path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - LOG_TAIL_BYTES))
            text = handle.read().decode("utf-8", "replace")
    except OSError:
        return CursorReport()
    session, desktop = desktop_session(environ)
    return read_cursor_report(text, session=session, desktop=desktop, configured=configured)


__all__ = ["CURSOR_LOST_MARKERS", "CursorReport", "capture_with_pointer", "cursor_report", "desktop_session", "read_cursor_report"]
