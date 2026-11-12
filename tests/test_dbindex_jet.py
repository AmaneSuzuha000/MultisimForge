"""Tests for pure-Python Jet3/Jet4 header password decryption and ADODB dbindex generation."""
import json
from pathlib import Path

import pytest

from multisimforge import parts


def _make_jet_header(version_byte: int, password: str) -> bytes:
    """Construct a synthetic Jet3 or Jet4 header block encrypted with Jet's fixed RC4 key."""
    hdr = bytearray(256)
    hdr[0x14] = version_byte
    plain = bytearray(0x80)
    if version_byte == 0:
        raw = password.encode("latin-1")
        plain[0x2A:0x2A + len(raw)] = raw
    else:
        cdate = b"\x37\x12\x00\x00"
        plain[0x4A:0x4E] = cdate
        enc_pw = bytearray(password.encode("utf-16-le").ljust(40, b"\x00")[:40])
        for i in range(0, 40, 2):
            enc_pw[i] ^= cdate[0] if (i // 2) % 2 == 0 else 0
        plain[0x2A:0x52] = enc_pw
    hdr[0x18:0x98] = parts._rc4_jet(bytes([0xC7, 0xDA, 0x39, 0x6B]), bytes(plain))
    return bytes(hdr)


def test_jet3_and_jet4_password_decryption(tmp_path):
    p3 = tmp_path / "jet3.prd"
    p3.write_bytes(_make_jet_header(0, "eMg87AVduF"))
    assert parts._jet_password(p3) == "eMg87AVduF"

    p4 = tmp_path / "jet4.accdb"
    p4.write_bytes(_make_jet_header(1, "TestJet4Pw"))
    assert parts._jet_password(p4) == "TestJet4Pw"


def test_build_db_index_prefers_adodb_when_available(tmp_path, monkeypatch):
    lib = parts.Library(tmp_path / "lib")
    db = tmp_path / "MSCOMP_S.PRD"
    db.write_bytes(_make_jet_header(0, "Secret12"))
    monkeypatch.setitem(parts.CONFIG, "master_db", str(db))
    monkeypatch.setitem(parts.CONFIG, "python32", str(Path("C:/python32/python.exe")))
    monkeypatch.setattr(parts.sys, "platform", "win32")

    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        class R:
            returncode = 0
            stdout = "56980\n"
            stderr = ""
        return R()

    monkeypatch.setattr(parts.subprocess, "run", fake_run)
    res = parts.build_db_index(lib)
    assert res["backend"] == "adodb"
    assert res["components"] == 56980
    assert seen and "-c" in seen[0] and "ADODB.Connection" in seen[0][2]
    assert "Secret12" in seen[0][2]


def test_build_db_index_real_master_db_if_present(tmp_path):
    db = Path(parts.CONFIG["master_db"])
    if not db.is_file():
        pytest.skip("master_db not present")
    pw = parts._jet_password(db)
    assert pw and pw.isascii()
    lib = parts.Library(tmp_path / "real_db")
    res = parts.build_db_index(lib)
    assert res["backend"] == "adodb"
    assert res["components"] > 50000
    idx = json.loads((lib.path / "db_index.json").read_text(encoding="utf-8"))
    assert "LM324AJ" in idx and "MMBD7000LT1" in idx
    sec_idx = json.loads((lib.path / "db_sections.json").read_text(encoding="utf-8"))
    assert "LM324AJ" in sec_idx and "4071BP_5V" in sec_idx
