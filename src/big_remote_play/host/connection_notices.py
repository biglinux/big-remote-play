"""Which devices just started playing on this computer, once per session.

Share's **Connected now** list is fed by ``SessionTracker`` (Sunshine's own
``CLIENT CONNECTED`` log marker, plus the address of the stream's RTSP
handshake). This module turns that list into one desktop notification per new
session. A session is identified by where it came from and when it was first
seen, so a device that stays connected is announced once, and a device that
disconnects and connects again is a new session and is announced again.

Nothing here is written to disk or to the log: names and addresses are
private and only travel to the desktop's notification.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import unicodedata

from big_remote_play.utils.connection_health import ConnectionInfo, Transport, valid_address

# A device name longer than this is shortened; it is someone else's text.
MAX_NAME_LENGTH = 64


@dataclass(frozen=True)
class ConnectionNotice:
    """One new session, with only what is known about it."""

    key: tuple[str, float]
    device_name: str  # "" when nothing names the device
    address: str  # a literal IP address, or ""
    transport: Transport  # UNKNOWN when the path was not determined


def clean_name(text: object) -> str:
    """A device name safe for a one-line notification title.

    Names come from the network (reverse DNS, a VPN's device list, a name the
    person saved). Control and formatting characters, including line breaks
    and bidirectional overrides, are dropped, spaces collapsed and the length
    capped. It is shown as plain text, never markup.
    """
    if not isinstance(text, str):
        return ""
    kept = "".join(" " if character.isspace() else character for character in text if unicodedata.category(character)[0] != "C" or character.isspace())
    name = " ".join(kept.split())
    if len(name) > MAX_NAME_LENGTH:
        name = name[: MAX_NAME_LENGTH - 1].rstrip() + "…"
    return name


def session_key(info: ConnectionInfo) -> tuple[str, float] | None:
    """Where the session came from and when it was first seen; ``None`` if unknown."""
    try:
        started = float(info.started_at) if info.started_at is not None else None
    except (TypeError, ValueError):
        return None
    if started is None:
        return None
    return (valid_address(str(info.address or "")) or "", started)


class ConnectionNotices:
    """New sessions since the last update; each one is reported exactly once."""

    def __init__(self) -> None:
        self._seen: set[tuple[str, float]] = set()

    def update(self, infos: Iterable[ConnectionInfo]) -> list[ConnectionNotice]:
        """The sessions in ``infos`` that were not there before.

        ``infos`` is the complete current list: sessions missing from it have
        ended and are forgotten, so the same device connecting again later is
        new. A session listed as ``preexisting`` (already playing when this
        computer started watching) is remembered without being announced.
        A stand-in name (``name_known`` false, such as "Connected device")
        does not identify a device and is not shown as its name.
        """
        current: dict[tuple[str, float], ConnectionInfo] = {}
        for info in infos:
            if not getattr(info, "connected", True):
                continue
            key = session_key(info)
            if key is not None:
                current[key] = info
        new = [key for key in current if key not in self._seen]
        self._seen = set(current)
        notices = []
        for key in new:
            info = current[key]
            if getattr(info, "preexisting", False):
                continue
            name = clean_name(info.device_name) if getattr(info, "name_known", True) else ""
            transport = info.transport if isinstance(info.transport, Transport) else Transport.UNKNOWN
            notices.append(ConnectionNotice(key, name, key[0], transport))
        return notices

    def clear(self) -> None:
        """Sharing stopped: every session ended."""
        self._seen.clear()


__all__ = ["ConnectionNotice", "ConnectionNotices", "MAX_NAME_LENGTH", "clean_name", "session_key"]
