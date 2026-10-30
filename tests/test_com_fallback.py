"""The COM fallback path must be testable without a real Multisim.

Every branch here decides whether a job is retried, and a wrong decision runs a long
simulation twice or leaves an orphaned Multisim process behind. The subprocess is the
only thing that needs the real world, so it is the only thing replaced.
"""
import json
import subprocess

import pytest

from multisimforge import com, config


class Proc:
    """Stand-in for subprocess.CompletedProcess."""

    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """Keep the real interpreter and the real process list out of these tests.

    python32_argv is imported inside _oneshot, so it has to be patched where it lives
    (the config module) rather than on com, where the name does not exist.
    """
    monkeypatch.setattr(com, "_multisim_pids", lambda: set())
    monkeypatch.setattr(config, "python32_argv", lambda: ["py"])


def test_oneshot_returns_the_last_json_line(monkeypatch):
    monkeypatch.setattr(com.subprocess, "run", lambda *a, **k: Proc(stdout="noise\n" + json.dumps({"ok": 1})))
    assert com._oneshot({"op": "ping"}, 5) == {"ok": 1}


def test_oneshot_sends_the_job_on_stdin(monkeypatch):
    seen = {}

    def run(args, **kw):
        seen["args"] = args
        seen["input"] = kw.get("input")
        seen["timeout"] = kw.get("timeout")
        return Proc(stdout=json.dumps({"ok": True}))

    monkeypatch.setattr(com.subprocess, "run", run)
    com._oneshot({"op": "analyze", "nets": ["vin"]}, 42)
    assert json.loads(seen["input"]) == {"op": "analyze", "nets": ["vin"]}
    assert seen["timeout"] == 42
    # proves the config seam is real: python32_argv is imported inside _oneshot,
    # so patching it on the config module is what keeps the real interpreter out
    assert seen["args"][0] == "py"
    assert "multisimforge.com" in seen["args"]


def test_oneshot_reports_a_timeout_as_such(monkeypatch):
    def run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(com.subprocess, "run", run)
    with pytest.raises(com.ComError, match="timeout"):
        com._oneshot({"op": "ping"}, 1)


def test_oneshot_reports_the_exit_code_and_stderr_tail(monkeypatch):
    monkeypatch.setattr(com.subprocess, "run", lambda *a, **k: Proc(returncode=3221226356, stderr="heap burst"))
    with pytest.raises(com.ComError) as info:
        com._oneshot({"op": "replace"}, 5)
    assert "3221226356" in str(info.value)
    assert "heap burst" in str(info.value)


def test_oneshot_passes_a_worker_error_through(monkeypatch):
    monkeypatch.setattr(com.subprocess, "run", lambda *a, **k: Proc(stdout=json.dumps({"error": "Q1 missing"})))
    with pytest.raises(com.ComError, match="Q1 missing"):
        com._oneshot({"op": "replace"}, 5)


def test_oneshot_ignores_non_json_noise_on_stdout(monkeypatch):
    monkeypatch.setattr(com.subprocess, "run", lambda *a, **k: Proc(stdout="ImportError: no pywin32"))
    with pytest.raises(com.ComError, match="failed"):
        com._oneshot({"op": "ping"}, 5)


def test_oneshot_kills_multisim_processes_it_started(monkeypatch):
    """A worker that dies abnormally leaves its Multisim running; it must be cleaned up."""
    pids = iter([set(), {"4242"}])
    monkeypatch.setattr(com, "_multisim_pids", lambda: next(pids, {"4242"}))
    monkeypatch.setattr(com, "python32_argv", lambda: ["py"], raising=False)
    killed = []

    def run(args, **kw):
        if args[0] == "taskkill":
            killed.append(args)
            return Proc()
        return Proc(returncode=1, stderr="died")

    monkeypatch.setattr(com.subprocess, "run", run)
    with pytest.raises(com.ComError):
        com._oneshot({"op": "ping"}, 5)
    assert killed, "the orphaned Multisim process was not killed"
    assert "4242" in killed[0]


def test_oneshot_leaves_pre_existing_multisim_alone(monkeypatch):
    """A Multisim the user had open before the call is not ours to kill."""
    monkeypatch.setattr(com, "_multisim_pids", lambda: {"1111"})
    monkeypatch.setattr(com, "python32_argv", lambda: ["py"], raising=False)
    killed = []

    def run(args, **kw):
        if args[0] == "taskkill":
            killed.append(args)
            return Proc()
        return Proc(returncode=1)

    monkeypatch.setattr(com.subprocess, "run", run)
    with pytest.raises(com.ComError):
        com._oneshot({"op": "ping"}, 5)
    assert killed == []


def test_call_does_not_replay_a_job_that_timed_out(monkeypatch):
    """A slow analysis must never be executed twice."""
    calls = []

    def request(job, timeout):
        calls.append(job)
        raise com.JobTimeout("accepted but slow")

    monkeypatch.setattr(com, "_request", request)
    monkeypatch.setattr(com, "_oneshot", lambda job, timeout: pytest.fail("a timed-out job must not be replayed"))
    with pytest.raises(com.ComError, match="timed out"):
        com.call({"op": "analyze"}, 5)
    assert len(calls) == 1


def test_call_falls_back_when_the_daemon_is_unreachable(monkeypatch):
    monkeypatch.setattr(com, "_request", lambda job, timeout: (_ for _ in ()).throw(ConnectionRefusedError()))
    monkeypatch.setattr(com, "_oneshot", lambda job, timeout: {"ok": True})
    assert com.call({"op": "ping"}, 5) == {"ok": True}