"""Template harvesting reads real Multisim files, so it must fail honestly.

This module builds the part library, which every later build depends on, and had no
test at all. The important property is that a missing or unreadable sample is named
rather than turning into a bare StopIteration somewhere in the middle of extraction.
"""
from pathlib import Path

import pytest

from multisimforge import structural, xmlio

SAMPLES = Path("C:/Users/Public/Documents/National Instruments/Circuit Design Suite 14.3/samples")
has_samples = (SAMPLES / structural.RLC_SAMPLE).is_file() and (SAMPLES / structural.PROBE_SAMPLE).is_file()


pytestmark = pytest.mark.skipif(not has_samples, reason="NI sample designs are not installed")


def test_extracts_every_template_from_the_samples(tmp_path):
    written = structural.extract(SAMPLES, tmp_path)
    for name in ("node_v0.xml", "node_named.xml", "wire.xml", "wire_named.xml",
                 "extpin.xml", "junction_member.xml", "junction_owner.xml",
                 "probe_element.xml", "probe_symbol.xml", "probe_instrument.xml", "simstate.txt"):
        assert name in written, f"{name} was not produced"
        assert (tmp_path / name).stat().st_size > 0, f"{name} is empty"


def test_extracted_templates_are_valid_xml(tmp_path):
    for name in structural.extract(SAMPLES, tmp_path):
        if name.endswith(".xml"):
            assert xmlio.parse_text((tmp_path / name).read_text(encoding="utf-8")) is not None


def test_simstate_is_a_string_a_caller_can_parse(tmp_path):
    structural.extract(SAMPLES, tmp_path)
    state = (tmp_path / "simstate.txt").read_text(encoding="utf-8")
    assert "DEFAULT_INST_SETTINGS" in state or "TSTOP" in state


def test_names_the_missing_sample(tmp_path):
    """A renamed sample must be reported by path, not as a bare StopIteration."""
    (tmp_path / "samples").mkdir()
    with pytest.raises(FileNotFoundError, match="RLC Values"):
        structural.extract(tmp_path / "samples", tmp_path / "out")


def test_shell_from_blank_empties_the_containers(tmp_path):
    blank = SAMPLES / structural.RLC_SAMPLE
    if not blank.is_file():
        pytest.skip("no blank design to shell")
    root = structural.shell_from_blank(blank)
    for composite in root.iter("CODComposite"):
        for name in ("Objects", "ReferencedComponents"):
            node = composite.find(name)
            if node is not None:
                assert len(list(node)) == 0