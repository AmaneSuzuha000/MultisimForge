"""Self-check that does not depend on Multisim: reads a .ms14, reconstructs wire connectivity, compares it with the electrical nets, and checks layout problems.

- Connectivity: wires, junctions, and component pins are merged into groups by their graphical connections; each group should correspond to exactly one electrical net
  (Multisim's ReportNetlist is not called, to avoid crashes of the automation interface on real machines).
- Layout: overlapping components, wires running through components, wires of different nets crossing, components outside the sheet.
"""
from collections import defaultdict

from . import xmlio
from .parts import identity


def _transform(el, x, y):
    g = lambda k, d: float(el.get("Transformer-" + k, d))
    return (x * g("M00", "1") + y * g("M10", "0") + g("M20", "0"),
            x * g("M01", "0") + y * g("M11", "1") + g("M21", "0"))


def symbol_bbox(sym_item):
    """Bounding rectangle (x0, y0, x1, y1) of a symbol's graphics (lines, boxes, arcs, images; text excluded) on the sheet."""
    xs, ys = [], []

    def walk(el, chain):
        if el.tag == "HiddenPins" or el.get("Hidden") == "1":      # hidden power pins are not drawn
            return
        if el.get("Transformer-M00") is not None:
            chain = [el] + chain
        tag = el.tag
        pts = []
        if not tag.startswith("CIITSymText") and not tag.startswith("CIITPinSymText"):
            if el.get("pt0X") is not None and el.get("pt1X") is not None:
                pts = [(float(el.get("pt0X")), float(el.get("pt0Y"))), (float(el.get("pt1X")), float(el.get("pt1Y")))]
            elif el.get("ptCenterX") is not None:
                pts = [(float(el.get("ptCenterX")), float(el.get("ptCenterY")))]
        for x, y in pts:
            for t in chain:
                x, y = _transform(t, x, y)
            xs.append(x)
            ys.append(y)
        for child in el:
            walk(child, chain)

    walk(sym_item[0], [])
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


class Circuit:
    """Read-only view of a .ms14: components, ports, nets, wires, and graphical connectivity."""

    def __init__(self, root):
        self.root = root
        self.items = {}
        for it in root.iter("Item"):
            key = it.get("CiID") or it.get("ID")
            if key and len(it):
                self.items[key] = it
        self.comps = {k: it for k, it in self.items.items() if it.get("Class") == "CiComponent"}
        self.ports = {k: it for k, it in self.items.items() if it.get("Class") == "CiPort"}
        self.nodes = {k: it for k, it in self.items.items() if it.get("Class") == "CiNode"}
        self.symbols = {k: it for k, it in self.items.items() if it.get("Class") == "CIITSymbolComp"}
        self.wires = {k: it for k, it in self.items.items() if it.get("Class") == "CIITLinkComp"}

    def comp_name(self, comp_id):
        it = self.comps.get(comp_id)
        return xmlio.unasc(it[0].get("LocalName")) if it is not None else "?"

    def port_label(self, port_id):
        port = self.ports[port_id][0]
        return f"{self.comp_name(port.get('Component'))}.{xmlio.unasc(port.get('LocalName'))}"

    def electrical_nets(self) -> dict:
        """{net name: {component.pin, ...}} (ground symbols are not included)."""
        out = {}
        for it in self.nodes.values():
            members = {self.port_label(p.get("CiID")) for p in it[0].find("Ports") if p.get("CiID") in self.ports}
            out[xmlio.unasc(it[0].get("LocalName")).lower()] = {m for m in members if not m.startswith("0.")}
        return out

    def graphic_groups(self):
        """Group component pins by the graphical connections of wires and junctions: [{component.pin, ...}, ...]."""
        parent = {}

        def find(a):
            parent.setdefault(a, a)
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        def union(a, b):
            parent[find(a)] = find(b)

        connector_port = {}
        for sym in self.symbols.values():
            for pin_item in sym[0].findall("./Objects/Item[@Class='CIITPinSymbolComp']"):
                conn = pin_item.find("./CIITPinSymbolComp/Objects/Item[@Class='CIITPinConnectorComp']")
                if conn is not None:
                    connector_port[conn.get("ID")] = pin_item[0].get("PortID")
        for key, it in self.items.items():
            if it.get("Class") == "CODPinComp":
                for link in it[0].find("ConnectList"):
                    union(key, link.get("ID"))
        for w in self.wires.values():
            union(w[0].get("Connect1"), w[0].get("Connect2"))
        groups = defaultdict(set)
        for conn, port_id in connector_port.items():
            if port_id in self.ports and conn in parent:
                groups[find(conn)].add(self.port_label(port_id))
        return list(groups.values())


def check_file(path, expected: dict | None = None) -> dict:
    root = xmlio.load(path)
    c = Circuit(root)
    problems = []
    nets = c.electrical_nets()
    port_net = {m: net for net, members in nets.items() for m in members}
    groups = c.graphic_groups()
    for group in groups:
        members = {m for m in group if not m.startswith("0.")}
        owners = {port_net.get(m) for m in members}
        if len(owners) > 1:
            problems.append(f"Wires connect different nets together: {sorted(members)} belong to {sorted(o or '?' for o in owners)}")
    for net, members in nets.items():
        parts_ = [g for g in groups if g & members]
        if net != "0" and len(parts_) > 1:
            problems.append(f"Net {net} is split into {len(parts_)} pieces on the diagram: {[sorted(g) for g in parts_]}")
    if expected:
        for net, want in expected.items():
            have = nets.get(net.lower(), set())
            if want != have:
                problems.append(f"Net {net} should connect {sorted(want)}, but the file has {sorted(have)}")

    # Layout
    warnings = []
    diagram = next(root.iter("CIITDiagram"))
    width = float(diagram.get("PageWidth", "11")) * 96
    height = float(diagram.get("PageHeight", "8.5")) * 96
    boxes = {}
    for sym in c.symbols.values():
        comp = c.comps.get(sym[0].get("CiComponent"))
        if comp is None:
            continue
        box = symbol_bbox(sym)
        if box is None:
            continue
        name = xmlio.unasc(comp[0].get("LocalName"))
        if name == "0":
            name = f"GND@{box[0]:.0f},{box[1]:.0f}"
        boxes[name] = box
        if box[0] < 0 or box[1] < 0 or box[2] > width or box[3] > height:
            warnings.append(f"{name} is outside the sheet ({box[0]:.0f},{box[1]:.0f} – {box[2]:.0f},{box[3]:.0f}, sheet {width:.0f}×{height:.0f})")
    names = sorted(boxes)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            ax0, ay0, ax1, ay1 = boxes[a]
            bx0, by0, bx1, by1 = boxes[b]
            if min(ax1, bx1) - max(ax0, bx0) > 4 and min(ay1, by1) - max(ay0, by0) > 4:
                warnings.append(f"{a} overlaps {b}")
    segs = []
    for w in c.wires.values():
        node = c.nodes.get(w[0].get("Node"))
        net = xmlio.unasc(node[0].get("LocalName")).lower() if node is not None else "?"
        pts = [(float(p.get("X")), float(p.get("Y"))) for p in w[0].find("Points")]
        segs.extend((net, a, b) for a, b in zip(pts, pts[1:]))
    crossings = 0
    for i, (na, a, b) in enumerate(segs):
        for nb, p, q in segs[i + 1:]:
            if na != nb and _cross(a, b, p, q):
                crossings += 1
    through = set()
    for net, a, b in segs:
        for name, (x0, y0, x1, y1) in boxes.items():
            if _seg_in_box(a, b, (x0 + 6, y0 + 6, x1 - 6, y1 - 6)):
                through.add((net, name))
    for net, name in sorted(through):
        warnings.append(f"Wire of net {net} passes through {name}")
    if crossings:
        warnings.append(f"Wires of different nets cross in {crossings} places")
    return {"ok": not problems, "problems": problems, "warnings": warnings, "crossings": crossings,
            "nets": {k: sorted(v) for k, v in nets.items()}}


def _cross(a, b, p, q):
    """A horizontal and a vertical segment intersect in their interiors (touching endpoints do not count)."""
    if (a[1] == b[1]) == (p[1] == q[1]):
        return False
    (h1, h2), (v1, v2) = ((a, b), (p, q)) if a[1] == b[1] else ((p, q), (a, b))
    x, y = v1[0], h1[1]
    return (min(h1[0], h2[0]) < x < max(h1[0], h2[0])) and (min(v1[1], v2[1]) < y < max(v1[1], v2[1]))


def _seg_in_box(a, b, box):
    x0, y0, x1, y1 = box
    if x1 <= x0 or y1 <= y0:
        return False
    if a[1] == b[1]:
        return y0 < a[1] < y1 and max(min(a[0], b[0]), x0) < min(max(a[0], b[0]), x1)
    if a[0] == b[0]:
        return x0 < a[0] < x1 and max(min(a[1], b[1]), y0) < min(max(a[1], b[1]), y1)
    return False


def inspect(path) -> dict:
    """Read out circuit contents: components (reference designator, part, value), nets, and instrument panel settings."""
    root = xmlio.load(path)
    c = Circuit(root)
    comps = []
    for it in c.comps.values():
        comp = it[0]
        ident = identity(comp)
        name = xmlio.unasc(comp.get("LocalName"))
        if ident is None or name == "0":
            continue
        plist = comp.find(".//CiaParamList")
        value = None
        if plist is not None and ident[0] in ("RESISTOR_VIRTUAL", "CAPACITOR_VIRTUAL", "INDUCTOR_VIRTUAL", "DC_POWER"):
            params = plist.findall("./parameters/Item")
            value = xmlio.unasc(params[1].get("Value")) if len(params) > 1 else None
        comps.append({"refdes": name, "part": ident[0], "family": ident[1], "value": value})
    instruments = {}
    for node in root.iter("CSourceSymbolCollectNode"):
        long_name = xmlio.unasc(node.get("CompLongName") or "")
        if "#" not in long_name:
            continue
        settings = {}
        for el in node.iter("Element"):
            data = el.find("CDataElement")
            if data is not None and data.get("Data") not in (None, ""):
                settings[xmlio.unasc(el.get("Key"))] = data.get("Data")
        instruments[long_name.split("#")[0]] = settings
    return {"components": sorted(comps, key=lambda d: d["refdes"]), "nets": {k: sorted(v) for k, v in c.electrical_nets().items()},
            "instruments": instruments}
