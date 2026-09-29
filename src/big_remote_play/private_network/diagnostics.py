"""Bounded, read-only checks used to explain why a connection can or cannot work.

Nothing in this module changes the system: no firewall rule, route, service or
router mapping is touched. Every network operation has a short timeout.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import http.client
import ipaddress
import json
import re
import socket
import subprocess

from big_remote_play.integration_contracts import SUNSHINE_DEFAULT_BASE_PORT

# Project heuristic for the latency label, documented in
# docs/private-network-architecture.md. The measured value is always shown too.
LATENCY_EXCELLENT_MS = 30.0
LATENCY_GOOD_MS = 60.0

_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_OVERLAY_PREFIXES = ("tailscale", "zt", "ztr")
_VIRTUAL_PREFIXES = ("lo", "docker", "veth", "br-", "virbr", "vnet", "vmnet", "vboxnet", "podman", "cni", "flannel")
_HOSTNAME_RE = re.compile(r"<hostname>([^<]{1,253})</hostname>")

Runner = Callable[..., subprocess.CompletedProcess]


def classify_latency(milliseconds: float | None) -> str:
    """``excellent``, ``good``, ``high`` or ``unknown``."""
    if milliseconds is None or milliseconds < 0:
        return "unknown"
    if milliseconds <= LATENCY_EXCELLENT_MS:
        return "excellent"
    if milliseconds <= LATENCY_GOOD_MS:
        return "good"
    return "high"


def valid_host(address: str) -> bool:
    """A literal IP or a DNS name, never an option, URL or path."""
    text = (address or "").strip().strip("[]")
    if not text or len(text) > 253 or text.startswith("-"):
        return False
    try:
        ipaddress.ip_address(text.split("%")[0])
        return True
    except ValueError:
        pass
    return re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,62})(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}))*\.?", text) is not None


def probe_tcp(address: str, port: int, *, timeout: float = 1.5) -> bool:
    if not valid_host(address) or not 1 <= int(port) <= 65_535:
        return False
    try:
        with socket.create_connection((address.strip("[]"), int(port)), timeout=timeout):
            return True
    except OSError:
        return False


@dataclass(frozen=True)
class SunshineProbe:
    listening: bool
    answered: bool = False
    hostname: str = ""


def probe_sunshine(address: str, port: int = SUNSHINE_DEFAULT_BASE_PORT, *, timeout: float = 2.0) -> SunshineProbe:
    """Ask the GameStream HTTP port for ``/serverinfo``.

    Sunshine answers this unauthenticated request with an XML document even
    before a client is paired, which distinguishes "Sunshine is sharing" from
    "some program listens on this port" and from "nothing answers".
    """
    if not valid_host(address):
        return SunshineProbe(False)
    host = address.strip().strip("[]")
    connection = http.client.HTTPConnection(host, int(port), timeout=timeout)
    try:
        connection.request("GET", "/serverinfo", headers={"User-Agent": "BigRemotePlay"})
        response = connection.getresponse()
        body = response.read(64 * 1024).decode("utf-8", errors="replace")
    except (OSError, http.client.HTTPException):
        return SunshineProbe(probe_tcp(address, port, timeout=timeout))
    finally:
        connection.close()
    match = _HOSTNAME_RE.search(body)
    answered = "<root" in body or match is not None
    return SunshineProbe(True, answered=answered, hostname=match.group(1).strip() if match else "")


@dataclass(frozen=True)
class NetworkFacts:
    """What this computer's own interfaces reveal. No external lookup."""

    ipv4: tuple[str, ...] = ()
    ipv6_global: tuple[str, ...] = ()
    overlay_interfaces: tuple[str, ...] = ()
    cgnat_suspected: bool = False

    @property
    def stack(self) -> str:
        """``dual``, ``ipv4``, ``ipv6`` or ``none``."""
        if self.ipv4 and self.ipv6_global:
            return "dual"
        if self.ipv4:
            return "ipv4"
        if self.ipv6_global:
            return "ipv6"
        return "none"


def parse_ip_json(payload: str) -> NetworkFacts:
    """Interpret ``ip -j addr`` output."""
    try:
        interfaces = json.loads(payload or "[]")
    except ValueError:
        return NetworkFacts()
    ipv4: list[str] = []
    ipv6: list[str] = []
    overlay: list[str] = []
    cgnat = False
    for interface in interfaces if isinstance(interfaces, list) else []:
        if not isinstance(interface, dict):
            continue
        name = str(interface.get("ifname") or "")
        flags = interface.get("flags") or []
        if name.startswith(_OVERLAY_PREFIXES):
            overlay.append(name)
            continue
        if name.startswith(_VIRTUAL_PREFIXES) or "UP" not in flags:
            continue
        for info in interface.get("addr_info") or []:
            if not isinstance(info, dict):
                continue
            local = str(info.get("local") or "")
            try:
                address = ipaddress.ip_address(local)
            except ValueError:
                continue
            if isinstance(address, ipaddress.IPv4Address):
                ipv4.append(local)
                # The carrier-grade NAT range on a physical interface means this
                # computer itself sits behind the provider's NAT (common on
                # mobile and some fibre links): inbound port forwarding cannot work.
                if address in _CGNAT:
                    cgnat = True
            elif info.get("scope") == "global" and address.is_global:
                ipv6.append(local)
    return NetworkFacts(tuple(ipv4), tuple(ipv6), tuple(overlay), cgnat)


def local_network_facts(runner: Runner = subprocess.run) -> NetworkFacts:
    try:
        result = runner(["ip", "-j", "addr"], capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.SubprocessError):
        return NetworkFacts()
    return parse_ip_json(result.stdout if result.returncode == 0 else "")


def is_cgnat_address(address: str) -> bool:
    try:
        return ipaddress.ip_address(address) in _CGNAT
    except ValueError:
        return False


def active_firewall(runner: Runner = subprocess.run, units: Sequence[str] = ("firewalld", "ufw")) -> str:
    """Name of an active firewall service, ``""`` when none is detected.

    nftables/iptables rules need root to read, so they are not inspected; the
    answer is limited to the two managers whose state a user can query.
    """
    for unit in units:
        try:
            if runner(["systemctl", "is-active", "--quiet", unit], timeout=3, check=False).returncode == 0:
                return unit
        except (OSError, subprocess.SubprocessError):
            continue
    return ""
