"""Read top-level windows from an X11 window manager (EWMH), without Xlib bindings.

Only ``libX11`` is used, through ``ctypes``: GTK 4 already depends on it, so the
application gains no package. Everything here is read-only; nothing is sent
to other clients' windows.

The compositing check matters for privacy: with a compositing manager every
top-level window keeps its own off-screen contents, so reading one window
never returns pixels of a window above it. Without one, the X server returns
whatever is on screen in the covered area.
"""

from __future__ import annotations

import ctypes
import ctypes.util
from dataclasses import dataclass
import logging
from typing import Any

_log = logging.getLogger("big-remoteplay")

_ANY_PROPERTY_TYPE = 0
_SUCCESS = 0
_MAX_WINDOWS = 512


@dataclass(frozen=True)
class X11Window:
    xid: int
    title: str
    wm_class: str
    wm_instance: str
    pid: int
    width: int
    height: int
    x: int
    y: int
    types: tuple[str, ...]
    states: tuple[str, ...]
    viewable: bool

    @property
    def minimized(self) -> bool:
        return "_NET_WM_STATE_HIDDEN" in self.states

    @property
    def fullscreen(self) -> bool:
        return "_NET_WM_STATE_FULLSCREEN" in self.states

    @property
    def normal(self) -> bool:
        # A window without a type is a normal window (EWMH).
        return not self.types or "_NET_WM_WINDOW_TYPE_NORMAL" in self.types

    @property
    def skip_taskbar(self) -> bool:
        return "_NET_WM_STATE_SKIP_TASKBAR" in self.states


class _XClientMessageEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("message_type", ctypes.c_ulong),
        ("format", ctypes.c_int),
        ("data", ctypes.c_long * 5),
    ]


class _XEvent(ctypes.Union):
    _fields_ = [("xclient", _XClientMessageEvent), ("pad", ctypes.c_long * 24)]


_CLIENT_MESSAGE = 33
_SUBSTRUCTURE_MASKS = (1 << 19) | (1 << 20)  # SubstructureNotify | SubstructureRedirect
_SOURCE_PAGER = 2


class _XWindowAttributes(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
        ("border_width", ctypes.c_int),
        ("depth", ctypes.c_int),
        ("visual", ctypes.c_void_p),
        ("root", ctypes.c_ulong),
        ("c_class", ctypes.c_int),
        ("bit_gravity", ctypes.c_int),
        ("win_gravity", ctypes.c_int),
        ("backing_store", ctypes.c_int),
        ("backing_planes", ctypes.c_ulong),
        ("backing_pixel", ctypes.c_ulong),
        ("save_under", ctypes.c_int),
        ("colormap", ctypes.c_ulong),
        ("map_installed", ctypes.c_int),
        ("map_state", ctypes.c_int),
        ("all_event_masks", ctypes.c_long),
        ("your_event_mask", ctypes.c_long),
        ("do_not_propagate_mask", ctypes.c_long),
        ("override_redirect", ctypes.c_int),
        ("screen", ctypes.c_void_p),
    ]


_ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
_IS_VIEWABLE = 2


def _load() -> ctypes.CDLL | None:
    name = ctypes.util.find_library("X11") or "libX11.so.6"
    try:
        lib = ctypes.CDLL(name)
    except OSError:
        return None
    lib.XOpenDisplay.restype = ctypes.c_void_p
    lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
    lib.XDefaultScreen.argtypes = [ctypes.c_void_p]
    lib.XRootWindow.restype = ctypes.c_ulong
    lib.XRootWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.XInternAtom.restype = ctypes.c_ulong
    lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    lib.XGetAtomName.restype = ctypes.c_void_p
    lib.XGetAtomName.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    lib.XGetWindowProperty.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_long,
        ctypes.c_long,
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    lib.XFree.argtypes = [ctypes.c_void_p]
    lib.XGetWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_XWindowAttributes)]
    lib.XTranslateCoordinates.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_ulong),
    ]
    lib.XGetSelectionOwner.restype = ctypes.c_ulong
    lib.XGetSelectionOwner.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    lib.XSetErrorHandler.restype = ctypes.c_void_p
    lib.XSetErrorHandler.argtypes = [ctypes.c_void_p]
    lib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long, ctypes.POINTER(_XEvent)]
    lib.XFlush.argtypes = [ctypes.c_void_p]
    return lib


# A window may close between listing and reading it; X11 then reports an
# error that must not end the process (the default handler exits).
_ignore_errors = _ERROR_HANDLER(lambda _display, _event: 0)


class X11Session:
    """One short-lived connection to an X server."""

    def __init__(self, display_name: str | None) -> None:
        # Any: ctypes resolves the libX11 functions at run time.
        self._lib: Any = _load()
        self._display = self._lib.XOpenDisplay(display_name.encode() if display_name else None) if self._lib else None
        if self._display:
            self._lib.XSetErrorHandler(ctypes.cast(_ignore_errors, ctypes.c_void_p))
            self._screen = self._lib.XDefaultScreen(self._display)
            self._root = self._lib.XRootWindow(self._display, self._screen)

    @property
    def connected(self) -> bool:
        return bool(self._display)

    def close(self) -> None:
        if self._display:
            self._lib.XCloseDisplay(self._display)
            self._display = None

    def __enter__(self) -> "X11Session":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _atom(self, name: str) -> int:
        return int(self._lib.XInternAtom(self._display, name.encode(), 0))

    def _atom_name(self, atom: int) -> str:
        pointer = self._lib.XGetAtomName(self._display, atom)
        if not pointer:
            return ""
        try:
            return ctypes.string_at(pointer).decode("utf-8", "replace")
        finally:
            self._lib.XFree(pointer)

    def _property(self, window: int, name: str, *, limit: int = 4096) -> tuple[int, int, bytes] | None:
        """``(format, count, raw bytes)`` of one property, or ``None``."""
        actual_type, actual_format = ctypes.c_ulong(), ctypes.c_int()
        count, remaining, data = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p()
        status = self._lib.XGetWindowProperty(
            self._display,
            window,
            self._atom(name),
            0,
            limit,
            0,
            _ANY_PROPERTY_TYPE,
            ctypes.byref(actual_type),
            ctypes.byref(actual_format),
            ctypes.byref(count),
            ctypes.byref(remaining),
            ctypes.byref(data),
        )
        if status != _SUCCESS or not data.value:
            return None
        try:
            item = {8: 1, 16: ctypes.sizeof(ctypes.c_short), 32: ctypes.sizeof(ctypes.c_long)}.get(actual_format.value, 0)
            return actual_format.value, count.value, ctypes.string_at(data.value, count.value * item)
        finally:
            self._lib.XFree(data)

    def _cardinals(self, window: int, name: str) -> list[int]:
        value = self._property(window, name)
        if value is None or value[0] != 32:
            return []
        return list((ctypes.c_ulong * value[1]).from_buffer_copy(value[2]))

    def _text(self, window: int, name: str) -> str:
        value = self._property(window, name)
        return value[2].split(b"\0", 1)[0].decode("utf-8", "replace") if value and value[0] == 8 else ""

    def client_list(self) -> list[int]:
        return self._cardinals(self._root, "_NET_CLIENT_LIST")[:_MAX_WINDOWS]

    def window(self, xid: int) -> X11Window | None:
        attributes = _XWindowAttributes()
        if not self._lib.XGetWindowAttributes(self._display, xid, ctypes.byref(attributes)):
            return None
        x, y, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
        self._lib.XTranslateCoordinates(self._display, xid, self._root, 0, 0, ctypes.byref(x), ctypes.byref(y), ctypes.byref(child))
        wm_class = self._property(xid, "WM_CLASS")
        parts = wm_class[2].split(b"\0") if wm_class and wm_class[0] == 8 else []
        instance = parts[0].decode("utf-8", "replace") if parts else ""
        klass = parts[1].decode("utf-8", "replace") if len(parts) > 1 else ""
        pids = self._cardinals(xid, "_NET_WM_PID")
        return X11Window(
            xid=xid,
            title=self._text(xid, "_NET_WM_NAME") or self._text(xid, "WM_NAME"),
            wm_class=klass,
            wm_instance=instance,
            pid=int(pids[0]) if pids else 0,
            width=attributes.width,
            height=attributes.height,
            x=x.value,
            y=y.value,
            types=tuple(self._atom_name(atom) for atom in self._cardinals(xid, "_NET_WM_WINDOW_TYPE")),
            states=tuple(self._atom_name(atom) for atom in self._cardinals(xid, "_NET_WM_STATE")),
            viewable=attributes.map_state == _IS_VIEWABLE,
        )

    def windows(self) -> list[X11Window]:
        found = []
        for xid in self.client_list():
            window = self.window(xid)
            if window is not None:
                found.append(window)
        self._lib.XSync(self._display, 0)
        return found

    def activate(self, xid: int) -> bool:
        """Ask the window manager to focus ``xid`` (EWMH ``_NET_ACTIVE_WINDOW``)."""
        if xid not in self.client_list():
            return False
        event = _XEvent()
        event.xclient.type = _CLIENT_MESSAGE
        event.xclient.send_event = 1
        event.xclient.window = xid
        event.xclient.message_type = self._atom("_NET_ACTIVE_WINDOW")
        event.xclient.format = 32
        event.xclient.data[0] = _SOURCE_PAGER
        sent = self._lib.XSendEvent(self._display, self._root, 0, _SUBSTRUCTURE_MASKS, ctypes.byref(event))
        self._lib.XFlush(self._display)
        return bool(sent)

    def compositing(self) -> bool:
        """Whether a compositing manager owns ``_NET_WM_CM_S<screen>`` (EWMH)."""
        return bool(self._lib.XGetSelectionOwner(self._display, self._atom(f"_NET_WM_CM_S{self._screen}")))


def list_windows(display_name: str | None) -> list[X11Window] | None:
    """Top-level windows, or ``None`` when the X server cannot be reached."""
    with X11Session(display_name) as session:
        if not session.connected:
            return None
        return session.windows()


def window_alive(display_name: str | None, xid: int) -> bool | None:
    """``None`` when the X server is gone, else whether ``xid`` is still managed."""
    with X11Session(display_name) as session:
        if not session.connected:
            return None
        return xid in session.client_list()


def compositing_active(display_name: str | None) -> bool | None:
    with X11Session(display_name) as session:
        return session.compositing() if session.connected else None


def activate_window(display_name: str | None, xid: int) -> bool:
    with X11Session(display_name) as session:
        return session.activate(xid) if session.connected else False
