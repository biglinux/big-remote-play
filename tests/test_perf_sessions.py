"""Session detection from `ss` output (FABLE item 10).

Exact local-port matching (no substrings), IPv6 peers captured, and the config
web-UI port 47990 excluded so the app's own localhost API is not counted.
"""

from big_remote_play.ui.performance_monitor import _normalize_ip, _parse_ss_sessions, _split_endpoint


def test_split_endpoint_ipv4():
    assert _split_endpoint("192.168.0.5:47989") == ("192.168.0.5", "47989")


def test_split_endpoint_ipv6_bracketed():
    assert _split_endpoint("[fe80::1%eth0]:48010") == ("fe80::1%eth0", "48010")
    assert _split_endpoint("[::ffff:1.2.3.4]:47989") == ("::ffff:1.2.3.4", "47989")


def test_normalize_ip_strips_scope_and_mapped_prefix():
    assert _normalize_ip("fe80::1%eth0") == "fe80::1"
    assert _normalize_ip("::ffff:192.168.0.9") == "192.168.0.9"
    assert _normalize_ip(" 10.0.0.1 ") == "10.0.0.1"


def test_parse_ss_matches_exact_stream_port_ipv4():
    stdout = "tcp ESTAB 0 0 192.168.0.10:47989 192.168.0.50:53012\n"
    sessions = _parse_ss_sessions(stdout)
    assert set(sessions) == {"192.168.0.50"}
    assert sessions["192.168.0.50"]["ip"] == "192.168.0.50"
    assert sessions["192.168.0.50"]["latency"] == 0


def test_parse_ss_captures_ipv6_peer():
    stdout = "udp ESTAB 0 0 [2001:db8::1]:48000 [2001:db8::99]:41641\n"
    assert "2001:db8::99" in _parse_ss_sessions(stdout)


def test_parse_ss_ignores_web_ui_port_47990():
    stdout = "tcp ESTAB 0 0 127.0.0.1:47990 127.0.0.1:52000\n"
    assert _parse_ss_sessions(stdout) == {}


def test_parse_ss_no_substring_false_positive():
    # Local port 479890 must not match stream port 47989.
    stdout = "tcp ESTAB 0 0 192.168.0.10:479890 192.168.0.50:53012\n"
    assert _parse_ss_sessions(stdout) == {}


def test_parse_ss_skips_wildcard_and_listen_rows():
    stdout = "udp UNCONN 0 0 0.0.0.0:47998 0.0.0.0:*\ntcp LISTEN 0 0 0.0.0.0:47989 0.0.0.0:*\n"
    assert _parse_ss_sessions(stdout) == {}
