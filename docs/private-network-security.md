# Private-network security

How Big Remote Play handles credentials, privileges and personal data for Tailscale, Headscale and ZeroTier. The repository-wide rules are in [AGENTS.md](../AGENTS.md); this document explains how the private-network code meets them and what it deliberately does not do.

## Two kinds of keys

| | Administrative credential | Provisioning key |
|---|---|---|
| Examples | ZeroTier API token, Tailscale API access token, Tailscale OAuth client, Headscale API key | Tailscale auth key, Headscale pre-auth key |
| Purpose | Lets Big Remote Play *manage* a network | Adds *one computer* to a network |
| Stored | Secret Service only, until removed | Never stored; shown once and forgotten |
| UI | Settings → **API access** | **Add device**, or the auth-key field on **Join a network** |

The API access dialog refuses an auth key pasted into an API field (`tskey-auth-…`) and explains the difference, because that is the most common mix-up.

## Where secrets can and cannot be

- **Keyring:** `credentials.py` stores administrative credentials with attributes `provider/kind/id`. The ZeroTier token keeps the attributes used by earlier versions (`zerotier/api_token/default`). Only a masked hint (`tskey-api-••••••••3FxQ`), the kind, the scope and the creation time go to `private_network/credentials.json` (0600).
- **Never in JSON settings, history or logs:** the connection history (`history/sessions.json`) and “Previous networks” (`private_network/history.json`) write an allowlist of non-secret fields. Tests assert that no credential appears in either file.
- **Never in argv:** a Tailscale auth key is written to a 0600 file under `$XDG_RUNTIME_DIR` and passed as `--auth-key=file:<path>`, then deleted. REST credentials travel only in HTTP headers of this process. No shipped shell helper accepts or forwards an API token any more.
- **Never followed through redirects:** `http.py` refuses 3xx responses, so an `Authorization` header is never replayed to another host. Only `https` is accepted, except `http` to a loopback address for a Headscale server on the same computer.
- **Never displayed again:** entry fields are never pre-filled, are cleared after saving, and use `Adw.PasswordEntryRow`. Status rows show only the masked hint.
- **Redacted everywhere text leaves the process:** `redaction.py` removes `tskey-*`, `hskey-*`, `Authorization` headers, credential-like JSON members and `key=value` pairs. The application logger and its handlers install `RedactingFilter`, which formats the message (including `%s` arguments and tracebacks) before redacting it. API error details are additionally stripped of the exact credential that was sent.

## Legacy files from earlier versions

Versions before this change wrote auth keys, Headscale API keys and Cloudflare API tokens in plain text into `private_network/history.json` and `private_network/private_network.json`, with mode 0644. On startup, three seconds after the window appears, `legacy.py`:

1. makes both files owner-only (0600) at once;
2. moves every legacy secret value into the keyring, labelled “Big Remote Play legacy …”, so nothing is lost;
3. removes each value from the JSON file only after the keyring accepted it.

If the keyring is locked or unavailable, only the permissions change and the values stay where they were. The migration is idempotent and logs counts, never values. **Credentials that sat in a world-readable file should be treated as exposed: revoke them at the provider** (Tailscale admin console, ZeroTier Central, `headscale apikeys expire`, Cloudflare API tokens) and create new ones if needed.

## Privileges

Each privileged step is a single, explicit, PolicyKit-authorized command started by a person's click:

| Action | Command | Why |
|---|---|---|
| Start a VPN daemon | `pkexec /usr/bin/systemctl enable --now tailscaled` or `zerotier-one` | Only these two units are accepted |
| Tailscale operator | `pkexec /usr/bin/tailscale set --operator=$USER` | Only after the CLI refused an unprivileged command |
| ZeroTier user access | `pkexec /usr/bin/cat /var/lib/zerotier-one/authtoken.secret` | The token reaches this process through a pipe; the user writes `~/.zeroTierOneAuthToken` (0600) without following links |
| Join/leave ZeroTier before access was granted | `pkexec zerotier-cli join|leave <validated id>` | Only a 16-hex-digit Network ID reaches argv |
| Firewall | `pkexec configure_firewall.sh <base port>` | Confirmed first, listing every port and saying the rule is permanent; the web UI port is never opened. Offered from **Share** when the read-only check (`host/firewall_check.py`: ufw's world-readable rule files, or unprivileged `firewall-cmd` zone queries) finds Sunshine's ports blocked; nothing is changed without this confirmation |
| Install a component | `pamac install --no-confirm <packages>` as the user (Pamac's own PolicyKit action), or without Pamac `pkexec install-components.sh <component ids>` | Only after **Install what's needed**; the helper accepts only known component ids and maps them to packages itself; no terminal, no password field |

The Docker/Caddy/Cloudflare Headscale installer that ran as root, placed the Cloudflare token on `curl`'s command line, opened firewall ports without asking and served the API with `Access-Control-Allow-Origin: *` has been removed. Self-hosting is now a guide with commands the administrator runs on their own server ([VPS and Headscale](vps-headscale.md)).

## Input validation

- ZeroTier Network IDs: exactly 16 hexadecimal digits; Node IDs: 10.
- Server URLs: `https` origin, no user info, query or whitespace.
- Host addresses for probes, history and Moonlight: an IP literal or a DNS name, never beginning with `-`, never containing `/`, `@`, whitespace or control characters.
- Emails for invitations: a conservative pattern, checked before any request.
- API path segments are percent-encoded; `.` and `..` are refused.
- Every dynamic string (network, device, account, host names) is rendered with markup disabled. Toasts disable markup too.

## What is sent where

- **Nothing** is sent to Big Remote Play servers; there are none.
- Status comes from the local VPN clients. REST calls go only to the provider the person configured (`api.tailscale.com`, `api.zerotier.com` or `central.zerotier.com`, their own Headscale server).
- Connect contacts only addresses the VPN clients report, on the Sunshine HTTP port. The connection check page reads local interfaces only; public-address lookups remain the existing opt-in “IPv4/IPv6 Global” rows under Share.

## Reporting

Report a vulnerability privately through the [security policy](https://github.com/biglinux/big-remote-play/security/policy). Never attach real tokens, keys or unredacted history files to an issue.

## Pairing requests

Waiting devices are shown in a dialog and a card with a countdown; nothing is approved without the PIN the person types from the other screen, the PIN goes only to this computer's Sunshine for one `pairing_id`, and a request expires after two minutes and is cancelled. On a first Sunshine without any user, Big Remote Play creates one with a random password kept only in the keyring; an existing user is never replaced. See [pairing requests](pairing-ux.md).

While sharing, and right before a code is sent, Big Remote Play reads Sunshine's waiting pairing requests (`GET /api/pin`) with the credentials from the keyring and cancels the abandoned ones (`DELETE /api/pin` with a validated 32-hex `pairing_id`, the same call as Sunshine's web panel). A request counts as abandoned only when its address has fewer open TCP connections to Sunshine's HTTP port (read with `ss -tan`, no privileges) than it has requests; if the connections cannot be read, nothing is cancelled. Addresses are not logged.
