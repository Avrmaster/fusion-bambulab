"""Thin planar faces of one part (slivers, ledges, crescents of old holes). Wraps the thin-face logic of tools/fusion/fusion_parts.py per body.

Fusion-side unit. Its text is pasted behind _geom.py, _geom_rings.py and _fusion.py (gate_fusion_rpc.build_script), so
their functions are globals here and this file imports nothing from disk. No adsk import is needed: _fusion.py has it.
All work happens inside probe(job), which _fusion.run() calls. Read-only: no timeline edit, no body deleted, temporary
geometry only through adsk.fusion.TemporaryBRepManager. The API is in cm, every fact is in mm.
job["legacy"] maps "insert_pockets", "fusion_parts" and "screw_access" to the absolute path of that tool of
tools/fusion (None when it is not there): a probe that wraps one loads it inside probe() with
g = {"__file__": path}; exec(open(path, encoding="utf-8").read(), g).

The rule is the one of thin() in tools/fusion/fusion_parts.py, on the one body of the unit: a planar face whose mean
width 2 * area / perimeter is small is a sliver, a ledge or the crescent an old hole left. Annular faces (lead-ins,
counterbore floors) and round discs of 0.9 mm radius and more (peg tops) are not thin features and are left out. The
body is the component's own body, so points and normals are in the part frame as they are.

A narrow face alone does not say what it is: one flat of a faceted curve, a chamfer strip and a tooth flank are narrow
by design, a ledge and the crescent of an old hole are leftovers. So each listed face also says how it JOINS its
neighbours ("sides": per edge its length, the turn between the two outward normals, and the neighbour's id). The judge
decides from that; this file only measures. A face whose sides cannot be read is listed without them and the judge
treats it as before.
The count of edges that do not join exactly two faces is split: an edge with ONE face is the seam of a closed face (the
face meets itself, a clean solid has such edges), an edge with more than two is where two volumes touch on an edge."""


_THN_ECC_MM = 0.05        # the circles of one ring may lie this far apart (and a quarter of its width): a cut placed on a rounded centre


def _thn_ring(face):
    """(offset of the two centres, outer radius, inner radius), all in mm, of a planar face bounded by exactly two
    loops of one full circle each, else None."""
    if face.loops.count != 2:
        return None
    c = []
    for lp in face.loops:
        if lp.edges.count != 1:
            return None
        g = lp.edges.item(0).geometry
        if gate_geo(g) != 'Circle3D':
            return None
        c.append((g.center, g.radius * 10.0))
    (c0, r0), (c1, r1) = c
    return c0.distanceTo(c1) * 10.0, max(r0, r1), min(r0, r1)


def _thn_annulus(face):
    """Is the face an annulus (a lead-in, a counterbore floor): two full circles whose centres lie at most _THN_ECC_MM
    apart and at most a quarter of the ring's width. A cut placed at a centre rounded to 0.01 mm leaves circles 0.002 to
    0.03 mm apart; nothing of that prints. A ring further off centre is a crescent at its narrow side: listed as "ring"."""
    ring = _thn_ring(face)
    return ring is not None and ring[0] <= min(_THN_ECC_MM, 0.25 * (ring[1] - ring[2])) + 1e-9


def _thn_disc(face):
    """Radius in mm of a face that is one full circle, else None."""
    if face.loops.count != 1 or face.loops.item(0).edges.count != 1:
        return None
    g = face.loops.item(0).edges.item(0).geometry
    return g.radius * 10.0 if gate_geo(g) == 'Circle3D' else None


def _thn_sides(body, face_id, n, edges, limit=12):
    """How a face joins its neighbours: [[length mm, turn in degrees, id of the neighbour face], ...], one row per edge
    that has another face on its far side, the longest edge first, at most `limit` rows.

    turn  the angle between the outward normal of this face (`n`) and the outward normal of the neighbour, both at the
          middle of the edge: 0 = the neighbour goes on in the same plane, 90 = square to it. Positive where the
          neighbour falls away from this face (a convex edge: the top of a rib, a chamfer), negative where it rises
          from it (a concave edge: the foot of a wall). None when Fusion gives no point or no normal there.
    The sign is asked from the body: the point 0.02 mm from the middle of the edge along (n - neighbour normal) lies
    in the material at a concave edge and in the air at a convex one (BRepBody.pointContainment, as the other units
    use it). The centroid of the face is not used for it: on an arc-, C- or L-shaped face the centroid lies outside
    the face, and the direction towards it is wrong on the inner side. Under 1 degree no sign is asked.
    edges: [(length in cm, edge, first parameter, last parameter)] as the caller measured them."""
    IN = adsk.fusion.PointContainment.PointInsidePointContainment
    kept = []
    for ln, e, p0, p1 in edges:
        other, fs = None, e.faces
        for k in range(fs.count):
            g = fs.item(k)
            if g.tempId != face_id:
                other = g
                break
        if other is None:
            continue                                    # a seam: the face meets itself there
        kept.append((round(ln * 10.0, 3), e, p0, p1, other))
    kept.sort(key=lambda r: -r[0])                      # the longest first; only the rows that are listed get a turn:
    rows = []                                           # a toothed outline has thousands of edges, and a turn is a point test
    for ln, e, p0, p1, other in kept[:limit]:
        turn = None
        ok, mid = e.evaluator.getPointAtParameter((p0 + p1) / 2.0)
        if ok:
            ok, nv = other.evaluator.getNormalAtPoint(mid)
            if ok:
                n2 = v_unit((nv.x, nv.y, nv.z))
                ang = math.degrees(math.acos(max(-1.0, min(1.0, v_dot(n, n2)))))
                turn = round(ang, 1)
                if ang > 1.0:
                    probe_at = v_add(gate_mm(mid), v_scale(v_unit(v_sub(n, n2)), 0.02))
                    if body.pointContainment(gate_point(probe_at)) == IN:
                        turn = -turn                    # material on that side: the neighbour rises from this face
        rows.append([ln, turn, other.tempId])
    return rows


def probe(job):
    """job  uses "doc", "component_id", "occurrence", "body", "args" ({"width_cap": 2.0}: faces up to this width are
         listed; the judge holds the limit), "cursor", "deadline_s"

    Returns {"facts": {...}, "done": bool, "cursor", "sig": gate_signature(body)} with facts
      "faces": [{"anchor": {"kind": "face", "axis": outward normal, "point": centroid}, "width": 0.3, "area": 1.2 (mm2),
                 "shape": "strip" | "disc" | "ring",
                 "ecc": 0.3                 only for "ring": mm between the centres of its two circles; its "width" is then
                                            the narrowest side, R - r - ecc
                 "id": 4711                 the face's id inside this body (tempId): only to tie rows of these facts together
                 "sides": [[6.0, 90.0, 4712], [6.0, -90.0, 4698], ...]}]       _thn_sides: length, turn, neighbour id
      "width_cap": 2.0, "lumps": 1, "shells": 1

    As in thin(): an annular face is never listed (_thn_annulus: its circles at most 0.05 mm and a quarter of its width
    apart), a disc only under 0.9 mm radius (shape "disc"); a face of two full circles further apart is a "ring"; every
    other thin planar face is a "strip". Without args.width_cap the cap is 2.0, or 1.5 x walls.min_face_width when that is more.
    "id" and "sides" are left out for a face where Fusion does not answer one of the calls behind them; "sides_unread"
    counts those faces.
    More than the contract lists: "n_faces" (faces of the body) and, on the last call only, "non_manifold_edges" (edges
    with more than two faces: two volumes touch on an edge) and "seam_edges" (edges with one face: the seam of a closed
    face, no defect). The cursor is the index of the next face; the face count itself means "only the edges are left".
    The edge walk looks at the clock every 500 edges; when the time is used up its cursor is {"e": index of the next
    edge, "seams", "bad": the counts so far}, and the next call goes on from there.
    args {"sig_only": true}: nothing is measured, the call returns the signature of the body as it is now, computed from
    its faces in this call and never taken from the memory of the session (the export uses it before it says "already
    stamped")."""
    design = gate_design(job)
    occ, comp, body = gate_body(design, job)
    if (job.get('args') or {}).get('sig_only'):         # the export asks only: is the part still the geometry that was checked
        return {'facts': {'sig_only': True}, 'done': True, 'cursor': None, 'sig': gate_signature(body, fresh=True)}
    sig = gate_signature(body)
    cap = (job.get('args') or {}).get('width_cap')
    cap = float(cap) if cap else max(2.0, 1.5 * float((job.get('params') or {}).get('walls.min_face_width', 1.2)))
    faces = body.faces
    n = faces.count
    facts = {'faces': [], 'width_cap': cap, 'lumps': body.lumps.count, 'shells': body.shells.count, 'n_faces': n}
    cur = job.get('cursor')
    walk = cur if isinstance(cur, dict) else None       # {"e", "seams", "bad"}: the faces are done, the edge walk goes on
    start = n if walk is not None else int(cur or 0)
    unread = 0
    for i in range(start, n):
        if i > start and i % 20 == 0 and gate_time_up(job, 2.0):
            if unread:
                facts['sides_unread'] = unread
            return {'facts': facts, 'done': False, 'cursor': i, 'sig': sig}
        f = faces.item(i)
        if gate_geo(f.geometry) != 'Plane' or _thn_annulus(f):
            continue
        ring = _thn_ring(f)                             # two circles further apart than a rounding: judged at the narrow side
        disc = _thn_disc(f)
        if disc is not None and disc >= 0.9:
            continue
        per, edges = 0.0, []
        for e in f.edges:
            _, p0, p1 = e.evaluator.getParameterExtents()
            _, ln = e.evaluator.getLengthAtParameter(p0, p1)
            per += ln
            edges.append((ln, e, p0, p1))
        if per <= 0:
            continue
        width = 2.0 * f.area / per * 10.0
        if ring is not None:
            width = max(0.0, ring[1] - ring[2] - ring[0])   # the mean width of a ring does not change with its offset; its narrowest side does
        if width > cap:
            continue
        ok, nv = f.evaluator.getNormalAtPoint(f.pointOnFace)
        if not ok:
            nv = f.geometry.normal
        try:
            c = f.centroid
        except Exception:
            c = f.pointOnFace
        normal = v_unit((nv.x, nv.y, nv.z))
        row = {'anchor': {'kind': 'face', 'axis': [round(x, 4) for x in normal], 'point': [round(x, 2) for x in gate_mm(c)]},
               'width': round(width, 3), 'area': round(f.area * 100.0, 2), 'shape': 'disc' if disc is not None else 'ring' if ring is not None else 'strip'}
        if ring is not None:
            row['ecc'] = round(ring[0], 4)
        try:
            fid = f.tempId
            row['sides'] = _thn_sides(body, fid, normal, edges)
            row['id'] = fid
        except Exception:
            row.pop('sides', None)
            unread += 1
        facts['faces'].append(row)
    if unread:
        facts['sides_unread'] = unread
    if n > start and gate_time_up(job, 2.0):            # the edges of a large body are walked in a call of their own
        return {'facts': facts, 'done': False, 'cursor': n, 'sig': sig}
    walk = walk or {}
    first = k = int(walk.get('e') or 0)
    seams, bad = int(walk.get('seams') or 0), int(walk.get('bad') or 0)
    edges = body.edges
    m = edges.count
    while k < m:                                        # a body with thousands of faces has three times as many edges: the walk has its own clock
        if k > first and (k - first) % 500 == 0 and gate_time_up(job, 2.0):
            return {'facts': facts, 'done': False, 'cursor': {'e': k, 'seams': seams, 'bad': bad}, 'sig': sig}
        c = edges.item(k).faces.count
        if c == 1:
            seams += 1
        elif c != 2:
            bad += 1
        k += 1
    facts['non_manifold_edges'], facts['seam_edges'] = bad, seams
    return {'facts': facts, 'done': True, 'cursor': None, 'sig': sig}
