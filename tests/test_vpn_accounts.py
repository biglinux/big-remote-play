from __future__ import annotations

import json
from pathlib import Path
from big_remote_play.utils.vpn_accounts import CommandResult, VPNAccountManager


class FakeSystemCheck:
    def tailscale_cmd(self):
        return ["tailscale"]

    def zerotier_cmd(self):
        return ["zerotier-cli"]


class ScriptedRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, argv, *, timeout=15):
        self.calls.append((list(argv), timeout))
        if not self.responses:
            raise AssertionError(f"unexpected command: {argv}")
        response = self.responses.pop(0)
        return response if isinstance(response, CommandResult) else CommandResult(*response)


def manager(tmp_path: Path, responses) -> tuple[VPNAccountManager, ScriptedRunner]:
    runner = ScriptedRunner(responses)
    return VPNAccountManager(FakeSystemCheck(), runner=runner, metadata_file=tmp_path / "accounts.json"), runner


def test_tailscale_profiles_parse_current_json_and_local_labels(tmp_path):
    mgr, runner = manager(
        tmp_path,
        [
            (
                0,
                json.dumps(
                    [
                        {"id": "1ab3", "tailnet": "home.example", "account": "ana@example.com", "nickname": "Home", "selected": True},
                        {"id": "9def", "tailnet": "games.example", "account": "ana@example.com", "nickname": "Friends", "selected": False},
                    ]
                ),
                "",
            )
        ],
    )
    mgr.set_tailscale_metadata("9def", friendly_name="Game night", provider="headscale", login_server="https://vpn.example")
    result = mgr.list_tailscale_profiles()

    assert result.switching_supported is True
    assert result.selected and result.selected.profile_id == "1ab3"
    assert result.profiles[1].display_name == "Game night"
    assert result.profiles[1].provider == "headscale"
    assert result.profiles[1].login_server == "https://vpn.example"
    assert result.profiles[0].removable is False
    assert runner.calls[-1][0] == ["tailscale", "switch", "--list", "--json"]


def test_tailscale_profile_fallback_uses_status_json(tmp_path):
    payload = {
        "BackendState": "Running",
        "CurrentTailnet": {"Name": "family.example", "MagicDNSSuffix": "family.ts.net"},
        "Self": {"HostName": "living-room", "UserID": 42},
        "User": {"42": {"LoginName": "jo@example.com", "DisplayName": "Jo"}},
    }
    mgr, runner = manager(tmp_path, [(1, "", "unknown command switch"), (0, json.dumps(payload), "")])
    result = mgr.list_tailscale_profiles()

    assert result.switching_supported is False
    assert len(result.profiles) == 1
    assert result.profiles[0].account == "jo@example.com"
    assert result.profiles[0].tailnet == "family.example"
    assert result.profiles[0].selected is True
    assert result.profiles[0].removable is False
    assert runner.calls[1][0] == ["tailscale", "status", "--json"]


def test_tailscale_switch_and_remove_validate_profile_ids(tmp_path):
    mgr, runner = manager(tmp_path, [(0, "ok", ""), (0, "ok", "")])
    assert mgr.switch_tailscale_profile("-bad").returncode == 2
    assert mgr.remove_tailscale_profile("current").returncode == 2
    assert mgr.switch_tailscale_profile("1ab3").returncode == 0
    assert mgr.remove_tailscale_profile("9def").returncode == 0
    assert runner.calls[0][0] == ["tailscale", "switch", "1ab3"]
    assert runner.calls[1][0] == ["tailscale", "switch", "remove", "9def"]


def test_tailscale_pause_is_not_logout(tmp_path):
    mgr, runner = manager(tmp_path, [(0, "", ""), (0, "", "")])
    assert mgr.pause_tailscale().returncode == 0
    assert mgr.logout_tailscale().returncode == 0
    assert runner.calls[0][0] == ["tailscale", "down"]
    assert runner.calls[1][0] == ["tailscale", "logout"]


def test_zerotier_lists_multiple_networks_and_preserves_friendly_names(tmp_path):
    payload = [
        {
            "nwid": "8056c2e21c000001",
            "name": "office",
            "status": "OK",
            "type": "PRIVATE",
            "portDeviceName": "ztabc",
            "assignedAddresses": ["10.10.1.2/24"],
        },
        {
            "nwid": "a09acf0233deadbe",
            "name": "friends",
            "status": "ACCESS_DENIED",
            "assignedAddresses": [],
        },
    ]
    mgr, runner = manager(tmp_path, [(0, json.dumps(payload), "")])
    mgr.set_zerotier_name("8056c2e21c000001", "Work games")
    result = mgr.list_zerotier_networks()

    assert result.needs_privilege is False
    assert [network.display_name for network in result.networks] == ["Work games", "friends"]
    assert result.networks[0].ready is True
    assert result.networks[1].awaiting_authorization is True
    assert result.networks[0].assigned_addresses == ("10.10.1.2/24",)
    assert runner.calls[-1][0] == ["zerotier-cli", "-j", "listnetworks"]


def test_zerotier_permission_failure_can_retry_through_pkexec(tmp_path):
    mgr, runner = manager(
        tmp_path,
        [
            (1, "", "cannot read authtoken.secret: Permission denied"),
            (0, "[]", ""),
        ],
    )
    result = mgr.list_zerotier_networks(allow_privileged=True)
    assert result.networks == ()
    assert result.needs_privilege is False
    assert runner.calls[0][0] == ["zerotier-cli", "-j", "listnetworks"]
    assert runner.calls[1][0] == ["pkexec", "zerotier-cli", "-j", "listnetworks"]


def test_zerotier_permission_failure_is_reported_without_prompt(tmp_path):
    mgr, _runner = manager(tmp_path, [(1, "", "401 access denied")])
    result = mgr.list_zerotier_networks(allow_privileged=False)
    assert result.needs_privilege is True
    assert result.networks == ()


def test_zerotier_join_and_leave_validate_network_ids(tmp_path):
    mgr, runner = manager(tmp_path, [(0, "200 join OK", ""), (0, "200 leave OK", "")])
    assert mgr.join_zerotier_network("bad").returncode == 2
    assert mgr.leave_zerotier_network("bad").returncode == 2
    assert mgr.join_zerotier_network("8056C2E21C000001").returncode == 0
    assert mgr.leave_zerotier_network("8056c2e21c000001").returncode == 0
    assert runner.calls[0][0] == ["zerotier-cli", "join", "8056c2e21c000001"]
    assert runner.calls[1][0] == ["zerotier-cli", "leave", "8056c2e21c000001"]


def test_metadata_file_contains_only_labels_and_provider_metadata(tmp_path):
    mgr, _runner = manager(tmp_path, [])
    mgr.set_tailscale_metadata("1ab3", friendly_name="Family", provider="tailscale")
    mgr.set_zerotier_name("8056c2e21c000001", "Friends")
    payload = json.loads((tmp_path / "accounts.json").read_text())

    assert payload["tailscale_profiles"]["1ab3"] == {"friendly_name": "Family", "provider": "tailscale"}
    assert payload["zerotier_networks"]["8056c2e21c000001"] == {"friendly_name": "Friends"}
    assert "token" not in json.dumps(payload).lower()
    assert (tmp_path / "accounts.json").stat().st_mode & 0o777 == 0o600


def test_tailscale_fallback_tolerates_malformed_nested_json(tmp_path):
    payload = {
        "BackendState": "Stopped",
        "CurrentTailnet": None,
        "Self": None,
        "User": [],
        "MagicDNSSuffix": "fallback.ts.net",
    }
    mgr, _runner = manager(tmp_path, [(1, "", "unknown command switch"), (0, json.dumps(payload), "")])

    result = mgr.list_tailscale_profiles()

    assert result.switching_supported is False
    assert len(result.profiles) == 1
    assert result.profiles[0].tailnet == "fallback.ts.net"
    assert result.profiles[0].account == ""
    assert result.profiles[0].nickname == ""
    assert result.profiles[0].selected is False


def test_zerotier_ignores_non_list_addresses(tmp_path):
    payload = [{"nwid": "8056c2e21c000001", "status": "OK", "assignedAddresses": None}]
    mgr, _runner = manager(tmp_path, [(0, json.dumps(payload), "")])

    result = mgr.list_zerotier_networks()

    assert len(result.networks) == 1
    assert result.networks[0].assigned_addresses == ()


def test_zerotier_command_falls_back_when_provider_returns_invalid_value(tmp_path):
    class InvalidSystemCheck(FakeSystemCheck):
        def zerotier_cmd(self):
            return None

    runner = ScriptedRunner([(0, "[]", "")])
    mgr = VPNAccountManager(InvalidSystemCheck(), runner=runner, metadata_file=tmp_path / "accounts.json")

    result = mgr.list_zerotier_networks()

    assert result.networks == ()
    assert runner.calls[0][0] == ["zerotier-cli", "-j", "listnetworks"]


class FakeProcess:
    def __init__(self, lines):
        self.stdout = iter(lines)
        self.waited = False

    def wait(self):
        self.waited = True
        return 0


def connecting_manager(tmp_path, runner_responses, script):
    """Manager whose ``tailscale up`` output is replayed from ``script``."""
    runner = ScriptedRunner(runner_responses)
    attempts: list[list[str]] = []

    def popen(argv, **_kwargs):
        attempts.append(list(argv))
        return FakeProcess(script[len(attempts) - 1])

    mgr = VPNAccountManager(FakeSystemCheck(), runner=runner, metadata_file=tmp_path / "accounts.json", popen=popen)
    return mgr, runner, attempts


def test_connect_uses_up_and_reports_the_daemons_own_state(tmp_path):
    mgr, runner, attempts = connecting_manager(
        tmp_path,
        [
            (0, ""),  # systemctl is-active tailscaled
            (0, json.dumps({"BackendState": "Running"})),
        ],
        [["To authenticate, visit:", "", "\thttps://login.tailscale.com/a/abc123", ""]],
    )
    opened: list[str] = []

    result = mgr.connect_tailscale(on_auth_url=opened.append)

    assert attempts == [["tailscale", "up", "--timeout=300s"]]
    assert opened == ["https://login.tailscale.com/a/abc123"]
    assert result.connected and result.backend_state == "Running"
    assert runner.calls[0][0] == ["systemctl", "is-active", "--quiet", "tailscaled"]


def test_connect_is_not_success_when_the_sign_in_never_completes(tmp_path):
    mgr, _runner, _attempts = connecting_manager(
        tmp_path,
        [(0, ""), (0, json.dumps({"BackendState": "NeedsLogin"}))],
        [["\thttps://login.tailscale.com/a/abc123", "timeout waiting for authentication"]],
    )

    result = mgr.connect_tailscale()

    assert not result.connected
    assert result.awaiting_authentication
    assert result.backend_state == "NeedsLogin"


def test_connect_sets_the_operator_only_after_access_is_denied(tmp_path):
    mgr, runner, attempts = connecting_manager(
        tmp_path,
        [
            (0, ""),  # daemon already running
            (0, ""),  # pkexec tailscale set --operator=<user>
            (0, json.dumps({"BackendState": "Running"})),
        ],
        [["Access denied: privileged operation"], ["Success."]],
    )

    result = mgr.connect_tailscale()

    assert len(attempts) == 2, "the same `up` is retried once, as the user"
    operator_call = runner.calls[1][0]
    assert operator_call[:3] == ["pkexec", "/usr/bin/tailscale", "set"]
    assert operator_call[3].startswith("--operator=")
    assert result.connected


def test_connect_passes_a_validated_headscale_server_and_a_key_file(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    mgr, _runner, attempts = connecting_manager(
        tmp_path,
        [(0, ""), (0, json.dumps({"BackendState": "Running"}))],
        [["Success."]],
    )

    result = mgr.connect_tailscale(login_server="vpn.example.org", auth_key="tskey-auth-test-only")

    argv = attempts[0]
    assert "--login-server=https://vpn.example.org" in argv
    assert not any("tskey-auth-test-only" in part for part in argv)
    key_argument = next(part for part in argv if part.startswith("--auth-key="))
    assert key_argument.startswith("--auth-key=file:")
    # The key file is removed once the attempt ends, successful or not.
    assert not Path(key_argument.split("file:", 1)[1]).exists()
    assert result.connected


def test_connect_refuses_a_login_server_that_is_not_https(tmp_path):
    mgr, _runner, attempts = connecting_manager(tmp_path, [], [])

    result = mgr.connect_tailscale(login_server="http://vpn.example.org")

    assert not result.connected and not attempts
    assert "invalid login server" in result.detail


def test_pause_escalates_only_when_the_operator_was_never_set(tmp_path):
    mgr, runner = manager(tmp_path, [(1, "", "Access denied: prefs access denied"), (0, "")])

    assert mgr.pause_tailscale().returncode == 0
    assert [call[0] for call in runner.calls] == [["tailscale", "down"], ["pkexec", "/usr/bin/tailscale", "down"]]


def test_adding_a_second_account_signs_in_instead_of_reusing_the_current_one(tmp_path):
    """`up` on a connected node offers no new sign-in; `login` switches profile."""
    mgr, _runner, attempts = connecting_manager(
        tmp_path,
        [
            (0, ""),  # daemon already running
            (0, json.dumps({"BackendState": "Running"})),
        ],
        [["\thttps://login.tailscale.com/a/second", "Success."]],
    )

    result = mgr.connect_tailscale(add_account=True)

    assert attempts == [["tailscale", "login", "--timeout=300s"]]
    assert result.connected


def test_a_new_account_left_stopped_is_brought_up(tmp_path):
    mgr, _runner, attempts = connecting_manager(
        tmp_path,
        [
            (0, ""),  # daemon already running
            (0, json.dumps({"BackendState": "Stopped"})),  # after login
            (0, json.dumps({"BackendState": "Running"})),  # after up
        ],
        [["Success."], ["Success."]],
    )

    result = mgr.connect_tailscale(add_account=True)

    assert [argv[1] for argv in attempts] == ["login", "up"]
    assert result.connected and result.backend_state == "Running"
