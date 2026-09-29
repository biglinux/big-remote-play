# Private-network test matrix

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

## Connect your devices (simple page)

| Scenario | Status |
|---|---|
| Every state maps to one sentence and one button (ready, turned off, service stopped, permission, sign-in, waiting, set up, install, problem, no internet) | Unit tested (`tests/test_network_plan.py`), UI tested (`tests/test_private_network_ui.py`) |
| Tailscale signed in but turned off → **Turn on** | **Real verified** 2026-09-29: `Stopped` → `Running` in 0.1 s; the first attempt without operator rights answered "Access denied", which led to the one-time operator step (machine restored to `Stopped`, no operator, afterwards) |
| `tailscale up` asks to repeat non-default settings | Unit tested with the client's own message; keys, `--reset`, `--force-reauth` and unknown flags refused |
| ZeroTier service stopped → **Fix**; existing networks found after start | **Real verified** 2026-09-29: stopped → "The connection service is stopped"; started → connected with the 12 existing networks, no new setup (service stopped again afterwards) |
| My devices, favourites, rename, recent connections, remove | Unit tested, UI tested; real Tailscale device list shown 2026-09-29 |
| Layout at 400, 720 and 1024 px, 150 % text, RTL (Hebrew), Japanese | Rendered offscreen (Broadway) 2026-09-29 |
| Two physical computers finishing an invitation (link, QR, ZeroTier approval) | **Pending** |

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
| Pairing code shown by the app, approved through Sunshine's API | **Real verified** (after fixing two pairing bugs, see the change record) |
| Stream over WireGuard | **Real verified**: Sunshine logged “New streaming session started”, 13–14 MB crossed the tunnel in 20 s at a 6 Mbps request |
| Stop from the app; history records the session; “Connect again” offers it | **Real verified** |
| Unpaired client: Moonlight window opens but no stream → nothing recorded | **Real verified** |
| Two physical computers across the internet, relayed (DERP) path, CGNAT | **Pending** |

## Visual and accessibility checks

Rendered offscreen with simulated data on 2026-09-28 (Broadway backend): 360, 480, 720 and 1024 px widths; light and dark; 150 % text; RTL (Hebrew); Japanese. Results: actions wrap on narrow widths, no horizontal scrolling, RTL mirrors correctly, CJK text fits. **Pending:** a real high-contrast theme session, Orca screen-reader navigation, keyboard-only walkthrough on real hardware.

## What still needs a person

- Real Tailscale device invite and user invite (needs an API access token and a second account).
- Real ZeroTier network creation, member approval and managed IP on a second computer.
- A real Headscale server (v0.27+): version, users, nodes, pre-auth key, registration approval, routes.
- Sunshine → Moonlight session over each provider, including relayed paths and latency reading.
- Native-speaker review of the 367 new strings in each of the 31 translated catalogs.
