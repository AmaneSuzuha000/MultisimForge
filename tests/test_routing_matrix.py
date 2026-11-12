"""Comprehensive validation matrix for schematic routing (open, corridor, dense multi-net, 50-200 parts, adversarial)."""
import time

import pytest

from multisimforge import builder, check, router


def _two_pin_comp(refdes: str, x: float, y: float):
    # Centered on (x, y) with body x±27 and left/right pins at (x±27, y) on the 9-unit grid
    box = {"refdes": refdes, "x": x - 27.0, "y": y - 18.0, "width": 54.0, "height": 36.0}
    c1 = {"x": x - 27.0, "y": y, "refdes": refdes, "direction": (-1, 0), "ground": False}
    c2 = {"x": x + 27.0, "y": y, "refdes": refdes, "direction": (1, 0), "ground": False}
    return box, c1, c2


def _assert_zero_box_or_overlap_violations(islands, boxes, occupied_all=None):
    rects = [router.rectangle(b) for b in boxes]
    segs_by_net = {}
    for net, _conns, edges, _junc in islands:
        for e in edges:
            for a, b in zip(e["path"], e["path"][1:]):
                assert a != b
                assert a[0] == b[0] or a[1] == b[1], f"non-orthogonal segment on {net}"
                for rect in rects:
                    assert not router.crosses_box(a, b, rect), f"segment {a}->{b} crossed box {rect}"
                segs_by_net.setdefault(net, []).append((a, b))
    nets = list(segs_by_net)
    for i, na in enumerate(nets):
        for nb in nets[i + 1:]:
            for a, b in segs_by_net[na]:
                for c, d in segs_by_net[nb]:
                    rel = router.segment_relation(a, b, c, d)
                    assert rel != "overlap", f"collinear overlap between {na} and {nb}: {a}->{b} vs {c}->{d}"


def test_routing_matrix_core_scenarios():
    # 1. Open space
    b1, c1a, c1b = _two_pin_comp("R1", 108, 108)
    b2, c2a, c2b = _two_pin_comp("R2", 324, 108)
    islands, fb = router.route_all({"n1": [c1b, c2a]}, [b1, b2])
    assert not fb
    _assert_zero_box_or_overlap_violations(islands, [b1, b2])

    # 2. Constrained corridor + multi-net crossing
    b1, c1a, c1b = _two_pin_comp("R1", 108, 108)
    b2, c2a, c2b = _two_pin_comp("R2", 108, 216)
    blk, _, _ = _two_pin_comp("BLK", 252, 162)
    b3, c3a, c3b = _two_pin_comp("R3", 396, 108)
    b4, c4a, c4b = _two_pin_comp("R4", 396, 216)
    conns = {"cross1": [c1b, c4a], "cross2": [c2b, c3a], "left": [c1a, c2a], "right": [c3b, c4b]}
    boxes = [b1, b2, blk, b3, b4]
    islands, fb = router.route_all(conns, boxes)
    assert not fb
    _assert_zero_box_or_overlap_violations(islands, boxes)

    # 3. Blocked pin escape at default clearance (9 units) that succeeds on escalated clearance
    b_src, s1, s2 = _two_pin_comp("R1", 108, 108)
    # Wall touching right at 9 units (x in 144..171, y in 90..126 -> pin at x=135 escapes around top/bottom)
    wall = {"refdes": "W1", "x": 153.0, "y": 90.0, "width": 27.0, "height": 36.0}
    b_dst, d1, d2 = _two_pin_comp("R2", 288, 108)
    islands, fb = router.route_all({"esc": [s2, d1]}, [b_src, wall, b_dst])
    assert not fb
    _assert_zero_box_or_overlap_violations(islands, [b_src, wall, b_dst])

    # 4. Same-net multi-pin star/tree branches
    b_a, _, a_out = _two_pin_comp("R1", 108, 108)
    b_b, b_in, _ = _two_pin_comp("R2", 288, 108)
    b_c, c_in, _ = _two_pin_comp("R3", 288, 216)
    b_d, d_in, _ = _two_pin_comp("R4", 468, 162)
    islands, fb = router.route_all({"bus": [a_out, b_in, c_in, d_in]}, [b_a, b_b, b_c, b_d])
    assert not fb
    assert len(islands) == 1 and len(islands[0][3]) >= 1  # tree junctions created
    _assert_zero_box_or_overlap_violations(islands, [b_a, b_b, b_c, b_d])

    # 5. Adversarial completely sealed box -> must FAIL (no silent box crossing)
    sealed_src, _, sealed_out = _two_pin_comp("R_SEAL", 108, 108)
    enclosure = [
        {"refdes": "E_TOP", "x": 18.0, "y": 54.0, "width": 189.0, "height": 36.0},
        {"refdes": "E_BOT", "x": 18.0, "y": 126.0, "width": 189.0, "height": 36.0},
        {"refdes": "E_LFT", "x": 18.0, "y": 54.0, "width": 36.0, "height": 108.0},
        {"refdes": "E_RGT", "x": 171.0, "y": 54.0, "width": 36.0, "height": 108.0},
    ]
    out_dst, dst_in, _ = _two_pin_comp("R_OUT", 360, 108)
    islands, fb = router.route_all({"trapped": [sealed_out, dst_in]}, [sealed_src, *enclosure, out_dst])
    assert fb and fb[0]["net"] == "trapped"


@pytest.mark.parametrize("n_parts", [50, 100, 200])
def test_routing_scale_benchmark(n_parts):
    """50 / 100 / 200 component grid benchmark: zero electrical/obstacle failures and fast runtime."""
    cols = 10
    boxes, conns = [], {}
    prev_pin = None
    for idx in range(n_parts):
        r, c = divmod(idx, cols)
        x = 180.0 + c * 216.0
        y = 144.0 + r * 144.0
        box, p_l, p_r = _two_pin_comp(f"R{idx}", x, y)
        boxes.append(box)
        if prev_pin is not None and c > 0:
            conns[f"n{idx}"] = [prev_pin, p_l]
        prev_pin = p_r
    t0 = time.perf_counter()
    islands, fb = router.route_all(conns, boxes)
    elapsed = time.perf_counter() - t0
    assert not fb
    assert len(islands) == len(conns)
    assert elapsed < 5.0
    _assert_zero_box_or_overlap_violations(islands, boxes)


@pytest.mark.needs_parts
def test_mirrored_rotated_and_explicit_wires_end_to_end(tmp_path):
    # Mirrored + rotated parts in real build() and explicit wire path verification
    out = tmp_path / "orient_explicit.ms14"
    spec = {
        "netlist": "\n".join([
            "V1 vin 0 DC 5",
            "R1 vin mid 1k",
            "R2 mid vout 2k",
            "C1 vout 0 100n",
        ]) + "\n",
        "output": str(out),
        "layout": {
            "V1": [108, 252, 0, "mirror-y"],
            "R1": [252, 180, 180],
            "R2": [396, 180, 0, "mirror-x"],
            "C1": [540, 252, 90],
            "GND": [[108, 360], [540, 360]],
        },
        "wires": {
            "mid": [[[225, 180], [225, 126], [414, 126], [414, 180]]],
        },
    }
    res = builder.build(spec, auto_repair=False)
    rep = check.check_file(out, res["expected"])
    assert rep["problems"] == []
