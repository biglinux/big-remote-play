"""Installer package-manager selection (FABLE item 12).

No --noconfirm anywhere; AUR helper preferred, pkexec pacman fallback, and a
clear None when nothing is available.
"""

import big_remote_play.ui.installer_window as iw
from big_remote_play.ui.installer_window import _installer_argv


def _only(available):
    return lambda name: f"/usr/bin/{name}" if name in available else None


def test_prefers_yay(monkeypatch):
    monkeypatch.setattr(iw.shutil, "which", _only({"yay", "pacman"}))
    argv = _installer_argv()
    assert argv == ["yay", "-S", "--needed", "sunshine", "moonlight-qt"]
    assert "--noconfirm" not in argv


def test_paru_when_no_yay(monkeypatch):
    monkeypatch.setattr(iw.shutil, "which", _only({"paru", "pacman"}))
    assert _installer_argv()[0] == "paru"


def test_pacman_via_pkexec_fallback(monkeypatch):
    monkeypatch.setattr(iw.shutil, "which", _only({"pacman"}))
    argv = _installer_argv()
    assert argv[:3] == ["pkexec", "pacman", "-S"]
    assert "--noconfirm" not in argv


def test_none_when_unsupported(monkeypatch):
    monkeypatch.setattr(iw.shutil, "which", _only(set()))
    assert _installer_argv() is None
