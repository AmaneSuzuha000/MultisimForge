---
name: multisimforge
description: Control a local NI Multisim 14 install from the command line - build editable .ms14 circuits from a netlist plus coordinates (auto-routing, part rotation/mirroring, explicit wire polylines, instrument panel presets), export a schematic preview and self-check the wiring, get DC operating point / AC sweep / transient numbers from Multisim's own simulation engine, open Multisim and capture real oscilloscope and bode plotter panels, pull any part (555, 74-series, op-amps, transistors) from the Multisim database, and read the parts and nets of an existing .ms14. Use this skill whenever a task involves Multisim, .ms14 files, circuit simulation screenshots, simulation data, scope or bode panel images, drawing a circuit in Multisim, or verifying one - even if the user never names the tool.
---

# MultisimForge

Control a local Multisim 14.3 install from the command line. The Python package and this document live in the same directory. From any directory, call it as below; every command prints JSON:

```
python -m multisimforge <command> ...
```

(If the Python Scripts directory is on PATH you can also write `multisimforge <command>`.)

## Hard rules

- **Screenshots must come from a real Multisim window** (`capture`, or the user's own screenshot). If capturing fails, explain why and ask the user to capture manually - never fake an instrument panel or a schematic with drawing code.
- **Numbers must come from a real simulation**: `sim` uses Multisim's own netlist and device models, so its output can be quoted directly. Never pass an estimate off as a simulated value.
- By default `capture` produces raw `PrintWindow` bitmaps plus a labelled `*_montage.png` composite without taking desktop focus; pass `--desktop-screen` only when an unmodified desktop screenshot is needed (that option borrows the foreground for about a minute). `capture` refuses if another Multisim window with the same file stem is already open, and closes only the window it launched.
- Generated `.ms14` file names may only contain letters, digits, underscore and hyphen (instrument presets and probes match on the file name). Directories may contain any characters.

## Commands

| Command | Purpose |
|---|---|
| `parts list [keyword]` | search the part library (name, family, pin order) |
| `parts show <model>` | pin order, hidden power pins, pin offsets and directions at 0° (for planning layout) |
| `parts fetch <model>` | pull a model from the Multisim database when the library lacks it (a few seconds), e.g. `LM555CN`, `74LS00N`, `UA741CD` |
| `parts harvest <.ms14 or dir>` | harvest parts from the user's own designs |
| `build spec.json [--preview x.png]` | generate the .ms14 and self-check it; `--preview` also exports a preview image |
| `preview x.ms14 x.png` | export the schematic through Multisim (a few seconds) |
| `check x.ms14` | self-check: wiring vs nets, overlapping parts, wires through parts, crossing count |
| `inspect x.ms14` | report parts, nets and instrument settings (works on the user's own designs) |
| `sections x.ms14 <ref>` | read-only native `EnumSections` query for a package refdes (e.g. `U1`) |
| `sim x.ms14 --nets a,b --op / --ac 10,100k[,points] --out b --in a --at 1k / --tran 10m[,rate] [--csv dir]` | simulation data from Multisim |
| `cursors file.csv --column col --x1 a --x2 b` | interpolate numerical cursors from simulator CSV (never modifies GUI cursors) |
| `spice x.cir --cmd "tran 1u 10m"` | hand arbitrary SPICE text to Multisim's command-line engine |
| `capture x.ms14 outdir [--cur1 v --cur2 v] [--desktop-screen]` | real screenshots: `<stem>_circuit.png`, `<stem>_XSC1.png` panels, `<stem>_montage.png` (and optional background `--cur1`/`--cur2` cursor placement) |
| `set-sim-time x.ms14 20ms` | change the interactive simulation stop time |
| `com status / stop` | inspect or release the background COM worker |
| `decode / encode` | .ms14 ↔ XML (debugging) |

## Workflow

1. **Confirm the parts**: look up every model in the netlist with `parts list` / `parts show`; `parts fetch` anything missing. Read the pin order and `pin_xy` to decide the netlist form and the orientation.
2. **Write spec.json** (syntax in `docs/spec.md`): netlist + coordinates/angle per part + ground symbol positions + instrument presets + sim_time.
3. **Generate and look**: `build spec.json --preview x.png`, then open the preview. Non-empty `problems` means the wiring is wrong and must be fixed; `warnings` about overlaps, wires through parts or many crossings mean moving coordinates and rebuilding - usually one or two rounds.
4. **Get numbers**: `sim` gives operating point, gain/phase/−3 dB frequency/resonance peak, transient peak and period. Compare against theory before quoting them.
5. **Capture**: by default `capture` runs via `PrintWindow` in the background without taking desktop focus; if you pass `--desktop-screen`, remind the user not to touch the mouse or keyboard during the run. Check each image: does the trace fill the screen (`scope_coverage` near 1), are the scale settings sensible (trace at least a third of the screen height, no clipping)? If not, change `instruments` or `sim_time`, rebuild and capture again.
6. **Deliver**: state which images are Multisim screenshots and which numbers came from `sim`; surface any `warnings` you did not resolve.

## Known limitations

- Multisim 14.3 still resets saved cursor positions on file reopen; pass `capture ... --cur1 <t1|freq> [--cur2 <t2>]` to position live GUI cursors in background and read their UI-verified readouts, or use `sim --csv` + `cursors` for CSV interpolation.
- Multi-section parts (`U1.A`/`U1.B` for LM324, 7404N, MMBD7000LT1) share one physical package and auto-inherit shared `COM` pins; digital parts still keep VCC/GND on hidden pins unless wired explicitly.
- Routing is automatic A*, so dense circuits can still detour - move coordinates or pass explicit `wires` (endpoints must land on a pin or a wire intersection).
- `parts fetch` reads the master DB directly via pure-Python Jet3/4 decryption + 32-bit ADODB (no Node.js required; Node decoder kept as fallback). Unsupported 3D/virtual categories (`Basic/3D_VIRTUAL`) still fault inside NI's COM process; use `parts harvest` if a category fails.

Part library mechanics and local paths are in `docs/parts.md`.