"""Resource path resolution.

Python code ships inside the wheel (site-packages); all data assets (icons, img,
scripts, ui/style.css) and gettext catalogs stay under /usr/share. This module is
the single source of truth for locating them, working in three layouts:

1. Installed: code in site-packages, data in /usr/share/big-remote-play, catalogs
   in /usr/share/locale.
2. Dev tree: run from the repo with PYTHONPATH=src; data in usr/share/... relative
   to the repo root.
3. Override: BIG_REMOTE_PLAY_DATADIR / BIG_REMOTE_PLAY_LOCALEDIR for tests/relocation.
"""

import os
from pathlib import Path

_DATADIR_ENV = "BIG_REMOTE_PLAY_DATADIR"
_LOCALEDIR_ENV = "BIG_REMOTE_PLAY_LOCALEDIR"
_INSTALLED_DATADIR = Path("/usr/share/big-remote-play")
_INSTALLED_LOCALEDIR = Path("/usr/share/locale")


def _resolve_data_dir() -> Path:
    override = os.environ.get(_DATADIR_ENV)
    if override and Path(override).is_dir():
        return Path(override)
    # Dev tree: <repo>/usr/share/big-remote-play next to <repo>/src.
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "usr" / "share" / "big-remote-play"
        if (candidate / "icons").is_dir():
            return candidate
    return _INSTALLED_DATADIR


def _resolve_locale_dir() -> Path:
    override = os.environ.get(_LOCALEDIR_ENV)
    if override and Path(override).is_dir():
        return Path(override)
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "usr" / "share" / "locale"
        if candidate.is_dir():
            return candidate
    return _INSTALLED_LOCALEDIR


DATA_DIR = _resolve_data_dir()
ICONS_DIR = DATA_DIR / "icons"
IMG_DIR = DATA_DIR / "img"
SCRIPTS_DIR = DATA_DIR / "scripts"
STYLE_CSS = DATA_DIR / "ui" / "style.css"
LOCALE_DIR = _resolve_locale_dir()


def script_path(name: str) -> str:
    """Absolute path to a bundled script.

    Prefers the fixed installed location (/usr/share/big-remote-play/scripts) since
    scripts are executed via pkexec and polkit expects stable system paths; falls
    back to the resolved data dir when running from the dev tree.
    """
    installed = _INSTALLED_DATADIR / "scripts" / name
    if installed.exists():
        return str(installed)
    return str(SCRIPTS_DIR / name)
