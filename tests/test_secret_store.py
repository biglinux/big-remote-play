"""SecretStore behavior without requiring a live Secret Service."""

from big_remote_play.utils.secret_store import InMemorySecretBackend, SecretKey, SecretStore, new_secret_id


def test_store_lookup_and_clear_secret() -> None:
    store = SecretStore(InMemorySecretBackend())
    key = SecretKey("zerotier", "api_token", "default")

    store.store(key, "zt-token", "ZeroTier token")

    assert store.lookup(key) == "zt-token"
    assert store.clear(key) is True
    assert store.lookup(key) == ""


def test_new_secret_id_is_opaque() -> None:
    first = new_secret_id()
    second = new_secret_id()

    assert first != second
    assert len(first) == 32
    assert first.isalnum()
