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

Moonlight Qt sends the same placeholder device name (`roth`) from every computer, so the name shown is the one this computer gives the address — a name set on the internet page, the private network's name for that peer, or the local network name — and otherwise *Device at 192.168.1.30*.

## What happens

While sharing, the existing upkeep (every few seconds, on a worker) reads the waiting requests. `host/pairing_requests.py` (`RequestTracker`) turns each reading into events:

- **new** — a dialog opens at once: **New connection request** — *Living room PC wants to connect to this computer. Type the PIN shown on its screen.* The PIN field has the focus, accepts paste (`12 34` and `12-34` count as `1234`), and Enter approves. **Approve** is enabled only with four digits; anything else says *The PIN has four digits…* and keeps the dialog open. **Reject** cancels the request in Sunshine. **Not now** (or Escape) closes the dialog and keeps the request. When the window is not in front, a desktop notification says *A computer wants to connect* (without any PIN).
- while it waits — **Connection request** stays at the top of **Share → Overview** with **Approve** and **Reject** and *This request expires in 1:42*;
- **gone** — the device stopped waiting (cancelled, cut by the network): the dialog closes and says so;
- **expired** — after two minutes without an answer the request is cancelled in Sunshine and *The connection request from … expired* is shown. A request is never left open indefinitely.

If Sunshine cannot be read for a moment, nothing on screen changes: an unknown state is not treated as "nobody waits". Stopping sharing clears every request.

**Type a pairing code yourself**, under **3. Connect the other PC**, keeps the previous manual path for older Sunshine versions or when no password is saved.

## Sunshine's password

Approving needs Sunshine's administrator user. When Sunshine has **no user at all** (its first start, the API answers 307) and the keyring is available, Big Remote Play creates one — the desktop user name and a random 24+ character password — and keeps the password only in the keyring, so the first request can be approved without asking anything. An existing Sunshine user is never replaced; when its password is not saved or is rejected, Share asks for it as before (**Enter Sunshine password**). **Support → Server password** changes it.

## Safety rules

- Nothing is approved without a person typing the PIN shown on the other screen.
- The PIN is sent only to this computer's Sunshine, for one `pairing_id`; it is never logged, stored, broadcast or put in a notification.
- Requests expire after two minutes and are cancelled, never left waiting.
- Sunshine's authentication is never disabled.

## Checked with a real Moonlight (2026-10-01)

Sunshine 2026.914 on its own port and three never-paired Moonlight Qt 6.1 identities, driven through the application's `SunshineHost` and `RequestTracker`:

- Moonlight sent the placeholder name `roth`; the request was shown as the name this computer gives the address.
- **Approve** with the right PIN for that `pairing_id`: Sunshine answered at once and the device appeared among the paired devices.
- **Reject**: the request was cancelled and the device was not paired.
- A wrong PIN: Sunshine holds the answer for about **10 seconds** (while the device checks it) and then returns `{"status": false}`; the device is not paired. The application used to stop waiting after 5 seconds and called this a connection error. It now waits up to 20 seconds, says *Checking the PIN with …* meanwhile, and then *The PIN did not match. On the other computer, start pairing again and type the new PIN here.*

## Tests

`tests/test_pairing_requests.py`: no request, a new request opening the dialog with the field ready, approving with Enter and a pasted PIN (only that `pairing_id`), an invalid PIN never sent, rejecting, expiring, a device that stops waiting, Sunshine not answering, stopping sharing, placeholder names, creating a Sunshine user only when none exists and never replacing one. Real Moonlight pairing through the dialog still needs a target-machine check ([release acceptance](release-testing.md)).
