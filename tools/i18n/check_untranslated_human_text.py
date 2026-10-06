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
    "Steam Remote Play",
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
    # ZeroTier's own names for its two management consoles.
    "Legacy Central",
    "New Central",
    # Cloudflare's own field name, kept as its dashboard shows it.
    "Proxy status",
    # A command and a product pair shown as technical details.
    "journalctl -u headscale",
    "HTTPS (Caddy)",
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
# Unit symbols shared by every catalog, as in the video presets above.
UNITS = {"FPS", "Hz", "kbps", "Mbps", "ms"}
# Reviewed translations that are spelled exactly like the English source.
SAME_AS_SOURCE = {
    "da": {"Software (CPU)", "Start ZeroTier", "Variation (jitter): {ms} ms"},
    "de": {"Software (CPU)", "In Cloudflare: DNS → Records → Add record.", "Optional: IPv6"},
    "es": {"Software (CPU)", "Vulkan (experimental)"},
    "it": {"Software (CPU)", "In Cloudflare: DNS → Records → Add record."},
    "nl": {"Software (CPU)", "In Cloudflare: DNS → Records → Add record."},
    "no": {"Start ZeroTier"},
    "pt": {"Software (CPU)", "Vulkan (experimental)"},
    "pt_BR": {"Software (CPU)", "Vulkan (experimental)"},
    "ro": {"Software (CPU)", "Vulkan (experimental)"},
    "sv": {"Variation (jitter): {ms} ms"},
}
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
    if all(word in UNITS for word in words):
        return False
    if len(words) < 2 or len(visible.strip()) < 8:
        return False
    if text.startswith(("BRP_DATA", "BRP_PHASE")):
        return False
    if text.startswith(("/", "--")):
        return False
    return True


def should_check_catalog(path: Path) -> bool:
    return path.stem != SOURCE_LANGUAGE


def findings(root: Path) -> list[tuple[str, str]]:
    failures = []
    for po in sorted((root / "locale").glob("*.po")):
        # English intentionally mirrors msgid. Treating it as untranslated
        # makes every valid source-language sentence a false positive.
        if not should_check_catalog(po):
            continue
        for item in entries(po):
            if item["msgid"] in SAME_AS_SOURCE.get(po.stem, ()):
                continue
            if human(item["msgid"]) and item.get("msgstr", "") == item["msgid"]:
                failures.append((po.name, item["msgid"]))
    return failures


def main() -> int:
    failures = findings(Path(__file__).resolve().parents[2])
    for name, msgid in failures:
        print(f"{name}: untranslated human text: {msgid!r}")
    print(f"checked catalogs; findings={len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
