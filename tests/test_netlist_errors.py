"""Every rejected netlist line must say what the author should have written.

These are the messages a user actually sees, so each one is asserted rather than just
executed: a wrong-but-plausible parse is worse than a rejection.
"""
import pytest

from multisimforge import netlist


def test_sin_needs_its_three_arguments():
    with pytest.raises(ValueError, match="SIN needs at least"):
        netlist.parse("V1 a 0 SIN(0 1)")


def test_pulse_needs_all_seven_arguments():
    with pytest.raises(ValueError, match="PULSE needs 7 parameters"):
        netlist.parse("V1 a 0 PULSE(0 1 0 1n 1n)")


def test_pulse_is_parsed_into_seven_numbers():
    inst = netlist.parse("V1 a 0 PULSE(0 1 0 1n 1n 500u 1m)")[0]
    assert inst.source["type"] == "pulse"
    assert inst.source["pulse"] == [0.0, 1.0, 0.0, 1e-9, 1e-9, 500e-6, 1e-3]


def test_a_bare_number_is_a_dc_value():
    assert netlist.parse("V1 a 0 12")[0].source["dc"] == 12.0


def test_an_unknown_source_parameter_is_named():
    with pytest.raises(ValueError, match="unrecognised source parameter"):
        netlist.parse("V1 a 0 WAT(1 2 3)")


def test_duplicate_refdes_is_caught_regardless_of_case():
    with pytest.raises(ValueError, match="duplicate refdes"):
        netlist.parse("R1 a 0 1k\nr1 b 0 2k")


def test_an_instrument_line_must_end_with_its_keyword():
    with pytest.raises(ValueError, match="must end with"):
        netlist.parse("XSC1 a 0 b 0 SCOPEISH")


def test_a_stray_word_after_the_instrument_keyword_is_refused():
    """It used to become an empty-valued parameter named EXTRA."""
    with pytest.raises(ValueError, match="not a key=value"):
        netlist.parse("XSC1 a 0 b 0 OSCILLOSCOPE extra")


def test_instrument_settings_are_key_value_pairs():
    inst = netlist.parse("XSC1 a 0 b 0 OSCILLOSCOPE TS=1m SCALE=2")[0]
    assert inst.params == {"TS": "1m", "SCALE": "2"}


def test_a_passive_needs_both_pins_and_a_value():
    with pytest.raises(ValueError, match="node1 node2 value"):
        netlist.parse("R1 a 0")


def test_an_explicit_library_part_is_kept():
    inst = netlist.parse("C1 a 0 10u CAP_ELECTROLYTIC")[0]
    assert inst.part == "CAP_ELECTROLYTIC"
    assert netlist.parse_value(inst.value) == pytest.approx(10e-6)


def test_a_current_source_with_only_an_ac_amplitude_is_refused():
    """It used to parse as DC_CURRENT with dc=0, silently simulating no bias at all."""
    with pytest.raises(ValueError, match="only DC current sources"):
        netlist.parse("I1 a 0 AC 1")


def test_a_current_source_with_an_explicit_bias_and_ac_is_allowed():
    inst = netlist.parse("I1 a 0 DC 1m AC 1")[0]
    assert inst.part == "DC_CURRENT"
    assert inst.source["dc"] == pytest.approx(1e-3)
    assert inst.source["ac"] == (1.0, 0.0)


def test_three_pin_parts_take_three_nodes_and_a_model():
    inst = netlist.parse("Q1 c b e 2N3904")[0]
    assert inst.nets == ["c", "b", "e"]
    assert inst.part == "2N3904"


def test_a_three_pin_part_with_the_wrong_node_count_is_named():
    with pytest.raises(ValueError, match="Q1: write Q1 node node node model"):
        netlist.parse("Q1 c b 2N3904")


def test_pin_name_form_assigns_nets_to_named_pins():
    """The mapping lives in .named, not .nets: the builder indexes it by pin."""
    inst = netlist.parse("U1 7404N 1A=a 1Y=b")[0]
    assert inst.part == "7404N"
    assert inst.named == {"1A": "a", "1Y": "b"}
    assert inst.nets == []


def test_a_part_line_without_enough_fields_is_refused():
    with pytest.raises(ValueError, match="write .* node... model"):
        netlist.parse("X9 MODELONLY")


def test_an_unsupported_line_is_refused_with_the_line_echoed():
    with pytest.raises(ValueError, match="unsupported part line"):
        netlist.parse("!! nonsense")


def test_dot_lines_are_comments():
    assert netlist.parse(".end\nR1 a 0 1k")[0].refdes == "R1"