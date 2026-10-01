"""NetworkDiscovery.parse_avahi_output (pure parsing, no network)."""

from pathlib import Path

from big_remote_play.utils.network import NetworkDiscovery

# avahi-browse -p fields: =;iface;proto;name;type;domain;hostname;ip;port;txt
_IPV4_LINE = '=;eth0;IPv4;MyHost;_nvstream._tcp;local;myhost.local;192.168.1.50;47989;"x"'
# Empty hostname avoids the IPv4-enrichment DNS lookup, keeping the test offline.
_IPV6_LL_LINE = '=;eth0;IPv6;V6Host;_nvstream._tcp;local;;fe80::1;47989;"x"'


def test_parse_ipv4(fake_home: Path) -> None:
    hosts = NetworkDiscovery().parse_avahi_output(_IPV4_LINE)
    assert len(hosts) == 1
    assert hosts[0]["ip"] == "192.168.1.50"
    assert hosts[0]["port"] == 47989
    assert hosts[0]["name"] == "MyHost"


def test_parse_ipv6_link_local_gets_scope_id(fake_home: Path) -> None:
    hosts = NetworkDiscovery().parse_avahi_output(_IPV6_LL_LINE)
    assert len(hosts) == 1
    assert hosts[0]["ip"] == "fe80::1%eth0"
    assert hosts[0]["name"] == "V6Host"


def test_parse_ignores_malformed_lines(fake_home: Path) -> None:
    assert NetworkDiscovery().parse_avahi_output("garbage;line\nanother") == []


def test_parse_drops_virtual_interfaces(fake_home: Path) -> None:
    # Same host announced over physical + docker veth ifaces: only physical kept.
    lines = "\n".join(
        [
            '=;eth0;IPv4;MyHost;_nvstream._tcp;local;;192.168.1.50;47989;"x"',
            '=;veth71621c2;IPv6;MyHost;_nvstream._tcp;local;;fe80::1;47989;"x"',
            '=;docker0;IPv6;MyHost;_nvstream._tcp;local;;fe80::2;47989;"x"',
            '=;br-abc123;IPv6;MyHost;_nvstream._tcp;local;;fe80::3;47989;"x"',
        ]
    )
    hosts = NetworkDiscovery().parse_avahi_output(lines)
    assert len(hosts) == 1
    assert hosts[0]["ip"] == "192.168.1.50"


def test_parse_caps_link_local_to_one_per_host(fake_home: Path) -> None:
    # Two physical interfaces, both link-local: only one entry survives.
    lines = "\n".join(
        [
            '=;eth0;IPv6;V6Host;_nvstream._tcp;local;;fe80::1;47989;"x"',
            '=;wlan0;IPv6;V6Host;_nvstream._tcp;local;;fe80::2;47989;"x"',
        ]
    )
    hosts = NetworkDiscovery().parse_avahi_output(lines)
    assert len(hosts) == 1
    assert hosts[0]["name"] == "V6Host"


def test_parse_prefers_ipv4_then_global_then_link_local(fake_home: Path) -> None:
    lines = "\n".join(
        [
            '=;eth0;IPv6;Multi;_nvstream._tcp;local;;fe80::9;47989;"x"',
            '=;eth0;IPv6;Multi;_nvstream._tcp;local;;2001:db8::5;47989;"x"',
            '=;eth0;IPv4;Multi;_nvstream._tcp;local;;192.168.1.7;47989;"x"',
        ]
    )
    hosts = NetworkDiscovery().parse_avahi_output(lines)
    # One row per PC: retain alternative addresses for bounded fallback attempts.
    assert len(hosts) == 1
    assert hosts[0]["ip"] == "192.168.1.7"
    assert hosts[0]["addresses"] == ["192.168.1.7", "2001:db8::5", "fe80::9%eth0"]


def test_discovery_never_offers_the_local_pc(monkeypatch):
    # Sunshine announces itself over mDNS and answers on loopback, so the PC
    # running the server used to list itself as a connection target.
    discovery = NetworkDiscovery()
    monkeypatch.setattr(NetworkDiscovery, "local_addresses", lambda self: {"127.0.0.1", "::1", "192.168.1.41", "fd7a:115c:a1e0::6"})
    monkeypatch.setattr(NetworkDiscovery, "local_names", lambda self: {"i5", "i5.local"})

    hosts = [
        {"name": "i5", "ip": "192.168.1.41", "hostname": "i5.local"},
        {"name": "i5 (IPv6 Global)", "ip": "fd7a:115c:a1e0::6", "hostname": "i5.local"},
        {"name": "i5", "ip": "100.64.0.6", "hostname": "i5.local"},
        {"name": "loopback", "ip": "127.0.0.1", "hostname": ""},
        {"name": "other", "ip": "192.168.1.99", "hostname": "other.local"},
    ]
    assert [host["name"] for host in discovery.drop_this_machine(hosts)] == ["other"]


def test_a_slow_mdns_browse_keeps_the_computers_already_resolved():
    """One stale announcement made avahi-browse time out and every result was lost."""
    import subprocess

    from big_remote_play.utils.network import NetworkDiscovery

    lines = "\n".join(f"=;eth0;IPv4;PC {index};_nvstream._tcp;local;pc{index}.local;192.168.50.{index + 10};47989;" for index in range(4))

    def slow(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 5), output=lines)

    hosts = NetworkDiscovery().browse_avahi(runner=slow)
    assert [host["name"] for host in hosts] == ["PC 0", "PC 1", "PC 2", "PC 3"]


def test_mdns_browse_without_avahi_finds_nothing_quietly():
    from big_remote_play.utils.network import NetworkDiscovery

    def missing(*_args, **_kwargs):
        raise FileNotFoundError("avahi-browse")

    assert NetworkDiscovery().browse_avahi(runner=missing) == []


def test_mdns_name_reads_the_announced_name_of_an_address():
    from types import SimpleNamespace

    from big_remote_play.utils.network import mdns_name

    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="192.168.0.121\tgaming-laptop.local\n")

    assert mdns_name("192.168.0.121", runner=runner) == "gaming-laptop"
    assert calls == [["avahi-resolve-address", "192.168.0.121"]]


def test_mdns_name_asks_only_for_ip_literals_and_tolerates_failures():
    from types import SimpleNamespace

    from big_remote_play.utils.network import mdns_name

    def never(*_args, **_kwargs):
        raise AssertionError("avahi must not run")

    assert mdns_name("-x; rm", runner=never) == ""
    assert mdns_name("host.example", runner=never) == ""
    assert mdns_name("10.0.0.5", runner=lambda *a, **k: SimpleNamespace(returncode=1, stdout="")) == ""
    assert mdns_name("10.0.0.5", runner=lambda *a, **k: SimpleNamespace(returncode=0, stdout="10.0.0.5\tbad name;x.local\n")) == ""
