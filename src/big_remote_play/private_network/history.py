"""Local history of streaming sessions that really started.

The file lives in the user's configuration directory with owner-only
permissions and is never sent anywhere. It holds an allowlisted set of
non-secret fields; credentials, pairing codes and search codes have no field
here, so they cannot be stored by accident.

A session is recorded when Moonlight is streaming, not when a connection is
attempted, and is discarded again if it ends almost at once with an error
(the stream never really worked).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import threading
import time
from typing import Any
import uuid

from big_remote_play import paths
from big_remote_play.utils.secure_io import secure_write_text

from .models import HostCandidate
from .redaction import redact

RETENTION_CHOICES = (30, 90, None)  # days; None keeps everything
DEFAULT_RETENTION_DAYS = 90
MAX_SESSIONS = 500
# A stream that ends with an error before this many seconds never worked.
FAILED_SESSION_SECONDS = 10.0

_LOCK = threading.RLock()
_TEXT_LIMIT = 128
_PROVIDERS = {"tailscale", "headscale", "zerotier", "lan", "manual"}
_PATHS = {"direct", "relay", "peer_relay", "lan", "unknown"}


@dataclass(frozen=True)
class SessionRecord:
    id: str
    started_at: float
    host_name: str
    host_address: str
    port: int
    provider: str = "lan"
    ended_at: float | None = None
    client_name: str = ""
    app: str = ""
    resolution: str = ""
    fps: int = 0
    bitrate_kbps: int = 0
    codec: str = ""
    connection_path: str = "unknown"

    @property
    def duration_seconds(self) -> float | None:
        if self.ended_at is None:
            return None
        return max(0.0, self.ended_at - self.started_at)


def _clean_text(value: object, limit: int = _TEXT_LIMIT) -> str:
    text = "".join(character for character in str(value or "") if character.isprintable())
    return redact(text.strip())[:limit]


def _clean_address(value: object) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 253 or text.startswith("-") or any(character.isspace() or not character.isprintable() or character in "/\\@?#" for character in text):
        raise ValueError("invalid host address")
    return text


def _clean_int(value: object, low: int, high: int, default: int = 0) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return number if low <= number <= high else default


def _record_from_json(item: object) -> SessionRecord | None:
    if not isinstance(item, dict):
        return None
    try:
        return SessionRecord(
            id=_clean_text(item.get("id"), 64) or uuid.uuid4().hex,
            started_at=float(item["started_at"]),
            ended_at=float(item["ended_at"]) if item.get("ended_at") is not None else None,
            host_name=_clean_text(item.get("host_name")),
            host_address=_clean_address(item.get("host_address")),
            port=_clean_int(item.get("port"), 1, 65_535, 47_989),
            provider=str(item.get("provider")) if item.get("provider") in _PROVIDERS else "lan",
            client_name=_clean_text(item.get("client_name")),
            app=_clean_text(item.get("app")),
            resolution=_clean_text(item.get("resolution"), 16),
            fps=_clean_int(item.get("fps"), 0, 1000),
            bitrate_kbps=_clean_int(item.get("bitrate_kbps"), 0, 10_000_000),
            codec=_clean_text(item.get("codec"), 16),
            connection_path=str(item.get("connection_path")) if item.get("connection_path") in _PATHS else "unknown",
        )
    except (KeyError, TypeError, ValueError):
        return None


class SessionHistory:
    def __init__(self, path: Path | None = None, *, clock: Callable[[], float] = time.time) -> None:
        self._path = path
        self._clock = clock

    @property
    def path(self) -> Path:
        return self._path or (paths.CONFIG_DIR / "history" / "sessions.json")

    # ── storage ────────────────────────────────────────────────────────────
    def _read(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "retention_days": DEFAULT_RETENTION_DAYS, "sessions": []}
        except (OSError, ValueError):
            # Unreadable history is not silently replaced: keep it for the
            # user to inspect and simply record nothing new into it.
            raise
        if not isinstance(payload, dict):
            raise ValueError("history file is not an object")
        payload.setdefault("version", 1)
        payload.setdefault("retention_days", DEFAULT_RETENTION_DAYS)
        if not isinstance(payload.get("sessions"), list):
            payload["sessions"] = []
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        secure_write_text(str(self.path), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def _records(self, payload: dict[str, Any]) -> list[SessionRecord]:
        records = [record for record in map(_record_from_json, payload["sessions"]) if record is not None]
        days = payload.get("retention_days")
        if isinstance(days, int) and days > 0:
            cutoff = self._clock() - days * 86_400
            records = [record for record in records if record.started_at >= cutoff]
        records.sort(key=lambda record: record.started_at, reverse=True)
        return records[:MAX_SESSIONS]

    # ── public API ─────────────────────────────────────────────────────────
    @property
    def retention_days(self) -> int | None:
        try:
            value = self._read().get("retention_days")
        except (OSError, ValueError):
            return DEFAULT_RETENTION_DAYS
        return value if isinstance(value, int) and value > 0 else None

    def set_retention(self, days: int | None) -> None:
        if days not in RETENTION_CHOICES:
            raise ValueError("unsupported retention")
        with _LOCK:
            payload = self._read()
            payload["retention_days"] = days
            payload["sessions"] = [asdict(record) for record in self._records(payload)]
            self._write(payload)

    def sessions(self) -> list[SessionRecord]:
        with _LOCK:
            try:
                return self._records(self._read())
            except (OSError, ValueError):
                return []

    def start(
        self,
        *,
        host_name: str,
        host_address: str,
        port: int,
        provider: str = "lan",
        client_name: str = "",
        app: str = "",
        resolution: str = "",
        fps: int = 0,
        bitrate_kbps: int = 0,
        codec: str = "",
        connection_path: str = "unknown",
    ) -> str:
        record = _record_from_json(
            {
                "id": uuid.uuid4().hex,
                "started_at": self._clock(),
                "host_name": host_name,
                "host_address": host_address,
                "port": port,
                "provider": provider,
                "client_name": client_name,
                "app": app,
                "resolution": resolution,
                "fps": fps,
                "bitrate_kbps": bitrate_kbps,
                "codec": codec,
                "connection_path": connection_path,
            }
        )
        if record is None:
            raise ValueError("invalid session")
        with _LOCK:
            payload = self._read()
            records = [record, *self._records(payload)][:MAX_SESSIONS]
            payload["sessions"] = [asdict(item) for item in records]
            self._write(payload)
        return record.id

    def finish(self, session_id: str, *, failed: bool = False) -> None:
        """Close a session; drop it if it failed before it really started."""
        with _LOCK:
            try:
                payload = self._read()
            except (OSError, ValueError):
                return
            now = self._clock()
            kept: list[dict[str, Any]] = []
            for record in self._records(payload):
                if record.id == session_id and record.ended_at is None:
                    if failed and now - record.started_at < FAILED_SESSION_SECONDS:
                        continue
                    record = SessionRecord(**{**asdict(record), "ended_at": now})
                kept.append(asdict(record))
            payload["sessions"] = kept
            self._write(payload)

    def clear(self) -> None:
        with _LOCK:
            try:
                payload = self._read()
            except (OSError, ValueError):
                payload: dict[str, Any] = {"version": 1, "retention_days": DEFAULT_RETENTION_DAYS, "sessions": []}
            payload["sessions"] = []
            self._write(payload)

    def forget_host(self, address: str, port: int | None = None) -> int:
        """Remove every session with this computer; returns how many."""
        target = (address or "").strip().lower()
        with _LOCK:
            try:
                payload = self._read()
            except (OSError, ValueError):
                return 0
            sessions = payload.get("sessions") or []
            kept = [s for s in sessions if not (isinstance(s, dict) and str(s.get("host_address", "")).lower() == target and (port is None or s.get("port") == port))]
            removed = len(sessions) - len(kept)
            if removed:
                payload["sessions"] = kept
                self._write(payload)
            return removed

    def recent_hosts(self, limit: int = 5) -> list[HostCandidate]:
        seen: set[tuple[str, int]] = set()
        hosts: list[HostCandidate] = []
        for record in self.sessions():
            key = (record.host_address.lower(), record.port)
            if key in seen:
                continue
            seen.add(key)
            hosts.append(
                HostCandidate(
                    name=record.host_name or record.host_address,
                    address=record.host_address,
                    port=record.port,
                    provider=record.provider,
                    source="history",
                    last_connected=record.ended_at or record.started_at,
                )
            )
            if len(hosts) >= limit:
                break
        return hosts
