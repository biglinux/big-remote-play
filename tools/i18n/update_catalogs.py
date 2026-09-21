#!/usr/bin/env python3
"""Rebuild, merge, validate, and compile every shipped gettext catalog.

The update is deterministic when ``SOURCE_DATE_EPOCH`` is set and transactional:
project catalogs are replaced only after all merges and ``msgfmt`` checks pass.
Python strings use ``_()``/``N_()``; shell strings use ``gettext`` or
``eval_gettext``. Machine-readable ``BRP_DATA`` and ``BRP_PHASE`` markers are
not translated.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCALE = ROOT / "locale"
POT = LOCALE / "big-remote-play.pot"
DOMAIN = "big-remote-play"
RUNTIME_LOCALE = ROOT / "usr/share/locale"


def _run(*args: str, cwd: Path = ROOT) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def _languages() -> list[str]:
    values: list[str] = []
    for raw in (LOCALE / "LINGUAS").read_text(encoding="utf-8").splitlines():
        values.extend(raw.split("#", 1)[0].split())
    if not values or len(values) != len(set(values)):
        raise ValueError("locale/LINGUAS is empty or contains duplicates")
    return values


def _quoted(value: str) -> str:
    parsed = ast.literal_eval(value.strip())
    if not isinstance(parsed, str):
        raise ValueError(value)
    return parsed


def _field_value(lines: list[str], field: str) -> str | None:
    prefix = field + " "
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            value = _quoted(line[len(prefix) :])
            index += 1
            while index < len(lines) and lines[index].startswith('"'):
                value += _quoted(lines[index])
                index += 1
            return value
    return None


def _replace_translation_field(lines: list[str], prefix: str, value: str) -> list[str]:
    index = next(
        (position for position, line in enumerate(lines) if line.startswith(prefix)),
        None,
    )
    if index is None:
        raise ValueError(f"missing {prefix.strip()} in English catalog entry")
    end = index + 1
    while end < len(lines) and lines[end].startswith('"'):
        end += 1
    lines[index:end] = [prefix + json.dumps(value, ensure_ascii=False)]
    return lines


def _fill_english(path: Path) -> None:
    """Make every English translation explicit and byte-stable."""
    output: list[str] = []
    for block in path.read_text(encoding="utf-8").split("\n\n"):
        lines = block.splitlines()
        message_id = _field_value(lines, "msgid")
        if message_id:
            plural = _field_value(lines, "msgid_plural")
            if plural is None:
                lines = _replace_translation_field(lines, "msgstr ", message_id)
            else:
                lines = _replace_translation_field(lines, "msgstr[0] ", message_id)
                lines = _replace_translation_field(lines, "msgstr[1] ", plural)
            block = "\n".join(lines)
        output.append(block)
    path.write_text("\n\n".join(output).rstrip() + "\n", encoding="utf-8")


def _creation_date() -> str:
    raw = os.environ.get("SOURCE_DATE_EPOCH")
    if raw is None:
        moment = datetime.now(timezone.utc)
    else:
        try:
            moment = datetime.fromtimestamp(int(raw), timezone.utc)
        except (ValueError, OverflowError) as error:
            raise ValueError("SOURCE_DATE_EPOCH must be an integer Unix timestamp") from error
    return moment.strftime("%Y-%m-%d %H:%M%z")


def _normalize_pot_creation_date(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    replacement = f'"POT-Creation-Date: {_creation_date()}\\n"'
    updated, count = re.subn(
        r'^"POT-Creation-Date: [^"\\]*(?:\\.[^"\\]*)*\\n"$',
        lambda _match: replacement,
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise ValueError(f"could not normalize POT-Creation-Date in {path}")
    path.write_text(updated, encoding="utf-8")


def _required_tools() -> None:
    missing = [tool for tool in ("xgettext", "msgcat", "msgmerge", "msgattrib", "msgfmt") if shutil.which(tool) is None]
    if missing:
        raise RuntimeError("missing GNU gettext tools: " + ", ".join(missing))


def main() -> int:
    _required_tools()
    languages = _languages()
    python_files = sorted(str(path.relative_to(ROOT)) for path in (ROOT / "src").rglob("*.py"))
    shell_files = sorted(str(path.relative_to(ROOT)) for path in (ROOT / "usr/share/big-remote-play/scripts").rglob("*.sh"))

    with tempfile.TemporaryDirectory(prefix="brp-i18n-") as directory:
        temporary = Path(directory)
        python_pot = temporary / "python.pot"
        shell_pot = temporary / "shell.pot"
        merged_pot = temporary / POT.name
        catalogs = temporary / "catalogs"
        runtime = temporary / "runtime"
        catalogs.mkdir()

        _run(
            "xgettext",
            "--language=Python",
            "--from-code=UTF-8",
            "--keyword=_",
            "--keyword=N_",
            "--add-comments=TRANSLATORS:",
            "--package-name=Big Remote Play",
            "--msgid-bugs-address=https://github.com/biglinux/big-remote-play/issues",
            f"--output={python_pot}",
            *python_files,
        )
        _run(
            "xgettext",
            "--language=Shell",
            "--from-code=UTF-8",
            "--keyword=gettext:1",
            "--keyword=eval_gettext:1",
            "--add-comments=TRANSLATORS:",
            "--package-name=Big Remote Play",
            "--msgid-bugs-address=https://github.com/biglinux/big-remote-play/issues",
            f"--output={shell_pot}",
            *shell_files,
        )
        _run(
            "msgcat",
            "--use-first",
            # ``msgcat --sort-output`` remains supported and gives msgmerge a
            # stable key order without relying on the deprecated xgettext or
            # msgmerge sorting switches.
            "--sort-output",
            f"--output-file={merged_pot}",
            str(python_pot),
            str(shell_pot),
        )
        _normalize_pot_creation_date(merged_pot)

        for language in languages:
            source = LOCALE / f"{language}.po"
            merged = catalogs / f"{language}.merged.po"
            cleaned = catalogs / f"{language}.po"
            _run(
                "msgmerge",
                "--no-fuzzy-matching",
                f"--output-file={merged}",
                str(source),
                str(merged_pot),
            )
            _run(
                "msgattrib",
                "--no-obsolete",
                f"--output-file={cleaned}",
                str(merged),
            )
            if language == "en":
                _fill_english(cleaned)
            destination = runtime / language / "LC_MESSAGES" / f"{DOMAIN}.mo"
            destination.parent.mkdir(parents=True, exist_ok=True)
            _run(
                "msgfmt",
                "--check",
                "--check-format",
                "--check-header",
                "-o",
                str(destination),
                str(cleaned),
            )

        # Commit only after every language validated and compiled successfully.
        POT.write_bytes(merged_pot.read_bytes())
        for language in languages:
            (LOCALE / f"{language}.po").write_bytes((catalogs / f"{language}.po").read_bytes())
            destination = RUNTIME_LOCALE / language / "LC_MESSAGES" / f"{DOMAIN}.mo"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((runtime / language / "LC_MESSAGES" / f"{DOMAIN}.mo").read_bytes())
            legacy = destination.with_name("big-remote-play-together.mo")
            legacy.unlink(missing_ok=True)

    print(f"Updated and compiled {len(languages)} catalogs from {POT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
