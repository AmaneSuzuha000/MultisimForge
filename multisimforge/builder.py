"""Build an editable Multisim .ms14 from a circuit spec (spec.json).

spec fields:
    netlist      netlist text (syntax in netlist.py)
    output       output .ms14 path; the file name may only contain letters, digits, underscore and hyphen (instrument presets match on the file name)
    layout       {"R1": [x, y] or [x, y, angle] or [x, y, angle, "mirror-x"], "GND": [[x, y], ...]},
                 coordinates are the part pin centre; angle 0/90/180/270 (clockwise on screen), mirroring is mirror-x/mirror-y
    instruments  instrument panel presets {"XSC1": {"timebase": "500us", ...}}
    sim_time     interactive simulation stop time, e.g. "20ms"; defaults to 20 screens when a scope is present
    net_names    whether to show net name labels, default false
    colors       wire colours {"vout": "blue"}; scope channel B is blue by default
    wires        explicit wire polylines {"vout": [[[x,y], [x,y], ...], ...]}; a net listed here bypasses the router;
                 every endpoint must land on a pin centre or coincide with another polyline, segments must be horizontal/vertical
    sheet        sheet size [width, height] in inches; defaults to a tight fit around the circuit
"""
import math
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from . import netlist as nl
from . import xmlio
from .parts import Library
from .result import new_run_id
from .router import RouteError, route_all

# Multisim colours are 0x00BBGGRR
COLORS = {"red": 255, "blue": 16711680, "green": 32768, "magenta": 16711935, "orange": 33023, "black": 0}
MATRICES = {0: (1, 0, 0, 1), 90: (0, 1, -1, 0), 180: (-1, 0, 0, -1), 270: (0, -1, 1, 0)}
GRID = 9

SCOPE_KEYS = {
    "timebase": "NI_EWB_TIMEBASE_SCALE_VALUE",          # s/div
    "x_offset": "NI_EWB_TIMEBASE_POSITION_VALUE",       # div
    "a_scale": "NI_EWB_A_CHANNEL_SCALE_VALUE",          # V/div
    "b_scale": "NI_EWB_B_CHANNEL_SCALE_VALUE",
    "a_offset": "NI_EWB_A_CHANNEL_POSITION_VALUE",      # div
    "b_offset": "NI_EWB_B_CHANNEL_POSITION_VALUE",
    "trigger_level": "NI_EWB_TRIGGERBASE_TRIGGERLEVEL", # V
}
SCOPE_ENUMS = {
    "a_coupling": ("NI_EWB_A_CHANNEL_MODE", {"ac": "0", "gnd": "1", "dc": "2"}),
    "b_coupling": ("NI_EWB_B_CHANNEL_MODE", {"ac": "0", "gnd": "1", "dc": "2"}),
    "trigger": ("NI_EWB_TRIGGERBASE_TRIGGER_MODE", {"none": "0", "normal": "1", "single": "2", "auto": "3"}),
    "trigger_edge": ("NI_EWB_TRIGGERBASE_TRIGGER_EDGE", {"rising": "0", "falling": "1"}),
    "trigger_source": ("NI_EWB_TRIGGERBASE_SOURCE_MODE", {"a": "0", "b": "1", "ext": "2"}),
}
BODE_KEYS = {
    "f_start": "NI_EWB_HORZ_INITIAL_VALUE", "f_stop": "NI_EWB_HORZ_FINAL_VALUE",
    "db_min": "NI_EWB_VERT_INITIAL_LOG_VALUE", "db_max": "NI_EWB_VERT_FINAL_LOG_VALUE",
    "lin_min": "NI_EWB_VERT_INITIAL_LIN_VALUE", "lin_max": "NI_EWB_VERT_FINAL_LIN_VALUE",
    "phase_min": "NI_EWB_VERT_INITIAL_PHASE_VALUE", "phase_max": "NI_EWB_VERT_FINAL_PHASE_VALUE",
    "points": "NI_EWB_RESOLUTION_POINTS",
}
BODE_ENUMS = {
    "mode": ("NI_EWB_BODE_CONTROL_MODE", {"magnitude": "0", "phase": "1"}),
    "vertical": ("NI_EWB_VERTICAL_MODE", {"log": "0", "lin": "1"}),
    "horizontal": ("NI_EWB_HORIZONTAL_MODE", {"log": "0", "lin": "1"}),
}
FGEN_WAVES = {"SINE": "0", "TRIANGLE": "1", "SQUARE": "2"}     # panel button order


# ---------------------------------------------------------------------------
# Values and labels
# ---------------------------------------------------------------------------
def number(value) -> float:
    """A value from the instrument settings; accepts "500us", "1V", "1.59kHz"."""
    if isinstance(value, (int, float)):
        return float(value)
    return nl.parse_value(re.sub(r"(?i)(v|s|hz|db|deg|div)$", "", str(value).strip()))


def eng(value: float, micro="µ") -> str:
    """Engineering notation: 0.0001 -> 100u, 4700 -> 4.7k."""
    if value == 0:
        return "0"
    exp = int(math.floor(math.log10(abs(value)) / 3)) * 3
    exp = max(-15, min(12, exp))
    mant = value / 10 ** exp
    text = f"{mant:.4g}"
    prefix = {-15: "f", -12: "p", -9: "n", -6: micro, -3: "m", 0: "", 3: "k", 6: "M", 9: "G", 12: "T"}[exp]
    return text + prefix


uni = xmlio.uni


def _set_param(comp, index: int, value: float, display: str | None = None):
    plist = comp.find(".//CiaParamList")
    doubles, params = plist.findall("./doubles/Item"), plist.findall("./parameters/Item")
    if index >= min(len(doubles), len(params)):
        raise ValueError(f"no parameter #{index} in the table")
    doubles[index].set("Value", format(value, ".17g"))
    # Multisim regenerates the value label on the symbol from this display string, so use engineering notation (1m, not 0.001)
    params[index].set("Value", xmlio.asc(display if display is not None else eng(value, "u")))


def _set_cstring(comp, index: int, text: str):
    items = comp.findall("./Attributes/Item")
    if index < len(items) and items[index].find("CiaCString") is not None:
        items[index].find("CiaCString").set("String", text)


def apply_value(inst: nl.Instance, comp, kind: str) -> str | None:
    """Write the netlist value into the part parameters and return the (encoded) value label shown on the symbol."""
    if inst.value is not None and kind in ("R", "C", "L"):
        v = nl.parse_value(inst.value)
        _set_param(comp, 1, v)
        unit = {"R": "Ω", "C": "F", "L": "H"}[kind]
        label = uni(eng(v) + unit)
        _set_cstring(comp, 41, xmlio.asc(eng(v, "u")))
        _set_cstring(comp, 42, label)
        return label
    src = inst.source
    if not src:
        return None
    ac = src.get("ac") or (0.0, 0.0)
    if inst.part == "AC_VOLTAGE":
        for idx, val in ((1, src["amplitude"]), (3, src["offset"]), (5, src["freq"]), (7, src["delay"]),
                         (9, src["damping"]), (11, src["phase"]), (13, ac[0]), (15, ac[1])):
            _set_param(comp, idx, val)
        return uni(f"{eng(src['amplitude'])}Vpk \n{eng(src['freq'])}Hz \n{src['phase']:g}° ")
    if inst.part == "PULSE_VOLTAGE":
        for k, val in enumerate(src["pulse"]):
            _set_param(comp, 1 + 2 * k, val)
        _set_param(comp, 15, ac[0])
        _set_param(comp, 17, ac[1])
        v1, v2, _, _, _, pw, per = src["pulse"]
        return xmlio.asc(f"{eng(v1, 'u')}V {eng(v2, 'u')}V \n{eng(pw, 'u')}s {eng(per, 'u')}s ")
    if inst.part in ("DC_POWER", "DC_CURRENT"):
        _set_param(comp, 1, src["dc"])
        if inst.part == "DC_POWER":
            _set_param(comp, 3, ac[0])
            _set_param(comp, 5, ac[1])
        return xmlio.asc(f"{eng(src['dc'], 'u')}{'V' if inst.part == 'DC_POWER' else 'A'} ")
    return None


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
def _transform(el, x, y, translation=True):
    g = lambda k, d: float(el.get("Transformer-" + k, d))
    return (x * g("M00", "1") + y * g("M10", "0") + (g("M20", "0") if translation else 0),
            x * g("M01", "0") + y * g("M11", "1") + (g("M21", "0") if translation else 0))


def pin_info(sym_item) -> dict:
    """{port ID: pin centre (rotation applied, translation not), escape direction, connector ID}."""
    sym = sym_item[0]
    info = {}
    for pin_item in sym_item.findall("./CIITSymbolComp/Objects/Item[@Class='CIITPinSymbolComp']"):
        pin = pin_item[0]
        conn_item = pin_item.find("./CIITPinSymbolComp/Objects/Item[@Class='CIITPinConnectorComp']")
        conn = conn_item[0]
        px, py = _transform(conn, float(conn.get("ptCenterX")), float(conn.get("ptCenterY")))
        px, py = _transform(pin, px, py)
        px, py = _transform(sym, px, py, translation=False)
        px = round(px / GRID) * GRID if abs(px - round(px / GRID) * GRID) < .001 else px
        py = round(py / GRID) * GRID if abs(py - round(py / GRID) * GRID) < .001 else py
        direction = None
        for lead in pin.findall("./Objects/Item/CODLineComp"):
            ends = []
            for n in (0, 1):
                lx, ly = _transform(lead, float(lead.get(f"pt{n}X")), float(lead.get(f"pt{n}Y")))
                lx, ly = _transform(pin, lx, ly)
                ends.append(_transform(sym, lx, ly, translation=False))
            for anchor, inside in (ends, ends[::-1]):
                if abs(anchor[0] - px) < .01 and abs(anchor[1] - py) < .01:
                    vx, vy = px - inside[0], py - inside[1]
                    if abs(vx) > .01 and abs(vy) < .01:
                        direction = (1 if vx > 0 else -1, 0)
                    elif abs(vy) > .01 and abs(vx) < .01:
                        direction = (0, 1 if vy > 0 else -1)
        info[pin.get("PortID")] = {"x": px, "y": py, "direction": direction, "connector": conn_item.get("ID")}
    return info


def _mirror_kind(value) -> str:
    text = str(value).strip().lower().replace("_", "-")
    if text in ("x", "h", "horizontal", "mirror-x", "flip-x", "flip-horizontal"):
        return "x"
    if text in ("y", "v", "vertical", "mirror-y", "flip-y", "flip-vertical"):
        return "y"
    raise ValueError(f"mirror must be x/horizontal or y/vertical, got {value!r}")


def _matrix(angle: int, mirror=None):
    if angle not in MATRICES:
        raise ValueError(f"rotation must be 0/90/180/270, got {angle}")
    a, b, c, d = MATRICES[angle]
    if mirror:
        if _mirror_kind(mirror) == "x":
            a, b, c, d = -a, b, -c, d
        else:
            a, b, c, d = a, -b, c, -d
    return a, b, c, d


def _rotate(sym_item, angle: int, relabel=False, mirror=None):
    """Set the symbol rotation/mirror matrix and keep the labels upright (text stays horizontal).

    mirror="x" flips left/right, "y" flips top/bottom;
    mirroring also reverses pin order, which matches symbols laid out as -/COM/+.
    """
    a, b, c, d = _matrix(angle, mirror)
    sym = sym_item[0]
    for key, v in zip(("M00", "M01", "M10", "M11"), (a, b, c, d)):
        sym.set("Transformer-" + key, str(v))
    if angle == 0 and not relabel and not mirror:
        return
    pins = pin_info(sym_item)
    if not pins:
        return
    xs, ys = [p["x"] for p in pins.values()], [p["y"] for p in pins.values()]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    if max(ys) - min(ys) > max(xs) - min(xs):   # vertical: labels on the right, left aligned
        offsets, align = {"CIITSymTextCompName": (18, -9), "CIITSymTextCompValue": (18, 9)}, "0"
    else:                                         # horizontal: name above, value below, centred
        offsets, align = {"CIITSymTextCompName": (0, -24), "CIITSymTextCompValue": (0, 15)}, "1"
    for tag, (ox, oy) in offsets.items():
        label = sym.find("./Objects/Item/" + tag)
        if label is None:
            continue
        px, py = cx + ox, cy + oy
        # The label matrix is the transpose of the symbol matrix (the inverse of rotation/mirror); the text itself is never flipped.
        for key, v in {"M00": a, "M01": c, "M10": b, "M11": d, "M20": a * px + b * py, "M21": c * px + d * py}.items():
            label.set("Transformer-" + key, f"{v:g}")
        label.set("HorizontalAlign", align)


def _label_boxes(sym_item, refdes):
    """Rectangle taken by the part name and value labels (estimated from the font size); treated as an obstacle when routing."""
    sym = sym_item[0]
    out = []
    for tag in ("CIITSymTextCompName", "CIITSymTextCompValue"):
        label = sym.find("./Objects/Item/" + tag)
        lines = xmlio.text(label.get("Output") or "").strip().splitlines() if label is not None else []
        if not any(line.strip() for line in lines) or label.get("Hidden") == "1":
            continue
        x, y = _transform(sym, *_transform(label, 0, 0))
        size = abs(float(label.get("Font-Height") or 13))
        size = size if size > 9 else 16                      # positive sizes are points, roughly 16 px
        w, h = max(len(line.rstrip()) for line in lines) * size * 0.6, len(lines) * size * 1.15
        if label.get("HorizontalAlign") == "1":
            x -= w / 2
        # round outwards to the grid so wires routed around labels stay on the grid
        gx0, gy0 = math.floor((x - 2) / GRID) * GRID, math.floor((y - 2) / GRID) * GRID
        gx1, gy1 = math.ceil((x + w + 2) / GRID) * GRID, math.ceil((y + h + 2) / GRID) * GRID
        out.append({"refdes": f"{refdes}:{tag}", "owner": refdes, "x": gx0, "y": gy0, "width": gx1 - gx0, "height": gy1 - gy0})
    return out


def _horizontal(sym_item) -> bool:
    """Whether the part's native symbol is horizontal (resistor, capacitor, inductor, diode); sources are vertical by nature."""
    pins = list(pin_info(sym_item).values())
    return len(pins) == 2 and abs(pins[0]["y"] - pins[1]["y"]) < 1


def _base_angle(sym_item, ports, pin_order) -> int:
    """Normalise two-terminal parts so the first node is on the left at 0 degrees (capacitors and similar carry pin 1 on the right, so they get a 180 degree turn first)."""
    if len(pin_order) != 2:
        return 0
    pins = pin_info(sym_item)
    name_of = {p.get("CiID"): xmlio.unasc(p[0].get("LocalName")) for p in ports}
    at = {name_of.get(pid): p for pid, p in pins.items()}
    a, b = at.get(pin_order[0]), at.get(pin_order[1])
    if a and b and abs(a["y"] - b["y"]) < 1 and a["x"] > b["x"]:
        return 180
    return 0


def _snap(value: float) -> float:
    return round(value / GRID) * GRID


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
def _find_main(root):
    for diagram in root.iter("CIITDiagram"):
        elements = diagram.find("./Elements")
        composite = diagram.find("./Components/CODComposite")
        if elements is None or composite is None:
            continue
        circuit = next((c for c in elements if c.get("Class") == "CiCircuit"), None)
        if circuit is not None:
            return diagram, composite, elements, circuit
    raise ValueError("the circuit shell has no main schematic")


def _refdes_info(refdes, file_path, stem, template=None):
    """RefDes record: multi-section parts copy the harvested one (it carries the pin order), everything else is created in the same format."""
    m = re.fullmatch(r"([A-Za-z]+)(\d+)", refdes)
    if not m:
        return None
    prefix, num = m.groups()
    text = f"&ASC!0!0!0{refdes}!0{file_path}!01!0{stem}!0"
    if template is not None:
        item = xmlio.deepcopy(template)
        section = xmlio.unasc(item.find("RefDesInfo").get("IRSection"))
        item.set("CIRKey", xmlio.asc(refdes + section))
        info = item.find("RefDesInfo")
        info.set("IRPrefix", xmlio.asc(prefix))
        info.set("IRNumber", num)
        data = info.find("RefDesInfoData")
        data.set("RefDes", text)
        data.set("RefDesStrSize", str(len(text)))
        data.set("Prefix", xmlio.asc(prefix))
        data.set("Number", num)
        return item
    item = ET.Element("CIRToInfoMapItem", {"CIRKey": xmlio.asc(refdes)})
    info = ET.SubElement(item, "RefDesInfo", {"Class": "CIITHierRefDesInfo", "IRPrefix": xmlio.asc(prefix),
                                              "IRNumber": num, "Locked": "0", "IRSection": "", "IRSectionID": "-1",
                                              "SpiceTemplate": ""})
    data = ET.SubElement(info, "RefDesInfoData", {
        "Class": "CIITHierRefDes", "RefDesBufSize": "260", "RefDesStrSize": str(len(text)), "RefDesCount": "2",
        "RefDes": text, "SectionBufSize": "0", "SectionStrSize": "0", "Section": "&ASC(null)",
        "Prefix": xmlio.asc(prefix), "Number": num})
    ET.SubElement(data, "RefDesData")
    for tag in ("PinOrderCIR", "PinOrderIR", "PinNumbersCIR", "PinNumbersIR", "SharedPins"):
        ET.SubElement(info, tag)
    return item


def _set_key(node, key, value):
    for el in node.iter("Element"):
        if el.get("Key") == "&ASC" + key:
            el.find("CDataElement").set("Data", value)
            return
    raise KeyError(f"no {key} in the instrument state")


def apply_instrument(node, ref: str, settings: dict, params: dict):
    up = ref.upper()
    _set_key(node, "NI_EWB_SHOW_WINDOW_STATUS", "1" if settings.get("show", not up.startswith("XFG")) else "0")
    keys, enums = ({}, {})
    if up.startswith("XSC"):
        keys, enums = SCOPE_KEYS, SCOPE_ENUMS
    elif up.startswith("XBP"):
        keys, enums = BODE_KEYS, BODE_ENUMS
    for name, value in settings.items():
        if name == "show":
            continue
        if name in keys:
            _set_key(node, keys[name], str(int(value)) if name == "points" else f"{number(value):g}")
        elif name in enums:
            key, table = enums[name]
            _set_key(node, key, table[str(value).lower()])
        else:
            raise ValueError(f"{ref}: unsupported instrument parameter {name}")
    if up.startswith("XFG"):
        wave = params.get("WAVE", "SINE").upper()
        if wave not in FGEN_WAVES:
            raise ValueError(f"{ref}: WAVE must be SINE/TRIANGLE/SQUARE")
        values = {"NI_EWB_WAVE_MODE": FGEN_WAVES[wave],
                  "NI_EWB_FREQUENCY_VALUE": params.get("FREQ", "1k"),
                  "NI_EWB_AMPLITUDE_VALUE": params.get("AMPLITUDE", "1"),
                  "NI_EWB_OFFSET_VALUE": params.get("OFFSET", "0"),
                  "NI_EWB_DEPUTY_CYCLE_VALUE": params.get("DUTY", "50"),
                  "NI_EWB_RISETIMESETTING": params.get("RISE", "10n")}
        for key, value in values.items():
            _set_key(node, key, value if key == "NI_EWB_WAVE_MODE" else f"{number(value):g}")


class Builder:
    def __init__(self, lib: Library, out: Path):
        self.lib, self.out, self.stem = lib, out, out.stem
        self.root = lib.struct("shell.xml")
        for el in self.root.iter():                 # the shell was created from a file named blank
            for k, v in el.attrib.items():
                if v == "&ASCblank" or "#1/blank:" in v:
                    el.set(k, v.replace("blank", self.stem))
        self.diagram, composite, self.elements, self.circuit_item = _find_main(self.root)
        self.objects = composite.find("Objects")
        self.refs = composite.find("ReferencedComponents")
        self.circuit = self.circuit_item.find("CiCircuit")
        self.ids = xmlio.IdAllocator()
        xmlio.remap([self.circuit_item], self.ids)
        self.nodes, self.connections = {}, {}
        self.comp_items, self.port_items, self.model_items = [], [], []
        self.boxes, self.labels, self.placements, self.warnings = [], [], [], []
        self.model_seq = {}
        self.instrument_states, self.refdes_items = [], []
        self.expected = {}                           # {net: {refdes.pin}}, compared against the file during self-check
        self.route_failures = []                     # nets whose wire could only be drawn by degrading
        self.degraded = False

    # -- nets ---------------------------------------------------------------
    def node(self, net: str):
        if net not in self.nodes:
            item = self.lib.struct("node_v0.xml" if net == "0" else "node_named.xml")
            xmlio.remap([item], self.ids)
            item[0].set("LocalName", xmlio.asc(net))
            xmlio.clear(item[0].find("Ports"))
            self.nodes[net] = item
        return self.nodes[net]

    def _attach(self, port_item, net):
        nodes = port_item[0].find("Nodes")
        xmlio.clear(nodes)
        if net is None:
            return
        node = self.node(net)
        nodes.append(ET.Element("Item", {"CiID": node.get("CiID")}))
        node[0].find("Ports").append(ET.Element("Item", {"CiID": port_item.get("CiID")}))

    # -- parts --------------------------------------------------------------
    def place(self, refdes, part_name, nets: dict, center, angle=0, inst=None, ground=False, mirror=None):
        """nets: {port name: net}; center: target pin centre coordinate."""
        part = self.lib.load(part_name)
        pin_order = self.lib.index[self.lib.resolve(part_name)]["pins"]
        items = [xmlio.deepcopy(i) for i in part.find("Items")]
        xmlio.remap(items, self.ids)
        comp_item = next(i for i in items if i.get("Class") == "CiComponent")
        ports = [i for i in items if i.get("Class") == "CiPort"]
        models = [i for i in items if i.get("Class") == "CiModel"]
        sym_item = next(i for i in items if i.get("Class") == "CIITSymbolComp")
        comp, sym = comp_item[0], sym_item[0]
        comp.set("LocalName", xmlio.asc("0" if ground else refdes))
        sym.set("CiComponent", comp_item.get("CiID"))
        if comp.get("SymCompID") not in (None, "", "0"):
            comp.set("SymCompID", sym_item.get("ID"))
        for model in models:                         # one model per instance, named <name>__<family>__<index> as Multisim does
            m = model[0]
            m.set("Scope", self.circuit_item.get("CiID"))
            parts = xmlio.unasc(m.get("LocalName")).split("__")
            if len(parts) >= 3:
                key = tuple(parts[:2])
                self.model_seq[key] = self.model_seq.get(key, 0) + 1
                new = f"{parts[0]}__{parts[1]}__{self.model_seq[key]}"
                m.set("LocalName", xmlio.asc(new))
                card = m.find("Attributes/Item/CiaCString")
                if card is not None:
                    card.set("String", re.sub(r"(?i)^(&ASC\s*\.(?:SUBCKT|MODEL)\s+)\S+", lambda mm: mm.group(1) + new,
                                              card.get("String", ""), count=1))

        kind = refdes[0].upper()
        value_label = apply_value(inst, comp, kind) if inst is not None else None
        name_label = sym.find("./Objects/Item/CIITSymTextCompName")
        if name_label is not None:
            section = xmlio.unasc(sym.get("IRSection"))
            name_label.set("Output", "" if ground else xmlio.asc(refdes + section))
        value_item = sym.find("./Objects/Item/CIITSymTextCompValue")
        if value_label and value_item is not None:
            value_item.set("Output", value_label)

        for tag in ("CIITSymTextCompName", "CIITSymTextCompValue"):
            label = sym.find("./Objects/Item/" + tag)
            if label is not None and float(label.get("Font-Height") or -13) > 0:
                label.set("Font-Height", "-13")      # a few samples use a positive size, which renders far too large
        base = _base_angle(sym_item, ports, pin_order)
        _rotate(sym_item, (angle + base) % 360, relabel=len(pin_order) == 2 and base in (0, 180) and _horizontal(sym_item), mirror=mirror)
        pins = pin_info(sym_item)
        if pins:
            xs = [p["x"] for p in pins.values()]
            ys = [p["y"] for p in pins.values()]
            first = next(iter(pins.values()))
            tx = _snap(center[0] - (min(xs) + max(xs)) / 2)
            ty = _snap(center[1] - (min(ys) + max(ys)) / 2)
            tx -= (tx + first["x"]) % GRID if (tx + first["x"]) % GRID <= GRID / 2 else (tx + first["x"]) % GRID - GRID
            ty -= (ty + first["y"]) % GRID if (ty + first["y"]) % GRID <= GRID / 2 else (ty + first["y"]) % GRID - GRID
        else:
            tx, ty = _snap(center[0]), _snap(center[1])
        sym.set("Transformer-M20", f"{tx:g}")
        sym.set("Transformer-M21", f"{ty:g}")

        name_of = {p.get("CiID"): xmlio.unasc(p[0].get("LocalName")) for p in ports}
        hidden = {h.get("Name"): h.get("Net") for h in part.findall("HiddenPin")}
        pts = []
        for port in ports:
            pname = name_of[port.get("CiID")]
            net = nets.get(pname, hidden.get(pname))
            self._attach(port, net)
            if net is not None and not ground:
                self.expected.setdefault(net, set()).add(f"{refdes}.{pname}")
            pin = pins.get(port.get("CiID"))
            if pin is None or net is None:
                continue
            conn = {"x": tx + pin["x"], "y": ty + pin["y"], "connector": pin["connector"], "symbol": sym_item,
                    "refdes": refdes, "direction": pin["direction"], "ground": ground}
            self.connections.setdefault(net, []).append(conn)
            pts.append((conn["x"], conn["y"]))
        if not pts:
            pts = [(tx + p["x"], ty + p["y"]) for p in pins.values()] or [(tx, ty)]
        # Routing obstacle box = pin bounds grown by two cells: keeps wires clear of the part body, and both the boundary and the escape points land on the 9-unit grid
        x0, y0 = min(p[0] for p in pts), min(p[1] for p in pts)
        box = {"refdes": refdes, "x": x0 - 18, "y": y0 - 18,
               "width": max(p[0] for p in pts) - x0 + 36, "height": max(p[1] for p in pts) - y0 + 36}
        self.boxes.append(box)
        self.labels.extend(_label_boxes(sym_item, refdes))
        self.placements.append({"refdes": refdes, "part": part_name, "center": [center[0], center[1]], "angle": angle, "mirror": mirror,
                                "box": [box["x"], box["y"], box["width"], box["height"]]})

        self.objects.append(sym_item)
        self.refs.append(ET.Element("Item", {"ID": sym_item.get("ID"), "Class": "CIITSymbolComp"}))
        self.comp_items.append(comp_item)
        self.port_items.extend(ports)
        self.model_items.extend(models)
        state = part.find("Instrument/CSourceSymbolCollectNode")
        if state is not None:
            state = xmlio.deepcopy(state)
            state.set("CompLongName", f"&ASC{refdes}#1/{self.stem}:")
            self.instrument_states.append((refdes, state))
        if not ground:
            info = _refdes_info(refdes, str(self.out), self.stem, part.find("RefDesInfo/CIRToInfoMapItem"))
            if info is not None:
                self.refdes_items.append(info)
        return pins

    # -- routing ------------------------------------------------------------
    def _wire_explicit_net(self, net, paths, colors, net_wire_items, wire_width=1):
        """Lay one net along explicit polylines; endpoints must land on a pin or a wire intersection."""
        conns = self.connections[net]

        def pt(p):
            return (round(float(p[0]), 3), round(float(p[1]), 3))

        pin_map = {}
        for c in conns:
            pin_map.setdefault((round(c["x"], 3), round(c["y"], 3)), []).append(c)
        if not paths:
            return
        if (isinstance(paths[0], (list, tuple)) and len(paths[0]) == 2
                and all(isinstance(v, (int, float)) for v in paths[0])):
            paths = [paths]
        vertices, segments = set(), []
        for path in paths:
            pts = [pt(p) for p in path]
            if len(pts) < 2:
                raise ValueError(f"wires.{net}: a wire needs at least two points")
            vertices.update(pts)
            for a, b in zip(pts, pts[1:]):
                if a == b:
                    continue
                if abs(a[0] - b[0]) > 1e-6 and abs(a[1] - b[1]) > 1e-6:
                    raise ValueError(f"wires.{net}: segments must be horizontal or vertical, ({a} -> {b}) is not")
                segments.append((a, b))

        def on_segment(p, a, b):
            if abs(a[0] - b[0]) < 1e-6:
                return abs(p[0] - a[0]) < 1e-6 and min(a[1], b[1]) + 1e-6 < p[1] < max(a[1], b[1]) - 1e-6
            return abs(p[1] - a[1]) < 1e-6 and min(a[0], b[0]) + 1e-6 < p[0] < max(a[0], b[0]) - 1e-6

        edges = set()
        for a, b in segments:
            cuts = [p for p in vertices if p not in (a, b) and on_segment(p, a, b)]
            cuts.sort(key=(lambda p: p[1]) if abs(a[0] - b[0]) < 1e-6 else (lambda p: p[0]))
            chain = [a] + cuts + [b]
            for u, v in zip(chain, chain[1:]):
                if u != v:
                    edges.add(tuple(sorted((u, v))))
        if not edges:
            return

        adj = {}
        for u, v in edges:
            adj.setdefault(u, set()).add(v)
            adj.setdefault(v, set()).add(u)

        def is_terminal(n):
            return n in pin_map or len(adj[n]) != 2

        visited, chains = set(), []
        for start in [n for n in adj if is_terminal(n)]:
            for nxt in sorted(adj[start]):
                edge = tuple(sorted((start, nxt)))
                if edge in visited:
                    continue
                visited.add(edge)
                chain, prev, cur = [start], start, nxt
                while True:
                    chain.append(cur)
                    if is_terminal(cur):
                        break
                    nxts = [x for x in adj[cur] if x != prev]
                    if not nxts:
                        break
                    prev, cur = cur, nxts[0]
                    visited.add(tuple(sorted((prev, cur))))
                chains.append(chain)
        for u, v in edges:
            edge = tuple(sorted((u, v)))
            if edge in visited:
                continue
            visited.add(edge)
            chain, prev, cur = [u], u, v
            while cur != u:
                chain.append(cur)
                nxts = [x for x in adj[cur] if x != prev]
                if not nxts:
                    break
                prev, cur = cur, nxts[0]
                visited.add(tuple(sorted((prev, cur))))
            chain.append(u)
            chains.append(chain)

        junction_nodes = {n for n in adj if len(adj[n]) + len(pin_map.get(n, [])) >= 3}
        junction_nodes.update(chain[0] for chain in chains if chain[0] == chain[-1])
        endpoint_count = {}
        for chain in chains:
            for node in (chain[0], chain[-1]):
                if node in junction_nodes:
                    endpoint_count[node] = endpoint_count.get(node, 0) + 1
        members = {}
        for node, count in endpoint_count.items():
            owner_item = self.lib.struct("junction_owner.xml")
            xmlio.remap([owner_item], self.ids)
            owner = owner_item[0]
            owner.set("Transformer-M20", f"{node[0]:g}")
            owner.set("Transformer-M21", f"{node[1]:g}")
            owner.set("CenterX", "0")
            owner.set("CenterY", "0")
            xmlio.clear(owner.find("ConnectList"))
            members[node] = []
            for _ in range(count):
                member_item = self.lib.struct("junction_member.xml")
                xmlio.remap([member_item], self.ids)
                member = member_item[0]
                member.set("CenterX", f"{node[0]:g}")
                member.set("CenterY", f"{node[1]:g}")
                member.find("ConnectList/Item").set("ID", owner_item.get("ID"))
                self.refs.append(member_item)
                owner.find("ConnectList").append(ET.Element("Item", {"ID": member_item.get("ID"), "Class": "CODPinComp"}))
                members[node].append(member_item.get("ID"))
            for c in pin_map.get(node, []):
                owner.find("ConnectList").append(ET.Element("Item", {"ID": c["extpin"], "Class": "CODPinComp"}))
            self.objects.append(owner_item)

        node_id = self.node(net).get("CiID")

        def endpoint(node):
            if node in junction_nodes:
                return members[node].pop()
            pins = pin_map.get(node)
            if pins and len(pins) == 1:
                return pins[0]["extpin"]
            raise ValueError(f"wires.{net}: wire endpoint {node} is not connected to a pin or a junction")

        for chain in chains:
            wire_item = self.lib.struct("wire_named.xml")
            xmlio.remap([wire_item], self.ids)
            w = wire_item[0]
            w.set("Pen-Width", str(int(wire_width)))
            w.set("Connect1", endpoint(chain[0]))
            w.set("Connect2", endpoint(chain[-1]))
            w.set("Node", node_id)
            points = w.find("Points")
            xmlio.clear(points)
            for px, py in chain:
                points.append(ET.Element("Item", {"X": f"{px:g}", "Y": f"{py:g}"}))
            mod = w.find("./ElectricalObject/ModifierInfo/Element")
            if net == "0":
                mod.set("NetModifier", "&ASCNI_EWB_NET_AUTONAMED")
                mod.find("Item").set("Value", "")
            else:
                mod.set("NetModifier", "&ASCNI_EWB_NET_NAME")
                mod.find("Item").set("Value", xmlio.asc(net))
            if net in colors:
                w.set("PenColor", str(colors[net]))
                w.set("Pen-Color", str(colors[net]))
                w.set("FgColor", "0")
                w.set("LockedPenColor", "0")
            self.objects.append(wire_item)
            self.refs.append(ET.Element("Item", {"ID": wire_item.get("ID"), "Class": "CIITLinkComp"}))
            net_wire_items.setdefault(net, []).append(wire_item)
            self.wires.setdefault(net, []).append(chain)

    def wire_all(self, colors: dict, net_names: bool, explicit=None, wire_width=1, allow_fallback=False):
        for net, conns in self.connections.items():
            if len(conns) < 2:
                continue
            for c in conns:
                pin_item = self.lib.struct("extpin.xml")
                xmlio.remap([pin_item], self.ids)
                p = pin_item[0]
                p.set("CenterX", f"{c['x']:g}")
                p.set("CenterY", f"{c['y']:g}")
                p.find("ConnectList/Item").set("ID", c["connector"])
                self.refs.append(pin_item)
                c["extpin"] = pin_item.get("ID")
                for conn_item in c["symbol"].iter("Item"):
                    if conn_item.get("Class") == "CIITPinConnectorComp" and conn_item.get("ID") == c["connector"]:
                        clist = conn_item[0].find("ConnectList")
                        if clist is None:
                            clist = ET.SubElement(conn_item[0], "ConnectList")
                        xmlio.clear(clist)       # Class is required, otherwise Multisim cannot tell which wire a pin is connected to
                        clist.append(ET.Element("Item", {"ID": c["extpin"], "Class": "CODPinComp"}))
        explicit = explicit or {}
        routed = {n: c for n, c in self.connections.items() if len(c) >= 2 and n not in explicit}
        islands, failures = route_all(routed, self.boxes, self.labels)
        self.route_failures = failures
        if failures:
            if not allow_fallback:
                raise RouteError(failures)        # nothing is written: a degraded drawing is not a result
            self.degraded = True
            nets = sorted({f["net"] for f in failures})
            self.warnings.append(
                f"degraded routing: {len(failures)} wire segment(s) on net(s) {', '.join(nets)} had no "
                f"obstacle-free path and fell back to a simple polyline; the drawing may cross parts or other nets")
        self.wires = {}
        net_wire_items = {}
        for net, conns, edges, junctions in islands:
            members = {}
            for k, (jx, jy) in enumerate(junctions):
                count = sum((e["a"] == ("j", k)) + (e["b"] == ("j", k)) for e in edges)
                owner_item = self.lib.struct("junction_owner.xml")
                xmlio.remap([owner_item], self.ids)
                owner = owner_item[0]
                owner.set("Transformer-M20", f"{jx:g}")
                owner.set("Transformer-M21", f"{jy:g}")
                owner.set("CenterX", "0")          # a stale centre offset from the template draws the junction dot elsewhere
                owner.set("CenterY", "0")
                xmlio.clear(owner.find("ConnectList"))
                members[k] = []
                for _ in range(count):
                    member_item = self.lib.struct("junction_member.xml")
                    xmlio.remap([member_item], self.ids)
                    member_item[0].set("CenterX", f"{jx:g}")
                    member_item[0].set("CenterY", f"{jy:g}")
                    member_item[0].find("ConnectList/Item").set("ID", owner_item.get("ID"))
                    self.refs.append(member_item)
                    owner.find("ConnectList").append(ET.Element("Item", {"ID": member_item.get("ID"), "Class": "CODPinComp"}))
                    members[k].append(member_item.get("ID"))
                self.objects.append(owner_item)

            def endpoint(end):
                kind, idx = end
                return conns[idx]["extpin"] if kind == "pin" else members[idx].pop()

            node_id = self.node(net).get("CiID")
            for e in edges:
                wire_item = self.lib.struct("wire_named.xml")
                xmlio.remap([wire_item], self.ids)
                w = wire_item[0]
                w.set("Pen-Width", str(int(wire_width)))
                w.set("Connect1", endpoint(e["a"]))
                w.set("Connect2", endpoint(e["b"]))
                w.set("Node", node_id)
                points = w.find("Points")
                xmlio.clear(points)
                for px, py in e["path"]:
                    points.append(ET.Element("Item", {"X": f"{px:g}", "Y": f"{py:g}"}))
                mod = w.find("./ElectricalObject/ModifierInfo/Element")
                if net == "0":
                    mod.set("NetModifier", "&ASCNI_EWB_NET_AUTONAMED")
                    mod.find("Item").set("Value", "")
                else:
                    mod.set("NetModifier", "&ASCNI_EWB_NET_NAME")
                    mod.find("Item").set("Value", xmlio.asc(net))
                if net in colors:                    # the scope trace follows the colour of the wire it is connected to
                    w.set("PenColor", str(colors[net]))
                    w.set("Pen-Color", str(colors[net]))
                    w.set("FgColor", "0")
                    w.set("LockedPenColor", "0")
                self.objects.append(wire_item)
                self.refs.append(ET.Element("Item", {"ID": wire_item.get("ID"), "Class": "CIITLinkComp"}))
                net_wire_items.setdefault(net, []).append(wire_item)
                self.wires.setdefault(net, []).append(e["path"])
        for net, paths in explicit.items():
            if net not in self.connections:
                raise ValueError(f"net {net!r} in wires is not in the netlist")
            if len(self.connections[net]) >= 2:
                self._wire_explicit_net(net, paths, colors, net_wire_items, wire_width)
        for net, items in net_wire_items.items():   # one net-name label per net
            text_item = self.lib.struct("nodetext.xml")
            xmlio.remap([text_item], self.ids)
            t = text_item[0]
            segs = [s for path in self.wires[net] for s in zip(path, path[1:]) if s[0][1] == s[1][1]]
            if segs:
                a, b = max(segs, key=lambda s: abs(s[0][0] - s[1][0]))
                t.set("Transformer-M20", f"{(a[0] + b[0]) / 2:g}")
                t.set("Transformer-M21", f"{a[1] - 12:g}")
            if not net_names:
                t.set("Hidden", "1")
            links = t.find("Links")
            xmlio.clear(links)
            for wire_item in items:
                wire_item[0].set("NodeText", text_item.get("ID"))
                links.append(ET.Element("Item", {"ID": wire_item.get("ID"), "Class": "CIITLinkComp"}))
            self.objects.append(text_item)
            self.refs.append(ET.Element("Item", {"ID": text_item.get("ID"), "Class": "CODNodeTextComp"}))

    # -- finish -------------------------------------------------------------
    def finish(self, instruments: dict, params_of: dict, sim_time, sheet):
        xmlio.clear(self.elements)                   # circuit objects must come after parts, ports and nets
        for item in self.comp_items + self.port_items + list(self.nodes.values()):
            self.elements.append(item)
        self.elements.append(self.circuit_item)
        for item in self.model_items:                # Multisim puts the models last in Elements
            self.elements.append(item)
        models = next(self.root.iter("Models"), None)
        if models is not None:
            for item in self.model_items:
                models.append(ET.Element("Item", {"CiID": item.get("CiID")}))
        for item in self.nodes.values():
            self.circuit.find("Nodes").append(ET.Element("Item", {"CiID": item.get("CiID")}))
        for item in self.comp_items:
            self.circuit.find("Components").append(ET.Element("Item", {"CiID": item.get("CiID")}))
        mapping = xmlio.find_tag(self.root, "CIRToInfoMap")
        if mapping is not None:
            for item in self.refdes_items:
                mapping.append(item)
        data = xmlio.find_tag(self.root, "InstrumentsData")
        for ref, state in self.instrument_states:
            apply_instrument(state, ref, instruments.get(ref, {}), params_of.get(ref, {}))
            data.append(state)
        unknown = set(instruments) - {ref for ref, _ in self.instrument_states}
        if unknown:
            raise ValueError(f"{sorted(unknown)} in instruments are not instruments of this circuit")
        cid = self.circuit_item.get("CiID")
        for el in self.root.iter():
            if "Circuit" in el.attrib:
                el.set("Circuit", cid)
        self._sheet(sheet)
        if sim_time:
            state = xmlio.read(self.lib.path / "_struct" / "simstate.txt")
            self.diagram.set("SimState", state.replace("TSTOP:double{1e+030}", f"TSTOP:double{{{sim_time:g}}}"))

    def _sheet(self, sheet):
        if sheet:
            width, height = round(sheet[0] * 96), round(sheet[1] * 96)
        else:                                        # tighten to the circuit extent so F7 fills the window
            xs, ys = [0.0], [0.0]
            for x, y, w, h in (p["box"] for p in self.placements):
                xs.append(x + w + 96)                # leave room for the labels on the right
                ys.append(y + h + 36)
            for paths in getattr(self, "wires", {}).values():
                for path in paths:
                    xs.extend(p[0] for p in path)
                    ys.extend(p[1] for p in path)
            width = max(480, int((max(xs) + 72) // 48 + 1) * 48)
            height = max(384, int((max(ys) + 72) // 48 + 1) * 48)
        values = {"Sheet Width": width, "Sheet Height": height,
                  "Sheet Width In Inch": width / 96, "Sheet Height In Inch": height / 96}
        for setting in self.diagram.findall("./CircPrefs/CIITCircuitPrefs/Settings/Element"):
            key = xmlio.unasc(setting.get("Key", ""))
            if key in values and setting.find("Item") is not None:
                setting.find("Item").set("Value", f"&ASC{values[key]:g}")
        self.diagram.set("PageWidth", f"{width / 96:g}")
        self.diagram.set("PageHeight", f"{height / 96:g}")
        hw, hh = round(width / 96 * 25.4 / 2 * 1e6), round(height / 96 * 25.4 / 2 * 1e6)
        for s in self.root.iter("DesignSheet"):
            s.set("WorkAreaLeft", str(-hw))
            s.set("WorkAreaRight", str(hw))
            s.set("WorkAreaBottom", str(-hh))
            s.set("WorkAreaTop", str(hh))


def _layout_key(layout: dict, refdes: str):
    for key in (refdes, refdes.upper(), "X" + refdes, refdes.lstrip("Xx")):
        if key in layout:
            return layout[key]
    return None


def build(spec: dict, lib: Library | None = None, allow_fallback: bool = False) -> dict:
    """Build a .ms14 from a spec.

    Degraded routing (a wire with no obstacle-free path) is an error by default:
    raise RouteError and write nothing. Pass allow_fallback=True to accept it;
    the result then carries degraded=True and the failing nets.
    """
    lib = lib or Library()
    run_id = new_run_id()
    out = Path(spec["output"]).resolve()
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", out.stem) or out.suffix.lower() != ".ms14":
        raise ValueError("output must be .ms14 with a file name of letters, digits, underscore and hyphen only")
    instances = nl.parse(spec["netlist"])
    layout = spec.get("layout") or {}
    b = Builder(lib, out)
    params_of = {}
    auto = 0
    for inst in instances:
        key = lib.resolve(inst.part)
        if key is None:
            raise KeyError(f"{inst.refdes}: part {inst.part!r} is not in the part library (search with 'multisimforge parts list', "
                           f"fetch it from the Multisim database with 'multisimforge parts fetch {inst.part}')")
        info = lib.index[key]
        refdes = inst.refdes
        if refdes[0] in "Xx" and info["kind"] != "instrument" and len(refdes) > 1 and refdes[1].isalpha():
            refdes = refdes[1:]                      # XU1 -> U1 (Multisim naming convention)
        pins = info["pins"]
        if inst.named:
            bad = [p for p in inst.named if p not in pins and p not in info["hidden_pins"]]
            if bad:
                raise ValueError(f"{inst.refdes}: {key} has no pin {bad}; available pins {pins + list(info['hidden_pins'])}")
            nets = dict(inst.named)
        else:
            if len(inst.nets) > len(pins):
                raise ValueError(f"{inst.refdes}: {key} only has {len(pins)} pins {pins}, the netlist gave {len(inst.nets)} nodes")
            nets = dict(zip(pins, inst.nets))
        pos = _layout_key(layout, inst.refdes) or _layout_key(layout, refdes)
        if pos is None:                              # parts without coordinates are spread out on the grid
            pos = [150 + (auto % 5) * 180, 150 + (auto // 5) * 180]
            auto += 1
            b.warnings.append(f"{inst.refdes} had no coordinates and was placed automatically")
        angle, mirror = 0, None
        if len(pos) > 2:
            third = pos[2]
            try:
                angle = int(third) % 360
            except (TypeError, ValueError):
                mirror = third
        if len(pos) > 3 and pos[3] is not None:
            mirror = pos[3]
        b.place(refdes, key, nets, (float(pos[0]), float(pos[1])), angle, inst=inst, mirror=mirror)
        params_of[refdes] = inst.params
    grounded = any("0" in (list(i.named.values()) + i.nets) for i in instances)
    grounds = layout.get("GND") or []
    if grounded and not grounds:
        raise ValueError("the circuit is grounded (node 0), so layout must give ground symbol positions: \"GND\": [[x, y], ...]")
    for k, g in enumerate(grounds, start=1):
        b.place(f"GND{k}", "GROUND", {"1": "0"}, (float(g[0]), float(g[1])), ground=True)

    colors = {}
    for inst in instances:                           # scope channel B wires are blue by default
        if inst.part == "SCOPE" and len(inst.nets) > 1 and inst.nets[1] != "0":
            colors[inst.nets[1]] = COLORS["blue"]
    for net, color in (spec.get("colors") or {}).items():
        colors[nl.net_name(net)] = COLORS.get(str(color).lower(), color)
    b.wire_all(colors, bool(spec.get("net_names")), spec.get("wires") or {}, int(spec.get("wire_width", 1)),
               allow_fallback=allow_fallback)

    instruments = spec.get("instruments") or {}
    sim_time = number(spec["sim_time"]) if spec.get("sim_time") else None
    sweep = None
    scopes = [i for i in instances if i.part == "SCOPE"]
    if scopes:
        sweep = 10 * number((instruments.get(scopes[0].refdes) or {}).get("timebase", 1e-3))
        sim_time = sim_time or 20 * sweep
    b.finish(instruments, params_of, sim_time, spec.get("sheet"))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.name}.tmp-{run_id}")
    try:
        xmlio.save(b.root, tmp)
        os.replace(tmp, out)          # atomic: a failed build never leaves a half-written .ms14
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    return {"ms14": str(out), "run_id": run_id, "sim_time": sim_time, "sweep": sweep,
            "placements": b.placements, "wires": b.wires, "boxes": b.boxes,
            "warnings": b.warnings, "expected": b.expected,
            "degraded": b.degraded, "route_failures": b.route_failures}


def set_sim_time(ms14, sim_time: float):
    """Change only the interactive simulation stop time (used by capture when aligning the scope sweep)."""
    root = xmlio.load(ms14)
    diagram = next(root.iter("CIITDiagram"))
    head, sep, tail = (diagram.get("SimState") or "").partition("DEFAULT_INST_SETTINGS")
    if not sep:
        raise ValueError("the file has no interactive simulation settings (sim_time was not written when it was generated)")
    tail = re.sub(r"TSTOP:double\{[^}]*\}", f"TSTOP:double{{{sim_time:g}}}", tail, count=1)
    diagram.set("SimState", head + sep + tail)
    xmlio.save(root, ms14)