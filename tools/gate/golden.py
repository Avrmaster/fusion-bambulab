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
    for o in des.rootComponent.allOccurrences:
        if o.fullPathName == path:
            return o
    raise KeyError('no occurrence %r' % path)


def job(JOB):
    d, des = _design(JOB.get('doc'))
    g = JOB['kind']['golden']
    if JOB['action'] == 'place':
        comp = _component(des, JOB.get('component') or '')
        occs = des.rootComponent.allOccurrencesByComponent(comp) if comp != des.rootComponent else None
        occ = occs.item(0) if occs and occs.count else None
        bodies = [golden_body(g, it['floor'], it['out']) for it in JOB['items']]
        return {'made': _add_bodies(comp, occ, bodies, g['name']), 'component': comp.name}
    if JOB['action'] == 'replace':
        done = []
        for it in JOB['items']:
            occ_path, body_name = it['path'].rsplit('/', 1) if '/' in it['path'] else ('', it['path'])
            occ = _occurrence_by_path(des, occ_path)
            comp = occ.component if occ else des.rootComponent
            old = next((b for b in comp.bRepBodies if b.name == body_name), None)
            if old is None:
                raise KeyError('no body %r in %s' % (body_name, comp.name))
            new = _add_bodies(comp, occ, [golden_body(g, it['floor'], it['out'])], g['name'])
            old.deleteMe()
            done.append({'old': it['path'], 'new': new})
        return {'replaced': done}
    raise ValueError('unknown action %r' % JOB['action'])
