"""Firewall, abandoned pairings and stale Moonlight entries (no real network).

Seen for real on 2026-09-29 between two BigLinux computers on ZeroTier: the
game PC's firewall dropped Sunshine's ports ("Could not connect"); the pairing
cut by it stayed waiting in Sunshine and refused the next attempt; and the
connecting Moonlight kept an entry without a certificate, taken for a pairing.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import subprocess
from pathlib import Path

import pytest

from big_remote_play.host import firewall_check as fc
from big_remote_play.host.sunshine_manager import SunshineHost
from big_remote_play.host.sunshine_sessions import established_peers
from big_remote_play.private_network import diagnostics
from big_remote_play.utils.moonlight_config import paired_host_certificate

PAIRING_A = "a" * 32
PAIRING_B = "b" * 32
UUID = "EBCEDF6F-B0B6-940A-7B14-430104AE696E"

# ── Moonlight's record of a host ───────────────────────────────────────────


def moonlight_conf(tmp_path: Path, srvcert: str, uuid: str = UUID) -> list[Path]:
    conf = tmp_path / "Moonlight.conf"
    conf.write_text(f"[hosts]\n1\\customname=false\n1\\hostname=game-pc\n1\\manualaddress=10.147.17.5\n1\\srvcert={srvcert}\n1\\uuid={uuid}\nsize=1\n")
    return [conf]


def test_a_host_moonlight_only_contacted_is_not_paired(tmp_path):
    # What Moonlight writes after listing a host, or after a failed pairing.
    assert paired_host_certificate("10.147.17.5", moonlight_conf(tmp_path, "@ByteArray()")) == ""


def test_a_paired_host_is_recognised_by_address_and_identity(tmp_path):
    paths = moonlight_conf(tmp_path, "@ByteArray(-----BEGIN CERTIFICATE-----\\nMIIC\\n-----END CERTIFICATE-----\\n)")
    assert paired_host_certificate("10.147.17.5", paths)
    assert paired_host_certificate("10.147.17.5", paths, uuid=UUID.lower())


def test_another_host_that_once_had_this_address_is_not_a_pairing(tmp_path):
    paths = moonlight_conf(tmp_path, "@ByteArray(-----BEGIN CERTIFICATE-----\\nMIIC\\n-----END CERTIFICATE-----\\n)")
    assert paired_host_certificate("10.147.17.5", paths, uuid="11111111-2222-3333-4444-555555555555") == ""


# ── how a connection attempt ended ─────────────────────────────────────────


def test_a_closed_port_is_refused_not_filtered():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert diagnostics.tcp_outcome("127.0.0.1", port) == "refused"


@pytest.mark.parametrize(
    "error,outcome",
    [(TimeoutError(), "timeout"), (OSError(113, "No route to host"), "unreachable"), (ConnectionRefusedError(), "refused"), (OSError(99, "other"), "error")],
)
def test_connection_outcomes_come_from_the_kernel_answer(monkeypatch, error, outcome):
    def connect(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(diagnostics.socket, "create_connection", connect)
    assert diagnostics.tcp_outcome("10.147.17.5", 47989) == outcome


def test_the_sunshine_probe_reads_the_host_identity(monkeypatch):
    body = f'<?xml version="1.0"?><root status_code="200"><hostname>game-pc</hostname><uniqueid>{UUID}</uniqueid><PairStatus>0</PairStatus></root>'.encode()

    class Response:
        def read(self, _size):
            return body

    class Connection:
        def __init__(self, *_args, **_kwargs):
            pass

        def request(self, *_args, **_kwargs):
            pass

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(diagnostics.http.client, "HTTPConnection", Connection)
    probe = diagnostics.probe_sunshine("10.147.17.5")
    assert probe.answered and probe.hostname == "game-pc" and probe.uniqueid == UUID


def test_the_connect_message_names_the_real_cause():
    from big_remote_play.ui.guest_view import unreachable_message

    off = unreachable_message([("10.147.17.5", "refused")], 47989, ping=lambda _address: None)
    firewall = unreachable_message([("10.147.17.5", "timeout")], 47989, ping=lambda _address: 12.0)
    away = unreachable_message([("10.147.17.5", "timeout")], 47989, ping=lambda _address: None)
    assert "not running" in off
    assert "firewall" in firewall and "47989" in firewall and "Allow in firewall" in firewall
    assert "firewall" not in away and away != off


def test_the_diagnosis_reports_a_firewall_when_only_the_port_is_silent():
    from big_remote_play.private_network.models import HostCandidate
    from big_remote_play.private_network.service import OfflinePrivateNetworkService

    service = OfflinePrivateNetworkService()
    service._probe_sunshine = lambda *_args: diagnostics.SunshineProbe(False, tcp="timeout")
    service._ping = lambda _address: 8.0
    result = service.diagnose(HostCandidate("game-pc", "192.168.0.20", 47989))
    assert result.problem == "firewall" and result.host_online is True and not result.ready
    service._probe_sunshine = lambda *_args: diagnostics.SunshineProbe(False, tcp="refused")
    assert service.diagnose(HostCandidate("game-pc", "192.168.0.20", 47989)).problem == "sunshine_missing"


# ── abandoned pairing requests ─────────────────────────────────────────────

SS = """State  Recv-Q Send-Q Local Address:Port  Peer Address:Port
LISTEN 0      4096         0.0.0.0:47989      0.0.0.0:*
ESTAB  0      0      10.147.17.1:47989 10.147.17.5:40022
ESTAB  0      0      [::ffff:10.147.17.1]:47989 [::ffff:10.147.17.9]:40100
TIME-WAIT 0   0      10.147.17.1:47989 10.147.17.7:41000
ESTAB  0      0      10.147.17.1:48010 10.147.17.7:41010
"""


def test_only_open_connections_to_the_http_port_count_as_waiting():
    assert established_peers(SS, 47989) == {"10.147.17.5": 1, "10.147.17.9": 1}


def api_host(tmp_path, pending, *, status=200):
    host = SunshineHost(cdir=tmp_path)
    calls = []

    def api(method, path, payload=None, auth=None, timeout=5.0):
        calls.append((method, path, payload))
        if method == "GET" and path == "/api/pin":
            return status, json.dumps({"pairings": pending}).encode()
        if method == "DELETE" and path == "/api/pin":
            return 200, b'{"status": true}'
        return 404, b""

    host._api_request = api  # type: ignore[method-assign]
    return host, calls


def test_requests_whose_device_gave_up_are_cancelled(tmp_path):
    pending = [
        {"id": PAIRING_A, "name": "notebook", "address": "10.147.17.7"},  # cut by the firewall
        {"id": PAIRING_B, "name": "tv", "address": "10.147.17.5"},  # still waiting
    ]
    host, calls = api_host(tmp_path, pending)
    assert host.discard_abandoned_pairings(("admin", "secret"), ss=lambda _argv: SS) == 1
    assert [call for call in calls if call[0] == "DELETE"] == [("DELETE", "/api/pin", {"pairing_id": PAIRING_A})]


def test_a_retry_from_the_same_address_keeps_only_the_newest(tmp_path):
    pending = [{"id": PAIRING_A, "name": "tv", "address": "10.147.17.5"}, {"id": PAIRING_B, "name": "tv", "address": "10.147.17.5"}]
    host, calls = api_host(tmp_path, pending)
    assert host.discard_abandoned_pairings(("admin", "secret"), ss=lambda _argv: SS) == 1
    assert [call[2] for call in calls if call[0] == "DELETE"] == [{"pairing_id": PAIRING_A}]  # Sunshine lists oldest first


def test_nothing_is_cancelled_when_connections_cannot_be_read_or_access_is_refused(tmp_path):
    pending = [{"id": PAIRING_A, "name": "tv", "address": "10.147.17.5"}]
    host, calls = api_host(tmp_path, pending)
    assert host.discard_abandoned_pairings(("admin", "secret"), ss=lambda _argv: "") == 0
    host, calls = api_host(tmp_path, pending, status=401)
    assert host.discard_abandoned_pairings(("admin", "wrong"), ss=lambda _argv: SS) == 0
    assert not [call for call in calls if call[0] == "DELETE"]


def test_a_malformed_pairing_id_is_never_sent(tmp_path):
    host, calls = api_host(tmp_path, [])
    assert host.cancel_pairing("../x") is False and calls == []


# ── the firewall of the sharing computer ───────────────────────────────────

LAN = fc.Network("local", "enp7s0", ipaddress.ip_network("192.168.0.0/24"))
ZT = fc.Network("zerotier", "ztabc123", ipaddress.ip_network("10.147.17.0/24"))
TS = fc.Network("tailscale", "tailscale0", ipaddress.ip_network("100.64.0.0/10"))
PORTS = fc.required_ports(47989)

BRP_RULES = """### tuple ### allow tcp 47984,47989,48010 0.0.0.0/0 any 0.0.0.0/0 in comment=4269672052656d6f746520506c6179
-A ufw-user-input -p tcp -m multiport --dports 47984,47989,48010 -j ACCEPT
### tuple ### allow udp 47998:48000 0.0.0.0/0 any 0.0.0.0/0 in
"""


def blocked(rules, policy="DROP", networks=(LAN, ZT, TS)):
    return fc.ufw_blocked(fc.parse_ufw_rules(rules), policy, networks, PORTS)


def test_the_ports_sunshine_needs_never_include_its_administration_page():
    assert PORTS == [(47984, "tcp"), (47989, "tcp"), (48010, "tcp"), (47998, "udp"), (47999, "udp"), (48000, "udp")]


def test_no_rules_and_a_drop_policy_block_every_network():
    result = blocked("")
    assert set(result) == {"local", "zerotier", "tailscale"} and "47989/tcp" in result["zerotier"]


def test_the_rules_big_remote_play_adds_allow_every_network():
    assert blocked(BRP_RULES) == {}


def test_an_interface_wide_rule_allows_only_that_interface():
    result = blocked("### tuple ### allow any any 0.0.0.0/0 any 0.0.0.0/0 in_tailscale0\n")
    assert "tailscale" not in result and set(result) == {"local", "zerotier"}


def test_a_rule_for_the_local_network_does_not_cover_zerotier():
    rules = BRP_RULES.replace("0.0.0.0/0 any 0.0.0.0/0 in", "0.0.0.0/0 any 192.168.0.0/24 in")
    assert set(blocked(rules)) == {"zerotier", "tailscale"}


def test_the_first_matching_rule_decides_as_in_ufw():
    rules = "### tuple ### deny tcp 47989 0.0.0.0/0 any 0.0.0.0/0 in\n" + BRP_RULES
    assert blocked(rules) == {kind: ("47989/tcp",) for kind in ("local", "zerotier", "tailscale")}


def test_an_accepting_policy_blocks_nothing():
    assert blocked("", policy="ACCEPT") == {}


IP_JSON = json.dumps(
    [
        {"ifname": "lo", "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8}]},
        {"ifname": "enp7s0", "addr_info": [{"family": "inet", "local": "192.168.0.10", "prefixlen": 24}]},
        {"ifname": "docker0", "addr_info": [{"family": "inet", "local": "172.17.0.1", "prefixlen": 16}]},
        {"ifname": "ztabc123", "addr_info": [{"family": "inet", "local": "10.147.17.1", "prefixlen": 24}]},
        {"ifname": "tailscale0", "addr_info": [{"family": "inet", "local": "100.101.1.2", "prefixlen": 32}]},
    ]
)


def test_networks_are_the_ones_other_computers_come_from():
    kinds = [(n.kind, n.interface) for n in fc.networks_from_ip_json(IP_JSON)]
    assert kinds == [("local", "enp7s0"), ("zerotier", "ztabc123"), ("tailscale", "tailscale0")]


def fake_system(files, commands):
    def read(path):
        return files.get(path)

    def runner(argv, **_kwargs):
        for prefix, (code, out) in commands.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return subprocess.CompletedProcess(argv, code, out, "")
        return subprocess.CompletedProcess(argv, 1, "", "")

    return read, runner


def test_an_enabled_ufw_without_rules_is_reported_per_network():
    read, runner = fake_system(
        {fc.UFW_CONF: "ENABLED=yes\n", fc.UFW_DEFAULTS: 'DEFAULT_INPUT_POLICY="DROP"\n', fc.UFW_RULES: ""},
        {("ip",): (0, IP_JSON), ("systemctl", "is-active", "--quiet", "ufw"): (0, "")},
    )
    report = fc.check_firewall(47989, runner=runner, read=read)
    assert report.tool == "ufw" and report.blocks and set(report.blocked) == {"local", "zerotier", "tailscale"}


def test_a_disabled_ufw_blocks_nothing():
    read, runner = fake_system({fc.UFW_CONF: "ENABLED=no\n", fc.UFW_DEFAULTS: 'DEFAULT_INPUT_POLICY="DROP"\n', fc.UFW_RULES: ""}, {("ip",): (0, IP_JSON)})
    assert not fc.check_firewall(47989, runner=runner, read=read).blocks


def test_unreadable_ufw_rules_are_unknown_never_blocking():
    read, runner = fake_system({fc.UFW_CONF: "ENABLED=yes\n"}, {("ip",): (0, IP_JSON), ("systemctl", "is-active", "--quiet", "ufw"): (0, "")})
    report = fc.check_firewall(47989, runner=runner, read=read)
    assert report.tool == "unknown" and not report.blocks


def test_firewalld_zones_are_read_per_interface():
    read, runner = fake_system(
        {},
        {
            ("ip",): (0, IP_JSON),
            ("systemctl", "is-active", "--quiet", "firewalld"): (0, ""),
            ("firewall-cmd", "--state"): (0, "running\n"),
            ("firewall-cmd", "--get-default-zone"): (0, "public\n"),
            ("firewall-cmd", "--get-zone-of-interface=tailscale0"): (0, "trusted\n"),
            ("firewall-cmd", "--zone=public", "--list-ports"): (0, "47984/tcp 47989/tcp 48010/tcp 47998-48000/udp\n"),
            ("firewall-cmd", "--permanent", "--zone=public", "--get-target"): (0, "default\n"),
            ("firewall-cmd", "--zone=trusted", "--list-ports"): (0, "\n"),
            ("firewall-cmd", "--permanent", "--zone=trusted", "--get-target"): (0, "ACCEPT\n"),
        },
    )
    report = fc.check_firewall(47989, runner=runner, read=read)
    assert report.tool == "firewalld" and not report.blocks


def test_firewalld_without_the_ports_blocks():
    read, runner = fake_system(
        {},
        {
            ("ip",): (0, IP_JSON),
            ("systemctl", "is-active", "--quiet", "firewalld"): (0, ""),
            ("firewall-cmd", "--state"): (0, "running\n"),
            ("firewall-cmd", "--get-default-zone"): (0, "public\n"),
            ("firewall-cmd", "--zone=public", "--list-ports"): (0, "\n"),
            ("firewall-cmd", "--permanent", "--zone=public", "--get-target"): (0, "default\n"),
        },
    )
    report = fc.check_firewall(47989, runner=runner, read=read)
    assert report.blocks and "47989/tcp" in report.blocked_ports and "zerotier" in report.blocked
