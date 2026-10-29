"""The status contract: exit code and payload status must never disagree.

This is the regression test for the worst bug in 0.1.0: the MCP adapter treated
parseable JSON as success, so a design with wiring problems (exit code 2) was
reported to the agent as ok=True.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from multisimforge.result import Status, exit_code_for, worst

ROOT = Path(__file__).resolve().parent.parent


def run_cli(*args):
    proc = subprocess.run([sys.executable, "-m", "multisimforge", *args],
                          cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
    return proc.returncode, json.loads(proc.stdout)


def test_exit_codes():
    assert exit_code_for(Status.PASS) == 0
    assert exit_code_for(Status.WARN) == 0
    assert exit_code_for(Status.FAIL) == 2
    assert exit_code_for(Status.ERROR) == 1
    assert exit_code_for(Status.CANCELLED) == 130


def test_worst():
    assert worst([]) is Status.PASS
    assert worst([Status.PASS, Status.WARN]) is Status.WARN
    assert worst([Status.WARN, Status.FAIL]) is Status.FAIL
    assert worst([Status.FAIL, Status.ERROR]) is Status.ERROR


def test_unknown_part_is_error():
    code, payload = run_cli("parts", "show", "definitely-not-a-part")
    assert code == 1, (code, payload)
    assert payload["status"] == "ERROR"
    assert payload["exit_code"] == 1


def test_bad_spec_is_error():
    code, payload = run_cli("build", str(ROOT / "examples" / "does_not_exist.json"))
    assert code == 1
    assert payload["status"] == "ERROR"


@pytest.mark.needs_parts
def test_broken_design_is_fail():
    """A design whose wiring does not match its netlist must exit 2, not 0."""
    from multisimforge import builder, check, xmlio
    spec = json.loads((ROOT / "examples" / "rc_lowpass.json").read_text(encoding="utf-8"))
    out = Path(__file__).resolve().parent / "_out" / "broken_contract.ms14"
    spec["output"] = str(out)
    builder.build(spec)
    root = xmlio.load(out)
    parent = {child: node for node in root.iter() for child in node}
    wires = list(root.iter("CIITLinkComp"))
    parent[wires[-1]].remove(wires[-1])
    xmlio.save(root, out)
    code, payload = run_cli("check", str(out))
    assert code == 2, (code, payload)
    assert payload["status"] == "FAIL"
    assert payload["problems"]


@pytest.mark.needs_parts
def test_mcp_ok_follows_exit_code():
    """The adapter must not report ok=True for a non-zero CLI exit."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("adapter_server", ROOT / "mcp_adapter" / "server.py")
    if spec is None or spec.loader is None:
        pytest.skip("adapter not importable")
    try:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:
        pytest.skip(f"adapter needs the mcp package: {exc}")
    from multisimforge import builder
    spec_obj = json.loads((ROOT / "examples" / "rc_lowpass.json").read_text(encoding="utf-8"))
    out = Path(__file__).resolve().parent / "_out" / "ok_contract.ms14"
    spec_obj["output"] = str(out)
    builder.build(spec_obj)
    result = module._run_cli(["check", str(out)])
    assert result["ok"] is True
    assert result["exit_code"] == 0