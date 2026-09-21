"""Execute novice paths, settings persistence and accessibility in real GTK."""

from pathlib import Path
import types
from unittest.mock import Mock
import pytest
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw
from test_ui_task_flows import ui as _ui_fixture, drain

ui = _ui_fixture


def widgets(root):
    yield root
    child = root.get_first_child()
    while child is not None:
        yield from widgets(child)
        child = child.get_next_sibling()


@pytest.mark.parametrize("role,component", [("host", "Sunshine"), ("guest", "Moonlight")])
def test_missing_component_is_explained_before_installation(ui, role, component):
    ui.update_dependency_ui(False, False, False, False, False)
    ui._select_home_role(role)
    drain()
    dialog = ui.get_visible_dialog()
    assert isinstance(dialog, Adw.AlertDialog)
    assert component in dialog.get_body()
    assert not ui.host_view.is_hosting
    assert ui.current_page == "welcome"
    dialog.emit("response", "cancel")
    dialog.close()


def test_home_vpn_action_can_return_directly_to_home(ui):
    ui.home_network_action.get_first_child().emit("activated")
    assert ui.current_page == "vpn_selector"
    ui.return_from_network()
    assert ui.current_page == "welcome"
    assert ui.home_navigation.get_visible_page_tag() == "choices"


def test_network_setup_returns_to_the_task_that_opened_it(ui):
    ui._select_home_role("guest")
    ui._go_to_private_network_setup()
    assert ui.current_page == "vpn_selector"
    ui.return_from_network()
    assert ui.current_page == "guest"


def test_ultrawide_manual_resolution_is_not_silently_replaced(ui):
    guest = ui.guest_view
    guest.moonlight_config.set_many({"width": 3440, "height": 1440, "fps": 144, "windowmode": 0})
    guest.load_guest_settings()
    assert guest.resolution_row.get_selected() == 4
    assert guest.custom_resolution_val == "3440x1440"
    assert guest.custom_fps_val == "144"
    assert guest.display_mode_row.get_selected() == 1
    guest.save_guest_settings()
    assert guest.moonlight_config.get("width") == "3440"
    assert guest.moonlight_config.get("windowmode") == "0"
    assert guest.moonlight_config.get("videodec") == "0"


def test_task_specific_client_rows_write_native_values(ui):
    guest = ui.guest_view
    guest.codec_row.set_selected(3)
    guest.audio_config_row.set_selected(2)
    guest.capture_keys_row.set_selected(1)
    assert guest.moonlight_config.get("videocfg") == "4"
    assert guest.moonlight_config.get("audiocfg") == "2"
    assert guest.moonlight_config.get("capturesyskeys") == "1"
    guest.touch_trackpad_row.set_active(True)
    assert guest.moonlight_config.get("abstouchmode") == "false"
    assert guest.image_row.get_title() == "Image"
    assert guest.audio_settings_row.get_title() == "Audio"
    assert guest.input_settings_row.get_title() == "Input"
    assert guest.host_connection_row.get_title() == "Game PC and connection"
    for row in (
        guest.vsync_row,
        guest.frame_pacing_row,
        guest.hdr_row,
        guest.touch_trackpad_row,
        guest.multi_controller_row,
    ):
        assert row.get_title()
        assert any(isinstance(child, Gtk.Switch) and child.get_accessible_role() == Gtk.AccessibleRole.SWITCH for child in widgets(row))


def test_incomplete_custom_app_does_not_fall_back_to_full_desktop(ui):
    host = ui.host_view
    host.game_mode_row.set_selected(3)
    host.custom_name_entry.set_text("")
    host.custom_cmd_entry.set_text("")
    with pytest.raises(ValueError):
        host._collect_hosting_config()


def test_empty_discovery_has_text_but_no_oversized_icon(ui):
    guest = ui.guest_view
    guest.update_hosts_list([])
    children = list(widgets(guest._empty_container))
    assert any(isinstance(w, Gtk.Label) and "No game PC found yet" in w.get_text() for w in children)
    assert not any(isinstance(w, Gtk.Image) and w.get_pixel_size() > 32 for w in children)


def test_hidden_home_does_not_launch_background_network_scans(ui, monkeypatch):
    guest = ui.guest_view
    from big_remote_play.utils.network import NetworkDiscovery

    scan = Mock()
    monkeypatch.setattr(NetworkDiscovery, "discover_hosts", scan)
    ui.navigate_to("welcome")
    # Periodic discovery must be passive and skip hidden pages altogether.
    guest._auto_discover()
    scan.assert_not_called()


def test_every_local_symbolic_icon_is_registered(ui):
    from big_remote_play import paths
    from gi.repository import Gdk

    theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
    assert theme.has_icon("adw-expander-arrow-symbolic"), "An application hicolor index must not shadow native libadwaita resources"
    for path in Path(paths.ICONS_DIR).glob("*-symbolic.svg"):
        icon = theme.lookup_icon(path.stem, None, 16, 1, Gtk.TextDirection.LTR, Gtk.IconLookupFlags.FORCE_SYMBOLIC)
        assert icon is not None and icon.is_symbolic(), path.name


def test_active_share_does_not_tell_user_to_start_again(ui, monkeypatch):
    host = ui.host_view
    monkeypatch.setattr(host.perf_monitor, "start_monitoring", lambda: None)
    monkeypatch.setattr(host, "_refresh_paired_devices", lambda: None)
    host.is_hosting = True
    host.sync_ui_state()
    assert not host.start_heading.get_visible()
    assert host.guest_access_group.get_visible()
    host.is_hosting = False
    host.sync_ui_state()
    assert host.start_heading.get_visible()


def test_closed_host_cannot_start_new_server(ui, monkeypatch):
    host = ui.host_view
    host._closed = True
    start = Mock()
    monkeypatch.setattr(host.sunshine, "start", start)
    host._run_start_hosting({"sunshine_config": {}})
    start.assert_not_called()
    host._closed = False


def test_first_passive_discovery_displays_an_empty_state(ui):
    guest = ui.guest_view
    guest._empty_container.set_visible(False)
    guest.update_hosts_list([], keep_selection=True)
    assert guest._empty_container.get_visible()
    assert not guest._host_scroll.get_visible()
    assert not guest._discover_action.get_visible()


def test_hidden_header_tabs_do_not_impose_their_width_on_compact_title(ui):
    ui._set_header_context("host")
    ui._on_compact_header_apply()
    assert not ui.header_title_stack.get_hhomogeneous()
    assert ui.header_title_stack.get_visible_child_name() == "title"
    ui._set_header_title("Home")
    assert ui.header_title_stack.measure(Gtk.Orientation.HORIZONTAL, -1).minimum < 350


def test_tailscale_join_uses_browser_before_optional_auth_key(ui, monkeypatch):
    import big_remote_play.ui.private_network_view as pnv

    monkeypatch.setattr(pnv.ConnectPage, "_prefill_from_history", lambda self: None)
    ui._apply_vpn_selection("tailscale")
    rows = [w for w in widgets(ui.connect_private_view) if isinstance(w, Adw.ExpanderRow)]
    keys = [row for row in rows if row.get_subtitle() == "Auth Key"]
    assert len(keys) == 1 and not keys[0].get_expanded()


def test_first_visit_to_connect_shows_guidance_and_does_one_passive_lookup(ui, monkeypatch):
    from big_remote_play.utils.network import NetworkDiscovery
    from test_ui_task_flows import drain

    guest = ui.guest_view
    assert guest._empty_container.get_visible()
    assert not guest._host_scroll.get_visible()
    calls = []

    def discover(_self, callback, **kwargs):
        calls.append(kwargs)
        callback([])

    monkeypatch.setattr(NetworkDiscovery, "discover_hosts", discover)
    ui.navigate_to("guest")
    drain()
    assert guest.get_mapped()
    assert calls == [{"allow_scan": False}]
    assert guest._empty_container.get_visible()
    assert not guest._discover_action.get_visible()
    # Draining again must not repeat an idle callback indefinitely.
    drain()
    assert len(calls) == 1


def test_cleanup_is_idempotent_and_one_failed_adapter_does_not_skip_the_other(ui, monkeypatch):
    host_cleanup = Mock(side_effect=OSError("test cleanup failure"))
    guest_cleanup = Mock(wraps=ui.guest_view.cleanup)
    save = Mock(wraps=ui._save_window_size)
    with monkeypatch.context() as local:
        local.setattr(ui.host_view, "cleanup", host_cleanup)
        local.setattr(ui.guest_view, "cleanup", guest_cleanup)
        local.setattr(ui, "_save_window_size", save)
        ui._shutdown_resources()
        ui._shutdown_resources()
        host_cleanup.assert_called_once()
        guest_cleanup.assert_called_once()
        save.assert_called_once()
    # The fixture must still dispose the real host's timers after the mock.
    ui.host_view.cleanup()


def test_app_shutdown_returns_normally_and_calls_window_cleanup():
    import ast

    tree = ast.parse(Path("src/big_remote_play/app.py").read_text())
    shutdown = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "do_shutdown")
    calls = [node.func for node in ast.walk(shutdown) if isinstance(node, ast.Call)]
    assert not any(isinstance(func, ast.Attribute) and func.attr == "_exit" for func in calls)
    assert any(isinstance(func, ast.Attribute) and func.attr == "_shutdown_resources" for func in calls)


def test_both_adaptive_ranges_relocate_task_tabs_before_truncating_titles():
    source = Path("src/big_remote_play/ui/main_window.py").read_text()
    for name in ("compact", "narrow"):
        assert f'{name}.connect("apply", self._on_compact_header_apply)' in source
        assert f'{name}.connect("unapply", self._on_compact_header_unapply)' in source


def test_component_state_is_read_directly_without_expanding_a_summary(ui):
    """The relevant service states are on screen, not behind a summary row."""
    ui.navigate_to("host")
    drain()

    row = ui._status_rows["sunshine"]
    assert row.get_visible()
    assert row.get_subtitle() in {"Running", "Stopped", "Missing"}
    # No ancestor collapses the row, so no click stands between the user and
    # the state; the Moonlight row simply does not belong to this path.
    ancestor = row.get_parent()
    while ancestor is not None:
        assert not isinstance(ancestor, Adw.ExpanderRow)
        ancestor = ancestor.get_parent()
    assert not ui._status_rows["moonlight"].get_visible()


def test_joining_replaces_the_form_with_the_connected_state(ui, monkeypatch):
    """Once this PC is on the network, the join button has nothing left to do."""
    import big_remote_play.ui.private_network_view as pnv

    monkeypatch.setattr(pnv.ConnectPage, "_prefill_from_history", lambda self: None)
    monkeypatch.setattr(pnv, "_save_history", lambda entry: "id")
    monkeypatch.setattr(pnv, "VPNAccountManager", lambda _system_check: Mock())
    monkeypatch.setattr(pnv.threading, "Thread", lambda target, daemon: types.SimpleNamespace(start=lambda: None))
    ui._apply_vpn_selection("tailscale")
    page = ui.connect_private_view.get_child()
    assert page._connect_form.get_visible()

    page._c_done(True)

    assert not page._connect_form.get_visible()
    assert page._return_to_game.get_visible()
    assert page._c_title.get_label() == "Already connected"


def test_adding_an_account_keeps_the_join_form_on_a_connected_pc(ui, monkeypatch):
    """ "Add another account" must offer a sign-in, not repeat "Already connected"."""
    import big_remote_play.ui.private_network_view as pnv

    monkeypatch.setattr(pnv.ConnectPage, "_prefill_from_history", lambda self: None)
    monkeypatch.setattr(pnv, "provider_connected", lambda *args: True)

    ui._apply_vpn_selection("tailscale", add_account=True)
    drain()
    page = ui.connect_private_view.get_child()

    assert page._add_account
    assert page._connect_form.get_visible()
    assert page._c_title.get_label() != "Already connected"
