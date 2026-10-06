# Connect your devices: one page per connection method

How Tailscale, ZeroTier and Headscale are presented and managed since the
internet page became **Connect your devices**, what was wrong before, and why
the structure is what it is. Provider internals (states, upstream contracts,
the shared Tailscale app) are in
[private-network architecture](private-network-architecture.md); the sidebar
cards are in [service status cards](service-status-cards.md).

## The previous problem

The sidebar entry was **Play over the internet**. Its page showed one plan
sentence with one button, a list of the three methods and links. Each method
opened a **dialog** over whatever page the person was on, with three views
(**Overview | Set up | Details**). Details embedded an older full page. Inside
the dialog, **Add a device or invite a player**, **Manage network**,
**API access** and the guides opened more dialogs or sheets on top.

| Found | Effect |
|---|---|
| Navigation was modal: method → dialog → sheet → sheet | Back meant "close", the window behind was unreachable, and three levels of sheets were common |
| Devices lived in Details, behind a view switch, mixed with addresses, IDs and maintenance | "Which of my computers are online, and what is their address?" took several clicks |
| One plan sentence for all methods on the first screen | Two methods on at once (for example Tailscale and ZeroTier) could not both be read; only the "best" one was described |
| Removing a device was in a separate management dialog, whether or not the provider's API could do it | Buttons that only explained that something was impossible |
| The Overview checklist (Installed, Turned on, Signed in, Connected) repeated the state in four rows | Long and technical for a state that fits in one word |

## The new architecture

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

The same tools still open as sheets when they are reached from outside Connect
your devices, for example the direct-connection guide from **Share →
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
narrow widths). Since the [cards redesign](connect-devices-cards-redesign.md)
each card is a small panel of its provider: name and tag (**Recommended**,
**Advanced**), a working on/off switch, the state in one word, the network or
server, this computer's private address and *2 online · 3 devices*. Network
IDs, accounts and keys are still not on a card. Below the cards, **Advanced**
links to *Internet, router and firewall* and *Without a private network*.

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
     then **Remove device** when the API can do it, otherwise **Manage
     devices** (the provider's website).
5. **Add device**: *Add another device*, a page with the steps for this method.

**Advanced** holds the technical part. While connected it shows **Connection
quality**, measured only while that tab is visible. Then *This computer on the
network*: addresses, name on the network, node ID, network name, account,
network IDs, the Headscale server and the service details. **Manage** offers
*Manage network*, *Join another network* or *Create a network* (ZeroTier),
*Use another account* (Tailscale, Headscale), *API access* and *Accounts and
networks*. For Tailscale and Headscale it also shows the saved Tailscale
accounts. **Help** has the internet guide, the Headscale server guide and,
for Tailscale and Headscale, the note that they share one app.

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

Headscale's **Set up** is a [step-by-step wizard](headscale-setup-wizard.md) (where the server runs, its address, DNS, HTTPS, this computer). A Headscale page is honest about the app: when Tailscale is active, Headscale
reads **Off** with *Starting it turns Tailscale off: both use the Tailscale
app on this computer.*, or **Set up** with *The Tailscale app on this computer
is used by Tailscale now. Setting up Headscale switches it.* The same holds the
other way round.

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

Credentials are unchanged: they live in the Secret Service keyring and are
entered on the **API access** page, never shown again, never logged.

## Navigation flow

```text
Sidebar "Connect your devices" ─► list (cards)
   card "Open" ─► method page [Devices]
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
so the page can offer **Back to Share/Connect**. The guided setup and older
callers use `_apply_vpn_selection`, which maps the old page names:
`connect_private` opens Set up, the rest opens Devices.

Home and navigation never install, start, stop or scan anything. Every action
is a button on a method's page.

## UX decisions

- **Pages, not dialogs.** A method is a place you go to and come back from.
  Back is always the header's Back, and the window behind stays the window.
- **Five state words.** The checklist of four steps became one word on the
  card and one sentence with one button on the page. The precise state
  (`ConnectionState`, recovery, technical detail) stays in **Advanced**.
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

- `tests/test_connect_your_devices.py`:
  - pure: the five state words, device lists per provider (this device, API
    ids, removability, last seen, ZeroTier per network);
  - service: two methods connected at once keep their own devices; removal
    goes to the right API and network; Headscale switches the Tailscale
    profile; Start/Stop commands;
  - UI with fake services: the list opens pages (never dialogs) with tabs and
    Back, Copy IP, Remove only with an API and only after confirming, Manage
    devices otherwise, the ZeroTier network choice, Start/Stop with
    confirmation, Install and continue going on by itself, sign-in as a page,
    technical data only in Advanced, quality measured only while visible,
    Back to Share/Connect, markup-safe names. Advanced tools, guides, steps and
    Previous Networks open as pages and stop their work when the navigation
    jumps past them.
- Updated: `test_private_network_ui.py`, `test_vpn_networks.py`,
  `test_service_status_cards.py`, `test_guided_setup.py`,
  `test_guided_home_review.py`, `test_ui_task_flows.py`,
  `test_connect_redesign_regressions.py`,
  `test_ui_premium_layout_regressions.py`,
  `test_visual_accessibility_regressions.py`.
- Off-screen renders (private `kwin_wayland --virtual`, simulated data) of the
  list, a method's Devices and Advanced, and Add device at 400 and 1100 px.
- Not covered by automated tests: real Tailscale, ZeroTier and Headscale
  clients and APIs, a real screen reader and a real high-contrast session; see
  the [private-network test matrix](private-network-test-matrix.md) and
  [release acceptance](release-testing.md).
