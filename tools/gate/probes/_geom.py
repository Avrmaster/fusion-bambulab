"""Pure geometry helpers on tuples: vectors, rigid transforms, the B-rep signature, hole stacks and their classification.

This file is used two ways and must work both ways:
  - on the host it is imported (probes._geom) by tests and judges;
  - gate_fusion_rpc.build_script() PASTES its text into every Fusion script, in front of _geom_rings.py, _fusion.py and
    the probe, so inside Fusion every function here is a global.
So: standard library only (math, hashlib, json), no import of another file of this folder, no adsk, no code that runs at
import except definitions. Everything is in mm; points and vectors are tuples (x, y, z)."""
import hashlib, json, math


# ---- vectors ---------------------------------------------------------------------------------------------------------------------

def v_add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def v_sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def v_scale(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def v_dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def v_cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def v_len(a):
    return math.sqrt(v_dot(a, a))


def v_dist(a, b):
    return v_len(v_sub(a, b))


def v_unit(a):
    """The vector scaled to length 1; (0, 0, 0) stays (0, 0, 0)."""
    n = v_len(a)
    return (0.0, 0.0, 0.0) if n < 1e-12 else (a[0] / n, a[1] / n, a[2] / n)


def axis_frame(a):
    """(u, v): two unit vectors square to the unit vector `a` and to each other, so that (u, v, a) is right-handed."""
    t = (1.0, 0.0, 0.0) if abs(a[0]) < 0.9 else (0.0, 1.0, 0.0)
    u = v_unit(v_cross(a, t))
    return u, v_cross(a, u)


def along(p, origin, axis):
    """Position of the point `p` along the line through `origin` with the unit direction `axis`."""
    return v_dot(v_sub(p, origin), axis)


def off_axis(p, origin, axis):
    """Distance of the point `p` from the line through `origin` with the unit direction `axis`."""
    d = v_sub(p, origin)
    s = v_dot(d, axis)
    return math.sqrt(max(0.0, v_dot(d, d) - s * s))


def canonical_axis(a, digits=6):
    """The unit axis or its opposite, whichever has its first non-zero component positive: one name for a line's direction."""
    a = v_unit(a)
    for c in a:
        if abs(c) > 1e-9:
            if c < 0:
                a = (-a[0], -a[1], -a[2])
            break
    return tuple(0.0 if round(c, digits) == 0 else round(c, digits) for c in a)


# ---- rigid transforms: a matrix is 16 floats, row-major, translation in mm at [3], [7], [11] ----------------------------------------

IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)


def mat_point(m, p):
    """The point `p` moved by the matrix `m`."""
    return (m[0] * p[0] + m[1] * p[1] + m[2] * p[2] + m[3],
            m[4] * p[0] + m[5] * p[1] + m[6] * p[2] + m[7],
            m[8] * p[0] + m[9] * p[1] + m[10] * p[2] + m[11])


def mat_vector(m, v):
    """The direction `v` turned by the matrix `m` (no translation)."""
    return (m[0] * v[0] + m[1] * v[1] + m[2] * v[2],
            m[4] * v[0] + m[5] * v[1] + m[6] * v[2],
            m[8] * v[0] + m[9] * v[1] + m[10] * v[2])


def mat_inverse(m):
    """The inverse of a rigid transform (rotation + translation): part frame -> document frame becomes the way back."""
    r = (m[0], m[4], m[8], m[1], m[5], m[9], m[2], m[6], m[10])            # the transposed rotation
    t = (m[3], m[7], m[11])
    it = (-(r[0] * t[0] + r[1] * t[1] + r[2] * t[2]), -(r[3] * t[0] + r[4] * t[1] + r[5] * t[2]), -(r[6] * t[0] + r[7] * t[1] + r[8] * t[2]))
    return (r[0], r[1], r[2], it[0], r[3], r[4], r[5], it[1], r[6], r[7], r[8], it[2], 0.0, 0.0, 0.0, 1.0)


def pose_hash(m):
    """16 hex characters over a matrix rounded to 1e-4 (rotation) and 0.001 mm (translation): the pose of an occurrence."""
    vals = [round(float(m[i]), 3 if i in (3, 7, 11) else 4) for i in range(12)]
    vals = [0.0 if v == 0 else v for v in vals]
    return hashlib.sha256(json.dumps(vals).encode()).hexdigest()[:16]


# ---- B-rep signature ----------------------------------------------------------------------------------------------------------------

SIG_MOD = 1 << 256


def face_digest(area, centroid, kind=None):
    """One face as a whole number below 2**256: sha256 over its area (rounded to 0.01 mm2) and its centroid (0.01 mm).
    kind: the surface type as an int when the caller has it; None leaves it out. The numbers of all faces of a body are
    ADDED (faces_sum), so the sum does not depend on the order of the faces and can be built in several calls."""
    def r(x):
        v = round(float(x), 2)
        return 0.0 if v == 0 else v
    row = [r(area), r(centroid[0]), r(centroid[1]), r(centroid[2])]
    if kind is not None:
        row.insert(0, int(kind))
    return int(hashlib.sha256(json.dumps(row, separators=(',', ':')).encode()).hexdigest(), 16)


def faces_sum(faces, start=0):
    """`start` plus the face_digest of every face, modulo 2**256.
    faces  one (area mm2, (x, y, z) centroid mm) or (surface type, area, centroid) per face, any order"""
    total = int(start)
    for f in faces:
        total = (total + (face_digest(f[1], f[2], f[0]) if len(f) == 3 else face_digest(f[0], f[1]))) % SIG_MOD
    return total


def brep_signature_of(volume, area, counts, bbox, n_faces, total):
    """The signature from the body's own numbers and the finished faces_sum of its `n_faces` faces: sha256, 64 hex.
    Rounding: volume 0.01 mm3, area 0.01 mm2, bbox 0.001 mm (face area and centroid: face_digest)."""
    def r(x, d):
        v = round(float(x), d)
        return 0.0 if v == 0 else v
    data = [r(volume, 2), r(area, 2), [int(c) for c in counts], [[r(c, 3) for c in bbox[0]], [r(c, 3) for c in bbox[1]]],
            int(n_faces), '%064x' % (int(total) % SIG_MOD)]
    return hashlib.sha256(json.dumps(data, separators=(',', ':')).encode()).hexdigest()


def brep_signature(volume, area, counts, bbox, faces):
    """sha256 (64 hex) that changes when the shape of a body changes, also when a hole only moves.

    volume  mm3            area  mm2            counts  (faces, edges, vertices)
    bbox    ((x, y, z) min, (x, y, z) max) in the component's own frame, mm
    faces   one (face area mm2, (x, y, z) centroid mm) per face, any order; a row may start with the surface type as an
            int, which is then part of the signature too

    A hole that only moves keeps the volume, the area and the counts of the body: it is the centroid of its wall face
    that moves, so every face goes in. The faces go in as a sum (faces_sum): a body with thousands of faces can be
    signed over several calls (gate_sign_step of _fusion.py) and the result is the same.
    Rounding: volume 0.01 mm3, area 0.01 mm2, bbox 0.001 mm, face area 0.01 mm2, centroid 0.01 mm."""
    faces = list(faces)
    return brep_signature_of(volume, area, counts, bbox, len(faces), faces_sum(faces))


def cheap_signature(volume, area, counts, bbox):
    """16 hex characters over volume, area, counts and bounding box: the signature of a body that is not a printed part
    (a bought part, hardware, a mesh ref). It is cheap (no loop over the faces) and does not see a hole that only moves.

    volume mm3, area mm2, counts (faces, edges, vertices) or any three integers, bbox ((x, y, z) min, (x, y, z) max) mm."""
    def r(x, d):
        v = round(float(x), d)
        return 0.0 if v == 0 else v
    data = [r(volume, 1), r(area, 1), [int(c) for c in counts], [[r(c, 2) for c in bbox[0]], [r(c, 2) for c in bbox[1]]]]
    return hashlib.sha256(json.dumps(data, separators=(',', ':')).encode()).hexdigest()[:16]


# ---- more transforms and boxes ------------------------------------------------------------------------------------------------------

def mat_mul(a, b):
    """The matrix that does `b` first and `a` after it: mat_point(mat_mul(a, b), p) == mat_point(a, mat_point(b, p))."""
    return tuple(sum(a[4 * i + k] * b[4 * k + j] for k in range(4)) for i in range(4) for j in range(4))


def box_transform(m, lo, hi):
    """The axis-aligned box round the box (lo, hi) after the matrix `m`: ((x, y, z) min, (x, y, z) max). For a turned
    body it is larger than the body's own box, never smaller."""
    pts = [mat_point(m, (x, y, z)) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    return (tuple(min(p[i] for p in pts) for i in range(3)), tuple(max(p[i] for p in pts) for i in range(3)))


def boxes_near(a_lo, a_hi, b_lo, b_hi, margin=0.0):
    """Do two axis-aligned boxes come within `margin` mm of each other on every axis?"""
    return all(a_lo[i] - margin <= b_hi[i] and b_lo[i] - margin <= a_hi[i] for i in range(3))


def box_has(lo, hi, p, margin=0.0):
    """Is the point `p` inside the box (lo, hi) grown by `margin`?"""
    return all(lo[i] - margin <= p[i] <= hi[i] + margin for i in range(3))


# ---- names and anchors as the host writes them ---------------------------------------------------------------------------------------

def part_slug(name):
    """'Motor plate (left)' -> 'motor_plate_left': the same text gate_common.slug() gives (tests compare the two)."""
    out, gap = [], False
    for ch in str(name).lower():
        if ('a' <= ch <= 'z') or ('0' <= ch <= '9'):
            if gap and out:
                out.append('_')
            out.append(ch)
            gap = False
        else:
            gap = True
    return ''.join(out) or 'part'


def part_name(component, body, printed_bodies):
    """The name a printed body has in the census: the component name, or "<component> / <body>" when the component holds
    more than one printed solid body."""
    return component if printed_bodies <= 1 else '%s / %s' % (component, body)


def anchor_of(kind, axis, point):
    """{"kind", "axis", "point"} exactly as gate_features.anchor() writes it: the axis as a unit vector to 4 decimals,
    the point rounded to 0.05 mm (tests compare the two)."""
    def clean(v):
        return 0.0 if v == 0 else v
    a = v_unit(axis)
    return {'kind': kind, 'axis': [clean(round(c, 4)) for c in a],
            'point': [clean(round(round(float(c) / 0.05) * 0.05, 6)) for c in point]}


# ---- hole stacks and their classification (census) --------------------------------------------------------------------------------

def _geo_r4(x):
    v = round(float(x), 4)
    return 0.0 if v == 0 else v


def cylinder_extent(origin, axis, r, points, circles=(), cone=False):
    """Extent of one cylindrical or conical face along its axis, from points on its boundary.

    origin, axis  the surface's own axis line (mm, unit vector)        r  the cylinder radius (ignored for a cone)
    points        boundary points (vertices, points along curved edges), mm
    circles       [(centre, radius)] of circular edges; only circles centred on the axis are used
    Returns {"s0", "s1", "r", "r2"} (r2 None for a cylinder; for a cone r is the radius at s0 and r2 at s1), or None when
    the boundary gives no length."""
    sr = [(along(p, origin, axis), off_axis(p, origin, axis)) for p in points]
    sr += [(along(c, origin, axis), float(cr)) for c, cr in circles if off_axis(c, origin, axis) < 0.02]
    if len(sr) < 2:
        return None
    lo, hi = min(sr), max(sr)
    if hi[0] - lo[0] < 0.02:
        return None
    if cone:
        return {'s0': lo[0], 's1': hi[0], 'r': lo[1], 'r2': hi[1]}
    return {'s0': lo[0], 's1': hi[0], 'r': float(r), 'r2': None}


def _geo_chains(spans, tol_r, tol_s):
    """spans on one axis line -> lists of spans that follow each other end to end"""
    spans = [dict(s) for s in sorted(spans, key=lambda x: (x['s0'], x['s1'], x['r']))]
    changed = True
    while changed:                                      # one cylinder cut into several faces (two halves, a seam) is one span
        changed = False
        for i in range(len(spans)):
            for j in range(i + 1, len(spans)):
                a, b = spans[i], spans[j]
                if a['r2'] is None and b['r2'] is None:
                    same = abs(a['r'] - b['r']) <= tol_r and b['s0'] <= a['s1'] + tol_s and a['s0'] <= b['s1'] + tol_s
                elif a['r2'] is not None and b['r2'] is not None:
                    same = (abs(a['r'] - b['r']) <= tol_r and abs(a['r2'] - b['r2']) <= tol_r
                            and abs(a['s0'] - b['s0']) <= tol_s and abs(a['s1'] - b['s1']) <= tol_s)
                else:
                    same = False
                if same:
                    a['s0'], a['s1'], a['arc'] = min(a['s0'], b['s0']), max(a['s1'], b['s1']), min(a['arc'], b['arc'])
                    del spans[j]
                    changed = True
                    break
            if changed:
                break
    spans.sort(key=lambda x: (x['s0'], x['r']))
    used, chains = [False] * len(spans), []
    for i in range(len(spans)):
        if used[i]:
            continue
        chain = [spans[i]]
        used[i] = True
        while True:
            last = chain[-1]
            end_r = last['r'] if last['r2'] is None else last['r2']
            cand = [j for j in range(len(spans)) if not used[j] and abs(spans[j]['s0'] - last['s1']) <= tol_s]
            if not cand:
                break
            j = min(cand, key=lambda k: abs(spans[k]['r'] - end_r))
            chain.append(spans[j])
            used[j] = True
        chains.append(chain)
    return chains


def stack_holes(cylinders, tol_r=0.02, tol_axis=0.02, tol_s=0.05):
    """Group the cylindrical and conical faces of one body into coaxial stacks.

    cylinders  [{"origin": (x, y, z) a point on the axis, "axis": unit (x, y, z), "r": radius mm, "r2": radius at the far
                 end for a cone or None, "s0": float, "s1": float (extent along the axis from `origin`, s0 < s1),
                 "full": bool (a full circle; a partial arc such as a fillet is not a hole), "inner": bool (True: the
                 material is outside the cylinder, the face is the wall of a hole), "arc": share of the circle that is
                 wall, 0..1 (optional, default 1.0)}]
    tol_r      mm: radii this close on one axis are one segment
    tol_axis   mm: axes this close (distance between the lines, directions parallel) are one axis
    tol_s      mm: spans whose ends are this close along the axis follow each other

    Returns [{"origin": point on the axis, "axis": canonical_axis, "spans": [{"s0", "s1", "r", "r2"}] sorted by s0 and
    measured along "axis" from "origin", "lo": least s0, "hi": largest s1}], only from faces with full and inner True,
    sorted by (axis, origin) so the order is stable between runs.
    "origin" is the point of the axis line nearest to (0, 0, 0), so it does not depend on which face came first.
    Spans on one line that do not touch (a second hole from the other face, a hole cut in two by a slot) are separate
    stacks; a wider cylinder that only overlaps another (the outer wall of a ring groove round a hole) is one too.
    Every span also carries "arc": the least share of the circle that is wall (under 1.0: the wall is broken open)."""
    lines = []
    for c in cylinders or []:
        if not (c.get('full') and c.get('inner')):
            continue
        raw = v_unit(c['axis'])
        if v_len(raw) < 0.5:
            continue
        s0, s1, r, r2 = float(c['s0']), float(c['s1']), float(c['r']), c.get('r2')
        r2 = None if r2 is None else float(r2)
        if s1 < s0:
            s0, s1 = s1, s0
            if r2 is not None:
                r, r2 = r2, r
        if r2 is not None and abs(r2 - r) <= 1e-6:
            r2 = None
        if s1 - s0 < 1e-4:
            continue
        line, dot = None, 1.0
        for cand in lines:
            dot = v_dot(raw, cand['axis'])
            if abs(dot) >= 1.0 - 1e-5 and off_axis(c['origin'], cand['origin'], cand['axis']) <= tol_axis:
                line = cand
                break
        if line is None:
            a = canonical_axis(raw)
            line = {'axis': a, 'origin': v_sub(c['origin'], v_scale(a, v_dot(c['origin'], a))), 'spans': []}
            lines.append(line)
            dot = v_dot(raw, a)
        shift = along(c['origin'], line['origin'], line['axis'])
        if dot < 0:                                     # this face names the same line the other way round
            s0, s1 = shift - s1, shift - s0
            if r2 is not None:
                r, r2 = r2, r
        else:
            s0, s1 = shift + s0, shift + s1
        line['spans'].append({'s0': s0, 's1': s1, 'r': r, 'r2': r2, 'arc': float(c.get('arc', 1.0))})
    out = []
    for line in lines:
        for chain in _geo_chains(line['spans'], tol_r, tol_s):
            spans = [{'s0': _geo_r4(s['s0']), 's1': _geo_r4(s['s1']), 'r': _geo_r4(s['r']),
                      'r2': None if s['r2'] is None else _geo_r4(s['r2']), 'arc': round(s['arc'], 3)} for s in chain]
            out.append({'origin': tuple(_geo_r4(x) for x in line['origin']), 'axis': tuple(line['axis']), 'spans': spans,
                        'lo': spans[0]['s0'], 'hi': spans[-1]['s1']})
    out.sort(key=lambda s: (tuple(round(x, 3) + 0.0 for x in s['axis']), tuple(round(x, 2) + 0.0 for x in s['origin']),
                            round(s['lo'], 2), round(s['spans'][0]['r'], 2)))
    return out


def _geo_end_r(span, hi):
    return span['r2'] if (hi and span.get('r2') is not None) else span['r']


def hole_from_stack(stack, open_lo, open_hi):
    """Turn one stack of stack_holes() into the hole record of the census, once the probe knows which ends are open.

    open_lo, open_hi  bool: does the stack end in air at its "lo" / "hi" end (True) or in material (False)

    Returns None when neither end is open (a closed cavity), else
    {"axis": unit vector pointing OUT of the material at the mouth, "point": (x, y, z) centre of the mouth,
     "segments": [{"d": diameter, "len": length, "d2": far diameter of a cone (key absent for a cylinder)}] from the mouth
     inwards, "depth": total length, "through": bool, "exit": (x, y, z) centre of the other open end or None}.
    The mouth of a through hole is its wider end; with equal ends, the "hi" end.
    Also "arc": the least wall share of its spans (under 1.0: the wall is broken open somewhere)."""
    if not open_lo and not open_hi:
        return None
    spans, a, o = stack['spans'], stack['axis'], stack['origin']
    if open_lo and open_hi:
        mouth_hi = _geo_end_r(spans[-1], True) >= spans[0]['r'] - 0.005
    else:
        mouth_hi = bool(open_hi)
    segs = []
    for sp in (reversed(spans) if mouth_hi else spans):
        near, far = (_geo_end_r(sp, True), sp['r']) if mouth_hi else (sp['r'], _geo_end_r(sp, True))
        seg = {'d': round(2.0 * near, 3), 'len': round(sp['s1'] - sp['s0'], 3)}
        if sp.get('r2') is not None:
            seg['d2'] = round(2.0 * far, 3)
        segs.append(seg)
    s_mouth, s_far = (stack['hi'], stack['lo']) if mouth_hi else (stack['lo'], stack['hi'])
    through = bool(open_lo and open_hi)
    return {'axis': tuple(a) if mouth_hi else tuple(_geo_r4(-x) for x in a),
            'point': tuple(_geo_r4(x) for x in v_add(o, v_scale(a, s_mouth))),
            'segments': segs, 'depth': round(stack['hi'] - stack['lo'], 3), 'through': through,
            'exit': tuple(_geo_r4(x) for x in v_add(o, v_scale(a, s_far))) if through else None,
            'arc': min([sp.get('arc', 1.0) for sp in spans])}


def behind_blind_end(stacks, index, at_hi, tol_axis=0.05):
    """What continues on the same axis behind the blind end of one stack: the nearest other stack of the same body that
    starts behind that end (a clearance hole that stops before its pocket, two pockets from opposite faces).

    stacks  the list of stack_holes()        index  the stack with the blind end        at_hi  True: its "hi" end is blind
    Returns {"stack": index of the other stack, "cap": mm between the two (the plastic left, when nothing else is cut
    there), "d": diameter of the other stack at its near end}, or None."""
    a = stacks[index]
    end, best = (a['hi'] if at_hi else a['lo']), None
    for j, b in enumerate(stacks):
        if j == index:
            continue
        dot = v_dot(a['axis'], b['axis'])
        if abs(dot) < 1.0 - 1e-5 or off_axis(b['origin'], a['origin'], a['axis']) > tol_axis:
            continue
        shift = along(b['origin'], a['origin'], a['axis'])
        lo, hi = (shift + b['lo'], shift + b['hi']) if dot > 0 else (shift - b['hi'], shift - b['lo'])
        r_lo, r_hi = (b['spans'][0]['r'], _geo_end_r(b['spans'][-1], True)) if dot > 0 else (_geo_end_r(b['spans'][-1], True), b['spans'][0]['r'])
        if at_hi and lo >= end - 1e-6:
            cap, r = lo - end, r_lo
        elif not at_hi and hi <= end + 1e-6:
            cap, r = end - hi, r_hi
        else:
            continue
        if best is None or cap < best['cap']:
            best = {'stack': j, 'cap': round(cap, 3), 'd': round(2.0 * r, 3)}
    return best


def coaxial_cylinders(point, axis, depth, cylinders, tol=0.25, reach=10.0):
    """Which cylinders of other bodies lie on the axis of a hole.

    point, axis, depth  the hole: centre of its mouth, unit axis out of the material, total depth (part frame, mm)
    cylinders           [{"name", "kind", "part", "origin", "axis", "s0", "s1"}] in the same frame; s0..s1 is the extent of
                        the cylinder along its own axis from its origin
    tol                 mm the other axis may be off the hole's axis, at both ends of the hole
    reach               mm in front of the mouth and behind the bottom that still count

    Returns [{"name", "kind", "part"}], one per name, sorted by name."""
    bottom = v_sub(point, v_scale(axis, depth))
    seen = {}
    for c in cylinders or []:
        ca = v_unit(c['axis'])
        if abs(v_dot(ca, axis)) < 0.9995:
            continue
        if off_axis(point, c['origin'], ca) > tol or off_axis(bottom, c['origin'], ca) > tol:
            continue
        e0 = along(v_add(c['origin'], v_scale(ca, c['s0'])), point, axis)        # positive: in front of the mouth
        e1 = along(v_add(c['origin'], v_scale(ca, c['s1'])), point, axis)
        if max(e0, e1) < -depth - reach or min(e0, e1) > reach:
            continue
        seen.setdefault(c['name'], {'name': c['name'], 'kind': c.get('kind'), 'part': c.get('part')})
    return [seen[k] for k in sorted(seen)]


def pocket_profile(kind_row):
    """The segments a heat-set pocket of one table row must have, from the mouth inwards.

    kind_row  one entry of rules/tables/inserts.json "kinds": {"relief": {"d", "depth"}, "lead_in": {"d", "depth"},
              "hole": {"d", "depth"}} (hole.depth is measured from the relief floor)

    Returns {"with_relief": [{"d", "len"}, {"d", "len"}, {"d", "len"}], "without_relief": [{"d", "len"}, {"d", "len"}]}.
    The relief segment also has "d_alt": the fallback relief diameters of the row; match_profile() accepts any of them.
    "with_relief" is None for a row that names no relief."""
    lead, hole, relief = kind_row['lead_in'], kind_row['hole'], kind_row.get('relief')
    body = [{'d': float(lead['d']), 'len': float(lead['depth'])},
            {'d': float(hole['d']), 'len': round(float(hole['depth']) - float(lead['depth']), 4)}]
    with_relief = None
    if relief:
        with_relief = [{'d': float(relief['d']), 'len': float(relief['depth']),
                        'd_alt': [float(d) for d in relief.get('fallback_d') or []]}] + [dict(s) for s in body]
    return {'with_relief': with_relief, 'without_relief': body}


def profile_diff(segments, profile, tol_d, tol_len):
    """Where measured segments leave an expected profile (both from the mouth inwards).

    Returns [] when they match, else [{"what": "count" | "cone" | "d" | "len", "index": segment index, "measured",
    "expected"}] ("count": the numbers of segments differ; measured and expected are the two counts)."""
    if not profile or len(segments) != len(profile):
        return [{'what': 'count', 'index': None, 'measured': len(segments), 'expected': len(profile or [])}]
    out = []
    for i, (s, p) in enumerate(zip(segments, profile)):
        if s.get('d2') is not None:
            out.append({'what': 'cone', 'index': i, 'measured': s['d2'], 'expected': p['d']})
            continue
        ds = [p['d']] + list(p.get('d_alt') or [])
        near = min(ds, key=lambda d: abs(s['d'] - d))
        if abs(s['d'] - near) > tol_d + 1e-9:
            out.append({'what': 'd', 'index': i, 'measured': s['d'], 'expected': p['d']})
        short = s['len'] < p['len'] - tol_len - 1e-9
        long_ = s['len'] > p['len'] + tol_len + 1e-9 and i != len(profile) - 1
        if short or long_:
            out.append({'what': 'len', 'index': i, 'measured': s['len'], 'expected': p['len']})
    return out


def match_profile(segments, profile, tol_d, tol_len):
    """Compare measured segments with an expected profile (both from the mouth inwards).

    Returns "exact" when every diameter is within tol_d and every length within tol_len (the last segment may be longer:
    a tip hole below the insert), else None."""
    return None if (not profile or profile_diff(segments, profile, tol_d, tol_len)) else 'exact'


def _geo_named(kind, coaxial):
    return any(str(kind).lower() in str(n).lower() for n in coaxial or [])


def _geo_pick(found, coaxial):
    """one of [(kind, how far its depth is from the row's)]: the kind an insert ref on the axis names, else the nearest depth"""
    if not found:
        return None
    named = [f for f in found if _geo_named(f[0], coaxial)]
    return min(named or found, key=lambda f: (round(f[1], 3), f[0]))[0]


def _geo_fold_tail(segments, hole_d, td):
    """The segments with every narrower cylinder below the hole of a pocket folded into that hole: a tip hole, or the
    clearance hole of the screw that comes through from the far side, is depth below the insert and not another step.
    hole_d is the hole diameter of the table row. Nothing is folded when the stack has no segment of that diameter, or
    when what follows it is not narrower (the far end of a hole with a pocket at each end)."""
    for i, s in enumerate(segments):
        if s.get('d2') is None and abs(s['d'] - hole_d) <= td + 1e-9:
            tail = segments[i + 1:]
            if tail and all(t.get('d2') is None and t['d'] < s['d'] for t in tail):
                return list(segments[:i]) + [dict(s, len=round(s['len'] + sum(t['len'] for t in tail), 4))]
            break
    return list(segments)


def _geo_pocket_fit(segments, row, td, tl, late):
    """Is this stack, from its mouth inwards, the pocket of one row of the inserts table?
    Accepted: the row's profile with or without its relief; a narrower tail below the hole (_geo_fold_tail); one wider
    step in front (a recess: a spot face, a counterbore for a spigot), with or without the pocket's own relief below it.
    late (the table's "hole_short_by_relief"): a pocket WITH a relief may end one relief depth short of the row's hole
    depth. The relief tool cuts the relief and the lead-in into a pocket that exists and leaves the narrow hole as it
    is, so such a pocket keeps the depth it had from the face."""
    prof = pocket_profile(row)
    rel = prof['with_relief']
    tries = [prof['without_relief']] + ([rel] if rel else [])
    if rel and late:
        tries.append([dict(s) for s in rel[:-1]] + [dict(rel[-1], len=round(rel[-1]['len'] - rel[0]['len'], 4))])
    segs = _geo_fold_tail(segments, float(row['hole']['d']), td)
    if len(segs) != len(segments) and row.get('insert_len') is not None:
        # a narrower tail is room below the insert, never a seat for it: the insert itself must end in the row's hole
        i = next(k for k, s in enumerate(segments) if s.get('d2') is None and abs(s['d'] - float(row['hole']['d'])) <= td + 1e-9)
        seat = float(row['insert_len']) - float(row['lead_in']['depth'])
        if late and rel and any(s.get('d2') is None and min(abs(s['d'] - d) for d in [rel[0]['d']] + rel[0]['d_alt']) <= td + 1e-9 for s in segments[:i]):
            seat -= rel[0]['len']                       # a relief cut later moved the insert down by its depth
        if segments[i]['len'] < seat - tl - 1e-9:
            return False

    def fits(sg):
        return any(match_profile(sg, p, td, tl) for p in tries)
    if fits(segs):
        return True
    if rel and len(segs) in (3, 4) and segs[0].get('d2') is None:
        least = min([rel[0]['d']] + rel[0]['d_alt'])
        return bool(segs[0]['d'] >= least - td and segs[0]['len'] >= rel[0]['len'] - tl
                    and segs[0]['d'] > segs[1].get('d2', segs[1]['d']) and fits(segs[1:]))
    return False


def _geo_insert_rows(through, ins):
    """(kind, row) of every row of the inserts table a pocket can match exactly"""
    kinds = ins.get('kinds') or {}
    for kind in sorted(kinds):
        row = kinds[kind]
        if (row.get('band') or {}).get('blind_only') and through:
            continue
        if row.get('profile') == 'recorded' or float((row.get('lead_in') or {}).get('depth') or 0.0) <= 0.0:
            continue                                    # numbers that were in use and never measured: no pocket can match them
        yield kind, row


def _geo_named_only(found, ins, coaxial):
    """found, or nothing when an insert ref on the axis names a kind of the table and the pocket is not that kind's: the
    insert the model puts into the hole decides which pocket it must be. A name alone never makes a pocket exact."""
    named = [k for k in (ins.get('kinds') or {}) if _geo_named(k, coaxial)]
    return [] if named and not any(f[0] in named for f in found) else found


def _geo_insert_exact(segments, through, ins, coaxial=None):
    tol = ins.get('tol') or {}
    td, tl = float(tol.get('d', 0.1)), float(tol.get('depth', 0.3))
    late = bool(ins.get('hole_short_by_relief'))
    found = []
    for kind, row in _geo_insert_rows(through, ins):
        if _geo_pocket_fit(segments, row, td, tl, late):
            # how far the end of the narrow hole itself is from the row's depth: a tail below it says nothing about the insert
            own = [s for s in segments if s.get('d2') is None and abs(s['d'] - float(row['hole']['d'])) <= td + 1e-9][:1]
            lead = [s for s in segments if s.get('d2') is None and abs(s['d'] - float(row['lead_in']['d'])) <= td + 1e-9][:1]
            deep = sum(s['len'] for s in own + lead) if own else segments[-1]['len']
            found.append((kind, abs(deep - float(row['hole']['depth']))))
    return _geo_pick(_geo_named_only(found, ins, coaxial), coaxial)


def pocket_ends_split(segments):
    """(front, back) of a through stack that may hold a pocket at each end, or None: the stack is cut in the middle of
    its narrowest cylinder, which must lie between two wider segments. front runs from the mouth inwards, back from the
    far end inwards; each gets half of the narrow hole."""
    if len(segments) < 3 or any(s.get('d2') is not None for s in segments):
        return None
    i = min(range(len(segments)), key=lambda k: (segments[k]['d'], k))
    if i == 0 or i == len(segments) - 1 or [s['d'] for s in segments].count(segments[i]['d']) != 1:
        return None
    half = dict(segments[i], len=round(segments[i]['len'] / 2.0, 4))
    return list(segments[:i]) + [half], list(reversed(segments[i + 1:])) + [dict(half)]


def _geo_insert_two_ended(segments, through, ins, coaxial=None):
    """the kind of a THROUGH hole that is a pocket of one table row at each end (a post or a spacer with an insert in
    both ends), or None. Each end must be the row's pocket with half of the narrow hole for itself."""
    if not through:
        return None
    halves = pocket_ends_split(segments)
    if halves is None:
        return None
    tol = ins.get('tol') or {}
    td, tl = float(tol.get('d', 0.1)), float(tol.get('depth', 0.3))
    late = bool(ins.get('hole_short_by_relief'))
    found = [(kind, 0.0) for kind, row in _geo_insert_rows(True, ins)
             if _geo_pocket_fit(halves[0], row, td, tl, late) and _geo_pocket_fit(halves[1], row, td, tl, late)]
    return _geo_pick(_geo_named_only(found, ins, coaxial), coaxial)


def pocket_ends(hole):
    """The pockets of one census hole whose class has the role "insert_pocket", as hole records to measure: the hole
    itself, and for a hole with a pocket at each end (class "ends" 2) a second record seen from its far end:
    "end": "exit", "at" and "anchor" at the far mouth with the axis turned round, "segments" in the opposite order.
    The first record has "end": "mouth". A hole without "at" is mirrored from its rounded anchor."""
    first = dict(hole, end='mouth')
    if (hole.get('class') or {}).get('ends') != 2 or not hole.get('through'):
        return [first]
    here = hole.get('at') or hole['anchor']
    axis = v_unit(tuple(here['axis']))
    far = v_sub(tuple(here['point']), v_scale(axis, float(hole['depth'])))
    back = v_scale(axis, -1.0)
    segs = [dict(s) for s in reversed(hole['segments'])]
    for s in segs:
        if s.get('d2') is not None:
            s['d'], s['d2'] = s['d2'], s['d']
    second = dict(hole, end='exit', segments=segs, anchor=anchor_of((hole.get('anchor') or {}).get('kind') or 'insert_pocket', back, far),
                  at={'point': [round(x, 4) for x in far], 'axis': [round(x, 6) for x in back]})
    return [first, second]


def _geo_insert_band(segments, through, ins, coaxial=None):
    if not [s for s in segments if s.get('d2') is None]:
        return None
    depth, found = sum(s['len'] for s in segments), []
    td = float((ins.get('tol') or {}).get('d', 0.1))
    kinds = ins.get('kinds') or {}
    for kind in sorted(kinds):
        row = kinds[kind]
        band = row.get('band') or {}
        rng = band.get('d')
        if not rng or (band.get('blind_only') and through):
            continue
        # a narrower tail below a hole of the row's own diameter (a tip hole, a screw hole from the far side) is depth
        d = min(s['d'] for s in _geo_fold_tail(segments, float(row['hole']['d']), td) if s.get('d2') is None)
        if band.get('min_depth') is not None and not through and depth < float(band['min_depth']) - 1e-9:
            continue                                    # a shallow blind hole of that diameter is a magnet pocket, not an insert pocket
        if rng[0] - 1e-9 <= d <= rng[1] + 1e-9:
            diff = min(abs(d - float(row['hole']['d'])), abs(d - float(row['lead_in']['d'])))
            found.append((kind, diff + (0.0 if through else 0.001 * abs(depth - float(row['hole']['depth'])))))
    return _geo_pick(found, coaxial)


def _geo_pilot(segments, ins):
    ds = [s['d'] for s in segments if s.get('d2') is None]
    if len(ds) != 1 or len(segments) != 1:
        return None
    pilots = ins.get('pilots') or {}
    for kind in sorted(pilots):
        rng = pilots[kind].get('d')
        if rng and rng[0] - 1e-9 <= ds[0] <= rng[1] + 1e-9:
            return kind
    return None


def _geo_trim(segments, chamfer=0.6):
    """the segments without an edge chamfer (a short cone) at either end"""
    segs = list(segments)
    while len(segs) > 1 and segs[0].get('d2') is not None and segs[0]['len'] <= chamfer:
        segs = segs[1:]
    while len(segs) > 1 and segs[-1].get('d2') is not None and segs[-1]['len'] <= chamfer:
        segs = segs[:-1]
    return segs


def _geo_screw(segments, scr):
    """(kind, role, match) of the screw hole these segments are, or None"""
    tol = float((scr.get('tol') or {}).get('d', 0.15))
    sizes = scr.get('sizes') or {}
    segs = _geo_trim(segments)
    cyl = [s.get('d2') is None for s in segs]
    found = []                                          # (not exact, diameter difference, role order, kind, role, match)
    for kind in sorted(sizes):
        row = sizes[kind]
        heads = row.get('heads') or {}
        clr = row.get('clearance_d')
        if len(segs) == 1 and cyl[0]:
            for order, role, key in ((0, 'clearance', 'clearance_d'), (1, 'tap_pilot', 'tap_pilot_d')):
                if row.get(key) is not None and abs(segs[0]['d'] - row[key]) <= tol + 1e-9:
                    found.append((0, round(abs(segs[0]['d'] - row[key]), 4), order, kind, role, 'exact'))
        elif len(segs) in (2, 3) and cyl[1] and clr is not None and abs(segs[1]['d'] - clr) <= tol + 1e-9:
            far_ok = len(segs) == 2 or (cyl[2] and segs[2]['d'] > segs[1]['d'])       # a counterbore from the other face too
            head_ds = [float(h['d']) for h in heads.values() if isinstance(h, dict) and h.get('d') is not None]
            if cyl[0] and far_ok and segs[0]['d'] > segs[1]['d'] + tol:
                cb = (heads.get('cap') or {}).get('counterbore_d')
                exact = cb is not None and abs(segs[0]['d'] - cb) <= tol + 1e-9
                roomy = bool(head_ds) and min(head_ds) - tol <= segs[0]['d'] <= 4.0 * float(row.get('thread_d') or 0) + 2.0
                if exact or roomy:
                    found.append((0 if exact else 1, round(abs(segs[1]['d'] - clr), 4), 0, kind, 'counterbore', 'exact' if exact else 'band'))
            elif not cyl[0] and len(segs) == 2 and abs(segs[0]['d2'] - segs[1]['d']) <= tol + 1e-9 and segs[0]['d'] > segs[1]['d']:
                cs = heads.get('countersunk') or {}
                angle = 2.0 * math.degrees(math.atan2((segs[0]['d'] - segs[0]['d2']) / 2.0, max(segs[0]['len'], 1e-9)))
                exact = (cs.get('d') is not None and abs(segs[0]['d'] - float(cs['d'])) <= 1.0
                         and abs(angle - float(cs.get('angle', 90))) <= 10.0)
                found.append((0 if exact else 1, round(abs(segs[1]['d'] - clr), 4), 0, kind, 'countersink', 'exact' if exact else 'band'))
        elif (len(segs) == 3 and all(cyl) and clr is not None and abs(segs[2]['d'] - clr) <= tol + 1e-9
              and segs[0]['d'] > segs[1]['d'] > segs[2]['d'] + tol):
            # a bore for the head and the key in front of the counterbore: the screw sits deep in the part
            head_ds = [float(h['d']) for h in heads.values() if isinstance(h, dict) and h.get('d') is not None]
            cb = (heads.get('cap') or {}).get('counterbore_d')
            exact = cb is not None and abs(segs[1]['d'] - cb) <= tol + 1e-9
            roomy = bool(head_ds) and min(head_ds) - tol <= segs[1]['d'] <= 4.0 * float(row.get('thread_d') or 0) + 2.0
            if exact or roomy:
                found.append((0 if exact else 1, round(abs(segs[2]['d'] - clr), 4), 0, kind, 'counterbore', 'exact' if exact else 'band'))
    if not found:
        return None
    best = min(found)
    return best[3], best[4], best[5]


def _geo_countersunk_seat(segments, scr, coaxial=None, chamfer=0.6):
    """The screw size of a countersunk screw seat that no clearance row of the screws table fits, or None.
    A seat: the first segment is a cone deeper than an edge chamfer whose included angle is the countersunk head angle
    of the table (within 10 degrees) and whose narrow end is the diameter of the cylinder right behind it (within the
    table's tolerance), AND a body named as a screw lies on the axis while none named as an insert does (by the body's
    own name, the text after the last " / ": a component that bundles screws and inserts names both). A wood screw
    or self-tapping screw is cut into a hole that is narrower than any ISO 273 clearance, and that narrow hole can lie
    in the band of an insert pocket: the cone and the screw on the axis say what it is. The size: the largest thread of
    the table that the narrow hole can take (thread diameter at most the hole + tolerance), and the wide end of the cone
    must be that thread's countersunk head diameter within 1 mm (as for an exact countersink): a 90 degree lead-in of an
    insert pocket is narrower than the head of the screw its hole could take. Else None."""
    names = [str(n).lower().rsplit(' / ', 1)[-1] for n in coaxial or []]   # the body's own name: a bundle "hardware (screws, inserts)" holds both
    if not any('screw' in n or 'bolt' in n for n in names) or any('insert' in n for n in names):
        return None
    if len(segments) < 2 or segments[0].get('d2') is None or segments[0]['len'] <= chamfer or segments[1].get('d2') is not None:
        return None
    tol = float((scr.get('tol') or {}).get('d', 0.15))
    top, cone = segments[0], segments[1]
    if not (top['d'] > top['d2'] + tol and abs(top['d2'] - cone['d']) <= tol + 1e-9):
        return None
    angle = 2.0 * math.degrees(math.atan2((top['d'] - top['d2']) / 2.0, max(top['len'], 1e-9)))
    best = None
    for kind, row in sorted((scr.get('sizes') or {}).items()):
        cs = (row.get('heads') or {}).get('countersunk') or {}
        t = row.get('thread_d')
        if t is None or float(t) > cone['d'] + tol + 1e-9 or abs(angle - float(cs.get('angle', 90))) > 10.0:
            continue
        if best is None or float(t) > best[0]:
            best = (float(t), kind, cs.get('d'))
    if best is None or best[2] is None or abs(top['d'] - float(best[2])) > 1.0:
        return None                                     # the cone must also be the seat of that thread's countersunk head
    return best[1]


def _geo_bearing(segments, through, brg, coaxial=None):
    """(designation, match, by a ref on the axis?) of the bearing whose outer ring one segment would seat, or None.
    A diameter alone is no seat (every round number is the outer diameter of some bearing): the segment must end at a
    shoulder (a narrower segment behind it, or the floor of a blind hole), or a body named "...bearing..." or by the
    designation must lie on the axis. A seat may be a slight cone (a tapered entry): its narrow end counts."""
    tol = float((brg.get('tol') or {}).get('d', 0.05))
    wide = float(brg.get('seat_tol', 0.3))
    sizes = brg.get('sizes') or {}
    has_ref = any('bearing' in str(n).lower() for n in coaxial or [])
    found = []
    for i, s in enumerate(segments):
        far = s.get('d2', s['d'])
        if abs(s['d'] - far) > 1.0:
            continue
        d = min(s['d'], far)
        nxt = segments[i + 1] if i + 1 < len(segments) else None
        shoulder = (nxt is None and not through) or (nxt is not None and min(nxt['d'], nxt.get('d2', nxt['d'])) < d - 0.5)
        for order, kind in enumerate(sizes):            # the order of the file: a short name comes before its five-digit twin
            row = sizes[kind]
            diff, width = abs(d - float(row['D'])), float(row['B'])
            if diff > wide + 1e-9 or not (0.4 * width <= s['len'] <= 2.0 * width + 2.0):
                continue
            named = _geo_named(kind, coaxial) and len(str(kind)) >= 3
            if shoulder or has_ref or named:
                found.append((0 if named else 1, 0 if diff <= tol + 1e-9 else 1, round(diff, 4), round(abs(s['len'] - width), 3), order, kind, has_ref or named))
    if not found:
        return None
    best = min(found)
    return best[5], ('exact' if best[1] == 0 else 'band'), best[6]


def classify_stack(segments, through, tables, coaxial=None):
    """What a hole is, by its diameters and lengths against the tables.

    segments  [{"d", "len", "d2"?}] from the mouth inwards        through  bool
    tables    {"inserts": rules/tables/inserts.json, "screws": .../screws.json, "bearings": .../bearings.json}
    coaxial   optional: names of the bodies of other components on the hole's axis (census "coaxial"[].name)

    Returns None when nothing fits (the census reports the hole as unknown), else
    {"table": "inserts" | "screws" | "bearings", "kind": the row ("M3x4x5", "M3", "6005"),
     "role": "insert_pocket" | "pilot" | "clearance" | "counterbore" | "countersink" | "tap_pilot" | "bearing_seat",
     "match": "exact" (the profile of the row) | "band" (a diameter inside the row's band, the profile is not the row's)}.
    Order: an exact insert pocket, an exact screw hole (clearance, counterbore + clearance, countersink + clearance, tap
    pilot), an exact bearing seat, then the insert bands. A hole in an insert band that is an exact screw hole of
    another size is that screw hole.

    Insert pockets
      - A row that was never measured ("profile": "recorded", or a lead-in depth of 0) is never an exact match: its
        pockets stay in its band, a wrong-pocket row, until somebody measures the insert.
      - Rows with the same diameters (a longer insert of the same thread) are told apart by the insert ref on the axis
        (a coaxial name that holds the kind), else by the depth nearest to the row's hole depth.
      - An exact pocket may sit at the bottom of a recess: one wider step (at least as wide as the smallest relief of the
        row) in front of the pocket, with or without the pocket's own relief below it. The recess is NOT its relief:
        pocket_levels() says whether the pocket has one.
      - "In the band" means: the narrowest cylindrical segment lies inside the row's band; a through hole is not in a
        "blind_only" band; a blind hole shallower than the band's "min_depth" is not in it (a magnet pocket).
      - A countersunk screw seat is no pocket (_geo_countersunk_seat): a 90 degree cone deeper than a chamfer over the
        narrow hole, with a screw named on the axis and no insert. A wood screw's hole fits no clearance row and can lie
        in an insert band; it is the screw row it can take, role "countersink", match "band".
      - Three cases keep a hole in its insert band although an exact screw row fits, because a wrong pocket must stay a
        row: the screw row has the insert's own thread (the narrow hole without its lead-in); it is one straight BLIND
        cylinder (an old straight pocket: a clearance hole goes through); a body named "...insert..." lies on its axis.
      - A narrower cylinder below the row's hole (a tip hole, the clearance hole of a screw that comes through from the
        far side) is depth below the insert, in the exact test and in the band test. The insert itself must still end
        in the row's own hole diameter: a hole that gets narrower before the insert ends stays in the band.
      - With "hole_short_by_relief" true in the table, a pocket WITH a relief may end one relief depth short of the
        row's hole depth (the relief tool cuts relief and lead-in into an existing pocket and leaves the hole).
      - A THROUGH hole that is the row's pocket at each end (a post with an insert in both ends) is exact with
        "ends": 2 in the class; pocket_ends() gives the two pockets to measure.
    Screw holes: a short cone (up to 0.6 mm) at either end is an edge chamfer. A counterbore or countersink of an odd
    diameter over an exact clearance hole is that screw's hole with match "band". A wider bore in front of a counterbore
    (the head sits deep in the part, the key comes down the bore) is still that screw's counterbored hole.
    Bearing seats: never by the diameter alone (see _geo_bearing): a shoulder or a bearing ref on the axis is needed.
    Within tables["bearings"]["tol"]["d"] of an outer diameter the match is "exact", within "seat_tol" (default 0.3 mm:
    a printed seat is tapered on purpose) it is "band".
    The whole order: exact insert pocket; exact screw hole; bearing seat with its bearing on the axis; insert band;
    pilot band of the insert table (role "pilot", match "exact"); screw hole with an odd counterbore; bearing seat by its
    shoulder."""
    if not segments:
        return None
    tables = tables or {}
    ins, scr, brg = tables.get('inserts') or {}, tables.get('screws') or {}, tables.get('bearings') or {}
    kind = _geo_insert_exact(segments, through, ins, coaxial)
    if kind:
        return {'table': 'inserts', 'kind': kind, 'role': 'insert_pocket', 'match': 'exact'}
    kind = _geo_insert_two_ended(segments, through, ins, coaxial)
    if kind:
        return {'table': 'inserts', 'kind': kind, 'role': 'insert_pocket', 'match': 'exact', 'ends': 2}
    band = _geo_insert_band(segments, through, ins, coaxial)
    screw = _geo_screw(segments, scr)
    sunk = _geo_countersunk_seat(segments, scr, coaxial) if band else None
    if sunk:                                            # a countersunk screw seat: its screw row when one fits, else the thread it takes
        if screw and screw[1] == 'countersink':
            return {'table': 'screws', 'kind': screw[0], 'role': 'countersink', 'match': screw[2]}
        return {'table': 'screws', 'kind': sunk, 'role': 'countersink', 'match': 'band'}
    if screw and screw[2] == 'exact':
        stays = False
        if band:
            thread = ((ins.get('kinds') or {}).get(band) or {}).get('thread')
            one_blind = len(segments) == 1 and not through
            has_insert = any('insert' in str(n).lower() for n in coaxial or [])
            stays = thread == screw[0] or one_blind or has_insert
        if not stays:
            return {'table': 'screws', 'kind': screw[0], 'role': screw[1], 'match': 'exact'}
    seat = _geo_bearing(segments, through, brg, coaxial)
    if seat and seat[2]:
        return {'table': 'bearings', 'kind': seat[0], 'role': 'bearing_seat', 'match': seat[1]}
    if band:
        return {'table': 'inserts', 'kind': band, 'role': 'insert_pocket', 'match': 'band'}
    pilot = _geo_pilot(segments, ins)
    if pilot:
        return {'table': 'inserts', 'kind': pilot, 'role': 'pilot', 'match': 'exact'}
    if screw:
        return {'table': 'screws', 'kind': screw[0], 'role': screw[1], 'match': screw[2]}
    if seat:
        return {'table': 'bearings', 'kind': seat[0], 'role': 'bearing_seat', 'match': seat[1]}
    return None


def is_hole(segments, klass, d_max):
    """Is a stack a hole of the census, or a bore (the opening of a ring, a housing, a tube)?
    A hole: it has a class, or its narrowest diameter is at most d_max. A bore is listed apart and nobody is asked what
    it is."""
    if klass:
        return True
    return min([min(s['d'], s.get('d2', s['d'])) for s in segments]) <= d_max + 1e-9


# ---- heat-set pockets: where the walls are measured and what the rays say --------------------------------------------------

def radius_at(segments, depth):
    """Radius of a hole at `depth` mm below its mouth (segments from the mouth inwards); the last segment's far radius
    below the bottom."""
    t = 0.0
    for s in segments:
        if depth <= t + s['len'] + 1e-9:
            d2 = s.get('d2')
            if d2 is None:
                return s['d'] / 2.0
            k = 0.0 if s['len'] <= 0 else min(1.0, max(0.0, (depth - t) / s['len']))
            return (s['d'] + (d2 - s['d']) * k) / 2.0
        t += s['len']
    last = segments[-1]
    return last.get('d2', last['d']) / 2.0


def pocket_levels(segments, kind_row, tol_d=0.1):
    """Where the plastic round one heat-set pocket is measured.

    segments  the hole, from the mouth inwards (census)        kind_row  its row of rules/tables/inserts.json "kinds"

    Returns {"recess": {"d", "depth"} or None      a first step that is NOT a relief: wider than the smallest relief of the
                                                    row and no relief diameter of the row (a spot face, a counterbore).
                                                    The pocket begins at its floor and needs its relief there
             "relief": {"d", "depth"} or None      the melt relief: the step in front of the lead-in (the first segment, or
                                                    the one behind a recess) when it is a cylinder of the row's relief
                                                    diameter or one of its fallbacks, within tol_d. Its depth is NOT
                                                    judged here (inserts.relief_depth_min / _max are the judge's)
             "top": depth of the relief floor below the mouth (the recess floor without a relief, 0.0 with neither): the
                    insert ends flush with it
             "levels": [{"name": "relief" | "lead_in" | "insert", "depth": mm below the mouth, "from_r": radius the wall
                         is measured from}]}
    relief   half the relief depth below the mouth or the recess floor (the table depth when the pocket has none), from
             the hole's own wall
    lead_in  half the lead-in depth below the relief floor (0.5 mm for a row with no lead-in), from the hole's own wall
    insert   0.6, 0.8 and 1.0 of the insert length below the relief floor, from the lead-in radius or the hole's own
             wall, whichever is larger: the knurl melts the plastic round the lead-in diameter, not round the narrow hole
    A depth below the bottom of the hole is moved up to 0.05 mm above it.
    A step of a relief diameter in front of another step of a relief diameter is a recess with the relief below it."""
    total = sum(s['len'] for s in segments)
    rel_row = kind_row.get('relief') or {}
    rel_ds = [float(d) for d in [rel_row.get('d')] + list(rel_row.get('fallback_d') or []) if d is not None]

    def is_relief(s):
        return s.get('d2') is None and any(abs(s['d'] - d) <= tol_d + 1e-9 for d in rel_ds)
    recess = relief = None
    if rel_ds and len(segments) > 1 and segments[0].get('d2') is None and segments[0]['d'] >= min(rel_ds) - tol_d:
        first = segments[0]
        if is_relief(first) and not (len(segments) > 3 and is_relief(segments[1]) and segments[1]['d'] < first['d']):
            relief = {'d': first['d'], 'depth': first['len']}
        else:
            recess = {'d': first['d'], 'depth': first['len']}
            if len(segments) > 2 and is_relief(segments[1]) and segments[1]['d'] < first['d']:
                relief = {'d': segments[1]['d'], 'depth': segments[1]['len']}
    base = recess['depth'] if recess else 0.0
    top = base + (relief['depth'] if relief else 0.0)

    def clip(t):
        return max(0.01, min(t, total - 0.05))
    lead, length = kind_row['lead_in'], float(kind_row.get('insert_len') or kind_row['hole']['depth'])
    t_rel = clip(base + (relief['depth'] if relief else float(rel_row.get('depth') or 0.5)) / 2.0)
    t_lead = clip(top + max(float(lead['depth']), 1.0) / 2.0)                # a row without a lead-in: half a millimetre below the mouth
    r_lead = max(radius_at(segments, t_lead), 0.0)
    levels = [{'name': 'relief', 'depth': round(t_rel, 3), 'from_r': round(radius_at(segments, t_rel), 4)},
              {'name': 'lead_in', 'depth': round(t_lead, 3), 'from_r': round(r_lead, 4)}]
    seen = set()
    for k in (0.6, 0.8, 1.0):
        t = round(clip(top + length * k), 3)
        if t not in seen:
            seen.add(t)
            levels.append({'name': 'insert', 'depth': t, 'from_r': round(max(radius_at(segments, t), r_lead), 4)})
    return {'recess': recess, 'relief': relief, 'top': round(top, 3), 'levels': levels}


def ray_direction(axis, k, n):
    """Unit direction of ray `k` of `n` round an axis: square to it, turning from the first to the second vector of
    axis_frame(axis)."""
    u, v = axis_frame(axis)
    th = 2.0 * math.pi * k / n
    return v_add(v_scale(u, math.cos(th)), v_scale(v, math.sin(th)))


def wall_summary(walls, axis, thin_under, beyond=None, cap=None):
    """What a ring of rays says about the plastic round a pocket at one level.

    walls       one wall per ray, mm from the surface outwards to the first exit from the body (already capped)
    axis        unit axis of the pocket (out of the material); ray k points along ray_direction(axis, k, len(walls))
    thin_under  mm: a ray with less wall is "thin" (the wall the rule asks for; the probe writes it into the facts)
    beyond      per ray: None (not looked at), "free" (nothing stands within the room the wall would need) or
                {"name", "kind", "part", "gap", ...} (what stands there, gap = mm between the wall and it)
    cap         mm the rays were walked to at most

    Returns {"min": least wall, rounded to 0.01, "dir": [x, y, z] unit direction of that ray (the middle one when
    several rays read the same least wall), "free_arc_deg": arc of thin rays with nothing in the way, "blocked_by":
    [{"name", "kind", "part", "gap", ...}] per name the entry with the least gap (of equal gaps: the one beyond the
    thinnest wall), sorted by name, "thin_arc_deg": arc of
    all thin rays, "thin_under", "min_beyond": what stands in the direction of the least wall: None (not thin) | "free" |
    a name, "w": the walls rounded to 0.01}.
    A thin ray nobody looked beyond counts as free: unknown must not read as blocked."""
    n = len(walls)
    w = [round(float(x), 2) + 0.0 for x in walls]
    if cap is not None:
        w = [min(x, round(float(cap), 2)) for x in w]
    k_min = min(range(n), key=lambda k: (w[k], k))
    lo = hi = k_min                                     # several rays read the same least wall: take the middle of that run
    while hi - lo < n - 1 and abs(w[(lo - 1) % n] - w[k_min]) < 0.015:
        lo -= 1
    while hi - lo < n - 1 and abs(w[(hi + 1) % n] - w[k_min]) < 0.015:
        hi += 1
    k_mid = ((lo + hi) // 2) % n
    step = 360.0 / n
    thin = [k for k in range(n) if w[k] < thin_under - 1e-9]
    by, free = {}, 0
    for k in sorted(thin, key=lambda i: (w[i], i)):     # thinnest first: with equal gaps the entry of the thinnest ray stays
        b = beyond[k] if beyond else None
        if isinstance(b, dict):
            gap = round(float(b.get('gap', 0.0)), 2) + 0.0
            if b['name'] not in by or gap < by[b['name']]['gap']:
                by[b['name']] = dict(b, gap=gap)
        else:
            free += 1
    b_min = (beyond[k_mid] if beyond else None) if k_mid in thin else None
    return {'min': w[k_min], 'dir': [_geo_r4(c) for c in ray_direction(axis, k_mid, n)],
            'free_arc_deg': round(free * step, 1), 'blocked_by': [by[k] for k in sorted(by)],
            'thin_arc_deg': round(len(thin) * step, 1), 'thin_under': thin_under,
            'min_beyond': (b_min['name'] if isinstance(b_min, dict) else 'free') if k_mid in thin else None, 'w': w}


def relief_room(exits, from_r, diameters):
    """The least wall that stays (or would stay) beside a melt relief of each diameter.

    exits      per ray, mm from the axis to the first exit from the body at the relief depth
    from_r     radius the rays started from (the hole's own wall at that depth); only a relief at least that wide can be
               judged from them, a narrower one is the wall it would leave if the pocket were drawn with it
    diameters  relief diameters to try (the row's own, then its fallbacks)
    Returns [{"d", "wall"}] in the order given: wall = least exit - d / 2; 0.0 = that relief would break out of the part."""
    return [{'d': float(d), 'wall': round(max(0.0, min(exits) - float(d) / 2.0), 2) + 0.0} for d in diameters]


# ---- a mesh as an obstacle: is a point inside a closed triangle mesh ------------------------------------------------------

def mesh_grid(coords, indices, cell=5.0, scale=1.0):
    """Prepare a closed triangle mesh for mesh_contains().

    coords   flat [x0, y0, z0, x1, ...] of the nodes        indices  flat [a, b, c, ...] node numbers of the triangles
    cell     size of the buckets, in the unit of the result        scale  factor applied to every coordinate (10: cm -> mm)
    Returns {"lo", "hi", "cell", "xs", "ys", "zs", "grid": {(gy, gz): [(a, b, c), ...]}}: the triangles bucketed on the
    y-z plane, for a ray along +x. None for an empty mesh."""
    xs = [coords[i] * scale for i in range(0, len(coords), 3)]
    ys = [coords[i + 1] * scale for i in range(0, len(coords), 3)]
    zs = [coords[i + 2] * scale for i in range(0, len(coords), 3)]
    if not xs:
        return None
    grid = {}
    for k in range(0, len(indices) - 2, 3):
        tri = (indices[k], indices[k + 1], indices[k + 2])
        y0, y1 = min(ys[i] for i in tri), max(ys[i] for i in tri)
        z0, z1 = min(zs[i] for i in tri), max(zs[i] for i in tri)
        for gy in range(int(math.floor(y0 / cell)), int(math.floor(y1 / cell)) + 1):
            for gz in range(int(math.floor(z0 / cell)), int(math.floor(z1 / cell)) + 1):
                grid.setdefault((gy, gz), []).append(tri)
    return {'lo': (min(xs), min(ys), min(zs)), 'hi': (max(xs), max(ys), max(zs)), 'cell': cell, 'xs': xs, 'ys': ys, 'zs': zs, 'grid': grid}


def mesh_contains(mesh, p):
    """Is the point `p` inside the closed mesh of mesh_grid()? Counts the triangles a ray along +x crosses."""
    if mesh is None or not box_has(mesh['lo'], mesh['hi'], p):
        return False
    xs, ys, zs, cell = mesh['xs'], mesh['ys'], mesh['zs'], mesh['cell']
    n = 0
    for a, b, c in mesh['grid'].get((int(math.floor(p[1] / cell)), int(math.floor(p[2] / cell))), ()):
        y0, y1, y2 = ys[a] - p[1], ys[b] - p[1], ys[c] - p[1]
        z0, z1, z2 = zs[a] - p[2], zs[b] - p[2], zs[c] - p[2]
        den = (y1 - y0) * (z2 - z0) - (y2 - y0) * (z1 - z0)
        if abs(den) < 1e-12:
            continue
        u = (-y0 * (z2 - z0) + z0 * (y2 - y0)) / den
        v = (-(y1 - y0) * z0 + (z1 - z0) * y0) / den
        if u < 0 or v < 0 or u + v > 1:
            continue
        if xs[a] + u * (xs[b] - xs[a]) + v * (xs[c] - xs[a]) > p[0]:
            n += 1
    return n % 2 == 1


# ---- openings that are not round: the outline of a planar loop -------------------------------------------------------------

def opening_shape(points, normal, arcs=0, edges=None):
    """Size and place of a planar loop from points on its outline.

    points  (x, y, z) on the loop        normal  unit normal of its plane
    arcs    how many of its edges are curved        edges  how many edges it has (None: not known)
    Returns {"point": (x, y, z) centre of the smallest rectangle round the loop, "size": [long side, short side],
    "round": bool (a circle: a hole, not an opening), "shape": "slot" | "window"}.
    Round means: every edge is curved and every point lies on one circle. The corners of a rectangle or of a hexagon lie
    on a circle too, so straight edges are never round. A slot is long: three times as long as wide, or 1.3 times with
    curved ends. None for fewer than three points."""
    if len(points) < 3:
        return None
    u, v = axis_frame(normal)
    o = points[0]
    q = [(v_dot(v_sub(p, o), u), v_dot(v_sub(p, o), v)) for p in points]
    best = None
    for i in range(len(q)):
        dx, dy = q[i][0] - q[i - 1][0], q[i][1] - q[i - 1][1]
        length = math.hypot(dx, dy)
        if length < 1e-6:
            continue
        ex, ey = dx / length, dy / length
        a = [x * ex + y * ey for x, y in q]
        b = [-x * ey + y * ex for x, y in q]
        w, h = max(a) - min(a), max(b) - min(b)
        if best is None or w * h < best[0] - 1e-9:
            best = (w * h, w, h, (max(a) + min(a)) / 2.0, (max(b) + min(b)) / 2.0, ex, ey)
    if best is None:
        return None
    _, w, h, ca, cb, ex, ey = best
    cx, cy = ca * ex - cb * ey, ca * ey + cb * ex
    centre = v_add(o, v_add(v_scale(u, cx), v_scale(v, cy)))
    radii = [math.hypot(x - cx, y - cy) for x, y in q]
    long_, short = max(w, h), min(w, h)
    is_round = bool(arcs) and (edges is None or arcs >= edges) and len(q) >= 6 and max(radii) - min(radii) <= 0.03 * max(radii) + 0.01
    slot = short > 0 and (long_ >= 3.0 * short or (arcs and long_ >= 1.3 * short))
    return {'point': tuple(_geo_r4(c) for c in centre), 'size': [round(long_, 2), round(short, 2)], 'round': bool(is_round),
            'shape': 'slot' if slot else 'window'}


# ---- the tape of a measurement that needs more than one call -------------------------------------------------------------------

TAPE_MOD = (1 << 61) - 1


def cursor_digest(cursor):
    """16 hex characters for a cursor (any JSON value). The host takes it of the cursor it sends, the unit of the cursor
    as it arrived, BEFORE its probe runs (a probe may work in place on the cursor): a result file is the answer of a
    call only when the two are equal. One definition for both sides."""
    return hashlib.sha256(json.dumps(cursor, sort_keys=True).encode('utf-8')).hexdigest()[:16]


def state_digest(rows):
    """16 hex characters for any JSON value that describes bodies (lists of names, boxes, state texts)."""
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode('utf-8')).hexdigest()[:16]


def tape_pack(answers, check=0):
    """A list of yes / no answers as plain JSON for a cursor: {"n": how many, "hex": the answers as one hexadecimal number
    (the first answer is the highest bit; "" for none), "sum": `check`, the tape_sum() over the questions they answer}."""
    n = len(answers)
    bits = 0
    for a in answers:
        bits = (bits << 1) | (1 if a else 0)
    return {'n': n, 'hex': ('%x' % bits) if n else '', 'sum': int(check)}


def tape_unpack(saved):
    """(answers as a list of bool, the stored sum) of a tape_pack() value. ValueError when it is not one: a cursor that
    was damaged must not be read as answers."""
    if not isinstance(saved, dict) or isinstance(saved.get('n'), bool) or not isinstance(saved.get('n'), int) or saved['n'] < 0 \
            or not isinstance(saved.get('hex'), str) or isinstance(saved.get('sum'), bool) or not isinstance(saved.get('sum'), int):
        raise ValueError('not a tape: %r' % (saved,))
    n = saved['n']
    bits = int(saved['hex'], 16) if saved['hex'] else 0
    if bits >> n:
        raise ValueError('a tape of %d answers holds more than %d bits' % (n, n))
    return [bool((bits >> (n - 1 - i)) & 1) for i in range(n)], saved['sum']


def tape_sum(check, point, tag=0):
    """The running check of the questions of a tape: `check` with one more question, "is `point` (mm, to 0.001) inside
    the body `tag`", worked in. Two runs that ask the same questions in the same order end with the same number; a
    point that moved, one more or one fewer question, or another order gives another one."""
    q = (int(round(point[0] * 1000.0)) * 73856093 + int(round(point[1] * 1000.0)) * 19349663 + int(round(point[2] * 1000.0)) * 83492791 + int(tag) * 2654435761 + 1)
    return (int(check) * 1000003 + q) % TAPE_MOD


# ---- the outline of a large planar loop, read over several calls (fusion_census) ---------------------------------------------
# opening_shape() takes every point of a loop at once and tries one direction per point against all points: on a toothed
# outline of thousands of edges that is seconds of Python in one piece. The functions below give EXACTLY its answer from
# a stream of points read edge by edge, with a state of plain JSON between two calls:
#   pass 1  outline_q() of every point; hull_keep() thins them to the points that can be the largest or the smallest
#           along some direction (a point at least `delta` inside the hull never is), so a direction costs |kept|, not N;
#   pass 2  the same points again, in the same order: rect_direction() for each, against the kept points;
#   pass 3  only for a loop of curved edges alone: the least distance from the centre (the largest is among the kept).
# Every value is computed by the same expression as in opening_shape(), so the floats are the same floats; a float goes
# through the JSON of a cursor unchanged (its shortest repr reads back to the same bits, -0.0 included).

def outline_q(p, o, u, v):
    """The point p of a planar loop in the frame of opening_shape(): (along u, along v) from the first point o."""
    return (v_dot(v_sub(p, o), u), v_dot(v_sub(p, o), v))


def _hull2(pts):
    """Convex hull of 2D points, counter-clockwise, no three in a line (monotone chain); fewer than 3 points as they are."""
    ps = sorted(set(pts))
    if len(ps) < 3:
        return ps

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in ps:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(ps):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def hull_keep(pts, delta=1e-6):
    """The points of `pts` (2D, mm) that may be the largest or the smallest along some direction, or the farthest from
    some point inside: every point except those that lie at least `delta` inside the convex hull of `pts`, in their
    order. A point that is dropped has a disc of radius `delta` round it inside the hull, so along any direction some
    kept point lies at least `delta` beyond it: far more than the rounding of a product of two floats, so the largest
    and the smallest of x*ex + y*ey (and of the distance from a point inside) over the kept points are the very floats
    those of all points are.
    The test is conservative: a point is dropped only when the hull, blown up round an inner point c by the factor
    1 / (1 - rho) (rho = delta / the least distance from c to an edge line), still holds it with a margin; the five
    edges round its angle from c are asked, so a point beside a vertex is never judged by the wrong edge."""
    pts = [(float(p[0]), float(p[1])) for p in pts]
    hull = _hull2(pts)
    if len(hull) < 3:
        return pts
    n = len(hull)
    cx = sum(p[0] for p in hull) / n
    cy = sum(p[1] for p in hull) / n
    edges, r = [], None
    for k in range(n):
        a, b = hull[k], hull[(k + 1) % n]
        ln = math.hypot(b[0] - a[0], b[1] - a[1])
        if ln <= 0.0:
            return pts
        edges.append((a, b, ln))
        d = ((b[0] - a[0]) * (cy - a[1]) - (b[1] - a[1]) * (cx - a[0])) / ln
        r = d if r is None or d < r else r
    if not r or r <= 0.0 or not math.isfinite(r):
        return pts
    rho = float(delta) / r
    if rho >= 0.5:
        return pts
    ang = [math.atan2(p[1] - cy, p[0] - cx) for p in hull]
    k0 = min(range(n), key=lambda k: ang[k])
    order = [(ang[(k0 + k) % n], (k0 + k) % n) for k in range(n)]
    keys = [a for a, _ in order]
    tol = 1e-9
    out = []
    for p in pts:
        y = (cx + (p[0] - cx) / (1.0 - rho), cy + (p[1] - cy) / (1.0 - rho))
        t = math.atan2(y[1] - cy, y[0] - cx)
        lo, hi = 0, n                                   # the last angle of the hull not past t (bisect_right - 1)
        while lo < hi:
            mid = (lo + hi) // 2
            if t < keys[mid]:
                hi = mid
            else:
                lo = mid + 1
        s = order[(lo - 1) % n][1]
        inside = True
        for j in range(s - 2, s + 3):
            a, b, ln = edges[j % n]
            if (b[0] - a[0]) * (y[1] - a[1]) - (b[1] - a[1]) * (y[0] - a[0]) <= tol * ln:
                inside = False
                break
        if not inside:
            out.append(p)
    return out


_RECT_WINDOW = 8        # kept points on either side of the four extremes of the last direction (rect_direction's hint)


def _rect_argext(a, ids):
    """[index of the largest, index of the smallest] of the values a, as indices of `ids` (first one on a tie)"""
    r = range(len(a))
    return [ids[max(r, key=a.__getitem__)], ids[min(r, key=a.__getitem__)]]


def rect_direction(best, prev, q, kept, hint=None):
    """One step of opening_shape(): the direction from the point `prev` to the point `q`, tried against the points
    `kept` (hull_keep() of all of them). best: None or [w*h, w, h, ca, cb, ex, ey] of the best direction so far; returns
    the new best (the same list when this direction is no better).
    hint: None, or a dict kept from one direction to the next ({} at the start): the four extremes of the last
    direction. The kept points round them are asked first; their rectangle is never larger than that of all points
    (the same products, fewer of them, and rounding keeps the order), so when it is no better than `best` the
    direction is not better either and the other points are not asked. Only the time differs, never the answer: on a
    convex outline every point is kept, and a direction then costs a few dozen points instead of thousands."""
    dx, dy = q[0] - prev[0], q[1] - prev[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return best
    ex, ey = dx / length, dy / length
    n = len(kept)
    if best is not None and hint is not None and hint.get('at') and n > 8 * _RECT_WINDOW:
        ids = sorted(set((c + d) % n for c in hint['at'] for d in range(-_RECT_WINDOW, _RECT_WINDOW + 1)))
        a = [kept[i][0] * ex + kept[i][1] * ey for i in ids]
        b = [-kept[i][0] * ey + kept[i][1] * ex for i in ids]
        if (max(a) - min(a)) * (max(b) - min(b)) >= best[0] - 1e-9:
            hint['at'] = _rect_argext(a, ids) + _rect_argext(b, ids)
            return best
    a = [x * ex + y * ey for x, y in kept]
    b = [-x * ey + y * ex for x, y in kept]
    if hint is not None and n > 8 * _RECT_WINDOW:
        ids = range(n)
        hint['at'] = _rect_argext(a, ids) + _rect_argext(b, ids)
    w, h = max(a) - min(a), max(b) - min(b)
    if best is None or w * h < best[0] - 1e-9:
        return [w * h, w, h, (max(a) + min(a)) / 2.0, (max(b) + min(b)) / 2.0, ex, ey]
    return best


def rect_centre(best):
    """(cx, cy) of the best rectangle, in the frame of outline_q()."""
    _, w, h, ca, cb, ex, ey = best
    return ca * ex - cb * ey, ca * ey + cb * ex


def round_wanted(arcs, edges, n):
    """Does opening_shape() look at the spread of the distances from the centre at all (every edge curved, 6 points)?"""
    return bool(arcs) and (edges is None or arcs >= edges) and n >= 6


def spread_round(r_max, r_min):
    """The roundness test of opening_shape() on the largest and the smallest distance from the centre."""
    return r_max - r_min <= 0.03 * r_max + 0.01


def shape_of_rect(best, o, normal, arcs, round_):
    """The answer of opening_shape() from the best rectangle (rect_direction), the first point o of the loop, the
    normal, the number of curved edges and the roundness (spread_round, or False when round_wanted() is False)."""
    u, v = axis_frame(normal)
    _, w, h, ca, cb, ex, ey = best
    cx, cy = rect_centre(best)
    centre = v_add(o, v_add(v_scale(u, cx), v_scale(v, cy)))
    long_, short = max(w, h), min(w, h)
    slot = short > 0 and (long_ >= 3.0 * short or (arcs and long_ >= 1.3 * short))
    return {'point': tuple(_geo_r4(c) for c in centre), 'size': [round(long_, 2), round(short, 2)], 'round': bool(round_),
            'shape': 'slot' if slot else 'window'}


def outline_picks(n):
    """(indices of the starting points, indices of the rim points) opening_shape()'s caller takes from a loop of n
    points: points[::max(1, n // 3)][:3] and points[::max(1, n // 4)][:4]."""
    return list(range(0, n, max(1, n // 3)))[:3], list(range(0, n, max(1, n // 4)))[:4]
