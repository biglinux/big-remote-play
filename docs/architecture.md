# Architecture and ownership

`app.py` owns the GTK application and theme. The base stylesheet remains loaded
for the application lifetime; the Gamer preset is one higher-priority provider
held by identity and removed when another appearance or high contrast is active.
`ui/main_window.py` owns permanent Home, role-specific instructions, task
navigation and lazy private-network pages. Home presents Share and Connect
before the secondary internet-access path; technical component names appear
there only when setup is required. Role selection itself does not install
software, start sharing or change a network.

`HostView` gathers GTK values on the main thread, then uses a worker for Sunshine/audio/process work. `GuestView` distinguishes discovery, pairing and streaming, uses attempt generations/cancellation events, and applies UI results through the main loop. Background discovery is passive while Connect is visible; explicit Search may perform the bounded subnet scan. Home does not start that scan. mDNS discovery (`utils/network.py`, `avahi-browse`) is bounded by a timeout; when one stale announcement keeps `avahi-browse` past it, the computers already resolved are kept instead of dropping every result.

Home's **Guided setup** (`ui/guided_setup.py`) asks two questions, gets this computer ready with the shared installation checklist and hands over: it pushes question pages on Home's navigation view and ends in Share, Connect, or a method's page in Connect your devices; it implements no network or sharing flow of its own. Opening it never installs anything, starts sharing, connects to a computer or changes a network; an installation runs only from **Install what's needed**. Its detection of an existing secure connection runs on a `Worker` that drops results from an older generation or after cancellation, and the ready step continues by itself only from the page that is on screen, so a page that is no longer visible cannot move the guide forward. The detection page (*Let's create a secure connection*) carries its own explanation and is replaced by its result: a ready connection with **Use this connection**, one missing step with **Continue** to the method's page, or, with nothing set up, **How do you want to connect?**. Covered by `tests/test_guided_setup.py` and, for the Home prompt, `tests/test_guided_home_review.py`; the steps for people are in the [user guide](user-guide.md#guided-setup).

`utils/dependencies.py` (GTK-free) owns which components a task needs, how they are detected (a fresh `PATH` lookup each time, Flatpak for the VPN clients) and the installation plan: Pamac as the user, or the allowlisted `install-components.sh` through `pkexec`; success is decided by looking again. `ui/dependency_installer.py` (`ComponentChecklist`, `InstallDialog`) is the only installation interface and allows one transaction at a time. See [installing what a task needs](dependency-installer.md).

Share's pairing requests come from `host/pairing_requests.py` (`RequestTracker`: new, gone and expired requests from Sunshine's `GET /api/pin`) and are answered in `ui/pairing_prompt.py`; see [pairing requests](pairing-requests.md). `host/share_history.py` stores the sessions **Connected now** showed (start, end, device name, 0600) for **Support → Connection history** (`ui/connection_history.py`); see [connection history](connection-status.md#connection-history-share). `host/connection_notices.py` (GTK-free) turns the same list into one desktop notification per new session, sent with `Gio.Application.send_notification`; see [connection notifications](connection-status.md#connection-notifications-share). Connect is a native `Adw.ViewStack` with **Computers** and **Advanced options**, like Share's tabs. Connection cards (`ui/connection_cards.py`) render `utils/connection_health.py` values; Share's live sessions come from `host/sunshine_sessions.py` through the existing `PerformanceMonitor` worker. See [connection status](connection-status.md). `host/stream_display.py` is Sunshine's `global_prep_cmd`: it switches an HDR screen to SDR for SDR clients and optionally to the client's resolution, and restores it; see [video quality](video-quality.md).

**Game Window** shares one game window without the desktop. `host/game_windows.py` lists open, capturable game windows (KWin scripting on Wayland, EWMH through `host/x11_windows.py` on X11), classifies their process trees and never persists window ids. `host/window_capture.py` is a helper process that owns the ScreenCast portal session (or X11 source), a private headless `kwin_wayland --virtual` screen and the GStreamer mirror, and tears them down together; Sunshine is started with `capture = kwin` and only that screen's `WAYLAND_DISPLAY`. `StreamDisplay` keeps owning the real monitors and is not used by this mode. When the game replaces its window or changes size, the helper follows the same game only; see [when the game changes mode](game-window.md#when-the-game-changes-mode).

**Host input priority** (`host/input_priority.py`, GTK-free; `ui/input_priority.py` for Share's preference and status row) is a helper process started with sharing when the setting is on. It reads this computer's physical keyboards and mice and, while one is used, holds Sunshine's virtual keyboard and mouse with an exclusive evdev grab after releasing their held keys; controllers are never touched. It runs as the user when the input devices allow it, otherwise as a fixed program through the PolicyKit action `br.com.biglinux.remoteplay.input-priority`. See [host input priority](host-input-priority.md).

## Navigation map

```text
Sidebar                        Content
  Home                           hero (logo, Start with the guided setup) · Share · Connect
  Share       ─ Overview         source → Start sharing; requests; Connected now;
                                 Available over the internet; 3. Connect the other PC
              ─ Preferences      sound, mouse and keyboard (Host input priority), access
              ─ Support          live latency · Connection history · server tools · diagnostics
  Connect     ─ Computers        one card per computer; choosing it connects
              ─ Advanced options Connect again · Other ways to connect · On this computer
  Connect your devices           three cards (switch, state, network, devices); Advanced links
    a method (card)              its page: Devices | Advanced
  ─────────────
  Share, Connect, Connect your devices: a Running/Stopped pill on the entry
  Home: no service cards
  Share, Connect, Connect your devices: one card per service
```

Two tasks, **Share** (this computer runs the game) and **Connect** (play on
this device), carry the normal path; everything else is reached from them or
from the guided setup. Technical, optional and diagnostic controls live in
named secondary places (**Advanced options**, **Technical details**,
**Connection details**, **Support**). The sidebar's service cards and
indicators are described in [service status cards](service-status-cards.md),
the private-network pages in [Connect your devices](connect-your-devices.md).
The navigation pill of each task comes from `ui/task_activity.py` (GTK-free:
probes, the views' own state listeners and `ProviderStatus`); see
[task states](service-status-cards.md#task-states-in-the-navigation).

## Settings ownership

| Owner | Values |
|---|---|
| Big Remote Play JSON | UI choices, Home role, per-task workflow state, the sessions shared from this computer (`history/shared.json`) |
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

This avoids three failure modes of a compatibility wrapper that probes for an interpreter:

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

Backup/restore is owned by `utils/backup_restore.py`. It writes a versioned, hashed archive in separate application/Sunshine/Moonlight namespaces, checks bounded size, entry types and destination paths before writing, respects Moonlight's actual location, atomically replaces each file and rolls the complete affected set back after an apply failure. The preferences window only selects files, confirms destructive actions and marshals worker results to GTK.

## External boundary

The app coordinates separately installed Sunshine, Moonlight, VPN clients, PolicyKit and the audio server. Hermetic UI tests substitute these adapters. They do not establish hardware compatibility, frame pacing, streaming latency or actual authorization behavior.

**Connect your devices** is one `Adw.NavigationView` inside the main window (`MainWindow.network_navigation`). Its root page (`ui/remote_connection.py`) shows a card per method; Tailscale, ZeroTier and Headscale each open a `ProviderPage` (`ui/provider_page.py`, **Devices | Advanced** in the window header) from those cards or from the Share/Connect sidebar cards (`MainWindow.open_provider`). Set up, Add another device, Manage network, API access and the guides are pushed on the same navigation, and a pushed page's worker stops when it leaves the stack. Methods are independent; each lists only its own devices. See [Connect your devices](connect-your-devices.md).

`utils/vpn_accounts.py` owns every Tailscale/Headscale/ZeroTier CLI call — listing and switching profiles, connecting (`tailscale up`), pausing (`down`), reading `BackendState`, joining/leaving ZeroTier networks and the one-time ZeroTier user access. Its command runner is injectable, so the UI never builds VPN argv itself and the flow is testable without a daemon. The GTK-free `private_network/` package owns provider status, REST APIs, credentials, connection history and diagnostics; see [private-network architecture](private-network-architecture.md). Privileged work goes through fixed helpers in `usr/share/big-remote-play/scripts/`, each started with `pkexec` and explicit, validated arguments: `install-components.sh` (the dependency installer, used only without Pamac), `configure_firewall.sh` (the firewall rule), `drop_guest.sh` (ends one device's stream from Share's monitoring), `headscale-server-helper.sh` (a Headscale server on this computer, PolicyKit action `br.com.biglinux.remoteplay.headscale-server`) and `input-priority-helper.sh` (host input priority, action `br.com.biglinux.remoteplay.input-priority`); the two actions are declared in `usr/share/polkit-1/actions/`. The full list with the direct `pkexec systemctl`/`tailscale`/`zerotier-cli` calls is in [private-network security](private-network-security.md#privileges).

## Audio ownership

`utils/audio.py` owns every sound-server call. Sunshine records the monitor of an
output, never a microphone; **Preferences → Audio → Microphone** (off by
default) links the default microphone into the app's own output instead, and
**Voice calls** (off by default) stops keeping call programs out. Automatic mode leaves `audio_sink` unset and writes
nothing to the sound server; application streams are never moved. The app adds
only PipeWire port links ("bridges", for example while a client makes Sunshine
mute this computer) and, during a voice call or a Game Window share, its own
output `big-remote-play-stream`; each is owned by a session token and removed
only while it is still the object the app created. `AudioRoutingSession` keeps a 0600 record in `$XDG_RUNTIME_DIR` so a
later start adopts or cleans up after a crash. See [audio architecture](audio-architecture.md).

See [host/network policy](host-network-policy.md) for the actual settings precedence
and the separation between VPN, Headscale hosting and public DNS.
