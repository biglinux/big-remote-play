"""Game Window capture helper: validation, isolation, watchdog and cleanup.

No compositor, portal, GStreamer pipeline or Sunshine is started: the helper's
boundaries (D-Bus portal, KWin, X11, process groups) are replaced by fakes.
"""

from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import time

import pytest
from gi.repository import GLib

from big_remote_play.host import game_windows, window_capture as wc
from big_remote_play.host import x11_windows

UUID = "{11111111-2222-3333-4444-555555555555}"
SOCKET = "brp-game-0123456789ab"


def spec(**overrides) -> dict:
    data = {"version": 1, "backend": "kwin", "handle": UUID, "width": 1280, "height": 720, "scale": 1.5, "decoration": [1, 30, 1, 1], "identity": "steam:750920", "name": "Game"}
    data.update(overrides)
    return data


# ── spec validation ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "overrides",
    [
        {"version": 2},
        {"backend": "kms"},
        {"handle": "../../etc"},
        {"handle": "0x1234"},
        {"backend": "x11", "handle": "{bad}", "display": ":0"},
        {"backend": "x11", "handle": "0x400001", "display": ":0; rm -rf ~"},
        {"width": 10},
        {"height": 99999},
        {"scale": 9},
        {"decoration": [0, 9999, 0, 0]},
        {"width": "wide"},
    ],
)
def test_invalid_specs_are_refused(overrides):
    with pytest.raises(wc.CaptureError) as error:
        wc.Spec.from_mapping(spec(**overrides))
    assert error.value.reason == "invalid-spec"


def test_spec_round_trips_and_cleans_free_text():
    parsed = wc.Spec.from_mapping(spec(identity="not valid", name="Game\x1b[31m\nname"))
    assert parsed.identity == ""
    assert parsed.name == "Game[31mname"
    assert wc.Spec.from_mapping(parsed.to_mapping()) == parsed


def test_private_screen_matches_the_game_in_device_pixels():
    assert wc.screen_size(wc.Spec.from_mapping(spec(width=1280, height=720, scale=1.5))) == (1920, 1080)
    # Encoders need even sizes.
    assert wc.screen_size(wc.Spec.from_mapping(spec(width=1281, height=721, scale=1.0))) == (1280, 720)
    x11 = wc.Spec.from_mapping(spec(backend="x11", handle="0x400001", display=":0", scale=2.0, width=800, height=600))
    assert wc.screen_size(x11) == (800, 600)


# ── what is started, with which environment ──────────────────────────────────


def test_private_screen_is_an_explicit_argv_with_its_own_session_bus():
    argv = wc.compositor_argv(SOCKET, 1920, 1080, which=lambda name: f"/usr/bin/{name}")
    assert argv[:2] == ["/usr/bin/dbus-run-session", "--"]
    assert argv[2:] == ["/usr/bin/kwin_wayland", "--virtual", "--no-lockscreen", "--no-global-shortcuts", "--no-kactivities", "--socket", SOCKET, "--width", "1920", "--height", "1080"]
    with pytest.raises(wc.CaptureError):
        wc.compositor_argv("wayland-0", 1920, 1080, which=lambda name: "/x")
    with pytest.raises(wc.CaptureError):
        wc.compositor_argv(SOCKET, 1920, 1080, which=lambda name: None)


def test_private_screen_cannot_reach_the_desktop():
    env = wc.compositor_env(
        {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/x", "KWIN_WAYLAND_NO_PERMISSION_CHECKS": "1", "XDG_RUNTIME_DIR": "/run/user/1000", "LANG": "C"}
    )
    assert env == {"XDG_RUNTIME_DIR": "/run/user/1000", "LANG": "C"}


def test_mirror_reads_only_the_chosen_window():
    kwin = wc.mirror_description(wc.Spec.from_mapping(spec()), SOCKET, node=77, fd=9)
    assert kwin.startswith("pipewiresrc name=source fd=9 path=77 ")
    assert f"display={SOCKET}" in kwin and "videocrop" in kwin
    x11 = wc.mirror_description(wc.Spec.from_mapping(spec(backend="x11", handle="0x400001", display=":1")), SOCKET)
    assert f"xid={0x400001}" in x11 and "display-name=:1" in x11
    with pytest.raises(wc.CaptureError):
        wc.mirror_description(wc.Spec.from_mapping(spec()), SOCKET)  # no portal stream
    with pytest.raises(wc.CaptureError):
        wc.mirror_description(wc.Spec.from_mapping(spec()), "wayland-0", node=1, fd=3)


def test_title_bar_is_cropped_in_buffer_pixels_and_never_when_fullscreen():
    parsed = wc.Spec.from_mapping(spec(width=1280, decoration=[1, 30, 1, 1]))
    assert wc.crop_for(parsed, 1923, decorated=True) == (2, 45, 2, 2)
    assert wc.crop_for(parsed, 1923, decorated=False) == (0, 0, 0, 0)
    x11 = wc.Spec.from_mapping(spec(backend="x11", handle="0x400001", display=":0"))
    assert wc.crop_for(x11, 1280, decorated=True) == (0, 0, 0, 0)


def test_a_different_window_shape_is_detected():
    assert wc.aspect_matches((1280, 750), (1920, 1125))
    assert not wc.aspect_matches((1280, 750), (3440, 1440))
    assert wc.aspect_matches((0, 0), (10, 10))


class FakeDrm:
    def __init__(self, nodes):
        self.nodes = nodes
        self.freed = False

    def drmGetDevices2(self, _flags, devices, count):
        if devices is None:
            return len(self.nodes)
        for index, path in enumerate(self.nodes[:count]):
            names = (wc.ctypes.c_char_p * 3)(b"/dev/dri/card0", None, path.encode())
            device = wc._DrmDevice(names, 1 << 2 | 1, 0)
            devices[index] = wc.ctypes.pointer(device)
            self.keep = getattr(self, "keep", []) + [names, device]
        return len(self.nodes)

    def drmFreeDevices(self, _devices, _count):
        self.freed = True


def test_encoder_gpu_is_the_private_screen_gpu():
    drm = FakeDrm(["/dev/dri/renderD129", "/dev/dri/renderD128"])
    assert wc.isolated_render_node(load=lambda: drm) == "/dev/dri/renderD129"
    assert drm.freed
    assert wc.isolated_render_node(load=lambda: FakeDrm(["/tmp/evil"])) is None
    assert wc.isolated_render_node(load=lambda: None) is None


# ── consent tokens ──────────────────────────────────────────────────────────


def test_restore_tokens_are_private_validated_and_bounded(tmp_path):
    path = tmp_path / "tokens.json"
    wc.save_restore_token("steam:1", "abc_DEF-123", path)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert wc.load_restore_token("steam:1", path) == "abc_DEF-123"
    wc.save_restore_token("steam:2", "has spaces", path)
    assert wc.load_restore_token("steam:2", path) == ""
    assert wc.load_restore_token("", path) == ""
    for index in range(80):
        wc.save_restore_token(f"steam:{index + 10}", "t", path)
    assert len(json.loads(path.read_text())) == 64
    path.write_text("[not a map")
    assert wc.load_restore_token("steam:1", path) == ""


# ── portal answers ──────────────────────────────────────────────────────────


class FakeBus:
    def get_unique_name(self):
        return ":1.42"

    def signal_subscribe(self, *args):
        return 1

    def signal_unsubscribe(self, _id):
        pass

    def call_with_unix_fd_list_sync(self, *args):
        class FdList:
            def get(self, _index):
                return 17

        return GLib.Variant("(h)", (0,)), FdList()

    def call_sync(self, *args):
        return None


class FakeGio:
    class BusType:
        SESSION = 0

    class DBusCallFlags:
        NONE = 0

    class DBusSignalFlags:
        NONE = 0

    @staticmethod
    def bus_get_sync(*_args):
        return FakeBus()


def portal(start_streams, *, types=7):
    closed = []
    item = wc._Portal(FakeGio, GLib, lambda: closed.append(True))
    item._properties = lambda: {"AvailableSourceTypes": types, "AvailableCursorModes": 7}
    answers = {
        "CreateSession": (0, {"session_handle": "/org/freedesktop/portal/desktop/session/1_42/s"}),
        "SelectSources": (0, {}),
        "Start": (0, {"streams": start_streams, "restore_token": "tok"}),
    }
    item._request = lambda method, signature, args, timeout: answers[method]
    return item


def test_portal_window_stream_is_accepted():
    assert portal([(55, {"source_type": 2})]).open("") == (55, 17, "tok")


@pytest.mark.parametrize("streams", [[(55, {"source_type": 1})], [(1, {"source_type": 2}), (2, {"source_type": 2})], []])
def test_portal_monitor_or_several_streams_are_refused(streams):
    with pytest.raises(wc.CaptureError) as error:
        portal(streams).open("")
    assert error.value.reason == "different-window"


def test_portal_without_window_sharing_is_unavailable():
    with pytest.raises(wc.CaptureError) as error:
        portal([], types=1).open("")
    assert error.value.reason == "portal-unavailable"


# ── the watchdog ─────────────────────────────────────────────────────────────


class FakePipeline:
    def __init__(self):
        self.states = []

    def set_state(self, state):
        self.states.append(state)

    def get_by_name(self, _name):
        return None


class FakeGst:
    class State:
        NULL = "null"


class FakeCompositor:
    pid = 0

    def __init__(self, alive=True):
        self.alive = alive

    def poll(self):
        return None if self.alive else 1


def session(backend="kwin", **extra):
    item = object.__new__(wc.CaptureSession)
    item.GLib, item.Gst, item.Gio = GLib, FakeGst, None
    item.spec = wc.Spec.from_mapping(spec(backend=backend, **({"handle": "0x400001", "display": ":0"} if backend == "x11" else {})))
    item.socket = SOCKET
    item.loop = GLib.MainLoop()
    item.pipeline = FakePipeline()
    item.portal = None
    item.compositor = FakeCompositor()
    item.ended = False
    item.reason = "stopped"
    item.started_at = time.time()
    item._decorated = True
    item._buffer_width = item._buffer_height = 0
    item._kwin_misses = 0
    item._fd = -1
    item._frames = item._frames_seen = 0
    item._still_since = time.monotonic()
    item._stall_reported = False
    item.__dict__.update(extra)
    return item


def test_closed_window_stops_the_picture_at_once_and_never_shows_anything_else(monkeypatch):
    monkeypatch.setattr(game_windows, "kwin_window_info", lambda uuid, connection=None: {})
    item = session()
    pipeline = item.pipeline
    assert item._check() is False
    assert item.ended and item.reason == "window-closed"
    assert pipeline.states == ["null"] and item.pipeline is None
    state = wc.read_state()
    assert state["state"] == "ended" and state["reason"] == "window-closed"
    assert not wc.helper_running(state)


def test_kwin_briefly_silent_is_tolerated_then_ends(monkeypatch):
    monkeypatch.setattr(game_windows, "kwin_window_info", lambda uuid, connection=None: None)
    item = session()
    assert item._check() and item._check()
    assert item._check() is False and item.reason == "window-unknown"


def test_fullscreen_toggle_updates_the_crop(monkeypatch):
    monkeypatch.setattr(game_windows, "kwin_window_info", lambda uuid, connection=None: {"fullscreen": True})
    applied = []
    item = session()
    item._apply_crop = lambda: applied.append(item._decorated)
    assert item._check() is True
    assert applied == [False]


def test_x11_capture_stops_when_compositing_is_suspended(monkeypatch):
    monkeypatch.setattr(x11_windows, "window_alive", lambda display, xid: True)
    monkeypatch.setattr(x11_windows, "compositing_active", lambda display: True)
    monkeypatch.setattr(game_windows, "kwin_compositing_active", lambda connection=None: False)
    item = session("x11")
    assert item._check() is False and item.reason == "compositing-off"


def test_x11_window_gone_or_display_gone_ends(monkeypatch):
    for alive in (False, None):
        monkeypatch.setattr(x11_windows, "window_alive", lambda display, xid, alive=alive: alive)
        item = session("x11")
        assert item._check() is False and item.reason == "window-closed"


def test_private_screen_exit_ends_the_session():
    item = session(compositor=FakeCompositor(alive=False))
    assert item._check() is False and item.reason == "compositor-exited"


# ── starting and stopping the helper from the application ───────────────────


class FakeProcess:
    def __init__(self, line: str, *, alive=False):
        self.stdout = io.StringIO(line + "\n" if line else "")
        self.stdin = io.StringIO()
        self.pid = 0
        self._alive = alive

    def poll(self):
        return None if self._alive else 0

    def wait(self, timeout=None):
        return 0


def fake_popen(line, **kwargs):
    def popen(argv, **options):
        assert argv[1:] == ["-I", str(wc.Path(wc.__file__).resolve()), "run"]
        assert options["start_new_session"] is True
        return FakeProcess(line, **kwargs)

    return popen


@pytest.fixture
def no_select(monkeypatch):
    monkeypatch.setattr(wc.select, "select", lambda readers, *_args: (readers, [], []))


def test_ready_helper_reports_its_private_screen(no_select):
    line = json.dumps({"state": "ready", "socket": SOCKET, "render_node": "/dev/dri/renderD128", "width": 1920, "height": 1080})
    started, _process = wc.start_helper(wc.Spec.from_mapping(spec()), popen=fake_popen(line))
    assert (started.socket, started.render_node) == (SOCKET, "/dev/dri/renderD128")


@pytest.mark.parametrize(
    "line, reason",
    [
        (json.dumps({"state": "failed", "reason": "window-closed"}), "window-closed"),
        (json.dumps({"state": "ready", "socket": "wayland-0"}), "capture-failed"),
        (json.dumps({"state": "ready", "socket": SOCKET, "render_node": "/tmp/x"}), "capture-failed"),
        ("not json", "capture-failed"),
    ],
)
def test_failed_or_odd_helper_answers_never_start_sharing(no_select, line, reason):
    with pytest.raises(wc.CaptureError) as error:
        wc.start_helper(wc.Spec.from_mapping(spec()), popen=fake_popen(line))
    assert error.value.reason == reason


def test_unconfirmed_window_times_out_as_not_confirmed(monkeypatch):
    monkeypatch.setattr(wc.select, "select", lambda readers, *_args: ([], [], []))
    with pytest.raises(wc.CaptureError) as error:
        wc.start_helper(wc.Spec.from_mapping(spec()), timeout=0.3, popen=fake_popen("", alive=True))
    assert error.value.reason == "portal-cancelled"


def _sleeper(*argv: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", *argv], start_new_session=True)


def test_left_over_private_screen_is_stopped_only_when_it_is_ours():
    ours, other = _sleeper(SOCKET), _sleeper("brp-game-ffffffffffff")
    try:
        wc.stop_helper({"state": "running", "pid": 0, "pgid": ours.pid, "socket": SOCKET})
        wc.stop_helper({"state": "running", "pid": 0, "pgid": other.pid, "socket": SOCKET})
        assert ours.wait(timeout=5) == -signal.SIGTERM
        assert other.poll() is None
    finally:
        for process in (ours, other):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


def test_helper_run_answers_failed_for_a_bad_spec(tmp_path):
    out = io.StringIO()
    assert wc.run(stdin=io.StringIO(json.dumps(spec(version=9)) + "\n"), stdout=out) == 1
    assert json.loads(out.getvalue()) == {"state": "failed", "reason": "invalid-spec"}


def _screen(modes, current="1"):
    return {
        "outputs": [{"name": "Virtual-0", "enabled": True, "currentModeId": current, "modes": [{"id": mode_id, "size": {"width": w, "height": h}, "refreshRate": r} for mode_id, w, h, r in modes]}]
    }


def test_private_screen_gets_a_fast_mode_then_uses_it():
    tool = "/usr/bin/kscreen-doctor"
    only_60 = _screen([("1", 1280, 720, 60)])
    assert wc.fast_mode_commands(only_60, 1280, 720, tool) == [[tool, "output.Virtual-0.addCustomMode.1280.720.240000.full"]]
    added = _screen([("1", 1280, 720, 60), ("2", 1280, 720, 239.3)])
    assert wc.fast_mode_commands(added, 1280, 720, tool) == [[tool, "output.Virtual-0.mode.2"]]
    assert wc.fast_mode_commands(_screen([("1", 1280, 720, 60), ("2", 1280, 720, 239.3)], current="2"), 1280, 720, tool) == []
    # Anything unexpected (no screen, several screens): leave it at 60 Hz.
    assert wc.fast_mode_commands({"outputs": []}, 1280, 720, tool) == []
    assert wc.fast_mode_commands(None, 1280, 720, tool) == []


def test_private_screen_width_suits_generated_modes():
    width, height = wc.screen_size(wc.Spec.from_mapping(spec(width=500, height=501, scale=1.0)))
    assert width % 8 == 0 and height % 2 == 0 and (width, height) == (496, 500)


def test_window_activation_never_builds_a_script_from_an_invalid_id(monkeypatch):
    scripts = []
    monkeypatch.setattr(game_windows, "_run_kwin_script", lambda script, timeout_ms=4000: scripts.append(script) or "1")
    assert not game_windows.activate_kwin_window('{x"}; workspace.activeWindow = null; //')
    assert scripts == []
    assert game_windows.activate_kwin_window(UUID)
    assert UUID in scripts[0]


def test_a_game_that_stops_drawing_is_reported_once_and_resuming_too(caplog):
    item = session()
    start = item._still_since
    with caplog.at_level("INFO", logger="big-remoteplay"):
        item._note_frames(start + wc.STALL_SECONDS)
        assert "sent no picture" in caplog.text  # never drew: the device sees black
        caplog.clear()
        item._frames = 10
        item._note_frames(start + 6)
        assert "sending pictures again" in caplog.text
        caplog.clear()
        item._note_frames(start + 6 + wc.STALL_SECONDS)
        item._note_frames(start + 7 + wc.STALL_SECONDS)
        assert caplog.text.count("no new picture") == 1


def _fake_sunshine(socket: str) -> subprocess.Popen:
    """A process named "sunshine" whose WAYLAND_DISPLAY is ``socket``."""
    code = "import ctypes, time; ctypes.CDLL(None).prctl(15, b'sunshine', 0, 0, 0); time.sleep(30)"
    env = dict(os.environ, WAYLAND_DISPLAY=socket)
    process = subprocess.Popen([sys.executable, "-c", code], env=env)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and wc.Path(f"/proc/{process.pid}/comm").read_text().strip() != "sunshine":
        time.sleep(0.05)
    return process


def test_the_capture_stops_only_the_sunshine_of_its_private_screen():
    ours, desktop = _fake_sunshine(SOCKET), _fake_sunshine("wayland-0")
    try:
        assert wc.stop_private_sunshine(SOCKET, grace=5) == 1
        assert ours.wait(timeout=5) == -signal.SIGTERM
        assert desktop.poll() is None
        assert wc.stop_private_sunshine("wayland-0") == 0  # not a private screen name
    finally:
        for process in (ours, desktop):
            if process.poll() is None:
                process.kill()
                process.wait()


def test_without_the_application_the_capture_ends_its_sunshine_after_the_hold(monkeypatch):
    stopped = []
    monkeypatch.setattr(wc, "stop_private_sunshine", lambda socket: stopped.append(socket) or 1)
    item = session()
    assert item._stop_sunshine_and_quit() is False
    assert stopped == [SOCKET]
