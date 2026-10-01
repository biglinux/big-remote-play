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
| Internet | Real Tailscale browser authorization and machine sharing, ZeroTier join/approval/managed IP, Headscale server (0.27+) with API key, registration approval and pre-auth key; Share ↔ Connect over each provider, including a relayed path. Track results in the [private-network test matrix](private-network-test-matrix.md) |
| Privileges | PolicyKit allow/cancel/failure and desktop keyring locked/unavailable states |
| Installation | From Home, the guided setup and the Tailscale/ZeroTier pages on a machine without the component: Pamac's password dialog (allow, cancel, wrong password), a busy package database, no network; without Pamac, the `install-components.sh` helper; Sunshine installed in another program while Big Remote Play is open is recognized on the next Share; after installing Tailscale the browser sign-in opens by itself. See [installing what a task needs](dependency-installer.md) |
| Pairing requests | A real Moonlight on another computer: the request dialog appears by itself (also with the window in the background, with a notification), Approve with the PIN shown there, a wrong PIN, Reject, letting it expire, cancelling on the other device; a first Sunshine without a user. See [pairing requests](pairing-ux.md) |
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
8. Repeat cards and guided choices in light, dark, Gamer and high contrast at
   normal/compact width, 150% text, keyboard-only, RTL and CJK locales. Confirm
   focus remains visible without a thick double border.

Real Secret Service, Sunshine/Moonlight pairing, provider authorization,
service control, low-disk behavior and assistive-technology announcements still
require target-system validation; hermetic tests deliberately do not perform
those external operations.
