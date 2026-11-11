"""Schematic routing: pin escape + obstacle-aware orthogonal A* + tree connection for multi-pin nets.

route / route_pins / pin_escape / simplify implement obstacle-aware orthogonal A* routing;
the tree connection (each pin attaches to the nearest point of the existing wire) is implemented here as well.
Coordinates are in Multisim schematic units (about 1/96 inch), y axis pointing down.
"""
import heapq
from bisect import bisect_left, bisect_right


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


def route(start, end, obstacles, *, occupied=None, dense=0):
    """A* on a sparse grid: turns and crossings with other nets cost extra, overlaps and passing through parts are forbidden.

    dense=1 inserts the midpoint of every grid interval, which finds corridors
    that the coarse grid (built only from part edges +-9) can miss.
    """
    occupied = occupied or []
    boxes = [rectangle(o) for o in obstacles]
    for p in (start, end):
        if any(x0 < p[0] < x1 and y0 < p[1] < y1 for x0, y0, x1, y1 in boxes):
            raise ValueError(f"routing endpoint is inside a part: {p}")
    if start == end:
        return [start]
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
    v_cols = sorted(v_by_col.items())

    xs.update((min(xs) - 18, max(xs) + 18))
    ys.update((min(ys) - 18, max(ys) + 18))
    xx, yy = sorted(xs), sorted(ys)
    if dense:
        xx = sorted(set(xx) | {(a + b) / 2 for a, b in zip(xx, xx[1:])})
        yy = sorted(set(yy) | {(a + b) / 2 for a, b in zip(yy, yy[1:])})
    nx_len, ny_len = len(xx), len(yy)
    # Precompute bitmasks of edges blocked by boxes, collinear occupied wires, and occupied segment endpoints.
    h_block = [0] * ny_len
    v_block = [0] * nx_len
    for x0, y0, x1, y1 in boxes:
        if x1 <= x0 or y1 <= y0:
            continue
        iy_lo = bisect_right(yy, y0)
        iy_hi = bisect_left(yy, y1)
        if iy_lo < iy_hi:
            ix_lo = max(0, bisect_right(xx, x0) - 1)
            ix_hi = min(nx_len - 1, bisect_left(xx, x1))
            if ix_lo < ix_hi:
                mask = ((1 << (ix_hi - ix_lo)) - 1) << ix_lo
                for yi in range(iy_lo, iy_hi):
                    h_block[yi] |= mask
        ix_lo_v = bisect_right(xx, x0)
        ix_hi_v = bisect_left(xx, x1)
        if ix_lo_v < ix_hi_v:
            iy_lo_v = max(0, bisect_right(yy, y0) - 1)
            iy_hi_v = min(ny_len - 1, bisect_left(yy, y1))
            if iy_lo_v < iy_hi_v:
                mask = ((1 << (iy_hi_v - iy_lo_v)) - 1) << iy_lo_v
                for xi in range(ix_lo_v, ix_hi_v):
                    v_block[xi] |= mask

    v_in_cell = [[] for _ in range(max(nx_len - 1, 0))]
    h_in_cell = [[] for _ in range(max(ny_len - 1, 0))]
    if occupied:
        x_idx = {x: i for i, x in enumerate(xx)}
        y_idx = {y: i for i, y in enumerate(yy)}
        for row_y, segs in h_rows:
            yi = y_idx.get(row_y)
            if yi is not None:
                for c, d in segs:
                    cx0, cx1 = (c[0], d[0]) if c[0] <= d[0] else (d[0], c[0])
                    ix_lo = max(0, bisect_left(xx, cx0) - 1)
                    ix_hi = min(nx_len - 1, bisect_right(xx, cx1))
                    if ix_lo < ix_hi:
                        h_block[yi] |= ((1 << (ix_hi - ix_lo)) - 1) << ix_lo
            # Pre-index this row into y-intervals [yy[yi], yy[yi+1]] it can cross/touch
            iy_start = max(0, bisect_left(yy, row_y) - 1)
            iy_stop = min(ny_len - 1, bisect_right(yy, row_y))
            for yi in range(iy_start, iy_stop):
                h_in_cell[yi].extend(segs)
        for col_x, segs in v_cols:
            xi = x_idx.get(col_x)
            if xi is not None:
                for c, d in segs:
                    cy0, cy1 = (c[1], d[1]) if c[1] <= d[1] else (d[1], c[1])
                    iy_lo = max(0, bisect_left(yy, cy0) - 1)
                    iy_hi = min(ny_len - 1, bisect_right(yy, cy1))
                    if iy_lo < iy_hi:
                        v_block[xi] |= ((1 << (iy_hi - iy_lo)) - 1) << iy_lo
            ix_start = max(0, bisect_left(xx, col_x) - 1)
            ix_stop = min(nx_len - 1, bisect_right(xx, col_x))
            for xi in range(ix_start, ix_stop):
                v_in_cell[xi].extend(segs)

    first = (bisect_left(xx, start[0]), bisect_left(yy, start[1]), 0)
    target = (bisect_left(xx, end[0]), bisect_left(yy, end[1]))
    target_x, target_y = target
    dist, parent = {first: 0.0}, {}
    queue = [(0.0, 0.0, first)]
    inf = float("inf")
    stride_h = max(nx_len - 1, 1)
    h_cache = [-1.0] * (ny_len * stride_h) if occupied else None
    stride_v = max(ny_len - 1, 1)
    v_cache = [-1.0] * (nx_len * stride_v) if occupied else None

    def occ_edge_cost(a, b, local, ax_len):
        crossings = 0
        end_touch = a == start or a == end or b == start or b == end
        for c, d in local:
            rel = segment_relation(a, b, c, d)
            if not rel:
                continue
            if rel == "overlap":
                return inf
            if rel == "touch" and (segment_relation(c, c, a, b) or segment_relation(d, d, a, b)):
                return inf
            if end_touch and (segment_relation(start, start, c, d) or segment_relation(end, end, c, d)):
                return inf
            crossings += 1
        return ax_len + crossings * 90

    heappop, heappush = heapq.heappop, heapq.heappush
    dist_get = dist.get
    end_x, end_y = end
    while queue:
        _, d0, cur = heappop(queue)
        if d0 != dist_get(cur):
            continue
        x, y, direction = cur
        if x == target_x and y == target_y:
            pts = []
            while True:
                pts.append((xx[cur[0]], yy[cur[1]]))
                if cur == first:
                    break
                cur = parent[cur]
            return simplify(pts[::-1])
        turn_h = 18.0 if direction and direction != 1 else 0.0
        turn_v = 18.0 if direction and direction != 2 else 0.0
        h_row_mask = h_block[y]
        v_col_mask = v_block[x]
        yy_y = yy[y]
        xx_x = xx[x]
        # Horizontal neighbors: (x-1, y) via edge x-1, (x+1, y) via edge x
        if x > 0 and not ((h_row_mask >> (x - 1)) & 1):
            ex = x - 1
            if h_cache is not None:
                idx = y * stride_h + ex
                w = h_cache[idx]
                if w < 0.0:
                    local = v_in_cell[ex]
                    w = (xx_x - xx[ex]) if not local else occ_edge_cost((xx[ex], yy_y), (xx_x, yy_y), local, xx_x - xx[ex])
                    h_cache[idx] = w
            else:
                w = xx_x - xx[ex]
            val = d0 + w + turn_h
            if val < dist_get((ex, y, 1), inf):
                new = (ex, y, 1)
                dist[new], parent[new] = val, cur
                heappush(queue, (val + abs(xx[ex] - end_x) + abs(yy_y - end_y), val, new))
        if x + 1 < nx_len and not ((h_row_mask >> x) & 1):
            nx_i = x + 1
            if h_cache is not None:
                idx = y * stride_h + x
                w = h_cache[idx]
                if w < 0.0:
                    local = v_in_cell[x]
                    w = (xx[nx_i] - xx_x) if not local else occ_edge_cost((xx_x, yy_y), (xx[nx_i], yy_y), local, xx[nx_i] - xx_x)
                    h_cache[idx] = w
            else:
                w = xx[nx_i] - xx_x
            val = d0 + w + turn_h
            if val < dist_get((nx_i, y, 1), inf):
                new = (nx_i, y, 1)
                dist[new], parent[new] = val, cur
                heappush(queue, (val + abs(xx[nx_i] - end_x) + abs(yy_y - end_y), val, new))
        # Vertical neighbors: (x, y-1) via edge y-1, (x, y+1) via edge y
        if y > 0 and not ((v_col_mask >> (y - 1)) & 1):
            ey = y - 1
            if v_cache is not None:
                idx = x * stride_v + ey
                w = v_cache[idx]
                if w < 0.0:
                    local = h_in_cell[ey]
                    w = (yy_y - yy[ey]) if not local else occ_edge_cost((xx_x, yy[ey]), (xx_x, yy_y), local, yy_y - yy[ey])
                    v_cache[idx] = w
            else:
                w = yy_y - yy[ey]
            val = d0 + w + turn_v
            if val < dist_get((x, ey, 2), inf):
                new = (x, ey, 2)
                dist[new], parent[new] = val, cur
                heappush(queue, (val + abs(xx_x - end_x) + abs(yy[ey] - end_y), val, new))
        if y + 1 < ny_len and not ((v_col_mask >> y) & 1):
            ny_i = y + 1
            if v_cache is not None:
                idx = x * stride_v + y
                w = v_cache[idx]
                if w < 0.0:
                    local = h_in_cell[y]
                    w = (yy[ny_i] - yy_y) if not local else occ_edge_cost((xx_x, yy_y), (xx_x, yy[ny_i]), local, yy[ny_i] - yy_y)
                    v_cache[idx] = w
            else:
                w = yy[ny_i] - yy_y
            val = d0 + w + turn_v
            if val < dist_get((x, ny_i, 2), inf):
                new = (x, ny_i, 2)
                dist[new], parent[new] = val, cur
                heappush(queue, (val + abs(xx_x - end_x) + abs(yy[ny_i] - end_y), val, new))
    raise ValueError(f"no obstacle-free path from {start} to {end}")


def route_pins(start, end, obstacles, occupied=None):
    """Route pin to pin, escalating before giving up.

    The pin must leave the part body along its direction. The first clearance may
    land in a neighbouring part, so the same attempt is repeated with a longer
    escape, then on a denser A* grid. Only when every combination fails does the
    caller hear a reason.
    """
    a, b = (float(start["x"]), float(start["y"])), (float(end["x"]), float(end["y"]))
    first_error = None
    for clearance in (9, 18, 27, 40):
        ea, eb = pin_escape(start, obstacles, clearance), pin_escape(end, obstacles, clearance)
        bad = False
        for p, e, conn in ((a, ea, start), (b, eb, end)):
            if any(o.get("owner", o.get("refdes")) != conn.get("refdes") and crosses_box(p, e, rectangle(o)) for o in obstacles):
                first_error = first_error or "pin escape crosses another part"
                bad = True
                break
            if any(segment_relation(p, e, c, d) == "overlap" for c, d in occupied or []):
                first_error = first_error or "pin escape overlaps another net"
                bad = True
                break
        if bad:
            continue
        for dense in (False, True):
            try:
                return simplify([a, *route(ea, eb, obstacles, occupied=occupied, dense=dense), b])
            except ValueError as exc:
                first_error = first_error or str(exc)
    raise ValueError(first_error or "no obstacle-free path")


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
        all_own = own_segments()

        def try_attach(ep_p, dense_flag, max_opts):
            if ep_p != pin_pt[p]:
                owner = conns[p].get("refdes")
                if any(o.get("owner", o.get("refdes")) != owner and crosses_box(pin_pt[p], ep_p, rectangle(o)) for o in obstacles):
                    return None
                if any(segment_relation(pin_pt[p], ep_p, c, d) == "overlap" for c, d in occupied_other + all_own):
                    return None
            pts_by_t, seen_t = [], set()
            if ep_p == esc[p]:
                raw_opts = cands[p]
            else:
                raw_opts = []
                for ei, e in enumerate(edges):
                    pts = e["path"]
                    last = len(pts) - 2
                    for si in range(len(pts) - 1):
                        a, b = pts[si], pts[si + 1]
                        if si == 0 and e["a"][0] == "pin":
                            ea = esc[e["a"][1]]
                            a = ea if _on_segment(ea, a, b) else b
                        if si == last and e["b"][0] == "pin":
                            eb = esc[e["b"][1]]
                            b = eb if _on_segment(eb, a, b) else a
                        cand = _closest_on_segment(a, b, ep_p)
                        raw_opts.append((_manhattan(ep_p, cand), ei, si, cand))
            for c in sorted(raw_opts):
                if c[3] not in seen_t:
                    seen_t.add(c[3])
                    pts_by_t.append(c)
            found = None
            for _, ei, si, t in pts_by_t[:max_opts]:
                occ = occupied_other + [s for s in all_own if not _on_segment(t, *s)]
                try:
                    tail = route(ep_p, t, obstacles, occupied=occ, dense=dense_flag)
                except ValueError:
                    continue
                cost = _path_cost([pin_pt[p], *tail], occupied_other)
                if found is None or cost < found[0]:
                    found = (cost, ei, si, t, tail, ep_p)
            return found

        best = try_attach(esc[p], 0, 4)
        if best is None:
            for clearance in (9, 18, 27, 40):
                ep_p = esc[p] if clearance == 9 else pin_escape(conns[p], boxes, clearance)
                for dense_flag in ((1,) if clearance == 9 else (0, 1)):
                    best = try_attach(ep_p, dense_flag, 3)
                    if best is not None:
                        break
                if best is not None:
                    break
        if best is None:
            options = sorted({c[3]: c for c in cands[p]}.values())
            _, ei, si, t = options[0]
            tail = _elbow(esc[p], t)
            failures.append(f"pin {p}: no obstacle-free path to the existing wire")
        else:
            _, ei, si, t, tail, ep_p = best
            esc[p] = ep_p
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

    base_obstacles = list(boxes) + list(labels)
    pin_boxes = []
    for other in connections.values():
        for pin in other:
            ex, ey = pin_escape(pin, boxes)
            px, py = float(pin["x"]), float(pin["y"])
            pin_boxes.append((id(pin), {"refdes": "__pin__", "x": min(px, ex) - 3, "y": min(py, ey) - 3,
                                       "width": abs(px - ex) + 6, "height": abs(py - ey) + 6}))

    def one_pass(order):
        result, occupied, failures = [], [], []
        for name, conns in order:
            mine = {id(c) for c in conns}
            obstacles = base_obstacles + [b for pid, b in pin_boxes if pid not in mine]
            edges, junctions, fb = route_island(conns, obstacles, boxes, occupied)
            failures += [{"net": name, "reason": reason} for reason in fb]
            for e in edges:
                occupied.extend(_segments(e["path"]))
            result.append((name, conns, edges, junctions))
        return result, failures

    # Rip-up and retry: earlier nets occupy space, so a later one may fail only
    # because of the order. Re-run with the failing nets first, then reversed,
    # and keep the pass with the fewest failures.
    order = list(islands)
    best = None
    for attempt in range(3):
        result, failures = one_pass(order)
        if best is None or len(failures) < len(best[1]):
            best = (result, failures)
        if not failures:
            return result, []
        if attempt == 1 and len(failures) == 1 and order and order[0][0] == failures[0]["net"] and sum(1 for n, _ in order if n == failures[0]["net"]) == 1:
            break                              # sole failing island already failed even with zero prior wires
        failed = {f["net"] for f in failures}
        order = [i for i in order if i[0] in failed] + [i for i in order if i[0] not in failed]
        if attempt == 1:
            order = list(reversed(order))
    return best
