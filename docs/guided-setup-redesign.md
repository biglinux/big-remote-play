# Guided setup

The recommended start for someone who has never set up remote play. It asks two questions, gets this computer ready, sets up the secure connection when one is needed, and opens the right page. The normal steps are in the [user guide](user-guide.md#guided-setup).

## Where it starts

Home has one hero: the logo, *Your games. Any screen. Anywhere.*, one sentence and one primary button, **Start with the guided setup**. The two task cards (**Share**, **Connect**) follow under *Or choose what you want to do* for people who already know.

## The flow

```text
What do you want to do?          Share my game · Connect to another computer
        ↓
Where is the other device?       On the same network (Simplest) · Somewhere else
        ↓
Let's get this computer ready    ✓/✕ per program  → [Install what's needed] → All set!
        ↓  (continues by itself)
same network   → Share or Connect
somewhere else → Let's create a secure connection (this computer is looked at)
                   ready            → Use this connection → Share or Connect
                   needs one step   → Play over the internet, whose button does that step
                   nothing yet      → How do you want to connect?
                                        Tailscale (Recommended) · I already use ZeroTier · Advanced options
```

- **Let's get this computer ready** uses the same checklist as every installation ([installing what a task needs](dependency-installer.md)). With everything there it shows the green checks and continues after a moment; with something missing nothing happens until **Install what's needed**, and after the installation the guide goes on by itself. A failed installation stays on the page with **Try again**.
- The guide no longer has a separate *Let's create a secure connection → Continue* page: the detection page carries that explanation and its result replaces it.
- Choosing **Tailscale** opens its connection page. If Tailscale is missing there, *Tailscale is not installed yet* with **Install and continue** comes first, and after installing, the browser sign-in opens without another click.
- Back always goes one question back. A page that is no longer on screen cannot move the guide forward later.

## What it never does

- install anything without the button;
- start sharing, connect to a computer or change a network by being opened;
- ask about Sunshine, Moonlight, Tailscale, ports or addresses: the product names appear only in the checklist rows and their purpose is written next to them.

## Tests

`tests/test_guided_setup.py` covers the questions, the ready step continuing by itself, installing from the guide and continuing, a failed installation with **Try again**, internet detection (ready, one step, nothing), ZeroTier, Back and restarting. `tests/test_guided_home_review.py` covers the Home prompt.
