"""Why the other device may not see the mouse pointer: read from Sunshine's own log."""

import pytest

from big_remote_play.host.cursor_check import capture_with_pointer, cursor_report, desktop_session, read_cursor_report

KMS_LOST = "[..]: Info: Screencasting with KMS\n[..]: Warning: No KMS cursor plane found. Cursor may not be displayed while streaming!\n"
KMS_OK = "[..]: Info: Screencasting with KMS\n[..]: Info: Found cursor plane [105]\n"


@pytest.mark.parametrize(
    "session,desktop,expected",
    [("wayland", "kde", "kwin"), ("wayland", "gnome", "portal"), ("x11", "kde", "x11"), ("x11", "gnome", "x11"), ("", "", "")],
)
def test_a_lost_kms_pointer_recommends_the_capture_of_this_desktop(session, desktop, expected):
    report = read_cursor_report(KMS_LOST, session=session, desktop=desktop)
    assert report.lost and report.backend == "kms"
    assert report.recommended == expected


def test_a_found_cursor_plane_is_not_a_problem():
    assert not read_cursor_report(KMS_OK, session="wayland", desktop="kde").lost


@pytest.mark.parametrize(
    "marker", ["Atomic mode-setting must be enabled to capture the cursor!", "Unsupported cursor format: 875713089", "Unsupported non-linear cursor modifier: 1", "Failed to mmap cursor FB: x"]
)
def test_every_kms_cursor_failure_counts(marker):
    assert read_cursor_report(f"Screencasting with KMS\n{marker}\n", session="wayland", desktop="kde").lost


def test_only_the_latest_capture_choice_counts():
    log = KMS_LOST + "[..]: Info: Screencasting with KWin ScreenCast\n"
    assert read_cursor_report(log, session="wayland", desktop="kde") == read_cursor_report("Screencasting with KWin ScreenCast\n", session="wayland", desktop="kde")
    assert not read_cursor_report(log, session="wayland", desktop="kde").lost


def test_nothing_is_recommended_when_it_is_already_configured():
    assert read_cursor_report(KMS_LOST, session="wayland", desktop="kde", configured="kwin").recommended == ""


def test_no_log_means_nothing_is_claimed(tmp_path):
    assert not cursor_report(tmp_path / "missing.log").lost
    assert not read_cursor_report("", session="wayland", desktop="kde").lost


def test_the_log_file_is_read_with_the_session_of_this_desktop(tmp_path):
    log = tmp_path / "sunshine.log"
    log.write_text(KMS_LOST)
    report = cursor_report(log, environ={"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE"})
    assert report.recommended == "kwin"


@pytest.mark.parametrize(
    "environ,expected",
    [
        ({"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE"}, ("wayland", "kde")),
        ({"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}, ("wayland", "gnome")),
        ({"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "WindowMaker"}, ("x11", "windowmaker")),
        ({"WAYLAND_DISPLAY": "wayland-0"}, ("wayland", "")),
    ],
)
def test_desktop_session(environ, expected):
    assert desktop_session(environ) == expected


def test_kms_is_never_recommended():
    assert "kms" not in {capture_with_pointer(s, d) for s in ("wayland", "x11", "") for d in ("kde", "gnome", "")}


def test_an_earlier_run_of_sunshine_in_the_same_log_is_ignored():
    """Sunshine appends each run; the screen picker can delay the new choice by a minute."""
    log = "Info: Sunshine version: 2026.914\n" + KMS_LOST + "Info: Sunshine version: 2026.914\nInfo: config: 'capture' = portal\n"
    report = read_cursor_report(log, session="wayland", desktop="gnome", configured="portal")
    assert not report.lost and report.backend == ""


def test_a_run_without_any_encoder_says_no_picture_can_be_sent():
    log = "Info: Sunshine version: 2026.914\nError: Unable to initialize capture method\nFatal: Unable to find display or encoder during startup.\n"
    report = read_cursor_report(log, session="wayland", desktop="gnome")
    assert report.no_picture and not report.lost
    healthy = "Info: Sunshine version: 2026.914\n" + KMS_OK
    assert not read_cursor_report(log + healthy, session="wayland", desktop="gnome").no_picture  # a later run worked
