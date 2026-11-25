"""The thin-wall judge on hand-made meshes: boxes, a plate with a hole, a wedge, a sacrificial layer and a toothed
wheel, each with a known wall.

    python3 -m unittest tests.test_thinwall_core -v"""
import json, math, os, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
import thinwall_core as C  # noqa: E402

RULES = json.load(open(os.path.join(ROOT, 'rules', 'thinwall.json'), encoding='utf-8'))
P06 = [{'project': 'p06.3mf', 'line_mm': 0.62, 'arachne': False, 'layer_mm': 0.3}]
P04 = [{'project': 'p04.3mf', 'line_mm': 0.42, 'arachne': False, 'layer_mm': 0.2}]
P06A = [{'project': 'p06.3mf', 'line_mm': 0.62, 'arachne': True, 'layer_mm': 0.3}]


def face(kind, pts, tris, **geo):
    """A face from points and triangles (indices into pts), wound outwards."""
    v = [c for p in pts for c in p]
    i = [k for t in tris for k in t]
    area = 0.0; mid = [0.0, 0.0, 0.0]
    for a, b, c in tris:
        cr = C._cross(C._sub(pts[b], pts[a]), C._sub(pts[c], pts[a])); ar = C._len(cr) / 2
        area += ar
        for k in range(3):
            mid[k] += ar * (pts[a][k] + pts[b][k] + pts[c][k]) / 3
    f = {'type': kind, 'v': v, 'i': i, 'area': area, 'mid': [m / area for m in mid]}
    f.update(geo)
    return f


def quad(kind, a, b, c, d, **geo):
    """Planar quad a b c d, counter-clockwise seen from outside."""
    n = C._unit(C._cross(C._sub(b, a), C._sub(d, a)))
    return face(kind, [a, b, c, d], [(0, 1, 2), (0, 2, 3)], o=list(a), a=list(n), **geo)


def box(x0, y0, z0, x1, y1, z1):
    P = lambda x, y, z: (x, y, z)  # noqa: E731
    return [quad('plane', P(x0, y0, z0), P(x0, y1, z0), P(x1, y1, z0), P(x1, y0, z0)),      # bottom (-z)
            quad('plane', P(x0, y0, z1), P(x1, y0, z1), P(x1, y1, z1), P(x0, y1, z1)),      # top
            quad('plane', P(x0, y0, z0), P(x1, y0, z0), P(x1, y0, z1), P(x0, y0, z1)),      # -y
            quad('plane', P(x0, y1, z0), P(x0, y1, z1), P(x1, y1, z1), P(x1, y1, z0)),      # +y
            quad('plane', P(x0, y0, z0), P(x0, y0, z1), P(x0, y1, z1), P(x0, y1, z0)),      # -x
            quad('plane', P(x1, y0, z0), P(x1, y1, z0), P(x1, y1, z1), P(x1, y0, z1))]      # +x


def part(name, faces):
    return {'name': name, 'faces': faces}


def prism(poly, z0, z1, hole=None):
    """A prism over a convex-or-star polygon (counter-clockwise, seen from +z), optionally with a round hole at the
    origin that every point of the polygon can see: (radius, segments). Side faces are one plane per polygon edge."""
    n = len(poly)
    faces = []
    for k in range(n):
        a, b = poly[k], poly[(k + 1) % n]
        faces.append(quad('plane', (a[0], a[1], z0), (b[0], b[1], z0), (b[0], b[1], z1), (a[0], a[1], z1)))
    if hole is None:
        c = (sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n)
        top = [(p[0], p[1], z1) for p in poly] + [(c[0], c[1], z1)]
        bot = [(p[0], p[1], z0) for p in poly] + [(c[0], c[1], z0)]
        faces.append(face('plane', top, [(n, k, (k + 1) % n) for k in range(n)], o=list(top[0]), a=[0, 0, 1]))
        faces.append(face('plane', bot, [(n, (k + 1) % n, k) for k in range(n)], o=list(bot[0]), a=[0, 0, -1]))
        return faces
    r, m = hole
    # caps: strip between the hole circle and the outline, both sampled at the same angles from the origin
    angs = [2 * math.pi * j / m for j in range(m)]
    inner = [(r * math.cos(t), r * math.sin(t)) for t in angs]
    outer = [_ray_poly(poly, t) for t in angs]
    for z, up in ((z1, True), (z0, False)):
        pts = [(p[0], p[1], z) for p in inner] + [(p[0], p[1], z) for p in outer]
        tris = []
        for j in range(m):
            a, b, c, d = j, (j + 1) % m, m + (j + 1) % m, m + j
            tris += [(a, d, c), (a, c, b)] if up else [(a, c, d), (a, b, c)]
        faces.append(face('plane', pts, tris, o=list(pts[0]), a=[0, 0, 1 if up else -1]))
    pts = [(p[0], p[1], z0) for p in inner] + [(p[0], p[1], z1) for p in inner]
    tris = []
    for j in range(m):
        a, b, c, d = j, (j + 1) % m, m + (j + 1) % m, m + j
        tris += [(a, c, b), (a, d, c)]                     # normals into the hole: out of the material
    faces.append(face('cylinder', pts, tris, o=[0, 0, z0], a=[0, 0, 1], r=r, d=2 * r, concave=True))
    return faces


def _ray_poly(poly, t):
    d = (math.cos(t), math.sin(t)); best = None
    for k in range(len(poly)):
        a, b = poly[k], poly[(k + 1) % len(poly)]
        e = (b[0] - a[0], b[1] - a[1]); den = d[0] * e[1] - d[1] * e[0]
        if abs(den) < 1e-12:
            continue
        s = (a[0] * e[1] - a[1] * e[0]) / den; u = (a[0] * d[1] - a[1] * d[0]) / den
        if s > 0 and -1e-9 <= u <= 1 + 1e-9 and (best is None or s < best):
            best = s
    return (d[0] * best, d[1] * best)


def codes(res):
    return sorted(f['code'] for f in res['findings'])


class Walls(unittest.TestCase):
    def test_thick_box_passes(self):
        r = C.judge(part('b', box(0, 0, 0, 20, 10, 5)), RULES, P06, '-z')
        self.assertEqual(r['findings'], [])

    def test_sheet_1mm_is_one_finding_with_its_thickness(self):
        r = C.judge(part('s', box(0, 0, 0, 20, 10, 1.0)), RULES, P06, '+y')
        self.assertEqual(codes(r), ['under_two_lines'])
        f = r['findings'][0]
        self.assertAlmostEqual(f['thickness_mm'], 1.0, places=3)
        self.assertAlmostEqual(f['limit_mm'], 1.24, places=3)
        self.assertAlmostEqual(f['at'][2], 0.5, places=3)
        for k in ('measured', 'expected', 'fix', 'at', 'limits', 'project'):
            self.assertTrue(f.get(k), k)

    def test_line_width_comes_from_the_project(self):
        self.assertEqual(codes(C.judge(part('s', box(0, 0, 0, 20, 10, 1.0)), RULES, P04, '+y')), ['thin_wall'])
        self.assertEqual(codes(C.judge(part('s', box(0, 0, 0, 20, 10, 1.0)), RULES, P06A, '+y')), ['thin_wall'])
        self.assertEqual(codes(C.judge(part('s', box(0, 0, 0, 20, 10, 0.5)), RULES, P06A, '+y')), ['under_one_line'])
        # two projects: the wider line counts
        self.assertEqual(codes(C.judge(part('s', box(0, 0, 0, 20, 10, 1.0)), RULES, P04 + P06, '+y')), ['under_two_lines'])

    def test_no_project_still_judges_the_rulebook_wall(self):
        r = C.judge(part('s', box(0, 0, 0, 20, 10, 1.4)), RULES, [], '-z')
        self.assertEqual(codes(r), ['thin_wall'])

    def test_wall_at_the_limit_passes(self):
        self.assertEqual(C.judge(part('s', box(0, 0, 0, 20, 10, 1.5)), RULES, P06, '+y')['findings'], [])

    def test_wall_a_hair_under_the_limit_is_the_limit(self):
        # modelled at the limit, read from the mesh a little under it (robotarm J3 shaft S bush: '1.500 < 1.500')
        self.assertEqual(C.judge(part('s', box(0, 0, 0, 20, 10, 1.4996)), RULES, P06, '+y')['findings'], [])
        self.assertEqual(codes(C.judge(part('s', box(0, 0, 0, 20, 10, 1.49)), RULES, P06, '+y')), ['thin_wall'])

    def test_hole_near_a_face_measured_to_the_exact_cylinder(self):
        # square 20 x 20 round a D6 hole at the origin, shifted so the hole is 1.2 from the +x face
        poly = [(-10, -10), (4.2, -10), (4.2, 10), (-10, 10)]
        r = C.judge(part('h', prism(poly, 0, 8, hole=(3.0, 24))), RULES, P06, '-z')
        self.assertEqual(codes(r), ['under_two_lines'])
        f = r['findings'][0]
        self.assertAlmostEqual(f['thickness_mm'], 1.2, delta=0.002)  # the 24-gon alone reads up to 0.03 more
        self.assertTrue(f['exact'])
        self.assertIn('hole D6.00', f['measured'])


class Exemptions(unittest.TestCase):
    def wedge(self, deg):
        """A long prism whose cross-section is a triangle with one tip of `deg` degrees at x = 0."""
        L = 12.0; h = L * math.tan(math.radians(deg))
        poly = [(0, 0), (L, 0), (L, h)]
        faces = prism(poly, 0, 10)
        return part('w', faces)

    def test_45_degree_wedge_is_a_tip_not_a_wall(self):
        r = C.judge(self.wedge(45), RULES, P06, '-z')
        self.assertEqual(r['findings'], [])
        self.assertGreater(r['counts']['wedge'], 0)

    def test_knife_edge_is_reported(self):
        r = C.judge(self.wedge(10), RULES, P06, '-z')
        self.assertEqual(codes(r), ['under_one_line'])
        self.assertLess(r['findings'][0]['thickness_mm'], 0.1)

    def test_sacrificial_layer_only_in_the_layer_plane(self):
        sheet = part('m', box(0, 0, 0, 10, 10, 0.3))
        r = C.judge(sheet, RULES, P06, '-z')
        self.assertEqual(r['findings'], [])
        self.assertGreater(r['counts']['sacrificial_layer'], 0)
        self.assertEqual(codes(C.judge(sheet, RULES, P06, '+y')), ['under_one_line'])     # printed on its side
        self.assertEqual(codes(C.judge(part('m', box(0, 0, 0, 10, 10, 0.45)), RULES, P06, '-z')), ['under_one_line'])  # 1.5 layers

    def test_teeth_of_a_wheel_are_the_profile(self):
        # 30 teeth, 0.6 mm wide at the tip, round a D10 hole
        poly = []
        N, ro, ri = 30, 12.0, 10.5
        for k in range(N):
            t = 2 * math.pi * k / N; w = 0.3 / ro
            for rr, tt in ((ri, t - 0.045), (ro, t - w), (ro, t + w), (ri, t + 0.045)):
                poly.append((rr * math.cos(tt), rr * math.sin(tt)))
        wheel = part('g', prism(poly, 0, 6, hole=(5.0, 60)))
        self.assertGreaterEqual(len(C.teeth_faces(wheel, RULES)), 2 * N)
        r = C.judge(wheel, RULES, P04, '-z')
        self.assertEqual(r['findings'], [], [f['measured'] for f in r['findings']])
        self.assertGreater(r['counts']['teeth'], 0)
        # the same wheel with fewer teeth than the rule's minimum is judged like any wall
        rules = json.loads(json.dumps(RULES)); rules['exempt']['teeth']['min_copies'] = 2 * N + 1   # left and right flanks are one family
        self.assertTrue(C.judge(wheel, rules, P04, '-z')['findings'])

    def test_accepted_finding_moves_out_of_the_findings(self):
        sheet = part('s', box(0, 0, 0, 20, 10, 1.0))
        first = C.judge(sheet, RULES, P06, '+y')['findings'][0]
        r = C.judge(sheet, RULES, P06, '+y', accepted=[{'near': first['at'], 'within_mm': 3, 'why': 'a shim, thin on purpose'}])
        self.assertEqual(r['findings'], [])
        self.assertEqual(r['accepted'][0]['accepted'], 'a shim, thin on purpose')


class Limits(unittest.TestCase):
    def test_limits_name_their_source(self):
        for k, v in RULES['limits'].items():
            self.assertTrue(v.get('source'), k)
        for k, v in RULES['exempt'].items():
            self.assertTrue(v.get('source'), k)
        self.assertTrue(RULES['sampling'].get('source'))

    def test_strictest_project(self):
        L = C.limits_for(RULES, P04 + P06A + P06)
        self.assertEqual(L['line_mm'], 0.62); self.assertFalse(L['arachne'])


if __name__ == '__main__':
    unittest.main()
