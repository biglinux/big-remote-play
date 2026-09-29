"""Joining a ZeroTier network: every answer the controller can give.

A scripted ZeroTier service stands in for zerotier-one: it answers the same
CLI protocol (exit codes, ``<status> <command>`` lines, JSON) and changes a
network's status over time, the way a controller does. No real service,
network or password prompt is involved.
"""

from __future__ import annotations

import json

import pytest

from big_remote_play.private_network.zerotier_join import (
    JoinPhase,
    ZeroTierJoiner,
    cli_status,
    phase_for_failed_command,
    phase_for_network,
    summarize_peers,
)
from big_remote_play.utils.vpn_accounts import CommandResult, VPNAccountManager, ZeroTierNetwork, ZeroTierNetworks, ZeroTierNode

NETWORK = "8056c2e21c000001"
NODE = "a1b2c3d4e5"


class FakeZeroTier:
    """zerotier-one as the account manager sees it.

    ``timeline`` is the status listnetworks reports on each successive read
    after the join; the last one repeats.
    """

    def __init__(self, timeline=("OK",), *, running=True, online=True, token=True, join_result=None, member=False):
        self.timeline = list(timeline)
        self.running = running
        self.online = online
        self.token = token
        self.join_result = join_result
        self.member = member
        self.calls: list[tuple] = []
        self.reads = 0

    # service probes
    def installed(self):
        return True

    def service_running(self):
        return self.running

    # VPNAccountManager surface
    def start_service(self, unit):
        self.calls.append(("start", unit))
        self.running = True
        return CommandResult(0)

    def zerotier_info(self):
        if not self.running:
            return ZeroTierNode("", error="Error connecting to the ZeroTier service: connection failed")
        if not self.token:
            return ZeroTierNode("", needs_privilege=True, error="401 info {}")
        return ZeroTierNode(NODE, online=self.online, version="1.16.2")

    def grant_zerotier_user_access(self):
        self.calls.append(("grant",))
        self.token = True
        return CommandResult(0)

    def _network(self):
        status = self.timeline[min(self.reads, len(self.timeline) - 1)]
        addresses = ("10.147.17.5/24",) if status == "OK" else ()
        return ZeroTierNetwork(NETWORK, "games", status, addresses, "ztabc123")

    def list_zerotier_networks(self, allow_privileged=False):
        if not self.token:
            return ZeroTierNetworks((), needs_privilege=True)
        if not self.member:
            return ZeroTierNetworks(())
        network = self._network()
        self.reads += 1
        return ZeroTierNetworks((network,))

    def join_zerotier_network(self, network_id, allow_privileged=True):
        self.calls.append(("join", network_id))
        if self.join_result is not None:
            return self.join_result
        self.member = True
        return CommandResult(0, json.dumps({"nwid": network_id, "status": "REQUESTING_CONFIGURATION", "assignedAddresses": []}))

    def leave_zerotier_network(self, network_id, allow_privileged=True):
        self.calls.append(("leave", network_id))
        self.member = False
        return CommandResult(0)

    def list_zerotier_peers(self):
        return [
            {"address": "1111111111", "role": "PLANET", "latency": 20, "paths": [{"active": True}]},
            {"address": "2222222222", "role": "LEAF", "latency": 18, "paths": [{"active": True, "expired": False}]},
            {"address": "3333333333", "role": "LEAF", "latency": -1, "paths": []},
        ]


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def joiner(service: FakeZeroTier):
    clock = Clock()
    return ZeroTierJoiner(service, installed=service.installed, service_running=service.service_running, sleep=clock.sleep, clock=clock)  # type: ignore[arg-type]


def run_join(service: FakeZeroTier, network=NETWORK, wait=45.0):
    seen: list[JoinPhase] = []
    result = joiner(service).join(network, report=lambda snapshot: seen.append(snapshot.phase), wait=wait)
    return result, seen


# ── join → final state ───────────────────────────────────────────────────


def test_join_ok_becomes_connected_with_its_address():
    result, seen = run_join(FakeZeroTier(["REQUESTING_CONFIGURATION", "OK"]))
    assert result.phase is JoinPhase.CONNECTED
    assert result.addresses == ("10.147.17.5",)
    assert result.node_id == NODE and result.device == "ztabc123"
    assert JoinPhase.WAITING_CONFIGURATION in seen  # progress was shown, not a failure
    assert JoinPhase.FAILED not in seen


def test_requesting_configuration_is_waiting_never_a_failure():
    result, seen = run_join(FakeZeroTier(["REQUESTING_CONFIGURATION"]), wait=6)
    assert result.phase is JoinPhase.WAITING_CONFIGURATION
    assert result.phase.waiting and not result.phase.problem
    assert all(not phase.problem for phase in seen)


def test_access_denied_is_waiting_for_authorization_and_keeps_the_membership():
    service = FakeZeroTier(["REQUESTING_CONFIGURATION", "ACCESS_DENIED"])
    result, _seen = run_join(service)
    assert result.phase is JoinPhase.WAITING_AUTHORIZATION
    assert result.node_id == NODE  # the owner needs this to approve the computer
    assert ("leave", NETWORK) not in service.calls


def test_later_authorization_is_detected_without_joining_again():
    service = FakeZeroTier(["ACCESS_DENIED"])
    first, _ = run_join(service)
    assert first.phase is JoinPhase.WAITING_AUTHORIZATION
    service.timeline = ["OK"]  # the owner approved it in ZeroTier Central
    later = joiner(service).check(NETWORK)
    assert later.phase is JoinPhase.CONNECTED
    assert [call for call in service.calls if call[0] == "join"] == [("join", NETWORK)]


def test_not_found_is_explained_and_a_mistyped_code_leaves_nothing_behind():
    service = FakeZeroTier(["REQUESTING_CONFIGURATION", "NOT_FOUND"])
    result, _ = run_join(service)
    assert result.phase is JoinPhase.NOT_FOUND and result.phase.problem
    assert ("leave", NETWORK) in service.calls


def test_not_found_on_a_network_already_joined_is_not_removed():
    service = FakeZeroTier(["NOT_FOUND"], member=True)
    result, _ = run_join(service)
    assert result.phase is JoinPhase.NOT_FOUND
    assert ("leave", NETWORK) not in service.calls  # someone else's decision


@pytest.mark.parametrize("status, phase", [("PORT_ERROR", JoinPhase.PORT_ERROR), ("CLIENT_TOO_OLD", JoinPhase.CLIENT_TOO_OLD), ("AUTHENTICATION_REQUIRED", JoinPhase.SIGN_IN_REQUIRED)])
def test_other_controller_answers_have_their_own_state(status, phase):
    result, _ = run_join(FakeZeroTier([status]))
    assert result.phase is phase


def test_authorized_without_an_address_is_not_called_connected():
    network = ZeroTierNetwork(NETWORK, "games", "OK", ())
    assert phase_for_network(network) is JoinPhase.WAITING_ADDRESS


def test_an_offline_node_is_told_apart_from_a_slow_controller():
    result, _ = run_join(FakeZeroTier(["REQUESTING_CONFIGURATION"], online=False), wait=3)
    assert result.phase is JoinPhase.NODE_OFFLINE


# ── the service and the command ──────────────────────────────────────────


def test_a_stopped_service_is_started_before_joining():
    """The bug seen for real: join with zerotier-one stopped → "0 join connection failed"."""
    service = FakeZeroTier(["OK"], running=False)
    result, seen = run_join(service)
    assert service.calls[0] == ("start", "zerotier-one")
    assert JoinPhase.STARTING_SERVICE in seen
    assert result.phase is JoinPhase.CONNECTED


def test_a_service_that_cannot_be_started_is_said_plainly():
    service = FakeZeroTier(running=False)
    service.start_service = lambda unit: CommandResult(126, "", "Not authorized")
    result, _ = run_join(service)
    assert result.phase is JoinPhase.SERVICE_STOPPED
    assert not any(call[0] == "join" for call in service.calls)


def test_the_cli_protocol_line_is_read_not_its_prose():
    assert cli_status("0 join connection failed") == (0, "join")
    assert cli_status("401 join {}") == (401, "join")
    assert cli_status("Erro qualquer traduzido") is None
    assert phase_for_failed_command(CommandResult(1, "0 join connection failed")) is JoinPhase.SERVICE_STOPPED
    assert phase_for_failed_command(CommandResult(1, "", "401 join {}")) is JoinPhase.NEEDS_PERMISSION
    assert phase_for_failed_command(CommandResult(127, "", "No such file")) is JoinPhase.NOT_INSTALLED
    assert phase_for_failed_command(CommandResult(1, "", "Falha na conexão")) is JoinPhase.FAILED


def test_a_join_refused_by_the_service_reports_the_real_error():
    service = FakeZeroTier(join_result=CommandResult(1, "", "500 join internal error"))
    result, _ = run_join(service)
    assert result.phase is JoinPhase.FAILED
    assert result.join_result == "500 join internal error"
    assert any(line.startswith("Join result: 500 join") for line in result.technical_lines())


def test_a_service_restarted_under_the_join_is_not_reported_as_a_failed_network():
    service = FakeZeroTier(join_result=CommandResult(1, "0 join connection failed"))
    result, _ = run_join(service)
    assert result.phase is JoinPhase.SERVICE_STOPPED


def test_missing_permission_is_asked_once_then_everything_runs_as_the_user():
    service = FakeZeroTier(["OK"], token=False)
    result, seen = run_join(service)
    assert ("grant",) in service.calls
    assert JoinPhase.ASKING_PERMISSION in seen
    assert result.phase is JoinPhase.CONNECTED


def test_refused_permission_stops_before_joining():
    service = FakeZeroTier(token=False)
    service.grant_zerotier_user_access = lambda: CommandResult(126, "", "could not read the ZeroTier service token")
    result, _ = run_join(service)
    assert result.phase is JoinPhase.NEEDS_PERMISSION
    assert not any(call[0] == "join" for call in service.calls)


@pytest.mark.parametrize("code", ["", "8056c2e21c00000", "8056c2e21c00000g", "--help-me-please"])
def test_an_incomplete_code_is_rejected_before_any_command(code):
    service = FakeZeroTier()
    result, _ = run_join(service, network=code)
    assert result.phase is JoinPhase.INVALID_ID
    assert service.calls == []


def test_a_copied_code_with_spaces_and_dashes_is_accepted():
    service = FakeZeroTier(["OK"])
    result, _ = run_join(service, network="8056 C2E2-1C00 0001")
    assert result.phase is JoinPhase.CONNECTED
    assert ("join", NETWORK) in service.calls


def test_check_reports_a_stopped_service_and_a_missing_membership():
    assert joiner(FakeZeroTier(running=False)).check(NETWORK).phase is JoinPhase.SERVICE_STOPPED
    assert joiner(FakeZeroTier()).check(NETWORK).phase is JoinPhase.NOT_JOINED


def test_cancelling_stops_following_the_controller():
    service = FakeZeroTier(["REQUESTING_CONFIGURATION"])
    answers = iter([False])  # joined, then the page was closed

    result = joiner(service).join(NETWORK, cancelled=lambda: next(answers, True))
    assert result.phase is JoinPhase.WAITING_CONFIGURATION  # what the join itself reported
    assert service.reads == 0  # the network was never polled after the join


# ── diagnostics ──────────────────────────────────────────────────────────


def test_peers_count_only_other_computers_and_say_how_they_are_reached():
    summary = summarize_peers(FakeZeroTier().list_zerotier_peers())
    assert (summary.total, summary.direct, summary.relayed, summary.best_latency_ms) == (2, 1, 1, 18)
    assert summarize_peers("junk").total == 0


def test_network_controllers_are_not_counted_as_devices():
    """Seen for real: with no device online, every LEAF peer was a controller."""
    peers = [{"address": NETWORK[:10], "role": "LEAF", "latency": 175, "paths": [{"active": True}]}]
    assert summarize_peers(peers, controllers=[NETWORK[:10]]).total == 0
    assert summarize_peers(peers).total == 1


def test_technical_details_are_useful_and_contain_no_secret():
    result, _ = run_join(FakeZeroTier(["OK"]))
    text = "\n".join(result.technical_lines())
    for expected in ("Provider: ZeroTier", f"Network: {NETWORK}", f"Node: {NODE}", "Daemon: ONLINE", "Network state: OK", "Interface: ztabc123", "Peers: 2 (1 direct, 1 relayed"):
        assert expected in text
    assert "token" not in text.lower()


def test_manager_joins_with_json_output_and_reads_peers(tmp_path):
    calls = []

    def runner(argv, timeout=15):
        calls.append(argv)
        if argv[-1] == "listpeers":
            return CommandResult(0, json.dumps([{"role": "LEAF", "paths": []}]))
        return CommandResult(0, json.dumps({"nwid": NETWORK, "status": "OK"}))

    manager = VPNAccountManager(type("Check", (), {"zerotier_cmd": staticmethod(lambda: ["zerotier-cli"])})(), runner=runner, metadata_file=tmp_path / "m.json")
    assert manager.join_zerotier_network(NETWORK).returncode == 0
    assert calls[0] == ["zerotier-cli", "-j", "join", NETWORK]
    assert manager.list_zerotier_peers() == [{"role": "LEAF", "paths": []}]
