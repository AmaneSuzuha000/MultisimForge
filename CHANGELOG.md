# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.3] - 2026-10-30

Correctness release, driven by a line-by-line re-audit of 0.1.2. Every entry below
was reproduced against the 0.1.2 source before it was fixed.

### Fixed

- **A value with trailing garbage was silently accepted.** `parse_value` matched a
  number anywhere in the string, so `1k5junk` became `1.5k`; 0.1.1's changelog
  claimed this was fixed, and it was not. Parsing now anchors at both ends and
  validates the unit suffix. RKM notation (`1k2`, `4u7`) is supported, and
  `1mil` is rejected rather than guessed at.
- **A repaired build could describe a file that no longer existed.** Each repair
  candidate was written to the same path, so a later candidate overwrote the file
  the returned result pointed at, and `sha256` matched neither. Each candidate now
  builds into its own staging file; only the winner is moved into place, and the
  result carries the SHA-256 of the file that is actually on disk.
- **A slow COM job could be executed twice.** The client gave every request a
  10-second ceiling and replayed it in a one-shot process on timeout, so an
  analysis that legitimately ran longer than 10 s ran again. Connecting is now
  probed with its own short timeout, the job itself gets the caller's timeout, and
  a job that timed out after being accepted is reported as an error instead of
  being retried.
- **A simulation with no data reported success.** `sim` and `spice` returned
  `PASS` unconditionally, even when a requested analysis produced nothing. Missing
  output is now a failure, an absent result file is a failure, and unconsumed
  warnings downgrade to `WARN` only.
- **`capture` could photograph a different file with the same name.** The window
  was chosen by title, which carries only the file stem; a user file called
  `rc.ms14` could be captured instead of the working copy. The window is now
  matched through the automation interface's own open path, the run refuses when
  no window shows the requested file, and a failed run always closes the window it
  opened.
- **`check` had blind spots.** It did not notice two components sharing a reference
  designator, a wire attached to nothing, or two nets drawn on top of each other
  (a short circuit, which the crossing rule deliberately ignored). All three are
  reported now.
- **A corrupt `.ms14` crashed with `struct.error`.** Decoding trusted the length
  fields a file declares about itself; a truncated or hostile file could ask for an
  unbounded allocation. Header, section, and total lengths are validated and
  capped, and reading a non-circuit file raises a described `ValueError`.
- **Unexpected exceptions broke the one-envelope rule.** `check notes.txt` printed
  a Python traceback instead of a JSON envelope. Unrecognised errors are now
  reported as `ERROR` (pass `--debug` to see the traceback).
- **The MCP adapter diagnosed a different configuration than the CLI used.** It
  read `config.json` directly, ignoring auto-detection and environment overrides.
  It now asks the CLI for its resolved configuration, and the `run_cli` escape
  hatch refuses the two subcommands that can destroy state (`com`, `parts`).
- **Bad arguments were accepted.** `--wait` / `--timeout` reject non-positive and
  absurd durations, a spec that is not one UTF-8 JSON object is rejected, and
  unknown spec fields are named instead of silently ignored.

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
  with no wire at all.
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