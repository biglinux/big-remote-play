"""Is a domain ready for a Headscale server? Checked without ``dig``.

Lookups use DNS over HTTPS (JSON API) at two public resolvers, Cloudflare and
Google, the second used only when the first does not answer. Only the name
being checked is sent. Public resolvers are used on purpose: the local
resolver may cache an old answer for hours while the person is waiting for a
change they just made.

What can be checked:

* which nameservers a domain uses, and whether they are Cloudflare's
  (``*.ns.cloudflare.com``) or the two the person copied from Cloudflare;
* whether ``A``/``AAAA`` records point at the expected address;
* whether Cloudflare's proxy (the orange cloud) answers instead of the
  server: Headscale needs **DNS only** (grey cloud).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import ipaddress
import re

from .http import ApiResult, JsonHttpClient, Transport

DOH_RESOLVERS: tuple[tuple[str, str], ...] = (
    ("https://cloudflare-dns.com", "/dns-query"),
    ("https://dns.google", "/resolve"),
)
TYPE_CODES = {"A": 1, "NS": 2, "CNAME": 5, "SOA": 6, "AAAA": 28}
NOERROR, NXDOMAIN = 0, 3

# Cloudflare's published proxy ranges (https://www.cloudflare.com/ips/,
# unchanged since 2021, checked 2026-10-06). An A/AAAA answer inside them
# means the record is proxied (orange cloud).
CLOUDFLARE_RANGES = tuple(
    ipaddress.ip_network(net)
    for net in (
        "173.245.48.0/20",
        "103.21.244.0/22",
        "103.22.200.0/22",
        "103.31.4.0/22",
        "141.101.64.0/18",
        "108.162.192.0/18",
        "190.93.240.0/20",
        "188.114.96.0/20",
        "197.234.240.0/22",
        "198.41.128.0/17",
        "162.158.0.0/15",
        "104.16.0.0/13",
        "104.24.0.0/14",
        "172.64.0.0/13",
        "131.0.72.0/22",
        "2400:cb00::/32",
        "2606:4700::/32",
        "2803:f800::/32",
        "2405:b500::/32",
        "2405:8100::/32",
        "2a06:98c0::/29",
        "2c0f:f248::/32",
    )
)

_LABEL_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


def normalize_domain(value: str) -> str:
    """A host name typed by a person (``https://Headscale.Example.com/``) as
    ``headscale.example.com``; raises ``ValueError`` for anything else."""
    text = (value or "").strip().lower()
    if "://" in text:
        text = text.split("://", 1)[1]
    text = text.split("/", 1)[0].split("?", 1)[0].rstrip(".")
    if ":" in text:
        host, _sep, port = text.rpartition(":")
        if port.isdigit():
            text = host
    if not text or any(character.isspace() for character in text):
        raise ValueError("empty or spaced domain")
    try:
        ipaddress.ip_address(text.strip("[]"))
    except ValueError:
        pass
    else:
        raise ValueError("an address, not a domain")
    try:
        ascii_name = text.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ValueError("invalid domain") from error
    labels = ascii_name.split(".")
    if len(labels) < 2 or len(ascii_name) > 253 or not all(_LABEL_RE.match(label) for label in labels) or labels[-1].isdigit():
        raise ValueError("invalid domain")
    return ascii_name


def is_cloudflare_proxy(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in network for network in CLOUDFLARE_RANGES if network.version == ip.version)


def is_cloudflare_nameserver(name: str) -> bool:
    return name.rstrip(".").lower().endswith(".ns.cloudflare.com")


@dataclass(frozen=True)
class DnsAnswer:
    ok: bool  # a resolver answered (NOERROR or NXDOMAIN)
    status: int = -1  # DNS RCODE; -1 when no resolver answered
    records: tuple[str, ...] = ()  # data of the answers of the asked type
    authority_zone: str = ""  # the zone named in the SOA of the authority section

    @property
    def exists(self) -> bool:
        return self.ok and self.status == NOERROR


class DnsClient:
    """DNS over HTTPS with a fallback resolver."""

    def __init__(self, *, transport: Transport | None = None, resolvers: Iterable[tuple[str, str]] = DOH_RESOLVERS, timeout: float = 5.0) -> None:
        self._resolvers = tuple(resolvers)
        self._transport = transport
        self._timeout = timeout

    def query(self, name: str, rtype: str) -> DnsAnswer:
        code = TYPE_CODES[rtype]
        for origin, path in self._resolvers:
            client = JsonHttpClient(origin, transport=self._transport, timeout=self._timeout)
            # ``ct`` selects the JSON format at Cloudflare; Google ignores it.
            result = client.get(path, query={"name": name, "type": rtype, "ct": "application/dns-json"}, authenticated=False)
            answer = _parse(result, code)
            if answer is not None:
                return answer
        return DnsAnswer(False)


def _parse(result: ApiResult, code: int) -> DnsAnswer | None:
    if not result.ok or not isinstance(result.data, dict):
        return None
    status = result.data.get("Status")
    if not isinstance(status, int):
        return None
    records = tuple(
        str(item.get("data") or "").rstrip(".").lower() if code == TYPE_CODES["NS"] else str(item.get("data") or "")
        for item in result.data.get("Answer") or []
        if isinstance(item, dict) and item.get("type") == code and item.get("data")
    )
    zone = ""
    for item in result.data.get("Authority") or []:
        if isinstance(item, dict) and item.get("type") == TYPE_CODES["SOA"]:
            zone = str(item.get("name") or "").rstrip(".").lower()
            break
    if not zone:
        for item in result.data.get("Answer") or []:
            if isinstance(item, dict) and item.get("type") == TYPE_CODES["SOA"]:
                zone = str(item.get("name") or "").rstrip(".").lower()
                break
    return DnsAnswer(True, status, records, zone)


# ── nameservers ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NameserverCheck:
    state: str  # cloudflare | expected | other | missing | error
    zone: str = ""
    nameservers: tuple[str, ...] = ()


def zone_of(client: DnsClient, domain: str) -> str:
    """The registered zone a name belongs to (``headscale.me.dpdns.org`` → ``me.dpdns.org``)."""
    answer = client.query(domain, "SOA")
    if answer.ok and answer.authority_zone:
        return answer.authority_zone
    return domain


def check_nameservers(domain: str, *, expected: Iterable[str] = (), client: DnsClient | None = None) -> NameserverCheck:
    """Are the domain's nameservers Cloudflare's (or exactly the ones expected)?"""
    dns = client or DnsClient()
    zone = zone_of(dns, domain)
    answer = dns.query(zone, "NS")
    if not answer.ok:
        return NameserverCheck("error", zone)
    found = tuple(sorted(set(answer.records)))
    if not answer.exists or not found:
        return NameserverCheck("missing", zone)
    wanted = {name.rstrip(".").lower() for name in expected if name.strip()}
    if wanted:
        return NameserverCheck("expected" if wanted <= set(found) else "other", zone, found)
    return NameserverCheck("cloudflare" if all(is_cloudflare_nameserver(name) for name in found) else "other", zone, found)


def parse_nameservers(text: str) -> tuple[str, ...]:
    """Nameserver names pasted by a person: any separator, trailing dots removed."""
    names = []
    for part in re.split(r"[\s,;]+", text or ""):
        part = part.strip().rstrip(".").lower()
        if part:
            try:
                names.append(normalize_domain(part))
            except ValueError:
                continue
    return tuple(dict.fromkeys(names))


# ── records ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RecordCheck:
    state: str  # ready | proxied | wrong | missing | error
    found: tuple[str, ...] = ()


def check_record(name: str, expected: str, *, rtype: str = "A", client: DnsClient | None = None) -> RecordCheck:
    """Does ``name`` point at ``expected`` and only there?"""
    dns = client or DnsClient()
    answer = dns.query(name, rtype)
    if not answer.ok:
        return RecordCheck("error")
    found = tuple(answer.records)
    if not found:
        return RecordCheck("missing")
    try:
        want = ipaddress.ip_address(expected)
        addresses = {ipaddress.ip_address(item) for item in found}
    except ValueError:
        return RecordCheck("wrong", found)
    if want in addresses and len(addresses) == 1:
        return RecordCheck("ready", found)
    if any(is_cloudflare_proxy(item) for item in found) and not is_cloudflare_proxy(expected):
        return RecordCheck("proxied", found)
    return RecordCheck("wrong", found)


def resolve_addresses(name: str, *, client: DnsClient | None = None) -> tuple[str, ...]:
    """Every A and AAAA address of ``name`` (empty when it does not resolve)."""
    dns = client or DnsClient()
    found: list[str] = []
    for rtype in ("A", "AAAA"):
        answer = dns.query(name, rtype)
        found.extend(answer.records)
    return tuple(found)


__all__ = [
    "CLOUDFLARE_RANGES",
    "DnsAnswer",
    "DnsClient",
    "NameserverCheck",
    "RecordCheck",
    "check_nameservers",
    "check_record",
    "is_cloudflare_nameserver",
    "is_cloudflare_proxy",
    "normalize_domain",
    "parse_nameservers",
    "resolve_addresses",
    "zone_of",
]
