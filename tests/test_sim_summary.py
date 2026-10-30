"""Result summaries must fail with a readable message, not an arithmetic error.

Both helpers used to index into an empty result: an AC sweep that produced no points
raised "max() iterable argument is empty" and an empty transient raised ZeroDivisionError,
so the CLI reported an ERROR envelope whose cause was invisible.
"""
import math

import pytest

from multisimforge import sim


def test_ac_summary_rejects_an_empty_sweep():
    with pytest.raises(ValueError, match="no points"):
        sim.ac_summary([], [])


def test_tran_summary_rejects_an_empty_run():
    with pytest.raises(ValueError, match="no samples"):
        sim.tran_summary([], [])


def test_length_mismatch_is_reported():
    with pytest.raises(ValueError, match="frequencies but"):
        sim.ac_summary([10, 100], [complex(1, 0)])
    with pytest.raises(ValueError, match="timestamps but"):
        sim.tran_summary([0, 1], [1.0])


def test_first_order_rc_cutoff_is_found_at_minus_three_db():
    """A single-pole RC has |H| = 1/sqrt(2) at 1/(2*pi*R*C); the summary must land there."""
    rc = 1e-3
    freq = [10 ** (k / 200) for k in range(0, 1200)]          # 1 Hz .. 1 MHz, fine log grid
    h = [1 / (1 + 1j * 2 * math.pi * f * rc) for f in freq]
    out = sim.ac_summary(freq, h)
    # the peak sits at the lowest swept frequency, where |H| is 1 to within the grid step
    assert out["peak"]["f"] == pytest.approx(freq[0], rel=1e-9)
    assert out["peak"]["dB"] == pytest.approx(0.0, abs=0.01)
    assert len(out["cutoff"]) == 1
    assert out["cutoff"][0]["f"] == pytest.approx(1 / (2 * math.pi * rc), rel=1e-3)
    assert out["cutoff"][0]["dB"] == pytest.approx(-3.0103, abs=0.01)


def test_phase_is_unwrapped_across_the_360_degree_boundary():
    """A two-pole rolloff passes -180 degrees; the summary must not jump to +180."""
    freq = [10 ** (k / 40) for k in range(0, 200)]
    h = [1 / ((1 + 1j * 2 * math.pi * f * 1e-3) ** 3) for f in freq]
    phase = sim._unwrap([math.degrees(math.atan2(v.imag, v.real)) for v in h])
    for a, b in zip(phase, phase[1:]):
        assert abs(b - a) < 90, "unwrapping left a 360 degree jump in the phase trace"
    assert phase[-1] < -180


def test_transient_period_from_zero_crossings():
    """A 1 kHz sine must report a 1 ms period and 1 kHz frequency."""
    n = 20000
    t = [k * 1e-5 for k in range(n)]                          # 10 us steps
    v = [math.sin(2 * math.pi * 1000 * x) for x in t]
    out = sim.tran_summary(t, v)
    assert out["period"] == pytest.approx(1e-3, rel=1e-3)
    assert out["freq"] == pytest.approx(1000.0, rel=1e-3)
    assert out["rms"] == pytest.approx(1 / math.sqrt(2), rel=1e-2)


def test_transient_without_enough_crossings_reports_no_period():
    flat = sim.tran_summary([0, 1, 2], [1.0, 1.0, 1.0])
    assert flat["period"] is None and flat["freq"] is None
