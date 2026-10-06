"""Behavioral checks for direct tasks, one host image owner and opt-in routing.

GTK is real; no router, sound-device, service or network mutations are made.
"""

from unittest.mock import Mock

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from test_ui_task_flows import ui as _ui_fixture, drain
from big_remote_play.utils.audio import AudioManager, SUNSHINE_STEREO_SINK
from test_audio import FakePulse, HDMI, USB
from big_remote_play.utils.network import NetworkDiscovery
from big_remote_play.ui import connection_guides
from big_remote_play.ui.host_view import _monitor_choice_label

ui = _ui_fixture


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from walk(child)
        child = child.get_next_sibling()


def text(widget):
    return "\n".join(w.get_text() for w in walk(widget) if isinstance(w, Gtk.Label))


def outputs(host, monkeypatch, pulse=None):
    """Give the host a fake sound server (HDMI current, USB headset, Bluetooth, EasyEffects)."""
    pulse = pulse or FakePulse()
    host.audio_manager = AudioManager(runner=pulse)
    host.load_audio_outputs()
    return pulse


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


def test_home_is_one_hero_with_the_guided_start_then_the_two_roles(ui):
    labels = text(ui.home_navigation)
    assert "Your games. Any screen. Anywhere." in labels
    assert "Start with the guided setup" in labels
    assert labels.index("Start with the guided setup") < labels.index("Share") < labels.index("Connect")
    # Internet play is a sidebar destination and a guided question; Home does
    # not repeat it, and never names the technology.
    assert "Playing over the internet?" not in labels
    assert "virtual private network" not in labels.lower()
    assert ui.home_logo.is_ancestor(ui.welcome_main_box)
    assert ui.home_navigation.find_page("guide") is None


def test_home_hides_working_component_details_but_explains_missing_ones(ui):
    ui.update_dependency_ui(True, True, True, True, True)
    assert not ui._role_card_ui["host"]["state"].get_visible()
    assert not ui._role_card_ui["guest"]["state"].get_visible()
    # Only the two indicators; no per-service cards on Home.
    assert [service for service, row in ui._status_rows.items() if row.get_visible()] == ["summary-streaming", "summary-network"]

    ui.update_dependency_ui(False, True, True, True, True)
    assert ui._role_card_ui["host"]["state"].get_visible()
    # Words for the person, the product name for whoever needs it.
    assert "Sunshine" not in ui._role_card_ui["host"]["label"].get_text()
    assert "Sunshine" in ui.host_card.get_tooltip_text()
    assert ui.host_card.get_sensitive()


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
    for row in (
        h.bandwidth_row,
        h.auto_quality_row,
        h.monitor_row,
        h.identify_monitors_row,
        h.gpu_row,
        h.platform_row,
        h.optimization_row,
        h.codecs_row,
        h.wifi_row,
    ):
        assert row.is_ancestor(h.quality_sheet)
    assert set(h.settings_dialogs) == {"Image and capture", "Network and access"}
    assert "higher request" in text(h.quality_sheet) or "limits a higher" in text(h.quality_sheet)


def test_identical_monitor_names_receive_distinct_numbers_and_connectors():
    assert _monitor_choice_label(1, "LG UltraGear", "DP-1") == "01 · LG UltraGear · DP-1"
    assert _monitor_choice_label(2, "LG UltraGear", "DP-2") == "02 · LG UltraGear · DP-2"


def test_automatic_mode_preserves_explicit_video_ceiling_and_screen(ui):
    h = ui.host_view
    monitor_labels = [
        "Automatic",
        "Monitor 01: LG UltraGear (DP-1)",
        "Monitor 02: LG UltraGear (DP-2)",
    ]
    h.available_monitors = list(zip(monitor_labels, ("auto", "DP-1", "DP-2"), strict=True))
    h.monitor_row.set_model(Gtk.StringList.new(monitor_labels))
    h.monitor_row.set_selected(2)
    h.bandwidth_row.set_value(25)
    h.auto_quality_row.set_active(True)
    h._apply_auto_quality(force=True)
    assert h.bandwidth_row.get_value() == 25
    assert h.bandwidth_row.get_sensitive()
    assert h.monitor_row.get_selected() == 2
    assert h._build_sunshine_config()["max_bitrate"] == 25000
    assert h._build_sunshine_config()["output_name"] == "DP-2"
    assert "Monitor 02: LG UltraGear (DP-2)" in h.auto_status_row.get_subtitle()
    assert "Monitor 02: LG UltraGear (DP-2)" in h.quality_summary_row.get_subtitle()
    assert "25" in h.quality_summary_row.get_subtitle()


def test_saved_monitor_follows_connector_when_display_order_changes(ui):
    h = ui.host_view
    h.available_monitors = [
        ("Automatic", "auto"),
        ("Monitor 01: Same Model (DP-1)", "DP-1"),
        ("Monitor 02: Same Model (DP-2)", "DP-2"),
    ]
    h.monitor_row.set_model(Gtk.StringList.new([label for label, _value in h.available_monitors]))
    h.monitor_row.set_selected(2)
    h.save_host_settings()
    assert h.config.get("host")["monitor_output_name"] == "DP-2"

    h.available_monitors = [
        ("Automatic", "auto"),
        ("Monitor 01: Same Model (DP-2)", "DP-2"),
        ("Monitor 02: Same Model (DP-1)", "DP-1"),
    ]
    h.monitor_row.set_model(Gtk.StringList.new([label for label, _value in h.available_monitors]))
    h.load_settings()
    assert h.monitor_row.get_selected() == 1
    assert h._build_sunshine_config()["output_name"] == "DP-2"


def test_identify_button_numbers_every_connected_monitor(ui, monkeypatch):
    h = ui.host_view
    specs = [
        (object(), "01", "Same Model", "DP-1"),
        (object(), "02", "Same Model", "DP-2"),
        (object(), "03", "Same Model", "HDMI-A-1"),
    ]
    windows = [Mock(), Mock(), Mock()]
    created = []
    window_iter = iter(windows)
    monkeypatch.setattr(h, "_monitor_identifier_specs", lambda: specs)
    monkeypatch.setattr(
        h,
        "_create_monitor_identifier_window",
        lambda monitor, number, name, connector: created.append((number, name, connector)) or next(window_iter),
    )
    monkeypatch.setattr(GLib, "timeout_add", lambda milliseconds, callback: 73)
    removed = Mock()
    monkeypatch.setattr(GLib, "source_remove", removed)

    h.identify_monitors_button.emit("clicked")

    assert created == [
        ("01", "Same Model", "DP-1"),
        ("02", "Same Model", "DP-2"),
        ("03", "Same Model", "HDMI-A-1"),
    ]
    assert h._monitor_identifier_timeout_id == 73
    h._close_monitor_identifiers()
    removed.assert_called_once_with(73)
    for window in windows:
        window.close.assert_called_once_with()


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
    # Automatic values are absent from sunshine.conf.  An empty encoder is an
    # explicit (invalid) encoder name, not Sunshine's automatic mode.
    assert cfg["encoder"] is None and cfg["capture"] is None


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
    assert h.audio_output_row.get_selected_item().get_string() == "Automatic — use the current output"
    assert h.audio_play_here_row.get_sensitive()


def test_the_old_other_computer_only_choice_becomes_client_only(ui, monkeypatch):
    h = ui.host_view
    h.config.set("host", dict(h.config.get("host", {}), audio_mode=1))
    h.load_settings()
    assert not h.audio_play_here_row.get_active()
    h.config.set("host", {**h.config.get("host", {}), "audio_mode": 3})
    h.config.get("host").pop("audio_play_on_host", None)
    h.load_settings()
    assert h.audio_play_here_row.get_active()


def test_default_sharing_makes_no_audio_mutations(ui, monkeypatch):
    h = ui.host_view
    pulse = outputs(h, monkeypatch)
    cfg, configured = run_start(h, monkeypatch)
    assert cfg["audio_output_name"] == "" and cfg["audio_play_on_host"] is True
    assert len(configured) == 1
    assert configured[0]["audio_sink"] is None  # Sunshine follows the current output
    assert "virtual_sink" not in configured[0]  # the person's value is left alone
    assert configured[0]["stream_audio"] == "enabled"
    assert h.audio_session is not None and h.audio_session.original_sink == HDMI
    h._rollback_start()
    assert pulse.writes == []


def test_client_only_points_sunshine_at_its_own_virtual_output(ui, monkeypatch):
    h = ui.host_view
    pulse = outputs(h, monkeypatch)
    h.audio_play_here_row.set_active(False)
    _cfg, configured = run_start(h, monkeypatch)
    assert configured[0]["audio_sink"] == SUNSHINE_STEREO_SINK
    assert pulse.writes == []  # Sunshine switches the output itself and restores it
    h._rollback_start()


def test_explicit_device_is_saved_by_name_and_survives_reordering(ui, monkeypatch):
    h = ui.host_view
    pulse = outputs(h, monkeypatch)
    h.audio_output_row.set_selected(2)
    h.save_host_settings()
    assert h.config.get("host")["audio_output_name"] == USB
    assert not h.audio_play_here_row.get_sensitive()
    pulse.sinks.reverse()
    h.load_audio_outputs()
    assert h._selected_audio_sink() == USB
    assert h.audio_output_row.get_selected_item().get_string() == "Fone USB — Estéreo analógico"


def test_virtual_outputs_are_not_offered_for_explicit_choice(ui, monkeypatch):
    h = ui.host_view
    outputs(h, monkeypatch)
    labels = [h.audio_output_row.get_model().get_string(i) for i in range(h.audio_output_row.get_model().get_n_items())]
    assert labels == ["Automatic — use the current output", "HDMI / DisplayPort", "Fone USB — Estéreo analógico", "Fone Bluetooth"]


def test_missing_selected_device_fails_without_redirecting_to_first_device(ui, monkeypatch):
    h = ui.host_view
    settings = dict(h.config.get("host", {}), audio_output_name="missing.usb")
    h.config.set("host", settings)
    pulse = outputs(h, monkeypatch)
    assert "Unavailable" in h.audio_output_row.get_selected_item().get_string()
    cfg, configured = run_start(h, monkeypatch)
    assert cfg["audio_output_name"] == "missing.usb"
    assert not configured
    assert pulse.writes == []


def test_explicit_device_is_recorded_by_sunshine_without_moving_applications(ui, monkeypatch):
    h = ui.host_view
    pulse = outputs(h, monkeypatch)
    h.audio_output_row.set_selected(2)
    cfg, configured = run_start(h, monkeypatch)
    assert cfg["audio_output_name"] == USB
    assert configured[0]["audio_sink"] == USB
    assert pulse.writes == []
    h._rollback_start()


def test_selecting_a_device_while_sharing_only_schedules_next_session(ui, monkeypatch):
    h = ui.host_view
    pulse = outputs(h, monkeypatch)
    h.is_hosting = True
    h.audio_output_row.set_selected(2)
    h.audio_play_here_row.set_active(False)
    h.is_hosting = False
    assert pulse.writes == []


def test_stopping_default_sharing_does_not_claim_audio_ownership(ui, monkeypatch):
    h = ui.host_view
    pulse = outputs(h, monkeypatch)
    _cfg, _configured = run_start(h, monkeypatch)
    monkeypatch.setattr(h.sunshine, "stop", lambda: None)
    monkeypatch.setattr(h.sunshine, "is_running", lambda: False)
    with monkeypatch.context() as mp:
        mp.setattr(GLib, "idle_add", lambda *a, **kw: 0)
        h._run_stop_hosting()
    assert pulse.writes == []
    assert h.audio_session is None


def test_stopping_after_a_host_muting_client_removes_only_our_bridge(ui, monkeypatch):
    h = ui.host_view
    pulse = outputs(h, monkeypatch)
    run_start(h, monkeypatch)
    pulse.sunshine_starts_session(host_audio=False)
    status = h.audio_session.reconcile()
    assert [(b.source_sink, b.target_sink) for b in status.bridges] == [(SUNSHINE_STEREO_SINK, HDMI)]
    pulse.sunshine_ends_session(HDMI)
    monkeypatch.setattr(h.sunshine, "stop", lambda: None)
    monkeypatch.setattr(h.sunshine, "is_running", lambda: False)
    with monkeypatch.context() as mp:
        mp.setattr(GLib, "idle_add", lambda *a, **kw: 0)
        h._run_stop_hosting()
    assert pulse.bridges() == []
    assert [m[0] for m in pulse.modules] == ["7"]


def test_stopping_reconnects_an_effects_program_sunshine_left_unlinked(ui, monkeypatch):
    from big_remote_play.utils.audio import AudioRoutingSession

    h = ui.host_view
    pulse = outputs(h, monkeypatch)
    run_start(h, monkeypatch)
    pulse.sunshine_starts_session(host_audio=False)
    h.audio_session.reconcile()
    # Linked after the last check: only the look right before stopping sees it.
    pulse.effects_program_plays_into(SUNSHINE_STEREO_SINK)
    monkeypatch.setattr(AudioRoutingSession, "RELINK_SETTLE_SECONDS", 0)
    monkeypatch.setattr(h.sunshine, "stop", lambda: pulse.sunshine_exits(HDMI))
    monkeypatch.setattr(h.sunshine, "is_running", lambda: False)
    with monkeypatch.context() as mp:
        mp.setattr(GLib, "idle_add", lambda *a, **kw: 0)
        h._run_stop_hosting()
    assert ("jdsp_@PwJamesDspPlugin_JamesDsp:output_FL", f"{HDMI}:playback_FL") in pulse.links.values()
    assert ("jdsp_@PwJamesDspPlugin_JamesDsp:output_FR", f"{HDMI}:playback_FR") in pulse.links.values()


def test_audio_details_state_the_microphone_is_not_sent(ui, monkeypatch):
    h = ui.host_view
    pulse = outputs(h, monkeypatch)
    from big_remote_play.utils.audio import audio_status

    h._apply_audio_status(audio_status(h.audio_manager.snapshot()), h._audio_generation)
    rows = h.audio_detail_rows
    assert rows["mic_sent"].get_subtitle() == "No"
    assert rows["monitor"].get_subtitle() == f"{HDMI}.monitor"
    assert rows["sunshine"].get_subtitle() == "No client is receiving sound right now"
    assert h.audio_output_row.get_subtitle() == "Now: HDMI / DisplayPort"
    pulse.sunshine_starts_session(host_audio=True)
    h._apply_audio_status(audio_status(h.audio_manager.snapshot()), h._audio_generation)
    assert "sound this computer plays" in rows["sunshine"].get_subtitle()
    stale = h._audio_generation - 1
    pulse.sinks = []
    h._apply_audio_status(audio_status(h.audio_manager.snapshot()), stale)
    assert h.audio_output_row.get_subtitle() == "Now: HDMI / DisplayPort"  # stale result ignored
    h._apply_audio_status(audio_status(h.audio_manager.snapshot()), h._audio_generation)
    assert h.audio_output_row.get_subtitle().startswith("System audio unavailable")


def test_audio_test_reports_the_measured_result_in_words(ui):
    h = ui.host_view
    button = h.audio_test_button
    h._apply_audio_test(button, {"played": True, "detected": True, "level_db": -18.4, "monitor": "m", "output": "HDMI"}, h._audio_generation)
    assert h.audio_test_row.get_subtitle() == "The tone reached the shared sound (-18 dB on HDMI)."
    h._apply_audio_test(button, {"played": True, "detected": False, "level_db": -80, "monitor": "m", "output": "HDMI"}, h._audio_generation)
    assert "did not reach" in h.audio_test_row.get_subtitle()
    h._apply_audio_test(button, {"played": False, "detected": False, "level_db": None, "monitor": None, "output": ""}, h._audio_generation)
    assert h.audio_test_row.get_subtitle().startswith("System audio unavailable")
    assert button.get_sensitive()


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


def test_connect_your_devices_keeps_non_vpn_help_secondary(ui):
    ui.navigate_to("vpn_selector")
    page = ui.remote_connection_page
    rows = [w for w in walk(page.advanced_group) if isinstance(w, Adw.ActionRow) and w.get_title() == "Without a private network"]
    assert len(rows) == 1 and page.advanced_group.get_title() == "Advanced"
    # Below the three methods, never among them.
    siblings = []
    child = page.cards_box.get_parent().get_first_child()
    while child is not None:
        siblings.append(child)
        child = child.get_next_sibling()
    assert siblings.index(page.cards_box) < siblings.index(page.advanced_group)
    rows[0].emit("activated")
    drain()
    assert ui.network_navigation.get_visible_page().get_title() == "Direct connection with a domain"
    assert ui.get_visible_dialog() is None


def test_guide_links_pass_the_requesting_widget_for_wayland_activation(monkeypatch):
    opened = Mock()
    monkeypatch.setattr(connection_guides, "open_uri", opened)
    row = connection_guides._link("Cloudflare", connection_guides.CLOUDFLARE_DNS_DOCS)
    opened.assert_not_called()
    row.emit("activated")
    opened.assert_called_once_with(row, connection_guides.CLOUDFLARE_DNS_DOCS)


def test_sharing_says_in_words_whether_the_other_computer_gets_sound(ui, monkeypatch):
    """The 2026-10-02 report: Sunshine's recording restored muted, the game audible only here."""
    from big_remote_play.utils.audio import audio_status

    h = ui.host_view
    pulse = outputs(h, monkeypatch)
    run_start(h, monkeypatch)
    h.is_hosting = True
    h._render_stream_audio()
    checking = h.session_audio_row.get_subtitle()
    h._apply_audio_status(audio_status(h.audio_manager.snapshot()), h._audio_generation)
    waiting = h.audio_stream_row.get_subtitle()
    pulse.sunshine_starts_session(host_audio=True, saved_level=(True, 40632))
    h._apply_audio_status(audio_status(h.audio_manager.snapshot()), h._audio_generation)
    muted = h.audio_stream_row.get_subtitle()
    assert h.audio_detail_rows["level"].get_subtitle() != h.audio_detail_rows["sunshine"].get_subtitle()
    h._apply_audio_status(h.audio_session.reconcile(), h._audio_generation)
    sending = h.audio_stream_row.get_subtitle()
    assert len({checking, waiting, muted, sending}) == 4
    assert h.session_audio_row.get_subtitle() == sending  # Overview shows the same fact
    assert "100" in h.audio_detail_rows["level"].get_subtitle()
    assert ["set-source-output-mute", "800", "0"] in pulse.writes
    h.is_hosting = False
    h._rollback_start()


def test_a_stale_audio_status_does_not_change_the_sound_row(ui, monkeypatch):
    from big_remote_play.utils.audio import audio_status

    h = ui.host_view
    pulse = outputs(h, monkeypatch)
    h.is_hosting = True
    h._apply_audio_status(audio_status(h.audio_manager.snapshot()), h._audio_generation)
    before = h.audio_stream_row.get_subtitle()
    pulse.sunshine_starts_session(host_audio=True, saved_level=(True, 40632))
    h._apply_audio_status(audio_status(h.audio_manager.snapshot()), h._audio_generation - 1)
    assert h.audio_stream_row.get_subtitle() == before
    h.is_hosting = False
