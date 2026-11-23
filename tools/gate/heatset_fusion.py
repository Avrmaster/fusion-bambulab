"""Fusion side of the heat-set gate: read the facts heatset_core.check() judges. Runs through fusion_rpc with
heatset_core.py pasted in front (gate.py does that). Read-only.

JOB = {"doc", "parts": [{"name", "component" | "path", "body"}], "rules": the merged rules, "max_d": 12.0}

Per printed body: every concave cylindrical face from D1.0 to max_d (30: a washer seat or a recess round a pocket is read, so a finding can name it;
the open-end test below sees a recess or a bearing bore as open space), grouped by axis line, and for each group
whether a point 0.3 mm beyond each end is outside the body (an open end: a mouth). Per insert body (a body whose name
matches rules["insert_name"], anywhere in the assembly, printed parts excluded): its cylinders, volume, occurrence path
and the printed part whose box holds its middle (its host). World mm throughout."""
import adsk.core, adsk.fusion, math, re, time

CM = 10.0


def _doc(name):
    app = adsk.core.Application.get()
    docs = [d for d in app.documents if d.name == name] if name else [app.activeDocument]
    if not docs:
        raise KeyError('document %r is not open; open: %s' % (name, sorted({d.name for d in app.documents})))
    return docs[0]


def _occurrences(root):
    out, stack = [], list(root.occurrences)
    while stack:
        o = stack.pop()
        out.append(o)
        stack.extend(o.childOccurrences)
    return out


def _find_body(des, spec, by_comp):
    if spec.get('path'):
        occs, occ = des.rootComponent.occurrences, None
        for p in spec['path']:
            occ = next((o for o in occs if o.name == p), None)
            if occ is None:
                raise KeyError('no occurrence %r' % p)
            occs = occ.childOccurrences
    elif spec.get('component'):
        occs = by_comp.get(spec['component'], [])
        if len(occs) != 1:
            raise KeyError('component %r has %d occurrences' % (spec['component'], len(occs)))
        occ = occs[0]
    else:
        occ = None
    bodies = list(occ.bRepBodies) if occ else list(des.rootComponent.bRepBodies)
    if spec.get('body'):
        bodies = [b for b in bodies if b.name == spec['body']]
    if len(bodies) != 1:
        raise KeyError('%d bodies match %r' % (len(bodies), spec.get('body')))
    return bodies[0]


def _p(pt):
    return [pt.x * CM, pt.y * CM, pt.z * CM]


def _cylinder(face, max_d, concave_only):
    """The fact of one cylindrical face, or None when it is out of range."""
    g = face.geometry
    d = g.radius * 2 * CM
    if d < 1.0 or (max_d and d > max_d):
        return None
    o, a = _p(g.origin), [g.axis.x, g.axis.y, g.axis.z]
    n_ = math.sqrt(sum(x * x for x in a)); a = [x / n_ for x in a]
    p = face.pointOnFace
    ok, nrm = face.evaluator.getNormalAtPoint(p)
    pp = _p(p); v = [pp[i] - o[i] for i in range(3)]; t = sum(v[i] * a[i] for i in range(3))
    r = [v[i] - t * a[i] for i in range(3)]
    concave = (nrm.x * r[0] + nrm.y * r[1] + nrm.z * r[2]) < 0
    if concave_only and not concave:
        return None
    # extent along the axis and the angle covered: from points along the face's edges
    u = [1.0, 0.0, 0.0] if abs(a[0]) < 0.9 else [0.0, 1.0, 0.0]
    e1 = [a[1] * u[2] - a[2] * u[1], a[2] * u[0] - a[0] * u[2], a[0] * u[1] - a[1] * u[0]]
    n1 = math.sqrt(sum(x * x for x in e1)); e1 = [x / n1 for x in e1]
    e2 = [a[1] * e1[2] - a[2] * e1[1], a[2] * e1[0] - a[0] * e1[2], a[0] * e1[1] - a[1] * e1[0]]
    ts, angs = [], []
    for e in face.edges:
        ok, pts = e.evaluator.getStrokes(e.evaluator.getParameterExtents()[1], e.evaluator.getParameterExtents()[2], 0.002)
        for q in pts:
            qq = _p(q); w = [qq[i] - o[i] for i in range(3)]; tt = sum(w[i] * a[i] for i in range(3))
            ts.append(tt)
            rr = [w[i] - tt * a[i] for i in range(3)]
            if math.sqrt(sum(x * x for x in rr)) > 0.25 * d:
                angs.append(math.degrees(math.atan2(sum(rr[i] * e2[i] for i in range(3)), sum(rr[i] * e1[i] for i in range(3)))) % 360)
    if not ts:
        return None
    span = 360.0
    if angs:
        angs.sort()
        gaps = [angs[i + 1] - angs[i] for i in range(len(angs) - 1)] + [angs[0] + 360 - angs[-1]]
        span = 360.0 - max(gaps) if max(gaps) > 20 else 360.0
    return {'d': round(d, 4), 'o': o, 'a': a, 't0': min(ts), 't1': max(ts), 'concave': concave, 'span': round(span, 1)}


def _cylinders(body, max_d, concave_only):
    out = []
    for f in body.faces:
        if f.geometry.objectType == adsk.core.Cylinder.classType():
            c = _cylinder(f, max_d, concave_only)
            if c:
                out.append(c)
    return out


def _outside(body, pt_mm):
    p = adsk.core.Point3D.create(pt_mm[0] / CM, pt_mm[1] / CM, pt_mm[2] / CM)
    return body.pointContainment(p) == adsk.fusion.PointContainment.PointOutsidePointContainment


def _inside(body, pt_mm):
    p = adsk.core.Point3D.create(pt_mm[0] / CM, pt_mm[1] / CM, pt_mm[2] / CM)
    return body.pointContainment(p) == adsk.fusion.PointContainment.PointInsidePointContainment


def job(JOB):
    t_start = time.time()
    rules = JOB['rules']; tol = rules['tolerance']; max_d = float(JOB.get('max_d') or 30.0)
    d = _doc(JOB.get('doc'))
    des = adsk.fusion.Design.cast(d.products.itemByProductType('DesignProductType'))
    occs = _occurrences(des.rootComponent)
    by_comp = {}
    for o in occs:
        by_comp.setdefault(o.component.name, []).append(o)
    parts, errors, printed, bodies = [], [], set(), {}
    for spec in JOB['parts']:
        t = time.time()
        try:
            b = _find_body(des, spec, by_comp)
        except Exception as e:
            errors.append({'part': spec['name'], 'error': '%s: %s' % (type(e).__name__, e)}); continue
        printed.add(b.entityToken); bodies[spec['name']] = b
        cyl = _cylinders(b, max_d, True)
        groups = []
        for g in group_holes(cyl, tol):                                 # heatset_core, pasted in front
            groups.extend(split_group(g, lambda pt: _inside(b, pt)))    # two holes on one axis with plastic between
        for g in groups:
            lo, hi = ends(g)
            g['open_lo'] = _outside(b, [g['base'][i] + g['a'][i] * (lo - 0.3) for i in range(3)])
            g['open_hi'] = _outside(b, [g['base'][i] + g['a'][i] * (hi + 0.3) for i in range(3)])
        bb = b.boundingBox
        parts.append({'name': spec['name'], 'groups': groups, 'faces': b.faces.count, 'volume': round(b.volume * 1000, 2),
                      'area': round(b.area * 100, 2), 'box': [_p(bb.minPoint), _p(bb.maxPoint)], 's': round(time.time() - t, 3)})
    pat = re.compile(rules['insert_name'])
    inserts = []
    def take(b, path):
        if not pat.search(b.name) or b.entityToken in printed:
            return
        bb = b.boundingBox
        mid = [(bb.minPoint.x + bb.maxPoint.x) * CM / 2, (bb.minPoint.y + bb.maxPoint.y) * CM / 2, (bb.minPoint.z + bb.maxPoint.z) * CM / 2]
        cyls = _cylinders(b, 0, False)
        # the host: the printed body that has plastic round the insert (1 mm beyond its widest cylinder, at its middle);
        # a box test alone took the gearbox inside a link for the link
        host = None
        outer = [c for c in cyls if not c['concave']] or cyls
        if outer:
            c = max(outer, key=lambda c: c['d'])
            e1, e2 = perp(c['a'])                                       # heatset_core
            r = c['d'] / 2 + 1.0
            for p in parts:
                if not all(p['box'][0][i] - 1 <= mid[i] <= p['box'][1][i] + 1 for i in range(3)):
                    continue
                pb = bodies[p['name']]
                if any(_inside(pb, [mid[i] + k * r * e[i] for i in range(3)]) for e in (e1, e2) for k in (1, -1)):
                    host = p['name']; break
        inserts.append({'name': b.name, 'path': path, 'cylinders': cyls, 'volume': round(b.volume * 1000, 3), 'host': host})
    for b in des.rootComponent.bRepBodies:
        take(b, b.name)
    for o in occs:
        for b in o.bRepBodies:
            take(b, o.fullPathName + '/' + b.name)
    return {'doc': d.name, 'version': d.dataFile.versionNumber if d.dataFile else 0, 'modified': d.isModified,
            'parts': parts, 'inserts': inserts, 'errors': errors, 'fusion_s': round(time.time() - t_start, 3)}
