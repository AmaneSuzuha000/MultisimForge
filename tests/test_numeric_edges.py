"""Scientific data must never be silently clamped, truncated or fabricated."""
import math

import pytest

from multisimforge import cli, netlist, sim


@pytest.mark.parametrize("v", [float("nan"), float("inf"), -float("inf"), "1e999"])
def test_nonfinite_input_rejected(v):
    with pytest.raises(ValueError, match="non-finite"):
        netlist.parse_value(v)


@pytest.mark.parametrize("txt", [
    "V1 a", "V1 a 0", "I1 a",
    "V1 a 0 SIN(0 1 100 0 0 0 999)",
    "R1 a b 1k PART extra",
])
def test_netlist_does_not_drop_required_or_extra_tokens(txt):
    with pytest.raises(ValueError):
        netlist.parse(txt)


def test_ac_cannot_report_out_of_sweep_value():
    with pytest.raises(ValueError, match="outside measured sweep"):
        sim.ac_summary([10, 100], [complex(1), complex(0.5)], at=[1000])


@pytest.mark.parametrize("freq", [[0, 1], [100, 10], [10, 10]])
def test_ac_invalid_frequency_axis(freq):
    with pytest.raises(ValueError, match="strictly increasing|positive"):
        sim.ac_summary(freq, [1 + 0j, 1 + 0j])


def test_transient_crossings_interpolated_between_samples():
    ts = [i * 0.05 for i in range(61)]
    vs = [math.sin(2 * math.pi * t) for t in ts]
    result = sim.tran_summary(ts, vs)
    assert result["period"] == pytest.approx(1.0, rel=0.01)


def test_cli_ac_rejects_fractional_points_per_decade():
    from argparse import Namespace
    a = Namespace(nets="vout", ac="10,100k,1.5", tran=None, op=False, ms14="x.ms14",
                  out=None, at=None, csv=None)
    setattr(a, "in", None)
    with pytest.raises(ValueError, match="integer"):
        cli.cmd_sim(a)
