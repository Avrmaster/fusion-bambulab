"""Independent audit of the thin-wall gate, the Fusion side. NO code of the gate: the B-rep is asked directly.

For every pair of faces of a printed body whose boxes come closer than the largest limit, Fusion's
measureMinimumDistance gives the closest points; the pair is a wall when the material lies between them
(pointContainment of the middle) and the outward normals there look at each other (within the corner angle), and not a
slit (normals facing each other across air). Pairs of faces that share an edge are wedges (thin only at their edge):
their dihedral angle is read at the edge's middle, and a convex edge sharper than the wedge angle is a knife edge. Two
faces that touch without sharing an edge, facing away from each other there, are a wall of zero width.

Frames, learned the hard way on Robot Arm V2 (its J2 and J3 parts sit in moved occurrences):
  - measureMinimumDistance and pointContainment take the proxies and work in the assembly's frame;
  - a face's (or an edge's) evaluator works in its component's frame, proxy or not: points go through the inverse of
    the occurrence's transform first, normals come back through the transform;
  - the evaluator's normal is the surface's, not always the face's: the outward sign is settled once per face by a
    point 0.01 mm off it;
  - on a plane, measureMinimumDistance can answer with a point outside the face (its unbounded plane): then the two
    faces are measured again as sheet bodies (TemporaryBRepManager.copy).
Resumable: JOB carries the part list and where to start.

JOB = {"doc", "parts": [{"name", "component", "body", "tmax", "teeth": [face index]}], "start": [part, pair], "budget": s}"""
import adsk.core, adsk.fusion, math, time

IN = None


def P(v): return adsk.core.Point3D.create(v[0] / 10, v[1] / 10, v[2] / 10)
def dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


class Body:
    def __init__(self, des, spec):
        occs = [o for o in des.rootComponent.allOccurrences if o.component.name == spec['component']]
        self.occ = occs[0] if occs else None
        bodies = self.occ.bRepBodies if self.occ else des.rootComponent.bRepBodies
        self.b = [b for b in bodies if b.name == spec['body']][0]           # proxy: assembly frame
        self.nb = self.b.nativeObject or self.b                             # native: component frame
        self.M = self.occ.transform2 if self.occ else adsk.core.Matrix3D.create()
        self.Mi = self.M.copy(); self.Mi.invert()
        self.faces = list(self.b.faces); self.nfaces = list(self.nb.faces)
        self.sign = {}

    def to_native(self, p):
        q = p.copy(); q.transformBy(self.Mi); return q

    def to_world(self, p):
        q = p.copy(); q.transformBy(self.M); return q

    def mm(self, p_world):
        return [p_world.x * 10, p_world.y * 10, p_world.z * 10]

    def inside(self, p_world):
        return self.b.pointContainment(p_world) == adsk.fusion.PointContainment.PointInsidePointContainment

    def containment(self, p_world):
        return self.b.pointContainment(p_world)

    def normal(self, i, p_world):
        """Outward normal of face i at a point of the assembly, in the assembly's frame."""
        f = self.nfaces[i]
        ok, n = f.evaluator.getNormalAtPoint(self.to_native(p_world))
        v = adsk.core.Vector3D.create(n.x, n.y, n.z); v.transformBy(self.M)
        if i not in self.sign:
            q = f.pointOnFace
            ok2, m = f.evaluator.getNormalAtPoint(q)
            w = adsk.core.Vector3D.create(m.x, m.y, m.z); w.transformBy(self.M)
            qw = self.to_world(q)
            off = adsk.core.Point3D.create(qw.x + w.x * 0.001, qw.y + w.y * 0.001, qw.z + w.z * 0.001)
            self.sign[i] = -1.0 if self.inside(off) else 1.0
        s = self.sign[i]
        return [v.x * s, v.y * s, v.z * s]

    def off_face(self, i, p_world):
        """Distance (cm) from the point to face i's surface."""
        f = self.nfaces[i]; pn = self.to_native(p_world)
        ok, uv = f.evaluator.getParameterAtPoint(pn)
        if not ok:
            return 1e9
        ok, q = f.evaluator.getPointAtParameter(uv)
        return q.distanceTo(pn) if ok else 1e9

    def on_face(self, i, p_world):
        f = self.nfaces[i]; pn = self.to_native(p_world)
        ok, uv = f.evaluator.getParameterAtPoint(pn)
        return bool(ok and f.evaluator.isParameterOnFace(uv) and self.off_face(i, p_world) < 1e-4)


def box_gap(a, b):
    g = 0.0
    for lo1, hi1, lo2, hi2 in ((a.minPoint.x, a.maxPoint.x, b.minPoint.x, b.maxPoint.x), (a.minPoint.y, a.maxPoint.y, b.minPoint.y, b.maxPoint.y),
                               (a.minPoint.z, a.maxPoint.z, b.minPoint.z, b.maxPoint.z)):
        d = max(lo2 - hi1, lo1 - hi2, 0.0)
        g += d * d
    return math.sqrt(g) * 10


def job(J):
    app = adsk.core.Application.get(); d = next(x for x in app.documents if x.name == J['doc'])
    des = adsk.fusion.Design.cast(d.products.itemByProductType('DesignProductType'))
    mmgr = app.measureManager
    tbm = adsk.fusion.TemporaryBRepManager.get()
    t0 = time.time(); pi, qi = J['start']; rows = []
    while pi < len(J['parts']):
        spec = J['parts'][pi]; B = Body(des, spec); tmax = spec['tmax']
        faces = B.faces; n = len(faces)
        boxes = [f.boundingBox for f in faces]                              # proxies: assembly frame
        ids = [f.tempId for f in B.nfaces]
        index = {t: k for k, t in enumerate(ids)}
        shared = {}                                                         # (i, j) -> native edges
        for e in B.nb.edges:
            fs = [index.get(f.tempId) for f in e.faces]
            if len(fs) == 2 and None not in fs:
                shared.setdefault((min(fs), max(fs)), []).append(e)
        sheets = {}

        def sheet(k):
            if k not in sheets:
                sheets[k] = tbm.copy(faces[k])
            return sheets[k]
        cell = {}
        h = 0.4
        for i, bb in enumerate(boxes):
            lo = [int(math.floor((v - tmax / 10) / h)) for v in (bb.minPoint.x, bb.minPoint.y, bb.minPoint.z)]
            hi = [int(math.floor((v + tmax / 10) / h)) for v in (bb.maxPoint.x, bb.maxPoint.y, bb.maxPoint.z)]
            for x in range(lo[0], hi[0] + 1):
                for y in range(lo[1], hi[1] + 1):
                    for z in range(lo[2], hi[2] + 1):
                        cell.setdefault((x, y, z), []).append(i)
        cand = set()
        for ids_ in cell.values():
            for a_ in range(len(ids_)):
                for b_ in range(a_ + 1, len(ids_)):
                    cand.add((ids_[a_], ids_[b_]) if ids_[a_] < ids_[b_] else (ids_[b_], ids_[a_]))
        teeth = set(spec.get('teeth') or [])          # the audit's own tooth faces (audit_judge.tooth_faces): a pair of two is exempt anyway
        pairs = sorted(p_ for p_ in cand if not (p_[0] in teeth and p_[1] in teeth) and box_gap(boxes[p_[0]], boxes[p_[1]]) < tmax)
        setup_s = round(time.time() - t0, 2)
        done_here = 0
        while qi < len(pairs):
            done_here += 1
            if time.time() - t0 > J['budget'] and done_here > 500:
                return {'rows': rows, 'next': [pi, qi], 'done': False, 'pairs': len(pairs), 'setup_s': setup_s}
            i, j = pairs[qi]; qi += 1
            if (i, j) in shared:
                for e in shared[(i, j)]:
                    ok, p0, p1 = e.evaluator.getParameterExtents()
                    ok, pn = e.evaluator.getPointAtParameter((p0 + p1) / 2)
                    pt = B.to_world(pn)
                    na, nb = B.normal(i, pt), B.normal(j, pt)
                    ang = math.degrees(math.acos(max(-1, min(1, dot(na, nb)))))   # between the outward normals
                    inner = 180.0 - ang
                    bis = [-(na[k] + nb[k]) for k in range(3)]; l = math.sqrt(dot(bis, bis))
                    convex = False
                    if l > 1e-9:
                        q = [pt.x * 10 + bis[k] / l * 0.02 for k in range(3)]
                        convex = B.inside(P(q))
                    if convex and inner < 25:
                        rows.append({'part': spec['name'], 'i': i, 'j': j, 'kind': 'edge', 'inner_deg': round(inner, 2), 'at': [round(x, 3) for x in B.mm(pt)]})
                continue
            fi, fj = faces[i], faces[j]
            r = mmgr.measureMinimumDistance(fi, fj)
            dist = r.value * 10
            if dist >= tmax:
                continue
            pa, pb = r.positionOne, r.positionTwo
            if B.off_face(i, pb) < B.off_face(i, pa):     # which point lies on which face is not promised
                pa, pb = pb, pa
            if not (B.on_face(i, pa) and B.on_face(j, pb)):
                # measured to a face's unbounded surface (seen on planes): measure the two faces as sheet bodies
                r = mmgr.measureMinimumDistance(sheet(i), sheet(j))
                dist = r.value * 10
                if dist >= tmax:
                    continue
                pa, pb = r.positionOne, r.positionTwo
                if B.off_face(i, pb) < B.off_face(i, pa):
                    pa, pb = pb, pa
            if dist < 1e-5:
                # two faces that touch without sharing an edge: a wall of zero width when they face away from each
                # other there (the plastic between them pinches to nothing)
                na, nb = B.normal(i, pa), B.normal(j, pa)
                if dot(na, nb) < -0.94:
                    rows.append({'part': spec['name'], 'i': i, 'j': j, 'kind': 'touch', 'at': [round(x, 3) for x in B.mm(pa)],
                                 'opposed_deg': round(math.degrees(math.acos(max(-1, min(1, -dot(na, nb))))), 2)})
                continue
            mid = [(pa.x + pb.x) * 5, (pa.y + pb.y) * 5, (pa.z + pb.z) * 5]
            c = B.containment(P(mid))
            if c != adsk.fusion.PointContainment.PointInsidePointContainment:
                # closest points on edges (the top edge of a wall): the middle is on the boundary; plastic right
                # beside it (0.02 mm along an axis) still makes it a wall
                # the segment slid 0.02 mm sideways must keep both ends on their faces and its middle in plastic (two
                # faces that only meet corner to corner, with the segment lying on a third face, are no wall)
                def slid(dx, dy, dz):
                    s_ = [dx / 10, dy / 10, dz / 10]
                    qa = adsk.core.Point3D.create(pa.x + s_[0], pa.y + s_[1], pa.z + s_[2])
                    qb = adsk.core.Point3D.create(pb.x + s_[0], pb.y + s_[1], pb.z + s_[2])
                    return B.inside(P([mid[0] + dx, mid[1] + dy, mid[2] + dz])) and B.off_face(i, qa) < 1e-4 and B.off_face(j, qb) < 1e-4 \
                        and B.on_face(i, qa) and B.on_face(j, qb)
                if c == adsk.fusion.PointContainment.PointOutsidePointContainment or not any(
                        slid(dx, dy, dz) for dx, dy, dz in ((0.02, 0, 0), (-0.02, 0, 0), (0, 0.02, 0), (0, -0.02, 0), (0, 0, 0.02), (0, 0, -0.02))):
                    continue
            na, nb = B.normal(i, pa), B.normal(j, pb)
            ab = [(pb.x - pa.x) * 10, (pb.y - pa.y) * 10, (pb.z - pa.z) * 10]
            if dot(na, ab) > 0 or dot(nb, ab) < 0:
                continue                                   # the outward normals face each other: air between (a slit)
            look = math.degrees(math.acos(max(-1, min(1, -dot(na, nb)))))     # 0: the two faces look at each other
            rows.append({'part': spec['name'], 'i': i, 'j': j, 'kind': 'pair', 'd': round(dist, 4), 'look_deg': round(look, 2),
                         'a': [round(x, 3) for x in B.mm(pa)], 'b': [round(x, 3) for x in B.mm(pb)],
                         'na': [round(x, 4) for x in na], 'nb': [round(x, 4) for x in nb],
                         'ta': fi.geometry.surfaceType, 'tb': fj.geometry.surfaceType,
                         'area_a': round(fi.area * 100, 3), 'area_b': round(fj.area * 100, 3)})
        pi += 1; qi = 0
    return {'rows': rows, 'next': [pi, 0], 'done': True}
