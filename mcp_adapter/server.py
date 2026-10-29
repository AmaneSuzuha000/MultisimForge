"""MCP stdio adapter: expose the local multisimforge capabilities (Multisim 14.3).

Design: this server is a thin, process-isolated wrapper. Every tool shells out to
``python -m multisimforge <cmd>`` in the adapter root, parses the JSON the CLI prints,
and returns it. No Multisim COM object lives in this process, so a COM crash or a
screenshot run cannot take the MCP server down. COM work is executed by
multisimforge.com in a 32-bit Python child (config.json -> python32).
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

ADAPTER_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ADAPTER_DIR / "config.json"
CLI_PYTHON = os.environ.get("MULTISIMFORGE_CLI_PYTHON", sys.executable)
WORK_DIR = ADAPTER_DIR / "work"
for sub in ("specs", "captures", "tmp"):
    (WORK_DIR / sub).mkdir(parents=True, exist_ok=True)

mcp = MCPServer(
    "multisimforge",
    title="MultisimForge",
    version="0.1.2",
    instructions=(
        "MultisimForge adapter for NI Multisim 14.3. Use check_design for offline "
        "connectivity/layout verification, inspect_design to read an existing .ms14, "
        "build_schematic to generate a design from a spec, capture_instruments for real "
        "Multisim instrument screenshots, simulate for COM/SPICE data, and convert_design "
        "for .ms14<->XML. All paths may be absolute or relative to the adapter directory."
    ),
)


def _python32_cmd(value: str) -> list[str]:
    """Split the launcher form 'py -3-32'; a real executable path (even with spaces) stays one element."""
    return value.split() if value.startswith(("py ", "py.exe ")) else [value]


def _load_config() -> dict[str, Any]:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else (ADAPTER_DIR / p)


def _run_cli(args: list[str], timeout_s: float = 300) -> dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(ADAPTER_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    started = time.time()
    try:
        proc = subprocess.run(
            [CLI_PYTHON, "-m", "multisimforge", *args],
            cwd=str(ADAPTER_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            env=env,
        )
        out, err, code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        err = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        code = None
    elapsed = round(time.time() - started, 2)
    parsed: Any = None
    text = (out or "").strip()
    if text:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
    status = parsed.get("status") if isinstance(parsed, dict) else None
    problems = parsed.get("problems") if isinstance(parsed, dict) else None
    error = None
    if parsed is None:
        error = f"CLI produced no JSON (exit={code})"
    elif code != 0:
        # A non-zero exit code is a failure even when the CLI printed a perfectly
        # valid JSON envelope: that is exactly how "problems" used to be lost.
        detail = "; ".join(problems or []) or str(parsed.get("error") or "")
        error = f"CLI exited {code} with status {status or 'MISSING'}" + (f": {detail[:800]}" if detail else "")
    elif isinstance(parsed, dict) and parsed.get("error"):
        error = str(parsed["error"])
    elif status not in ("PASS", "WARN"):
        error = f"CLI reported status {status or 'MISSING'} with exit code {code}"
    result: dict[str, Any] = {
        "ok": error is None,
        "status": status,
        "exit_code": code,
        "elapsed_s": elapsed,
        "command": ["multisimforge", *args],
        "result": parsed if parsed is not None else text[-8000:],
    }
    if problems:
        result["problems"] = problems
    if error:
        result["error"] = error
    if err and err.strip():
        result["stderr_tail"] = err.strip()[-4000:]
    return result


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _make_output_path(p: str, default_name: str) -> Path:
    target = _resolve(p) if p else (WORK_DIR / "tmp" / default_name)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


@mcp.tool()
async def runtime_status() -> dict[str, Any]:
    """Check the adapter environment.

    Only the items Multisim really needs are 'required' (they decide ok). Config
    extras such as node_decoder, samples_dir or master_db are reported as optional:
    the CLI locates them by itself and most features work without them.
    """
    cfg = _load_config()
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str = "", required: bool = True) -> None:
        checks.append({"name": name, "ok": bool(ok), "required": bool(required), "detail": detail})

    add("config.json", CONFIG_PATH.is_file(), str(CONFIG_PATH), required=False)
    add("cli_python", Path(CLI_PYTHON).is_file(), CLI_PYTHON)
    add("multisim_exe", Path(cfg.get("multisim_exe", "")).is_file(), cfg.get("multisim_exe", ""))
    add("samples_dir", Path(cfg.get("samples_dir", "")).is_dir(), cfg.get("samples_dir", ""), required=False)
    add("master_db", Path(cfg.get("master_db", "")).is_file(), cfg.get("master_db", ""), required=False)
    add("node_decoder", Path(cfg.get("node_decoder", "")).is_dir(), cfg.get("node_decoder", ""), required=False)
    add("python32", Path(cfg.get("python32", "")).is_file(), cfg.get("python32", ""))
    add("library_dir", Path(cfg.get("library_dir", "")).is_dir(), cfg.get("library_dir", ""), required=False)
    add("work_dir", Path(cfg.get("work_dir", "")).is_dir(), cfg.get("work_dir", ""), required=False)

    probe = (
        "import importlib.util as u,json;"
        "print(json.dumps({m: bool(u.find_spec(m)) for m in "
        "['win32api','win32gui','win32ui','PIL','numpy','scipy','win32com']}))"
    )
    try:
        cap = subprocess.run([CLI_PYTHON, "-c", probe], capture_output=True, text=True, timeout=30)
        deps = json.loads(cap.stdout.strip() or "{}")
    except Exception as exc:  # pragma: no cover - diagnostic only
        deps = {}
        add("capture_deps_error", False, str(exc))
    for name, ok in deps.items():
        add(f"cli_dep:{name}", ok, "capture/sim dependency", required=(name == "win32com"))
    if cfg.get("python32"):
        try:
            com = subprocess.run(
                [*_python32_cmd(cfg["python32"]), "-c", "import win32com.client, pythoncom;print('ok')"],
                capture_output=True, text=True, timeout=30,
            )
            add("python32_win32com", com.returncode == 0 and "ok" in com.stdout, (com.stderr or "").strip()[-300:])
        except Exception as exc:  # pragma: no cover
            add("python32_win32com", False, str(exc))

    if sys.platform == "win32":
        import winreg

        try:
            key = "WOW6432Node\\CLSID\\{D9CBB7A1-6AD6-438B-AD1B-42FB2DB00CAE}\\InprocServer32"
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, key) as h:
                dll = str(winreg.QueryValue(h, None))
            add("com_registration", Path(dll).is_file(), dll)
        except OSError as exc:
            add("com_registration", False, str(exc))
    else:
        add("com_registration", True, "not a Windows host: COM is unavailable by design", required=False)

    required = [item for item in checks if item.get("required", True)]
    return {
        "adapter": str(ADAPTER_DIR),
        "config": cfg,
        "checks": checks,
        "ok": all(item["ok"] for item in required),
        "failing": [item["name"] for item in required if not item["ok"]],
        "optional_failing": [item["name"] for item in checks if not item.get("required", True) and not item["ok"]],
    }


@mcp.tool()
async def check_design(ms14: str) -> dict[str, Any]:
    """Offline self-check: reconstruct nets from wire/junction graphics, compare against
    the target netlist, and report component overlap, wires through bodies, and crossings.
    Does not start Multisim. A non-empty 'problems' list means the drawing is electrically
    wrong; 'warnings' are layout quality issues (overlap/crossing)."""
    return await asyncio.to_thread(_run_cli, ["check", str(_resolve(ms14))])


@mcp.tool()
async def inspect_design(ms14: str) -> dict[str, Any]:
    """Read an existing .ms14: components (refdes/part/family/value), nets with pin lists,
    and virtual-instrument panel settings. Works on designs the user drew by hand."""
    return await asyncio.to_thread(_run_cli, ["inspect", str(_resolve(ms14))])


@mcp.tool()
async def build_schematic(spec: dict[str, Any], preview_png: str = "") -> dict[str, Any]:
    """Generate an editable .ms14 from a spec dict (netlist + layout + instruments + sim_time),
    run the offline self-check, and optionally export a Multisim preview PNG (COM). The spec
    supports layout mirror-x/mirror-y and explicit `wires` polylines when the routing has to be
    exact; see docs/spec.md. Returns ms14 path, warnings, problems."""
    spec = dict(spec)
    out = str(spec.get("output") or "")
    if out:
        spec["output"] = str(_make_output_path(out, "design.ms14"))
    else:
        spec["output"] = str(WORK_DIR / "tmp" / f"design_{_stamp()}.ms14")
    spec_path = WORK_DIR / "specs" / f"{Path(spec['output']).stem}_{_stamp()}.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    args = ["build", str(spec_path)]
    if preview_png:
        args += ["--preview", str(_make_output_path(preview_png, "preview.png"))]
    return await asyncio.to_thread(_run_cli, args, 600)


@mcp.tool()
async def capture_instruments(
    ms14: str,
    outdir: str = "",
    wait_s: float = 1.5,
    timeout_s: float = 90,
    keep_open: bool = False,
) -> dict[str, Any]:
    """Real screenshots from a running Multisim window: circuit canvas, every instrument
    panel (XSC scope, XBP bode, ...) and the full screen. Runs the interactive simulation,
    waits for the trace to settle, and auto-adjusts sim time when the scope scan is not
    full-width. Multisim comes to the foreground; do not use the mouse while it runs."""
    target = _resolve(outdir) if outdir else (WORK_DIR / "captures" / f"{Path(ms14).stem}_{_stamp()}")
    target.mkdir(parents=True, exist_ok=True)
    args = ["capture", str(_resolve(ms14)), str(target), "--wait", str(wait_s),
            "--timeout", str(timeout_s)]
    if keep_open:
        args.append("--keep-open")
    result = await asyncio.to_thread(_run_cli, args, timeout_s + 180)
    result["outdir"] = str(target)
    return result


@mcp.tool()
async def simulate(
    ms14: str,
    nets: str = "",
    op: bool = False,
    ac: str = "",
    out_net: str = "",
    in_net: str = "",
    at: str = "",
    tran: str = "",
    csv_dir: str = "",
) -> dict[str, Any]:
    """Run Multisim's own engine through COM: DC operating point (op), AC sweep
    (ac='10,100k,100' with out_net/in_net/at), or transient (tran='10m' or '10m,1u').
    Optionally write every data point to CSV. Values come from the real simulator."""
    args = ["sim", str(_resolve(ms14))]
    if nets:
        args += ["--nets", nets]
    if op:
        args.append("--op")
    if ac:
        args += ["--ac", ac]
    if out_net:
        args += ["--out", out_net]
    if in_net:
        args += ["--in", in_net]
    if at:
        args += ["--at", at]
    if tran:
        args += ["--tran", tran]
    if csv_dir:
        args += ["--csv", str(_make_output_path(csv_dir, "csv"))]
    return await asyncio.to_thread(_run_cli, args, 900)


@mcp.tool()
async def preview_design(ms14: str, png: str = "") -> dict[str, Any]:
    """Export the circuit canvas to PNG using Multisim itself (COM), without simulation."""
    out = _make_output_path(png, f"{Path(ms14).stem}_preview.png")
    return await asyncio.to_thread(_run_cli, ["preview", str(_resolve(ms14)), str(out)])


@mcp.tool()
async def set_sim_time(ms14: str, seconds: str) -> dict[str, Any]:
    """Set the interactive simulation end time stored in a .ms14 (e.g. '20ms')."""
    return await asyncio.to_thread(_run_cli, ["set-sim-time", str(_resolve(ms14)), seconds])


@mcp.tool()
async def convert_design(src: str, dst: str = "", direction: Literal["decode", "encode"] = "decode") -> dict[str, Any]:
    """Convert .ms14 -> XML (decode) or XML -> .ms14 (encode). Defaults write beside the source."""
    src_p = _resolve(src)
    if dst:
        dst_p = _resolve(dst)
    elif direction == "decode":
        dst_p = src_p.with_suffix(src_p.suffix + ".xml")
    else:
        dst_p = src_p.with_name(src_p.stem + "_rebuilt.ms14")
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    return await asyncio.to_thread(_run_cli, [direction, str(src_p), str(dst_p)])


@mcp.tool()
async def parts(action: Literal["list", "show", "harvest", "fetch", "init", "dbindex"], arg: str = "",
                force: bool = False, group: str = "", family: str = "") -> dict[str, Any]:
    """Component template library: list/search, show pin geometry, harvest from an existing
    .ms14 (offline), fetch a part from the Multisim database (COM + Node decoder), init from
    official samples (COM), or build the database index."""
    args = ["parts", action]
    if arg:
        args.append(str(_resolve(arg)) if action == "harvest" else arg)
    if force:
        args.append("--force")
    if group:
        args += ["--group", group]
    if family:
        args += ["--family", family]
    return await asyncio.to_thread(_run_cli, args, 900)


@mcp.tool()
async def run_cli(argv: list[str], timeout_s: float = 300) -> dict[str, Any]:
    """Escape hatch: run an arbitrary multisimforge subcommand (JSON args only), e.g.
    ['decode','a.ms14','a.xml'] or ['sim','a.ms14','--tran','10m','--csv','out']. Prefer the
    typed tools; use this only for commands without a wrapper."""
    return await asyncio.to_thread(_run_cli, [str(a) for a in argv], timeout_s)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()