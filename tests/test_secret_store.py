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


# ── a keyring that does not answer never blocks the caller ─────────────────


def _hanging_backend(release):
    from big_remote_play.utils.secret_store import InMemorySecretBackend

    class Hanging(InMemorySecretBackend):
        def lookup(self, key):
            release.wait(5)
            return None

        def store(self, key, value, label):
            release.wait(5)

    return Hanging()


def test_an_unanswered_keyring_call_times_out_and_later_calls_fail_fast():
    import threading
    import time

    import pytest

    from big_remote_play.utils.secret_store import SecretKey, SecretStore, SecretStoreTimeout, SecretStoreUnavailable, keyring_recently_unresponsive, retry_keyring

    release = threading.Event()
    store = SecretStore(_hanging_backend(release), timeout=0.2)
    key = SecretKey("sunshine", "api_password", "default")
    started = time.monotonic()
    with pytest.raises(SecretStoreTimeout):
        store.lookup(key)
    assert time.monotonic() - started < 1.0
    assert keyring_recently_unresponsive()
    started = time.monotonic()
    with pytest.raises(SecretStoreUnavailable):  # no second wait, no second prompt
        store.store(key, "secret", "label")
    assert time.monotonic() - started < 0.1
    release.set()
    retry_keyring()  # an explicit "try again"
    assert not keyring_recently_unresponsive()


def test_backend_errors_still_reach_the_caller_as_app_errors():
    import pytest

    from big_remote_play.utils.secret_store import InMemorySecretBackend, SecretKey, SecretStore, SecretStoreUnavailable

    class Refuses(InMemorySecretBackend):
        def store(self, key, value, label):
            raise SecretStoreUnavailable("refused")

    with pytest.raises(SecretStoreUnavailable):
        SecretStore(Refuses()).store(SecretKey("a", "b", "c"), "secret", "label")


def test_a_save_finished_after_the_timeout_counts_and_reopens_the_keyring():
    import threading

    import pytest

    from big_remote_play.utils.secret_store import InMemorySecretBackend, SecretKey, SecretStore, SecretStoreTimeout, keyring_recently_unresponsive

    release, late = threading.Event(), threading.Event()

    class Prompting(InMemorySecretBackend):
        def store(self, key, value, label):
            release.wait(5)  # KWallet's "create a wallet" window, finished later
            super().store(key, value, label)

    backend = Prompting()
    key = SecretKey("a", "b", "c")
    with pytest.raises(SecretStoreTimeout):
        SecretStore(backend, timeout=0.2).store(key, "secret", "label", on_late=late.set)
    assert keyring_recently_unresponsive()
    release.set()
    assert late.wait(3)
    assert backend.lookup(key) == "secret"
    assert not keyring_recently_unresponsive()
