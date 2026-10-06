# Connect your devices: cards with a real switch

The first page of **Connect your devices** shows Tailscale, ZeroTier and
Headscale side by side, always all three. Each card is a small panel of its
own provider: a switch that really turns that connection on or off, the state
in words, its network or server, this computer's private address there and how
many devices are online. This page builds on
[one page per connection method](connect-your-devices-redesign.md); the
Headscale setup it leads to is in [Headscale setup wizard](headscale-setup-wizard.md).

## What a card shows

```text
┌──────────────────────────────────────┐
│ Tailscale  Recommended       [ ON  ] │  provider · tag · switch
│ ✓ Connected                          │  state in words
│ Network: family.example              │  tailnet / ZeroTier network / "Server: host"
│ 100.64.0.1  This computer            │  this computer's private address
│ 2 online · 3 devices                 │  counts, this computer included
│ View devices                       → │  the body opens the method's page
└──────────────────────────────────────┘
```

| Line | Tailscale | ZeroTier | Headscale |
|---|---|---|---|
| Network | **Network:** the tailnet name (`CurrentTailnet.Name`, else the MagicDNS suffix) | **Network:** the network chosen on the ZeroTier page (else the client's primary), with *+ 1 more network* when this computer is in others | **Server:** the host of the Headscale server |
| Address | Tailscale IPv4 of this computer | the address that network assigned | Tailscale IPv4 from that server |
| Devices | from `tailscale status` | from ZeroTier Central (API token) for that network only | from `tailscale status` |

Nothing is borrowed from another provider. A tailnet name is never shown on the
Headscale card, a ZeroTier address never on the Tailscale card.

### Counting

The same rule for all three: **total** is every device of that network
*including this computer*; **online** counts this computer too while its
connection works. With Tailscale, two other devices of which one is online,
the card reads *2 online · 3 devices*.

A number that could not be read is never shown as zero:

| Situation | Card |
|---|---|
| ZeroTier without an API token (the local service knows only this computer) | *Devices unavailable*, with a tooltip about the token |
| The API answered with an error | *Could not load devices* |
| The connection is off | no device line |

`private_network/cards.py` (`DeviceCount`) keeps "unknown" as `None`, and
`ui/remote_connection.devices_line` only prints numbers when both are known.

## The switch

`Gtk.Switch` with GTK's *delayed state*. `state-set` returns `True`, so the
handle moves at once while the track (the state) keeps showing the real
connection. When the action ends, `settle()` sets both to what the provider
reports *then*, not to what the command answered.

**ON means the connection is active and usable**, i.e. the client reports
`ConnectionState.CONNECTED`. A running daemon, a signed-in account waiting for
approval or a joined network not yet authorized are not ON. Internally the
states stay distinct (installed, service running, signed in or joined,
connected, see [private-network architecture](private-network-architecture.md));
the card shows one word.

| The person turns it… | Situation | What happens |
|---|---|---|
| ON | set up (signed in, joined, or a saved account) | *Starting…*, `service.start()` (starts the service through PolicyKit if needed, switches the Tailscale app's profile when the other product owns it, `tailscale up`); then *Connected* and ON |
| ON | Tailscale/Headscale while the other one is active | asks **Switch from Tailscale to Headscale?** — *Only one Tailscale-based network can be active on this computer at a time… ZeroTier is not affected.* **Cancel** leaves everything as it was |
| ON | never set up, or signed out | opens the method's set-up step (Headscale: the [wizard](headscale-setup-wizard.md)); the switch stays OFF |
| ON | not installed | opens the method's page with **Install and continue**, which goes on to set-up by itself |
| OFF | Tailscale or Headscale | *Stopping…*, `tailscale down`: accounts are kept |
| OFF | ZeroTier in several networks | asks first: every ZeroTier network on this computer disconnects |
| either | the provider did not reach the wanted state | the switch returns to the real state, the card says **Could not connect** / **Could not disconnect** and its action becomes **See what happened** (opens the page, whose Connection section names the problem) |

The switch is its own control: moving it never opens the page when the method
is already set up. The rest of the card is one flat button that opens
**Connect your devices → that method** (no dialog).

### Tailscale and Headscale share one app

Which product owns the active profile is decided by the control server the
client really uses (`tailscale debug prefs` → `ControlURL`):
`*.tailscale.com` is Tailscale, anything else is Headscale. What Big Remote
Play recorded when it joined is only a fallback, and it is corrected when it
contradicts the client. A profile joined on the Tailscale page with a custom
login server had been recorded as Tailscale, which made a working Headscale
show **Off**.

Both use the same `tailscaled`. It keeps several accounts (profiles), and one
of them is active at a time, so the two cards cannot both be ON. The backend
already reports this. The product that does not own the active profile reads
`DISCONNECTED` with *tailscaled is using …* and, with a saved profile,
`Recovery.RECONNECT`. The card then says *Tailscale is using the Tailscale app
now*, and the switch asks before switching profiles (`tailscale switch`). ZeroTier
is a separate program and stays as it is.

## Loading and refresh

- On open, each card shows *Checking…* with its switch disabled. Nothing
  numeric appears until the provider answered.
- `PrivateNetworkService.card_summaries()` runs on a worker: one
  `overview()` (three `status()` calls in parallel), the Headscale server, the
  saved-profile lookup, and, for ZeroTier with a token, Central's member list
  for the chosen network, cached for 60 s.
- The page refreshes every 10 s while it is on screen and stops when hidden.
  A card whose switch is working is not redrawn by a refresh, so it never
  flickers back.
- A failed read shows *State unknown* instead of stale or zero values.

## Responsive layout

A `Gtk.FlowBox` with homogeneous children: three cards per row when there is
room, two at medium widths, one per row on narrow windows. Every line wraps
(`WORD_CHAR`), so nothing is cut at 360 px, 150 % text or in long translations.
Rendered at 400 and 1100 px.

## Accessibility

- The switch is named *Tailscale connection* (or ZeroTier, Headscale) and has a
  tooltip; the body button is named *Open Tailscale* with the full card text
  as its description.
- States are words. The green check and the colour only repeat them.
- While busy, the card's description is the action in progress (*Starting…*).

## Code

| Module | Role |
|---|---|
| `private_network/cards.py` | `CardSummary`, `DeviceCount`, `summarize()`: pure, one provider at a time |
| `private_network/service.py` | `card_summaries(zerotier_network)`, ZeroTier count cache |
| `ui/remote_connection.py` | `ProviderCard` (switch, lines, action), `RemoteConnectionPage.switch()` |
| `ui/main_window.py` | `zerotier_network_id`: the network the ZeroTier page and card share |

The internal choice of a preferred provider for Connect (`recommended_status`,
`plan_connection`) is unchanged and used by the guided setup and the sidebar
summary. It no longer influences which cards are shown.

## Tests

`tests/test_connect_cards.py`:
- three cards at once with their own network, address and counts, never mixed;
- this computer counted once;
- ZeroTier without a token says *unavailable*, and its counts come from Central for the chosen network;
- the Headscale server line;
- *Checking…* and *State unknown*;
- the switch going OFF → ON and ON → OFF;
- a failed start rolling the switch back with *Could not connect*;
- install and continue, and setup and continue, without faking ON;
- ZeroTier alongside Tailscale and alongside Headscale, without asking;
- the Tailscale/Headscale confirmation, both Cancel and Switch;
- the body opening the page while the switch doesn't;
- *Setup incomplete* leading to the wizard.

`tests/test_headscale_setup.py` covers `summarize()` (counts, unknown counts,
ZeroTier's chosen network and *+ 1 more*, Headscale's server and the shared app,
"configured").

Not covered automatically: real clients changing state under the switch, and
the PolicyKit prompt itself. See [release acceptance](release-testing.md).
