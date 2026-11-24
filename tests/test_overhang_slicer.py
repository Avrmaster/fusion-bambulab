"""The overhang gate's slicer comparison on hand-made G-code and frames (no Bambu Studio, no Fusion).
    python3 -m unittest tests.test_overhang_slicer -v"""
import math, os, sys, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate')); sys.path.insert(0, os.path.join(ROOT, 'tools', 'fusion'))
import overhang_slicer as sl  # noqa: E402

GCODE = """; HEADER
; Z_HEIGHT: 0.3
; FEATURE: Brim
G1 X0 Y0 E1
G1 X200 Y0 E1
; start printing object, unique label id: 7
; FEATURE: Outer wall
G1 X10 Y10
G1 X30 Y10 E1
G1 X30 Y20 E1
G1 X10 Y20 E1
G1 X10 Y10 E1
G1 E0.8
; FEATURE: Support
G1 X20 Y15
G1 X22 Y15 E0.1
; stop printing object, unique label id: 7
; Z_HEIGHT: 0.6
; start printing object, unique label id: 7
; FEATURE: Support
G1 X20 Y15
G1 X22 Y15 E0.1
; FEATURE: Support
G1 X50 Y50
G1 X51 Y50 E0.1
; stop printing object, unique label id: 7
; Z_HEIGHT: 5.0
; start printing object, unique label id: 7
; FEATURE: Support interface
G1 X20 Y15
G1 X22 Y15 E0.1
; stop printing object, unique label id: 7
"""


class Gcode(unittest.TestCase):
    def setUp(self):
        fd, self.p = tempfile.mkstemp(suffix='.gcode'); os.close(fd)
        with open(self.p, 'w') as f:
            f.write(GCODE)

    def tearDown(self):
        os.remove(self.p)

    def test_object_box_skips_brim_and_support(self):
        g = sl.read_gcode(self.p, 1.0)
        b = g['7']['box']
        self.assertEqual([b[0][0], b[0][1], b[1][0], b[1][1]], [10.0, 10.0, 30.0, 20.0])
        self.assertAlmostEqual(g['7']['support_mm'], 2 + 2 + 1 + 2, places=6)

    def test_contacts_are_tips_not_feet(self):
        g = sl.read_gcode(self.p, 1.0)
        tips = sl.contacts(g['7']['tops'], 0.3)
        self.assertTrue(all(abs(z - 5.0) < 1e-9 for z in tips.values()), tips)      # the column under (20..22, 15): 0.3, 0.6 and 5.0
        self.assertNotIn((50, 50), tips)                                             # a 0.6 high foot holds nothing
        self.assertEqual(len(sl.clusters(tips)), 1)

    def test_stacked_tips_in_one_column(self):
        lv = {(0, 0): {1.2, 1.5, 1.8, 9.0, 9.3}, (1, 0): {1.2, 1.5, 1.8, 9.0, 9.3}}
        tips = sl.contacts(lv, 0.3)
        self.assertEqual(sorted(tips.values()), [1.8, 1.8, 9.3, 9.3])
        self.assertEqual(len(sl.clusters(tips)), 2)                                    # two heights: two clusters

    def test_slanted_branch_is_not_a_tip(self):
        lv = {(0, 0): {0.9, 1.2}, (1, 0): {1.5, 1.8}, (2, 0): {2.1, 2.4}}
        self.assertEqual(sl.contacts(lv, 0.3), {(2, 0): 2.4})


class Frames(unittest.TestCase):
    def frame(self, face, deg):
        a = math.radians(deg)
        I = [[math.cos(a), math.sin(a), 0], [-math.sin(a), math.cos(a), 0], [0, 0, 1], [500, 300, 20]]
        C = [[1, 0, 0], [0, 1, 0], [0, 0, 1], [0, 0, 0]]
        box = [[-10, -20, 5], [30, 40, 15]]                                          # the body in world mm
        turn = sl.bambu3mf.ORIENT[face]
        corners = [turn(x, y, z) for x in (box[0][0], box[1][0]) for y in (box[0][1], box[1][1]) for z in (box[0][2], box[1][2])]
        lo = [min(c[i] for c in corners) for i in range(3)]; hi = [max(c[i] for c in corners) for i in range(3)]
        c = [(lo[i] + hi[i]) / 2 for i in range(3)]
        V = [[x - c[0], y - c[1], z - c[2]] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]  # mesh: centred
        G = [sl._apply(sl._apply(v, C), I) for v in V]
        o3 = {'xf': [C, I], 'lo': [min(v[i] for v in V) for i in range(3)], 'hi': [max(v[i] for v in V) for i in range(3)],
              'glo': [min(g[i] for g in G) for i in range(3)], 'ghi': [max(g[i] for g in G) for i in range(3)]}
        gb = [[o3['glo'][0] - 480 + 0.3, o3['glo'][1] - 250 + 0.3, 0.3], [o3['ghi'][0] - 480 - 0.3, o3['ghi'][1] - 250 - 0.3, 10]]
        return sl.Frame(face, o3, box, gb), box

    def test_round_trip_every_face_and_turn(self):
        for face in ('-z', '+z', '-y', '+y', '-x', '+x'):
            for deg in (0, 10, -100):
                fr, box = self.frame(face, deg)
                self.assertIsNone(fr.problem(), (face, deg))
                for p in ([0, 0, 10], [30, 40, 5], [-10, -20, 15]):
                    q = fr.to_model(fr.to_print(p))
                    self.assertTrue(all(abs(q[i] - p[i]) < 1e-6 for i in range(3)), (face, deg, p, q))

    def test_bed_face_lands_on_z0(self):
        fr, box = self.frame('-y', 10)
        self.assertAlmostEqual(fr.to_print([0, -20, 10])[2], 0.0, places=6)          # y = -20 is the -y face
        fr, box = self.frame('+z', 0)
        self.assertAlmostEqual(fr.to_print([0, 0, 15])[2], 0.0, places=6)

    def test_stale_mesh_is_an_error(self):
        fr, box = self.frame('-z', 0)
        fr.size_mesh = [fr.size_mesh[0] + 3, fr.size_mesh[1], fr.size_mesh[2]]
        self.assertIn('stale', fr.problem())


class Compare(unittest.TestCase):
    class Ident:
        def to_print(self, p): return list(p)
        def to_model(self, q): return list(q)

    def region(self, code, x, y, h, slope=0.0, area=10.0):
        return {'code': code, 'at': [x, y, h], 'box': [[x - 1, y - 1, h], [x + 1, y + 1, h]], 'area_mm2': area, 'slope_deg': slope}

    def test_both_ways(self):
        regs = [self.region('overhang', 10, 10, 5), self.region('overhang', 40, 40, 5), self.region('overhang', 60, 60, 5, slope=40)]
        info = [self.region('bridge', 80, 80, 5)]
        tips = {(10, 10): 4.7, (80, 80): 4.7, (120, 120): 4.7, (121, 120): 4.7, (120, 121): 4.7}
        r = sl.compare(regs, info, tips, 1.0, 2.0, self.Ident(), 0.3, 30.0, True, 20.0, 500.0)
        got = {tuple(x['at'][:2]): x['slicer'] for x in r['regions']}
        self.assertEqual(got[(10, 10)], 'supports')
        self.assertEqual(got[(40, 40)], 'no support')
        self.assertEqual(got[(60, 60)], 'no support')
        self.assertIn('threshold', next(x for x in r['regions'] if x['at'][0] == 60)['why'])
        self.assertEqual(len(r['slicer_only']), 1)                                    # the 3-cell cluster at 120
        self.assertEqual(r['slicer_on_allowed']['bridge']['n'], 1)
        self.assertFalse(r['agree'])

    def test_quiet_part_agrees(self):
        r = sl.compare([], [], {}, 1.0, 2.0, self.Ident(), 0.3, 30.0, True, 20.0, 0.0)
        self.assertTrue(r['agree'])


if __name__ == '__main__':
    unittest.main()
