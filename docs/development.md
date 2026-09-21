# Development guide

## Repository layout

```text
src/big_remote_play/      Python application and integration adapters
tests/                    Hermetic unit, integration and GTK task-flow tests
usr/share/                Desktop files, icons, scripts, metainfo and runtime catalogs
locale/                   Source PO catalogs and POT template
tools/i18n/               Gettext extraction, compilation and validation
tools/release/            Automatic build version and PEP 517 wrapper
pkgbuild/                  BigLinux/Arch package recipe
docs/                     Durable user, contributor and maintainer documentation
```

Read [architecture](architecture.md) before moving responsibilities between modules. Repository-wide invariants and the required agent workflow live in [AGENTS.md](../AGENTS.md).

## Native prerequisites

Install these through the distribution package manager:

- Python 3.11 or newer;
- GTK 4.10 or newer;
- libadwaita 1.7 or newer;
- PyGObject and Cairo bindings;
- libsecret and the hicolor icon theme;
- gettext;
- Xvfb and D-Bus tools;
- `desktop-file-validate` and `appstreamcli`;
- Bash and ShellCheck for shell validation.

Sunshine, Moonlight, VPN clients, VTE, Avahi and audio tools are needed only for the related runtime paths or target-machine tests.

## Isolated developer environment

Use the system Python as the base because its GI bindings are native packages. Keep Python-only developer tools in an external environment:

```bash
uv venv --python /usr/bin/python3 --system-site-packages ../brp-dev-venv
uv pip install --python ../brp-dev-venv/bin/python \
  pytest ruff pyright build installer 'uv_build>=0.11.16,<0.13'
uv pip install --python ../brp-dev-venv/bin/python --no-deps -e .
export PATH="$(realpath ../brp-dev-venv/bin):$PATH"
```

Node.js is needed by the Pyright wrapper unless the installed Pyright package supplies a supported runtime. These setup steps require network access or a pre-populated cache. Runtime code must never install dependencies automatically.

## Run from the checkout

```bash
PYTHONPATH=src python3 -m big_remote_play
```

A source-tree run uses real user settings and can request privileged operations when selected. It is not a read-only preview. Use fixtures or a disposable desktop account when testing destructive, network or authentication paths.

## Fast feedback

Run the narrowest relevant tests first:

```bash
python3 -m pytest -q tests/test_vpn_accounts.py
python3 -m pytest -q tests/test_ui_task_flows.py
python3 -m pytest -q tests/test_audio.py
```

GTK tests need a display and session bus:

```bash
GDK_BACKEND=x11 xvfb-run -a dbus-run-session -- \
  python3 -m pytest -q -p no:cacheprovider tests/test_ui_task_flows.py
```

## Required gates

```bash
make lint
make typecheck
make translations-check
make metadata-check
make test
make release-check
```

`make release` cleans generated files, runs the gates in order and builds wheel/sdist artifacts. It does not upload, sign, tag or declare a stable release.

## Build version

Do not edit version fields. The checkout keeps `0.0.0` and a neutral AppStream date. Builds call `tools/release/build_version.py` and derive `YY.MM.DD` from:

1. `BRP_BUILD_VERSION`, when explicitly set by a trusted pipeline;
2. `SOURCE_DATE_EPOCH`, for reproducible builds;
3. the current UTC date.

The native package follows the same scheme in its own language: `pkgver` and `pkgrel` are computed from `BRP_BUILD_VERSION` and `SOURCE_DATE_EPOCH`, falling back to the current UTC date and time, so the wheel and the package built from one tree carry the same version. There is deliberately no `pkgver()` function: makepkg rewrites the `pkgver=` line in place whenever one exists, which turns the command substitution into a syntax error on the next load. `tools/release_check.py` accepts either that build-date form or a literal that matches the checkout — never a hand-pinned date on its own.

Python distribution filenames use PEP 440 normalization, so `26.09.20` appears as `26.9.20` in wheel and sdist names.

## Native rolling-release layout

The wheel remains a standard Python artifact with the `big-remote-play` GUI entry point. The BigLinux/Manjaro PKGBUILD uses `tools/release/install_private_wheel.py` to place its pure-Python contents under `/usr/lib/big-remote-play`, then installs `usr/bin/big-remote-play` as a direct `/usr/bin/python3 -I` launcher.

This separation is intentional:

- the application path does not include the Python minor version;
- the launcher uses the active system Python after a rolling upgrade;
- only the private application root is added to `sys.path`;
- current system packages provide PyGObject and other dependencies;
- the build fails if the wheel gains a native extension or unsafe archive member.

Run the focused regression tests with:

```bash
python3 -m pytest -q tests/test_native_launcher.py tests/test_release_metadata.py
python3 tools/release_check.py
```

Do not replace this design with a glob over `/usr/lib/python3.*/site-packages` or a global `PYTHONPATH`: either approach can select stale application code and expose the new interpreter to extension modules built for an older ABI.

## Flatpak bundle

`build-aux/flatpak/br.com.biglinux.remoteplay.yaml` builds the same layout under `/app` on the GNOME runtime:

```bash
flatpak install --user flathub org.gnome.Platform//49 org.gnome.Sdk//49
flatpak-builder --user --install --force-clean build-aux/flatpak/.build \
    build-aux/flatpak/br.com.biglinux.remoteplay.yaml
flatpak run br.com.biglinux.remoteplay
```

Three points decide whether this build works at all:

- **Relocation.** `BIG_REMOTE_PLAY_DATADIR` and `BIG_REMOTE_PLAY_LOCALEDIR` point `paths.py` at `/app/share`; without them the code looks under `/usr/share` and finds an empty runtime.
- **Host programs.** `tailscale`, `systemctl`, `pkexec`, `sunshine`, `moonlight-qt`, `pacman` and the rest do not exist in the runtime. `/app/libexec/host-tools` holds one symlink per program pointing at `build-aux/flatpak/host-tool`, which re-runs it on the host with `flatpak-spawn --host`, and that directory comes first in `PATH`. Arguments under `/app` are rewritten to the host-visible deploy path, so a bundled helper handed to `pkexec` is readable by root. Add a wrapper whenever the code starts probing a new host program — `tests/test_flatpak_manifest.py` fails otherwise.
- **Sandbox reality.** `--talk-name=org.freedesktop.Flatpak` gives the bundle the logged-in user's reach on the host. That is the honest cost of an application whose job is to manage host services; do not describe this build as confined.

Known host-tool gotcha: with AppStream 1.1.6 and glycin 2.x on Arch, `appstreamcli compose` fails to read any icon (`No image loaders are configured`) and takes the whole build down with it. The metainfo itself is valid — `make metadata-check` passes. Until the host tools agree, build that machine with `appstream-compose: false` added to a local copy of the manifest; keep the committed manifest composing.

## Translations

Visible text belongs in gettext. After an intentional source-string or PO change:

```bash
python3 tools/i18n/update_catalogs.py
python3 tools/i18n/validate_catalogs.py
```

Commit PO and MO changes together. Do not copy Brazilian Portuguese into European Portuguese or Simplified Chinese into Traditional Chinese. See [translations](translations.md) and `tools/i18n/AGENTS.md`.

## UI changes

Keep GTK access on the main thread. Capture widget values before launching a worker, bound external work, reject stale results and return UI updates through the main loop. Validate:

- normal and compact widths;
- light, dark and high-contrast themes;
- 150% text;
- keyboard focus order and accessible labels;
- RTL and CJK text;
- empty, loading, success, cancellation and error states.

Use native libadwaita controls where possible. Do not encode state only by color or icon. See `src/big_remote_play/ui/AGENTS.md`.

## Debugging external integrations

Use explicit argument arrays and sanitized logs. Record the exact upstream version and command/config contract. Never place real passwords, tokens, auth keys, certificates, backup archives or private addresses in fixtures, screenshots or issues.

Hermetic tests replace external services. Passing them does not prove streaming performance, GPU compatibility, audio behavior, VPN authentication or real firewall reachability. Those remain part of [release acceptance](release-testing.md).
