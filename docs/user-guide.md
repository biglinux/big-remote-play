# User guide

Big Remote Play coordinates a game computer, a connecting computer and—when necessary—a private network. It does not replace Sunshine, Moonlight or the selected VPN client; it guides their normal workflows from one interface.

## Before you start

The game runs on the **sharing computer**. The person playing remotely uses the **connecting computer**.

- Not sure? **Start with the guided setup** on Home asks two questions, prepares this computer and opens the right page.
- On the same home network, no VPN is normally required.
- Across the internet, the guided path is to place both computers on the same private network.
- Sunshine is required on the sharing computer.
- Moonlight Qt is required on the connecting computer.
- Pair only devices and people you trust.

## Choose the appearance

Open the application menu and use **Appearance**. **Gamer** is the dark
violet/cyan preset; **Automatic** follows the desktop; **Light** and **Dark**
force the corresponding system appearance. The choice takes effect immediately
and is restored the next time Big Remote Play opens. High contrast temporarily
uses the native high-contrast appearance while keeping the saved choice.

## Guided setup

Not sure where to start? On Home, choose **Start with the guided setup**. It asks two questions, gets this computer ready and opens the right page. It installs something only when you press its button.

1. **What do you want to do?** — **Share my game** (this computer runs the game) or **Connect to another computer** (you play on this one).
2. **Where is the other device?** — **On the same network** (same Wi-Fi or router, marked *Simplest*) or **Somewhere else**.
3. **Let's get this computer ready** checks the program the task needs (Sunshine to share, Moonlight to connect). With everything there it continues by itself; otherwise press **Install what's needed** (see [installing what is missing](#installing-what-is-missing)) and the guide continues when it is done.
4. On the same network, Share or Connect opens: no private network is needed. Somewhere else continues with **Let's create a secure connection**, where Big Remote Play first looks at this computer:
   - **We found a connection that is ready** shows the method and network with **Use this connection** (then Share or Connect opens) and **Choose another option**;
   - a connection that needs one step (turned off, service stopped, permission, sign-in) shows **Continue**, which opens that method's page in **Connect your devices**, where its button does that step;
   - with nothing set up, **How do you want to connect?** offers **Tailscale** (*Recommended*: sign in with your account), **I already use ZeroTier** (type the network code you received, see [joining a ZeroTier network](#joining-a-zerotier-network)) and **Advanced options** (your own Headscale server).

The back arrow goes one question back. After a connection is set up from the guide, **Ready to play?** returns to the task you chose.

## Installing what is missing

When a task needs a program this computer does not have yet, Big Remote Play says so and installs it for you — from Home (**Let's get this computer ready**), from the guided setup, and on the Tailscale, Headscale and ZeroTier pages (**Install and continue**). Each program is listed with **Installed** or **Not installed** and what it is for. Press **Install what's needed**: the system asks for your password in its own window, and when everything is installed you see **All set!** and the task continues by itself. Nothing opens a terminal. **Technical details** shows the packages and the package manager's messages for someone helping you. If the installation is cancelled or fails, **Try again** stays on the page. A program installed some other way is recognized the next time you choose the task. Details: [installing what a task needs](dependency-installer.md).

## Share a game

1. On Home, choose **Share** — the option that says this computer runs the game.
2. In **Overview**, under **Source**, choose **Full Desktop**, **Game Window** (only one open game is sent, see below), or a game or app to start.
3. Open **Image and capture** only when the default display, GPU, encoder or host video ceiling needs to change. If displays have the same name, choose **Identify monitors** to show 01, 02 and 03 on the physical screens; the chosen screen then appears in the **Image and capture** summary.
4. Sound works without setup: the other computer hears what this computer plays. **Preferences → Audio** has **Test audio** and the options below.
5. Start sharing and leave the game computer running.
6. The first time a device connects, **New connection request** appears by itself: type the four digits the other computer shows and choose **Approve** (see [approve a new device](#approve-a-new-device)).

Opening Share does not start the server, install software or change network/firewall settings by itself.

### Share only a game window

**Full Desktop** sends everything on the screen, including notifications and other windows. **Game Window** sends one game and nothing else: windows above it, the panel, pop-ups and notifications stay on this computer.

1. Open the game first.
2. Under **Source**, choose **Game Window**. The **Game window** list shows the open games, for example *Shadow of the Tomb Raider — Steam · Proton* or *SuperTuxKart — Linux native*. Select the game.
3. If the game is not listed, select **Refresh** (the circular arrow). The list also follows games opening and closing while it is on screen. For an emulator or a game the list does not recognize, turn on **Show all open windows**.
4. Select **Start sharing**. The first time you share a game, KDE asks you to confirm it: choose the same game, keep **Allow restoring on future sessions** on and select **Share**. Next time it is not asked again.

**Start sharing** stays unavailable until a game is chosen, so nothing else is ever shared instead. When the game switches between a window and fullscreen, or changes its resolution, the picture follows it; a game that opens a new window for that is found again, and Share says **Reconnecting to the game window…** meanwhile (the other device sees a black picture for a few seconds, never your desktop). If the game closes or crashes, or opens more than one window so that Big Remote Play cannot tell which one to show, sharing stops and says why; open the game and start again.

While sharing, keep the game as the active window: keyboard and mouse input from the other device goes to the active window of this computer, and many games stop their sound while another window is active (measured with *Shadow of the Tomb Raider*: its sound stops as soon as it loses focus). Big Remote Play brings the game to the front when sharing starts and every time a device starts playing. Only the game's sound is sent: other programs, notifications and voice calls stay on this computer, which still hears everything. To send all of this computer's sound instead, turn off **Preferences → Audio → Send only the game's sound**. Game Window needs KDE Plasma on Wayland (or an X11 desktop with window effects on) and some GStreamer plugins; if anything is missing, the list says what to install. Details and limits are in [Game Window](game-window.md).

While sharing, **3. Connect the other PC** also tells you, before any device waits:

- **Sunshine's password is needed to approve devices** (or **Sunshine rejected the saved password**): **Enter Sunshine password** checks it with Sunshine and keeps it in the system keyring, so a code is approved the moment you type it;
- **The firewall blocks other computers**: this computer's firewall (ufw or firewalld) does not allow Sunshine's ports from the local network, ZeroTier, Tailscale or Headscale. **Allow in firewall** lists the ports and asks for your password;
- **A stream is open on this computer**: new devices can pair only after **End for everyone**.

Pairing requests that a device abandoned (cancelled, cut by the network or a firewall) are cleared automatically, so the device's next attempt is not refused.

### Mouse and keyboard: this computer first

When someone else plays on this computer, they control its mouse and keyboard too. If you also want to use them, turn on **Preferences → Host input priority → Host has priority**:

- as soon as you move this computer's own mouse or press one of its keys, the other device's mouse and keyboard stop;
- **Guest control resumes after** (5 seconds unless you choose another time) without using them here, the other device controls them again; each new use here starts the count again;
- keys and buttons the other person was holding are released first, so nothing stays pressed in the game;
- controllers are never paused.

While sharing, **Connected now** says who has them: **You're controlling this PC** or **Guests can use the mouse and keyboard**. With **Full Desktop** on KDE Plasma, a short message on the screen (which the other person also sees in their stream) says when the mouse and keyboard pause and when they come back. The setting is off by default and is kept for the next time. Details and limits are in [host input priority](host-input-priority.md).

### Approve a new device

When a device that was never paired asks to play, a dialog opens on its own: **New connection request** — *Living room PC wants to connect to this computer. Type the PIN shown on its screen.* The PIN field is ready for typing or pasting; Enter or **Approve** sends it. **Reject** refuses the device; **Not now** keeps the request. While it waits, **Connection request** stays at the top of **Overview** with the time left (*This request expires in 1:42*); after two minutes without an answer it is cancelled. If the window is in the background, a desktop notification tells you. **Type a pairing code yourself**, under **3. Connect the other PC**, is the manual fallback. Nothing is approved without the PIN shown on the other screen. Details: [pairing requests](pairing-ux.md).

Two options in **Image and capture** decide how faithful the picture is:

- **Correct colors of HDR screens** (on by default): if the shared screen uses HDR, it switches to SDR while sharing and back afterwards, so every connected device sees correct colors; a device that asks for HDR gets SDR. Without it, devices without HDR see grey, washed-out colors. The **Image and capture** summary says when the shared screen is in HDR.
- **Screen resolution while sharing** (needs a chosen screen): **Keep this screen's resolution** (default), **Same as the first device that connects**, **1920 × 1080 — best for TVs**, 2560 × 1440 or **1280 × 720 — slow connections**. A screen larger than the other device is scaled down and small text loses detail; with a wide 3440 × 1440 screen, 1920 × 1080 makes text on a TV or car screen much sharper. The screen changes only while sharing (for anyone sitting at it too) and returns afterwards.

On the other device, Moonlight chooses its own resolution, frame rate and bitrate. For a TV on a fast home network, ask for its screen resolution and about 20–30 Mbps; a car screen on 2.4 GHz Wi-Fi works best at 1280 × 720 or 1920 × 1080 with about 8–10 Mbps, where the connection, not the picture, sets the limit.

See [video quality](video-quality.md) for what was measured.

While sharing, **Connected now** lists every device playing on this computer at this moment, each with its quality, round trip in ms and how it is reached (see [how good the connection is](#see-how-good-the-connection-is)). **Paired devices**, below it, are the devices allowed to connect; a paired device is not connected until it starts playing.

## Connect to a shared game

1. Ask the other person to start sharing.
2. On Home, choose **Connect** — the option for playing on this device.
3. Under **Computers**, choose the game computer: every computer found has its own card, two per line on a wide window. Choosing it connects (*Connecting to Living room PC…*); **Stop** cancels.
4. On the first connection, keep the Moonlight pairing window open while the sharing computer approves the code.

Everything else is on the **Advanced options** tab: **Connect again** (recent computers), **Other ways to connect** (**I have a search code**, **I know the IP address**, **Connection history**) and **On this computer** (**Image**, **Audio**, **Input**, **Game PC and connection**). The address dialog and search-code dialog are separate because they solve different problems.

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

## Services in the sidebar

On Home the bottom of the sidebar shows two indicators: **Streaming** (for example *Sunshine · Running* or *Moonlight · Ready*) and **Secure connection** (for example *Tailscale · Connected*). Selecting one opens its task or **Connect your devices**.

On Share, Connect and **Connect your devices** the bottom of the sidebar shows (on Connect your devices, the streaming card is the one of your task), under **Streaming**, Sunshine (Share) or Moonlight (Connect), and under **Secure connection**, Tailscale, ZeroTier and Headscale. Each card always stays in its place and says its state in words: for example **Running**, **Stopped** or **Ready** for streaming, and **Connected**, **Not connected**, **Sign-in needed**, **Waiting for approval** or **Not installed** for a secure connection. A connected method also shows how many other devices are online (Tailscale, Headscale) or the network name (ZeroTier). Select a card to open its controls: Sunshine and Moonlight open their window with **Start** and **Stop**; Tailscale, ZeroTier and Headscale open their page in **Connect your devices**, which then offers **Back to Share** or **Back to Connect** (see [Connect your devices](#connect-your-devices)). Details in [service status cards](service-status-cards.md).

## Pairing code versus search code

The **pairing code** is a four-digit Moonlight code that authorizes a device. It appears on the connecting computer and is approved on the sharing computer.

The optional **search code** only helps locate a Big Remote Play computer on a network that permits the discovery protocol. It does not authorize access, open a firewall, bypass a router or connect two different networks.

## Connect your devices

On Home, Share and Connect work directly when both computers are on the same home network. For computers in different houses, open **Connect your devices** in the sidebar. It shows three large cards, one per way of connecting:

| Card | Tag | For |
|---|---|---|
| **Tailscale** | *Recommended* | the easy way to connect your devices from anywhere, with your account |
| **ZeroTier** | | a ZeroTier network, joined with its 16-character code |
| **Headscale** | *Advanced* | your own Headscale server |

Each card is a small panel of its own method only:

- a **switch** that really turns the connection on or off. On means the connection works, not just that a program runs. While it works the card says **Starting…** or **Stopping…**; if it did not work, the switch goes back and the card says **Could not connect** with **See what happened**. A method that is not installed or not set up yet takes you to **Install and continue** or to its setup instead of pretending to be on;
- the state in words: **Not installed**, **Off**, **Connecting…**, **Connected** or **Needs attention**;
- **Network:** (Tailscale's tailnet, the ZeroTier network — *+ 1 more network* when this computer is in others) or **Server:** (Headscale);
- this computer's private address there (**This computer**);
- how many devices are online, for example **2 online · 3 devices** — this computer is counted too. When the number cannot be read it says **Devices unavailable** (ZeroTier without an API token) or **Could not load devices**, never a made-up zero.

Several methods can be on at the same time; each keeps its own devices. Tailscale and Headscale are the exception: both use the Tailscale app on this computer, so only one of them is on at a time. Turning one on asks **Switch from Tailscale to Headscale?** first; the other account stays saved. ZeroTier works alongside either. Details: [cards and switches](connect-devices-cards-redesign.md).

Select the rest of a card to open that method's page in the same window; **Back** at the top returns. The top of the window switches between **Devices** and **Advanced**.

**Devices**, from top to bottom:

- **Connection**: one sentence and one button — **Start**, **Stop** (asks first), **Set up** (ZeroTier: **Join a network**), **Sign in**, **Allow** or **Try again**. When the program is missing, **Install and continue** installs it (your password may be requested) and goes on to the sign-in or the network code by itself; nothing opens a terminal.
- **Ready** (once connected): *Your devices can now find each other in Big Remote Play.* with **Back to Share** or **Back to Connect** when you came from there, or **Go to Share** and **Go to Connect**.
- **Devices**: the computers of this method only — never mixed with another method's. Each shows its name (*This device* for this computer), **Online**, **Offline** or **Waiting for approval**, its private address with **Copy IP**, and, for an offline device, when it was last seen. **⋮** offers **Play on this computer** (opens Connect and connects), **Check connection**, **Rename** (a name only this computer uses) and **Remove device**, which asks *Remove notebook?* first. **Remove device** appears only when the method's API is set up under **Advanced → API access**; without it, **Manage devices** opens the official website instead (Tailscale, ZeroTier). ZeroTier with several networks shows a **Network** choice; without a ZeroTier API token it lists only this computer and says that the others are on the ZeroTier website.
- **Add device**: how to bring another computer in, step by step. For Tailscale it separates **Share this computer** (a friend gets access to this one computer only — recommended for playing together), **Add a person to my network** and **Add another computer of mine**. For ZeroTier it shows the **Network code** in groups of four (copied without spaces) with a **QR code** and — with an API token — lists new devices with **Approve** while the page is open. For Headscale it shows the server address with a QR code and, with an API key, approves a waiting computer from the sign-in link it shows. Keys never get a QR code.

**Stop** for Tailscale and Headscale disconnects this computer and keeps you signed in, without a password; for ZeroTier it stops ZeroTier's service, which disconnects all its networks (your password may be requested). Headscale is not a separate program: it is the Tailscale app signed in to your server, and its page says when the app is in use by Tailscale.

**Advanced** holds the technical part: **Connection quality** while connected — **In use now** (what this computer sends and receives through the secure connection now, not its maximum speed; no speed test is run), **Network: Stable** or **Unstable** with the measured time, and whether the path is a **Direct connection** or goes **Through a relay server**; this computer's addresses, name, node ID, network name, account and the service details, each with **Copy**; **Manage network** (approvals, **Leave network**, removing members), **Join another network** and **Create a network** (ZeroTier), **Use another account** (Tailscale, Headscale), **API access**, **Accounts and networks**; and **Help** with **Internet, router and firewall**. All of these open as pages of the same window. The Tailscale and Headscale pages list **Tailscale accounts and tailnets** on this computer and say which one is **Active**; **Switch** changes to another. To join a **friend's tailnet** after accepting their invitation, choose **Use another tailnet**: after your password (once), it opens Tailscale's sign-in, where Tailscale shows **Select a tailnet**. Your current tailnet stays in the list.

Below the cards, **Advanced** links to **Internet, router and firewall** and **Without a private network** (a direct connection with a domain and router ports). Technical background: [Connect your devices redesign](connect-your-devices-redesign.md).

When sharing, **Share** shows under **Available over the internet** the **Secure connection** in use, **This computer**'s name and **Status**, with **Copy connection information** to send to the other person; the private addresses are under **Connection details**. On the other computer, **Connect** lists the computers your private network knows about, next to local ones, marked “Sharing found”, “Sharing not found” or “Offline”. **Check connection** (or **Diagnose** after a failed connection) checks the private network, the computer and Sunshine, and — for Tailscale — whether the route is direct (best performance) or relayed (works, may add latency), with the measured latency.

### Setting up Headscale

**Headscale → Set up** is a step-by-step guide. It first asks **Where will your Headscale server run?**

- **Another server** (*Recommended*): a VPS or a computer that stays online. Type its address and choose **Check server**: Big Remote Play checks the name, that the server answers, the secure connection (HTTPS) and Headscale itself, and says in words what is wrong. **Guided server setup** lists the commands to run on that server, with your address already in them.
- **This computer**: Big Remote Play finds your public IP (**Detected automatically**, with **Copy**) and warns when your internet provider may block incoming connections (CGNAT). Then **Use a domain** (*Recommended*) or **Use the detected address**. With no domain yet, **Help me get one** guides you through a free name at DigitalPlat and DNS at Cloudflare, step by step; Big Remote Play checks the nameservers itself. The record to add is shown ready to copy — Type **A**, Name **headscale**, the IP, and Proxy status **DNS only** (the cloud must be gray). **Install Headscale** then installs and starts the server with HTTPS (your password is requested) and checks it. If the internet cannot reach it, the page explains the router step or suggests a VPS.

Then **Add this computer** (with the server's API key, a key from the server owner, or the owner's approval) and **Add another device**. A phone or computer that joins with the Tailscale app (**Use an alternate server**) shows a code starting with `hskey-authreq-`: type or paste it — the whole line, the link or only the code — under **Approve a device**, on that step or on the Headscale page, and choose **Approve**. No terminal is needed. Each step is marked done only after it was checked. If you close the app, the Headscale card says **Setup incomplete** and **Continue setup** goes on where you stopped. Details: [Headscale setup wizard](headscale-setup-wizard.md).

### Joining a ZeroTier network

Type the **Network code** (16 letters and numbers, sent by the owner of the network; spaces and dashes are ignored) and choose **Join network**. If ZeroTier's service is stopped, it is started first (your password may be requested); right after a first installation this is followed by the one-time **Allow**. Being on one ZeroTier network never hides the field: a computer can join several. Nobody has a network yet? **I don't have a code** shows how to create one for free in ZeroTier Central, step by step, or offers Tailscale.

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

On the sharing computer, **Share → Support → Connection history** shows the sessions other devices played here in the **Last 7 days** — one bar per day — with **Sessions**, **Time played**, **Average session length** and **Last connection**. It is kept only on this computer for 90 days and holds no address, password or code; **Clear history** removes it. With nothing yet it says *No connection history yet.* Details: [connection history](connection-history-fix.md). Above it, while sharing, **Latency in the last 3 minutes** is the live ping measurement; a device that does not answer pings plays normally but draws no line.

On the connecting computer, **Connect → Advanced options → Connection history** lists streams that really started from this computer: the computer, the private-network service, start and end time, duration and the requested resolution and frame rate. It is stored only on this computer and never contains passwords, keys or codes. Choose how long to keep it (30 days, 90 days or always) or **Clear history**. The most recent computers also appear under **Connect again**.

## Image quality and host limits

The connecting computer requests resolution, frame rate and video bitrate. The sharing computer captures and encodes the stream and may impose a maximum video-bitrate ceiling.

In **Connect → Image**, the **Image preset** selector offers Automatic, Balanced, Save bandwidth, Sharper picture, 4K and Custom. Resolution, FPS and bitrate controls appear under **Custom**. Custom preserves the current values even when they happen to match a named preset.

A host ceiling of **0** adds no Big Remote Play video limit. A positive value caps a higher client request. It limits video, not total network traffic. Audio, input and error correction still consume bandwidth.

Displayed configuration is not a live measurement. Network ping is not end-to-end game-input latency, and configured FPS is not proof that the stream is delivering that frame rate.

## Audio behavior

The other computer hears the sound this computer plays — games, the desktop and effects such as EasyEffects. **The microphone is not sent.** Voice chat keeps working in its own apps.

**Voice calls are not sent either.** A call app on this computer plays the voices of everyone in the call, including the person connecting, who would otherwise hear their own voice come back. While Discord, Zoom, Teams, Telegram, Fluxer or another call app plays here, the other computer receives every other sound without it, and this computer still hears the call and the game. **Voice calls** in the audio settings names the apps kept out. A call in a web browser cannot be separated from the rest of the browser's sound, so it is still sent. During the call, the other computer hears programs without the effects of EasyEffects or JamesDSP.

- **Server output — Automatic — use the current output** (recommended): shares whatever output you are using. If you switch output during a stream, the stream follows. Nothing in the sound settings changes.
- **Also play sound on this computer** (on by default): keeps the game audible here even when the connecting computer asks for silence on the game PC. Turn it off to make only the other computer hear the game; Sunshine then switches this computer's output to its own silent output during sessions and switches it back afterwards.
- **Server output — a device**: always share that device (headphones, HDMI, Bluetooth). Sunshine makes it the output during the session, so it also plays here. Effect outputs are not listed; use Automatic for them.
- **Sound for the other computer** (and **Sound** under *Sharing now* on Overview) says, from what the sound system and Sunshine report, whether this computer's sound is being sent, or why not. If Sunshine's recording was muted or turned down in a mixer, Big Remote Play turns it back up and says so.
- **Test audio** plays a short tone and says whether it reached the sound that is shared and, while a device is connected, whether Sunshine is recording it. **Technical audio details** shows what is recorded right now, Sunshine's recording level, what Sunshine's log says, the default microphone, the line **Microphone sent to Sunshine: No** and the calls kept out of the stream.

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

Backups may contain private certificates, Moonlight identity and other sensitive native configuration. Treat them as secrets. Keyring passwords, API tokens and authentication keys are not exported, so a restored setup may ask you to sign in again. Restore validates the complete archive before changing anything and rolls back changes if a file cannot be applied. Logs, caches and temporary session data are not included.

Do not run the UI with `sudo`. Privileged operations request narrowly scoped authorization when you choose them.

## Requirements

The application requires Python 3.11+, GTK 4.10+, libadwaita 1.7+, PyGObject, Cairo bindings, the system hicolor icon theme and a graphical desktop session.

Optional/role-specific components include Sunshine, Moonlight Qt, Avahi, `pactl`, VTE, a Secret Service-compatible keyring and one supported private-network client. Install native components through the distribution package manager rather than into the system interpreter with pip.

## When something fails

Start with [troubleshooting](troubleshooting.md). Include the exact application/component versions, distribution, desktop session, role, network layout and sanitized logs in a bug report. Never publish credentials, private certificates, authentication keys or unredacted backup archives.
