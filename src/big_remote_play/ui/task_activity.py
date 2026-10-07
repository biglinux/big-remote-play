"""Is each task doing something right now? One word per sidebar entry.

Share, Connect and Connect your devices each show a small state next to
their name in the sidebar. The state is decided here from facts the window
already has, never from the page that happens to be open:

- **Share**: Big Remote Play's own start/stop work, then whether it is
  sharing, then the Sunshine process probe (a server started elsewhere is
  sharing too).
- **Connect**: a connection attempt in progress, a stream Big Remote Play
  started, or the Moonlight process probe.
- **Connect your devices**: the private-network clients' own status
  (``ProviderStatus``); one connected method is enough.

The words live in ``activity_text``; logic only compares ``Activity`` values.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum

from big_remote_play.private_network.models import ConnectionState, ProviderStatus
from big_remote_play.utils.i18n import _


class Activity(str, Enum):
    CHECKING = "checking"  # nothing read yet
    STARTING = "starting"  # Big Remote Play is starting sharing
    CONNECTING = "connecting"  # a connection is being made
    RUNNING = "running"
    STOPPING = "stopping"  # Big Remote Play is stopping sharing
    STOPPED = "stopped"


# Big Remote Play's own transitions while its start or stop worker runs.
TRANSITION_STARTING = "starting"
TRANSITION_STOPPING = "stopping"


def share_activity(*, transition: str | None = None, sharing: bool = False, server_running: bool | None = None) -> Activity:
    """Share: this computer sends a game, or is about to start or stop."""
    if transition == TRANSITION_STARTING:
        return Activity.STARTING
    if transition == TRANSITION_STOPPING:
        return Activity.STOPPING
    if sharing or server_running:
        return Activity.RUNNING
    if server_running is None:
        return Activity.CHECKING
    return Activity.STOPPED


def connect_activity(*, connecting: bool = False, streaming: bool = False, client_running: bool | None = None) -> Activity:
    """Connect: this computer plays a game from another one, or is connecting."""
    if streaming or client_running:
        return Activity.RUNNING
    if connecting:
        return Activity.CONNECTING
    if client_running is None:
        return Activity.CHECKING
    return Activity.STOPPED


def network_activity(statuses: Iterable[ProviderStatus] | None, *, read_failed: bool = False) -> Activity:
    """Connect your devices: at least one private network is connected."""
    if statuses is None:
        # A failed first read says nothing is known; it never claims Running.
        return Activity.STOPPED if read_failed else Activity.CHECKING
    states = [status.state for status in statuses]
    if ConnectionState.CONNECTED in states:
        return Activity.RUNNING
    if ConnectionState.CONNECTING in states:
        return Activity.CONNECTING
    return Activity.STOPPED


def activity_text(activity: Activity) -> str:
    """The word shown next to the task's name."""
    return {
        Activity.CHECKING: _("Checking…"),
        # TRANSLATORS: short state next to Share in the sidebar while sharing starts.
        Activity.STARTING: _("Starting…"),
        Activity.CONNECTING: _("Connecting…"),
        # TRANSLATORS: short state next to Share, Connect or Connect your devices in the sidebar: active now.
        Activity.RUNNING: _("Running"),
        # TRANSLATORS: short state next to Share in the sidebar while sharing stops.
        Activity.STOPPING: _("Stopping…"),
        # TRANSLATORS: short state next to Share, Connect or Connect your devices in the sidebar: nothing active.
        Activity.STOPPED: _("Stopped"),
    }[activity]


# The same round pills as Share's own Running/Stopped state.
PILL_CLASSES = {
    Activity.RUNNING: "online",
    Activity.STARTING: "starting",
    Activity.CONNECTING: "starting",
    Activity.STOPPING: "starting",
}


def pill_class(activity: Activity) -> str:
    return PILL_CLASSES.get(activity, "offline")


__all__ = [
    "Activity",
    "TRANSITION_STARTING",
    "TRANSITION_STOPPING",
    "activity_text",
    "connect_activity",
    "network_activity",
    "pill_class",
    "share_activity",
]
