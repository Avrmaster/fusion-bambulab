"""Independent audit of the heat-set gate: NO code of the gate. For each pocket the gate listed (mouth M, axis out),
the hole is felt with pointContainment: at every 0.03 mm of depth, in 8 directions, the radius where plastic starts
(bisection to 0.005 mm). Inserts are found by a point test inside their wall, their top by an oriented box."""
import adsk.core, adsk.fusion, math, re, time

def P(v): return adsk.core.Point3D.create(v[0] / 10, v[1] / 10, v[2] / 10)

def frame(u):
    t = [1.0, 0, 0] if abs(u[0]) < 0.9 else [0, 1.0, 0]
    e1 = [u[1]*t[2]-u[2]*t[1], u[2]*t[0]-u[0]*t[2], u[0]*t[1]-u[1]*t[0]]; n = math.sqrt(sum(x*x for x in e1)); e1 = [x/n for x in e1]
    e2 = [u[1]*e1[2]-u[2]*e1[1], u[2]*e1[0]-u[0]*e1[2], u[0]*e1[1]-u[1]*e1[0]]
    return e1, e2

def job(J):
    app = adsk.core.Application.get(); d = next(x for x in app.documents if x.name == J['doc'])
    des = adsk.fusion.Design.cast(d.products.itemByProductType('DesignProductType'))
    occs = list(des.rootComponent.allOccurrences)
    IN = adsk.fusion.PointContainment.PointInsidePointContainment
    by = {}
    for o in occs: by.setdefault(o.component.name, []).append(o)
    pat = re.compile(r'(?i)\binsert\b')
    ins = []
    for o in occs:
        for b in o.bRepBodies:
            if pat.search(b.name): ins.append((o.fullPathName + '/' + b.name, b, b.boundingBox))
    mm = app.measureManager
    out = []; t0 = time.time()
    for job_ in J['pockets']:
        spec = J['parts'][job_['part']]
        o = by[spec['component']][0]; body = next(b for b in o.bRepBodies if b.name == spec['body'])
        M = job_['at']; u = [-x for x in job_['out']]          # inwards
        e1, e2 = frame(u)
        dirs = [[math.cos(k*math.pi/4)*e1[i] + math.sin(k*math.pi/4)*e2[i] for i in range(3)] for k in range(8)]
        prof = []
        s = -0.3
        while s <= 14.0:
            c = [M[i] + u[i]*s for i in range(3)]
            if body.pointContainment(P(c)) == IN:
                prof.append((round(s, 3), 'solid', None)); s += 0.03; continue
            rs = []
            for dv in dirs:
                far = [c[i] + dv[i]*8.0 for i in range(3)]
                if body.pointContainment(P(far)) != IN:
                    # nothing at 8 mm: is there plastic nearer? sample
                    hit = None
                    for r in (1.0, 1.5, 1.8, 2.0, 2.3, 2.6, 3.0, 3.5, 4.0, 5.0, 6.0, 7.0):
                        if body.pointContainment(P([c[i] + dv[i]*r for i in range(3)])) == IN: hit = r; break
                    if hit is None: rs.append(None); continue
                    lo, hi = 0.0, hit
                else:
                    lo, hi = 0.0, 8.0
                for _ in range(11):
                    m = (lo + hi) / 2
                    if body.pointContainment(P([c[i] + dv[i]*m for i in range(3)])) == IN: hi = m
                    else: lo = m
                rs.append(round(hi, 3))
            prof.append((round(s, 3), [None if r is None else round(2*r, 3) for r in rs], None))
            s += 0.03
        # inserts whose wall holds a point 2 mm into the pocket below each candidate depth
        found = []
        for depth in [x / 10 for x in range(0, 60, 5)]:
            c = [M[i] + u[i]*depth + e1[i]*2.0 for i in range(3)]
            for path, b, bb in ins:
                if not (bb.minPoint.x - 0.01 <= c[0]/10 <= bb.maxPoint.x + 0.01 and bb.minPoint.y - 0.01 <= c[1]/10 <= bb.maxPoint.y + 0.01 and bb.minPoint.z - 0.01 <= c[2]/10 <= bb.maxPoint.z + 0.01): continue
                if b.pointContainment(P(c)) == IN and path not in [f['path'] for f in found]:
                    ob = mm.getOrientedBoundingBox(b, adsk.core.Vector3D.create(*u), adsk.core.Vector3D.create(*e1))
                    cc = [ob.centerPoint.x*10, ob.centerPoint.y*10, ob.centerPoint.z*10]
                    sc = sum((cc[i]-M[i])*u[i] for i in range(3)); L = ob.length*10
                    w = ob.width*10; h = ob.height*10
                    # pilot test: plastic of the insert at r 2.3 near each end
                    near_top = [M[i] + u[i]*(sc - L/2 + 0.5) + e1[i]*2.3 for i in range(3)]
                    near_bot = [M[i] + u[i]*(sc + L/2 - 0.5) + e1[i]*2.3 for i in range(3)]
                    bore = [M[i] + u[i]*sc + e1[i]*1.4 for i in range(3)]
                    found.append({'path': path, 'top': round(sc - L/2, 3), 'len': round(L, 3), 'wid': [round(w, 3), round(h, 3)],
                                  'vol': round(b.volume*1000, 3), 'top_r23': b.pointContainment(P(near_top)) == IN,
                                  'bot_r23': b.pointContainment(P(near_bot)) == IN, 'bore_air': b.pointContainment(P(bore)) != IN})
        out.append({'part': job_['part'], 'at': M, 'prof': prof, 'inserts': found})
        if time.time() - t0 > J.get('budget', 45): break
    return {'rows': out}
