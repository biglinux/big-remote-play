"""Secret storage backed by the Freedesktop Secret Service.

Persistent VPN/API credentials belong in the user's keyring, not in JSON
history files or shell-script config directories.  The app uses libsecret
through GObject Introspection at runtime; tests inject the in-memory backend.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import threading
import time
from typing import Any, Protocol, TypeVar
import uuid

_T = TypeVar("_T")

# libsecret's *_sync calls have no timeout of their own: a locked KWallet, a
# GNOME Keyring waiting for its unlock prompt or a D-Bus activation that never
# finishes block the caller for as long as that lasts. Every call is therefore
# run on a helper thread and abandoned after this many seconds.
KEYRING_TIMEOUT_SECONDS = 10.0
# After a call that did not answer, further calls fail at once for a while
# instead of each one waiting again (and stacking unlock prompts).
KEYRING_RETRY_SECONDS = 60.0


class SecretStoreUnavailable(RuntimeError):
    """Raised when no Secret Service backend is available."""


class SecretStoreTimeout(SecretStoreUnavailable):
    """The Secret Service did not answer in time (locked, prompting or hung)."""


class _Breaker:
    """Process-wide memory of a keyring that recently did not answer."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._lock = threading.Lock()
        self._until = 0.0

    def open_for(self, seconds: float) -> None:
        with self._lock:
            self._until = self.clock() + seconds

    def is_open(self) -> bool:
        with self._lock:
            return self.clock() < self._until

    def reset(self) -> None:
        with self._lock:
            self._until = 0.0


_BREAKER = _Breaker()


def keyring_recently_unresponsive() -> bool:
    """True while calls are being refused because the keyring did not answer."""
    return _BREAKER.is_open()


def retry_keyring() -> None:
    """Allow the next call to try the keyring again (an explicit user action)."""
    _BREAKER.reset()


def _bounded(call: Callable[[], _T], timeout: float, on_late: Callable[[_T], object] | None = None) -> _T:
    """Run ``call`` on a helper thread; give up after ``timeout`` seconds.

    A call that is abandoned keeps running on its daemon thread until libsecret
    returns. The breaker keeps new calls from piling up behind it. When an
    abandoned call still succeeds — the person finished KWallet's "create a
    wallet" wizard or an unlock prompt after the timeout — the keyring works
    again: the breaker closes and ``on_late(value)`` runs on that thread.
    """
    if _BREAKER.is_open():
        raise SecretStoreTimeout("The system keyring did not answer recently")
    outcome: dict[str, Any] = {}
    finished = threading.Event()
    lock = threading.Lock()
    abandoned = [False]

    def run() -> None:
        try:
            outcome["value"] = call()
        except BaseException as error:  # delivered to the caller below
            outcome["error"] = error
        finally:
            with lock:
                finished.set()
                late = abandoned[0]
        if late and "error" not in outcome:
            _BREAKER.reset()
            if on_late is not None:
                try:
                    on_late(outcome["value"])
                except Exception:  # a late bookkeeping step never raises into a thread
                    pass

    threading.Thread(target=run, name="brp-keyring", daemon=True).start()
    finished.wait(timeout)
    with lock:
        if not finished.is_set():
            abandoned[0] = True
    if abandoned[0]:
        _BREAKER.open_for(KEYRING_RETRY_SECONDS)
        raise SecretStoreTimeout("The system keyring did not answer in time")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


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
            from gi.repository import Secret  # type: ignore

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
        try:
            ok = secret.password_store_sync(
                self._schema,
                key.attributes,
                secret.COLLECTION_DEFAULT,
                label,
                value,
                None,
            )
        except Exception as exc:
            # libsecret may be installed while the session service is absent,
            # locked or fails during D-Bus activation. Keep one app-facing error.
            raise SecretStoreUnavailable("Cannot store credentials in the system keyring") from exc
        if not ok:
            raise SecretStoreUnavailable("Secret Service refused to store the secret")

    def lookup(self, key: SecretKey) -> str | None:
        secret = self._require_secret()
        try:
            return secret.password_lookup_sync(self._schema, key.attributes, None)
        except Exception as exc:
            raise SecretStoreUnavailable("Cannot read credentials from the system keyring") from exc

    def clear(self, key: SecretKey) -> bool:
        secret = self._require_secret()
        try:
            return bool(secret.password_clear_sync(self._schema, key.attributes, None))
        except Exception as exc:
            raise SecretStoreUnavailable("Cannot remove credentials from the system keyring") from exc


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


def _default_backend() -> SecretBackend:
    return LibsecretBackend()


# Replaced by the test suite with an in-memory backend so no test can reach
# the developer's real keyring.
default_backend_factory: Callable[[], SecretBackend] = _default_backend


class SecretStore:
    """App-facing wrapper for the configured secret backend.

    Every backend call is bounded by ``timeout`` seconds (see
    :data:`KEYRING_TIMEOUT_SECONDS`). Still, these calls can block for that
    long: run them on a worker thread, never on the GTK main loop.
    """

    def __init__(self, backend: SecretBackend | None = None, *, timeout: float = KEYRING_TIMEOUT_SECONDS) -> None:
        self._backend = backend or default_backend_factory()
        self._timeout = timeout

    def is_available(self) -> bool:
        return self._backend.is_available()

    def store(self, key: SecretKey, value: str, label: str, *, on_late: Callable[[], object] | None = None) -> None:
        """Keep ``value``; ``on_late()`` runs if the keyring saves it only after the timeout."""
        if not value:
            self.clear(key)
            return
        _bounded(lambda: self._backend.store(key, value, label), self._timeout, None if on_late is None else (lambda _result: on_late()))

    def lookup(self, key: SecretKey) -> str:
        value = _bounded(lambda: self._backend.lookup(key), self._timeout)
        return value or ""

    def clear(self, key: SecretKey) -> bool:
        return _bounded(lambda: self._backend.clear(key), self._timeout)


def new_secret_id() -> str:
    """Return an opaque, non-secret identifier safe to store in JSON history."""
    return uuid.uuid4().hex
