"""Host input priority: who gets the mouse and keyboard, and that nothing sticks.

No test opens a real input device: the kernel calls are a fake that records
what the helper asked for, and the helper loop talks through pipes.
"""

from __future__ import annotations

import io
import json
import os
import threading
import time
from pathlib import Path

import pytest

from big_remote_play.host import input_priority as ip

ORIGINAL_HELPER_ARGV = ip.helper_argv  # conftest replaces it for every test

KEYBOARD_KEYS = sum(1 << code for code in range(1, 128))  # Esc .. letters, Enter, Space
EV = lambda *kinds: sum(1 << kind for kind in kinds)  # noqa: E731


def device(node: str, name: str, *, virtual: bool = False, ev: int = 0, key: int = 0, rel: int = 0, abs_: int = 0) -> ip.DeviceInfo:
    return ip.DeviceInfo(node, name, virtual, ev, key, rel, abs_)


def keyboard(node="/dev/input/event3", name="Logitech USB Keyboard", virtual=False):
    return device(node, name, virtual=virtual, ev=EV(ip.EV_KEY), key=KEYBOARD_KEYS)


def mouse(node="/dev/input/event2", name="Logitech USB Optical Mouse", virtual=False):
    return device(node, name, virtual=virtual, ev=EV(ip.EV_KEY, ip.EV_REL), key=sum(1 << code for code in range(0x110, 0x115)), rel=0b11 | 1 << 8)


SUNSHINE_KEYBOARD = keyboard("/dev/input/event25", "libvirtualhid Keyboard", virtual=True)
SUNSHINE_MOUSE = mouse("/dev/input/event26", "libvirtualhid Mouse", virtual=True)
SUNSHINE_ABSOLUTE = device("/dev/input/event27", "libvirtualhid Mouse (Absolute)", virtual=True, ev=EV(ip.EV_KEY, ip.EV_ABS), key=1 << ip.BTN_LEFT, abs_=0b11)


# ── which device is whose ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "info", [SUNSHINE_KEYBOARD, SUNSHINE_MOUSE, SUNSHINE_ABSOLUTE, keyboard("/dev/input/event9", "Keyboard passthrough", virtual=True), mouse("/dev/input/event8", "Mouse passthrough", virtual=True)]
)
def test_sunshine_keyboard_and_mice_are_the_other_devices(info) -> None:
    assert ip.classify(info) == "remote"


@pytest.mark.parametrize(
    "info",
    [
        keyboard(),
        mouse(),
        # Bluetooth LE keyboards are created through uhid, under /sys/devices/virtual too.
        keyboard("/dev/input/event40", "MX Keys", virtual=True),
        # A touchpad: absolute position with finger buttons.
        device("/dev/input/event5", "ELAN Touchpad", ev=EV(ip.EV_KEY, ip.EV_ABS), key=1 << ip.BTN_LEFT | 1 << ip.BTN_TOOL_FINGER | 1 << ip.BTN_TOUCH, abs_=0b11),
    ],
)
def test_keyboards_mice_and_touchpads_of_this_computer_are_local(info) -> None:
    assert ip.classify(info) == "local"


@pytest.mark.parametrize(
    "info",
    [
        device("/dev/input/event0", "Power Button", ev=EV(ip.EV_KEY), key=1 << 116),
        device("/dev/input/event8", "Video Bus", ev=EV(ip.EV_KEY), key=1 << 224 | 1 << 225),
        device("/dev/input/event12", "HD-Audio Generic HDMI/DP,pcm=3", ev=EV(5)),
        # Controllers: never paused, never "someone at this computer".
        device("/dev/input/event30", "Sunshine (libvirtualhid) X-Box 360 Controller", virtual=True, ev=EV(ip.EV_KEY, ip.EV_ABS), key=1 << 0x130, abs_=0b11),
        device("/dev/input/event31", "Sunshine (libvirtualhid) PS5 Controller Touchpad", virtual=True, ev=EV(ip.EV_KEY, ip.EV_ABS), key=1 << ip.BTN_LEFT | 1 << ip.BTN_TOUCH, abs_=0b11),
        device("/dev/input/event32", "Sunshine X-Box One (virtual) pad", virtual=True, ev=EV(ip.EV_KEY, ip.EV_ABS), key=1 << 0x130, abs_=0b11),
        device("/dev/input/event33", "Xbox Wireless Controller", ev=EV(ip.EV_KEY, ip.EV_ABS), key=1 << 0x130 | 1 << 0x131, abs_=0b11),
    ],
)
def test_buttons_jacks_and_controllers_are_ignored(info) -> None:
    assert ip.classify(info) == "ignored"


def test_a_physical_device_named_like_sunshines_is_still_local() -> None:
    """The name alone never makes a real keyboard the other device's."""
    assert ip.classify(keyboard("/dev/input/event4", "libvirtualhid Keyboard", virtual=False)) == "local"


def test_sysfs_description_is_read_without_opening_the_device(tmp_path: Path) -> None:
    sys_root, devices = tmp_path / "class", tmp_path / "devices"
    for event, name, location, caps in (
        ("event25", "libvirtualhid Keyboard", devices / "virtual" / "input" / "input26", {"ev": "120013", "key": "7fffffffffffffff ffffffffffffffff fffffffffffffffe", "rel": "0", "abs": "0"}),
        ("event2", "Logitech USB Optical Mouse", devices / "pci0000:00" / "usb3" / "input2", {"ev": "17", "key": "1f0000 0 0 0 0", "rel": "903", "abs": "0"}),
    ):
        (location / "capabilities").mkdir(parents=True)
        (location / "name").write_text(name + "\n")
        for key, value in caps.items():
            (location / "capabilities" / key).write_text(value + "\n")
        (sys_root / event).mkdir(parents=True)
        (sys_root / event / "device").symlink_to(location)
    (sys_root / "mouse0").mkdir()

    found = {info.name: info for info in ip.list_devices(sys_root=sys_root, dev_root=Path("/dev/input"))}

    assert set(found) == {"libvirtualhid Keyboard", "Logitech USB Optical Mouse"}
    assert found["libvirtualhid Keyboard"].virtual and found["libvirtualhid Keyboard"].node == "/dev/input/event25"
    assert ip.classify(found["libvirtualhid Keyboard"]) == "remote"
    assert not found["Logitech USB Optical Mouse"].virtual
    assert ip.classify(found["Logitech USB Optical Mouse"]) == "local"
    assert ip.read_device("mouse0", sys_root=sys_root) is None


def test_bitmaps_are_read_most_significant_word_first() -> None:
    assert ip.parse_bitmap("1f0000 0 0 0 0", 64) == 0x1F0000 << 256
    assert ip.parse_bitmap("903", 64) == 0x903
    assert ip.parse_bitmap("not hex") == 0


def test_access_check_needs_reading_local_and_writing_sunshines() -> None:
    devices = [keyboard(), SUNSHINE_KEYBOARD, device("/dev/input/event0", "Power Button", ev=EV(ip.EV_KEY), key=1 << 116)]
    asked = []

    def access(node, mode):
        asked.append((node, mode))
        return True

    assert ip.devices_accessible(devices, access=access)
    assert ("/dev/input/event3", os.R_OK) in asked and ("/dev/input/event25", os.R_OK | os.W_OK) in asked
    assert all(node != "/dev/input/event0" for node, _mode in asked)
    assert not ip.devices_accessible(devices, access=lambda node, mode: mode == os.R_OK)
    assert not ip.devices_accessible([], access=lambda *_args: True)


# ── the decision ──────────────────────────────────────────────────────────


def test_local_use_pauses_and_each_new_use_restarts_the_count() -> None:
    arbiter = ip.Arbiter(5)
    assert arbiter.wait(0.0) is None  # nothing happens by itself while the guest plays

    assert arbiter.local_activity(10.0) is True
    assert arbiter.local_activity(12.0) is False  # already paused
    assert arbiter.wait(14.0) == pytest.approx(3.0)
    assert arbiter.expire(16.9) is False
    assert arbiter.expire(17.0) is True
    assert not arbiter.paused and arbiter.wait(17.0) is None


@pytest.mark.parametrize(("value", "expected"), [(0, 1), (-3, 1), (5, 5), (31, 30), ("7", 7), (None, 5), ("x", 5), (2.9, 2)])
def test_delay_is_bounded(value, expected) -> None:
    assert ip.clamp_delay(value) == expected


@pytest.mark.parametrize(
    ("events", "active"),
    [
        ([(ip.EV_KEY, 30, 1)], True),
        ([(ip.EV_KEY, 30, 0)], True),
        ([(ip.EV_REL, ip.REL_X, 3), (ip.EV_SYN, 0, 0)], True),
        ([(ip.EV_REL, 8, -1)], True),  # scroll
        ([(ip.EV_ABS, ip.ABS_X, 0)], True),  # a touch at the edge
        ([(ip.EV_REL, ip.REL_X, 0), (ip.EV_SYN, 0, 0)], False),
        ([(4, 4, 458756), (ip.EV_SYN, 0, 0)], False),  # MSC_SCAN only
        ([(0x11, 1, 1)], False),  # LED
        ([], False),
    ],
)
def test_only_real_use_counts_as_activity(events, active) -> None:
    assert ip.is_activity(events) is active


# ── pause and resume ──────────────────────────────────────────────────────


class FakeEvdev(ip.Evdev):
    """The kernel's view of a few devices: held keys, grabs and pending events."""

    def __init__(self, *, denied=()):
        self.calls: list[tuple] = []
        self.held: dict[int, set[int]] = {}
        self.grabbed: set[int] = set()
        self.pending: dict[int, list[tuple[int, int, int]]] = {}
        self.unplugged: set[int] = set()
        self.denied = set(denied)
        self.fds: dict[str, int] = {}
        self.press_during_grab: dict[int, int] = {}  # fd → key pressed right as the grab happens

    def open(self, node, *, write):
        if node in self.denied:
            raise PermissionError(13, "denied")
        fd = 100 + len(self.fds)
        self.fds[node] = fd
        self.held.setdefault(fd, set())
        self.calls.append(("open", node, write))
        return fd

    def close(self, fd):
        self.calls.append(("close", fd))

    def grab(self, fd, on):
        self.calls.append(("grab", fd, on))
        (self.grabbed.add if on else self.grabbed.discard)(fd)
        if on and fd in self.press_during_grab:
            self.held[fd].add(self.press_during_grab.pop(fd))

    def pressed(self, fd):
        return sorted(self.held[fd])

    def release(self, fd, codes):
        codes = list(codes)
        if codes:
            self.calls.append(("release", fd, tuple(codes), fd in self.grabbed))
            self.held[fd] -= set(codes)

    def read(self, fd):
        if fd in self.unplugged:
            raise OSError(19, "No such device")
        events, self.pending[fd] = self.pending.get(fd, []), []
        return events


def session_with(devices, evdev=None, delay=5):
    messages: list[dict] = []
    fake = evdev or FakeEvdev()
    session = ip.PrioritySession(delay, evdev=fake, emit=messages.append, scan=lambda: list(devices))
    session.refresh_devices()
    return session, fake, messages


def test_held_guest_keys_are_released_where_the_desktop_sees_them_before_the_pause() -> None:
    session, kernel, messages = session_with([keyboard(), SUNSHINE_KEYBOARD, SUNSHINE_MOUSE])
    remote_kb, remote_mouse = session.remote["/dev/input/event25"], session.remote["/dev/input/event26"]
    kernel.held[remote_kb] = {17, 29, 42, 56, 125}  # W, Ctrl, Shift, Alt, Super
    kernel.held[remote_mouse] = {ip.BTN_LEFT}  # a drag in progress

    session.on_local_activity(100.0)

    assert ("release", remote_kb, (17, 29, 42, 56, 125), False) in kernel.calls
    assert ("release", remote_mouse, (ip.BTN_LEFT,), False) in kernel.calls
    # Released first, grabbed after: the release reached the desktop.
    assert kernel.calls.index(("release", remote_kb, (17, 29, 42, 56, 125), False)) < kernel.calls.index(("grab", remote_kb, True))
    assert kernel.grabbed == {remote_kb, remote_mouse}
    assert messages[-1] == {"event": "paused", "delay": 5}
    # Physical devices are only read, never grabbed or written.
    local = session.local["/dev/input/event3"]
    assert all(call[1] != local for call in kernel.calls if call[0] in ("grab", "release"))


def test_a_key_pressed_while_grabbing_is_released_to_the_desktop_too() -> None:
    kernel = FakeEvdev()
    session, kernel, _messages = session_with([keyboard(), SUNSHINE_KEYBOARD], kernel)
    fd = session.remote["/dev/input/event25"]
    kernel.press_during_grab[fd] = 30  # A pressed between reading the state and taking the device

    session.on_local_activity(1.0)

    grabs = [call for call in kernel.calls if call[0] == "grab" and call[1] == fd]
    assert grabs == [("grab", fd, True), ("grab", fd, False), ("grab", fd, True)]
    assert ("release", fd, (30,), False) in kernel.calls
    assert fd in kernel.grabbed and not kernel.held[fd]


def test_resume_forgets_keys_pressed_during_the_pause_before_giving_the_device_back() -> None:
    """Otherwise the kernel drops the next press of that key: it believes it is still down."""
    session, kernel, messages = session_with([keyboard(), SUNSHINE_KEYBOARD])
    fd = session.remote["/dev/input/event25"]
    session.on_local_activity(10.0)
    kernel.held[fd] = {17}  # the guest pressed W while paused (only the helper saw it)
    kernel.calls.clear()

    session.on_timeout(14.9)
    assert fd in kernel.grabbed and messages[-1]["event"] == "paused"
    session.on_timeout(15.0)

    assert kernel.calls[0] == ("release", fd, (17,), True)  # silent: still grabbed
    assert kernel.calls[-1] == ("grab", fd, False)
    assert not kernel.grabbed and messages[-1] == {"event": "resumed"}


def test_only_this_computers_devices_start_a_pause() -> None:
    session, kernel, messages = session_with([keyboard(), SUNSHINE_KEYBOARD])
    remote, local = session.remote["/dev/input/event25"], session.local["/dev/input/event3"]

    kernel.pending[remote] = [(ip.EV_KEY, 30, 1)]
    session.on_readable(remote, 1.0)
    assert not session.arbiter.paused

    kernel.pending[local] = [(4, 4, 7), (ip.EV_SYN, 0, 0)]  # a scan code alone is not use
    session.on_readable(local, 2.0)
    assert not session.arbiter.paused

    kernel.pending[local] = [(ip.EV_KEY, 30, 1), (ip.EV_SYN, 0, 0)]
    session.on_readable(local, 3.0)
    assert session.arbiter.paused and messages[-1]["event"] == "paused"


def test_new_sunshine_devices_join_a_pause_and_unplugged_ones_are_forgotten() -> None:
    devices = [keyboard(), mouse(), SUNSHINE_KEYBOARD]
    session, kernel, messages = session_with(devices)
    assert messages[-1] == {"event": "devices", "remote": 1, "local": 2}
    session.on_local_activity(1.0)

    # Sunshine restarted (new nodes) and the USB mouse was unplugged.
    devices[:] = [keyboard(), keyboard("/dev/input/event41", "libvirtualhid Keyboard", virtual=True)]
    session.refresh_devices()

    new_fd = session.remote["/dev/input/event41"]
    assert new_fd in kernel.grabbed
    assert "/dev/input/event2" not in session.local and "/dev/input/event25" not in session.remote
    assert messages[-1] == {"event": "devices", "remote": 1, "local": 1}


def test_a_device_that_disappears_while_read_is_dropped() -> None:
    session, kernel, _messages = session_with([keyboard(), SUNSHINE_KEYBOARD])
    local = session.local["/dev/input/event3"]
    kernel.unplugged.add(local)

    session.on_readable(local, 1.0)

    assert "/dev/input/event3" not in session.local and ("close", local) in kernel.calls


def test_closing_while_paused_gives_the_guest_control_back() -> None:
    session, kernel, messages = session_with([keyboard(), SUNSHINE_KEYBOARD])
    session.on_local_activity(1.0)

    session.close()

    assert not kernel.grabbed and messages[-1] == {"event": "resumed"}
    assert not session.remote and not session.local


def test_permission_problems_are_counted() -> None:
    session, _kernel, messages = session_with([keyboard(), SUNSHINE_KEYBOARD], FakeEvdev(denied={"/dev/input/event25"}))
    assert session.denied == {"/dev/input/event25"} and not session.remote
    assert messages[-1] == {"event": "devices", "remote": 0, "local": 1, "denied": 1}


# ── the helper process ────────────────────────────────────────────────────


class Helper:
    """run() in a thread, talking through pipes like the real process."""

    def __init__(self, monkeypatch, devices, kernel):
        monkeypatch.setattr(ip, "list_devices", lambda **_kwargs: list(devices))
        monkeypatch.setattr(ip, "Evdev", lambda: kernel)
        monkeypatch.setattr(ip, "_Inotify", lambda: type("NoWatch", (), {"fd": -1})())
        read_fd, self.write_fd = os.pipe()
        self.stdin = os.fdopen(read_fd, "r")
        self.stdout = io.StringIO()
        self.code: list[int] = []
        self.thread = threading.Thread(target=lambda: self.code.append(ip.run(self.stdin, self.stdout)), daemon=True)

    def send(self, message) -> None:
        os.write(self.write_fd, (json.dumps(message) + "\n").encode())

    def start(self):
        self.thread.start()
        return self

    def messages(self):
        return [json.loads(line) for line in self.stdout.getvalue().splitlines()]

    def wait_for(self, event, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(message.get("event") == event for message in self.messages()):
                return True
            time.sleep(0.01)
        return False

    def close(self):
        os.close(self.write_fd)
        self.thread.join(5)


class PipeEvdev(FakeEvdev):
    """FakeEvdev whose devices are real pipes, so the helper's select() works."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.writers: dict[int, int] = {}

    def open(self, node, *, write):
        if node in self.denied:
            raise PermissionError(13, "denied")
        read_fd, write_fd = os.pipe()
        os.set_blocking(read_fd, False)
        self.fds[node] = read_fd
        self.writers[read_fd] = write_fd
        self.held.setdefault(read_fd, set())
        return read_fd

    def emit(self, node, events):
        fd = self.fds[node]
        self.pending.setdefault(fd, []).extend(events)
        os.write(self.writers[fd], b"x")

    def read(self, fd):
        try:
            os.read(fd, 4096)
        except BlockingIOError:
            pass
        return super().read(fd)

    def close(self, fd):
        super().close(fd)
        for end in (fd, self.writers.pop(fd, -1)):
            try:
                os.close(end)
            except OSError:
                pass


def test_helper_pauses_on_local_use_resumes_after_the_delay_and_exits_with_its_input(monkeypatch) -> None:
    kernel = PipeEvdev()
    helper = Helper(monkeypatch, [keyboard(), SUNSHINE_KEYBOARD], kernel)
    helper.send({"version": 1, "delay": 30})
    helper.send({"delay": 1})  # sent right behind the spec: must not be lost
    helper.start()
    assert helper.wait_for("ready")
    remote = kernel.fds["/dev/input/event25"]
    kernel.held[remote] = {17}  # the guest holds W

    started = time.monotonic()
    kernel.emit("/dev/input/event3", [(ip.EV_KEY, 57, 1), (ip.EV_SYN, 0, 0)])
    assert helper.wait_for("paused")
    assert remote in kernel.grabbed and ("release", remote, (17,), False) in kernel.calls
    assert helper.wait_for("resumed", timeout=4)
    assert 0.9 <= time.monotonic() - started < 3.0  # the 1 s delay, not the 30 s one
    assert remote not in kernel.grabbed

    kernel.emit("/dev/input/event3", [(ip.EV_REL, ip.REL_X, 2)])
    assert helper.wait_for("paused")
    helper.close()  # Big Remote Play stopped sharing while paused

    assert helper.code == [0]
    assert not kernel.grabbed and helper.messages()[-1] == {"event": "resumed"}
    assert [m["event"] for m in helper.messages()].count("paused") == 2


def test_helper_refuses_a_bad_spec_and_missing_permission(monkeypatch) -> None:
    helper = Helper(monkeypatch, [keyboard()], FakeEvdev())
    helper.send({"version": 99})
    helper.start()
    helper.thread.join(5)
    assert helper.code == [1] and helper.messages() == [{"event": "unavailable", "reason": "invalid-spec"}]
    os.close(helper.write_fd)

    denied = Helper(monkeypatch, [keyboard(), SUNSHINE_KEYBOARD], FakeEvdev(denied={"/dev/input/event25"}))
    denied.send({"version": 1, "delay": 5})
    denied.start()
    denied.thread.join(5)
    assert denied.code == [1] and denied.messages()[-1] == {"event": "unavailable", "reason": "permission"}
    os.close(denied.write_fd)


def test_helper_runs_as_the_user_when_allowed_and_through_policykit_otherwise() -> None:
    devices = [keyboard(), SUNSHINE_KEYBOARD]
    allowed = ORIGINAL_HELPER_ARGV(devices=devices, exists=lambda _path: False)
    assert allowed is not None or not ip.devices_accessible(devices)

    import big_remote_play.host.input_priority as module

    original_access = module.devices_accessible
    try:
        module.devices_accessible = lambda _devices: True  # type: ignore[assignment]
        argv = ORIGINAL_HELPER_ARGV(devices=devices, exists=lambda _path: False)
        assert argv is not None and argv[1:3] == ["-I", str(Path(module.__file__).resolve())] and argv[-1] == "run"
        module.devices_accessible = lambda _devices: False  # type: ignore[assignment]
        assert ORIGINAL_HELPER_ARGV(devices=devices, exists=lambda path: path == ip.LAUNCHER, which=lambda name: "/usr/bin/pkexec") == ["pkexec", ip.LAUNCHER]
        assert ORIGINAL_HELPER_ARGV(devices=devices, exists=lambda _path: False, which=lambda name: "/usr/bin/pkexec") is None
        assert ORIGINAL_HELPER_ARGV(devices=devices, exists=lambda _path: True, which=lambda _name: None) is None
    finally:
        module.devices_accessible = original_access  # type: ignore[assignment]


class FakeProcess:
    def __init__(self, lines=(), code=0):
        read_fd, write_fd = os.pipe()
        os.write(write_fd, "".join(json.dumps(line) + "\n" for line in lines).encode())
        os.close(write_fd)
        self.stdout = os.fdopen(read_fd, "r")
        self.stdin = io.StringIO()
        self.sent: list[dict] = []
        self.code = code
        self.closed = False
        original_close = self.stdin.close

        def close():
            self.sent = [json.loads(line) for line in self.stdin.getvalue().splitlines()]
            self.closed = True
            original_close()

        self.stdin.close = close  # type: ignore[method-assign]

    def wait(self, timeout=None):
        return self.code

    def poll(self):
        return self.code if self.closed else None


def test_process_sends_the_spec_and_delay_changes_and_stops_by_closing_input() -> None:
    events: list[dict] = []
    fake = FakeProcess([{"event": "ready", "remote": 3, "local": 2}, {"event": "paused", "delay": 5}])
    process = ip.PriorityProcess(5, events.append, argv=["helper"], popen=lambda *_args, **_kwargs: fake)

    assert process.start()
    process.set_delay(99)
    deadline = time.monotonic() + 3
    while not any(event["event"] == "stopped" for event in events) and time.monotonic() < deadline:
        time.sleep(0.01)
    process.stop()

    assert fake.closed and fake.sent == [{"version": 1, "delay": 5}, {"delay": 30}]
    assert [event["event"] for event in events] == ["ready", "paused", "stopped"]


def test_policykit_refusal_is_reported_as_not_authorized() -> None:
    events: list[dict] = []
    process = ip.PriorityProcess(5, events.append, argv=["pkexec", ip.LAUNCHER], popen=lambda *_args, **_kwargs: FakeProcess([], code=126))
    process.start()
    deadline = time.monotonic() + 3
    while len(events) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert events == [{"event": "unavailable", "reason": "not-authorized"}, {"event": "stopped", "code": 126}]


def test_no_way_to_start_is_reported_without_starting_anything() -> None:
    events: list[dict] = []
    started = []
    process = ip.PriorityProcess(5, events.append, popen=lambda *args, **kwargs: started.append(args))
    assert process.start() is False
    assert events == [{"event": "unavailable", "reason": "permission"}] and not started


def test_shipped_launcher_and_policy_name_the_same_fixed_program() -> None:
    root = Path(__file__).resolve().parents[1]
    launcher = root / "usr" / "share" / "big-remote-play" / "scripts" / Path(ip.LAUNCHER).name
    policy = (root / "usr" / "share" / "polkit-1" / "actions" / "br.com.biglinux.remoteplay.input-priority.policy").read_text(encoding="utf-8")

    assert os.access(launcher, os.X_OK)
    assert "/usr/lib/big-remote-play/big_remote_play/host/input_priority.py run" in launcher.read_text(encoding="utf-8")
    assert f'"org.freedesktop.policykit.exec.path">{ip.LAUNCHER}<' in policy
    assert "<allow_any>no</allow_any>" in policy and "<allow_inactive>no</allow_inactive>" in policy
