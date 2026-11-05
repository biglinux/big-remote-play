"""Share → Image and capture → Monitor / Display in an X11 session.

Seen on a real X11 desktop (amdgpu, three screens): the person chose
"DisplayPort-1", Sunshine's KMS capture logged "Couldn't map 'DisplayPort-1'
to a monitor index" and shared the first screen. The kernel calls that
screen DP-2.
"""

from __future__ import annotations

from pathlib import Path

from big_remote_play.host.display_connectors import drm_connector_edids, kms_connector_names, parse_xrandr_edids
from test_ui_task_flows import ui as _ui_fixture  # noqa: F401

ui = _ui_fixture

WIDE = bytes(range(128))
SMALL = bytes(reversed(range(128)))


def _props(**outputs: bytes) -> str:
    lines = ["Screen 0: minimum 320 x 200, current 9440 x 1440, maximum 16384 x 16384"]
    for name, edid in outputs.items():
        lines.append(f"{name} connected 3440x1440+0+0 (normal left inverted right x axis y axis) 800mm x 334mm")
        lines.append("\tEDID: ")
        hex_text = edid.hex()
        lines += ["\t\t" + hex_text[i : i + 32] for i in range(0, len(hex_text), 32)]
        lines.append("\tTearFree: auto ")
        lines.append("   3440x1440     59.97*+")
    lines.append("DisplayPort-2 disconnected (normal left inverted right x axis y axis)")
    return "\n".join(lines) + "\n"


def _drm(root: Path, **connectors: tuple[str, bytes]) -> Path:
    for name, (status, edid) in connectors.items():
        path = root / f"card0-{name}"
        path.mkdir(parents=True)
        (path / "status").write_text(status + "\n")
        (path / "edid").write_bytes(edid)
    return root


def test_x11_output_names_are_paired_with_kernel_connectors_by_edid(tmp_path):
    x = parse_xrandr_edids(_props(**{"DisplayPort-0": WIDE, "DisplayPort-1": SMALL}))
    assert x == {"DisplayPort-0": WIDE, "DisplayPort-1": SMALL}
    drm = drm_connector_edids(_drm(tmp_path, **{"DP-1": ("connected", WIDE), "DP-2": ("connected", SMALL), "HDMI-A-1": ("disconnected", b"")}))
    assert kms_connector_names(x, drm) == {"DisplayPort-0": "DP-1", "DisplayPort-1": "DP-2"}


def test_identical_screens_are_paired_in_connector_order():
    x = {"DisplayPort-10": WIDE, "DisplayPort-2": WIDE}
    drm = {"DP-11": WIDE, "DP-3": WIDE}
    assert kms_connector_names(x, drm) == {"DisplayPort-2": "DP-3", "DisplayPort-10": "DP-11"}


def test_a_screen_without_a_sure_match_is_left_alone():
    assert kms_connector_names({"DisplayPort-0": WIDE, "DisplayPort-1": WIDE}, {"DP-1": WIDE}) == {}
    assert kms_connector_names({"VGA-0": SMALL}, {"DP-1": WIDE}) == {}


def _choose(host, value: str) -> None:
    from gi.repository import Gtk

    host.available_monitors = [("Automatic", "auto"), ("01 · Wide · DisplayPort-0", "DisplayPort-0"), ("02 · Small · DisplayPort-1", "DisplayPort-1")]
    host.monitor_row.set_model(Gtk.StringList.new([label for label, _value in host.available_monitors]))
    host.monitor_row.set_selected([v for _label, v in host.available_monitors].index(value))


def test_sunshine_gets_the_kernel_name_of_the_chosen_screen(ui, monkeypatch):
    host = ui.host_view
    monkeypatch.setattr(host, "_kms_connectors", {"DisplayPort-0": "DP-1", "DisplayPort-1": "DP-2"})
    monkeypatch.setattr(host, "_pointer_capture", lambda: "")
    host.platform_row.set_selected(0)
    _choose(host, "DisplayPort-1")
    config = host._build_sunshine_config()
    assert config["output_name"] == "DP-2"
    # The session's own screen settings still use the session's name.
    assert "DisplayPort-1" in str(config["brp_stream_display"])


def test_x11_capture_keeps_the_x11_name(ui, monkeypatch):
    host = ui.host_view
    monkeypatch.setattr(host, "_kms_connectors", {"DisplayPort-1": "DP-2"})
    host.platform_row.set_selected(host._capture_values.index("x11"))
    _choose(host, "DisplayPort-1")
    assert host._build_sunshine_config()["output_name"] == "DisplayPort-1"


def test_each_screen_is_listed_once():
    from big_remote_play.ui.host_view import _unlisted_connectors

    listed = ["DisplayPort-0", "DisplayPort-1", "HDMI-A-0"]
    pairs = {"DisplayPort-0": "DP-1", "DisplayPort-1": "DP-2", "HDMI-A-0": "HDMI-A-1"}
    assert _unlisted_connectors(["DP-1", "DP-2", "HDMI-A-1"], listed, pairs) == []
    # A screen X11 does not show is still offered.
    assert _unlisted_connectors(["DP-1", "DP-2", "HDMI-A-1", "DP-3"], listed, pairs) == ["DP-3"]
    # Drivers that already use the kernel names (modesetting, Intel).
    assert _unlisted_connectors(["DP-1", "eDP-1"], ["DP-1", "eDP-1"], {}) == []


def test_a_screen_saved_under_its_kernel_name_is_still_selected(ui, monkeypatch):
    host = ui.host_view
    monkeypatch.setattr(host, "_kms_connectors", {"DisplayPort-0": "DP-1", "DisplayPort-1": "DP-2"})
    _choose(host, "DisplayPort-0")
    host.save_host_settings()
    settings = dict(host.config.get("host"))
    settings["monitor_output_name"] = "DP-2"
    host.config.set("host", settings)
    host.load_settings()
    assert host._shared_output() == "DisplayPort-1"


# ── the mouse pointer on a phone (X11) ──────────────────────────────────
def test_an_x11_session_uses_the_capture_that_draws_the_pointer(ui, monkeypatch):
    """Seen on a real X11 desktop: KMS capture streamed no pointer, so a
    phone (which shows only the pointer in the picture) saw none."""
    host = ui.host_view
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(host, "_kms_connectors", {"DisplayPort-1": "DP-2"})
    host.platform_row.set_selected(0)
    _choose(host, "DisplayPort-1")
    config = host._build_sunshine_config()
    # X11 capture finds the screen by the X11 name (checked with Sunshine 2026.914).
    assert (config["capture"], config["output_name"]) == ("x11", "DisplayPort-1")
    host._sync_pointer_capture_row()
    assert host.pointer_capture_row.get_visible() and not host._pointer_automatic_button.get_visible()


def test_a_chosen_capture_method_still_wins_on_x11(ui, monkeypatch):
    host = ui.host_view
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(host, "_kms_connectors", {"DisplayPort-1": "DP-2"})
    host.platform_row.set_selected(host._capture_values.index("kms"))
    _choose(host, "DisplayPort-1")
    assert (host._build_sunshine_config()["capture"], host._build_sunshine_config()["output_name"]) == ("kms", "DP-2")


def test_wayland_keeps_sunshine_s_own_choice(ui, monkeypatch):
    host = ui.host_view
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    host.platform_row.set_selected(0)
    assert host._build_sunshine_config()["capture"] is None
