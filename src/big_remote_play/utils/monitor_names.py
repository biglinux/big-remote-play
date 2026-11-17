"""A name a person recognises for each connected screen.

GTK does not always know it. Measured on the maintainer's three-monitor PC
(2026-11-17):

- wlroots compositors (wlmaker) send ``Unknown`` as both the manufacturer and
  the model, while the description carries the real name with the serial
  number and connector: ``LG Electronics LG ULTRAWIDE 503AZFM72555 (DP-1)``;
- GDK on X11 gives no manufacturer and repeats the connector as the model
  (``DP-1``).

Joining those fields showed *Unknown Unknown* in Share → Image and capture →
Screen. The screen's own EDID is the reliable source: its display-name
descriptor (``LG ULTRAWIDE``) and its three-letter vendor code (``GSM``). The
kernel exposes it, readable by the user, under ``/sys/class/drm``.

GTK-free and side-effect free except :func:`connector_edids`, which only
reads sysfs.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import re

from big_remote_play.utils.i18n import _

DRM_ROOT = Path("/sys/class/drm")

# Values GTK reports when it does not know (compared case-insensitively).
_PLACEHOLDERS = {"", "unknown", "unknown display", "unknown monitor", "default", "none", "0x0000"}

# EDID vendor codes (PNP ids) of common screen makers → the brand people know.
_VENDORS = {
    "ACR": "Acer",
    "AOC": "AOC",
    "APP": "Apple",
    "AUO": "AU Optronics",
    "AUS": "ASUS",
    "BNQ": "BenQ",
    "BOE": "BOE",
    "CMN": "Innolux",
    "DEL": "Dell",
    "ENC": "EIZO",
    "GBT": "Gigabyte",
    "GSM": "LG",
    "HPN": "HP",
    "HWP": "HP",
    "IVM": "iiyama",
    "LEN": "Lenovo",
    "LGD": "LG Display",
    "MEI": "Panasonic",
    "MSI": "MSI",
    "NEC": "NEC",
    "PHL": "Philips",
    "SAM": "Samsung",
    "SHP": "Sharp",
    "SNY": "Sony",
    "VSC": "ViewSonic",
    "XMI": "Xiaomi",
}

# Panels inside a notebook or tablet.
_BUILT_IN = re.compile(r"^(eDP|LVDS|DSI)(-|$)", re.IGNORECASE)
# A trailing token that is a serial number, not part of the name:
# ``503AZFM72555``, ``0x01010101``.
_SERIAL = re.compile(r"^(0x[0-9a-fA-F]+|(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{8,})$")


def _real(value: str | None, connector: str) -> str:
    text = " ".join((value or "").split())
    if text.casefold() in _PLACEHOLDERS or text.casefold() == connector.casefold():
        return ""
    return text


def edid_vendor(edid: bytes) -> str:
    """The three-letter vendor code (``GSM``), or ``""``."""
    if len(edid) < 10:
        return ""
    value = (edid[8] << 8) | edid[9]
    letters = [((value >> shift) & 0x1F) for shift in (10, 5, 0)]
    if not all(1 <= letter <= 26 for letter in letters):
        return ""
    return "".join(chr(letter + 64) for letter in letters)


def edid_product_name(edid: bytes) -> str:
    """The display-name descriptor (``LG ULTRAWIDE``), or ``""``."""
    if len(edid) < 128:
        return ""
    for offset in (54, 72, 90, 108):
        block = edid[offset : offset + 18]
        # A display descriptor starts with three zero bytes; 0xFC is its name.
        if block[0:3] == b"\x00\x00\x00" and block[3] == 0xFC:
            text = block[5:18].split(b"\n", 1)[0].decode("cp437", errors="replace")
            return " ".join(text.split())
    return ""


def _with_brand(name: str, brand: str) -> str:
    if not brand or not name:
        return name
    if name.casefold().startswith(brand.casefold()) or brand.casefold() in name.casefold().split():
        return name
    return f"{brand} {name}"


def _brand(manufacturer: str, edid: bytes) -> str:
    if manufacturer:
        return _VENDORS.get(manufacturer.upper(), manufacturer) if len(manufacturer) == 3 and manufacturer.isalpha() else manufacturer
    return _VENDORS.get(edid_vendor(edid), "")


def _from_description(description: str, connector: str) -> str:
    text = " ".join((description or "").split())
    if connector:
        text = re.sub(rf"\s*\({re.escape(connector)}\)$", "", text)
    words = [word for word in text.split() if word.casefold() != "unknown"]
    while len(words) > 1 and _SERIAL.match(words[-1]):
        words.pop()
    name = " ".join(words)
    return "" if name.casefold() == connector.casefold() else name


def monitor_label(manufacturer: str | None, model: str | None, connector: str, *, description: str | None = "", edid: bytes = b"") -> str:
    """The best name for one screen; ``""`` when nothing better than its connector is known."""
    connector = connector or ""
    maker = _real(manufacturer, connector)
    product = _real(model, connector)
    if product:
        return _with_brand(product, _brand(maker, edid))
    name = edid_product_name(edid)
    if name:
        return _with_brand(name, _brand(maker, edid))
    name = _from_description(description or "", connector)
    if name:
        return name
    if maker:
        return _brand(maker, edid)
    if _BUILT_IN.match(connector):
        return _("Built-in screen")
    return ""


def connector_edids(root: Path = DRM_ROOT) -> dict[str, bytes]:
    """Kernel connector name (``DP-1``) → EDID of each connected screen."""
    from big_remote_play.host.display_connectors import drm_connector_edids

    try:
        return drm_connector_edids(root)
    except OSError:
        return {}


def gdk_monitor_label(monitor, edids: dict[str, bytes] | None = None, *, kernel_name: Callable[[str], str | None] | None = None) -> str:
    """:func:`monitor_label` for a ``Gdk.Monitor`` (or anything with its getters)."""
    connector = ""
    try:
        connector = monitor.get_connector() or ""
    except Exception:
        connector = ""

    def read(getter: str) -> str:
        method = getattr(monitor, getter, None)
        try:
            return str(method() or "") if callable(method) else ""
        except Exception:
            return ""

    edids = edids if edids is not None else connector_edids()
    edid = edids.get(connector, b"")
    if not edid and kernel_name is not None and connector:
        edid = edids.get(kernel_name(connector) or "", b"")
    return monitor_label(read("get_manufacturer"), read("get_model"), connector, description=read("get_description"), edid=edid)


__all__ = ["connector_edids", "edid_product_name", "edid_vendor", "gdk_monitor_label", "monitor_label"]
