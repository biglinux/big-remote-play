"""Screen names people recognise, even when GTK only knows "Unknown".

Reproduces the report "Share → Image and capture → Screen shows Unknown
Unknown": measured on a wlmaker (wlroots) session, GTK gave ``Unknown`` as the
manufacturer and model of all three LG screens, and on X11 the connector as
the model.
"""

from __future__ import annotations

from big_remote_play.utils.monitor_names import edid_product_name, edid_vendor, gdk_monitor_label, monitor_label


def _edid(vendor: str = "GSM", name: str = "LG ULTRAWIDE") -> bytes:
    data = bytearray(128)
    data[0:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    code = 0
    for letter in vendor:
        code = (code << 5) | (ord(letter) - 64)
    data[8], data[9] = code >> 8, code & 0xFF
    descriptor = b"\x00\x00\x00\xfc\x00" + (name.encode() + b"\n").ljust(13, b" ")
    data[54:72] = descriptor
    return bytes(data)


class _Monitor:
    def __init__(self, connector, manufacturer, model, description=None):
        self.values = {"get_connector": connector, "get_manufacturer": manufacturer, "get_model": model, "get_description": description}

    def __getattr__(self, name):
        return lambda: self.values[name]


def test_edid_gives_the_vendor_code_and_the_name():
    assert edid_vendor(_edid()) == "GSM"
    assert edid_product_name(_edid()) == "LG ULTRAWIDE"
    assert edid_product_name(b"short") == "" and edid_vendor(b"") == ""


def test_wlroots_unknown_fields_use_the_edid_name():
    monitor = _Monitor("DP-1", "Unknown", "Unknown", "LG Electronics LG ULTRAWIDE 503AZFM72555 (DP-1)")
    assert gdk_monitor_label(monitor, {"DP-1": _edid()}) == "LG ULTRAWIDE"


def test_without_edid_the_description_loses_the_serial_and_connector():
    monitor = _Monitor("DP-2", "Unknown", "Unknown", "LG Electronics LG HDR WFHD 0x01010101 (DP-2)")
    assert gdk_monitor_label(monitor, {}) == "LG Electronics LG HDR WFHD"


def test_x11_model_equal_to_the_connector_is_not_a_name():
    monitor = _Monitor("DP-2", None, "DP-2")
    assert gdk_monitor_label(monitor, {"DP-2": _edid(name="LG HDR WFHD")}) == "LG HDR WFHD"
    # An X11 output name differing from the kernel's still finds its EDID.
    other = _Monitor("DisplayPort-1", None, "DisplayPort-1")
    assert gdk_monitor_label(other, {"DP-2": _edid(name="27GL850")}, kernel_name={"DisplayPort-1": "DP-2"}.get) == "LG 27GL850"


def test_real_fields_win_and_vendor_codes_become_brands():
    assert monitor_label("Dell Inc.", "U2723QE", "DP-3") == "Dell Inc. U2723QE"
    assert monitor_label("SAM", "Odyssey G7", "HDMI-A-1") == "Samsung Odyssey G7"
    assert monitor_label("GSM", "LG", "HDMI-A-1") == "LG"


def test_nothing_known_is_empty_and_panels_are_built_in():
    assert monitor_label("Unknown", "Unknown", "HDMI-A-1") == ""
    assert monitor_label(None, "HDMI-A-1", "HDMI-A-1") == ""
    assert monitor_label("", "", "eDP-1") != ""
