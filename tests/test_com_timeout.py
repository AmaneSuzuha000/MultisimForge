"""A COM job that the daemon accepted must never be executed a second time.

0.1.2 gave every request a 10-second ceiling and replayed it in a one-shot process
on timeout, so any analysis that legitimately ran longer than 10 s ran twice.
"""
import socket
import threading

import pytest

from multisimforge import com


class FakeDaemon:
    """Accepts one connection, reads the request, and never answers."""

    def __init__(self):
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.received = []
        self._stop = False
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        try:
            conn, _ = self.server.accept()
        except OSError:
            return
        with conn:
            data = b""
            while not data.endswith(b"\n"):
                block = conn.recv(65536)
                if not block:
                    return
                data += block
            self.received.append(data)
            while not self._stop:                 # hold the connection open: the client must time out
                threading.Event().wait(0.05)

    def close(self):
        self._stop = True
        try:
            self.server.close()
        except OSError:
            pass


@pytest.fixture()
def daemon(monkeypatch):
    fake = FakeDaemon()
    monkeypatch.setattr(com, "_read_state", lambda: {"port": fake.port, "token": "t"})
    yield fake
    fake.close()


def test_job_timeout_is_not_replayed(daemon, monkeypatch):
    replayed = []
    monkeypatch.setattr(com, "_oneshot", lambda job, timeout: replayed.append(job) or {})
    with pytest.raises(com.ComError) as excinfo:
        com.call({"op": "analyze"}, timeout=0.2)
    assert "not retried" in str(excinfo.value) or "timed out" in str(excinfo.value)
    assert replayed == [], "an accepted job must not be replayed in a one-shot process"


def test_roundtrip_raises_job_timeout(daemon):
    with pytest.raises(com.JobTimeout):
        com._roundtrip({"port": daemon.port, "token": "t"}, {"op": "analyze"}, 0.2)


def test_unreachable_daemon_still_falls_back(monkeypatch):
    """A daemon that is not there at all is a different case: falling back is correct."""
    calls = []

    def no_daemon(state, job, timeout):
        calls.append(job)
        raise ConnectionRefusedError(10061, "nothing listening")

    monkeypatch.setattr(com, "_read_state", lambda: {"port": 9, "token": "t"})
    monkeypatch.setattr(com, "_roundtrip", no_daemon)
    started = []

    def no_popen(*a, **k):
        started.append(a)
        raise OSError("test stops here: the daemon would be started now")

    # Patch the name as com sees it, not subprocess itself: monkeypatching
    # com.subprocess.Popen would replace the real module attribute for every later
    # test and break unrelated subprocess calls with WinError 6.
    monkeypatch.setattr(com, "Popen", no_popen)
    with pytest.raises(OSError):
        com._request({"op": "ping"}, 0.2)
    assert calls == [{"op": "ping"}], "an unreachable daemon gets one attempt before it is restarted"
    assert started, "restarting the daemon is the correct response to a dead one"
