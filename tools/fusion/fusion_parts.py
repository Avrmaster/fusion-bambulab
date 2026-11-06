"""Printed parts of a Fusion design: export checked STLs, find stale ones, find thin features. Runs inside Fusion through the MCP.
The part list is fusion/parts.json, {stl name: spec}:
    {"bracket": {"path": ["Assembly:1", "Bracket:1"]},      occurrence path from the root; its one visible body
     "cube":    {"body": "Cube"},                           a body of the root component (a single-part document)
     "lid":     {"path": ["Box:1"], "body": "Lid"}}         a named body of that occurrence

Run through fusion_mcp_execute (featureType script) with this wrapper; use readOnly true for stale and thin:
    P = r'<project>/fusion/tools/fusion_parts.py'
    g = {'__file__': P}; exec(open(P, encoding='utf-8').read(), g); run = g['run']     # or g['stale'], g['thin']

run: writes stl/<name>.stl. Call it again until it prints DONE: each call works for at most BUDGET s and skips what this saved
version already exported. It refuses while the document has unsaved changes, because the saved version number is the stamp in
the STL header and in bambulab/fusion_export.json (per part: version, time, B-rep signature, STL sha1). Save first.
Set ONLY = ['name'] in the wrapper's globals to redo those parts.

Mesh: a fresh MeshCalculator tessellation per part, not displayMeshes.bestMesh, whose density depends on what was last drawn and
which does not exist for a part never shown. Checks before a file is written (a part that fails is listed and not written):
  - per triangle: a point just outside its face is outside the body and a point just inside is inside; a facet that fails
    is measured, and more than TOL_DIST from the body's faces means surface the body does not have
  - one shell, closed, volume within 1 % of the B-rep"""
import adsk.core, adsk.fusion, struct, time, os, json, math, hashlib
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PARTS_FILE = os.path.join(REPO, 'fusion', 'parts.json')
MANIFEST = os.path.join(REPO, 'bambulab', 'fusion_export.json')
LOG = os.path.join(REPO, 'bambulab', '.fusion_export_log.jsonl')
STL_DIR = os.path.join(REPO, 'stl')
BUDGET = 20.0
ONLY = None
TOL_OFF, TOL_DIST = 0.03, 0.1   # mm
QUALITY = ('High', 'Normal', 'VeryHigh')   # tried in turn: one may give a non-manifold edge where faces touch along an arc
MIN_WIDTH = 1.2  # mm, thin(): narrower planar faces are slivers, ledges or crescent leftovers of old holes


def parts():
    return json.load(open(PARTS_FILE, encoding='utf-8'))


def design():
    return adsk.fusion.Design.cast(adsk.core.Application.get().activeDocument.products.itemByProductType('DesignProductType'))


def find(root, path):
    occs, occ = root.occurrences, None
    for p in path:
        occ = next((o for o in occs if o.name == p), None)
        if occ is None: raise KeyError('no occurrence %r; here: %s' % (p, [o.name for o in occs]))
        occs = occ.childOccurrences
    return occ


def part_body(d, spec):
    occ = find(d.rootComponent, spec['path']) if spec.get('path') else None
    comp = occ.component if occ else d.rootComponent
    if spec.get('body'): bodies = [b for b in comp.bRepBodies if b.name == spec['body']]
    else: bodies = [b for b in comp.bRepBodies if b.isLightBulbOn] or list(comp.bRepBodies)
    assert len(bodies) == 1, (spec, [b.name for b in comp.bRepBodies])
    return bodies[0].createForAssemblyContext(occ) if occ else bodies[0]


def tris(m):
    p, ii = m.nodeCoordinatesAsFloat, m.nodeIndices
    return [tuple((p[3 * k] * 10, p[3 * k + 1] * 10, p[3 * k + 2] * 10) for k in ii[t:t + 3]) for t in range(0, len(ii), 3)]


def shells(T):
    idx, par = {}, []
    def f(x):
        while par[x] != x: par[x] = par[par[x]]; x = par[x]
        return x
    for t in T:
        ids = [idx.setdefault(v, len(idx)) for v in t]
        while len(par) < len(idx): par.append(len(par))
        par[f(ids[0])] = f(ids[1]); par[f(ids[1])] = f(ids[2])
    return len({f(i) for i in range(len(par))})


def open_edges(T):
    """Edges not shared by exactly two triangles in opposite directions (welded on exact float values)."""
    E = {}
    for t in T:
        for u, v in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])): E[(u, v)] = E.get((u, v), 0) + 1
    return sum(1 for (u, v), n in E.items() if n != 1 or E.get((v, u)) != 1)


def check(body, T):
    """[(centroid, distance mm)] of the facets that are not on the body's surface."""
    IN, OUT = adsk.fusion.PointContainment.PointInsidePointContainment, adsk.fusion.PointContainment.PointOutsidePointContainment
    P = lambda q: adsk.core.Point3D.create(q[0] / 10, q[1] / 10, q[2] / 10)
    mm, bad = adsk.core.Application.get().measureManager, []
    for a, b, c in T:
        u = [b[k] - a[k] for k in range(3)]; w = [c[k] - a[k] for k in range(3)]
        n = [u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0]]
        L = math.sqrt(sum(x * x for x in n))
        if L < 1e-6: continue
        n = [x / L for x in n]; q = [(a[k] + b[k] + c[k]) / 3 for k in range(3)]
        if any(body.pointContainment(P([q[k] + e * n[k] for k in range(3)])) == OUT and
               body.pointContainment(P([q[k] - e * n[k] for k in range(3)])) == IN for e in (TOL_OFF, TOL_DIST)): continue
        d = min(mm.measureMinimumDistance(f, P(q)).value for f in body.faces) * 10   # chord error on curved faces is small
        if d > TOL_DIST: bad.append(([round(x, 1) for x in q], round(d, 2)))
        if len(bad) >= 20: break
    return bad


def fusion_version():
    """('v57', dirty): the saved version of the active document; dirty = unsaved changes on top of it."""
    doc = adsk.core.Application.get().activeDocument
    return ('v%d' % doc.dataFile.versionNumber if doc.dataFile else 'v?'), doc.isModified


def signature(body):
    return {'vol': round(body.volume * 1000, 2), 'area': round(body.area * 100, 2), 'faces': body.faces.count}


def stale(_context: str = ''):
    """Read-only: the parts whose B-rep changed since their export."""
    d, P = design(), parts()
    man = json.load(open(MANIFEST, encoding='utf-8')) if os.path.exists(MANIFEST) else {}
    v, dirty = fusion_version(); n = 0
    print('Fusion now %s%s' % (v, ' + unsaved changes' if dirty else ''))
    for key, spec in P.items():
        m = man.get(key)
        if not m:
            print('  %-40s never exported' % key); n += 1; continue
        sg = signature(part_body(d, spec))
        diff = [k for k in sg if abs(sg[k] - m['sig'][k]) > (0.05 if k != 'faces' else 0)]
        if diff:
            print('  %-40s CHANGED since %s (%s)' % (key, m['fusion'], ', '.join('%s %s -> %s' % (k, m['sig'][k], sg[k]) for k in diff))); n += 1
    print('STALE %d of %d' % (n, len(P)) if n else 'all %d parts match their export' % len(P))


def is_annulus(face):
    if face.loops.count != 2: return False
    c = []
    for lp in face.loops:
        if lp.edges.count != 1: return False
        g = lp.edges.item(0).geometry
        if not g.objectType.endswith('Circle3D'): return False
        c.append(g.center)
    return c[0].distanceTo(c[1]) < 1e-4


def is_disc(face):
    if face.loops.count != 1 or face.loops.item(0).edges.count != 1: return False
    g = face.loops.item(0).edges.item(0).geometry
    return g.objectType.endswith('Circle3D') and g.radius * 10 >= 0.9


def thin(_context: str = ''):
    """Read-only: planar faces with mean width 2 * area / perimeter under MIN_WIDTH, and parts that are not one clean solid.
    Annular faces (lead-ins, counterbore floors) and round discs (peg tops) are ignored. Must end with TOTAL 0."""
    d, total = design(), 0
    print('START thin check, min width %.1f mm' % MIN_WIDTH)
    only = globals().get('ONLY')
    for key, spec in parts().items():
        if only and key not in only: continue
        body, hits = part_body(d, spec), []
        for f in body.faces:
            if not f.geometry.objectType.endswith('Plane') or is_annulus(f) or is_disc(f): continue
            per = 0.0
            for e in f.edges:
                _, p0, p1 = e.evaluator.getParameterExtents(); _, ln = e.evaluator.getLengthAtParameter(p0, p1); per += ln
            if per > 0 and 2 * f.area / per * 10 < MIN_WIDTH:
                p, n = f.pointOnFace, f.geometry.normal
                hits.append('  width %.2f mm, area %.1f mm2, at (%.1f, %.1f, %.1f), normal (%.0f, %.0f, %.0f)' % (2 * f.area / per * 10, f.area * 100, p.x * 10, p.y * 10, p.z * 10, n.x, n.y, n.z))
        nm = sum(1 for e in body.edges if e.faces.count != 2)
        if body.lumps.count != 1 or body.shells.count != 1 or nm:
            hits.append('  NOT ONE CLEAN SOLID: lumps %d, shells %d, non-manifold edges %d' % (body.lumps.count, body.shells.count, nm))
        print('%s: %d' % (key, len(hits)))
        for h in hits: print(h)
        total += len(hits)
    print('TOTAL', total); print('END')


def run(_context: str):
    t0, d, P = time.time(), design(), parts()
    ver, dirty = fusion_version()
    if dirty:
        print('REFUSED: unsaved changes on top of %s. Save the document, then call again.' % ver); return
    os.makedirs(STL_DIR, exist_ok=True)
    man = json.load(open(MANIFEST, encoding='utf-8')) if os.path.exists(MANIFEST) else {}
    recs = [json.loads(l) for l in open(LOG, encoding='utf-8')] if os.path.exists(LOG) else []
    done = {r['key'] for r in recs if r['ok'] and r['fusion'] == ver}   # a new saved version exports again; failed parts are retried
    only = globals().get('ONLY'); left = 0
    for key, spec in P.items():
        if (key not in only) if only else (key in done): continue
        if time.time() - t0 > BUDGET: left += 1; continue
        body = part_body(d, spec); bvol = body.volume * 1000
        for qname in QUALITY:
            mc = body.meshManager.createMeshCalculator(); mc.setQuality(getattr(adsk.fusion.TriangleMeshQualityOptions, qname + 'QualityTriangleMesh'))
            T = tris(mc.calculate())
            vol = sum(a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0]) for a, b, c in T) / 6
            ns, op = shells(T), open_edges(T)
            bad = check(body, T) if ns == 1 and op == 0 else []
            ok = not bad and ns == 1 and op == 0 and abs(vol - bvol) <= 0.01 * bvol
            if ok: break
        rec = {'key': key, 'ok': ok, 'fusion': ver, 'quality': qname, 'tris': len(T), 'shells': ns, 'open_edges': op, 'vol': round(vol, 1), 'brep_vol': round(bvol, 1), 'bad': bad}
        if ok:
            fn = os.path.join(STL_DIR, key + '.stl')
            with open(fn, 'wb') as f:
                f.write(('Fusion %s %s' % (ver, key)).encode('ascii', 'replace').ljust(80, b' ')[:80] + struct.pack('<I', len(T)))
                for t in T: f.write(struct.pack('<12fH', 0, 0, 0, *t[0], *t[1], *t[2], 0))
            man[key] = {'fusion': ver, 'at': time.strftime('%Y-%m-%d %H:%M'), 'sig': signature(body), 'stl_sha1': hashlib.sha1(open(fn, 'rb').read()).hexdigest()}
            with open(MANIFEST, 'w', encoding='utf-8') as f: json.dump(dict(sorted(man.items())), f, indent=1, ensure_ascii=False)
        with open(LOG, 'a', encoding='utf-8') as f: f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        print('%-40s %s %-8s tris %d shells %d open %d vol %.1f / brep %.1f%s' % (key, 'ok  ' if ok else 'FAIL', qname, len(T), ns, op, vol, bvol, '  bad %s' % bad[:3] if bad else ''))
    last = {}
    for l in open(LOG, encoding='utf-8'):
        r = json.loads(l)
        if r['fusion'] == ver: last[r['key']] = r['ok']
    fails = [k for k, ok in last.items() if not ok]
    print('FAILED (not written): %s' % fails if fails else 'no failures')
    print('DONE' if left == 0 else 'CALL AGAIN (%d left)' % left)
