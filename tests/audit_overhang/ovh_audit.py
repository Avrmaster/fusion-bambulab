#!/usr/bin/env python3
"""Independent overhang audit of printed meshes (pure Python 3 stdlib).

Usage:
  python3 ovh_audit.py [PART ...]            -> writes audit.json (merging per-part cache)
Library: analyze(V, T) -> dict  (V = list of (x,y,z) in plate frame, T = list of (i,j,k))
"""
import html
import json
import math
import os
import re
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict

REPO = os.path.abspath(os.environ.get('PROJECT', os.getcwd()))              # the project folder (fusion/parts.json)
HERE = os.path.abspath(os.environ.get('AUDIT_OUT', os.path.join(os.path.expanduser('~'), '.print-engineering', 'audit_overhang')))
os.makedirs(HERE, exist_ok=True)

OVH_DEG = 44.5          # slope < 45 - 0.5 tolerance = overhang
FLAT_DEG = 1.0          # flat ceiling
BED_MM = 0.3            # triangles fully within 0.3 mm of z min are ignored
BRIDGE_MM = 6.0
MIN_AREA = 0.25
MIN_RIDGE_LEN = 0.5
RIDGE_DEG = 44.5
RELIEF_D = (7.5, 6.5, 5.9)
LEAD_D = 4.4
BORE_D = 3.6
D_TOL = 0.1
RELIEF_LEN, RELIEF_LEN_TOL = 0.54, 0.2
LEAD_LEN, LEAD_LEN_TOL = 1.5, 0.3
N_DIRS = 18             # chord directions (10 deg steps)
FORBIDDEN = ('overhang', 'long_bridge', 'round_hole', 'shallow_ridge')

NS = {'c': 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'}
P_NS = '{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}'


# ----------------------------------------------------------------- vectors
def sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
def norm(a): return math.sqrt(dot(a, a))


def unit(a):
    n = norm(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > 0 else (0.0, 0.0, 0.0)


def angle_deg(a, b):
    return math.degrees(math.acos(max(-1.0, min(1.0, dot(a, b)))))


# ----------------------------------------------------------------- 3MF reading
def parse_tf(s):
    if not s:
        return [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]
    v = [float(x) for x in s.split()]
    assert len(v) == 12
    return v


def tf_apply(m, p):
    x, y, z = p
    return (x * m[0] + y * m[3] + z * m[6] + m[9],
            x * m[1] + y * m[4] + z * m[7] + m[10],
            x * m[2] + y * m[5] + z * m[8] + m[11])


def tf_mul(a, b):
    """first a then b (row-vector convention)."""
    r = []
    for i in range(3):
        for j in range(3):
            r.append(sum(a[i * 3 + k] * b[k * 3 + j] for k in range(3)))
    t = tf_apply(b, (a[9], a[10], a[11]))
    return r + list(t)


class Project:
    def __init__(self, path):
        self.path = path
        self.z = zipfile.ZipFile(path, 'r')
        self.root = ET.fromstring(self.z.read('3D/3dmodel.model'))
        cfg = self.z.read('Metadata/model_settings.config').decode('utf-8')
        self.names, self.part_sub = {}, {}
        for m in re.finditer(r'<object id="(\d+)">(.*?)</object>', cfg, re.S):
            oid, body = m.group(1), m.group(2)
            head = body.split('<part', 1)[0]
            nm = re.search(r'<metadata key="name" value="([^"]*)"', head)
            if nm:
                self.names[oid] = html.unescape(nm.group(1))
            for pm in re.finditer(r'<part id="(\d+)" subtype="([^"]+)"', body):
                self.part_sub[(oid, pm.group(1))] = pm.group(2)
        self.objects = {o.get('id'): o for o in self.root.find('c:resources', NS).findall('c:object', NS)}
        self.items = {}
        for it in self.root.find('c:build', NS).findall('c:item', NS):
            self.items.setdefault(it.get('objectid'), it)
        self._files = {}

    def _model(self, path):
        path = path.lstrip('/')
        if path not in self._files:
            r = ET.fromstring(self.z.read(path))
            self._files[path] = {o.get('id'): o for o in r.find('c:resources', NS).findall('c:object', NS)}
        return self._files[path]

    @staticmethod
    def _mesh(obj):
        mesh = obj.find('c:mesh', NS)
        V = [(float(v.get('x')), float(v.get('y')), float(v.get('z')))
             for v in mesh.find('c:vertices', NS).findall('c:vertex', NS)]
        T = [(int(t.get('v1')), int(t.get('v2')), int(t.get('v3')))
             for t in mesh.find('c:triangles', NS).findall('c:triangle', NS)]
        return V, T

    def find(self, name):
        return [oid for oid, n in self.names.items() if n == name]

    def object_mesh(self, oid):
        """normal_part meshes of top object oid, in plate coordinates."""
        obj = self.objects[oid]
        item = self.items.get(oid)
        itf = parse_tf(item.get('transform') if item is not None else None)
        V, T, skipped = [], [], []
        comps = obj.find('c:components', NS)
        if comps is None:
            parts = [(obj, [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0], oid)]
        else:
            parts = []
            for c in comps.findall('c:component', NS):
                cid = c.get('objectid')
                sub_t = self.part_sub.get((oid, cid), 'normal_part')
                if sub_t != 'normal_part':
                    skipped.append(sub_t)
                    continue
                p = c.get(P_NS + 'path')
                src = self._model(p)[cid] if p else self.objects[cid]
                parts.append((src, parse_tf(c.get('transform')), cid))
        for src, ctf, _ in parts:
            m = tf_mul(ctf, itf)
            v, t = self._mesh(src)
            base = len(V)
            V.extend(tf_apply(m, p) for p in v)
            T.extend((a + base, b + base, c + base) for a, b, c in t)
        zrot = math.degrees(math.atan2(itf[1], itf[0]))
        return V, T, round(zrot, 3), skipped, (item.get('printable') if item is not None else None)


# ----------------------------------------------------------------- circle fit
def solve3(A, b):
    M = [A[i][:] + [b[i]] for i in range(3)]
    for c in range(3):
        p = max(range(c, 3), key=lambda r: abs(M[r][c]))
        if abs(M[p][c]) < 1e-14:
            return None
        M[c], M[p] = M[p], M[c]
        for r in range(3):
            if r != c:
                f = M[r][c] / M[c][c]
                for k in range(c, 4):
                    M[r][k] -= f * M[c][k]
    return [M[i][3] / M[i][i] for i in range(3)]


def fit_circle(pts):
    """Kasa fit; returns (cx, cy, r, max_abs_residual) or None."""
    n = len(pts)
    if n < 3:
        return None
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    Sxx = Sxy = Syy = Sx = Sy = Sxz = Syz = Sz = 0.0
    for x, y in pts:
        x -= mx; y -= my
        z = x * x + y * y
        Sxx += x * x; Sxy += x * y; Syy += y * y; Sx += x; Sy += y
        Sxz += x * z; Syz += y * z; Sz += z
    sol = solve3([[Sxx, Sxy, Sx], [Sxy, Syy, Sy], [Sx, Sy, n]], [-Sxz, -Syz, -Sz])
    if sol is None:
        return None
    D, E, F = sol
    cx, cy = -D / 2, -E / 2
    r2 = cx * cx + cy * cy - F
    if r2 <= 0:
        return None
    r = math.sqrt(r2)
    res = max(abs(math.hypot(x - mx - cx, y - my - cy) - r) for x, y in pts)
    return cx + mx, cy + my, r, res


def circ_tol(r):
    return 0.02 + 0.005 * r


def coverage_deg(pts, cx, cy):
    angs = sorted(math.atan2(y - cy, x - cx) for x, y in pts)
    if len(angs) < 2:
        return 0.0
    gaps = [angs[i + 1] - angs[i] for i in range(len(angs) - 1)]
    gaps.append(angs[0] + 2 * math.pi - angs[-1])
    return math.degrees(2 * math.pi - max(gaps))


def distinct2d(pts, q=0.005):
    seen, out = set(), []
    for p in pts:
        k = (round(p[0] / q), round(p[1] / q))
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


# ----------------------------------------------------------------- analysis
class Mesh:
    def __init__(self, V, T):
        # weld
        key2i, Vw, remap = {}, [], []
        for p in V:
            k = (round(p[0], 4), round(p[1], 4), round(p[2], 4))
            i = key2i.get(k)
            if i is None:
                i = key2i[k] = len(Vw)
                Vw.append(p)
            remap.append(i)
        self.n_input_tris = len(T)
        tris = []
        for a, b, c in T:
            a, b, c = remap[a], remap[b], remap[c]
            if a == b or b == c or a == c:
                continue
            tris.append((a, b, c))
        xs = [p[0] for p in Vw]; ys = [p[1] for p in Vw]; zs = [p[2] for p in Vw]
        self.mins = (min(xs), min(ys), min(zs))
        self.maxs = (max(xs), max(ys), max(zs))
        mn = self.mins
        self.V = [(p[0] - mn[0], p[1] - mn[1], p[2] - mn[2]) for p in Vw]
        self.T, self.N, self.A, self.C = [], [], [], []
        for t in tris:
            p0, p1, p2 = (self.V[i] for i in t)
            cr = cross(sub(p1, p0), sub(p2, p0))
            a2 = norm(cr)
            if a2 < 1e-10:
                continue
            self.T.append(t)
            self.N.append((cr[0] / a2, cr[1] / a2, cr[2] / a2))
            self.A.append(a2 / 2)
            self.C.append(((p0[0] + p1[0] + p2[0]) / 3, (p0[1] + p1[1] + p2[1]) / 3, (p0[2] + p1[2] + p2[2]) / 3))
        self.edge_tris = defaultdict(list)
        for ti, (a, b, c) in enumerate(self.T):
            for e in ((a, b), (b, c), (c, a)):
                self.edge_tris[(min(e), max(e))].append(ti)
        self.vert_tris = defaultdict(list)
        for ti, t in enumerate(self.T):
            for v in t:
                self.vert_tris[v].append(ti)
        self.slope = [math.degrees(math.acos(max(-1.0, min(1.0, -n[2])))) for n in self.N]
        vol = 0.0
        for (a, b, c) in self.T:
            vol += dot(self.V[a], cross(self.V[b], self.V[c])) / 6
        self.volume = vol

    def tri_edges(self, ti):
        a, b, c = self.T[ti]
        return ((min(a, b), max(a, b)), (min(b, c), max(b, c)), (min(c, a), max(c, a)))

    def edge_nbrs(self, ti):
        for e in self.tri_edges(ti):
            for u in self.edge_tris[e]:
                if u != ti:
                    yield u

    def on_bed(self, ti):
        return all(self.V[v][2] <= BED_MM for v in self.T[ti])


def jacobi_min_eig(S):
    """eigenvector of the smallest eigenvalue of a symmetric 3x3 matrix."""
    A = [row[:] for row in S]
    Vv = [[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]]
    for _ in range(30):
        p, q = max(((0, 1), (0, 2), (1, 2)), key=lambda pq: abs(A[pq[0]][pq[1]]))
        if abs(A[p][q]) < 1e-14:
            break
        th = 0.5 * math.atan2(2 * A[p][q], A[q][q] - A[p][p])
        c, s_ = math.cos(th), math.sin(th)
        for k in range(3):
            akp, akq = A[k][p], A[k][q]
            A[k][p], A[k][q] = c * akp - s_ * akq, s_ * akp + c * akq
        for k in range(3):
            apk, aqk = A[p][k], A[q][k]
            A[p][k], A[q][k] = c * apk - s_ * aqk, s_ * apk + c * aqk
        for k in range(3):
            vkp, vkq = Vv[k][p], Vv[k][q]
            Vv[k][p], Vv[k][q] = c * vkp - s_ * vkq, s_ * vkp + c * vkq
    i = min(range(3), key=lambda j: A[j][j])
    return (Vv[0][i], Vv[1][i], Vv[2][i])


def _canon_h(a):
    a = unit((a[0], a[1], 0.0))
    if a[0] < -1e-9 or (abs(a[0]) < 1e-9 and a[1] < 0):
        a = (-a[0], -a[1], 0.0)
    return a


def find_cylinders(M, seeds):
    """Grow cylinder patches with a horizontal axis from overhang seed triangles."""
    cyls, done = [], set()
    for s in seeds:
        if s in done:
            continue
        n0 = M.N[s]
        cand = []
        snap = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]

        def addc(a, maxz=0.05):
            if norm(a) < 1e-9:
                return
            a = unit(a)
            if abs(a[2]) >= maxz or abs(dot(a, n0)) > 0.15:
                return
            a = _canon_h(a)
            for w in snap:  # snap to a world axis within 1 deg
                if abs(dot(a, w)) > math.cos(math.radians(1.0)):
                    a = w
            if all(abs(dot(a, b)) < 0.9998 for b in cand):
                cand.append(a)
        # (0) world x / y first
        addc((1.0, 0.0, 0.0))
        addc((0.0, 1.0, 0.0))
        # (1) smallest eigenvector of the normal scatter of the local neighbourhood
        local, layer = {s}, [s]
        for _ in range(3):
            nxt = []
            for t in layer:
                for u in M.edge_nbrs(t):
                    if u not in local and angle_deg(M.N[u], M.N[t]) < 40:
                        local.add(u)
                        nxt.append(u)
            layer = nxt
        S = [[sum(M.A[t] * M.N[t][i] * M.N[t][j] for t in local) for j in range(3)] for i in range(3)]
        addc(jacobi_min_eig(S))
        # (2) cross products of neighbouring normals
        frontier = [s] + [u for u in M.edge_nbrs(s) if angle_deg(M.N[u], n0) < 2]
        for f in frontier:
            for u in M.edge_nbrs(f):
                if 2 < angle_deg(M.N[u], M.N[f]) < 40:
                    addc(cross(M.N[f], M.N[u]))
        for a in cand:
            cyl = grow_cylinder(M, s, a)
            if cyl:
                cyls.append(cyl)
                done |= cyl['tris']
                break
    return cyls


def grow_cylinder(M, s, a):
    e1 = unit(cross(a, (0.0, 0.0, 1.0)))
    e2 = (0.0, 0.0, 1.0)

    def p2(v):
        p = M.V[v]
        return (dot(p, e1), p[2])

    def ok_normal(u):
        return abs(dot(M.N[u], a)) < 0.15

    # local patch: BFS depth <= 3
    local, layer = {s}, [s]
    for _ in range(3):
        nxt = []
        for t in layer:
            for u in M.edge_nbrs(t):
                if u not in local and ok_normal(u) and angle_deg(M.N[u], M.N[t]) < 40:
                    local.add(u)
                    nxt.append(u)
        layer = nxt
    pts = distinct2d([p2(v) for t in local for v in M.T[t]])
    if len(pts) < 4:
        return None
    f = fit_circle(pts)
    if not f or f[2] > 150 or f[3] > circ_tol(f[2]):
        return None
    cx, cy, r, _ = f

    def on_circ(u):
        tol = circ_tol(r)
        for v in M.T[u]:
            x, y = p2(v)
            if abs(math.hypot(x - cx, y - cy) - r) > tol:
                return False
        return True

    if not on_circ(s):
        return None
    patch, q = {s}, [s]
    while q:
        t = q.pop()
        for u in M.edge_nbrs(t):
            if u not in patch and ok_normal(u) and angle_deg(M.N[u], M.N[t]) < 40 and on_circ(u):
                patch.add(u)
                q.append(u)
    pts = distinct2d([p2(v) for t in patch for v in M.T[t]])
    if len(pts) < 5:
        return None
    f = fit_circle(pts)
    if not f or f[3] > circ_tol(f[2]):
        return None
    cx, cy, r, res = f
    conc = 0.0
    for t in patch:
        c = M.C[t]
        cc = (dot(c, e1), c[2])
        n2 = (dot(M.N[t], e1), M.N[t][2])
        conc += M.A[t] * (1 if (n2[0] * (cx - cc[0]) + n2[1] * (cy - cc[1])) > 0 else -1)
    tv = [dot(M.V[v], a) for t in patch for v in M.T[t]]
    # 3D point on axis
    p0 = (e1[0] * cx, e1[1] * cx, cy)
    return {'axis': a, 'p0': p0, 'r': r, 'res': res, 'concave': conc > 0,
            'cover': coverage_deg(pts, cx, cy), 't0': min(tv), 't1': max(tv),
            'tris': patch}


def coaxial(c1, c2):
    if abs(dot(c1['axis'], c2['axis'])) < 0.999:
        return False
    d = sub(c2['p0'], c1['p0'])
    return norm(cross(d, c1['axis'])) < 0.15


def near(d, target):
    return abs(d - target) <= D_TOL


def heatset_holes(holes):
    """Return set of hole indices that belong to a valid heat-set step stack."""
    hs = set()
    groups = []
    for i, h in enumerate(holes):
        for g in groups:
            if coaxial(holes[g[0]], h):
                g.append(i)
                break
        else:
            groups.append([i])
    for g in groups:
        rel = [i for i in g if any(near(2 * holes[i]['r'], d) for d in RELIEF_D)]
        lead = [i for i in g if near(2 * holes[i]['r'], LEAD_D)]
        bore = [i for i in g if near(2 * holes[i]['r'], BORE_D)]
        for li in lead:
            L = holes[li]
            ll = L['t1'] - L['t0']
            if abs(ll - LEAD_LEN) > LEAD_LEN_TOL:
                continue
            for ri in rel:
                R = holes[ri]
                rl = R['t1'] - R['t0']
                if abs(rl - RELIEF_LEN) > RELIEF_LEN_TOL:
                    continue
                side = None
                if abs(R['t1'] - L['t0']) < 0.15:
                    side = +1   # relief below t, bore must be beyond L.t1
                elif abs(R['t0'] - L['t1']) < 0.15:
                    side = -1
                if side is None:
                    continue
                for bi in bore:
                    B = holes[bi]
                    if (side > 0 and abs(B['t0'] - L['t1']) < 0.15) or (side < 0 and abs(B['t1'] - L['t0']) < 0.15):
                        hs |= {li, ri, bi}
    return hs


def boundary_loops(M, tris):
    tset = set(tris)
    bedges = []
    for t in tris:
        for e in M.tri_edges(t):
            if sum(1 for u in M.edge_tris[e] if u in tset) == 1:
                bedges.append(e)
    adj = defaultdict(list)
    for a, b in bedges:
        adj[a].append(b)
        adj[b].append(a)
    seen, loops = set(), []
    for a, b in bedges:
        if (a, b) in seen:
            continue
        loop, cur, prev = [a], b, a
        seen.add((a, b))
        guard = 0
        while cur != a and guard < 100000:
            guard += 1
            loop.append(cur)
            nxts = [x for x in adj[cur] if x != prev and (min(cur, x), max(cur, x)) not in seen]
            if not nxts:
                break
            nx = nxts[0]
            seen.add((min(cur, nx), max(cur, nx)))
            prev, cur = cur, nx
        loops.append(loop)
    return loops, bedges


def heatset_annulus(M, tris):
    loops, _ = boundary_loops(M, tris)
    if len(loops) != 2:
        return None
    circs = []
    for lp in loops:
        pts = distinct2d([(M.V[v][0], M.V[v][1]) for v in lp])
        if len(pts) < 6:
            return None
        f = fit_circle(pts)
        if not f or f[3] > circ_tol(f[2]):
            return None
        circs.append(f)
    if math.hypot(circs[0][0] - circs[1][0], circs[0][1] - circs[1][1]) > 0.1:
        return None
    d_out, d_in = sorted((2 * circs[0][2], 2 * circs[1][2]), reverse=True)
    if (any(near(d_out, d) for d in RELIEF_D) and near(d_in, LEAD_D)) or (near(d_out, LEAD_D) and near(d_in, BORE_D)):
        return (d_out, d_in)
    return None


def bridge_samples(M, tris, zc):
    """Classify sample points of a flat ceiling region: 'bridge' / 'long_bridge' / 'overhang'."""
    tset = set(tris)
    segs = []
    for t in tris:
        for e in M.tri_edges(t):
            inside = [u for u in M.edge_tris[e] if u in tset]
            if len(inside) != 1:
                continue
            anch = False
            for u in M.edge_tris[e]:
                if u in tset:
                    continue
                third = [v for v in M.T[u] if v not in e]
                if third and M.V[third[0]][2] < zc - 0.005:
                    anch = True
            a, b = M.V[e[0]], M.V[e[1]]
            segs.append((a[0], a[1], b[0], b[1], anch))
    area = sum(M.A[t] for t in tris)
    sp = max(0.3, math.sqrt(area / 3000.0))
    samples = []  # (x, y, w)
    claimed = set()
    for t in tris:
        p = [M.V[v] for v in M.T[t]]
        x0, x1 = min(q[0] for q in p), max(q[0] for q in p)
        y0, y1 = min(q[1] for q in p), max(q[1] for q in p)
        pts = []
        (ax, ay), (bx, by), (cx, cy) = (p[0][0], p[0][1]), (p[1][0], p[1][1]), (p[2][0], p[2][1])
        den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(den) > 1e-12:
            for i in range(math.ceil(x0 / sp), math.floor(x1 / sp) + 1):
                for j in range(math.ceil(y0 / sp), math.floor(y1 / sp) + 1):
                    x, y = i * sp, j * sp
                    l1 = ((by - cy) * (x - cx) + (cx - bx) * (y - cy)) / den
                    l2 = ((cy - ay) * (x - cx) + (ax - cx) * (y - cy)) / den
                    if l1 >= -1e-9 and l2 >= -1e-9 and 1 - l1 - l2 >= -1e-9 and (i, j) not in claimed:
                        claimed.add((i, j))
                        pts.append((x, y))
        if not pts:
            pts = [(M.C[t][0], M.C[t][1])]
        w = M.A[t] / len(pts)
        gx, gy = M.C[t][0], M.C[t][1]
        for x, y in pts:  # nudge 1e-4 mm toward the centroid so no sample sits on an edge
            dx, dy = gx - x, gy - y
            dl = math.hypot(dx, dy)
            if dl > 1e-9:
                x, y = x + dx / dl * 1e-4, y + dy / dl * 1e-4
            samples.append((x, y, w))
    codes = ['overhang'] * len(samples)
    best = [math.inf] * len(samples)
    bw = max(0.5, sp)
    # directions: uniform + perpendicular to every anchored boundary segment (exact wall-to-wall chords)
    dirs = {round(math.pi * k / N_DIRS, 4) for k in range(N_DIRS)}
    for (xa, ya, xb, yb, an) in segs:
        if an and math.hypot(xb - xa, yb - ya) > 1e-6:
            th = (math.atan2(yb - ya, xb - xa) + math.pi / 2) % math.pi
            dirs.add(round(th / 0.0044) * 0.0044)  # 0.25 deg bins
    for th in sorted(dirs):
        c, s = math.cos(th), math.sin(th)
        rs, bins = [], defaultdict(list)
        for (xa, ya, xb, yb, an) in segs:
            Xa, Ya = xa * c + ya * s, -xa * s + ya * c
            Xb, Yb = xb * c + yb * s, -xb * s + yb * c
            if abs(Ya - Yb) < 1e-12:
                continue
            idx = len(rs)
            rs.append((Xa, Ya, Xb, Yb, an))
            for bi in range(math.floor(min(Ya, Yb) / bw), math.floor(max(Ya, Yb) / bw) + 1):
                bins[bi].append(idx)
        for si, (x, y, w) in enumerate(samples):
            if codes[si] == 'bridge':
                continue
            PX, PY = x * c + y * s, -x * s + y * c
            dp, dn, ap, an_ = math.inf, math.inf, False, False
            for idx in bins.get(math.floor(PY / bw), ()):
                Xa, Ya, Xb, Yb, anc = rs[idx]
                if (Ya - PY) * (Yb - PY) > 0:
                    continue
                X = Xa + (PY - Ya) * (Xb - Xa) / (Yb - Ya)
                d = X - PX
                if d > 1e-9:
                    if d < dp:
                        dp, ap = d, anc
                elif d < -1e-9:
                    if -d < dn:
                        dn, an_ = -d, anc
            if ap and an_:
                L = dp + dn
                if L < best[si]:
                    best[si] = L
                if L <= BRIDGE_MM + 1e-3:
                    codes[si] = 'bridge'
                else:
                    codes[si] = 'long_bridge'
    return samples, codes, sp, best


def cluster_samples(samples, codes, sp):
    out = defaultdict(list)  # code -> list of clusters (list of sample idx)
    by_code = defaultdict(dict)
    for i, (x, y, w) in enumerate(samples):
        by_code[codes[i]].setdefault((round(x / sp), round(y / sp)), []).append(i)
    for code, cells in by_code.items():
        seen = set()
        for cell in cells:
            if cell in seen:
                continue
            comp, st = [], [cell]
            seen.add(cell)
            while st:
                c = st.pop()
                comp.extend(cells[c])
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        n = (c[0] + dx, c[1] + dy)
                        if n in cells and n not in seen:
                            seen.add(n)
                            st.append(n)
            out[code].append(comp)
    return out


def tri_components(M, tris, by_vertex=True):
    tset, seen, comps = set(tris), set(), []
    for t in tris:
        if t in seen:
            continue
        comp, st = [], [t]
        seen.add(t)
        while st:
            u = st.pop()
            comp.append(u)
            nb = (w for v in M.T[u] for w in M.vert_tris[v]) if by_vertex else M.edge_nbrs(u)
            for w in nb:
                if w in tset and w not in seen:
                    seen.add(w)
                    st.append(w)
        comps.append(comp)
    return comps


def r3(v, n=2):
    return [round(x, n) for x in v]


def region_from_tris(M, comp, code, extra=None):
    area = sum(M.A[t] for t in comp)
    cx = sum(M.C[t][0] * M.A[t] for t in comp) / area
    cy = sum(M.C[t][1] * M.A[t] for t in comp) / area
    cz = sum(M.C[t][2] * M.A[t] for t in comp) / area
    vs = [M.V[v] for t in comp for v in M.T[t]]
    reg = {'code': code, 'at': r3((cx, cy, cz)),
           'box': [r3((min(p[0] for p in vs), min(p[1] for p in vs), min(p[2] for p in vs))),
                   r3((max(p[0] for p in vs), max(p[1] for p in vs), max(p[2] for p in vs)))],
           'area_mm2': round(area, 3), 'min_slope_deg': round(min(M.slope[t] for t in comp), 2),
           'n_tris': len(comp)}
    if extra:
        reg.update(extra)
    return reg


def analyze(V, T):
    M = Mesh(V, T)
    nt = len(M.T)
    ovh = [t for t in range(nt) if M.N[t][2] < 0 and M.slope[t] < OVH_DEG and not M.on_bed(t)]
    ovh_set = set(ovh)
    label = {}
    extra = {}
    # 1. horizontal cylinders
    cyls = find_cylinders(M, ovh)
    holes = [c for c in cyls if c['concave'] and c['cover'] >= 180.0]
    hs = heatset_holes(holes)
    hole_groups = []
    for i, h in enumerate(holes):
        code = 'heatset_ceiling' if i in hs else 'round_hole'
        mine = [t for t in h['tris'] if t in ovh_set and t not in label]
        for t in mine:
            label[t] = code
        if mine:
            hole_groups.append((code, mine, {'d': round(2 * h['r'], 3), 'axis': r3(h['axis'], 3)}))
    regions, small = [], 0

    def add(reg):
        nonlocal small
        if reg['area_mm2'] >= MIN_AREA:
            regions.append(reg)
        else:
            small += 1

    for code, mine, ex in hole_groups:
        for comp in tri_components(M, mine):
            add(region_from_tris(M, comp, code, ex))
    # 2. flat ceilings
    flat = [t for t in ovh if t not in label and M.slope[t] < FLAT_DEG]
    fset = set(flat)
    seen = set()
    bridged_area = 0.0
    for t in flat:
        if t in seen:
            continue
        zc = M.C[t][2]
        comp, st = [], [t]
        seen.add(t)
        while st:
            u = st.pop()
            comp.append(u)
            for w in M.edge_nbrs(u):
                if w in fset and w not in seen and abs(M.C[w][2] - zc) < 0.05:
                    seen.add(w)
                    st.append(w)
        ann = heatset_annulus(M, comp)
        if ann:
            for u in comp:
                label[u] = 'heatset_ceiling'
            add(region_from_tris(M, comp, 'heatset_ceiling', {'d': round(ann[1], 3), 'd_outer': round(ann[0], 3)}))
            continue
        samples, codes, sp, best = bridge_samples(M, comp, zc)
        for u in comp:
            label[u] = 'flat'
        min_slope = min(M.slope[u] for u in comp)
        for code, clusters in cluster_samples(samples, codes, sp).items():
            for cl in clusters:
                area = sum(samples[i][2] for i in cl)
                if code == 'bridge':
                    bridged_area += area
                xs = [samples[i][0] for i in cl]; ys = [samples[i][1] for i in cl]
                reg = {'code': code,
                       'at': r3((sum(samples[i][0] * samples[i][2] for i in cl) / area,
                                 sum(samples[i][1] * samples[i][2] for i in cl) / area, zc)),
                       'box': [r3((min(xs), min(ys), zc)), r3((max(xs), max(ys), zc))],
                       'area_mm2': round(area, 3), 'min_slope_deg': round(min_slope, 2),
                       'n_samples': len(cl)}
                if code == 'long_bridge':
                    # shortest anchored chord at each point; the worst point of the region needs max_chord_mm
                    reg['min_chord_mm'] = round(min(best[i] for i in cl), 2)
                    reg['max_chord_mm'] = round(max(best[i] for i in cl), 2)
                if code == 'bridge':
                    reg['max_chord_mm'] = round(max(best[i] for i in cl), 2)
                add(reg)
    # 3. other overhangs
    rest = [t for t in ovh if t not in label]
    for comp in tri_components(M, rest):
        reg = region_from_tris(M, comp, 'overhang')
        reg['near_45'] = reg['min_slope_deg'] >= 43.5  # every triangle within 1.5 deg of the limit
        add(reg)
    # 4. shallow ridges
    ridge_edges = []
    for e, ts in M.edge_tris.items():
        if len(ts) != 2:
            continue
        a, b = ts
        na, nb = M.N[a], M.N[b]
        if na[2] >= -0.05 or nb[2] >= -0.05:
            continue
        if angle_deg(na, nb) < 1.0:
            continue
        pa, pb = M.V[e[0]], M.V[e[1]]
        if pa[2] <= BED_MM and pb[2] <= BED_MM:
            continue
        opp = [v for v in M.T[b] if v not in e][0]
        if dot(sub(M.V[opp], pa), na) >= -1e-7:
            continue  # concave or flat
        d = sub(pb, pa)
        L = norm(d)
        rise = math.degrees(math.asin(min(1.0, abs(d[2]) / L)))
        if rise < RIDGE_DEG:
            ridge_edges.append((e, L, rise, min(M.slope[a], M.slope[b]), a in ovh_set or b in ovh_set))
    if ridge_edges:
        vadj = defaultdict(list)
        for i, (e, *_r) in enumerate(ridge_edges):
            vadj[e[0]].append(i)
            vadj[e[1]].append(i)
        seen = set()
        for i in range(len(ridge_edges)):
            if i in seen:
                continue
            comp, st = [], [i]
            seen.add(i)
            while st:
                k = st.pop()
                comp.append(k)
                for v in ridge_edges[k][0]:
                    for j in vadj[v]:
                        if j not in seen:
                            seen.add(j)
                            st.append(j)
            L = sum(ridge_edges[k][1] for k in comp)
            if L < MIN_RIDGE_LEN:
                small += 1
                continue
            pts = [M.V[v] for k in comp for v in ridge_edges[k][0]]
            mid = [sum((M.V[ridge_edges[k][0][0]][j] + M.V[ridge_edges[k][0][1]][j]) / 2 * ridge_edges[k][1]
                       for k in comp) / L for j in range(3)]
            regions.append({'code': 'shallow_ridge', 'at': r3(mid),
                            'box': [r3([min(p[j] for p in pts) for j in range(3)]),
                                    r3([max(p[j] for p in pts) for j in range(3)])],
                            'length_mm': round(L, 3),
                            'min_slope_deg': round(min(ridge_edges[k][3] for k in comp), 2),
                            'max_rise_deg': round(max(ridge_edges[k][2] for k in comp), 2),
                            'min_rise_deg': round(min(ridge_edges[k][2] for k in comp), 2),
                            'near_45': min(ridge_edges[k][2] for k in comp) >= 43.5,
                            'on_overhang_faces': any(ridge_edges[k][4] for k in comp),
                            'n_edges': len(comp)})
    regions.sort(key=lambda r: (r['code'], -r.get('area_mm2', r.get('length_mm', 0))))
    open_edges = sum(1 for ts in M.edge_tris.values() if len(ts) == 1)
    return {'bbox_mm': r3(sub(M.maxs, M.mins), 3), 'volume_mm3': round(M.volume, 1),
            'regions': regions, 'small_regions': small,
            'bridged_area_mm2': round(bridged_area, 2),
            'n_tris': nt, 'open_edges': open_edges,
            'n_cylinders': len(cyls), 'n_holes': len(holes)}


# ----------------------------------------------------------------- driver
def summarize(res, detail=False):
    cnt, forb, near = defaultdict(int), 0.0, 0.0
    for r in res['regions']:
        cnt[r['code']] += 1
        if r['code'] in FORBIDDEN and 'area_mm2' in r:
            forb += r['area_mm2']
            if r.get('near_45'):
                near += r['area_mm2']
    if detail:
        return dict(cnt), round(forb, 2), round(near, 2)
    return dict(cnt), round(forb, 2)


def main(argv):
    parts = json.load(open(os.path.join(REPO, 'fusion/parts.json')))['parts']
    want = argv or list(parts)
    cache_dir = os.path.join(HERE, 'cache')
    os.makedirs(cache_dir, exist_ok=True)
    projects = {}
    for name in want:
        ent = parts[name]['3mf'][0]
        f = os.path.join(REPO, ent['file'])
        if f not in projects:
            projects[f] = Project(f)
        P = projects[f]
        t0 = time.time()
        oids = P.find(ent['object'])
        if not oids:
            res = {'object': ent['object'], 'file': ent['file'], 'error': 'object not found'}
        else:
            V, T, zrot, skipped, printable = P.object_mesh(oids[0])
            res = {'object': ent['object'], 'file': ent['file'], 'z_rot_deg': zrot,
                   'face': ent.get('face'), 'printable': printable, 'skipped_parts': skipped,
                   'duplicates_by_name': len(oids)}
            res.update(analyze(V, T))
        res['seconds'] = round(time.time() - t0, 1)
        json.dump(res, open(os.path.join(cache_dir, name + '.json'), 'w'), indent=1)
        c, fa = summarize(res) if 'regions' in res else ({}, 0)
        print(f'{name}: {c} forbidden {fa} mm2 ({res["seconds"]} s)', flush=True)
    out = {}
    for name in parts:
        p = os.path.join(cache_dir, name + '.json')
        if os.path.exists(p):
            out[name] = json.load(open(p))
    json.dump(out, open(os.path.join(HERE, 'audit.json'), 'w'), indent=1)


if __name__ == '__main__':
    main(sys.argv[1:])
