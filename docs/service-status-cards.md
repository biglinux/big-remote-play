# Service status cards

The sidebar of **Share** and **Connect** shows one card per service: the
streaming component of the task, then every secure-connection method. It is an
at-a-glance summary, not a daemon monitor; the page's own primary action stays
the most prominent control. Audit and validation notes:
[ui-service-cards-audit.md](ui-service-cards-audit.md),
[ui-service-cards-validation.md](ui-service-cards-validation.md).

## Layout

```text
Streaming                         Secure connection
  Sunshine   (Share)                Tailscale
  Moonlight  (Connect)              ZeroTier
                                    Headscale
```

- Home and the private-network pages show no cards.
- Share shows Sunshine, Connect shows Moonlight, and both always show
  Tailscale, ZeroTier and Headscale in that order.
- A card never appears or disappears because its service changed state. It
  changes in place, so the list keeps its shape and position.
- More than one method can be connected; each card shows its own state.
- The sidebar keeps the cards in one column. When the window is compact the
  sidebar becomes its own page and the same column fills its width.

Activating Sunshine or Moonlight opens its component controls. Activating a
network card opens that method's page for the current task (network details
from Share, setup from Connect); sign-in, approval and permission steps stay
on those pages and are never duplicated in the card.

## Components

- `ServiceStatusCard` (`ui/service_status_card.py`) is an activatable
  `Adw.ActionRow`: the same small icon tile as **Connection method**, the
  product name as title, the state (and an optional second line) as subtitle,
  and one state icon.
- `streaming_presentation(service_id, installed, running)` and
  `provider_presentation(ProviderStatus)` are the only places that turn
  backend facts into a `CardPresentation(text, tone, detail)`. The
  **Connection method** dialog uses the same `provider_presentation`, so a
  method is named the same way in the sidebar and in the dialog.
- `DeviceConnectionCard` (`ui/connection_cards.py`) is a different concept, a
  device you stream with, and is not reused for services.

## State sources

No second source of truth is created.

- Sunshine and Moonlight: `SystemCheck` installed probes at startup and the
  running probe (`pgrep`) that `MainWindow.p_check` runs every 3 s, only for
  the streaming card of the visible task.
- Network methods: `PrivateNetworkService.overview()` (`ProviderStatus` per
  provider), read on a background thread when Share or Connect opens and then
  every 6 s while one of them is visible. Overlapping reads are refused and
  results arriving after the window closes are dropped. Tailscale and Headscale
  share `tailscaled`; only the product that owns the daemon reports Connected.

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
- The Gamer overlay (`gamer.css`) repeats the tone rules with its palette: an
  active card is lit like the BiGameMode Turbo preset, the rest stay quiet.
