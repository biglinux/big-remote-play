"""PEP 517 wrapper that stamps build-date metadata without mutating the checkout."""

from __future__ import annotations

from contextlib import contextmanager
import gzip
import importlib
import os
from pathlib import Path
import re
import tarfile
from tempfile import NamedTemporaryFile
from typing import Any, Iterator

from build_version import build_version

_ORIGINAL_BACKEND = "uv_build"
_VERSION = re.compile(r"^(?:\d{2}|\d{4})\.\d{2}\.\d{2}$")


def _backend() -> Any:
    module_name, sep, attr = _ORIGINAL_BACKEND.partition(":")
    obj: Any = importlib.import_module(module_name)
    return getattr(obj, attr) if sep else obj


def _source_version(root: Path) -> str | None:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    project = re.search(r"(?ms)^\[project\]\s*\n(.*?)(?=^\[|\Z)", text)
    if not project:
        return None
    match = re.search(r'(?m)^version\s*=\s*["\']([^"\']+)["\']', project.group(1))
    if not match:
        return None
    value = match.group(1)
    return value if value != "0.0.0" and _VERSION.fullmatch(value) else None


def _resolved_version(root: Path) -> str:
    # A stamped sdist keeps its date when rebuilt later without an epoch.
    return _source_version(root) or build_version()


def _stamp_text(path: Path, text: str, version: str) -> str:
    if path.name == "pyproject.toml":
        project = re.search(r"(?ms)^\[project\]\s*\n(.*?)(?=^\[|\Z)", text)
        if project:
            block = project.group(0)
            stamped = re.sub(
                r'(?m)^version\s*=\s*["\'][^"\']+["\']',
                f'version = "{version}"',
                block,
                count=1,
            )
            text = text[: project.start()] + stamped + text[project.end() :]
        return text
    if path.name == "PKGBUILD":
        return re.sub(r"(?m)^pkgver=.*$", f"pkgver={version}", text, count=1)
    if path.suffix == ".xml" and ("metainfo" in path.name or "appdata" in path.name):
        return re.sub(
            r'(<release\s+version=")[^"]+("\s+date=")[^"]+("[^>]*>)',
            lambda m: m.group(1) + version + m.group(2) + _iso_date(version) + m.group(3),
            text,
            count=1,
        )
    # Explicit build-version placeholders and conventional assignments only.
    text = text.replace("@BUILD_VERSION@", version)
    text = re.sub(
        r'(?m)^(\s*(?:__version__|VERSION|version)\s*=\s*["\'])0\.0\.0(["\'])',
        lambda m: m.group(1) + version + m.group(2),
        text,
    )
    return text


def _iso_date(version: str) -> str:
    parts = version.split(".")
    year = parts[0] if len(parts[0]) == 4 else "20" + parts[0]
    return f"{year}-{parts[1]}-{parts[2]}"


def _stamp_targets(root: Path) -> list[Path]:
    targets = [root / "pyproject.toml"]
    for name in ("PKGBUILD", "pkgbuild/PKGBUILD", "flake.nix", "default.nix"):
        candidate = root / name
        if candidate.exists():
            targets.append(candidate)
    for pattern in ("*.metainfo.xml", "*.appdata.xml"):
        targets.extend(root.rglob(pattern))
    for candidate in root.rglob("*.py"):
        # Only the project's own build output is skipped. Matching the absolute
        # path instead silently stops stamping for every checkout that happens
        # to sit under a directory called build/ or dist/ — makepkg's BUILDDIR
        # is /build on BigLinux, which shipped packages with __version__ 0.0.0.
        if any(part in {".git", "build", "dist"} for part in candidate.relative_to(root).parts):
            continue
        try:
            sample = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if "@BUILD_VERSION@" in sample or re.search(r'(?m)^(?:__version__|VERSION|version)\s*=\s*["\']0\.0\.0', sample):
            targets.append(candidate)
    return list(dict.fromkeys(path for path in targets if path.exists()))


@contextmanager
def _stamped_tree() -> Iterator[str]:
    root = Path.cwd()
    version = _resolved_version(root)
    originals: dict[Path, bytes] = {}
    try:
        for path in _stamp_targets(root):
            raw = path.read_bytes()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            stamped = _stamp_text(path.relative_to(root), text, version)
            if stamped != text:
                originals[path] = raw
                path.write_text(stamped, encoding="utf-8")
        yield version
    finally:
        for path, raw in originals.items():
            path.write_bytes(raw)


def _call(name: str, *args: Any, **kwargs: Any) -> Any:
    with _stamped_tree():
        return getattr(_backend(), name)(*args, **kwargs)


def get_requires_for_build_wheel(config_settings=None):
    return _call("get_requires_for_build_wheel", config_settings)


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    return _call("prepare_metadata_for_build_wheel", metadata_directory, config_settings)


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    return _call("build_wheel", wheel_directory, config_settings, metadata_directory)


def get_requires_for_build_sdist(config_settings=None):
    return _call("get_requires_for_build_sdist", config_settings)


def _strip_sdist_compatibility_copy(archive: Path) -> None:
    """Remove uv's internal ``pyproject.toml.orig`` compatibility copy.

    The rewritten ``pyproject.toml`` in the sdist is the canonical build input.
    Keeping the pre-rewrite copy creates generated clutter and makes the source
    archive fail the project's own hygiene gate.  Repacking is deterministic
    when ``SOURCE_DATE_EPOCH`` is set and preserves all member metadata.
    """

    epoch_text = os.environ.get("SOURCE_DATE_EPOCH", "0")
    try:
        gzip_mtime = max(0, int(epoch_text))
    except ValueError:
        gzip_mtime = 0

    removed = 0
    with NamedTemporaryFile(prefix=f".{archive.name}.", suffix=".tmp", dir=archive.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)

    try:
        with tarfile.open(archive, mode="r:gz") as source, temporary_path.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=gzip_mtime) as compressed:
                with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT) as target:
                    for member in source:
                        if member.name == "pyproject.toml.orig" or member.name.endswith("/pyproject.toml.orig"):
                            removed += 1
                            continue
                        payload = source.extractfile(member) if member.isfile() else None
                        target.addfile(member, payload)

        if removed > 1:
            raise RuntimeError(f"source distribution contains {removed} pyproject.toml.orig entries")
        os.replace(temporary_path, archive)
    finally:
        temporary_path.unlink(missing_ok=True)


def build_sdist(sdist_directory, config_settings=None):
    filename = _call("build_sdist", sdist_directory, config_settings)
    _strip_sdist_compatibility_copy(Path(sdist_directory) / filename)
    return filename


def get_requires_for_build_editable(config_settings=None):
    backend = _backend()
    hook = getattr(backend, "get_requires_for_build_editable", None)
    return _call("get_requires_for_build_editable", config_settings) if hook else []


def prepare_metadata_for_build_editable(metadata_directory, config_settings=None):
    return _call("prepare_metadata_for_build_editable", metadata_directory, config_settings)


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    return _call("build_editable", wheel_directory, config_settings, metadata_directory)
