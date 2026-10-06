"""Which components this computer has, and how to install the missing ones.

GTK-free. The interface asks :func:`audit` what a task needs, shows the result
in words and, only when the person presses Install, runs the plan from
:func:`install_plan` on a worker thread:

* with Pamac (BigLinux, Manjaro) ``pamac install`` runs as the user; Pamac
  asks for the password through its own PolicyKit action and owns the
  transaction, its lock and its log;
* otherwise one reviewed helper runs through ``pkexec`` and installs with
  pacman.

No terminal is opened and no password passes through this process. A system
service that a component needs (``tailscaled``, ``zerotier-one``) is enabled
afterwards through the same allowlisted ``systemctl`` call the network pages
use. Sunshine's user service is deliberately left alone: Big Remote Play starts
Sunshine itself with its own configuration when sharing starts.

Whether an installation worked is decided by looking again, never by the exit
code alone: a package manager that reports success for a package that
provides nothing usable must not turn into a green check.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
import os
import re
import shutil
import subprocess

from big_remote_play.utils.i18n import _
from big_remote_play.utils.script_protocol import parse_script_line

_PACKAGE_RE = re.compile(r"[a-z0-9][a-z0-9@._+-]{0,127}")


@dataclass(frozen=True)
class Component:
    id: str
    name: str  # product name, never translated
    executables: tuple[str, ...]
    package: str  # name given to the package manager (it may be a "provides")
    unit: str = ""  # system service enabled after installation
    flatpak_keyword: str = ""  # a Flatpak the app can also drive


def _purpose(component_id: str) -> str:
    return {
        "sunshine": _("Sends your game to the other computer."),
        "moonlight": _("Receives the game from the other computer."),
        "tailscale": _("Creates a secure connection between your computers."),
        "zerotier": _("Joins the private network of a friend or a club."),
        "headscale_server": _("Your own private network server."),
        "caddy": _("Gives the server a secure HTTPS address."),
    }.get(component_id, "")


COMPONENTS: dict[str, Component] = {
    "sunshine": Component("sunshine", "Sunshine", ("sunshine",), "sunshine"),
    "moonlight": Component("moonlight", "Moonlight", ("moonlight-qt", "moonlight"), "moonlight-qt"),
    "tailscale": Component("tailscale", "Tailscale", ("tailscale",), "tailscale", unit="tailscaled", flatpak_keyword="tailscale"),
    "zerotier": Component("zerotier", "ZeroTier", ("zerotier-cli",), "zerotier-one", unit="zerotier-one", flatpak_keyword="zerotier"),
    # A Headscale server on this computer; the setup helper configures and
    # starts their services, so none is enabled at installation.
    "headscale_server": Component("headscale_server", "Headscale", ("headscale",), "headscale"),
    "caddy": Component("caddy", "Caddy", ("caddy",), "caddy"),
}

# What each task cannot work without. The secure connection is added only when
# the other computer is somewhere else.
ROLE_COMPONENTS = {"host": ("sunshine",), "guest": ("moonlight",)}
NETWORK_COMPONENTS = {"tailscale": ("tailscale",), "headscale": ("tailscale",), "zerotier": ("zerotier",)}


def purpose(component_id: str) -> str:
    return _purpose(component_id)


def find_executable(names: Iterable[str], *, which: Callable[[str], str | None] | None = None) -> str | None:
    """The first of ``names`` on PATH, looked up now (never cached)."""
    lookup = which or shutil.which
    for name in names:
        found = lookup(name)
        if found:
            return found
    return None


@dataclass(frozen=True)
class ComponentState:
    id: str
    installed: bool
    location: str = ""  # executable path or Flatpak id, for the technical details

    @property
    def component(self) -> Component:
        return COMPONENTS[self.id]


def detect(component_id: str, *, which: Callable[[str], str | None] | None = None, flatpak: Callable[[str], str | None] | None = None) -> ComponentState:
    """Is the component usable by Big Remote Play right now? Read-only and quick.

    Looked up at every call: an installation made a moment ago, here or in
    another program, is seen at once.
    """
    component = COMPONENTS[component_id]
    path = find_executable(component.executables, which=which)
    if path:
        return ComponentState(component_id, True, path)
    if component.flatpak_keyword and flatpak is not None:
        app_id = flatpak(component.flatpak_keyword)
        if app_id:
            return ComponentState(component_id, True, app_id)
    return ComponentState(component_id, False)


def audit(component_ids: Iterable[str], **probes) -> list[ComponentState]:
    """One state per requested component, in the order asked, without repeats."""
    return [detect(component_id, **probes) for component_id in dict.fromkeys(component_ids) if component_id in COMPONENTS]


def missing(states: Iterable[ComponentState]) -> list[str]:
    return [state.id for state in states if not state.installed]


def components_for(role: str | None, *, network: str | None = None) -> list[str]:
    """What a task needs: its streaming component, plus the secure connection when used."""
    needed = list(ROLE_COMPONENTS.get(role or "", ()))
    needed += NETWORK_COMPONENTS.get(network or "", ())
    return list(dict.fromkeys(needed))


# ── installation ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class InstallPlan:
    """What runs when the person presses Install."""

    method: str  # "pamac" | "helper"
    argv: tuple[str, ...]
    components: tuple[str, ...]
    packages: tuple[str, ...]
    units: tuple[str, ...]


def resolve_package(name: str, *, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> str:
    """The repository package that provides ``name``; ``""`` when none does.

    BigLinux ships Sunshine as ``sunshine-bin`` (``provides=sunshine``) while
    the AUR has a source package named ``sunshine``. Asking pacman which
    *repository* package satisfies the name keeps an AUR helper or Pamac from
    picking the hour-long source build. ``pacman -Sp`` only reads the sync
    databases; it needs no privileges and changes nothing.
    """
    if not _PACKAGE_RE.fullmatch(name):
        return ""
    try:
        result = runner(["pacman", "-Sp", "--print-format", "%n", name], capture_output=True, text=True, timeout=20, env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    # Dependencies are printed too; the target is the line with its own name
    # or, for a virtual name, the provider listed last.
    lines = [line.strip() for line in (result.stdout or "").splitlines() if _PACKAGE_RE.fullmatch(line.strip())]
    if name in lines:
        return name
    return lines[-1] if lines else ""


def install_plan(
    component_ids: Sequence[str],
    *,
    which: Callable[[str], str | None] | None = None,
    resolve: Callable[[str], str] | None = None,
    helper: str = "",
) -> InstallPlan | None:
    """The installation of ``component_ids``, or ``None`` when this system offers none.

    Only components from :data:`COMPONENTS` are accepted, so no free text ever
    reaches a package manager.
    """
    ids = tuple(dict.fromkeys(component_ids))
    if not ids or any(component_id not in COMPONENTS for component_id in ids):
        raise ValueError("unsupported component")
    which = which or shutil.which
    units = tuple(dict.fromkeys(COMPONENTS[component_id].unit for component_id in ids if COMPONENTS[component_id].unit))
    if which("pamac"):
        resolver = resolve or resolve_package
        packages = tuple(resolver(COMPONENTS[component_id].package) for component_id in ids)
        if all(packages):
            return InstallPlan("pamac", ("pamac", "install", "--no-confirm", *packages), ids, packages, units)
    if which("pacman") and which("pkexec") and helper:
        # The helper maps the ids to packages itself, as root: it trusts no
        # package name from this process.
        packages = tuple(COMPONENTS[component_id].package for component_id in ids)
        return InstallPlan("helper", ("pkexec", helper, *ids), ids, packages, ())
    return None


@dataclass(frozen=True)
class InstallOutcome:
    """After the transaction, what is really there now."""

    states: tuple[ComponentState, ...]
    returncode: int
    cancelled: bool = False
    service_failed: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return all(state.installed for state in self.states)


# pkexec: 126 = the person dismissed the password dialog, 127 = not authorized.
_CANCELLED_CODES = {126, 127}
# What Pamac's client prints when its PolicyKit authorization is refused or
# the transaction is cancelled (English under LC_ALL=C).
_PAMAC_CANCELLED = ("Authentication failed", "Transaction cancelled")


def _pamac_cancel_messages() -> set[str]:
    """Pamac's own words for a refused authorization, also as it translates them."""
    import gettext

    words = set(_PAMAC_CANCELLED)
    try:
        catalog = gettext.translation("pamac", fallback=True)
        words |= {catalog.gettext(text) for text in _PAMAC_CANCELLED}
    except OSError:
        pass
    return {word.lower() for word in words if word}


def _cancelled(plan: InstallPlan, returncode: int, lines: Sequence[str]) -> bool:
    if plan.method == "helper":
        return returncode in _CANCELLED_CODES
    words = _pamac_cancel_messages()
    return returncode != 0 and any(word in line.lower() for line in lines for word in words)


def run_install(
    plan: InstallPlan,
    *,
    on_line: Callable[[str], None] | None = None,
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
    start_unit: Callable[[str], int] | None = None,
    probe: Callable[[Sequence[str]], list[ComponentState]] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> InstallOutcome:
    """Run the plan on the calling (worker) thread, then look again."""
    returncode = 1
    seen: list[str] = []
    try:
        process = popen(list(plan.argv), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True, bufsize=1, env={**os.environ, "LC_ALL": "C"})
    except OSError as error:
        if on_line is not None:
            on_line(str(error))
        process = None
    if process is not None:
        stream = process.stdout
        if stream is not None:
            for line in stream:
                kind, *values = parse_script_line(line)
                # The helper's progress markers are protocol, not prose.
                if kind == "text" and values[0]:
                    seen.append(values[0])
                    if on_line is not None:
                        on_line(values[0])
        returncode = process.wait()
    states = (probe or (lambda ids: audit(ids)))(plan.components)
    failed: list[str] = []
    if plan.units and all(state.installed for state in states) and start_unit is not None and not (cancelled and cancelled()):
        for unit in plan.units:
            if start_unit(unit) != 0:
                failed.append(unit)
    return InstallOutcome(tuple(states), returncode, cancelled=_cancelled(plan, returncode, seen[-20:]), service_failed=tuple(failed))


__all__ = [
    "COMPONENTS",
    "Component",
    "ComponentState",
    "InstallOutcome",
    "InstallPlan",
    "NETWORK_COMPONENTS",
    "ROLE_COMPONENTS",
    "audit",
    "components_for",
    "detect",
    "find_executable",
    "install_plan",
    "missing",
    "purpose",
    "resolve_package",
    "run_install",
]


# ── the probes the interface uses (tests replace them) ────────────────────


def _default_audit(component_ids: Sequence[str]) -> list[ComponentState]:
    from big_remote_play.utils.system_check import SystemCheck

    return audit(component_ids, flatpak=SystemCheck().flatpak_app_id)


def _default_start_unit(unit: str) -> int:
    from big_remote_play.utils.system_check import SystemCheck
    from big_remote_play.utils.vpn_accounts import VPNAccountManager

    return VPNAccountManager(SystemCheck()).start_service(unit).returncode


def _default_plan(component_ids: Sequence[str]) -> InstallPlan | None:
    from big_remote_play import paths

    return install_plan(component_ids, helper=paths.script_path("install-components.sh"))


_AUDIT: Callable[[Sequence[str]], list[ComponentState]] = _default_audit
_PLAN: Callable[[Sequence[str]], InstallPlan | None] = _default_plan
_RUN: Callable[..., InstallOutcome] = run_install
_START_UNIT: Callable[[str], int] = _default_start_unit


def check(component_ids: Sequence[str]) -> list[ComponentState]:
    """Blocking (worker thread): the current state of ``component_ids``."""
    return _AUDIT(component_ids)


def plan_for(component_ids: Sequence[str]) -> InstallPlan | None:
    """Blocking (worker thread): how ``component_ids`` would be installed here."""
    return _PLAN(component_ids)


def install(plan: InstallPlan, *, on_line: Callable[[str], None] | None = None) -> InstallOutcome:
    """Blocking (worker thread): run ``plan``, enable its services, look again."""
    return _RUN(plan, on_line=on_line, start_unit=_START_UNIT, probe=check)
