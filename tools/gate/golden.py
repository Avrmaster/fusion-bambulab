"""Fusion side: the golden heat-set insert and the fixes that put it in place. Pasted after heatset_core.py and
heatset_fusion.py (gate.py does that); job(JOB) runs one action:

  {"action": "place",   "doc", "component": name or "" (root), "kind": the kind's rules, "items": [{"floor": [x,y,z], "out": [ax,ay,az]}]}
      a golden insert per item: its top end on "floor" (the relief floor), its axis along "out" (out of the material),
      the pilot band at the bottom. One base feature per call. Returns the new bodies' names.
  {"action": "replace", "doc", "kind", "items": [{"path": occurrence path + "/" + body name, "floor", "out"}]}
      the body at "path" is removed and a golden insert is placed in the same component at floor / out.

The golden insert is drawn from its numbers alone (rules/heatset.json "golden"): a pilot band D(pilot_d) x pilot_len at
the end that goes in first, the knurl D(knurl_d) up to the top, a bore D(bore_d) through. It is named
golden["name"], so the gate and a person both see what it is."""
import adsk.core, adsk.fusion, math


def golden_body(g, floor_mm, out):
    """A temporary B-rep of the golden insert: top at floor_mm, axis out of the pocket along `out`."""
    tbm = adsk.fusion.TemporaryBRepManager.get()
    u = [x / math.sqrt(sum(y * y for y in out)) for x in out]
    P = lambda s: adsk.core.Point3D.create((floor_mm[0] - u[0] * s) / 10, (floor_mm[1] - u[1] * s) / 10, (floor_mm[2] - u[2] * s) / 10)
    L, pl = g['length'], g['pilot_len']
    body = tbm.createCylinderOrCone(P(L), g['pilot_d'] / 20, P(L - pl), g['pilot_d'] / 20)
    knurl = tbm.createCylinderOrCone(P(L - pl), g['knurl_d'] / 20, P(0), g['knurl_d'] / 20)
    tbm.booleanOperation(body, knurl, adsk.fusion.BooleanTypes.UnionBooleanType)
    bore = tbm.createCylinderOrCone(P(L + 0.01), g['bore_d'] / 20, P(-0.01), g['bore_d'] / 20)
    tbm.booleanOperation(body, bore, adsk.fusion.BooleanTypes.DifferenceBooleanType)
    return body


def _design(name):
    app = adsk.core.Application.get()
    docs = [d for d in app.documents if d.name == name] if name else [app.activeDocument]
    if not docs:
        raise KeyError('document %r is not open' % name)
    return docs[0], adsk.fusion.Design.cast(docs[0].products.itemByProductType('DesignProductType'))


def _component(des, name):
    if not name:
        return des.rootComponent
    for c in des.allComponents:
        if c.name == name:
            return c
    occ = des.rootComponent.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    occ.component.name = name
    return occ.component


def _to_component(occ, body):
    """A temporary body in world mm -> the same body in the frame of the occurrence's component."""
    if occ is None:
        return body
    m = occ.transform2.copy()
    m.invert()
    adsk.fusion.TemporaryBRepManager.get().transform(body, m)
    return body


def _add_bodies(comp, occ, bodies, name):
    des = comp.parentDesign
    made = []
    if des.designType == adsk.fusion.DesignTypes.ParametricDesignType:
        bf = comp.features.baseFeatures.add()
        bf.startEdit()
        try:
            for b in bodies:
                nb = comp.bRepBodies.add(_to_component(occ, b), bf)
                nb.name = name
                made.append(nb.name)
        finally:
            bf.finishEdit()
        bf.name = 'golden heat-set inserts'
    else:
        for b in bodies:
            nb = comp.bRepBodies.add(_to_component(occ, b))
            nb.name = name
            made.append(nb.name)
    return made


def _occurrence_by_path(des, path):
    if not path:
        return None
    for o in _occurrences(des.rootComponent):                           # proxies: transform2 is the place in the assembly
        if o.fullPathName == path:
            return o
    raise KeyError('no occurrence %r' % path)


def _remove(comp, name):
    """Remove one body by name with a Remove feature (deleteMe on a body of a base feature took its neighbours along)."""
    b = next((b for b in comp.bRepBodies if b.name == name), None)
    if b is None:
        raise KeyError('no body %r in %s' % (name, comp.name))
    if comp.parentDesign.designType == adsk.fusion.DesignTypes.ParametricDesignType:
        comp.features.removeFeatures.add(b)
    else:
        b.deleteMe()
    return 1


def job(JOB):
    d, des = _design(JOB.get('doc'))
    g = JOB['kind']['golden']
    if JOB['action'] == 'place':
        comp = _component(des, JOB.get('component') or '')
        occ = next((o for o in _occurrences(des.rootComponent) if o.component == comp), None) if comp != des.rootComponent else None
        bodies = [golden_body(g, it['floor'], it['out']) for it in JOB['items']]
        return {'made': _add_bodies(comp, occ, bodies, g['name']), 'component': comp.name}
    if JOB['action'] == 'replace':
        # tokens first: after any timeline change a body object can go stale, a token is found again
        groups = {}
        for it in JOB['items']:
            occ_path, body_name = it['path'].rsplit('/', 1) if '/' in it['path'] else ('', it['path'])
            occ = _occurrence_by_path(des, occ_path)
            comp = occ.component if occ else des.rootComponent
            old = next((b for b in comp.bRepBodies if b.name == body_name), None)
            if old is None:
                raise KeyError('no body %r in %s' % (body_name, comp.name))
            groups.setdefault(comp.name, {'comp': comp, 'occ': occ, 'items': []})['items'].append((body_name, it))
        done = []
        for gname, gr in groups.items():
            made = _add_bodies(gr['comp'], gr['occ'], [golden_body(g, it['floor'], it['out']) for _, it in gr['items']], g['name'])
            for body_name, it in gr['items']:
                _remove(gr['comp'], body_name)
                done.append({'old': it['path']})
        return {'replaced': done}
    if JOB['action'] == 'delete':
        n = 0
        for path in JOB['paths']:
            occ_path, body_name = path.rsplit('/', 1) if '/' in path else ('', path)
            occ = _occurrence_by_path(des, occ_path)
            n += _remove(occ.component if occ else des.rootComponent, body_name)
        return {'deleted': n}
    raise ValueError('unknown action %r' % JOB['action'])


# ---------------------------------------------------------------- pockets back to the table
def _cyl_world(tbm, mouth, inward, s0, s1, d):
    P = lambda s: adsk.core.Point3D.create(*[(mouth[i] + inward[i] * s) / 10 for i in range(3)])
    return tbm.createCylinderOrCone(P(s0), d / 20, P(s1), d / 20)


def _ring_inside(body, mouth, inward, s, r):
    """Is there plastic all round at radius r, depth s (16 angles)?"""
    e1, e2 = perp(inward)                                               # heatset_core
    return all(_inside(body, [mouth[i] + inward[i] * s + r * (math.cos(k * math.pi / 8) * e1[i] + math.sin(k * math.pi / 8) * e2[i]) for i in range(3)])
               for k in range(16))                                      # heatset_fusion


def fix_pockets(JOB):
    """Per pocket: a plug that fills exactly the pocket's present steps (from its mouth to the depth of the table), minus
    the part's own wide bores it might reach into, is joined; then the table's pocket is cut: relief (the present one
    when it is a relief of the table, else the widest that keeps JOB["relief_wall"] of plastic round it), lead-in, hole.
    One base feature and two combine features per part, at the end of the timeline."""
    d, des = _design(JOB.get('doc'))
    tbm = adsk.fusion.TemporaryBRepManager.get()
    spec = JOB['part']
    by = {}
    for o in _occurrences(des.rootComponent):                           # proxies, not allOccurrences (parent-relative)
        by.setdefault(o.component.name, []).append(o)
    occ = by[spec['component']][0]
    body = next(b for b in occ.bRepBodies if b.name == spec['body'])
    native = body.nativeObject or body
    comp = occ.component
    # the part's wide bores, as solids, to keep a plug out of them
    wide = _cylinders(body, 0, False)
    bores = [c for c in wide if c['concave'] and c['d'] > 8.0]
    bosses = [c for c in wide if not c['concave'] and c['d'] > 6.0]
    plugs, cuts, report = [], [], []
    for it in JOB['items']:
        M, u = it['mouth'], it['inward']
        pk = JOB['kind']['pocket']
        depth = pk['relief']['depth'] + pk['hole']['min_depth_below_relief']
        plug = None
        for dd, s0, s1 in it['plug']:
            if s0 >= depth or dd is None:
                continue
            c = _cyl_world(tbm, M, u, s0, min(s1, depth) + (0.02 if s1 >= depth else 0), dd)
            if plug is None: plug = c
            else: tbm.booleanOperation(plug, c, adsk.fusion.BooleanTypes.UnionBooleanType)
        def axis_dist(c):
            w = [M[i] - c['o'][i] for i in range(3)]; t = sum(w[i] * c['a'][i] for i in range(3))
            return math.sqrt(sum((w[i] - t * c['a'][i]) ** 2 for i in range(3)))
        def solid(c):
            p0 = [c['o'][i] + c['a'][i] * (c['t0'] - 0.01) for i in range(3)]
            p1 = [c['o'][i] + c['a'][i] * (c['t1'] + 0.01) for i in range(3)]
            return tbm.createCylinderOrCone(adsk.core.Point3D.create(*[x / 10 for x in p0]), c['d'] / 20,
                                            adsk.core.Point3D.create(*[x / 10 for x in p1]), c['d'] / 20)
        if plug is not None:
            # a plug never stands proud of the part: kept inside a coaxial boss the pocket sits on, and out of a wide
            # bore the pocket is beside (not one it stands inside of, on a post)
            for c in bosses:
                if axis_dist(c) < 0.05 and abs(abs(sum(c['a'][i] * u[i] for i in range(3))) - 1) < 1e-6:
                    tbm.booleanOperation(plug, solid(c), adsk.fusion.BooleanTypes.IntersectionBooleanType)
            for c in [c for c in bores if axis_dist(c) > c['d'] / 2]:
                tbm.booleanOperation(plug, solid(c), adsk.fusion.BooleanTypes.DifferenceBooleanType)
        rd = it.get('relief_d')
        if not rd:
            rd = next((x for x in pk['relief']['d'] if _ring_inside(body, M, u, pk['relief']['depth'] / 2, x / 2 + JOB['relief_wall'])), pk['relief']['d'][-1])
        cut = _cyl_world(tbm, M, u, -0.02, pk['relief']['depth'], rd)
        tbm.booleanOperation(cut, _cyl_world(tbm, M, u, pk['relief']['depth'] - 0.01, pk['relief']['depth'] + pk['lead_in']['depth'], pk['lead_in']['d']),
                             adsk.fusion.BooleanTypes.UnionBooleanType)
        tbm.booleanOperation(cut, _cyl_world(tbm, M, u, pk['relief']['depth'] + pk['lead_in']['depth'] - 0.01, depth, pk['hole']['d']),
                             adsk.fusion.BooleanTypes.UnionBooleanType)
        if plug is not None and plug.volume > 1e-9:
            plugs.append(plug)
        cuts.append(cut)
        report.append({'mouth': M, 'relief_d': rd, 'plug_mm3': round(plug.volume * 1000, 2) if plug else 0})
    def add(bodies, op, name):
        if not bodies:
            return
        bf = comp.features.baseFeatures.add(); bf.startEdit()
        try:
            for b in bodies:
                comp.bRepBodies.add(_to_component(occ, b), bf)
        finally:
            bf.finishEdit()
        bf.name = name
        tools = adsk.core.ObjectCollection.create()
        for b in bf.bodies: tools.add(b)                       # the base feature's own bodies: the added objects are stale now
        target = next(b for b in comp.bRepBodies if b.name == spec['body'])
        ci = comp.features.combineFeatures.createInput(target, tools)
        ci.operation = op; ci.isKeepToolBodies = False
        comp.features.combineFeatures.add(ci).name = name
    add(plugs, adsk.fusion.FeatureOperations.JoinFeatureOperation, 'heat-set pockets: plugs')
    add(cuts, adsk.fusion.FeatureOperations.CutFeatureOperation, 'heat-set pockets: table cut')
    return {'part': spec['body'], 'fixed': report}


_job_inserts = job


def job(JOB):
    if JOB['action'] == 'pockets':
        return fix_pockets(JOB)
    return _job_inserts(JOB)
