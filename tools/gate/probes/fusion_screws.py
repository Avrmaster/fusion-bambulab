"""Screw and washer refs of one part: length, engagement, plastic under the head, tip room, washer landing and what stands near it.

Fusion-side unit. Its text is pasted behind _geom.py, _geom_rings.py and _fusion.py (gate_fusion_rpc.build_script), so
their functions are globals here and this file imports nothing from disk. No adsk import is needed: _fusion.py has it.
All work happens inside probe(job), which _fusion.run() calls. Read-only: no timeline edit, no body deleted, temporary
geometry only through adsk.fusion.TemporaryBRepManager. The API is in cm, every fact is in mm.
job["legacy"] maps "insert_pockets", "fusion_parts" and "screw_access" to the absolute path of that tool of
tools/fusion (None when it is not there): a probe that wraps one loads it inside probe() with
g = {"__file__": path}; exec(open(path, encoding="utf-8").read(), g).

How it measures (arithmetic in _geom_rings.py; Fusion is asked "is this point inside that body" behind a box test):
  screw     a name picks the candidates, the geometry decides (screw_claim of _geom_rings.py). Candidates: every body
            that is not printed whose own name matches screws.ref_pattern, and every body with no hardware word in
            its own name whose component name matches it. A body whose own name says washer, insert, nut or bearing
            is never a screw. Bodies of one occurrence on one axis that touch end to end are one screw (a head body
            and a shank body). Head, shank, seat, tip and length come from the circular edges (screw_shape); the size
            from the shank diameter against the table, else from the name. A candidate that only its component name
            picked needs a head on a shank of a table thread; a name that carries another thread than the shank
            measures is not that screw. What is passed over is listed in "not_screws" and "rejected".
  thread    an insert or nut ref on the screw's axis: the stretch they share. Else the longest stretch of the shank
            that lies in a body whose hole is narrower than the thread (8 points just outside the shank, 6 inside).
  the rest  lines along the screw sampled every 0.25 .. 0.5 mm, ends refined by halving (runs_along).
  gap       an air gap between the clamped parts is looked into: a bought solid body in its slab within
            screws.bridge_search_r of the axis (a box test, then circles of points), and, on lines parallel to the
            screw through that body, the air between it and the part on either side (_scr_stack: point tests only).
  washer    every body whose own name matches washers.ref_pattern (it must be a thin ring, else it is listed as
            unreadable), and every thin ring of a row of the washers table with no hardware word in its own name whose
            component name matches. Landing: 24 x 6 points under the washer; the share is counted on
            the circles outside the opening it sits over. Nearest neighbour: Application.measureManager.
            measureMinimumDistance from a temporary cylinder round washer and head to each body within 3 mm that the
            washer does not lie on (the bounding boxes when that call fails: "approx").
  bodies    gate_world() of _fusion.py: printed by the rule of the scan, named as the scan names them. A printed body
            whose name matches a ref pattern is a ref only when its shape is one; an insert or nut is never printed.
  clamp     a washer is a clamp washer when it bears on a bearing. A bearing is a ring of the bearing table by its
            geometry, or a body whose own name says so; a name of the component alone makes no bearing of a screw.
  holes     "fastener_holes": the screw holes and insert pockets of the census, each with the recognised screw on its
            axis or with the bought bodies on its axis that nothing recognised (hardware.recognised compares them).
Limits: a washer drawn in a screw's component under a body name that does not say "washer" is read as part of the
screw (its head comes out too wide); a helical thread drawn as real geometry has few circular edges, the size then
comes from the name; hidden bodies count as material; a bare head over a slot (no washer) is not seen as sitting on
the part."""

_SCR_NEAR = 3.0                 # mm: neighbours of a washer farther than this are not listed
_SCR_RINGS = 6                  # circles of sample points on a washer's landing face, between its hole and its rim
_SCR_GAP_STEP = 1.0             # mm between the circles of points that look for a bought body in an air gap along a screw
_SCR_STACK_LOOK = 3.0           # mm past the end of that body in which the part on that side is looked for (farther: "not met")
_SCR_STACK_SPAN = 60.0          # mm: that body is followed no farther than this from the gap along the screw
_SCR_STACK_LINES = 3            # lines per circle, on the first three circles that meet the body


_SCR_TAPE = None                # the GateTape of this call (_fusion.py): probe() sets it, every point test goes through it


class _ScrLate(Exception):
    """The time of the call ran out inside a screw that is not the first of the call, in a caller that asks without a
    tape: the screw is measured whole by the next call and no half-measured row is stored. probe() asks through a
    GateTape: there the tape stops the screw, wherever it is, and the next call goes on with it."""


def _scr_inside(rec, tag=0):
    """callable(point mm) -> is the point inside the body of this record. A point outside the body's bounding box is
    answered without a call; the others are asked through the tape of the call when probe() has set one."""
    body, lo, hi = rec['body'], rec['box'][0], rec['box'][1]
    IN = adsk.fusion.PointContainment.PointInsidePointContainment

    def inside(p):
        if p[0] < lo[0] or p[0] > hi[0] or p[1] < lo[1] or p[1] > hi[1] or p[2] < lo[2] or p[2] > hi[2]:
            return False
        if _SCR_TAPE is not None:
            return _SCR_TAPE.ask(body, p, tag)
        return body.pointContainment(gate_point(p)) == IN
    return inside


def _scr_circles(body):
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


_SCR_KINDS = ('washer', 'insert', 'nut', 'screw', 'bearing')      # the order in which a body's own name is read


def _scr_own_kind(body_name, pats):
    """What the body's OWN name says it is: "washer", "insert", "nut", "screw", "bearing", or None. The first pattern
    of _SCR_KINDS that matches wins: "washer M3x12x1.5" is a washer although the screw pattern knows "M3x12"."""
    for k in _SCR_KINDS:
        if gate_search(pats[k], body_name):
            return k
    return None


def _scr_pick(world, pats, kinds, taken=(), bought_only=False):
    """The candidate bodies of some kinds: a body whose own name says one of `kinds`, and a body whose own name says
    nothing while its component name matches one of them. Never the part itself, a mesh, or a body in `taken` (ids)."""
    out = []
    for r in world:
        if r['mesh'] or r['own'] or id(r) in taken or (bought_only and r['printed']):
            continue
        if r['hw'] in kinds or (r['hw'] is None and any(gate_search(pats[k], r['comp']) for k in kinds)):
            out.append(r)
    return out


def _scr_close(picked, boxes, reach=1.0, touch=0.5):
    """The picked bodies that can matter for this part, by their boxes alone: a body within `reach` mm of one of
    `boxes`, and a body of the same occurrence that touches such a body (the head body of a shank that reaches the
    part). The circles of a body are read only after this filter: a part has dozens of screw refs within 45 mm, and
    reading every one of them on every call of the unit was the cost before the first screw was measured."""
    near = [r for r in picked if any(box_gap(r['box'][0], r['box'][1], b[0], b[1]) <= reach for b in boxes)]
    have = set(id(r) for r in near)
    out = list(near)
    for r in picked:
        if id(r) not in have and any(n['path'] == r['path'] and box_gap(r['box'][0], r['box'][1], n['box'][0], n['box'][1]) <= touch for n in near):
            out.append(r)
    return out


def _scr_refs(picked, slack):
    """Chosen bodies grouped into single parts: [{"key", "name", "recs", "box", "profile"}], sorted by key.
    slack None: every body is a part of its own (washers stacked on one axis stay apart).
    When the bodies of one occurrence cannot be read at all, each of them is a part of its own with no profile and
    "unread": the reason. The probe lists such a body under "rejected" (a screw) or "unreadable_washers" (a washer)."""
    by_path = {}
    for r in picked:
        by_path.setdefault(r['path'], []).append(r)
    out = []
    for path in sorted(by_path):
        recs = by_path[path]
        try:
            circles = [_scr_circles(r['body']) for r in recs]
            profs = [revolve_profile(c) for c in circles]
            turned = [i for i, p in enumerate(profs) if p]
            if slack is None:
                groups = [[i] for i in turned]
            else:
                groups = [[turned[k] for k in c] for c in
                          cluster_coaxial([(profs[i]['origin'], profs[i]['axis'], profs[i]['lo'], profs[i]['hi']) for i in turned], slack)]
            groups += [[i] for i, p in enumerate(profs) if not p]          # no circular edge: listed, with no profile
            made = [{'key': '%s|%s' % (path, recs[g[0]]['body_name']), 'name': recs[g[0]]['name'], 'recs': [recs[i] for i in g],
                     'box': box_of([c for i in g for c in recs[i]['box']]),
                     'profile': revolve_profile([c for i in g for c in circles[i]])} for g in groups]
        except Exception as e:                                             # the bodies of one occurrence could not be read: each is listed with no
            why = 'its shape could not be read (%s: %s)' % (type(e).__name__, str(e)[:80])     # profile and the reason, never dropped
            made = [{'key': '%s|%s' % (path, r['body_name']), 'name': r['name'], 'recs': [r], 'box': r['box'], 'profile': None, 'unread': why} for r in recs]
        out += made
    out.sort(key=lambda g: g['key'])
    return out


def _scr_bearing_like(r, pats, table):
    """Is a body a bearing to a washer that lies on it? A ring of the bearing table by its geometry; else a body whose
    own name says bearing or carries a designation of the table; else a ring with no hardware word in its own name in
    a component named so. Never a printed body, and never a screw, washer, insert or nut by its own name: the screw
    of a clamp in a component "... 608 clamp" is no bearing."""
    if r['printed'] or r['mesh'] or r['hw'] not in (None, 'bearing'):
        return False
    if '_brg' not in r:
        sizes = (table or {}).get('sizes') or {}
        size = bearing_size(revolve_profile(_scr_circles(r['body'])))
        if size and match_bearing(size['d'], size['D'], size['B'], table or {}):
            r['_brg'] = True
        elif r['hw'] == 'bearing' or designation_in_name(r['body_name'], sizes):
            r['_brg'] = True
        else:
            r['_brg'] = size is not None and (gate_search(pats['bearing'], r['comp']) or designation_in_name(r['comp'], sizes) is not None)
    return r['_brg']


def _scr_ring(centre, axis, r, n=8, phase=0.0):
    u, v = axis_frame(axis)
    return [v_add(centre, v_add(v_scale(u, r * math.cos(2 * math.pi * (k + phase) / n)), v_scale(v, r * math.sin(2 * math.pi * (k + phase) / n))))
            for k in range(n)]


def _scr_wall(inside, centre, axis, s, direction, r_air, r_base):
    """Where the nearest wall begins on a line outwards from the axis, or None: looked for from four distances, so a thin
    rib that ends 1 mm on is found as well as a solid block."""
    found = [radial_edge(inside, centre, axis, s, direction, r_air, r_base + extra) for extra in (6.0, 3.0, 1.5, 0.6)]
    found = [x for x in found if x is not None]
    return min(found) if found else None


def _scr_median(vals):
    vals = sorted(vals)
    return vals[len(vals) // 2] if vals else None


def _scr_engagement(sh, size, table, others, threads, nut_pattern):
    """(engagement mm, {"kind", "name"} or None, the body record it is threaded into or None)."""
    L = v_dot(v_sub(sh['seat'], sh['tip']), sh['axis'])
    best = None
    for t in threads:                                                    # an insert or a nut on the axis
        p = t['profile']
        if not p or not coaxial(sh['tip'], sh['axis'], p['origin'], p['axis'], 0.3):
            continue
        e = sorted(along(v_add(p['origin'], v_scale(p['axis'], s)), sh['tip'], sh['axis']) for s in (p['lo'], p['hi']))
        got = min(L, e[1]) - max(0.0, e[0])
        if got > 0 and (best is None or got > best[0]):
            best = (got, {'kind': 'nut' if gate_search(nut_pattern, t['name']) else 'insert', 'name': t['name']}, t['recs'][0])
    if best:
        return round(best[0], 2), best[1], best[2]
    row = (table.get('sizes') or {}).get(size or '') or {}
    out = sh['shank_r'] + (min(0.15, 0.5 * (row['clearance_d'] / 2.0 - sh['shank_r'])) if row.get('clearance_d') else 0.1)
    solids = [r for r in others if not r['mesh']]

    def owner(s):
        pts = _scr_ring(v_add(sh['tip'], v_scale(sh['axis'], s)), sh['axis'], out)
        for i, r in enumerate(solids):
            if sum(1 for p in pts if r['_inside'](p)) >= 6:
                return i
        return None
    runs = runs_along(owner, 0.0, L, 0.25, 0.02) if L > 0 else []
    if not runs:
        return 0.0, None, None
    i, a, b = max(runs, key=lambda t: t[2] - t[1])
    rec = solids[i]
    return round(b - a, 2), {'kind': 'plastic' if (rec['own'] or rec['printed']) else 'bought_thread', 'name': rec['name']}, rec


def _scr_in_solid(sh, others, into_rec):
    """{"len", "name", "part", "at"}: the longest stretch of the shank whose core lies inside one printed body: at least
    two of five points (the centre line and four at half the shank radius). There the hole is closed, or half closed by
    a body that grew over it. A hole that is there, however narrow (a pilot at 0.8 of the thread), has all five points
    in air and does not count. "at": mm from the head seat to the near end of that stretch (0.0 = it begins right
    under the head: the one-layer skin of a counterbore that prints facing the bed lies there).
    {"len": 0.0, "name": None, "part": None, "at": None} when there is none."""
    A, tip = sh['axis'], sh['tip']
    L = v_dot(v_sub(sh['seat'], tip), A)
    solids = [r for r in others if not r['mesh'] and (r['own'] or r['printed']) and r is not into_rec]
    if into_rec is not None and (into_rec['own'] or into_rec['printed']):
        solids.append(into_rec)                                          # "threaded into plastic" may be a hole that is not there

    def owner(s):
        c = v_add(tip, v_scale(A, s))
        pts = [c] + _scr_ring(c, A, 0.5 * sh['shank_r'], 4)
        for i, r in enumerate(solids):
            if sum(1 for p in pts if r['_inside'](p)) >= 2:
                return i
        return None
    runs = runs_along(owner, 0.0, L, 0.25, 0.02) if (L > 0 and solids) else []
    if not runs:
        return {'len': 0.0, 'name': None, 'part': None, 'at': None}
    i, a, b = max(runs, key=lambda t: t[2] - t[1])
    return {'len': round(b - a, 2), 'name': solids[i]['name'], 'part': solids[i]['part'], 'at': round(max(0.0, L - b), 2)}


def _scr_who(rec):
    return {'name': rec['name'], 'kind': rec['kind'], 'part': rec['part']}


def _scr_stack(body, sides, p, A):
    """What lies on ONE line parallel to the screw through p, a point of an air gap that is inside `body`: [the air
    between the part on the head side and the body, the air between the body and the part on the thread side], mm.
    The body is followed from p to where it ends (towards the head, then towards the tip), and the part on that side
    is looked for from that end on: 0.0 when it begins right there (face on face), the distance when it begins within
    _SCR_STACK_LOOK, None when it does not (the body ends in the open there, or under another body), None when the
    body does not end within _SCR_STACK_SPAN of p, and None when the part is drawn THROUGH the body there (it already
    holds the point 0.3 mm before the body ends: two bodies drawn into each other have no face that presses).
    A load path needs both numbers at about 0 on the same line: part, body, part, with no air along the screw. A ring
    that only sits in a bore of one part, a pin through both parts and a plate that lies on one of them and stops
    short of the other all have air or nothing at one end. Point tests only."""
    out = []
    for side, d in ((sides[0], A), (sides[1], v_scale(A, -1.0))):
        at = lambda s, d=d: v_add(p, v_scale(d, s))
        top = min(_SCR_STACK_SPAN, box_span(body['box'][0], body['box'][1], p, d)[1] + 0.5)
        n = max(1, int(math.ceil(top / 0.5)))
        prev, end = 0.0, None
        for k in range(1, n + 1):
            s = top * k / float(n)
            if not body['_inside'](at(s)):
                end = _rings_last_true(lambda x: body['_inside'](at(x)), prev, s, 0.005)
                break
            prev = s
        if end is None or side['_inside'](at(max(0.0, end - 0.3))):
            out.append(None)
            continue
        hit = first_hit(lambda s: 'part' if side['_inside'](at(s)) else None, end, end + _SCR_STACK_LOOK, 0.25, 0.005)
        out.append(None if hit is None else round(max(0.0, hit[1] - end - 0.005), 2))
    return out


def _scr_stack_rank(pair):
    """Sort key of one line's two numbers: both measured first, then the smaller of the larger number, then the sum."""
    known = [x for x in pair if x is not None]
    return (2 - len(known), max(known) if known else 0.0, sum(known))


def _scr_gaps(gaps, solid, land, A, job, may_stop=False):
    """What stands in each air gap between the clamped parts along one screw.

    gaps   run_gaps() rows wider than face to face: (gap, index of the body before, index of the body after, s where
           the gap starts, counted from the landing towards the tip); the indices point into `solid`
    Returns one row per gap: {"gap", "at", "between": [who, who] (the bodies either side, head side first),
    "search_r": screws.bridge_search_r as it was used, "in_gap": [{"name", "kind", "off", "along": [mm, mm],
    "lines"}]}.
    "in_gap": every bought solid body that lies in the slab of the gap within search_r of the screw axis. A printed
    body, the screw itself and its washers, every recognised screw, washer, insert and nut, and a body whose own name
    says so are left out: only a bearing ring, a sleeve, a bought plate can carry the clamp load of two parts.
    "off": the radius of the first circle of points round the axis, at half the height of the gap, that meets the
    body (circles every _SCR_GAP_STEP mm, 24 points each).
    "along": on the best of "lines" lines parallel to the screw through the body, the air between the part on the
    head side and the body, and between the body and the part on the thread side (_scr_stack; None = that part is not
    met on the line). The lines stand on the first three circles that meet the body (a chamfer at its rim is passed
    by the second), up to _SCR_STACK_LINES on each; the best line is the one with both numbers and the smallest larger
    number. [0.0, 0.0] = the two parts clamp the body between them along the screw.
    "late": True on a row that was not measured because the time of the call was used up ("along" is then
    [None, None]); with may_stop the caller stops instead (_ScrLate). Both only without a tape: probe() asks through a
    GateTape, which stops the screw between two point tests and lets the next call go on, so a unit writes no "late".
    Cost: a box test per body, then point tests for the bodies whose box meets the slab; no distance call."""
    R = float((job.get('params') or {}).get('screws.bridge_search_r', 8.0))
    n_r = max(1, int(math.ceil(R / _SCR_GAP_STEP - 1e-9)))
    down = v_scale(A, -1.0)
    out = []
    for gap, i0, i1, s0 in gaps:
        sides = (solid[i0], solid[i1])
        lo, hi = path_box(v_add(land, v_scale(down, s0)), down, R, gap)
        mid = v_add(land, v_scale(down, s0 + gap / 2.0))
        found = []
        for r in solid:
            if r is sides[0] or r is sides[1] or r['own'] or r['printed'] or r.get('_fast') or r.get('hw') in ('screw', 'washer', 'insert', 'nut'):
                continue
            if box_gap(r['box'][0], r['box'][1], lo, hi) > 0.0:
                continue
            k0 = next((k for k in range(1, n_r + 1) if any(r['_inside'](p) for p in _scr_ring(mid, A, R * k / n_r, 24))), None)
            if k0 is None:
                continue
            row = {'name': r['name'], 'kind': r['kind'], 'off': round(R * k0 / n_r, 2), 'along': [None, None], 'lines': 0}
            best = None
            for k in range(k0, k0 + 3):
                pts = _scr_ring(mid, A, R * k / n_r, 24)
                flags = [r['_inside'](p) for p in pts]
                if not any(flags):
                    continue
                start = next((i for i in range(len(flags)) if flags[i] and not flags[i - 1]), 0)     # the first arc inside the body
                arc = []
                for i in range(len(flags)):
                    if not flags[(start + i) % len(flags)]:
                        break
                    arc.append(pts[(start + i) % len(flags)])
                picks = []
                for i in (len(arc) // 2, len(arc) // 4, (3 * len(arc)) // 4)[:_SCR_STACK_LINES]:
                    if arc[i] not in picks:
                        picks.append(arc[i])
                for p in picks:
                    if _SCR_TAPE is None and gate_time_up(job, 1.5):     # with a tape the tape stops the screw, and the next call goes on
                        if may_stop:
                            raise _ScrLate()
                        row['late'] = True
                        break
                    pair = _scr_stack(r, sides, p, A)
                    row['lines'] += 1
                    if best is None or _scr_stack_rank(pair) < _scr_stack_rank(best):
                        best = pair
                    if None not in best and max(best) <= 0.02:
                        break                                           # contact on both sides is proven: no further line
                if row.get('late') or (best is not None and None not in best and max(best) <= 0.02):
                    break
            if best is not None and not row.get('late'):
                row['along'] = best
            found.append(row)
        found.sort(key=lambda f: (f['off'], f['name']))
        out.append({'gap': round(gap, 2), 'at': round(s0, 2), 'between': [_scr_who(sides[0]), _scr_who(sides[1])], 'search_r': R, 'in_gap': found})
    return out


def _scr_tip_at(sh, solids):
    """{"name", "kind", "part"} of the body the end of the thread stands in, or None: the first solid body that holds
    at least half of a circle of eight points round the axis, 1 mm behind the tip, looked for 1, 2 and 3.5 mm outside
    the shank (the wall of a pocket up to 7 mm wider than the thread). Asked only for a screw whose thread nothing
    receives: the judge then names the part in which an insert or a nut is missing."""
    L = v_dot(v_sub(sh['seat'], sh['tip']), sh['axis'])
    c = v_add(sh['tip'], v_scale(sh['axis'], min(1.0, max(0.0, 0.5 * L))))
    for extra in (1.0, 2.0, 3.5):
        pts = _scr_ring(c, sh['axis'], sh['shank_r'] + extra)
        for r in solids:
            if sum(1 for p in pts if r['_inside'](p)) >= 4:
                return _scr_who(r)
    return None


def _scr_screw(g, job, world, own, inv, washers, threads, table, may_stop=False):
    """The facts of one screw. A screw whose head and thread are both elsewhere (it passes through this part, or
    belongs to a neighbour) comes back as {"passing": True, "ref", "size", "anchor"}."""
    sh = screw_shape(g['profile'])
    named = g.get('named') or thread_in_name(g['name'])
    if sh is None:                                                       # a name without a readable shape: never dropped
        c = tuple((g['box'][0][i] + g['box'][1][i]) / 2.0 for i in range(3))
        return {'ref': g['name'], 'size': named[0], 'length': None, 'named_length': named[1], 'head': None,
                'anchor': {'kind': 'screw', 'axis': [0.0, 0.0, 1.0], 'point': [round(x, 2) for x in mat_point(inv, c)]},
                'role': 'thread', 'under_head': None, 'seat': None, 'engagement': None, 'into': None, 'tip_room': None, 'air_gap': None,
                'in_solid': None, 'washer': None, 'why': 'no screw shape: no coaxial circular edges'}
    A, seat, tip = sh['axis'], sh['seat'], sh['tip']
    size, head = screw_size(sh, table)
    size = size or named[0]
    mine = set(id(r) for r in g['recs'])
    under = [w for w in washers if w['shape'] and coaxial(seat, A, w['shape']['centre'], w['shape']['axis'], 0.3)
             and -(w['shape']['t'] + 3.0) <= along(w['shape']['centre'], seat, A) <= 0.1]
    under.sort(key=lambda w: -along(w['shape']['centre'], seat, A))
    drop = min([0.0] + [along(w['shape']['centre'], seat, A) - w['shape']['t'] / 2.0 for w in under])     # landing below the seat
    land = v_add(seat, v_scale(A, drop))
    skip = mine | set(id(r) for w in under for r in w['recs'])
    others = [r for r in world if id(r) not in skip and box_gap(r['box'][0], r['box'][1], g['box'][0], g['box'][1]) <= 12.0]
    r_mid = sh['shank_r'] + 0.7 * (sh['head_r'] - sh['shank_r'])
    radii = [r_mid]
    if under:                                                            # the washer spreads the head: over a slot or an oversize
        ws = under[-1]['shape']                                          # hole only the washer's ring finds the plastic
        r_mid = ws['id'] / 2.0 + 0.85 * (ws['od'] - ws['id']) / 2.0
        radii = [ws['id'] / 2.0 + 0.5 * (ws['od'] - ws['id']) / 2.0, r_mid]
    in_own = own['_inside']
    head_on = (not sh['headless']) and any(sum(1 for p in _scr_ring(v_sub(land, v_scale(A, off)), A, r) if in_own(p)) >= 4
                                           for r in radii for off in (0.1, 0.6, 1.1))      # also a head drawn up to 1 mm above its seat
    engagement, into, into_rec = _scr_engagement(sh, size, table, others, [t for t in threads if not any(id(r) in skip for r in t['recs'])],
                                                 gate_ref_patterns(job)['nut'])
    thread_on = False
    if into_rec is not None:
        if into_rec['own']:
            thread_on = True
        elif into and into['kind'] in ('insert', 'nut'):                 # is that insert or nut held by this part
            b = into_rec['box']
            c = tuple((b[0][i] + b[1][i]) / 2.0 for i in range(3))
            r_out = max(off_axis(k, tip, A) for k in box_corners(b[0], b[1])) / math.sqrt(2.0) + 0.4
            mid = v_add(tip, v_scale(A, along(c, tip, A)))
            thread_on = sum(1 for p in _scr_ring(mid, A, r_out) if in_own(p)) >= 4
    if not head_on and not thread_on:
        return {'passing': True, 'ref': g['name'], 'size': size,
                'anchor': {'kind': 'screw', 'axis': [round(c, 4) for c in v_unit(mat_vector(inv, A))], 'point': [round(c, 2) for c in mat_point(inv, seat)]}}
    L = along(seat, tip, A)
    out = {'ref': g['name'], 'size': size, 'length': round(sh['length'], 2), 'named_length': named[1], 'named_size': named[0], 'head': head,
           'head_d': round(2 * sh['head_r'], 2), 'head_h': round(sh['head_h'], 2), 'thread_d': round(2 * sh['shank_r'], 2),
           'anchor': {'kind': 'screw', 'axis': [round(c, 4) for c in v_unit(mat_vector(inv, A))], 'point': [round(c, 2) for c in mat_point(inv, seat)]},
           'role': 'both' if (head_on and thread_on) else ('head' if head_on else 'thread'),
           'under_head': None, 'seat': None, 'engagement': engagement, 'into': into, 'tip_room': None, 'air_gap': None,
           'in_solid': _scr_in_solid(sh, others, into_rec), 'washer': under[0]['id'] if under else None}
    solid = [r for r in others if not r['mesh']]
    if into is None and engagement == 0.0:
        out['tip_at'] = _scr_tip_at(sh, solid)                           # nothing receives the thread: in which body does it end
    if head_on:
        deep = min(60.0, max(1.0, box_span(own['box'][0], own['box'][1], land, v_scale(A, -1.0))[1] + 0.5))
        lens = []
        for p in _scr_ring(land, A, r_mid):
            runs = runs_along(lambda s, p=p: 'own' if in_own(v_sub(p, v_scale(A, s))) else None, 0.05, deep, 0.5, 0.02)
            if runs and runs[0][1] <= 0.3:
                lens.append(runs[0][2])                                  # from the landing to where the plastic ends
            else:
                lens.append((runs[0][2] - runs[0][1]) if (runs and runs[0][1] <= 1.2) else 0.0)
        out['under_head'] = round(_scr_median(lens), 2)
        out['under_head_min'] = round(min(lens), 2)
        d_ring = sh['head_r'] - sh['shank_r']
        pts = _scr_ring(v_sub(land, v_scale(A, 0.05)), A, sh['shank_r'] + 0.45 * d_ring) + _scr_ring(v_sub(land, v_scale(A, 0.05)), A, sh['shank_r'] + 0.9 * d_ring, 8, 0.5)
        share = round(sum(1 for p in pts if in_own(p)) / float(len(pts)), 3)
        top = along(sh['top'], land, A)                                  # head top above the landing
        if sh['countersunk']:
            out['seat'] = {'kind': 'countersink', 'd': round(2 * sh['head_r'], 2), 'recess': 0.0, 'share': share}
        else:
            u, v = axis_frame(A)
            dirs = [v_add(v_scale(u, math.cos(k * math.pi / 4)), v_scale(v, math.sin(k * math.pi / 4))) for k in range(8)]
            r_top = max([sh['head_r']] + [w['shape']['od'] / 2.0 for w in under])
            walls = [_scr_wall(in_own, land, A, min(0.5, top / 2.0), d, r_top + 0.02, r_top) for d in dirs]
            walls = [w for w in walls if w is not None]
            if len(walls) >= 6:
                rw = _scr_median(walls)
                hs = []
                for d in dirs[::2]:
                    p = v_add(land, v_scale(d, rw + 0.3))
                    runs = runs_along(lambda s, p=p: 'own' if in_own(v_add(p, v_scale(A, s))) else None, 0.05, top + 5.0, 0.5, 0.02)
                    hs.append((runs[0][2]) if (runs and runs[0][1] <= 0.3) else 0.0)
                out['seat'] = {'kind': 'counterbore', 'd': round(2 * rw, 2), 'recess': round(_scr_median(hs) - top, 2), 'share': share}
            else:
                out['seat'] = {'kind': 'flat', 'd': round(2 * sh['head_r'], 2), 'recess': round(-top, 2), 'share': share}
        r_g = max(0.9 * sh['head_r'], sh['shank_r'] + 1.0)
        span = along(land, tip, A)

        def owner(s):                                                    # what the clamped stack is made of, from the landing down
            c = v_sub(land, v_scale(A, s))
            pts = _scr_ring(c, A, r_g, 4) + _scr_ring(c, A, r_g + 1.5, 4, 0.5) + _scr_ring(c, A, r_g + 3.0, 4)
            for i, r in enumerate(solid):
                if sum(1 for p in pts if r['_inside'](p)) >= 4:
                    return i
            return None
        gaps = run_gaps(runs_along(owner, 0.05, max(0.3, span), 0.25, 0.01)) if span > 0.3 else []
        widest = max([0.0] + [gp[0] for gp in gaps])
        out['air_gap'] = round(widest, 2) if widest > 0.025 else 0.0     # under the halving step: face to face
        out['gaps'] = _scr_gaps([gp for gp in gaps if gp[0] > 0.025], solid, land, A, job, may_stop)      # what stands in each of them
    # past the tip: the centre line and four points at half the shank radius
    blockers = [r for r in solid if not (into_rec is r and into and into['kind'] in ('insert', 'nut'))]

    def blocked(s):                                                      # s mm past the tip (negative: back towards the head)
        c = v_sub(tip, v_scale(A, s))
        pts = [c] + _scr_ring(c, A, 0.5 * sh['shank_r'], 4)
        for r in blockers:
            if any(r['_inside'](p) for p in pts):
                return r['name']
        return None
    if blocked(0.05) is not None:
        free = first_hit(lambda s: None if blocked(-s) is not None else 'air', 0.0, max(0.5, L), 0.25, 0.02)
        out['tip_room'] = round(-(free[1] if free else L), 2)
        out['tip_in'] = blocked(0.05)
    else:
        hit = first_hit(blocked, 0.05, 10.0, 0.25, 0.02)
        out['tip_room'] = round(hit[1], 2) if hit else 10.0
        out['tip_room_capped'] = hit is None
    return out


def _scr_washer(w, job, world, own, inv, screws, threads, touch):
    """The facts of one washer, or None when it does not lie on this part and has no shape."""
    sp = w['shape']
    if sp is None:
        return None
    mine = set(id(r) for r in w['recs'])
    on_it = [s for s in screws if s['shape'] and coaxial(sp['centre'], sp['axis'], s['shape']['seat'], s['shape']['axis'], 0.3)
             and -0.1 <= along(s['shape']['seat'], sp['centre'], s['shape']['axis']) <= sp['t'] + 3.0]
    solid = [r for r in world if id(r) not in mine and not r['mesh'] and box_gap(r['box'][0], r['box'][1], w['box'][0], w['box'][1]) <= _SCR_NEAR + 0.5]
    skip = mine | set(id(r) for s in on_it for r in s['recs'])
    bodies = [r for r in solid if id(r) not in skip]
    ring = (sp['od'] - sp['id']) / 2.0
    if on_it:
        A = on_it[0]['shape']['axis']
    else:                                                                # no screw on it: the head side is the bare side
        A = sp['axis']
        brg = (job.get('tables') or {}).get('bearings')                  # a washer lies on printed plastic or on a bearing, never on a head
        lands = [r for r in bodies if r['printed'] or r['own'] or _scr_bearing_like(r, gate_ref_patterns(job), brg)]
        count = lambda d: sum(1 for p, _, _ in annulus_points(v_add(sp['centre'], v_scale(d, sp['t'] / 2.0 + 0.07)), d, sp['id'] / 2.0 + 0.1 * ring,
                                                               sp['od'] / 2.0 - 0.02 * ring, 16, 4)
                              if any(r['_inside'](p) for r in lands))      # the whole ring: a clamp washer reaches its bearing with its rim only
        if count(A) > count(v_scale(A, -1.0)):
            A = v_scale(A, -1.0)
    down = v_scale(A, -1.0)
    face = v_add(sp['centre'], v_scale(down, sp['t'] / 2.0))
    r_lo, r_hi = sp['id'] / 2.0 + 0.15 * ring, sp['od'] / 2.0 - 0.05 * ring
    pts = annulus_points(face, A, r_lo, r_hi, 24, _SCR_RINGS)
    look = max(2.0, float((job.get('params') or {}).get('bearings.clamp_room', 1.0)) + 0.5)
    offsets = cover_offsets(touch, look)
    pats = gate_ref_patterns(job)
    on_axis = set(id(r) for t in threads for r in t['recs'] if t['profile'] and coaxial(sp['centre'], A, t['profile']['origin'], t['profile']['axis'], 0.3))
    covers = []
    for r in bodies:
        if id(r) in on_axis:
            continue                                                     # the insert or nut of its own screw is no landing
        cov = face_cover(pts, r['_inside'], down, offsets, touch)
        if cov is not None:
            is_brg = _scr_bearing_like(r, pats, (job.get('tables') or {}).get('bearings'))
            covers.append({'name': r['name'], 'kind': r['kind'], 'part': r['part'], 'share': cov['share'], 'gap': cov['gap'],
                           'bearing': is_brg, '_rec': r})
    if not any(c['_rec']['own'] for c in covers):
        return None
    covers.sort(key=lambda c: (-c['share'], c['gap'], c['name']))
    total = face_cover(pts, lambda p: any(c['_rec']['_inside'](p) for c in covers), down, offsets, touch)
    in_touch = [v_scale(down, o) for o in offsets[:2]]
    share, open_d, ring_shares = landing_band(pts, lambda p: any(c['_rec']['_inside'](v_add(p, d)) for d in in_touch for c in covers), r_lo, r_hi)
    on_bearing = next((c['name'] for c in covers if c['bearing'] and c['gap'] <= touch), None)
    centre = sp['centre']
    u, v = axis_frame(A)
    dirs = [v_add(v_scale(u, math.cos(k * math.pi / 8)), v_scale(v, math.sin(k * math.pi / 8))) for k in range(16)]
    under = [c['_rec'] for c in covers if c['gap'] <= touch] or [c['_rec'] for c in covers]     # what it lies on: their walls
    landing = lambda p: any(r['_inside'](p) for r in under)
    walls = [_scr_wall(landing, centre, A, 0.0, d, sp['id'] / 2.0 + 0.5 * ring, sp['od'] / 2.0) for d in dirs]
    walls = [x for x in walls if x is not None]
    head_h = max([0.0] + [along(s['shape']['top'], face, A) - sp['t'] for s in on_it])
    height = sp['t'] + head_h
    # the nearest body the stack does not lie on: one temporary cylinder round washer and head, least distance to each
    TB = adsk.fusion.TemporaryBRepManager.get()
    r_env = max([sp['od'] / 2.0] + [s['shape']['head_r'] for s in on_it])
    env = TB.createCylinderOrCone(gate_point(v_add(face, v_scale(A, 0.02))), r_env / 10.0, gate_point(v_add(face, v_scale(A, height))), r_env / 10.0)
    env_box = gate_bbox(env)
    lying = set(id(c['_rec']) for c in covers if c['gap'] <= touch)
    lying |= set(id(r) for t in threads for r in t['recs'] if t['profile'] and coaxial(centre, A, t['profile']['origin'], t['profile']['axis'], 0.3))
    mm, nearest = adsk.core.Application.get().measureManager, None
    for r in world:
        if id(r) in skip or id(r) in lying or box_gap(r['box'][0], r['box'][1], env_box[0], env_box[1]) > _SCR_NEAR:
            continue
        gap, approx = box_gap(r['box'][0], r['box'][1], env_box[0], env_box[1]), True       # never more than the real gap
        if not r['mesh']:
            try:                                                        # whether the measure manager takes a temporary body is not sure:
                gap, approx = mm.measureMinimumDistance(env, r['body']).value * 10.0, False     # when it does not, the boxes stand
            except Exception:
                pass
        if gap <= _SCR_NEAR and (nearest is None or gap < nearest['gap']):
            nearest = {'name': r['name'], 'kind': r['kind'], 'part': r['part'], 'gap': round(gap, 2), 'approx': approx}
    best = covers[0]
    return {'id': w['id'], 'ref': w['name'], 'od': round(sp['od'], 2), 'id_d': round(sp['id'], 2), 't': round(sp['t'], 2),
            'anchor': {'kind': 'face', 'axis': [round(c, 4) for c in v_unit(mat_vector(inv, A))], 'point': [round(c, 2) for c in mat_point(inv, face)]},
            'landing': {'share': share, 'share_all': total['share'] if total else 0.0, 'gap': total['gap'] if total else None,
                        'open_d': open_d, 'ring_shares': ring_shares, 'on_bearing': on_bearing, 'look': look,
                        'on': best['part'] or best['name'], 'spot_face_d': round(2 * min(walls), 2) if walls else None,
                        'wall_angles': len(walls), 'covers': [{k: c[k] for k in c if not k.startswith('_')} for c in covers]},
            'proud': {'height': round(height, 2), 'nearest': nearest}, 'screw': on_it[0]['name'] if on_it else None}


def _scr_holes(job, world, m, screws, known, pats):
    """The screw holes and insert pockets of the census, with what is on the axis of each:
    [{"hole", "role", "kind", "anchor", "screw": ref of the recognised screw on its axis or None,
      "bodies": [names of the bodies on its axis that are not printed and that nothing recognised]}].
    m: part frame -> document frame. known: ids of the bodies taken as screw, washer, insert or nut.
    A dowel or pin by its name, a bearing and a body whose own name says what it is are known too."""
    by_name = {}
    for r in world:
        by_name.setdefault(r['name'], []).append(r)
    brg = (job.get('tables') or {}).get('bearings')
    out = []
    for h in ((job.get('features') or {}).get('holes') or []):
        cls, a = h.get('class') or {}, h.get('anchor') or {}
        if cls.get('role') not in ('clearance', 'counterbore', 'countersink', 'insert_pocket') or not a.get('point'):
            continue
        p, ax = mat_point(m, tuple(a['point'])), v_unit(mat_vector(m, tuple(a['axis'])))
        on = [g for g in screws if g['shape'] and coaxial(p, ax, g['shape']['seat'], g['shape']['axis'], 0.6)
              and box_gap(g['box'][0], g['box'][1], p, p) <= float(h.get('depth') or 0.0) + 12.0]
        other = []
        for c in h.get('coaxial') or []:
            if not isinstance(c, dict) or c.get('kind') not in ('bought', 'hardware'):
                continue
            recs = by_name.get(c.get('name')) or []
            if recs and all(id(r) in known or r.get('hw') or gate_search(_GATE_HW, r['body_name']) or _scr_bearing_like(r, pats, brg) for r in recs):
                continue
            if not recs and gate_hardware(str(c.get('name')), job):
                continue
            other.append(c.get('name'))
        out.append({'hole': h.get('id'), 'role': cls.get('role'), 'kind': cls.get('kind'), 'anchor': a,
                    'screw': on[0]['name'] if on else None, 'bodies': sorted(set(str(n) for n in other))})
    return out


def probe(job):
    """job  uses "doc", "component_id", "occurrence", "body", "params" ("screws.ref_pattern", "washers.ref_pattern",
         "washers.landing_gap_max", "screws.bridge_search_r"), "tables" ("screws", "washers", "inserts"), "features" ({"holes", "openings"} of the census),
         "not_printed", "cursor" (see the end), "deadline_s", "args" ({"tape_limit": n}: stop after n point tests in one call)

    A screw belongs to this unit when its head sits on this part or its thread ends in this part.

    Returns {"facts": {...}, "done": bool, "cursor", "sig": gate_signature(body)} with facts
      "screws": [{"ref": "ref screw M3x12 cap (3)", "size": "M3", "length": 12.0, "head": "cap" | "button" | "countersunk" | None,
                  "anchor": {"kind": "screw", "axis": tip -> head, "point": centre of the head seat}      part frame
                  "role": "head" | "thread" | "both"            which end of the screw is in this part
                  "under_head": 5.2 or None        plastic of this part under the head along the screw, mm
                  "seat": {"kind": "counterbore" | "flat" | "countersink", "d": 6.5, "recess": 1.2, "share": 1.0} or None
                  "engagement": 3.4 or None        threaded length inside the insert, nut or tapped part, mm
                  "into": {"kind": "insert" | "nut" | "bought_thread" | "plastic", "name": "ref insert M3x4x5"} or None
                  "tip_room": 1.2 or None          free length past the tip to the end of the hole (negative: past it)
                  "air_gap": 0.0                   air between the clamped parts along the screw, mm
                  "in_solid": {"len": 0.0, "name": None, "part": None, "at": None}      the longest stretch of the shank
                                                   whose core lies in printed plastic (the hole is closed or half closed
                                                   there), in which body, and how far from the head seat it begins
                  "washer": "w1" or None}]
      "washers": [{"id": "w1", "ref": "ref washer M3 large", "od": 9.0, "id_d": 3.2, "t": 0.8,
                   "anchor": {"kind": "face", "axis": towards the head, "point": centre of the washer's landing face},
                   "landing": {"share": 1.0 (of the washer ring on material within washers.landing_gap_max, counted
                                         outside the opening it sits over; "share_all": over the whole ring),
                               "open_d": 6.85 or None (about the diameter of that opening: its screw hole, a pocket mouth
                                         with its relief; None = contact right up to the washer's own hole),
                               "on_bearing": name of the bearing ref it bears on (a clamp washer) or None,
                               "gap": 0.0, "on": "demo/housing" | name of what it lies on, "spot_face_d": 13.2 or None},
                   "proud": {"height": 3.15 (washer + head above the landing),
                             "nearest": {"name": "...", "kind": "printed" | "bought" | "hardware", "part": id or None,
                                         "gap": 0.6} or None}}]

    How the values are meant:
      "length" is measured (under the head; a countersunk screw with its head); "named_length" and "named_size" are what
      the ref's name says ("M3x12"), so a judge can tell a 12 named as a 10. "head_d", "head_h", "thread_d" are measured.
      "engagement" is 0.0 with "into": None when the thread meets no insert, no nut and no hole narrower than itself.
      "under_head" is the median of eight lines under the head ("under_head_min": the least); with a washer it is
      measured from under the washer.
      "seat"."recess": how far the top of the head lies under the surface round a counterbore (negative: it stands out;
      a flat seat is minus the head height). "seat"."d" of a flat seat is the head diameter.
      "tip_room" is capped at 10.0 ("tip_room_capped"); a negative value comes with "tip_in": the body the tip is in.
      "air_gap", "under_head" and "seat" are None for a screw whose head is not on this part.
      "gaps": [{"gap", "at", "between": [{"name", "kind", "part"} x 2], "search_r", "in_gap": [{"name", "kind", "off",
      "along": [mm, mm], "lines", "late"}]}], written with "air_gap": one row per air gap between the clamped parts ("at": mm
      under the landing), the two bodies either side, and the bought solid bodies that stand in the slab of the gap
      within screws.bridge_search_r of the axis, each with the air between it and the part on either side on a line
      parallel to the screw (_scr_gaps, _scr_stack). A bearing ring the two parts clamp between them reads
      "along": [0.0, 0.0]: screws.shell_bears does not take that gap for air. [] = no gap. "late": True marked a
      body that was not measured because the time of a call was used up; a unit does not write it any more (a screw
      that runs out of time goes on in the next call), a judge still reads it in facts stored before.
      "tip_at": {"name", "kind", "part"} or None, only on a screw with "engagement" 0.0 and "into" None: the body the
      end of the thread stands in (_scr_tip_at).
      A screw ref without a readable shape is listed with "role": "thread", "engagement": None and "why".
      A washer is listed when it lies on this part. "landing"."covers" lists every body under it with its share and gap
      (looked for "landing"."look" mm deep: 2.0, or bearings.clamp_room + 0.5 when that is more) and "bearing": True
      for a bearing ref; the insert or nut on the washer's own axis is left out. "landing"."ring_shares" are the
      shares of the six sample circles, hole side first.
      "not_screws": names the screw pattern picked and the geometry refused: a printed body whose shape is no screw of
      the table, a plain cylinder in a hardware component (a magnet), a shank of another thread than its name says.
      "rejected": [{"name", "as": "screw" | "washer", "why"}] says why for each of them.
      "passing": [{"ref", "size", "anchor"}]: recognised screws near the part whose head and thread are both elsewhere.
      "fastener_holes": [{"hole", "role", "kind", "anchor", "screw", "bodies"}]: every clearance hole, counterbore,
      countersink and insert pocket of the census with the recognised screw on its axis ("screw", else None) and the
      bought bodies on its axis that nothing recognised ("bodies"). A hole with "bodies" and no "screw" means the
      probe may be blind there: hardware.recognised says so, and "this part has no screws" is no answer.
      "landing"."spot_face_d" is twice the distance to the nearest wall of the bodies it lies on, looked for round the
      washer at mid thickness within 6 mm, in "wall_angles" of 16 directions (a wall on one side counts: that is where
      the washer would not fit); None on an open face. "proud"."nearest" looks 3 mm far and leaves out what the washer
      lies on, its screw and an insert or nut on its axis; a mesh neighbour is measured by its bounding box and has
      "approx": True.
      facts also hold "n_screws" and "n_washers" (the refs near the part, of which the lists hold those that belong to
      it) and "unreadable_washers": washer refs near the part whose body has no ring shape, so nothing was measured.
    The cursor is the index of the next ref in: screws, then washers; or {"i": that index, "tape": the answers of the
    point tests of ref i so far, "world": gate_rows_key() of the bodies round the part, "asked": gate_states() of the
    ref's own bodies and of the bodies that gave those answers} when the time of a call ran out inside a screw or a washer. A later call whose
    bodies are not those (one moved, grew, was hidden, or changed inside its box) measures that ref again from its
    start, with a note; the third such call in a row ends the unit in an error. One screw is thousands of
    point tests against the part and every body within 12 mm, which bodies with thousands of faces do not answer within
    one call: every point test goes through a GateTape of _fusion.py, the clock is read every 16 tests, and the next
    call goes on with the same ref. Its facts are the facts of one call. "unreadable_washers", "not_screws", "rejected"
    and "fastener_holes" are written by the first call of a unit only."""
    design = gate_design(job)
    occ, comp, body = gate_body(design, job)
    sig = gate_signature(body)
    inv = mat_inverse(gate_matrix(occ))
    pbody = body.createForAssemblyContext(occ) if occ is not None else body
    part_box = gate_bbox(pbody)
    params, tables = job.get('params') or {}, job.get('tables') or {}
    touch = float(params.get('washers.landing_gap_max', 0.05))
    world = gate_world(design, job, part_box, 45.0, '' if occ is None else occ.fullPathName, body.name)
    for k, r in enumerate(world):
        if not r['mesh']:
            r['_inside'] = _scr_inside(r, k)
    own = [r for r in world if r['own']]
    if not own:
        raise KeyError('the body of %r was not found among the bodies of the document' % (job.get('part'),))
    pats = gate_ref_patterns(job)
    table = tables.get('screws') or {}
    for r in world:
        r['hw'] = None if r['mesh'] else _scr_own_kind(r['body_name'], pats)
    near = lambda g: box_gap(g['box'][0], g['box'][1], part_box[0], part_box[1]) <= 1.0
    screws, not_screws, rejected = [], [], []
    for g in _scr_refs(_scr_close(_scr_pick(world, pats, ('screw',)), [part_box]), 0.2):
        if not near(g):
            continue
        shape = screw_shape(g['profile'])
        named = next((t for t in (thread_in_name(r['name']) for r in g['recs']) if t[0]), (None, None))
        own_name = any(r['hw'] == 'screw' for r in g['recs'])
        ok, why, size, _ = screw_claim(shape, table, named[0], own_name)
        if ok and all(r['printed'] for r in g['recs']) and (shape is None or screw_size(shape, table)[0] is None):
            ok, why = False, 'a printed body that carries the word, and no screw of the table by its shape'     # "Leadscrew cover"
        if not ok:
            not_screws.append(g['name'])
            rejected.append({'name': g['name'], 'as': 'screw', 'why': g.get('unread') or why})
            continue
        g['shape'], g['named'] = shape, named
        screws.append(g)
    taken = set(id(r) for g in screws for r in g['recs'])
    washers, unreadable = [], []
    for g in _scr_refs(_scr_close(_scr_pick(world, pats, ('washer',), taken), [part_box], touch=-1.0), None):
        if not near(g):
            continue
        shape, own_name, printed = washer_shape(g['profile']), g['recs'][0]['hw'] == 'washer', all(r['printed'] for r in g['recs'])
        if own_name and shape is None and not printed:
            unreadable.append(g['name'])                                 # it says washer and is no thin ring: never dropped
            g['shape'] = None
            washers.append(g)
        elif shape is not None and (own_name or washer_row(shape, tables.get('washers') or {}) is not None):
            g['shape'] = shape                                           # picked by its component only: a ring of the washers table
            washers.append(g)
        elif own_name or shape is not None:
            rejected.append({'name': g['name'], 'as': 'washer', 'why': 'no thin ring' if shape is None else
                             'a ring D%g x %g that is no row of the washers table, and its own name does not say washer' % (round(shape['od'], 2), round(shape['t'], 2))})
    for k, g in enumerate(washers):
        g['id'] = 'w%d' % (k + 1)
    taken |= set(id(r) for g in washers for r in g['recs'])
    # an insert or a nut matters where a screw of this part can reach it: at the part, or at one of its screws
    reach = [part_box] + [g['box'] for g in screws]
    threads = [t for t in _scr_refs(_scr_close(_scr_pick(world, pats, ('insert', 'nut'), taken, bought_only=True), reach, touch=-1.0), None)
               if t['recs'][0]['hw'] in ('insert', 'nut') or bearing_size(t['profile']) is not None]     # by its component only: it must be a ring
    for g in screws + washers + threads:
        for r in g['recs']:
            r['_fast'] = True                                            # a recognised fastener carries no clamp load across a gap
    items = [('screw', g) for g in screws] + [('washer', g) for g in washers]
    cur = job.get('cursor')
    first_call = cur is None                                             # lists are joined over the calls of one unit: some are written once
    saved = cur.get('tape') if isinstance(cur, dict) else None           # the ref an earlier call began
    start = int((cur.get('i') if isinstance(cur, dict) else cur) or 0)
    again = 0                                                            # calls in a row that had to start ref `start` again
    here = gate_rows_key(world)
    if saved is not None and (cur.get('world') != here or not gate_states_same(cur.get('asked'), world)):
        # the bodies are not the ones the answers of that tape were given by: the ref from its start, never two states joined
        saved = None
        name = str(items[start][1].get('name')) if start < len(items) else 'ref %d' % (start + 1)
        again = gate_again(cur.get('again'), name, GATE_MOVED)
        note = 'the bodies round the part changed between two calls of this unit: %s is measured again from its start' % name
        if note not in _GATE_NOTES:
            _GATE_NOTES.append(note)
    facts = {'screws': [], 'washers': [], 'passing': [], 'n_screws': len(screws), 'n_washers': len(washers),
             'unreadable_washers': unreadable if first_call else [], 'not_screws': not_screws if first_call else [],
             'rejected': rejected if first_call else [],
             'fastener_holes': _scr_holes(job, world, gate_matrix(occ), screws, taken | set(id(r) for t in threads for r in t['recs']), pats) if first_call else []}
    global _SCR_TAPE
    tape = _SCR_TAPE = GateTape(job, reserve=3.0)
    i = start
    while i < len(items):
        if i > start and (gate_time_up(job, 3.0) or tape.out_of_time()):
            return {'facts': facts, 'done': False, 'cursor': i, 'sig': sig}
        kind, g = items[i]

        def one():
            if kind == 'screw':
                return _scr_screw(g, job, world, own[0], inv, washers, threads, table)
            return _scr_washer(g, job, world, own[0], inv, screws, threads, touch)
        mine = saved if i == start else None
        try:
            try:
                tape.begin(mine)
                row = one()
                tape.end()
            except (GateReplay, ValueError) as e:                        # not the questions the tape answers: this ref from its start
                if mine is None:
                    raise
                again = gate_again(cur.get('again'), str(g.get('name')))
                note = '%s was measured again from its start: %s' % (g.get('name'), str(e)[:120])
                if note not in _GATE_NOTES:
                    _GATE_NOTES.append(note)
                tape.begin(None)
                row = one()
                tape.end()
        except GateStop:                                                 # out of time inside this ref: its answers go with the cursor
            mine = [k for k, r in enumerate(world) if any(r is x for x in g['recs'])]      # the ref itself: its shape is read again by every call
            state = {'i': i, 'tape': tape.saved(), 'world': here, 'asked': gate_states(world, set(tape.tags) | set(mine))}
            if i == start and again:
                state['again'] = again
            return {'facts': facts, 'done': False, 'cursor': state, 'sig': sig}
        if kind == 'screw':
            facts['passing' if row.get('passing') else 'screws'].append({k: v for k, v in row.items() if k != 'passing'})
        elif row:
            facts['washers'].append(row)
        i += 1
    return {'facts': facts, 'done': True, 'cursor': None, 'sig': sig}
