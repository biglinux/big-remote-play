"""Guards for external contracts verified against current upstream docs."""

from pathlib import Path

from big_remote_play.integration_contracts import (
    MOONLIGHT_BITRATE_KBPS_RANGE,
    MOONLIGHT_DISPLAY_MODES,
    MOONLIGHT_FPS_RANGE,
    MOONLIGHT_PAIRING_PIN_LENGTH,
    MOONLIGHT_VIDEO_CODECS,
    MOONLIGHT_VIDEO_DECODERS,
    SUNSHINE_ADDRESS_FAMILIES,
    SUNSHINE_CAPTURE_BACKENDS_LINUX,
    SUNSHINE_DEFAULT_BASE_PORT,
    SUNSHINE_ENCODERS_LINUX,
    SUNSHINE_SERVICE_UNITS,
    sunshine_web_ui_port,
)

ROOT = Path(__file__).resolve().parents[1]


def test_moonlight_current_cli_contract() -> None:
    assert MOONLIGHT_PAIRING_PIN_LENGTH == 4
    assert MOONLIGHT_FPS_RANGE == (10, 480)
    assert MOONLIGHT_BITRATE_KBPS_RANGE == (500, 500_000)
    assert MOONLIGHT_DISPLAY_MODES == ("fullscreen", "borderless", "windowed")
    assert MOONLIGHT_VIDEO_DECODERS == ("auto", "hardware", "software")
    assert MOONLIGHT_VIDEO_CODECS == ("auto", "H.264", "HEVC", "AV1")


def test_sunshine_current_configuration_contract() -> None:
    assert sunshine_web_ui_port(SUNSHINE_DEFAULT_BASE_PORT) == 47_990
    assert SUNSHINE_ADDRESS_FAMILIES == ("ipv4", "both")
    assert "portal" in SUNSHINE_CAPTURE_BACKENDS_LINUX
    assert "kwin" in SUNSHINE_CAPTURE_BACKENDS_LINUX
    assert SUNSHINE_ENCODERS_LINUX == ("", "nvenc", "vaapi", "vulkan", "software")
    assert "app-dev.lizardbyte.app.Sunshine.service" in SUNSHINE_SERVICE_UNITS


def test_no_stale_six_digit_pairing_copy_or_validation() -> None:
    stale = []
    for path in [*ROOT.joinpath("src").rglob("*.py"), *ROOT.joinpath("docs").rglob("*.md")]:
        text = path.read_text(encoding="utf-8")
        if "6-digit PIN" in text or "6 digit PIN" in text:
            stale.append(str(path.relative_to(ROOT)))
    assert not stale, stale


def test_sunshine_safe_ui_defaults_are_not_pre_enabled() -> None:
    source = (ROOT / "src/big_remote_play/ui/host_view.py").read_text(encoding="utf-8")
    assert "self.upnp_row.set_active(False)" in source
    assert "self.ipv6_row.set_active(False)" in source
