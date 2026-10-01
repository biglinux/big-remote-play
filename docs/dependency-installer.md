# Installing what a task needs

How Big Remote Play finds out which programs are missing and installs them without a terminal. The person-facing steps are in the [user guide](user-guide.md#installing-what-is-missing); privileges are listed in [private-network security](private-network-security.md#privileges).

## What is checked

`utils/dependencies.py` owns one table of components:

| Component | Looked for | Package asked for | Service started afterwards |
|---|---|---|---|
| Sunshine | `sunshine` on `PATH` | `sunshine` (BigLinux ships it as `sunshine-bin`, which provides `sunshine`) | none |
| Moonlight | `moonlight-qt` or `moonlight` on `PATH` | `moonlight-qt` | none |
| Tailscale (also for Headscale) | `tailscale` on `PATH`, or a Tailscale Flatpak | `tailscale` | `tailscaled` |
| ZeroTier | `zerotier-cli` on `PATH`, or a ZeroTier Flatpak | `zerotier-one` | `zerotier-one` |

Share needs Sunshine, Connect needs Moonlight, and the secure connection adds its client only when the other computer is somewhere else. Detection is a lookup at the moment it is asked, never a cached answer: a program installed a second ago, by Big Remote Play or by any other tool, is seen at once.

"Installed" means "Big Remote Play can start it". A Sunshine Flatpak is not counted, because Big Remote Play starts Sunshine itself with its own configuration directory; a Tailscale or ZeroTier Flatpak is counted, because their commands are run through `flatpak run`.

Sunshine's own systemd user service is deliberately not enabled: it would start Sunshine at every login with a different configuration from the one Big Remote Play writes when sharing starts.

## Where it is shown

One widget, `ComponentChecklist` (`ui/dependency_installer.py`), is used everywhere:

- **Home → Share or Connect** when the task's program is missing: *Let's get this computer ready*, then the task opens by itself;
- **Guided setup**, right after *Where is the other device?*;
- **Play over the internet** and the Tailscale, Headscale and ZeroTier connection pages, before sign-in or joining.

Each component is a row with a check or a warning icon and its state in words (**Installed**, **Not installed**, **Checking…**). One button, **Install what's needed** (or **Install and continue** on a connection page), installs exactly the missing ones. While it runs the button reads **Installing…** and is disabled, the status line says what happens next (*Confirm with your password in the window that opens.*), and **Technical details** shows the packages, the method and the package manager's own output.

Only one installation runs at a time in the whole application; a second button waits with *Another installation is running*.

## How it is installed

`install_plan()` builds one of two plans; nothing runs until the person presses the button.

1. **Pamac** (BigLinux, Manjaro): `pamac install --no-confirm <packages>` runs as the user. Pamac asks for the password itself through its PolicyKit action `org.manjaro.pamac.commit`, owns the transaction, waits for the package database lock and keeps its log. No terminal, no `sudo`, no password field in Big Remote Play.
2. **pacman** without Pamac: `pkexec install-components.sh <component ids>`. The helper receives component ids, never package names; it maps them itself, installs with `pacman -S --needed --noconfirm` and enables the services, in the same authorization.

Package names are resolved to the **repository** package that provides them, with the read-only `pacman -Sp --print-format %n <name>`. This matters for Sunshine on BigLinux: the repositories have `sunshine-bin` (`provides=sunshine`) while the AUR has a source package literally named `sunshine`. The previous installer ran `yay -S --needed sunshine` in a terminal, which picked the AUR source build.

After the transaction the components are looked up again, and that decides the result, not the exit code. A package manager that reports success for something still missing is a failure; a noisy transaction that installed the program is a success. With everything there, the services the components need are enabled through the same allowlisted `pkexec /usr/bin/systemctl enable --now <unit>` the network pages use.

| Result | What the person sees | Next |
|---|---|---|
| Everything installed | *All set!*, green checks | the flow continues by itself after a moment |
| Installed, service not started | *Installed. Its background service did not start yet…* | the next step (sign-in) starts it again |
| Password dialog dismissed (pkexec 126/127) | *The installation was cancelled. Nothing was changed.* | **Try again** |
| Still missing | *The installation did not finish…* | **Try again**, details show the package manager's words |
| No Pamac and no pacman | *Big Remote Play cannot install programs on this system…* with the package names | install them with the software center |

The flow continues by itself only after an installation started from that checklist. A checklist that finds everything already installed shows the page as it is (for example **Sign in** on Tailscale) instead of starting a sign-in nobody asked for.

## The Sunshine "still asks to install" bug

Reported: Share asked to install Sunshine, Sunshine was installed, and Share kept asking.

Causes, all fixed:

1. The installed state was read once at startup (`MainWindow.check_system`) and only again from the old installer's success callback. Installing in another program, or closing the old installer window after an installation in an external terminal without pressing **Check again**, left `Not installed` for the rest of the session. Now a task whose program was missing is looked up again before anything is offered, and coming back to the window (focus) refreshes all probes at most every 10 seconds.
2. `yay -S sunshine` resolved to the AUR source package, a long build that often fails or conflicts with `sunshine-bin`, so the transaction failed while the person had already installed Sunshine another way.
3. Success was taken from the terminal's exit code only.

Covered by `tests/test_installer.py`, `tests/test_guided_home_review.py` (a component installed after startup opens the task without asking; installing from the prompt opens the task by itself; a second click starts no second transaction) and `tests/test_guided_setup.py`.

## Checked on a real system (2026-10-01)

On BigLinux with Pamac 11.7 and the KDE PolicyKit agent, the application's own `run_install` installed and removed a small repository package (`sl`): the system's password window appeared, the transaction finished in about ten seconds with no terminal, and the look-up afterwards confirmed the program. Pamac's PolicyKit action keeps an authorization for a few minutes (`auth_admin_keep`), so a second transaction right after the first does not ask again. Pressing **Cancel** in the real password window ended the transaction with exit code 1 and Pamac's *Authentication failed* — printed in the desktop's language (*Autenticação falhou* in Brazilian Portuguese), which is why the check also uses Pamac's own translation of the message; the interface then says *The installation was cancelled* instead of blaming the network. The `pkexec` helper on a system without Pamac and a busy package database remain for [release acceptance](release-testing.md).
