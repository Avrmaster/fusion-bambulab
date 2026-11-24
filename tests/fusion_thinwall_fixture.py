"""Fusion side of tests/test_thinwall_fusion.py: a new, unsaved document with two printed bodies whose walls are known.

JOB = {}. Returns {"doc": the new document's name, "cases": {case: [x, y, z] of the wall's middle, world mm}}.

Block (120 x 40 x 20, nested one level down in an occurrence moved away from the origin):
  sheet_1_0   a slot from the top leaves a 1.0 floor                     -> under_two_lines 1.0 (0.6 project)
  layer_0_3   a slot leaves a 0.3 floor: one layer of 0.3, flat on the bed -> sacrificial layer, not reported
  wall_0_5    two pockets leave a 0.5 upright wall                        -> under_one_line 0.5
  hole_1_2    a D6 hole 1.2 from the side face                            -> under_two_lines 1.2 (to the exact cylinder)
  wall_2_0    two pockets leave a 2.0 wall                                -> passes
  wall_1_4    two pockets leave a 1.4 wall                                -> thin_wall 1.4
  chamfer     a 45 degree chamfer along the top back edge                 -> a wedge, not reported
Wheel (0.4 project): a D30 x 6 disc with a D10 bore and 30 grooves cut round it; the teeth between the grooves are
about 1.0 wide and taper by 12 degrees -> the tooth rule, not reported. The test closes the document without saving."""
import adsk.core, adsk.fusion, math

OFFSET = (60.0, -30.0, 15.0)


def _box(tbm, x0, y0, z0, x1, y1, z1):
    c = adsk.core.Point3D.create((x0 + x1) / 20, (y0 + y1) / 20, (z0 + z1) / 20)
    return tbm.createBox(adsk.core.OrientedBoundingBox3D.create(c, adsk.core.Vector3D.create(1, 0, 0), adsk.core.Vector3D.create(0, 1, 0),
                                                                (x1 - x0) / 10, (y1 - y0) / 10, (z1 - z0) / 10))


def _cut(tbm, body, tool):
    tbm.booleanOperation(body, tool, adsk.fusion.BooleanTypes.DifferenceBooleanType)


def job(JOB):
    app = adsk.core.Application.get()
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    des = adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))
    tbm = adsk.fusion.TemporaryBRepManager.get()
    block = _box(tbm, 0, 0, 0, 120, 40, 20)
    cases = {}
    _cut(tbm, block, _box(tbm, 5, 5, 1.0, 15, 35, 21)); cases['sheet_1_0'] = [10, 20, 0.5]
    _cut(tbm, block, _box(tbm, 20, 5, 0.3, 30, 35, 21)); cases['layer_0_3'] = [25, 20, 0.15]
    _cut(tbm, block, _box(tbm, 35, 5, 5, 40, 35, 21)); _cut(tbm, block, _box(tbm, 40.5, 5, 5, 45, 35, 21)); cases['wall_0_5'] = [40.25, 20, 12]
    hole = tbm.createCylinderOrCone(adsk.core.Point3D.create(5.5, 0.42, -0.1), 0.3, adsk.core.Point3D.create(5.5, 0.42, 2.1), 0.3)
    _cut(tbm, block, hole); cases['hole_1_2'] = [55, 0.6, 10]
    _cut(tbm, block, _box(tbm, 62, 5, 5, 67, 35, 21)); _cut(tbm, block, _box(tbm, 69, 5, 5, 74, 35, 21)); cases['wall_2_0'] = [68, 20, 12]
    _cut(tbm, block, _box(tbm, 80, 5, 5, 85, 35, 21)); _cut(tbm, block, _box(tbm, 86.4, 5, 5, 91, 35, 21)); cases['wall_1_4'] = [85.7, 20, 12]
    # 45 degree chamfer along the top back edge (y = 40, z = 20), 3 mm: a box turned 45 degrees about x
    s = 3.0 / math.sqrt(2)
    cb = adsk.core.OrientedBoundingBox3D.create(adsk.core.Point3D.create(6.0, 4.0, 2.0), adsk.core.Vector3D.create(1, 0, 0),
                                                 adsk.core.Vector3D.create(0, math.cos(math.pi / 4), math.sin(math.pi / 4)), 13.0, 2 * s / 10, 2 * s / 10)
    _cut(tbm, block, tbm.createBox(cb)); cases['chamfer'] = [100, 39, 19]
    # the wheel, beside the block
    wc = (160.0, 20.0)
    wheel = tbm.createCylinderOrCone(adsk.core.Point3D.create(wc[0] / 10, wc[1] / 10, 0), 1.5, adsk.core.Point3D.create(wc[0] / 10, wc[1] / 10, 0.6), 1.5)
    _cut(tbm, wheel, tbm.createCylinderOrCone(adsk.core.Point3D.create(wc[0] / 10, wc[1] / 10, -0.01), 0.5, adsk.core.Point3D.create(wc[0] / 10, wc[1] / 10, 0.61), 0.5))
    N = 30
    for k in range(N):
        t = 2 * math.pi * (k + 0.5) / N
        u = adsk.core.Vector3D.create(math.cos(t), math.sin(t), 0); v = adsk.core.Vector3D.create(-math.sin(t), math.cos(t), 0)
        c = adsk.core.Point3D.create((wc[0] + 15.0 * math.cos(t)) / 10, (wc[1] + 15.0 * math.sin(t)) / 10, 0.3)
        _cut(tbm, wheel, tbm.createBox(adsk.core.OrientedBoundingBox3D.create(c, u, v, 0.30, 0.18, 0.8)))     # 1.5 deep, 1.8 wide: teeth 1.0 to 1.3
    cases['wheel'] = [wc[0], wc[1] + 14.3, 3.0]

    def add(comp, named):
        bf = comp.features.baseFeatures.add(); bf.startEdit()
        try:
            for name, b in named:
                comp.bRepBodies.add(b, bf).name = name
        finally:
            bf.finishEdit()
    m = adsk.core.Matrix3D.create(); m.translation = adsk.core.Vector3D.create(OFFSET[0] / 10, OFFSET[1] / 10, OFFSET[2] / 10)
    asm = des.rootComponent.occurrences.addNewComponent(m); asm.component.name = 'Assembly'
    occ = asm.component.occurrences.addNewComponent(adsk.core.Matrix3D.create()); occ.component.name = 'Block'
    add(occ.component, [('Block', block)])
    occ2 = asm.component.occurrences.addNewComponent(adsk.core.Matrix3D.create()); occ2.component.name = 'Wheel'
    add(occ2.component, [('Wheel', wheel)])
    return {'doc': doc.name, 'cases': {k: [v[i] + OFFSET[i] for i in range(3)] for k, v in cases.items()}}
