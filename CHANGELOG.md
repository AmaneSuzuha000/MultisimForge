# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.2] - 2026-10-30

Routing release. A wire with no clean path is now a last resort, not the first
answer: the router escalates, and the builder will nudge a part aside before it
gives up.

### Changed

- The router tries harder before failing a net: it retries the pin escape at
  growing clearances, re-runs A* on a denser grid built from interval
  midpoints, and routes the whole design up to three times with the failing
  nets moved first (rip-up and retry), keeping the pass with the fewest
  failures.
- `build` auto-repairs the layout: when no arrangement of wires fits, it moves
  one part or one ground symbol by half a grid step at a time (up to 48 tries,
  3 s budget), keeps the cleanest result, and reports what it moved in
  `moved_parts` plus a warning line. `--no-auto-repair` restores the strict
  "fail on the spec as written" behaviour.

### Fixed

- A repaired build no longer hides the reason it was repaired: the move is
  listed in `warnings`, so the command exits `WARN` rather than `PASS`.

## [0.1.1] - 2026-10-30

Reliability release. No new user-facing features; the goal is that a failed run
can no longer look like a successful one.

### Changed

- Every command now returns one envelope: `status` (`PASS` / `WARN` / `FAIL` /
  `ERROR` / `CANCELLED`) plus the matching `exit_code` (0 / 0 / 2 / 1 / 130).
  The MCP adapter derives `ok` from the exit code and status, so a design with
  wiring problems is reported as a failure instead of `ok: true`.
- Degraded routing is an error by default: when a net has no obstacle-free path,
  `build` raises `RouteError`, names the failing nets and writes nothing. Pass
  `--allow-fallback` to accept the old behaviour; the result is then `WARN` with
  `degraded: true` and the failing nets listed.
- `check` additionally reports pins that are not attached to any wire and nets
  with no wire at all. Numeric parsing now rejects trailing garbage
  (`1kgarbage` is an error, not 1000).
- `capture` works on a copy under `work/capture/<run_id>/`; the input `.ms14` is
  byte-identical before and after (both hashes are returned). A netlist-error
  dialog aborts the run by default (`--on-netlist-error continue` to override).
- `sim` and `capture` use a per-run working directory, so two concurrent runs of
  the same file name cannot overwrite each other; `build` writes atomically.
- `runtime_status` separates required checks from optional ones and no longer
  imports `winreg` on non-Windows hosts.
- CI runs the offline suite with `-m "not needs_parts"`; circuit-building tests
  skip when no part library is present and run on a self-hosted Windows job.

### Fixed

- MCP `check_design` / `build_schematic` could report success with exit code 2.
- `capture` could rewrite the input design when recalibrating the scope timebase.
- `"py -3-32"` was passed to `subprocess` as a single executable name.
- `server.json` no longer advertises a PyPI package that was never published.

## [0.1.0] - 2026-10-29

First public release.

### Added

- `.ms14` container codec in pure Python (PKWare DCL implode/explode); the
  XML layer round-trips byte for byte against the vendor decoder.
- Netlist parser (SPICE-style devices, library parts by position or by pin
  name, instruments) and a JSON spec format for layout and instrument presets.
- Schematic generator: part placement, rotation, mirroring, labels, sheet
  sizing, instrument panel presets and interactive simulation stop time.
- Obstacle-aware orthogonal A* router with pin escape, tree connection and
  junction synthesis, plus explicit `wires` polylines per net.
- Offline self-check: wire/junction geometry is reconstructed into pin groups
  and compared against the electrical nets; overlapping parts, wires crossing
  parts, cross-net crossings and off-sheet parts are reported.
- Part template library harvested from `.ms14` files, with `parts fetch` to
  pull a model out of the Multisim master database.
- Real simulation data through COM: DC operating point, AC sweep (peak,
  −3 dB points, phase), transient (min/max/pp/mean/RMS/period/frequency), CSV
  export, and arbitrary SPICE text through Multisim's command-line engine.
- Real screenshots: circuit window, every instrument panel and the full screen,
  captured with PrintWindow; scope trace coverage is measured and the
  simulation time is recalibrated when the trace does not fill the screen.
- Background COM worker that keeps one hidden Multisim instance alive and
  serves requests over a loopback socket, so consecutive commands reuse it
  (`multisimforge com status` / `com stop`).
- stdio MCP server (`mcp_adapter/server.py`) exposing the CLI as tools, plus a
  batch debug client (`mcp_adapter/harness.py`).
- Runtime detection of local paths (registry, install directories, 32-bit
  interpreter) with `config.json` and `MULTISIMFORGE_*` overrides.
- Offline test suite and GitHub Actions workflow.

### Known limitations

- Scope and bode cursors cannot be preset from the file format.
- Multi-section parts use section A per instance.
- Routing is heuristic; dense designs may need coordinate tuning.