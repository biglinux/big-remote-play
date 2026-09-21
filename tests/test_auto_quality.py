"""Rules that turn detected hardware into streaming settings.

No display and no network: every function under test is pure except the link
probe, which reads files the test writes itself.
"""

from __future__ import annotations

import pytest

from big_remote_play.utils import auto_quality


@pytest.mark.parametrize(
    "panel_height, expected",
    # A tie (900 sits exactly between 720 and 1080) resolves downwards: the
    # lower step always fits the link that the higher one might not.
    [(768, 720), (900, 720), (1080, 1080), (1200, 1080), (1440, 1440), (2160, 2160)],
)
def test_odd_panel_heights_snap_to_an_offered_step(panel_height, expected) -> None:
    assert auto_quality.nearest_height(panel_height) == expected


def test_automatic_encoder_skips_experimental_vulkan_for_software() -> None:
    gpus = [{"encoder": "vulkan"}, {"encoder": "software"}]
    assert auto_quality.encoder_index(gpus) == 1


def test_automatic_encoder_prefers_real_hardware() -> None:
    gpus = [{"encoder": "nvenc"}, {"encoder": "vaapi"}, {"encoder": "software"}]
    assert auto_quality.encoder_index(gpus) == 0


def test_high_refresh_needs_hardware_before_it_raises_the_frame_rate() -> None:
    assert auto_quality.frame_rate(144, hardware_encoder=True) == 120
    assert auto_quality.frame_rate(144, hardware_encoder=False) == 60
    assert auto_quality.frame_rate(50, hardware_encoder=True) == 30


def test_wireless_link_lowers_the_bitrate_and_high_frame_rate_raises_it() -> None:
    wired = auto_quality.bitrate_mbps(1080, 60, wireless=False)
    wireless = auto_quality.bitrate_mbps(1080, 60, wireless=True)
    fast = auto_quality.bitrate_mbps(1080, 120, wireless=False)
    assert wired == 20.0
    assert wireless < wired
    assert fast > wired


def test_software_encoder_does_not_advertise_efficient_codecs() -> None:
    defaults = auto_quality.host_defaults(gpus=[{"encoder": "software"}], refresh_hz=60, height=1080, wireless=False)
    assert defaults["efficient_codecs"] is False
    assert defaults["fps"] == 60
    assert defaults["wifi_mode"] is False


def test_guest_defaults_keep_the_panel_aspect_ratio() -> None:
    defaults = auto_quality.guest_defaults(width=3440, height=1440, refresh_hz=60, wireless=False)
    assert defaults["height"] == 1440
    assert defaults["width"] == 3440
    assert defaults["bitrate_mbps"] == 40.0


def test_guest_defaults_scale_an_unusual_panel_to_the_chosen_step() -> None:
    defaults = auto_quality.guest_defaults(width=1366, height=768, refresh_hz=60, wireless=False)
    assert (defaults["width"], defaults["height"]) == (1280, 720)


def test_signature_changes_when_any_detected_resource_changes() -> None:
    before = auto_quality.signature(0, 2, 1920, 1080, 60, False)
    assert before == auto_quality.signature(0, 2, 1920, 1080, 60, False)
    assert before != auto_quality.signature(0, 2, 1920, 1080, 60, True)
    assert before != auto_quality.signature(0, 2, 2560, 1440, 60, False)


def test_wireless_probe_reads_the_interface_of_the_default_route(tmp_path, monkeypatch) -> None:
    route = tmp_path / "route"
    route.write_text("Iface\tDestination\tGateway\nenp3s0\t00FEA8C0\t00000000\nwlan0\t00000000\t0102A8C0\n")
    net = tmp_path / "net"
    (net / "wlan0").mkdir(parents=True)
    (net / "wlan0" / "wireless").write_text("")
    monkeypatch.setattr(auto_quality, "_PROC_ROUTE", route)
    monkeypatch.setattr(auto_quality, "_SYS_CLASS_NET", net)
    assert auto_quality.wireless_link() is True


def test_wireless_probe_is_false_without_a_readable_route_table(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(auto_quality, "_PROC_ROUTE", tmp_path / "missing")
    assert auto_quality.wireless_link() is False
