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

**Vendor `.ms14` persistence limitation (still true):** the saved `.ms14` file carries cursor
attributes, but Multisim 14.3 resets them to zero whenever a design is reopened.

**Resolved measurements & opt-in background GUI cursor control:**
- Numerical `sim --csv` + `cursors` interpolates from real simulator CSV samples
  (`not_gui_cursors: true`).
- For live instrument screenshots (`capture --cur1 <val> [--cur2 <val>]`), `capture.py`
  now moves physical oscilloscope T1/T2 and Bode frequency cursors via background Win32
  canvas messages (`WM_LBUTTONDOWN`/`WM_MOUSEMOVE`/`WM_LBUTTONUP`, `BM_CLICK`), closed-loop
  two-point handle calibration, and live static UI readout verification
  (`verified_by_ui_readout: true`, `method: "win32_background_messages"`).
- Natively verified on Multisim 14.3: Scope `T1=1.5ms`/`T2=3.5ms` settled at
  `1.496 ms` / `3.499 ms` (`T2-T1 = 2.003 ms`) and Bode `1kHz` settled at
  `1 kHz` (`-1.445 dB`) without stealing desktop focus.

## 2. Multi-section packages (`LM324AJ`, `MMBD7000LT1`, `7404N`)

**Resolved and natively verified on Multisim 14.3:**
- `builder.py` and `parts.py` (`section_spec`, `db_sections.json` via ADODB) serialize
  multi-section slots (`U1.A`/`U1.B`/`U1B`) into one physical package (`LocalName="U1"`),
  remapping per-section `CiComponent`, `CiaSpiceTmpltExprt`, `CIITSymbolComp`
  (`IRSection`/`IRSectionID`/`DBComponentSectionName`), `CiPort`/`CIITPinSymbolComp`,
  `CIRToInfoMapItem` (`PinOrderCIR`/`PinOrderIR`/`PinNumbersCIR`/`PinNumbersIR`),
  `RefDesPrefixUsageMap` (`MultisectionUsage`), and `.MODEL`/`.SUBCKT` rename headers
  (including comment-headed cards).
- Shared `COM` pins (e.g. `VS+`/`VS-`, `K/A`) are automatically inherited across sections
  of the same physical package and rejected on conflict.
- Natively verified against `Mixed-signal/FrequencyDivider.ms14` and a generated
  multi-section fixture (`LM324AJ` `U1.A`+`U1.B` + `MMBD7000LT1` `D1.A`+`D1.B` + `7404N`
  `U2.A`+`U2.B`): `check` `problems=[]`, COM `EnumSections` `['A', 'B']`, and COM DC
  operating point agreement (`outa=2.002 V`, `outb=4.007 V` with inherited rails, series
  diode drops `2.000 -> 1.412 -> 0.825 V`).

## 3. ReplaceComponent process heap corruption

**Native benchmark results on frozen target list (disposable library, COM reuse):**
- Single reused COM worker (`PID=13588`) succeeded on **7/7 present database parts**
  (`LM555CN` 6.65s cold, `UA741CD` 0.82s, `2N2222A` 0.69s, `1N4148` 0.75s, `74LS00N`
  0.77s, `IRF540N` 0.69s, `TL082CD` 0.83s; ~0.76s warm reuse).
- Missing catalog names (`CD4011BE`, `NE5532D`) failed cleanly (`0.27s`) leaving the COM
  worker healthy.
- Vendor-faulting category (`Basic/3D_VIRTUAL` `555_Timer`) triggered `0xC0000374` inside
  NI's `ReplaceComponent`; `_replace_with_restart` cleaned up the dead process (`pid=None`),
  retried once safely, rejected partial outputs, and left zero orphaned `multisim.exe`
  processes. This vendor-internal fault in unsupported 3D/virtual categories remains an
  upstream NI limitation; use harvested templates when needed.

## 4. A* layout and dense routing

Validated across the offline routing matrix (`tests/test_routing_matrix.py`: open space,
constrained corridor, multi-net crossing, pin escape escalation up to `54` units,
same-net tree junction branching, 4-pass rip-up-and-retry, 50/100/200 component scale
benchmarks, mirrored/rotated + explicit `wires`, and non-routable sealed enclosure failing
explicitly with zero obstacle crossings or net overlaps). Dense fixed geometries without
physical channel room still intentionally raise `RouteError` (or move parts via
`auto_repair`) rather than drawing through components.

## 5. Capture focus and exact provenance

Default `capture` launches `multisim.exe` with `STARTUPINFO(SW_SHOWNOACTIVATE)` and grabs
real `PrintWindow` panel/circuit bitmaps plus an explicitly labelled `PrintWindow` composite
without stealing `GetForegroundWindow` during scope/Bode capture or background cursor
positioning. Only `--desktop-screen` explicitly opts into foreground activation for a full
desktop grab.

## Release rule

Public CI green is **necessary but insufficient**. Do not merge real-machine
changes as proven until the native Windows/Multisim verification passes and its
evidence is recorded. A vendor behaviour that cannot be controlled must remain
a stated limitation with a documented safe alternative, rather than a false
"fixed" claim.
