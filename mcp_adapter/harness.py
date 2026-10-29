"""Batch MCP client for the multisimforge adapter (stdio JSON-RPC).

Usage:
    python harness.py calls.json [timeout_s] [out.json]
    python harness.py --list

calls.json: [{"tool": "check_design", "arguments": {...}}, ...]
Runs the server with 32-bit Python (MCP SDK lives there), one session for all calls,
and writes a result list with per-call ok/elapsed/text.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = os.environ.get("MULTISIMFORGE_MCP_PYTHON", sys.executable)
SERVER = str(HERE / "server.py")


def main() -> None:
    if "--list" in sys.argv:
        calls: list[dict] = []
    else:
        if len(sys.argv) < 2:
            raise SystemExit(__doc__)
        calls = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    timeout = float(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else 600.0
    out_path = Path(sys.argv[3]) if len(sys.argv) > 3 else HERE.parent / "work" / "mcp_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    log = open(HERE.parent / "work" / "mcp_server_stderr.log", "w", encoding="utf-8", errors="replace")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen([PY, SERVER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=log, cwd=str(HERE), env=env)
    lines: "queue.Queue[str]" = queue.Queue()

    def reader() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            lines.put(raw.decode("utf-8", "replace").strip())

    threading.Thread(target=reader, daemon=True).start()

    def send(obj: dict) -> None:
        assert proc.stdin is not None
        proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        proc.stdin.flush()

    def wait_for(mid: int, tmo: float) -> dict:
        deadline = time.time() + tmo
        while time.time() < deadline:
            try:
                line = lines.get(timeout=min(1.0, max(0.05, deadline - time.time())))
            except queue.Empty:
                continue
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == mid:
                return msg
            if "method" in msg and "id" in msg:  # server request -> empty result
                send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
        raise TimeoutError(f"no response for id={mid}")

    send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
          "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                     "clientInfo": {"name": "ms-adapter-harness", "version": "0.1.0"}}})
    wait_for(1, 60)
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    listing = wait_for(2, 60)
    tools = [t.get("name") for t in listing.get("result", {}).get("tools", [])]
    print("TOOLS:", ", ".join(tools), flush=True)

    results = [{"tool": "__tools__", "ok": True, "tools": tools}]
    for index, call in enumerate(calls, 10):
        name = call["tool"]
        args = call.get("arguments", {})
        started = time.time()
        send({"jsonrpc": "2.0", "id": index, "method": "tools/call",
              "params": {"name": name, "arguments": args}})
        try:
            resp = wait_for(index, timeout)
            result = resp.get("result", {})
            text = "".join(c.get("text", "") for c in (result.get("content") or []) if c.get("type") == "text")
            payload = None
            try:
                payload = json.loads(text) if text else None
            except json.JSONDecodeError:
                payload = None
            entry = {"tool": name, "ok": not result.get("isError"), "elapsed": round(time.time() - started, 1),
                     "text": text[:20000]}
            if payload is not None:
                entry["payload"] = payload
            results.append(entry)
            print(f"[{name}] ok={entry['ok']} {entry['elapsed']}s", flush=True)
        except Exception as exc:  # noqa: BLE001
            results.append({"tool": name, "ok": False, "error": str(exc)})
            print(f"[{name}] FAILED: {exc}", flush=True)

    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE", out_path)
    proc.terminate()
    try:
        proc.wait(5)
    except Exception:  # noqa: BLE001
        proc.kill()
    log.close()
    tail = (HERE.parent / "work" / "mcp_server_stderr.log").read_text(encoding="utf-8", errors="replace")
    if tail.strip():
        print("STDERR TAIL:", tail[-1500:])


if __name__ == "__main__":
    main()