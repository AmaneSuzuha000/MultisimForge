"""Multisim COM automation (32-bit only).

Multisim's automation interface is an in-process COM server that can only be
loaded by a 32-bit process, so all COM work runs in a 32-bit Python worker.

The worker is a **background daemon**: it starts once, keeps one hidden
Multisim instance connected, and serves requests over a loopback socket. That
way a batch of `sim` / `preview` / `parts fetch` calls reuses the same
instance instead of launching and killing Multisim every time.

The daemon exits by itself after `com_idle_timeout` seconds without requests
(config key, default 900). Stop it early with `multisimforge com stop`.
"""
import json
import os
import socket
import subprocess
import sys
import time

PROG_ID = "MultisimInterface.MultisimApp"


class ComError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Client side (any Python)
# ---------------------------------------------------------------------------
def _state_path():
    from .config import CONFIG
    from pathlib import Path
    return Path(CONFIG["library_dir"]).parent / "com_daemon.json"


def _multisim_pids() -> set:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq multisim.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, errors="ignore").stdout
    return {line.split('","')[1] for line in out.splitlines() if line.startswith('"multisim.exe"')}


def _read_state():
    try:
        state = json.loads(_state_path().read_text(encoding="utf-8"))
        return state if state.get("port") else None
    except (OSError, ValueError):
        return None


def _write_state(state):
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def _request(job: dict, timeout: float) -> dict:
    """Send a job to the daemon; start the daemon if it is not running."""
    from .config import CONFIG, ROOT, python32_argv
    state = _read_state()
    if state:
        try:
            return _roundtrip(state, job, min(timeout, 10))
        except (OSError, ValueError):
            pass                                        # daemon has exited; restart it
    proc = subprocess.Popen(
        [*python32_argv(), "-X", "utf8", "-m", "multisimforge.com", "--serve"],
        cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0),
    )
    deadline = time.time() + 90                        # the first connection to Multisim takes about 3-10 seconds
    while time.time() < deadline:
        state = _read_state()
        if state and state.get("pid") == proc.pid:
            try:
                return _roundtrip(state, job, timeout)
            except (OSError, ValueError):
                pass
        if proc.poll() is not None:
            break
        time.sleep(0.2)
    proc.kill()
    raise ComError("Cannot start the COM background process (Multisim automation interface unavailable)")


def _roundtrip(state: dict, job: dict, timeout: float) -> dict:
    payload = dict(job, token=state.get("token"))
    with socket.create_connection(("127.0.0.1", int(state["port"])), timeout=timeout) as sock:
        sock.settimeout(max(5.0, timeout))
        sock.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        chunks = []
        while True:
            block = sock.recv(65536)
            if not block:
                break
            chunks.append(block)
            if block.endswith(b"\n"):
                break
    text = b"".join(chunks).decode("utf-8", "replace").strip()
    if not text:
        raise ValueError("daemon returned no result")
    result = json.loads(text)
    if result.get("error"):
        raise ComError(result["error"])
    return result


def call(job: dict, timeout: float = 300) -> dict:
    """Run one COM job. Reuses the background daemon when possible and falls back to a one-shot subprocess on failure."""
    try:
        return _request(job, timeout)
    except (ComError, OSError, ValueError):
        return _oneshot(job, timeout)


def stop() -> bool:
    """Stop the background daemon (also releasing the Multisim instance it holds)."""
    state = _read_state()
    if not state:
        return False
    try:
        _roundtrip(state, {"op": "shutdown"}, 10)
    except (ComError, OSError, ValueError):
        pass
    try:
        _state_path().unlink()
    except OSError:
        pass
    return True


def status() -> dict:
    state = _read_state()
    if not state:
        return {"running": False}
    try:
        return {"running": True, **_roundtrip(state, {"op": "ping"}, 10)}
    except (ComError, OSError, ValueError) as exc:
        return {"running": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Compatibility fallback: one process per task
# ---------------------------------------------------------------------------
def _oneshot(job: dict, timeout: float) -> dict:
    from .config import CONFIG, ROOT
    before = _multisim_pids()
    try:
        proc = subprocess.run([*python32_argv(), "-X", "utf8", "-m", "multisimforge.com"],
                              input=json.dumps(job), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, cwd=str(ROOT))
    except subprocess.TimeoutExpired:
        proc = None
    lines = proc.stdout.strip().splitlines() if proc else []
    result = None
    if lines:
        try:
            result = json.loads(lines[-1])
        except json.JSONDecodeError:
            pass
    if result is None or result.get("error"):
        for pid in _multisim_pids() - before:      # if the process dies abnormally, the Multisim instance it started is not closed automatically
            subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
        if result is None:
            detail = "timeout" if proc is None else f"process exit code {proc.returncode}, {(proc.stderr or '').strip()[-400:]}"
            raise ComError(f"Multisim COM job {job.get('op')} failed ({detail})")
        raise ComError(result["error"])
    return result


# ---------------------------------------------------------------------------
# Worker side (32-bit Python + pywin32)
# ---------------------------------------------------------------------------
def _repair_gen_py():
    """A half-written pywin32 gen_py cache makes all subsequent calls fail; it is only a cache, so delete it when broken."""
    import shutil
    from win32com import client
    root = client.gencache.GetGeneratePath()
    if os.path.isdir(root) and any(name.endswith(".temp") for _, _, files in os.walk(root) for name in files):
        shutil.rmtree(root, ignore_errors=True)


def _connect():
    import pythoncom
    from win32com import client
    pythoncom.CoInitialize()
    _repair_gen_py()
    try:
        app = client.gencache.EnsureDispatch(PROG_ID)
    except Exception:
        app = client.dynamic.Dispatch(PROG_ID)
    if not app.IsConnected:
        app.Connect()
    return app


def _disconnect(app):
    try:
        app.Disconnect()
    except Exception:
        pass


def _open(app, path):
    path = os.path.abspath(path)
    if not os.path.isfile(path):          # OpenFile crashes outright if the path does not exist
        raise FileNotFoundError(path)
    return app.OpenFile(path)


def _bstrs(values):
    import pythoncom
    from win32com import client
    return client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_BSTR, list(values))


def _clean(text):
    # Multisim returns UTF-8 bytes as BSTR code points
    try:
        return str(text).encode("latin-1").decode("utf-8", errors="replace")
    except Exception:
        return str(text)


def _collect(circuit, names, timeout):
    import pythoncom
    deadline = time.time() + timeout
    pending = list(names)
    while pending and time.time() < deadline:
        pending = [n for n in pending if not circuit.OutputReady(n)]
        if pending:
            time.sleep(0.05)
    data = {}
    for n in names:
        if n not in pending:
            rows, _ = circuit.GetOutputData(n, pythoncom.Missing, pythoncom.Missing)
            data[n] = [list(r) for r in rows]
    return data, pending


def _analyze(app, job):
    c = _open(app, job["ms14"])
    outputs = list(c.EnumOutputs(0) or ())
    results = []
    for a in job.get("analyses", []):
        wanted = a.get("outputs") or outputs
        names = [n for n in wanted if n in outputs]      # a nonexistent output name makes the analysis fail outright
        if not names:
            raise RuntimeError(f"No readable outputs in the circuit (probes not recognized by Multisim); existing outputs {outputs}")
        if a["type"] == "op":
            c.DoDCOperatingPoint(_bstrs(names))
        elif a["type"] == "ac":
            sweep = {"dec": 0, "oct": 1, "lin": 2}[a.get("sweep", "dec")]
            c.DoACSweep(sweep, int(a.get("points", 100)), float(a["start"]), float(a["stop"]), _bstrs(names))
        elif a["type"] == "tran":
            if c.SimulationState:
                c.StopSimulation()
            rate = float(a.get("rate") or 2000 / float(a["time"]))
            for n in names:
                try:
                    c.ClearOutputRequest(n)
                except Exception:
                    pass
                c.SetOutputRequest(n, 2, rate, int(float(a["time"]) * rate) + 1, False)
            c.RunSimulation(float(a["time"]), False)
        data, pending = _collect(c, names, float(a.get("timeout", 120)))
        if a["type"] == "tran" and c.SimulationState:
            c.StopSimulation()
        pending += [n for n in wanted if n not in outputs]
        results.append({"type": a["type"], "data": data, "missing": pending,
                        "last_error": _clean(c.LastErrorMessage) if pending else ""})
    return {"outputs": outputs, "results": results}


def _spice(app, job):
    work = job["work"]
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, "circuit.cir"), "w", encoding="utf-8") as f:
        f.write(job["netlist"])
    raw = os.path.join(work, "result.raw")
    if os.path.exists(raw):
        os.remove(raw)
    with open(os.path.join(work, "run.txt"), "w", encoding="utf-8") as f:      # the path must be absolute
        f.write(f"source {os.path.join(work, 'circuit.cir')}\n" + "\n".join(job["commands"]) + f"\nwrite {raw}\n")
    c = app.NewFile()
    log = os.path.join(work, "run.log")
    c.DoCommandLine(os.path.join(work, "run.txt"), log)
    deadline = time.time() + float(job.get("timeout", 120))
    while c.SimulationState and time.time() < deadline:
        time.sleep(0.2)
    text = open(log, encoding="utf-8", errors="replace").read() if os.path.exists(log) else ""
    from .xmlio import decode_uc
    text = decode_uc(text)
    return {"raw": raw if os.path.exists(raw) else None, "log": text[-4000:], "last_error": _clean(c.LastErrorMessage)}


def _replace(app, job):
    c = _open(app, job["ms14"])
    names = list(c.EnumComponents(0) or ())
    if job["ref"] not in names:                # a nonexistent reference designator also crashes the process
        raise RuntimeError(f"{job['ref']} not found in the carrier circuit; existing {names}")
    new_ref = c.ReplaceComponent(job["ref"], "", 0, job["group"], job["family"], job["name"], job.get("model", ""))
    c.SaveAs(os.path.abspath(job["out"]))
    return {"ref": str(new_ref), "components": list(c.EnumComponents(0) or ()), "last_error": _clean(c.LastErrorMessage)}


def _execute(app, job):
    op = job["op"]
    if op == "blank":
        c = app.NewFile()
        c.SaveAs(os.path.abspath(job["out"]))
        return {"out": job["out"]}
    if op == "image":
        c = _open(app, job["ms14"])
        c.GetCircuitImage(0, os.path.abspath(job["out"]))
        return {"out": job["out"], "last_error": _clean(c.LastErrorMessage)}
    if op == "components":
        c = _open(app, job["ms14"])
        return {"components": list(c.EnumComponents(0) or ()), "outputs": list(c.EnumOutputs(0) or ())}
    if op == "analyze":
        return _analyze(app, job)
    if op == "spice":
        return _spice(app, job)
    if op == "replace":
        return _replace(app, job)
    raise ValueError(f"Unknown job {op}")


def _run(job):
    """One-shot execution: connect → do the work → disconnect."""
    app = _connect()
    try:
        return _execute(app, job)
    finally:
        _disconnect(app)


def _serve():
    """Background daemon: keeps one Multisim instance alive and reuses it across requests."""
    import secrets
    import socket as _socket
    import threading
    from .config import CONFIG

    app_holder = {"app": None, "busy": threading.Lock()}

    def app():
        if app_holder["app"] is None:
            app_holder["app"] = _connect()
        return app_holder["app"]

    def drop():
        if app_holder["app"] is not None:
            _disconnect(app_holder["app"])
        app_holder["app"] = None

    token = secrets.token_hex(16)
    server = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    server.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    port = server.getsockname()[1]
    _write_state({"port": port, "token": token, "pid": os.getpid()})
    idle = float(CONFIG.get("com_idle_timeout") or 900)
    last = time.time()

    def handle(conn):
        buffer = b""
        while not buffer.endswith(b"\n"):
            block = conn.recv(65536)
            if not block:
                return
            buffer += block
        try:
            job = json.loads(buffer.decode("utf-8"))
        except ValueError:
            conn.sendall(b'{"error": "bad request"}\n')
            return
        if job.get("token") != token:
            conn.sendall(b'{"error": "bad token"}\n')
            return
        op = job.get("op")
        if op == "ping":
            reply = {"pid": os.getpid(), "connected": app_holder["app"] is not None}
        elif op == "shutdown":
            reply = {"bye": True}
        else:
            with app_holder["busy"]:
                try:
                    reply = _execute(app(), job)
                except Exception as exc:            # a single failed job does not affect the daemon
                    reply = {"error": f"{type(exc).__name__}: {_clean(exc)}"}
                    if not app_holder["app"] or not _alive(app_holder["app"]):
                        drop()
        conn.sendall((json.dumps(reply, ensure_ascii=False) + "\n").encode("utf-8"))
        return op == "shutdown"

    try:
        while True:
            server.settimeout(30)
            try:
                conn, _ = server.accept()
            except _socket.timeout:
                if time.time() - last > idle:
                    break
                continue
            last = time.time()
            try:
                if handle(conn):
                    break
            finally:
                conn.close()
    finally:
        drop()
        try:
            _state_path().unlink()
        except OSError:
            pass


def _alive(app) -> bool:
    try:
        return bool(app.IsConnected)
    except Exception:
        return False


if __name__ == "__main__":
    if "--serve" in sys.argv:
        _serve()
    else:
        try:
            result = _run(json.loads(sys.stdin.read()))
        except Exception as exc:
            result = {"error": f"{type(exc).__name__}: {_clean(exc)}"}
        print(json.dumps(result, ensure_ascii=False))