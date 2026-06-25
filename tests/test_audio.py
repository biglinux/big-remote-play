"""AudioManager.is_virtual classification (pure, no subprocess)."""

import pytest

from big_remote_play.utils.audio import AudioManager


@pytest.mark.parametrize(
    "name,description,expected",
    [
        ("SunshineGameSink", "", True),
        ("alsa_output.pci-0000_00_1f.3.analog-stereo", "Built-in Audio", False),
        ("alsa_output.pci.analog-stereo.monitor", "", True),
        ("combined", "", True),
        ("null-sink", "", True),
        ("regular_sink", "easyeffects sink", True),
        ("hw_card", "USB Headset", False),
    ],
)
def test_is_virtual(name: str, description: str, expected: bool) -> None:
    assert AudioManager().is_virtual(name, description) is expected
