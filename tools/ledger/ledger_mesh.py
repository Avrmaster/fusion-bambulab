"""Mesh work of the print ledger. Standard library only.

Identity (geom_hash, shape_id, read_stl ...) is a thin re-export of tools/bambu/meshid.py, so the ledger and the gate
share one definition. Vendored flat to bambulab/tools/ in a project (meshid.py lies beside it there); in the plugin
meshid.py is in ../bambu.

Three tests of its own, all SAMPLED and all on STLs that share one frame (the model frame the parts were exported in):

    change_region(a, b)   where two versions of one part differ: boxes round the surface of one that is not on the other
    mating_zone(a, b)     where the surface of A comes within `near` mm of B: boxes
    fit(a, b)             smallest gap from A to B inside the mating zone and how many sample points of A lie inside B

They work on the meshes as modelled. fit() against an as-printed copy is still model to model: it knows neither
shrink, nor hole compensation, nor the wall generator of the slicer (FIT_LABEL is printed with every result).
A mesh argument is a path of an STL or a (vertices, triangles) pair."""
import math, os, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (_HERE, os.path.join(_HERE, '..', 'bambu')):
    if os.path.isfile(os.path.join(_d, 'meshid.py')) and _d not in sys.path:
        sys.path.append(_d)
from meshid import QUANTUM, read_stl, quantize, geom_hash, vset_hash, shape_id, same_shape, mesh_ids, stl_ids   # noqa: E402,F401

FIT_LABEL = 'model to model, sampled: it knows neither shrink, nor hole compensation, nor slicer settings'
STEP = 1.0             # mm between sample points on a triangle
BUDGET = 40000         # sample points per call; with more the step grows and the result says which step was used
CHANGE_TOL = 0.05      # mm: a sample point farther than this from the other surface is a change
NEAR = 1.0             # mm: two surfaces closer than this are a mating zone
MERGE = 3.0            # mm: sample points closer than about this end up in one box
_BIG = 4096            # a triangle that would lie in more grid cells than this is tested on every query instead
_EPS = 0.02            # mm: a point this close to a surface is on it, not inside


def load(mesh):
    """(vertices, triangles) of a path or of a pair that already is one."""
    if isinstance(mesh, (str, bytes)) or hasattr(mesh, '__fspath__'):
        return read_stl(mesh)
    return mesh[0], mesh[1]


def bbox(vertices):
    """[x0, y0, z0, x1, y1, z1] of a list of points; None for an empty list."""
    if not vertices:
        return None
    xs, ys, zs = zip(*vertices)
    return [min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)]


def boxes_touch(a, b, margin=0.0):
    """Do two boxes [x0, y0, z0, x1, y1, z1] overlap when each is grown by `margin`?"""
    return all(a[i] - margin <= b[i + 3] + margin and b[i] - margin <= a[i + 3] + margin for i in range(3))


def any_touch(boxes_a, boxes_b, margin=0.0):
    """Does any box of one list touch any box of the other?"""
    return any(boxes_touch(a, b, margin) for a in boxes_a for b in boxes_b)


def _point_triangle(p, a, b, c):
    """Distance from point p to triangle a b c (closest point by Voronoi regions)."""
    px, py, pz = p
    ax, ay, az = a
    abx, aby, abz = b[0] - ax, b[1] - ay, b[2] - az
    acx, acy, acz = c[0] - ax, c[1] - ay, c[2] - az
    apx, apy, apz = px - ax, py - ay, pz - az
    d1 = abx * apx + aby * apy + abz * apz
    d2 = acx * apx + acy * apy + acz * apz
    if d1 <= 0 and d2 <= 0:
        return math.sqrt(apx * apx + apy * apy + apz * apz)
    bpx, bpy, bpz = px - b[0], py - b[1], pz - b[2]
    d3 = abx * bpx + aby * bpy + abz * bpz
    d4 = acx * bpx + acy * bpy + acz * bpz
    if d3 >= 0 and d4 <= d3:
        return math.sqrt(bpx * bpx + bpy * bpy + bpz * bpz)
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        t = d1 / (d1 - d3) if d1 != d3 else 0.0
        qx, qy, qz = apx - t * abx, apy - t * aby, apz - t * abz
        return math.sqrt(qx * qx + qy * qy + qz * qz)
    cpx, cpy, cpz = px - c[0], py - c[1], pz - c[2]
    d5 = abx * cpx + aby * cpy + abz * cpz
    d6 = acx * cpx + acy * cpy + acz * cpz
    if d6 >= 0 and d5 <= d6:
        return math.sqrt(cpx * cpx + cpy * cpy + cpz * cpz)
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        t = d2 / (d2 - d6) if d2 != d6 else 0.0
        qx, qy, qz = apx - t * acx, apy - t * acy, apz - t * acz
        return math.sqrt(qx * qx + qy * qy + qz * qz)
    va = d3 * d6 - d5 * d4
    if va <= 0 and (d4 - d3) >= 0 and (d5 - d6) >= 0:
        den = (d4 - d3) + (d5 - d6)
        t = (d4 - d3) / den if den else 0.0
        qx, qy, qz = bpx - t * (c[0] - b[0]), bpy - t * (c[1] - b[1]), bpz - t * (c[2] - b[2])
        return math.sqrt(qx * qx + qy * qy + qz * qz)
    nx, ny, nz = aby * acz - abz * acy, abz * acx - abx * acz, abx * acy - aby * acx
    n = math.sqrt(nx * nx + ny * ny + nz * nz)
    if n == 0.0:                                   # a needle: the regions above did not catch it
        return math.sqrt(min(apx * apx + apy * apy + apz * apz, bpx * bpx + bpy * bpy + bpz * bpz, cpx * cpx + cpy * cpy + cpz * cpz))
    return abs(apx * nx + apy * ny + apz * nz) / n


class Grid(object):
    """Triangles of one mesh in a uniform grid: the distance from a point to the surface, and "is this point inside"."""

    def __init__(self, vertices, triangles, cell=None):
        self.tris = [(vertices[a], vertices[b], vertices[c]) for a, b, c in triangles]
        self.box = bbox(vertices)
        if cell is None:
            area = 0.0
            for a, b, c in self.tris:
                ux, uy, uz, vx, vy, vz = b[0] - a[0], b[1] - a[1], b[2] - a[2], c[0] - a[0], c[1] - a[1], c[2] - a[2]
                area += math.sqrt((uy * vz - uz * vy) ** 2 + (uz * vx - ux * vz) ** 2 + (ux * vy - uy * vx) ** 2) / 2
            cell = 2.0 * math.sqrt(area / len(self.tris)) if self.tris and area > 0 else 1.0
        self.cell = max(cell, 0.25)
        self.cells, self.big, self.columns, self.big_columns = {}, [], {}, []
        inv = 1.0 / self.cell
        for i, (a, b, c) in enumerate(self.tris):
            lo = [int(math.floor(min(a[k], b[k], c[k]) * inv)) for k in range(3)]
            hi = [int(math.floor(max(a[k], b[k], c[k]) * inv)) for k in range(3)]
            nxy = (hi[0] - lo[0] + 1) * (hi[1] - lo[1] + 1)
            if nxy > _BIG:
                self.big_columns.append(i)
            else:
                for x in range(lo[0], hi[0] + 1):
                    for y in range(lo[1], hi[1] + 1):
                        self.columns.setdefault((x, y), []).append(i)
            if nxy * (hi[2] - lo[2] + 1) > _BIG:
                self.big.append(i)
                continue
            for x in range(lo[0], hi[0] + 1):
                for y in range(lo[1], hi[1] + 1):
                    for z in range(lo[2], hi[2] + 1):
                        self.cells.setdefault((x, y, z), []).append(i)

    def distance(self, p, cap):
        """Distance from p to the surface, or None when it is more than `cap`."""
        inv = 1.0 / self.cell
        lo = [int(math.floor((p[k] - cap) * inv)) for k in range(3)]
        hi = [int(math.floor((p[k] + cap) * inv)) for k in range(3)]
        best, seen, tris, cells = None, set(self.big), self.tris, self.cells
        for i in self.big:
            d = _point_triangle(p, *tris[i])
            if d <= cap and (best is None or d < best): best = d
        for x in range(lo[0], hi[0] + 1):
            for y in range(lo[1], hi[1] + 1):
                for z in range(lo[2], hi[2] + 1):
                    for i in cells.get((x, y, z), ()):
                        if i in seen: continue
                        seen.add(i)
                        d = _point_triangle(p, *tris[i])
                        if d <= cap and (best is None or d < best): best = d
        return best

    def inside(self, p):
        """Is p inside the closed mesh: the parity of a ray along +z. The ray is moved a hair off the grid of the model,
        so that it does not run exactly through an edge shared by two triangles."""
        x, y, z = p[0] + 1.37e-6, p[1] + 2.11e-6, p[2]
        inv = 1.0 / self.cell
        hits = 0
        cand = self.columns.get((int(math.floor(x * inv)), int(math.floor(y * inv))), ())
        for i in list(cand) + self.big_columns:
            a, b, c = self.tris[i]
            d1 = (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0])
            d2 = (c[0] - b[0]) * (y - b[1]) - (c[1] - b[1]) * (x - b[0])
            d3 = (a[0] - c[0]) * (y - c[1]) - (a[1] - c[1]) * (x - c[0])
            if not ((d1 > 0 and d2 > 0 and d3 > 0) or (d1 < 0 and d2 < 0 and d3 < 0)):
                continue
            den = d1 + d2 + d3                       # twice the signed area of the projection
            if den == 0.0:
                continue
            zt = (d2 * a[2] + d3 * b[2] + d1 * c[2]) / den
            if zt > z:
                hits += 1
        return hits % 2 == 1


def _subdivisions(tri, step):
    a, b, c = tri
    e = max(math.sqrt(sum((p[k] - q[k]) ** 2 for k in range(3))) for p, q in ((a, b), (b, c), (c, a)))
    return max(1, min(64, int(math.ceil(e / step))))


def _points(tri, n):
    a, b, c = tri
    yield ((a[0] + b[0] + c[0]) / 3.0, (a[1] + b[1] + c[1]) / 3.0, (a[2] + b[2] + c[2]) / 3.0)      # the corners alone can all lie on the other surface
    for i in range(n + 1):
        for j in range(n + 1 - i):
            u, v = i / float(n), j / float(n)
            w = 1.0 - u - v
            yield (a[0] * w + b[0] * u + c[0] * v, a[1] * w + b[1] * u + c[1] * v, a[2] * w + b[2] * u + c[2] * v)


def sample(tris, step=STEP, budget=BUDGET):
    """Sample points on a list of triangles (each triangle three points): its corners, its centre and a grid of about
    `step`. Returns (points, step used, coverage). When the budget would be exceeded the step grows; when even four
    points per triangle are too many, only every k-th triangle is sampled and coverage (the share of triangles looked
    at) drops under 1.0."""
    step, coverage = float(step), 1.0
    if not tris:
        return [], step, coverage
    if len(tris) * 4 > budget:
        k = int(math.ceil(len(tris) * 4 / float(budget)))
        picked = tris[::k]
        coverage = len(picked) / float(len(tris))
        tris = picked
    for _ in range(12):
        ns = [_subdivisions(t, step) for t in tris]
        total = sum((n + 1) * (n + 2) // 2 + 1 for n in ns)
        if total <= budget or all(n == 1 for n in ns):
            break
        step *= max(1.25, math.sqrt(total / float(budget)))
    pts = []
    for t, n in zip(tris, ns):
        pts.extend(_points(t, n))
    return pts, step, coverage


def boxes_of(points, merge=MERGE):
    """Boxes [x0, y0, z0, x1, y1, z1] (rounded to 0.01) round groups of points: points in touching voxels of size `merge`
    are one group."""
    if not points:
        return []
    inv = 1.0 / merge
    vox = {}
    for p in points:
        vox.setdefault((int(math.floor(p[0] * inv)), int(math.floor(p[1] * inv)), int(math.floor(p[2] * inv))), []).append(p)
    left, out = set(vox), []
    while left:
        start = left.pop()
        stack, group = [start], [start]
        while stack:
            x, y, z = stack.pop()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        k = (x + dx, y + dy, z + dz)
                        if k in left:
                            left.discard(k); stack.append(k); group.append(k)
        pts = [p for k in group for p in vox[k]]
        b = bbox(pts)
        out.append([round(v, 2) + 0.0 for v in b])
    return sorted(out)


def _tri_keys(vertices, triangles):
    q = [(int(round(x / QUANTUM)), int(round(y / QUANTUM)), int(round(z / QUANTUM))) for x, y, z in vertices]
    return [tuple(sorted((q[a], q[b], q[c]))) for a, b, c in triangles]


def change_region(a, b, tol=CHANGE_TOL, step=STEP, budget=BUDGET, far=10.0):
    """Where do two versions of one part differ? Both in the model frame.

    Triangles that exist in only one mesh are sampled; a sample point more than `tol` from the other surface is a
    change (so a flat face that was only cut into other triangles is not one). Returns
    {"changed": bool, "boxes": [[x0, y0, z0, x1, y1, z1], ...], "max_mm": largest distance found (capped at `far`),
     "points": changed sample points, "only_a": triangles only in a, "only_b": triangles only in b, "step": step used,
     "coverage": share of those triangles that was sampled (under 1.0 only past the budget), "sampled": True}. "changed" False with different hashes means: the same surface within `tol` at the sampled points."""
    (va, fa), (vb, fb) = load(a), load(b)
    ka, kb = _tri_keys(va, fa), _tri_keys(vb, fb)
    sa, sb = set(ka), set(kb)
    only_a = [(va[t[0]], va[t[1]], va[t[2]]) for t, k in zip(fa, ka) if k not in sb]
    only_b = [(vb[t[0]], vb[t[1]], vb[t[2]]) for t, k in zip(fb, kb) if k not in sa]
    out = {'changed': False, 'boxes': [], 'max_mm': 0.0, 'points': 0, 'only_a': len(only_a), 'only_b': len(only_b), 'step': step, 'coverage': 1.0, 'sampled': True}
    if not only_a and not only_b:
        return out
    moved, worst, used, cover = [], 0.0, step, 1.0
    for mine, other_v, other_f in ((only_a, vb, fb), (only_b, va, fa)):
        if not mine:
            continue
        pts, st, cov = sample(mine, step, budget // 2)
        used, cover = max(used, st), min(cover, cov)
        if not other_f:
            moved.extend(pts); worst = far
            continue
        grid = Grid(other_v, other_f)
        for p in pts:
            if grid.distance(p, tol) is None:
                moved.append(p)
                d = grid.distance(p, far)
                worst = max(worst, far if d is None else d)
    out.update(changed=bool(moved), boxes=boxes_of(moved), max_mm=round(worst, 3), points=len(moved), step=round(used, 3), coverage=round(cover, 3))
    return out


def _near_triangles(va, fa, box_b, near):
    grown = [box_b[0] - near, box_b[1] - near, box_b[2] - near, box_b[3] + near, box_b[4] + near, box_b[5] + near]
    out = []
    for t in fa:
        a, b, c = va[t[0]], va[t[1]], va[t[2]]
        if all(min(a[k], b[k], c[k]) <= grown[k + 3] and max(a[k], b[k], c[k]) >= grown[k] for k in range(3)):
            out.append((a, b, c))
    return out


def mating_zone(a, b, near=NEAR, step=STEP, budget=BUDGET):
    """Where does the surface of A come within `near` mm of B? Both in one frame, as exported: a neighbour that only
    comes close in another position of a joint is not seen. Returns
    {"touch": bool, "boxes": [...] on A, "min_gap": smallest distance found or None, "points": n, "near": near,
     "step": step used, "sampled": True}."""
    (va, fa), (vb, fb) = load(a), load(b)
    out = {'touch': False, 'boxes': [], 'min_gap': None, 'points': 0, 'near': near, 'step': step, 'coverage': 1.0, 'sampled': True}
    if not fa or not fb:
        return out
    tris = _near_triangles(va, fa, bbox(vb), near)
    if not tris:
        return out
    grid = Grid(vb, fb)
    pts, used, cover = sample(tris, step, budget)
    close, best = [], None
    for p in pts:
        d = grid.distance(p, near)
        if d is not None:
            close.append(p)
            if best is None or d < best: best = d
    out.update(touch=bool(close), boxes=boxes_of(close), min_gap=None if best is None else round(best, 3), points=len(close), step=round(used, 3), coverage=round(cover, 3))
    return out


def fit(a, b, near=NEAR, step=0.5, budget=BUDGET):
    """Does A fit B (for example a new part against the as-printed copy of its neighbour)? A sampled distance test, not a
    boolean: it finds a gap or an interference to about the step on the surface. Returns
    {"result": "apart" (nothing of A within `near` of B) | "pass" (close, nothing inside) | "interference",
     "min_gap": smallest distance from A to B's surface in the zone or None, "inside": sample points of A inside B,
     "depth": how deep the deepest of them lies (capped at 5 * near), "boxes": where A is inside B,
     "zone": sample points within `near`, "near", "step", "label": FIT_LABEL}."""
    (va, fa), (vb, fb) = load(a), load(b)
    out = {'result': 'apart', 'min_gap': None, 'inside': 0, 'depth': 0.0, 'boxes': [], 'zone': 0, 'near': near, 'step': step, 'coverage': 1.0, 'label': FIT_LABEL}
    if not fa or not fb:
        return out
    tris = _near_triangles(va, fa, bbox(vb), near)
    if not tris:
        return out
    grid = Grid(vb, fb)
    pts, used, cover = sample(tris, step, budget)
    cap = 5.0 * near
    zone, best, inside, depth = 0, None, [], 0.0
    box = grid.box
    for p in pts:
        d = grid.distance(p, near)
        if d is not None:
            zone += 1
            if best is None or d < best: best = d
            if d <= _EPS:
                continue
        elif not all(box[k] <= p[k] <= box[k + 3] for k in range(3)):
            continue
        if grid.inside(p):
            inside.append(p)
            if d is None:
                d = grid.distance(p, cap)
                d = cap if d is None else d
            if d > depth: depth = d
    out.update(result='interference' if inside else ('pass' if zone else 'apart'), min_gap=None if best is None else round(best, 3),
               inside=len(inside), depth=round(depth, 3), boxes=boxes_of(inside), zone=zone, step=round(used, 3), coverage=round(cover, 3))
    return out


def box_text(box):
    """One box as text: x 10.0..14.0  y -3.0..3.0  z 0.0..8.0."""
    return 'x %.1f..%.1f  y %.1f..%.1f  z %.1f..%.1f' % (box[0], box[3], box[1], box[4], box[2], box[5])
