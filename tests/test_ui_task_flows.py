"""Real GTK task-flow tests. Service adapters are hermetic, never installed/launched.

Run under Xvfb and a session D-Bus; no screenshot matching, internet or GPU needed.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import xml.etree.ElementTree as ET

import pytest
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk
from big_remote_play import paths
from big_remote_play.ui.components import action_row
from big_remote_play.ui.main_window import MainWindow
from big_remote_play.ui.host_view import HostView
from big_remote_play.host.sunshine_manager import SunshineHost
from big_remote_play.utils.audio import AudioManager
from big_remote_play.utils.config import Config
from big_remote_play.utils.moonlight_config import MoonlightConfigManager
from big_remote_play.utils.network import NetworkDiscovery


def drain() -> None:
    context = GLib.MainContext.default()
    for _ in range(100):
        if not context.pending():
            break
        context.iteration(False)


@pytest.fixture
def ui(tmp_path, monkeypatch):
    if Gdk.Display.get_default() is None:
        pytest.skip("Requires a GTK display (run under xvfb-run)")
    Adw.init()
    Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.setitem(paths.__dict__, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setitem(paths.__dict__, "SUNSHINE_CONFIG_DIR", tmp_path / "sunshine")
    monkeypatch.setitem(paths.__dict__, "SUNSHINE_CONF", tmp_path / "sunshine/sunshine.conf")
    monkeypatch.setattr(MoonlightConfigManager, "_shared_state", {})
    import big_remote_play.ui.main_window as mw

    monkeypatch.setattr(mw, "VPN_CONFIG_FILE", str(tmp_path / "vpn.json"))
    monkeypatch.setattr(MainWindow, "check_system", lambda self: self.update_dependency_ui(True, True, True, True, True))
    monkeypatch.setattr(HostView, "detect_monitors", lambda self: [("Automatic", "auto")])
    monkeypatch.setattr(HostView, "detect_gpus", lambda self: [{"label": "Automatic", "encoder": "auto", "adapter": "auto"}])
    monkeypatch.setattr(HostView, "_ensure_sunshine_config", lambda self: None)
    monkeypatch.setattr(SunshineHost, "is_running", lambda self: False)
    monkeypatch.setattr(AudioManager, "get_passive_sinks", lambda self: [])
    monkeypatch.setattr(AudioManager, "get_default_sink", lambda self: None)
    monkeypatch.setattr(AudioManager, "disable_streaming_audio", lambda *args: None)
    monkeypatch.setattr(NetworkDiscovery, "discover_hosts", lambda self, callback, **kwargs: callback([]))
    # The private-network pages probe the real tailscale/zerotier CLIs, each
    # with a ten-second timeout; the UI tests are about the pages, not the CLIs.
    import big_remote_play.ui.private_network_view as pnv

    monkeypatch.setattr(pnv, "provider_connected", lambda *args: False)
    win = MainWindow(config=Config())
    win.present()
    drain()
    # With no window manager the X server hands the window its natural size
    # (~630px wide), which trips the compact breakpoint. These tests target the
    # 1280x720 baseline, so they start from the wide state.
    win._on_compact_header_unapply()
    yield win
    dialog = win.get_visible_dialog()
    if dialog:
        dialog.close()
    win.close()
    drain()


def test_vpn_pages_are_not_built_on_startup(ui):
    assert ui.content_stack.get_child_by_name("create_private") is None
    assert ui.content_stack.get_child_by_name("connect_private") is None
    assert ui.current_page == "welcome"


def test_home_help_is_native_rounded_action_and_opens_network(ui):
    guide = ui._create_home_network_guide()
    assert isinstance(guide, Gtk.ListBox)
    assert guide.has_css_class("brp-boxed")
    row = guide.get_first_child()
    assert isinstance(row, Adw.ActionRow) and row.get_activatable()
    row.emit("activated")
    assert ui.current_page == "vpn_selector"


def test_sharing_selects_source_before_start_and_keeps_advanced_off_main(ui):
    host = ui.host_view
    assert host.share_controls.get_last_child() is host.overview_start_button
    # The page states the picture settings; the controls themselves stay in the sheet.
    assert host.quality_summary_row.is_ancestor(host)
    assert not host.fps_row.is_ancestor(host)
    assert not host.bandwidth_row.is_ancestor(host)
    assert host.overview_start_button.get_accessible_role() == Gtk.AccessibleRole.BUTTON
    assert host.game_mode_row.get_use_subtitle()  # full selected source, not clipped suffix


def test_automatic_quality_locks_the_rows_it_owns_and_frees_them_when_turned_off(ui):
    host = ui.host_view
    host.auto_quality_row.set_active(True)
    host._apply_auto_quality(force=True)
    assert host.redetect_row.get_visible()
    assert not host.fps_row.get_sensitive()
    assert not host.gpu_row.get_sensitive()
    # What it chose is visible on the page, not only inside the sheet.
    assert "Automatic capture and encoding" in host.quality_summary_row.get_subtitle()
    assert host.bandwidth_row.get_sensitive()
    assert host.bandwidth_row.get_value() == 0
    assert "FPS" not in host.quality_summary_row.get_subtitle()

    host.auto_quality_row.set_active(False)
    assert not host.redetect_row.get_visible()
    assert host.fps_row.get_sensitive()
    assert host.bandwidth_row.get_sensitive()


def test_each_host_settings_sheet_is_reachable_and_preserves_widgets(ui):
    host = ui.host_view
    assert len(host.settings_dialogs) == 3
    for dialog in host.settings_dialogs.values():
        dialog.present(host)
        drain()
        assert ui.get_visible_dialog() is dialog
        dialog.close()
        drain()
    assert host.fps_row.get_model().get_n_items() == 5
    assert host.platform_row.get_model().get_n_items() == 7


def test_mixer_has_an_explanatory_empty_state(ui):
    host = ui.host_view
    assert host.mixer_empty_row.get_visible()
    host._apply_mixer_apps([{"id": "4", "name": "Game & voice"}])
    assert not host.mixer_empty_row.get_visible()
    assert host.mixer_rows["4"].get_title() == "Game & voice"
    assert not host.mixer_rows["4"].get_use_markup()
    host._apply_mixer_apps([])
    assert host.mixer_empty_row.get_visible()


def test_guest_page_summarises_quality_and_the_row_opens_the_sheet(ui):
    guest = ui.guest_view
    # Neither the presets nor the individual settings sit under the computer
    # list: the page carries one row saying what will be sent.
    assert not guest.profile_row.is_ancestor(guest)
    assert not guest.resolution_row.is_ancestor(guest)
    assert guest.image_row.is_ancestor(guest)
    guest.image_row.emit("activated")
    drain()
    assert ui.get_visible_dialog() is guest.image_dialog


def test_other_ways_sheet_carries_address_and_pin_with_focus_on_the_asked_field(ui):
    guest = ui.guest_view
    assert not guest.manual_ip_entry.is_ancestor(guest)
    guest.present_other_ways("pin")
    drain()
    assert ui.get_visible_dialog() is guest._search_code_dialog
    assert guest.pin_entry.is_ancestor(guest._search_code_dialog)
    assert not guest.manual_ip_entry.is_ancestor(guest._search_code_dialog)
    assert guest.manual_ip_entry.is_ancestor(guest._address_dialog.get_child())
    guest.close_other_ways()
    drain()


@pytest.mark.parametrize("index, width, bitrate", [(1, "1920", "20000"), (2, "1280", "8000"), (3, "2560", "40000")])
def test_quality_profiles_save_values_once_and_keep_audio(ui, index, width, bitrate):
    guest = ui.guest_view
    guest.audio_row.set_active(False)
    guest.profile_row.set_selected(0)
    guest.profile_row.set_selected(index)
    assert guest.moonlight_config.get("width") == width
    assert guest.moonlight_config.get("fps") == "60"
    assert guest.moonlight_config.get("bitrate") == bitrate
    assert not guest.audio_row.get_active()
    assert guest.profile_row.get_selected() == index
    # Choosing a preset by hand is a decision detection must not overwrite.
    assert not guest._is_automatic()


def test_automatic_profile_follows_this_screen_and_can_be_left_again(ui):
    guest = ui.guest_view
    guest.profile_row.set_selected(0)
    assert guest._is_automatic()
    assert guest.profile_row.get_selected() == 0
    assert guest.moonlight_config.get("width")
    guest.profile_row.set_selected(1)
    assert not guest._is_automatic()


def test_custom_profile_never_overwrites_values(ui):
    guest = ui.guest_view
    guest.bitrate_scale.set_value(13.5)
    before = guest.moonlight_config.get("bitrate")
    # Custom values leave every preset unselected and must not be rewritten.
    assert guest.profile_row.get_selected() == guest._CUSTOM_PROFILE
    guest._apply_profile()
    assert guest.moonlight_config.get("bitrate") == before


def test_custom_resolution_summary_displays_real_values_after_reload(ui):
    guest = ui.guest_view
    guest._set_automatic(False)
    guest.scale_row.set_active(False)
    for key, value in {"width": 1600, "height": 900, "fps": 75}.items():
        guest.moonlight_config.set(key, value)
    guest.load_guest_settings()
    assert "1600x900" in guest.image_row.get_subtitle()
    assert "75 FPS" in guest.image_row.get_subtitle()
    assert "1600x900" in guest.image_row.get_subtitle()
    assert guest.profile_row.get_selected() == guest._CUSTOM_PROFILE


def test_connect_is_one_page_and_private_network_keeps_the_header_switcher(ui):
    ui.navigate_to("guest")
    guest = ui.guest_view
    # One question, one page: no switcher, and no leftover in-page navigation.
    assert ui._header_context is None
    assert ui.header_title_stack.get_visible_child_name() == "title"
    assert not ui.compact_view_switcher.get_reveal()
    assert not hasattr(guest, "method_stack")
    assert guest.connect_card.is_ancestor(guest)

    ui._vpn_choice = "tailscale"
    ui.navigate_to("create_private")
    assert ui._header_context == "network"
    assert ui.header_title_stack.get_visible_child_name() == "switcher"
    assert ui.compact_view_switcher.get_stack() is ui.network_navigation_stack
    assert ui.network_navigation_stack.get_visible_child_name() == "create_private"
    ui.navigate_to("connect_private")
    assert ui.network_navigation_stack.get_visible_child_name() == "connect_private"
    ui.navigate_to("vpn_selector")
    assert ui.network_navigation_stack.get_visible_child_name() == "vpn_selector"


def test_computer_row_is_keyboard_activatable_and_handles_literal_markup(ui):
    guest = ui.guest_view
    host = {"name": "Alice & Bob <Game>", "ip": "192.168.1.9", "port": 47989}
    guest.update_hosts_list([host])
    row = guest.hosts_list.get_row_at_index(0)
    assert isinstance(row, Adw.ActionRow)
    assert not row.get_use_markup()
    row.emit("activated")
    assert guest.selected_host_card_data is host
    assert guest.main_connect_btn.get_sensitive()


@pytest.mark.parametrize("value", ["", "12x456", "12345", "１２３４５６", "1234567"])
def test_pin_rejects_invalid_values_before_discovery(ui, monkeypatch, value):
    guest = ui.guest_view
    error = Mock()
    monkeypatch.setattr(guest, "show_error_dialog", error)
    guest.pin_entry.set_text(value)
    guest.connect_pin(None)
    assert error.called
    assert not getattr(guest, "is_connecting", False)


@pytest.mark.parametrize("address,port", [("https://game.local", "47989"), ("game pc", "47989"), ("-host", "47989"), ("", "47989"), ("host", "0"), ("host", "65536"), ("host", "abc")])
def test_manual_input_is_validated_before_connecting(ui, monkeypatch, address, port):
    guest = ui.guest_view
    error = Mock()
    monkeypatch.setattr(guest, "show_error_dialog", error)
    guest.manual_ip_entry.set_text(address)
    guest.manual_port_entry.set_text(port)
    connect = Mock()
    monkeypatch.setattr(guest, "connect_to_host", connect)
    guest.on_main_button_clicked("manual")
    assert error.called
    connect.assert_not_called()


def test_network_returns_to_original_role(ui):
    for role in ("host", "guest"):
        ui.navigate_to(role)
        ui.navigate_to("vpn_selector")
        ui.return_from_network()
        assert ui.current_page == role


def test_no_duplicate_discovery_worker(ui, monkeypatch):
    guest = ui.guest_view
    callbacks = []
    monkeypatch.setattr(NetworkDiscovery, "discover_hosts", lambda self, callback: callbacks.append(callback))
    guest.discover_hosts()
    guest.discover_hosts()
    assert len(callbacks) == 1
    callbacks[0]([])
    assert not guest._discovery_running


def test_plain_native_actions_do_not_parse_ampersands_or_angle_brackets(ui):
    called = Mock()
    row = action_row("R&D <settings>", "A < B & C", "brp-preferences-system-symbolic", called)
    assert not row.get_use_markup()
    row.emit("activated")
    called.assert_called_once()


def test_sunshine_numeric_help_is_plain_text_and_negative_timeout_preserved(ui):
    from big_remote_play.ui.sunshine_preferences import SunshineSettings

    dialog = SunshineSettings()
    opt = next(item for item in dialog.get_input_options() if item[0] == "back_button_timeout")
    row = dialog.create_option_row(opt)
    assert isinstance(row, Adw.SpinRow)
    assert not row.get_use_markup()
    assert "< 0" in row.get_subtitle()
    assert row.get_value() == -1


def test_installer_requires_explicit_install_and_only_requested_packages(ui, monkeypatch):
    from big_remote_play.ui.installer_window import InstallerWindow

    launch = Mock()
    monkeypatch.setattr(InstallerWindow, "start_installation", launch)
    win = InstallerWindow(parent=ui, packages=("moonlight-qt",))
    launch.assert_not_called()
    assert win.packages == ("moonlight-qt",)
    assert not win.frame.get_visible()
    win.install_btn.emit("clicked")
    launch.assert_called_once()
    win.close()


def test_installer_failure_can_retry_and_success_callback_is_once(ui):
    from big_remote_play.ui.installer_window import InstallerWindow

    callback = Mock()
    win = InstallerWindow(parent=ui, on_success=callback)
    win.on_failure(1)
    assert win.install_btn.get_sensitive()
    win.on_success()
    win.on_success()
    callback.assert_called_once()
    win.close()


def test_active_installation_cannot_be_silently_closed(ui):
    from big_remote_play.ui.installer_window import InstallerWindow

    win = InstallerWindow(parent=ui)
    win._running = True
    assert win._on_close(win) is True
    win._running = False
    assert win._on_close(win) is False
    win.close()


@pytest.mark.parametrize("packages", [(), ("other",), ("sunshine;touch /tmp/pwned",)])
def test_installer_rejects_unreviewed_arguments(packages):
    from big_remote_play.ui.installer_window import _installer_argv

    with pytest.raises(ValueError):
        _installer_argv(packages)


@pytest.mark.parametrize("operation", ["lookup", "store", "clear"])
def test_libsecret_bus_failures_are_safe_app_errors(operation):
    from big_remote_play.utils.secret_store import LibsecretBackend, SecretKey, SecretStoreUnavailable

    backend = LibsecretBackend.__new__(LibsecretBackend)
    backend._schema = object()
    fail = Mock(side_effect=RuntimeError("No session service"))
    backend._secret = SimpleNamespace(password_lookup_sync=fail, password_store_sync=fail, password_clear_sync=fail, COLLECTION_DEFAULT="default")
    key = SecretKey("zerotier", "api_token", "default")
    with pytest.raises(SecretStoreUnavailable):
        getattr(backend, operation)(key, "secret", "label") if operation == "store" else getattr(backend, operation)(key)


def test_new_symbolic_icons_are_valid_local_self_contained_vectors():
    names = (
        "brp-network-server-symbolic",
        "brp-network-workgroup-symbolic",
        "brp-network-private-symbolic",
        "brp-cloud-symbolic",
        "brp-quality-symbolic",
        "brp-preferences-symbolic",
        "brp-network-setup-symbolic",
        "brp-network-connect-symbolic",
        "brp-provider-switch-symbolic",
        "brp-address-symbolic",
        "brp-firewall-symbolic",
    )
    for name in names:
        file = Path("usr/share/big-remote-play/icons") / f"{name}.svg"
        svg = ET.parse(file).getroot()
        assert svg.attrib["viewBox"] == "0 0 16 16"
        assert "http" not in "".join(element.attrib.get("href", "") for element in svg.iter())


class InlineThread:
    """Keep helper failures deterministic while retaining the main-loop handoff."""

    def __init__(self, target, **kwargs):
        self.target = target

    def start(self):
        self.target()


def test_network_helper_failure_reenables_connect_and_stops_spinner(ui, monkeypatch):
    import big_remote_play.ui.private_network_view as pnv

    monkeypatch.setattr(pnv.threading, "Thread", InlineThread)
    monkeypatch.setattr(pnv, "_load_history", lambda: [])
    monkeypatch.setattr(pnv.subprocess, "Popen", Mock(side_effect=FileNotFoundError("pkexec")))
    page = pnv.ConnectPage("tailscale", ui)
    page._btn_connect.emit("clicked")
    drain()
    assert page._btn_connect.get_sensitive()
    assert not page._c_spinner.get_visible()
    assert page._c_lbl.get_label() == "Try Again"
    assert not page._return_to_game.get_visible()


def test_create_helper_failure_is_delivered_to_completion_callback(ui, monkeypatch):
    import big_remote_play.ui.private_network_view as pnv

    monkeypatch.setattr(pnv.CreatePage, "_check_logged_in", lambda self: False)
    monkeypatch.setattr(pnv.CreatePage, "_is_vpn_installed", lambda self: True)
    monkeypatch.setattr(pnv.threading, "Thread", InlineThread)
    monkeypatch.setattr(pnv.subprocess, "Popen", Mock(side_effect=FileNotFoundError("pkexec")))
    page = pnv.CreatePage("tailscale", ui)
    done = Mock(return_value=False)
    page._run_script("create-network_zerotier.sh", [], done)
    drain()
    done.assert_called_once_with(127, {})


@pytest.mark.parametrize("network_id", ["", "abcdefghijklmnop", "a1b2c3d4", "a1b2c3d4e5f6a7b8;ls"])
def test_zerotier_id_is_validated_before_launch(ui, monkeypatch, network_id):
    import big_remote_play.ui.private_network_view as pnv

    monkeypatch.setattr(pnv, "_load_history", lambda: [])
    launch = Mock()
    monkeypatch.setattr(pnv.subprocess, "Popen", launch)
    page = pnv.ConnectPage("zerotier", ui)
    page._e_netid.set_text(network_id)
    page._btn_connect.emit("clicked")
    drain()
    # The page probes zerotier-cli for its own state; what must not happen is
    # the join script being launched with an invalid id.
    assert not [call for call in launch.call_args_list if "create-network" in " ".join(str(part) for part in call.args[0])]
    assert page._e_netid.has_css_class("error")
    assert page._btn_connect.get_sensitive()


def test_join_page_states_the_connection_instead_of_asking_to_sign_in_again(ui, monkeypatch):
    import big_remote_play.ui.private_network_view as pnv

    monkeypatch.setattr(pnv, "_load_history", lambda: [])
    monkeypatch.setattr(pnv, "provider_connected", lambda *args: True)
    monkeypatch.setattr(pnv.threading, "Thread", InlineThread)
    page = pnv.ConnectPage("tailscale", ui)
    drain()

    assert not page._connect_form.get_visible()
    assert page._return_to_game.get_visible()
    assert page._c_title.get_label() == "Already connected"


def test_backup_round_trips_and_refuses_a_crafted_archive(ui, tmp_path, monkeypatch):
    import tarfile

    from big_remote_play.ui.preferences import PreferencesWindow

    paths.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (paths.CONFIG_DIR / "config.json").write_text('{"theme": "dark"}')
    window = PreferencesWindow(transient_for=ui, config=ui.config)

    archive = tmp_path / "backup.tar.gz"
    window._write_backup(archive)
    assert archive.exists()
    with tarfile.open(archive) as handle:
        assert f"{paths.CONFIG_DIR.name}/config.json" in handle.getnames()
    # The partial file used while writing must not survive.
    assert not archive.with_name(archive.name + ".part").exists()

    # An archive that escapes the restore root is refused before extraction.
    hostile = tmp_path / "hostile.tar.gz"
    payload = tmp_path / "payload"
    payload.write_text("x")
    with tarfile.open(hostile, "w:gz") as handle:
        handle.add(payload, arcname=f"{paths.CONFIG_DIR.name}/../escaped")
    errors = []
    monkeypatch.setattr(window, "_show_error", lambda heading, body: errors.append(heading))
    window._restore_backup(hostile)
    assert errors and not (paths.CONFIG_DIR.parent / "escaped").exists()
    window.close()
    drain()


def test_background_discovery_keeps_the_chosen_computer(ui, monkeypatch):
    guest = ui.guest_view
    hosts = [{"name": "one", "ip": "10.0.0.1", "port": 47989}, {"name": "two", "ip": "10.0.0.2", "port": 47989}]
    guest.update_hosts_list(hosts)
    drain()
    guest.hosts_list.select_row(guest.hosts_list.get_row_at_index(1))
    assert guest.selected_host_card_data["ip"] == "10.0.0.2"

    # Same set: the list is left untouched, selection included.
    guest.update_hosts_list(list(hosts), keep_selection=True)
    assert guest.selected_host_card_data["ip"] == "10.0.0.2"

    # A newcomer rebuilds the list but the chosen computer stays chosen.
    guest.update_hosts_list([*hosts, {"name": "three", "ip": "10.0.0.3", "port": 47989}], keep_selection=True)
    drain()
    assert guest.selected_host_card_data["ip"] == "10.0.0.2"


def test_connected_network_page_shows_devices_not_a_sign_in_form(ui, monkeypatch):
    import big_remote_play.ui.private_network_view as pnv

    monkeypatch.setattr(pnv, "provider_connected", lambda *args: True)
    monkeypatch.setattr(pnv.CreatePage, "_refresh_networks", lambda self: None)
    ui._vpn_choice = "tailscale"
    ui.navigate_to("create_private")
    drain()
    page = ui.content_stack.get_child_by_name("create_private").get_child()
    page._apply_logged_in(True)
    drain()

    assert page._title.get_label() == "Devices on this private network"
    assert page._maintenance_group.get_visible()
    assert page._return_to_game.get_visible()


def test_network_token_help_is_visible_only_when_missing(ui, monkeypatch):
    import big_remote_play.ui.private_network_view as pnv

    monkeypatch.setattr(pnv, "_load_history", lambda: [])
    monkeypatch.setattr(pnv.threading, "Thread", InlineThread)
    monkeypatch.setattr(pnv, "_has_zerotier_api_token", lambda: False)
    without_token = pnv.ConnectPage("zerotier", ui)
    drain()
    assert without_token._token_help_row.get_visible()

    monkeypatch.setattr(pnv, "_has_zerotier_api_token", lambda: True)
    with_token = pnv.ConnectPage("zerotier", ui)
    drain()
    assert not with_token._token_help_row.get_visible()


@pytest.mark.parametrize(
    "output,connected",
    [
        ("[]", False),
        ('[{"status":"ACCESS_DENIED"}]', False),
        ('[{"status":"OK"}]', True),
    ],
)
def test_zerotier_connection_requires_a_real_authorized_network(monkeypatch, output, connected):
    import big_remote_play.ui.private_network_view as pnv

    monkeypatch.setattr(pnv.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=output))
    # Both the "my network" page and the join page ask this one function.
    assert pnv.provider_connected("zerotier", SimpleNamespace()) is connected


def test_cancelled_pin_does_not_reconnect_or_show_late_error(ui, monkeypatch):
    guest = ui.guest_view
    guest.is_connecting = False
    connect = Mock()
    error = Mock()
    monkeypatch.setattr(guest, "connect_to_host", connect)
    monkeypatch.setattr(guest, "show_error_dialog", error)
    guest._on_pin_resolved({"ip": "192.168.1.2"}, "123456")
    guest._on_pin_failed()
    connect.assert_not_called()
    error.assert_not_called()


def widget_tree(widget):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from widget_tree(child)
        child = child.get_next_sibling()


def test_password_validation_keeps_form_and_values_until_valid(ui, monkeypatch):
    host = ui.host_view
    monkeypatch.setattr(host, "_get_sunshine_creds", lambda: None)
    change = Mock()
    monkeypatch.setattr(host, "_change_password_async", change)
    host.open_password_dialog(None)
    drain()
    dialog = ui.get_visible_dialog()
    assert isinstance(dialog, Adw.Dialog)
    rows = {row.get_title(): row for row in widget_tree(dialog) if isinstance(row, Adw.EntryRow)}
    save = next(widget for widget in widget_tree(dialog) if isinstance(widget, Gtk.Button) and widget.get_label() == "Save")
    save.emit("clicked")
    assert ui.get_visible_dialog() is dialog
    change.assert_not_called()
    rows["New Password"].set_text("test-only-password")
    rows["Confirm New Password"].set_text("wrong-confirmation")
    save.emit("clicked")
    assert ui.get_visible_dialog() is dialog
    assert rows["New Password"].get_text() == "test-only-password"
    change.assert_not_called()
    rows["Confirm New Password"].set_text("test-only-password")
    save.emit("clicked")
    change.assert_called_once_with("sunshine", "test-only-password", ("sunshine", ""))
    drain()
    assert ui.get_visible_dialog() is None


def test_diagnostic_address_does_not_force_a_wide_layout(ui):
    host = ui.host_view
    field = host.field_widgets["ipv6"]
    field["label"].set_text("abcd:" * 30)
    assert field["label"].measure(Gtk.Orientation.HORIZONTAL, -1).minimum < 30


def test_installer_review_does_not_construct_terminal(ui):
    from big_remote_play.ui.installer_window import InstallerWindow

    win = InstallerWindow(parent=ui)
    assert not hasattr(win, "terminal")
    win.close()


def test_public_ip_diagnostics_are_lazy_and_do_not_overlap(ui, monkeypatch):
    host = ui.host_view
    import big_remote_play.ui.host_view as hv

    ipv4 = Mock(return_value="203.0.113.1")
    ipv6 = Mock(return_value="2001:db8::1")
    monkeypatch.setattr(NetworkDiscovery, "get_global_ipv4", ipv4)
    monkeypatch.setattr(NetworkDiscovery, "get_global_ipv6", ipv6)
    monkeypatch.setattr(hv.threading, "Thread", InlineThread)
    host.populate_summary_fields()
    ipv4.assert_not_called()
    host.diagnostics_expander.set_expanded(True)
    host.populate_summary_fields()
    ipv4.assert_called_once()
    ipv6.assert_called_once()
    drain()
    assert host.field_widgets["ipv6_global"]["real_value"] == "[2001:db8::1]"


def test_browser_rows_bind_their_own_target_and_accept_literal_names(ui):
    callback = Mock()
    first = ui.host_view._create_browse_button("A & B", "brp-folder-open-symbolic", "Open folder", callback, "/games/a")
    second = ui.host_view._create_browse_button("C <D>", "brp-folder-open-symbolic", "Open folder", callback, "/games/c")
    assert isinstance(first, Adw.ActionRow) and not first.get_use_markup()
    first.emit("activated")
    second.emit("activated")
    assert [call.args[0] for call in callback.call_args_list] == ["/games/a", "/games/c"]


def test_failed_reconnect_never_leaves_a_success_shortcut(ui, monkeypatch):
    import big_remote_play.ui.private_network_view as pnv

    monkeypatch.setattr(pnv, "_load_history", lambda: [])
    page = pnv.ConnectPage("tailscale", ui)
    page._return_to_game.set_visible(True)
    page._c_done(False)
    assert not page._return_to_game.get_visible()


def test_reused_network_page_updates_return_label_for_current_role(ui, monkeypatch):
    import big_remote_play.ui.private_network_view as pnv

    monkeypatch.setattr(pnv, "_load_history", lambda: [])
    ui._vpn_choice = "tailscale"
    for role, title in (("host", "Share my game"), ("guest", "Access shared game")):
        ui.navigate_to(role)
        ui.navigate_to("connect_private")
        page = ui.connect_private_view.get_child()
        assert page._return_to_game.get_first_child().get_subtitle() == title


@pytest.mark.parametrize("installed", [True, False])
def test_about_icon_resolves_for_installed_and_source_layouts(ui, monkeypatch, installed):
    from big_remote_play.app import BigRemotePlayApp

    theme = SimpleNamespace(has_icon=lambda name: installed and name == "br.com.biglinux.remoteplay")
    monkeypatch.setattr(Gtk.IconTheme, "get_for_display", lambda _display: theme)
    assert BigRemotePlayApp._application_icon_name() == ("br.com.biglinux.remoteplay" if installed else "big-remote-play")


def _walk_widgets(widget: Gtk.Widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from _walk_widgets(child)
        child = child.get_next_sibling()


def _view_stack_pages(stack: Adw.ViewStack) -> list[tuple[str, str, str]]:
    pages = stack.get_pages()
    result = []
    for index in range(pages.get_n_items()):
        page = pages.get_item(index)
        result.append((page.get_name(), page.get_title(), page.get_icon_name()))
    return result


def test_primary_switchers_have_titles_distinct_icons_and_native_tab_role(ui):
    ui._vpn_choice = "tailscale"
    stacks = {
        "host": ui.host_view.view_stack,
        "network": ui.network_navigation_stack,
    }
    for stack in stacks.values():
        pages = _view_stack_pages(stack)
        assert all(name and title and icon for name, title, icon in pages)
        assert len({icon for _name, _title, icon in pages}) == len(pages)

    assert ui.header_view_switcher.get_accessible_role() == Gtk.AccessibleRole.TAB_LIST
    ui.navigate_to("host")
    assert ui.header_view_switcher.get_stack() is ui.host_view.view_stack
    ui.navigate_to("create_private")
    assert ui.header_view_switcher.get_stack() is ui.network_navigation_stack


def test_header_switcher_moves_to_bottom_only_in_compact_mode(ui):
    ui.navigate_to("host")
    assert ui.header_title_stack.get_visible_child_name() == "switcher"
    assert not ui.compact_view_switcher.get_reveal()

    ui._on_compact_header_apply()
    assert ui.header_title_stack.get_visible_child_name() == "title"
    assert ui.compact_view_switcher.get_reveal()

    ui._on_compact_header_unapply()
    assert ui.header_title_stack.get_visible_child_name() == "switcher"
    assert not ui.compact_view_switcher.get_reveal()


def test_support_lists_are_rounded_and_discovery_empty_state_is_text_first(ui):
    ui.navigate_to("host")
    ui.host_view.view_stack.set_visible_child_name("support")
    groups = {widget.get_title(): widget for widget in _walk_widgets(ui.host_view) if isinstance(widget, Adw.PreferencesGroup) and widget.get_title()}
    for title in ("Server tools", "Advanced administration"):
        assert groups[title].has_css_class("brp-rounded-group")

    empty = ui.guest_view._build_discover_empty_state()
    image_sizes = [widget.get_pixel_size() for widget in _walk_widgets(empty) if isinstance(widget, Gtk.Image)]
    assert not any(size >= 40 for size in image_sizes)
    labels = [widget.get_label() for widget in _walk_widgets(empty) if isinstance(widget, Gtk.Label)]
    assert "No game PC found yet" in labels


def test_sunshine_web_panel_is_not_opened_while_the_server_is_stopped(ui, monkeypatch):
    host = ui.host_view
    opened = Mock()
    toasts = []
    monkeypatch.setattr("big_remote_play.ui.host_view.open_uri", opened)
    monkeypatch.setattr(HostView, "show_toast", lambda self, message: toasts.append(message))

    host.open_sunshine_config(None)
    opened.assert_not_called()
    assert toasts == ["Sunshine is not running."]

    monkeypatch.setattr(SunshineHost, "is_running", lambda self: True)
    host.open_sunshine_config(None)
    opened.assert_called_once()


def test_sunshine_without_a_systemd_unit_is_driven_by_its_own_manager(ui, monkeypatch):
    started = Mock(return_value=(True, None))
    monkeypatch.setattr(SunshineHost, "start", started)
    dialog = Gtk.Window()

    ui._run_sunshine_action("start", dialog)
    for _ in range(200):
        if started.called:
            break
        GLib.usleep(5000)
    drain()
    started.assert_called_once()
