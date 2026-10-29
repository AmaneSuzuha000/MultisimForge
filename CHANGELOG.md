# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
