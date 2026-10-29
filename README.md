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
- **Real screenshots**: opens Multisim, runs the simulation, waits for the waveform to settle, then grabs the circuit window, each instrument panel and the full screen. If the scope trace does not fill the screen, the simulation time is recalibrated and the run repeats.
- **Reused COM instance**: `preview` / `sim` / `parts fetch` share one hidden Multisim inside a background 32-bit worker — roughly 0.3 s for the second call, with no repeated launch/kill cycle. `multisimforge com status` / `com stop` inspect and release it.
- **Read existing designs**: `inspect` reports parts, nets and instrument settings.
- **`.ms14` codec in pure Python**: decoded XML matches the vendor decoder, and the XML layer round-trips byte for byte.

## Requirements

- Windows 10/11
- NI Multisim 14.x (licensed)
- Python 3.10+
- `pywin32`, `Pillow`, `numpy`, `scipy` for screenshots
- a 32-bit Python with `pywin32` — Multisim's automation interface is an in-process 32-bit component
- optional: Node.js + `electronics-workbench-decoder` (only `parts dbindex` needs it)

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

Every command prints JSON:

```json
{"problems": [], "warnings": [], "crossings": 0, "nets": {"vin": ["R1.1", "V1.1", "XSC1.1"]}}
```

A non-empty `problems` means the drawing disagrees with the netlist and must be fixed; `warnings` are layout advice (too many crossings, detours).

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
| `sim x.ms14 --nets a,b --op / --ac ... / --tran ...` | real simulation data |
| `spice x.cir --cmd "tran 1u 10m"` | hand SPICE text to Multisim's engine |
| `capture x.ms14 outdir` | real screenshots: circuit, each instrument panel, full screen |
| `set-sim-time x.ms14 20ms` | change the interactive simulation stop time |
| `com status / stop` | inspect or release the background COM worker |
| `decode / encode` | `.ms14` ↔ XML (debugging) |

## MCP server

`mcp_adapter/server.py` is a stdio MCP server exposing the CLI as tools (`check_design`, `inspect_design`, `build_schematic`, `capture_instruments`, `simulate`, `preview_design`, `set_sim_time`, `convert_design`, `parts`, `run_cli`).

It is deliberately a **thin, process-isolated shell**: every tool shells out to `python -m multisimforge <cmd>` and returns the JSON the CLI prints. No Multisim COM object ever lives in the server process, so a COM crash or a failed screenshot run cannot take the server down.

```powershell
python -m pip install -e .[mcp]
python mcp_adapter/server.py          # stdio; let your MCP client launch it
```

`mcp_adapter/harness.py` is an offline debug client: hand it a JSON call list, it launches the server, runs the calls and writes the results to JSON.

## Layout

```text
MultisimForge/
  codec.py        .ms14 container codec (PKWare DCL implode/explode, pure Python)
  xmlio.py        XML read/write (attribute newlines preserved, &ASC encoding, ID remapping)
  parts.py        part template library: harvest, index, lookup
  structural.py   extract structural templates from a blank design (shell, wire, junction, probe, sim state)
  netlist.py      netlist parser
  builder.py      spec → .ms14 (placement, rotation/mirror, labels, routing, instrument state, sheet size)
  router.py       pin escape + obstacle-aware orthogonal A* + tree connection
  check.py        offline self-check: geometry → electrical nets, plus layout checks
  sim.py          real simulation (probe injection, OP/AC/TRAN, result statistics)
  capture.py      real screenshots (PrintWindow, F5/F7 key posting, waveform coverage)
  com.py          COM worker (one reused Multisim instance)
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

## Known limitations

- Scope and bode cursors cannot be preset, so they sit at the start of the trace in screenshots; take readings from `sim` instead.
- Multi-section parts (LM324, 74LS00, ...) use only section A per instance; digital parts keep VCC/GND on hidden pins.
- Routing is automatic A*, so dense circuits can still detour — move coordinates or switch to `wires`; the `crossings` count is the metric to optimise.
- `parts fetch` drives a real Multisim and replaces a part in a carrier circuit; an incompatible carrier can crash that process (leftovers are cleaned up automatically).
- `capture` brings Multisim to the foreground, so leave the mouse and keyboard alone for about a minute.

## License

MIT, see `LICENSE`. Third-party components and notices are listed in `THIRD_PARTY_NOTICES.md`.
