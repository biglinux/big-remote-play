"""Behavioral guards for the contracts checked against upstream in this review.

External processes are substituted; no actual pairing, firewall or VPN changes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import threading
from unittest.mock import Mock

import pytest

from big_remote_play.guest.moonlight_client import MoonlightClient
from big_remote_play.host.sunshine_manager import SunshineHost
from big_remote_play.utils.config import Config
from big_remote_play.utils.moonlight_config import MoonlightConfigManager


@pytest.fixture
def mooncfg(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "custom-config"))
    monkeypatch.setattr(MoonlightConfigManager, "_shared_state", {})
    return MoonlightConfigManager()


def test_moonlight_ini_preserves_case_percent_values_and_paired_hosts(mooncfg):
    file = mooncfg.config_file
    file.parent.mkdir(parents=True)
    file.write_text("[General]\nquitAppAfter=false\nCertificate=@ByteArray(%AB\\x00)\n[hosts]\n1\\hostname=Game PC\n1\\uniqueId=ABC\n", encoding="utf-8")
    assert mooncfg.set_many({"width": 3440, "height": 1440, "videocfg": 4})
    saved = file.read_text()
    assert "Certificate=@ByteArray(%AB\\x00)" in saved
    assert "1\\uniqueId=ABC" in saved
    assert "quitAppAfter=false" in saved
    assert "videocfg=4" in saved
    assert stat.S_IMODE(file.stat().st_mode) == 0o600
    assert "custom-config" in str(file)


@pytest.mark.parametrize(
    "old,value,key,expected",
    [
        ("videocodec", "3", "videocfg", "4"),
        ("windowmode", "3", "windowmode", "1"),
        ("windowMode", "1", "windowmode", "0"),
        ("captureSystemKeys", "true", "capturesyskeys", "2"),
        ("touchscreentrackpad", "true", "abstouchmode", "false"),
        ("muteHostSpeakers", "true", "hostaudio", "false"),
        ("videoDecoder", "2", "videodec", "2"),
        ("audioConfig", "1", "audiocfg", "1"),
        ("gamepadSwapButtons", "true", "swapfacebuttons", "true"),
    ],
)
def test_migration_maps_property_names_to_serialized_keys(mooncfg, old, value, key, expected):
    mooncfg.config_file.parent.mkdir(parents=True)
    mooncfg.config_file.write_text(f"[General]\n{old}={value}\n")
    mooncfg.reload()
    assert mooncfg.get(key) == expected
    assert mooncfg.set("fps", 60)
    if old != key:
        assert f"{old}=" not in mooncfg.config_file.read_text()


def test_native_settings_take_precedence_over_legacy_aliases(mooncfg):
    mooncfg.config_file.parent.mkdir(parents=True)
    mooncfg.config_file.write_text("[General]\nvideocfg=2\nvideoCodec=3\nwindowmode=1\nwindowMode=1\n")
    mooncfg.reload()
    assert mooncfg.get("videocfg") == "2"
    assert mooncfg.get("windowmode") == "1"


def test_unreadable_ini_is_not_replaced_with_empty_settings(mooncfg):
    mooncfg.config_file.parent.mkdir(parents=True)
    original = b"this is not an INI file\nCertificate=keep me"
    mooncfg.config_file.write_bytes(original)
    assert not mooncfg.set("fps", 60)
    assert mooncfg.config_file.read_bytes() == original


def test_a_new_write_keeps_external_native_settings(mooncfg):
    assert mooncfg.set("fps", 60)
    with mooncfg.config_file.open("a") as stream:
        stream.write("videocfg=4\n")
    assert mooncfg.set_many({"width": 2560, "height": 1440})
    assert mooncfg.get("videocfg") == "4"


def test_independent_application_configs_do_not_erase_other_pages(fake_home):
    first, second = Config(), Config()
    first.set("home_role", "host")
    second.set("guest", {"auto_quality": False})
    fresh = Config()
    assert fresh.get("home_role") == "host"
    assert fresh.get("guest")["auto_quality"] is False


@pytest.mark.parametrize("value", [[], None, "not an object", 42])
def test_nonobject_json_config_is_rejected(fake_home, value):
    cfg = Config()
    cfg.config_file.write_text(json.dumps(value))
    assert Config().get("network")["upnp"] is False


def test_sunshine_keeps_custom_port_and_library_path(tmp_path):
    host = SunshineHost(cdir=tmp_path)
    (tmp_path / "sunshine.conf").write_text("port = 50000\nfile_apps = custom.json\nqp = 24\nplatform = wayland\nfps = 60\n")
    original = {"env": {"CUSTOM": "present"}, "apps": [{"name": "My game", "cmd": "game"}]}
    (tmp_path / "custom.json").write_text(json.dumps(original))
    assert host.ensure_desktop_app()
    assert host.configure({"max_bitrate": 20000, "capture": ""})
    assert host.api_port == 50001 and host.web_ui_url.endswith(":50001")
    config = (tmp_path / "sunshine.conf").read_text()
    assert "qp = 24" in config and "file_apps = custom.json" in config
    assert "fps =" not in config and "platform =" not in config
    library = json.loads((tmp_path / "custom.json").read_text())
    assert library["env"] == original["env"] and library["apps"][0] == original["apps"][0]
    assert library["apps"][-1] == {"name": "Desktop", "cmd": ""}
    assert host.ensure_desktop_app()
    assert len(json.loads((tmp_path / "custom.json").read_text())["apps"]) == 2


@pytest.mark.parametrize("body", ["broken", "[]", '{"apps":null}', '{"apps":{}}'])
def test_invalid_game_library_is_left_untouched(tmp_path, body):
    host = SunshineHost(cdir=tmp_path)
    file = tmp_path / "apps.json"
    file.write_text(body)
    assert not host.ensure_desktop_app()
    assert file.read_text() == body


@pytest.mark.parametrize(
    "address,port,expected",
    [
        ("192.0.2.1", 47989, "192.0.2.1"),
        ("game.local", 50000, "game.local:50000"),
        ("2001:db8::1", 50000, "[2001:db8::1]:50000"),
        ("[2001:db8::1]", 47989, "2001:db8::1"),
    ],
)
def test_moonlight_target_address_preserves_custom_port(address, port, expected):
    assert MoonlightClient().target_address(address, port) == expected


@pytest.mark.parametrize("address,port", [("--help", 47989), ("a b", 47989), ("a", 0), ("a", 65536)])
def test_moonlight_rejects_option_injection_and_invalid_port(address, port):
    with pytest.raises(ValueError):
        MoonlightClient().target_address(address, port)


@pytest.mark.parametrize("code", [0, 1])
def test_pairing_uses_explicit_four_digit_pin_and_process_result(monkeypatch, code):
    import big_remote_play.guest.moonlight_client as mc

    process = Mock()
    process.wait.return_value = code
    process.poll.return_value = code
    popen = Mock(return_value=process)
    monkeypatch.setattr(mc.subprocess, "Popen", popen)
    monkeypatch.setattr(mc.secrets, "randbelow", lambda maximum: 7)
    client = MoonlightClient()
    client.moonlight_cmd = "moonlight-qt"
    pins = []
    assert client.pair("192.0.2.10", pins.append, port=50000) is (code == 0)
    assert popen.call_args.args[0] == ["moonlight-qt", "pair", "192.0.2.10:50000", "--pin", "0007"]
    assert pins == ["0007"]
    assert client.process is None and not client.is_connected()
    assert client._pair_process is None


def test_cancelled_pair_never_spawns_a_process(monkeypatch):
    import big_remote_play.guest.moonlight_client as mc

    popen = Mock()
    monkeypatch.setattr(mc.subprocess, "Popen", popen)
    client = MoonlightClient()
    client.moonlight_cmd = "moonlight-qt"
    event = threading.Event()
    event.set()
    assert not client.pair("192.0.2.10", cancel_event=event)
    popen.assert_not_called()


def test_stream_does_not_force_remote_app_quit_or_host_audio(monkeypatch):
    import big_remote_play.guest.moonlight_client as mc

    process = Mock()
    process.poll.return_value = None
    process.wait.side_effect = subprocess.TimeoutExpired("moonlight", 1)
    popen = Mock(return_value=process)
    monkeypatch.setattr(mc.subprocess, "Popen", popen)
    client = MoonlightClient()
    client.moonlight_cmd = "moonlight-qt"
    assert client.connect("192.0.2.10", port=50000, play_audio_on_host=False)
    argv = popen.call_args.args[0]
    assert "--quit-after" not in argv and "--audio-on-host" not in argv
    assert "--no-audio-on-host" in argv and "192.0.2.10:50000" in argv
    assert argv[argv.index("--video-decoder") + 1] == "auto"


@pytest.mark.parametrize("base", [6, 47989, 50000, 65514])
def test_firewall_dry_run_uses_only_expected_ports(base):
    script = Path(__file__).parents[1] / "usr/share/big-remote-play/scripts/configure_firewall.sh"
    result = subprocess.run(["bash", str(script), "--dry-run", str(base)], capture_output=True, text=True, check=True)
    ports = {int(s) for s in result.stdout.replace(",", " ").split() if s.isdigit()}
    assert base + 1 not in ports  # HTTPS administration must not be exposed.
    assert base + 9 in ports and base + 21 in ports


def test_secret_backup_permissions_are_not_controlled_by_umask(tmp_path):
    from big_remote_play.ui.preferences import PreferencesWindow
    from types import SimpleNamespace

    source = tmp_path / "config"
    source.mkdir()
    (source / "key.pem").write_text("private")
    destination = source / "backup.tar.gz"
    old = os.umask(0)
    try:
        PreferencesWindow._write_backup(SimpleNamespace(_backup_sources=lambda: [source]), destination)
    finally:
        os.umask(old)
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    import tarfile

    with tarfile.open(destination) as archive:
        assert not any("backup" in name or ".brp-backup" in name for name in archive.getnames())
        assert "config/key.pem" in archive.getnames()


def test_reset_streaming_preferences_keeps_pairing_identity(mooncfg):
    mooncfg.config_file.parent.mkdir(parents=True)
    mooncfg.config_file.write_text("[General]\nwidth=3440\nvideocfg=4\nCertificate=PRIVATE\n[hosts]\n1\\uniqueId=KEEP\n")
    assert mooncfg.reset_streaming_settings()
    body = mooncfg.config_file.read_text()
    assert "width=" not in body and "videocfg=" not in body
    assert "Certificate=PRIVATE" in body and "1\\uniqueId=KEEP" in body


def test_restore_uses_actual_flatpak_destination(tmp_path, monkeypatch, mooncfg):
    import io
    import tarfile
    from types import SimpleNamespace
    from big_remote_play import paths
    from big_remote_play.ui.preferences import PreferencesWindow

    for name, directory in [("CONFIG_DIR", "brp"), ("SUNSHINE_CONFIG_DIR", "sunshine")]:
        monkeypatch.setitem(paths.__dict__, name, tmp_path / directory)
    mooncfg.config_file = tmp_path / ".var/app/moonlight/config/Moonlight Game Streaming Project/Moonlight.conf"
    archive_path = tmp_path / "backup.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        data = b"[General]\nfps=120\n"
        info = tarfile.TarInfo("Moonlight Game Streaming Project/Moonlight.conf")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    app, error = Mock(), Mock()
    window = SimpleNamespace(_show_error=error, get_application=lambda: app)
    PreferencesWindow._restore_backup(window, archive_path)
    error.assert_not_called()
    app.quit.assert_called_once()
    assert mooncfg.config_file.read_bytes() == data
    assert stat.S_IMODE(mooncfg.config_file.stat().st_mode) == 0o600


def test_restore_refuses_existing_symlink_parent(tmp_path, monkeypatch, mooncfg):
    import io
    import tarfile
    from types import SimpleNamespace
    from big_remote_play import paths
    from big_remote_play.ui.preferences import PreferencesWindow

    target = tmp_path / "outside"
    target.mkdir()
    config = tmp_path / "brp"
    config.symlink_to(target, target_is_directory=True)
    monkeypatch.setitem(paths.__dict__, "CONFIG_DIR", config)
    monkeypatch.setitem(paths.__dict__, "SUNSHINE_CONFIG_DIR", tmp_path / "sunshine")
    archive_path = tmp_path / "malicious.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        info = tarfile.TarInfo("brp/config.json")
        info.size = 2
        archive.addfile(info, io.BytesIO(b"{}"))
    app, error = Mock(), Mock()
    PreferencesWindow._restore_backup(SimpleNamespace(_show_error=error, get_application=lambda: app), archive_path)
    error.assert_called_once()
    app.quit.assert_not_called()
    assert not (target / "config.json").exists()


def test_unreadable_sunshine_credentials_config_is_not_rewritten(tmp_path, monkeypatch):
    from big_remote_play.utils.sunshine_credentials import _rewrite_config

    conf = tmp_path / "sunshine.conf"
    conf.write_text("port = 50000\n")
    reader = Path.read_text

    def fail(path, *args, **kwargs):
        if path == conf:
            raise PermissionError("not readable")
        return reader(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail)
    with pytest.raises(PermissionError):
        _rewrite_config(conf, {"sunshine_user": "user"}, set())
    assert conf.read_bytes() == b"port = 50000\n"
