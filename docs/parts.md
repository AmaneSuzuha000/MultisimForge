# Part library and local environment

## What the part library is

A part inside a `.ms14` is not one object but several: the electrical component (`CiComponent`, carrying the SPICE template and parameters), its ports (`CiPort`), the model it references (`CiModel`), the schematic symbol (`CIITSymbolComp`), the panel state for virtual instruments, and a RefDes record for multi-section parts. `parts` harvests this object group out of an existing `.ms14` and stores it as a template; generating a circuit copies the group, allocates fresh IDs, sets the reference designator and value, and attaches it to nets. Any part that has ever appeared in a `.ms14` can therefore be used, and simulation runs against the part's own model (LM324AJ saturates, the 555 uses its real macromodel).

The library lives in `%LOCALAPPDATA%\multisimforge\parts\`:

- `index.json` — name → family, pin order, hidden power pins, SPICE template, source
- `<name>.xml` — the part template
- `_struct\` — structural templates extracted from a blank design (drawing shell, wire, external pin, junction, net, net label, voltage probe, simulation settings)
- `db_index.json` — `{part name: [group, family]}` for the Multisim master database (used by `parts fetch`)

## Adding parts

1. **`parts fetch <model>` (preferred)**: look up group and family in `db_index.json`, replace a part in a carrier design via COM `ReplaceComponent`, save, then harvest. The carrier must be compatible with the target or the Multisim process crashes, so the tool tries a transistor carrier (analog: op-amps, 555, diodes, transistors) and a 7404N carrier (TTL/CMOS logic) in turn. Crashes are cleaned up and later calls are unaffected.
2. **`parts harvest <file-or-dir>`**: place the part in Multisim, save, then harvest. Existing names are skipped unless `--force` is given (single files only).
3. **`parts init`**: rebuild the entire library (structural templates plus the ~230 parts found in the samples shipped with Multisim). Needs Multisim and COM.

Use the full database name, e.g. `LM555CN`, `74LS00N`, `UA741CD`, `2N2222A`. If you do not know it, the error from `parts fetch` lists nearby names.

## Pins

- `pins` is the positional order used by the netlist: it follows the `%t<pin name>` order in the SPICE template, matching SPICE convention (diode A K, BJT C B E, LM324AJ 1IN+ 1IN- VS+ VS- 1OUT).
- Instrument ports are numbered; the meaning is annotated in `parts show` (scope 1=A+, 4=A-, 2=B+, 5=B-, 3/6=external trigger).
- `hidden_pins` are power pins that are not drawn (VCC/GND on 74-series parts); they default to the nets of the same name. Use named-pin syntax to rewire, e.g. `VCC=vcc5`.
- Multi-section parts (four op-amps in an LM324, four NAND gates in a 74LS00) use section A per instance; designators show as U1A, U2A, ...

## Local paths (defaults in config.py, override with config.json)

| Key | Default |
|---|---|
| multisim_exe | read from the Multisim automation registration; otherwise the newest 13/14/15 directory under `Program Files\National Instruments\Circuit Design Suite\` |
| samples_dir | `%PUBLIC%\Documents\National Instruments\Circuit Design Suite <version>\samples` |
| master_db | `%PROGRAMDATA%\National Instruments\Circuit Design Suite\<version>\database\MSCOMP_S.PRD` |
| python32 | a 32-bit interpreter with pywin32 (`%LOCALAPPDATA%\Programs\Python\Python3*-32`, `C:\Python3*-32`, `py -3-32`, `%LOCALAPPDATA%\multisimforge\.venv32`) |
| library_dir | `%LOCALAPPDATA%\multisimforge\parts` |
| work_dir | `C:\msim_work` (Multisim's command-line engine rejects paths containing spaces) |
| com_idle_timeout | 900 — seconds the background COM worker stays alive without requests |

Override with a `config.json` in the repo root (same keys), or with an environment variable `MULTISIMFORGE_<KEY>`, e.g. `MULTISIMFORGE_PYTHON32`.

## Internals (for troubleshooting)

- **`.ms14` codec**: `codec.py` is a pure-Python implementation (PKWare DCL implode); the decoded XML matches the vendor decoder and the XML layer round-trips byte for byte. Node is no longer needed.
- **COM**: Multisim 14's automation interface can only be loaded by a 32-bit process. A background worker holds one hidden Multisim instance and serves requests over a loopback socket, so consecutive commands reuse it; `multisimforge com status` / `com stop` inspect and release it, and it exits on its own after `com_idle_timeout`. `ReportNetlist` and incompatible `ReplaceComponent` calls can kill the process — the former is never called, the latter is followed by cleanup of leftover instances.
- **Simulation data**: voltage probes are placed on the nets to measure and the design is saved to `work_dir\probe\` (the file name must match the original), then COM runs DoDCOperatingPoint / DoACSweep / RunSimulation. Probes must sit on a wire endpoint at a part pin; probes on junctions or instrument terminals are dropped by Multisim.
- **Self-check**: `check` does not need Multisim — it groups pins by wire and junction geometry and compares each group against the electrical nets.
- **Screenshots**: `capture` grabs panels and the circuit window with PrintWindow; F5 (run), F7 (zoom) and F6 (pause) are posted as key messages, so no keyboard focus is needed. Only the full-screen grab brings the window to the foreground.
