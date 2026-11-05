"""How good a connection to another computer is, from real measurements.

One place decides what "Excellent", "Good", "Poor" and "Unstable" mean, so
the Connect card, the Share list and the internet page never disagree.

Latency is the network round trip measured with a single ICMP echo every few
seconds (never a flood, never a speed test). Quality looks at a short window
of samples, so one slow reply does not change the verdict:

* **latency** – median of the answered samples;
* **jitter** – mean difference between consecutive answered samples;
* **loss** – share of samples without an answer.

Thresholds are for interactive game streaming. The stream adds encode,
decode and display time on top of the network, so the network budget is
small. Around 30 ms of round trip is not noticeable even in fast games;
up to about 70 ms is comfortable for most games; above that input lag is
felt. Jitter above ~20 ms or loss of several percent shows as stutter and
artifacts regardless of the average, so it is reported as unstable.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
import ipaddress
import json
import os
import re
import statistics
from pathlib import Path
import subprocess

EXCELLENT_MAX_MS = 30.0
GOOD_MAX_MS = 70.0
# A downgrade by one level (Excellent → Good) for mild instability.
MILD_JITTER_MS = 10.0
MILD_LOSS_PERCENT = 2.0
# Unstable: stutter is likely no matter how low the average is.
UNSTABLE_JITTER_MS = 20.0
UNSTABLE_LOSS_PERCENT = 10.0
MIN_SAMPLES = 3
WINDOW = 12  # one minute at the default 5-second interval
SAMPLE_INTERVAL_SECONDS = 5

_TIME_RE = re.compile(r"time[=<]\s*([0-9]+(?:\.[0-9]+)?)\s*ms")


class Quality(str, Enum):
    MEASURING = "measuring"
    EXCELLENT = "excellent"
    GOOD = "good"
    POOR = "poor"
    UNSTABLE = "unstable"
    NO_RESPONSE = "no_response"


class Transport(str, Enum):
    LOCAL = "local"
    ZEROTIER = "zerotier"
    TAILSCALE = "tailscale"
    HEADSCALE = "headscale"
    DIRECT_INTERNET = "direct_internet"
    UNKNOWN = "unknown"

    @property
    def private_network(self) -> bool:
        return self in (Transport.ZEROTIER, Transport.TAILSCALE, Transport.HEADSCALE)


@dataclass(frozen=True)
class Health:
    quality: Quality
    latency_ms: float | None = None
    jitter_ms: float | None = None
    loss_percent: float | None = None
    samples: int = 0

    @property
    def stable(self) -> bool | None:
        """``None`` while there are too few samples to say."""
        if self.quality in (Quality.MEASURING,):
            return None
        return self.quality not in (Quality.UNSTABLE, Quality.NO_RESPONSE)


def _base_quality(latency_ms: float) -> Quality:
    if latency_ms <= EXCELLENT_MAX_MS:
        return Quality.EXCELLENT
    if latency_ms <= GOOD_MAX_MS:
        return Quality.GOOD
    return Quality.POOR


def assess(samples: Iterable[float | None]) -> Health:
    """Samples are round-trip times in ms, ``None`` for an unanswered probe."""
    values = list(samples)
    answered = [value for value in values if value is not None]
    count = len(values)
    if count and all(value is None for value in values[-MIN_SAMPLES:]) and count >= MIN_SAMPLES:
        return Health(Quality.NO_RESPONSE, loss_percent=100.0 * (count - len(answered)) / count, samples=count)
    if len(answered) < MIN_SAMPLES:
        return Health(Quality.MEASURING, latency_ms=answered[-1] if answered else None, samples=count)
    latency = statistics.median(answered)
    jitter = statistics.fmean(abs(b - a) for a, b in zip(answered, answered[1:]))
    loss = 100.0 * (count - len(answered)) / count
    quality = _base_quality(latency)
    if quality is not Quality.POOR:
        if jitter >= UNSTABLE_JITTER_MS or loss >= UNSTABLE_LOSS_PERCENT:
            quality = Quality.UNSTABLE
        elif quality is Quality.EXCELLENT and (jitter >= MILD_JITTER_MS or loss >= MILD_LOSS_PERCENT):
            quality = Quality.GOOD
    return Health(quality, round(latency, 1), round(jitter, 1), round(loss, 1), count)


class LatencyWindow:
    """The last :data:`WINDOW` samples for one address."""

    def __init__(self, size: int = WINDOW) -> None:
        self._samples: deque[float | None] = deque(maxlen=size)

    def add(self, sample: float | None) -> Health:
        self._samples.append(sample)
        return self.health

    @property
    def health(self) -> Health:
        return assess(self._samples)

    def clear(self) -> None:
        self._samples.clear()


def valid_address(address: str) -> str | None:
    """A literal IP address (zone id removed), or ``None``; hostnames are refused."""
    text = (address or "").strip().strip("[]").split("%", 1)[0]
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return None


Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def _run(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, LC_ALL="C", LANG="C")
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env, check=False)


def ping_once(address: str, *, timeout_s: int = 1, runner: Runner = _run) -> float | None:
    """One ICMP echo; the round trip in ms, or ``None`` without an answer."""
    literal = valid_address(address)
    if literal is None:
        return None
    argv = ["ping", "-n", "-c", "1", "-W", str(timeout_s)]
    if ":" in literal:
        argv.insert(1, "-6")
    try:
        result = runner([*argv, literal], timeout=timeout_s + 1.5)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    match = _TIME_RE.search(result.stdout or "")
    return float(match.group(1)) if match else None


@dataclass(frozen=True)
class Route:
    device: str = ""
    gateway: str = ""


def route_to(address: str, *, runner: Runner = _run) -> Route | None:
    """The interface the kernel really uses to reach ``address`` (``ip route get``)."""
    literal = valid_address(address)
    if literal is None:
        return None
    try:
        result = runner(["ip", "-j", "route", "get", literal], timeout=3)
        payload = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return None
    return Route(str(payload[0].get("dev") or ""), str(payload[0].get("gateway") or ""))


def transport_for(address: str, route: Route | None, *, tailnet: Transport = Transport.TAILSCALE) -> Transport:
    """LOCAL, a private network, or the plain internet — from the real route.

    ``tailnet`` says whether this computer's Tailscale client is signed in to
    Tailscale or to a Headscale server; the interface is the same.
    """
    literal = valid_address(address)
    if literal is None or route is None or not route.device:
        return Transport.UNKNOWN
    device = route.device
    if device.startswith("zt"):
        return Transport.ZEROTIER
    if device.startswith("tailscale"):
        return tailnet if tailnet in (Transport.TAILSCALE, Transport.HEADSCALE) else Transport.TAILSCALE
    ip = ipaddress.ip_address(literal)
    if device == "lo" or ip.is_loopback or not route.gateway or not ip.is_global:
        return Transport.LOCAL
    return Transport.DIRECT_INTERNET


def read_interface_bytes(prefixes: tuple[str, ...], *, root: Path = Path("/sys/class/net")) -> tuple[int, int] | None:
    """Received and sent bytes of every interface whose name starts with a prefix.

    Kernel counters, read from sysfs: no command runs and no traffic is made.
    ``None`` when no such interface exists.
    """
    received = sent = 0
    found = False
    try:
        interfaces = sorted(root.iterdir())
    except OSError:
        return None
    for interface in interfaces:
        if not interface.name.startswith(prefixes):
            continue
        try:
            received += int((interface / "statistics" / "rx_bytes").read_text().strip())
            sent += int((interface / "statistics" / "tx_bytes").read_text().strip())
            found = True
        except (OSError, ValueError):
            continue
    return (received, sent) if found else None


def traffic_mbps(previous: tuple[float, int, int] | None, current: tuple[float, int, int]) -> tuple[float, float] | None:
    """Download and upload in Mbit/s between two ``(time, rx, tx)`` readings."""
    if previous is None:
        return None
    elapsed = current[0] - previous[0]
    if elapsed <= 0 or current[1] < previous[1] or current[2] < previous[2]:
        return None  # clock or counter reset
    return ((current[1] - previous[1]) * 8 / elapsed / 1e6, (current[2] - previous[2]) * 8 / elapsed / 1e6)


@dataclass(frozen=True)
class LinkSample:
    """One measurement of a private network, from the provider itself."""

    latency_ms: float | None = None
    path: str = "unknown"  # direct | relay | unknown | none (no other device online)
    peer: str = ""


@dataclass(frozen=True)
class ConnectionInfo:
    """One other computer, as the status cards show it."""

    device_name: str
    address: str
    transport: Transport = Transport.UNKNOWN
    health: Health = Health(Quality.MEASURING)
    connected: bool = True
    started_at: float | None = None
    # How the picture is sent, in stable technical words (e.g. from Sunshine's log).
    video: str = ""
    # Problems the person can fix, as (code, detail): "scaled", "hdr_as_sdr".
    warnings: tuple[tuple[str, str], ...] = ()
    # Share: already playing when this computer started watching, not a new connection.
    preexisting: bool = False
    # False when ``device_name`` is only a stand-in ("Connected device", "Device at …").
    name_known: bool = True


__all__ = [
    "ConnectionInfo",
    "EXCELLENT_MAX_MS",
    "GOOD_MAX_MS",
    "Health",
    "LatencyWindow",
    "LinkSample",
    "Quality",
    "Route",
    "SAMPLE_INTERVAL_SECONDS",
    "Transport",
    "assess",
    "ping_once",
    "read_interface_bytes",
    "route_to",
    "traffic_mbps",
    "transport_for",
    "valid_address",
]
