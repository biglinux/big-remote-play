"""Sound on the connecting computer: what Moonlight logs and what it plays.

The game PC can only tell that Sunshine records and sends its sound. Whether
it arrives and is played is known here, from two facts:

- Moonlight's own messages (moonlight-common-c and Moonlight Qt, English and
  never translated), read from its output;
- Moonlight's playback stream on this computer's sound server.

The session manager restores a saved per-application mute to every new
stream, exactly as on the game PC: one mute of Moonlight in a mixer silences
every later connection. A muted Moonlight stream is unmuted; its volume is
the person's listening level and is left alone.
"""

from __future__ import annotations

import logging
import threading
import time

from big_remote_play.utils.audio import VOLUME_NORM, AudioGraph, AudioManager, AudioStream

_log = logging.getLogger("big-remoteplay")

RECEIVED = "received"  # "Received first audio packet after %d ms"
NO_TRAFFIC = "no-traffic"  # "No audio traffic was ever received from the host!"
DEVICE_FAILED = "device-failed"  # "Failed to open audio device…"
LOSS = "loss"  # packets arriving faster than played, or lost on the network
RENDERER_RESET = "renderer-reset"  # "Reinitializing audio renderer after failure"
SURROUND_UNSUPPORTED = "surround-unsupported"

_MARKERS = (
    ("Received first audio packet", RECEIVED),
    ("No audio traffic was ever received", NO_TRAFFIC),
    ("Failed to open audio device", DEVICE_FAILED),
    ("Audio packet queue overflow", LOSS),
    ("Network dropped audio data", LOSS),
    ("Reinitializing audio renderer after failure", RENDERER_RESET),
    ("surround sound setting is not supported by the current audio device", SURROUND_UNSUPPORTED),
)

MOONLIGHT_APP_IDS = frozenset({"com.moonlight_stream.Moonlight"})


def classify_line(line: str) -> str | None:
    for marker, kind in _MARKERS:
        if marker in line:
            return kind
    return None


class MoonlightAudioReport:
    """Counts Moonlight's sound messages for one connection; safe across threads.

    Loss messages come in bursts of dozens: the first is logged, then one
    summary per ``LOSS_LOG_INTERVAL`` while they continue.
    """

    LOSS_LOG_INTERVAL = 30.0
    # More loss messages than this within one interval is told to the person once.
    LOSS_ALERT = 50

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self.counts: dict[str, int] = {}
        self._loss_window_start = 0.0
        self._loss_in_window = 0
        self._loss_alert = False
        self._loss_alert_taken = False

    def note(self, line: str) -> tuple[str | None, bool]:
        """``(kind, log the line)`` for one line of Moonlight output."""
        kind = classify_line(line)
        if kind is None:
            return None, True
        with self._lock:
            self.counts[kind] = self.counts.get(kind, 0) + 1
            if kind != LOSS:
                return kind, True
            now = self._clock()
            if self.counts[LOSS] == 1:
                self._loss_window_start, self._loss_in_window = now, 1
                return kind, True
            self._loss_in_window += 1
            if self._loss_in_window > self.LOSS_ALERT:
                self._loss_alert = True
            if now - self._loss_window_start < self.LOSS_LOG_INTERVAL:
                return kind, False
            _log.warning("[AUDIO] Moonlight: %d audio loss messages in the last %.0f s (packet queue overflow / network dropped audio data)", self._loss_in_window, now - self._loss_window_start)
            self._loss_window_start, self._loss_in_window = now, 0
            return kind, False

    def seen(self, kind: str) -> bool:
        with self._lock:
            return self.counts.get(kind, 0) > 0

    def take_loss_alert(self) -> bool:
        """True once per connection, when sound was being lost in large bursts."""
        with self._lock:
            if self._loss_alert and not self._loss_alert_taken:
                self._loss_alert_taken = True
                return True
            return False


def moonlight_playback(graph: AudioGraph | None) -> list[AudioStream]:
    """Moonlight's playback streams on this computer's sound server."""
    if graph is None:
        return []
    found = []
    for stream in graph.playback:
        app_id = stream.properties.get("application.id", "") or stream.properties.get("pipewire.access.portal.app_id", "")
        if stream.binary in ("moonlight", "moonlight-qt") or stream.app.lower() == "moonlight" or app_id in MOONLIGHT_APP_IDS:
            found.append(stream)
    return found


# The result of the check after a stream starts.
SOUND_PLAYING = "playing"
SOUND_UNMUTED = "unmuted"  # it was muted on this computer and was turned back on
SOUND_DEVICE_FAILED = "device-failed"
SOUND_NO_TRAFFIC = "no-traffic"
SOUND_NOT_PLAYING = "not-playing"  # Moonlight has no playback stream here
SOUND_UNKNOWN = "unknown"  # no sound server to ask


def check_playback(manager: AudioManager, report: MoonlightAudioReport, *, timeout: float = 10.0, alive=lambda: True, sleep=time.sleep, clock=time.monotonic) -> str:
    """Wait for Moonlight's sound to start here, then say whether it plays.

    Waits for an observable fact — Moonlight's playback stream, or a message
    saying the sound failed — never for a fixed time.
    """
    deadline = clock() + timeout
    streams: list[AudioStream] = []
    graph: AudioGraph | None = None
    while alive():
        if report.seen(DEVICE_FAILED):
            _log.warning("[AUDIO] Moonlight could not open this computer's sound device")
            return SOUND_DEVICE_FAILED
        if report.seen(NO_TRAFFIC):
            _log.warning("[AUDIO] Moonlight received no sound from the game PC")
            return SOUND_NO_TRAFFIC
        graph = manager.snapshot()
        streams = moonlight_playback(graph)
        if streams or graph is None or clock() >= deadline:
            break
        sleep(0.5)
    if graph is None:
        return SOUND_UNKNOWN
    if not streams:
        _log.warning("[AUDIO] Moonlight has no playback stream on this computer (first packet received: %s)", report.seen(RECEIVED))
        return SOUND_NOT_PLAYING
    result = SOUND_PLAYING
    for stream in streams:
        if stream.muted and manager.unmute_playback(stream.index):
            _log.warning("[AUDIO] Moonlight's playback #%s was muted on this computer; unmuted.", stream.index)
            result = SOUND_UNMUTED
        level = f"{round(stream.volume * 100 / VOLUME_NORM)} %" if stream.volume is not None else "level unknown"
        _log.info("[AUDIO] Moonlight plays #%s into %s, %s%s", stream.index, stream.device, level, ", was muted" if stream.muted else "")
    return result
