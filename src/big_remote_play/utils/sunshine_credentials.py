"""Sunshine admin credential storage.

The Sunshine server owns its credential database. Big Remote Play only needs a
local copy to authenticate API calls; that copy belongs in the user's Secret
Service wallet, not in ``sunshine.conf``.
"""

from __future__ import annotations

from pathlib import Path

from big_remote_play.utils.secret_store import SecretKey, SecretStore, SecretStoreUnavailable
from big_remote_play.utils.secure_io import secure_write_text

SUNSHINE_PASSWORD_KEY = SecretKey("sunshine", "api_password", "default")
SUNSHINE_PASSWORD_LABEL = "Big Remote Play Sunshine API password"
LEGACY_SECRET_KEYS = {"sunshine_password", "credentials"}
API_CONFIG_DEFAULTS = {
    "log_level": "2",
    "port": "47989",
    "webserver": "0.0.0.0",
    "enable_api_endpoints": "true",
}


def default_sunshine_conf_path() -> Path:
    return Path.home() / ".config" / "big-remoteplay" / "sunshine" / "sunshine.conf"


def _read_lines(conf_path: Path) -> list[str]:
    if not conf_path.exists():
        return []
    try:
        return conf_path.read_text().splitlines(keepends=True)
    except OSError:
        return []


def _parse_config(lines: list[str]) -> dict[str, str]:
    config: dict[str, str] = {}
    for line in lines:
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        config[key.strip()] = value.strip()
    return config


def _legacy_credentials(config: dict[str, str]) -> tuple[str, str] | None:
    user = config.get("sunshine_user", "").strip()
    password = config.get("sunshine_password", "").strip()
    if user and password:
        return (user, password)

    credentials = config.get("credentials", "").strip()
    if ":" not in credentials:
        return None
    legacy_user, legacy_password = credentials.split(":", 1)
    legacy_user = legacy_user.strip()
    legacy_password = legacy_password.strip()
    return (legacy_user, legacy_password) if legacy_user and legacy_password else None


def _rewrite_config(conf_path: Path, updates: dict[str, str], removed_keys: set[str]) -> None:
    lines = _read_lines(conf_path)
    final_lines: list[str] = []
    written: set[str] = set()

    for line in lines:
        if "=" not in line:
            final_lines.append(line)
            continue

        key = line.split("=", 1)[0].strip()
        if key in removed_keys:
            continue
        if key in updates:
            final_lines.append(f"{key} = {updates[key]}\n")
            written.add(key)
            continue
        final_lines.append(line)

    if final_lines and not final_lines[-1].endswith("\n"):
        final_lines[-1] += "\n"
    for key, value in updates.items():
        if key not in written:
            final_lines.append(f"{key} = {value}\n")

    secure_write_text(str(conf_path), "".join(final_lines))


def save_sunshine_credentials(
    user: str,
    password: str,
    *,
    conf_path: Path | None = None,
    secret_store: SecretStore | None = None,
) -> None:
    user = user.strip()
    if not user or not password:
        raise ValueError("Sunshine username and password cannot be empty")

    store = secret_store or SecretStore()
    store.store(SUNSHINE_PASSWORD_KEY, password, SUNSHINE_PASSWORD_LABEL)

    updates = dict(API_CONFIG_DEFAULTS)
    updates["sunshine_user"] = user
    _rewrite_config(conf_path or default_sunshine_conf_path(), updates, LEGACY_SECRET_KEYS)


def load_sunshine_credentials(
    *,
    conf_path: Path | None = None,
    secret_store: SecretStore | None = None,
    migrate_legacy: bool = True,
) -> tuple[str, str] | None:
    path = conf_path or default_sunshine_conf_path()
    config = _parse_config(_read_lines(path))
    user = config.get("sunshine_user", "").strip()
    store = secret_store or SecretStore()

    if user:
        try:
            password = store.lookup(SUNSHINE_PASSWORD_KEY)
        except SecretStoreUnavailable:
            password = ""
        if password:
            if migrate_legacy and LEGACY_SECRET_KEYS.intersection(config):
                _rewrite_config(path, {"sunshine_user": user, **API_CONFIG_DEFAULTS}, LEGACY_SECRET_KEYS)
            return (user, password)

    legacy = _legacy_credentials(config)
    if not legacy:
        return None

    if migrate_legacy:
        try:
            save_sunshine_credentials(legacy[0], legacy[1], conf_path=path, secret_store=store)
        except (OSError, SecretStoreUnavailable):
            pass
    return legacy


def ensure_sunshine_api_config(*, conf_path: Path | None = None, secret_store: SecretStore | None = None) -> None:
    path = conf_path or default_sunshine_conf_path()
    if not path.exists():
        return

    load_sunshine_credentials(conf_path=path, secret_store=secret_store, migrate_legacy=True)
    config = _parse_config(_read_lines(path))
    removed_keys = LEGACY_SECRET_KEYS if not LEGACY_SECRET_KEYS.intersection(config) else set()
    _rewrite_config(path, dict(API_CONFIG_DEFAULTS), removed_keys)
