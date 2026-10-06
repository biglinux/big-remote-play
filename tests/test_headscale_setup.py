"""Headscale setup and the cards' data, without GTK and without the network.

Public-address services, DNS-over-HTTPS resolvers, the server and the
PolicyKit helper are replaced by fakes; nothing leaves this process.
"""

from __future__ import annotations

import json
import os
import ssl
import struct
import subprocess
from pathlib import Path

import pytest

from big_remote_play.private_network import dns_check, public_address
from big_remote_play.private_network.cards import summarize
from big_remote_play.private_network.credentials import CredentialError, CredentialKind, validate_secret
from big_remote_play.private_network.device_list import DeviceListing, ProviderDevice
from big_remote_play.private_network.diagnostics import NetworkFacts
from big_remote_play.private_network.headscale_server import (
    LocalServer,
    ServerAddress,
    SetupProgress,
    SetupStore,
    check_server,
    parse_server_address,
    preauth_key_kind,
)
from big_remote_play.private_network.models import ConnectionState as S, OverlayNetwork, PeerDevice, ProviderId as P, ProviderStatus

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "usr/share/big-remote-play/scripts/headscale-server-helper.sh"

# Documentation addresses (RFC 5737 / 3849) and private ones; nothing real.
PUBLIC_V4 = "203.0.113.10"
OTHER_V4 = "198.51.100.7"
PUBLIC_V6 = "2001:db8::10"


# ── public address ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("address", "kind"),
    [
        ("8.8.8.8", "public"),
        ("192.168.1.20", "private"),
        ("10.0.0.5", "private"),
        ("172.20.1.1", "private"),
        ("100.70.1.2", "shared"),
        ("127.0.0.1", "private"),
        ("169.254.1.1", "private"),
        ("nonsense", "invalid"),
    ],
)
def test_only_internet_addresses_count_as_public(address, kind):
    assert public_address.address_kind(address) == kind


def test_stun_answer_is_decoded_from_the_xor_mapped_address():
    transaction = bytes(range(12))
    raw = bytes(int(part) for part in "8.8.4.4".split("."))
    cookie = struct.pack("!I", 0x2112A442)
    xored = bytes(byte ^ cookie[index] for index, byte in enumerate(raw))
    attribute = struct.pack("!HHBBH", 0x0020, 8, 0, 1, 1234 ^ 0x2112) + xored
    message = struct.pack("!HHI", 0x0101, len(attribute), 0x2112A442) + transaction + attribute
    assert public_address.parse_stun_response(message, transaction) == "8.8.4.4"
    assert public_address.parse_stun_response(message, bytes(12)) == ""  # someone else's answer
    assert len(public_address.stun_request(transaction)) == 20


def test_https_answers_are_read_from_trace_or_json():
    assert public_address.parse_ip_answer("fl=1\nip=8.8.8.8\nts=1\n") == "8.8.8.8"
    assert public_address.parse_ip_answer('{"ip":"2001:4860::1"}') == "2001:4860::1"


def facts(*, ipv4=("192.168.1.20",), ipv6=(), cgnat=False):
    return lambda: NetworkFacts(tuple(ipv4), tuple(ipv6), (), cgnat)


def test_public_ipv4_found_by_stun_and_ipv6_only_when_this_computer_has_it():
    stun = lambda server, family: {2: "8.8.8.8", 10: "2001:4860::10"}.get(int(family), "")  # noqa: E731
    report = public_address.detect_public_addresses(stun=stun, https=lambda url: "", facts=facts(ipv6=("2001:4860::10",)), router=lambda: "")
    assert (report.ipv4, report.ipv4_source) == ("8.8.8.8", "stun")
    assert report.ipv6 == "2001:4860::10" and report.ipv6_on_this_computer
    other = public_address.detect_public_addresses(stun=stun, https=lambda url: "", facts=facts(ipv6=()), router=lambda: "")
    assert other.ipv6 and not other.ipv6_on_this_computer  # seen outside, not this computer's own


def test_a_private_answer_is_never_taken_as_the_public_address():
    stun = lambda server, family: "192.168.1.20" if int(family) == 2 else ""  # noqa: E731
    report = public_address.detect_public_addresses(stun=stun, https=lambda url: "8.8.8.8" if "1.1.1.1" in url else "", facts=facts(), router=lambda: "")
    assert report.ipv4 == "8.8.8.8" and report.ipv4_source == "https"
    assert "192.168.1.20" in report.rejected
    nothing = public_address.detect_public_addresses(stun=lambda s, f: "10.0.0.1", https=lambda url: "", facts=facts(), router=lambda: "")
    assert not nothing.found


@pytest.mark.parametrize(
    ("local_cgnat", "router", "expected", "reason"),
    [
        (True, "", True, "local_shared"),
        (False, "100.80.1.1", True, "router_private"),
        (False, "8.8.4.4", True, "router_differs"),
        (False, "8.8.8.8", False, ""),
        (False, "", None, ""),
    ],
)
def test_cgnat_is_reported_only_on_evidence(local_cgnat, router, expected, reason):
    assert public_address.assess_cgnat("8.8.8.8", NetworkFacts(("192.168.1.20",), (), (), local_cgnat), router) == (expected, reason)


def test_the_answer_is_cached_for_a_while():
    public_address.clear_cache()
    calls = []
    clock = [0.0]
    detect = lambda: calls.append(1) or public_address.PublicAddressReport(ipv4="8.8.8.8")  # noqa: E731
    public_address.cached_public_addresses(clock=lambda: clock[0], detect=detect)
    clock[0] = 60
    public_address.cached_public_addresses(clock=lambda: clock[0], detect=detect)
    assert len(calls) == 1
    public_address.cached_public_addresses(force=True, clock=lambda: clock[0], detect=detect)
    assert len(calls) == 2
    public_address.clear_cache()


# ── DNS ────────────────────────────────────────────────────────────────────


class Resolver:
    """DNS over HTTPS answers by (name, type); a resolver host can be made to fail."""

    def __init__(self, answers, *, down=()):
        self.answers = answers
        self.down = set(down)
        self.asked = []

    def __call__(self, method, url, headers, body, timeout):
        from urllib.parse import parse_qs, urlsplit

        parts = urlsplit(url)
        self.asked.append(parts.hostname)
        if parts.hostname in self.down:
            raise OSError("down")
        query = parse_qs(parts.query)
        key = (query["name"][0], query["type"][0])
        payload = self.answers.get(key, {"Status": 3})
        return 200, json.dumps(payload).encode()


def answer(rtype, *values, name="x"):
    code = dns_check.TYPE_CODES[rtype]
    return {"Status": 0, "Answer": [{"name": name, "type": code, "data": value} for value in values]}


def soa_authority(zone):
    return {"Status": 0, "Authority": [{"name": zone + ".", "type": 6, "data": "ns admin 1 2 3 4 5"}]}


def client(answers, **kwargs):
    return dns_check.DnsClient(transport=Resolver(answers, **kwargs))


def test_cloudflare_nameservers_are_recognised_for_the_zone_of_a_subdomain():
    answers = {
        ("headscale.me.dpdns.org", "SOA"): soa_authority("me.dpdns.org"),
        ("me.dpdns.org", "NS"): answer("NS", "ada.ns.cloudflare.com.", "bob.ns.cloudflare.com."),
    }
    check = dns_check.check_nameservers("headscale.me.dpdns.org", client=client(answers))
    assert check.state == "cloudflare" and check.zone == "me.dpdns.org"
    expected = dns_check.check_nameservers("headscale.me.dpdns.org", expected=("ada.ns.cloudflare.com", "bob.ns.cloudflare.com"), client=client(answers))
    assert expected.state == "expected"
    wrong = dns_check.check_nameservers("headscale.me.dpdns.org", expected=("cid.ns.cloudflare.com",), client=client(answers))
    assert wrong.state == "other"


def test_nameservers_still_elsewhere_missing_domain_and_no_resolver():
    other = {("me.dpdns.org", "SOA"): answer("SOA", "x", name="me.dpdns.org."), ("me.dpdns.org", "NS"): answer("NS", "ns1.digitalplat.org.")}
    assert dns_check.check_nameservers("me.dpdns.org", client=client(other)).state == "other"
    assert dns_check.check_nameservers("nothere.example", client=client({})).state == "missing"
    down = dns_check.DnsClient(transport=Resolver({}, down={"cloudflare-dns.com", "dns.google"}))
    assert dns_check.check_nameservers("me.dpdns.org", client=down).state == "error"


def test_the_second_resolver_answers_when_the_first_is_down():
    resolver = Resolver({("h.example.com", "A"): answer("A", "8.8.8.8")}, down={"cloudflare-dns.com"})
    check = dns_check.check_record("h.example.com", "8.8.8.8", client=dns_check.DnsClient(transport=resolver))
    assert check.state == "ready" and resolver.asked == ["cloudflare-dns.com", "dns.google"]


@pytest.mark.parametrize(
    ("records", "state"),
    [
        (("8.8.8.8",), "ready"),
        (("8.8.4.4",), "wrong"),
        (("8.8.8.8", "8.8.4.4"), "wrong"),  # one more address would send some clients elsewhere
        (("104.16.1.1",), "proxied"),  # Cloudflare's orange cloud
        ((), "missing"),
    ],
)
def test_a_record_validation(records, state):
    answers = {("headscale.example.com", "A"): answer("A", *records)} if records else {}
    assert dns_check.check_record("headscale.example.com", "8.8.8.8", client=client(answers)).state == state


def test_aaaa_record_validation_and_proxied_ipv6():
    answers = {("h.example.com", "AAAA"): answer("AAAA", "2001:4860::10")}
    assert dns_check.check_record("h.example.com", "2001:4860::10", rtype="AAAA", client=client(answers)).state == "ready"
    proxied = {("h.example.com", "AAAA"): answer("AAAA", "2606:4700::1")}
    assert dns_check.check_record("h.example.com", "2001:4860::10", rtype="AAAA", client=client(proxied)).state == "proxied"


@pytest.mark.parametrize(
    ("typed", "domain"),
    [
        ("Headscale.Example.com", "headscale.example.com"),
        ("https://headscale.example.com/", "headscale.example.com"),
        ("headscale.example.com:443", "headscale.example.com"),
        ("vpn.exämple.com", "vpn.xn--exmple-cua.com"),
    ],
)
def test_domains_typed_by_people_are_cleaned(typed, domain):
    assert dns_check.normalize_domain(typed) == domain


@pytest.mark.parametrize("typed", ["", "localhost", "203.0.113.10", "bad domain.com", "-x.example.com", "x.example.123", "a;b.com"])
def test_invalid_domains_are_refused(typed):
    with pytest.raises(ValueError):
        dns_check.normalize_domain(typed)


def test_pasted_nameservers_are_parsed_whatever_the_separator():
    assert dns_check.parse_nameservers("ada.ns.cloudflare.com.,\nBOB.ns.cloudflare.com  junk!") == ("ada.ns.cloudflare.com", "bob.ns.cloudflare.com")


# ── the server ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("headscale.example.com", ServerAddress("headscale.example.com")),
        ("https://Headscale.Example.com/", ServerAddress("headscale.example.com")),
        (PUBLIC_V4, ServerAddress(PUBLIC_V4)),
        ("[2001:db8::10]:8443", ServerAddress(PUBLIC_V6, 8443)),
    ],
)
def test_server_addresses(typed, expected):
    assert parse_server_address(typed) == expected


@pytest.mark.parametrize("typed", ["", "http://headscale.example.com", "https://x.example.com/admin", "https://user@x.example.com", "x y", "0.0.0.0"])
def test_invalid_server_addresses(typed):
    with pytest.raises(ValueError):
        parse_server_address(typed)
    assert check_server(typed).problem == "invalid"


def server(**steps):
    resolve = steps.get("resolve", lambda host, port: [PUBLIC_V4])
    port = steps.get("port", lambda host, port: "open")

    def https(address, path, target):
        if "tls" in steps:
            raise steps["tls"]
        if path == "/version":
            return 200, b'{"version":"0.29.3"}', {}
        return steps.get("health", (200, b'{"status":"pass"}', {"issuer": ((("organizationName", "Let's Encrypt"),),), "notAfter": "Jan  1 00:00:00 2027 GMT"}))

    return dict(resolve=resolve, port_check=port, https_get=https)


def test_a_healthy_server_passes_every_step():
    check = check_server("headscale.example.com", **server())
    assert check.healthy and (check.dns, check.port, check.https, check.headscale) == ("ok", "ok", "ok", "ok")
    assert check.version == "0.29.3" and check.certificate_issuer == "Let's Encrypt"


@pytest.mark.parametrize(
    ("steps", "problem"),
    [
        (dict(resolve=lambda host, port: (_ for _ in ()).throw(OSError("no such name"))), "dns"),
        (dict(port=lambda host, port: "refused"), "refused"),
        (dict(port=lambda host, port: "timeout"), "timeout"),
        (dict(tls=ssl.SSLCertVerificationError("certificate has expired")), "tls_invalid"),
        (dict(tls=ssl.SSLError("handshake failure")), "tls_failed"),
        (dict(health=(404, b"not found", {})), "not_headscale"),
        (dict(health=(200, b'{"status":"fail"}', {})), "not_headscale"),
    ],
)
def test_each_failure_names_its_step(steps, problem):
    check = check_server("headscale.example.com", **server(**steps))
    assert check.problem == problem and not check.healthy


def test_a_server_on_this_computer_is_checked_by_its_name_through_loopback():
    seen = []

    def port(host, number):
        seen.append(host)
        return "open"

    check_server("headscale.example.com", connect_host="127.0.0.1", **{**server(), "port_check": port})
    assert seen == ["127.0.0.1"]


# ── progress ───────────────────────────────────────────────────────────────


def test_progress_order_skips_domain_and_dns_for_an_address():
    progress = SetupProgress(mode="this_computer", address_kind="ip", server_ip=PUBLIC_V4)
    assert "domain" not in progress.steps and progress.server_url == f"https://{PUBLIC_V4}"
    progress = progress.mark("server", now=1).mark("https", now=2)
    assert progress.next_step == "headscale" and not progress.complete
    done = progress.mark("headscale").mark("this_computer")
    assert done.complete and done.next_step == "another_device"  # inviting is optional


def test_a_domain_becomes_the_server_address():
    progress = SetupProgress(mode="this_computer", address_kind="domain", domain="headscale.example.com", server_ip=PUBLIC_V4)
    assert progress.server_url == "https://headscale.example.com"


def test_progress_survives_a_restart_without_any_secret(tmp_path):
    store = SetupStore(tmp_path / "headscale-setup.json")
    progress = SetupProgress(mode="this_computer", address_kind="domain", domain="headscale.example.com", server_ip=PUBLIC_V4, nameservers=("ada.ns.cloudflare.com",), user="player").mark(
        "server", now=5
    )
    store.save(progress)
    assert store.load() == progress
    assert os.stat(store.path).st_mode & 0o777 == 0o600
    text = store.path.read_text()
    for forbidden in ("hskey", "key", "password", "token"):
        assert forbidden not in text.lower().replace("nameservers", "")
    store.clear()
    assert store.load() == SetupProgress()


def test_a_damaged_progress_file_starts_over(tmp_path):
    path = tmp_path / "headscale-setup.json"
    path.write_text('{"mode": 3, "done": {"server": "x", "bogus": 1}')
    assert SetupStore(path).load() == SetupProgress()
    path.write_text('{"mode": "another_server", "done": {"server": 1, "bogus": 2}, "unknown": 1}')
    assert SetupStore(path).load().done == {"server": 1.0}


# ── keys ───────────────────────────────────────────────────────────────────


def test_api_keys_and_pre_auth_keys_are_never_confused():
    assert preauth_key_kind("hskey-auth-abc123def456") == "preauth"
    assert preauth_key_kind("0123456789abcdef0123456789abcdef0123456789abcdef") == "preauth"
    assert preauth_key_kind("hskey-api-abc123def456") == "api"
    assert preauth_key_kind("hello world") == "invalid"
    with pytest.raises(CredentialError) as error:
        validate_secret(CredentialKind.HEADSCALE_API_KEY, "hskey-auth-abc123def456")
    assert error.value.reason == "auth_key"
    with pytest.raises(CredentialError):
        validate_secret(CredentialKind.HEADSCALE_API_KEY, "tskey-api-abc123def456")
    assert validate_secret(CredentialKind.HEADSCALE_API_KEY, "hskey-api-abc123def456")


# ── the PolicyKit helper ───────────────────────────────────────────────────


class Process:
    def __init__(self, lines, code=0):
        self.stdout = iter(line + "\n" for line in lines)
        self.code = code

    def wait(self):
        return self.code


def test_the_api_key_from_the_helper_never_reaches_text_or_data():
    argv = []

    def popen(command, **kwargs):
        argv.append(command)
        return Process(["Creating…", "BRP_PHASE 0.5", "BRP_DATA API_KEY=hskey-api-SECRET", "BRP_DATA RESULT=ok"])

    texts = []
    result = LocalServer(popen=popen, exists=lambda path: True).run("create-apikey", on_text=texts.append)
    assert result.ok and result.secret == "hskey-api-SECRET"
    assert "SECRET" not in json.dumps(result.data) + " ".join(result.lines) + " ".join(texts)
    assert argv == [["pkexec", "/usr/share/big-remote-play/scripts/headscale-server-helper.sh", "create-apikey"]]


def test_helper_refusals_and_a_missing_install():
    refused = LocalServer(popen=lambda *a, **k: Process([], code=126), exists=lambda path: True).run("status")
    assert refused.result == "not_authorized" and not refused.ok
    conflict = LocalServer(popen=lambda *a, **k: Process(["BRP_DATA RESULT=other_server"], code=4), exists=lambda path: True).run("configure", "headscale.example.com")
    assert conflict.result == "other_server" and not conflict.ok
    assert LocalServer(exists=lambda path: False).run("status").result == "not_available"
    with pytest.raises(ValueError):
        LocalServer().argv("rm", "-rf")


@pytest.mark.parametrize(
    "argv",
    [
        ["configure", "headscale.example.com; rm -rf /"],
        ["configure", "$(id)"],
        ["configure", "HEADSCALE.example.com"],
        ["configure", "300.1.1.1"],
        ["configure"],
        ["create-user", "Bad Name"],
        ["create-user", "x", "y"],
        ["unknown"],
        [],
        ["hosts-pin", "x.example.com; rm -rf /"],
        ["hosts-pin", "203.0.113.10"],
        ["hosts-pin"],
        ["hosts-unpin", "Bad Name"],
    ],
)
def test_the_helper_refuses_anything_but_its_validated_commands(argv):
    result = subprocess.run(["bash", str(HELPER), *argv], capture_output=True, text=True, timeout=10, env={"PATH": "/usr/bin:/bin", "LANG": "C"})
    assert result.returncode == 2
    assert "BRP_DATA RESULT=invalid" in result.stdout


# ── the cards ──────────────────────────────────────────────────────────────

ME = PeerDevice("game-pc", ("100.64.0.1",), is_self=True)
PEERS = (PeerDevice("notebook", ("100.64.0.2",), online=True), PeerDevice("tv", ("100.64.0.3",), online=False))


def test_tailscale_card_counts_this_computer_once_in_both_numbers():
    card = summarize(ProviderStatus(P.TAILSCALE, S.CONNECTED, network_name="family.example", self_device=ME, peers=PEERS))
    assert card.switch_on and card.network == "family.example" and card.address == "100.64.0.1"
    assert (card.devices.total, card.devices.online) == (3, 2)


def test_an_off_card_shows_no_numbers_instead_of_zero():
    card = summarize(ProviderStatus(P.TAILSCALE, S.DISCONNECTED, recovery=None))
    assert not card.switch_on and card.devices.total is None and card.address == ""


def zt_status(networks):
    return ProviderStatus(P.ZEROTIER, S.CONNECTED, network_id=networks[0].network_id, self_device=PeerDevice("game-pc", (), is_self=True, node_id="aaaaaaaaaa"), networks=tuple(networks))


NETS = (OverlayNetwork("8056c2e21c000001", "Home Gaming", S.CONNECTED, ("10.147.17.5",)), OverlayNetwork("8056c2e21c000002", "Work", S.CONNECTED, ("10.200.0.5",)))


def test_zerotier_card_shows_the_chosen_network_and_how_many_more():
    listing = DeviceListing(P.ZEROTIER, (ProviderDevice("aaaaaaaaaa", "game-pc", "10.147.17.5", True, is_local=True), ProviderDevice("b", "pc", "10.147.17.6", False)), network_id=NETS[0].network_id)
    card = summarize(zt_status(NETS), listing=listing)
    assert (card.network, card.address, card.more_networks) == ("Home Gaming", "10.147.17.5", 1)
    assert (card.devices.total, card.devices.online) == (2, 1)
    work = summarize(zt_status(NETS), network_id=NETS[1].network_id, listing=listing)
    assert (work.network, work.address) == ("Work", "10.200.0.5")


@pytest.mark.parametrize(
    ("listing", "problem"),
    [
        (DeviceListing(P.ZEROTIER, (ProviderDevice("a", "me", "", True, is_local=True),), complete=False), "api_missing"),
        (DeviceListing(P.ZEROTIER, (ProviderDevice("a", "me", "", True, is_local=True),), problem="auth"), "api_failed"),
        (None, "api_failed"),
    ],
)
def test_unknown_device_counts_are_never_zero(listing, problem):
    card = summarize(zt_status(NETS), listing=listing)
    assert card.devices.total is None and card.devices.problem == problem


def test_headscale_card_names_its_server_and_the_shared_app():
    connected = summarize(ProviderStatus(P.HEADSCALE, S.CONNECTED, self_device=ME, peers=PEERS[:1]), headscale_server="https://gaming.example.com")
    assert connected.network == "gaming.example.com" and (connected.devices.total, connected.devices.online) == (2, 2)
    off = summarize(ProviderStatus(P.HEADSCALE, S.DISCONNECTED, technical_detail="tailscaled is using Tailscale"), saved_profile=True)
    assert off.other_active is P.TAILSCALE and off.configured and not off.switch_on
    fresh = summarize(ProviderStatus(P.HEADSCALE, S.DISCONNECTED), setup_incomplete=True)
    assert fresh.setup_incomplete and not fresh.configured


def test_signed_out_and_missing_are_not_configured():
    assert not summarize(ProviderStatus(P.TAILSCALE, S.NEEDS_AUTHENTICATION)).configured
    assert not summarize(ProviderStatus(P.TAILSCALE, S.UNAVAILABLE, installed=False)).configured
    assert summarize(ProviderStatus(P.ZEROTIER, S.DISCONNECTED, networks=NETS)).configured


def test_web_ports_taken_by_another_program_are_reported():
    from big_remote_play.private_network.headscale_server import web_ports_in_use

    def runner(argv, **kwargs):
        if argv[:2] == ["systemctl", "is-active"]:
            return subprocess.CompletedProcess(argv, 3)
        return subprocess.CompletedProcess(argv, 0, stdout="LISTEN 0 4096 0.0.0.0:80 0.0.0.0:*\nLISTEN 0 4096 *:443 *:*\nLISTEN 0 4096 0.0.0.0:8080 0.0.0.0:*\n")

    assert web_ports_in_use(runner) == (80, 443)
    caddy_running = lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0)  # noqa: E731
    assert web_ports_in_use(caddy_running) == ()  # Caddy's own service: a site can be added


def test_the_local_api_follows_the_port_the_helper_chose():
    assert SetupProgress().local_api == "http://127.0.0.1:8080"
    assert SetupProgress(local_listen="127.0.0.1:18080").local_api == "http://127.0.0.1:18080"


@pytest.mark.parametrize(
    "shown",
    [
        "headscale auth register --auth-id hskey-authreq-ABCDEFGHIJKLMNOPQRSTUVWX --user USERNAME",
        "https://headscale.example.com/register/hskey-authreq-ABCDEFGHIJKLMNOPQRSTUVWX",
        "hskey-authreq-ABCDEFGHIJKLMNOPQRSTUVWX",
        "  ABCDEFGHIJKLMNOPQRSTUVWX  ",
    ],
)
def test_the_code_a_device_shows_is_accepted_in_every_form(shown):
    from big_remote_play.private_network.headscale_api import registration_key

    assert registration_key(shown) == "hskey-authreq-ABCDEFGHIJKLMNOPQRSTUVWX"


def test_text_that_is_not_a_code_is_refused_and_the_node_name_is_read():
    from big_remote_play.private_network.headscale_api import registered_name, registration_key
    from big_remote_play.private_network.http import ApiResult

    assert registration_key("hello world") == "" and registration_key("") == ""
    assert registered_name(ApiResult(True, 200, {"node": {"givenName": "pixel-8", "name": "x"}})) == "pixel-8"
    assert registered_name(ApiResult(False, 404)) == ""
