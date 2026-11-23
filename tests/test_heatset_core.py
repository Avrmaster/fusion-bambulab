"""tools/gate/heatset_core.py on hand-made B-rep facts: a pocket of the table passes, and each way a pocket or an insert
can be wrong gives its own finding with a fix. No Fusion here (tests/test_heatset_fusion.py runs the real thing)."""
import copy, json, os, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
import heatset_core as H  # noqa: E402

RULES = json.load(open(os.path.join(ROOT, 'rules', 'heatset.json'), encoding='utf-8'))
TOL = RULES['tolerance']
Z = [0.0, 0.0, 1.0]


def cyl(d, z0, z1, concave=True, x=10.0, y=5.0, span=360.0, axis=Z):
    return {'d': d, 'o': [x, y, 0.0], 'a': axis, 't0': z0, 't1': z1, 'concave': concave, 'span': span}


def pocket(top=10.0, relief=(7.5, 0.54), lead=(4.4, 1.5), hole=(3.6, 6.5), x=10.0, y=5.0, below=None):
    """A blind pocket opening upwards at z = top. hole[1] is the depth below the relief floor."""
    c, z = [], top
    if relief:
        c.append(cyl(relief[0], z - relief[1], z, x=x, y=y)); z -= relief[1]
    floor = z
    if lead:
        c.append(cyl(lead[0], z - lead[1], z, x=x, y=y)); z -= lead[1]
    if hole:
        c.append(cyl(hole[0], floor - hole[1], z, x=x, y=y)); z = floor - hole[1]
    if below:
        c.append(cyl(below[0], z - below[1], z, x=x, y=y))
    return c


def golden_insert(floor, x=10.0, y=5.0, pilot_down=True, **over):
    g = dict(RULES['kinds']['M3x4x5']['golden'], **over)
    L, pl = g['length'], g['pilot_len']
    if pilot_down:
        c = [cyl(g['pilot_d'], floor - L, floor - L + pl, False, x, y), cyl(g['knurl_d'], floor - L + pl, floor, False, x, y)]
    else:
        c = [cyl(g['knurl_d'], floor - L, floor - pl, False, x, y), cyl(g['pilot_d'], floor - pl, floor, False, x, y)]
    c.append(cyl(g['bore_d'], floor - L, floor, True, x, y))
    return {'name': g['name'], 'path': 'HW:1/' + g['name'], 'host': 'demo', 'cylinders': c}


def run(cyls, inserts, open_lo=False, open_hi=True):
    groups = H.group_holes(cyls, TOL)
    for g in groups:
        g['open_lo'], g['open_hi'] = open_lo, open_hi
    return H.check([{'name': 'demo', 'groups': groups}], inserts, RULES)


def codes(res):
    return sorted(f['code'] for f in res['findings'])


class Pocket(unittest.TestCase):
    def test_golden_pocket_passes(self):
        res = run(pocket(), [golden_insert(10 - 0.54)])
        self.assertEqual(codes(res), [])
        self.assertEqual(res['counts'], {'inserts': 1, 'in_pockets': 1, 'pockets': 1, 'outside_printed_parts': 0})

    def test_smaller_reliefs_pass(self):
        for d in (6.5, 5.9):
            self.assertEqual(codes(run(pocket(relief=(d, 0.54)), [golden_insert(10 - 0.54)])), [], d)

    def test_no_relief(self):
        res = run(pocket(relief=None), [golden_insert(10.0)])
        self.assertEqual(codes(res), ['relief_missing'])
        f = res['findings'][0]
        self.assertEqual(f['at'], [10.0, 5.0, 10.0]); self.assertEqual(f['out'], [0.0, 0.0, 1.0])
        self.assertIn('relief', f['fix']); self.assertIn('7.5', f['fix'])

    def test_wrong_relief_diameter(self):
        self.assertEqual(codes(run(pocket(relief=(7.0, 0.54)), [golden_insert(10 - 0.54)])), ['relief_d'])

    def test_wrong_relief_depth(self):
        self.assertEqual(codes(run(pocket(relief=(7.5, 1.0)), [golden_insert(9.0)])), ['relief_depth'])

    def test_old_straight_pocket(self):
        # straight D4.4 x 6: the form that held nothing
        res = run(pocket(lead=(4.4, 6.0), hole=None), [golden_insert(10 - 0.54)])
        self.assertIn('lead_in_depth', codes(res)); self.assertIn('hole_missing', codes(res))

    def test_no_lead_in(self):
        self.assertEqual(codes(run(pocket(lead=None), [golden_insert(10 - 0.54)])), ['lead_in_missing'])

    def test_wrong_hole(self):
        self.assertEqual(codes(run(pocket(hole=(3.4, 6.5)), [golden_insert(10 - 0.54)])), ['hole_d'])

    def test_short_hole(self):
        res = run(pocket(hole=(3.6, 5.0)), [golden_insert(10 - 0.54)])
        self.assertEqual(codes(res), ['hole_short']); self.assertIn('5.00', res['findings'][0]['measured'])

    def test_short_hole_into_screw_hole(self):
        res = run(pocket(hole=(3.6, 4.5), below=(3.2, 10.0)), [golden_insert(10 - 0.54)])
        self.assertEqual(codes(res), ['hole_short'])

    def test_short_but_through_passes(self):
        self.assertEqual(codes(run(pocket(hole=(3.6, 5.0)), [golden_insert(10 - 0.54)], open_lo=True)), [])

    def test_deep_screw_hole_after_full_depth_passes(self):
        self.assertEqual(codes(run(pocket(below=(3.2, 10.0)), [golden_insert(10 - 0.54)])), [])

    def test_pocket_in_a_recess_floor(self):
        c = [cyl(10.0, 10.0, 13.0)] + pocket()
        self.assertEqual(codes(run(c, [golden_insert(10 - 0.54)])), [])

    def test_recess_is_not_the_relief(self):
        c = [cyl(10.0, 10.0, 13.0)] + pocket(relief=None)
        res = run(c, [golden_insert(10.0)])
        self.assertEqual(codes(res), ['relief_missing'])
        self.assertIn('washer seat, is not the relief', res['findings'][0]['measured'])
        self.assertIn('floor of the recess', res['findings'][0]['fix'])

    def test_open_wall(self):
        c = pocket()
        c[1]['span'] = 200.0
        self.assertEqual(codes(run(c, [golden_insert(10 - 0.54)])), ['open_wall'])

    def test_split_faces_join(self):
        c = pocket()
        half = dict(c[1], span=180.0); c[1] = half; c.append(dict(half))
        long1, long2 = dict(c[2], t1=6.0), dict(c[2], t0=6.0)
        c[2] = long1; c.append(long2)
        self.assertEqual(codes(run(c, [golden_insert(10 - 0.54)])), [])

    def test_both_ends_of_a_post(self):
        c = pocket(top=37.0, hole=(3.6, 37.0 - 0.54 - 2.04))
        bottom = [dict(x, t0=37.0 - x['t1'], t1=37.0 - x['t0']) for x in pocket(top=37.0, hole=None)]
        res = run(c + bottom, [golden_insert(37 - 0.54), golden_insert(0.54 + 4.0, pilot_down=False)], open_lo=True)
        self.assertEqual(codes(res), []); self.assertEqual(res['counts']['pockets'], 2)

    def test_tilted_axis(self):
        import math
        a = [0.0, math.sin(0.3), math.cos(0.3)]
        c = [dict(x, a=a) for x in pocket()]
        ins = golden_insert(10 - 0.54)
        for x in ins['cylinders']: x['a'] = a
        self.assertEqual(codes(run(c, [ins])), [])

    def test_wood_screw_hole_is_no_pocket(self):
        # D4.4 x 2.7 then D4.2 out of the part: a wood screw's clearance (J1 cable post), not a lead-in
        res = run([cyl(4.4, 7.3, 10.0), cyl(4.2, 0.0, 7.3)], [], open_lo=True)
        self.assertEqual(res['counts']['pockets'], 0); self.assertEqual(codes(res), [])

    def test_old_blind_straight_pocket_is_a_pocket(self):
        res = run([cyl(4.4, 4.0, 10.0)], [])
        self.assertEqual(res['counts']['pockets'], 1); self.assertIn('relief_missing', codes(res))

    def test_screw_hole_is_no_pocket(self):
        res = run([cyl(3.4, 0.0, 10.0)], [], open_lo=True)
        self.assertEqual(res['counts']['pockets'], 0); self.assertEqual(codes(res), [])


class Insert(unittest.TestCase):
    def test_no_insert(self):
        res = run(pocket(), [])
        self.assertEqual(codes(res), ['no_insert']); self.assertIn('golden', res['findings'][0]['fix'])

    def test_old_solid_cylinder_is_not_golden(self):
        old = {'name': 'insert M3x4x5', 'path': 'HW:1/insert M3x4x5', 'host': 'demo', 'cylinders': [cyl(5.0, 10 - 0.54 - 4, 10 - 0.54, False)]}
        res = run(pocket(), [old])
        self.assertEqual(codes(res), ['not_golden']); self.assertIn('fix-inserts', res['findings'][0]['fix'])

    def test_not_flush(self):
        res = run(pocket(), [golden_insert(10.0)])
        self.assertEqual(codes(res), ['not_flush']); self.assertIn('0.54', res['findings'][0]['measured'])

    def test_upside_down(self):
        self.assertEqual(codes(run(pocket(), [golden_insert(10 - 0.54, pilot_down=False)])), ['upside_down'])

    def test_off_axis(self):
        self.assertEqual(codes(run(pocket(), [golden_insert(10 - 0.54, x=10.3)])), ['off_axis'])

    def test_insert_without_pocket(self):
        res = run([], [golden_insert(5.0)])
        self.assertEqual(codes(res), ['insert_without_pocket'])

    def test_insert_in_another_document_part_is_counted_not_judged(self):
        ins = golden_insert(5.0); ins['host'] = None
        res = run([], [ins])
        self.assertEqual(codes(res), []); self.assertEqual(res['counts']['outside_printed_parts'], 1)

    def test_every_finding_says_how_to_fix(self):
        for c in (pocket(relief=None), pocket(lead=None), pocket(hole=(3.4, 6.5)), pocket(hole=(3.6, 4.0))):
            for f in run(c, [])['findings']:
                for k in ('part', 'code', 'at', 'out', 'measured', 'expected', 'fix'):
                    self.assertTrue(f.get(k) is not None, (f['code'], k))


if __name__ == '__main__':
    unittest.main()
