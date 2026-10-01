"""Behaviour of the GTK-free private-network layer.

Every external boundary is simulated: CLI output comes from a scripted runner,
HTTP from a recording transport, the keyring from the in-memory backend.
Nothing here contacts a real service.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from big_remote_play.integration_contracts import SUNSHINE_DEFAULT_BASE_PORT, sunshine_stream_ports, sunshine_web_ui_port
from big_remote_play.private_network import diagnostics, tailscale, zerotier
from big_remote_play.private_network.credentials import CredentialError, CredentialKind, CredentialStore, mask_secret
from big_remote_play.private_network.headscale_api import HeadscaleApi, registration_key
from big_remote_play.private_network.history import SessionHistory
from big_remote_play.private_network.http import ApiErrorKind, JsonHttpClient
from big_remote_play.private_network.models import ConnectionState, ProviderId, ProviderStatus, Recovery
from big_remote_play.private_network.redaction import RedactingFilter, redact, redact_values
from big_remote_play.private_network.service import PrivateNetworkService, recommended_status
from big_remote_play.private_network.tailscale_api import TailscaleApi
from big_remote_play.private_network.zerotier_api import ZeroTierCentral, choose_subnet
from big_remote_play.utils.secret_store import InMemorySecretBackend, SecretKey, SecretStore
from big_remote_play.utils.vpn_accounts import CommandResult, VPNAccountManager, ZeroTierNetwork

FAKE_TS_TOKEN = "tskey-api-kFAKE1CNTRL-abcdefghijklmnop3FxQ"
FAKE_ZT_TOKEN = "ZtFakeToken0123456789abcdefABCDEF"


class Transport:
    """Records requests and replays (status, JSON) responses by (method, path suffix)."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append(SimpleNamespace(method=method, url=url, headers=dict(headers), body=body, timeout=timeout))
        for (route_method, suffix), response in self.routes.items():
            if route_method == method and url.split("?")[0].endswith(suffix):
                if isinstance(response, Exception):
                    raise response
                status, payload = response
                return status, json.dumps(payload).encode() if payload is not None else b""
        return 404, b'{"message":"not found"}'


# ── redaction ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "auth_key=tskey-auth-kSECRET1-XYZ",
        "Authorization: Bearer SECRETVALUE",
        '{"key": "SECRETVALUE"}',
        "api_token: SECRETVALUE",
        "https://x.example/?token=SECRETVALUE&a=1",
        "tailscale up --auth-key tskey-auth-kSECRET1-XYZ",
    ],
)
def test_redaction_removes_every_known_credential_shape(text):
    assert "SECRET" not in redact(text)


def test_redaction_keeps_file_references_and_ordinary_text():
    assert redact("--auth-key=file:/run/user/1000/key") == "--auth-key=file:/run/user/1000/key"
    assert redact("Provider: tailscale operation: connect result: timeout") == "Provider: tailscale operation: connect result: timeout"
    assert redact_values("value abcdef123 here", ["abcdef123"]) == "value [REDACTED] here"


def test_log_filter_redacts_arguments_and_tracebacks(caplog):
    logger = logging.getLogger("brp-redaction-test")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO, logger="brp-redaction-test"):
        logger.info("joining with %s", "tskey-auth-kSECRET1-XYZ")
        try:
            raise RuntimeError("Authorization: token SECRETVALUE")
        except RuntimeError:
            logger.exception("request failed")
    text = caplog.text
    assert "SECRET" not in text
    assert "[REDACTED]" in text


# ── HTTP client ───────────────────────────────────────────────────────────


def test_http_credentials_travel_in_headers_never_in_the_url():
    transport = Transport({("GET", "/devices"): (200, {"devices": []})})
    client = JsonHttpClient("https://api.example.test/api/v2", auth_headers=lambda: {"Authorization": "Bearer SECRETVALUE"}, transport=transport)
    assert client.get("/tailnet/-/devices").ok
    call = transport.calls[0]
    assert "SECRET" not in call.url
    assert call.headers["Authorization"] == "Bearer SECRETVALUE"
    assert call.timeout > 0


@pytest.mark.parametrize(
    "status,kind",
    [(401, ApiErrorKind.AUTH), (403, ApiErrorKind.FORBIDDEN), (404, ApiErrorKind.NOT_FOUND), (302, ApiErrorKind.REDIRECT), (503, ApiErrorKind.UNAVAILABLE), (429, ApiErrorKind.RATE_LIMITED)],
)
def test_http_errors_are_classified(status, kind):
    transport = Transport({("GET", "/x"): (status, {"message": "token SECRETVALUE rejected"})})
    result = JsonHttpClient("https://api.example.test", auth_headers=lambda: {"Authorization": "token SECRETVALUE"}, transport=transport).get("/x")
    assert not result.ok and result.error is kind
    assert "SECRET" not in result.detail


def test_http_timeout_and_unreachable_server_are_distinct():
    timeout = JsonHttpClient("https://api.example.test", transport=Transport({("GET", "/x"): TimeoutError()})).get("/x")
    offline = JsonHttpClient("https://api.example.test", transport=Transport({("GET", "/x"): ConnectionRefusedError()})).get("/x")
    assert timeout.error is ApiErrorKind.TIMEOUT
    assert offline.error is ApiErrorKind.UNAVAILABLE


@pytest.mark.parametrize("url", ["http://vpn.example.test", "https://user:pw@vpn.example.test", "https://vpn.example.test?x=1", "ftp://vpn.example.test", "https://vpn example.test", "https://"])
def test_http_rejects_insecure_or_ambiguous_urls(url):
    with pytest.raises(ValueError):
        JsonHttpClient(url)


def test_http_allows_plain_http_only_to_loopback_when_requested():
    assert JsonHttpClient("http://127.0.0.1:8080", allow_http_loopback=True).base_url == "http://127.0.0.1:8080"
    with pytest.raises(ValueError):
        JsonHttpClient("http://192.0.2.10:8080", allow_http_loopback=True)


def test_http_path_segments_cannot_escape():
    transport = Transport({})
    central = ZeroTierCentral(FAKE_ZT_TOKEN, flavor="legacy", transport=transport)
    central.set_authorized("../../x", "../abc", True)
    assert "/../" not in transport.calls[0].url


# ── credentials ───────────────────────────────────────────────────────────


def make_store(tmp_path):
    backend = InMemorySecretBackend()
    return CredentialStore(SecretStore(backend), tmp_path / "credentials.json"), backend


def test_credentials_live_in_the_keyring_and_json_has_only_a_mask(tmp_path):
    store, backend = make_store(tmp_path)
    info = store.save(CredentialKind.TAILSCALE_API_TOKEN, FAKE_TS_TOKEN)
    raw = (tmp_path / "credentials.json").read_text()
    assert FAKE_TS_TOKEN not in raw
    assert info.hint == "tskey-api-••••••••3FxQ"
    assert json.loads(raw)["tailscale_api_token"]["default"]["hint"] == info.hint
    assert store.secret(CredentialKind.TAILSCALE_API_TOKEN) == FAKE_TS_TOKEN
    assert stat.S_IMODE((tmp_path / "credentials.json").stat().st_mode) == 0o600
    store.remove(CredentialKind.TAILSCALE_API_TOKEN)
    assert store.secret(CredentialKind.TAILSCALE_API_TOKEN) == ""
    assert store.info(CredentialKind.TAILSCALE_API_TOKEN) is None


@pytest.mark.parametrize(
    "kind,value,reason",
    [
        (CredentialKind.TAILSCALE_API_TOKEN, "tskey-auth-kABC1234567-xyz", "auth_key"),
        (CredentialKind.ZEROTIER_API_TOKEN, "tskey-auth-kABC1234567-xyz", "auth_key"),
        (CredentialKind.TAILSCALE_API_TOKEN, "tskey-client-kABC1234567-xyz", "wrong_kind"),
        (CredentialKind.HEADSCALE_API_KEY, "two words", "whitespace"),
        (CredentialKind.HEADSCALE_API_KEY, "   ", "empty"),
    ],
)
def test_an_auth_key_is_never_accepted_as_an_api_credential(tmp_path, kind, value, reason):
    store, backend = make_store(tmp_path)
    with pytest.raises(CredentialError) as error:
        store.save(kind, value)
    assert error.value.reason == reason
    assert not backend._values


def test_oauth_client_secret_and_id_are_stored_together(tmp_path):
    store, _backend = make_store(tmp_path)
    store.save(CredentialKind.TAILSCALE_OAUTH_CLIENT, "tskey-client-kFAKE-secretsecret", client_id="kFAKEID", tags=("tag:gaming",))
    assert store.oauth_client() == ("kFAKEID", "tskey-client-kFAKE-secretsecret")
    assert store.info(CredentialKind.TAILSCALE_OAUTH_CLIENT).tags == ("tag:gaming",)
    assert "secretsecret" not in (tmp_path / "credentials.json").read_text()


def test_legacy_plain_zerotier_token_moves_into_the_keyring(tmp_path, monkeypatch):
    from big_remote_play import paths

    monkeypatch.setitem(paths.__dict__, "CONFIG_DIR", tmp_path)
    legacy = tmp_path / "zerotier" / "api_token.txt"
    legacy.parent.mkdir()
    legacy.write_text(FAKE_ZT_TOKEN)
    store, backend = make_store(tmp_path)
    assert store.secret(CredentialKind.ZEROTIER_API_TOKEN) == FAKE_ZT_TOKEN
    assert not legacy.exists()
    # Same keyring attributes as earlier versions used.
    assert backend.lookup(SecretKey("zerotier", "api_token", "default")) == FAKE_ZT_TOKEN


def test_mask_never_reveals_short_secrets():
    assert mask_secret("abc") == "••••••••"
    assert "abcdefghijkl" not in mask_secret("abcdefghijklmnopqrstu")


# ── ZeroTier Central ──────────────────────────────────────────────────────


def test_zerotier_detects_legacy_central_and_uses_its_token_header():
    transport = Transport({("GET", "/api/v1/status"): (200, {"user": {"id": "u"}, "apiVersion": 4})})
    central = ZeroTierCentral(FAKE_ZT_TOKEN, transport=transport)
    assert central.detect().ok and central.flavor == "legacy"
    assert transport.calls[0].headers["Authorization"] == f"token {FAKE_ZT_TOKEN}"


def test_zerotier_falls_back_to_new_central_with_a_bearer_token():
    transport = Transport({("GET", "/api/v1/status"): (401, {}), ("GET", "/api/v2/org"): (200, [{"id": "org1"}])})
    central = ZeroTierCentral(FAKE_ZT_TOKEN, transport=transport)
    assert central.detect().ok and central.flavor == "central"
    assert transport.calls[-1].headers["Authorization"] == f"Bearer {FAKE_ZT_TOKEN}"


def test_zerotier_revoked_token_is_an_auth_error():
    transport = Transport({("GET", "/api/v1/status"): (401, {}), ("GET", "/api/v2/org"): (401, {})})
    result = ZeroTierCentral(FAKE_ZT_TOKEN, transport=transport).detect()
    assert result.error is ApiErrorKind.AUTH


def test_zerotier_create_network_is_private_and_assigns_addresses():
    transport = Transport(
        {
            ("POST", "/api/v1/network"): (200, {"id": "8056c2e21c000001", "config": {}}),
            ("POST", "/api/v1/network/8056c2e21c000001"): (200, {"id": "8056c2e21c000001", "config": {"name": "Game <b>Night</b>", "private": True}}),
        }
    )
    network, result = ZeroTierCentral(FAKE_ZT_TOKEN, flavor="legacy", transport=transport).create_network("Game <b>Night</b>", "10.147.17.0/24")
    assert result.ok and network.network_id == "8056c2e21c000001"
    config = json.loads(transport.calls[1].body)["config"]
    assert config["private"] is True
    assert config["v4AssignMode"] == {"zt": True}
    assert config["ipAssignmentPools"] == [{"ipRangeStart": "10.147.17.1", "ipRangeEnd": "10.147.17.254"}]
    assert config["routes"][0]["target"] == "10.147.17.0/24"


def test_zerotier_new_central_creates_inside_a_network_group():
    transport = Transport(
        {
            ("GET", "/api/v2/network-group"): (200, [{"id": "grp1"}]),
            ("POST", "/api/v2/network-group/grp1/network"): (200, {"id": "8056c2e21c000002", "name": "Games", "config": {"private": True}}),
        }
    )
    network, result = ZeroTierCentral(FAKE_ZT_TOKEN, flavor="central", transport=transport).create_network("Games", "10.147.18.0/24")
    assert result.ok and network.name == "Games"
    body = json.loads(transport.calls[-1].body)
    assert body["config"]["v4AssignmentMode"] == {"zt": True}
    assert body["config"]["v4IpAssignmentPools"][0]["ipRangeStart"] == "10.147.18.1"


def test_zerotier_members_parse_both_flavors_and_authorize_correctly():
    legacy = Transport(
        {
            ("GET", "/member"): (200, [{"nodeId": "a1b2c3d4e5", "name": "Notebook", "config": {"authorized": False, "ipAssignments": []}, "lastSeen": 0}]),
            ("POST", "/member/a1b2c3d4e5"): (200, {}),
        }
    )
    central = ZeroTierCentral(FAKE_ZT_TOKEN, flavor="legacy", transport=legacy)
    members, _ = central.list_members("8056c2e21c000001")
    assert members[0].node_id == "a1b2c3d4e5" and not members[0].authorized and not members[0].recently_seen()
    assert central.set_authorized("8056c2e21c000001", "a1b2c3d4e5", True).ok
    assert json.loads(legacy.calls[-1].body) == {"config": {"authorized": True}}

    new = Transport({("GET", "/member"): (200, [{"deviceId": "a1b2c3d4e5", "status": "authorized", "ipv4Assignments": ["10.147.17.9"]}]), ("POST", "/authorize"): (200, {})})
    central = ZeroTierCentral(FAKE_ZT_TOKEN, flavor="central", transport=new)
    members, _ = central.list_members("8056c2e21c000001")
    assert members[0].authorized and members[0].ip_assignments == ("10.147.17.9",)
    central.set_authorized("8056c2e21c000001", "a1b2c3d4e5", True)
    assert new.calls[-1].url.endswith("/member/a1b2c3d4e5/authorize") and new.calls[-1].body is None


def test_zerotier_subnet_avoids_networks_already_in_use():
    for _ in range(20):
        assert choose_subnet(["10.147.17.0/24", "10.147.18.5/16"]) not in {"10.147.17.0/24", "10.147.18.0/24"}


@pytest.mark.parametrize(
    "status,addresses,expected",
    [
        ("OK", ("10.147.17.5/24",), ConnectionState.CONNECTED),
        ("OK", (), ConnectionState.NEEDS_AUTHORIZATION),
        ("ACCESS_DENIED", (), ConnectionState.NEEDS_AUTHORIZATION),
        ("REQUESTING_CONFIGURATION", (), ConnectionState.CONNECTING),
        ("NOT_FOUND", (), ConnectionState.ERROR),
    ],
)
def test_zerotier_network_state_requires_a_managed_address(status, addresses, expected):
    network = ZeroTierNetwork("8056c2e21c000001", "n", status, addresses)
    assert zerotier.network_state(network) is expected


def test_zerotier_unreadable_service_token_asks_for_one_time_access(tmp_path):
    runner = lambda argv, timeout=15: CommandResult(1, "", "zerotier-cli: authtoken.secret not found or readable in /var/lib/zerotier-one")  # noqa: E731
    manager = VPNAccountManager(SimpleNamespace(zerotier_cmd=lambda: ["zerotier-cli"], tailscale_cmd=lambda: ["tailscale"]), runner=runner, metadata_file=tmp_path / "a.json")
    result = zerotier.status(manager)
    assert result.recovery is Recovery.GRANT_ACCESS
    assert result.state is ConnectionState.ERROR


def test_granting_zerotier_access_reads_the_token_through_a_pipe_and_writes_it_privately(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    victim = tmp_path / "victim"
    victim.write_text("untouched")
    target = tmp_path / ".zeroTierOneAuthToken"
    target.symlink_to(victim)  # a planted link must be replaced, not followed
    calls = []

    def runner(argv, timeout=15):
        calls.append(argv)
        return CommandResult(0, "abcdefghijklmnopqrstuvwx\n")

    manager = VPNAccountManager(SimpleNamespace(zerotier_cmd=lambda: ["zerotier-cli"]), runner=runner, metadata_file=tmp_path / "a.json")
    assert manager.grant_zerotier_user_access().returncode == 0
    assert calls == [["pkexec", "/usr/bin/cat", "/var/lib/zerotier-one/authtoken.secret"]]
    assert victim.read_text() == "untouched"
    assert not target.is_symlink() and target.read_text() == "abcdefghijklmnopqrstuvwx"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_granting_zerotier_access_rejects_unexpected_output(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    manager = VPNAccountManager(SimpleNamespace(), runner=lambda argv, timeout=15: CommandResult(0, "../../etc/passwd; rm -rf /"), metadata_file=tmp_path / "a.json")
    assert manager.grant_zerotier_user_access().returncode != 0
    assert not (tmp_path / ".zeroTierOneAuthToken").exists()


# ── Tailscale ─────────────────────────────────────────────────────────────


def ts_status(state="Running", **extra):
    payload = {
        "BackendState": state,
        "AuthURL": "https://login.tailscale.com/a/abc" if state == "NeedsLogin" else "",
        "CurrentTailnet": {"Name": "example.test", "MagicDNSSuffix": "tail0.ts.net"},
        "Self": {"ID": "nSELF", "HostName": "game-pc", "DNSName": "game-pc.tail0.ts.net.", "TailscaleIPs": ["100.64.0.1", "fd7a::1"], "Online": True, "UserID": 1},
        "User": {"1": {"LoginName": "player@example.test"}},
        "Peer": {
            "k1": {"ID": "n1", "HostName": "notebook", "DNSName": "notebook.tail0.ts.net.", "TailscaleIPs": ["100.64.0.2"], "Online": True, "OS": "linux"},
            "k2": {"ID": "n2", "HostName": "old-pc", "DNSName": "old-pc.tail0.ts.net.", "TailscaleIPs": ["100.64.0.3"], "Online": True, "Expired": True},
            "k3": {"ID": "n3", "HostName": "tv", "TailscaleIPs": ["100.64.0.4"], "Online": False},
        },
    }
    payload.update(extra)
    return json.dumps(payload)


@pytest.mark.parametrize(
    "backend,state,recovery",
    [
        ("Running", ConnectionState.CONNECTED, None),
        ("NeedsLogin", ConnectionState.NEEDS_AUTHENTICATION, Recovery.SIGN_IN),
        ("Stopped", ConnectionState.DISCONNECTED, Recovery.RECONNECT),
        ("NeedsMachineAuth", ConnectionState.NEEDS_AUTHORIZATION, Recovery.AUTHORIZE_DEVICE),
        ("Starting", ConnectionState.CONNECTING, None),
        ("SomethingNew", ConnectionState.ERROR, None),
    ],
)
def test_tailscale_only_running_is_connected(backend, state, recovery):
    status = tailscale.parse_status(ts_status(backend))
    assert status.state is state and status.recovery is recovery
    assert f"BackendState: {backend}" in status.technical_detail
    assert status.connected is (backend == "Running")
    assert bool(status.reachable_address) is (backend == "Running")


def test_tailscale_status_reports_real_addresses_and_marks_expired_peers_offline():
    status = tailscale.parse_status(ts_status())
    assert status.reachable_address == "100.64.0.1"
    assert status.self_device.dns_name == "game-pc.tail0.ts.net"
    assert status.account == "player@example.test"
    by_name = {peer.name: peer for peer in status.peers}
    assert by_name["notebook"].online is True
    assert by_name["old-pc"].online is False and by_name["old-pc"].expired
    assert status.peers[0].name == "notebook"  # online first
    assert status.device_count == 4


def test_tailscale_stopped_client_offers_no_peers_as_reachable():
    assert tailscale.parse_status(ts_status("Stopped")).peers == ()


@pytest.mark.parametrize(
    "output,kind,latency,relay",
    [
        ("pong from notebook (100.64.0.2) via 192.0.2.4:41641 in 12ms", "direct", 12.0, ""),
        ("pong from notebook (100.64.0.2) via DERP(fra) in 48ms\npong from notebook (100.64.0.2) via DERP(fra) in 41ms", "relay", 41.0, "fra"),
        ("pong from notebook (100.64.0.2) via DERP(fra) in 48ms\npong from notebook (100.64.0.2) via 192.0.2.4:41641 in 9ms", "direct", 9.0, ""),
        ("pong from notebook (100.64.0.2, 42001) via peer-relay(192.0.2.9:7777:vni:3) in 20ms", "peer_relay", 20.0, ""),
        # Real output from a lab node on 2026-09-28: sub-millisecond paths print "0s".
        ("pong from game-pc (100.64.0.1) via 10.200.0.2:41641 in 0s", "direct", 0.0, ""),
        ("pong from pc (100.64.0.1) via 192.0.2.4:41641 in 523µs", "direct", 0.523, ""),
        ("pong from pc (100.64.0.1) via DERP(fra) in 1.2s", "relay", 1200.0, "fra"),
    ],
)
def test_tailscale_ping_reports_path_and_measured_latency(output, kind, latency, relay):
    path = tailscale.parse_ping(output)
    assert path.reachable and path.kind == kind and path.latency_ms == latency and path.relay == relay


@pytest.mark.parametrize("output", ['ping "100.64.0.2" timed out', "no reply", "peer's node key has expired", ""])
def test_tailscale_ping_failure_is_not_a_path(output):
    assert not tailscale.parse_ping(output, 1).reachable


def test_tailscale_ping_rejects_option_injection():
    calls = []
    cli = tailscale.TailscaleCli(["tailscale"], lambda argv, timeout: calls.append(argv) or CommandResult(0))
    assert not cli.ping("--help").reachable
    assert not cli.ping("a b").reachable
    assert calls == []


def test_tailscale_oauth_exchange_uses_a_form_body_and_caches_the_token():
    clock = [0.0]
    transport = Transport({("POST", "/oauth/token"): (200, {"access_token": "ACCESS1", "expires_in": 3600}), ("GET", "/devices"): (200, {"devices": []})})
    api = TailscaleApi(oauth_client_id="kFAKEID", oauth_client_secret="tskey-client-SECRET", transport=transport, clock=lambda: clock[0])
    api.test()
    api.test()
    exchanges = [call for call in transport.calls if call.url.endswith("/oauth/token")]
    assert len(exchanges) == 1
    assert b"client_secret=tskey-client-SECRET" in exchanges[0].body and "SECRET" not in exchanges[0].url
    assert b"grant_type=client_credentials" in exchanges[0].body
    assert transport.calls[-1].headers["Authorization"] == "Bearer ACCESS1"
    clock[0] = 4000.0
    api.test()
    assert len([call for call in transport.calls if call.url.endswith("/oauth/token")]) == 2


def test_tailscale_invites_need_a_user_owned_token():
    transport = Transport({})
    api = TailscaleApi(oauth_client_id="id", oauth_client_secret="tskey-client-x", transport=transport)
    url, result = api.invite_user("friend@example.test")
    assert url == "" and result.error is ApiErrorKind.FORBIDDEN
    url, result = api.share_device("d1", email="friend@example.test")
    assert url == "" and result.error is ApiErrorKind.FORBIDDEN
    assert transport.calls == []


def test_tailscale_share_machine_and_invite_user_are_different_requests():
    transport = Transport(
        {
            ("POST", "/device/dev123/device-invites"): (200, [{"inviteUrl": "https://login.tailscale.com/admin/invite/share1"}]),
            ("POST", "/tailnet/-/user-invites"): (200, [{"inviteUrl": "https://login.tailscale.com/admin/invite/user1"}]),
        }
    )
    api = TailscaleApi(api_token=FAKE_TS_TOKEN, transport=transport)
    share, _ = api.share_device("dev123", email="friend@example.test")
    invite, _ = api.invite_user("friend@example.test")
    assert share.endswith("share1") and invite.endswith("user1")
    assert json.loads(transport.calls[0].body) == [{"multiUse": False, "allowExitNode": False, "email": "friend@example.test"}]
    assert json.loads(transport.calls[1].body) == [{"role": "member", "email": "friend@example.test"}]


@pytest.mark.parametrize("email", ["", "no-at-sign", "a@b", "<script>@x.test", "a b@example.test"])
def test_tailscale_invalid_email_is_refused_before_any_request(email):
    transport = Transport({})
    assert TailscaleApi(api_token=FAKE_TS_TOKEN, transport=transport).invite_user(email)[1].error is ApiErrorKind.BAD_REQUEST
    assert transport.calls == []


def test_tailscale_auth_key_is_single_use_preapproved_and_short_lived_by_default():
    transport = Transport({("POST", "/tailnet/-/keys"): (200, {"key": "tskey-auth-kNEW-secret", "id": "k1"})})
    key, result = TailscaleApi(api_token=FAKE_TS_TOKEN, transport=transport).create_auth_key()
    body = json.loads(transport.calls[0].body)
    assert key.startswith("tskey-auth-") and result.ok
    assert body["capabilities"]["devices"]["create"] == {"reusable": False, "ephemeral": False, "preauthorized": True}
    assert body["expirySeconds"] == 3600


def test_tailscale_oauth_auth_keys_require_tags():
    transport = Transport({("POST", "/oauth/token"): (200, {"access_token": "A", "expires_in": 3600})})
    key, result = TailscaleApi(oauth_client_id="id", oauth_client_secret="tskey-client-x", transport=transport).create_auth_key()
    assert key == "" and result.error is ApiErrorKind.BAD_REQUEST


# ── Headscale ─────────────────────────────────────────────────────────────


def test_headscale_version_and_health_need_no_credential():
    transport = Transport({("GET", "/version"): (200, {"version": "v0.29.4"}), ("GET", "/health"): (200, {"status": "pass"})})
    api = HeadscaleApi("https://vpn.example.test", "", transport=transport)
    assert api.version()[0] == "v0.29.4"
    assert api.health().ok
    assert all("Authorization" not in call.headers for call in transport.calls)


def test_headscale_invalid_api_key_and_offline_server_are_explained():
    denied = HeadscaleApi("https://vpn.example.test", "bad", transport=Transport({("GET", "/api/v1/user"): (401, {"message": "Unauthorized"})})).test()
    offline = HeadscaleApi("https://vpn.example.test", "k", transport=Transport({("GET", "/api/v1/user"): ConnectionRefusedError()})).test()
    assert denied.error is ApiErrorKind.AUTH
    assert offline.error is ApiErrorKind.UNAVAILABLE


def test_headscale_preauth_key_uses_the_numeric_user_id_and_expires():
    transport = Transport({("POST", "/api/v1/preauthkey"): (200, {"preAuthKey": {"key": "hs-preauth-new"}})})
    key, result = HeadscaleApi("https://vpn.example.test", "api-key", transport=transport).create_preauth_key("7")
    body = json.loads(transport.calls[0].body)
    assert key == "hs-preauth-new" and result.ok
    assert body["user"] == "7" and body["reusable"] is False and body["expiration"].endswith("Z")
    assert transport.calls[0].headers["Authorization"] == "Bearer api-key"
    assert HeadscaleApi("https://vpn.example.test", "k", transport=Transport({})).create_preauth_key("friends")[1].error is ApiErrorKind.BAD_REQUEST


def test_headscale_registration_uses_the_user_name_and_the_current_endpoint_first():
    auth_id = "hskey-authreq-AbCdEfGhIjKlMnOpQrStUvWx"
    current = Transport({("POST", "/api/v1/auth/register"): (200, {"node": {"id": "4"}})})
    assert HeadscaleApi("https://vpn.example.test", "k", transport=current).register_node("ana", f"https://vpn.example.test/register/{auth_id}").ok
    assert json.loads(current.calls[0].body) == {"user": "ana", "authId": auth_id}
    older = Transport({("POST", "/api/v1/node/register"): (200, {"node": {"id": "4"}})})
    assert HeadscaleApi("https://vpn.example.test", "k", transport=older).register_node("ana", auth_id).ok
    assert older.calls[-1].url.endswith(f"/api/v1/node/register?user=ana&key={auth_id}")


def test_headscale_nodes_and_users_parse_string_ids():
    transport = Transport(
        {
            ("GET", "/api/v1/node"): (
                200,
                {"nodes": [{"id": "3", "givenName": "desk", "user": {"name": "ana"}, "ipAddresses": ["100.64.0.9"], "online": True, "availableRoutes": ["192.168.1.0/24"]}]},
            ),
            ("GET", "/api/v1/user"): (200, {"users": [{"id": "1", "name": "ana"}]}),
        }
    )
    api = HeadscaleApi("https://vpn.example.test", "k", transport=transport)
    node = api.nodes()[0][0]
    assert node.node_id == "3" and node.online and node.available_routes == ("192.168.1.0/24",)
    assert api.users()[0][0].user_id == "1"


@pytest.mark.parametrize(
    "text,key",
    [
        ("https://vpn.example.test/register/AbCdEf123456", "AbCdEf123456"),
        ("AbCdEf123456", "AbCdEf123456"),
        ("https://vpn.example.test/register/mkey:0123456789abcdef", "mkey:0123456789abcdef"),
        ("https://vpn.example.test/register/hskey-authreq-AbCdEfGhIjKlMnOpQrStUvWx", "hskey-authreq-AbCdEfGhIjKlMnOpQrStUvWx"),
        ("rm -rf /", ""),
    ],
)
def test_headscale_registration_key_is_extracted_from_the_sign_in_url(text, key):
    assert registration_key(text) == key


# ── History ───────────────────────────────────────────────────────────────


def history(tmp_path, now):
    return SessionHistory(tmp_path / "history" / "sessions.json", clock=lambda: now[0])


def test_history_records_a_completed_session_with_duration(tmp_path):
    now = [1_000_000.0]
    store = history(tmp_path, now)
    session = store.start(host_name="Desktop", host_address="100.64.0.2", port=47989, provider="tailscale", resolution="1920x1080", fps=60)
    now[0] += 5700
    store.finish(session)
    record = store.sessions()[0]
    assert record.duration_seconds == 5700 and record.provider == "tailscale" and record.fps == 60
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_history_drops_a_stream_that_failed_immediately(tmp_path):
    now = [1_000_000.0]
    store = history(tmp_path, now)
    session = store.start(host_name="Desktop", host_address="100.64.0.2", port=47989)
    now[0] += 3
    store.finish(session, failed=True)
    assert store.sessions() == []


def test_history_retention_clear_and_reconnect_suggestions(tmp_path):
    now = [100 * 86_400.0]
    store = history(tmp_path, now)
    old = store.start(host_name="Old", host_address="192.0.2.1", port=47989)
    store.finish(old)
    now[0] += 40 * 86_400
    store.start(host_name="Desk", host_address="100.64.0.2", port=47989, provider="tailscale")
    store.start(host_name="Desk", host_address="100.64.0.2", port=47989, provider="tailscale")
    assert len(store.sessions()) == 3
    store.set_retention(30)
    assert [record.host_name for record in store.sessions()] == ["Desk", "Desk"]
    assert [host.address for host in store.recent_hosts()] == ["100.64.0.2"]
    store.clear()
    assert store.sessions() == []
    assert store.retention_days == 30


def test_history_keeps_only_allowlisted_sanitized_fields(tmp_path):
    now = [1.0]
    store = history(tmp_path, now)
    store.start(host_name="<b>PC</b>\x00\x1b[31m", host_address="100.64.0.2", port=47989, app="auth_key=tskey-auth-kSECRET-x")
    raw = store.path.read_text()
    assert "SECRET" not in raw and "\x00" not in raw and "\x1b" not in raw
    assert set(json.loads(raw)["sessions"][0]) <= {
        "id", "started_at", "ended_at", "host_name", "host_address", "port", "provider", "client_name", "app", "resolution", "fps", "bitrate_kbps", "codec", "connection_path",
    }  # fmt: skip


@pytest.mark.parametrize("address", ["-oProxyCommand=x", "a b", "http://x/y", "x@y", ""])
def test_history_rejects_hostile_addresses(tmp_path, address):
    with pytest.raises(ValueError):
        history(tmp_path, [1.0]).start(host_name="x", host_address=address, port=47989)


def test_history_does_not_overwrite_a_malformed_file(tmp_path):
    store = history(tmp_path, [1.0])
    store.path.parent.mkdir(parents=True)
    store.path.write_text("{not json")
    with pytest.raises(ValueError):
        store.start(host_name="x", host_address="192.0.2.1", port=47989)
    assert store.path.read_text() == "{not json"
    assert store.sessions() == []


# ── Service ───────────────────────────────────────────────────────────────


class FakeSystemCheck:
    def __init__(self, tailscale=True, zerotier=True):
        self._tailscale, self._zerotier = tailscale, zerotier

    def tailscale_cmd(self):
        return ["tailscale"]

    def zerotier_cmd(self):
        return ["zerotier-cli"]

    def has_tailscale(self):
        return self._tailscale

    def has_zerotier(self):
        return self._zerotier

    def is_zerotier_running(self):
        return True


def scripted(responses):
    def runner(argv, timeout=15):
        for prefix, result in responses.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return result
        return CommandResult(1, "", "unexpected")

    return runner


def make_service(tmp_path, responses, **kwargs):
    manager = VPNAccountManager(FakeSystemCheck(), runner=scripted(responses), metadata_file=tmp_path / "accounts.json")
    store, _ = make_store(tmp_path)
    return PrivateNetworkService(
        FakeSystemCheck(**kwargs),
        manager=manager,
        credentials=store,
        history=SessionHistory(tmp_path / "h.json"),
        sunshine_probe=lambda address, port: SimpleNamespace(listening=address == "100.64.0.2", answered=address == "100.64.0.2"),
    )


ZT_OK = json.dumps([{"nwid": "8056c2e21c000001", "name": "games", "status": "OK", "assignedAddresses": ["10.147.17.5/24"], "type": "PRIVATE"}])


def test_recommendation_keeps_a_working_connection_even_when_another_provider_was_saved():
    statuses = [
        ProviderStatus(ProviderId.TAILSCALE, ConnectionState.DISCONNECTED, installed=True),
        ProviderStatus(ProviderId.ZEROTIER, ConnectionState.CONNECTED, installed=True),
        ProviderStatus(ProviderId.HEADSCALE, ConnectionState.UNAVAILABLE, installed=False),
    ]
    assert recommended_status(statuses, ProviderId.TAILSCALE).provider is ProviderId.ZEROTIER


def test_recommendation_reuses_an_installed_provider_and_uses_saved_choice_only_as_a_tiebreaker():
    statuses = [
        ProviderStatus(ProviderId.TAILSCALE, ConnectionState.DISCONNECTED, installed=True),
        ProviderStatus(ProviderId.ZEROTIER, ConnectionState.DISCONNECTED, installed=True),
        ProviderStatus(ProviderId.HEADSCALE, ConnectionState.UNAVAILABLE, installed=False),
    ]
    assert recommended_status(statuses, ProviderId.ZEROTIER).provider is ProviderId.ZEROTIER
    assert recommended_status(statuses).provider is ProviderId.TAILSCALE


def test_recommendation_defaults_to_the_guided_option_when_nothing_is_installed():
    statuses = [ProviderStatus(provider, ConnectionState.UNAVAILABLE, installed=False) for provider in ProviderId]
    assert recommended_status(statuses).provider is ProviderId.TAILSCALE


def test_service_offers_only_peers_of_connected_providers_online_first(tmp_path):
    service = make_service(
        tmp_path,
        {
            ("tailscale", "status"): CommandResult(0, ts_status()),
            ("tailscale", "switch"): CommandResult(1, "", "no"),
            ("zerotier-cli", "-j", "listnetworks"): CommandResult(0, ZT_OK),
            ("zerotier-cli", "-j", "info"): CommandResult(0, json.dumps({"address": "a1b2c3d4e5", "online": True})),
        },
    )
    statuses = service.overview()
    by_provider = {status.provider: status for status in statuses}
    assert by_provider[ProviderId.TAILSCALE].connected
    assert by_provider[ProviderId.HEADSCALE].state is ConnectionState.DISCONNECTED  # one daemon, used by Tailscale
    assert by_provider[ProviderId.ZEROTIER].reachable_address == "10.147.17.5"
    candidates = service.candidate_hosts(statuses)
    assert [c.address for c in candidates][:1] == ["100.64.0.2"]
    assert all(c.provider == "tailscale" for c in candidates)
    assert candidates[0].as_host()["dns_name"] == "notebook.tail0.ts.net"
    endpoints = service.share_endpoints(statuses)
    assert {(provider, device.best_address) for provider, device in endpoints} == {(ProviderId.TAILSCALE, "100.64.0.1"), (ProviderId.ZEROTIER, "10.147.17.5")}


def test_service_headscale_profile_owns_the_shared_daemon(tmp_path):
    service = make_service(tmp_path, {("tailscale", "status"): CommandResult(0, ts_status()), ("tailscale", "switch"): CommandResult(0, json.dumps([{"id": "p1", "selected": True}]))})
    service.manager.set_tailscale_metadata("p1", provider="headscale", login_server="https://vpn.example.test")
    assert service.status(ProviderId.HEADSCALE).connected
    assert not service.status(ProviderId.TAILSCALE).connected
    assert service.headscale_server() == "https://vpn.example.test"


def test_service_diagnosis_separates_network_host_and_sunshine(tmp_path):
    from big_remote_play.private_network.models import HostCandidate

    service = make_service(
        tmp_path,
        {
            ("tailscale", "status"): CommandResult(0, ts_status()),
            ("tailscale", "switch"): CommandResult(1, "", "no"),
            ("tailscale", "ping"): CommandResult(0, "pong from notebook (100.64.0.2) via DERP(fra) in 44ms"),
        },
    )
    ready = service.diagnose(HostCandidate("notebook", "100.64.0.2", SUNSHINE_DEFAULT_BASE_PORT, provider="tailscale"))
    assert ready.ready and ready.path.kind == "relay" and ready.path.latency_ms == 44
    missing = service.diagnose(HostCandidate("tv", "100.64.0.4", SUNSHINE_DEFAULT_BASE_PORT, provider="tailscale"))
    assert not missing.ready and missing.problem in {"host_offline", "sunshine_missing"}


def test_service_reports_network_down_before_probing_the_host(tmp_path):
    from big_remote_play.private_network.models import HostCandidate

    probes = []
    service = make_service(tmp_path, {("tailscale", "status"): CommandResult(0, ts_status("Stopped")), ("tailscale", "switch"): CommandResult(1, "", "no")})
    service._probe_sunshine = lambda *args: probes.append(args)
    result = service.diagnose(HostCandidate("notebook", "100.64.0.2", 47989, provider="tailscale"))
    assert result.problem == "network_down" and probes == []


def test_capabilities_follow_the_configured_credential(tmp_path):
    service = make_service(tmp_path, {})
    assert not service.capabilities(ProviderId.ZEROTIER).can_authorize_member
    assert service.capabilities(ProviderId.ZEROTIER).can_join_network
    service.credentials.save(CredentialKind.ZEROTIER_API_TOKEN, FAKE_ZT_TOKEN)
    assert service.capabilities(ProviderId.ZEROTIER).can_create_network
    assert not service.capabilities(ProviderId.TAILSCALE).can_share_machine
    service.credentials.save(CredentialKind.TAILSCALE_API_TOKEN, FAKE_TS_TOKEN)
    assert service.capabilities(ProviderId.TAILSCALE).can_share_machine


# ── contracts and diagnostics ─────────────────────────────────────────────


@pytest.mark.parametrize("base", [47989, 50000])
def test_sunshine_stream_ports_follow_the_base_and_never_include_the_web_ui(base):
    ports = sunshine_stream_ports(base)
    assert ports["tcp"] == (base - 5, base, base + 21)
    assert ports["udp"] == (base + 9, base + 10, base + 11)
    assert sunshine_web_ui_port(base) not in ports["tcp"] + ports["udp"]


def test_interface_facts_detect_dual_stack_cgnat_and_overlays():
    payload = json.dumps(
        [
            {"ifname": "eth0", "flags": ["UP"], "addr_info": [{"family": "inet", "local": "100.72.1.5"}, {"family": "inet6", "local": "2001:db8::5", "scope": "global"}]},
            {"ifname": "tailscale0", "flags": ["UP"], "addr_info": [{"family": "inet", "local": "100.64.0.1"}]},
            {"ifname": "ztabc", "flags": ["UP"], "addr_info": [{"family": "inet", "local": "10.147.17.5"}]},
            {"ifname": "docker0", "flags": ["UP"], "addr_info": [{"family": "inet", "local": "100.65.0.1"}]},
        ]
    )
    facts = diagnostics.parse_ip_json(payload)
    assert facts.cgnat_suspected  # eth0, not the Tailscale interface
    assert facts.overlay_interfaces == ("tailscale0", "ztabc")
    assert facts.ipv4 == ("100.72.1.5",)
    # 2001:db8::/32 is documentation space, not globally routable.
    assert facts.stack == "ipv4"


def test_tailscale_interface_alone_is_not_cgnat():
    payload = json.dumps(
        [
            {"ifname": "tailscale0", "flags": ["UP"], "addr_info": [{"family": "inet", "local": "100.64.0.1"}]},
            {"ifname": "wlan0", "flags": ["UP"], "addr_info": [{"family": "inet", "local": "192.168.1.9"}]},
        ]
    )
    assert not diagnostics.parse_ip_json(payload).cgnat_suspected


@pytest.mark.parametrize("value,ok", [("100.64.0.2", True), ("fd7a::1", True), ("game-pc.tail0.ts.net", True), ("-oProxyCommand", False), ("a b", False), ("x/y", False), ("", False)])
def test_diagnostics_accept_only_hosts(value, ok):
    assert diagnostics.valid_host(value) is ok


@pytest.mark.parametrize("ms,label", [(12, "excellent"), (30, "excellent"), (45, "good"), (120, "high"), (None, "unknown")])
def test_latency_label_is_documented_heuristic(ms, label):
    assert diagnostics.classify_latency(ms) == label


def test_secret_files_are_created_owner_only(tmp_path):
    from big_remote_play.utils.vpn_accounts import _write_private_file

    target = tmp_path / "token"
    _write_private_file(target, "x")
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
    assert Path(target).read_text() == "x"


def test_clear_all_data_removes_every_api_credential_and_migrated_legacy_secret(tmp_path, monkeypatch):
    """Preferences → Clear all data must not leave any credential behind."""
    from big_remote_play import paths
    from big_remote_play.private_network.legacy import migrate_legacy_secrets
    from big_remote_play.ui import preferences
    import big_remote_play.utils.secret_store as secret_store

    monkeypatch.setitem(paths.__dict__, "CONFIG_DIR", tmp_path)
    backend = InMemorySecretBackend()
    monkeypatch.setattr(secret_store, "LibsecretBackend", lambda: backend)
    store = CredentialStore(SecretStore(backend))
    store.save(CredentialKind.ZEROTIER_API_TOKEN, FAKE_ZT_TOKEN)
    store.save(CredentialKind.TAILSCALE_API_TOKEN, FAKE_TS_TOKEN)
    store.save(CredentialKind.HEADSCALE_API_KEY, "hs-api-key-value", scope="https://vpn.example.test")
    legacy = tmp_path / "private_network" / "history.json"
    legacy.write_text(json.dumps({"history": [{"id": 1, "vpn": "headscale", "auth_key": "OLDSECRET"}]}))
    migrate_legacy_secrets(SecretStore(backend), legacy.parent)
    assert len(backend._values) == 4

    preferences.PreferencesWindow._wipe_keyring_secrets(SimpleNamespace())

    assert backend._values == {}


@pytest.mark.parametrize("control,expected", [("https://vpn.example.test", ProviderId.HEADSCALE), ("https://controlplane.tailscale.com", ProviderId.TAILSCALE)])
def test_tailnet_owner_follows_the_clients_control_url_when_nothing_was_recorded(tmp_path, control, expected):
    """A tailnet joined outside Big Remote Play is classified by its control server."""
    service = make_service(
        tmp_path,
        {
            ("tailscale", "status"): CommandResult(0, ts_status()),
            ("tailscale", "switch"): CommandResult(0, json.dumps([{"id": "p1", "selected": True}])),
            ("tailscale", "debug", "prefs"): CommandResult(0, json.dumps({"ControlURL": control})),
        },
    )
    assert service.status(expected).connected
    other = ProviderId.TAILSCALE if expected is ProviderId.HEADSCALE else ProviderId.HEADSCALE
    assert not service.status(other).connected
    assert service.headscale_server() == (control if expected is ProviderId.HEADSCALE else "")
