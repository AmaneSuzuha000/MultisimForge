"""Numerical cursor readouts require actual data and must not be confused with GUI cursors."""
import pytest

from multisimforge.measure import measure_csv


def test_transient_linear_cursor_and_difference(tmp_path):
    p = tmp_path / "tran.csv"
    p.write_text("time,vout\n0,0\n0.001,2\n0.002,4\n", encoding="utf-8")
    result = measure_csv(p, "vout", 0.0005, 0.0015)
    assert result["not_gui_cursors"] is True
    assert result["cursor1"]["y"] == pytest.approx(1)
    assert result["cursor2"]["y"] == pytest.approx(3)
    assert result["delta_y"] == pytest.approx(2)
    assert result["inverse_delta_x"] == pytest.approx(1000)


def test_frequency_interpolation_uses_log_axis(tmp_path):
    p = tmp_path / "ac.csv"
    p.write_text("freq,vout_mag\n10,0\n100,2\n1000,4\n", encoding="utf-8")
    result = measure_csv(p, "vout_mag", 10**1.5, 1000)
    assert result["axis"] == "log"
    assert result["cursor1"]["y"] == pytest.approx(1)
    assert result["cursor2"]["y"] == pytest.approx(4)


def test_phase_does_not_interpolate_through_zero_at_wrap(tmp_path):
    p = tmp_path / "ac.csv"
    p.write_text("freq,vout_phase_deg\n10,170\n100,-170\n", encoding="utf-8")
    result = measure_csv(p, "vout_phase_deg", 10, 10**1.5)
    assert result["cursor2"]["y"] == pytest.approx(180)


def test_out_of_range_cursor_is_not_extrapolated(tmp_path):
    p = tmp_path / "tran.csv"
    p.write_text("time,vout\n0,0\n1,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="outside measured range"):
        measure_csv(p, "vout", 0, 2)


def test_bad_data_and_column_are_rejected(tmp_path):
    p = tmp_path / "tran.csv"
    p.write_text("time,vout\n0,0\n1,nan\n", encoding="utf-8")
    with pytest.raises(ValueError, match="non-finite"):
        measure_csv(p, "vout", 0, 1)
    with pytest.raises(ValueError, match="choose from"):
        measure_csv(p, "voltage", 0, 1)
