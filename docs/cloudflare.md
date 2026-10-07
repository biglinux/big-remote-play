# Cloudflare and Big Remote Play

What Cloudflare can do for internet play, and — just as important — what it cannot. Read this before putting a domain in front of a game computer or a Headscale server.

Facts about Cloudflare products were checked against Cloudflare's documentation on 2026-09-28. Plans and limits change; the linked pages are authoritative.

For a Headscale server, the [Headscale setup wizard](headscale-setup-wizard.md) walks through DigitalPlat, the Cloudflare nameservers and the **DNS only** record inside Big Remote Play and checks each step.

## Short answer

| Goal | Use Cloudflare for | Do not use |
|---|---|---|
| Play a game over the internet | Nothing is required. Use Tailscale, ZeroTier or Headscale. | Cloudflare Tunnel or the orange-cloud proxy for the stream |
| Give a game computer a name for direct access (advanced) | **DNS only** (gray cloud) A/AAAA record | The HTTP proxy |
| Run your own Headscale server | **DNS only** record for the server name; HTTPS on the server itself | Proxy or Tunnel in front of Headscale |
| Protect a web dashboard you host | Cloudflare Tunnel + Cloudflare Access | Exposing the Sunshine web UI (port 47990) at all |

## Why the game stream cannot go through Cloudflare

Moonlight and Sunshine stream video, audio and control over **UDP** (ports 47998–48000 with the default base port) plus a few TCP ports.

- The **orange-cloud proxy** carries only HTTP and HTTPS on a fixed list of ports (for example 80, 443, 8080, 8443). It does not carry arbitrary TCP or UDP. See [network ports](https://developers.cloudflare.com/fundamentals/reference/network-ports/).
- **Cloudflare Tunnel public hostnames** support HTTP(S) and, with a client-side `cloudflared`, some TCP services such as SSH or RDP. UDP is not a supported public-hostname service. See [Tunnel protocols](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/routing-to-tunnel/protocols/).
- UDP through a Tunnel works only for **private networks** reached from devices enrolled in Cloudflare Zero Trust with the Cloudflare One (WARP) client and the Gateway UDP proxy. That is a VPN-like setup of its own and is not integrated with Big Remote Play. See [private networks](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/private-net/).
- **Spectrum** can proxy arbitrary TCP/UDP only on the Enterprise plan. See [Spectrum protocols per plan](https://developers.cloudflare.com/spectrum/protocols-per-plan/).

In short: a Cloudflare name helps someone *find* an address. It does not create a route to a computer behind NAT or CGNAT, and it does not hide the game computer's address when the record is DNS only.

## DNS only (gray cloud)

A DNS-only record returns your origin address directly, so any protocol works and the address is public. See [proxy status](https://developers.cloudflare.com/dns/proxy-status/).

1. Add your domain to Cloudflare and set the nameservers it gives you at your registrar.
2. Create an `A` record (public IPv4) and/or `AAAA` record (IPv6) for a name such as `game.example.com`.
3. Set **Proxy status** to **DNS only**.
4. Update the record when your public address changes (a dynamic-DNS client can do this; Big Remote Play does not).

This is the “Without a VPN (advanced)” path. It exposes the streaming ports to the internet; follow the [router guide](router.md) and never forward the Sunshine web UI port.

### Free domain names (DigitalPlat)

[DigitalPlat FreeDomain](https://github.com/DigitalPlatDev/FreeDomain) is a non-profit service that delegates free subdomains such as `*.dpdns.org` or `*.us.kg`. You point the name at external nameservers — Cloudflare is the usual choice — and manage records there. It is **not** a hosting or VPS provider: it does not run servers, open ports or issue certificates. Availability, renewal rules and the number of names per account can change; check its dashboard and terms before relying on it.

## Headscale behind Cloudflare

Headscale's documentation states that running it behind the Cloudflare proxy or Cloudflare Tunnel **is not supported and does not work**, because the Tailscale control protocol needs WebSocket-style POST upgrades that Cloudflare does not pass. Use a **DNS-only** record for the Headscale server name and terminate HTTPS on the server itself (Headscale's built-in Let's Encrypt support, or a reverse proxy such as Caddy or nginx configured as the Headscale docs describe). See [running behind a reverse proxy](https://headscale.net/stable/ref/integration/reverse-proxy/).

The embedded DERP relay also needs **3478/UDP** open directly to the server, which Cloudflare cannot proxy either.

## Protecting a web dashboard with Cloudflare Access

Cloudflare Tunnel plus Access is a good fit for a web page you host yourself (for example a monitoring dashboard): the server opens an outbound tunnel, and Access asks for an identity before any request reaches it. See [self-hosted applications](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/).

- Keep a deny-by-default Access policy and allow only named people or groups.
- Validate the Access token at the origin (the Tunnel's “Protect with Access” option, or your own JWT check).
- Do **not** publish the Sunshine web UI this way. It controls pairing and the host; keep it on the local network.

## What Big Remote Play does with Cloudflare

It never changes anything at Cloudflare and never asks for or stores Cloudflare credentials. The guides show the steps and open Cloudflare's dashboard or documentation when you click a link; you create the records yourself. The [Headscale setup wizard](headscale-setup-wizard.md#dns-checks-no-dig) then checks the domain's nameservers and records read-only through DNS over HTTPS (`cloudflare-dns.com`, falling back to `dns.google`), sending only the name being checked, and says when the proxy (orange cloud) is still on.
