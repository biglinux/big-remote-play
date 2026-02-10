import gi
import gettext
import locale

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib, GObject
from pathlib import Path
import configparser
import os

from utils.i18n import _
from utils.icons import create_icon_widget

class SunshineConfigManager:
    def __init__(self):
        self.config_dir = Path.home() / '.config' / 'big-remoteplay' / 'sunshine'
        self.config_file = self.config_dir / 'sunshine.conf'
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config = {}
        self.load()

    def load(self):
        self.config = {}
        if self.config_file.exists():
            try:
                # Sunshine config is key = value typical format but without sections
                with open(self.config_file, 'r') as f:
                    content = f.read()
                
                # Add dummy section
                content = "[DEFAULT]\n" + content
                
                cp = configparser.ConfigParser()
                cp.read_string(content)
                
                for key in cp['DEFAULT']:
                    self.config[key] = cp['DEFAULT'][key]
            except Exception as e:
                print(f"Error loading Sunshine config: {e}")

    def save(self):
        try:
            with open(self.config_file, 'w') as f:
                for key, value in self.config.items():
                    f.write(f"{key} = {value}\n")
                print(f"DEBUG: SunshineConfigManager - Saved config to {self.config_file}")
        except Exception as e:
            print(f"Error saving Sunshine config: {e}")

    def get(self, key, default=None):
        return self.config.get(key, str(default))

    def set(self, key, value):
        self.config[key] = str(value)
        self.save()


class SunshinePreferencesPage(Adw.PreferencesPage):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title(_("Sunshine"))
        self.set_icon_name("preferences-desktop-remote-desktop-symbolic")
        
        self.config = SunshineConfigManager()
        
        # Standard Adwaita Layout: Multiple Groups in one Page
        # This creates a long scrollable page with sections.
        self.setup_groups()

    def setup_groups(self):
        # Categories mapping to (Function, Icon, Description)
        categories = {
            _("General"): (self.get_general_options(), "preferences-system-symbolic", _("Server general settings")),
            _("Input"): (self.get_input_options(), "input-keyboard-symbolic", _("Keyboard, mouse and gamepad")),
            _("Audio/Video"): (self.get_av_options(), "video-display-symbolic", _("Resolution, bitrate and quality")),
            _("Network"): (self.get_network_options(), "network-workgroup-symbolic", _("Ports and connectivity")),
            _("Config Files"): ([], "document-properties-symbolic", _("Configuration files and logs")),
            _("Advanced"): (self.get_advanced_options(), "preferences-other-symbolic", _("Advanced options")),
            
            # Encoders
            _("NVIDIA NVENC"): (self.get_nvenc_options(), "media-flash-symbolic", _("NVIDIA Encoder")),
            _("Intel QuickSync"): (self.get_qsv_options(), "media-memory-symbolic", _("Intel Encoder")),
            _("AMD AMF"): (self.get_amf_options(), "media-tape-symbolic", _("AMD Encoder")),
            _("VideoToolbox"): (self.get_vt_options(), "media-optical-symbolic", _("Apple Encoder")),
            _("VA-API"): (self.get_vaapi_options(), "media-view-subtitles-symbolic", _("VA-API Encoder (Linux)")),
            _("Software"): (self.get_software_options(), "software-update-available-symbolic", _("CPU Encoding")),
        }

        for title, (options, icon, desc) in categories.items():
            # Create Group
            group = Adw.PreferencesGroup()
            group.set_title(title)
            group.set_description(desc)
            
            has_content = False
            
            if title == _("Config Files"):
                 self.setup_config_files_tab(group)
                 has_content = True
            else:
                if options:
                    for opt in options:
                        row = self.create_option_row(opt)
                        if row:
                            group.add(row)
                            has_content = True
            
            if not has_content and title != _("Config Files"):
                 # Add placeholder row
                 row = Adw.ActionRow()
                 row.set_title(_("No options available"))
                 row.set_sensitive(False)
                 group.add(row)
                 
            self.add(group)

    def create_option_row(self, opt):
        # Unpack option tuple
        # Now supports optional description: (key, label, type, default, choices, description)
        if len(opt) == 6:
            key, label, type_, default, choices, description = opt
        else:
            key, label, type_, default, choices = opt
            description = None

        current_val = self.config.get(key, default)
        
        row = None
        if type_ == "switch":
            row = Adw.SwitchRow()
            row.set_title(label)
            active = current_val.lower() in ('true', 'enabled', '1', 'on')
            row.set_active(active)
            row.connect('notify::active', lambda w, p: self.config.set(key, str(w.get_active()).lower()))
            
        elif type_ == "entry":
            row = Adw.EntryRow()
            row.set_title(label)
            row.set_text(str(current_val))
            row.connect('changed', lambda w: self.config.set(key, w.get_text()))
            
        elif type_ == "spin":
            row = Adw.ActionRow()
            row.set_title(label)
            spin = Gtk.SpinButton.new_with_range(0, 100000, 1) # Generic range
            try:
                val = float(current_val)
            except:
                val = float(default) if default else 0
            spin.set_value(val)
            spin.connect('value-changed', lambda w: self.config.set(key, int(w.get_value()))) 
            spin.set_valign(Gtk.Align.CENTER)
            row.add_suffix(spin)
            
        elif type_ == "combo":
            row = Adw.ComboRow()
            row.set_title(label)
            
            # Check if choices are strings or tuples (key, label)
            is_kv = False
            if choices and isinstance(choices[0], tuple):
                is_kv = True
                display_values = [c[1] for c in choices]
                keys = [c[0] for c in choices]
            else:
                display_values = choices
                keys = choices

            model = Gtk.StringList()
            for c in display_values:
                model.append(c)
            row.set_model(model)
            
            # Find index
            try:
                # current_val should be the key
                idx = 0
                if current_val in keys:
                    idx = keys.index(current_val)
            except:
                idx = 0
            row.set_selected(idx)
            
            row.connect('notify::selected', lambda w, p, k=keys, key_name=key: self.config.set(key_name, k[w.get_selected()]))
        
        if row and description:
            if isinstance(row, Adw.ActionRow):
                row.set_subtitle(description)
            else:
                row.set_tooltip_text(description)
            
        return row


    def setup_config_files_tab(self, group):
        # Determine paths
        # Sunshine defaults usually:
        # apps.json: alongside sunshine.conf or in config_dir
        # sunshine.log: in config_dir
        # credentials: in config_dir (often credentials.json)
        # pkey: sunshine.key (in config_dir)
        # cert: sunshine.cert (in config_dir)
        # state: sunshine_state.json (in config_dir)

        # We can check specific config keys if they exist, otherwise assume defaults
        
        def get_path(key, default_filename):
            base = self.config.config_dir
            val = self.config.get(key)
            if val and val != str(None):
                p = Path(val)
                if p.is_absolute():
                    return p
                else:
                    return base / p
            return base / default_filename

        files = [
            (_("Apps File"), get_path("apps_file", "apps.json"), _("The file where current apps of Sunshine are stored.")),
            (_("Credentials File"), get_path("credentials_file", "credentials.json"), _("Store Username/Password separately from Sunshine's state file.")),
            (_("Logfile Path"), get_path("log_path", "sunshine.log"), _("The file where the current logs of Sunshine are stored.")),
            (_("Private Key"), get_path("pkey", "sunshine.key"), _("The private key used for the web UI and Moonlight client pairing. For best compatibility, this should be an RSA-2048 private key.")),
            (_("Certificate"), get_path("cert", "sunshine.cert"), _("The certificate used for the web UI and Moonlight client pairing. For best compatibility, this should have an RSA-2048 public key.")),
            (_("State File"), get_path("state_file", "sunshine_state.json"), _("The file where current state of Sunshine is stored")),
            (_("Configuration File"), self.config.config_file, _("The main configuration file for Sunshine."))
        ]
        
        for name, path, desc in files:
            row = Adw.ActionRow()
            row.set_title(name)
            row.set_subtitle(str(path))
            row.set_tooltip_text(desc)
            
            # Check if file exists
            if not path.exists():
                row.add_css_class("error")
                row.add_prefix(create_icon_widget("dialog-warning-symbolic", size=16))
            
            btn = Gtk.Button()
            btn.set_child(create_icon_widget("document-open-symbolic", size=16))
            btn.set_valign(Gtk.Align.CENTER)
            btn.add_css_class("flat")
            btn.connect('clicked', lambda _, p=path: self.open_file(p))
            
            row.add_suffix(btn)
            group.add(row)

    def open_file(self, path):
        # Try to open with xdg-open
        try:
            GLib.spawn_command_line_async(f"xdg-open '{path}'")
        except:
            pass

    def get_general_options(self):
        return [
            ("locale", _("Locale"), "combo", "en", [
                "bg", "cs", "de", "en", "en_GB", "en_US", "es", "fr", "hu", 
                "it", "ja", "ko", "pl", "pt", "pt_BR", "ru", "sv", "tr", 
                "uk", "vi", "zh", "zh_TW"
            ], _("The locale used for Sunshine's user interface.")),
            ("sunshine_name", _("Sunshine Name"), "entry", "Sunshine", None, _("The name of the Sunshine instance as seen by clients.")),
            ("sunshine_user", _("Sunshine User"), "entry", "", None, _("Username for API access (Monitoring)")),
            ("sunshine_password", _("Sunshine Password"), "entry", "", None, _("Password for API access (Monitoring)")),
            ("min_log_level", _("Log Level"), "combo", "2", [
                ("0", "Verbose"), ("1", "Debug"), ("2", "Info"), 
                ("3", "Warning"), ("4", "Error"), ("5", "Fatal"), ("6", "None")
            ], _("The minimum log level printed to standard out")),
            ("notify_pre_releases", _("Pre-Release Notifications"), "switch", "false", None, _("Whether to be notified of new pre-release versions of Sunshine")),
            ("system_tray", _("Enable System Tray"), "switch", "true", None, _("Show icon in system tray and display desktop notifications")),
        ]

    def get_network_options(self):
        return [
            ("upnp", _("UPnP"), "switch", "false", None, _("Automatically configure port forwarding for streaming over the Internet")),
            ("address_family", _("Address Family"), "combo", "ipv4", [
                ("ipv4", "IPv4"), ("both", "IPv4 + IPv6")
            ], _("Set the address family used by Sunshine")),
            ("bind_address", _("Bind Address"), "entry", "0.0.0.0", None, _("IP address to bind the service to")),
            ("port", _("Port (Moonlight)"), "spin", "47989", None, _("Set the family of ports used by Sunshine.\nTCP: 47984, 47989, 47990 (Web UI), 48010\nUDP: 47998 - 48012")),
            ("origin_web_ui_allowed", _("Web UI Access"), "combo", "lan", [
                ("pc", "Localhost"), ("lan", "LAN"), ("wan", "WAN")
            ], _("The origin of the remote endpoint address that is not denied access to Web UI.\nWarning: Exposing the Web UI to the internet is a security risk! Proceed at your own risk!")),
            ("external_ip", _("External IP"), "entry", "", None, _("If no external IP address is given, Sunshine will automatically detect external IP")),
            ("lan_encryption_mode", _("LAN Encryption"), "combo", "0", [
                ("0", _("Disabled")), ("1", _("Mode 1")), ("2", _("Mode 2"))
            ], _("This determines when encryption will be used when streaming over your local network. Encryption can reduce streaming performance, particularly on less powerful hosts and clients.")),
            ("wan_encryption_mode", _("WAN Encryption"), "combo", "0", [
                ("0", _("Disabled")), ("1", _("Mode 1")), ("2", _("Mode 2"))
            ], _("This determines when encryption will be used when streaming over the Internet. Encryption can reduce streaming performance, particularly on less powerful hosts and clients.")),
            ("ping_timeout", _("Ping Timeout (ms)"), "spin", "2000", None, _("How long to wait in milliseconds for data from moonlight before shutting down the stream")),
        ]

    def get_input_options(self):
        return [
            ("controller", _("Enable Gamepad Input"), "switch", "true", None, _("Allows guests to control the host system with a gamepad / controller")),
            ("gamepad", _("Emulated Gamepad Type"), "combo", "auto", [
                ("auto", _("Automatic")), ("x360", "Xbox 360"), ("ds4", "DualShock 4"),
                ("ds5", "DualSense (Linux)"), ("switch", "Nintendo Switch"), ("xone", "Xbox One")
            ], _("Choose which type of gamepad to emulate on the host")),
            ("motion_as_ds4", _("Motion as DS4"), "switch", "true", None, _("Emulate motion controls as DS4")),
            ("touchpad_as_ds4", _("Touchpad as DS4"), "switch", "true", None, _("Emulate touchpad as DS4")),
            ("ds4_back_as_touchpad_click", _("Back Button as Touchpad Click"), "switch", "true", None, _("Use Back button for touchpad click on DS4")),
            ("ds5_inputtino_randomize_mac", _("Randomize MAC (DS5)"), "switch", "true", None, _("Randomize virtual MAC address for DS5")),
            ("back_button_timeout", _("Home/Guide Timeout (ms)"), "spin", "-1", None, _("Hold Back/Select to emulate Guide button. &lt; 0 to disable.")),
            ("keyboard", _("Enable Keyboard Input"), "switch", "true", None, _("Allows guests to control the host system with the keyboard")),
            ("mouse", _("Enable Mouse Input"), "switch", "true", None, _("Allows guests to control the host system with the mouse")),
            ("always_send_scancodes", _("Always Send Scancodes"), "switch", "true", None, _("Always send raw key scancodes")),
            ("key_rightalt_to_key_win", _("Map Right Alt to Windows"), "switch", "false", None, _("Make Sunshine think the Right Alt key is the Windows key")),
            ("high_resolution_scrolling", _("High Resolution Scrolling"), "switch", "true", None, _("Pass through high resolution scroll events from Moonlight clients")),
        ]

    def get_av_options(self):
        return [
            ("audio_sink", _("Audio Sink"), "entry", "", None, _("Listing all available audio sinks is possible by running `pactl list short sinks` (PulseAudio) or `wpctl status` (PipeWire).")),
            ("stream_audio", _("Stream Audio"), "switch", "true", None, _("Whether to stream audio or not. Disabling this can be useful for streaming headless displays as second monitors.")),
            ("adapter_name", _("Graphics Adapter"), "entry", "", None, _("Specific GPU to use")),
            ("output_name", _("Display Name"), "entry", "", None, _("During Sunshine startup, you should see the list of detected displays. Note: You need to use the id value inside the parenthesis. Below is an example; the actual output can be found in the Troubleshooting tab.")),
            ("max_bitrate", _("Maximum Bitrate"), "spin", "0", None, _("The maximum bitrate (in Kbps) that Sunshine will encode the stream at. If set to 0, it will always use the bitrate requested by Moonlight.")),
            ("min_fps", _("Minimum FPS Target"), "spin", "0", None, _("The lowest effective FPS a stream can reach. A value of 0 is treated as roughly half of the stream's FPS. A setting of 20 is recommended if you stream 24 or 30fps content.")),
        ]

    def get_advanced_options(self):
        return [
            ("fec_percentage", _("FEC Percentage"), "spin", "20", None, _("Percentage of error correcting packets per data packet in each video frame. Higher values can correct for more network packet loss, but at the cost of increasing bandwidth usage.")),
            ("qp", _("Quantization Parameter"), "spin", "28", None, _("Some devices may not support Constant Bit Rate. For those devices, QP is used instead. Higher value means more compression, but less quality.")),
            ("min_threads", _("Minimum CPU Thread Count"), "spin", "4", None, _("Increasing the value slightly reduces encoding efficiency, but the tradeoff is usually worth it to gain the use of more CPU cores for encoding. The ideal value is the lowest value that can reliably encode at your desired streaming settings on your hardware.")),
            ("hevc_mode", _("HEVC Support"), "combo", "0", [
                ("0", _("Disabled")), ("1", _("Advertised (Recommended)")), ("2", "Main 10"), ("3", "Main 10 + HDR")
            ], _("Allows the client to request HEVC Main or HEVC Main10 video streams. HEVC is more CPU-intensive to encode, so enabling this may reduce performance when using software encoding.")),
            ("av1_mode", _("AV1 Support"), "combo", "0", [
                 ("0", _("Disabled")), ("1", _("Advertised (Recommended)")), ("2", "Main 10"), ("3", "Main 10 + HDR")
            ], _("Allows the client to request AV1 Main 8-bit or 10-bit video streams. AV1 is more CPU-intensive to encode, so enabling this may reduce performance when using software encoding.")),
            ("capture", _("Force a Specific Capture Method"), "combo", "auto", [
                ("auto", _("Autodetect (Recommended)")), ("nvfbc", "NvFBC"), ("wlr", "wlroots"), ("kms", "KMS"), ("x11", "X11"), ("portal", "XDG Desktop Portal")
            ], _("On automatic mode Sunshine will use the first one that works. NvFBC requires patched nvidia drivers.")),
            ("encoder", _("Force a Specific Encoder"), "combo", "auto", [
                ("auto", _("Autodetect")), ("nvenc", "NVIDIA NVENC"), 
                ("vaapi", "VA-API"), ("software", "Software"), 
                ("quicksync", "Intel QuickSync"), ("amdvce", "AMD AMF")
            ], _("Force a specific encoder, otherwise Sunshine will select the best available option. Note: If you specify a hardware encoder on Windows, it must match the GPU where the display is connected.")),
        ]

    def get_nvenc_options(self):
        return [
            ("nvenc_preset", _("Performance Preset"), "combo", "1", [
                ("1", "P1 (Fastest)"), ("2", "P2"), ("3", "P3"), 
                ("4", "P4 (Default)"), ("5", "P5"), ("6", "P6"), ("7", "P7 (Best Quality)")
            ], _("Higher numbers improve compression (quality at given bitrate) at the cost of increased encoding latency. Recommended to change only when limited by network or decoder, otherwise similar effect can be accomplished by increasing bitrate.")),
            ("nvenc_twopass", _("Two-Pass Mode"), "combo", "quarter_res", [
                ("disabled", _("Disabled")), ("quarter_res", _("Quarter Resolution")), ("full_res", _("Full Resolution"))
            ], _("Adds preliminary encoding pass. This allows to detect more motion vectors, better distribute bitrate across the frame and more strictly adhere to bitrate limits. Disabling it is not recommended since this can lead to occasional bitrate overshoot and subsequent packet loss.")),
            ("nvenc_spatial_aq", _("Spatial AQ"), "switch", "false", None, _("Assign higher QP values to flat regions of the video. Recommended to enable when streaming at lower bitrates.")),
            ("nvenc_vbv_increase", _("Single-frame VBV/HRD percentage increase"), "spin", "0", None, _("By default sunshine uses single-frame VBV/HRD, which means any encoded video frame size is not expected to exceed requested bitrate divided by requested frame rate. Relaxing this restriction can be beneficial and act as low-latency variable bitrate, but may also lead to packet loss if the network doesn't have buffer headroom to handle bitrate spikes. Maximum accepted value is 400, which corresponds to 5x increased encoded video frame upper size limit.")),
            ("nvenc_h264_cavlc", _("Prefer CAVLC over CABAC in H.264"), "switch", "false", None, _("Simpler form of entropy coding. CAVLC needs around 10% more bitrate for same quality. Only relevant for really old decoding devices.")),
        ]

    def get_qsv_options(self):
        return [
            ("qsv_preset", _("QuickSync Preset"), "combo", "medium", [
                "veryfast", "faster", "fast", "medium", "slow", "slower", "slowest"
            ], _("Performance preset")),
            ("qsv_coder", _("QuickSync Coder (H264)"), "combo", "auto", [
                ("auto", _("Auto")), ("cabac", "CABAC"), ("cavlc", "CAVLC")
            ], _("Entropy coding mode")),
            ("qsv_slow_hevc", _("Allow Slow HEVC Encoding"), "switch", "false", None, _("This can enable HEVC encoding on older Intel GPUs, at the cost of higher GPU usage and worse performance.")),
        ]

    def get_amf_options(self):
        return [
            ("amd_usage", _("AMF Usage"), "combo", "ultralowlatency", [
                ("transcoding", "Transcoding"), ("webcam", "Webcam"), 
                ("lowlatency_high_quality", "Low Latency High Quality"), 
                ("lowlatency", "Low Latency"), ("ultralowlatency", "Ultra Low Latency")
            ], _("This sets the base encoding profile. All options presented below will override a subset of the usage profile, but there are additional hidden settings applied that cannot be configured elsewhere.")),
            ("amd_rc", _("AMF Rate Control"), "combo", "vbr_latency", [
                ("cbr", "CBR"), ("cqp", "CQP"), 
                ("vbr_latency", "VBR Latency"), ("vbr_peak", "VBR Peak")
            ], _("This controls the rate control method to ensure we are not exceeding the client bitrate target. 'cqp' is not suitable for bitrate targeting, and other options besides 'vbr_latency' depend on HRD Enforcement to help constrain bitrate overflows.")),
            ("amd_enforce_hrd", _("AMF Hypothetical Reference Decoder (HRD) Enforcement"), "switch", "false", None, _("Increases the constraints on rate control to meet HRD model requirements. This greatly reduces bitrate overflows, but may cause encoding artifacts or reduced quality on certain cards.")),
            ("amd_quality", _("AMF Quality"), "combo", "balanced", [
                ("speed", _("Speed")), ("balanced", _("Balanced")), ("quality", _("Quality"))
            ], _("This controls the balance between encoding speed and quality.")),
            ("amd_preanalysis", _("AMF Preanalysis"), "switch", "false", None, _("This enables rate-control preanalysis, which may increase quality at the expense of increased encoding latency.")),
            ("amd_vbaq", _("AMF Variance Based Adaptive Quantization (VBAQ)"), "switch", "true", None, _("The human visual system is typically less sensitive to artifacts in highly textured areas. In VBAQ mode, pixel variance is used to indicate the complexity of spatial textures, allowing the encoder to allocate more bits to smoother areas. Enabling this feature leads to improvements in subjective visual quality with some content.")),
            ("amd_coder", _("AMF Coder (H264)"), "combo", "auto", [
                ("auto", _("Auto")), ("cabac", "CABAC"), ("cavlc", "CAVLC")
            ], _("Allows you to select the entropy encoding to prioritize quality or encoding speed. H.264 only.")),
        ]

    def get_vt_options(self):
        return [
            ("vt_coder", _("VideoToolbox Coder"), "combo", "auto", [
                ("auto", _("Auto")), ("cabac", "CABAC"), ("cavlc", "CAVLC")
            ], _("Entropy coding mode")),
            ("vt_software", _("VideoToolbox Software Encoding"), "combo", "auto", [
                ("auto", _("Auto")), ("disabled", _("Disabled")), ("allowed", _("Allowed")), ("forced", _("Forced"))
            ], _("Allow fallback to software encoding")),
            ("vt_realtime", _("VideoToolbox Realtime Encoding"), "switch", "true", None, _("Realtime encoding priority")),
        ]

    def get_vaapi_options(self):
        return [
            ("vaapi_strict_rc_buffer", _("Strictly enforce frame bitrate limits for H.264/HEVC on AMD GPUs"), "switch", "false", None, _("Enabling this option can avoid dropped frames over the network during scene changes, but video quality may be reduced during motion.")),
        ]

    def get_software_options(self):
        return [
            ("sw_preset", _("SW Presets"), "combo", "superfast", [
                "ultrafast", "superfast", "veryfast", "faster", "fast", 
                "medium", "slow", "slower", "veryslow"
            ], _("Optimize the trade-off between encoding speed (encoded frames per second) and compression efficiency (quality per bit in the bitstream). Defaults to superfast.")),
            ("sw_tune", _("SW Tune"), "combo", "zerolatency", [
                ("film", "Film"), ("animation", "Animation"), ("grain", "Grain"), ("stillimage", "Still Image"), ("fastdecode", "Fast Decode"), ("zerolatency", "Zero Latency")
            ], _("Tuning options, which are applied after the preset. Defaults to zerolatency.")),
        ]
