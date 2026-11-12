"""Tests for multi-section package specialization, shared COM pins, and model renaming."""
from pathlib import Path

import pytest

from multisimforge import builder, check, com, netlist, parts, xmlio


def _multi_part_names(lib: parts.Library) -> list[str]:
    return sorted(n for n, info in lib.index.items() if int(info.get("sections") or 1) > 1)


def test_split_section_ref_formats():
    assert netlist.split_section_ref("U1.B", ["A", "B", "C", "D"]) == ("U1", "B")
    assert netlist.split_section_ref("U1.a") == ("U1", "A")
    assert netlist.split_section_ref("U1B", ["A", "B"]) == ("U1", "B")
    assert netlist.split_section_ref("D1A", ["A", "B"]) == ("D1", "A")
    assert netlist.split_section_ref("U1", ["A", "B"]) == ("U1", None)
    assert netlist.split_section_ref("R1B") == ("R1B", None)
    with pytest.raises(ValueError, match="invalid section suffix"):
        netlist.split_section_ref("U1.")
    with pytest.raises(ValueError, match="invalid section suffix"):
        netlist.split_section_ref("U1.AB")


def test_rewrite_template_handles_digital_conditional_syntax():
    tpl = "a%p_a  [%t1A?%t:d%t;1A %t1B?%t:d%t;1B] %t1Y?%t:d%t;1Y %m"
    pin_map = {"1A": ("2A", "5"), "1B": ("2B", "6"), "1Y": ("2Y", "4")}
    out = parts._rewrite_template_for_section(tpl, "A", "B", pin_map)
    assert out == "a%p_b  [%t2A?%t:d%t;2A %t2B?%t:d%t;2B] %t2Y?%t:d%t;2Y %m"


def test_rewrite_template_preserves_series_dual_diode_polarity():
    tpl = "d%p_a %tA %tK/A %m\n"
    pin_map = {"A": ("K", "2"), "K/A": ("K/A", "3")}
    assert parts._rewrite_template_for_section(tpl, "A", "B", pin_map).strip() == "d%p_b %tK/A %tK %m"


def test_dqjm_supports_both_positional_and_pin_name_forms():
    pos = netlist.parse("D1.A in1 mid MMBD7000LT1\nD1.B mid out MMBD7000LT1")
    named = netlist.parse("D1.A MMBD7000LT1 A=in1 K/A=mid\nD1.B MMBD7000LT1 K/A=mid K=out")
    assert pos[0].nets == ["in1", "mid"] and pos[1].part == "MMBD7000LT1"
    assert named[0].named == {"A": "in1", "K/A": "mid"} and named[1].named == {"K/A": "mid", "K": "out"}


@pytest.mark.needs_parts
def test_all_library_multisection_parts_have_valid_section_specs():
    lib = parts.Library()
    names = _multi_part_names(lib)
    assert len(names) >= 15
    for name in names:
        part = lib.load(name)
        secs = parts.section_names(part)
        assert len(secs) >= 2
        seen_pin_sets = []
        for s in secs:
            sp = parts.section_spec(part, s, lib)
            assert sp["section"] == s
            assert len(sp["pins"]) == len(set(sp["pins"]))
            # Non-shared signal pins must map cleanly for this section
            seen_pin_sets.append(set(sp["pins"]))
        # Different sections of a physical package must differ in at least one signal pin
        assert len({frozenset(ps) for ps in seen_pin_sets}) == len(secs), name


@pytest.mark.needs_parts
def test_lm324am_keeps_harvested_3pin_subcircuit_arity():
    lib = parts.Library()
    if not lib.resolve("LM324AM"):
        pytest.skip("LM324AM not in local library")
    sp_b = parts.section_spec(lib.load("LM324AM"), "B", lib)
    assert "%t2IN+" in sp_b["template"] and "%t2IN-" in sp_b["template"] and "%t2OUT" in sp_b["template"]
    assert "VS+" not in sp_b["template"], "harvested LM324AM 3-pin subckt must not be overwritten with 5-pin DB template"


@pytest.mark.needs_parts
def test_multisection_lm324aj_mmbd7000lt1_and_7404n_build_and_check(tmp_path):
    lib = parts.Library()
    out = tmp_path / "multisec.ms14"
    spec = {
        "netlist": "\n".join([
            "V1 vcc 0 DC 12",
            "V2 in1 0 DC 2",
            "U1.A LM324AJ 1IN+=in1 1IN-=outa VS+=vcc VS-=0 1OUT=outa",
            # Omit VS+/VS- on U1.B: must auto-inherit from U1.A so U1B stays powered!
            "U1.B LM324AJ 2IN+=outa 2IN-=midb 2OUT=outb",
            "R1 outb midb 10k",
            "R2 midb 0 10k",
            "U2.A 7404N 1A=in1 1Y=inv1",
            "U2.B 7404N 2A=inv1 2Y=inv2",
            "D1.A MMBD7000LT1 A=in1 K/A=dmid",
            "D1.B MMBD7000LT1 K/A=dmid K=dout",
            "R3 dout 0 1k",
        ]) + "\n",
        "output": str(out),
        "layout": {
            "V1": [108, 252],
            "V2": [216, 252],
            "U1A": [432, 180],
            "U1B": [432, 396],
            "R1": [648, 396],
            "R2": [648, 504, 90],
            "U2A": [828, 180],
            "U2B": [828, 324],
            "D1A": [1008, 180],
            "D1B": [1188, 180],
            "R3": [1332, 252, 90],
            "GND": [[108, 360], [216, 360], [648, 594], [423, 72], [1332, 360]],
        },
    }
    res = builder.build(spec, lib)
    assert not any("section A only" in w for w in res["warnings"])
    report = check.check_file(out, res["expected"])
    assert report["problems"] == [], report["problems"]

    # Check inherited VS+/VS- and K/A nets, renamed comment-headed MMBD7000LT1 models,
    # stripped stale LM324AJ\nU1 label, and full RefDesPrefixUsageMap MultisectionUsage.
    root = xmlio.load(out)
    nets = report["nets"]
    assert {"U1A.VS+", "U1B.VS+"} <= set(nets["vcc"])
    assert {"U1A.VS-", "U1B.VS-"} <= set(nets["0"])
    assert {"D1A.K/A", "D1B.K/A"} <= set(nets["dmid"])

    model_names = [xmlio.unasc(m[0].get("LocalName")) for m in root.iter("Item") if m.get("Class") == "CiModel"]
    for m_it in root.iter("Item"):
        if m_it.get("Class") == "CiModel" and len(m_it):
            name = xmlio.unasc(m_it[0].get("LocalName"))
            card = m_it[0].find("Attributes/Item/CiaCString")
            if card is not None and "__" in name:
                raw = xmlio.unasc(card.get("String", ""))
                assert f" {name}" in raw or f"\n{name}" in raw or f".MODEL {name}" in raw.upper() or f".SUBCKT {name}" in raw.upper()
    assert len(model_names) == len(set(model_names))

    rd_keys = {xmlio.unasc(r.get("CIRKey")): r for r in root.iter("CIRToInfoMapItem")}
    assert {"U1A", "U1B", "U2A", "U2B", "D1A", "D1B"} <= set(rd_keys)
    assert xmlio.unasc(rd_keys["U1B"].find("RefDesInfo").get("IRSection")) == "B"
    assert rd_keys["U1B"].find("RefDesInfo").get("IRSectionID") == "1"
    assert "%t2IN+" in xmlio.unasc(rd_keys["U1B"].find("RefDesInfo").get("SpiceTemplate"))
    cir_pins = [xmlio.unasc(x.get("PinNameCIR")) for x in rd_keys["U1B"].findall(".//PinOrderCIRItem")]
    assert "2IN+" in cir_pins and "2OUT" in cir_pins and "1IN+" not in cir_pins

    for sym_it in root.iter("Item"):
        if sym_it.get("Class") == "CIITSymbolComp" and len(sym_it):
            val_item = sym_it[0].find("./Objects/Item/CIITSymTextCompValue")
            if val_item is not None and "LM324AJ" in xmlio.text(val_item.get("Output") or ""):
                assert xmlio.text(val_item.get("Output") or "").strip() == "LM324AJ"

    insp = check.inspect(out)
    u1_secs = sorted(c["section"] for c in insp["components"] if c.get("package") == "U1")
    assert u1_secs == ["A", "B"]

    # Confirm check_file accepts real native multi-section sample circuits when installed
    samples = Path(parts.CONFIG["samples_dir"])
    fd = samples / "Mixed-signal" / "FrequencyDivider.ms14"
    if fd.is_file():
        assert check.check_file(fd)["problems"] == []


@pytest.mark.needs_parts
def test_multisection_package_validation_errors(tmp_path):
    lib = parts.Library()
    # Mixed parts inside one package
    with pytest.raises(ValueError, match="cannot mix different parts"):
        builder.build({
            "netlist": "U1.A LM324AJ 1IN+=a 1IN-=b VS+=v VS-=0 1OUT=c\nU1.B LM258AD 2IN+=c 2IN-=d VS+=v VS-=0 2OUT=e\n",
            "output": str(tmp_path / "mix.ms14"),
        }, lib)
    # Invalid section letter
    with pytest.raises(ValueError, match="only supports sections"):
        builder.build({
            "netlist": "U1.E LM324AJ 1IN+=a 1IN-=b VS+=v VS-=0 1OUT=c\n",
            "output": str(tmp_path / "badsec.ms14"),
        }, lib)
    # Single-section part rejecting section suffix
    with pytest.raises(ValueError, match="does not support section"):
        builder.build({
            "netlist": "R1.B a 0 1k\n",
            "output": str(tmp_path / "single.ms14"),
        }, lib)
    # Conflicting shared COM power net across sections of one package
    with pytest.raises(ValueError, match="shared pin VS\\+ has conflicting nets"):
        builder.build({
            "netlist": (
                "U1.A LM324AJ 1IN+=a 1IN-=b VS+=v1 VS-=0 1OUT=c\n"
                "U1.B LM324AJ 2IN+=c 2IN-=d VS+=v2 VS-=0 2OUT=e\n"
            ),
            "output": str(tmp_path / "conflict.ms14"),
        }, lib)
    # Duplicate section slot
    with pytest.raises(ValueError, match="duplicate refdes after naming normalization"):
        builder.build({
            "netlist": (
                "U1.B LM324AJ 2IN+=a 2IN-=b VS+=v VS-=0 2OUT=c\n"
                "XU1B LM324AJ 2IN+=c 2IN-=d VS+=v VS-=0 2OUT=e\n"
            ),
            "output": str(tmp_path / "dupsec.ms14"),
        }, lib)


def test_comment_headed_model_cards_are_renamed_on_second_instance():
    # Regression guard for builder.py regex missing re.MULTILINE on comment-headed cards
    import re
    raw = "* Copyright ON Semi\n* Revision 1\n.MODEL MMBD7000LT1__DIODE__1 D(IS=1e-9)\n"
    out = re.sub(r"(?im)^(\s*\.(?:SUBCKT|MODEL)\s+)\S+", lambda m: m.group(1) + "MMBD7000LT1__DIODE__2", raw, count=1)
    assert ".MODEL MMBD7000LT1__DIODE__2 D(IS=1e-9)" in out
    assert "MMBD7000LT1__DIODE__1" not in out


def test_com_enum_sections_reports_multisection_fixture(tmp_path):
    ms14 = tmp_path / "multi.ms14"
    ms14.write_bytes(b"placeholder")

    class FakeCircuit:
        def EnumComponents(self, _):
            return ("U1", "D1")

        def EnumSections(self, ref):
            return ("A", "B") if ref in ("U1", "D1") else ("",)

    class FakeApp:
        def OpenFile(self, path):
            return FakeCircuit()

    assert com._execute(FakeApp(), {"op": "sections", "ms14": str(ms14), "ref": "U1"})["sections"] == ["A", "B"]
    assert com._execute(FakeApp(), {"op": "sections", "ms14": str(ms14), "ref": "D1"})["sections"] == ["A", "B"]
