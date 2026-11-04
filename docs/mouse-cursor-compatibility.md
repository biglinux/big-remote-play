# Mouse pointer compatibility

Why the other device may not see this computer's mouse pointer, what Big Remote Play checks, and what is still unverified. Steps for people are in [troubleshooting](troubleshooting.md#the-other-device-does-not-see-the-mouse-pointer).

## Where the pointer comes from

In Moonlight's normal (game) mouse mode, Moonlight Qt uses SDL's relative mouse mode: the other device hides its own pointer and shows only the pointer that **this computer draws into the video**. A pointer that is missing from the picture is therefore a capture problem on the sharing computer; the mouse itself usually still works. In “Optimize mouse for remote desktop instead of games” mode Moonlight sends absolute positions and can show its own pointer with Ctrl+Alt+Shift+C (Moonlight Qt `app/streaming/input/keyboard.cpp`).

## Capture methods and the pointer

With **Capture method** set to **Automatic**, Big Remote Play leaves Sunshine's `capture` option unset and Sunshine tries NvFBC, wlroots, KMS, X11, KWin and the portal in that order (Sunshine `src/platform/linux/misc.cpp`). The Arch/BigLinux package gives `/usr/bin/sunshine` the `cap_sys_admin` capability that KMS needs, so on KDE Plasma and GNOME Sunshine normally ends up on **KMS**, on Wayland and also on X11. Observed on the development machine (X11 session, Sunshine 2026.914): the log says `Screencasting with KMS` and `Found cursor plane [105]`.

| Capture | How the pointer gets into the picture | When it is lost |
|---|---|---|
| `kms` | Reads the driver's hardware cursor plane and draws it into the frame (`kmsgrab.cpp`) | No cursor plane, no atomic mode-setting, an unsupported cursor format or modifier; Sunshine logs it (below). The pointer is not scaled. |
| `kwin` | Asks KWin to embed the pointer in its screen cast (`POINTER_EMBEDDED`, `kwingrab.cpp`) | KDE Plasma on Wayland only; needs KWin's permission for Sunshine, which Sunshine's package provides |
| `portal` | Asks the desktop portal for an embedded pointer (`cursor_mode=EMBEDDED`, `portalgrab.cpp`) | The desktop asks once which screen to share |
| `x11` | Draws the pointer with XFixes (`x11grab.cpp`) | X11 sessions only |
| `wlr` | The compositor's overlay cursor option | Only wlroots compositors; KWin and Mutter do not offer it |

Sunshine's Ctrl+Alt+Shift+N (sent from Moonlight) toggles drawing the pointer for `kms`, `x11` and `wlr`; PipeWire-based captures (`kwin`, `portal`) ignore it (`pipewire.cpp`, “show_cursor is ignored”).

The KMS messages Big Remote Play looks for (Sunshine 2026.914):

- `No KMS cursor plane found. Cursor may not be displayed while streaming!`
- `Atomic mode-setting must be enabled to capture the cursor!`
- `Unsupported cursor format` / `Unsupported non-linear cursor modifier`
- `Failed to map cursor FB` / `Failed to mmap cursor FB`

## What the app does

`host/cursor_check.py` reads the last 256 KiB of Sunshine's log a few seconds after sharing starts and whenever someone joins or leaves. Only lines after the last `Screencasting with …` choice count, so an earlier capture's problem is not reported after it was fixed. When the capture in use is KMS and one of the messages above is present, **Share → Overview → Needs attention** shows **The other device may not see the mouse pointer** and, if this desktop has a capture that embeds the pointer, **Use it**:

| Session | Recommended capture |
|---|---|
| KDE Plasma, Wayland | `kwin` |
| GNOME or another portal desktop, Wayland | `portal` |
| Any desktop, X11 | `x11` |

**Use it** asks first, stores the choice as `pointer_capture` in the application settings (not in Sunshine's own file) and restarts sharing; Sunshine then receives that `capture` value whenever **Capture method** is Automatic. A manual **Capture method** always wins. **Preferences → Mouse, keyboard and controllers → Use automatic** removes the choice. **Support → Check this computer** shows the same result and whether Sunshine can open `/dev/uinput` (needed for the remote mouse to move at all).

The app never changes the desktop's own cursor settings (`KWIN_FORCE_SW_CURSOR`, `MUTTER_DEBUG_DISABLE_HW_CURSORS`), KWin or Mutter configuration, or input devices, and never draws a fake pointer.

## Symptoms

| What the person sees | Most likely cause | What to do |
|---|---|---|
| The pointer moves things but is invisible | The capture does not draw it (KMS cursor plane, above) | **Use it** in Needs attention; Ctrl+Alt+Shift+N in Moonlight |
| The pointer is visible but does not move | Host input priority paused the other device (it says so in **Connected now**), or Sunshine cannot create its virtual mouse (`/dev/uinput`) | Wait for the pause to end; **Support → Check this computer** |
| It moves but clicks do nothing | The same pause (buttons are released first), or a game that grabs the mouse | As above; Sunshine's log at debug level shows `[libvirtualhid]` |
| It appears in the wrong place | Absolute mode with several monitors, or Game Window's private screen | Moonlight's game mouse mode for Game Window |
| Missing only in some games | The game hides or confines the pointer itself | Nothing to fix on the sharing computer |
| Works on X11, missing on Wayland | KMS on a driver whose cursor plane is not readable | **Use it** (KWin or portal) |
| Missing only with Game Window | The window stream embeds the pointer only when the portal offers it (`window_capture.py`) | See [Game Window](game-window.md) |
| Appears after moving this computer's own mouse | Upstream [Sunshine #5293](https://github.com/LizardByte/Sunshine/issues/5293) describes this on **Windows 11** (DWM cursor); no Linux reproduction is known | Not addressed by the app |

## Checked with real streams (2026-11-04)

Sunshine 2026.914 (`cap_sys_admin` set by the package) in live sessions of BigLinux 2026-10-03 (KDE Plasma 6.7, Wayland) and Big GNOME 2026-11-03 (GNOME Shell 50.5, Wayland), each a QEMU/KVM virtual machine with a virtio GPU, streamed with Moonlight Qt 6.2.0 from the host:

| Sharing session | Automatic capture | Sunshine's log | Pointer in the stream | After **Use it** |
|---|---|---|---|---|
| KDE Plasma, Wayland | KMS | `No KMS cursor plane found` | not visible (moves things) | `kwin`: visible, follows the mouse, clicks work |
| GNOME, Wayland | KMS | `No KMS cursor plane found` | not visible (moves things) | `portal`: visible, follows the mouse, clicks work |
| WindowMaker, X11 (development PC, AMD RX 9060 XT) | KMS | `Found cursor plane [105]` | not affected | — |
| GNOME, Wayland, hybrid Intel HD 630 + GTX 1050 Ti (real notebook) | KMS + NVENC | `Found cursor plane [56]` | no picture at all: `Couldn't find monitor [0]` (see below) | — |

The two Wayland desktops showed the reported symptom with Sunshine's own automatic choice, and the capture the app recommends fixed it on both. The app's diagnosis appeared in **Needs attention** in both sessions. A virtual GPU has no hardware cursor plane at all, so these runs reproduce the cause but not every real driver: a real AMD, Intel or NVIDIA card may or may not expose a readable cursor plane under KWin or Mutter. Run the [release acceptance](release-testing.md) matrix on real hardware before calling a given driver fixed.

Observed with GNOME's portal: each time Sunshine starts, GNOME shows **Remote desktop** and asks which screen to share; until it is answered, Sunshine opens no port. Share then says **Not responding** with what to do, instead of claiming that devices can connect. Leaving **Permitir interação remota** (allow remote interaction) off is enough for the pointer to move and click: Moonlight's input goes through Sunshine's own virtual devices, not through the portal.

### A real hybrid notebook (Intel + NVIDIA)

Dell notebook with Intel HD 630 (driving the built-in eDP screen) and GeForce GTX 1050 Ti Mobile (driving HDMI), driver 580.178.04, BigCommunity GNOME 50.5 on Wayland with BigCommunity's own portal (`xdg-desktop-portal-big`), Sunshine 2026.914, 2026-11-04:

- with automatic capture, after both desktop questions were answered (share the screen, allow control), Sunshine used **KMS**, found the real **cursor plane** (`Found cursor plane [56]`) and the **NVENC** H.264/HEVC encoders: the pointer itself is captured on this hardware;
- but the stream to Moonlight ended at once: Sunshine logged `Couldn't find monitor [0]` and Moonlight `No video traffic was ever received from the host!`. Sunshine's KMS list shows one screen per card (eDP-1 on the Intel, HDMI-A-1 on the NVIDIA), and capturing on one card while encoding on the other does not work there. This is a Sunshine limitation on hybrid notebooks, not the pointer problem;
- BigCommunity's portal answered `RemoteDesktop Start: no streams in response`, so portal capture is not an alternative on that desktop yet;
- answered more than about two minutes late, the desktop questions left Sunshine without its capture permission: every encoder failed (`Unable to find display or encoder during startup`) while its ports were open. Share now shows **The picture could not start** with **Restart sharing** instead of claiming that devices can connect.

So on hybrid notebooks the first question is whether any picture arrives at all; a working combination of capture and encoder still has to be found per model (Sunshine's `adapter_name`/`output_name`, or a portal that returns streams) before the pointer can be judged there.

The automated tests (`tests/test_cursor_check.py`, `tests/test_health_checks.py`) cover log parsing (only the current Sunshine run counts), the recommendation table and the check states.
