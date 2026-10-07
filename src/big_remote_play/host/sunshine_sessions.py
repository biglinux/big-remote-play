"""Which devices are streaming from this computer right now.

"Paired" and "connected" are different: a paired device may connect at any
time; a connected one is receiving the stream now. Sunshine has no API for
live sessions (``/api/clients/list`` lists pairings only), so two pieces of
real evidence are combined:

1. **Sunshine's own log** says how many sessions are active. Each run starts
   with ``Sunshine version:``; ``CLIENT CONNECTED`` / ``CLIENT DISCONNECTED``
   mark a stream starting and ending; ``Terminate handler called`` ends the
   run. These are Sunshine's untranslated log markers, read incrementally.
2. **The stream's RTSP handshake** says who. A client that starts a stream
   talks to Sunshine's RTSP port (base port + 21, 48010 by default) just
   before the session starts; Sunshine closes those connections, which keeps
   them visible in ``ss`` for about a minute. Moonlight's routine
   ``/serverinfo`` polling uses other ports and never counts.

A device appears as connected only while the log says a session is active.
When a session starts and no handshake was seen, it is listed without an
address instead of guessing one.
"""

from __future__ import annotations

from collections import Counter

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import time

from big_remote_play.integration_contracts import SUNSHINE_DEFAULT_BASE_PORT
from big_remote_play.utils.connection_health import valid_address

RTSP_OFFSET = 21
START_MARKER = "Sunshine version:"
CONNECTED_MARKER = "CLIENT CONNECTED"
DISCONNECTED_MARKER = "CLIENT DISCONNECTED"
TERMINATED_MARKER = "Terminate handler called"
# A handshake belongs to a session that starts within this many seconds.
HANDSHAKE_WINDOW_SECONDS = 90.0
_MAX_READ = 1 << 20


# What Sunshine logs about the encoder it opened for a session (Info level).
_VIDEO_PATTERNS = {
    "encoder": re.compile(r"Creating encoder \[([a-z0-9_]+)\]"),
    "color": re.compile(r"Color coding: (.+)$"),
    "depth": re.compile(r"Color depth: (\d+)-bit"),
    "range": re.compile(r"Color range: (JPEG|MPEG)"),
    "bitrate": re.compile(r"Streaming bitrate is (\d+)"),
    "capture": re.compile(r"Screencasting with (\w+)|\[(portalgrab|wlgrab|x11grab|kwingrab)\]"),
}


@dataclass(frozen=True)
class VideoInfo:
    """The encoder Sunshine opened for the latest session, from its own log."""

    encoder: str = ""
    color: str = ""
    depth: int = 0
    full_range: bool | None = None
    bitrate_kbps: int = 0
    capture: str = ""

    @property
    def codec(self) -> str:
        name = self.encoder.split("_", 1)[0]
        return {"h264": "H.264", "hevc": "HEVC", "av1": "AV1"}.get(name, name.upper())

    @property
    def backend(self) -> str:
        return self.encoder.split("_", 1)[1].upper() if "_" in self.encoder else ""

    @property
    def hdr(self) -> bool:
        return "HDR" in self.color

    def summary(self) -> str:
        """Stable technical words (not translated), for details and logs."""
        if not self.encoder:
            return ""
        parts = [f"{self.codec} ({self.backend})" if self.backend else self.codec]
        if self.color:
            parts.append(self.color)
        if self.depth:
            parts.append(f"{self.depth}-bit")
        if self.full_range is not None:
            parts.append("full range" if self.full_range else "limited range")
        if self.bitrate_kbps:
            parts.append(f"{self.bitrate_kbps / 1000:.1f} Mbps")
        if self.capture:
            parts.append(f"capture: {self.capture}")
        return " · ".join(parts)


def read_video(lines: Iterable[str], current: VideoInfo | None = None) -> VideoInfo:
    """Update ``current`` with the encoder lines found in ``lines``."""
    values = dict(current.__dict__) if current is not None else {}
    for line in lines:
        if START_MARKER in line:
            values = {}
            continue
        for key, pattern in _VIDEO_PATTERNS.items():
            match = pattern.search(line)
            if not match:
                continue
            if key == "depth":
                values["depth"] = int(match.group(1))
            elif key == "range":
                values["full_range"] = match.group(1) == "JPEG"
            elif key == "bitrate":
                values["bitrate_kbps"] = int(match.group(1)) // 1000
            elif key == "capture":
                values["capture"] = (match.group(1) or match.group(2) or "").replace("grab", "").upper()
            else:
                values[key] = match.group(1).strip()
    return VideoInfo(**values)


def count_sessions(lines: Iterable[str], start: int = 0) -> int:
    """Active sessions after ``lines``, starting from ``start`` active ones."""
    active = start
    for line in lines:
        if START_MARKER in line or TERMINATED_MARKER in line:
            active = 0
        elif DISCONNECTED_MARKER in line:
            active = max(0, active - 1)
        elif CONNECTED_MARKER in line:
            active += 1
    return active


def _split_endpoint(token: str) -> tuple[str, str]:
    token = token.strip()
    if token.startswith("["):
        host, _, port = token[1:].partition("]:")
        return host, port
    host, _, port = token.rpartition(":")
    return host, port


def _normalize(host: str) -> str:
    host = host.strip().split("%", 1)[0]
    if host.lower().startswith("::ffff:") and "." in host:
        host = host[len("::ffff:") :]
    return host


def handshake_peers(ss_output: str, rtsp_port: int) -> list[str]:
    """Peers of TCP connections on the local RTSP port, from ``ss -tan``."""
    peers: list[str] = []
    for line in ss_output.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] == "State":
            continue
        # ss -tan: State Recv-Q Send-Q Local Peer
        _local_host, local_port = _split_endpoint(parts[3])
        if local_port != str(rtsp_port) or parts[0] == "LISTEN":
            continue
        peer = valid_address(_normalize(_split_endpoint(parts[4])[0]))
        if peer and peer not in peers:
            peers.append(peer)
    return peers


def established_peers(ss_output: str, port: int) -> Counter[str]:
    """Open TCP connections to a local ``port`` per peer, from ``ss -tan``.

    A device that is pairing keeps its request to Sunshine's HTTP port open
    until the PIN is entered (measured with Sunshine 2026.914); one that gave
    up has none.
    """
    peers: Counter[str] = Counter()
    for line in ss_output.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] != "ESTAB":
            continue
        if _split_endpoint(parts[3])[1] != str(port):
            continue
        peer = valid_address(_normalize(_split_endpoint(parts[4])[0]))
        if peer:
            peers[peer] += 1
    return peers


@dataclass(frozen=True)
class LiveSession:
    """One active stream. ``address`` is empty when its origin was not seen."""

    address: str
    started_at: float
    video: VideoInfo | None = None
    # Already playing when this tracker first read the log (for example,
    # Big Remote Play was opened during a stream): not a new connection.
    preexisting: bool = False


class LogReader:
    """Follow Sunshine's log without re-reading it: only new bytes each time."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._inode: int | None = None
        self._offset = 0
        self._partial = ""
        self.active = 0
        self.video = VideoInfo()

    def poll(self) -> int:
        try:
            info = os.stat(self.path)
        except OSError:
            self._inode, self._offset, self._partial, self.active = None, 0, "", 0
            self.video = VideoInfo()
            return 0
        if info.st_ino != self._inode or info.st_size < self._offset:
            # Sunshine rotated the log (a new run) or it was replaced.
            self._inode, self._offset, self._partial, self.active = info.st_ino, 0, "", 0
            self.video = VideoInfo()
        if info.st_size == self._offset:
            return self.active
        if info.st_size - self._offset > _MAX_READ and self._offset == 0:
            # Only the current run matters: find its start near the end.
            self._offset = info.st_size - _MAX_READ
        try:
            with open(self.path, "rb") as handle:
                handle.seek(self._offset)
                chunk = handle.read(_MAX_READ)
        except OSError:
            return self.active
        self._offset += len(chunk)
        text = self._partial + chunk.decode("utf-8", errors="replace")
        lines = text.split("\n")
        self._partial = lines.pop()
        self.active = count_sessions(lines, self.active)
        self.video = read_video(lines, self.video)
        return self.active


def _ss(argv: list[str]) -> str:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


class SessionTracker:
    """Active streams with the address each one came from, when known."""

    def __init__(
        self,
        log_path: Path,
        *,
        base_port: int = SUNSHINE_DEFAULT_BASE_PORT,
        ss: Callable[[list[str]], str] = _ss,
        clock: Callable[[], float] = time.time,
        running: Callable[[], bool] = lambda: True,
    ) -> None:
        self.log = LogReader(log_path)
        self.rtsp_port = base_port + RTSP_OFFSET
        self._ss = ss
        self._clock = clock
        self._running = running
        self._seen: dict[str, float] = {}  # handshake peer → last seen
        self.sessions: list[LiveSession] = []
        self._first_poll = True

    def poll(self) -> list[LiveSession]:
        now = self._clock()
        first, self._first_poll = self._first_poll, False
        active = self.log.poll() if self._running() else 0
        if active == 0:
            self.sessions = []
            # Handshakes already seen stay usable for a session about to start.
            self._remember_handshakes(now)
            return []
        self._remember_handshakes(now)
        while len(self.sessions) > active:
            # Sunshine does not say which client left; drop one that has no
            # known origin first, then the oldest.
            unknown = next((index for index, session in enumerate(self.sessions) if not session.address), None)
            self.sessions.pop(unknown if unknown is not None else 0)
        while len(self.sessions) < active:
            self.sessions.append(LiveSession(self._claim_handshake(now), now, preexisting=first))
        # Sunshine logs the encoder once per session; the newest one is shown.
        video = self.log.video if self.log.video.encoder else None
        return [LiveSession(session.address, session.started_at, video, session.preexisting) for session in self.sessions]

    def _remember_handshakes(self, now: float) -> None:
        for peer in handshake_peers(self._ss(["ss", "-tan"]), self.rtsp_port):
            self._seen[peer] = now
        for peer, seen in list(self._seen.items()):
            if now - seen > HANDSHAKE_WINDOW_SECONDS:
                del self._seen[peer]

    def _claim_handshake(self, now: float) -> str:
        taken = {session.address for session in self.sessions}
        candidates = sorted(((seen, peer) for peer, seen in self._seen.items() if peer not in taken), reverse=True)
        if not candidates:
            return ""
        _seen, peer = candidates[0]
        del self._seen[peer]
        return peer


__all__ = ["LiveSession", "LogReader", "SessionTracker", "VideoInfo", "count_sessions", "handshake_peers", "read_video"]
