"""The kernel name of a screen chosen in an X11 session.

Sunshine's KMS capture, the one it uses on X11 when it can, finds the screen
to share by its kernel connector name (``DP-2``, ``HDMI-A-1``). The X server
names the same outputs its own way: the amdgpu driver says ``DisplayPort-1``
and ``HDMI-A-0``, the NVIDIA driver ``DP-0``. Sunshine does not recognise
those names and shares the first screen instead. The EDID a screen sends
is the same on both sides, so it is what pairs the two names.
"""

from __future__ import annotations

import re
from pathlib import Path

DRM_ROOT = Path("/sys/class/drm")

_OUTPUT_LINE = re.compile(r"^(\S+) connected\b")
_HEX_LINE = re.compile(r"^\s+([0-9a-fA-F]+)\s*$")


def parse_xrandr_edids(text: str) -> dict[str, bytes]:
    """``xrandr --props`` → the EDID of each connected output."""
    edids: dict[str, bytes] = {}
    output = ""
    reading = False
    chunks: list[str] = []
    for line in text.splitlines():
        match = _OUTPUT_LINE.match(line)
        if match or (line and not line[0].isspace()):
            output, reading = (match.group(1) if match else ""), False
            continue
        if line.strip() == "EDID:":
            reading, chunks = bool(output), []
            continue
        if reading:
            hex_match = _HEX_LINE.match(line)
            if hex_match:
                chunks.append(hex_match.group(1))
                continue
            reading = False
            if chunks:
                try:
                    edids[output] = bytes.fromhex("".join(chunks))
                except ValueError:
                    pass
    if reading and chunks:
        try:
            edids[output] = bytes.fromhex("".join(chunks))
        except ValueError:
            pass
    return edids


def drm_connector_edids(root: Path = DRM_ROOT) -> dict[str, bytes]:
    """Connected kernel connectors (``DP-2``) and the EDID each one reads."""
    edids: dict[str, bytes] = {}
    for path in sorted(root.glob("card*-*")):
        try:
            if (path / "status").read_text().strip() != "connected":
                continue
            edid = (path / "edid").read_bytes()
        except OSError:
            continue
        if edid:
            edids[path.name.split("-", 1)[1]] = edid
    return edids


def _natural(name: str) -> list[object]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]


def kms_connector_names(x_edids: dict[str, bytes], drm_edids: dict[str, bytes]) -> dict[str, str]:
    """X output name → kernel connector name, only where the EDID tells.

    Two identical screens without a serial number send the same EDID; they
    are paired in connector order, which both drivers number the same way.
    A screen whose EDID matches nothing on the other side is left out.
    """

    def groups(edids: dict[str, bytes]) -> dict[bytes, list[str]]:
        grouped: dict[bytes, list[str]] = {}
        for name, edid in edids.items():
            grouped.setdefault(edid[:128], []).append(name)
        return {edid: sorted(names, key=_natural) for edid, names in grouped.items()}

    kernel = groups(drm_edids)
    names: dict[str, str] = {}
    for edid, x_names in groups(x_edids).items():
        drm_names = kernel.get(edid, [])
        if len(drm_names) == len(x_names):
            names.update(zip(x_names, drm_names, strict=True))
    return names
