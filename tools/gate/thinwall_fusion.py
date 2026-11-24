"""Fusion side of the thin-wall gate: read the facts thinwall_core.judge() works on. Runs through fusion_rpc; read-only.

JOB = {"doc", "parts": [{"name", "component" | "path", "body"}], "budget_s": 50}

Per printed body, per face: its surface type, area, middle, for a cylinder its diameter and whether it is a hole, and
its triangles in world mm, wound so that the right-hand normal points out of the material. The triangles are the
face's display mesh (Fusion keeps one for every face it has drawn: reading it costs nothing). A face that has none (a
body that was never drawn, a hidden one) is meshed with a MeshCalculator at normal quality: meshing every face that
way took Fusion minutes on the 216-tooth pulley. The winding is checked against the mesh's own node normals."""
import adsk.core, adsk.fusion, math, time

CM = 10.0
TYPES = {0: 'plane', 1: 'cylinder', 2: 'cone', 3: 'sphere', 4: 'torus', 5: 'elliptical_cylinder', 6: 'elliptical_cone', 7: 'nurbs'}


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


def _find(des, spec, by_comp):
    """(proxy body, occurrence or None)."""
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
    return bodies[0], occ


def _mesh(face):
    """(node coordinates cm, node normals, indices, source) of a face in the frame of the face's own component."""
    m = None
    try:
        m = face.meshManager.displayMeshes.bestMesh
    except Exception:
        m = None
    src = 'display'
    if m is None or m.triangleCount == 0:
        mc = face.meshManager.createMeshCalculator()
        mc.setQuality(adsk.fusion.TriangleMeshQualityOptions.NormalQualityTriangleMesh)
        m = mc.calculate(); src = 'calculated'
    return m.nodeCoordinatesAsDouble, m.normalVectorsAsDouble, m.nodeIndices, src


def _concave(face, g):
    """A cylinder whose material is outside it (a hole)."""
    p = face.pointOnFace
    ok, n = face.evaluator.getNormalAtPoint(p)
    o, a = g.origin, g.axis
    v = [p.x - o.x, p.y - o.y, p.z - o.z]
    al = math.sqrt(a.x * a.x + a.y * a.y + a.z * a.z)
    ax = [a.x / al, a.y / al, a.z / al]
    t = sum(v[i] * ax[i] for i in range(3))
    r = [v[i] - t * ax[i] for i in range(3)]
    return (n.x * r[0] + n.y * r[1] + n.z * r[2]) < 0


def job(JOB):
    t_start = time.time()
    d = _doc(JOB.get('doc'))
    des = adsk.fusion.Design.cast(d.products.itemByProductType('DesignProductType'))
    by_comp = {}
    for o in _occurrences(des.rootComponent):
        by_comp.setdefault(o.component.name, []).append(o)
    parts, errors = [], []
    budget = float(JOB.get('budget_s') or 50)
    for spec in JOB['parts']:
        if time.time() - t_start > budget:
            errors.append({'part': spec['name'], 'error': 'not read: the call ran out of its %g s' % budget}); continue
        t = time.time()
        try:
            b, occ = _find(des, spec, by_comp)
        except Exception as e:
            errors.append({'part': spec['name'], 'error': '%s: %s' % (type(e).__name__, e)}); continue
        nb = b.nativeObject or b
        M = occ.transform2 if occ else adsk.core.Matrix3D.create()
        m = [M.getCell(r, c) for r in range(4) for c in range(4)]
        faces, calc, flipped = [], 0, 0
        for f, pf in zip(nb.faces, b.faces):
            g = f.geometry
            ft = {'type': TYPES.get(g.surfaceType, str(g.surfaceType)), 'area': round(pf.area * 100, 4)}
            c = pf.centroid
            ft['mid'] = [round(c.x * CM, 4), round(c.y * CM, 4), round(c.z * CM, 4)]
            if ft['type'] in ('cylinder', 'cone', 'sphere', 'torus', 'plane'):
                # the exact surface, world mm: the judge measures the wall to it, not to the mesh's chords
                o = g.origin if ft['type'] != 'torus' else g.origin
                ft['o'] = [round((m[0] * o.x + m[1] * o.y + m[2] * o.z + m[3]) * CM, 5), round((m[4] * o.x + m[5] * o.y + m[6] * o.z + m[7]) * CM, 5),
                           round((m[8] * o.x + m[9] * o.y + m[10] * o.z + m[11]) * CM, 5)]
                ax = g.normal if ft['type'] == 'plane' else (g.axis if ft['type'] != 'sphere' else None)
                if ax is not None:
                    w = [m[0] * ax.x + m[1] * ax.y + m[2] * ax.z, m[4] * ax.x + m[5] * ax.y + m[6] * ax.z, m[8] * ax.x + m[9] * ax.y + m[10] * ax.z]
                    l = math.sqrt(sum(x * x for x in w)) or 1.0
                    ft['a'] = [round(x / l, 9) for x in w]
                if ft['type'] in ('cylinder', 'cone', 'sphere'):
                    ft['r'] = round(g.radius * CM, 6)
                if ft['type'] == 'cone':
                    ft['half_deg'] = round(math.degrees(g.halfAngle), 6)
                if ft['type'] == 'torus':
                    ft['R'], ft['r'] = round(g.majorRadius * CM, 6), round(g.minorRadius * CM, 6)
            if ft['type'] == 'cylinder':
                ft['d'] = round(g.radius * 2 * CM, 4)
                ft['concave'] = _concave(f, g)
            co, nv, ix, src = _mesh(f)
            calc += src == 'calculated'
            v = []
            for k in range(0, len(co), 3):
                x, y, z = co[k], co[k + 1], co[k + 2]
                v.extend((round((m[0] * x + m[1] * y + m[2] * z + m[3]) * CM, 5), round((m[4] * x + m[5] * y + m[6] * z + m[7]) * CM, 5),
                          round((m[8] * x + m[9] * y + m[10] * z + m[11]) * CM, 5)))
            # winding: the cross product of each triangle against its nodes' normals (native frame; a rigid transform
            # keeps the sign)
            s = 0.0
            for k in range(0, len(ix) - 2, 3):
                i, j, l = ix[k], ix[k + 1], ix[k + 2]
                ux, uy, uz = co[3 * j] - co[3 * i], co[3 * j + 1] - co[3 * i + 1], co[3 * j + 2] - co[3 * i + 2]
                wx, wy, wz = co[3 * l] - co[3 * i], co[3 * l + 1] - co[3 * i + 1], co[3 * l + 2] - co[3 * i + 2]
                cx, cy, cz = uy * wz - uz * wy, uz * wx - ux * wz, ux * wy - uy * wx
                s += cx * (nv[3 * i] + nv[3 * j] + nv[3 * l]) + cy * (nv[3 * i + 1] + nv[3 * j + 1] + nv[3 * l + 1]) + cz * (nv[3 * i + 2] + nv[3 * j + 2] + nv[3 * l + 2])
            ix = list(ix)
            if s < 0:
                flipped += 1
                for k in range(0, len(ix) - 2, 3):
                    ix[k + 1], ix[k + 2] = ix[k + 2], ix[k + 1]
            if M.determinant < 0:
                for k in range(0, len(ix) - 2, 3):
                    ix[k + 1], ix[k + 2] = ix[k + 2], ix[k + 1]
            ft['v'], ft['i'] = v, ix
            faces.append(ft)
        bb = b.boundingBox
        parts.append({'name': spec['name'], 'faces': faces, 'face_count': b.faces.count, 'volume': round(b.volume * 1000, 2),
                      'area': round(b.area * 100, 2),
                      'box': [[bb.minPoint.x * CM, bb.minPoint.y * CM, bb.minPoint.z * CM], [bb.maxPoint.x * CM, bb.maxPoint.y * CM, bb.maxPoint.z * CM]],
                      'meshed_faces': calc, 'flipped_faces': flipped, 's': round(time.time() - t, 3)})
    return {'doc': d.name, 'version': d.dataFile.versionNumber if d.dataFile else 0, 'modified': d.isModified,
            'parts': parts, 'errors': errors, 'fusion_s': round(time.time() - t_start, 3)}
