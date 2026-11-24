"""Fusion side of tests/test_overhang_fusion.py: a new, unsaved document with one body per known case of the overhang
gate, each in its own component inside an "Assembly" occurrence moved away from the origin. Every case prints with its
-z face on the bed. Pasted after heatset_core.py (perp). Returns {"doc", "cases": [names]}. The test closes the document
without saving.

   cone_50        a pedestal with a cone 50 deg from the vertical on top of it       overhang
   chamfer_45     the same at 45 deg                                                  passes
   round_hole     a block with a horizontal D6 hole                                   round_hole
   teardrop       the same hole with a 45 deg roof                                    passes
   heatset_side   a block with a heat-set pocket opening sideways (no insert)         allowed heatset_ceiling
   short_bridge   a block with a 4 mm wide tunnel, flat ceiling                       passes
   long_bridge    the same, 10 mm wide                                                long_bridge
   ledge          a 5 mm cantilever ledge at mid height                               overhang"""
import adsk.core, adsk.fusion, math

OFFSET = (130.0, -40.0, 25.0)


def _P(x, y, z):
    return adsk.core.Point3D.create(x / 10, y / 10, z / 10)


def _V(x, y, z):
    return adsk.core.Vector3D.create(x, y, z)


def _box(tbm, x0, y0, z0, x1, y1, z1):
    return tbm.createBox(adsk.core.OrientedBoundingBox3D.create(_P((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2), _V(1, 0, 0), _V(0, 1, 0),
                                                                (x1 - x0) / 10, (y1 - y0) / 10, (z1 - z0) / 10))


def _cyl(tbm, a, b, r):
    return tbm.createCylinderOrCone(_P(*a), r / 10, _P(*b), r / 10)


def _op(tbm, target, tool, kind):
    tbm.booleanOperation(target, tool, {'cut': adsk.fusion.BooleanTypes.DifferenceBooleanType,
                                        'join': adsk.fusion.BooleanTypes.UnionBooleanType,
                                        'and': adsk.fusion.BooleanTypes.IntersectionBooleanType}[kind])
    return target


def _cone(tbm, deg):
    b = _cyl(tbm, (15, 10, 0), (15, 10, 10), 5.0)
    r1 = 5.0 + 6.0 * math.tan(math.radians(deg))
    c = tbm.createCylinderOrCone(_P(15, 10, 10), 0.5, _P(15, 10, 16), r1 / 10)
    return _op(tbm, b, c, 'join')


def _teardrop_tool(tbm, cx, cz, r, y0, y1):
    """A horizontal hole along y with a 45 deg roof: the circle plus the top of the square whose sides touch it."""
    t = _cyl(tbm, (cx, y0, cz), (cx, y1, cz), r)
    s = 2 * r
    sq = tbm.createBox(adsk.core.OrientedBoundingBox3D.create(_P(cx, (y0 + y1) / 2, cz), _V(1, 0, 1), _V(0, 1, 0), s / 10, (y1 - y0) / 10, s / 10))
    cap = _box(tbm, cx - 2 * r, y0, cz + r / math.sqrt(2) - 0.02, cx + 2 * r, y1, cz + 2 * r)
    _op(tbm, sq, cap, 'and')
    return _op(tbm, t, sq, 'join')


def _pocket(tbm, block, mouth, inward, prof):
    s = 0.0
    for d, depth in prof:
        a = [mouth[i] + inward[i] * (s - 0.01 if s == 0 else s) for i in range(3)]
        b = [mouth[i] + inward[i] * (s + depth) for i in range(3)]
        _op(tbm, block, _cyl(tbm, a, b, d / 2), 'cut')
        s += depth


def build(tbm, rules):
    out = {}
    out['cone_50'] = _cone(tbm, 50.0)
    out['chamfer_45'] = _cone(tbm, 45.0)
    b = _box(tbm, 0, 0, 0, 30, 20, 20)
    out['round_hole'] = _op(tbm, b, _cyl(tbm, (15, -1, 10), (15, 21, 10), 3.0), 'cut')
    b = _box(tbm, 0, 0, 0, 30, 20, 20)
    out['teardrop'] = _op(tbm, b, _teardrop_tool(tbm, 15, 10, 3.0, -1, 21), 'cut')
    b = _box(tbm, 0, 0, 0, 30, 20, 20)
    pk = rules['kinds']['M3x4x5']['pocket']
    _pocket(tbm, b, [15, 0, 10], [0, 1, 0], [(pk['relief']['d'][0], pk['relief']['depth']), (pk['lead_in']['d'], pk['lead_in']['depth']),
                                             (pk['hole']['d'], pk['hole']['min_depth_below_relief'] - pk['lead_in']['depth'])])
    out['heatset_side'] = b
    b = _box(tbm, 0, 0, 0, 30, 20, 20)
    out['short_bridge'] = _op(tbm, b, _box(tbm, 13, -1, 5, 17, 21, 10), 'cut')
    b = _box(tbm, 0, 0, 0, 30, 20, 20)
    out['long_bridge'] = _op(tbm, b, _box(tbm, 10, -1, 5, 20, 21, 10), 'cut')
    b = _box(tbm, 0, 0, 0, 20, 20, 20)
    out['ledge'] = _op(tbm, b, _box(tbm, 20, 0, 15, 25, 20, 20), 'join')
    return out


def job(JOB):
    app = adsk.core.Application.get()
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    des = adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))
    tbm = adsk.fusion.TemporaryBRepManager.get()
    bodies = build(tbm, JOB['heatset_rules'])
    m = adsk.core.Matrix3D.create(); m.translation = adsk.core.Vector3D.create(OFFSET[0] / 10, OFFSET[1] / 10, OFFSET[2] / 10)
    asm = des.rootComponent.occurrences.addNewComponent(m); asm.component.name = 'Assembly'
    for k, (name, body) in enumerate(sorted(bodies.items())):
        mm = adsk.core.Matrix3D.create(); mm.translation = adsk.core.Vector3D.create(k * 4.0, 0, 0)    # 40 mm apart
        occ = asm.component.occurrences.addNewComponent(mm); occ.component.name = name
        bf = occ.component.features.baseFeatures.add(); bf.startEdit()
        try:
            occ.component.bRepBodies.add(body, bf).name = name
        finally:
            bf.finishEdit()
    return {'doc': doc.name, 'cases': sorted(bodies)}
