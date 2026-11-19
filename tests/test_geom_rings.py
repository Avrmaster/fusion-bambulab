"""Ring, path and gap helpers of probes/_geom_rings.py on plain tuples, and the six Fusion probes that use them
(fusion_bearings, fusion_openings, fusion_screws, fusion_gap, fusion_thin, fusion_export) run as real unit scripts against
a small fake `adsk` made of boxes and tubes. The fake proves the control flow and the arithmetic of the probes, not the
Fusion API: every call it stands in for is listed in the probes' package notes as untested inside Fusion."""
import json, math, os, shutil, struct, sys, tempfile, time, types, unittest
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(ROOT, 'tools', 'gate')
sys.path.insert(0, GATE)
import gate_common, gate_fusion_rpc
from probes import _geom
from probes import _geom_rings as R


def table(name):
    with open(os.path.join(ROOT, 'rules', 'tables', name + '.json'), encoding='utf-8') as f: return json.load(f)


def box(lo, hi):
    return lambda p: all(lo[i] < p[i] < hi[i] for i in range(3))


def tube(cx, cy, z0, z1, r_out, r_in=0.0):
    return lambda p: z0 < p[2] < z1 and (not r_in or r_in < math.hypot(p[0] - cx, p[1] - cy)) and math.hypot(p[0] - cx, p[1] - cy) < r_out


def ring_circles(c, d, D, B):
    """The four circular edges of a plain ring along z."""
    return [((c[0], c[1], c[2] + s), (0, 0, 1), r) for s in (-B / 2.0, B / 2.0) for r in (d / 2.0, D / 2.0)]


Z = (0.0, 0.0, 1.0)


class Names(unittest.TestCase):
    def test_slug_is_the_slug_of_the_gate(self):
        for n in ('Motor plate (left)', 'ref  bearing 6005--2RS', '***', 'A/B c', 'Demo Assembly v12', ''):
            self.assertEqual(R.name_slug(n), gate_common.slug(n), n)

    def test_copy_numbers_are_dropped_but_sizes_stay(self):
        self.assertEqual(R.name_number_base('ref washer M3 (4)'), 'ref washer M3')
        self.assertEqual(R.name_number_base('Washer:2'), 'Washer')
        self.assertEqual(R.name_number_base('ref screw M3x12'), 'ref screw M3x12')
        self.assertEqual(R.name_number_base('6005'), '6005')

    def test_thread_and_designation_in_a_name(self):
        self.assertEqual(R.thread_in_name('ref screw M3x12 cap (3)'), ('M3', 12.0))
        self.assertEqual(R.thread_in_name('nut M4'), ('M4', None))
        self.assertEqual(R.thread_in_name('M2.5 x 8'), ('M2.5', 8.0))
        self.assertEqual(R.thread_in_name('Motor plate'), (None, None))
        sizes = table('bearings')['sizes']
        self.assertEqual(R.designation_in_name('ref bearing 6005ZZ upper', sizes), '6005')
        self.assertEqual(R.designation_in_name('608-2RS', sizes), '608')
        self.assertIsNone(R.designation_in_name('bearing 6080', sizes))

    def test_kind_and_motion_group(self):
        self.assertEqual(R.body_kind(True, False, own=True), 'self')
        self.assertEqual(R.body_kind(True, False, visible=False), 'hidden')
        self.assertEqual(R.body_kind(False, True), 'hardware')
        self.assertEqual(R.body_kind(False, False), 'bought')
        self.assertEqual(R.body_kind(False, False, mesh=True), 'mesh')
        import re
        groups = [{'key': 'base', 'patterns': ['^Frame']}, {'key': 'slide', 'patterns': ['^Slide', 'Pulley']}]
        self.assertEqual(R.group_of('Slide:1+Pulley:1', groups, re.search), 'slide')
        self.assertIsNone(R.group_of('Cover:1', groups, re.search))


class RingRecognition(unittest.TestCase):
    def test_a_ring_body_is_sized_and_matched_to_the_table(self):
        circles = ring_circles((10, 20, 30), 25, 47, 12) + [((14, 20, 30), (1, 0, 0), 3.0)]       # + one stray circle off the axis
        prof = R.revolve_profile(circles)
        self.assertEqual((prof['n'], prof['of']), (4, 5))
        size = R.bearing_size(prof)
        self.assertEqual((size['d'], size['D'], size['B']), (25.0, 47.0, 12.0))
        self.assertEqual(tuple(round(c, 6) for c in size['centre']), (10.0, 20.0, 30.0))
        self.assertEqual(R.match_bearing(size['d'], size['D'], size['B'], table('bearings')), '6005')

    def test_a_ring_of_no_table_size_and_a_solid_disc_are_not_matched(self):
        size = R.bearing_size(R.revolve_profile(ring_circles((0, 0, 0), 25, 47.4, 12)))
        self.assertIsNone(R.match_bearing(size['d'], size['D'], size['B'], table('bearings')))
        self.assertIsNone(R.bearing_size(R.revolve_profile([((0, 0, 0), Z, 5.0), ((0, 0, 3), Z, 5.0)])))     # no bore
        self.assertIsNone(R.revolve_profile([]))

    def test_split_faces_and_a_turned_axis_give_the_same_ring(self):
        a = _geom.v_unit((1.0, 1.0, 0.0))
        circles = []
        for s in (-3.5, 3.5):
            c = _geom.v_scale(a, s)
            for r in (4.0, 11.0):
                circles += [(c, a, r), (c, _geom.v_scale(a, -1.0), r)]                               # two half faces: two arcs each
        size = R.bearing_size(R.revolve_profile(circles))
        self.assertEqual((round(size['d'], 6), round(size['D'], 6), round(size['B'], 6)), (8.0, 22.0, 7.0))
        self.assertEqual(R.match_bearing(size['d'], size['D'], size['B'], table('bearings')), '608')

    def test_two_rings_of_one_bearing_are_one_part_two_bearings_side_by_side_are_two(self):
        inner, outer = ((0, 0, 0), Z, 0.0, 7.0), ((0, 0, 0), Z, 0.0, 7.0)
        nxt = ((0, 0, 7.0), Z, 0.0, 7.0)                                                              # touching, same axis
        self.assertEqual(R.cluster_coaxial([inner, outer, nxt], -0.05), [[0, 1], [2]])
        head, shank, far = ((0, 0, 10), Z, 0.0, 3.0), ((0, 0, 0), Z, 0.0, 10.0), ((0, 0, -20), Z, 0.0, 5.0)
        self.assertEqual(R.cluster_coaxial([head, shank, far], 0.2), [[0, 1], [2]])
        self.assertEqual(R.cluster_coaxial([head, ((5, 0, 10), Z, 0.0, 3.0)], 0.2), [[0], [1]])      # another axis

    def test_screw_shapes(self):
        cap = [((0, 0, 0), Z, 1.2), ((0, 0, 0.3), Z, 1.5), ((0, 0, 12), Z, 1.5), ((0, 0, 12), Z, 2.75), ((0, 0, 15), Z, 2.75)]
        sh = R.screw_shape(R.revolve_profile(cap))
        self.assertEqual((sh['axis'], sh['tip'], sh['seat'], sh['top']), ((0.0, 0.0, 1.0), (0.0, 0.0, 0.0), (0.0, 0.0, 12.0), (0.0, 0.0, 15.0)))
        self.assertEqual((sh['head_r'], sh['shank_r'], sh['head_h'], sh['length'], sh['countersunk'], sh['headless']), (2.75, 1.5, 3.0, 12.0, False, False))
        self.assertEqual(R.screw_size(sh, table('screws')), ('M3', 'cap'))
        sunk = [((0, 0, 10), Z, 1.5), ((0, 0, 1.5), Z, 1.5), ((0, 0, 0), Z, 3.0)]                    # head down
        sh = R.screw_shape(R.revolve_profile(sunk))
        self.assertEqual((sh['tip'], sh['top'], sh['length'], sh['countersunk']), ((0.0, 0.0, 10.0), (0.0, 0.0, 0.0), 10.0, True))
        self.assertEqual(tuple(round(c, 6) + 0.0 for c in sh['axis']), (0.0, 0.0, -1.0))
        self.assertEqual(R.screw_size(sh, table('screws')), ('M3', 'countersunk'))
        grub = R.screw_shape(R.revolve_profile([((0, 0, 0), Z, 2.0), ((0, 0, 6), Z, 2.0)]))
        self.assertTrue(grub['headless']); self.assertEqual(R.screw_size(grub, table('screws')), ('M4', None))
        self.assertEqual(R.screw_size({'shank_r': 7.0, 'head_r': 9.0, 'head_h': 5.0, 'countersunk': False, 'headless': False}, table('screws')), (None, None))

    def test_washer_shape(self):
        w = R.washer_shape(R.revolve_profile(ring_circles((5, 5, 0.4), 3.2, 9.0, 0.8)))
        self.assertEqual((round(w['od'], 6), round(w['id'], 6), round(w['t'], 6)), (9.0, 3.2, 0.8))
        self.assertIsNone(R.washer_shape(R.revolve_profile(ring_circles((0, 0, 0), 25, 47, 30))))    # a sleeve, not a washer
        self.assertEqual(R.washer_row(w, table('washers')), ('M3', 'large'))
        wide = R.washer_shape(R.revolve_profile(ring_circles((0, 0, 0), 3.2, 11.7, 1.5)))             # the measured outer diameter of a retail washer
        self.assertEqual(R.washer_row(wide, table('washers')), ('M3', 'wide_thick'))
        self.assertIsNone(R.washer_row(R.washer_shape(R.revolve_profile(ring_circles((0, 0, 0), 8.0, 22.0, 7.0))), table('washers')))     # a 608 is no washer

    def test_a_name_picks_a_screw_and_the_geometry_decides(self):
        t = table('screws')
        cap = R.screw_shape(R.revolve_profile([((0, 0, 0), Z, 1.5), ((0, 0, 12), Z, 1.5), ((0, 0, 12), Z, 2.75), ((0, 0, 15), Z, 2.75)]))
        disc = R.screw_shape(R.revolve_profile([((0, 0, 0), Z, 3.0), ((0, 0, 2.5), Z, 3.0)]))        # a D6 x 2.5 magnet
        grub = R.screw_shape(R.revolve_profile([((0, 0, 0), Z, 1.5), ((0, 0, 4), Z, 1.5)]))
        thick = R.screw_shape(R.revolve_profile([((0, 0, 0), Z, 3.0), ((0, 0, 12), Z, 3.0), ((0, 0, 12), Z, 5.0), ((0, 0, 18), Z, 5.0)]))
        self.assertEqual(R.screw_claim(cap, t, 'M3', True), (True, None, 'M3', 'cap'))
        self.assertEqual(R.screw_claim(cap, t, None, False), (True, None, 'M3', 'cap'))               # picked by its component: a head on a table shank
        ok, why, size, _ = R.screw_claim(disc, t, None, False)                                        # a plain cylinder in a hardware component
        self.assertEqual((ok, size), (False, 'M6')); self.assertIn('plain cylinder D6 x 2.5', why)
        self.assertEqual(R.screw_claim(grub, t, 'M3', True)[:3], (True, None, 'M3'))                  # a set screw by its own name
        self.assertFalse(R.screw_claim(grub, t, None, False)[0])                                      # the same pin in a component named so: not proven
        ok, why, _, _ = R.screw_claim(thick, t, 'M3', True)                                           # named M3, measures M6
        self.assertFalse(ok); self.assertIn('its name says M3, its shank measures D6', why)
        self.assertEqual(R.screw_claim(None, t, 'M3', True), (True, None, 'M3', None))                # no shape, its own name: listed with "why", never dropped
        self.assertFalse(R.screw_claim(None, t, None, False)[0])
        rod = R.screw_shape(R.revolve_profile([((0, 0, 0), Z, 4.0), ((0, 0, 60), Z, 4.0)]))           # M8: no row of the table
        self.assertEqual(R.screw_claim(rod, t, 'M8', True)[:3], (True, None, 'M8')); self.assertFalse(R.screw_claim(rod, t, None, False)[0])

    def test_a_name_picks_a_bearing_and_the_geometry_decides(self):
        t = table('bearings')
        size = lambda d, D, B: R.bearing_size(R.revolve_profile(ring_circles((0, 0, 0), d, D, B)))
        self.assertEqual(R.bearing_claim(size(8, 22, 7), None, t), ('608', 'geometry', None))         # a ring of the table, whatever it is called
        self.assertEqual(R.bearing_claim(size(8, 22, 7), '6005', t)[:2], ('608', 'geometry'))
        des, by, why = R.bearing_claim(size(3, 12, 8), '608', t)                                      # a clamp screw named after the bearing it clamps
        self.assertEqual((des, by), (None, None)); self.assertEqual(why, 'its name says 608 (8 x 22 x 7), it measures 3 x 12 x 8')
        self.assertEqual(R.bearing_claim(size(9, 23, 7), None, t), (None, None, None))                # a ring of no row and no name
        self.assertEqual(R.bearing_claim(None, '608', t), ('608', 'name', None))                      # no ring geometry: the name is all there is
        self.assertEqual(R.bearing_claim(None, None, t), (None, None, None))


class RingFaces(unittest.TestCase):
    def test_the_four_annuli(self):
        self.assertEqual(R.ring_annulus(25, 47, 'inner'), (12.5, 15.25))
        self.assertEqual(R.ring_annulus(25, 47, 'outer'), (20.75, 23.5))
        self.assertEqual(R.ring_annulus(25, 47, 'outer', 0.5), (18.0, 23.5))
        with self.assertRaises(ValueError): R.ring_annulus(25, 47, 'middle')
        self.assertEqual(R.ring_face_centre((1, 2, 3), Z, 12, '+'), (1.0, 2.0, 9.0))
        self.assertEqual(R.ring_face_centre((1, 2, 3), Z, 12, '-'), (1.0, 2.0, -3.0))

    def test_annulus_sampling(self):
        pts = R.annulus_points((0, 0, 5), Z, 20.75, 23.5, 24, 2)
        self.assertEqual(len(pts), 48)
        self.assertEqual([(k, j) for _, k, j in pts[:3]], [(0, 0), (1, 0), (2, 0)]); self.assertEqual(pts[24][1:], (0, 1))
        radii = sorted(set(round(math.hypot(p[0], p[1]), 6) for p, _, _ in pts))
        self.assertEqual(radii, [round(20.75 + 2.75 / 3, 6), round(20.75 + 2 * 2.75 / 3, 6)])          # never on an edge
        self.assertTrue(all(abs(p[2] - 5) < 1e-9 for p, _, _ in pts))
        tilted = R.annulus_points((0, 0, 0), _geom.v_unit((1, 1, 1)), 3, 5, 8, 1)
        self.assertTrue(all(abs(_geom.v_dot(p, _geom.v_unit((1, 1, 1)))) < 1e-9 and abs(_geom.v_len(p) - 4) < 1e-9 for p, _, _ in tilted))

    def test_arc_runs(self):
        self.assertEqual(R.arc_runs([True] * 24), (1.0, 1))
        self.assertEqual(R.arc_runs([False] * 5), (0.0, 0))
        self.assertEqual(R.arc_runs([]), (0.0, 0))
        six = [(k % 4) < 2 for k in range(24)]                                                         # six washers
        self.assertEqual(R.arc_runs(six), (0.5, 6))
        self.assertEqual(R.arc_runs([True, False, False, True]), (0.5, 1))                             # one run across the seam

    def cover(self, body, ring='outer', side='+', touch=0.05, d=25.0, D=47.0, B=12.0):
        fc = R.ring_face_centre((0, 0, 6), Z, B, side)
        r_in, r_out = R.ring_annulus(d, D, ring)
        return R.face_cover(R.annulus_points(fc, Z, r_in, r_out, 24, 2), body, Z if side == '+' else (0, 0, -1), R.cover_offsets(touch, 2.0), touch)

    def test_a_lip_that_touches_one_ring(self):
        lip = tube(0, 0, 12.0, 14.0, 23.4, 21.0)
        got = self.cover(lip)
        self.assertEqual((got['share'], got['points'], got['gap']), (1.0, 1, 0.0))
        self.assertIsNone(self.cover(lip, 'inner'))
        self.assertIsNone(self.cover(lip, side='-'))

    def test_a_clamp_that_touches_both_rings(self):
        plate = tube(0, 0, 12.0, 14.0, 30.0)                                                           # a flat plate over the whole bearing
        outer, inner = self.cover(plate, 'outer'), self.cover(plate, 'inner')
        self.assertEqual((outer['share'], inner['share']), (1.0, 1.0))
        self.assertGreaterEqual(sum(inner['flags']), 2)                                                # what the probe calls touches_other_ring

    def test_a_cover_that_stops_short_of_the_face_holds_nothing_but_is_seen(self):
        short = tube(0, 0, 12.3, 14.0, 23.4, 21.0)                                                     # 0.3 above the ring
        got = self.cover(short)
        self.assertEqual((got['share'], got['points']), (0.0, 0))
        self.assertAlmostEqual(got['gap'], 0.35, delta=0.051)                                          # seen on the 0.1 mm ladder
        self.assertEqual(got['near'], 1.0)
        self.assertIsNone(self.cover(tube(0, 0, 14.5, 16.0, 23.4, 21.0)))                              # 2.5 away: not listed

    def test_six_washers_are_six_contact_runs(self):
        centres = [(24.0 * math.cos(math.radians(60 * k)), 24.0 * math.sin(math.radians(60 * k))) for k in range(6)]
        washers = lambda p: any(tube(cx, cy, 12.0, 12.8, 7.0, 1.6)(p) for cx, cy in centres)
        got = self.cover(washers)
        self.assertEqual((got['points'], got['share']), (6, 0.625))                                    # 5 of the 8 points of each sixth

    def test_a_clamp_drawn_into_the_ring(self):
        deep = tube(0, 0, 11.0, 14.0, 23.4, 21.0)                                                      # reaches 1.0 into the ring
        fc = R.ring_face_centre((0, 0, 6), Z, 12, '+')
        pts = R.annulus_points(fc, Z, *R.ring_annulus(25, 47, 'outer'))
        self.assertEqual(R.face_overlap(pts, deep, Z), 0.75)                                           # the deepest step short of 1.0
        self.assertEqual(R.face_overlap(pts, tube(0, 0, 12.0, 14.0, 23.4, 21.0), Z), 0.0)

    def test_clamp_room_arithmetic(self):
        self.assertEqual(R.clamp_room(10.0, 1.0), 9.0)                    # the clamp lands 1.0 after it meets the ring
        self.assertEqual(R.clamp_room(10.0, 0.3), 9.7)                    # a landing 0.3 behind the ring face: too little
        self.assertEqual(R.clamp_room(10.0, 0.0), 10.0)                   # flange and ring meet together: nothing is clamped
        self.assertEqual(R.clamp_room(10.0, 1.0, gap_stop=0.2), 9.2)      # the ring floats 0.2 over its seat floor
        self.assertEqual(R.clamp_room(10.0, 0.0, gap_clamp=-1.0), 9.0)    # drawn with 1.0 of preload, flange seated
        self.assertEqual(R.least_travel([None, (1.0, 'Housing'), (2.5, 'Rib'), None], 3.0), (1.0, 'Housing', False))
        self.assertEqual(R.least_travel([None, None], 3.0), (3.0, None, True))
        self.assertEqual(R.least_travel([(3.4, 'Far')], 3.0), (3.0, None, True))


class Lines(unittest.TestCase):
    def test_runs_and_the_gap_between_two_bodies(self):
        owner = lambda s: 'plate' if 0 <= s <= 5 else ('base' if 5.35 <= s <= 9 else None)
        runs = R.runs_along(owner, 0.0, 12.0, 0.25, 0.01)
        self.assertEqual([n for n, _, _ in runs], ['plate', 'base'])
        self.assertAlmostEqual(runs[0][2], 5.0, delta=0.011); self.assertAlmostEqual(runs[1][1], 5.35, delta=0.011)
        gap, a, b, at = R.run_gaps(runs)[0]
        self.assertAlmostEqual(gap, 0.35, delta=0.02); self.assertEqual((a, b), ('plate', 'base'))
        tight = lambda s: 'plate' if s <= 5 else 'base'                                # two bodies face to face
        self.assertAlmostEqual(R.run_gaps(R.runs_along(tight, 0.0, 8.0, 0.25, 0.01))[0][0], 0.0, delta=0.02)
        self.assertEqual(R.runs_along(lambda s: None, 0.0, 3.0), [])
        with self.assertRaises(ValueError): R.runs_along(owner, 2.0, 1.0)

    def test_first_hit(self):
        self.assertEqual(R.first_hit(lambda s: 'ring', 0.03, 3.03), ('ring', 0.03))
        name, s = R.first_hit(lambda s: 'housing' if s >= 1.0 else None, 0.03, 3.03, 0.5, 0.02)
        self.assertEqual(name, 'housing'); self.assertAlmostEqual(s, 1.0, delta=0.021)
        self.assertIsNone(R.first_hit(lambda s: None, 0.03, 3.03))


class Seats(unittest.TestCase):
    def test_an_outer_seat_with_a_floor_and_an_open_side(self):
        housing = lambda p: box((-40, -40, -5), (40, 40, 12))(p) and not tube(0, 0, 0, 13, 23.5)(p) and not tube(0, 0, -6, 0.001, 20)(p)
        s = R.seat_scan(housing, (0, 0, 6), Z, 23.5, 12.0, outer=True)
        self.assertAlmostEqual(s['d'], 47.0, delta=0.03); self.assertEqual(s['len'], 12.0)
        self.assertEqual((s['entry_side'], s['entry_open']), ('+', True))
        self.assertEqual(s['entry_d'], 49.0)                                         # open: the edge of the window
        self.assertEqual(s['entry_other'], 41.0)                                     # the floor: 3 mm into the ring's way, or more

    def test_a_lip_narrower_than_the_ring_on_the_way_in(self):
        lid = lambda p: tube(0, 0, -2, 20, 40, 23.5)(p) or tube(0, 0, 12, 14, 24, 22.9)(p) or tube(0, 0, -2, 0, 24, 22.0)(p)
        s = R.seat_scan(lid, (0, 0, 6), Z, 23.5, 12.0, outer=True)
        self.assertAlmostEqual(s['d'], 47.0, delta=0.03)
        self.assertEqual(s['entry_side'], '+'); self.assertFalse(s['entry_open'])
        self.assertAlmostEqual(s['entry_d'], 45.8, delta=0.03)                       # 1.2 under the ring: it cannot pass
        self.assertAlmostEqual(s['entry_other'], 44.0, delta=0.03)

    def test_a_tapered_seat_and_a_seat_of_the_wrong_size(self):
        cone = lambda p: -1 < p[2] < 13 and 23.5 + 0.125 * max(0.0, p[2] - 6) / 6.0 < math.hypot(p[0], p[1]) < 40
        s = R.seat_scan(cone, (0, 0, 6), Z, 23.5, 12.0, outer=True)
        self.assertAlmostEqual(s['d'], 47.0, delta=0.06)
        loose = R.seat_scan(tube(0, 0, -1, 13, 40, 23.9), (0, 0, 6), Z, 23.5, 12.0, outer=True)
        self.assertAlmostEqual(loose['d'], 47.8, delta=0.03)                         # listed with its real size: the judge decides
        self.assertIsNone(R.seat_scan(tube(0, 0, -1, 13, 40, 26.0), (0, 0, 6), Z, 23.5, 12.0, outer=True))   # no seat at all

    def test_an_inner_seat_on_a_post_with_a_shoulder(self):
        post = lambda p: tube(0, 0, -5, 12, 12.5)(p) or tube(0, 0, -5, 0, 16.0)(p)
        s = R.seat_scan(post, (0, 0, 6), Z, 12.5, 12.0, outer=False)
        self.assertAlmostEqual(s['d'], 25.0, delta=0.03)
        self.assertEqual((s['entry_side'], s['entry_open']), ('+', True))
        self.assertAlmostEqual(s['entry_other'], 31.0, delta=0.03)                   # the shoulder, 3 mm wider than the bore
        self.assertIsNone(R.seat_scan(tube(0, 0, -5, 12, 10.0), (0, 0, 6), Z, 12.5, 12.0, outer=False))

    def test_a_census_seat_with_and_without_its_bearing(self):
        self.assertTrue(R.seat_has_bearing((0, 0, 12), Z, 12.0, [((0, 0, 6), (0, 0, -1))]))
        self.assertFalse(R.seat_has_bearing((0, 0, 12), Z, 12.0, [((30, 0, 6), Z)]))
        self.assertFalse(R.seat_has_bearing((0, 0, 12), Z, 12.0, [((0, 0, 40), Z)]))
        self.assertFalse(R.seat_has_bearing((0, 0, 12), Z, 12.0, []))


class Paths(unittest.TestCase):
    def test_path_points(self):
        pts = R.path_points((1, 2, 3), Z, 2.0, 5.0, 2.0, 8)
        self.assertEqual(sorted(set(s for _, s in pts)), [0.0, 2.0, 4.0, 5.0])
        self.assertEqual(len(pts), 4 * 9)
        self.assertEqual(pts[0], ((1.0, 2.0, 3.0), 0.0))
        self.assertTrue(all(abs(math.hypot(p[0] - 1, p[1] - 2) - 1.8) < 1e-9 for p, s in pts[1:9]))

    def test_the_path_test_on_boxes(self):
        P, r, reach = (10.0, 10.0, 10.0), 1.7, 60.0
        self.assertTrue(R.box_overlaps_path((-5, -5, 22.5), (45, 45, 24), P, Z, r, reach))            # a cover over the hole
        self.assertFalse(R.box_overlaps_path((-5, -5, 80), (45, 45, 82), P, Z, r, reach))             # beyond the reach
        self.assertFalse(R.box_overlaps_path((-5, -5, 0), (45, 45, 9.9), P, Z, r, reach))             # behind the mouth
        self.assertFalse(R.box_overlaps_path((12, 12, 20), (30, 30, 30), P, Z, r, reach))             # beside the path
        self.assertTrue(R.box_overlaps_path((11, 9, 20), (30, 30, 30), P, Z, r, reach))               # a ledge over its edge
        a = _geom.v_unit((1.0, 0.0, 1.0))
        self.assertTrue(R.box_overlaps_path((19, -1, 19), (21, 1, 21), (0, 0, 0), a, 1.0, 40.0))      # on a slanted path
        self.assertFalse(R.box_overlaps_path((19, -1, 0), (21, 1, 2), (0, 0, 0), a, 1.0, 40.0))
        lo, hi = R.path_box(P, Z, r, reach)
        self.assertEqual((lo, hi), ((8.3, 8.3, 10.0), (11.7, 11.7, 70.0)))
        self.assertEqual(R.box_span((-5, -5, 22.5), (45, 45, 24), P, Z), (12.5, 14.0))

    def test_points_in_the_path_and_the_blocked_share(self):
        pts = [(10, 10, 25), (10, 11.5, 12), (10, 12, 12), (10, 10, 5), (10, 10, 71)]
        self.assertEqual([(round(s, 6), round(off, 6), i) for s, off, i in R.points_in_path(pts, (10, 10, 10), Z, 1.7, 60.0)], [(2.0, 1.5, 1), (15.0, 0.0, 0)])
        self.assertEqual(R.section_share(4, 16), 0.25); self.assertEqual(R.section_share(0, 0), 0.0)
        cover = box((-5, -5, 22.5), (45, 45, 24))
        self.assertEqual(R.path_share(cover, (10, 10, 10), Z, 1.7, 12.5, 14.0), 1.0)
        ledge = box((10.0, -5, 22.5), (45, 45, 24))                                                    # covers half the hole
        self.assertTrue(0.3 < R.path_share(ledge, (10, 10, 10), Z, 1.7, 12.5, 14.0) < 0.7)
        self.assertEqual(R.path_share(cover, (10, 10, 10), Z, 1.7, 30.0, 40.0), 0.0)

    def test_free_length(self):
        hits = [{'at': 0.05, 'expected': True}, {'at': 12.5, 'expected': False}, {'at': 30.0, 'expected': False}]
        self.assertEqual(R.path_free(hits, 60.0), 12.5)
        self.assertEqual(R.path_free(hits[:1], 60.0), 60.0); self.assertEqual(R.path_free([], 60.0), 60.0)

    def test_a_mesh_in_the_path(self):
        nodes = [(5, 5, 15), (15, 5, 15), (15, 15, 15), (5, 15, 15)]                                   # one big flat quad: no vertex in the path
        tris = [(0, 1, 2), (0, 2, 3)]
        self.assertEqual(R.mesh_blocks_path(nodes, tris, (10, 10, 10), Z, 1.7, 60.0), (5.0, 1.0))
        self.assertIsNone(R.mesh_blocks_path(nodes, tris, (30, 30, 10), Z, 1.7, 60.0))
        self.assertIsNone(R.mesh_blocks_path(nodes, tris, (10, 10, 16), Z, 1.7, 60.0))                 # the path starts behind it
        fine = [(10 + 0.5 * i, 10 + 0.5 * j, 18.0) for i in range(-6, 7) for j in range(-6, 7)]        # a dense mesh: vertices inside
        at, share = R.mesh_blocks_path(fine, [], (10, 10, 10), Z, 1.7, 60.0)
        self.assertEqual(at, 8.0); self.assertGreaterEqual(share, 0.5)

    def test_coaxial(self):
        self.assertTrue(R.coaxial((0, 0, 0), Z, (0.05, 0, 30), (0, 0, -1)))
        self.assertFalse(R.coaxial((0, 0, 0), Z, (0.5, 0, 30), Z))
        self.assertFalse(R.coaxial((0, 0, 0), Z, (0, 0, 30), _geom.v_unit((0, 0.05, 1))))


class Gaps(unittest.TestCase):
    def test_box_gap(self):
        self.assertEqual(R.box_gap((0, 0, 0), (1, 1, 1), (3, 0, 0), (4, 1, 1)), 2.0)
        self.assertEqual(R.box_gap((0, 0, 0), (1, 1, 1), (1, 0, 0), (4, 1, 1)), 0.0)                 # touching
        self.assertEqual(R.box_gap((0, 0, 0), (2, 2, 2), (1, 1, 1), (4, 4, 4)), 0.0)                 # overlapping
        self.assertEqual(R.box_gap((0, 0, 0), (1, 1, 1), (4, 5, 0), (5, 6, 1)), 5.0)                 # across a corner: 3, 4
        self.assertEqual(R.box_of(R.box_corners((1, 2, 3), (4, 5, 6))), ((1, 2, 3), (4, 5, 6)))
        self.assertEqual(R.box_nearest((0, 0, 0), (1, 1, 1), (5, 0.5, -2)), (1, 0.5, 0))

    def test_a_cover_is_asked_once_and_counted_twice(self):
        """face_cover() = face_first() + face_cover_of(). The cover of the other ring with two offsets is the first list cut
        to two offsets, so the points of a ring face are asked once for both questions."""
        import random
        rnd = random.Random(11)
        offsets = R.cover_offsets(0.05, 2.0)
        self.assertEqual((len(offsets), offsets[:3], offsets[-1]), (21, [0.02, 0.07, 0.17], 1.97))
        for case in range(40):
            z0 = rnd.choice([0.0, 0.03, 0.06, 0.5, 1.5, 2.5])                      # where the body begins above the face
            r_in, r_out = sorted((rnd.uniform(0.0, 30.0), rnd.uniform(5.0, 40.0)))
            a0, a1 = sorted((rnd.uniform(0.0, 6.3), rnd.uniform(0.0, 6.3)))
            def inside(p, z0=z0, r_in=r_in, r_out=r_out, a0=a0, a1=a1):
                return p[2] > z0 and r_in < math.hypot(p[0], p[1]) < r_out and a0 <= math.atan2(p[1], p[0]) % (2 * math.pi) <= a1
            pts = R.annulus_points((0.0, 0.0, 0.0), Z, 12.5, 15.0, 36, 2)
            first = R.face_first(pts, inside, Z, offsets)
            self.assertEqual(len(first), len(pts)); self.assertTrue(all(i is None or 0 <= i < 21 for i in first))
            self.assertEqual(R.face_cover_of(pts, first, offsets, 0.05), R.face_cover(pts, inside, Z, offsets, 0.05), case)
            self.assertEqual(R.face_cover_of(pts, R.face_first_cut(first, 2), offsets[:2], 0.05), R.face_cover(pts, inside, Z, offsets[:2], 0.05), case)
            asked = []
            R.face_first(pts, lambda p: (asked.append(p), inside(p))[1], Z, offsets)
            self.assertEqual(len(asked), sum(21 if i is None else i + 1 for i in first))       # 21 questions for a point with nothing in front of it
        self.assertEqual(R.face_first_cut([0, 1, 2, None, 20], 2), [0, 1, None, None, None])

    def test_the_slab_in_front_of_a_face_and_the_box_of_a_seat_say_no_without_a_point(self):
        import random
        rnd = random.Random(3)
        offsets = R.cover_offsets(0.05, 2.0)
        pts = R.annulus_points((0.0, 0.0, 10.0), Z, 40.0, 42.5, 105, 2)
        lo, hi = R.slab_box(pts, Z, offsets)
        self.assertAlmostEqual(lo[2], 10.02); self.assertAlmostEqual(hi[2], 11.97); self.assertLess(hi[0], 42.5); self.assertGreater(lo[0], -42.5)
        for _ in range(300):                                                       # a box that does not reach the slab holds none of its points
            c = [rnd.uniform(-60, 60), rnd.uniform(-60, 60), rnd.uniform(0, 20)]
            h = [rnd.uniform(0.1, 30), rnd.uniform(0.1, 30), rnd.uniform(0.1, 6)]
            blo, bhi = tuple(c[i] - h[i] for i in range(3)), tuple(c[i] + h[i] for i in range(3))
            if R.box_gap(blo, bhi, lo, hi) > 0.0:
                box_has = lambda p: all(blo[i] <= p[i] <= bhi[i] for i in range(3))
                self.assertEqual(R.face_first(pts, box_has, Z, offsets), [None] * len(pts))
        said_no = 0
        for _ in range(400):                                                       # seat_possible() False: seat_scan() gives None for every body in that box
            outer = rnd.random() < 0.5
            r_nom, width = rnd.choice([4.0, 12.5, 23.5, 50.0]), rnd.choice([7.0, 12.0])
            c = [rnd.uniform(-1.5 * r_nom, 1.5 * r_nom), rnd.uniform(-1.5 * r_nom, 1.5 * r_nom), rnd.uniform(-10, 10)]
            h = [rnd.uniform(0.5, 2.5 * r_nom), rnd.uniform(0.5, 2.5 * r_nom), rnd.uniform(0.5, 20)]
            blo, bhi = tuple(c[i] - h[i] for i in range(3)), tuple(c[i] + h[i] for i in range(3))
            solid = (lambda p: math.hypot(p[0], p[1]) > r_nom) if outer else (lambda p: math.hypot(p[0], p[1]) < r_nom)      # a perfect seat, cut to the box
            inside = lambda p: all(blo[i] <= p[i] <= bhi[i] for i in range(3)) and solid(p)
            scan = R.seat_scan(inside, (0.0, 0.0, 0.0), Z, r_nom, width, outer=outer)
            if not R.seat_possible(blo, bhi, (0.0, 0.0, 0.0), Z, r_nom, outer=outer):
                said_no += 1
                self.assertIsNone(scan)
            elif all(blo[i] < -1.4 * r_nom and bhi[i] > 1.4 * r_nom for i in range(2)) and blo[2] < -width and bhi[2] > width:
                self.assertIsNotNone(scan)                                         # the box holds the whole ring: the seat is found
        self.assertGreater(said_no, 100)

    def test_the_gap_list_keeps_the_least_per_neighbour(self):
        rows = [{'name': 'Pulley', 'group': 'slide', 'gap': 0.8}, {'name': 'Pulley', 'group': 'slide', 'gap': 0.5},
                {'name': 'Belt', 'group': 'slide', 'gap': 0.2}, {'name': 'Far', 'group': 'slide', 'gap': 9.0}]
        self.assertEqual([(r['name'], r['gap']) for r in R.least_gaps(rows, 5.0)], [('Belt', 0.2), ('Pulley', 0.5)])


# ---- a fake adsk of boxes and tubes ----------------------------------------------------------------------------------------------

class Pt:
    def __init__(self, x, y, z): self.x, self.y, self.z = x, y, z
    def asArray(self): return (self.x, self.y, self.z)
    def distanceTo(self, o): return math.sqrt((self.x - o.x) ** 2 + (self.y - o.y) ** 2 + (self.z - o.z) ** 2)


def cm(p): return Pt(p[0] / 10.0, p[1] / 10.0, p[2] / 10.0)
def mm(p): return (p.x * 10.0, p.y * 10.0, p.z * 10.0)


class L(list):
    count = property(len)
    def item(self, i): return self[i]


class Blk:
    """A box, mm."""
    def __init__(self, lo, hi): self.lo, self.hi = tuple(map(float, lo)), tuple(map(float, hi))
    def has(self, p): return all(self.lo[i] < p[i] < self.hi[i] for i in range(3))
    def box(self): return self.lo, self.hi
    def circles(self): return []
    def cyls(self): return []
    def planes(self):
        out = []
        for k in range(3):
            for side, sgn in ((self.lo, -1.0), (self.hi, 1.0)):
                c = [(self.lo[i] + self.hi[i]) / 2.0 for i in range(3)]; c[k] = side[k]
                lo, hi = list(self.lo), list(self.hi); lo[k] = hi[k] = side[k]
                out.append((tuple(c), tuple(sgn if i == k else 0.0 for i in range(3)), (tuple(lo), tuple(hi))))
        return out


class Rod:
    """A cylinder or tube along z, mm."""
    def __init__(self, cx, cy, z0, z1, r_out, r_in=0.0): self.cx, self.cy, self.z0, self.z1, self.r, self.ri = cx, cy, z0, z1, r_out, r_in
    def has(self, p):
        h = math.hypot(p[0] - self.cx, p[1] - self.cy)
        return self.z0 < p[2] < self.z1 and (not self.ri or self.ri < h) and h < self.r
    def box(self): return (self.cx - self.r, self.cy - self.r, self.z0), (self.cx + self.r, self.cy + self.r, self.z1)
    def circles(self): return [((self.cx, self.cy, z), Z, r) for z in (self.z0, self.z1) for r in ((self.ri, self.r) if self.ri else (self.r,))]
    def cyls(self): return [((self.cx, self.cy, self.z0), Z, r) for r in ((self.ri, self.r) if self.ri else (self.r,))]
    def planes(self):
        mid = (self.cx + (self.ri + self.r) / 2.0, self.cy)
        return [((mid[0], mid[1], z), (0.0, 0.0, sgn), ((self.cx - self.r, self.cy - self.r, z), (self.cx + self.r, self.cy + self.r, z)))
                for z, sgn in ((self.z0, -1.0), (self.z1, 1.0))]


class Add:
    def __init__(self, *parts): self.parts = parts
    def has(self, p): return any(s.has(p) for s in self.parts)
    def box(self): return R.box_of([c for s in self.parts for c in s.box()])
    def circles(self): return [c for s in self.parts for c in s.circles()]
    def cyls(self): return [c for s in self.parts for c in s.cyls()]
    def planes(self): return [c for s in self.parts for c in s.planes()]


class Sub:
    """A solid with holes cut out: the holes give their cylinders and circles, not their end planes."""
    def __init__(self, solid, *holes): self.solid, self.holes = solid, holes
    def has(self, p): return self.solid.has(p) and not any(h.has(p) for h in self.holes)
    def box(self): return self.solid.box()
    def circles(self): return self.solid.circles() + [c for h in self.holes for c in h.circles()]
    def cyls(self): return self.solid.cyls() + [c for h in self.holes for c in h.cyls()]
    def planes(self): return self.solid.planes()


class Seg:
    """The temporary cylinder of TemporaryBRepManager.createCylinderOrCone: any direction; after a boolean, a cloud of the
    sample points that are left."""
    def __init__(self, p0, p1, r):
        self.p0, self.p1, self.r = p0, p1, r
        self.axis = _geom.v_unit(_geom.v_sub(p1, p0)); self.len = _geom.v_dist(p0, p1)
        u, v = _geom.axis_frame(self.axis)
        n = max(1, int(round(self.len / 0.25)))
        self.pts = []
        for i in range(n):
            c = _geom.v_add(p0, _geom.v_scale(self.axis, self.len * (i + 0.5) / n))
            self.pts.append(c)
            for f in (0.5, 0.9):
                for k in range(8):
                    th = math.pi * k / 4.0
                    self.pts.append(_geom.v_add(c, _geom.v_add(_geom.v_scale(u, f * r * math.cos(th)), _geom.v_scale(v, f * r * math.sin(th)))))
        self.all = len(self.pts)
    def has(self, p): return False
    def vol(self): return math.pi * self.r * self.r * self.len * len(self.pts) / self.all
    def box(self):
        if not self.pts: return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        if len(self.pts) == self.all: return R.path_box(self.p0, self.axis, self.r, self.len)
        lo, hi = R.box_of(self.pts)
        along = [_geom.v_dot(_geom.v_sub(p, self.p0), self.axis) for p in self.pts]                   # the kept slab, with half a step each way
        step = self.len / max(1, int(round(self.len / 0.25))) / 2.0
        a, b = _geom.v_add(self.p0, _geom.v_scale(self.axis, min(along) - step)), _geom.v_add(self.p0, _geom.v_scale(self.axis, max(along) + step))
        return R.box_of([lo, hi, a, b])
    def circles(self): return []
    def cyls(self): return []
    def planes(self): return []


class Body:
    isSolid = True
    def __init__(self, name, shape, bulb=True, volume=1.0, faces=None, mesher=None):
        self.name, self.shape, self.isLightBulbOn, self._volume, self._faces, self._mesher = name, shape, bulb, volume, faces, mesher
        self.area = 6.0
        self.vertices = L([1, 2])
        self.lumps, self.shells = L([1]), L([1])
    @property
    def volume(self): return self.shape.vol() / 1000.0 if isinstance(self.shape, Seg) else self._volume
    @property
    def boundingBox(self):
        lo, hi = self.shape.box()
        return types.SimpleNamespace(minPoint=cm(lo), maxPoint=cm(hi))
    preciseBoundingBox = boundingBox
    def pointContainment(self, p): return 'IN' if self.shape.has(mm(p)) else 'OUT'
    def createForAssemblyContext(self, occ): return self
    @property
    def _plain_edges(self):
        return L(types.SimpleNamespace(geometry=types.SimpleNamespace(objectType='adsk::core::Circle3D', center=cm(c), normal=Pt(*n), radius=r / 10.0),
                                       faces=L([1, 2])) for c, n, r in self.shape.circles())
    edges = _plain_edges
    @property
    def faces(self):
        if self._faces is not None: return L(self._faces)
        if isinstance(self.shape, Seg): return L([1] if self.shape.pts else [])
        out = []
        for c, n, (lo, hi) in self.shape.planes():
            out.append(types.SimpleNamespace(
                geometry=types.SimpleNamespace(objectType='adsk::core::Plane', surfaceType=0, normal=Pt(*n)), area=1.0, centroid=cm(c), pointOnFace=cm(c),
                boundingBox=types.SimpleNamespace(minPoint=cm(lo), maxPoint=cm(hi)),
                evaluator=types.SimpleNamespace(getNormalAtPoint=lambda p, n=n: (True, Pt(*n))), loops=L(), edges=L()))
        for o, a, r in self.shape.cyls():
            out.append(types.SimpleNamespace(
                geometry=types.SimpleNamespace(objectType='adsk::core::Cylinder', surfaceType=1, origin=cm(o), axis=Pt(*a), radius=r / 10.0),
                area=2.0, centroid=cm(o), pointOnFace=cm(o), loops=L(), edges=L()))
        return L(out)
    @property
    def meshManager(self): return types.SimpleNamespace(createMeshCalculator=lambda: self._mesher())


class MeshBody:
    def __init__(self, name, nodes, tris, bulb=True):
        self.name, self.isLightBulbOn = name, bulb
        lo, hi = R.box_of(nodes)
        self.boundingBox = types.SimpleNamespace(minPoint=cm(lo), maxPoint=cm(hi))
        self.displayMesh = types.SimpleNamespace(nodeCoordinatesAsDouble=[c / 10.0 for p in nodes for c in p], nodeIndices=[i for t in tris for i in t])


class Comp:
    def __init__(self, name, bodies=(), meshes=()):
        self.name, self.id, self.bRepBodies, self.meshBodies = name, 'id-' + name, L(bodies), L(meshes)


class Occ:
    assemblyContext = None
    def __init__(self, comp, bulb=True, n=1):
        self.component, self.isLightBulbOn, self.fullPathName = comp, bulb, '%s:%d' % (comp.name, n)
        self.bRepBodies = comp.bRepBodies
        self.transform2 = types.SimpleNamespace(asArray=lambda: _geom.IDENTITY)
    @property
    def boundingBox(self):
        boxes = [c for b in self.component.bRepBodies for c in b.shape.box()] + [mm(p) for m in self.component.meshBodies for p in (m.boundingBox.minPoint, m.boundingBox.maxPoint)]
        lo, hi = R.box_of(boxes)
        return types.SimpleNamespace(minPoint=cm(lo), maxPoint=cm(hi))


class Manager:
    """TemporaryBRepManager: a cylinder is a cloud of sample points, an intersection keeps those inside the tool."""
    def createCylinderOrCone(self, p0, r0, p1, r1): return Body('temporary', Seg(mm(p0), mm(p1), r0 * 10.0))
    def copy(self, body): return body
    def booleanOperation(self, target, tool, kind):
        target.shape.pts = [p for p in target.shape.pts if tool.shape.has(p)]
        return True


def measure(a, b):
    """measureMinimumDistance of the fake: between the bounding boxes."""
    (alo, ahi), (blo, bhi) = a.shape.box(), b.shape.box()
    c = tuple((blo[i] + bhi[i]) / 2.0 for i in range(3))
    p2 = R.box_nearest(blo, bhi, R.box_nearest(alo, ahi, c))
    p1 = R.box_nearest(alo, ahi, p2)
    return types.SimpleNamespace(value=R.box_gap(alo, ahi, blo, bhi) / 10.0, positionOne=cm(p1), positionTwo=cm(p2))


def fake_adsk(comps, modified=False):
    occs = L(Occ(c) for c in comps)
    root = Comp('Demo Assembly'); root.id = 'root'; root.allOccurrences = occs
    design = types.SimpleNamespace(rootComponent=root)
    doc = types.SimpleNamespace(name='Demo Assembly v7', dataFile=types.SimpleNamespace(versionNumber=7, id='f1'), isModified=modified,
                                products=types.SimpleNamespace(itemByProductType=lambda t: design))
    app = types.SimpleNamespace(documents=[doc], measureManager=types.SimpleNamespace(measureMinimumDistance=measure))
    core = types.ModuleType('adsk.core')
    core.Application = types.SimpleNamespace(get=lambda: app)
    core.Point3D = types.SimpleNamespace(create=lambda x, y, z: Pt(x, y, z))
    core.Vector3D = types.SimpleNamespace(create=lambda x, y, z: Pt(x, y, z))
    fusion = types.ModuleType('adsk.fusion')
    fusion.Design = types.SimpleNamespace(cast=lambda x: x)
    fusion.PointContainment = types.SimpleNamespace(PointInsidePointContainment='IN', PointOutsidePointContainment='OUT')
    manager = Manager()
    fusion.TemporaryBRepManager = types.SimpleNamespace(get=lambda: manager)
    fusion.BooleanTypes = types.SimpleNamespace(IntersectionBooleanType='intersect')
    fusion.TriangleMeshQualityOptions = types.SimpleNamespace(HighQualityTriangleMesh='High', NormalQualityTriangleMesh='Normal', VeryHighQualityTriangleMesh='VeryHigh')
    adsk = types.ModuleType('adsk'); adsk.core, adsk.fusion = core, fusion
    return {'adsk': adsk, 'adsk.core': core, 'adsk.fusion': fusion}


def default(name):
    """The default of one parameter as the registry ships it."""
    with open(os.path.join(ROOT, 'rules', 'registry', name.split('.')[0] + '.json'), encoding='utf-8') as f: return json.load(f)['params'][name]['default']


PARAMS = {'bearings.ref_pattern': '(?i)bearing', 'bearings.contact_gap_max': 0.05, 'bearings.clamp_room': 1.0,
          'screws.ref_pattern': default('screws.ref_pattern'), 'washers.ref_pattern': '(?i)washer', 'washers.landing_gap_max': 0.05,
          'access.reach': 60.0, 'access.tool_clearance': 1.0, 'access.cap_max': 2.0, 'inserts.iron_d': 8.0, 'inserts.iron_reach': 30.0,
          'clearance.scan_margin': 5.0, 'walls.min_face_width': 1.2}


SHIPPED = {'screws.ref_pattern': default('screws.ref_pattern')}        # the default as it ships: the words, and a thread with a length ("M3x12")
WORD = {'screws.ref_pattern': '(?i)screw|bolt'}                         # the pattern of a project that wants the word


class Probes(unittest.TestCase):
    """Each probe as the script gate_fusion_rpc builds, run by _fusion.run() against the fake."""

    def unit_with(self, mods, probe, **kw):
        """unit() against a fake that the test changed first (an occurrence path, a transform)."""
        return self.unit(probe, mods, **kw)

    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.d)

    def unit(self, probe, comps, part='Bracket', features=None, args=None, cursor=None, sig=None, groups=None, deadline_s=20, params=None, body='Body1', sent=None):
        job = self.job = gate_fusion_rpc.new_job(unit_id='u-0001', run='r1', probe=probe, doc='Demo Assembly', doc_key='demo',
                                      out=os.path.join(self.d, 'gate', 'run', 'r1', 'units', 'u-0001.json'), part='demo/' + gate_common.slug(part),
                                      component_id='id-' + part, occurrence=part + ':1', body=body, sig=sig, cursor=cursor, deadline_s=deadline_s,
                                      params=dict(PARAMS, **(params or {})), tables={n: table(n) for n in ('inserts', 'screws', 'bearings', 'washers')},
                                      features=features, not_printed=['^ref '], motion_groups=groups, args=args)
        if sent is not None: job['sent'] = sent
        mods = comps if isinstance(comps, dict) else fake_adsk(comps)       # a prepared fake (unit_with), or the components of one
        saved = {k: sys.modules.get(k) for k in mods}
        sys.modules.update(mods)
        try:
            g = {}
            exec(compile(gate_fusion_rpc.build_script(job), probe + '.py', 'exec'), g)
            g['run']('')
            self.assertEqual(sorted(k for k in g if not k.startswith('__')), ['json', 'run'])        # nothing else is left in the shared namespace
        finally:
            for k, v in saved.items():
                if v is None: sys.modules.pop(k, None)
                else: sys.modules[k] = v
        with open(job['out'], encoding='utf-8') as f: env = json.load(f)
        self.assertIsNone(env['error'], env['error'])
        # every call of every probe: the file names the call by the cursor AS IT WAS SENT, whatever the probe did with it
        self.assertEqual(env['asked']['cursor_key'], gate_fusion_rpc.cursor_key(cursor), probe)
        return env

    def rest(self, probe, comps, env, **kw):
        """The calls after `env` (the envelope of an earlier call) until the unit is done: (the facts of those calls joined, the notes of all of them)."""
        facts, notes, calls = None, [], 0
        while not env['done']:
            env = self.unit(probe, comps() if callable(comps) else comps, cursor=env['cursor'], **kw)
            facts, notes, calls = gate_fusion_rpc.merge_facts(facts, env['facts']), notes + env['notes'], calls + 1
            self.assertLess(calls, 4000, 'the unit does not end')
        return facts, notes

    # -- bearings: a 6005 in a housing (seat with a floor under the outer ring), a lid whose lip clamps the outer ring

    def bearing_scene(self, flange_gap=1.0, lid=None, extra=()):
        top = 13.0 - flange_gap                                                        # the housing's top face under the lid's flange
        housing = Sub(Blk((-40, -40, -5), (40, 40, top)), Rod(0, 0, 0.0, 20.0, 23.5), Rod(0, 0, -6.0, 0.001, 20.0))
        lid = lid or Add(Rod(0, 0, 12.0, 13.0, 23.4, 21.0), Rod(0, 0, 13.0, 16.0, 35.0, 16.0))
        return [Comp('Housing', [Body('Body1', housing)]), Comp('Lid', [Body('Body1', lid)]),
                Comp('ref bearing 6005', [Body('Body1', Rod(0, 0, 0.0, 12.0, 23.5, 12.5))])] + list(extra)

    def face(self, b, ring, side):
        return [f for f in b['rings'][ring]['faces'] if f['side'] == side][0]['covered']

    def test_bearings_a_clamped_outer_ring_and_a_free_inner_ring(self):
        env = self.unit('fusion_bearings', self.bearing_scene(), part='Housing')
        self.assertTrue(env['done']); self.assertEqual(len(env['sig']), 64)
        b, = env['facts']['bearings']
        self.assertEqual((b['ref'], b['designation'], b['by'], b['d'], b['D'], b['B']), ('ref bearing 6005', '6005', 'geometry', 25.0, 47.0, 12.0))
        self.assertEqual(b['anchor'], {'kind': 'bearing', 'axis': [0.0, 0.0, 1.0], 'point': [0.0, 0.0, 6.0]})
        floor, = self.face(b, 'outer', '-')
        self.assertEqual((floor['name'], floor['kind'], floor['part'], floor['share'], floor['points'], floor['gap'], floor['touches_other_ring']),
                         ('Housing', 'printed', 'demo/housing', 1.0, 1, 0.0, False))
        lip, = self.face(b, 'outer', '+')
        self.assertEqual((lip['name'], lip['part'], lip['share'], lip['gap'], lip['touches_other_ring']), ('Lid', 'demo/lid', 1.0, 0.0, False))
        self.assertEqual(self.face(b, 'inner', '+'), []); self.assertEqual(self.face(b, 'inner', '-'), [])     # nothing holds the inner ring
        clamp, = b['clamps']
        self.assertEqual((clamp['ring'], clamp['side'], clamp['by'], clamp['name'], clamp['ring_width'], clamp['lands_on']), ('outer', '+', 'printed', 'Lid', 12.0, 'Housing'))
        self.assertAlmostEqual(clamp['travel'], 1.0, delta=0.03); self.assertAlmostEqual(clamp['room'], 11.0, delta=0.03)
        seat = b['seat']['outer']
        self.assertEqual((seat['part'], seat['len'], seat['entry_side'], seat['entry_open']), ('demo/housing', 12.0, '+', True))
        self.assertAlmostEqual(seat['d'], 47.0, delta=0.03); self.assertIsNone(b['seat']['inner'])
        self.assertEqual(env['facts']['seats_without_bearing'], [])

    def test_bearings_a_landing_only_0_3_behind_the_ring_face(self):
        b, = self.unit('fusion_bearings', self.bearing_scene(flange_gap=0.3), part='Housing')['facts']['bearings']
        self.assertAlmostEqual(b['clamps'][0]['room'], 11.7, delta=0.03)              # 0.3 of clamp room: the judge fails it

    def test_bearings_a_flat_lid_touches_both_rings(self):
        b, = self.unit('fusion_bearings', self.bearing_scene(lid=Rod(0, 0, 12.0, 14.0, 30.0)), part='Housing')['facts']['bearings']
        self.assertTrue(self.face(b, 'outer', '+')[0]['touches_other_ring'])
        self.assertTrue(self.face(b, 'inner', '+')[0]['touches_other_ring'])

    def test_bearings_a_hidden_lid_holds_nothing_and_is_named(self):
        scene = self.bearing_scene()
        scene[1].bRepBodies[0].isLightBulbOn = False
        b, = self.unit('fusion_bearings', scene, part='Housing')['facts']['bearings']
        self.assertEqual(self.face(b, 'outer', '+'), []); self.assertEqual(b['hidden_near'], ['Lid']); self.assertEqual(b['clamps'], [])

    def test_bearings_seat_without_a_bearing_and_a_name_without_geometry(self):
        seat = {'id': 'h9', 'anchor': {'kind': 'hole', 'axis': [0, 0, 1], 'point': [0, 0, 12]}, 'segments': [{'d': 47.0, 'len': 12.0}], 'depth': 12.0,
                'through': False, 'class': {'table': 'bearings', 'kind': '6005', 'role': 'bearing_seat', 'match': 'exact'}}
        other = dict(seat, id='h10', anchor={'kind': 'hole', 'axis': [0, 0, 1], 'point': [30, 30, 12]})
        env = self.unit('fusion_bearings', self.bearing_scene(), part='Housing', features={'holes': [seat, other], 'openings': []})
        self.assertEqual(env['facts']['seats_without_bearing'], [{'hole': 'h10', 'anchor': other['anchor'], 'd': 47.0, 'designation': '6005', 'match': 'exact', 'bodies': []}])
        blob = Comp('ref bearing 608 as a block', [Body('Body1', Blk((30, 30, 0), (39, 39, 7)))])     # no circular edge at all
        env = self.unit('fusion_bearings', self.bearing_scene(extra=[blob]), part='Housing')
        names = {b['ref']: b for b in env['facts']['bearings']}
        self.assertEqual((names['ref bearing 608 as a block']['designation'], names['ref bearing 608 as a block']['by'], names['ref bearing 608 as a block']['rings']), ('608', 'name', None))
        printed = Comp('Bearing cap', [Body('Body1', Rod(45, 0, 0.0, 5.0, 4.5, 2.0))])                # printed, the word in its name
        env = self.unit('fusion_bearings', self.bearing_scene(extra=[printed]), part='Housing')
        self.assertEqual(len(env['facts']['bearings']), 1); self.assertEqual(env['facts']['not_bearings'], ['Bearing cap'])

    def test_bearings_a_washer_on_a_post_that_ends_flush_with_the_ring(self):
        def scene(post_top):
            hub = Add(Rod(0, 0, -5.0, post_top, 12.5), Rod(0, 0, -5.0, 0.0, 16.0))                   # post through the bore, shoulder under the inner ring
            return [Comp('Hub', [Body('Body1', hub)]), Comp('ref bearing 6005', [Body('Body1', Rod(0, 0, 0.0, 12.0, 23.5, 12.5))]),
                    Comp('ref washer big', [Body('Body1', Rod(0, 0, 12.0, 13.0, 15.0, 2.0))])]
        b, = self.unit('fusion_bearings', scene(12.0), part='Hub')['facts']['bearings']
        top, = self.face(b, 'inner', '+')
        self.assertEqual((top['name'], top['kind'], top['part'], top['share'], top['touches_other_ring']), ('ref washer big', 'hardware', None, 1.0, False))
        self.assertEqual(self.face(b, 'inner', '-')[0]['name'], 'Hub'); self.assertEqual(self.face(b, 'outer', '+'), [])
        clamp, = b['clamps']
        self.assertEqual((clamp['ring'], clamp['side'], clamp['by'], clamp['lands_on'], clamp['travel'], clamp['room']), ('inner', '+', 'washer', 'Hub', 0.0, 12.0))
        self.assertAlmostEqual(b['seat']['inner']['d'], 25.0, delta=0.03)
        clamp, = self.unit('fusion_bearings', scene(11.0), part='Hub')['facts']['bearings'][0]['clamps']       # the post 1.0 short of the ring face
        self.assertAlmostEqual(clamp['room'], 11.0, delta=0.03); self.assertFalse(clamp['travel_capped'])
        clamp, = self.unit('fusion_bearings', scene(5.0), part='Hub')['facts']['bearings'][0]['clamps']        # nothing stops the washer
        self.assertEqual((clamp['travel'], clamp['travel_capped'], clamp['lands_on'], clamp['room']), (3.0, True, None, 9.0))

    def whole(self, probe, comps, **kw):
        """A unit called again with its cursor until it is done, as the host does: (merged facts, calls, the last envelope)."""
        facts, calls, cursor = None, 0, None
        while True:
            env = self.unit(probe, comps() if callable(comps) else comps, cursor=cursor, **kw)
            facts, calls, cursor = gate_fusion_rpc.merge_facts(facts, env['facts']), calls + 1, env['cursor']
            if env['done']: return facts, calls, env
            self.assertLess(calls, 4000, 'the unit does not end')

    def test_bearings_cursor(self):
        second = Comp('ref bearing 608', [Body('Body1', Rod(30, 30, 0.0, 7.0, 11.0, 4.0))])
        scene = lambda: self.bearing_scene(extra=[second])
        one = self.unit('fusion_bearings', scene(), part='Housing')
        self.assertTrue(one['done']); self.assertIsNone(one['cursor'])
        env = self.unit('fusion_bearings', scene(), part='Housing', deadline_s=0.001)   # no time: a call finishes one step or asks 48 points, then stops
        self.assertFalse(env['done']); self.assertEqual(env['facts']['bearings'], [])
        cur = env['cursor']
        self.assertEqual((cur['i'], len(cur['key']), cur['part']['f'], 'tape' in cur['part'], cur['timing']['calls']), (0, 16, {'0|+': []}, False, 1))   # its "+" side is known from the boxes: one step
        self.assertEqual((len(cur['part']['k']), sorted(cur['part']['asked'])), (16, ['2']))    # what it is measured against: the bodies it uses, and the state of the bearing itself
        json.dumps(cur)                                                             # plain JSON: no body, no record
        env = self.unit('fusion_bearings', scene(), part='Housing', deadline_s=0.001, cursor=cur)
        cur = env['cursor']
        self.assertEqual((cur['part']['at'], cur['part']['tape']['n'], sorted(cur['part']['asked'])), ('faces:0|-', 48, ['0', '2']))  # inside the next step: 48 points, then the clock; the housing answered
        facts, calls, last = self.whole('fusion_bearings', scene, part='Housing', deadline_s=0.001)
        self.assertGreater(calls, 20); self.assertEqual(facts, one['facts'])        # many calls, the facts of one call
        self.assertEqual(sorted(b['designation'] for b in facts['bearings']), ['6005', '608'])
        self.assertEqual(last['timing']['calls'], calls)
        old = self.unit('fusion_bearings', scene(), part='Housing', cursor=1)        # the cursor of an older plan: the index of the next bearing
        self.assertTrue(old['done']); self.assertEqual([b['designation'] for b in old['facts']['bearings']], ['608'])
        self.assertEqual((old['facts']['not_bearings'], old['facts']['rejected']), ([], []))

    def test_bearings_every_stop_gives_the_facts_of_one_call(self):
        """The work on one bearing is cut wherever the time runs out: inside a ring face, inside a seat, inside the travel
        of a clamp, between two steps. Whatever the cut, the joined facts are the facts of a call with all the time."""
        def washer_scene():
            hub = Add(Rod(0, 0, -5.0, 11.0, 12.5), Rod(0, 0, -5.0, 0.0, 16.0))
            return [Comp('Hub', [Body('Body1', hub)]), Comp('ref bearing 6005', [Body('Body1', Rod(0, 0, 0.0, 12.0, 23.5, 12.5))]),
                    Comp('ref washer big', [Body('Body1', Rod(0, 0, 12.0, 13.0, 15.0, 2.0))])]
        seat = {'id': 'h10', 'anchor': {'kind': 'hole', 'axis': [0, 0, 1], 'point': [30, 30, 12]}, 'segments': [{'d': 47.0, 'len': 12.0}], 'depth': 12.0,
                'through': False, 'class': {'table': 'bearings', 'kind': '6005', 'role': 'bearing_seat', 'match': 'exact'}}
        printed = Comp('Bearing cap', [Body('Body1', Rod(45, 0, 0.0, 5.0, 4.5, 2.0))])
        cases = [('Housing', lambda: self.bearing_scene(extra=[printed]), {'holes': [seat], 'openings': []}), ('Hub', washer_scene, None),
                 ('Housing', lambda: self.bearing_scene(lid=Rod(0, 0, 12.0, 14.0, 30.0)), None)]
        for part, scene, features in cases:
            one = self.unit('fusion_bearings', scene(), part=part, features=features)
            self.assertTrue(one['done'])
            total = sum(one['timing'][k]['pc'] for k in ('faces', 'seats', 'travel'))
            for limit in (max(13, total // 40), 1500):
                facts, calls, last = self.whole('fusion_bearings', scene, part=part, features=features, args={'tape_limit': limit})
                self.assertEqual(facts, one['facts'], (part, limit)); self.assertGreaterEqual(calls, total // limit)
                self.assertEqual(json.dumps(facts, sort_keys=True), json.dumps(one['facts'], sort_keys=True))
                self.assertEqual({k: last['timing'][k]['pc'] for k in ('faces', 'seats', 'travel')}, {k: one['timing'][k]['pc'] for k in ('faces', 'seats', 'travel')})

    def test_bearings_other_bodies_than_the_cursor_was_written_for_start_the_bearing_again(self):
        first = self.unit('fusion_bearings', self.bearing_scene(), part='Housing', args={'tape_limit': 300})
        self.assertFalse(first['done']); self.assertTrue(first['cursor']['part']['f'])
        moved = self.bearing_scene(lid=Add(Rod(0, 0, 12.0, 13.0, 23.4, 21.0), Rod(0, 0, 13.0, 17.0, 35.0, 16.0)))      # the lid grew between the two calls
        env = self.unit('fusion_bearings', moved, part='Housing', cursor=first['cursor'])
        self.assertTrue(env['done']); self.assertTrue(any('measured again from its start' in n for n in env['notes']), env['notes'])
        self.assertEqual(env['facts']['bearings'], self.unit('fusion_bearings', moved, part='Housing')['facts']['bearings'])
        cur = json.loads(json.dumps(first['cursor'])); cur['part']['tape']['sum'] += 1                                 # a tape that answers other questions
        env = self.unit('fusion_bearings', self.bearing_scene(), part='Housing', cursor=cur)
        self.assertTrue(any('a step of a bearing' in n for n in env['notes']), env['notes'])
        self.assertEqual(env['facts']['bearings'], self.unit('fusion_bearings', self.bearing_scene(), part='Housing')['facts']['bearings'])
        # a step that never asks the same questions twice is not started again for ever: the third call in a row ends the unit in an error
        def spoil(c):
            c = json.loads(json.dumps(c)); c['part']['tape']['sum'] += 1
            return c
        cur = first['cursor']
        for n in (1, 2):
            env = self.unit('fusion_bearings', self.bearing_scene(), part='Housing', cursor=spoil(cur), args={'tape_limit': 100})
            self.assertEqual((env['done'], env['cursor']['part']['again']), (False, n))
            cur = env['cursor']
        with self.assertRaises(AssertionError) as e: self.unit('fusion_bearings', self.bearing_scene(), part='Housing', cursor=spoil(cur), args={'tape_limit': 100})
        self.assertIn('did not ask the same questions in three calls in a row', str(e.exception))
        ok = self.whole('fusion_bearings', self.bearing_scene, part='Housing', args={'tape_limit': 400})[2]      # undisturbed, the count never appears
        self.assertNotIn('again', json.dumps(ok['cursor']))

    def test_bearings_the_answer_of_a_later_call_names_the_cursor_it_was_sent_with(self):
        """The probe works on the state of the bearing in progress. The result file still names the call by the cursor
        the host sent, so a late answer of the second or a later call of the unit is adopted like the one of the first."""
        first = self.unit('fusion_bearings', self.bearing_scene(), part='Housing', args={'tape_limit': 300})
        cur = first['cursor']
        self.assertTrue(cur['part']['f'])
        sent = time.time()
        second = self.unit('fusion_bearings', self.bearing_scene(), part='Housing', args={'tape_limit': 300}, cursor=cur, sent=sent)
        self.assertEqual(cur, first['cursor']); self.assertNotEqual(second['cursor']['part'], cur['part'])       # the unit went on; what was sent is as it was
        job = self.job
        env, why = gate_fusion_rpc.late_answer(job, {'sent': sent})
        self.assertIsNone(why); self.assertEqual((env['cursor'], env['done']), (second['cursor'], False))
        state, env, why = gate_fusion_rpc.settle(job, 'http://127.0.0.1:1/mcp', pinger=lambda u, t: True,
                                                 flight={'sent': sent, 'cursor': gate_fusion_rpc.cursor_key(cur)})
        self.assertEqual((state, env['cursor'], env['late']['sent']), ('late', second['cursor'], sent))
        self.assertEqual(gate_fusion_rpc.late_answer(dict(job, cursor=second['cursor']), {'sent': sent}), (None, 'the result file was written for another cursor'))

    def test_bearings_a_neighbour_that_changes_between_two_calls_starts_the_bearing_again(self):
        """One bearing is measured on one state of the model. The lip of the lid is cut back inside the same bounding box
        while the unit is between two calls: the faces and seats of the lid that are in the cursor are of a lid that is
        no more. The resumed unit gives the facts of one call on the model as it is now."""
        def cut():
            lid = Add(Rod(0, 0, 12.5, 13.0, 23.4, 21.0), Rod(0, 0, 13.0, 16.0, 35.0, 16.0), Blk((33, -1, 12.0), (35, 1, 13.0)))     # the lip 0.5 short of the ring
            scene = self.bearing_scene(lid=lid)
            scene[1].bRepBodies[0]._volume = 0.97                                    # a cut changes the volume of a body (and its revision id)
            return scene
        whole_lid = lambda: self.bearing_scene(lid=Add(Rod(0, 0, 12.0, 13.0, 23.4, 21.0), Rod(0, 0, 13.0, 16.0, 35.0, 16.0), Blk((33, -1, 12.0), (35, 1, 13.0))))
        before, after = self.unit('fusion_bearings', whole_lid(), part='Housing'), self.unit('fusion_bearings', cut(), part='Housing')
        self.assertNotEqual(before['facts']['bearings'], after['facts']['bearings'])
        self.assertEqual((self.face(before['facts']['bearings'][0], 'outer', '+')[0]['share'], self.face(after['facts']['bearings'][0], 'outer', '+')[0]['share']), (1.0, 0.0))
        first = self.unit('fusion_bearings', whole_lid(), part='Housing', args={'tape_limit': 5500})
        self.assertFalse(first['done'])
        lid_at = '1'                                                                 # the place of the lid in the walk: Housing, Lid, the bearing
        self.assertIn(lid_at, first['cursor']['part']['asked']); self.assertTrue(any(k.startswith('1|') for k in first['cursor']['part']['f']))
        facts, notes = self.rest('fusion_bearings', cut, first, part='Housing', args={'tape_limit': 5500})
        self.assertEqual(facts['bearings'], after['facts']['bearings'])
        self.assertEqual(len([n for n in notes if 'bearing ref bearing 6005 is measured again from its start' in n]), 1)
        # unchanged bodies: the same cursor goes on without a note, and gives the facts of the model as it was
        facts, notes = self.rest('fusion_bearings', whole_lid, first, part='Housing', args={'tape_limit': 5500})
        self.assertEqual(facts['bearings'], before['facts']['bearings']); self.assertFalse([n for n in notes if 'measured again' in n])

    def test_bearings_a_body_the_bearing_does_not_use_restarts_nothing_and_a_restart_is_counted(self):
        far = lambda dx: [Comp('ref far block', [Body('Body1', Blk((50, -5, 0), (55 + dx, 5, 5)))])]       # 10 mm from every neighbour of the bearing
        first = self.unit('fusion_bearings', self.bearing_scene(extra=far(0.0)), part='Housing', args={'tape_limit': 300})
        facts, notes = self.rest('fusion_bearings', lambda: self.bearing_scene(extra=far(0.5)), first, part='Housing', args={'tape_limit': 300})
        self.assertFalse([n for n in notes if 'measured again' in n])                # it grew between the calls: no body of this bearing
        self.assertEqual(facts['bearings'], self.unit('fusion_bearings', self.bearing_scene(), part='Housing')['facts']['bearings'])
        # a body of the bearing that is another one in every call: started again twice, then the unit ends in an error
        grown = lambda dz: self.bearing_scene(lid=Add(Rod(0, 0, 12.0, 13.0, 23.4, 21.0), Rod(0, 0, 13.0, 16.0 + dz, 35.0, 16.0)))
        env = self.unit('fusion_bearings', grown(0.0), part='Housing', args={'tape_limit': 300})
        for n in (1, 2):
            env = self.unit('fusion_bearings', grown(0.01 * n), part='Housing', cursor=env['cursor'], args={'tape_limit': 300})
            self.assertEqual((env['done'], env['cursor']['restarts'], env['cursor']['i']), (False, n, 0))
        with self.assertRaises(AssertionError) as e: self.unit('fusion_bearings', grown(0.03), part='Housing', cursor=env['cursor'], args={'tape_limit': 300})
        self.assertIn('bearing ref bearing 6005 met other bodies than the call before in three calls in a row', str(e.exception))
        env = self.unit('fusion_bearings', grown(0.02), part='Housing', cursor=env['cursor'], args={'tape_limit': 300})      # it stays as it is: the count ends
        self.assertNotIn('restarts', env['cursor'])

    def test_bearings_another_list_of_bearings_than_the_cursor_counts_in_ends_the_unit(self):
        second = Comp('ref bearing 608', [Body('Body1', Rod(30, 30, 0.0, 7.0, 11.0, 4.0))])
        third = Comp('a ref bearing 608 more', [Body('Body1', Rod(-30, 30, 0.0, 7.0, 11.0, 4.0))])        # its key sorts first
        scene = lambda *more: self.bearing_scene(extra=[second] + list(more))
        env = self.unit('fusion_bearings', scene(), part='Housing', args={'tape_limit': 3000})
        while env['cursor']['i'] == 0: env = self.unit('fusion_bearings', scene(), part='Housing', cursor=env['cursor'], args={'tape_limit': 3000})
        self.assertEqual((env['done'], env['cursor']['i']), (False, 1))              # the first bearing is in the facts, the second is next
        with self.assertRaises(AssertionError) as e: self.unit('fusion_bearings', scene(third), part='Housing', cursor=env['cursor'])
        self.assertIn('the bearings near the part changed between two calls of this unit (3 found now): start the unit again', str(e.exception))
        between = dict(env['cursor'], part={})                                       # also between two bearings, with no step in progress
        with self.assertRaises(AssertionError): self.unit('fusion_bearings', scene(third), part='Housing', cursor=between)
        self.assertTrue(self.unit('fusion_bearings', scene(), part='Housing', cursor=between)['done'])

    def test_bearings_the_walk_over_a_large_clamp_body_opens_a_call_of_its_own(self):
        """The walk over the faces of a clamp body reads no clock. For a body with many faces (here: any, "walk_faces" 0) it
        does not start in a call that has done other work: the step stops in front of it and the next call begins with it."""
        one = self.unit('fusion_bearings', self.bearing_scene(), part='Housing')
        env, calls, facts, stopped = None, 0, None, 0
        while env is None or not env['done']:
            env = self.unit('fusion_bearings', self.bearing_scene(), part='Housing', cursor=env['cursor'] if env else None, args={'tape_limit': 100000, 'walk_faces': 0})
            facts, calls = gate_fusion_rpc.merge_facts(facts, env['facts']), calls + 1
            if not env['done']:
                part = env['cursor']['part']
                self.assertEqual((part['at'], part['pick']), ('travel:outer', {})); stopped += 1                # in front of the walk, no face picked yet
        self.assertEqual((calls, stopped), (2, 1)); self.assertEqual(facts, one['facts'])
        self.assertIn('travel_walk_s', env['timing'])
        again = self.unit('fusion_bearings', self.bearing_scene(), part='Housing', args={'tape_limit': 100000})   # the limit of the probe is 500 faces: this lid has a handful
        self.assertTrue(again['done'])

    def test_bearings_timing_is_beside_the_facts_and_counts_the_point_tests(self):
        env = self.unit('fusion_bearings', self.bearing_scene(), part='Housing')
        t = env['timing']
        self.assertEqual(sorted(t), ['calls', 'faces', 'prelude_s', 'seats', 'slowest', 'state_s', 'travel', 'travel_walk_s'])
        self.assertEqual(t['calls'], 1); self.assertGreater(t['faces']['pc'], 500); self.assertGreater(t['seats']['pc'], 50); self.assertGreater(t['travel']['pc'], 10)
        self.assertEqual(sum(b['pc'] for b in t['slowest']), t['faces']['pc'] + t['seats']['pc'] + t['travel']['pc'])
        self.assertEqual(sorted(b['name'] for b in t['slowest']), ['Housing', 'Lid'])
        self.assertNotIn('timing', env['facts']); self.assertNotIn('timing', json.dumps(env['facts']))
        self.assertEqual(len([n for n in env['notes'] if n.startswith('timing: ')]), 1)

    def test_bearings_what_is_known_without_a_point_test_is_not_asked(self):
        """A neighbour whose box does not reach the slab in front of a ring face, a body whose box cannot hold a seat
        wall, and the bearing of another printed part cost no point test."""
        far = Comp('ref plate', [Body('Body1', Blk((-40, -40, 14.6), (40, 40, 20)))])       # 2.6 above the ring face: near the bearing's box, out of every slab
        base = self.unit('fusion_bearings', self.bearing_scene(), part='Housing')
        env = self.unit('fusion_bearings', self.bearing_scene(extra=[far]), part='Housing')
        self.assertEqual(env['facts']['bearings'][0]['rings'], base['facts']['bearings'][0]['rings'])
        self.assertNotIn('ref plate', [b['name'] for b in env['timing']['slowest']])
        cap = Comp('Cap', [Body('Body1', Blk((24, -5, 0), (34, 5, 12)))])                    # a printed part beside the ring: the bearing is not its own
        env = self.unit('fusion_bearings', self.bearing_scene(extra=[cap]), part='Cap')
        self.assertEqual((env['facts']['bearings'], env['facts']['near_bearings']), ([], ['ref bearing 6005']))
        self.assertEqual([b['name'] for b in env['timing']['slowest']], ['Housing'])        # the first printed part at a ring face settles it: the lid is not asked
        self.assertLess(env['timing']['faces']['pc'], base['timing']['faces']['pc'])
        self.assertEqual((env['timing']['seats']['pc'], env['timing']['travel']['pc']), (0, 0))

    # -- openings, screws, washers, gap: a bracket screwed to a base, a cover over it

    def bracket_scene(self):
        bracket = Sub(Blk((0, 0, 0), (40, 40, 10)), Rod(10, 10, -1, 11, 1.7), Rod(30, 10, -1, 11, 1.7), Rod(30, 30, 4.0, 11, 1.7), Rod(30, 30, -1, 3.16, 1.7))
        base = Sub(Blk((0, 0, -12), (40, 40, 0)), Rod(10, 10, -6.5, 1, 2.5), Rod(30, 10, -6.5, 1, 2.5))
        screw = lambda x, seat: Add(Rod(x, 10, -2.0, seat, 1.5), Rod(x, 10, seat, seat + 3.0, 2.75))
        flat = [(25, 25, 15), (35, 25, 15), (35, 35, 15), (25, 35, 15)]
        return [Comp('Bracket', [Body('Body1', bracket)]), Comp('Base', [Body('Body1', base)]),
                Comp('Cover', [Body('Body1', Blk((-5, -5, 22.5), (20, 45, 24)))]),
                Comp('Old cover', [Body('Body1', Blk((25, 25, 30), (35, 35, 32)), bulb=False)]),
                Comp('Pulley', [Body('Body1', Blk((34.0, 5, 10.2), (40, 15, 14)))]),
                Comp('ref hardware', [Body('screw M3x12 cap (1)', screw(10, 10.0)), Body('screw M3x12 cap (2)', screw(30, 10.5)),
                                      Body('washer M3', Rod(30, 10, 10.0, 10.5, 3.5, 1.6)),
                                      Body('insert M3x4x5 (1)', Rod(10, 10, -4.0, 0.0, 2.5, 1.25)), Body('insert M3x4x5 (2)', Rod(30, 10, -4.0, 0.0, 2.5, 1.25))]),
                Comp('ref motor', [], [MeshBody('shell', flat, [(0, 1, 2), (0, 2, 3)])])]

    FEATURES = {'holes': [
        {'id': 'h1', 'anchor': {'kind': 'hole', 'axis': [0, 0, 1], 'point': [10, 10, 10]}, 'segments': [{'d': 3.4, 'len': 10.0}], 'depth': 10.0, 'through': True,
         'floor': None, 'exit': {'kind': 'hole', 'axis': [0, 0, -1], 'point': [10, 10, 0]}, 'class': {'table': 'screws', 'kind': 'M3', 'role': 'clearance', 'match': 'exact'}, 'coaxial': []},
        {'id': 'h2', 'anchor': {'kind': 'hole', 'axis': [0, 0, 1], 'point': [30, 30, 10]}, 'segments': [{'d': 3.4, 'len': 6.0}], 'depth': 6.0, 'through': False,
         'floor': 0.84, 'exit': None, 'class': None, 'coaxial': []},
        {'id': 'h3', 'anchor': {'kind': 'hole', 'axis': [0, 0, -1], 'point': [30, 30, 0]}, 'segments': [{'d': 3.4, 'len': 3.16}], 'depth': 3.16, 'through': False,
         'floor': 0.84, 'exit': None, 'class': None, 'coaxial': []}],
        'openings': [{'id': 'o1', 'anchor': {'kind': 'face', 'axis': [0, 0, 1], 'point': [10, 30, 10]}, 'shape': 'slot', 'size': [8.0, 3.0], 'through': True}]}

    def opening(self, facts, feature, end='mouth'):
        return [o for o in facts['openings'] if o['feature'] == feature and o['end'] == end][0]

    def test_openings_a_cover_a_mesh_a_hidden_body_and_the_screw_that_belongs(self):
        env = self.unit('fusion_openings', self.bracket_scene(), features=self.FEATURES)
        self.assertTrue(env['done'])
        facts = env['facts']
        own = self.opening(facts, 'h1')['paths']['own']
        self.assertEqual((own['r'], own['reach']), (1.7, 60.0))
        by = {h['name']: h for h in own['hits']}
        self.assertEqual(sorted(by), ['Cover', 'ref hardware / screw M3x12 cap (1)'])
        self.assertEqual((by['Cover']['kind'], by['Cover']['part'], by['Cover']['share'], by['Cover']['approx'], by['Cover']['expected']), ('printed', 'demo/cover', 1.0, False, False))
        self.assertAlmostEqual(by['Cover']['at'], 12.5, delta=0.15)
        screw = by['ref hardware / screw M3x12 cap (1)']
        self.assertEqual((screw['kind'], screw['expected']), ('hardware', True))                       # the fastener that belongs there
        self.assertAlmostEqual(own['free'], 12.5, delta=0.15)
        exit_ = self.opening(facts, 'h1', 'exit')
        self.assertEqual(exit_['anchor'], self.FEATURES['holes'][0]['exit'])
        self.assertTrue(all(h['expected'] for h in exit_['paths']['own']['hits'])); self.assertEqual(exit_['paths']['own']['free'], 60.0)
        blind = self.opening(facts, 'h2')['paths']['own']
        kinds = {h['name']: (h['kind'], h['approx'], round(h['at'], 1)) for h in blind['hits']}
        self.assertEqual(kinds['ref motor'], ('mesh', True, 5.0))                              # two big triangles, no vertex in the path
        self.assertEqual(kinds['Old cover'][:2], ('hidden', False)); self.assertAlmostEqual(kinds['Old cover'][2], 20.0, delta=0.2)
        self.assertEqual(blind['free'], 5.0)
        slot = self.opening(facts, 'o1')
        self.assertEqual((slot['of'], slot['section'], slot['paths']['own']['r']), ('slot', 'inscribed circle', 1.5))
        self.assertAlmostEqual(slot['paths']['own']['free'], 12.5, delta=0.15)
        heads = sorted(o['feature'] for o in facts['openings'] if o['of'] == 'screw_head')
        self.assertEqual(len(heads), 2)
        head = self.opening(facts, heads[0])
        self.assertEqual(head['anchor'], {'kind': 'screw', 'axis': [0.0, 0.0, 1.0], 'point': [10.0, 10.0, 10.0]})
        self.assertEqual(sorted(head['paths']), ['own', 'tool'])
        self.assertEqual((head['paths']['own']['r'], head['paths']['tool']['r']), (2.75, 3.25))
        self.assertEqual([h['name'] for h in head['paths']['tool']['hits']], ['Cover']); self.assertAlmostEqual(head['paths']['tool']['free'], 9.5, delta=0.15)
        self.assertEqual(self.opening(facts, heads[1])['paths']['tool']['free'], 60.0)                 # nothing over the second screw

    def test_openings_cut_by_the_clock_body_by_body_give_the_facts_of_one_call(self):
        one = self.unit('fusion_openings', self.bracket_scene(), features=self.FEATURES)
        self.assertTrue(one['done']); self.assertTrue(one['facts']['closed']); self.assertIn('screw_head', [o['of'] for o in one['facts']['openings']])
        for limit in (1, 3):
            facts, calls, _ = self.whole('fusion_openings', self.bracket_scene, features=self.FEATURES, args={'path_limit': limit})
            self.assertEqual(json.dumps(facts, sort_keys=True), json.dumps(one['facts'], sort_keys=True), limit); self.assertGreater(calls, 3)

    def test_openings_a_body_that_changes_between_two_calls_starts_the_feature_again(self):
        """A call stops inside the first hole after the cover over it was cut. Then the cover is moved off the hole, or gets a
        window over it inside the same box: the hit in the cursor is of a cover that is no more."""
        def scene(cover):
            sc = self.bracket_scene()
            sc[2] = Comp('Cover', [Body('Body1', cover)])
            return sc
        moved = lambda: scene(Blk((45, -5, 22.5), (70, 45, 24)))
        window = lambda: scene(Sub(Blk((-5, -5, 22.5), (20, 45, 24)), Rod(10, 10, 22.0, 25.0, 3.0)))
        first = self.unit('fusion_openings', self.bracket_scene(), features=self.FEATURES, args={'path_limit': 1})
        cur = first['cursor']
        self.assertEqual((cur['i'], [h['name'] for h in cur['path']['hits']], sorted(cur['asked'])), (0, ['Cover'], ['2']))      # the cover was cut, the screw is next
        for changed in (moved, window):
            one = self.unit('fusion_openings', changed(), features=self.FEATURES)
            facts, notes = self.rest('fusion_openings', changed, first, features=self.FEATURES, args={'path_limit': 1})
            self.assertEqual(json.dumps(facts['openings'], sort_keys=True), json.dumps(one['facts']['openings'], sort_keys=True))
            self.assertEqual(self.opening(facts, 'h1')['paths']['own']['free'], 60.0)
            self.assertEqual(len([n for n in notes if 'feature 1 is measured again from its start' in n]), 1)
        facts, notes = self.rest('fusion_openings', self.bracket_scene, first, features=self.FEATURES, args={'path_limit': 1})    # nothing changed: it goes on
        self.assertFalse([n for n in notes if 'measured again' in n]); self.assertLess(self.opening(facts, 'h1')['paths']['own']['free'], 13.0)
        env = first                                                                  # a body that is another one in every call: counted, then an error
        for n in (1, 2):
            env = self.unit('fusion_openings', scene(Blk((-5, -5, 22.5), (20, 45, 24 + 0.01 * n))), features=self.FEATURES, cursor=env['cursor'], args={'path_limit': 1})
            self.assertEqual(env['cursor']['again'], n)
        with self.assertRaises(AssertionError) as e:
            self.unit('fusion_openings', scene(Blk((-5, -5, 22.5), (20, 45, 24.03))), features=self.FEATURES, cursor=env['cursor'], args={'path_limit': 1})
        self.assertIn('feature 1 met other bodies than the call before in three calls in a row', str(e.exception))

    def test_openings_a_hole_closed_by_a_thin_cap(self):
        closed = self.unit('fusion_openings', self.bracket_scene(), features=self.FEATURES)['facts']['closed']
        self.assertEqual([(c['hole'], c['cap'], c['behind']) for c in closed],
                         [('h2', 0.84, {'what': 'hole', 'name': 'h3', 'd': 3.4}), ('h3', 0.84, {'what': 'hole', 'name': 'h2', 'd': 3.4})])
        thick = json.loads(json.dumps(self.FEATURES)); thick['holes'][1]['floor'] = thick['holes'][2]['floor'] = 2.5
        self.assertEqual(self.unit('fusion_openings', self.bracket_scene(), features=thick)['facts']['closed'], [])

    def test_openings_an_insert_pocket_under_a_ledge_and_the_cursor(self):
        pocket = {'id': 'h1', 'anchor': {'kind': 'insert_pocket', 'axis': [0, 0, 1], 'point': [10, 10, 10]}, 'segments': [{'d': 4.4, 'len': 1.5}, {'d': 3.6, 'len': 5.0}],
                  'depth': 6.5, 'through': False, 'floor': 3.5, 'exit': None, 'class': {'table': 'inserts', 'kind': 'M3x4x5', 'role': 'insert_pocket', 'match': 'exact'}, 'coaxial': []}
        part = Add(Sub(Blk((0, 0, 0), (40, 40, 10)), Rod(10, 10, 3.5, 11, 2.2)), Blk((12.5, 0, 10), (40, 40, 18)))     # a wall of the same part 2.5 from the axis
        comps, feats = [Comp('Bracket', [Body('Body1', part)])], {'holes': [pocket, dict(pocket, id='h2')], 'openings': []}
        one = self.unit('fusion_openings', comps, features=feats)
        self.assertTrue(one['done']); self.assertEqual([o['feature'] for o in one['facts']['openings']], ['h1', 'h2'])
        # no time: a call cuts one body, then stops INSIDE the feature. The cursor has the index, the screw heads the first call found,
        # the finished paths of the feature and the path it stopped in; the feature is in the facts when all its paths are done
        env = self.unit('fusion_openings', comps, features=feats, deadline_s=0.001)
        cur = env['cursor']
        self.assertEqual((env['done'], env['facts']['openings'], cur['i'], cur['screws'], sorted(cur['paths']), cur['path'], len(cur['world']), sorted(cur['asked'])),
                         (False, [], 0, [], ['mouth.own'], {'k': 0, 'hits': [], 'key': 'mouth.iron'}, 16, ['0']))
        facts, calls, _ = self.whole('fusion_openings', comps, features=feats, deadline_s=0.001)
        self.assertEqual(calls, 4); self.assertEqual(facts, one['facts'])                              # one body per call, the facts of one call
        other = dict(cur, world='0' * 16)                                                              # written for another list of bodies: from the start
        again = self.unit('fusion_openings', comps, features=feats, cursor=other)
        self.assertEqual(again['facts']['openings'], one['facts']['openings']); self.assertTrue(any('measured again from its start' in n for n in again['notes']))
        o = facts['openings'][0]
        self.assertEqual((o['of'], sorted(o['paths'])), ('insert_pocket', ['iron', 'own']))
        self.assertEqual(o['paths']['own']['hits'], [])                                                # the pocket's own section is free
        hit, = o['paths']['iron']['hits']                                                              # the iron is 8 mm wide
        self.assertEqual((hit['name'], hit['kind'], hit['part'], hit['expected']), ('Bracket', 'self', 'demo/bracket', False))
        self.assertLess(o['paths']['iron']['free'], 0.5); self.assertTrue(0.05 < hit['share'] < 0.5)

    def test_openings_a_screw_is_known_by_its_shape_and_a_name_alone_is_no_screw(self):
        # screws drawn as a head body and a shank body, one pair under names the screw pattern does not know; a D7 stop whose name says M3
        bracket = Sub(Blk((0, 0, 0), (40, 40, 10)), Rod(10, 10, -1, 11, 1.7), Rod(30, 10, -1, 11, 1.7), Rod(10, 30, -1, 11, 1.7), Rod(10, 30, 8.5, 11, 3.2))
        hole = lambda hid, x, y=10: {'id': hid, 'anchor': {'kind': 'hole', 'axis': [0, 0, 1], 'point': [x, y, 10]}, 'segments': [{'d': 3.4, 'len': 10.0}], 'depth': 10.0,
                                     'through': True, 'floor': None, 'exit': {'kind': 'hole', 'axis': [0, 0, -1], 'point': [x, y, 0]}, 'class': None, 'coaxial': []}
        holes = [hole('h1', 10), hole('h2', 30), hole('h3', 10, 30)]
        comps = [Comp('Bracket', [Body('Body1', bracket)]),
                 Comp('Spacer', [Body('Body1', Sub(Blk((20, 0, 10), (40, 20, 20)), Rod(30, 10, 9, 21, 1.7)))]),
                 Comp('ref fasteners', [Body('DIN 912 head (1)', Rod(10, 10, 10.0, 13.0, 2.75)), Body('DIN 912 shank (2)', Rod(10, 10, -2.0, 10.0, 1.5)),
                                        Body('M3x20 DIN 912 head (3)', Rod(30, 10, 20.0, 23.0, 2.75)), Body('M3x20 DIN 912 shank (4)', Rod(30, 10, 0.0, 20.0, 1.5)),
                                        Body('bearing 686 (5)', Rod(30, 30, 10.0, 15.0, 6.5, 3.0)),
                                        Body('M3x8 ISO 7380 head (7)', Rod(10, 30, 10.5, 12.15, 2.85)), Body('M3x8 ISO 7380 shank (8)', Rod(10, 30, 2.5, 10.5, 1.5)),
                                        Body('washer M3 (9)', Rod(10, 30, 10.0, 10.5, 3.5, 1.6)),
                                        Body('screw M3x6 stop (6)', Add(Rod(20, 20, 10.0, 12.0, 6.0), Rod(20, 20, 4.0, 10.0, 3.5)))])]
        env = self.unit('fusion_openings', comps, features={'holes': holes, 'openings': []})
        facts = env['facts']
        self.assertEqual([(s['n'], s['ref'], s['size'], s['head'], s['by'], s['seated'], s['holes'], (s['under'] or {}).get('name')) for s in facts['screws']],
                         [(1, 'ref fasteners / DIN 912 head (1)', 'M3', 'cap', 'shape', True, ['h1'], None),       # its head sits on the bracket
                          (2, 'ref fasteners / M3x20 DIN 912 head (3)', 'M3', 'cap', 'name', False, ['h2'], 'Spacer'),     # on the printed spacer above: judged there
                          (3, 'ref fasteners / M3x8 ISO 7380 head (7)', 'M3', 'button', 'name', True, ['h3'], 'ref fasteners / washer M3 (9)')])  # on a washer over a relief
        self.assertEqual(facts['not_screws'], ['ref fasteners / screw M3x6 stop (6)'])     # the name says M3, the shank measures D7
        self.assertFalse(facts['screws_cut'])
        heads = [o for o in facts['openings'] if o['of'] == 'screw_head']
        self.assertEqual([(o['feature'], o['ref'], o['anchor']['point']) for o in heads],
                         [('s1', 'ref fasteners / DIN 912 head (1)', [10.0, 10.0, 10.0]), ('s3', 'ref fasteners / M3x8 ISO 7380 head (7)', [10.0, 30.0, 10.5])])
        self.assertEqual(heads[0]['paths']['tool']['hits'], [])                            # head and shank are both the screw itself
        self.assertEqual(facts['n_features'], 5)
        # no time for the look at the shapes: it says so, and a judge must not read "no screw" from it
        cut = self.unit('fusion_openings', comps, features={'holes': holes, 'openings': []}, deadline_s=0.001)['facts']
        self.assertEqual(([s['by'] for s in cut['screws']], cut['screws_cut']), (['name', 'name'], True))
        # a later call of the same unit writes these lists no second time (lists are joined over the calls)
        later = self.unit('fusion_openings', comps, features={'holes': holes, 'openings': []}, cursor=1)['facts']
        self.assertNotIn('screws', later); self.assertNotIn('not_screws', later)

    def test_openings_a_later_call_measures_the_heads_the_first_call_found(self):
        # The look at the shapes is cut by the clock. A call that waited behind another session finds fewer screws than the first call did,
        # and then an index into "holes + openings + heads" would point at another head. So the first call stores its heads in the cursor.
        bracket = Sub(Blk((0, 0, 0), (40, 40, 10)), Rod(10, 10, -1, 11, 1.7), Rod(30, 10, -1, 11, 1.7))
        hole = lambda hid, x: {'id': hid, 'anchor': {'kind': 'hole', 'axis': [0, 0, 1], 'point': [x, 10, 10]}, 'segments': [{'d': 3.4, 'len': 10.0}], 'depth': 10.0,
                               'through': True, 'floor': None, 'exit': {'kind': 'hole', 'axis': [0, 0, -1], 'point': [x, 10, 0]}, 'class': None, 'coaxial': []}
        holes = [hole('h1', 10), hole('h2', 30)]
        comps = [Comp('Bracket', [Body('Body1', bracket)]),
                 Comp('ref fasteners', [Body('DIN 912 head (1)', Rod(10, 10, 10.0, 13.0, 2.75)), Body('DIN 912 shank (2)', Rod(10, 10, -2.0, 10.0, 1.5)),   # found by its shape only
                                        Body('M3x12 DIN 912 head (3)', Rod(30, 10, 10.0, 13.0, 2.75)), Body('M3x12 DIN 912 shank (4)', Rod(30, 10, -2.0, 10.0, 1.5))])]
        whole = self.unit('fusion_openings', comps, features={'holes': holes, 'openings': []})['facts']
        self.assertEqual([(s['n'], s['by']) for s in whole['screws']], [(1, 'shape'), (2, 'name')])
        want = {o['feature']: (o['ref'], o['anchor']['point']) for o in whole['openings'] if o['of'] == 'screw_head'}
        self.assertEqual(want, {'s1': ('ref fasteners / DIN 912 head (1)', [10.0, 10.0, 10.0]), 's2': ('ref fasteners / M3x12 DIN 912 head (3)', [30.0, 10.0, 10.0])})
        # the cursor a first call hands back when its time is used up after the two holes: both heads are in it, with their numbers
        heads = [{'n': 1, 'name': 'ref fasteners / DIN 912 head (1)', 'path': 'ref fasteners:1', 'bodies': ['DIN 912 head (1)', 'DIN 912 shank (2)'],
                  'top': [10.0, 10.0, 13.0], 'seat': [10.0, 10.0, 10.0], 'axis': [0.0, 0.0, 1.0], 'head_r': 2.75},
                 {'n': 2, 'name': 'ref fasteners / M3x12 DIN 912 head (3)', 'path': 'ref fasteners:1', 'bodies': ['M3x12 DIN 912 head (3)', 'M3x12 DIN 912 shank (4)'],
                  'top': [30.0, 10.0, 13.0], 'seat': [30.0, 10.0, 10.0], 'axis': [0.0, 0.0, 1.0], 'head_r': 2.75}]
        first = self.unit('fusion_openings', comps, features={'holes': holes, 'openings': []}, deadline_s=0.001)
        self.assertEqual((first['done'], first['cursor']['i'], first['facts']['openings']), (False, 0, []))     # it stopped inside the first hole
        self.assertTrue(first['facts']['screws_cut'])                                      # this first call had no time for the shapes: it knows one head only ...
        self.assertEqual([h['n'] for h in first['cursor']['screws']], [1]); self.assertEqual(first['cursor']['screws'][0]['name'], 'ref fasteners / M3x12 DIN 912 head (3)')
        # ... and a later call with all the time in the world measures exactly that head under that number: it does not look for screws again
        rest = self.unit('fusion_openings', comps, features={'holes': holes, 'openings': []}, cursor=first['cursor'])
        self.assertTrue(rest['done']); self.assertNotIn('screws', rest['facts'])
        self.assertEqual([(o['feature'], o.get('ref')) for o in rest['facts']['openings'] if o['of'] == 'screw_head'], [('s1', 'ref fasteners / M3x12 DIN 912 head (3)')])
        self.assertEqual([o['feature'] for o in rest['facts']['openings'] if o['of'] != 'screw_head'], ['h1', 'h1', 'h2', 'h2'])   # h1 was begun by the first call
        self.assertEqual([o for o in rest['facts']['openings'] if o['of'] != 'screw_head'], [o for o in whole['openings'] if o['of'] != 'screw_head'])
        # the other way round: the first call found both; a later call that is short of time still measures the head the index points at
        rows, cursor = [], {'i': 3, 'screws': heads}
        while True:
            late = self.unit('fusion_openings', comps, features={'holes': holes, 'openings': []}, cursor=cursor, deadline_s=0.001)
            rows, cursor = rows + late['facts']['openings'], late['cursor']
            if late['done']: break
        row, = rows
        self.assertEqual((late['done'], row['feature'], row['ref'], row['anchor']['point']), (True, 's2', want['s2'][0], want['s2'][1]))
        self.assertEqual(row['paths']['tool']['hits'], [])                                 # head and shank are skipped by the names of the cursor

    def test_openings_what_sits_in_a_slot_is_told_from_what_lies_on_it(self):
        bracket = Sub(Blk((0, 0, 0), (40, 40, 10)), Rod(10, 30, -1, 11, 1.5), Rod(30, 30, -1, 11, 1.5))
        slot = lambda oid, x: {'id': oid, 'anchor': {'kind': 'face', 'axis': [0, 0, 1], 'point': [x, 30, 10]}, 'shape': 'slot', 'size': [8.0, 3.0], 'through': True, 'wall': 10.0}
        comps = [Comp('Bracket', [Body('Body1', bracket)]),
                 Comp('ref lock pin', [Body('Body1', Rod(10, 30, 4.0, 14.0, 1.2))]),                      # it stands in the first slot
                 Comp('ref motor', [Body('Body1', Blk((22, 22, 10.0), (38, 38, 30.0)))])]                  # it lies flat on the second one
        facts = self.unit('fusion_openings', comps, features={'holes': [], 'openings': [slot('o1', 10), slot('o2', 30)]})['facts']
        first, second = self.opening(facts, 'o1'), self.opening(facts, 'o2')
        rib = comps[:1] + [Comp('Rib plate', [Body('Body1', Rod(10, 30, 4.0, 14.0, 1.2))])]                # a printed neighbour reaches into the slot
        inside = self.opening(self.unit('fusion_openings', rib, features={'holes': [], 'openings': [slot('o1', 10)]})['facts'], 'o1')['inside']
        self.assertEqual(inside, [{'name': 'Rib plate', 'kind': 'printed', 'approx': False}])               # named with its kind: the judge does not take it for what the slot is for
        self.assertEqual((first['inside'], [h['name'] for h in first['paths']['own']['hits']]),
                         ([{'name': 'ref lock pin', 'kind': 'hardware', 'approx': False}], ['ref lock pin']))       # with its kind: only a measured bought body says what a slot is for
        self.assertEqual((second['inside'], [h['name'] for h in second['paths']['own']['hits']]), ([], ['ref motor']))
        self.assertLess(second['paths']['own']['hits'][0]['at'], 0.2)

    def test_screws_and_washers(self):
        env = self.unit('fusion_screws', self.bracket_scene(), features=self.FEATURES)
        self.assertTrue(env['done'])
        facts = env['facts']
        self.assertEqual((facts['n_screws'], facts['n_washers'], len(facts['screws']), len(facts['washers'])), (2, 1, 2, 1))
        s = facts['screws'][0]
        self.assertEqual((s['ref'], s['size'], s['length'], s['named_length'], s['head'], s['role'], s['washer']),
                         ('ref hardware / screw M3x12 cap (1)', 'M3', 12.0, 12.0, 'cap', 'head', None))
        self.assertEqual(s['anchor'], {'kind': 'screw', 'axis': [0.0, 0.0, 1.0], 'point': [10.0, 10.0, 10.0]})
        self.assertAlmostEqual(s['under_head'], 10.0, delta=0.06)
        self.assertEqual((s['seat']['kind'], s['seat']['share'], s['seat']['recess']), ('flat', 1.0, -3.0))
        self.assertEqual((s['engagement'], s['into']), (2.0, {'kind': 'insert', 'name': 'ref hardware / insert M3x4x5 (1)'}))
        self.assertAlmostEqual(s['tip_room'], 4.5, delta=0.05)                                         # tip at -2, pocket floor at -6.5
        self.assertAlmostEqual(s['air_gap'], 0.0, delta=0.05)
        t = facts['screws'][1]
        self.assertEqual((t['washer'], t['length'], t['named_length']), ('w1', 12.5, 12.0))             # drawn 12.5 long, named 12
        self.assertAlmostEqual(t['under_head'], 10.0, delta=0.06)                                      # measured from under the washer
        w, = facts['washers']
        self.assertEqual((w['id'], w['ref'], w['od'], w['id_d'], w['t'], w['screw']), ('w1', 'ref hardware / washer M3', 7.0, 3.2, 0.5, 'ref hardware / screw M3x12 cap (2)'))
        self.assertEqual(w['anchor'], {'kind': 'face', 'axis': [0.0, 0.0, 1.0], 'point': [30.0, 10.0, 10.0]})
        self.assertEqual((w['landing']['share'], w['landing']['gap'], w['landing']['on'], w['landing']['spot_face_d']), (1.0, 0.0, 'demo/bracket', None))
        self.assertEqual(w['proud']['height'], 3.5)
        self.assertEqual((w['proud']['nearest']['name'], w['proud']['nearest']['kind'], w['proud']['nearest']['part'], w['proud']['nearest']['gap']), ('Pulley', 'printed', 'demo/pulley', 0.5))

    def test_screws_a_counterbore_and_a_washer_that_floats(self):
        scene = self.bracket_scene()
        bracket = Sub(Blk((0, 0, 0), (40, 40, 10)), Rod(10, 10, -1, 11, 1.7), Rod(10, 10, 6.0, 11, 3.25), Rod(30, 10, -1, 11, 1.7))     # D6.5 x 4 at the first hole
        scene[0] = Comp('Bracket', [Body('Body1', bracket)])
        hw = scene[-2].bRepBodies
        hw[0] = Body('screw M3x8 cap (1)', Add(Rod(10, 10, -2.0, 6.0, 1.5), Rod(10, 10, 6.0, 9.0, 2.75)))
        hw[1] = Body('screw M3x12 cap (2)', Add(Rod(30, 10, -2.0, 10.8, 1.5), Rod(30, 10, 10.8, 13.8, 2.75)))
        hw[2] = Body('washer M3', Rod(30, 10, 10.3, 10.8, 3.5, 1.6))                                   # 0.3 above the bracket
        facts = self.unit('fusion_screws', scene)['facts']
        by = {s['ref'].split(' / ')[1]: s for s in facts['screws']}
        up = by['screw M3x12 cap (2)']                                                                 # its washer hangs 0.3 over the part: still this part's screw
        self.assertEqual((up['role'], up['washer'], up['seat']['share']), ('head', 'w1', 0.0)); self.assertAlmostEqual(up['under_head'], 10.0, delta=0.03)
        s = by['screw M3x8 cap (1)']
        self.assertEqual((s['length'], s['named_length'], s['seat']['kind'], s['seat']['share']), (8.0, 8.0, 'counterbore', 1.0))
        self.assertAlmostEqual(s['seat']['d'], 6.5, delta=0.03); self.assertAlmostEqual(s['seat']['recess'], 1.0, delta=0.03)       # 4.0 deep, head 3.0
        self.assertAlmostEqual(s['under_head'], 6.0, delta=0.03)
        w, = facts['washers']
        self.assertEqual((w['landing']['share'], w['landing']['on']), (0.0, 'demo/bracket'))
        self.assertAlmostEqual(w['landing']['gap'], 0.35, delta=0.051)

    def test_screws_seen_from_the_part_that_holds_the_insert(self):
        env = self.unit('fusion_screws', self.bracket_scene(), part='Base')
        roles = [(s['ref'][-2:], s['role'], s['engagement'], s['under_head'], s['seat']) for s in env['facts']['screws']]
        self.assertEqual(roles, [('1)', 'thread', 2.0, None, None), ('2)', 'thread', 2.0, None, None)])
        self.assertEqual(env['facts']['washers'], [])                                                  # the washer lies on the bracket

    def test_screws_a_gap_between_the_clamped_parts_and_a_screw_into_nothing(self):
        scene = self.bracket_scene()
        scene[1] = Comp('Base', [Body('Body1', Sub(Blk((0, 0, -12), (40, 40, -0.35)), Rod(10, 10, -6.5, 1, 2.5), Rod(30, 10, -6.5, 1, 2.5)))])   # 0.35 lower
        s = self.unit('fusion_screws', scene)['facts']['screws'][0]
        self.assertAlmostEqual(s['air_gap'], 0.35, delta=0.05)
        scene = [c for c in self.bracket_scene() if c.name != 'Base']
        scene[-2].bRepBodies[:] = [b for b in scene[-2].bRepBodies if 'insert' not in b.name]
        s = self.unit('fusion_screws', scene)['facts']['screws'][0]
        self.assertEqual((s['engagement'], s['into'], s['tip_room'], s['tip_room_capped']), (0.0, None, 10.0, True))

    # -- an air gap along a screw and what stands in it; the body a thread ends in; the place of a body inside an assembly

    def flange_scene(self, ring_top=11.0, ring=True, shoulder=0.0, bore=27.0, extra=()):
        """A lid screwed to a housing across a 1.0 mm gap. The outer ring of a bearing stands in a bore of the housing on
        its shoulder (z 0), 1.0 proud of the top face, 5 mm from the screw axis: the lid closes on the ring (ring_top
        11.0) or stops short of it. shoulder: where the bore ends (None = the bore goes through: no shoulder)."""
        seat = Rod(0, 0, -21.0, 12.0, bore) if shoulder is None else Rod(0, 0, shoulder, 12.0, bore)
        housing = Sub(Blk((-40, -40, -20), (40, 40, 10)), seat, Rod(0, 0, -21.0, 1.0, 20.0), Rod(32, 0, 2.0, 11.0, 2.5), *[h for h in extra if isinstance(h, Rod) and h.z0 < 10.0])
        lid = Sub(Blk((-40, -40, 11), (40, 40, 16)), Rod(0, 0, 10.0, 17.0, 20.0), Rod(32, 0, 10.0, 17.0, 1.7), *[h for h in extra if isinstance(h, Rod) and h.z1 > 11.0])
        hw = [Body('screw M3x12 cap (1)', Add(Rod(32, 0, 4.0, 16.0, 1.5), Rod(32, 0, 16.0, 19.0, 2.75))), Body('insert M3x4x5 (1)', Rod(32, 0, 5.0, 10.0, 2.5, 1.25))]
        scene = [Comp('Lid', [Body('Body1', lid)]), Comp('Housing', [Body('Body1', housing)]), Comp('ref hardware', hw)]
        return scene + ([Comp('ref bearing ring', [Body('Body1', Rod(0, 0, 0.0, ring_top, 27.0, 20.0))])] if ring else [])

    def gap_of(self, scene, part='Lid', **kw):
        s, = self.unit('fusion_screws', scene, part=part, **kw)['facts']['screws']
        return s, self.judge('screws.shell_bears', {'screws': [s], 'washers': []}, 'demo/' + part.lower())

    def test_screws_a_gap_that_a_ring_bridges_is_told_with_what_stands_in_it(self):
        s, out = self.gap_of(self.flange_scene())
        self.assertAlmostEqual(s['air_gap'], 1.0, delta=0.05)
        row, = s['gaps']
        self.assertAlmostEqual(row['gap'], 1.0, delta=0.05); self.assertAlmostEqual(row['at'], 5.0, delta=0.05)          # the lid is 5 thick under the head
        self.assertEqual(row['between'], [{'name': 'Lid', 'kind': 'printed', 'part': 'demo/lid'}, {'name': 'Housing', 'kind': 'printed', 'part': 'demo/housing'}])
        self.assertEqual((row['search_r'], row['in_gap']), (8.0, [{'name': 'ref bearing ring', 'kind': 'hardware', 'off': 6.0, 'along': [0.0, 0.0], 'lines': 1}]))
        self.assertEqual([(f['verdict'], f['by']) for f in out], [('INFO', ['ref bearing ring'])])       # the lid ends on the ring, the shoulder begins under it
        # the lid stops 0.3 short of the ring: the ring is in the gap and bridges nothing
        s, out = self.gap_of(self.flange_scene(ring_top=10.7))
        body, = s['gaps'][0]['in_gap']
        self.assertEqual((body['name'], body['along'][1]), ('ref bearing ring', 0.0)); self.assertAlmostEqual(body['along'][0], 0.3, delta=0.011)
        self.assertEqual([f['verdict'] for f in out], ['FAIL']); self.assertIn('Lid stands 0.3 mm off it along the screw, Housing presses on it', out[0]['text'])
        # no ring at all: air; and a ring farther from the screw than the search radius is not looked at
        s, out = self.gap_of(self.flange_scene(ring=False))
        self.assertEqual(s['gaps'][0]['in_gap'], [])
        self.assertEqual([f['verdict'] for f in out], ['FAIL'])
        s, out = self.gap_of(self.flange_scene(), params={'screws.bridge_search_r': 4.0})
        self.assertEqual((s['gaps'][0]['search_r'], s['gaps'][0]['in_gap']), (4.0, []))
        # a screw with no gap has an empty list, and the neighbour that only holds the thread has none
        scene = self.bracket_scene()
        self.assertEqual(self.unit('fusion_screws', scene)['facts']['screws'][0]['gaps'], [])
        self.assertNotIn('gaps', self.unit('fusion_screws', scene, part='Base')['facts']['screws'][0])

    def test_screws_a_body_that_only_one_part_presses_on_bridges_nothing(self):
        """What touches both parts somewhere and carries no clamp load along the screw."""
        # the ring is pressed by the lid; the shoulder under it stands 0.85 lower: the flanges would meet first
        s, out = self.gap_of(self.flange_scene(shoulder=-0.85))
        body, = s['gaps'][0]['in_gap']
        self.assertEqual(body['along'][0], 0.0); self.assertAlmostEqual(body['along'][1], 0.85, delta=0.011)
        self.assertEqual([f['verdict'] for f in out], ['FAIL']); self.assertIn('Lid presses on it, Housing stands 0.85 mm off it along the screw', out[0]['text'])
        # the ring sits in a through bore with a fit clearance and no shoulder: sideways it is 0.07 from the housing, along the screw nothing follows it
        s, out = self.gap_of(self.flange_scene(shoulder=None, bore=27.07))
        self.assertEqual(s['gaps'][0]['in_gap'][0]['along'], [0.0, None])
        self.assertEqual([f['verdict'] for f in out], ['FAIL']); self.assertIn('Housing is not met on a line along the screw through it', out[0]['text'])
        # a dowel pin through both parts 6 mm from the screw, in a through hole of the lid and a blind hole of the housing
        pin = [Rod(26, 0, 3.0, 17.0, 1.5)]
        scene = self.flange_scene(ring=False, extra=pin) + [Comp('ref dowel pin D3 x 10', [Body('Body1', Rod(26, 0, 4.0, 14.0, 1.5))])]
        s, out = self.gap_of(scene)
        body, = s['gaps'][0]['in_gap']
        self.assertEqual((body['name'], body['along'][0]), ('ref dowel pin D3 x 10', None)); self.assertAlmostEqual(body['along'][1], 1.0, delta=0.011)
        self.assertEqual([f['verdict'] for f in out], ['FAIL'])
        # the same pin drawn into both parts with no holes at all: two bodies drawn into each other press nowhere
        scene = self.flange_scene(ring=False) + [Comp('ref dowel pin D3 x 10', [Body('Body1', Rod(26, 0, 4.0, 14.0, 1.5))])]
        s, out = self.gap_of(scene)
        self.assertEqual(s['gaps'][0]['in_gap'][0]['along'], [None, None])
        self.assertEqual([f['verdict'] for f in out], ['FAIL'])
        # a recognised screw beside the gap, known by the name of its component only, is never what the parts close on
        scene = self.flange_scene(ring=False, extra=[Rod(26, 0, 3.0, 17.0, 1.7)])
        scene.append(Comp('ref screw M3x12 cap', [Body('Body1', Add(Rod(26, 0, 4.0, 16.0, 1.5), Rod(26, 0, 16.0, 19.0, 2.75)))]))
        screws = self.unit('fusion_screws', scene, part='Lid')['facts']['screws']
        self.assertEqual(len(screws), 2)
        self.assertEqual([c['name'] for s in screws for row in s['gaps'] for c in row['in_gap']], [])

    def test_screws_a_hollow_in_one_shell_with_a_board_on_its_floor_is_air(self):
        """The incident of the rule: one shell, a gap at its seam screw. A bought body that lies on the floor of the hollow
        and stops short of its roof touches the shell on both sides of the gap (it is one body) and closes nothing."""
        shell = Sub(Blk((-20, -20, 0), (20, 20, 16)), Blk((-15, -15, 5), (15, 15, 8)), Rod(0, 0, 4.0, 17.0, 1.7), Rod(0, 0, -1.0, 5.5, 2.5))
        hw = [Body('screw M3x12 cap (1)', Add(Rod(0, 0, 3.0, 16.0, 1.5), Rod(0, 0, 16.0, 19.0, 2.75))), Body('insert M3x4x5 (1)', Rod(0, 0, 0.0, 5.0, 2.5, 1.25))]
        scene = [Comp('Shell', [Body('Body1', shell)]), Comp('ref hardware', hw), Comp('ref controller board', [Body('Body1', Blk((4, -6, 5), (14, 6, 7)))])]
        s, out = self.gap_of(scene, part='Shell')
        row, = s['gaps']
        self.assertAlmostEqual(row['gap'], 3.0, delta=0.05)
        self.assertEqual([w['part'] for w in row['between']], ['demo/shell', 'demo/shell'])
        body, = row['in_gap']
        self.assertEqual((body['name'], body['along'][1]), ('ref controller board', 0.0)); self.assertAlmostEqual(body['along'][0], 1.0, delta=0.011)
        self.assertEqual([f['verdict'] for f in out], ['FAIL'])
        self.assertIn('Shell on the head side stands 1 mm off it along the screw, Shell on the thread side presses on it', out[0]['text'])
        # a spacer that fills the hollow from floor to roof is what the screw closes on
        scene[-1] = Comp('ref spacer block', [Body('Body1', Blk((4, -6, 5), (14, 6, 8)))])
        s, out = self.gap_of(scene, part='Shell')
        self.assertEqual(s['gaps'][0]['in_gap'][0]['along'], [0.0, 0.0])
        self.assertEqual([(f['verdict'], f['by']) for f in out], [('INFO', ['ref spacer block'])])

    def test_screws_a_gap_is_measured_whole_or_by_the_next_call_never_half(self):
        """No distance call: only point tests. When the time of the call is used up inside a screw that is not the first of
        the call, the unit stops with its cursor on that screw; the first screw of a call is written and marked."""
        mods = fake_adsk(self.flange_scene())
        app = mods['adsk.core'].Application.get()
        def refuse(a, b): raise AssertionError('the gap is measured without a distance call')
        app.measureManager = types.SimpleNamespace(measureMinimumDistance=refuse)
        s, = self.unit_with(mods, 'fusion_screws', part='Lid')['facts']['screws']
        self.assertEqual(s['gaps'][0]['in_gap'][0]['along'], [0.0, 0.0])
        g = {}                                                                                         # the functions of the unit, with a clock that has run out
        text = ''
        for name in ('_geom.py', '_geom_rings.py', 'fusion_screws.py'):
            with open(os.path.join(ROOT, 'tools', 'gate', 'probes', name), encoding='utf-8') as f: text += f.read() + '\n'
        exec(compile(text, 'screws', 'exec'), g)
        g['gate_time_up'] = lambda job, reserve=1.5: True
        ring = {'name': 'ref ring', 'kind': 'hardware', 'own': False, 'printed': False, 'box': ((-27, -27, 0), (27, 27, 11)), '_inside': Rod(0, 0, 0.0, 11.0, 27.0, 20.0).has}
        lid = {'name': 'Lid', 'kind': 'printed', 'part': 'demo/lid', 'own': True, 'printed': True, 'box': ((-40, -40, 11), (40, 40, 16)), '_inside': Blk((-40, -40, 11), (40, 40, 16)).has}
        housing = {'name': 'Housing', 'kind': 'printed', 'part': 'demo/housing', 'own': False, 'printed': True, 'box': ((-40, -40, -20), (40, 40, 0)), '_inside': Blk((-40, -40, -20), (40, 40, 0)).has}
        args = ([(1.0, 0, 1, 5.0)], [lid, housing, ring], (32.0, 0.0, 16.0), (0.0, 0.0, 1.0), {'params': {}})
        with self.assertRaises(g['_ScrLate']): g['_scr_gaps'](*args, may_stop=True)
        row, = g['_scr_gaps'](*args)[0]['in_gap']
        self.assertEqual((row['along'], row['lines'], row['late']), ([None, None], 0, True))
        out = self.judge('screws.shell_bears', {'screws': [dict(s, gaps=[dict(s['gaps'][0], in_gap=[row])])], 'washers': []}, 'demo/lid')
        self.assertEqual([f['verdict'] for f in out], ['FAIL']); self.assertIn('was not measured (the time of that call was used up): collect the facts again', out[0]['text'])
        # the whole unit asks through a tape: a budget that is used up from the start stops inside the first screw after 48 point
        # tests, the next call goes on with it, and no row is ever marked "late"
        def scene():
            sc = self.flange_scene(extra=[Rod(-32, 0, 3.0, 17.0, 1.7)])
            sc[2].bRepBodies.append(Body('screw M3x12 cap (2)', Add(Rod(-32, 0, 4.0, 16.0, 1.5), Rod(-32, 0, 16.0, 19.0, 2.75))))
            return sc
        one = self.unit('fusion_screws', scene(), part='Lid')
        self.assertEqual((one['done'], len(one['facts']['screws'])), (True, 2))
        env = self.unit('fusion_screws', scene(), part='Lid', deadline_s=1)
        self.assertEqual((env['done'], env['cursor']['i'], env['cursor']['tape']['n'], env['facts']['screws']), (False, 0, 48, []))
        facts, calls, _ = self.whole('fusion_screws', scene, part='Lid', deadline_s=1)
        self.assertGreater(calls, 5); self.assertEqual(facts, one['facts'])
        self.assertNotIn('"late"', json.dumps(facts))
        self.assertEqual([s['gaps'][0]['in_gap'][0]['along'] for s in facts['screws']], [[0.0, 0.0], [0.0, 0.0]])
        for limit in (333, 2000):
            facts, calls, _ = self.whole('fusion_screws', scene, part='Lid', args={'tape_limit': limit})
            self.assertEqual(facts, one['facts'], limit)
        env = self.unit('fusion_screws', scene(), part='Lid', cursor=1)                                # the cursor of an older plan: the index
        self.assertEqual((env['done'], len(env['facts']['screws']), env['facts']['rejected'], env['facts']['fastener_holes']), (True, 1, [], []))

    def test_screws_a_neighbour_that_changes_inside_its_box_between_two_calls_starts_the_screw_again(self):
        """A call stops inside the screw. Then a pocket is cut into the housing round the screw, inside the same box: the gap
        between the clamped parts is 3 mm now. A screw joined from the two states would read 1 mm."""
        cut = lambda: self.flange_scene(extra=[Rod(32, 0, 8.0, 11.0, 6.0)])
        before, after = self.unit('fusion_screws', self.flange_scene(), part='Lid'), self.unit('fusion_screws', cut(), part='Lid')
        self.assertAlmostEqual(before['facts']['screws'][0]['air_gap'], 1.0, delta=0.05); self.assertAlmostEqual(after['facts']['screws'][0]['air_gap'], 3.0, delta=0.05)
        for limit in (700, 1100):
            first = self.unit('fusion_screws', self.flange_scene(), part='Lid', args={'tape_limit': limit})
            cur = first['cursor']
            self.assertEqual((first['done'], cur['i'], len(cur['world'])), (False, 0, 16)); self.assertIn('0', cur['asked'])      # the housing answered point tests
            facts, notes = self.rest('fusion_screws', cut, first, part='Lid', args={'tape_limit': limit})
            self.assertEqual(json.dumps(facts['screws'], sort_keys=True), json.dumps(after['facts']['screws'], sort_keys=True), limit)
            self.assertEqual(len([n for n in notes if 'is measured again from its start' in n]), 1)
            facts, notes = self.rest('fusion_screws', self.flange_scene, first, part='Lid', args={'tape_limit': limit})     # nothing changed: it goes on
            self.assertEqual(facts['screws'], before['facts']['screws']); self.assertFalse([n for n in notes if 'measured again' in n])
        env = first                                                                  # a body that moves in every call: counted, then an error
        for n in (1, 2):
            env = self.unit('fusion_screws', self.flange_scene(ring_top=11.0 - 0.01 * n), part='Lid', cursor=env['cursor'], args={'tape_limit': 300})
            self.assertEqual(env['cursor']['again'], n)
        with self.assertRaises(AssertionError) as e: self.unit('fusion_screws', self.flange_scene(ring_top=10.9), part='Lid', cursor=env['cursor'], args={'tape_limit': 300})
        self.assertIn('met other bodies than the call before in three calls in a row', str(e.exception))

    def test_screws_and_washers_cut_by_the_clock_give_the_facts_of_one_call(self):
        one = self.unit('fusion_screws', self.bracket_scene(), features=self.FEATURES)
        self.assertTrue(one['done']); self.assertTrue(one['facts']['screws']); self.assertTrue(one['facts']['washers'])
        for limit in (57, 400):
            facts, calls, _ = self.whole('fusion_screws', self.bracket_scene, features=self.FEATURES, args={'tape_limit': limit})
            self.assertEqual(json.dumps(facts, sort_keys=True), json.dumps(one['facts'], sort_keys=True), limit); self.assertGreater(calls, 2)

    def test_screws_the_body_a_thread_ends_in_when_nothing_receives_it(self):
        scene = self.bracket_scene()
        scene[-2].bRepBodies[:] = [b for b in scene[-2].bRepBodies if 'insert' not in b.name]         # the base keeps its pockets, the inserts are not modelled
        facts = self.unit('fusion_screws', scene, features=self.FEATURES)['facts']
        s = facts['screws'][0]
        self.assertEqual((s['engagement'], s['into'], s['tip_at']), (0.0, None, {'name': 'Base', 'kind': 'printed', 'part': 'demo/base'}))
        out = self.judge('screws.engagement', facts, 'demo/bracket')
        self.assertEqual([f['verdict'] for f in out], ['FAIL', 'FAIL'])
        self.assertIn('It passes this part through the clearance h1 (M3) at (10, 10, 10); the end of its thread stands in demo/base', out[0]['text'])
        with_inserts = self.unit('fusion_screws', self.bracket_scene(), features=self.FEATURES)['facts']['screws'][0]
        self.assertNotIn('tip_at', with_inserts)                                                      # asked only where nothing receives the thread
        alone = [c for c in scene if c.name != 'Base']
        self.assertIsNone(self.unit('fusion_screws', alone)['facts']['screws'][0]['tip_at'])           # the thread ends in open air

    def test_openings_a_body_inside_an_assembly_carries_its_place(self):
        def facts_with(outer, inner, top='Cover'):
            """The bracket scene with the body "Rotor" two levels down in a bought assembly: in the component `inner` inside `outer`."""
            scene = self.bracket_scene() + [Comp('Rotor', [Body('Body1', Blk((5, 5, 40), (15, 15, 50)))])]
            scene[2].name = top
            mods = fake_adsk(scene)
            occs = mods['adsk.core'].Application.get().documents[0].products.itemByProductType('x').rootComponent.allOccurrences
            up = Occ(Comp(inner), n=2); up.assemblyContext = Occ(Comp(outer))
            occs[-1].assemblyContext = up
            occs[-1].fullPathName = '%s:1+%s:2+Rotor:1' % (outer, inner)                               # what Fusion prints: no way to cut it by "+" and ":"
            return self.unit_with(mods, 'fusion_openings', features=self.FEATURES)['facts']
        facts = facts_with('ref gearbox', 'ref motor 57')
        by = {h['name']: h for h in self.opening(facts, 'h1')['paths']['own']['hits']}
        self.assertEqual((by['Rotor']['kind'], by['Rotor']['in']), ('bought', 'ref gearbox / ref motor 57'))
        self.assertNotIn('in', by['Cover'])                                                           # a top-level component has no place to tell
        census = {'holes': self.FEATURES['holes'], 'openings': self.FEATURES['openings']}
        head = [f for f in self.judge('access.screw_path', facts, 'demo/bracket', census) if f['ask']]
        self.assertEqual([f['by'] for f in head], [['Cover', 'ref gearbox / ref motor 57 / Rotor']])  # one row for both covers, the rotor with its assembly
        # names that hold "+" and ":" themselves are one name each, nested or at the top level
        facts = facts_with('Drive unit (A + B)', 'ref shaft: pin 6x50 + sleeves + 2 x bearing', top='Cover - clamp + foot')
        by = {h['name']: h for h in self.opening(facts, 'h1')['paths']['own']['hits']}
        self.assertEqual(by['Rotor']['in'], 'Drive unit (A + B) / ref shaft: pin 6x50 + sleeves + 2 x bearing')
        self.assertNotIn('in', by['Cover - clamp + foot'])
        head = [f for f in self.judge('access.screw_path', facts, 'demo/bracket', census) if f['ask']]
        self.assertEqual([f['by'] for f in head], [['Cover - clamp + foot', 'Drive unit (A + B) / ref shaft: pin 6x50 + sleeves + 2 x bearing / Rotor']])

    def test_the_place_of_an_occurrence_is_read_from_the_objects_and_cut_from_the_text_only_as_a_last_resort(self):
        g = {}
        with open(os.path.join(ROOT, 'tools', 'gate', 'probes', '_fusion.py'), encoding='utf-8') as f: text = f.read()
        mods = fake_adsk([])
        saved = {k: sys.modules.get(k) for k in mods}
        sys.modules.update(mods)
        try:
            exec(compile(text, '_fusion', 'exec'), g)
        finally:
            for k, v in saved.items():
                if v is None: sys.modules.pop(k, None)
                else: sys.modules[k] = v
        above = g['gate_above']
        self.assertEqual(above(None), [])
        top = Occ(Comp('ref HW - clamp + foot'))
        self.assertEqual(above(top), [])
        leaf = Occ(Comp('Rotor')); leaf.assemblyContext = Occ(Comp('Motor: 57 + brake'), n=2); leaf.assemblyContext.assemblyContext = Occ(Comp('Gearbox'))
        self.assertEqual(above(leaf), ['Gearbox', 'Motor: 57 + brake'])
        class Broken:                                                                                  # an occurrence whose context cannot be read
            fullPathName = 'Gearbox:1+Motor: 57 + brake:12+Rotor:1'
            @property
            def assemblyContext(self): raise RuntimeError('no context')
        self.assertEqual(above(Broken()), ['Gearbox', 'Motor: 57 + brake'])

    def test_gap_to_the_other_motion_group(self):
        groups = [{'key': 'base', 'patterns': ['^Bracket', '^Base']}, {'key': 'slide', 'patterns': ['^Pulley', '^ref motor']}]
        env = self.unit('fusion_gap', self.bracket_scene(), groups=groups)
        facts = env['facts']
        self.assertEqual((facts['own_group'], facts['margin'], facts['n_bodies']), ('base', 5.0, 2))
        rows = {g['name']: g for g in facts['gaps']}
        p = rows['Pulley']
        self.assertEqual((p['kind'], p['part'], p['group'], p['gap'], p['approx'], p['dir']), ('printed', 'demo/pulley', 'slide', 0.2, False, [0.0, 0.0, 1.0]))
        self.assertEqual(p['point'][2], 10.0)
        m = rows['ref motor']
        self.assertEqual((m['kind'], m['gap'], m['approx']), ('mesh', 5.0, True))
        self.assertEqual(self.unit('fusion_gap', self.bracket_scene(), groups=[{'key': 'slide', 'patterns': ['^Pulley']}])['facts'],
                         {'own_group': None, 'margin': 5.0, 'gaps': [], 'groups': 1})                # the part is in no group: nothing measured
        first = self.unit('fusion_gap', self.bracket_scene(), groups=groups, deadline_s=0.001)
        self.assertEqual((first['done'], first['cursor'], len(first['facts']['gaps'])), (False, 1, 1))

    def test_gap_does_not_start_a_measurement_that_the_slowest_one_says_will_not_fit(self):
        """One distance between two bodies is one API call. After a slow one the next is not started with less time left than
        1.5 times that: here every measurement takes 4 s of a 20 s call, and the fifth would end exactly at the budget."""
        groups = [{'key': 'base', 'patterns': ['^Bracket']}, {'key': 'slide', 'patterns': ['^Slider']}]
        scene = [Comp('Bracket', [Body('Body1', Blk((0, 0, 0), (40, 40, 10)))])] + [Comp('Slider %d' % n, [Body('Body1', Blk((5 * n, 0, 10.5), (5 * n + 4, 40, 14)))]) for n in range(7)]
        mods = fake_adsk(scene)
        app = mods['adsk.core'].Application.get()
        clock = {'t': 1000.0, 'asked': 0}

        def slow(a, b):
            clock['t'] += 4.0; clock['asked'] += 1
            return measure(a, b)
        app.measureManager = types.SimpleNamespace(measureMinimumDistance=slow)
        with mock.patch('time.time', lambda: clock['t']):
            env = self.unit_with(mods, 'fusion_gap', groups=groups)
        self.assertEqual((env['done'], env['cursor'], clock['asked'], env['elapsed_s']), (False, 4, 4, 16.0))      # 4 s left, 6 s wanted: the fifth waits
        facts, notes = self.rest('fusion_gap', scene, env, groups=groups)
        self.assertEqual(len(env['facts']['gaps'] + facts['gaps']), 7)               # every neighbour is measured, by the next call

    # -- thin faces and the export

    def plane(self, w, h, at, normal=(0.0, 0.0, 1.0), loops=None):
        edge = lambda ln: types.SimpleNamespace(evaluator=types.SimpleNamespace(getParameterExtents=lambda: (True, 0.0, 1.0), getLengthAtParameter=lambda a, b: (True, ln / 10.0)),
                                                geometry=types.SimpleNamespace(objectType='adsk::core::Line3D'))
        return types.SimpleNamespace(geometry=types.SimpleNamespace(objectType='adsk::core::Plane', surfaceType=0, normal=Pt(*normal)), area=w * h / 100.0,
                                     centroid=cm(at), pointOnFace=cm(at), evaluator=types.SimpleNamespace(getNormalAtPoint=lambda p: (True, Pt(*normal))),
                                     loops=L(loops if loops is not None else [types.SimpleNamespace(edges=L([1, 2, 3, 4]))]), edges=L([edge(w), edge(h), edge(w), edge(h)]))

    def circle_loop(self, r, c=(0, 0, 0)):
        return types.SimpleNamespace(edges=L([types.SimpleNamespace(geometry=types.SimpleNamespace(objectType='adsk::core::Circle3D', radius=r / 10.0, center=cm(c)))]))

    def test_thin_faces(self):
        faces = [self.plane(10.0, 0.3, (5, 0.15, 10)),                                                 # a sliver
                 self.plane(40.0, 40.0, (20, 20, 0), (0.0, 0.0, -1.0)),                                # the bottom
                 self.plane(1.0, 1.0, (9, 9, 9), loops=[self.circle_loop(2.2), self.circle_loop(1.8)]),     # a lead-in ledge: an annulus
                 self.plane(1.0, 0.4, (3, 3, 3), loops=[self.circle_loop(0.5)]),                       # a tiny disc
                 self.plane(3.0, 3.0, (4, 4, 4), loops=[self.circle_loop(1.5)]),                       # a peg top
                 types.SimpleNamespace(geometry=types.SimpleNamespace(objectType='adsk::core::Cylinder', surfaceType=1), area=1.0, centroid=cm((0, 0, 0)))]
        env = self.unit('fusion_thin', [Comp('Bracket', [Body('Body1', Blk((0, 0, 0), (40, 40, 10)), faces=faces)])])
        facts = env['facts']
        self.assertEqual((facts['width_cap'], facts['lumps'], facts['shells'], facts['n_faces'], facts['non_manifold_edges']), (2.0, 1, 1, 6, 0))
        self.assertEqual(facts['faces'], [{'anchor': {'kind': 'face', 'axis': [0.0, 0.0, 1.0], 'point': [5.0, 0.15, 10.0]}, 'width': 0.291, 'area': 3.0, 'shape': 'strip'},
                                          {'anchor': {'kind': 'face', 'axis': [0.0, 0.0, 1.0], 'point': [3.0, 3.0, 3.0]}, 'width': 0.286, 'area': 0.4, 'shape': 'disc'}])
        capped = self.unit('fusion_thin', [Comp('Bracket', [Body('Body1', Blk((0, 0, 0), (40, 40, 10)), faces=faces)])], args={'width_cap': 0.29})
        self.assertEqual([f['shape'] for f in capped['facts']['faces']], ['disc'])

    def cube_mesher(self, drop=0, size=10.0):
        s = size / 10.0
        nodes = [(x, y, z) for x in (0, s) for y in (0, s) for z in (0, s)]
        quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
        tris = [t for a, b, c, d in quads for t in ((a, b, c), (a, c, d))]
        tris = tris[:len(tris) - drop]
        mesh = types.SimpleNamespace(nodeCoordinatesAsFloat=[c for n in nodes for c in n], nodeIndices=[i for t in tris for i in t])
        return lambda: types.SimpleNamespace(setQuality=lambda q: None, calculate=lambda: mesh)

    def export(self, mesher, sig=None, volume=1.0):
        stl = os.path.join(self.d, 'gate', 'run', 'quarantine', 'bracket.stl')
        body = Body('Body1', Blk((0, 0, 0), (10, 10, 10)), volume=volume, mesher=mesher)
        env = self.unit('fusion_export', [Comp('Bracket', [body])], args={'stl': stl, 'name': 'bracket'}, sig=sig)
        return env, stl

    def test_export_writes_a_checked_stl(self):
        env, stl = self.export(self.cube_mesher())
        f = env['facts']
        self.assertEqual((f['written'], f['stl'], f['why_not'], f['triangles'], f['open_edges'], f['shells'], f['quality']), (True, stl, None, 12, 0, 1, 'High'))
        self.assertEqual((f['volume_mesh'], f['volume_brep'], f['bbox_mesh'], f['bbox_brep']), (1000.0, 1000.0, [[0.0, 0.0, 0.0], [10.0, 10.0, 10.0]], [[0.0, 0.0, 0.0], [10.0, 10.0, 10.0]]))
        self.assertEqual(f['header'], 'Fusion Demo Assembly v7 bracket'); self.assertEqual(f['sig_before'], f['sig_after']); self.assertEqual(env['sig'], f['sig_after'])
        with open(stl, 'rb') as fh: data = fh.read()
        self.assertEqual(len(data), 84 + 50 * 12)
        self.assertEqual(data[:80].rstrip(), b'Fusion Demo Assembly v7 bracket'); self.assertEqual(struct.unpack('<I', data[80:84])[0], 12)
        self.assertFalse(os.path.exists(stl + '.tmp'))
        again, _ = self.export(self.cube_mesher(), sig=f['sig_after'])                                 # the signature it was checked at
        self.assertTrue(again['facts']['written'])

    def test_export_refuses_a_changed_body_and_a_bad_mesh(self):
        env, stl = self.export(self.cube_mesher(), sig='0' * 64)
        self.assertFalse(env['facts']['written']); self.assertIn('changed since it was checked', env['facts']['why_not']); self.assertFalse(os.path.exists(stl))
        env, stl = self.export(self.cube_mesher(drop=1))
        self.assertEqual((env['facts']['written'], env['facts']['open_edges']), (False, 3)); self.assertIn('not closed', env['facts']['why_not'])
        self.assertEqual([t['quality'] for t in env['facts']['tried']], ['High', 'Normal', 'VeryHigh']); self.assertFalse(os.path.exists(stl))
        env, _ = self.export(self.cube_mesher(), volume=1.2)                                           # the body is 20 % more than the mesh
        self.assertIn('more than 1 % off', env['facts']['why_not'])
        env, _ = self.export(self.cube_mesher(size=9.0), volume=0.729)                                 # right volume, a millimetre of the body missing
        self.assertIn('bounding box', env['facts']['why_not'])
        body = Body('Body1', Blk((0, 0, 0), (10, 10, 10)), mesher=self.cube_mesher())
        env = self.unit('fusion_export', [Comp('Bracket', [body])], args={'stl': 'relative/bracket.stl'})
        self.assertIn('absolute path', env['facts']['why_not'])

    def test_export_reads_every_face_once_and_the_whole_body_again_after_the_mesh(self):
        calls = {'faces': 0}
        class Counted(Body):
            @property
            def faces(self):
                calls['faces'] += 1
                return Body.faces.fget(self)
        stl = os.path.join(self.d, 'gate', 'run', 'quarantine', 'bracket.stl')
        body = Counted('Body1', Blk((0, 0, 0), (10, 10, 10)), mesher=self.cube_mesher())
        f = self.unit('fusion_export', [Comp('Bracket', [body])], args={'stl': stl, 'name': 'bracket'})['facts']
        quiet = calls['faces']
        self.assertEqual((f['written'], f['sig_before']), (True, f['sig_after']))
        # the body is changed by the mesh call itself (a stand-in for anything that edits it inside the script): its volume moves
        changing = Counted('Body1', Blk((0, 0, 0), (10, 10, 10)))
        def mesher():
            inner = self.cube_mesher()()
            def calculate():
                changing._volume = 1.005                                                   # one of the whole-body values moves
                return inner.calculate()
            return types.SimpleNamespace(setQuality=inner.setQuality, calculate=calculate)
        changing._mesher = mesher
        calls['faces'] = 0
        f = self.unit('fusion_export', [Comp('Bracket', [changing])], args={'stl': stl + '.2', 'name': 'bracket'})['facts']
        self.assertGreater(calls['faces'], quiet)                                          # then, and only then, every face is read a second time
        self.assertEqual(f['written'], False); self.assertIn('changed while it was meshed', f['why_not']); self.assertNotEqual(f['sig_before'], f['sig_after'])
        self.assertFalse(os.path.exists(stl + '.2'))

    def test_export_does_not_start_a_mesh_when_the_signature_used_up_the_call(self):
        stl = os.path.join(self.d, 'gate', 'run', 'quarantine', 'bracket.stl')
        body = Body('Body1', Blk((0, 0, 0), (10, 10, 10)), mesher=lambda: self.fail('no mesh may be started'))
        env = self.unit('fusion_export', [Comp('Bracket', [body])], args={'stl': stl, 'name': 'bracket'}, deadline_s=0.001)
        f = env['facts']
        self.assertEqual((env['done'], f['written'], f['tried']), (True, False, [])); self.assertIn('took the time of the call: nothing was meshed', f['why_not'])
        self.assertFalse(os.path.exists(stl))

    # -- one edge or one body that cannot be read must not end a unit for every other body

    def broken(self, comps, whole=()):
        """The same scene with one more edge on every ref body: an edge that has no geometry (the tip of a cone can be
        one). A body named in `whole` cannot list its edges at all."""
        class OneBadEdge(Body):
            @property
            def edges(self):
                if self.name in whole:
                    raise RuntimeError('3 : object is invalid')
                return L(list(Body.edges.fget(self)) + [types.SimpleNamespace(geometry=None, faces=L([1, 2]))])
        out = []
        for c in comps:
            if c.name.startswith('ref '):
                c = Comp(c.name, [OneBadEdge(b.name, b.shape, b.isLightBulbOn, b._volume, b._faces, b._mesher) for b in c.bRepBodies], list(c.meshBodies))
            out.append(c)
        return out

    def strip(self, facts):
        return json.loads(json.dumps(facts))

    def test_an_edge_without_geometry_does_not_end_a_unit(self):
        scene = self.bracket_scene()
        for probe in ('fusion_screws', 'fusion_openings'):
            good = self.unit(probe, scene, features=self.FEATURES)
            bad = self.unit(probe, self.broken(scene), features=self.FEATURES)           # self.unit() fails on an envelope with an error
            self.assertEqual(self.strip(bad['facts']), self.strip(good['facts']), probe)
        good = self.unit('fusion_bearings', self.bearing_scene(), part='Housing')
        bad = self.unit('fusion_bearings', self.broken(self.bearing_scene()), part='Housing')
        self.assertEqual(self.strip(bad['facts']), self.strip(good['facts']))

    def test_a_body_that_cannot_be_read_is_named_and_the_others_are_still_measured(self):
        scene = self.broken(self.bracket_scene(), whole=('screw M3x12 cap (2)',))        # this one cannot list its edges
        f = self.unit('fusion_screws', scene, features=self.FEATURES)['facts']
        first, second = f['screws']
        self.assertEqual((first['ref'], first['size'], first['length']), ('ref hardware / screw M3x12 cap (1)', 'M3', 12.0))       # measured as before
        # the other is not dropped: its own name says screw and its shape says nothing, so it is listed with "why" and no numbers
        self.assertEqual((second['ref'], second['role'], second['engagement']), ('ref hardware / screw M3x12 cap (2)', 'thread', None)); self.assertTrue(second['why'])
        o = self.unit('fusion_openings', scene, features=self.FEATURES)['facts']
        self.assertEqual([s['ref'] for s in o['screws']], ['ref hardware / screw M3x12 cap (1)'])        # a head that cannot be read has no opening
        b = self.unit('fusion_bearings', self.broken(self.bearing_scene(), whole=('Body1',)), part='Housing')['facts']
        self.assertEqual(b['bearings'][0]['by'] if b['bearings'] else None, 'name')                        # known by its name only: the judge says "model it as a ring"

    def test_the_edges_of_hardware_far_from_the_part_are_not_read(self):
        reads = {}
        class Counted(Body):
            @property
            def edges(self):
                reads[self.name] = reads.get(self.name, 0) + 1
                return Body.edges.fget(self)
        screw = lambda x, y: Add(Rod(x, y, -2.0, 10.0, 1.5), Rod(x, y, 10.0, 13.0, 2.75))
        scene = self.bracket_scene()
        scene[5] = Comp('ref hardware', list(scene[5].bRepBodies) + [
            Counted('screw M3x12 cap (8)', screw(75, 10)), Counted('washer M3 (8)', Rod(75, 10, 10.0, 10.5, 3.5, 1.6)),      # 35 mm beside the part: in the 45 mm world
            Counted('insert M3x4x5 (8)', Rod(75, 10, -4.0, 0.0, 2.5, 1.25))])
        f = self.unit('fusion_screws', scene, features=self.FEATURES)['facts']
        self.assertEqual((f['n_screws'], f['n_washers']), (2, 1))                         # the two screws and the washer at the part, as before
        self.assertEqual(reads, {})                                                       # and no edge of the three far bodies was read
        housing = self.bearing_scene(extra=[Comp('Housing 6005 cover', [Counted('Body1', Blk((-40, -40, 20), (40, 40, 24)), faces=list(range(120)))])])
        b = self.unit('fusion_bearings', housing, part='Housing')['facts']
        self.assertEqual(([x['ref'] for x in b['bearings']], reads), (['ref bearing 6005'], {}))       # a printed part named after the bearing, 120 faces: not read, not listed

    # -- after the review: the shared walker, exact geometry names, the washer's own view of a clamp, a closed screw hole

    def clamp_scene(self, setback):
        """A D12 washer under a screw into a column, beside a 6005 whose outer ring it reaches over by 2 mm. The column's
        face beside the ring lies `setback` below the ring face (z 12)."""
        column = Sub(Blk((-40, -40, -5), (60, 40, 12.0 - setback)), Rod(0, 0, 0.0, 20.0, 23.5), Rod(0, 0, -6.0, 0.001, 20.0), Rod(27.5, 0, -1, 20, 1.7))
        return [Comp('Column', [Body('Body1', column)]),
                Comp('ref bearing 6005', [Body('Body1', Rod(0, 0, 0.0, 12.0, 23.5, 12.5))]),
                Comp('ref hardware', [Body('screw M3x12 cap (1)', Add(Rod(27.5, 0, 3.0, 13.5, 1.5), Rod(27.5, 0, 13.5, 16.5, 2.75))),
                                      Body('washer M3 wide', Rod(27.5, 0, 12.0, 13.5, 6.0, 1.6))])]

    def judge_washers(self, facts):
        import gate_judges, gate_registry, gate_params
        reg = gate_registry.load_default()
        rule = reg['rules']['washers.landing']
        return gate_judges.run_judge(rule, facts, gate_params.values(gate_params.resolve(reg)), {'part': 'demo/column', 'rule': rule['id'], 'tables': reg['tables']})

    def test_a_clamp_washer_is_judged_by_what_lies_beside_its_ring(self):
        for setback, verdicts in ((1.0, []), (0.3, ['FAIL']), (0.0, ['FAIL'])):
            facts = self.unit('fusion_screws', self.clamp_scene(setback), part='Column')['facts']
            w, = facts['washers']
            self.assertEqual(w['landing']['on_bearing'], 'ref bearing 6005', setback)
            beside = [c for c in w['landing']['covers'] if not c['bearing']]
            self.assertEqual([(c['name'], c['kind']) for c in beside], [('Column', 'printed')])
            self.assertAlmostEqual(beside[0]['gap'], setback, delta=0.11)
            out = self.judge_washers(facts)
            self.assertEqual([f['verdict'] for f in out], verdicts, (setback, out))
            if verdicts: self.assertEqual((out[0]['limit'], out[0]['by']), (1.0, ['demo/column']))

    def test_a_washer_over_a_relieved_pocket_lies_on_its_landing(self):
        plate = Sub(Blk((0, 0, 0), (40, 40, 10)), Rod(20, 20, 9.46, 11, 3.75), Rod(20, 20, 3.0, 11, 2.2))           # D7.5 x 0.54 relief over a D4.4 pocket
        scene = [Comp('Plate', [Body('Body1', plate)]),
                 Comp('ref hardware', [Body('washer M3 large', Rod(20, 20, 10.0, 10.8, 4.5, 1.6)), Body('unused', Rod(90, 90, 0, 1, 1))])]
        w, = self.unit('fusion_screws', scene, part='Plate')['facts']['washers']
        self.assertEqual((w['landing']['share'], w['landing']['on_bearing'], w['landing']['gap']), (1.0, None, 0.0))
        self.assertAlmostEqual(w['landing']['open_d'], 7.4, delta=0.3); self.assertLess(w['landing']['share_all'], 0.5)
        self.assertEqual(self.judge_washers({'screws': [], 'washers': [w]}), [])

    def test_a_shank_in_solid_plastic_is_measured(self):
        scene = self.bracket_scene()
        scene[0] = Comp('Bracket', [Body('Body1', Sub(Blk((0, 0, 0), (40, 40, 10)), Rod(10, 10, -1, 11, 1.7)))])    # the second hole was filled by a join
        facts = self.unit('fusion_screws', scene, features=self.FEATURES)['facts']
        by = {s['ref'].split(' / ')[1]: s for s in facts['screws']}
        self.assertEqual(by['screw M3x12 cap (1)']['in_solid'], {'len': 0.0, 'name': None, 'part': None, 'at': None})
        solid = by['screw M3x12 cap (2)']['in_solid']
        self.assertEqual((solid['name'], solid['part']), ('Bracket', 'demo/bracket')); self.assertGreater(solid['len'], 9.0)
        half = Sub(Blk((0, 0, 0), (40, 40, 10)), Rod(10, 10, -1, 11, 1.7), Sub(Rod(30, 10, -1, 11, 1.7), Blk((29.9, 5, 3), (35, 15, 8))))      # a join grew over half of the hole, 5 mm high
        scene[0] = Comp('Bracket', [Body('Body1', half)])
        part = {s['ref'].split(' / ')[1]: s for s in self.unit('fusion_screws', scene, features=self.FEATURES)['facts']['screws']}['screw M3x12 cap (2)']['in_solid']
        self.assertAlmostEqual(part['len'], 5.0, delta=0.1); self.assertAlmostEqual(part['at'], 2.5, delta=0.1)      # the fill ends 2.5 under the washer's screw seat
        skin = Sub(Blk((0, 0, 0), (40, 40, 10)), Rod(10, 10, -1, 9.72, 1.7), Rod(30, 10, -1, 11, 1.7))                    # a 0.28 skin right under the first head
        scene[0] = Comp('Bracket', [Body('Body1', skin)])
        under = {s['ref'].split(' / ')[1]: s for s in self.unit('fusion_screws', scene, features=self.FEATURES)['facts']['screws']}['screw M3x12 cap (1)']['in_solid']
        self.assertAlmostEqual(under['len'], 0.28, delta=0.05); self.assertLessEqual(under['at'], 0.03)      # followed in steps: both ends read to 0.02
        import gate_judges, gate_registry, gate_params
        reg = gate_registry.load_default(); rule = reg['rules']['screws.hole_through']
        row = dict(by['screw M3x12 cap (1)'], in_solid=under)                                         # this head sits on a flat face: a closed hole
        sunk = dict(row, seat={'kind': 'counterbore', 'd': 6.5, 'recess': 0.3, 'share': 1.0})          # in a counterbore it is the bridge skin: told, not failed
        out = gate_judges.run_judge(rule, {'screws': [row, sunk]}, gate_params.values(gate_params.resolve(reg)), {'part': 'demo/bracket', 'rule': rule['id'], 'tables': reg['tables']})
        self.assertEqual([f['verdict'] for f in out], ['FAIL', 'INFO'])
        import gate_judges, gate_registry, gate_params
        reg = gate_registry.load_default(); rule = reg['rules']['screws.hole_through']
        out = gate_judges.run_judge(rule, facts, gate_params.values(gate_params.resolve(reg)), {'part': 'demo/bracket', 'rule': rule['id'], 'tables': reg['tables']})
        self.assertEqual([(f['verdict'], f['by']) for f in out], [('FAIL', ['demo/bracket'])])

    def test_a_body_inside_a_bought_assembly_is_a_ref_as_the_scan_says(self):
        scene = self.bearing_scene()
        inner = Comp('Bearing 6005', [Body('Body1', Rod(0, 0, 0.0, 12.0, 23.5, 12.5))])               # no "ref" in its own names
        scene[2] = inner
        mods = fake_adsk(scene)
        occ = mods['adsk.core'].Application.get().documents[0].products.itemByProductType('x').rootComponent.allOccurrences[2]
        occ.fullPathName = 'ref gearbox:1+Bearing 6005:1'                                             # ... but it lies in a bought assembly
        self.assertEqual(occ.component.name, 'Bearing 6005')
        env = self.unit_with(mods, 'fusion_bearings', part='Housing')
        b, = env['facts']['bearings']
        self.assertEqual((b['ref'], b['designation']), ('Bearing 6005', '6005')); self.assertEqual(env['facts']['not_bearings'], [])

    def test_a_printed_part_is_named_as_the_scan_names_it(self):
        tool = Body('Tool', Blk((100, 100, 100), (101, 101, 101)), bulb=False)                         # a switched-off tool body in the lid's component
        lid = Add(Rod(0, 0, 12.0, 13.0, 23.4, 21.0), Rod(0, 0, 13.0, 16.0, 35.0, 16.0))
        scene = self.bearing_scene()
        scene[1] = Comp('Lid', [Body('Body1', lid), tool])
        b, = self.unit('fusion_bearings', scene, part='Housing')['facts']['bearings']
        lip, = self.face(b, 'outer', '+')
        self.assertEqual((lip['name'], lip['part']), ('Lid', 'demo/lid'))                              # not "Lid / Body1": one PRINTED body

    def test_an_elliptical_edge_or_face_does_not_end_a_unit(self):
        scene = self.bracket_scene()
        odd = Body('screw M3x12 cap (9)', Add(Rod(10, 30, -2.0, 10.0, 1.5), Rod(10, 30, 10.0, 13.0, 2.75)))
        plain = odd.edges
        ellipse = types.SimpleNamespace(geometry=types.SimpleNamespace(objectType='adsk::core::EllipticalArc3D', center=cm((10, 30, 5)), normal=Pt(0, 0, 1)), faces=L([1, 2]))
        type(odd).edges = property(lambda self_, plain=plain, ellipse=ellipse: L(list(plain) + [ellipse]) if self_ is odd else Body.__dict__['_plain_edges'].fget(self_))
        self.addCleanup(setattr, Body, 'edges', Body.__dict__['_plain_edges'])
        scene[-2].bRepBodies.append(odd)
        for probe in ('fusion_screws', 'fusion_openings', 'fusion_bearings'):
            env = self.unit(probe, scene, features=self.FEATURES)
            self.assertIsNone(env['error'], probe)

    def test_ring_angles_follow_the_size_of_the_ring(self):
        self.assertEqual((R.ring_angles(23.5), R.ring_angles(11.0), R.ring_angles(50.0), R.ring_angles(500.0)), (50, 24, 105, 144))
        for phase in (0.0, 3.0, 7.5, 11.0):                                                            # six D12 washers, 2 mm over a D100 ring, turned anyhow
            r_in, r_out = R.ring_annulus(80.0, 100.0, 'outer', 0.25)
            pts = R.annulus_points((0, 0, 0), (0, 0, 1), r_in, r_out, R.ring_angles(r_out), 2)
            cs = [(54.0 * math.cos(math.radians(phase + 60 * k)), 54.0 * math.sin(math.radians(phase + 60 * k))) for k in range(6)]
            inside = lambda p: 0 < p[2] < 1.0 and any(math.hypot(p[0] - cx, p[1] - cy) <= 6.0 for cx, cy in cs)
            cov = R.face_cover(pts, inside, (0, 0, 1), R.cover_offsets(0.05, 2.0), 0.05)
            self.assertEqual(cov['points'], 6, phase)

    def test_landing_band(self):
        def run(od, inside):
            ring = (od - 3.2) / 2.0; r_lo, r_hi = 1.6 + 0.15 * ring, od / 2.0 - 0.05 * ring
            return R.landing_band(R.annulus_points((0, 0, 0), (0, 0, 1), r_lo, r_hi, 24, 6), lambda p: inside((p[0], p[1], -0.02)), r_lo, r_hi)
        flat = lambda p: True
        relief = lambda p: math.hypot(p[0], p[1]) >= 3.75
        edge = lambda p: p[0] <= 2.5
        self.assertEqual(run(9.0, flat)[:2], (1.0, None))
        share, open_d, rings = run(9.0, relief)
        self.assertEqual((share, rings[-1], rings[0]), (1.0, 1.0, 0.0)); self.assertAlmostEqual(open_d, 7.4, delta=0.3)
        self.assertEqual(run(7.0, relief)[0], 0.0)                                                     # a D7 washer falls into a D7.5 relief
        self.assertLess(run(9.0, edge)[0], 0.9)                                                        # a third of it hangs over an edge
        self.assertEqual(R.landing_band([], flat, 1.0, 2.0), (0.0, None, []))

    def test_export_writes_the_file_in_the_document_frame(self):
        stl = os.path.join(self.d, 'gate', 'run', 'quarantine', 'bracket.stl')
        body = Body('Body1', Blk((0, 0, 0), (10, 10, 10)), mesher=self.cube_mesher())
        mods = fake_adsk([Comp('Bracket', [body])])
        occ = mods['adsk.core'].Application.get().documents[0].products.itemByProductType('x').rootComponent.allOccurrences[0]
        turn = (0.0, -1.0, 0.0, 5.0,  1.0, 0.0, 0.0, 0.0,  0.0, 0.0, 1.0, 2.0,  0.0, 0.0, 0.0, 1.0)      # a quarter turn about z, moved 50 mm in x and 20 in z (cm)
        occ.transform2 = types.SimpleNamespace(asArray=lambda: turn)
        f = self.unit_with(mods, 'fusion_export', args={'stl': stl, 'name': 'bracket'})['facts']
        self.assertEqual((f['written'], f['frame'], f['facets_checked'], f['facets_off']), (True, 'document', 12, []))
        self.assertEqual(f['bbox_mesh'], [[0.0, 0.0, 0.0], [10.0, 10.0, 10.0]])                         # checked in the part frame
        with open(stl, 'rb') as fh: data = fh.read()
        pts = [struct.unpack('<3f', data[84 + 50 * t + 12 + 12 * k: 84 + 50 * t + 24 + 12 * k]) for t in range(12) for k in range(3)]
        lo, hi = R.box_of(pts)
        self.assertEqual(([round(c, 3) for c in lo], [round(c, 3) for c in hi]), ([40.0, 0.0, 20.0], [50.0, 10.0, 30.0]))      # written where the assembly has it

    def test_export_refuses_a_mesh_whose_facets_are_not_on_the_body(self):
        notch = Sub(Blk((0, 0, 0), (10, 10, 10)), Blk((1, 1, 9), (9, 9, 11)))                           # the body has a pocket the mesh does not have
        body = Body('Body1', notch, volume=1.0, mesher=self.cube_mesher())
        stl = os.path.join(self.d, 'gate', 'run', 'quarantine', 'bracket.stl')
        f = self.unit('fusion_export', [Comp('Bracket', [body])], args={'stl': stl, 'name': 'bracket'})['facts']
        self.assertFalse(f['written']); self.assertIn('sampled facets are not on the surface', f['why_not']); self.assertEqual(len(f['facets_off']), 2)
        self.assertFalse(os.path.exists(stl))

    # -- recognising hardware: a name picks the candidates, the geometry decides

    def plate_scene(self, names=('M3x14 cap head (%d)', 'M3x14 cap shank (%d)'), comp='ref plate hardware', extra=()):
        """A plate on two posts. Each screw is TWO bodies, a head and a shank, in a component whose name says nothing."""
        plate = Sub(Blk((0, 0, 0), (40, 20, 6)), Rod(10, 10, -1, 7, 1.7), Rod(30, 10, -1, 7, 1.7))
        post = lambda x: Sub(Blk((x - 5, 5, -12), (x + 5, 15, 0)), Rod(x, 10, -6.5, 1, 2.5))
        hw = []
        for k, x in ((1, 10), (2, 30)):
            hw += [Body(names[0] % k, Rod(x, 10, 6.0, 9.0, 2.75)), Body(names[1] % k, Rod(x, 10, -5.0, 6.0, 1.5)),
                   Body('insert M3x4x5 (%d)' % k, Rod(x, 10, -4.0, 0.0, 2.5, 1.25))]
        return [Comp('Plate', [Body('Body1', plate)]), Comp('Post A', [Body('Body1', post(10))]), Comp('Post B', [Body('Body1', post(30))]),
                Comp(comp, hw)] + list(extra)

    def plate_holes(self, comp='ref plate hardware', names=('M3x14 cap head (%d)', 'M3x14 cap shank (%d)')):
        hole = lambda i, x, k: {'id': i, 'anchor': {'kind': 'hole', 'axis': [0, 0, 1], 'point': [x, 10, 6]}, 'segments': [{'d': 3.4, 'len': 6.0}], 'depth': 6.0, 'through': True,
                                'class': {'table': 'screws', 'kind': 'M3', 'role': 'clearance', 'match': 'exact'},
                                'coaxial': [{'kind': 'bought', 'name': '%s / %s' % (comp, n % k), 'part': None} for n in names] +
                                           [{'kind': 'hardware', 'name': '%s / insert M3x4x5 (%d)' % (comp, k), 'part': None}, {'kind': 'printed', 'name': 'Post', 'part': 'demo/post_a'}]}
        return {'holes': [hole('h1', 10, 1), hole('h2', 30, 2)], 'openings': []}

    def judge(self, rule_id, facts, part, census=None):
        import gate_judges, gate_registry, gate_params
        reg = gate_registry.load_default()
        rule = reg['rules'][rule_id]
        return gate_judges.run_judge(rule, facts, gate_params.values(gate_params.resolve(reg)), {'part': part, 'rule': rule['id'], 'tables': reg['tables'], 'census': census})

    def test_a_screw_drawn_as_a_head_body_and_a_shank_body_is_one_screw(self):
        facts = self.unit('fusion_screws', self.plate_scene(), part='Plate', features=self.plate_holes(), params=SHIPPED)['facts']
        self.assertEqual((facts['n_screws'], len(facts['screws']), facts['not_screws'], facts['n_washers'], facts['unreadable_washers']), (2, 2, [], 0, []))
        s = facts['screws'][0]
        self.assertEqual((s['ref'], s['size'], s['length'], s['named_length'], s['head'], s['role']), ('ref plate hardware / M3x14 cap head (1)', 'M3', 11.0, 14.0, 'cap', 'head'))
        self.assertAlmostEqual(s['under_head'], 6.0, delta=0.06)
        self.assertEqual((s['engagement'], s['into']['kind']), (4.0, 'insert'))
        self.assertEqual([(h['hole'], h['role'], h['screw'], h['bodies']) for h in facts['fastener_holes']],
                         [('h1', 'clearance', 'ref plate hardware / M3x14 cap head (1)', []), ('h2', 'clearance', 'ref plate hardware / M3x14 cap head (2)', [])])
        self.assertEqual(self.judge('hardware.recognised', facts, 'demo/plate'), [])
        post = self.unit('fusion_screws', self.plate_scene(), part='Post A', params=SHIPPED)['facts']  # seen from the post: the thread ends in it
        self.assertEqual([(x['ref'][-3:], x['role']) for x in post['screws']], [('(1)', 'thread')])
        self.assertEqual([x['ref'][-3:] for x in post['passing']], [])                                 # the other screw is 20 mm away: not near

    def test_a_naming_the_pattern_misses_is_said_never_read_as_no_screws(self):
        names = ('cap head (%d)', 'cap shank (%d)')                                                    # no thread, no word: nothing picks them
        facts = self.unit('fusion_screws', self.plate_scene(names), part='Plate', features=self.plate_holes(names=names), params=SHIPPED)['facts']
        self.assertEqual((facts['n_screws'], facts['screws']), (0, []))
        blind = [(h['hole'], h['screw'], h['bodies']) for h in facts['fastener_holes']]
        self.assertEqual(blind[0], ('h1', None, ['ref plate hardware / cap head (1)', 'ref plate hardware / cap shank (1)']))
        out = self.judge('hardware.recognised', facts, 'demo/plate')
        self.assertEqual([f['verdict'] for f in out], ['NOT_RUN'])
        self.assertIn('2 screw holes or insert pockets with a bought body on the axis that nothing recognised', out[0]['text'])
        self.assertIn('recognised 0 screws', out[0]['text'])
        named = self.unit('fusion_screws', self.plate_scene(names), part='Plate', features=self.plate_holes(names=names),
                          params={'screws.ref_pattern': '(?i)cap (head|shank)'})['facts']               # the project sets its pattern: found
        self.assertEqual((named['n_screws'], [h['screw'] is not None for h in named['fastener_holes']]), (2, [True, True]))

    def test_a_magnet_in_a_hardware_component_is_no_screw(self):
        magnet = Body('magnet D6x2.5 (1)', Rod(20, 10, 3.5, 6.0, 3.0))
        scene = self.plate_scene(comp='ref sensor parts (magnets, screws, inserts, washers)', extra=[])
        scene[0] = Comp('Plate', [Body('Body1', Sub(Blk((0, 0, 0), (40, 20, 6)), Rod(10, 10, -1, 7, 1.7), Rod(30, 10, -1, 7, 1.7), Rod(20, 10, 3.5, 7, 3.05)))])
        scene[3].bRepBodies.append(magnet)
        facts = self.unit('fusion_screws', scene, part='Plate', params=WORD)['facts']                  # only the component says "screws"
        self.assertEqual((facts['n_screws'], len(facts['screws'])), (2, 2))                            # the two real screws of that component
        self.assertEqual(facts['not_screws'], ['ref sensor parts (magnets, screws, inserts, washers) / magnet D6x2.5 (1)'])
        self.assertEqual((facts['rejected'][0]['as'], facts['rejected'][0]['why']), ('screw', 'a plain cylinder D6 x 2.5 with no head, and its own name does not say screw'))
        self.assertEqual((facts['n_washers'], facts['unreadable_washers'], facts['washers']), (0, [], []))        # "washers" in the component name makes no washer of a head
        self.assertEqual([s['into']['kind'] for s in facts['screws']], ['insert', 'insert'])                       # and "inserts" no insert of a shank
        out = self.judge('hardware.recognised', facts, 'demo/plate')
        self.assertEqual([f['verdict'] for f in out], ['INFO']); self.assertIn('magnet D6x2.5 (1)', out[0]['text'])
        for rule in ('screws.engagement', 'screws.tip_bottoming', 'screws.hole_through'):
            self.assertEqual([f for f in self.judge(rule, facts, 'demo/plate') if 'magnet' in f['text']], [], rule)

    def test_a_head_on_a_washer_over_a_slot_sits_on_the_part(self):
        plate = Sub(Blk((0, 0, 0), (40, 20, 6)), Blk((16.3, 5, -1), (19.7, 15, 7)))                    # a 3.4 mm wide slot, 10 long
        base = Sub(Blk((0, 0, -12), (40, 20, 0)), Rod(18, 10, -6.5, 1, 2.5))
        scene = [Comp('Plate', [Body('Body1', plate)]), Comp('Base', [Body('Body1', base)]),
                 Comp('ref slot hardware', [Body('M3x16 cap head (1)', Rod(18, 10, 6.5, 9.5, 2.75)), Body('M3x16 cap shank (1)', Rod(18, 10, -5.0, 6.5, 1.5)),
                                            Body('washer M3 (1)', Rod(18, 10, 6.0, 6.5, 3.5, 1.6)), Body('insert M3x4x5 (1)', Rod(18, 10, -4.0, 0.0, 2.5, 1.25))])]
        facts = self.unit('fusion_screws', scene, part='Plate', params=SHIPPED)['facts']
        s, = facts['screws']; w, = facts['washers']
        self.assertEqual((s['role'], s['washer'], w['screw']), ('head', 'w1', s['ref']))
        self.assertAlmostEqual(s['under_head'], 6.0, delta=0.06)                                       # measured under the washer's ring, beside the slot
        self.assertTrue(all(0.3 < x < 0.7 for x in w['landing']['ring_shares']), w['landing']['ring_shares'])
        census = {'holes': [], 'openings': [{'id': 'o1', 'anchor': {'kind': 'face', 'axis': [0, 0, 1], 'point': [18, 10, 6]}, 'shape': 'slot', 'size': [10.0, 3.4], 'through': True}]}
        self.assertEqual(self.judge('washers.landing', facts, 'demo/plate', census), [])               # it bridges the slot: both flanks bear
        self.assertEqual([f['verdict'] for f in self.judge('washers.landing', facts, 'demo/plate', {'holes': [], 'openings': []})], ['FAIL'])   # no slot there: an edge
        self.assertEqual(self.judge('screws.under_head', facts, 'demo/plate'), [])
        self.assertEqual(self.judge('hardware.recognised', facts, 'demo/plate'), [])

    def shaft_scene(self, ring='608 (1)', second=None, comp='ref roller axle: pin + sleeves + 2 x 608'):
        """A 608 in a plate, named by its designation alone inside an assembly component; one clamp washer whose screw
        is named after the bearing it clamps. The plate's landing lies 1.0 below the ring face."""
        plate = Sub(Blk((-30, -30, -2), (30, 30, 6)), Rod(0, 0, 0.0, 20.0, 11.0), Rod(0, 0, -3.0, 0.001, 6.0), Rod(15.5, 0, -3, 7, 1.7))
        shaft = [Body(ring, Rod(0, 0, 0.0, 7.0, 11.0, 4.0)), Body('axle pin', Rod(0, 0, -20.0, 30.0, 3.0)), Body('sleeve 6/8', Rod(0, 0, 0.0, 7.0, 4.0, 3.0))]
        if second:
            shaft.append(Body(second, Rod(0, 0, 7.0, 14.0, 11.0, 4.0)))
        clamp = [Body('M3x8 button shank (608 clamp) (1)', Rod(15.5, 0, 0.5, 8.5, 1.5)), Body('M3x8 button head (608 clamp) (1)', Rod(15.5, 0, 8.5, 10.15, 2.85)),
                 Body('washer M3x12x1.5 (608 clamp) (1)', Rod(15.5, 0, 7.0, 8.5, 6.0, 1.6))]
        return [Comp('Roller plate', [Body('Body1', plate)]), Comp(comp, shaft), Comp('ref clamp hardware (fixed bearing)', clamp)]

    SEAT = {'id': 'h6', 'anchor': {'kind': 'hole', 'axis': [0, 0, 1], 'point': [0, 0, 6]}, 'segments': [{'d': 22.0, 'len': 6.0}, {'d': 12.0, 'len': 2.0}], 'depth': 8.0,
            'through': True, 'class': {'table': 'bearings', 'kind': '608', 'role': 'bearing_seat', 'match': 'exact'},
            'coaxial': [{'kind': 'hardware', 'name': 'ref roller axle: pin + sleeves + 2 x 608 / ring A', 'part': None},
                        {'kind': 'hardware', 'name': 'ref roller axle: pin + sleeves + 2 x 608 / axle pin', 'part': None}]}

    def test_a_bearing_named_by_its_designation_in_an_assembly_is_found_and_its_clamp_screw_is_not_one(self):
        env = self.unit('fusion_bearings', self.shaft_scene(), part='Roller plate', features={'holes': [self.SEAT], 'openings': []}, params=SHIPPED)
        facts = env['facts']
        b, = facts['bearings']
        self.assertEqual((b['ref'], b['designation'], b['by'], b['d'], b['D'], b['B']), ('ref roller axle: pin + sleeves + 2 x 608 / 608 (1)', '608', 'geometry', 8.0, 22.0, 7.0))
        self.assertEqual((facts['n_bearings'], facts['not_bearings'], facts['near_bearings'], facts['seats_without_bearing']), (1, [], [], []))
        self.assertEqual(b['seat']['outer']['part'], 'demo/roller_plate')
        top, = self.face(b, 'outer', '+')
        self.assertEqual((top['name'], top['bearing']), ('ref clamp hardware (fixed bearing) / washer M3x12x1.5 (608 clamp)', False))
        clamp = next(c for c in b['clamps'] if c['ring'] == 'outer')
        self.assertEqual((clamp['by'], clamp['gap'], clamp['stop_gap'], clamp['stop_part'], clamp['lands_on']), ('washer', 0.0, 0.0, 'demo/roller_plate', 'Roller plate'))
        self.assertEqual((clamp['travel'], clamp['room']), (1.0, 6.0))                                 # the landing is 1.0 below the ring face: measured as 1.0, not 0.98
        self.assertEqual([f['verdict'] for f in self.judge('bearings.clamp_room', facts, 'demo/roller_plate')], [])
        self.assertEqual(self.judge('bearings.seat', facts, 'demo/roller_plate', {'holes': [self.SEAT], 'openings': []}), [])
        old = self.unit('fusion_bearings', self.shaft_scene(), part='Roller plate', params=WORD)['facts']       # a pattern that does not know the screw: its shape says no
        self.assertEqual([b['designation'] for b in old['bearings']], ['608'])
        self.assertEqual(old['not_bearings'], ['ref clamp hardware (fixed bearing) / M3x8 button shank (608 clamp) (1)'])
        self.assertIn('solid body of revolution', old['rejected'][0]['why'])

    def test_a_ring_no_name_points_at_leaves_its_seat_unproven_not_empty(self):
        scene = self.shaft_scene(ring='ring A', comp='ref roller axle')                                # no word, no designation anywhere
        facts = self.unit('fusion_bearings', scene, part='Roller plate', features={'holes': [self.SEAT], 'openings': []})['facts']
        self.assertEqual((facts['bearings'], facts['n_bearings']), ([], 0))
        row, = facts['seats_without_bearing']
        self.assertEqual((row['hole'], row['match'], len(row['bodies'])), ('h6', 'exact', 2))
        out = self.judge('bearings.seat', facts, 'demo/roller_plate')
        self.assertEqual([f['verdict'] for f in out], ['NOT_RUN']); self.assertIn('ring A', out[0]['text'])

    def test_a_clamp_washer_knows_its_bearing_by_geometry_not_by_the_name_of_a_component(self):
        facts = self.unit('fusion_screws', self.shaft_scene(), part='Roller plate', params=SHIPPED)['facts']
        w, = facts['washers']; s, = facts['screws']
        self.assertEqual((w['landing']['on_bearing'], w['screw'], s['washer']), ('ref roller axle: pin + sleeves + 2 x 608 / 608 (1)', s['ref'], 'w1'))
        self.assertEqual(sorted((c['name'].split(' / ')[-1], c['bearing']) for c in w['landing']['covers']), [('608 (1)', True), ('Roller plate', False)])
        beside = next(c for c in w['landing']['covers'] if not c['bearing'])
        self.assertAlmostEqual(beside['gap'], 1.0, delta=0.11)
        self.assertEqual(self.judge('washers.landing', facts, 'demo/roller_plate'), [])                 # it bears on the ring, the plastic is 1.0 below
        self.assertEqual((facts['n_screws'], facts['n_washers'], facts['not_screws']), (1, 1, []))

    def test_a_bearing_drawn_as_a_solid_puck_is_listed_by_its_name_and_not_run(self):
        scene = self.bearing_scene()
        scene[2] = Comp('ref bearing 6005', [Body('Body1', Rod(0, 0, 0.0, 12.0, 23.5))])                # no bore: a puck
        facts = self.unit('fusion_bearings', scene, part='Housing')['facts']
        b, = facts['bearings']
        self.assertEqual((b['designation'], b['by'], b['rings'], facts['not_bearings']), ('6005', 'name', None, []))
        out = self.judge('bearings.holding', facts, 'demo/housing')
        self.assertEqual([f['verdict'] for f in out], ['NOT_RUN']); self.assertIn('Model it as a ring', out[0]['text'])
        scene[2] = Comp('ref shaft with 6005', [Body('pin', Rod(0, 0, 0.0, 12.0, 12.5)), Body('collar for the 6005', Rod(0, 0, 12.5, 14.0, 14.0))])
        facts = self.unit('fusion_bearings', scene, part='Housing')['facts']                           # a designation in a name on a solid body: no bearing
        self.assertEqual((facts['bearings'], facts['not_bearings']), ([], ['ref shaft with 6005 / collar for the 6005']))

    def test_a_bearing_another_part_seats_and_holds_is_named_and_not_listed(self):
        shell = Sub(Blk((-60, -60, -40), (60, 60, 40)), Blk((-55, -55, -35), (55, 55, 45)))            # a link that carries the unit and touches no ring
        facts = self.unit('fusion_bearings', self.shaft_scene() + [Comp('Carrier frame', [Body('Body1', shell)])], part='Carrier frame')['facts']
        self.assertEqual((facts['bearings'], facts['n_bearings'], facts['near_bearings']), ([], 1, ['ref roller axle: pin + sleeves + 2 x 608 / 608 (1)']))

    def test_the_other_bearing_of_a_pair_is_marked_as_its_twin(self):
        facts = self.unit('fusion_bearings', self.shaft_scene(second='608 (2)'), part='Roller plate')['facts']
        first = next(b for b in facts['bearings'] if b['ref'].endswith('608 (1)'))
        cover = next(c for c in self.face(first, 'outer', '+') if c['bearing'])
        self.assertEqual((cover['name'].split(' / ')[-1], cover['twin'], cover['touches_other_ring']), ('608', True, True))
        out = self.judge('bearings.holding', facts, 'demo/roller_plate')
        self.assertEqual([f for f in out if 'both rings' in f['text']], [])


if __name__ == '__main__':
    unittest.main()
