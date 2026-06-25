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
    assert "enable_api_endpoints = true" in config_text
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
