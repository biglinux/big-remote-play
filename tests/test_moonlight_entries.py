"""Moonlight entries besides Desktop: Desktop Low, Steam Big Picture, one per screen.

Asked for on a real three-monitor PC: "Desktop Low" (low bandwidth), Steam in
Big Picture and "01 - Monitor", "02 - Monitor", "03 - Monitor". Stock Sunshine
2026.914 captures one screen and caps the bitrate for the whole server, so the
screen and bitrate entries are offered only when Sunshine reads them per app.
"""

from __future__ import annotations

import json
from pathlib import Path

from big_remote_play.host.game_library import FILE, SERVER, GameLibrary
from big_remote_play.host.moonlight_entries import (
    DEFAULT_SETTINGS,
    MARKER,
    Screen,
    monitor_entry_name,
    plan_entries,
    settings_from,
    stream_display_prep,
    sunshine_supports_app_overrides,
    wanted_entries,
)
from tests.test_game_library import FakeSunshine

SCREENS = [Screen("DP-1", "LG ULTRAWIDE"), Screen("DP-2", "LG HDR WFHD"), Screen("HDMI-A-1", "LG ULTRAWIDE")]
HELPER = '[{"do":"\\"/usr/bin/python3\\" -I \\"/usr/lib/big-remote-play/big_remote_play/host/stream_display.py\\" prepare --output DP-1 --sdr","undo":"\\"/usr/bin/python3\\" -I \\"/usr/lib/big-remote-play/big_remote_play/host/stream_display.py\\" restore","elevated":false}]'


def _names(entries):
    return [entry["name"] for entry in entries]


def test_everything_asked_for_with_a_capable_sunshine():
    entries = wanted_entries(DEFAULT_SETTINGS, supported=True, steam_launcher=("steam",), screens=SCREENS, global_prep_cmd=HELPER)
    assert _names(entries) == ["Desktop Low", "Steam Big Picture", "01 - Monitor", "02 - Monitor", "03 - Monitor"]
    low, steam, *monitors = entries
    assert low["max-bitrate"] == 3000 and "output-name" not in low
    assert steam["detached"] == ["setsid steam steam://open/bigpicture"]
    assert steam["prep-cmd"][0]["undo"] == "setsid steam steam://close/bigpicture"
    assert [m["output-name"] for m in monitors] == ["DP-1", "DP-2", "HDMI-A-1"]
    # Each screen's entry prepares its own screen, not the shared one.
    second = monitors[1]
    assert second["exclude-global-prep-cmd"] is True
    assert "--output DP-2 --sdr" in second["prep-cmd"][0]["do"] and "DP-1" not in second["prep-cmd"][0]["do"]
    assert all(entry[MARKER] for entry in entries)


def test_stock_sunshine_gets_no_entry_that_would_not_work():
    entries = wanted_entries(DEFAULT_SETTINGS, supported=False, steam_launcher=("flatpak", "run", "com.valvesoftware.Steam"), screens=SCREENS)
    assert _names(entries) == ["Steam Big Picture"]
    assert entries[0]["detached"] == ["setsid flatpak run com.valvesoftware.Steam steam://open/bigpicture"]


def test_one_screen_needs_no_screen_entry_and_choices_are_kept():
    assert "01 - Monitor" not in _names(wanted_entries(DEFAULT_SETTINGS, supported=True, steam_launcher=None, screens=SCREENS[:1]))
    chosen = {"desktop_low": False, "steam_big_picture": True, "monitors": True, "low_kbps": 1500}
    assert _names(wanted_entries(chosen, supported=True, steam_launcher=None, screens=SCREENS)) == ["01 - Monitor", "02 - Monitor", "03 - Monitor"]
    assert wanted_entries({"low_kbps": 1500}, supported=True, steam_launcher=None, screens=[])[0]["max-bitrate"] == 1500


def test_malformed_settings_fall_back_to_defaults():
    assert settings_from(None) == DEFAULT_SETTINGS
    assert settings_from({"desktop_low": "yes", "low_kbps": 999999, "monitors": False})["low_kbps"] == 3000
    assert settings_from({"monitors": False})["monitors"] is False


def test_unsafe_connectors_are_never_written():
    assert _names(wanted_entries(DEFAULT_SETTINGS, supported=True, steam_launcher=None, screens=[Screen("DP-1", "a"), Screen("x;rm", "b")])) == ["Desktop Low", "01 - Monitor"]


def test_other_global_commands_are_kept_and_an_unreadable_value_is_not_rewritten():
    other = json.dumps([{"do": "notify-send hi", "undo": "", "elevated": False}, json.loads(HELPER)[0]])
    commands, ok = stream_display_prep(other, "HDMI-A-1")
    assert ok and commands[0]["do"] == "notify-send hi" and "--output HDMI-A-1" in commands[1]["do"]
    assert stream_display_prep("not json", "DP-2") == ([], False)
    entry = wanted_entries(DEFAULT_SETTINGS, supported=True, steam_launcher=None, screens=SCREENS, global_prep_cmd="not json")[1]
    assert "prep-cmd" not in entry and "exclude-global-prep-cmd" not in entry


def test_the_numbers_match_identify_screens():
    assert monitor_entry_name(1) == "01 - Monitor" and monitor_entry_name(12) == "12 - Monitor"


def test_feature_detection_reads_the_executable(tmp_path: Path):
    patched = tmp_path / "sunshine-patched"
    patched.write_bytes(b"\x7fELF" + b"\x00" * (3 << 20) + b"\x00output-name\x00junk\x00max-bitrate\x00")
    stock = tmp_path / "sunshine"
    stock.write_bytes(b"\x7fELF\x00output\x00max_bitrate\x00")
    assert sunshine_supports_app_overrides(patched)
    assert not sunshine_supports_app_overrides(stock)
    assert not sunshine_supports_app_overrides(tmp_path / "missing") and not sunshine_supports_app_overrides(None)


def test_a_persons_own_app_is_never_changed_or_duplicated():
    current = [{"name": "Desktop"}, {"name": "Steam Big Picture", "cmd": "mine"}]
    wanted = wanted_entries(DEFAULT_SETTINGS, supported=True, steam_launcher=("steam",), screens=[])
    plan = plan_entries(current, wanted)
    assert plan.skipped == ("Steam Big Picture",) and _names(plan.add) == ["Desktop Low"] and plan.remove == ()


def test_sync_through_the_file_then_off_again(tmp_path):
    sunshine = FakeSunshine(tmp_path)
    library = GameLibrary(sunshine, lambda: None)
    wanted = wanted_entries(DEFAULT_SETTINGS, supported=True, steam_launcher=("steam",), screens=SCREENS)
    change, skipped = library.sync_entries(wanted)
    assert (change.ok, change.count, change.source, skipped) == (True, 5, FILE, ())
    data = json.loads(sunshine.file.read_text())
    assert data["env"] == {"PATH": "$(PATH):/opt"} and data["apps"][1]["name"] == "My Emulator"
    # Unchanged settings: nothing is rewritten.
    assert library.sync_entries(wanted)[0].count == 0
    # A lower limit replaces only Desktop Low.
    lower = wanted_entries({**DEFAULT_SETTINGS, "low_kbps": 1500}, supported=True, steam_launcher=("steam",), screens=SCREENS)
    assert library.sync_entries(lower)[0].count == 2
    assert next(app for app in json.loads(sunshine.file.read_text())["apps"] if app["name"] == "Desktop Low")["max-bitrate"] == 1500
    # Everything off: ours go, the person's apps stay.
    assert library.sync_entries([])[0].ok
    assert [app["name"] for app in json.loads(sunshine.file.read_text())["apps"]] == ["Desktop", "My Emulator"]


def test_sync_while_sharing_uses_sunshine_and_finds_moved_positions(tmp_path):
    sunshine = FakeSunshine(tmp_path, running=True)
    library = GameLibrary(sunshine, lambda: ("u", "p"))
    wanted = wanted_entries(DEFAULT_SETTINGS, supported=True, steam_launcher=None, screens=SCREENS)
    change, _skipped = library.sync_entries(wanted)
    assert (change.ok, change.source, len(sunshine.api_adds)) == (True, SERVER, 4)
    change, _skipped = library.sync_entries(wanted[:1])
    assert change.ok and len(sunshine.api_deletes) == 3
    assert [app["name"] for app in sunshine.read_apps_file()] == ["Desktop", "My Emulator", "Desktop Low"]
