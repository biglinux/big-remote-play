"""Resource path resolution and overrides."""

import importlib
import os
from pathlib import Path

from big_remote_play import paths


def test_script_path_resolves_existing_bundled_script() -> None:
    p = paths.script_path("drop_guest.sh")
    assert p.endswith("drop_guest.sh")
    assert os.path.exists(p)


def test_data_dir_has_icons() -> None:
    assert paths.ICONS_DIR.is_dir()
    assert (paths.ICONS_DIR / "big-remote-play.svg").exists()


def test_datadir_env_override(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "icons").mkdir()
    monkeypatch.setenv("BIG_REMOTE_PLAY_DATADIR", str(tmp_path))
    reloaded = importlib.reload(paths)
    try:
        assert reloaded.DATA_DIR == tmp_path
        assert reloaded.ICONS_DIR == tmp_path / "icons"
    finally:
        monkeypatch.delenv("BIG_REMOTE_PLAY_DATADIR", raising=False)
        importlib.reload(paths)
