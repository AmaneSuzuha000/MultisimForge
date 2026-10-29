"""Offline regression: netlist parsing."""
import pytest

from multisimforge import netlist


def test_values():
    assert netlist.parse_value("4.7k") == pytest.approx(4700)
    assert netlist.parse_value("0.1u") == pytest.approx(1e-7)
    assert netlist.parse_value("2meg") == pytest.approx(2e6)


def test_basic_parse():
    items = netlist.parse("R1 in out 1k\nC1 out 0 0.1u\n")
    assert [i.refdes for i in items] == ["R1", "C1"]
    assert items[1].nets == ["out", "0"]
    assert items[1].value == "0.1u"


def test_named_pins():
    items = netlist.parse("U1 LM555CN GND=0 OUT=out VCC=vcc\n")
    assert items[0].part == "LM555CN"
    assert items[0].named == {"GND": "0", "OUT": "out", "VCC": "vcc"}


def test_comments_and_continuation():
    text = "* comment\nR1 in out\n+ 1k\n.end\n"
    items = netlist.parse(text)
    assert items[0].refdes == "R1" and items[0].value == "1k"
