# Connection status: who is connected and how good the link is

What the connection cards show, where each value comes from and what it does not prove. The labels a person sees are described in the [user guide](user-guide.md#see-how-good-the-connection-is); private-network states are in [private-network architecture](private-network-architecture.md).

## One model, three places

| Place | Card | Who is measured |
|---|---|---|
| **Connect** | **Playing now** | The computer Moonlight is streaming from |
| **Share → Overview** | **Connected now** (one card per device) | Every device streaming from this computer now |
| **Connect your devices → a method → Advanced** | **Connection quality** | The private network itself |

All three use `utils/connection_health.py` for quality, thresholds and the path, and `ui/connection_cards.py` for the words. Connect and Share take their measurements from the existing `PerformanceMonitor` worker (the same data as **Support → monitoring**); no second monitor runs.

## Connected is not paired

Sunshine's API lists **paired** devices only (`/api/clients/list` returns certificates). A paired device can connect at any time; it is not connected because it is paired, because it answers a ping or because Moonlight polls `/serverinfo`. `host/sunshine_sessions.py` combines two pieces of evidence:

1. **Sunshine's log** (read incrementally, only new bytes): `Sunshine version:` starts a run, `CLIENT CONNECTED` / `CLIENT DISCONNECTED` start and end a session, `Terminate handler called` ends the run. These are Sunshine's untranslated markers. The count of active sessions comes only from here.
2. **The RTSP handshake** (`ss -tan`, local port = base port + 21, 48010 by default): a client starting a stream talks to this port just before the session starts, and the closed connections stay listed for about a minute. The most recent handshake peer within 90 seconds is attributed to a new session.

A session without a seen handshake is listed as **Connected device** without an address; an address is never guessed. When one of several sessions ends, Sunshine does not say which: the one without an address, then the oldest, is removed.

Verified on 2026-09-29 with Sunshine 2026.914 and Moonlight Qt 6.1 (isolated lab instance): the session appeared 3 s after the stream started, with the handshake address, and disappeared at `CLIENT DISCONNECTED` although the handshake was still listed.

## Quality

One ICMP echo every 5 seconds per connected address (`ping -n -c 1 -W 1`, C locale; hostnames and option-like text are refused). The last 12 samples (one minute) give:

- **latency**: median of answered samples, so one slow reply does not change the verdict;
- **jitter**: mean difference between consecutive answered samples;
- **loss**: share of unanswered samples.

| Word | Rule |
|---|---|
| **Measuring…** | fewer than 3 answered samples |
| **Excellent** | latency ≤ 30 ms, jitter < 10 ms, loss < 2 % |
| **Good** | latency ≤ 70 ms (or Excellent with mild jitter/loss) |
| **Poor** | latency > 70 ms |
| **Unstable** | jitter ≥ 20 ms or loss ≥ 10 % on an otherwise Excellent/Good link |
| **Not responding** | the last 3 probes unanswered |

The stream adds encoding, decoding and display time on top of the network round trip, so the network budget is small: about 30 ms is not noticeable even in fast games, up to about 70 ms is comfortable for most games, and above that input lag is felt. Jitter and loss show as stutter and artifacts regardless of the average. A device whose firewall drops ICMP shows **Not responding** although it streams; the card states what was measured, never the stream's own frame timing.

## Local network or private network

The path is the kernel's real route (`ip -j route get <address>`), cached for a minute, never the method selected in the settings:

| Route | Shown |
|---|---|
| interface `zt*` | **ZeroTier** |
| interface `tailscale*` | **Tailscale**, or **Headscale** when this computer's client is signed in to a Headscale server |
| loopback, no gateway, or a non-global address | **Local network** |
| a gateway to a global address | **Internet** |

## Connection quality (Connect your devices)

Shown on a method's page under **Advanced** while that method is connected, and measured only while that tab is on screen (every 5 seconds, one worker, cancelled on leaving).

- **In use now**: received and sent bits per second of the `zt*` or `tailscale*` interfaces, from the kernel counters in `/sys/class/net`. This is current traffic, not the connection's capacity; no speed test is ever run.
- **Network: Stable / Unstable**: the quality window above, fed by the provider's own measurement. Tailscale and Headscale: one `tailscale ping --c=1` of an online device (answered by the client on any system, and it says **Direct connection** or **Through a relay server**). ZeroTier: the latencies ZeroTier keeps for its peers, so nothing is sent. ZeroTier lists network controllers as `LEAF` peers too; they are excluded (a controller's node ID is the first 10 hex digits of its network ID), so with no other device online the card says **No other device online to measure** instead of a controller's latency.

## Live latency chart

**Share → Support** shows, while sharing, **Latency in the last 3 minutes**
(*Measured with a ping every 5 seconds while a device plays.*). It holds the
last 60 samples of the same ICMP measurement as the cards above, in memory
only. When there is no line it says why instead of waiting silently:

| Message | When |
|---|---|
| *Nobody is playing now. The line starts when a device plays.* | no device is streaming |
| *Measuring…* | a device plays but no reply arrived yet |
| *No latency to show: the other device does not answer pings. This does not affect the game.* | every connected device stopped answering (common with Windows, phones and TVs whose firewall drops ping) |

The chart draws only real measurements, never a flat line of configured
targets. Its messages are laid out with Pango in the theme's foreground colour
and follow the text size; a drawing error is logged once per failure streak
and the values stay readable as labels. The widget's accessible name is
*Latency in the last 3 minutes*.

## Connection notifications (Share)

Each new session in **Connected now** produces one desktop notification,
whether or not the window is in front: *Living Room TV connected*, with
*Connection: Tailscale* and *IP address: 100.64.0.2* when they are known. The
source is the same evidence as above (Sunshine's `CLIENT CONNECTED` marker and
the RTSP handshake address); a ping, a pairing or Moonlight's `/serverinfo`
polling never notifies.

- **One per session.** `host/connection_notices.py` identifies a session by
  its handshake address and the moment the tracker first saw it, so a device
  that stays connected is announced once, and one that disconnects and
  connects again is a new session and is announced again. Stopping sharing
  forgets every session.
- **Already playing is not new.** Sessions found on the tracker's first read
  of the log (Big Remote Play opened during a stream) are remembered without a
  notification.
- **What is shown.** The title carries the device name from **Connected now**
  (a saved name, the private network's name or the reverse DNS name), cleaned
  of control and bidirectional characters and cut to 64 characters; a
  placeholder (*Connected device*, *Device at …*) is replaced by *A device
  connected*. The body holds only the connection path (**Local network**,
  **Internet**, **Tailscale**, **ZeroTier** or **Headscale**, from the real
  route) and a validated literal IP address; each line is left out when it is
  unknown.
- **Privacy.** Names and addresses go to the notification only: they are not
  logged and not stored. The notification is plain text.

The session list is read every 3 s while sharing, so a notification arrives
within a few seconds of the stream starting. Covered by
`tests/test_connection_notices.py`.

## Connection history (Share)

`host/share_history.py` keeps what **Connected now** showed, so **Share →
Support → Connection history** (`ui/connection_history.py`) can say how this
computer was used in the last days. No extra probe runs: when a device appears
in **Connected now** (Sunshine's `CLIENT CONNECTED`, see
[connected is not paired](#connected-is-not-paired)) a start is written, when
it leaves (`CLIENT DISCONNECTED`) the end is written, both on a worker.

- File: `$XDG_CONFIG_HOME/big-remote-play/history/shared.json` (normally
  `~/.config/big-remote-play/history/shared.json`), owner-only (0600), written
  atomically.
- Fields: id, start, end, device name. No address, no key, no code.
- Retention: 90 days (`RETENTION_DAYS`), at most 1000 sessions
  (`MAX_SESSIONS`); a session shorter than 5 seconds (a connection test) is
  dropped.
- A malformed entry, a start in the future or an end before its start is
  skipped, never repaired by rewriting the file; an unreadable file is left
  untouched and nothing new is written to it.
- Closing Big Remote Play during a session ends that session at that moment.

The card shows:

- **Last 7 days**: one bar per local calendar day (the timezone of this
  computer; a session counts on the day it started), with the number of
  sessions above it and the day's short name below it, in the theme's accent
  and foreground colours (light, dark, Gamer and high contrast). The chart's
  accessible name is *Connections in the last 7 days* and its description
  lists every day in words.
- **Sessions**, **Time played**, **Average session length** and **Last
  connection** (*Today at 20:42*) as text.
- **Clear history**, after the confirmation **Clear the connection history?**
  — *Every saved connection is removed from this computer. Paired devices and
  network settings are not affected.*

With nothing stored it says *No connection history yet. Your recent sessions
will appear here.* instead of an empty chart.

The connecting computer keeps its own history of streams it started
(`private_network/history.py`); see
[private-network architecture](private-network-architecture.md#history).

Covered by `tests/test_connection_history.py`: empty history, one and several
sessions over several days and outside the week, short and open sessions,
malformed/future/old entries, an unreadable file, file permissions and fields,
the chart drawn off-screen in light and dark, the totals, Share recording and
ending a session from **Connected now**, and the live chart's messages.

## Cost

| Work | Interval | Only while |
|---|---|---|
| Sunshine log read + one `ss -tan` | 3 s | sharing |
| One ping per connected device | 5 s | a session is active |
| Route lookup | 60 s per address | a session is active |
| Interface counters + one provider probe | 5 s | a connected method's **Advanced** tab is visible |

Nothing runs on the GTK main thread; timers stop when the page is unmapped or sharing stops.

## Limitations

- Latency is ICMP round trip on the network path, not Moonlight's end-to-end latency or frame pacing.
- With several simultaneous sessions, the device that left is not reported by Sunshine; the list can briefly keep the wrong name until the next start.
- A client behind the same NAT address as another cannot be told apart by address.
- The history's device name is the one **Connected now** shows: Sunshine does not say which client a session belongs to. Several sessions ending at once may be closed in a different order than they really ended.
