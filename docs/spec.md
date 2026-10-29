# spec.json reference

```json
{
  "netlist": "* RC step response\nV1 vin 0 PULSE(0 2 0 1u 1u 1m 2m)\nR1 vin vout 1k\nC1 vout 0 0.1u\nXSC1 vin vout 0 nc1 0 nc2 OSCILLOSCOPE\n",
  "output": "out/rc_step.ms14",
  "layout": {"V1": [100, 260], "R1": [250, 180], "C1": [400, 260, 90],
             "XSC1": [560, 140], "GND": [[100, 350], [400, 350], [610, 230]]},
  "instruments": {"XSC1": {"timebase": "500us", "a_scale": "1V", "b_scale": "1V", "trigger": "none"}},
  "sim_time": "100ms"
}
```

| Field | Meaning |
|---|---|
| netlist | netlist text, syntax below |
| output | output `.ms14`; the file name may only contain letters, digits, underscore and hyphen (instrument presets and probes match on the file name) |
| layout | `{"REF": [x, y]} or [x, y, angle] or [x, y, angle, "mirror-x"], "GND": [[x, y], ...]}` |
| instruments | instrument panel presets, see below |
| sim_time | interactive simulation stop time; defaults to 20 screens when a scope is present |
| net_names | whether to show net name labels, default false |
| colors | wire colours `{"vout": "blue"}` (red/blue/green/magenta/orange/black); scope channel B is blue by default and trace colour follows the wire |
| wire_width | wire thickness (integer, default 1) |
| wires | explicit polylines `{"net": [[[x,y], [x,y], ...], ...]}`; a net listed here ignores the router, segments must be horizontal/vertical and endpoints must land on a pin or coincide with another polyline |
| sheet | sheet size `[width, height]` in inches; defaults to a tight fit around the circuit so F7 fills the window |

## Netlist

| Part | Syntax | Pin order / notes |
|---|---|---|
| resistor / capacitor / inductor | `R1 a b 1k`, `C1 a b 0.1u`, `L1 a b 10m` | values accept k/m/u/n/p/meg; append a library part name to pick another model (e.g. electrolytic `CAP_ELECTROLIT_VIRTUAL`) |
| DC voltage source | `V1 vcc 0 DC 12` | first node is the positive terminal |
| sine source | `V1 vin 0 DC 0 AC 1 SIN(0 <peak> <freq>)` | `AC 1` is required for AC sweeps and bode plots |
| pulse source | `V1 vin 0 PULSE(<low> <high> <delay> <rise> <fall> <width> <period>)` | `AC 1` may be added |
| DC current source | `I1 a b DC 1m` | |
| diode | `D1 a k 1N4148` | anode, cathode |
| BJT | `Q1 c b e 2N3904` | collector, base, emitter |
| MOSFET | `M1 d g s b <model>` | |
| library part, positional | `XU1 inp inn vp vn out LM324AJ` | in `parts show` pin order; the leading X is dropped (shown as U1A) |
| library part, named pins | `U1 LM555CN GND=0 TRI=thr OUT=out RST=vcc CON=con THR=thr DIS=dis VCC=vcc` | use this when a part has many pins; hidden power pins can be rewired the same way |
| oscilloscope | `XSC1 <A+> <B+> <A-> <T+> <B-> <T-> OSCILLOSCOPE` | tie A- and B- to 0; give unused external-trigger pins a unique net name (nc1, nc2) |
| function generator | `XFG1 <+> <COM> <-> FGEN WAVE=SQUARE FREQ=1k AMPLITUDE=2 OFFSET=0 DUTY=50` | WAVE: SINE/TRIANGLE/SQUARE; AMPLITUDE is the peak value; COM goes to ground |
| bode plotter | `XBP1 <IN+> <IN-> <OUT+> <OUT-> BODE` | tie IN- and OUT- to 0; the circuit needs an AC source |
| multimeter | `XMM1 <+> <-> MULTIMETER` | |

Comment lines start with `*`, a leading `+` continues the previous line, and dot commands such as `.end` are ignored. Net names are case-insensitive; `0` and `gnd` are ground. A grounded circuit must provide `"GND"` positions in `layout`.

## Layout

Coordinates are about 1/96 inch and the Multisim grid is 9. What you specify is the **centre of the pin bounding box**; the generator snaps it to the grid.

- **Angles are clockwise on screen.** For two-terminal parts (R, C, L, D): 0° is horizontal with the first node on the **left**, 90° vertical with the first node on **top**, 180° on the right, 270° at the bottom. A part shunting to ground is always 90 (signal on top, ground at the bottom).
- Voltage and current sources are vertical by nature; at 0° the positive terminal (first node) is on top, so they usually need no rotation.
- For multi-pin parts run `parts show` first and read `pin_xy`: for LM555CN, VCC is 81 above, GND 81 below, OUT on the right, the rest on the left; op-amp non-inverting/inverting inputs are on the left and the output on the right.
- Signal flows left to right; keep at least 120 between adjacent pin centres to leave routing channels. Stay at least 100 from the left and top sheet edges.
- Scope and bode plotter pins are on the bottom of the symbol, the body extends about 60 upwards (name above that): keep centre y ≥ 120 and put them in the upper right of the circuit, with short A/B leads. Body extents are in `parts show` under `body`.
- Put a ground symbol about 90 below each grounded pin so ground wires stay short; multiple ground symbols connect to their nearest pin automatically.
- For straight wires, align connected pins on the same x or y (e.g. series parts on one y, a vertical shunt part sharing x with the node above it).
- After generating, read the `--preview` image and `warnings`: if wires cross parts, parts overlap, or there are many crossings, move coordinates. `problems` (drawing vs netlist mismatch) must be empty.

### Mirroring

The third element of a layout entry may be `"mirror-x"` (flip left/right) or `"mirror-y"` (flip top/bottom); the full form `[x, y, 0, "mirror-x"]` also works.

Mirroring reverses pin order too. FGEN's pins are `+/COM/-` left to right at 0°, so a horizontal mirror gives `-/COM/+`. Labels stay horizontal and readable.

### Explicit wires

The router is A*: it guarantees same-net, orthogonal, obstacle-free wiring, but not the exact bend positions. To pin down the routing of a net, use `wires`:

```json
"wires": {
  "out": [[[63, 90], [63, 144], [135, 144], [135, 126]]],
  "0": [[[27, 90], [27, 171], [144, 171]], [[144, 126], [144, 171]]]
}
```

Rules:

- Each point `[x, y]` is an absolute sheet coordinate (about 1/96 inch, grid 9).
- Every segment must be horizontal or vertical; diagonals are rejected.
- Each polyline endpoint must land on a pin centre, or coincide with another polyline; shared points become junctions automatically.
- A net listed in `wires` is routed exactly as given; every other net still uses the router.
- One net may have several polylines (tree branches, ground ties).

## Instrument presets

| Scope (XSC) | Meaning | Example |
|---|---|---|
| timebase | time per division (s/div) | `"500us"` |
| a_scale / b_scale | volts per division | `"1V"`, `"10mV"` |
| a_offset / b_offset / x_offset | Y/X offset in divisions | `-2` |
| a_coupling / b_coupling | `dc` / `ac` / `gnd` | `"ac"` |
| trigger | `none` / `auto` / `normal` / `single` | `none` is the most stable for screenshots |
| trigger_source / trigger_edge / trigger_level | `a`/`b`/`ext`, `rising`/`falling`, level in V | |
| show | whether the panel opens when the simulation runs; true by default (false for the function generator) | |

| Bode plotter (XBP) | Meaning | Example |
|---|---|---|
| mode | `magnitude` / `phase` | |
| horizontal / vertical | `log` / `lin` | |
| f_start / f_stop | frequency range | `"10Hz"`, `"100kHz"` |
| db_min / db_max | vertical range, log scale (dB) | `-40`, `5` |
| lin_min / lin_max | vertical range, linear scale (ratio) | `0`, `1` |
| phase_min / phase_max | phase range (degrees) | `-90`, `90` |

To capture magnitude and phase separately, generate two designs (`xx_mag.ms14`, `xx_phase.ms14`, different `mode`) and capture each.

**sim_time**: the interactive simulation stops here and the scope holds the last screen. Use a whole multiple of one screen (10 × timebase) and make it longer than the circuit needs to settle (≥ 5τ, or until a coupling capacitor has charged). If the sweep origin and the screen boundary do not line up, `capture` nudges the time and runs once more.

## sim output

- `--op`: DC voltage of every net to ground.
- `--ac start,stop[,points-per-decade] --out <net> [--in <net>] [--at f1,f2]`: `peak` (maximum gain, i.e. resonance), `cutoff` (frequency where gain falls to 0.707 of the peak, with phase — one for low/high pass, two for band pass), and `at` with gain, dB and phase at the requested frequencies.
- `--tran duration[,rate]`: min/max/peak-to-peak/mean/RMS/period/frequency per net; `--csv` writes every sample.
- If the input net has zero AC amplitude you get a hint: the source needs `AC 1`.
