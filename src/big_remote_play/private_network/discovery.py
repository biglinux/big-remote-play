"""Computers sharing with Sunshine on a ZeroTier network, without Central.

Joining someone else's ZeroTier network does not give access to its member
list: only the owner's ZeroTier Central account has it, and ``listpeers``
shows ZeroTier's own infrastructure and recently contacted nodes, not the
members. What this computer *can* do, as any member can, is ask the
GameStream port of the addresses of the network it is in.

The search is started by the person, one network at a time:

- the addresses come from this computer's own assignment on that network
  (its real prefix, never an assumed /24); a large network is searched only
  in the block around this computer's address, and the page says so;
- addresses this computer already talked to (neighbour table of the
  ZeroTier interface) and computers found before are asked first;
- each address gets one TCP connection to the GameStream port and, when it
  answers, Sunshine's unauthenticated ``/serverinfo``; no ping sweep, no
  other port;
- concurrency, per-address wait and total time are bounded, and the search
  stops as soon as it is cancelled.

What was found is remembered per network (address, name, Sunshine's
identity), so Connect can offer it later. A remembered computer is shown as
"not checked" until it answers again.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from big_remote_play import paths
from big_remote_play.integration_contracts import SUNSHINE_DEFAULT_BASE_PORT
from big_remote_play.utils.secure_io import secure_write_text

from .diagnostics import SunshineProbe, probe_sunshine

# A network larger than this is searched only in the block around this
# computer's own address (``NEAR_PREFIX``).
MAX_ADDRESSES = 1024
NEAR_PREFIX = 24
CONNECT_SECONDS = 1.0
PROBE_SECONDS = 2.0
WORKERS = 64
DEADLINE_SECONDS = 45.0
REMEMBER_SECONDS = 30 * 24 * 3600
_ID_CHARS = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class SearchArea:
    """The addresses of one ZeroTier network that will be asked."""

    network_id: str
    network_name: str
    interface: str
    own_address: str
    network: str  # the network as this computer sees it, e.g. 10.147.20.0/24
    searched: str  # the part searched: the whole network or the block around this computer
    first: tuple[str, ...] = ()  # asked before the rest

    @property
    def partial(self) -> bool:
        return self.network != self.searched

    def addresses(self) -> list[str]:
        block = ipaddress.ip_network(self.searched, strict=False)
        whole = ipaddress.ip_network(self.network, strict=False)
        own = self.own_address
        # Known addresses are asked even outside the searched block of a large network.
        ordered = [address for address in dict.fromkeys(self.first) if address != own and _inside(address, whole)]
        seen = set(ordered)
        ordered += [str(host) for host in block.hosts() if str(host) != own and str(host) not in seen]
        return ordered


def _inside(address: str, network: ipaddress.IPv4Network | ipaddress.IPv6Network) -> bool:
    try:
        return ipaddress.ip_address(address) in network
    except ValueError:
        return False


@dataclass(frozen=True)
class FoundComputer:
    address: str
    network_id: str
    name: str = ""
    uniqueid: str = ""  # Sunshine's identity (Moonlight's host uuid)
    sharing: bool = False  # Sunshine answered /serverinfo
    seen: float = 0.0  # epoch seconds of the last answer

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SearchResult:
    area: SearchArea | None
    found: tuple[FoundComputer, ...] = ()
    asked: int = 0
    cancelled: bool = False
    timed_out: bool = False
    problem: str = ""  # no_network | not_ready | no_address


# The question Big Remote Play asks on a private network, and the answer of
# a computer that is sharing (see utils/network.py start_pin_listener).
DISCOVERY_PORT = 48011
QUESTION = "BRP_WHO_SHARES 1"
_ANSWER = "BRP_SHARING 1"
_NAME_LIMIT = 64


def answer_for(name: str, port: int) -> bytes:
    clean = "".join(ch for ch in name if ch.isprintable())[:_NAME_LIMIT]
    return f"{_ANSWER} {int(port)} {clean}".encode()


def parse_answer(data: bytes) -> tuple[int, str] | None:
    """``(port, name)`` from an answer, or ``None`` for anything else."""
    text = data.decode("utf-8", errors="replace").strip()
    if not text.startswith(_ANSWER + " "):
        return None
    parts = text[len(_ANSWER) + 1 :].split(" ", 1)
    try:
        port = int(parts[0])
    except ValueError:
        return None
    if not 1 <= port <= 65535:
        return None
    name = "".join(ch for ch in (parts[1] if len(parts) > 1 else "") if ch.isprintable())[:_NAME_LIMIT]
    return port, name


def ask_who_shares(area: SearchArea, *, timeout: float = 2.0, cancel: threading.Event | None = None) -> dict[str, str]:
    """Broadcast the question on the network of ``area``; ``{address: name}`` of the answers.

    Sent from this computer's address on that network to the network's own
    broadcast address, so it stays on that network; answers from outside the
    network are ignored.
    """
    network = ipaddress.ip_network(area.network, strict=False)
    if network.version != 4:
        return {}
    answers: dict[str, str] = {}
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind((area.own_address, 0))
            sock.settimeout(0.2)
            sock.sendto(QUESTION.encode(), (str(network.broadcast_address), DISCOVERY_PORT))
            end = time.monotonic() + timeout
            while time.monotonic() < end and not (cancel and cancel.is_set()):
                try:
                    data, (address, _port) = sock.recvfrom(512)
                except TimeoutError:
                    continue
                parsed = parse_answer(data)
                try:
                    inside = ipaddress.ip_address(address) in network
                except ValueError:
                    inside = False
                if parsed is not None and inside and address != area.own_address:
                    answers[address] = parsed[1]
    except OSError:
        return answers
    return answers


def valid_network_id(value: str) -> bool:
    return len(value) == 16 and set(value.lower()) <= _ID_CHARS


def search_area(network, *, max_addresses: int = MAX_ADDRESSES, first: Iterable[str] = ()) -> SearchArea | None:
    """Where to search on ``network`` (a ``ZeroTierNetwork``), from its IPv4 assignment."""
    for assigned in getattr(network, "assigned_addresses", ()) or ():
        try:
            interface = ipaddress.ip_interface(str(assigned))
        except ValueError:
            continue
        if interface.version != 4 or interface.network.prefixlen >= 31:
            continue
        whole = interface.network
        searched = whole
        if whole.num_addresses > max_addresses:
            searched = ipaddress.ip_network(f"{interface.ip}/{max(NEAR_PREFIX, whole.prefixlen)}", strict=False)
        return SearchArea(
            network_id=network.network_id,
            network_name=getattr(network, "display_name", "") or network.network_id,
            interface=getattr(network, "device", "") or "",
            own_address=str(interface.ip),
            network=str(whole),
            searched=str(searched),
            first=tuple(first),
        )
    return None


def neighbours(interface: str, run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> list[str]:
    """IPv4 addresses this computer recently exchanged packets with on ``interface``."""
    if not interface or not all(ch.isalnum() or ch in "-_." for ch in interface):
        return []
    try:
        result = run(["ip", "-4", "-j", "neigh", "show", "dev", interface], capture_output=True, text=True, timeout=3, check=False)
        entries = json.loads(result.stdout or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    # Every entry, even one that did not answer lately: it is a computer
    # this one talked to on that network, worth one question.
    found = []
    for entry in entries if isinstance(entries, list) else ():
        if isinstance(entry, dict) and entry.get("dst"):
            found.append(str(entry["dst"]))
    return found


def port_open(address: str, port: int = SUNSHINE_DEFAULT_BASE_PORT, timeout: float = CONNECT_SECONDS) -> bool:
    try:
        with socket.create_connection((address, port), timeout=timeout):
            return True
    except OSError:
        return False


def search(
    area: SearchArea,
    *,
    cancel: threading.Event | None = None,
    port: int = SUNSHINE_DEFAULT_BASE_PORT,
    connect: Callable[[str], bool] | None = None,
    probe: Callable[[str], SunshineProbe] | None = None,
    workers: int = WORKERS,
    deadline: float = DEADLINE_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    progress: Callable[[int, int], object] | None = None,
) -> SearchResult:
    """Ask every address of ``area`` for Sunshine; bounded and cancellable."""
    cancel = cancel or threading.Event()
    connect = connect or (lambda address: port_open(address, port))
    probe = probe or (lambda address: probe_sunshine(address, port, timeout=PROBE_SECONDS))
    addresses = area.addresses()
    total = len(addresses)
    found: list[FoundComputer] = []
    asked = 0
    timed_out = False
    started = clock()

    def ask(address: str) -> FoundComputer | None:
        if cancel.is_set() or not connect(address):
            return None
        answer = probe(address)
        if not answer.listening:
            return None
        return FoundComputer(address, area.network_id, answer.hostname, answer.uniqueid, bool(answer.answered), time.time())

    pending: set[Future] = set()
    queue = iter(addresses)
    with ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="brp-zt-search") as pool:
        while True:
            while len(pending) < workers and not cancel.is_set():
                address = next(queue, None)
                if address is None:
                    break
                pending.add(pool.submit(ask, address))
            if not pending:
                break
            done, pending = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
            for future in done:
                asked += 1
                computer = future.result()
                if computer is not None:
                    found.append(computer)
            if progress is not None and done:
                progress(asked, total)
            if clock() - started > deadline:
                timed_out = True
                cancel.set()
            if cancel.is_set():
                for future in pending:
                    future.cancel()
                break
    order = {address: index for index, address in enumerate(addresses)}
    found.sort(key=lambda computer: (not computer.sharing, order.get(computer.address, 0)))
    return SearchResult(area, tuple(found), asked, cancelled=cancel.is_set() and not timed_out, timed_out=timed_out)


@dataclass
class FoundStore:
    """Computers found per ZeroTier network, in a private file."""

    path: Path = field(default_factory=lambda: paths.CONFIG_DIR / "history" / "zerotier-found.json")
    clock: Callable[[], float] = time.time

    def load(self) -> dict[str, list[FoundComputer]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            return {}
        result: dict[str, list[FoundComputer]] = {}
        now = self.clock()
        for network_id, items in data.items() if isinstance(data, dict) else ():
            if not valid_network_id(str(network_id)) or not isinstance(items, list):
                continue
            kept = []
            for item in items:
                try:
                    computer = FoundComputer(
                        address=str(ipaddress.ip_address(str(item["address"]))),
                        network_id=str(network_id),
                        name=str(item.get("name", ""))[:128],
                        uniqueid=str(item.get("uniqueid", ""))[:64],
                        sharing=bool(item.get("sharing")),
                        seen=float(item.get("seen", 0.0)),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                if now - computer.seen < REMEMBER_SECONDS:
                    kept.append(computer)
            if kept:
                result[str(network_id)] = kept
        return result

    def for_network(self, network_id: str) -> list[FoundComputer]:
        return self.load().get(network_id, [])

    def remember(self, result: SearchResult) -> None:
        """Replace what is known of the searched block with what answered now."""
        area = result.area
        if area is None or result.cancelled:
            return
        data = self.load()
        searched = ipaddress.ip_network(area.searched, strict=False)
        # Computers of this block that did not answer are forgotten only after
        # a complete search; a timed-out one keeps the older knowledge.
        kept = [c for c in data.get(area.network_id, []) if result.timed_out or ipaddress.ip_address(c.address) not in searched]
        answered = {computer.address for computer in result.found}
        data[area.network_id] = [c for c in kept if c.address not in answered] + list(result.found)
        self._save(data)

    def forget(self, network_id: str, address: str) -> None:
        data = self.load()
        if network_id in data:
            data[network_id] = [c for c in data[network_id] if c.address != address]
            self._save(data)

    def _save(self, data: Mapping[str, Sequence[FoundComputer]]) -> None:
        payload = {network_id: [computer.as_dict() for computer in items] for network_id, items in data.items() if items}
        secure_write_text(str(self.path), json.dumps(payload, indent=2, sort_keys=True))


def find_computers(
    network,
    *,
    known: Iterable[str] = (),
    cancel: threading.Event | None = None,
    progress: Callable[[int, int], object] | None = None,
    ask: Callable[..., dict[str, str]] = ask_who_shares,
    run_search: Callable[..., SearchResult] = search,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> SearchResult:
    """The whole search of one joined network (a ``ZeroTierNetwork``)."""
    cancel = cancel or threading.Event()
    if not getattr(network, "ready", False):
        return SearchResult(None, problem="not_ready")
    area = search_area(network)
    if area is None:
        return SearchResult(None, problem="no_address")
    answered = ask(area, cancel=cancel)
    area = replace(area, first=(*answered, *neighbours(area.interface, run), *known))
    result = run_search(area, cancel=cancel, progress=progress)
    found = tuple(replace(computer, name=computer.name or answered.get(computer.address, "")) for computer in result.found)
    return replace(result, found=found)
