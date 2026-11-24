"""Overhang gate, the deterministic part: no Fusion here. Pure Python (3.9+): the same text runs in the tests and on
the facts overhang_fusion.py reads.

The print frame of a part: "down" is the outward normal of its bed face (fusion/parts.json "face": "-z" -> [0, 0, -1],
"+y" -> [0, 1, 0]); the height of a point p is h = -p . down, the bed is the lowest h of the body.

Facts in (overhang_fusion.py, world mm):
  part   {"name", "down", "bed_h", "box", "faces": [face], "ridges": [ridge]}
  face   {"i", "type": "plane" | "cylinder" | "cone" | ..., "area", "concave": bool for cylinders and cones,
          "axis": unit axis for cylinders and cones, "d",
          plane:     "n" outward normal, "h": [lowest, highest], "loops": [edge polyline {"pts": [[x,y,z]...],
                     "anch": [bool per segment: the material goes on downward beyond the edge there]}] for a ceiling
          not plane: "tris": [[cx, cy, cz, nx, ny, nz, area_mm2 (, x0, y0, z0, x1, y1, z1 its box)]] its downward triangles}
  ridge  {"p0", "p1" ends, "faces": [i, j], "convex": bool}

What comes out: {"regions": [...], "findings": [...], "allowed": [...], "small": n} per part. A region
{"part", "code", "at" centroid, "box" [lo, hi], "area_mm2", "slope_deg" (the steepest; 0 = flat ceiling, 90 = wall),
"h" height above the bed, "face"} (+ "d" for a hole, "length_mm" for a ridge, "bridged_mm2" for a ceiling)."""
import math

AXES = {'-z': [0.0, 0.0, -1.0], '+z': [0.0, 0.0, 1.0], '-y': [0.0, -1.0, 0.0], '+y': [0.0, 1.0, 0.0],
        '-x': [-1.0, 0.0, 0.0], '+x': [1.0, 0.0, 0.0]}
DIRS = 12           # chord directions tried through a ceiling point (every 15 degrees, plus along the free edges)
MAX_PTS = 20000     # ceiling points per face; the grid gets coarser beyond this


def _dot(u, v): return u[0] * v[0] + u[1] * v[1] + u[2] * v[2]
def _sub(u, v): return [u[0] - v[0], u[1] - v[1], u[2] - v[2]]
def _add(u, v): return [u[0] + v[0], u[1] + v[1], u[2] + v[2]]
def _mul(u, s): return [u[0] * s, u[1] * s, u[2] * s]
def _cross(u, v): return [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
def _norm(u):
    n = math.sqrt(_dot(u, u)) or 1.0
    return [u[0] / n, u[1] / n, u[2] / n]


def val(rules, key):
    v = rules[key]
    return v['value'] if isinstance(v, dict) else v


def basis(down):
    """Two unit vectors across the bed (e1, e2) with e1 x e2 = -down (up)."""
    d = _norm(down)
    u = [1.0, 0.0, 0.0] if abs(d[0]) < 0.9 else [0.0, 1.0, 0.0]
    e1 = _norm(_cross(u, d))
    e2 = _cross(d, e1)
    return e1, e2


def slope(n, down):
    """Angle of a surface with outward normal n from the horizontal ceiling: 0 = flat ceiling, 90 = vertical wall,
    over 90 = it faces up. The gate's limit is measured from the vertical: overhang when slope < 90 - (90 - limit) = limit."""
    c = max(-1.0, min(1.0, _dot(_norm(n), _norm(down))))
    return math.degrees(math.acos(c))


def bed_faces(parts_cfg_entry):
    """(face, problem) from a fusion/parts.json part: the bed face of its 3mf entries. problem is None, 'no_bed_face'
    or 'bed_face_conflict'."""
    faces = sorted({t.get('face') for t in parts_cfg_entry.get('3mf') or [] if t.get('face')})
    if not faces:
        return None, 'no_bed_face'
    if len(faces) > 1:
        return faces[0], 'bed_face_conflict'
    if faces[0] not in AXES:
        return None, 'no_bed_face'
    return faces[0], None


# ---------------------------------------------------------------- heat-set pockets (from the heat-set gate's check)
def in_pocket(q, pockets, margin=0.15):
    """The pocket whose space holds point q (on its axis between mouth and floor, within its widest radius)."""
    for p in pockets:
        t = _dot(_sub(q, p['base']), p['a'])
        depth = sum(s.get('len') or 0 for s in p.get('profile') or [])       # the whole stack: relief, lead-in, hole
        lo, hi = sorted((p['t_mouth'], p['t_mouth'] + p['sign'] * depth if depth else p['t_floor']))
        if t < lo - margin or t > hi + margin:
            continue
        r_max = max([s['d'] for s in p.get('profile') or [] if s.get('d')] + [p.get('d_max', 0)]) / 2
        w = _sub(_sub(q, p['base']), _mul(p['a'], t))
        if math.sqrt(_dot(w, w)) <= r_max + margin:
            return p
    return None


# ---------------------------------------------------------------- a flat ceiling: which part of it is a bridge
def _segments(loops, e1, e2):
    segs = []
    for lp in loops:
        pts = [(_dot(p, e1), _dot(p, e2)) for p in lp['pts']]
        anch = lp.get('anch') or [False] * (len(pts) - 1)
        for i in range(len(pts) - 1):
            (x0, y0), (x1, y1) = pts[i], pts[i + 1]
            if abs(x1 - x0) + abs(y1 - y0) < 1e-9:
                continue
            segs.append((x0, y0, x1, y1, bool(anch[i])))
    return segs


def _pip(x, y, segs):
    c = False
    for x0, y0, x1, y1, _ in segs:
        if (y0 > y) != (y1 > y):
            xi = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
            if xi > x:
                c = not c
    return c


def _hits(x, y, ux, uy, segs):
    """(t_forward, anchored), (t_backward, anchored): the nearest crossings of the line through (x, y) along u."""
    fw, bw = (None, False), (None, False)
    for x0, y0, x1, y1, an in segs:
        dx, dy = x1 - x0, y1 - y0
        den = ux * dy - uy * dx
        if abs(den) < 1e-12:
            continue
        wx, wy = x0 - x, y0 - y
        t = (wx * dy - wy * dx) / den
        s = (wx * uy - wy * ux) / den
        if s < -1e-9 or s > 1 + 1e-9:
            continue
        if t > 1e-9 and (fw[0] is None or t < fw[0]):
            fw = (t, an)
        elif t < -1e-9 and (bw[0] is None or -t < bw[0]):
            bw = (-t, an)
    return fw, bw


def _seg_dist(x, y, s):
    x0, y0, x1, y1 = s[0], s[1], s[2], s[3]
    dx, dy = x1 - x0, y1 - y0
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / L2))
    return math.hypot(x - x0 - t * dx, y - y0 - t * dy)


def ceiling(loops, down, bridge_max, grid, ledge=0.0):
    """Sample a flat ceiling on a grid. A point is bridged when a straight chord through it ends on both sides on an
    anchored edge (the material goes on downward there) and is no longer than bridge_max; a point within `ledge` of an
    anchored edge is a ledge the wall line still sits on. Returns {"n", "bridged", "ledge", "long": points held on both
    sides by a longer chord, "free": [(x, y, z)] the points that are not bridged}."""
    e1, e2 = basis(down)
    segs = _segments(loops, e1, e2)
    if not segs:
        return {'n': 0, 'bridged': 0, 'ledge': 0, 'long': 0, 'free': [], 'step': grid, 'open_mm': 0.0}
    xs = [s[0] for s in segs] + [s[2] for s in segs]; ys = [s[1] for s in segs] + [s[3] for s in segs]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    step = max(grid, math.sqrt((x1 - x0) * (y1 - y0) / MAX_PTS) if (x1 - x0) * (y1 - y0) > 0 else grid)
    h = -_dot(loops[0]['pts'][0], down)
    # bins of the segments, cell = bridge_max: a chord of a bridged point only meets segments within bridge_max of it
    cell = max(bridge_max, step)
    bins = {}
    for s in segs:
        for i in range(int(math.floor((min(s[0], s[2]) - x0) / cell)), int(math.floor((max(s[0], s[2]) - x0) / cell)) + 1):
            for j in range(int(math.floor((min(s[1], s[3]) - y0) / cell)), int(math.floor((max(s[1], s[3]) - y0) / cell)) + 1):
                bins.setdefault((i, j), []).append(s)
    dirs = [(math.cos(k * math.pi / DIRS), math.sin(k * math.pi / DIRS)) for k in range(DIRS)]
    # and along every free edge: a bridge often runs parallel to the edge it leaves open (a half-disc ceiling)
    for x0_, y0_, x1_, y1_, an in segs:
        if an:
            continue
        L = math.hypot(x1_ - x0_, y1_ - y0_)
        u = ((x1_ - x0_) / L, (y1_ - y0_) / L)
        if all(abs(u[0] * v[1] - u[1] * v[0]) > 0.01 for v in dirs):
            dirs.append(u)
        if len(dirs) >= DIRS + 24:
            break
    n = bridged = held = on_ledge = 0
    free = []
    y = y0 + step / 2
    while y < y1:
        x = x0 + step / 2
        while x < x1:
            if _pip(x, y, segs):
                n += 1
                ci, cj = int(math.floor((x - x0) / cell)), int(math.floor((y - y0) / cell))
                near, seen = [], set()
                for i in range(ci - 1, ci + 2):
                    for j in range(cj - 1, cj + 2):
                        for s in bins.get((i, j), ()):
                            if id(s) not in seen:
                                seen.add(id(s)); near.append(s)
                ok = False
                for ux, uy in dirs:
                    fw, bw = _hits(x, y, ux, uy, near)
                    if fw[0] is not None and bw[0] is not None and fw[1] and bw[1] and fw[0] + bw[0] <= bridge_max + 1e-6:
                        ok = True; break
                if ok:
                    bridged += 1
                elif ledge and any(s[4] and _seg_dist(x, y, s) <= ledge for s in near):
                    on_ledge += 1
                else:
                    long_ = False
                    for ux, uy in dirs:
                        fw, bw = _hits(x, y, ux, uy, segs)
                        if fw[0] is not None and bw[0] is not None and fw[1] and bw[1]:
                            long_ = True; break
                    held += long_
                    free.append((_add(_add(_mul(e1, x), _mul(e2, y)), _mul(down, -h)), long_))
            x += step
        y += step
    open_mm = sum(math.hypot(s[2] - s[0], s[3] - s[1]) for s in segs if not s[4])
    return {'n': n, 'bridged': bridged, 'ledge': on_ledge, 'long': held, 'free': free, 'step': step, 'open_mm': open_mm}


# ---------------------------------------------------------------- one part
def _region(part, code, pts, area, slope_deg, face=None, **kw):
    lo = [min(p[i] for p in pts) for i in range(3)]; hi = [max(p[i] for p in pts) for i in range(3)]
    at = [sum(p[i] for p in pts) / len(pts) for i in range(3)]
    r = {'part': part['name'], 'code': code, 'at': [round(x, 2) for x in at], 'box': [[round(x, 2) for x in lo], [round(x, 2) for x in hi]],
         'area_mm2': round(area, 2), 'slope_deg': round(slope_deg, 1), 'h': round(-_dot(at, part['down']) - part['bed_h'], 2)}
    if face is not None:
        r['face'] = face
    r.update(kw)
    return r


def judge_part(part, rules, pockets=()):
    """Regions of one part (see the module doc). pockets: the heat-set gate's pockets of this part."""
    lim = val(rules, 'limit_deg'); tol = rules['limit_deg'].get('tolerance', 0.5) if isinstance(rules['limit_deg'], dict) else 0.5
    flat = val(rules, 'bridge_flat_deg'); bed = val(rules, 'bed_mm'); bmax = val(rules, 'bridge_max_mm'); grid = val(rules, 'grid_mm')
    down = _norm(part['down']); bed_h = part['bed_h']
    out, small, info = [], 0, []
    over = set()
    for f in part['faces']:
        if f['type'] == 'plane':
            s = slope(f['n'], down)
            if s >= lim - tol:
                continue
            if f['h'][1] - bed_h <= bed:                      # on the bed, or within the first layer
                continue
            if s <= flat and f.get('annulus') and f['h'][1] - bed_h > bed and f['annulus']['d_in'] <= val(rules, 'screw_hole_max_d_mm') + 1e-6 \
                    and f['annulus']['d_out'] <= val(rules, 'screw_seat_max_d_mm') + 1e-6:
                # a flat ring round a hole: the seat of a screw head (or washer); support there is allowed, the head closes it
                a = f['annulus']
                out.append(_region(part, 'screw_seat', [p for lp in f['loops'] for p in lp['pts']], f['area'], s, f['i'],
                                   d_in=a['d_in'], d_out=a['d_out']))
                over.add(f['i'])
                continue
            if s <= flat and f.get('loops'):
                per = sum(math.sqrt(_dot(_sub(lp['pts'][k + 1], lp['pts'][k]), _sub(lp['pts'][k + 1], lp['pts'][k])))
                          for lp in f['loops'] for k in range(len(lp['pts']) - 1))
                if per and 2 * f['area'] / per < val(rules, 'sliver_mm'):        # two faces a hair apart: not printable at all
                    info.append(_region(part, 'sliver', [p for lp in f['loops'] for p in lp['pts']], f['area'], s, f['i'],
                                        width_mm=round(2 * f['area'] / per, 3)))
                    continue
                c = ceiling(f['loops'], down, bmax, grid, val(rules, 'ledge_mm'))
                if c['n'] and c['bridged']:
                    pts_b = [p for lp in f['loops'] for p in lp['pts']]
                    pkb = in_pocket(pts_b[0], pockets) if pts_b else None
                    pkb = pkb if pkb and all(in_pocket(p, pockets) for p in pts_b) else None     # a heat-set pocket's own ceiling (its blind end)
                    info.append(_region(part, 'bridge', pts_b, f['area'] * c['bridged'] / c['n'], s, f['i'], open_mm=round(c['open_mm'], 2),
                                        **({'pocket': pockets.index(pkb)} if pkb else {})))
                if not c['n'] or not c['free']:
                    continue
                groups = {}                                     # the free points by the pocket they are in (None: none)
                for p, long_ in c['free']:
                    pk = in_pocket(p, pockets)
                    groups.setdefault(None if pk is None else pockets.index(pk), []).append((p, long_))
                for pi, fr in sorted(groups.items(), key=lambda kv: -1 if kv[0] is None else kv[0]):
                    area = f['area'] * len(fr) / c['n']
                    pts = [p for p, _ in fr]
                    code = 'heatset_ceiling' if pi is not None else ('long_bridge' if sum(l for _, l in fr) >= len(fr) / 2 else 'overhang')
                    if area < val(rules, 'min_area_mm2'):
                        small += 1; info.append(_region(part, 'small', pts, area, s, f['i'])); continue
                    kw = {'pocket': pi} if pi is not None else {}
                    out.append(_region(part, code, pts, area, s, f['i'], bridged_mm2=round(f['area'] * c['bridged'] / c['n'], 2), **kw))
                    over.add(f['i'])
                continue
            pts = [p for lp in f.get('loops') or [] for p in lp['pts']] or [f['c']]
            pk = in_pocket(pts[0], pockets)
            pk = pk if pk and all(in_pocket(p, pockets) for p in pts) else None
            if f['area'] < val(rules, 'min_area_mm2'):
                small += 1; info.append(_region(part, 'small', pts, f['area'], s, f['i'])); continue
            out.append(_region(part, 'heatset_ceiling' if pk else 'overhang', pts, f['area'], s, f['i'], **({'pocket': pockets.index(pk)} if pk else {})))
            over.add(f['i'])
            continue
        tris = [t for t in f.get('tris') or [] if slope(t[3:6], down) < lim - tol and -_dot(t[:3], down) - bed_h > bed]
        if not tris:
            continue
        area = sum(t[6] for t in tris)
        pts = [t[:3] for t in tris]
        ext = [q for t in tris if len(t) >= 13 for q in (t[7:10], t[10:13])]       # the triangles' own boxes: a long facet's ends
        smin = min(slope(t[3:6], down) for t in tris)
        pk = in_pocket(pts[0], pockets)
        if pk and all(in_pocket(p, pockets) for p in pts):
            code = 'heatset_ceiling'
        elif f['type'] == 'cylinder' and f.get('concave') and abs(_dot(f['axis'], down)) < math.sin(math.radians(lim)):
            code = 'round_hole'
        else:
            code = 'overhang'
        if area < val(rules, 'min_area_mm2'):
            small += 1; info.append(_region(part, 'small', pts, area, smin, f['i'])); continue
        kw = {'d': round(f['d'], 2)} if f.get('d') else {}
        if code == 'round_hole':
            kw['axis'] = [round(x, 4) for x in f['axis']]
        if code == 'heatset_ceiling':
            kw['pocket'] = pockets.index(pk)
        out.append(_region(part, code, pts, area, smin, f['i'], **kw))
        if ext:
            r_ = out[-1]
            r_['box'] = [[round(min([r_['box'][0][i]] + [q[i] for q in ext]), 2) for i in range(3)],
                         [round(max([r_['box'][1][i]] + [q[i] for q in ext]), 2) for i in range(3)]]
        over.add(f['i'])
    for rg in part.get('ridges') or []:
        if not rg.get('convex') or any(i in over for i in rg['faces']):
            continue
        t = _sub(rg['p1'], rg['p0']); L = math.sqrt(_dot(t, t))
        if L < val(rules, 'ridge_min_mm'):
            continue
        rise = math.degrees(math.asin(min(1.0, abs(_dot(t, down)) / L)))
        if rise >= lim - tol:
            continue
        if rg.get('n') and all(slope(n, down) >= lim - tol for n in rg['n']):
            continue                     # the hip of two faces that each pass (two 45 deg chamfers meeting at a corner): prints clean
        if max(-_dot(rg['p0'], down), -_dot(rg['p1'], down)) - bed_h <= bed:
            continue
        out.append(_region(part, 'shallow_ridge', [rg['p0'], rg['p1']], 0.0, rise, rg['faces'], length_mm=round(L, 2)))
    by_i = {f['i']: f for f in part['faces']}
    for r in out:                                         # the surface under each region: what the redesign works on
        f = by_i.get(r.get('face')) if not isinstance(r.get('face'), list) else None
        if f:
            r['surface'] = f['type']
            if f.get('concave') is not None:
                r['concave'] = f['concave']
    out = merge_holes(out)
    return {'regions': out, 'small': small, 'info': info}


def merge_holes(regions, axis_mm=0.05):
    """The faces of one round hole (a cylinder split in two) are one region."""
    out = []
    for r in regions:
        m = None
        if r['code'] == 'round_hole' and r.get('axis'):
            m = next((x for x in out if x['code'] == r['code'] and x.get('axis') and abs(abs(_dot(x['axis'], r['axis'])) - 1) < 1e-4
                      and abs(x.get('d', 0) - r.get('d', 0)) < 0.05 and _axis_dist(x, r) < max(axis_mm, x.get('d', 0) / 2)), None)
        elif r['code'] == 'heatset_ceiling' and r.get('pocket') is not None:
            m = next((x for x in out if x['code'] == r['code'] and x.get('pocket') == r['pocket']), None)
        if m:
            a1, a2 = m['area_mm2'], r['area_mm2']
            m['at'] = [round((m['at'][i] * a1 + r['at'][i] * a2) / (a1 + a2), 2) for i in range(3)]
            m['box'] = [[min(m['box'][0][i], r['box'][0][i]) for i in range(3)], [max(m['box'][1][i], r['box'][1][i]) for i in range(3)]]
            m['area_mm2'] = round(a1 + a2, 2); m['slope_deg'] = min(m['slope_deg'], r['slope_deg'])
            m['face'] = (m['face'] if isinstance(m['face'], list) else [m['face']]) + [r['face']]
            continue
        out.append(r)
    return out


def _axis_dist(x, r):
    w = _sub(r['at'], x['at'])
    w = _sub(w, _mul(x['axis'], _dot(w, x['axis'])))
    return math.sqrt(_dot(w, w))




def finding(r, rules):
    """A region as a finding line (what was measured, what is expected, how to fix it)."""
    lim = val(rules, 'limit_deg')
    what = {'overhang': '%.1f mm2 facing down at %.0f deg from the horizontal (%.0f from the vertical), %.1f mm above the bed' % (
                r['area_mm2'], r['slope_deg'], 90 - r['slope_deg'], r['h']),
            'round_hole': 'round hole D%.2f, axis horizontal, %.1f mm2 of its top steeper than %g deg' % (r.get('d', 0), r['area_mm2'], lim),
            'long_bridge': '%.1f mm2 of flat ceiling held on both sides by a span longer than %g mm, %.1f mm above the bed' % (
                r['area_mm2'], val(rules, 'bridge_max_mm'), r['h']),
            'shallow_ridge': 'ridge %.1f mm long rising %.0f deg, between two downward faces' % (r.get('length_mm', 0), r['slope_deg']),
            'heatset_ceiling': 'heat-set pocket ceiling %.1f mm2' % r['area_mm2'],
            'screw_seat': 'screw head seat (ring round a hole) %.1f mm2' % r['area_mm2'],
            'slicer_bridge': '%.1f mm2 flat ceiling %.1f mm above the bed: the gate takes it for a bridge, Bambu Studio puts %.0f mm2 of '
                             'support contact under it' % (r['area_mm2'], r['h'], r.get('contact_mm2', 0))}.get(r['code'], r['code'])
    return dict(r, measured=what, expected='no downward face steeper than %g deg from the vertical, bridges up to %g mm' % (
        lim, val(rules, 'bridge_max_mm')), fix=rules['codes'][r['code']], redesign=redesign(r, rules))


def _xyz(p):
    return '(%s)' % ', '.join('%.1f' % x for x in p)


def redesign(r, rules):
    """A concrete redesign for one region: what to change, where. {"kind", "text"}; kind is one of chamfer_45, steepen,
    bottom_chamfer, teardrop, split_bridge, roof_45, level_ridge, drop_to_bed, none."""
    lim, bmax, code, h = val(rules, 'limit_deg'), val(rules, 'bridge_max_mm'), r['code'], r.get('h', 0)
    surf, flat = r.get('surface'), r.get('slope_deg', 0) <= val(rules, 'bridge_flat_deg')
    if code == 'round_hole':
        return {'kind': 'teardrop', 'text': 'teardrop top on the D%.2f hole at %s: a %g deg roof over it, its flat top no wider than %g mm' % (
            r.get('d', 0), _xyz(r['at']), lim, bmax)}
    if code == 'long_bridge':
        return {'kind': 'split_bridge', 'text': 'the ceiling at %s (h %.1f) spans more than %g mm: a %g deg roof over it, or ribs down to the '
                'floor so no span is over %g mm, or narrow the opening' % (_xyz(r['at']), h, bmax, lim, bmax)}
    if code == 'slicer_bridge':
        return {'kind': 'roof_45', 'text': 'the ceiling at %s (h %.1f, open edge %.1f mm): a %g deg roof up into it, or a wall down under its open '
                'side so it is held all round, or lay the part on another side' % (_xyz(r['at']), h, r.get('open_mm', 0), lim)}
    if code == 'shallow_ridge':
        return {'kind': 'level_ridge', 'text': 'the edge at %s rises %.0f deg: give the two faces the same tilt so the edge runs level, or a %g deg '
                'chamfer along it' % (_xyz(r['at']), r['slope_deg'], lim)}
    if code != 'overhang':
        return {'kind': 'none', 'text': ''}
    if surf in ('cylinder', 'torus', 'sphere', 'nurbs') and not r.get('concave') and h <= 2.0:
        return {'kind': 'bottom_chamfer', 'text': 'the rounded edge at %s, %.1f mm above the bed: a %g deg chamfer instead of the round' % (
            _xyz(r['at']), h, lim)}
    if flat and h <= 2.0:
        return {'kind': 'drop_to_bed', 'text': 'the ceiling at %s is only %.1f mm above the bed: take the face down to the bed, or a %g deg '
                'chamfer under it' % (_xyz(r['at']), h, lim)}
    if flat:
        return {'kind': 'chamfer_45', 'text': 'a %g deg chamfer or a rib from below under the ceiling at %s (h %.1f, %.1f mm2)' % (
            lim, _xyz(r['at']), h, r['area_mm2'])}
    return {'kind': 'steepen', 'text': 'the %s at %s is %.0f deg from the vertical: make it %g deg (a %g deg cone or chamfer)' % (
        'face' if surf == 'plane' else surf or 'surface', _xyz(r['at']), 90 - r['slope_deg'], lim, lim)}


def apply_exceptions(res, exceptions, near=2.0):
    """Move each finding a project names in gate/settings.json "overhang": {"exceptions": [{"part", "at": [x, y, z] or
    "face": index, or "whole_part": true, "code" (optional), "reason"}]} from the findings to res["excepted"] (with its
    reason). "whole_part" covers every finding of the part (the owner calls the part itself an exception). An exception
    with no written reason, or one that matches no finding any more, is a finding itself."""
    res.setdefault('excepted', [])
    keep = []
    used = [False] * len(exceptions or [])
    def hit(e, f):
        if e.get('part') != f.get('part') or (e.get('code') and e['code'] != f.get('code')):
            return False
        if e.get('whole_part') is True:
            return True
        if e.get('face') is not None:
            fc = f.get('face')
            return e['face'] == fc or (isinstance(fc, list) and e['face'] in fc)
        if e.get('at') and f.get('box'):
            lo, hi = f['box']
            return all(lo[i] - near <= e['at'][i] <= hi[i] + near for i in range(3))
        return False
    for f in res['findings']:
        k = next((k for k, e in enumerate(exceptions or []) if hit(e, f)), None)
        if k is None:
            keep.append(f); continue
        used[k] = True
        e = exceptions[k]
        if not (e.get('reason') or '').strip():
            keep.append(dict(f, code='exception_without_reason', measured='an exception in gate/settings.json covers this %s but gives no reason' % f['code'],
                             fix='write why this overhang is really needed ("reason"), or redesign it: %s' % f.get('redesign', {}).get('text', '')))
            continue
        res['excepted'].append(dict(f, exception=e['reason'].strip()))
    for k, e in enumerate(exceptions or []):
        if not used[k]:
            keep.append({'part': e.get('part'), 'code': 'exception_unused', 'at': e.get('at'), 'face': e.get('face'),
                         'measured': 'the exception in gate/settings.json matches no finding (fixed or moved?)', 'expected': 'only live exceptions',
                         'fix': 'remove it from gate/settings.json, or point it at the place again'})
    res['findings'] = keep
    return res


def check(parts, rules, pockets_by_part=None):
    """Judge every part. Returns {"findings", "allowed", "small": {part: n}, "regions": {part: [...]}}."""
    findings, allowed, small, regions, info = [], [], {}, {}, {}
    for p in parts:
        if p.get('problem'):
            findings.append({'part': p['name'], 'code': p['problem'], 'measured': p.get('measured', p['problem']),
                             'expected': 'one bed face in fusion/parts.json', 'fix': rules['codes'][p['problem']]})
            if p['problem'] == 'no_bed_face':
                continue
        r = judge_part(p, rules, (pockets_by_part or {}).get(p['name'], ()))
        regions[p['name']] = r['regions']; small[p['name']] = r['small']; info[p['name']] = r['info']
        for x in r['regions']:
            (allowed if x['code'] in rules.get('allowed', ()) else findings).append(finding(x, rules))
    return apply_exceptions({'findings': findings, 'allowed': allowed, 'small': small, 'regions': regions, 'info': info},
                            rules.get('exceptions') or [])
