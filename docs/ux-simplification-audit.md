# UX simplification audit

A review of every main screen against one question — *does someone who has never set up remote play need to see this to play now?* — and what was changed. Technical, optional and diagnostic controls moved behind named secondary places (**Advanced options**, **Technical details**, **Connection details**, **Support**); nothing was removed that someone needs.

Product decisions behind the changes:

- Two tasks: **Share** (this computer runs the game) and **Connect** (play on this device). Everything else is reached from them or from the guided setup.
- One primary action per state. A destination that is permanently in the sidebar is not repeated as a button on other pages.
- Detect instead of asking; install in place instead of sending people to a terminal.
- States in words; colour and icons only repeat them.

## Navigation after the change

```text
Sidebar                        Content
  Home                           hero (logo, Start with the guided setup) · Share · Connect
  Share       ─ Overview         source → Start sharing; requests; Connected now;
                                 Available over the internet; 3. Connect the other PC
              ─ Preferences      sound and access
              ─ Support          live latency · Connection history · server tools · diagnostics
  Connect     ─ Computers        Choose a computer (one card per computer)
              ─ Advanced options Connect again · Other ways to connect · On this computer
  Play over the internet         one sentence and one button; devices; Advanced mode
  ─────────────
  Streaming          indicator (Home and internet pages): e.g. "Sunshine · Running"
  Secure connection  indicator (Home and internet pages): e.g. "Tailscale · Connected"
  (Share/Connect show one card per service instead)
```

## Findings and changes

### Home

| Found | Change |
|---|---|
| The logo was absent; an "eyebrow" with the product name, a device diagram and three benefit chips competed with the decision | One hero: the application logo (the existing `big-remote-play.svg`, rasterized for HiDPI), the headline, one sentence and **Start with the guided setup** |
| **Guided setup** was a separate card between the hero and the tasks | It is the hero's primary button |
| *Playing over the internet?* repeated the sidebar item and the guide's second question | Removed from Home |
| Role cards said *Needs Sunshine* | *Needs a quick installation*; the product name stays in the tooltip and the installer |
| Status of streaming and the secure connection was visible only on Share/Connect | Two sidebar indicators on Home and the internet pages |

### Guided setup — see [guided setup](guided-setup-redesign.md)

| Found | Change |
|---|---|
| It only handed over to pages; a missing program was discovered later | **Let's get this computer ready** checks and installs in place, then continues by itself |
| An extra *Continue* page before the detection | Folded into the detection page |

### Installing — see [installing what a task needs](dependency-installer.md)

| Found | Change |
|---|---|
| Installation opened an embedded or external terminal (`yay`, `paru`, `pkexec pacman`) | Pamac (its own PolicyKit dialog) or an allowlisted `pkexec` helper; no terminal |
| Sunshine kept being reported missing after installation | Looked up again before offering anything; refreshed on window focus; resolved to the repository provider (`sunshine-bin`) |
| Success decided by the exit code | Decided by looking again |

### Share

| Found | Change |
|---|---|
| Pairing was a field at the end of step 3 (*Pairing code shown on the other PC*) | A request opens a dialog by itself and stays as a card with a countdown; the field is the fallback under **Type a pairing code yourself** — see [pairing requests](pairing-ux.md) |
| *Available over the internet* showed copy rows of addresses only | **Secure connection**, **This computer**, **Status**, **Copy connection information**; the addresses under **Connection details** |
| *Playing over the internet?* row duplicated the sidebar | Removed |
| A first Sunshine without a user blocked approvals with a browser detour | A user is created once with a random password kept in the keyring; an existing user is never replaced |
| Support's *Connection history* was a 3-minute ping plot that stayed empty | Renamed **Latency in the last 3 minutes** with explanations; a real, stored **Connection history** — see [connection history](connection-history-fix.md) |

### Connect

| Found | Change |
|---|---|
| *Choose a computer* sat in a list capped at 400 px inside the scrolling page, so the third and fourth computers were hidden behind an inner scrollbar — it looked like a limit of two | A grid of cards, two per line when there is room, every computer visible; no inner scroller |
| Select, then press **Connect** | Choosing a card connects; *Connecting to …* while it runs; **Stop** only while there is something to stop |
| Addresses on every row | In the tooltip and the accessible description |
| *Connect again*, *Other ways to connect*, *On this computer* and an internet link on the same page | **Computers** and **Advanced options** tabs in the header, like Share; the internet link removed (sidebar) |
| Discovery dropped every result when `avahi-browse` took longer than its timeout (one stale announcement is enough) | The computers already resolved are kept |

### Play over the internet

| Found | Change |
|---|---|
| **Share this computer** / **Connect to another computer** rows shown in every state | Shown only once the connection is ready (*Ready to play?*) |
| **Sign in** opened a page with a second **Sign in with browser** button | **Sign in** starts the browser sign-in |
| Tailscale's page offered sign-in when Tailscale was not installed; the sign-in could only fail | *Tailscale is not installed yet* with **Install and continue** / **Not now**; after installing, its service starts and the sign-in opens by itself |
| An installation from the hub ended on a page with another button | Continues to sign-in (Tailscale) or the join page (ZeroTier) |

### Polling and cost

Home now reads the private-network state like Share and Connect did (every 6 s while the page is visible) for its indicator, and probes one streaming process. A window in the background runs neither; focusing it reads again.

## Not changed, and why

- **Advanced mode** on the internet page and the **Image and capture** sheet already followed the same principle.
- Connection guides for direct internet access, UPnP and firewall stay where they were: they are already secondary.
- Game Window audio: see [Game Window audio](window-audio-fix.md).

## Checked how

Behavior tests for every change listed (`tests/test_installer.py`, `test_guided_setup.py`, `test_guided_home_review.py`, `test_home_image_audio_policy.py`, `test_service_status_cards.py`, `test_connect_simplified.py`, `test_pairing_requests.py`, `test_connection_history.py`, `test_private_network_ui.py`), and off-screen renders of Home, the guided setup, the install prompt, Connect (both tabs), Share with a request and Support with history, at 1280 and 800 px wide, in light, dark and Gamer. Rendering with real assistive technologies, 150 % text and RTL/CJK screens after translation is part of [release acceptance](release-testing.md).
