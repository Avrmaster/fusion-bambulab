"""Probe-side parts of the 0.3.1 fixes, run on stand-ins for the Fusion objects they read (no Fusion): the functions
are taken from the probe texts exactly as gate_fusion_rpc pastes them together, and fed small fakes."""
import math, os, sys, types, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBES = os.path.join(ROOT, 'tools', 'gate', 'probes')


def probe_globals(*names):
    """The globals of the probe files `names` (e.g. '_geom.py', 'fusion_thin.py') run one after the other, as the unit
    text runs them; gate_geo reads a fake's objectType."""
    g = {'__name__': 'probe_test'}
    text = ''
    for name in names:
        with open(os.path.join(PROBES, name), encoding='utf-8') as f:
            text += f.read() + '\n'
    exec(compile(text, 'probe', 'exec'), g)
    g['gate_geo'] = lambda o: str(o.objectType)
    return g


class Coll(list):
    @property
    def count(self):
        return len(self)

    def item(self, i):
        return self[i]


class P3:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z

    def distanceTo(self, o):
        return math.sqrt((self.x - o.x) ** 2 + (self.y - o.y) ** 2 + (self.z - o.z) ** 2)


def circle_loop(cx, r):
    """One loop of one full circle, centre (cx, 0, 0) and radius r, in cm as Fusion gives them."""
    g = types.SimpleNamespace(objectType='Circle3D', center=P3(cx, 0.0, 0.0), radius=r)
    return types.SimpleNamespace(edges=Coll([types.SimpleNamespace(geometry=g)]))


def ring_face(r_out_mm, r_in_mm, offset_mm):
    return types.SimpleNamespace(loops=Coll([circle_loop(0.0, r_out_mm / 10.0), circle_loop(offset_mm / 10.0, r_in_mm / 10.0)]))


class ThinRings(unittest.TestCase):
    """fusion_thin: an annular face whose circles lie a rounding apart is skipped, one further off is a "ring"."""
    def setUp(self):
        self.g = probe_globals('_geom.py', 'fusion_thin.py')

    def test_a_ring_0_03_off_centre_is_an_annulus(self):
        self.assertTrue(self.g['_thn_annulus'](ring_face(2.95, 2.2, 0.03)))           # a melt relief floor re-cut at a rounded centre
        self.assertTrue(self.g['_thn_annulus'](ring_face(2.2, 2.1, 0.003)))           # a 0.1 wide step, 0.003 off

    def test_a_ring_0_3_off_centre_is_not(self):
        self.assertFalse(self.g['_thn_annulus'](ring_face(2.95, 2.2, 0.3)))
        e, big, small = self.g['_thn_ring'](ring_face(2.95, 2.2, 0.3))
        self.assertAlmostEqual(e, 0.3, places=6); self.assertAlmostEqual(big - small - e, 0.45, places=6)

    def test_the_offset_must_also_stay_under_a_quarter_of_the_width(self):
        self.assertFalse(self.g['_thn_annulus'](ring_face(2.2, 2.1, 0.04)))           # 0.04 is under 0.05, but over a quarter of 0.1

    def test_a_face_that_is_no_two_circles_is_no_ring(self):
        arc = types.SimpleNamespace(objectType='Arc3D', center=P3(0, 0, 0), radius=0.2)
        face = types.SimpleNamespace(loops=Coll([types.SimpleNamespace(edges=Coll([types.SimpleNamespace(geometry=arc)])), circle_loop(0.0, 0.1)]))
        self.assertIsNone(self.g['_thn_ring'](face)); self.assertFalse(self.g['_thn_annulus'](face))


class SplitStacks(unittest.TestCase):
    """_geom.stack_holes: a relief and a deepening cut 0.03 mm off a pocket's axis are one stack; a hole 0.3 off is not."""
    def cyl(self, x, y, d, z0, z1):
        return {'origin': (x, y, 0.0), 'axis': (0.0, 0.0, 1.0), 'r': d / 2.0, 'r2': None, 's0': z0, 's1': z1, 'full': True, 'inner': True}

    def test_pieces_0_03_apart_are_one_pocket(self):
        sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
        from probes import _geom as G
        faces = [self.cyl(10.0234, 0.0178, 4.4, 8.52, 10.0), self.cyl(10.0234, 0.0178, 3.6, 4.16, 8.52),   # lead-in and hole on the old axis
                 self.cyl(10.0, 0.0, 5.9, 10.0, 10.66), self.cyl(10.0, 0.0, 3.6, 3.47, 4.16)]               # the relief and the deepening, cut 0.0294 off
        st = G.stack_holes(faces)
        self.assertEqual(len(st), 1)
        self.assertEqual([round(s['r'] * 2, 1) for s in st[0]['spans']], [3.6, 4.4, 5.9])          # the two D3.6 pieces are one segment
        self.assertAlmostEqual(st[0]['lo'], 3.47); self.assertAlmostEqual(st[0]['hi'], 10.66)

    def test_a_hole_0_3_off_stays_apart(self):
        sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
        from probes import _geom as G
        st = G.stack_holes([self.cyl(10.0, 0.0, 4.4, 8.5, 10.0), self.cyl(10.3, 0.0, 3.6, 4.0, 8.5)])
        self.assertEqual(len(st), 2)


class ScrewPick(unittest.TestCase):
    """fusion_screws: a bought part that is no fastener is never a screw by its component's name, and a body with equal
    holes on several axes is no turned part."""
    def setUp(self):
        self.g = probe_globals('_geom.py', '_geom_rings.py', 'fusion_screws.py')
        self.g['gate_search'] = lambda pat, text: bool(pat) and __import__('re').search(pat, text or '') is not None
        self.pats = {'screw': r'(?i:screw|bolt)', 'washer': r'(?i)washer', 'insert': r'(?i)insert', 'nut': r'(?i)\bnut\b', 'bearing': r'(?i)bearing'}

    def rec(self, body, comp, hw=None):
        return {'mesh': False, 'own': False, 'printed': False, 'hw': hw, 'body_name': body, 'comp': comp, 'name': comp + ' / ' + body}

    def test_a_board_in_a_bundle_named_screws_is_no_screw(self):
        world = [self.rec('board 23x23 (J1)', 'ref encoders (magnets, boards, screws, inserts)'),
                 self.rec('magnet D6x2.5', 'ref encoders (magnets, boards, screws, inserts)'),
                 self.rec('Body1', 'ref encoders (magnets, boards, screws, inserts)'),
                 self.rec('M2x6 head (board) #5', 'ref encoders (magnets, boards, screws, inserts)', hw='screw')]
        got = [(r['body_name'], bool(r.get('_not_fastener'))) for r in self.g['_scr_pick'](world, self.pats, ('screw',))]
        self.assertEqual(got, [('board 23x23 (J1)', True), ('magnet D6x2.5', True), ('Body1', False), ('M2x6 head (board) #5', False)])
        # picked so that it is listed as refused; marked, so that _scr_refs never joins it to the screw on its axis

    def test_the_screw_on_one_of_its_axes_is_never_merged_into_the_board(self):
        g = self.g
        g['_scr_circles'] = lambda body: body.circles
        holes = [((x, y, z), (0, 0, 1), 1.1) for x, y in ((0, 0), (20, 0), (0, 20), (20, 20)) for z in (0.0, 1.6)]
        screw = [((0, 0, z), (0, 0, 1), r) for z, r in ((1.6, 1.0), (4.0, 1.0), (4.0, 1.9), (5.6, 1.9))]      # on the axis of the first hole
        def rec(name, circles, flag=False):
            r = {'path': 'enc:1', 'body_name': name, 'name': 'enc / ' + name, 'box': [(0, 0, 0), (1, 1, 1)], 'body': types.SimpleNamespace(circles=circles)}
            if flag:
                r['_not_fastener'] = True
            return r
        groups = g['_scr_refs']([rec('board 23x23', holes, True), rec('M2x6 head #5', screw)], 0.2)
        names = sorted(tuple(r['body_name'] for r in grp['recs']) for grp in groups)
        self.assertEqual(names, [('M2x6 head #5',), ('board 23x23',)])
        board = next(grp for grp in groups if grp['recs'][0]['body_name'] == 'board 23x23')
        self.assertTrue(board.get('not_fastener'))

    def test_a_plate_with_four_equal_holes_is_no_turned_part(self):
        circles = [((x, y, z), (0, 0, 1), 1.0) for x, y in ((0, 0), (10, 0), (0, 10), (10, 10)) for z in (0.0, 1.6)]
        self.assertFalse(self.g['_scr_turned'](circles))
        screw = [((0, 0, z), (0, 0, 1), r) for z, r in ((0.0, 1.5), (3.0, 1.5), (3.0, 2.75), (6.0, 2.75))]
        knurl = screw + [((2.6 * math.cos(k), 2.6 * math.sin(k), 1.0), (0, 0, 1), 0.2) for k in range(20)]   # the teeth of a knurl: many small arcs
        self.assertTrue(self.g['_scr_turned'](screw)); self.assertTrue(self.g['_scr_turned'](knurl))


class ThinSleeveSeat(unittest.TestCase):
    """_geom_rings.seat_scan: a sleeve 0.5 thick on a pin under a D6 bore is read as the D6 seat; the pin alone as before."""
    def setUp(self):
        sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
        from probes import _geom_rings as R
        self.R = R

    def body(self, r_in, r_out, half=5.0):
        return lambda p: abs(p[2]) <= half and r_in <= math.hypot(p[0], p[1]) <= r_out

    def scan(self, inside):
        return self.R.seat_scan(inside, (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 3.0, 5.0, outer=False)

    def test_a_sleeve_half_a_millimetre_thick_is_the_seat(self):
        self.assertEqual(self.scan(self.body(2.5, 3.0))['d'], 6.0)
        self.assertEqual(self.scan(self.body(2.0, 3.0))['d'], 6.0)          # a D4 bore: exactly where the window used to probe
        self.assertTrue(self.R.seat_possible((-3.0, -3.0, -5.0), (3.0, 3.0, 5.0), (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 3.0, outer=False))

    def test_a_bare_pin_still_reads_its_own_diameter(self):
        self.assertEqual(self.scan(self.body(0.0, 2.495))['d'], 4.99)


class SwingBlocks(unittest.TestCase):
    """fusion_openings: what blocks one swing direction keeps the assembly path of a body inside a bought assembly."""
    def test_the_assembly_path_travels_with_the_body(self):
        g = probe_globals('_geom.py', 'fusion_openings.py')
        rows = g['_opn_blocks']([{'name': 'Armature', 'kind': 'bought', 'at': 3.0, 'share': 0.4, 'in': 'Gearbox / Motor'},
                                 {'name': 'Cover', 'kind': 'printed', 'at': 1.0, 'share': 1.0, 'part': 'demo/cover'}])
        self.assertEqual([(r['name'], r.get('in')) for r in rows], [('Cover', None), ('Armature', 'Gearbox / Motor')])



class InsertWallEnd(unittest.TestCase):
    """fusion_inserts: a wall is read at the thinner end of its bisection, rounded down to 0.01 (a wall drawn at a
    limit reads the limit, one a few thousandths under it reads under it)."""
    def walls(self, wall):
        g = probe_globals('_geom.py', 'fusion_inserts.py')
        g['_ins_in'] = lambda body, p, tag=0: math.hypot(p[0], p[1]) < 2.0 + wall      # a ring of plastic `wall` thick round a D4 hole
        return g['_ins_walk'](None, (0.0, 0.0, 0.0), [(1.0, 0.0, 0.0), (0.6, 0.8, 0.0)], 2.0, 0.03, 4.3, dict(g['_INS_RAY']))

    def test_a_wall_at_the_limit_reads_the_limit(self):
        self.assertEqual(self.walls(1.2), [1.2, 1.2])
        self.assertEqual(self.walls(4.0), [4.0, 4.0])

    def test_a_wall_under_the_limit_never_reads_the_limit(self):
        self.assertEqual(self.walls(1.195), [1.19, 1.19])
        self.assertEqual(self.walls(1.199), [1.19, 1.19])


class USlotSamples(unittest.TestCase):
    """fusion_screws: the seat samples of a head reach 1.0 beyond its edge, so the judge can tell a U-slot whose flanks
    run on from a hole broken out of the end of a bar."""
    def test_the_probe_samples_beyond_the_head(self):
        with open(os.path.join(PROBES, 'fusion_screws.py'), encoding='utf-8') as f:
            text = f.read()
        self.assertIn("r_far = sh['head_r'] + 1.0", text)
        self.assertIn('round(r_far, 3)', text)


if __name__ == '__main__':
    unittest.main()
