"""Secret storage backed by the Freedesktop Secret Service.

Persistent VPN/API credentials belong in the user's keyring, not in JSON
history files or shell-script config directories.  The app uses libsecret
through GObject Introspection at runtime; tests inject the in-memory backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
import uuid


class SecretStoreUnavailable(RuntimeError):
    """Raised when no Secret Service backend is available."""


@dataclass(frozen=True)
class SecretKey:
    """Stable lookup key for one app secret."""

    provider: str
    kind: str
    identifier: str

    @property
    def attributes(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "kind": self.kind,
            "id": self.identifier,
        }


class SecretBackend(Protocol):
    def is_available(self) -> bool: ...

    def store(self, key: SecretKey, value: str, label: str) -> None: ...

    def lookup(self, key: SecretKey) -> str | None: ...

    def clear(self, key: SecretKey) -> bool: ...


class LibsecretBackend:
    """libsecret backend loaded lazily so tests can run without a session bus."""

    def __init__(self) -> None:
        self._error: Exception | None = None
        self._secret: Any | None = None
        self._schema: Any | None = None
        try:
            import gi

            gi.require_version("Secret", "1")
            from gi.repository import Secret  # type: ignore[reportMissingModuleSource]

            self._secret = Secret
            self._schema = Secret.Schema.new(
                "org.biglinux.BigRemotePlay",
                Secret.SchemaFlags.NONE,
                {
                    "provider": Secret.SchemaAttributeType.STRING,
                    "kind": Secret.SchemaAttributeType.STRING,
                    "id": Secret.SchemaAttributeType.STRING,
                },
            )
        except Exception as exc:
            self._error = exc

    def is_available(self) -> bool:
        return self._secret is not None and self._schema is not None

    def _require_secret(self) -> Any:
        if not self.is_available():
            raise SecretStoreUnavailable(str(self._error) if self._error else "Secret Service unavailable")
        return self._secret

    def store(self, key: SecretKey, value: str, label: str) -> None:
        secret = self._require_secret()
        ok = secret.password_store_sync(
            self._schema,
            key.attributes,
            secret.COLLECTION_DEFAULT,
            label,
            value,
            None,
        )
        if not ok:
            raise SecretStoreUnavailable("Secret Service refused to store the secret")

    def lookup(self, key: SecretKey) -> str | None:
        secret = self._require_secret()
        return secret.password_lookup_sync(self._schema, key.attributes, None)

    def clear(self, key: SecretKey) -> bool:
        secret = self._require_secret()
        return bool(secret.password_clear_sync(self._schema, key.attributes, None))


class InMemorySecretBackend:
    """Small test backend with the same semantics as SecretBackend."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, str, str], str] = {}

    def is_available(self) -> bool:
        return True

    def store(self, key: SecretKey, value: str, label: str) -> None:
        self._values[(key.provider, key.kind, key.identifier)] = value

    def lookup(self, key: SecretKey) -> str | None:
        return self._values.get((key.provider, key.kind, key.identifier))

    def clear(self, key: SecretKey) -> bool:
        return self._values.pop((key.provider, key.kind, key.identifier), None) is not None


class SecretStore:
    """App-facing wrapper for the configured secret backend."""

    def __init__(self, backend: SecretBackend | None = None) -> None:
        self._backend = backend or LibsecretBackend()

    def is_available(self) -> bool:
        return self._backend.is_available()

    def store(self, key: SecretKey, value: str, label: str) -> None:
        if not value:
            self.clear(key)
            return
        self._backend.store(key, value, label)

    def lookup(self, key: SecretKey) -> str:
        value = self._backend.lookup(key)
        return value or ""

    def clear(self, key: SecretKey) -> bool:
        return self._backend.clear(key)


def new_secret_id() -> str:
    """Return an opaque, non-secret identifier safe to store in JSON history."""
    return uuid.uuid4().hex
