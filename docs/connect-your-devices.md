# Connect your devices: one page per connection method

How Tailscale, ZeroTier and Headscale are presented and managed under
**Connect your devices**, and why the structure is what it is. Provider
internals (states, upstream contracts, the shared Tailscale app) are in
[private-network architecture](private-network-architecture.md); the sidebar
cards are in [service status cards](service-status-cards.md). The steps for
people are in the [user guide](user-guide.md#connect-your-devices).

## Structure

```text
Main window
└─ Connect your devices          (Adw.NavigationView, root tag "providers")
   ├─ RemoteConnectionPage        three cards + Advanced links
   ├─ ProviderPage                one method; tabs Devices | Advanced in the window header
   │  ├─ Set up                   ConnectPage (sign-in, network code, server address)
   │  ├─ Add another device       AddDeviceFlow (and its own sub-pages)
   │  ├─ Manage network           ManageNetworkFlow
   │  ├─ API access               ApiAccessDialog shown as a page
   │  ├─ VPN accounts and networks
   │  └─ guides                   Internet, router and firewall · Host a Headscale VPN server
   └─ (from the cards' Advanced)  Internet, router and firewall · Direct connection with a domain
```

Everything is a page of one `Adw.NavigationView` inside the main window. The
window header shows the page title and its own **Back** button
(`network_back_button`). On a method's page it also shows the **Devices |
Advanced** switcher. Dialogs are kept only for short things: **Stop?**,
**Remove X?**, **Create a ZeroTier network** (a name), leaving a network,
renaming an account, a QR code, and errors as toasts.

The same tools open as sheets when they are reached from outside Connect your
devices, for example the direct-connection guide from **Share →
Advanced**. `MainWindow._network_pages()` decides which: while Connect your
devices is on screen they become pages.

| Module | Role |
|---|---|
| `private_network/device_list.py` | Pure: `ProviderDevice`, `DeviceListing`, `tailnet_listing`, `zerotier_listing`, `simple_state` |
| `private_network/service.py` | `device_listing(provider, network_id)`, `remove_device(provider, id, network_id)`, `start`/`stop`, Tailscale profile switching for Headscale |
| `ui/remote_connection.py` | `RemoteConnectionPage` and `ProviderCard` |
| `ui/provider_page.py` | `ProviderPage`: Devices and Advanced of one method |
| `ui/network_devices.py` | `AddDeviceFlow`, `ManageNetworkFlow` as pushed pages (`_Flow`) |
| `ui/network_common.py` | `push_page`/`in_stack`: a pushed page's work stops when it leaves the stack, even when navigation jumps past it |
| `ui/components.py` | `content_page`/`preferences_page`: the content of a sheet as a page |
| `ui/main_window.py` | `open_provider`, `finish_provider`, header sync, `_network_return_page` |

## Page structure

### The list (Connect your devices)

One sentence of introduction, then three cards side by side (one per line at
narrow widths). Each card is a small panel of its provider, described in
[cards and switches](#cards-and-switches). Network IDs, accounts and keys are
never on a card. Below the cards, **Advanced** links to *Internet, router and
firewall* and *Without a private network*.

### A method's page

Same order for the three methods.

1. **Header**: icon, name and the state word.
2. **Connection**: one sentence, a detail and one button. The button is
   **Start**, **Stop** (after a confirmation), **Set up** (ZeroTier: **Join a
   network**), **Sign in**, **Allow** or **Try again**. When the method is not
   installed, the button gives way to **Install and continue**. After the
   install, the next step (the sign-in, or the join form) opens by itself.
3. **Ready** (only while connected): *Your devices can now find each other in
   Big Remote Play.* with **Back to Share** or **Back to Connect** when the
   person came from there. Otherwise it offers **Go to Share** and **Go to
   Connect**.
4. **Devices**: this method's devices only. ZeroTier adds a **Network**
   choice when the computer is in more than one network. Each row shows:
   - the name, and *This device · Online* for this computer;
   - **Online**, **Offline**, **Waiting for approval** or **Status unknown**;
   - the private address with **Copy IP**;
   - *Last seen 3 hours ago* for an offline device when the provider says when;
   - **⋮** with **Play on this computer** (online devices, opens Connect),
     **Check connection**, **Rename** (a name only this computer uses), then
     **Remove device** when the API can do it, otherwise **Manage devices**
     (the provider's website).
5. **Add device**: *Add another device*, a page with the steps for this method.

**Advanced** holds the technical part. While connected it shows **Connection
quality**, measured only while that tab is visible
([connection status](connection-status.md#connection-quality-connect-your-devices)).
Then *This computer on the network*: addresses, name on the network, node ID,
network name, account, network IDs, the Headscale server and the service
details. **Manage** offers *Manage network*, *Join another network* or *Create
a network* (ZeroTier), *Use another account* (Tailscale, Headscale), *API
access* and *Accounts and networks*. For Tailscale and Headscale it also shows
the saved Tailscale accounts. **Help** has the internet guide, the Headscale
server guide and, for Tailscale and Headscale, the note that they share one
app.

## Provider model

Each method is read and acted on alone. `PrivateNetworkService.overview()`
gives the cards one status per method. `ProviderPage` asks only for
`status(provider)` and `device_listing(provider, network_id)`. Lists are never
merged, so a ZeroTier address is never offered on the Tailscale page, and the
reverse.

| | Tailscale | ZeroTier | Headscale |
|---|---|---|---|
| Program on this computer | `tailscaled` | `zerotier-one` | the same `tailscaled` (Tailscale app) signed in to a Headscale server |
| Can be on with the others | with ZeroTier | with either | with ZeroTier |
| Start | start `tailscaled` if stopped (PolicyKit), then `tailscale up`. If the app belongs to Headscale, switch to the saved Tailscale profile first | `systemctl enable --now zerotier-one` (PolicyKit) | switch the Tailscale app to the saved Headscale profile, then up |
| Stop | `tailscale down`: accounts stay | `systemctl stop zerotier-one` (PolicyKit): every ZeroTier network disconnects, memberships stay | `tailscale down`, only while the app belongs to Headscale |
| Devices without API | every peer the client knows, with online state and address | only this computer, plus *Your other devices are listed on the ZeroTier website* and **Manage devices** | every peer the client knows |
| Devices with API | the same, with *last seen*, and removable | every member of the chosen network, approval state, last seen, removable | the same, with *last seen*, and removable |
| **Remove device** uses | Tailscale API `DELETE /device/{id}` (API token or OAuth client) | ZeroTier Central API token: `DELETE /network/{id}/member/{node}` (legacy Central) or `DELETE /network/{id}/member` with the device (new Central) | Headscale `DELETE /api/v1/node/{id}` (API key) |
| **Manage devices** | `login.tailscale.com/admin/machines` | `my.zerotier.com/network/{id}` | none: the server is the person's own |
| Add device | share this computer, invite a person, or another computer of mine (sign-in or a one-time key) | network code with QR and the steps, then approval here (API) or on the website | server address with QR and the steps; approve the sign-in link here (API) or on the server |

Headscale's **Set up** is a [step-by-step wizard](headscale-setup-wizard.md)
(where the server runs, its address, DNS, HTTPS, this computer). Tailscale and
Headscale share the Tailscale app on this computer
([state model](private-network-architecture.md#state-model)), and a method's
page says so in words: when Tailscale is active, Headscale reads **Off** with
*Starting it turns Tailscale off: both use the Tailscale app on this
computer.*, and the same holds the other way round.

## Capabilities

**Remove device** appears only when both are true:

1. the method's administrative API is configured and answered the listing
   (`DeviceListing.can_remove`), and
2. that device was matched in the API's answer, by node ID or address
   (`ProviderDevice.removable`). A peer the API does not know, for example
   from another tailnet, cannot be removed from here.

Otherwise the menu offers **Manage devices**, which opens the official website
(Tailscale, ZeroTier). Headscale has none. If the API is configured but
fails, the list shows *The full device list could not be read* with the local
list below it, and the details are in **Advanced**.

Removing asks first: **Remove notebook?** — *It will need to be added again
before it can connect.* — **Cancel** / **Remove**. Then a toast reports
*notebook was removed*, or the API's error in plain words.

Credentials live in the Secret Service keyring and are entered on the **API
access** page, never shown again, never logged
([private-network security](private-network-security.md)).

## Cards and switches

The first page shows Tailscale, ZeroTier and Headscale side by side, always
all three. Each card is a small panel of its own provider: a switch that
really turns that connection on or off, the state in words, its network or
server, this computer's private address there and how many devices are online.

### What a card shows

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

The state line is one of the five words of `device_list.simple_state`
(**Not installed**, **Off**, **Connecting…**, **Connected**, **Needs
attention**), or **Setup incomplete** while the Headscale wizard was started
and not finished. The action line at the bottom reads **Install**, **Set up**,
**Start to connect**, **View devices**, **Continue setup** or **See what
happened**. Nothing is borrowed from another provider: a tailnet name is never
shown on the Headscale card, a ZeroTier address never on the Tailscale card.

### Counting

The same rule for all three: **total** is every device of that network
*including this computer*; **online** counts this computer too while its
connection works. With Tailscale, two other devices of which one is online,
the card reads *2 online · 3 devices*.

A number that could not be read is never shown as zero:

| Situation | Card |
|---|---|
| ZeroTier without an API token (the local service knows only this computer) | *Devices unavailable*, with the tooltip *Add a ZeroTier API token in Advanced to count the devices.* |
| The API answered with an error | *Could not load devices* |
| The connection is off | no device line |

`private_network/cards.py` (`DeviceCount`) keeps "unknown" as `None`, and
`ui/remote_connection.devices_line` prints numbers only when both are known.

### The switch

`Gtk.Switch` with GTK's *delayed state*. `state-set` returns `True`, so the
handle moves at once while the track (the state) keeps showing the real
connection. When the action ends, `settle()` sets both to what the provider
reports *then*, not to what the command answered.

**ON means the connection is active and usable**, i.e. the client reports
`ConnectionState.CONNECTED`. A running daemon, a signed-in account waiting for
approval or a joined network not yet authorized are not ON. Internally the
states stay distinct (see
[private-network architecture](private-network-architecture.md#state-model));
the card shows one word.

| The person turns it… | Situation | What happens |
|---|---|---|
| ON | set up (signed in, joined, or a saved account) | *Starting…*, `service.start()` (starts the service through PolicyKit if needed, switches the Tailscale app's profile when the other product owns it, `tailscale up`); then *Connected* and ON |
| ON | Tailscale/Headscale while the other one is active | asks **Switch from Tailscale to Headscale?** (or the reverse) — *Only one Tailscale-based network can be active on this computer at a time. … ZeroTier is not affected.* — **Cancel** leaves everything as it was, **Switch** starts it |
| ON | never set up, or signed out | opens the method's set-up step (Headscale: the [wizard](headscale-setup-wizard.md)); the switch stays OFF |
| ON | ZeroTier needs the one-time permission | opens the method's page, whose **Allow** button asks for it; the switch stays OFF |
| ON | not installed | opens the method's page with **Install and continue**, which goes on to set-up by itself; the switch stays OFF |
| OFF | Tailscale or Headscale | *Stopping…*, `tailscale down`: accounts are kept |
| OFF | ZeroTier in several networks | asks **Turn off ZeroTier?** first: every ZeroTier network on this computer disconnects, memberships are kept |
| either | the provider did not reach the wanted state | the switch returns to the real state, the card says **Could not connect** / **Could not disconnect** and its action becomes **See what happened** (opens the page, whose Connection section names the problem) |

The switch is its own control: moving it never opens the page when the method
is already set up. The rest of the card is one flat button that opens
**Connect your devices → that method** (no dialog). While Tailscale owns the
shared app, the Headscale card reads *Tailscale is using the Tailscale app
now* (and the reverse).

### Loading and refresh

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

### Responsive layout

A `Gtk.FlowBox` with homogeneous children: three cards per row when there is
room, fewer as the window narrows, one per row on narrow windows. Every line
wraps (`WORD_CHAR`), so nothing is cut at 360 px, 150 % text or in long
translations.

### Accessibility

- The switch is named *Tailscale connection* (or ZeroTier, Headscale) and has
  the tooltip *Turn Tailscale on or off*; the body button is named *Open
  Tailscale* with the full card text as its description.
- The flow box is named *Connection methods*; the switch and the body are the
  only tab stops of a card.
- States are words. The green check and the colour only repeat them.
- While busy, the card's description is the action in progress (*Starting…*).

### Code

| Module | Role |
|---|---|
| `private_network/cards.py` | `CardSummary`, `DeviceCount`, `summarize()`: pure, one provider at a time |
| `private_network/service.py` | `card_summaries(zerotier_network)`, ZeroTier count cache |
| `ui/remote_connection.py` | `ProviderCard` (switch, lines, action), `RemoteConnectionPage.switch()` |
| `ui/main_window.py` | `MainWindow.zerotier_network_id`: the network the ZeroTier page and card share |

The preferred provider for the guided setup and the sidebar summary
(`recommended_status`, `plan_connection`) does not influence which cards are
shown.

Covered by `tests/test_connect_cards.py` (three cards with their own network,
address and counts; the switch both ways; a failed start rolling back; install
and set-up without faking ON; the Tailscale/Headscale confirmation, Cancel and
Switch; *Setup incomplete* leading to the wizard) and
`tests/test_headscale_setup.py` (`summarize()`: counts, unknown counts,
ZeroTier's chosen network and *+ 1 more*, Headscale's server and the shared
app). Real clients changing state under the switch and the PolicyKit prompt
itself are part of [release acceptance](release-testing.md).

## Navigation flow

```text
Sidebar "Connect your devices" ─► list (cards)
   card body ─► method page [Devices]
       Start/Stop ─────────────► same page, new state (Stop asks first)
       Set up / Sign in ───────► Set up page ─► connected ─► back to the method page
       Not installed ──────────► Install and continue ─► Set up page opens by itself
       Add device ─────────────► Add another device ─► (sub-pages) ─► Back
       ⋮ Remove device ────────► "Remove X?" ─► list refreshed
       Ready: Back to Share/Connect ─► the task the person came from
   header switcher ─► [Advanced] ─► API access / Manage network / guides (pages)
Header Back: one page back. Selecting the sidebar entry again: back to the list.
```

**Share** and **Connect** reach the same pages. Their sidebar cards call
`open_provider(provider)`, which records the task in `_network_return_page`
so the page can offer **Back to Share/Connect**. The guided setup uses
`_apply_vpn_selection`, which maps a destination to a view:
`connect_private` opens Set up, the rest opens Devices.

Home and navigation never install, start, stop or scan anything. Every action
is a button on a method's page or a card's switch.

## UX decisions

- **Pages, not dialogs.** A method is a place you go to and come back from.
  Back is always the header's Back, and the window behind stays the window.
- **Five state words.** One word on the card and one sentence with one button
  on the page. The precise state (`ConnectionState`, recovery, technical
  detail) stays in **Advanced**.
- **Devices first.** The Devices tab answers "which computers, are they
  online, what is their address" without any ID.
- **No promises the API cannot keep.** Remove appears only when it will work.
  Without an API, the place where it works (the website) is offered instead.
- **Providers are independent.** Two methods on at the same time each show
  their own state and devices. The only coupling, Tailscale and Headscale
  sharing one app, is stated in words where it matters.
- **Tabs in the header.** On wide windows the switcher sits in the header bar;
  at narrow widths it moves to the bottom bar, like the other tasks.
- **One primary action per state**: the Connection button, and **Add device**
  once there is a network.
- **Words over colour**: every state is a word; icons and colour repeat it.

## Tests

`tests/test_connect_your_devices.py` covers the state words, device lists per
provider, removal through the right API, Start/Stop, and the pages (never
dialogs) with their Back behaviour. Real Tailscale, ZeroTier and Headscale
clients and APIs, a screen reader and a high-contrast session are in
[private-network testing](private-network-testing.md) and
[release acceptance](release-testing.md).
