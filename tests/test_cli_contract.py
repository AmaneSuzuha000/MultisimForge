"""CLI behaviour that must hold without Multisim: one envelope, real exit codes, --debug.

These run the CLI in-process so the exception branches can be exercised directly.
"""
import json
import sys
from pathlib import Path

import pytest

from multisimforge import cli, result

ROOT = Path(__file__).resolve().parent.parent


def run(argv, monkeypatch, capsys):
    """Invoke cli.main and return (exit code, parsed envelope)."""
    monkeypatch.setattr(sys, "argv", ["multisimforge", *argv])
    code = 0
    try:
        cli.main(argv)
    except SystemExit as exc:
        code = exc.code or 0
    out = capsys.readouterr().out.strip()
    return code, json.loads(out)


def test_debug_is_a_real_global_option(monkeypatch, capsys):
    """--debug used to be an undeclared flag: argparse rejected it before any command ran."""
    monkeypatch.setattr(cli, "cmd_check", lambda a: (_ for _ in ()).throw(ZeroDivisionError("boom")))
    with pytest.raises(ZeroDivisionError):
        run(["--debug", "check", str(ROOT / "README.md")], monkeypatch, capsys)


def test_unexpected_exception_still_prints_one_envelope(monkeypatch, capsys):
    monkeypatch.setattr(cli, "cmd_check", lambda a: (_ for _ in ()).throw(ZeroDivisionError("boom")))
    code, payload = run(["check", str(ROOT / "README.md")], monkeypatch, capsys)
    assert code == 1
    assert payload["status"] == "ERROR"
    assert "ZeroDivisionError" in payload["error"]
    assert "detail" in payload, "the envelope should point at --debug"


def test_cancelled_is_130(monkeypatch, capsys):
    monkeypatch.setattr(cli, "cmd_check", lambda a: (_ for _ in ()).throw(KeyboardInterrupt()))
    code, payload = run(["check", str(ROOT / "README.md")], monkeypatch, capsys)
    assert code == 130 and payload["status"] == "CANCELLED"


def test_readable_errors_do_not_mention_debug(monkeypatch, capsys):
    """A ValueError is an expected failure; it is not an unexpected bug."""
    code, payload = run(["check", str(ROOT / "README.md")], monkeypatch, capsys)
    assert code == 1 and payload["status"] == "ERROR"
    assert "detail" not in payload


def test_worst_status_wins():
    assert result.worst([result.Status.PASS, result.Status.WARN]).value == "WARN"
    assert result.worst([result.Status.WARN, result.Status.FAIL]).value == "FAIL"
    assert result.worst([result.Status.FAIL, result.Status.CANCELLED]).value == "CANCELLED"
    assert result.worst([]).value == "PASS"


def test_every_status_has_a_documented_exit_code():
    for status in result.Status:
        assert status in result.EXIT_CODES
    assert [result.EXIT_CODES[s] for s in
            (result.Status.PASS, result.Status.WARN, result.Status.FAIL,
             result.Status.ERROR, result.Status.CANCELLED)] == [0, 0, 2, 1, 130]
