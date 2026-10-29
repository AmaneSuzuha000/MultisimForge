"""multisimforge command-line entry point.

Every command prints one JSON envelope to stdout and exits with the code that
matches its status (see multisimforge/result.py): 0 PASS/WARN, 2 FAIL, 1 ERROR.
"""
import argparse
import json
import sys
from pathlib import Path

from .result import Status, exit_code_for, worst


def _out(payload, status=Status.PASS):
    from .result import envelope
    print(json.dumps(envelope(payload, status), ensure_ascii=False, indent=1, default=str))


def _floats(text, n=None):
    from .netlist import parse_value
    vals = [parse_value(t) for t in text.split(",") if t.strip()]
    if n and len(vals) not in n:
        raise ValueError(f"wrong number of values in argument {text!r}")
    return vals


def cmd_parts(a):
    from . import parts
    lib = parts.Library()
    if a.action == "init":
        r = parts.init_library(lib)
        status = Status.WARN if r["failed"] else Status.PASS
        return {"library": str(lib.path), "added": len(r["added"]), "failed": r["failed"]}, status
    if a.action == "list":
        rows = [f"{n}  [{i['family']}]  {' '.join(i['pins'])}" for n, i in lib.search(a.arg or "")]
        return {"count": len(rows), "parts": rows}, Status.PASS
    if a.action == "show":
        key = lib.resolve(a.arg or "")
        if key is None:
            raise KeyError(f"part not found in the library: {a.arg!r} (try 'multisimforge parts fetch {a.arg}')")
        pin_xy, body = _pin_xy(lib, key)
        return {"name": key, **lib.index[key], "pin_xy": pin_xy, "body": body}, Status.PASS
    if a.action == "harvest":
        return parts.harvest_path(Path(a.arg), lib, force=a.force), Status.PASS
    if a.action == "fetch":
        return parts.fetch(a.arg, lib, group=a.group, family=a.family), Status.PASS
    return parts.build_db_index(lib), Status.PASS


def _pin_xy(lib, key):
    """Pin coordinates relative to the pin center and lead directions at 0 deg, plus the symbol body extent."""
    from . import builder, check, parts, xmlio
    part = lib.load(key)
    items = part.find("Items")
    sym = items.find("Item[@Class='CIITSymbolComp']")
    ports = [i for i in items if i.get("Class") == "CiPort"]
    builder._rotate(sym, builder._base_angle(sym, ports, lib.index[key]["pins"]))
    pins = builder.pin_info(sym)
    if not pins:
        return {}, ""
    name_of = {p.get("CiID"): xmlio.unasc(p[0].get("LocalName")) for p in ports}
    xs, ys = [p["x"] for p in pins.values()], [p["y"] for p in pins.values()]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    words = {(-1, 0): "left", (1, 0): "right", (0, -1): "up", (0, 1): "down"}
    meaning = parts.INSTRUMENT_PIN_NAMES.get(key, {})
    pin_xy = {}
    for pid, p in pins.items():
        name = name_of.get(pid, pid)
        label = f"{name} ({meaning[name]})" if name in meaning else name
        pin_xy[label] = f"({p['x'] - cx:g}, {p['y'] - cy:g}) {words.get(p['direction'], '')}"
    box = check.symbol_bbox(sym)
    body = f"x {box[0] - cx:+.0f}..{box[2] - cx:+.0f}, y {box[1] - cy:+.0f}..{box[3] - cy:+.0f} (excluding name labels)" if box else ""
    return pin_xy, body


def cmd_build(a):
    from . import builder, check, com
    from .router import RouteError
    spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
    try:
        r = builder.build(spec, allow_fallback=a.allow_fallback)
    except RouteError as exc:
        return {"problems": [str(exc)], "route_failures": exc.failures, "warnings": [],
                "degraded": False}, Status.FAIL
    report = check.check_file(r["ms14"], r.get("expected"))
    warnings = r["warnings"] + report["warnings"]
    payload = {
        "ms14": r["ms14"],
        "run_id": r.get("run_id"),
        "sim_time": r["sim_time"],
        "warnings": warnings,
        "problems": report["problems"],
        "degraded": r.get("degraded", False),
        "route_failures": r.get("route_failures", []),
        "placements": {p["refdes"]: p["box"] for p in r["placements"]},
    }
    if a.preview:
        com.call({"op": "image", "ms14": r["ms14"], "out": str(Path(a.preview).resolve())})
        payload["preview"] = str(Path(a.preview).resolve())
    if report["problems"]:
        return payload, Status.FAIL
    return payload, (Status.WARN if warnings else Status.PASS)


def cmd_preview(a):
    from . import com
    r = com.call({"op": "image", "ms14": str(Path(a.ms14).resolve()), "out": str(Path(a.png).resolve())})
    return r, (Status.WARN if r.get("last_error") else Status.PASS)


def cmd_check(a):
    from . import check
    r = check.check_file(a.ms14)
    if r["problems"]:
        return r, Status.FAIL
    return r, (Status.WARN if r["warnings"] else Status.PASS)


def cmd_inspect(a):
    from . import check
    return check.inspect(a.ms14), Status.PASS


def cmd_sim(a):
    from . import sim
    nets = [n for n in (a.nets or "").split(",") if n]
    ac = None
    if a.ac:
        v = _floats(a.ac, (2, 3))
        ac = (v[0], v[1], int(v[2]) if len(v) > 2 else 100)
    tran = None
    if a.tran:
        v = _floats(a.tran, (1, 2))
        tran = (v[0], v[1] if len(v) > 1 else None)
    if not (a.op or ac or tran):
        raise ValueError("choose at least one analysis: --op / --ac / --tran")
    r = sim.run(a.ms14, nets, op=a.op, ac=ac, tran=tran, ac_out=a.out, ac_in=getattr(a, "in"),
                at=_floats(a.at) if a.at else (), csv_dir=a.csv)
    return r, Status.PASS


def cmd_spice(a):
    from . import sim
    r = sim.spice(Path(a.cir).read_text(encoding="utf-8"), a.cmd)
    vectors = r.pop("vectors", None)
    if vectors:
        r["vectors"] = {k: {"points": len(v), "first": v[0] if not isinstance(v[0], complex) else str(v[0]),
                            "last": v[-1] if not isinstance(v[-1], complex) else str(v[-1])} for k, v in vectors.items()}
    return r, Status.PASS


def cmd_capture(a):
    from . import capture
    r = capture.capture(a.ms14, a.outdir, wait=a.wait, timeout=a.timeout, keep_open=a.keep_open,
                        on_netlist_error=a.on_netlist_error)
    return r, (Status.WARN if r.get("warnings") else Status.PASS)


def cmd_sim_time(a):
    from . import builder
    from .netlist import parse_value
    value = parse_value(a.seconds)
    builder.set_sim_time(a.ms14, value)
    return {"ms14": a.ms14, "sim_time": value}, Status.PASS


def cmd_com(a):
    from . import com
    if a.action == "status":
        return com.status(), Status.PASS
    return {"stopped": com.stop()}, Status.PASS


def cmd_codec(a):
    from . import codec
    data = Path(a.src).read_bytes()
    Path(a.dst).write_bytes(codec.decode(data) if a.command == "decode" else codec.encode(data))
    return {"written": a.dst}, Status.PASS


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="multisimforge",
                                description="Control NI Multisim 14 in text: generate circuits, preview, run simulations and read data, take real screenshots")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("parts", help="Part library: init to create / list to search / show details / harvest to collect / fetch from database")
    s.add_argument("action", choices=["init", "list", "show", "harvest", "fetch", "dbindex"])
    s.add_argument("arg", nargs="?")
    s.add_argument("--force", action="store_true", help="overwrite parts with the same name during harvest")
    s.add_argument("--group", help="fetch: database group, e.g. Mixed")
    s.add_argument("--family", help="fetch: part family, e.g. TIMER")
    s.set_defaults(func=cmd_parts)

    s = sub.add_parser("build", help="build a .ms14 from spec.json and self-check it")
    s.add_argument("spec")
    s.add_argument("--preview", help="also export a preview image PNG")
    s.add_argument("--allow-fallback", action="store_true",
                   help="allow degraded routing (a wire that has no obstacle-free path) instead of failing")
    s.set_defaults(func=cmd_build)

    s = sub.add_parser("preview", help="export the circuit diagram as PNG using Multisim")
    s.add_argument("ms14")
    s.add_argument("png")
    s.set_defaults(func=cmd_preview)

    s = sub.add_parser("check", help="self-check: wiring matches nets, overlapping components, crossing wires")
    s.add_argument("ms14")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("inspect", help="read components, nets, and instrument settings from the circuit")
    s.add_argument("ms14")
    s.set_defaults(func=cmd_inspect)

    s = sub.add_parser("sim", help="run DC operating point / AC sweep / transient analysis on a .ms14")
    s.add_argument("ms14")
    s.add_argument("--nets", help="nets to measure, comma-separated")
    s.add_argument("--op", action="store_true", help="DC operating point")
    s.add_argument("--ac", help="AC sweep: start,stop[,points per decade], e.g. 10,100k,100")
    s.add_argument("--out", help="AC: output net")
    s.add_argument("--in", help="AC: input net (when given, computes the transfer function as out/in)")
    s.add_argument("--at", help="AC: frequencies to read, comma-separated")
    s.add_argument("--tran", help="transient: duration[, sample rate], e.g. 10m")
    s.add_argument("--csv", help="directory to write the data points as CSV")
    s.set_defaults(func=cmd_sim)

    s = sub.add_parser("spice", help="run an arbitrary SPICE netlist with Multisim's command-line engine")
    s.add_argument("cir")
    s.add_argument("--cmd", action="append", required=True, help='analysis command, e.g. "tran 1u 10m"; may be repeated')
    s.set_defaults(func=cmd_spice)

    s = sub.add_parser("capture", help="open Multisim, run the simulation, and capture the circuit, instrument panels, and full screen")
    s.add_argument("ms14")
    s.add_argument("outdir")
    s.add_argument("--wait", type=float, default=1.5, help="how long the display must be still to consider the simulation finished (seconds)")
    s.add_argument("--timeout", type=float, default=90, help="maximum wait (seconds)")
    s.add_argument("--keep-open", action="store_true", help="do not close Multisim after capturing")
    s.add_argument("--on-netlist-error", choices=["abort", "continue"], default="abort",
                   help="what to do when Multisim reports a netlist error (default: abort and fail)")
    s.set_defaults(func=cmd_capture)

    s = sub.add_parser("set-sim-time", help="change the interactive simulation end time")
    s.add_argument("ms14")
    s.add_argument("seconds")
    s.set_defaults(func=cmd_sim_time)

    s = sub.add_parser("com", help="COM background process (reuses a single Multisim instance)")
    s.add_argument("action", choices=["status", "stop"])
    s.set_defaults(func=cmd_com)

    for name in ("decode", "encode"):
        s = sub.add_parser(name, help=".ms14 <-> XML (for debugging)")
        s.add_argument("src")
        s.add_argument("dst")
        s.set_defaults(func=cmd_codec)

    a = p.parse_args(argv)
    try:
        payload, status = a.func(a)
    except KeyboardInterrupt:
        _out({"error": "cancelled"}, Status.CANCELLED)
        sys.exit(exit_code_for(Status.CANCELLED))
    except (ValueError, KeyError, FileNotFoundError, RuntimeError) as exc:
        _out({"error": f"{type(exc).__name__}: {exc}"}, Status.ERROR)
        sys.exit(exit_code_for(Status.ERROR))
    status = worst([status])
    _out(payload, status)
    sys.exit(exit_code_for(status))


if __name__ == "__main__":
    main()
