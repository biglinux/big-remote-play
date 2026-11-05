# Several screens: where the game opens and which screen is shared

Two different choices, on two different computers:

| Choice | Where | What it does |
|---|---|---|
| **Open on** | Connect → Image settings → Playback on this computer | The screen of **this** computer where Moonlight shows the game |
| **Monitor / Display** | Share → Preferences → Hardware and Capture, on the game PC | The screen of the **game PC** that is sent |

## Open on (this computer)

Moonlight has no setting for the screen. It opens the stream on the screen of its own window: its log says `Qt UI screen is at (6880,0)` and then `SDL found matching display 1`. On X11 Big Remote Play places that window with Qt's standard `-qwindowgeometry` option, so the stream opens on the chosen screen in every display mode.

Verified on an X11 desktop with three monitors (3440×1440, 3440×1440, 2560×1080): with **Open on** set to the 2560×1080 screen, the borderless stream window was 2560 pixels wide at x = 6880; with the middle screen, 3440 pixels wide at x = 3440.

- The list shows the number, connector, resolution and the main screen.
- The choice is saved. When that screen is unplugged, the row says so and Moonlight opens on the main screen; the choice comes back when the screen is connected again.
- On Wayland the desktop places windows and an application cannot choose the screen, so the row is shown but not offered: Moonlight opens on the screen you are using (usually the one with the pointer). Moving the Moonlight window to another screen and switching to full screen there (Ctrl+Alt+Shift+X twice) works on any desktop.

## Monitor / Display (the game PC)

The chosen screen is Sunshine's `output_name`. Sunshine's KMS capture, which it uses even in X11 sessions when it can, finds the screen by its kernel connector name (`DP-2`); X11 drivers name the same output differently (`DisplayPort-1`). Big Remote Play pairs the two names by the screen's EDID and writes the kernel name, so the chosen screen is the one shared. Each screen is listed once.

## One entry per screen in Moonlight: not possible with one Sunshine

It would be convenient to see *Desktop — Screen 1*, *Desktop — Screen 2* in Moonlight. Sunshine (checked on 2026.914) does not support it:

- `output_name` is one setting for the whole server, read when Sunshine starts;
- an app's `output` field is the file where its command output is written, not a screen;
- an app's `prep-cmd` runs before the stream but cannot change which screen Sunshine captures.

Several *Desktop* entries would all show the same screen, so Big Remote Play does not create them.

The way that would work is one Sunshine per screen, each with its own port, name and pairing, shown in Moonlight as separate computers (*Game PC — Screen 2*). That needs pairing each screen, more firewall ports and a decision on which one carries the sound. It is a future design, not part of the app today. Until then, choose the screen in Share; if sharing is on, stop and start sharing to apply it.
