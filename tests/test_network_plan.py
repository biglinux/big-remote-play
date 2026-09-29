"""The simple network page: next-step decision, one-click recovery, device names.

No real VPN client, network or keyring: commands go to scripted runners and
files to temporary directories.
"""

from __future__ import annotations

import json
import stat
from types import SimpleNamespace

import pytest

from big_remote_play.private_network.devices import NAME_LIMIT, DevicePreferences
from big_remote_play.private_network.diagnostics import NetworkFacts
from big_remote_play.private_network.history import SessionHistory
from big_remote_play.private_network.models import ConnectionState as S, PeerDevice, ProviderId as P, ProviderStatus, Recovery as R
from big_remote_play.private_network.plan import PlanKind, plan_connection
from big_remote_play.private_network.service import OfflinePrivateNetworkService
from big_remote_play.utils.vpn_accounts import CommandResult, VPNAccountManager, suggested_up_settings

MISSING = [ProviderStatus(p, S.UNAVAILABLE, installed=False, recovery=R.INSTALL) for p in P]


def with_status(status: ProviderStatus) -> list[ProviderStatus]:
    return [status, *[s for s in MISSING if s.provider is not status.provider]]


# ── next step ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "status, kind",
    [
        (ProviderStatus(P.TAILSCALE, S.CONNECTED, peers=(PeerDevice("pc", ("100.64.0.2",), online=True),)), PlanKind.READY),
        (ProviderStatus(P.TAILSCALE, S.DISCONNECTED, recovery=R.RECONNECT), PlanKind.TURNED_OFF),
        (ProviderStatus(P.ZEROTIER, S.UNAVAILABLE, recovery=R.START_SERVICE), PlanKind.SERVICE_STOPPED),
        (ProviderStatus(P.ZEROTIER, S.UNAVAILABLE, recovery=R.GRANT_ACCESS), PlanKind.ALLOW),
        (ProviderStatus(P.ZEROTIER, S.NEEDS_AUTHORIZATION, recovery=R.AUTHORIZE_DEVICE), PlanKind.WAITING),
        (ProviderStatus(P.TAILSCALE, S.NEEDS_AUTHENTICATION, recovery=R.SIGN_IN), PlanKind.SIGN_IN),
        (ProviderStatus(P.TAILSCALE, S.ERROR), PlanKind.PROBLEM),
        (ProviderStatus(P.ZEROTIER, S.DISCONNECTED, recovery=R.JOIN_NETWORK), PlanKind.SET_UP),
        (ProviderStatus(P.TAILSCALE, S.CONNECTING), PlanKind.WAITING),
    ],
)
def test_each_state_has_exactly_one_next_step(status, kind):
    plan = plan_connection(with_status(status))
    assert plan.kind is kind
    assert plan.provider is status.provider


def test_nothing_installed_recommends_installing_the_easiest_method():
    plan = plan_connection(MISSING)
    assert plan.kind is PlanKind.INSTALL and plan.provider is P.TAILSCALE
    assert plan_connection(MISSING, preferred=P.ZEROTIER).provider is P.ZEROTIER


def test_what_already_works_beats_what_was_remembered():
    working = ProviderStatus(P.ZEROTIER, S.CONNECTED)
    off = ProviderStatus(P.TAILSCALE, S.DISCONNECTED, recovery=R.RECONNECT)
    plan = plan_connection([off, working, MISSING[2]], preferred=P.TAILSCALE)
    assert plan.kind is PlanKind.READY and plan.provider is P.ZEROTIER


def test_a_configured_method_is_turned_on_instead_of_installing_another():
    """The development machine: Tailscale signed in but off, ZeroTier stopped."""
    statuses = [ProviderStatus(P.TAILSCALE, S.DISCONNECTED, recovery=R.RECONNECT), ProviderStatus(P.ZEROTIER, S.UNAVAILABLE, recovery=R.START_SERVICE), ProviderStatus(P.HEADSCALE, S.DISCONNECTED)]
    plan = plan_connection(statuses)
    assert plan.kind is PlanKind.TURNED_OFF and plan.provider is P.TAILSCALE


def test_no_internet_is_said_before_any_setup_but_never_hides_a_working_network():
    off = with_status(ProviderStatus(P.TAILSCALE, S.DISCONNECTED, recovery=R.RECONNECT))
    assert plan_connection(off, internet=False).kind is PlanKind.OFFLINE
    working = with_status(ProviderStatus(P.TAILSCALE, S.CONNECTED))
    assert plan_connection(working, internet=False).kind is PlanKind.READY
    assert plan_connection(None).kind is PlanKind.CHECKING


def test_online_devices_counts_only_other_online_devices():
    status = ProviderStatus(P.TAILSCALE, S.CONNECTED, self_device=PeerDevice("me", ("100.64.0.1",), is_self=True), peers=(PeerDevice("a", online=True), PeerDevice("b", online=False)))
    assert plan_connection(with_status(status)).online_devices == 1


# ── turning a signed-in client back on ────────────────────────────────────

NON_DEFAULT = """Error: changing settings via 'tailscale up' requires mentioning all
non-default flags. To proceed, either re-run your command with --reset or
use the command below to explicitly mention the current value of
all non-default settings:

\ttailscale up --login-server=https://hs.example.test --operator=player
"""


class Scripted:
    def __init__(self, *results):
        self.results = list(results)
        self.calls: list[list[str]] = []

    def __call__(self, argv, timeout=15):
        self.calls.append(list(argv))
        if argv[1:3] == ["status", "--json"]:
            return CommandResult(0, json.dumps({"BackendState": self.state}))
        return self.results.pop(0) if self.results else CommandResult(0)

    state = "Running"


def manager(runner):
    return VPNAccountManager(SimpleNamespace(tailscale_cmd=lambda: ["tailscale"]), runner=runner)


def test_turn_on_is_a_plain_up_when_nothing_else_is_needed():
    runner = Scripted(CommandResult(0))
    result = manager(runner).resume_tailscale()
    assert result.connected
    assert runner.calls[0] == ["tailscale", "up", "--timeout=30s"]


def test_turn_on_repeats_only_the_settings_the_client_asks_for():
    runner = Scripted(CommandResult(1, "", NON_DEFAULT), CommandResult(0))
    assert manager(runner).resume_tailscale().connected
    assert runner.calls[1] == ["tailscale", "up", "--timeout=30s", "--login-server=https://hs.example.test", "--operator=player"]


@pytest.mark.parametrize("flag", ["--auth-key=tskey-secret", "--reset", "--force-reauth", "--login-server=x; rm -rf /", "--unknown-flag"])
def test_turn_on_refuses_a_suggestion_that_would_change_the_account(flag):
    runner = Scripted(CommandResult(1, "", NON_DEFAULT.replace("--operator=player", flag)))
    runner.state = "Stopped"
    result = manager(runner).resume_tailscale()
    assert not result.connected
    assert len([c for c in runner.calls if c[1] == "up"]) == 1  # nothing else was run


def test_turn_on_reports_a_sign_in_instead_of_success():
    runner = Scripted(CommandResult(1, "", "not logged in"))
    runner.state = "NeedsLogin"
    result = manager(runner).resume_tailscale()
    assert not result.connected and result.backend_state == "NeedsLogin"


def test_suggested_settings_parser():
    assert suggested_up_settings("no suggestion here") == []
    assert suggested_up_settings("\ttailscale up --ssh --hostname=game-pc") == ["--ssh", "--hostname=game-pc"]
    assert suggested_up_settings("\ttailscale up --hostname='unterminated") is None


def test_service_turn_on_and_fix_use_the_allowlisted_units():
    service = OfflinePrivateNetworkService()
    calls = []
    service.manager = SimpleNamespace(
        resume_tailscale=lambda: SimpleNamespace(connected=True),
        start_service=lambda unit: calls.append(unit) or CommandResult(0),
    )
    assert service.turn_on(P.TAILSCALE)
    assert service.turn_on(P.ZEROTIER)
    assert service.start_service(P.HEADSCALE)
    assert calls == ["zerotier-one", "tailscaled"]


def test_internet_check_reads_only_local_interfaces():
    service = OfflinePrivateNetworkService()
    service._network_facts = lambda: NetworkFacts()
    assert not service.internet_available()
    service._network_facts = lambda: NetworkFacts(ipv6_global=("2001:db8::1",))
    assert service.internet_available()


# ── friendly names, favourites, recent connections ───────────────────────


def test_device_names_and_favourites_are_private_and_allowlisted(tmp_path):
    prefs = DevicePreferences(tmp_path / "devices.json")
    prefs.rename("100.64.0.2", "  Notebook\nda   sala\x00 ")
    prefs.set_favorite("100.64.0.2", True)
    assert prefs.get("100.64.0.2").name == "Notebook da sala"
    assert prefs.display_name("100.64.0.9", "fallback") == "fallback"
    prefs.rename("100.64.0.3", "x" * 500)
    assert len(prefs.get("100.64.0.3").name) == NAME_LIMIT
    path = tmp_path / "devices.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    stored = json.loads(path.read_text())
    assert set(stored["devices"]["100.64.0.2"]) == {"name", "favorite"}
    prefs.rename("100.64.0.2", "")
    prefs.set_favorite("100.64.0.2", False)
    assert "100.64.0.2" not in json.loads(path.read_text())["devices"]  # nothing left to keep
    with pytest.raises(ValueError):
        prefs.rename("--option", "x")


def test_a_damaged_preferences_file_is_ignored_not_trusted(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps({"devices": {"ok": {"name": "PC", "favorite": True}, "bad key": {"name": "x"}, "x": "not a dict", "y": {"favorite": "yes"}}}))
    found = DevicePreferences(path).all()
    assert set(found) == {"ok", "y"}
    assert found["y"].favorite is False
    path.write_text("{not json")
    assert DevicePreferences(path).all() == {}


def test_removing_a_recent_connection_removes_only_that_computer(tmp_path):
    history = SessionHistory(tmp_path / "s.json")
    for address in ("100.64.0.2", "100.64.0.9", "100.64.0.2"):
        history.finish(history.start(host_name="pc", host_address=address, port=47989, provider="tailscale"))
    assert history.forget_host("100.64.0.2", 47989) == 2
    assert [r.host_address for r in history.sessions()] == ["100.64.0.9"]
    assert history.recent_hosts()[0].last_connected is not None


def test_turn_on_without_operator_rights_asks_once_to_become_operator(monkeypatch):
    """Seen for real: `tailscale up` answers "Access denied: checkprefs access denied"."""
    monkeypatch.setattr("big_remote_play.utils.vpn_accounts.getpass.getuser", lambda: "player")
    denied = CommandResult(1, "", "Access denied: checkprefs access denied\n\nUse 'sudo tailscale up --timeout=30s'.")
    runner = Scripted(denied, CommandResult(0), CommandResult(0))
    assert manager(runner).resume_tailscale().connected
    commands = [c for c in runner.calls if c[1:3] != ["status", "--json"]]
    assert commands == [["tailscale", "up", "--timeout=30s"], ["pkexec", "/usr/bin/tailscale", "set", "--operator=player"], ["tailscale", "up", "--timeout=30s"]]
    assert not any(c[:2] == ["pkexec", "/usr/bin/tailscale"] and "up" in c for c in commands)  # never `up` as root


def test_turn_on_stops_when_the_password_is_refused():
    denied = CommandResult(1, "", "Access denied: checkprefs access denied")
    runner = Scripted(denied, CommandResult(126, "", "Not authorized"))
    runner.state = "Stopped"
    result = manager(runner).resume_tailscale()
    assert not result.connected
    assert len([c for c in runner.calls if "up" in c]) == 1
