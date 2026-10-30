"""Argument validation and the file-only commands, exercised through main().

These are the errors a user hits while typing a command, so each one is asserted on its
message: an envelope that says ERROR but not why is not much better than a traceback.
"""
import json
import sys

import pytest

from multisimforge import cli, xmlio


def run(argv, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["multisimforge", *argv])
    code = 0
    try:
        cli.main(argv)
    except SystemExit as exc:
        code = exc.code or 0
    return code, json.loads(capsys.readouterr().out.strip())


def test_positive_rejects_zero_and_negative(monkeypatch):
    import argparse
    for text in ("0", "-1", "-0.5"):
        with pytest.raises(argparse.ArgumentTypeError, match="greater than 0"):
            cli._positive(text)


def test_positive_rejects_an_absurd_duration(monkeypatch):
    import argparse
    with pytest.raises(argparse.ArgumentTypeError, match="at most 3600"):
        cli._positive("3601")


def test_positive_accepts_a_normal_duration():
    assert cli._positive("1.5") == 1.5
    assert cli._positive("3600") == 3600


def test_positive_rejects_a_non_number():
    with pytest.raises(ValueError):
        cli._positive("soon")


def test_floats_parses_rkm_values():
    assert cli._floats("10,100k") == pytest.approx([10.0, 100e3])
    # 3n lands on 3.0000000000000004e-09: multiply, do not divide by the prefix
    assert cli._floats("1m,2u,3n") == pytest.approx([1e-3, 2e-6, 3e-9], rel=1e-12)


def test_floats_skips_empty_fields():
    assert cli._floats("10,,100k,") == [10.0, 100e3]


def test_floats_enforces_the_expected_count():
    assert cli._floats("1,2,3", n=(3,)) == [1.0, 2.0, 3.0]
    with pytest.raises(ValueError, match="wrong number of values"):
        cli._floats("1,2", n=(3,))


def test_a_spec_that_is_not_a_json_object_is_refused(tmp_path, monkeypatch, capsys):
    spec = tmp_path / "spec.json"
    spec.write_text("[1, 2, 3]", encoding="utf-8")
    code, payload = run(["build", str(spec)], monkeypatch, capsys)
    assert code == 1 and payload["status"] == "ERROR"
    assert "one JSON object" in payload["error"]


def test_a_spec_that_is_not_utf8_is_refused(tmp_path, monkeypatch, capsys):
    spec = tmp_path / "spec.json"
    spec.write_bytes(b"\xff\xfe{\"a\": 1}")
    code, payload = run(["build", str(spec)], monkeypatch, capsys)
    assert code == 1 and payload["status"] == "ERROR"
    assert "UTF-8" in payload["error"]


def test_a_missing_spec_is_reported_as_a_missing_file(tmp_path, monkeypatch, capsys):
    code, payload = run(["build", str(tmp_path / "nope.json")], monkeypatch, capsys)
    assert code == 1 and payload["status"] == "ERROR"
    assert "FileNotFoundError" in payload["error"]


def test_decode_then_encode_round_trips_through_the_cli(tmp_path, monkeypatch, capsys):
    """codec is the only command pair that needs no Multisim at all."""
    xml = tmp_path / "circuit.xml"
    ms14 = tmp_path / "circuit.ms14"
    back = tmp_path / "back.xml"
    xml.write_text('<?xml version="1.0"?><Root><A>1</A></Root>', encoding="utf-8")
    code, payload = run(["encode", str(xml), str(ms14)], monkeypatch, capsys)
    assert code == 0 and payload["status"] == "PASS"
    assert ms14.read_bytes()[:4] != b"<?xm", "encode must compress, not copy"
    code, payload = run(["decode", str(ms14), str(back)], monkeypatch, capsys)
    assert code == 0 and payload["status"] == "PASS"
    assert xmlio.parse_text(back.read_text(encoding="utf-8")) is not None


def test_decode_of_a_non_ms14_file_fails_cleanly(tmp_path, monkeypatch, capsys):
    src = tmp_path / "garbage.ms14"
    src.write_bytes(b"this is not a compressed file at all" * 4)
    code, payload = run(["decode", str(src), str(tmp_path / "out.xml")], monkeypatch, capsys)
    assert code == 1 and payload["status"] == "ERROR"
    assert "ValueError" in payload["error"]


def test_a_bad_argument_choice_exits_through_argparse(tmp_path, monkeypatch, capsys):
    with pytest.raises(SystemExit) as info:
        cli.main(["parts", "not-an-action"])
    assert info.value.code == 2, "argparse reports a usage error as 2"