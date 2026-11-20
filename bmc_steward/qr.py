"""BMC-Steward: QR-Code ohne Fremdpaket (für die Seriennummer auf dem Audit-Blatt).

Byte-Modus (UTF-8), Fehlerkorrektur M, Versionen 1 bis 10 (bis 213 Byte). Aufbau nach ISO/IEC 18004:
Finder-, Timing- und Ausrichtungsmuster, Reed-Solomon über GF(256), Verschränkung der Blöcke, Maske mit der
geringsten Strafpunktzahl, Format- und Versionsinformation. encode() liefert die Modulmatrix (True = dunkel)
ohne Ruhezone; wer zeichnet, lässt rundherum vier Module frei.
"""

# Version: (Fehlerkorrektur-Codewörter je Block, [(Blöcke, Datencodewörter je Block), ...]) für Stufe M
_BLOCKS = {1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]), 5: (24, [(2, 43)]),
           6: (16, [(4, 27)]), 7: (18, [(4, 31)]), 8: (22, [(2, 38), (2, 39)]), 9: (22, [(3, 36), (2, 37)]),
           10: (26, [(4, 43), (1, 44)])}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42],
          9: [6, 26, 46], 10: [6, 28, 50]}
_FORMAT_M = 0                            # Formatbits der Stufe M


def _mul(x, y):
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _divisor(degree):
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(len(result)):
            result[j] = _mul(result[j], root)
            if j + 1 < len(result):
                result[j] ^= result[j + 1]
        root = _mul(root, 0x02)
    return result


def _remainder(data, divisor):
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _mul(coef, factor)
    return result


def _codewords(data):
    """Kleinste passende Version und die verschränkten Codewörter."""
    for version, (ec_len, groups) in _BLOCKS.items():
        capacity = sum(n * k for n, k in groups)
        count_bits = 8 if version < 10 else 16
        if 4 + count_bits + 8 * len(data) <= capacity * 8:
            break
    else:
        raise ValueError("zu lang für einen QR-Code bis Version 10")
    bits = [0, 1, 0, 0] + [(len(data) >> i) & 1 for i in reversed(range(count_bits))]
    for b in data:
        bits += [(b >> i) & 1 for i in reversed(range(8))]
    bits += [0] * min(4, capacity * 8 - len(bits))
    bits += [0] * (-len(bits) % 8)
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(words) < capacity:
        words.append(pad)
        pad ^= 0xEC ^ 0x11
    div = _divisor(ec_len)
    blocks, pos = [], 0
    for n, k in groups:
        for _ in range(n):
            blocks.append((words[pos:pos + k], _remainder(words[pos:pos + k], div)))
            pos += k
    out = []
    for i in range(max(len(d) for d, _ in blocks)):
        out += [d[i] for d, _ in blocks if i < len(d)]
    for i in range(ec_len):
        out += [e[i] for _, e in blocks]
    return version, out


_MASKS = [lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0,
          lambda x, y: (x + y) % 3 == 0, lambda x, y: (x // 3 + y // 2) % 2 == 0,
          lambda x, y: x * y % 2 + x * y % 3 == 0, lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
          lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0]


class _Matrix:
    def __init__(self, version):
        self.version = version
        self.size = n = 17 + 4 * version
        self.m = [[False] * n for _ in range(n)]
        self.fixed = [[False] * n for _ in range(n)]

    def put(self, x, y, dark):
        self.m[y][x] = dark
        self.fixed[y][x] = True

    def functions(self):
        n = self.size
        for i in range(n):
            self.put(6, i, i % 2 == 0)
            self.put(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < n and 0 <= y < n:
                        self.put(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = _ALIGN[self.version]
        last = len(pos) - 1
        for i, cy in enumerate(pos):
            for j, cx in enumerate(pos):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.put(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)
        self.format_bits(0)
        if self.version >= 7:
            rem = self.version
            for _ in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            bits = self.version << 12 | rem
            for i in range(18):
                bit = (bits >> i) & 1 == 1
                a, b = n - 11 + i % 3, i // 3
                self.put(a, b, bit)
                self.put(b, a, bit)

    def format_bits(self, mask):
        n = self.size
        data = _FORMAT_M << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412

        def bit(i):
            return (bits >> i) & 1 == 1
        for i in range(6):
            self.put(8, i, bit(i))
        self.put(8, 7, bit(6))
        self.put(8, 8, bit(7))
        self.put(7, 8, bit(8))
        for i in range(9, 15):
            self.put(14 - i, 8, bit(i))
        for i in range(8):
            self.put(n - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.put(8, n - 15 + i, bit(i))
        self.put(8, n - 8, True)

    def place(self, words):
        n, i, total = self.size, 0, len(words) * 8
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(n):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = n - 1 - vert if upward else vert
                    if not self.fixed[y][x] and i < total:
                        self.m[y][x] = (words[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def apply_mask(self, mask):
        f = _MASKS[mask]
        for y in range(self.size):
            for x in range(self.size):
                if not self.fixed[y][x] and f(x, y):
                    self.m[y][x] = not self.m[y][x]

    def penalty(self):
        m, n, score = self.m, self.size, 0
        lines = m + [list(col) for col in zip(*m)]
        for line in lines:
            run, prev = 0, None
            for v in line:
                run = run + 1 if v == prev else 1
                prev = v
                if run == 5:
                    score += 3
                elif run > 5:
                    score += 1
            s = "".join("1" if v else "0" for v in line)
            score += 40 * (s.count("10111010000") + s.count("00001011101"))
        for y in range(n - 1):
            for x in range(n - 1):
                if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    score += 3
        dark = sum(map(sum, m))
        score += 10 * (abs(dark * 20 - n * n * 10) // (n * n))
        return score


def encode(text):
    """Modulmatrix (Liste von Zeilen, True = dunkel) für `text` (UTF-8, Byte-Modus, Stufe M)."""
    version, words = _codewords(text.encode("utf-8"))
    q = _Matrix(version)
    q.functions()
    q.place(words)
    best, best_score = 0, None
    for mask in range(8):
        q.apply_mask(mask)
        q.format_bits(mask)
        score = q.penalty()
        if best_score is None or score < best_score:
            best, best_score = mask, score
        q.apply_mask(mask)              # rückgängig (XOR)
    q.apply_mask(best)
    q.format_bits(best)
    return q.m
