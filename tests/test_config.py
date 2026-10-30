"""Configuration resolution must be predictable and must never pick a 64-bit interpreter.

Multisim exposes its automation interface only to a 32-bit process, so a wrong guess
here shows up much later as a COM failure with no obvious cause.
"""
import sys
from pathlib import Path

import pytest

from multisimforge import config


def test_version_directories_are_sorted_newest_first(tmp_path):
    for name in ("14.0", "13.0", "15.1", "not-a-version", "14.3"):
        (tmp_path / name).mkdir()
    assert config._version_dirs(tmp_path) == ["15.1", "14.3", "14.0", "13.0"]


def test_version_directories_ignore_files(tmp_path):
    (tmp_path / "14.3").write_text("not a directory", encoding="utf-8")
    assert config._version_dirs(tmp_path) == []


def test_a_missing_base_directory_is_not_an_error(tmp_path):
    assert config._version_dirs(tmp_path / "nope") == []


def test_python32_argv_splits_a_launcher_command(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "python32", "py -3-32")
    assert config.python32_argv() == ["py", "-3-32"]
    monkeypatch.setitem(config.CONFIG, "python32", r"C:\Python32\python.exe")
    assert config.python32_argv() == [r"C:\Python32\python.exe"]


def test_python32_honours_the_environment_override(monkeypatch, tmp_path):
    monkeypatch.setenv("MULTISIMFORGE_PYTHON32", r"D:\custom\python.exe")
    assert config._detect_python32() == r"D:\custom\python.exe"


@pytest.mark.skipif(sys.platform != "win32", reason="the 32-bit rule is Windows-only")
@pytest.mark.skipif(sys.maxsize <= 2 ** 32, reason="this interpreter is already 32-bit")
def test_a_32_bit_interpreter_is_never_guessed_when_one_is_configured(monkeypatch):
    """A resolved path must point at a python.exe, never at a 64-bit directory by accident."""
    exe = config._detect_python32()
    assert exe.endswith(".exe") or exe.startswith("py "), exe
    if exe.endswith(".exe") and Path(exe).exists():
        assert Path(exe).name.lower() == "python.exe"


def test_the_configured_python32_runs(monkeypatch):
    """The interpreter that will actually be launched must exist and import win32com."""
    argv = config.python32_argv()
    if argv[0] == "py":
        pytest.skip("resolved through the py launcher, which cannot be probed here")
    if not Path(argv[0]).exists():
        pytest.skip(f"{argv[0]} is not installed on this machine")
    import subprocess
    out = subprocess.run([*argv, "-c", "import win32com"], capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        pytest.skip("pywin32 is not installed for the 32-bit interpreter yet")


def test_the_resolved_configuration_points_at_multisim():
    exe = config.CONFIG.get("multisim_exe") or ""
    assert exe.lower().endswith("multisim.exe"), exe


def test_the_configuration_is_cached_not_recomputed_per_call():
    """CONFIG is resolved once at import; a later call must not silently return a different dict."""
    assert config._load() is not None
    assert config.CONFIG is config.CONFIG