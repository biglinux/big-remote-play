"""One-time cleanup of credentials that older versions left in plain JSON.

Versions before this module wrote auth keys, Headscale API keys and Cloudflare
API tokens into ``private_network/history.json`` (and, earlier still,
``private_network/private_network.json``) with mode 0644, readable by every
local user. This migration:

* tightens both files to 0600 immediately;
* moves each secret value into the Secret Service, labelled so the person can
  find it ("Big Remote Play legacy …"), and only then removes it from JSON;
* keeps every entry and every non-secret field; nothing is deleted if the
  keyring is unavailable (only the permissions are fixed).

It is idempotent and never logs a secret value.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import stat

from big_remote_play import paths
from big_remote_play.utils.secret_store import SecretKey, SecretStore, SecretStoreUnavailable
from big_remote_play.utils.secure_io import secure_write_text

_log = logging.getLogger("big-remoteplay")

LEGACY_SECRET_FIELDS = ("auth_key", "api_key", "cf_token")
LEGACY_FILES = ("history.json", "private_network.json")


@dataclass(frozen=True)
class MigrationReport:
    files: int = 0
    moved: int = 0
    kept_in_file: int = 0
    permissions_fixed: int = 0


def _tighten(path: Path) -> bool:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            os.chmod(path, 0o600)
            return True
    except OSError:
        pass
    return False


def migrate_legacy_secrets(store: SecretStore | None = None, directory: Path | None = None) -> MigrationReport:
    base = directory or (paths.CONFIG_DIR / "private_network")
    secrets = store  # created only when a secret is actually found
    files = moved = kept = fixed = 0
    for name in LEGACY_FILES:
        path = base / name
        if not path.is_file() or path.is_symlink():
            continue
        files += 1
        fixed += int(_tighten(path))
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # malformed: leave it exactly as it is (now owner-only)
        entries = payload.get("history") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            continue
        changed = False
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            provider = str(entry.get("vpn") or "private-network")
            identifier = f"{name}:{entry.get('id', index)}"
            for field in LEGACY_SECRET_FIELDS:
                value = entry.get(field)
                if not isinstance(value, str) or not value:
                    continue
                label = f"Big Remote Play legacy {provider} {field.replace('_', ' ')} ({entry.get('domain') or entry.get('network_id') or entry.get('timestamp') or index})"
                try:
                    if secrets is None:
                        secrets = SecretStore()
                    secrets.store(SecretKey(provider, f"legacy_{field}", identifier), value, label)
                except SecretStoreUnavailable:
                    kept += 1
                    continue
                del entry[field]
                moved += 1
                changed = True
        if changed:
            secure_write_text(str(path), json.dumps(payload, indent=2) + "\n")
    if moved or fixed:
        _log.info("Legacy private-network files secured: %d secrets moved to the keyring, %d files made owner-only", moved, fixed)
    return MigrationReport(files, moved, kept, fixed)


def clear_migrated_secrets(store: SecretStore | None = None, directory: Path | None = None) -> int:
    """Remove the keyring items created by :func:`migrate_legacy_secrets`.

    Their identifiers are derived from the (sanitized) legacy files, which keep
    every entry, so they can be recomputed without searching the keyring.
    """
    base = directory or (paths.CONFIG_DIR / "private_network")
    secrets = store or SecretStore()
    cleared = 0
    for name in LEGACY_FILES:
        try:
            payload = json.loads((base / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        entries = payload.get("history") if isinstance(payload, dict) else None
        for index, entry in enumerate(entries if isinstance(entries, list) else []):
            if not isinstance(entry, dict):
                continue
            provider = str(entry.get("vpn") or "private-network")
            identifier = f"{name}:{entry.get('id', index)}"
            for field in LEGACY_SECRET_FIELDS:
                try:
                    cleared += int(bool(secrets.clear(SecretKey(provider, f"legacy_{field}", identifier))))
                except SecretStoreUnavailable:
                    return cleared
    return cleared
