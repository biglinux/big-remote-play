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
    assert "IPv6 Local" in hosts[0]["name"]


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
    assert "IPv6 Local" in hosts[0]["name"]


def test_parse_prefers_ipv4_then_global_then_link_local(fake_home: Path) -> None:
    lines = "\n".join(
        [
            '=;eth0;IPv6;Multi;_nvstream._tcp;local;;fe80::9;47989;"x"',
            '=;eth0;IPv6;Multi;_nvstream._tcp;local;;2001:db8::5;47989;"x"',
            '=;eth0;IPv4;Multi;_nvstream._tcp;local;;192.168.1.7;47989;"x"',
        ]
    )
    hosts = NetworkDiscovery().parse_avahi_output(lines)
    # IPv4 ranks first, link-local last; all three kept (single link-local).
    assert [h["ip"] for h in hosts] == ["192.168.1.7", "2001:db8::5", "fe80::9%eth0"]
