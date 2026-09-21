"""HostView subprocess command parsing stays shell-free."""

from pathlib import Path

from big_remote_play.ui.host_view import _parse_xrandr_monitor_names, _split_launch_command


def test_parse_xrandr_monitor_names() -> None:
    output = """Monitors: 2
 0: +*HDMI-1 1920/520x1080/290+0+0  HDMI-1
 1: +DP-2 2560/600x1440/340+1920+0  DP-2
"""

    assert _parse_xrandr_monitor_names(output) == ["HDMI-1", "DP-2"]


def test_split_launch_command_preserves_quoted_arguments() -> None:
    assert _split_launch_command('/usr/bin/game --profile "Living Room"') == [
        "/usr/bin/game",
        "--profile",
        "Living Room",
    ]


def test_split_launch_command_rejects_invalid_shell_syntax() -> None:
    assert _split_launch_command('/usr/bin/game "unterminated') == []


def test_host_view_does_not_use_shell_true() -> None:
    source = Path("src/big_remote_play/ui/host_view.py").read_text()

    assert "shell=True" not in source
