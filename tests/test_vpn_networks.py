"""Private-network pages: dashboard states, joining, disconnecting.

Service adapters are fakes; no VPN client, keyring or API is contacted.
"""

import json
import types

import pytest

import big_remote_play.ui.private_network_view as pnv
from big_remote_play.private_network.models import ConnectionState, OverlayNetwork, PeerDevice, ProviderCapabilities, ProviderId, ProviderStatus, Recovery
from big_remote_play.utils.vpn_accounts import CommandResult, TailscaleConnection, VPNAccountManager


def _gtk_or_skip():
    from gi.repository import Gdk

    if Gdk.Display.get_default() is None:
        pytest.skip("GTK display required")


def _window(**extra):
    calls = []
    window = types.SimpleNamespace(
        system_check=types.SimpleNamespace(tailscale_cmd=lambda: ["tailscale"], zerotier_cmd=lambda: ["zerotier-cli"], has_pacman=lambda: True),
        network_return_label=lambda: "Access shared game",
        return_from_network=lambda: calls.append("return"),
        navigate_to=lambda page: calls.append(("navigate", page)),
        show_toast=lambda message: calls.append(("toast", message)),
        show_vpn_accounts=lambda: None,
        calls=calls,
    )
    for key, value in extra.items():
        setattr(window, key, value)
    return window


class FakeService:
    def __init__(self, status, capabilities=None):
        self._status = status
        self._capabilities = capabilities or ProviderCapabilities(can_join_network=True)
        self.manager = types.SimpleNamespace(pause_tailscale=lambda: CommandResult(0), start_service=lambda unit: CommandResult(0))
        self.granted = False

    def status(self, provider):
        return self._status

    def capabilities(self, provider):
        return self._capabilities

    def grant_zerotier_access(self):
        self.granted = True
        return CommandResult(0)


def _dashboard(monkeypatch, status, capabilities=None, vpn_id="tailscale"):
    _gtk_or_skip()
    from big_remote_play.ui import network_dashboard

    service = FakeService(status, capabilities)
    monkeypatch.setattr(network_dashboard, "default_service", lambda: service)
    monkeypatch.setattr(network_dashboard.Worker, "submit", lambda self, work, done, failed=None, keep_previous=False: done(work()))
    page = network_dashboard.NetworkDashboardPage(vpn_id, _window())
    page.refresh()
    return page, service


def _labels(widget):
    from gi.repository import Gtk

    found = []
    stack = [widget]
    while stack:
        current = stack.pop()
        if isinstance(current, Gtk.Label):
            found.append(current.get_label())
        getattr(current, "get_title", None) and isinstance(current.get_title(), str) and found.append(current.get_title())
        getattr(current, "get_subtitle", None) and isinstance(current.get_subtitle(), str) and found.append(current.get_subtitle())
        child = current.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return " | ".join(found)


CONNECTED = ProviderStatus(
    ProviderId.TAILSCALE,
    ConnectionState.CONNECTED,
    network_name="example.test",
    self_device=PeerDevice("game-pc", ("100.64.0.1",), provider="tailscale", dns_name="game-pc.tail0.ts.net", is_self=True),
    peers=(PeerDevice("notebook", ("100.64.0.2",), online=True), PeerDevice("tv", ("100.64.0.3",), online=False)),
    technical_detail="BackendState: Running",
)


def test_connected_dashboard_shows_the_usable_address_and_the_three_actions(monkeypatch):
    page, _service = _dashboard(monkeypatch, CONNECTED)
    text = _labels(page)
    assert "100.64.0.1" in text and "game-pc.tail0.ts.net" in text
    assert "1 online" in text
    for action in ("Share this PC", "Add device", "Manage network"):
        assert action in text
    assert page._return_to_game.get_visible()
    assert "Connected" in text  # state in words, not only a colour


def test_disconnected_dashboard_offers_to_connect_and_shows_no_address(monkeypatch):
    status = ProviderStatus(ProviderId.TAILSCALE, ConnectionState.DISCONNECTED, self_device=CONNECTED.self_device, recovery=Recovery.RECONNECT, technical_detail="BackendState: Stopped")
    page, _service = _dashboard(monkeypatch, status)
    text = _labels(page)
    assert "Connect this computer" in text
    assert "100.64.0.1" not in text  # a stale address from a stopped client is not usable
    assert not page._return_to_game.get_visible()
    assert "BackendState: Stopped" in text  # technical detail kept for the expander


def test_zerotier_permission_problem_offers_one_explicit_allow_action(monkeypatch):
    status = ProviderStatus(ProviderId.ZEROTIER, ConnectionState.ERROR, recovery=Recovery.GRANT_ACCESS, technical_detail="authtoken.secret is not readable")
    page, service = _dashboard(monkeypatch, status, vpn_id="zerotier")
    assert page.banner.get_revealed()
    assert not service.granted  # nothing happens until the person chooses it
    page._run_recovery()
    assert service.granted


def test_zerotier_waiting_for_approval_shows_the_node_id_to_send(monkeypatch):
    status = ProviderStatus(
        ProviderId.ZEROTIER,
        ConnectionState.NEEDS_AUTHORIZATION,
        network_id="8056c2e21c000001",
        self_device=PeerDevice("pc", (), is_self=True, node_id="a1b2c3d4e5"),
        networks=(OverlayNetwork("8056c2e21c000001", "games", ConnectionState.NEEDS_AUTHORIZATION),),
    )
    page, _service = _dashboard(monkeypatch, status, vpn_id="zerotier")
    text = _labels(page)
    assert "a1b2c3d4e5" in text and "8056c2e21c000001" in text


def test_create_network_is_offered_only_with_the_capability(monkeypatch):
    status = ProviderStatus(ProviderId.ZEROTIER, ConnectionState.DISCONNECTED, recovery=Recovery.JOIN_NETWORK)
    without, _ = _dashboard(monkeypatch, status, ProviderCapabilities(can_join_network=True), vpn_id="zerotier")
    assert "Needs a ZeroTier API token" in _labels(without)
    with_api, _ = _dashboard(monkeypatch, status, ProviderCapabilities(can_join_network=True, can_create_network=True), vpn_id="zerotier")
    assert "A new private ZeroTier network" in _labels(with_api)


def test_network_names_are_never_markup(monkeypatch):
    from gi.repository import Gtk

    status = ProviderStatus(ProviderId.TAILSCALE, ConnectionState.CONNECTED, network_name="<b>Evil</b> & Co", self_device=PeerDevice("<i>pc</i>", ("100.64.0.1",), is_self=True))
    page, _service = _dashboard(monkeypatch, status)
    assert page._title.get_label() == "<b>Evil</b> & Co"
    assert not page._title.get_use_markup()
    rows = []
    stack = [page]
    while stack:
        current = stack.pop()
        if hasattr(current, "get_use_markup") and not isinstance(current, Gtk.Label):
            rows.append(current)
        child = current.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    assert rows and all(not row.get_use_markup() for row in rows)


def test_leaving_a_private_network_is_confirmed_before_anything_runs(monkeypatch):
    # Sign-out is a list row, so an accidental activation must not drop the
    # link the other PC is connecting through.
    from big_remote_play.ui import network_common

    ran = []
    page = types.SimpleNamespace(provider=ProviderId.TAILSCALE, _run_logout=lambda btn: ran.append(btn))
    presented = []
    monkeypatch.setattr(network_common.Adw.AlertDialog, "present", lambda self, parent: presented.append(self))
    from big_remote_play.ui.network_dashboard import NetworkDashboardPage

    NetworkDashboardPage._on_logout(page, object())
    assert not ran
    assert len(presented) == 1
    assert presented[0].get_response_appearance("confirm") == network_common.Adw.ResponseAppearance.DESTRUCTIVE
    presented[0].emit("response", "confirm")
    assert ran


def test_tailscale_disconnect_uses_down_not_logout(monkeypatch):
    from big_remote_play.ui.network_dashboard import NetworkDashboardPage

    commands = []
    manager = VPNAccountManager(types.SimpleNamespace(tailscale_cmd=lambda: ["tailscale"]), runner=lambda argv, timeout=15: commands.append(list(argv)) or CommandResult(0))
    page = types.SimpleNamespace(
        provider=ProviderId.TAILSCALE,
        service=types.SimpleNamespace(manager=manager),
        _toast=lambda message: None,
        _finish_logout=lambda success: commands.append(("finished", success)),
        worker=types.SimpleNamespace(submit=lambda work, done, failed=None, keep_previous=False: done(work())),
    )
    NetworkDashboardPage._run_logout(page, types.SimpleNamespace(set_sensitive=lambda _enabled: None))
    assert ["tailscale", "down"] in commands
    assert all("logout" not in command for command in commands if isinstance(command, list))
    assert ("finished", True) in commands


def test_disconnect_failure_keeps_state_and_success_refreshes_without_erasing_credentials():
    from big_remote_play.ui.network_dashboard import NetworkDashboardPage

    calls = []
    page = types.SimpleNamespace(
        _btn_logout=types.SimpleNamespace(set_sensitive=lambda enabled: calls.append(("enabled", enabled))),
        _toast=lambda message: calls.append(("message", message)),
        refresh=lambda: calls.append("refresh"),
    )
    NetworkDashboardPage._finish_logout(page, False)
    assert calls == [("enabled", True), ("message", "Operation failed")]
    calls.clear()
    NetworkDashboardPage._finish_logout(page, True)
    assert calls == ["refresh"]


def test_join_reports_success_only_when_the_daemon_is_connected(monkeypatch):
    """A reachable auth URL is not membership: the client has the last word."""
    outcomes = []
    page = types.SimpleNamespace(
        vpn_id="tailscale",
        _add_account=False,
        main_window=types.SimpleNamespace(show_toast=lambda _message: None, system_check=types.SimpleNamespace(tailscale_cmd=lambda: ["tailscale"])),
        _c_done=lambda connected, connection=None: outcomes.append((connected, connection)),
        _open_login_url=lambda url: outcomes.append(("url", url)),
        _report_connect_output=lambda line: None,
    )

    class PendingSignIn:
        def connect_tailscale(self, *, login_server, auth_key, on_auth_url, on_output, add_account):
            on_auth_url("https://login.tailscale.com/a/abc")
            return TailscaleConnection(False, backend_state="NeedsLogin", auth_url="https://login.tailscale.com/a/abc")

    monkeypatch.setattr(pnv, "VPNAccountManager", lambda _system_check: PendingSignIn())
    monkeypatch.setattr(pnv.threading, "Thread", lambda target, daemon: types.SimpleNamespace(start=target))
    monkeypatch.setattr(pnv.GLib, "idle_add", lambda callback, *args: callback(*args))

    pnv.ConnectPage._connect_tailnet(page, login_server="", auth_key="")

    assert ("url", "https://login.tailscale.com/a/abc") in outcomes
    connected, connection = outcomes[-1]
    assert connected is False
    assert connection.awaiting_authentication


def test_privileged_helpers_keep_the_users_message_locale(monkeypatch):
    monkeypatch.setenv("LANG", "pt_BR.UTF-8")
    monkeypatch.setenv("LANGUAGE", "pt_BR:pt")
    command = pnv._localized_helper_command("/usr/share/big-remote-play/scripts/helper.sh")
    assert "LC_ALL=C" not in command
    assert "LANGUAGE=" not in command
    assert "LANG=pt_BR.UTF-8" in command
    assert "LANGUAGE=pt_BR:pt" in command
    assert any(value.startswith("TEXTDOMAINDIR=") for value in command)
    assert command[-1].endswith("helper.sh")


def test_resolution_regex_accepts_and_rejects():
    from big_remote_play.ui.guest_view import GuestView

    assert GuestView._RESOLUTION_RE.match("1920x1080")
    assert GuestView._RESOLUTION_RE.match(" 2560 X 1440 ")
    assert GuestView._RESOLUTION_RE.match("junk") is None
    assert GuestView._RESOLUTION_RE.match("1920") is None


def test_previous_networks_never_store_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(pnv, "HISTORY_FILE", str(tmp_path / "history.json"))
    pnv._save_history({"vpn": "headscale", "domain": "vpn.example.test", "auth_key": "hskey-auth-SECRET", "api_key": "SECRET2", "public_ip": "198.51.100.7"})
    raw = (tmp_path / "history.json").read_text()
    assert "SECRET" not in raw and "198.51.100.7" not in raw
    assert json.loads(raw)["history"][0]["domain"] == "vpn.example.test"


def test_legacy_plaintext_secrets_move_to_the_keyring_and_files_become_private(tmp_path):
    import stat

    from big_remote_play.private_network.legacy import migrate_legacy_secrets
    from big_remote_play.utils.secret_store import InMemorySecretBackend, SecretStore

    history = tmp_path / "history.json"
    history.write_text(json.dumps({"history": [{"id": 1, "vpn": "headscale", "domain": "vpn.example.test", "auth_key": "AUTHSECRET", "cf_token": "CFSECRET", "public_ip": "198.51.100.7"}]}))
    history.chmod(0o644)
    backend = InMemorySecretBackend()
    report = migrate_legacy_secrets(SecretStore(backend), tmp_path)
    raw = history.read_text()
    assert "AUTHSECRET" not in raw and "CFSECRET" not in raw
    assert json.loads(raw)["history"][0]["domain"] == "vpn.example.test"  # entry kept
    assert sorted(backend._values.values()) == ["AUTHSECRET", "CFSECRET"]  # value kept, safely
    assert stat.S_IMODE(history.stat().st_mode) == 0o600
    assert report.moved == 2
    assert migrate_legacy_secrets(SecretStore(backend), tmp_path).moved == 0  # idempotent


def test_legacy_migration_keeps_secrets_in_place_when_the_keyring_is_unavailable(tmp_path):
    import stat

    from big_remote_play.private_network.legacy import migrate_legacy_secrets
    from big_remote_play.utils.secret_store import SecretStore, SecretStoreUnavailable

    class Locked:
        def is_available(self):
            return False

        def store(self, *args):
            raise SecretStoreUnavailable("locked")

    history = tmp_path / "history.json"
    history.write_text(json.dumps({"history": [{"id": 1, "vpn": "tailscale", "auth_key": "AUTHSECRET"}]}))
    history.chmod(0o644)
    report = migrate_legacy_secrets(SecretStore(Locked()), tmp_path)
    assert "AUTHSECRET" in history.read_text()  # never lost
    assert stat.S_IMODE(history.stat().st_mode) == 0o600  # but no longer world-readable
    assert report.kept_in_file == 1
