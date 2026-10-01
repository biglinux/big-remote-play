"""Devices asking to pair with this computer, as Sunshine reports them.

GTK-free. While sharing, Big Remote Play already reads Sunshine's waiting
pairing requests (``GET /api/pin``) every few seconds. :class:`RequestTracker`
turns those snapshots into events the interface can show: a new request (ask
the person now), a request that is gone (the device stopped waiting, or it was
answered) and a request that expired (nobody answered in time).

What Sunshine can and cannot tell: it lists each waiting device with an id,
the name Moonlight sent and its address. It never knows the PIN: Moonlight
makes it up and shows it only on the other screen, and the PIN is what proves
that the person approving sees that screen. So the PIN is always typed here;
it is never stored, logged or sent anywhere but to this computer's Sunshine.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
import time

from big_remote_play.host.sunshine_manager import PendingPairing
from big_remote_play.utils.i18n import _

# How long a request waits for an answer before it is cancelled. Moonlight
# keeps its PIN on screen while it waits; two minutes is plenty to read it.
REQUEST_TIMEOUT_SECONDS = 120.0
# Moonlight Qt sends the same placeholder device name from every computer.
_PLACEHOLDER_NAMES = {"", "roth", "moonlight", "unknown"}


@dataclass(frozen=True)
class PairRequest:
    pairing_id: str
    name: str  # what to call the device, already friendly
    address: str
    first_seen: float

    def remaining(self, now: float, timeout: float = REQUEST_TIMEOUT_SECONDS) -> float:
        return max(0.0, self.first_seen + timeout - now)


@dataclass(frozen=True)
class TrackerEvents:
    new: tuple[PairRequest, ...] = ()
    gone: tuple[PairRequest, ...] = ()
    expired: tuple[PairRequest, ...] = ()


def friendly_name(item: PendingPairing, resolve: Callable[[str], str] | None = None) -> str:
    """The device's own name when it has one, else what the network calls it."""
    name = (item.name or "").strip()
    if name.lower() not in _PLACEHOLDER_NAMES:
        return name
    address = (item.address or "").split("%", 1)[0].removeprefix("::ffff:")
    if resolve is not None and address:
        try:
            resolved = resolve(address)
        except Exception:  # a name is a nicety; the address is still shown
            resolved = ""
        if resolved:
            return resolved
    if address:
        return _("Device at {address}").format(address=address)
    return _("Another computer")


@dataclass
class RequestTracker:
    """The requests on screen, from one Sunshine snapshot to the next."""

    timeout: float = REQUEST_TIMEOUT_SECONDS
    clock: Callable[[], float] = time.monotonic
    requests: dict[str, PairRequest] = field(default_factory=dict)

    def update(self, pending: Iterable[PendingPairing], names: dict[str, str] | None = None) -> TrackerEvents:
        now = self.clock()
        names = names or {}
        current = {item.pairing_id: item for item in pending}
        new: list[PairRequest] = []
        for pairing_id, item in current.items():
            if pairing_id not in self.requests:
                request = PairRequest(pairing_id, names.get(pairing_id) or friendly_name(item), item.address, now)
                self.requests[pairing_id] = request
                new.append(request)
        gone = [request for pairing_id, request in self.requests.items() if pairing_id not in current]
        for request in gone:
            del self.requests[request.pairing_id]
        expired = [request for request in self.requests.values() if request.remaining(now, self.timeout) <= 0]
        for request in expired:
            del self.requests[request.pairing_id]
        return TrackerEvents(tuple(new), tuple(gone), tuple(expired))

    def forget(self, pairing_id: str) -> None:
        """Answered here (approved or rejected): not a request any more."""
        self.requests.pop(pairing_id, None)

    def clear(self) -> None:
        self.requests.clear()

    def oldest(self) -> PairRequest | None:
        return min(self.requests.values(), key=lambda request: request.first_seen, default=None)


def valid_pin(text: str) -> str:
    """The four digits typed or pasted, or ``""``.

    Spaces and dashes from a pasted "12 34" or "12-34" are ignored; anything
    else (letters, a fifth digit, full-width digits) is refused rather than
    silently trimmed into a different PIN.
    """
    from big_remote_play.integration_contracts import MOONLIGHT_PAIRING_PIN_LENGTH

    compact = "".join(character for character in (text or "") if character not in " -\t")
    if len(compact) == MOONLIGHT_PAIRING_PIN_LENGTH and compact.isascii() and compact.isdigit():
        return compact
    return ""


def countdown_text(seconds: float) -> str:
    whole = int(seconds + 0.999)
    return _("This request expires in {time}").format(time=f"{whole // 60}:{whole % 60:02d}")


__all__ = ["PairRequest", "REQUEST_TIMEOUT_SECONDS", "RequestTracker", "TrackerEvents", "countdown_text", "friendly_name", "valid_pin"]
