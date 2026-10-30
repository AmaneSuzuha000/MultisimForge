"""Daemon state handling must never crash the caller, whatever is on disk.

_read_state is the first thing every COM call does. A truncated, empty, non-object or
partially written file used to be indistinguishable from "no daemon" only by luck;
these pin down that it stays that way, and that a stale pid cannot look healthy.
"""
import json

import pytest

from multisimforge import com


@pytest.fixture()
def state_file(tmp_path, monkeypatch):
    path = tmp_path / "com_daemon.json"
    monkeypatch.setattr(com, "_state_path", lambda: path)
    return path


@pytest.mark.parametrize("content", [
    "",                                  # the file exists but nothing was written yet
    "{",                                 # truncated mid-write
    "null",                              # valid JSON, not an object
    "[1, 2, 3]",                         # valid JSON, wrong shape
    "\x00\x00\x00",                     # binary garbage
])
def test_unreadable_state_is_treated_as_no_daemon(state_file, content):
    state_file.write_text(content, encoding="utf-8")
    assert com._read_state() is None


def test_missing_state_is_no_daemon(state_file):
    assert not state_file.exists()
    assert com._read_state() is None


def test_state_without_a_port_is_not_a_daemon(state_file):
    state_file.write_text(json.dumps({"token": "t", "pid": 1}), encoding="utf-8")
    assert com._read_state() is None


def test_status_never_raises_when_the_state_is_corrupt(state_file):
    state_file.write_text("not json", encoding="utf-8")
    assert com.status() == {"running": False}


def test_status_reports_the_error_of_a_dead_daemon(state_file, monkeypatch):
    state_file.write_text(json.dumps({"port": 65535, "token": "t", "pid": 1}), encoding="utf-8")

    def dead(state, job, timeout):
        raise ConnectionRefusedError(10061, "nothing listening")

    monkeypatch.setattr(com, "_roundtrip", dead)
    out = com.status()
    assert out["running"] is False
    assert "10061" in out["error"] or "refused" in out["error"].lower()


def test_write_state_creates_the_directory(tmp_path, monkeypatch):
    deep = tmp_path / "a" / "b" / "com_daemon.json"
    monkeypatch.setattr(com, "_state_path", lambda: deep)
    com._write_state({"port": 1, "token": "t", "pid": 2})
    assert json.loads(deep.read_text(encoding="utf-8"))["port"] == 1


def test_stop_without_a_daemon_is_not_an_error(state_file):
    assert com.stop() is False
