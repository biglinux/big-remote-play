"""Config: atomic persistence, defaults, and corruption tolerance."""

import json
from pathlib import Path

from big_remote_play.utils.config import Config


def _config_path(home: Path) -> Path:
    return home / ".config" / "big-remoteplay" / "config.json"


def test_defaults_when_no_file(fake_home: Path) -> None:
    cfg = Config()
    assert cfg.get("theme") == "auto"
    assert cfg.get("network")["sunshine_port"] == 47989
    assert cfg.get("missing", "fallback") == "fallback"


def test_set_persists_to_disk(fake_home: Path) -> None:
    cfg = Config()
    cfg.set("theme", "dark")
    on_disk = json.loads(_config_path(fake_home).read_text())
    assert on_disk["theme"] == "dark"


def test_set_is_atomic_no_temp_leftover(fake_home: Path) -> None:
    cfg = Config()
    cfg.set("theme", "light")
    cfg_dir = _config_path(fake_home).parent
    assert not list(cfg_dir.glob(".config-*.tmp")), "atomic write left a temp file"


def test_reload_reads_persisted_value(fake_home: Path) -> None:
    Config().set("theme", "dark")
    assert Config().get("theme") == "dark"


def test_corrupt_file_falls_back_to_defaults(fake_home: Path) -> None:
    path = _config_path(fake_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not valid json ")
    cfg = Config()
    assert cfg.get("theme") == "auto"
