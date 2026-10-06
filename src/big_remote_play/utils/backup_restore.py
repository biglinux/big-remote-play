"""Versioned, bounded and transactional backup/restore support.

The archive contains only regular files owned or deliberately managed by Big
Remote Play.  Secret Service values are never read here; JSON files that refer
to a secret contain metadata only and restoring them may require signing in
again on the new computer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tarfile
import tempfile
from typing import Iterable


FORMAT_NAME = "big-remote-play-backup"
FORMAT_VERSION = 1
MAX_ARCHIVE_SIZE = 64 * 1024 * 1024
MAX_CONTENT_SIZE = 128 * 1024 * 1024
MAX_FILE_SIZE = 32 * 1024 * 1024
MAX_ENTRIES = 4096


class BackupError(Exception):
    """A backup could not be created or restored safely."""


class BackupValidationError(BackupError):
    """The selected archive is invalid or outside the supported contract."""


@dataclass(frozen=True)
class BackupSummary:
    components: tuple[str, ...]
    file_count: int


@dataclass(frozen=True)
class _Entry:
    archive_path: str
    component: str
    relative_path: str
    data: bytes
    mode: int = 0o600


_TRANSIENT_TOP_LEVEL = frozenset({"logs", "audio-session.json"})
# sunshine_cert.sha256 pins the certificate Sunshine has on this computer
# (trust on first use). Sunshine's certificate itself is not in the backup, so
# a restored pin would make every API call fail after a new certificate.
_TRANSIENT_NAMES = frozenset({"sunshine.log", "sunshine.pid", "sunshine_cert.sha256"})
_LEGACY_JSON_SECRET_KEYS = frozenset({"auth_key", "api_key", "cf_token"})


def _is_temporary_name(name: str) -> bool:
    return name in _TRANSIENT_NAMES or name.startswith((".config-", ".brp-backup-", ".brp-restore-")) or name.endswith((".tmp", ".part"))


def _remove_json_secrets(value):
    if isinstance(value, dict):
        return {key: _remove_json_secrets(item) for key, item in value.items() if key not in _LEGACY_JSON_SECRET_KEYS}
    if isinstance(value, list):
        return [_remove_json_secrets(item) for item in value]
    return value


def _sanitized_data(component: str, relative: Path, data: bytes) -> bytes | None:
    """Remove known pre-keyring credentials from the copy, never the source."""
    relative_name = relative.as_posix()
    if component == "application" and relative_name == "zerotier/api_token.txt":
        return None
    if component == "application" and relative_name in {
        "private_network/history.json",
        "private_network/private_network.json",
    }:
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            # An unreadable legacy file may contain a token. Omitting it is
            # safer than exporting bytes that cannot be inspected.
            return None
        return (json.dumps(_remove_json_secrets(payload), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if component == "sunshine" and relative_name == "sunshine.conf":
        try:
            lines = data.decode("utf-8").splitlines(keepends=True)
        except UnicodeError:
            return None
        kept = []
        for line in lines:
            key = line.split("=", 1)[0].strip() if "=" in line else ""
            if key not in {"sunshine_password", "credentials"}:
                kept.append(line)
        return "".join(kept).encode("utf-8")
    return data


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _safe_relative(name: str) -> PurePosixPath:
    if not name or "\\" in name or name.startswith("/"):
        raise BackupValidationError("The backup contains an invalid path.")
    path = PurePosixPath(name)
    if str(path) != name or any(part in ("", ".", "..") for part in path.parts):
        raise BackupValidationError("The backup contains an invalid path.")
    return path


def _iter_regular_files(root: Path, *, application: bool = False) -> Iterable[tuple[Path, Path]]:
    """Yield safe regular files below *root* without following links."""
    if not root.exists():
        return
    if root.is_symlink() or not root.is_dir():
        raise BackupError(f"The backup source is not a safe directory: {root}")
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        relative_dir = current_path.relative_to(root)
        kept_directories: list[str] = []
        for name in sorted(directories):
            child = current_path / name
            if child.is_symlink():
                continue
            if application and relative_dir == Path(".") and (name == "sunshine" or name in _TRANSIENT_TOP_LEVEL):
                continue
            if _is_temporary_name(name):
                continue
            kept_directories.append(name)
        directories[:] = kept_directories
        for name in sorted(files):
            source = current_path / name
            relative = source.relative_to(root)
            if application and relative.parts and relative.parts[0] in _TRANSIENT_TOP_LEVEL:
                continue
            if _is_temporary_name(name) or source.is_symlink():
                continue
            try:
                file_mode = source.stat(follow_symlinks=False).st_mode
            except OSError as error:
                raise BackupError(f"Could not inspect {source}: {error}") from error
            if not stat.S_ISREG(file_mode):
                continue
            yield source, relative


class BackupManager:
    """Create and restore the supported configuration bundle."""

    def __init__(self, config_dir: Path, sunshine_dir: Path, moonlight_file: Path | None, *, app_version: str = ""):
        self.config_dir = Path(config_dir)
        self.sunshine_dir = Path(sunshine_dir)
        self.moonlight_file = Path(moonlight_file) if moonlight_file is not None else None
        self.app_version = app_version

    def _source_entries(self, excluded: set[Path]) -> list[_Entry]:
        result: list[_Entry] = []
        sources = (
            ("application", self.config_dir, True),
            ("sunshine", self.sunshine_dir, False),
        )
        for component, root, is_application in sources:
            for source, relative in _iter_regular_files(root, application=is_application):
                if source.resolve() in excluded:
                    continue
                data = _sanitized_data(component, relative, source.read_bytes())
                if data is None:
                    continue
                if len(data) > MAX_FILE_SIZE:
                    raise BackupError(f"A settings file is too large to back up: {relative}")
                archive_path = f"{component}/{relative.as_posix()}"
                result.append(_Entry(archive_path, component, relative.as_posix(), data))
        moonlight = self.moonlight_file
        if moonlight is not None and moonlight.exists():
            if moonlight.is_symlink() or not moonlight.is_file():
                raise BackupError("The Moonlight configuration is not a regular file.")
            data = moonlight.read_bytes()
            if len(data) > MAX_FILE_SIZE:
                raise BackupError("The Moonlight configuration is too large to back up.")
            result.append(_Entry("moonlight/Moonlight.conf", "moonlight", "Moonlight.conf", data))
        if len(result) > MAX_ENTRIES or sum(len(entry.data) for entry in result) > MAX_CONTENT_SIZE:
            raise BackupError("The settings exceed the supported backup size.")
        return sorted(result, key=lambda entry: entry.archive_path)

    @staticmethod
    def _manifest(entries: list[_Entry], app_version: str) -> bytes:
        document = {
            "format": FORMAT_NAME,
            "version": FORMAT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "app_version": app_version,
            "components": sorted({entry.component for entry in entries}),
            "files": [
                {
                    "path": entry.archive_path,
                    "size": len(entry.data),
                    "sha256": hashlib.sha256(entry.data).hexdigest(),
                }
                for entry in entries
            ],
        }
        return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")

    @staticmethod
    def _add_bytes(archive: tarfile.TarFile, name: str, data: bytes) -> None:
        info = tarfile.TarInfo(name)
        info.size = len(data)
        info.mode = 0o600
        info.mtime = 0
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        archive.addfile(info, io.BytesIO(data))

    def create(self, destination: Path) -> BackupSummary:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=".brp-backup-", dir=destination.parent)
        temporary = Path(temporary_name)
        try:
            excluded = {temporary.resolve(), destination.resolve()}
            entries = self._source_entries(excluded)
            with os.fdopen(fd, "wb") as output:
                with tarfile.open(fileobj=output, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
                    self._add_bytes(archive, "manifest.json", self._manifest(entries, self.app_version))
                    for entry in entries:
                        self._add_bytes(archive, entry.archive_path, entry.data)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, 0o600)
            # Never replace a known-good destination until the completed file
            # has passed the exact same validation used during restore.
            self.inspect(temporary)
            os.replace(temporary, destination)
            os.chmod(destination, 0o600)
            _fsync_directory(destination.parent)
            return BackupSummary(tuple(sorted({entry.component for entry in entries})), len(entries))
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _read_member(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
        if member.size < 0 or member.size > MAX_FILE_SIZE:
            raise BackupValidationError("The backup contains a file that is too large.")
        stream = archive.extractfile(member)
        if stream is None:
            raise BackupValidationError("A file in the backup could not be read.")
        with stream:
            data = stream.read(MAX_FILE_SIZE + 1)
        if len(data) != member.size or len(data) > MAX_FILE_SIZE:
            raise BackupValidationError("A file in the backup is incomplete or too large.")
        return data

    def _read_archive(self, archive_path: Path) -> tuple[dict[str, bytes], bool]:
        try:
            archive_stat = archive_path.stat()
        except OSError as error:
            raise BackupValidationError(f"Could not open the backup: {error}") from error
        if not stat.S_ISREG(archive_stat.st_mode) or archive_stat.st_size > MAX_ARCHIVE_SIZE:
            raise BackupValidationError("The backup file is too large or is not a regular file.")
        files: dict[str, bytes] = {}
        total = 0
        try:
            with tarfile.open(archive_path, mode="r:gz") as archive:
                members = archive.getmembers()
                if not members or len(members) > MAX_ENTRIES + 64:
                    raise BackupValidationError("The backup has an unsupported number of entries.")
                seen: set[str] = set()
                for member in members:
                    path = _safe_relative(member.name)
                    normalized = path.as_posix()
                    if normalized in seen:
                        raise BackupValidationError("The backup contains duplicate paths.")
                    seen.add(normalized)
                    if member.isdir():
                        continue
                    if not member.isfile():
                        raise BackupValidationError("The backup contains links or special files.")
                    total += member.size
                    if total > MAX_CONTENT_SIZE:
                        raise BackupValidationError("The uncompressed backup is too large.")
                    files[normalized] = self._read_member(archive, member)
        except (OSError, tarfile.TarError) as error:
            raise BackupValidationError(f"The backup archive is damaged: {error}") from error
        return files, "manifest.json" in files

    def _new_entries(self, files: dict[str, bytes]) -> list[_Entry]:
        try:
            manifest = json.loads(files.pop("manifest.json").decode("utf-8"))
        except (KeyError, UnicodeError, json.JSONDecodeError) as error:
            raise BackupValidationError("The backup manifest is missing or damaged.") from error
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT_NAME or manifest.get("version") != FORMAT_VERSION:
            raise BackupValidationError("This backup format or version is not supported.")
        declared_components = manifest.get("components")
        if not isinstance(declared_components, list) or any(component not in {"application", "sunshine", "moonlight"} for component in declared_components):
            raise BackupValidationError("The backup component list is malformed.")
        inventory = manifest.get("files")
        if not isinstance(inventory, list) or len(inventory) != len(files):
            raise BackupValidationError("The backup inventory does not match its contents.")
        expected: dict[str, tuple[int, str]] = {}
        for item in inventory:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise BackupValidationError("The backup inventory is malformed.")
            path = _safe_relative(item["path"]).as_posix()
            if path in expected or not isinstance(item.get("size"), int) or not isinstance(item.get("sha256"), str):
                raise BackupValidationError("The backup inventory is malformed.")
            expected[path] = (item["size"], item["sha256"])
        if set(expected) != set(files):
            raise BackupValidationError("The backup inventory does not match its contents.")
        entries: list[_Entry] = []
        for name, data in files.items():
            parts = PurePosixPath(name).parts
            if len(parts) < 2 or parts[0] not in {"application", "sunshine", "moonlight"}:
                raise BackupValidationError("The backup contains an unknown component.")
            if parts[0] == "application" and (parts[1] == "sunshine" or parts[1] in _TRANSIENT_TOP_LEVEL):
                raise BackupValidationError("The backup contains an overlapping or transient application path.")
            if parts[0] == "moonlight" and tuple(parts[1:]) != ("Moonlight.conf",):
                raise BackupValidationError("The backup contains an unexpected Moonlight file.")
            size, digest = expected[name]
            if size != len(data) or digest != hashlib.sha256(data).hexdigest():
                raise BackupValidationError("The backup failed its integrity check.")
            if parts[-1] in _TRANSIENT_NAMES:
                continue  # an older backup's certificate pin: trusted again on first use
            relative = PurePosixPath(*parts[1:]).as_posix()
            if _sanitized_data(parts[0], Path(relative), data) != data:
                raise BackupValidationError("The backup contains a plaintext credential.")
            entries.append(_Entry(name, parts[0], relative, data))
        if sorted(set(declared_components)) != sorted({entry.component for entry in entries}):
            raise BackupValidationError("The backup component list does not match its contents.")
        if not entries:
            raise BackupValidationError("The backup contains no settings.")
        return entries

    def _legacy_entries(self, files: dict[str, bytes]) -> list[_Entry]:
        """Map archives produced before the manifest format to safe namespaces."""
        config_root = self.config_dir.name
        sunshine_root = self.sunshine_dir.name
        moonlight_root = self.moonlight_file.parent.name if self.moonlight_file is not None else "Moonlight Game Streaming Project"
        if len({config_root, sunshine_root, moonlight_root}) != 3:
            raise BackupValidationError("Legacy backup namespaces are ambiguous on this system.")
        merged: dict[tuple[str, str], bytes] = {}
        recognized = False
        for name, data in files.items():
            parts = PurePosixPath(name).parts
            if len(parts) < 2:
                continue
            root, relative_parts = parts[0], parts[1:]
            component = ""
            if root == config_root:
                recognized = True
                if relative_parts[0] == "sunshine":
                    component, relative_parts = "sunshine", relative_parts[1:]
                else:
                    component = "application"
            elif root == sunshine_root:
                recognized = True
                component = "sunshine"
            elif root == moonlight_root:
                recognized = True
                if tuple(relative_parts) != ("Moonlight.conf",):
                    continue
                component = "moonlight"
            else:
                raise BackupValidationError("This file is not a Big Remote Play backup.")
            if not relative_parts:
                continue
            relative = PurePosixPath(*relative_parts).as_posix()
            if component == "application" and relative.split("/", 1)[0] in _TRANSIENT_TOP_LEVEL:
                continue
            if _is_temporary_name(PurePosixPath(relative).name):
                continue
            sanitized = _sanitized_data(component, Path(relative), data)
            if sanitized is None:
                continue
            data = sanitized
            key = (component, relative)
            previous = merged.get(key)
            if previous is not None and previous != data:
                raise BackupValidationError("The legacy backup contains conflicting duplicate settings.")
            merged[key] = data
        if not recognized or not merged:
            raise BackupValidationError("This file is not a Big Remote Play backup.")
        return [_Entry(f"{component}/{relative}", component, relative, data) for (component, relative), data in sorted(merged.items())]

    def inspect(self, archive_path: Path) -> BackupSummary:
        files, modern = self._read_archive(Path(archive_path))
        entries = self._new_entries(files) if modern else self._legacy_entries(files)
        return BackupSummary(tuple(sorted({entry.component for entry in entries})), len(entries))

    @staticmethod
    def _assert_safe_root(root: Path) -> None:
        if root.is_symlink():
            raise BackupValidationError(f"A restore destination is a symbolic link: {root}")
        current = root
        while current != current.parent and not current.exists():
            current = current.parent
        if current.is_symlink():
            raise BackupValidationError(f"A restore destination uses a symbolic link: {current}")

    def _current_files(self, component: str) -> dict[str, Path]:
        if component == "moonlight":
            if self.moonlight_file is not None and self.moonlight_file.exists():
                if self.moonlight_file.is_symlink() or not self.moonlight_file.is_file():
                    raise BackupValidationError("The Moonlight destination is not a regular file.")
                return {"Moonlight.conf": self.moonlight_file}
            return {}
        root = self.config_dir if component == "application" else self.sunshine_dir
        return {relative.as_posix(): source for source, relative in _iter_regular_files(root, application=component == "application")}

    def _target(self, component: str, relative: str) -> Path:
        relative_path = _safe_relative(relative)
        if component == "moonlight":
            if self.moonlight_file is None or relative != "Moonlight.conf":
                raise BackupValidationError("Moonlight is not available as a restore destination.")
            return self.moonlight_file
        root = self.config_dir if component == "application" else self.sunshine_dir
        return root.joinpath(*relative_path.parts)

    @staticmethod
    def _atomic_write(target: Path, data: bytes, mode: int = 0o600) -> None:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary_name = tempfile.mkstemp(prefix=".brp-restore-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary_name, mode)
            os.replace(temporary_name, target)
            _fsync_directory(target.parent)
        finally:
            Path(temporary_name).unlink(missing_ok=True)

    def restore(self, archive_path: Path) -> BackupSummary:
        files, modern = self._read_archive(Path(archive_path))
        entries = self._new_entries(files) if modern else self._legacy_entries(files)
        components = tuple(sorted({entry.component for entry in entries}))
        for component in components:
            root = self.moonlight_file if component == "moonlight" else (self.config_dir if component == "application" else self.sunshine_dir)
            if root is None:
                raise BackupValidationError("A restore destination is unavailable.")
            self._assert_safe_root(root)

        desired = {(entry.component, entry.relative_path): entry for entry in entries}
        current: dict[tuple[str, str], Path] = {}
        for component in components:
            current.update({(component, relative): path for relative, path in self._current_files(component).items()})
        affected = set(current) | set(desired)
        snapshots: dict[tuple[str, str], tuple[bytes, int] | None] = {}
        targets: dict[tuple[str, str], Path] = {}
        snapshot_size = 0

        # Complete every read and path check before the first mutation.
        for key in sorted(affected):
            target = self._target(*key)
            targets[key] = target
            if target.exists():
                if target.is_symlink() or not target.is_file():
                    raise BackupValidationError(f"A restore target is not a regular file: {target}")
                data = target.read_bytes()
                snapshot_size += len(data)
                if len(data) > MAX_FILE_SIZE or snapshot_size > MAX_CONTENT_SIZE:
                    raise BackupValidationError("The current settings are too large to restore transactionally.")
                snapshots[key] = (data, stat.S_IMODE(target.stat().st_mode))
            else:
                snapshots[key] = None
            parent = target.parent
            while parent != parent.parent:
                if parent.exists():
                    if parent.is_symlink() or not parent.is_dir():
                        raise BackupValidationError(f"A restore parent is unsafe: {parent}")
                    break
                parent = parent.parent

        changed: list[tuple[str, str]] = []
        try:
            for key in sorted(affected):
                target = targets[key]
                changed.append(key)
                if key in desired:
                    self._atomic_write(target, desired[key].data)
                elif target.exists():
                    target.unlink()
                    _fsync_directory(target.parent)
        except Exception as error:
            rollback_errors: list[str] = []
            for key in reversed(changed):
                target = targets[key]
                try:
                    original = snapshots[key]
                    if original is None:
                        target.unlink(missing_ok=True)
                    else:
                        self._atomic_write(target, original[0], original[1])
                except Exception as rollback_error:  # pragma: no cover - catastrophic filesystem failure
                    rollback_errors.append(str(rollback_error))
            if rollback_errors:
                raise BackupError(f"Restore failed and rollback was incomplete: {error}; {'; '.join(rollback_errors)}") from error
            raise BackupError(f"Restore failed; the previous settings were restored: {error}") from error
        return BackupSummary(components, len(entries))
