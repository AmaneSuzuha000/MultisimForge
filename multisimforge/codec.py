"""Multisim .ms14 container codec.

Format: 36-byte magic + u64 total raw length + one or more sections (u32 raw length, u32 compressed length, PKWare DCL Implode data),
each section with a raw length of 900000. Multisim itself uses ASCII literal encoding and a 4 KB dictionary (first two bytes 1, 6); the same is done here.
The code tables are taken from the PKWare DCL format (the same as StormLib / node-pkware).
"""
import struct

MAGIC = b"MSMCompressedElectronicsWorkbenchXML"
SECTION = 900_000
WINDOW = 4096          # 6 low bits of the dictionary → maximum distance 4096
MAX_LEN = 518          # 519 is the end code
MAX_SECTION = 4 * SECTION        # a section may never be bigger than a few chunks
MAX_FILE = 256 * 1024 * 1024     # refuse to allocate more than this for one design

CH_BITS_ASC = (
    11, 12, 12, 12, 12, 12, 12, 12, 12, 8, 7, 12, 12, 7, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12,
    12, 12, 13, 12, 12, 12, 12, 12, 4, 10, 8, 12, 10, 12, 10, 8, 7, 7, 8, 9, 7, 6, 7, 8, 7, 6,
    7, 7, 7, 7, 8, 7, 7, 8, 8, 12, 11, 7, 9, 11, 12, 6, 7, 6, 6, 5, 7, 8, 8, 6, 11, 9, 6, 7, 6,
    6, 7, 11, 6, 6, 6, 7, 9, 8, 9, 9, 11, 8, 11, 9, 12, 8, 12, 5, 6, 6, 6, 5, 6, 6, 6, 5, 11, 7,
    5, 6, 5, 5, 6, 10, 5, 5, 5, 5, 8, 7, 8, 8, 10, 11, 11, 12, 12, 12, 13, 13, 13, 13, 13, 13,
    13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13,
    13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 12, 12, 12, 12,
    12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12,
    12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 13, 12,
    13, 13, 13, 12, 13, 13, 13, 12, 13, 13, 13, 13, 12, 13, 13, 13, 12, 12, 12, 13, 13, 13, 13,
    13, 13, 13, 13, 13, 13, 13,
)
CH_CODE_ASC = (
    1168, 4064, 2016, 3040, 992, 3552, 1504, 2528, 480, 184, 98, 3808, 1760, 34, 2784, 736,
    3296, 1248, 2272, 224, 3936, 1888, 2912, 864, 3424, 1376, 4672, 2400, 352, 3680, 1632, 2656,
    15, 592, 56, 608, 80, 3168, 912, 216, 66, 2, 88, 432, 124, 41, 60, 152, 92, 9, 28, 108, 44,
    76, 24, 12, 116, 232, 104, 1120, 144, 52, 176, 1808, 2144, 49, 84, 17, 33, 23, 20, 168, 40,
    1, 784, 304, 62, 100, 30, 46, 36, 1296, 14, 54, 22, 68, 48, 200, 464, 208, 272, 72, 1552,
    336, 96, 136, 4000, 7, 38, 6, 58, 27, 26, 42, 10, 11, 528, 4, 19, 50, 3, 29, 18, 400, 13,
    21, 5, 25, 8, 120, 240, 112, 656, 1040, 16, 1952, 2976, 928, 576, 7232, 3136, 5184, 1088,
    6208, 2112, 4160, 64, 8064, 3968, 6016, 1920, 7040, 2944, 4992, 896, 7552, 3456, 5504, 1408,
    6528, 2432, 4480, 384, 7808, 3712, 5760, 1664, 6784, 2688, 4736, 640, 7296, 3200, 5248,
    1152, 6272, 2176, 4224, 128, 7936, 3840, 5888, 1792, 6912, 2816, 4864, 3488, 1440, 2464,
    416, 3744, 1696, 2720, 672, 3232, 1184, 2208, 160, 3872, 1824, 2848, 800, 3360, 1312, 2336,
    288, 3616, 1568, 2592, 544, 3104, 1056, 2080, 32, 4032, 1984, 3008, 960, 3520, 1472, 2496,
    448, 3776, 1728, 2752, 704, 3264, 1216, 2240, 192, 3904, 1856, 2880, 832, 768, 3392, 7424,
    3328, 5376, 1344, 1280, 6400, 2304, 2368, 4352, 256, 7680, 3584, 320, 5632, 1536, 6656,
    3648, 1600, 2624, 2560, 4608, 512, 7168, 3072, 5120, 1024, 6144, 2048, 4096, 0,
)
LEN_BITS = (3, 2, 3, 3, 4, 4, 4, 5, 5, 5, 5, 6, 6, 6, 7, 7)
LEN_CODE = (5, 3, 1, 6, 10, 2, 12, 20, 4, 24, 8, 48, 16, 32, 64, 0)
EX_LEN_BITS = (0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8)
LEN_BASE = (0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 14, 22, 38, 70, 134, 262)
DIST_BITS = (
    2, 4, 4, 5, 5, 5, 5, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 7, 7, 7, 7, 7, 7, 7, 7, 7,
    7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8,
    8, 8,
)
DIST_CODE = (
    3, 13, 5, 25, 9, 17, 1, 62, 30, 46, 14, 54, 22, 38, 6, 58, 26, 42, 10, 50, 18, 34, 66, 2,
    124, 60, 92, 28, 108, 44, 76, 12, 116, 52, 84, 20, 100, 36, 68, 4, 120, 56, 88, 24, 104, 40,
    72, 8, 240, 112, 176, 48, 208, 80, 144, 16, 224, 96, 160, 32, 192, 64, 128, 0,
)


def _lut(codes, bits, width):
    """Codewords are stored least-significant-bit first; a width-bit window indexes the table directly to get (symbol, code length)."""
    table = [None] * (1 << width)
    for sym, (code, n) in enumerate(zip(codes, bits)):
        for fill in range(1 << (width - n)):
            table[code | (fill << n)] = (sym, n)
    return table


_LIT = _lut(CH_CODE_ASC, CH_BITS_ASC, 13)
_LEN = _lut(LEN_CODE, LEN_BITS, 7)
_DIST = _lut(DIST_CODE, DIST_BITS, 8)

# lengths 2..519 → (codeword, bit count): 1 flag bit + length code + extra bits
_LEN_TOKEN = {}
for _i in range(16):
    for _extra in range(1 << EX_LEN_BITS[_i]):
        _LEN_TOKEN[LEN_BASE[_i] + _extra + 2] = (
            (_extra << (LEN_BITS[_i] + 1)) | (LEN_CODE[_i] << 1) | 1, LEN_BITS[_i] + EX_LEN_BITS[_i] + 1)
_LIT_TOKEN = [(CH_CODE_ASC[b] << 1, CH_BITS_ASC[b] + 1) for b in range(256)]
_DIST_TOKEN = [(DIST_CODE[d >> 6] | ((d & 0x3F) << DIST_BITS[d >> 6]), DIST_BITS[d >> 6] + 6) for d in range(WINDOW)]
_U32 = struct.Struct("<I").unpack_from


def explode(block: bytes, *, max_output: int | None = None) -> bytes:
    """Decompress one DCL Implode block, bounding actual output as it expands."""
    if max_output is not None and max_output < 0:
        raise ValueError("Declared section length cannot be negative")
    if len(block) < 2:
        raise ValueError("Corrupt compressed data: missing DCL header")
    ascii_lit, dict_bits = block[0], block[1]
    if ascii_lit > 1 or not 4 <= dict_bits <= 6:
        raise ValueError("Not DCL Implode data")
    bit_limit = (len(block) - 2) * 8
    buf = bytes(block[2:]) + b"\x00" * 8
    out = bytearray()
    out_append = out.append
    lit_tbl = _LIT
    len_tbl = _LEN
    dist_tbl = _DIST
    ex_bits = EX_LEN_BITS
    len_base = LEN_BASE
    unpack_u32 = _U32
    dist_mask = (1 << dict_bits) - 1
    pos = 0
    while True:
        if pos >= bit_limit:
            raise ValueError("Corrupt compressed data: missing end marker")
        v = unpack_u32(buf, pos >> 3)[0] >> (pos & 7)
        if not v & 1:                                   # literal
            if max_output is not None and len(out) >= max_output:
                raise ValueError("Decompressed data exceeds declared section length")
            if ascii_lit:
                entry = lit_tbl[(v >> 1) & 0x1FFF]
                if entry is None:
                    raise ValueError("Corrupt compressed data: invalid literal code")
                sym, n = entry
                pos += 1 + n
                if pos > bit_limit:
                    raise ValueError("Corrupt compressed data: truncated literal")
                out_append(sym)
            else:
                pos += 9
                if pos > bit_limit:
                    raise ValueError("Corrupt compressed data: truncated literal")
                out_append((v >> 1) & 0xFF)
            continue
        entry = len_tbl[(v >> 1) & 0x7F]
        if entry is None:
            raise ValueError("Corrupt compressed data: invalid length code")
        i, n = entry
        e = ex_bits[i]
        length = len_base[i] + ((v >> (1 + n)) & ((1 << e) - 1)) + 2
        pos += 1 + n + e
        if pos > bit_limit:
            raise ValueError("Corrupt compressed data: truncated length code")
        if length == 519:
            break
        if max_output is not None and len(out) + length > max_output:
            raise ValueError("Decompressed data exceeds declared section length")
        if pos >= bit_limit:
            raise ValueError("Corrupt compressed data: truncated back-reference")
        v = unpack_u32(buf, pos >> 3)[0] >> (pos & 7)
        entry = dist_tbl[v & 0xFF]
        if entry is None:
            raise ValueError("Corrupt compressed data: invalid distance code")
        hi, n = entry
        if length == 2:
            low_bits, mask = 2, 3
        else:
            low_bits, mask = dict_bits, dist_mask
        dist = ((hi << low_bits) | ((v >> n) & mask)) + 1
        pos += n + low_bits
        if pos > bit_limit:
            raise ValueError("Corrupt compressed data: truncated distance code")
        start = len(out) - dist
        if start < 0:
            raise ValueError("Corrupt compressed data: distance out of range")
        if dist >= length:
            out += out[start:start + length]
        elif dist == 1:
            out.extend(out[start:start + 1] * length)
        else:                                           # overlapping copy
            rem = length
            while rem > 0:
                take = dist if dist < rem else rem
                cur = len(out) - dist
                out += out[cur:cur + take]
                rem -= take
    return bytes(out)


def _match_len(data, a, b, limit):
    """Length of the common prefix of data[a:] and data[b:] (at most limit); exponential search followed by binary search, using slice comparisons for speed."""
    lo, step = 0, 16
    while lo < limit:
        hi = min(lo + step, limit)
        if data[a + lo:a + hi] == data[b + lo:b + hi]:
            lo, step = hi, step * 2
            continue
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if data[a + lo:a + mid] == data[b + lo:b + mid]:
                lo = mid
            else:
                hi = mid
        break
    return lo


def implode(data: bytes) -> bytes:
    """Greedy LZ77 compression into DCL Implode (ASCII literals, 4 KB dictionary)."""
    out = bytearray(b"\x01\x06")
    out_append = out.append
    lit_tok = _LIT_TOKEN
    len_tok = _LEN_TOKEN
    dist_tok = _DIST_TOKEN
    acc = nacc = 0
    last = {}                       # 24-bit trigram int -> most recent position
    last_get = last.get
    n = len(data)
    n2 = n - 2
    i = 0
    while i < n:
        if i + 3 <= n:
            key = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2]
            c = last_get(key)
            last[key] = i
            if c is not None and i - c <= WINDOW:
                rem = n - i
                limit = MAX_LEN if rem >= MAX_LEN else rem
                if limit == 3 or data[c + 3] != data[i + 3]:
                    length = 3
                else:
                    lo, step = 4, 8
                    while lo < limit:
                        hi = lo + step
                        if hi > limit:
                            hi = limit
                        if data[c + lo:c + hi] == data[i + lo:i + hi]:
                            lo = hi
                            step <<= 1
                        else:
                            while hi - lo > 1:
                                mid = (lo + hi) >> 1
                                if data[c + lo:c + mid] == data[i + lo:i + mid]:
                                    lo = mid
                                else:
                                    hi = mid
                            break
                    length = lo
                lc, ln = len_tok[length]
                dc, dn = dist_tok[i - c - 1]
                acc |= (lc | (dc << ln)) << nacc
                nacc += ln + dn
                while nacc >= 8:
                    out_append(acc & 0xFF)
                    acc >>= 8
                    nacc -= 8
                end_j = i + length
                if end_j > n2:
                    end_j = n2
                for j in range(i + 1, end_j):
                    last[(data[j] << 16) | (data[j + 1] << 8) | data[j + 2]] = j
                i += length
                continue
        c_lit, n_lit = lit_tok[data[i]]
        acc |= c_lit << nacc
        nacc += n_lit
        while nacc >= 8:
            out_append(acc & 0xFF)
            acc >>= 8
            nacc -= 8
        i += 1
    lc, ln = len_tok[519]
    acc |= lc << nacc
    nacc += ln
    while nacc >= 8:
        out_append(acc & 0xFF)
        acc >>= 8
        nacc -= 8
    if nacc:
        out_append(acc & 0xFF)
    return bytes(out)


def decode(data: bytes, *, max_bytes: int = MAX_FILE, max_section: int = MAX_SECTION) -> bytes:
    """.ms14 file contents → XML bytes.

    A .ms14 declares its own uncompressed size, so a corrupt or hostile file could ask
    for an unbounded allocation. Both the declared total and every section are capped,
    and no section may read past the end of the file.
    """
    if not data.startswith(MAGIC):
        raise ValueError("Not a Multisim compressed file (missing MSMCompressedElectronicsWorkbenchXML header)")
    head = len(MAGIC) + 8
    if len(data) < head:
        raise ValueError("corrupt .ms14: the header is truncated")
    total = struct.unpack_from("<Q", data, head - 8)[0]
    if total > max_bytes:
        raise ValueError(f"corrupt or oversized .ms14: it declares {total} bytes of XML, the limit is {max_bytes}")
    pos = head
    parts, size = [], 0
    while size < total:
        if pos + 8 > len(data):
            raise ValueError("corrupt .ms14: a section header is truncated")
        raw_len, comp_len = struct.unpack_from("<II", data, pos)
        pos += 8
        if raw_len == 0 or raw_len > max_section or size + raw_len > total:
            raise ValueError(f"corrupt or oversized .ms14: invalid section length {raw_len} (limit {max_section}, remaining {total - size})")
        if comp_len > len(data) - pos:
            raise ValueError("corrupt .ms14: a section is longer than the file")
        part = explode(data[pos:pos + comp_len], max_output=raw_len)
        if len(part) != raw_len:
            raise ValueError(f"Section length mismatch: expected {raw_len}, got {len(part)}")
        parts.append(part)
        size += raw_len
        pos += comp_len
    if size != total:
        raise ValueError(f"corrupt .ms14: sections add up to {size} bytes, the header says {total}")
    return b"".join(parts)


def encode(xml: bytes) -> bytes:
    """XML bytes → .ms14 file contents."""
    chunks = [MAGIC, struct.pack("<Q", len(xml))]
    for k in range(0, len(xml), SECTION):
        part = xml[k:k + SECTION]
        comp = implode(part)
        chunks += [struct.pack("<II", len(part), len(comp)), comp]
    return b"".join(chunks)
