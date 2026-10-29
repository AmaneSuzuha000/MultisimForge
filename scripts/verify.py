"""One-shot health check.

    python scripts/verify.py            # offline checks only
    python scripts/verify.py --real     # also drive Multisim (needs a licensed install)

Prints a table of checks and exits non-zero if any offline check fails.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from multisimforge import builder, check, codec, config, netlist, xmlio   # noqa: E402

results = []


def record(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'  ' + detail if detail else ''}")


def run(argv):
    return subprocess.run([sys.executable, "-m", "multisimforge", *argv], cwd=str(ROOT),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def offline():
    record("python", sys.version_info >= (3, 10), sys.version.split()[0])

    conf = config.CONFIG
    record("multisim.exe found", Path(conf["multisim_exe"]).is_file(), conf["multisim_exe"])
    record("samples dir found", Path(conf["samples_dir"]).is_dir(), conf["samples_dir"])
    record("component database found", Path(conf["master_db"]).is_file(), conf["master_db"])
    record("32-bit interpreter configured", bool(conf.get("python32")), str(conf.get("python32")))

    lib = Path(conf["library_dir"])
    index = lib / "index.json"
    count = len(json.loads(index.read_text(encoding="utf-8"))) if index.exists() else 0
    record("part library built", count > 0, f"{count} parts in {lib}")

    data = (b"<Root>" + b"<Item Value=\"1\"/>\n" * 2000 + b"</Root>")
    record("codec round-trip", codec.explode(codec.implode(data)) == data, f"{len(data)} bytes")

    samples = sorted(Path(conf["samples_dir"]).rglob("*.ms14"))[:3]
    if samples:
        ok = all(codec.decode(codec.encode(codec.decode(p.read_bytes()))) == codec.decode(p.read_bytes())
                 for p in samples)
        record(".ms14 round-trip", ok, f"{len(samples)} sample files")
    else:
        record(".ms14 round-trip", False, "no samples to test with")

    record("netlist parser", netlist.parse_value("4.7k") == 4700)
    record("netlist pin syntax", netlist.parse("U1 LM555CN OUT=out\n")[0].named == {"OUT": "out"})

    for name in ("rc_lowpass.json", "phase_lead.json", "explicit_wiring.json"):
        spec = json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            spec["output"] = str(Path(tmp) / Path(spec["output"]).name)
            built = builder.build(spec)
            report = check.check_file(built["ms14"])
        ok = report["problems"] == []
        record(f"build {name}", ok, f"nets={len(report['nets'])} crossings={report['crossings']}")

    out = run(["parts", "list"])
    ok = out.returncode == 0 and '"count"' in out.stdout and not out.stderr.strip()
    record("cli + part library", ok, out.stdout.strip().splitlines()[0][:60] if out.stdout.strip() else (out.stderr or "")[:60])

    xml = '<?xml version="1.0" encoding="ASCII"?>\n<Root>\n<Item A="1"/>\n</Root>\n'
    record("xml round-trip", xmlio.to_text(xmlio.parse_text(xml)) == xml)


def real():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        spec = json.loads((ROOT / "examples" / "rc_lowpass.json").read_text(encoding="utf-8"))
        spec["output"] = str(tmp / "rc.ms14")
        built = builder.build(spec)
        record("build for real run", Path(built["ms14"]).is_file(), built["ms14"])

        png = tmp / "rc.png"
        out = run(["preview", str(built["ms14"]), str(png)])
        record("preview via Multisim", png.is_file() and png.stat().st_size > 1000,
               f"{png.stat().st_size if png.exists() else 0} bytes")

        out = run(["sim", str(built["ms14"]), "--ac", "10,100k", "--out", "vout", "--in", "vin", "--at", "1591"])
        ok = False
        detail = (out.stdout or out.stderr).strip().replace("\n", " ")[:120]
        try:
            payload = json.loads(out.stdout)
            at = payload["ac"]["at"][0]
            ok = 0.66 < at["gain"] < 0.75          # RC low-pass: -3 dB at 1/(2*pi*R*C) = 1591 Hz
            detail = f"gain@1591Hz={at['gain']:.3f} phase={at['phase_deg']:.1f}deg"
        except Exception:
            pass
        record("ac sweep via Multisim", ok, detail)

        out = run(["com", "status"])
        try:
            status = json.loads(out.stdout)
        except ValueError:
            status = {}
        record("com worker", bool(status.get("running")) or "error" in status, json.dumps(status)[:100])
        run(["com", "stop"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="also run the checks that need Multisim")
    args = ap.parse_args()
    offline()
    if args.real:
        real()
    failed = [name for name, ok, _ in results if not ok]
    print()
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("failed:", ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()