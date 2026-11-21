"""Is every opening, insert mouth and screw head of one part reachable: a straight path from each mouth outwards against obstacles of every kind.

Fusion-side unit. Its text is pasted behind _geom.py, _geom_rings.py and _fusion.py (gate_fusion_rpc.build_script), so
their functions are globals here and this file imports nothing from disk. No adsk import is needed: _fusion.py has it.
All work happens inside probe(job), which _fusion.run() calls. Read-only: no timeline edit, no body deleted, temporary
geometry only through adsk.fusion.TemporaryBRepManager. The API is in cm, every fact is in mm.
job["legacy"] maps "insert_pockets", "fusion_parts" and "screw_access" to the absolute path of that tool of
tools/fusion (None when it is not there): a probe that wraps one loads it inside probe() with
g = {"__file__": path}; exec(open(path, encoding="utf-8").read(), g).

How it measures (the way tools/fusion/screw_access.py does, for every opening and against every kind of body):
  path      a temporary cylinder (TemporaryBRepManager.createCylinderOrCone) from 0.05 mm outside the mouth to the reach,
            0.05 mm slimmer than the opening, intersected with a temporary copy of each body whose bounding box it can
            touch. A cut piece of more than about 2 % of the section over 1 mm is a hit: a 0.2 mm shim over a hole is seen.
  at        where the cut piece begins along the path, from its bounding box: exact for a path along x, y or z, up to
            one path radius early for a slanted one.
  share     the larger of: volume of the cut piece over the volume of the path along its stretch, and the fullest of up
            to 12 sections sampled with pointContainment (17 points each).
  profile   for a measured hit that begins within 0.5 mm of the mouth: [[mm from the mouth, share of one section]] at
            stations from the start of the hit, up to the first full section (share 0.95) or one path radius plus 1 mm
            ("full_window"). A plate flat on the mouth is full at once; a shaft lying across it covers a strip at once
            and is full within its sagitta; a body that clips the rim of the mouth and covers the whole section only
            2 mm out has a "share" of 1 and a profile that stays near 0 at the mouth.
  expected  a hit by another body that has a cylinder or cone on the path's axis (the screw or insert that belongs
            there, a mating hole), or that the census lists as coaxial. The part itself is never expected.
  mesh      a mesh body has no B-rep: its vertices inside the path, else its triangles against nine lines; with no
            mesh data its bounding box ("by_box"). Always "approx": a vertex in the path proves that the mesh is there,
            not how much it covers, and a mesh has no axis, so a mating hole in it is never "expected". The judges
            take a mesh hit for what it is: not a measurement.
Which bodies are printed and how they are named: gate_world() of _fusion.py, the one walker with the rule of the scan.
Limits: a slot or window is tested with the circle that fits into it, not with its own outline; hidden bodies are
tested like visible ones and reported as "hidden"."""



def _opn_inside(rec):
    body, lo, hi = rec['body'], rec['box'][0], rec['box'][1]
    IN = adsk.fusion.PointContainment.PointInsidePointContainment

    def inside(p):
        if p[0] < lo[0] or p[0] > hi[0] or p[1] < lo[1] or p[1] > hi[1] or p[2] < lo[2] or p[2] > hi[2]:
            return False
        return body.pointContainment(gate_point(p)) == IN
    return inside


def _opn_circles(body):
    out = []
    try:
        edges = list(body.edges)
    except Exception:                                   # a body whose edges cannot be listed has no circles to give
        return out
    for e in edges:
        try:                                            # one edge without a readable geometry (the tip of a cone, a
            g = e.geometry                              # degenerate edge of an imported body) must not end the unit
            if gate_geo(g) in ('Circle3D', 'Arc3D'):                   # exactly: an EllipticalArc3D has no radius
                out.append((gate_mm(g.center), (g.normal.x, g.normal.y, g.normal.z), g.radius * 10.0))
        except Exception:
            continue
    return out


def _opn_axes(rec):
    """[(point on the axis, unit axis, radius or None)] of every cylinder and cone face of a body, read once per call."""
    if '_axes' not in rec:
        out = []
        for f in rec['body'].faces:
            try:                                                    # a face whose geometry cannot be read gives no axis; the others still do
                g = f.geometry
                t = gate_geo(g)                                     # exactly: an elliptical cylinder has no radius
                if t in ('Cylinder', 'Cone'):
                    out.append((gate_mm(g.origin), v_unit((g.axis.x, g.axis.y, g.axis.z)), g.radius * 10.0 if t == 'Cylinder' else None))
            except Exception:
                continue
        rec['_axes'] = out
    return rec['_axes']


def _opn_mesh(rec):
    """(nodes in the document frame, triangles) of a mesh body, read once per call; ([], []) when it has no display mesh
    or is too big to walk in Python (then its bounding box stands for it)."""
    if '_mesh' not in rec:
        nodes, tris = [], []
        try:
            dm = rec['body'].displayMesh
            c, idx, m = dm.nodeCoordinatesAsDouble, dm.nodeIndices, rec['matrix']
            if len(c) <= 3 * 300000:
                nodes = [mat_point(m, (c[i] * 10.0, c[i + 1] * 10.0, c[i + 2] * 10.0)) for i in range(0, len(c), 3)]
                tris = [(idx[i], idx[i + 1], idx[i + 2]) for i in range(0, len(idx), 3)]
        except Exception:
            nodes, tris = [], []
        rec['_mesh'] = (nodes, tris)
    return rec['_mesh']


def _opn_above(rec):
    """The components above a body's own component, outermost first, as one text: "Gearbox / Motor" for a body of the
    component "Rotor" inside "Motor" inside "Gearbox"; "" for a body of a top-level component. The names come from
    the occurrence objects (gate_above: "above" of the record), not from the text of the path, so a component named
    "clamp + foot" or "shaft: pin + sleeves" is one name. A hit carries it as "in" when it is not empty: a body of a
    bought or linked assembly is then named with its place ("Gearbox / Motor / Rotor"), not by a bare component name."""
    return ' / '.join(str(n) for n in (rec.get('above') or []))


_OPN_FULL_PAST = 1.0          # mm past one path radius in front of a mouth that "full_at" is looked for
_OPN_FULL_AT = (0.02, 0.1, 0.2, 0.3, 0.45, 0.6, 0.8, 1.0, 1.3, 1.7, 2.2, 2.8, 3.5, 4.5, 6.0, 8.0, 10.0)   # mm past the start of a hit


class _OpnLate(Exception):
    """The time of the call is used up inside a path. state = {"k": the index in `world` of the next body, "hits": the
    hits of the bodies before it}; probe() adds the name of the path and keeps it in the cursor."""

    def __init__(self, state):
        Exception.__init__(self, 'out of time inside a path')
        self.state = state


def _opn_path(point, axis, r, reach, world, skip, coax_names, part=None, clock=None, seen=None, mesh_by_box=False):
    """PATH of the contract: one straight cylinder against every body of `world` but those in `skip` (ids).
    One body is one step: a cut of a temporary cylinder with a copy of the body, and some point tests.
    clock  callable() -> True when the time of the call is used up. It is asked before every body whose box the path
           meets; on True the path stops there and raises _OpnLate with what it has.
    part   the state of such a stop ({"k", "hits"}), handed in by the call that goes on: the bodies before "k" are not
           cut again. The hits of a path made over several calls are the hits of one call.
    seen   a set that gets the index of every body the path looked at (cut, sampled or read as a mesh): probe() keeps
           the state of those bodies in the cursor of a feature in progress.
    mesh_by_box  a mesh body is met by its bounding box only, its nodes are not walked (the swing of a key: twelve paths per
           head beside a large STL ref would walk its nodes twelve times; a mesh hit is never a measurement anyway)."""
    TB = adsk.fusion.TemporaryBRepManager.get()
    rr = max(0.05, r - 0.05)
    start = v_add(point, v_scale(axis, 0.05))
    length = max(0.1, reach - 0.05)
    end = v_add(start, v_scale(axis, length))
    section = math.pi * rr * rr
    gate_read(path_box(start, axis, rr, length), 0.0)  # a body this path can meet lies in its box (box_overlaps_path)
    hits = [dict(h) for h in part['hits']] if part else []
    for k in range(int(part['k']) if part else 0, len(world)):
        rec = world[k]
        if id(rec) in skip or not box_overlaps_path(rec['box'][0], rec['box'][1], start, axis, rr, length):
            continue
        if clock is not None and clock():
            raise _OpnLate({'k': k, 'hits': hits})
        if seen is not None:
            seen.add(k)
        if rec['mesh']:
            nodes, tris = _opn_mesh(rec) if not mesh_by_box else (None, None)
            if nodes:
                got = mesh_blocks_path(nodes, tris, start, axis, rr, length)
            else:
                got = (round(max(0.0, box_span(rec['box'][0], rec['box'][1], start, axis)[0]), 2), 1.0)
            if got is not None:
                hits.append({'name': rec['name'], 'kind': 'mesh', 'part': None, 'at': round(got[0] + 0.05, 2), 'share': got[1],
                             'approx': True, 'expected': False, 'by_box': not nodes})
                if _opn_above(rec):
                    hits[-1]['in'] = _opn_above(rec)
            continue
        if '_inside' not in rec:
            rec['_inside'] = _opn_inside(rec)
        approx, why = False, None
        try:
            if '_copy' not in rec:                                      # the tool body of a boolean is not changed: one copy per call
                rec['_copy'] = TB.copy(rec['body'])
            x = TB.createCylinderOrCone(gate_point(start), rr / 10.0, gate_point(end), rr / 10.0)
            TB.booleanOperation(x, rec['_copy'], adsk.fusion.BooleanTypes.IntersectionBooleanType)
            vol = x.volume * 1000.0 if x.faces.count else 0.0
            if vol < min(0.3, 0.02 * section):
                continue
            lo, hi = box_span(gate_bbox(x)[0], gate_bbox(x)[1], start, axis)
            at, to = max(0.0, lo), min(length, hi)
            share = max(vol / (section * max(to - at, 0.05)),
                        path_share(rec['_inside'], start, axis, rr, at, to, min(12, max(3, int(math.ceil((to - at) / 0.25))))))
        except Exception as e:                                          # a boolean that fails on imported geometry must not end the unit:
            approx, why = True, ('%s: %s' % (type(e).__name__, e))[:120]   # the body is sampled with points instead, and the hit says so
            lo, hi = box_span(rec['box'][0], rec['box'][1], start, axis)
            a0, a1 = max(0.0, lo), min(length, hi)
            n_st = int(min(40, max(4, math.ceil((a1 - a0) / 1.0))))
            first = first_hit(lambda t: 'in' if path_share(rec['_inside'], start, axis, rr, t, min(a1, t + 0.5), 2) > 0.0 else None,
                              a0, max(a0 + 0.05, a1), max(0.25, (a1 - a0) / n_st), 0.05) if a1 > a0 else None
            if first is None:
                continue
            at, to = first[1], a1
            share = path_share(rec['_inside'], start, axis, rr, at, to, min(12, max(3, int(math.ceil((to - at) / 0.25)))))
            if share <= 0.0:
                continue
        expected = (not rec['own']) and (rec['name'] in coax_names or any(coaxial(point, axis, o, a, 0.15) for o, a, _ in _opn_axes(rec)))
        hit = {'name': rec['name'], 'kind': body_kind(rec['printed'], rec['hardware'], rec['visible'], False, rec['own']),
               'part': rec['part'], 'at': round(at + 0.05, 2), 'share': round(min(1.0, share), 3), 'approx': approx, 'expected': expected}
        if not approx and at <= 0.5:
            # how the section fills in front of the mouth: [distance from the mouth, share] at _OPN_FULL_AT past the start
            # of the hit, up to the first full section, looked for up to one path radius plus _OPN_FULL_PAST. A plate flat
            # on the mouth is full at once, a shaft or rod lying across it covers a strip at once and is full within its
            # sagitta, a body that only clips the rim covers a sliver and is full far out or never. "full_window" says
            # how far was looked
            win = min(length, rr + _OPN_FULL_PAST)
            try:
                prof = []
                for t in _OPN_FULL_AT:
                    s_ = at + t
                    if s_ > win:
                        break
                    got_ = path_share(rec['_inside'], start, axis, rr, s_, s_ + 1e-4, 1)
                    prof.append([round(s_ + 0.05, 2), round(min(1.0, got_), 3)])
                    if got_ >= 0.95:
                        break
                hit['profile'], hit['full_window'] = prof, round(win + 0.05, 2)
            except Exception:
                hit.pop('profile', None); hit.pop('full_window', None)  # not measured: the judge keeps "share"
        if why:
            hit['by_points'] = why
        if _opn_above(rec) and not rec['own']:
            hit['in'] = _opn_above(rec)
        hits.append(hit)
    hits.sort(key=lambda h: (h['at'], h['name']))
    return {'r': round(r, 3), 'reach': reach, 'free': path_free(hits, reach), 'hits': hits}


_OPN_WALK = None                # the box the walk of this call covers (part box and its margin): probe() sets it


def _opn_slab(end, Ad, floor):
    """The box of what _opn_closed() can find behind a cap: bodies whose box spans [floor, floor + 3] mm behind the blind
    end along the axis, within the walk. Exact for an axis along x, y or z; the whole walk for any other axis."""
    lo, hi = list(_OPN_WALK[0]), list(_OPN_WALK[1])
    for i in range(3):
        if abs(Ad[i]) > 0.999999:
            a, b = end[i] - Ad[i] * floor, end[i] - Ad[i] * (floor + 3.0)
            lo[i], hi[i] = max(lo[i], min(a, b)), min(hi[i], max(a, b))
            if lo[i] > hi[i]:
                lo[i] = hi[i] = (a + b) / 2.0
    return (tuple(lo), tuple(hi))


def _opn_closed(h, holes, m, world, cap_max, screws):
    """The "closed" row of a blind hole, or None: a cap of at most cap_max with something on the same axis behind it."""
    floor = h.get('floor')
    if h.get('through') or floor is None or floor > cap_max:
        return None
    P, A, depth = tuple(h['anchor']['point']), tuple(h['anchor']['axis']), float(h.get('depth') or 0.0)
    behind = [v_sub(P, v_scale(A, depth + floor + 0.1 + 0.5 * k)) for k in range(6)]        # part frame, behind the cap
    for h2 in holes:
        if h2 is h:
            continue
        P2, A2, d2 = tuple(h2['anchor']['point']), tuple(h2['anchor']['axis']), float(h2.get('depth') or 0.0)
        if coaxial(P, A, P2, A2, 0.15) and any(-d2 - 0.05 <= along(q, P2, A2) <= 0.05 for q in behind):
            ds = [s['d'] for s in h2.get('segments') or [] if 'd' in s]
            return {'hole': h.get('id'), 'anchor': h['anchor'], 'cap': floor, 'behind': {'what': 'hole', 'name': h2.get('id'), 'd': min(ds) if ds else None}}
    Pd, Ad = mat_point(m, P), v_unit(mat_vector(m, A))
    end = v_sub(Pd, v_scale(Ad, depth))                                                      # the blind end, document frame
    if _OPN_WALK is not None:
        gate_read(_opn_slab(end, Ad, floor), 0.0)
    for rec in world:
        if rec['own'] or rec['mesh']:
            continue
        lo, hi = box_span(rec['box'][0], rec['box'][1], end, v_scale(Ad, -1.0))
        if hi < floor or lo > floor + 3.0:
            continue
        radii = [r for o, a, r in _opn_axes(rec) if coaxial(Pd, Ad, o, a, 0.15)]
        if not radii:
            continue
        rs = [r for r in radii if r is not None]
        is_screw = gate_search(screws, rec['comp']) or gate_search(screws, rec['body_name'])
        return {'hole': h.get('id'), 'anchor': h['anchor'], 'cap': floor,
                'behind': {'what': 'screw' if is_screw else 'ref', 'name': rec['name'], 'd': round(2 * min(rs), 2) if rs else None}}
    return None


def _opn_within(path):
    """The "inside" list of a slot or window from the path that runs back into it: one row per other body,
    [{"name", "kind", "approx"}], sorted by name. "approx" is true for a mesh and for a body whose cut failed (it was
    sampled with points): such a body proves nothing about what the opening is for."""
    rows = {}
    for h in path['hits']:
        if h['kind'] == 'self':
            continue
        approx = bool(h.get('approx') or h.get('by_box') or h.get('by_points') or h['kind'] == 'mesh')
        old = rows.get(h['name'])
        if old is None or (old['approx'] and not approx):
            rows[h['name']] = {'name': h['name'], 'kind': h['kind'], 'approx': approx}
    return [rows[k] for k in sorted(rows)]


def _opn_blocks(hits):
    """What blocks one swing direction, for the judge to sort as it sorts every path (access.screw_path): each body
    once, nearest hit first, [{"name", "kind", "at", "share", "approx", "part"}]. "approx" is true for a mesh, a box and
    a body sampled with points: such a hit proves nothing."""
    rows = {}
    for h in sorted(hits, key=lambda h: h.get('at') or 0.0):
        if h['name'] in rows:
            continue
        rows[h['name']] = {'name': h['name'], 'kind': h.get('kind'), 'at': h.get('at'), 'share': h.get('share'), 'part': h.get('part'),
                           'approx': bool(h.get('approx') or h.get('by_box') or h.get('by_points') or h.get('kind') == 'mesh')}
    return sorted(rows.values(), key=lambda r: (r['at'] if isinstance(r['at'], (int, float)) else 0.0, r['name']))


def _opn_small(rec):
    """Can this body be a screw by its size alone? At most 80 faces, and the two shorter sides of its box at most 40 mm.
    Only a filter for cost: the shape decides (_opn_screws)."""
    d = sorted(rec['box'][1][i] - rec['box'][0][i] for i in range(3))
    try:
        return d[1] <= 40.0 and rec['body'].faces.count <= 80
    except Exception:
        return False


def _opn_swing_frame(params):
    """How the swing of an L key over a screw head is looked at: its long arm (access.key_long_arm) lies square to the
    screw axis, access.key_short_arm above the top of the head (the short arm stands in the socket), as a path of radius
    access.key_side_clearance / 2, in _OPN_SWING_N directions round the axis."""
    return {'h': float(params.get('access.key_short_arm', 20.0)), 'reach': float(params.get('access.key_long_arm', 60.0)),
            'r': max(0.5, float(params.get('access.key_side_clearance', 5.0)) / 2.0), 'n': _OPN_SWING_N}


_OPN_SWING_N = 12               # directions of the key swing round a head: every 30 degrees


def _opn_screws(world, pattern, washers, part_box, own, job, holes, doc_pt):
    """(screws, not_screws, cut): the screw refs near the part.

    A screw is a turned body (one body, or a head body and a shank body on one axis in one occurrence) with a head.
      - a ref whose own name or whose component's name matches the screw pattern: the name is a hint, the shape
        decides, by the rule fusion_screws uses (screw_claim). A name that carries a thread while the shank measures
        another one, or a component name alone over a body that is no screw of the table, is NOT a screw: it is
        listed in not_screws;
      - a ref with any other name is a screw when shank AND head are a row of the screws table (a head body and a
        shank body called "cap head" and "shank" are found without any pattern). Only small bodies are looked at
        (_opn_small), and only while a third of the time budget (at least 3 s) is left: `cut` says the look was cut short.
    A body whose own name says washer, insert, nut or bearing, a set screw (no head) and a mesh body are left out.
    screws  [{"n", "name", "recs", "shape", "size", "head", "by": "name" | "shape", "holes", "seated": bool, "under"}],
            n = the place in the sorted list of all of them, so "s<n>" stays the same from call to call;
            "holes": the census holes of this part it lies in;
            "seated": this part answers for its head and gets the opening "s<n>": the head sits on it, or the screw
            lies in a hole of it and what is under the head is no printed part (a washer over a relief, a bought
            part, nothing found). A screw is judged on the printed part under its head, else on the printed part it
            screws into;
            "under": {"name", "kind", "part"} of the other body under the head when the screw lies in a hole of this
            part and its head does not sit on it, else None."""
    by_path, pats, tables = {}, gate_ref_patterns(job), (job.get('tables') or {}).get('screws') or {}
    budget = float(job.get('deadline_s') or 20.0)
    cut = False
    far = []
    for r in world:
        if r['mesh'] or r['own']:
            continue
        kind = next((k for k in ('washer', 'insert', 'nut', 'screw', 'bearing') if gate_search(pats[k], r['body_name'])), None)
        if kind not in (None, 'screw') or gate_search(washers, r['body_name']):
            continue                                                    # its own name says what it is, and that is no screw
        named = 2 if kind == 'screw' else (1 if gate_search(pattern, r['body_name']) or gate_search(pattern, r['comp']) else 0)
        if not named:
            if r['printed'] or cut or not _opn_small(r):
                continue
            if gate_time_up(job, max(3.0, budget / 3.0)):
                cut = True
                continue
        if box_gap(r['box'][0], r['box'][1], part_box[0], part_box[1]) > 1.0:
            far.append((r, named))                                      # perhaps the head body of a shank that reaches the part
            continue
        by_path.setdefault(r['path'], []).append((r, named))
    gate_read(part_box, 1.0)                                            # the candidates: within 1 mm of the part
    for rows in by_path.values():                                       # ... and a body of their occurrence that touches one of them
        for r, _ in rows:
            gate_read(r['box'], 0.5)
    away = set(id(r) for r, _ in far)
    for r, named in far:                                                # a body that touches a near body of its own occurrence is looked at with it
        near = by_path.get(r['path']) or []
        if any(box_gap(r['box'][0], r['box'][1], n[0]['box'][0], n[0]['box'][1]) <= 0.5 for n in near if id(n[0]) not in away):
            near.append((r, named))
    found, not_screws = [], []
    for path in sorted(by_path):
        rows = sorted(by_path[path], key=lambda t: t[0]['body_name'])
        if gate_time_up(job, 3.0) and not all(n for _, n in rows):      # out of time: only the bodies with a screw name are read
            cut, rows = True, [t for t in rows if t[1]]
        recs = [r for r, _ in rows]
        named = [n for _, n in rows]
        try:
            circles = [_opn_circles(r['body']) for r in recs]
            profs = [revolve_profile(c) for c in circles]
            turned = [i for i, p in enumerate(profs) if p]
            groups = cluster_coaxial([(profs[i]['origin'], profs[i]['axis'], profs[i]['lo'], profs[i]['hi']) for i in turned], 0.2)
        except Exception as e:                                          # the bodies of one occurrence could not be read: the look is incomplete,
            cut = True                                                  # and "no screw in this hole" must not be read from it
            note = 'the bodies of %s could not be read as screws (%s: %s)' % (path or 'the root component', type(e).__name__, str(e)[:80])
            if note not in _GATE_NOTES:
                _GATE_NOTES.append(note)
            continue
        for g in groups:
            g = [turned[k] for k in g]
            prof = revolve_profile([c for i in g for c in circles[i]])
            sh = screw_shape(prof)
            by_name = max(named[i] for i in g)
            size, head = screw_size(sh, tables) if sh is not None else (None, None)
            name = recs[g[0]]['name']
            if by_name:                                                   # the name is a hint, the shape decides: the rule of fusion_screws
                said = next((t[0] for t in (thread_in_name(recs[i]['name']) for i in g) if t[0]), None)
                ok, _, size, head = screw_claim(sh, tables, said, by_name == 2)
                if ok and all(recs[i]['printed'] for i in g) and (sh is None or screw_size(sh, tables)[0] is None):
                    ok = False                                            # a printed body that only carries the word
                if not ok:
                    not_screws.append(name)
                    continue
                if sh is None or sh['headless']:
                    continue                                              # a set screw has no head to reach
            elif sh is None or sh['headless'] or size is None or head is None or washer_shape(prof) is not None:
                continue
            found.append({'key': '%s|%s' % (path, recs[g[0]]['body_name']), 'name': name, 'recs': [recs[i] for i in g], 'shape': sh,
                          'size': size, 'head': head, 'by': 'name' if by_name else 'shape'})
    found.sort(key=lambda s: s['key'])
    inside = own['_inside']
    for n, s in enumerate(found):
        sh = s['shape']
        u, v = axis_frame(sh['axis'])
        r = sh['shank_r'] + 0.7 * (sh['head_r'] - sh['shank_r'])
        ring = lambda off: [v_add(v_sub(sh['seat'], v_scale(sh['axis'], off)), v_add(v_scale(u, r * math.cos(k * math.pi / 4)), v_scale(v, r * math.sin(k * math.pi / 4))))
                            for k in range(8)]
        seated, under = False, None
        for off in (0.1, 0.6, 1.1, 1.7):                                  # straight under the head, or under a washer
            if sum(1 for p in ring(off) if inside(p)) >= 4:
                seated = True; break
        s['holes'] = _opn_on_axis(sh, holes, doc_pt)
        if not seated and s['holes']:                                     # it lies in a hole of this part: what is under its head?
            mine = set(id(x) for x in s['recs'])
            for off in (0.1, 0.6, 1.1, 1.7):                              # a printed part under the head, also under a washer, comes first
                pts = ring(off)
                gate_read((pts[0], pts[0]), 2.0 * sh['head_r'])
                for rec in world:
                    if rec['mesh'] or rec['own'] or id(rec) in mine or box_gap(rec['box'][0], rec['box'][1], pts[0], pts[0]) > 2.0 * sh['head_r']:
                        continue
                    if '_inside' not in rec:
                        rec['_inside'] = _opn_inside(rec)
                    if sum(1 for p in pts if rec['_inside'](p)) >= 4 and (under is None or (rec['printed'] and not under['printed'])):
                        under = rec
                if under is not None and under['printed']:
                    break
            # a screw answers to the printed part under its head. With a washer, a bought part or nothing there, the
            # printed part it screws into answers for it: nobody else will check its key path
            seated = under is None or not under['printed']
        s['n'], s['seated'] = n + 1, seated
        s['under'] = None if under is None else {'name': under['name'], 'kind': under['kind'], 'part': under['part']}
    return found, sorted(set(not_screws)), cut


def _opn_on_axis(sh, holes, doc_pt):
    """Ids of the census holes a screw lies in: same axis (0.3 mm), and its stretch from tip to head top meets the hole."""
    out = []
    for h in holes:
        P, A = doc_pt(h['anchor'])
        if not coaxial(P, A, sh['seat'], sh['axis'], 0.3):
            continue
        a, b = sorted((along(sh['tip'], P, A), along(sh['top'], P, A)))
        if b >= -float(h.get('depth') or 0.0) - 1.0 and a <= 1.0:
            out.append(h.get('id'))
    return out


def probe(job):
    """job  uses "doc", "component_id", "occurrence", "body", "features" ({"holes": [...], "openings": [...]} of this part's census),
         "params" ("access.reach", "access.tool_clearance", "access.cap_max", "inserts.iron_d", "inserts.iron_reach",
         "screws.ref_pattern"), "tables"["screws"], "not_printed", "cursor", "deadline_s"

    Obstacles are ALL bodies whose bounding box is in reach: the part itself, printed parts, bought parts, hardware refs,
    hidden components, and mesh refs (by bounding box, then vertices inside the path: marked "approx").

    Returns {"facts": {...}, "done": bool, "cursor", "sig": gate_signature(body)} with facts
      "openings": [{"feature": "h3" | "o1" | "s2"       census id, or "s<n>" for the head of a screw ref seated on this part
                    "of": "hole" | "insert_pocket" | "counterbore" | "slot" | "window" | "screw_head",
                    "anchor": the census anchor; for a screw head {"kind": "screw", "axis": tip -> head, "point": head seat}
                    "paths": {"own": PATH,                 the opening's own section over access.reach
                              "tool": PATH (screw heads: head radius + access.tool_clearance / 2),
                              "iron": PATH (insert pockets: inserts.iron_d / 2 over inserts.iron_reach)},
                    "swing": (screw heads only) {"h": mm above the head top, "r": mm, "reach": mm, "n": directions,
                              "from": the first direction in the part frame (the others follow it at 360 / n degrees,
                              right-handed about the screw axis), "free": [mm free along each direction], "first":
                              [name of the first body met that is not expected, or None]}: the long arm of an L key
                              turning over the head (_opn_swing_frame)}]
            PATH = {"r": 2.2, "reach": 60.0,
                    "free": 60.0        mm from the mouth to the first hit that is not expected; = reach when clear
                    "hits": [{"name": "Cover", "kind": "self" | "printed" | "bought" | "hardware" | "hidden" | "mesh",
                              "part": id or None, "at": 12.5 (mm from the mouth), "share": 0.4 (of the section),
                              "approx": bool, "expected": bool (the fastener that belongs in the hole, or a coaxial
                              mating hole: not an obstacle),
                              "in": "Gearbox / Motor" (only for a body inside an assembly: the components above its
                              own, outermost first; the judges name such a cover with it)}]}
      "closed": [{"hole": "h7", "anchor": the census anchor, "cap": 0.84 (mm of plastic behind the blind end),
                  "behind": {"what": "hole" | "screw" | "ref", "name": "...", "d": 3.4}}]
            blind holes with something that continues on the same axis behind at most access.cap_max of plastic

    More than the contract lists:
      an opening has "end": "mouth" | "exit". The far end of a through hole is an opening too: it gets an entry of its
      own with the same "feature", the census "exit" as its anchor and "end": "exit" (which end of a plain through hole
      the census calls the mouth is arbitrary, and the covered end may be the other one);
      a slot or window has "section": "inscribed circle" (its path is the circle that fits into it) and "inside":
      [{"name", "kind", "approx"}], the other bodies that lie in the opening itself, up to 3 mm behind its mouth (a
      lock screw in its slot, a motor in its window): what sits in an opening is not what covers it. "kind" is the
      kind of a hit; "approx" true = a mesh or a body sampled with points (the judge takes only a measured bought
      part or fastener for what the opening is for);
      a mesh hit has "by_box": True when the mesh could not be read and its bounding box stood for it;
      a hit has "by_points": <the error text> and "approx": True when the boolean failed on that body (imported geometry)
      and the body was sampled with points instead;
      a part whose signature is not job["sig"] any more is not measured: {"stale": true}, "done" false, the new "sig";
      facts hold "reach", "iron_reach", "cap_max" (mm: how far it looked) and "n_features" (what the unit has to do);
      the far end of a through insert pocket has an "iron" path too (a post can hold an insert at each end);
      "screws": [{"n", "ref", "size", "head", "by": "name" | "shape", "seated": bool, "holes": [census hole ids],
                  "under": {"name", "kind", "part"} or None}]
          every screw ref near the part (_opn_screws): a head body and a shank body are one screw, and a pair under
          names no pattern knows is found by its shape. "seated" true: this part answers for its head, and the screw has
          the opening "s<n>". "holes": the census holes it lies in. "seated" false with "holes": its head sits on the
          printed part "under", and its key path is judged there;
      "not_screws": [names]   refs the screw pattern matches whose shape is no screw of the table;
      "screws_cut": bool      true when the look at bodies without a screw name was cut short for time: "screws" may
          then miss screws, and "no screw in this hole" must not be read from it.
      These three are written by the first call of a unit only (lists are joined over its calls).
    The cursor is {"i": index of the next feature in: holes, slots and windows, screw heads; "screws": the screw heads
    the first call found, each with its number, its bodies and where its head is}. When the time of a call runs out
    inside a feature it also has "paths" (the finished paths of feature i, by name: "mouth.own", "mouth.iron",
    "exit.own", "exit.iron", "within", "own", "tool"), "path" (the path in progress: its name, the index of the next
    body and the hits so far), "world" (gate_rows_key() of the bodies the walk listed: place, name and box of each)
    and "asked" (gate_states() of every body one of its paths looked at). A later call whose bodies are not those (one
    moved, grew, was hidden, added or removed, or changed inside its box) measures the feature again from its start,
    with a note; the third such call in a row ends the unit in an error. One body against one path is the largest step between two looks at the clock (one
    cut, at most some hundred point tests); a call cuts at least one body, so it always moves on. A feature is written
    into the facts when all its paths are done, never in halves. job["args"]["path_limit"] = n: at most n bodies cut
    per call (tests, slow machines). The screws are looked for by the
    first call only: that look is cut by the clock, so a later call could find another list, and then the index would
    point at another head and "s<n>" would name another screw. A later call measures exactly the heads of the cursor.
    (A plain number as cursor, from an older plan, is read as the index, and the screws are looked for again.)"""
    design = gate_design(job)
    occ, comp, body = gate_body(design, job)
    sig = gate_signature(body)
    if job.get('sig') and job['sig'] != sig:            # the part changed since its census: the host scans again
        return {'facts': {'stale': True}, 'done': False, 'cursor': job.get('cursor'), 'sig': sig}
    m = gate_matrix(occ)
    inv = mat_inverse(m)
    pbody = body.createForAssemblyContext(occ) if occ is not None else body
    part_box = gate_bbox(pbody)
    params, feats = job.get('params') or {}, job.get('features') or {}
    reach = float(params.get('access.reach', 60.0))
    clear = float(params.get('access.tool_clearance', 1.0))
    cap_max = float(params.get('access.cap_max', 2.0))
    iron_r, iron_reach = float(params.get('inserts.iron_d', 8.0)) / 2.0, float(params.get('inserts.iron_reach', 30.0))
    screws_pat = params.get('screws.ref_pattern') or '(?i)screw|bolt'
    global _OPN_WALK
    swing = _opn_swing_frame(params)
    walk = max(reach, iron_reach, swing['h'] + swing['reach']) + 12.0
    _OPN_WALK = (tuple(c - walk for c in part_box[0]), tuple(c + walk for c in part_box[1]))
    # the facts depend on the bodies the paths, the look for screws and the look behind caps meet: each notes its own
    # region (gate_read), not the whole walk
    world = gate_world(design, job, part_box, walk, '' if occ is None else occ.fullPathName, body.name, read=False)
    own = [r for r in world if r['own']]
    if not own:
        raise KeyError('the body of %r was not found among the bodies of the document' % (job.get('part'),))
    own[0]['_inside'] = _opn_inside(own[0])
    holes = list(feats.get('holes') or [])
    items = [('hole', h) for h in holes] + [('opening', o) for o in (feats.get('openings') or [])]
    doc_pt = lambda a: (mat_point(m, tuple(a['point'])), v_unit(mat_vector(m, tuple(a['axis']))))
    cur = job.get('cursor')
    kept = cur.get('screws') if isinstance(cur, dict) else None
    if isinstance(kept, list):
        # a later call: the screw heads are the ones the first call found, as it stored them in the cursor. The look
        # at the shapes is cut by the clock, so a second look could find another list, and "s<n>" would name another head
        screws, not_screws, cut, heads = None, None, None, kept
    else:
        screws, not_screws, cut = _opn_screws(world, screws_pat, params.get('washers.ref_pattern'), part_box, own[0], job, holes, doc_pt)
        heads = [{'n': s['n'], 'name': s['name'], 'path': s['recs'][0]['path'], 'bodies': sorted(r['body_name'] for r in s['recs']),
                  'top': [round(c, 4) for c in s['shape']['top']], 'seat': [round(c, 4) for c in s['shape']['seat']],
                  'axis': [round(c, 6) for c in s['shape']['axis']], 'head_r': round(s['shape']['head_r'], 4)} for s in screws if s['seated']]
    items += [('screw', h) for h in heads]
    start = int((cur.get('i') if isinstance(cur, dict) else cur) or 0)
    facts = {'openings': [], 'closed': [], 'reach': reach, 'iron_reach': iron_reach, 'cap_max': cap_max, 'n_features': len(items)}
    if screws is not None and start == 0 and not (isinstance(cur, dict) and isinstance(cur.get('screws'), list)):
        # lists are joined over the calls of one unit: these are written once, by the call that looked for the screws
        facts['screws'] = [{'n': s['n'], 'ref': s['name'], 'size': s['size'], 'head': s['head'], 'by': s['by'], 'seated': s['seated'],
                            'holes': s['holes'], 'under': s['under']} for s in screws]
        facts['not_screws'] = not_screws
        facts['screws_cut'] = cut
    # the feature an earlier call began: its finished paths, and the path it stopped in
    begun = {'paths': {}, 'path': None}
    here, touched, again = gate_rows_key(world), set(), 0
    if isinstance(cur, dict) and (cur.get('paths') or cur.get('path')):
        # that feature was measured against the bodies as they were then: the same places, names and boxes (the walk
        # list), and the same state of every body one of its paths looked at. Else it starts again: never two states joined
        if cur.get('world') == here and gate_states_same(cur.get('asked'), world):
            begun = {'paths': dict(cur.get('paths') or {}), 'path': cur.get('path')}
            touched = set(int(t) for t in cur['asked'])
        else:
            again = gate_again(cur.get('again'), 'feature %d' % (start + 1), GATE_MOVED)
            note = 'the bodies round the part changed between two calls of this unit: feature %d is measured again from its start' % (start + 1)
            if note not in _GATE_NOTES:
                _GATE_NOTES.append(note)
    limit = (job.get('args') or {}).get('path_limit')
    work = {'cut': 0}

    def clock():                                                        # asked before every body a path meets
        if work['cut'] >= (int(limit) if limit is not None else 1) and (limit is not None or gate_time_up(job, 3.0)):
            return True
        work['cut'] += 1
        return False

    def path(key, *a):
        if key in begun['paths']:
            return begun['paths'][key]
        part = begun['path'] if (isinstance(begun['path'], dict) and begun['path'].get('key') == key) else None
        try:
            got = _opn_path(*a, part=part, clock=clock, seen=touched, mesh_by_box=key.startswith('swing.'))
        except _OpnLate as e:
            e.state['key'] = key
            raise
        finally:
            for k in touched:                                           # every body a path asked: in a noted region (_fusion.py checks)
                gate_asked(world[k]['name'], world[k]['box'])
        begun['paths'][key], begun['path'] = got, None
        return got
    i = start
    while i < len(items):
        if i > start and (gate_time_up(job, 3.0) or (limit is not None and work['cut'] >= int(limit))):
            return {'facts': facts, 'done': False, 'cursor': {'i': i, 'screws': heads}, 'sig': sig}
        kind, it = items[i]
        rows, closed = [], []                                           # written into the facts when the whole feature is done
        try:
            if kind == 'hole':
                segs = it.get('segments') or [{}]
                role = (it.get('class') or {}).get('role')
                pocket = it['anchor'].get('kind') == 'insert_pocket' or role == 'insert_pocket'
                of = 'insert_pocket' if pocket else ('counterbore' if role in ('counterbore', 'countersink') else 'hole')
                names = [c.get('name') for c in it.get('coaxial') or []]
                P, A = doc_pt(it['anchor'])
                r = max(float(segs[0].get('d') or 0.0), float(segs[0].get('d2') or 0.0)) / 2.0
                paths = {'own': path('mouth.own', P, A, r, reach, world, (), names)}
                if pocket:
                    paths['iron'] = path('mouth.iron', P, A, iron_r, iron_reach, world, (), names)
                rows.append({'feature': it.get('id'), 'of': of, 'anchor': it['anchor'], 'end': 'mouth', 'paths': paths})
                if it.get('through') and it.get('exit'):
                    P, A = doc_pt(it['exit'])
                    r = float(segs[-1].get('d2') or segs[-1].get('d') or 0.0) / 2.0
                    paths = {'own': path('exit.own', P, A, r, reach, world, (), names)}
                    if pocket:                                          # a post with an insert at each end; a straight pocket set from either end
                        paths['iron'] = path('exit.iron', P, A, iron_r, iron_reach, world, (), names)
                    rows.append({'feature': it.get('id'), 'of': of, 'anchor': it['exit'], 'end': 'exit', 'paths': paths})
                row = _opn_closed(it, holes, m, world, cap_max, screws_pat)
                if row:
                    closed.append(row)
            elif kind == 'opening':
                P, A = doc_pt(it['anchor'])
                size = [float(x) for x in (it.get('size') or [0.0])]
                deep = it.get('wall') if it.get('through') else it.get('depth')
                deep = min(3.0, float(deep)) if isinstance(deep, (int, float)) and not isinstance(deep, bool) and deep > 0.2 else 1.0
                within = path('within', P, v_scale(A, -1.0), min(size) / 2.0, deep, world, (), [])      # what lies in the opening itself, behind its mouth
                rows.append({'feature': it.get('id'), 'of': it.get('shape') or 'window', 'anchor': it['anchor'], 'end': 'mouth',
                             'section': 'inscribed circle', 'paths': {'own': path('own', P, A, min(size) / 2.0, reach, world, (), [])},
                             'inside': _opn_within(within)})
            else:
                top, axis, seat, head_r = tuple(it['top']), tuple(it['axis']), tuple(it['seat']), float(it['head_r'])
                skip = set(id(r) for r in world if r['path'] == it['path'] and r['body_name'] in it['bodies'])       # the screw itself
                anchor = {'kind': 'screw', 'axis': [round(c, 4) for c in v_unit(mat_vector(inv, axis))],
                          'point': [round(c, 2) for c in mat_point(inv, seat)]}
                paths = {'own': path('own', top, axis, head_r, reach, world, skip, []),
                         'tool': path('tool', top, axis, head_r + clear / 2.0, reach, world, skip, [])}
                base = v_add(top, v_scale(axis, swing['h']))                  # where the long arm of an L key lies while its short arm is in the head
                u, v = axis_frame(v_unit(axis))
                free, first, blocks = [], [], []
                for k in range(swing['n']):
                    t = 2.0 * math.pi * k / swing['n']
                    d = v_add(v_scale(u, math.cos(t)), v_scale(v, math.sin(t)))
                    got = path('swing.%d' % k, base, d, swing['r'], swing['reach'], world, skip, [])
                    free.append(got['free'])
                    blocking = [h for h in got['hits'] if not h.get('expected')]
                    first.append(blocking[0]['name'] if blocking else None)
                    blocks.append(_opn_blocks(blocking))
                u_part = v_unit(mat_vector(inv, u))
                rows.append({'feature': 's%d' % it['n'], 'of': 'screw_head', 'anchor': anchor, 'end': 'mouth', 'ref': it['name'], 'paths': paths,
                             'swing': {'h': swing['h'], 'r': swing['r'], 'reach': swing['reach'], 'n': swing['n'],
                                       'from': [round(c, 4) for c in u_part], 'free': free, 'first': first, 'blocks': blocks}})
        except _OpnLate as e:                                           # out of time inside this feature: what is done goes with the cursor
            state = {'i': i, 'screws': heads, 'paths': begun['paths'], 'path': e.state, 'world': here, 'asked': gate_states(world, touched)}
            if i == start and again:
                state['again'] = again
            return {'facts': facts, 'done': False, 'sig': sig, 'cursor': state}
        facts['openings'] += rows
        facts['closed'] += closed
        begun['paths'], begun['path'] = {}, None
        touched.clear()
        i += 1
    return {'facts': facts, 'done': True, 'cursor': None, 'sig': sig}
