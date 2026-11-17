"""Game ports on the home router without a VPN (UPnP IGD), checked.

Measured on 2026-11-17 with a ZTE F6645P: the router supports UPnP, but its
SSDP answers were dropped by this computer's firewall (ufw), so `upnpc -s` and
Sunshine's own UPnP found "no IGD". Asking the router at its description
address opened, listed and removed the six game ports.
"""

from __future__ import annotations

import json
import subprocess

from big_remote_play.host import router_ports as rp

DESC = "<root><device><deviceType>urn:schemas-upnp-org:device:InternetGatewayDevice:1</deviceType><manufacturer>ZTE</manufacturer><modelName>F6645P</modelName><serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType></device></root>"


class Router:
    """A fake router behind a firewall that drops SSDP: only the direct address works."""

    def __init__(self, external="177.235.186.139", refuse=(), others=()):
        self.external = external
        self.refuse = set(refuse)
        self.mappings = {(port, proto): (client, desc) for port, proto, client, desc in others}
        self.calls = []

    def fetch(self, url):
        if url == "http://192.168.0.1:52869/gatedesc.xml":
            return DESC
        raise OSError("closed")

    def run(self, argv, **_kwargs):
        self.calls.append(argv)
        out = ""
        if argv[:3] == ["ip", "-j", "-4"] and argv[3] == "route" and argv[4] == "show":
            out = json.dumps([{"dst": "default", "gateway": "192.168.0.1", "dev": "enp7s0"}])
        elif argv[:4] == ["ip", "-j", "-4", "route"]:
            out = json.dumps([{"dst": "192.168.0.1", "dev": "enp7s0", "prefsrc": "192.168.0.235"}])
        elif argv == ["upnpc", "-s"]:
            out = "No IGD UPnP Device found on the network !"
        elif argv[0] == "upnpc" and argv[3] == "-s":
            out = f"Found valid IGD : x\nStatus : Connected, uptime=1s\nExternalIPAddress = {self.external}\n"
        elif argv[0] == "upnpc" and argv[3] == "-l":
            out = "\n".join(f" {i} {proto} {port}->{client}:{port} '{desc}' '' 100" for i, ((port, proto), (client, desc)) in enumerate(sorted(self.mappings.items())))
        elif argv[0] == "upnpc" and argv[3] == "-e":
            local, port, proto = argv[6], int(argv[7]), argv[9]
            if (port, proto) not in self.refuse:
                self.mappings[(port, proto)] = (local, argv[4])
        elif argv[0] == "upnpc" and argv[3] == "-d":
            self.mappings.pop((int(argv[4]), argv[5]), None)
        return subprocess.CompletedProcess(argv, 0, out, "")

    def kwargs(self):
        return {"runner": self.run, "fetch": self.fetch, "which": lambda name: f"/usr/bin/{name}"}


def test_the_router_is_found_even_when_the_firewall_drops_ssdp():
    router = Router()
    state, local = rp.locate(**router.kwargs())
    assert (state.found, state.model, state.external_ip, local, state.cgnat) == (True, "ZTE F6645P", "177.235.186.139", "192.168.0.235", False)


def test_the_six_game_ports_open_and_are_read_back_never_the_admin_panel():
    router = Router(others=[(54873, "UDP", "192.168.0.235", "ZeroTier")])
    outcome = rp.open_ports(rp.wanted_ports(), **router.kwargs())
    assert outcome.ok and len(outcome.opened) == 6 and not outcome.failed
    assert (47990, "TCP") not in router.mappings
    assert all(desc == rp.DESCRIPTION for key, (client, desc) in router.mappings.items() if key != (54873, "UDP"))
    added = [call for call in router.calls if "-e" in call]
    assert all(call[-1] == str(rp.LEASE_SECONDS) for call in added)  # a lease, never forever
    closed = rp.close_ports(rp.wanted_ports(), **router.kwargs())
    assert len(closed.opened) == 6 and not closed.failed
    assert list(router.mappings) == [(54873, "UDP")]  # someone else's forward is kept


def test_a_port_another_device_uses_is_left_alone_and_reported():
    router = Router(others=[(47998, "UDP", "192.168.0.50", "Xbox")])
    outcome = rp.open_ports(rp.wanted_ports(), **router.kwargs())
    assert not outcome.ok and outcome.failed == ((47998, "UDP"),)
    assert router.mappings[(47998, "UDP")] == ("192.168.0.50", "Xbox")
    rp.close_ports(rp.wanted_ports(), **router.kwargs())
    assert router.mappings == {(47998, "UDP"): ("192.168.0.50", "Xbox")}


def test_a_refused_port_is_not_reported_as_open():
    router = Router(refuse={(48010, "TCP")})
    outcome = rp.open_ports(rp.wanted_ports(), **router.kwargs())
    assert outcome.failed == ((48010, "TCP"),) and not outcome.ok


def test_cgnat_and_missing_router_are_told_apart():
    router = Router(external="100.70.1.2")
    assert rp.locate(**router.kwargs())[0].cgnat
    silent = Router()
    silent.fetch = lambda url: (_ for _ in ()).throw(OSError("no"))
    assert rp.locate(**silent.kwargs())[0].problem == "no_router"
    assert rp.locate(runner=router.run, fetch=router.fetch, which=lambda name: None)[0].problem == "no_upnpc"


def test_mappings_are_parsed_from_upnpc():
    text = " 0 UDP 54873->192.168.0.235:54873 'ZeroTier/e8b6@54873' '' 424407\n 1 TCP 47984->192.168.0.235:47984 'Big Remote Play' '' 7000"
    assert [(m.external_port, m.protocol, m.client, m.description) for m in rp.parse_mappings(text)] == [
        (54873, "UDP", "192.168.0.235", "ZeroTier/e8b6@54873"),
        (47984, "TCP", "192.168.0.235", "Big Remote Play"),
    ]
