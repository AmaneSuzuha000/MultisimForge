# Native Multisim 14.3 validation: remaining known limitations

This is an **experimental protocol**, not a claim that missing vendor capabilities have
been implemented. Offline CI cannot establish behaviour of licensed NI Multisim.

## Safe environment and evidence

Run only on an authorised Windows 10/11 machine in an interactive desktop session
with Multisim 14.3 and a disposable test workspace. Close unrelated Multisim
sessions and back up every user-authored original project. Record executable,
Multisim version, system DPI, component library, test timestamp, commit SHA,
each input .ms14 SHA-256, output SHA-256, invocation, exit code, and raw logs.

Before native work:

```powershell
python -m pip install -e ".[dev,capture,mcp]"
python -m pytest -q
python scripts/verify.py --real
```

The native verifier must complete, including actual RC AC data, instrument panel
images, labelled PrintWindow montage, and unmodified input hash. If it cannot,
stop and file the first reproducible failure. Never turn a skipped test into PASS.

## 1. Physical scope and Bode cursors

**Established limitation:** the saved design stores cursor positions, but Multisim
14.3 resets them to zero when reopening a design. Neither serialised cursor
attributes nor the known RestoreData flag restored them in prior experiments.

**Working alternative:** `sim --csv` followed by `cursors` reports readings
interpolated from *real* simulator data, clearly marked `not_gui_cursors`.

**Native experiments before any UI-control attempt:**
1. Make an original scope and Bode example with visible T1/T2 and F1/F2 positions.
2. Save, close, reopen, and inspect both the GUI and the stored XML fields.
3. Move cursors using a human operator; inspect Windows UI Automation accessible
   objects and messages for those widgets. Avoid undocumented writes to original
   projects or arbitrary address injection.
4. If automation is possible, write a separate opt-in GUI adapter with explicit
   position verification after each action; otherwise retain numerical cursors.

**Gate:** never label a virtual/CSV cursor reading as a screenshot of a physical
NI GUI cursor. Raw waveform samples must remain accessible.

## 2. Multi-section packages (LM324, 74LS00)

**Current limit:** harvested template carries section metadata, but the builder
places only section A. Another instance is not automatically another section of
the same physical package. This is now surfaced as a build warning.

**Native evidence required for implementation:**
1. In Multisim, create one *package* with two sections (U1A/U1B), then all sections
   (A-D for LM324). Save both and collect original + decoded XML.
2. Compare `CiComponent`, `CiPort`, `CIITSymbolComp` / `IRSection`,
   `CIRToInfoMapItem`, pin IDs, hidden VCC/GND, shared package identity, SPICE
   model records, and per-section local names.
3. Construct a fixture with physically shared supply pins and separate section
   I/O. Run the same AC and transient validation on Multisim-authored and
   generated circuits; prove port-to-net equality and numerical agreement.
4. Repeat with a digital multi-section part where shared power pins are hidden.
5. Implement section-aware package creation only after those fixtures demonstrate
   the exact native serialisation rules; otherwise reject requested non-A sections.

**Gate:** no PASS for a multi-section package unless all requested sections
and hidden supply nets match the original native package.

## 3. ReplaceComponent process heap corruption

**Observed vendor problem:** some `ReplaceComponent` operations can terminate
the Multisim process with 0xC0000374. A Python exception handler cannot prevent
a native heap corruption inside NI's process.

**Mitigation:** all fetch outputs are isolated by run ID, stale outputs are
rejected, and known crash retry is limited. Prefer locally harvested, licensed
templates over repeated remote COM replacement.

**Native experiments:**
1. On disposable carriers, test single fresh-session replace, then repeated
   same-session replace using a frozen model list.
2. Record PID, stdout/stderr, exit codes, working-file checksums, and restart
   success/failure. Do not reuse or kill the user's pre-existing Multisim.
3. Compare fresh-session success rate against reuse, report confidence intervals,
   and decide whether `parts fetch` must always isolate the risky call in a
   single-use process.
4. On an ambiguous disconnect, do **not** replay: a partially completed operation
   may already have changed a file. Require explicit recovery inspection.

**Gate:** zero unsafe side-effects on pre-existing sessions and no stale-file PASS.

## 4. A* layout and dense routing

Obstacle-aware routing has no universal clean-path guarantee for an arbitrary
fixed geometry. Some dense layouts are physically impossible without moving
parts or crossing nets.

**Validation matrix:** open-space, constrained corridor, multiple-net crossing,
pin escape blocked, same-net branches, 50-200 components, mirrored/rotated
parts, explicit wire paths, and a non-routable adversarial fixture.

Measure: valid connections, hard errors, overlap count, crossings, wire length,
number of bends, execution time, peak memory, and nondeterministic variants.

**Gate:** every reported PASS must have zero electrical problems; non-routable
cases must FAIL rather than drawing through components. Optimise wire aesthetics
only subject to that constraint.

## 5. Capture focus and exact provenance

Default capture now supplies independent real PrintWindow panel/circuit bitmaps
and a **labelled composite**. It no longer requires a desktop screenshot.
An opt-in `--desktop-screen` still needs foreground.

**Native experiment:** monitor `GetForegroundWindow` before/during/after both
modes, use partial occlusion tests, handle high-DPI and secondary monitors, and
verify a netlist error during the screenshot closes only the owned process.
Launching Multisim may activate it independently of the screenshot code.

**Gate:** the contact sheet must be labelled composite, never misrepresented as
a raw desktop capture. Preserve raw panels; verify their pixel content against
the visible instrument state.

## Release rule

Public CI green is **necessary but insufficient**. Keep this PR in draft until
the real Windows/Multisim job is run and its evidence attached. A vendor
behaviour that cannot be controlled must remain a stated limitation with
a documented safe alternative, rather than a false "fixed" claim.
