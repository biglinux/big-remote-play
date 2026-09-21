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

Run the native interface in Portuguese and English, light/dark themes, compact width, large text and high contrast. Inspect Home with both roles, Share stopped/active, pairing, no computers/one computer, manual address, quality sheets, support, each network provider, installation and backup errors. Check that text is not obscured, primary actions are reachable, tabs retain names, and symbols follow the theme. Data injected for screenshots must be labeled as simulated.

## Required target-machine checks

| Area | Acceptance evidence still required outside a hermetic UI run |
|---|---|
| Native package | Clean install/upgrade/removal on supported BigLinux/Manjaro; private Python layout, resources and launchers intact |
| Streaming | Sunshine → Moonlight connection, pairing, stop/reconnect, custom port and invalid credentials |
| Capture/audio | Wayland/X11 sessions, actual GPU encoding/decoding, display changes, host/client sound |
| Internet | Real Tailscale browser authorization, ZeroTier authorization and Headscale deployment/client join |
| Privileges | PolicyKit allow/cancel/failure and desktop keyring locked/unavailable states |
| Accessibility | Orca/AT-SPI navigation and announcements, keyboard-only use, touch targets and text scaling |
| Safety | Real backup/restore with native certificates, cancelled startup, preserved libraries and devices |
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

Confirm with multiple real outputs (USB, HDMI, speakers and an existing virtual
processor) that default startup/stop/close never reroutes audio. Test a fresh client
with host playback on, a saved mute-off choice and a third-party Moonlight client;
Sunshine may choose its virtual sink for the latter request. Test explicit routing,
device removal/reordering and the user's changing output during a session.

For UPnP, check the router mappings and Sunshine logs, then connect from another
network. Also test a router without UPnP and a CGNAT/double-NAT connection. Neither
a local status nor a successful DNS lookup proves remote streaming. Do not expose
the administration panel. Test public-domain setup separately from Headscale VPN.
