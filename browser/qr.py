"""QR codes for page addresses (Send to Your Phone), per ISO/IEC 18004.

Byte mode with medium error correction, the smallest version that fits, and
the mask with the lowest penalty. Small and self-contained, so Steed needs no
extra package for it; tests decode the output with zbar.
"""

# Error-correction codewords per block and number of blocks, level M, by
# version (index 0 unused).
_ECC_PER_BLOCK = (
    -1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26,
    26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28,
    28, 28, 28,
)  # fmt: skip
_BLOCKS = (
    -1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17,
    17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49,
)  # fmt: skip
_LEVEL_BITS = 0  # Format bits for level M.


class QrTooLong(ValueError):
    pass


def _raw_modules(version):
    result = (16 * version + 128) * version + 64
    if version >= 2:
        count = version // 7 + 2
        result -= (25 * count - 10) * count - 55
        if version >= 7:
            result -= 36
    return result


def _data_codewords(version):
    return _raw_modules(version) // 8 - _ECC_PER_BLOCK[version] * _BLOCKS[version]


def _multiply(x, y):
    """Multiply in GF(2^8) modulo x^8 + x^4 + x^3 + x^2 + 1."""
    result = 0
    for i in range(7, -1, -1):
        result = (result << 1) ^ ((result >> 7) * 0x11D)
        result ^= ((y >> i) & 1) * x
    return result


def _divisor(degree):
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _multiply(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _multiply(root, 0x02)
    return result


def _remainder(data, divisor):
    result = [0] * len(divisor)
    for byte in data:
        factor = byte ^ result.pop(0)
        result.append(0)
        for i, coefficient in enumerate(divisor):
            result[i] ^= _multiply(coefficient, factor)
    return result


def _codewords(data):
    """(version, data and error-correction codewords, interleaved)."""
    # Text that isn't plain ASCII says it is UTF-8 (ECI 26), so scanners
    # don't guess another character set.
    eci = 12 if any(byte > 0x7F for byte in data) else 0
    for version in range(1, 41):
        count_bits = 8 if version <= 9 else 16
        capacity = _data_codewords(version) * 8
        if eci + 4 + count_bits + len(data) * 8 <= capacity:
            break
    else:
        raise QrTooLong("This address is too long for a QR code.")
    bits = []

    def put(value, length):
        bits.extend((value >> i) & 1 for i in range(length - 1, -1, -1))

    if eci:
        put(0b0111, 4)
        put(26, 8)
    put(0b0100, 4)
    put(len(data), count_bits)
    for byte in data:
        put(byte, 8)
    put(0, min(4, capacity - len(bits)))
    put(0, -len(bits) % 8)
    words = [int("".join(map(str, bits[i : i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(words) < capacity // 8:
        words.append(pad)
        pad ^= 0xEC ^ 0x11
    blocks_count, ecc_length = _BLOCKS[version], _ECC_PER_BLOCK[version]
    raw = _raw_modules(version) // 8
    short_blocks = blocks_count - raw % blocks_count
    short_length = raw // blocks_count
    divisor = _divisor(ecc_length)
    blocks, k = [], 0
    for i in range(blocks_count):
        length = short_length - ecc_length + (0 if i < short_blocks else 1)
        block = words[k : k + length]
        k += length
        ecc = _remainder(block, divisor)
        if i < short_blocks:
            block.append(0)  # Placeholder, skipped when interleaving.
        blocks.append(block + ecc)
    result = []
    for i in range(len(blocks[0])):
        for j, block in enumerate(blocks):
            if i != short_length - ecc_length or j >= short_blocks:
                result.append(block[i])
    return version, result


class _Grid:
    def __init__(self, version):
        self.version = version
        self.size = version * 4 + 17
        self.dark = [[False] * self.size for _ in range(self.size)]
        self.fixed = [[False] * self.size for _ in range(self.size)]

    def set(self, x, y, dark):
        self.dark[y][x] = dark
        self.fixed[y][x] = True

    def alignment_positions(self):
        if self.version == 1:
            return []
        count = self.version // 7 + 2
        step = (self.version * 8 + count * 3 + 5) // (count * 4 - 4) * 2
        return [6] + [self.size - 7 - i * step for i in range(count - 1)][::-1]

    def function_patterns(self):
        size = self.size
        for i in range(size):
            self.set(6, i, i % 2 == 0)
            self.set(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < size and 0 <= y < size:
                        self.set(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        positions = self.alignment_positions()
        last = len(positions) - 1
        for i, x in enumerate(positions):
            for j, y in enumerate(positions):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set(x + dx, y + dy, max(abs(dx), abs(dy)) != 1)
        self.format_bits(0)
        if self.version >= 7:
            remainder = self.version
            for _ in range(12):
                remainder = (remainder << 1) ^ ((remainder >> 11) * 0x1F25)
            bits = self.version << 12 | remainder
            for i in range(18):
                bit = (bits >> i) & 1 == 1
                a, b = size - 11 + i % 3, i // 3
                self.set(a, b, bit)
                self.set(b, a, bit)

    def format_bits(self, mask):
        data = _LEVEL_BITS << 3 | mask
        remainder = data
        for _ in range(10):
            remainder = (remainder << 1) ^ ((remainder >> 9) * 0x537)
        bits = (data << 10 | remainder) ^ 0x5412

        def bit(i):
            return (bits >> i) & 1 == 1

        size = self.size
        for i in range(6):
            self.set(8, i, bit(i))
        self.set(8, 7, bit(6))
        self.set(8, 8, bit(7))
        self.set(7, 8, bit(8))
        for i in range(9, 15):
            self.set(14 - i, 8, bit(i))
        for i in range(8):
            self.set(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set(8, size - 15 + i, bit(i))
        self.set(8, size - 8, True)

    def place(self, codewords):
        size, i, total = self.size, 0, len(codewords) * 8
        right = size - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vertical in range(size):
                for j in range(2):
                    x = right - j
                    upward = (right + 1) & 2 == 0
                    y = size - 1 - vertical if upward else vertical
                    if not self.fixed[y][x] and i < total:
                        self.dark[y][x] = (codewords[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def apply_mask(self, mask):
        test = (
            lambda x, y: (x + y) % 2 == 0,
            lambda x, y: y % 2 == 0,
            lambda x, y: x % 3 == 0,
            lambda x, y: (x + y) % 3 == 0,
            lambda x, y: (x // 3 + y // 2) % 2 == 0,
            lambda x, y: x * y % 2 + x * y % 3 == 0,
            lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
            lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
        )[mask]
        for y in range(self.size):
            for x in range(self.size):
                if not self.fixed[y][x] and test(x, y):
                    self.dark[y][x] = not self.dark[y][x]

    def penalty(self):
        """Rules 1, 2 and 4 of the standard, plus its finder-like runs."""
        size, score = self.size, 0
        lines = [row for row in self.dark] + [
            [self.dark[y][x] for y in range(size)] for x in range(size)
        ]
        for line in lines:
            run, previous = 0, None
            for value in line:
                if value == previous:
                    run += 1
                else:
                    if run >= 5:
                        score += run - 2
                    run, previous = 1, value
            if run >= 5:
                score += run - 2
            text = "".join("1" if v else "0" for v in line)
            for pattern in ("10111010000", "00001011101"):
                score += 40 * text.count(pattern)
        for y in range(size - 1):
            for x in range(size - 1):
                value = self.dark[y][x]
                if (
                    value == self.dark[y][x + 1]
                    and value == self.dark[y + 1][x]
                    and value == self.dark[y + 1][x + 1]
                ):
                    score += 3
        dark = sum(map(sum, self.dark))
        total = size * size
        score += (abs(dark * 20 - total * 10) + total - 1) // total * 10 - 10
        return score


def encode(text):
    """The QR code for text as rows of booleans (True is dark), without the
    quiet zone around it."""
    version, codewords = _codewords(text.encode("utf-8"))
    best = None
    for mask in range(8):
        grid = _Grid(version)
        grid.function_patterns()
        grid.place(codewords)
        grid.apply_mask(mask)
        grid.format_bits(mask)
        score = grid.penalty()
        if best is None or score < best[0]:
            best = (score, grid)
    return best[1].dark
