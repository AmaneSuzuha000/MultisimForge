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


@pytest.mark.parametrize("text,expected", [
    ("1k", 1e3), ("1K", 1e3), ("4.7k", 4.7e3), ("1k2", 1.2e3), ("4u7", 4.7e-6),
    ("2meg", 2e6), ("1MEG", 1e6), ("1M", 1e6), ("10m", 0.01), ("1e3", 1e3),
    ("1E-3", 1e-3), ("-5", -5.0), ("+5", 5.0), (" 2.2k ", 2.2e3), ("1kohm", 1e3),
    ("10mV", 0.01), ("5Hz", 5.0), ("0", 0.0), ("1t", 1e12), ("1p", 1e-12),
])
def test_value_forms(text, expected):
    assert netlist.parse_value(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["1k garbage", "4.7kjunk", "1kgarbage", "abc", "", "1k2x", "--5", "nan", "inf"])
def test_value_rejects_trailing_garbage(text):
    """0.1.1 promised this and it was never implemented; a typo must not silently become 1k."""
    with pytest.raises(ValueError):
        netlist.parse_value(text)
