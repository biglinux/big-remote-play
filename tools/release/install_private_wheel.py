#!/usr/bin/env python3
"""Install a pure-Python wheel into an application-private import root.

Native BigLinux/Manjaro packages use ``/usr/lib/big-remote-play`` instead of a
Python-minor-specific ``site-packages`` directory. The direct Python launcher
adds that one root to ``sys.path`` while dependencies continue to come from the
current system Python.
"""

from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import os
import shutil
import stat
import tempfile
import zipfile

_PACKAGE_NAME = "big_remote_play"
_DIST_INFO_PREFIX = "big_remote_play-"
_SUPPORTED_DATA_SCHEMES = {"purelib", "platlib"}
_NATIVE_SUFFIXES = {".so", ".pyd", ".dylib"}


def _relative_destination(member_name: str) -> PurePosixPath | None:
    """Map a wheel member to the private root and reject unsafe schemes."""

    if not member_name or member_name.endswith("/"):
        return None

    member = PurePosixPath(member_name)
    if member.is_absolute() or ".." in member.parts:
        raise ValueError(f"unsafe wheel member path: {member_name}")

    if len(member.parts) >= 2 and member.parts[0].endswith(".data"):
        scheme = member.parts[1]
        if scheme not in _SUPPORTED_DATA_SCHEMES:
            raise ValueError(f"unsupported wheel data scheme {scheme!r}: {member_name}")
        member = PurePosixPath(*member.parts[2:])
        if not member.parts:
            return None

    if member.suffix.lower() in _NATIVE_SUFFIXES:
        raise ValueError(f"native extension modules cannot use the version-independent layout: {member_name}")
    if member.suffix.lower() in {".pyc", ".pyo"} or "__pycache__" in member.parts:
        raise ValueError(f"generated Python bytecode is not allowed in the wheel: {member_name}")
    return member


def _wheel_is_pure(stage: Path) -> bool:
    wheel_files = list(stage.glob(f"{_DIST_INFO_PREFIX}*.dist-info/WHEEL"))
    if len(wheel_files) != 1:
        raise ValueError(f"expected one WHEEL metadata file, found {len(wheel_files)}")
    for line in wheel_files[0].read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip().lower() == "root-is-purelib":
            return value.strip().lower() == "true"
    raise ValueError("wheel metadata does not declare Root-Is-Purelib")


def _validate_stage(stage: Path) -> None:
    package_init = stage / _PACKAGE_NAME / "__init__.py"
    if not package_init.is_file():
        raise ValueError(f"wheel does not contain {_PACKAGE_NAME}/__init__.py")

    dist_infos = [path for path in stage.glob(f"{_DIST_INFO_PREFIX}*.dist-info") if path.is_dir()]
    if len(dist_infos) != 1:
        raise ValueError(f"expected one {_DIST_INFO_PREFIX}*.dist-info directory, found {len(dist_infos)}")
    for required in ("METADATA", "WHEEL", "RECORD"):
        if not (dist_infos[0] / required).is_file():
            raise ValueError(f"wheel metadata is missing {required}")

    if not _wheel_is_pure(stage):
        raise ValueError("wheel is not pure Python; rebuild/repackage it for the active Python ABI")


def _normalise_permissions(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"wheel extraction produced an unsupported symlink: {path}")
        if path.is_dir():
            path.chmod(0o755)
        elif path.is_file():
            path.chmod(0o644)


def install_private_wheel(wheel: Path, destination: Path) -> None:
    wheel = wheel.resolve(strict=True)
    destination = destination.resolve(strict=False)
    if wheel.suffix != ".whl":
        raise ValueError(f"not a wheel file: {wheel}")
    if destination.exists():
        raise FileExistsError(f"destination already exists: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{destination.name}.", dir=destination.parent) as temporary:
        stage = Path(temporary) / destination.name
        stage.mkdir()
        extracted: set[PurePosixPath] = set()

        with zipfile.ZipFile(wheel) as archive:
            damaged = archive.testzip()
            if damaged is not None:
                raise ValueError(f"wheel CRC check failed for {damaged}")
            for info in archive.infolist():
                mode = info.external_attr >> 16
                member_type = stat.S_IFMT(mode)
                if member_type == stat.S_IFLNK:
                    raise ValueError(f"symbolic links are not allowed in the wheel: {info.filename}")
                if member_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    raise ValueError(f"unsupported wheel member type: {info.filename}")
                relative = _relative_destination(info.filename)
                if relative is None:
                    continue
                if relative in extracted:
                    raise ValueError(f"duplicate wheel destination: {relative}")
                extracted.add(relative)
                target = stage.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)

        _validate_stage(stage)
        _normalise_permissions(stage)
        os.replace(stage, destination)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path, help="wheel built for Big Remote Play")
    parser.add_argument("destination", type=Path, help="private import root to create")
    return parser


def main() -> int:
    args = _parser().parse_args()
    install_private_wheel(args.wheel, args.destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
