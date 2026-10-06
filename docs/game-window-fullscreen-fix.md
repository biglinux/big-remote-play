# Game Window: black picture after fullscreen or a resolution change

Investigation of reports that the other device's picture turned black when
a game shared with **Game Window** switched between windowed and fullscreen
or changed resolution, and what was changed. The feature itself is described
in [Game Window](game-window.md).

## Evidence

Big Remote Play's own log on the development machine (KDE Plasma 6.7.4
Wayland, Sunshine `2026.914.233613`, 2026-09-30 to 2026-10-05):

- 22 sessions ended with `the game picture stopped (portal-closed)`, followed
  seconds later by `the chosen window is no longer open`, including sessions
  of Cyberpunk 2077 and Street Fighter V (Proton).
- Cyberpunk 2077 (2026-10-01): shared at 2560×1110; 20 s later the window
  picture changed to 1920×1110 while the private screen stayed 2560×1080. The
  person stopped sharing; two immediate restarts were refused with
  `different-window (stream 1920x1110, window 2560x1110)`.

Reproduced on the same machine with a test window that can go fullscreen,
change size or recreate itself (no portal capture involved, so nothing
private was recorded):

| Change | KWin window id | Size reported by KWin |
|---|---|---|
| Windowed → fullscreen | **same** | 800×480 → 3440×1440, no title bar |
| Fullscreen → windowed | **same** | back to 800×480 with title bar |
| Window destroyed and created again (what Wine/Proton, Unity and some SDL games do on a mode switch) | **new id**; `getWindowInfo(old)` answers `{}` | — |

And on an isolated rig (its own Sunshine on another port, its own Moonlight
settings, a private screen showing a synthetic picture, no portal — nothing of
the desktop was captured):

| Step | Result |
|---|---|
| The picture changes size inside the same stream (800×480 → 3440×1440) | The mirror keeps going, scaled into the private screen |
| The private screen changes mode while the mirror is connected | **The mirror crashes**: GStreamer 1.28.6's Wayland sink segfaults in its display thread (`libgstwayland` output listener, `g_mutex_lock`) when its output reports a new mode; the stream turns black |
| Mirror disconnected → mode changed → mirror connected again | Sunshine keeps the session, re-creates its encoder for the new size by itself, and the same Moonlight session shows the picture again |
| No mirror at all for about a minute | Sunshine keeps the session; the picture returns when the mirror does |

Also found: the private screen's KWin saved its output layout into the
desktop's own `~/.config/kwinoutputconfig.json` (a `Virtual-0` entry, written
by two compositors) and the next private screen came back with an earlier
game's mode instead of the requested size.

## Root causes

1. **The game replaced its window.** KWin's window capture follows one window
   object. When the game destroys it and opens another, KWin ends that stream
   and the portal closes the session (`portal-closed`), or the 1-second watch
   finds the id gone (`window-closed`). The helper then did what the privacy
   rule demands — it stopped showing anything — and the application stopped
   Sunshine. The other device saw a black picture and then lost the stream,
   although the same game was running in a new window.
2. **The picture changed size inside the same window.** Fullscreen and
   resolution changes renegotiate the PipeWire stream to a new size. The
   private screen kept the size it had at start, the title-bar crop was
   computed from the size at start (wrong as soon as the size changed), and
   the decoration sizes were never read again. Depending on the change, the
   picture was scaled into a much smaller screen, cropped wrongly or, while
   the stream renegotiated, absent.
3. **A restart right after such a change was refused.** The window list is
   refreshed every 8 s; starting from a list read before the change compared
   the new stream with the old size and failed as `different-window`.

4. **Changing the private screen's size kills the mirror** while it is
   connected (the GStreamer crash above), so the screen cannot simply follow
   the game without disconnecting the mirror first.
5. **The private screen did not get the size it asked for**, because it read
   and wrote the desktop's KWin output settings.

There was no fallback to the desktop in any of these: the failures were
"black and stop", never "show something else".

## What changed

All of it lives in the capture helper (`host/window_capture.py`); the
desktop is still never a fallback.

- **Reacquisition.** When the stream or the window goes away
  (`portal-closed`, `window-closed`, mirror error or end of stream) and the
  helper was not asked to stop, it enters *reacquiring* instead of ending:
  the private screen stays (black, nothing else exists there) and Sunshine
  keeps the session. For up to 15 seconds it looks for the **same game**:
  - windows that belong to the original game's process family (the window's
    process, the game's launcher root and everything started below it), or that
    carry the same game identity (`steam:<AppID>`, `app:<id>`, `exe:<name>`);
  - exactly one such open, non-minimized window — two candidates are never
    guessed between;
  - and exactly one open window with that application id on the desktop,
    because KDE restores a saved permission by application id: with two, the
    portal could choose the other one.
  It then opens a new portal session with the game's saved permission, checks
  that the new picture has the new window's shape, rebuilds the mirror and
  gives the game the keyboard again. Otherwise the session ends as before,
  with the reason explained.
- **Size follows the game.** When the picture changes size, the helper waits
  0.8 s for the size to settle, reads the window's size, title bar and
  fullscreen state from KWin, recomputes the crop (one scale per axis, for
  games Xwayland shows scaled on one axis), and, if the private screen needs
  another size, disconnects the mirror, switches the screen to that size (the
  same `kscreen-doctor` path that sets its 240 Hz mode) and connects the mirror
  again to the same portal session. About a second of black, then a 1:1
  picture.
- **The private screen has its own settings** folder in the session's runtime
  directory, so it no longer writes the desktop's `kwinoutputconfig.json` and
  always starts at the size it asks for.
- **No silent black picture.** A light watchdog uses the frame counter that
  already existed: the window exists and is not minimized, but no picture
  arrived for 10 seconds after the mirror (re)connected or the size changed →
  the stream is treated as lost and opened again, once per window, so a game
  that simply draws nothing cannot keep reopening the portal. No pixel is
  inspected.
- **Starting right after a change.** The size check at start compares the
  stream with the window's size read *now* from KWin, not with the list, and
  accepts a picture that matches one side exactly (the Xwayland scaling of the
  Cyberpunk 2077 case).
- The application treats *reacquiring* as running and shows
  *Reconnecting to the game window…*; the other device keeps its session.

## Checked how

`tests/test_window_capture.py` (finding the replacement, never guessing
between two, the same application rule, a share stopped from KDE, reconnecting
the mirror only after the mode change, the once-per-window watchdog, the
isolated settings) and `tests/test_game_window_focus.py` (Share keeps sharing
while the helper reconnects and focuses the new window). The portal part of a
reacquisition — KDE restoring the saved permission for the new window — needs a
real game and a person; it is in [release acceptance](release-testing.md).

## Not covered

- A game that opens a *different* program for fullscreen (a separate launcher
  process outside the game's process tree and without its identity) is not
  followed; sharing stops and says why.
- Exclusive fullscreen on X11 sessions suspends compositing; X11 Game Window
  still stops then (privacy).
- Gamescope nested sessions: the Gamescope window is the one followed; a
  Gamescope restart is a new process tree and is followed only by identity.
- Each scenario of the test list (Alt+Enter, Alt+Tab, 1080p → 1440p, refresh
  changes, Proton, Wine, Gamescope, native) still needs a person on real games;
  see [release acceptance](release-testing.md).
