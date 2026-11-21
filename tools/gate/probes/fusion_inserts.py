"""Plastic round every heat-set pocket of one part: walls at the lead-in, along the insert and at the relief depth, the floor, the relief.
Uses the ray logic of tools/fusion/insert_pockets.py (wall(), _relief_d()).

Fusion-side unit. Its text is pasted behind _geom.py, _geom_rings.py and _fusion.py (gate_fusion_rpc.build_script), so
their functions are globals here and this file imports nothing from disk. No adsk import is needed: _fusion.py has it.
All work happens inside probe(job), which _fusion.run() calls. Read-only: no timeline edit, no body deleted, temporary
geometry only through adsk.fusion.TemporaryBRepManager. The API is in cm, every fact is in mm.
job["legacy"] maps "insert_pockets", "fusion_parts" and "screw_access" to the absolute path of that tool of
tools/fusion (None when it is not there): a probe that wraps one loads it inside probe() with
g = {"__file__": path}; exec(open(path, encoding="utf-8").read(), g).

The ray logic is the one of wall() in insert_pockets.py, written again here so that it works on EVERY pocket of the
census (wall() finds only pockets that already have the right shape) and in the part frame: 48 rays round the axis, each
walked outwards from the pocket surface in 0.1 mm steps until the first point that is not inside the body; the last step
is then halved until the wall is known to 0.01 mm, because the judges compare it with the limit exactly. What is new:
a ring at the relief depth, the floor, a relief read from the census segments, and the look beyond a thin wall at each
depth from that depth's own exit (wall() started every depth at the thinnest one and so could name the part's own
material at another depth a cavity), and the part's own material further out than the wall target (wall() calls a wall
in front of a 5 mm wide slot "free"). The file is not loaded with exec: a vendored copy may be older than this probe.
The pure parts (levels, summary, relief room, mesh test) are in _geom.py."""

_INS_RAY = {'n': 48, 'step': 0.1, 'max': 8.0, 'coarse_step': 0.5, 'refine': 0.005}


_INS_TAPE = None                # the GateTape of this call (_fusion.py): probe() sets it, every point test goes through it


def _ins_in(body, p, tag=0):
    """Is the point inside the body? tag: 0 for the part itself, 1 + its place in env["solids"] for a neighbour (the tape
    of the call keeps which bodies a pocket asked)."""
    if _INS_TAPE is not None:
        return _INS_TAPE.ask(body, p, tag)
    return body.pointContainment(gate_point(p)) == adsk.fusion.PointContainment.PointInsidePointContainment


def _ins_printed(path, comp_name, body_name, job):
    names = [n.rsplit(':', 1)[0] for n in (path or '').split('+') if n] + [comp_name, body_name]
    return all(gate_printed(n, job) for n in names)


def _ins_kind(name, job):
    """"hardware" (a screw, washer, bearing, nut or insert ref, by the name patterns of the parameters) or "bought", for a
    body that is not printed"""
    return 'hardware' if gate_hardware(name, job) else 'bought'


def _ins_walk(body, centre, dirs, r0, start, fine_to, ray):
    """one ring of rays at one depth: per ray the mm of plastic from the radius r0 outwards to the first point that is
    not inside the body. Steps of ray["step"] up to fine_to, of ray["coarse_step"] beyond, never past ray["max"]; the
    last step is then halved until the exit is known to ray["refine"] mm, and its outer end is the wall (so a wall is
    never read too small)."""
    out = []
    for d in dirs:
        t, last = start, 0.0
        while t < ray['max'] and _ins_in(body, v_add(centre, v_scale(d, r0 + t))):
            last = t
            t += ray['step'] if t < fine_to else ray['coarse_step']
        if t >= ray['max'] and (last >= ray['max'] or _ins_in(body, v_add(centre, v_scale(d, r0 + ray['max'])))):
            out.append(ray['max'])                      # plastic all the way to the cap
            continue
        lo, hi = last, min(t, ray['max'])
        while hi - lo > ray['refine'] and hi > start:
            mid = (lo + hi) / 2.0
            if _ins_in(body, v_add(centre, v_scale(d, r0 + mid))):
                lo = mid
            else:
                hi = mid
        out.append(0.0 if hi <= start else hi)          # no plastic at the first point: the wall is broken open here
    return out


def _ins_env(design, occ, body, job, margin):
    """What can stand beside this part: {"m": part -> document matrix, "solids": [(name, kind, part, hidden, body in the
    document frame, lo, hi)], "meshes": [(name, hidden, document -> mesh matrix, mesh_grid)], "mesh_rows": [[name,
    hidden, number of node coordinates, box]] of the same meshes} of every other body whose box comes within `margin`
    mm of the part's box."""
    root = design.rootComponent
    own = occ.fullPathName if occ is not None else None
    m_own = gate_matrix(occ)
    lo, hi = gate_bbox(body)
    plo, phi = box_transform(m_own, lo, hi)
    solids, meshes, mesh_rows = [], [], []
    for o in [None] + list(root.allOccurrences):
        comp = o.component if o is not None else root
        path = o.fullPathName if o is not None else None
        bodies = [b for b in (o.bRepBodies if o is not None else root.bRepBodies) if b.isSolid]      # an occurrence gives its bodies in the document frame
        n_mesh = comp.meshBodies.count
        if not bodies and not n_mesh:
            continue
        shown = True if o is None else bool(o.isLightBulbOn)
        printed = [bool(b.isLightBulbOn) and _ins_printed(path, comp.name, b.name, job) for b in bodies]
        for b, is_part in zip(bodies, printed):
            if path == own and b.name == body.name:
                continue
            blo, bhi = gate_bbox(b)
            if not boxes_near(blo, bhi, plo, phi, margin):
                continue
            if is_part:
                name = part_name(comp.name, b.name, sum(printed))
                kind, pid = 'printed', '%s/%s' % (job.get('doc_key'), part_slug(name))
            else:
                name = comp.name if len(bodies) + n_mesh == 1 else '%s / %s' % (comp.name, b.name)
                kind, pid = _ins_kind(name, job), None
            solids.append((name, kind, pid, not (shown and bool(b.isLightBulbOn)), b, blo, bhi))
        if n_mesh:
            m = gate_matrix(o)
            for mb in comp.meshBodies:
                try:
                    dm = mb.displayMesh
                    c, idx = dm.nodeCoordinatesAsDouble, dm.nodeIndices
                except Exception:
                    continue                            # a mesh that was never drawn has no display mesh: it cannot be seen here
                if not len(c):
                    continue
                mlo = tuple(min(c[i + k] for i in range(0, len(c), 3)) * 10.0 for k in range(3))
                mhi = tuple(max(c[i + k] for i in range(0, len(c), 3)) * 10.0 for k in range(3))
                dlo, dhi = box_transform(m, mlo, mhi)
                if boxes_near(dlo, dhi, plo, phi, margin):
                    name = comp.name if len(bodies) + n_mesh == 1 else '%s / %s' % (comp.name, mb.name)
                    meshes.append((name, not (shown and bool(mb.isLightBulbOn)), mat_inverse(m), mesh_grid(c, idx, 5.0, 10.0)))
                    mesh_rows.append([name, not (shown and bool(mb.isLightBulbOn)), len(c), [round(x, 3) for x in dlo], [round(x, 3) for x in dhi]])
    return {'m': m_own, 'solids': solids, 'meshes': meshes, 'mesh_rows': mesh_rows}


def _ins_env_key(env):
    """16 hex characters for what stands beside the part: every solid of the env with its place in the list, name, kind,
    hidden or shown, and box to 0.001 mm, and every mesh with its name, size and box."""
    return state_digest([[[n, s[0], s[1], bool(s[3]), [round(c, 3) for c in s[5]], [round(c, 3) for c in s[6]]] for n, s in enumerate(env['solids'])],
                         env.get('mesh_rows') or []])


def _ins_recs(env, body):
    """The bodies behind the tags of _ins_in(), as gate_states() of _fusion.py reads them: the part, then the solids of the env."""
    return [{'body': body, 'mesh': False}] + [{'body': s[4], 'mesh': False} for s in (env['solids'] if env else [])]


def _ins_beyond(env, body, box, centre, d, r_exit, r_need, skip, part):
    """What stands outside a thin wall in one direction at one depth: None (nothing: the outline of the part is free
    there), or {"name", "kind", "part", "gap", "hidden"}.
    Other bodies count between the wall and the radius it should reach: looked at 0.2 mm outside the wall, then every
    0.3 mm, and at that radius itself; "gap" is the air between the wall and the body, refined to 0.02 mm.
    The part's own material counts as far as its bounding box goes (every 0.5 mm past that radius): air and then the
    part again is a hole, slot or pocket of its own, not a free outline. Its "gap" is 0 (the cavity begins at the wall)
    and "width" says how wide the cavity is along the ray."""
    spots, t = [], r_exit + 0.2
    while t < r_need - 1e-9:
        spots.append(t)
        t += 0.3
    spots.append(max(r_need, r_exit + 0.05))            # always the radius the wall should reach itself: a sliver there counts
    own = {'name': 'own cavity', 'kind': 'own_cavity', 'part': part, 'gap': 0.0, 'hidden': False}
    before = r_exit
    for t in spots:
        p = v_add(centre, v_scale(d, t))
        if _ins_in(body, p):
            return dict(own, width=round(t - r_exit, 2))
        pd = mat_point(env['m'], p)
        hit = None
        for n, (name, kind, pid, hidden, b, lo, hi) in enumerate(env['solids']):
            if name not in skip and box_has(lo, hi, pd) and _ins_in(b, pd, n + 1):
                hit = ({'name': name, 'kind': kind, 'part': pid, 'hidden': hidden}, lambda q, b=b, n=n: _ins_in(b, q, n + 1))
                break
        if hit is None:
            for name, hidden, inv, grid in env['meshes']:
                if name not in skip and mesh_contains(grid, mat_point(inv, pd)):
                    hit = ({'name': name, 'kind': 'mesh', 'part': None, 'hidden': hidden}, lambda q, inv=inv, grid=grid: mesh_contains(grid, mat_point(inv, q)))
                    break
        if hit is not None:
            lo_t, hi_t = before, t                      # where between the last free point and this one does the body begin
            while hi_t - lo_t > 0.02:
                mid = (lo_t + hi_t) / 2.0
                if hit[1](mat_point(env['m'], v_add(centre, v_scale(d, mid)))):
                    hi_t = mid
                else:
                    lo_t = mid
            return dict(hit[0], gap=round(max(0.0, lo_t - r_exit), 2))
        before = t
    t = spots[-1] + 0.5
    while True:
        p = v_add(centre, v_scale(d, t))
        if not box_has(box[0], box[1], p):
            return None
        if _ins_in(body, p):
            return dict(own, width=round(t - r_exit, 2))
        t += 0.5


def _ins_level(get_env, body, box, axis, dirs, rings, thin, ray, skip, part):
    """the W of one level from its rings [(centre, r0, walls)]: least wall per ray over the rings, and what stands beyond
    every thin ray"""
    n = len(dirs)
    walls = [min(r[2][k] for r in rings) for k in range(n)]
    beyond = [None] * n
    for k in range(n):
        if round(walls[k], 2) >= thin - 1e-9:
            continue
        beyond[k] = 'free'
        for centre, r0, w in rings:
            if round(w[k], 2) >= thin - 1e-9:
                continue
            hit = _ins_beyond(get_env(), body, box, centre, dirs[k], r0 + w[k], r0 + thin, skip, part)
            if hit:
                beyond[k] = hit
                break
    out = wall_summary(walls, axis, thin, beyond, ray['max'])
    out['from_d'] = round(2.0 * rings[0][1], 3)
    return out


def _ins_floor(body, segs, point, axis, depth, cap):
    """least plastic under a blind pocket: on the axis and, under a flat bottom, on six points at 0.6 of its radius"""
    end = v_sub(point, v_scale(axis, depth))
    last = segs[-1]
    starts = [end]
    if last.get('d2') is None and last['d'] > 0.6:
        starts += [v_add(end, v_scale(ray_direction(axis, k, 6), 0.3 * last['d'])) for k in range(6)]
    best = None
    for q in starts:
        t, found = 0.05, None
        while t <= cap:
            step = 0.1 if t < 3.0 else 0.5
            if not _ins_in(body, v_sub(q, v_scale(axis, t))):
                found = max(0.0, t - step / 2.0)
                break
            t += step
        found = float(cap) if found is None else found
        best = found if best is None else min(best, found)
    return round(best, 2)


_INS_M = None                   # part -> document matrix of the unit's part (probe() sets it): the frame _ins_reads() notes regions in


def _ins_reads(point, axis, levels, thin_wall, thin_relief):
    """Note the region the env is asked about for one pocket (gate_read of _fusion.py): _ins_beyond() looks at other
    bodies only on rays square to the axis, from the centre of a level out to its radius plus the wall it should
    have (thin_wall, thin_relief for the relief level), and nowhere else. So the facts of a pocket depend on the bodies
    in the cylinder round its levels, not on everything near the part."""
    if _INS_M is None or not levels:
        return
    reach = max(float(lv['from_r']) + (thin_relief if lv.get('name') == 'relief' else thin_wall) for lv in levels) + 0.55
    ends = [v_sub(point, v_scale(axis, float(lv['depth']))) for lv in levels]
    lo = tuple(min(e[i] for e in ends) - reach for i in range(3))
    hi = tuple(max(e[i] for e in ends) + reach for i in range(3))
    gate_read(box_transform(_INS_M, lo, hi), 0.0)


def _ins_asked(env, tape):
    """Note every other body the tape asked (tag n + 1 = solid n of the env), so _fusion.py can check it lies in a
    region _ins_reads() noted."""
    if not env:
        return
    for t in tape.bodies:
        if isinstance(t, int) and not isinstance(t, bool) and 1 <= t <= len(env['solids']):
            s = env['solids'][t - 1]
            gate_asked(s[0], (s[5], s[6]))


def _ins_pocket(get_env, body, box, h, row, tol_d, thin_wall, thin_relief, ray, part):
    """the facts of one pocket (see probe)"""
    here = h.get('at') or {}
    point = tuple(here.get('point') or h['anchor']['point'])
    axis = v_unit(tuple(here.get('axis') or h['anchor']['axis']))
    start = 0.03 if here else 0.08                      # an anchor is rounded to 0.05 mm: start further out so no ray begins in the hole
    segs = h['segments']
    plan = pocket_levels(segs, row, tol_d)
    n = ray['n']
    dirs = [ray_direction(axis, k, n) for k in range(n)]
    rel_row = row.get('relief') or {}
    rel_ds = [float(d) for d in [rel_row.get('d')] + list(rel_row.get('fallback_d') or []) if d is not None]
    rings = {}
    for lv in plan['levels']:
        if lv['name'] == 'relief':                      # fine steps as far as any relief of the table plus its wall can reach
            fine_to = max([d / 2.0 - lv['from_r'] for d in rel_ds] + [0.0]) + thin_relief + 0.3
        else:
            fine_to = thin_wall + 0.3
        centre = v_sub(point, v_scale(axis, lv['depth']))
        rings.setdefault(lv['name'], []).append((centre, lv['from_r'], _ins_walk(body, centre, dirs, lv['from_r'], start, fine_to, ray)))
    _ins_reads(point, axis, plan['levels'], thin_wall, thin_relief)
    skip = set(c.get('name') for c in h.get('coaxial') or [])                # the insert or screw that belongs in this hole
    walls = {'lead_in': _ins_level(get_env, body, box, axis, dirs, rings['lead_in'], thin_wall, ray, skip, part),
             'insert': _ins_level(get_env, body, box, axis, dirs, rings['insert'], thin_wall, ray, skip, part),
             'relief': _ins_level(get_env, body, box, axis, dirs, rings['relief'], thin_relief, ray, skip, part) if plan['relief'] else None}
    _, r_rel, w_rel = rings['relief'][0]
    through = bool(h.get('through'))
    return {'hole': h.get('id'), 'end': h.get('end') or 'mouth', 'anchor': h['anchor'], 'kind': (h.get('class') or {}).get('kind'), 'match': (h.get('class') or {}).get('match'),
            'segments': segs, 'recess': plan['recess'], 'relief': plan['relief'], 'relief_room': relief_room([r_rel + w for w in w_rel], r_rel, rel_ds),
            'walls': walls, 'floor': None if through else _ins_floor(body, segs, point, axis, h['depth'], ray['max']),
            'depth': h.get('depth'), 'through': through, 'levels': plan['levels'],
            'ray': {'n': n, 'step': ray['step'], 'max': ray['max'], 'coarse_step': ray['coarse_step'], 'refine': ray['refine'], 'fine_to': round(thin_wall + 0.3, 2),
                    'start': start, 'thin_wall': thin_wall, 'thin_relief': thin_relief}}


def probe(job):
    """job  uses "doc", "component_id", "occurrence", "body", "features" ({"holes": [...], "openings": [...]} of this part's
         census: only the holes with class.role "insert_pocket" are measured), "tables"["inserts"], "not_printed", "cursor" (index into the pocket
         list of pocket_ends()), "deadline_s"

    Returns {"facts": {...}, "done": bool, "cursor", "sig": gate_signature(body)} with facts
      "pockets": [{"hole": "h1", "anchor": the census anchor, "kind": "M3x4x5", "segments": as in the census,
                   "relief": {"d": 7.5, "depth": 0.54} or None       the step of a table relief diameter in front of the lead-in
                   "recess": {"d": 13.2, "depth": 1.0} or None       a wider first step that is no relief (a spot face): the
                             pocket begins at its floor, and "relief" says whether it has its relief there
                   "relief_room": [{"d": 7.5, "wall": 1.9}, {"d": 6.5, "wall": 2.4}, {"d": 5.9, "wall": 2.7}]
                             the least wall that stays (or would stay) beside a relief of each table diameter
                   "walls": {"lead_in": W, "insert": W, "relief": W or None (no relief)}
                             W = {"min": 2.6        least plastic from the pocket surface outwards, mm, capped at "ray"."max"
                                  "dir": [x, y, z]  unit direction of that thinnest wall, part frame
                                  "free_arc_deg": 140   arc round the pocket where the wall is under the cap
                                  "blocked_by": [{"name": "ref bearing 6005", "kind": "bought" | "hardware" | "printed" |
                                                  "own_cavity" | "mesh", "part": id or None, "gap": 0.2}]}
                                                  what stands in the thin direction within the room the wall would need
                   "floor": 2.1 or None                              plastic under a blind pocket; None = through
                   "ray": {"n": 48, "step": 0.1, "max": 8.0}}]       how it was measured
    A wall is measured to the first exit from the body; "insert" is the least of three depths along the insert.

    Said more exactly, and more keys than the list above:
      "thin" is a ray whose wall is under job["params"]["inserts.wall_target"] (the relief level:
      "inserts.relief_wall_min"); the number used is in W "thin_under" and in "ray" ("thin_wall", "thin_relief"). With no
      such parameter in the job every ray under "ray"."max" is thin. The probe decides nothing with it: it only chooses
      which rays to look beyond.
      W "free_arc_deg" = arc of THIN rays with nothing within the room the wall would need (the outline is free there:
      the wall can be made wider); "thin_arc_deg" = arc of all thin rays; "blocked_by" = what stands beyond the other thin
      rays (each also has "hidden": its light bulb is off); "min_beyond" = what stands in the direction of the least
      wall (None: not thin, "free", or a name). Another body blocks when it stands within the room the wall would need.
      The part itself blocks ("own cavity", kind "own_cavity") when its material comes again ANYWHERE further out along
      the ray: the air behind the wall is then a hole, slot or pocket of the part, not its outline; its "gap" is 0 and
      "width" is the width of the cavity; "w" = the wall of every ray; "from_d" = the diameter the walls are
      measured from. A ray is walked in "ray"."step" steps ("ray"."coarse_step" past "ray"."fine_to") and its last step
      is halved down to "ray"."refine": a wall is known to 0.01 mm and never read too small.
      "lead_in" and "relief" are measured from the hole's own wall. "insert" is measured from the lead-in diameter (or
      the hole's own wall where that is wider) at 0.6, 0.8 and 1.0 of the insert length below the relief floor: the
      knurl melts the plastic round the lead-in diameter. "levels" lists the depths and radii (pocket_levels).
      A body named in the hole's census "coaxial" list (the insert or screw that belongs there) is never a blocker.
      "floor" is the least of the axis and six points under a flat bottom, capped at "ray"."max".
      Also "match" (the census class match: "exact" | "band"), "depth", "through".
      "end": "mouth" | "exit". A through hole whose census class has "ends": 2 (the table's pocket at each end: a post
      with an insert in both ends) gives two pockets with the same "hole" id: the second one is measured from the far
      mouth ("end": "exit", its anchor is the far mouth with the axis turned round, its segments run from there).
      The cursor counts pockets, not holes: the index of the next pocket, or {"i": that index, "tape": the answers of
      the point tests of pocket i so far, "env": _ins_env_key() of what stood beside the part (None while no thin
      wall made the pocket look beyond itself), "asked": gate_states() of the bodies that gave those answers} when
      the time of a call ran out inside a pocket. A later call whose neighbours are not those (one moved, grew, was
      hidden, or changed inside its box) measures that pocket again from its start, with a note; the third such call
      in a row ends the unit in an error. The part itself is held by its signature.
    A part whose signature is not job["sig"] any more is not measured: {"stale": true}, "done" false, the new "sig".
    job["args"]: {"limit": n} at most n pockets in this call; "ray_n", "ray_step", "ray_max", "ray_coarse_step", "ray_refine" override
    the rays; {"tape_limit": n} stop after n point tests in one call (tests, slow machines).
    A call stops before a pocket when less time is left than 1.5 times the slowest pocket so far (at least 3 s).
    One pocket is some 12 000 point tests on the part itself (48 rays at five depths, 0.1 mm steps), which a part with
    thousands of faces does not answer within one call. So every point test goes through a GateTape of _fusion.py: the
    clock is read every 16 tests, a pocket that runs out of time stops with its answers in the cursor, and the next
    call goes on with the same pocket. The walls of a pocket measured over several calls are the walls of one call."""
    design = gate_design(job)
    occ, comp, body = gate_body(design, job)
    sig = gate_signature(body)
    feats = job.get('features')
    if feats is None:
        raise ValueError('no census in the job: fusion_census must run on this part first')
    if job.get('sig') and job['sig'] != sig:
        return {'facts': {'stale': True}, 'done': False, 'cursor': job.get('cursor'), 'sig': sig}
    ins = (job.get('tables') or {}).get('inserts') or {}
    kinds = ins.get('kinds') or {}
    tol_d = float((ins.get('tol') or {}).get('d', 0.1))
    params, args = job.get('params') or {}, job.get('args') or {}
    ray = dict(_INS_RAY)
    ray.update({k: args['ray_' + k] for k in _INS_RAY if 'ray_' + k in args})
    thin_wall = float(params.get('inserts.wall_target') or ray['max'])
    thin_relief = float(params.get('inserts.relief_wall_min') or ray['max'])
    cache = {}

    def get_env():
        if 'env' not in cache:
            cache['env'] = _ins_env(design, occ, body, job, max(thin_wall, thin_relief) + 1.0)
        return cache['env']
    box = gate_bbox(body)
    todo = []                                           # one record per pocket: a hole with a pocket at each end gives two (pocket_ends)
    for h in feats.get('holes') or []:
        if (h.get('class') or {}).get('role') == 'insert_pocket':
            todo += pocket_ends(h)
    pockets, slowest = [], 0.0
    cur = job.get('cursor')
    saved = cur.get('tape') if isinstance(cur, dict) else None          # the pocket an earlier call began
    i = i0 = int((cur.get('i') if isinstance(cur, dict) else cur) or 0)
    limit = args.get('limit')
    again = 0                                                           # calls in a row that had to start pocket i0 again
    if saved is not None and cur.get('env') is not None:
        # that pocket looked at its neighbours: they must be the ones, and in the state, its answers were given by
        if _ins_env_key(get_env()) != cur['env'] or not gate_states_same(cur.get('asked'), _ins_recs(get_env(), body)):
            saved = None
            name = 'pocket %s' % (todo[i0].get('id') if i0 < len(todo) else i0 + 1)
            again = gate_again(cur.get('again'), name, GATE_MOVED)
            note = 'the bodies round the part changed between two calls of this unit: %s is measured again from its start' % name
            if note not in _GATE_NOTES:
                _GATE_NOTES.append(note)
    global _INS_TAPE, _INS_M
    tape = _INS_TAPE = GateTape(job, reserve=2.5)
    _INS_M = gate_matrix(occ)
    while i < len(todo):
        if i > i0 and (gate_left(job) < max(3.0, 1.5 * slowest) or tape.out_of_time() or (limit is not None and i - i0 >= int(limit))):
            break
        t0 = time.time()
        h = todo[i]
        row = kinds.get((h.get('class') or {}).get('kind'))
        if row is None:
            raise ValueError('pocket %s: no insert kind %r in the table of this job' % (h.get('id'), (h.get('class') or {}).get('kind')))
        mine = saved if i == i0 else None
        try:
            try:
                tape.begin(mine)
                pocket = _ins_pocket(get_env, body, box, h, row, tol_d, thin_wall, thin_relief, ray, job.get('part'))
                tape.end()
            except (GateReplay, ValueError) as e:                       # not the questions the tape answers: this pocket from its start
                if mine is None:
                    raise
                again = gate_again(cur.get('again'), 'pocket %s' % h.get('id'))
                note = 'pocket %s was measured again from its start: %s' % (h.get('id'), str(e)[:120])
                if note not in _GATE_NOTES:
                    _GATE_NOTES.append(note)
                tape.begin(None)
                pocket = _ins_pocket(get_env, body, box, h, row, tol_d, thin_wall, thin_relief, ray, job.get('part'))
                tape.end()
        except GateStop:                                                # out of time inside this pocket: its answers go with the cursor
            env = cache.get('env')                                      # None: this pocket has asked the part alone so far
            state = {'i': i, 'tape': tape.saved(), 'env': _ins_env_key(env) if env else None,
                     'asked': gate_states(_ins_recs(env, body), [t for t in tape.tags if t]) if env else {}}
            if i == i0 and again:
                state['again'] = again
            _ins_asked(env, tape)
            return {'facts': {'pockets': pockets}, 'done': False, 'cursor': state, 'sig': sig}
        pockets.append(pocket)
        slowest = max(slowest, time.time() - t0)
        i += 1
    _ins_asked(cache.get('env'), tape)
    done = i >= len(todo)
    return {'facts': {'pockets': pockets}, 'done': done, 'cursor': None if done else i, 'sig': sig}
