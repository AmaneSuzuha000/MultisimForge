"""Offline regression: a corrupt .ms14 must fail with ValueError, never allocate wildly or raise struct.error."""
import struct

import pytest

from multisimforge import codec


def test_roundtrip_sizes():
    for size in (0, 1, 255, 256, 900_000, 1_800_000):
        blob = bytes([7]) * size
        assert codec.decode(codec.encode(blob)) == blob


def test_not_a_ms14():
    with pytest.raises(ValueError, match="Not a Multisim compressed file"):
        codec.decode(b"plain text, not a design")


def test_truncated_header():
    with pytest.raises(ValueError, match="header is truncated"):
        codec.decode(codec.MAGIC + b"\0\0")


@pytest.fixture()
def good():
    return codec.encode(b"x" * 500)


def test_truncated_section(good):
    with pytest.raises(ValueError):
        codec.decode(good[:-10])


def test_oversized_declared_total(good):
    blob = good[:36] + struct.pack("<Q", 1 << 40) + good[44:]
    with pytest.raises(ValueError, match="oversized"):
        codec.decode(blob)


def test_oversized_section(good):
    blob = good[:44] + struct.pack("<II", 1 << 30, 10) + good[52:]
    with pytest.raises(ValueError, match="oversized"):
        codec.decode(blob)


def test_limit_is_configurable(good):
    with pytest.raises(ValueError, match="limit"):
        codec.decode(good, max_bytes=10)
