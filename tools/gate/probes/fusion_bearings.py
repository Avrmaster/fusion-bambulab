"""Bearings near one part: found from ref bodies, sized against the table, with what covers each ring face and the clamp room.

Fusion-side unit. Its text is pasted behind _geom.py, _geom_rings.py and _fusion.py (gate_fusion_rpc.build_script), so
their functions are globals here and this file imports nothing from disk. No adsk import is needed: _fusion.py has it.
All work happens inside probe(job), which _fusion.run() calls. Read-only: no timeline edit, no body deleted, temporary
geometry only through adsk.fusion.TemporaryBRepManager. The API is in cm, every fact is in mm.
job["legacy"] maps "insert_pockets", "fusion_parts" and "screw_access" to the absolute path of that tool of
tools/fusion (None when it is not there): a probe that wraps one loads it inside probe() with
g = {"__file__": path}; exec(open(path, encoding="utf-8").read(), g).

How it measures (the arithmetic is in _geom_rings.py and is tested there; this file only asks Fusion "is this point
inside that body" through BRepBody.pointContainment, behind a bounding-box test):
  bearing   a name picks the candidates, the geometry decides (bearing_claim of _geom_rings.py). Candidates: a body
            whose own name matches bearings.ref_pattern or carries a designation of the table ("608 (1)"), and a body
            with no hardware word in its own name in a component named so. A body whose own name says screw, washer,
            insert or nut is never a bearing, whatever bearing its name mentions. A single body that is a ring of the
            table is a bearing by itself; the other bodies of one occurrence on one axis that overlap along it are one
            bearing (rings, balls and cage together). A ring that measures otherwise than the row its name carries is
            not that bearing, and neither is a solid body of revolution with no bore (a pin, a screw named after the
            bearing it clamps): both are listed in "not_bearings" and "rejected". A name alone stands only for a
            body with no circular edge at all. A printed part counts only when its geometry is a table row.
  whose     a bearing is listed for this part when the part seats it or stands at one of its ring faces, or when no
            printed part does. The others are named in "near_bearings" and not measured further.
  cover     two circles of points on each ring face, one point every 3 mm of arc (at least 24, at most 144 per circle:
            a D12 washer on a D100 ring is met by two or three of them wherever it sits), pushed out of the face by
            0.02, contact gap + 0.02, then every 0.1 mm to 2 mm.
  bodies    gate_world() of _fusion.py: printed by the rule of the scan, named as the scan names them.
  clamp     a ring covered on both faces by two different bodies. From every flat face of the clamp that looks towards
            the ring a grid of lines runs on in that direction: the first body met (not the bearing) is where the clamp
            lands. room = ring width + the gaps as drawn - that travel.
  seat      eight directions at nine heights of the ring: where the wall of a neighbour begins (halving search).
Limits: bodies with the light bulb off and mesh bodies hold nothing (they are named in "hidden_near" / "mesh_near"); a
clamp that lands on a cone or a curved face is not seen (its travel reads as free); the grid on a big flat face is 6 x 6.

Time. One bearing can cost more than a call has: a D100 ring has 210 sample points per ring face, each looked at at 21
offsets, against every neighbour whose bounding box holds the point, and a point test on a body with thousands of faces
takes milliseconds. So the work on ONE bearing is cut into steps, and every step asks through a GateTape of _fusion.py:
  face step    one neighbour at one side of the bearing: both ring faces of that side (_brg_face_step)
  seat step    one neighbour as the seat of one ring (_brg_seat_step)
  travel step  the clamp of one ring: where it lands (_brg_clamp)
The clock is read between the steps and, inside a step, every 16 point tests. A call that runs out of time returns
"done": False with a cursor that carries the finished steps of the bearing as plain JSON and the tape of the step in
progress; the next call reads them and goes on. Bodies are named in the cursor by their index in the list of
gate_world(). One bearing is measured on ONE state of the model: the state of a bearing in progress carries "k", the
place, name and box of every body the bearing uses (its own bodies, the bodies beside it, and every body a clamp
could land on: _brg_used), and "asked", the state of the bearing's own bodies and of every body that answered a
point test (gate_states of _fusion.py: revision id, face count, volume, area). When one of them differs in the next call, the bearing in
progress is measured again from its start, with a note; the third such call in a row ends the unit in an error. A
body that is none of these may change: it restarts nothing. The cursor also carries "key", the list of the bearings
found: when that list differs in a later call, the index of the cursor points at another bearing and the unit ends in
an error ("start the unit again").
One piece of a travel step cannot look at the clock: the walk over the faces of a clamp body, once per bearing and
ring (the picked faces are kept in the cursor). Before the walk over a body with more than _BRG_WALK_FACES faces
the step stops when the call has done other work and less than half of its time is left: the walk then opens the
next call. Its seconds are in the timing block ("travel_walk_s").
What is not asked at all, because the answer is known without a point test: a neighbour whose bounding box does not
reach the slab in front of a ring face (slab_box), a neighbour whose box cannot hold the wall of a seat
(seat_possible), and the cover of the other ring, which samples the points the other ring's own test samples
(face_first_cut). The part of the unit is looked at first: when it neither seats the bearing nor stands at a ring
face, the bearing is another part's as soon as one printed neighbour stands at a ring face, and the rest is not
measured (it would be thrown away: such a bearing is only named in "near_bearings")."""

_BRG_LOOK = 2.0                 # mm beyond a ring face in which a cover is still listed (with its gap)
_BRG_FAR = 120.0                # mm round the part in which bodies are read at all
_BRG_RESERVE = 3.0              # s of a call that are kept for writing its answer
_BRG_WALK_FACES = 500           # a clamp body with more faces: the walk over them does not start late in a call


class _BrgLate(Exception):
    """The time of the call is used up between two steps of one bearing."""


def _brg_inside(rec, tape=None, tag=0):
    """callable(point mm) -> is the point inside the body of this record. A point outside the body's bounding box is
    answered without a call; the others are asked through `tape` (GateTape of _fusion.py) when one is given."""
    body, lo, hi = rec['body'], rec['box'][0], rec['box'][1]
    IN = adsk.fusion.PointContainment.PointInsidePointContainment

    def inside(p):
        if p[0] < lo[0] or p[0] > hi[0] or p[1] < lo[1] or p[1] > hi[1] or p[2] < lo[2] or p[2] > hi[2]:
            return False
        if tape is not None:
            return tape.ask(body, p, tag)
        return body.pointContainment(gate_point(p)) == IN
    return inside


def _brg_circles(body):
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


_BRG_OTHER = ('washer', 'insert', 'nut', 'screw')      # a body whose own name says one of these is never a bearing


def _brg_many_faces(rec, most=80):
    """Has a body more faces than a bearing drawn as one ring can have? Only a filter for cost, for printed bodies: a
    printed part counts as a bearing only when its geometry is a ring of the table, and a ring has a handful of faces."""
    try:
        return rec['body'].faces.count > most
    except Exception:
        return True


def _brg_hint(text, pattern, sizes):
    """Does a name point at a bearing: the pattern matches it, or it carries a designation of the table."""
    return gate_search(pattern, text) or designation_in_name(text, sizes) is not None


def _brg_pick(world, pattern, sizes, pats, part_box):
    """The candidate bodies: [(record, own)], own = its own name points at a bearing (for the only body of a
    component that is the component's name): 2 when the bearing pattern matches it and no other hardware word does
    ("ref bearing upper"), 1 when it only carries a designation or another hardware word too, 0 when only the name of
    its component points at a bearing. Only bodies within 25 mm of the part are read at all, and of the printed
    bodies only those with at most 80 faces (_brg_many_faces)."""
    out = []
    for r in world:
        if r['mesh'] or r['own'] or box_gap(r['box'][0], r['box'][1], part_box[0], part_box[1]) > 25.0:
            continue
        if r['printed'] and _brg_many_faces(r):
            continue                                    # a printed part with a designation in its name ("Housing 608"): no ring, and its edges are not read
        other = any(gate_search(pats[k], r['body_name']) for k in _BRG_OTHER)
        if other and not gate_search(pattern, r['body_name']):
            continue                                    # "M3x8 shank (608 clamp)" is a screw
        whole = r['name'] == r['comp']                  # the only body of its component: the component's name is its own
        text = r['name'] if whole else r['body_name']
        own = (2 if (gate_search(pattern, text) and not other) else 1) if _brg_hint(text, pattern, sizes) else 0
        if own or _brg_hint(r['comp'], pattern, sizes):
            out.append((r, own))
    return out


def _brg_hollow(recs, size):
    """Is the middle of the ring air? A stepped solid pin has circular edges of two radii and no bore."""
    c = size['centre']
    IN = adsk.fusion.PointContainment.PointInsidePointContainment
    return not any(r['body'].pointContainment(gate_point(c)) == IN for r in recs)


def _brg_find(world, pattern, part_box, table, pats):
    """(the bearings near this part, names passed over, why): bearings as [{"key", "name", "recs", "box", "size",
    "designation", "by"}] sorted by key. Passed over: a candidate whose own name (or whose component, by the pattern)
    points at a bearing and whose geometry says no; a printed part that only carries the word. A body picked only by a
    designation in the name of its assembly ("shaft: pin + 2 x 608") and that is no bearing is passed over in silence:
    it is the pin."""
    sizes = table.get('sizes') or {}
    by_path = {}
    for r, own in _brg_pick(world, pattern, sizes, pats, part_box):
        by_path.setdefault(r['path'], []).append((r, own))
    out, skipped, rejected = [], [], []

    def take(recs, owns, profile, name):
        box = box_of([c for r in recs for c in r['box']])
        if box_gap(box[0], box[1], part_box[0], part_box[1]) > 1.0:
            return
        size = bearing_size(profile)
        solid = profile is not None and (size is None or not _brg_hollow(recs, size))      # it has circular edges and no bore: a pin, a screw
        if solid:
            size = None
        named = next((d for d in (designation_in_name(r['body_name'], sizes) for r in recs) if d), None) or designation_in_name(recs[0]['comp'], sizes)
        des, by, why = bearing_claim(size, named, table)
        if solid and max(owns) < 2:                                    # a readable shape that is no ring contradicts a designation in a name
            des, by, why = None, None, 'a solid body of revolution D%g x %g with no bore' % (round(2 * profile['r_max'], 2), round(profile['hi'] - profile['lo'], 2))
        # (a body that calls itself a bearing and is drawn as a solid puck stays listed by its name: the judge asks for a ring)
        own, by_comp = any(owns), gate_search(pattern, recs[0]['comp'])
        printed = all(r['printed'] for r in recs)
        # kept: a ring of the table; a body whose own name points at a bearing and that nothing contradicts (also with
        # no ring geometry: listed, never dropped); a ring of no table row in a component the pattern calls a bearing
        if by == 'geometry' or (not printed and why is None and (own or (by_comp and size is not None))):
            out.append({'key': '%s|%s' % (recs[0]['path'], recs[0]['body_name']), 'recs': recs, 'name': name, 'box': box,
                        'profile': profile, 'size': size, 'designation': des, 'by': by})
        elif own or by_comp:                                           # the name claims a bearing and the geometry says no
            skipped.append(name)
            rejected.append({'name': name, 'why': why or ('a printed body that carries the word, and no ring of the bearing table' if printed else
                                                          'no ring geometry' if size is None else 'no ring of the bearing table'),
                             'd': round(size['d'], 3) if size else None, 'D': round(size['D'], 3) if size else None, 'B': round(size['B'], 3) if size else None})

    for path in sorted(by_path):
        try:
            _brg_group(by_path[path], table, take)
        except Exception as e:                                         # the bodies of one occurrence could not be read: named, never dropped
            name = by_path[path][0][0]['name']
            skipped.append(name)
            rejected.append({'name': name, 'why': 'its shape could not be read (%s: %s)' % (type(e).__name__, str(e)[:80]), 'd': None, 'D': None, 'B': None, 'unread': True})
    out.sort(key=lambda g: g['key'])
    return out, skipped, rejected


def _brg_group(rows, table, take):
    """The candidate bodies of ONE occurrence, put together into bearings and handed to take() of _brg_find."""
    recs = [r for r, _ in rows]
    owns = [o for _, o in rows]
    circles = [_brg_circles(r['body']) for r in recs]
    profs = [revolve_profile(c) for c in circles]
    single = []
    for i, p in enumerate(profs):                                  # a body that is a table ring by itself
        size = bearing_size(p)
        if size and match_bearing(size['d'], size['D'], size['B'], table) and _brg_hollow([recs[i]], size):
            single.append(i)
            take([recs[i]], [owns[i]], p, recs[i]['name'])
    rest = [i for i in range(len(recs)) if i not in single]
    turned = [i for i in rest if profs[i]]
    groups = [[turned[k] for k in c] for c in
              cluster_coaxial([(profs[i]['origin'], profs[i]['axis'], profs[i]['lo'], profs[i]['hi']) for i in turned], -0.05)]
    loose = [i for i in rest if not profs[i]]
    if not groups and loose:
        groups, loose = [loose], []
    boxes = [box_of([c for i in g for c in recs[i]['box']]) for g in groups]
    for i in loose:                                                # a ball: it joins the part whose box holds its centre
        c = tuple((recs[i]['box'][0][k] + recs[i]['box'][1][k]) / 2.0 for k in range(3))
        for g, b in zip(groups, boxes):
            if all(b[0][k] <= c[k] <= b[1][k] for k in range(3)):
                g.append(i); break
    for g in groups:
        first = recs[g[0]]
        name = first['comp'] if (len(g) > 1 and len(groups) == 1 and not single) else first['name']
        take([recs[i] for i in g], [owns[i] for i in g], revolve_profile([c for i in g for c in circles[i]]), name)


def _brg_merge(entries):
    """Covers with the same name (copy numbers dropped) are one cover: six washers round a ring are six contact runs."""
    merged = {}
    for e in entries:
        k = (name_number_base(e['name']), e['kind'])
        if k not in merged:
            merged[k] = dict(e, name=k[0], _recs=[e['_rec']], _flags=list(e['_flags']))
            continue
        t = merged[k]
        t['_recs'].append(e['_rec'])
        t['_flags'] = [a or b for a, b in zip(t['_flags'], e['_flags'])]
        t['share'] = round(min(1.0, t['share'] + e['share']), 3)
        t['near'] = round(min(1.0, t['near'] + e['near']), 3)
        t['gap'] = min(t['gap'], e['gap'])
        t['touches_other_ring'] = t['touches_other_ring'] or e['touches_other_ring']
        t['other_ring_share'] = round(min(1.0, t['other_ring_share'] + e['other_ring_share']), 3)
    out = []
    for t in merged.values():
        t['points'] = arc_runs(t['_flags'])[1] if t['share'] > 0 else 0
        out.append(t)
    out.sort(key=lambda t: (-t['share'], -t['near'], t['name']))
    return out


def _brg_face_step(g, A, rec, side, touch, land, offsets, brg_ids):
    """One neighbour at one side of the bearing: [{"ring": "inner" | "outer", "cover": the public keys of a cover,
    "flags": [bool per angle]}], one row per ring face of that side the body stands in front of (0, 1 or 2 rows).
    Plain JSON, so a cursor can carry it. brg_ids: {id of a body record: (d, D) of the bearing it belongs to}: a cover
    that is itself a bearing has "bearing": True, and "twin": True when its bore and outer diameter are those of this
    bearing (ring lies on ring).
    Both ring faces of a side are sampled on the same angles, and "does it touch the other ring" asks the points the
    other ring's own test asks: each point is asked once."""
    size = g['size']
    same = id(rec) in brg_ids and brg_ids[id(rec)] is not None and abs(brg_ids[id(rec)][0] - size['d']) <= 0.1 and abs(brg_ids[id(rec)][1] - size['D']) <= 0.1
    fc = ring_face_centre(size['centre'], A, size['B'], side)
    dirn = A if side == '+' else v_scale(A, -1.0)
    ann = [ring_annulus(size['d'], size['D'], ring, land) for ring in ('inner', 'outer')]
    n_ang = ring_angles(max(ann[0][1], ann[1][1]))                      # a big ring is sampled closer than 24 points
    pts = {'inner': annulus_points(fc, A, ann[0][0], ann[0][1], n_ang, 2), 'outer': annulus_points(fc, A, ann[1][0], ann[1][1], n_ang, 2)}
    lo, hi = slab_box(pts['inner'] + pts['outer'], dirn, offsets)
    if box_gap(rec['box'][0], rec['box'][1], lo, hi) > 0.0:
        return []                                                       # its box does not reach the slab in front of this side: no point is inside it
    inside = rec['_inside']
    first = {}
    for ring in ('inner', 'outer'):
        first[ring] = face_first(pts[ring], inside, dirn, offsets)
    out = []
    for ring, other in (('inner', 'outer'), ('outer', 'inner')):
        cov = face_cover_of(pts[ring], first[ring], offsets, touch)
        if cov is None:
            continue
        gap = cov['gap']
        if gap == 0.0:
            deep = face_overlap(pts[ring], inside, dirn)
            gap = -deep if deep > 0 else 0.0
        oth = face_cover_of(pts[other], face_first_cut(first[other], 2), offsets[:2], touch)
        out.append({'ring': ring, 'flags': cov['flags'],
                    'cover': {'name': rec['name'], 'kind': rec['kind'], 'part': rec['part'], 'bearing': id(rec) in brg_ids, 'twin': bool(same),
                              'share': cov['share'], 'points': cov['points'], 'gap': round(gap, 3), 'near': cov['near'],
                              'touches_other_ring': bool(oth and sum(oth['flags']) >= 2), 'other_ring_share': oth['share'] if oth else 0.0}})
    return out


def _brg_seat_step(g, A, rec, ring):
    """One neighbour as the radial seat of one ring: seat_scan() of _geom_rings.py, or None. A body whose box cannot
    hold the wall of a seat is not asked a single point."""
    size = g['size']
    r_nom = (size['D'] if ring == 'outer' else size['d']) / 2.0
    if not seat_possible(rec['box'][0], rec['box'][1], size['centre'], A, r_nom, outer=(ring == 'outer')):
        return None
    return seat_scan(rec['_inside'], size['centre'], A, r_nom, size['B'], outer=(ring == 'outer'))


def _brg_gather(g, world, neighbours, part):
    """(faces, seat, seat_recs) of one bearing from its finished face and seat steps, in the order of gate_world():
    faces = {"inner": {"+": [cover], "-": [cover]}, "outer": {...}} with the private keys on the covers ("_recs": the
    body records), seat = {"inner": S or None, "outer": S or None}, seat_recs = the body record behind each seat."""
    size, faces = g['size'], {}
    for ring in ('inner', 'outer'):
        faces[ring] = {}
        for side in ('+', '-'):
            entries = []
            for k in neighbours:
                for row in part['f'].get('%d|%s' % (k, side)) or []:
                    if row['ring'] == ring:
                        entries.append(dict(row['cover'], _rec=world[k], _flags=list(row['flags'])))
            faces[ring][side] = _brg_merge(entries)
    seat, seat_recs = {}, {}
    for ring in ('inner', 'outer'):
        r_nom = (size['D'] if ring == 'outer' else size['d']) / 2.0
        best = None
        for k in neighbours:
            s = part['s'].get('%s|%d' % (ring, k))
            if s is not None and (best is None or abs(s['d'] - 2 * r_nom) < abs(best[0]['d'] - 2 * r_nom)):
                best = (s, world[k])
        if best is None:
            seat[ring], seat_recs[ring] = None, None
        else:
            seat[ring], seat_recs[ring] = dict(best[0], part=best[1]['part'], name=best[1]['name'], kind=best[1]['kind']), best[1]
    return faces, seat, seat_recs


def _brg_travel(clamp_recs, press, g, A, world, look, pick=None, pick_key='', tape=None, heavy=_BRG_WALK_FACES):
    """How far the clamp can still move along `press` before one of its flat faces lands on another body.
    Returns (travel mm, name of the stop or None, capped: nothing met within `look`).
    pick: a dict that keeps, per clamp body, the indices of its flat faces that look along `press` (the largest 16),
    under pick_key + the place of the body in `clamp_recs`. A call that goes on with this clamp reads them from there
    and does not walk the faces of the body again.
    tape: the GateTape of the call. The walk over the faces of a body with more than `heavy` faces reads no clock: when
    the call has done other work and less than half of its time is left, GateStop is raised before it and the walk
    opens the next call. The seconds of every walk go into tape.timing["walk"]."""
    size = g['size']
    own = set(id(r) for r in clamp_recs) | set(id(r) for r in g['recs'])
    u, v = axis_frame(press)
    hits = []
    pick = pick if pick is not None else {}
    for n_rec, rec in enumerate(clamp_recs[:12]):
        in_c = rec['_inside']
        stops = [r for r in world if id(r) not in own and not r['mesh'] and r['visible']
                 and box_gap(r['box'][0], r['box'][1], rec['box'][0], rec['box'][1]) <= look + 0.5]
        for r in stops:
            if '_inside' not in r:
                r['_inside'] = _brg_inside(r)
        all_faces = rec['body'].faces
        key = '%s|%d' % (pick_key, n_rec)
        if not isinstance(pick.get(key), list):
            n_faces = all_faces.count
            if tape is not None and n_faces > heavy and tape.no_time_for(float(tape.job.get('deadline_s') or 20) / 2.0):
                raise GateStop()                                        # the walk is one piece: it opens the next call
            t_walk = time.time()
            flat = []
            for fi in range(n_faces):
                f = all_faces.item(fi)
                if gate_geo(f.geometry) != 'Plane':
                    continue
                ok, n = f.evaluator.getNormalAtPoint(f.pointOnFace)
                if ok and v_dot(v_unit((n.x, n.y, n.z)), press) > 0.99:
                    flat.append((f.area, fi))
            flat.sort(key=lambda t: -t[0])
            pick[key] = [fi for _, fi in flat[:16]]
            if tape is not None:
                row = tape.timing.setdefault('walk', [0.0, 0])
                row[0] += time.time() - t_walk
                row[1] += n_faces
        for fi in pick[key]:
            f = all_faces.item(fi)
            p0 = gate_mm(f.pointOnFace)
            lo, hi = gate_bbox(f)
            cs = box_corners(lo, hi)
            us, vs = [v_dot(c, u) for c in cs], [v_dot(c, v) for c in cs]
            nu = min(6, max(2, int(math.ceil((max(us) - min(us)) / 1.5)) + 1))
            nv = min(6, max(2, int(math.ceil((max(vs) - min(vs)) / 1.5)) + 1))
            base = v_scale(press, v_dot(p0, press))
            for i in range(nu):
                for j in range(nv):
                    a = min(us) + (max(us) - min(us)) * (i + 0.5) / nu
                    b = min(vs) + (max(vs) - min(vs)) * (j + 0.5) / nv
                    p = v_add(base, v_add(v_scale(u, a), v_scale(v, b)))
                    if not in_c(v_sub(p, v_scale(press, 0.03))) or in_c(v_add(p, v_scale(press, 0.03))):
                        continue                                       # not on this face

                    def owner(s, p=p):
                        q = v_add(p, v_scale(press, s))
                        if abs(along(q, size['centre'], A)) <= size['B'] / 2.0 and \
                                size['d'] / 2.0 - 0.05 <= off_axis(q, size['centre'], A) <= size['D'] / 2.0 + 0.05:
                            return '\x00bearing'                       # the ring itself; the bore is NOT skipped: a post that
                                                                       # ends flush with the ring face is where a washer lands
                        if in_c(q):
                            return '\x00self'
                        for r in stops:
                            if r['_inside'](q):
                                return r['name']
                        return None
                    h = first_hit(owner, 0.03, look + 0.03, 0.5, 0.002)
                    if h is not None and not h[0].startswith('\x00'):               # the line starts ON the face: s is the travel. Met at the
                        hits.append((0.0 if h[1] <= 0.03 + 1e-9 else round(h[1] - 0.001, 2), h[0]))     # start: 0. Else to 0.01 (a landing drawn 1.0 back reads 1.0)
    return least_travel(hits, look)


def _brg_clamp(ring, g, A, faces, seats, world, washers, look, pick=None, tape=None, heavy=_BRG_WALK_FACES):
    """The clamp of one ring, or None: a ring covered on both faces by two different bodies (see the top)."""
    ends = {}
    for side in ('+', '-'):
        near = [c for c in faces[ring][side] if c['gap'] <= _BRG_LOOK]
        ends[side] = near[0] if near else None
    if not ends['+'] or not ends['-']:
        return None
    ids = {sd: sorted(id(r) for r in ends[sd]['_recs']) for sd in ('+', '-')}
    if ids['+'] == ids['-']:
        return None                                                     # one body on both faces: nothing can be drawn tight
    washer = {sd: gate_search(washers, ends[sd]['name']) for sd in ('+', '-')}
    seat_rec = seats.get(ring)
    if washer['+'] != washer['-']:
        side = '+' if washer['+'] else '-'
    elif seat_rec is not None and any(r is seat_rec for r in ends['+']['_recs']) != any(r is seat_rec for r in ends['-']['_recs']):
        side = '-' if any(r is seat_rec for r in ends['+']['_recs']) else '+'
    else:
        vol = lambda c: sum((r['box'][1][0] - r['box'][0][0]) * (r['box'][1][1] - r['box'][0][1]) * (r['box'][1][2] - r['box'][0][2]) for r in c['_recs'])
        side = '+' if vol(ends['+']) <= vol(ends['-']) else '-'
    clamp, stop = ends[side], ends['-' if side == '+' else '+']
    press = v_scale(A, -1.0 if side == '+' else 1.0)
    travel, landed_on, capped = _brg_travel(clamp['_recs'], press, g, A, world, look, pick, ring, tape, heavy)
    return {'ring': ring, 'side': side, 'by': 'washer' if washer[side] else clamp['kind'], 'name': clamp['name'],
            'part': clamp['part'], 'gap': clamp['gap'], 'stop_gap': stop['gap'], 'stop_part': stop['part'],
            'room': clamp_room(g['size']['B'], travel, stop['gap'], clamp['gap']), 'ring_width': round(g['size']['B'], 3),
            'travel': round(travel, 2), 'travel_capped': capped, 'lands_on': landed_on, 'against': stop['name']}


def _brg_public(covers):
    return [{k: c[k] for k in c if not k.startswith('_')} for c in covers]


def _brg_step(part, tape, store, key, stage, fn):
    """Run one step of a bearing, or read it when an earlier call finished it. `store` is the dict of `part` the
    results of this kind of step live in, `key` the step's name in it. A step that an earlier call began is run again
    with its tape (part["at"] names it, part["tape"] holds it). Raises _BrgLate before a step when the time of the call
    is used up; GateStop comes out of a step that ran out of time (the caller then stores tape.saved() in the part)."""
    if key in store:
        return store[key]
    sid = '%s:%s' % (stage, key)
    saved = part.pop('tape', None) if part.get('at') == sid else None
    part.pop('tape', None)
    if tape.out_of_time():
        if saved is not None:
            part['tape'] = saved
        raise _BrgLate()
    part['at'] = sid
    tape.set_stage(stage)
    try:
        tape.begin(saved)
        value = fn()
        tape.end()
    except (GateReplay, ValueError) as e:                               # not the questions the tape answers: this step from its start
        if saved is None:
            raise
        part['again'] = gate_again(part.get('again'), 'a step of a bearing (%s)' % sid)
        note = 'a step of a bearing (%s) was measured again from its start: %s' % (sid, str(e)[:120])
        if note not in _GATE_NOTES:
            _GATE_NOTES.append(note)
        tape.begin(None)
        value = fn()
        tape.end()
    store[key] = value
    part.pop('at', None)
    part.pop('again', None)
    return value


def _brg_used(g, world, near, neighbours, look):
    """The indices in `world` of every body the measurement of this bearing can depend on: its own bodies, the bodies
    beside it (`near`: covers, seats, and the hidden and mesh bodies it names), and every shown solid within the travel
    look of a neighbour (where a clamp could land: the stops of _brg_travel are among them)."""
    used = set(near) | set(k for k, r in enumerate(world) if any(r is x for x in g['recs']))
    for k, r in enumerate(world):
        if k in used or r['mesh'] or not r['visible']:
            continue
        if any(box_gap(r['box'][0], r['box'][1], world[n]['box'][0], world[n]['box'][1]) <= look + 0.5 for n in neighbours):
            used.add(k)
    return sorted(used)


def _brg_one(g, job, world, m, inv, table, touch, look, brg_ids, part, tape, again=None):
    """The facts of one bearing; None when another printed part seats and holds it (it is judged there).
    part: the plain JSON state of this bearing, {} at its start: {"f": face steps, "s": seat steps, "t": travel steps,
    "pick": faces picked for the travel, "at" / "tape": the step in progress, "again": calls in a row that had to
    start that step again (gate_again of _fusion.py), "k": gate_rows_key() of the bodies the bearing uses (_brg_used),
    "asked": gate_states() of the bodies that answered a point test (probe() adds them after every call)}. Finished
    steps are read from it, new ones are written into it; the caller keeps it in the cursor. When the bodies are not
    the ones the state was written for, the state is emptied (the bearing starts again) and again["n"] counts it
    (gate_again: the third call in a row raises). Raises GateStop or _BrgLate when the time of the call is used up."""
    size = g['size']
    row = (table.get('sizes') or {}).get(g['designation'] or '') or {}
    if size is None:                                                    # a name without usable geometry: listed, never skipped
        c = tuple((g['box'][0][i] + g['box'][1][i]) / 2.0 for i in range(3))
        return {'ref': g['name'], 'designation': g['designation'], 'by': g['by'], 'd': row.get('d'), 'D': row.get('D'), 'B': row.get('B'),
                'anchor': {'kind': 'bearing', 'axis': [0.0, 0.0, 1.0], 'point': [round(x, 2) for x in mat_point(inv, c)]},
                'rings': None, 'clamps': [], 'seat': {'inner': None, 'outer': None}, 'why': 'no ring geometry: no coaxial circular edges'}
    axis_part = canonical_axis(mat_vector(inv, size['axis']))
    A = v_unit(mat_vector(m, axis_part))                                # "+" is along the anchor axis
    near = [k for k, r in enumerate(world) if not any(r is x for x in g['recs']) and box_gap(r['box'][0], r['box'][1], g['box'][0], g['box'][1]) <= _BRG_LOOK + 0.5]
    neighbours = [k for k in near if not world[k]['mesh'] and world[k]['visible']]
    for k in neighbours:
        if '_inside' not in world[k]:
            world[k]['_inside'] = _brg_inside(world[k], tape, k)
    t_state = time.time()
    here = gate_rows_key(world, _brg_used(g, world, near, neighbours, look))
    same = (not part) or (part.get('k') == here and gate_states_same(part.get('asked') or {}, world))
    row = tape.timing.setdefault('state', [0.0, 0])
    row[0], row[1] = row[0] + time.time() - t_state, row[1] + len(part.get('asked') or {})
    if not same:
        # other bodies, or bodies in another state, than this bearing was measured against so far: never join two states
        part.clear()
        if again is not None:
            again['n'] = gate_again(again.get('was'), 'bearing %s' % g['name'], GATE_MOVED)
        note = 'the bodies round the part changed between two calls of this unit: bearing %s is measured again from its start' % g['name']
        if note not in _GATE_NOTES:
            _GATE_NOTES.append(note)
    part['k'] = here
    heavy = (job.get('args') or {}).get('walk_faces', _BRG_WALK_FACES)
    land, offsets = float(table.get('ring_land_share', 0.25)), cover_offsets(touch, _BRG_LOOK)
    F, S, T = part.setdefault('f', {}), part.setdefault('s', {}), part.setdefault('t', {})
    pick = part.setdefault('pick', {})

    def face(k, side):
        return _brg_step(part, tape, F, '%d|%s' % (k, side), 'faces', lambda: _brg_face_step(g, A, world[k], side, touch, land, offsets, brg_ids))

    def seat_of(ring, k):
        return _brg_step(part, tape, S, '%s|%d' % (ring, k), 'seats', lambda: _brg_seat_step(g, A, world[k], ring))

    # the part of this unit first: when it neither seats the bearing nor stands at a ring face, the bearing is another
    # part's as soon as one printed neighbour stands at a ring face, and nothing else of it is measured
    own = [k for k in neighbours if world[k]['own']]
    printed = [k for k in neighbours if world[k]['printed'] and not world[k]['own']]
    rest = [k for k in neighbours if not world[k]['own'] and not world[k]['printed']]
    may_hold = False
    for k in own:
        for side in ('+', '-'):
            may_hold = bool(face(k, side)) or may_hold
        for ring in ('inner', 'outer'):
            may_hold = (seat_of(ring, k) is not None) or may_hold
    for k in printed:
        for side in ('+', '-'):
            if face(k, side) and not may_hold:
                return None                                             # another printed part holds it: judged there
    for k in rest:
        for side in ('+', '-'):
            face(k, side)
    for k in printed + rest:
        for ring in ('inner', 'outer'):
            seat_of(ring, k)
    faces, seat, seat_recs = _brg_gather(g, world, neighbours, part)
    holders = [r for r in seat_recs.values() if r is not None] + [r for ring in faces for side in faces[ring] for c in faces[ring][side] for r in c['_recs']]
    if not any(r['own'] for r in holders) and any(r['printed'] for r in holders):
        return None                                                     # another printed part seats and holds it: judged there
    washers = (job.get('params') or {}).get('washers.ref_pattern')
    clamps = []
    for ring in ('inner', 'outer'):
        c = _brg_step(part, tape, T, ring, 'travel', lambda: _brg_clamp(ring, g, A, faces, seat_recs, world, washers, look, pick, tape, heavy))
        if c is not None:
            clamps.append(c)
    return {'ref': g['name'], 'designation': g['designation'], 'by': g['by'], 'd': round(size['d'], 3), 'D': round(size['D'], 3), 'B': round(size['B'], 3),
            'anchor': {'kind': 'bearing', 'axis': list(axis_part), 'point': [round(x, 2) for x in mat_point(inv, size['centre'])]},
            'rings': {ring: {'faces': [{'side': side, 'covered': _brg_public(faces[ring][side])} for side in ('+', '-')]} for ring in ('inner', 'outer')},
            'clamps': clamps, 'seat': seat,
            'hidden_near': sorted(set(world[k]['name'] for k in near if not world[k]['mesh'] and not world[k]['visible'])),
            'mesh_near': sorted(set(world[k]['name'] for k in near if world[k]['mesh']))}


def _brg_key(found):
    """16 hex characters that name the list of the bearings found: the index of a cursor counts in this list."""
    return state_digest([g['key'] for g in found])


def _brg_timing(before, tape, world, prelude_s):
    """(the running sums for the cursor, the "timing" block of the envelope) after this call.
    before: the sums of the calls before this one ({} at the first call)."""
    stages = {k: list(v) for k, v in (before.get('stages') or {}).items()}
    for k, (sec, n) in tape.timing.items():
        row = stages.setdefault(k, [0.0, 0])
        row[0], row[1] = round(row[0] + sec, 3), row[1] + n
    bodies = {k: list(v) for k, v in (before.get('bodies') or {}).items()}
    for tag, (sec, n) in tape.bodies.items():
        rec = world[tag] if isinstance(tag, int) and 0 <= tag < len(world) else None
        name = rec['name'] if rec else str(tag)
        row = bodies.setdefault(name, [0.0, 0, None])
        row[0], row[1] = round(row[0] + sec, 3), row[1] + n
        if row[2] is None and rec is not None:
            try:
                row[2] = rec['body'].faces.count
            except Exception:
                row[2] = None
    sums = {'calls': int(before.get('calls') or 0) + 1, 'prelude_s': round(float(before.get('prelude_s') or 0.0) + prelude_s, 3),
            'stages': stages, 'bodies': bodies}
    slow = sorted(bodies.items(), key=lambda kv: (-kv[1][0], -kv[1][1], kv[0]))[:5]
    block = {'calls': sums['calls'], 'prelude_s': sums['prelude_s'], 'travel_walk_s': round((stages.get('walk') or [0.0, 0])[0], 2),
             'state_s': round((stages.get('state') or [0.0, 0])[0], 2),
             'slowest': [{'name': k, 'faces': v[2], 'pc': v[1], 's': round(v[0], 2)} for k, v in slow]}
    for k in ('faces', 'seats', 'travel'):
        block[k] = {'s': round((stages.get(k) or [0.0, 0])[0], 2), 'pc': (stages.get(k) or [0.0, 0])[1]}
    return sums, block


def probe(job):
    """job  uses "doc", "component_id", "occurrence", "body", "params" ("bearings.ref_pattern", "bearings.contact_gap_max"),
         "tables"["bearings"], "features" ({"holes", "openings"} of the census: the holes with role "bearing_seat" are the seats), "not_printed", "cursor" (see
         below), "deadline_s", "args" ({"tape_limit": n}: stop after n point tests in one call, for tests and slow machines)

    A bearing is near this unit when its bounding box comes within 1 mm of the part's; it is listed when the part seats
    it or stands at one of its ring faces (within 2 mm), or when no printed part does. Its size is read from its geometry
    (bore, outer cylinder, width) and matched to a table row within the table's tolerance. A name alone stands only for
    a body with no ring geometry.

    Returns {"facts": {...}, "done": bool, "cursor", "sig": gate_signature(body)} with facts
      "bearings": [{"ref": "ref bearing 6005 upper", "designation": "6005" or None, "by": "geometry" | "name" | None,
                    "d": 25.0, "D": 47.0, "B": 12.0,
                    "anchor": {"kind": "bearing", "axis": canonical_axis, "point": centre}       part frame
                    "rings": {"inner": {"faces": [F, F]}, "outer": {"faces": [F, F]}}
                          F = {"side": "+" | "-"            along the anchor axis
                               "covered": [{"name": "Hub", "kind": "printed" | "bought" | "hardware", "part": id or None,
                                            "share": 0.0 .. 1.0   of the ring-face circle within the contact gap
                                            "points": n           separate contact runs round the circle (arc_runs)
                                            "gap": 0.0            mm between the cover and the ring face
                                            "touches_other_ring": bool}]}       [] = nothing holds this face
                    "clamps": [{"ring": "outer", "side": "+", "by": "washer" | "printed", "name": "...",
                                "room": 9.0         axial room between the two stops of that ring, mm
                                "ring_width": 12.0}]
                    "seat": {"inner": S or None, "outer": S or None}
                          S = {"d": 47.0, "part": id, "len": 12.0, "entry_d": 47.25, "rows": [9 diameters or None]}    the seat
                          cylinder of a printed part; "rows" is its profile along the axis (seat_scan of _geom_rings.py)}]
      "seats_without_bearing": [{"hole": "h9", "anchor": the census anchor, "d": 47.0, "designation": "6005"}]
               census holes of this part classified as a bearing seat with no bearing in them

    More keys than the contract lists (a judge may read them, none is needed):
      a cover also has "near" (share of the face with the body within 2 mm) and "other_ring_share"; its "gap" is negative
      when the cover is drawn reaching into the ring, and a cover that only comes near has "share" 0.0 and its gap;
      a clamp also has "travel" (mm the clamp can still move before it lands), "travel_capped" (nothing stops it within
      "travel_look"), "lands_on" (what stops it) and "against" (the stop on the other face of the ring);
      a seat also has "name", "kind", "entry_side", "entry_open", "entry_other" (seat_scan of _geom_rings.py);
      a bearing also has "hidden_near" and "mesh_near": bodies beside it that were NOT counted as covers;
      a bearing whose name matched but whose body has no ring geometry has "rings": None and "why";
      a cover has "bearing": True when it is itself one of the bearings found, and "twin": True when that bearing has
      the same bore and outer diameter (the other bearing of a pair: its rings lie on these rings);
      a clamp also has "gap" and "stop_gap" (air between the ring and the clamp, and between the ring and the stop on
      the other face, as drawn), "part" and "stop_part";
      a seat without a bearing also has "match" ("exact" | "band": how the census knew the seat) and "bodies": the
      bought bodies on its axis that are no recognised bearing (a bearing the name patterns miss looks like this);
      facts also hold "touch", "look", "travel_look" (mm: how it was measured), "ring_angles" (points per circle of the
      largest ring), "n_bearings" (bearings near the part), "near_bearings" (names of those another printed part seats
      and holds: not listed here), "not_bearings" (names that point at a bearing and were passed over: no table ring by
      geometry) and "rejected" ([{"name", "why", "d", "D", "B"}]: why, with what was measured).

    The cursor is {"i": index of the next bearing, "key": _brg_key() of the bearings found, "part": the finished steps
    of bearing i, the tape of the step in progress and the bodies it was measured against (see the top; {} between two
    bearings), "timing": the running sums, "restarts": calls in a row that had to start bearing i again because its
    bodies changed (only while that is so)}. A cursor whose "key" is not the list of bearings found now ends the unit
    in an error: the index would point at another bearing. A plain number (a cursor of an older plan) is read as the
    index. "not_bearings", "rejected" and "seats_without_bearing" are written by the first call of a unit only (lists
    are joined over its calls).
    The answer also carries "timing" beside the facts (never in them: no judge reads it), and one line of it in the
    envelope's notes: {"calls": calls of this unit so far, "prelude_s": seconds before the first step (the walk over the
    document, the search for bearings), summed over the calls, "faces" / "seats" / "travel": {"s": seconds inside
    pointContainment, "pc": how many calls of it}, "travel_walk_s": seconds in the walks over the faces of clamp
    bodies (no point test, no clock), "slowest": the five bodies that took the most seconds, each
    {"name", "faces": its face count, "pc", "s"}, "state_s": seconds spent reading the state of the bodies a bearing in
    progress was measured against (revision id, face count, volume, area: at the start and at the end of a call)}."""
    t_start = time.time()
    design = gate_design(job)
    occ, comp, body = gate_body(design, job)
    sig = gate_signature(body)
    m = gate_matrix(occ)
    inv = mat_inverse(m)
    pbody = body.createForAssemblyContext(occ) if occ is not None else body
    part_box = gate_bbox(pbody)
    params, table = job.get('params') or {}, (job.get('tables') or {}).get('bearings') or {}
    pattern = params.get('bearings.ref_pattern') or '(?i)bearing'
    touch = float(params.get('bearings.contact_gap_max', 0.05))
    look = max(3.0, 3.0 * float(params.get('bearings.clamp_room', 1.0)))
    world = gate_world(design, job, part_box, _BRG_FAR, '' if occ is None else occ.fullPathName, body.name)
    found, skipped, rejected = _brg_find(world, pattern, part_box, table, gate_ref_patterns(job))
    brg_ids = {id(r): ((g['size']['d'], g['size']['D']) if g['size'] else None) for g in found for r in g['recs']}
    cur = job.get('cursor')
    first_call = cur is None                                            # lists are joined over the calls of one unit: some are written once
    again = {'was': None, 'n': 0}                                       # calls in a row that started the bearing in progress again
    if isinstance(cur, dict):
        start, part, sums = int(cur.get('i') or 0), cur.get('part'), cur.get('timing')
        part = json.loads(json.dumps(part)) if isinstance(part, dict) else {}       # a copy: the cursor of the job stays as it came
        sums = sums if isinstance(sums, dict) else {}
        again['was'] = cur.get('restarts')
    else:
        start, part, sums = int(cur or 0), {}, {}
    here = _brg_key(found)
    if isinstance(cur, dict) and cur.get('key') != here:                # another list of bearings: the index points at another bearing,
        # and the lists the first call wrote (not_bearings, rejected, seats without a bearing) are of the old list
        raise ValueError('the bearings near the part changed between two calls of this unit (%d found now): start the unit again' % len(found))
    tape = GateTape(job, reserve=_BRG_RESERVE)
    for k, r in enumerate(world):
        if not r['mesh']:
            r['_inside'] = _brg_inside(r, tape, k)
    facts = {'bearings': [], 'near_bearings': [], 'seats_without_bearing': [], 'touch': touch, 'look': _BRG_LOOK, 'travel_look': look, 'n_bearings': len(found),
             'ring_angles': max([24] + [ring_angles(g['size']['D'] / 2.0) for g in found if g['size']]),
             'not_bearings': skipped if first_call else [], 'rejected': rejected if first_call else []}
    if first_call:
        known = set(r['name'] for g in found for r in g['recs'])
        placed = [(mat_point(inv, g['size']['centre']), v_unit(mat_vector(inv, g['size']['axis']))) for g in found if g['size']]
        for h in ((job.get('features') or {}).get('holes') or []):
            cls = h.get('class') or {}
            if cls.get('role') != 'bearing_seat':
                continue
            a = h['anchor']
            if seat_has_bearing(tuple(a['point']), tuple(a['axis']), float(h.get('depth') or 0.0), placed):
                continue
            row = (table.get('sizes') or {}).get(cls.get('kind') or '') or {}
            ds = [s['d'] for s in h.get('segments') or [] if 'd' in s]
            d = min(ds, key=lambda x: abs(x - row['D'])) if (ds and row) else (ds[0] if ds else None)
            facts['seats_without_bearing'].append({'hole': h.get('id'), 'anchor': a, 'd': d, 'designation': cls.get('kind'), 'match': cls.get('match'),
                                                   'bodies': sorted(set(str(c.get('name')) for c in h.get('coaxial') or []
                                                                        if isinstance(c, dict) and c.get('kind') in ('bought', 'hardware') and c.get('name') not in known))})
    prelude_s = time.time() - t_start
    i, late = start, False
    while i < len(found):
        if tape.out_of_time():                                          # between two bearings
            late = True
            break
        try:
            row = _brg_one(found[i], job, world, m, inv, table, touch, look, brg_ids, part, tape, again if i == start else None)
        except GateStop:                                                # inside a step: its answers so far go with the cursor
            part['tape'] = tape.saved()
            late = True
            break
        except _BrgLate:                                                # between two steps
            late = True
            break
        if row is None:
            facts['near_bearings'].append(found[i]['name'])
        else:
            facts['bearings'].append(row)
        part = {}
        i += 1
    if late and part:                                                   # what this bearing was measured against so far (see the top)
        t_state = time.time()
        own = [k for k, r in enumerate(world) if i < len(found) and any(r is x for x in found[i]['recs'])]     # the bearing itself: its size is read again by every call
        part['asked'] = dict(part.get('asked') or {}, **gate_states(world, set(tape.bodies) | set(own)))
        row = tape.timing.setdefault('state', [0.0, 0])
        row[0], row[1] = row[0] + time.time() - t_state, row[1] + len(tape.bodies) + len(own)
    sums, timing = _brg_timing(sums, tape, world, prelude_s)
    note = 'timing: %d call(s), %.1f s before the first step; point tests: faces %.1f s / %d, seats %.1f s / %d, travel %.1f s / %d%s' % (
        timing['calls'], timing['prelude_s'], timing['faces']['s'], timing['faces']['pc'], timing['seats']['s'], timing['seats']['pc'],
        timing['travel']['s'], timing['travel']['pc'],
        ('; slowest body %s (%s faces): %.1f s / %d' % (timing['slowest'][0]['name'], timing['slowest'][0]['faces'], timing['slowest'][0]['s'], timing['slowest'][0]['pc']))
        if timing['slowest'] else '')
    _GATE_NOTES.append(note)
    if late:
        cursor = {'i': i, 'key': here, 'part': part, 'timing': sums}
        if again['n'] and i == start:
            cursor['restarts'] = again['n']
        return {'facts': facts, 'done': False, 'cursor': cursor, 'sig': sig, 'timing': timing}
    return {'facts': facts, 'done': True, 'cursor': None, 'sig': sig, 'timing': timing}
