"""This computer's address on the internet, found without guessing.

Used by the Headscale setup when this computer will be the server: a domain
must point at the address the internet sees, never at a LAN address.

Order of sources, each with a short timeout:

1. STUN (RFC 5389 binding request) to two public STUN servers. Only a 20-byte
   request is sent; the answer is the address and port the server saw.
2. HTTPS to two "what is my address" services (Cloudflare's trace, ipify), as
   a fallback when UDP is blocked.

Every answer is checked with :mod:`ipaddress`: private, shared (CGNAT),
loopback, link-local, reserved and multicast addresses are never accepted as
public. Results are cached for a few minutes so a redraw never queries the
internet again.

CGNAT is reported only on evidence: this computer's own interface in
100.64.0.0/10, or the router (asked through UPnP, when ``upnpc`` exists)
reporting an internet address that is private, shared, or different from the
one the internet sees.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import ipaddress
import re
import secrets
import socket
import struct
import subprocess
import threading
import time

from .diagnostics import NetworkFacts, local_network_facts
from .http import _default_transport

STUN_SERVERS: tuple[tuple[str, int], ...] = (("stun.cloudflare.com", 3478), ("stun.l.google.com", 19302))
# Cloudflare's public resolver answers the trace over HTTPS with a valid
# certificate for its IP; 100.64.0.0/10 is the RFC 6598 shared address space.
HTTPS_IPV4: tuple[str, ...] = ("https://1.1.1.1/cdn-cgi/trace", "https://api.ipify.org?format=json")  # NOSONAR
HTTPS_IPV6: tuple[str, ...] = ("https://[2606:4700:4700::1111]/cdn-cgi/trace", "https://api6.ipify.org?format=json")  # NOSONAR
CACHE_SECONDS = 300.0
TIMEOUT = 2.5

_MAGIC_COOKIE = 0x2112A442
_BINDING_REQUEST = 0x0001
_BINDING_SUCCESS = 0x0101
_ATTR_MAPPED_ADDRESS = 0x0001
_ATTR_XOR_MAPPED_ADDRESS = 0x0020
_CGNAT = ipaddress.ip_network("100.64.0.0/10")  # NOSONAR
_UPNP_EXTERNAL_RE = re.compile(r"ExternalIPAddress\s*=\s*([0-9a-fA-F.:]+)")
_TRACE_IP_RE = re.compile(r"^ip=([0-9a-fA-F.:]+)\s*$", re.MULTILINE)
_JSON_IP_RE = re.compile(r'"ip"\s*:\s*"([0-9a-fA-F.:]+)"')


def is_public_address(value: str) -> bool:
    """An address another computer on the internet could use to reach this one."""
    try:
        address = ipaddress.ip_address((value or "").strip().split("%")[0])
    except ValueError:
        return False
    if address.is_multicast or address.is_unspecified or address.is_loopback or address.is_link_local:
        return False
    if isinstance(address, ipaddress.IPv4Address) and address in _CGNAT:
        return False
    return address.is_global


def address_kind(value: str) -> str:
    """``public``, ``private`` (LAN), ``shared`` (CGNAT 100.64/10) or ``invalid``."""
    try:
        address = ipaddress.ip_address((value or "").strip().split("%")[0])
    except ValueError:
        return "invalid"
    if isinstance(address, ipaddress.IPv4Address) and address in _CGNAT:
        return "shared"
    if is_public_address(value):
        return "public"
    return "private"


# ── STUN ───────────────────────────────────────────────────────────────────


def stun_request(transaction_id: bytes) -> bytes:
    """A Binding request with no attributes (20 bytes)."""
    if len(transaction_id) != 12:
        raise ValueError("transaction id must be 12 bytes")
    return struct.pack("!HHI", _BINDING_REQUEST, 0, _MAGIC_COOKIE) + transaction_id


def parse_stun_response(data: bytes, transaction_id: bytes) -> str:
    """The mapped address in a Binding success response, ``""`` otherwise."""
    if len(data) < 20:
        return ""
    message_type, length, cookie = struct.unpack("!HHI", data[:8])
    if message_type != _BINDING_SUCCESS or cookie != _MAGIC_COOKIE or data[8:20] != transaction_id:
        return ""
    body = data[20 : 20 + length]
    mapped = ""
    offset = 0
    while offset + 4 <= len(body):
        attr_type, attr_length = struct.unpack("!HH", body[offset : offset + 4])
        value = body[offset + 4 : offset + 4 + attr_length]
        offset += 4 + attr_length + (-attr_length % 4)
        if len(value) < 4 or attr_type not in (_ATTR_XOR_MAPPED_ADDRESS, _ATTR_MAPPED_ADDRESS):
            continue
        family = value[1]
        raw = value[4:]
        if attr_type == _ATTR_XOR_MAPPED_ADDRESS:
            key = struct.pack("!I", _MAGIC_COOKIE) + transaction_id
            raw = bytes(byte ^ key[index] for index, byte in enumerate(raw))
        try:
            if family == 0x01 and len(raw) >= 4:
                text = str(ipaddress.IPv4Address(raw[:4]))
            elif family == 0x02 and len(raw) >= 16:
                text = str(ipaddress.IPv6Address(raw[:16]))
            else:
                continue
        except ValueError:
            continue
        if attr_type == _ATTR_XOR_MAPPED_ADDRESS:
            return text
        mapped = mapped or text
    return mapped


def stun_lookup(server: tuple[str, int], family: int, *, timeout: float = TIMEOUT) -> str:
    """Ask one STUN server which address it sees; ``""`` on any failure."""
    host, port = server
    try:
        infos = socket.getaddrinfo(host, port, family, socket.SOCK_DGRAM)
    except OSError:
        return ""
    for info in infos[:2]:
        transaction_id = secrets.token_bytes(12)
        try:
            with socket.socket(info[0], socket.SOCK_DGRAM) as sock:
                sock.settimeout(timeout)
                sock.sendto(stun_request(transaction_id), info[4])
                data, _sender = sock.recvfrom(2048)
        except OSError:
            continue
        found = parse_stun_response(data, transaction_id)
        if found:
            return found
    return ""


# ── HTTPS fallback ─────────────────────────────────────────────────────────


def parse_ip_answer(body: str) -> str:
    """The address in a Cloudflare trace (``ip=…``) or an ipify JSON answer."""
    match = _TRACE_IP_RE.search(body or "") or _JSON_IP_RE.search(body or "")
    return match.group(1) if match else ""


def https_lookup(url: str, *, transport=None, timeout: float = TIMEOUT) -> str:
    send = transport or _default_transport
    try:
        status, payload = send("GET", url, {"User-Agent": "BigRemotePlay", "Accept": "text/plain, application/json"}, None, timeout)
    except (OSError, ValueError):
        return ""
    if not 200 <= status < 300 or len(payload) > 4096:
        return ""
    return parse_ip_answer(payload.decode("utf-8", errors="replace"))


# ── the report ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PublicAddressReport:
    ipv4: str = ""
    ipv6: str = ""
    ipv4_source: str = ""  # stun | https | ""
    ipv6_source: str = ""
    local_ipv4: tuple[str, ...] = ()
    local_ipv6: tuple[str, ...] = ()
    router_external: str = ""  # what the router says through UPnP, "" when unknown
    cgnat: bool | None = None  # None: no evidence either way
    cgnat_reason: str = ""  # local_shared | router_private | router_differs
    rejected: tuple[str, ...] = field(default_factory=tuple)  # answers that were not public

    @property
    def ipv6_on_this_computer(self) -> bool:
        """The internet sees an IPv6 address this computer really has (no IPv6 NAT)."""
        return bool(self.ipv6) and self.ipv6 in self.local_ipv6

    @property
    def found(self) -> bool:
        return bool(self.ipv4 or self.ipv6)


def router_external_address(runner: Callable[..., subprocess.CompletedProcess] = subprocess.run, *, which: Callable[[str], str | None] | None = None) -> str:
    """The internet address the router reports through UPnP, ``""`` when it cannot tell."""
    import shutil

    command = (which or shutil.which)("upnpc")
    if not command:
        return ""
    try:
        result = runner([command, "-s"], capture_output=True, text=True, timeout=8, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    match = _UPNP_EXTERNAL_RE.search(result.stdout or "")
    if not match:
        return ""
    try:
        return str(ipaddress.ip_address(match.group(1)))
    except ValueError:
        return ""


def assess_cgnat(public_ipv4: str, facts: NetworkFacts, router_external: str) -> tuple[bool | None, str]:
    """``(suspected, reason)`` from evidence only; ``(None, "")`` when nothing is known."""
    if facts.cgnat_suspected:
        return True, "local_shared"
    if router_external:
        if address_kind(router_external) != "public":
            return True, "router_private"
        if public_ipv4 and router_external != public_ipv4:
            return True, "router_differs"
        if public_ipv4 and router_external == public_ipv4:
            return False, ""
    return None, ""


def _first_public(candidates: Sequence[Callable[[], str]]) -> tuple[str, str, list[str]]:
    rejected: list[str] = []
    for index, lookup in enumerate(candidates):
        value = lookup()
        if not value:
            continue
        if is_public_address(value):
            return value, ("stun" if index < len(STUN_SERVERS) else "https"), rejected
        rejected.append(value)
    return "", "", rejected


def detect_public_addresses(
    *,
    stun: Callable[[tuple[str, int], int], str] | None = None,
    https: Callable[[str], str] | None = None,
    facts: Callable[[], NetworkFacts] = local_network_facts,
    router: Callable[[], str] = router_external_address,
) -> PublicAddressReport:
    """Ask the internet which address it sees (blocking; run on a worker)."""
    stun_call = stun or (lambda server, family: stun_lookup(server, family))
    https_call = https or (lambda url: https_lookup(url))
    v4 = [lambda server=server: stun_call(server, socket.AF_INET) for server in STUN_SERVERS] + [lambda url=url: https_call(url) for url in HTTPS_IPV4]
    v6 = [lambda server=server: stun_call(server, socket.AF_INET6) for server in STUN_SERVERS] + [lambda url=url: https_call(url) for url in HTTPS_IPV6]
    ipv4, source4, rejected4 = _first_public(v4)
    ipv6, source6, rejected6 = _first_public(v6)
    if ipv6 and ":" not in ipv6:
        ipv6, source6 = "", ""
    if ipv4 and ":" in ipv4:
        ipv4, source4 = "", ""
    local = facts()
    external = router()
    cgnat, reason = assess_cgnat(ipv4, local, external)
    return PublicAddressReport(
        ipv4=ipv4,
        ipv6=ipv6,
        ipv4_source=source4,
        ipv6_source=source6,
        local_ipv4=local.ipv4,
        local_ipv6=local.ipv6_global,
        router_external=external,
        cgnat=cgnat,
        cgnat_reason=reason,
        rejected=tuple(rejected4 + rejected6),
    )


_cache_lock = threading.Lock()
_cache: tuple[float, PublicAddressReport] | None = None


def cached_public_addresses(*, force: bool = False, clock: Callable[[], float] = time.monotonic, detect: Callable[[], PublicAddressReport] | None = None) -> PublicAddressReport:
    """:func:`detect_public_addresses`, remembered for :data:`CACHE_SECONDS`."""
    global _cache
    with _cache_lock:
        if not force and _cache is not None and clock() - _cache[0] < CACHE_SECONDS:
            return _cache[1]
    report = (detect or detect_public_addresses)()
    with _cache_lock:
        _cache = (clock(), report)
    return report


def clear_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = None


__all__ = [
    "PublicAddressReport",
    "address_kind",
    "assess_cgnat",
    "cached_public_addresses",
    "clear_cache",
    "detect_public_addresses",
    "is_public_address",
    "parse_ip_answer",
    "parse_stun_response",
    "router_external_address",
    "stun_request",
]
