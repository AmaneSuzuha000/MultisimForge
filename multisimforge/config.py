"""Local machine path configuration.

All defaults are detected from the local machine at runtime; no user directory is hard-coded:
- multisim_exe: reads the registration information of the Multisim automation interface (the WOW6432Node CLSID entry under HKCR)
- samples_dir / master_db: detects 14.x / 15.x under the installation directory
- python32: detects a 32-bit interpreter with pywin32 installed
- library_dir / work_dir: fixed locations under the user directory

To override, put a config.json in the repository root (with the same key names), or use environment variables MULTISIMFORGE_<UPPERCASE_KEY>.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_LOCAL = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MultisimForge"
_COM_CLSID = r"WOW6432Node\CLSID\{D9CBB7A1-6AD6-438B-AD1B-42FB2DB00CAE}\InprocServer32"   # MultisimApp
_PROGRAM_FILES = [os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                  os.environ.get("ProgramFiles", r"C:\Program Files")]


def _registry_exe() -> str | None:
    """Find multisim.exe from the registration information of the automation interface."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, _COM_CLSID) as key:
            return str(Path(winreg.QueryValue(key, None)).with_name("multisim.exe"))
    except (OSError, ImportError):
        return None


def _version_dirs(base: Path) -> list[str]:
    """All 13/14/15 version numbers under the installation directory, newest first."""
    try:
        if not base.is_dir():
            return []
        names = [p.name for p in base.iterdir() if p.is_dir() and p.name[:3] in ("13.", "14.", "15.")]
    except OSError:
        return []

    def key(name):
        try:
            return tuple(int(x) for x in name.split("."))
        except ValueError:
            return (0,)

    return sorted(names, key=key, reverse=True)


def _exists(path) -> bool:
    try:
        return Path(path).exists()
    except OSError:
        return False


def _version_of(name: str) -> str:
    """Extract a version number such as 14.3 from an installation directory name."""
    import re
    match = re.search(r"(\d+\.\d+)", name)
    return match.group(1) if match else "14.3"


def _detect_suite() -> tuple[str, str, str]:
    """Return (exe, samples_dir, master_db) for the version actually installed on this machine."""
    candidates = []
    exe = _registry_exe()
    if exe:
        candidates.append(Path(exe).parent)
    for pf in _PROGRAM_FILES:
        base = Path(pf) / "National Instruments" / "Circuit Design Suite"
        candidates += [base / v for v in _version_dirs(base)]
    suite = next((c for c in candidates if _exists(c / "multisim.exe")), None)
    if suite is None:
        suite = Path(_PROGRAM_FILES[0]) / "National Instruments" / "Circuit Design Suite" / "14.3"
    version = _version_of(suite.name)    # "Circuit Design Suite 14.3" → "14.3"
    # samples are in "...\\Circuit Design Suite 14.3\\samples"; the part database is in "...\\Circuit Design Suite\\14.3\\database"
    samples = Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Documents" / "National Instruments" \
        / f"Circuit Design Suite {version}" / "samples"
    db = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "National Instruments" \
        / "Circuit Design Suite" / version / "database" / "MSCOMP_S.PRD"
    return str(suite / "multisim.exe"), str(samples), str(db)


def python32_argv() -> list[str]:
    """Command prefix for the 32-bit COM worker.

    The value may be a real executable path (which can contain spaces) or the
    launcher invocation "py -3-32" -- that one has to become two argv elements,
    otherwise subprocess looks for a file literally named "py -3-32".
    """
    value = str(CONFIG.get("python32") or "")
    if value.startswith("py ") or value.startswith("py.exe "):
        return value.split()
    return [value]


def _detect_python32() -> str:
    """Find a 32-bit interpreter with pywin32 installed (Multisim's automation interface is only usable from a 32-bit process)."""
    override = os.environ.get("MULTISIMFORGE_PYTHON32")
    if sys.platform != "win32":
        return sys.executable
    if override:
        return override
    if sys.maxsize <= 2 ** 32:            # this process is already 32-bit
        return sys.executable
    local = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    guesses = []
    for base in (local / "Programs" / "Python", Path(r"C:\Python312-32"), Path(r"C:\Python311-32")):
        if _exists(base):
            guesses += sorted((p / "python.exe" for p in base.glob("Python3*-32")), reverse=True)
    guesses.append(_LOCAL / ".venv32" / "Scripts" / "python.exe")
    for guess in guesses:
        if _exists(guess):
            return str(guess)
    try:                                   # fallback: hand off to the py launcher
        out = subprocess.run(["py", "-3-32", "-c", "import win32com"], capture_output=True, text=True, timeout=20)
        if out.returncode == 0:
            return "py -3-32"
    except (OSError, subprocess.SubprocessError):
        pass
    return str(_LOCAL / ".venv32" / "Scripts" / "python.exe")


_EXE, _SAMPLES, _DB = _detect_suite()
_DEFAULTS = {
    "multisim_exe": _EXE,
    "samples_dir": _SAMPLES,
    "master_db": _DB,
    "node_decoder": str(Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "electronics-workbench-decoder"),
    "python32": _detect_python32(),
    "library_dir": str(_LOCAL / "parts"),
    "work_dir": r"C:\msim_work",          # the working directory of the Multisim command-line engine must not contain spaces
    "com_idle_timeout": 900,               # seconds of inactivity before the COM background process exits by itself
}


def _load() -> dict:
    merged = dict(_DEFAULTS)
    path = ROOT / "config.json"
    if path.exists():
        merged.update(json.loads(path.read_text(encoding="utf-8")))
    for key in _DEFAULTS:                  # environment variables take precedence
        env = os.environ.get("MULTISIMFORGE_" + key.upper())
        if env:
            merged[key] = env
    return merged


CONFIG = _load()

# repository root (where SKILL.md and tools/ live)
SKILL_DIR = ROOT


def library_dir() -> Path:
    return Path(CONFIG["library_dir"])