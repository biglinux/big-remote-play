"""Private-network pages: dashboard states, joining, disconnecting.

Service adapters are fakes; no VPN client, keyring or API is contacted.
"""

import json
import types

import pytest

import big_remote_play.ui.private_network_view as pnv
from big_remote_play.utils.vpn_accounts import TailscaleConnection


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
