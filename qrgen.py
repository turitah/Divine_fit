"""Tiny dependency-free QR code generator.

Encodes text in byte mode with error-correction level M for QR versions
1 through 10 and renders the finished matrix as an inline SVG string.

This exists because the project must run without third-party packages.
"""

# ---------------------------------------------------------------------------
# Galois field arithmetic (GF(256), primitive polynomial 0x11D)
# ---------------------------------------------------------------------------

def _gf_mul(x, y):
    result = 0
    for _ in range(8):
        if y & 1:
            result ^= x
        high = x & 0x80
        x = (x << 1) & 0xFF
        if high:
            x ^= 0x1D
        y >>= 1
    return result


def _gf_pow(power):
    result = 1
    for _ in range(power):
        result = _gf_mul(result, 2)
    return result


def _poly_mul(p, q):
    result = [0] * (len(p) + len(q) - 1)
    for i, a in enumerate(p):
        for j, b in enumerate(q):
            result[i + j] ^= _gf_mul(a, b)
    return result


def _rs_generator(nsym):
    generator = [1]
    for i in range(nsym):
        generator = _poly_mul(generator, [1, _gf_pow(i)])
    return generator


def _rs_ecc(data, nsym):
    generator = _rs_generator(nsym)
    message = list(data) + [0] * nsym
    for i in range(len(data)):
        coefficient = message[i]
        if coefficient:
            for j in range(1, len(generator)):
                message[i + j] ^= _gf_mul(generator[j], coefficient)
    return message[len(data):]


# ---------------------------------------------------------------------------
# Version / capacity tables (error-correction level M)
# ---------------------------------------------------------------------------

# version: (ecc codewords per block, [(block count, data codewords per block), ...])
_SPEC = {
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

_ALIGNMENT = {
    1: [],
    2: [6, 18],
    3: [6, 22],
    4: [6, 26],
    5: [6, 30],
    6: [6, 34],
    7: [6, 22, 38],
    8: [6, 24, 42],
    9: [6, 26, 46],
    10: [6, 28, 50],
}

_EC_LEVEL_M_BITS = 0b00


def _data_codewords(version):
    return sum(count * data for count, data in _SPEC[version][1])


def _pick_version(byte_length):
    for version in sorted(_SPEC):
        count_bits = 16 if version >= 10 else 8
        needed_bits = 4 + count_bits + (8 * byte_length)
        if needed_bits <= _data_codewords(version) * 8:
            return version
    raise ValueError("Text is too long to fit in a QR code")


# ---------------------------------------------------------------------------
# Bit stream
# ---------------------------------------------------------------------------

class _Bits:
    def __init__(self):
        self.bits = []

    def put(self, value, length):
        for i in range(length - 1, -1, -1):
            self.bits.append((value >> i) & 1)

    @property
    def length(self):
        return len(self.bits)

    def to_bytes(self):
        output = []
        for i in range(0, len(self.bits), 8):
            byte = 0
            for bit in self.bits[i:i + 8]:
                byte = (byte << 1) | bit
            output.append(byte)
        return output


def _encode_data(data, version):
    capacity = _data_codewords(version) * 8

    bits = _Bits()
    bits.put(0b0100, 4)  # byte mode
    bits.put(len(data), 16 if version >= 10 else 8)

    for byte in data:
        bits.put(byte, 8)

    bits.put(0, min(4, capacity - bits.length))

    if bits.length % 8:
        bits.put(0, 8 - (bits.length % 8))

    pad = (0xEC, 0x11)
    index = 0
    while bits.length < capacity:
        bits.put(pad[index % 2], 8)
        index += 1

    return bits.to_bytes()


def _make_codewords(data, version):
    ecc_length, groups = _SPEC[version]
    data_codewords = _encode_data(data, version)

    blocks = []
    position = 0
    for block_count, data_count in groups:
        for _ in range(block_count):
            block = data_codewords[position:position + data_count]
            position += data_count
            blocks.append((block, _rs_ecc(block, ecc_length)))

    result = []
    longest = max(len(block) for block, _ in blocks)
    for i in range(longest):
        for block, _ in blocks:
            if i < len(block):
                result.append(block[i])

    for i in range(ecc_length):
        for _, ecc in blocks:
            result.append(ecc[i])

    return result


# ---------------------------------------------------------------------------
# BCH helpers for format / version information
# ---------------------------------------------------------------------------

def _poly_remainder(value, poly):
    while value.bit_length() >= poly.bit_length():
        value ^= poly << (value.bit_length() - poly.bit_length())
    return value


def _format_bits(mask):
    data = (_EC_LEVEL_M_BITS << 3) | mask
    remainder = _poly_remainder(data << 10, 0x537)
    return ((data << 10) | remainder) ^ 0x5412


def _version_bits(version):
    remainder = _poly_remainder(version << 12, 0x1F25)
    return (version << 12) | remainder


# ---------------------------------------------------------------------------
# Mask patterns / penalty scoring
# ---------------------------------------------------------------------------

def _mask(mask, row, column):
    if mask == 0:
        return (row + column) % 2 == 0
    if mask == 1:
        return row % 2 == 0
    if mask == 2:
        return column % 3 == 0
    if mask == 3:
        return (row + column) % 3 == 0
    if mask == 4:
        return (row // 2 + column // 3) % 2 == 0
    if mask == 5:
        return (row * column) % 2 + (row * column) % 3 == 0
    if mask == 6:
        return ((row * column) % 2 + (row * column) % 3) % 2 == 0
    return ((row + column) % 2 + (row * column) % 3) % 2 == 0


_RUN_PENALTY_MIN = 5
_FINDER_A = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
_FINDER_B = [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1]


def _run_penalty(sequence):
    score = 0
    run = 1
    for i in range(1, len(sequence)):
        if sequence[i] == sequence[i - 1]:
            run += 1
        else:
            if run >= _RUN_PENALTY_MIN:
                score += 3 + (run - _RUN_PENALTY_MIN)
            run = 1
    if run >= _RUN_PENALTY_MIN:
        score += 3 + (run - _RUN_PENALTY_MIN)
    return score


def _finder_penalty(sequence):
    score = 0
    for i in range(len(sequence) - 10):
        window = sequence[i:i + 11]
        if window == _FINDER_A or window == _FINDER_B:
            score += 40
    return score


def _penalty(matrix):
    size = len(matrix)
    score = 0

    for row in matrix:
        score += _run_penalty(row)
        score += _finder_penalty(row)

    for column in range(size):
        values = [matrix[row][column] for row in range(size)]
        score += _run_penalty(values)
        score += _finder_penalty(values)

    for row in range(size - 1):
        for column in range(size - 1):
            if (matrix[row][column] == matrix[row][column + 1]
                    == matrix[row + 1][column] == matrix[row + 1][column + 1]):
                score += 3

    dark = sum(sum(row) for row in matrix)
    total = size * size
    score += 10 * (abs(100 * dark - 50 * total) // (5 * total))

    return score


# ---------------------------------------------------------------------------
# Matrix construction
# ---------------------------------------------------------------------------

class _Matrix:
    def __init__(self, version):
        self.version = version
        self.size = 17 + 4 * version
        self.modules = [[None] * self.size for _ in range(self.size)]

        self._place_finder(0, 0)
        self._place_finder(0, self.size - 7)
        self._place_finder(self.size - 7, 0)

        self._place_separators()
        self._place_timing()
        self._place_alignment()
        self._place_version_info()

        self.modules[self.size - 8][8] = 1

    def _set(self, row, column, value):
        if 0 <= row < self.size and 0 <= column < self.size:
            self.modules[row][column] = value

    def _place_finder(self, row, column):
        for r in range(7):
            for c in range(7):
                is_dark = (
                    r in (0, 6)
                    or c in (0, 6)
                    or (2 <= r <= 4 and 2 <= c <= 4)
                )
                self._set(row + r, column + c, 1 if is_dark else 0)

    def _place_separators(self):
        for i in range(8):
            self._set(7, i, 0)
            self._set(i, 7, 0)

            self._set(7, self.size - 1 - i, 0)
            self._set(i, self.size - 8, 0)

            self._set(self.size - 8, i, 0)
            self._set(self.size - 1 - i, 7, 0)

    def _place_timing(self):
        for i in range(8, self.size - 8):
            value = 1 if i % 2 == 0 else 0
            self._set(6, i, value)
            self._set(i, 6, value)

    def _place_alignment(self):
        centres = _ALIGNMENT[self.version]
        for row in centres:
            for column in centres:
                if (row, column) in (
                    (6, 6),
                    (6, self.size - 7),
                    (self.size - 7, 6),
                ):
                    continue

                for dr in range(-2, 3):
                    for dc in range(-2, 3):
                        is_dark = max(abs(dr), abs(dc)) != 1
                        self._set(
                            row + dr,
                            column + dc,
                            1 if is_dark else 0,
                        )

    def _place_version_info(self):
        if self.version < 7:
            return

        bits = _version_bits(self.version)
        for i in range(18):
            value = (bits >> i) & 1
            self._set(i // 3, i % 3 + self.size - 11, value)
            self._set(i % 3 + self.size - 11, i // 3, value)

    def place_format(self, mask):
        bits = _format_bits(mask)

        for i in range(15):
            value = (bits >> i) & 1
            if i < 6:
                self._set(i, 8, value)
            elif i < 8:
                self._set(i + 1, 8, value)
            else:
                self._set(self.size - 15 + i, 8, value)

        for i in range(15):
            value = (bits >> i) & 1
            if i < 8:
                self._set(8, self.size - i - 1, value)
            elif i < 9:
                self._set(8, 15 - i, value)
            else:
                self._set(8, 15 - i - 1, value)

        self.modules[self.size - 8][8] = 1

    def place_data(self, codewords, mask):
        row = self.size - 1
        direction = -1
        bit_index = 7
        byte_index = 0

        for column in range(self.size - 1, 0, -2):
            if column <= 6:
                column -= 1

            while True:
                for offset in range(2):
                    target = column - offset
                    if self.modules[row][target] is None:
                        dark = False
                        if byte_index < len(codewords):
                            dark = (
                                (codewords[byte_index] >> bit_index) & 1
                            ) == 1

                        if _mask(mask, row, target):
                            dark = not dark

                        self.modules[row][target] = 1 if dark else 0

                        bit_index -= 1
                        if bit_index == -1:
                            byte_index += 1
                            bit_index = 7

                row += direction
                if row < 0 or row >= self.size:
                    row -= direction
                    direction = -direction
                    break


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def make_qr_matrix(text):
    data = text.encode("utf-8")
    version = _pick_version(len(data))
    codewords = _make_codewords(data, version)

    best_matrix = None
    best_score = None

    for mask in range(8):
        matrix = _Matrix(version)
        matrix.place_format(mask)
        matrix.place_data(codewords, mask)

        score = _penalty(matrix.modules)
        if best_score is None or score < best_score:
            best_matrix = matrix
            best_score = score

    return best_matrix.modules


def make_qr_svg(text, scale=10, border=4):
    matrix = make_qr_matrix(text)
    size = len(matrix)
    total = size + (border * 2)

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{total * scale}" height="{total * scale}" '
        f'viewBox="0 0 {total} {total}" '
        'shape-rendering="crispEdges" role="img" '
        'aria-label="Divine Fit QR code">',
        f'<rect width="{total}" height="{total}" fill="#ffffff"/>',
    ]

    for row in range(size):
        column = 0
        while column < size:
            if matrix[row][column]:
                start = column
                while column < size and matrix[row][column]:
                    column += 1
                parts.append(
                    f'<rect x="{start + border}" y="{row + border}" '
                    f'width="{column - start}" height="1" fill="#101010"/>'
                )
            else:
                column += 1

    parts.append("</svg>")
    return "".join(parts)
