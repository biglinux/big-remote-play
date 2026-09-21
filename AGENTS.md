# AGENTS.md

This is the canonical project guidance for coding agents and a concise operational reference for maintainers. Read it before changing the repository.

## Instruction scope

- These rules apply to the whole repository.
- A nested `AGENTS.md` adds more specific rules for its directory.
- The nearest applicable file wins when guidance conflicts.
- Keep durable facts here. Do not add task transcripts, temporary paths, release artifacts or one-off review notes.

## Project in one minute

Big Remote Play is a Python 3.11+ GTK 4/libadwaita desktop application that guides:

- Sunshine sharing on the game computer;
- Moonlight connection on the receiving computer;
- Tailscale, Headscale or ZeroTier private-network setup;
- optional audio routing, backup/restore and support workflows.

Python code lives in `src/`. Native resources, scripts, icons, desktop files and compiled translations live under `usr/share/`. The wheel is not a complete desktop installation by itself.

Start with:

- `README.md` for product intent;
- `docs/architecture.md` for ownership boundaries;
- `docs/host-network-policy.md` for settings precedence and safety;
- `docs/development.md` for the environment and commands;
- `docs/release-testing.md` for release acceptance.

## Required working method

1. Inspect the real implementation and relevant tests before proposing a change.
2. Reproduce the problem or identify the exact violated contract.
3. Make the smallest coherent fix; preserve unrelated behavior and user data.
4. Add or update behavioral regression coverage.
5. Run targeted checks first, then the applicable full gates.
6. Review the diff for safety, translated text, accessibility, docs and generated clutter.
7. Report what was inspected, changed, tested and still requires real hardware/services.

Do not claim a gate passed without command output or a persisted result. Distinguish **failed**, **blocked/not available** and **not run**.

## Common commands

```bash
PYTHONPATH=src python3 -m big_remote_play
python3 -m pytest -q tests/test_relevant_area.py
make lint
make typecheck
make translations-check
make metadata-check
make test
make release-check
```

`make test` runs GTK tests under Xvfb and a D-Bus session. `make release` cleans, validates and builds; it does not publish or approve a stable release.

## Non-negotiable safety invariants

- Run the UI as the logged-in user, never as root.
- Use narrowly scoped PolicyKit helpers for privileged operations.
- Invoke external commands with explicit argv; never build shell commands from untrusted text.
- Validate addresses, ports, paths, archive members and protocol markers before use.
- Never store supported secrets in ordinary JSON settings when Secret Service is available.
- Preserve existing Sunshine libraries/configuration, Moonlight identity/paired hosts and VPN profiles.
- Treat unreadable or malformed native configuration as an error, not as an empty file to overwrite.
- Use owner-only permissions and atomic replacement for sensitive files.
- Never put real credentials, tokens, private certificates, backups or private addresses in tests, logs, screenshots or issues.

## Architecture and concurrency

- `app.py` owns the GTK application and theme.
- `ui/main_window.py` owns Home and task navigation.
- `HostView` and `GuestView` gather GTK state on the main thread and delegate blocking work to bounded workers.
- Apply worker results through the main loop and reject stale generations after cancellation, navigation or close.
- Home/navigation must not silently install software, start sharing, scan a subnet or change a network.
- Keep external integration contracts explicit and covered by behavioral tests.

## UI and accessibility

- Use native GTK/libadwaita controls and semantics before custom widgets.
- Keep normal actions visible; put technical controls in clearly named secondary pages or sheets.
- Express state in words, not only color or icon.
- Preserve keyboard focus, accessible names/descriptions and predictable Back behavior.
- Test normal/compact widths, light/dark/high contrast, 150% text, RTL and CJK after layout or text changes.
- Cover loading, empty, success, cancellation and error states.
- Do not present configured bitrate/FPS/ping as measured stream performance.
- Follow `src/big_remote_play/ui/AGENTS.md` for UI changes.

## Translations

- All visible text belongs in gettext unless it is a protocol token or technical identifier.
- Keep action names consistent across pages and documentation.
- Preserve placeholders, markup and shell variables exactly.
- Keep `pt` distinct from `pt_BR`; keep `zh_TW` genuinely Traditional and distinct from `zh`/`zh_CN`.
- Commit source PO and compiled MO files together.
- Never substitute automated structural checks for native editorial review.
- Follow `tools/i18n/AGENTS.md`.

## Tests

- Prefer behavior assertions over source-string or implementation-detail assertions.
- Tests must be hermetic: no real VPN changes, firewall edits, credentials, package installation or network dependency.
- Use fixtures for subprocesses, services, clocks, settings and filesystem boundaries.
- A translated message may change; security tests should assert exit status and semantic effects rather than exact English wording.
- Follow `tests/AGENTS.md`.

## Build, version and release

- Leave checkout versions at `0.0.0` and the neutral AppStream date at `1970-01-01`.
- Builds derive `YY.MM.DD` automatically from `BRP_BUILD_VERSION`, `SOURCE_DATE_EPOCH` or UTC date.
- Do not hand-edit version fields.
- Keep builds reproducible when `SOURCE_DATE_EPOCH` is set and restore the checkout after stamping.
- The automated suite does not prove real streaming, GPU/audio compatibility, VPN authorization, router reachability, package installation or assistive-technology behavior.
- The native BigLinux/Manjaro package must keep the pure-Python application in `/usr/lib/big-remote-play`, never under a Python-minor-specific `site-packages` path.
- Keep `usr/bin/big-remote-play` as a direct isolated Python launcher. Do not scan old `python3.X` trees or prepend a complete stale `site-packages` directory.
- The private native layout is valid only while the application wheel is pure Python; the installer and tests must reject native extension modules.
- Follow `tools/release/AGENTS.md` and `docs/release-testing.md`.

## Documentation and repository hygiene

- Update README/user docs when behavior, labels, defaults or supported workflows change.
- Update architecture/policy docs when ownership or external contracts change.
- Put durable guides in `docs/` and keep the root small.
- Do not commit caches, virtual environments, generated reports, audit bundles, screenshots with private data or build artifacts.
- Keep all local Markdown links valid and symlinks relative/inside the tree.

## Definition of done

A change is complete only when:

- the user-visible behavior and failure modes are correct;
- existing user configuration/data is preserved;
- targeted regression tests pass;
- applicable lint, type, translation, metadata and release checks pass;
- documentation and translations are synchronized;
- the diff contains no generated clutter or unrelated rewrites;
- real-hardware/service limitations are stated explicitly.
