"""Touch controls: what each mode changes, the checks and the big-keyboard client.

Hermetic: a fake ``/sys/class/input`` tree, a fake session bus and fixed facts.
Nothing touches real input devices, Sunshine or big-keyboard.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from big_remote_play.host import touch_control as tc


# ── modes ─────────────────────────────────────────────────────────────────


def test_unknown_or_missing_mode_is_automatic_and_changes_nothing() -> None:
    assert tc.normalize_mode(None) == tc.normalize_mode("root") == tc.AUTOMATIC
    assert tc.sunshine_settings(tc.AUTOMATIC) == {}  # the person's own Sunshine choice is kept
    assert tc.priority_pauses_touch(tc.AUTOMATIC)


@pytest.mark.parametrize(
    ("mode", "native", "remote", "pauses"),
    [
        (tc.MOUSE, "disabled", "off", True),
        (tc.TOUCH, "enabled", "off", True),
        (tc.GAMEPAD, "enabled", "exclusive", False),
        (tc.HYBRID, "enabled", "shared", False),
    ],
)
def test_each_mode_has_a_real_effect(mode, native, remote, pauses) -> None:
    assert tc.sunshine_settings(mode) == {"native_pen_touch": native}
    assert tc.remote_touch_mode(mode) == remote
    assert tc.priority_pauses_touch(mode) is pauses
    assert tc.uses_controller(mode) is (not pauses)


@pytest.mark.parametrize(("value", "enabled"), [(None, True), ("enabled", True), ("disabled", False), ("false", False), (" Off ", False), ("true", True)])
def test_sunshine_native_touch_defaults_to_on(value, enabled) -> None:
    config = {} if value is None else {"native_pen_touch": value}
    assert tc.native_touch_enabled(config) is enabled


# ── devices and environment ───────────────────────────────────────────────


def _sys_input(root: Path, names: dict[str, str]) -> Path:
    for event, name in names.items():
        (root / event / "device").mkdir(parents=True)
        (root / event / "device" / "name").write_text(name + "\n")
    return root


def test_sunshine_touchscreens_are_found_by_name_only(tmp_path) -> None:
    root = _sys_input(
        tmp_path,
        {
            "event3": "Logitech USB Keyboard",
            "event29": "libvirtualhid Touchscreen",
            "event28": "libvirtualhid Mouse (Absolute)",
            "event12": "ELAN Touchscreen",  # this computer's own touchscreen
            "event9": "Touch passthrough",  # older Sunshine, desktop-wide coordinates
            "event100": "Sunshine (libvirtualhid) Touchscreen",
        },
    )
    assert tc.sunshine_touchscreens(root) == ["/dev/input/event29", "/dev/input/event100"]
    assert tc.sunshine_touchscreens(tmp_path / "missing") == []


def test_desktop_comes_from_the_session_environment() -> None:
    assert tc.desktop_name({"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "wlmaker"}) == ("wayland", "wlmaker")
    assert tc.desktop_name({"XDG_CURRENT_DESKTOP": "KDE:Plasma", "DISPLAY": ":0"}) == ("x11", "kde")
    assert tc.desktop_name({}) == ("", "")


@pytest.mark.parametrize(("name", "valid"), [("DP-1", True), ("HDMI-A-1", True), ("", True), ("a b", False), ("../x", False), ("x" * 40, False), ("DP-1;rm", False)])
def test_output_names_are_validated_before_big_keyboard_sees_them(name, valid) -> None:
    assert tc.valid_output(name) is valid


# ── big-keyboard over D-Bus (fake bus) ────────────────────────────────────


class FakeBus:
    """Records org.gtk.Actions calls and answers like a running big-keyboard."""

    def __init__(self, *, running=True, actions=None, remote_modes=("off", "exclusive", "shared", "mouse")):
        self.running = running
        self.remote_modes = remote_modes
        self.calls: list[tuple[str, object]] = []
        self.actions = (
            actions
            if actions is not None
            else {
                "mode": (True, "", ["gamepad"]),
                "set-gamepad": (True, "b", []),
                "remote-touch": (True, "s", ["exclusive"]),
                "overlay-output": (True, "s", ["DP-1"]),
                "remote-touch-status": (True, "", ["active"]),
                "release": (True, "", []),
            }
        )

    def call_sync(self, name, path, interface, method, args, reply_type, flags, timeout, cancellable):
        from gi.repository import GLib  # type: ignore

        if method == "NameHasOwner":
            return GLib.Variant("(b)", (self.running,))
        self.calls.append((method, args.unpack() if args is not None else None))
        if method == "SetState":
            name, value, _platform = args.unpack()
            enabled, sig, _states = self.actions.get(name, (True, "s", []))
            if name != "remote-touch" or value in self.remote_modes:
                self.actions[name] = (enabled, sig, [value])
            return None
        if method == "Describe":
            enabled, sig, states = self.actions[args.unpack()[0]]
            return GLib.Variant("((bgav))", ((enabled, sig, [GLib.Variant("s" if isinstance(v, str) else "b", v) for v in states]),))
        if method == "DescribeAll":
            return GLib.Variant("(a{s(bgav)})", ({key: (enabled, sig, [GLib.Variant("s" if isinstance(v, str) else "b", v) for v in states]) for key, (enabled, sig, states) in self.actions.items()},))
        return None


gi = pytest.importorskip("gi")


def keyboard(bus, installed=True):
    return tc.BigKeyboard(bus=bus, which=lambda _name: "/usr/bin/big-keyboard" if installed else None)


def test_status_reports_facts_not_settings() -> None:
    assert keyboard(FakeBus(), installed=False).status() == tc.ControllerStatus(installed=False)
    assert keyboard(FakeBus(running=False)).status() == tc.ControllerStatus(installed=True)
    status = keyboard(FakeBus()).status()
    assert status == tc.ControllerStatus(installed=True, running=True, supports_remote_touch=True, visible=True, remote_touch="exclusive", remote_state="active", output="DP-1")


def test_on_a_desktop_without_touch_hybrid_keeps_the_phone_mouse_with_everything_hidden() -> None:
    assert tc.remote_touch_mode(tc.HYBRID, ignores_touch=True) == "mouse"
    assert tc.remote_touch_mode(tc.GAMEPAD, ignores_touch=True) == "exclusive"
    assert tc.remote_touch_mode(tc.HYBRID, ignores_touch=False) == "shared"


def test_the_mouse_mode_falls_back_on_an_older_big_keyboard() -> None:
    bus = FakeBus()
    assert keyboard(bus).configure(remote_touch="mouse", output="DP-2") == "mouse"
    assert ("SetState", ("remote-touch", "exclusive", {})) not in bus.calls
    older = FakeBus(remote_modes=("off", "exclusive", "shared"))
    assert keyboard(older).configure(remote_touch="mouse", output="DP-2") == "exclusive"
    assert [call for call in older.calls if call[0] == "SetState"] == [
        ("SetState", ("remote-touch", "mouse", {})),
        ("SetState", ("remote-touch", "exclusive", {})),
        ("SetState", ("overlay-output", "DP-2", {})),
    ]


def test_an_older_big_keyboard_is_recognised() -> None:
    old = FakeBus(actions={"mode": (True, "", ["hidden"]), "gamepad": (True, "", []), "release": (True, "", [])})
    status = keyboard(old).status()
    assert status.running and not status.supports_remote_touch and not status.visible


def test_opening_the_controller_sets_mode_and_screen_then_shows_it() -> None:
    bus = FakeBus()
    client = keyboard(bus)
    client.configure(remote_touch="exclusive", output="DP-1")
    client.show(True)
    client.release()
    assert bus.calls == [
        ("SetState", ("remote-touch", "exclusive", {})),
        ("SetState", ("overlay-output", "DP-1", {})),
        ("Activate", ("set-gamepad", [True], {})),
        ("Activate", ("release", [], {})),
    ]


def test_invalid_values_never_reach_big_keyboard() -> None:
    bus = FakeBus()
    keyboard(bus).configure(remote_touch="root", output="DP-1; rm -rf ~")
    assert bus.calls == []


# ── checks ────────────────────────────────────────────────────────────────


def facts(mode, *, config=None, touchscreens=(), uinput=True, controller=None, desktop="kde", game_window=False):
    return tc.Facts(
        mode=mode,
        sunshine_config=config or {},
        touchscreens=tuple(touchscreens),
        uinput=uinput,
        controller=controller if controller is not None else tc.ControllerStatus(installed=True, running=True, supports_remote_touch=True, visible=True, remote_state="active"),
        session="wayland",
        desktop=desktop,
        sharing=True,
        game_window=game_window,
    )


def outcomes(result):
    return {check.key: check.outcome for check in result}


def test_controller_mode_ready_with_everything_in_place() -> None:
    result = tc.checks(facts(tc.GAMEPAD, touchscreens=["/dev/input/event29"]))
    assert outcomes(result) == {"sunshine_touch": "ok", "touchscreen": "ok", "controller": "ok", "uinput": "ok", "remote_state": "ok"}
    assert tc.ready(result)


def test_controller_mode_blocks_without_the_pieces_it_needs() -> None:
    missing = tc.checks(facts(tc.GAMEPAD, config={"native_pen_touch": "disabled"}, controller=tc.ControllerStatus(installed=False), uinput=False))
    assert outcomes(missing)["sunshine_touch"] == "fail" and outcomes(missing)["controller"] == "fail" and outcomes(missing)["uinput"] == "fail"
    assert not tc.ready(missing)
    old = tc.checks(facts(tc.GAMEPAD, controller=tc.ControllerStatus(installed=True, running=True)))
    assert outcomes(old)["controller_version"] == "fail"


def test_direct_touch_on_wlmaker_is_reported_as_not_working() -> None:
    result = tc.checks(facts(tc.TOUCH, desktop="wlmaker"))
    assert outcomes(result)["desktop_touch"] == "fail" and not tc.ready(result)
    assert tc.ready(tc.checks(facts(tc.TOUCH, desktop="kde")))


def test_game_window_warns_that_the_phone_cannot_see_the_controller() -> None:
    assert outcomes(tc.checks(facts(tc.GAMEPAD, game_window=True)))["game_window"] == "warn"


def test_waiting_for_a_phone_is_not_a_failure() -> None:
    waiting = tc.ControllerStatus(installed=True, running=True, supports_remote_touch=True, visible=True, remote_state="waiting")
    result = tc.checks(facts(tc.GAMEPAD, controller=waiting))
    assert outcomes(result)["remote_state"] == "warn" and outcomes(result)["touchscreen"] == "info" and tc.ready(result)
    denied = tc.ControllerStatus(installed=True, running=True, supports_remote_touch=True, visible=True, remote_state="denied")
    assert not tc.ready(tc.checks(facts(tc.GAMEPAD, controller=denied)))


def test_mouse_mode_only_reports_sunshine() -> None:
    assert outcomes(tc.checks(facts(tc.MOUSE))) == {"sunshine_touch": "warn"}  # turned off at the next share
    assert outcomes(tc.checks(facts(tc.MOUSE, config={"native_pen_touch": "disabled"}))) == {"sunshine_touch": "info"}
