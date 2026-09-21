#!/usr/bin/env python3
"""Verify gettext catalogs through real runtime lookups.

When GNU ``msgfmt`` is available, every PO is compiled into a temporary tree
first. Otherwise, the checked-in development MOs are exercised. The structural
validator separately proves that those checked-in MOs match their PO sources.
"""

from __future__ import annotations

import gettext
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCALE_DIR = ROOT / "locale"
RUNTIME_LOCALE_DIR = ROOT / "usr" / "share" / "locale"
DOMAIN = "big-remote-play"


def languages() -> list[str]:
    values: list[str] = []
    for raw in (LOCALE_DIR / "LINGUAS").read_text(encoding="utf-8").splitlines():
        value = raw.split("#", 1)[0].strip()
        if value:
            values.extend(value.split())
    return values


def _translation(root: Path, language: str) -> gettext.NullTranslations:
    return gettext.translation(
        DOMAIN,
        localedir=root,
        languages=[language],
        fallback=False,
    )


def _verify_runtime(root: Path) -> None:
    for language in languages():
        translation = _translation(root, language)
        translated = translation.gettext("Unknown")
        if language != "en" and translated == "Unknown":
            raise SystemExit(f"FAIL: {language} returned the English source for a known translated key")

    simplified = _translation(root, "zh_CN").gettext("Install Dependencies")
    traditional = _translation(root, "zh_TW").gettext("Install Dependencies")
    if simplified == traditional:
        raise SystemExit("FAIL: zh_CN and zh_TW returned identical runtime text")

    european = _translation(root, "pt").gettext("Client Settings")
    brazilian = _translation(root, "pt_BR").gettext("Client Settings")
    if european == brazilian:
        # A single term can legitimately coincide. Use a second representative key.
        european = _translation(root, "pt").gettext("Save to File")
        brazilian = _translation(root, "pt_BR").gettext("Save to File")
    if european == brazilian:
        raise SystemExit("FAIL: pt and pt_BR returned identical representative text")


def main() -> int:
    msgfmt = shutil.which("msgfmt")
    if msgfmt:
        with tempfile.TemporaryDirectory(prefix="big-remote-play-gettext-") as tmp:
            compiled_root = Path(tmp)
            for language in languages():
                source = LOCALE_DIR / f"{language}.po"
                target = compiled_root / language / "LC_MESSAGES" / f"{DOMAIN}.mo"
                target.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    [
                        msgfmt,
                        "--check",
                        "--check-format",
                        "-o",
                        str(target),
                        str(source),
                    ],
                    check=True,
                )
            _verify_runtime(compiled_root)
        source = "freshly compiled catalogs"
    else:
        _verify_runtime(RUNTIME_LOCALE_DIR)
        source = "checked-in runtime catalogs (msgfmt unavailable)"

    print(f"OK: gettext lookup and regional runtime checks passed using {source}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
