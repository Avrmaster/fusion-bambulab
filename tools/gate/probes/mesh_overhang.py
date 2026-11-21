"""Host-side geometry pre-check of a mesh in its bed frame (z up, the bed under the lowest vertex): what hangs. It warns
early, before anything is sliced; the slicer stays the judge of the support it makes (print.slice_support_growth).

    measure(V, F, max_angle, max_bridge)   -> {"faces": [...], "ridges": [...], "bridges": [...], "bed_z", "triangles"}

faces    connected regions (triangles that share an edge) of down-facing triangles flatter than max_angle, measured from
         vertical: a triangle whose normal n has -n_z > sin(max_angle). The bottom face on the bed is left out (every
         vertex within BED_EPS of the lowest one). A region: {"area" (mm2, the real area), "flattest" (deg from vertical),
         "z" [lowest, highest] above the bed, "at" [x, y, z] of its largest triangle relative to the low corner of the
         mesh, "triangles"}.
ridges   edges that hang although the faces beside them do not: an edge shared by two triangles that are both steep
         enough, that rises into both of them (it is the lowest line there: a keel, the edge where two slopes meet under
         a part), and whose own direction is flatter than max_angle from vertical. Chains of such edges are one ridge:
         {"length", "flattest" (deg from vertical of its flattest edge), "z", "at"}. A cradle whose two 45 deg faces met
         in a ridge of about 11 deg got a support tree, and only the slicer showed it.
bridges  regions of faces that face straight down (within FLAT_DEG of horizontal) above the bed, wider than max_bridge
         across: "span" is the least width of the region's outline (its convex hull, over directions every 3 deg), the
         longest a bridge over it must reach when it rests on two opposite sides. {"span", "area", "z", "at"}.

Positions are given so the owner can find them by eye in the slicer: "z" above the bed, "at" from the corner of the
part's bounding box nearest the plate origin. Every row also has "box" [x0, y0, x1, y1], its extent in X-Y from that
corner, and "all" lists every region of each kind as [x0, y0, x1, y1, lowest z, width] (not only the MAX_LIST
largest; "width" = 2 x area / perimeter of a face region, None for a ridge):
print.overhang_angle holds each one against the support cells of the slice (probes.project_slice)."""
import math

BED_EPS = 0.01            # mm: a vertex this close to the lowest one lies on the bed
FLAT_DEG = 5.0            # a face within this of horizontal faces straight down: the slicer bridges it
MAX_LIST = 12             # rows kept per kind, largest first; the totals count every one
ALL_MAX = 4000            # regions kept per kind in "all" ([x0, y0, x1, y1, lowest z]): where each one is, to hold against the slice
ANGLE_EPS = 0.01          # deg: float noise of a face or edge modelled exactly at the limit (a 45 deg chamfer is at it, not over it)


def _sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
def _dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _regions(members, edges_of):
    """Connected groups of the triangle indexes in `members` (sharing an edge)."""
    left, out = set(members), []
    while left:
        seed = left.pop(); group = [seed]; stack = [seed]
        while stack:
            t = stack.pop()
            for n in edges_of(t):
                if n in left:
                    left.discard(n); group.append(n); stack.append(n)
        out.append(group)
    return out


def _hull_width(points):
    """The least width of the convex hull of 2-D points, over directions every 3 deg (an upper bound of the true least
    width within 0.2 %). 0 for fewer than two points."""
    pts = sorted(set((round(p[0], 4), round(p[1], 4)) for p in points))
    if len(pts) < 2:
        return 0.0

    def half(seq):
        h = []
        for p in seq:
            while len(h) >= 2 and (h[-1][0] - h[-2][0]) * (p[1] - h[-2][1]) - (h[-1][1] - h[-2][1]) * (p[0] - h[-2][0]) <= 0:
                h.pop()
            h.append(p)
        return h
    hull = half(pts)[:-1] + half(reversed(pts))[:-1]
    best = None
    for k in range(60):
        a = math.radians(3 * k); c, s = math.cos(a), math.sin(a)
        proj = [x * c + y * s for x, y in hull]
        w = max(proj) - min(proj)
        best = w if best is None else min(best, w)
    return best


def measure(V, F, max_angle=45.0, max_bridge=6.0):
    """See the top of the file. V: [(x, y, z)] in the bed frame, F: [(a, b, c)] with outward winding."""
    if not V or not F:
        return {'faces': [], 'ridges': [], 'bridges': [], 'bed_z': None, 'triangles': 0, 'totals': {}}
    lo = [min(v[k] for v in V) for k in range(3)]
    bed = lo[2]
    sin_lim = math.sin(math.radians(max_angle + ANGLE_EPS)); cos_flat = math.cos(math.radians(FLAT_DEG))
    cos_lim = math.cos(math.radians(max_angle + ANGLE_EPS))
    normals, areas, on_bed = [], [], []
    for a, b, c in F:
        n = _cross(_sub(V[b], V[a]), _sub(V[c], V[a]))
        ln = math.sqrt(_dot(n, n))
        normals.append((n[0] / ln, n[1] / ln, n[2] / ln) if ln > 1e-12 else (0.0, 0.0, 0.0))
        areas.append(ln / 2.0)
        on_bed.append(V[a][2] - bed <= BED_EPS and V[b][2] - bed <= BED_EPS and V[c][2] - bed <= BED_EPS)
    edge_tris = {}
    for i, (a, b, c) in enumerate(F):
        for u, w in ((a, b), (b, c), (c, a)):
            edge_tris.setdefault((u, w) if u < w else (w, u), []).append(i)
    neigh = {}

    def edges_of(t):
        if t not in neigh:
            a, b, c = F[t]
            out = []
            for u, w in ((a, b), (b, c), (c, a)):
                out += [x for x in edge_tris.get((u, w) if u < w else (w, u), ()) if x != t]
            neigh[t] = out
        return neigh[t]

    def where(i):
        a, b, c = F[i]
        p = ((V[a][0] + V[b][0] + V[c][0]) / 3.0, (V[a][1] + V[b][1] + V[c][1]) / 3.0, (V[a][2] + V[b][2] + V[c][2]) / 3.0)
        return [round(p[0] - lo[0], 2), round(p[1] - lo[1], 2), round(p[2] - bed, 2)]

    def zs(tris):
        z = [V[k][2] - bed for t in tris for k in F[t]]
        return [round(min(z), 2), round(max(z), 2)]

    def width(tris):
        """2 x area / perimeter of a region (its boundary: the edges it uses once): the width of a strip, half the side of
        a square. A region narrower than a line prints from the layer below without support."""
        seen = {}
        for t in tris:
            a, b, c = F[t]
            for u, w in ((a, b), (b, c), (c, a)):
                k = (u, w) if u < w else (w, u)
                seen[k] = seen.get(k, 0) + 1
        per = sum(math.sqrt(sum((V[u][i] - V[w][i]) ** 2 for i in range(3))) for (u, w), n in seen.items() if n == 1)
        return round(2.0 * sum(areas[t] for t in tris) / per, 3) if per > 1e-9 else 0.0

    def box(verts):
        xs, ys = [V[k][0] - lo[0] for k in verts], [V[k][1] - lo[1] for k in verts]
        return [round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)]

    flat = [i for i, n in enumerate(normals) if -n[2] > sin_lim and not on_bed[i]]
    faces = []
    for group in _regions(flat, edges_of):
        biggest = max(group, key=lambda t: areas[t])
        steep = min(-normals[t][2] for t in group)
        flattest = max(math.degrees(math.asin(min(1.0, -normals[t][2]))) for t in group)
        faces.append({'area': round(sum(areas[t] for t in group), 2), 'flattest': round(flattest, 1), 'z': zs(group), 'at': where(biggest),
                      'triangles': len(group), 'steepest': round(math.degrees(math.asin(min(1.0, steep))), 1), 'box': box([k for t in group for k in F[t]]),
                      'width': width(group)})
    bridges = []
    level = [i for i in flat if -normals[i][2] > cos_flat]
    for group in _regions(level, lambda t: [x for x in edges_of(t) if -normals[x][2] > cos_flat]):
        pts = [(V[k][0], V[k][1]) for t in group for k in F[t]]
        span = _hull_width(pts)
        if span > max_bridge:
            bridges.append({'span': round(span, 2), 'area': round(sum(areas[t] for t in group), 2), 'z': zs(group), 'at': where(max(group, key=lambda t: areas[t])),
                            'box': box([k for t in group for k in F[t]]), 'width': width(group)})
    hanging = []
    for (u, w), tris in edge_tris.items():
        if len(tris) != 2:
            continue
        t1, t2 = tris
        n1, n2 = normals[t1], normals[t2]
        if not (n1[2] < 0 and n2[2] < 0) or -n1[2] > sin_lim or -n2[2] > sin_lim:
            continue                                     # not both down-facing, or a face is flat already (a face region holds it)
        if V[u][2] - bed <= BED_EPS and V[w][2] - bed <= BED_EPS:
            continue
        d = _sub(V[w], V[u]); ld = math.sqrt(_dot(d, d))
        if ld < 1e-9 or abs(d[2]) / ld >= cos_lim:
            continue                                     # the edge itself is steep enough
        rises = True
        for t in tris:
            other = next(k for k in F[t] if k != u and k != w)
            p = _sub(V[other], V[u]); along = _dot(p, d) / (ld * ld)
            perp = (p[0] - along * d[0], p[1] - along * d[1], p[2] - along * d[2])
            if perp[2] <= 1e-9:
                rises = False; break
        if rises:
            hanging.append(((u, w), ld, math.degrees(math.acos(min(1.0, abs(d[2]) / ld)))))
    by_vertex = {}
    for k, (e, _l, _a) in enumerate(hanging):
        for v in e:
            by_vertex.setdefault(v, []).append(k)
    ridges, seen = [], set()
    for k in range(len(hanging)):
        if k in seen:
            continue
        chain, stack = [], [k]; seen.add(k)
        while stack:
            j = stack.pop(); chain.append(j)
            for v in hanging[j][0]:
                for m in by_vertex[v]:
                    if m not in seen:
                        seen.add(m); stack.append(m)
        verts = [v for j in chain for v in hanging[j][0]]
        z = [V[v][2] - bed for v in verts]
        mid = hanging[max(chain, key=lambda j: hanging[j][1])][0]
        p = [(V[mid[0]][i] + V[mid[1]][i]) / 2.0 for i in range(3)]
        ridges.append({'length': round(sum(hanging[j][1] for j in chain), 2), 'flattest': round(max(hanging[j][2] for j in chain), 1),
                       'z': [round(min(z), 2), round(max(z), 2)], 'at': [round(p[0] - lo[0], 2), round(p[1] - lo[1], 2), round(p[2] - bed, 2)], 'box': box(verts)})
    faces.sort(key=lambda r: -r['area']); bridges.sort(key=lambda r: -r['span']); ridges.sort(key=lambda r: -r['length'])
    totals = {'faces': len(faces), 'face_area': round(sum(r['area'] for r in faces), 2), 'ridges': len(ridges),
              'ridge_length': round(sum(r['length'] for r in ridges), 2), 'bridges': len(bridges)}
    every = {k: [r['box'] + [r['z'][0], r.get('width')] for r in rows][:ALL_MAX] for k, rows in (('faces', faces), ('ridges', ridges), ('bridges', bridges))}
    return {'faces': faces[:MAX_LIST], 'ridges': ridges[:MAX_LIST], 'bridges': bridges[:MAX_LIST], 'bed_z': round(bed, 4),
            'triangles': len(F), 'totals': totals, 'max_angle': max_angle, 'max_bridge': max_bridge, 'all': every}
