"""VPN history deduplication (FABLE item 29).

Re-saving the same provider + domain/network updates the existing entry instead
of appending a duplicate (e.g. repeated Tailscale browser sign-ins).
"""

import importlib
import json
import sys
from pathlib import Path

from big_remote_play.utils.secret_store import InMemorySecretBackend, SecretStore


def _import_pnv(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    sys.modules.pop("big_remote_play.ui.private_network_view", None)
    pnv = importlib.import_module("big_remote_play.ui.private_network_view")
    monkeypatch.setattr(pnv, "_SECRET_STORE", SecretStore(InMemorySecretBackend()))
    monkeypatch.setattr(pnv, "HISTORY_FILE", str(tmp_path / "history.json"))
    return pnv


def _history(pnv):
    return json.loads(Path(pnv.HISTORY_FILE).read_text())["history"]


def test_repeated_tailscale_login_updates_single_entry(tmp_path, monkeypatch):
    pnv = _import_pnv(tmp_path, monkeypatch)

    first_id = pnv._save_history({"vpn": "tailscale", "domain": "Default Login"})
    second_id = pnv._save_history({"vpn": "tailscale", "domain": "Default Login"})

    history = _history(pnv)
    assert len(history) == 1  # not appended twice
    assert first_id == second_id  # same entry id reused


def test_distinct_networks_get_separate_entries(tmp_path, monkeypatch):
    pnv = _import_pnv(tmp_path, monkeypatch)

    pnv._save_history({"vpn": "zerotier", "network_id": "aaaa1111"})
    pnv._save_history({"vpn": "zerotier", "network_id": "bbbb2222"})

    assert len(_history(pnv)) == 2
