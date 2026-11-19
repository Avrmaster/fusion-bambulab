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
  2. stack_holes() groups those faces into coaxial stacks. For each stack: an island on the axis (the outer wall of a
     ring groove) is dropped; air on the axis just past an end makes that end open; hole_from_stack() gives the mouth,
     the segments and the depth; a blind end gets the plastic behind it (walked along the axis) and what continues
     behind it (behind_blind_end). classify_stack() names it. A stack with no class whose narrowest diameter is over
     "hole_d_max" is a bore (the opening of a ring or a housing): listed under "bores", not under "holes".
  3. every inner loop of a planar face that is not a circle and has air under it is an opening (a window, a slot, a nut
     trap, a tie tunnel); a recess shallower than "opening_min_depth" is not.
Not seen: an opening in a curved face, a notch that is part of the outline of a face, a hole whose wall is no cylinder
(a modelled thread). Two coaxial cylinders with a fillet between them (no cone, no touching ends) are two holes."""

_CEN_EPS = 0.05         # mm a test point is put off a surface
_CEN_LIMITS = {'hole_d_max': 30.0, 'floor_max': 20.0, 'opening_min_depth': 1.0, 'coaxial_tol': 0.25, 'coaxial_reach': 10.0,
               'neighbour_faces': 8000}


_CEN_TAPE = None                # the GateTape of the openings of one planar face (_fusion.py); None: ask Fusion directly


def _cen_in(body, p):
    if _CEN_TAPE is not None:
        return _CEN_TAPE.ask(body, p)
    return body.pointContainment(gate_point(p)) == adsk.fusion.PointContainment.PointInsidePointContainment


def _cen_ring(body, c, u, v, r, ks, n):
    """how many of the ring points `ks` (out of n round the centre c, radius r) lie inside the body"""
    hit = 0
    for k in ks:
        th = 2.0 * math.pi * k / n
        hit += _cen_in(body, v_add(c, v_add(v_scale(u, r * math.cos(th)), v_scale(v, r * math.sin(th)))))
    return hit


def _cen_edge_points(e, counts):
    """points along one edge, mm, in order: its start vertex, five points inside a curved edge, its end vertex"""
    pts = []
    if e.startVertex is not None:
        pts.append(gate_mm(e.startVertex.geometry))
    if gate_geo(e.geometry) != 'Line3D':
        try:
            ok, p0, p1 = e.evaluator.getParameterExtents()
            for k in range(1, 6):
                ok2, q = e.evaluator.getPointAtParameter(p0 + (p1 - p0) * k / 6.0)
                if ok2:
                    pts.append(gate_mm(q))
        except Exception:
            counts['edges_not_sampled'] = counts.get('edges_not_sampled', 0) + 1
    if e.endVertex is not None:
        pts.append(gate_mm(e.endVertex.geometry))
    return pts


def _cen_cylinder(f, body, counts, partial=None, d_max=30.0):
    """the raw tuple of one face for stack_holes(), or None: not a cylinder or cone, no length, or not the wall of a hole.
    partial: a list that gets one record per cylinder whose wall stands over 9 or 10 of 16 points of the circle (more than
    a half circle, so not the end of a slot, and less than the 11 a hole needs): a hole broken open, dropped from the
    stacks. The judge of census.unknown_hole asks about those of insert-pocket size."""
    g = f.geometry
    kind = gate_geo(g)                                  # exactly: an EllipticalCylinder has no radius and is no hole wall
    cone = kind == 'Cone'
    if not cone and kind != 'Cylinder':
        return None
    counts['cylinders'] = counts.get('cylinders', 0) + 1
    a = v_unit((g.axis.x, g.axis.y, g.axis.z))
    o = gate_mm(g.origin)
    pts, circles = [], []
    for e in f.edges:
        eg = e.geometry
        if gate_geo(eg) == 'Circle3D':
            circles.append((gate_mm(eg.center), eg.radius * 10.0))
        pts += _cen_edge_points(e, counts)
    ext = cylinder_extent(o, a, 0.0 if cone else g.radius * 10.0, pts, circles, cone)
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
    if partial is not None and not cone and not both_sides and 9 <= best <= 10 and 2.0 * ext['r'] <= d_max:
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


def _cen_neighbours(design, occ, body, job, limit):
    """(cylinders of the solid bodies round this part, names of bodies left out): every other body whose box comes within
    2 mm of the part's, smallest bodies first, until `limit` faces are read. The cylinders are in the part frame."""
    root = design.rootComponent
    own = occ.fullPathName if occ is not None else None
    m_own = gate_matrix(occ)
    inv = mat_inverse(m_own)
    lo, hi = gate_bbox(body)
    plo, phi = box_transform(m_own, lo, hi)
    cand = []
    for o in [None] + list(root.allOccurrences):
        comp = o.component if o is not None else root
        path = o.fullPathName if o is not None else None
        solids = [b for b in comp.bRepBodies if b.isSolid]
        if not solids:
            continue
        m = gate_matrix(o)
        printed = [bool(b.isLightBulbOn) and _cen_printed(path, comp.name, b.name, job) for b in solids]
        n_bodies = len(solids) + comp.meshBodies.count
        for b, is_part in zip(solids, printed):
            if path == own and b.name == body.name:
                continue
            blo, bhi = gate_bbox(b)
            dlo, dhi = box_transform(m, blo, bhi)
            if not boxes_near(dlo, dhi, plo, phi, 2.0):
                continue
            if is_part:
                name = part_name(comp.name, b.name, sum(printed))
                kind, pid = 'printed', '%s/%s' % (job.get('doc_key'), part_slug(name))
            else:
                name = comp.name if n_bodies == 1 else '%s / %s' % (comp.name, b.name)
                kind, pid = _cen_kind(name, job), None
            cand.append((b.faces.count, name, kind, pid, b, mat_mul(inv, m)))
    cand.sort(key=lambda c: (c[0], c[1]))
    cyls, used, skipped = [], 0, []
    for n_faces, name, kind, pid, b, rel in cand:
        if used + n_faces > limit:
            skipped.append(name)
            continue
        used += n_faces
        for f in b.faces:
            g = f.geometry
            if gate_geo(g) != 'Cylinder':
                continue
            o_ = gate_mm(g.origin)
            a_ = v_unit((g.axis.x, g.axis.y, g.axis.z))
            flo, fhi = gate_bbox(f)
            ss = [along((x, y, z), o_, a_) for x in (flo[0], fhi[0]) for y in (flo[1], fhi[1]) for z in (flo[2], fhi[2])]
            cyls.append({'name': name, 'kind': kind, 'part': pid, 'origin': mat_point(rel, o_), 'axis': v_unit(mat_vector(rel, a_)),
                         's0': min(ss), 's1': max(ss)})
    return cyls, sorted(set(skipped))


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


def _cen_openings(body, f, box, lim, counts):
    """the openings in one planar face: [{"anchor", "shape", "size", "through", "depth", "wall"}] (no ids yet)"""
    g = f.geometry
    if gate_geo(g) != 'Plane' or f.loops.count < 2:
        return []
    nrm = v_unit((g.normal.x, g.normal.y, g.normal.z))
    if _cen_in(body, v_add(gate_mm(f.pointOnFace), v_scale(nrm, _CEN_EPS))):
        nrm = v_scale(nrm, -1.0)                        # the plane's own normal pointed into the material
    down = v_scale(nrm, -1.0)
    out = []
    for lp in f.loops:
        if lp.isOuter:
            continue
        edges = list(lp.edges)
        if len(edges) == 1 and gate_geo(edges[0].geometry) == 'Circle3D':
            continue                                    # a round hole: the cylinder census has it
        pts, arcs = [], 0
        for e in edges:
            arcs += 0 if gate_geo(e.geometry) == 'Line3D' else 1
            pts += _cen_edge_points(e, counts)
        shape = opening_shape(pts, nrm, arcs, len(edges))
        if shape is None or shape['round']:
            continue
        c = shape['point']
        starts = [c] + [v_add(p, v_scale(v_sub(c, p), 0.15)) for p in pts[::max(1, len(pts) // 3)][:3]]
        q = next((s for s in starts if not _cen_in(body, v_add(s, v_scale(down, _CEN_EPS)))), None)
        if q is None:
            counts['boss_feet'] = counts.get('boss_feet', 0) + 1             # material under the loop: something stands on the face
            continue
        rim = []                                        # points 0.3 mm outside the outline: in the wall the opening is cut through
        for p in pts[::max(1, len(pts) // 4)][:4]:
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
                depth = round(t - step / 2.0, 2)        # a floor: a pocket
                break
            if rim and 2 * sum(_cen_in(body, v_add(w, v_scale(down, t))) for w in rim) <= len(rim):
                wall = round(t - step / 2.0, 2)         # the wall round the opening has ended: it is through that wall
                break
            t += step
        if depth is not None and depth < lim['opening_min_depth']:
            counts['shallow_recesses'] = counts.get('shallow_recesses', 0) + 1
            continue
        out.append({'anchor': anchor_of('face', nrm, c), 'shape': shape['shape'], 'size': shape['size'], 'through': depth is None,
                    'depth': depth, 'wall': wall})
    return out


def probe(job):
    """job  uses "doc", "component_id", "occurrence", "body", "tables" ({"inserts", "screws", "bearings"}),
         "not_printed", "cursor", "deadline_s"

    Returns {"facts": {...}, "done": bool, "cursor", "sig": gate_signature(body)} with facts
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
      of neighbour bodies whose faces were not read (over "neighbour_faces" faces in all);
      "partial": [{"anchor" (kind "hole", one end of the cylinder on its axis), "d", "len", "arc"}] cylinders whose wall
      stands over 9 or 10 sixteenths of the circle: a hole broken open by more than a hole of the census may be, and by
      less than a half (the end of a slot is a half circle and is not listed). They are in no stack;
      "counts" also has "cylinders", "partial_arcs", "outer", "closed", "ring_grooves", "bores", "boss_feet",
      "shallow_recesses", "edges_not_sampled".
    The cursor is an object {"sig", "face", "cyl", "stack", "oface", "n_open", "counts"}; each call returns only the NEW
    holes, bores and openings and the running counts. The clock is read every 4 cylinder faces, before the walk over
    the neighbours, before every stack, and every 32 faces of the openings. One planar face can hold many openings,
    each walked down in up to 200 steps of five point tests: there the point tests go through a GateTape of _fusion.py,
    and a face that runs out of time is in the cursor as "tape" (its answers so far), with "oface" still on that face.
    A part whose signature is not job["sig"] any more is not
    measured: {"stale": true}, "done" false, the new "sig". With no job["sig"], a body that changed between two calls
    raises.
    job["args"]: any key of "limits", {"limit": n}: at most n faces, stacks or planar faces per call, and
    {"tape_limit": n}: stop after n point tests of the openings in one call (tests, slow machines)."""
    design = gate_design(job)
    occ, comp, body = gate_body(design, job)
    sig = gate_signature(body)
    cur = job.get('cursor') or {}
    if job.get('sig') and job['sig'] != sig:            # the part changed since it was scanned: the host scans again
        return {'facts': {'stale': True}, 'done': False, 'cursor': job.get('cursor'), 'sig': sig}
    if cur and cur.get('sig') != sig:
        raise ValueError('the body changed between two calls of one census: start the unit again')
    args = job.get('args') or {}
    lim = dict(_CEN_LIMITS)
    lim.update({k: args[k] for k in _CEN_LIMITS if k in args})
    counts = dict(cur.get('counts') or {})
    cyl = list(cur.get('cyl') or [])
    faces = body.faces
    n = faces.count
    holes, bores, openings, skipped, partial = [], [], [], [], []

    def result(face, stack, oface, n_open, done, tape=None):
        counts['openings'] = n_open
        counts.setdefault('holes', 0)
        counts.setdefault('unknown', 0)
        counts.setdefault('insert_pocket', 0)
        facts = {'holes': holes, 'openings': openings, 'bores': bores, 'partial': partial, 'counts': counts, 'limits': lim}
        if skipped:
            facts['coaxial_skipped'] = skipped
        state = None if done else {'sig': sig, 'face': face, 'cyl': cyl, 'stack': stack, 'oface': oface, 'n_open': n_open, 'counts': counts}
        if state is not None and tape is not None:
            state['tape'] = tape                         # the planar face "oface" was begun: its point tests so far
        return {'facts': facts, 'done': done, 'cursor': state, 'sig': sig}

    limit = args.get('limit')                           # at most this many faces, stacks or planar faces per call (tests, slow machines)
    i = i0 = int(cur.get('face') or 0)                  # 1. the cylindrical and conical faces
    while i < n:
        if i > i0 and (((i - i0) % 4 == 0 and gate_time_up(job, 2.5)) or (limit is not None and i - i0 >= int(limit))):
            return result(i, 0, 0, 0, False)
        c = _cen_cylinder(faces.item(i), body, counts, partial, lim['hole_d_max'])
        if c:
            cyl.append(c)
        i += 1
    stacks = stack_holes(cyl)                           # 2. the stacks
    j = j0 = int(cur.get('stack') or 0)
    n_open = int(cur.get('n_open') or 0)
    if j < len(stacks):
        if i > i0 and gate_time_up(job, 5.0):           # the walk over the faces of the neighbours is one piece: not at the end of a call
            return result(n, j, 0, n_open, False)
        nb, skipped = _cen_neighbours(design, occ, body, job, lim['neighbour_faces'])
        tables = job.get('tables') or {}
        while j < len(stacks):
            if j > j0 and (gate_time_up(job, 2.5) or (limit is not None and j - j0 >= int(limit))):
                return result(n, j, 0, n_open, False)
            what, rec = _cen_hole(body, stacks, j, nb, tables, lim, counts)
            if what == 'hole':
                holes.append(rec)
            elif what == 'bore':
                bores.append(rec)
            j += 1
    box = gate_bbox(body)                               # 3. the openings that are not round
    k = k0 = int(cur.get('oface') or 0)
    global _CEN_TAPE
    tape = _CEN_TAPE = GateTape(job, reserve=2.5)
    saved = cur.get('tape')                             # the planar face an earlier call began
    again = 0                                           # calls in a row that had to start that face again
    try:
        while k < n:
            if k > k0 and (((k - k0) % 32 == 0 and gate_time_up(job, 2.5)) or tape.out_of_time() or (limit is not None and k - k0 >= int(limit))):
                return result(n, len(stacks), k, n_open, False)
            before = dict(counts)                       # a face that is cut by the clock has counted nothing
            mine = saved if k == k0 else None
            try:
                try:
                    tape.begin(mine)
                    found = _cen_openings(body, faces.item(k), box, lim, counts)
                    tape.end()
                except (GateReplay, ValueError):        # not the questions the tape answers: this face from its start
                    if mine is None:
                        raise
                    again = gate_again(cur.get('again'), 'the openings of face %d' % k)
                    counts.clear()
                    counts.update(before)
                    tape.begin(None)
                    found = _cen_openings(body, faces.item(k), box, lim, counts)
                    tape.end()
            except GateStop:
                counts.clear()
                counts.update(before)
                out = result(n, len(stacks), k, n_open, False, tape.saved())
                if k == k0 and again:
                    out['cursor']['again'] = again
                return out
            for op in found:
                n_open += 1
                openings.append(dict(op, id='o%d' % n_open))
            k += 1
        return result(n, len(stacks), n, n_open, True)
    finally:
        _CEN_TAPE = None
