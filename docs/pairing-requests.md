# Approving a device: pairing requests

How a device asking to play on this computer is shown and answered. Steps for people are in the [user guide](user-guide.md#approve-a-new-device); the Sunshine API calls are in [architecture](architecture.md#settings-ownership).

## What Sunshine can tell, and what it cannot

Moonlight pairs with a four-digit PIN that **Moonlight** makes up and shows only on its own screen. Sunshine needs the same PIN to finish the handshake, and that is the security of pairing: the person approving proves they can see the other screen. Sunshine therefore never knows the PIN in advance.

Current Sunshine lists the devices that are waiting (`GET /api/pin`: an id, the name Moonlight sent and the address), accepts the PIN for one of them (`POST /api/pin` with `pairing_id`) and cancels one (`DELETE /api/pin`). So, of the three designs considered:

| Design | Possible? | Used |
|---|---|---|
| A. Sunshine reports the waiting request **and its PIN** | No: Sunshine does not have it | — |
| B. The waiting request is detected; the person types the PIN shown on the other screen | Yes | **Yes** |
| C. Two Big Remote Play computers exchange the PIN between themselves | Only with a new authenticated channel between the apps; sending the PIN over the network would defeat the reason it exists, and a new protocol would be a new attack surface | No |

Moonlight Qt sends the same placeholder device name (`roth`) from every computer, so the name shown is the one this computer gives the address — a name given with **Rename** in Connect your devices, the private network's name for that peer, or the local network name — and otherwise *Device at 192.168.1.30*.

## What happens

While sharing, the existing upkeep (every few seconds, on a worker) reads the waiting requests. `host/pairing_requests.py` (`RequestTracker`) turns each reading into events:

- **new** — a dialog opens at once: **New connection request** — *Living room PC wants to connect to this computer. Type the PIN shown on its screen.* The PIN field has the focus, accepts paste (`12 34` and `12-34` count as `1234`), and Enter approves. **Approve** is enabled only with four digits; anything else says *The PIN has four digits…* and keeps the dialog open. **Reject** cancels the request in Sunshine. **Not now** (or Escape) closes the dialog and keeps the request. When the window is not in front, a desktop notification says *A computer wants to connect* (without any PIN).
- while it waits — **Connection request** stays at the top of **Share → Overview** with **Approve** and **Reject** and *This request expires in 1:42*;
- **gone** — the device stopped waiting (cancelled, cut by the network): the dialog closes and says so;
- **expired** — after two minutes without an answer the request is cancelled in Sunshine and *The connection request from … expired* is shown. A request is never left open indefinitely.

If Sunshine cannot be read for a moment, nothing on screen changes: an unknown state is not treated as "nobody waits". Stopping sharing clears every request.

A request answered here (approved or rejected) is never announced again, even while Sunshine still lists it during the PIN check: pairing ids are never reused, so a new attempt is a new request. After one request is answered, the next device still waiting is asked at once; **Not now** leaves the others on the page. Clicking the desktop notification runs `app.show-pairing-request`, which brings the window to **Share → Overview** and reopens the oldest waiting request.

**Pair a new device**, always on **Overview** right under **Start sharing**, takes the PIN too: one field drawn as four boxes, focused by the notification action, accepting paste with spaces or dashes; Enter or **Approve** sends it. It asks for nothing else. The PIN goes to the request Sunshine lists (one: that one; several: the person picks which; none: nothing is sent and the line under the field says *No device is waiting…*). While Sunshine checks it the field shows a spinner and *Checking the PIN with …*; after a wrong PIN the digits stay selected, ready to type the new PIN over them. This is also the path for Sunshine versions that do not list waiting devices.

## Sunshine's password

Approving needs Sunshine's administrator user. When Sunshine has **no user at all** (its first start, the API answers 307) and the keyring answers a real lookup, Big Remote Play creates the user `sunshine` with a random 32-character password and keeps the password only in the keyring, so the first request is approved with nothing but the PIN. A fixed default password such as `sunshine`/`sunshine` is never used, and Sunshine's authentication is never disabled. An existing Sunshine user is never replaced.

The password belongs to one owner, `SunshineCredentials` ([architecture](architecture.md#sunshines-administrator-password)): the keyring is read once per session, never on the GTK thread, and every call gives up after 10 seconds. That is what fixed the reported freeze when **Save password** was switched on: the old five-field form (PIN, device name, user, password, save) wrote to the keyring on the GTK thread, so a locked or prompting KWallet froze the window, and the same lookup every few seconds in the request upkeep could stop request detection for the rest of the session. The form no longer exists.

The keyring is written **before** Sunshine gets the password. Seen in Big GNOME and BigLinux KDE live sessions (2026-11-04): the first keyring call opens the desktop's own "create a keyring" window, and if the person cancels it, lookups still answer while saving fails; a password that only Sunshine knew would then be lost for good. So a refused save creates no Sunshine user (**Choose a password for sharing** is offered instead), and a save that answers only after the 10-second timeout — the person still typing in that window — is not retried by itself, which would open a new window every minute: **The system keyring did not answer** offers **Try the keyring again**. If the keyring finishes the save later, Sunshine gets that same user on the next upkeep.

When a password is really needed, one sheet asks for it — **Sunshine password**, or **Choose a password for sharing** on a first Sunshine when no keyring works — checks it with Sunshine first (a wrong one keeps the sheet open with what was typed), holds it in memory, then saves it in the keyring when **Remember on this computer** is on. If the keyring cannot keep it, it works until the app closes and **Preferences → Security and access** offers **Save in keyring**. The approval that was waiting for it continues by itself. A password Sunshine rejects (401) is not tried or re-read again until a new one is given. **Preferences → Security and access** says where the password is kept and offers **Change Sunshine password**, which also resets a forgotten one.

## Safety rules

- Nothing is approved without a person typing the PIN shown on the other screen.
- The PIN is sent only to this computer's Sunshine, for one `pairing_id`; it is never logged, stored, broadcast or put in a notification.
- Requests expire after two minutes and are cancelled, never left waiting.
- Sunshine's authentication is never disabled.

## Checked again with Big Remote Play's new screens (2026-11-04)

Moonlight Qt 6.2.0 with new identities (`moonlight pair <host> --pin <PIN>`) against this build:

- on the development PC (X11): the request dialog opened by itself; the PIN typed there paired the device; a wrong PIN kept nothing paired and said so under the PIN field; **Not now** left the request on the page and the PIN typed in **Pair a new device** (`33-33`, with a dash) paired it;
- in the BigLinux KDE Plasma (Wayland) and Big GNOME (Wayland) live sessions: the dialog opened by itself and the PIN alone paired the device; on GNOME the user `sunshine` was created from the keyring without any question once the keyring existed.

- on a real notebook (BigCommunity GNOME 50.5, Wayland) with the **login keyring locked** (its unlock window left open): the window kept answering in 0.2–0.4 s during the 10-second keyring wait; the PIN typed in **Pair a new device** led to the password sheet, which created Sunshine's first user (the password held for the session), and the same PIN was then sent by itself: *Paired. The other device can play now.*

Moonlight shows the placeholder device name, so the requests read *Device at 192.168.122.1*; systemd-resolved's synthetic name `_gateway` is no longer shown as a device name.

## Checked with a real Moonlight (2026-10-01)

Sunshine 2026.914 on its own port and three never-paired Moonlight Qt 6.1 identities, driven through the application's `SunshineHost` and `RequestTracker`:

- Moonlight sent the placeholder name `roth`; the request was shown as the name this computer gives the address.
- **Approve** with the right PIN for that `pairing_id`: Sunshine answered at once and the device appeared among the paired devices.
- **Reject**: the request was cancelled and the device was not paired.
- A wrong PIN: Sunshine holds the answer for about **10 seconds** (while the device checks it) and then returns `{"status": false}`; the device is not paired.

Because of that delay the application waits up to 20 seconds for Sunshine's answer (`PIN_ANSWER_TIMEOUT` in `host/sunshine_manager.py`), says *Checking the PIN with …* meanwhile, and then, for a wrong PIN, *The PIN did not match. On the other computer, start pairing again and type the new PIN here.*

## Tests

`tests/test_pairing_requests.py`: no request, a new request opening the dialog with the field ready, approving with Enter and a pasted PIN (only that `pairing_id`), an invalid PIN never sent, rejecting, expiring, a device that stops waiting, Sunshine not answering, stopping sharing, placeholder names, a request being checked not announced again, the next waiting device asked after an answer, **Not now** and the notification action, creating the `sunshine` user only when none exists and the keyring answers, never replacing one, and a password the keyring cannot save still working for the session. `tests/test_pairing_blocked.py`: the password checked before it is kept, the sheet staying open after a wrong one, and saving with a keyring that never answers while the main loop keeps running. `tests/test_sunshine_credentials.py` and `tests/test_secret_store.py`: reading the keyring once, unavailable, unresponsive, rejected, retry, concurrent saves and an unreadable `sunshine.conf`. Real Moonlight pairing through the dialog still needs a target-machine check ([release acceptance](release-testing.md)).
