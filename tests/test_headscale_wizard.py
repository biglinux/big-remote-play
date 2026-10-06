"""The Headscale setup wizard, page by page, with every outside effect faked.

The public-address services, DNS, the server check, the PolicyKit helper and
the Tailscale client are replaced; nothing is installed, configured or sent.
Addresses are documentation addresses (RFC 5737).
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

from big_remote_play.private_network import dns_check, public_address, service as service_module  # noqa: E402
from big_remote_play.private_network.credentials import CredentialKind  # noqa: E402
from big_remote_play.private_network.headscale_server import HelperResult, ServerAddress, ServerCheck, SetupProgress, SetupStore  # noqa: E402
from big_remote_play.private_network.models import ConnectionState as S, PeerDevice, ProviderId as P, ProviderStatus  # noqa: E402
from big_remote_play.utils.vpn_accounts import TailscaleConnection  # noqa: E402
from test_connect_your_devices import Service, buttons, texts, wait  # noqa: E402
from test_ui_task_flows import ui as _ui_fixture  # noqa: E402

ui = _ui_fixture

PUBLIC = "203.0.113.10"
VPS = "198.51.100.20"  # a server somewhere else
DOMAIN = "headscale.example.com"
HEALTHY = ServerCheck(ServerAddress(DOMAIN), dns="ok", port="ok", https="ok", headscale="ok", version="0.29.3", details=("dns: ok",))


class Manager:
    """The Tailscale app: joining records the key and login server used."""

    def __init__(self, service):
        self.service = service
        self.joins = []

    def list_tailscale_profiles(self):
        return SimpleNamespace(profiles=(), selected=SimpleNamespace(profile_id="hs1"))

    def connect_tailscale(self, *, login_server="", auth_key="", add_account=False, on_auth_url=None, on_output=None, timeout=300.0):
        self.joins.append((login_server, auth_key))
        if on_auth_url is not None:
            on_auth_url(f"{login_server}/register/hskey-authreq-ABCDEFGHIJKLMNOPQRSTUVWX")
        self.service._statuses[P.HEADSCALE] = ProviderStatus(P.HEADSCALE, S.CONNECTED, self_device=PeerDevice("game-pc", ("100.64.0.1",), is_self=True))
        return TailscaleConnection(True)

    def set_tailscale_metadata(self, profile_id, **values):
        self.metadata = (profile_id, values)


class WizardService(Service):
    def __init__(self, statuses=()):
        super().__init__(statuses)
        self.manager = Manager(self)


class FakeLocal:
    def __init__(self, **results):
        self.results = results
        self.calls = []

    def run(self, command, *args, **kwargs):
        self.calls.append((command, *args))
        return self.results.get(command, HelperResult(True, "ok", {"USER_NAME": "player"}, secret="hskey-api-LOCALKEY" if command == "create-apikey" else ""))


class FakeApi:
    created = []
    tested = []

    def __init__(self, origin, key=""):
        self.origin, self.key = origin, key

    def test(self):
        from big_remote_play.private_network.http import ApiResult

        FakeApi.tested.append(self.origin)
        return ApiResult(self.key.startswith("hskey-api-"), 200 if self.key.startswith("hskey-api-") else 401)

    def users(self):
        from big_remote_play.private_network.headscale_api import HeadscaleUser
        from big_remote_play.private_network.http import ApiResult

        return [HeadscaleUser("1", "player")], ApiResult(True, 200)

    def create_user(self, name):
        from big_remote_play.private_network.headscale_api import HeadscaleUser
        from big_remote_play.private_network.http import ApiResult

        return HeadscaleUser("2", name), ApiResult(True, 200)

    registered = []
    accept = True

    def register_node(self, user_name, key):
        from big_remote_play.private_network.http import ApiResult

        FakeApi.registered.append((self.origin, user_name, key))
        return ApiResult(True, 200, {"node": {"givenName": "pixel-8"}}) if FakeApi.accept else ApiResult(False, 404)

    def create_preauth_key(self, user_id, *, hours=1, **kwargs):
        from big_remote_play.private_network.http import ApiResult

        FakeApi.created.append((self.origin, user_id, hours))
        return "hskey-auth-ONETIME", ApiResult(True, 200)


@pytest.fixture(autouse=True)
def fake_api(monkeypatch):
    import big_remote_play.private_network.headscale_api as api

    FakeApi.created = []
    FakeApi.tested = []
    FakeApi.registered = []
    FakeApi.accept = True
    monkeypatch.setattr(api, "HeadscaleApi", FakeApi)
    monkeypatch.setattr("big_remote_play.ui.headscale_wizard.default_user_name", lambda: "player")


def report(**values):
    return public_address.PublicAddressReport(**{"ipv4": PUBLIC, "ipv4_source": "stun", "local_ipv4": ("192.168.1.20",), **values})


def wizard(ui, tmp_path, *, service=None, local=None, **deps):
    from big_remote_play.ui.headscale_wizard import HeadscaleWizard

    service = service or WizardService()
    service_module.set_default_factory(lambda: service)
    ui.navigate_to("vpn_selector")
    defaults = dict(
        detect=lambda force: report(),
        nameservers=lambda zone, expected: dns_check.NameserverCheck("cloudflare", zone, ("ada.ns.cloudflare.com",)),
        zone_of=lambda domain: "example.com",
        record=lambda name, expected, rtype: dns_check.RecordCheck("ready", (expected,)) if rtype == "A" else dns_check.RecordCheck("missing"),
        check_server=lambda address, connect_host: HEALTHY,
        web_ports=lambda: (),
        open_uri=lambda widget, url: opened.append(url),
        sleep=lambda seconds: None,
        clock=lambda: 0.0,
    )
    defaults.update(deps)
    opened: list[str] = []
    wiz = HeadscaleWizard(ui, ui.network_navigation, service_factory=lambda: service, store=SetupStore(tmp_path / "setup.json"), local=local or FakeLocal(), deps=SimpleNamespace(**defaults))
    wiz.opened = opened
    wiz.present()
    wiz.service = service
    return wiz


def visible(ui):
    return ui.network_navigation.get_visible_page()


def rows(widget):
    found, stack = [], [widget]
    while stack:
        current = stack.pop()
        if isinstance(current, Adw.ActionRow):
            found.append(current)
        child = current.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return found


def press(ui, label):
    found = [button for button in buttons(visible(ui), label) if button.is_sensitive()]
    assert found, f"no button {label!r} on {visible(ui).get_tag()}"
    found[-1].emit("clicked")


# ── the start ──────────────────────────────────────────────────────────────


def test_it_starts_by_asking_where_the_server_runs(ui, tmp_path):
    wiz = wizard(ui, tmp_path)
    content = texts(visible(ui))
    assert visible(ui).get_tag() == "hs-setup" and ui.get_visible_dialog() is None
    assert "Where will your Headscale server run?" in content and "stay available" in content
    assert wiz.root is visible(ui)


# ── this computer ──────────────────────────────────────────────────────────


def test_the_public_ip_is_detected_and_offered_to_copy(ui, tmp_path):
    wiz = wizard(ui, tmp_path)
    wiz.choose_this_computer()
    assert wait(lambda: PUBLIC in texts(visible(ui)))
    public = [row for row in rows(visible(ui)) if row.get_title() == "Your public IP"]
    assert len(public) == 1 and public[0].get_subtitle() == f"{PUBLIC} · Detected automatically"
    press(ui, "Next")
    assert wiz.progress.is_done("server") and wiz.progress.server_ip == PUBLIC
    assert visible(ui).get_tag() == "hs-address"


def test_a_private_address_is_never_offered_as_public(ui, tmp_path):
    wiz = wizard(ui, tmp_path, detect=lambda force: public_address.PublicAddressReport(local_ipv4=("192.168.1.20",), rejected=("192.168.1.20",)))
    wiz.choose_this_computer()
    assert wait(lambda: "We could not find your public address" in texts(visible(ui)))
    assert not buttons(visible(ui), "Next") and not wiz.progress.server_ip


def test_cgnat_suggests_a_vps_without_blocking(ui, tmp_path):
    wiz = wizard(ui, tmp_path, detect=lambda force: report(cgnat=True, cgnat_reason="router_private"))
    wiz.choose_this_computer()
    assert wait(lambda: "may block incoming connections" in texts(visible(ui)))
    assert buttons(visible(ui), "Use another server") and buttons(visible(ui), "Continue anyway")
    press(ui, "Continue anyway")
    assert visible(ui).get_tag() == "hs-address"


def test_ipv6_is_suggested_only_when_it_reaches_this_computer(ui, tmp_path):
    wiz = wizard(ui, tmp_path, detect=lambda force: report(ipv6="2001:db8::10", local_ipv6=("2001:db8::10",)))
    wiz.choose_this_computer()
    assert wait(lambda: "Public IPv6" in texts(visible(ui)))
    press(ui, "Next")
    assert wiz.progress.server_ipv6 == "2001:db8::10"
    ui.network_navigation.pop_to_tag("providers")  # what opening the setup again does
    other = wizard(ui, tmp_path / "b", detect=lambda force: report(ipv6="2001:db8::10", local_ipv6=()))
    other.choose_this_computer()
    assert wait(lambda: PUBLIC in texts(visible(ui)))
    press(ui, "Next")
    assert other.progress.server_ipv6 == ""


def to_record_page(ui, wiz):
    wiz.choose_this_computer()
    assert wait(lambda: PUBLIC in texts(visible(ui)))
    press(ui, "Next")
    wiz._use_domain()
    wiz._push(wiz._domain_entry_page())
    wiz.domain_entry.set_text("https://Headscale.Example.com/")
    wiz._submit_domain()


def test_an_existing_domain_on_cloudflare_goes_straight_to_the_record(ui, tmp_path):
    wiz = wizard(ui, tmp_path)
    to_record_page(ui, wiz)
    assert wait(lambda: visible(ui).get_tag() == "hs-record")
    content = texts(visible(ui))
    for expected in ("Add this to Cloudflare.", "headscale", PUBLIC, "DNS only", "the cloud must be gray", "Keep Proxy status = DNS only"):
        assert expected in content
    assert wiz.progress.domain == DOMAIN and wiz.progress.is_done("domain")
    press(ui, "I added the record")
    assert wait(lambda: "Domain is ready" in texts(visible(ui)))
    assert wiz.progress.is_done("dns")
    assert wiz.progress.server_url == f"https://{DOMAIN}"  # nothing to type again


def test_an_invalid_domain_is_explained(ui, tmp_path):
    wiz = wizard(ui, tmp_path)
    wiz._push(wiz._domain_entry_page())
    wiz.domain_entry.set_text("my server")
    wiz._submit_domain()
    assert "This is not a domain." in texts(visible(ui)) and not wiz.progress.domain


@pytest.mark.parametrize(
    ("a", "aaaa", "message"),
    [
        (dns_check.RecordCheck("missing"), dns_check.RecordCheck("missing"), "We are still waiting for DNS"),
        (dns_check.RecordCheck("proxied", ("104.16.1.1",)), dns_check.RecordCheck("missing"), "orange cloud"),
        (dns_check.RecordCheck("wrong", ("198.51.100.7",)), dns_check.RecordCheck("missing"), "not pointing to this server yet"),
        (dns_check.RecordCheck("ready", (PUBLIC,)), dns_check.RecordCheck("wrong", ("2001:db8::99",)), "IPv6 record points somewhere else"),
    ],
)
def test_record_problems_are_named_and_not_marked_done(ui, tmp_path, a, aaaa, message):
    wiz = wizard(ui, tmp_path, record=lambda name, expected, rtype: a if rtype == "A" else aaaa)
    to_record_page(ui, wiz)
    assert wait(lambda: visible(ui).get_tag() == "hs-record")
    press(ui, "I added the record")
    assert wait(lambda: message in texts(visible(ui)))
    assert not wiz.progress.is_done("dns") and buttons(visible(ui), "Check again")


def test_no_domain_leads_through_digitalplat_then_cloudflare_and_checks_nameservers(ui, tmp_path):
    states = iter(["other", "cloudflare"])
    wiz = wizard(ui, tmp_path, nameservers=lambda zone, expected: dns_check.NameserverCheck(next(states), zone, ("ns1.digitalplat.org",)))
    wiz.progress = SetupProgress(mode="this_computer", server_ip=PUBLIC, address_kind="domain").mark("server")
    wiz._push(wiz._get_domain_page())
    guide = wiz.digitalplat_guide
    assert "Step 1 of 3" in texts(guide)
    press(ui, "Open DigitalPlat")
    assert wiz.opened == ["https://dashboard.digitalplat.org"]
    guide.advance()
    guide.advance()  # step 2 needs the registered domain
    assert guide.index == 1
    wiz.registered_entry.set_text("mydomain.dpdns.org")
    guide.advance()
    assert guide.index == 2
    press(ui, "Set up Cloudflare")
    assert visible(ui).get_tag() == "hs-cloudflare" and wiz.progress.domain == "headscale.mydomain.dpdns.org"
    cloudflare = wiz.cloudflare_guide
    assert "mydomain.dpdns.org" in texts(cloudflare)
    cloudflare.advance()
    wiz.ns_entry.set_text("ada.ns.cloudflare.com bob.ns.cloudflare.com")
    cloudflare.advance()
    assert wiz.progress.nameservers == ("ada.ns.cloudflare.com", "bob.ns.cloudflare.com")
    assert "Copy these two Cloudflare nameservers to DigitalPlat." in texts(cloudflare)
    cloudflare.advance()  # step 4 checks by itself
    assert wait(lambda: "still uses other nameservers" in texts(visible(ui)))
    assert not wiz.progress.is_done("domain")
    press(ui, "Check again")
    assert wait(lambda: "Cloudflare is now managing your domain" in texts(visible(ui)))
    assert wiz.progress.is_done("domain")


def test_cloudflare_rejects_nameservers_that_are_not_cloudflares(ui, tmp_path):
    wiz = wizard(ui, tmp_path)
    wiz.progress = SetupProgress(mode="this_computer", domain=DOMAIN, dns_manager="cloudflare")
    wiz._push(wiz._cloudflare_page())
    wiz.cloudflare_guide.advance()
    wiz.ns_entry.set_text("ns1.digitalplat.org")
    wiz.cloudflare_guide.advance()
    assert wiz.cloudflare_guide.index == 1


def test_using_the_address_skips_the_domain_steps(ui, tmp_path):
    wiz = wizard(ui, tmp_path)
    wiz.choose_this_computer()
    assert wait(lambda: PUBLIC in texts(visible(ui)))
    press(ui, "Next")
    wiz._use_ip()
    assert visible(ui).get_tag() == "hs-install" and "domain" not in wiz.progress.steps
    assert f"https://{PUBLIC}" in texts(visible(ui))


NOT_THERE = ServerCheck(ServerAddress(DOMAIN), dns="ok", port="failed", problem="refused")


def install_page(ui, tmp_path, *, ready=True, **kwargs):
    # By its public name nothing answers yet; through loopback the new server does.
    kwargs.setdefault("check_server", lambda address, host: HEALTHY if host == "127.0.0.1" else NOT_THERE)
    wiz = wizard(ui, tmp_path, **kwargs)
    wiz.progress = SetupProgress(mode="this_computer", address_kind="domain", domain=DOMAIN, server_ip=PUBLIC).mark("server").mark("domain").mark("dns")
    wiz.report = report()
    wiz._push(wiz._install_page())
    wiz._components_ready()  # Headscale and Caddy are there
    if ready:
        assert wait(lambda: wiz.setup_button.is_sensitive())
    return wiz


def test_the_local_server_is_configured_checked_and_its_network_created(ui, tmp_path):
    local = FakeLocal(configure=HelperResult(True, "ok", {"LISTEN": "127.0.0.1:18080"}))
    wiz = install_page(ui, tmp_path, local=local)
    assert "ports 80 and 443 in this computer's firewall" in texts(visible(ui))  # said before anything changes
    press(ui, "Set up the server")
    assert wait(lambda: "Your Headscale server is ready" in texts(visible(ui)))
    assert local.calls[:2] == [("configure", DOMAIN), ("firewall",)]
    assert ("create-user", "player") in local.calls and ("create-apikey",) in local.calls
    assert wiz.progress.is_done("https") and wiz.progress.is_done("headscale")
    assert wiz.phase_rows["firewall"].get_subtitle() == "Done"
    assert wiz.progress.local_listen == "127.0.0.1:18080"  # the free port the helper chose
    assert FakeApi.tested == ["http://127.0.0.1:18080"]
    stored = wiz.service.credentials.secret(CredentialKind.HEADSCALE_API_KEY, f"https://{DOMAIN}")
    assert stored == "hskey-api-LOCALKEY"  # in the keyring, not in the progress file
    assert "LOCALKEY" not in (tmp_path / "setup.json").read_text()


def test_no_password_changes_nothing_and_says_so(ui, tmp_path):
    wiz = install_page(ui, tmp_path, local=FakeLocal(configure=HelperResult(False, "not_authorized")))
    press(ui, "Set up the server")
    assert wait(lambda: "The password was not given." in texts(visible(ui)))
    assert not wiz.progress.is_done("https")


def test_https_not_reachable_explains_the_router_or_cgnat(ui, tmp_path):
    clock = iter(range(0, 1000, 50))
    failing = ServerCheck(ServerAddress(DOMAIN), dns="ok", port="ok", https="failed", problem="tls_failed", details=("tls: no certificate",))
    wiz = install_page(ui, tmp_path, check_server=lambda address, host: failing if host else NOT_THERE, clock=lambda: float(next(clock)))
    press(ui, "Set up the server")
    assert wait(lambda: "Your router needs one more step" in texts(visible(ui)))
    assert "192.168.1.20" in texts(visible(ui)) and not wiz.progress.is_done("https")
    wiz.report = report(cgnat=True)
    clock2 = iter(range(0, 1000, 50))
    wiz.deps.clock = lambda: float(next(clock2))
    press(ui, "Check again")
    assert wait(lambda: "blocks incoming connections" in texts(visible(ui)))
    assert buttons(visible(ui), "Use a VPS instead")


# ── another server ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("check", "message"),
    [
        (ServerCheck(ServerAddress(DOMAIN), dns="ok", port="failed", problem="refused", details=("tcp: refused",)), "The Headscale server is not reachable."),
        (ServerCheck(ServerAddress(DOMAIN), dns="ok", port="ok", https="failed", problem="tls_invalid"), "certificate is not valid"),
        (ServerCheck(ServerAddress(DOMAIN), dns="ok", port="ok", https="ok", headscale="failed", problem="not_headscale"), "it is not a Headscale server"),
        (ServerCheck(ServerAddress(DOMAIN), dns="failed", problem="dns"), "does not lead to a server yet"),
    ],
)
def test_server_problems_are_said_in_words_with_details_underneath(ui, tmp_path, check, message):
    wiz = wizard(ui, tmp_path, check_server=lambda address, host: check)
    wiz.choose_another_server()
    wiz.remote_entry.set_text(DOMAIN)
    press(ui, "Check server")
    assert wait(lambda: message in texts(visible(ui)))
    assert not wiz.progress.is_done("headscale") and buttons(visible(ui), "Guided server setup")


def test_an_invalid_server_address_is_refused_before_any_check(ui, tmp_path):
    asked = []
    wiz = wizard(ui, tmp_path, check_server=lambda address, host: asked.append(address) or HEALTHY)
    wiz.choose_another_server()
    wiz.remote_entry.set_text("http://insecure.example.com")
    press(ui, "Check server")
    assert "This is not a server address." in texts(visible(ui)) and asked == []


def test_the_guided_server_setup_uses_the_address_already_typed(ui, tmp_path):
    wiz = wizard(ui, tmp_path, check_server=lambda address, host: ServerCheck(ServerAddress(DOMAIN), dns="ok", port="failed", problem="refused"))
    wiz.choose_another_server()
    wiz.remote_entry.set_text(DOMAIN)
    press(ui, "Check server")
    assert wait(lambda: bool(buttons(visible(ui), "Guided server setup")))
    press(ui, "Guided server setup")
    content = texts(visible(ui))
    assert f"server_url: https://{DOMAIN}" in content and "apikeys create" in content


def test_a_healthy_server_then_this_computer_joins_with_a_one_time_key(ui, tmp_path):
    wiz = wizard(ui, tmp_path)
    wiz.choose_another_server()
    wiz.remote_entry.set_text(DOMAIN)
    press(ui, "Check server")
    assert wait(lambda: "Server found" in texts(visible(ui)))
    assert all(wiz.progress.is_done(step) for step in ("server", "domain", "dns", "https", "headscale"))
    press(ui, "Next")
    assert visible(ui).get_tag() == "hs-this-computer"
    wiz.api_entry.set_text("hskey-api-REMOTEKEY")
    press(ui, "Use the API key")
    assert wait(lambda: "This computer is connected" in texts(visible(ui)))
    assert wiz.service.manager.joins == [(f"https://{DOMAIN}", "hskey-auth-ONETIME")]
    assert FakeApi.created == [(f"https://{DOMAIN}", "1", 1)]  # one hour, not an admin key
    assert wiz.progress.is_done("this_computer")
    assert wiz.service.manager.metadata[1]["provider"] == "headscale"


def test_api_and_pre_auth_keys_cannot_be_swapped(ui, tmp_path):
    wiz = wizard(ui, tmp_path)
    wiz.progress = SetupProgress(mode="another_server", domain=DOMAIN, address_kind="domain")
    wiz._push(wiz._this_computer_page())
    wiz.api_entry.set_text("hskey-auth-abcdef123456")
    press(ui, "Use the API key")
    assert "it is not an API key" in texts(visible(ui))
    wiz.key_entry.set_text("hskey-api-abcdef123456")
    press(ui, "Use this key")
    assert "This is an administrator key." in texts(visible(ui))
    assert wiz.service.manager.joins == []
    wiz.key_entry.set_text("hskey-auth-abcdef123456")
    press(ui, "Use this key")
    assert wait(lambda: "This computer is connected" in texts(visible(ui)))
    assert wiz.service.manager.joins == [(f"https://{DOMAIN}", "hskey-auth-abcdef123456")]


def test_asking_the_owner_shows_the_approval_command(ui, tmp_path):
    wiz = wizard(ui, tmp_path)
    wiz.progress = SetupProgress(mode="another_server", domain=DOMAIN, address_kind="domain", user="friends")
    wiz._push(wiz._this_computer_page())
    approved = threading.Event()

    def join(**kwargs):
        kwargs["on_auth_url"](f"https://{DOMAIN}/register/hskey-authreq-ABCDEFGHIJKLMNOPQRSTUVWX")
        approved.wait(5)  # like tailscale up: it waits for the owner
        return TailscaleConnection(False)

    wiz.service.manager.connect_tailscale = join
    press(ui, "Ask to join")
    try:
        assert wait(lambda: "headscale auth register --auth-id hskey-authreq-ABCDEFGHIJKLMNOPQRSTUVWX --user friends" in texts(visible(ui)))
    finally:
        approved.set()


def test_joining_headscale_while_tailscale_is_on_asks_first(ui, tmp_path, monkeypatch):
    from test_connect_your_devices import TS_CONNECTED

    dialogs = []
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda self, parent: dialogs.append(self))
    wiz = wizard(ui, tmp_path, service=WizardService([TS_CONNECTED]))
    wiz.progress = SetupProgress(mode="another_server", domain=DOMAIN, address_kind="domain")
    wiz._push(wiz._this_computer_page())
    wiz.key_entry.set_text("hskey-auth-abcdef123456")
    press(ui, "Use this key")
    assert wait(lambda: bool(dialogs))
    assert dialogs[-1].get_heading() == "Switch from Tailscale to Headscale?" and wiz.service.manager.joins == []
    dialogs[-1].emit("response", "switch")
    assert wait(lambda: bool(wiz.service.manager.joins))


def test_another_device_gets_the_address_and_a_short_lived_key(ui, tmp_path):
    wiz = wizard(ui, tmp_path)
    wiz.progress = SetupProgress(mode="another_server", domain=DOMAIN, address_kind="domain", user="player")
    wiz.service.credentials.save(CredentialKind.HEADSCALE_API_KEY, "hskey-api-REMOTEKEY", scope=f"https://{DOMAIN}")
    wiz._push(wiz._another_device_page())
    content = texts(visible(ui))
    assert f"https://{DOMAIN}" in content and "Copy address" in content
    press(ui, "Create a one-time key")
    assert wait(lambda: "Key created" in texts(visible(ui)))
    assert "hskey-auth-ONETIME" not in texts(visible(ui))  # hidden; Copy gives it
    press(ui, "Check for the other device")
    assert wait(lambda: "No other device yet" in texts(visible(ui)))
    assert not wiz.progress.is_done("another_device")


# ── progress ───────────────────────────────────────────────────────────────


def test_the_setup_continues_after_a_restart(ui, tmp_path):
    SetupStore(tmp_path / "setup.json").save(SetupProgress(mode="another_server", domain=DOMAIN, address_kind="domain").mark("server").mark("https").mark("headscale").mark("domain").mark("dns"))
    wizard(ui, tmp_path)
    content = texts(visible(ui))
    assert "Headscale setup" in content and "Done" in content and "Next" in content
    press(ui, "Continue setup")
    assert visible(ui).get_tag() == "hs-this-computer"


def test_start_over_forgets_the_steps(ui, tmp_path, monkeypatch):
    import big_remote_play.ui.network_common as common

    monkeypatch.setattr(common, "confirm", lambda parent, heading, body, action, run: run())
    store = SetupStore(tmp_path / "setup.json")
    store.save(SetupProgress(mode="another_server").mark("server"))
    restarted = []
    wiz = wizard(ui, tmp_path)
    wiz.on_restart = lambda: restarted.append(True)
    press(ui, "Start over")
    assert store.load() == SetupProgress()
    assert wait(lambda: restarted == [True])


def test_no_check_mark_without_a_check(ui, tmp_path):
    wiz = wizard(ui, tmp_path)
    wiz.choose_another_server()
    assert not wiz.progress.is_done("server")
    wiz.choose_this_computer()
    assert not wiz.progress.is_done("server")  # only once the public address is known


def test_coming_back_to_the_server_check_after_its_domain_works(ui, tmp_path):
    checks = iter([ServerCheck(ServerAddress(VPS), port="ok", https="failed", problem="tls_invalid"), HEALTHY])
    wiz = wizard(ui, tmp_path, check_server=lambda address, host: next(checks))
    wiz.choose_another_server()
    wiz.remote_entry.set_text(VPS)
    press(ui, "Check server")
    assert wait(lambda: bool(buttons(visible(ui), "Set up a domain for it")))
    press(ui, "Set up a domain for it")
    wiz._push(wiz._domain_entry_page())
    wiz.domain_entry.set_text(DOMAIN)
    wiz._submit_domain()
    assert wait(lambda: visible(ui).get_tag() == "hs-record")
    assert wiz.progress.server_ip == VPS  # the server's address, never asked again
    press(ui, "I added the record")
    assert wait(lambda: "Domain is ready" in texts(visible(ui)))
    press(ui, "Next")
    assert wait(lambda: "Server found" in texts(visible(ui)))
    assert visible(ui).get_tag() == "hs-remote" and wiz.progress.is_done("headscale")


def test_advanced_test_connection_answers_in_words(ui, monkeypatch):
    import big_remote_play.private_network.headscale_server as server_module
    from test_connect_your_devices import open_page

    refused = ServerCheck(ServerAddress("vpn.example.test"), dns="ok", port="failed", problem="refused", details=("tcp 203.0.113.10:443: refused",))
    monkeypatch.setattr(server_module, "check_server", lambda url, connect_host="": refused)
    connected = ProviderStatus(P.HEADSCALE, S.CONNECTED, self_device=PeerDevice("game-pc", ("100.64.0.1",), is_self=True))
    page = open_page(ui, WizardService([connected]), "headscale")
    assert wait(lambda: bool(getattr(page, "_headscale_url", "")))
    assert "server_url" in texts(page.headscale_group) and "https://vpn.example.test/api/v1" in texts(page.headscale_group)
    assert "vpn.example.test" not in texts(page.view_stack.get_child_by_name("devices")).replace("Server", "")  # technical: Advanced only
    page.test_headscale()
    assert wait(lambda: "The Headscale server is not reachable." in texts(page.headscale_group))
    content = texts(page.headscale_group)
    assert "Connection refused on port 443." in content and "203.0.113.10:443: refused" in content


def test_busy_web_ports_are_found_before_installing_anything(ui, tmp_path):
    local = FakeLocal()
    wiz = install_page(ui, tmp_path, ready=False, local=local, web_ports=lambda: (80, 443))
    assert wait(lambda: "Another program already uses the web ports (80 and 443)" in texts(visible(ui)))
    assert not wiz.setup_button.is_sensitive() and local.calls == []
    assert buttons(visible(ui), "Use another server")


def test_the_helper_refusing_busy_ports_is_explained(ui, tmp_path):
    wiz = install_page(ui, tmp_path, local=FakeLocal(configure=HelperResult(False, "ports_busy", {"BUSY_BY": "docker-proxy"})))
    press(ui, "Set up the server")
    assert wait(lambda: "Another program already uses the web ports" in texts(visible(ui)))
    assert not wiz.progress.is_done("https")


def test_a_headscale_already_answering_at_the_address_is_offered_instead(ui, tmp_path):
    local = FakeLocal()
    wiz = install_page(ui, tmp_path, ready=False, local=local, check_server=lambda address, host: HEALTHY)
    assert wait(lambda: "A Headscale server already answers at this address." in texts(visible(ui)))
    press(ui, "Use this server")
    assert visible(ui).get_tag() == "hs-this-computer" and local.calls == []
    assert wiz.progress.mode == "another_server" and wiz.progress.is_done("headscale")


ROUTER_ANSWERS = ServerCheck(ServerAddress(DOMAIN), dns="ok", port="ok", https="failed", problem="tls_invalid", resolved=(PUBLIC,), details=("tls: self-signed certificate",))


def test_a_server_on_this_computer_is_checked_directly_when_the_router_answers_itself(ui, tmp_path):
    # The name leads to this computer's public address; from inside, the
    # router (no hairpin NAT) answers with its own certificate.
    local = FakeLocal()
    wiz = wizard(ui, tmp_path, local=local, check_server=lambda address, host: HEALTHY if host == "127.0.0.1" else ROUTER_ANSWERS)
    wiz.choose_another_server()
    wiz.remote_entry.set_text(DOMAIN)
    press(ui, "Check server")
    assert wait(lambda: "Server found" in texts(visible(ui)))
    assert "This server runs on this computer." in texts(visible(ui))
    assert wiz.progress.on_this_computer and wiz.progress.is_done("headscale")
    press(ui, "Next")
    assert wait(lambda: visible(ui).get_tag() == "hs-this-computer")
    assert local.calls == [("hosts-pin", DOMAIN)]  # before any API call or join


def test_a_name_already_in_the_hosts_file_is_left_alone(ui, tmp_path):
    local = FakeLocal(**{"hosts-pin": HelperResult(False, "hosts_conflict")})
    wiz = wizard(ui, tmp_path, local=local, check_server=lambda address, host: HEALTHY if host == "127.0.0.1" else ROUTER_ANSWERS)
    wiz.choose_another_server()
    wiz.remote_entry.set_text(DOMAIN)
    press(ui, "Check server")
    assert wait(lambda: "Server found" in texts(visible(ui)))
    press(ui, "Next")
    assert wait(lambda: "This name is already set in /etc/hosts." in texts(visible(ui)))
    assert visible(ui).get_tag() == "hs-remote"


def test_a_remote_check_elsewhere_is_never_retried_through_this_computer(ui, tmp_path):
    asked = []
    elsewhere = ServerCheck(ServerAddress(DOMAIN), dns="ok", port="ok", https="failed", problem="tls_invalid", resolved=("198.51.100.7",))
    wiz = wizard(ui, tmp_path, check_server=lambda address, host: asked.append(host) or elsewhere)
    wiz.choose_another_server()
    wiz.remote_entry.set_text(DOMAIN)
    press(ui, "Check server")
    assert wait(lambda: "certificate is not valid" in texts(visible(ui)))
    assert asked == [""] and not wiz.progress.on_this_computer


def test_a_failed_check_always_has_a_way_out(ui, tmp_path):
    wiz = wizard(ui, tmp_path, check_server=lambda address, host: NOT_THERE)
    wiz.choose_another_server()
    wiz.remote_entry.set_text("ruscher.example.com")
    press(ui, "Check server")
    assert wait(lambda: bool(buttons(visible(ui), "Use a different address")))
    assert buttons(visible(ui), "Start over") and buttons(visible(ui), "Guided server setup")
    press(ui, "Use a different address")
    assert wiz.remote_entry.get_text() == ""


def approval_page(ui, tmp_path):
    wiz = wizard(ui, tmp_path)
    wiz.progress = SetupProgress(mode="another_server", domain=DOMAIN, address_kind="domain", user="player")
    wiz.service.credentials.save(CredentialKind.HEADSCALE_API_KEY, "hskey-api-REMOTEKEY", scope=f"https://{DOMAIN}")
    wiz._push(wiz._another_device_page())
    return wiz


def test_a_waiting_phone_is_approved_with_the_line_it_shows(ui, tmp_path):
    wiz = approval_page(ui, tmp_path)
    assert "Approve a device" in texts(visible(ui))
    wiz.approval.entry.set_text("headscale auth register --auth-id hskey-authreq-ABCDEFGHIJKLMNOPQRSTUVWX --user USERNAME")
    press(ui, "Approve")
    assert wait(lambda: "pixel-8 was added to your network." in texts(visible(ui)))
    assert FakeApi.registered == [(f"https://{DOMAIN}", "player", "hskey-authreq-ABCDEFGHIJKLMNOPQRSTUVWX")]  # the network's user, not USERNAME
    assert wiz.approval.entry.get_text() == "" and wiz.progress.is_done("another_device")


def test_a_wrong_or_expired_code_is_explained(ui, tmp_path):
    wiz = approval_page(ui, tmp_path)
    wiz.approval.entry.set_text("not a code")
    press(ui, "Approve")
    assert "This is not a code from the other device." in texts(visible(ui)) and FakeApi.registered == []
    FakeApi.accept = False
    wiz.approval.entry.set_text("ABCDEFGHIJKLMNOPQRSTUVWX")
    press(ui, "Approve")
    assert wait(lambda: "The code was not accepted." in texts(visible(ui)))
    assert not wiz.progress.is_done("another_device")
