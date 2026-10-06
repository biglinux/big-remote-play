"""A settings file that does not parse is kept, never overwritten by defaults."""

from __future__ import annotations

from big_remote_play import paths
from big_remote_play.private_network.credentials import CredentialStore
from big_remote_play.utils.config import Config
from big_remote_play.utils.vpn_accounts import _load_metadata, _save_metadata


def _aside(directory, name):
    return sorted(p.name for p in directory.iterdir() if p.name.startswith(name + ".corrupt-"))


def test_a_malformed_config_json_is_kept_aside_before_defaults_are_saved():
    paths.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    original = '{"theme": "gamer",'
    (paths.CONFIG_DIR / "config.json").write_text(original)
    config = Config()
    config.save()
    kept = _aside(paths.CONFIG_DIR, "config.json")
    assert len(kept) == 1
    assert (paths.CONFIG_DIR / kept[0]).read_text() == original


def test_malformed_vpn_metadata_is_kept_aside(tmp_path):
    path = tmp_path / "accounts.json"
    path.write_text("[not an object]")
    metadata = _load_metadata(path)
    _save_metadata(metadata, path)
    assert _aside(tmp_path, "accounts.json")


def test_malformed_private_network_metadata_is_kept_aside(tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text("{oops")

    class _NoSecrets:
        pass

    store = CredentialStore(secret_store=_NoSecrets(), metadata_file=path)
    assert store._load() == {}
    assert _aside(tmp_path, "credentials.json")


def test_an_unreadable_file_is_left_where_it_is(tmp_path):
    path = tmp_path / "accounts.json"
    path.write_text('{"version": 1}')
    path.chmod(0)
    try:
        _load_metadata(path)
        assert path.exists() and not _aside(tmp_path, "accounts.json")
    finally:
        path.chmod(0o600)
