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


def _opn_path(point, axis, r, reach, world, skip, coax_names):
    """PATH of the contract: one straight cylinder against every body of `world` but those in `skip` (ids)."""
    TB = adsk.fusion.TemporaryBRepManager.get()
    rr = max(0.05, r - 0.05)
    start = v_add(point, v_scale(axis, 0.05))
    length = max(0.1, reach - 0.05)
    end = v_add(start, v_scale(axis, length))
    section = math.pi * rr * rr
    hits = []
    for rec in world:
        if id(rec) in skip or not box_overlaps_path(rec['box'][0], rec['box'][1], start, axis, rr, length):
            continue
        if rec['mesh']:
            nodes, tris = _opn_mesh(rec)
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
        if why:
            hit['by_points'] = why
        if _opn_above(rec) and not rec['own']:
            hit['in'] = _opn_above(rec)
        hits.append(hit)
    hits.sort(key=lambda h: (h['at'], h['name']))
    return {'r': round(r, 3), 'reach': reach, 'free': path_free(hits, reach), 'hits': hits}


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


def _opn_small(rec):
    """Can this body be a screw by its size alone? At most 80 faces, and the two shorter sides of its box at most 40 mm.
    Only a filter for cost: the shape decides (_opn_screws)."""
    d = sorted(rec['box'][1][i] - rec['box'][0][i] for i in range(3))
    try:
        return d[1] <= 40.0 and rec['body'].faces.count <= 80
    except Exception:
        return False


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
                              "iron": PATH (insert pockets: inserts.iron_d / 2 over inserts.iron_reach)}}]
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
    the first call found, each with its number, its bodies and where its head is}. The screws are looked for by the
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
    world = gate_world(design, job, part_box, max(reach, iron_reach) + 12.0, '' if occ is None else occ.fullPathName, body.name)
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
    if screws is not None and start == 0:               # lists are joined over the calls of one unit: these are written once
        facts['screws'] = [{'n': s['n'], 'ref': s['name'], 'size': s['size'], 'head': s['head'], 'by': s['by'], 'seated': s['seated'],
                            'holes': s['holes'], 'under': s['under']} for s in screws]
        facts['not_screws'] = not_screws
        facts['screws_cut'] = cut
    i = start
    while i < len(items):
        if i > start and gate_time_up(job, 3.0):
            return {'facts': facts, 'done': False, 'cursor': {'i': i, 'screws': heads}, 'sig': sig}
        kind, it = items[i]
        if kind == 'hole':
            segs = it.get('segments') or [{}]
            role = (it.get('class') or {}).get('role')
            pocket = it['anchor'].get('kind') == 'insert_pocket' or role == 'insert_pocket'
            of = 'insert_pocket' if pocket else ('counterbore' if role in ('counterbore', 'countersink') else 'hole')
            names = [c.get('name') for c in it.get('coaxial') or []]
            P, A = doc_pt(it['anchor'])
            r = max(float(segs[0].get('d') or 0.0), float(segs[0].get('d2') or 0.0)) / 2.0
            paths = {'own': _opn_path(P, A, r, reach, world, (), names)}
            if pocket:
                paths['iron'] = _opn_path(P, A, iron_r, iron_reach, world, (), names)
            facts['openings'].append({'feature': it.get('id'), 'of': of, 'anchor': it['anchor'], 'end': 'mouth', 'paths': paths})
            if it.get('through') and it.get('exit'):
                P, A = doc_pt(it['exit'])
                r = float(segs[-1].get('d2') or segs[-1].get('d') or 0.0) / 2.0
                paths = {'own': _opn_path(P, A, r, reach, world, (), names)}
                if pocket:                                              # a post with an insert at each end; a straight pocket set from either end
                    paths['iron'] = _opn_path(P, A, iron_r, iron_reach, world, (), names)
                facts['openings'].append({'feature': it.get('id'), 'of': of, 'anchor': it['exit'], 'end': 'exit', 'paths': paths})
            row = _opn_closed(it, holes, m, world, cap_max, screws_pat)
            if row:
                facts['closed'].append(row)
        elif kind == 'opening':
            P, A = doc_pt(it['anchor'])
            size = [float(x) for x in (it.get('size') or [0.0])]
            deep = it.get('wall') if it.get('through') else it.get('depth')
            deep = min(3.0, float(deep)) if isinstance(deep, (int, float)) and not isinstance(deep, bool) and deep > 0.2 else 1.0
            within = _opn_path(P, v_scale(A, -1.0), min(size) / 2.0, deep, world, (), [])      # what lies in the opening itself, behind its mouth
            facts['openings'].append({'feature': it.get('id'), 'of': it.get('shape') or 'window', 'anchor': it['anchor'], 'end': 'mouth',
                                      'section': 'inscribed circle', 'paths': {'own': _opn_path(P, A, min(size) / 2.0, reach, world, (), [])},
                                      'inside': _opn_within(within)})
        else:
            top, axis, seat, head_r = tuple(it['top']), tuple(it['axis']), tuple(it['seat']), float(it['head_r'])
            skip = set(id(r) for r in world if r['path'] == it['path'] and r['body_name'] in it['bodies'])       # the screw itself
            anchor = {'kind': 'screw', 'axis': [round(c, 4) for c in v_unit(mat_vector(inv, axis))],
                      'point': [round(c, 2) for c in mat_point(inv, seat)]}
            facts['openings'].append({'feature': 's%d' % it['n'], 'of': 'screw_head', 'anchor': anchor, 'end': 'mouth', 'ref': it['name'],
                                      'paths': {'own': _opn_path(top, axis, head_r, reach, world, skip, []),
                                                'tool': _opn_path(top, axis, head_r + clear / 2.0, reach, world, skip, [])}})
        i += 1
    return {'facts': facts, 'done': True, 'cursor': None, 'sig': sig}
