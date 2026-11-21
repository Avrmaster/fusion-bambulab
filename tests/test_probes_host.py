"""The three host-side probes: stl_mesh (closed, one shell), stl_walls (thin material, rims, slits, one-line tubes, with
an honest coverage) and project_consistency (a .3mf against its STL folder, the journal and the stamps).
Every mesh and every .3mf here is made by this file. No Fusion, no Bambu Studio, standard library only."""
import ast, contextlib, io, json, math, os, random, shutil, struct, sys, tempfile, unittest, zipfile
os.environ.pop('CLAUDE_PROJECT_DIR', None)      # bambu3mf finds a gated root for a file outside it from this: never the session's project
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
for d in (os.path.join(ROOT, 'tools', 'bambu'), os.path.join(ROOT, 'tools', 'ledger')):
    if d not in sys.path: sys.path.insert(0, d)
import gate_common, gate_judges, gate_stamp
from gate_common import GateError
from probes import project_consistency, stl_mesh, stl_walls

PROBES = os.path.join(ROOT, 'tools', 'gate', 'probes')
UNDER = 3.0                                          # what a caller passes for a 1.5 mm wall limit: twice the largest limit


# ---- meshes --------------------------------------------------------------------------------------------------------------

def blocks(xs, ys, zs, filled):
    """A closed mesh made of the cells of a box grid (grid lines xs, ys, zs) whose centre satisfies filled(x, y, z).
    Cells that only touch along an edge would make an open mesh: the shapes below avoid that."""
    nx, ny, nz = len(xs) - 1, len(ys) - 1, len(zs) - 1
    occ = [[[bool(filled((xs[i] + xs[i + 1]) / 2, (ys[j] + ys[j + 1]) / 2, (zs[k] + zs[k + 1]) / 2)) for k in range(nz)] for j in range(ny)] for i in range(nx)]
    at = lambda i, j, k: 0 <= i < nx and 0 <= j < ny and 0 <= k < nz and occ[i][j][k]
    tris = []
    def quad(a, b, c, d): tris.append((a, b, c)); tris.append((a, c, d))
    for i in range(nx):
        x0, x1 = xs[i], xs[i + 1]
        for j in range(ny):
            y0, y1 = ys[j], ys[j + 1]
            for k in range(nz):
                if not occ[i][j][k]: continue
                z0, z1 = zs[k], zs[k + 1]
                if not at(i - 1, j, k): quad((x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0))
                if not at(i + 1, j, k): quad((x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1))
                if not at(i, j - 1, k): quad((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1))
                if not at(i, j + 1, k): quad((x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0))
                if not at(i, j, k - 1): quad((x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0))
                if not at(i, j, k + 1): quad((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1))
    return tris


def box(sx, sy, sz, at=(0.0, 0.0, 0.0)):
    return blocks([at[0], at[0] + sx], [at[1], at[1] + sy], [at[2], at[2] + sz], lambda x, y, z: True)


def steps(*stops, step):
    """Grid lines through every stop, the spans between them cut into pieces of about `step`."""
    out = [float(stops[0])]
    for a, b in zip(stops, stops[1:]):
        n = max(1, int(round((b - a) / step)))
        out += [a + (b - a) * (i + 1) / n for i in range(n)]
    return out


def tube(r_in, r_out, height, n=48):
    """A round tube (or a washer) standing on z = 0."""
    tris = []
    def quad(a, b, c, d): tris.append((a, b, c)); tris.append((a, c, d))
    for i in range(n):
        a0, a1 = 2 * math.pi * i / n, 2 * math.pi * (i + 1) / n
        c0, s0, c1, s1 = math.cos(a0), math.sin(a0), math.cos(a1), math.sin(a1)
        if i == n - 1: c1, s1 = 1.0, 0.0
        o0, o1, i0, i1 = (r_out * c0, r_out * s0), (r_out * c1, r_out * s1), (r_in * c0, r_in * s0), (r_in * c1, r_in * s1)
        quad(o0 + (0.0,), o1 + (0.0,), o1 + (height,), o0 + (height,))
        quad(i0 + (0.0,), i0 + (height,), i1 + (height,), i1 + (0.0,))
        quad(o0 + (height,), o1 + (height,), i1 + (height,), i0 + (height,))
        quad(o0 + (0.0,), i0 + (0.0,), i1 + (0.0,), o1 + (0.0,))
    return tris


def wedge(length, angle_deg, width):
    """A prism whose cross-section is a right triangle: the tip along y at x = 0, the slope rising at angle_deg."""
    h = length * math.tan(math.radians(angle_deg))
    a0, b0, c0, a1, b1, c1 = (0.0, 0.0, 0.0), (length, 0.0, 0.0), (length, 0.0, h), (0.0, width, 0.0), (length, width, 0.0), (length, width, h)
    tris = [(a0, b0, c0), (a1, c1, b1)]
    def quad(a, b, c, d): tris.append((a, b, c)); tris.append((a, c, d))
    quad(a0, a1, b1, b0); quad(b0, b1, c1, c0); quad(a0, c0, c1, a1)
    return tris


def faces(*polygons):
    """Triangles of flat convex polygons: (corners, outward direction). The fan starts at the first corner, so a corner
    that lies on a straight side goes first."""
    tris = []
    for pts, out in polygons:
        for i in range(1, len(pts) - 1):
            a, b, c = pts[0], pts[i], pts[i + 1]
            n = ((b[1] - a[1]) * (c[2] - a[2]) - (b[2] - a[2]) * (c[1] - a[1]), (b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2]),
                 (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
            tris.append((a, b, c) if n[0] * out[0] + n[1] * out[1] + n[2] * out[2] > 0 else (a, c, b))
    return tris


def grooved_wedge(length=20.0, width=15.0, g=2.0, y0=5.5, y1=9.5):
    """A 45 degree wedge (tip along y at x = 0) with a groove g deep cut into its base from y0 to y1. The groove runs out
    through the slope: its floor ends there in a 45 degree edge, as a recess does that reaches a slanted face."""
    T = lambda y: (0.0, y, 0.0); B = lambda y: (length, y, 0.0); C = lambda y: (length, y, length); G = lambda y: (g, y, g); K = lambda y: (length, y, g)
    slope, sq = (-1.0, 0.0, 1.0), math.sqrt(0.5)
    return faces(([T(0), B(0), C(0)], (0, -1, 0)), ([T(width), B(width), C(width)], (0, 1, 0)),                       # the two ends
                 ([T(0), B(0), B(y0), T(y0)], (0, 0, -1)), ([T(y1), B(y1), B(width), T(width)], (0, 0, -1)),             # the base, left and right of the groove
                 ([K(y0), B(y0), B(0), C(0), C(y0)], (1, 0, 0)), ([K(y1), C(y1), C(width), B(width), B(y1)], (1, 0, 0)),   # the back
                 ([K(y0), C(y0), C(y1), K(y1)], (1, 0, 0)),
                 ([G(y0), C(y0), C(0), T(0), T(y0)], slope), ([G(y1), T(y1), T(width), C(width), C(y1)], slope),           # the slope
                 ([G(y0), G(y1), C(y1), C(y0)], slope),
                 ([G(y0), K(y0), K(y1), G(y1)], (0, 0, -1)),                                                         # the groove floor
                 ([T(y0), G(y0), K(y0), B(y0)], (0, 1, 0)), ([T(y1), G(y1), K(y1), B(y1)], (0, -1, 0)))                 # the groove walls


def pocket_under_slope(front=2.6, length=20.0, width=15.0, g=2.0, y0=5.5, y1=9.5):
    """The same wedge with a pocket that stops at x = front, before the slope: between its floor and the slope
    (front - g) is left, and (front - g) / sqrt 2 between its front corner and the slope. Nothing meets in an edge there."""
    T = lambda y: (0.0, y, 0.0); B = lambda y: (length, y, 0.0); C = lambda y: (length, y, length); K = lambda y: (length, y, g)
    F = lambda y: (front, y, 0.0); H = lambda y: (front, y, g)
    slope = (-1.0, 0.0, 1.0)
    return faces(([T(0), B(0), C(0)], (0, -1, 0)), ([T(width), B(width), C(width)], (0, 1, 0)),
                 ([F(y0), B(y0), B(0), T(0), T(y0)], (0, 0, -1)), ([F(y1), T(y1), T(width), B(width), B(y1)], (0, 0, -1)),
                 ([T(y0), F(y0), F(y1), T(y1)], (0, 0, -1)),                                                         # the base in front of the pocket
                 ([K(y0), B(y0), B(0), C(0), C(y0)], (1, 0, 0)), ([K(y1), C(y1), C(width), B(width), B(y1)], (1, 0, 0)),
                 ([K(y0), C(y0), C(y1), K(y1)], (1, 0, 0)),
                 ([T(0), T(y0), C(y0), C(0)], slope), ([T(y0), T(y1), C(y1), C(y0)], slope), ([T(y1), T(width), C(width), C(y1)], slope),
                 ([F(y0), H(y0), H(y1), F(y1)], (1, 0, 0)), ([H(y0), K(y0), K(y1), H(y1)], (0, 0, -1)),                 # the pocket: front wall and floor
                 ([F(y0), B(y0), K(y0), H(y0)], (0, 1, 0)), ([F(y1), B(y1), K(y1), H(y1)], (0, -1, 0)))                 # and its two sides


def cone(r_base, r_top, height, n=48):
    """A round frustum standing on its base: the side meets the base in a wedge all round."""
    tris = []
    for i in range(n):
        a0, a1 = 2 * math.pi * i / n, 2 * math.pi * (i + 1) / n
        c0, s0, c1, s1 = math.cos(a0), math.sin(a0), math.cos(a1), math.sin(a1)
        if i == n - 1: c1, s1 = 1.0, 0.0
        b0, b1, t0, t1 = (r_base * c0, r_base * s0, 0.0), (r_base * c1, r_base * s1, 0.0), (r_top * c0, r_top * s0, height), (r_top * c1, r_top * s1, height)
        tris += [(b0, b1, t1), (b0, t1, t0), ((0.0, 0.0, 0.0), b1, b0), ((0.0, 0.0, height), t0, t1)]
    return tris


def plate_with_needles():
    """The 3 mm plate, its top cut into triangles round two points 0.00001 apart: two needle triangles whose normals are noise."""
    tris = [t for t in box(60, 40, 3) if not all(p[2] == 3 for p in t)]
    a, b, c, d, m1, m2 = (0.0, 0.0, 3.0), (60.0, 0.0, 3.0), (60.0, 40.0, 3.0), (0.0, 40.0, 3.0), (30.0, 20.0, 3.0), (30.0, 20.00001, 2.99998)
    return tris + [(a, b, m1), (b, c, m1), (c, m2, m1), (c, d, m2), (d, a, m2), (a, m1, m2)]


def turned(tris, axis=(0.3, -0.5, 0.8), deg=37.0, shift=(12.5, -7.25, 30.0)):
    """The mesh turned by a free angle about an axis and moved: nothing in it lies along the coordinate axes any more."""
    l = math.sqrt(sum(a * a for a in axis)); kx, ky, kz = (a / l for a in axis)
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    def f(p):
        x, y, z = p; d = kx * x + ky * y + kz * z
        cx, cy, cz = ky * z - kz * y, kz * x - kx * z, kx * y - ky * x
        return (x * c + cx * s + kx * d * (1 - c) + shift[0], y * c + cy * s + ky * d * (1 - c) + shift[1], z * c + cz * s + kz * d * (1 - c) + shift[2])
    return [tuple(f(p) for p in t) for t in tris]


def write_stl(path, tris, ascii=False):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if ascii:
        with open(path, 'w') as f:
            f.write('solid demo\n')
            for t in tris:
                f.write(' facet normal 0 0 0\n  outer loop\n' + ''.join('   vertex %r %r %r\n' % tuple(p) for p in t) + '  endloop\n endfacet\n')
            f.write('endsolid demo\n')
        return path
    with open(path, 'wb') as f:
        f.write(b'demo part'.ljust(80, b'\0') + struct.pack('<I', len(tris)))
        for t in tris:
            f.write(struct.pack('<12fH', 0.0, 0.0, 0.0, *(c for p in t for c in p), 0))
    return path


# the shapes the owner paid for, with invented sizes
D = 0.86 / math.sqrt(2.0)
SHAPES = {
    'plate_3': lambda: box(60, 40, 3),
    'neck_086': lambda: blocks([0, 10, 20, 30], [0, 9.57, 10.43, 20], [0, 5], lambda x, y, z: x < 10 or x > 20 or 9.57 < y < 10.43),
    'rim_095': lambda: blocks([0, 11, 19, 30], [0, 0.95, 8.95, 20], [0, 4, 10], lambda x, y, z: not (11 < x < 19 and 0.95 < y < 8.95 and z > 4)),
    'tube_08': lambda: tube(2.0, 2.8, 12.0),
    'slit_03': lambda: blocks([0, 10, 10.3, 20], [0, 15], [0, 3, 10], lambda x, y, z: not (10 < x < 10.3 and z > 3)),
    'washer_02': lambda: tube(1.6, 3.5, 0.2),
    'corner_neck': lambda: blocks([0, 5, 15, 15 + D, 25 + D, 30 + D], [0, 5, 15, 15 + D, 25 + D, 30 + D], [0, 6],
                                  lambda x, y, z: not ((5 < x < 15 and 5 < y < 15) or (15 + D < x < 25 + D and 15 + D < y < 25 + D))),
}


class Temp(unittest.TestCase):
    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.d)

    def stl(self, name, tris, **kw):
        return write_stl(os.path.join(self.d, name if name.endswith('.stl') else name + '.stl'), tris, **kw)


# ---- stl_mesh ------------------------------------------------------------------------------------------------------------

class Mesh(Temp):
    def test_a_box_is_closed_one_shell_and_measured(self):
        f = stl_mesh.measure(self.stl('box', box(10, 20, 30, at=(1, 2, 3))))
        self.assertEqual((f['triangles'], f['degenerate'], f['open_edges'], f['non_manifold_edges'], f['flipped_edges'], f['shells']), (12, 0, 0, 0, 0, 1))
        self.assertAlmostEqual(f['volume'], 6000.0, 3); self.assertFalse(f['inverted'])
        self.assertEqual(f['bbox'], [[1.0, 2.0, 3.0], [11.0, 22.0, 33.0]])
        self.assertEqual(f['geom_hash'], gate_common.sibling('meshid').stl_ids(f['stl'])['geom_hash'])
        self.assertEqual(f['sha256'], gate_common.sha256_file(f['stl']))
        self.assertEqual(f['stl'], os.path.join(self.d, 'box.stl'))

    def test_the_listed_keys_are_all_there(self):
        f = stl_mesh.measure(self.stl('box', box(4, 4, 4)))
        for k in ('stl', 'sha256', 'triangles', 'degenerate', 'open_edges', 'non_manifold_edges', 'shells', 'volume', 'bbox', 'geom_hash'):
            self.assertIn(k, f)

    def test_a_missing_triangle_is_three_open_edges(self):
        f = stl_mesh.measure(self.stl('open', box(10, 10, 10)[:-1]))
        self.assertEqual((f['open_edges'], f['non_manifold_edges'], f['shells']), (3, 0, 1))

    def test_two_bodies_are_two_shells_also_when_they_touch_in_one_point(self):
        apart = stl_mesh.measure(self.stl('apart', box(5, 5, 5) + box(5, 5, 5, at=(9, 0, 0))))
        self.assertEqual((apart['shells'], apart['open_edges']), (2, 0))
        corner = stl_mesh.measure(self.stl('corner', box(5, 5, 5) + box(5, 5, 5, at=(5, 5, 5))))
        self.assertEqual((corner['shells'], corner['open_edges'], corner['non_manifold_edges']), (2, 0, 0))

    def test_two_bodies_sharing_an_edge_have_a_non_manifold_edge(self):
        f = stl_mesh.measure(self.stl('edge', box(5, 5, 5) + box(5, 5, 5, at=(5, 5, 0))))
        self.assertEqual((f['non_manifold_edges'], f['open_edges']), (1, 0))

    def test_a_triangle_turned_the_wrong_way_is_flipped_not_open(self):
        tris = box(10, 10, 10); a, b, c = tris[0]; tris[0] = (a, c, b)
        f = stl_mesh.measure(self.stl('flipped', tris))
        self.assertEqual((f['open_edges'], f['non_manifold_edges'], f['flipped_edges']), (0, 0, 3))

    def test_an_inside_out_mesh_says_so_and_keeps_a_positive_volume(self):
        f = stl_mesh.measure(self.stl('inverted', [(a, c, b) for a, b, c in box(2, 3, 4)]))
        self.assertTrue(f['inverted']); self.assertAlmostEqual(f['volume'], 24.0, 4); self.assertEqual(f['flipped_edges'], 0)

    def test_degenerate_triangles_are_dropped_and_counted(self):
        f = stl_mesh.measure(self.stl('degenerate', box(10, 10, 10) + [((0, 0, 0), (0, 0, 0), (10, 0, 0))]))
        self.assertEqual((f['triangles'], f['degenerate'], f['open_edges']), (12, 1, 0))

    def test_a_seam_written_as_a_rounding_error_is_one_vertex(self):
        tris = [tuple((x, 1e-15 if (y == 0 and i % 2) else y, z) for x, y, z in t) for i, t in enumerate(box(10, 10, 10))]
        f = stl_mesh.measure(self.stl('seam', tris, ascii=True))
        self.assertEqual((f['open_edges'], f['shells']), (0, 1))

    def test_ascii_and_binary_give_the_same_facts(self):
        a, b = stl_mesh.measure(self.stl('a', tube(2, 3, 5, 12), ascii=True)), stl_mesh.measure(self.stl('b', tube(2, 3, 5, 12)))
        for k in ('triangles', 'open_edges', 'shells', 'bbox'): self.assertEqual(a[k], b[k], k)
        self.assertAlmostEqual(a['volume'], b['volume'], 2)
        self.assertEqual((a['triangles'], a['open_edges'], a['shells']), (96, 0, 1))

    def test_what_is_not_an_stl_is_a_gate_error(self):
        with self.assertRaises(GateError): stl_mesh.measure(os.path.join(self.d, 'nothing.stl'))
        p = os.path.join(self.d, 'text.stl')
        with open(p, 'w') as f: f.write('this is a note, not a mesh\n')
        with self.assertRaises(GateError): stl_mesh.measure(p)
        with open(p, 'wb') as f: f.write(b'x' * 80 + struct.pack('<I', 5) + b'\0' * 20)       # the header promises five triangles
        with self.assertRaises(GateError): stl_mesh.measure(p)
        with open(p, 'wb') as f: f.write(b'x' * 80 + struct.pack('<I', 0))
        with self.assertRaises(GateError): stl_mesh.measure(p)

    def test_the_command_prints_json_and_fails_with_4(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out): code = stl_mesh.main([self.stl('box', box(3, 3, 3))])
        self.assertEqual(code, 0); self.assertEqual(json.loads(out.getvalue())['shells'], 1)
        err = io.StringIO()
        with contextlib.redirect_stderr(err): self.assertEqual(stl_mesh.main([os.path.join(self.d, 'none.stl')]), 4)
        self.assertIn('GATE ERROR', err.getvalue())

    def test_the_mesh_judges_read_these_facts(self):
        if not (gate_judges.is_built('mesh_closed') and gate_judges.is_built('mesh_one_shell')): self.skipTest('the mesh judges are not built')
        ctx = {'part': 'demo/bracket', 'rule': 'mesh.closed', 'tables': {}, 'target': None, 'census': None, 'prev': None, 'intent': None}
        good, bad = stl_mesh.measure(self.stl('good', box(5, 5, 5))), stl_mesh.measure(self.stl('bad', box(5, 5, 5)[:-1] + box(2, 2, 2, at=(9, 9, 9))))
        self.assertEqual(gate_judges.JUDGES['mesh_closed'](good, {}, ctx), [])
        self.assertEqual([f['verdict'] for f in gate_judges.JUDGES['mesh_closed'](bad, {}, ctx)], ['FAIL'])
        self.assertEqual(gate_judges.JUDGES['mesh_one_shell'](good, {}, dict(ctx, rule='mesh.one_shell')), [])
        self.assertEqual([f['measure'] for f in gate_judges.JUDGES['mesh_one_shell'](bad, {}, dict(ctx, rule='mesh.one_shell'))], [2])


# ---- stl_walls ------------------------------------------------------------------------------------------------------------

class Walls(Temp):
    def measure(self, name, tris=None, under=UNDER, **kw):
        return stl_walls.measure(self.stl(name, SHAPES[name]() if tris is None else tris), under, **kw)

    def thin(self, facts, kind='wall'):
        return [r for r in facts['regions'] if r['kind'] == kind and r['under'] < facts['report_under']]

    def assertFull(self, f):
        self.assertEqual((f['coverage'], f['stopped'], f['samples']), (1.0, None, f['planned']))

    def test_a_3_mm_plate_is_clean(self):
        f = self.measure('plate_3')
        self.assertFull(f); self.assertEqual(f['regions'], []); self.assertEqual(f['wedge_samples'], {'wall': 0, 'slit': 0})
        self.assertAlmostEqual(f['area'], 2 * (60 * 40 + 60 * 3 + 40 * 3), 2); self.assertEqual(f['triangles'], 12)
        self.assertEqual(f['bbox'], [[0.0, 0.0, 0.0], [60.0, 40.0, 3.0]])

    def test_a_plate_turned_by_a_free_angle_is_clean_too(self):
        f = self.measure('turned', turned(SHAPES['plate_3']()))
        self.assertFull(f); self.assertEqual([r for r in f['regions'] if r['min'] < 2.99], [])

    def test_the_listed_keys_are_all_there(self):
        f = self.measure('neck_086')
        for k in ('stl', 'triangles', 'bbox', 'area', 'samples', 'spacing', 'coverage', 'report_under', 'budget_s', 'elapsed_s', 'regions'): self.assertIn(k, f)
        for k in ('kind', 'min', 'p50', 'area', 'point', 'normal', 'samples', 'bbox'): self.assertIn(k, f['regions'][0])
        self.assertEqual((f['report_under'], f['budget_s']), (UNDER, 60.0))
        json.dumps(f)

    def test_a_086_neck_between_two_blocks_is_found(self):
        f = self.measure('neck_086'); self.assertFull(f)
        r = self.thin(f)
        self.assertEqual(len(r), 1); r = r[0]
        self.assertEqual((r['min'], r['p50'], r['under']), (0.86, 0.86, 1.5))
        self.assertAlmostEqual(r['area'], 2 * 10 * 5, delta=3.0)                # the two faces of the neck: 10 long, 5 tall
        self.assertEqual([abs(c) for c in r['normal']], [0.0, 1.0, 0.0])
        self.assertTrue(10 <= r['point'][0] <= 20 and r['point'][1] in (9.57, 10.43), r['point'])
        self.assertTrue(r['bbox'][0][0] >= 10 and r['bbox'][1][0] <= 20)

    def test_a_095_rim_round_a_pocket_is_found_however_small(self):
        f = self.measure('rim_095'); self.assertFull(f)
        r = self.thin(f)
        self.assertEqual(len(r), 1); r = r[0]
        self.assertEqual((r['min'], r['under']), (0.95, 1.5))
        self.assertAlmostEqual(r['area'], 2 * 8 * 6, delta=3.0)                 # 8 wide, 6 deep, both faces: 96 mm2, about 0.05 cm3 of material
        self.assertTrue(11 <= r['point'][0] <= 19 and r['point'][2] >= 4, r['point'])

    def test_a_tube_with_a_08_wall_is_found_as_one_region(self):
        f = self.measure('tube_08'); self.assertFull(f)
        r = self.thin(f)
        self.assertEqual(len(r), 1); r = r[0]
        self.assertTrue(0.79 <= r['min'] <= 0.8, r['min'])                      # between facets: the chord error under 0.8
        self.assertAlmostEqual(r['area'], 2 * math.pi * (2.0 + 2.8) * 12.0, delta=12.0)
        self.assertAlmostEqual(math.hypot(*r['normal'][:2]), 1.0, 3)

    def test_a_03_slit_is_found_as_a_slit_not_as_a_wall(self):
        f = self.measure('slit_03'); self.assertFull(f)
        s = self.thin(f, 'slit')
        self.assertEqual(len(s), 1); s = s[0]
        self.assertEqual((s['min'], s['p50']), (0.3, 0.3))
        self.assertAlmostEqual(s['area'], 2 * 15 * 7, delta=6.0)                # both walls of the gap: 15 long, 7 deep
        self.assertEqual([abs(c) for c in s['normal']], [1.0, 0.0, 0.0])
        self.assertEqual(self.thin(f, 'wall'), [])                              # the two walls beside it are 10 and 9.7 thick

    def test_a_02_washer_is_found(self):
        f = self.measure('washer_02'); self.assertFull(f)
        r = self.thin(f)
        self.assertEqual(len(r), 1); r = r[0]
        self.assertEqual((r['min'], [abs(c) for c in r['normal']]), (0.2, [0.0, 0.0, 1.0]))
        self.assertAlmostEqual(r['area'], 2 * math.pi * (3.5 ** 2 - 1.6 ** 2), delta=3.0)

    def test_every_shape_is_found_the_same_when_it_is_turned_by_a_free_angle(self):
        want = {'neck_086': ('wall', 0.86), 'rim_095': ('wall', 0.95), 'tube_08': ('wall', 0.798), 'slit_03': ('slit', 0.3), 'washer_02': ('wall', 0.2),
                'corner_neck': ('wall', 0.86)}
        for name, (kind, least) in sorted(want.items()):
            with self.subTest(shape=name):
                f = self.measure(name + '_turned', turned(SHAPES[name]())); self.assertFull(f)
                r = self.thin(f, kind)
                self.assertEqual(len(r), 1, r); self.assertAlmostEqual(r[0]['min'], least, delta=0.003)
                self.assertAlmostEqual(math.sqrt(sum(c * c for c in r[0]['normal'])), 1.0, 3)

    def test_a_neck_between_two_corners_is_found_by_the_corner_rays(self):
        f = self.measure('corner_neck'); self.assertFull(f)
        r = self.thin(f)
        self.assertEqual(len(r), 1); r = r[0]
        self.assertAlmostEqual(r['min'], 0.86, delta=0.01)
        self.assertEqual((r['area'], r['samples']), (0.0, r['corner_samples']))   # no face looks through this neck: only fan rays see it
        self.assertGreater(r['samples'], 5); self.assertGreater(f['corner_rays'], 0); self.assertEqual(f['corner_rays'], f['corner_rays_planned'])
        self.assertAlmostEqual(abs(r['normal'][0]), math.sqrt(0.5), 3); self.assertAlmostEqual(abs(r['normal'][1]), math.sqrt(0.5), 3)

    def test_a_45_degree_flank_is_not_a_thin_wall_and_is_counted(self):
        f = self.measure('wedge_45', wedge(20, 45, 15)); self.assertFull(f)
        self.assertEqual(f['regions'], []); self.assertGreater(f['wedge_samples']['wall'], 1000)
        g = self.measure('wedge_45_turned', turned(wedge(20, 45, 15)))
        self.assertEqual(g['regions'], []); self.assertGreater(g['wedge_samples']['wall'], 1000)

    def test_a_round_45_degree_foot_is_not_a_thin_wall_either(self):
        f = self.measure('cone_45', cone(10.0, 4.0, 6.0)); self.assertFull(f)             # the wedge runs round a circle of 48 facets
        self.assertEqual(self.thin(f), []); self.assertGreater(f['wedge_samples']['wall'], 500)
        g = self.measure('cone_45_turned', turned(cone(10.0, 4.0, 6.0)))
        self.assertEqual(self.thin(g), []); self.assertGreater(g['wedge_samples']['wall'], 500)

    def test_a_round_blade_is_listed(self):
        f = self.measure('cone_15', cone(10.0, 2.0, 8.0 * math.tan(math.radians(15.0)))); self.assertFull(f)
        r = self.thin(f)
        self.assertEqual(len(r), 1); self.assertLess(r[0]['min'], 0.1)

    def test_a_recess_that_runs_out_through_a_slanted_face_ends_in_a_wedge_not_in_a_wall(self):
        for name, tris in (('grooved_wedge', grooved_wedge()), ('grooved_wedge_turned', turned(grooved_wedge()))):
            with self.subTest(mesh=name):
                f = self.measure(name, tris); self.assertFull(f)
                self.assertEqual(f['regions'], [])                                    # face rays from the groove floor and fan rays from its two inside corners
                self.assertGreater(f['wedge_samples']['wall'], 1000)
        self.assertEqual(stl_mesh.measure(self.stl('grooved_wedge', grooved_wedge()))['open_edges'], 0)

    def test_a_thin_spot_beside_a_wedge_is_not_taken_for_the_wedge(self):
        # the pocket floor and the base of the wedge look the same way, and the base does meet the slope 3 mm further on
        for name, tris in (('pocket_under_slope', pocket_under_slope()), ('pocket_under_slope_turned', turned(pocket_under_slope()))):
            with self.subTest(mesh=name):
                self.assertEqual(stl_mesh.measure(self.stl(name, tris))['open_edges'], 0)
                f = self.measure(name, tris); self.assertFull(f)
                r = self.thin(f)
                self.assertEqual(len(r), 1, r)
                self.assertAlmostEqual(r[0]['min'], 0.6 / math.sqrt(2.0), delta=0.01)       # from the pocket's front corner to the slope
                self.assertGreater(r[0]['area'], 5.0); self.assertGreater(r[0]['corner_samples'], 3)

    def test_needle_triangles_with_noise_for_a_normal_make_no_corner(self):
        tris = plate_with_needles()
        m = stl_mesh.measure(self.stl('needles', tris))
        self.assertEqual((m['triangles'], m['open_edges'], m['degenerate'], m['shells']), (16, 0, 0, 1))      # the two points are not welded: the needles are real triangles
        f = self.measure('needles', tris); self.assertFull(f)
        self.assertEqual(f['regions'], [])

    def test_a_blade_under_30_degrees_is_listed(self):
        f = self.measure('wedge_20', wedge(20, 20, 15)); self.assertFull(f)
        r = self.thin(f)
        self.assertEqual(len(r), 1); self.assertLess(r[0]['min'], 0.05); self.assertLess(r[0]['point'][0], 0.2)   # at the tip; the 70 degree edge at the top is not listed

    def test_thin_spots_in_a_part_of_thin_walls_are_regions_of_their_own(self):
        # a 2.5 mm plate (under 3.0 everywhere) with two grooves that leave 0.9 and 0.7
        tris = blocks(steps(0, 20, 26, 50, 56, 80, step=2.0), steps(0, 40, step=2.0), [0, 0.7, 0.9, 2.5],
                      lambda x, y, z: not ((20 < x < 26 and z > 0.9) or (50 < x < 56 and z > 0.7)))
        f = self.measure('grooved', tris); self.assertFull(f)
        thin, near = self.thin(f), [r for r in f['regions'] if r['under'] == UNDER]
        self.assertEqual([(r['min'], r['under']) for r in thin], [(0.7, 1.5), (0.9, 1.5)])       # thinnest first
        for r in thin: self.assertAlmostEqual(r['area'], 2 * 6 * 40, delta=25.0)                   # each groove floor, both faces
        self.assertEqual([r['min'] for r in near], [2.5])                                         # the plate between the grooves joins both: its own region
        self.assertTrue(26 <= near[0]['bbox'][0][0] and near[0]['bbox'][1][0] <= 50, near[0]['bbox'])
        self.assertAlmostEqual(thin[0]['rim_area'], 2 * 24 * 40, delta=120.0)                      # the plate outside each groove is that groove's rim
        self.assertAlmostEqual(thin[1]['rim_area'], 2 * 20 * 40, delta=120.0)
        self.assertEqual(near[0]['rim_area'], 0.0)
        listed = sum(r['samples'] + r['rim_samples'] for r in f['regions'])
        self.assertGreater(listed, 0.9 * f['samples'])                                           # top and bottom of the whole plate: nothing left out

    def test_a_gap_between_two_round_tubes_is_one_slit_with_its_rim(self):
        pair = tube(3.0, 5.5, 30.0, 64) + [tuple((x + 12.0, y, z) for x, y, z in t) for t in tube(3.0, 5.5, 30.0, 64)]
        f = self.measure('pair', pair); self.assertFull(f)
        slits = [r for r in f['regions'] if r['kind'] == 'slit']
        self.assertEqual(len(slits), 1); self.assertAlmostEqual(slits[0]['min'], 1.0, delta=0.01)
        self.assertGreater(slits[0]['rim_samples'], 100)                                         # where the gap opens up to 3.0
        self.assertEqual([round(r['min'], 1) for r in f['regions'] if r['kind'] == 'wall'], [2.5, 2.5])

    def test_two_thin_spots_far_apart_are_two_regions(self):
        tris = blocks([0, 10, 10.9, 30, 30.7, 40], [0, 20], [0, 5, 12], lambda x, y, z: z < 5 or 10 < x < 10.9 or 30 < x < 30.7)
        f = self.measure('two_fins', tris); self.assertFull(f)
        self.assertEqual([r['min'] for r in self.thin(f)], [0.7, 0.9])

    def test_a_run_out_of_time_is_not_proven_and_says_so(self):
        f = self.measure('neck_086', budget_s=0.0)
        self.assertEqual((f['coverage'], f['samples'], f['stopped'], f['regions']), (0.0, 0, 'budget', []))
        self.assertGreater(f['planned'], 1000)

    def test_a_run_stopped_half_way_reports_its_share_and_what_it_found_all_over_the_part(self):
        calls = [0]
        def clock():
            calls[0] += 1
            return 0.0 if calls[0] < 110 else 1e9                               # the time ends after about 55 sample checks
        with mock.patch.object(stl_walls.time, 'perf_counter', clock):
            f = self.measure('neck_086')
        self.assertEqual(f['stopped'], 'budget'); self.assertTrue(0.05 < f['coverage'] < 0.9, f['coverage'])
        self.assertLess(f['samples'], f['planned']); self.assertAlmostEqual(f['coverage'], f['samples'] / f['planned'], 3)
        self.assertGreater(f['spacing'], f['spacing_target'] * 1.05)             # fewer rays per area, and it says so
        self.assertEqual([r['min'] for r in self.thin(f)], [0.86])               # the passes are interleaved: the neck was reached

    def test_a_part_that_needs_more_samples_than_allowed_is_sampled_coarser_with_a_coverage_under_one(self):
        with mock.patch.object(stl_walls, 'MAX_SAMPLES', 2000):
            f = self.measure('neck_086')
        self.assertEqual(f['stopped'], 'max_samples'); self.assertTrue(1900 <= f['samples'] <= 2100, f['samples'])
        self.assertAlmostEqual(f['coverage'], f['samples'] / f['planned'], 3); self.assertLess(f['coverage'], 0.1)
        self.assertGreater(f['spacing'], 4 * f['spacing_target'])

    def test_coverage_is_one_only_for_a_full_run(self):
        self.assertEqual(self.measure('plate_3')['coverage'], 1.0)
        with mock.patch.object(stl_walls, 'MAX_SAMPLES', 39999): self.assertLess(self.measure('plate_3')['coverage'], 1.0)

    def test_the_same_seed_gives_the_same_facts_and_another_seed_the_same_finding(self):
        strip = lambda f: {k: v for k, v in f.items() if k not in ('elapsed_s', 'phases')}
        a, b, c = self.measure('rim_095'), self.measure('rim_095'), self.measure('rim_095', seed=7)
        self.assertEqual(strip(a), strip(b)); self.assertNotEqual(a['regions'][0]['point'], c['regions'][0]['point'])
        self.assertEqual([r['min'] for r in self.thin(c)], [0.95])

    def test_a_small_part_is_sampled_finer_and_a_large_one_at_a_quarter_of_report_under(self):
        small, large = self.measure('washer_02'), self.measure('large', box(200, 150, 4))
        self.assertAlmostEqual(small['spacing_target'], UNDER / 16, 4); self.assertAlmostEqual(large['spacing_target'], UNDER / 4, 4)
        self.assertAlmostEqual(large['spacing'], UNDER / 4, 2); self.assertFull(large); self.assertEqual(large['regions'], [])
        self.assertAlmostEqual(large['samples'], large['area'] / (UNDER / 4) ** 2, delta=2)

    def test_report_under_decides_what_is_listed(self):
        self.assertEqual(self.measure('plate_3', under=3.01)['regions'][0]['min'], 3.0)
        self.assertEqual(self.measure('neck_086', under=0.8)['regions'], [])
        f = self.measure('neck_086', under=0.9)
        self.assertEqual([(r['min'], r['under']) for r in f['regions']], [(0.86, 0.9)])

    def test_long_strips_get_samples_along_their_whole_length(self):
        # a tall tube of long thin triangles: every 3 mm band of its height must hold samples
        f = self.measure('tall', tube(4.0, 5.2, 60.0, 96)); self.assertFull(f)
        r = self.thin(f)
        self.assertEqual(len(r), 1); self.assertTrue(r[0]['bbox'][0][2] < 1.0 and r[0]['bbox'][1][2] > 59.0, r[0]['bbox'])
        self.assertAlmostEqual(r[0]['area'], 2 * math.pi * 9.2 * 60.0, delta=0.03 * 2 * math.pi * 9.2 * 60.0)

    def test_a_mesh_that_is_not_closed_is_refused(self):
        for name, tris in (('open', box(10, 10, 10)[:-1]), ('flipped', [box(10, 10, 10)[0][::-1]] + box(10, 10, 10)[1:])):
            with self.subTest(mesh=name), self.assertRaises(GateError) as e: self.measure(name, tris)
            self.assertIn('stl_mesh', str(e.exception))
        with self.assertRaises(GateError): stl_walls.measure(os.path.join(self.d, 'nothing.stl'), UNDER)
        with self.assertRaises(GateError): self.measure('plate_3', under=0)
        with self.assertRaises(GateError): self.measure('plate_3', under='thin')

    def test_an_inside_out_mesh_is_measured_like_the_right_one(self):
        f = self.measure('inverted', [(a, c, b) for a, b, c in SHAPES['neck_086']()])
        self.assertEqual([r['min'] for r in self.thin(f)], [0.86]); self.assertEqual(self.thin(f, 'slit'), [])

    def test_a_medium_mesh_is_done_in_full_within_its_budget(self):
        tris = blocks(steps(0, 3, 57, 60, step=1.5), steps(0, 3, 47, 50, step=1.5), steps(0, 3, 30, step=1.5), lambda x, y, z: z < 3 or x < 3 or x > 57 or y < 3 or y > 47)
        self.assertGreater(len(tris), 12000)
        f = self.measure('tray', tris, budget_s=30.0)
        self.assertFull(f); self.assertEqual(f['regions'], []); self.assertLess(f['elapsed_s'], 30.0)
        self.assertEqual(sorted(f['phases']), ['corner_rays', 'grid', 'rays', 'read', 'regions', 'samples'])

    def test_the_wall_judges_read_these_facts(self):
        names = ('walls_min_material', 'walls_one_line', 'walls_slit')
        if not all(gate_judges.is_built(n) for n in names): self.skipTest('the wall judges are not built')
        params = {'walls.min_wall_effective': 1.5, 'walls.min_wall': 1.5, 'walls.coverage_min': 0.9, 'target.line_width': 0.42, 'walls.slit_min': 0.4}
        def verdicts(facts):
            out = {}
            for n in names:
                ctx = {'part': 'demo/bracket', 'rule': n.replace('_', '.', 1), 'tables': {}, 'target': None, 'census': None, 'prev': None, 'intent': None}
                out[n] = [(f['verdict'], f['measure']) for f in gate_judges.JUDGES[n](facts, params, ctx)]
            return out
        self.assertEqual(verdicts(self.measure('plate_3')), {'walls_min_material': [], 'walls_one_line': [], 'walls_slit': []})
        self.assertEqual(verdicts(self.measure('neck_086'))['walls_min_material'], [('FAIL', 0.86)])
        self.assertEqual(verdicts(self.measure('rim_095'))['walls_min_material'], [('FAIL', 0.95)])
        self.assertEqual(verdicts(self.measure('washer_02'))['walls_min_material'], [('FAIL', 0.2)])
        self.assertEqual([v for v, _ in verdicts(self.measure('tube_08'))['walls_one_line']], ['FAIL'])
        self.assertEqual(verdicts(self.measure('slit_03'))['walls_slit'], [('FAIL', 0.3)])
        short = verdicts(self.measure('neck_086', budget_s=0.0))
        self.assertEqual([v for v, _ in short['walls_min_material']], ['NOT_RUN'])           # nothing sampled is never a pass

    def test_the_command_lists_and_never_judges(self):
        p = self.stl('neck_086', SHAPES['neck_086']())
        out = io.StringIO()
        with contextlib.redirect_stdout(out): code = stl_walls.main([p, '--under', '3', '--budget', '20'])
        self.assertEqual(code, 0); self.assertIn('wall min   0.860', out.getvalue()); self.assertIn('coverage 100.0 %', out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out): self.assertEqual(stl_walls.main([p, '--json']), 0)
        self.assertEqual(json.loads(out.getvalue())['regions'][0]['min'], 0.86)
        out = io.StringIO()
        with contextlib.redirect_stdout(out): self.assertEqual(stl_walls.main([self.stl('plate_3', SHAPES['plate_3']())]), 0)
        self.assertIn('nothing under', out.getvalue())
        err = io.StringIO()
        with contextlib.redirect_stderr(err): self.assertEqual(stl_walls.main([os.path.join(self.d, 'none.stl')]), 4)
        self.assertIn('GATE ERROR', err.getvalue())
        with contextlib.redirect_stderr(io.StringIO()): self.assertEqual(stl_walls.main([]), 4)


def housing(X, Y, Z, wall, step, rib=3.0):
    """An open box with a floor, two ribs, a pocket under the floor 0.95 from the outer face (and 1.0 under the floor's
    top) and a second rib 0.3 beside the first: a part-like mesh of small triangles for the speed runs."""
    xs = steps(0, wall, X * 0.3, X * 0.3 + rib, X * 0.6, X * 0.6 + 8, X - wall, X, step=step)
    ys = steps(0, 0.95, 8.95, Y * 0.5, Y * 0.5 + rib, Y * 0.5 + rib + 0.3, Y * 0.5 + rib + 0.3 + rib, Y - wall, Y, step=step)
    zs = steps(0, wall * 0.75, wall, Z * 0.5, Z, step=step)
    def filled(x, y, z):
        if z < wall: return not (X * 0.6 < x < X * 0.6 + 8 and 0.95 < y < 8.95 and z < wall * 0.75)
        if x < wall or x > X - wall or y > Y - wall or y < wall: return True
        if X * 0.3 < x < X * 0.3 + rib and z < Z * 0.5: return True
        if Y * 0.5 < y < Y * 0.5 + rib and z < Z * 0.5: return True
        return Y * 0.5 + rib + 0.3 < y < Y * 0.5 + 2 * rib + 0.3 and z < Z * 0.5
    return blocks(xs, ys, zs, filled)


@unittest.skipUnless(os.environ.get('GATE_BENCH'), 'speed runs: set GATE_BENCH=1 (about a minute)')
class Speed(Temp):
    """How long the wall probe takes on large meshes. Not part of the normal run: GATE_BENCH=1 python3 -m unittest
    tests.test_probes_host.Speed. Each case must finish in full (coverage 1.0) inside the 60 s budget."""

    def run_case(self, name, tris, want):
        import time
        p = self.stl(name, tris); t = time.time()
        f = stl_walls.measure(p, UNDER, budget_s=60.0)
        sys.stderr.write('\n%s python %s: %d triangles, %.0f mm2, %d samples at %.2f mm, coverage %.4f, %d corner rays, %d regions, %.1f s %s\n'
                         % (name, sys.version.split()[0], f['triangles'], f['area'], f['samples'], f['spacing'], f['coverage'], f['corner_rays'], len(f['regions']),
                            time.time() - t, f['phases']))
        self.assertEqual((f['coverage'], f['stopped']), (1.0, None)); self.assertLess(f['elapsed_s'], 60.0)
        found = [(r['kind'], r['min']) for r in f['regions'] if r['under'] < UNDER]
        for w in want: self.assertIn(w, found)
        return f

    def test_50k_triangles(self):
        tris = housing(100, 80, 60, 4.0, 1.70); self.assertTrue(40000 < len(tris) < 60000, len(tris))
        self.run_case('housing_50k', tris, [('wall', 0.95), ('wall', 1.0), ('slit', 0.3)])

    def test_300k_triangles_200_x_200_x_170(self):
        tris = housing(200, 200, 170, 4.0, 1.715); self.assertTrue(280000 < len(tris) < 320000, len(tris))
        self.run_case('housing_300k', tris, [('wall', 0.95), ('wall', 1.0), ('slit', 0.3)])

    def test_300k_triangles_all_walls_under_report_under(self):
        tris = housing(200, 200, 170, 2.5, 1.715, rib=2.5); self.assertTrue(280000 < len(tris) < 320000, len(tris))
        self.run_case('thin_housing_300k', tris, [('wall', 0.95), ('slit', 0.3)])

    def test_300k_long_strip_triangles(self):
        tris = []                                                               # 290 tubes: every triangle is a strip 30 mm long and 0.15 to 0.27 wide
        for i in range(290):
            tris += [tuple((x + (i % 17) * 12.0, y + (i // 17) * 12.0, z) for x, y, z in t) for t in tube(3.0, 5.5, 30.0, 128)]
        f = self.run_case('tubes_300k', tris, [])
        self.assertEqual(len([r for r in f['regions'] if r['kind'] == 'slit' and r['under'] < UNDER]), 545)      # every 1 mm gap between two tubes


class Grid(unittest.TestCase):
    """The grid the rays walk through must list a triangle in every cell it touches: a missing cell is a missed wall."""

    def test_every_point_of_a_triangle_lies_in_a_cell_that_lists_it(self):
        rnd = random.Random(11)
        def triangle(kind):
            p = [rnd.uniform(5, 55) for _ in range(3)]
            if kind == 'small': size = [rnd.uniform(0.05, 1.5)] * 3
            elif kind == 'strip': size = [0.2, 0.2, 0.2]; size[rnd.randrange(3)] = rnd.uniform(10, 40)      # long along one axis
            elif kind == 'slanted': size = [rnd.uniform(8, 30), rnd.uniform(8, 30), rnd.uniform(0.1, 30)]   # a long thin one across the grid
            else: size = [rnd.uniform(5, 40)] * 3
            q = [p[k] + rnd.uniform(-1, 1) * size[k] for k in range(3)]
            if kind == 'slanted':
                r = [p[k] + (q[k] - p[k]) * rnd.random() + rnd.uniform(-0.3, 0.3) for k in range(3)]
            else:
                r = [p[k] + rnd.uniform(-1, 1) * size[k] for k in range(3)]
            return tuple(p), tuple(q), tuple(r)
        tris = [triangle(kind) for kind in ('small', 'strip', 'slanted', 'fat') for _ in range(60)]
        tris += [((10.0, 10.0, 10.0), (40.0, 10.0, 10.0), (40.0, 30.0, 10.0)), ((0.0, 0.0, 0.0), (60.0, 60.0, 60.0), (0.0, 60.0, 0.0))]
        V = [p for t in tris for p in t]; F = [(3 * i, 3 * i + 1, 3 * i + 2) for i in range(len(tris))]
        for cell, shift in ((1.3, 0.37), (1.0, 0.0), (2.7, 0.5)):                # also a grid whose walls pass through whole numbers
            _, A, _, _, _ = stl_walls._normals(V, F)
            inv = 1.0 / cell; g = -50.0 - shift; NX = int((110 - g) * inv) + 2; NXY = NX * NX       # the triangles reach from -35 to 95
            ox = oy = oz = g * inv
            grid = stl_walls._build_grid(V, F, A, ox, oy, oz, inv, NX, NXY)
            pairs = sum(len(c) for c in grid.values())
            self.assertLess(pairs, 400000, 'a slanted triangle must not fill the box round it')
            for i, (p, q, r) in enumerate(tris):
                if A[i] == 0.0: continue
                points = [p, q, r] + [tuple(p[k] + (q[k] - p[k]) * t for k in range(3)) for t in (0.25, 0.5, 0.75)]
                for _ in range(120):
                    a, b = rnd.random(), rnd.random()
                    if a + b > 1: a, b = 1 - a, 1 - b
                    points.append(tuple(p[k] + a * (q[k] - p[k]) + b * (r[k] - p[k]) for k in range(3)))
                for x, y, z in points:
                    k = int(x * inv - ox) + NX * int(y * inv - oy) + NXY * int(z * inv - oz)
                    self.assertIn(i, grid.get(k, ()), 'triangle %d %r is missing from the cell of its point %r (cell %.1f)' % (i, tris[i], (x, y, z), cell))

    def test_the_build_gives_up_when_its_time_is_over(self):
        V = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]; F = [(0, 1, 2)]
        _, A, _, _, _ = stl_walls._normals(V, F)
        self.assertIsNone(stl_walls._build_grid(V, F, A, -4.0, -4.0, -4.0, 1.0, 16, 256, clock=lambda: 5.0, deadline=1.0))


class StandardLibraryOnly(unittest.TestCase):
    def test_the_host_probes_import_nothing_but_the_standard_library_and_the_gate(self):
        allowed = {'argparse', 'array', 'ast', 'glob', 'hashlib', 'itertools', 'json', 'math', 'os', 'random', 're', 'shutil', 'struct', 'subprocess', 'sys',
                   'time', 'zipfile', 'gate_common', 'gate_stamp', 'gate_registry', 'gate_params', 'probes', 'stl_mesh'}
        for name in ('stl_walls', 'stl_mesh', 'project_consistency', 'project_bed', 'project_slice', 'mesh_overhang'):
            with open(os.path.join(PROBES, name + '.py'), encoding='utf-8') as f: tree = ast.parse(f.read())
            found = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import): found |= {a.name.split('.')[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom): found.add((node.module or '').split('.')[0])
            self.assertTrue(found <= allowed, '%s imports %s' % (name, sorted(found - allowed)))

    def test_the_wall_probe_holds_no_limit_of_a_rule(self):
        with open(os.path.join(PROBES, 'stl_walls.py'), encoding='utf-8') as f: text = f.read()
        for word in ('min_wall', 'slit_min', 'line_width', 'coverage_min'): self.assertNotIn(word, text)


# ---- project_consistency ------------------------------------------------------------------------------------------------------

NS = ('xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" '
      'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" requiredextensions="p"')
SETTINGS = {'nozzle_diameter': ['0.6'], 'line_width': '0.62', 'outer_wall_line_width': '0.62', 'filament_type': ['PETG', 'PETG'], 'filament_settings_id': ['Demo PETG', 'Demo PETG']}
TARGET = {'nozzle': 0.6, 'line_width': 0.62, 'material': 'PETG'}


def indexed(tris):
    """(vertices, triangles) with every corner once, as a project stores a mesh."""
    ids, V, F = {}, [], []
    for t in tris:
        f = []
        for p in t:
            p = tuple(struct.unpack('<3f', struct.pack('<3f', *p)))            # as an STL holds them
            if p not in ids: ids[p] = len(V); V.append(p)
            f.append(ids[p])
        F.append(tuple(f))
    return V, F


def write_3mf(path, objects, plates, settings=SETTINGS, blocker_on=None):
    """A project file as the slicer saves it. objects: [(name, triangles, plate index (1-based) or None, printable)];
    plates: [plate names]. blocker_on: name of an object that also carries a support blocker volume."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    mesh = lambda V, F: ('<mesh><vertices>' + ''.join('<vertex x="%.9g" y="%.9g" z="%.9g"/>' % v for v in V) + '</vertices><triangles>'
                         + ''.join('<triangle v1="%d" v2="%d" v3="%d"/>' % f for f in F) + '</triangles></mesh>')
    files, resources, items, cfg = {}, [], [], []
    for n, (name, tris, plate, printable) in enumerate(objects):
        mesh_id, obj_id, part = 2 * n + 1, 2 * n + 2, 'Objects/object_%d.model' % (n + 1)
        bodies = '  <object id="%d" p:UUID="0" type="model">\n   %s\n  </object>\n' % (mesh_id, mesh(*indexed(tris)))
        comps = '    <component p:path="/3D/%s" objectid="%d" p:UUID="0" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>\n' % (part, mesh_id)
        parts = '    <part id="%d" subtype="normal_part">\n      <metadata key="name" value="%s"/>\n    </part>\n' % (mesh_id, name)
        if name == blocker_on:
            bodies += '  <object id="%d" p:UUID="0" type="model">\n   %s\n  </object>\n' % (9000 + n, mesh(*indexed(box(1, 1, 1))))
            comps += '    <component p:path="/3D/%s" objectid="%d" p:UUID="0" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>\n' % (part, 9000 + n)
            parts += '    <part id="%d" subtype="support_blocker">\n      <metadata key="name" value="blocker"/>\n    </part>\n' % (9000 + n)
        files['3D/' + part] = '<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" %s>\n <resources>\n%s </resources>\n <build/>\n</model>\n' % (NS, bodies)
        resources.append('  <object id="%d" p:UUID="0" type="model">\n   <components>\n%s   </components>\n  </object>\n' % (obj_id, comps))
        items.append('  <item objectid="%d" p:UUID="0" transform="1 0 0 0 1 0 0 0 1 %d 90 0" printable="%d"/>\n' % (obj_id, 90 + 200 * n, 1 if printable else 0))
        cfg.append('  <object id="%d">\n    <metadata key="name" value="%s"/>\n    <metadata key="extruder" value="1"/>\n%s  </object>\n' % (obj_id, name, parts))
    for i, pname in enumerate(plates):
        inst = ''.join('    <model_instance>\n      <metadata key="object_id" value="%d"/>\n      <metadata key="instance_id" value="0"/>\n    </model_instance>\n' % (2 * n + 2)
                       for n, o in enumerate(objects) if o[2] == i + 1)
        cfg.append('  <plate>\n    <metadata key="plater_id" value="%d"/>\n    <metadata key="plater_name" value="%s"/>\n    <metadata key="locked" value="false"/>\n%s  </plate>\n' % (i + 1, pname, inst))
    files['3D/3dmodel.model'] = ('<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" %s>\n <resources>\n%s </resources>\n <build p:UUID="0">\n%s </build>\n</model>\n'
                                 % (NS, ''.join(resources), ''.join(items)))
    files['Metadata/model_settings.config'] = '<?xml version="1.0" encoding="UTF-8"?>\n<config>\n%s</config>\n' % ''.join(cfg)
    if settings is not None: files['Metadata/project_settings.config'] = json.dumps(settings)
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types/>')
        for name, text in files.items(): z.writestr(name, text)
    return path


def moved(tris, dx=0.0, dy=0.0, dz=0.0):
    return [tuple((x + dx, y + dy, z + dz) for x, y, z in t) for t in tris]


def on_face(tris):
    """The mesh turned a quarter turn (the model's +y face down) and centred: what a build does with it."""
    t = [tuple((x, z, -y) for x, y, z in tri) for tri in tris]
    xs = [p[0] for tri in t for p in tri]; ys = [p[1] for tri in t for p in tri]; zs = [p[2] for tri in t for p in tri]
    return moved(t, -(min(xs) + max(xs)) / 2, -(min(ys) + max(ys)) / 2, -(min(zs) + max(zs)) / 2)


BRACKET = lambda: blocks([0, 6, 30], [0, 20], [0, 4, 16], lambda x, y, z: z < 4 or x < 6)          # an angle bracket
COVER = lambda: box(40, 25, 3)
SPACER = lambda: tube(2.2, 4.0, 5.0, 16)


class FakeStamps(object):
    """Stands in for gate_stamp: stamps by geometry and part, and what verify says about each."""

    def __init__(self): self.rows, self.says = [], {}

    def add(self, part, geom, sig='a' * 64, state='valid', why='stamp ok', target=TARGET, time='2026-03-04T10:00:00Z'):
        s = {'part': part, 'sig': sig, 'geom_hash': geom, 'target': target, 'time': time, 'verdict': 'pass', 'checks': {}}
        self.rows.append(s); self.says[(part, sig)] = (state, why)
        return s

    def find_by_geom(self, root, geom): return sorted((s for s in self.rows if s['geom_hash'] == geom), key=lambda s: s['time'], reverse=True)

    def all_stamps(self, root, part=None): return sorted((s for s in self.rows if part is None or s['part'] == part), key=lambda s: s['time'], reverse=True)

    def verify(self, root, reg, stamp, geom_hash=None, census=None, target=None, resolved=None):
        if geom_hash is not None and geom_hash != stamp['geom_hash']: return 'stale', 'another geometry'
        return self.says[(stamp['part'], stamp['sig'])]


class Project(Temp):
    """A gated project in a temp folder: STLs in stl/, one .3mf, a census that knows the parts."""

    def setUp(self):
        super().setUp()
        self.meshid = gate_common.sibling('meshid')
        gate_common.write_json(gate_common.gate_path(self.d, 'project.json'), {'mode': 'observe', 'targets': {'main': TARGET}})
        self.entry = {'key': 'main', 'file': 'bambulab/Demo.3mf', 'stl_dir': 'stl'}
        self.layout = {'documents': [{'key': 'demo', 'name': 'Demo Assembly', 'stl_dir': 'stl'}], 'projects': [self.entry], 'generators': []}
        self.census = {'docs': {}, 'parts': {}}
        self.stamps = FakeStamps()
        for name in ('find_by_geom', 'all_stamps', 'verify'):
            p = mock.patch.object(gate_stamp, name, getattr(self.stamps, name)); p.start(); self.addCleanup(p.stop)
        self.proj = os.path.join(self.d, 'bambulab', 'Demo.3mf')

    def part(self, name, tris, sig='a' * 64):
        """An STL in the folder that the census knows as a part. Returns its geometry hash."""
        write_stl(os.path.join(self.d, 'stl', name + '.stl'), tris)
        self.census['parts']['demo/' + name] = {'doc': 'demo', 'name': name, 'source': 'fusion', 'stl': 'stl/%s.stl' % name, 'sig': sig, 'gone': False}
        return self.meshid.stl_ids(os.path.join(self.d, 'stl', name + '.stl'))['geom_hash']

    def pose(self, tris):
        V, F = indexed(tris)
        return self.meshid.geom_hash(V, len(F))

    def wrote(self, obj, tris, stl, model, project='bambulab/Demo.3mf', stamp=None, **more):
        """The line a tool leaves in the journal when it places a mesh."""
        e = {'v': 1, 't': '2026-03-04T10:35:00Z', 'by': 'tool', 'kind': 'write', 'project': project, 'object': obj, 'plate': None, 'plate_index': None,
             'pose': self.pose(tris), 'model': model, 'stl': stl, 'stl_sha256': None, 'stamp': stamp, 'cad': None, 'tool': 'update_meshes'}
        e.update(more)
        gate_common.append_jsonl(gate_common.events_path(self.d), e)

    def measure(self, entry=None, target=TARGET):
        return project_consistency.measure(self.d, entry or self.entry, self.layout, self.census, declared_target=target, reg={'rules': {}})

    def obj(self, facts, name): return [o for o in facts['objects'] if o['name'] == name][0]

    def stl_row(self, facts, name): return [s for s in facts['stls'] if s['path'] == 'stl/%s.stl' % name][0]

    def judge(self, name, facts):
        if not gate_judges.is_built(name): self.skipTest('%s is not built' % name)
        ctx = {'part': 'main:', 'rule': name.replace('_', '.', 1), 'tables': {}, 'target': TARGET, 'census': None, 'prev': None, 'intent': None}
        return [(f['verdict'], f['part']) for f in gate_judges.JUDGES[name](facts, {}, ctx)]


class Objects(Project):
    def test_every_object_is_listed_with_plate_flag_and_pose_hash(self):
        write_3mf(self.proj, [('bracket', BRACKET(), 1, True), ('cover', COVER(), 2, True)], ['first plate', 'second plate'])
        f = self.measure()
        self.assertEqual((f['project'], f['file'], f['exists'], f['stl_dir']), ('main', 'bambulab/Demo.3mf', True, 'stl'))
        self.assertEqual([(o['name'], o['plate'], o['plate_index'], o['printable']) for o in f['objects']],
                         [('bracket', 'first plate', 1, True), ('cover', 'second plate', 2, True)])
        self.assertEqual(self.obj(f, 'bracket')['pose'], self.pose(BRACKET())); self.assertEqual(len(self.obj(f, 'cover')['pose']), 40)
        self.assertEqual(self.obj(f, 'cover')['triangles'], 12)
        for k in ('name', 'plate', 'plate_index', 'printable', 'pose', 'part', 'stl', 'model', 'stamp'): self.assertIn(k, f['objects'][0])
        for k in ('state', 'why', 'sig12', 'target'): self.assertIn(k, f['objects'][0]['stamp'])
        self.assertEqual(f['target_declared'], TARGET); json.dumps(f)

    def test_pose_hashes_are_the_ones_the_guard_reads(self):
        write_3mf(self.proj, [('bracket', BRACKET(), 1, True), ('cover', COVER(), 1, True)], ['first plate'])
        self.assertEqual({o['name']: o['pose'] for o in self.measure()['objects']}, self.meshid.pose_hashes(self.meshid.read_project(self.proj)))

    def test_an_object_on_no_plate_is_a_fact(self):
        write_3mf(self.proj, [('bracket', BRACKET(), 1, True), ('cover', COVER(), None, True)], ['first plate'])
        f = self.measure()
        self.assertEqual((self.obj(f, 'cover')['plate'], self.obj(f, 'cover')['plate_index']), (None, None))
        self.assertEqual(self.obj(f, 'bracket')['plate'], 'first plate')
        self.assertEqual(self.judge('project_object_on_no_plate', f), [('FAIL', 'main:cover')])

    def test_an_object_that_is_switched_off_is_a_fact(self):
        write_3mf(self.proj, [('bracket', BRACKET(), 1, False), ('cover', COVER(), 1, True)], ['first plate'])
        f = self.measure()
        self.assertEqual([(o['name'], o['printable']) for o in f['objects']], [('bracket', False), ('cover', True)])
        self.assertEqual(self.judge('project_object_not_printable', f), [('FAIL', 'main:bracket')])

    def test_a_plate_without_a_name_is_still_a_plate(self):
        write_3mf(self.proj, [('bracket', BRACKET(), 1, True)], [''])
        o = self.measure()['objects'][0]
        self.assertEqual((o['plate'], o['plate_index']), ('plate 1', 1))

    def test_a_support_blocker_is_not_part_of_the_mesh(self):
        write_3mf(self.proj, [('bracket', BRACKET(), 1, True)], ['first plate'], blocker_on='bracket')
        o = self.measure()['objects'][0]
        self.assertEqual((o['pose'], o['triangles']), (self.pose(BRACKET()), len(BRACKET())))

    def test_a_missing_project_file_is_said_and_the_stl_folder_is_still_listed(self):
        self.part('bracket', BRACKET())
        f = self.measure()
        self.assertEqual((f['exists'], f['objects'], f['target_file']), (False, [], None))
        self.assertEqual([s['path'] for s in f['stls']], ['stl/bracket.stl'])
        self.assertEqual([v for v, _ in self.judge('project_object_on_no_plate', f)], ['NOT_RUN'])

    def test_a_file_that_is_no_project_is_a_gate_error(self):
        os.makedirs(os.path.dirname(self.proj))
        with open(self.proj, 'w') as fh: fh.write('not a zip')
        with self.assertRaises(GateError): self.measure()
        with zipfile.ZipFile(self.proj, 'w') as z: z.writestr('notes.txt', 'no model in here')
        with self.assertRaises(GateError): self.measure()

    def test_nothing_is_written(self):
        self.part('bracket', BRACKET()); write_3mf(self.proj, [('bracket', BRACKET(), 1, True)], ['first plate'])
        def snapshot():
            out = {}
            for dirpath, _, names in os.walk(self.d):
                for n in names:
                    p = os.path.join(dirpath, n); out[os.path.relpath(p, self.d)] = (os.path.getsize(p), gate_common.sha256_file(p))
            return out
        before = snapshot(); self.measure()
        self.assertEqual(snapshot(), before)


class Ties(Project):
    def test_a_write_event_ties_the_object_to_its_stl_and_stamp(self):
        geom = self.part('bracket', BRACKET()); placed = on_face(BRACKET())
        self.stamps.add('demo/bracket', geom)
        write_3mf(self.proj, [('Bracket left', placed, 1, True)], ['first plate'])        # the name says nothing: the journal does
        self.wrote('Bracket left', placed, 'stl/bracket.stl', geom, stamp='demo/bracket@' + 'a' * 12)
        f = self.measure(); o = f['objects'][0]
        self.assertEqual((o['tie'], o['stl'], o['model'], o['part']), ('write', 'stl/bracket.stl', geom, 'demo/bracket'))
        self.assertEqual(o['stamp'], {'state': 'valid', 'why': 'stamp ok', 'sig12': 'a' * 12, 'target': TARGET})
        self.assertEqual(f['writes'], 1)
        self.assertEqual(self.stl_row(f, 'bracket')['objects'], ['main:Bracket left'])
        self.assertEqual(self.judge('project_mesh_unstamped', f) + self.judge('project_mesh_stale', f) + self.judge('project_stl_without_object', f), [])

    def test_a_mesh_written_from_an_stl_that_never_got_a_stamp_is_unstamped(self):
        geom = self.part('bracket', BRACKET())
        write_3mf(self.proj, [('bracket', BRACKET(), 1, True)], ['first plate']); self.wrote('bracket', BRACKET(), 'stl/bracket.stl', geom)
        f = self.measure(); o = f['objects'][0]
        self.assertEqual((o['tie'], o['stamp']['state'], o['part']), ('write', 'none', 'demo/bracket'))
        self.assertEqual(self.judge('project_mesh_unstamped', f), [('FAIL', 'demo/bracket')])
        self.assertEqual((self.stl_row(f, 'bracket')['stamp'], self.judge('project_stl_without_stamp', f)), ('none', [('FAIL', 'demo/bracket')]))

    def test_a_mesh_of_an_older_export_is_stale(self):
        old = self.part('bracket', BRACKET()); placed = moved(BRACKET(), -15, -10, 0)
        self.stamps.add('demo/bracket', old, sig='a' * 64, state='stale', why='the part changed since the stamp', time='2026-03-01T10:00:00Z')
        write_3mf(self.proj, [('bracket', placed, 1, True)], ['first plate']); self.wrote('bracket', placed, 'stl/bracket.stl', old)
        new = self.part('bracket', blocks([0, 6, 30], [0, 20], [0, 5, 16], lambda x, y, z: z < 5 or x < 6), sig='b' * 64)      # exported again, thicker
        self.stamps.add('demo/bracket', new, sig='b' * 64, time='2026-03-04T10:00:00Z')
        f = self.measure(); o = f['objects'][0]
        self.assertEqual((o['tie'], o['model'], o['stamp']['state'], o['stamp']['sig12']), ('write', old, 'stale', 'a' * 12))
        self.assertIn('changed', o['stamp']['why'])
        self.assertEqual(self.judge('project_mesh_stale', f), [('FAIL', 'demo/bracket')])
        row = self.stl_row(f, 'bracket')
        self.assertEqual((row['stamp'], row['objects'], row['named']), ('valid', [], ['main:bracket']))      # the new STL is stamped but in no project
        self.assertEqual(self.judge('project_stl_without_object', f), [('FAIL', 'demo/bracket')])

    def test_a_mesh_whose_geometry_has_no_stamp_while_the_part_has_one_is_stale(self):
        geom = self.part('bracket', BRACKET()); self.stamps.add('demo/bracket', 'f' * 40, sig='c' * 64)       # stamped with another geometry
        write_3mf(self.proj, [('bracket', BRACKET(), 1, True)], ['first plate']); self.wrote('bracket', BRACKET(), 'stl/bracket.stl', geom)
        o = self.measure()['objects'][0]
        self.assertEqual((o['stamp']['state'], o['stamp']['sig12']), ('stale', 'c' * 12)); self.assertIn('another geometry', o['stamp']['why'])

    def test_without_the_journal_a_moved_and_quarter_turned_mesh_is_proven_by_its_geometry(self):
        geom = self.part('bracket', BRACKET()); self.part('cover', COVER()); self.stamps.add('demo/bracket', geom)
        write_3mf(self.proj, [('bracket', on_face(BRACKET()), 1, True), ('lid', moved(COVER(), 3.5, -8.25, 0.125), 1, True)], ['first plate'])
        f = self.measure(); a, b = self.obj(f, 'bracket'), self.obj(f, 'lid')
        self.assertEqual((a['tie'], a['stl'], a['model'], a['part'], a['stamp']['state']), ('geometry', 'stl/bracket.stl', geom, 'demo/bracket', 'valid'))
        self.assertEqual((b['tie'], b['stl'], b['part'], b['stamp']['state']), ('geometry', 'stl/cover.stl', 'demo/cover', 'none'))   # found though the name differs
        self.assertEqual(f['writes'], 0)
        self.assertEqual((self.stl_row(f, 'bracket')['objects'], self.stl_row(f, 'cover')['objects']), (['main:bracket'], ['main:lid']))

    def test_a_carried_event_without_a_source_does_not_stop_the_proof(self):
        geom = self.part('bracket', BRACKET())
        write_3mf(self.proj, [('bracket', BRACKET(), 1, True)], ['first plate'])
        self.wrote('bracket', BRACKET(), None, None, tool='carried')
        o = self.measure()['objects'][0]
        self.assertEqual((o['tie'], o['model']), ('geometry', geom))

    def test_a_mesh_at_a_free_angle_is_only_a_hint_and_gets_no_stamp(self):
        geom = self.part('bracket', BRACKET()); self.stamps.add('demo/bracket', geom)
        write_3mf(self.proj, [('bracket - left', turned(BRACKET(), axis=(0, 0, 1), deg=26.6, shift=(0, 0, 0)), 1, True)], ['first plate'])
        f = self.measure(); o = f['objects'][0]
        self.assertEqual((o['tie'], o['stl'], o['model'], o['part']), ('name', None, None, 'demo/bracket'))
        self.assertEqual(o['hint'], {'stl': 'stl/bracket.stl', 'same_shape': False})       # the box of a turned part differs: meshid's hint says no
        self.assertEqual(o['stamp']['state'], 'none'); self.assertIn('stl/bracket.stl', o['stamp']['why'])
        row = self.stl_row(f, 'bracket')
        self.assertEqual((row['objects'], row['named']), ([], ['main:bracket - left']))
        self.assertEqual(self.judge('project_mesh_unstamped', f), [('FAIL', 'demo/bracket')])

    def test_an_old_geometry_under_the_name_of_an_stl_is_not_that_stl(self):
        self.part('bracket', BRACKET())
        write_3mf(self.proj, [('bracket', blocks([0, 6, 30], [0, 20], [0, 4, 12], lambda x, y, z: z < 4 or x < 6), 1, True)], ['first plate'])
        f = self.measure(); o = f['objects'][0]
        self.assertEqual((o['tie'], o['hint']['same_shape'], o['stamp']['state']), ('name', False, 'none')); self.assertIn('shape differs', o['stamp']['why'])
        self.assertEqual(self.stl_row(f, 'bracket')['objects'], [])

    def test_a_hole_moved_inside_the_part_is_not_the_same_geometry(self):
        # same triangle count, volume, area and box: only the vertices tell
        a = blocks([0, 8, 12, 30], [0, 8, 12, 20], [0, 4], lambda x, y, z: not (8 < x < 12 and 8 < y < 12))
        b = blocks([0, 10, 14, 30], [0, 8, 12, 20], [0, 4], lambda x, y, z: not (10 < x < 14 and 8 < y < 12))
        self.part('plate', b)
        write_3mf(self.proj, [('plate', a, 1, True)], ['first plate'])
        o = self.measure()['objects'][0]
        self.assertEqual((o['tie'], o['model'], o['stamp']['state']), ('name', None, 'none'))
        self.assertTrue(o['hint']['same_shape'])                                 # the hint is fooled, the proof is not

    def test_an_object_that_is_no_part_at_all(self):
        self.part('bracket', BRACKET())
        write_3mf(self.proj, [('purge square', box(30, 30, 1.2), 1, True)], ['first plate'])
        f = self.measure(); o = f['objects'][0]
        self.assertEqual((o['tie'], o['part'], o['stl'], o['hint'], o['stamp']['state']), (None, None, None, None, 'none'))
        self.assertEqual(self.judge('project_mesh_unstamped', f), [('FAIL', 'main:purge square')])


class Stls(Project):
    def test_an_stl_no_project_holds_is_a_fact(self):
        geom = self.part('bracket', BRACKET()); self.part('spacer', SPACER()); self.stamps.add('demo/bracket', geom)
        write_3mf(self.proj, [('bracket', BRACKET(), 1, True)], ['first plate'])
        f = self.measure()
        self.assertEqual([(s['path'], s['part'], s['objects']) for s in f['stls']], [('stl/bracket.stl', 'demo/bracket', ['main:bracket']), ('stl/spacer.stl', 'demo/spacer', [])])
        self.assertEqual(self.judge('project_stl_without_object', f), [('FAIL', 'demo/spacer')])
        for k in ('path', 'part', 'sha256', 'geom_hash', 'stamp', 'why', 'objects'): self.assertIn(k, f['stls'][0])
        self.assertEqual(f['stls'][0]['sha256'], gate_common.sha256_file(os.path.join(self.d, 'stl', 'bracket.stl')))

    def test_an_stl_without_a_stamp_and_one_with_a_stale_stamp(self):
        a = self.part('bracket', BRACKET()); b = self.part('cover', COVER()); self.part('spacer', SPACER())
        self.stamps.add('demo/bracket', a); self.stamps.add('demo/cover', b, state='stale', why='the judge of walls.min_material changed since the stamp')
        write_3mf(self.proj, [('bracket', BRACKET(), 1, True), ('cover', COVER(), 1, True), ('spacer', SPACER(), 1, True)], ['first plate'])
        f = self.measure()
        self.assertEqual([(s['path'], s['stamp']) for s in f['stls']], [('stl/bracket.stl', 'valid'), ('stl/cover.stl', 'stale'), ('stl/spacer.stl', 'none')])
        self.assertIn('judge', self.stl_row(f, 'cover')['why'])
        self.assertEqual(self.judge('project_stl_without_stamp', f), [('FAIL', 'demo/cover'), ('FAIL', 'demo/spacer')])

    def test_an_stl_changed_on_disk_after_its_stamp_is_stale(self):
        old = self.part('bracket', BRACKET()); self.stamps.add('demo/bracket', old, sig='a' * 64)
        self.part('bracket', blocks([0, 6, 30], [0, 20], [0, 4, 15], lambda x, y, z: z < 4 or x < 6))          # written again by something else
        row = self.stl_row(self.measure(), 'bracket')
        self.assertEqual(row['stamp'], 'stale'); self.assertIn('demo/bracket', row['why'])

    def test_an_stl_made_by_code_is_known_by_its_generator_entry(self):
        write_stl(os.path.join(self.d, 'stl', 'shim_05.stl'), box(10, 10, 0.5))
        self.layout['generators'] = [{'part': 'demo/shim_05', 'stl': 'stl/shim_05.stl', 'source': 'tools/make_shims.py'}]
        self.assertEqual(self.measure()['stls'][0]['part'], 'demo/shim_05')

    def test_an_stl_nobody_knows_has_no_part(self):
        write_stl(os.path.join(self.d, 'stl', 'left_over.stl'), box(5, 5, 5))
        f = self.measure()
        self.assertEqual((f['stls'][0]['part'], f['stls'][0]['stamp']), (None, 'none'))
        self.assertEqual(self.judge('project_stl_without_stamp', f), [('FAIL', 'main:left_over.stl')])

    def test_a_file_that_is_no_stl_is_listed_with_the_reason(self):
        os.makedirs(os.path.join(self.d, 'stl'))
        with open(os.path.join(self.d, 'stl', 'broken.stl'), 'w') as fh: fh.write('nothing')
        row = self.measure()['stls'][0]
        self.assertEqual((row['geom_hash'], row['stamp'], row['objects']), (None, 'none', [])); self.assertIn('cannot be read', row['why'])

    def test_an_stl_placed_in_another_registered_project_counts(self):
        geom = self.part('bracket', BRACKET()); self.part('cover', COVER())
        fine = {'key': 'fine', 'file': 'bambulab/Fine.3mf', 'stl_dir': 'stl'}; self.layout['projects'].append(fine)
        write_3mf(self.proj, [('cover', COVER(), 1, True)], ['first plate'])
        write_3mf(os.path.join(self.d, 'bambulab', 'Fine.3mf'), [('bracket', on_face(BRACKET()), 1, True)], ['teeth'])
        f = self.measure()
        self.assertEqual((self.stl_row(f, 'bracket')['objects'], self.stl_row(f, 'cover')['objects']), (['fine:bracket'], ['main:cover']))
        self.assertEqual([o['name'] for o in f['objects']], ['cover'])
        other = self.measure(fine)
        self.assertEqual(([o['name'] for o in other['objects']], other['objects'][0]['model']), (['bracket'], geom))

    def test_another_project_that_cannot_be_read_is_named_not_fatal(self):
        self.part('bracket', BRACKET()); self.layout['projects'].append({'key': 'fine', 'file': 'bambulab/Fine.3mf', 'stl_dir': 'stl'})
        write_3mf(self.proj, [('bracket', BRACKET(), 1, True)], ['first plate'])
        with open(os.path.join(self.d, 'bambulab', 'Fine.3mf'), 'w') as fh: fh.write('broken')
        f = self.measure()
        self.assertEqual((f['unread_projects'], self.stl_row(f, 'bracket')['objects']), (['fine'], ['main:bracket']))


class Target(Project):
    def test_the_target_of_the_file_is_read_from_its_settings(self):
        write_3mf(self.proj, [('bracket', BRACKET(), 1, True)], ['first plate'])
        f = self.measure()
        self.assertEqual({k: f['target_file'][k] for k in ('nozzle', 'line_width', 'materials')}, {'nozzle': 0.6, 'line_width': 0.62, 'materials': ['PETG']})
        self.assertEqual(self.judge('project_target_differs', f), [])

    def test_a_project_set_up_for_another_nozzle_than_declared(self):
        write_3mf(self.proj, [('bracket', BRACKET(), 1, True)], ['first plate'], settings=dict(SETTINGS, nozzle_diameter=['0.4'], line_width='0.42'))
        f = self.measure()
        self.assertEqual((f['target_file']['nozzle'], f['target_file']['line_width'], f['target_declared']['nozzle']), (0.4, 0.42, 0.6))
        self.assertEqual(self.judge('project_target_differs', f), [('FAIL', 'main:Demo.3mf'), ('FAIL', 'main:Demo.3mf')])

    def test_a_part_stamped_for_another_nozzle_carries_that_target(self):
        geom = self.part('bracket', BRACKET()); self.stamps.add('demo/bracket', geom, target={'nozzle': 0.4, 'line_width': 0.42, 'material': 'PETG'})
        write_3mf(self.proj, [('bracket', BRACKET(), 1, True)], ['first plate'])
        f = self.measure()
        self.assertEqual(f['objects'][0]['stamp']['target']['nozzle'], 0.4)
        self.assertEqual(self.judge('project_target_differs', f), [('FAIL', 'demo/bracket')])

    def test_a_file_without_settings_and_a_line_width_in_percent(self):
        write_3mf(self.proj, [('bracket', BRACKET(), 1, True)], ['first plate'], settings=None)
        self.assertIsNone(self.measure()['target_file'])
        write_3mf(self.proj, [('bracket', BRACKET(), 1, True)], ['first plate'], settings={'nozzle_diameter': '0.4', 'line_width': '105%', 'filament_type': 'PLA'})
        self.assertEqual(self.measure()['target_file'], {'nozzle': 0.4, 'line_width': 0.42, 'materials': ['PLA']})


class BuildTable(Project):
    def table(self, text):
        with open(os.path.join(self.d, 'bambulab', 'build_3mf.py'), 'w') as fh: fh.write(text)
        return dict(self.entry, build_table='bambulab/build_3mf.py')

    def setUp(self):
        super().setUp()
        write_3mf(self.proj, [('bracket', BRACKET(), 1, True), ('cover', COVER(), 2, True), ('spacer', SPACER(), None, True)], ['[TODO] base - bracket', 'covers'])

    def test_no_build_table_named_is_none(self):
        self.assertIsNone(self.measure()['build_table'])

    def test_a_literal_table_that_agrees(self):
        e = self.table("import os\nPLATES = [\n    ('[TODO] base: bracket', [('bracket', 'bracket.stl', '-z')]),   # the colon is written as ' -'\n"
                       "    ('covers', [('cover', 'cover.stl', False, {'_filament': 2})], {'cols': 2}),\n    ('spare', [('spacer', 'spacer.stl', '+y')]),\n]\n")
        t = self.measure(e)['build_table']
        self.assertEqual((t['file'], t['literal'], t['plates']), ('bambulab/build_3mf.py', True, 3))
        self.assertEqual([(d['what'], d['object']) for d in t['differs']], [('object on another plate', 'spacer')])      # the table has it on plate 3, the project on none
        self.assertEqual((t['differs'][0]['table'], t['differs'][0]['project']), ('spacer on plate 3 (spare)', 'spacer on no plate'))

    def test_every_kind_of_difference_is_a_row(self):
        e = self.table("PLATES = [('base', [('bracket', 'bracket.stl', '-z'), ('cover', 'cover.stl', False), ('clip', 'clip.stl', False)]), ('covers', [])]\n")
        t = self.measure(e)['build_table']
        self.assertEqual(sorted((d['what'], d.get('object', d.get('plate_index'))) for d in t['differs']),
                         [('object missing in the project', 'clip'), ('object missing in the table', 'spacer'), ('object on another plate', 'cover'), ('plate name', 1)])
        self.assertEqual([v for v, _ in self.judge('project_build_table_differs', self.measure(e))], ['FAIL'] * 4)

    def test_a_computed_table_is_not_compared(self):
        for text in ("NAMES = ['bracket']\nPLATES = [('base', [(n, n + '.stl', False) for n in NAMES])]\n", "PLATES = make_plates()\n", "print('no table here')\n", "PLATES = [(\n"):
            with self.subTest(table=text):
                t = self.measure(self.table(text))['build_table']
                self.assertEqual((t['literal'], t['differs']), (False, [])); self.assertTrue(t['why'])
        self.assertEqual([v for v, _ in self.judge('project_build_table_differs', self.measure(self.table("PLATES = make_plates()\n")))], ['INFO'])

    def test_a_table_file_that_is_missing(self):
        t = self.measure(dict(self.entry, build_table='bambulab/none.py'))['build_table']
        self.assertEqual((t['literal'], t['differs']), (False, []))


class WrittenByTheTools(Temp):
    """The real writer, the real guard and the real ledger leave the journal; the probe reads it."""

    def test_a_project_built_by_bambu3mf_is_tied_by_its_write_events(self):
        try:
            import bambu3mf
        except Exception as e:
            self.skipTest('bambu3mf cannot be imported: %s' % e)
        gate_common.write_json(gate_common.gate_path(self.d, 'project.json'), {'mode': 'observe', 'targets': {'main': TARGET}})
        gate_common.write_json(gate_common.gate_path(self.d, 'layout.json'), {'documents': [{'key': 'demo', 'name': 'Demo Assembly', 'stl_dir': 'stl'}],
                                                                               'projects': [{'key': 'main', 'file': 'bambulab/Demo.3mf', 'stl_dir': 'stl'}]})
        os.makedirs(os.path.join(self.d, 'bambulab', 'ledger'))
        write_stl(os.path.join(self.d, 'stl', 'bracket.stl'), BRACKET()); write_stl(os.path.join(self.d, 'stl', 'cover.stl'), COVER())
        k = bambu3mf.FILAMENT_KEYS; ps = {key: ['0'] * stride for key, stride in k.items()}
        ps.update(filament_settings_id=['Demo PETG'], filament_colour=['#FFFFFF'], flush_volumes_matrix=['0'], filament_self_index=['1'] * k['filament_self_index'],
                  printable_area=['0x0', '180x0', '180x180', '0x180'], different_settings_to_system=['', '', ''], nozzle_diameter=['0.6'], line_width='0.62', filament_type=['PETG'])
        tpl = os.path.join(self.d, 'template.3mf'); proj = os.path.join(self.d, 'bambulab', 'Demo.3mf')
        with zipfile.ZipFile(tpl, 'w') as z:
            z.writestr('[Content_Types].xml', '<Types/>'); z.writestr('_rels/.rels', '<Relationships/>'); z.writestr('Metadata/project_settings.config', json.dumps(ps))
        bambu3mf._NOTES.clear() if hasattr(bambu3mf, '_NOTES') else None
        with contextlib.redirect_stdout(io.StringIO()):
            bambu3mf.new_project(tpl, proj)
            bambu3mf.build(proj, os.path.join(self.d, 'stl'), [('first plate', [('bracket', 'bracket.stl', '+y')]), ('second plate', [('cover', 'cover.stl', False)])])
        events = [e for e in gate_common.read_jsonl(gate_common.events_path(self.d)) if e.get('kind') == 'write']
        if not any(e.get('model') for e in events): self.skipTest('this writer left no write event that names its STL')
        layout, meshid = gate_common.load_layout(self.d), gate_common.sibling('meshid')
        f = project_consistency.measure(self.d, layout['projects'][0], layout, gate_common.load_census(self.d), declared_target=TARGET)
        self.assertEqual([(o['name'], o['plate'], o['plate_index'], o['printable'], o['tie'], o['stl']) for o in f['objects']],
                         [('bracket', 'first plate', 1, True, 'write', 'stl/bracket.stl'), ('cover', 'second plate', 2, True, 'write', 'stl/cover.stl')])
        self.assertEqual(f['objects'][0]['model'], meshid.stl_ids(os.path.join(self.d, 'stl', 'bracket.stl'))['geom_hash'])
        self.assertEqual([o['stamp']['state'] for o in f['objects']], ['none', 'none'])                      # nothing was exported through the gate
        self.assertEqual([(s['path'], s['stamp'], s['objects']) for s in f['stls']], [('stl/bracket.stl', 'none', ['main:bracket']), ('stl/cover.stl', 'none', ['main:cover'])])
        self.assertEqual((f['target_file']['nozzle'], f['target_file']['line_width']), (0.6, 0.62))


if __name__ == '__main__':
    unittest.main()
