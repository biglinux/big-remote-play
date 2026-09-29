"""ZeroTier Central REST client, for both Centrals that exist today.

* **Legacy Central** — ``https://api.zerotier.com/api/v1``, personal API access
  token, header ``Authorization: token <TOKEN>``.
* **New Central** (launched 2025-11; API v2 since 2026-04) —
  ``https://central.zerotier.com/api/v2``, service-account token, header
  ``Authorization: Bearer <TOKEN>``. Networks live inside network groups and
  several field names differ.

The flavor is detected from which API accepts the token, then remembered.
Neither API reports a member as "online"; :attr:`CentralMember.recently_seen`
is derived from the last-seen time and labelled accordingly in the UI.
Sources are listed in docs/private-network-architecture.md.
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import random
import time
from typing import Any

from .http import ApiErrorKind, ApiResult, JsonHttpClient, Transport, path_segment

LEGACY_BASE = "https://api.zerotier.com/api/v1"
CENTRAL_BASE = "https://central.zerotier.com/api/v2"
LEGACY_CONSOLE = "https://my.zerotier.com"
CENTRAL_CONSOLE = "https://central.zerotier.com"
RECENTLY_SEEN_SECONDS = 5 * 60

# Small /24 pools in ranges ZeroTier itself suggests for managed networks.
_POOL_CANDIDATES = tuple(f"10.147.{third}.0/24" for third in range(17, 21)) + tuple(f"10.{second}.{third}.0/24" for second in (241, 242, 243, 244) for third in (1, 7, 42, 77))


@dataclass(frozen=True)
class CentralNetwork:
    network_id: str
    name: str
    private: bool = True
    subnets: tuple[str, ...] = ()
    online_members: int | None = None
    authorized_members: int | None = None


@dataclass(frozen=True)
class CentralMember:
    node_id: str
    name: str
    authorized: bool
    ip_assignments: tuple[str, ...] = ()
    last_seen: float | None = None  # epoch seconds
    client_version: str = ""
    rejected: bool = False

    def recently_seen(self, now: float | None = None) -> bool:
        if self.last_seen is None:
            return False
        return (now if now is not None else time.time()) - self.last_seen <= RECENTLY_SEEN_SECONDS


def choose_subnet(in_use: list[str] | tuple[str, ...] = (), *, rng: random.Random | None = None) -> str:
    """A pool that does not overlap any network this computer already uses."""
    used = []
    for value in in_use:
        try:
            used.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            continue
    free = [candidate for candidate in _POOL_CANDIDATES if not any(ipaddress.ip_network(candidate).overlaps(net) for net in used)]
    return (rng or random.SystemRandom()).choice(free or list(_POOL_CANDIDATES))


def _pool(subnet: str) -> dict[str, str]:
    network = ipaddress.ip_network(subnet)
    hosts = list(network.hosts())
    return {"ipRangeStart": str(hosts[0]), "ipRangeEnd": str(hosts[-1])}


def _items(data: Any, key: str) -> list:
    """A JSON list, whether returned bare or wrapped as ``{key: [...]}``."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get(key), list):
        return data[key]
    return []


def _ms_to_seconds(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number / 1000.0 if number > 0 else None


def _iso_to_seconds(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    from datetime import datetime

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class ZeroTierCentral:
    def __init__(self, token: str, *, flavor: str = "", transport: Transport | None = None, timeout: float = 10.0) -> None:
        self._token = token
        self._transport = transport
        self._timeout = timeout
        self.flavor = flavor if flavor in {"legacy", "central"} else ""

    def _client(self, flavor: str) -> JsonHttpClient:
        if flavor == "central":
            return JsonHttpClient(CENTRAL_BASE, auth_headers=lambda: {"Authorization": f"Bearer {self._token}"}, transport=self._transport, timeout=self._timeout)
        return JsonHttpClient(LEGACY_BASE, auth_headers=lambda: {"Authorization": f"token {self._token}"}, transport=self._transport, timeout=self._timeout)

    @property
    def console_url(self) -> str:
        return CENTRAL_CONSOLE if self.flavor == "central" else LEGACY_CONSOLE

    def network_console_url(self, network_id: str) -> str:
        if self.flavor == "central":
            return CENTRAL_CONSOLE
        return f"{LEGACY_CONSOLE}/network/{path_segment(network_id)}"

    def detect(self) -> ApiResult:
        """Find which Central accepts the token; ``flavor`` is set on success."""
        legacy = self._client("legacy").get("/status")
        if legacy.ok and isinstance(legacy.data, dict) and legacy.data.get("user") is not None:
            self.flavor = "legacy"
            return legacy
        if legacy.error in (ApiErrorKind.TIMEOUT, ApiErrorKind.UNAVAILABLE):
            first_failure = legacy
        else:
            first_failure = None
        central = self._client("central").get("/org")
        if central.ok:
            self.flavor = "central"
            return central
        # Report the most informative failure: an auth error beats "not found".
        for result in (legacy, central):
            if result.error is ApiErrorKind.AUTH:
                return result
        return first_failure or central

    def _ensure(self) -> ApiResult | None:
        if self.flavor:
            return None
        result = self.detect()
        return None if result.ok else result

    # ── networks ───────────────────────────────────────────────────────────
    def list_networks(self) -> tuple[list[CentralNetwork], ApiResult]:
        failure = self._ensure()
        if failure is not None:
            return [], failure
        result = self._client(self.flavor).get("/network")
        if not result.ok:
            return [], result
        return [network for network in (self._network(item) for item in _items(result.data, "networks")) if network is not None], result

    def _network(self, item: Any) -> CentralNetwork | None:
        if not isinstance(item, dict):
            return None
        network_id = str(item.get("id") or "").lower()
        if len(network_id) != 16:
            return None
        raw_config = item.get("config")
        config: dict = raw_config if isinstance(raw_config, dict) else {}
        name = str(item.get("name") or config.get("name") or "")
        pools = config.get("v4IpAssignmentPools") or config.get("ipAssignmentPools") or []
        subnets = tuple(str(route.get("target")) for route in config.get("routes") or [] if isinstance(route, dict) and route.get("target"))
        if not subnets and pools:
            subnets = tuple(f"{pool.get('ipRangeStart')}–{pool.get('ipRangeEnd')}" for pool in pools if isinstance(pool, dict))
        return CentralNetwork(
            network_id=network_id,
            name=name,
            private=bool(config.get("private", True)),
            subnets=subnets,
            online_members=item.get("onlineMemberCount") if isinstance(item.get("onlineMemberCount"), int) else None,
            authorized_members=item.get("authorizedMemberCount") if isinstance(item.get("authorizedMemberCount"), int) else None,
        )

    def create_network(self, name: str, subnet: str) -> tuple[CentralNetwork | None, ApiResult]:
        """Create a private network with a managed IPv4 pool, ready to use.

        A network without an assignment pool gives members no address, which
        is why the pool and route are always sent.
        """
        failure = self._ensure()
        if failure is not None:
            return None, failure
        clean_name = "".join(character for character in name if character.isprintable()).strip()[:64] or "Big Remote Play"
        pool = _pool(subnet)
        route = [{"target": str(ipaddress.ip_network(subnet)), "via": None}]
        client = self._client(self.flavor)
        if self.flavor == "central":
            groups = client.get("/network-group")
            group_id = next((str(item.get("id")) for item in _items(groups.data, "networkGroups") if isinstance(item, dict) and item.get("id")), "")
            if not groups.ok or not group_id:
                return None, groups if not groups.ok else ApiResult.failure(ApiErrorKind.NOT_FOUND, "no network group")
            body = {"name": clean_name, "description": "Big Remote Play", "config": {"private": True, "v4IpAssignmentPools": [pool], "v4AssignmentMode": {"zt": True}, "routes": route}}
            created = client.post(f"/network-group/{path_segment(group_id)}/network", json_body=body)
            return (self._network(created.data) if created.ok else None), created
        created = client.post("/network", json_body={})
        network = self._network(created.data) if created.ok else None
        if network is None:
            return None, created if not created.ok else ApiResult.failure(ApiErrorKind.INVALID_RESPONSE, "no network id")
        body = {"description": "Big Remote Play", "config": {"name": clean_name, "private": True, "ipAssignmentPools": [pool], "routes": route, "v4AssignMode": {"zt": True}}}
        updated = client.post(f"/network/{path_segment(network.network_id)}", json_body=body)
        return (self._network(updated.data) or network) if updated.ok else network, updated

    def delete_network(self, network_id: str) -> ApiResult:
        failure = self._ensure()
        return failure or self._client(self.flavor).delete(f"/network/{path_segment(network_id)}")

    # ── members ────────────────────────────────────────────────────────────
    def list_members(self, network_id: str) -> tuple[list[CentralMember], ApiResult]:
        failure = self._ensure()
        if failure is not None:
            return [], failure
        result = self._client(self.flavor).get(f"/network/{path_segment(network_id)}/member")
        if not result.ok:
            return [], result
        members = [member for member in (self._member(item) for item in _items(result.data, "members")) if member is not None]
        members.sort(key=lambda member: (member.authorized, member.name.lower()))
        return members, result

    def _member(self, item: Any) -> CentralMember | None:
        if not isinstance(item, dict):
            return None
        node_id = str(item.get("nodeId") or item.get("deviceId") or "").lower()
        if len(node_id) != 10:
            return None
        if self.flavor == "central":
            status = str(item.get("status") or "")
            return CentralMember(
                node_id=node_id,
                name=str(item.get("name") or ""),
                authorized=status == "authorized",
                rejected=status == "rejected",
                ip_assignments=tuple(str(ip) for ip in item.get("ipv4Assignments") or [] if ip),
                last_seen=_iso_to_seconds(item.get("lastSeenTime")),
                client_version=str(item.get("agentVersion") or ""),
            )
        raw_config = item.get("config")
        config: dict = raw_config if isinstance(raw_config, dict) else {}
        return CentralMember(
            node_id=node_id,
            name=str(item.get("name") or ""),
            authorized=bool(config.get("authorized")),
            ip_assignments=tuple(str(ip) for ip in config.get("ipAssignments") or [] if ip),
            last_seen=_ms_to_seconds(item.get("lastSeen") or item.get("lastOnline")),
            client_version=str(item.get("clientVersion") or ""),
        )

    def set_authorized(self, network_id: str, node_id: str, authorized: bool) -> ApiResult:
        failure = self._ensure()
        if failure is not None:
            return failure
        client = self._client(self.flavor)
        base = f"/network/{path_segment(network_id)}/member/{path_segment(node_id)}"
        if self.flavor == "central":
            return client.post(base + ("/authorize" if authorized else "/de-authorize"))
        return client.post(base, json_body={"config": {"authorized": bool(authorized)}})

    def rename_member(self, network_id: str, node_id: str, name: str) -> ApiResult:
        failure = self._ensure()
        if failure is not None:
            return failure
        clean = "".join(character for character in name if character.isprintable()).strip()[:64]
        return self._client(self.flavor).post(f"/network/{path_segment(network_id)}/member/{path_segment(node_id)}", json_body={"name": clean})

    def remove_member(self, network_id: str, node_id: str) -> ApiResult:
        failure = self._ensure()
        if failure is not None:
            return failure
        client = self._client(self.flavor)
        if self.flavor == "central":
            return client.delete(f"/network/{path_segment(network_id)}/member", json_body=[{"deviceId": node_id}])
        return client.delete(f"/network/{path_segment(network_id)}/member/{path_segment(node_id)}")
