"""Offline regression: check must catch the defects a hand-edited design can have."""
import copy

import pytest

from multisimforge import builder, check, xmlio


@pytest.fixture()
def clean(tmp_path):
    import json
    from pathlib import Path
    example = json.loads((Path(__file__).resolve().parents[1] / "examples" / "rc_lowpass.json").read_text(encoding="utf-8"))
    spec = dict(example, output=str(tmp_path / "rc.ms14"))
    result = builder.build(spec, auto_repair=False)
    assert check.check_file(result["ms14"])["problems"] == []
    return result["ms14"]


def _rewrite(path, tmp_path, edit):
    root = xmlio.load(path)
    edit(root)
    out = tmp_path / "edited.ms14"
    xmlio.save(root, out)
    return out


@pytest.mark.needs_parts
def test_duplicate_refdes(clean, tmp_path):
    def edit(root):
        # check.Circuit keys components as Item[@Class='CiComponent'], so the refdes lives on the item itself
        for it in root.iter("Item"):
            if it.get("Class") == "CiComponent" and xmlio.unasc(it[0].get("LocalName")) == "C1":
                it[0].set("LocalName", xmlio.asc("R1"))
    report = check.check_file(_rewrite(clean, tmp_path, edit))
    assert any("Reference designator R1" in p for p in report["problems"])


@pytest.mark.needs_parts
def test_dangling_wire(clean, tmp_path):
    def edit(root):
        wire = next(it for it in root.iter("Item") if it.get("Class") == "CIITLinkComp")
        stray = copy.deepcopy(wire)
        stray[0].set("Connect1", "0")
        stray[0].set("Connect2", "0")
        for point in stray[0].find("Points"):
            point.set("X", str(int(float(point.get("X"))) + 900))
            point.set("Y", str(int(float(point.get("Y"))) + 300))
        for parent in root.iter():
            if wire in list(parent):
                parent.append(stray)
                break
    report = check.check_file(_rewrite(clean, tmp_path, edit))
    assert any("is not attached to anything" in p for p in report["problems"])


@pytest.mark.needs_parts
def test_ground_symbols_are_not_duplicate_refdes(clean):
    """Multisim names every ground symbol 0; that must not look like a duplicate."""
    report = check.check_file(clean)
    assert not any("Reference designator 0" in p for p in report["problems"])


def test_not_a_circuit(tmp_path):
    bad = tmp_path / "notes.ms14"
    bad.write_text("this is not xml at all", encoding="utf-8")
    with pytest.raises(ValueError, match="not a Multisim design"):
        check.check_file(bad)


def test_xml_without_diagram(tmp_path):
    bad = tmp_path / "empty.ms14"
    bad.write_text('<?xml version="1.0"?><Root><A/></Root>', encoding="utf-8")
    with pytest.raises(ValueError, match="no CIITDiagram"):
        check.check_file(bad)
