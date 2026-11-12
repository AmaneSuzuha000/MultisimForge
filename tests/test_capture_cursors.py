"""Tests for background Win32 capture robustness, non-activating launch, and closed-loop cursor readouts."""
import types

import pytest

from multisimforge import capture


def test_parse_panel_value_and_scope_bode_readouts():
    assert capture.parse_panel_value("3.512 ms") == pytest.approx(3.512e-3)
    assert capture.parse_panel_value("-154.227 mV") == pytest.approx(-0.154227)
    assert capture.parse_panel_value("967.867 Hz") == pytest.approx(967.867)
    assert capture.parse_panel_value("-1.367 dB") == pytest.approx(-1.367)
    assert capture.parse_panel_value("--- ") is None
    assert capture.parse_panel_value("") is None

    xsc_txt = "时间\t通道_A\t通道_B\n3.512 ms\t-154.227 mV\t785.249 mV\n1.483 ms\t208.769 mV\t1.043 V\n-2.029 ms\t362.996 mV\t258.010 mV\n"
    parsed = capture.parse_scope_readout(xsc_txt)
    assert parsed["t1"] == pytest.approx(3.512e-3)
    assert parsed["t2"] == pytest.approx(1.483e-3)
    assert parsed["dt"] == pytest.approx(-2.029e-3)
    assert parsed["a1"] == pytest.approx(-0.154227)
    assert parsed["b2"] == pytest.approx(1.043)

    bode = capture.parse_bode_readout("967.867 Hz", "-1.367 dB")
    assert bode["freq"] == pytest.approx(967.867)
    assert bode["y"] == pytest.approx(-1.367)


def test_top_windows_handles_stale_hwnd_error_without_crashing(monkeypatch):
    if capture.win32gui is None:
        pytest.skip("Windows-only Win32 test")

    class FakeWin32Gui:
        @staticmethod
        def EnumWindows(cb, param):
            assert cb(101, param) is True
            assert cb(102, param) is True

        @staticmethod
        def IsWindowVisible(hwnd):
            if hwnd == 101:
                raise capture._WIN_ERR[0](1400, "Invalid window handle", "IsWindowVisible")
            return True

        @staticmethod
        def GetWindowText(hwnd):
            return "Good"

        @staticmethod
        def GetClassName(hwnd):
            return "Multisim"

    monkeypatch.setattr(capture, "win32gui", FakeWin32Gui)
    monkeypatch.setattr(capture, "win32process", types.SimpleNamespace(GetWindowThreadProcessId=lambda h: (1, 4242)))
    assert capture.top_windows() == [(102, "Good", "Multisim", 4242)]


def test_open_design_uses_sw_shownoactivate_by_default(tmp_path, monkeypatch):
    if capture.win32con is None:
        pytest.skip("Windows-only test")
    ms14 = tmp_path / "demo.ms14"
    ms14.write_bytes(b"placeholder")
    seen_si = []
    seen_show = []

    class DummyProc:
        pid = 999
        def poll(self):
            return None

    monkeypatch.setattr(capture, "_require_capture_deps", lambda: None)
    monkeypatch.setattr(capture, "design_windows", lambda stem: [(111, 999)])
    monkeypatch.setattr(capture, "design_windows", lambda stem, _calls=[0]: [] if _calls.__setitem__(0, _calls[0] + 1) or _calls[0] == 1 else [(111, 999)])
    monkeypatch.setattr(capture.subprocess, "Popen", lambda argv, startupinfo=None: seen_si.append(startupinfo) or DummyProc())
    monkeypatch.setattr(capture.win32gui, "ShowWindow", lambda h, flag: seen_show.append(flag))
    monkeypatch.setattr(capture, "send_key", lambda *a: None)
    monkeypatch.setattr(capture.time, "sleep", lambda s: None)

    main, pid, launched = capture.open_design(ms14, desktop_screen=False)
    assert (main, pid, launched) == (111, 999, True)
    assert seen_si and seen_si[0] is not None
    assert seen_si[0].wShowWindow == capture.win32con.SW_SHOWNOACTIVATE
    assert seen_show == [], "default capture must not call SW_MAXIMIZE (which activates the window)"
