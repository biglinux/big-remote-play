# Controlling the game PC from a phone

You are playing on a phone or tablet with Moonlight and need a mouse click, the keyboard or a way out. This page lists what each Moonlight app really does. Everything here was checked against Moonlight's own guide and the source of Moonlight for Android and for iPhone/iPad. Big Remote Play shows the same list under **Connect → Shortcuts & Instructions**, where the **On the Phone** tab has a QR code that opens this page.

Big Remote Play does not run on the phone and cannot add buttons to Moonlight there. The keys and gestures below are Moonlight's own.

## Mouse by touch (Android and iPhone/iPad)

In trackpad mode (Android: Moonlight setting **Use the touchscreen as a trackpad**; iPhone/iPad: touchpad mode):

| Gesture | Result |
|---|---|
| Slide one finger | Move the pointer |
| Tap with one finger | Left click |
| Hold one finger, then tap with a second | Right click |
| Hold one finger for half a second, then move it | Click and drag (move a window, select text) |
| Drag two fingers up or down | Scroll |
| Tap with three fingers | Show or hide the keyboard |

In touchscreen mode a tap clicks where you touch and a long press is a right click. On iPhone/iPad two fingers also zoom and move the picture.

## The mouse pointer

The pointer on the phone is drawn by the game PC into the picture; Moonlight on a phone has none of its own. If taps click but no pointer is visible, the game PC's screen capture leaves it out: see [the other device does not see the mouse pointer](troubleshooting.md#the-other-device-does-not-see-the-mouse-pointer). In an X11 session Big Remote Play uses a capture that draws it.

## Keyboard

**iPhone/iPad.** The keyboard opened with three fingers has a bar of extra keys: **Win, Esc, Tab, Shift, Ctrl, Alt, Delete**. Win, Shift, Ctrl and Alt stay pressed until you tap them again. For Ctrl+Alt+Delete, tap Ctrl, then Alt, then Delete.

**Android.** Three fingers open the phone's own keyboard, which has no Ctrl, Alt, Esc or function keys. For those, use a keyboard app that has them or a Bluetooth/USB keyboard.

**Any phone with a Bluetooth or USB keyboard.** Every key and shortcut (Ctrl+C, Alt+Tab, F5) goes to the game PC.

## Leaving the game

| Phone | Leave the game (it keeps running on the game PC) | Close the game on the game PC |
|---|---|---|
| Android | Back button | Moonlight's list of apps of that computer → **Quit Session** |
| iPhone/iPad | Swipe from the left edge of the screen | Moonlight's list of apps → **Quit Session** |
| Apple TV | Press Menu twice on the remote | **Quit Session** |

## Controllers

A controller connected to the phone works as a controller on the game PC. Moonlight for Android adds:

| Buttons | Result |
|---|---|
| Start + Select | The guide (home) button |
| L1 + Start | Select, on controllers without that button |
| Hold Start | Use the controller as a mouse: a stick moves the pointer, A clicks, B right-clicks |

Moonlight for Android can also draw a controller on the screen: setting **Show on-screen controls**.

## Moonlight on a computer

Moonlight on a computer handles these keys itself; they are not sent to the game PC.

| Keys | Result |
|---|---|
| Ctrl+Alt+Shift+Q | Leave the game (it keeps running on the game PC) |
| Ctrl+Alt+Shift+Z | Release or capture the mouse and keyboard |
| Ctrl+Alt+Shift+X | Switch between full screen and a window |
| Ctrl+Alt+Shift+D | Minimize the game window |
| Ctrl+Alt+Shift+S | Show or hide the connection statistics |
| Ctrl+Alt+Shift+M | Switch the mouse between game mode and desktop mode |
| Ctrl+Alt+Shift+V | Type the text copied on this computer into the game PC |
| Ctrl+Alt+Shift+C | Show this computer's pointer (desktop mouse mode) |
| Ctrl+Alt+Shift+L | Keep the pointer inside the game window (desktop mouse mode) |
| Ctrl+Alt+Shift+N | Show or hide the game PC's pointer in the picture (Sunshine handles it) |

## Sources and limits

- Moonlight setup guide (keyboard shortcuts and touch gestures): <https://github.com/moonlight-stream/moonlight-docs/wiki/Setup-Guide>.
- Moonlight for Android: three-finger tap and Back handling in `Game.java`; the gamepad combinations and settings in its strings.
- Moonlight for iPhone/iPad: the keyboard's extra-keys bar in `StreamView.m`.
- A virtual panel of keys on the phone would have to be part of the Moonlight app there. A separate service on the game PC that types keys sent from a phone would need its own authentication and pairing, and it is not provided.
