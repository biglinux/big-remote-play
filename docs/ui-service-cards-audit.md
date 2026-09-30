# Service cards and guided choices: audit

Findings that led to the current [service status cards](service-status-cards.md)
contract. Validation: [ui-service-cards-validation.md](ui-service-cards-validation.md).

## Files involved

| Area | Files |
|---|---|
| Service cards | `ui/service_status_card.py`, `MainWindow.create_status_footer`, `_relevant_service_ids`, `_filter_status_rows`, `_refresh_service_state`, `_refresh_private_network_status`, `p_check` in `ui/main_window.py` |
| Guided setup and Home choices | `choice_card`, `question_page` in `ui/guided_setup.py`; `create_action_card`, `_on_home_page_changed` in `ui/main_window.py` |
| Connection method | `RemoteConnectionPage.show_methods` in `ui/remote_connection.py`; `action_row(icon_style="tile")`, `icon_tile` in `ui/components.py` |
| Wording | `state_label` in `ui/network_common.py` |
| Styles | `usr/share/big-remote-play/ui/style.css`, `gamer.css` (higher-priority overlay) |
| State sources | `utils/system_check.py` (Sunshine/Moonlight), `private_network/service.py`, `tailscale.py`, `zerotier.py` (`ProviderStatus`) |

## Thick border on guided choices

Cause: `.role-card:focus, .role-card:focus-visible` drew an accent border plus
a 2 px outline at a 2 px offset. Plain `:focus` matches any focused widget, and
the UI focuses a card for the user: `Adw.NavigationView` focuses the first
card of every pushed guided question, and `_on_home_page_changed` focuses the
Share or Connect card when Home returns. After a mouse click the next page
therefore showed a permanent double frame of about 5 px on its first card. The
Gamer overlay repeated the rule. `.role-card:checked` and
`.brp-guided-choice:checked` never matched (the cards are plain buttons).

Confirmed by rendering the first guided question with the old stylesheet and
window focus not visible: the first card carried the heavy frame.

## How states were detected

- Sunshine/Moonlight: `SystemCheck.has_*` at startup and `is_*_running`
  (`pgrep`) every 3 s for the visible task only. The card combined both.
- Tailscale/ZeroTier/Headscale: `PrivateNetworkService.overview()` on a thread
  every 6 s while Share or Connect is visible, with an overlap guard.
- No extra source of truth is needed: both already give an explicit state
  (`ConnectionState` plus `Recovery`).

## Why network cards appeared and disappeared

`_relevant_service_ids` showed a provider only when it was the saved method or
reported Connected. Connecting a method added a row; disconnecting removed it;
an unused method never appeared. The rows shifted and the list read as broken.
The card also showed a large **Selected: Tailscale** pill that repeated the
title in the narrow sidebar.

## Other inconsistencies

- Card wording ("Missing", "Disconnected", "Connection failed", "Waiting for
  authorization") differed from the private-network pages ("Not available",
  "Not connected", "Needs attention", "Waiting for approval").
- The Connection method dialog said only **Installed**/**Not installed**, even
  when a method needed sign-in or approval.
- Cards used a 28 px `service-icon-frame`, unlike the Connection method tile.
- A failed first overview left network cards on "Checking..." indefinitely.
- Sunshine and Moonlight had inline state mapping in `MainWindow` instead of
  one presentation function.
- Detail that could be shown safely was missing; the Tailscale tailnet name is
  often an e-mail address and must not be it.

## Decision

Evolve the existing `ServiceStatusCard` instead of adding a second widget:
keep `ConnectionState`/`Recovery`/`ProviderId` as the state model, add one
pure presentation layer for streaming and providers, show every method on
Share and Connect, and reuse the Connection method tile and state pills.
`DeviceConnectionCard` stays separate: it describes devices, not services.
Cards stay in the sidebar, where they are persistent, subordinate to the
page's primary action and do not turn Share or Connect into a dashboard.
