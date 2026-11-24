"""Thin-wall gate, the deterministic part: no Fusion here. Pure Python (3.9+, standard library), so the tests and the
host run the same text.

Data in (from thinwall_fusion.py, all mm, world frame), one part:
  {"name", "faces": [{"type": "plane" | "cylinder" | "cone" | "sphere" | "torus" | "nurbs" | ..., "area",
                      "v": [x, y, z, ...] mesh nodes, "i": [a, b, c, ...] triangles, wound so that (b - a) x (c - a)
                      points out of the material}],
   "box": [[x, y, z], [x, y, z]]}

What a wall is here. From sample points spread over the surface (one per `sampling.spacing_mm` squared of area, every
face at least one) a ray runs into the material along the inward normal. The first face it leaves the material through
ends the measurement: its distance is the wall thickness at that point when that face looks back at the start (its
normal within `corner_deg` of the reversed start normal). A ray that leaves through a side face at a steeper angle has
run along a corner, not across a wall (rule `corner`). The two faces of a wall are the start face and the exit face;
the angle between their planes is the wall's taper. A taper of `wedge_deg` or more is the tip of a wedge (a 45 degree
flank or chamfer running out on a face, a bed knife edge of an overhang, a chamfer tip): thin only within a short
distance of its edge, and named, not reported (rule `wedge`). A thin spot on the flanks of teeth (faces repeated round
one axis at least `teeth.min_copies` times, at least one of the two not a hole) is the profile of a gear or a pulley:
its size comes from the belt or gear standard, not from this gate (rule `teeth`). A flat wall in the layer plane, one
or two layer heights thick, is a sacrificial bridge layer over a hole (rule `sacrificial_layer`). Everything else under
a limit is a finding. Ends on a plane, cylinder, cone or sphere are moved onto the exact surface (the face's own
formula), so the mesh's chords do not count; the start normal is the exact one too. Each thin spot is sampled again,
`sampling.refine` times finer, round its thinnest samples.

Limits (per part, from the projects it is printed in): the least wall of the rulebook (`limits.min_wall_mm`), two
lines of the project's outer wall width unless the object is printed with the arachne wall generator
(`limits.two_lines`), one line of it in any case (`limits.one_line`). Thin samples that lie close together are one
region; each region is one finding with the place (the middle of its thinnest measurement), the thickness there, the
limit it breaks and the faces it lies on."""
import math

# ---------------------------------------------------------------- vectors
def _sub(u, v): return (u[0] - v[0], u[1] - v[1], u[2] - v[2])
def _dot(u, v): return u[0] * v[0] + u[1] * v[1] + u[2] * v[2]
def _cross(u, v): return (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
def _len(u): return math.sqrt(u[0] * u[0] + u[1] * u[1] + u[2] * u[2])


def _unit(u):
    n = _len(u)
    return (u[0] / n, u[1] / n, u[2] / n) if n > 1e-15 else (0.0, 0.0, 0.0)


# ---------------------------------------------------------------- mesh
def triangles(part):
    """Flat triangle table of a part: [(a, e1, e2, n, face, area)]; degenerate triangles dropped."""
    out = []
    for fi, f in enumerate(part['faces']):
        v, ix = f['v'], f['i']
        for k in range(0, len(ix) - 2, 3):
            i, j, l = 3 * ix[k], 3 * ix[k + 1], 3 * ix[k + 2]
            a = (v[i], v[i + 1], v[i + 2]); b = (v[j], v[j + 1], v[j + 2]); c = (v[l], v[l + 1], v[l + 2])
            e1, e2 = _sub(b, a), _sub(c, a)
            cr = _cross(e1, e2); ar = _len(cr) / 2
            if ar < 1e-9:
                continue
            out.append((a, e1, e2, (cr[0] / (2 * ar), cr[1] / (2 * ar), cr[2] / (2 * ar)), fi, ar))
    return out


class Grid:
    """Uniform grid of triangles for short rays: a triangle sits in every cell its box (split into pieces no longer
    than a cell) touches."""

    def __init__(self, tris, h):
        self.h = h; self.cells = {}; self.tris = tris
        cells = self.cells
        for ti, (a, e1, e2, n, fi, ar) in enumerate(tris):
            m = max(1, int(math.ceil(max(_len(e1), _len(e2), _len(_sub(e2, e1))) / h)))
            keys = set()
            for p in range(m):
                for q in range(m - p):
                    # the sub-triangle (p, q) and its upside-down neighbour: corners at barycentric steps of 1/m
                    for (u0, w0), (u1, w1), (u2, w2) in (((p, q), (p + 1, q), (p, q + 1)), ((p + 1, q), (p + 1, q + 1), (p, q + 1))):
                        if u1 + w1 > m or u2 + w2 > m:
                            continue
                        pts = [(a[0] + (e1[0] * u + e2[0] * w) / m, a[1] + (e1[1] * u + e2[1] * w) / m, a[2] + (e1[2] * u + e2[2] * w) / m)
                               for u, w in ((u0, w0), (u1, w1), (u2, w2))]
                        lo = [int(math.floor(min(pt[k] for pt in pts) / h)) for k in range(3)]
                        hi = [int(math.floor(max(pt[k] for pt in pts) / h)) for k in range(3)]
                        for x in range(lo[0], hi[0] + 1):
                            for y in range(lo[1], hi[1] + 1):
                                for z in range(lo[2], hi[2] + 1):
                                    keys.add((x, y, z))
            for k in keys:
                cells.setdefault(k, []).append(ti)

    def first_hit(self, o, d, tmax, skip):
        """(t, triangle index) of the first triangle the ray o + t d (0 < t <= tmax) crosses, or None."""
        h = self.h; tris = self.tris; cells = self.cells
        e = (o[0] + d[0] * tmax, o[1] + d[1] * tmax, o[2] + d[2] * tmax)
        lo = [int(math.floor(min(o[k], e[k]) / h)) for k in range(3)]
        hi = [int(math.floor(max(o[k], e[k]) / h)) for k in range(3)]
        seen = set(); best = None; bt = tmax
        dx, dy, dz = d
        ox, oy, oz = o
        for x in range(lo[0], hi[0] + 1):
            for y in range(lo[1], hi[1] + 1):
                for z in range(lo[2], hi[2] + 1):
                    for ti in cells.get((x, y, z), ()):
                        if ti in seen or ti == skip:
                            continue
                        seen.add(ti)
                        a, e1, e2 = tris[ti][0], tris[ti][1], tris[ti][2]
                        # Moller-Trumbore
                        px, py, pz = dy * e2[2] - dz * e2[1], dz * e2[0] - dx * e2[2], dx * e2[1] - dy * e2[0]
                        det = e1[0] * px + e1[1] * py + e1[2] * pz
                        if -1e-12 < det < 1e-12:
                            continue
                        inv = 1.0 / det
                        sx, sy, sz = ox - a[0], oy - a[1], oz - a[2]
                        u = (sx * px + sy * py + sz * pz) * inv
                        if u < -1e-9 or u > 1 + 1e-9:
                            continue
                        qx, qy, qz = sy * e1[2] - sz * e1[1], sz * e1[0] - sx * e1[2], sx * e1[1] - sy * e1[0]
                        w = (dx * qx + dy * qy + dz * qz) * inv
                        if w < -1e-9 or u + w > 1 + 1e-9:
                            continue
                        t = (e2[0] * qx + e2[1] * qy + e2[2] * qz) * inv
                        if 1e-6 < t < bt:
                            bt, best = t, ti
        return None if best is None else (bt, best)


def samples(tris, nfaces, spacing, small_face=0.0, face_area=None):
    """[(point, triangle index)]: one point per spacing^2 of area on every face, at least one per face. A triangle
    bigger than that is cut into m^2 equal pieces and each piece gives its middle; small triangles add their area up
    and give the middle of the triangle where the sum passes spacing^2. On a face smaller than small_face mm2 (a
    sliver: two cuts that nearly meet) every triangle gives its middle and the middles of its three halves."""
    s2 = spacing * spacing
    out, acc, got = [], [0.0] * nfaces, [False] * nfaces
    for ti, (a, e1, e2, n, fi, ar) in enumerate(tris):
        if face_area is not None and face_area[fi] < small_face:
            for u, w in ((1.0 / 3, 1.0 / 3), (2.0 / 3, 1.0 / 6), (1.0 / 6, 2.0 / 3), (1.0 / 6, 1.0 / 6)):
                out.append(((a[0] + e1[0] * u + e2[0] * w, a[1] + e1[1] * u + e2[1] * w, a[2] + e1[2] * u + e2[2] * w), ti))
            got[fi] = True
            continue
        if ar >= s2:
            m = int(math.ceil(math.sqrt(ar / s2)))
            for p in range(m):
                for q in range(m - p):
                    for (u, w) in ((p + 1.0 / 3, q + 1.0 / 3), (p + 2.0 / 3, q + 2.0 / 3)):
                        if u + w > m:
                            continue
                        out.append(((a[0] + (e1[0] * u + e2[0] * w) / m, a[1] + (e1[1] * u + e2[1] * w) / m,
                                     a[2] + (e1[2] * u + e2[2] * w) / m), ti))
            got[fi] = True
            continue
        acc[fi] += ar
        if acc[fi] >= s2 or not got[fi]:
            if acc[fi] >= s2:
                acc[fi] -= s2
            got[fi] = True
            out.append(((a[0] + (e1[0] + e2[0]) / 3, a[1] + (e1[1] + e2[1]) / 3, a[2] + (e1[2] + e2[2]) / 3), ti))
    return out


# ---------------------------------------------------------------- exact surfaces
def _roots(A, B, C):
    if abs(A) < 1e-15:
        return [] if abs(B) < 1e-15 else [-C / B]
    D = B * B - 4 * A * C
    if D < 0:
        return []
    s = math.sqrt(D)
    return [(-B - s) / (2 * A), (-B + s) / (2 * A)]


def _cone_k(f):
    """tan of the half angle with the sign that fits the face's own mesh (radius r at o, growing along +a or -a)."""
    if '_k' in f:
        return f['_k']
    t = math.tan(math.radians(f['half_deg']))
    v = f['v']; o, a = f['o'], f['a']
    best = None
    for k in (t, -t):
        res = 0.0
        for j in range(0, min(len(v), 30), 3):
            w = (v[j] - o[0], v[j + 1] - o[1], v[j + 2] - o[2])
            h = _dot(w, a)
            rho = math.sqrt(max(0.0, _dot(w, w) - h * h))
            res += abs(rho - (f['r'] + k * h))
        if best is None or res < best[0]:
            best = (res, k)
    f['_k'] = best[1]
    return best[1]


def exact_t(f, p, d, guess, window):
    """Where the ray p + t d meets the face's exact surface, the root nearest `guess` within `window`; None for a
    surface the judge has no formula for (torus, nurbs) or when no root is that near."""
    ty = f['type']
    if 'o' not in f:
        return None
    o = f['o']
    w = (p[0] - o[0], p[1] - o[1], p[2] - o[2])
    if ty == 'plane':
        a = f['a']; dn = _dot(a, d)
        ts = [] if abs(dn) < 1e-12 else [-_dot(a, w) / dn]
    elif ty == 'cylinder':
        a = f['a']
        wd, dd = _dot(w, a), _dot(d, a)
        u = (w[0] - a[0] * wd, w[1] - a[1] * wd, w[2] - a[2] * wd)
        e = (d[0] - a[0] * dd, d[1] - a[1] * dd, d[2] - a[2] * dd)
        ts = _roots(_dot(e, e), 2 * _dot(u, e), _dot(u, u) - f['r'] ** 2)
    elif ty == 'cone':
        a = f['a']; k = _cone_k(f)
        wd, dd = _dot(w, a), _dot(d, a)
        # |w + t d|^2 - (h)^2 = (r + k h)^2, h = wd + t dd
        A = _dot(d, d) - dd * dd - (k * dd) ** 2
        B = 2 * (_dot(w, d) - wd * dd) - 2 * (f['r'] + k * wd) * k * dd
        C = _dot(w, w) - wd * wd - (f['r'] + k * wd) ** 2
        ts = [t for t in _roots(A, B, C) if f['r'] + k * (wd + t * dd) >= 0]
    elif ty == 'sphere':
        ts = _roots(_dot(d, d), 2 * _dot(w, d), _dot(w, w) - f['r'] ** 2)
    else:
        return None
    ts = [t for t in ts if abs(t - guess) <= window]
    return min(ts, key=lambda t: abs(t - guess)) if ts else None


def exact_normal(f, p, near):
    """The unit normal of the face's exact surface at (or nearest to) p, on the side of `near` (the mesh normal);
    the mesh normal itself for a surface the judge has no formula for."""
    ty = f['type']
    if 'o' not in f or ty not in ('plane', 'cylinder', 'cone', 'sphere'):
        return near
    o = f['o']
    w = _sub(p, o)
    if ty == 'plane':
        n = tuple(f['a'])
    elif ty == 'sphere':
        n = _unit(w)
    else:
        a = f['a']; h = _dot(w, a)
        u = _unit((w[0] - a[0] * h, w[1] - a[1] * h, w[2] - a[2] * h))
        if ty == 'cylinder':
            n = u
        else:                                              # cone: radial tilted back by the half angle
            k = _cone_k(f); c = 1 / math.sqrt(1 + k * k)
            n = _unit((u[0] * c - a[0] * k * c, u[1] * c - a[1] * k * c, u[2] * c - a[2] * k * c))
    if _len(n) < 0.5:
        return near
    return n if _dot(n, near) >= 0 else (-n[0], -n[1], -n[2])


# ---------------------------------------------------------------- teeth
def _axes(part):
    """The distinct axis lines of the part's cylinders and cones: [(point, unit axis)]."""
    out = []
    for f in part['faces']:
        if f['type'] in ('cylinder', 'cone') and 'a' in f:
            a, o = f['a'], f['o']
            if any(abs(abs(_dot(a, b)) - 1) < 1e-6 and _len(_cross(_sub(o, q), b)) < 0.05 for q, b in out):
                continue
            out.append((o, a))
    return out


def teeth_faces(part, rules):
    """Indices of the faces of a tooth profile: faces of one type and one area (within `teeth.area_rel`) that repeat at
    least `teeth.min_copies` times, all at one distance from one axis of the part (a cylinder's or a cone's) and spread
    all round it (no gap wider than 3 pitches)."""
    tr = rules['exempt']['teeth']
    fams = {}
    for fi, f in enumerate(part['faces']):
        if f.get('area', 0) <= 0 or 'mid' not in f:
            continue
        key = (f['type'], round(math.log(f['area']) / math.log(1 + tr['area_rel'])))
        fams.setdefault(key, []).append(fi)
    axes = _axes(part)
    out = set()
    for key, fis in fams.items():
        if len(fis) < tr['min_copies']:
            continue
        for o, a in axes:
            e1 = _unit(_cross(a, (1.0, 0.0, 0.0) if abs(a[0]) < 0.9 else (0.0, 1.0, 0.0))); e2 = _cross(a, e1)
            ring = []                                       # (radius, angle, face) round this axis
            for fi in fis:
                w = _sub(part['faces'][fi]['mid'], o); h = _dot(w, a)
                u = (w[0] - a[0] * h, w[1] - a[1] * h, w[2] - a[2] * h)
                ring.append((_len(u), math.degrees(math.atan2(_dot(u, e2), _dot(u, e1))) % 360, fi))
            ring.sort()
            # faces at one radius (within radius_tol_mm of the previous one): one sub-family each
            subs, cur = [], [ring[0]]
            for x in ring[1:]:
                if x[0] - cur[-1][0] <= tr['radius_tol_mm']:
                    cur.append(x)
                else:
                    subs.append(cur); cur = [x]
            subs.append(cur)
            for sub in subs:
                if len(sub) < tr['min_copies'] or sub[0][0] < 1.0:
                    continue
                angs = sorted({round(x[1], 1) for x in sub})
                gap = max([angs[i + 1] - angs[i] for i in range(len(angs) - 1)] + [angs[0] + 360 - angs[-1]])
                if gap <= 3 * 360.0 / len(angs):
                    out.update(x[2] for x in sub)
    return out


# ---------------------------------------------------------------- judge
class Ruler:
    """The measuring set-up of one part: its triangles, their grid, its tooth faces; measure() one sample."""

    def __init__(self, part, rules, tmax, up=None, layer_mm=None):
        self.part, self.rules, self.tmax, self.up, self.layer_mm = part, rules, tmax, up, layer_mm
        self.tris = triangles(part)
        ex = rules['exempt']
        self.corner_cos = math.cos(math.radians(ex['corner']['deg']))
        self.wedge_deg = ex['wedge']['deg']
        self.slack = rules['sampling']['mesh_slack_mm']
        self.sac = ex['sacrificial_layer']
        self.teeth = teeth_faces(part, rules)
        self.faces = part['faces']
        self.grid = Grid(self.tris, max(tmax + self.slack, rules['sampling']['spacing_mm'])) if self.tris else None

    def measure(self, p, ti):
        """{"p", "q" (exit point), "t", "n" (start normal out), "face", "exit_face", "taper" (degrees between the two
        planes), "exact": both ends on exact surfaces, "kind": "wall" | "corner" | "wedge" | "teeth" |
        "sacrificial_layer"} of the ray from p (on triangle ti) into the material, or None when it runs longer than
        tmax."""
        tris, faces = self.tris, self.faces
        n = exact_normal(faces[tris[ti][4]], p, tris[ti][3])
        if _dot(n, tris[ti][3]) < 0.9:                     # far off its facet: keep the mesh normal
            n = tris[ti][3]
        d = (-n[0], -n[1], -n[2])
        hit = self.grid.first_hit(p, d, self.tmax + self.slack, ti)
        if hit is None:
            return None
        t, hj = hit
        fa, fb = tris[ti][4], tris[hj][4]
        s0 = exact_t(faces[fa], p, d, 0.0, self.slack)
        s1 = exact_t(faces[fb], p, d, t, self.slack)
        exact = s0 is not None and s1 is not None
        if s1 is not None:
            t = s1 - (s0 or 0.0)
        elif s0 is not None:
            t = t - s0
        if t >= self.tmax:
            return None
        crossed = exact and t < -self.rules['exempt']['faces_cross']['mm']
        t = max(t, 0.0)
        q = (p[0] + d[0] * t, p[1] + d[1] * t, p[2] + d[2] * t)
        nh = exact_normal(faces[fb], q, tris[hj][3])
        if _dot(nh, tris[hj][3]) < 0.9:
            nh = tris[hj][3]
        c = -_dot(n, nh)                                   # 1: the exit face looks straight back
        taper = math.degrees(math.acos(max(-1.0, min(1.0, c))))
        up, sac = self.up, self.sac
        if crossed:
            kind = 'faces_cross'                           # the exit face lies behind the start face: they overlap
        elif c < self.corner_cos:
            kind = 'corner'
        elif fa in self.teeth and fb in self.teeth and not (_is_hole(faces[fa]) and _is_hole(faces[fb])):
            kind = 'teeth'
        elif taper >= self.wedge_deg:
            kind = 'wedge'
        elif up and self.layer_mm and faces[fa]['type'] == 'plane' and faces[fb]['type'] == 'plane' and \
                abs(_dot(n, up)) >= math.cos(math.radians(sac['deg'])) and \
                any(abs(t - k * self.layer_mm) <= sac['tol_mm'] for k in range(1, sac['max_layers'] + 1)):
            kind = 'sacrificial_layer'
        else:
            kind = 'wall'
        return {'p': p, 'q': q, 't': t, 'n': n, 'face': fa, 'exit_face': fb, 'taper': taper, 'kind': kind, 'exact': exact, 'tri': ti}

    def neck(self, p, n1, n2, f1, f2, steps, nbr):
        """Rays from a point of a re-entrant edge into the material, fanned between the two inward normals: the
        thinnest that leaves through a face looking back at it (within the corner angle). A neck between two
        corners (two pockets whose rims come close at different depths) has no face normal across it."""
        best = None
        fan = [k / float(steps) for k in range(1, steps)]
        for s in fan + [None]:
            if s is None:                                  # refine round the best direction: 8 more between its neighbours
                if best is None:
                    break
                s0 = best['s']
                more = [s0 + (k - 4) / (4.0 * steps) for k in range(9) if k != 4 and 0 < s0 + (k - 4) / (4.0 * steps) < 1]
                for s2 in more:
                    r = self._fan_ray(p, n1, n2, f1, f2, s2, nbr)
                    if r and r['t'] < best['t']:
                        best = r
                break
            r = self._fan_ray(p, n1, n2, f1, f2, s, nbr)
            if r and (best is None or r['t'] < best['t']):
                best = r
        return best

    def _fan_ray(self, p, n1, n2, f1, f2, s, nbr):
        """One ray of the fan (s from 0: along -n1, to 1: along -n2), or None."""
        d = _unit((-(1 - s) * n1[0] - s * n2[0], -(1 - s) * n1[1] - s * n2[1], -(1 - s) * n1[2] - s * n2[2]))
        o = (p[0] + d[0] * 1e-4, p[1] + d[1] * 1e-4, p[2] + d[2] * 1e-4)
        hit = self.grid.first_hit(o, d, self.tmax, -1)
        if hit is None:
            return None
        t, hj = hit
        nh = self.tris[hj][3]
        if _dot(d, nh) < self.corner_cos:
            return None
        fb = self.tris[hj][4]
        if fb in (f1, f2) or fb in nbr.get(f1, ()) or fb in nbr.get(f2, ()):
            return None                                  # its own faces or their neighbours: the corner round the edge
        q = (o[0] + d[0] * t, o[1] + d[1] * t, o[2] + d[2] * t)
        faces = self.faces
        up, sac = self.up, self.sac
        cs = math.cos(math.radians(sac['deg']))
        taper = min(math.degrees(math.acos(max(-1.0, min(1.0, -_dot(m, nh))))) for m in (n1, n2))
        if f1 in self.teeth and fb in self.teeth:
            kind = 'teeth'
        elif taper >= self.wedge_deg:
            kind = 'wedge'                               # the exit face is tilted to both faces of the edge
        elif up and self.layer_mm and faces[fb]['type'] == 'plane' and abs(_dot(nh, up)) >= cs and \
                any(faces[f]['type'] == 'plane' and abs(_dot(m, up)) >= cs for f, m in ((f1, n1), (f2, n2))) and \
                any(abs(abs(_dot(_sub(q, p), up)) - k * self.layer_mm) <= sac['tol_mm'] for k in range(1, sac['max_layers'] + 1)):
            kind = 'sacrificial_layer'                   # the rim of a bridge layer: its thickness is a layer
        else:
            kind = 'wall'
        return {'p': p, 'q': q, 't': t, 'n': (-d[0], -d[1], -d[2]), 'face': f1, 'exit_face': fb, 'taper': taper,
                'kind': kind, 'exact': False, 'tri': None, 'neck': True, 's': s}

    def near_triangles(self, pts, r):
        """Indices of the triangles in the grid cells within r of any of the points."""
        h = self.grid.h; out = set(); k = int(math.ceil(r / h))
        for p in pts:
            c = [int(math.floor(p[i] / h)) for i in range(3)]
            for x in range(c[0] - k, c[0] + k + 1):
                for y in range(c[1] - k, c[1] + k + 1):
                    for z in range(c[2] - k, c[2] + k + 1):
                        out.update(self.grid.cells.get((x, y, z), ()))
        return out


def inner_edges(tris, spacing):
    """Points along the re-entrant edges of the mesh (where two faces meet with the material on the wide side: the
    floor and wall of a pocket, the root of a rib), every `spacing`: [(point, n1, n2, face1, face2)]. Found from the
    face meshes alone: a triangle side used once in its face is on the face's border; the same side in another face
    is the neighbour across the edge. Also the neighbours of every face ({face: set of faces})."""
    side = {}
    def key(p):
        return (round(p[0], 3), round(p[1], 3), round(p[2], 3))
    border = []
    for ti, (a, e1, e2, n, fi, ar) in enumerate(tris):
        b = (a[0] + e1[0], a[1] + e1[1], a[2] + e1[2]); c = (a[0] + e2[0], a[1] + e2[1], a[2] + e2[2])
        for u, v in ((a, b), (b, c), (c, a)):
            k = (key(u), key(v)) if key(u) < key(v) else (key(v), key(u))
            side.setdefault(k, []).append((ti, u, v))
    out, nbr = [], {}
    for k, users in side.items():
        if len(users) != 2:
            continue
        (t1, u, v), (t2, _, _) = users
        f1, f2 = tris[t1][4], tris[t2][4]
        if f1 == f2:
            continue
        nbr.setdefault(f1, set()).add(f2); nbr.setdefault(f2, set()).add(f1)
        n1, n2 = tris[t1][3], tris[t2][3]
        e = _sub(v, u); L = _len(e)
        if L < 1e-6:
            continue
        into1 = _unit(_cross(n1, e))                     # from the edge into face 1 (triangles wound outwards)
        if _dot(into1, _sub(_centroid(tris[t1]), u)) < 0:
            into1 = (-into1[0], -into1[1], -into1[2])
        if _dot(into1, n2) <= 0.05:                      # convex or flat: no neck starts here
            continue
        m = max(1, int(L / spacing))
        for j in range(m):
            s = (j + 0.5) / m
            out.append(((u[0] + e[0] * s, u[1] + e[1] * s, u[2] + e[2] * s), n1, n2, f1, f2))
    return out, nbr


def coaxial_slivers(part, limit, rules):
    """Pairs of cylinder faces of the body on one axis line (axes within `coaxial.axis_deg`, lines within
    `coaxial.offset_mm`), one a boss and one a hole, whose radii differ by less than `limit` and whose lengths along the
    axis meet or overlap: the plastic between them is a tube thinner than one line, or the two faces cross. Exact (from
    the face geometry): a mesh cannot see a tube of 0.02 mm. [{"faces": (i, j), "t", "at", "crossed"}]"""
    cx = rules['exempt']['coaxial']
    cyl = [(i, f) for i, f in enumerate(part['faces']) if f['type'] == 'cylinder' and 'a' in f]
    out = []
    for k, (i, fi) in enumerate(cyl):
        for j, fj in cyl[k + 1:]:
            if bool(fi.get('concave')) == bool(fj.get('concave')):
                continue
            if abs(abs(_dot(fi['a'], fj['a'])) - 1) > 1 - math.cos(math.radians(cx['axis_deg'])):
                continue
            a = fi['a']
            w = _sub(fj['o'], fi['o']); h = _dot(w, a)
            off = _len((w[0] - a[0] * h, w[1] - a[1] * h, w[2] - a[2] * h))
            if off > cx['offset_mm']:
                continue
            boss, hole = (fi, fj) if not fi.get('concave') else (fj, fi)
            # the boss's plastic is inside its radius, the hole's outside its own: with the boss wider, the band
            # between the two radii is plastic bounded by both faces: a tube as thin as their difference (less the
            # offset of the axes) wherever the two faces run side by side along the axis
            gap = boss['r'] - hole['r']
            if gap <= 0 or gap - off >= limit:
                continue

            def span(f):
                v = f['v']
                ts = [_dot(_sub((v[m], v[m + 1], v[m + 2]), fi['o']), a) for m in range(0, len(v), 3)]
                return min(ts), max(ts)
            s1, s2 = span(fi), span(fj)
            lo, hi = max(s1[0], s2[0]), min(s1[1], s2[1])
            if hi - lo <= cx['overlap_mm']:
                continue                                   # one after the other along the axis: a step, not a tube
            tm = (lo + hi) / 2
            out.append({'faces': (i, j), 't': max(0.0, gap - off), 'gap': gap, 'offset': off,
                        'at': [round(fi['o'][q] + a[q] * tm, 3) for q in range(3)]})
    return out


def _centroid(t):
    a, e1, e2 = t[0], t[1], t[2]
    return (a[0] + (e1[0] + e2[0]) / 3, a[1] + (e1[1] + e2[1]) / 3, a[2] + (e1[2] + e2[2]) / 3)


def measure(part, rules, tmax, up=None, layer_mm=None):
    """Every sample's measurement (Ruler.measure) for the rays that leave the material within tmax; rays that run
    longer are thick and not listed. Also the number of samples."""
    R = Ruler(part, rules, tmax, up, layer_mm)
    if not R.tris:
        return [], 0
    sm = samples(R.tris, len(part['faces']), rules['sampling']['spacing_mm'])
    out = []
    for p, ti in sm:
        r = R.measure(p, ti)
        if r:
            out.append(r)
    return out, len(sm)


def refine(R, group, rules):
    """The thin spot again round its `refine_best` thinnest samples: points every spacing / `refine` within one
    spacing of each, on the triangles of the same face, measured the same way. The reported thickness is then the
    region's thinnest within spacing / refine."""
    sm = rules['sampling']; sp = sm['spacing_mm']; k = sm['refine']; step = sp / k
    out = []
    for g in sorted([r for r in group if r.get('tri') is not None], key=lambda r: r['t'])[:sm['refine_best']]:
        a0, e1, e2, n, fi, ar = R.tris[g['tri']]
        u1 = _unit(e1); u2 = _cross(n, u1)
        cand = [ti for ti in R.near_triangles([g['p']], sp) if R.tris[ti][4] == fi]
        for i in range(-k, k + 1):
            for j in range(-k, k + 1):
                if i == 0 and j == 0:
                    continue
                p = (g['p'][0] + step * (i * u1[0] + j * u2[0]), g['p'][1] + step * (i * u1[1] + j * u2[1]), g['p'][2] + step * (i * u1[2] + j * u2[2]))
                ti = _on(R.tris, cand, p)
                if ti is None:
                    continue
                r = R.measure(_onto(R.tris[ti], p), ti)
                if r and r['kind'] == 'wall':
                    out.append(r)
    return out


def _on(tris, cand, p):
    """The candidate triangle whose prism holds p (within 0.05 mm of its plane), or None."""
    for ti in cand:
        a, e1, e2, n, fi, ar = tris[ti]
        w = _sub(p, a)
        if abs(_dot(w, n)) > 0.05:
            continue
        d00, d01, d11 = _dot(e1, e1), _dot(e1, e2), _dot(e2, e2)
        d20, d21 = _dot(w, e1), _dot(w, e2)
        den = d00 * d11 - d01 * d01
        if den <= 0:
            continue
        v = (d11 * d20 - d01 * d21) / den; u = (d00 * d21 - d01 * d20) / den
        if v >= -1e-9 and u >= -1e-9 and u + v <= 1 + 1e-9:
            return ti
    return None


def _onto(tri, p):
    a, e1, e2, n, fi, ar = tri
    h = _dot(_sub(p, a), n)
    return (p[0] - n[0] * h, p[1] - n[1] * h, p[2] - n[2] * h)


def _is_hole(f):
    return f['type'] == 'cylinder' and f.get('concave')


def regions(rows, link_mm):
    """Rows that lie within link_mm of each other (either end of their measurement), or that measure between the same
    two faces -> groups (lists of rows)."""
    h = link_mm
    cell = {}
    for i, r in enumerate(rows):
        for pt in (r['p'], r['q']):
            cell.setdefault((int(math.floor(pt[0] / h)), int(math.floor(pt[1] / h)), int(math.floor(pt[2] / h))), []).append(i)
    parent = list(range(len(rows)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i

    def join(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj
    for (x, y, z), ids in cell.items():
        near = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    near.extend(cell.get((x + dx, y + dy, z + dz), ()))
        for i in ids:
            pi = rows[i]
            for j in near:
                if j <= i:
                    continue
                pj = rows[j]
                if min(_len(_sub(a, b)) for a in (pi['p'], pi['q']) for b in (pj['p'], pj['q'])) <= link_mm:
                    join(i, j)
    pairs = {}
    for i, r in enumerate(rows):
        k = (min(r['face'], r['exit_face']), max(r['face'], r['exit_face']))
        if k in pairs:
            join(i, pairs[k])
        else:
            pairs[k] = i
    groups = {}
    for i in range(len(rows)):
        groups.setdefault(find(i), []).append(rows[i])
    return list(groups.values())


def limits_for(rules, projects):
    """The limits of a part from the projects it is printed in: [{"project", "line_mm", "arachne", "layer_mm"}]. The
    strictest project counts (the widest line; classic walls before arachne)."""
    lim = rules['limits']
    out = {'min_wall': lim['min_wall_mm']['value'], 'two_lines': None, 'one_line': None, 'project': None, 'arachne': None,
           'line_mm': None, 'layer_mm': None}
    for pr in projects or []:
        two = lim['two_lines']['lines'] * pr['line_mm']
        one = lim['one_line']['lines'] * pr['line_mm']
        if out['one_line'] is None or one > out['one_line'] or (one == out['one_line'] and not pr['arachne'] and out['arachne']):
            out.update(one_line=one, two_lines=two, project=pr['project'], arachne=pr['arachne'], line_mm=pr['line_mm'],
                       layer_mm=pr.get('layer_mm'))
    return out


UP = {'-z': (0.0, 0.0, 1.0), '+z': (0.0, 0.0, -1.0), '-y': (0.0, 1.0, 0.0), '+y': (0.0, -1.0, 0.0), '-x': (1.0, 0.0, 0.0), '+x': (-1.0, 0.0, 0.0)}


def judge(part, rules, projects=None, bed_face=None, accepted=()):
    """Findings of one part. projects: [{"project", "line_mm", "arachne", "layer_mm"}] (see limits_for); bed_face: the
    face of the part on the bed ("-z", "+y", ...: parts.json), for the sacrificial-layer rule. accepted: owner
    decisions from gate/settings.json, [{"near": [x, y, z], "within_mm", "code"?, "why"}]: a finding whose place is
    that close is listed under "accepted", not "findings"."""
    L = limits_for(rules, projects)
    tmax = max(x for x in (L['min_wall'], L['two_lines'], L['one_line']) if x is not None)
    up = UP.get(bed_face) if bed_face else None
    R = Ruler(part, rules, tmax, up, L['layer_mm'])
    sm = samples(R.tris, len(part['faces']), rules['sampling']['spacing_mm'], rules['sampling']['small_face_mm2'],
                 [f.get('area', 0.0) for f in part['faces']]) if R.tris else []
    rows = [r for r in (R.measure(p, ti) for p, ti in sm) if r]
    n = len(sm)
    if R.tris and rules['sampling'].get('neck_steps'):
        edges, nbr = inner_edges(R.tris, rules['sampling']['spacing_mm'] / 2)
        for p, n1, n2, f1, f2 in edges:
            if f1 in R.teeth and f2 in R.teeth:
                continue                                 # the root of a tooth: the profile is the belt's
            r = R.neck(p, n1, n2, f1, f2, rules['sampling']['neck_steps'], nbr)
            if r:
                rows.append(r)
                n += 1
    if L['one_line']:
        for c in coaxial_slivers(part, L['one_line'], rules):
            i, j = c['faces']
            rows.append({'p': tuple(c['at']), 'q': tuple(c['at']), 't': c['t'], 'n': (0.0, 0.0, 1.0), 'face': i, 'exit_face': j,
                         'taper': 0.0, 'kind': 'wall', 'exact': True, 'tri': None, 'coaxial': True})
    counts = {'samples': n, 'corner': 0, 'wedge': 0, 'teeth': 0, 'sacrificial_layer': 0, 'wall': 0, 'faces_cross': 0}
    thin = []
    for r in rows:
        counts[r['kind']] += 1
        if r['kind'] in ('wall', 'faces_cross'):
            thin.append(r)
    sp = rules['sampling']['spacing_mm']
    findings, acc = [], []
    faces = part['faces']
    for g in regions(thin, rules['sampling']['link_spacings'] * sp):
        if rules['sampling'].get('refine', 1) > 1:
            g = g + refine(R, g, rules)
        best = min(g, key=lambda r: r['t'])
        t = best['t']
        if any(r['kind'] == 'faces_cross' for r in g):
            best = next(r for r in g if r['kind'] == 'faces_cross')
            code, limit, t = 'faces_cross', 0.0, 0.0
        elif L['one_line'] is not None and t < L['one_line']:
            code, limit = 'under_one_line', L['one_line']
        elif L['two_lines'] is not None and t < L['two_lines'] and not L['arachne']:
            code, limit = 'under_two_lines', L['two_lines']
        elif t < L['min_wall']:
            code, limit = 'thin_wall', L['min_wall']
        else:
            continue
        mid = [round((best['p'][k] + best['q'][k]) / 2, 3) for k in range(3)]
        fset = sorted({r['face'] for r in g} | {r['exit_face'] for r in g})
        f = {'part': part['name'], 'code': code, 'at': mid, 'across': [round(-x, 4) for x in best['n']],
             'thickness_mm': round(t, 3), 'limit_mm': round(limit, 3), 'exact': best['exact'],
             'samples': len(g), 'area_mm2': round(len(g) * sp * sp / 2, 2),
             'from': [round(x, 3) for x in best['p']], 'to': [round(x, 3) for x in best['q']],
             'extent': [[round(min(min(r['p'][k], r['q'][k]) for r in g), 2) for k in range(3)], [round(max(max(r['p'][k], r['q'][k]) for r in g), 2) for k in range(3)]],
             'faces': [dict({'index': i, 'type': faces[i]['type'], 'area_mm2': round(faces[i].get('area', 0), 2)},
                            **({'d': faces[i]['d'], 'hole': bool(faces[i].get('concave'))} if faces[i]['type'] == 'cylinder' else {}))
                       for i in fset[:60]],
             'limits': {k: (round(L[k], 3) if L[k] is not None else None) for k in ('min_wall', 'two_lines', 'one_line')},
             'project': L['project'], 'arachne': L['arachne'],
             'neck': bool(best.get('neck')), 'coaxial': bool(best.get('coaxial')),
             'measured': ('two faces cross: plastic on both sides of each (%s)' % describe(faces[best['face']], faces[best['exit_face']])) if code == 'faces_cross' else
                         '%s %.2f mm (%s)' % ('tube between a boss and a hole on one axis' if best.get('coaxial') else 'neck between two corners' if best.get('neck') else 'wall', t, describe(faces[best['face']], faces[best['exit_face']])),
             'expected': '>= %.2f mm (%s)' % (limit, rules['codes'][code]['limit']),
             'fix': rules['codes'][code]['fix']}
        hit = next((a for a in accepted if _len(_sub(a['near'], mid)) <= a.get('within_mm', 2.0) and a.get('code') in (None, code)), None)
        if hit:
            f['accepted'] = hit.get('why', '')
            acc.append(f)
        else:
            findings.append(f)
    findings.sort(key=lambda f: (f['thickness_mm'], f['at']))
    return {'findings': findings, 'accepted': acc, 'counts': counts, 'limits': L}


def describe(fa, fb):
    def one(f):
        if f['type'] == 'cylinder':
            return ('hole' if f.get('concave') else 'boss') + (' D%.2f' % f['d'] if f.get('d') else '')
        return f['type']
    return '%s to %s' % (one(fa), one(fb))
