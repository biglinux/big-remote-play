# User guide

Big Remote Play coordinates a game computer, a connecting computer and—when necessary—a private network. It does not replace Sunshine, Moonlight or the selected VPN client; it guides their normal workflows from one interface.

## Before you start

The game runs on the **sharing computer**. The person playing remotely uses the **connecting computer**.

- Not sure? **Guided setup** on Home asks two questions and opens the right page.
- On the same home network, no VPN is normally required.
- Across the internet, the guided path is to place both computers on the same private network.
- Sunshine is required on the sharing computer.
- Moonlight Qt is required on the connecting computer.
- Pair only devices and people you trust.

## Guided setup

Not sure where to start? On Home, choose **Guided setup**. It asks two questions and opens the right page; it never installs or changes anything by itself.

1. **What do you want to do?** — **Share my game** (this computer runs the game) or **Connect to another computer** (you play on this one).
2. **Where is the other device?** — **On the same network** (same Wi-Fi or router, marked *Simplest*) opens Share or Connect directly: no private network is needed. **Somewhere else** continues with **Let's create a secure connection**.
3. For somewhere else, Big Remote Play first looks at this computer:
   - **We found a connection that is ready** shows the method and network with **Use this connection** (then Share or Connect opens) and **Choose another option**;
   - a connection that needs one step (turned off, service stopped, permission, sign-in) continues on **Play over the internet**, whose button does that step;
   - with nothing set up, **How do you want to connect?** offers **Tailscale** (*Recommended*: sign in with your account), **I already use ZeroTier** (type the network code you received, see [joining a ZeroTier network](#joining-a-zerotier-network)) and **Advanced options** (your own Headscale server).

The back arrow goes one question back. After a connection is set up from the guide, **Ready to play?** returns to the task you chose.

## Share a game

1. On Home, choose **Share** — the option that says this computer runs the game.
2. In **Overview**, choose a game or the whole desktop.
3. Open **Image and capture** only when the default display, GPU, encoder or host video ceiling needs to change. If displays have the same name, choose **Identify monitors** to show 01, 02 and 03 on the physical screens; the chosen screen then appears in the **Image and capture** summary.
4. Sound works without setup: the other computer hears what this computer plays. **Preferences → Audio** has **Test audio** and the options below.
5. Start sharing and leave the game computer running.
6. When the other computer shows a Moonlight pairing code, enter the four digits on the sharing page and approve the device.

Opening Share does not start the server, install software or change network/firewall settings by itself.

Two options in **Image and capture** decide how faithful the picture is:

- **Correct colors of HDR screens** (on by default): if the shared screen uses HDR, it switches to SDR while sharing and back afterwards, so every connected device sees correct colors; a device that asks for HDR gets SDR. Without it, devices without HDR see grey, washed-out colors. The **Image and capture** summary says when the shared screen is in HDR.
- **Screen resolution while sharing** (needs a chosen screen): **Keep this screen's resolution** (default), **Same as the first device that connects**, **1920 × 1080 — best for TVs**, 2560 × 1440 or **1280 × 720 — slow connections**. A screen larger than the other device is scaled down and small text loses detail; with a wide 3440 × 1440 screen, 1920 × 1080 makes text on a TV or car screen much sharper. The screen changes only while sharing (for anyone sitting at it too) and returns afterwards.

On the other device, Moonlight chooses its own resolution, frame rate and bitrate. For a TV on a fast home network, ask for its screen resolution and about 20–30 Mbps; a car screen on 2.4 GHz Wi-Fi works best at 1280 × 720 or 1920 × 1080 with about 8–10 Mbps, where the connection, not the picture, sets the limit.

See [video quality](video-quality.md) for what was measured.

While sharing, **Connected now** lists every device playing on this computer at this moment, each with its quality, round trip in ms and how it is reached (see [how good the connection is](#see-how-good-the-connection-is)). **Paired devices**, below it, are the devices allowed to connect; a paired device is not connected until it starts playing.

## Connect to a shared game

1. Ask the other person to start sharing.
2. On Home, choose **Connect** — the option for playing on this device.
3. Select the game computer from the list and connect.
4. On the first connection, keep the Moonlight pairing window open while the sharing computer approves the code.
5. Use **Image**, **Audio**, **Input** and **Game PC and connection** for client-side preferences.

When discovery cannot find the computer, use **I know the IP address**. The address dialog and search-code dialog are separate because they solve different problems.

While a stream runs, **Playing now** at the top of Connect shows the computer's name, the quality, the round trip in ms and whether it is reached on the local network or through ZeroTier, Tailscale or Headscale.

## See how good the connection is

The connection cards on Connect and Share measure the network round trip with one ping every five seconds and judge the last minute, so one slow reply does not change the verdict:

| Word | Meaning |
|---|---|
| **Excellent** | up to 30 ms, steady — not noticeable even in fast games |
| **Good** | up to 70 ms — comfortable for most games |
| **Poor** | more than 70 ms — input lag is felt |
| **Unstable** | the time varies a lot or replies are lost — expect stutter |
| **Measuring…** | the first measurements are still arriving |
| **Not responding** | the last replies did not arrive (a firewall may also block them) |

The word is always written next to its colour. The path (**Local network**, **ZeroTier**, **Tailscale**, **Headscale** or **Internet**) comes from the route the computer really uses. The **(i)** button shows the address, the variation (jitter) and lost replies. This is the network, not Moonlight's own end-to-end latency; details in [connection status](connection-status.md).

## Pairing code versus search code

The **pairing code** is a four-digit Moonlight code that authorizes a device. It appears on the connecting computer and is approved on the sharing computer.

The optional **search code** only helps locate a Big Remote Play computer on a network that permits the discovery protocol. It does not authorize access, open a firewall, bypass a router or connect two different networks.

## Play over the internet

On Home, Share and Connect work directly when both computers are on the same home network. For computers in different houses, open **Play over the internet**. The page, **Connect your devices**, first looks at what already works on this computer and shows one sentence with one button:

| You see | The button | What it does |
|---|---|---|
| Ready to play over the internet | **Add a device or invite a player** | Shows a link, a code or a QR code for the other device |
| Your secure connection is turned off | **Turn on** | Reconnects the account this computer already uses |
| The connection service is stopped | **Fix** | Starts the background service (your password may be requested) |
| Big Remote Play needs your permission | **Allow** | One-time permission to read ZeroTier networks |
| Sign in to connect this computer | **Sign in** | Opens the sign-in in your browser |
| Waiting for approval | **Check again** | The network owner still has to allow this computer |
| Set up a secure connection | **Set up** | Joins a network (Tailscale sign-in, a ZeroTier network code or a Headscale server) |
| One component is needed | **Install** | Installs the connection program (your password is requested) |
| This computer is not connected to the internet | **Check again** | Connect Wi-Fi or a cable first |

Nothing is installed, turned on or changed until you press the button. **Using Tailscale · Change** says which method is used; **Change** lists the three methods in plain words — Tailscale (easy, for your own devices), ZeroTier (a private network between several devices) and Headscale (your own server) — each with its state.

Below the card:

- **Share this computer** and **Connect to another computer** open Share and Connect.
- **My devices** lists the other devices of your network by name, with **Online**, **Offline** or **Needs to sign in again**. The star keeps a device at the top; **⋮** offers **Connect**, **Details**, **Rename** (a name only this computer uses) and **Check connection**. ZeroTier lists its members only with an API token (Advanced mode → API access).
- **Recent connections** lists the computers you really played on, with **Last connection: Today at 20:42**, **Connect**, a star, **Rename**, **Check connection** and **Remove from recent connections**.

When the connection is ready, a card below the status shows the method and network with **Connected**, **In use now** (what this computer sends and receives through the secure connection now — not its maximum speed; no speed test is run), **Network: Stable** or **Unstable** with the measured time, and whether the path is a **Direct connection** or goes **Through a relay server**. With no other device online it says **No other device online to measure**.

**Add a device** explains, step by step, how to bring another device in. For Tailscale it separates **Share this computer** (a friend gets access to this one computer only — recommended for playing together) from **Add a person to my network**. For ZeroTier it shows the **Network code** in groups of four (copied without spaces) with a **QR code**, and — with an API token — lists new devices with **Approve**. Invitation links and the Headscale server address also have a QR code; keys never do. When you type a network code, spaces and dashes are ignored.

**Advanced mode** (off by default, remembered) adds the connection methods with their states, this network's name and this computer's private address (with **Copy**), **Network details**, **Manage network** (devices, approvals, **Leave network**, **Remove device**, **Revoke access**), **Accounts and networks**, **API access**, **Internet, router and firewall** and **Without a private network**. Pages opened from here have a **Back** button to Connect your devices.

When sharing, **Share** lists under **Available over the internet** the name and private address the other person can use. On the other computer, **Connect** lists the computers your private network knows about, next to local ones, marked “Sharing found”, “Sharing not found” or “Offline”. **Check connection** (or **Diagnose** after a failed connection) checks the private network, the computer and Sunshine, and — for Tailscale — whether the route is direct (best performance) or relayed (works, may add latency), with the measured latency.

### Joining a ZeroTier network

Type the **Network code** (16 letters and numbers, sent by the owner of the network; spaces and dashes are ignored) and choose **Join network**. If ZeroTier's service is stopped, it is started first (your password may be requested). Being on one ZeroTier network never hides the field: a computer can join several.

| You see | What it means | Button |
|---|---|---|
| **Connecting to the network…** | ZeroTier is receiving the network settings | — |
| **Waiting for authorization** | The request was sent; the owner must authorize this computer. **Your device** shows this computer's name and **Node ID** (with Copy) to send to the owner, and **Approve on the ZeroTier website** for the owner. It connects by itself as soon as they do, while the page is open | **Check again**, **Cancel the request** |
| **Almost there** | Authorized, but the network has not given this computer an address; the owner can assign one | **Check again** |
| **Connected** | This computer is on the network | (the guide shows **Continue**) |
| **Network code not found** | The network does not exist; a mistyped code leaves nothing behind | **Try again** |
| **The network has not answered yet** | After a minute without an answer — usually a wrong code whose network does not exist anywhere | **Check again**, **Cancel the request** |
| **ZeroTier is not running** / **needs your permission** | The service could not be started, or the one-time permission was not given | **Start ZeroTier** / **Allow** |
| **ZeroTier cannot reach the internet** | The service runs but reaches no ZeroTier server | **Check again** |

**Technical details** show what ZeroTier answered (network, node, service, state, interface, peers) to share with someone helping you; they never contain a key.

A private network may use a relay and does not guarantee a direct or low-latency route. “Ready” only proves the connection program's state; it does not prove that Sunshine answers or how well a stream will run. For CGNAT, IPv6, UPnP and firewalls see [router, NAT and firewall](router.md); for Cloudflare see [Cloudflare](cloudflare.md); for your own server see [VPS and Headscale](vps-headscale.md).

## Connection history

**Connect → Connection history** lists streams that really started from this computer: the computer, the private-network service, start and end time, duration and the requested resolution and frame rate. It is stored only on this computer and never contains passwords, keys or codes. Choose how long to keep it (30 days, 90 days or always) or **Clear history**. The most recent computers also appear under **Connect again** and on **Play over the internet → Recent connections**.

## Image quality and host limits

The connecting computer requests resolution, frame rate and video bitrate. The sharing computer captures and encodes the stream and may impose a maximum video-bitrate ceiling.

In **Connect → Image**, the **Image preset** selector offers Automatic, Balanced, Save bandwidth, Sharper picture, 4K and Custom. Resolution, FPS and bitrate controls appear under **Custom**. Custom preserves the current values even when they happen to match a named preset.

A host ceiling of **0** adds no Big Remote Play video limit. A positive value caps a higher client request. It limits video, not total network traffic. Audio, input and error correction still consume bandwidth.

Displayed configuration is not a live measurement. Network ping is not end-to-end game-input latency, and configured FPS is not proof that the stream is delivering that frame rate.

## Audio behavior

The other computer hears the sound this computer plays — games, the desktop and effects such as EasyEffects. **The microphone is not sent.** Voice chat keeps working in its own apps.

- **Server output — Automatic — use the current output** (recommended): shares whatever output you are using. If you switch output during a stream, the stream follows. Nothing in the sound settings changes.
- **Also play sound on this computer** (on by default): keeps the game audible here even when the connecting computer asks for silence on the game PC. Turn it off to make only the other computer hear the game; Sunshine then switches this computer's output to its own silent output during sessions and switches it back afterwards.
- **Server output — a device**: always share that device (headphones, HDMI, Bluetooth). Sunshine makes it the output during the session, so it also plays here. Effect outputs are not listed; use Automatic for them.
- **Test audio** plays a short tone and says whether it reached the sound that is shared. **Technical audio details** shows what is recorded right now, the default microphone and the line **Microphone sent to Sunshine: No**.

Changes apply the next time you start sharing. The volume on this computer does not change the volume heard on the other computer.

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
