"""Hermetic behavioral coverage for the backup/restore boundary."""

from __future__ import annotations

import io
import hashlib
import json
from pathlib import Path
import stat
import tarfile

import pytest

from big_remote_play.utils.backup_restore import BackupError, BackupManager, BackupValidationError


def manager(tmp_path: Path) -> BackupManager:
    return BackupManager(
        tmp_path / "config" / "big-remote-play",
        tmp_path / "config" / "big-remote-play" / "sunshine",
        tmp_path / "moonlight" / "Moonlight.conf",
        app_version="test",
    )


def add_file(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    archive.addfile(info, io.BytesIO(data))


def test_round_trip_uses_namespaces_replaces_scope_and_skips_transient_data(tmp_path):
    backup = manager(tmp_path)
    backup.config_dir.mkdir(parents=True)
    backup.sunshine_dir.mkdir()
    backup.moonlight_file.parent.mkdir()
    (backup.config_dir / "config.json").write_text('{"theme":"gamer"}')
    (backup.config_dir / "vpn_choice.json").write_text('{"vpn_provider":"tailscale"}')
    (backup.config_dir / "private_network").mkdir()
    (backup.config_dir / "private_network" / "history.json").write_text('{"history":[{"name":"keep","auth_key":"NEVER_EXPORT"}]}')
    (backup.config_dir / "zerotier").mkdir()
    (backup.config_dir / "zerotier" / "api_token.txt").write_text("NEVER_EXPORT")
    (backup.config_dir / "audio-session.json").write_text("runtime")
    (backup.config_dir / "logs").mkdir()
    (backup.config_dir / "logs" / "app.log").write_text("private log")
    (backup.sunshine_dir / "sunshine.conf").write_text("port = 50000\nsunshine_password = NEVER_EXPORT\n")
    (backup.sunshine_dir / "sunshine.log").write_text("runtime log")
    (backup.sunshine_dir / "ignored-link").symlink_to(backup.config_dir / "config.json")
    backup.moonlight_file.write_text("[General]\nfps=120\n[hosts]\n1\\uniqueId=KEEP\n")

    archive = tmp_path / "settings.tar.gz"
    summary = backup.create(archive)
    assert summary.components == ("application", "moonlight", "sunshine")
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    with tarfile.open(archive) as handle:
        names = handle.getnames()
        assert names.count("sunshine/sunshine.conf") == 1
        assert "application/config.json" in names and "moonlight/Moonlight.conf" in names
        assert not any("logs" in name or "audio-session" in name or "sunshine.log" in name or "ignored-link" in name for name in names)
        assert "application/zerotier/api_token.txt" not in names
        archived_bytes = b"".join(handle.extractfile(member).read() for member in handle.getmembers() if member.isfile())
        assert b"NEVER_EXPORT" not in archived_bytes

    (backup.config_dir / "config.json").write_text("changed")
    (backup.config_dir / "obsolete.json").write_text("remove me")
    (backup.sunshine_dir / "obsolete.conf").write_text("remove me")
    backup.moonlight_file.write_text("changed")
    backup.restore(archive)

    assert json.loads((backup.config_dir / "config.json").read_text()) == {"theme": "gamer"}
    assert not (backup.config_dir / "obsolete.json").exists()
    assert not (backup.sunshine_dir / "obsolete.conf").exists()
    assert (backup.config_dir / "audio-session.json").read_text() == "runtime"
    assert "uniqueId=KEEP" in backup.moonlight_file.read_text()


@pytest.mark.parametrize("name", ["big-remote-play/../escaped", "/absolute", "big-remote-play\\outside"])
def test_legacy_restore_rejects_path_traversal_and_absolute_paths(tmp_path, name):
    archive = tmp_path / "hostile.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        add_file(handle, name, b"owned")
    with pytest.raises(BackupValidationError):
        manager(tmp_path).restore(archive)
    assert not (tmp_path / "escaped").exists()


def test_restore_rejects_links_duplicates_and_unknown_roots(tmp_path):
    backup = manager(tmp_path)
    for kind in ("link", "duplicate", "unknown"):
        archive = tmp_path / f"{kind}.tar.gz"
        with tarfile.open(archive, "w:gz") as handle:
            if kind == "link":
                info = tarfile.TarInfo("big-remote-play/link")
                info.type = tarfile.SYMTYPE
                info.linkname = "/tmp/outside"
                handle.addfile(info)
            elif kind == "duplicate":
                add_file(handle, "big-remote-play/config.json", b"{}")
                add_file(handle, "big-remote-play/config.json", b"{}")
            else:
                add_file(handle, "some-other-app/config.json", b"{}")
        with pytest.raises(BackupValidationError):
            backup.restore(archive)


def test_integrity_mismatch_is_rejected_before_current_settings_change(tmp_path):
    backup = manager(tmp_path)
    backup.config_dir.mkdir(parents=True)
    config = backup.config_dir / "config.json"
    config.write_text("original")
    valid = tmp_path / "valid.tar.gz"
    backup.create(valid)

    damaged = tmp_path / "damaged.tar.gz"
    with tarfile.open(valid, "r:gz") as source, tarfile.open(damaged, "w:gz") as target:
        for member in source.getmembers():
            data = source.extractfile(member).read() if member.isfile() else b""
            if member.name == "application/config.json":
                data = b"tampered"
            add_file(target, member.name, data)
    config.write_text("current")
    with pytest.raises(BackupValidationError):
        backup.restore(damaged)
    assert config.read_text() == "current"


def test_structurally_valid_archive_still_cannot_restore_a_plaintext_credential(tmp_path):
    data = b"SECRET_TOKEN"
    path = "application/zerotier/api_token.txt"
    manifest = json.dumps(
        {
            "format": "big-remote-play-backup",
            "version": 1,
            "created_at": "2026-01-01T00:00:00+00:00",
            "app_version": "test",
            "components": ["application"],
            "files": [{"path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}],
        }
    ).encode()
    archive = tmp_path / "plaintext-secret.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        add_file(handle, "manifest.json", manifest)
        add_file(handle, path, data)
    with pytest.raises(BackupValidationError, match="plaintext credential"):
        manager(tmp_path).restore(archive)
    assert not (manager(tmp_path).config_dir / "zerotier" / "api_token.txt").exists()


def test_restore_rolls_back_every_changed_file_after_a_write_failure(tmp_path, monkeypatch):
    backup = manager(tmp_path)
    backup.config_dir.mkdir(parents=True)
    first, second = backup.config_dir / "a.json", backup.config_dir / "b.json"
    first.write_text("new a")
    second.write_text("new b")
    archive = tmp_path / "settings.tar.gz"
    backup.create(archive)
    first.write_text("old a")
    second.write_text("old b")

    real_write = backup._atomic_write
    calls = 0

    def fail_second(target, data, mode=0o600):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk full")
        return real_write(target, data, mode)

    monkeypatch.setattr(backup, "_atomic_write", fail_second)
    with pytest.raises(BackupError, match="previous settings were restored"):
        backup.restore(archive)
    assert first.read_text() == "old a" and second.read_text() == "old b"


def test_interrupted_backup_never_replaces_an_existing_archive(tmp_path, monkeypatch):
    backup = manager(tmp_path)
    backup.config_dir.mkdir(parents=True)
    (backup.config_dir / "config.json").write_text("settings")
    destination = tmp_path / "settings.tar.gz"
    destination.write_bytes(b"known-good")

    monkeypatch.setattr(backup, "inspect", lambda _path: (_ for _ in ()).throw(BackupValidationError("incomplete")))
    with pytest.raises(BackupValidationError):
        backup.create(destination)
    assert destination.read_bytes() == b"known-good"
    assert not list(tmp_path.glob(".brp-backup-*"))


def test_legacy_archive_is_migrated_without_duplicate_sunshine_content(tmp_path):
    backup = manager(tmp_path)
    archive = tmp_path / "legacy.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        add_file(handle, "big-remote-play/config.json", b"{}")
        add_file(handle, "big-remote-play/sunshine/sunshine.conf", b"port = 1")
        add_file(handle, "sunshine/sunshine.conf", b"port = 1")
        add_file(handle, "Moonlight/Moonlight.conf", b"[General]\nfps=60\n")
    # The actual Moonlight parent is named "moonlight" here, so an unrelated
    # legacy root must be rejected instead of guessed.
    with pytest.raises(BackupValidationError):
        backup.restore(archive)

    compatible = tmp_path / "legacy-compatible.tar.gz"
    with tarfile.open(compatible, "w:gz") as handle:
        add_file(handle, "big-remote-play/config.json", b"{}")
        add_file(handle, "big-remote-play/sunshine/sunshine.conf", b"port = 1")
        add_file(handle, "sunshine/sunshine.conf", b"port = 1")
        add_file(handle, "moonlight/Moonlight.conf", b"[General]\nfps=60\n")
    backup.restore(compatible)
    assert (backup.sunshine_dir / "sunshine.conf").read_text() == "port = 1"
    assert backup.moonlight_file.read_text().endswith("fps=60\n")


def test_reset_defaults_removes_unknown_keys(fake_home):
    from big_remote_play.utils.config import Config

    config = Config()
    config.config = {"obsolete": True, "theme": "dark"}
    assert config.save() and config.reset_defaults()
    assert "obsolete" not in json.loads(config.config_file.read_text())
