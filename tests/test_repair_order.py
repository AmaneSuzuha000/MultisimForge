"""The auto-repair search must reach the object that actually blocks routing.

0.1.3 tried one object's twelve offsets in a row before moving to the next, inside a flat
three-second budget. With four movable objects that exhausts the budget on the first two,
so a blocker sitting later in the list was never nudged: a layout that routes after moving
GND[3] by (0, 24) failed the build with "no obstacle-free route".
"""
import itertools
import json
from pathlib import Path

import pytest

from multisimforge import builder
from multisimforge.router import RouteError

ROOT = Path(__file__).resolve().parent.parent


def _spec(out):
    data = json.loads((ROOT / "examples" / "rc_lowpass.json").read_text(encoding="utf-8"))
    data["output"] = str(out)
    return data


def test_repair_variants_move_every_object_before_deepening():
    """Breadth first: the first N candidates move N different objects."""
    spec = _spec("unused.ms14")
    variants = itertools.islice(builder._repair_variants(spec, 48), 4)
    names = [next(iter(moves)) for _candidate, moves in variants]
    assert len(set(names)) == len(names), f"the search repeated an object early: {names}"


def test_repair_variants_start_with_small_offsets():
    """Deepening comes later, so the first move for each object is a half grid step."""
    spec = _spec("unused.ms14")
    seen = set()
    for candidate, moves in builder._repair_variants(spec, 48):
        for name in moves:
            dx, dy = moves[name]
            if name in seen:
                continue
            seen.add(name)
            assert max(abs(dx), abs(dy)) <= 12, f"{name} was nudge by ({dx}, {dy}) before the others got a turn"
            if len(seen) == 4:
                return
    pytest.fail("fewer than four movable objects were offered")


@pytest.mark.needs_parts
def test_repair_reaches_a_late_blocker(tmp_path):
    """A ground symbol dropped into a routing corridor is found and moved."""
    spec = _spec(tmp_path / "blocked.ms14")
    spec["layout"]["GND"] = list(spec["layout"]["GND"]) + [[252, 216]]
    with pytest.raises(RouteError):
        builder.build(spec, auto_repair=False)            # it really is unroutable as written
    result = builder.build(spec)
    assert result["moved_parts"], "auto-repair did not move the blocker"
    assert Path(result["ms14"]).is_file()
