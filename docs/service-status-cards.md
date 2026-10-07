# Service status cards

The sidebar of **Share** and **Connect** shows one card per service: the
streaming component of the task, then every secure-connection method. It is an
at-a-glance summary, not a daemon monitor; the page's own primary action stays
the most prominent control. Release checks are in
[release acceptance](release-testing.md#backup-restore-and-service-cards).

## Layout

```text
Streaming                         Secure connection
  Sunshine   (Share)                Tailscale
  Moonlight  (Connect)              ZeroTier
                                    Headscale
```

- **Connect your devices** shows the same cards as Share and Connect: the
  streaming component of the remembered task (Sunshine or Moonlight) and
  Tailscale, ZeroTier and Headscale, each with its own state.
- Home shows no cards. The navigation entries above say whether each task is
  active (see [task states](#task-states-in-the-navigation)).
- Share shows Sunshine, Connect shows Moonlight, and both always show
  Tailscale, ZeroTier and Headscale in that order.
- A card never appears or disappears because its service changed state. It
  changes in place, so the list keeps its shape and position.
- More than one method can be connected; each card shows its own state.
- The sidebar keeps the cards in one column. When the window is compact the
  sidebar becomes its own page and the same column fills its width.

Activating Sunshine or Moonlight opens its component controls. Activating a
network card opens that method's page in **Connect your devices**
(**Devices | Advanced**), which remembers Share or Connect and offers **Back
to Share** or **Back to Connect** once the method works. Sign-in, approval
and permission steps live on that page and are never duplicated in the card.

## Task states in the navigation

**Share**, **Connect** and **Connect your devices** carry a small pill with
one word: **Running** or **Stopped**, and during a transition **Starting…**,
**Stopping…** or **Connecting…** (**Checking…** before the first read). It is
the task's real state on every page, never a consequence of the page that is
open.

| Entry | Running when | Transition |
|---|---|---|
| Share | this window shares, or the Sunshine probe finds a running server (one started elsewhere shares too) | **Starting…** / **Stopping…** while this window's start or stop worker runs |
| Connect | this window streams, or the Moonlight probe finds Moonlight running | **Connecting…** while a connection attempt runs |
| Connect your devices | at least one `ProviderStatus` is `CONNECTED` | **Connecting…** while one is `CONNECTING` and none is connected |

A failed private-network read never claims **Running**. `ui/task_activity.py`
(GTK-free) decides the state as an `Activity` value; only `activity_text`
turns it into words, and logic never compares those words. The pill reuses the
round `state-pill` of Share's own Running/Stopped label: success colour for
running, accent for a transition, neutral for stopped, plus a 1 px outline in
high contrast. The pill itself is hidden from assistive technologies; the
navigation row's accessible description carries the page description and the
state instead.

Starts and stops made in this window update the pill at once:
`HostView` and `GuestView` call their state listeners when sharing starts,
stops or changes phase and when a connection attempt or a stream begins or
ends. The probes below catch changes made outside Big Remote Play.

## Components

- `ServiceStatusCard` (`ui/service_status_card.py`) is an activatable
  `Adw.ActionRow`: the same small icon tile as the methods in Connect your devices, the
  product name as title, the state (and an optional second line) as subtitle,
  and one state icon.
- `streaming_presentation(service_id, installed, running)` and
  `provider_presentation(ProviderStatus)` are the only places that turn
  backend facts into a `CardPresentation(text, tone, detail)` for the
  sidebar. The large cards of **Connect your devices** and a method's page
  reduce the same `ProviderStatus` to five words with
  `device_list.simple_state` (**Not installed**, **Off**, **Connecting…**,
  **Connected**, **Needs attention**); both read the same status, so they
  never disagree about whether a method is connected.
- `DeviceConnectionCard` (`ui/connection_cards.py`) is a different concept, a
  device you stream with, and is not reused for services.

## State sources

No second source of truth is created.

- Sunshine and Moonlight: `SystemCheck` installed probes at startup, again
  whenever the window comes back to the front (at most every 10 s) and before
  a task whose program was missing opens; the running probe (`pgrep`) that
  `MainWindow.p_check` runs on a background thread every 3 s for the
  streaming card of the visible page, and for both programs every third tick
  (9 s) for the Share and Connect pills. One probe runs at a time, and none
  while the window is in the background.
- Network methods: `PrivateNetworkService.overview()` (`ProviderStatus` per
  provider), read on a background thread when a page opens and then every
  6 s while the window is in front; the same read feeds the cards and the
  Connect your devices pill. Overlapping reads are refused and results
  arriving after the window closes are dropped. Tailscale and Headscale share
  `tailscaled`; only the product that owns the daemon reports Connected.

| Source | Card text | Tone |
|---|---|---|
| Streaming: probe pending | Checking... | `checking` |
| Streaming: not installed | Not installed | `missing` |
| Sunshine: installed, not running | Stopped | `inactive` |
| Moonlight: installed, not running | Ready | `ready` |
| Streaming: running | Running | `active` |
| Provider not installed / `Recovery.INSTALL` | Not installed | `missing` |
| `Recovery.START_SERVICE` | Stopped | `inactive` |
| `Recovery.GRANT_ACCESS` | Permission needed | `attention` |
| `DISCONNECTED` | Not connected | `inactive` |
| `CONNECTING` | Setting up | `starting` |
| `NEEDS_AUTHENTICATION` | Sign-in needed | `attention` |
| `NEEDS_AUTHORIZATION` | Waiting for approval | `attention` |
| `CONNECTED` | Connected | `active` |
| `ERROR` | Needs attention | `error` |
| Overview read failed before any result | State unknown | `inactive` |

Network wording comes from `network_common.state_label`, the vocabulary of
every private-network page.

Second line, only when connected and only what the provider really reports:
Tailscale/Headscale show the number of other devices online; ZeroTier shows
the network name. The tailnet name (often the account's e-mail address), the
account and private addresses are never shown on the card.

## Visual and accessibility contract

- One 1 px boundary in every state. Active: accent-tinted surface, accent
  border, accent icon tile and a check icon. Off, missing and checking: neutral
  surface and a neutral (not faded) icon tile with a small dot. Attention and
  error: warning/error border and a warning icon.
- The state is always written; colour and icon only reinforce it. The
  accessible description is state, second line and service purpose.
- Keyboard focus draws a separate 2 px ring on `:focus-visible` only. A card
  that merely holds focus (after a click or a page change) keeps its thin
  border. The same rule applies to the Home and Guided setup choice cards.
- Motion: 200 ms colour/border transitions and one 420 ms fading ring when a
  card becomes active. Nothing loops. `@media (prefers-reduced-motion:
  reduce)` removes them, and GTK drops them when animations are disabled.
- High contrast: full-contrast 1 px borders, an inset ring for active cards and
  full-contrast headings and icons.
- The **Gamer** appearance (`gamer.css`) repeats the tone rules with its
  palette: an active card is lit, the rest stay quiet; see
  [visual design](visual-design.md#component-mapping).
