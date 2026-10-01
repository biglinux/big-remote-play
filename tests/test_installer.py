"""Dependency detection and installation, without a terminal and without running a package manager.

Every external command is a fake: ``which``, ``pacman -Sp`` and the
transaction itself. The real contracts are argv, the package names that reach
the package manager, and that success is decided by looking again.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from big_remote_play.utils import dependencies as deps


def _only(available):
    return lambda name: f"/usr/bin/{name}" if name in available else None


# ── detection ─────────────────────────────────────────────────────────────


def test_detection_reports_each_component_in_order_without_repeats():
    states = deps.audit(["moonlight", "sunshine", "moonlight", "unknown"], which=_only({"moonlight", "sunshine"}))
    assert [(state.id, state.installed) for state in states] == [("moonlight", True), ("sunshine", True)]
    assert states[1].location == "/usr/bin/sunshine"


def test_moonlight_is_found_under_both_executable_names():
    assert deps.detect("moonlight", which=_only({"moonlight"})).installed
    assert deps.detect("moonlight", which=_only({"moonlight-qt"})).installed
    assert not deps.detect("moonlight", which=_only(set())).installed


def test_sunshine_installed_after_the_first_look_is_seen_at_once():
    """The bug: Sunshine installed, the app still asked for it. Nothing is cached."""
    installed: set[str] = set()
    which = lambda name: f"/usr/bin/{name}" if name in installed else None  # noqa: E731
    assert not deps.detect("sunshine", which=which).installed
    installed.add("sunshine")
    assert deps.detect("sunshine", which=which).installed


def test_a_vpn_client_installed_as_flatpak_counts_but_sunshine_flatpak_does_not():
    flatpak = lambda keyword: {"tailscale": "com.tailscale.Tailscale", "sunshine": "dev.lizardbyte.app.Sunshine"}.get(keyword)  # noqa: E731
    assert deps.detect("tailscale", which=_only(set()), flatpak=flatpak).location == "com.tailscale.Tailscale"
    # Big Remote Play starts Sunshine itself with its own configuration: a
    # Flatpak it cannot start is not reported as ready to share.
    assert not deps.detect("sunshine", which=_only(set()), flatpak=flatpak).installed


def test_components_for_a_task_add_the_secure_connection_only_when_used():
    assert deps.components_for("host") == ["sunshine"]
    assert deps.components_for("guest", network="tailscale") == ["moonlight", "tailscale"]
    assert deps.components_for("host", network="headscale") == ["sunshine", "tailscale"]
    assert deps.components_for(None, network="zerotier") == ["zerotier"]


# ── which package provides a component ────────────────────────────────────


def test_virtual_name_resolves_to_the_repository_provider_without_privileges():
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs.get("env", {}).get("LC_ALL")))
        return SimpleNamespace(returncode=0, stdout="libdep\nsunshine-bin\n")

    assert deps.resolve_package("sunshine", runner=runner) == "sunshine-bin"
    assert calls == [(["pacman", "-Sp", "--print-format", "%n", "sunshine"], "C")]


def test_an_exact_package_name_wins_over_its_dependencies():
    runner = lambda argv, **kwargs: SimpleNamespace(returncode=0, stdout="qt6-base\nmoonlight-qt\nsdl2\n")  # noqa: E731
    assert deps.resolve_package("moonlight-qt", runner=runner) == "moonlight-qt"


@pytest.mark.parametrize("name", ["", "-Syu", "x;rm -rf /", "a b", "../x"])
def test_package_names_are_validated_before_pacman_runs(name):
    def runner(*_args, **_kwargs):
        raise AssertionError("pacman must not run")

    assert deps.resolve_package(name, runner=runner) == ""


def test_an_unknown_package_or_missing_pacman_resolves_to_nothing():
    assert deps.resolve_package("sunshine", runner=lambda *a, **k: SimpleNamespace(returncode=1, stdout="")) == ""

    def missing(*_args, **_kwargs):
        raise FileNotFoundError("pacman")

    assert deps.resolve_package("sunshine", runner=missing) == ""


# ── how it is installed ───────────────────────────────────────────────────


def test_pamac_installs_the_resolved_packages_as_the_user_and_names_services():
    plan = deps.install_plan(["sunshine", "tailscale"], which=_only({"pamac", "pacman", "pkexec"}), resolve=lambda name: {"sunshine": "sunshine-bin"}.get(name, name))
    assert plan is not None and plan.method == "pamac"
    # No terminal, no sudo, no pkexec around Pamac: it asks for the password itself.
    assert plan.argv == ("pamac", "install", "--no-confirm", "sunshine-bin", "tailscale")
    assert plan.units == ("tailscaled",)


def test_without_pamac_the_reviewed_helper_receives_ids_never_package_names():
    plan = deps.install_plan(["moonlight", "zerotier"], which=_only({"pacman", "pkexec"}), helper="/usr/share/big-remote-play/scripts/install-components.sh")
    assert plan is not None and plan.method == "helper"
    assert plan.argv == ("pkexec", "/usr/share/big-remote-play/scripts/install-components.sh", "moonlight", "zerotier")
    assert plan.units == ()  # the helper enables the service itself, in the same authorization


def test_pamac_falls_back_to_the_helper_when_a_provider_cannot_be_resolved():
    plan = deps.install_plan(["sunshine"], which=_only({"pamac", "pacman", "pkexec"}), resolve=lambda name: "", helper="/h.sh")
    assert plan is not None and plan.method == "helper"


def test_no_supported_package_manager_means_no_plan():
    assert deps.install_plan(["sunshine"], which=_only(set()), helper="/h.sh") is None


@pytest.mark.parametrize("ids", [[], ["docker"], ["sunshine;touch /tmp/pwned"]])
def test_only_known_components_can_be_installed(ids):
    with pytest.raises(ValueError):
        deps.install_plan(ids, which=_only({"pamac"}))


# ── running it ────────────────────────────────────────────────────────────


class FakeProcess:
    def __init__(self, lines, code):
        self.stdout = iter(line + "\n" for line in lines)
        self._code = code

    def wait(self):
        return self._code


def _plan(method="pamac", units=()):
    return deps.InstallPlan(method, ("pamac", "install", "--no-confirm", "sunshine-bin"), ("sunshine",), ("sunshine-bin",), units)


def test_success_is_decided_by_looking_again_not_by_the_exit_code():
    lines: list[str] = []
    outcome = deps.run_install(
        _plan(),
        on_line=lines.append,
        popen=lambda argv, **kwargs: FakeProcess(["Preparing...", "BRP_PHASE 0.5", "Transaction successfully finished."], 0),
        probe=lambda ids: [deps.ComponentState("sunshine", False)],
    )
    assert not outcome.ok  # reported success, still missing: not a green check
    assert "BRP_PHASE 0.5" not in lines and "Preparing..." in lines


def test_installed_component_is_ok_even_after_a_noisy_transaction():
    outcome = deps.run_install(_plan(), popen=lambda argv, **kwargs: FakeProcess(["warning: something"], 1), probe=lambda ids: [deps.ComponentState("sunshine", True)])
    assert outcome.ok


def test_services_start_only_after_the_component_is_really_there():
    started: list[str] = []

    def start(unit):
        started.append(unit)
        return 0

    deps.run_install(_plan(units=("tailscaled",)), popen=lambda argv, **kwargs: FakeProcess([], 1), start_unit=start, probe=lambda ids: [deps.ComponentState("sunshine", False)])
    assert started == []
    outcome = deps.run_install(_plan(units=("tailscaled",)), popen=lambda argv, **kwargs: FakeProcess([], 0), start_unit=lambda unit: 1, probe=lambda ids: [deps.ComponentState("sunshine", True)])
    assert outcome.ok and outcome.service_failed == ("tailscaled",)


def test_a_dismissed_password_dialog_is_a_cancellation_not_an_error():
    plan = deps.InstallPlan("helper", ("pkexec", "/h.sh", "sunshine"), ("sunshine",), ("sunshine",), ())
    outcome = deps.run_install(plan, popen=lambda argv, **kwargs: FakeProcess([], 126), probe=lambda ids: [deps.ComponentState("sunshine", False)])
    assert outcome.cancelled and not outcome.ok


def test_the_transaction_runs_without_a_terminal_or_a_shell():
    seen = {}

    def popen(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        return FakeProcess([], 0)

    deps.run_install(_plan(), popen=popen, probe=lambda ids: [deps.ComponentState("sunshine", True)])
    assert seen["argv"][0] == "pamac" and "shell" not in seen
    assert seen["stdin"] is subprocess.DEVNULL  # nothing can wait for typed input


def test_a_missing_program_is_reported_and_the_state_looked_up_again():
    lines: list[str] = []

    def popen(argv, **kwargs):
        raise FileNotFoundError("pamac")

    outcome = deps.run_install(_plan(), on_line=lines.append, popen=popen, probe=lambda ids: [deps.ComponentState("sunshine", False)])
    assert not outcome.ok and lines == ["pamac"]


def test_a_refused_pamac_authorization_is_a_cancellation_not_a_network_error():
    plan = _plan()
    refused = deps.run_install(plan, popen=lambda argv, **kwargs: FakeProcess(["Preparing...", "Error: Authentication failed"], 1), probe=lambda ids: [deps.ComponentState("sunshine", False)])
    assert refused.cancelled and not refused.ok
    failed = deps.run_install(plan, popen=lambda argv, **kwargs: FakeProcess(["Error: Failed to synchronize databases"], 1), probe=lambda ids: [deps.ComponentState("sunshine", False)])
    assert not failed.cancelled and not failed.ok
