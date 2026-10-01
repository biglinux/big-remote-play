# Connection status: who is connected and how good the link is

What the connection cards show, where each value comes from and what it does not prove. The labels a person sees are described in the [user guide](user-guide.md#see-how-good-the-connection-is); private-network states are in [private-network architecture](private-network-architecture.md).

## One model, three places

| Place | Card | Who is measured |
|---|---|---|
| **Connect** | **Playing now** | The computer Moonlight is streaming from |
| **Share → Overview** | **Connected now** (one card per device) | Every device streaming from this computer now |
| **Play over the internet** | The connection in use | The private network itself |

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

## The connection in use (Play over the internet)

Shown only when the page's plan is *ready*, and measured only while the page is on screen (every 5 seconds, one worker, cancelled on leaving).

- **In use now**: received and sent bits per second of the `zt*` or `tailscale*` interfaces, from the kernel counters in `/sys/class/net`. This is current traffic, not the connection's capacity; no speed test is ever run.
- **Network: Stable / Unstable**: the quality window above, fed by the provider's own measurement. Tailscale and Headscale: one `tailscale ping --c=1` of an online device (answered by the client on any system, and it says **Direct connection** or **Through a relay server**). ZeroTier: the latencies ZeroTier keeps for its peers, so nothing is sent. ZeroTier lists network controllers as `LEAF` peers too; they are excluded (a controller's node ID is the first 10 hex digits of its network ID), so with no other device online the card says **No other device online to measure** instead of a controller's latency.

## Cost

| Work | Interval | Only while |
|---|---|---|
| Sunshine log read + one `ss -tan` | 3 s | sharing |
| One ping per connected device | 5 s | a session is active |
| Route lookup | 60 s per address | a session is active |
| Interface counters + one provider probe | 5 s | the internet page is visible and ready |

Nothing runs on the GTK main thread; timers stop when the page is unmapped or sharing stops.

## Limitations

- Latency is ICMP round trip on the network path, not Moonlight's end-to-end latency or frame pacing.
- With several simultaneous sessions, the device that left is not reported by Sunshine; the list can briefly keep the wrong name until the next start.
- A client behind the same NAT address as another cannot be told apart by address.
