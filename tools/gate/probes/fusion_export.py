"""Tessellate one part to a quarantine STL and check the mesh against the body.

The only unit with a side effect: it writes the STL file named in the job.

Fusion-side unit. Its text is pasted behind _geom.py, _geom_rings.py and _fusion.py (gate_fusion_rpc.build_script), so
their functions are globals here and this file imports nothing from disk. No adsk import is needed: _fusion.py has it.
All work happens inside probe(job), which _fusion.run() calls. Read-only: no timeline edit, no body deleted, temporary
geometry only through adsk.fusion.TemporaryBRepManager. The API is in cm, every fact is in mm.
job["legacy"] maps "insert_pockets", "fusion_parts" and "screw_access" to the absolute path of that tool of
tools/fusion (None when it is not there): a probe that wraps one loads it inside probe() with
g = {"__file__": path}; exec(open(path, encoding="utf-8").read(), g).

Mesh: a fresh MeshCalculator tessellation of a TemporaryBRepManager copy of the component's own body. Not the
occurrence's proxy body (its mesh has come out torn after edits of a base-feature body) and not displayMeshes.bestMesh
(its density depends on what was last drawn). Qualities are tried in turn, as tools/fusion/fusion_parts.py does: one may
give a non-manifold edge where faces touch along an arc; no further quality is started when less than a third of the
budget is left.
Frame: the mesh is made and checked in the part frame (the component's own), then its vertices are moved with the
occurrence's matrix, so the FILE is in the document frame, exactly as fusion_parts.py writes it (it meshes the
occurrence's proxy body). A project whose plates were built from fusion_parts STLs keeps its orientations. A part at the
root of the document, or under an identity occurrence, is the same in both frames. facts "frame" says "document".
Checks: one shell, closed, volume within 1 %, bounding box, and a SAMPLE of the facets against the body (a point just
outside each sampled facet is outside the body, a point just inside is inside; fusion_parts.check() tests every facet
and measures the distance of the failing ones; here at most _EXP_FACETS facets spread over the mesh are tested, and the
facts say how many: "facets_checked" of "triangles"). A small filled hole between the sampled facets is NOT proven
absent by this unit: the B-rep signature binds the file to the checked geometry, and the wall check runs on the file.
Nothing here saves, activates or changes the document. The file is written under a temporary name and renamed."""

_EXP_QUALITY = ('High', 'Normal', 'VeryHigh')
_EXP_BOX_TOL = 0.1              # mm: mesh box against body box. A chord cuts a curve short by less than this.
_EXP_FACETS = 400               # facets tested against the body (two pointContainment calls each, more for a miss)
_EXP_OFF = (0.03, 0.1)          # mm off the facet, as fusion_parts: the second try forgives the chord error of a curved face
_EXP_DIST = 0.1                 # mm: a facet whose centre is farther than this from every face of the body is not the body's surface


def _exp_facets(body, T, job):
    """(facets tested, [[centroid, distance mm or None]] of those that are not on the body's surface, at most 20).
    T is in the frame of `body`. A facet passes when a point just outside it is outside the body and a point just
    inside is inside (at 0.03 mm, else at 0.1 mm). One that fails both (a facet beside a sharp edge can) is measured
    against the faces as fusion_parts.check() does: farther than 0.1 mm from all of them is surface the body does not
    have. A measurement that cannot be made counts as a miss (distance None). Stops early when the budget runs out."""
    IN, OUT = adsk.fusion.PointContainment.PointInsidePointContainment, adsk.fusion.PointContainment.PointOutsidePointContainment
    mm = adsk.core.Application.get().measureManager
    step = max(1, len(T) // _EXP_FACETS)
    n, bad, measured = 0, [], 0
    for i in range(0, len(T), step):
        if n and n % 25 == 0 and gate_time_up(job, 2.0):
            break
        a, b, c = T[i]
        nrm = v_cross(v_sub(b, a), v_sub(c, a))
        if v_len(nrm) < 1e-6:
            continue
        nrm = v_unit(nrm)
        q = tuple((a[k] + b[k] + c[k]) / 3.0 for k in range(3))
        n += 1
        if any(body.pointContainment(gate_point(v_add(q, v_scale(nrm, e)))) == OUT and
               body.pointContainment(gate_point(v_sub(q, v_scale(nrm, e)))) == IN for e in _EXP_OFF):
            continue
        dist = None
        if measured < 40:                               # one call per face of the body: only for the few that miss
            measured += 1
            try:
                dist = min(mm.measureMinimumDistance(f, gate_point(q)).value for f in body.faces) * 10.0
            except Exception:
                dist = None
            if dist is not None and dist <= _EXP_DIST:
                continue
        bad.append([[round(x, 2) for x in q], None if dist is None else round(dist, 2)])
        if len(bad) >= 20:
            break
    return n, bad


def _exp_tris(mesh):
    # under 1e-9 mm = 0: the two sides of a revolved face's seam come out as 0 and r * sin(2 pi) = 1e-15, and stay unwelded
    p, ii = [0.0 if abs(c) < 1e-10 else c * 10 for c in mesh.nodeCoordinatesAsFloat], mesh.nodeIndices
    return [tuple((p[3 * k], p[3 * k + 1], p[3 * k + 2]) for k in ii[t:t + 3]) for t in range(0, len(ii), 3)]


def _exp_shells(T):
    idx, par = {}, []

    def f(x):
        while par[x] != x:
            par[x] = par[par[x]]; x = par[x]
        return x
    for t in T:
        ids = [idx.setdefault(v, len(idx)) for v in t]
        while len(par) < len(idx):
            par.append(len(par))
        par[f(ids[0])] = f(ids[1]); par[f(ids[1])] = f(ids[2])
    return len({f(i) for i in range(len(par))})


def _exp_open_edges(T):
    """Edges not shared by exactly two triangles in opposite directions (welded on exact float values)."""
    E = {}
    for t in T:
        for u, v in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            E[(u, v)] = E.get((u, v), 0) + 1
    return sum(1 for (u, v), n in E.items() if n != 1 or E.get((v, u)) != 1)


def _exp_whole(body):
    """What Fusion gives for a body as a whole, in one read each: volume, area, the counts of faces, edges and vertices,
    and the bounding box. The same values the signature starts from (gate_sign_done). Any edit of the body moves one of
    them, or it moves a face, and then the area or the box moves too."""
    bb = body.boundingBox
    return (round(body.volume * 1000.0, 6), round(body.area * 100.0, 6), body.faces.count, body.edges.count, body.vertices.count,
            tuple(round(c, 6) for c in gate_mm(bb.minPoint)), tuple(round(c, 6) for c in gate_mm(bb.maxPoint)))


def probe(job):
    """job  uses "doc", "component_id", "occurrence", "body", "sig" (the signature the part was checked at), "args"
         ({"stl": absolute path under gate/run/quarantine/, "name": part name for the STL header}), "deadline_s"

    The signature is computed from every face before the tessellation. After it the body is read as a whole again
    (_exp_whole): nothing else runs inside one script call, so a body whose volume, area, counts and box are the same
    still has the signature it had; only when one of them moved is every face read a second time. (Two full loops
    over a body with thousands of faces cost more than the call has.) When the signature before, or after, differs
    from job["sig"] nothing is written. A body whose signature used up the call is not meshed: nothing is written and
    "why_not" says so.
    The mesh is made from a temporary copy of the body (TemporaryBRepManager) in the part frame. Checks before the file is
    written: one shell, closed, volume within 1 % of the body, bounding box equal to the body's within the tessellation
    tolerance, and a sample of the facets on the body's surface. A mesh that fails is not written. The file is written
    in the DOCUMENT frame (the vertices moved with the occurrence's matrix), as tools/fusion/fusion_parts.py writes it.

    Returns {"facts": {...}, "done": True, "cursor": None, "sig": signature after} with facts
      "stl": the path written or None, "written": bool, "why_not": text or None,
      "sig_before", "sig_after", "triangles": n, "open_edges": 0, "shells": 1,
      "volume_mesh", "volume_brep" (mm3), "bbox_mesh", "bbox_brep" ([[x, y, z], [x, y, z]]), "quality": "High",
      "header": "Fusion <doc> v<N> <name>" (the 80-byte STL header: the saved version of the document)

    Also "frame": "document", "matrix": the 16 numbers the vertices were moved with (identity for a part that is not
    turned or moved), "facets_checked": n of "triangles" tested against the body, "facets_off": [[centroid in the part
    frame, distance from the body in mm or None]] of the sampled facets that are not on the body's surface (then nothing
    is written),
    "qualities_skipped": qualities not tried because the budget ran out.
    More than the contract lists: "bbox_tol" (mm, the tolerance used: 0.1), "bbox_precise" (the body's box came from
    preciseBoundingBox; the plain boundingBox of a body with free-form faces can be larger than the body), "tried"
    (per quality: triangles, shells, open edges, volume), "doc_modified" (the document has unsaved changes: the header
    then names the last SAVED version, the signature is what binds the file to the geometry).
    With no job["sig"] the "before" check is skipped; before and after must still agree."""
    import struct
    design = gate_design(job)
    doc = gate_document(job)
    occ, comp, body = gate_body(design, job)
    args = job.get('args') or {}
    path, name = args.get('stl'), args.get('name') or job.get('part') or body.name
    ver = gate_doc_version(doc)
    header = 'Fusion %s v%s %s' % (job.get('doc'), ver if ver is not None else '?', name)
    before = gate_signature(body, fresh=True)           # never from the memory of the session: the STL must be this geometry
    whole = _exp_whole(body)
    facts = {'stl': None, 'written': False, 'why_not': None, 'sig_before': before, 'sig_after': None, 'triangles': 0, 'open_edges': None,
             'shells': None, 'volume_mesh': None, 'volume_brep': round(body.volume * 1000.0, 3), 'bbox_mesh': None, 'bbox_brep': None,
             'quality': None, 'header': header, 'bbox_tol': _EXP_BOX_TOL, 'bbox_precise': False, 'tried': [], 'doc_modified': bool(doc.isModified),
             'frame': 'document', 'matrix': [round(x, 6) for x in gate_matrix(occ)], 'facets_checked': 0, 'facets_off': [], 'qualities_skipped': []}
    done = lambda: {'facts': facts, 'done': True, 'cursor': None, 'sig': facts['sig_after'] or before}
    if not path or not os.path.isabs(path):
        facts['why_not'] = 'job args.stl must be an absolute path, got %r' % (path,)
        return done()
    if job.get('sig') and before != job['sig']:
        facts['why_not'] = 'the body changed since it was checked: signature %s, checked at %s' % (before[:12], str(job['sig'])[:12])
        return done()
    if gate_time_up(job, 3.0):                          # the loop over the faces used up the call: a mesh started now would end after the host stopped waiting
        facts['why_not'] = ('the signature of this body (%d faces) took the time of the call: nothing was meshed. The time of one unit '
                            '("fusion": "budget_s" in gate/layout.json) is too short for a body this large' % body.faces.count)
        return done()
    box = getattr(body, 'preciseBoundingBox', None)
    facts['bbox_precise'] = box is not None
    if box is None:
        box = body.boundingBox
    lo, hi = gate_mm(box.minPoint), gate_mm(box.maxPoint)
    facts['bbox_brep'] = [[round(c, 3) for c in lo], [round(c, 3) for c in hi]]
    bvol = body.volume * 1000.0
    copy = adsk.fusion.TemporaryBRepManager.get().copy(body)
    T, ok, why = [], False, 'no mesh'
    budget = float(job.get('deadline_s') or 20)
    for k, qname in enumerate(_EXP_QUALITY):
        if k and gate_left(job) < budget / 3.0:         # a finer mesh of a big part can run far past the budget
            facts['qualities_skipped'] = list(_EXP_QUALITY[k:])
            why = '%s; no time left to try %s' % (why, ', '.join(_EXP_QUALITY[k:]))
            break
        mc = copy.meshManager.createMeshCalculator()
        mc.setQuality(getattr(adsk.fusion.TriangleMeshQualityOptions, qname + 'QualityTriangleMesh'))
        T = _exp_tris(mc.calculate())
        vol = sum(a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0]) for a, b, c in T) / 6.0
        ns, op = _exp_shells(T), _exp_open_edges(T)
        mbox = box_of([v for t in T for v in t]) if T else ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        dev = max(max(abs(mbox[0][k] - lo[k]), abs(mbox[1][k] - hi[k])) for k in range(3))
        facts.update({'triangles': len(T), 'open_edges': op, 'shells': ns, 'volume_mesh': round(vol, 3), 'quality': qname,
                      'bbox_mesh': [[round(c, 3) for c in mbox[0]], [round(c, 3) for c in mbox[1]]]})
        facts['tried'].append({'quality': qname, 'triangles': len(T), 'shells': ns, 'open_edges': op, 'volume': round(vol, 3), 'bbox_dev': round(dev, 3)})
        why = None
        if not T:
            why = 'the mesh is empty'
        elif ns != 1:
            why = 'the mesh has %d shells' % ns
        elif op:
            why = 'the mesh is not closed: %d open edges' % op
        elif abs(vol - bvol) > 0.01 * bvol:
            why = 'mesh volume %.1f mm3 is more than 1 %% off the body (%.1f mm3)' % (vol, bvol)
        elif dev > _EXP_BOX_TOL:
            why = 'mesh bounding box is %.2f mm off the body: part of the body is missing or extra' % dev
        else:
            facts['facets_checked'], facts['facets_off'] = _exp_facets(body, T, job)
            if facts['facets_off']:
                why = '%d of %d sampled facets are not on the surface of the body (the first at %s)' % (len(facts['facets_off']), facts['facets_checked'], facts['facets_off'][0][0])
        ok = why is None
        if ok:
            break
    # Nothing else runs inside one script call. So the body is read as a whole again; every face is read a second time
    # only when one of those values moved
    after = before if _exp_whole(body) == whole else gate_signature(body, fresh=True)
    facts['sig_after'] = after
    if after != before:
        facts['why_not'] = 'the body changed while it was meshed: signature %s -> %s' % (before[:12], after[:12])
        return done()
    if not ok:
        facts['why_not'] = why
        return done()
    m = gate_matrix(occ)
    if m != IDENTITY:                                   # the file is in the document frame, as fusion_parts.py writes it
        T = [tuple(tuple(0.0 if abs(c) < 1e-9 else c for c in mat_point(m, v)) for v in t) for t in T]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(header.encode('ascii', 'replace').ljust(80, b' ')[:80] + struct.pack('<I', len(T)))
        for t in T:
            f.write(struct.pack('<12fH', 0, 0, 0, *t[0], *t[1], *t[2], 0))
    os.replace(tmp, path)
    facts['stl'], facts['written'] = path, True
    return done()
