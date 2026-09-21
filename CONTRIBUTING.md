# Contributing to Big Remote Play

Thank you for helping make remote cooperative play on Linux easier. Contributions are welcome in English or Portuguese, from first-time contributors and experienced maintainers alike.

Be respectful and constructive with other contributors, maintainers and users.

## Choose a useful contribution

Good contributions solve a clear user or maintainer problem:

- reproduce and reduce a bug;
- improve a confusing Share, Connect or network task;
- add a focused regression test;
- test real hardware, audio, networks or packaging;
- review a translation in its UI context;
- improve user or developer documentation.

Open an issue before starting a large feature or redesign. That gives maintainers a chance to confirm scope and avoid duplicated work.

## Before changing code

1. Read [AGENTS.md](AGENTS.md) and the nearest nested `AGENTS.md`.
2. Read the relevant source-of-truth document in [docs/](docs/README.md).
3. Reproduce the behavior or identify the exact contract being changed.
4. Search existing issues and tests.
5. Keep the proposed change focused; separate unrelated cleanup.

Security-sensitive findings must follow the [security policy](https://github.com/biglinux/big-remote-play/security/policy), not a public issue.

## Development setup

The application needs native GTK, libadwaita and PyGObject packages from the distribution. Python-only development tools can live in an external virtual environment. Follow [docs/development.md](docs/development.md) for prerequisites, setup and targeted commands.

Basic source-tree run:

```bash
PYTHONPATH=src python3 -m big_remote_play
```

## Required checks

Run targeted tests while developing, then the applicable gates before a pull request:

```bash
make lint
make typecheck
make translations-check
make metadata-check
make test
make release-check
```

`make release` also cleans and builds wheel/sdist artifacts. It does not publish or approve a stable release.

A change that cannot run a required gate must say which tool/environment was unavailable. Do not describe a blocked or unexecuted check as passing.

## Code and safety expectations

- Keep GTK operations on the main thread and blocking work in bounded workers.
- Use explicit command argument lists and validate addresses, ports, paths and archive entries.
- Preserve native Sunshine/Moonlight configuration, paired devices, libraries and VPN profiles.
- Never place real secrets, certificates, backups or private addresses in tests, screenshots or logs.
- Add behavioral regression coverage, not only source-string assertions.
- Record updated upstream flags, keys or enum contracts in behavioral tests, so a changed contract fails the suite instead of only a document.

## UI and accessibility changes

Use native GTK/libadwaita patterns. Keep common actions visible and advanced controls in named secondary surfaces. Validate:

- normal and compact widths;
- light, dark and high-contrast themes;
- 150% text;
- keyboard focus and accessible labels;
- RTL and CJK locales;
- loading, empty, success, cancellation and error states.

Update screenshots only after implementation and translations are stable. Simulated service data must be obvious and sanitized.

## Translation changes

Visible text belongs in gettext. Preserve placeholders and protocol tokens exactly. Keep regional catalogs genuinely distinct, especially `pt`/`pt_BR` and `zh_CN`/`zh_TW`.

After a source-string or PO edit:

```bash
python3 tools/i18n/update_catalogs.py
python3 tools/i18n/validate_catalogs.py
```

Commit PO and compiled MO changes together. Structural validation does not replace native editorial review. See [docs/translations.md](docs/translations.md).

## Documentation changes

Update the document that owns the behavior rather than copying the same explanation into several places. Keep the repository root small; durable guides belong in `docs/` and GitHub community files in `.github/`.

## Pull-request checklist

A pull request should include:

- the user problem and chosen solution;
- scope and intentional non-goals;
- tests run and exact results;
- screenshots for visible UI changes;
- safety/migration impact for settings, credentials or native files;
- documentation and translation updates;
- remaining target-machine validation.

Keep commits reviewable. Do not include generated caches, virtual environments, audit bundles or build artifacts.

## Reporting and support

Use the [issue tracker](https://github.com/biglinux/big-remote-play/issues) for reproducible defects and feature proposals. General usage guidance starts in [docs/user-guide.md](docs/user-guide.md) and [docs/troubleshooting.md](docs/troubleshooting.md).
