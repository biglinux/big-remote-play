"""The three cards of Connect your devices and their real switches.

Services are fakes built on the offline service (see test_connect_your_devices);
no VPN client, API or keyring is reached.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

from big_remote_play.private_network import service as service_module  # noqa: E402
from big_remote_play.private_network.models import ConnectionState as S, PeerDevice, ProviderId as P, ProviderStatus, Recovery as R  # noqa: E402
from test_connect_your_devices import HS_OFF, TS_CONNECTED, TS_MISSING, TS_OFF, ZT_CONNECTED, FakeCentral, Service, texts, wait  # noqa: E402
from test_ui_task_flows import ui as _ui_fixture  # noqa: E402

ui = _ui_fixture

HS_CONNECTED = ProviderStatus(P.HEADSCALE, S.CONNECTED, self_device=PeerDevice("game-pc", ("100.64.0.9",), is_self=True), peers=(PeerDevice("laptop", ("100.64.0.10",), online=True),))
HS_SAVED_BEHIND_TAILSCALE = ProviderStatus(P.HEADSCALE, S.DISCONNECTED, technical_detail="tailscaled is using Tailscale", recovery=R.RECONNECT)
TS_BEHIND_HEADSCALE = ProviderStatus(P.TAILSCALE, S.DISCONNECTED, technical_detail="tailscaled is using Headscale", recovery=R.RECONNECT)
ZT_OFF = ProviderStatus(P.ZEROTIER, S.DISCONNECTED, recovery=R.START_SERVICE, networks=ZT_CONNECTED.networks)


class CardService(Service):
    """Start/stop change the statuses as scripted; ``fail`` leaves them unchanged."""

    def __init__(self, statuses=(), *, after=None, fail=(), token=False, **kwargs):
        super().__init__(statuses, after=after, **kwargs)
        self.fail = set(fail)
        self.token = token

    def has_credential(self, kind, scope="default"):
        return self.token

    def _act(self, name, provider):
        self.calls.append((name, provider))
        if provider in self.fail:
            return False
        if provider in self.after:
            self._statuses[provider] = self.after[provider]
        return True


def hub(ui, service):
    service_module.set_default_factory(lambda: service)
    ui.navigate_to("vpn_selector")
    page = ui.remote_connection_page
    for card in page.cards.values():
        card.set_summary(None)  # nothing left from an earlier reading
    page.refresh()
    assert wait(lambda: all(card.summary is not None for card in page.cards.values()))
    return page


def presented(monkeypatch):
    dialogs = []
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda self, parent: dialogs.append(self))
    return dialogs


# ── what the cards show ────────────────────────────────────────────────────


def test_three_cards_at_once_each_with_its_own_network_address_and_devices(ui):
    page = hub(ui, CardService([TS_CONNECTED, ZT_CONNECTED, HS_OFF]))
    tailscale = texts(page.cards[P.TAILSCALE])
    assert "Network: family.ts.net" in tailscale and "100.64.0.1" in tailscale and "This computer" in tailscale
    assert "2 online · 3 devices" in tailscale  # this computer is counted once
    zerotier = texts(page.cards[P.ZEROTIER])
    assert "Network: Home Gaming" in zerotier and "10.147.17.5" in zerotier and "+ 1 more network" in zerotier
    assert "100.64.0." not in zerotier and "10.147." not in tailscale  # never mixed
    for card in (page.cards[P.TAILSCALE], page.cards[P.ZEROTIER]):
        assert card.address_label.get_visible() and card.address_box.get_visible()  # really on screen
    assert page.cards[P.TAILSCALE].switch.get_active() and page.cards[P.ZEROTIER].switch.get_active()
    assert not page.cards[P.HEADSCALE].switch.get_active()


def test_zerotier_without_a_token_says_unavailable_never_zero(ui):
    page = hub(ui, CardService([ZT_CONNECTED]))
    content = texts(page.cards[P.ZEROTIER])
    assert "Devices unavailable" in content and "0 devices" not in content


def test_zerotier_counts_come_from_central_for_the_chosen_network(ui):
    page = hub(ui, CardService([ZT_CONNECTED], central=FakeCentral(), token=True))
    content = texts(page.cards[P.ZEROTIER])
    assert "· 2 devices" in content


def test_headscale_card_names_its_server(ui):
    page = hub(ui, CardService([TS_OFF, HS_CONNECTED]))
    content = texts(page.cards[P.HEADSCALE])
    assert "Server: vpn.example.test" in content and "100.64.0.9" in content and "2 online · 2 devices" in content


def test_cards_say_checking_first_and_state_unknown_when_reading_fails(ui):
    from big_remote_play.ui.remote_connection import RemoteConnectionPage

    class Broken(CardService):
        def card_summaries(self, zerotier_network=""):
            raise RuntimeError("no")

    page = RemoteConnectionPage(ui, service_factory=lambda: Broken())
    assert "Checking…" in texts(page.cards[P.TAILSCALE]) and not page.cards[P.TAILSCALE].switch.get_sensitive()
    page.refresh()
    assert wait(lambda: "State unknown" in texts(page.cards[P.TAILSCALE]))


# ── the switch ─────────────────────────────────────────────────────────────


def test_off_to_on_starts_the_connection_and_shows_it(ui):
    service = CardService([TS_OFF], after={P.TAILSCALE: TS_CONNECTED})
    card = hub(ui, service).cards[P.TAILSCALE]
    card.switch.set_active(True)
    assert card.busy and "Starting…" in texts(card)
    assert wait(lambda: not card.busy)
    assert service.calls == [("start", P.TAILSCALE)]
    assert card.switch.get_active() and card.switch.get_state() and "Connected" in texts(card)


def test_on_to_off_really_stops_it(ui):
    service = CardService([TS_CONNECTED], after={P.TAILSCALE: TS_OFF})
    card = hub(ui, service).cards[P.TAILSCALE]
    card.switch.set_active(False)
    assert wait(lambda: not card.busy)
    assert service.calls == [("stop", P.TAILSCALE)] and not card.switch.get_active()


def test_a_failed_start_puts_the_switch_back_and_says_so(ui):
    service = CardService([TS_OFF], fail={P.TAILSCALE})
    card = hub(ui, service).cards[P.TAILSCALE]
    card.switch.set_active(True)
    assert wait(lambda: not card.busy)
    assert not card.switch.get_active() and not card.switch.get_state()
    assert "Could not connect" in texts(card) and "See what happened" in texts(card)


def test_not_installed_leads_to_install_and_continue(ui):
    service = CardService([TS_MISSING])
    card = hub(ui, service).cards[P.TAILSCALE]
    card.switch.set_active(True)
    page = ui.network_navigation.get_visible_page()
    assert page.provider is P.TAILSCALE and page.install_slot.get_visible()
    assert not card.switch.get_active() and service.calls == []


def test_not_set_up_leads_to_the_setup_and_does_not_fake_on(ui):
    service = CardService([TS_OFF, HS_OFF])
    card = hub(ui, service).cards[P.HEADSCALE]
    card.switch.set_active(True)
    assert ui.network_navigation.get_visible_page().get_tag() == "hs-setup"
    assert not card.switch.get_active() and service.calls == []


def test_zerotier_turns_on_next_to_tailscale_or_headscale_without_asking(ui, monkeypatch):
    dialogs = presented(monkeypatch)
    for other in (TS_CONNECTED, HS_CONNECTED):
        service = CardService([other, ZT_OFF], after={P.ZEROTIER: ZT_CONNECTED})
        page = hub(ui, service)
        page.cards[P.ZEROTIER].switch.set_active(True)
        assert wait(lambda: not page.cards[P.ZEROTIER].busy)
        assert service.calls == [("start", P.ZEROTIER)] and dialogs == []
        assert page.cards[P.ZEROTIER].switch.get_active() and page.cards[other.provider].switch.get_active()


def test_tailscale_and_headscale_share_one_app_so_switching_asks_first(ui, monkeypatch):
    dialogs = presented(monkeypatch)
    after = {P.HEADSCALE: HS_CONNECTED}
    service = CardService([TS_CONNECTED, HS_SAVED_BEHIND_TAILSCALE], after=after)
    page = hub(ui, service)
    card = page.cards[P.HEADSCALE]
    assert "Tailscale is using the Tailscale app now" in texts(card)
    card.switch.set_active(True)
    assert dialogs and dialogs[-1].get_heading() == "Switch from Tailscale to Headscale?"
    dialogs[-1].emit("response", "cancel")
    assert not card.switch.get_active() and service.calls == []
    card.switch.set_active(True)
    service._statuses[P.TAILSCALE] = TS_BEHIND_HEADSCALE  # what the shared app reports after switching
    dialogs[-1].emit("response", "switch")
    assert wait(lambda: not card.busy)
    assert service.calls == [("start", P.HEADSCALE)] and card.switch.get_active()


def test_the_card_body_opens_the_page_and_the_switch_does_not(ui):
    service = CardService([TS_OFF], after={P.TAILSCALE: TS_CONNECTED})
    page = hub(ui, service)
    page.cards[P.TAILSCALE].switch.set_active(True)
    assert ui.network_navigation.get_visible_page().get_tag() == "providers"
    assert wait(lambda: not page.cards[P.TAILSCALE].busy)
    page.cards[P.TAILSCALE].body.emit("clicked")
    shown = ui.network_navigation.get_visible_page()
    assert shown.provider is P.TAILSCALE and ui.get_visible_dialog() is None


def test_an_unfinished_headscale_setup_says_so_and_continues(ui):
    from big_remote_play.private_network.headscale_server import SetupProgress, SetupStore

    SetupStore().save(SetupProgress(mode="another_server").mark("server"))
    page = hub(ui, CardService([TS_OFF, HS_OFF]))
    card = page.cards[P.HEADSCALE]
    assert "Setup incomplete" in texts(card) and "Continue setup" in texts(card)
    card.body.emit("clicked")
    assert ui.network_navigation.get_visible_page().get_tag() == "hs-setup"
    assert "Continue setup" in texts(ui.network_navigation.get_visible_page())


def test_a_missing_permission_leads_to_allow_instead_of_failing(ui):
    needs_permission = ProviderStatus(P.ZEROTIER, S.ERROR, recovery=R.GRANT_ACCESS, networks=ZT_CONNECTED.networks)
    service = CardService([needs_permission])
    card = hub(ui, service).cards[P.ZEROTIER]
    card.switch.set_active(True)
    page = ui.network_navigation.get_visible_page()
    assert page.provider is P.ZEROTIER and page.connection_label.get_label() in ("Allow", "")
    assert service.calls == [] and not card.switch.get_active()
