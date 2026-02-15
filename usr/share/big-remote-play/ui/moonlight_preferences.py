import gi
import gettext
import locale
import configparser
import os
from pathlib import Path

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib

from utils.i18n import _

from utils.i18n import _
from utils.moonlight_config import MoonlightConfigManager

class MoonlightPreferencesPage(Adw.PreferencesPage):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title(_("Moonlight"))
        self.set_icon_name("preferences-desktop-remote-desktop-symbolic")
        
        self.config = MoonlightConfigManager()
        self.config.reload()
        self.setup_ui()

    def setup_ui(self):

        # 1. Configurações Básicas
        basic_group = Adw.PreferencesGroup()
        basic_group.set_title(_("Basic Settings"))
        self.add(basic_group)

        # Resolution and FPS
        res_row = Adw.ComboRow()
        res_row.set_title(_("Resolution"))
        res_model = Gtk.StringList()
        # Use simple labels to match screenshot style
        resolutions = [("720", "720p"), ("1080", "1080p"), ("1440", "1440p"), ("2160", "4K")]
        for __, label in resolutions: res_model.append(label)
        res_row.set_model(res_model)
        curr_h = self.config.get("height", "1080")
        res_row.set_selected(next((i for i, (h, _) in enumerate(resolutions) if h == curr_h), 1))
        res_row.connect("notify::selected", self.on_res_changed, resolutions)
        basic_group.add(res_row)

        fps_row = Adw.ComboRow()
        fps_row.set_title(_("Frame Rate (FPS)"))
        fps_model = Gtk.StringList()
        fps_options = ["30", "60", "90", "120"]
        for f in fps_options: fps_model.append(f + " FPS")
        fps_row.set_model(fps_model)
        curr_fps = self.config.get("fps", "60")
        fps_row.set_selected(next((i for i, f in enumerate(fps_options) if f == curr_fps), 1))
        fps_row.connect("notify::selected", lambda w, p: self.config.set("fps", fps_options[w.get_selected()]))
        basic_group.add(fps_row)

        # Bitrate
        bitrate_row = Adw.ActionRow()
        bitrate_row.set_title(_("Video Bitrate"))
        bitrate_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 150, 1)
        bitrate_scale.set_hexpand(True)
        bitrate_scale.set_draw_value(True)
        bitrate_scale.set_value_pos(Gtk.PositionType.RIGHT)
        bitrate_scale.set_value(int(self.config.get("bitrate", "10000")) / 1000)
        bitrate_scale.connect("value-changed", lambda w: self.config.set("bitrate", int(w.get_value() * 1000)))
        bitrate_row.add_suffix(bitrate_scale)
        basic_group.add(bitrate_row)

        # Window Mode
        mode_row = Adw.ComboRow()
        mode_row.set_title(_("Display Mode"))
        mode_model = Gtk.StringList()
        modes = [("3", _("Borderless Window")), ("1", _("Fullscreen")), ("2", _("Windowed"))]
        for __, label in modes: mode_model.append(label)
        mode_row.set_model(mode_model)
        curr_mode = self.config.get("windowMode", "3")
        mode_row.set_selected(next((i for i, (m, _) in enumerate(modes) if m == curr_mode), 0))
        mode_row.connect("notify::selected", lambda w, p: self.config.set("windowMode", modes[w.get_selected()][0]))
        basic_group.add(mode_row)

        self.add_boolean_option(basic_group, "vSync", _("V-Sync"), _("Visual Synchronization"), "true")
        self.add_boolean_option(basic_group, "framePacing", _("Frame Pacing"), _("Improve smoothness"), "true")

        # 2. Configurações de Entrada
        input_group = Adw.PreferencesGroup()
        input_group.set_title(_("Input Settings"))
        self.add(input_group)
        self.add_boolean_option(input_group, "mouseAcceleration", _("Optimize mouse for Remote Desktop"), None, "false")
        self.add_boolean_option(input_group, "captureSystemKeys", _("Capture system shortcuts"), None, "false")
        self.add_boolean_option(input_group, "touchscreenTrackpad", _("Use touchscreen as virtual trackpad"), None, "true")
        self.add_boolean_option(input_group, "swapMouseButtons", _("Invert mouse left/right buttons"), None, "false")
        self.add_boolean_option(input_group, "reverseScrollDirection", _("Invert scroll wheel direction"), None, "false")

        # 3. Configurações de Áudio
        audio_group = Adw.PreferencesGroup()
        audio_group.set_title(_("Audio Settings"))
        self.add(audio_group)

        audio_cfg_row = Adw.ComboRow()
        audio_cfg_row.set_title(_("Audio Configuration"))
        audio_cfg_model = Gtk.StringList()
        audio_cfgs = [("0", _("Stereo")), ("1", "5.1 Surround"), ("2", "7.1 Surround")]
        for __, label in audio_cfgs: audio_cfg_model.append(label)
        audio_cfg_row.set_model(audio_cfg_model)
        curr_audio = self.config.get("audioConfig", "0")
        audio_cfg_row.set_selected(next((i for i, (c, _) in enumerate(audio_cfgs) if c == curr_audio), 0))
        audio_cfg_row.connect("notify::selected", lambda w, p: self.config.set("audioConfig", audio_cfgs[w.get_selected()][0]))
        audio_group.add(audio_cfg_row)

        self.add_boolean_option(audio_group, "muteHostSpeakers", _("Mute host PC speakers while streaming"), None, "true")
        self.add_boolean_option(audio_group, "muteOnFocusLost", _("Mute audio when Moonlight is not the active window"), None, "false")

        # 4. Configurações do Controle
        ctrl_group = Adw.PreferencesGroup()
        ctrl_group.set_title(_("Controller Settings"))
        self.add(ctrl_group)
        self.add_boolean_option(ctrl_group, "gamepadSwapButtons", _("Swap A/B and X/Y buttons"), None, "false")
        self.add_boolean_option(ctrl_group, "gamepadForceController1", _("Force controller #1 always connected"), None, "false")
        self.add_boolean_option(ctrl_group, "gamepadMouseEmulation", _("Enable mouse control with gamepad"), None, "true")
        self.add_boolean_option(ctrl_group, "gamepadBackgroundInput", _("Process controller input in background"), None, "false")

        # 5. Configurações de Host
        host_group = Adw.PreferencesGroup()
        host_group.set_title(_("Host Settings"))
        self.add(host_group)
        self.add_boolean_option(host_group, "optimizeGameSettings", _("Optimize game settings for streaming"), None, "true")
        self.add_boolean_option(host_group, "quitAfter", _("Quit app on host after streaming ends"), None, "false")

        # 6. Configurações Avançadas
        adv_group = Adw.PreferencesGroup()
        adv_group.set_title(_("Advanced Settings"))
        self.add(adv_group)

        decoder_row = Adw.ComboRow()
        decoder_row.set_title(_("Video Decoder"))
        dec_model = Gtk.StringList()
        decs = [("0", _("Automatic (Recommended)")), ("1", _("Hardware")), ("2", _("Software"))]
        for __, l in decs: dec_model.append(l)
        decoder_row.set_model(dec_model)
        curr_dec = self.config.get("videoDecoder", "0")
        decoder_row.set_selected(next((i for i, (d, _) in enumerate(decs) if d == curr_dec), 0))
        decoder_row.connect("notify::selected", lambda w, p: self.config.set("videoDecoder", decs[w.get_selected()][0]))
        adv_group.add(decoder_row)

        codec_row = Adw.ComboRow()
        codec_row.set_title(_("Video Codec"))
        codec_model = Gtk.StringList()
        codecs = [("0", _("Automatic (Recommended)")), ("1", "H.264"), ("2", "HEVC"), ("3", "AV1")]
        for __, l in codecs: codec_model.append(l)
        codec_row.set_model(codec_model)
        curr_codec = self.config.get("videoCodec", "0")
        codec_row.set_selected(next((i for i, (c, _) in enumerate(codecs) if c == curr_codec), 0))
        codec_row.connect("notify::selected", lambda w, p: self.config.set("videoCodec", codecs[w.get_selected()][0]))
        adv_group.add(codec_row)

        self.add_boolean_option(adv_group, "hdr", _("Enable HDR (Experimental)"), None, "false")
        self.add_boolean_option(adv_group, "yuv444", _("Enable YUV 4:4:4 (Experimental)"), None, "false")
        self.add_boolean_option(adv_group, "unlockBitrate", _("Unlock bitrate limit (Experimental)"), None, "false")
        self.add_boolean_option(adv_group, "pcAutodiscovery", _("Discover PCs automatically"), None, "true")
        self.add_boolean_option(adv_group, "checkBlockedConnections", _("Check for blocked connections automatically"), None, "true")
        self.add_boolean_option(adv_group, "performanceOverlay", _("Show performance stats while streaming"), None, "false")

        # 7. Configurações de UI
        ui_group = Adw.PreferencesGroup()
        ui_group.set_title(_("UI Settings"))
        self.add(ui_group)
        self.add_boolean_option(ui_group, "connectionQualityWarnings", _("Show connection quality warnings"), None, "true")
        self.add_boolean_option(ui_group, "keepDisplayAwake", _("Keep display active during streaming"), None, "true")

    def add_boolean_option(self, group, key, title, subtitle, default):
        row = Adw.SwitchRow()
        row.set_title(title)
        if subtitle: row.set_subtitle(subtitle)
        val = self.config.get(key, default).lower() == "true"
        row.set_active(val)
        row.connect("notify::active", lambda w, p: self.config.set(key, str(w.get_active()).lower()))
        group.add(row)

    def on_res_changed(self, row, param, resolutions):
        idx = row.get_selected()
        h, __ = resolutions[idx]
        w = "1280" if h == "720" else "1920" if h == "1080" else "2560" if h == "1440" else "3840"
        self.config.set("width", w)
        self.config.set("height", h)
