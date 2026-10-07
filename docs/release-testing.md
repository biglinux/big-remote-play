# Release acceptance

A passed automated gate is not sufficient to label this desktop/network application stable. Publish a new tag only after the maintainers accept the target-machine results.

## Automatic build version

The checkout keeps `0.0.0` (and the neutral AppStream date `1970-01-01`) as build placeholders. Do not edit version fields manually. The PEP 517 backend and `pkgbuild/PKGBUILD` call `tools/release/build_version.py`, which produces `YY.MM.DD` from `SOURCE_DATE_EPOCH`; without that variable it uses the current UTC date. `BRP_BUILD_VERSION` is an explicit pipeline override and accepts only `YY.MM.DD` or `YYYY.MM.DD`.

The backend stamps a temporary build copy and restores the checkout afterward. Python package filenames use PEP 440 normalization, so a native version such as `26.09.20` appears as `26.9.20` in wheel and sdist names.

## Automated checks

With the development dependencies installed:

```bash
make lint
make typecheck
make translations-check
make metadata-check
make test
make release-check
```

`make test` uses a private Xvfb display and a D-Bus session, and keeps pytest's temporary tree under `$HOME/.cache/big-remote-play/pytest` (override with `TEST_TMPDIR=`): the launcher tests execute a staged copy of `usr/bin/big-remote-play`, which a `noexec` `/tmp` refuses. Its external service adapters are replaced; it must not modify a live desktop's VPN or firewall. Tests cover task navigation, first passive discovery, selection, settings ownership, code validation, custom ports, cancellation and file safety. The package source must not contain runtime caches or nested review bundles.

## Visual matrix

Run the native interface in Portuguese and English, Gamer/Automatic/Light/Dark
appearances, compact width, large text and high contrast. Switch repeatedly
between Gamer and every base appearance and check that no Gamer surface remains.
Inspect Home with both roles, Share stopped/active, pairing, no computers/one
computer, manual address, quality sheets, support, each network provider,
installation and backup errors. On Share/Connect, verify that all three network
cards stay in place while a method connects and disconnects, several connected
methods at once, and the keyboard-only focus ring on cards and guided choices.
Check that text is not obscured, primary actions
are reachable, tabs retain names, and symbols follow the theme. Data injected
for screenshots must be labeled as simulated.

## Required target-machine checks

| Area | Acceptance evidence still required outside a hermetic UI run |
|---|---|
| Native package | Clean install/upgrade/removal on supported BigLinux/Manjaro; private Python layout, resources and launchers intact |
| Streaming | Sunshine → Moonlight connection, pairing, stop/reconnect, custom port and invalid credentials |
| Capture/audio | Wayland/X11 sessions, actual GPU encoding/decoding, display changes, host/client sound |
| Game Window | On KDE Plasma Wayland and X11: a native game, a Steam/Proton game and a Gamescope game listed by name; first-share confirmation and silent restore; terminal, browser, Dolphin, a notification and the Plasma menu over the game never reach the client; closing and crashing the game stop sharing with no desktop shown; windowed, borderless and fullscreen; two and three monitors; AMD, Intel and NVIDIA (hybrid laptops included) with hardware encoding; controllers, keyboard and mouse; host and client sound. See [Game Window](game-window.md) |
| Game Window changes | With a real game of each kind (native, Steam/Proton, Wine, Gamescope; Vulkan and OpenGL): windowed → fullscreen, fullscreen → windowed, windowed → borderless, borderless → fullscreen, Alt+Enter, Alt+Tab, 1920×1080 → 2560×1440, a refresh-rate change; the game closing and crashing; KDE's sharing indicator stopped by hand; Moonlight reconnecting; Sunshine restarted. Expected: at most a few seconds of black with **Reconnecting to the game window…**, never the desktop; two windows of the game end the share. See [when the game changes mode](game-window.md#when-the-game-changes-mode) |
| Host input priority | Physical USB and Bluetooth keyboards and mice (hot-plugged during a session), a touchpad; Wayland and X11; guest holding a key, Ctrl/Alt/Shift/Super, a mouse button and a drag when this computer takes over; relative mouse in a game; scroll; 1, 5 and 30 s; the guest disconnecting while paused; Moonlight reconnecting; Sunshine restarted; a controller on each side keeps working; a user outside the `input` group with the package's PolicyKit rule; the on-screen message in Full Desktop. See [host input priority](host-input-priority.md) |
| Internet | Real Tailscale browser authorization, machine sharing and a user invitation, ZeroTier network creation, join/approval/managed IP on a second computer, Headscale server (0.27+) with API key, registration approval and pre-auth key; Share ↔ Connect over each provider, including a relayed path. Track results in [private-network testing](private-network-testing.md) |
| Privileges | PolicyKit allow/cancel/failure and desktop keyring locked/unavailable states |
| Installation | From Home, the guided setup and the Tailscale/ZeroTier pages on a machine without the component: Pamac's password dialog (allow, cancel, wrong password), a busy package database, no network; without Pamac, the `install-components.sh` helper; Sunshine installed in another program while Big Remote Play is open is recognized on the next Share; after installing Tailscale the browser sign-in opens by itself. See [installing what a task needs](dependency-installer.md) |
| Pairing requests | A real Moonlight on another computer: the request dialog appears by itself (also with the window in the background, with a notification), Approve with the PIN shown there, a wrong PIN, Reject, letting it expire, cancelling on the other device; a first Sunshine without a user. See [pairing requests](pairing-requests.md) |
| Accessibility | Orca/AT-SPI navigation and announcements, keyboard-only use, touch targets and text scaling |
| Safety | Real backup/restore with native certificates, corrupt/legacy archives, forced apply failure and preserved libraries, Moonlight identity and devices; follow [backup, restore and service cards](#backup-restore-and-service-cards) and the [backup and restore contract](backup-restore.md) |
| Usability | First-time participants completing Share/Connect without developer guidance |

Store exact component versions and the result of each scenario. Do not infer these results from a screenshot or a mock. Nix packaging and service paths also require target-system testing; the included expression is not a claim of a completed NixOS integration test.

### Python-minor upgrade resilience

For each supported rolling-release base, inspect the built native package and verify that application code and `.dist-info` metadata are under `/usr/lib/big-remote-play`, with no owned path below `/usr/lib/python3.X`. Then:

1. launch normally and record `python3 --version`;
2. launch with a deliberately hostile `PYTHONPATH` and confirm the package-owned application is used;
3. upgrade the distribution's Python minor version and its normal repository dependencies without rebuilding Big Remote Play;
4. launch the unchanged Big Remote Play package and exercise Home, Share and Connect;
5. rebuild and reinstall the package normally, then repeat the smoke test;
6. inspect the process tree and confirm the launcher runs the application in one Python process rather than a shell plus probe interpreter.

The hermetic regression test simulates the path and process behavior, but only a real distribution transition validates package-manager ownership and dependency coordination.

## Additional acceptance for audio and public access

Follow [audio testing](audio-testing.md). With multiple real outputs (USB, HDMI,
Bluetooth and an effects output), confirm that Automatic startup/stop/close writes
nothing to the sound server and that `sunshine-record` always records a monitor.
Test a client that plays sound on the host and one that mutes it, with **Also play
sound on this computer** on and off, a chosen device, device removal and the person
changing output during a stream. Test Steam Remote Play Together with sharing active,
kill Sunshine during a host-muting session and check that the next start restores
the output. **Microphone sent to Sunshine** must read **No** throughout.

With **Microphone** on, a real second computer must hear this computer's
default microphone while playing and this computer's speakers must not play
it; check with headphones and without (echo), with a USB headset plugged in
during the session and with no microphone at all. With **Voice calls** on, a
Discord or Zoom call must reach the other computer, also with Game Window;
turning either switch off during the session must remove it within a second.
Both choices must survive closing and reopening Big Remote Play.

For the sidebar states, start and stop sharing, a Moonlight stream and each
private network both from Big Remote Play and from outside it (`systemctl
--user start sunshine`, `moonlight-qt`, `tailscale up`/`down`): **Share**,
**Connect** and **Connect your devices** must say **Running** and **Stopped**
within about 10 s on every page, and never only because a page is open. For
connection notifications, connect a real Moonlight from the local network and
over Tailscale, stay connected for several minutes, disconnect and reconnect:
one notification per connection, with the device name, the path and the
address; none while the window is opened during a stream.

For Game Window sound, share a game that mutes itself in the background (for
example *TMNT: Shredder's Revenge* through Heroic/Wine) to a real second
computer: the sound must stay continuous for the whole session while nobody
uses the sharing computer, also after the game is reactivated when a device
connects. Record the game's own options for sound in the background, whether
the sound is also wrong on the sharing computer itself (then it is the game's
playback under load, before Sunshine), and CPU and GPU load on a single-GPU
computer, where the private screen encodes on the GPU the game uses. Measured
results so far are in [audio testing](audio-testing.md#game-window-with-real-games).

For UPnP, check the router mappings and Sunshine logs, then connect from another
network. Also test a router without UPnP and a CGNAT/double-NAT connection. Neither
a local status nor a successful DNS lookup proves remote streaming. Do not expose
the administration panel. Test public-domain setup separately from Headscale VPN.

## Backup, restore and service cards

Run this on both a native install and a Flatpak Moonlight setup. Use synthetic
accounts/configuration where possible and never attach the resulting archive to
a public report.

1. Create a backup with app settings, a Sunshine library, paired Moonlight host
   and one configured VPN. Confirm progress, responsive UI and mode `0600`.
2. Change and add settings, restore, then reopen. Confirm restored values,
   removed in-scope extras, preserved logs and Moonlight at its actual path.
3. Move the archive to a different test user/machine. Confirm hardware choices
   fail safely or fall back, and keyring-backed services request authentication.
4. Try a corrupt/truncated archive and a legacy archive. Confirm the former
   changes nothing and the latter restores supported content.
5. Force low disk space or an unwritable destination. Confirm a previous backup
   remains usable and restore reports rollback without partial settings.
6. Check **Restore Defaults** with an obsolete JSON key and confirm Moonlight
   pairing remains. Check **Clear Everything** twice-confirmed behavior and the
   same external-data preservation.
7. On Share and Connect, exercise Sunshine/Moonlight missing, stopped and
   running states plus all VPN model states. Connect two providers together and
   confirm both show Connected while every disconnected method keeps its card.
   Open each method's page from the sidebar card and from **Connect your
   devices**: the state word, the Connection button and **Stop** must match the
   real client; **Back to Share/Connect** returns to the task it was opened
   from, and the header's Back returns one page. On each page check **Devices**
   against the provider's own console (online state, address, **Copy IP**),
   **Remove device** with an API credential on a disposable test device (it
   must disappear from the provider and need re-adding), **Manage devices**
   without one, the ZeroTier network choice with two networks, and Headscale
   turning Tailscale off (and back) on a computer that has both profiles.
   Nothing in Connect your devices may open a dialog except confirmations,
   short inputs, the QR code and a diagnosis result.
   On **Connect your devices**, move each card's switch both ways against the
   real clients and confirm the card shows the provider's real state, network,
   address and device counts (compare with `tailscale status` and ZeroTier
   Central) and that a refused PolicyKit prompt puts the switch back.
8. **Headscale setup** ([wizard](headscale-setup-wizard.md)) on a disposable
   machine and domain: a) *Another server* against a real VPS running
   Headscale behind Caddy — the check, adding this computer with an API key,
   with a pre-auth key and with the owner's approval, then a second device;
   b) *This computer* on a connection with a public IPv4 and router port
   forwarding of 80/443 — DigitalPlat or an existing domain, Cloudflare
   nameservers and the A record checked, installation through Pamac, the
   certificate obtained, the network created, this computer joined; the
   router step on a router without forwarding; c) on a CGNAT connection the
   warning and the VPS suggestion; d) **Remove the server setup** restores
   the original `/etc/headscale/config.yaml`. Confirm the API key exists only
   in the keyring and never in logs or `headscale-setup.json`.
9. Repeat cards and guided choices in light, dark, Gamer and high contrast at
   normal/compact width, 150% text, keyboard-only, RTL and CJK locales. Confirm
   focus remains visible without a thick double border.

Real Secret Service, Sunshine/Moonlight pairing, provider authorization,
service control, low-disk behavior and assistive-technology announcements still
require target-system validation; hermetic tests deliberately do not perform
those external operations.
