"""Host-side probe: wall thickness of an STL on the standard library. Material between faces, rims round pockets, slits.

From sample points on each triangle a ray runs inwards along the negative normal; the distance to the first hit is the
local thickness. A second ray runs outwards; a hit on a face that looks back at close range is an air gap (slit).
A uniform grid of triangles keeps it fast. The sample budget is bounded: when the time runs out the probe samples less
and says what share of the surface it covered. Rims round pockets are never hidden.

Must handle a 200 x 200 x 170 mm part with about 300 000 triangles in under about 60 s in pure Python, or report a
coverage below 1. The probe holds no limit: it lists every region under `report_under` and the judges decide.

    python3 gate/tools/probes/stl_walls.py <file.stl> [--under MM] [--budget S] [--json]

How it measures, and what that means for a reader of the facts:

- Samples. One sample point per `spacing_target` squared of surface (report_under / 4, finer on a small part), handed
  out along the triangles in file order, so every patch of surface gets its share whatever the triangle sizes are.
  The points are worked off in 16 interleaved passes: when the time ends, what was done is spread over the whole part
  and "coverage" says which share of the planned samples was shot. Rays are never longer than `report_under`.
- A ray counts when the face it ends on looks back at the start within 55 degrees. Two faces that meet at a wider angle
  are a corner, not a wall.
- A wedge is not a wall: when the two faces are 30 to 55 degrees apart and really meet in an edge near the sample (a
  corner of the mesh with these two faces, within the distance at which the wedge comes to its point), the thin
  strip along that edge is counted in "wedge_samples" and not listed. A 45 degree flank ends in such an edge, and so
  does a recess that runs out through a slanted face. A blade under 30 degrees and every pair of faces that does not
  meet are listed. The same holds for the air in a groove.
- Corner to corner. Two pockets whose corners point at each other leave a neck no face normal runs through. From every
  inside corner (an edge where the material is the wider side) rays fan into the material between the two face
  normals, every 11.25 degrees; from every outside corner they fan into the air, for a gap between two corners.
  A neck seen only by the fan is measured along the nearest fan ray: up to about 10 % more than its true width.
  A neck between two corner POINTS (three edges meeting, on both sides) is not seen.
- Regions. Thin samples are grouped into connected patches (a wall's two sides are one patch; so are two patches less
  than about 1.5 x the spacing apart, also across a narrow gap), separately under and over half of `report_under`:
  with the caller's convention (report_under = 2 x the largest limit) a patch of the first kind is one thin spot with
  its own area, and a patch of the second kind is "near the limit". "under" says which. Where a thin spot thickens
  past half of `report_under` that rim is counted with the spot ("rim_area"), not listed again. A near-the-limit patch
  that joins several thin spots is a region of its own, so two thin spots in one thin-walled part stay two regions.
  No sample under `report_under` is left out of the list, unless one kind has more than REGION_CAP regions: then the
  thickest ones are only counted ("regions_dropped"), and every region that is listed is thinner than those.
- Numbers are rounded to 0.0001 mm. A curved wall is measured between facets, so it reads the chord error of the mesh
  (about 0.01 mm) thinner or thicker than modelled."""
import array, json, math, os, random, sys, time

_HERE = os.path.dirname(os.path.abspath(__file__))
try:
    import gate_common
except ImportError:                                  # run as a script: tools/gate (or gate/tools) is one folder up
    sys.path.insert(0, os.path.dirname(_HERE))
    import gate_common
from gate_common import GateError
try:
    from probes import stl_mesh as _mesh
except ImportError:
    import stl_mesh as _mesh

MAX_SAMPLES = 3000000        # sample points per call; a part that needs more is sampled coarser and its coverage says so
REGION_CAP = 1000            # regions listed per kind, thinnest first; "regions_dropped" counts the rest
_FINE = 40000                # a small part is sampled finer until it has about this many samples
_PASSES = (0, 8, 4, 12, 2, 10, 6, 14, 1, 9, 5, 13, 3, 11, 7, 15)
_C_FACING = math.cos(math.radians(55.0))     # the far face must look back within this angle
_C_WEDGE = math.cos(math.radians(30.0))      # beyond this angle a pair of flat faces is tested for being a plain wedge
_C_CREASE = math.cos(math.radians(25.0))     # an edge whose faces differ by more than this is a corner (it gets a fan)
_C_BLADE = math.cos(math.radians(175.0))     # faces nearly back to back: no fan
_C_SAME = math.cos(math.radians(25.0))       # two normals this close are one face (the facets of a curved one)
_FAN = math.radians(11.25)
_EPS = 1e-7                  # mm: nearer than this is the start face itself
_TOL = 1e-7                  # barycentric tolerance: a ray through an edge hits one of its two triangles
_INF = float('inf')
_SKIN = 1e-6                 # in cells: how far a triangle reaches into the neighbour cell it only touches
_BARY_CACHE = 150000         # triangles whose inside test is kept ready
_PAIRS = 12000000            # (triangle, cell) pairs the grid may hold before its cells are made larger
_STRIDE = 8                  # doubles per thin sample: t, px, py, pz, qx, qy, qz, weight


def _twins(F, nv, stl_path):
    """{directed edge a * nv + b: triangle}. GateError unless every edge is used once each way (closed, one orientation)."""
    twin = {}
    for i, (a, b, c) in enumerate(F):
        twin[a * nv + b] = i; twin[b * nv + c] = i; twin[c * nv + a] = i
    bad = 3 * len(F) - len(twin)
    if not bad:
        for a, b, c in F:
            if b * nv + a not in twin or c * nv + b not in twin or a * nv + c not in twin:
                bad += 1
    if bad:
        raise GateError('%s is not a closed mesh with one orientation (%d triangles with an open, doubled or flipped edge): '
                        'run stl_mesh first' % (stl_path, bad))
    return twin


def _normals(V, F):
    """([(nx, ny, nz, d)] unit normal and plane offset per triangle, array of areas, total area, 6 x signed volume,
    the sum of every triangle's longest edge)."""
    N, A, total, vol6, edges = [None] * len(F), array.array('d', bytes(8 * len(F))), 0.0, 0.0, 0.0
    sqrt = math.sqrt
    for i, (a, b, c) in enumerate(F):
        x0, y0, z0 = V[a]; x1, y1, z1 = V[b]; x2, y2, z2 = V[c]
        ux, uy, uz, vx, vy, vz = x1 - x0, y1 - y0, z1 - z0, x2 - x0, y2 - y0, z2 - z0
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        l = sqrt(nx * nx + ny * ny + nz * nz)
        vol6 += x0 * (y1 * z2 - z1 * y2) - y0 * (x1 * z2 - z1 * x2) + z0 * (x1 * y2 - y1 * x2)
        uu = ux * ux + uy * uy + uz * uz; vv = vx * vx + vy * vy + vz * vz
        ww = uu + vv - 2.0 * (ux * vx + uy * vy + uz * vz)
        edges += sqrt(uu if uu > vv and uu > ww else (vv if vv > ww else ww))
        if l < 1e-14:
            N[i] = (0.0, 0.0, 0.0, 0.0); continue
        nx /= l; ny /= l; nz /= l
        N[i] = (nx, ny, nz, nx * x0 + ny * y0 + nz * z0); A[i] = 0.5 * l; total += 0.5 * l
    if vol6 < 0:                                     # inside out: turn every normal
        N = [(-n[0], -n[1], -n[2], -n[3]) for n in N]
    return N, A, total, vol6, edges


def _build_grid(V, F, A, ox, oy, oz, inv, NX, NXY, clock=None, deadline=None):
    """{cell key: [triangles]}: every triangle is listed in every cell it touches, and in the cell beyond a cell wall it
    only touches (a skin of 1e-6 cell). Returns None when `deadline` passes first.

    A triangle whose bounding box is at most two cells wide in two directions goes into every cell of that box (a small
    triangle, or a long strip along an axis). Any other triangle is cut into slabs one cell thick across its longest
    direction; in each slab it goes into the cells of the exact bounding box of its cross-section, so a large slanted
    face fills the cells along its plane and not the whole box round it."""
    grid = {}
    get = grid.get
    strides = (1, NX, NXY)
    for i, (a, b, c) in enumerate(F):
        if A[i] == 0.0:
            continue
        if deadline is not None and not i & 4095 and clock() > deadline:
            return None
        x0, y0, z0 = V[a]; x1, y1, z1 = V[b]; x2, y2, z2 = V[c]
        x0 = x0 * inv - ox; x1 = x1 * inv - ox; x2 = x2 * inv - ox
        y0 = y0 * inv - oy; y1 = y1 * inv - oy; y2 = y2 * inv - oy
        z0 = z0 * inv - oz; z1 = z1 * inv - oz; z2 = z2 * inv - oz
        ix0 = int(((x0 if x0 < x1 else x1) if (x0 if x0 < x1 else x1) < x2 else x2) - _SKIN)
        ix1 = int(((x0 if x0 > x1 else x1) if (x0 if x0 > x1 else x1) > x2 else x2) + _SKIN)
        iy0 = int(((y0 if y0 < y1 else y1) if (y0 if y0 < y1 else y1) < y2 else y2) - _SKIN)
        iy1 = int(((y0 if y0 > y1 else y1) if (y0 if y0 > y1 else y1) > y2 else y2) + _SKIN)
        iz0 = int(((z0 if z0 < z1 else z1) if (z0 if z0 < z1 else z1) < z2 else z2) - _SKIN)
        iz1 = int(((z0 if z0 > z1 else z1) if (z0 if z0 > z1 else z1) > z2 else z2) + _SKIN)
        wx, wy, wz = ix1 - ix0, iy1 - iy0, iz1 - iz0
        if (wx < 2) + (wy < 2) + (wz < 2) >= 2:
            for kz in range(iz0, iz1 + 1):
                for ky in range(iy0, iy1 + 1):
                    base = ky * NX + kz * NXY
                    for k in range(base + ix0, base + ix1 + 1):
                        cell = get(k)
                        if cell is None: grid[k] = [i]
                        elif cell[-1] != i: cell.append(i)
            continue
        # slabs across the longest direction d; e and f are the other two
        if wx >= wy and wx >= wz: p0, p1, p2, sd, se, sf = (x0, y0, z0), (x1, y1, z1), (x2, y2, z2), 1, NX, NXY
        elif wy >= wz: p0, p1, p2, sd, se, sf = (y0, x0, z0), (y1, x1, z1), (y2, x2, z2), NX, 1, NXY
        else: p0, p1, p2, sd, se, sf = (z0, x0, y0), (z1, x1, y1), (z2, x2, y2), NXY, 1, NX
        if p1[0] < p0[0]: p0, p1 = p1, p0
        if p2[0] < p1[0]:
            p1, p2 = p2, p1
            if p1[0] < p0[0]: p0, p1 = p1, p0
        d0, e0, f0 = p0; d1, e1, f1 = p1; d2, e2, f2 = p2
        span = d2 - d0
        le, lf = (e2 - e0) / span, (f2 - f0) / span                      # the long edge p0 -> p2
        has_a, has_b = d1 - d0 > 1e-12, d2 - d1 > 1e-12                  # the two short edges p0 -> p1 and p1 -> p2
        if has_a: ae, af = (e1 - e0) / (d1 - d0), (f1 - f0) / (d1 - d0)
        if has_b: be, bf = (e2 - e1) / (d2 - d1), (f2 - f1) / (d2 - d1)
        s, last, mid = int(d0 - _SKIN), int(d2 + _SKIN), int(d1)
        emin = emax = e0; fmin = fmax = f0
        while True:
            if s < last:                             # where the far wall of this slab cuts the long edge and the short ones
                x = s + 1.0
                if x < d0: x = d0
                elif x > d2: x = d2
                pe = e0 + le * (x - d0); pf = f0 + lf * (x - d0)
                if x < d1 and has_a: qe = e0 + ae * (x - d0); qf = f0 + af * (x - d0)
                elif has_b: qe = e1 + be * (x - d1); qf = f1 + bf * (x - d1)
                else: qe = e1; qf = f1
                if pe < emin: emin = pe
                if pe > emax: emax = pe
                if qe < emin: emin = qe
                if qe > emax: emax = qe
                if pf < fmin: fmin = pf
                if pf > fmax: fmax = pf
                if qf < fmin: fmin = qf
                if qf > fmax: fmax = qf
            else:                                    # the last slab holds the far corner
                if e2 < emin: emin = e2
                if e2 > emax: emax = e2
                if f2 < fmin: fmin = f2
                if f2 > fmax: fmax = f2
            if s == mid:                             # the slab that holds the middle corner
                if e1 < emin: emin = e1
                if e1 > emax: emax = e1
                if f1 < fmin: fmin = f1
                if f1 > fmax: fmax = f1
            base = s * sd
            ie0 = int(emin - _SKIN) * se; ie1 = int(emax + _SKIN) * se
            for kf in range(int(fmin - _SKIN), int(fmax + _SKIN) + 1):
                row = base + kf * sf
                for k in range(row + ie0, row + ie1 + 1, se):
                    cell = get(k)
                    if cell is None: grid[k] = [i]
                    elif cell[-1] != i: cell.append(i)
            if s >= last:
                break
            s += 1
            if pe < qe: emin = pe; emax = qe
            else: emin = qe; emax = pe
            if pf < qf: fmin = pf; fmax = qf
            else: fmin = qf; fmax = pf
    return grid


def _samples(V, F, A, spacing, rnd):
    """Sample points, one per spacing squared of surface, in file order. Returns arrays (x, y, z, triangle).

    How many points a triangle gets follows the running sum of the areas, so a patch of small triangles gets its share.
    Where they lie inside the triangle follows a low-discrepancy sequence that runs on from one triangle to the next:
    the long thin strips of a cylinder get their points at different heights, not all on one ring."""
    SX, SY, SZ, ST = array.array('d'), array.array('d'), array.array('d'), array.array('l')
    s2 = spacing * spacing
    acc = rnd.random() * s2
    r1, r2 = rnd.random(), rnd.random()
    sqrt = math.sqrt
    for i, (a, b, c) in enumerate(F):
        acc += A[i]
        if acc < s2:
            continue
        k = int(acc / s2); acc -= k * s2
        ax, ay, az = V[a]; bx, by, bz = V[b]; cx, cy, cz = V[c]
        for _ in range(k):
            r1 += 0.7548776662466927; r2 += 0.5698402909980532
            if r1 >= 1.0: r1 -= 1.0
            if r2 >= 1.0: r2 -= 1.0
            q = sqrt(r1); wa = 1.0 - q; wb = q * (1.0 - r2); wc = q * r2
            SX.append(wa * ax + wb * bx + wc * cx); SY.append(wa * ay + wb * by + wc * cy); SZ.append(wa * az + wb * bz + wc * cz); ST.append(i)
    return SX, SY, SZ, ST


def _fans(V, F, N, A, twin, nv, spacing, rnd):
    """Rays from the corners of the mesh and the corners themselves. One start point per `spacing` of edge length.

    fans     9 doubles per ray: px, py, pz, dx, dy, dz, kind, triangle, triangle; kind 0.0 = into the material from an
             inside corner (wall), 1.0 = into the air from an outside corner (slit); the two triangles of the edge
    corners  6 doubles per start point: px, py, pz, triangle, triangle, kind (0.0 inside corner, 1.0 outside corner)
    An edge beside a needle (a triangle less than 0.001 mm high: its normal means nothing) is not a corner."""
    fans, corners = array.array('d'), array.array('d')
    acc = rnd.random() * spacing
    sin, acos, sqrt = math.sin, math.acos, math.sqrt

    def needle(t):
        a, b, c = F[t]
        (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = V[a], V[b], V[c]
        longest = max((x1 - x0) ** 2 + (y1 - y0) ** 2 + (z1 - z0) ** 2, (x2 - x1) ** 2 + (y2 - y1) ** 2 + (z2 - z1) ** 2, (x0 - x2) ** 2 + (y0 - y2) ** 2 + (z0 - z2) ** 2)
        return 4.0 * A[t] * A[t] < 1e-6 * longest      # height = 2 * area / longest edge under 0.001

    for i, (a, b, c) in enumerate(F):
        n1 = N[i]
        for u, v, w in ((a, b, c), (b, c, a), (c, a, b)):
            if u > v:
                continue                             # each edge once, from the triangle that runs it upwards
            j = twin[v * nv + u]
            n2 = N[j]
            dot = n1[0] * n2[0] + n1[1] * n2[1] + n1[2] * n2[2]
            if dot > _C_CREASE or dot < _C_BLADE or needle(i) or needle(j):
                continue
            pu, pv = V[u], V[v]
            ex, ey, ez = pv[0] - pu[0], pv[1] - pu[1], pv[2] - pu[2]
            length = sqrt(ex * ex + ey * ey + ez * ez)
            acc += length
            if acc < spacing:
                continue
            k = int(acc / spacing); acc -= k * spacing
            # the other triangle's far corner lies above this triangle's plane: the surface folds towards the outside
            q = [x for x in F[j] if x != u and x != v]
            far = V[q[0]] if q else pu
            inside_corner = n1[0] * far[0] + n1[1] * far[1] + n1[2] * far[2] - n1[3] > 0.0
            phi = acos(dot); steps = int(math.ceil(phi / _FAN - 1e-9)); sp = sin(phi)
            sign, kind = (-1.0, 0.0) if inside_corner else (1.0, 1.0)
            for e in range(k):
                f = (e + 0.5) / k
                px, py, pz = pu[0] + f * ex, pu[1] + f * ey, pu[2] + f * ez
                corners.extend((px, py, pz, i, j, kind))
                for st in range(1, steps):
                    g = st / float(steps)
                    w1, w2 = sin((1.0 - g) * phi) / sp * sign, sin(g * phi) / sp * sign
                    fans.extend((px, py, pz, w1 * n1[0] + w2 * n2[0], w1 * n1[1] + w2 * n2[1], w1 * n1[2] + w2 * n2[2], kind, i, j))
    return fans, corners


def _regions(thin, srcs, kind, R, lo, hi, cell, per_sample, normal_of):
    """Connected patches of the thin samples of one kind, thinnest first. Nothing is left out.

    Samples in one cell of size `cell` or in touching cells are one patch, and so is a sample with whatever lies where
    its ray ended (the other face of the wall, the other wall of the gap). Samples under half of R and samples from
    half of R upwards are grouped apart: the first make the thin spots, the second what lies near them. A patch of the
    second kind that touches exactly one thin spot is that spot's rim and is counted there ("rim_samples",
    "rim_area"); one that touches several, or none, is a region of its own."""
    n = len(thin) // _STRIDE
    if not n:
        return []
    half = 0.5 * R
    inv = 1.0 / cell
    NX = int((hi[0] - lo[0]) * inv) + 3; NY = int((hi[1] - lo[1]) * inv) + 3; NZ = int((hi[2] - lo[2]) * inv) + 3
    NXY = NX * NY; BAND = NXY * NZ
    lx, ly, lz = lo[0] - cell, lo[1] - cell, lo[2] - cell
    ids, parent, owner = {}, [], array.array('l', bytes(array.array('l').itemsize * n))
    for e in range(n):
        o = e * _STRIDE
        key = (BAND if thin[o] >= half else 0) + int((thin[o + 1] - lx) * inv) + NX * int((thin[o + 2] - ly) * inv) + NXY * int((thin[o + 3] - lz) * inv)
        cid = ids.get(key)
        if cid is None:
            cid = ids[key] = len(parent); parent.append(cid)
        owner[e] = cid

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x

    near = [dx + NX * dy + NXY * dz for dz in (0, 1) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if (dz, dy, dx) > (0, 0, 0)]
    get = ids.get
    for key, cid in ids.items():
        for d in near:
            other = get(key + d)
            if other is not None:
                ra, rb = find(cid), find(other)
                if ra != rb: parent[ra] = rb
    for e in range(n):                               # the far side of the wall (or of the gap) is the same region
        o = e * _STRIDE
        other = get((BAND if thin[o] >= half else 0) + int((thin[o + 4] - lx) * inv) + NX * int((thin[o + 5] - ly) * inv) + NXY * int((thin[o + 6] - lz) * inv))
        if other is not None:
            ra, rb = find(owner[e]), find(other)
            if ra != rb: parent[ra] = rb
    rim_of = {}                                      # patch of the upper band -> the one thin spot it touches, or -1 for several
    around = [BAND + dx + NX * dy + NXY * dz for dz in (-1, 0, 1) for dy in (-1, 0, 1) for dx in (-1, 0, 1)]
    for key, cid in ids.items():
        if key < BAND:
            spot = find(cid)
            for d in around:
                other = get(key + d)
                if other is not None:
                    patch = find(other)
                    was = rim_of.get(patch)
                    if was is None: rim_of[patch] = spot
                    elif was != spot: rim_of[patch] = -1
    stats, values, rims = {}, {}, {}                 # root -> [samples, weight, min, its sample, x0, y0, z0, x1, y1, z1]; -> thicknesses; -> [samples, weight]
    for e in range(n):
        o = e * _STRIDE
        t, x, y, z, w = thin[o], thin[o + 1], thin[o + 2], thin[o + 3], thin[o + 7]
        root = find(owner[e])
        spot = rim_of.get(root, -1)
        if spot >= 0:
            rim = rims.get(spot)
            if rim is None: rims[spot] = [1, w]
            else: rim[0] += 1; rim[1] += w
            continue
        st = stats.get(root)
        if st is None:
            stats[root] = [1, w, t, e, x, y, z, x, y, z]; values[root] = array.array('d', (t,))
            continue
        st[0] += 1; st[1] += w
        if t < st[2]: st[2] = t; st[3] = e
        if x < st[4]: st[4] = x
        elif x > st[7]: st[7] = x
        if y < st[5]: st[5] = y
        elif y > st[8]: st[8] = y
        if z < st[6]: st[6] = z
        elif z > st[9]: st[9] = z
        values[root].append(t)
    out = []
    for root, st in stats.items():
        ts = sorted(values[root]); m = st[0]; o = st[3] * _STRIDE
        rim = rims.get(root, (0, 0.0))
        out.append({'kind': 'slit' if kind else 'wall', 'min': st[2],
                    'p50': round(ts[m // 2] if m % 2 else 0.5 * (ts[m // 2 - 1] + ts[m // 2]), 4),
                    'area': round(st[1] * per_sample, 2),
                    'point': [round(thin[o + 1], 3), round(thin[o + 2], 3), round(thin[o + 3], 3)],
                    'normal': [round(v, 4) + 0.0 for v in normal_of(srcs[st[3]])],
                    'samples': m, 'corner_samples': m - int(round(st[1])),
                    'under': half if st[2] < half else R, 'rim_samples': rim[0], 'rim_area': round(rim[1] * per_sample, 2),
                    'bbox': [[round(st[4], 3), round(st[5], 3), round(st[6], 3)], [round(st[7], 3), round(st[8], 3), round(st[9], 3)]]})
    out.sort(key=lambda r: (r['min'], r['point']))
    return out


def measure(stl_path, report_under, budget_s=60.0, seed=1):
    """Measure one STL.

    stl_path      path of the STL (the model frame)
    report_under  mm: regions thinner than this are listed (the caller passes 2 x the largest limit it will judge with)
    budget_s      seconds this call may take; less time = fewer samples = lower coverage, never a silent pass
    seed          seed of the sampler, so two runs on one file give the same facts

    Returns {"stl": the path as given, "triangles": n, "bbox": [[x, y, z], [x, y, z]], "area": mm2 of the surface,
             "samples": n rays shot, "spacing": mean distance between samples in mm,
             "coverage": 0.0 .. 1.0 share of the surface area that got at least one sample within `spacing`,
             "report_under": as given, "budget_s": as given, "elapsed_s": seconds used,
             "regions": [{"kind": "wall" | "slit",
                          "min": 0.86          thinnest material (wall) or narrowest air gap (slit) of the region, mm
                          "p50": 1.1           median over the region's samples
                          "area": 14.2         mm2 of surface in the region
                          "point": [x, y, z]   where the minimum is, model frame
                          "normal": [x, y, z]  outward surface normal there
                          "samples": 12,
                          "bbox": [[x, y, z], [x, y, z]]}]}
    Regions are connected patches of samples under `report_under`, thinnest first.
    Raises gate_common.GateError when the file is missing, is not an STL or is not closed (run stl_mesh first).

    More exactly, and with the keys added to the list above (the top of this file says how the measuring works):
      "samples"         sample points worked off; from each one ray runs inwards and one outwards
      "spacing_target"  the spacing a full run uses: report_under / 4, finer on a small part
      "planned"         sample points a full run shoots at that spacing
      "coverage"        samples / planned, 1.0 only for a full run. It is the share of the surface sampled at
                        `spacing_target`; "spacing" is the mean distance between the samples that were shot, so a run
                        that stopped early shows a larger spacing AND a coverage under 1
      "stopped"         None | "budget" (the time ended) | "max_samples" (the part needs more than MAX_SAMPLES points)
      "corner_rays"     fan rays shot from the corners of the mesh (see the top); "corner_rays_planned" a full run's count
      "wedge_samples"   {"wall": n, "slit": n} thin samples and fan rays left out because the two faces meet in an edge
      "regions_dropped" regions beyond REGION_CAP per kind (the thickest ones); 0 means the list is complete
      "phases"          seconds per step: read, samples, grid, corner_rays, rays, regions
      a region also has "under" (the band it was grouped in: report_under / 2 or report_under), "corner_samples"
      (how many of its samples are fan rays; they add no area: a region seen only by fan rays has "area" 0.0) and
      "rim_samples" / "rim_area" (what lies round a thin spot between report_under / 2 and report_under).
    A wall region's "point" lies on one face of the wall and "normal" points out of the material there; for a slit the
    point lies on one wall of the gap and the normal points into the gap."""
    clock = time.perf_counter
    t0 = clock()
    try:
        R = float(report_under); budget = float(budget_s)
    except (TypeError, ValueError):
        raise GateError('stl_walls: report_under and budget_s must be numbers')
    if not R > 0.0:
        raise GateError('stl_walls: report_under must be above 0, got %r' % (report_under,))
    phases, mark = {}, [t0]

    def lap(name):
        now = clock(); phases[name] = round(phases.get(name, 0.0) + now - mark[0], 3); mark[0] = now

    V, F, _dropped = _mesh._read_stl(stl_path)
    if not F:
        raise GateError('%s holds no triangle' % (stl_path,))
    nv, nt = len(V), len(F)
    twin = _twins(F, nv, stl_path)
    N, A, total, _vol6, edges = _normals(V, F)
    if not total > 0.0:
        raise GateError('%s has no surface' % (stl_path,))
    xs, ys, zs = zip(*V)
    lo, hi = (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))
    del xs, ys, zs
    rnd = random.Random(seed)
    lap('read')

    # ---- how fine: the spacing a full run needs, and the spacing this run can afford
    spacing_target = R / 4.0
    if total / (spacing_target * spacing_target) < _FINE:
        spacing_target = max(R / 16.0, math.sqrt(total / _FINE))
    full = total / (spacing_target * spacing_target)
    spacing_gen, stopped = spacing_target, None
    if full > MAX_SAMPLES:
        spacing_gen, stopped = math.sqrt(total / MAX_SAMPLES), 'max_samples'

    # ---- what to shoot
    SX, SY, SZ, ST = _samples(V, F, A, spacing_gen, rnd)
    ns = len(ST)
    planned = ns if spacing_gen == spacing_target else int(round(full))
    fans, corners = _fans(V, F, N, A, twin, nv, spacing_gen, rnd)
    nfans = len(fans) // 9
    del twin
    # the corners in a coarse grid of their own: "do these two faces meet in an edge near here?"
    ccell = max(R, 2.0 * spacing_gen); cinv = 1.0 / ccell
    CNX = int((hi[0] - lo[0]) * cinv) + 3; CNY = int((hi[1] - lo[1]) * cinv) + 3; CNXY = CNX * CNY
    cx0, cy0, cz0 = lo[0] - ccell, lo[1] - ccell, lo[2] - ccell
    corner_cells = {}
    for o in range(0, len(corners), 6):
        corner_cells.setdefault(int((corners[o] - cx0) * cinv) + CNX * int((corners[o + 1] - cy0) * cinv) + CNXY * int((corners[o + 2] - cz0) * cinv), []).append(o)
    lap('samples')

    def facts(shot, corner_rays, regions, dropped, wedges):
        coverage = min(1.0, shot / float(planned)) if planned else 0.0
        if shot < planned or stopped:
            coverage = min(coverage, 0.9999)
        lap('regions')
        return {'stl': stl_path, 'triangles': nt, 'bbox': [[round(c, 6) for c in lo], [round(c, 6) for c in hi]],
                'area': round(total, 2), 'samples': shot, 'planned': planned,
                'spacing': round(math.sqrt(total / shot), 4) if shot else 0.0, 'spacing_target': round(spacing_target, 4),
                'coverage': math.floor(coverage * 10000.0) / 10000.0, 'stopped': stopped,
                'corner_rays': corner_rays, 'corner_rays_planned': nfans, 'wedge_samples': {'wall': wedges[0], 'slit': wedges[1]},
                'report_under': report_under, 'budget_s': budget_s, 'elapsed_s': round(clock() - t0, 3), 'phases': phases,
                'regions': regions, 'regions_dropped': dropped}

    # ---- the grid: cells of about R / 2.5; larger when the surface would fill too many of them
    cell = max(R / 2.5, math.sqrt(total / 300000.0))
    while 1.5 * nt + edges / cell + total / (cell * cell) > _PAIRS and cell < 8.0 * R:
        cell *= 1.25
    inv = 1.0 / cell
    pad = int(R * inv) + 2
    # 0.37: the faces of the bounding box do not lie on cell walls
    gx, gy, gz = lo[0] - (pad + 0.37) * cell, lo[1] - (pad + 0.37) * cell, lo[2] - (pad + 0.37) * cell
    NX = int((hi[0] - gx) * inv) + pad + 3; NY = int((hi[1] - gy) * inv) + pad + 3
    NXY = NX * NY
    ox, oy, oz = gx * inv, gy * inv, gz * inv
    deadline = t0 + budget * 0.92 - 0.2              # keep a little for the grouping at the end
    grid = _build_grid(V, F, A, ox, oy, oz, inv, NX, NXY, clock, deadline)
    lap('grid')
    if grid is None:                                 # the time ended before a single ray: nothing is proven
        stopped = 'budget'
        return facts(0, 0, [], 0, (0, 0))
    get = grid.get
    BARY = {}

    def bary(j):
        a, b, c = F[j]
        x0, y0, z0 = V[a]; x1, y1, z1 = V[b]; x2, y2, z2 = V[c]
        ux, uy, uz, vx, vy, vz = x1 - x0, y1 - y0, z1 - z0, x2 - x0, y2 - y0, z2 - z0
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        nn = nx * nx + ny * ny + nz * nz
        bx, by, bz = (vy * nz - vz * ny) / nn, (vz * nx - vx * nz) / nn, (vx * ny - vy * nx) / nn       # (v x n) / nn
        cx, cy, cz = (ny * uz - nz * uy) / nn, (nz * ux - nx * uz) / nn, (nx * uy - ny * ux) / nn       # (n x u) / nn
        if len(BARY) >= _BARY_CACHE:
            BARY.clear()
        r = BARY[j] = (bx, by, bz, -(bx * x0 + by * y0 + bz * z0), cx, cy, cz, -(cx * x0 + cy * y0 + cz * z0))
        return r

    bget = BARY.get

    def cast(px, py, pz, dx, dy, dz, want):
        """First triangle along the ray within R that ends the material (want = 1.0) or starts it (want = -1.0):
        (distance, triangle, cosine between its normal and the ray); (R, -1, 0.0) when there is none."""
        fx = px * inv - ox; fy = py * inv - oy; fz = pz * inv - oz
        ix = int(fx); iy = int(fy); iz = int(fz)
        k = ix + NX * iy + NXY * iz
        if dx > 1e-12: ddx = cell / dx; tx = (ix + 1 - fx) * ddx; kx = 1
        elif dx < -1e-12: ddx = -cell / dx; tx = (fx - ix) * ddx; kx = -1
        else: ddx = 0.0; tx = _INF; kx = 0
        if dy > 1e-12: ddy = cell / dy; ty = (iy + 1 - fy) * ddy; ky = NX
        elif dy < -1e-12: ddy = -cell / dy; ty = (fy - iy) * ddy; ky = -NX
        else: ddy = 0.0; ty = _INF; ky = 0
        if dz > 1e-12: ddz = cell / dz; tz = (iz + 1 - fz) * ddz; kz = NXY
        elif dz < -1e-12: ddz = -cell / dz; tz = (fz - iz) * ddz; kz = -NXY
        else: ddz = 0.0; tz = _INF; kz = 0
        best, bj, bc = R, -1, 0.0
        while True:
            tris = get(k)
            if tris is not None:
                for j in tris:
                    mx, my, mz, md = N[j]
                    cs = (mx * dx + my * dy + mz * dz) * want
                    if cs < 1e-6:
                        continue
                    t = (md - mx * px - my * py - mz * pz) * want / cs
                    if t >= best or t < _EPS:
                        continue
                    qx = px + t * dx; qy = py + t * dy; qz = pz + t * dz
                    bb = bget(j) or bary(j)
                    u = bb[0] * qx + bb[1] * qy + bb[2] * qz + bb[3]
                    if u < -_TOL:
                        continue
                    v = bb[4] * qx + bb[5] * qy + bb[6] * qz + bb[7]
                    if v < -_TOL or u + v > 1.0 + _TOL:
                        continue
                    best = t; bj = j; bc = cs
            if tx < ty:
                if tx < tz: tin = tx; tx += ddx; k += kx
                else: tin = tz; tz += ddz; k += kz
            elif ty < tz: tin = ty; ty += ddy; k += ky
            else: tin = tz; tz += ddz; k += kz
            if tin >= best:
                return best, bj, bc

    cget = corner_cells.get

    def meet(px, py, pz, nx, ny, nz, mx, my, mz, reach, outside):
        """Do a face with normal n and a face with normal m meet in an edge within `reach` of this point? outside 1.0:
        an edge where the material is the narrow side (a wedge of material); 0.0: where the air is (a groove)."""
        r2 = reach * reach
        ix0 = int((px - reach - cx0) * cinv); ix1 = int((px + reach - cx0) * cinv)
        iy0 = int((py - reach - cy0) * cinv); iy1 = int((py + reach - cy0) * cinv)
        iz0 = int((pz - reach - cz0) * cinv); iz1 = int((pz + reach - cz0) * cinv)
        for kz in range(iz0 if iz0 > 0 else 0, iz1 + 1):
            for ky in range(iy0 if iy0 > 0 else 0, min(iy1, CNY - 1) + 1):
                for kx in range(ix0 if ix0 > 0 else 0, min(ix1, CNX - 1) + 1):
                    for o in cget(kx + CNX * ky + CNXY * kz) or ():
                        if corners[o + 5] != outside:
                            continue
                        dx = corners[o] - px; dy = corners[o + 1] - py; dz = corners[o + 2] - pz
                        if dx * dx + dy * dy + dz * dz > r2:
                            continue
                        a = N[int(corners[o + 3])]; b = N[int(corners[o + 4])]
                        if ((a[0] * nx + a[1] * ny + a[2] * nz > _C_SAME and b[0] * mx + b[1] * my + b[2] * mz > _C_SAME)
                                or (b[0] * nx + b[1] * ny + b[2] * nz > _C_SAME and a[0] * mx + a[1] * my + a[2] * mz > _C_SAME)):
                            return True
        return False

    def wedge(px, py, pz, nx, ny, nz, j, t, cs, outside):
        """Is a ray of length t from a face with normal n onto triangle j (cosine cs between the two faces) only the thin
        end of a wedge: the two faces 30 to 55 degrees apart and meeting in an edge where the wedge comes to its point?"""
        if not _C_FACING < cs < _C_WEDGE:
            return False
        mx, my, mz, _md = N[j]
        return meet(px, py, pz, nx, ny, nz, mx, my, mz, 1.3 * t * cs / math.sqrt(1.0 - cs * cs) + spacing_gen, outside)

    thin = (array.array('d'), array.array('d'))      # wall, slit: _STRIDE doubles per thin sample
    srcs = (array.array('l'), array.array('l'))      # per thin sample: its triangle (>= 0) or -(1 + index into fan_normals)
    fan_normals = array.array('d')
    wedges = [0, 0]
    over = False

    # ---- corner fans first: they are few
    corner_rays = 0
    for e in range(nfans):
        if not e & 255 and clock() > deadline:
            over = True; break
        corner_rays += 1
        px, py, pz, dx, dy, dz, kind, ta, tb = fans[9 * e:9 * e + 9]
        kind = int(kind)
        t, j, cs = cast(px, py, pz, dx, dy, dz, -1.0 if kind else 1.0)
        if j >= 0 and cs > _C_FACING and round(t, 4) < R:
            mx, my, mz, md = N[j]
            off = abs(md - mx * px - my * py - mz * pz)               # from the corner to the plane of the far face
            skip = False
            for nx, ny, nz, _nd in (N[int(ta)], N[int(tb)]):          # the far face and one face of the corner: a wedge?
                ci = -(nx * mx + ny * my + nz * mz)
                if _C_FACING < ci < _C_WEDGE and wedge(px, py, pz, nx, ny, nz, j, off / ci, ci, 0.0 if kind else 1.0):
                    skip = True; break
            if skip:
                wedges[kind] += 1; continue
            thin[kind].extend((round(t, 4), px, py, pz, px + t * dx, py + t * dy, pz + t * dz, 0.0))
            srcs[kind].append(-(1 + len(fan_normals) // 3))
            fan_normals.extend((dx, dy, dz) if kind else (-dx, -dy, -dz))
    lap('corner_rays')

    # ---- the surface samples, in interleaved passes
    shot = 0
    wall, slit, wsrc, ssrc = thin[0], thin[1], srcs[0], srcs[1]
    if not over:
        for first in _PASSES:
            for idx in range(first, ns, 16):
                if not shot & 255 and clock() > deadline:
                    over = True; break
                shot += 1
                px = SX[idx]; py = SY[idx]; pz = SZ[idx]; ti = ST[idx]
                nx, ny, nz, _nd = N[ti]
                t, j, cs = cast(px, py, pz, -nx, -ny, -nz, 1.0)
                if j >= 0 and cs > _C_FACING and round(t, 4) < R:
                    if cs < _C_WEDGE and wedge(px, py, pz, nx, ny, nz, j, t, cs, 1.0):
                        wedges[0] += 1
                    else:
                        wall.extend((round(t, 4), px, py, pz, px - t * nx, py - t * ny, pz - t * nz, 1.0)); wsrc.append(ti)
                t, j, cs = cast(px, py, pz, nx, ny, nz, -1.0)
                if j >= 0 and cs > _C_FACING and round(t, 4) < R:
                    if cs < _C_WEDGE and wedge(px, py, pz, nx, ny, nz, j, t, cs, 0.0):
                        wedges[1] += 1
                    else:
                        slit.extend((round(t, 4), px, py, pz, px + t * nx, py + t * ny, pz + t * nz, 1.0)); ssrc.append(ti)
            if over:
                break
    if over:
        stopped = 'budget'
    lap('rays')
    del grid, SX, SY, SZ, ST, fans, corners, corner_cells
    BARY.clear()

    # ---- regions
    per_sample = total / shot if shot else 0.0
    link = 1.5 * (math.sqrt(per_sample) if shot else spacing_target)

    def normal_of(src):
        if src >= 0:
            return N[src][:3]
        o = 3 * (-1 - src)
        return fan_normals[o:o + 3]

    regions, dropped = [], 0
    for kind in (0, 1):
        rs = _regions(thin[kind], srcs[kind], kind, R, lo, hi, link, per_sample, normal_of)
        dropped += max(0, len(rs) - REGION_CAP)
        regions += rs[:REGION_CAP]
    regions.sort(key=lambda r: (r['min'], r['kind'], r['point']))
    return facts(shot, corner_rays, regions, dropped, wedges)


def main(argv=None):
    """python3 stl_walls.py <file.stl> [--under MM] [--budget S] [--json]: print the regions (or the facts as JSON).
    Returns 0, or 4 when the file cannot be read. It never judges: there is no pass or fail here."""
    import argparse
    ap = argparse.ArgumentParser(prog='stl_walls.py', description='Wall thickness of an STL: thin material and narrow air gaps. It lists, it does not judge.')
    ap.add_argument('stl')
    ap.add_argument('--under', type=float, default=3.0, help='list regions thinner than this, mm (default 3.0)')
    ap.add_argument('--budget', type=float, default=60.0, help='seconds this run may take (default 60)')
    ap.add_argument('--json', action='store_true', help='print the facts as JSON')
    try:
        args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit:
        return gate_common.EXIT_INFRA
    try:
        facts = measure(args.stl, args.under, budget_s=args.budget)
    except GateError as e:
        sys.stderr.write('GATE ERROR  %s\n' % e)
        return gate_common.EXIT_INFRA
    if args.json:
        print(json.dumps(facts, indent=1, sort_keys=True))
        return gate_common.EXIT_PASS
    print('%s: %d triangles, %.0f mm2, %d samples %.2f mm apart, coverage %.1f %%%s, %.1f s'
          % (os.path.basename(str(args.stl)), facts['triangles'], facts['area'], facts['samples'], facts['spacing'],
             100.0 * facts['coverage'], ' (stopped: %s)' % facts['stopped'] if facts['stopped'] else '', facts['elapsed_s']))
    for r in facts['regions']:
        print('%-4s min %7.3f  p50 %7.3f  area %9.2f mm2  %5d samples  at (%.2f, %.2f, %.2f)  normal (%.2f, %.2f, %.2f)  under %s'
              % (r['kind'], r['min'], r['p50'], r['area'], r['samples'], r['point'][0], r['point'][1], r['point'][2],
                 r['normal'][0], r['normal'][1], r['normal'][2], r['under']))
    if facts['regions_dropped']:
        print('... and %d more regions (the thickest ones are not listed)' % facts['regions_dropped'])
    if not facts['regions']:
        print('nothing under %s mm%s' % (args.under, '' if facts['coverage'] >= 1.0 else ' on the share that was sampled: not proven'))
    return gate_common.EXIT_PASS


if __name__ == '__main__':
    sys.exit(main())
