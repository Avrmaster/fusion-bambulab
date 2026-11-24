"""The overhang gate's judge on hand-made facts (no Fusion).    python3 -m unittest tests.test_overhang_core -v"""
import json, math, os, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
import overhang_core as oc  # noqa: E402

with open(os.path.join(ROOT, 'rules', 'overhang.json'), encoding='utf-8') as f:
    RULES = json.load(f)
DOWN = [0.0, 0.0, -1.0]


def rect(x0, y0, x1, y1, z, anchored):
    """A flat ceiling at height z: four edges, anchored = which edges (S, E, N, W) have a wall going down."""
    c = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    loops = []
    for k in range(4):
        (a, b), (p, q) = c[k], c[k + 1]
        n = 8
        pts = [[a + (p - a) * i / n, b + (q - b) * i / n, z] for i in range(n + 1)]
        loops.append({'pts': pts, 'anch': [anchored[k]] * n})
    return {'i': 0, 'type': 'plane', 'n': DOWN, 'area': (x1 - x0) * (y1 - y0), 'h': [z, z], 'loops': loops}


def part(faces, ridges=(), name='p'):
    return {'name': name, 'down': DOWN, 'bed_h': 0.0, 'faces': faces, 'ridges': list(ridges)}


def tilted(slope_deg, area=50.0, i=0):
    s = math.radians(slope_deg)
    n = [math.sin(s), 0.0, -math.cos(s)]               # 0 = flat ceiling, 90 = wall
    pts = [[0, 0, 10], [10, 0, 10], [10, 5, 15], [0, 5, 15], [0, 0, 10]]
    return {'i': i, 'type': 'plane', 'n': n, 'area': area, 'h': [10, 15], 'loops': [{'pts': pts, 'anch': []}]}


def hole_tris(d=4.0, z0=10.0, length=10.0, step=10):
    """The top half of a horizontal hole along x, as downward triangles of its (concave) surface."""
    r = d / 2; out = []
    for k in range(-90 + step // 2, 90, step):
        a = math.radians(k)                           # 0 = the very top
        y, z = r * math.sin(a), z0 + r * math.cos(a)
        n = [0.0, math.sin(a), -math.cos(a)]          # pointing into the hole: down at the top
        out.append([5.0, y, z] + n + [length * r * math.radians(step) / 1])
    return out


def codes(p, pockets=()):
    return sorted(r['code'] for r in oc.judge_part(p, RULES, pockets)['regions'])


class Slope(unittest.TestCase):
    def test_45_chamfer_passes(self):
        self.assertEqual(codes(part([tilted(45.0)])), [])

    def test_50_deg_from_vertical_fails(self):
        r = oc.judge_part(part([tilted(40.0)]), RULES)['regions']
        self.assertEqual([x['code'] for x in r], ['overhang'])
        self.assertAlmostEqual(r[0]['slope_deg'], 40.0, places=1)

    def test_within_tolerance(self):
        self.assertEqual(codes(part([tilted(44.7)])), [])

    def test_on_bed_ignored(self):
        f = rect(0, 0, 20, 20, 0.2, [False] * 4)
        self.assertEqual(codes(part([f])), [])


class Bridge(unittest.TestCase):
    def test_short_bridge_passes(self):
        self.assertEqual(codes(part([rect(0, 0, 20, 4, 10, [True, False, True, False])])), [])

    def test_long_bridge(self):
        self.assertEqual(codes(part([rect(0, 0, 20, 10, 10, [True, False, True, False])])), ['long_bridge'])

    def test_narrow_ledge_passes(self):
        # a 0.38 wide ceiling held on one long side only: the wall line still sits on the layer below
        self.assertEqual(codes(part([rect(0, 0, 20, 0.38, 10, [True, False, False, False])])), [])

    def test_cantilever_is_overhang(self):
        self.assertEqual(codes(part([rect(0, 0, 20, 4, 10, [True, False, False, False])])), ['overhang'])

    def test_half_disc_bridges_along_its_free_edge(self):
        # a half disc R2.8 with its straight edge free at 98 deg: the chords parallel to that edge (5.5 long) hold it
        a0 = math.radians(98.0); c = (0.0, 0.0)
        arc = [[c[0] + 2.8 * math.cos(a0 - math.pi * k / 16), c[1] + 2.8 * math.sin(a0 - math.pi * k / 16), 10.0] for k in range(17)]
        f = {'i': 0, 'type': 'plane', 'n': DOWN, 'area': math.pi * 2.8 ** 2 / 2, 'h': [10, 10],
             'loops': [{'pts': arc, 'anch': [True] * 16}, {'pts': [arc[-1], arc[0]], 'anch': [False]}]}
        self.assertEqual(codes(part([f])), [])

    def test_sliver_is_not_an_overhang(self):
        r = oc.judge_part(part([rect(0, 0, 40, 0.05, 10, [True, False, False, False])]), RULES)
        self.assertEqual(r['regions'], []); self.assertEqual([x['code'] for x in r['info']], ['sliver'])

    def test_square_held_all_round(self):
        # 5 x 5 held on all four sides: every point has a chord <= 5
        self.assertEqual(codes(part([rect(0, 0, 5, 5, 10, [True] * 4)])), [])

    def test_partly_bridged_area(self):
        # 20 x 10 held on all four sides: within 3 of the short sides it is bridged lengthwise only if <= 6 ... no: the
        # 10 mm span across and the 20 mm span along are both too long except near the corners
        r = oc.judge_part(part([rect(0, 0, 20, 10, 10, [True] * 4)]), RULES)['regions']
        self.assertEqual([x['code'] for x in r], ['long_bridge'])
        self.assertLess(r[0]['area_mm2'], 200.0); self.assertGreater(r[0]['bridged_mm2'], 0.0)


class Holes(unittest.TestCase):
    def face(self, **kw):
        f = {'i': 1, 'type': 'cylinder', 'axis': [1.0, 0.0, 0.0], 'concave': True, 'd': 4.0, 'area': 60.0, 'tris': hole_tris()}
        f.update(kw); return f

    def test_round_hole(self):
        r = oc.judge_part(part([self.face()]), RULES)['regions']
        self.assertEqual([x['code'] for x in r], ['round_hole']); self.assertEqual(r[0]['d'], 4.0)

    def test_teardrop_passes(self):
        tris = [t for t in hole_tris() if oc.slope(t[3:6], DOWN) >= 45]
        self.assertEqual(codes(part([self.face(tris=tris)])), [])

    def test_two_halves_one_hole(self):
        tr = hole_tris()
        a = self.face(i=1, tris=[t for t in tr if t[1] < 0]); b = self.face(i=2, tris=[t for t in tr if t[1] > 0])
        self.assertEqual(codes(part([a, b])), ['round_hole'])

    def test_convex_cylinder_is_overhang(self):
        self.assertEqual(codes(part([self.face(concave=False)])), ['overhang'])

    def test_heatset_pocket_allowed(self):
        pk = {'a': [1.0, 0.0, 0.0], 'base': [0.0, 0.0, 10.0], 't_mouth': 0.0, 't_floor': 0.54, 'sign': 1,
              'profile': [{'d': 7.5, 'len': 0.54}, {'d': 4.4, 'len': 1.5}, {'d': 3.6, 'len': 5}]}
        p = part([self.face()])
        res = oc.check([p], RULES, {'p': [pk]})
        self.assertEqual(res['findings'], [])
        self.assertEqual([x['code'] for x in res['allowed']], ['heatset_ceiling'])


class Ridges(unittest.TestCase):
    def test_shallow_ridge(self):
        rg = {'p0': [0, 0, 10], 'p1': [10, 0, 15], 'convex': True, 'faces': [5, 6]}      # rises 26.6 deg
        self.assertEqual(codes(part([], [rg])), ['shallow_ridge'])

    def test_steep_ridge_passes(self):
        rg = {'p0': [0, 0, 10], 'p1': [5, 0, 20], 'convex': True, 'faces': [5, 6]}
        self.assertEqual(codes(part([], [rg])), [])

    def test_concave_valley_passes(self):
        rg = {'p0': [0, 0, 10], 'p1': [10, 0, 10], 'convex': False, 'faces': [5, 6]}
        self.assertEqual(codes(part([], [rg])), [])


class BedFace(unittest.TestCase):
    def test_faces(self):
        self.assertEqual(oc.bed_faces({'3mf': [{'face': '-y'}]}), ('-y', None))
        self.assertEqual(oc.bed_faces({'3mf': [{'file': 'x'}]}), (None, 'no_bed_face'))
        self.assertEqual(oc.bed_faces({}), (None, 'no_bed_face'))
        self.assertEqual(oc.bed_faces({'3mf': [{'face': '-y'}, {'face': '+z'}]})[1], 'bed_face_conflict')

    def test_no_bed_face_is_a_finding(self):
        res = oc.check([{'name': 'q', 'problem': 'no_bed_face'}], RULES)
        self.assertEqual([f['code'] for f in res['findings']], ['no_bed_face'])

    def test_down_vectors_match_orient(self):
        # bambu3mf.ORIENT turns the named face's outward normal to -z
        sys.path.insert(0, os.path.join(ROOT, 'tools', 'bambu'))
        import bambu3mf
        for face, d in oc.AXES.items():
            self.assertEqual([round(x, 9) for x in bambu3mf.ORIENT[face](*d)], [0, 0, -1], face)


class Exceptions(unittest.TestCase):
    def res(self, exceptions):
        rules = dict(RULES, exceptions=exceptions)
        return oc.check([part([tilted(40.0)], name='p')], rules)

    def test_no_exception_is_a_finding(self):
        r = self.res([])
        self.assertEqual([f['code'] for f in r['findings']], ['overhang']); self.assertEqual(r['excepted'], [])

    def test_named_exception_with_reason_passes(self):
        r = self.res([{'part': 'p', 'at': [5, 2, 12], 'reason': 'the neighbour fills the chamfer space'}])
        self.assertEqual(r['findings'], [])
        self.assertEqual([(f['code'], f['exception']) for f in r['excepted']], [('overhang', 'the neighbour fills the chamfer space')])

    def test_by_face_index(self):
        self.assertEqual(self.res([{'part': 'p', 'face': 0, 'reason': 'x'}])['findings'], [])

    def test_exception_without_reason_is_a_finding(self):
        r = self.res([{'part': 'p', 'at': [5, 2, 12], 'reason': '  '}])
        self.assertEqual([f['code'] for f in r['findings']], ['exception_without_reason'])

    def test_exception_elsewhere_is_unused(self):
        r = self.res([{'part': 'p', 'at': [90, 90, 90], 'reason': 'x'}])
        self.assertEqual(sorted(f['code'] for f in r['findings']), ['exception_unused', 'overhang'])


class Redesign(unittest.TestCase):
    def kinds(self, faces):
        return sorted(f['redesign']['kind'] for f in oc.check([part(faces)], RULES)['findings'])

    def test_each_finding_gets_one(self):
        hole = {'i': 1, 'type': 'cylinder', 'axis': [1.0, 0.0, 0.0], 'concave': True, 'd': 4.0, 'area': 60.0, 'tris': hole_tris()}
        self.assertEqual(self.kinds([tilted(40.0)]), ['steepen'])
        self.assertEqual(self.kinds([hole]), ['teardrop'])
        self.assertEqual(self.kinds([rect(0, 0, 20, 4, 10, [True, False, False, False])]), ['chamfer_45'])
        self.assertEqual(self.kinds([rect(0, 0, 20, 4, 1.2, [True, False, False, False])]), ['drop_to_bed'])
        self.assertEqual(self.kinds([rect(0, 0, 20, 10, 10, [True, False, True, False])]), ['split_bridge'])
        fillet = {'i': 2, 'type': 'torus', 'concave': False, 'area': 5.0, 'tris': [[0, 0, 0.5, 0.5, 0, -0.866, 3.0]]}
        self.assertEqual(self.kinds([fillet]), ['bottom_chamfer'])


class Rules(unittest.TestCase):
    def test_every_number_has_a_source(self):
        for k, v in RULES.items():
            if isinstance(v, dict) and 'value' in v:
                self.assertIn('source', v, k)
        self.assertIn('source', RULES['slicer'])



class ScrewSeat(unittest.TestCase):
    def ring(self, z):
        loops = []
        for r in (5.5, 2.95):
            pts = [[r * math.cos(2 * math.pi * k / 24), r * math.sin(2 * math.pi * k / 24), z] for k in range(25)]
            loops.append({'pts': pts, 'anch': [False] * 24})
        return {'i': 0, 'type': 'plane', 'n': DOWN, 'area': math.pi * (5.5 ** 2 - 2.95 ** 2), 'h': [z, z], 'loops': loops,
                'annulus': {'c': [0, 0, z], 'd_in': 5.9, 'd_out': 11.0}}

    def test_a_screw_head_seat_is_allowed(self):
        # owner 2026-11-24: support under the seat of a screw head is ok, the head closes it
        self.assertEqual(codes(part([self.ring(4.5)])), ['screw_seat'])
        self.assertIn('screw_seat', RULES['allowed'])
        res = oc.check([part([self.ring(4.5)])], RULES)            # the whole path: finding text, allowed list
        self.assertEqual([x['code'] for x in res['allowed']], ['screw_seat'])
        self.assertEqual(res['findings'], [])

    def test_the_same_ring_without_a_hole_ring_is_a_bridge(self):
        f = self.ring(4.5); f.pop('annulus')
        self.assertNotIn('screw_seat', codes(part([f])))


class WholePartException(unittest.TestCase):
    def test_whole_part_covers_every_finding_of_that_part(self):
        res = {'findings': [{'part': 'a', 'code': 'overhang', 'box': [[0, 0, 0], [1, 1, 1]]},
                            {'part': 'a', 'code': 'long_bridge', 'box': [[9, 9, 9], [10, 10, 10]]},
                            {'part': 'b', 'code': 'overhang', 'box': [[0, 0, 0], [1, 1, 1]]}]}
        r = oc.apply_exceptions(res, [{'part': 'a', 'whole_part': True, 'reason': 'thread part'}])
        self.assertEqual([f['part'] for f in r['findings']], ['b'])
        self.assertEqual(len(r['excepted']), 2)

    def test_whole_part_without_reason_is_a_finding(self):
        res = {'findings': [{'part': 'a', 'code': 'overhang', 'box': [[0, 0, 0], [1, 1, 1]]}]}
        r = oc.apply_exceptions(res, [{'part': 'a', 'whole_part': True}])
        self.assertEqual(r['findings'][0]['code'], 'exception_without_reason')


if __name__ == '__main__':
    unittest.main()
