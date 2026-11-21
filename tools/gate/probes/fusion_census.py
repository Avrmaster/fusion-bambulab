"""Hole census of one part: every cylindrical hole stack and every non-round opening, classified against the tables.

Fusion-side unit. Its text is pasted behind _geom.py, _geom_rings.py and _fusion.py (gate_fusion_rpc.build_script), so
their functions are globals here and this file imports nothing from disk. No adsk import is needed: _fusion.py has it.
All work happens inside probe(job), which _fusion.run() calls. Read-only: no timeline edit, no body deleted, temporary
geometry only through adsk.fusion.TemporaryBRepManager. The API is in cm, every fact is in mm.
job["legacy"] maps "insert_pockets", "fusion_parts" and "screw_access" to the absolute path of that tool of
tools/fusion (None when it is not there): a probe that wraps one loads it inside probe() with
g = {"__file__": path}; exec(open(path, encoding="utf-8").read(), g).

How it works (the logic is in _geom.py, this file only asks Fusion):
  1. every cylindrical and conical face of the body -> its axis line, radius and extent along the axis (from its
     vertices, the centres of its circular edges and a few points along its other curved edges). A ring of 16 points just
     outside the surface at mid height says how much of the circle is wall; 11 of 16 make it the wall of a hole, so a
     tooth groove, a fillet and the round end of a slot are not holes, and a hole that breaks out of the part still is.
     Air on a ring just inside the surface says the material is outside (a boss or the outside of a round part is not).
     The same walk notes the planar faces with an inner loop: only those are read again in step 3.
  2. stack_holes() groups those faces into coaxial stacks. The cylinders of the bodies round the part are read once
     (only those whose axis can lie on the axis of a stack are kept). For each stack: an island on the axis (the outer
     wall of a ring groove) is dropped; air on the axis just past an end makes that end open; hole_from_stack() gives
     the mouth, the segments and the depth; a blind end gets the plastic behind it (walked along the axis) and what
     continues behind it (behind_blind_end). classify_stack() names it. A stack with no class whose narrowest diameter
     is over "hole_d_max" is a bore (the opening of a ring or a housing): listed under "bores", not under "holes".
  3. every inner loop of a planar face that is not a circle and has air under it is an opening (a window, a slot, a nut
     trap, a tie tunnel); a recess shallower than "opening_min_depth" is not. A loop of more than _CEN_SMALL edges (the
     toothed outline a flange leaves round the teeth of a pulley) is read edge by edge in up to three passes, and its
     size comes from the streaming form of opening_shape() in _geom.py (the same answer, without trying every
     direction against every point). Its outline points stay in the memory of the Fusion process between two calls
     (_cen_memory), not in the cursor: when they are gone (Fusion restarted) the loop is read again from its first edge.
Not seen: an opening in a curved face, a notch that is part of the outline of a face, a hole whose wall is no cylinder
(a modelled thread). Two coaxial cylinders with a fillet between them (no cone, no touching ends) are two holes.

Steps and the clock. The unit is cut into small steps and the clock is read before every one (the probe stops when less
than _CEN_RESERVE seconds are left, after at least one step of the call): one face of the part (one EDGE of a face or a
loop with more than _CEN_SMALL edges), one occurrence of the walk over the document, one face of a neighbour, one stack,
one planar face, one loop. The point tests of a step go through a GateTape of _fusion.py (the clock is read every 16 of
them), and a step cut inside its point tests is in the cursor as "tape". Everything else a step leaves for the next
call is plain JSON in the cursor (the outline points of a large loop excepted, see 3.), so the facts of a unit
measured over many calls are the facts of one call."""

_CEN_EPS = 0.05         # mm a test point is put off a surface
_CEN_LIMITS = {'hole_d_max': 30.0, 'floor_max': 20.0, 'opening_min_depth': 1.0, 'coaxial_tol': 0.25, 'coaxial_reach': 10.0,
               'neighbour_faces': 8000}
_CEN_SMALL = 32         # edges: a face or a loop with at most this many is read in one step
_CEN_BUF = 256          # outline points gathered before hull_keep() thins the kept ones again
_CEN_RESERVE = 2.5      # seconds left at which a call stops before its next step
_CEN_STAGES = ('prelude', 'faces', 'neighbours', 'state', 'stacks', 'openings', 'shape')


_CEN_TAPE = None                # the GateTape of this call (_fusion.py); None: ask Fusion directly
_CEN_RUN = None                 # the counters of this call: stage, [seconds, API reads, point tests] per stage and per face


class _CenStop(Exception):
    """The time of this call is used up: the step in progress is in the cursor."""


def _cen_stage(name):
    """Count the seconds since the last switch to the stage in progress, and switch to `name` (None: only count)."""
    run = _CEN_RUN
    if run is None:
        return
    now = time.time()
    run['rows'].setdefault(run['stage'], [0.0, 0, 0])[0] += now - run['t']
    run['t'] = now
    if name is not None:
        run['stage'] = name


def _cen_face(key):
    """Count the seconds since the last switch to the face in progress, and switch to `key` ("f<index>", None: none)."""
    run = _CEN_RUN
    if run is None:
        return
    now = time.time()
    if run['face'] is not None:
        run['faces'].setdefault(run['face'], [0.0, 0, 0])[0] += now - run['tf']
    run['face'], run['tf'] = key, now


def _cen_n(k=1, pc=False):
    """Count k Fusion API reads (pc: point tests) of the stage and the face in progress. A read is one property read or
    one method call on a Fusion object; the coordinates of a point and the work inside pointContainment are not."""
    run = _CEN_RUN
    if run is None:
        return
    i = 2 if pc else 1
    run['rows'].setdefault(run['stage'], [0.0, 0, 0])[i] += k
    if run['face'] is not None:
        run['faces'].setdefault(run['face'], [0.0, 0, 0])[i] += k


def _cen_in(body, p):
    tape = _CEN_TAPE
    if tape is not None:
        before = tape.fresh
        answer = tape.ask(body, p)
        if tape.fresh != before:
            _cen_n(1, pc=True)
        return answer
    _cen_n(1, pc=True)
    return body.pointContainment(gate_point(p)) == adsk.fusion.PointContainment.PointInsidePointContainment


def _cen_ring(body, c, u, v, r, ks, n):
    """how many of the ring points `ks` (out of n round the centre c, radius r) lie inside the body"""
    hit = 0
    for k in ks:
        th = 2.0 * math.pi * k / n
        hit += _cen_in(body, v_add(c, v_add(v_scale(u, r * math.cos(th)), v_scale(v, r * math.sin(th)))))
    return hit


def _cen_edge_points(e, kind, counts):
    """points along one edge, mm, in order: its start vertex, five points inside a curved edge, its end vertex.
    kind: gate_geo() of its curve, read by the caller. counts: where an edge that cannot be sampled is counted (None:
    the edge is read again, it was counted the first time)"""
    pts = []
    _cen_n()
    sv = e.startVertex
    if sv is not None:
        _cen_n()
        pts.append(gate_mm(sv.geometry))
    if kind != 'Line3D':
        try:
            _cen_n()
            ev = e.evaluator
            _cen_n()
            ok, p0, p1 = ev.getParameterExtents()
            for k in range(1, 6):
                _cen_n()
                ok2, q = ev.getPointAtParameter(p0 + (p1 - p0) * k / 6.0)
                if ok2:
                    pts.append(gate_mm(q))
        except Exception:
            if counts is not None:
                counts['edges_not_sampled'] = counts.get('edges_not_sampled', 0) + 1
    _cen_n()
    ve = e.endVertex
    if ve is not None:
        _cen_n()
        pts.append(gate_mm(ve.geometry))
    return pts


def _cen_cyl_open(f, g, cone):
    """(the state of one cylindrical or conical face before its edges are read, its edges)"""
    ax = g.axis
    o = gate_mm(g.origin)
    r = 0.0 if cone else g.radius * 10.0
    edges = f.edges
    ne = edges.count
    _cen_n(4 if cone else 5)
    return {'o': list(o), 'a': list(v_unit((ax.x, ax.y, ax.z))), 'r': r, 'cone': cone, 'ne': ne, 'e': 0, 'k': 0, 'lo': None, 'hi': None}, edges


def _cen_cyl_edge(st, e, counts):
    """Add one edge of a cylindrical face to its state: the least and the largest (along the axis, off the axis) of its
    points, and of its centre when it is a circle on the axis (cylinder_extent of _geom.py, one edge at a time)."""
    eg = e.geometry
    kind = gate_geo(eg)
    _cen_n(2)
    o, a = st['o'], st['a']
    circle = None
    if kind == 'Circle3D':
        _cen_n(2)
        circle = (gate_mm(eg.center), eg.radius * 10.0)
    sr = [(along(p, o, a), off_axis(p, o, a)) for p in _cen_edge_points(e, kind, counts)]
    if circle is not None and off_axis(circle[0], o, a) < 0.02:
        sr.append((along(circle[0], o, a), float(circle[1])))
    lo = None if st['lo'] is None else tuple(st['lo'])
    hi = None if st['hi'] is None else tuple(st['hi'])
    for t in sr:
        if lo is None or t < lo:
            lo = t
        if hi is None or t > hi:
            hi = t
    st['k'] += len(sr)
    st['lo'], st['hi'] = (None if lo is None else list(lo)), (None if hi is None else list(hi))


def _cen_cyl_extent(st):
    """cylinder_extent() of the face from its state: {"s0", "s1", "r", "r2"} or None"""
    if st['k'] < 2:
        return None
    lo, hi = st['lo'], st['hi']
    if hi[0] - lo[0] < 0.02:
        return None
    if st['cone']:
        return {'s0': lo[0], 's1': hi[0], 'r': lo[1], 'r2': hi[1]}
    return {'s0': lo[0], 's1': hi[0], 'r': float(st['r']), 'r2': None}


def _cen_cyl_ring(body, st, counts, partial, d_max):
    """the raw tuple of one face for stack_holes(), or None: no length, or not the wall of a hole (a step of point tests).
    partial: a list that gets one record per cylinder whose wall stands over 9 or 10 of 16 points of the circle (more than
    a half circle, so not the end of a slot, and less than the 11 a hole needs): a hole broken open, dropped from the
    stacks. The judge of census.unknown_hole asks about those of insert-pocket size."""
    ext, o, a, cone = st.get('ext'), st['o'], st['a'], st['cone']
    if ext is None:
        return None
    u, v = axis_frame(a)
    best, both_sides = 0, False
    for share in (0.5, 0.25, 0.75):                     # mid height first; the quarters only when a cross hole may have eaten the wall
        r = ext['r'] if ext['r2'] is None else ext['r'] + (ext['r2'] - ext['r']) * share
        if r < 0.15:
            continue
        eps = min(_CEN_EPS, 0.3 * r)
        c = v_add(o, v_scale(a, ext['s0'] + (ext['s1'] - ext['s0']) * share))
        hit = _cen_ring(body, c, u, v, r + eps, (0, 4, 8, 12), 16)
        if hit >= 2:
            hit += _cen_ring(body, c, u, v, r + eps, (1, 2, 3, 5, 6, 7, 9, 10, 11, 13, 14, 15), 16)
        best = max(best, hit)
        if hit >= 11:
            if _cen_ring(body, c, u, v, r - eps, (0, 3, 5, 8, 11, 13), 16) != 0:
                both_sides = True
                break                                   # material on both sides: not a hole wall
            return {'origin': [round(x, 5) for x in o], 'axis': [round(x, 8) for x in a], 'r': round(ext['r'], 5),
                    'r2': None if ext['r2'] is None else round(ext['r2'], 5), 's0': round(ext['s0'], 5), 's1': round(ext['s1'], 5),
                    'full': True, 'inner': True, 'arc': round(hit / 16.0, 3)}
        if hit < 6:
            break
    counts['outer' if best == 0 else 'partial_arcs'] = counts.get('outer' if best == 0 else 'partial_arcs', 0) + 1
    if not cone and not both_sides and 9 <= best <= 10 and 2.0 * ext['r'] <= d_max:
        end = v_add(o, v_scale(a, ext['s1']))
        partial.append({'anchor': anchor_of('hole', a, end), 'd': round(2.0 * ext['r'], 3), 'len': round(ext['s1'] - ext['s0'], 3),
                        'arc': round(best / 16.0, 3)})
    return None


def _cen_printed(path, comp_name, body_name, job):
    names = [n.rsplit(':', 1)[0] for n in (path or '').split('+') if n] + [comp_name, body_name]
    return all(gate_printed(n, job) for n in names)


def _cen_kind(name, job):
    """"hardware" (a screw, washer, bearing, nut or insert ref, by the name patterns of the parameters) or "bought", for a
    body that is not printed"""
    return 'hardware' if gate_hardware(name, job) else 'bought'


def _cen_coaxial_may(stacks, tol):
    """A test for one cylinder of a neighbour (origin, unit axis, part frame): can coaxial_cylinders() put it on the axis
    of a hole made of one of these stacks? Necessary, not sufficient: the hole's mouth is an end of its stack rounded to
    0.0001 mm and its axis that of the stack rounded to 0.0001, so a cylinder this lets through may still be no match,
    and one it holds back never is. Only the cylinders it lets through are kept: the cursor stays small."""
    ends = [(s['axis'], [v_add(s['origin'], v_scale(s['axis'], s['lo'])), v_add(s['origin'], v_scale(s['axis'], s['hi']))]) for s in stacks]
    reach = float(tol) + 0.01

    def may(origin, axis):
        ca = v_unit(axis)
        for a, pts in ends:
            if abs(v_dot(ca, a)) < 0.999:
                continue
            if min(off_axis(p, origin, ca) for p in pts) <= reach:
                return True
        return False
    return may


def _cen_floor(body, end, inwards, fmax):
    """(mm of plastic behind a blind end along the axis, capped?): 0.1 mm steps for 3 mm, then 0.5 mm steps up to fmax"""
    t = _CEN_EPS
    while t <= fmax:
        step = 0.1 if t < 3.0 else 0.5
        if not _cen_in(body, v_add(end, v_scale(inwards, t))):
            return round(max(0.0, t - step / 2.0), 2), False
        t += step
    return float(fmax), True


def _cen_hole(body, stacks, j, nb, tables, lim, counts):
    """the census record of stack j: ("hole", record), ("bore", record) or (None, None)"""
    st = stacks[j]
    o, a, spans = st['origin'], st['axis'], st['spans']

    def at(s):
        return v_add(o, v_scale(a, s))
    e_lo = min(_CEN_EPS, (spans[0]['s1'] - spans[0]['s0']) / 4.0)
    e_hi = min(_CEN_EPS, (spans[-1]['s1'] - spans[-1]['s0']) / 4.0)
    if _cen_in(body, at(st['lo'] + e_lo)) or _cen_in(body, at(st['hi'] - e_hi)):
        counts['ring_grooves'] = counts.get('ring_grooves', 0) + 1          # material on the axis: an island, not a hole
        return None, None
    h = hole_from_stack(st, not _cen_in(body, at(st['lo'] - _CEN_EPS)), not _cen_in(body, at(st['hi'] + _CEN_EPS)))
    if h is None:
        counts['closed'] = counts.get('closed', 0) + 1
        return None, None
    co = coaxial_cylinders(h['point'], h['axis'], h['depth'], nb, lim['coaxial_tol'], lim['coaxial_reach'])
    cls = classify_stack(h['segments'], h['through'], tables, [c['name'] for c in co])
    here = {'point': [round(x, 4) for x in h['point']], 'axis': [round(x, 6) for x in h['axis']]}
    if not is_hole(h['segments'], cls, lim['hole_d_max']):
        counts['bores'] = counts.get('bores', 0) + 1
        return 'bore', {'id': 'h%d' % (j + 1), 'anchor': anchor_of('hole', h['axis'], h['point']), 'segments': h['segments'],
                        'depth': h['depth'], 'through': h['through'], 'at': here}
    floor, capped, behind = None, False, None
    if not h['through']:
        inwards = v_scale(h['axis'], -1.0)
        floor, capped = _cen_floor(body, v_add(h['point'], v_scale(inwards, h['depth'])), inwards, lim['floor_max'])
        b = behind_blind_end(stacks, j, v_dot(h['axis'], a) < 0)             # the mouth is at "lo": the blind end is at "hi"
        if b:
            behind = {'hole': 'h%d' % (b['stack'] + 1), 'cap': b['cap'], 'd': b['d']}
    role = cls['role'] if cls else 'unknown'
    counts[role] = counts.get(role, 0) + 1
    counts['holes'] = counts.get('holes', 0) + 1
    return 'hole', {'id': 'h%d' % (j + 1),
                    'anchor': anchor_of('insert_pocket' if role == 'insert_pocket' else 'hole', h['axis'], h['point']),
                    'segments': h['segments'], 'depth': h['depth'], 'through': h['through'], 'floor': floor,
                    'exit': anchor_of('hole', v_scale(h['axis'], -1.0), h['exit']) if h['through'] else None,
                    'class': cls, 'coaxial': co, 'at': here, 'behind': behind, 'floor_capped': capped, 'arc': h['arc']}


def _cen_walk(body, lp, nrm, box, lim, counts):
    """The opening of one loop whose outline is read (lp: its centre "c", "shape", "size" and the outline points "s3"
    and "s4" the walk starts from): {"anchor", "shape", "size", "through", "depth", "wall"} (no id yet), or None when
    something stands on the face there or the recess is shallow. A step of point tests."""
    down = v_scale(nrm, -1.0)
    c = tuple(lp['c'])
    starts = [c] + [v_add(p, v_scale(v_sub(c, p), 0.15)) for p in lp['s3']]
    q = next((s for s in starts if not _cen_in(body, v_add(s, v_scale(down, _CEN_EPS)))), None)
    if q is None:
        counts['boss_feet'] = counts.get('boss_feet', 0) + 1                 # material under the loop: something stands on the face
        return None
    rim = []                                            # points 0.3 mm outside the outline: in the wall the opening is cut through
    for p in lp['s4']:
        d = v_sub(p, c)
        if v_len(d) > 1e-6:
            w = v_add(p, v_scale(d, 0.3 / v_len(d)))
            if _cen_in(body, v_add(w, v_scale(down, _CEN_EPS))):
                rim.append(w)
    far = max(along(p, q, down) for p in [(x, y, z) for x in (box[0][0], box[1][0]) for y in (box[0][1], box[1][1]) for z in (box[0][2], box[1][2])])
    step = max(0.25, far / 200.0)
    t, depth, wall = step, None, None
    while t <= far + step:
        if _cen_in(body, v_add(q, v_scale(down, t))):
            depth = round(t - step / 2.0, 2)            # a floor: a pocket
            break
        if rim and 2 * sum(_cen_in(body, v_add(w, v_scale(down, t))) for w in rim) <= len(rim):
            wall = round(t - step / 2.0, 2)             # the wall round the opening has ended: it is through that wall
            break
        t += step
    if depth is not None and depth < lim['opening_min_depth']:
        counts['shallow_recesses'] = counts.get('shallow_recesses', 0) + 1
        return None
    return {'anchor': anchor_of('face', nrm, c), 'shape': lp['shape'], 'size': lp['size'], 'through': depth is None,
            'depth': depth, 'wall': wall}


def _cen_walk_state(c, shape, size, s3, s4):
    return {'phase': 'walk', 'c': list(c), 'shape': shape, 'size': list(size), 's3': [list(p) for p in s3], 's4': [list(p) for p in s4]}


def _cen_memory():
    """What this Fusion process keeps between two calls of the large loops in progress: their outline points so far, by
    the key in the cursor ({"n", "sum", "K": kept points, "buf": points not thinned yet}). On a convex or finely curved
    outline hull_keep() keeps nearly every point, thousands of them: they do not go into the cursor (it is in the JOB
    line of every call and in the plan). A module object of its own in sys.modules, as the signature memory of
    _fusion.py; gone when Fusion quits, and then the loop is read again from its first edge."""
    mod = sys.modules.get('_gate_census_outline')
    if mod is None:
        mod = type(sys)('_gate_census_outline')
        mod.loops = {}
        sys.modules['_gate_census_outline'] = mod
    return mod.loops


def _cen_nb_state(b):
    """gate_body_state() of a neighbour, or when Fusion gives none, its face count and box: something a later call can
    compare. None only when nothing of it can be read."""
    s = gate_body_state(b)
    if s is not None:
        return s
    try:
        lo, hi = gate_bbox(b)
        return 'no state|%d|%s' % (b.faces.count, ','.join('%.6f' % x for x in tuple(lo) + tuple(hi)))
    except Exception:
        return None


def _cen_upgrade(old, n):
    """The cursor of the census of 0.2.0 or 0.2.1 ({"sig", "face", "cyl", "stack", "oface", "n_open", "counts", and
    "tape" / "again" of a planar face begun}) as a cursor of this version, so that a unit an older run left half done
    goes on instead of ending in an error (which would skip the units that need the census). Those versions did not note
    the planar faces with an inner loop: "pscan" says below which face step 3 looks at every face again. Their walk over
    the neighbours wrote "coaxial_skipped" in every call that measured a stack: when stacks were measured already it is
    not written again. The tape of a planar face is dropped (its counts were those of the start of that face)."""
    cyl = list(old.get('cyl') or [])
    i, j, k = int(old.get('face') or 0), int(old.get('stack') or 0), int(old.get('oface') or 0)
    cur = {'v': 2, 'sig': old.get('sig'), 'counts': dict(old.get('counts') or {}), 'n_open': int(old.get('n_open') or 0), 'planes': '0'}
    if i < n:
        cur.update(stage='faces', face=i, cyl=cyl, pscan=i)
        return cur, 'the faces (face %d)' % i
    if j < len(stack_holes(cyl)):
        cur.update(stage='near', face=n, cyl=cyl, stack=j, pscan=n)
        if j > 0:
            cur['skipped_written'] = True
        return cur, 'the stacks (stack %d)' % j
    cur.update(stage='openings', face=n, oface=k, pscan=n)
    return cur, 'the openings (face %d)' % k


def _cen_timing(sums, run, cur, gap):
    """(the running sums for the cursor, the "timing" block of the answer) after this call"""
    rows = {k: list(v) for k, v in (sums.get('rows') or {}).items()}
    for k, (s, api, pc) in run['rows'].items():
        row = rows.setdefault(k, [0.0, 0, 0])
        row[0], row[1], row[2] = round(row[0] + s, 3), row[1] + api, row[2] + pc
    faces = {k: list(v) for k, v in (sums.get('faces') or {}).items()}
    for k, (s, api, pc) in run['faces'].items():
        row = faces.setdefault(k, [0.0, 0, 0])
        row[0], row[1], row[2] = round(row[0] + s, 3), row[1] + api, row[2] + pc
    slow = sorted(faces.items(), key=lambda kv: (-kv[1][0], -kv[1][1], kv[0]))[:5]
    keep = set(k for k, _ in slow)
    if cur is not None:                                 # a face in progress keeps its sums so far
        if cur.get('fs') is not None:
            keep.add('f%d' % int(cur.get('face') or 0))
        if cur.get('of') is not None:
            keep.add('f%d' % int(cur.get('oface') or 0))
    sums = {'calls': int(sums.get('calls') or 0) + 1, 'gap': round(max(float(sums.get('gap') or 0.0), gap), 3), 'rows': rows,
            'faces': {k: v for k, v in faces.items() if k in keep}}
    block = {'calls': sums['calls'], 'gap_s': sums['gap'], 'call_gap_s': round(gap, 3),
             'stages': {k: {'s': round(rows.get(k, [0.0])[0], 2), 'api': rows.get(k, [0, 0, 0])[1], 'pc': rows.get(k, [0, 0, 0])[2]} for k in _CEN_STAGES},
             'slowest': [{'face': int(k[1:]), 's': round(v[0], 2), 'api': v[1], 'pc': v[2]} for k, v in slow]}
    return sums, block


def probe(job):
    """job  uses "doc", "component_id", "occurrence", "body", "tables" ({"inserts", "screws", "bearings"}),
         "not_printed", "cursor", "deadline_s"

    Returns {"facts": {...}, "done": bool, "cursor", "sig": gate_signature(body), "timing"} with facts
      "holes": [{"id": "h1"                       stable for one signature: stacks sorted as stack_holes() returns them
                 "anchor": {"kind": "insert_pocket" | "hole", "axis": [x, y, z], "point": [x, y, z]}
                           part frame; axis points out of the material at the mouth, point = centre of the mouth
                 "segments": [{"d": 7.5, "len": 0.54}, {"d": 4.4, "len": 1.5}, {"d": 3.6, "len": 5.0}]   from the mouth inwards; a cone has "d2"
                 "depth": 7.04, "through": false,
                 "floor": 2.1          mm of plastic behind a blind end along the axis; None for a through hole
                 "exit": anchor of the other open end of a through hole (kind "hole", axis opposite), else None
                 "class": classify_stack(segments, through, tables) -> {"table", "kind", "role", "match"} or None
                 "coaxial": [{"name": "ref insert M3x4x5", "kind": "hardware" | "bought" | "printed", "part": part id or None}]
                           bodies of OTHER components with a cylinder on the same axis (document frame compared)}]
      "openings": [{"id": "o1", "anchor": {"kind": "face", "axis": outward normal of the face the opening is in,
                    "point": centre of the opening}, "shape": "slot" | "window", "size": [w, h], "through": bool}]
               planar loops that pass through a wall and are not round
      "counts": {"holes": n, "openings": n, "insert_pocket": n, "unknown": n, ...one count per role}
    The anchor kind is "insert_pocket" when class.role is "insert_pocket", else "hole".

    More keys than the list above:
      a hole also has "at" {"point", "axis"}: the mouth and the axis NOT rounded (the anchor is rounded to 0.05 mm, too
      coarse to aim a ray), "behind" {"hole": id, "cap": mm, "d": mm} or None (the coaxial hole of this part that starts
      behind its blind end), "floor_capped" (no air within "floor_max": "floor" is that limit), "arc" (least share of the
      circle that is wall; under 1.0 the wall is broken open). Its class is classify_stack() WITH the names of its
      coaxial bodies, so a hole with an insert ref on its axis stays in the insert band.
      An id is "h" + the index of the stack + 1; ids of dropped stacks are not reused, so there are gaps.
      an opening also has "depth" (mm of air under the face before a floor; None when it is through) and "wall" (mm
      from the face to where the wall round it ends: the thickness it is cut through; None for a pocket, and when the
      air runs to the far side of the part). "through" means: no floor before the wall round the opening ends. A blind
      opening at least "opening_min_depth" deep is listed too (a nut trap), with "through": false.
      "bores": [{"id", "anchor", "segments", "depth", "through", "at"}] stacks with no class and a narrowest diameter over
      "hole_d_max"; "limits": the ranges this call worked with (job["args"] may override each); "coaxial_skipped": names
      of neighbour bodies whose faces were not read (over "neighbour_faces" faces in all), written once per unit;
      "partial": [{"anchor" (kind "hole", one end of the cylinder on its axis), "d", "len", "arc"}] cylinders whose wall
      stands over 9 or 10 sixteenths of the circle: a hole broken open by more than a hole of the census may be, and by
      less than a half (the end of a slot is a half circle and is not listed). They are in no stack;
      "counts" also has "cylinders", "partial_arcs", "outer", "closed", "ring_grooves", "bores", "boss_feet",
      "shallow_recesses", "edges_not_sampled".

    The cursor (plain JSON) is {"v": 2, "sig", "stage": "faces" | "near" | "nbfaces" | "stacks" | "openings", "counts",
    "n_open", "timing" (running sums), and what the stage in progress needs: "face" (next face), "fs" (a face whose
    edges are being read: the least and largest points so far), "cyl" (the hole walls found), "planes" (hex: the faces
    step 3 reads), "near" (the walk over the occurrences: next index, bodies found), "nb" (the neighbours chosen, the
    one being read and its next face, the cylinders kept, the state and place of every neighbour read so far), "stack" (next
    stack), "oface" / "of" (the planar face in progress: its outward normal, its loop in progress and that loop's pass,
    next edge, point count and running sum, and the key of its outline points in _cen_memory), "pscan" (faces below it
    are looked at again in step 3: a cursor of 0.2.0 / 0.2.1 taken over), "tape" (the point tests so far of a step cut
    inside them), "again" / "lost" / "nb_again" (calls in a row that had to start a step, a large loop or the
    neighbours again)}. Each call returns only the NEW holes, bores, openings and partial walls, and the running counts.
    A neighbour that changed between two calls (moved, or the part moved; edited, removed, its face count) starts the
    walk over the neighbours again, with a note; the third such call in a row ends the unit in an error (a call that
    goes on with what the call before read ends the row). A neighbour whose state Fusion cannot give is joined over the
    calls with a note that says so. A cursor of the census of 0.2.0 / 0.2.1 is taken over where it stopped
    (_cen_upgrade), with a note; a cursor of a later version ends the unit in an error.
    A part whose signature is not job["sig"] any more is not measured: {"stale": true}, "done" false, the new "sig".
    With no job["sig"], a body that changed between two calls raises.
    job["args"]: any key of "limits", {"limit": n}: at most n steps per call, and {"tape_limit": n}: at most n point
    tests per call (tests, slow machines).

    "timing" goes beside the facts (no judge reads it), and one line of it into the notes: {"calls", "gap_s": the longest
    stretch of any call so far without a look at the clock (gate_clock_gap of _fusion.py), "call_gap_s": that of this
    call, "stages": {"prelude" | "faces" | "neighbours" | "state" | "stacks" | "openings" | "shape": {"s": seconds, "api":
    Fusion API reads, "pc": pointContainment calls}} summed over the calls ("state": reading the state of the
    neighbours, "shape": the Python that sizes a large loop), "slowest": the five faces of the part that took the most
    seconds, [{"face": index, "s", "api", "pc"}], "cursor_bytes": the size of the cursor this call returned (0 when
    done)}."""
    global _CEN_TAPE, _CEN_RUN
    t0 = time.time()
    _CEN_RUN = {'stage': 'prelude', 't': t0, 'rows': {}, 'face': None, 'tf': t0, 'faces': {}}
    try:
        return _cen_probe(job)
    finally:
        _CEN_TAPE = None
        _CEN_RUN = None


def _cen_probe(job):
    global _CEN_TAPE
    design = gate_design(job)
    occ, comp, body = gate_body(design, job)
    sig = gate_signature(body)
    came = job.get('cursor') or {}
    if job.get('sig') and job['sig'] != sig:            # the part changed since it was scanned: the host scans again
        return {'facts': {'stale': True}, 'done': False, 'cursor': job.get('cursor'), 'sig': sig}
    if came and (not isinstance(came, dict) or came.get('sig') != sig):
        raise ValueError('the body changed between two calls of one census: start the unit again')
    if came and 'v' not in came and 'face' in came:     # a unit the census of 0.2.0 / 0.2.1 left half done: go on from there
        _cen_n(2)
        came, where = _cen_upgrade(came, body.faces.count)
        _GATE_NOTES.append('a cursor of the census before 0.2.2 was taken over at %s; the planar faces are looked at again '
                           'in the openings stage' % where)
    if came and came.get('v') != 2:
        raise ValueError('this cursor was written by another version of the census: start the unit again')
    cur = json.loads(json.dumps(came)) if came else {'v': 2, 'sig': sig, 'stage': 'faces', 'face': 0, 'cyl': [], 'counts': {}, 'n_open': 0}
    sums = cur.pop('timing', None) or {}
    args = job.get('args') or {}
    lim = dict(_CEN_LIMITS)
    lim.update({k: args[k] for k in _CEN_LIMITS if k in args})
    counts = cur['counts']
    out = {'holes': [], 'bores': [], 'openings': [], 'partial': [], 'skipped': None}
    limit = args.get('limit')                           # at most this many steps per call (tests, slow machines)
    work = [0]                                          # steps finished in this call
    tape = _CEN_TAPE = GateTape(job, reserve=_CEN_RESERVE)

    def late():
        """Must this call stop before its next step? Never before it has finished one."""
        if work[0] <= 0:
            return False
        if limit is not None and work[0] >= int(limit):
            return True
        if tape.limit is not None and tape.fresh >= tape.limit:
            return True
        return gate_time_up(job, _CEN_RESERVE)

    def taped(what, fn, *a):
        """Run one step of point tests through the tape: from the tape of the cursor when the step was begun, from its
        start when that tape does not fit; a step cut by the clock leaves its counts as they were and its tape behind."""
        saved = cur.pop('tape', None)
        before, n_partial = dict(counts), len(out['partial'])
        try:
            try:
                tape.begin(saved)
                got = fn(*a)
                tape.end()
            except (GateReplay, ValueError):            # not the questions the tape answers: this step from its start
                if saved is None:
                    raise
                cur['again'] = gate_again(cur.get('again'), what)
                counts.clear()
                counts.update(before)
                del out['partial'][n_partial:]
                tape.begin(None)
                got = fn(*a)
                tape.end()
        except GateStop:
            counts.clear()
            counts.update(before)
            del out['partial'][n_partial:]
            cur['tape'] = tape.saved()
            raise _CenStop()
        cur.pop('again', None)
        return got

    def restart_neighbours(why):
        cur['nb_again'] = gate_again(cur.get('nb_again'), 'the walk over the bodies round the part', GATE_MOVED)
        cur.pop('near', None)
        cur.pop('nb', None)
        cur['stage'] = 'near'
        note = 'the bodies round the part changed between two calls of this unit (%s): the walk over them starts again' % why
        if note not in _GATE_NOTES:
            _GATE_NOTES.append(note)

    # ---- 1. the faces of the part: walls of holes, and the planar faces with an inner loop ----
    def stage_faces():
        _cen_stage('faces')
        faces = body.faces
        n = faces.count
        _cen_n(2)
        mask = int(cur.get('planes') or '0', 16)
        i, fs = int(cur.get('face') or 0), cur.get('fs')
        try:
            while i < n:
                edges = None
                if fs is None:
                    if late():
                        raise _CenStop()
                    _cen_face('f%d' % i)
                    f = faces.item(i)
                    g = f.geometry
                    kind = gate_geo(g)                  # exactly: an EllipticalCylinder has no radius and is no hole wall
                    _cen_n(3)
                    if kind == 'Plane':
                        _cen_n(2)
                        if f.loops.count >= 2:
                            mask |= 1 << i
                    if kind not in ('Cylinder', 'Cone'):
                        i += 1
                        work[0] += 1
                        continue
                    counts['cylinders'] = counts.get('cylinders', 0) + 1
                    fs, edges = _cen_cyl_open(f, g, kind == 'Cone')
                else:
                    _cen_face('f%d' % i)
                big = fs['ne'] > _CEN_SMALL                 # a face of many edges: one edge per step
                if fs['e'] < fs['ne'] and edges is None:    # a face an earlier call began
                    _cen_n(2)
                    edges = faces.item(i).edges
                while fs['e'] < fs['ne']:
                    if big and late():
                        raise _CenStop()
                    _cen_n()
                    _cen_cyl_edge(fs, edges.item(fs['e']), counts)
                    fs['e'] += 1
                    if big:
                        work[0] += 1
                if 'ext' not in fs:
                    fs['ext'] = _cen_cyl_extent(fs)
                if big and late():
                    raise _CenStop()
                c = taped('the wall test of face %d' % i, _cen_cyl_ring, body, fs, counts, out['partial'], lim['hole_d_max'])
                if c:
                    cur['cyl'].append(c)
                fs = None
                i += 1
                work[0] += 1
        finally:
            _cen_face(None)
            cur['face'], cur['fs'], cur['planes'] = i, fs, '%x' % mask

    # ---- 2a. the bodies near the part: the walk over the occurrences ----
    def stage_near():
        _cen_stage('neighbours')
        root = design.rootComponent
        occs = root.allOccurrences
        n_occ = occs.count
        _cen_n(3)
        st = cur.get('near')
        if st is not None:
            same = st.get('n') == n_occ
            if same and int(st['o']) >= 2:
                _cen_n(2)
                same = occs.item(int(st['o']) - 2).fullPathName == st.get('last')
            if not same:
                restart_neighbours('the occurrences')
                st = None
        if st is None:
            st = {'o': 0, 'n': n_occ, 'last': None, 'cand': []}
        own, m_own = None, IDENTITY
        if occ is not None:
            _cen_n(3)
            own, m_own = occ.fullPathName, gate_matrix(occ)
        inv = mat_inverse(m_own)
        lo, hi = gate_bbox(body)
        own_body = body.name
        _cen_n(4)
        plo, phi = box_transform(m_own, lo, hi)
        gate_read((plo, phi), 2.0)                      # every body this walk can choose lies within 2 mm of the part's box
        try:
            while int(st['o']) <= n_occ:
                if late():
                    raise _CenStop()
                k = int(st['o'])
                if k == 0:
                    o, cmp_, path = None, root, None
                else:
                    _cen_n(3)
                    o = occs.item(k - 1)
                    cmp_, path = o.component, o.fullPathName
                bodies = cmp_.bRepBodies
                n_bodies_all = bodies.count
                _cen_n(2)
                solids = []
                for bi in range(n_bodies_all):
                    _cen_n(2)
                    b = bodies.item(bi)
                    if b.isSolid:
                        solids.append((bi, b))
                if solids:
                    m = gate_matrix(o)
                    _cen_n(3 if o is not None else 1)
                    cname = cmp_.name
                    rows = []
                    for bi, b in solids:
                        _cen_n(2)
                        on, bn = b.isLightBulbOn, b.name
                        rows.append((bi, b, bn, bool(on) and _cen_printed(path, cname, bn, job)))
                    _cen_n(2)
                    n_bodies = len(solids) + cmp_.meshBodies.count
                    n_printed = sum(1 for r in rows if r[3])
                    for bi, b, bn, is_part in rows:
                        if path == own and bn == own_body:
                            continue
                        _cen_n(3)
                        blo, bhi = gate_bbox(b)
                        dlo, dhi = box_transform(m, blo, bhi)
                        if not boxes_near(dlo, dhi, plo, phi, 2.0):
                            continue
                        if is_part:
                            name = part_name(cname, bn, n_printed)
                            kind, pid = 'printed', '%s/%s' % (job.get('doc_key'), part_slug(name))
                        else:
                            name = cname if n_bodies == 1 else '%s / %s' % (cname, bn)
                            kind, pid = _cen_kind(name, job), None
                        _cen_n(2)
                        st['cand'].append({'o': k, 'b': bi, 'path': path, 'body': bn, 'faces': b.faces.count, 'name': name,
                                           'kind': kind, 'part': pid, 'at': state_digest(list(mat_mul(inv, m)))})
                st['last'] = path
                st['o'] = k + 1
                work[0] += 1
        finally:
            cur['near'] = st
        cand = sorted(st['cand'], key=lambda c: (c['faces'], c['name']))     # smallest bodies first
        used, chosen, skipped = 0, [], []
        for c in cand:
            if used + c['faces'] > lim['neighbour_faces']:
                skipped.append(c['name'])
                continue
            used += c['faces']
            chosen.append(c)
        cur.pop('near', None)
        cur['nb'] = {'cand': chosen, 'i': 0, 'f': 0, 'cyls': [], 'states': {}, 'skipped': sorted(set(skipped))}
        cur['stage'] = 'nbfaces'

    # ---- 2b. the faces of the neighbours: their cylinders that may lie on the axis of a stack ----
    def stage_nbfaces(stacks):
        _cen_stage('neighbours')
        nbs = cur['nb']
        chosen = nbs['cand']
        root = design.rootComponent
        occs = root.allOccurrences
        _cen_n(2)
        if occ is not None:
            _cen_n(2)
        inv = mat_inverse(gate_matrix(occ))

        def fetch(c):
            """(the body, its frame -> the part frame), or (None, None) when it is not the body the walk chose, or not
            where it was then (the part or the neighbour moved)"""
            try:
                if int(c['o']) == 0:
                    o, cmp_, path = None, root, None
                else:
                    _cen_n(3)
                    o = occs.item(int(c['o']) - 1)
                    path, cmp_ = o.fullPathName, o.component
                _cen_n(3)
                b = cmp_.bRepBodies.item(int(c['b']))
                if path != c['path'] or b.name != c['body']:
                    return None, None
                if o is not None:
                    _cen_n(2)
                rel = mat_mul(inv, gate_matrix(o))
                return (b, rel) if state_digest(list(rel)) == c['at'] else (None, None)
            except Exception:
                return None, None

        def unchecked(name):
            note = ('the state of the body %s round the part cannot be read: its faces are joined over the calls of this '
                    'unit without a check that it stayed the same' % name)
            if note not in _GATE_NOTES:
                _GATE_NOTES.append(note)
        if nbs['states']:                               # the neighbours read so far must be what and where they were
            _cen_stage('state')
            for key in sorted(nbs['states'], key=int):
                c, was = chosen[int(key)], nbs['states'][key]
                b, _ = fetch(c)
                if b is not None and was is None:
                    unchecked(c['name'])
                if b is None or (was is not None and _cen_nb_state(b) != was):
                    _cen_stage('neighbours')
                    restart_neighbours(c['name'])
                    return
            cur.pop('nb_again', None)                   # this call goes on with what the last one read: no restart in a row
            _cen_stage('neighbours')
        may = _cen_coaxial_may(stacks, lim['coaxial_tol'])
        i, f = int(nbs['i']), int(nbs['f'])
        try:
            while i < len(chosen):
                c = chosen[i]
                b, rel = fetch(c)
                if b is not None:
                    _cen_n(2)
                    faces = b.faces
                    nf = faces.count
                if b is None or nf != c['faces']:
                    restart_neighbours(c['name'])
                    return
                if str(i) not in nbs['states']:
                    _cen_stage('state')
                    nbs['states'][str(i)] = _cen_nb_state(b)
                    if nbs['states'][str(i)] is None:
                        unchecked(c['name'])
                    _cen_stage('neighbours')
                while f < nf:
                    if late():
                        raise _CenStop()
                    _cen_n(3)
                    fc = faces.item(f)
                    g = fc.geometry
                    if gate_geo(g) == 'Cylinder':
                        _cen_n(2)
                        o_ = gate_mm(g.origin)
                        ga = g.axis
                        a_ = v_unit((ga.x, ga.y, ga.z))
                        origin, axis = mat_point(rel, o_), v_unit(mat_vector(rel, a_))
                        if may(origin, axis):
                            _cen_n(3)
                            flo, fhi = gate_bbox(fc)
                            ss = [along((x, y, z), o_, a_) for x in (flo[0], fhi[0]) for y in (flo[1], fhi[1]) for z in (flo[2], fhi[2])]
                            nbs['cyls'].append({'name': c['name'], 'kind': c['kind'], 'part': c['part'], 'origin': list(origin),
                                                'axis': list(axis), 's0': min(ss), 's1': max(ss)})
                    f += 1
                    work[0] += 1
                i += 1
                f = 0
        finally:
            if cur.get('nb') is nbs:
                nbs['i'], nbs['f'] = i, f
        if not cur.pop('skipped_written', False):      # a census before 0.2.2 wrote them already (_cen_upgrade)
            out['skipped'] = nbs['skipped']
        cur.pop('nb_again', None)
        cur['stage'] = 'stacks'

    # ---- 2c. the stacks ----
    def stage_stacks(stacks):
        _cen_stage('stacks')
        nb = (cur.get('nb') or {}).get('cyls') or []
        tables = job.get('tables') or {}
        j = int(cur.get('stack') or 0)
        try:
            while j < len(stacks):
                if late():
                    raise _CenStop()
                what, rec = taped('stack %d' % j, _cen_hole, body, stacks, j, nb, tables, lim, counts)
                if what == 'hole':
                    out['holes'].append(rec)
                elif what == 'bore':
                    out['bores'].append(rec)
                j += 1
                work[0] += 1
        finally:
            cur['stack'] = j
        for k in ('cyl', 'nb', 'stack'):
            cur.pop(k, None)
        cur['stage'] = 'openings'

    # ---- 3. the openings that are not round ----
    def stage_openings():
        _cen_stage('openings')
        faces = body.faces
        n = faces.count
        _cen_n(5)
        box = gate_bbox(body)
        mask = int(cur.get('planes') or '0', 16)
        pscan = int(cur.get('pscan') or 0)              # faces below it were not looked at by the walk of step 1 (_cen_upgrade)
        k, of = int(cur.get('oface') or 0), cur.get('of')
        try:
            while True:
                if of is None:
                    while k < n and k >= pscan and not (mask >> k) & 1:
                        k += 1
                    if k >= n:
                        break
                    if late():
                        raise _CenStop()
                    _cen_face('f%d' % k)
                    if not (mask >> k) & 1:             # a face the walk of step 1 did not see: a planar face with an inner loop?
                        _cen_n(5)
                        f = faces.item(k)
                        g = f.geometry
                        if gate_geo(g) != 'Plane' or f.loops.count < 2:
                            k += 1
                            work[0] += 1
                            continue
                        _cen_n(2)
                    else:
                        _cen_n(4)
                        f = faces.item(k)
                        g = f.geometry
                    gn = g.normal
                    nrm = v_unit((gn.x, gn.y, gn.z))
                    p0 = gate_mm(f.pointOnFace)
                    if taped('the side of face %d' % k, _cen_in, body, v_add(p0, v_scale(nrm, _CEN_EPS))):
                        nrm = v_scale(nrm, -1.0)            # the plane's own normal pointed into the material
                    of = {'nrm': list(nrm), 'L': 0, 'lp': None}
                    work[0] += 1
                    _cen_n(2)
                    loops = f.loops
                    nl = loops.count
                else:
                    _cen_face('f%d' % k)
                    _cen_n(3)
                    loops = faces.item(k).loops
                    nl = loops.count
                one_face(k, of, loops, nl, box)
                of = None
                k += 1
        finally:
            _cen_face(None)
            cur['oface'] = k
            cur['of'] = None if of is None else json.loads(json.dumps(of))

    def one_face(k, of, loops, nl, box):
        nrm = tuple(of['nrm'])
        u, v = axis_frame(nrm)
        while int(of['L']) < nl:
            L, lp, edges, mem = int(of['L']), of['lp'], None, None
            if lp is not None and lp['phase'] in ('p1', 'p2'):    # a large loop an earlier call began (p3 needs no points)
                mem = _cen_memory().get(lp.get('mem'))
                if mem is None or mem['n'] != lp['n'] or mem['sum'] != lp['sum']:
                    cur['lost'] = gate_again(cur.get('lost'), 'the outline of loop %d of face %d' % (L, k),
                                             'was not in the memory of Fusion (it restarted, or the call before was answered again)')
                    _cen_memory().pop(lp.get('mem'), None)
                    counts.clear()
                    counts.update(lp['c0'])
                    of['lp'] = lp = None                # the loop from its first edge
                else:
                    cur.pop('lost', None)
            if lp is None:
                if late():
                    raise _CenStop()
                _cen_n(2)
                lp_ = loops.item(L)
                if lp_.isOuter:
                    of['L'] = L + 1
                    work[0] += 1
                    continue
                _cen_n(2)
                edges = lp_.edges
                ne = edges.count
                if ne == 1:
                    _cen_n(3)
                    if gate_geo(edges.item(0).geometry) == 'Circle3D':
                        of['L'] = L + 1                 # a round hole: the cylinder census has it
                        work[0] += 1
                        continue
                if ne <= _CEN_SMALL:                    # a small loop: read and sized in one step
                    pts, arcs = [], 0
                    for e in range(ne):
                        _cen_n(3)
                        ed = edges.item(e)
                        kind = gate_geo(ed.geometry)
                        arcs += 0 if kind == 'Line3D' else 1
                        pts += _cen_edge_points(ed, kind, counts)
                    _cen_stage('shape')
                    shape = opening_shape(pts, nrm, arcs, ne)
                    _cen_stage('openings')
                    if shape is None or shape['round']:
                        of['L'] = L + 1
                        work[0] += 1
                        continue
                    s3, s4 = outline_picks(len(pts))
                    lp = _cen_walk_state(shape['point'], shape['shape'], shape['size'], [pts[x] for x in s3], [pts[x] for x in s4])
                else:
                    mems = _cen_memory()
                    if len(mems) > 64:                  # loops of units that never ended
                        mems.clear()
                    key = '%s|%d|%d|%s' % (sig, k, L, os.urandom(6).hex())
                    mem = mems[key] = {'n': 0, 'sum': 0, 'K': [], 'buf': []}
                    lp = {'phase': 'p1', 'mem': key, 'ne': ne, 'e': 0, 'n': 0, 'arcs': 0, 'o': None, 'last': None, 'sum': 0,
                          'c0': dict(counts)}
                of['lp'] = lp
            while lp['phase'] in ('p1', 'p2', 'p3'):    # a large loop: one edge per step, three passes at most
                if late():
                    raise _CenStop()
                if edges is None:
                    _cen_n(2)
                    edges = loops.item(L).edges
                _cen_n(3)
                ed = edges.item(int(lp['e']))
                kind = gate_geo(ed.geometry)
                if lp['phase'] == 'p1':
                    lp['arcs'] += 0 if kind == 'Line3D' else 1
                    pts = _cen_edge_points(ed, kind, counts)
                else:
                    pts = _cen_edge_points(ed, kind, None)
                _cen_stage('shape')
                verdict = outline_step(lp, mem, pts, nrm, u, v)
                _cen_stage('openings')
                lp['e'] += 1
                work[0] += 1
                if verdict is not None:                 # the loop is through its passes: its points are not needed any more
                    _cen_memory().pop(lp['mem'], None)
                if verdict == 'again':                  # the second read gave other points: the loop from its start
                    cur['again'] = gate_again(cur.get('again'), 'the outline of loop %d of face %d' % (L, k))
                    counts.clear()
                    counts.update(lp['c0'])
                    of['lp'] = None
                    lp = None
                    break
                if verdict == 'none':
                    of['L'], of['lp'], lp = L + 1, None, None
                    break
                if verdict is not None:                 # the shape is known
                    if verdict['round']:
                        of['L'], of['lp'], lp = L + 1, None, None
                        break
                    lp = _cen_walk_state(verdict['point'], verdict['shape'], verdict['size'], lp['s3'], lp['s4'])
                    of['lp'] = lp
            if lp is None:
                continue
            found = taped('the openings of face %d' % k, _cen_walk, body, lp, nrm, box, lim, counts)
            if found:
                cur['n_open'] = int(cur['n_open']) + 1
                out['openings'].append(dict(found, id='o%d' % cur['n_open']))
            of['L'], of['lp'] = L + 1, None
            work[0] += 1

    def outline_step(lp, mem, pts, nrm, u, v):
        """One edge of a large loop in its pass. lp: its state in the cursor; mem: its outline points in the memory of
        this Fusion (_cen_memory), which keeps "n" and "sum" equal to those of lp after every step. Returns None (go on),
        "again", "none" (no shape: opening_shape() gave None) or the shape (opening_shape's answer, with the walk's start
        points in lp["s3"] / lp["s4"])."""
        ph = lp['phase']
        if ph == 'p1':
            for p in pts:
                if lp['o'] is None:
                    lp['o'] = list(p)
                q = outline_q(p, lp['o'], u, v)
                mem['buf'].append(q)
                lp['last'] = list(q)
                lp['n'] += 1
                lp['sum'] = tape_sum(lp['sum'], p)
                if len(mem['buf']) >= _CEN_BUF:
                    mem['K'], mem['buf'] = hull_keep(mem['K'] + mem['buf']), []
            mem['n'], mem['sum'] = lp['n'], lp['sum']
            if lp['e'] + 1 < lp['ne']:
                return None
            mem['K'], mem['buf'] = hull_keep(mem['K'] + mem['buf']), []
            if lp['n'] < 3:
                return 'none'
            lp.update({'phase': 'p2', 'e': -1, 'i': 0, 'prev': lp['last'], 'best': None, 'pick': {}, 'sum2': 0})
            return None
        if ph == 'p2':
            s3, s4 = outline_picks(lp['n'])
            want = set(s3) | set(s4)
            for p in pts:
                q = outline_q(p, lp['o'], u, v)
                lp['best'] = rect_direction(lp['best'], lp['prev'], q, mem['K'], mem.setdefault('hint', {}))
                lp['prev'] = list(q)
                if lp['i'] in want:
                    lp['pick'][str(lp['i'])] = list(p)
                lp['i'] += 1
                lp['sum2'] = tape_sum(lp['sum2'], p)
            if lp['e'] + 1 < lp['ne']:
                return None
            if lp['i'] != lp['n'] or lp['sum2'] != lp['sum']:
                return 'again'
            if lp['best'] is None:
                return 'none'
            lp['s3'] = [lp['pick'][str(x)] for x in s3]
            lp['s4'] = [lp['pick'][str(x)] for x in s4]
            if not round_wanted(lp['arcs'], lp['ne'], lp['n']):
                return shape_of_rect(lp['best'], lp['o'], nrm, lp['arcs'], False)
            cx, cy = rect_centre(lp['best'])
            lp.update({'phase': 'p3', 'e': -1, 'mn': None, 'M': max(math.hypot(x - cx, y - cy) for x, y in mem['K'])})
            return None
        cx, cy = rect_centre(lp['best'])                # p3: the least distance from the centre, all points again
        for p in pts:
            x, y = outline_q(p, lp['o'], u, v)
            r = math.hypot(x - cx, y - cy)
            lp['mn'] = r if lp['mn'] is None or r < lp['mn'] else lp['mn']
            if not spread_round(lp['M'], lp['mn']):     # only grows from here: not round, whatever the rest says
                return shape_of_rect(lp['best'], lp['o'], nrm, lp['arcs'], False)
        if lp['e'] + 1 < lp['ne']:
            return None
        return shape_of_rect(lp['best'], lp['o'], nrm, lp['arcs'], spread_round(lp['M'], lp['mn']))

    done = False
    try:
        if cur['stage'] == 'faces':
            stage_faces()
            cur['stage'] = 'near'
        stacks = stack_holes(cur['cyl']) if cur['stage'] in ('near', 'nbfaces', 'stacks') else []
        if cur['stage'] == 'near' and not stacks:
            cur['stage'] = 'stacks'
        while cur['stage'] in ('near', 'nbfaces'):
            if cur['stage'] == 'near':
                stage_near()
            if cur['stage'] == 'nbfaces':
                stage_nbfaces(stacks)
        if cur['stage'] == 'stacks':
            stage_stacks(stacks)
        if cur['stage'] == 'openings':
            stage_openings()
        done = True
    except _CenStop:
        pass
    _cen_stage(None)
    counts['openings'] = int(cur['n_open'])
    counts.setdefault('holes', 0)
    counts.setdefault('unknown', 0)
    counts.setdefault('insert_pocket', 0)
    facts = {'holes': out['holes'], 'openings': out['openings'], 'bores': out['bores'], 'partial': out['partial'], 'counts': counts, 'limits': lim}
    if out['skipped']:
        facts['coaxial_skipped'] = out['skipped']
    sums, timing = _cen_timing(sums, _CEN_RUN, None if done else cur, gate_clock_gap())
    if not done:
        cur['timing'] = sums
    timing['cursor_bytes'] = 0 if done else len(json.dumps(cur))
    st = timing['stages']
    _GATE_NOTES.append('timing: %d call(s), longest stretch without the clock %.2f s (this call %.2f s); faces %.1f s / %d API / %d pc, '
                       'neighbours %.1f s / %d API, stacks %.1f s / %d pc, openings %.1f s / %d API / %d pc, shape %.1f s; cursor %d bytes%s' % (
                           timing['calls'], timing['gap_s'], timing['call_gap_s'], st['faces']['s'], st['faces']['api'], st['faces']['pc'],
                           st['neighbours']['s'], st['neighbours']['api'], st['stacks']['s'], st['stacks']['pc'], st['openings']['s'],
                           st['openings']['api'], st['openings']['pc'], st['shape']['s'], timing['cursor_bytes'],
                           ('; slowest face %d: %.1f s / %d API / %d pc' % (timing['slowest'][0]['face'], timing['slowest'][0]['s'],
                                                                           timing['slowest'][0]['api'], timing['slowest'][0]['pc']))
                           if timing['slowest'] else ''))
    if done:
        return {'facts': facts, 'done': True, 'cursor': None, 'sig': sig, 'timing': timing}
    return {'facts': facts, 'done': False, 'cursor': cur, 'sig': sig, 'timing': timing}
