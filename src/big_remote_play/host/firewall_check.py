"""Does this computer's firewall let other computers reach Sunshine? (read-only)

Nothing here changes the firewall or asks for a password. The answer comes
from what the firewall itself stores or reports to an ordinary user:

* **ufw** (BigLinux, Manjaro, Ubuntu): ``/etc/ufw/ufw.conf`` says whether it
  is enabled, ``/etc/default/ufw`` gives the incoming policy, and
  ``/etc/ufw/user.rules`` holds one ``### tuple ###`` line per rule, in the
  order ufw applies them (the first match decides). These files are
  world-readable on those systems.
* **firewalld**: ``firewall-cmd`` answers zone queries without privileges.
* anything else (plain nftables/iptables rules need root to read) is
  reported as unknown, never as blocking.

Each network the computer is on — the local network, ZeroTier, Tailscale —
is checked separately, because an interface can have rules of its own.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
import ipaddress
import json
from pathlib import Path
import re
import subprocess

from big_remote_play.integration_contracts import SUNSHINE_DEFAULT_BASE_PORT, sunshine_stream_ports

Runner = Callable[..., subprocess.CompletedProcess]

UFW_CONF = Path("/etc/ufw/ufw.conf")
UFW_DEFAULTS = Path("/etc/default/ufw")
UFW_RULES = Path("/etc/ufw/user.rules")

_ALL = ipaddress.ip_network("0.0.0.0/0")
_DIRECTION = re.compile(r"^in(?:_(\S+))?$")


@dataclass(frozen=True)
class Network:
    """One network this computer is on: its kind, interface and IPv4 network."""

    kind: str  # "local" | "zerotier" | "tailscale"
    interface: str
    network: ipaddress.IPv4Network


@dataclass(frozen=True)
class FirewallReport:
    tool: str  # "ufw" | "firewalld" | "none" | "unknown"
    active: bool = False
    # Blocked "port/proto" per network kind, e.g. {"zerotier": ("47989/tcp", …)}.
    blocked: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def blocks(self) -> bool:
        return self.active and any(self.blocked.values())

    @property
    def blocked_ports(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for ports in self.blocked.values():
            seen.update(dict.fromkeys(ports))
        return tuple(seen)


def required_ports(base_port: int = SUNSHINE_DEFAULT_BASE_PORT) -> list[tuple[int, str]]:
    ports = sunshine_stream_ports(base_port)
    return [(port, "tcp") for port in ports["tcp"]] + [(port, "udp") for port in ports["udp"]]


def networks_from_ip_json(payload: str) -> list[Network]:
    """The networks other computers can come from, from ``ip -j -4 addr``."""
    try:
        interfaces = json.loads(payload or "[]")
    except ValueError:
        return []
    found: list[Network] = []
    for item in interfaces if isinstance(interfaces, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("ifname") or "")
        if not name or name == "lo" or name.startswith(("docker", "br-", "veth", "virbr", "vnet", "podman", "cni", "flannel", "vmnet", "vboxnet")):
            continue
        kind = "zerotier" if name.startswith("zt") else "tailscale" if name.startswith("tailscale") else "local"
        for address in item.get("addr_info") or []:
            if not isinstance(address, dict) or address.get("family") not in (None, "inet"):
                continue
            try:
                network = ipaddress.ip_interface(f"{address.get('local')}/{address.get('prefixlen')}").network
            except ValueError:
                continue
            if isinstance(network, ipaddress.IPv4Network):
                found.append(Network(kind, name, network))
    return found


# ── ufw ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class UfwRule:
    action: str  # allow | deny | reject | limit
    proto: str  # tcp | udp | any
    ports: tuple[tuple[int, int], ...] | None  # None = any port
    source: ipaddress.IPv4Network | ipaddress.IPv6Network
    interface: str  # "" = every interface

    def matches(self, port: int, proto: str, network: Network) -> bool:
        if self.proto not in ("any", proto):
            return False
        if self.ports is not None and not any(low <= port <= high for low, high in self.ports):
            return False
        if self.interface and self.interface != network.interface:
            return False
        return self.source.version == 4 and self.source.overlaps(network.network)


def _ports(text: str) -> tuple[tuple[int, int], ...] | None:
    if text == "any":
        return None
    ranges = []
    for part in text.split(","):
        low, _, high = part.partition(":")
        try:
            ranges.append((int(low), int(high or low)))
        except ValueError:
            continue
    return tuple(ranges)


def parse_ufw_rules(text: str) -> list[UfwRule]:
    """ufw's own record of its rules: ``### tuple ### action proto dport dst sport src [apps] direction``."""
    rules: list[UfwRule] = []
    for line in (text or "").splitlines():
        if not line.startswith("### tuple ###"):
            continue
        tokens = [token for token in line[len("### tuple ###") :].split() if not token.startswith("comment=")]
        if len(tokens) < 7:
            continue
        action, proto, dport, _dst, _sport, src = tokens[:6]
        direction = next((_DIRECTION.match(token) for token in reversed(tokens[6:]) if _DIRECTION.match(token)), None)
        if direction is None:
            continue  # outgoing or routed
        try:
            source = ipaddress.ip_network(src, strict=False)
        except ValueError:
            continue
        rules.append(UfwRule(action.split("_")[0], proto, _ports(dport), source, direction.group(1) or ""))
    return rules


def _setting(text: str, key: str) -> str:
    for line in (text or "").splitlines():
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.strip().strip("\"'")
    return ""


def ufw_blocked(rules: Iterable[UfwRule], policy: str, networks: Iterable[Network], ports: Iterable[tuple[int, str]]) -> dict[str, tuple[str, ...]]:
    """First matching rule decides, as in ufw; without one, the incoming policy."""
    rules = list(rules)
    ports = list(ports)
    blocked: dict[str, list[str]] = {}
    for network in networks:
        for port, proto in ports:
            rule = next((rule for rule in rules if rule.matches(port, proto, network)), None)
            allowed = rule.action in ("allow", "limit") if rule is not None else policy.upper() == "ACCEPT"
            if not allowed:
                label = f"{port}/{proto}"
                entries = blocked.setdefault(network.kind, [])
                if label not in entries:
                    entries.append(label)
    return {kind: tuple(entries) for kind, entries in blocked.items()}


# ── firewalld ───────────────────────────────────────────────────────────────


def _firewalld_zone_allows(zone_ports: str, target: str, port: int, proto: str) -> bool:
    if target.upper() == "ACCEPT":
        return True
    for item in zone_ports.split():
        span, _, item_proto = item.partition("/")
        low, _, high = span.partition("-")
        try:
            if item_proto == proto and int(low) <= port <= int(high or low):
                return True
        except ValueError:
            continue
    return False


# ── the check ───────────────────────────────────────────────────────────────


def _run(runner: Runner, argv: list[str]) -> subprocess.CompletedProcess | None:
    try:
        return runner(argv, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def check_firewall(
    base_port: int = SUNSHINE_DEFAULT_BASE_PORT,
    *,
    runner: Runner = subprocess.run,
    read: Callable[[Path], str | None] = _read,
) -> FirewallReport:
    ports = required_ports(base_port)
    result = _run(runner, ["ip", "-j", "-4", "addr"])
    networks = networks_from_ip_json(result.stdout if result is not None and result.returncode == 0 else "")

    # firewall-cmd waits ~10 s for a firewalld that is not running; ask systemd first.
    firewalld_unit = _run(runner, ["systemctl", "is-active", "--quiet", "firewalld"])
    firewalld = _run(runner, ["firewall-cmd", "--state"]) if firewalld_unit is not None and firewalld_unit.returncode == 0 else None
    if firewalld is not None and firewalld.returncode == 0 and firewalld.stdout.strip() == "running":
        default = _run(runner, ["firewall-cmd", "--get-default-zone"])
        default_zone = default.stdout.strip() if default is not None and default.returncode == 0 else ""
        blocked: dict[str, list[str]] = {}
        for network in networks:
            zone_result = _run(runner, ["firewall-cmd", f"--get-zone-of-interface={network.interface}"])
            zone = zone_result.stdout.strip() if zone_result is not None and zone_result.returncode == 0 else default_zone
            if not zone:
                return FirewallReport("unknown", True)
            listing = _run(runner, ["firewall-cmd", f"--zone={zone}", "--list-ports"])
            target = _run(runner, ["firewall-cmd", "--permanent", f"--zone={zone}", "--get-target"])
            if listing is None or listing.returncode != 0:
                return FirewallReport("unknown", True)
            zone_target = target.stdout.strip() if target is not None and target.returncode == 0 else ("ACCEPT" if zone == "trusted" else "")
            for port, proto in ports:
                if not _firewalld_zone_allows(listing.stdout, zone_target, port, proto):
                    entries = blocked.setdefault(network.kind, [])
                    if f"{port}/{proto}" not in entries:
                        entries.append(f"{port}/{proto}")
        return FirewallReport("firewalld", True, {kind: tuple(entries) for kind, entries in blocked.items()})

    conf = read(UFW_CONF)
    if conf is not None:
        if _setting(conf, "ENABLED").lower() != "yes":
            return FirewallReport("ufw", False)
        service = _run(runner, ["systemctl", "is-active", "--quiet", "ufw"])
        if service is not None and service.returncode != 0:
            return FirewallReport("ufw", False)
        rules_text = read(UFW_RULES)
        defaults = read(UFW_DEFAULTS)
        if rules_text is None or defaults is None:
            return FirewallReport("unknown", True)
        policy = _setting(defaults, "DEFAULT_INPUT_POLICY") or "DROP"
        return FirewallReport("ufw", True, ufw_blocked(parse_ufw_rules(rules_text), policy, networks, ports))

    return FirewallReport("unknown" if firewalld is not None else "none")
