"""Network-script marker protocol: capture is locale-independent."""

from big_remote_play.utils.script_protocol import parse_script_line


def test_data_marker() -> None:
    assert parse_script_line("BRP_DATA network_id=abc123") == ("data", "network_id", "abc123")


def test_data_marker_value_with_url() -> None:
    assert parse_script_line("BRP_DATA api_url=https://api.zerotier.com/api/v1") == (
        "data", "api_url", "https://api.zerotier.com/api/v1",
    )


def test_phase_marker_and_clamp() -> None:
    assert parse_script_line("BRP_PHASE 0.6") == ("phase", 0.6)
    assert parse_script_line("BRP_PHASE 1.5") == ("phase", 1.0)


def test_text_strips_ansi() -> None:
    assert parse_script_line("\x1b[0;32mChecking dependencies...\x1b[0m") == (
        "text", "Checking dependencies...",
    )


def test_capture_is_locale_independent() -> None:
    """Same data captured whether prose is English or Portuguese."""
    def capture(lines):
        captured, phase = {}, 0.1
        for ln in lines:
            kind = parse_script_line(ln)
            if kind[0] == "data":
                captured[kind[1]] = kind[2]
            elif kind[0] == "phase":
                phase = kind[1]
        return captured, phase

    en = [
        "Creating network via API...",
        "BRP_DATA network_id=NET42",
        "BRP_PHASE 0.6",
        "BRP_DATA public_ip=1.2.3.4",
        "BRP_PHASE 0.95",
    ]
    pt = [
        "Criando rede via API...",
        "BRP_DATA network_id=NET42",
        "BRP_PHASE 0.6",
        "BRP_DATA public_ip=1.2.3.4",
        "BRP_PHASE 0.95",
    ]
    assert capture(en) == capture(pt) == ({"network_id": "NET42", "public_ip": "1.2.3.4"}, 0.95)
