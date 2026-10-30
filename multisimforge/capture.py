"""Open a .ms14 → run an interactive simulation → capture instrument panels and the circuit window (real Multisim screenshots).

Run with the system Python (requires pywin32, Pillow, numpy, scipy). Whether instrument panels appear, their ranges,
and the simulation end time are all written into the .ms14 by the builder. During capture the Multisim window is brought to the foreground; do not touch the mouse or keyboard.
Output:
    <stem>_circuit.png      circuit diagram (whitespace trimmed)
    <stem>_<instrument>.png each instrument panel, e.g. rc_XSC1.png
    <stem>_screen.png       full-screen capture with the panels overlaid on the circuit window
"""
import ctypes
import re
import hashlib
import subprocess
import time
from pathlib import Path

import numpy as np
import win32api
import win32con
import win32gui
import win32process
import win32ui
from PIL import Image, ImageGrab
from scipy import ndimage

from . import xmlio
from .config import CONFIG

ctypes.windll.shcore.SetProcessDpiAwareness(2)
user32 = ctypes.windll.user32
PANEL_HINTS = ("XSC", "XBP", "XFG", "XMM", "\u793a\u6ce2\u5668", "\u6ce2\u7279", "\u51fd\u6570", "\u4e07\u7528\u8868",
               "Oscilloscope", "Bode", "Function", "Multimeter")


class NetlistError(RuntimeError):
    """Multisim refused to build the circuit it was about to simulate."""

    def __init__(self, dialog: dict):
        self.dialog = dialog
        super().__init__("Multisim reported a netlist error: " + (dialog.get("text") or dialog.get("title") or "unknown"))


def top_windows(pid=None):
    found = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        _, owner = win32process.GetWindowThreadProcessId(hwnd)
        if pid is None or owner == pid:
            found.append((hwnd, win32gui.GetWindowText(hwnd), win32gui.GetClassName(hwnd), owner))
    win32gui.EnumWindows(cb, None)
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


def grab(hwnd: int) -> Image.Image:
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    w, h = right - left, bottom - top
    hdc = win32gui.GetWindowDC(hwnd)
    src = win32ui.CreateDCFromHandle(hdc)
    mem = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(src, w, h)
    mem.SelectObject(bmp)
    user32.PrintWindow(hwnd, mem.GetSafeHdc(), 2)     # PW_RENDERFULLCONTENT: renders fully even when occluded
    info = bmp.GetInfo()
    img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
    win32gui.DeleteObject(bmp.GetHandle())
    mem.DeleteDC()
    src.DeleteDC()
    win32gui.ReleaseDC(hwnd, hdc)
    return img.copy()


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
        win32gui.EnumChildWindows(
            hwnd, lambda h, _: found.append(h) if win32gui.GetWindowText(h) == "***EWbCDSTabWnd***" else None, None)
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
    for hwnd, title, cls, _ in top_windows(pid):
        if title != "\u7f51\u8868\u9519\u8bef":
            continue
        text = []
        win32gui.EnumChildWindows(
            hwnd, lambda h, _: text.append(win32gui.GetWindowText(h)) if win32gui.GetWindowText(h) else None, None)
        buttons = []
        win32gui.EnumChildWindows(
            hwnd, lambda h, _: buttons.append(h) if win32gui.GetWindowText(h).startswith("\u662f") else None, None)
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


def scope_coverage(img: Image.Image) -> float:
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


def _view_rect(main: int):
    found = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == "***EWbCDSTabWnd***":
            found.append(win32gui.GetWindowRect(hwnd))
    win32gui.EnumChildWindows(main, cb, None)
    return found[0] if found else None


def drawing_area(main: int, img: Image.Image) -> Image.Image:
    """Crop to the drawing view (excluding menus, toolbars, the design toolbox, and the bottom spreadsheet view)."""
    left, top, *_ = win32gui.GetWindowRect(main)
    r = _view_rect(main)
    return img.crop((r[0] - left, r[1] - top, r[2] - left, r[3] - top)) if r else img


def ink_mask(img: Image.Image):
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


def frame_inner(img: Image.Image):
    """Inner drawing frame (inside the zone numbering strip): gray lines near the page edges that span the whole page."""
    a = np.asarray(img).astype(int)
    lo, hi = a.min(axis=2), a.max(axis=2)
    gray = (lo > 60) & (hi < 210) & ((hi - lo) < 25)
    h, w = gray.shape
    rows = np.where(gray.mean(axis=1) > 0.5)[0]
    cols = np.where(gray.mean(axis=0) > 0.5)[0]
    return (max([c for c in cols if c < w * 0.3], default=0), max([r for r in rows if r < h * 0.3], default=0),
            min([c for c in cols if c > w * 0.7], default=w), min([r for r in rows if r > h * 0.7], default=h))


def crop_ink(img: Image.Image, margin=40) -> Image.Image:
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


def arrange_panels(main: int, pid: int, view: Image.Image):
    """Move panels to the corner of the drawing view with the least circuit ink so they block as little as possible."""
    rect = _view_rect(main)
    if rect is None:
        return
    vx0, vy0, vx1, vy1 = rect
    ink, (ox, oy) = ink_mask(view)
    occupied = np.zeros((vy1 - vy0, vx1 - vx0), bool)
    occupied[oy:oy + ink.shape[0], ox:ox + ink.shape[1]] = ink[:occupied.shape[0] - oy, :occupied.shape[1] - ox]
    for hwnd, _ in panels(pid, main):
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        w, h, pad = r - l, b - t, 20
        corners = [(vx1 - w - pad, vy0 + pad), (vx1 - w - pad, vy1 - h - pad), (vx0 + pad, vy1 - h - pad), (vx0 + pad, vy0 + pad)]

        def cost(pos):
            x, y = pos[0] - vx0, pos[1] - vy0
            return occupied[max(y, 0):y + h, max(x, 0):x + w].sum()
        x, y = min(corners, key=cost)
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOP, x, y, 0, 0, win32con.SWP_NOSIZE)
        occupied[max(y - vy0, 0):y - vy0 + h, max(x - vx0, 0):x - vx0 + w] = True


def open_design(ms14: Path):
    """Return (main window, process ID, whether it was launched by this call)."""
    existing = design_windows(ms14.stem)
    if existing:
        raise RuntimeError(
            f"{len(existing)} Multisim window(s) are already open for a design named {ms14.stem!r} "
            f"(pids {sorted({p for _, p in existing})}). Capture cannot tell which file such a window "
            f"shows, so close it first; capture opens its own copy and closes it afterwards")
    main, pid = None, None
    subprocess.Popen([CONFIG["multisim_exe"], str(ms14)])
    deadline = time.time() + 120
    while main is None and time.time() < deadline:
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
    win32gui.ShowWindow(main, win32con.SW_MAXIMIZE)
    time.sleep(1)
    send_key(main, win32con.VK_F7)                    # Zoom to fit the entire drawing
    time.sleep(1)
    return main, pid, launched


def run_once(ms14: Path, out: Path, wait: float, timeout: float, on_netlist_error: str = "abort",
             stem: str | None = None):
    stem = stem or ms14.stem                      # the original name drives the file names we write
    main, pid, launched = open_design(ms14)
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
    # Full-screen capture: grab the screen after bringing Multisim to the foreground; panels overlay the circuit window at their real positions
    if bring_to_front(main):
        arrange_panels(main, pid, view)
        time.sleep(0.8)
        l, t, r, b = win32gui.GetWindowRect(main)
        ImageGrab.grab(bbox=(max(l, 0), max(t, 0), r, b), all_screens=True).save(out / f"{stem}_screen.png")
        result["images"]["screen"] = str(out / f"{stem}_screen.png")
    else:
        result["warnings"].append("Could not bring Multisim to the foreground (do not touch the mouse or keyboard while capturing); no full-screen capture was generated")
    return result, pid, launched, worst


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
        if not any(p == pid for _, _, _, p in top_windows()):
            return
        time.sleep(0.5)
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)


def capture(ms14, outdir, wait=1.5, timeout=90, keep_open=False, on_netlist_error="abort") -> dict:
    """Capture a real Multisim run.

    The input design is never modified: every run works on a copy inside
    work/capture/<run_id>/. A netlist error makes the run fail unless
    on_netlist_error="continue"; the dialog is always recorded in the result.
    """
    from .builder import set_sim_time
    from . import com
    from .result import new_run_id

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
        result, pid, launched, worst = run_once(copy, out, wait, timeout, on_netlist_error, stem=source.stem)
        if worst < 0.9 and launched and sim_time and sweep and sim_time < 1e20:
            # The sweep start is fixed for a given circuit: extend the end time until this screen is almost fully swept, then run again
            _close(pid)
            pid = None
            time.sleep(2)
            new_time = sim_time + (0.99 - worst) * sweep
            set_sim_time(copy, new_time)          # only the working copy is retimed
            first = result
            result, pid, launched, worst = run_once(copy, out, wait, timeout, on_netlist_error, stem=source.stem)
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
    })
    if input_sha != after_sha:
        raise RuntimeError(f"the input design changed during capture ({source}); this is a bug, the run is not trustworthy")
    return result