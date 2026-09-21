"""User-visible transitions, not just presence of strings in the source."""

import pytest
import time
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib
from test_ui_task_flows import ui as _ui_fixture, drain

ui = _ui_fixture


def test_code_and_address_are_separate_tasks(ui):
    g = ui.guest_view
    g.present_other_ways("pin")
    drain()
    code = ui.get_visible_dialog()
    assert g.pin_entry.is_ancestor(code)
    assert not g.manual_ip_entry.is_ancestor(code)
    g.pin_entry.set_text("1234")
    code.close()
    drain()
    g.present_other_ways("ip")
    drain()
    address = ui.get_visible_dialog()
    assert address is not code
    assert g.manual_ip_entry.is_ancestor(address)
    assert not g.pin_entry.is_ancestor(address)
    assert g.pin_entry.get_text() == "1234"


@pytest.mark.parametrize("preset", [0, 1, 2, 3, 4])
def test_custom_is_selectable_from_every_preset_without_changing_values(ui, preset):
    g = ui.guest_view
    g.profile_row.set_selected(preset)
    before = {key: g.moonlight_config.get(key) for key in ("width", "height", "fps", "bitrate")}
    g.profile_row.set_selected(g._CUSTOM_PROFILE)
    assert g.profile_row.get_selected() == g._CUSTOM_PROFILE
    assert not g._is_automatic()
    assert {key: g.moonlight_config.get(key) for key in before} == before
    g._sync_quality_summary()
    assert g.profile_row.get_selected() == g._CUSTOM_PROFILE
    g.load_guest_settings()
    assert g.profile_row.get_selected() == g._CUSTOM_PROFILE


def test_presets_do_not_show_a_second_resolution_list(ui):
    g = ui.guest_view
    g.profile_row.set_selected(1)
    g.image_dialog.present(g)
    drain()
    assert not g.resolution_row.get_mapped()
    g.profile_row.set_selected(g._CUSTOM_PROFILE)
    drain()
    deadline = time.monotonic() + 1.0
    while not g.resolution_row.get_mapped() and time.monotonic() < deadline:
        GLib.MainContext.default().iteration(False)
        time.sleep(0.01)
    assert g.resolution_row.get_mapped()
    assert isinstance(g.resolution_row, Gtk.ListBoxRow)  # compact native ComboRow


@pytest.mark.parametrize("role", ["host", "guest"])
def test_home_click_opens_the_task_directly(ui, role):
    card = getattr(ui, f"{role}_card")
    card.emit("clicked")
    drain()
    assert ui.current_page == role
    assert ui.content_stack.get_visible_child_name() == role
    assert not card.get_mapped()
    assert not ui.host_view.is_hosting
    ui.nav_list.select_row(ui.nav_list.get_row_at_index(0))
    drain()
    assert ui.current_page == "welcome"
    assert ui.home_navigation.get_visible_page_tag() == "choices"


def test_all_preset_transitions_remain_selected(ui):
    g = ui.guest_view
    for previous in range(6):
        for requested in range(6):
            g.profile_row.set_selected(previous)
            g.profile_row.set_selected(requested)
            g._sync_quality_summary()
            assert g.profile_row.get_selected() == requested, (previous, requested)
            assert g.custom_picture_group.get_visible() == (requested == g._CUSTOM_PROFILE)


def test_custom_matching_a_preset_is_not_reclassified(ui):
    g = ui.guest_view
    g.profile_row.set_selected(1)
    g.profile_row.set_selected(5)
    g.bitrate_scale.set_value(19)
    g.bitrate_scale.set_value(20)
    g.load_guest_settings()
    assert g.profile_row.get_selected() == 5
    assert g.moonlight_config.get("bitrate") == "20000"


def test_named_profile_reconciles_an_external_edit_on_reload(ui):
    g = ui.guest_view
    g.profile_row.set_selected(1)
    g.moonlight_config.set("bitrate", 13500)
    g.load_guest_settings()
    assert g.profile_row.get_selected() == 5
    assert g.bitrate_scale.get_value() == 13.5


def test_enter_dispatches_only_the_corresponding_connection_method(ui, monkeypatch):
    g = ui.guest_view
    calls = []
    monkeypatch.setattr(g, "connect_manual", lambda *args: calls.append(("address", args)))
    monkeypatch.setattr(g, "connect_pin", lambda value: calls.append(("code", value)))
    g.present_other_ways("pin")
    drain()
    g.pin_entry.set_text("2345")
    g.pin_entry.emit("entry-activated")
    assert calls == [("code", "2345")]
    g.close_other_ways()
    g.present_other_ways("ip")
    g.manual_ip_entry.set_text("192.0.2.7")
    g.manual_ip_entry.emit("entry-activated")
    assert len(calls) == 2 and calls[1][0] == "address"
    assert calls[1][1][0] == "192.0.2.7"


def test_sidebar_home_restores_focus_to_the_chosen_card(ui):
    ui.host_card.emit("clicked")
    drain()
    assert ui.current_page == "host"
    ui.navigate_to("welcome")
    drain()
    assert not ui.home_back_button.get_visible()
    focus = ui.get_focus()
    assert focus is ui.host_card or (focus is not None and focus.is_ancestor(ui.host_card))


def test_sidebar_home_has_a_single_choice_page(ui):
    ui.guest_card.emit("clicked")
    drain()
    assert ui.current_page == "guest"
    ui.nav_list.select_row(ui.nav_list.get_row_at_index(0))
    drain()
    assert ui.current_page == "welcome"
    assert ui.home_navigation.get_visible_page_tag() == "choices"
    assert ui.home_navigation.find_page("guide") is None


def test_bitrate_unlock_updates_the_actual_range_and_preserves_reload(ui):
    g = ui.guest_view
    g.profile_row.set_selected(5)
    g.unlock_bitrate_row.set_active(True)
    assert g.bitrate_scale.get_adjustment().get_upper() == 500
    g.bitrate_scale.set_value(250)
    g.load_guest_settings()
    assert g.bitrate_scale.get_value() == 250
    assert g.unlock_bitrate_row.get_active()
    g.unlock_bitrate_row.set_active(False)
    assert g.bitrate_scale.get_adjustment().get_upper() == 150
    assert g.bitrate_scale.get_value() == 150
    assert g.moonlight_config.get("bitrate") == "150000"


def test_custom_dimensions_can_be_edited_again_without_switching_preset(ui, monkeypatch):
    g = ui.guest_view
    g.profile_row.set_selected(1)
    g.profile_row.set_selected(5)
    g.scale_row.set_active(False)
    monkeypatch.setattr(g, "show_custom_input_dialog", lambda title, subtitle, callback, **kw: callback("1600x900"))
    g.resolution_row.set_selected(4)
    assert g.custom_resolution_val == "1600x900"
    assert g.custom_resolution_edit.get_visible()
    assert g.resolution_row.get_subtitle() == "1600x900"
    monkeypatch.setattr(g, "show_custom_input_dialog", lambda title, subtitle, callback, **kw: callback("1920x1200"))
    g.custom_resolution_edit.emit("clicked")
    assert g.custom_resolution_val == "1920x1200"
    assert g.moonlight_config.get("height") == "1200"
    assert g.profile_row.get_selected() == 5


def test_custom_fps_can_be_edited_again(ui, monkeypatch):
    g = ui.guest_view
    g.profile_row.set_selected(5)
    monkeypatch.setattr(g, "show_custom_input_dialog", lambda title, subtitle, callback, **kw: callback("75"))
    g.fps_row.set_selected(3)
    assert g.custom_fps_edit.get_visible()
    assert g.fps_row.get_subtitle() == "75 FPS"
    monkeypatch.setattr(g, "show_custom_input_dialog", lambda title, subtitle, callback, **kw: callback("90"))
    g.custom_fps_edit.emit("clicked")
    assert g.moonlight_config.get("fps") == "90"
    assert g.profile_row.get_selected() == 5


def test_compact_home_and_tasks_use_only_the_native_sidebar_back(ui):
    ui.split_view.set_collapsed(True)
    ui.host_card.emit("clicked")
    drain()
    assert ui.current_page == "host"
    assert not ui.home_back_button.get_visible()
    assert ui.content_headerbar.get_show_back_button()
    ui.navigate_to("welcome")
    drain()
    assert not ui.home_back_button.get_visible()
    assert ui.content_headerbar.get_show_back_button()


def test_installer_without_vte_uses_the_explicit_fallback(ui, monkeypatch):
    import big_remote_play.ui.installer_window as module

    dialog = module.InstallerWindow(parent=ui)
    calls = []
    monkeypatch.setattr(module, "Vte", None)
    monkeypatch.setattr(dialog, "start_external_installation", lambda: calls.append("external"))
    dialog.start_installation()
    assert calls == ["external"]
    assert not hasattr(dialog, "terminal")
    dialog.close()


def test_missing_connection_widgets_during_teardown_do_not_raise(ui):
    g = ui.guest_view
    original = g.manual_btn_spinner
    del g.manual_btn_spinner
    try:
        g._update_all_buttons_state()
    finally:
        g.manual_btn_spinner = original


def test_preset_does_not_keep_a_stale_custom_resolution_subtitle(ui):
    g = ui.guest_view
    g.profile_row.set_selected(5)
    g.loading_settings = True
    g.custom_resolution_val = "1600x900"
    g.resolution_row.set_selected(4)
    g.loading_settings = False
    g._sync_quality_summary()
    assert g.resolution_row.get_subtitle() == "1600x900"
    g.profile_row.set_selected(1)
    g.profile_row.set_selected(5)
    assert g.resolution_row.get_selected() == 1
    assert g.resolution_row.get_subtitle() == ""
    assert g.resolution_row.get_selected_item().get_string() == "1080p"
    assert g.moonlight_config.get("width") == "1920"
