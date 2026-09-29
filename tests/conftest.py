"""Shared fixtures. Keep the filesystem hermetic: every test that touches config
or logs runs against a temporary HOME so nothing writes to the real user dir."""

import os

# Assertions compare the untranslated source strings, so no catalog may be
# selected. Set before big_remote_play.utils.i18n binds the domain at import;
# otherwise a developer running under pt_BR gets the translated text.
# LANGUAGE alone: it outranks LC_MESSAGES in gettext and leaves the process
# encoding as it is (LC_ALL=C would make read_text() decode sources as ASCII).
os.environ["LANGUAGE"] = "C"

# xvfb-run exports DISPLAY but leaves the caller's WAYLAND_DISPLAY in place, and
# GTK4 prefers Wayland when both exist: the UI tests then present their windows
# on the developer's own screen instead of the headless X server, flashing dozens
# of windows over whatever they were doing. Pin the run to the display it was
# given, before any gi import creates a GdkDisplay.
if os.environ.get("DISPLAY"):
    os.environ.pop("WAYLAND_DISPLAY", None)
    os.environ.setdefault("GDK_BACKEND", "x11")

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_user_config(tmp_path_factory, monkeypatch):
    """No test may read, migrate or delete the developer's real settings.

    paths.CONFIG_DIR is resolved lazily from XDG_CONFIG_HOME, so pointing it at
    a per-test directory covers every module, including legacy-file migrations
    that delete the source after copying it. Tests that need a particular
    layout still override HOME/XDG_CONFIG_HOME or paths.CONFIG_DIR themselves.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg-config")))
    # The UI must never reach a real VPN client from a test: the private-network
    # facade is replaced by an offline one unless a test installs its own.
    from big_remote_play.private_network import service

    monkeypatch.setattr(service, "_default_factory", lambda: service.OfflinePrivateNetworkService())
    # Nor the sound server: AudioManager sees no PulseAudio/PipeWire server
    # unless a test passes its own runner, and session state stays temporary.
    from big_remote_play.utils import audio

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path_factory.mktemp("runtime")))
    monkeypatch.setattr(audio.AudioManager, "__init__", _offline_audio_init(audio.AudioManager.__init__))
    monkeypatch.setattr(audio.AudioManager, "test_tone", lambda self, manual_output="": {"played": False, "detected": False, "level_db": None, "monitor": None, "output": ""})
    # Nor the display configuration: the stream-display helper finds no
    # kscreen-doctor unless a test passes its own tool and runner.
    from big_remote_play.host import stream_display

    monkeypatch.setattr(stream_display.StreamDisplay, "__init__", _offline_display_init(stream_display.StreamDisplay.__init__))
    # Nor a Sunshine that may be running on the developer machine.
    from big_remote_play.host.sunshine_manager import SunshineHost

    monkeypatch.setattr(SunshineHost, "running_app_id", lambda self, timeout=2.0: None)


def _offline_audio_init(original):
    def init(self, runner=None):
        original(self, runner if runner is not None else _no_sound_server)

    return init


def _offline_display_init(original):
    def init(self, *, runner=None, state=None, tool=None):
        kwargs = {"state": state, "tool": tool if tool is not None else ""}
        if runner is not None:
            kwargs["runner"] = runner
        original(self, **kwargs)

    return init


def _no_sound_server(*_args, **_kwargs):
    raise FileNotFoundError("pactl is not available in tests")


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Point HOME at a temp dir so Config/Logger write under tmp_path.

    XDG_CONFIG_HOME is cleared so the config dir always resolves under the fake
    HOME even when the host developer has it set."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return tmp_path
