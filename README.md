# MultisimForge

Generate editable NI Multisim 14 circuits from plain text, verify connectivity offline, pull numbers out of Multisim's own simulation engine, and capture real instrument panels.

CLI + MCP server + agent skill in one package.

```text
spec.json ──► MultisimForge build ──► .ms14 (editable) ──► MultisimForge check    offline connectivity check
                                      │
                                      ├──► MultisimForge preview   real schematic PNG from Multisim
                                      ├──► MultisimForge sim       real OP / AC / TRAN data
                                      └──► MultisimForge capture   real scope / bode panel screenshots
```

## Why

Multisim exposes no public "place a part, draw a wire" API — only a COM automation interface that has to run inside a 32-bit process. Doing a batch of circuits, or producing reproducible simulation data, means clicking through the GUI by hand.

MultisimForge treats a `.ms14` file as an XML container: it implements the container codec itself, reconstructs the pin-to-net relationships from the drawing geometry itself, and routes wires itself. So **you can generate and validate a circuit without Multisim running at all**. Multisim is only started when real-machine behaviour is needed (schematic image, simulation numbers, instrument screenshots) — and a background worker keeps one instance alive so repeated calls reuse it.

## Features

- **Generate circuits**: SPICE-style netlist plus part coordinates/angles → `.ms14`. Automatic routing (obstacle-aware orthogonal A*, tree connection), multiple ground symbols, instrument panel presets, interactive simulation stop time.
- **Explicit wiring**: parts accept `mirror-x` / `mirror-y`; `wires` lets you pin the exact polyline of any net (junctions are created automatically), and `colors` / `wire_width` control wire colour and thickness.
- **Offline self-check**: reconstructs pin groups from wire and junction geometry and compares them against the electrical nets; also reports overlapping parts, wires crossing parts, crossings between different nets, and parts outside the sheet. No Multisim involved, so it cannot crash.
- **Any part**: part templates are harvested whole from a `.ms14` (electrical part + ports + model + symbol + panel state). Simulation uses the part's own model.
- **Real simulation data**: injects voltage probes on the nets you ask for, then drives DC operating point, AC sweep and transient analysis through COM — resonance peak, −3 dB frequency, phase, peak-to-peak, period. Arbitrary SPICE text can also be handed to Multisim's command-line engine.
- **Real screenshots**: opens Multisim, runs the simulation, waits for the waveform to settle, then grabs the circuit window, each instrument panel and a labelled `PrintWindow` montage (or the full screen with `--desktop-screen`). If the scope trace does not fill the screen, the simulation time is recalibrated and the run repeats.
- **Reused COM instance**: `preview` / `sim` / `parts fetch` share one hidden Multisim inside a background 32-bit worker — roughly 0.3 s for the second call, with no repeated launch/kill cycle. `multisimforge com status` / `com stop` inspect and release it.
- **Read existing designs**: `inspect` reports parts, nets and instrument settings.
- **`.ms14` codec in pure Python**: decoded XML matches the vendor decoder, and the XML layer round-trips byte for byte.

## Requirements

- Windows 10/11
- NI Multisim 14.x (licensed)
- Python 3.10+
- `pywin32`, `Pillow`, `numpy`, `scipy` for screenshots
- a 32-bit Python with `pywin32` — Multisim's automation interface is an in-process 32-bit component, and `parts dbindex` reads `MSCOMP_S.PRD` natively via pure-Python Jet3/4 decryption + 32-bit ADODB (Node.js + `electronics-workbench-decoder` in `tools/dbindex.js` is an optional fallback only)

Local paths are detected at runtime (registry + install directories). Override them with a `config.json` in the repo root or with `MULTISIMFORGE_<KEY>` environment variables.

## Install

```powershell
git clone <this repo>
cd MultisimForge
python -m pip install -e .[capture]

MultisimForge parts init     # build the part library from the local Multisim samples (~30 s)
```

`parts init` harvests part templates from the samples shipped with your Multisim install and writes them to `%LOCALAPPDATA%\multisimforge\parts`. That directory is machine-local and is not distributed.

## Quick start

`examples/rc_lowpass.json`:

```json
{
  "netlist": "V1 vin 0 DC 0 AC 1 SIN(0 1 1k)\nR1 vin vout 1k\nC1 vout 0 0.1u\nXSC1 vin vout 0 nc1 0 nc2 OSCILLOSCOPE\n",
  "output": "out/rc.ms14",
  "layout": {"V1": [108, 216], "R1": [252, 180], "C1": [396, 252, 90], "XSC1": [540, 126],
             "GND": [[108, 306], [396, 342], [612, 198]]},
  "instruments": {"XSC1": {"timebase": "500us", "a_scale": "1V", "b_scale": "1V", "trigger": "none"}},
  "sim_time": "5ms"
}
```

```powershell
MultisimForge build examples/rc_lowpass.json --preview out/rc.png   # generate + self-check + preview
MultisimForge sim out/rc.ms14 --ac 10,100k --out vout --in vin      # cutoff frequency, phase
MultisimForge capture out/rc.ms14 shots                             # real screenshots (do not touch mouse/keyboard)
```

`capture` works on a copy under `work/capture/<run_id>/` and opens its own window,
so the input `.ms14` is byte-identical before and after. A window title carries only
the file stem, so close any other window showing the same file name first: capture
refuses to guess which file such a window holds.

Every command prints one JSON envelope with a `status` and the matching `exit_code`:

```json
{"problems": [], "warnings": [], "crossings": 0, "nets": {"vin": ["R1.1", "V1.1", "XSC1.1"]},
 "status": "PASS", "exit_code": 0}
```

| status | exit code | meaning |
|---|---|---|
| `PASS` | 0 | every mandatory check passed |
| `WARN` | 0 | usable, with a declared caveat (layout advice, degraded routing you allowed) |
| `FAIL` | 2 | the design is wrong: `problems` says what |
| `ERROR` | 1 | the tool or the environment failed |
| `CANCELLED` | 130 | interrupted |

A non-empty `problems` means the drawing disagrees with the netlist and must be fixed; `warnings` are layout advice (too many crossings, detours).

### Routing

The router looks for a clean path before it reports a problem. It retries the
pin escape at growing clearances, falls back to a denser A* grid, and routes the
design up to three times with the failing nets moved first (rip-up and retry).

If a net still has no obstacle-free path, `build` tries to repair the layout:
it moves one part or one ground symbol by half a grid step (up to 48 tries, 3 s)
and keeps the cleanest result. The move is reported, so the run is `WARN`:

```json
{"status": "WARN", "moved_parts": {"GND[1]": [0, 24]},
 "warnings": ["layout auto-repaired: moved GND[1] by (+0,+24) so the router could pass"]}
```

If no arrangement routes cleanly, `build` fails with `RouteError` and writes
nothing — it will not quietly draw a wire through a component. Two escape hatches:

```powershell
MultisimForge build design.json --no-auto-repair      # fail on the spec exactly as written
MultisimForge build design.json --allow-fallback      # status WARN, degraded: true
```

## Explicit wiring

The third element of a `layout` entry may be `"mirror-x"` / `"mirror-y"`; mirroring reverses pin order while labels stay horizontal:

```json
"layout": {"XFG3": [45, 90, "mirror-x"], "XSC3": [183, 108], "GND": [[147, 171]]},
"wires": {
  "out": [[[63, 90], [63, 144], [135, 144], [135, 126]]],
  "0": [[[27, 90], [27, 171], [144, 171]], [[144, 126], [144, 171]]]
},
"colors": {"out": "red", "0": "red"},
"wire_width": 2
```

Every segment must be horizontal or vertical, and polyline endpoints must land on a pin centre or coincide with another polyline (a junction is created at shared points). A net with `wires` follows those polylines exactly; other nets keep using the router. See `examples/explicit_wiring.json`.

## Commands

| Command | Purpose |
|---|---|
| `parts list [keyword]` | search the part library (name, family, pin order) |
| `parts show <model>` | pin order, hidden power pins, pin offsets and directions at 0° |
| `parts fetch <model>` | pull a model out of the Multisim database when it is not in the library |
| `parts harvest <file-or-dir>` | harvest parts from existing `.ms14` files |
| `parts init` | rebuild the whole part library |
| `build spec.json [--preview x.png]` | generate + self-check (optionally export a preview) |
| `preview x.ms14 x.png` | export the schematic through Multisim |
| `check x.ms14` | offline self-check |
| `inspect x.ms14` | report parts, nets, instrument settings |
| `sections x.ms14 <ref>` | read-only native `EnumSections` query for a package |
| `sim x.ms14 --nets a,b --op / --ac ... / --tran ...` | real simulation data |
| `cursors file.csv --column c --x1 a --x2 b` | numerical cursor interpolation from simulator CSV |
| `spice x.cir --cmd "tran 1u 10m"` | hand SPICE text to Multisim's engine |
| `capture x.ms14 outdir` | real screenshots: circuit, each instrument panel, labelled montage (`--desktop-screen` for full screen) |
| `set-sim-time x.ms14 20ms` | change the interactive simulation stop time |
| `com status / stop` | inspect or release the background COM worker |
| `decode / encode` | `.ms14` ↔ XML (debugging) |

### Flags

| Command | Flag | Meaning |
|---|---|---|
| `build` | `--preview <png>` | export a preview image after building |
| | `--allow-fallback` | accept degraded wiring: status `WARN`, `degraded: true` |
| | `--no-auto-repair` | fail on the spec exactly as written; do not move parts |
| `check` | — | reads a `.ms14` or a decoded `.xml`; no flags |
| `sim` | `--nets a,b` | nets to probe (required for meaningful data) |
| | `--op` / `--ac start,stop,points` / `--tran step,stop` | analyses to run (any combination) |
| | `--out` / `--in` | AC output node / AC input source |
| | `--at t1,t2,...` | time points to sample from a transient run |
| | `--csv <dir>` | also write each analysis to CSV |
| `cursors` | `--column / --x1 / --x2 / --axis` | column name, bounds (supports units), and `auto`/`linear`/`log` axis |
| `spice` | `--cmd "tran 1u 10m"` | SPICE command handed to Multisim's engine |
| `capture` | `--wait <s>` | how long the display must be still to call it finished (default 1.5) |
| | `--timeout <s>` | maximum wait (default 90) |
| | `--cur1 <val>` | move scope T1 cursor (time) or Bode cursor (frequency) in background via Win32 messages and verify live readout |
| | `--cur2 <val>` | move scope T2 cursor in background via Win32 messages and verify live readout |
| | `--keep-open` | leave the window open after capture |
| | `--desktop-screen` | opt into full-screen capture requiring foreground (default: PrintWindow montage) |
| | `--on-netlist-error continue` | continue past the netlist-error dialog (default: abort) |
| any | `--debug` | show the traceback instead of a JSON envelope on an unexpected error |

Durations must be greater than 0 and at most 3600 seconds.

## MCP server

`mcp_adapter/server.py` is a stdio MCP server exposing the CLI as tools (`runtime_status`, `check_design`, `inspect_design`, `list_sections`, `cursor_readings`, `build_schematic`, `capture_instruments`, `simulate`, `preview_design`, `set_sim_time`, `convert_design`, `parts`, `run_cli`).

It is deliberately a **thin, process-isolated shell**: every tool shells out to `python -m multisimforge <cmd>` and returns the JSON the CLI prints. No Multisim COM object ever lives in the server process, so a COM crash or a failed screenshot run cannot take the server down.

```powershell
python -m pip install -e .[mcp]
python mcp_adapter/server.py          # stdio; let your MCP client launch it
```

`mcp_adapter/harness.py` is an offline debug client: hand it a JSON call list, it launches the server, runs the calls and writes the results to JSON.

## Layout

```text
multisimforge/
  codec.py        .ms14 container codec (PKWare DCL implode/explode, pure Python)
  xmlio.py        XML read/write (attribute newlines preserved, &ASC encoding, ID remapping)
  parts.py        part template library: harvest, index, lookup
  structural.py   extract structural templates from a blank design (shell, wire, junction, probe, sim state)
  netlist.py      netlist parser
  builder.py      spec → .ms14 (placement, rotation/mirror, labels, routing, instrument state, sheet size)
  router.py       pin escape + obstacle-aware orthogonal A* + tree connection
  check.py        offline self-check: geometry → electrical nets, plus layout checks
  sim.py          real simulation (probe injection, OP/AC/TRAN, result statistics)
  measure.py      data-driven numerical cursor interpolation on simulator CSVs
  capture.py      real screenshots (PrintWindow, F5/F7 key posting, waveform coverage)
  imaging.py      labelled PrintWindow contact-sheet montage builder
  com.py          COM worker (one reused Multisim instance)
  access.py       MCP path confinement & read-only escape-hatch policy
  config.py       local path detection
mcp_adapter/      stdio MCP server + debug client
tools/dbindex.js  read the Multisim master component database index (optional)
```

## Tests

```powershell
python -m pip install -e .[dev]
python -m pytest -q
```

The offline suite covers the container codec, XML round-trips, the netlist parser, generation and connectivity of the three examples, and argument validation for mirroring and explicit wiring. CI runs on Windows and Linux (Linux runs only the parts that do not need Multisim).

## Readouts and screenshots without foreground access

Multisim 14.3 discards saved scope/Bode cursor positions on open. This is a vendor
GUI limitation, **not** an excuse to fabricate readings. Use `sim --csv data/`
and then measure two numerical cursors from the **real** simulator CSV:

```powershell
multisimforge sim out/rc.ms14 --nets vin,vout --tran 10m --csv work/data
multisimforge cursors work/data/tran.csv --column vout --x1 1ms --x2 3ms
multisimforge sim out/rc.ms14 --ac 10,100k --out vout --in vin --csv work/ac
multisimforge cursors work/ac/ac.csv --column vout_mag --x1 1k --x2 10k
```

Numerical cursors interpolate inside the measured interval (linear in time, log
in frequency); phase columns are unwrapped first. Returned results carry
`not_gui_cursors: true`, raw CSV path, cursor coordinates and differences.

`capture` now saves an explicitly labelled `*_montage.png` contact sheet
assembled from real `PrintWindow` bitmaps plus each original instrument image.
It needs no explicit foreground-grab step, and is **not a desktop screenshot**. Launching Multisim itself may still activate its window on some Windows desktops. If a
genuine desktop view is needed, opt in with `capture ... --desktop-screen`; that
specific option still requires foreground and should be run unattended.

### MCP write safety

Agent-side MCP writes are restricted to `work/` by default; to use a different
workspace, set `MULTISIMFORGE_MCP_OUTPUT_ROOT` to its directory. This applies
to build/preview/capture/CSV/convert destinations and in-place `set_sim_time`.
The `run_cli` escape hatch is read-only (`check`, `inspect`, `cursors`).
Mutating part-library actions require the explicit
`MULTISIMFORGE_MCP_ALLOW_LIBRARY_WRITES=1` environment setting. The direct
local CLI remains available for trusted manual maintenance.

## Native acceptance

The public CI matrix is offline and does not contain licensed Multisim. On an
authorised Windows worker with an interactive desktop, run
`python scripts/verify.py --real`. Native acceptance now checks real AC data,
the COM worker, live circuit/instrument images, the PrintWindow montage, and
the preservation of the original circuit file's SHA-256. The manually triggered
`native` GitHub job runs the same gate. A skipped native job is **not** a pass.

## Known limitations

- **Multisim 14.3 still resets saved GUI cursor positions on file reopen.** Use
  `sim --csv` + `cursors` for pure numerical CSV readings (`not_gui_cursors: true`),
  or pass `capture ... --cur1 <val> [--cur2 <val>]` to move live Scope T1/T2 and
  Bode frequency cursors in the background via Win32 messages with live UI readout
  verification (`verified_by_ui_readout: true`).
- **Multi-section parts (`LM324AJ`, `MMBD7000LT1`, `7404N`):** supported via
  `U1.A`/`U1.B` (or `U1B`) syntax sharing one physical package, with automatic
  shared `COM` pin inheritance (`VS+`/`VS-`, `K/A`) and `db_sections.json` per-section
  specialization; `multisimforge sections design.ms14 U1` (or MCP `list_sections`)
  queries placed sections natively via `EnumSections`.
- **Dense routing:** multi-pass A* (with up to 54-unit pin-escape escalation and 4-pass
  rip-up-and-retry) and failing-net-prioritized layout repair are best-effort; some
  layouts have no clean route under the supplied positions. Use explicit `wires` or
  adjust the layout. The build fails instead of silently drawing through parts.
- **Multisim `ReplaceComponent` in unsupported categories (`Basic/3D_VIRTUAL`) can crash with 0xC0000374.**
  This fault originates inside the vendor process and cannot be fixed from Python.
  `parts fetch` uses the single-section `2N3904` carrier, stages in ASCII paths,
  cleans up orphaned daemon processes, and retries once safely; use `parts harvest`
  for templates in unsupported 3D/virtual categories.
- **Unmodified full-desktop screenshots need foreground.** Default `capture` launches
  non-activating (`SW_SHOWNOACTIVATE`) with `PrintWindow` raw panel images and a
  labelled composite; `--desktop-screen` is the opt-in full-desktop capture.
- **Real-native integration and GUI lifecycle remain Windows-only.** Linux can
  run the offline codec/netlist/checker/router suites, but requires licensed
  Windows NI Multisim 14.x for COM simulation and panel capture.

## License

MIT, see `LICENSE`. Third-party components and notices are listed in `THIRD_PARTY_NOTICES.md`.