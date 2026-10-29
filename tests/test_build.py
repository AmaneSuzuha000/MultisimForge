"""Offline regression: build the example specs, verify connectivity, mirroring and explicit wiring.

Does not need Multisim.
"""
import json
from pathlib import Path

import pytest

from multisimforge import builder, check, xmlio

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "_out"

pytestmark = pytest.mark.needs_parts


def _build(name, **kwargs):
    spec = json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))
    spec["output"] = str(OUT / Path(spec["output"]).with_suffix(".ms14").name)
    return builder.build(spec, **kwargs)


def test_rc_lowpass():
    result = _build("rc_lowpass.json")
    assert result["degraded"] is False
    assert result["route_failures"] == []
    assert result["warnings"] == [], result["warnings"]
    report = check.check_file(result["ms14"])
    assert report["problems"] == [], report["problems"]
    assert report["nets"]["vin"] == ["R1.1", "V1.1", "XSC1.1"], report["nets"]
    assert sorted(report["nets"]["vout"]) == ["C1.1", "R1.2", "XSC1.2"], report["nets"]


def test_phase_lead():
    result = _build("phase_lead.json")
    report = check.check_file(result["ms14"])
    assert report["problems"] == [], report["problems"]
    assert sorted(report["nets"]["vin"]) == ["C1.1", "XFG1.1", "XSC1.1"], report["nets"]
    assert sorted(report["nets"]["vout"]) == ["C1.2", "R4.1", "XSC1.2"], report["nets"]


def test_explicit_wiring():
    result = _build("explicit_wiring.json")
    assert result["warnings"] == [], result["warnings"]
    report = check.check_file(result["ms14"])
    assert report["problems"] == [], report["problems"]
    assert report["crossings"] == 0, report["crossings"]
    assert report["nets"] == {
        "out": ["XFG3.1", "XSC3.1"],
        "nc1": ["XFG3.2"],
        "0": ["XFG3.3", "XSC3.4"],
        "nc2": ["XSC3.2"],
        "nc3": ["XSC3.3"],
        "nc4": ["XSC3.5"],
        "nc5": ["XSC3.6"],
    }, report["nets"]
    root = xmlio.load(result["ms14"])
    mirrored = [e for e in root.iter("CIITSymbolComp") if e.get("Transformer-M00") == "-1"]
    assert mirrored, "no horizontally mirrored symbol"
    wires = list(root.iter("CIITLinkComp"))
    assert len(wires) == 3, len(wires)
    for wire in wires:
        assert wire.get("PenColor") == "255"
        assert wire.get("FgColor") == "0"
        assert wire.get("LockedPenColor") == "0"
        assert wire.get("Pen-Width") == "2"


def test_explicit_wire_validation():
    spec = json.loads((ROOT / "examples" / "explicit_wiring.json").read_text(encoding="utf-8"))
    spec["output"] = str(OUT / "bad.ms14")
    spec["wires"] = {"out": [[[63, 90], [140, 150]]]}          # diagonal
    with pytest.raises(ValueError):
        builder.build(spec)
    spec["wires"] = {"out": [[[63, 90], [63, 149]]]}           # endpoint off the pin
    with pytest.raises(ValueError):
        builder.build(spec)


def test_mirror_validation():
    spec = json.loads((ROOT / "examples" / "explicit_wiring.json").read_text(encoding="utf-8"))
    spec["output"] = str(OUT / "badmirror.ms14")
    spec["layout"]["XFG3"] = [45, 90, "diagonal"]
    with pytest.raises(ValueError):
        builder.build(spec)

def test_degraded_routing_is_rejected():
    """A layout with no routing channel must fail instead of drawing wires through parts."""
    from multisimforge.router import RouteError
    spec = json.loads((ROOT / "examples" / "rc_lowpass.json").read_text(encoding="utf-8"))
    spec["output"] = str(OUT / "crowded.ms14")
    spec["layout"] = {"V1": [108, 216], "R1": [252, 180], "C1": [252, 196, 90], "XSC1": [300, 130],
                      "GND": [[108, 306], [252, 342], [300, 198]]}
    with pytest.raises(RouteError) as exc:
        builder.build(spec, auto_repair=False)
    assert exc.value.failures, "RouteError must name the failing nets"
    assert not (OUT / "crowded.ms14").exists(), "a rejected build must not leave a file behind"


def test_degraded_routing_can_be_allowed():
    spec = json.loads((ROOT / "examples" / "rc_lowpass.json").read_text(encoding="utf-8"))
    spec["output"] = str(OUT / "crowded_ok.ms14")
    spec["layout"] = {"V1": [108, 216], "R1": [252, 180], "C1": [252, 196, 90], "XSC1": [300, 130],
                      "GND": [[108, 306], [252, 342], [300, 198]]}
    result = builder.build(spec, allow_fallback=True, auto_repair=False)
    assert result["degraded"] is True
    assert result["route_failures"]
    assert any("degraded routing" in w for w in result["warnings"])

def test_layout_is_auto_repaired():
    """A part sitting on top of a ground symbol is nudged aside instead of failing the build."""
    from multisimforge.router import RouteError
    spec = json.loads((ROOT / "examples" / "rc_lowpass.json").read_text(encoding="utf-8"))
    spec["output"] = str(OUT / "repaired.ms14")
    spec["layout"]["C1"] = [396, 300, 90]                      # overlaps the ground symbol at (396, 342)
    with pytest.raises(RouteError):
        builder.build(spec, auto_repair=False)
    result = builder.build(spec)
    assert result["moved_parts"], "the repair must report what it moved"
    assert result["degraded"] is False
    assert check.check_file(result["ms14"])["problems"] == []
    assert any("auto-repaired" in w for w in result["warnings"]), result["warnings"]
