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


def test_cleanup_leaves_a_default_the_app_never_changed(monkeypatch) -> None:
    """Closing the app must not move the user's sound to another device.

    cleanup() used to set "the first hardware sink" as default unconditionally,
    so a user whose output was the HDMI card found it back on the analog one
    every time the window closed.
    """
    manager = AudioManager()
    moved: list[str] = []
    monkeypatch.setattr(manager, "get_default_sink", lambda: "alsa_output.hdmi-stereo")
    monkeypatch.setattr(manager, "get_passive_sinks", lambda: [{"name": "alsa_output.analog-stereo"}])
    monkeypatch.setattr(manager, "set_default_sink", lambda sink: moved.append(sink))
    monkeypatch.setattr(manager, "get_apps", list)
    monkeypatch.setattr("big_remote_play.utils.audio.subprocess.run", lambda *a, **k: None)

    manager.cleanup()

    assert moved == []


def test_cleanup_restores_the_default_the_app_took_over(monkeypatch) -> None:
    manager = AudioManager()
    moved: list[str] = []
    manager._user_default_sink = "alsa_output.hdmi-stereo"
    monkeypatch.setattr(manager, "get_default_sink", lambda: "SunshineGameSink")
    monkeypatch.setattr(manager, "set_default_sink", lambda sink: moved.append(sink))
    monkeypatch.setattr(manager, "get_apps", list)
    monkeypatch.setattr("big_remote_play.utils.audio.subprocess.run", lambda *a, **k: None)

    manager.cleanup()

    assert moved == ["alsa_output.hdmi-stereo"]


def test_cleanup_never_guesses_an_output_after_a_crash(monkeypatch) -> None:
    """Without ownership state, recovery must not redirect sound to a random device."""
    manager = AudioManager()
    moved: list[str] = []
    monkeypatch.setattr(manager, "get_default_sink", lambda: "SunshineGameSink")
    monkeypatch.setattr(manager, "get_passive_sinks", lambda: [{"name": "alsa_output.analog-stereo"}])
    monkeypatch.setattr(manager, "set_default_sink", lambda sink: moved.append(sink))
    monkeypatch.setattr(manager, "get_apps", list)
    monkeypatch.setattr("big_remote_play.utils.audio.subprocess.run", lambda *a, **k: None)

    manager.cleanup()

    assert moved == []
