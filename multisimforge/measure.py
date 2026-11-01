"""Data-driven numerical cursors as an honest alternative to Multisim's GUI cursors.

NI Multisim 14.3 ignores cursor positions stored in a .ms14 on open. These readouts
are interpolated from the *actual* CSV exported by the native simulation, not
painted onto a GUI screenshot and not invented from circuit specifications.
"""
from __future__ import annotations

import bisect
import csv
import math
from pathlib import Path


def _unwrap_degrees(values: list[float]) -> list[float]:
    out: list[float] = []
    offset = 0.0
    for i, value in enumerate(values):
        if i:
            offset -= 360 * round((value + offset - out[-1]) / 360)
        out.append(value + offset)
    return out


def measure_csv(path, column: str, x1: float, x2: float, *, axis: str = "auto") -> dict:
    """Interpolate two cursor readings within measured data; never extrapolate.

    AC frequency axes interpolate in log(f) and time axes interpolate in t.
    Phase degree columns are unwrapped prior to interpolation. Raw CSV is untouched.
    """
    path = Path(path)
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        x_name = "freq" if "freq" in fields else ("time" if "time" in fields else None)
        if x_name is None:
            raise ValueError(f"CSV must contain a freq or time column; found {fields}")
        if column not in fields or column == x_name:
            raise ValueError(f"data column {column!r} not found; choose from {[c for c in fields if c != x_name]}")
        xs, ys = [], []
        for rownum, row in enumerate(reader, start=2):
            try:
                x, y = float(row[x_name]), float(row[column])
            except (ValueError, TypeError) as exc:
                raise ValueError(f"CSV row {rownum} is missing numeric cursor data") from exc
            if not (math.isfinite(x) and math.isfinite(y)):
                raise ValueError(f"CSV row {rownum} contains a non-finite value")
            if xs and x <= xs[-1]:
                raise ValueError(f"CSV x values must increase strictly (row {rownum})")
            xs.append(x)
            ys.append(y)
    if len(xs) < 2:
        raise ValueError("CSV must contain at least two samples to interpolate cursors")
    if axis == "auto":
        axis = "log" if x_name == "freq" else "linear"
    if axis not in ("log", "linear"):
        raise ValueError("axis must be auto, linear or log")
    if axis == "log" and (min(xs) <= 0 or min(x1, x2) <= 0):
        raise ValueError("logarithmic cursors require positive x coordinates")
    if column.endswith("_phase_deg"):
        ys = _unwrap_degrees(ys)
    def interpolate(x):
        if not math.isfinite(x) or x < xs[0] or x > xs[-1]:
            raise ValueError(f"cursor x={x:g} is outside measured range [{xs[0]:g}, {xs[-1]:g}]")
        k = bisect.bisect_left(xs, x)
        if k < len(xs) and xs[k] == x:
            return ys[k]
        left, right = k - 1, k
        if axis == "log":
            t = math.log(x / xs[left]) / math.log(xs[right] / xs[left])
        else:
            t = (x - xs[left]) / (xs[right] - xs[left])
        return ys[left] + t * (ys[right] - ys[left])
    y1, y2 = interpolate(x1), interpolate(x2)
    delta_x = x2 - x1
    return {
        "source_csv": str(path.resolve()), "method": "simulation_csv_interpolation",
        "not_gui_cursors": True, "x_column": x_name, "y_column": column, "axis": axis,
        "samples": len(xs), "measured_range": [xs[0], xs[-1]],
        "cursor1": {"x": x1, "y": y1}, "cursor2": {"x": x2, "y": y2},
        "delta_x": delta_x, "delta_y": y2 - y1,
        "inverse_delta_x": 1 / delta_x if delta_x else None,
    }
