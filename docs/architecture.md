# Architecture and ownership

`app.py` owns the GTK application and theme. `ui/main_window.py` owns permanent Home, role-specific instructions, task navigation and lazy private-network pages. Home presents Share and Connect before the secondary internet-access path; technical component names appear there only when setup is required. Role selection itself does not install software, start sharing or change a network.

`HostView` gathers GTK values on the main thread, then uses a worker for Sunshine/audio/process work. `GuestView` distinguishes discovery, pairing and streaming, uses attempt generations/cancellation events, and applies UI results through the main loop. Background discovery is passive while Connect is visible; explicit Search may perform the bounded subnet scan. Home does not start that scan.

Home's **Guided setup** (`ui/guided_setup.py`) only asks and hands over: it pushes question pages on Home's navigation view and ends in Share, Connect, the internet page or a provider's connection page; it implements no flow of its own. Connection cards (`ui/connection_cards.py`) render `utils/connection_health.py` values; Share's live sessions come from `host/sunshine_sessions.py` through the existing `PerformanceMonitor` worker. See [connection status](connection-status.md). `host/stream_display.py` is Sunshine's `global_prep_cmd`: it switches an HDR screen to SDR for SDR clients and optionally to the client's resolution, and restores it; see [video quality](video-quality.md).

## Settings ownership

| Owner | Values |
|---|---|
| Big Remote Play JSON | UI choices, Home role, per-task workflow state |
| Share | Capture, encoder, host audio, maximum bitrate, network exposure |
| Connecting PC | Stream resolution/FPS/bitrate, display mode, audio-on-host preference |
| Connect task dialogs | Native input, controller, codec and decoder preferences (one owner, no second advanced-client window) |
| Sunshine | Native server configuration, credentials database, app library |
| Secret Service | Saved administrative passwords and provider auth tokens |

The Moonlight adapter writes native **QSettings serialization keys**, not C++/QML property names. It preserves key case, percent escapes, unrecognized entries and paired-device sections, and refreshes the file before each batch write. A concurrently running Moonlight instance can still write afterward: edit settings between sessions when practical. Reset removes known streaming preferences, not the identity database.

The Sunshine adapter merges native settings and keeps a custom `file_apps` path. Automatic capture/encoding removes the native options instead of writing empty option values. Startup watches the child for an early exit and local-API readiness for a bounded interval. The server child does not inherit session-wide vkBasalt activation, which is a game post-processing layer; games launched directly by Big Remote Play retain the user's environment. Pairing follows the current config API: `GET /api/pin` lists waiting clients and `POST /api/pin` carries the chosen `pairing_id` (older servers without the list receive `{pin, name}`). On the client, pairing success is the server certificate Moonlight stores for the address, and a stream counts as started only when Moonlight's protocol log starts the video stream; `moonlight list` is not used on the connection path because it can take about 20 seconds. Adding the Desktop entry must not replace the existing library. Sunshine resolves relative `file_apps`, `file_state` and similar paths in its own data directory (`$XDG_CONFIG_HOME/sunshine`), not beside the configuration file it is started with, so `SunshineHost.apps_file()` resolves them the same way. Invalid or unreadable configuration is not interpreted as an empty file to overwrite.

## Native resources

Python lives in `src/`; the distribution package supplies resources under `usr/share/`. The wheel alone does not carry the complete desktop installation. Local symbolic assets are exposed in `icons/hicolor/scalable/actions` using relative links. The system hicolor index must remain authoritative so native Adwaita resource icons continue to resolve.

### Python layout on rolling distributions

The native BigLinux/Manjaro package deliberately does **not** install the application below `/usr/lib/python3.X/site-packages`. That location is tied to the Python minor version used when the package was built and can become invisible after a rolling upgrade from, for example, Python 3.14 to 3.15.

Instead, the package extracts the pure-Python wheel into `/usr/lib/big-remote-play`. `/usr/bin/big-remote-play` is itself a Python program with the direct shebang `/usr/bin/python3 -I`; it derives the private directory from its installed prefix, adds only that directory to `sys.path`, and calls `big_remote_play.app:main` in the same interpreter process. Isolated mode ignores `PYTHONPATH`, the current directory and the user's site-packages, while the active system installation still supplies distribution-managed dependencies such as PyGObject.

This avoids three failure modes in the previous compatibility wrapper:

- starting one Python process only to probe an import and then starting another;
- choosing an arbitrary stale package from several `python3.X` directories;
- exposing the current interpreter to an entire old `site-packages` tree, including ABI-bound extension modules.

`tools/release/install_private_wheel.py` validates and extracts the wheel. It rejects path traversal, generated bytecode, unsupported wheel data schemes and native extension modules. If Big Remote Play ever gains a compiled Python extension, this layout must be reviewed rather than bypassing that rejection.

The standard PEP 517 `gui_scripts` entry point remains in the wheel for Python environments and Nix, where the interpreter and package closure are installed together. The private `/usr/lib/big-remote-play` layout is the native rolling-distribution policy.

Official references:

- [Python isolated mode (`-I`)](https://docs.python.org/3/using/cmdline.html#cmdoption-I)
- [PyPA entry-point specification](https://packaging.python.org/en/latest/specifications/entry-points/)
- [Arch PKGBUILD packaging functions](https://man.archlinux.org/man/PKGBUILD.5)
- [Filesystem Hierarchy Standard: `/usr/lib`](https://refspecs.linuxfoundation.org/FHS_3.0/fhs/ch04s06.html)

## Persistence and migration

Writes of sensitive files use temporary files, owner-only permissions and atomic replacement. JSON writers merge independent key updates under a process lock. The legacy config-directory migration moves missing descendants but retains conflicting legacy files; it never deletes a conflicting library automatically.

Backup restore checks size, entry types and destination paths before writing, respects Moonlight's actual location, and atomically replaces each file. This is not a transactional rollback mechanism for the entire archive.

## External boundary

The app coordinates separately installed Sunshine, Moonlight, VPN clients, PolicyKit and the audio server. Hermetic UI tests substitute these adapters. They do not establish hardware compatibility, frame pacing, streaming latency or actual authorization behavior.

`utils/vpn_accounts.py` owns every Tailscale/Headscale/ZeroTier CLI call — listing and switching profiles, connecting (`tailscale up`), pausing (`down`), reading `BackendState`, joining/leaving ZeroTier networks and the one-time ZeroTier user access. Its command runner is injectable, so the UI never builds VPN argv itself and the flow is testable without a daemon. The GTK-free `private_network/` package owns provider status, REST APIs, credentials, connection history and diagnostics; see [private-network architecture](private-network-architecture.md). The only privileged shell helpers left are the dependency installer and the firewall rule.

## Audio ownership

`utils/audio.py` owns every sound-server call. Sunshine records the monitor of an
output, never a microphone. Automatic mode leaves `audio_sink` unset and writes
nothing to the sound server; application streams are never moved. The only routing
the app adds is a PipeWire port link ("bridge") while a client makes Sunshine mute
this computer, owned by a session token and removed only while its id still joins
the same ports. `AudioRoutingSession` keeps a 0600 record in `$XDG_RUNTIME_DIR` so a
later start adopts or cleans up after a crash. See [audio architecture](audio-architecture.md).

See [host/network policy](host-network-policy.md) for the actual settings precedence
and the separation between VPN, Headscale hosting and public DNS.
