"""secure_write_text: secrets land 0o600 in a 0o700 directory, atomically."""

import stat
from pathlib import Path

from big_remote_play.utils.secure_io import secure_write_text


def test_writes_content(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "token.txt"
    secure_write_text(str(target), "s3cret")
    assert target.read_text() == "s3cret"


def test_file_is_owner_only(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "token.txt"
    secure_write_text(str(target), "x")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_dir_is_owner_only(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "token.txt"
    secure_write_text(str(target), "x")
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700


def test_overwrite_no_temp_leftover(tmp_path: Path) -> None:
    target = tmp_path / "token.txt"
    secure_write_text(str(target), "one")
    secure_write_text(str(target), "two")
    assert target.read_text() == "two"
    assert not list(tmp_path.glob(".tmp-*"))
