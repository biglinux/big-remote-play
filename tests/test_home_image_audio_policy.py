"""Behavioral checks for direct tasks, one host image owner and opt-in routing.

GTK is real; no router, sound-device, service or network mutations are made.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from test_ui_task_flows import ui as _ui_fixture, drain
from big_remote_play.utils.audio import AudioManager
from big_remote_play.utils.network import NetworkDiscovery
from big_remote_play.ui import connection_guides

ui = _ui_fixture
DEVICES = [{"name": "alsa.analog", "description": "Built-in Audio"}, {"name": "alsa.usb", "description": "USB Headphones"}]


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from walk(child)
        child = child.get_next_sibling()


def text(widget):
    return "\n".join(w.get_text() for w in walk(widget) if isinstance(w, Gtk.Label))


def outputs(host, monkeypatch):
    monkeypatch.setattr(host.audio_manager, "get_passive_sinks", lambda: list(DEVICES))
    monkeypatch.setattr(host.audio_manager, "get_default_sink", lambda: "alsa.usb")
    host.load_audio_outputs()


def run_start(host, monkeypatch):
    cfg = host._collect_hosting_config()
    configured = []
    with monkeypatch.context() as mp:
        mp.setattr(NetworkDiscovery, "start_pin_listener", lambda *a: lambda: None)
        mp.setattr(host.sunshine, "ensure_desktop_app", lambda: True)
        mp.setattr(host.sunshine, "is_running", lambda: False)
        mp.setattr(host.sunshine, "configure", lambda values: configured.append(dict(values)) or True)
        mp.setattr(host.sunshine, "start", lambda: (True, "test service started"))
        mp.setattr(GLib, "idle_add", lambda *a, **kw: 0)
        host._run_start_hosting(cfg)
    return cfg, configured


def test_home_has_one_vpn_action_before_the_two_direct_roles(ui):
    assert isinstance(ui.home_network_action, Gtk.ListBox)
    row = ui.home_network_action.get_first_child()
    assert row.get_title() == "Set up a virtual private network"
    assert ui.home_network_action.get_next_sibling() is ui.welcome_cards_box
    labels = text(ui.home_navigation)
    assert "PLAY TOGETHER" in labels
    assert "same network" in labels and "virtual private network (VPN)" in labels
    assert ui.home_navigation.find_page("guide") is None


@pytest.mark.parametrize("role", ["host", "guest"])
def test_home_card_does_not_start_a_service_or_install(ui, monkeypatch, role):
    share, connect = Mock(), Mock()
    monkeypatch.setattr(ui.host_view, "start_hosting", share)
    monkeypatch.setattr(ui.guest_view, "connect_to_host", connect)
    getattr(ui, f"{role}_card").emit("clicked")
    drain()
    assert ui.current_page == role
    assert ui.get_visible_dialog() is None
    share.assert_not_called()
    connect.assert_not_called()


def test_host_has_one_image_dialog_with_limits_and_capture(ui):
    h = ui.host_view
    h.quality_summary_row.emit("activated")
    drain()
    assert ui.get_visible_dialog() is h.quality_sheet
    for row in (h.bandwidth_row, h.auto_quality_row, h.monitor_row, h.gpu_row, h.platform_row, h.optimization_row, h.codecs_row, h.wifi_row):
        assert row.is_ancestor(h.quality_sheet)
    assert len(h.settings_dialogs) == 3
    assert "higher request" in text(h.quality_sheet) or "limits a higher" in text(h.quality_sheet)


def test_automatic_mode_preserves_explicit_video_ceiling_and_screen(ui):
    h = ui.host_view
    h.available_monitors = [("Automatic", "auto"), ("Second screen", "output2")]
    h.monitor_row.set_model(Gtk.StringList.new(["Automatic", "Second screen"]))
    h.monitor_row.set_selected(1)
    h.bandwidth_row.set_value(25)
    h.auto_quality_row.set_active(True)
    h._apply_auto_quality(force=True)
    assert h.bandwidth_row.get_value() == 25
    assert h.bandwidth_row.get_sensitive()
    assert h.monitor_row.get_selected() == 1
    assert h._build_sunshine_config()["max_bitrate"] == 25000
    assert h._build_sunshine_config()["output_name"] == "output2"
    assert "Second screen" in h.auto_status_row.get_subtitle()
    assert "25" in h.quality_summary_row.get_subtitle()


def test_automatic_summary_describes_configured_not_measured_values(ui):
    h = ui.host_view
    h.auto_quality_row.set_active(True)
    h._apply_auto_quality(force=True)
    assert h.auto_status_row.get_title() == "Configured for the next sharing session"
    summary = h.auto_status_row.get_subtitle()
    assert "Graphics card: Automatic" in summary
    assert "Capture: Automatic" in summary
    assert "Encoding priority: Balanced" in summary
    assert "Error correction:" in summary
    cfg = h._build_sunshine_config()
    assert "fps" not in cfg and "resolution" not in cfg
    assert cfg["encoder"] == "" and cfg["capture"] is None


@pytest.mark.parametrize("index,nvenc,sw", [(0, "1", "ultrafast"), (1, "4", "veryfast"), (2, "7", "medium")])
def test_immediate_start_and_saved_image_settings_have_the_same_mapping(ui, index, nvenc, sw):
    h = ui.host_view
    h.auto_quality_row.set_active(False)
    h.optimization_row.set_selected(index)
    h.codecs_row.set_active(False)
    h.wifi_row.set_active(True)
    cfg = h._build_sunshine_config()
    assert cfg["nvenc_preset"] == nvenc and cfg["sw_preset"] == sw
    assert cfg["hevc_mode"] == cfg["av1_mode"] == "1"
    assert cfg["fec_percentage"] == "30"
    h.save_host_settings()
    from big_remote_play.ui.sunshine_preferences import SunshineConfigManager

    saved = SunshineConfigManager()
    for key, value in h._encoding_settings().items():
        assert saved.get(key) == value


def test_portal_capture_is_a_real_backend_and_limit_zero_follows_client(ui):
    h = ui.host_view
    h.auto_quality_row.set_active(False)
    h.platform_row.set_selected(h._capture_values.index("portal"))
    h.bandwidth_row.set_value(0)
    cfg = h._build_sunshine_config()
    assert cfg["capture"] == "portal" and cfg["max_bitrate"] == 0


def test_default_audio_does_not_infer_consent_from_a_legacy_device_index(ui, monkeypatch):
    h = ui.host_view
    settings = dict(h.config.get("host", {}))
    settings.update(audio_output_idx=1, audio_mode=1)
    settings.pop("audio_output_name", None)
    h.config.set("host", settings)
    outputs(h, monkeypatch)
    assert h._selected_audio_sink() == ""
    assert h.audio_output_row.get_selected() == 0
    assert not h.audio_mode_row.get_sensitive()
    assert not h.audio_mode_row.get_visible()
    assert h.audio_output_row.get_use_subtitle()
    assert not h.audio_mixer_link.get_sensitive()


def test_default_sharing_makes_no_audio_mutations(ui, monkeypatch):
    h = ui.host_view
    outputs(h, monkeypatch)
    enable, default, move, disable = Mock(), Mock(), Mock(), Mock()
    monkeypatch.setattr(h.audio_manager, "enable_streaming_audio", enable)
    monkeypatch.setattr(h.audio_manager, "set_default_sink", default)
    monkeypatch.setattr(h.audio_manager, "move_app", move)
    monkeypatch.setattr(h.audio_manager, "disable_streaming_audio", disable)
    cfg, configured = run_start(h, monkeypatch)
    assert cfg["audio_output_name"] == ""
    assert len(configured) == 1
    assert configured[0]["audio_sink"] is None
    assert configured[0]["virtual_sink"] is None
    assert configured[0]["stream_audio"] == "enabled"
    assert not h._audio_routing_active
    h._rollback_start()
    for operation in (enable, default, move, disable):
        operation.assert_not_called()


def test_explicit_device_is_saved_by_name_and_survives_reordering(ui, monkeypatch):
    h = ui.host_view
    outputs(h, monkeypatch)
    h.audio_output_row.set_selected(2)
    h.save_host_settings()
    assert h.config.get("host")["audio_output_name"] == "alsa.usb"
    monkeypatch.setattr(h.audio_manager, "get_passive_sinks", lambda: list(reversed(DEVICES)))
    h.load_audio_outputs()
    assert h.audio_output_row.get_selected() == 1
    assert h._selected_audio_sink() == "alsa.usb"
    assert h.audio_mode_row.get_sensitive()
    assert h.audio_mode_row.get_visible()
    assert h.audio_mode_row.get_use_subtitle()


def test_missing_selected_device_fails_without_redirecting_to_first_device(ui, monkeypatch):
    h = ui.host_view
    settings = dict(h.config.get("host", {}), audio_output_name="missing.usb")
    h.config.set("host", settings)
    outputs(h, monkeypatch)
    enable = Mock()
    monkeypatch.setattr(h.audio_manager, "enable_streaming_audio", enable)
    assert "Unavailable" in h.audio_output_row.get_selected_item().get_string()
    cfg, configured = run_start(h, monkeypatch)
    assert cfg["audio_output_name"] == "missing.usb"
    assert not configured
    enable.assert_not_called()


def test_manual_audio_routing_only_runs_after_explicit_device_selection(ui, monkeypatch):
    h = ui.host_view
    outputs(h, monkeypatch)
    h.audio_output_row.set_selected(2)
    enable = Mock(return_value=True)
    monkeypatch.setattr(h.audio_manager, "enable_streaming_audio", enable)
    cfg, configured = run_start(h, monkeypatch)
    enable.assert_called_once_with("alsa.usb", guest_only=False)
    assert cfg["audio_output_name"] == "alsa.usb"
    assert configured[0]["audio_sink"] == "SunshineGameSink"
    assert h._audio_routing_active


def test_selecting_a_device_while_sharing_only_schedules_next_session(ui, monkeypatch):
    h = ui.host_view
    outputs(h, monkeypatch)
    enable = Mock()
    monkeypatch.setattr(h.audio_manager, "enable_streaming_audio", enable)
    h.is_hosting = True
    h.audio_output_row.set_selected(2)
    h.audio_mode_row.set_selected(3)
    enable.assert_not_called()
    h.is_hosting = False


def test_stopping_default_sharing_does_not_claim_audio_ownership(ui, monkeypatch):
    h = ui.host_view
    disable = Mock()
    monkeypatch.setattr(h.audio_manager, "disable_streaming_audio", disable)
    monkeypatch.setattr(h.sunshine, "stop", lambda: None)
    with monkeypatch.context() as mp:
        mp.setattr(GLib, "idle_add", lambda *a, **kw: 0)
        h._run_stop_hosting("")
    disable.assert_not_called()


def test_new_client_keeps_host_playback_but_preserves_existing_user_choice(ui):
    g = ui.guest_view
    assert g.audio_row.get_active()
    g.audio_row.set_active(False)
    g.save_guest_settings()
    g.load_guest_settings()
    assert not g.audio_row.get_active()
    assert g.moonlight_config.get("hostaudio") == "false"


def test_upnp_is_an_explicit_attempt_not_a_connectivity_claim(ui):
    h = ui.host_view
    assert not h.upnp_row.get_active()
    assert h._build_sunshine_config()["upnp"] == "disabled"
    assert "does not confirm" in h.upnp_row.get_subtitle()
    h.upnp_row.set_active(True)
    assert h._build_sunshine_config()["upnp"] == "enabled"
    assert not h.webui_anyone_row.get_active()
    assert "administration port" in h.webui_anyone_row.get_subtitle()
    assert h._build_sunshine_config()["origin_web_ui_allowed"] == "lan"


def test_direct_and_headscale_guides_are_distinct_and_read_only(ui, monkeypatch):
    open_link = Mock()
    monkeypatch.setattr(connection_guides, "open_uri", open_link)
    direct = connection_guides.build_direct_internet_dialog()
    direct.present(ui)
    drain()
    content = text(direct)
    assert "DNS only (gray cloud)" in content
    assert "DigitalPlat" in content and "47990" in content
    assert "CGNAT" in content
    direct.close()
    drain()
    vpn = connection_guides.build_headscale_hosting_dialog()
    vpn.present(ui)
    drain()
    content = text(vpn)
    assert "This still creates a VPN" in content
    assert "not directly to the game stream" in content
    open_link.assert_not_called()
    vpn.close()
    drain()


def test_provider_page_keeps_non_vpn_help_optional(ui):
    ui.navigate_to("vpn_selector")
    rows = [w for w in walk(ui.content_stack.get_visible_child()) if isinstance(w, Adw.ExpanderRow)]
    alternatives = [r for r in rows if r.get_title() == "Without a VPN (advanced)"]
    assert len(alternatives) == 1
    assert not alternatives[0].get_expanded()


def test_audio_cleanup_does_not_move_other_virtual_outputs(monkeypatch):
    manager = AudioManager()
    moved = Mock()
    monkeypatch.setattr(manager, "get_default_sink", lambda: "easyeffects_sink")
    monkeypatch.setattr(manager, "set_default_sink", moved)
    monkeypatch.setattr(manager, "get_passive_sinks", lambda: DEVICES)
    manager.cleanup()
    moved.assert_not_called()


def test_audio_restore_preserves_newer_user_output_and_moves_only_owned_streams(monkeypatch):
    manager = AudioManager()
    manager._user_default_sink = "alsa.analog"
    set_default, moved = Mock(), Mock()
    monkeypatch.setattr(manager, "get_default_sink", lambda: "easyeffects_sink")
    monkeypatch.setattr(manager, "set_default_sink", set_default)
    monkeypatch.setattr(manager, "move_app", moved)
    monkeypatch.setattr(manager, "get_apps", lambda: [{"id": "1", "sink_name": "SunshineGameSink"}, {"id": "2", "sink_name": "easyeffects_sink"}])
    monkeypatch.setattr("big_remote_play.utils.audio.subprocess.run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=""))
    manager.disable_streaming_audio(None)
    set_default.assert_not_called()
    moved.assert_called_once_with("1", "easyeffects_sink")
    assert manager._user_default_sink is None


def test_audio_sink_parser_uses_english_fields_and_rejects_unnamed_devices(monkeypatch):
    command = Mock(return_value=SimpleNamespace(returncode=0, stdout="Sink #2\n Name: alsa.usb\n Description: USB\nSink #3\n"))
    monkeypatch.setattr("big_remote_play.utils.audio.subprocess.run", command)
    assert AudioManager().get_passive_sinks() == [{"id": "2", "name": "alsa.usb", "description": "USB"}]
    assert command.call_args.kwargs["env"]["LC_ALL"] == "C"


def test_guide_links_pass_the_requesting_widget_for_wayland_activation(monkeypatch):
    opened = Mock()
    monkeypatch.setattr(connection_guides, "open_uri", opened)
    row = connection_guides._link("Cloudflare", connection_guides.CLOUDFLARE_DNS_DOCS)
    opened.assert_not_called()
    row.emit("activated")
    opened.assert_called_once_with(row, connection_guides.CLOUDFLARE_DNS_DOCS)
