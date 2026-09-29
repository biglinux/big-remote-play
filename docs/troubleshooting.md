# Troubleshooting

## Play over the internet says something is wrong

The card on **Play over the internet** names the problem and its fix:

- **Your secure connection is turned off → Turn on.** The account is kept; it only reconnects. The first time, your password may be requested once so your user can control Tailscale; later it needs none.
- **The connection service is stopped → Fix.** Starts the service and keeps it enabled (password requested).
- **This computer is not connected to the internet.** No network interface has an address: connect Wi-Fi or a cable and choose **Check again**.
- **We need to fix a setting → See details.** Opens the network details, where **Technical details** shows what the connection program reported.

## Colors look grey or washed out on the other computer

The shared screen is almost certainly in HDR (KDE: **System Settings → Display** shows HDR on). Sunshine captures HDR pixels and sends them as SDR, without converting them. Keep **Share → Image and capture → Correct colors of HDR screens** on and start sharing again: the screen uses SDR during the session and HDR afterwards. On desktops other than KDE Plasma, turn HDR off on that screen by hand before sharing. Measurements and details: [video quality](video-quality.md).

## Small text is blurry on the other computer

Text is sharp when the stream has the sharing screen's resolution. A wide or high-resolution screen sent to a smaller screen is scaled down (for example 3440×1440 to 1920×1080). Choose the screen in **Share → Image and capture**, then **Screen resolution while sharing → 1920 × 1080 — best for TVs** (or the size of the device), and start sharing again. Raising the bitrate does not bring back detail lost to scaling. Lag and stutter on a car screen or other 2.4 GHz Wi-Fi device come from the connection: **Share → Connected now** shows **Unstable** when replies vary a lot; lower the device's resolution or bitrate in its Moonlight settings.

## Connected now is empty, or a card says “Not responding”

**Connected now** lists only devices Sunshine reports as streaming; paired devices that are not playing are not listed. A device listed as **Connected device** without an address started its stream without a handshake the program could see. **Not responding** means pings got no reply for a while: the stream may still work if that device's firewall blocks ping. See [connection status](connection-status.md).

## No computer found

First start sharing on the game PC. Check that both PCs are reachable on the same home or private network. Search again. Across a VPN, use the game PC's private IP address; broadcast discovery is not universally supported. A search code does not create network connectivity.

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

`0 join connection failed` is what `zerotier-cli join` prints when it cannot reach the local `zerotier-one` service — not an answer from the network. Older versions showed it as “Connection failed” when the service had been stopped (for example by **Network details → Disconnect**, which stops ZeroTier temporarily). The join now starts the service first and says **ZeroTier is not running** only if it cannot. Right after ZeroTier is installed or enabled for the first time, the service runs but this user may not use it yet: the join then asks for the one-time permission (**Allow**) instead of reporting a stopped service, as versions up to 26.09.29 did after waiting 15 s (technical details showed only `Created symlink … zerotier-one.service`). To check by hand: `systemctl is-active zerotier-one`, then `zerotier-cli -j listnetworks`.

Without a network code, choose **I don't have a code**: in the guided setup and on the ZeroTier connection page it explains how the owner creates a free network in ZeroTier Central, where the 16-character code is shown, and how each computer is approved under **Members**.

## ZeroTier shows “Waiting for authorization”

The network is private: its owner must authorize this computer. Send them the **Node ID** under **Your device**. The owner approves it in **Play over the internet → Add a device or invite a player** (with a ZeroTier API token) or on the ZeroTier website (**Approve on the ZeroTier website**). Keep the page open: it checks again by itself and turns into **Connected** without joining again. A network that approves the computer but assigns no address shows **Almost there**; the owner must assign an address or enable automatic IPv4 assignment.

## ZeroTier says “Network code not found” or “The network has not answered yet”

**Not found** comes from the network's controller: the network does not exist (a typo in a real owner's code, or a deleted network). **Has not answered yet** after a minute usually means the code points to no controller at all, so nobody can say “not found”; check the code, or choose **Cancel the request** so the computer stops trying.

## “Could not connect” over ZeroTier, Tailscale or Headscale: the game PC's firewall

The private network can work (both computers listed, pings answered) while the game PC's firewall drops Sunshine's ports; the connecting computer then sees exactly what it would see if sharing were off. Big Remote Play now tells the two apart:

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

## A device says “The computer is currently in a game” or asks to close the game

Moonlight pairs only when Sunshine has no stream open, and Sunshine counts the shared **Desktop** as a game while it is open — also after every device disconnected, until it is closed. While a stream is open, **Share → 3. Connect the other PC** says **A stream is open on this computer** with **End for everyone**, and a PIN sent then offers the same action instead of only **No computer is waiting**. **End for everyone** asks first, closes the stream on every device (Sunshine's own close, as in its web panel) and checks that nothing is open any more; then start pairing again on the new device and enter its new code. The devices already paired reconnect afterwards without a new PIN.

To pair several new devices, pair them one at a time, each while nobody is playing; the devices already paired reconnect afterwards without a new PIN. A device that never lists the computer has not found it on the network (some TV apps keep an empty computer list): add it by the sharing computer's address, shown in **Share**, then pair.

When a device asks to close the game although nothing seems to be open, it asked for a different app than the one running for the other devices. Choose the app that is already running (usually **Desktop**) to join it; closing it ends the other devices' streams. A device that remembers an app the computer no longer offers (for example an old **Steam Big Picture** entry) fails to start it: refresh the app list on that device.

## Pairing does not finish, or Moonlight says “GeForce Experience returned error”

The connecting PC displays Moonlight's four-digit code. Enter it on the game PC under Share, not into the search-code field. Keep the connecting window open while approving.

A pairing that was cut before its code was entered — cancelled, dropped by the network or by a firewall — stays waiting in Sunshine until it restarts. Measured with Sunshine 2026.914: the next attempt of the same Moonlight is then refused at once with “A pairing session with this uniqueid already exists” (Moonlight shows it as *GeForce Experience returned error*), and a code entered on the game PC goes to the dead request. While sharing, Big Remote Play cancels such requests: a device that is really waiting keeps its connection to Sunshine's HTTP port open, one that gave up has none; with several requests from one address, only the newest is kept. It also does this right before sending a code. On the connecting computer, a failed pairing says where the code goes and offers **Try again**; after a refusal like this, wait a few seconds so the game PC has cleared the earlier attempt. The fix works on the game PC: the version installed there must include it.

Approving a device needs Sunshine's user and password. When they are not saved, or Sunshine rejects the saved ones, Share says so as soon as sharing starts, with **Enter Sunshine password**: they are checked with Sunshine before they are kept in the system keyring, so the code is approved the moment you type it instead of while the device waits.

On the connecting computer, a host that Moonlight only contacted — listed, discovered, or whose pairing failed — is recorded without a certificate (`srvcert=@ByteArray()`). Earlier versions took that record for a pairing and started the stream, which Moonlight refused with “Computer … has not been paired. Please open Moonlight to pair before streaming.” Now only a stored certificate for the same host (its Sunshine `uniqueid`) counts, and pairing starts instead.

## Incorrect resolution, frame rate or sound

Set resolution and frame rate on the **connecting** PC under Image. Share's bitrate limit is a ceiling, not the requested video resolution. Its automatic mode leaves the request to the client. Check the selected display on the game PC.

For sound, open **Share → Preferences → Audio** on the game PC:

1. Press **Test audio**. "The tone reached the shared sound" means the game PC side works; check the connecting computer's volume and its **Audio** settings. "System audio unavailable" means no output device was found: connect or enable one.
2. Open **Technical audio details** while connected. **Sunshine records now** should name a source followed by "sound this computer plays". **Microphone sent to Sunshine** must say **No**; if it says otherwise, stop sharing and report it.
3. The game PC went silent when a client connected: that client asked Sunshine to mute the game PC. Keep **Also play sound on this computer** on. If your output is an effects program (EasyEffects, JamesDSP), this computer stays silent for that client; enable **Also play sound on the game PC** on the connecting computer instead.
4. No sound anywhere after sharing stopped unexpectedly: start Big Remote Play again; it puts back the output Sunshine left on its silent output and reconnects an effects program (JamesDSP, EasyEffects) left without an output. Otherwise choose your output in the system sound settings; if the right output is already chosen and there is still no sound, restart the effects program.
5. Steam Remote Play Together has no sound while sharing: the technical details show which source Steam records. Big Remote Play no longer moves application sound, so Steam's own capture is not undone. See [audio architecture](audio-architecture.md#coexisting-with-steam-remote-play-together).

A configured value in the monitor is not a measured frame rate. Network ping is not end-to-end gaming latency. Test wired networking and the actual encoder/decoder before attributing low FPS to the interface.

## Unable to save credentials

Unlock or start the desktop's Secret Service keyring. The app does not intentionally fall back to writing these passwords into ordinary settings. Do not post passwords, tokens or the unredacted keyring error context publicly.

## Wrong colors or missing symbols

Install the native hicolor icon theme and keep the supplied relative symbolic links intact. Do not replace its directory index with a stripped custom index. Test the default Adwaita theme before diagnosing a third-party theme override. Reload the application after replacing resource files.

## Settings are not saved

Check file ownership and permissions. An unreadable or malformed Moonlight/Sunshine file must be repaired or restored deliberately, not overwritten as though empty. Do not launch the app using sudo. If Moonlight is running, its own preferences may be written when it closes; configure between sessions where possible.

## Backup and rollback

Back up before testing a new build. Backups may contain native private keys. Keyring secrets are separate. Restore checks archive paths and sizes and writes to the actual configuration locations, including Flatpak Moonlight, but does not provide a transaction spanning every file. Preserve the archive and check remaining files if restore is interrupted.

If both legacy and current Big Remote Play config directories exist, the migration preserves conflicting old files rather than deleting them. Keep those files until a maintainer or administrator confirms which version is needed.
