"""Private-network fetch helpers (FABLE items 2 & 11).

Covers the Tailscale/ZeroTier dispatch split and the header-based ZeroTier API
helper that keeps the token out of process argv.
"""

import types

import big_remote_play.ui.private_network_view as pnv
from big_remote_play.ui.private_network_view import CreatePage, _zt_api_get
from big_remote_play.utils.vpn_accounts import CommandResult, TailscaleConnection, VPNAccountManager


def _fake_completed(stdout: str, returncode: int = 0):
    return types.SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)


def test_tailscale_rows_parse_self_and_peers(monkeypatch):
    payload = {
        "Self": {"DNSName": "my-pc.tail.ts.net.", "TailscaleIPs": ["100.64.0.1"]},
        "Peer": {
            "abc": {"DNSName": "friend.tail.ts.net.", "TailscaleIPs": ["100.64.0.2"], "Online": True},
        },
    }
    monkeypatch.setattr(pnv.subprocess, "run", lambda *a, **k: _fake_completed(__import__("json").dumps(payload)))

    rows = CreatePage._fetch_tailscale_rows()

    assert rows[0]["is_self"] is True
    assert rows[0]["title"] == "my-pc"
    assert rows[0]["subtitle"] == "100.64.0.1"
    assert rows[1]["title"] == "friend"
    assert rows[1]["online"] is True


def test_tailscale_rows_empty_on_error(monkeypatch):
    monkeypatch.setattr(pnv.subprocess, "run", lambda *a, **k: _fake_completed("", returncode=1))
    assert CreatePage._fetch_tailscale_rows() == []


def test_disconnect_failure_keeps_state_and_success_rebuilds_without_erasing_credentials(monkeypatch):
    calls = []
    page = types.SimpleNamespace(
        vpn_id="zerotier",
        _btn_logout=types.SimpleNamespace(set_sensitive=lambda enabled: calls.append(("enabled", enabled))),
        main_window=types.SimpleNamespace(show_toast=lambda message: calls.append(("message", message))),
        _rebuild=lambda: calls.append("rebuild"),
    )
    monkeypatch.setattr(pnv, "_clear_zerotier_api_token", lambda: calls.append("clear token"))
    CreatePage._finish_logout(page, False)
    assert calls == [("enabled", True), ("message", "Operation failed")]
    calls.clear()
    CreatePage._finish_logout(page, True)
    assert calls == ["rebuild"]


def test_leaving_a_private_network_is_confirmed_before_anything_runs(monkeypatch):
    # Sign-out is a list row now, so an accidental activation must not drop the
    # link the other PC is connecting through.
    ran = []
    page = types.SimpleNamespace(vpn_id="tailscale", _run_logout=lambda btn: ran.append(btn))
    presented = []
    monkeypatch.setattr(pnv.Adw.AlertDialog, "present", lambda self, parent: presented.append(self))

    CreatePage._on_logout(page, object())

    assert not ran
    assert len(presented) == 1
    assert presented[0].get_response_appearance("leave") == pnv.Adw.ResponseAppearance.DESTRUCTIVE


def test_my_network_sends_tailscale_sign_in_to_the_join_page(monkeypatch, fake_home):
    """Signing in lives on one page: "My network" points at it, and does not
    ask for the same auth key a second time."""
    import pytest
    from gi.repository import Gdk

    if Gdk.Display.get_default() is None:
        pytest.skip("GTK display required")
    monkeypatch.setattr(CreatePage, "_check_logged_in", lambda _self: False)
    window = types.SimpleNamespace(
        system_check=types.SimpleNamespace(has_tailscale=lambda: True, has_zerotier=lambda: True, has_docker=lambda: True),
        network_return_label=lambda: "Access shared game",
        return_from_network=lambda: None,
        navigate_to=lambda page: None,
    )
    page = CreatePage("tailscale", window)

    assert page._defers_to_join
    assert not hasattr(page, "_btn_action")
    assert not hasattr(page, "_e_authkey")


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
        _e_server=types.SimpleNamespace(get_text=lambda: ""),
        _e_key=types.SimpleNamespace(get_text=lambda: ""),
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


def test_zerotier_rows_use_api_helper(monkeypatch):
    monkeypatch.setattr(pnv, "_get_zerotier_api_token", lambda: "secret-token")
    monkeypatch.setattr(pnv, "_zt_api_get", lambda path, token, **k: [{"id": "abc123", "config": {"name": "game-net"}}])

    rows = CreatePage._fetch_zerotier_rows()

    assert rows == [{"title": "game-net", "subtitle": "ID: abc123", "icon": "brp-zerotier-symbolic", "network_id": "abc123"}]


def test_zerotier_rows_empty_without_token(monkeypatch):
    monkeypatch.setattr(pnv, "_get_zerotier_api_token", lambda: "")
    assert CreatePage._fetch_zerotier_rows() == []


def test_zt_api_get_sends_token_in_header_not_argv(monkeypatch):
    captured = {}

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"[]"

    def fake_urlopen(request, timeout=10.0):
        captured["headers"] = dict(request.header_items())
        captured["url"] = request.full_url
        return _FakeResponse()

    monkeypatch.setattr(pnv.urllib.request, "urlopen", fake_urlopen)

    result = _zt_api_get("/network", "secret-token")

    assert result == []
    # Token travels as an HTTP header (urllib title-cases header keys).
    assert captured["headers"].get("Authorization") == "token secret-token"
    assert "secret-token" not in captured["url"]


def test_zt_api_get_none_on_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr(pnv.urllib.request, "urlopen", boom)
    assert _zt_api_get("/network", "tok") is None


def test_resolution_regex_accepts_and_rejects():
    from big_remote_play.ui.guest_view import GuestView

    assert GuestView._RESOLUTION_RE.match("1920x1080")
    assert GuestView._RESOLUTION_RE.match(" 2560 X 1440 ")
    assert GuestView._RESOLUTION_RE.match("junk") is None
    assert GuestView._RESOLUTION_RE.match("1920") is None


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


def test_tailscale_disconnect_uses_down_not_logout(monkeypatch):
    commands = []
    page = types.SimpleNamespace(
        vpn_id="tailscale",
        main_window=types.SimpleNamespace(
            show_toast=lambda _message: None,
            system_check=types.SimpleNamespace(tailscale_cmd=lambda: ["tailscale"]),
        ),
        _finish_logout=lambda success: commands.append(("finished", success)),
    )
    button = types.SimpleNamespace(set_sensitive=lambda _enabled: None)
    manager = VPNAccountManager(
        types.SimpleNamespace(tailscale_cmd=lambda: ["tailscale"]),
        runner=lambda argv, timeout=15: commands.append(list(argv)) or CommandResult(0),
    )
    monkeypatch.setattr(pnv, "VPNAccountManager", lambda _system_check: manager)
    monkeypatch.setattr(pnv.threading, "Thread", lambda target, daemon: types.SimpleNamespace(start=target))
    monkeypatch.setattr(pnv.GLib, "idle_add", lambda callback, *args: callback(*args))

    CreatePage._run_logout(page, button)

    assert ["tailscale", "down"] in commands
    assert all("logout" not in command for command in commands if isinstance(command, list))
    assert ("finished", True) in commands
