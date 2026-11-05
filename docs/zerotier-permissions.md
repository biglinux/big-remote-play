# ZeroTier and Tailscale without repeated passwords

Turning ZeroTier off in **Connect your devices** used to ask for the administrator password every time, while Tailscale did not. This page explains why and what changed.

## Why it asked

Tailscale's `down`/`up` are requests to its daemon, which allows the desktop user once that user is its *operator*. ZeroTier has no such pause: the app turns ZeroTier off by stopping the `zerotier-one` service, a system change that `pkexec systemctl` protects with the generic administrator rule.

## What changed

The package ships a helper with one purpose, `/usr/share/big-remote-play/scripts/vpn-service-helper.sh`, and its PolicyKit action `br.com.biglinux.remoteplay.vpn-service`:

- it accepts exactly two words: `start` or `stop`, and `zerotier-one` or `tailscaled`; anything else exits with status 2 before any command runs;
- `start` runs `systemctl enable --now <unit>.service` (as the app always did), `stop` runs `systemctl stop <unit>.service`;
- it cannot run other commands, touch other services or change a network's settings, memberships or keys;
- the action is allowed without a password for the **active local session** (the person at this computer) and refused for remote and inactive sessions.

There is no sudoers rule. An administrator who wants the password back can add a PolicyKit rule for that action id. A source checkout without the installed helper keeps asking through `pkexec systemctl`.

## What turning off means

Stopping `zerotier-one` disconnects **every** ZeroTier network of this computer; the switch asks first when there is more than one. Memberships, network IDs, settings and addresses are kept, and turning it on again reconnects them. Leaving a network (**Leave network** on the network page) is different: it removes the membership from this computer and is never used as a pause.

Joining a network and reading the network list use ZeroTier's own local token in `~/.zeroTierOneAuthToken`, copied once with **Allow** on the ZeroTier page; after that they need no password.
