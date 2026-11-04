"""Sunshine API credentials use the system secret store, not sunshine.conf."""

from pathlib import Path
import stat

from pytest import MonkeyPatch

from big_remote_play.utils.secret_store import InMemorySecretBackend, SecretStore
from big_remote_play.utils.sunshine_credentials import (
    SUNSHINE_PASSWORD_KEY,
    load_sunshine_credentials,
    save_sunshine_credentials,
)


def test_sunshine_credentials_save_password_in_secret_store(tmp_path: Path) -> None:
    conf = tmp_path / "sunshine.conf"
    store = SecretStore(InMemorySecretBackend())

    save_sunshine_credentials("admin", "api-secret", conf_path=conf, secret_store=store)

    config_text = conf.read_text()
    assert "sunshine_user = admin" in config_text
    assert "api-secret" not in config_text
    assert "sunshine_password" not in config_text
    assert "credentials =" not in config_text
    assert "enable_api_endpoints" not in config_text
    assert store.lookup(SUNSHINE_PASSWORD_KEY) == "api-secret"
    assert stat.S_IMODE(conf.stat().st_mode) == 0o600


def test_sunshine_credentials_migrate_legacy_plaintext(tmp_path: Path) -> None:
    conf = tmp_path / "sunshine.conf"
    conf.write_text("sunshine_user = legacy\nsunshine_password = legacy-secret\ncredentials = legacy:legacy-secret\nport = 47989\n")
    store = SecretStore(InMemorySecretBackend())

    assert load_sunshine_credentials(conf_path=conf, secret_store=store) == ("legacy", "legacy-secret")

    config_text = conf.read_text()
    assert "legacy-secret" not in config_text
    assert "sunshine_password" not in config_text
    assert "credentials =" not in config_text
    assert "sunshine_user = legacy" in config_text
    assert store.lookup(SUNSHINE_PASSWORD_KEY) == "legacy-secret"


def test_sunshine_config_manager_save_filters_legacy_secrets(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    from big_remote_play.ui.sunshine_preferences import SunshineConfigManager

    manager = SunshineConfigManager()
    manager.config.update(
        {
            "sunshine_user": "admin",
            "sunshine_password": "plain-secret",
            "credentials": "admin:plain-secret",
            "min_log_level": "2",
        }
    )

    manager.save()

    config_text = manager.config_file.read_text()
    assert "plain-secret" not in config_text
    assert "sunshine_password" not in config_text
    assert "credentials =" not in config_text
    assert "sunshine_user = admin" in config_text
    assert "min_log_level = 2" in config_text


# ── SunshineCredentials: one owner, read once, never blocking the window ───

import threading  # noqa: E402

import pytest  # noqa: E402

from big_remote_play.utils.secret_store import SecretStoreUnavailable  # noqa: E402
from big_remote_play.utils.sunshine_credentials import (  # noqa: E402
    KEYRING_AVAILABLE,
    KEYRING_UNAVAILABLE,
    KEYRING_UNRESPONSIVE,
    SAVED,
    SESSION_ONLY,
    SunshineCredentials,
    ensure_sunshine_api_config,
)


class CountingBackend(InMemorySecretBackend):
    def __init__(self):
        super().__init__()
        self.lookups = 0

    def lookup(self, key):
        self.lookups += 1
        return super().lookup(key)


class BrokenBackend(InMemorySecretBackend):
    def store(self, key, value, label):
        raise SecretStoreUnavailable("No session service")

    def lookup(self, key):
        raise SecretStoreUnavailable("No session service")


def service(tmp_path, backend):
    return SunshineCredentials(lambda: tmp_path / "sunshine.conf", store_factory=lambda: SecretStore(backend))


def test_the_keyring_is_read_once_per_session(tmp_path: Path) -> None:
    backend = CountingBackend()
    save_sunshine_credentials("sunshine", "pw", conf_path=tmp_path / "sunshine.conf", secret_store=SecretStore(backend))
    creds = service(tmp_path, backend)
    assert creds.cached() is None  # memory only: nothing read on the GTK thread
    for _ in range(5):
        assert creds.get() == ("sunshine", "pw")
    assert backend.lookups == 1
    status = creds.status()
    assert status.persisted and status.keyring == KEYRING_AVAILABLE and status.user == "sunshine"
    assert "pw" not in repr(status)


def test_without_a_keyring_a_verified_password_works_for_the_session_only(tmp_path: Path) -> None:
    conf = tmp_path / "sunshine.conf"
    conf.write_text("port = 47989\n")
    before = conf.read_bytes()
    creds = service(tmp_path, BrokenBackend())
    assert creds.get() is None and creds.status().keyring == KEYRING_UNAVAILABLE
    assert creds.remember("sunshine", "pw") == SESSION_ONLY
    assert creds.get() == ("sunshine", "pw")
    assert conf.read_bytes() == before  # nothing written anywhere, no plain-text fallback


def test_a_rejected_password_is_not_used_or_reread_until_a_new_one(tmp_path: Path) -> None:
    backend = CountingBackend()
    save_sunshine_credentials("sunshine", "old", conf_path=tmp_path / "sunshine.conf", secret_store=SecretStore(backend))
    creds = service(tmp_path, backend)
    assert creds.get() == ("sunshine", "old")
    creds.mark_rejected()
    assert creds.get() is None and creds.cached() is None and creds.status().rejected
    assert backend.lookups == 1  # no keyring hammering every few seconds
    assert creds.remember("sunshine", "new") == SAVED
    assert creds.get() == ("sunshine", "new") and not creds.status().rejected


def test_retry_reads_the_keyring_again(tmp_path: Path) -> None:
    backend = CountingBackend()
    creds = service(tmp_path, backend)
    assert creds.get() is None
    save_sunshine_credentials("sunshine", "pw", conf_path=tmp_path / "sunshine.conf", secret_store=SecretStore(backend))
    assert creds.get() is None  # still the first reading
    creds.retry()
    assert creds.get() == ("sunshine", "pw")


def test_an_unresponsive_keyring_is_reported_and_tried_again_later(tmp_path: Path, monkeypatch) -> None:
    from big_remote_play.utils import secret_store

    release = threading.Event()

    class Hanging(InMemorySecretBackend):
        def lookup(self, key):
            release.wait(5)
            return None

    (tmp_path / "sunshine.conf").write_text("sunshine_user = sunshine\n")
    creds = SunshineCredentials(lambda: tmp_path / "sunshine.conf", store_factory=lambda: SecretStore(Hanging(), timeout=0.2))
    assert creds.get() is None
    assert creds.status().keyring == KEYRING_UNRESPONSIVE
    release.set()
    secret_store.retry_keyring()


def test_concurrent_saves_never_interleave(tmp_path: Path) -> None:
    active, overlaps = [], []

    class Slow(InMemorySecretBackend):
        def store(self, key, value, label):
            active.append(1)
            if len(active) > 1:
                overlaps.append(1)
            threading.Event().wait(0.05)
            super().store(key, value, label)
            active.pop()

    creds = service(tmp_path, Slow())
    threads = [threading.Thread(target=creds.remember, args=("sunshine", f"pw{index}")) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert overlaps == []
    assert creds.cached() is not None


def test_remember_refuses_empty_credentials(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        service(tmp_path, InMemorySecretBackend()).remember("sunshine", "")


def test_a_configuration_without_legacy_secrets_is_left_untouched(tmp_path: Path) -> None:
    conf = tmp_path / "sunshine.conf"
    conf.write_text("port = 47989\n# comment kept\n")
    before = (conf.read_bytes(), conf.stat().st_mtime_ns)
    ensure_sunshine_api_config(conf_path=conf, secret_store=SecretStore(InMemorySecretBackend()))
    assert (conf.read_bytes(), conf.stat().st_mtime_ns) == before


def test_an_unreadable_configuration_is_never_replaced(tmp_path: Path) -> None:
    conf = tmp_path / "sunshine.conf"
    conf.mkdir()  # reading it fails
    creds = SunshineCredentials(lambda: conf, store_factory=lambda: SecretStore(InMemorySecretBackend()))
    assert creds.get() is None
    assert conf.is_dir()
