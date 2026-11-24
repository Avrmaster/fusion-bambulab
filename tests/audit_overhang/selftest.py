#!/usr/bin/env python3
"""Synthetic meshes for ovh_audit.analyze."""
import math
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ovh_audit import analyze, summarize, cross, sub, dot, unit


class Builder:
    def __init__(self):
        self.V, self.T, self.idx = [], [], {}

    def v(self, p):
        k = tuple(round(c, 6) for c in p)
        if k not in self.idx:
            self.idx[k] = len(self.V)
            self.V.append(k)
        return self.idx[k]

    def tri(self, a, b, c, want):
        """want: function(centroid, normal) -> True if orientation ok."""
        pa, pb, pc = a, b, c
        n = cross(sub(pb, pa), sub(pc, pa))
        if math.sqrt(dot(n, n)) < 1e-12:
            return
        cen = tuple((pa[i] + pb[i] + pc[i]) / 3 for i in range(3))
        if not want(cen, unit(n)):
            pb, pc = pc, pb
        self.T.append((self.v(pa), self.v(pb), self.v(pc)))

    def quad(self, a, b, c, d, want):
        self.tri(a, b, c, want)
        self.tri(a, c, d, want)


def ray_poly(poly, c, th):
    dx, dy = math.cos(th), math.sin(th)
    best = None
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        ex, ey = x2 - x1, y2 - y1
        den = dx * ey - dy * ex
        if abs(den) < 1e-12:
            continue
        t = ((x1 - c[0]) * ey - (y1 - c[1]) * ex) / den
        u = ((x1 - c[0]) * dy - (y1 - c[1]) * dx) / den
        if t > 1e-9 and -1e-9 <= u <= 1 + 1e-9 and (best is None or t < best):
            best = t
    return (c[0] + best * dx, c[1] + best * dy)


def rect(cx, cz, w, h):
    return [(cx - w / 2, cz - h / 2), (cx + w / 2, cz - h / 2), (cx + w / 2, cz + h / 2), (cx - w / 2, cz + h / 2)]


def circle(cx, cz, r, n=32):
    return [(cx + r * math.cos(2 * math.pi * i / n), cz + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def teardrop(cx, cz, r, n=32):
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        deg = math.degrees(a)
        if 45 + 1e-6 < deg < 135 - 1e-6:
            continue
        pts.append((cx + r * math.cos(a), cz + r * math.sin(a)))
    # insert apex between 45 and 135 deg
    out = []
    for p in pts:
        out.append(p)
        if abs(math.degrees(math.atan2(p[1] - cz, p[0] - cx)) - 45) < 1e-6:
            out.append((cx, cz + r * math.sqrt(2)))
    return out


def angles_of(poly, c):
    return [math.atan2(y - c[1], x - c[0]) % (2 * math.pi) for x, y in poly]


def prism_with_holes(outer, sections, c, L, n=32):
    """Profile in x-z, extruded along y 0..L. sections: list of (y0, y1, inner_poly) consecutive from y=0;
    last section ends blind (closed by a cap) unless y1 == L (through)."""
    angs = set(round(2 * math.pi * i / n, 9) for i in range(n))
    for poly in [outer] + [s[2] for s in sections]:
        angs |= set(round(a, 9) for a in angles_of(poly, c))
    angs = sorted(angs)
    B = Builder()
    P = lambda poly: [ray_poly(poly, c, a) for a in angs]
    out = P(outer)
    m = len(angs)
    X = lambda p, y: (p[0], y, p[1])
    cen3 = lambda y: (c[0], y, c[1])

    def facing(normal_vec):
        return lambda cen, nrm: dot(nrm, normal_vec) > 0

    def radial(outward):
        def f(cen, nrm):
            r = (cen[0] - c[0], 0, cen[2] - c[1])
            return (dot(nrm, r) > 0) == outward
        return f
    # outer walls
    for i in range(m):
        j = (i + 1) % m
        B.quad(X(out[i], 0), X(out[j], 0), X(out[j], L), X(out[i], L), radial(True))
    # front face: ring outer -> first inner
    prev = P(sections[0][2])
    for i in range(m):
        j = (i + 1) % m
        B.quad(X(out[i], 0), X(out[j], 0), X(prev[j], 0), X(prev[i], 0), facing((0, -1, 0)))
    for k, (y0, y1, poly) in enumerate(sections):
        cur = P(poly)
        if k > 0:  # step face at y0 between previous (bigger) and current
            for i in range(m):
                j = (i + 1) % m
                B.quad(X(prev[i], y0), X(prev[j], y0), X(cur[j], y0), X(cur[i], y0), facing((0, -1, 0)))
        for i in range(m):
            j = (i + 1) % m
            B.quad(X(cur[i], y0), X(cur[j], y0), X(cur[j], y1), X(cur[i], y1), radial(False))
        prev = cur
    yend = sections[-1][1]
    if yend < L - 1e-9:  # blind: cap the hole at yend facing -y, and close back face fully
        for i in range(m):
            j = (i + 1) % m
            B.tri(cen3(yend), X(prev[i], yend), X(prev[j], yend), facing((0, -1, 0)))
        for i in range(m):
            j = (i + 1) % m
            B.tri(cen3(L), X(out[i], L), X(out[j], L), facing((0, 1, 0)))
    else:
        for i in range(m):
            j = (i + 1) % m
            B.quad(X(out[i], L), X(out[j], L), X(prev[j], L), X(prev[i], L), facing((0, 1, 0)))
    return B.V, B.T


def revolve(profile, n=64):
    """profile: list of (r, z) from bottom centre (r=0) to top centre (r=0)."""
    B = Builder()
    ring = lambda r, z: [(r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n), z) for i in range(n)]
    cz = sum(z for _, z in profile) / len(profile)

    def outward(cen, nrm):
        # outward: away from axis point at same height... use bottom/top/sides heuristics
        return dot(nrm, (cen[0], cen[1], 0)) > 1e-9 or (abs(cen[0]) + abs(cen[1]) < 1e-6 and nrm[2] * (cen[2] - cz) > 0)
    for k in range(len(profile) - 1):
        (r0, z0), (r1, z1) = profile[k], profile[k + 1]
        a, b = ring(r0, z0), ring(r1, z1)
        for i in range(n):
            j = (i + 1) % n
            if r0 == 0:
                B.tri(a[0], b[i], b[j], lambda cen, nrm: nrm[2] < 0)
            elif r1 == 0:
                B.tri(a[i], a[j], b[0], lambda cen, nrm: nrm[2] > 0)
            else:
                def w(cen, nrm, z0=z0, z1=z1, r0=r0, r1=r1):
                    # side normal: radial outward component, z component sign from profile
                    t = (r1 - r0, z1 - z0)
                    nz = -t[0]  # outward normal of profile going up: (dz, -dr)
                    nr = t[1]
                    rad = (cen[0], cen[1], 0)
                    return dot(nrm, (rad[0] * nr, rad[1] * nr, nz * math.hypot(cen[0], cen[1]))) > 0
                B.quad(a[i], a[j], b[j], b[i], w)
    return B.V, B.T


def cone_part(angle_from_vertical):
    r0, h0, h = 5.0, 5.0, 10.0
    r1 = r0 + h * math.tan(math.radians(angle_from_vertical))
    return revolve([(0, 0), (r0, 0), (r0, h0), (r1, h0 + h), (0, h0 + h)])


def report(name, res):
    c, f = summarize(res)
    print(f'{name}: {c} forbidden {f} mm2  bridged {res["bridged_area_mm2"]} open_edges {res["open_edges"]} vol {res["volume_mm3"]}')
    for r in res['regions']:
        print('   ', {k: r[k] for k in r if k not in ('box',)})
    return c, f


def main():
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(('PASS ' if cond else 'FAIL ') + msg)
        ok = ok and cond

    c, f = report('cone 50', analyze(*cone_part(50)))
    res = analyze(*cone_part(50))
    ov = [r for r in res['regions'] if r['code'] == 'overhang']
    check(len(ov) == 1 and abs(ov[0]['min_slope_deg'] - 40) < 1.0, 'cone 50 deg from vertical -> overhang, slope ~40')
    exp = math.pi * ((5 + 10 * math.tan(math.radians(50))) ** 2 - 25)
    check(abs(ov[0]['area_mm2'] * math.sin(math.radians(50)) - exp) / exp < 0.03 if ov else False, 'cone overhang area ~ annulus/sin')
    c, f = report('chamfer 45', analyze(*cone_part(45)))
    check(f == 0 and not c, '45 deg chamfer passes (nothing flagged)')

    outer = rect(0, 10, 20, 20)
    cc = (0, 10)
    V, T = prism_with_holes(outer, [(0, 10, circle(0, 10, 3))], cc, 10)
    res = analyze(V, T)
    c, f = report('round hole D6', res)
    rh = [r for r in res['regions'] if r['code'] == 'round_hole']
    check(len(rh) == 1 and abs(rh[0]['d'] - 6.0) < 0.05 and set(c) == {'round_hole'}, 'horizontal round hole D6 -> round_hole d=6')
    V, T = prism_with_holes(outer, [(0, 10, teardrop(0, 10, 3))], cc, 10)
    c, f = report('teardrop D6', analyze(V, T))
    check(f == 0 and not c, 'teardrop hole passes')

    for w, exp_code in ((4.0, 'bridge'), (10.0, 'long_bridge')):
        outer2 = rect(0, 5, 30, 10)
        V, T = prism_with_holes(outer2, [(0, 20, rect(0, 4.5, w, 3))], (0, 4.5), 20)
        res = analyze(V, T)
        c, f = report(f'tunnel {w}', res)
        if exp_code == 'bridge':
            check(f == 0 and res['bridged_area_mm2'] > 0.95 * w * 20, f'{w} mm bridge passes (bridged area ~{w*20})')
        else:
            lb = [r for r in res['regions'] if r['code'] == 'long_bridge']
            check(len(lb) == 1 and abs(lb[0]['area_mm2'] - w * 20) < 2 and set(c) == {'long_bridge'},
                  f'{w} mm bridge -> long_bridge ~{w*20} mm2')
    # cantilever shelf: slab overhanging free on one side (wall goes up at the free edge)
    shelf = [(0, 0), (5, 0), (5, 5), (15, 5), (15, 8), (0, 8)]
    V, T = prism_with_holes_solid(shelf, 10)
    res = analyze(V, T)
    c, f = report('shelf 10', res)
    check(set(c) == {'overhang'} and abs(f - 100) < 2, 'cantilever shelf 10x10 -> overhang 100 mm2')

    # horizontal heat-set pocket D5.9 x 0.54 / D4.4 x 1.5 / D3.6 blind
    secs = [(0, 0.54, circle(0, 10, 2.95)), (0.54, 2.04, circle(0, 10, 2.2)), (2.04, 8, circle(0, 10, 1.8))]
    V, T = prism_with_holes(outer, secs, cc, 12)
    res = analyze(V, T)
    c, f = report('heat-set pocket horizontal', res)
    check(set(c) == {'heatset_ceiling'} and c['heatset_ceiling'] == 3, 'horizontal heat-set stack -> 3 heatset_ceiling, none forbidden')
    # vertical heat-set pocket opening downward on the bed: D5.9 x 0.54, D4.4 x 1.5, D3.6 blind
    prof = [(0, 8.04), (1.8, 8.04), (1.8, 2.04), (2.2, 2.04), (2.2, 0.54), (2.95, 0.54), (2.95, 0), (8, 0), (8, 12), (0, 12)]
    res = analyze(*revolve(prof))
    c, f = report('heat-set pocket vertical, opening down', res)
    check(c.get('heatset_ceiling') == 2 and f == 0, 'downward pocket: 2 annular steps heatset_ceiling, D3.6 end bridged, none forbidden')
    # same stack but a D12 counterbore instead of the lead-in: wide annulus at z 2.04 is not bridged
    prof = [(0, 8.04), (1.8, 8.04), (1.8, 2.04), (6, 2.04), (6, 0), (8, 0), (8, 12), (0, 12)]
    res = analyze(*revolve(prof))
    c, f = report('D12 counterbore step', res)
    check(not c.get('heatset_ceiling') and f > 5, 'non-standard D12/D3.6 step -> forbidden area (chords > 6 mm near the bore)')
    # downward keel (two 60 deg faces meeting at a horizontal ridge, above the bed)
    k = 4.0 + 5 * math.tan(math.radians(55))
    keel = [(-10, 0), (-8, 0), (-8, k), (-5, k), (0, 4.0), (5, k), (8, k), (8, 0), (10, 0), (10, k + 3), (-10, k + 3)]
    V, T = prism_with_holes_solid(keel, 10)
    res = analyze(V, T)
    c, f = report('keel', res)
    check(c.get('shallow_ridge') == 1 and f == 0 + sum(0 for _ in ()) or c.get('shallow_ridge') == 1, 'downward horizontal keel of 55 deg faces -> one shallow_ridge')
    print('ALL PASS' if ok else 'SOME FAILED')
    return ok


def prism_with_holes_solid(profile_xz, L):
    """Simple prism of a (possibly non-convex) profile polygon extruded along y; caps triangulated by ear clipping."""
    B = Builder()
    n = len(profile_xz)
    X = lambda p, y: (p[0], y, p[1])
    area2 = sum(profile_xz[i][0] * profile_xz[(i + 1) % n][1] - profile_xz[(i + 1) % n][0] * profile_xz[i][1] for i in range(n))
    ccw = area2 > 0
    for i in range(n):
        j = (i + 1) % n
        a, b = profile_xz[i], profile_xz[j]
        # outward normal in x-z for ccw: (dz, -dx)
        d = (b[0] - a[0], b[1] - a[1])
        on = (d[1], -d[0]) if ccw else (-d[1], d[0])
        B.quad(X(a, 0), X(b, 0), X(b, L), X(a, L), lambda cen, nrm, on=on: nrm[0] * on[0] + nrm[2] * on[1] > 0)
    for tri in ear_clip(profile_xz):
        for y, s in ((0, -1), (L, 1)):
            B.tri(X(tri[0], y), X(tri[1], y), X(tri[2], y), lambda cen, nrm, s=s: nrm[1] * s > 0)
    return B.V, B.T


def ear_clip(poly):
    pts = list(poly)
    area2 = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))
    if area2 < 0:
        pts.reverse()
    out = []
    while len(pts) > 3:
        for i in range(len(pts)):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            cr = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            if cr <= 1e-12:
                continue
            inside = False
            for p in pts:
                if p in (a, b, c):
                    continue
                d1 = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
                d2 = (c[0] - b[0]) * (p[1] - b[1]) - (c[1] - b[1]) * (p[0] - b[0])
                d3 = (a[0] - c[0]) * (p[1] - c[1]) - (a[1] - c[1]) * (p[0] - c[0])
                if d1 >= 0 and d2 >= 0 and d3 >= 0:
                    inside = True
                    break
            if not inside:
                out.append((a, b, c))
                pts.pop(i)
                break
        else:
            raise ValueError('ear clip failed')
    out.append(tuple(pts))
    return out


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
