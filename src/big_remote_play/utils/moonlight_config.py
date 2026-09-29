"""Preserve Moonlight's QSettings INI while editing supported streaming keys.

Keys and enum values follow app/settings/streamingpreferences.{h,cpp} upstream.
Case, percent escapes and unknown sections (including paired hosts) are opaque.
"""

import configparser
import io
import logging
import os
import threading
from pathlib import Path
from typing import Any

from .secure_io import secure_write_text

_log = logging.getLogger("big-remoteplay")

# Migration from the names previously written by Big Remote Play, not by Qt.
# Most property names differ from the actual QSettings serialization keys.
_LEGACY_KEYS = {
    "vSync": "vsync",
    "framePacing": "framepacing",
    "mouseAcceleration": "mouseacceleration",
    "captureSystemKeys": "capturesyskeys",
    "touchscreenTrackpad": "abstouchmode",
    "swapMouseButtons": "swapmousebuttons",
    "reverseScrollDirection": "reversescroll",
    "audioConfig": "audiocfg",
    "muteHostSpeakers": "hostaudio",
    "muteOnFocusLost": "muteonfocusloss",
    "gamepadSwapButtons": "swapfacebuttons",
    "gamepadMouseEmulation": "gamepadmouse",
    "gamepadBackgroundInput": "backgroundgamepad",
    "optimizeGameSettings": "gameopts",
    "quitAfter": "quitAppAfter",
    "quitappafter": "quitAppAfter",
    "videoCodec": "videocfg",
    "videoDecoder": "videodec",
    "unlockBitrate": "unlockbitrate",
    "pcAutodiscovery": "mdns",
    "checkBlockedConnections": "detectnetblocking",
    "performanceOverlay": "showperfoverlay",
    "connectionQualityWarnings": "connwarnings",
    "keepDisplayAwake": "keepawake",
    "windowMode": "windowmode",
}


def moonlight_config_paths() -> list[Path]:
    """Where Moonlight Qt keeps its settings: native first, then Flatpak."""
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return [
        config_home / "Moonlight Game Streaming Project" / "Moonlight.conf",
        Path.home() / ".var/app/com.moonlight_stream.Moonlight/config/Moonlight Game Streaming Project/Moonlight.conf",
    ]


def paired_host_certificate(address: str, paths: list[Path] | None = None) -> str:
    """The server certificate Moonlight stored for ``address`` (``""`` if none).

    Moonlight writes ``hosts\\N\\srvcert`` only after a pairing succeeds, next to
    the addresses it knows for that host.
    """
    wanted = address.strip().strip("[]").split("%")[0].lower()
    for path in paths or moonlight_config_paths():
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        section = ""
        hosts: dict[str, dict[str, str]] = {}
        for line in lines:
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
                continue
            if section != "hosts" or "=" not in line or "\\" not in line.split("=", 1)[0]:
                continue
            key, value = line.split("=", 1)
            index, name = key.split("\\", 1)
            hosts.setdefault(index, {})[name.lower()] = value
        for entry in hosts.values():
            known = {
                entry.get(field, "").strip("[]").rsplit(":", 1)[0].lower() if entry.get(field, "").count(":") == 1 else entry.get(field, "").strip("[]").lower()
                for field in ("manualaddress", "localaddress", "remoteaddress", "ipv6address")
            }
            if wanted in known and entry.get("srvcert"):
                return entry["srvcert"]
    return ""


class MoonlightConfigManager:
    _shared_state: dict[str, Any] = {}
    _lock = threading.RLock()

    def __init__(self) -> None:
        self.__dict__ = self._shared_state
        if hasattr(self, "cp"):
            return
        candidates = moonlight_config_paths()
        self.config_file: Path = next((p for p in candidates if p.is_file()), candidates[0])
        self.cp = self._parser()
        self._load_error = False
        self.load()

    @staticmethod
    def _parser() -> configparser.ConfigParser:
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = lambda optionstr: optionstr
        return parser

    def _migrate_keys(self) -> None:
        general = self.cp["General"]
        for legacy, canonical in _LEGACY_KEYS.items():
            # Older ConfigParser versions used by this app lowercased aliases.
            for alias in dict.fromkeys((legacy, legacy.lower())):
                if alias == canonical or alias not in general:
                    continue
                value = general[alias]
                if legacy in ("touchscreenTrackpad", "muteHostSpeakers"):
                    value = "false" if value.lower() == "true" else "true"
                elif legacy == "captureSystemKeys":
                    value = "2" if value.lower() == "true" else "0"
                elif legacy == "videoCodec" and value == "3":
                    value = "4"  # AV1 is enum 4; enum 3 is legacy HEVC+HDR.
                elif legacy == "windowMode":
                    value = {"3": "1", "1": "0", "2": "2"}.get(value, "1")
                if canonical not in general:
                    general[canonical] = value
                del general[alias]
        # Unambiguously invalid old value. Do not reinterpret valid native 0/1/2.
        if general.get("windowmode") == "3":
            general["windowmode"] = "1"
        # This property never existed in Moonlight; preserve unrelated keys.
        for key in ("gamepadForceController1", "gamepadforcecontroller1"):
            general.pop(key, None)

    def load(self) -> None:
        with self._lock:
            self._load_error = False
            parser = self._parser()
            try:
                if self.config_file.exists():
                    with self.config_file.open(encoding="utf-8") as stream:
                        parser.read_file(stream)
            except (OSError, UnicodeError, configparser.Error) as exc:
                self._load_error = True
                _log.error("Cannot read Moonlight settings; refusing to overwrite them: %s", exc)
            if "General" not in parser:
                parser.add_section("General")
            self.cp = parser
            self._migrate_keys()

    def reload(self) -> None:
        self.load()

    def save(self) -> bool:
        """Write atomically with owner-only permissions; never erase unreadable data."""
        with self._lock:
            if self._load_error:
                return False
            try:
                stream = io.StringIO()
                self.cp.write(stream, space_around_delimiters=False)
                secure_write_text(str(self.config_file), stream.getvalue())
                return True
            except OSError as exc:
                _log.error("Cannot save Moonlight settings: %s", exc)
                return False

    def get(self, key: str, default: object = None) -> str:
        return self.cp.get("General", key, fallback=str(default))

    def reset_streaming_settings(self) -> bool:
        """Reset supported preferences without deleting identity or paired hosts."""
        with self._lock:
            self.load()
            if self._load_error:
                return False
            owned = set(_LEGACY_KEYS.values()) | {"width", "height", "fps", "bitrate", "hdr", "yuv444", "multicontroller"}
            for key in owned:
                self.cp["General"].pop(key, None)
            return self.save()

    def set_many(self, values: dict[str, object]) -> bool:
        # Refresh before each transaction so an expert setting is not lost when
        # a different page changes picture size. Qt's running process can still
        # write later; configuration should be edited between sessions.
        with self._lock:
            self.load()
            if self._load_error:
                return False
            for key, value in values.items():
                self.cp.set("General", key, str(value))
            return self.save()

    def set(self, key: str, value: object) -> bool:
        return self.set_many({key: value})
