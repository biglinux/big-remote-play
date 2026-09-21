from __future__ import annotations

import gettext
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GETTEXT_TOOLS = ("xgettext", "msgcat", "msgmerge", "msgattrib", "msgfmt")


def _hashes(root: Path) -> dict[str, str]:
    paths = [root / "locale/big-remote-play.pot"]
    paths.extend(sorted((root / "locale").glob("*.po")))
    paths.extend(sorted((root / "usr/share/locale").glob("*/LC_MESSAGES/big-remote-play.mo")))
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def _run_update(root: Path) -> None:
    environment = os.environ.copy()
    environment["SOURCE_DATE_EPOCH"] = "1789862400"
    subprocess.run(
        [sys.executable, "tools/i18n/update_catalogs.py"],
        cwd=root,
        env=environment,
        check=True,
        text=True,
        capture_output=True,
    )


def test_catalog_update_is_idempotent_and_keeps_regional_variants(tmp_path: Path) -> None:
    missing = [tool for tool in GETTEXT_TOOLS if shutil.which(tool) is None]
    if missing:
        pytest.skip("GNU gettext tools unavailable: " + ", ".join(missing))

    copy = tmp_path / "project"
    shutil.copytree(
        ROOT,
        copy,
        ignore=shutil.ignore_patterns(
            ".git",
            ".pytest_cache",
            ".ruff_cache",
            ".mypy_cache",
            "__pycache__",
            "*.pyc",
            "build",
            "dist",
        ),
        symlinks=True,
    )

    _run_update(copy)
    first = _hashes(copy)
    _run_update(copy)
    second = _hashes(copy)

    assert first == second

    def runtime_catalog(language: str) -> dict[object, str]:
        path = copy / "usr/share/locale" / language / "LC_MESSAGES/big-remote-play.mo"
        with path.open("rb") as stream:
            translation = gettext.GNUTranslations(stream)
        return {
            key: value
            for key, value in translation._catalog.items()  # type: ignore[attr-defined]
            if key != ""
        }

    # Compare compiled message payloads, not PO headers, so metadata-only
    # differences cannot satisfy the regional-variant regression guard.
    assert runtime_catalog("pt") != runtime_catalog("pt_BR")
    assert runtime_catalog("zh_CN") != runtime_catalog("zh_TW")
    for path in (copy / "locale").glob("*.po"):
        text = path.read_text(encoding="utf-8")
        assert "#, fuzzy" not in text
        assert "#~ msgid" not in text
