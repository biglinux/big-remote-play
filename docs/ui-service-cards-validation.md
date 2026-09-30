# Service cards and guided choices: validation

What was verified for the [service status cards](service-status-cards.md) and
the guided-choice border fix ([audit](ui-service-cards-audit.md)).

## Automated coverage

`tests/test_service_status_cards.py`, in real GTK with a fake
private-network service (no VPN client, Sunshine or Moonlight runs):

- every `ConnectionState` maps to one tone and to the network pages' wording;
  install, stopped-service and permission recoveries are named by their step;
- Sunshine missing/stopped/running/checking and Moonlight missing/ready/running;
- connected details are real (online devices, ZeroTier network name) and never
  show an e-mail address, account or private address;
- Share and Connect, each with no method, Tailscale, ZeroTier or Headscale
  connected: the same four cards are visible and each shows its real state;
- two methods connected at once;
- one card going Setting up → Connected → Not connected while the page stays
  open: same widget, same visible set;
- not installed, sign-in needed and error states keep their card with words;
- a failed first read shows **State unknown** instead of checking forever;
- results arriving after the window closes are dropped;
- switching Share ↔ Connect swaps only the streaming card;
- **Streaming** and **Secure connection** headings; Home shows no card;
- the Connection method dialog uses the same state words;
- both stylesheets parse without errors in GTK; choice and service cards draw
  their focus ring only on `:focus-visible` and never a 2 px normal border;
  motion is one-shot, at most 420 ms, and removed under reduced motion.

The existing guided-setup tests (same network, somewhere else, ready,
needs-a-step and no connection, ZeroTier code, Back and restart) pass unchanged.

## Gates

Run on 2026-09-30 on the development machine.

| Gate | Result |
|---|---|
| Targeted: service cards, guided setup, guided home, private-network UI, CSS, accessibility, UI flows | PASS |
| `make lint` | PASS |
| `make typecheck` (pyright via `uvx`, same configuration) | PASS, 0 errors |
| `make translations-check` | PASS, 32 catalogs |
| Catalog update run twice with a fixed `SOURCE_DATE_EPOCH` | PASS, byte-identical |
| `make metadata-check` | PASS |
| `make test` | BLOCKED: `xvfb-run` is not installed. The same suite ran under the GTK Broadway backend with a private D-Bus: see below |
| Full suite under Broadway | PASS except `test_navigation_feedback::test_presets_do_not_show_a_second_resolution_list`, which also fails on the untouched baseline under Broadway only |
| `make release-check` | PASS with the git-ignored makepkg outputs in `pkgbuild/` set aside; with them present it reports them as generated paths |

## Rendered states

Real frames rendered under a virtual `kwin_wayland` session and inspected
(not committed):

- Share: Sunshine running; Tailscale connected with one device online; ZeroTier
  waiting for approval; Headscale not installed.
- Connect: Moonlight ready; Tailscale setting up with keyboard focus; ZeroTier
  connected with its network name; Headscale not connected.
- Guided setup first question with focus not visible (thin border only) and
  with keyboard focus (separate ring); the old stylesheet, rendered the same
  way, showed the thick double frame on the first card.
- Where is the other device, How do you want to connect, Home.
- Light, dark, Gamer and high contrast; 1280 px and 600 px (collapsed sidebar);
  150 % text in German; Hebrew RTL; Traditional Chinese; Brazilian Portuguese.

No text was clipped; long states and second lines wrap inside the card.

## Found and fixed during validation

- Gamer's base card border hid the attention border; Gamer now repeats the
  attention and starting borders.
- Cards stayed on "Checking..." if the first provider read failed.

## Requires real hardware and services

- Real Tailscale, ZeroTier and Headscale transitions (sign-in, approval,
  turning off) observed in the sidebar within the 6 s refresh.
- Sunshine start/stop and Moonlight open/close reflected within 3 s.
- Orca reading card names, states and headings; keyboard-only navigation
  through the sidebar on a real desktop.
- The system reduced-motion setting on GNOME and KDE Plasma.
- Native review of the new strings (Streaming, Secure connection, Services,
  Ready, Permission needed) in every catalog: they are machine translations.
