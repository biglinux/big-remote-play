# Private-network change record

A durable record of the private-network review completed on 2026-09-28 and the simplification of 2026-09-29: what the integration did before, what was wrong and why, what replaced it, and what still needs real-world validation. Current behavior is documented in the [user guide](user-guide.md), [private-network architecture](private-network-architecture.md) and [private-network security](private-network-security.md); this page explains why it changed.

## State before the change

- `ui/private_network_view.py` (about 2,100 lines) mixed GTK widgets, `subprocess` calls, an embedded ZeroTier HTTP client, keyring access, history storage and parsing.
- “My network” (`CreatePage`) and “Join a network” (`ConnectPage`) each probed the VPN clients themselves and represented state with booleans (`_logged_in`, `_defers_to_join`).
- ZeroTier network creation and joining ran a whole shell script as root through `pkexec`; the Headscale “server” was a Docker/Caddy/Cloudflare installer also run as root.
- Connect found hosts only by local discovery, a typed address or a search code. Share showed LAN addresses only.
- “Previous networks” was the only history: saved join details, including auth keys.

## Problems found and root causes

| # | Problem | Root cause | Resolution |
|---|---|---|---|
| 1 | **World-readable plaintext secrets.** On the test machine, `private_network/history.json` (0644) held 18 auth keys, 9 API keys and Cloudflare API tokens; `private_network.json` (0644) held 16 more entries with secrets. | Older versions wrote secrets straight into JSON; later versions only changed *new* writes and kept reading old values “for compatibility”. | `legacy.py` makes the files 0600, moves each value to the keyring and removes it from JSON only after the keyring accepted it. History now writes an allowlist of non-secret fields. |
| 2 | **API tokens on command lines.** The ZeroTier and Headscale helpers received tokens on stdin, then passed them to `curl -H "Authorization: …"` (visible in `/proc/<pid>/cmdline`). | Network administration implemented in shell. | Both helpers removed. REST calls happen in-process (`http.py`); no shipped script accepts a token. |
| 3 | **ZeroTier networks created without addresses.** The API created a network with only a name, so members got no managed IP. | Missing `ipAssignmentPools`/`routes`/`v4AssignMode`. | `zerotier_api.create_network()` sets a private network with a non-overlapping /24 pool; the local node joins and is approved. |
| 4 | **ZeroTier state unreadable without root.** On the test machine `zerotier-cli` failed with “authtoken.secret not found or readable”; “Join a network” therefore always looked disconnected and every read needed a password. | The per-user token file `~/.zeroTierOneAuthToken` was never set up. | One explicit **Allow** reads the service token via `pkexec cat` into the app, which writes the user file (0600) itself. |
| 5 | **A joined ZeroTier network waiting for approval was reported as “Connection failed”.** | Success was the helper's exit code plus “any network OK”. | Joining waits for the service's verdict per network; `ACCESS_DENIED` becomes “Waiting for approval” with the Node ID to send. |
| 6 | **The ZeroTier API token was loaded back into an entry field.** | Pre-filling convenience. | Secret fields are never pre-filled and are cleared after saving; only a masked hint is shown. |
| 7 | **Tailscale “auth key” saved in history and pre-filled.** | History stored every form field. | One-time keys are never stored. |
| 8 | **The Headscale installer** ran as root in `/root`, opened 80/443/41641 without asking, served the API with `Access-Control-Allow-Origin: *`, looked up the public IP from a third-party site and required a Cloudflare token. | A custom deployment embedded in the app. | Removed; replaced by a guide and commands for the administrator's own server ([VPS and Headscale](vps-headscale.md)). |
| 9 | **Headscale joining required an auth key.** | Form validation. | Joining without a key opens the sign-in link; an administrator approves it (`/api/v1/auth/register`). |
| 10 | **Connect ignored the private network.** | No shared provider layer. | `candidate_hosts()` offers the peers of connected providers, probed for Sunshine, beside local results. |
| 11 | **Share never showed the private address.** | Same. | “Available over the internet” lists the name and address from each connected provider. |
| 12 | **Firewall rules applied without saying which ports, or that they are permanent**, and with no timeout. | Direct button → `pkexec`. | A confirmation lists every port from `integration_contracts.sunshine_stream_ports()`; the call is bounded. |
| 13 | **Toasts interpreted markup**, so a network or device name with `&` or `<` could break or style the message. | `Adw.Toast` defaults to markup. | Toasts disable markup; all dynamic rows use `use_markup=False`. |
| 14 | **ZeroTier interfaces were shown as the LAN address** in Share's connection information. | The filter looked for “zerotier”, but interfaces are named `zt…`. | `zt*` excluded. |
| 15 | **Multiple ZeroTier interfaces** (twelve on the test machine) with no way to see them without root. | Repeated joins/creations; unreadable state. | After **Allow**, every membership is listed in Manage network with **Leave network**. |
| 16 | **Logs could carry credentials** from CLI output or HTTP errors. | No central redaction. | `RedactingFilter` on the app logger and handlers; API details stripped of the sent credential. |

## Found by the real end-to-end session (2026-09-29)

| # | Problem | Root cause | Resolution |
|---|---|---|---|
| 17 | **Pairing from Share failed with current Sunshine** (HTTP 400: “pairing_id must contain exactly 32 hexadecimal characters”). | Sunshine now lists pending requests with `GET /api/pin` and needs the chosen `pairing_id` in `POST /api/pin`. | `SunshineHost.send_pin()` reads the pending list, uses the only waiting request, asks which computer when several wait, explains when none waits, and keeps the old `{pin, name}` form for Sunshine without the list. The PIN is sent off the GTK thread. |
| 18 | **Connect reported a pairing error after a successful pairing.** | Current Moonlight Qt accepts the PIN but never exits `moonlight pair`; the client waited 90 s for an exit code. | Success is the server certificate Moonlight stores for that address appearing or changing; the `pair` process is then stopped gracefully. |
| 19 | **Connect told people sharing was not running.** | `moonlight list` takes about 20 s with current Moonlight; the client gave it 5 s and treated the timeout as “no apps”. | Connect no longer uses `list`: Sunshine's `/serverinfo` decides reachability, Moonlight's stored certificate decides pairing. |
| 20 | **A Moonlight error window was recorded as a session.** | “Process alive after one second” was the success signal. | Moonlight's output is always read; success is its protocol log starting the video stream. Otherwise Connect offers to pair again. |
| 21 | **“Sunshine is already running” when it was not.** | A stale PID file whose PID the kernel had reused. | The PID must belong to a process named `sunshine`. |
| 22 | **Direct Tailscale paths reported as unreachable.** | `tailscale ping` prints Go durations (`0s`, `523µs`, `1.2s`), not only `N ms`. | Durations are parsed in every Go unit. |
| 23 | **A tailnet joined outside Big Remote Play was shown as the wrong product.** | A missing profile record defaulted to “Tailscale”. | The client's `ControlURL` decides when nothing was recorded. |

The Sunshine configuration used for daily play on the test machine had `upnp = enabled` together with `origin_web_ui_allowed = wan`, the combination that can map the administration panel publicly; it was reported to the owner and not changed.

## Simplification of the internet-play pages (2026-09-29)

| # | Problem | Resolution |
|---|---|---|
| 24 | After the first page everything was named after the product, with three header tabs (Devices · Connect this computer · Internet play) cut off at 1024 px. | One page, **Connect your devices**, with a status card; provider pages open from it with a titled **Back** button; the tabs are gone. |
| 25 | A signed-in Tailscale that was only turned off (`Stopped`) was sent to "Sign in with browser". | `plan.py` chooses one next step; **Turn on** reconnects the existing account (`resume_tailscale`). |
| 26 | The sidebar said "Tailscale · Running" (the daemon) while the page said "Not connected"; a stopped ZeroTier had no **Fix**. | Network pages hide the service rows; the card states the problem and its button. |
| 27 | No list of the person's devices or recent connections on the internet page; no favourites or names. | **My devices** and **Recent connections** with friendly names, favourites, **Connect**, **Check connection** and **Remove**. |
| 28 | "Advanced" switch without explanation; tools spread over three pages. | **Advanced mode** (remembered) groups methods, this network, Manage network and the tools. |
| 29 | ZeroTier asked for a "16-character Network ID" and pointed to ZeroTier Central in simple mode. | "Type the network code you received"; the code is shown in groups of four and accepted with spaces or dashes. |
| 30 | The sidebar started five service probes every 3 s on every page. | Only the rows the page shows are probed; none on Home and network pages. |
| 31 | "Turn on" with PolicyKit ran `tailscale up` as root, waiting on a password prompt. | Found in the real test: the documented one-time `tailscale set --operator` is asked instead, and `up` runs as the user. |

## New structure

- `private_network/` (no GTK): `models.py`, `service.py`, `tailscale.py`, `zerotier.py`, `tailscale_api.py`, `zerotier_api.py` (Legacy and New Central), `headscale_api.py`, `http.py`, `credentials.py`, `history.py`, `diagnostics.py`, `redaction.py`, `legacy.py`.
- UI: `remote_connection.py` (Connect your devices), `network_dashboard.py` (Network details), `network_devices.py` (Manage network, Add device), `api_access_dialog.py`, `history_dialog.py`, `network_common.py` (worker, wording, copy rows), plus integration in `guest_view.py`, `host_view.py` and `main_window.py`.
- Tests: `test_private_network_backend.py` (117), `test_private_network_ui.py` (11), rewritten `test_vpn_networks.py`, updated security, flow and documentation tests.

Removed: `create-network_zerotier.sh`, `create-network_headscale.sh`, the ZeroTier HTTP helper inside the UI, `CreatePage`, auth-key persistence and 193 translation entries that only those parts used.

## Incident during the work

While the new tests were first written, one test called the legacy ZeroTier-token migration without an isolated configuration directory. It read the developer's real `~/.config/big-remote-play/zerotier/api_token.txt`, stored it in the test's in-memory keyring and deleted the file. The real keyring still held a ZeroTier token saved on 2026-09-22, and it was verified to work against ZeroTier Central afterwards; whether the deleted file contained the same token cannot be proven. Every test now runs with an isolated `XDG_CONFIG_HOME` and an offline private-network service (`tests/conftest.py`), so no test can reach real settings, a real keyring or a real VPN client.

## Verification summary

A full Share → private network → Connect → Moonlight session was run for real on 2026-09-29 through a Headscale server and WireGuard, in the real application window. See the [test matrix](private-network-test-matrix.md) for the per-scenario evidence level. In short: parsing, validation, security properties, API request shapes and GTK flows are unit tested or integration simulated; Tailscale status, the ZeroTier permission state and read-only ZeroTier Legacy Central calls were verified against real services on 2026-09-28. Streaming over each provider, Headscale against a real server, New Central, invitations and a real CGNAT link still need target-machine testing.

## Known gaps

- QR codes are generated by a small built-in encoder (no new dependency) for the Network ID, invitation links and the Headscale server address; credentials are refused.
- The setup entry now recommends a connected provider, then an installed provider that needs recovery, and uses the saved choice only to break ties. It does not automatically choose between duplicate peer rows in Connect when several providers reach the same host.
- No live latency or packet-loss reading during a stream; latency is measured on demand in **Diagnose**.
- The host side does not keep a session history (Sunshine exposes paired devices, not a reliable per-session log).
- New translations are structurally validated only; native review is pending for all 31 catalogs.
