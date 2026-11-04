"""Support → Check this computer: plain-language results from real facts.

GTK-free. :func:`gather_facts` reads this computer (worker thread, read-only:
no package, firewall, keyring write or network change). :func:`checks` turns
those facts into the rows Support shows, each with a state in words and,
when one exists, a safe action the interface offers (never run by itself).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
import shutil

from big_remote_play.utils.i18n import _, pgettext

OK = "ok"
WARNING = "warning"
PROBLEM = "problem"
INFO = "info"

# Actions the interface knows how to offer (see ui/support_center.py).
ACTION_START = "start_sharing"
ACTION_ALLOW_FIREWALL = "allow_firewall"
ACTION_FIX_POINTER = "fix_pointer"
ACTION_SAVE_KEYRING = "save_keyring"
ACTION_RETRY_KEYRING = "retry_keyring"
ACTION_ENTER_PASSWORD = "enter_password"
ACTION_INSTALL = "install"
ACTION_TEST_AUDIO = "test_audio"
ACTION_OPEN_LOG = "open_log"
ACTION_PAIR = "pair"


@dataclass(frozen=True)
class Check:
    key: str
    state: str
    title: str
    detail: str
    action: str = ""
    action_label: str = ""


@dataclass(frozen=True)
class Facts:
    sunshine_installed: bool = False
    sunshine_running: bool = False
    moonlight_installed: bool = False
    session: str = ""
    desktop: str = ""
    unusable_devices: tuple[str, ...] = ()
    keyring: str = "unknown"  # sunshine_credentials.KEYRING_*
    password_known: bool = False
    password_persisted: bool = False
    password_rejected: bool = False
    cursor_problem: str = ""
    cursor_fix: str = ""  # capture value that shows the pointer
    cursor_backend: str = ""
    firewall_blocked_ports: tuple[str, ...] | None = None  # None: not checked
    firewall_tool: str = ""
    encoders: tuple[str, ...] = field(default_factory=tuple)


def moonlight_installed(which: Callable[[str], str | None] = shutil.which, home: Path | None = None) -> bool:
    if which("moonlight") or which("moonlight-qt"):
        return True
    home = home or Path.home()
    return any((root / "com.moonlight_stream.Moonlight").is_dir() for root in (Path("/var/lib/flatpak/app"), home / ".local/share/flatpak/app"))


def gather_facts(*, sunshine, credentials_status, configured_capture: str = "", encoders: tuple[str, ...] = (), check_firewall: bool = True, environ: Mapping[str, str] | None = None) -> Facts:
    """Read the facts (worker thread). Each probe is bounded and read-only."""
    from big_remote_play.host.controllers import unusable_devices
    from big_remote_play.host.cursor_check import cursor_report, desktop_session
    from big_remote_play.host.sunshine_manager import sunshine_executable

    running = bool(sunshine.is_running())
    session, desktop = desktop_session(environ)
    cursor = cursor_report(sunshine.config_dir / "sunshine.log", configured=configured_capture, environ=environ) if running else None
    blocked: tuple[str, ...] | None = None
    tool = ""
    if running and check_firewall:
        from big_remote_play.host.firewall_check import check_firewall as firewall

        try:
            report = firewall(int(sunshine.api_port) - 1)
        except Exception:  # a check that cannot run claims nothing
            report = None
        if report is not None:
            blocked = tuple(report.blocked_ports) if report.blocks else ()
            tool = report.tool
    status = credentials_status
    return Facts(
        sunshine_installed=bool(sunshine_executable()),
        sunshine_running=running,
        moonlight_installed=moonlight_installed(),
        session=session,
        desktop=desktop,
        unusable_devices=tuple(unusable_devices()),
        keyring=status.keyring,
        password_known=status.known,
        password_persisted=status.persisted,
        password_rejected=status.rejected,
        cursor_problem=cursor.problem if cursor else "",
        cursor_fix=cursor.recommended if cursor else "",
        cursor_backend=cursor.backend if cursor else "",
        firewall_blocked_ports=blocked,
        firewall_tool=tool,
        encoders=encoders,
    )


def _session_name(facts: Facts) -> str:
    desktop = {"kde": "KDE Plasma", "gnome": "GNOME"}.get(facts.desktop, facts.desktop.capitalize() or _("an unknown desktop"))
    session = {"wayland": "Wayland", "x11": "X11"}.get(facts.session, _("an unknown session type"))
    # TRANSLATORS: {desktop} is a desktop name such as KDE Plasma; {session} is Wayland or X11.
    return _("{desktop} on {session}").format(desktop=desktop, session=session)


def checks(facts: Facts) -> list[Check]:
    rows: list[Check] = []
    if not facts.sunshine_installed:
        rows.append(Check("sharing", PROBLEM, _("Sharing program is missing"), _("Sunshine shares this computer. Install it to share games and the desktop."), ACTION_INSTALL, _("Install")))
    elif facts.sunshine_running:
        rows.append(Check("sharing", OK, _("Sharing is on"), _("Sunshine is running and other devices can connect.")))
    else:
        rows.append(Check("sharing", INFO, _("Sharing is off"), _("Sunshine is installed. Start sharing to let another device connect."), ACTION_START, _("Start sharing")))

    if facts.moonlight_installed:
        rows.append(Check("receiving", OK, _("Ready to play on other computers"), _("Moonlight is installed, so this computer can also connect to another one.")))
    else:
        rows.append(Check("receiving", INFO, _("Moonlight is not installed"), _("Needed only to play another computer's games here. Sharing this computer does not need it.")))

    session_detail = _("Sunshine chooses how to capture the screen when sharing starts.")
    if facts.cursor_backend:
        session_detail = _("Sunshine captures the screen with {method}.").format(method=facts.cursor_backend.upper() if facts.cursor_backend in ("kms", "x11") else facts.cursor_backend)
    rows.append(Check("session", INFO, _session_name(facts), session_detail))

    if facts.unusable_devices:
        rows.append(
            Check(
                "input",
                PROBLEM,
                _("Remote mouse, keyboard and controllers cannot work"),
                _("Sunshine cannot open {devices}, so the other device's input does not reach this computer. Restart this computer once after installing or updating Sunshine.").format(
                    devices=", ".join(facts.unusable_devices)
                ),
            )
        )
    else:
        rows.append(Check("input", OK, _("Remote mouse, keyboard and controllers can work"), _("Sunshine can create its virtual input devices.")))

    if not facts.sunshine_running:
        rows.append(Check("pointer", INFO, _("Mouse pointer"), _("Checked while sharing: Sunshine reports whether it can draw the pointer into the picture.")))
    elif facts.cursor_problem and facts.cursor_fix:
        rows.append(
            Check(
                "pointer",
                WARNING,
                _("The other device may not see the mouse pointer"),
                _("Sunshine's screen capture cannot draw the pointer on this computer. Another capture method can."),
                ACTION_FIX_POINTER,
                _("Fix"),
            )
        )
    elif facts.cursor_problem:
        rows.append(
            Check(
                "pointer",
                WARNING,
                _("The other device may not see the mouse pointer"),
                _("Sunshine reports that it cannot capture the pointer. In Moonlight, Ctrl+Alt+Shift+N asks Sunshine to draw it again."),
            )
        )
    else:
        rows.append(Check("pointer", OK, _("Mouse pointer"), _("Sunshine reports no problem drawing the pointer.")))

    if facts.password_rejected:
        rows.append(
            Check("keyring", PROBLEM, _("Sunshine rejected the saved password"), _("Devices cannot be approved until the current password is entered."), ACTION_ENTER_PASSWORD, _("Enter password"))
        )
    elif facts.keyring == "unresponsive":
        rows.append(Check("keyring", WARNING, _("The system keyring did not answer"), _("It may be locked. Unlock it, then try again. Sharing still works."), ACTION_RETRY_KEYRING, _("Try again")))
    elif facts.password_known and not facts.password_persisted:
        rows.append(
            Check("keyring", WARNING, _("Password kept only for this session"), _("The system keyring did not save it. It works until you close Big Remote Play."), ACTION_SAVE_KEYRING, _("Save"))
        )
    elif facts.keyring == "unavailable":
        rows.append(Check("keyring", WARNING, _("No system keyring"), _("Passwords cannot be remembered. Install or start a keyring such as KWallet or GNOME Keyring.")))
    elif facts.password_persisted:
        rows.append(Check("keyring", OK, _("Password saved in the system keyring"), _("Devices can be approved with their PIN alone.")))
    else:
        rows.append(Check("keyring", INFO, _("System keyring"), _("Big Remote Play creates and saves Sunshine's password the first time sharing starts.")))

    if facts.firewall_blocked_ports is None:
        rows.append(Check("firewall", INFO, _("Firewall"), _("Checked while sharing.")))
    elif facts.firewall_blocked_ports:
        rows.append(
            Check(
                "firewall",
                PROBLEM,
                _("The firewall blocks other computers"),
                _("{tool} does not allow Sunshine's ports ({ports}).").format(tool=facts.firewall_tool or _("The firewall"), ports=", ".join(facts.firewall_blocked_ports)),
                ACTION_ALLOW_FIREWALL,
                _("Allow"),
            )
        )
    else:
        rows.append(Check("firewall", OK, _("The firewall lets other computers in"), _("Sunshine's ports are reachable from the local and private networks.")))

    hardware = [name for name in facts.encoders if name]
    if hardware:
        rows.append(Check("picture", OK, _("Video encoding"), _("Available: {encoders}").format(encoders=", ".join(hardware))))
    else:
        rows.append(Check("picture", INFO, _("Video encoding"), _("Sunshine chooses the graphics card when sharing starts. Without one, video uses the processor and the picture may be slower.")))
    return rows


def state_label(state: str) -> str:
    """The state in words: color and icon only repeat it."""
    # TRANSLATORS: status badges of Support → Check this computer, shown next to
    # each result. "Check" is the warning state ("worth a look"), a noun or
    # adjective, never the verb: use the word your desktop shows for a warning.
    return {
        OK: pgettext("check state", "OK"),
        WARNING: pgettext("check state", "Check"),
        PROBLEM: pgettext("check state", "Problem"),
        INFO: pgettext("check state", "Info"),
    }.get(state, state)


def facts_without_secrets(facts: Facts) -> dict[str, object]:
    """For a support report: states only, never a password, token or address."""
    return {
        "sunshine_installed": facts.sunshine_installed,
        "sunshine_running": facts.sunshine_running,
        "moonlight_installed": facts.moonlight_installed,
        "session": facts.session,
        "desktop": facts.desktop,
        "virtual_input_unusable": list(facts.unusable_devices),
        "keyring": facts.keyring,
        "password_saved": facts.password_persisted,
        "cursor_capture": facts.cursor_backend,
        "cursor_problem": facts.cursor_problem,
        "firewall_blocks": bool(facts.firewall_blocked_ports),
    }


__all__ = ["Check", "Facts", "checks", "facts_without_secrets", "gather_facts", "moonlight_installed", "state_label"]
