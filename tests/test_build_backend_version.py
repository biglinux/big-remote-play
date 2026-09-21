from __future__ import annotations
import importlib.util
from pathlib import Path
import tarfile

_ROOT = Path(__file__).resolve().parents[1]
_RELEASE = _ROOT / "tools/release"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # The PEP 517 backend-path normally supplies this import path.
    import sys

    sys.path.insert(0, str(_RELEASE))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(_RELEASE))
    return module


_backend = _load("brp_build_backend_test", _RELEASE / "brp_build_backend.py")


def test_stamp_project_version() -> None:
    source = '[project]\nname = "big-remote-play"\nversion = "0.0.0"\n'
    result = _backend._stamp_text(Path("pyproject.toml"), source, "26.09.19")
    assert 'version = "26.09.19"' in result


def test_stamp_appstream_version_and_date() -> None:
    source = '<releases><release version="0.0.0" date="1970-01-01"/></releases>'
    result = _backend._stamp_text(Path("app.metainfo.xml"), source, "26.09.19")
    assert 'version="26.09.19"' in result
    assert 'date="2026-09-19"' in result


def test_stamp_pkgbuild_version() -> None:
    source = "pkgname=big-remote-play\npkgver=0.0.0\n"
    result = _backend._stamp_text(Path("PKGBUILD"), source, "26.09.19")
    assert "pkgver=26.09.19" in result


def test_stamp_targets_detects_python_version_assignment(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "big-remote-play"\nversion = "0.0.0"\n',
        encoding="utf-8",
    )
    module = tmp_path / "src" / "example.py"
    module.parent.mkdir()
    module.write_text('__version__ = "0.0.0"\n', encoding="utf-8")

    targets = _backend._stamp_targets(tmp_path)

    assert tmp_path / "pyproject.toml" in targets
    assert module in targets


def test_stamp_targets_ignore_only_the_projects_own_output(tmp_path: Path) -> None:
    """A checkout under a directory called build/ must still be stamped.

    makepkg builds in /build on BigLinux: matching the absolute path stopped
    every source file from being stamped there, so the package shipped
    `__version__ = "0.0.0"` next to a dated wheel."""
    root = tmp_path / "build" / "big-remote-play"
    (root / "src").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "big-remote-play"\nversion = "0.0.0"\n', encoding="utf-8")
    module = root / "src" / "example.py"
    module.write_text('__version__ = "0.0.0"\n', encoding="utf-8")
    own_output = root / "build" / "stale.py"
    own_output.parent.mkdir()
    own_output.write_text('__version__ = "0.0.0"\n', encoding="utf-8")

    targets = _backend._stamp_targets(root)

    assert module in targets
    assert own_output not in targets


def test_stamp_targets_detects_nested_pkgbuild(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "big-remote-play"\nversion = "0.0.0"\n',
        encoding="utf-8",
    )
    pkgbuild = tmp_path / "pkgbuild" / "PKGBUILD"
    pkgbuild.parent.mkdir()
    pkgbuild.write_text("pkgver=0.0.0\n", encoding="utf-8")

    targets = _backend._stamp_targets(tmp_path)

    assert pkgbuild in targets


def test_strip_sdist_compatibility_copy(tmp_path: Path, monkeypatch) -> None:
    archive = tmp_path / "example-1.0.tar.gz"
    source_root = tmp_path / "source" / "example-1.0"
    source_root.mkdir(parents=True)
    (source_root / "pyproject.toml").write_text('[project]\nname = "example"\n', encoding="utf-8")
    (source_root / "pyproject.toml.orig").write_text("generated copy\n", encoding="utf-8")
    executable = source_root / "tool.sh"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    with tarfile.open(archive, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
        tar.add(source_root, arcname=source_root.name)

    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1789862400")
    _backend._strip_sdist_compatibility_copy(archive)

    with tarfile.open(archive, mode="r:gz") as tar:
        names = tar.getnames()
        assert "example-1.0/pyproject.toml" in names
        assert "example-1.0/pyproject.toml.orig" not in names
        member = tar.getmember("example-1.0/tool.sh")
        assert member.mode & 0o111
        payload = tar.extractfile("example-1.0/pyproject.toml")
        assert payload is not None
        assert payload.read() == b'[project]\nname = "example"\n'
