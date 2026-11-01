"""Failure cleanup cannot depend on the function returning the PID tuple."""
import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("win32api")
from multisimforge import capture


def test_run_once_cleans_window_even_when_command_raises(monkeypatch, tmp_path):
    closed = []
    monkeypatch.setattr(capture, "open_design", lambda path: (101, 9876, True))
    monkeypatch.setattr(capture, "_close", lambda pid, main=None: closed.append((pid, main)))
    monkeypatch.setattr(capture, "send_key", lambda *args: (_ for _ in ()).throw(RuntimeError("failed")))
    with pytest.raises(RuntimeError, match="failed"):
        capture.run_once(tmp_path / "rc.ms14", tmp_path, 0.1, 1)
    assert closed == [(9876, 101)]
