from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "usr/bin/big-remote-play"
INSTALLER_PATH = ROOT / "tools/release/install_private_wheel.py"


def _load_installer():
    spec = importlib.util.spec_from_file_location("brp_private_wheel_installer", INSTALLER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INSTALLER = _load_installer()


def _stage_launcher(tmp_path: Path) -> tuple[Path, Path]:
    prefix = tmp_path / "usr"
    launcher = prefix / "bin/big-remote-play"
    launcher.parent.mkdir(parents=True)
    shutil.copy2(LAUNCHER, launcher)
    launcher.chmod(0o755)
    private_root = prefix / "lib/big-remote-play"
    return launcher, private_root


def _write_test_application(private_root: Path, output: Path, *, return_code: int = 0) -> None:
    package = private_root / "big_remote_play"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "app.py").write_text(
        "from importlib.metadata import version\n"
        "import json, os, sys\n"
        f"OUTPUT = {str(output)!r}\n"
        "def main():\n"
        "    with open(OUTPUT, 'w', encoding='utf-8') as stream:\n"
        "        json.dump({'pid': os.getpid(), 'argv': sys.argv, 'version': version('big-remote-play')}, stream)\n"
        f"    return {return_code}\n",
        encoding="utf-8",
    )
    dist_info = private_root / "big_remote_play-99.1.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.4\nName: big-remote-play\nVersion: 99.1\n",
        encoding="utf-8",
    )


def _write_hostile_pythonpath(root: Path) -> Path:
    package = root / "big_remote_play"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("raise RuntimeError('PYTHONPATH was not isolated')\n", encoding="utf-8")
    return root


def test_native_launcher_is_single_process_version_independent_and_isolated(tmp_path: Path) -> None:
    launcher, private_root = _stage_launcher(tmp_path)
    output = tmp_path / "result.json"
    _write_test_application(private_root, output, return_code=23)
    hostile = _write_hostile_pythonpath(tmp_path / "hostile")

    process = subprocess.Popen(
        [str(launcher), "--example", "value"],
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(hostile)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = process.communicate(timeout=15)

    assert process.returncode == 23, (stdout, stderr)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["pid"] == process.pid
    assert payload["argv"] == [str(launcher), "--example", "value"]
    assert payload["version"] == "99.1"


def test_native_launcher_reports_missing_private_installation(tmp_path: Path) -> None:
    launcher, private_root = _stage_launcher(tmp_path)

    completed = subprocess.run(
        [str(launcher)],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 1
    assert str(private_root / "big_remote_play/__init__.py") in completed.stderr


def _write_wheel(path: Path, extra_members: dict[str, bytes] | None = None) -> None:
    members = {
        "big_remote_play/__init__.py": b'__version__ = "1.0"\n',
        "big_remote_play/app.py": b"def main(): return 0\n",
        "big_remote_play-1.0.dist-info/METADATA": b"Metadata-Version: 2.4\nName: big-remote-play\nVersion: 1.0\n",
        "big_remote_play-1.0.dist-info/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        "big_remote_play-1.0.dist-info/RECORD": b"",
    }
    members.update(extra_members or {})
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)


def test_private_wheel_installer_preserves_package_and_metadata(tmp_path: Path) -> None:
    wheel = tmp_path / "big_remote_play-1.0-py3-none-any.whl"
    destination = tmp_path / "usr/lib/big-remote-play"
    _write_wheel(wheel)

    INSTALLER.install_private_wheel(wheel, destination)

    assert (destination / "big_remote_play/app.py").is_file()
    assert (destination / "big_remote_play-1.0.dist-info/METADATA").is_file()
    assert not list(destination.rglob("*.pyc"))


@pytest.mark.parametrize(
    "member",
    [
        "../escape.py",
        "big_remote_play/native.so",
        "big_remote_play/__pycache__/app.cpython-314.pyc",
        "big_remote_play-1.0.data/scripts/unexpected-command",
    ],
)
def test_private_wheel_installer_rejects_unsafe_or_abi_bound_content(tmp_path: Path, member: str) -> None:
    wheel = tmp_path / "big_remote_play-1.0-py3-none-any.whl"
    _write_wheel(wheel, {member: b"unsafe"})

    with pytest.raises(ValueError):
        INSTALLER.install_private_wheel(wheel, tmp_path / "private")
