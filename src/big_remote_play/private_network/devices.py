"""Friendly names and favourites for the person's computers.

Stored beside the session history with owner-only permissions. Only two
allowlisted fields exist per device, keyed by the address used to reach it:
a display name and a favourite flag. Nothing here is secret or sent anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import threading

from big_remote_play import paths
from big_remote_play.utils.secure_io import secure_write_text

NAME_LIMIT = 64
MAX_DEVICES = 500
_LOCK = threading.RLock()


@dataclass(frozen=True)
class DevicePreference:
    name: str = ""
    favorite: bool = False


def device_key(address: str) -> str:
    """The stable identity of a device: its address, case-insensitive."""
    return (address or "").strip().lower()


def _clean_name(value: object) -> str:
    text = " ".join(str(value or "").split())
    return "".join(character for character in text if character.isprintable())[:NAME_LIMIT]


def _valid_key(key: object) -> bool:
    return isinstance(key, str) and 0 < len(key) <= 253 and not key.startswith("-") and all(character.isprintable() and not character.isspace() for character in key)


class DevicePreferences:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path or (paths.CONFIG_DIR / "history" / "devices.json")

    def _read(self) -> dict[str, DevicePreference]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            return {}
        devices = payload.get("devices") if isinstance(payload, dict) else None
        found: dict[str, DevicePreference] = {}
        for key, value in (devices or {}).items() if isinstance(devices, dict) else ():
            if _valid_key(key) and isinstance(value, dict):
                found[key] = DevicePreference(_clean_name(value.get("name")), value.get("favorite") is True)
        return found

    def _write(self, devices: dict[str, DevicePreference]) -> None:
        kept = {key: pref for key, pref in devices.items() if pref.name or pref.favorite}
        payload = {"version": 1, "devices": {key: {"name": pref.name, "favorite": pref.favorite} for key, pref in list(kept.items())[:MAX_DEVICES]}}
        secure_write_text(str(self.path), json.dumps(payload, indent=1, ensure_ascii=False))

    def all(self) -> dict[str, DevicePreference]:
        with _LOCK:
            return self._read()

    def get(self, address: str) -> DevicePreference:
        return self.all().get(device_key(address), DevicePreference())

    def display_name(self, address: str, fallback: str) -> str:
        return self.get(address).name or fallback

    def rename(self, address: str, name: str) -> None:
        self._update(address, name=_clean_name(name))

    def set_favorite(self, address: str, favorite: bool) -> None:
        self._update(address, favorite=bool(favorite))

    def forget(self, address: str) -> None:
        key = device_key(address)
        with _LOCK:
            devices = self._read()
            if devices.pop(key, None) is not None:
                self._write(devices)

    def _update(self, address: str, **changes) -> None:
        key = device_key(address)
        if not _valid_key(key):
            raise ValueError("invalid device address")
        with _LOCK:
            devices = self._read()
            current = devices.get(key, DevicePreference())
            devices[key] = DevicePreference(changes.get("name", current.name), changes.get("favorite", current.favorite))
            self._write(devices)
