# Moonlight entries: Desktop Low, Steam Big Picture and one per screen

What the other device can choose in Moonlight besides the games, how each entry works, and why two of them need a Sunshine build that reads per-entry settings. Steps for people are in the [user guide](user-guide.md#preferences-and-support); screen numbering is shared with [several screens](moonlight-multi-monitor.md).

## What Moonlight shows

| Entry | What it does | Needs |
|---|---|---|
| **Desktop** | The shared screen, at the quality chosen on the other device | Any Sunshine (always offered) |
| **Desktop Low** | The same screen with a bitrate ceiling (1.5, 3, 5 or 8 Mbit/s; 3 by default) for slow or mobile connections | Sunshine that reads `max-bitrate` per entry |
| **Steam Big Picture** | Opens Steam's TV interface when the stream starts and closes it when the stream ends | Steam installed (native or Flatpak) |
| **01 - Monitor**, **02 - Monitor**, … | One entry per connected screen; the numbers are the ones **Identify screens** shows on each monitor | Sunshine that reads `output-name` per entry, and two or more screens |

They are chosen in **Share → Preferences → Game Library → Moonlight Entries**. All are on by default; each switch says in words why it cannot be used (Steam not installed, one screen only, a Sunshine without per-entry settings). The **One entry per screen** row lists every entry with the screen's real name and connector, for example *01 - Monitor: LG ULTRAWIDE (DP-1)*.

## Pictures in Moonlight

Every entry has its own picture (`host/box_art.py`), 600×830 PNG in `~/.config/big-remote-play/sunshine/covers`:

- **Desktop** and **Desktop Low**: the desktop's logo (`LOGO=` in `/etc/os-release`, e.g. Big WindowMaker's `big-wm`) and its name; Desktop Low adds a *Low bandwidth · 3 Mbit/s* badge.
- **01 - Monitor**…: the number, the screen's real name and its connector.
- **Games**: the cover the launcher already keeps — Steam's library cache (old `library_600x900.jpg` and the 2024 `<hash>/library_capsule.jpg`), Heroic's `images-cache` (SHA-256 of the art URL) and Lutris' cover art. Nothing is downloaded.
- **Steam Big Picture** keeps Sunshine's Steam picture.

Only apps without a picture of their own get one (empty, Sunshine's generic `desktop.png`/`box.png`, or one drawn here); a picture the person chose is kept. File names come from the content, so a changed picture gets a new path, Sunshine a new app id and Moonlight the new picture. Pictures no entry uses are removed. They are applied when sharing starts. Tests: `tests/test_box_art.py`.

## Why stock Sunshine cannot do two of them

Checked in the Sunshine 2026.914 source:

- `output_name` (the captured screen) and `max_bitrate` are single server settings, read once when Sunshine starts (`config::video`).
- An app's `output` field is the file that receives the command's output, not a screen.
- `prep-cmd` runs before the stream but cannot change what Sunshine captures or how fast it encodes.

An entry called *02 - Monitor* or *Desktop Low* would therefore show the same screen at the same bitrate as *Desktop*. Big Remote Play does not create entries that would not do what their names say.

## The Sunshine build with per-entry settings

[`build-aux/sunshine/`](../build-aux/sunshine/) builds Sunshine 2026.914 with two small changes in [`brp-per-app-display-bitrate.patch`](../build-aux/sunshine/brp-per-app-display-bitrate.patch) (besides the touch fix backported from upstream):

- `output-name` in an `apps.json` entry: while that entry runs, the capture uses this screen instead of the global `output_name` (`video.cpp`, `refresh_displays`). It accepts the same names as `output_name` (KMS and wlroots use the kernel connector, `DP-2`).
- `max-bitrate` (Kbps): while that entry runs, the bitrate the client asked for is capped (`rtsp.cpp`, after Sunshine's own FEC and audio adjustments), for every encoder.

The running entry's values are kept behind a mutex (`proc::running_output_name()`, `proc::running_max_bitrate()`), set when the entry starts and cleared when it ends. Entries without these keys behave exactly as before. Sunshine's web API keeps unknown keys when it saves an app, so editing the entry there does not lose them.

Build and install on BigLinux/Manjaro:

```bash
cd build-aux/sunshine
makepkg -s -p sunshine.PKGBUILD
sudo pacman -U sunshine-2026.914.233613-5-*.pkg.tar*
```

The recipe is called `sunshine.PKGBUILD`, not `PKGBUILD`: the repository has exactly one file named `PKGBUILD` (`pkgbuild/PKGBUILD`, Big Remote Play itself), because package builders find it by name. Reported on 2026-11-17: BigLinux's package builder failed with `cd: too many arguments` when this recipe was `pkgbuild/sunshine/PKGBUILD`.

Stop sharing first: a Sunshine that was already running keeps the old binary until it is started again.

### How Big Remote Play knows

Sunshine has no feature query. `host/moonlight_entries.sunshine_supports_app_overrides()` searches the executable for both key names as NUL-terminated strings (how the compiler stores them), and caches the answer by file path, size and modification time. Stock 2026.914 contains neither.

## How the entries are kept

- Each entry carries `"brp-entry"` (`desktop-low`, `steam-big-picture`, `monitor:DP-2`). Only entries with that marker are replaced or removed; a person's own app is never touched. If a person's app already uses the name (Sunshine's default *Steam Big Picture*, for example), Big Remote Play does not add a second one and says so.
- They are written when sharing starts (the file, before Sunshine starts) and when a switch changes (Sunshine's API while sharing, so Moonlight sees it at once; the file otherwise). Unchanged settings write nothing.
- A screen entry prepares its own screen: Big Remote Play's screen helper (`stream_display.py`, HDR→SDR and client resolution on KDE Plasma) runs with `--output` set to that screen, and the person's other `global_prep_cmd` commands are copied in their order (`exclude-global-prep-cmd` plus the same commands). An unreadable `global_prep_cmd` is left alone and the entry keeps the global commands.
- Connector names are checked (`[A-Za-z0-9._-]`); nothing typed by a person reaches a command line. Steam's command is the detected launcher (`steam` or `flatpak run com.valvesoftware.Steam`), joined with `shlex`.
- A Game Window share offers none of them (the private screen is the only screen).

## Verified on real hardware (2026-11-17)

Three-monitor PC (LG ULTRAWIDE DP-1, LG HDR WFHD DP-2, LG ULTRAWIDE HDMI-A-1), wlmaker (wlroots) session, Sunshine built from `build-aux/sunshine/` running from its build tree on its own port (48989) with a temporary configuration, global `output_name = DP-1`; Moonlight Qt 6.2.0 with its own configuration directories, paired through `POST /api/pin`:

| Entry streamed | Sunshine's log |
|---|---|
| *02 - Monitor* (`output-name: DP-2`) | `[wlgrab] Selected monitor [LG Electronics LG HDR WFHD … (DP-2)] for streaming` |
| *Desktop Low* (`max-bitrate: 1500`), client asked 20 Mbit/s | `Selected monitor [… (DP-1)]`, `Streaming bitrate is 1500000` |

Not yet checked on hardware: KMS capture (it reads the connector per session like wlgrab, verified in the source), NVENC (capped before the encoder is configured), Steam Big Picture closing on a real Steam.

## Tests

`tests/test_moonlight_entries.py`: every requested entry with a capable Sunshine and only Steam Big Picture with a stock one; one screen gives no screen entry; saved choices and malformed settings; unsafe connectors never written; screen entries prepare their own screen and keep the person's other global commands; an unreadable `global_prep_cmd` left alone; feature detection; a person's own app neither changed nor duplicated; syncing through the file and through Sunshine's API with moving positions, and switching everything off.
