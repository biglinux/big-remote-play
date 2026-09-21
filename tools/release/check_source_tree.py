#!/usr/bin/env python3
"""Reject generated clutter and stale compatibility artifacts in the source tree."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCALE_ROOT = ROOT / "usr" / "share" / "locale"
DOMAIN = "big-remote-play"

ALLOWED_ROOT_MARKDOWN = {
    "README.md",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
}
FORBIDDEN_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".pyright",
    ".ruff_cache",
    "artifacts",
    "reports",
    "dist",
    "build",
}
FORBIDDEN_SUFFIXES = {
    ".bak",
    ".log",
    ".orig",
    ".pyc",
    ".pyo",
    ".rej",
    ".sarif",
    ".tmp",
    ".whl",
}
FORBIDDEN_ROOT_SUFFIXES = {".csv", ".json", ".log", ".sarif", ".txt"}
FORBIDDEN_ROOT_NAMES = {"SHA256SUMS", "MANIFEST.sha256"}
FORBIDDEN_EXACT = {
    "pkgbuild/pkgbuild.install",
    "usr/share/icons/hicolor/scalable/apps/big-remote-play-together.svg",
    "usr/share/big-remote-play/img/big-remote-play.svg",
}


def linguas() -> list[str]:
    result: list[str] = []
    path = ROOT / "locale" / "LINGUAS"
    if not path.is_file():
        return result
    for raw in path.read_text(encoding="utf-8").splitlines():
        value = raw.split("#", 1)[0].strip()
        if value:
            result.extend(value.split())
    return result


def _check_symlink(path: Path, relative: Path, errors: list[str]) -> None:
    try:
        target = (path.parent / os.readlink(path)).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        errors.append(f"broken or unreadable symlink: {relative.as_posix()} ({error})")
        return
    try:
        target.relative_to(ROOT.resolve())
    except ValueError:
        errors.append(f"symlink escapes source tree: {relative.as_posix()} -> {target}")


def main() -> int:
    errors: list[str] = []

    for path in ROOT.iterdir():
        if not path.is_file():
            continue
        if path.suffix == ".md" and path.name not in ALLOWED_ROOT_MARKDOWN:
            errors.append(f"unexpected Markdown file at repository root: {path.name}")
        if path.suffix.lower() in FORBIDDEN_ROOT_SUFFIXES:
            errors.append(f"generated data/log file at repository root: {path.name}")
        if path.name in FORBIDDEN_ROOT_NAMES:
            errors.append(f"stale generated manifest at repository root: {path.name}")

    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        relative_string = relative.as_posix()

        if ".git" in relative.parts:
            continue
        if path.is_symlink():
            _check_symlink(path, relative, errors)
            continue
        if path.is_dir():
            if path.name in FORBIDDEN_DIR_NAMES:
                errors.append(f"generated directory remains: {relative_string}/")
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            errors.append(f"generated file remains: {relative_string}")
        if relative_string in FORBIDDEN_EXACT:
            errors.append(f"obsolete or redundant file remains: {relative_string}")
        if path.name == "big-remote-play-together.mo":
            errors.append(f"obsolete gettext domain remains: {relative_string}")
        if path.parent == ROOT / "locale" and path.suffix == ".json":
            errors.append(f"generated translation sidecar remains: {relative_string}")

    expected = linguas()
    if not expected:
        errors.append("locale/LINGUAS is missing or empty")
    elif len(expected) != len(set(expected)):
        errors.append("locale/LINGUAS contains duplicate entries")
    else:
        expected_set = set(expected)
        actual_po = {path.stem for path in (ROOT / "locale").glob("*.po")}
        if actual_po != expected_set:
            for language in sorted(expected_set - actual_po):
                errors.append(f"missing source catalog: locale/{language}.po")
            for language in sorted(actual_po - expected_set):
                errors.append(f"unlisted source catalog: locale/{language}.po")

        actual_mo: set[str] = set()
        if LOCALE_ROOT.is_dir():
            for path in LOCALE_ROOT.rglob("*"):
                if path.is_dir():
                    continue
                relative = path.relative_to(LOCALE_ROOT)
                if len(relative.parts) == 3 and relative.parts[1] == "LC_MESSAGES" and relative.name == f"{DOMAIN}.mo":
                    actual_mo.add(relative.parts[0])
                    if path.stat().st_size == 0:
                        errors.append(f"compiled runtime catalog is empty: {path.relative_to(ROOT).as_posix()}")
                else:
                    errors.append(f"unexpected file in compiled locale tree: {path.relative_to(ROOT).as_posix()}")
        else:
            errors.append("compiled runtime locale directory is missing: usr/share/locale")

        for language in sorted(expected_set - actual_mo):
            errors.append(f"missing compiled runtime catalog: usr/share/locale/{language}/LC_MESSAGES/{DOMAIN}.mo")
        for language in sorted(actual_mo - expected_set):
            errors.append(f"compiled runtime catalog is not listed in LINGUAS: usr/share/locale/{language}/LC_MESSAGES/{DOMAIN}.mo")

    if errors:
        print("SOURCE TREE HYGIENE FAILED", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("OK: source tree is free of generated root reports, caches, and obsolete assets; locale inventories are exact.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
