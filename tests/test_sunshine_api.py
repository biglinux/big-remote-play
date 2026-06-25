"""Sunshine config-API client: request shape, response parsing, TOFU cert pinning.

Hermetic: the network call (`_api_request`) is captured, never executed. Assertions
target request structure and booleans (locale-independent), not translated prose.
"""

import hashlib
import json
import stat

import pytest

from big_remote_play.host.sunshine_manager import SunshineHost, _cert_fingerprint


@pytest.fixture
def host(tmp_path):
    return SunshineHost(cdir=tmp_path)


def _capture(host, status, body=b""):
    """Replace _api_request with a recorder returning a canned response."""
    calls = []

    def recorder(method, path, payload=None, auth=None, timeout=5.0):
        calls.append({"method": method, "path": path, "payload": payload, "auth": auth})
        return status, body

    host._api_request = recorder
    return calls


# --- fingerprint + TOFU ---------------------------------------------------


def test_cert_fingerprint_is_sha256_hex() -> None:
    assert _cert_fingerprint(b"abc") == hashlib.sha256(b"abc").hexdigest()


def test_trust_fingerprint_pins_on_first_use_then_matches(host) -> None:
    fp = "a" * 64
    assert host._trust_fingerprint(fp) is True  # first contact pins
    assert host.cert_fp_file.exists()
    assert host._trust_fingerprint(fp) is True  # same cert -> trusted


def test_trust_fingerprint_rejects_mismatch(host) -> None:
    host._trust_fingerprint("a" * 64)
    assert host._trust_fingerprint("b" * 64) is False  # cert changed -> refuse


def test_pinned_fingerprint_file_is_owner_only(host) -> None:
    host._trust_fingerprint("c" * 64)
    mode = stat.S_IMODE(host.cert_fp_file.stat().st_mode)
    assert mode == 0o600


# --- send_pin -------------------------------------------------------------


def test_send_pin_posts_pin_and_name(host) -> None:
    calls = _capture(host, 200, b'{"status": true}')
    ok, _msg = host.send_pin("1234", name="laptop", auth=("admin", "pw"))
    assert ok is True
    assert calls == [
        {
            "method": "POST",
            "path": "/api/pin",
            "payload": {"pin": "1234", "name": "laptop"},
            "auth": ("admin", "pw"),
        }
    ]


def test_send_pin_omits_empty_name(host) -> None:
    calls = _capture(host, 200, b'{"status": true}')
    host.send_pin("9999")
    assert calls[0]["payload"] == {"pin": "9999"}


def test_send_pin_status_false_is_rejected(host) -> None:
    _capture(host, 200, b'{"status": false}')
    ok, _msg = host.send_pin("0000")
    assert ok is False


def test_send_pin_401_is_auth_failure(host) -> None:
    _capture(host, 401)
    ok, _msg = host.send_pin("1234")
    assert ok is False


def test_send_pin_connection_failure(host) -> None:
    _capture(host, 0)
    ok, _msg = host.send_pin("1234")
    assert ok is False


# --- create_user (POST /api/password, not the nonexistent /api/users) -----


def test_create_user_uses_password_endpoint(host) -> None:
    calls = _capture(host, 200, b'{"status": true}')
    ok, _msg = host.create_user("admin", "secret")
    assert ok is True
    call = calls[0]
    assert call["method"] == "POST"
    assert call["path"] == "/api/password"
    assert call["payload"] == {
        "currentUsername": "",
        "currentPassword": "",
        "newUsername": "admin",
        "newPassword": "secret",
        "confirmNewPassword": "secret",
    }
    assert call["auth"] is None  # first-run: no credentials yet


def test_create_user_rejected(host) -> None:
    _capture(host, 200, b'{"status": false}')
    ok, _msg = host.create_user("admin", "secret")
    assert ok is False


def test_set_credentials_change_sends_current_and_authenticates(host) -> None:
    calls = _capture(host, 200, b'{"status": true}')
    ok, _msg = host.set_credentials("admin", "newpass", current=("admin", "oldpass"))
    assert ok is True
    call = calls[0]
    assert call["path"] == "/api/password"
    assert call["payload"] == {
        "currentUsername": "admin",
        "currentPassword": "oldpass",
        "newUsername": "admin",
        "newPassword": "newpass",
        "confirmNewPassword": "newpass",
    }
    assert call["auth"] == ("admin", "oldpass")  # change is authenticated


def test_set_credentials_change_wrong_password_is_401(host) -> None:
    _capture(host, 401)
    ok, _msg = host.set_credentials("admin", "newpass", current=("admin", "bad"))
    assert ok is False


# --- reset_credentials (sunshine --creds, no old password) ----------------


class _FakeProc:
    def __init__(self, returncode, stderr=""):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = stderr


def test_reset_credentials_runs_creds_cli(host, monkeypatch) -> None:
    import big_remote_play.host.sunshine_manager as sm

    calls = {}
    monkeypatch.setattr(sm.shutil, "which", lambda _n: "/usr/bin/sunshine")

    def fake_run(argv, **kwargs):
        calls["argv"] = argv
        return _FakeProc(0)

    monkeypatch.setattr(sm.subprocess, "run", fake_run)
    ok, _msg = host.reset_credentials("admin", "newpass")
    assert ok is True
    assert calls["argv"] == ["/usr/bin/sunshine", "--creds", "admin", "newpass"]


def test_reset_credentials_reports_failure(host, monkeypatch) -> None:
    import big_remote_play.host.sunshine_manager as sm

    monkeypatch.setattr(sm.shutil, "which", lambda _n: "/usr/bin/sunshine")
    monkeypatch.setattr(sm.subprocess, "run", lambda argv, **kw: _FakeProc(1, "boom"))
    ok, msg = host.reset_credentials("admin", "newpass")
    assert ok is False
    assert "boom" in msg


def test_reset_credentials_requires_nonempty(host, monkeypatch) -> None:
    import big_remote_play.host.sunshine_manager as sm

    called = {"ran": False}
    monkeypatch.setattr(sm.shutil, "which", lambda _n: "/usr/bin/sunshine")

    def fake_run(*a, **k):
        called["ran"] = True
        return _FakeProc(0)

    monkeypatch.setattr(sm.subprocess, "run", fake_run)
    ok, _msg = host.reset_credentials("", "newpass")
    assert ok is False
    assert called["ran"] is False  # no exec on empty input


def test_reset_credentials_no_binary(host, monkeypatch) -> None:
    import big_remote_play.host.sunshine_manager as sm

    monkeypatch.setattr(sm.shutil, "which", lambda _n: None)
    ok, _msg = host.reset_credentials("admin", "newpass")
    assert ok is False


# --- client management ----------------------------------------------------


def test_list_clients_parses_named_certs(host) -> None:
    body = json.dumps(
        {
            "status": True,
            "named_certs": [
                {"name": "phone", "uuid": "u1", "enabled": True},
            ],
        }
    ).encode()
    _capture(host, 200, body)
    clients = host.list_clients(auth=("admin", "pw"))
    assert clients == [{"name": "phone", "uuid": "u1", "enabled": True}]


def test_list_clients_empty_on_error(host) -> None:
    _capture(host, 0)
    assert host.list_clients() == []


def test_unpair_client_posts_uuid(host) -> None:
    calls = _capture(host, 200)
    assert host.unpair_client("u1") is True
    assert calls[0] == {
        "method": "POST",
        "path": "/api/clients/unpair",
        "payload": {"uuid": "u1"},
        "auth": None,
    }


def test_unpair_client_rejects_empty_uuid(host) -> None:
    calls = _capture(host, 200)
    assert host.unpair_client("") is False
    assert calls == []  # no request issued


def test_set_client_enabled_posts_uuid_and_flag(host) -> None:
    calls = _capture(host, 200)
    assert host.set_client_enabled("u2", False) is True
    assert calls[0]["path"] == "/api/clients/update"
    assert calls[0]["payload"] == {"uuid": "u2", "enabled": False}


# --- logs -----------------------------------------------------------------


def test_get_logs_decodes_text(host) -> None:
    _capture(host, 200, b"line one\nline two")
    assert host.get_logs() == "line one\nline two"


def test_get_logs_empty_on_error(host) -> None:
    _capture(host, 0)
    assert host.get_logs() == ""


# --- close_app ------------------------------------------------------------


def test_close_app_posts_close(host) -> None:
    calls = _capture(host, 200, b'{"status": true}')
    assert host.close_app() is True
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "/api/apps/close"
    assert calls[0]["payload"] == {}


# --- apps -----------------------------------------------------------------


def test_get_apps_parses_array(host) -> None:
    _capture(host, 200, json.dumps({"apps": [{"name": "Game", "index": 0}]}).encode())
    assert host.get_apps() == [{"name": "Game", "index": 0}]


def test_add_app_defaults_index_to_append(host) -> None:
    calls = _capture(host, 200, b'{"status": true}')
    assert host.add_app({"name": "Steam", "cmd": "steam"}) is True
    assert calls[0]["path"] == "/api/apps"
    assert calls[0]["payload"] == {"name": "Steam", "cmd": "steam", "index": -1}


def test_add_app_preserves_explicit_index_and_prep_cmd(host) -> None:
    calls = _capture(host, 200, b'{"status": true}')
    entry = {"name": "G", "cmd": "g", "index": 3, "prep-cmd": [{"do": "a", "undo": "b", "elevated": False}]}
    host.add_app(entry)
    assert calls[0]["payload"]["index"] == 3
    assert calls[0]["payload"]["prep-cmd"] == [{"do": "a", "undo": "b", "elevated": False}]


def test_delete_app_uses_index_in_path(host) -> None:
    calls = _capture(host, 200, b'{"status": true}')
    assert host.delete_app(2) is True
    assert calls[0] == {"method": "DELETE", "path": "/api/apps/2", "payload": None, "auth": None}


def test_delete_app_rejects_negative_index(host) -> None:
    calls = _capture(host, 200)
    assert host.delete_app(-1) is False
    assert calls == []


# --- covers ---------------------------------------------------------------


def test_upload_cover_with_url_returns_path(host) -> None:
    calls = _capture(host, 200, b'{"status": true, "path": "/cov/x.png"}')
    out = host.upload_cover("igdb_42", url="https://images.igdb.com/x.png")
    assert out == "/cov/x.png"
    assert calls[0]["payload"] == {"key": "igdb_42", "url": "https://images.igdb.com/x.png"}


def test_upload_cover_requires_key_and_source(host) -> None:
    calls = _capture(host, 200)
    assert host.upload_cover("", url="https://images.igdb.com/x.png") == ""
    assert host.upload_cover("igdb_42") == ""  # no url and no data
    assert calls == []


# --- config ---------------------------------------------------------------


def test_get_config_parses_dict(host) -> None:
    _capture(host, 200, json.dumps({"status": True, "fps": "60", "platform": "linux"}).encode())
    cfg = host.get_config()
    assert cfg["fps"] == "60"
    assert cfg["platform"] == "linux"


def test_save_config_posts_settings(host) -> None:
    calls = _capture(host, 200, b'{"status": true}')
    assert host.save_config({"bitrate": "20000"}) is True
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "/api/config"
    assert calls[0]["payload"] == {"bitrate": "20000"}


# --- restart --------------------------------------------------------------


def test_restart_via_api_success_on_200(host) -> None:
    _capture(host, 200, b'{"status": true}')
    assert host.restart_via_api() is True


def test_restart_via_api_tolerates_connection_drop(host) -> None:
    _capture(host, 0)  # restart drops the connection
    assert host.restart_via_api() is True


# --- browse ---------------------------------------------------------------


def test_browse_builds_query_and_parses_entries(host) -> None:
    calls = _capture(
        host,
        200,
        json.dumps(
            {
                "path": "/home",
                "parent": "/",
                "entries": [{"name": "g", "type": "file", "path": "/home/g"}],
            }
        ).encode(),
    )
    out = host.browse("/home", "executable")
    assert out["entries"][0]["name"] == "g"
    assert calls[0]["method"] == "GET"
    assert calls[0]["path"].startswith("/api/browse?")
    assert "path=%2Fhome" in calls[0]["path"]
    assert "type=executable" in calls[0]["path"]


def test_browse_empty_on_error(host) -> None:
    _capture(host, 0)
    assert host.browse("/home") == {}
