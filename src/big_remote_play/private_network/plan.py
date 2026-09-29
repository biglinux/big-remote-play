"""Decide, from what is already on this computer, the one next step to show.

The simple interface never asks which network product to use before it has
looked: a method that already works wins, then one that only needs to be
turned on or fixed, then one that is installed, and only then an install.
This module is pure (no GTK, no subprocess) so every branch is testable.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from .models import ConnectionState, ProviderId, ProviderStatus, Recovery
from .service import recommended_status


class PlanKind(str, Enum):
    CHECKING = "checking"
    OFFLINE = "offline"  # this computer has no internet address at all
    READY = "ready"  # connected: other devices can be added
    WAITING = "waiting"  # joined, the network owner has not approved it yet
    TURNED_OFF = "turned_off"  # signed in, the connection is switched off
    SERVICE_STOPPED = "service_stopped"  # the background service is not running
    ALLOW = "allow"  # one-time permission to read the network client
    SIGN_IN = "sign_in"
    SET_UP = "set_up"  # installed, never joined a network
    INSTALL = "install"  # nothing is installed
    PROBLEM = "problem"  # the client answered with something unusable


@dataclass(frozen=True)
class ConnectionPlan:
    kind: PlanKind
    provider: ProviderId
    status: ProviderStatus | None = None

    @property
    def online_devices(self) -> int:
        """Other devices the provider reports online (this computer excluded)."""
        return len(self.status.online_peers) if self.status is not None else 0


def plan_connection(statuses: Iterable[ProviderStatus] | None, *, internet: bool | None = True, preferred: ProviderId | None = None) -> ConnectionPlan:
    """The single step to offer. ``statuses`` is ``None`` while still checking."""
    if statuses is None:
        return ConnectionPlan(PlanKind.CHECKING, preferred or ProviderId.TAILSCALE)
    available = list(statuses)
    status = recommended_status(available, preferred)
    provider = status.provider
    if status.connected:
        return ConnectionPlan(PlanKind.READY, provider, status)
    if internet is False:
        # Checked after "connected": a working overlay proves there is a network.
        return ConnectionPlan(PlanKind.OFFLINE, provider, status)
    if status.state is ConnectionState.NEEDS_AUTHORIZATION:
        return ConnectionPlan(PlanKind.WAITING, provider, status)
    if status.recovery is Recovery.RECONNECT:
        return ConnectionPlan(PlanKind.TURNED_OFF, provider, status)
    if status.recovery is Recovery.START_SERVICE:
        return ConnectionPlan(PlanKind.SERVICE_STOPPED, provider, status)
    if status.recovery is Recovery.GRANT_ACCESS:
        return ConnectionPlan(PlanKind.ALLOW, provider, status)
    if status.state is ConnectionState.NEEDS_AUTHENTICATION:
        return ConnectionPlan(PlanKind.SIGN_IN, provider, status)
    if status.state is ConnectionState.ERROR:
        return ConnectionPlan(PlanKind.PROBLEM, provider, status)
    if not status.installed:
        # Nothing works and nothing is installed: recommend the easiest method.
        return ConnectionPlan(PlanKind.INSTALL, preferred if preferred is not None else ProviderId.TAILSCALE, status)
    if status.state is ConnectionState.CONNECTING:
        return ConnectionPlan(PlanKind.WAITING, provider, status)
    return ConnectionPlan(PlanKind.SET_UP, provider, status)
