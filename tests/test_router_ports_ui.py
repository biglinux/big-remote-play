"""Share → Overview → Over the internet without a VPN: what the router really did."""

from __future__ import annotations

import pytest

pytest.importorskip("gi")

from big_remote_play.host.router_ports import Outcome, RouterState  # noqa: E402
from test_connect_your_devices import texts  # noqa: E402
from test_ui_task_flows import ui as _ui_fixture  # noqa: E402

ui = _ui_fixture
PORTS = ((47984, "TCP"), (47989, "TCP"), (48010, "TCP"), (47998, "UDP"), (47999, "UDP"), (48000, "UDP"))


@pytest.fixture
def host(ui):
    view = ui.host_view
    view.is_hosting = True
    view.direct_access_group.set_visible(True)
    yield view
    view.is_hosting = False


def test_open_ports_give_the_address_for_moonlight(host):
    state = RouterState(found=True, url="u", model="ZTE F6645P", external_ip="177.235.186.139", connected=True)
    host._show_router_ports((Outcome(state, PORTS), "177.235.186.139", (), "ruscher.example.org"))
    content = texts(host.direct_access_group)
    assert "Game ports open on the router" in content and "ZTE F6645P" in content
    assert "177.235.186.139" in content and "ruscher.example.org" in content
    assert "administration panel stays closed" in content


def test_a_router_without_upnp_says_how_to_turn_it_on(host):
    host._show_router_ports((Outcome(RouterState(problem="no_router")), "", (), ""))
    content = texts(host.direct_access_group)
    assert "The router did not answer" in content and "Address for Moonlight" not in content


def test_cgnat_never_offers_an_address_that_cannot_work(host):
    state = RouterState(found=True, url="u", model="Router", external_ip="100.70.1.2", connected=True)
    host._show_router_ports((Outcome(state, PORTS), "203.0.113.9", (), ""))
    content = texts(host.direct_access_group)
    assert "Another network sits in front of the router" in content and "Address for Moonlight" not in content


def test_sunshine_own_upnp_stays_off_because_the_app_checks_the_ports(host):
    host.upnp_row.set_active(True)
    assert host._build_sunshine_config().get("upnp") == "disabled" or "upnp" not in host._build_sunshine_config()
