# Your own Headscale server on a Linux VPS

> In Big Remote Play, **Connect your devices → Headscale → Set up → Another server** checks your server step by step and shows these commands with your address already filled in. This page is the reference behind that [wizard](headscale-setup-wizard.md).

A self-hosted control server for a private Tailscale-compatible network. Use this when you want full control instead of an account at Tailscale. Everything on this page is run **by you, on your server**; Big Remote Play never connects to a server over SSH or changes it.

Checked against Headscale **v0.29.4** (released 2026-09-23) and its [official documentation](https://headscale.net/stable/). Commands and file names can change between versions: when they differ, the official documentation for your version wins.

## What you need

- A Linux server that stays online: a small VPS (1 vCPU, 1 GB RAM) is enough. Debian 12+ or Ubuntu 22.04+ match the official packages.
- A domain or subdomain you control, for example `vpn.example.com`. A free delegated name (see [Cloudflare and DigitalPlat](cloudflare.md#free-domain-names-digitalplat)) works.
- SSH access with a user that can use `sudo`.
- A public IPv4 address (and optionally IPv6) on the server. A VPS normally has one; a home connection behind CGNAT does not.

## 1. DNS

Create an `A` record (and `AAAA` if the server has IPv6) for `vpn.example.com` pointing at the server. If the domain is on Cloudflare, set it to **DNS only**: Headscale does not work behind the Cloudflare proxy or Tunnel.

## 2. Firewall on the server

Allow only what Headscale needs:

| Port | Why |
|---|---|
| 443/TCP | HTTPS control API (clients and Big Remote Play) |
| 80/TCP | Only if Let's Encrypt uses the HTTP-01 challenge |
| 3478/UDP | Only if you enable the embedded DERP relay (STUN) |
| 22/TCP | SSH, ideally limited to your address |

Example with ufw: `sudo ufw allow 443/tcp`, `sudo ufw allow 3478/udp`. Keep 8080 (plain HTTP), 9090 (metrics) and 50443 (gRPC) closed to the internet.

## 3. Install Headscale

The Debian/Ubuntu package is the recommended method; it installs a systemd service, `/etc/headscale/config.yaml` and `/var/lib/headscale`. Download the package for your architecture from the [releases page](https://github.com/juanfont/headscale/releases) and install it:

```bash
sudo apt install ./headscale_0.29.4_linux_amd64.deb
```

Edit `/etc/headscale/config.yaml`:

- `server_url: https://vpn.example.com`
- **HTTPS directly in Headscale:** `listen_addr: 0.0.0.0:443` and `tls_letsencrypt_hostname: vpn.example.com`, **or**
- **behind a reverse proxy:** keep `listen_addr: 127.0.0.1:8080` and configure the proxy as the [reverse-proxy page](https://headscale.net/stable/ref/integration/reverse-proxy/) describes. With Caddy, a site block with `reverse_proxy 127.0.0.1:8080` is enough because Caddy passes the protocol upgrade.

Then:

```bash
sudo systemctl enable --now headscale
sudo systemctl status headscale
curl -fsS https://vpn.example.com/health     # {"status":"pass"}
curl -fsS https://vpn.example.com/version
```

## 4. Users and the API key for Big Remote Play

```bash
sudo headscale users create gaming
sudo headscale users list
sudo headscale apikeys create --expiration 90d
```

In Big Remote Play open **Connect your devices → Headscale → Advanced → API access**, enter `https://vpn.example.com` and paste the API key. **Test connection** should succeed. The API key manages the server; keep it only in the keyring and expire it with `headscale apikeys expire --prefix <prefix>` if it leaks.

## 5. Add computers

On each computer, in Big Remote Play choose **Headscale → Join a network** and enter the server address. Without a key, a sign-in link appears on that computer; approve it:

- in Big Remote Play on a computer that has the API key: **Connect your devices → Headscale → Add device → Approve a waiting computer**, paste the link; or
- on the server: `sudo headscale auth register --auth-id hskey-authreq-… --user gaming`.

Alternatively create a one-time pre-auth key (**Add device → Without a browser**, or `sudo headscale preauthkeys create --user <ID> --expiration 1h`). It is shown once; save it in a file, run `sudo tailscale up --login-server=https://vpn.example.com --auth-key=file:<file>`, then delete the file. Avoid reusable keys.

## 6. Routes (optional)

A computer that advertises a subnet (`tailscale up --advertise-routes=…`) shows “Approve routes” under **Manage network**. On the server, route approval is a `headscale nodes` subcommand (see `headscale nodes --help` for your version). Game streaming does not need subnet routes.

## Backups

Back up, with the service stopped or from a consistent snapshot:

- `/var/lib/headscale/` — the database and `noise_private.key` (losing the key forces every client to sign in again);
- `/etc/headscale/config.yaml` (and ACL policy file if you use one).

Store backups encrypted; they identify every device on the network.

## Updates

Read the [changelog](https://github.com/juanfont/headscale/blob/main/CHANGELOG.md) first: releases sometimes change configuration keys or API fields. Back up, install the new package, then `sudo systemctl restart headscale` and check `/health` and `/version`.

## Logs

```bash
sudo journalctl -u headscale -f
```

Logs can contain device names and addresses; do not paste them publicly without removing them.

## Checklist before telling friends to join

- [ ] `https://vpn.example.com/health` answers from outside
- [ ] The certificate is valid (no browser warning)
- [ ] Only 443 (plus 80 for ACME, 3478/UDP for DERP) is open
- [ ] Big Remote Play's **Test connection** succeeds with the API key
- [ ] A test computer joins and reaches the game computer's private address
