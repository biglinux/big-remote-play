# Host input priority

**Share → Preferences → Mouse and keyboard → Host has priority.** When the
person sitting at the game PC uses its own mouse or keyboard, the other
device's mouse and keyboard stop for a few seconds; after that pause without
local use, the other device controls them again. Controllers are never
paused.

This page records how input reaches the desktop, why the feature is built
the way it is, and what it does not cover.

## The problem

Sunshine turns the other device's keyboard and mouse into virtual input
devices on this computer. The desktop treats them exactly like the physical
ones, so two people moving the same pointer or typing into the same window
fight over it. Turning Sunshine's **Enable Keyboard/Mouse Input** off would
need a Sunshine restart (the stream drops) and would be all or nothing.

## How input reaches the desktop

Checked on Sunshine `2026.914.233613` (KDE Plasma 6.7.4, kernel 7.2):

| Device (evdev name) | Created by | sysfs location |
|---|---|---|
| `libvirtualhid Keyboard` | Sunshine, at startup (uinput) | `/sys/devices/virtual/input/…` |
| `libvirtualhid Mouse` | Sunshine, at startup (uinput) | `/sys/devices/virtual/input/…` |
| `libvirtualhid Mouse (Absolute)` | Sunshine, at startup (uinput) | `/sys/devices/virtual/input/…` |
| `libvirtualhid Touchscreen`, `libvirtualhid Pen Tablet` | Sunshine, when a device sends touch or pen | `/sys/devices/virtual/input/…` |
| `Sunshine (libvirtualhid) … Controller` | Sunshine, per controller (uhid) | `/sys/devices/virtual/misc/uhid/…` |
| Older Sunshine (inputtino): `Keyboard passthrough`, `Mouse passthrough`, `Mouse passthrough (absolute)`, `Touch passthrough`, `Pen passthrough` | uinput | `/sys/devices/virtual/input/…` |

All of them are `root:input 0660`. Sunshine's udev rules grant the desktop
user access only to `/dev/uinput`, `/dev/uhid` and the controllers.

KWin (through libinput) opens every evdev node like any other reader. The
kernel's **`EVIOCGRAB`** gives one reader exclusive delivery of a device:
while it is held, the desktop receives nothing from that device, and the
device keeps working for its creator. Releasing it (or the holder exiting,
or crashing) restores normal delivery at once. Nothing in Sunshine is
restarted or reconfigured.

### Measured on this machine (uinput keyboard named like Sunshine's)

| Step | What the desktop received |
|---|---|
| Guest holds **W** | `W` down |
| Host activity: write a **W** release into the device, then grab | `W` up (the key is not left held) |
| Guest keeps typing while grabbed | nothing |
| Clear the kernel's key state silently (still grabbed), release the grab | nothing |
| Guest presses **W** again | `W` down |
| Same, **without** the silent clear | nothing — the kernel drops a press for a key it believes is still down |

Two consequences shaped the design:

1. **No stuck keys.** Before pausing, every key and button the virtual device
   holds (`EVIOCGKEY`) is released *into* the device, so the desktop and the
   game see an ordinary release: a held `W`, `Ctrl`, `Alt`, `Shift`, `Super`,
   a mouse button or a drag ends cleanly.
2. **The other device's next press must work.** Keys pressed during the pause
   are released silently (only the grab holder sees it) before the grab is
   dropped; otherwise the kernel would swallow that key's next press.

## Physical or virtual?

A device is the other device's when its name is one of Sunshine's keyboard,
mouse, touch or pen names above **and** it lives under
`/sys/devices/virtual/input/`. Sunshine's controllers are never touched.

Local activity is any other keyboard (it has letter keys) or pointer (relative
X/Y, or an absolute pointer with touch or buttons): USB, PS/2, I²C touchpads,
Bluetooth keyboards and mice — including Bluetooth LE ones, which Linux also
creates through uhid under `/sys/devices/virtual`, which is why the location
alone is not used to decide. Power buttons, lid switches, sound-card jacks,
`Video Bus` and controllers are ignored. Keys, buttons, motion, scroll and
touch count as activity; LEDs, sync and timestamps do not.

Hot-plugged devices (USB, Bluetooth) are picked up through `inotify` on
`/dev/input`; Sunshine restarting creates new virtual devices, which are
found the same way and grabbed again if a pause is in progress.

## Design

```text
Share starts (setting on) ──► input-priority helper (separate process)
                                │ reads physical keyboards/mice (read only)
                                │ grabs Sunshine's keyboard/mouse while paused
                                ▼
           JSON lines on stdout: ready / host / guest / unavailable
                                ▼
HostView: "You're controlling this PC" row · KDE on-screen message
```

- **Event driven.** One `select()` over the device nodes, `/dev/input`
  (`inotify`) and stdin. Its timeout is the time left in the pause; while the
  other device has control it sleeps until something happens. No polling and
  no work per mouse movement beyond reading the event.
- **Pause:** local activity → release held keys/buttons into Sunshine's
  devices → grab them → the deadline is *now + delay*. Every later local event
  only moves the deadline.
- **Resume:** deadline reached → silently clear the grabbed devices' key
  state → release the grab.
- **Lifetime:** the helper exits when Big Remote Play closes its stdin (stop
  sharing, setting off, application closed or crashed). The kernel drops a
  grab when its holder exits, so the other device can never stay locked out.
- **Logs:** one line when a pause starts, one when it ends, and device
  counts; never key codes or individual events.

### Permissions

Reading physical keyboards means reading what is typed, so the helper never
reports keys: only "paused" and "resumed", at most once per pause. That is the
same information any Wayland client gets from the idle-notification protocol.

- When the desktop user can already open the input devices (member of the
  `input` group), the helper runs as that user.
- Otherwise it runs through PolicyKit (`pkexec`) as the fixed, installed
  program `/usr/share/big-remote-play/scripts/input-priority-helper`, under the
  action `br.com.biglinux.remoteplay.input-priority` (allowed for the active
  local session without a password; denied for inactive and remote sessions).
  It accepts only a delay in seconds on stdin and touches no file.
- Running from a source checkout without that program and without access, the
  setting explains that it needs the installed package.

### Telling the people

- **This computer:** in **Share → Overview → Connected now**, one row:
  *You're controlling this PC* while paused, *The other device can use the
  mouse and keyboard* otherwise. It changes in place; there are no toasts.
- **The other device:** Big Remote Play has no channel to the Moonlight
  window, and opening one only for this would mean a new network listener. In
  **Full Desktop** the message is drawn where the other person already looks:
  KDE Plasma's own on-screen display (`org.kde.osdService.showText`), once when
  the pause starts (*Mouse and keyboard are being used on the host PC*) and
  once when it ends (*You can control the mouse and keyboard again*), at most
  every few seconds. In **Game Window** the other person sees only the game, so
  no message is shown there.

## Verified

On the development machine (2026-10-06), the real helper against the real
kernel and KWin, with synthetic devices so nothing was typed or clicked (a
uinput keyboard named like Sunshine's, a uinput mouse moved 1 px and back; a
plain evdev reader stood in for the desktop): 7 Sunshine devices (two Sunshine
instances and the synthetic one) and 3 local devices were told apart, sound
jacks, power buttons and consumer-control keys were ignored; the pause started
1 ms after the first movement, a held key was released to the desktop, keys
typed during the pause did not reach it, a second movement restarted the
count, the guest's next press after the pause worked, a delay change applied
at once, and closing the helper's input during a pause gave the devices back.

Automated (hermetic, no device opened): `tests/test_input_priority.py` and
`tests/test_input_priority_ui.py`.

Still needs real hardware and people: a real Moonlight session, Bluetooth and
hot-plugged USB devices, X11, a user outside the `input` group with the
package's PolicyKit rule, and the on-screen message seen in a Full Desktop
stream; see [release acceptance](release-testing.md).

## Limitations

- Needs Sunshine's virtual devices (uinput). Sunshine's X11 XTest fallback
  injects through the X server and cannot be paused; the setting says so.
- A key the other person keeps holding through the pause must be pressed again
  afterwards.
- Absolute positions (touch, Moonlight's remote-desktop mouse) jump to wherever
  the other device points next.
- Steam Input can turn a controller into a virtual mouse or keyboard; activity
  on such a device counts as local.
- The automated tests do not prove real hardware, Bluetooth timing or every
  desktop; see the release checks.
