"""A small, dependency-free QR Code (Model 2) encoder.

Scope is deliberately narrow: byte mode, error-correction level M, versions
1–10 (up to 213 bytes). That covers what Big Remote Play shows as a QR code —
a ZeroTier Network ID, an invitation link or a server address — without
adding a runtime dependency. Administrative credentials are never encoded.

The algorithm follows ISO/IEC 18004: Reed–Solomon over GF(256) with the
polynomial 0x11D, block interleaving, the eight data masks with the standard
penalty rules, BCH-protected format and version information. Output is a
square matrix of booleans (True = dark), without the quiet zone.
"""

from __future__ import annotations

# Error correction level M: (EC codewords per block, [(block count, data codewords)])
_BLOCKS_M = {
    1: (10, [(1, 16)]),
    2: (16, [(1, 28)]),
    3: (26, [(1, 44)]),
    4: (18, [(2, 32)]),
    5: (24, [(2, 43)]),
    6: (16, [(4, 27)]),
    7: (18, [(4, 31)]),
    8: (22, [(2, 38), (2, 39)]),
    9: (22, [(3, 36), (2, 37)]),
    10: (26, [(4, 43), (1, 44)]),
}
_ALIGNMENT = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}
_FORMAT_LEVEL_M = 0b00
MAX_VERSION = 10


class QrTooLong(ValueError):
    """The text does not fit in the supported versions."""


# ── GF(256) and Reed–Solomon ──────────────────────────────────────────────


def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z & 0xFF


def _rs_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for byte in data:
        factor = byte ^ result.pop(0)
        result.append(0)
        for index, coefficient in enumerate(divisor):
            result[index] ^= _gf_mul(coefficient, factor)
    return result


# ── data encoding ─────────────────────────────────────────────────────────


def _capacity(version: int) -> int:
    _ec, groups = _BLOCKS_M[version]
    return sum(count * length for count, length in groups)


def _choose_version(length: int) -> int:
    for version in range(1, MAX_VERSION + 1):
        count_bits = 8 if version < 10 else 16
        if 4 + count_bits + 8 * length <= 8 * _capacity(version):
            return version
    raise QrTooLong(f"{length} bytes do not fit in a version {MAX_VERSION}-M QR code")


def _data_codewords(payload: bytes, version: int) -> list[int]:
    bits: list[int] = []

    def put(value: int, width: int) -> None:
        bits.extend((value >> shift) & 1 for shift in range(width - 1, -1, -1))

    put(0b0100, 4)  # byte mode
    put(len(payload), 8 if version < 10 else 16)
    for byte in payload:
        put(byte, 8)
    capacity_bits = 8 * _capacity(version)
    put(0, min(4, capacity_bits - len(bits)))  # terminator
    put(0, (-len(bits)) % 8)
    codewords = [int("".join(map(str, bits[i : i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(codewords) < _capacity(version):
        codewords.append(pad)
        pad ^= 0xEC ^ 0x11
    return codewords


def _interleave(codewords: list[int], version: int) -> list[int]:
    ec_length, groups = _BLOCKS_M[version]
    divisor = _rs_divisor(ec_length)
    blocks: list[list[int]] = []
    offset = 0
    for count, length in groups:
        for _ in range(count):
            blocks.append(codewords[offset : offset + length])
            offset += length
    ec_blocks = [_rs_remainder(block, divisor) for block in blocks]
    result: list[int] = []
    for index in range(max(len(block) for block in blocks)):
        result.extend(block[index] for block in blocks if index < len(block))
    for index in range(ec_length):
        result.extend(block[index] for block in ec_blocks)
    return result


# ── matrix ────────────────────────────────────────────────────────────────


class _Matrix:
    def __init__(self, version: int) -> None:
        self.version = version
        self.size = 17 + 4 * version
        self.dark = [[False] * self.size for _ in range(self.size)]
        self.function = [[False] * self.size for _ in range(self.size)]

    def set_function(self, x: int, y: int, dark: bool) -> None:
        self.dark[y][x] = dark
        self.function[y][x] = True

    def draw_function_patterns(self) -> None:
        size = self.size
        for i in range(size):
            self.set_function(6, i, i % 2 == 0)
            self.set_function(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < size and 0 <= y < size:
                        self.set_function(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        positions = _ALIGNMENT[self.version]
        last = len(positions) - 1
        for i, cy in enumerate(positions):
            for j, cx in enumerate(positions):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue  # the finder patterns occupy these corners
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set_function(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format(0)  # reserve the area; redrawn with the chosen mask
        if self.version >= 7:
            remainder = self.version
            for _ in range(12):
                remainder = (remainder << 1) ^ ((remainder >> 11) * 0x1F25)
            bits = self.version << 12 | remainder
            for i in range(18):
                dark = (bits >> i) & 1 == 1
                a, b = size - 11 + i % 3, i // 3
                self.set_function(a, b, dark)
                self.set_function(b, a, dark)

    def draw_format(self, mask: int) -> None:
        data = _FORMAT_LEVEL_M << 3 | mask
        remainder = data
        for _ in range(10):
            remainder = (remainder << 1) ^ ((remainder >> 9) * 0x537)
        bits = (data << 10 | remainder) ^ 0x5412
        size = self.size

        def bit(i: int) -> bool:
            return (bits >> i) & 1 == 1

        for i in range(6):
            self.set_function(8, i, bit(i))
        self.set_function(8, 7, bit(6))
        self.set_function(8, 8, bit(7))
        self.set_function(7, 8, bit(8))
        for i in range(9, 15):
            self.set_function(14 - i, 8, bit(i))
        for i in range(8):
            self.set_function(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set_function(8, size - 15 + i, bit(i))
        self.set_function(8, size - 8, True)  # the dark module

    def draw_codewords(self, codewords: list[int]) -> None:
        size = self.size
        index = 0
        total = len(codewords) * 8
        right = size - 1
        while right >= 1:
            if right == 6:
                right = 5
            upward = ((right + 1) & 2) == 0
            for vertical in range(size):
                y = size - 1 - vertical if upward else vertical
                for offset in range(2):
                    x = right - offset
                    if not self.function[y][x] and index < total:
                        self.dark[y][x] = (codewords[index >> 3] >> (7 - (index & 7))) & 1 == 1
                        index += 1
            right -= 2

    def apply_mask(self, mask: int) -> None:
        for y in range(self.size):
            for x in range(self.size):
                if self.function[y][x]:
                    continue
                if (
                    (mask == 0 and (x + y) % 2 == 0)
                    or (mask == 1 and y % 2 == 0)
                    or (mask == 2 and x % 3 == 0)
                    or (mask == 3 and (x + y) % 3 == 0)
                    or (mask == 4 and (x // 3 + y // 2) % 2 == 0)
                    or (mask == 5 and x * y % 2 + x * y % 3 == 0)
                    or (mask == 6 and (x * y % 2 + x * y % 3) % 2 == 0)
                    or (mask == 7 and ((x + y) % 2 + x * y % 3) % 2 == 0)
                ):
                    self.dark[y][x] = not self.dark[y][x]

    def penalty(self) -> int:
        size = self.size
        grid = self.dark
        score = 0
        lines = [row for row in grid] + [[grid[y][x] for y in range(size)] for x in range(size)]
        finder_a = [True, False, True, True, True, False, True, False, False, False, False]
        finder_b = finder_a[::-1]
        for line in lines:
            run = 1
            for index in range(1, size + 1):
                if index < size and line[index] == line[index - 1]:
                    run += 1
                    continue
                if run >= 5:
                    score += 3 + (run - 5)
                run = 1
            for index in range(size - 10):
                window = line[index : index + 11]
                if window == finder_a or window == finder_b:
                    score += 40
        for y in range(size - 1):
            for x in range(size - 1):
                colour = grid[y][x]
                if colour == grid[y][x + 1] == grid[y + 1][x] == grid[y + 1][x + 1]:
                    score += 3
        dark = sum(sum(row) for row in grid)
        total = size * size
        score += 10 * (abs(dark * 20 - total * 10) // total)
        return score


def encode(text: str) -> list[list[bool]]:
    """Return the QR matrix for ``text`` (UTF-8, byte mode, level M)."""
    payload = text.encode("utf-8")
    version = _choose_version(len(payload))
    codewords = _interleave(_data_codewords(payload, version), version)
    best: tuple[int, list[list[bool]]] | None = None
    for mask in range(8):
        matrix = _Matrix(version)
        matrix.draw_function_patterns()
        matrix.draw_codewords(codewords)
        matrix.apply_mask(mask)
        matrix.draw_format(mask)
        score = matrix.penalty()
        if best is None or score < best[0]:
            best = (score, [row[:] for row in matrix.dark])
    assert best is not None
    return best[1]
