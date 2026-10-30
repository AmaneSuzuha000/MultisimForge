"""inspect must describe a design faithfully, without Multisim.

This existed as a command but had no test at all: it is the only way a caller can read
what a .ms14 actually contains, so a silent misread is worse than a crash.
"""
import json
from pathlib import Path

import pytest

from multisimforge import builder, check, netlist

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("inspect") / "rc.ms14"
    spec = json.loads((ROOT / "examples" / "rc_lowpass.json").read_text(encoding="utf-8"))
    return Path(builder.build(dict(spec, output=str(out)))["ms14"])


@pytest.mark.needs_parts
def test_inspect_lists_the_parts_with_their_values(built):
    info = check.inspect(built)
    by_ref = {c["refdes"]: c for c in info["components"]}
    assert set(by_ref) == {"V1", "R1", "C1", "XSC1"}
    assert by_ref["R1"]["part"] == "RESISTOR_VIRTUAL"
    assert by_ref["C1"]["part"] == "CAPACITOR_VIRTUAL"
    # the file stores an SI-normalised form, so compare the value, not the spelling
    assert netlist.parse_value(by_ref["R1"]["value"]) == pytest.approx(1e3)
    assert netlist.parse_value(by_ref["C1"]["value"]) == pytest.approx(0.1e-6)


@pytest.mark.needs_parts
def test_inspect_reports_the_nets_the_netlist_declared(built):
    info = check.inspect(built)
    # nc1/nc2 are the oscilloscope's unused pins; they are real nets, just unconnected
    assert set(info["nets"]) == {"vin", "vout", "0", "nc1", "nc2"}
    assert sorted(info["nets"]["vin"]) == ["R1.1", "V1.1", "XSC1.1"]
    assert sorted(info["nets"]["vout"]) == ["C1.1", "R1.2", "XSC1.2"]


@pytest.mark.needs_parts
def test_inspect_reads_the_oscilloscope_settings(built):
    info = check.inspect(built)
    settings = info["instruments"]["XSC1"]
    assert settings["NI_EWB_TIMEBASE_SCALE_VALUE"]
    assert "NI_EWB_A_CHANNEL_SCALE_VALUE" in settings


@pytest.mark.needs_parts
def test_components_are_sorted_and_ground_is_not_listed(built):
    refs = [c["refdes"] for c in check.inspect(built)["components"]]
    assert refs == sorted(refs)
    assert "0" not in refs, "a ground symbol is not a component with a reference designator"