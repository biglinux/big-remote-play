# Troubleshooting

## Host input priority does not pause the other device

- **Host input priority is not working — not allowed to notice…**: the desktop user cannot read input devices and the installed helper is missing or was refused. Install Big Remote Play from its package (it ships the PolicyKit rule that allows it for the person at the computer); a source checkout works only for members of the `input` group.
- **Waiting for Sunshine's mouse and keyboard…**: Sunshine has not created its virtual devices yet, or uses its X11 fallback, which cannot be paused. Stop and start sharing.
- A key the other person kept holding while paused has to be pressed again after the pause. Controllers are never paused, by design.

## Connect your devices says something is wrong

Each card on **Connect your devices** says its state in one word. Select the card to open the method: its **Connection** section names the problem and has the button that fixes it.

- **Off → Start.** The account or network is kept; it only reconnects. If the service was stopped it is started and kept enabled (your password may be requested once).
- **Needs attention → Sign in, Allow or Try again.** *Sign in to finish* opens the browser sign-in; *Big Remote Play needs your permission* asks once to read ZeroTier networks; *… reported a problem* keeps what the program said under **Advanced → Service details**.
- **Not installed → Install and continue.** Installs the program and continues to the sign-in or the network code by itself.
- **Headscale is off although the server works.** Tailscale and Headscale share the Tailscale app on this computer: starting one turns the other off. The page says so before you press **Start**.
- **The full device list could not be read.** The method's API (under **Advanced → API access**) answered with an error; the list shows what this computer knows. Check or replace the credential there.

## A card's switch goes back to Off

The switch shows the real connection: after **Start** it stays on only if the provider reports a working connection. **Could not connect** means it did not: select **See what happened** — the method's page names the problem (sign-in, permission, service) and **Advanced → Service details** keeps what the program said. Turning Headscale on while Tailscale is active (or the other way round) asks first, because both use the same Tailscale app on this computer.

## Headscale setup

- **We could not find your public address** — this computer has no internet connection, or both the STUN and the HTTPS lookups were blocked. Check the connection and **Check again**, or use another server.
- **Your internet provider may block incoming connections** — evidence of CGNAT (a shared address on this computer, or the router reporting a different internet address). A server here would not be reachable from outside; a small VPS is the reliable way.
- **We are still waiting for DNS** — the record was not found yet; changes can take from a minute to a few hours. **Your domain is not pointing to this server yet** shows where it points now; fix the record's address. **Cloudflare's proxy is on** — click the orange cloud so it turns gray (**DNS only**).
- **Your domain still uses other nameservers** — the two Cloudflare nameservers were not saved at the registrar (DigitalPlat) yet, or the change has not reached the resolvers.
- **Your router needs one more step** — the certificate could not be obtained because the internet cannot reach ports 80 and 443 of this computer. Add the two port-forwarding rules shown, then **Check again**.
- **Headscale on this computer is already set up for another address** — nothing was changed; your existing server configuration is kept. See the Headscale page's **Advanced**.
- **The server's secure certificate is not valid** (another server) — HTTPS is not set up for that name on the server; **Guided server setup** shows the Caddy step.
- **This computer could not join** — check **Advanced → Test connection**. If it says the server works but this computer still cannot join, your router may not let this computer reach its own public name (no "hairpin NAT"); other devices still work.

## Share keeps asking to install Sunshine, or an installation does not finish

Big Remote Play looks for the program again every time you choose the task and whenever its window comes back to the front, so a program installed with another tool is recognized without restarting. If the prompt still appears, Sunshine's executable is not on the system path: on BigLinux and Manjaro install the repository package (`sunshine-bin`, which provides `sunshine`), not the AUR source package.

The installation runs through Pamac (or pacman with PolicyKit where Pamac is absent) and asks for your password in the system's own window; no terminal opens. **The installation was cancelled** means the password window was closed: choose **Try again**. **The installation did not finish** usually means no internet connection or another program updating the system: wait for it, then **Try again**. When a component is not in the system's repositories at all (Sunshine on Arch Linux, which is in the AUR), the page says so and installs nothing: install it from the AUR or its project's own package, then **Try again**. **Technical details** shows Pamac's own messages. See [installing what a task needs](dependency-installer.md).

## A device wants to connect, but nothing appears on the sharing computer

The request dialog needs Sunshine's password to read the waiting devices. When Share shows **Sunshine's password is needed to approve devices**, choose **Enter Sunshine password** once. On a first Sunshine without any user, Big Remote Play creates one itself and keeps it in the keyring. With an older Sunshine that does not list waiting devices, use **Type a pairing code yourself** under **3. Connect the other PC**. Requests expire after two minutes: start pairing again on the other device. See [pairing requests](pairing-requests.md).

## Connection history is empty

**Support → Connection history** fills in as devices play here: a session appears when it ends (shorter than five seconds is not kept). The live chart above it, **Latency in the last 3 minutes**, draws only ping replies; Windows, phones and TVs often do not answer ping, and the chart then says so — the game is not affected. See [connection history](connection-status.md#connection-history-share).

## Colors look grey or washed out on the other computer

The shared screen is almost certainly in HDR (KDE: **System Settings → Display** shows HDR on). Sunshine captures HDR pixels and sends them as SDR, without converting them. Keep **Share → Image and capture → Correct colors of HDR screens** on and start sharing again: the screen uses SDR during the session and HDR afterwards. On desktops other than KDE Plasma, turn HDR off on that screen by hand before sharing. Measurements and details: [video quality](video-quality.md).

## Small text is blurry on the other computer

Text is sharp when the stream has the sharing screen's resolution. A wide or high-resolution screen sent to a smaller screen is scaled down (for example 3440×1440 to 1920×1080). Choose the screen in **Share → Image and capture**, then **Screen resolution while sharing → 1920 × 1080 — best for TVs** (or the size of the device), and start sharing again. Raising the bitrate does not bring back detail lost to scaling. Lag and stutter on a car screen or other 2.4 GHz Wi-Fi device come from the connection: **Share → Connected now** shows **Unstable** when replies vary a lot; lower the device's resolution or bitrate in its Moonlight settings.

## Connected now is empty, or a card says “Not responding”

**Connected now** lists only devices Sunshine reports as streaming; paired devices that are not playing are not listed. A device listed as **Connected device** without an address started its stream without a handshake the program could see. **Not responding** means pings got no reply for a while: the stream may still work if that device's firewall blocks ping. See [connection status](connection-status.md).

## No computer found

First start sharing on the game PC. Every computer found gets its own card under **Connect → Computers**; the other ways to connect are on the **Advanced options** tab. Check that both PCs are reachable on the same home or private network. Search again. Across a VPN, use the game PC's private IP address; broadcast discovery is not universally supported. A search code does not create network connectivity.

## The browser sign-in for the private network did not open, or the PC is not really connected

The sign-in page opens in your own browser and is brought to the front; if a
browser window was never raised, check that a default browser is set. Closing
the page without finishing the sign-in leaves the PC authenticated but
disconnected — the program reports "Sign-in was not completed in the browser"
and Tailscale's own status reads `Stopped`, not `Running`. Choose Connect again
and finish the sign-in. The password prompt appears at most once per PC, to
start the `tailscaled` service and to authorize your user to control it;
connecting afterwards needs no password.

## ZeroTier says “Needs attention” and asks for permission

ZeroTier's control key is readable only by the system. Choose **Allow** once: Big Remote Play copies it to a private file in your home folder, as ZeroTier documents for desktop users, and then lists and joins networks without a password. Until then, joining asks for your password every time.

## ZeroTier: joining failed, or showed “0 join connection failed”

`0 join connection failed` is what `zerotier-cli join` prints when it cannot reach the local `zerotier-one` service — not an answer from the network. It happens when the service is stopped, for example by **Stop** on the ZeroTier page. The join starts the service first and says **ZeroTier is not running** only if it cannot. Right after ZeroTier is installed or enabled for the first time, the service runs but this user may not use it yet: the join then asks for the one-time permission (**Allow**). To check by hand: `systemctl is-active zerotier-one`, then `zerotier-cli -j listnetworks`.

Without a network code, choose **I don't have a code**: in the guided setup and on the ZeroTier connection page it explains how the owner creates a free network in ZeroTier Central, where the 16-character code is shown, and how each computer is approved under **Members**.

## ZeroTier shows “Waiting for authorization”

The network is private: its owner must authorize this computer. Send them the **Node ID** under **Your device**. The owner approves it in **Connect your devices → ZeroTier → Add device** (with a ZeroTier API token) or on the ZeroTier website (**Approve on the ZeroTier website**). Keep the page open: it checks again by itself and turns into **Connected** without joining again. A network that approves the computer but assigns no address shows **Almost there**; the owner must assign an address or enable automatic IPv4 assignment.

## ZeroTier says “Network code not found” or “The network has not answered yet”

**Not found** comes from the network's controller: the network does not exist (a typo in a real owner's code, or a deleted network). **Has not answered yet** after a minute usually means the code points to no controller at all, so nobody can say “not found”; check the code, or choose **Cancel the request** so the computer stops trying.

## Tailscale never shows “Select a tailnet”

Reconnecting (`tailscale up`) reuses the tailnet this computer is already on, so Tailscale has nothing to ask. To use a friend's tailnet, first accept their invitation with the same account, then on the Tailscale page choose **Use another tailnet**. The new sign-in shows **Select a tailnet** when your account belongs to more than one.

**“Access denied: profiles access denied”** (or *checkprefs access denied*) after `tailscale login`: Tailscale keeps its operator — the user allowed to use it without `sudo` — per account, and a sign-in started as a normal user creates a new account entry without one, then loses access to it halfway. The Tailscale page then shows **Permission is needed to see your Tailscale accounts**: choose **Allow** (your password is requested once), then **Switch** back to your account or finish the sign-in. **Use another tailnet** avoids the problem by signing in as administrator once, with you as the new account's operator. To play with a single friend, sharing one computer from their tailnet (**Share this computer**) avoids switching tailnets at all.

## “Could not connect” over ZeroTier, Tailscale or Headscale: the game PC's firewall

The private network can work (both computers listed, pings answered) while the game PC's firewall drops Sunshine's ports; the connecting computer then sees exactly what it would see if sharing were off. Big Remote Play tells the two apart:

- **On the game PC**, while sharing, **Share → 3. Connect the other PC** says **The firewall blocks other computers**, naming the firewall (ufw or firewalld), the blocked ports and the networks affected (the local network, ZeroTier, Tailscale or Headscale). **Allow in firewall** first lists the ports it will open — TCP 47984, 47989, 48010 and UDP 47998–48000 for the default base port; never the administration page — then asks for your password. The check only reads: ufw's own rule files (`/etc/ufw/ufw.conf`, `/etc/default/ufw`, `/etc/ufw/user.rules`) or firewalld's zone queries. The first ufw rule that matches decides, as in ufw; a rule limited to the local network does not cover ZeroTier; an interface rule such as `allow in on tailscale0` covers only that interface. Plain nftables/iptables rules need root to read, so they are never reported as blocking.
- **On the connecting computer**, a refused connection means the game PC answered but sharing is not running; silence from a game PC that still answers a ping means its firewall filters the ports, and the message and **Diagnose** say so.

## A computer shows “Sign-in expired” or “Sharing not found”

“Sign-in expired” means that device's Tailscale key expired: sign in again on that device. “Sharing not found” means the private network reaches the computer but Sunshine did not answer: start sharing on it, and check its firewall allows the streaming ports on the VPN interface. Use **Diagnose** for details.

## An API credential is refused

**API access → Test connection** explains whether the credential was refused (wrong, expired or revoked), lacks permission, or the service was unreachable. Replace it with a new one. Tailscale OAuth clients cannot share a single computer or invite people; use a Tailscale API access token for those.

## “No computer is waiting to pair” or “The stream did not start”

The first appears when you enter a code on the game PC but no Moonlight is waiting: choose the game PC under Connect on the other computer first, then enter the code it shows. If several computers are waiting, Share asks which one shows your code. “The stream did not start” means the game PC answered but no video arrived — usually because this computer was removed from its paired devices; choose **Pair again**.

## Sharing never becomes ready on a PC with several GPUs or monitors

Sunshine probes every display and encoder when capture and encoder are set to automatic. On some multi-GPU, multi-monitor machines that probe can stall for minutes. In **Share → Image and capture**, choose the screen, the capture method and the encoder explicitly.

## Game Window: the game is not listed, or sharing stops

- **No game windows found**: open the game first and select **Refresh**. Minimized windows are not listed (they have no picture); restore the game. For emulators and programs the list does not recognize, turn on **Show all open windows**.
- **Game Window is not available here**: the line below names what is missing. Game Window needs KDE Plasma on Wayland or an X11 desktop with window effects (compositing) on, `kwin_wayland` and the GStreamer plugins `gst-plugin-pipewire`, `gst-plugins-bad` and `gst-plugins-good`. It is not available in the Flatpak.
- **The other device gets sound but a black picture** (Moonlight: *No video traffic was ever received from the host*): the sharing computer needs Sunshine v2026.516 or newer for Game Window. Update Sunshine; Big Remote Play also reports an older Sunshine in the Game window list.
- **The game window was not confirmed**: KDE's **Share screen with** dialog was cancelled or closed. Start again and choose the same game.
- **The window chosen in the system dialog is not the selected game**: a different window was chosen in KDE's dialog; start again and choose the game selected in Big Remote Play.
- **Reconnecting to the game window…** (the other device sees black for a few seconds): the game opened a new window, usually when switching to fullscreen or another resolution. It continues by itself when that window appears.
- **The game window closed**: the game exited or crashed, or no window of the same game appeared within 15 seconds. Nothing else was shown; open the game and share again.
- **The game opened more than one window**: Big Remote Play could not tell which one to show (a launcher and the game, or two windows of the same program). Close the extra window and share again.
- On X11, **Window effects (compositing) are off**: many games suspend compositing in fullscreen. In **System Settings → Display and Monitor → Compositor**, turn off *Allow applications to block compositing*, or play in borderless windowed mode.
- Keys from the other device reach another program: Sunshine sends input to the active window. Click the game once on this computer.

See [Game Window](game-window.md) for what it guarantees and its limits.

## A device says “The computer is currently in a game” or asks to close the game

Moonlight pairs only when Sunshine has no stream open, and Sunshine counts the shared **Desktop** as a game while it is open — also after every device disconnected, until it is closed. While a stream is open, **Share → 3. Connect the other PC** says **A stream is open on this computer** with **End for everyone**, and a PIN sent then offers the same action instead of only **No computer is waiting**. **End for everyone** asks first, closes the stream on every device (Sunshine's own close, as in its web panel) and checks that nothing is open any more; then start pairing again on the new device and enter its new code. The devices already paired reconnect afterwards without a new PIN.

To pair several new devices, pair them one at a time, each while nobody is playing; the devices already paired reconnect afterwards without a new PIN. A device that never lists the computer has not found it on the network (some TV apps keep an empty computer list): add it by the sharing computer's address, shown in **Share**, then pair.

When a device asks to close the game although nothing seems to be open, it asked for a different app than the one running for the other devices. Choose the app that is already running (usually **Desktop**) to join it; closing it ends the other devices' streams. A device that remembers an app the computer no longer offers (for example an old **Steam Big Picture** entry) fails to start it: refresh the app list on that device.

## Pairing does not finish, or Moonlight says “GeForce Experience returned error”

The connecting PC displays Moonlight's four-digit code. Enter it on the game PC under Share, not into the search-code field. Keep the connecting window open while approving.

A pairing that was cut before its code was entered — cancelled, dropped by the network or by a firewall — stays waiting in Sunshine until it restarts. Measured with Sunshine 2026.914: the next attempt of the same Moonlight is then refused at once with “A pairing session with this uniqueid already exists” (Moonlight shows it as *GeForce Experience returned error*), and a code entered on the game PC goes to the dead request. While sharing, Big Remote Play cancels such requests: a device that is really waiting keeps its connection to Sunshine's HTTP port open, one that gave up has none; with several requests from one address, only the newest is kept. It also does this right before sending a code. On the connecting computer, a failed pairing says where the code goes and offers **Try again**; after a refusal like this, wait a few seconds so the game PC has cleared the earlier attempt. This clean-up runs on the game PC, so it needs Big Remote Play sharing there.

Approving a device needs Sunshine's user and password. When they are not saved, or Sunshine rejects the saved ones, Share says so as soon as sharing starts, with **Enter Sunshine password**: they are checked with Sunshine before they are kept in the system keyring, so the code is approved the moment you type it instead of while the device waits.

On the connecting computer, a host that Moonlight only contacted — listed, discovered, or whose pairing failed — is recorded without a certificate (`srvcert=@ByteArray()`). That record is not a pairing: only a stored certificate for the same host (its Sunshine `uniqueid`) counts, so Connect starts pairing instead of a stream that Moonlight would refuse with “Computer … has not been paired. Please open Moonlight to pair before streaming.”

## Incorrect resolution, frame rate or sound

Set resolution and frame rate on the **connecting** PC under Image. Share's bitrate limit is a ceiling, not the requested video resolution. Its automatic mode leaves the request to the client. Check the selected display on the game PC.

For sound, look at **Sound** under *Sharing now* on the game PC (Share → Overview), then open **Share → Preferences → Audio**:

1. **Sound** says *Sending the sound this computer plays* while a device is connected: the game PC side works; look at the connecting computer. *Not sent: Sunshine's recording is muted…* means its recording was muted in a mixer three times during this session; press **Test** to turn it back on. *Not sent: Sunshine could not open this computer's sound* means Sunshine's log reports that the sound could not be opened; stop sharing and start again. Big Remote Play turns a recording that was saved as muted or turned down back up by itself and says so.
2. Press **Test audio**. "The tone reached the shared sound" means the game PC side works; check the connecting computer's volume and its **Audio** settings. "System audio unavailable" means no output device was found: connect or enable one.
3. Open **Technical audio details** while connected. **Sunshine records now** should name a source followed by "sound this computer plays", and **Sunshine recording level** should say 100 %, not muted. **Microphone sent to Sunshine** must say **No**; if it says otherwise, stop sharing and report it.
4. The game PC went silent when a client connected: that client asked Sunshine to mute the game PC. Keep **Also play sound on this computer** on. If your output is an effects program (EasyEffects, JamesDSP), this computer stays silent for that client; enable **Also play sound on the game PC** on the connecting computer instead.
5. No sound anywhere after sharing stopped unexpectedly: start Big Remote Play again; it puts back the output Sunshine left on its silent output and reconnects an effects program (JamesDSP, EasyEffects) left without an output. Otherwise choose your output in the system sound settings; if the right output is already chosen and there is still no sound, restart the effects program.
6. Steam Remote Play Together has no sound while sharing: the technical details show which source Steam records. Big Remote Play does not move application sound, so Steam's own capture is not undone. See [audio architecture](audio-architecture.md#coexisting-with-steam-remote-play-together).

7. The person connecting hears their own voice during a voice call with this computer: the call app here plays their voice, and Sunshine sends what this computer plays. **Voice calls** under Audio names the call apps kept out of the stream; if yours is not named, the call runs in a web browser (its sound cannot be separated) or in an app Big Remote Play does not recognize. Use the call app's desktop version, or headphones plugged into another output than the shared one.
8. Game Window, the game plays here but the other device hears nothing: **Game sound sent** in the technical details names the game's streams. If it says the game is not playing sound while it is, the sound comes from a program outside the game's launch; turn off **Send only the game's sound** to send everything this computer plays.
9. On the connecting computer, Connect tells after the stream starts when Moonlight's sound was muted there (it is turned back on), when Moonlight could not open the sound device (check the output, connect again), when Moonlight is not playing sound at all, and when sound from the game PC is being lost on the way (use a wired or less busy connection). Details: [audio architecture](audio-architecture.md#on-the-connecting-computer).

**Check by hand** on the game PC while a device plays, when the steps above do not explain a silent stream:

```bash
LC_ALL=C pactl list sink-inputs        # the game's streams, their output, mute and volume
LC_ALL=C pactl list source-outputs     # sunshine-record: which source it records, Mute, Volume
pw-link -l                              # the real path game → effects → output → monitor
grep -n "application.name:sunshine" ~/.local/state/wireplumber/stream-properties
grep -n "New streaming session\|Found default monitor\|Opus initialized\|Unable to initialize audio" \
  ~/.config/big-remote-play/sunshine/sunshine.log
# The same monitor at the same moment, as "sunshine" (a saved mute applies) and as a control
parec --raw --format=s16le --rate=48000 --channels=2 -d <monitor> --client-name=sunshine --stream-name=probe > a.raw
parec --raw --format=s16le --rate=48000 --channels=2 -d <monitor> --client-name=control  --stream-name=probe > b.raw
```

Never give a probe the stream name `sunshine-record`: Big Remote Play would take it for Sunshine's own recording. If `a.raw` is silent while `b.raw` carries the game, a saved level is muting Sunshine's recording ([audio architecture](audio-architecture.md#sunshines-recording-level)); press **Test audio** so Big Remote Play restores it.

A configured value in the monitor is not a measured frame rate. Network ping is not end-to-end gaming latency. Test wired networking and the actual encoder/decoder before attributing low FPS to the interface.

## The other computer's controller does nothing in the game

Sunshine gives each controller of the connecting computer a virtual controller on the game PC. While someone is connected, **Share → 3. Connect the other PC** reads Sunshine's log for this connection and says what happened:

- **The other computer's controller is here**: Sunshine created it, and names it. If the game still ignores it, see the next item or check that the game window is in front: many games read a controller only while their window is active.
- **No controller has arrived from the other computer** (20 seconds after the connection began): the other computer sent none. Connect the controller there before starting, and keep Moonlight's window in front; Moonlight sends only controllers it recognises.
- **Sunshine could not create the controller**: restart the game PC once after installing or updating Sunshine, then connect again.
- **Controllers are turned off**: turn on **Enable Gamepad Input** in **Share → Support → Advanced server settings**.

It also says when something on the game PC keeps the controller out of the game:

- **This computer's controller comes first**: a controller is plugged into the game PC. A game that uses one controller reads that one instead of the virtual one, which is always found after it. Unplug the game PC's controller while the other person plays, or choose the “Sunshine” controller in the game's settings. Games made for several players give the virtual controller the next player.
- **Controllers of the other computer cannot work**: this user cannot open `/dev/uinput` or `/dev/uhid`, so Sunshine cannot create the virtual controller. Sunshine's package loads `uhid` and opens it to the `input` group at startup; restart the game PC once after installing or updating Sunshine.

If neither appears, check on the connecting computer that Moonlight sees the controller (**Connect → Input → Controllers**) and that the Moonlight window is focused, or turn on **Keep controller input active in the background**.

## Unable to save credentials

Unlock or start the desktop's Secret Service keyring. The app does not intentionally fall back to writing these passwords into ordinary settings. Do not post passwords, tokens or the unredacted keyring error context publicly.

## Wrong colors or missing symbols

Install the native hicolor icon theme and keep the supplied relative symbolic links intact. Do not replace its directory index with a stripped custom index. Test the default Adwaita theme before diagnosing a third-party theme override. Reload the application after replacing resource files.

## Settings are not saved

Check file ownership and permissions. An unreadable or malformed Moonlight/Sunshine file must be repaired or restored deliberately, not overwritten as though empty. Do not launch the app using sudo. If Moonlight is running, its own preferences may be written when it closes; configure between sessions where possible.

## Backup and rollback

Back up before testing a new build. Backups may contain native private keys and Moonlight identity; keyring secrets are separate. Restore checks the versioned inventory, paths, types, sizes and hashes before writing to the actual configuration locations, including Flatpak Moonlight. If applying any file fails, files already changed are rolled back and the error reports that the previous settings were restored.

If both legacy and current Big Remote Play config directories exist, the migration preserves conflicting old files rather than deleting them. Keep those files until a maintainer or administrator confirms which version is needed.
