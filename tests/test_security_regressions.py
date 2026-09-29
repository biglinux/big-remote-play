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


def test_no_privileged_helper_receives_an_api_credential() -> None:
    """Network creation and member approval moved to the Python API client.

    The former helpers ran as root, read tokens from stdin and passed them to
    curl on its command line (visible in /proc/<pid>/cmdline). No shipped
    script may take an API token, and none may call a provider API.
    """
    scripts = {path.name: path.read_text() for path in (ROOT / "usr/share/big-remote-play/scripts").glob("*.sh")}
    assert "create-network_zerotier.sh" not in scripts
    assert "create-network_headscale.sh" not in scripts
    for name, text in scripts.items():
        assert "API_TOKEN" not in text, name
        assert "Authorization:" not in text, name
        assert "api.zerotier.com" not in text and "api.cloudflare.com" not in text, name


def test_tailnet_auth_key_never_reaches_argv() -> None:
    """The key goes through a 0600 file; /proc/<pid>/cmdline is world-readable."""
    from big_remote_play.utils.vpn_accounts import tailscale_connect_argv

    argv = tailscale_connect_argv(["tailscale"], login_server="https://vpn.example", auth_key_path="/run/user/1000/brp/key")

    assert "--auth-key=file:/run/user/1000/brp/key" in argv
    assert not any("tskey-" in part for part in argv)
    source = (ROOT / "src/big_remote_play/utils/vpn_accounts.py").read_text()
    assert "secure_write_text(path, key)" in source


def test_provider_api_clients_match_current_upstream_contracts() -> None:
    from big_remote_play.private_network import headscale_api, tailscale_api, zerotier_api

    assert zerotier_api.LEGACY_BASE == "https://api.zerotier.com/api/v1"
    assert zerotier_api.CENTRAL_BASE == "https://central.zerotier.com/api/v2"
    assert tailscale_api.API_BASE == "https://api.tailscale.com/api/v2"
    # /api/v1/routes was removed in Headscale 0.26; routes are approved per node.
    source = (ROOT / "src/big_remote_play/private_network/headscale_api.py").read_text()
    assert '"/api/v1/routes' not in source
    assert "/approve_routes" in source
    assert headscale_api.registration_key("https://vpn.example/register/AbCdEf123456") == "AbCdEf123456"


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


def test_history_never_saves_credentials(tmp_path: Path, monkeypatch) -> None:
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
            "cf_token": "cf-secret",
        }
    )

    raw_history = Path(private_network_view.HISTORY_FILE).read_text()
    assert "secret" not in raw_history
    entry = json.loads(raw_history)["history"][0]
    assert entry["domain"] == "vpn.example.test"
    # One-time keys are not kept anywhere, not even in the keyring.
    assert backend._values == {}
    assert stat.S_IMODE(Path(private_network_view.HISTORY_FILE).stat().st_mode) == 0o600


def test_zerotier_token_uses_secret_store(tmp_path: Path, monkeypatch) -> None:
    from big_remote_play.private_network.credentials import CredentialKind, CredentialStore

    backend = InMemorySecretBackend()
    store = CredentialStore(SecretStore(backend), tmp_path / "credentials.json")
    store.save(CredentialKind.ZEROTIER_API_TOKEN, "ztFakeToken0123456789")

    assert store.secret(CredentialKind.ZEROTIER_API_TOKEN) == "ztFakeToken0123456789"
    assert "ztFakeToken0123456789" not in (tmp_path / "credentials.json").read_text()


def test_no_user_interface_module_performs_http_or_holds_tokens() -> None:
    """UI → service → provider API: the UI never builds a request itself."""
    for path in (ROOT / "src/big_remote_play/ui").glob("*.py"):
        text = path.read_text()
        assert "urllib.request" not in text, path.name
        assert '"Authorization":' not in text and "Authorization: " not in text, path.name
