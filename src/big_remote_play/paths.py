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
import shutil
from pathlib import Path

_DATADIR_ENV = "BIG_REMOTE_PLAY_DATADIR"
_LOCALEDIR_ENV = "BIG_REMOTE_PLAY_LOCALEDIR"
_INSTALLED_DATADIR = Path("/usr/share/big-remote-play")
_INSTALLED_LOCALEDIR = Path("/usr/share/locale")

# Per-user writable state (config.json, sunshine/, private_network/, logs/).
# Canonical name matches the data dir and app id; the pre-2.0 name was
# "big-remoteplay". migrate_legacy_config_dir() reconciles the two once at
# startup — nothing else may reference the legacy path.
#
# CONFIG_DIR / SUNSHINE_CONFIG_DIR / SUNSHINE_CONF / LOG_DIR are exposed lazily
# via module __getattr__ so they resolve HOME/XDG_CONFIG_HOME at access time
# (keeps tests hermetic under a monkeypatched HOME) instead of freezing at import.
_LEGACY_CONFIG_DIRNAME = "big-remoteplay"
_CONFIG_DIRNAME = "big-remote-play"


def _config_root() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))


def _config_dir() -> Path:
    return _config_root() / _CONFIG_DIRNAME


def legacy_config_dir() -> Path:
    return _config_root() / _LEGACY_CONFIG_DIRNAME


def __getattr__(name: str) -> Path:
    lazy = {
        "CONFIG_DIR": _config_dir,
        "SUNSHINE_CONFIG_DIR": lambda: _config_dir() / "sunshine",
        "SUNSHINE_CONF": lambda: _config_dir() / "sunshine" / "sunshine.conf",
        "LOG_DIR": lambda: _config_dir() / "logs",
    }
    builder = lazy.get(name)
    if builder is not None:
        return builder()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def migrate_legacy_config_dir() -> None:
    """Reconcile the pre-2.0 config dir into the canonical one (idempotent).

    Three states are handled:
      * only the legacy dir exists  -> rename it to the canonical name;
      * both exist (split-brain left by the old import-time move)  -> merge the
        legacy entries missing from the canonical dir, preserving conflicts;
      * only the canonical dir (or neither) exists  -> nothing to do.

    Called once from app startup BEFORE any Config()/SunshineHost() is built, so
    it is never a module import side effect.
    """
    legacy = legacy_config_dir()
    canonical = _config_dir()
    if not legacy.exists() or legacy.resolve() == canonical.resolve():
        return
    if not canonical.exists():
        try:
            legacy.rename(canonical)
        except OSError:
            pass
        return

    # Merge missing descendants only. Conflicting legacy files may hold newer
    # credentials or a different game library; never delete them implicitly.
    def merge_missing(source: Path, target: Path) -> None:
        for item in source.iterdir():
            destination = target / item.name
            if item.is_symlink() or destination.is_symlink():
                continue
            if not destination.exists():
                shutil.move(str(item), str(destination))
            elif item.is_dir() and destination.is_dir():
                merge_missing(item, destination)
        try:
            source.rmdir()  # succeeds only when nothing remains to preserve
        except OSError:
            pass

    try:
        if not legacy.is_symlink() and not canonical.is_symlink():
            merge_missing(legacy, canonical)
    except OSError:
        pass


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
