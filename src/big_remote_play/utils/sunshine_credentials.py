"""Sunshine admin credential storage.

The Sunshine server owns its credential database. Big Remote Play only needs a
local copy to authenticate API calls; that copy belongs in the user's Secret
Service wallet, not in ``sunshine.conf``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from pathlib import Path
import threading

from big_remote_play import paths
from big_remote_play.utils.secret_store import SecretKey, SecretStore, SecretStoreTimeout, SecretStoreUnavailable, retry_keyring
from big_remote_play.utils.secure_io import secure_write_text

_log = logging.getLogger("big-remoteplay")

SUNSHINE_PASSWORD_KEY = SecretKey("sunshine", "api_password", "default")
SUNSHINE_PASSWORD_LABEL = "Big Remote Play Sunshine API password"
# The administrator user Big Remote Play creates on a Sunshine that has none.
DEFAULT_SUNSHINE_USER = "sunshine"
LEGACY_SECRET_KEYS = {"sunshine_password", "credentials"}
API_CONFIG_DEFAULTS: dict[str, str] = {}  # The API is built in; no enabling keys or port reset is needed.


def default_sunshine_conf_path() -> Path:
    return paths.SUNSHINE_CONF


def _read_lines(conf_path: Path) -> list[str]:
    if not conf_path.exists():
        return []
    # A read failure is not an empty configuration. Propagate it rather than
    # silently rewriting the user's existing server settings from scratch.
    return conf_path.read_text().splitlines(keepends=True)


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
    on_late: Callable[[], object] | None = None,
) -> None:
    """Keep the password in the keyring, then name the user in ``sunshine.conf``.

    If the keyring answers only after its timeout, the user name is written
    then and ``on_late()`` runs (on the keyring's helper thread).
    """
    user = user.strip()
    if not user or not password:
        raise ValueError("Sunshine username and password cannot be empty")
    path = conf_path or default_sunshine_conf_path()
    updates = dict(API_CONFIG_DEFAULTS)
    updates["sunshine_user"] = user

    def saved_late() -> None:
        _rewrite_config(path, updates, LEGACY_SECRET_KEYS)
        if on_late is not None:
            on_late()

    store = secret_store or SecretStore()
    store.store(SUNSHINE_PASSWORD_KEY, password, SUNSHINE_PASSWORD_LABEL, on_late=saved_late)
    _rewrite_config(path, updates, LEGACY_SECRET_KEYS)


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
    """Move legacy plain-text credentials out of ``sunshine.conf``.

    Sunshine's own file is rewritten only when it still holds such a value;
    a configuration without one is left byte-for-byte as it is.
    """
    path = conf_path or default_sunshine_conf_path()
    if not path.exists():
        return
    if not LEGACY_SECRET_KEYS.intersection(_parse_config(_read_lines(path))):
        return
    load_sunshine_credentials(conf_path=path, secret_store=secret_store, migrate_legacy=True)


# ── one owner for the credentials the app uses ─────────────────────────────

KEYRING_UNKNOWN = "unknown"  # not asked yet
KEYRING_AVAILABLE = "available"  # answered
KEYRING_UNAVAILABLE = "unavailable"  # no Secret Service (or it refused)
KEYRING_UNRESPONSIVE = "unresponsive"  # did not answer in time (locked, prompting)

SAVED = "saved"  # in the keyring, used from now on
SESSION_ONLY = "session"  # used until the app closes; the keyring could not keep it


@dataclass(frozen=True)
class CredentialStatus:
    """What the interface may say about the credentials, without the password."""

    user: str
    known: bool  # a password is held for this session
    persisted: bool  # the keyring holds it
    keyring: str
    rejected: bool  # Sunshine refused the held password


class SunshineCredentials:
    """The Sunshine administrator credentials the app talks to Sunshine with.

    One instance per window. :meth:`get`, :meth:`remember` and the other
    methods that may touch the keyring or ``sunshine.conf`` block for up to
    the keyring timeout and must run on a worker thread. :meth:`cached` and
    :meth:`status` only read memory and are safe on the GTK main loop.

    The keyring is read once per session (again only after :meth:`retry`);
    the password is then served from memory, so a locked keyring is not
    asked again every few seconds. When the keyring cannot keep a password it
    is still used for this session and never written anywhere else.
    """

    def __init__(self, conf_path: Callable[[], Path] | None = None, *, store_factory: Callable[[], SecretStore] | None = None) -> None:
        self._conf_path = conf_path or default_sunshine_conf_path
        self._store_factory = store_factory or SecretStore
        self._state_lock = threading.Lock()
        # Keyring and sunshine.conf writes happen one at a time.
        self._io_lock = threading.Lock()
        self._credentials: tuple[str, str] | None = None
        self._loaded = False
        self._persisted = False
        self._rejected = False
        self._keyring = KEYRING_UNKNOWN
        # Called (from any thread) when the state changes by itself, e.g. a
        # keyring prompt finished after the timeout. Must be thread-safe.
        self.on_change: Callable[[], object] | None = None

    # GTK-safe -------------------------------------------------------------
    def cached(self) -> tuple[str, str] | None:
        with self._state_lock:
            return None if self._rejected else self._credentials

    def status(self) -> CredentialStatus:
        with self._state_lock:
            user = self._credentials[0] if self._credentials else ""
            return CredentialStatus(user, self._credentials is not None and not self._rejected, self._persisted, self._keyring, self._rejected)

    # worker thread --------------------------------------------------------
    def get(self) -> tuple[str, str] | None:
        """The credentials to use now, reading the keyring at most once."""
        with self._state_lock:
            if self._loaded:
                return None if self._rejected else self._credentials
        with self._io_lock:
            with self._state_lock:
                if self._loaded:  # another worker finished the read meanwhile
                    return None if self._rejected else self._credentials
            keyring = KEYRING_UNKNOWN
            found: tuple[str, str] | None = None
            persisted = False
            config: dict[str, str] = {}
            store = self._store_factory()
            path = self._conf_path()
            try:
                config = _parse_config(_read_lines(path))
            except OSError as error:
                # An unreadable sunshine.conf is an error, never an empty file.
                _log.warning("Cannot read Sunshine's configuration: %s", type(error).__name__)
            user = config.get("sunshine_user", "").strip()
            try:
                if not store.is_available():
                    raise SecretStoreUnavailable("Secret Service unavailable")
                # Asked even without a user name: the answer says whether the
                # keyring really works, which decides the first-run setup.
                password = store.lookup(SUNSHINE_PASSWORD_KEY)
                keyring = KEYRING_AVAILABLE
                if password and user:
                    found, persisted = (user, password), True
            except SecretStoreTimeout:
                keyring = KEYRING_UNRESPONSIVE
            except SecretStoreUnavailable:
                keyring = KEYRING_UNAVAILABLE
            legacy = _legacy_credentials(config)
            if found is None and legacy is not None:
                found = legacy
                if keyring == KEYRING_AVAILABLE:
                    # Move the plain-text password into the keyring; the file
                    # keeps it if the keyring cannot (never lose a credential).
                    try:
                        save_sunshine_credentials(legacy[0], legacy[1], conf_path=path, secret_store=store)
                        persisted = True
                    except (OSError, SecretStoreUnavailable):
                        pass
            with self._state_lock:
                self._keyring = keyring
                if keyring == KEYRING_UNRESPONSIVE and found is None and self._credentials is None:
                    # Try again after the keyring's cool-down, not on every call.
                    return None
                self._loaded = True
                if self._credentials is None and found:
                    self._credentials = found
                    self._persisted = persisted
                return None if self._rejected else self._credentials

    def remember(self, user: str, password: str, *, persist: bool = True) -> str:
        """Use these (already verified) credentials; keep them in the keyring if asked.

        Returns :data:`SAVED` or :data:`SESSION_ONLY`. The password is held in
        memory first, so a keyring failure never undoes a working login and is
        never mistaken for a pairing failure.
        """
        user = user.strip()
        if not user or not password:
            raise ValueError("Sunshine username and password cannot be empty")
        with self._state_lock:
            self._credentials = (user, password)
            self._loaded = True
            self._rejected = False
            self._persisted = False
        if not persist:
            return SESSION_ONLY
        return self.persist()

    def note_keyring(self, state: str) -> None:
        """Record what a keyring call made elsewhere found out."""
        with self._state_lock:
            self._keyring = state

    def adopt_saved(self, user: str, password: str) -> None:
        """Use credentials that are already in the keyring (no write)."""
        with self._state_lock:
            self._credentials = (user, password)
            self._loaded = True
            self._rejected = False
            self._persisted = True
            self._keyring = KEYRING_AVAILABLE

    def persist(self) -> str:
        """Write the held credentials to the keyring (worker thread)."""
        with self._state_lock:
            credentials = self._credentials
        if credentials is None:
            return SESSION_ONLY
        with self._io_lock:
            keyring = KEYRING_AVAILABLE

            def saved_late() -> None:
                # The person finished the keyring's own prompt after the timeout.
                with self._state_lock:
                    self._keyring = KEYRING_AVAILABLE
                    self._persisted = self._credentials == credentials
                callback = self.on_change
                if callback is not None:
                    callback()

            try:
                save_sunshine_credentials(credentials[0], credentials[1], conf_path=self._conf_path(), secret_store=self._store_factory(), on_late=saved_late)
            except SecretStoreTimeout:
                keyring = KEYRING_UNRESPONSIVE
            except SecretStoreUnavailable:
                keyring = KEYRING_UNAVAILABLE
            except OSError as error:
                # The keyring has it; sunshine.conf only names the user.
                _log.warning("Sunshine user name not written to its configuration: %s", type(error).__name__)
            with self._state_lock:
                self._keyring = keyring
                # Saved only if these are still the credentials in use.
                self._persisted = keyring == KEYRING_AVAILABLE and self._credentials == credentials
                return SAVED if self._persisted else SESSION_ONLY

    def mark_rejected(self) -> None:
        """Sunshine answered 401: stop using the held password until a new one is given."""
        with self._state_lock:
            self._rejected = True

    def retry(self) -> None:
        """Read the keyring again on the next :meth:`get` (an explicit user action)."""
        retry_keyring()
        with self._state_lock:
            if self._credentials is None or self._rejected:
                self._loaded = False
                self._credentials = None
                self._rejected = False
