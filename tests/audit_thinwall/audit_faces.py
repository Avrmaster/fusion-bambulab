"""Thin-wall audit, Fusion side, second call: per printed body every face's surface type, area, middle, and the axes
of its cylinders and cones (for the audit's own tooth rule). No code of the gate."""
import adsk.core, adsk.fusion

def job(J):
    app = adsk.core.Application.get(); d = next(x for x in app.documents if x.name == J['doc'])
    des = adsk.fusion.Design.cast(d.products.itemByProductType('DesignProductType'))
    out = {}
    for spec in J['parts']:
        occ = [o for o in des.rootComponent.allOccurrences if o.component.name == spec['component']][0]
        b = [x for x in occ.bRepBodies if x.name == spec['body']][0]
        fl = []
        for f in b.faces:
            g = f.geometry; c = f.centroid
            r = {'t': g.surfaceType, 'area': f.area * 100, 'c': [c.x * 10, c.y * 10, c.z * 10]}
            if g.surfaceType in (1, 2):
                r['o'] = [g.origin.x * 10, g.origin.y * 10, g.origin.z * 10]; r['ax'] = [g.axis.x, g.axis.y, g.axis.z]
                if g.surfaceType == 1:
                    p = f.pointOnFace; ok, n = f.evaluator.getNormalAtPoint(p)
                    w = [p.x - g.origin.x, p.y - g.origin.y, p.z - g.origin.z]; a = [g.axis.x, g.axis.y, g.axis.z]
                    la = sum(x * x for x in a) ** 0.5; a = [x / la for x in a]; h = sum(w[k] * a[k] for k in range(3))
                    r['hole'] = (n.x * (w[0] - a[0] * h) + n.y * (w[1] - a[1] * h) + n.z * (w[2] - a[2] * h)) < 0
            fl.append(r)
        out[spec['name']] = fl
    return out
