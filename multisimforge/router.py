"""Schematic routing: pin escape + obstacle-aware orthogonal A* + tree connection for multi-pin nets.

route / route_pins / pin_escape / simplify implement obstacle-aware orthogonal A* routing;
the tree connection (each pin attaches to the nearest point of the existing wire) is implemented here as well.
Coordinates are in Multisim schematic units (about 1/96 inch), y axis pointing down.
"""
import heapq
from bisect import bisect_left, bisect_right
from functools import lru_cache


class RouteError(ValueError):
    """One or more nets have no obstacle-free route (degraded wiring is not acceptable)."""

    def __init__(self, failures):
        self.failures = list(failures)
        nets = sorted({f["net"] for f in self.failures})
        detail = "; ".join(f"{f['net']}: {f['reason']}" for f in self.failures[:6])
        more = "" if len(self.failures) <= 6 else f" (+{len(self.failures) - 6} more)"
        super().__init__(
            f"no obstacle-free route for {len(nets)} net(s): {detail}{more}. "
            f"Move parts to leave routing channels, or pass --allow-fallback to accept degraded wiring")


def rectangle(item):
    x, y = float(item["x"]), float(item["y"])
    return x, y, x + float(item.get("width", 126)), y + float(item.get("height", 108))


def crosses_box(a, b, box):
    x0, y0, x1, y1 = box
    if a[1] == b[1]:
        return y0 < a[1] < y1 and max(min(a[0], b[0]), x0) < min(max(a[0], b[0]), x1)
    if a[0] == b[0]:
        return x0 < a[0] < x1 and max(min(a[1], b[1]), y0) < min(max(a[1], b[1]), y1)
    raise ValueError("segment is neither horizontal nor vertical")


def segment_relation(a, b, c, d):
    """Relationship between two segments: overlap / touch / crossing / None."""
    ah, ch = a[1] == b[1], c[1] == d[1]
    if ah == ch:
        fixed, axis = (1, 0) if ah else (0, 1)
        if a[fixed] != c[fixed]:
            return None
        lo = max(min(a[axis], b[axis]), min(c[axis], d[axis]))
        hi = min(max(a[axis], b[axis]), max(c[axis], d[axis]))
        return "overlap" if lo < hi else "touch" if lo == hi else None
    h1, h2, v1, v2 = (a, b, c, d) if ah else (c, d, a, b)
    if min(h1[0], h2[0]) <= v1[0] <= max(h1[0], h2[0]) and min(v1[1], v2[1]) <= h1[1] <= max(v1[1], v2[1]):
        p = (v1[0], h1[1])
        return "touch" if p in (a, b, c, d) else "crossing"
    return None


def pin_escape(conn, obstacles, clearance=9):
    """Step away from the part body along the pin direction to reach a point where routing can start."""
    p = (float(conn["x"]), float(conn["y"]))
    owner = next((o for o in obstacles if o.get("refdes") == conn.get("refdes")), None)
    if owner is None:
        return p
    x0, y0, x1, y1 = rectangle(owner)
    direction = conn.get("direction")
    if direction:
        dx, dy = direction
        if dx:
            return (x1 + clearance if dx > 0 else x0 - clearance, p[1])
        if dy:
            return (p[0], y1 + clearance if dy > 0 else y0 - clearance)
    choices = [(abs(p[0] - x0), (x0 - clearance, p[1])), (abs(p[0] - x1), (x1 + clearance, p[1])),
               (abs(p[1] - y0), (p[0], y0 - clearance)), (abs(p[1] - y1), (p[0], y1 + clearance))]
    return min(choices, key=lambda c: c[0])[1]


def simplify(points):
    """Drop duplicate points and collinear intermediate points (including spikes left by backtracking)."""
    out = []
    for p in points:
        if out and p == out[-1]:
            continue
        while len(out) > 1 and ((out[-2][0] == out[-1][0] == p[0]) or (out[-2][1] == out[-1][1] == p[1])):
            out.pop()
        if out and p == out[-1]:
            continue
        out.append(p)
    return out


def route(start, end, obstacles, *, occupied=None):
    """A* on a sparse grid: turns and crossings with other nets cost extra, overlaps and passing through parts are forbidden."""
    occupied = occupied or []
    boxes = [rectangle(o) for o in obstacles]
    for p in (start, end):
        if any(x0 < p[0] < x1 and y0 < p[1] < y1 for x0, y0, x1, y1 in boxes):
            raise ValueError(f"routing endpoint is inside a part: {p}")
    xs, ys = {start[0], end[0]}, {start[1], end[1]}
    for x0, y0, x1, y1 in boxes:
        xs.update((x0 - 9, x1 + 9))
        ys.update((y0 - 9, y1 + 9))
    for a, b in occupied:
        if a[0] == b[0]:
            xs.update((a[0] - 9, a[0] + 9))
        else:
            ys.update((a[1] - 9, a[1] + 9))
    h_by_row, v_by_col = {}, {}
    for a, b in occupied:
        (v_by_col.setdefault(a[0], []) if a[0] == b[0] else h_by_row.setdefault(a[1], [])).append((a, b))
    h_rows = sorted(h_by_row.items())
    h_ys = [y for y, _ in h_rows]
    v_cols = sorted(v_by_col.items())
    v_xs = [x for x, _ in v_cols]

    def in_range(indexed, coords, lo, hi):
        lo, hi = min(lo, hi), max(lo, hi)
        out = []
        for _, segs in indexed[bisect_left(coords, lo):bisect_right(coords, hi)]:
            out.extend(segs)
        return out

    xs.update((min(xs) - 18, max(xs) + 18))
    ys.update((min(ys) - 18, max(ys) + 18))
    xx, yy = sorted(xs), sorted(ys)
    first = (bisect_left(xx, start[0]), bisect_left(yy, start[1]), 0)
    target = (bisect_left(xx, end[0]), bisect_left(yy, end[1]))
    dist, parent = {first: 0.0}, {}
    queue = [(0.0, 0.0, first)]

    @lru_cache(maxsize=None)
    def cost(x, y, nx, ny):
        a, b = (xx[x], yy[y]), (xx[nx], yy[ny])
        if any(crosses_box(a, b, box) for box in boxes):
            return float("inf")
        if a[1] == b[1]:
            local = list(h_by_row.get(a[1], ())) + in_range(v_cols, v_xs, a[0], b[0])
        else:
            local = list(v_by_col.get(a[0], ())) + in_range(h_rows, h_ys, a[1], b[1])
        crossings = 0
        for c, d in local:
            rel = segment_relation(a, b, c, d)
            if rel == "overlap":
                return float("inf")
            if rel == "touch" and (segment_relation(c, c, a, b) or segment_relation(d, d, a, b)):
                return float("inf")          # never through a node or bend of another net
            if rel and (a in (start, end) or b in (start, end)):
                if segment_relation(start, start, c, d) or segment_relation(end, end, c, d):
                    return float("inf")      # endpoints must not land on another net
            crossings += bool(rel)
        return abs(a[0] - b[0]) + abs(a[1] - b[1]) + crossings * 90

    while queue:
        _, d0, cur = heapq.heappop(queue)
        if d0 != dist.get(cur):
            continue
        x, y, direction = cur
        if (x, y) == target:
            pts = []
            while True:
                pts.append((xx[cur[0]], yy[cur[1]]))
                if cur == first:
                    break
                cur = parent[cur]
            return simplify(pts[::-1])
        for nx, ny, nd in ((x - 1, y, 1), (x + 1, y, 1), (x, y - 1, 2), (x, y + 1, 2)):
            if not (0 <= nx < len(xx) and 0 <= ny < len(yy)):
                continue
            new = (nx, ny, nd)
            value = d0 + cost(x, y, nx, ny) + (18 if direction and direction != nd else 0)
            if value < dist.get(new, float("inf")):
                dist[new], parent[new] = value, cur
                heapq.heappush(queue, (value + abs(xx[nx] - end[0]) + abs(yy[ny] - end[1]), value, new))
    raise ValueError(f"no obstacle-free path from {start} to {end}")


def route_pins(start, end, obstacles, occupied=None):
    a, b = (float(start["x"]), float(start["y"])), (float(end["x"]), float(end["y"]))
    ea, eb = pin_escape(start, obstacles), pin_escape(end, obstacles)
    for p, e, conn in ((a, ea, start), (b, eb, end)):
        if any(o.get("owner", o.get("refdes")) != conn.get("refdes") and crosses_box(p, e, rectangle(o)) for o in obstacles):
            raise ValueError("pin escape crosses another part")
        if any(segment_relation(p, e, c, d) == "overlap" for c, d in occupied or []):
            raise ValueError("pin escape overlaps another net")
    return simplify([a, *route(ea, eb, obstacles, occupied=occupied), b])


# ---------------------------------------------------------------------------
# Tree connection
# ---------------------------------------------------------------------------
def _manhattan(p, q):
    return abs(p[0] - q[0]) + abs(p[1] - q[1])


def _closest_on_segment(a, b, q):
    if a[1] == b[1]:
        lo, hi = sorted((a[0], b[0]))
        return (min(max(q[0], lo), hi), a[1])
    lo, hi = sorted((a[1], b[1]))
    return (a[0], min(max(q[1], lo), hi))


def _on_segment(p, a, b):
    return (min(a[0], b[0]) <= p[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])
            and (a[0] == b[0] == p[0] or a[1] == b[1] == p[1]))


def _segments(path):
    return list(zip(path, path[1:]))


def _elbow(start, end):
    if start[0] == end[0] or start[1] == end[1]:
        return [start, end]
    return [start, (end[0], start[1]), end]


def _path_cost(path, occupied):
    """Length + 18 per turn + 90 per crossing with another net (matches the A* cost)."""
    path = simplify(path)
    length = sum(_manhattan(a, b) for a, b in zip(path, path[1:]))
    crossings = sum(1 for a, b in zip(path, path[1:]) for c, d in occupied if segment_relation(a, b, c, d) == "crossing")
    return length + 18 * max(0, len(path) - 2) + 90 * crossings


def route_island(conns, obstacles, boxes, occupied_other):
    """Connect a group of pins into a tree: join the closest pair first, then attach each remaining pin to the nearest point of the existing wire.

    Returns (edges, junctions, fallback count); edge = {"a": ("pin", i)|("j", k), "b": ..., "path": [...]}.
    """
    pin_pt = [(float(c["x"]), float(c["y"])) for c in conns]
    esc = [pin_escape(c, boxes) for c in conns]
    edges, junctions, failures = [], [], []
    n = len(conns)

    def own_segments(exclude=None):
        return [s for e in edges for s in _segments(e["path"]) if exclude is None or not _on_segment(exclude, *s)]

    i, j = min(((i, j) for i in range(n) for j in range(i + 1, n)), key=lambda ij: _manhattan(esc[ij[0]], esc[ij[1]]))
    try:
        path = route_pins(conns[i], conns[j], obstacles, occupied_other)
    except ValueError as exc:
        path = simplify([pin_pt[i], *_elbow(esc[i], esc[j]), pin_pt[j]])
        failures.append(f"pins {i}-{j}: {exc}" or "no obstacle-free path")
    edges.append({"a": ("pin", i), "b": ("pin", j), "path": path})
    done = {i, j}
    while len(done) < n:
        cands = {}
        for p in range(n):
            if p in done:
                continue
            for ei, e in enumerate(edges):
                pts = e["path"]
                last = len(pts) - 2
                for si in range(len(pts) - 1):
                    a, b = pts[si], pts[si + 1]
                    # Never attach to the short escape stub at a pin: that segment may only be joined beyond the escape point
                    if si == 0 and e["a"][0] == "pin":
                        ea = esc[e["a"][1]]
                        a = ea if _on_segment(ea, a, b) else b
                    if si == last and e["b"][0] == "pin":
                        eb = esc[e["b"][1]]
                        b = eb if _on_segment(eb, a, b) else a
                    cand = _closest_on_segment(a, b, esc[p])
                    cands.setdefault(p, []).append((_manhattan(esc[p], cand), ei, si, cand))
        # Attach the pin nearest to the existing wire first; among its nearest access points, route for real and take the cheapest in length, turns and crossings
        p = min(cands, key=lambda k: min(c[0] for c in cands[k]))
        options, seen = [], set()
        for c in sorted(cands[p]):
            if c[3] not in seen:
                seen.add(c[3])
                options.append(c)
        best = None
        for _, ei, si, t in options[:4]:
            try:
                tail = route(esc[p], t, obstacles, occupied=occupied_other + own_segments(exclude=t))
            except ValueError:
                continue
            cost = _path_cost([pin_pt[p], *tail], occupied_other)
            if best is None or cost < best[0]:
                best = (cost, ei, si, t, tail)
        if best is None:
            _, ei, si, t = options[0]
            tail = _elbow(esc[p], t)
            failures.append(f"pin {p}: no obstacle-free path to the existing wire")
        else:
            _, ei, si, t, tail = best
        new_path = simplify([pin_pt[p], *tail])
        e = edges[ei]
        pts = e["path"]
        if t == pts[0] and e["a"][0] == "j":
            jk = e["a"][1]
        elif t == pts[-1] and e["b"][0] == "j":
            jk = e["b"][1]
        else:
            junctions.append(t)
            jk = len(junctions) - 1
            edges[ei] = {"a": e["a"], "b": ("j", jk), "path": simplify(pts[:si + 1] + [t])}
            edges.append({"a": ("j", jk), "b": e["b"], "path": simplify([t] + pts[si + 1:])})
        edges.append({"a": ("pin", p), "b": ("j", jk), "path": new_path})
        done.add(p)
    return edges, junctions, failures


def route_all(connections, boxes, labels=()):
    """connections: {net: [pin, ...]}, pin = {"x","y","refdes","direction","ground"}; labels are label rectangles the wires route around.

    Ground nets are split into islands per ground symbol; each ground pin connects to its nearest symbol;
    short nets are routed first (less room to detour), long ones later and around them.
    Returns ([(net, pin group, edges, junctions)], [{"net", "reason"} for every degraded wire]).
    """
    islands = []
    for name, conns in connections.items():
        if len(conns) < 2:
            continue
        gnds = [c for c in conns if c.get("ground")] if name == "0" else []
        if len(gnds) > 1:
            groups = {id(g): [g] for g in gnds}
            for c in conns:
                if not c.get("ground"):
                    g = min(gnds, key=lambda g: _manhattan((g["x"], g["y"]), (c["x"], c["y"])))
                    groups[id(g)].append(c)
            lonely = [grp for grp in groups.values() if len(grp) == 1]
            full = [grp for grp in groups.values() if len(grp) > 1] or [lonely.pop()]
            for grp in lonely:            # ground symbols with no pins join the nearest island
                g = grp[0]
                min(full, key=lambda f: min(_manhattan((g["x"], g["y"]), (c["x"], c["y"])) for c in f)).append(g)
            islands.extend((name, grp) for grp in full)
        else:
            islands.append((name, conns))

    def span(item):
        xs = [c["x"] for c in item[1]]
        ys = [c["y"] for c in item[1]]
        return (max(xs) - min(xs)) + (max(ys) - min(ys))

    islands.sort(key=span)
    result, occupied, failures = [], [], []
    for name, conns in islands:
        mine = {id(c) for c in conns}
        obstacles = list(boxes) + list(labels)
        for other in connections.values():          # leave escape channels for the other pins
            for pin in other:
                if id(pin) in mine:
                    continue
                ex, ey = pin_escape(pin, boxes)
                px, py = float(pin["x"]), float(pin["y"])
                obstacles.append({"refdes": "__pin__", "x": min(px, ex) - 3, "y": min(py, ey) - 3,
                                  "width": abs(px - ex) + 6, "height": abs(py - ey) + 6})
        edges, junctions, fb = route_island(conns, obstacles, boxes, occupied)
        failures += [{"net": name, "reason": reason} for reason in fb]
        for e in edges:
            occupied.extend(_segments(e["path"]))
        result.append((name, conns, edges, junctions))
    return result, failures