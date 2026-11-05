"""Finding computers that share games on someone else's ZeroTier network.

Seen on a real game night: three computers on a friend's ZeroTier network
(a /16, no ZeroTier Central account) showed "Devices unavailable", and
Connect did not offer the game PC.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

from big_remote_play.private_network import device_list
from big_remote_play.private_network.cards import _zerotier_count
from big_remote_play.private_network.diagnostics import SunshineProbe
from big_remote_play.private_network.discovery import (
    FoundComputer,
    FoundStore,
    SearchResult,
    answer_for,
    find_computers,
    neighbours,
    parse_answer,
    search,
    search_area,
)
from big_remote_play.private_network.models import ConnectionState, OverlayNetwork, PeerDevice, ProviderId, ProviderStatus
from big_remote_play.private_network.service import PrivateNetworkService

NET_A = "8056c2e21c000001"
NET_B = "8056c2e21c000002"


def network(address: str, network_id: str = NET_A, ready: bool = True):
    return SimpleNamespace(network_id=network_id, display_name="Friends", device="ztabc123", assigned_addresses=(address,), ready=ready)


def test_a_small_network_is_searched_whole_with_its_real_prefix():
    area = search_area(network("10.147.20.7/23"))
    assert (area.network, area.searched, area.partial) == ("10.147.20.0/23", "10.147.20.0/23", False)
    addresses = area.addresses()
    assert len(addresses) == 509 and "10.147.20.7" not in addresses


def test_a_large_network_is_searched_near_this_computer_and_known_addresses_first():
    area = search_area(network("172.29.133.40/16"), first=["172.29.24.45", "8.8.8.8", "172.29.133.41"])
    assert area.partial and area.searched == "172.29.133.0/24"
    addresses = area.addresses()
    # A known computer outside the block is still asked; an address outside the network never is.
    assert addresses[:2] == ["172.29.24.45", "172.29.133.41"] and "8.8.8.8" not in addresses
    assert len(addresses) == 254  # 253 neighbours in the block, plus the known computer


def test_a_network_without_ipv4_cannot_be_searched():
    assert search_area(network("fd80:56c2:e21c::1/88")) is None


def _probe(answers: dict[str, SunshineProbe]):
    return lambda address: answers.get(address, SunshineProbe(False))


def test_only_addresses_where_sunshine_answers_are_found():
    area = search_area(network("10.0.0.1/29"))
    answers = {"10.0.0.3": SunshineProbe(True, answered=True, hostname="thiago-pc", uniqueid="ABC"), "10.0.0.5": SunshineProbe(True)}
    result = search(area, connect=lambda a: a in answers, probe=_probe(answers), workers=4)
    assert [(c.address, c.name, c.sharing) for c in result.found] == [("10.0.0.3", "thiago-pc", True), ("10.0.0.5", "", False)]
    assert result.asked == 5 and not result.cancelled


def test_a_cancelled_search_stops_and_says_so():
    area = search_area(network("10.0.0.1/24"))
    cancel = threading.Event()
    asked = []

    def connect(address):
        asked.append(address)
        if len(asked) == 3:
            cancel.set()
        return False

    result = search(area, cancel=cancel, connect=connect, probe=_probe({}), workers=1)
    assert result.cancelled and len(asked) < 10


def test_the_search_has_a_time_limit():
    area = search_area(network("10.0.0.1/24"))
    ticks = iter(range(1000))
    result = search(area, connect=lambda _a: False, probe=_probe({}), workers=1, deadline=2, clock=lambda: next(ticks))
    assert result.timed_out and not result.cancelled and result.asked < 253


def test_computers_that_answer_the_broadcast_question_are_asked_and_named():
    seen_areas = []

    def run_search(area, **_kwargs):
        seen_areas.append(area)
        return SearchResult(area, (FoundComputer("172.29.24.45", NET_A, "", "U1", True, 1.0),), asked=1)

    result = find_computers(network("172.29.133.40/16"), ask=lambda _area, cancel=None: {"172.29.24.45": "thiago"}, run_search=run_search, run=lambda *a, **k: SimpleNamespace(stdout="[]"))
    assert seen_areas[0].first[0] == "172.29.24.45"
    assert result.found[0].name == "thiago"


def test_a_network_waiting_for_approval_is_not_searched():
    assert find_computers(network("10.0.0.1/24", ready=False)).problem == "not_ready"


def test_the_answer_carries_only_name_and_port():
    assert parse_answer(answer_for("game\npc", 47989)) == (47989, "gamepc")
    assert parse_answer(b"BRP_SHARING 1 99999 x") is None
    assert parse_answer(b"I_HAVE_PIN x") is None


def test_computers_talked_to_before_are_asked_first():
    out = '[{"dst":"10.0.0.2","state":["REACHABLE"]},{"dst":"10.0.0.3","state":["FAILED"]}]'
    assert neighbours("ztabc123", run=lambda *a, **k: SimpleNamespace(stdout=out)) == ["10.0.0.2", "10.0.0.3"]
    assert neighbours("zt;rm -rf", run=lambda *a, **k: SimpleNamespace(stdout=out)) == []


# ── remembered per network ──────────────────────────────────────────────
def test_found_computers_are_remembered_per_network_and_forgotten_when_gone(tmp_path):
    store = FoundStore(tmp_path / "found.json", clock=lambda: 1000.0)
    area = search_area(network("10.0.0.1/24"))
    store.remember(SearchResult(area, (FoundComputer("10.0.0.3", NET_A, "pc", "U", True, 1000.0),)))
    assert [c.address for c in store.for_network(NET_A)] == ["10.0.0.3"] and store.for_network(NET_B) == []
    # A complete later search where it did not answer forgets it.
    store.remember(SearchResult(area, ()))
    assert store.for_network(NET_A) == []
    assert (tmp_path / "found.json").stat().st_mode & 0o777 == 0o600


def test_a_timed_out_or_cancelled_search_keeps_older_knowledge(tmp_path):
    store = FoundStore(tmp_path / "found.json", clock=lambda: 1000.0)
    area = search_area(network("10.0.0.1/24"))
    store.remember(SearchResult(area, (FoundComputer("10.0.0.3", NET_A, "pc", "U", True, 1000.0),)))
    store.remember(SearchResult(area, (), timed_out=True))
    store.remember(SearchResult(area, (), cancelled=True))
    assert len(store.for_network(NET_A)) == 1


def test_a_damaged_file_is_treated_as_nothing_found(tmp_path):
    path = tmp_path / "found.json"
    path.write_text('{"8056c2e21c000001": [{"address": "not an ip"}], "bad id": []}')
    assert FoundStore(path).load() == {}


# ── what the pages show ─────────────────────────────────────────────────
ZT = ProviderStatus(
    ProviderId.ZEROTIER,
    ConnectionState.CONNECTED,
    network_id=NET_A,
    self_device=PeerDevice("me", ("10.0.0.1",), provider="zerotier", is_self=True, node_id="aaaaaaaaaa"),
    networks=(OverlayNetwork(NET_A, "Friends", ConnectionState.CONNECTED, ("10.0.0.1",)), OverlayNetwork(NET_B, "Work", ConnectionState.CONNECTED, ("10.0.0.1",))),
)


def test_a_remembered_computer_is_not_shown_as_online():
    found = [FoundComputer("10.0.0.3", NET_A, "thiago", "U", True, seen=1000.0)]
    old = device_list.zerotier_listing(ZT, NET_A, found=found, now=1000.0 + 3600)
    fresh = device_list.zerotier_listing(ZT, NET_A, found=found, now=1000.0 + 10)
    assert [d.online for d in old.devices if d.found] == [None]
    assert [d.online for d in fresh.devices if d.found] == [True]
    assert _zerotier_count(fresh).found == 1 and _zerotier_count(fresh).problem == "api_missing"


def test_two_networks_never_mix_their_computers():
    found = [FoundComputer("10.0.0.3", NET_A, "thiago", "U", True, 1.0)]
    other = device_list.zerotier_listing(ZT, NET_B, found=found)
    assert [d.address for d in other.devices if d.found] == []


class _Manager:
    def list_zerotier_networks(self, allow_privileged=False):
        return SimpleNamespace(networks=())


def test_connect_offers_found_computers_only_of_networks_joined_now(tmp_path):
    store = FoundStore(tmp_path / "found.json")
    store._save({NET_A: [FoundComputer("10.0.0.3", NET_A, "thiago", "UID", True, 9e12)], "8056c2e21c00dead": [FoundComputer("10.9.0.9", "8056c2e21c00dead", "old", "", True, 9e12)]})
    service = PrivateNetworkService(manager=_Manager(), found_store=store)
    store.clock = lambda: 9e12
    candidates = service.candidate_hosts([ZT])
    assert [(c.address, c.provider, c.identity) for c in candidates] == [("10.0.0.3", "zerotier", "UID")]
    disconnected = ProviderStatus(ProviderId.ZEROTIER, ConnectionState.DISCONNECTED, networks=ZT.networks)
    assert service.candidate_hosts([disconnected]) == []
