#!/usr/bin/env python3
"""Validate every gettext catalog without modifying project files.

The release gate is intentionally deterministic and offline. It checks the
catalog inventory, headers, key coverage, plural slots, fuzzy/untranslated
entries, placeholder preservation, GNU gettext syntax (when ``msgfmt`` is
available), and the required regional distinctions.
"""

from __future__ import annotations

import ast
import gettext
import json
import re
import shutil
import subprocess
import struct
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LOCALE_DIR = ROOT / "locale"
LINGUAS_FILE = LOCALE_DIR / "LINGUAS"
TEMPLATE = LOCALE_DIR / "big-remote-play.pot"

EXPECTED_PLURAL_COUNTS = {
    "be": 3,
    "bg": 2,
    "cs": 3,
    "da": 2,
    "de": 2,
    "el": 2,
    "en": 2,
    "es": 2,
    "et": 2,
    "fi": 2,
    "fr": 2,
    "he": 4,
    "hr": 3,
    "hu": 2,
    "is": 2,
    "it": 2,
    "ja": 1,
    "ko": 1,
    "nl": 2,
    "no": 2,
    "pl": 3,
    "pt": 2,
    "pt_BR": 2,
    "ro": 3,
    "ru": 3,
    "sk": 3,
    "sv": 2,
    "tr": 1,
    "uk": 3,
    "zh": 1,
    "zh_CN": 1,
    "zh_TW": 1,
}


def _unquote(value: str) -> str:
    parsed = ast.literal_eval(value.strip())
    if not isinstance(parsed, str):
        raise ValueError(f"expected quoted string, got {value!r}")
    return parsed


def _parse_po(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    active: tuple[str, int] | None = None

    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines() + [""], start=1):
        if not line:
            if current is not None and "id" in current:
                entries.append(current)
            current = None
            active = None
            continue

        if line.startswith("#~"):
            raise ValueError(f"{path}:{line_number}: obsolete entry remains")

        if current is None:
            current = {"flags": set(), "str": {}}

        if line.startswith("#"):
            if line.startswith("#,"):
                current["flags"].update(item.strip() for item in line[2:].split(",") if item.strip())
            continue

        match = re.match(r"msgctxt\s+(.*)", line)
        if match:
            current["ctx"] = _unquote(match.group(1))
            active = ("ctx", 0)
            continue

        match = re.match(r"msgid_plural\s+(.*)", line)
        if match:
            current["plural"] = _unquote(match.group(1))
            active = ("plural", 0)
            continue

        match = re.match(r"msgid\s+(.*)", line)
        if match:
            current["id"] = _unquote(match.group(1))
            active = ("id", 0)
            continue

        match = re.match(r"msgstr(?:\[(\d+)\])?\s+(.*)", line)
        if match:
            index = int(match.group(1) or 0)
            current["str"][index] = _unquote(match.group(2))
            active = ("str", index)
            continue

        if line.startswith('"') and active is not None:
            value = _unquote(line)
            field, index = active
            if field == "str":
                current["str"][index] = current["str"].get(index, "") + value
            else:
                current[field] = current.get(field, "") + value
            continue

        raise ValueError(f"{path}:{line_number}: unsupported PO syntax: {line!r}")

    return entries


def _read_linguas() -> list[str]:
    if not LINGUAS_FILE.is_file():
        raise ValueError("locale/LINGUAS is missing")
    languages: list[str] = []
    for raw in LINGUAS_FILE.read_text(encoding="utf-8").splitlines():
        value = raw.split("#", 1)[0].strip()
        if value:
            languages.extend(value.split())
    if not languages:
        raise ValueError("locale/LINGUAS is empty")
    if len(languages) != len(set(languages)):
        raise ValueError("locale/LINGUAS contains duplicate entries")
    return languages


def _header(entry: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in entry["str"].get(0, "").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip()
    return values


def _signature(text: str) -> Counter[str]:
    tokens: list[str] = []
    # Shell prompts currently parse the literal ASCII answers shown by these
    # markers.  Translating ``(y/N)`` to a locale-specific abbreviation would
    # advertise an input the scripts do not accept, so treat the marker as a
    # protocol token rather than translatable prose.
    tokens.extend(re.findall(r"\([yY]/[nN]\)", text))
    tokens.extend(
        re.findall(
            r"%(?:\([^)]+\))?[#0+\- ]*(?:\d+|\*)?(?:\.\d+|\.\*)?[hlL]?[diouxXeEfFgGcrsa%]",
            text,
        )
    )
    # Do not count the braced portion of ``${name}`` twice: shell variables
    # are collected by the expression below as one indivisible token.
    tokens.extend("{" + token + "}" for token in re.findall(r"(?<![$\{])\{([^{}]*)\}(?!\})", text))
    tokens.extend(re.findall(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*", text))
    # Angle-bracket command metavariables (for example ``<IP address>``) are
    # translatable prose, not markup.  Preserve only the inline markup tags
    # that the UI can actually render.
    tokens.extend(
        re.findall(
            r"</?(?:a|b|big|br|code|em|i|p|small|span|strong|tt|u)(?:\s+[^>]*)?/?>",
            text,
            flags=re.IGNORECASE,
        )
    )
    return Counter(tokens)


def _plural_count(headers: dict[str, str]) -> int | None:
    match = re.search(r"\bnplurals\s*=\s*(\d+)\b", headers.get("Plural-Forms", ""))
    return int(match.group(1)) if match else None


def _catalog_fingerprint(entries: list[dict[str, Any]]) -> str:
    payload = [
        (
            entry.get("ctx"),
            entry.get("id"),
            entry.get("plural"),
            sorted(entry["str"].items()),
        )
        for entry in entries
        if entry.get("id")
    ]
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _expected_mo_catalog(entries: list[dict[str, Any]]) -> dict[Any, str]:
    expected: dict[Any, str] = {}
    for entry in entries:
        message_id = entry.get("id")
        if not message_id:
            continue
        context = entry.get("ctx")
        key_base = f"{context}\x04{message_id}" if context else message_id
        if entry.get("plural") is None:
            expected[key_base] = entry["str"].get(0, "")
        else:
            for index, translated in entry["str"].items():
                expected[(key_base, index)] = translated
    return expected


def _validate_checked_in_mo(language: str, entries: list[dict[str, Any]], errors: list[str]) -> None:
    path = ROOT / "usr" / "share" / "locale" / language / "LC_MESSAGES" / "big-remote-play.mo"
    if not path.is_file():
        errors.append(f"{language}: checked-in runtime catalog is missing")
        return
    try:
        with path.open("rb") as stream:
            translation = gettext.GNUTranslations(stream)
    except (OSError, EOFError, UnicodeError, struct.error) as error:
        errors.append(f"{language}: invalid checked-in MO catalog: {error}")
        return

    actual = {
        key: value
        for key, value in translation._catalog.items()  # type: ignore[attr-defined]
        if key != ""
    }
    expected = _expected_mo_catalog(entries)
    if actual != expected:
        missing = len(expected.keys() - actual.keys())
        extra = len(actual.keys() - expected.keys())
        changed = sum(1 for key in expected.keys() & actual.keys() if expected[key] != actual[key])
        errors.append(f"{language}: checked-in MO differs from PO (missing={missing}, extra={extra}, changed={changed})")


def main() -> int:
    errors: list[str] = []

    try:
        languages = _read_linguas()
    except ValueError as error:
        print(f"TRANSLATION VALIDATION FAILED\n- {error}", file=sys.stderr)
        return 1

    expected_languages = set(EXPECTED_PLURAL_COUNTS)
    if set(languages) != expected_languages:
        missing = sorted(expected_languages - set(languages))
        extra = sorted(set(languages) - expected_languages)
        if missing:
            errors.append("LINGUAS missing: " + ", ".join(missing))
        if extra:
            errors.append("LINGUAS has unsupported entries: " + ", ".join(extra))

    actual_po = {path.stem for path in LOCALE_DIR.glob("*.po")}
    if actual_po != set(languages):
        missing = sorted(set(languages) - actual_po)
        extra = sorted(actual_po - set(languages))
        if missing:
            errors.append("catalogs missing: " + ", ".join(missing))
        if extra:
            errors.append("catalogs not listed in LINGUAS: " + ", ".join(extra))

    json_sidecars = sorted(path.name for path in LOCALE_DIR.glob("*.json"))
    if json_sidecars:
        errors.append("generated locale JSON sidecars remain: " + ", ".join(json_sidecars))

    if not TEMPLATE.is_file():
        errors.append("translation template is missing: locale/big-remote-play.pot")
        template_entries: list[dict[str, Any]] = []
    else:
        try:
            template_entries = _parse_po(TEMPLATE)
        except (SyntaxError, ValueError) as error:
            errors.append(str(error))
            template_entries = []

    template_messages = [entry for entry in template_entries if entry.get("id")]
    template_keys = {(entry.get("ctx"), entry.get("id"), entry.get("plural")) for entry in template_messages}
    if not template_keys:
        errors.append("translation template contains no messages")
    if len(template_keys) != len(template_messages):
        errors.append("translation template contains duplicate message keys")

    fingerprints: dict[str, str] = {}

    msgfmt = shutil.which("msgfmt")
    with tempfile.TemporaryDirectory(prefix="big-remote-play-i18n-") as tmp:
        temporary_dir = Path(tmp)
        for language in languages:
            path = LOCALE_DIR / f"{language}.po"
            if not path.is_file():
                continue

            try:
                entries = _parse_po(path)
            except (SyntaxError, ValueError) as error:
                errors.append(str(error))
                continue

            message_entries = [entry for entry in entries if entry.get("id")]
            keys = {(entry.get("ctx"), entry.get("id"), entry.get("plural")) for entry in message_entries}
            if len(keys) != len(message_entries):
                errors.append(f"{language}: catalog contains duplicate message keys")
            if keys != template_keys:
                errors.append(f"{language}: message-key set differs from template (missing={len(template_keys - keys)}, extra={len(keys - template_keys)})")

            header_entries = [entry for entry in entries if entry.get("id") == ""]
            if len(header_entries) != 1:
                errors.append(f"{language}: expected one catalog header, found {len(header_entries)}")
                headers: dict[str, str] = {}
            else:
                headers = _header(header_entries[0])

            if headers.get("Language") != language:
                errors.append(f"{language}: Language header is {headers.get('Language')!r}")
            content_type = headers.get("Content-Type", "").lower().replace(" ", "")
            if "charset=utf-8" not in content_type:
                errors.append(f"{language}: Content-Type is not UTF-8")

            count = _plural_count(headers)
            expected_count = EXPECTED_PLURAL_COUNTS.get(language)
            if count != expected_count:
                errors.append(f"{language}: nplurals is {count!r}; expected {expected_count}")

            for entry in message_entries:
                message_id = entry["id"]
                flags = entry["flags"]
                if "fuzzy" in flags:
                    errors.append(f"{language}: fuzzy entry: {message_id[:90]!r}")

                translations = entry["str"]
                required_slots = set(range(expected_count or 1)) if entry.get("plural") is not None else {0}
                if set(translations) != required_slots:
                    errors.append(f"{language}: wrong translation slots for {message_id[:90]!r}; found={sorted(translations)}, expected={sorted(required_slots)}")
                    continue

                for index, translated in translations.items():
                    if not translated:
                        errors.append(f"{language}: untranslated entry: {message_id[:90]!r} form {index}")
                        continue
                    source = message_id if index == 0 or entry.get("plural") is None else entry["plural"]
                    if _signature(source) != _signature(translated):
                        errors.append(f"{language}: placeholder mismatch in {message_id[:90]!r} form {index}")

            fingerprints[language] = _catalog_fingerprint(entries)
            _validate_checked_in_mo(language, entries, errors)

            if msgfmt:
                output = temporary_dir / language / "LC_MESSAGES" / "big-remote-play.mo"
                output.parent.mkdir(parents=True, exist_ok=True)
                completed = subprocess.run(
                    [
                        msgfmt,
                        "--check",
                        "--check-format",
                        "-o",
                        str(output),
                        str(path),
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                if completed.returncode:
                    errors.append(f"{language}: msgfmt failed: {completed.stderr.strip() or completed.stdout.strip()}")
                elif not output.is_file() or output.stat().st_size == 0:
                    errors.append(f"{language}: msgfmt produced no catalog")

    for first, second in (("pt", "pt_BR"), ("zh_CN", "zh_TW")):
        if fingerprints.get(first) == fingerprints.get(second):
            errors.append(f"{first} and {second} have identical translations")

    if "zh_TW" in fingerprints and not any(term in fingerprints["zh_TW"] for term in ("軟體", "設定", "連線", "應用程式")):
        errors.append("zh_TW lacks expected Traditional Chinese terminology")

    if "pt" in fingerprints and not any(term in fingerprints["pt"] for term in ("utilizador", "ficheiro", "ecrã", "definições", "ligação")):
        errors.append("pt lacks expected European Portuguese terminology")

    if "pt_BR" in fingerprints and not any(term in fingerprints["pt_BR"] for term in ("usuário", "arquivo", "tela", "configurações", "conexão")):
        errors.append("pt_BR lacks expected Brazilian Portuguese terminology")

    if errors:
        print("TRANSLATION VALIDATION FAILED", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    suffix = " with msgfmt" if msgfmt else " (msgfmt not available; structural checks only)"
    print(f"OK: {len(languages)} catalogs match the template, preserve placeholders, match their runtime MOs, and pass regional checks{suffix}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
