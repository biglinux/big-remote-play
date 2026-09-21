"""The Flatpak bundle must stay consistent with the code and the desktop files.

No YAML parser is used on purpose: the manifest is checked as the declarative
text it is, so the suite keeps running without an extra dependency.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "build-aux/flatpak/br.com.biglinux.remoteplay.yaml"
HOST_TOOL = ROOT / "build-aux/flatpak/host-tool"
APP_ID = "br.com.biglinux.remoteplay"


def _manifest() -> str:
    return MANIFEST.read_text(encoding="utf-8")


def _wrapped_tools() -> set[str]:
    block = re.search(r"for tool in (.+?); do", _manifest(), re.S)
    assert block, "the host-tool loop is missing from the manifest"
    return set(block.group(1).replace("\\\n", " ").split())


def test_manifest_targets_the_same_application_as_the_native_package() -> None:
    manifest = _manifest()

    assert f"id: {APP_ID}" in manifest
    assert "runtime: org.gnome.Platform" in manifest
    assert re.search(r"runtime-version: '\d+'", manifest), "the runtime must be pinned"
    assert "sdk: org.gnome.Sdk" in manifest
    assert "command: big-remote-play" in manifest
    assert (ROOT / f"usr/share/applications/{APP_ID}.desktop").is_file()
    assert f"<id>{APP_ID}</id>" in (ROOT / f"usr/share/metainfo/{APP_ID}.metainfo.xml").read_text(encoding="utf-8")


def test_relocation_and_the_host_bridge_are_declared() -> None:
    """Under /app the code finds nothing at /usr/share, and the host programs
    it drives live outside the sandbox."""
    manifest = _manifest()

    assert "--env=BIG_REMOTE_PLAY_DATADIR=/app/share/big-remote-play" in manifest
    assert "--env=BIG_REMOTE_PLAY_LOCALEDIR=/app/share/locale" in manifest
    assert "--env=PATH=/app/libexec/host-tools:" in manifest
    assert "--talk-name=org.freedesktop.Flatpak" in manifest
    assert "--talk-name=org.freedesktop.secrets" in manifest
    assert "flatpak-spawn --host" in HOST_TOOL.read_text(encoding="utf-8")


def test_every_probed_host_program_has_a_wrapper() -> None:
    """A program the code looks up with shutil.which is absent in the runtime:
    without a wrapper the Flatpak build silently reports it as not installed."""
    sources = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "src").rglob("*.py"))
    probed = set(re.findall(r'shutil\.which\("([^"]+)"\)', sources))

    assert probed, "no host program probes found — the regex no longer matches"
    assert probed <= _wrapped_tools(), f"missing host-tool wrappers: {sorted(probed - _wrapped_tools())}"
