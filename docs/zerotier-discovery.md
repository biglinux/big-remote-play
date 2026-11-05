# Finding computers on a ZeroTier network

You joined a friend's ZeroTier network and want to play on the computer that shares games there. This page explains how Big Remote Play finds it, what it can and cannot know, and what to do when nothing is found.

## What a member can know

Joining a ZeroTier network does not give access to its member list. Only the network owner's ZeroTier Central account has it. `zerotier-cli listpeers` shows ZeroTier's own servers and the nodes this computer recently talked to, not the members, and never their addresses on the network.

So the app keeps these sources apart:

| Source | Needs | Shown as |
|---|---|---|
| ZeroTier Central (API token in **Advanced**) | The owner's account | Every member, approved or waiting, with last activity |
| A search of the network | Nothing; you start it | Computers that answered, **Ready to play** when Sunshine answered just now |
| Computers found before | A previous search | **Not checked since the last search** until they answer again |
| Connection history | A previous connection | Recent computers in Connect |

## The search

**Connect your devices → ZeroTier → Devices → Find computers to play on → Search**, or the search button in **Connect** while ZeroTier is on.

1. The question *who shares here?* goes to the network's broadcast address. A computer sharing with Big Remote Play answers while it shares (UDP 48011, the port the search code already uses), with its name and port only. This finds the game PC on a network of any size.
2. Addresses this computer talked to on that network (its neighbour table), computers found before and ZeroTier computers from the connection history are asked next.
3. The network itself is asked, using this computer's real prefix (never an assumed /24). A network larger than 1,024 addresses is asked only in the /24 around this computer, and the page says so.

Each address gets one TCP connection to the GameStream port (47989) and, when it answers, Sunshine's unauthenticated `/serverinfo`, which gives the computer's name and Sunshine identity. No ping sweep, no other port. At most 64 addresses are asked at once, each for up to 1 second, the whole search for at most 45 seconds, and **Cancel** stops it at once.

Measured on a real ZeroTier network (a /16, two other members offline): 253 addresses in 6.0 seconds, 26 MB of memory for the search process.

## What is remembered

Found computers are kept per network in `~/.config/big-remote-play/history/zerotier-found.json` (owner-only), for 30 days. A complete search replaces what is known of the searched block; a cancelled or timed-out search keeps the older knowledge.

Connect offers a found computer only while this computer is connected to that same network. An address from a network you left, or from another network that uses the same addresses, is never offered. The same Sunshine (same identity) reached through two networks is one card, with the other address tried as an alternative.

## When nothing is found

- The game PC must be sharing, and on the same network (both approved by the owner).
- A computer sharing with another program than Big Remote Play does not answer the broadcast question. On a large network it is found only if it is in the block near this computer or was contacted before. Use **Add by Address** with its ZeroTier address.
- The network owner may block traffic between members with flow rules. The search respects that and then finds nothing; ask the owner.
