# Host image, audio and internet policy

## Which setting wins?

There is no single "host wins" rule. These settings control different parts of the session.

| Choice | Owner and effect |
|---|---|
| Stream resolution / FPS | Requested by the connecting Moonlight client. Capture source, display/game and codec support constrain the result. This is not automatically a physical monitor mode change on Linux. |
| Requested video bitrate | Set on Connect → Image. UI Mbps are converted to Kbps for Moonlight/Sunshine. It is a target, not a live traffic measurement. |
| Maximum video bitrate | Host ceiling in Share → Image and capture. Zero adds no ceiling; a positive value limits a higher client request. A 40 Mbps request with a 25 Mbps ceiling is capped to 25 Mbps video, not 25 Mbps total network traffic. |
| GPU, screen and capture | Host-local choices. Automatic leaves probing to Sunshine when it starts. The automatic summary describes configured policy, not a measured active encoder. |
| Codec / HDR | The client requests a format the host advertises and the devices can use. Unsupported combinations can fail or require a compatible choice; no universal fallback guarantee is made. |
| V-Sync / decoding | Client-local display/decoding behavior, not the host encoder. |
| Extra FEC | Host packet-loss redundancy. Adds network traffic; not a cure for a slow route. |
| Minimum FPS target | Sunshine's special capture/duplicate-frame setting. Not a replacement for requested stream FPS. |

The consolidated host sheet keeps the video ceiling independently editable in
Automatic mode. Reapplying Automatic preserves that ceiling and the selected
capture screen. Encoding priority, advertised codecs and FEC have a single
mapping used both when saving and when the start worker snapshots settings.

## Audio is opt-in routing

`audio_output_name=""` means keep the current system output. Opening the program,
starting this default mode, stopping it and closing it do not create app-owned
virtual sinks, set a default sink or move playback applications. Old numeric
`audio_output_idx` values do not constitute consent to route audio and are ignored
for selection. A named unavailable device produces a recoverable error instead
of silently substituting the first output.

Explicit routing remembers the previous default, operates only on the app-owned
sinks, and preserves any newer system output selected by the user. Edits made
while sharing take effect on the next start, not as an unsolicited live reroute.
Cleanup without ownership does not guess a device, even after a crash.

This is distinct from Sunshine's own behavior: its audio capture prefers a virtual
sink when the connecting client disables host playback. New Big Remote Play
clients therefore request `hostaudio=true`; saved user choices are preserved.
A third-party client can still request mute. We cannot claim the host wrapper
unconditionally prevents all output changes made by independently configured
Sunshine/Moonlight sessions.

## UPnP is an attempt, not a connection test

Sunshine has a real MiniUPnPc implementation: it discovers an Internet Gateway
Device and requests mappings for streaming/control ports. It can also request
IPv6 pinholes on capable gateways. It is off by default. Enabling it does not
prove mappings succeeded, that the host firewall permits traffic, or that an ISP
permits inbound access. CGNAT and a second upstream NAT need separate handling.

Keep it off for normal LAN/VPN play. For direct public access, inspect mappings
and logs and test from another network. Enabling internet access to the web
administration panel **together with UPnP** can cause Sunshine to map that panel
port too. The UI warns about this combination; the default remains LAN-only.

## Three separate paths, not one mixed tutorial

1. **VPN client setup:** the usual path. Join every computer to a private network;
   use the VPN address when discovery/broadcast does not work across the overlay.
2. **Headscale control server:** still a VPN. A control-server domain and HTTPS
   are not a direct-public-game route. The bundled Docker/Cloudflare deployment
   is custom, not Headscale's recommended supported deployment. Official setup
   requirements remain authoritative.
3. **Direct game access by public IP/domain:** no VPN. A domain only resolves the
   public address. For Cloudflare, delegate the domain as documented and use
   DNS-only A/AAAA records (gray cloud), not the HTTP proxy. It is not Cloudflare
   Tunnel. A public-IP connection works without registering a domain. DigitalPlat
   is an optional free-name provider subject to its availability and terms, not
   a dependency or a promise of perpetual free service. DNS does not bypass NAT.

Direct access publishes the origin address and streaming ports; it increases
public exposure relative to a properly restricted private VPN. Keep Sunshine
updated, approve trusted devices, restrict firewall rules, and do not expose
its web administration panel (47990 with the default base port).

The new guides only render text and open an official page after a user clicks a
link. Merely opening a guide changes no DNS, ports, credentials or services.

## Joining a tailnet: privilege, browser and connected state

Tailscale and Headscale are the same client (`tailscaled`), so both follow the
same rules.

- **`up`, not `login`.** `tailscale login` authenticates the node and leaves it
  stopped; `tailscale up` is what joins the tailnet. A PC can be signed in
  (`LoggedOut=false`) and still be unreachable (`BackendState=Stopped`).
- **Success is the daemon's verdict.** A helper exiting with status 0 is not
  membership. Only `BackendState == "Running"` in `tailscale status --json`
  reports a connection; a shown sign-in URL that was never completed is
  reported as a pending sign-in, not as a failure of this PC.
- **One privileged step, not a privileged session.** The daemon is started with
  `pkexec systemctl enable --now tailscaled` when it is stopped, and the user is
  made the tailscaled operator (`pkexec tailscale set --operator=$USER`) only
  after the client itself refuses an unprivileged command. Connecting,
  disconnecting and switching accounts then run as the logged-in user.
- **The browser belongs to the user's session.** The CLI prints the sign-in URL;
  the application opens it with `Gtk.show_uri()` and the requesting window as
  launcher, so Wayland grants the activation token and the browser is raised. A
  browser launched from a privileged helper never reaches the desktop.
- **Authentication keys never enter argv.** A key is written to a 0600 file
  under `$XDG_RUNTIME_DIR` and passed as `--auth-key=file:<path>`, then removed;
  `/proc/<pid>/cmdline` is world-readable.
- **Switching providers does not stop services.** `tailscale down` leaves the
  tailnet and keeps every saved profile, so the other provider works without
  `logout` (which would expire the node key) and without stopping `tailscaled`.
  ZeroTier is an independent daemon; neither one requires disabling the other.

## Primary references checked for this review

- [Sunshine configuration](https://docs.lizardbyte.dev/projects/sunshine/latest/md_docs_2configuration.html): `max_bitrate`, codecs, capture, audio and `upnp`.
- [Sunshine UPnP implementation](https://github.com/LizardByte/Sunshine/blob/master/src/upnp.cpp), blob `ca20e985658cc9fc869f6cc87f59bba7d5cf77b0`: streaming mappings and conditional web-manager mapping.
- [Sunshine audio capture](https://github.com/LizardByte/Sunshine/blob/master/src/audio.cpp), blob `28a3d810df924f15a8ceea33cca568c9b67e4d9c`: `HOST_AUDIO`, virtual sink selection and restoration.
- [Linux Sunshine audio backend](https://github.com/LizardByte/Sunshine/blob/master/src/platform/linux/audio.cpp), blob `fe246bff509969d4de5b8292cb6b8fd5ed228d29`: default sink lookup and monitor capture.
- [Moonlight setup](https://github.com/moonlight-stream/moonlight-docs/wiki/Setup-Guide).
- [Cloudflare proxy status](https://developers.cloudflare.com/dns/proxy-status/) and [supported proxy ports](https://developers.cloudflare.com/fundamentals/reference/network-ports/).
- [DigitalPlat domain service](https://domain.digitalplat.org/): check current availability, renewal and terms before registration.
- [Headscale README](https://github.com/juanfont/headscale/blob/main/README.md), blob `c18bf2ad5e57f7120f3672d92003ffb02f3418dc`, and [stable documentation](https://headscale.net/stable/).
- [Tailscale CLI](https://tailscale.com/kb/1080/cli) and [`tailscale up`](https://tailscale.com/docs/reference/tailscale-cli/up): `up` versus `login`, `--login-server`, `--auth-key`, `--timeout`.
- [Linux operator permission](https://tailscale.com/docs/reference/troubleshooting/linux/linux-operator-permission): running the CLI as the desktop user instead of root.

Checked on 2026-09-18. The source references describe upstream behavior, not proof
that a particular local hardware/driver/router combination has been tested here.
