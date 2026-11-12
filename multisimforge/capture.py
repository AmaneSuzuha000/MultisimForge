"""Open a .ms14 → run an interactive simulation → capture instrument panels and the circuit window (real Multisim screenshots).

Run with the system Python (requires pywin32, Pillow, numpy, scipy). Whether instrument panels appear, their ranges,
and the simulation end time are all written into the .ms14 by the builder. During capture the Multisim window is brought to the foreground; do not touch the mouse or keyboard.
Output:
    <stem>_circuit.png      circuit diagram (whitespace trimmed)
    <stem>_<instrument>.png each instrument panel, e.g. rc_XSC1.png
    <stem>_screen.png       full-screen capture with the panels overlaid on the circuit window
"""
import ctypes
import hashlib
import math
import re
import subprocess
import sys
import time
from pathlib import Path

from . import netlist as nl
from . import xmlio
from .config import CONFIG

try:
    import numpy as np
except ImportError:
    np = None

try:
    from PIL import Image, ImageGrab
except ImportError:
    Image = None
    ImageGrab = None

try:
    from scipy import ndimage
except ImportError:
    ndimage = None

if sys.platform == "win32":
    try:
        import pywintypes
        import win32api
        import win32con
        import win32gui
        import win32process
        import win32ui
    except ImportError:
        pywintypes = win32api = win32con = win32gui = win32process = win32ui = None
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    user32 = ctypes.windll.user32
else:
    pywintypes = win32api = win32con = win32gui = win32process = win32ui = None
    user32 = None

_WIN_ERR = (pywintypes.error, OSError) if pywintypes is not None else (OSError,)

PANEL_HINTS = ("XSC", "XBP", "XFG", "XMM", "\u793a\u6ce2\u5668", "\u6ce2\u7279", "\u51fd\u6570", "\u4e07\u7528\u8868",
               "Oscilloscope", "Bode", "Function", "Multimeter")


def _require_capture_deps():
    missing = [name for name, mod in (
        ("pywin32", win32gui), ("Pillow", Image), ("numpy", np), ("scipy", ndimage)
    ) if mod is None]
    if missing:
        raise RuntimeError(f"Capture extras not installed ({', '.join(missing)}); install with pip install -e .[capture]")


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        kernel32 = getattr(ctypes, "windll", None) and ctypes.windll.kernel32
        if kernel32 is not None:
            from ctypes import wintypes
            open_proc = kernel32.OpenProcess
            open_proc.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            open_proc.restype = wintypes.HANDLE
            get_code = kernel32.GetExitCodeProcess
            get_code.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            get_code.restype = wintypes.BOOL
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle.restype = wintypes.BOOL
            handle = open_proc(0x1000, False, int(pid))
            if not handle:
                return False
            try:
                code = wintypes.DWORD()
                if get_code(handle, ctypes.byref(code)):
                    return code.value == 259  # STILL_ACTIVE
            finally:
                close_handle(handle)
    return any(p == pid for _, _, _, p in top_windows())


class NetlistError(RuntimeError):
    """Multisim refused to build the circuit it was about to simulate."""

    def __init__(self, dialog: dict):
        self.dialog = dialog
        super().__init__("Multisim reported a netlist error: " + (dialog.get("text") or dialog.get("title") or "unknown"))


def top_windows(pid=None):
    found = []

    def cb(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            _, owner = win32process.GetWindowThreadProcessId(hwnd)
            if pid is None or owner == pid:
                found.append((hwnd, win32gui.GetWindowText(hwnd), win32gui.GetClassName(hwnd), owner))
        except _WIN_ERR:
            pass
        return True

    try:
        win32gui.EnumWindows(cb, None)
    except _WIN_ERR:
        pass
    return found


def design_windows(stem: str):
    """Every visible Multisim main window whose title carries [stem]."""
    return [(hwnd, pid) for hwnd, title, cls, pid in top_windows()
            if cls.startswith("Multisim") and f"[{stem}]" in title]


def capture_copy(source: Path, work: Path) -> Path:
    """Copy the design into *work*, keeping its name.

    The window title carries only the file stem, and the 14.3 automation interface
    exposes no open-path property (IMultisimApp has no Circuits collection), so a
    title can never prove which file it shows. Instead of trying, capture refuses to
    reuse a window that was already open: it always starts its own, so any window
    with this stem must be the copy it just launched.
    """
    copy = work / source.name
    copy.write_bytes(source.read_bytes())
    return copy


def grab(hwnd: int):
    _require_capture_deps()
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    w, h = right - left, bottom - top
    if w <= 0 or h <= 0:
        raise RuntimeError(f"Window {hwnd} has zero area ({w}x{h}); cannot capture")
    hdc = win32gui.GetWindowDC(hwnd)
    src = mem = bmp = None
    try:
        src = win32ui.CreateDCFromHandle(hdc)
        mem = src.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(src, w, h)
        mem.SelectObject(bmp)
        if not user32.PrintWindow(hwnd, mem.GetSafeHdc(), 2):     # PW_RENDERFULLCONTENT: renders fully even when occluded
            raise RuntimeError(f"PrintWindow returned failure for window {hwnd}; refusing to save a blank screenshot")
        info = bmp.GetInfo()
        img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
        return img.copy()
    finally:
        if bmp is not None:
            win32gui.DeleteObject(bmp.GetHandle())
        if mem is not None:
            mem.DeleteDC()
        if src is not None:
            src.DeleteDC()
        win32gui.ReleaseDC(hwnd, hdc)


def bring_to_front(hwnd: int) -> bool:
    """Bypass the foreground lock: attach to the current foreground thread before switching."""
    fg = win32gui.GetForegroundWindow()
    me = win32api.GetCurrentThreadId()
    other = win32process.GetWindowThreadProcessId(fg)[0] if fg else me
    try:
        user32.AttachThreadInput(me, other, True)
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        win32gui.SetForegroundWindow(hwnd)
        win32gui.BringWindowToTop(hwnd)
    except Exception:
        pass
    finally:
        user32.AttachThreadInput(me, other, False)
    time.sleep(0.4)
    return win32gui.GetForegroundWindow() == hwnd


def send_key(hwnd: int, vk: int):
    """Post key messages directly without relying on keyboard focus.

    Multisim 14.3 handles shortcuts in the drawing view (***EWbCDSTabWnd***); posting to the main frame
    is ignored. This drills down one level automatically and still posts to the main window in versions without that child window.
    """
    target = hwnd
    if win32gui.GetClassName(hwnd).startswith("Multisim"):
        found = []

        def find_view(h, _):
            try:
                if win32gui.GetWindowText(h) == "***EWbCDSTabWnd***":
                    found.append(h)
            except _WIN_ERR:
                pass
            return True

        try:
            win32gui.EnumChildWindows(hwnd, find_view, None)
        except _WIN_ERR:
            pass
        if found:
            target = found[0]
    win32api.PostMessage(target, win32con.WM_KEYDOWN, vk, 0)
    time.sleep(0.05)
    win32api.PostMessage(target, win32con.WM_KEYUP, vk, 0xC0000000)


def dismiss_netlist_warning(pid: int) -> dict:
    """Detect the "Netlist error, continue anyway?" dialog.

    Returns {"found": bool, "clicked": bool, "title": str, "text": str}. Clicking
    "Yes" only dismisses the dialog: a netlist error means Multisim could not
    build the circuit it is about to simulate, so the caller must treat it as a
    failed run unless the user explicitly allowed continuing.
    """
    for hwnd, title, _cls, _ in top_windows(pid):
        low_title = title.strip().lower()
        if title != "\u7f51\u8868\u9519\u8bef" and "netlist error" not in low_title:
            continue
        text = []

        def collect_text(h, _):
            try:
                t = win32gui.GetWindowText(h)
                if t:
                    text.append(t)
            except _WIN_ERR:
                pass
            return True

        try:
            win32gui.EnumChildWindows(hwnd, collect_text, None)
        except _WIN_ERR:
            pass
        buttons = []

        def collect_yes(h, _):
            try:
                lbl = win32gui.GetWindowText(h).lstrip("&").strip()
                if lbl.startswith("\u662f") or lbl.lower() == "yes":
                    buttons.append(h)
            except _WIN_ERR:
                pass
            return True

        try:
            win32gui.EnumChildWindows(hwnd, collect_yes, None)
        except _WIN_ERR:
            pass
        clicked = False
        for button in buttons:
            win32api.PostMessage(button, win32con.BM_CLICK, 0, 0)
            clicked = True
            break
        return {"found": True, "clicked": clicked, "title": title, "text": " | ".join(text)[:2000]}
    return {"found": False, "clicked": False, "title": "", "text": ""}


def panels(pid: int, main: int):
    return [(h, t) for h, t, c, _ in top_windows(pid)
            if h != main and c == "#32770" and any(k in t for k in PANEL_HINTS)]


def _longest_run(mask, gap=6):
    """Longest continuous True run [start, stop), allowing short gaps of at most gap (grid lines, axes)."""
    mask = list(mask)
    for i in range(len(mask)):
        if not mask[i] and any(mask[max(0, i - gap):i]) and any(mask[i + 1:i + 1 + gap]):
            mask[i] = True
    best, start = (0, 0), None
    for i, v in enumerate(mask + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start > best[1] - best[0]:
                best = (start, i)
            start = None
    return best


def scope_coverage(img) -> float:
    """Fraction of columns (0-1) in the oscilloscope's black display area that contain colored waveforms."""
    a = np.asarray(img).astype(int)
    dark = a.sum(axis=2) < 60
    r0, r1 = _longest_run(dark.mean(axis=1) > 0.6)
    if r1 - r0 < 10:
        return 0.0
    c0, c1 = _longest_run(dark[r0:r1].mean(axis=0) > 0.6)
    if c1 - c0 < 10:
        return 0.0
    region = a[r0:r1, c0:c1]
    colored = ((region.max(axis=2) - region.min(axis=2)) > 80) & (region.max(axis=2) > 120)
    hit = colored.any(axis=0)
    lo, hi = int(len(hit) * 0.03), int(len(hit) * 0.97)
    return float(hit[lo:hi].mean())


def _children_by_id(hwnd: int) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}

    def cb(h, _):
        try:
            cid = win32gui.GetDlgCtrlID(h)
            out.setdefault(cid, []).append(h)
        except _WIN_ERR:
            pass
        return True

    try:
        win32gui.EnumChildWindows(hwnd, cb, None)
    except _WIN_ERR:
        pass
    return out


def parse_panel_value(text: str) -> float | None:
    """Parse a live Win32 panel cursor readout value such as '3.512 ms', '-1.367 dB', or '967.867 Hz'."""
    s = (text or "").strip()
    if not s or s.startswith("---"):
        return None
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"(?i)(?:db|deg|°)$", "", s)
    try:
        return nl.parse_value(s)
    except ValueError:
        return None


def parse_scope_readout(text: str) -> dict:
    """Parse Multisim oscilloscope Static cid=10015 readout table (header, T1 row, T2 row, T2-T1 row)."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    out = {"t1": None, "t2": None, "dt": None, "a1": None, "b1": None, "a2": None, "b2": None, "raw": text or ""}
    data_rows = []
    for ln in lines[1:]:
        cells = [c.strip() for c in re.split(r"\t+|\s{2,}", ln.strip()) if c.strip()]
        if cells:
            data_rows.append(cells)
    for idx, key_prefix in enumerate(("t1", "t2", "dt")):
        if idx >= len(data_rows):
            break
        row = data_rows[idx]
        val = parse_panel_value(row[0])
        out[key_prefix] = val
        if idx < 2:
            suf = str(idx + 1)
            if len(row) > 1:
                out[f"a{suf}"] = parse_panel_value(row[1])
            if len(row) > 2:
                out[f"b{suf}"] = parse_panel_value(row[2])
    return out


def parse_bode_readout(freq_text: str, y_text: str) -> dict:
    """Parse Multisim Bode plotter cursor frequency (cid=10024) and gain/phase (cid=10025)."""
    return {
        "freq": parse_panel_value(freq_text),
        "y": parse_panel_value(y_text),
        "raw_freq": (freq_text or "").strip(),
        "raw_y": (y_text or "").strip(),
    }


def _cursor_target(val) -> float | None:
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        v = float(val)
        if not math.isfinite(v):
            raise ValueError(f"cursor target must be finite, got {val!r}")
        return v
    return nl.parse_value(str(val).strip())


def _find_scope_handles(img, ox: int, oy: int, w: int) -> tuple[int | None, int | None]:
    """Return canvas x-coordinates (cyan T1 handle, yellow T2 handle) from top triangle strip."""
    arr = np.asarray(img.crop((ox, oy + 2, ox + w, oy + 15)))
    cya_x = np.where((arr[:, :, 0] < 110) & (arr[:, :, 1] > 140) & (arr[:, :, 2] > 150))[1]
    yel_x = np.where((arr[:, :, 0] > 170) & (arr[:, :, 1] > 140) & (arr[:, :, 2] < 90))[1]
    x1 = int(np.median(cya_x)) if len(cya_x) else None
    x2 = int(np.median(yel_x)) if len(yel_x) else None
    return x1, x2


def _find_bode_handle(img, ox: int, oy: int, w: int) -> int | None:
    arr = np.asarray(img.crop((ox, oy + 2, ox + w, oy + 15)))
    cya_x = np.where((arr[:, :, 0] < 120) & (arr[:, :, 2] > 140))[1]
    return int(np.median(cya_x)) if len(cya_x) else None


def _drag_canvas(canvas: int, sx: int, ex: int, y: int = 6) -> None:
    win32api.SendMessage(canvas, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, win32api.MAKELONG(int(sx), y))
    win32api.SendMessage(canvas, win32con.WM_MOUSEMOVE, win32con.MK_LBUTTON, win32api.MAKELONG(int(ex), y))
    win32api.SendMessage(canvas, win32con.WM_LBUTTONUP, 0, win32api.MAKELONG(int(ex), y))
    time.sleep(0.12)


def _canvas_info(ph: int, canvas: int):
    pl, pt, _, _ = win32gui.GetWindowRect(ph)
    cl, ct, _, _ = win32gui.GetWindowRect(canvas)
    _, _, w, h = win32gui.GetClientRect(canvas)
    return cl - pl, ct - pt, w, h


def _move_xsc_cursor(ph: int, canvas: int, readout_hwnd: int, btn_l: int, btn_r: int,
                     key: str, target: float, color: str, ox: int, oy: int, w: int,
                     x_lo: int, x_hi: int, t_lo: float, t_hi: float) -> tuple[dict, list[str]]:
    warns = []
    span_t = t_hi - t_lo
    ro = parse_scope_readout(win32gui.GetWindowText(readout_hwnd))
    for _ in range(3):
        h1, h2 = _find_scope_handles(grab(ph), ox, oy, w)
        cur_hx = h1 if color == "cyan" else h2
        cur_t = ro.get(key)
        if cur_hx is None:
            cur_hx = x_lo if cur_t is None else max(x_lo, min(x_hi, round(x_lo + (cur_t - t_lo) / span_t * (x_hi - x_lo))))
        if cur_t is not None and abs(target - cur_t) <= abs(span_t) * 0.004:
            break
        frac = (target - t_lo) / span_t if span_t else 0.5
        est_x = max(x_lo, min(x_hi, round(x_lo + frac * (x_hi - x_lo))))
        _drag_canvas(canvas, cur_hx, est_x)
        ro_new = parse_scope_readout(win32gui.GetWindowText(readout_hwnd))
        if ro_new.get(key) is None and cur_t is not None:
            warns.append(f"{key} target {target:g}s exceeded active waveform range on screen")
            break
        ro = ro_new
        if ro.get(key) is not None and abs(cur_hx - est_x) >= 4 and cur_t is not None and abs(ro[key] - cur_t) > 0:
            # Refine linear map locally using measured slope
            slope = (ro[key] - cur_t) / (est_x - cur_hx)
            if slope != 0:
                next_x = max(x_lo, min(x_hi, round(est_x + (target - ro[key]) / slope)))
                if abs(next_x - est_x) <= 2:
                    break
                _drag_canvas(canvas, est_x, next_x)
                ro = parse_scope_readout(win32gui.GetWindowText(readout_hwnd))
                break
    # Fine button nudges
    for _ in range(20):
        cur_t = ro.get(key)
        if cur_t is None or abs(target - cur_t) <= abs(span_t) * 0.003:
            break
        btn = btn_r if target > cur_t else btn_l
        win32api.SendMessage(btn, win32con.BM_CLICK, 0, 0)
        time.sleep(0.04)
        nxt = parse_scope_readout(win32gui.GetWindowText(readout_hwnd))
        if nxt.get(key) is None or abs(nxt[key] - target) >= abs(cur_t - target):
            if nxt.get(key) is None:
                win32api.SendMessage(btn_l if btn == btn_r else btn_r, win32con.BM_CLICK, 0, 0)
                time.sleep(0.04)
                ro = parse_scope_readout(win32gui.GetWindowText(readout_hwnd))
            else:
                ro = nxt
            break
        ro = nxt
    return ro, warns


def position_scope_cursors(ph: int, cur1: float | None, cur2: float | None) -> dict:
    """Move T1/T2 oscilloscope cursors via background Win32 messages and verify static readouts."""
    by_id = _children_by_id(ph)
    canvas = by_id[1050][0]
    readout_hwnd = by_id[10015][0]
    b1_l, b1_r = by_id[10011][0], by_id[10012][0]
    b2_l, b2_r = by_id[10013][0], by_id[10014][0]
    ox, oy, w, _ = _canvas_info(ph, canvas)
    x_lo, x_hi = 7, max(20, w - 8)
    ro0 = parse_scope_readout(win32gui.GetWindowText(readout_hwnd))
    t_lo = ro0.get("t1") if ro0.get("t1") is not None else 0.0
    # Ensure cursors are separated and calibrate [x_lo..x_hi] -> [t_lo..t_hi] on canvas
    h1, h2 = _find_scope_handles(grab(ph), ox, oy, w)
    probe_sx = h2 if h2 is not None else (h1 if h1 is not None else x_lo)
    probe_ex = int(w * 0.75)
    _drag_canvas(canvas, probe_sx, probe_ex)
    ro_probe = parse_scope_readout(win32gui.GetWindowText(readout_hwnd))
    # Determine which cursor moved during probe drag (top stacked handle could be t1 or t2)
    if ro_probe.get("t2") is not None and ro0.get("t2") != ro_probe.get("t2"):
        moved_t = ro_probe["t2"]
    elif ro_probe.get("t1") is not None and ro0.get("t1") != ro_probe.get("t1"):
        moved_t = ro_probe["t1"]
    else:
        moved_t = None
    if moved_t is not None and probe_ex > probe_sx:
        dt_per_px = (moved_t - t_lo) / (probe_ex - probe_sx)
        t_lo_cal = t_lo - (probe_sx - x_lo) * dt_per_px
        t_hi_cal = t_lo_cal + (x_hi - x_lo) * dt_per_px
    else:
        t_lo_cal, t_hi_cal = t_lo, t_lo + 1e-3

    warns = []
    if cur1 is not None:
        _, w1 = _move_xsc_cursor(ph, canvas, readout_hwnd, b1_l, b1_r, "t1", cur1, "cyan",
                                 ox, oy, w, x_lo, x_hi, t_lo_cal, t_hi_cal)
        warns.extend(w1)
    if cur2 is not None:
        _, w2 = _move_xsc_cursor(ph, canvas, readout_hwnd, b2_l, b2_r, "t2", cur2, "yellow",
                                 ox, oy, w, x_lo, x_hi, t_lo_cal, t_hi_cal)
        warns.extend(w2)
    final_ro = parse_scope_readout(win32gui.GetWindowText(readout_hwnd))
    return {"method": "win32_background_messages", "verified_by_ui_readout": True,
            "requested": {"t1": cur1, "t2": cur2}, **final_ro, "warnings": warns}


def position_bode_cursor(ph: int, target_freq: float) -> dict:
    """Move Bode plotter cursor via background Win32 messages and verify live frequency/gain readouts."""
    by_id = _children_by_id(ph)
    canvas = by_id[1050][0]
    f_hwnd, y_hwnd = by_id[10024][0], by_id[10025][0]
    b_l, b_r = by_id[10022][0], by_id[10023][0]
    ox, oy, w, _ = _canvas_info(ph, canvas)
    x_lo, x_hi = 7, max(20, w - 8)
    ro0 = parse_bode_readout(win32gui.GetWindowText(f_hwnd), win32gui.GetWindowText(y_hwnd))
    f_a = ro0.get("freq")
    hx_a = _find_bode_handle(grab(ph), ox, oy, w) or x_lo
    if not f_a or f_a <= 0:
        x_wake = max(x_lo, min(x_hi, int(w * 0.20)))
        _drag_canvas(canvas, hx_a, x_wake)
        hx_a = _find_bode_handle(grab(ph), ox, oy, w) or x_wake
        f_a = parse_bode_readout(win32gui.GetWindowText(f_hwnd), win32gui.GetWindowText(y_hwnd)).get("freq")
    probe_x = max(x_lo, min(x_hi, int(w * 0.80)))
    if probe_x == hx_a:
        probe_x = max(x_lo, int(w * 0.25))
    _drag_canvas(canvas, hx_a, probe_x)
    hx_b = _find_bode_handle(grab(ph), ox, oy, w) or probe_x
    ro = parse_bode_readout(win32gui.GetWindowText(f_hwnd), win32gui.GetWindowText(y_hwnd))
    f_b = ro.get("freq")
    warns = []
    if f_a and f_b and f_a > 0 and f_b > 0 and f_a != f_b and target_freq > 0 and hx_b != hx_a:
        for _ in range(3):
            if abs(f_b - target_freq) / max(abs(target_freq), 1e-12) <= 0.01:
                break
            est_x = round(hx_a + (hx_b - hx_a) * math.log(target_freq / f_a) / math.log(f_b / f_a))
            est_x = max(x_lo, min(x_hi, est_x))
            if abs(est_x - hx_b) < 2:
                break
            _drag_canvas(canvas, hx_b, est_x)
            hx_new = _find_bode_handle(grab(ph), ox, oy, w) or est_x
            ro = parse_bode_readout(win32gui.GetWindowText(f_hwnd), win32gui.GetWindowText(y_hwnd))
            f_new = ro.get("freq")
            if not f_new or f_new <= 0 or f_new == f_b:
                break
            hx_a, f_a = hx_b, f_b
            hx_b, f_b = hx_new, f_new
    for _ in range(25):
        f_cur = ro.get("freq")
        if not f_cur or abs(f_cur - target_freq) / max(abs(target_freq), 1e-12) <= 0.01:
            break
        btn = b_r if target_freq > f_cur else b_l
        win32api.SendMessage(btn, win32con.BM_CLICK, 0, 0)
        time.sleep(0.04)
        nxt = parse_bode_readout(win32gui.GetWindowText(f_hwnd), win32gui.GetWindowText(y_hwnd))
        if not nxt.get("freq") or abs(nxt["freq"] - target_freq) >= abs(f_cur - target_freq):
            ro = nxt if nxt.get("freq") else ro
            break
        ro = nxt
    if ro.get("freq") and abs(ro["freq"] - target_freq) / max(abs(target_freq), 1e-12) > 0.15:
        warns.append(f"Bode cursor frequency {ro['freq']:g}Hz differs from requested {target_freq:g}Hz (check Bode range)")
    return {"method": "win32_background_messages", "verified_by_ui_readout": True,
            "requested": {"freq": target_freq}, **ro, "warnings": warns}


def _view_rect(main: int):
    found = []

    def cb(hwnd, _):
        try:
            if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == "***EWbCDSTabWnd***":
                found.append(win32gui.GetWindowRect(hwnd))
        except _WIN_ERR:
            pass
        return True

    try:
        win32gui.EnumChildWindows(main, cb, None)
    except _WIN_ERR:
        pass
    return found[0] if found else None


def drawing_area(main: int, img):
    """Crop to the drawing view (excluding menus, toolbars, the design toolbox, and the bottom spreadsheet view)."""
    left, top, *_ = win32gui.GetWindowRect(main)
    r = _view_rect(main)
    return img.crop((r[0] - left, r[1] - top, r[2] - left, r[3] - top)) if r else img


def ink_mask(img):
    """Ink mask within the white page area (black line art/text or highly saturated color); returns (mask, page top-left offset)."""
    a = np.asarray(img).astype(int)
    lo, hi = a.min(axis=2), a.max(axis=2)
    white = lo > 235
    c0, c1 = _longest_run(white.mean(axis=0) > 0.6, gap=3)
    r0, r1 = _longest_run(white[:, c0:c1].mean(axis=1) > 0.6, gap=3)
    if r1 - r0 < 10 or c1 - c0 < 10:
        return np.zeros(lo.shape, bool), (0, 0)
    ink = (hi[r0:r1, c0:c1] < 60) | ((hi - lo)[r0:r1, c0:c1] > 120)
    # Remove grid dots and frame tick marks: keep only connected components with enough pixels
    labels, count = ndimage.label(ink, structure=np.ones((3, 3)))
    if count:
        sizes = ndimage.sum(ink, labels, index=np.arange(1, count + 1))
        keep = np.zeros(count + 1, bool)
        keep[1:] = sizes >= 30
        ink = keep[labels]
    return ink, (c0, r0)


def frame_inner(img):
    """Inner drawing frame (inside the zone numbering strip): gray lines near the page edges that span the whole page."""
    a = np.asarray(img).astype(int)
    lo, hi = a.min(axis=2), a.max(axis=2)
    gray = (lo > 60) & (hi < 210) & ((hi - lo) < 25)
    h, w = gray.shape
    rows = np.where(gray.mean(axis=1) > 0.5)[0]
    cols = np.where(gray.mean(axis=0) > 0.5)[0]
    return (max([c for c in cols if c < w * 0.3], default=0), max([r for r in rows if r < h * 0.3], default=0),
            min([c for c in cols if c > w * 0.7], default=w), min([r for r in rows if r > h * 0.7], default=h))


def crop_ink(img, margin=40):
    ink, (ox, oy) = ink_mask(img)
    rows = np.where(ink.sum(axis=1) > 2)[0]
    cols = np.where(ink.sum(axis=0) > 2)[0]
    if len(rows) == 0 or len(cols) == 0:
        return img
    x0, y0, x1, y1 = ox + cols.min(), oy + rows.min(), ox + cols.max(), oy + rows.max()
    fl, ft, fr, fb = frame_inner(img)
    # Padding must not cross the inner frame; when components overlap the frame, crop by ink so components are not cut off
    left = max(x0 - margin, fl + 2) if x0 > fl else max(x0 - margin, 0)
    top = max(y0 - margin, ft + 2) if y0 > ft else max(y0 - margin, 0)
    right = min(x1 + margin, fr - 1) if x1 < fr else min(x1 + margin, img.width)
    bottom = min(y1 + margin, fb - 1) if y1 < fb else min(y1 + margin, img.height)
    return img.crop((left, top, right, bottom))


def arrange_panels(main: int, pid: int, view):
    """Move panels to the corner of the drawing view with the least circuit ink so they block as little as possible."""
    rect = _view_rect(main)
    if rect is None:
        return
    vx0, vy0, vx1, vy1 = rect
    ink, (ox, oy) = ink_mask(view)
    occupied = np.zeros((max(0, vy1 - vy0), max(0, vx1 - vx0)), bool)
    h_copy = min(ink.shape[0], max(0, occupied.shape[0] - max(oy, 0)))
    w_copy = min(ink.shape[1], max(0, occupied.shape[1] - max(ox, 0)))
    if h_copy > 0 and w_copy > 0:
        occupied[max(oy, 0):max(oy, 0) + h_copy, max(ox, 0):max(ox, 0) + w_copy] = ink[:h_copy, :w_copy]
    for hwnd, _ in panels(pid, main):
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        w, h, pad = r - l, b - t, 20
        corners = [(vx1 - w - pad, vy0 + pad), (vx1 - w - pad, vy1 - h - pad), (vx0 + pad, vy1 - h - pad), (vx0 + pad, vy0 + pad)]

        def cost(pos):
            x, y = pos[0] - vx0, pos[1] - vy0
            return occupied[max(y, 0):max(y + h, 0), max(x, 0):max(x + w, 0)].sum()
        x, y = min(corners, key=cost)
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOP, x, y, 0, 0, win32con.SWP_NOSIZE)
        occupied[max(y - vy0, 0):max(y - vy0 + h, 0), max(x - vx0, 0):max(x - vx0 + w, 0)] = True


def open_design(ms14: Path, desktop_screen: bool = False):
    """Return (main window, process ID, whether it was launched by this call)."""
    _require_capture_deps()
    existing = design_windows(ms14.stem)
    if existing:
        raise RuntimeError(
            f"{len(existing)} Multisim window(s) are already open for a design named {ms14.stem!r} "
            f"(pids {sorted({p for _, p in existing})}). Capture cannot tell which file such a window "
            f"shows, so close it first; capture opens its own copy and closes it afterwards")
    si = None
    if not desktop_screen and win32con is not None and sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = win32con.SW_SHOWNOACTIVATE
    proc = subprocess.Popen([CONFIG["multisim_exe"], str(ms14)], startupinfo=si)
    try:
        return _wait_for_open_design(ms14, proc, desktop_screen=desktop_screen)
    except BaseException:
        # Only terminate the process we launched, never an existing user instance.
        try:
            if proc.poll() is None:
                proc.terminate()
        except OSError:
            pass
        raise


def _wait_for_open_design(ms14: Path, proc, desktop_screen: bool = False):
    """Resolve the new window or fail; open_design owns cleanup before handoff."""
    main, pid = None, None
    deadline = time.time() + 120
    while main is None and time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"Multisim exited before opening {ms14.name} (exit={proc.returncode})")
        time.sleep(1)
        found = design_windows(ms14.stem)
        if len(found) > 1:
            raise RuntimeError(
                f"{len(found)} Multisim windows appeared for {ms14.stem!r}; close the extra ones and retry")
        if found:
            main, pid = found[0]
    if main is None:
        raise RuntimeError("Timed out waiting for the Multisim window")
    time.sleep(3)
    launched = True
    if desktop_screen:
        win32gui.ShowWindow(main, win32con.SW_MAXIMIZE)
        time.sleep(1)
    send_key(main, win32con.VK_F7)                    # Zoom to fit the entire drawing
    time.sleep(1)
    return main, pid, launched


def run_once(ms14: Path, out: Path, wait: float, timeout: float, on_netlist_error: str = "abort",
             stem: str | None = None, desktop_screen: bool = False,
             cur1: float | None = None, cur2: float | None = None):
    _require_capture_deps()
    stem = stem or ms14.stem                      # the original name drives the file names we write
    main, pid, launched = open_design(ms14, desktop_screen=True) if desktop_screen else open_design(ms14)
    try:
        result = {"ms14": str(ms14), "images": {}, "warnings": [], "netlist_errors": []}
        send_key(main, win32con.VK_F5)
        deadline = time.time() + 15
        while not panels(pid, main) and time.time() < deadline:
            dialog = dismiss_netlist_warning(pid)
            if dialog["found"]:
                result["netlist_errors"].append(dialog)
                if on_netlist_error == "abort":
                    raise NetlistError(dialog)
            time.sleep(0.5)
        if not panels(pid, main):
            result["warnings"].append("No instrument panels appeared after running: check the instruments' show settings")
        # The simulation stops automatically at sim_time: wait until the panel images stop changing before capturing
        previous, stable_since = None, None
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.7)
            dialog = dismiss_netlist_warning(pid)
            if dialog["found"]:
                result["netlist_errors"].append(dialog)
                if on_netlist_error == "abort":
                    raise NetlistError(dialog)
            shots = [np.asarray(grab(h)) for h, _ in panels(pid, main)]
            same = previous is not None and len(shots) == len(previous) and all(
                a.shape == b.shape and (a == b).all() for a, b in zip(shots, previous))
            previous = shots
            if not same:
                stable_since = None
            elif stable_since is None:
                stable_since = time.time()
            elif time.time() - stable_since >= wait:
                break
        else:
            send_key(main, win32con.VK_F6)
            time.sleep(1.0)
            result["warnings"].append("The simulation did not stop before the timeout; paused manually: check sim_time")
        if cur1 is not None or cur2 is not None:
            gui_cursors = {}
            for hwnd, title in panels(pid, main):
                name = title.split("-")[-1].strip() or "panel"
                if "XSC" in title or "\u793a\u6ce2\u5668" in title:
                    cur_res = position_scope_cursors(hwnd, cur1, cur2)
                    gui_cursors[name] = cur_res
                    result["warnings"].extend(cur_res.get("warnings") or [])
                elif ("XBP" in title or "\u6ce2\u7279" in title) and cur1 is not None:
                    cur_res = position_bode_cursor(hwnd, cur1)
                    gui_cursors[name] = cur_res
                    result["warnings"].extend(cur_res.get("warnings") or [])
            if gui_cursors:
                result["gui_cursors"] = gui_cursors
                time.sleep(0.25)
        worst = 1.0
        for hwnd, title in panels(pid, main):
            name = title.split("-")[-1].strip() or "panel"
            img = grab(hwnd)
            path = out / f"{stem}_{name}.png"
            img.save(path)
            result["images"][name] = str(path)
            if "XSC" in title or "\u793a\u6ce2\u5668" in title:
                coverage = scope_coverage(img)
                result.setdefault("scope_coverage", {})[name] = round(coverage, 2)
                worst = min(worst, coverage)
        view = drawing_area(main, grab(main))
        crop_ink(view).save(out / f"{stem}_circuit.png")
        result["images"]["circuit"] = str(out / f"{stem}_circuit.png")
        # Assemble actual window bitmaps without stealing desktop focus.
        # Explicitly label this as a composite, not a native desktop screenshot.
        from .imaging import evidence_montage
        panel_images = [(title.split("-")[-1].strip() or "panel", grab(hwnd))
                        for hwnd, title in panels(pid, main)]
        montage = evidence_montage(grab(main), panel_images)
        montage_path = out / f"{stem}_montage.png"
        montage.save(montage_path)
        result["images"]["montage"] = str(montage_path)
        result["montage_method"] = "PrintWindow contact sheet; not a desktop screenshot"
        if desktop_screen:
            if bring_to_front(main):
                arrange_panels(main, pid, view)
                time.sleep(0.8)
                l, t, r, b = win32gui.GetWindowRect(main)
                ImageGrab.grab(bbox=(max(l, 0), max(t, 0), r, b), all_screens=True).save(out / f"{stem}_screen.png")
                result["images"]["screen"] = str(out / f"{stem}_screen.png")
            else:
                result["warnings"].append("Could not take the opt-in desktop screenshot; montage and raw panel images are available")
        return result, pid, launched, worst
    except BaseException:
        # The caller cannot receive pid/launched when run_once raises.
        if launched:
            try:
                _close(pid, main)
            except Exception:
                pass
        raise


def sweep_info(ms14: Path):
    """Read the interactive simulation end time and the oscilloscope's per-screen sweep duration from the file."""
    root = xmlio.load(ms14)
    state = next(root.iter("CIITDiagram")).get("SimState") or ""
    _, _, tail = state.partition("DEFAULT_INST_SETTINGS")
    m = re.search(r"TSTOP:double\{([^}]*)\}", tail)
    sim_time = float(m.group(1)) if m else None
    sweep = None
    for node in root.iter("CSourceSymbolCollectNode"):
        if (node.get("CompLongName") or "").upper().startswith("&ASCXSC"):
            for el in node.iter("Element"):
                if el.get("Key") == "&ASCNI_EWB_TIMEBASE_SCALE_VALUE":
                    sweep = 10 * float(el.find("CDataElement").get("Data"))
            break
    return sim_time, sweep



def _close(pid: int, main=None) -> None:
    """Close Multisim politely first; force-kill only what refuses to go away."""
    try:
        if main:
            win32api.PostMessage(main, win32con.WM_CLOSE, 0, 0)
    except Exception:
        pass
    deadline = time.time() + 6
    while time.time() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.5)
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, stdin=subprocess.DEVNULL)


def capture(ms14, outdir, wait=1.5, timeout=90, keep_open=False, on_netlist_error="abort",
            desktop_screen=False, cur1=None, cur2=None) -> dict:
    """Capture a real Multisim run.

    The input design is never modified: every run works on a copy inside
    work/capture/<run_id>/. A netlist error makes the run fail unless
    on_netlist_error="continue"; the dialog is always recorded in the result.
    Optional cur1/cur2 position live GUI panel cursors via background Win32
    messages and record verified UI readouts.
    """
    _require_capture_deps()
    from .builder import set_sim_time
    from . import com
    from .result import new_run_id

    c1 = _cursor_target(cur1)
    c2 = _cursor_target(cur2)
    com.stop()                       # release the hidden COM instance first: only one Multisim may run
    source, out = Path(ms14).resolve(), Path(outdir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    run_id = new_run_id()
    work = Path(CONFIG["work_dir"]) / "capture" / run_id
    work.mkdir(parents=True, exist_ok=True)
    copy = capture_copy(source, work)          # unique stem: the window title is then unambiguous
    input_sha = hashlib.sha256(source.read_bytes()).hexdigest()

    sim_time, sweep = sweep_info(copy)
    pid, launched = None, False
    try:
        result, pid, launched, worst = run_once(copy, out, wait, timeout, on_netlist_error, stem=source.stem,
                                                desktop_screen=desktop_screen, cur1=c1, cur2=c2)
        if worst < 0.9 and launched and sim_time and sweep and sim_time < 1e20:
            # The sweep start is fixed for a given circuit: extend the end time until this screen is almost fully swept, then run again
            _close(pid)
            pid = None
            time.sleep(2)
            new_time = sim_time + (0.99 - worst) * sweep
            set_sim_time(copy, new_time)          # only the working copy is retimed
            first = result
            result, pid, launched, worst = run_once(copy, out, wait, timeout, on_netlist_error, stem=source.stem,
                                                    desktop_screen=desktop_screen, cur1=c1, cur2=c2)
            result["warnings"] = first["warnings"] + result["warnings"]
            result["netlist_errors"] = first["netlist_errors"] + result["netlist_errors"]
            result["sim_time_adjusted"] = new_time
        if worst < 0.9:
            result["warnings"].append(f"Oscilloscope waveform covers only {worst:.0%} of the screen; increase sim_time, then regenerate and re-capture")
    finally:
        if launched and not keep_open and pid:
            _close(pid)                   # a failed run must not leave a Multisim window behind

    after_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    result.update({
        "run_id": run_id,
        "source": str(source),
        "input_sha256": input_sha,
        "input_unchanged": input_sha == after_sha,
        "working_copy": str(copy),
        "on_netlist_error": on_netlist_error,
        "desktop_screen_requested": bool(desktop_screen),
    })
    if input_sha != after_sha:
        raise RuntimeError(f"the input design changed during capture ({source}); this is a bug, the run is not trustworthy")
    return result