# Private-network architecture

This document describes how Big Remote Play talks to Tailscale, Headscale and ZeroTier. Read it before changing anything under `src/big_remote_play/private_network/` or the private-network pages. User workflows are in the [user guide](user-guide.md); security rules are in [private-network security](private-network-security.md).

## Layers

```text
UI (GTK, main thread)            ui/remote_connection.py (Connect your devices hub),
                                 ui/network_dashboard.py (Network details),
                                 ui/network_devices.py (Add a device, Manage network),
                                 ui/api_access_dialog.py, ui/history_dialog.py,
                                 ui/private_network_view.py (Set up the connection),
                                 guest_view.py / host_view.py integration
        │  plain data only (models.py), through ui/network_common.Worker
        ▼
Service facade (worker threads)  private_network/service.py
        │
        ├── Providers            tailscale.py · zerotier.py (+ utils/vpn_accounts.py for CLI actions)
        ├── Provider REST APIs   tailscale_api.py · zerotier_api.py · headscale_api.py
        │                        all built on http.py
        ├── Credentials          credentials.py (Secret Service) · legacy.py (one-time cleanup)
        ├── Next step            plan.py (pure: statuses → one ConnectionPlan)
        ├── History              history.py (local streaming sessions) · devices.py (names, favourites)
        └── Diagnostics          diagnostics.py (bounded, read-only checks)
```

Rules that keep the layers honest:

- **The UI never builds a command line or an HTTP request.** `tests/test_security_regressions.py` fails if a UI module imports `urllib.request` or builds an `Authorization` header.
- **Every blocking call runs in `Worker.submit()`**, which delivers results on the GTK main loop and drops results from an older generation or a closed page.
- **The facade returns plain dataclasses** (`ProviderStatus`, `PeerDevice`, `HostCandidate`, `HostDiagnosis`), never widgets, subprocess objects or credentials.
- **`default_service()`** is the only way the UI obtains the facade. The test suite replaces its factory with `OfflinePrivateNetworkService`, so no test can reach a real daemon, keyring or API.

## The hub and its one next step

**Play over the internet** opens `RemoteConnectionPage`. It never asks for a product first. `plan.plan_connection(statuses, internet=…, preferred=…)` turns every provider's status into one `ConnectionPlan`, in this order:

1. a connected provider → `READY` (primary: Add a device or invite a player);
2. no interface has an address (`service.internet_available()`, local `ip -j addr` only) → `OFFLINE`;
3. the recommended provider's recovery: `WAITING` (approval), `TURNED_OFF` (`Recovery.RECONNECT`, e.g. Tailscale `Stopped`), `SERVICE_STOPPED`, `ALLOW` (ZeroTier permission), `SIGN_IN`, `PROBLEM` (client error);
4. nothing installed → `INSTALL` (Tailscale unless a method was chosen before); otherwise `SET_UP`.

`recommended_status()` chooses the provider: connected, then installed-with-a-recovery, then installed, then missing; the remembered method only breaks ties. The page maps each kind to one sentence and one button (`remote_connection.plan_words`); Home uses the same sentence.

One-click actions run on a `Worker` and re-read the state afterwards:

- **Turn on** — `VPNAccountManager.resume_tailscale()`: `tailscale up --timeout=30s` as the user. On "access denied" it asks once, through PolicyKit, for the documented `tailscale set --operator=$USER`, then retries as the user; `up` is never run as root. When `up` demands the profile's non-default settings, the command the client prints is run only if every flag is a plain setting from an allowlist (`--login-server`, `--operator`, `--accept-routes`, …); keys, `--reset` and `--force-reauth` are refused.
- **Fix** — `service.start_service()` → `systemctl enable --now` of the allowlisted unit through PolicyKit.
- **Allow**, **Sign in**, **Set up**, **Install** reuse the existing ZeroTier permission, join page and installer.

Friendly names and favourites live in `history/devices.json` (0600, two allowlisted fields per device, keyed by address). Removing a recent connection deletes that computer's sessions from the history and its preferences.

The sidebar's service rows are hidden on network pages — the card is the one source of truth, so a daemon "Running" never sits beside "Turned off" — and the 3-second service probe checks only the rows the current page shows (none on Home and network pages).

## State model

A provider reports exactly one `ConnectionState`:

| State | Meaning | Typical recovery |
|---|---|---|
| `UNAVAILABLE` | Client not installed, or its service is stopped | Install · Start service |
| `DISCONNECTED` | Usable, but not on a network now | Connect this computer |
| `NEEDS_AUTHENTICATION` | Sign-in required (Tailscale `NeedsLogin`) | Sign in |
| `CONNECTING` | The client is still starting or configuring | Wait / refresh |
| `NEEDS_AUTHORIZATION` | Joined, waiting for an administrator (ZeroTier `ACCESS_DENIED`, Tailscale `NeedsMachineAuth`, ZeroTier network without a managed IP) | How to approve |
| `CONNECTED` | The client itself reports a working network | — |
| `ERROR` | The client answered with something unusable, or Big Remote Play cannot read it | Allow access · Try again |

Only `CONNECTED` exposes a `reachable_address`. A stopped Tailscale client still lists the peers and addresses it knew; they are hidden because they cannot be reached.

**Tailscale and Headscale share one daemon.** `service._tailnet_owner()` decides which product the active profile belongs to: first the profile metadata Big Remote Play saved when it joined (`private_network/accounts.json`), then the MagicDNS suffix (`*.ts.net` means the Tailscale service). The other product reports `DISCONNECTED`.

## Capabilities

The UI shows an action only when `ProviderCapabilities` says the provider can do it in the current setup. Capabilities that need an administrative credential are true only when one is saved.

| Capability | Tailscale | Headscale | ZeroTier |
|---|---|---|---|
| Join / leave | CLI | CLI | CLI |
| Switch account | CLI profiles | CLI profiles | — (several networks at once) |
| Measure path (direct/relay, latency) | `tailscale ping` | `tailscale ping` | — |
| Create network | — (one tailnet per account) | — (the server is the network) | Central API token |
| List/remove devices | API token or OAuth client | Headscale API key | Central API token |
| Approve device/member | API (`/device/{id}/authorized`) | API (`/auth/register`, fallback `/node/register`) | Central API |
| Create sign-in key | API (`/tailnet/-/keys`) | API (`/preauthkey`) | — |
| Share one machine | **API access token only** | — | — |
| Invite a person | **API access token only** | — | — |
| Self-host | — | yes (guide, never automatic) | — |

Tailscale refuses user invites and device invites with OAuth-derived tokens, so an OAuth client never enables those two actions. Auth keys created by an OAuth client must carry the client's tags; the API access dialog asks for them.

## Upstream contracts

Checked on 2026-09-28. Re-check before changing a client.

| Provider | Contract used | Source |
|---|---|---|
| Tailscale CLI | `status --json` (`BackendState`, `Self`, `Peer[*].Online/Expired`, `CurrentTailnet`), `ping --c --timeout` output `pong from … via <ip:port \| DERP(x) \| peer-relay(…)> in N ms`, `up --auth-key=file:` (since 1.16), `switch --list --json` (since 1.96) | [CLI reference](https://tailscale.com/kb/1080/cli), `ipn/ipnstate/ipnstate.go` |
| Tailscale API v2 | `https://api.tailscale.com/api/v2`, `Authorization: Bearer`, OAuth token endpoint `/oauth/token` (1-hour tokens), `/tailnet/-/devices`, `/tailnet/-/keys`, `/tailnet/-/user-invites`, `/device/{id}/device-invites` | [API](https://tailscale.com/api), [OAuth clients](https://tailscale.com/kb/1215/oauth-clients) |
| ZeroTier Legacy Central | `https://api.zerotier.com/api/v1`, `Authorization: token`, `/status`, `/network`, `/network/{id}/member/{node}` with `{"config":{"authorized":true}}` | [Central v1 OpenAPI](https://docs.zerotier.com/openapi/central/v1.json) |
| ZeroTier New Central | `https://central.zerotier.com/api/v2`, `Authorization: Bearer` (service-account token), networks inside network groups, `…/member/{id}/authorize` | [Central v2 OpenAPI](https://docs.zerotier.com/openapi/central/v2.yaml), [tokens](https://docs.zerotier.com/tokens/) |
| ZeroTier CLI | `-j info` (`address`), `-j listnetworks` (`status`, `assignedAddresses`), user token file `~/.zeroTierOneAuthToken` | `zerotier-one` source (`one.cpp`) |
| Headscale 0.29 | `/health`, `/version` (0.27+), `/api/v1/user`, `/api/v1/node`, `/api/v1/preauthkey` with **numeric user ID** (0.26+), `/api/v1/auth/register` with the user **name** and the `hskey-authreq-…` ID from the sign-in link, `/api/v1/node/{id}/approve_routes` (`/api/v1/routes` removed in 0.26) | [Headscale docs](https://headscale.net/stable/), `headscale.swagger.json` at v0.29.4 |
| Sunshine | base port 47989: TCP −5, 0, +21; UDP +9, +10, +11; web UI +1 (never exposed); `GET /serverinfo` on the HTTP port answers without pairing | [Sunshine configuration](https://docs.lizardbyte.dev/projects/sunshine/latest/md_docs_2configuration.html) |

Neither ZeroTier API reports whether a member is online. `CentralMember.recently_seen()` uses the last-seen time (five minutes) and the UI words it as “Seen in the last few minutes”, never “Online”.

Sunshine port offsets live only in `integration_contracts.py` (`sunshine_stream_ports()`, `sunshine_web_ui_port()`). Change the base port there, not in callers.

## Connect: which computers are offered

`service.candidate_hosts()` lists the peers reported by **connected** providers only, in this order of preference for the address: the provider-assigned IPv4, then IPv6, then the MagicDNS name. Nothing is scanned. Connect then asks each online peer's Sunshine HTTP port for `/serverinfo` (at most four at a time, two-second timeout) and labels the row “Sharing found”, “Sharing not found” or “Offline”. Local discovery results keep their place at the top; a private peer with the same address is not repeated.

Recent sessions from the history are offered as “Connect again”. Manual address entry remains available.

## Diagnosis

`service.diagnose()` runs, in order: provider state → peer state from the provider → `tailscale ping` (Tailscale/Headscale only) → Sunshine `/serverinfo`. The result says which step failed (`network_down`, `host_offline`, `sunshine_missing`). The latency label is a **project heuristic**: up to 30 ms “Excellent”, up to 60 ms “Good”, above that “High”. The measured value is always shown next to the label. These thresholds are not a quality guarantee; a relayed path can still be playable and a direct one can suffer from Wi-Fi loss.

## History

`history.py` writes `$XDG_CONFIG_HOME/big-remote-play/history/sessions.json` (0600). A session starts when Moonlight's stream process is running and is dropped if it ends with an error within ten seconds. Fields are an allowlist (host, address, port, provider, requested resolution/FPS/bitrate/codec, start/end). Retention is 30 days, 90 days (default) or always. The host side does not record sessions in this history: Sunshine logs when a session starts and ends but not which client it was, so “computers that connected before” on Share is the Sunshine paired-device list. The live **Connected now** list combines those log markers with the stream's RTSP handshake; see [connection status](connection-status.md).

## Joining a ZeroTier network

`zerotier-cli join` only *asks*: the network's controller then sends a configuration, refuses the computer until its owner authorizes it, or says the network does not exist, seconds or minutes later. `private_network/zerotier_join.py` models this as one `JoinPhase` at a time; `ui/zerotier_join.py` (`ZeroTierJoinPanel`) renders it on the ZeroTier connection page and in the guided setup, so both say the same thing.

1. Validate the code (16 hex digits; spaces and dashes removed) before any command → `INVALID_ID`.
2. Not installed → `NOT_INSTALLED`. Service not active → `STARTING_SERVICE`: the one privileged step, `pkexec systemctl enable --now zerotier-one`, then wait for `zerotier-cli -j info` → `SERVICE_STOPPED` if it cannot start.
3. The user cannot talk to the service (401) → `ASKING_PERMISSION`: the one-time token copy (below), so every later check runs without a password → `NEEDS_PERMISSION` if refused.
4. `zerotier-cli -j join <id>`: on success the service answers with the network object, which gives the first status. A failure is classified by the CLI's protocol line `<HTTP status> <command> <body>` — `0` means the service could not be reached (`SERVICE_STOPPED`), `401/403` permission, `400` a bad id — never by reading prose.
5. Follow `-j listnetworks` every 1.5 s for up to 45 s: `OK` with an address → `CONNECTED`; `OK` without one → `WAITING_ADDRESS`; `REQUESTING_CONFIGURATION` → `WAITING_CONFIGURATION` (`NODE_OFFLINE` when `info` says the node itself is offline); `ACCESS_DENIED` → `WAITING_AUTHORIZATION` (stop following: the owner decides); `NOT_FOUND`, `PORT_ERROR`, `CLIENT_TOO_OLD`, `AUTHENTICATION_REQUIRED` → their own states.
6. While a waiting state is on screen the panel checks again (read-only `check()`, no join) every 5 s for two minutes, then every 15 s; the timer stops when the panel is unmapped or the network is decided. Authorization given later becomes `CONNECTED` without joining again.

A `NOT_FOUND` for a membership this attempt created is left again, so a typo leaves nothing behind. A code whose controller does not exist never gets any answer and stays `REQUESTING_CONFIGURATION`; after the first wait it is shown as *has not answered yet* with **Cancel the request** (`leave`). Being on one ZeroTier network never hides the join form. With several memberships, the provider status reports the most advanced one (connected, then awaiting authorization, then configuring, then errors), so a stale `NOT_FOUND` membership no longer hides one awaiting approval.

Technical details (`JoinSnapshot.technical_lines()`) list provider, network, node, service, daemon, join result, state, addresses, interface and peers (direct/relayed). Peers exclude ZeroTier roots and the networks' controllers, which `listpeers` also reports as `LEAF`. No key or token appears.

The previous join reused an interactive menu script run through `pkexec` with `sudo` calls inside it (already root there; `pkexec` also resets `HOME`, so the user's token file was irrelevant) and fed it `2` and the id on stdin; its successor treated every non-`OK` status as a failure. Both are gone. The failure seen on 2026-09-29 (“0 join connection failed”) was the service stopped by **Disconnect** and the join not starting it.

## ZeroTier without a password

`zerotier-cli` needs the service's `authtoken.secret`, readable only by root. On request (“Allow”), Big Remote Play reads it once with `pkexec /usr/bin/cat` into its own process and writes `~/.zeroTierOneAuthToken` (0600) itself, which `zerotier-cli` uses for non-root users. Root never writes into the home directory, so a planted symlink cannot redirect the write. This grants the desktop user the same control over the local ZeroTier service that the Tailscale operator setting grants over `tailscaled`.
