# User guide

Big Remote Play coordinates a game computer, a connecting computer and—when necessary—a private network. It does not replace Sunshine, Moonlight or the selected VPN client; it guides their normal workflows from one interface.

## Before you start

The game runs on the **sharing computer**. The person playing remotely uses the **connecting computer**.

- On the same home network, no VPN is normally required.
- Across the internet, the guided path is to place both computers on the same private network.
- Sunshine is required on the sharing computer.
- Moonlight Qt is required on the connecting computer.
- Pair only devices and people you trust.

## Share a game

1. On Home, choose **Share my game**.
2. In **Overview**, choose a game or the whole desktop.
3. Open **Image and capture** only when the default display, GPU, encoder or host video ceiling needs to change.
4. Open **Preferences → Audio** only when you want explicit audio routing. The default keeps the current system output unchanged.
5. Start sharing and leave the game computer running.
6. When the other computer shows a Moonlight pairing code, enter the four digits on the sharing page and approve the device.

Opening Share does not start the server, install software or change network/firewall settings by itself.

## Connect to a shared game

1. Ask the other person to start sharing.
2. On Home, choose **Access shared game**.
3. Select the game computer from the list and connect.
4. On the first connection, keep the Moonlight pairing window open while the sharing computer approves the code.
5. Use **Image**, **Audio**, **Input** and **Game PC and connection** for client-side preferences.

When discovery cannot find the computer, use **I know the IP address**. The address dialog and search-code dialog are separate because they solve different problems.

## Pairing code versus search code

The **pairing code** is a four-digit Moonlight code that authorizes a device. It appears on the connecting computer and is approved on the sharing computer.

The optional **search code** only helps locate a Big Remote Play computer on a network that permits the discovery protocol. It does not authorize access, open a firewall, bypass a router or connect two different networks.

## Play over the internet

Open **Play over the internet** and choose the service already appropriate for your network:

- **Tailscale** — browser sign-in and saved account profiles.
- **Headscale** — a Tailscale client connected to a control server administered by you or your organization.
- **ZeroTier** — membership in one or more ZeroTier networks.

Join both computers to the same private network, then return to Share or Connect. If discovery does not cross the private network, use the game computer's private address.

A VPN may use a relay and does not guarantee a direct or low-latency route. Connected status only proves the VPN client's state; it does not prove Sunshine reachability or streaming performance.

## Image quality and host limits

The connecting computer requests resolution, frame rate and video bitrate. The sharing computer captures and encodes the stream and may impose a maximum video-bitrate ceiling.

In **Connect → Image**, the **Image preset** selector offers Automatic, Balanced, Save bandwidth, Sharper picture, 4K and Custom. Resolution, FPS and bitrate controls appear under **Custom**. Custom preserves the current values even when they happen to match a named preset.

A host ceiling of **0** adds no Big Remote Play video limit. A positive value caps a higher client request. It limits video, not total network traffic. Audio, input and error correction still consume bandwidth.

Displayed configuration is not a live measurement. Network ping is not end-to-end game-input latency, and configured FPS is not proof that the stream is delivering that frame rate.

## Audio behavior

The default is **Keep the current system output**. In this mode Big Remote Play does not create a managed virtual sink, change the default output or move applications simply because Share is opened, started or closed.

Selecting a named device enables optional routing for the next sharing session. Only routing owned by Big Remote Play is restored. If you change the system output yourself during a session, that newer user choice is retained.

Sunshine may still react to a third-party Moonlight client that requests host mute. That upstream behavior is separate from Big Remote Play's default audio policy.

## Direct public access without a VPN

**Without a VPN (advanced)** provides a separate guide for public-IP/domain access. This path requires more network knowledge and exposes streaming ports directly to the internet.

- A domain is optional; a reachable public IP can be used directly.
- Cloudflare must be **DNS only**. Its HTTP proxy does not carry the game stream.
- DNS does not solve CGNAT, double NAT, blocked ports or firewall rules.
- UPnP is only an attempt to create router mappings and is not proof that remote access works.
- Do not expose the Sunshine administration panel publicly.
- A Headscale control server is still part of a VPN workflow; it is not the direct-domain alternative.

Test public access from a genuinely different network. See [host and network policy](host-network-policy.md) for precedence and safety details.

## Backup, restore and data

Application data is under `$XDG_CONFIG_HOME/big-remote-play` (normally `~/.config/big-remote-play`). App-managed Sunshine configuration is stored below its `sunshine/` directory. Moonlight keeps its own native or Flatpak configuration.

Backups may contain private certificates and other sensitive native configuration. Treat them as secrets. Keyring secrets are not exported. Restore validates paths and replaces files individually; it is not an all-or-nothing transaction across the whole archive.

Do not run the UI with `sudo`. Privileged operations request narrowly scoped authorization when you choose them.

## Requirements

The application requires Python 3.11+, GTK 4.10+, libadwaita 1.7+, PyGObject, Cairo bindings, the system hicolor icon theme and a graphical desktop session.

Optional/role-specific components include Sunshine, Moonlight Qt, Avahi, `pactl`, VTE, a Secret Service-compatible keyring and one supported private-network client. Install native components through the distribution package manager rather than into the system interpreter with pip.

## When something fails

Start with [troubleshooting](troubleshooting.md). Include the exact application/component versions, distribution, desktop session, role, network layout and sanitized logs in a bug report. Never publish credentials, private certificates, authentication keys or unredacted backup archives.
