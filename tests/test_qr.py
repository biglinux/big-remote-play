"""The built-in QR encoder produces codes a real decoder reads back."""

from __future__ import annotations

import shutil
import struct
import subprocess
import zlib

import pytest

from big_remote_play.utils import qr


def _png(matrix, path, scale=6, quiet=4):
    size = len(matrix)
    width = (size + 2 * quiet) * scale
    rows = []
    for py in range(width):
        y = py // scale - quiet
        row = bytearray([0])
        for px in range(width):
            x = px // scale - quiet
            row.append(0 if 0 <= x < size and 0 <= y < size and matrix[y][x] else 255)
        rows.append(bytes(row))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, width, 8, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


SAMPLES = [
    "8056c2e21c000001",
    "https://login.tailscale.com/admin/invite/AbCdEf123456",
    "https://vpn.example.test",
    "Big Remote Play · ação 日本語",
    "x" * 100,
    "https://example.test/" + "a1B2-_" * 32,  # version 10
]


@pytest.mark.parametrize("text", SAMPLES)
def test_matrix_has_the_size_of_a_valid_version(text):
    matrix = qr.encode(text)
    version = (len(matrix) - 17) // 4
    assert 1 <= version <= qr.MAX_VERSION and len(matrix) == 17 + 4 * version
    assert all(len(row) == len(matrix) for row in matrix)
    # Finder pattern corners are dark, their separators light.
    assert matrix[0][0] and matrix[0][6] and not matrix[0][7] and not matrix[7][0]


@pytest.mark.skipif(shutil.which("zbarimg") is None, reason="zbarimg (zbar) not installed")
@pytest.mark.parametrize("text", SAMPLES)
def test_a_real_decoder_reads_the_text_back(tmp_path, text):
    image = tmp_path / "code.png"
    _png(qr.encode(text), image)
    decoded = subprocess.run(["zbarimg", "-q", "--raw", str(image)], capture_output=True, text=True, timeout=30).stdout.rstrip("\n")
    assert decoded == text


def test_text_beyond_the_supported_size_is_refused():
    with pytest.raises(qr.QrTooLong):
        qr.encode("x" * 214)


def test_credentials_are_never_turned_into_a_qr_code():
    from gi.repository import Gdk

    if Gdk.Display.get_default() is None:
        pytest.skip("GTK display required")
    from big_remote_play.ui.network_common import qr_code

    for secret in ("tskey-auth-kSECRET-x", "hskey-api-SECRET"):
        with pytest.raises(ValueError):
            qr_code(secret)
    assert qr_code("8056c2e21c000001")._brp_qr_text == "8056c2e21c000001"
