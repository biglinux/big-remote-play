#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import ast, re

FIELD = re.compile(r'^(msgid|msgstr)\s+(".*")\s*$')
QUOTED = re.compile(r'^".*"$')
TECHNICAL = {
    # Product/protocol labels whose official spelling is shared by locales.
    "Tailscale",
    "Headscale",
    "ZeroTier",
    "Sunshine",
    "Moonlight",
    "Big Remote Play",
    "AMD AMF",
    "Docker Engine",
    "Intel QuickSync",
    "NVIDIA NVENC",
    "OK",
    "Spatial AQ",
    "V-Sync",
    "VA-API",
    "VPN",
    "VideoToolbox",
    "4K",
}

PLACEHOLDER = re.compile(
    r"\$\{[A-Za-z_][A-Za-z0-9_]*\}"
    r"|\$[A-Za-z_][A-Za-z0-9_]*"
    r"|(?<!\{)\{[^{}]*\}(?!\})"
    r"|%(?:\([^)]+\))?[#0+\- ]*(?:\d+|\*)?(?:\.\d+|\.\*)?[hlL]?[diouxXeEfFgGcrsa%]"
    r"|</?[A-Za-z][^>]*>"
)
URL = re.compile(r"https?://\S+")
VIDEO_PRESET = re.compile(r"^(?:\d{3,4}p|4K)(?:\s*·\s*\d+\s*FPS\s*·\s*\d+\s*Mbps)?$")
SOURCE_LANGUAGE = "en"


def uq(value: str) -> str:
    try:
        return ast.literal_eval(value)
    except Exception:
        return ""


def entries(path: Path):
    current = None
    item = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines() + [""]:
        match = FIELD.match(line)
        if match:
            current = match.group(1)
            item[current] = uq(match.group(2))
            continue
        if current and QUOTED.match(line):
            item[current] = item.get(current, "") + uq(line)
            continue
        if not line.strip():
            if item.get("msgid"):
                yield item
            current = None
            item = {}
        elif not line.startswith("#"):
            current = None


def human(text: str) -> bool:
    if text in TECHNICAL or VIDEO_PRESET.fullmatch(text):
        return False
    # Placeholder names such as ``{resolution}`` are implementation tokens,
    # not user-facing English.  Strip them before deciding whether a string
    # contains natural language that should have been translated.
    visible = URL.sub(" ", PLACEHOLDER.sub(" ", text))
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ']+", visible)
    if len(words) < 2 or len(visible.strip()) < 8:
        return False
    if text.startswith(("BRP_DATA", "BRP_PHASE")):
        return False
    if text.startswith(("/", "--")):
        return False
    return True


def should_check_catalog(path: Path) -> bool:
    return path.stem != SOURCE_LANGUAGE


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    failures = []
    for po in sorted((root / "locale").glob("*.po")):
        # English intentionally mirrors msgid. Treating it as untranslated
        # makes every valid source-language sentence a false positive.
        if not should_check_catalog(po):
            continue
        for item in entries(po):
            if human(item["msgid"]) and item.get("msgstr", "") == item["msgid"]:
                failures.append((po.name, item["msgid"]))
    for name, msgid in failures:
        print(f"{name}: untranslated human text: {msgid!r}")
    print(f"checked catalogs; findings={len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
