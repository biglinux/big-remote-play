"""Streaming quality derived from this machine's own hardware and network link.

Detection happens when the user first runs the app and whenever the detected
hardware changes (``signature`` differs); it never reacts to a live session, so
a value the user sees today is the value they get tomorrow. Every rule here is
a pure function so it can be tested without a display or a network.
"""

from __future__ import annotations

from pathlib import Path

# Stream heights the UI offers, and the bitrate each one needs on a wired link
# at 60 FPS. Higher frame rates and lossy links scale these below.
_BITRATE_MBPS_BY_HEIGHT = {720: 10.0, 1080: 20.0, 1440: 40.0, 2160: 60.0}

_PROC_ROUTE = Path("/proc/net/route")
_SYS_CLASS_NET = Path("/sys/class/net")

# Encoders that can carry HEVC/AV1 without melting the CPU.
_HARDWARE_ENCODERS = ("nvenc", "vaapi")


def nearest_height(height: int) -> int:
    """Snap a panel height to the closest height the stream can offer."""
    return min(_BITRATE_MBPS_BY_HEIGHT, key=lambda step: abs(step - height))


def encoder_index(gpus: list[dict]) -> int:
    """Index of the best real encoder in ``HostView.detect_gpus()`` order.

    The detected list ends with Vulkan (experimental) and Software, so "first
    entry" is not automatically a good automatic choice on a machine without a
    supported GPU; software encoding is.
    """
    for index, gpu in enumerate(gpus):
        if gpu.get("encoder") in _HARDWARE_ENCODERS:
            return index
    for index, gpu in enumerate(gpus):
        if gpu.get("encoder") == "software":
            return index
    return 0


def frame_rate(refresh_hz: int, hardware_encoder: bool) -> int:
    """60 FPS unless the panel is faster and the GPU can keep up."""
    if refresh_hz >= 120 and hardware_encoder:
        return 120
    if refresh_hz and refresh_hz < 60:
        return 30
    return 60


def bitrate_mbps(height: int, fps: int, wireless: bool) -> float:
    """Bitrate for a height/frame-rate pair, reduced on a wireless link."""
    value = _BITRATE_MBPS_BY_HEIGHT[nearest_height(height)]
    if fps >= 120:
        value *= 1.5
    if wireless:
        value *= 0.6
    return round(value, 1)


def wireless_link() -> bool:
    """True when the interface carrying the default route is Wi-Fi."""
    for interface in _default_route_interfaces():
        if (_SYS_CLASS_NET / interface / "wireless").exists():
            return True
    return False


def _default_route_interfaces() -> list[str]:
    try:
        lines = _PROC_ROUTE.read_text().splitlines()[1:]
    except OSError:
        return []
    interfaces = []
    for line in lines:
        fields = line.split()
        # Destination 00000000 is the default route.
        if len(fields) > 1 and fields[1] == "00000000":
            interfaces.append(fields[0])
    return interfaces


def host_defaults(*, gpus: list[dict], refresh_hz: int, height: int, wireless: bool) -> dict:
    """Sharing settings for this PC's GPU, display and network link."""
    index = encoder_index(gpus)
    hardware = bool(gpus) and gpus[index].get("encoder") in _HARDWARE_ENCODERS
    fps = frame_rate(refresh_hz, hardware)
    return {
        "gpu_index": index,
        "fps": fps,
        "bitrate_mbps": bitrate_mbps(height, fps, wireless),
        "efficient_codecs": hardware,
        "wifi_mode": wireless,
    }


def guest_defaults(*, width: int, height: int, refresh_hz: int, wireless: bool) -> dict:
    """Playing settings for this PC's screen and network link.

    The client decodes rather than encodes, so hardware decoding is assumed
    available and the picture follows the screen it will be shown on.
    """
    stream_height = nearest_height(height)
    # Keep the source aspect ratio the panel actually has.
    stream_width = round(width * stream_height / height / 2) * 2 if height else 1920
    fps = frame_rate(refresh_hz, True)
    return {
        "width": stream_width,
        "height": stream_height,
        "fps": fps,
        "bitrate_mbps": bitrate_mbps(stream_height, fps, wireless),
    }


def signature(*parts: object) -> str:
    """Stable identity of the detected resources, to spot a hardware change."""
    return "|".join(str(part) for part in parts)
