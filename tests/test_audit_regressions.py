"""Regression tests for audit findings across parser, XML, parts, checker, COM, probes, and router."""
import copy
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from multisimforge import builder, check, com, netlist, parts, router, sim, xmlio

ROOT = Path(__file__).resolve().parent.parent


def test_subcircuit_starting_with_xsc_not_hijacked_as_oscilloscope():
    inst = netlist.parse("XSC_AMP in out vcc gnd LM324\n")[0]
    assert inst.refdes == "XSC_AMP"
    assert inst.part == "LM324"
    assert inst.nets == ["in", "out", "vcc", "0"]


def test_instrument_refdes_without_known_keyword_still_fails():
    with pytest.raises(ValueError, match="must end with"):
        netlist.parse("XSC1 a b 0 nc1 0 nc2 NOT_AN_INST\n")


def test_netlist_rejects_empty_and_duplicate_clauses():
    with pytest.raises(ValueError):
        netlist.net_name("")
    with pytest.raises(ValueError, match="duplicate DC"):
        netlist.parse("V1 a 0 DC 5 DC 1\n")
    with pytest.raises(ValueError, match="invalid pin=net"):
        netlist.parse("U1 LM555CN =net\n")
    with pytest.raises(ValueError, match="invalid pin=net"):
        netlist.parse("U1 LM555CN OUT=\n")
    with pytest.raises(ValueError, match="duplicate assignment for pin 'OUT'"):
        netlist.parse("U1 LM555CN OUT=a OUT=b\n")


def test_xmlio_attribute_newline_and_non_bmp_unicode():
    xml = '<?xml version="1.0" encoding="ASCII"?>\n<Root><Item Key="a\nb">value="1\n2"</Item></Root>\n'
    parsed = xmlio.parse_text(xml)
    assert parsed.find("Item").get("Key") == "a\nb"
    assert parsed.find("Item").text == 'value="1\n2"'
    roundtrip = xmlio.to_text(parsed)
    assert xmlio.parse_text(roundtrip).find("Item").text == 'value="1\n2"'

    emoji = "Hello 😀 世界"
    encoded = xmlio.uni(emoji)
    assert xmlio.text(encoded) == emoji
    assert xmlio.decode_uc("_uc2d83dde00") == "😀"


def test_parts_safe_name_reserved_windows_devices_and_cache_isolation(tmp_path):
    for name in ("CON", "con", "NUL.", "COM1", "LPT9"):
        assert parts.safe_name(name).startswith("_")

    lib_dir = tmp_path / "parts"
    lib_dir.mkdir()
    tpl = "<Part><Items><Item Class=\"CiComponent\"><CiComponent LocalName=\"&amp;ASCQ1\"/></Item></Items></Part>"
    (lib_dir / "Q.xml").write_text(tpl, encoding="utf-8")
    (lib_dir / "index.json").write_text(json.dumps({"Q": {"family": "BJT", "file": "Q.xml", "pins": ["C", "B", "E"]}}), encoding="utf-8")
    lib = parts.Library(lib_dir)
    first = lib.load("Q")
    first.set("mutated", "yes")
    second = lib.load("Q")
    assert second.get("mutated") is None


def test_router_same_point_returns_single_point():
    assert router.route((18, 27), (18, 27), [], occupied=[]) == [(18, 27)]


def test_com_pid_parsing_case_insensitive_and_atomic_state(monkeypatch, tmp_path):
    class Proc:
        stdout = '"Multisim.exe","4321","Console","1","12,000 K"\n"multisim.exe","9999","Console","1","10,000 K"\n'

    monkeypatch.setattr(com.subprocess, "run", lambda *a, **k: Proc())
    assert com._multisim_pids() == {"4321", "9999"}

    state_file = tmp_path / "sub" / "com_daemon.json"
    monkeypatch.setattr(com, "_state_path", lambda: state_file)
    com._write_state({"port": 5555, "token": "abc", "pid": 42})
    assert com._read_state()["port"] == 5555


def test_builder_set_sim_time_validation_and_missing_tstop(tmp_path):
    bad = tmp_path / "nosettings.xml"
    bad.write_text('<?xml version="1.0"?><Root><CIITDiagram SimState="NO_TIME"/></Root>', encoding="utf-8")
    with pytest.raises(ValueError, match="finite and positive"):
        builder.set_sim_time(bad, 0)
    with pytest.raises(ValueError, match="finite and positive"):
        builder.set_sim_time(bad, float("nan"))
    with pytest.raises(ValueError, match="no interactive simulation settings"):
        builder.set_sim_time(bad, 0.01)

    no_tstop = tmp_path / "notstop.xml"
    no_tstop.write_text('<?xml version="1.0"?><Root><CIITDiagram SimState="DEFAULT_INST_SETTINGS NO_TSTOP"/></Root>', encoding="utf-8")
    with pytest.raises(ValueError, match="TSTOP"):
        builder.set_sim_time(no_tstop, 0.01)


@pytest.mark.needs_parts
def test_check_scopes_to_primary_diagram_merges_nodes_and_ignores_hidden_power(tmp_path):
    spec = json.loads((ROOT / "examples" / "rc_lowpass.json").read_text(encoding="utf-8"))
    out = tmp_path / "rc.ms14"
    builder.build(dict(spec, output=str(out)))
    root = xmlio.load(out)

    # Duplicate a named CiNode with the same LocalName: electrical_nets must merge them rather than overwriting
    elements = next(d.find("./Elements") for d in root.iter("CIITDiagram") if d.find("./Elements") is not None)
    vin_node = next(it for it in elements if it.get("Class") == "CiNode" and xmlio.unasc(it[0].get("LocalName")).lower() == "vin")
    dup_node = copy.deepcopy(vin_node)
    dup_node.set("CiID", "8888888")
    dup_node[0].set("ID", "8888889")
    elements.append(dup_node)

    # Secondary subcircuit CIITDiagram with an unattached wire must not trigger main-sheet failures
    sub_diag = ET.SubElement(root, "CIITDiagram")
    sub_elems = ET.SubElement(sub_diag, "Elements")
    wire = copy.deepcopy(next(it for it in root.iter("Item") if it.get("Class") == "CIITLinkComp"))
    wire[0].set("Connect1", "missing")
    wire[0].set("Connect2", "missing")
    sub_elems.append(wire)

    edited = tmp_path / "edited.ms14"
    xmlio.save(root, edited)
    report = check.check_file(edited)
    assert report["problems"] == []


@pytest.mark.needs_parts
def test_add_probes_twice_avoids_id_and_refdes_collisions(tmp_path):
    from multisimforge.parts import Library
    spec = json.loads((ROOT / "examples" / "rc_lowpass.json").read_text(encoding="utf-8"))
    target = tmp_path / "double_probe.ms14"
    builder.build(dict(spec, output=str(target)))
    lib = Library()
    root = xmlio.load(target)
    first = sim.add_probes(root, ["vin"], lib, str(target))
    second = sim.add_probes(root, ["vout"], lib, str(target))
    assert first == {"vin": "V(OutProbe)"}
    assert second == {"vout": "V(OutProbe1)"}
    all_pr = [xmlio.unasc(it[0].get("LocalName")) for it in root.iter("Item")
              if it.get("Class") == "CiProbeExtComp" and len(it)]
    assert all_pr == ["PR1", "PR2"]
    usages = [u for u in root.iter("RefDesPrefixUsage") if u.get("RefDes") == "&ASCOUTPROBE"]
    assert len(usages) == 1

    with pytest.raises(ValueError, match="non-ground"):
        sim.run(str(target), ["0"], op=True)


@pytest.mark.needs_parts
def test_builder_rejects_invalid_visuals_and_xu1_duplicate_refdes(tmp_path):
    base = {
        "netlist": "R1 vin vout 1k\nV1 vin 0 DC 5\nR2 vout 0 1k\n",
        "output": str(tmp_path / "v.ms14"),
        "layout": {"V1": [108, 216], "R1": [252, 180], "R2": [396, 252, 90], "GND": [[108, 306], [396, 342]]},
    }
    with pytest.raises(ValueError, match="unknown colour"):
        builder.build(dict(base, colors={"vin": "chartreuse"}))
    with pytest.raises(ValueError, match="wire_width"):
        builder.build(dict(base, wire_width=0))
    with pytest.raises(ValueError, match="duplicate refdes after naming normalization"):
        builder.build({
            "netlist": "U1 LM324AJ 1IN+=in1 1IN-=g1 VS+=vp VS-=vn 1OUT=o1\nXU1 in1 g1 vp vn o2 LM324AJ\nV1 vp 0 DC 5\n",
            "output": str(tmp_path / "dup.ms14"),
            "layout": {"U1": [200, 200], "XU1": [450, 200], "V1": [100, 200], "GND": [[100, 300]]},
        })


def test_com_execute_sections_uses_enum_sections_and_validates_ref(tmp_path):
    design = tmp_path / "d.ms14"
    design.write_bytes(b"placeholder")

    class FakeCircuit:
        def EnumComponents(self, _):
            return ("U1", "R1")

        def EnumSections(self, ref):
            return ("A", "B") if ref == "U1" else ("",)

    class FakeApp:
        def OpenFile(self, path):
            return FakeCircuit()

    assert com._execute(FakeApp(), {"op": "sections", "ms14": str(design), "ref": "U1"}) == {
        "ref": "U1", "sections": ["A", "B"], "single_section": False,
    }
    assert com._execute(FakeApp(), {"op": "sections", "ms14": str(design), "ref": "R1"}) == {
        "ref": "R1", "sections": [], "single_section": True,
    }
    with pytest.raises(ValueError, match="NOPE not found"):
        com._execute(FakeApp(), {"op": "sections", "ms14": str(design), "ref": "NOPE"})


@pytest.mark.needs_parts
def test_builder_populates_refdes_prefix_usage_map_and_str_size(tmp_path):
    out = tmp_path / "usage_check.ms14"
    builder.build({
        "netlist": (
            "U1 LM324AJ 1IN+=in1 1IN-=out1 1OUT=out1 VS+=vcc VS-=0\n"
            "V1 vcc 0 DC 5\nV2 in1 0 DC 2\nR1 out1 0 1k\n"
        ),
        "output": str(out),
        "layout": {"V1": [90, 216], "V2": [162, 216], "U1": [342, 180], "R1": [468, 252, 90],
                   "GND": [[90, 306], [162, 306], [342, 270], [468, 324]]},
    })
    root = xmlio.load(out)
    usage_map = xmlio.find_tag(root, "RefDesPrefixUsageMap")
    by_prefix = {xmlio.unasc(u.get("RefDes")): u for u in usage_map}
    assert {"U", "V", "R"} <= set(by_prefix)
    v_used = [n.get("NumberUsed") for n in by_prefix["V"].findall("./RefDesPrefixUsage/NumbersUsedVTwo")]
    assert v_used == ["1", "2"]
    assert by_prefix["V"].get("NextNumber") == "3"
    u_ms = by_prefix["U"].findall("./MultisectionUsage/MultisectionItem")
    assert len(u_ms) == 1 and u_ms[0].get("NumberOfMultisection") == "1"
    assert [s.get("SectionUsed") for s in u_ms[0].findall("./SectionUsage/SectionUsageItem")] == ["1"]
    for rd in root.iter("RefDesInfoData"):
        dec = xmlio.unasc(rd.get("RefDes"))
        assert rd.get("RefDesStrSize") == str(len(dec.replace("!0", "\x00")) + 2)


def test_parts_fetch_relocates_carrier_stages_non_ascii_and_retries_indeterminate(tmp_path, monkeypatch):
    lib_dir = tmp_path / "parts"
    lib_dir.mkdir()
    (lib_dir / "db_index.json").write_text(json.dumps({"BC547B": ["Transistors", "BJT_NPN"]}), encoding="utf-8")
    (lib_dir / "index.json").write_text(json.dumps({"2N3904": {"family": "BJT_NPN", "file": "2N3904.xml", "pins": ["C", "B", "E"]}}), encoding="utf-8")
    lib = parts.Library(lib_dir)

    non_ascii_work = tmp_path / "项目工作区"
    monkeypatch.setitem(parts.CONFIG, "work_dir", str(non_ascii_work))

    built_paths = []

    def fake_build(spec, _lib):
        p = Path(spec["output"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"carrier-bytes")
        built_paths.append(p)
        return {"ms14": str(p)}

    seen_jobs = []
    stops = []
    fake_part = xmlio.parse_text(
        '<Part Name="BC547B" Family="BJT_NPN" Kind="component" Source="x" SourceRef="Q1">'
        '<Items>'
        '<Item CiID="1" Class="CiComponent"><CiComponent LocalName="&amp;ASCQ1" SymCompID="4">'
        '<CiaCollString><strings><Item Value="&amp;ASCBJT_NPN"/><Item Value="&amp;ASCBC547B"/></strings></CiaCollString>'
        '<CiaCollString><strings><Item Value="&amp;ASC1"/><Item Value=""/><Item Value=""/><Item Value=""/><Item Value="&amp;ASCQ%d"/></strings></CiaCollString>'
        '<Ports/></CiComponent></Item>'
        '<Item ID="4" Class="CIITSymbolComp"><CIITSymbolComp CiComponent="1" NumSections="1" IRSection=""><Objects/></CIITSymbolComp></Item>'
        '</Items></Part>'
    )

    def fake_call(job, timeout=180):
        seen_jobs.append(dict(job))
        if len(seen_jobs) == 1:
            raise com.JobIndeterminate("COM connection lost after request submission: [WinError 10054]")
        Path(job["out"]).write_bytes(b"replaced")
        return {"ref": "Q1"}

    monkeypatch.setattr("multisimforge.builder.build", fake_build)
    monkeypatch.setattr("multisimforge.com.call", fake_call)
    monkeypatch.setattr("multisimforge.com.stop", lambda: stops.append(True) or True)
    monkeypatch.setattr(parts.xmlio, "load", lambda path: fake_part)
    monkeypatch.setattr(parts.Harvester, "components", lambda self: [fake_part.find("./Items/Item[@Class='CiComponent']")])
    monkeypatch.setattr(parts.Harvester, "extract", lambda self, ci: fake_part)

    res = parts.fetch("BC547B", lib)
    assert res["name"] == "BC547B"
    assert len(stops) == 1 and len(seen_jobs) == 2
    opened = Path(seen_jobs[0]["ms14"])
    assert str(opened).isascii()
    assert opened != built_paths[0] and opened.stem != built_paths[0].stem
    assert not opened.parent.exists()


def test_com_cleanup_orphans_only_kills_dead_daemon_multisim_pids(monkeypatch):
    killed = []
    monkeypatch.setattr(com, "_multisim_pids", lambda: {"101", "202", "999"})

    def fake_run(args, **kw):
        killed.append(args)
        class R:
            returncode = 0
            stdout = ""
        return R()

    monkeypatch.setattr(com.subprocess, "run", fake_run)

    # Live daemon: must NOT kill anything
    monkeypatch.setattr(com, "_pid_alive", lambda pid: True)
    com._cleanup_orphans({"pid": 555, "multisim_pids": ["101"]})
    assert killed == []

    # Dead daemon: kills only its own still-live multisim PIDs ("101", "202"), never pre-existing "999" or vanished "303"
    monkeypatch.setattr(com, "_pid_alive", lambda pid: False)
    com._cleanup_orphans({"pid": 555, "multisim_pids": ["101", "202", "303"]})
    targeted = {cmd[2] for cmd in killed if cmd[0] == "taskkill"}
    assert targeted == {"101", "202"}


def test_parse_value_accepts_greek_small_mu():
    assert netlist.parse_value("4.7μ") == pytest.approx(4.7e-6)
    assert netlist.parse_value("10μs") == pytest.approx(10e-6)


def test_match_section_pin_handles_letter_suffixed_multisection_pins(tmp_path):
    # Regression guard for dual op-amps like LTC6244 (+INA/-INA/OUTA -> +INB/-INB/OUTB)
    # where ERC-type fallback alone could swap non-inverting and inverting inputs.
    cands = [("+INB", "5", "A:INPUT"), ("-INB", "6", "A:INPUT"), ("OUTB", "7", "A:OUTPUT")]
    used = set()
    m1 = parts._match_section_pin("-INA", "A:INPUT", 1, 2, cands, used, base_sec="A", target_sec="B")
    used.add(m1[0])
    m2 = parts._match_section_pin("+INA", "A:INPUT", 1, 2, cands, used, base_sec="A", target_sec="B")
    assert m1[0] == "-INB" and m2[0] == "+INB"

    # Token boundary must not truncate pin names containing '~' or '-'
    tpl = "a%p_a %t~1Q %t~1 %m"
    out = parts._rewrite_template_for_section(tpl, "A", "B", {"~1": ("~2", "1"), "~1Q": ("~2Q", "2")})
    assert out == "a%p_b %t~2Q %t~2 %m"

    # Case-insensitive Library.db_section lookup
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    (lib_dir / "db_sections.json").write_text(json.dumps({"LM324AJ": {"sections": {"A": {"pins": ["1IN+"]}}}}), encoding="utf-8")
    lib = parts.Library(lib_dir)
    assert lib.db_section("lm324aj") is not None


def test_check_comp_label_and_builder_repair_hot_ref_normalization():
    xml = xmlio.parse_text(
        '<?xml version="1.0"?><Root><CIITDiagram><Elements>'
        '<Item CiID="1" Class="CiComponent"><CiComponent LocalName="&amp;ASCQA"/></Item>'
        '<Item CiID="2" Class="CiComponent"><CiComponent LocalName="&amp;ASCQA"/></Item>'
        '<Item ID="3" Class="CIITSymbolComp"><CIITSymbolComp CiComponent="1" IRSection="&amp;ASCA" NumSections="2"><Objects/></CIITSymbolComp></Item>'
        '<Item ID="4" Class="CIITSymbolComp"><CIITSymbolComp CiComponent="2" IRSection="&amp;ASCB" NumSections="2"><Objects/></CIITSymbolComp></Item>'
        '</Elements></CIITDiagram></Root>'
    )
    c = check.Circuit(xml)
    assert c.comp_label("1") == "QAA" and c.comp_label("2") == "QAB"

    variants = list(builder._repair_variants({
        "netlist": "XU1.B LM324AJ 2IN+=hot 2IN-=0 VS+=vcc VS-=0 2OUT=out\nR1 other 0 1k\n",
        "output": "x.ms14",
        "layout": {"R1": [100, 100], "U1B": [300, 300], "GND": [[100, 200]]},
    }, 10, failing_nets={"hot"}))
    # Part on failing net ("U1B") must be moved before unrelated part ("R1")
    assert next(iter(variants[0][1])) == "U1B"


def test_add_probes_reads_existing_cir_outprobe_slots_without_collision(tmp_path):
    root = xmlio.parse_text(
        '<?xml version="1.0"?><Root><CIITDiagram>'
        '<Components><CODComposite><Objects/></CODComposite></Components>'
        '<Elements>'
        '<Item CiID="1" Class="CiCircuit"><CiCircuit><Nodes/><Components/><ProbeExts/></CiCircuit></Item>'
        '<Item CiID="2" Class="CiNode"><CiNode LocalName="&amp;ASCvin"/></Item>'
        '<Item CiID="3" Class="CIITLinkComp"><CIITLinkComp Node="2"><Points><Item X="0" Y="0"/><Item X="100" Y="0"/></Points></CIITLinkComp></Item>'
        '</Elements></CIITDiagram>'
        '<InstrumentsData/>'
        '<RefDesInfoContainer CIR="&amp;ASC#1/test:"><CIRToInfoMap>'
        '<CIRToInfoMapItem CIRKey="&amp;ASCPR1"><RefDesInfo IRPrefix="&amp;ASCOutProbe" IRNumber="-1"/></CIRToInfoMapItem>'
        '<CIRToInfoMapItem CIRKey="&amp;ASCPR2"><RefDesInfo IRPrefix="&amp;ASCOutProbe" IRNumber="1"/></CIRToInfoMapItem>'
        '</CIRToInfoMap></RefDesInfoContainer>'
        '<RefDesPrefixUsageMap/>'
        '<TotalProbeTriggers NextProbeID="1"/>'
        '</Root>'
    )
    lib_dir = tmp_path / "lib"
    struct = lib_dir / parts.STRUCT
    struct.mkdir(parents=True)
    (struct / "probe_element.xml").write_text('<Item CiID="10" Class="CiProbeExtComp"><CiProbeExtComp LocalName=""/></Item>', encoding="utf-8")
    (struct / "probe_symbol.xml").write_text('<Item ID="11" Class="CIITProbeExtComponent"><CIITProbeExtComponent/></Item>', encoding="utf-8")
    (struct / "probe_instrument.xml").write_text('<CSourceSymbolCollectNode CompLongName=""/>', encoding="utf-8")
    lib = parts.Library(lib_dir)
    outputs = sim.add_probes(root, ["vin"], lib, str(tmp_path / "test.ms14"))
    assert outputs == {"vin": "V(OutProbe2)"}


def test_mcp_parts_flag_preserves_force_and_guards_leading_hyphen(monkeypatch, tmp_path):
    import asyncio
    from mcp_adapter import server
    seen = []

    def fake_run_cli(args, timeout_s=300):
        seen.append(args)
        return {"ok": True, "status": "PASS", "exit_code": 0, "result": {}}

    monkeypatch.setattr(server, "_run_cli", fake_run_cli)
    monkeypatch.setenv("MULTISIMFORGE_MCP_ALLOW_LIBRARY_WRITES", "1")
    asyncio.run(server.parts("harvest", "design.ms14", force=True))
    assert "--force" in seen[-1]
    asyncio.run(server.parts("list", "--force"))
    assert "--" in seen[-1] and seen[-1].index("--") < seen[-1].index("--force")


def test_builder_rename_model_card_updates_named_subckt_ends_only():
    raw_named = (
        "* LT macro\r\n"
        ".SUBCKT LTC6244/LT__OPAMP__1 1 2 3 4 5\r\n"
        ".MODEL M_INT NMOS(KP=1e-4)\r\n"
        ".SUBCKT INNER_CELL 1 2\r\n"
        ".ENDS INNER_CELL\r\n"
        ".ENDS LTC6244/LT\r\n"
    )
    out_named = builder._rename_model_card(raw_named, "LTC6244/LT__OPAMP__1", "LTC6244/LT__OPAMP__2")
    assert ".SUBCKT LTC6244/LT__OPAMP__2 1 2 3 4 5" in out_named
    assert ".ENDS LTC6244/LT__OPAMP__2" in out_named
    assert ".MODEL M_INT NMOS" in out_named
    assert ".ENDS INNER_CELL" in out_named

    raw_bare = ".SUBCKT Bare__OPAMP__1 1 2\nR1 1 2 1k\n.ENDS\n"
    out_bare = builder._rename_model_card(raw_bare, "Bare__OPAMP__1", "Bare__OPAMP__2")
    assert ".SUBCKT Bare__OPAMP__2 1 2" in out_bare
    assert out_bare.rstrip().endswith(".ENDS")


def test_com_analyze_attaches_last_error_message_on_com_exception(tmp_path):
    from multisimforge import com

    ms14 = tmp_path / "fail.ms14"
    ms14.write_bytes(b"placeholder")

    class FailingCircuit:
        LastErrorMessage = "输出未找到。"
        SimulationState = 0

        def EnumOutputs(self, _):
            return ("V(OutProbe1)",)

        def DoDCOperatingPoint(self, _):
            return None

        def OutputReady(self, _):
            raise RuntimeError("(-2147467259, 'Unspecified error', None, None)")

    class FakeApp:
        def OpenFile(self, path):
            return FailingCircuit()

    with pytest.raises(RuntimeError, match=r"op analysis failed:.*LastErrorMessage='输出未找到。'"):
        com._analyze(FakeApp(), {"ms14": str(ms14), "analyses": [{"type": "op"}]})

