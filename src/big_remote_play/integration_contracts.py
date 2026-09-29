"""Versioned external integration contracts.

Keep these values synchronized with current upstream Sunshine and Moonlight
documentation. They intentionally live outside the UI so tests can guard
protocol and configuration drift.
"""

MOONLIGHT_PAIRING_PIN_LENGTH = 4
# Big Remote Play UDP lookup, not Sunshine/Moonlight authentication.
# Keep the existing wire length; distinguish the concepts in code and UI.
BRP_DISCOVERY_CODE_LENGTH = 4
MOONLIGHT_FPS_RANGE = (10, 480)
MOONLIGHT_BITRATE_KBPS_RANGE = (500, 500_000)
MOONLIGHT_DISPLAY_MODES = ("fullscreen", "borderless", "windowed")
MOONLIGHT_VIDEO_DECODERS = ("auto", "hardware", "software")
MOONLIGHT_VIDEO_CODECS = ("auto", "H.264", "HEVC", "AV1")

SUNSHINE_DEFAULT_BASE_PORT = 47_989
SUNSHINE_WEB_UI_PORT_OFFSET = 1
SUNSHINE_SERVICE_UNITS = (
    "sunshine.service",
    "app-dev.lizardbyte.app.Sunshine.service",
)
SUNSHINE_ADDRESS_FAMILIES = ("ipv4", "both")
SUNSHINE_CAPTURE_BACKENDS_LINUX = ("", "nvfbc", "wlr", "kms", "x11", "kwin", "portal")
SUNSHINE_ENCODERS_LINUX = ("", "nvenc", "vaapi", "vulkan", "software")


# Offsets from Sunshine's ``port`` option (docs: configuration → Network →
# port). Streaming needs every entry below except the web UI, which must stay
# private; it is listed separately so no caller can open it by accident.
SUNSHINE_STREAM_TCP_OFFSETS = (-5, 0, 21)  # HTTPS pairing, HTTP, RTSP
SUNSHINE_STREAM_UDP_OFFSETS = (9, 10, 11)  # video, control, audio
SUNSHINE_BASE_PORT_RANGE = (6, 65_514)  # keeps every derived port in 1..65535
# Big Remote Play's own UDP search-code lookup (not part of Sunshine).
BRP_DISCOVERY_UDP_PORT = 48_011


def _checked_base(base_port: int) -> int:
    base = int(base_port)
    low, high = SUNSHINE_BASE_PORT_RANGE
    if not low <= base <= high:
        raise ValueError("Sunshine base port is outside the supported range")
    return base


def sunshine_web_ui_port(base_port: int = SUNSHINE_DEFAULT_BASE_PORT) -> int:
    """Return Sunshine's HTTPS configuration/API port for a base GameStream port."""
    port = int(base_port) + SUNSHINE_WEB_UI_PORT_OFFSET
    if not 1 <= port <= 65_535:
        raise ValueError("Sunshine web UI port is outside the valid TCP range")
    return port


def sunshine_stream_ports(base_port: int = SUNSHINE_DEFAULT_BASE_PORT) -> dict[str, tuple[int, ...]]:
    """The TCP and UDP ports a remote Moonlight client needs, never the web UI."""
    base = _checked_base(base_port)
    return {
        "tcp": tuple(base + offset for offset in SUNSHINE_STREAM_TCP_OFFSETS),
        "udp": tuple(base + offset for offset in SUNSHINE_STREAM_UDP_OFFSETS),
    }
