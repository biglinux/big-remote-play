"""drop_guest.sh input validation (security: runs as root via pkexec)."""

import subprocess

from big_remote_play import paths

_SCRIPT = paths.script_path("drop_guest.sh")


def _run(arg: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", _SCRIPT, arg], capture_output=True, text=True, timeout=10)


def test_rejects_empty() -> None:
    assert subprocess.run(["bash", _SCRIPT], capture_output=True, timeout=10).returncode == 1


def test_rejects_command_injection() -> None:
    r = _run("1.2.3.4 evilarg")
    assert r.returncode == 1
    assert "invalid IP" in r.stdout


def test_rejects_garbage() -> None:
    assert _run("999.bad@x").returncode == 1


def test_accepts_valid_ipv4() -> None:
    # Validation passes; ss may fail without CAP_NET_ADMIN but the script exits 0.
    assert _run("10.0.0.5").returncode == 0


def test_accepts_valid_ipv6() -> None:
    assert _run("fe80::1").returncode == 0
