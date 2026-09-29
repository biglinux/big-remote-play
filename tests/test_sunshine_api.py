"""Sunshine config-API client: request shape, response parsing, TOFU cert pinning.

Hermetic: the network call (`_api_request`) is captured, never executed. Assertions
target request structure and booleans (locale-independent), not translated prose.
"""

import hashlib
import json
import os
import stat

import pytest

from big_remote_play.host.sunshine_manager import SunshineHost, _cert_fingerprint

REAL_RUNNING_APP_ID = SunshineHost.running_app_id  # conftest replaces it with an offline stub


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
# Older Sunshine has no GET /api/pin and takes {"pin", "name"}. Current Sunshine
# (checked against v2026.914 on 2026-09-28) lists pending clients with
# GET /api/pin and requires the chosen request's 32-hex "pairing_id".

PAIRING_ID = "0123456789abcdef0123456789abcdef"


def _routes(host, routes):
    calls = []

    def recorder(method, path, payload=None, auth=None, timeout=5.0):
        calls.append({"method": method, "path": path, "payload": payload, "auth": auth})
        return routes.get(method, (404, b""))

    host._api_request = recorder
    return calls


def _legacy_pin(host, status, body=b""):
    """Legacy server: no pending list, the POST answers with ``status``."""
    return _routes(host, {"GET": (404, b""), "POST": (status, body)})


def test_send_pin_posts_pin_and_name_to_a_legacy_server(host) -> None:
    calls = _legacy_pin(host, 200, b'{"status": true}')
    result = host.send_pin("1234", name="laptop", auth=("admin", "pw"))
    assert result.ok is True and result.status == 200
    assert calls[-1] == {"method": "POST", "path": "/api/pin", "payload": {"pin": "1234", "name": "laptop"}, "auth": ("admin", "pw")}


def test_send_pin_omits_empty_name_on_a_legacy_server(host) -> None:
    calls = _legacy_pin(host, 200, b'{"status": true}')
    host.send_pin("9999")
    assert calls[-1]["payload"] == {"pin": "9999"}


def test_send_pin_uses_the_single_pending_pairing_id(host) -> None:
    pending = json.dumps([{"id": PAIRING_ID, "name": "Notebook", "address": "100.64.0.2"}]).encode()
    calls = _routes(host, {"GET": (200, pending), "POST": (200, b'{"status": true}')})
    result = host.send_pin("1234", auth=("admin", "pw"))
    assert result.ok
    assert calls[0]["method"] == "GET" and calls[0]["path"] == "/api/pin"
    assert calls[1]["payload"] == {"pin": "1234", "pairing_id": PAIRING_ID, "name": "Notebook"}


def test_send_pin_asks_which_computer_when_several_are_waiting(host) -> None:
    from big_remote_play.host.sunshine_manager import PIN_CHOOSE

    other = "f" * 32
    pending = json.dumps([{"id": PAIRING_ID, "name": "A", "address": "x"}, {"id": other, "name": "B", "address": "y"}]).encode()
    calls = _routes(host, {"GET": (200, pending), "POST": (200, b'{"status": true}')})
    result = host.send_pin("1234")
    assert not result.ok and result.status == PIN_CHOOSE
    assert [item.pairing_id for item in result.pending] == [PAIRING_ID, other]
    assert all(call["method"] == "GET" for call in calls)  # nothing approved blindly
    chosen = host.send_pin("1234", name="B", pairing_id=other)
    assert chosen.ok and calls[-1]["payload"] == {"pin": "1234", "pairing_id": other, "name": "B"}


def test_send_pin_explains_when_no_computer_is_waiting(host) -> None:
    from big_remote_play.host.sunshine_manager import PIN_NONE_WAITING

    calls = _routes(host, {"GET": (200, b"[]")})
    result = host.send_pin("1234")
    assert not result.ok and result.status == PIN_NONE_WAITING
    assert [call["method"] for call in calls] == ["GET"]


def test_send_pin_refuses_a_malformed_pairing_id(host) -> None:
    calls = _routes(host, {})
    assert not host.send_pin("1234", pairing_id="../x").ok
    assert calls == []


def test_send_pin_status_false_is_rejected(host) -> None:
    _legacy_pin(host, 200, b'{"status": false}')
    result = host.send_pin("0000")
    assert result.ok is False
    assert result.status == 200


def test_send_pin_401_is_auth_failure(host) -> None:
    calls = _routes(host, {"GET": (401, b"")})
    result = host.send_pin("1234")
    assert result.ok is False and result.status == 401
    assert len(calls) == 1  # no PIN is sent with credentials that were refused


def test_send_pin_307_signals_no_admin_user(host) -> None:
    _routes(host, {"GET": (307, b"")})
    result = host.send_pin("1234")
    assert result.ok is False
    assert result.status == 307  # caller offers to create the first admin user


def test_send_pin_connection_failure(host) -> None:
    _routes(host, {"GET": (0, b""), "POST": (0, b"")})
    result = host.send_pin("1234")
    assert result.ok is False
    assert result.status == 0


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


def test_a_reused_pid_in_a_stale_pid_file_is_not_a_running_sunshine(tmp_path, monkeypatch):
    """After Sunshine exits the kernel may give its PID to another process."""
    import os
    from types import SimpleNamespace

    from big_remote_play.host import sunshine_manager
    from big_remote_play.host.sunshine_manager import SunshineHost

    host = SunshineHost(tmp_path)
    (tmp_path / "sunshine.pid").write_text(str(os.getpid()))  # alive, but this is Python
    monkeypatch.setattr(sunshine_manager.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1))
    assert not host.is_running()
    assert not (tmp_path / "sunshine.pid").exists()


def test_an_exited_sunshine_nobody_collected_does_not_block_a_new_start(tmp_path, monkeypatch):
    """A zombie keeps its name in pgrep; it used to make Start sharing a no-op."""
    import subprocess
    import time
    from types import SimpleNamespace

    from big_remote_play.host import sunshine_manager
    from big_remote_play.host.sunshine_manager import SunshineHost

    child = subprocess.Popen(["true"])
    try:
        for _ in range(100):  # exited, not yet collected
            if sunshine_manager._is_zombie(child.pid):
                break
            time.sleep(0.02)
        assert sunshine_manager._is_zombie(child.pid)
        monkeypatch.setattr(sunshine_manager, "_process_name", lambda pid: "sunshine")
        monkeypatch.setattr(sunshine_manager.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=f"{child.pid}\n"))
        host = SunshineHost(tmp_path)
        (tmp_path / "sunshine.pid").write_text(str(child.pid))
        assert not host.is_running()
    finally:
        child.wait()


def test_stop_collects_a_sunshine_that_ignored_sigterm(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys

    from big_remote_play.host import sunshine_manager
    from big_remote_play.host.sunshine_manager import SunshineHost

    monkeypatch.setattr(sunshine_manager, "STOP_GRACE_SECONDS", 0.3)
    stubborn = subprocess.Popen(
        [sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(30)"],
        start_new_session=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert stubborn.stdout.readline().strip() == "ready"
    host = SunshineHost(tmp_path)
    host.process = stubborn
    assert host.stop()
    assert stubborn.returncode is not None  # collected: no zombie left behind
    assert not os.path.exists(f"/proc/{stubborn.pid}") or not sunshine_manager._is_zombie(stubborn.pid)


class _RunningSunshine:
    pid = 12345

    def poll(self):
        return None


def test_start_does_not_inject_the_game_vulkan_layer_into_sunshine(tmp_path, monkeypatch):
    """A session-wide vkBasalt setting must not wrap Sunshine's encoder probe."""
    from big_remote_play.host import sunshine_manager
    from big_remote_play.host.sunshine_manager import SunshineHost

    host = SunshineHost(tmp_path)
    captured = {}
    monkeypatch.setenv("ENABLE_VKBASALT", "1")
    monkeypatch.setattr(host, "is_running", lambda: False)
    monkeypatch.setattr(host, "_api_is_reachable", lambda: True)
    monkeypatch.setattr(sunshine_manager.shutil, "which", lambda _name: "/usr/bin/sunshine")

    def popen(argv, **kwargs):
        captured.update(argv=argv, **kwargs)
        return _RunningSunshine()

    monkeypatch.setattr(sunshine_manager.subprocess, "Popen", popen)
    ok, error = host.start()
    try:
        assert ok and error is None
        assert captured["argv"] == ["/usr/bin/sunshine", str(tmp_path / "sunshine.conf")]
        assert captured["env"]["ENABLE_VKBASALT"] == "0"
        assert os.environ["ENABLE_VKBASALT"] == "1"  # games keep the user's environment
    finally:
        host.log_file.close()


def test_start_reports_a_crash_that_happens_before_the_api_is_ready(tmp_path, monkeypatch):
    """Do not announce success merely because Sunshine survived one instant."""
    from big_remote_play.host import sunshine_manager
    from big_remote_play.host.sunshine_manager import SunshineHost

    class DelayedCrash:
        pid = 23456

        def __init__(self):
            self.polls = 0

        def poll(self):
            self.polls += 1
            return None if self.polls == 1 else -11

    host = SunshineHost(tmp_path)
    monkeypatch.setattr(host, "is_running", lambda: False)
    monkeypatch.setattr(host, "_api_is_reachable", lambda: False)
    monkeypatch.setattr(sunshine_manager.shutil, "which", lambda _name: "/usr/bin/sunshine")
    monkeypatch.setattr(sunshine_manager.subprocess, "Popen", lambda *a, **k: DelayedCrash())
    monkeypatch.setattr(sunshine_manager.time, "sleep", lambda _seconds: None)

    ok, error = host.start()
    assert not ok and error == "Exit code -11"
    assert host.process is None and host.pid is None
    assert not (tmp_path / "sunshine.pid").exists()


def test_configure_keeps_the_previous_file_and_unknown_options(tmp_path):
    import stat

    from big_remote_play.host.sunshine_manager import SunshineHost

    conf = tmp_path / "sunshine.conf"
    conf.write_text("my_custom_option = 42\naudio_sink = SunshineGameSink\nvirtual_sink = Speakers\n")
    host = SunshineHost(tmp_path)
    assert host.configure({"audio_sink": None, "stream_audio": "enabled"})
    text = conf.read_text()
    assert "my_custom_option = 42" in text and "virtual_sink = Speakers" in text
    assert "audio_sink" not in text
    previous = tmp_path / "sunshine.conf.previous"
    assert previous.read_text() == "my_custom_option = 42\naudio_sink = SunshineGameSink\nvirtual_sink = Speakers\n"
    assert stat.S_IMODE(previous.stat().st_mode) == 0o600
    before = previous.read_text()
    assert host.configure({"stream_audio": "enabled"})  # nothing changes: no new backup
    assert previous.read_text() == before


def test_configure_removes_encoder_for_automatic_selection(tmp_path):
    from big_remote_play.host.sunshine_manager import SunshineHost

    conf = tmp_path / "sunshine.conf"
    conf.write_text("encoder = vulkan\noutput_name = DP-2\n")
    host = SunshineHost(tmp_path)
    assert host.configure({"encoder": None})
    text = conf.read_text()
    assert "encoder" not in text
    assert "output_name = DP-2" in text


# --- open stream (GET /serverinfo, what Moonlight checks before pairing) ----


@pytest.fixture
def serverinfo(tmp_path):
    """A local HTTP server on a free port answering /serverinfo with a canned reply."""
    import http.server
    import threading

    reply = {"status": 200, "body": b""}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(reply["status"] if self.path == "/serverinfo" else 404)
            self.end_headers()
            self.wfile.write(reply["body"])

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    (tmp_path / "sunshine.conf").write_text(f"port = {server.server_address[1]}\n")
    yield reply
    server.shutdown()
    server.server_close()


def _xml(game: str, state: str) -> bytes:
    # Sunshine 2026.914, unpaired client over HTTP (measured 2026-09-29).
    return f'<?xml version="1.0" encoding="utf-8"?><root status_code="200"><hostname>lab</hostname><PairStatus>0</PairStatus><currentgame>{game}</currentgame><state>{state}</state></root>'.encode()


def test_an_open_stream_is_read_from_serverinfo(host, serverinfo) -> None:
    serverinfo["body"] = _xml("881448767", "SUNSHINE_SERVER_BUSY")
    assert REAL_RUNNING_APP_ID(host) == 881448767
    serverinfo["body"] = _xml("0", "SUNSHINE_SERVER_FREE")
    assert REAL_RUNNING_APP_ID(host) == 0


def test_an_unknown_stream_state_is_none_not_free(host, serverinfo) -> None:
    serverinfo["body"] = b"<root><hostname>lab</hostname></root>"
    assert REAL_RUNNING_APP_ID(host) is None
    serverinfo["status"], serverinfo["body"] = 503, _xml("0", "SUNSHINE_SERVER_FREE")
    assert REAL_RUNNING_APP_ID(host) is None


def test_no_sunshine_listening_is_none(tmp_path) -> None:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]  # closed again: nothing listens there
    (tmp_path / "sunshine.conf").write_text(f"port = {port}\n")
    assert REAL_RUNNING_APP_ID(SunshineHost(cdir=tmp_path), timeout=0.5) is None
