"""Static integration checks derived from current official parsers."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOONLIGHT = ROOT / "src/big_remote_play/guest/moonlight_client.py"
GUEST = ROOT / "src/big_remote_play/ui/guest_view.py"
HOST = ROOT / "src/big_remote_play/ui/host_view.py"


def test_moonlight_uses_current_long_option_spelling() -> None:
    source = MOONLIGHT.read_text(encoding="utf-8")
    for obsolete in ('"-fps"', '"-bitrate"', '"-display-mode"', '"-video-decoder"', '"--width"', '"--height"'):
        assert obsolete not in source


def test_guest_does_not_offer_unsupported_receive_audio_toggle() -> None:
    source = GUEST.read_text(encoding="utf-8")
    assert "Receive audio streaming" not in source
    assert "Also play sound on the game PC" in source
    assert "play_audio_on_host" in source


def test_sunshine_capture_mapping_never_writes_wayland_literal() -> None:
    source = HOST.read_text(encoding="utf-8")
    assert '["auto", "wayland", "x11", "kms"]' not in source
    assert '{0: "auto", 1: "wayland", 2: "x11", 3: "kms"}' not in source
