from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib, Gdk  # type: ignore
import logging

_log = logging.getLogger("big-remoteplay")

import re
import threading, time
from big_remote_play.utils import auto_quality
from big_remote_play.utils.config import Config
from big_remote_play.guest.moonlight_client import MoonlightClient
from big_remote_play.utils.i18n import _
from big_remote_play.utils.icons import create_icon_widget
from big_remote_play.integration_contracts import BRP_DISCOVERY_CODE_LENGTH
from big_remote_play.utils.moonlight_config import MoonlightConfigManager
from .components import action_row, content_dialog, icon_tile, intro, note, set_row_icon, name_icon_button, preferences_dialog


class GuestView(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        # Guards saves/dialogs while load_guest_settings() drives the rows.
        self.loading_settings = True
        # Last committed combo index, used to revert when a "Custom" entry is
        # cancelled or invalid.
        self._last_res_idx = 1
        self._last_fps_idx = 1
        self.discovered_hosts = []
        self.is_connected = False
        self._attempt_id = 0
        self._attempt_cancel = threading.Event()
        self._closed = False
        self._stopping = False

        from big_remote_play.utils.logger import Logger

        self.config = Config()
        self.logger = None
        if self.config.get("verbose_logging", False):
            self.logger = Logger()

        self.moonlight_config = MoonlightConfigManager()
        self.moonlight = MoonlightClient(logger=self.logger)
        self.setup_ui()
        # Home must not launch a subnet scan. Begin when Connect is shown.
        self.connect("map", self._on_mapped)
        self._connection_timer = GLib.timeout_add(1000, self.monitor_connection)
        # The other PC usually starts sharing after this page is already open.
        self._discovery_timer = GLib.timeout_add_seconds(10, self._auto_discover)

    def _root_window(self):
        root = self.get_root()
        return root if isinstance(root, Gtk.Window) else None

    @staticmethod
    def _selected_string(row: Adw.ComboRow, fallback: str) -> str:
        item = row.get_selected_item()
        get_string = getattr(item, "get_string", None)
        return str(get_string()) if callable(get_string) else fallback

    def _moonlight_bool_row(
        self,
        key: str,
        title: str,
        subtitle: str | None = None,
        default: str = "false",
        *,
        invert: bool = False,
    ) -> Adw.SwitchRow:
        row = Adw.SwitchRow(title=title)
        if subtitle:
            row.set_subtitle(subtitle)
        native = self.moonlight_config.get(key, default).lower() == "true"
        row.set_active(not native if invert else native)

        def save(widget: Adw.SwitchRow, _pspec) -> None:
            if self.loading_settings:
                return
            value = not widget.get_active() if invert else widget.get_active()
            self.moonlight_config.set(key, str(value).lower())

        row.connect("notify::active", save)
        self._moonlight_boolean_rows[key] = (row, default, invert)
        return row

    def _moonlight_combo_row(
        self,
        key: str,
        title: str,
        choices: list[tuple[str, str]],
        default: str,
        subtitle: str | None = None,
    ) -> Adw.ComboRow:
        row = Adw.ComboRow(title=title)
        if subtitle:
            row.set_subtitle(subtitle)
        row.set_model(Gtk.StringList.new([label for _value, label in choices]))
        current = self.moonlight_config.get(key, default)
        row.set_selected(next((index for index, (value, _label) in enumerate(choices) if value == current), 0))

        def save(widget: Adw.ComboRow, _pspec) -> None:
            if self.loading_settings:
                return
            self.moonlight_config.set(key, choices[widget.get_selected()][0])

        row.connect("notify::selected", save)
        self._moonlight_combo_rows[key] = (row, choices, default)
        return row

    def _build_reconnect_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        button = Gtk.Button(label=_("Apply and Reconnect"))
        button.add_css_class("suggested-action")
        button.set_size_request(-1, 50)
        button.set_visible(False)
        button.connect("clicked", lambda _button: self.check_reconnect())
        group.add(button)
        self.apply_settings_buttons.append(button)
        return group

    def _sync_audio_summary(self) -> None:
        if not hasattr(self, "audio_settings_row"):
            return
        layout = self._selected_string(self.audio_config_row, _("Stereo"))
        location = _("Game PC + this computer") if self.audio_row.get_active() else _("This computer only")
        self.audio_settings_row.set_subtitle(_("{layout} · {location}").format(layout=layout, location=location))

    def _quality_summary(self) -> str:
        """One-line summary shown on the main Connect page."""
        resolution = self._selected_string(self.resolution_row, "1080p")
        frame_rate = self._selected_string(self.fps_row, "60 FPS")
        if self.scale_row.get_active():
            resolution = _("This screen's resolution")
        elif self.resolution_row.get_selected() == 4:
            resolution = self.custom_resolution_val or resolution
        if self.fps_row.get_selected() == 3:
            frame_rate = f"{self.custom_fps_val or 60} FPS"
        bitrate = _("{value:g} Mbps").format(value=self.bitrate_scale.get_value())
        if self._is_automatic():
            return _("Automatic · {resolution} · {frame_rate} · {bitrate}").format(
                resolution=resolution,
                frame_rate=frame_rate,
                bitrate=bitrate,
            )
        return _("{resolution} · {frame_rate} · {bitrate}").format(
            resolution=resolution,
            frame_rate=frame_rate,
            bitrate=bitrate,
        )

    def setup_ui(self) -> None:
        clamp = Adw.Clamp()
        self.content_clamp = clamp
        clamp.set_maximum_size(820)
        clamp.set_valign(Gtk.Align.START)
        for m in ["top", "bottom", "start", "end"]:
            getattr(clamp, f"set_margin_{m}")(24)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)

        from .performance_monitor import PerformanceMonitor

        self.perf_monitor = PerformanceMonitor()
        self.perf_monitor.set_visible(False)
        content.append(self.perf_monitor)

        # One question per page: which computer. Address and PIN are not rival
        # tabs — they are what the page offers next when no computer answers.
        self.connect_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.connect_card.add_css_class("card")
        self.connect_card.add_css_class("padded")
        self.connect_card.append(self.create_discover_page())

        reset_btn = Gtk.Button(icon_name="brp-edit-undo-symbolic")
        reset_btn.add_css_class("flat")
        name_icon_button(reset_btn, _("Reset connection settings"))
        reset_btn.connect("clicked", self.on_reset_clicked)

        help_title = _("Shortcuts & Instructions")
        # Adw.PreferencesRow titles accept Pango markup. Escape translated text
        # so a literal ampersand (as in the English title) is not parsed as an
        # unterminated entity and remains visible without GTK warnings.
        help_row = Adw.ActionRow(title=GLib.markup_escape_text(help_title), subtitle=_("Keyboard Shortcuts"))
        set_row_icon(help_row, "brp-input-keyboard-symbolic")
        help_row.set_activatable(True)
        help_row.add_suffix(create_icon_widget("go-next-symbolic", size=16))
        help_row.connect("activated", lambda _row: self.show_shortcuts_dialog())

        self._moonlight_boolean_rows: dict[str, tuple[Adw.SwitchRow, str, bool]] = {}
        self._moonlight_combo_rows: dict[str, tuple[Adw.ComboRow, list[tuple[str, str]], str]] = {}
        self.apply_settings_buttons: list[Gtk.Button] = []

        # Presets handle the routine decisions. Detailed controls remain in the
        # same Image dialog, below the presets, rather than in a second expert UI.
        self.profile_row = Adw.ComboRow(title=_("Image preset"), use_subtitle=True, use_markup=False)
        self.profile_row.set_model(
            Gtk.StringList.new(
                [
                    _("Automatic"),
                    _("Balanced"),
                    _("Save bandwidth"),
                    _("Sharper picture"),
                    _("4K"),
                    _("Custom"),
                ]
            )
        )
        self.profile_group = Adw.PreferencesGroup()
        self.profile_group.set_header_suffix(reset_btn)
        self.profile_group.add(self.profile_row)

        self.resolution_row = Adw.ComboRow(title=_("Resolution"), use_markup=False)
        self.resolution_row.set_model(Gtk.StringList.new(["720p", "1080p", "1440p", "4K", _("Custom")]))
        self.resolution_row.set_selected(1)

        self.hw_decode_row = Adw.SwitchRow(title=_("Use hardware decoding when available"))
        self.hw_decode_row.set_subtitle(_("Recommended for most computers. It reduces processor use and usually makes video smoother."))
        self.hw_decode_row.set_active(True)

        self.scale_row = Adw.SwitchRow(title=_("Use this screen's resolution"))
        self.scale_row.set_subtitle(_("Matches the stream to the physical resolution of the screen used for playback."))
        self.scale_row.set_active(True)
        self.scale_row.connect("notify::active", self.on_scale_changed)

        bitrate_row = Adw.ActionRow(title=_("Image quality / video bitrate (Mbps)"))
        bitrate_row.set_subtitle(_("Controls how much video data is sent each second. Higher values can look sharper but need a faster connection."))
        self.bitrate_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.5, 150.0, 0.5)
        self.bitrate_scale.set_hexpand(True)
        self.bitrate_scale.set_draw_value(True)
        self.bitrate_scale.set_value(20.0)
        self.bitrate_scale.update_property([Gtk.AccessibleProperty.LABEL], [_("Image quality / video bitrate (Mbps)")])
        slider_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        slider_box.add_css_class("brp-slider-control")
        slider_box.append(bitrate_row)
        slider_box.append(self.bitrate_scale)

        self.fps_row = Adw.ComboRow(title=_("Frame rate"), use_markup=False)
        self.fps_row.set_model(Gtk.StringList.new(["30 FPS", "60 FPS", "120 FPS", _("Custom")]))
        self.fps_row.set_selected(1)
        self.custom_resolution_val = ""
        self.custom_fps_val = ""
        self.resolution_row.connect("notify::selected-item", self.on_resolution_changed)
        self.fps_row.connect("notify::selected-item", self.on_fps_changed)
        self.custom_resolution_edit = Gtk.Button(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER, visible=False)
        name_icon_button(self.custom_resolution_edit, _("Edit custom resolution"))
        self.custom_resolution_edit.add_css_class("flat")
        self.custom_resolution_edit.connect("clicked", lambda _button: self.on_resolution_changed(self.resolution_row, None))
        self.resolution_row.add_suffix(self.custom_resolution_edit)
        self.custom_fps_edit = Gtk.Button(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER, visible=False)
        name_icon_button(self.custom_fps_edit, _("Edit custom frame rate"))
        self.custom_fps_edit.add_css_class("flat")
        self.custom_fps_edit.connect("clicked", lambda _button: self.on_fps_changed(self.fps_row, None))
        self.fps_row.add_suffix(self.custom_fps_edit)
        self.display_mode_row = Adw.ComboRow(title=_("Display mode"), use_markup=False)
        self.display_mode_row.set_model(Gtk.StringList.new([_("Borderless Window"), _("Fullscreen"), _("Windowed")]))

        decoding_group = Adw.PreferencesGroup()
        decoding_group.add(self.hw_decode_row)
        self.custom_picture_group = Adw.PreferencesGroup(title=_("Custom image settings"))
        self.custom_picture_group.set_description(_("Changes here only affect your next connection. They do not change the game PC's display."))
        self.custom_picture_group.add(self.scale_row)
        self.custom_picture_group.add(self.resolution_row)
        self.custom_picture_group.add(self.fps_row)
        self.custom_picture_group.add(slider_box)
        self.custom_picture_group.set_visible(False)

        self.vsync_row = self._moonlight_bool_row(
            "vsync",
            _("V-Sync"),
            _("Prevents image tearing. Disabling it can reduce latency."),
            "true",
        )
        self.frame_pacing_row = self._moonlight_bool_row(
            "framepacing",
            _("Frame pacing"),
            _("Makes motion more even when frame delivery varies."),
            "false",
        )
        self.codec_row = self._moonlight_combo_row(
            "videocfg",
            _("Video codec"),
            [("0", _("Automatic (Recommended)")), ("1", "H.264"), ("2", "HEVC"), ("4", "AV1")],
            "0",
            _("Automatic mode chooses the best format supported by both computers."),
        )
        self.hdr_row = self._moonlight_bool_row(
            "hdr",
            _("Enable HDR"),
            _("Use only when both computers and the screen support HDR."),
            "false",
        )
        self.yuv444_row = self._moonlight_bool_row(
            "yuv444",
            _("Enable YUV 4:4:4"),
            _("Improves text and fine details, but uses more bandwidth and decoding power."),
            "false",
        )
        self.unlock_bitrate_row = self._moonlight_bool_row(
            "unlockbitrate",
            _("Unlock the bitrate limit"),
            _("Allows unusually high values. Use only on a fast, stable network."),
            "false",
        )
        self.unlock_bitrate_row.connect("notify::active", self._on_bitrate_limit_changed)
        self.performance_overlay_row = self._moonlight_bool_row(
            "showperfoverlay",
            _("Show performance statistics during streaming"),
            _("Displays latency, frame rate, and decoding information over the stream."),
            "false",
        )
        self.connection_warnings_row = self._moonlight_bool_row(
            "connwarnings",
            _("Warn when connection quality drops"),
            _("Shows a warning when the network becomes unstable or too slow."),
            "true",
        )
        self.keep_awake_row = self._moonlight_bool_row(
            "keepawake",
            _("Keep this screen active during streaming"),
            _("Prevents this computer's screen from turning off during a session."),
            "true",
        )
        playback_group = Adw.PreferencesGroup(title=_("Playback on this computer"))
        for row in (self.display_mode_row, self.vsync_row, self.frame_pacing_row, self.keep_awake_row):
            playback_group.add(row)
        compatibility_group = Adw.PreferencesGroup()
        self.image_details = Adw.ExpanderRow(title=_("Video format and diagnostics"), use_markup=False)
        self.image_details.set_subtitle(_("Codec, HDR, data limit and connection warnings"))
        for row in (
            self.codec_row,
            self.hdr_row,
            self.yuv444_row,
            self.unlock_bitrate_row,
            self.performance_overlay_row,
            self.connection_warnings_row,
        ):
            self.image_details.add_row(row)
        compatibility_group.add(self.image_details)

        self.audio_config_row = self._moonlight_combo_row(
            "audiocfg",
            _("Audio layout"),
            [("0", _("Stereo")), ("1", "5.1"), ("2", "7.1")],
            "0",
            _("Choose surround sound only when the speakers on this computer support it."),
        )
        self.audio_row = Adw.SwitchRow(title=_("Also play sound on the game PC"))
        self.audio_row.set_subtitle(_("When enabled, the game PC keeps playing sound. Turning this off may make Sunshine switch to a virtual output."))
        self.audio_row.set_active(True)
        self.mute_focus_row = self._moonlight_bool_row(
            "muteonfocusloss",
            _("Mute when Moonlight is not the active window"),
            None,
            "false",
        )
        audio_group = Adw.PreferencesGroup(title=_("Audio"))
        audio_group.set_description(_("These choices control how sound is played during this connection."))
        audio_group.add(self.audio_config_row)
        audio_group.add(self.audio_row)
        audio_group.add(self.mute_focus_row)

        self.optimize_mouse_row = self._moonlight_bool_row(
            "mouseacceleration",
            _("Optimize the mouse for remote desktop"),
            _("Makes pointer movement more suitable for desktop use than for games."),
            "false",
        )
        self.capture_keys_row = self._moonlight_combo_row(
            "capturesyskeys",
            _("Send system shortcuts to the remote computer"),
            [("0", _("Never")), ("1", _("Only in fullscreen")), ("2", _("Always"))],
            "0",
            _("Controls shortcuts such as Alt+Tab and the Super key."),
        )
        self.touch_trackpad_row = self._moonlight_bool_row(
            "abstouchmode",
            _("Use the touchscreen as a virtual trackpad"),
            _("Useful for precise pointer control on touch devices."),
            "true",
            invert=True,
        )
        self.swap_mouse_row = self._moonlight_bool_row(
            "swapmousebuttons",
            _("Swap the left and right mouse buttons"),
            None,
            "false",
        )
        self.reverse_scroll_row = self._moonlight_bool_row(
            "reversescroll",
            _("Reverse the scroll-wheel direction"),
            None,
            "false",
        )
        input_group = Adw.PreferencesGroup(title=_("Mouse, keyboard, and touchscreen"))
        for row in (
            self.optimize_mouse_row,
            self.capture_keys_row,
            self.touch_trackpad_row,
            self.swap_mouse_row,
            self.reverse_scroll_row,
        ):
            input_group.add(row)

        self.swap_gamepad_row = self._moonlight_bool_row(
            "swapfacebuttons",
            _("Swap the A/B and X/Y buttons"),
            _("Use this when the controller labels do not match the game."),
            "false",
        )
        self.multi_controller_row = self._moonlight_bool_row(
            "multicontroller",
            _("Use multiple controllers"),
            _("Allows more than one controller to be sent to the game PC."),
            "true",
        )
        self.gamepad_mouse_row = self._moonlight_bool_row(
            "gamepadmouse",
            _("Use the controller to move the mouse"),
            None,
            "true",
        )
        self.background_gamepad_row = self._moonlight_bool_row(
            "backgroundgamepad",
            _("Keep controller input active in the background"),
            _("Continues processing the controller when Moonlight is not the focused window."),
            "false",
        )
        controller_group = Adw.PreferencesGroup(title=_("Controllers"))
        for row in (
            self.swap_gamepad_row,
            self.multi_controller_row,
            self.gamepad_mouse_row,
            self.background_gamepad_row,
        ):
            controller_group.add(row)

        self.game_optimization_row = self._moonlight_bool_row(
            "gameopts",
            _("Ask the game PC to optimize the game for streaming"),
            _("This is a request. It only works when the game PC supports it."),
            "true",
        )
        self.quit_after_row = self._moonlight_bool_row(
            "quitAppAfter",
            _("Close the game on the game PC when streaming ends"),
            _("Useful on a dedicated game PC. Leave it off for normal use."),
            "false",
        )
        host_request_group = Adw.PreferencesGroup(title=_("Requests to the game PC"))
        host_request_group.set_description(_("The game PC decides whether these requests can be applied."))
        host_request_group.add(self.game_optimization_row)
        host_request_group.add(self.quit_after_row)

        self.autodiscovery_row = self._moonlight_bool_row(
            "mdns",
            _("Discover game PCs automatically"),
            _("Searches the local network for computers that are sharing a game."),
            "true",
        )
        self.blocked_connection_row = self._moonlight_bool_row(
            "detectnetblocking",
            _("Check for blocked connections automatically"),
            _("Warns when firewall or network rules may be preventing a connection."),
            "true",
        )
        connection_group = Adw.PreferencesGroup(title=_("Connection checks"))
        connection_group.set_description(_("These checks run on this computer and do not change the game PC."))
        connection_group.add(self.autodiscovery_row)
        connection_group.add(self.blocked_connection_row)

        self.image_dialog = preferences_dialog(
            _("Image"),
            [
                decoding_group,
                self.profile_group,
                self.custom_picture_group,
                playback_group,
                compatibility_group,
                self._build_reconnect_group(),
            ],
            description=_("Higher image quality requires more processing power, memory, and connection speed. If either computer or the network cannot keep up, the stream may stutter or lag."),
            height=640,
        )
        self.image_dialog.connect("closed", lambda *_: self._sync_quality_summary())
        self.audio_dialog = preferences_dialog(
            _("Audio"),
            [audio_group, self._build_reconnect_group()],
            description=_("Choose the sound format used on this computer and whether sound should also play on the game PC."),
            height=440,
        )
        self.audio_dialog.connect("closed", lambda *_: self._sync_audio_summary())
        self.input_dialog = preferences_dialog(
            _("Input"),
            [input_group, controller_group, self._build_reconnect_group()],
            description=_("Adjust how the mouse, keyboard, touchscreen, and controllers behave while connected."),
            height=600,
        )
        self.host_connection_dialog = preferences_dialog(
            _("Game PC and connection"),
            [host_request_group, connection_group, self._build_reconnect_group()],
            description=_("These settings either send a request to the game PC or check the connection. The game PC can ignore requests it does not support."),
            height=500,
        )
        self.build_connection_dialogs()

        self.image_row = action_row(
            _("Image"),
            _("Checking..."),
            "brp-quality-symbolic",
            lambda: self.image_dialog.present(self),
        )
        self.audio_settings_row = action_row(
            _("Audio"),
            _("Stereo · This computer only"),
            "brp-audio-speakers-symbolic",
            lambda: self.audio_dialog.present(self),
        )
        self.input_settings_row = action_row(
            _("Input"),
            _("Mouse, keyboard, touchscreen, and controllers"),
            "brp-input-keyboard-symbolic",
            lambda: self.input_dialog.present(self),
        )
        self.host_connection_row = action_row(
            _("Game PC and connection"),
            _("Requests to the game PC and connection checks"),
            "brp-host-symbolic",
            lambda: self.host_connection_dialog.present(self),
        )
        self.address_row = action_row(
            _("I know the IP address"),
            _("Connect by address"),
            "brp-address-symbolic",
            lambda: self.present_other_ways("ip"),
        )
        self.search_code_row = action_row(
            _("I have a search code"),
            _("Find by code"),
            "brp-dialog-password-symbolic",
            lambda: self.present_other_ways("pin"),
        )
        methods = Adw.PreferencesGroup(title=_("Other ways to connect"))
        methods.add(self.search_code_row)
        methods.add(self.address_row)
        methods.add(
            action_row(
                _("Internet play"),
                _("Set up Private Network"),
                "brp-network-private-symbolic",
                self._go_to_private_network,
            )
        )
        settings = Adw.PreferencesGroup(title=_("On this computer"))
        for row in (self.image_row, self.audio_settings_row, self.input_settings_row, self.host_connection_row, help_row):
            settings.add(row)
        content.append(self.connect_card)
        content.append(methods)
        content.append(settings)
        self.load_guest_settings()
        self.connect_settings_signals()
        # The summary follows the real values, so the page says what the current
        # picture is without opening the sheet.
        for row, signal in (
            (self.resolution_row, "notify::selected"),
            (self.fps_row, "notify::selected"),
            (self.bitrate_scale, "value-changed"),
            (self.scale_row, "notify::active"),
        ):
            row.connect(signal, lambda *_: self._sync_quality_summary())
        self.audio_row.connect("notify::active", lambda *_: self._sync_audio_summary())
        self.audio_config_row.connect("notify::selected", lambda *_: self._sync_audio_summary())
        self.profile_row.connect("notify::selected", self._apply_profile)
        self._apply_auto_quality()
        self._sync_quality_summary()
        clamp.set_child(content)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(clamp)
        self.append(scroll)
        # Discovery is deliberately lazy, but the first render must already
        # explain how to make the other PC appear instead of showing a blank
        # list and a disabled Connect button until the periodic timer fires.
        self.update_hosts_list([])

    # resolution index, frame-rate index, Mbps, for the presets that follow
    # "Automatic" in the choice list (hence the +1 offset when reading it).
    _QUALITY_PROFILES = ((1, 1, 20.0), (0, 1, 8.0), (2, 1, 40.0), (3, 1, 60.0))
    _AUTOMATIC_PROFILE = 0
    _CUSTOM_PROFILE = 5

    def _screen_metrics(self) -> tuple[int, int, int]:
        """Pixel size and refresh rate of the screen this PC will play on."""
        try:
            display = Gdk.Display.get_default()
            monitor = None
            if native := self.get_native():
                if surface := native.get_surface():
                    monitor = display.get_monitor_at_surface(surface) if display is not None else None
            if monitor is None and display is not None:
                monitors = display.get_monitors()
                monitor = monitors.get_item(0) if monitors.get_n_items() else None
            if monitor is not None:
                area = monitor.get_geometry()
                scale = monitor.get_scale_factor() or 1
                # get_refresh_rate() is in milli-Hz.
                return area.width * scale, area.height * scale, round((monitor.get_refresh_rate() or 60000) / 1000)
        except Exception as exc:
            _log.debug(f"Cannot read screen metrics: {exc}")
        return 1920, 1080, 60

    def _apply_auto_quality(self, force: bool = False) -> None:
        """Write the values this screen and link imply, once per hardware change."""
        settings = self.config.get("guest", {})
        settings = settings if isinstance(settings, dict) else {}
        if not force and not settings.get("auto_quality", True):
            return

        width, height, refresh = self._screen_metrics()
        wireless = auto_quality.wireless_link()
        current_signature = auto_quality.signature(width, height, refresh, wireless)
        if not force and settings.get("auto_signature") == current_signature:
            self._auto_signature = current_signature
            return

        defaults = auto_quality.guest_defaults(width=width, height=height, refresh_hz=refresh, wireless=wireless)
        self._auto_signature = current_signature
        resolution = f"{defaults['width']}x{defaults['height']}"
        resolution_index = next((index for index, value in self._RESOLUTION_BY_INDEX.items() if value == resolution), 4)
        if resolution_index == 4:
            self.custom_resolution_val = resolution
        fps_index = {30: 0, 60: 1, 120: 2}.get(defaults["fps"], 1)
        self.loading_settings = True
        try:
            self.scale_row.set_active(True)
            self.resolution_row.set_selected(resolution_index)
            self.fps_row.set_selected(fps_index)
            self.bitrate_scale.set_value(defaults["bitrate_mbps"])
        finally:
            self.loading_settings = False
        self._last_res_idx, self._last_fps_idx = resolution_index, fps_index
        self.save_guest_settings()

    def _is_automatic(self) -> bool:
        settings = self.config.get("guest", {})
        if not isinstance(settings, dict):
            return True
        # Settings written before automatic existed are the user's own choices:
        # detection must not overwrite them on the first launch after an update.
        return bool(settings.get("auto_quality", not settings))

    def _set_automatic(self, automatic: bool) -> None:
        settings = self.config.get("guest", {})
        settings = settings if isinstance(settings, dict) else {}
        settings["auto_quality"] = automatic
        self.config.set("guest", settings)

    def _sync_quality_summary(self) -> None:
        if self.loading_settings or not hasattr(self, "profile_row"):
            return
        # Keep ComboRow's own selection label untouched. A subtitle is only
        # needed for custom values, and must be cleared for ordinary sizes.
        self.resolution_row.set_subtitle(self.custom_resolution_val if self.resolution_row.get_selected() == 4 else "")
        self.fps_row.set_subtitle(f"{self.custom_fps_val} FPS" if self.fps_row.get_selected() == 3 and self.custom_fps_val else "")
        self.custom_resolution_edit.set_visible(self.resolution_row.get_selected() == 4)
        self.custom_fps_edit.set_visible(self.fps_row.get_selected() == 3)
        summary = self._quality_summary()
        explanations = {
            self._AUTOMATIC_PROFILE: _("Uses this screen at connection time; it does not measure internet speed."),
            1: _("1080p · 60 FPS · 20 Mbps"),
            2: _("720p · 60 FPS · 8 Mbps"),
            3: _("1440p · 60 FPS · 40 Mbps"),
            4: _("4K · 60 FPS · 60 Mbps"),
            self._CUSTOM_PROFILE: _("Choose resolution, frame rate and video data rate below."),
        }
        self.profile_group.set_description(explanations.get(self.profile_row.get_selected(), summary))
        if hasattr(self, "image_row"):
            self.image_row.set_subtitle(summary)
        settings = self.config.get("guest", {})
        settings = settings if isinstance(settings, dict) else {}
        if self._is_automatic():
            index = self._AUTOMATIC_PROFILE
        else:
            index = settings.get("quality_profile")
            if type(index) is not int or not 1 <= index <= self._CUSTOM_PROFILE:
                current = (self.resolution_row.get_selected(), self.fps_row.get_selected(), self.bitrate_scale.get_value())
                index = self._QUALITY_PROFILES.index(current) + 1 if current in self._QUALITY_PROFILES and not self.scale_row.get_active() else self._CUSTOM_PROFILE
        self._updating_profile = True
        try:
            self.profile_row.set_selected(index)
        finally:
            self._updating_profile = False
        self.custom_picture_group.set_visible(index == self._CUSTOM_PROFILE)
        self.profile_group.set_description(explanations[index])
        self._sync_audio_summary()

    def _apply_profile(self, *_args) -> None:
        if self.loading_settings or getattr(self, "_updating_profile", False):
            return
        index = self.profile_row.get_selected()
        if not 0 <= index <= self._CUSTOM_PROFILE:
            return
        settings = self.config.get("guest", {})
        settings = dict(settings) if isinstance(settings, dict) else {}
        settings["quality_profile"] = index
        self.config.set("guest", settings)
        if index == self._AUTOMATIC_PROFILE:
            self._set_automatic(True)
            self._apply_auto_quality(force=True)
            self._sync_quality_summary()
            return
        if index == self._CUSTOM_PROFILE:
            self._set_automatic(False)
            self._sync_quality_summary()
            return
        preset = index - 1
        if not 0 <= preset < len(self._QUALITY_PROFILES):
            return
        resolution, fps, bitrate = self._QUALITY_PROFILES[preset]
        self._set_automatic(False)
        self.loading_settings = True
        try:
            self.scale_row.set_active(False)
            self.resolution_row.set_selected(resolution)
            self.fps_row.set_selected(fps)
            self.bitrate_scale.set_value(bitrate)
        finally:
            self.loading_settings = False
        self._last_res_idx, self._last_fps_idx = resolution, fps
        self.save_guest_settings()
        self._sync_quality_summary()

    def monitor_connection(self):
        """Monitors Moonlight connection state"""
        if hasattr(self, "moonlight"):
            is_running = self.moonlight.is_connected()

            if is_running:
                if not self.is_connected:
                    # Update state if it was disconnected
                    self.is_connected = True
                    host_name = self.moonlight.connected_host if self.moonlight.connected_host else "Host"
                    self.perf_monitor.set_connection_status(host_name, _("Active Session"), True)

                    self.perf_monitor.set_visible(True)
                    self.perf_monitor.start_monitoring()

            else:
                if self.is_connected:
                    # Detected disconnection
                    self.is_connected = False
                    self.perf_monitor.set_connection_status("None", _("Disconnected"), False)
                    self.perf_monitor.stop_monitoring()
                    self.perf_monitor.set_visible(False)

                    self.show_toast(_("Moonlight closed"))

            # Update UI visibility
            self.update_ui_state()

        return True  # Continue polling

    def update_ui_state(self):
        c = self.is_connected
        # The card must remain visible so the "Stop" button is accessible
        if hasattr(self, "connect_card"):
            self.connect_card.set_visible(True)
        for button in getattr(self, "apply_settings_buttons", []):
            button.set_visible(c)

        # Update connection buttons state
        self._update_all_buttons_state()

    def _update_all_buttons_state(self):
        is_connecting = getattr(self, "is_connecting", False)
        connected = self.is_connected
        if self._stopping:
            for name in ("main_connect_btn", "manual_connect_btn", "pin_connect_btn"):
                button = getattr(self, name, None)
                if button is not None:
                    button.set_sensitive(False)
            return

        # Helper to update a button
        def update_btn(btn, label_widget, spinner, default_text, default_sensitive=True):
            button = getattr(self, btn, None)
            label = getattr(self, label_widget, None)
            progress = getattr(self, spinner, None)
            if button is None or label is None or progress is None:
                return  # construction or teardown: no half-built state update
            active = connected or is_connecting
            button.set_sensitive(True if active else default_sensitive)
            button.remove_css_class("suggested-action" if active else "destructive-action")
            button.add_css_class("destructive-action" if active else "suggested-action")
            label.set_label(_("Stop") if active else default_text)
            progress.set_visible(is_connecting and not connected)
            if is_connecting and not connected:
                progress.start()
            else:
                progress.stop()

        # Update Discover Button
        # Logic specific for discover: only sensitive if host selected (when disconnected)
        selected_host = self.selected_host_card_data
        has_host = selected_host is not None
        update_btn(
            "main_connect_btn",
            "connect_btn_label",
            "connect_btn_spinner",
            _("Connect"),
            default_sensitive=has_host,
        )

        if hasattr(self, "main_connect_btn"):
            # Keep the visible action short. The selected row and accessible
            # description identify the destination without repeating a long name.
            target = _("Connect to {}").format(selected_host["name"]) if selected_host else _("Pick the PC that is sharing the game.")
            if connected or is_connecting:
                target = _("Stop")
            self.main_connect_btn.update_property([Gtk.AccessibleProperty.DESCRIPTION], [target])

        # Update Manual Button
        update_btn("manual_connect_btn", "manual_btn_label", "manual_btn_spinner", _("Connect"))

        # Update PIN Button
        update_btn("pin_connect_btn", "pin_btn_label", "pin_btn_spinner", _("Find by code"))

    def check_reconnect(self):
        if self.is_connected and hasattr(self, "current_host_ctx"):
            self.show_toast(_("Applying settings..."))
            ctx = self.current_host_ctx
            if self.is_connected:
                self.moonlight.disconnect()
            if ctx["type"] == "auto":
                self.connect_to_host(ctx["host"])
            elif ctx["type"] == "manual":
                self.connect_manual(ctx["ip"], str(ctx["port"]), ctx["ipv6"])

    def check_reconnect_debounced(self):
        """Checks if reconnection is needed (with debounce)"""
        # Cancel previous
        if hasattr(self, "_reconnect_timer") and self._reconnect_timer:
            GLib.source_remove(self._reconnect_timer)

        self._reconnect_timer = GLib.timeout_add(1000, self._do_reconnect_timer)

    def _do_reconnect_timer(self):
        self._reconnect_timer = None
        self.check_reconnect()
        return False

    def _go_to_private_network(self) -> None:
        root = self._root_window()
        setup = getattr(root, "_go_to_private_network_setup", None)
        if callable(setup):
            setup()

    def _build_discover_empty_state(self) -> Gtk.Widget:
        """Status page plus a short list of the ways out.

        A wall of explanatory cards asked a non-technical person to read three
        paragraphs before their first click. The page now states the situation
        in one line, offers the retry, and lists the alternatives as rows they
        can scan in a second."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        empty.add_css_class("brp-empty")
        # The status sentence is the primary signal.  A large decorative search
        # icon previously pushed it below the fold and could overlap it under
        # large-text settings, so the empty state is intentionally text-first.
        title = Gtk.Label(label=_("No game PC found yet"), wrap=True, xalign=0.5)
        title.add_css_class("title-3")
        empty.append(title)
        description = Gtk.Label(label=_("Start sharing on the game PC, then search again."), wrap=True, justify=Gtk.Justification.CENTER)
        description.add_css_class("dim-label")
        empty.append(description)
        retry = Gtk.Button(label=_("Search again"), halign=Gtk.Align.CENTER)
        retry.add_css_class("suggested-action")
        retry.connect("clicked", lambda _button: self.discover_hosts())
        empty.append(retry)
        box.append(empty)

        return box

    def create_discover_page(self):
        self.selected_host_card_data = None
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        for m in ["top", "bottom", "start", "end"]:
            getattr(header, f"set_margin_{m}")(12)
        lbl = Gtk.Label(label=_("Choose a computer"))
        lbl.add_css_class("title-2")
        lbl.set_halign(Gtk.Align.START)
        desc = Gtk.Label(label=_("Start sharing on the game PC. It will appear here."))
        desc.add_css_class("dim-label")
        desc.set_halign(Gtk.Align.START)
        desc.set_wrap(True)
        desc.set_xalign(0)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_hexpand(True)
        text_box.append(lbl)
        text_box.append(desc)

        refresh = Gtk.Button(icon_name="brp-view-refresh-symbolic")
        refresh.add_css_class("flat")
        refresh.set_tooltip_text(_("Search again"))
        refresh.update_property([Gtk.AccessibleProperty.LABEL], [_("Search for game PCs again")])
        refresh.connect("clicked", lambda b: self.discover_hosts())
        header.append(text_box)
        header.append(refresh)
        self.hosts_list = Gtk.ListBox()
        self.hosts_list.add_css_class("boxed-list")
        self.hosts_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.hosts_list.set_activate_on_single_click(True)
        self.hosts_list.connect("row-selected", self._on_host_row_selected)
        for m in ["start", "end"]:
            getattr(self.hosts_list, f"set_margin_{m}")(12)
        # Top/bottom margin so the boxed-list card's rounded corners and the
        # first/last rows are not clipped by the ScrolledWindow viewport edge.
        self.hosts_list.set_margin_top(6)
        self.hosts_list.set_margin_bottom(6)
        action = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ["top", "bottom", "start", "end"]:
            getattr(action, f"set_margin_{m}")(12)

        buttons_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        buttons_box.set_halign(Gtk.Align.CENTER)

        self.main_connect_btn = Gtk.Button()
        self.main_connect_btn.add_css_class("suggested-action")
        self.main_connect_btn.set_size_request(250, 50)
        self.main_connect_btn.set_sensitive(False)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_box.set_halign(Gtk.Align.CENTER)
        self.connect_btn_spinner = Gtk.Spinner()
        self.connect_btn_spinner.set_visible(False)
        self.connect_btn_label = Gtk.Label(label=_("Connect"), wrap=True, max_width_chars=26)
        btn_box.append(self.connect_btn_spinner)
        btn_box.append(self.connect_btn_label)
        self.main_connect_btn.set_child(btn_box)

        self.main_connect_btn.connect("clicked", lambda b: self.on_main_button_clicked("discover"))

        buttons_box.append(self.main_connect_btn)

        self._host_scroll = Gtk.ScrolledWindow()
        self._host_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._host_scroll.set_max_content_height(400)
        self._host_scroll.set_min_content_height(120)
        self._host_scroll.set_vexpand(False)
        self._host_scroll.set_propagate_natural_height(True)
        self._host_scroll.set_child(self.hosts_list)

        # Empty state lives OUTSIDE the height-capped scroller, so its taller
        # guidance card is never clipped (the scroller is only for host rows).
        self._empty_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._empty_container.set_visible(False)

        action.append(buttons_box)
        self._discover_action = action
        box.append(header)
        box.append(self._host_scroll)
        box.append(self._empty_container)
        box.append(action)
        box.set_hexpand(True)
        return box

    def _on_mapped(self, _view: Gtk.Widget) -> None:
        # A map signal may run before get_mapped() changes. Defer once to the
        # main loop, and never reuse the repeating timeout's True return value
        # as an idle result (which would create a continuous discovery loop).
        def discover_once() -> bool:
            self._auto_discover()
            return False

        GLib.idle_add(discover_once)

    def _auto_discover(self) -> bool:
        """Keep looking while the page is on screen, so the other PC shows up
        by itself once sharing starts there."""
        if getattr(self, "_closed", False):
            return False
        busy = getattr(self, "is_connecting", False) or self.is_connected
        if self.get_mapped() and not busy:
            self.discover_hosts(silent=True)
        return True

    def discover_hosts(self, silent: bool = False):
        from big_remote_play.utils.network import NetworkDiscovery

        if getattr(self, "_discovery_running", False) or self.is_connected or getattr(self, "is_connecting", False) or self._closed:
            return
        self._discovery_running = True

        if silent:
            # A background refresh must not steal the selection or blank the
            # list the person is reading.
            def on_silent_result(hosts):
                self._discovery_running = False
                if not self._closed and not self.is_connected and not getattr(self, "is_connecting", False):
                    self.update_hosts_list(hosts, keep_selection=True)
                return False

            NetworkDiscovery().discover_hosts(callback=on_silent_result, allow_scan=False)
            return

        self.selected_host_card_data = None
        self._update_all_buttons_state()
        # Scanning shows the spinner in the list scroller, not the empty state.
        if hasattr(self, "_empty_container"):
            self._empty_container.set_visible(False)
            self._host_scroll.set_visible(True)
            self._discover_action.set_visible(True)
        while row := self.hosts_list.get_row_at_index(0):
            self.hosts_list.remove(row)
        self.loading_row = Gtk.ListBoxRow()
        self.loading_row.set_selectable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        box.set_size_request(-1, 150)
        for m in ["top", "bottom"]:
            getattr(box, f"set_margin_{m}")(24)
        spinner = Gtk.Spinner()
        spinner.set_size_request(48, 48)
        spinner.start()
        lbl = Gtk.Label(label=_("Searching for game PCs..."))
        lbl.add_css_class("title-2")
        box.append(spinner)
        box.append(lbl)
        self.loading_row.set_child(box)
        self.hosts_list.append(self.loading_row)

        def on_hosts_discovered(hosts):
            self._discovery_running = False
            if self._closed or self.is_connected or getattr(self, "is_connecting", False):
                return False
            if self.loading_row.get_parent():
                self.hosts_list.remove(self.loading_row)
            self.update_hosts_list(hosts)
            return False

        NetworkDiscovery().discover_hosts(callback=on_hosts_discovered)

    def update_hosts_list(self, hosts, keep_selection: bool = False):
        previous = self.selected_host_card_data if keep_selection else None
        if keep_selection and (hosts or self._empty_container.get_visible()):
            listed = [getattr(self.hosts_list.get_row_at_index(index), "_brp_host", None) for index in range(len(hosts) + 1)]
            if hosts == [entry for entry in listed if entry]:
                return  # nothing changed; leave the list and the selection alone

        self.selected_host_card_data = None
        self._update_all_buttons_state()

        while True:
            row = self.hosts_list.get_row_at_index(0)
            if row is None:
                break
            self.hosts_list.remove(row)

        if not hosts:
            # Show the uncapped guidance card; hide the list scroller and the
            # connect button, which only make sense with hosts.
            while child := self._empty_container.get_first_child():
                self._empty_container.remove(child)
            self._empty_container.append(self._build_discover_empty_state())
            self._empty_container.set_visible(True)
            self._host_scroll.set_visible(False)
            self._discover_action.set_visible(False)
            return

        self._empty_container.set_visible(False)
        self._host_scroll.set_visible(True)
        self._discover_action.set_visible(True)
        first_row = None
        keep_row = None
        for host in hosts:
            row = self.create_host_row_custom(host)
            self.hosts_list.append(row)
            if first_row is None:
                first_row = row
            if previous is not None and host["ip"] == previous.get("ip"):
                keep_row = row
        # Discovery normally returns one obvious target. Preselecting the first
        # result removes an unnecessary click while preserving keyboard choice;
        # a background refresh keeps whatever the person had chosen.
        selected = keep_row or first_row
        if selected is not None:
            self.hosts_list.select_row(selected)

    def _on_host_row_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        self.selected_host_card_data = getattr(row, "_brp_host", None) if row is not None else None
        self._update_all_buttons_state()

    def create_host_row_custom(self, host):
        name = str(host.get("name") or host["ip"])
        row = Adw.ActionRow(title=name, subtitle=str(host["ip"]), use_markup=False, activatable=True)
        row.set_title_lines(2)
        row.set_subtitle_lines(1)
        row._brp_host = host
        row.add_prefix(icon_tile("brp-computer-symbolic"))
        row.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            [name, _("Available at {}").format(host["ip"])],
        )
        row.connect("activated", lambda selected_row: self.hosts_list.select_row(selected_row))
        return row

    def build_connection_dialogs(self) -> None:
        """Each fallback owns its fields; closing one never clears the other."""
        self._address_dialog = content_dialog(
            _("Connect by address"),
            self.create_manual_page(),
            height=420,
        )
        self._search_code_dialog = content_dialog(
            _("Find by code"),
            self.create_pin_page(),
            height=420,
        )

    def present_other_ways(self, focus: str = "ip") -> None:
        """Open only the task requested by the action row."""
        if focus == "pin":
            dialog, entry = self._search_code_dialog, self.pin_entry
        else:
            dialog, entry = self._address_dialog, self.manual_ip_entry
        dialog.present(self)
        entry.grab_focus()

    def close_other_ways(self) -> None:
        for name in ("_address_dialog", "_search_code_dialog"):
            dialog = getattr(self, name, None)
            if dialog is not None:
                dialog.close()

    def create_manual_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        grp = Adw.PreferencesGroup()
        grp.set_description(_("Use this only when automatic discovery cannot find the PC."))

        ip = Adw.EntryRow()
        ip.set_title(_("IP address or computer name"))
        ip.set_tooltip_text(_("For example: 192.168.1.20 or gaming-pc.local"))

        port = Adw.EntryRow()
        port.set_title(_("Port"))
        port.set_text("47989")

        ipv6 = Adw.SwitchRow()
        ipv6.set_title(_("Use IPv6"))

        self.manual_ip_entry = ip
        self.manual_port_entry = port
        self.manual_ipv6_switch = ipv6

        grp.add(ip)
        # Named for what it holds: "Advanced Settings" already names the host
        # sheet and the Moonlight page, and this one is two fields.
        advanced = Adw.ExpanderRow(title=_("Port and IPv6"))
        advanced.add_row(port)
        advanced.add_row(ipv6)
        grp.add(advanced)

        box.append(grp)

        buttons_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        buttons_box.set_halign(Gtk.Align.CENTER)

        self.manual_connect_btn = Gtk.Button()
        self.manual_connect_btn.add_css_class("suggested-action")
        self.manual_connect_btn.set_size_request(200, 50)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_box.set_halign(Gtk.Align.CENTER)
        self.manual_btn_spinner = Gtk.Spinner()
        self.manual_btn_spinner.set_visible(False)
        self.manual_btn_label = Gtk.Label(label=_("Connect"))
        btn_box.append(self.manual_btn_spinner)
        btn_box.append(self.manual_btn_label)
        self.manual_connect_btn.set_child(btn_box)

        self.manual_connect_btn.connect("clicked", lambda b: self.on_main_button_clicked("manual"))
        ip.connect("entry-activated", lambda _entry: self.on_main_button_clicked("manual"))

        buttons_box.append(self.manual_connect_btn)

        box.append(buttons_box)
        return box

    def create_pin_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        grp = Adw.PreferencesGroup()
        grp.set_description(_("Use the search code in Big Remote Play on the game PC."))

        pin = Adw.EntryRow()
        pin.set_title(_("Search code"))
        pin.set_input_purpose(Gtk.InputPurpose.DIGITS)
        pin.update_property(
            [Gtk.AccessibleProperty.DESCRIPTION],
            [_("Enter exactly four digits.")],
        )
        pin.add_css_class("brp-code-entry")
        self.pin_entry = pin

        grp.add(pin)

        box.append(grp)
        # The caveat belongs under the field it qualifies, not above its heading.
        box.append(note(_("This only finds the computer; it does not pair it. Use an address if the network blocks discovery.")))

        buttons_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        buttons_box.set_halign(Gtk.Align.CENTER)

        self.pin_connect_btn = Gtk.Button()
        self.pin_connect_btn.add_css_class("suggested-action")
        self.pin_connect_btn.set_size_request(200, 50)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_box.set_halign(Gtk.Align.CENTER)
        self.pin_btn_spinner = Gtk.Spinner()
        self.pin_btn_spinner.set_visible(False)
        self.pin_btn_label = Gtk.Label(label=_("Find by code"))
        btn_box.append(self.pin_btn_spinner)
        btn_box.append(self.pin_btn_label)
        self.pin_connect_btn.set_child(btn_box)

        self.pin_connect_btn.connect("clicked", lambda b: self.on_main_button_clicked("pin"))
        pin.connect("entry-activated", lambda _entry: self.on_main_button_clicked("pin"))

        buttons_box.append(self.pin_connect_btn)

        box.append(buttons_box)
        return box

    def on_main_button_clicked(self, source):
        # If connecting (loading) or connected, button becomes "Stop"
        if getattr(self, "is_connecting", False) or self.is_connected:
            self.on_cancel_connection(None)
        else:
            # Start connection based on source
            if source == "discover":
                if self.selected_host_card_data:
                    self.connect_to_host(dict(self.selected_host_card_data))
            elif source == "manual":
                self.connect_manual(self.manual_ip_entry.get_text(), self.manual_port_entry.get_text(), self.manual_ipv6_switch.get_active())
            elif source == "pin":
                self.connect_pin(self.pin_entry.get_text())

    _RESOLUTION_BY_INDEX = {0: "1280x720", 1: "1920x1080", 2: "2560x1440", 3: "3840x2160"}
    _FPS_BY_INDEX = {0: "30", 1: "60", 2: "120"}

    def _wait_until_paired(self, host_ip: str, retries: int, port: int = 47989) -> bool:
        """Poll Moonlight until the host reports as paired, up to `retries` times."""
        for attempt in range(retries):
            if bool(self.moonlight.list_apps(host_ip, port=port)):
                return True
            if attempt < retries - 1:
                time.sleep(1.0)  # allow the host to sync after a fresh pairing
        return False

    def _attempt_valid(self, attempt: int) -> bool:
        return not self._closed and attempt == self._attempt_id

    def connect_to_host(self, host, paired_retry=False, override_check=False):
        if self._stopping or self._closed:
            return
        if getattr(self, "is_connecting", False) and not paired_retry and not override_check:
            return
        if not paired_retry and not override_check:
            self._attempt_id += 1
            self._attempt_cancel = threading.Event()
        attempt = self._attempt_id
        cancel_event = self._attempt_cancel
        self.close_other_ways()
        fps_idx = self.fps_row.get_selected()
        if self.scale_row.get_active():
            res = self.get_auto_resolution()
        else:
            res_idx = self.resolution_row.get_selected()
            res = (self.custom_resolution_val or "1920x1080") if res_idx == 4 else self._RESOLUTION_BY_INDEX.get(res_idx, "1920x1080")
        width, height = res.split("x") if "x" in res else ("1920", "1080")
        fps = (self.custom_fps_val or "60") if fps_idx == 3 else self._FPS_BY_INDEX.get(fps_idx, "60")
        opts = {
            "cancel_event": cancel_event,
            "port": host.get("port", 47989),
            "width": width,
            "height": height,
            "fps": fps,
            "bitrate": int(self.bitrate_scale.get_value() * 1000),
            "display_mode": ["borderless", "fullscreen", "windowed"][self.display_mode_row.get_selected()],
            "play_audio_on_host": self.audio_row.get_active(),
            "hw_decode": self.hw_decode_row.get_active(),
        }
        self.perf_monitor.set_target_fps(float(fps))
        self.perf_monitor.set_target_bandwidth(opts["bitrate"] / 1000)
        self.current_host_ctx = {"type": "auto", "host": dict(host)}
        self.show_loading(True)

        def start_pairing(resolved_host):
            if self._attempt_valid(attempt):
                self.start_pairing_flow(resolved_host, attempt=attempt)
            return False

        def failed(message):
            if self._attempt_valid(attempt):
                self.show_loading(False)
                self.show_error_dialog(_("Could not connect"), message)
            return False

        def succeeded():
            if self._attempt_valid(attempt):
                self.show_loading(False)
                self.perf_monitor.set_connection_status(host["name"], _("Active Stream"), True)
                self.perf_monitor.start_monitoring()
            return False

        def run(host=dict(host)):
            try:
                if not paired_retry:
                    apps = []
                    candidates = list(dict.fromkeys([host["ip"], *host.get("addresses", [])]))
                    for address in candidates[:3]:
                        if not self._attempt_valid(attempt):
                            return
                        apps = self.moonlight.list_apps(address, port=opts["port"])
                        if apps is None or apps:
                            host = dict(host, ip=address)
                            break
                    if not self._attempt_valid(attempt):
                        return
                    if apps is None:
                        GLib.idle_add(start_pairing, host)
                        return
                    if not apps:
                        GLib.idle_add(failed, _("Check that sharing is running on the game PC and that both computers can reach each other."))
                        return
                if not self._attempt_valid(attempt):
                    return
                ok = self.moonlight.connect(host["ip"], **opts)
                if not self._attempt_valid(attempt):
                    # A cancellation during process creation must stop that
                    # process too; the UI blocks new starts until this settles.
                    self.moonlight.disconnect()
                    return
                if ok:
                    GLib.idle_add(succeeded)
                else:
                    GLib.idle_add(failed, _("Failed to connect. Verify if Moonlight is paired."))
            except Exception as exc:
                _log.warning("Connection attempt failed: %s", exc)
                GLib.idle_add(failed, _("Check that sharing is running on the game PC and that both computers can reach each other."))

        self._connect_thread = threading.Thread(target=run, daemon=True)
        self._connect_thread.start()

    def start_pairing_flow(self, host, attempt=None):
        """Show the upstream pairing code, without touching GTK in the worker."""
        if attempt is None:
            attempt = self._attempt_id
        if not self._attempt_valid(attempt):
            return
        self.show_loading(True)
        cancel_event = self._attempt_cancel

        def on_pin_callback(pin):
            def show():
                if self._attempt_valid(attempt):
                    self.show_pairing_dialog(host["ip"], pin, hostname=host.get("name"))
                return False

            GLib.idle_add(show)

        def do_pair():
            if not self._attempt_valid(attempt):
                return
            success = self.moonlight.pair(host["ip"], on_pin_callback=on_pin_callback, port=host.get("port", 47989), cancel_event=cancel_event)

            def finished():
                if not self._attempt_valid(attempt):
                    return False
                self.close_pairing_dialog()
                if success:
                    self.show_toast(_("Paired successfully!"))
                    self.connect_to_host(host, paired_retry=True)
                else:
                    self.show_loading(False)
                    self.show_error_dialog(_("Pairing Error"), _("Could not pair with the game PC.\nCheck that the PIN was entered correctly."))
                return False

            GLib.idle_add(finished)

        self._pair_thread = threading.Thread(target=do_pair, daemon=True)
        self._pair_thread.start()

    def show_loading(self, show=True, message=""):
        self.is_connecting = show
        self._update_all_buttons_state()

    def on_cancel_connection(self, btn):
        self._attempt_cancel.set()
        self._attempt_id += 1
        self._stopping = True
        self.show_loading(False)
        self.close_pairing_dialog()
        self.main_connect_btn.set_sensitive(False)

        def stop():
            self.moonlight.disconnect()
            # Wait for a process being launched concurrently before permitting
            # another attempt. The old attempt cannot stop a newer one.
            for attr in ("_connect_thread", "_pair_thread"):
                thread = getattr(self, attr, None)
                if thread is not None:
                    thread.join(timeout=6)

            def finish():
                self._stopping = False
                if not self._closed:
                    self._update_all_buttons_state()
                return False

            GLib.idle_add(finish)

        threading.Thread(target=stop, daemon=True).start()

    def show_pairing_dialog(self, host_ip, pin=None, on_confirm=None, hostname=None):
        self.close_pairing_dialog()
        body = _("On the game PC:\n1. Open Share my game.\n2. Enter this code under Connect the other PC.\n3. Choose Pair.\n\nKeep this window open. Connection continues after approval.")
        dialog = Adw.AlertDialog(heading=_("Approve on the game PC"), body=body)
        dialog.set_body_use_markup(False)
        code = Gtk.Label(label=str(pin or ""), selectable=True, xalign=0.5)
        code.add_css_class("title-1")
        code.update_property([Gtk.AccessibleProperty.LABEL], [_("Pairing code: {}").format(pin or "")])
        dialog.set_extra_child(code)
        dialog.add_response("cancel", _("Cancel"))
        dialog.set_close_response("cancel")
        self.pairing_dialog = dialog

        def on_response(_dialog, _response):
            # Programmatic completion clears the reference before closing.
            if self.pairing_dialog is dialog:
                self.pairing_dialog = None
                self.on_cancel_connection(None)

        dialog.connect("response", on_response)
        dialog.present(self)

    def close_pairing_dialog(self):
        dialog = getattr(self, "pairing_dialog", None)
        self.pairing_dialog = None
        if dialog is not None:
            dialog.close()

    def get_auto_resolution(self):
        try:
            display = Gdk.Display.get_default()
            if display is None:
                return "1920x1080"
            monitor = None
            if native := self.get_native():
                if surface := native.get_surface():
                    monitor = display.get_monitor_at_surface(surface)
            if not monitor:
                monitors = display.get_monitors()
                if monitors.get_n_items() > 0:
                    monitor = monitors.get_item(0)
            if monitor:
                r = monitor.get_geometry()
                scale = monitor.get_scale_factor() or 1
                return f"{r.width * scale}x{r.height * scale}"
        except Exception:
            pass
        return "1920x1080"

    def connect_manual(self, ip, port, ipv6=False):
        ip = str(ip).strip()
        if not ip or any(char.isspace() for char in ip) or "/" in ip or ip.startswith("-"):
            self.show_error_dialog(_("Invalid address"), _("Enter a hostname or IP address, without a URL or spaces."))
            self.manual_ip_entry.grab_focus()
            return
        try:
            port_number = int(str(port).strip() or "47989")
            if not 1 <= port_number <= 65535:
                raise ValueError
        except ValueError:
            self.show_error_dialog(_("Invalid port"), _("Use a number between 1 and 65535."))
            return
        port = str(port_number)
        self.current_host_ctx = {"type": "manual", "ip": ip, "port": port, "ipv6": ipv6}
        self.connect_to_host({"name": ip, "ip": ip, "port": int(port) if port else 47989})

    def connect_pin(self, _widget):
        # Reverse Connection via PIN (Custom Feature)
        pin = self.pin_entry.get_text().strip()
        if len(pin) != BRP_DISCOVERY_CODE_LENGTH or not pin.isascii() or not pin.isdigit():
            self.show_error_dialog(_("Invalid code"), _("Enter exactly four digits."))
            self.pin_entry.grab_focus()
            return

        if self._stopping or getattr(self, "is_connecting", False):
            return
        self._attempt_id += 1
        self._attempt_cancel = threading.Event()
        attempt = self._attempt_id
        self.show_loading(True)
        # 1. Resolve PIN to IP via Utils
        from big_remote_play.utils.network import resolve_pin_to_ip

        def run_resolve():
            res = resolve_pin_to_ip(pin)

            def finished():
                if self._attempt_valid(attempt):
                    if res:
                        self._on_pin_resolved(res, pin)
                    else:
                        self._on_pin_failed()
                return False

            GLib.idle_add(finished)

        threading.Thread(target=run_resolve, daemon=True).start()

    def _on_pin_resolved(self, ip_info, pin):
        if not getattr(self, "is_connecting", False):
            return
        ip = ip_info.get("ip")
        port = ip_info.get("port", 47989)
        hostname = ip_info.get("hostname", "Host")

        self.show_toast(_("Game PC found: {}").format(hostname))
        self.connect_to_host({"name": hostname, "ip": ip, "port": port}, override_check=True)

    def _on_pin_failed(self):
        if not getattr(self, "is_connecting", False):
            return
        self.show_loading(False)
        self.show_error_dialog(
            _("Game PC not found"),
            _("No computer answered this search code. Check the code or connect using its address."),
        )

    def show_error_dialog(self, title, message):
        dialog = Adw.AlertDialog(heading=title, body=message)
        dialog.add_response("ok", _("OK"))
        dialog.present(self)

    _RESOLUTION_RE = re.compile(r"^\s*(\d{3,5})\s*[xX]\s*(\d{3,5})\s*$")

    def on_resolution_changed(self, row, _pspec):
        if getattr(self, "loading_settings", False):
            return
        item = row.get_selected_item()
        val = item.get_string() if item is not None else ""
        if val != _("Custom"):
            self._last_res_idx = row.get_selected()
            self.save_guest_settings()
            return

        def apply_custom(text: str) -> None:
            match = self._RESOLUTION_RE.match(text)
            if not match:
                self.show_error_dialog(_("Invalid Resolution"), _("Use the form WIDTHxHEIGHT, e.g. 1920x1080."))
                self.resolution_row.set_selected(self._last_res_idx)
                return
            self.custom_resolution_val = f"{int(match.group(1))}x{int(match.group(2))}"
            self._last_res_idx = row.get_selected()
            self.save_guest_settings()
            self._sync_quality_summary()

        self.show_custom_input_dialog(
            _("Custom Resolution"),
            _("Enter WIDTHxHEIGHT (e.g. 1920x1080):"),
            apply_custom,
            on_cancel=lambda: self.resolution_row.set_selected(self._last_res_idx),
            initial=getattr(self, "custom_resolution_val", "") or "",
        )

    def on_fps_changed(self, row, _pspec):
        if getattr(self, "loading_settings", False):
            return
        item = row.get_selected_item()
        val = item.get_string() if item is not None else ""
        if val != _("Custom"):
            self._last_fps_idx = row.get_selected()
            self.save_guest_settings()
            return

        def apply_custom(text: str) -> None:
            digits = text.strip()
            if not digits.isdigit() or not (1 <= int(digits) <= 480):
                self.show_error_dialog(_("Invalid Frame Rate"), _("Enter a whole number of frames per second (1–480)."))
                self.fps_row.set_selected(self._last_fps_idx)
                return
            self.custom_fps_val = str(int(digits))
            self._last_fps_idx = row.get_selected()
            self.save_guest_settings()
            self._sync_quality_summary()

        self.show_custom_input_dialog(
            _("Custom Frame Rate"),
            _("Enter frames per second (e.g. 75):"),
            apply_custom,
            on_cancel=lambda: self.fps_row.set_selected(self._last_fps_idx),
            initial=getattr(self, "custom_fps_val", "") or "",
        )

    def _on_bitrate_limit_changed(self, *_args) -> None:
        upper = 500.0 if self.unlock_bitrate_row.get_active() else 150.0
        self.bitrate_scale.get_adjustment().set_upper(upper)
        if self.bitrate_scale.get_value() > upper:
            self.bitrate_scale.set_value(upper)

    def on_scale_changed(self, row, _param):
        self.resolution_row.set_sensitive(not row.get_active())
        if self.loading_settings:
            return
        if row.get_active():
            self.show_toast(_("Using this screen's resolution"))
        self.save_guest_settings()

    def show_custom_input_dialog(self, title, subtitle, callback, on_cancel=None, initial=""):
        dialog = Adw.AlertDialog(heading=title, body=subtitle)

        grp = Adw.PreferencesGroup()
        entry = Adw.EntryRow(title=_("Value"))
        if initial:
            entry.set_text(initial)
        grp.add(entry)
        dialog.set_extra_child(grp)

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("ok", _("Apply"))
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("ok")
        # Any dismissal (Esc / close) resolves as "cancel" so callers can revert.
        dialog.set_close_response("cancel")

        def on_response(d, r):
            if r == "ok":
                callback(entry.get_text())
            elif on_cancel is not None:
                on_cancel()

        dialog.connect("response", on_response)
        dialog.present(self)

    def cleanup(self):
        self._closed = True
        self._attempt_cancel.set()
        self._attempt_id += 1
        for attribute in ("_connection_timer", "_discovery_timer"):
            source = getattr(self, attribute, None)
            if source:
                GLib.source_remove(source)
                setattr(self, attribute, None)
        (self.perf_monitor.stop_monitoring() if hasattr(self, "perf_monitor") else None)

    def connect_settings_signals(self):
        self.bitrate_scale.connect("value-changed", lambda w: self.save_guest_settings())
        for r in [self.display_mode_row, self.audio_row, self.hw_decode_row]:
            r.connect("notify::selected-item" if isinstance(r, Adw.ComboRow) else "notify::active", lambda *x: self.save_guest_settings())
        # A value changed by hand owns the settings from then on: automatic must
        # not overwrite it on the next launch.
        for row, signal in (
            (self.resolution_row, "notify::selected"),
            (self.fps_row, "notify::selected"),
            (self.scale_row, "notify::active"),
            (self.bitrate_scale, "value-changed"),
        ):
            row.connect(signal, self._on_manual_quality_change)

    def _on_manual_quality_change(self, *_args) -> None:
        if self.loading_settings or getattr(self, "_updating_profile", False):
            return
        settings = self.config.get("guest", {})
        settings = dict(settings) if isinstance(settings, dict) else {}
        settings.update({"auto_quality": False, "quality_profile": self._CUSTOM_PROFILE})
        self.config.set("guest", settings)
        self._sync_quality_summary()

    def save_guest_settings(self):
        if getattr(self, "loading_settings", False):
            return
        # 1. Save to Moonlight.conf (Global Sync)

        # Resolution
        idx = self.resolution_row.get_selected()
        w, h = "1920", "1080"
        if idx == 0:
            w, h = "1280", "720"
        elif idx == 1:
            w, h = "1920", "1080"
        elif idx == 2:
            w, h = "2560", "1440"
        elif idx == 3:
            w, h = "3840", "2160"
        elif idx == 4:
            if hasattr(self, "custom_resolution_val") and "x" in str(self.custom_resolution_val):
                parts = str(self.custom_resolution_val).split("x")
                if len(parts) >= 2:
                    w, h = parts[0], parts[1]

        # FPS
        f_idx = self.fps_row.get_selected()
        fps = "60"
        if f_idx == 0:
            fps = "30"
        elif f_idx == 1:
            fps = "60"
        elif f_idx == 2:
            fps = "120"
        elif f_idx == 3:
            fps = getattr(self, "custom_fps_val", "") or "60"

        # Bitrate (kbps)
        br = int(self.bitrate_scale.get_value() * 1000)

        # Native QSettings enum: fullscreen=0, borderless=1, windowed=2.
        mode = {0: "1", 1: "0", 2: "2"}.get(self.display_mode_row.get_selected(), "1")
        hw = self.hw_decode_row.get_active()
        saved = self.moonlight_config.set_many(
            {
                "width": w,
                "height": h,
                "fps": fps,
                "bitrate": br,
                "windowmode": mode,
                "videodec": "0" if hw else "2",
                "hostaudio": str(self.audio_row.get_active()).lower(),
            }
        )
        if not saved:
            self.show_toast(_("Could not save the connection settings. Check file permissions."))

        # 2. Save Local Settings (Not in Moonlight.conf or specific to GuestView)
        s = self.config.get("guest", {})
        if not isinstance(s, dict):
            s = {}
        s["scale_native"] = self.scale_row.get_active()
        s["audio"] = self.audio_row.get_active()
        s["auto_quality"] = bool(s.get("auto_quality", True))
        s["auto_signature"] = getattr(self, "_auto_signature", "")
        self.config.set("guest", s)

    def load_guest_settings(self):
        # Force reload from file to catch external changes (e.g. from Preferences)
        self.moonlight_config.reload()
        # Suppress the row change handlers (custom-value dialogs, saves) while we
        # drive the widgets from disk.
        self.loading_settings = True

        # 1. Load from Moonlight.conf
        try:
            # Resolution
            w = int(float(self.moonlight_config.get("width", 1920)))
            h = int(float(self.moonlight_config.get("height", 1080)))

            # Map back to index
            if (w, h) == (1280, 720):
                self.resolution_row.set_selected(0)
            elif (w, h) == (1920, 1080):
                self.resolution_row.set_selected(1)
            elif (w, h) == (2560, 1440):
                self.resolution_row.set_selected(2)
            elif (w, h) == (3840, 2160):
                self.resolution_row.set_selected(3)
            else:
                self.resolution_row.set_selected(4)  # Custom
                self.custom_resolution_val = f"{w}x{h}"

            # FPS
            fps = int(float(self.moonlight_config.get("fps", 60)))
            if fps == 30:
                self.fps_row.set_selected(0)
            elif fps == 60:
                self.fps_row.set_selected(1)
            elif fps == 120:
                self.fps_row.set_selected(2)
            else:
                self.fps_row.set_selected(3)
                self.custom_fps_val = str(fps)

            # Set the range before reading a saved high bitrate so reload cannot
            # silently truncate it to the standard UI limit.
            br = int(float(self.moonlight_config.get("bitrate", 10000))) / 1000.0
            unlocked = self.moonlight_config.get("unlockbitrate", "false").lower() == "true" or br > 150
            self.unlock_bitrate_row.set_active(unlocked)
            self._on_bitrate_limit_changed()
            self.bitrate_scale.set_value(br)

            # Native enum (1=borderless, 0=fullscreen, 2=windowed) -> UI (0, 1, 2)
            mode = self.moonlight_config.get("windowmode", "1")
            if mode == "1":
                self.display_mode_row.set_selected(0)
            elif mode == "0":
                self.display_mode_row.set_selected(1)
            elif mode == "2":
                self.display_mode_row.set_selected(2)

            # HW Decode
            dec = self.moonlight_config.get("videodec", "0")
            self.hw_decode_row.set_active(dec != "2")

        except Exception as e:
            _log.error(f"Error loading Moonlight global settings: {e}")

        # 2. Load Local Settings and the native Moonlight preferences used by
        # the task-specific dialogs.
        try:
            s = self.config.get("guest", {})
            if not isinstance(s, dict):
                s = {}
            self.scale_row.set_active(bool(s.get("scale_native", True)))
            self.audio_row.set_active(
                self.moonlight_config.get(
                    "hostaudio",
                    str(bool(s.get("audio", True))).lower(),
                ).lower()
                == "true"
            )

            for key, (row, default, invert) in self._moonlight_boolean_rows.items():
                if key == "unlockbitrate":
                    continue  # already loaded before the slider value
                native = self.moonlight_config.get(key, default).lower() == "true"
                row.set_active(not native if invert else native)

            for key, (row, choices, default) in self._moonlight_combo_rows.items():
                current = self.moonlight_config.get(key, default)
                row.set_selected(next((index for index, (value, _label) in enumerate(choices) if value == current), 0))
        except Exception as exc:
            _log.error(f"Error loading guest settings: {exc}")
        finally:
            self._last_res_idx = self.resolution_row.get_selected()
            self._last_fps_idx = self.fps_row.get_selected()
            self.loading_settings = False
            settings = self.config.get("guest", {})
            settings = dict(settings) if isinstance(settings, dict) else {}
            profile = settings.get("quality_profile")
            if type(profile) is int and 1 <= profile <= len(self._QUALITY_PROFILES) and not self._is_automatic():
                current = (self.resolution_row.get_selected(), self.fps_row.get_selected(), self.bitrate_scale.get_value())
                if self.scale_row.get_active() or current != self._QUALITY_PROFILES[profile - 1]:
                    settings["quality_profile"] = self._CUSTOM_PROFILE
                    self.config.set("guest", settings)
            self.resolution_row.set_sensitive(not self.scale_row.get_active())
            self._sync_quality_summary()
            self._sync_audio_summary()

    def on_reset_clicked(self, _widget):
        dialog = Adw.AlertDialog(
            heading=_("Reset settings?"),
            body=_("Image, audio, input, and connection settings will return to their recommended defaults."),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("reset", _("Reset"))
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _dialog, response: self.reset_to_defaults() if response == "reset" else None)
        dialog.present(self)

    def reset_to_defaults(self):
        defaults: dict[str, object] = {
            "width": "1920",
            "height": "1080",
            "fps": "60",
            "bitrate": "20000",
            "windowmode": "1",
            "videodec": "0",
            "hostaudio": "true",
            "audiocfg": "0",
            "muteonfocusloss": "false",
            "vsync": "true",
            "framepacing": "false",
            "videocfg": "0",
            "hdr": "false",
            "yuv444": "false",
            "unlockbitrate": "false",
            "showperfoverlay": "false",
            "connwarnings": "true",
            "keepawake": "true",
            "mouseacceleration": "false",
            "capturesyskeys": "0",
            "abstouchmode": "true",
            "swapmousebuttons": "false",
            "reversescroll": "false",
            "swapfacebuttons": "false",
            "multicontroller": "true",
            "gamepadmouse": "true",
            "backgroundgamepad": "false",
            "gameopts": "true",
            "quitAppAfter": "false",
            "mdns": "true",
            "detectnetblocking": "true",
        }
        if not self.moonlight_config.set_many(defaults):
            self.show_toast(_("Could not reset the connection settings. Check file permissions."))
            return
        settings = self.config.get("guest", {})
        settings = settings if isinstance(settings, dict) else {}
        settings.update({"scale_native": True, "audio": True, "auto_quality": True, "quality_profile": self._AUTOMATIC_PROFILE, "auto_signature": ""})
        self.config.set("guest", settings)
        self.custom_resolution_val = ""
        self.custom_fps_val = ""
        self.load_guest_settings()
        self._set_automatic(True)
        self._apply_auto_quality(force=True)
        self._sync_quality_summary()
        self.show_toast(_("Connection settings restored"))

    def show_toast(self, m):
        show_toast = getattr(self.get_root(), "show_toast", None)
        if callable(show_toast):
            show_toast(m)
        else:
            _log.info(f"Toast: {m}")

    def show_shortcuts_dialog(self):
        dialog = Adw.Window(transient_for=self._root_window())
        dialog.add_css_class("brp-dialog")
        dialog.set_modal(True)
        dialog.set_title(_("Shortcuts & Instructions"))
        dialog.set_default_size(500, 600)

        content = Adw.ToolbarView()

        # Header
        header = Adw.HeaderBar()
        content.add_top_bar(header)

        # Body
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(600)
        for m in ["top", "bottom", "start", "end"]:
            getattr(clamp, f"set_margin_{m}")(16)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        box.append(intro(_("Keyboard Shortcuts"), _("Common shortcuts used during streaming"), "brp-input-keyboard-symbolic"))

        # Shortcuts Group
        grp = Adw.PreferencesGroup()
        grp.set_title(_("Keyboard Shortcuts"))
        grp.set_description(_("Common shortcuts used during streaming"))

        shortcuts = [
            ("Ctrl+Alt+Shift+Q", _("Quit Stream")),
            ("Ctrl+Alt+Shift+Z", _("Toggle Mouse Capture")),
            ("Ctrl+Alt+Shift+S", _("Toggle Stats Overlay")),
            ("Ctrl+Alt+Shift+M", _("Toggle Mouse Mode (Remote/Absolute)")),
            ("Ctrl+Alt+Shift+X", _("Toggle Fullscreen")),
        ]

        for keys, desc in shortcuts:
            row = Adw.ActionRow()
            row.set_title(keys)
            row.set_subtitle(desc)
            # Make keys bold/styled
            # We can't style title easily without custom child, but title is fine.
            grp.add(row)

        box.append(grp)

        monitor_group = Adw.PreferencesGroup(title=_("Multi-Monitor Support"))
        monitor_row = Adw.ActionRow(
            title=_("Switching Monitors"),
            subtitle=_("Choose the display on the game PC under Share → Preferences → Hardware and Capture. Reconnect after changing it."),
            use_markup=False,
        )
        monitor_group.add(monitor_row)
        box.append(monitor_group)

        clamp.set_child(box)
        scroll.set_child(clamp)
        content.set_content(scroll)

        dialog.set_content(content)
        dialog.present()
