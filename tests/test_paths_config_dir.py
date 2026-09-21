"""Config-dir canonicalization + legacy migration (FABLE item 1).

The pre-2.0 config dir was ``~/.config/big-remoteplay``; the canonical dir is
``~/.config/big-remote-play``. A previous release moved the dir at import time,
which split config across both names. These tests pin the reconciliation.
"""

import json
from pathlib import Path

from big_remote_play import paths
from big_remote_play.utils.config import Config


def _legacy(home: Path) -> Path:
    return home / ".config" / "big-remoteplay"


def _canonical(home: Path) -> Path:
    return home / ".config" / "big-remote-play"


def test_config_dir_is_canonical(fake_home: Path) -> None:
    assert paths.CONFIG_DIR == _canonical(fake_home)
    assert paths.SUNSHINE_CONF == _canonical(fake_home) / "sunshine" / "sunshine.conf"


def test_migration_renames_legacy_only(fake_home: Path) -> None:
    legacy = _legacy(fake_home)
    (legacy / "sunshine").mkdir(parents=True)
    (legacy / "config.json").write_text('{"theme": "dark"}')

    paths.migrate_legacy_config_dir()

    assert not legacy.exists()
    assert json.loads((_canonical(fake_home) / "config.json").read_text())["theme"] == "dark"
    assert (_canonical(fake_home) / "sunshine").is_dir()


def test_migration_merges_split_brain_keeping_canonical(fake_home: Path) -> None:
    # Buggy state: canonical already holds the real data, legacy holds a stray
    # later write of the same file. Canonical must win on conflicts.
    canonical = _canonical(fake_home)
    canonical.mkdir(parents=True)
    (canonical / "config.json").write_text('{"theme": "light"}')
    (canonical / "private_network").mkdir()

    legacy = _legacy(fake_home)
    legacy.mkdir(parents=True)
    (legacy / "config.json").write_text('{"theme": "STALE"}')
    (legacy / "zerotier").mkdir()

    paths.migrate_legacy_config_dir()

    assert (legacy / "config.json").read_text() == '{"theme": "STALE"}'
    assert json.loads((canonical / "config.json").read_text())["theme"] == "light"
    # Entry present only in legacy is pulled in.
    assert (canonical / "zerotier").is_dir()


def test_migration_is_noop_without_legacy(fake_home: Path) -> None:
    paths.migrate_legacy_config_dir()  # nothing to do; must not raise
    assert not _legacy(fake_home).exists()


def test_importing_private_network_view_does_not_break_config_save(fake_home: Path) -> None:
    # Regression for the old import-time directory move that broke Config.save.
    import big_remote_play.ui.private_network_view  # noqa: F401

    cfg = Config()
    cfg.set("theme", "dark")
    on_disk = json.loads((_canonical(fake_home) / "config.json").read_text())
    assert on_disk["theme"] == "dark"


def test_migration_recurses_without_erasing_conflicting_libraries(fake_home):
    legacy = _legacy(fake_home) / "sunshine"
    canonical = _canonical(fake_home) / "sunshine"
    legacy.mkdir(parents=True)
    canonical.mkdir(parents=True)
    (legacy / "apps.json").write_text("legacy games")
    (legacy / "client.pem").write_text("client key")
    (canonical / "apps.json").write_text("current games")
    paths.migrate_legacy_config_dir()
    assert (canonical / "apps.json").read_text() == "current games"
    assert (canonical / "client.pem").read_text() == "client key"
    assert (legacy / "apps.json").read_text() == "legacy games"
    paths.migrate_legacy_config_dir()
    assert (legacy / "apps.json").read_text() == "legacy games"
