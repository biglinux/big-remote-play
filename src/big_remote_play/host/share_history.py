"""Sessions other devices played on this computer, kept only here.

GTK-free. Share's live list (**Connected now**) comes from Sunshine's own log
markers (``CLIENT CONNECTED`` / ``CLIENT DISCONNECTED``, see
``host/sunshine_sessions.py``). This module keeps what that list showed once
the session ends: when it started, when it ended and the device's name, so
**Support → Connection history** can say how this computer was used in the
last days. Nothing else is stored: no address, no key, no code.

The file is ``$XDG_CONFIG_HOME/big-remote-play/history/shared.json`` (0600).
A malformed or future entry is skipped, never repaired by rewriting the
user's file from scratch; an unreadable file records nothing new.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import threading
import time
from typing import Any
import uuid

from big_remote_play import paths
from big_remote_play.private_network.redaction import redact
from big_remote_play.utils.secure_io import secure_write_text

RETENTION_DAYS = 90
MAX_SESSIONS = 1000
# A session shorter than this is a connection test, not play.
MIN_SECONDS = 5.0
_FUTURE_TOLERANCE = 300.0
_LOCK = threading.RLock()


@dataclass(frozen=True)
class SharedSession:
    id: str
    started_at: float
    device: str = ""
    ended_at: float | None = None

    @property
    def duration(self) -> float | None:
        return None if self.ended_at is None else max(0.0, self.ended_at - self.started_at)


@dataclass(frozen=True)
class DayUse:
    day: date
    sessions: int
    seconds: float


@dataclass(frozen=True)
class HistorySummary:
    days: tuple[DayUse, ...]
    sessions: int  # in the period
    total_seconds: float
    average_seconds: float | None
    last_connection: float | None  # ever, not only in the period
    has_history: bool  # anything kept at all


def _clean_name(value: object) -> str:
    text = "".join(character for character in str(value or "") if character.isprintable())
    return redact(text.strip())[:128]


def _session_from_json(item: object, now: float) -> SharedSession | None:
    if not isinstance(item, dict):
        return None
    try:
        started = float(item["started_at"])
        ended = float(item["ended_at"]) if item.get("ended_at") is not None else None
    except (KeyError, TypeError, ValueError):
        return None
    if started <= 0 or started > now + _FUTURE_TOLERANCE:
        return None
    if ended is not None and (ended < started or ended > now + _FUTURE_TOLERANCE):
        ended = None
    session_id = str(item.get("id") or "")[:64] or uuid.uuid4().hex
    return SharedSession(session_id, started, _clean_name(item.get("device")), ended)


class ShareHistory:
    def __init__(self, path: Path | None = None, *, clock: Callable[[], float] = time.time) -> None:
        self._path = path
        self._clock = clock

    @property
    def path(self) -> Path:
        return self._path or (paths.CONFIG_DIR / "history" / "shared.json")

    def _read(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "sessions": []}
        if not isinstance(payload, dict):
            raise ValueError("history file is not an object")
        if not isinstance(payload.get("sessions"), list):
            payload["sessions"] = []
        payload.setdefault("version", 1)
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        secure_write_text(str(self.path), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def _sessions(self, payload: dict[str, Any]) -> list[SharedSession]:
        now = self._clock()
        cutoff = now - RETENTION_DAYS * 86_400
        sessions = [session for session in (_session_from_json(item, now) for item in payload["sessions"]) if session is not None and session.started_at >= cutoff]
        sessions.sort(key=lambda session: session.started_at, reverse=True)
        return sessions[:MAX_SESSIONS]

    def sessions(self) -> list[SharedSession]:
        with _LOCK:
            try:
                return self._sessions(self._read())
            except (OSError, ValueError):
                return []

    def start(self, device: str, *, started_at: float | None = None) -> str:
        """A device started playing; returns the id to finish it with."""
        session = SharedSession(uuid.uuid4().hex, float(started_at or self._clock()), _clean_name(device))
        with _LOCK:
            try:
                payload = self._read()
            except (OSError, ValueError):
                return ""  # an unreadable file is left for the person to inspect
            payload["sessions"] = [asdict(session), *(asdict(item) for item in self._sessions(payload))][:MAX_SESSIONS]
            self._write(payload)
        return session.id

    def finish(self, session_id: str, *, ended_at: float | None = None) -> None:
        """The device stopped playing; a session of a few seconds is not kept."""
        if not session_id:
            return
        end = float(ended_at or self._clock())
        with _LOCK:
            try:
                payload = self._read()
            except (OSError, ValueError):
                return
            kept = []
            for session in self._sessions(payload):
                if session.id == session_id and session.ended_at is None:
                    if end - session.started_at < MIN_SECONDS:
                        continue
                    session = SharedSession(session.id, session.started_at, session.device, end)
                kept.append(asdict(session))
            payload["sessions"] = kept
            self._write(payload)

    def clear(self) -> None:
        with _LOCK:
            self._write({"version": 1, "sessions": []})

    def summary(self, *, days: int = 7, now: float | None = None) -> HistorySummary:
        """Sessions per local day for the last ``days`` days, and totals."""
        now = self._clock() if now is None else now
        sessions = self.sessions()
        today = datetime.fromtimestamp(now).date()
        first = today - timedelta(days=days - 1)
        counts = {first + timedelta(days=offset): [0, 0.0] for offset in range(days)}
        in_period: list[SharedSession] = []
        for session in sessions:
            day = datetime.fromtimestamp(session.started_at).date()
            if day in counts:
                counts[day][0] += 1
                counts[day][1] += session.duration or 0.0
                in_period.append(session)
        durations = [session.duration for session in in_period if session.duration is not None]
        total = sum(durations)
        last = max((session.ended_at or session.started_at for session in sessions), default=None)
        return HistorySummary(
            tuple(DayUse(day, int(count), float(seconds)) for day, (count, seconds) in sorted(counts.items())),
            len(in_period),
            total,
            (total / len(durations)) if durations else None,
            last,
            bool(sessions),
        )


__all__ = ["DayUse", "HistorySummary", "ShareHistory", "SharedSession"]
