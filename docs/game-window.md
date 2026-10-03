# Game Window

**Game Window** shares one game's window instead of a whole screen. The other
device sees only the game; the desktop, panels, other windows, pop-ups and
notifications of this computer are never part of the picture, even when they
are drawn on top of the game.

This page explains how that works, what it needs, what it guarantees and what
it does not. The normal steps are in the [user guide](user-guide.md#share-only-a-game-window).

## Why a separate design

Sunshine only captures whole screens. The installed version checked for this
feature (`2026.914.233613`, commit `63d35f7`) has no single-window capture in
any backend:

| Sunshine `capture` | What it reads |
|---|---|
| `kms` | a monitor as the GPU scans it out |
| `x11` | the X screen or a monitor rectangle |
| `wlr` | a wlroots output |
| `kwin` | `zkde_screencast_unstable_v1.stream_output` — a whole output |
| `portal` | the ScreenCast portal restricted to monitors |
| `nvfbc` | the NVIDIA framebuffer |

Cropping a monitor, moving other windows away or hiding the panel would still
send whatever covers the game. Game Window therefore gives Sunshine a screen of
its own that contains nothing but the game.

## How it works

```
game window on the desktop
  ├─ KDE Plasma Wayland: ScreenCast portal, window source → KWin stream_window → PipeWire (DMA-BUF)
  └─ X11: the window's own contents (ximagesrc xid=…) while the desktop composites
        ↓ mirror (GStreamer: pipewiresrc/ximagesrc → videocrop → waylandsink)
private game screen: kwin_wayland --virtual on its own socket and D-Bus
        ↓ Sunshine capture = kwin, WAYLAND_DISPLAY = that socket
hardware encoder → Moonlight
```

- **The window's picture** comes from the desktop's own window capture. On
  Wayland KWin renders only that window off-screen, so windows above it are not
  in the stream. KDE asks the person to confirm the window the first time for
  each game (**Allow restoring on future sessions** keeps that choice) and
  restores it silently afterwards by application id and title.
- **The private game screen** is a headless KWin that runs no shell, panel or
  notification service; the only client is the mirror. Its size is the game's
  size in device pixels (width rounded to a multiple of 8) and, when
  `kscreen-doctor` exists, it switches to a 240 Hz mode so a game frame waits
  about 2 ms instead of up to 16.7 ms for the next repaint.
- **Sunshine** is started with `capture = kwin` and only the private screen's
  socket. With a forced method Sunshine enables no other source, so it has no
  path to the real monitors. `adapter_name` is the private screen's GPU:
  a DMA-BUF made on one GPU cannot be imported by another GPU's encoder.
- **The title bar** is removed with crop metadata that the private screen
  applies (no copy). Fullscreen and borderless games have none.
- **Sound:** only the game's sound is sent (Preferences → Audio → **Send only
  the game's sound**, on by default). Big Remote Play links the game's own
  playback into its private output and Sunshine records that output; other
  programs, notifications and calls are not sent, and this computer still
  hears everything. The game is its window's process and everything started
  under the same game launch (Steam's per-game `reaper`, Lutris's wrapper,
  Wine/Proton and runtime wrappers). See
  [audio architecture](audio-architecture.md#game-window-only-the-games-sound).
- Input, pairing and the app library are unchanged. The Desktop entry of
  `apps.json` is used; no temporary app is created.
- When sharing starts the game window is made active, because Sunshine sends
  keyboard and mouse to the active window (right after **Start sharing** that
  would be Big Remote Play itself), and again every time a device starts
  playing (approving that device happened in Big Remote Play). Many games also
  slow down or mute themselves while they are not the active window; see
  [Game Window audio](window-audio-fix.md).

`host/game_windows.py` lists open windows (KWin scripting on Wayland, EWMH on
X11), reads each window's process tree and names it. `host/window_capture.py`
is the capture helper: a separate process, like Sunshine, that owns the portal
session, the private screen and the mirror, and stops them together.
`host/x11_windows.py` reads X11 windows through `libX11` with `ctypes`.

## Which windows are listed

Installed, running and capturable are different things. The list shows open,
capturable windows whose process tree says they are games:

- Steam (`SteamAppId`, the `reaper` launcher or the `steam_app_<AppID>` window
  class), named from the Steam library;
- Proton and Wine (`wine64-preloader`, `*.exe`, `STEAM_COMPAT_DATA_PATH`);
- Lutris and Heroic launches;
- Gamescope, where the game below the Gamescope window is named;
- native games whose desktop entry has the `Game` category or that run from a
  games folder.

Never listed: this application, Sunshine, Moonlight, the Steam, Lutris and
Heroic windows themselves, Plasma's shell, KRunner, System Settings, portal
dialogs, dialogs, splash screens, panels, notifications and minimized windows
(a minimized window has no picture). **Show all open windows** adds ordinary
application windows (emulators, custom executables), marked as not recognized.

Only a small set of launcher markers is read from a process environment. Window
ids are never saved: the settings keep the source and a game identity
(`steam:<AppID>`, `app:<id>` or `exe:<name>`). The same game is selected again
only when exactly one open window has that identity.

## Safety rules

- No fallback: if the game window cannot be shared, sharing does not start.
- The helper stops showing the game at once when the window closes or crashes,
  the portal session ends, the mirror fails, the private screen exits or, on
  X11, compositing is suspended. The private screen then shows nothing; after
  20 seconds it is removed too. Big Remote Play stops Sunshine and explains:
  **The game window closed** — *Sharing stopped so nothing else on this
  computer is shown.*
- If Big Remote Play was closed meanwhile, the next start stops Sunshine and
  says why.
- The private screen keeps KWin's permission checks and gets no desktop
  display, session bus or Wayland socket.
- Portal restore tokens are stored per game in
  `~/.config/big-remote-play/game-window-portal.json` (owner-only).

## Requirements

- KDE Plasma 6 on Wayland (ScreenCast portal with window sharing), or an X11
  desktop with compositing on.
- Sunshine **v2026.516 or newer**: its KWin capture method is the only way it can read the private screen. An older Sunshine starts without any video source, so devices connect and get sound but never a picture; Big Remote Play checks the version and says so instead.
- `kwin_wayland` (the private screen), GStreamer with `pipewiresrc`
  (`gst-plugin-pipewire`), `waylandsink` (`gst-plugins-bad`) and `videocrop`,
  `ximagesrc` (`gst-plugins-good`). Missing parts are named in the interface.
- Not available in the Flatpak: a sandbox cannot see other applications'
  windows, and Sunshine runs on the host.
- The Nix package does not pull GStreamer in; provide the plugins in the
  environment to use Game Window there.

## Limitations

- **Input goes to the desktop's active window.** Sunshine injects keyboard,
  mouse and controllers through `uinput` for the whole session, as with Full
  Desktop. Game Window activates the game when sharing starts and when a
  device starts playing; if someone at
  this computer switches to another window, keys typed on the other device go
  there, without the other person seeing it. Controllers are read by games
  directly and are not affected.
- **Absolute mouse positions** (touch screens, Moonlight's remote-desktop mouse
  mode) are mapped to the private screen, not to where the game sits on the
  desktop. Games that capture the mouse (relative movement) are not affected.
- **SDR only.** Window capture is 8-bit SDR; HDR games are sent in SDR. The
  HDR and resolution options of **Image and capture** apply to Full Desktop and
  are disabled for Game Window.
- **Size is fixed at start.** If the game changes size, the picture is scaled
  to fit with its aspect ratio kept; start sharing again for a sharp 1:1 picture.
- **Hybrid graphics.** The private screen renders on libdrm's first GPU, and
  Sunshine encodes there. On a machine whose first GPU is an integrated one the
  encoder is the integrated GPU's.
- **X11** copies each frame through the CPU (`ximagesrc`, 60 fps) and stops when
  compositing is suspended, because an uncomposited X server returns whatever
  covers the window.
- A game that shows several top-level windows (a separate launcher, a video
  window) is shared one window at a time.

## Measured results

AMD RX 9060 XT + Renoir iGPU, KDE Plasma 6.7.4 Wayland, Sunshine
`2026.914.233613`, local Moonlight 1280×720 at 60 fps, `vkcube` animating,
30-second sessions (Moonlight's session statistics):

| | Full Desktop (KMS, dGPU) | Game Window (private screen, iGPU) |
|---|---|---|
| Frames received / rendered | 60.29 / 60.02 fps | 60.24 / 60.05 fps |
| Host processing latency (min/avg/max) | 1.0 / 1.3 / 9.6 ms | 2.0 / 2.7 / 6.7 ms |
| CPU: Sunshine | 2.0 % of a core | 1.5 % |
| CPU: capture helper + private screen | — | 1.4 % + 6.5 % |
| CPU: desktop KWin | 11.8 % | 14.2 % |
| Transport | DMA-BUF | DMA-BUF end to end |

Host processing latency starts when Sunshine captures; Game Window adds, before
that, one repaint of the private screen (about 2 ms on average at 240 Hz).
