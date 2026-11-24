"""Fusion side of the overhang gate: read the facts overhang_core judges. Read-only. gate.py pastes heatset_core.py,
heatset_fusion.py, overhang_core.py and this file in that order: one call reads the heat-set facts too (the pockets are
the one allowed support), so heatset_fusion's job() is kept as _heatset_job and this file's job() runs both.

JOB = {"doc", "parts": [{"name", "component" | "path", "body", "face"}], "rules": overhang rules, "heatset_rules"}

Per printed body, in world mm with "down" the bed face's outward normal:
  every planar face that faces down steeper than the limit: its outward normal, area, height range and its edges as
  polylines; for a flat one (a ceiling) each polyline segment says whether the material goes on downward beyond it
  (a point 0.4 mm beyond the edge, on the side away from the face, and 0.2 mm below the ceiling is inside the body):
  the anchors of a bridge;
  every other face that is not a vertical cylinder: its downward triangles (the face's own mesh), cylinder and cone
  axis, diameter and whether it is concave (a hole);
  every straight edge between two downward faces: its ends and whether it is convex (a ridge the slicer sees)."""
import adsk.core, adsk.fusion, math, time

_heatset_job = job      # noqa: F821  heatset_fusion.job, pasted in front

_CM = 10.0
_OUT, _DN = 0.4, 0.2     # mm: the anchor probe beyond a ceiling edge


def _pmm(pt):
    return [pt.x * _CM, pt.y * _CM, pt.z * _CM]


def _pt(p):
    return adsk.core.Point3D.create(p[0] / _CM, p[1] / _CM, p[2] / _CM)


def _in(body, p):
    return body.pointContainment(_pt(p)) == adsk.fusion.PointContainment.PointInsidePointContainment


def _normal(face, pt=None):
    ok, n = face.evaluator.getNormalAtPoint(pt or face.pointOnFace)
    return [n.x, n.y, n.z]


def _strokes(edge, tol_cm=0.005):
    ev = edge.evaluator
    ok, t0, t1 = ev.getParameterExtents()
    ok, pts = ev.getStrokes(t0, t1, tol_cm)
    return [_pmm(q) for q in pts]


def _loops(body, face, down, flat):
    polys = []
    for lp in face.loops:
        for co in lp.coEdges:
            pts = _strokes(co.edge)
            if len(pts) >= 2:
                polys.append(pts)
    out = []
    e1, e2 = basis(down)                                                        # overhang_core
    segs = _segments([{'pts': p} for p in polys], e1, e2) if flat else []
    for pts in polys:
        anch = []
        if flat:
            for i in range(len(pts) - 1):
                m = [(pts[i][k] + pts[i + 1][k]) / 2 for k in range(3)]
                t = _norm(_sub(pts[i + 1], pts[i]))
                d = _norm(_cross(t, down))
                # the face is on the side where a point 0.02 mm off the edge is inside the face's outline
                q = [m[k] + 0.02 * d[k] for k in range(3)]
                dout = [-x for x in d] if _pip(_dot(q, e1), _dot(q, e2), segs) else d
                anch.append(_in(body, [m[k] + _OUT * dout[k] + _DN * down[k] for k in range(3)]))
        out.append({'pts': [[round(x, 4) for x in p] for p in pts], 'anch': anch})
    return out


def _tris(face, down, cos_keep):
    """The face's downward triangles [cx, cy, cz, nx, ny, nz, area mm2, x0, y0, z0, x1, y1, z1 (its box)] with
    n . down > cos_keep. The normal is the
    surface's own at the triangle's centre (a facet of a 45 deg cone leans up to a degree off it)."""
    calc = face.meshManager.createMeshCalculator()
    calc.setQuality(adsk.fusion.TriangleMeshQualityOptions.NormalQualityTriangleMesh)
    m = calc.calculate()
    P = m.nodeCoordinatesAsDouble; I = m.nodeIndices
    cand = []
    for k in range(0, len(I), 3):
        a, b, c = I[k], I[k + 1], I[k + 2]
        A = [P[3 * a] * _CM, P[3 * a + 1] * _CM, P[3 * a + 2] * _CM]
        B = [P[3 * b] * _CM, P[3 * b + 1] * _CM, P[3 * b + 2] * _CM]
        C = [P[3 * c] * _CM, P[3 * c + 1] * _CM, P[3 * c + 2] * _CM]
        n = _cross(_sub(B, A), _sub(C, A))
        ln = math.sqrt(_dot(n, n))
        if ln < 1e-12:
            continue
        cand.append(([(A[i] + B[i] + C[i]) / 3 for i in range(3)], ln / 2,
                     [min(A[i], B[i], C[i]) for i in range(3)] + [max(A[i], B[i], C[i]) for i in range(3)]))
    if not cand:
        return []
    ev = face.evaluator
    ok, params = ev.getParametersAtPoints([_pt(c_[0]) for c_ in cand])
    ok2, normals = ev.getNormalsAtParameters(params) if ok else (False, None)
    out = []
    for k, (cc, area, bx) in enumerate(cand):
        if ok2:
            nv = normals[k]; n = _norm([nv.x, nv.y, nv.z])
        else:
            n = _normal(face, _pt(cc))
        if _dot(n, down) <= cos_keep:
            continue
        out.append([round(x, 4) for x in cc] + [round(x, 6) for x in n] + [round(area, 5)] + [round(x, 3) for x in bx])
    return out


def _axis_face(face):
    g = face.geometry
    o = _pmm(g.origin)
    a = _norm([g.axis.x, g.axis.y, g.axis.z])
    p = face.pointOnFace
    pp = _pmm(p)
    v = _sub(pp, o); r = _sub(v, _mul(a, _dot(v, a)))
    n = _normal(face, p)
    return a, _dot(n, r) < 0, o


def _overhang_facts(body, spec, rules):
    down = _norm(spec['down'])
    lim = val(rules, 'limit_deg'); flat = val(rules, 'bridge_flat_deg')
    cos_keep = math.cos(math.radians(lim + 5))
    bb = body.boundingBox
    lo, hi = _pmm(bb.minPoint), _pmm(bb.maxPoint)
    bed_h = min(-_dot([x, y, z], down) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2]))
    faces, normals = [], {}
    plane_t, cyl_t, cone_t = adsk.core.Plane.classType(), adsk.core.Cylinder.classType(), adsk.core.Cone.classType()
    for i, f in enumerate(body.faces):
        gt = f.geometry.objectType
        rec = {'i': i, 'area': round(f.area * 100, 4)}
        if gt == plane_t:
            n = _normal(f)
            normals[i] = n
            s = slope(n, down)
            if s >= lim + 5:
                continue
            rec.update(type='plane', n=[round(x, 6) for x in n], c=_pmm(f.pointOnFace))
            rec['loops'] = _loops(body, f, down, s <= flat)
            hs = [-_dot(p, down) for lp in rec['loops'] for p in lp['pts']] or [-_dot(rec['c'], down)]
            rec['h'] = [round(min(hs), 4), round(max(hs), 4)]
            faces.append(rec)
            continue
        if gt in (cyl_t, cone_t):
            a, concave, o = _axis_face(f)
            if gt == cyl_t and abs(_dot(a, down)) > 0.9999:
                continue                                            # a vertical cylinder never faces down
            rec.update(type='cylinder' if gt == cyl_t else 'cone', axis=[round(x, 6) for x in a], concave=concave)
            if gt == cyl_t:
                rec['d'] = round(f.geometry.radius * 2 * _CM, 4)
        else:
            rec['type'] = {adsk.core.Sphere.classType(): 'sphere', adsk.core.Torus.classType(): 'torus'}.get(gt, 'nurbs')
        rec['tris'] = _tris(f, down, cos_keep)
        if rec['tris']:
            faces.append(rec)
    ridges = []
    face_index = {f.tempId: i for i, f in enumerate(body.faces)}
    for e in body.edges:
        if e.geometry.objectType != adsk.core.Line3D.classType() or e.faces.count != 2:
            continue
        f1, f2 = e.faces.item(0), e.faces.item(1)
        p0, p1 = _pmm(e.startVertex.geometry), _pmm(e.endVertex.geometry)
        mid = [(p0[k] + p1[k]) / 2 for k in range(3)]
        mp = _pt(mid)
        n1, n2 = _normal(f1, mp), _normal(f2, mp)
        if _dot(n1, down) <= 0.05 or _dot(n2, down) <= 0.05:
            continue
        dn = _norm(_sub(n1, n2))
        convex = not _in(body, [mid[k] + 0.05 * dn[k] for k in range(3)]) if math.sqrt(_dot(_sub(n1, n2), _sub(n1, n2))) > 1e-3 else False
        ridges.append({'p0': [round(x, 4) for x in p0], 'p1': [round(x, 4) for x in p1], 'convex': convex,
                       'faces': [face_index.get(f1.tempId), face_index.get(f2.tempId)]})
    return {'down': down, 'bed_h': round(bed_h, 4), 'box': [lo, hi], 'faces': faces, 'ridges': ridges,
            'n_faces': body.faces.count, 'volume': round(body.volume * 1000, 2), 'area': round(body.area * 100, 2)}


def job(JOB):
    t0 = time.time()
    hs = _heatset_job({'doc': JOB.get('doc'), 'parts': JOB['parts'], 'rules': JOB['heatset_rules']})
    t_hs = time.time() - t0
    d = _doc(JOB.get('doc'))                                                     # heatset_fusion
    des = adsk.fusion.Design.cast(d.products.itemByProductType('DesignProductType'))
    by_comp = {}
    for o in _occurrences(des.rootComponent):
        by_comp.setdefault(o.component.name, []).append(o)
    parts, errors = [], []
    for spec in JOB['parts']:
        if not spec.get('down'):
            continue
        t = time.time()
        try:
            b = _find_body(des, spec, by_comp)
            rec = _overhang_facts(b, spec, JOB['rules'])
        except Exception as e:
            errors.append({'part': spec['name'], 'error': '%s: %s' % (type(e).__name__, e)}); continue
        rec.update(name=spec['name'], face=spec.get('face'), s=round(time.time() - t, 3))
        parts.append(rec)
    return {'doc': d.name, 'version': d.dataFile.versionNumber if d.dataFile else 0, 'modified': d.isModified,
            'parts': parts, 'errors': errors + hs['errors'], 'heatset': {'parts': hs['parts'], 'inserts': hs['inserts']},
            'heatset_s': round(t_hs, 3), 'fusion_s': round(time.time() - t0, 3)}
