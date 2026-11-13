"""Heat-set insert pockets in Fusion: the sizes (one source for every project) plus tools to cut new pockets, convert old ones
and check them. The rules and the measurements behind the sizes are in skills/print-design ("Heat-set inserts").

Run through the Fusion MCP (fusion_mcp_execute, featureType script) with exec():
    P = r'<project>/fusion/tools/insert_pockets.py'
    g = {}; exec(open(P, encoding='utf-8').read(), g); g['DOC'] = 'MyDesign'      # DOC: the user may switch tabs mid-run
    g['plan']('Bracket')                 read-only: one row per old pocket, what convert() would do and why not
    g['convert']('Bracket')              one part per call: old straight pockets -> lead-in + narrow hole (sleeve join)
    g['audit']('Bracket')                read-only: every pocket has its lead-in and nothing solid on its axis
    g['cut']('Bracket', 'Body1', [((x, y, z), (ax, ay, az))])   new pockets: point on the face (mm), axis out of the material

Pocket, from the face down: melt relief (D x depth), lead-in (D x depth), hole down to `depth` below the relief floor.
The insert sits flush with the relief floor. Units: mm here, cm in the API.

Which old pockets convert() takes: cylindrical faces of the old diameter. With an `HW (ref)` child component (bought parts as
reference bodies), only those coaxial with an insert ref body (INSERT_RE); without one, with bare=True, every blind pocket no
longer than `depth` (check the plan first: the old diameter is often also a screw or rod hole). Refused, with a reason: a cavity
crossing the sleeve wall, a pocket too short for the lead-in, an unclear open end. A long hole with an insert at each end
counts as two pockets. A curved through exit (a radial set-screw pocket into a shaft bore) is trimmed where the full wall ends.
Each convert() adds one base feature of sleeves and one combine JOIN per host body at the end of the part's timeline."""
import adsk.core, adsk.fusion, math, re

# measured 2026-11-12, PETG-CF, Bambu P2S 0.4, slicer X-Y hole compensation 0.125 (skills/print-design)
POCKETS = {
    'M3x4x5': dict(relief_d=7.5, relief_depth=0.54, lead_d=4.4, lead_depth=1.5, hole_d=3.6, depth=6.5,
                   old_d=4.4,                        # the straight pocket it replaces (too loose with hole compensation)
                   insert_len=4.0, knurl_d=5.0, pilot_d=4.2),
}
KIND = 'M3x4x5'
DOC = None
HW_PREFIX = 'HW (ref)'
INSERT_RE = r'^insert M3x'
FEATURE = 'insert pocket sleeves'

_app = adsk.core.Application.get()
_TOL_R, _TOL_AX = 0.003, 0.004


def _k(kind=None):
    p = POCKETS[kind or KIND]
    return dict(r_old=p['old_d'] / 20, r_new=p['hole_d'] / 20, r_lead=p['lead_d'] / 20, lead=p['lead_depth'] / 10,
                depth=p['depth'] / 10, r_relief=p['relief_d'] / 20, relief=p['relief_depth'] / 10, p=p)


def _d():
    if DOC is None:
        return adsk.fusion.Design.cast(_app.activeProduct)
    docs = [x for x in _app.documents if x.name == DOC]
    assert docs, 'document %r is not open' % DOC
    assert len({x.dataFile.id if x.dataFile else id(x) for x in docs}) == 1, 'two different documents named %r' % DOC
    return adsk.fusion.Design.cast(docs[0].products.itemByProductType('DesignProductType'))


def _frame(a):
    t = (1, 0, 0) if abs(a[0]) < 0.9 else (0, 1, 0)
    u = (a[1] * t[2] - a[2] * t[1], a[2] * t[0] - a[0] * t[2], a[0] * t[1] - a[1] * t[0])
    n = math.sqrt(sum(x * x for x in u)); u = tuple(x / n for x in u)
    return u, (a[1] * u[2] - a[2] * u[1], a[2] * u[0] - a[0] * u[2], a[0] * u[1] - a[1] * u[0])


def _P(o, a, u, v, s, r=0.0, th=0.0):
    return adsk.core.Point3D.create(*[o[i] + a[i] * s + (u[i] * math.cos(th) + v[i] * math.sin(th)) * r for i in range(3)])


def _inside(body, p):
    return body.pointContainment(p) == adsk.fusion.PointContainment.PointInsidePointContainment


def _ring(body, o, a, u, v, s, r, n=16):
    return sum(_inside(body, _P(o, a, u, v, s, r, 2 * math.pi * k / n)) for k in range(n))


def _cyl(o, a, s_a, s_b, r):
    z = (0, 0, 0)
    return adsk.fusion.TemporaryBRepManager.get().createCylinderOrCone(_P(o, a, z, z, s_a), r, _P(o, a, z, z, s_b), r)


def _occ(comp_name):
    return next((o for o in _d().rootComponent.allOccurrences if o.component.name == comp_name), None)


def _faces(body, r):
    """cylindrical faces of radius r grouped into pockets: same axis line AND touching along it -> [o, a, s_lo, s_hi]"""
    groups = []
    for f in body.faces:
        g = f.geometry
        if not isinstance(g, adsk.core.Cylinder) or abs(g.radius - r) > _TOL_R:
            continue
        ax = g.axis; ax.normalize(); a = (ax.x, ax.y, ax.z); o = (g.origin.x, g.origin.y, g.origin.z)
        ss = [sum((p.asArray()[k] - o[k]) * a[k] for k in range(3)) for e in f.edges for p in (e.startVertex.geometry, e.endVertex.geometry)]
        for gr in groups:
            go, ga = gr[0], gr[1]
            dot = sum(a[k] * ga[k] for k in range(3))
            if abs(abs(dot) - 1) > 1e-6:
                continue
            w = [o[k] - go[k] for k in range(3)]; t = sum(w[k] * ga[k] for k in range(3))
            lo, hi = (t + min(ss), t + max(ss)) if dot > 0 else (t - max(ss), t - min(ss))
            if math.sqrt(max(0.0, sum(x * x for x in w) - t * t)) < _TOL_AX and lo <= gr[3] + 0.002 and hi >= gr[2] - 0.002:
                gr[2], gr[3] = min(gr[2], lo), max(gr[3], hi); break
        else:
            groups.append([o, a, min(ss), max(ss)])
    return groups


def _insert_axes(po):
    ho = next((ch for ch in po.childOccurrences if ch.component.name.startswith(HW_PREFIX)), None)
    if ho is None:
        return None
    TB = adsk.fusion.TemporaryBRepManager.get()
    T = ho.transform2.copy(); To = po.transform2.copy(); To.invert(); T.transformBy(To)
    out = []
    for ib in ho.component.bRepBodies:
        if re.match(INSERT_RE, ib.name):
            tb = TB.copy(ib); TB.transform(tb, T)
            g = max((f for f in tb.faces if isinstance(f.geometry, adsk.core.Cylinder)), key=lambda f: f.area).geometry
            ax = g.axis; ax.normalize()
            out.append((ib.name, (g.origin.x, g.origin.y, g.origin.z), (ax.x, ax.y, ax.z)))
    return out


def _on_axis(o, a, q):
    w = [q[k] - o[k] for k in range(3)]; t = sum(w[k] * a[k] for k in range(3))
    return math.sqrt(max(0.0, sum(x * x for x in w) - t * t)) < _TOL_AX


def plan(comp_name, bare=False, kind=None):
    """read-only: the old pockets of this part and what convert() would do with each."""
    K = _k(kind); po = _occ(comp_name)
    if po is None:
        return [dict(ok=False, why='no component %r' % comp_name)]
    ins = _insert_axes(po)
    rows = []
    for body in [b for b in po.component.bRepBodies if b.isSolid]:
        for o, a, s_lo, s_hi in _faces(body, K['r_old']):
            u, v = _frame(a)
            name = next((n for n, io, ia in (ins or []) if abs(abs(sum(a[k] * ia[k] for k in range(3))) - 1) < 1e-4 and _on_axis(o, a, io)), None)
            row = dict(body=body.name, at_mm=tuple(round(10 * (o[k] + a[k] * (s_lo + s_hi) / 2), 2) for k in range(3)),
                       len_mm=round(10 * (s_hi - s_lo), 2), insert=name, ok=False)
            rows.append(row)
            if name is None and not (bare and not ins):
                row['why'] = 'no insert ref on this axis'; continue
            if name is None and s_hi - s_lo > K['depth'] + 0.05:
                row['why'] = 'bare hole longer than a pocket: not an insert pocket'; continue
            # the open end: air on the axis just past it and in a ring around it (relief or face); a floor is material
            open_hi = not _inside(body, _P(o, a, u, v, s_hi + 0.02)) and _ring(body, o, a, u, v, s_hi + 0.02, K['r_old'] + 0.04, 8) <= 2
            open_lo = not _inside(body, _P(o, a, u, v, s_lo - 0.02)) and _ring(body, o, a, u, v, s_lo - 0.02, K['r_old'] + 0.04, 8) <= 2
            if open_hi == open_lo:
                if open_hi and name:                                     # through: the insert's nearer end is the top
                    io = next(io for n, io, ia in ins if n == name); t = sum((io[k] - o[k]) * a[k] for k in range(3))
                    open_hi = abs(s_hi - t) < abs(t - s_lo)
                elif open_hi and bare and abs(a[2]) < 0.1:               # radial set-screw pocket: open on the outside
                    rr = lambda s: sum((o[k] + a[k] * s) ** 2 for k in range(2)); open_hi = rr(s_hi) > rr(s_lo)
                else:
                    row['why'] = 'open end unclear (open hi %s lo %s)' % (open_hi, open_lo); continue
            sign = 1 if open_hi else -1
            s_open, s_far = (s_hi, s_lo) if sign > 0 else (s_lo, s_hi)
            row['open'] = 'hi' if sign > 0 else 'lo'
            if s_hi - s_lo < K['lead'] + 0.1:
                row['why'] = 'pocket %.2f mm: too short, or already converted (the lead-in is left)' % (10 * (s_hi - s_lo)); continue
            s_a = s_open - sign * K['lead']
            if abs(s_far - s_open) > K['depth']:
                s_far = s_open - sign * K['depth']; row['sleeve_to_mm'] = K['p']['depth']
            rw = K['r_old'] + 0.015
            n = max(3, int(abs(s_far - s_a) / 0.05))
            bad = [round(10 * (s_a + (s_far - s_a) * i / n), 2) for i in range(n + 1)
                   if _ring(body, o, a, u, v, s_a + (s_far - s_a) * i / n - sign * 0.005 * (i == n) + sign * 0.005 * (i == 0), rw) < 16]
            far_air = not _inside(body, _P(o, a, u, v, s_far - sign * 0.02))
            if bad and not (far_air and len(bad) == 1 and bad[0] == round(10 * s_far, 2)):
                row['why'] = 'cavity across the sleeve wall at %s mm' % bad[:4]; continue
            if far_air and _ring(body, o, a, u, v, s_far + sign * 0.01, rw) < 16:
                s = s_a
                while sign * (s_open - s) < sign * (s_open - s_far) and _ring(body, o, a, u, v, s - sign * 0.005, rw) == 16:
                    s -= sign * 0.005
                if abs(s - s_a) < 0.1:
                    row['why'] = 'through pocket with a slanted exit, < 1 mm of full wall'; continue
                row['trim_mm'] = round(10 * abs(s - s_far), 2); s_far = s
            row['ok'] = True; row['_geo'] = (body, o, a, s_a, s_far)
    return rows


def _join_or_cut(comp, tools, op, feature_name, tools_name):
    """tools: [(host body, temp body)] -> one base feature + one combine per host"""
    bf = comp.features.baseFeatures.add(); bf.name = tools_name
    bf.startEdit()
    for _, t in tools:
        comp.bRepBodies.add(t, bf)
    bf.finishEdit()
    hosts = {}
    for i, (body, _) in enumerate(tools):                   # base-feature bodies come back in insertion order
        hosts.setdefault(body.name, [body, []])[1].append(bf.bodies.item(i))
    for name, (body, bs) in hosts.items():
        coll = adsk.core.ObjectCollection.create()
        for b in bs:
            coll.add(b)
        ci = comp.features.combineFeatures.createInput(body if body.isValid else comp.bRepBodies.itemByName(name), coll)
        ci.operation = op; ci.isKeepToolBodies = False
        comp.features.combineFeatures.add(ci).name = feature_name
    return len(hosts)


def convert(comp_name, bare=False, again=False, kind=None):
    """old straight pockets -> lead-in + narrow hole: a sleeve (OD a hair over the old pocket, ID the new hole) from the
    lead-in floor to the pocket floor, joined at the end of the timeline."""
    K = _k(kind); po = _occ(comp_name)
    rows = plan(comp_name, bare, kind)
    good = [r for r in rows if r['ok']]
    comp = po.component if po else None
    out = lambda **kw: dict(part=comp_name, skipped=[(r.get('insert') or r.get('at_mm'), r['why']) for r in rows if not r['ok']], **kw)
    if not good:
        return out(sleeves=0)
    d = _d()
    assert d.designType == adsk.fusion.DesignTypes.ParametricDesignType
    if not again and any(comp.features.item(i).name == FEATURE for i in range(comp.features.count)):
        return out(sleeves=0, why='already converted; again=True for pockets the first run missed')
    vol0 = sum(b.volume for b in comp.bRepBodies if b.isSolid)
    TB = adsk.fusion.TemporaryBRepManager.get(); tools = []
    for r in good:
        body, o, a, s_a, s_far = r['_geo']
        t = _cyl(o, a, s_a, s_far, K['r_old'] + 0.0006)
        e = 1 if s_far > s_a else -1                         # the core pokes out of both ends: a flush core left a 0.1 plug
        TB.booleanOperation(t, _cyl(o, a, s_a - 0.01 * e, s_far + 0.01 * e, K['r_new']), adsk.fusion.BooleanTypes.DifferenceBooleanType)
        tools.append((body, t))
    p = K['p']
    joins = _join_or_cut(comp, tools, adsk.fusion.FeatureOperations.JoinFeatureOperation,
                         'insert pockets D%g + lead-in D%g x %g' % (p['hole_d'], p['lead_d'], p['lead_depth']), FEATURE)
    assert d.designType == adsk.fusion.DesignTypes.ParametricDesignType
    return out(sleeves=len(good), joins=joins, bodies=comp.bRepBodies.count,
               dvol_mm3=round((sum(b.volume for b in comp.bRepBodies if b.isSolid) - vol0) * 1000, 1))


def cut(comp_name, body_name, points, kind=None):
    """new pockets: points = [((x, y, z) on the face in mm, (ax, ay, az) out of the material)] in the component's space."""
    K = _k(kind); comp = _occ(comp_name).component; body = comp.bRepBodies.itemByName(body_name)
    TB = adsk.fusion.TemporaryBRepManager.get(); tools = []
    for q, ax in points:
        n = math.sqrt(sum(x * x for x in ax)); a = tuple(x / n for x in ax); o = tuple(x / 10 for x in q)
        t = _cyl(o, a, -K['relief'], 0.03, K['r_relief'])
        f = -K['relief']
        TB.booleanOperation(t, _cyl(o, a, f + 0.001, f - K['lead'], K['r_lead']), adsk.fusion.BooleanTypes.UnionBooleanType)
        TB.booleanOperation(t, _cyl(o, a, f - K['lead'] + 0.001, f - K['depth'], K['r_new']), adsk.fusion.BooleanTypes.UnionBooleanType)
        tools.append((body, t))
    p = K['p']
    _join_or_cut(comp, tools, adsk.fusion.FeatureOperations.CutFeatureOperation,
                 '%d %s pockets (relief D%g, lead-in D%g x %g, D%g x %g)' % (len(points), KIND, p['relief_d'], p['lead_d'], p['lead_depth'], p['hole_d'], p['depth']),
                 'insert pocket tools')
    return dict(part=comp_name, pockets=len(points))


def audit(comp_name, kind=None):
    """read-only: every new-size hole has a lead-in at one end and nothing solid on its axis."""
    K = _k(kind); comp = _occ(comp_name).component
    n = 0; bad = []
    for b in [b for b in comp.bRepBodies if b.isSolid]:
        for o, a, lo, hi in _faces(b, K['r_new']):
            n += 1; u, v = _frame(a)
            solid = [round(10 * (lo + (hi - lo) * i / 20), 2) for i in range(1, 20) if _inside(b, _P(o, a, u, v, lo + (hi - lo) * i / 20))]
            lead = [not _inside(b, _P(o, a, u, v, se + sg * 0.05)) and _ring(b, o, a, u, v, se + sg * 0.05, K['r_new'] + 0.02, 8) == 0
                    for sg, se in ((1, hi), (-1, lo))]
            if solid or not any(lead):
                bad.append(dict(at_mm=tuple(round(10 * (o[k] + a[k] * (lo + hi) / 2), 2) for k in range(3)), solid_on_axis=solid, lead_in=lead))
    old = [r['at_mm'] for r in plan(comp_name, kind=kind) if r.get('insert') and r['ok']]
    return dict(part=comp_name, pockets=n, bad=bad, old_pockets_left=old)
