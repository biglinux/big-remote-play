"""Network administration keys without a keyring (Window Maker/wlmaker sessions).

Reported 2026-11-17: Headscale's setup on this computer created an API key it
could not keep (no Secret Service), and the wizard then asked the person for a
key they never had.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from big_remote_play.private_network.credentials import CredentialKind, CredentialStore
from big_remote_play.utils.secret_store import InMemorySecretBackend, SecretStore, SecretStoreTimeout, SecretStoreUnavailable

KEY = "hskey-api-" + "A" * 40


class NoKeyring(InMemorySecretBackend):
    def store(self, key, value, label):
        raise SecretStoreUnavailable("no service")

    def lookup(self, key):
        raise SecretStoreUnavailable("no service")

    def clear(self, key):
        raise SecretStoreUnavailable("no service")


class Refusing(InMemorySecretBackend):
    """A keyring that answers lookups but refuses to save (its prompt dismissed)."""

    def store(self, key, value, label):
        raise SecretStoreUnavailable("dismissed")


class Locked(InMemorySecretBackend):
    def store(self, key, value, label):
        raise SecretStoreTimeout("locked")


def _store(tmp_path: Path, backend) -> CredentialStore:
    return CredentialStore(SecretStore(backend), metadata_file=tmp_path / "pn" / "credentials.json")


def test_without_any_keyring_the_key_is_kept_in_an_owner_only_file(tmp_path):
    _store(tmp_path, NoKeyring()).save(CredentialKind.HEADSCALE_API_KEY, KEY, scope="https://hs.example")
    secrets = tmp_path / "pn" / "secrets.json"
    assert stat.S_IMODE(secrets.stat().st_mode) == 0o600
    assert KEY not in (tmp_path / "pn" / "credentials.json").read_text()
    # A new session reads it back.
    assert _store(tmp_path, NoKeyring()).secret(CredentialKind.HEADSCALE_API_KEY, "https://hs.example") == KEY
    _store(tmp_path, NoKeyring()).remove(CredentialKind.HEADSCALE_API_KEY, "https://hs.example")
    assert not secrets.exists()


@pytest.mark.parametrize("backend", [Refusing, Locked])
def test_a_keyring_that_is_there_never_sends_the_key_to_a_file(tmp_path, backend):
    with pytest.raises(SecretStoreUnavailable):
        _store(tmp_path, backend()).save(CredentialKind.HEADSCALE_API_KEY, KEY, scope="https://hs.example")
    assert not (tmp_path / "pn" / "secrets.json").exists()


def test_a_keyring_that_appears_later_takes_the_key_over(tmp_path):
    _store(tmp_path, NoKeyring()).save(CredentialKind.HEADSCALE_API_KEY, KEY, scope="s")
    keyring = InMemorySecretBackend()
    assert _store(tmp_path, keyring).secret(CredentialKind.HEADSCALE_API_KEY, "s") == KEY
    assert not (tmp_path / "pn" / "secrets.json").exists()
    assert _store(tmp_path, keyring).secret(CredentialKind.HEADSCALE_API_KEY, "s") == KEY


def test_the_local_server_is_recognised_from_its_configuration(tmp_path):
    from big_remote_play.private_network.headscale_server import serves_here

    config = tmp_path / "config.yaml"
    config.write_text("server_url: https://ruscher.example.org\nlisten_addr: 127.0.0.1:8080\n")
    assert serves_here("https://ruscher.example.org", config=config, active=lambda: True) == "127.0.0.1:8080"
    assert serves_here("https://ruscher.example.org/", config=config, active=lambda: True) == "127.0.0.1:8080"
    assert serves_here("https://other.example.org", config=config, active=lambda: True) == ""
    assert serves_here("https://ruscher.example.org", config=config, active=lambda: False) == ""
    config.write_text("server_url: https://ruscher.example.org\nlisten_addr: 0.0.0.0:8080\n")
    assert serves_here("https://ruscher.example.org", config=config, active=lambda: True) == ""
    assert serves_here("https://x", config=tmp_path / "missing.yaml") == ""
