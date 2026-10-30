"""Probe placement is what makes sim read a net instead of guessing.

net_wires decides which wire belongs to which net and which coordinates are component
pins; a mistake there sends the probe onto an instrument terminal, where Multisim drops
it and the measurement silently comes back empty.
"""
import json
from pathlib import Path

import pytest

from multisimforge import builder, sim, xmlio

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    out = tmp_path_factory.mktemp("probes") / "rc.ms14"
    spec = json.loads((ROOT / "examples" / "rc_lowpass.json").read_text(encoding="utf-8"))
    return xmlio.load(builder.build(dict(spec, output=str(out)))["ms14"])


@pytest.mark.needs_parts
def test_net_wires_groups_wires_by_net_name(root):
    wires, pins = sim.net_wires(root)
    assert {"vin", "vout"} <= set(wires)
    for net, paths in wires.items():
        assert paths, f"net {net} has no geometry"
        for path in paths:
            assert len(path) >= 2, "a wire needs at least two points"


@pytest.mark.needs_parts
def test_component_pins_are_reported_but_instrument_terminals_are_not(root):
    wires, pins = sim.net_wires(root)
    assert pins, "no component pins were found"
    # the oscilloscope occupies six terminals; none of them may look like a probe target
    scope_terminals = {(p[0], p[1]) for path in wires.get("nc1", []) for p in path}
    assert not (scope_terminals & pins)


@pytest.mark.needs_parts
def test_probe_point_prefers_a_pin_endpoint(root):
    wires, pins = sim.net_wires(root)
    point = sim._probe_point(wires["vin"], [], pins)
    assert point in pins, f"{point} is not a component pin"


def test_probe_point_falls_back_to_a_wire_midpoint():
    path = [(0.0, 0.0), (100.0, 0.0)]
    assert sim._probe_point([path], [], set()) == (50.0, 0.0)


def test_probe_point_gives_up_on_a_wire_too_short_to_hold_one():
    assert sim._probe_point([[(0.0, 0.0), (10.0, 0.0)]], [], set()) is None


def test_blocked_detects_a_point_on_a_segment_only():
    path = [(0.0, 0.0), (100.0, 0.0)]
    assert sim._blocked((50.0, 0.0), [path])
    assert not sim._blocked((50.0, 10.0), [path])
    assert not sim._blocked((150.0, 0.0), [path]), "past the end of the segment is not on it"


@pytest.mark.needs_parts
def test_a_probe_is_added_for_each_requested_net(root, tmp_path):
    from multisimforge.parts import Library

    target = tmp_path / "probed.ms14"
    xmlio.save(root, target)                       # add_probes edits in place
    out = sim.add_probes(xmlio.load(target), ["vin", "vout"], Library(), str(target))
    assert out, "add_probes reported nothing for two nets that exist"