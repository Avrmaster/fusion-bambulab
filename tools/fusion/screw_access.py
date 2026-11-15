# Fusion MCP check (read-only): can every screw actually be put in and turned? A printed ledge over the edge of a
# head seat passes every hole, wall and sliver check, yet the screw cannot go in (skill print-design, "Parts that mate").
# For every screw ref (a '... head ...' body + its coaxial '... shank ...' body in the same component, or one plain
# 2-cylinder screw body) a straight access cylinder of the head radius runs from the head top outwards along the screw
# axis for REACH mm (the screw goes in that way, then the hex key). Any printed part it cuts blocks the screw.
# exec() this file, then access(doc_name, only=None, reach=60) -> rows (blocked mm3, first hit mm from the head top,
# part, screw), worst first. `only`: substring of the HW component name (one group per call - keep calls short).
# A hit by a part that is mounted AFTER the screw is fine (assembly order): judge each row, do not trust a zero blindly.
import adsk.core, adsk.fusion, math

TB = adsk.fusion.TemporaryBRepManager.get()
SKIP = ('ref',)          # component name prefixes that are not printed parts (bought parts, HW, meshes); pass skip=
REACH, MIN_MM3, STEP = 60.0, 0.3, 2.0


def _com(b):
    p = b.physicalProperties.centerOfMass
    return (p.x * 10, p.y * 10, p.z * 10)


def _proj(b, a):
    k = b.boundingBox
    v = [x * a[0] + y * a[1] + z * a[2] for x in (k.minPoint.x, k.maxPoint.x) for y in (k.minPoint.y, k.maxPoint.y)
         for z in (k.minPoint.z, k.maxPoint.z)]
    return min(v) * 10, max(v) * 10


def _cyls(b):
    return [f.geometry for f in b.faces if f.geometry.objectType.endswith('Cylinder')]


def _axis_point(g, a):
    o = [c * 10 for c in g.origin.asArray()]
    s = sum(o[i] * a[i] for i in range(3))
    return [o[i] - a[i] * s for i in range(3)]


def _screws(comp):
    """(name, axis unit head-outwards, point on the axis, head radius, head top along the axis)."""
    out, bodies = [], list(comp.bRepBodies)
    shanks = [b for b in bodies if 'shank' in b.name]
    for b in bodies:
        n = b.name
        if 'head' in n and 'shank' not in n:
            if not shanks:
                continue
            ch = _com(b)
            sh = min(shanks, key=lambda s: sum((c - d) ** 2 for c, d in zip(_com(s), ch)))
            cs = _com(sh)
            a = [ch[i] - cs[i] for i in range(3)]
            la = math.sqrt(sum(v * v for v in a))
            if la < 1e-6:
                continue
            a = [v / la for v in a]
            cy = _cyls(b)
            if not cy:
                continue
            r = max(g.radius for g in cy) * 10
            out.append((n, a, _axis_point(max(cy, key=lambda g: g.radius), a), r, _proj(b, a)[1]))
        elif n.startswith('M') and 'x' in n and not any(t in n for t in ('insert', 'washer', 'nut', 'shank')):
            cy = _cyls(b)
            if len(cy) < 2:
                continue
            hg = max(cy, key=lambda g: g.radius); sg = min(cy, key=lambda g: g.radius)
            a = list(hg.axis.asArray()); la = math.sqrt(sum(v * v for v in a)); a = [v / la for v in a]
            ho = _axis_point(hg, a); so = _axis_point(sg, a)
            # head end = the end of the body nearer the head cylinder face
            hf = [f for f in b.faces if f.geometry.objectType.endswith('Cylinder') and abs(f.geometry.radius - hg.radius) < 1e-6][0]
            sf = [f for f in b.faces if f.geometry.objectType.endswith('Cylinder') and abs(f.geometry.radius - sg.radius) < 1e-6][0]
            ph = sum(c * a[i] for i, c in enumerate(_com_face(hf))); ps = sum(c * a[i] for i, c in enumerate(_com_face(sf)))
            if ph < ps:
                a = [-v for v in a]
            out.append((n, a, ho, hg.radius * 10, _proj(b, a)[1]))
    return out


def _com_face(f):
    k = f.boundingBox
    return ((k.minPoint.x + k.maxPoint.x) * 5, (k.minPoint.y + k.maxPoint.y) * 5, (k.minPoint.z + k.maxPoint.z) * 5)


def _path(c0, a, s0, s1, r):
    P = lambda s: adsk.core.Point3D.create((c0[0] + a[0] * s) / 10, (c0[1] + a[1] * s) / 10, (c0[2] + a[2] * s) / 10)
    return TB.createCylinderOrCone(P(s0), r / 10, P(s1), r / 10)


def _hits(box_min, box_max, parts):
    return [(n, b) for n, b, k in parts
            if k.minPoint.x * 10 < box_max[0] and box_min[0] < k.maxPoint.x * 10 and k.minPoint.y * 10 < box_max[1]
            and box_min[1] < k.maxPoint.y * 10 and k.minPoint.z * 10 < box_max[2] and box_min[2] < k.maxPoint.z * 10]


def access(doc_name, only=None, reach=REACH, skip=SKIP):
    app = adsk.core.Application.get()
    doc = next(d for d in app.documents if d.name == doc_name)
    des = adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))
    parts = []
    for o in des.rootComponent.allOccurrences:
        n = o.component.name
        if n.startswith(tuple(skip)) or not o.isLightBulbOn:
            continue
        for b in o.bRepBodies:
            if b.isSolid and b.isLightBulbOn:
                parts.append((n, b, b.boundingBox))
    rows = []
    for o in des.rootComponent.allOccurrences:
        n = o.component.name
        if only and only not in n:
            continue
        for name, a, c0, r, top in _screws(o.component):
            s0, s1 = top + 0.05, top + reach
            e = [c0[i] + a[i] * s for s in (s0, s1) for i in range(3)]
            lo = [min(e[i], e[i + 3]) - r for i in range(3)]; hi = [max(e[i], e[i + 3]) + r for i in range(3)]
            rr = r - 0.05
            for pn, pb in _hits(lo, hi, parts):
                x = _path(c0, a, s0, s1, rr)
                TB.booleanOperation(x, TB.copy(pb), adsk.fusion.BooleanTypes.IntersectionBooleanType)
                v = x.volume * 1000
                if v < MIN_MM3:
                    continue
                first = reach                                    # first hit distance, STEP resolution
                s = s0
                while s < s1:
                    y = _path(c0, a, s, min(s + STEP, s1), rr)
                    TB.booleanOperation(y, TB.copy(pb), adsk.fusion.BooleanTypes.IntersectionBooleanType)
                    if y.volume * 1000 > 0.05:
                        first = s - top; break
                    s += STEP
                rows.append((round(v, 1), round(first, 1), pn, '%s / %s' % (n, name)))
    return sorted(rows, key=lambda t: (t[1], -t[0]))
