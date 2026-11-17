"""Open Sunshine's game ports on the home router (UPnP IGD), checked, and close them again.

Playing over the internet without a VPN needs the router to forward the game
ports to this computer. UPnP lets a program ask for that. Measured on the
maintainer's network (2026-11-17, ZTE F6645P fibre router):

- the router supports UPnP IGD (``WANIPConnection``), and ZeroTier already had a
  mapping there;
- yet ``upnpc`` and Sunshine's own UPnP found "no IGD": the router's SSDP
  answers were dropped by this computer's firewall (ufw, ``[UFW BLOCK]
  SRC=192.168.0.1 … PROTO=UDP``). Asking the router directly at its
  description address works without opening anything in the firewall.

So discovery tries SSDP first, then the description addresses common routers
use, on the default gateway only. Every mapping is then read back from the
router: "open" is said only when the router lists it pointing at this
computer. Mappings have a lease and carry the description
:data:`DESCRIPTION`; only those are ever removed. Sunshine's web UI port is
never mapped.

The router's external address is compared with the public address seen from
outside: when they differ (CGNAT, two routers), opening ports here cannot
work, and that is said instead.

Blocking (worker threads); commands use explicit argv with validated values.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
import ipaddress
import json
import re
import shutil
import subprocess
import urllib.request

from big_remote_play.integration_contracts import SUNSHINE_DEFAULT_BASE_PORT, sunshine_stream_ports

DESCRIPTION = "Big Remote Play"
LEASE_SECONDS = 7200  # renewed while sharing; gone by itself when nobody renews it
RENEW_SECONDS = 1800
TIMEOUT = 12

# Where routers publish their UPnP description, tried in order on the gateway.
DESCRIPTION_PATHS = (
    (52869, "/gatedesc.xml"),  # ZTE, Realtek SDK
    (52869, "/picsdesc.xml"),
    (5000, "/rootDesc.xml"),  # miniupnpd (OpenWrt, many TP-Link, ASUS)
    (1900, "/rootDesc.xml"),
    (1900, "/igd.xml"),  # TP-Link
    (49000, "/igddesc.xml"),  # AVM FRITZ!Box
    (49152, "/IGDdevicedesc_brlan0.xml"),  # Arris, Technicolor
    (49152, "/gatedesc.xml"),
    (2869, "/upnphost/udhisapi.dll?content=uuid"),
    (80, "/rootDesc.xml"),
)

Runner = Callable[..., subprocess.CompletedProcess]
Fetcher = Callable[[str], str]


@dataclass(frozen=True)
class Mapping:
    external_port: int
    protocol: str  # TCP | UDP
    client: str
    internal_port: int
    description: str


@dataclass(frozen=True)
class RouterState:
    found: bool = False
    url: str = ""  # the router's description address
    model: str = ""
    external_ip: str = ""
    connected: bool = False
    mappings: tuple[Mapping, ...] = ()
    problem: str = ""  # "" | no_upnpc | no_gateway | no_router | not_connected | refused

    @property
    def cgnat(self) -> bool:
        """The router itself is behind another NAT: ports opened here are not reachable."""
        try:
            address = ipaddress.ip_address(self.external_ip)
        except ValueError:
            return False
        return address.is_private or address in ipaddress.ip_network("100.64.0.0/10")


@dataclass(frozen=True)
class Outcome:
    state: RouterState
    opened: tuple[tuple[int, str], ...] = ()
    failed: tuple[tuple[int, str], ...] = ()
    lines: tuple[str, ...] = field(default=(), compare=False)

    @property
    def ok(self) -> bool:
        return self.state.found and not self.failed and bool(self.opened)


def wanted_ports(base_port: int = SUNSHINE_DEFAULT_BASE_PORT) -> list[tuple[int, str]]:
    ports = sunshine_stream_ports(base_port)
    return [(port, "TCP") for port in ports["tcp"]] + [(port, "UDP") for port in ports["udp"]]


def _run(argv: list[str], timeout: float = TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False, env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"})


def _fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310 - a private gateway address, validated
        return response.read(65536).decode("utf-8", errors="replace")


def default_route(runner: Runner | None = None) -> tuple[str, str]:
    """(gateway, this computer's address towards it), both private IPv4, or ("", "")."""
    runner = runner or _run
    try:
        routes = json.loads(runner(["ip", "-j", "-4", "route", "show", "default"]).stdout or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return "", ""
    for route in routes if isinstance(routes, list) else []:
        gateway, device = str(route.get("gateway", "")), str(route.get("dev", ""))
        if not gateway or not device or device.startswith(("tailscale", "zt", "wg", "tun")):
            continue
        try:
            if not ipaddress.ip_address(gateway).is_private:
                continue
            source = json.loads(runner(["ip", "-j", "-4", "route", "get", gateway]).stdout or "[]")
        except (OSError, subprocess.SubprocessError, ValueError):
            continue
        local = str(source[0].get("prefsrc", "")) if source and isinstance(source, list) else ""
        if local:
            return gateway, local
    return "", ""


def _is_igd(text: str) -> bool:
    return "InternetGatewayDevice" in text and ("WANIPConnection" in text or "WANPPPConnection" in text)


def _model(text: str) -> str:
    maker = re.search(r"<manufacturer>([^<]{1,64})</manufacturer>", text)
    model = re.search(r"<modelName>([^<]{1,64})</modelName>", text)
    return " ".join(part.group(1).strip() for part in (maker, model) if part)


def find_router(gateway: str, *, runner: Runner | None = None, fetch: Fetcher | None = None) -> tuple[str, str]:
    """(description URL, model) of the gateway's UPnP service, or ("", "")."""
    runner = runner or _run
    fetch = fetch or _fetch
    try:
        if not ipaddress.ip_address(gateway).is_private:
            return "", ""
    except ValueError:
        return "", ""
    try:
        found = runner(["upnpc", "-s"]).stdout or ""
    except (OSError, subprocess.SubprocessError):
        found = ""
    match = re.search(r"desc: (http://[^\s]+)", found)
    if match and f"//{gateway}:" in match.group(1):
        url = match.group(1)
        try:
            return url, _model(fetch(url))
        except OSError:
            return url, ""
    for port, path in DESCRIPTION_PATHS:
        url = f"http://{gateway}:{port}{path}"
        try:
            text = fetch(url)
        except (OSError, ValueError):
            continue
        if _is_igd(text):
            return url, _model(text)
    return "", ""


_MAPPING = re.compile(r"^\s*\d+\s+(TCP|UDP)\s+(\d+)->([\d.]+):(\d+)\s+'([^']*)'", re.MULTILINE)


def parse_mappings(text: str) -> tuple[Mapping, ...]:
    return tuple(Mapping(int(port), proto, client, int(internal), description) for proto, port, client, internal, description in _MAPPING.findall(text))


def read_state(url: str, model: str = "", *, runner: Runner | None = None) -> RouterState:
    runner = runner or _run
    try:
        status = runner(["upnpc", "-u", url, "-s"]).stdout or ""
        listing = runner(["upnpc", "-u", url, "-l"]).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return RouterState(found=True, url=url, model=model, problem="refused")
    external = re.search(r"ExternalIPAddress = ([\d.]+)", status)
    connected = bool(re.search(r"Status : Connected", status)) if "Status :" in status else bool(external)
    return RouterState(
        found=True, url=url, model=model, external_ip=external.group(1) if external else "", connected=connected, mappings=parse_mappings(listing), problem="" if connected else "not_connected"
    )


def locate(*, runner: Runner | None = None, fetch: Fetcher | None = None, which: Callable[[str], str | None] | None = None) -> tuple[RouterState, str]:
    """The router and this computer's address towards it."""
    runner = runner or _run
    fetch = fetch or _fetch
    which = which or shutil.which
    if not which("upnpc"):
        return RouterState(problem="no_upnpc"), ""
    gateway, local = default_route(runner)
    if not gateway:
        return RouterState(problem="no_gateway"), ""
    url, model = find_router(gateway, runner=runner, fetch=fetch)
    if not url:
        return RouterState(problem="no_router"), local
    return read_state(url, model, runner=runner), local


def _ours(mapping: Mapping, local: str) -> bool:
    return mapping.description == DESCRIPTION and mapping.client == local


def open_ports(
    ports: Iterable[tuple[int, str]], *, runner: Runner | None = None, fetch: Fetcher | None = None, which: Callable[[str], str | None] | None = None, lease: int = LEASE_SECONDS
) -> Outcome:
    """Ask the router to forward ``ports`` to this computer, then read them back."""
    runner = runner or _run
    fetch = fetch or _fetch
    which = which or shutil.which
    state, local = locate(runner=runner, fetch=fetch, which=which)
    if not state.found or not local:
        return Outcome(state)
    ports = [(int(port), proto) for port, proto in ports if proto in ("TCP", "UDP") and 0 < int(port) < 65536]
    lines: list[str] = []
    for port, proto in ports:
        taken = next((item for item in state.mappings if item.external_port == port and item.protocol == proto and not _ours(item, local)), None)
        if taken is not None:
            lines.append(f"{proto} {port} already forwarded to {taken.client} ({taken.description})")
            continue
        try:
            result = runner(["upnpc", "-u", state.url, "-e", DESCRIPTION, "-a", local, str(port), str(port), proto, str(int(lease))])
            lines.extend(line for line in (result.stdout or "").splitlines() if "failed" in line.lower() or "error" in line.lower())
        except (OSError, subprocess.SubprocessError) as error:
            lines.append(str(error))
    after = read_state(state.url, state.model, runner=runner)
    present = {(item.external_port, item.protocol) for item in after.mappings if item.client == local and item.internal_port == item.external_port}
    opened = tuple(item for item in ports if item in present)
    failed = tuple(item for item in ports if item not in present)
    return Outcome(after, opened, failed, tuple(lines))


def close_ports(ports: Iterable[tuple[int, str]], *, runner: Runner | None = None, fetch: Fetcher | None = None, which: Callable[[str], str | None] | None = None) -> Outcome:
    """Remove the forwards Big Remote Play made for this computer, and nothing else."""
    runner = runner or _run
    fetch = fetch or _fetch
    which = which or shutil.which
    state, local = locate(runner=runner, fetch=fetch, which=which)
    if not state.found or not local:
        return Outcome(state)
    wanted = {(int(port), proto) for port, proto in ports}
    removed: list[tuple[int, str]] = []
    for item in state.mappings:
        if (item.external_port, item.protocol) in wanted and _ours(item, local):
            try:
                runner(["upnpc", "-u", state.url, "-d", str(item.external_port), item.protocol])
                removed.append((item.external_port, item.protocol))
            except (OSError, subprocess.SubprocessError):
                pass
    after = read_state(state.url, state.model, runner=runner)
    left = tuple((item.external_port, item.protocol) for item in after.mappings if _ours(item, local) and (item.external_port, item.protocol) in wanted)
    return Outcome(after, tuple(removed), left)


__all__ = ["DESCRIPTION", "LEASE_SECONDS", "Mapping", "Outcome", "RENEW_SECONDS", "RouterState", "close_ports", "locate", "open_ports", "parse_mappings", "wanted_ports"]
