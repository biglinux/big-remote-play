# Share → Support → Connection history

What the history on the sharing computer shows, where its numbers come from, and why the previous chart looked broken. How sessions are detected is in [connection status](connection-status.md); the Connect-side history (computers this one streamed from) is in the [user guide](user-guide.md#connection-history).

## Why the old chart stayed empty

The expander called **Connection history** was not a history. It held a live latency plot of the last 60 measurements (about three minutes), kept in memory only:

1. It was fed only by ICMP replies. Windows, phones, TVs and many firewalls do not answer ping, so a device that played perfectly produced no point at all, and the chart said *Waiting for data…* for the whole session.
2. It existed only while sharing and was emptied when Big Remote Play closed: there was never anything from yesterday to show.
3. Drawing errors were swallowed (`except Exception: pass`), so a failure looked like an empty chart.
4. The text was drawn with Cairo's toy font API in fixed colours, ignoring the theme and text size.

Hiding it would not have fixed anything; it is now named for what it is and the real history is a separate card.

## Live latency (Support, while sharing)

The live chart is now **Latency in the last 3 minutes** (*Measured with a ping every 5 seconds while a device plays.*). Instead of an endless *Waiting for data…* it says why there is no line: *Nobody is playing now…*, *Measuring…*, or *No latency to show: the other device does not answer pings. This does not affect the game.* Its messages are laid out with Pango in the theme's foreground colour, and a drawing error is logged once.

## Connection history (kept between sessions)

`host/share_history.py` keeps what **Connected now** showed. **Connected now** comes from Sunshine's own log markers (`CLIENT CONNECTED` / `CLIENT DISCONNECTED`) through the existing monitor, so no extra probe runs: when a device appears a start is written, when it leaves the end is written, both on a worker.

- File: `~/.config/big-remote-play/history/shared.json`, owner-only (0600).
- Fields: id, start, end, device name. No address, no key, no code.
- Kept 90 days, at most 1000 sessions; a session shorter than 5 seconds (a connection test) is dropped.
- A malformed entry, a start in the future or an end before its start is skipped or not trusted; an unreadable file is left untouched and nothing new is written to it.
- Closing Big Remote Play during a session ends it at that moment.

**Support → Connection history** shows:

- **Last 7 days**: one bar per local day with the number of sessions above it and the day's short name below it, in the theme's accent and foreground colours (light, dark, Gamer and high contrast);
- **Sessions**, **Time played**, **Average session length** and **Last connection** (*Today at 20:42*) as text;
- **Clear history**, after a confirmation.

With nothing stored it says *No connection history yet. Your recent sessions will appear here.* instead of an empty chart. The chart's accessible description lists every day in words.

Days are local calendar days (the timezone of this computer); a session is counted on the day it started.

## Tests

`tests/test_connection_history.py`: empty history, one session, several sessions over several days and outside the week, short sessions, open sessions, malformed/future/old entries, an unreadable file, file permissions and fields, the chart drawn off-screen in light and dark with 0, 1 and 25 sessions, the empty state, the totals, Share recording a session from **Connected now** and ending it, and the live chart's explanations.

## Limits

- Sunshine does not say which client a session belongs to; the device name is the one **Connected now** shows (see [connection status](connection-status.md#connected-is-not-paired)).
- Several sessions ending at once may be closed in a different order than they really ended.
