"""Administrative API credentials, kept in the Secret Service.

These credentials let Big Remote Play *administer* a network (list devices,
authorize a member, create a sign-in key). They are different from the
provisioning keys that *add a computer* to a network (Tailscale auth keys,
Headscale pre-auth keys), which are used once and never stored here.

Only non-secret metadata is written to JSON: which kind of credential exists,
for which server or tailnet, when it was saved and a masked hint such as
``tskey-api-••••••••3FxQ``. The secret itself lives only in the keyring.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path
import time
from typing import Any

from big_remote_play import paths
from big_remote_play.utils.secret_store import SecretKey, SecretStore, SecretStoreUnavailable
from big_remote_play.utils.secure_io import secure_write_text

MASK = "••••••••"
_MAX_SECRET_LENGTH = 4096


class CredentialKind(str, Enum):
    ZEROTIER_API_TOKEN = "zerotier_api_token"
    TAILSCALE_API_TOKEN = "tailscale_api_token"
    TAILSCALE_OAUTH_CLIENT = "tailscale_oauth_client"
    HEADSCALE_API_KEY = "headscale_api_key"

    @property
    def provider(self) -> str:
        return self.value.split("_", 1)[0]


# The ZeroTier token predates this module; keep its keyring attributes so a
# token saved by an earlier version is still found.
_LEGACY_KEYS = {CredentialKind.ZEROTIER_API_TOKEN: SecretKey("zerotier", "api_token", "default")}

# Recognisable prefixes of provisioning keys that must never be accepted as an
# administrative credential (and vice versa).
# Headscale pre-auth keys start with hskey-auth- since 0.28 (older ones are
# plain hex and cannot be told apart).
_PROVISIONING_PREFIXES = ("tskey-auth-", "hskey-auth-")
_EXPECTED_PREFIX = {
    CredentialKind.TAILSCALE_API_TOKEN: "tskey-api-",
    CredentialKind.TAILSCALE_OAUTH_CLIENT: "tskey-client-",
    CredentialKind.HEADSCALE_API_KEY: "hskey-api-",
}


class CredentialError(ValueError):
    """The pasted text is not a credential of the requested kind."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason  # empty | whitespace | too_long | auth_key | wrong_kind


@dataclass(frozen=True)
class CredentialInfo:
    kind: CredentialKind
    scope: str = "default"
    hint: str = ""
    created_at: float = 0.0
    client_id: str = ""
    # Non-secret facts learned about the credential: the ZeroTier Central
    # flavor ("legacy"/"central") or the tags an OAuth client must use.
    note: str = ""
    tags: tuple[str, ...] = ()


def mask_secret(secret: str) -> str:
    """Show at most a known prefix and the last four characters."""
    value = (secret or "").strip()
    if len(value) < 12:
        return MASK
    prefix = next((prefix for prefix in ("tskey-api-", "tskey-client-", "tskey-auth-", "hskey-api-") if value.startswith(prefix)), "")
    return f"{prefix}{MASK}{value[-4:]}"


def classify_secret(secret: str) -> str:
    """``auth_key``, ``api_token``, ``oauth_client`` or ``unknown``."""
    value = (secret or "").strip()
    if value.startswith(_PROVISIONING_PREFIXES):
        return "auth_key"
    if value.startswith(("tskey-api-", "hskey-api-")):
        return "api_token"
    if value.startswith("tskey-client-"):
        return "oauth_client"
    return "unknown"


def validate_secret(kind: CredentialKind, secret: str) -> str:
    """Return the cleaned secret or raise :class:`CredentialError`."""
    value = (secret or "").strip()
    if not value:
        raise CredentialError("empty")
    if len(value) > _MAX_SECRET_LENGTH:
        raise CredentialError("too_long")
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise CredentialError("whitespace")
    classified = classify_secret(value)
    if classified == "auth_key":
        # The most common mix-up: an auth key adds a computer; it cannot
        # administer anything, so saving it here would only fail later.
        raise CredentialError("auth_key")
    expected = _EXPECTED_PREFIX.get(kind)
    if expected and classified != "unknown" and not value.startswith(expected):
        raise CredentialError("wrong_kind")
    return value


def _secret_key(kind: CredentialKind, scope: str) -> SecretKey:
    if scope == "default" and kind in _LEGACY_KEYS:
        return _LEGACY_KEYS[kind]
    return SecretKey(kind.provider, kind.value, scope)


def _label(kind: CredentialKind) -> str:
    return {
        CredentialKind.ZEROTIER_API_TOKEN: "Big Remote Play ZeroTier API token",
        CredentialKind.TAILSCALE_API_TOKEN: "Big Remote Play Tailscale API access token",
        CredentialKind.TAILSCALE_OAUTH_CLIENT: "Big Remote Play Tailscale OAuth client",
        CredentialKind.HEADSCALE_API_KEY: "Big Remote Play Headscale API key",
    }[kind]


class CredentialStore:
    """Save, look up and remove administrative credentials.

    All methods may block on D-Bus and must run outside the GTK main thread.
    ``SecretStoreUnavailable`` propagates: without a keyring nothing is saved,
    and nothing falls back to a plain file.
    """

    def __init__(self, secret_store: SecretStore | None = None, metadata_file: Path | None = None) -> None:
        self._secrets = secret_store or SecretStore()
        self._metadata_file = metadata_file

    @property
    def metadata_file(self) -> Path:
        return self._metadata_file or (paths.CONFIG_DIR / "private_network" / "credentials.json")

    # ── metadata ───────────────────────────────────────────────────────────
    def _load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.metadata_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _save(self, payload: dict[str, Any]) -> None:
        secure_write_text(str(self.metadata_file), json.dumps(payload, indent=2, sort_keys=True) + "\n")

    # ── public API ─────────────────────────────────────────────────────────
    def save(self, kind: CredentialKind, secret: str, *, scope: str = "default", client_id: str = "", tags: tuple[str, ...] = ()) -> CredentialInfo:
        value = validate_secret(kind, secret)
        if kind is CredentialKind.TAILSCALE_OAUTH_CLIENT:
            client = (client_id or "").strip()
            if not client or any(character.isspace() for character in client):
                raise CredentialError("empty")
            stored = json.dumps({"client_id": client, "client_secret": value})
        else:
            client = ""
            stored = value
        self._secrets.store(_secret_key(kind, scope), stored, _label(kind))
        info = CredentialInfo(kind, scope=scope, hint=mask_secret(value), created_at=time.time(), client_id=client, tags=tuple(tags))
        payload = self._load()
        payload.setdefault(kind.value, {})[scope] = {"hint": info.hint, "created_at": info.created_at, "client_id": client, "tags": list(info.tags)}
        self._save(payload)
        return info

    def set_note(self, kind: CredentialKind, scope: str, note: str) -> None:
        payload = self._load()
        entry = payload.setdefault(kind.value, {}).setdefault(scope, {})
        if entry.get("note") != note:
            entry["note"] = note
            self._save(payload)

    def secret(self, kind: CredentialKind, scope: str = "default") -> str:
        """The stored secret (for an OAuth client: the client secret)."""
        raw = self._secrets.lookup(_secret_key(kind, scope))
        if kind is CredentialKind.ZEROTIER_API_TOKEN and not raw and scope == "default":
            raw = self._migrate_legacy_zerotier_file()
        if kind is CredentialKind.TAILSCALE_OAUTH_CLIENT and raw:
            try:
                return str(json.loads(raw).get("client_secret") or "")
            except (ValueError, AttributeError):
                return ""
        return raw

    def oauth_client(self, scope: str = "default") -> tuple[str, str]:
        raw = self._secrets.lookup(_secret_key(CredentialKind.TAILSCALE_OAUTH_CLIENT, scope))
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        return str(data.get("client_id") or ""), str(data.get("client_secret") or "")

    def info(self, kind: CredentialKind, scope: str = "default") -> CredentialInfo | None:
        entry = self._load().get(kind.value, {}).get(scope)
        if isinstance(entry, dict) and entry.get("hint"):
            return CredentialInfo(
                kind,
                scope=scope,
                hint=str(entry["hint"]),
                created_at=float(entry.get("created_at") or 0),
                client_id=str(entry.get("client_id") or ""),
                note=str(entry.get("note") or ""),
                tags=tuple(str(tag) for tag in entry.get("tags") or []),
            )
        # A credential saved by an older version has no metadata yet.
        secret = self.secret(kind, scope)
        if not secret:
            return None
        note = str(entry.get("note") or "") if isinstance(entry, dict) else ""
        return CredentialInfo(kind, scope=scope, hint=mask_secret(secret), note=note)

    def scopes(self, kind: CredentialKind) -> list[str]:
        entries = self._load().get(kind.value, {})
        return sorted(entries) if isinstance(entries, dict) else []

    def remove(self, kind: CredentialKind, scope: str = "default") -> None:
        self._secrets.clear(_secret_key(kind, scope))
        if kind is CredentialKind.ZEROTIER_API_TOKEN and scope == "default":
            self._remove_legacy_zerotier_file()
        payload = self._load()
        entries = payload.get(kind.value)
        if isinstance(entries, dict) and scope in entries:
            del entries[scope]
            if not entries:
                del payload[kind.value]
            self._save(payload)

    def remove_all(self) -> None:
        """Remove every administrative credential this app saved (Clear all data)."""
        payload = self._load()
        for kind in CredentialKind:
            scopes = set(payload.get(kind.value, {}) or {}) | {"default"}
            for scope in scopes:
                self._secrets.clear(_secret_key(kind, scope))
        self._remove_legacy_zerotier_file()
        try:
            self.metadata_file.unlink()
        except OSError:
            pass

    # ── legacy plain-text ZeroTier token ───────────────────────────────────
    @staticmethod
    def legacy_zerotier_file() -> Path:
        return paths.CONFIG_DIR / "zerotier" / "api_token.txt"

    def _migrate_legacy_zerotier_file(self) -> str:
        path = self.legacy_zerotier_file()
        try:
            token = path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
        if not token:
            return ""
        try:
            self._secrets.store(_LEGACY_KEYS[CredentialKind.ZEROTIER_API_TOKEN], token, _label(CredentialKind.ZEROTIER_API_TOKEN))
        except SecretStoreUnavailable:
            return ""  # never keep using the plain file without a keyring
        self._remove_legacy_zerotier_file()
        return token

    def _remove_legacy_zerotier_file(self) -> None:
        try:
            os.remove(self.legacy_zerotier_file())
        except OSError:
            pass
