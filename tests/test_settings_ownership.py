"""One owner per stored setting.

The sharing and connecting pages write `sunshine.conf` and `moonlight.conf`
whenever they save. Any option offered a second time in the advanced sheets was
therefore silently overwritten: the person changed it, then lost it on the next
save from the simple page. These tests keep the two sides disjoint.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "src/big_remote_play/ui/host_view.py"
GUEST = ROOT / "src/big_remote_play/ui/guest_view.py"
SUNSHINE_SHEET = ROOT / "src/big_remote_play/ui/sunshine_preferences.py"


def _sunshine_keys_written_by_the_sharing_page() -> set[str]:
    source = HOST.read_text()
    saved = source.split("sunshine_settings = {", 1)[1].split("}", 1)[0]
    presets = source.split("def _encoding_settings", 1)[1].split("def _build_sunshine_config", 1)[0]
    started = source.split("config = {", 1)[1].split("return config", 1)[0]
    return set(re.findall(r'"([a-z0-9_.]+)":', saved)) | set(re.findall(r'"([a-z0-9_]+)":\s*"', presets)) | set(re.findall(r'"([a-z0-9_.]+)":', started))


def _sunshine_keys_offered_by_the_advanced_sheet() -> set[str]:
    source = SUNSHINE_SHEET.read_text()
    inline = set(re.findall(r'^\s*\(\s*"([a-z0-9_.]+)"\s*,', source, re.M))
    wrapped = set(re.findall(r'^\s*"([a-z0-9_.]+)",\s*$', source, re.M))
    # Values of choice lists are not keys; only tuples that start an option are.
    return {key for key in inline | wrapped if key not in {"auto"}}


def _moonlight_keys(path: Path, pattern: str) -> set[str]:
    source = path.read_text()
    return set(re.findall(pattern, source))


def test_sunshine_settings_have_a_single_owner() -> None:
    shared = _sunshine_keys_written_by_the_sharing_page() & _sunshine_keys_offered_by_the_advanced_sheet()
    assert shared == set(), f"settings offered twice and overwritten by the sharing page: {sorted(shared)}"


def test_moonlight_settings_have_a_single_owner() -> None:
    source = GUEST.read_text()
    assert "moonlight_preferences" not in source
    assert not (ROOT / "src/big_remote_play/ui/moonlight_preferences.py").exists()
    # Every client setting is constructed in GuestView exactly once.
    keys = re.findall(r'_moonlight_(?:bool|combo)_row\(\s*"([A-Za-z0-9_]+)"', source)
    assert len(keys) == len(set(keys)), keys


def test_advanced_sheets_still_carry_their_own_settings() -> None:
    """Server expert settings remain available; client settings are task-specific."""
    assert len(_sunshine_keys_offered_by_the_advanced_sheet()) > 30
    source = GUEST.read_text()
    for dialog in ("image_dialog", "audio_dialog", "input_dialog", "host_connection_dialog"):
        assert f"self.{dialog} = preferences_dialog(" in source
