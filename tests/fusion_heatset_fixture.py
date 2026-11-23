"""Fusion side of tests/test_heatset_fusion.py: a new, unsaved document with one printed block and 15 holes, each a
known case for the heat-set gate, plus the insert models. Pasted after heatset_core.py, heatset_fusion.py and golden.py.

JOB = {"rules": the heat-set rules}. Returns {"doc": the new document's name, "cases": {case: [x, y, z] of its mouth}}.
The block is 200 x 60 x 20 mm, top face at z = 20; most pockets open upwards from the top face, a row opens sideways
from the y = 0 face. The document is left open for the gate to read; the test closes it without saving."""
import adsk.core, adsk.fusion

CASES = [
    # name,            mouth (x, y),  profile from the mouth: [(d, depth)], insert: (variant, top below the mouth) or None
    ('good',            (10, 15), 'std', ('golden', 0.54)),
    ('good_relief_6_5', (25, 15), [(6.5, 0.54), (4.4, 1.5), (3.6, 5.0)], ('golden', 0.54)),
    ('no_relief',       (40, 15), [(4.4, 1.5), (3.6, 5.0)], ('golden', 0.0)),
    ('wrong_hole',      (55, 15), [(7.5, 0.54), (4.4, 1.5), (3.4, 5.0)], ('golden', 0.54)),
    ('short_hole',      (70, 15), [(7.5, 0.54), (4.4, 1.5), (3.6, 3.5)], ('golden', 0.54)),
    ('old_straight',    (85, 15), [(4.4, 6.0)], ('old_solid', 0.0)),
    ('no_insert',       (100, 15), 'std', None),
    ('not_flush',       (115, 15), 'std', ('golden', 0.0)),
    ('upside_down',     (130, 15), 'std', ('upside', 0.54)),
    ('screw_cbore',     (145, 15), [(6.5, 3.5), (3.4, 16.5)], None),
    ('in_recess',       (160, 15), [(12.0, 3.0), (7.5, 0.54), (4.4, 1.5), (3.6, 5.0)], ('golden', 3.54)),
    ('relief_depth',    (175, 15), [(7.5, 1.2), (4.4, 1.5), (3.6, 5.0)], ('golden', 1.2)),
    ('old_insert',      (190, 15), 'std', ('old_solid', 0.54)),
]
SIDE = [
    ('side_good',       (20, 10), 'std', ('golden', 0.54)),
    ('side_no_lead',    (60, 10), [(7.5, 0.54), (3.6, 6.5)], ('golden', 0.54)),
]


def _profile(spec, rules):
    if spec != 'std':
        return spec
    pk = rules['kinds']['M3x4x5']['pocket']
    return [(pk['relief']['d'][0], pk['relief']['depth']), (pk['lead_in']['d'], pk['lead_in']['depth']),
            (pk['hole']['d'], pk['hole']['min_depth_below_relief'] - pk['lead_in']['depth'])]


def _cut(tbm, block, mouth, inward, prof):
    """Subtract the stack of cylinders from the block: mouth in mm, inward a unit vector."""
    s = 0.0
    for d, depth in prof:
        a = [(mouth[i] + inward[i] * (s - 0.01 if s == 0 else s)) / 10 for i in range(3)]
        b = [(mouth[i] + inward[i] * (s + depth)) / 10 for i in range(3)]
        tool = tbm.createCylinderOrCone(adsk.core.Point3D.create(*a), d / 20, adsk.core.Point3D.create(*b), d / 20)
        tbm.booleanOperation(block, tool, adsk.fusion.BooleanTypes.DifferenceBooleanType)
        s += depth


def _insert(tbm, g, variant, top, out):
    if variant == 'golden':
        return golden_body(g, top, out)                                    # golden.py
    if variant == 'upside':
        b = golden_body(g, [top[i] - out[i] * g['length'] for i in range(3)], [-x for x in out])
        return b
    if variant == 'old_solid':                                             # the solid D5 x 4 cylinder most old refs are
        p0 = adsk.core.Point3D.create(*[top[i] / 10 for i in range(3)])
        p1 = adsk.core.Point3D.create(*[(top[i] - out[i] * 4.0) / 10 for i in range(3)])
        return tbm.createCylinderOrCone(p0, 0.25, p1, 0.25)
    raise ValueError(variant)


def job(JOB):
    rules = JOB['rules']; g = rules['kinds']['M3x4x5']['golden']
    app = adsk.core.Application.get()
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    des = adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))
    des.designType = adsk.fusion.DesignTypes.DirectDesignType
    tbm = adsk.fusion.TemporaryBRepManager.get()
    block = tbm.createBox(adsk.core.OrientedBoundingBox3D.create(adsk.core.Point3D.create(10.0, 3.0, 1.0), adsk.core.Vector3D.create(1, 0, 0),
                                                                  adsk.core.Vector3D.create(0, 1, 0), 20.0, 6.0, 2.0))
    cases, inserts = {}, []
    for name, (x, y), spec, ins in CASES:
        mouth = [float(x), float(y), 20.0]
        _cut(tbm, block, mouth, [0, 0, -1], _profile(spec, rules))
        cases[name] = mouth
        if ins:
            inserts.append((name, _insert(tbm, g, ins[0], [x, y, 20.0 - ins[1]], [0, 0, 1])))
    for name, (x, z), spec, ins in SIDE:
        mouth = [float(x), 0.0, float(z)]
        _cut(tbm, block, mouth, [0, 1, 0], _profile(spec, rules))
        cases[name] = mouth
        if ins:
            inserts.append((name, _insert(tbm, g, ins[0], [x, ins[1], z], [0, -1, 0])))
    # an insert with no hole under it: sunk into solid plastic between two cases
    inserts.append(('insert_in_solid', _insert(tbm, g, 'golden', [117.5, 45.0, 12.0], [0, 0, 1])))
    cases['insert_in_solid'] = [117.5, 45.0, 12.0]
    root = des.rootComponent
    occ = root.occurrences.addNewComponent(adsk.core.Matrix3D.create()); occ.component.name = 'Block'
    pb = occ.component.bRepBodies.add(block); pb.name = 'Block'
    hw = root.occurrences.addNewComponent(adsk.core.Matrix3D.create()); hw.component.name = 'HW (ref)'
    for name, b in inserts:
        nb = hw.component.bRepBodies.add(b)
        nb.name = g['name'] if name not in ('old_straight', 'old_insert') else 'insert M3x4x5 (%s)' % name
    return {'doc': doc.name, 'cases': cases}

