"""Sound on the connecting computer: Moonlight's messages and its playback stream.

Messages are Moonlight 6.1's own (moonlight-common-c, Moonlight Qt). The sound
server is the fake pipewire-pulse of ``test_audio``; nothing real is touched.
"""

from __future__ import annotations

import io
import logging

from big_remote_play.guest import moonlight_audio
from big_remote_play.guest.moonlight_audio import MoonlightAudioReport, check_playback, classify_line, moonlight_playback
from big_remote_play.utils.audio import AudioManager

from test_audio import HDMI, FakePulse, Stream  # noqa: E402  (the fake sound server)

MOONLIGHT = {"application.name": "Moonlight", "application.process.binary": "moonlight", "media.name": "Playback"}


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_moonlight_sound_messages_are_classified():
    assert classify_line("Received first audio packet after 31 ms") == moonlight_audio.RECEIVED
    assert classify_line("Failed to open audio device. Audio will be unavailable during this session.") == moonlight_audio.DEVICE_FAILED
    assert classify_line("No audio traffic was ever received from the host!") == moonlight_audio.NO_TRAFFIC
    assert classify_line("Audio packet queue overflow") == moonlight_audio.LOSS
    assert classify_line("Network dropped audio data (expected 1201, but received 1240)") == moonlight_audio.LOSS
    assert classify_line("Starting video stream") is None


def test_bursts_of_loss_messages_are_logged_once_per_interval_and_told_once(caplog):
    clock = Clock()
    report = MoonlightAudioReport(clock=clock)
    kept = []
    with caplog.at_level(logging.WARNING, logger="big-remoteplay"):
        for _ in range(80):
            kept.append(report.note("Audio packet queue overflow")[1])
            clock.sleep(0.1)
        clock.sleep(30)
        kept.append(report.note("Audio packet queue overflow")[1])
    assert kept.count(True) == 1  # only the first line itself
    assert len([r for r in caplog.records if "audio loss messages" in r.getMessage()]) == 1
    assert report.take_loss_alert() is True and report.take_loss_alert() is False


def test_a_few_loss_messages_are_not_told_to_the_person():
    report = MoonlightAudioReport(clock=Clock())
    for _ in range(5):
        report.note("Network dropped audio data (expected 1, but received 3)")
    assert report.take_loss_alert() is False


def test_moonlight_playback_is_found_by_binary_name_or_flatpak_id():
    pulse = FakePulse()
    pulse.playback = [
        Stream(HDMI, MOONLIGHT),
        Stream(HDMI, {"application.name": "Moonlight", "application.id": "com.moonlight_stream.Moonlight"}),
        Stream(HDMI, {"application.name": "Firefox", "application.process.binary": "firefox"}),
    ]
    found = moonlight_playback(AudioManager(runner=pulse).snapshot())
    assert [s.index for s in found] == ["500", "501"]


def test_a_muted_moonlight_stream_is_unmuted_and_its_volume_left_alone():
    pulse = FakePulse()
    pulse.playback = [Stream(HDMI, MOONLIGHT, muted=True, volume=30000), Stream(HDMI, {"application.name": "Music", "application.process.binary": "player"}, muted=True)]
    clock = Clock()
    result = check_playback(AudioManager(runner=pulse), MoonlightAudioReport(clock=clock), sleep=clock.sleep, clock=clock)
    assert result == moonlight_audio.SOUND_UNMUTED
    assert pulse.writes == [["set-sink-input-mute", "500", "0"]]
    assert pulse.playback[0].volume == 30000 and pulse.playback[1].muted is True


def test_a_playing_moonlight_stream_changes_nothing():
    pulse = FakePulse()
    pulse.playback = [Stream(HDMI, MOONLIGHT)]
    clock = Clock()
    assert check_playback(AudioManager(runner=pulse), MoonlightAudioReport(clock=clock), sleep=clock.sleep, clock=clock) == moonlight_audio.SOUND_PLAYING
    assert pulse.writes == []


def test_the_check_waits_for_the_stream_and_reports_when_it_never_comes():
    pulse = FakePulse()
    clock = Clock()
    result = check_playback(AudioManager(runner=pulse), MoonlightAudioReport(clock=clock), timeout=10, sleep=clock.sleep, clock=clock)
    assert result == moonlight_audio.SOUND_NOT_PLAYING and clock.now >= 110


def test_a_device_moonlight_could_not_open_is_reported_without_writes():
    pulse = FakePulse()
    report = MoonlightAudioReport(clock=Clock())
    report.note("Failed to open audio device: Couldn't open audio device")
    assert check_playback(AudioManager(runner=pulse), report) == moonlight_audio.SOUND_DEVICE_FAILED
    assert pulse.writes == []


def test_the_check_stops_when_the_connection_ends():
    pulse = FakePulse()
    assert check_playback(AudioManager(runner=pulse), MoonlightAudioReport(), alive=lambda: False) == moonlight_audio.SOUND_UNKNOWN


def test_no_sound_server_is_unknown_not_an_error():
    pulse = FakePulse()
    pulse.fail_list = True
    assert check_playback(AudioManager(runner=pulse), MoonlightAudioReport()) == moonlight_audio.SOUND_UNKNOWN


def test_the_client_counts_moonlight_sound_messages_and_collapses_loss_lines(monkeypatch):
    from big_remote_play.guest import moonlight_client

    output = "Initializing audio stream...\nReceived first audio packet after 30 ms\n" + "Audio packet queue overflow\n" * 20 + "Starting video stream\n"

    class Process:
        stdout = io.StringIO(output)

        def wait(self, timeout=None):
            raise moonlight_client.subprocess.TimeoutExpired("moonlight", timeout)

        def poll(self):
            return None

    logged = []

    class Logger:
        def info(self, message):
            logged.append(message)

        error = info

    monkeypatch.setattr(moonlight_client.subprocess, "Popen", lambda *a, **k: Process())
    client = moonlight_client.MoonlightClient(logger=Logger())
    client.moonlight_cmd = "moonlight"
    assert client.connect("192.0.2.10")
    assert client.wait_for_stream(2)
    assert client.audio.seen(moonlight_audio.RECEIVED)
    assert client.audio.counts[moonlight_audio.LOSS] == 20
    assert len([line for line in logged if "queue overflow" in line]) == 1
