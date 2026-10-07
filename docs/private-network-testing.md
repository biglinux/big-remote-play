# Private-network testing

What has been verified for Tailscale, Headscale and ZeroTier, and how. Use it to plan release acceptance ([release testing](release-testing.md)) and to avoid claiming more than was tested.

## Evidence levels

| Level | Meaning |
|---|---|
| **Unit tested** | Automated test of parsing, validation or a decision, with no GTK |
| **Integration simulated** | Automated GTK or service-level test with fake CLIs, fake HTTP transport and an in-memory keyring |
| **Real service verified** | Run against a real client or API on a developer machine, read-only, with the date |
| **Pending** | Needs a real network, device, server or person; not yet done |

Automated tests live in `tests/test_private_network_backend.py`, `tests/test_private_network_ui.py`, `tests/test_vpn_networks.py`, `tests/test_vpn_accounts.py` and `tests/test_security_regressions.py`.

## Matrix

| Scenario | Tailscale | Headscale | ZeroTier |
|---|---|---|---|
| Status parsing and state model | Unit tested; **real service verified** 2026-09-28 (`Running`, one peer with an expired key shown offline) | Unit tested (same client) | Unit tested; **real service verified** 2026-09-28 (unreadable service token → “Allow” state) |
| Sign-in / connect succeeds only on `Running` / `OK` + managed IP | Unit tested, integration simulated | Unit tested, integration simulated | Unit tested, integration simulated |
| Pending browser sign-in is not a failure | Integration simulated | Integration simulated | — |
| Waiting for approval (Node ID shown) | Unit tested (`NeedsMachineAuth`) | — | Integration simulated |
| Disconnect keeps accounts (`down`, not `logout`) | Integration simulated | Integration simulated | Pending (stops `zerotier-one`) |
| Leave network (confirmation first) | — | — | Integration simulated |
| Join with invalid Network ID refused | — | — | Unit tested, integration simulated |
| Create network (private, managed IPv4 pool) | — | — | Unit tested (Legacy and New Central request bodies); **real service verified** 2026-09-29 on Legacy Central: temporary private network created, joined, approved through the API, managed IPv4 from the pool, then left and deleted |
| List members / authorize / revoke / remove | Unit tested (devices, authorize, delete) | Unit tested; **real server verified** 2026-09-29 (nodes, `/auth/register` approval of a sign-in link) | Unit tested; **real service verified** 2026-09-29 (member listed, authorized via API) |
| Token accepted / refused / revoked | Unit tested (401 → “refused”) | Unit tested (401, server offline) | Unit tested; **real service verified** 2026-09-28 (Legacy token accepted with `Authorization: token`) |
| OAuth client (token exchange, caching, tags) | Unit tested | — | — |
| Share one machine vs invite person | Unit tested, integration simulated; pending real invitation | — | — |
| One-time sign-in key | Unit tested; pending real key | Unit tested; **real server verified** 2026-09-29: pre-auth key created through the API, computer joined with `--auth-key=file:` | — |
| Headscale `/health`, `/version`, users, wrong API key | — | Unit tested; **real server verified** 2026-09-29 (Headscale 0.29.4 over HTTPS; wrong key → “refused”) | — |
| ZeroTier permission without a password | — | — | **Real service verified** 2026-09-29: “Allow” read the service token once, wrote `~/.zeroTierOneAuthToken` (0600), all 12 memberships then readable (pkexec replaced by sudo for the unattended run) |
| Routes approval | — | Unit tested; pending | — |
| Path: direct / relayed / peer relay, latency | Unit tested (output formats); **real service verified** failure path 2026-09-28 (“node key has expired”) | **Real verified** 2026-09-29: direct path over WireGuard, latency “0s” parsed | Not supported by the client |
| Connect lists private peers with readiness | Integration simulated | Integration simulated | Integration simulated (managed IPs) |
| Share shows the usable private address | Integration simulated; **real service verified** endpoint present 2026-09-28 | Integration simulated | Integration simulated |
| Diagnosis: network down / host offline / Sunshine missing | Unit tested | Unit tested | Unit tested |
| History: only started streams, retention, clear | Unit tested, integration simulated | same | same |
| Legacy plaintext secrets migrated | Unit tested (keyring available / locked) | same | same |

## A method's page (Devices | Advanced)

| Scenario | Status |
|---|---|
| Five state words per status and the one Connection action per state | Unit tested, UI tested (`tests/test_connect_your_devices.py`) |
| Pages, not dialogs: the list, a method, Set up, Add device, API access, Accounts and networks, guides; header Back; work stops when navigation jumps past a page | UI tested |
| Two methods connected at once keep their own devices; ZeroTier one network at a time | Unit tested, UI tested |
| Remove device only with an API and a matched device, after a confirmation; Manage devices otherwise | Unit tested, UI tested (fake APIs) |
| Rename (this computer only), Check connection, Play on this computer | UI tested |
| Start: service first, then a signed-in connection; Headscale switches the Tailscale profile; never a sign-in by itself | Unit tested |
| Stop: `tailscale down` (accounts kept); ZeroTier's service through PolicyKit; Headscale never stops Tailscale's connection | Unit tested, UI tested |
| Back to Share / Back to Connect once connected | UI tested |
| Layout at 400 and 1100 px | Rendered offscreen 2026-10-06 |
| Against real clients and APIs (start, stop, sign-in, approval, removal) | **Pending** |

## The one next step (guided setup)

| Scenario | Status |
|---|---|
| Every state maps to one sentence (ready, turned off, service stopped, permission, sign-in, waiting, set up, install, problem, no internet) | Unit tested (`tests/test_network_plan.py`), UI tested (`tests/test_guided_setup.py`) |
| Tailscale signed in but turned off → **Start** (`turn_on`) | **Real verified** 2026-09-29: `Stopped` → `Running` in 0.1 s; the first attempt without operator rights answered "Access denied", which led to the one-time operator step (machine restored to `Stopped`, no operator, afterwards) |
| `tailscale up` asks to repeat non-default settings | Unit tested with the client's own message; keys, `--reset`, `--force-reauth` and unknown flags refused |
| ZeroTier service stopped → **Start** (`start_service`); existing networks found after start | **Real verified** 2026-09-29: stopped → "The connection service is stopped"; started → connected with the 12 existing networks, no new setup (service stopped again afterwards) |
| Two physical computers finishing an invitation (link, QR, ZeroTier approval) | **Pending** |

## Joining ZeroTier (2026-09-29)

Real service: ZeroTier One 1.16.2 on the development machine, with three existing memberships (A, B, C; their ids are not published).

| Scenario | Status |
|---|---|
| Service stopped: `zerotier-cli join` prints `0 join connection failed` (exit 1) | **Real verified**: read as the service not running, never as a network failure |
| Service stopped → join in the app: starts the service, joins, **Connected** | **Real verified** (the privileged start was run with `sudo` instead of the desktop PolicyKit prompt) |
| Network A (the owner's own): CLI `join` exit 0, `-j join` status `OK`, address and `zt…` interface | **Real verified**; the app shows **Connected** with the network name, in the GUI |
| Network B: `ACCESS_DENIED` | **Real verified**: **Waiting for authorization** with the computer name, Node ID and Copy; never “failed” |
| Network C: `NOT_FOUND` (deleted network) | **Real verified** read-only |
| Typo of a real code (last digit changed): controller answers `NOT_FOUND` | **Real verified**: **Network code not found**, membership removed automatically |
| Code of a controller that does not exist | **Real verified**: stays `REQUESTING_CONFIGURATION`; after 45 s **The network has not answered yet**; **Cancel the request** left the network |
| Memberships afterwards | **Real verified**: the same three as before |
| Authorization given later, permission, port error, old client, SSO networks, cancellation | Unit tested (`tests/test_zerotier_join.py`), UI tested (`tests/test_zerotier_join_ui.py`) |

## Guided setup and connection cards (2026-09-29)

| Scenario | Status |
|---|---|
| Share/Connect + same network → the task, no VPN; somewhere else → detection; ready connection → **Use this connection**; one step missing → that method's page; nothing → method choice; ZeroTier code; Back; restart | UI tested (`tests/test_guided_setup.py`); detection **real**: found the connected ZeroTier network |
| Sidebar groups; the Connect your devices item always opens the list of methods | UI tested |
| Sunshine session → **Connected now** with the handshake address; gone at disconnect | **Real verified** with an isolated Sunshine 2026.914 + Moonlight Qt 6.1 lab instance (own port, state and credentials; sound output checked unchanged) |
| Quality thresholds (Excellent ≤ 30 ms, Good ≤ 70 ms, Unstable from 20 ms jitter or 10 % loss; see [connection status](connection-status.md#quality)), no answer, LAN/ZeroTier/Tailscale/Headscale/internet route | Unit tested (`tests/test_connection_health.py`); routes **real** on this machine (`zt…`, `lo`, default gateway) |
| Connection quality (a method's Advanced): traffic from interface counters, stability, direct/relay, no device online | UI tested; **real**: ZeroTier with no other device online → “No other device online to measure” (controllers excluded) |
| Layout: 400, 720, 1100 px, 150 % text, RTL + light theme | Rendered offscreen (Broadway) 2026-09-29 |
| Connect card during a real stream between two computers | **Pending** (simulated data only) |

## Network conditions

| Condition | Status |
|---|---|
| LAN only (no VPN) | Existing discovery tests; unchanged |
| IPv4 / IPv6 / dual stack detection | Unit tested; **real** 2026-09-28: dual stack detected |
| CGNAT address on this computer | Unit tested; real CGNAT link **pending** |
| NAT with port forwarding (direct access) | Pending |
| Direct vs relayed path in a real game session | Pending |
| Host offline | Unit tested, integration simulated |
| VPN disconnected | Unit tested, integration simulated |
| API unavailable / timeout | Unit tested |
| Wrong token / expired key | Unit tested |
| Firewall detection | Unit tested; **real** 2026-09-28: ufw active detected |

## Share ↔ Connect end to end

Verified on 2026-09-29 on one physical machine with two isolated Linux network namespaces acting as two computers, joined to a real Headscale 0.29.4 server over HTTPS by two real `tailscaled` instances with their own WireGuard devices (Sunshine v2026.914, Moonlight Qt from the distribution, AMD GPU, KDE Wayland). The host's own network, DNS and Tailscale were not involved and were checked unchanged afterwards.

| Step | Status |
|---|---|
| Game PC joins with a pre-auth key; playing PC joins by sign-in link approved through the API | **Real verified** |
| Connect lists the game PC from Headscale with “Sharing found” (real `/serverinfo` over the tunnel) | **Real verified**, in the real Big Remote Play window |
| Diagnose: network connected, Sunshine answers, direct path | **Real verified** |
| Pairing code shown by the app, approved through Sunshine's API | **Real verified** |
| Stream over WireGuard | **Real verified**: Sunshine logged “New streaming session started”, 13–14 MB crossed the tunnel in 20 s at a 6 Mbps request |
| Stop from the app; history records the session; “Connect again” offers it | **Real verified** |
| Unpaired client: Moonlight window opens but no stream → nothing recorded | **Real verified** |
| Two physical computers across the internet, relayed (DERP) path, CGNAT | **Pending** |

## Visual and accessibility checks

Rendered offscreen with simulated data on 2026-09-28 (Broadway backend): 360, 480, 720 and 1024 px widths; light and dark; 150 % text; RTL (Hebrew); Japanese. Results: actions wrap on narrow widths, no horizontal scrolling, RTL mirrors correctly, CJK text fits. **Pending:** a real high-contrast theme session, Orca screen-reader navigation, keyboard-only walkthrough on real hardware.

## Still to verify

The scenarios marked **Pending** above, together with real invitations, real Headscale servers and Share ↔ Connect over each provider, are part of [release acceptance](release-testing.md#required-target-machine-checks).
