"""Quality words, thresholds and the real path to another computer."""

from __future__ import annotations

import json
import subprocess

import pytest

from big_remote_play.utils.connection_health import (
    GOOD_MAX_MS,
    LatencyWindow,
    Quality,
    Route,
    Transport,
    assess,
    ping_once,
    route_to,
    transport_for,
    valid_address,
)


@pytest.mark.parametrize("latency, quality", [(10, Quality.EXCELLENT), (30, Quality.EXCELLENT), (45, Quality.GOOD), (GOOD_MAX_MS, Quality.GOOD), (120, Quality.POOR)])
def test_latency_thresholds(latency, quality):
    health = assess([latency] * 6)
    assert health.quality is quality
    assert health.latency_ms == latency and health.jitter_ms == 0 and health.loss_percent == 0


def test_a_few_samples_are_still_measuring():
    assert assess([]).quality is Quality.MEASURING
    assert assess([12, 13]).quality is Quality.MEASURING
    assert assess([12, 13]).stable is None


def test_no_answer_is_said_plainly():
    health = assess([10, 11, None, None, None])
    assert health.quality is Quality.NO_RESPONSE
    assert health.stable is False


def test_one_slow_reply_does_not_change_the_verdict():
    assert assess([12, 11, 13, 90, 12, 11, 12, 13]).quality is not Quality.POOR  # median, not the last value


def test_jitter_and_loss_make_a_fast_link_unstable():
    assert assess([5, 45, 5, 45, 5, 45]).quality is Quality.UNSTABLE  # 40 ms swings
    assert assess([10, None, 10, 10, None, 10, 10, 10]).quality is Quality.UNSTABLE  # 25 % lost
    mild = assess([10, 22, 10, 22, 10, 22])  # 12 ms jitter
    assert mild.quality is Quality.GOOD
    assert mild.stable is True


def test_a_slow_link_stays_poor_even_when_unstable():
    assert assess([100, 160, 100, 160, 100]).quality is Quality.POOR


def test_window_keeps_only_recent_samples():
    window = LatencyWindow(size=4)
    for _ in range(4):
        window.add(None)
    for _ in range(4):
        window.add(12)
    assert window.health.quality is Quality.EXCELLENT


def completed(stdout="", code=0):
    return subprocess.CompletedProcess([], code, stdout, "")


def test_ping_parses_the_c_locale_reply_and_refuses_non_addresses():
    calls = []

    def runner(argv, timeout):
        calls.append(argv)
        return completed("64 bytes from 192.0.2.5: icmp_seq=1 ttl=64 time=18.4 ms\n")

    assert ping_once("192.0.2.5", runner=runner) == 18.4
    assert calls[0] == ["ping", "-n", "-c", "1", "-W", "1", "192.0.2.5"]
    assert ping_once("2001:db8::5", runner=runner) == 18.4
    assert calls[1][1] == "-6"
    assert ping_once("192.0.2.5", runner=lambda argv, timeout: completed("", 1)) is None
    for bad in ("-f", "example.com", "192.0.2.5; reboot", ""):
        assert ping_once(bad, runner=runner) is None
    assert len(calls) == 2


def test_the_route_decides_local_or_private_network():
    def runner(argv, timeout):
        assert argv[:4] == ["ip", "-j", "route", "get"]
        dev = {"10.147.17.7": "ztexample0", "100.64.0.9": "tailscale0", "192.168.56.50": "enp7s0", "8.8.8.8": "enp7s0"}[argv[4]]
        gateway = "192.168.56.1" if argv[4] == "8.8.8.8" else None
        return completed(json.dumps([{"dst": argv[4], "dev": dev, **({"gateway": gateway} if gateway else {})}]))

    assert transport_for("10.147.17.7", route_to("10.147.17.7", runner=runner)) is Transport.ZEROTIER
    assert transport_for("100.64.0.9", route_to("100.64.0.9", runner=runner)) is Transport.TAILSCALE
    assert transport_for("100.64.0.9", route_to("100.64.0.9", runner=runner), tailnet=Transport.HEADSCALE) is Transport.HEADSCALE
    assert transport_for("192.168.56.50", route_to("192.168.56.50", runner=runner)) is Transport.LOCAL
    assert transport_for("8.8.8.8", route_to("8.8.8.8", runner=runner)) is Transport.DIRECT_INTERNET
    assert transport_for("127.0.0.1", Route("lo")) is Transport.LOCAL
    assert transport_for("192.0.2.1", None) is Transport.UNKNOWN


def test_valid_address_accepts_only_literal_addresses():
    assert valid_address("[fe80::1%eth0]") == "fe80::1"
    assert valid_address("host.local") is None
    assert valid_address("--help") is None
