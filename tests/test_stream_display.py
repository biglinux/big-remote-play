"""Preparing the shared screen for a stream: HDR → SDR and optional resolution.

``kscreen-doctor`` is scripted; no real display changes. The outputs mirror a
real KDE Plasma machine: an HDR ultrawide (the one Sunshine captures) and
an SDR secondary screen.
"""

from __future__ import annotations

import json
import stat
import subprocess

from big_remote_play.host.sunshine_manager import SunshineHost
from big_remote_play.host.sunshine_sessions import read_video
from big_remote_play.host.stream_display import MARKER, Request, StreamDisplay, main, merge_prep_commands, parse_outputs, plan_session, prep_command

from test_ui_task_flows import ui as _ui_fixture  # the hermetic window fixture

ui = _ui_fixture

KSCREEN = {
    "outputs": [
        {
            "name": "DP-1",
            "enabled": True,
            "hdr": True,
            "wcg": True,
            "currentModeId": "89",
            "modes": [
                {"id": "89", "size": {"width": 3440, "height": 1440}, "refreshRate": 159.96},
                {"id": "92", "size": {"width": 3440, "height": 1440}, "refreshRate": 59.99},
                {"id": "95", "size": {"width": 1920, "height": 1080}, "refreshRate": 60.0},
                {"id": "93", "size": {"width": 1920, "height": 1080}, "refreshRate": 120.0},
            ],
        },
        {"name": "DP-2", "enabled": True, "hdr": False, "wcg": False, "currentModeId": "34", "modes": [{"id": "34", "size": {"width": 2560, "height": 1080}, "refreshRate": 74.99}]},
        {"name": "bad name; rm", "enabled": True, "hdr": True},
    ]
}


def outputs():
    return parse_outputs(KSCREEN)


def test_an_hdr_screen_is_shared_in_sdr_to_a_device_without_hdr():
    plan = plan_session(outputs(), Request(hdr=False, width=1920, height=1080, fps=60), target="DP-1")
    assert plan.apply == ["output.DP-1.hdr.disable", "output.DP-1.wcg.disable"]
    assert plan.undo == ["output.DP-1.hdr.enable", "output.DP-1.wcg.enable"]


def test_an_hdr_device_keeps_hdr():
    plan = plan_session(outputs(), Request(hdr=True), target="DP-1")
    assert plan.apply == [] and plan.undo == []


def test_an_sdr_screen_is_left_alone_and_malformed_outputs_are_ignored():
    assert [output.name for output in outputs()] == ["DP-1", "DP-2"]
    assert plan_session(outputs(), Request(), target="DP-2").apply == []


def test_automatic_screen_choice_switches_every_hdr_screen():
    assert plan_session(outputs(), Request(), target=None).apply == ["output.DP-1.hdr.disable", "output.DP-1.wcg.disable"]


def test_the_option_can_be_turned_off():
    assert plan_session(outputs(), Request(), target="DP-1", sdr_for_sdr_clients=False).apply == []


def test_resolution_matching_uses_an_exact_mode_and_restores_the_old_one():
    plan = plan_session(outputs(), Request(hdr=False, width=1920, height=1080, fps=60), target="DP-1", resolution="client")
    assert "output.DP-1.mode.95" in plan.apply  # 60 Hz, not the 120 Hz mode
    assert plan.undo[0] == "output.DP-1.mode.89"  # the mode comes back before HDR
    high = plan_session(outputs(), Request(hdr=True, width=1920, height=1080, fps=120), target="DP-1", resolution="client")
    assert high.apply == ["output.DP-1.mode.93"]


def test_a_fixed_sharing_resolution_serves_every_device():
    """Measured: a 1080p TV at 10 Mbps H.264 went from text SSIM 0.89 to 0.99."""
    plan = plan_session(outputs(), Request(hdr=True, width=2610, height=1220, fps=60), target="DP-1", resolution="1920x1080")
    assert plan.apply == ["output.DP-1.mode.95"] and plan.undo == ["output.DP-1.mode.89"]
    assert plan_session(outputs(), Request(hdr=True), target="DP-1", resolution="1920x1080;rm").apply == []
    assert plan_session(outputs(), Request(hdr=True), target=None, resolution="1920x1080").apply == []  # needs a chosen screen


def test_no_exact_mode_means_no_change():
    plan = plan_session(outputs(), Request(hdr=True, width=1366, height=768, fps=60), target="DP-1", resolution="client")
    assert plan.apply == []
    assert any("Sunshine scales" in note for note in plan.notes)


def test_the_client_request_is_read_from_sunshines_environment():
    request = Request.from_environment({"SUNSHINE_CLIENT_HDR": "true", "SUNSHINE_CLIENT_WIDTH": "2560", "SUNSHINE_CLIENT_HEIGHT": "1440", "SUNSHINE_CLIENT_FPS": "x"})
    assert (request.hdr, request.width, request.height, request.fps) == (True, 2560, 1440, 0)
    assert Request.from_environment({}).hdr is False


class Kscreen:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.fail = False

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[1:] == ["-j"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps(KSCREEN), "")
        return subprocess.CompletedProcess(argv, 1 if self.fail else 0, "", "boom" if self.fail else "")


def test_prepare_then_restore_puts_the_screen_back(tmp_path):
    runner = Kscreen()
    display = StreamDisplay(runner=runner, state=tmp_path / "state.json", tool="kscreen-doctor")
    display.prepare(Request(hdr=False), target="DP-1", sdr_for_sdr_clients=True)
    assert runner.calls[-1] == ["kscreen-doctor", "output.DP-1.hdr.disable", "output.DP-1.wcg.disable"]
    assert stat.S_IMODE((tmp_path / "state.json").stat().st_mode) == 0o600
    assert display.restore() is True
    assert runner.calls[-1] == ["kscreen-doctor", "output.DP-1.hdr.enable", "output.DP-1.wcg.enable"]
    assert not (tmp_path / "state.json").exists()
    assert display.restore() is False  # nothing left to undo


def test_a_session_that_was_never_undone_is_restored_before_the_next(tmp_path):
    runner = Kscreen()
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"undo": ["output.DP-1.hdr.enable"]}))
    StreamDisplay(runner=runner, state=state, tool="kscreen-doctor").prepare(Request(hdr=True), target="DP-1", sdr_for_sdr_clients=True)
    assert ["kscreen-doctor", "output.DP-1.hdr.enable"] in runner.calls


def test_a_tampered_state_file_runs_nothing(tmp_path):
    runner = Kscreen()
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"undo": ["--help", "output.DP-1;rm.x.y", "output.DP-1.hdr.enable"]}))
    StreamDisplay(runner=runner, state=state, tool="kscreen-doctor").restore()
    assert runner.calls == [["kscreen-doctor", "output.DP-1.hdr.enable"]]


def test_without_kscreen_doctor_nothing_happens(tmp_path):
    display = StreamDisplay(runner=Kscreen(), state=tmp_path / "s.json", tool="")
    assert display.prepare(Request(), target="DP-1", sdr_for_sdr_clients=True, resolution="client").apply == []


def test_prep_command_runs_this_module_with_validated_arguments():
    fixed = prep_command(target="DP-1", sdr_for_sdr_clients=True, resolution="1920x1080")
    assert str(fixed["do"]).endswith("--sdr --resolution 1920x1080")
    assert "--resolution" not in str(prep_command(target="DP-1", sdr_for_sdr_clients=True, resolution="$(reboot)")["do"])
    entry = prep_command(target="DP-1", sdr_for_sdr_clients=True)
    assert entry is not None and MARKER in str(entry["do"])
    assert str(entry["do"]).endswith("prepare --output DP-1 --sdr")
    assert str(entry["undo"]).endswith("restore") and entry["elevated"] is False
    assert prep_command(target="DP-1; reboot", sdr_for_sdr_clients=True) is None
    recorder = prep_command(target=None, sdr_for_sdr_clients=False)
    assert recorder is not None and str(recorder["do"]).endswith("prepare ")  # only records the request


def test_the_persons_own_prep_commands_are_kept():
    theirs = json.dumps([{"do": "notify-send start", "undo": "notify-send stop"}])
    ours = prep_command(target="DP-1", sdr_for_sdr_clients=True)
    merged = json.loads(merge_prep_commands(theirs, ours) or "[]")
    assert merged[0]["do"] == "notify-send start" and MARKER in merged[1]["do"]
    again = json.loads(merge_prep_commands(json.dumps(merged), ours) or "[]")
    assert len(again) == 2  # replaced, not duplicated
    assert json.loads(merge_prep_commands(json.dumps(merged), None) or "[]") == [{"do": "notify-send start", "undo": "notify-send stop"}]
    assert merge_prep_commands(None, None) is None
    assert merge_prep_commands("not json", ours) == "not json"  # never overwrite what cannot be read


def test_sunshine_configuration_gets_the_prep_command_and_keeps_other_options(tmp_path):
    (tmp_path / "sunshine.conf").write_text('output_name = DP-1\nglobal_prep_cmd = [{"do":"echo hi","undo":""}]\nsunshine_name = pc\n')
    host = SunshineHost(tmp_path)
    assert host.configure({"brp_stream_display": prep_command(target="DP-1", sdr_for_sdr_clients=True)})
    text = (tmp_path / "sunshine.conf").read_text()
    assert "brp_stream_display" not in text and "sunshine_name = pc" in text
    value = json.loads(next(line.split("=", 1)[1] for line in text.splitlines() if line.startswith("global_prep_cmd")))
    assert [item["do"] for item in value][0] == "echo hi" and MARKER in value[1]["do"]
    assert host.configure({"brp_stream_display": None})
    assert "stream_display.py" not in (tmp_path / "sunshine.conf").read_text()


def test_the_encoder_sunshine_opened_is_read_from_its_log():
    lines = """[x]: Info: Sunshine version: 2026.914
[x]: Info: Screencasting with KMS
[x]: Info: New streaming session started [active sessions: 1]
[x]: Info: CLIENT CONNECTED
[x]: Info: Creating encoder [hevc_vulkan]
[x]: Info: Color coding: SDR (Rec. 709)
[x]: Info: Color depth: 8-bit
[x]: Info: Color range: MPEG
[x]: Info: Streaming bitrate is 14988000""".splitlines()
    video = read_video(lines)
    assert video.summary() == "HEVC (VULKAN) · SDR (Rec. 709) · 8-bit · limited range · 15.0 Mbps · capture: KMS"
    assert not video.hdr
    assert read_video(lines + ["[x]: Info: Sunshine version: 2026.914"]).summary() == ""


# ── Share → Image and capture ────────────────────────────────────────────


def test_share_offers_the_color_fix_on_by_default_and_matching_off(ui):
    host = ui.host_view
    assert host.hdr_sdr_row.get_active() and host._share_resolution() == ""
    assert not host.share_resolution_row.get_sensitive()  # Automatic screen: nothing to change yet
    config = host._build_sunshine_config()
    assert config["brp_stream_display"] is not None and "--sdr" in config["brp_stream_display"]["do"]
    host.hdr_sdr_row.set_active(False)
    assert "--sdr" not in host._build_sunshine_config()["brp_stream_display"]["do"]


def test_share_says_when_the_shared_screen_is_in_hdr(ui):
    host = ui.host_view
    host._hdr_outputs = {"DP-1"}
    host._sync_quality_controls()
    assert "shared in SDR" in host.quality_summary_row.get_subtitle()
    host.hdr_sdr_row.set_active(False)
    host._sync_quality_controls()
    assert "washed out" in host.quality_summary_row.get_subtitle()
    host._hdr_outputs = set()
    host._sync_quality_controls()
    assert "HDR" not in host.quality_summary_row.get_subtitle()


def test_the_prep_command_never_fails_so_sunshine_still_streams(monkeypatch):
    def broken(*_args, **_kwargs):
        raise OSError("read-only runtime directory")

    monkeypatch.setattr(StreamDisplay, "prepare", broken)
    assert main(["prepare", "--output", "DP-1", "--sdr"]) == 0


def test_no_prep_command_inside_flatpak(monkeypatch):
    import big_remote_play.host.stream_display as module

    real_exists = module.Path.exists
    monkeypatch.setattr(module.Path, "exists", lambda self: str(self) == "/.flatpak-info" or real_exists(self))
    assert prep_command(target="DP-1", sdr_for_sdr_clients=True) is None


# ── what the device asked for, and what to tell the person ───────────────


def test_prepare_records_the_request_and_the_screen_during_the_session(tmp_path):
    from big_remote_play.host.stream_display import read_client_report

    display = StreamDisplay(runner=Kscreen(), state=tmp_path / "state.json", tool="kscreen-doctor")
    report_path = tmp_path / "client.json"
    display.prepare(Request(hdr=False, width=2400, height=1080, fps=60), target="DP-1", sdr_for_sdr_clients=True, report=report_path)
    report = read_client_report(report_path)
    assert (report.width, report.height, report.fps, report.hdr) == (2400, 1080, 60, False)
    assert (report.screen, report.screen_width, report.screen_height, report.screen_hdr) == ("DP-1", 3440, 1440, False)
    assert report.scaled and not report.hdr_sent_as_sdr
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600


def test_without_the_color_fix_the_report_says_hdr_is_sent_as_sdr(tmp_path):
    from big_remote_play.host.stream_display import read_client_report

    display = StreamDisplay(runner=Kscreen(), state=tmp_path / "state.json", tool="kscreen-doctor")
    display.prepare(Request(width=1920, height=1080, fps=60), target="DP-1", sdr_for_sdr_clients=False, resolution="client", report=tmp_path / "c.json")
    report = read_client_report(tmp_path / "c.json")
    assert report.hdr_sent_as_sdr
    assert not report.scaled  # the screen used the device's 1920x1080 mode
    assert read_client_report(tmp_path / "missing.json") is None


def test_connection_cards_show_fixable_problems_in_words():
    from big_remote_play.ui.connection_cards import warning_words

    assert "1920x1080" in warning_words("scaled", "1920x1080|3440x1440") and "3440x1440" in warning_words("scaled", "1920x1080|3440x1440")
    assert "HDR" in warning_words("hdr_as_sdr", "DP-1")
    assert warning_words("unknown", "") == ""


class Monitor:
    def __init__(self, width, height, scale, fractional=True):
        self._area = type("Area", (), {"width": width, "height": height})()
        self._scale = scale
        if fractional:
            self.get_scale = lambda: scale

    def get_geometry(self):
        return self._area

    def get_scale_factor(self):
        import math

        return math.ceil(self._scale)


def test_physical_resolution_under_kde_fractional_scaling():
    """Measured in KWin: a 2560x1440 panel at 125/150/175 % reports 2048x1152,
    1707x960 and 1463x823 logical pixels with an integer factor of 2."""
    from big_remote_play.ui.components import physical_size

    for width, height, scale in ((2048, 1152, 1.25), (1707, 960, 1.5), (1463, 823, 1.7498291182501708), (1280, 720, 2.0), (2560, 1440, 1.0)):
        assert physical_size(Monitor(width, height, scale)) == (2560, 1440), scale
    assert physical_size(Monitor(1280, 720, 2.0, fractional=False)) == (2560, 1440)


def test_the_sharing_resolution_is_saved_and_older_settings_are_migrated(ui):
    host = ui.host_view
    host.monitor_row.set_selected(1)
    host.share_resolution_row.set_selected(2)
    config = host._build_sunshine_config()
    if config["output_name"]:
        assert "--resolution 1920x1080" in config["brp_stream_display"]["do"]
    host.save_host_settings()
    assert host.config.get("host")["share_resolution"] == "1920x1080"
    settings = dict(host.config.get("host"))
    settings.pop("share_resolution")
    settings["match_client_resolution"] = True
    host.config.set("host", settings)
    host.load_settings()
    assert host._share_resolution() == "client"


def test_pairing_while_someone_plays_explains_the_moonlight_rule(ui):
    from big_remote_play.utils.connection_health import ConnectionInfo

    host = ui.host_view
    assert host._none_waiting_message("plain") == "plain"
    host.perf_monitor.connections = [ConnectionInfo("TV", "192.0.2.5")]
    assert "currently in a game" in host._none_waiting_message("plain")
    host.perf_monitor.connections = []
