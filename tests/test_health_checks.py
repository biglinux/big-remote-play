"""Support → Check this computer: each state in words, a safe action only when one exists."""

from big_remote_play.host import health_checks as hc


def by_key(facts):
    return {check.key: check for check in hc.checks(facts)}


def test_a_healthy_sharing_computer():
    rows = by_key(
        hc.Facts(
            sunshine_installed=True,
            sunshine_running=True,
            moonlight_installed=True,
            session="wayland",
            desktop="kde",
            keyring="available",
            password_known=True,
            password_persisted=True,
            firewall_blocked_ports=(),
            encoders=("AMD (VAAPI)",),
        )
    )
    assert {key: row.state for key, row in rows.items() if row.state != hc.INFO} == {key: hc.OK for key in ("sharing", "receiving", "input", "pointer", "keyring", "firewall", "picture")}
    assert all(not row.action for row in rows.values() if row.state == hc.OK)
    assert "KDE Plasma" in rows["session"].title and "Wayland" in rows["session"].title


def test_problems_offer_only_safe_actions():
    rows = by_key(
        hc.Facts(
            sunshine_installed=True,
            sunshine_running=True,
            unusable_devices=("/dev/uinput",),
            keyring="available",
            password_known=True,
            password_persisted=False,
            cursor_problem="No KMS cursor plane found",
            cursor_fix="kwin",
            firewall_blocked_ports=("47989/tcp",),
            firewall_tool="ufw",
        )
    )
    assert rows["input"].state == hc.PROBLEM and "/dev/uinput" in rows["input"].detail and not rows["input"].action
    assert (rows["pointer"].state, rows["pointer"].action) == (hc.WARNING, hc.ACTION_FIX_POINTER)
    assert (rows["keyring"].state, rows["keyring"].action) == (hc.WARNING, hc.ACTION_SAVE_KEYRING)
    assert (rows["firewall"].state, rows["firewall"].action) == (hc.PROBLEM, hc.ACTION_ALLOW_FIREWALL)


def test_checks_that_need_sharing_say_so_instead_of_guessing():
    rows = by_key(hc.Facts(sunshine_installed=True, sunshine_running=False))
    assert rows["sharing"].action == hc.ACTION_START
    assert rows["pointer"].state == rows["firewall"].state == hc.INFO


def test_keyring_states_are_distinct():
    assert by_key(hc.Facts(keyring="unresponsive"))["keyring"].action == hc.ACTION_RETRY_KEYRING
    assert by_key(hc.Facts(password_rejected=True))["keyring"].action == hc.ACTION_ENTER_PASSWORD
    assert by_key(hc.Facts(keyring="unavailable"))["keyring"].state == hc.WARNING


def test_every_state_has_words():
    assert {hc.state_label(state) for state in (hc.OK, hc.WARNING, hc.PROBLEM, hc.INFO)} == {"OK", "Check", "Problem", "Info"}


def test_a_support_report_never_carries_secrets_or_addresses():
    report = hc.facts_without_secrets(hc.Facts(password_known=True, password_persisted=True, firewall_blocked_ports=("47989/tcp",)))
    text = repr(report)
    assert "password_saved" in report and "pw" not in text.replace("password_saved", "")
    assert all(isinstance(value, (bool, str, list)) for value in report.values())


def test_moonlight_found_by_command_or_flatpak(tmp_path):
    assert hc.moonlight_installed(lambda name: "/usr/bin/moonlight" if name == "moonlight" else None, home=tmp_path)
    assert not hc.moonlight_installed(lambda name: None, home=tmp_path)
    (tmp_path / ".local/share/flatpak/app/com.moonlight_stream.Moonlight").mkdir(parents=True)
    # The Flatpak counts only when flatpak itself can start it.
    assert not hc.moonlight_installed(lambda name: None, home=tmp_path)
    assert hc.moonlight_installed(lambda name: "/usr/bin/flatpak" if name == "flatpak" else None, home=tmp_path)


def test_a_flatpak_only_moonlight_is_started_through_flatpak(tmp_path):
    """It used to be reported as installed, then the client found nothing to run."""
    from big_remote_play.guest.moonlight_client import moonlight_command

    (tmp_path / ".local/share/flatpak/app/com.moonlight_stream.Moonlight").mkdir(parents=True)
    flatpak_only = lambda name: "/usr/bin/flatpak" if name == "flatpak" else None  # noqa: E731
    assert moonlight_command(flatpak_only, home=tmp_path) == ["/usr/bin/flatpak", "run", "com.moonlight_stream.Moonlight"]
    both = lambda name: f"/usr/bin/{name}"  # noqa: E731
    assert moonlight_command(both, home=tmp_path) == "moonlight-qt"
