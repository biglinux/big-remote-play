"""Controllers of the person connecting: what keeps them out of games here (read-only)."""

from __future__ import annotations

import os
from pathlib import Path

from big_remote_play.host import controllers

REAL_CONTROLLER_REPORT = controllers.controller_report  # conftest replaces it with an empty report

# /proc/bus/input/devices as the kernel writes it: this computer's own
# controller, Sunshine's virtual ones (uinput and uhid) and Steam Input's.
DEVICES = """I: Bus=0003 Vendor=045e Product=02dd Version=0203
N: Name="Microsoft X-Box One pad (Firmware 2015)"
P: Phys=usb-0000:04:00.0-8/input0
S: Sysfs=/devices/pci0000:00/0000:00:02.1/0000:04:00.0/usb1/1-8/1-8:1.0/input/input11
U: Uniq=
H: Handlers=event10 js0
B: EV=20000b

I: Bus=0003 Vendor=045e Product=028e Version=0114
N: Name="Sunshine (libvirtualhid) X-Box 360 Controller"
P: Phys=
S: Sysfs=/devices/virtual/input/input43
H: Handlers=event20 js1

I: Bus=0005 Vendor=045e Product=0b13 Version=0513
N: Name="Sunshine (libvirtualhid) X-Box Series Controller"
S: Sysfs=/devices/virtual/misc/uhid/0005:045E:0B13.0006/input/input32
H: Handlers=event21 js2

I: Bus=0003 Vendor=28de Product=11ff Version=0001
N: Name="Microsoft X-Box 360 pad 0"
S: Sysfs=/devices/virtual/input/input50
H: Handlers=event22 js3

I: Bus=0005 Vendor=054c Product=0ce6 Version=8100
N: Name="DualSense Wireless Controller"
S: Sysfs=/devices/pci0000:00/0000:00:14.0/usb1/1-6/1-6:1.0/bluetooth/hci0/hci0:256/0005:054C:0CE6.0003/input/input60
H: Handlers=event23 js4

I: Bus=0003 Vendor=046d Product=c52b Version=0111
N: Name="Logitech USB Keyboard"
S: Sysfs=/devices/pci0000:00/0000:00:14.0/usb1/1-3/1-3:1.0/input/input5
H: Handlers=sysrq kbd leds event5
"""


def test_only_physical_controllers_count_as_this_computers_own():
    assert controllers.local_controllers(DEVICES) == ["Microsoft X-Box One pad (Firmware 2015)", "DualSense Wireless Controller"]
    assert controllers.local_controllers("") == []


def test_devices_sunshine_cannot_open_are_named(tmp_path):
    allowed = {"/dev/uinput"}
    assert controllers.unusable_devices(access=lambda path, mode: path in allowed and mode == os.R_OK | os.W_OK) == ["/dev/uhid"]


def test_an_unreadable_device_list_means_no_local_controller():
    def unreadable(path: Path) -> str:
        raise PermissionError(path)

    assert REAL_CONTROLLER_REPORT(unreadable).local == ()
    assert REAL_CONTROLLER_REPORT(lambda path: DEVICES).local == ("Microsoft X-Box One pad (Firmware 2015)", "DualSense Wireless Controller")


# ------------------------------------------- what Sunshine did in this connection

import datetime as _dt

from big_remote_play.host.controllers import ConnectionControllers, ControllerReport, connection_controllers, controllers_disabled
from big_remote_play.ui.host_view import CONTROLLER_GRACE_SECONDS, controller_connection_text

_LOG = """[2026-10-05 15:50:00.000]: Info: CLIENT CONNECTED
[2026-10-05 15:50:10.000]: Info: Gamepad 0 will be Old Pad (manual selection)
[2026-10-05 15:51:00.000]: Info: CLIENT DISCONNECTED
[2026-10-05 15:54:40.100]: Info: CLIENT CONNECTED
[2026-10-05 15:54:44.218]: Info: Gamepad 0 will be Sunshine (libvirtualhid) X-Box 360 Controller (manual selection)
[2026-10-05 15:54:50.000]: Info: Gamepad 1 will be Sunshine (libvirtualhid) DualSense (auto-selected by client-reported type)
"""


def test_only_the_current_connections_controllers_are_reported():
    found = connection_controllers(_LOG)
    assert found == ConnectionControllers(
        _dt.datetime(2026, 10, 5, 15, 54, 40),
        ("Sunshine (libvirtualhid) X-Box 360 Controller", "Sunshine (libvirtualhid) DualSense"),
        False,
    )
    assert connection_controllers(_LOG + "[2026-10-05 15:56:00.000]: Info: CLIENT DISCONNECTED\n") is None
    assert connection_controllers("") is None


def test_a_device_failure_in_the_connection_is_reported():
    log = "[2026-10-05 15:54:40.100]: Info: CLIENT CONNECTED\n[2026-10-05 15:54:41.000]: Error: Failed to open /dev/uhid: Permission denied\n"
    assert connection_controllers(log).failed is True


def test_controllers_turned_off_in_sunshine_are_seen():
    assert controllers_disabled("port = 47989\ncontroller = disabled\n")
    assert controllers_disabled("controller=false")
    assert not controllers_disabled("controller = enabled\n")
    assert not controllers_disabled("# controller = disabled\n")


def test_the_share_page_says_what_happened_to_the_other_computers_controller():
    started = _dt.datetime(2026, 10, 5, 15, 54, 40)
    soon = started + _dt.timedelta(seconds=CONTROLLER_GRACE_SECONDS - 1)
    later = started + _dt.timedelta(seconds=CONTROLLER_GRACE_SECONDS + 1)
    arrived = ControllerReport(connection=ConnectionControllers(started, ("Pad",)))
    none_yet = ControllerReport(connection=ConnectionControllers(started))
    failed = ControllerReport(connection=ConnectionControllers(started, failed=True))
    assert "Pad" in controller_connection_text(arrived, now=later)[1]
    assert controller_connection_text(none_yet, now=soon) is None  # give it time to arrive
    assert controller_connection_text(none_yet, now=later) is not None
    assert controller_connection_text(failed, now=soon) is not None
    assert controller_connection_text(ControllerReport(disabled=True)) is not None
    assert controller_connection_text(ControllerReport()) is None  # nobody connected
    assert controller_connection_text(None) is None


def test_inside_flatpak_the_hosts_devices_are_not_judged():
    from big_remote_play.host.controllers import unusable_devices

    assert unusable_devices(access=lambda _p, _m: False, sandboxed=True) == []
    assert unusable_devices(access=lambda _p, _m: False, sandboxed=False) == ["/dev/uinput", "/dev/uhid"]
