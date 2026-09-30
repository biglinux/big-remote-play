# Backup and service-card validation

## Automated coverage

`tests/test_backup_restore.py` uses only temporary directories and synthetic
content. It covers:

- round trip of application, Sunshine and Moonlight data;
- namespace separation, owner-only archive mode and transient/log/link
  exclusion;
- replace semantics and preservation of excluded runtime data;
- traversal, absolute/backslash paths, duplicate names, links and unknown roots;
- manifest integrity failure before any current setting changes;
- rollback after an injected mid-restore write failure;
- interrupted creation preserving an existing archive;
- compatible legacy layout and conflicting/ambiguous legacy rejection;
- complete default reset removing obsolete keys.

`tests/test_service_status_cards.py` covers every provider and streaming state
mapping, the stable card set on Share and Connect (including several connected
providers and in-place state changes), empty/Home states, role-appropriate
click routing and the keyboard-only focus-ring contract. Details:
[ui-service-cards-validation.md](ui-service-cards-validation.md).

The broader UI and integration suites continue to cover the actual Flatpak
Moonlight destination, symbolic-link destinations, keyring clearing, task
navigation and component status visibility.

## Executed checks

- **PASS** — focused backup, private-network, card, Gamer and UI regression
  selection: 254 passed. Warnings were upstream GI/GTK deprecations.
- **PASS** — `make lint` (Ruff checks and formatting for 141 Python files).
- **PASS** — `make translations-check`: all 32 PO catalogs match the template
  and their compiled MO files, including regional checks.
- **PASS** — `make metadata-check` and `git diff --check`.
- **BLOCKED / not available** — `make typecheck`: `pyright` is not installed.
- **BLOCKED / not available** — `make test`: `xvfb-run` is not installed. A
  direct display-backed collection is not treated as a substitute for the
  project's isolated Xvfb/D-Bus gate.
- **FAILED because of preserved pre-existing artifacts** — `make
  release-check` reports only the ignored
  `pkgbuild/src/big-remote-play/dist` wheel and release-tool `__pycache__` (plus
  the copied checker path warning). Session-created caches outside that
  packaging tree were removed; the pre-existing packaging copy was not altered.

## Manual release matrix

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
