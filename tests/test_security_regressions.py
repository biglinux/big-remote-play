"""Regression tests for security findings from the Codex Security scan."""

from __future__ import annotations

import json
import re
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


def test_bundled_scripts_ship_executable() -> None:
    """Every bundled script is exec'd via pkexec, so it needs the execute bit here.

    The PKGBUILD installs them with ``cp -a``, which preserves the repo mode, and
    the app's own os.chmod cannot fix an installed root-owned file. A script
    committed 0644 therefore fails with EACCES for root too (install-vpn.sh did).
    """
    scripts = sorted((ROOT / "usr/share/big-remote-play/scripts").glob("*.sh"))
    assert scripts, "no bundled scripts found"
    not_executable = [s.name for s in scripts if not stat.S_IMODE(s.stat().st_mode) & 0o111]
    assert not_executable == []


def test_firewall_does_not_open_sunshine_web_ui(tmp_path: Path) -> None:
    import subprocess

    script = ROOT / "usr/share/big-remote-play/scripts/configure_firewall.sh"
    result = subprocess.run(["bash", str(script), "--dry-run"], capture_output=True, text=True, check=True)
    port_sets = [{int(value) for value in re.findall(r"\b\d+\b", line)} for line in result.stdout.splitlines() if line.strip()]
    assert {47984, 47989, 48010} in port_sets
    assert {47998, 47999, 48000, 5353, 48011} in port_sets
    assert all(47990 not in ports for ports in port_sets)
    assert all(48020 not in ports for ports in port_sets)
    assert "sysctl" not in script.read_text()


def test_network_scripts_do_not_persist_raw_tokens() -> None:
    zerotier = (ROOT / "usr/share/big-remote-play/scripts/create-network_zerotier.sh").read_text()
    headscale = (ROOT / "usr/share/big-remote-play/scripts/create-network_headscale.sh").read_text()

    assert "API_TOKEN_FILE" not in zerotier
    assert "api_token.txt" not in zerotier
    assert "big-remoteplay/zerotier" not in zerotier
    assert "brp_data api_key" not in zerotier
    assert "brp_data api_key" not in headscale
    assert "chmod -R 777" not in headscale
    assert "/var/lib/tailscale" not in headscale
    assert '--authkey="$AUTH_KEY"' not in headscale


def test_tailnet_auth_key_never_reaches_argv() -> None:
    """The key goes through a 0600 file; /proc/<pid>/cmdline is world-readable."""
    from big_remote_play.utils.vpn_accounts import tailscale_connect_argv

    argv = tailscale_connect_argv(["tailscale"], login_server="https://vpn.example", auth_key_path="/run/user/1000/brp/key")

    assert "--auth-key=file:/run/user/1000/brp/key" in argv
    assert not any("tskey-" in part for part in argv)
    source = (ROOT / "src/big_remote_play/utils/vpn_accounts.py").read_text()
    assert "secure_write_text(path, key)" in source


def test_network_scripts_match_current_provider_docs() -> None:
    zerotier = (ROOT / "usr/share/big-remote-play/scripts/create-network_zerotier.sh").read_text()
    headscale = (ROOT / "usr/share/big-remote-play/scripts/create-network_headscale.sh").read_text()

    assert "https://api.zerotier.com/api/v1" in zerotier
    assert "Authorization: token $API_TOKEN" in zerotier
    assert "Authorization: bearer $API_TOKEN" not in zerotier
    assert 'headscale_version="${HEADSCALE_VERSION:-0.29.1}"' in headscale
    assert "raw.githubusercontent.com/juanfont/headscale/v$headscale_version/config-example.yaml" in headscale
    assert "ip_prefixes:" not in headscale
    assert "0.0.0.0/0" not in headscale
    assert "read_only: true" in headscale
    assert "./config:/etc/headscale:ro" in headscale
    assert "./caddy_config:/config" in headscale
    assert '"443:443/udp"' in headscale
    assert "handle /generate_204" in headscale
    assert '$headscale_image" configtest' in headscale


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
