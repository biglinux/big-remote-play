"""Regression tests for security findings from the Codex Security scan."""

from __future__ import annotations

import json
import stat
import importlib
import sys
from pathlib import Path

from big_remote_play.utils.secret_store import InMemorySecretBackend, SecretStore

ROOT = Path(__file__).resolve().parents[1]


def _import_private_network_view(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    sys.modules.pop("big_remote_play.ui.private_network_view", None)
    return importlib.import_module("big_remote_play.ui.private_network_view")


def test_icu_repair_script_is_not_shipped() -> None:
    assert not (ROOT / "usr/share/big-remote-play/scripts/fix_sunshine_libs.sh").exists()


def test_firewall_does_not_open_sunshine_web_ui(tmp_path: Path) -> None:
    script = ROOT / "usr/share/big-remote-play/scripts/configure_firewall.sh"
    executable_lines = "\n".join(line for line in script.read_text().splitlines() if line.strip() and not line.lstrip().startswith("#"))

    assert "47990" not in executable_lines
    assert "47984-48020" not in executable_lines
    assert "TCP_PORTS=(47984 47989 48010)" in executable_lines
    assert "${port}/tcp" in executable_lines
    assert 'UDP_START="47998"' in executable_lines
    assert 'UDP_END="48020"' in executable_lines
    assert "${UDP_START}-${UDP_END}/udp" in executable_lines


def test_network_scripts_do_not_persist_raw_tokens() -> None:
    tailscale = (ROOT / "usr/share/big-remote-play/scripts/create-network_tailscale.sh").read_text()
    zerotier = (ROOT / "usr/share/big-remote-play/scripts/create-network_zerotier.sh").read_text()
    headscale = (ROOT / "usr/share/big-remote-play/scripts/create-network_headscale.sh").read_text()

    assert "AUTH_KEY_FILE" not in tailscale
    assert ".tailscale-script" not in tailscale
    assert "API_TOKEN_FILE" not in zerotier
    assert "api_token.txt" not in zerotier
    assert "big-remoteplay/zerotier" not in zerotier
    assert "brp_data api_key" not in zerotier
    assert "brp_data api_key" not in headscale
    assert "chmod -R 777" not in headscale
    assert "/var/lib/tailscale" not in headscale
    assert '--auth-key="$AUTH_KEY"' not in tailscale
    assert '--authkey="$AUTH_KEY"' not in headscale
    assert "file:/path/to/auth-key" in tailscale
    assert "write_auth_key_file" in tailscale
    assert "write_auth_key_file" in headscale
    assert "--force-reauth" in tailscale
    assert "--force-reauth" in headscale


def test_network_scripts_match_current_provider_docs() -> None:
    zerotier = (ROOT / "usr/share/big-remote-play/scripts/create-network_zerotier.sh").read_text()
    headscale = (ROOT / "usr/share/big-remote-play/scripts/create-network_headscale.sh").read_text()

    assert "https://api.zerotier.com/api/v1" in zerotier
    assert "Authorization: token $API_TOKEN" in zerotier
    assert "Authorization: bearer $API_TOKEN" not in zerotier
    assert 'HEADSCALE_VERSION="${HEADSCALE_VERSION:-0.29.1}"' in headscale
    assert "raw.githubusercontent.com/juanfont/headscale/v$HEADSCALE_VERSION/config-example.yaml" in headscale
    assert "ip_prefixes:" not in headscale
    assert "0.0.0.0/0" not in headscale
    assert "read_only: true" in headscale
    assert "./config:/etc/headscale:ro" in headscale
    assert "./caddy_config:/config" in headscale
    assert '"443:443/udp"' in headscale
    assert "handle /generate_204" in headscale
    assert '$HEADSCALE_IMAGE" configtest' in headscale


def test_sunshine_network_options_cover_current_docs() -> None:
    source = (ROOT / "src/big_remote_play/ui/sunshine_preferences.py").read_text()

    assert '"csrf_allowed_origins"' in source
    assert '"packetsize"' in source
    assert '"wan_encryption_mode",\n                _("WAN Encryption"),\n                "combo",\n                "1",' in source
    assert '"ping_timeout", _("Ping Timeout (ms)"), "spin", "10000"' in source


def test_stop_hosting_does_not_broad_kill_sunshine() -> None:
    source = (ROOT / "src/big_remote_play/ui/host_view.py").read_text()
    stop_hosting = source.split("def stop_hosting(", 1)[1].split("def _show_share_hint", 1)[0]

    assert "pkill" not in stop_hosting
    assert "self.sunshine.stop()" in stop_hosting


def test_history_saves_secret_refs_not_plaintext(tmp_path: Path, monkeypatch) -> None:
    private_network_view = _import_private_network_view(tmp_path, monkeypatch)

    backend = InMemorySecretBackend()
    monkeypatch.setattr(private_network_view, "_SECRET_STORE", SecretStore(backend))
    monkeypatch.setattr(private_network_view, "HISTORY_FILE", str(tmp_path / "history.json"))

    private_network_view._save_history(
        {
            "vpn": "headscale",
            "domain": "vpn.example.test",
            "auth_key": "hs-auth-secret",
            "api_key": "hs-admin-secret",
        }
    )

    raw_history = Path(private_network_view.HISTORY_FILE).read_text()
    assert "hs-auth-secret" not in raw_history
    assert "hs-admin-secret" not in raw_history

    history = json.loads(raw_history)["history"]
    entry = history[0]
    assert entry["domain"] == "vpn.example.test"
    assert set(entry["secret_refs"]) == {"auth_key", "api_key"}
    assert private_network_view._entry_secret(entry, "auth_key") == "hs-auth-secret"
    assert private_network_view._entry_secret(entry, "api_key") == "hs-admin-secret"
    assert stat.S_IMODE(Path(private_network_view.HISTORY_FILE).stat().st_mode) == 0o600


def test_zerotier_token_uses_secret_store(tmp_path: Path, monkeypatch) -> None:
    private_network_view = _import_private_network_view(tmp_path, monkeypatch)

    backend = InMemorySecretBackend()
    legacy_file = tmp_path / "api_token.txt"
    monkeypatch.setattr(private_network_view, "_SECRET_STORE", SecretStore(backend))
    monkeypatch.setattr(private_network_view, "LEGACY_ZT_TOKEN_FILE", str(legacy_file))

    private_network_view._set_zerotier_api_token("zt-secret")

    assert private_network_view._get_zerotier_api_token() == "zt-secret"
    assert not legacy_file.exists()
