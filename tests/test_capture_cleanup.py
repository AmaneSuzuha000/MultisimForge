"""Failure cleanup cannot depend on the function returning the PID tuple."""
import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("win32api")
from multisimforge import capture  # noqa: E402


def test_run_once_cleans_window_even_when_command_raises(monkeypatch, tmp_path):
    closed = []
    monkeypatch.setattr(capture, "open_design", lambda path: (101, 9876, True))
    monkeypatch.setattr(capture, "_close", lambda pid, main=None: closed.append((pid, main)))
    monkeypatch.setattr(capture, "send_key", lambda *args: (_ for _ in ()).throw(RuntimeError("failed")))
    with pytest.raises(RuntimeError, match="failed"):
        capture.run_once(tmp_path / "rc.ms14", tmp_path, 0.1, 1)
    assert closed == [(9876, 101)]


def test_grab_releases_gdi_handles_when_printwindow_fails(monkeypatch):
    from types import SimpleNamespace
    actions = []

    class FakeMem:
        def GetSafeHdc(self):
            return 777

        def SelectObject(self, bitmap):
            return None

        def DeleteDC(self):
            actions.append("mem")

    class FakeSource:
        def CreateCompatibleDC(self):
            return FakeMem()

        def DeleteDC(self):
            actions.append("src")

    class FakeBitmap:
        def CreateCompatibleBitmap(self, source, w, h):
            return None

        def GetHandle(self):
            return 456

    monkeypatch.setattr(capture.win32gui, "GetWindowRect", lambda hwnd: (0, 0, 20, 20))
    monkeypatch.setattr(capture.win32gui, "GetWindowDC", lambda hwnd: 123)
    monkeypatch.setattr(capture.win32gui, "ReleaseDC", lambda hwnd, dc: actions.append("release"))
    monkeypatch.setattr(capture.win32gui, "DeleteObject", lambda h: actions.append("bitmap"))
    monkeypatch.setattr(capture.win32ui, "CreateDCFromHandle", lambda dc: FakeSource())
    monkeypatch.setattr(capture.win32ui, "CreateBitmap", lambda: FakeBitmap())
    monkeypatch.setattr(capture, "user32", SimpleNamespace(PrintWindow=lambda hwnd, hdc, flags: 0))
    with pytest.raises(RuntimeError, match="PrintWindow returned failure"):
        capture.grab(101)
    assert actions == ["bitmap", "mem", "src", "release"]
