"""Offline regression: .ms14 codec round-trip and XML byte fidelity."""
from pathlib import Path

import pytest

from multisimforge import config, codec, xmlio

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "_out"


def test_implode_explode_roundtrip():
    data = (b"<Root>" + b"<Item Value=\"1\"/>" * 500 + b"</Root>") * 3
    assert codec.explode(codec.implode(data)) == data


def test_xml_write_is_idempotent(tmp_path):
    """Write → read back → write again, byte-for-byte identical (line breaks and newlines inside attributes are preserved)."""
    xml = '<?xml version="1.0" encoding="ASCII"?>\n<Root>\n<Item A="1" B="x"/>\n<Item C="y"/>\n</Root>\n'
    root = xmlio.parse_text(xml)
    first = xmlio.to_text(root)
    second = xmlio.to_text(xmlio.parse_text(first))
    assert first == second
    out = tmp_path / "a.xml"
    xmlio.write(out, first)
    assert out.read_text(encoding="utf-8") == first


def test_ms14_roundtrip(tmp_path):
    """A real .ms14: decode → encode, restoring the XML layer byte-for-byte (skipped when no samples exist on this machine)."""
    samples = Path(config.CONFIG["samples_dir"])
    files = sorted(samples.rglob("*.ms14"))[:3] if samples.is_dir() else []
    if not files:
        pytest.skip("No Multisim samples on this machine; skipping")
    for src in files:
        raw = src.read_bytes()
        xml = codec.decode(raw)
        rebuilt = codec.encode(xml)
        assert codec.decode(rebuilt) == xml, src.name
        assert xmlio.to_text(xmlio.parse_text(xml.decode("utf-8"))) == xml.decode("utf-8"), src.name


def test_ascii_encoding():
    text = "op-amp LM324A"
    assert xmlio.unasc(xmlio.asc(text)) == text