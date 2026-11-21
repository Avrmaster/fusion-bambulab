"""probes/_geom.py on tuples (hole stacks, classification, anchors, pocket levels, ray summaries, mesh test), and the three
Fusion-side probes fusion_scan, fusion_census and fusion_inserts run against a FAKE adsk: an analytic solid (a box with
drilled stacks, cuts and bosses) that answers pointContainment and lists its faces the way the probes read them.
The fake checks the logic, the frames and the units of the probes. It cannot check that Fusion answers the same calls
the same way: that is validated inside Fusion, one part at a time."""
import contextlib, json, math, os, shutil, sys, tempfile, types, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
import gate_common, gate_features, gate_fusion_rpc      # noqa: E402
from probes import _geom as G                           # noqa: E402


def tables():
    out = {}
    for name in ('inserts', 'screws', 'bearings', 'washers'):
        with open(os.path.join(ROOT, 'rules', 'tables', name + '.json'), encoding='utf-8') as f: out[name] = json.load(f)
    return out


SHIPPED = tables()
# The tables these tests classify with: invented and frozen here, in the shape of rules/tables/*.json, so that a change of
# the shipped numbers (another package's content) cannot move the logic under test. Shipped tables: class Shipped.
TABLES = {
    'inserts': {'tol': {'d': 0.1, 'depth': 0.3},
                'kinds': {'M3x4x5': {'thread': 'M3', 'insert_len': 4.0, 'relief': {'d': 7.5, 'depth': 0.54, 'fallback_d': [6.5, 5.9]},
                                     'lead_in': {'d': 4.4, 'depth': 1.5}, 'hole': {'d': 3.6, 'depth': 6.5},
                                     'band': {'d': [3.5, 5.3], 'blind_only': False}, 'profile': 'measured'}},
                'pilots': {'M3 thread-forming': {'thread': 'M3', 'd': [2.4, 2.7]}}},
    'screws': {'tol': {'d': 0.15}, 'sizes': {
        'M2': {'thread_d': 2.0, 'clearance_d': 2.4, 'tap_pilot_d': 1.6, 'heads': {'cap': {'d': 3.8, 'h': 2.0, 'counterbore_d': 4.4}, 'countersunk': {'d': 4.0, 'angle': 90}}},
        'M3': {'thread_d': 3.0, 'clearance_d': 3.4, 'tap_pilot_d': 2.5, 'heads': {'cap': {'d': 5.5, 'h': 3.0, 'counterbore_d': 6.5}, 'button': {'d': 5.7, 'h': 1.65}, 'countersunk': {'d': 6.0, 'angle': 90}}},
        'M4': {'thread_d': 4.0, 'clearance_d': 4.5, 'tap_pilot_d': 3.3, 'heads': {'cap': {'d': 7.0, 'h': 4.0, 'counterbore_d': 8.0}, 'countersunk': {'d': 8.0, 'angle': 90}}},
        'M5': {'thread_d': 5.0, 'clearance_d': 5.5, 'tap_pilot_d': 4.2, 'heads': {'cap': {'d': 8.5, 'h': 5.0, 'counterbore_d': 10.0}, 'countersunk': {'d': 10.0, 'angle': 90}}},
        'M6': {'thread_d': 6.0, 'clearance_d': 6.6, 'tap_pilot_d': 5.0, 'heads': {'cap': {'d': 10.0, 'h': 6.0, 'counterbore_d': 11.0}, 'countersunk': {'d': 12.0, 'angle': 90}}}}},
    'bearings': {'tol': {'d': 0.05}, 'sizes': {'684': {'d': 4, 'D': 9, 'B': 2.5}, '608': {'d': 8, 'D': 22, 'B': 7}, '6005': {'d': 25, 'D': 47, 'B': 12},
                                               '6805': {'d': 25, 'D': 37, 'B': 7}, '61805': {'d': 25, 'D': 37, 'B': 7}}},
    'washers': {}}
RELIEF, LEAD, HOLE = (7.5, 0.54), (4.4, 1.5), (3.6, 5.0)       # the shipped M3x4x5 pocket, from the mouth inwards
POCKET = [RELIEF, LEAD, HOLE]


def segs(*pairs):
    return [dict(zip(('d', 'len', 'd2'), p)) for p in pairs]


def cyl(x, y, d, s0, s1, axis=(0, 0, 1), z0=0.0, d2=None, **kw):
    """one raw cylinder face on a vertical axis through (x, y): extent s0..s1 above z0"""
    c = {'origin': (x, y, z0), 'axis': axis, 'r': d / 2.0, 'r2': None if d2 is None else d2 / 2.0, 's0': s0, 's1': s1, 'full': True, 'inner': True}
    c.update(kw)
    return c


def pocket_faces(x, y, top, profile=POCKET):
    """the faces of a pocket drilled down from z = top"""
    out, z = [], top
    for d, ln in profile:
        out.append(cyl(x, y, d, z - ln, z)); z -= ln
    return out


class Stacks(unittest.TestCase):
    def test_pocket_is_one_stack_ordered_from_the_mouth(self):
        faces = pocket_faces(12.0, -30.0, 40.0)
        for order in (faces, list(reversed(faces))):
            st = G.stack_holes(order)
            self.assertEqual(len(st), 1)
            self.assertEqual(st[0]['axis'], (0.0, 0.0, 1.0)); self.assertEqual(st[0]['origin'], (12.0, -30.0, 0.0))
            self.assertEqual([round(s['r'] * 2, 2) for s in st[0]['spans']], [3.6, 4.4, 7.5])
            self.assertAlmostEqual(st[0]['lo'], 32.96); self.assertAlmostEqual(st[0]['hi'], 40.0)
            h = G.hole_from_stack(st[0], False, True)
            self.assertEqual(h['segments'], segs(*POCKET)); self.assertEqual(h['depth'], 7.04)
            self.assertEqual((h['axis'], h['point'], h['through'], h['exit']), ((0.0, 0.0, 1.0), (12.0, -30.0, 40.0), False, None))

    def test_a_face_that_names_the_axis_the_other_way_round_joins_the_same_stack(self):
        up = cyl(5, 5, 4.4, 8.5, 10.0)
        down = {'origin': (5, 5, 20.0), 'axis': (0, 0, -1), 'r': 1.8, 'r2': None, 's0': 11.5, 's1': 16.5, 'full': True, 'inner': True}   # z 3.5 .. 8.5
        st = G.stack_holes([down, up])
        self.assertEqual(len(st), 1); self.assertEqual([(s['s0'], s['s1']) for s in st[0]['spans']], [(3.5, 8.5), (8.5, 10.0)])
        self.assertEqual(G.hole_from_stack(st[0], False, True)['segments'], segs(LEAD, HOLE))

    def test_a_pocket_from_below_points_down(self):
        st = G.stack_holes([cyl(0, 0, 4.4, 0.0, 1.5), cyl(0, 0, 3.6, 1.5, 6.5)])
        h = G.hole_from_stack(st[0], True, False)
        self.assertEqual(h['axis'], (0.0, 0.0, -1.0)); self.assertEqual(h['point'], (0.0, 0.0, 0.0)); self.assertEqual(h['segments'], segs(LEAD, HOLE))

    def test_closed_cavity_and_through_hole(self):
        st = G.stack_holes([cyl(0, 0, 6.5, 7.0, 10.0), cyl(0, 0, 3.4, 0.0, 7.0)])[0]
        self.assertIsNone(G.hole_from_stack(st, False, False))
        h = G.hole_from_stack(st, True, True)                     # the mouth of a through hole is its wider end
        self.assertEqual((h['through'], h['point'], h['exit'], h['axis']), (True, (0.0, 0.0, 10.0), (0.0, 0.0, 0.0), (0.0, 0.0, 1.0)))
        self.assertEqual(h['segments'], segs((6.5, 3.0), (3.4, 7.0)))
        plain = G.hole_from_stack(G.stack_holes([cyl(0, 0, 3.4, 0.0, 7.0)])[0], True, True)          # equal ends: the "hi" end
        self.assertEqual((plain['point'], plain['axis']), ((0.0, 0.0, 7.0), (0.0, 0.0, 1.0)))
        wide_below = G.hole_from_stack(G.stack_holes([cyl(0, 0, 6.5, 0.0, 3.0), cyl(0, 0, 3.4, 3.0, 10.0)])[0], True, True)
        self.assertEqual((wide_below['point'], wide_below['axis']), ((0.0, 0.0, 0.0), (0.0, 0.0, -1.0)))

    def test_two_half_faces_and_a_seam_are_one_segment(self):
        st = G.stack_holes([cyl(1, 2, 3.4, 0, 6), cyl(1, 2, 3.4, 0, 6), cyl(1, 2, 3.4, 6, 9)])
        self.assertEqual(len(st), 1); self.assertEqual(len(st[0]['spans']), 1); self.assertEqual((st[0]['lo'], st[0]['hi']), (0.0, 9.0))

    def test_tooth_grooves_fillets_and_bosses_are_not_holes(self):
        groove = [cyl(20 * math.cos(k), 20 * math.sin(k), 1.11, 0, 8, full=False) for k in range(40)]
        boss = cyl(0, 0, 12.0, 0, 9, inner=False)
        self.assertEqual(G.stack_holes(groove + [boss]), [])
        self.assertEqual(len(G.stack_holes(groove + [boss, cyl(0, 0, 5.0, 0, 8)])), 1)

    def test_holes_on_one_line_that_do_not_touch_are_separate_stacks(self):
        top = cyl(3, 3, 3.4, 7.0, 12.0)                          # blind from the top face
        bottom = cyl(3, 3, 3.4, 0.0, 6.16)                       # from the bottom face: 0.84 mm of plastic between them
        st = G.stack_holes([top, bottom])
        self.assertEqual([(s['lo'], s['hi']) for s in st], [(0.0, 6.16), (7.0, 12.0)])
        self.assertEqual(G.behind_blind_end(st, 1, False), {'stack': 0, 'cap': 0.84, 'd': 3.4})
        self.assertEqual(G.behind_blind_end(st, 0, True), {'stack': 1, 'cap': 0.84, 'd': 3.4})
        self.assertIsNone(G.behind_blind_end(st, 1, True)); self.assertIsNone(G.behind_blind_end(G.stack_holes([top]), 0, False))

    def test_the_outer_wall_of_a_ring_groove_round_a_hole_is_its_own_stack(self):
        st = G.stack_holes([cyl(0, 0, 3.4, 0, 10), cyl(0, 0, 20.0, 8, 10)])
        self.assertEqual(sorted(len(s['spans']) for s in st), [1, 1]); self.assertEqual(len(st), 2)

    def test_axes_closer_than_the_tolerance_are_one_axis_and_the_order_is_stable(self):
        a, b, c = pocket_faces(10.0, 10.0, 12.0), pocket_faces(30.0, 10.0, 12.0), pocket_faces(10.0, 30.0, 12.0)
        b[1]['origin'] = (30.01, 10.0, 0.0)                      # a modelling slip of 0.01 mm
        one = G.stack_holes(a + b + c)
        self.assertEqual(len(one), 3)
        self.assertEqual([s['origin'][:2] for s in one], [(10.0, 10.0), (10.0, 30.0), (30.0, 10.0)])
        self.assertEqual(G.stack_holes(c + b + a), one)
        b[1]['origin'] = (30.5, 10.0, 0.0)                       # half a millimetre off: another hole
        self.assertEqual(len(G.stack_holes(a + b + c)), 5)

    def test_cone_ends(self):
        sink = G.stack_holes([cyl(0, 0, 3.4, 8.7, 10.0, d2=6.0), cyl(0, 0, 3.4, 0, 8.7)])[0]       # a countersink at the top
        h = G.hole_from_stack(sink, True, True)
        self.assertEqual(h['segments'], [{'d': 6.0, 'len': 1.3, 'd2': 3.4}, {'d': 3.4, 'len': 8.7}]); self.assertEqual(h['point'], (0.0, 0.0, 10.0))
        drill = G.stack_holes([cyl(0, 0, 0.0, 0.0, 1.0, d2=3.4), cyl(0, 0, 3.4, 1.0, 8.0)])[0]      # a drill point at the bottom
        self.assertEqual(G.hole_from_stack(drill, False, True)['segments'], [{'d': 3.4, 'len': 7.0}, {'d': 3.4, 'len': 1.0, 'd2': 0.0}])

    def test_cylinder_extent_from_boundary_points(self):
        ext = G.cylinder_extent((0, 0, 0), (0, 0, 1), 1.7, [(1.7, 0, 2.0), (1.7, 0, 9.0)])
        self.assertEqual(ext, {'s0': 2.0, 's1': 9.0, 'r': 1.7, 'r2': None})
        cone = G.cylinder_extent((0, 0, 0), (0, 0, 1), 0.0, [(3.0, 0, 10.0)], circles=[((0, 0, 8.7), 1.7), ((5, 5, 20.0), 9.0)], cone=True)
        self.assertEqual(cone, {'s0': 8.7, 's1': 10.0, 'r': 1.7, 'r2': 3.0})
        self.assertIsNone(G.cylinder_extent((0, 0, 0), (0, 0, 1), 1.7, [(1.7, 0, 2.0)]))

    def test_coaxial_bodies(self):
        ins = {'name': 'HW / insert M3x4x5', 'kind': 'hardware', 'part': None, 'origin': (12.0, -30.0, 0), 'axis': (0, 0, -1), 's0': -39.5, 's1': -35.5}
        cover = {'name': 'Cover', 'kind': 'printed', 'part': 'demo/cover', 'origin': (12.1, -30.0, 0), 'axis': (0, 0, 1), 's0': 40.2, 's1': 43.0}
        far = {'name': 'Lid', 'kind': 'printed', 'part': 'demo/lid', 'origin': (12.0, -30.0, 0), 'axis': (0, 0, 1), 's0': 60.0, 's1': 63.0}
        off = dict(cover, name='Plate', origin=(12.4, -30.0, 0)); tilted = dict(cover, name='Wedge', axis=G.v_unit((0.1, 0, 1)))
        got = G.coaxial_cylinders((12.0, -30.0, 40.0), (0, 0, 1), 7.04, [cover, ins, far, off, tilted, cover])
        self.assertEqual(got, [{'name': 'Cover', 'kind': 'printed', 'part': 'demo/cover'}, {'name': 'HW / insert M3x4x5', 'kind': 'hardware', 'part': None}])


class Anchors(unittest.TestCase):
    def anchor(self, faces, open_lo=False, open_hi=True):
        h = G.hole_from_stack(G.stack_holes(faces)[0], open_lo, open_hi)
        return G.anchor_of('insert_pocket', h['axis'], h['point']), h

    def test_anchor_is_written_as_the_host_writes_it(self):
        for axis, point in (((0, 0, 2), (12.0, -30.012, 39.974)), ((0.3, -0.4, 0.5), (-0.01, 0.024, 0.026)), ((0, -1, 0), (1.0249, 2.975, -3.3))):
            self.assertEqual(G.anchor_of('hole', axis, point), gate_features.anchor('hole', axis, point))
        self.assertEqual(json.dumps(G.anchor_of('hole', (0, 0, -1), (0.0, -0.01, 5))), '{"kind": "hole", "axis": [0.0, 0.0, -1.0], "point": [0.0, 0.0, 5.0]}')

    def test_anchor_stays_when_half_a_millimetre_is_cut_off_the_mouth(self):
        before, h0 = self.anchor(pocket_faces(12.0, -30.0, 40.0, [LEAD, HOLE]))
        lowered = [cyl(12.0, -30.0, 4.4, 38.0, 39.5), cyl(12.0, -30.0, 3.6, 33.5, 38.0)]            # the face is milled down by 0.5
        after, h1 = self.anchor(lowered)
        self.assertEqual(after['point'], [12.0, -30.0, 39.5]); self.assertEqual(after['axis'], before['axis'])
        self.assertTrue(gate_features.anchors_match(before, after))
        self.assertEqual(gate_features.anchor_distance(before, after), (0.0, 0.5))                    # it moved along the axis only
        self.assertNotEqual(h0['segments'], h1['segments'])                                           # ... and the context is what changes

    def test_anchor_stays_when_a_relief_is_cut_into_the_mouth(self):
        before, _ = self.anchor(pocket_faces(12.0, -30.0, 40.0, [LEAD, HOLE]))
        after, h = self.anchor(pocket_faces(12.0, -30.0, 40.0, [RELIEF, (4.4, 1.5), (3.6, 4.46)]))
        self.assertEqual(before, after); self.assertEqual(h['segments'][0], {'d': 7.5, 'len': 0.54})

    def test_a_moved_pocket_and_a_pocket_from_the_other_face_are_other_features(self):
        a, _ = self.anchor(pocket_faces(12.0, -30.0, 40.0))
        moved, _ = self.anchor(pocket_faces(13.2, -30.0, 40.0))
        self.assertFalse(gate_features.anchors_match(a, moved))
        below, _ = self.anchor([cyl(12.0, -30.0, 4.4, 38.5, 40.0), cyl(12.0, -30.0, 3.6, 40.0, 45.0)], open_lo=True, open_hi=False)
        self.assertEqual(below['axis'], [0.0, 0.0, -1.0]); self.assertFalse(gate_features.anchors_match(a, below))

    def test_slug_and_part_name_as_the_host_writes_them(self):
        for name in ('Motor plate (left)', 'Bracket / Body2', '  x  ', '***', 'Arm_2A v3', 'ref bearing 6005', 'A--b__C'):
            self.assertEqual(G.part_slug(name), gate_common.slug(name), name)
        self.assertEqual(G.part_name('Bracket', 'Body1', 1), 'Bracket'); self.assertEqual(G.part_name('Bracket', 'Body2', 2), 'Bracket / Body2')


class Classify(unittest.TestCase):
    def c(self, pairs, through=False, tables=TABLES, **kw):
        return G.classify_stack(segs(*pairs), through, tables, **kw)

    def test_known_pocket(self):
        want = {'table': 'inserts', 'kind': 'M3x4x5', 'role': 'insert_pocket', 'match': 'exact'}
        self.assertEqual(self.c(POCKET), want)                                    # D3.6 hole, D4.4 lead-in, D7.5 relief
        self.assertEqual(self.c([LEAD, HOLE]), want)                              # the relief is another rule's business
        self.assertEqual(self.c([(6.5, 0.54), LEAD, HOLE]), want)                 # a fallback relief
        self.assertEqual(self.c([RELIEF, LEAD, (3.6, 11.0)]), want)               # a tip hole below the insert
        self.assertEqual(self.c([RELIEF, LEAD, (3.6, 9.0)], through=True), want)
        self.assertEqual(self.c([(10.0, 5.0), LEAD, HOLE]), want)                 # the pocket at the bottom of a recess

    def test_straight_hole_of_the_lead_in_diameter_is_a_wrong_pocket(self):
        band = {'table': 'inserts', 'kind': 'M3x4x5', 'role': 'insert_pocket', 'match': 'band'}
        self.assertEqual(self.c([(4.4, 6.0)]), band)                              # the old straight pocket, blind
        self.assertEqual(self.c([(4.6, 6.0)]), band); self.assertEqual(self.c([(5.2, 1.5), (4.6, 5.0)]), band)
        self.assertEqual(self.c([RELIEF, LEAD, (3.6, 3.0)]), band)                # too shallow
        self.assertEqual(self.c([RELIEF, (4.4, 3.0), HOLE]), band)                # lead-in twice as deep
        self.assertEqual(self.c([(3.6, 8.0)], through=True), band)                # the narrow hole without its lead-in
        self.assertEqual(self.c([(10.0, 5.0), (4.4, 6.0)]), band)                 # ... at the bottom of a recess
        self.assertEqual(self.c([(4.4, 0.3, 3.6), HOLE]), band)                   # a cone is no lead-in

    def test_exact_screw_hole_of_another_size_in_the_band(self):
        m4 = {'table': 'screws', 'kind': 'M4', 'role': 'clearance', 'match': 'exact'}
        self.assertEqual(self.c([(4.5, 10.0)], through=True), m4)
        self.assertEqual(self.c([(4.4, 6.0)], through=True), m4)                  # through: a clearance hole ...
        self.assertEqual(self.c([(4.4, 6.0)], through=True, coaxial=['HW (ref) / insert M3x4x5 (2)'])['match'], 'band')   # ... unless an insert sits in it
        self.assertEqual(self.c([(4.5, 10.0)])['table'], 'inserts')               # one straight blind cylinder stays a pocket row
        self.assertEqual(self.c([(8.0, 4.0), (4.5, 10.0)]), dict(m4, role='counterbore'))       # blind, but a screw profile
        self.assertEqual(self.c([(3.5, 8.0)], through=True)['table'], 'inserts')  # M3 is the insert's own thread

    def test_screw_holes(self):
        m3 = {'table': 'screws', 'kind': 'M3', 'match': 'exact'}
        self.assertEqual(self.c([(6.5, 3.0), (3.4, 8.0)], through=True), dict(m3, role='counterbore'))   # D3.4 through, D6.5 counterbore
        self.assertEqual(self.c([(6.5, 3.0), (3.4, 4.0), (6.5, 3.0)], through=True), dict(m3, role='counterbore'))
        self.assertEqual(self.c([(3.4, 8.0)], through=True), dict(m3, role='clearance'))
        self.assertEqual(self.c([(3.8, 0.2, 3.4), (3.4, 8.0)], through=True), dict(m3, role='clearance'))   # an edge chamfer
        self.assertEqual(self.c([(6.0, 1.3, 3.4), (3.4, 8.0)], through=True), dict(m3, role='countersink'))
        self.assertEqual(self.c([(2.5, 8.0)]), dict(m3, role='tap_pilot'))
        self.assertEqual(self.c([(9.0, 2.0), (3.4, 8.0)], through=True), dict(m3, role='counterbore', match='band'))   # a washer face
        self.assertEqual(self.c([(5.5, 20.0)], through=True)['kind'], 'M5')
        self.assertEqual(self.c([(2.7, 8.0)]), {'table': 'inserts', 'kind': 'M3 thread-forming', 'role': 'pilot', 'match': 'exact'})

    def test_an_m4_insert_band(self):
        t = json.loads(json.dumps(TABLES))
        t['inserts']['kinds']['M4x6x6'] = {'thread': 'M4', 'insert_len': 6.0, 'relief': {'d': 9.0, 'depth': 0.54, 'fallback_d': [8.0]},
                                           'lead_in': {'d': 5.6, 'depth': 1.5}, 'hole': {'d': 4.8, 'depth': 8.5}, 'band': {'d': [4.7, 6.6], 'blind_only': True}}
        self.assertEqual(self.c([(5.6, 8.0)], tables=t), {'table': 'inserts', 'kind': 'M4x6x6', 'role': 'insert_pocket', 'match': 'band'})
        self.assertEqual(self.c([(9.0, 0.54), (5.6, 1.5), (4.8, 7.0)], tables=t), {'table': 'inserts', 'kind': 'M4x6x6', 'role': 'insert_pocket', 'match': 'exact'})
        self.assertEqual(self.c([(5.0, 6.0)], tables=t)['kind'], 'M4x6x6')        # in both bands: the nearer row
        self.assertEqual(self.c([(4.4, 6.0)], tables=t)['kind'], 'M3x4x5')
        self.assertEqual(self.c([(6.2, 9.0)], through=True, tables=t), None)      # blind_only: a through hole is not in this band
        self.assertEqual(self.c(POCKET, tables=t)['kind'], 'M3x4x5')

    def test_a_row_that_was_never_measured_is_never_an_exact_match(self):
        t = json.loads(json.dumps(TABLES))
        t['inserts']['kinds']['M4'] = {'thread': 'M4', 'insert_len': 7.0, 'relief': {'d': 8.5, 'depth': 0.54, 'fallback_d': []}, 'lead_in': {'d': 5.6, 'depth': 0.0},
                                       'hole': {'d': 5.6, 'depth': 8.0}, 'band': {'d': [5.5, 5.7], 'blind_only': True, 'min_depth': 5.0}, 'profile': 'recorded'}
        band = {'table': 'inserts', 'kind': 'M4', 'role': 'insert_pocket', 'match': 'band'}
        self.assertEqual(self.c([(5.6, 8.0)], tables=t), band)                    # exactly the recorded hole: still a wrong-pocket row
        self.assertEqual(self.c([(8.5, 0.54), (5.6, 8.0)], tables=t), band)
        self.assertEqual(self.c([(5.6, 3.0)], tables=t)['table'], 'screws')      # shallower than min_depth: not in the band (a magnet pocket); M5 clearance fits
        self.assertEqual(self.c([(5.6, 12.0)], through=True, tables=t)['table'], 'screws')
        t['inserts']['kinds']['M4']['profile'] = 'measured'                       # ... and a lead-in depth of 0 alone is enough to say so
        self.assertEqual(self.c([(5.6, 8.0)], tables=t), band)

    def test_kinds_with_the_same_diameters_are_told_apart_by_depth_or_by_the_insert_on_the_axis(self):
        t = json.loads(json.dumps(TABLES))
        t['inserts']['kinds']['M3x6x5'] = dict(t['inserts']['kinds']['M3x4x5'], insert_len=6.0, hole={'d': 3.6, 'depth': 8.5}, profile='derived')
        deep, short = [RELIEF, LEAD, (3.6, 7.0)], POCKET
        self.assertEqual(self.c(short, tables=t)['kind'], 'M3x4x5'); self.assertEqual(self.c(deep, tables=t)['kind'], 'M3x6x5')
        self.assertEqual(self.c(deep, tables=t, coaxial=['HW (ref) / insert M3x4x5 (7)'])['kind'], 'M3x4x5')     # the ref says which insert goes in
        self.assertEqual(self.c([RELIEF, LEAD, (3.6, 6.8)], tables=t)['kind'], 'M3x6x5'); self.assertEqual(self.c([RELIEF, LEAD, (3.6, 6.1)], tables=t)['kind'], 'M3x4x5')
        self.assertEqual(self.c([(4.4, 6.0)], tables=t)['kind'], 'M3x4x5'); self.assertEqual(self.c([(4.4, 9.0)], tables=t)['kind'], 'M3x6x5')
        self.assertEqual(self.c([(4.4, 6.0)], tables=t, coaxial=['insert M3x6x5'])['kind'], 'M3x6x5')

    def test_bearing_seat_needs_a_shoulder_or_its_bearing(self):
        seat = {'table': 'bearings', 'kind': '608', 'role': 'bearing_seat', 'match': 'exact'}
        self.assertEqual(self.c([(22.0, 7.0), (12.0, 10.0)], through=True), seat)             # a step behind it
        self.assertEqual(self.c([(22.0, 7.0)]), seat)                                         # the floor of a blind hole
        self.assertIsNone(self.c([(22.0, 7.0)], through=True))                                # a diameter alone is no seat
        self.assertEqual(self.c([(22.0, 7.0)], through=True, coaxial=['ref bearing 608 upper']), seat)
        self.assertEqual(self.c([(22.15, 7.0)])['match'], 'band')                             # a printed seat is a little wide
        self.assertEqual(self.c([(22.25, 7.0, 22.0), (12.0, 10.0)], through=True), seat)       # ... or tapered: its narrow end counts
        self.assertIsNone(self.c([(22.0, 40.0), (12.0, 5.0)], through=True))                  # a long tube, not a seat
        self.assertEqual(self.c([(37.0, 7.0), (30.0, 3.0)], through=True)['kind'], '6805')     # the first row of the file, not its five-digit twin
        self.assertEqual(self.c([(37.0, 7.0), (30.0, 3.0)], through=True, coaxial=['ref 61805 2RS'])['kind'], '61805')
        self.assertEqual(self.c([(9.0, 2.0), (3.4, 8.0)], through=True)['table'], 'screws')    # a washer face over a clearance hole, not a 684 seat
        self.assertEqual(self.c([(9.0, 2.5), (3.4, 8.0)], through=True, coaxial=['ref bearing 684'])['kind'], '684')

    def test_bore_and_unknown(self):
        self.assertIsNone(self.c([(60.0, 7.0)])); self.assertIsNone(self.c([(8.3, 12.0)], through=True)); self.assertIsNone(self.c([]))
        self.assertFalse(G.is_hole(segs((60.0, 7.0)), None, 30.0)); self.assertTrue(G.is_hole(segs((8.3, 12.0)), None, 30.0))
        self.assertTrue(G.is_hole(segs((47.0, 12.0)), {'role': 'bearing_seat'}, 30.0))
        self.assertTrue(G.is_hole(segs((40.0, 5.0), (8.3, 12.0)), None, 30.0))    # a small hole at the bottom of a recess

    def test_profile_and_what_differs(self):
        prof = G.pocket_profile(TABLES['inserts']['kinds']['M3x4x5'])
        self.assertEqual(prof['without_relief'], segs(LEAD, HOLE))
        self.assertEqual(prof['with_relief'], [{'d': 7.5, 'len': 0.54, 'd_alt': [6.5, 5.9]}] + segs(LEAD, HOLE))
        self.assertEqual(G.match_profile(segs(*POCKET), prof['with_relief'], 0.1, 0.3), 'exact')
        self.assertIsNone(G.match_profile(segs(*POCKET), prof['without_relief'], 0.1, 0.3)); self.assertIsNone(G.match_profile(segs(*POCKET), None, 0.1, 0.3))
        self.assertEqual(G.profile_diff(segs((4.6, 1.5), HOLE), prof['without_relief'], 0.1, 0.3), [{'what': 'd', 'index': 0, 'measured': 4.6, 'expected': 4.4}])
        self.assertEqual(G.profile_diff(segs(LEAD, (3.6, 3.0)), prof['without_relief'], 0.1, 0.3), [{'what': 'len', 'index': 1, 'measured': 3.0, 'expected': 5.0}])
        self.assertEqual(G.profile_diff(segs((4.4, 6.0)), prof['without_relief'], 0.1, 0.3)[0]['what'], 'count')
        self.assertIsNone(G.pocket_profile({'lead_in': {'d': 4.4, 'depth': 1.5}, 'hole': {'d': 3.6, 'depth': 6.5}})['with_relief'])


class Pockets(unittest.TestCase):
    ROW = TABLES['inserts']['kinds']['M3x4x5']

    def test_levels_of_a_pocket_with_a_relief(self):
        p = G.pocket_levels(segs(*POCKET), self.ROW)
        self.assertEqual(p['relief'], {'d': 7.5, 'depth': 0.54}); self.assertEqual(p['top'], 0.54)
        self.assertEqual([(lv['name'], lv['depth'], lv['from_r']) for lv in p['levels']],
                         [('relief', 0.27, 3.75), ('lead_in', 1.29, 2.2), ('insert', 2.94, 2.2), ('insert', 3.74, 2.2), ('insert', 4.54, 2.2)])

    def test_levels_without_a_relief_and_of_a_wrong_shape(self):
        p = G.pocket_levels(segs(LEAD, HOLE), self.ROW)
        self.assertIsNone(p['relief']); self.assertEqual(p['top'], 0.0)
        self.assertEqual([(lv['name'], lv['depth'], lv['from_r']) for lv in p['levels']][:3], [('relief', 0.27, 2.2), ('lead_in', 0.75, 2.2), ('insert', 2.4, 2.2)])
        wide = G.pocket_levels(segs((5.2, 1.5), (4.6, 5.0)), self.ROW)            # D5.2 is no relief: narrower than the smallest one
        self.assertIsNone(wide['relief']); self.assertEqual([lv['from_r'] for lv in wide['levels']], [2.6, 2.6, 2.6, 2.6, 2.6])
        shallow = G.pocket_levels(segs((4.4, 3.0)), self.ROW)                     # a through pocket in a 3 mm plate
        self.assertEqual([lv['depth'] for lv in shallow['levels']], [0.27, 0.75, 2.4, 2.95])

    def test_a_recess_is_not_the_relief(self):
        spot = G.pocket_levels(segs((13.2, 1.0), LEAD, HOLE), self.ROW)           # a pocket at the bottom of a spot face, no relief cut
        self.assertEqual((spot['recess'], spot['relief'], spot['top']), ({'d': 13.2, 'depth': 1.0}, None, 1.0))
        self.assertEqual([(lv['name'], lv['depth'], lv['from_r']) for lv in spot['levels']][:3], [('relief', 1.27, 2.2), ('lead_in', 1.75, 2.2), ('insert', 3.4, 2.2)])
        odd = G.pocket_levels(segs((10.0, 3.0), LEAD, HOLE), self.ROW)            # any wide first step used to count as "relieved"
        self.assertIsNone(odd['relief']); self.assertEqual(odd['recess']['d'], 10.0)
        both = G.pocket_levels(segs((13.2, 1.0), (7.5, 0.54), LEAD, HOLE), self.ROW)       # the relief in the floor of the spot face
        self.assertEqual((both['recess'], both['relief'], both['top']), ({'d': 13.2, 'depth': 1.0}, {'d': 7.5, 'depth': 0.54}, 1.54))
        self.assertEqual([(lv['name'], lv['depth'], lv['from_r']) for lv in both['levels']][:2], [('relief', 1.27, 3.75), ('lead_in', 2.29, 2.2)])
        nested = G.pocket_levels(segs((7.5, 3.0), (6.5, 0.54), LEAD, HOLE), self.ROW)      # a relief diameter over a relief diameter: the first is a recess
        self.assertEqual((nested['recess']['d'], nested['relief']['d']), (7.5, 6.5))
        deep = G.pocket_levels(segs((7.5, 3.0), LEAD, HOLE), self.ROW)            # a table diameter, far too deep: a relief for the judge to fail
        self.assertEqual((deep['recess'], deep['relief']), (None, {'d': 7.5, 'depth': 3.0}))
        fallback = G.pocket_levels(segs((6.5, 0.54), LEAD, HOLE), self.ROW)
        self.assertEqual(fallback['relief'], {'d': 6.5, 'depth': 0.54}); self.assertIsNone(fallback['recess'])
        plain = G.pocket_levels(segs(*POCKET), self.ROW)
        self.assertIsNone(plain['recess'])

    def test_a_pocket_in_a_recess_is_still_that_pocket_for_the_census(self):
        exact = lambda *sg: (G.classify_stack(segs(*sg), False, TABLES) or {}).get('match')
        self.assertEqual(exact((13.2, 1.0), LEAD, HOLE), 'exact'); self.assertEqual(exact((13.2, 1.0), (7.5, 0.54), LEAD, HOLE), 'exact')
        self.assertEqual(exact((10.0, 3.0), LEAD, HOLE), 'exact')
        self.assertEqual(exact((13.2, 1.0), (5.2, 1.5), (4.6, 5.0)), 'band')      # a wrong pocket in a recess is still a wrong pocket
        self.assertEqual(G.classify_stack(segs((4.2, 1.7)), False, SHIPPED)['table'], 'screws')        # a shallow blind hole in the M3 band is no insert pocket
        self.assertEqual(G.classify_stack(segs((4.0, 1.7)), False, SHIPPED), None)                      # (the shipped band has "min_depth")
        self.assertEqual(G.classify_stack(segs((4.4, 6.5)), False, SHIPPED)['table'], 'inserts')       # the old straight pocket, deep enough: stays a wrong-pocket row

    def test_radius_at(self):
        s = segs(*POCKET)
        self.assertEqual([G.radius_at(s, t) for t in (0.0, 0.5, 0.6, 2.0, 2.1, 7.0, 9.0)], [3.75, 3.75, 2.2, 2.2, 1.8, 1.8, 1.8])
        self.assertAlmostEqual(G.radius_at([{'d': 6.0, 'len': 1.3, 'd2': 3.4}], 0.65), 2.35)

    def test_ray_directions_cover_the_circle_square_to_the_axis(self):
        for axis in ((0, 0, 1), (1, 0, 0), G.v_unit((1, 2, 3))):
            dirs = [G.ray_direction(axis, k, 48) for k in range(48)]
            for d in dirs: self.assertAlmostEqual(G.v_dot(d, axis), 0.0); self.assertAlmostEqual(G.v_len(d), 1.0)
            for i in range(3): self.assertAlmostEqual(sum(d[i] for d in dirs), 0.0)
        self.assertAlmostEqual(G.v_dist(G.ray_direction((0, 0, 1), 12, 48), (-1.0, 0.0, 0.0)), 0.0)

    def test_summary_free_blocked_and_mixed(self):
        n, axis = 48, (0, 0, 1)
        thick = G.wall_summary([8.0] * n, axis, 4.0, [None] * n, 8.0)
        self.assertEqual((thick['min'], thick['free_arc_deg'], thick['thin_arc_deg'], thick['blocked_by'], thick['min_beyond']), (8.0, 0.0, 0.0, [], None))
        walls = [8.0] * n
        for k in (11, 12, 13): walls[k] = 2.5 + abs(k - 12) * 0.004               # 2.5 mm towards -x; the two rays beside it read the same to 0.01
        free = G.wall_summary(walls, axis, 4.0, ['free' if w < 4 else None for w in walls], 8.0)
        self.assertEqual((free['min'], free['free_arc_deg'], free['thin_arc_deg'], free['blocked_by'], free['min_beyond']), (2.5, 22.5, 22.5, [], 'free'))
        self.assertEqual(free['dir'], [-1.0, 0.0, 0.0]); self.assertEqual(free['w'][10:15], [8.0, 2.5, 2.5, 2.5, 8.0])    # the middle one of the three
        motor = {'name': 'ref motor', 'kind': 'bought', 'part': None, 'gap': 0.5, 'hidden': False}
        blocked = G.wall_summary(walls, axis, 4.0, [dict(motor, gap=0.5 + abs(k - 12) * 0.3) if w < 4 else None for k, w in enumerate(walls)], 8.0)
        self.assertEqual((blocked['free_arc_deg'], blocked['blocked_by'], blocked['min_beyond']), (0.0, [motor], 'ref motor'))
        own = {'name': 'own cavity', 'kind': 'own_cavity', 'part': 'demo/bracket', 'gap': 0.0, 'width': 4.4}
        mixed = G.wall_summary(walls, axis, 4.0, [None] * 11 + ['free', motor, own] + [None] * 34, 8.0)
        self.assertEqual(mixed['free_arc_deg'], 7.5); self.assertEqual(mixed['blocked_by'], [own, motor]); self.assertEqual(mixed['min_beyond'], 'ref motor')
        unknown = G.wall_summary(walls, axis, 4.0, None, 8.0)                     # nobody looked beyond: never "blocked"
        self.assertEqual((unknown['free_arc_deg'], unknown['blocked_by']), (22.5, []))
        self.assertEqual(G.wall_summary([4.0] * n, axis, 4.0)['thin_arc_deg'], 0.0)           # a wall of exactly the target is not thin
        self.assertEqual(G.wall_summary([4.004] * n, axis, 4.0)['min'], 4.0)
        self.assertEqual(G.wall_summary([3.99] * n, axis, 4.0)['thin_arc_deg'], 360.0)
        self.assertEqual(G.wall_summary([9.7] * n, axis, 4.0, cap=8.0)['min'], 8.0)

    def test_relief_room(self):
        exits = [4.7] + [12.0] * 47                                              # the part ends 4.7 from the axis on one side
        self.assertEqual(G.relief_room(exits, 2.2, [7.5, 6.5, 5.9]), [{'d': 7.5, 'wall': 0.95}, {'d': 6.5, 'wall': 1.45}, {'d': 5.9, 'wall': 1.75}])
        self.assertEqual(G.relief_room(exits, 3.75, [7.5, 6.5]), [{'d': 7.5, 'wall': 0.95}, {'d': 6.5, 'wall': 1.45}])    # what a smaller relief would leave
        self.assertEqual(G.relief_room([3.0] * 48, 2.2, [7.5])[0]['wall'], 0.0)                 # it would break out


class Shipped(unittest.TestCase):
    """The classifier on the tables the plugin ships. Their numbers belong to another package: only what must hold for
    every row is checked."""
    def test_measured_rows_match_their_own_profile_and_recorded_rows_never_do(self):
        kinds = SHIPPED['inserts']['kinds']
        self.assertIn('M3x4x5', kinds)
        for kind, row in kinds.items():
            relief, lead, hole = row['relief'], row['lead_in'], row['hole']
            if row.get('profile') == 'recorded' or lead['depth'] <= 0:
                for stack in ([(hole['d'], hole['depth'])], [(relief['d'], relief['depth']), (hole['d'], hole['depth'])]):
                    c = G.classify_stack(segs(*stack), False, SHIPPED)
                    self.assertEqual((c['table'], c['kind'], c['match']), ('inserts', kind, 'band'), kind)
            else:
                own = [(relief['d'], relief['depth']), (lead['d'], lead['depth']), (hole['d'], round(hole['depth'] - lead['depth'], 3))]
                want = {'table': 'inserts', 'kind': kind, 'role': 'insert_pocket', 'match': 'exact'}
                self.assertEqual(G.classify_stack(segs(*own), False, SHIPPED), want, kind)
                self.assertEqual(G.classify_stack(segs(*own[1:]), False, SHIPPED), want, kind)
                straight = G.classify_stack(segs((lead['d'], hole['depth'])), False, SHIPPED)
                self.assertEqual((straight['table'], straight['match']), ('inserts', 'band'), kind)
                levels = G.pocket_levels(segs(*own), row)
                self.assertEqual(levels['relief'], {'d': relief['d'], 'depth': relief['depth']}, kind)
                self.assertEqual([lv['name'] for lv in levels['levels']], ['relief', 'lead_in', 'insert', 'insert', 'insert'], kind)

    def test_screw_and_bearing_rows(self):
        for size, row in SHIPPED['screws']['sizes'].items():
            bore = row['heads']['cap']['counterbore_d']
            got = G.classify_stack(segs((bore, row['heads']['cap']['h'] + 0.5), (row['clearance_d'], 6.0)), True, SHIPPED)
            self.assertEqual(got, {'table': 'screws', 'kind': size, 'role': 'counterbore', 'match': 'exact'}, size)
        for name, row in SHIPPED['bearings']['sizes'].items():
            self.assertIsNone(G.classify_stack(segs((row['D'] + 40.0, row['B'])), True, SHIPPED), name)      # a diameter alone, and through: never a seat
            seat = G.classify_stack(segs((row['D'], row['B']), (row['D'] - 3.0, 2.0)), True, SHIPPED, ['ref bearing ' + name])
            self.assertEqual((seat['table'], seat['role']), ('bearings', 'bearing_seat'), name)


def box_mesh(lo, hi):
    """a closed box as flat node coordinates and triangle indices"""
    c = [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    idx = []
    for a, b, d, e in quads: idx += [a, b, d, a, d, e]
    return [v for p in c for v in p], idx


class Misc(unittest.TestCase):
    def test_mesh_contains(self):
        coords, idx = box_mesh((1.0, 2.0, 3.0), (4.0, 6.0, 5.0))
        m = G.mesh_grid(coords, idx, cell=5.0, scale=10.0)                       # cm -> mm
        self.assertEqual((m['lo'], m['hi']), ((10.0, 20.0, 30.0), (40.0, 60.0, 50.0)))
        for p, want in (((25.0, 41.3, 37.7), True), ((11.0, 21.2, 31.9), True), ((39.0, 58.7, 48.1), True), ((9.0, 41.3, 37.7), False),
                        ((41.0, 41.3, 37.7), False), ((25.0, 61.0, 37.7), False), ((25.0, 41.3, 29.0), False), ((25.0, 19.0, 51.0), False)):
            self.assertEqual(G.mesh_contains(m, p), want, p)
        self.assertIsNone(G.mesh_grid([], [])); self.assertFalse(G.mesh_contains(None, (0, 0, 0)))

    def test_boxes_and_matrices(self):
        c, s = math.cos(0.5), math.sin(0.5)
        a = (c, -s, 0, 10.0, s, c, 0, -5.0, 0, 0, 1, 13.0, 0, 0, 0, 1)
        b = (1, 0, 0, 2.0, 0, 0, -1, 0.0, 0, 1, 0, 1.0, 0, 0, 0, 1)
        p = (3.0, 4.0, 5.0)
        for i in range(3): self.assertAlmostEqual(G.mat_point(G.mat_mul(a, b), p)[i], G.mat_point(a, G.mat_point(b, p))[i])
        for i in range(16): self.assertAlmostEqual(G.mat_mul(a, G.mat_inverse(a))[i], G.IDENTITY[i])
        quarter = (0, -1, 0, 100.0, 1, 0, 0, 0.0, 0, 0, 1, 13.0, 0, 0, 0, 1)
        lo, hi = G.box_transform(quarter, (0, 0, 0), (40, 30, 12))
        self.assertEqual((lo, hi), ((70.0, 0.0, 13.0), (100.0, 40.0, 25.0)))
        self.assertTrue(G.boxes_near((0, 0, 0), (1, 1, 1), (1.5, 0, 0), (2, 1, 1), 0.5)); self.assertFalse(G.boxes_near((0, 0, 0), (1, 1, 1), (1.6, 0, 0), (2, 1, 1), 0.5))
        self.assertTrue(G.box_has((0, 0, 0), (1, 1, 1), (0.5, 1.0, 0.0))); self.assertFalse(G.box_has((0, 0, 0), (1, 1, 1), (0.5, 1.1, 0.0)))

    def test_cheap_signature(self):
        box = ((0, 0, 0), (40, 30, 10))
        a = G.cheap_signature(11500.0, 2900.0, (7, 12, 8), box)
        self.assertEqual(len(a), 16); self.assertEqual(a, G.cheap_signature(11500.004, 2900.0, (7, 12, 8), box))
        self.assertNotEqual(a, G.cheap_signature(11500.0, 2900.0, (8, 12, 8), box)); self.assertNotEqual(a, G.cheap_signature(11500.0, 2900.0, (7, 12, 8), ((0, 0, 0), (40, 30, 11))))

    def test_opening_shapes(self):
        rect = [(10, 5, 12), (22, 5, 12), (22, 9, 12), (10, 9, 12)]
        s = G.opening_shape(rect, (0, 0, 1))
        self.assertEqual((s['point'], s['size'], s['round'], s['shape']), ((16.0, 7.0, 12.0), [12.0, 4.0], False, 'slot'))
        turned = [(0, 0, 0), (0, 3, 3), (0, 1, 5), (0, -2, 2)]                    # 4.24 x 2.83, turned 45 degrees in the y-z plane
        t = G.opening_shape(turned, (1, 0, 0))
        self.assertEqual((t['size'], t['shape']), ([4.24, 2.83], 'window')); self.assertAlmostEqual(G.v_dist(t['point'], (0, 0.5, 2.5)), 0.0, places=3)
        self.assertEqual(G.opening_shape(turned, (1, 0, 0), arcs=2)['shape'], 'slot')         # curved ends: 1.3 times as long is a slot
        ring = [(5 + 3 * math.cos(k / 14.0 * 2 * math.pi), 3 * math.sin(k / 14.0 * 2 * math.pi), 0) for k in range(14)]
        self.assertTrue(G.opening_shape(ring, (0, 0, 1), arcs=2, edges=2)['round'])          # a circle cut in two arcs
        self.assertFalse(G.opening_shape(ring, (0, 0, 1), arcs=2, edges=4)['round'])         # two arcs and two lines: an obround slot end to end
        hexagon = [(4 * math.cos(k * math.pi / 3), 4 * math.sin(k * math.pi / 3), 0) for k in range(6)]
        self.assertFalse(G.opening_shape(hexagon, (0, 0, 1), arcs=0, edges=6)['round'])      # a nut trap: its corners lie on a circle, its edges are straight
        self.assertFalse(G.opening_shape(rect * 2, (0, 0, 1))['round'])
        self.assertIsNone(G.opening_shape(rect[:2], (0, 0, 1)))


# ---- a fake adsk: an analytic solid behind the calls the probes make ---------------------------------------------------------

class V:
    """Point3D / Vector3D in cm"""
    def __init__(self, x, y, z): self.x, self.y, self.z = float(x), float(y), float(z)
    def asArray(self): return (self.x, self.y, self.z)
    def normalize(self):
        n = math.sqrt(self.x ** 2 + self.y ** 2 + self.z ** 2); self.x, self.y, self.z = self.x / n, self.y / n, self.z / n
        return True
    def distanceTo(self, o): return math.sqrt((self.x - o.x) ** 2 + (self.y - o.y) ** 2 + (self.z - o.z) ** 2)


def P(p):
    """an (x, y, z) in mm as an API point in cm"""
    return V(p[0] / 10.0, p[1] / 10.0, p[2] / 10.0)


class Coll(list):
    count = property(len)
    def item(self, i): return self[i]


class Mat:
    """Matrix3D: row-major, translation in cm"""
    def __init__(self, mm): self.c = [v / 10.0 if i in (3, 7, 11) else v for i, v in enumerate(mm)]
    def asArray(self): return tuple(self.c)
    def getCell(self, i, j): return self.c[4 * i + j]
    translation = property(lambda self: V(self.c[3], self.c[7], self.c[11]))


def placement(dx=0.0, dy=0.0, dz=0.0, turn=0.0):
    """16 floats in mm: a turn round z (degrees), then a move"""
    c, s = math.cos(math.radians(turn)), math.sin(math.radians(turn))
    return (c, -s, 0.0, dx, s, c, 0.0, dy, 0.0, 0.0, 1.0, dz, 0.0, 0.0, 0.0, 1.0)


class Cylinder:
    objectType, surfaceType = 'adsk::core::Cylinder', 1
    def __init__(self, origin, axis, radius): self.origin, self._axis, self.radius = P(origin), axis, radius / 10.0
    axis = property(lambda self: V(*self._axis))         # a new vector each time, as the API gives one


class Cone(Cylinder):
    objectType, surfaceType = 'adsk::core::Cone', 2


class Edge:
    def __init__(self, kind, fn, centre=None, radius=None):
        self.geometry = types.SimpleNamespace(objectType='adsk::core::' + kind, center=None if centre is None else P(centre), radius=None if radius is None else radius / 10.0)
        self.startVertex, self.endVertex = types.SimpleNamespace(geometry=P(fn(0.0))), types.SimpleNamespace(geometry=P(fn(1.0)))
        self.evaluator = types.SimpleNamespace(getParameterExtents=lambda: (True, 0.0, 1.0), getPointAtParameter=lambda t: (True, P(fn(t))))


def line(a, b):
    return Edge('Line3D', lambda t: tuple(a[i] + (b[i] - a[i]) * t for i in range(3)))


def loop(edges, outer=False):
    return types.SimpleNamespace(isOuter=outer, edges=Coll(edges))


class Face:
    def __init__(self, geometry, area, centroid, on, edges=(), loops=()):
        self.geometry, self.area, self.centroid, self.pointOnFace = geometry, area / 100.0, P(centroid), P(on)
        self.edges, self.loops = Coll(edges), Coll(loops)
        pts = [e.startVertex.geometry for e in self.edges] + [e.evaluator.getPointAtParameter(t)[1] for e in self.edges for t in (0.25, 0.5, 0.75)] or [self.centroid]
        self.boundingBox = types.SimpleNamespace(minPoint=V(min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)),
                                                 maxPoint=V(max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))


class Solid:
    """A box with stacks drilled into it, rectangular cuts from its top face and round bosses; mm, its own frame.
    holes   [{"o": centre of the mouth, "a": unit axis out of the material, "segs": [(d, len) or (d, len, d2)] from the
             mouth inwards, "split": 2 (the wall is two half faces)}]
    cuts    [(x0, y0, x1, y1, depth)] from the top face downwards
    bosses  [{"o": centre of the base, "a": unit axis, "r", "h"}] added material"""
    def __init__(self, lo=None, hi=None, holes=(), cuts=(), bosses=()):
        self.lo, self.hi, self.holes, self.cuts, self.bosses = lo, hi, list(holes), list(cuts), list(bosses)
        self._h = []
        for h in self.holes:
            t, spans = 0.0, []
            for s in h['segs']:
                spans.append((t, t + s[1], s[0] / 2.0, (s[2] if len(s) > 2 else s[0]) / 2.0)); t += s[1]
            self._h.append((h['o'], h['a'], spans, max(max(s[2], s[3]) for s in spans) ** 2))

    def box(self):
        los, his = ([self.lo] if self.lo else []), ([self.hi] if self.hi else [])
        for b in self.bosses:
            top = G.v_add(b['o'], G.v_scale(b['a'], b['h']))
            los.append(tuple(min(b['o'][i], top[i]) - b['r'] * (1 - abs(b['a'][i])) for i in range(3)))
            his.append(tuple(max(b['o'][i], top[i]) + b['r'] * (1 - abs(b['a'][i])) for i in range(3)))
        return tuple(min(p[i] for p in los) for i in range(3)), tuple(max(p[i] for p in his) for i in range(3))

    def inside(self, p):
        ok = self.lo is not None and all(self.lo[i] < p[i] < self.hi[i] for i in range(3))
        if not ok:
            for b in self.bosses:
                w = G.v_sub(p, b['o']); t = G.v_dot(w, b['a'])
                if 0.0 < t < b['h'] and G.v_dot(w, w) - t * t < b['r'] ** 2: ok = True; break
            if not ok: return False
        for o, a, spans, rmax2 in self._h:
            w = (p[0] - o[0], p[1] - o[1], p[2] - o[2])
            t = -(w[0] * a[0] + w[1] * a[1] + w[2] * a[2])                        # depth below the mouth
            rr = w[0] * w[0] + w[1] * w[1] + w[2] * w[2] - t * t
            if rr > rmax2 or t < 0: continue
            for t0, t1, r0, r1 in spans:
                if t0 <= t <= t1:
                    r = r0 + (r1 - r0) * (t - t0) / (t1 - t0)
                    if rr < r * r: return False
        for x0, y0, x1, y1, depth in self.cuts:
            if x0 < p[0] < x1 and y0 < p[1] < y1 and p[2] > self.hi[2] - depth: return False
        return True

    def faces(self, m):
        T = lambda q: G.mat_point(m, q)
        R = lambda v: G.mat_vector(m, v)
        out = []
        for h in self.holes:
            o, a, t, parts = h['o'], h['a'], 0.0, h.get('split', 1)
            u, v = G.axis_frame(a)
            for i, s in enumerate(h['segs']):
                d, ln, d2 = s[0], s[1], (s[2] if len(s) > 2 else None)
                top, bot = G.v_sub(o, G.v_scale(a, t)), G.v_sub(o, G.v_scale(a, t + ln))
                geo = (Cone if d2 is not None else Cylinder)(T(o), R(a if i % 2 == 0 else G.v_scale(a, -1.0)), d / 2.0)
                r_bot = (d2 if d2 is not None else d) / 2.0
                for k in range(parts):
                    def arc(c, r, k=k):
                        return lambda x: T(G.v_add(c, G.v_add(G.v_scale(u, r * math.cos(2 * math.pi * (k + x) / parts)), G.v_scale(v, r * math.sin(2 * math.pi * (k + x) / parts)))))
                    edges = [Edge('Circle3D' if parts == 1 else 'Arc3D', arc(top, d / 2.0), T(top), d / 2.0), Edge('Circle3D' if parts == 1 else 'Arc3D', arc(bot, r_bot), T(bot), r_bot)]
                    if parts > 1: edges += [line(arc(top, d / 2.0)(0.0), arc(bot, r_bot)(0.0)), line(arc(top, d / 2.0)(1.0), arc(bot, r_bot)(1.0))]
                    out.append(Face(geo, math.pi * (d / 2.0 + r_bot) * ln / parts, T(G.v_sub(o, G.v_scale(a, t + ln / 2.0))), arc(top, d / 2.0)(0.3), edges))
                t += ln
        for b in self.bosses:
            top = G.v_add(b['o'], G.v_scale(b['a'], b['h']))
            u, v = G.axis_frame(b['a'])
            ring = lambda c: (lambda x: T(G.v_add(c, G.v_add(G.v_scale(u, b['r'] * math.cos(2 * math.pi * x)), G.v_scale(v, b['r'] * math.sin(2 * math.pi * x))))))
            out.append(Face(Cylinder(T(b['o']), R(b['a']), b['r']), 2 * math.pi * b['r'] * b['h'], T(G.v_add(b['o'], G.v_scale(b['a'], b['h'] / 2.0))), ring(top)(0.1),
                            [Edge('Circle3D', ring(b['o']), T(b['o']), b['r']), Edge('Circle3D', ring(top), T(top), b['r'])]))
        if self.lo is None: return out
        lo, hi = self.lo, self.hi
        for i in range(3):
            j, k = (i + 1) % 3, (i + 2) % 3
            for side in (0, 1):
                def at(cj, ck, i=i, j=j, k=k, side=side):
                    q = [0.0, 0.0, 0.0]; q[i], q[j], q[k] = (hi if side else lo)[i], cj, ck
                    return tuple(q)
                n = [0.0, 0.0, 0.0]; n[i] = 1.0 if side else -1.0
                corners = [at(lo[j], lo[k]), at(hi[j], lo[k]), at(hi[j], hi[k]), at(lo[j], hi[k])]
                loops = [loop([line(T(corners[c]), T(corners[(c + 1) % 4])) for c in range(4)], outer=True)]
                for c, a, r in [(h['o'], h['a'], h['segs'][0][0] / 2.0) for h in self.holes] + [(b['o'], b['a'], b['r']) for b in self.bosses]:
                    if G.v_dot(a, n) > 0.999 and abs(c[i] - at(0, 0)[i]) < 1e-9:      # a mouth or the foot of a boss in this face: one circle
                        e1, e2 = G.axis_frame(tuple(n))
                        loops.append(loop([Edge('Circle3D', lambda x, c=c, r=r, e1=e1, e2=e2: T(G.v_add(c, G.v_add(G.v_scale(e1, r * math.cos(2 * math.pi * x)), G.v_scale(e2, r * math.sin(2 * math.pi * x))))), T(c), r)]))
                if i == 2 and side == 1:
                    for x0, y0, x1, y1, depth in self.cuts:
                        cs = [(x0, y0, hi[2]), (x1, y0, hi[2]), (x1, y1, hi[2]), (x0, y1, hi[2])]
                        loops.append(loop([line(T(cs[c]), T(cs[(c + 1) % 4])) for c in range(4)]))
                normal = R(tuple(-c for c in n) if (i == 2 and side == 1) else tuple(n))      # the top plane's own normal points INTO the material
                geo = types.SimpleNamespace(objectType='adsk::core::Plane', surfaceType=0, normal=V(*normal))
                out.append(Face(geo, (hi[j] - lo[j]) * (hi[k] - lo[k]), T(at((lo[j] + hi[j]) / 2.0, (lo[k] + hi[k]) / 2.0)), T(at(lo[j] + 0.7, lo[k] + 0.7)),
                                [e for lp in loops for e in lp.edges], loops))
        return out


class Body:
    """a B-rep body: the solid seen through a matrix (identity = the native body of its component)"""
    def __init__(self, name, solid, m=G.IDENTITY, on=True):
        self.name, self.solid, self.m, self.inv, self.isSolid, self.isLightBulbOn = name, solid, m, G.mat_inverse(m), True, on
        lo, hi = solid.box()
        self.volume, self.area = (hi[0] - lo[0]) * (hi[1] - lo[1]) * (hi[2] - lo[2]) / 1000.0, 2 * sum((hi[i] - lo[i]) * (hi[(i + 1) % 3] - lo[(i + 1) % 3]) for i in range(3)) / 100.0
        self.edges, self.vertices, self.asked, self._faces = Coll(range(24)), Coll(range(16)), 0, None

    def pointContainment(self, p):
        self.asked += 1
        return 0 if self.solid.inside(G.mat_point(self.inv, (p.x * 10.0, p.y * 10.0, p.z * 10.0))) else 2

    @property
    def faces(self):
        if self._faces is None: self._faces = Coll(self.solid.faces(self.m))
        return self._faces

    @property
    def boundingBox(self):
        lo, hi = G.box_transform(self.m, *self.solid.box())
        return types.SimpleNamespace(minPoint=P(lo), maxPoint=P(hi), contains=lambda p: all(lo[i] <= c * 10.0 <= hi[i] for i, c in enumerate((p.x, p.y, p.z))))


class MeshBody:
    def __init__(self, name, lo, hi, on=True):
        coords, idx = box_mesh([c / 10.0 for c in lo], [c / 10.0 for c in hi])
        self.name, self.isLightBulbOn = name, on
        self.displayMesh = types.SimpleNamespace(nodeCoordinatesAsDouble=coords, nodeIndices=idx)


class Comp:
    def __init__(self, cid, name, bodies=(), meshes=()):
        self.id, self.name, self.bRepBodies, self.meshBodies = cid, name, Coll(bodies), Coll(meshes)


class Occ:
    def __init__(self, comp, path, m=G.IDENTITY, on=True):
        self.component, self.fullPathName, self.name, self.m, self.transform2 = comp, path, path.split('+')[-1], m, Mat(m)
        self.isLightBulbOn, self.entityToken, self.isReferencedComponent, self.childOccurrences = on, 'token of ' + path, False, []
        self.bRepBodies = Coll(Body(b.name, b.solid, m, b.isLightBulbOn) for b in comp.bRepBodies)


def application(occs, root_bodies=(), name='Demo Assembly v7'):
    root = Comp('root', 'Demo Assembly', root_bodies)
    root.allOccurrences = Coll(occs)
    design = types.SimpleNamespace(rootComponent=root)
    doc = types.SimpleNamespace(name=name, dataFile=types.SimpleNamespace(versionNumber=7, id='file-1'), products=types.SimpleNamespace(itemByProductType=lambda t: design))
    return types.SimpleNamespace(documents=[doc], activeProduct=design, design=design)


@contextlib.contextmanager
def fake_adsk(app):
    core, fusion, adsk = types.ModuleType('adsk.core'), types.ModuleType('adsk.fusion'), types.ModuleType('adsk')
    core.Application = types.SimpleNamespace(get=lambda: app)
    core.Point3D = core.Vector3D = types.SimpleNamespace(create=lambda x, y, z: V(x, y, z))
    core.Cylinder, core.Cone = Cylinder, Cone
    fusion.Design = types.SimpleNamespace(cast=lambda x: x)
    fusion.PointContainment = types.SimpleNamespace(PointInsidePointContainment=0, PointOnPointContainment=1, PointOutsidePointContainment=2)
    tb = types.SimpleNamespace(createCylinderOrCone=lambda p0, r0, p1, r1: ('tool', r0), booleanOperation=lambda a, b, op: True)
    fusion.TemporaryBRepManager = types.SimpleNamespace(get=lambda: tb)
    fusion.BooleanTypes = types.SimpleNamespace(DifferenceBooleanType=0, IntersectionBooleanType=1, UnionBooleanType=2)
    adsk.core, adsk.fusion = core, fusion
    mods = {'adsk': adsk, 'adsk.core': core, 'adsk.fusion': fusion}
    saved = {k: sys.modules.get(k) for k in mods}
    sys.modules.update(mods)
    try:
        yield
    finally:
        for k, old in saved.items():
            if old is None: sys.modules.pop(k, None)
            else: sys.modules[k] = old


# the demo part, mm, in its own frame: a plate 86 x 40 x 12 with these features in its top face (z = 12)
UP, DOWN = (0.0, 0.0, 1.0), (0.0, 0.0, -1.0)
AT = {'p1': (36.0, 16.0), 'p2': (4.7, 20.0), 'p3': (6.2, 8.0), 'p4': (6.0, 32.0), 'p5': (50.0, 36.2), 'p6': (20.0, 6.0),
      's1': (48.0, 5.0), 't1': (40.0, 30.0), 'bore': (66.0, 20.0), 'top': (80.0, 4.0)}
NOT_PRINTED = ['^ref ', '^HW']


def demo(place=placement(100.0, 0.0, 13.0), mesh=False):
    def hole(key, *profile, **kw):
        return dict({'o': AT[key] + (12.0,), 'a': UP, 'segs': list(profile)}, **kw)
    bracket = Solid((0.0, 0.0, 0.0), (86.0, 40.0, 12.0), holes=[
        hole('p1', *POCKET),                             # room on every side
        hole('p2', *POCKET, split=2),                    # 2.5 mm from the edge at x = 0: 0.95 beside the relief; its wall is two half faces
        hole('p3', LEAD, HOLE),                          # exactly 4.0 from the edge, no relief
        hole('p4', *POCKET),                             # 3.8 from the edge
        hole('p5', *POCKET),                             # 1.6 from the edge at y = 40, and a bought part stands 0.3 beyond it
        hole('p6', (4.4, 6.0)),                          # an old straight pocket, blind
        hole('s1', (6.5, 3.0), (3.4, 9.0)),              # a screw: counterbore + clearance, through
        hole('t1', (4.4, 12.0)),                         # straight and through, with an insert ref in it
        hole('bore', (32.0, 12.0)),                      # the opening of the part: not a hole
        hole('top', (3.4, 5.0)),                         # blind from the top ...
        {'o': AT['top'] + (0.0,), 'a': DOWN, 'segs': [(3.4, 6.16)]},             # ... and its other half from below: 0.84 between them
    ], cuts=[(14.0, 27.0, 26.0, 31.0, 12.0),             # a slot through the plate
             (30.0, 34.0, 34.0, 37.0, 0.4)])             # an engraved recess
    motor = Solid((40.0, 40.3, 0.0), (60.0, 50.0, 12.0))
    cover = Solid((0.0, 14.0, 12.2), (12.0, 26.0, 15.0), holes=[{'o': AT['p2'] + (15.0,), 'a': UP, 'segs': [(3.4, 2.8)]}])
    insert1 = Solid(bosses=[{'o': AT['p1'] + (7.46,), 'a': UP, 'r': 2.5, 'h': 4.0}])
    insert2 = Solid(bosses=[{'o': AT['t1'] + (8.0,), 'a': UP, 'r': 2.5, 'h': 4.0}])
    screw = Solid(bosses=[{'o': AT['s1'] + (1.0,), 'a': UP, 'r': 1.5, 'h': 8.0}])
    comps = [Comp('c-bracket', 'Bracket', [Body('Body1', bracket), Body('old tool body', Solid((0.0, 0.0, 20.0), (5.0, 5.0, 25.0)), on=False)]),
             Comp('c-motor', 'ref motor', [Body('Body1', motor)]), Comp('c-cover', 'Cover', [Body('Body1', cover)]),
             Comp('c-hw', 'HW (ref)', [Body('insert M3x4x5 (1)', insert1), Body('insert M3x4x5 (2)', insert2), Body('M3x10 screw (1)', screw)])]
    occs = [Occ(comps[0], 'Bracket:1', place), Occ(comps[1], 'ref motor:1', place), Occ(comps[2], 'Cover:1', place), Occ(comps[3], 'Bracket:1+HW (ref):1', place)]
    occs.append(Occ(comps[2], 'Cover:2', G.mat_mul(place, placement(0.0, 0.0, 60.0)), on=False))                      # a second, hidden copy far above
    if mesh:
        occs.append(Occ(Comp('c-chain', 'ref chain', meshes=[MeshBody('MeshBody1', (-3.0, 14.0, 0.0), (-0.4, 26.0, 12.0))]), 'ref chain:1', place))
    return application(occs)


PARAMS = {'inserts.wall_target': 4.0, 'inserts.wall_floor': 1.6, 'inserts.relief_wall_min': 1.2, 'screws.ref_pattern': '(?i)screw|bolt', 'washers.ref_pattern': '(?i)washer'}


class Units(unittest.TestCase):
    """runs one unit script the way Fusion would, against the fake"""
    @classmethod
    def setUpClass(cls):
        cls.tmp = os.path.realpath(tempfile.mkdtemp())
        cls.addClassCleanup(shutil.rmtree, cls.tmp)

    @classmethod
    def unit(cls, app, probe, **kw):
        base = dict(unit_id='u-0001', run='r1', probe=probe, doc='Demo Assembly', doc_key='demo', out=os.path.join(cls.tmp, 'gate', 'run', 'r1', 'units', probe + '.json'),
                    params=PARAMS, tables=TABLES, not_printed=NOT_PRINTED, deadline_s=30)
        if probe != 'fusion_scan': base.update(part='demo/bracket', component_id='c-bracket', occurrence='Bracket:1', body='Body1')
        base.update(kw)
        job = gate_fusion_rpc.new_job(**base)
        if os.path.exists(job['out']): os.remove(job['out'])
        with fake_adsk(app):
            g = {}
            exec(compile(gate_fusion_rpc.build_script(job), probe + '.py', 'exec'), g)
            g['run']('')
        with open(job['out'], encoding='utf-8') as f: env = json.load(f)
        if env['error']: raise AssertionError(env['error'])
        # every call of every probe: the file names the call by the cursor as it was SENT, whatever the probe did with it
        assert env['asked']['cursor_key'] == gate_fusion_rpc.cursor_key(kw.get('cursor')), probe
        return env

    @classmethod
    def whole(cls, app, probe, **kw):
        """every call of a unit until it is done: (merged facts, number of calls, last envelope)"""
        facts, calls, cursor = None, 0, None
        while True:
            env = cls.unit(app, probe, cursor=cursor, **kw)
            facts, calls, cursor = gate_fusion_rpc.merge_facts(facts, env['facts']), calls + 1, env['cursor']
            if env['done']: return facts, calls, env
            assert calls < 2000, 'the unit never finishes'


def by_place(holes):
    """census holes by the name of the place they are drilled at"""
    out = {}
    for h in holes:
        for key, (x, y) in AT.items():
            if abs(h['at']['point'][0] - x) < 0.01 and abs(h['at']['point'][1] - y) < 0.01:
                out[key if h['at']['axis'][2] > 0 else key + '_below'] = h
    return out


class Scan(Units):
    def test_parts_refs_copies_and_signatures(self):
        app = demo(mesh=True)
        facts, calls, env = self.whole(app, 'fusion_scan')
        self.assertEqual((calls, env['sig'], env['doc_version'], facts['occurrences']), (1, None, 7, 7))
        parts = {p['name']: p for p in facts['parts']}
        self.assertEqual(sorted(parts), ['Bracket', 'Cover'])
        b = parts['Bracket']
        self.assertEqual((b['component_id'], b['occurrence'], b['body'], b['copies'], b['visible'], b['external']), ('c-bracket', 'Bracket:1', 'Body1', 1, True, False))
        self.assertEqual(b['bbox'], [[0.0, 0.0, 0.0], [86.0, 40.0, 12.0]]); self.assertEqual(b['matrix'][3::4][:3], [100.0, 0.0, 13.0])
        self.assertEqual(len(b['sig']), 64); self.assertEqual(len(b['pose']), 16); self.assertEqual(b['faces'], 6 + 24)
        self.assertEqual((parts['Cover']['copies'], parts['Cover']['copy_paths'], parts['Cover']['occurrence']), (2, ['Cover:2'], 'Cover:1'))
        refs = {(r['name'], r['occurrence']): r for r in facts['refs']}
        self.assertEqual(sorted(n for n, _ in refs), ['Bracket / old tool body', 'HW (ref) / M3x10 screw (1)', 'HW (ref) / insert M3x4x5 (1)',
                                                    'HW (ref) / insert M3x4x5 (2)', 'ref chain', 'ref motor'])
        motor = refs[('ref motor', 'ref motor:1')]
        self.assertEqual((motor['mesh'], motor['primitive'], motor['visible'], len(motor['sig'])), (False, True, True, 16))
        self.assertEqual(motor['bbox_doc'], [[140.0, 40.3, 13.0], [160.0, 50.0, 25.0]])
        self.assertFalse(refs[('Bracket / old tool body', 'Bracket:1')]['visible'])                 # a body switched off is not a part
        chain = refs[('ref chain', 'ref chain:1')]
        self.assertEqual((chain['mesh'], chain['faces'], chain['bbox_doc']), (True, 12, [[97.0, 14.0, 13.0], [99.6, 26.0, 25.0]]))

    def test_signature_follows_the_shape_not_the_pose(self):
        here = {p['name']: p for p in self.whole(demo(), 'fusion_scan')[0]['parts']}
        turned = {p['name']: p for p in self.whole(demo(place=placement(-20.0, 55.0, 3.0, turn=90.0)), 'fusion_scan')[0]['parts']}
        self.assertEqual(here['Bracket']['sig'], turned['Bracket']['sig']); self.assertNotEqual(here['Bracket']['pose'], turned['Bracket']['pose'])
        moved = dict(AT)
        try:
            AT['p1'] = (36.0, 18.0)                                               # one pocket moves 2 mm: same volume, area and counts
            other = {p['name']: p for p in self.whole(demo(), 'fusion_scan')[0]['parts']}
        finally:
            AT.update(moved)
        self.assertNotEqual(here['Bracket']['sig'], other['Bracket']['sig']); self.assertEqual(here['Bracket']['volume'], other['Bracket']['volume'])
        self.assertEqual(here['Cover']['sig'], other['Cover']['sig'])

    def test_a_ref_whose_hole_moves_inside_its_outline_has_another_shape(self):
        # a bought plate: same volume, area, counts and box when its hole moves 5 mm. The cheap "sig" cannot tell, the
        # "shape" (its faces) must: the units of the printed parts next to it depend on it
        def plate(x):
            body = Solid((0.0, 0.0, 0.0), (40.0, 30.0, 12.0), holes=[{'o': (x, 15.0, 12.0), 'a': UP, 'segs': [(3.4, 12.0)]}])
            occs = [Occ(Comp('c-plate', 'ref plate', [Body('Body1', body)]), 'ref plate:1', placement(0.0, 0.0, 0.0)),
                    Occ(Comp('c-chain', 'ref chain', meshes=[MeshBody('MeshBody1', (-3.0, 14.0, 0.0), (-0.4 + x / 100.0, 26.0, 12.0))]), 'ref chain:1',
                        placement(0.0, 0.0, 0.0))]
            return {r['name']: r for r in self.whole(application(occs), 'fusion_scan')[0]['refs']}
        a, b, again = plate(10.0), plate(15.0), plate(10.0)
        self.assertEqual(a['ref plate']['sig'], b['ref plate']['sig'])                     # the cheap signature does not see it
        self.assertEqual(a['ref plate']['bbox_doc'], b['ref plate']['bbox_doc'])
        self.assertEqual(len(a['ref plate']['shape']), 16)
        self.assertNotEqual(a['ref plate']['shape'], b['ref plate']['shape'])
        self.assertEqual(a['ref plate']['shape'], again['ref plate']['shape'])               # and it is the same for the same shape
        self.assertEqual(len(a['ref chain']['shape']), 16); self.assertNotEqual(a['ref chain']['shape'], b['ref chain']['shape'])

    def test_cursor_limit_and_ping(self):
        app = demo()
        ping = self.unit(app, 'fusion_scan', args={'limit': 0})
        self.assertEqual((ping['facts'], ping['done'], ping['open_documents']), ({'parts': [], 'refs': []}, True, ['Demo Assembly']))
        one, _, _ = self.whole(app, 'fusion_scan')
        steps, calls, _ = self.whole(app, 'fusion_scan', args={'limit': 2})
        self.assertEqual(calls, 3); self.assertEqual(steps, one)
        first = self.unit(app, 'fusion_scan', args={'limit': 2})
        self.assertEqual((first['done'], first['cursor'], [p['name'] for p in first['facts']['parts']]), (False, 2, ['Bracket']))

    def test_everything_under_a_bought_assembly_is_a_ref(self):
        gear = Comp('c-gear', 'Planet carrier', [Body('Body1', Solid((0.0, 0.0, 0.0), (10.0, 10.0, 5.0)))])
        app = application([Occ(Comp('c-box', 'ref gearbox'), 'ref gearbox:1'), Occ(gear, 'ref gearbox:1+Planet carrier:1'), Occ(gear, 'Planet carrier:2', on=False)],
                          root_bodies=[Body('Base', Solid((0.0, 0.0, -5.0), (50.0, 50.0, 0.0)))])
        facts, _, _ = self.whole(app, 'fusion_scan')
        self.assertEqual([(p['name'], p['occurrence'], p['visible'], p['copies']) for p in facts['parts']],
                         [('Demo Assembly', None, True, 1), ('Planet carrier', 'Planet carrier:2', False, 1)])     # hidden, still a part
        self.assertEqual([(r['name'], r['occurrence']) for r in facts['refs']], [('Planet carrier', 'ref gearbox:1+Planet carrier:1')])


class Census(Units):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = demo()
        cls.facts, cls.calls, cls.env = cls.whole(cls.app, 'fusion_census')
        cls.holes = by_place(cls.facts['holes'])

    def test_every_hole_is_found_once_and_the_bore_is_not_a_hole(self):
        self.assertEqual(self.calls, 1); self.assertEqual(len(self.env['sig']), 64)
        self.assertEqual(sorted(self.holes), ['p1', 'p2', 'p3', 'p4', 'p5', 'p6', 's1', 't1', 'top', 'top_below'])
        self.assertEqual(len(self.facts['holes']), 10)
        self.assertEqual([(b['segments'], b['through']) for b in self.facts['bores']], [(segs((32.0, 12.0)), True)])
        c = self.facts['counts']
        self.assertEqual((c['holes'], c['insert_pocket'], c['counterbore'], c['clearance'], c['unknown'], c['bores'], c['openings']), (10, 7, 1, 2, 0, 1, 1))
        self.assertEqual(c.get('outer', 0), 0); self.assertEqual(self.facts['limits']['hole_d_max'], 30.0)
        ids = [h['id'] for h in self.facts['holes']]
        self.assertEqual(len(set(ids)), 10); self.assertTrue(all(i.startswith('h') for i in ids))

    def test_segments_classes_and_anchors_in_the_part_frame(self):
        exact = {'table': 'inserts', 'kind': 'M3x4x5', 'role': 'insert_pocket', 'match': 'exact'}
        p1 = self.holes['p1']
        self.assertEqual((p1['segments'], p1['depth'], p1['through'], p1['exit'], p1['class']), (segs(*POCKET), 7.04, False, None, exact))
        self.assertEqual(p1['anchor'], {'kind': 'insert_pocket', 'axis': [0.0, 0.0, 1.0], 'point': [36.0, 16.0, 12.0]})     # not the document's 136, 16, 25
        self.assertEqual(self.holes['p2']['segments'], segs(*POCKET)); self.assertEqual(self.holes['p2']['class'], exact)   # two half faces per segment
        self.assertEqual(self.holes['p3']['segments'], segs(LEAD, HOLE)); self.assertEqual(self.holes['p3']['class'], exact)
        self.assertEqual(self.holes['p6']['class'], dict(exact, match='band')); self.assertEqual(self.holes['p6']['anchor']['kind'], 'insert_pocket')
        s1 = self.holes['s1']
        self.assertEqual((s1['segments'], s1['through'], s1['floor']), (segs((6.5, 3.0), (3.4, 9.0)), True, None))
        self.assertEqual(s1['class'], {'table': 'screws', 'kind': 'M3', 'role': 'counterbore', 'match': 'exact'})
        self.assertEqual(s1['anchor'], {'kind': 'hole', 'axis': [0.0, 0.0, 1.0], 'point': [48.0, 5.0, 12.0]})
        self.assertEqual(s1['exit'], {'kind': 'hole', 'axis': [0.0, 0.0, -1.0], 'point': [48.0, 5.0, 0.0]})
        self.assertEqual(p1['arc'], 1.0); self.assertEqual(p1['at'], {'point': [36.0, 16.0, 12.0], 'axis': [0.0, 0.0, 1.0]})

    def test_floor_and_what_continues_behind_a_blind_end(self):
        self.assertAlmostEqual(self.holes['p1']['floor'], 4.96, delta=0.4); self.assertFalse(self.holes['p1']['floor_capped'])
        self.assertIsNone(self.holes['p1']['behind'])
        top, below = self.holes['top'], self.holes['top_below']
        self.assertAlmostEqual(top['floor'], 0.84, delta=0.06); self.assertAlmostEqual(below['floor'], 0.84, delta=0.06)
        self.assertEqual(top['behind'], {'hole': below['id'], 'cap': 0.84, 'd': 3.4}); self.assertEqual(below['behind'], {'hole': top['id'], 'cap': 0.84, 'd': 3.4})
        self.assertEqual(below['anchor'], {'kind': 'hole', 'axis': [0.0, 0.0, -1.0], 'point': [80.0, 4.0, 0.0]})

    def test_coaxial_bodies_and_the_insert_that_keeps_a_through_hole_in_the_band(self):
        self.assertEqual(self.holes['p1']['coaxial'], [{'name': 'HW (ref) / insert M3x4x5 (1)', 'kind': 'hardware', 'part': None}])
        self.assertEqual(self.holes['p2']['coaxial'], [{'name': 'Cover', 'kind': 'printed', 'part': 'demo/cover'}])
        self.assertEqual(self.holes['s1']['coaxial'], [{'name': 'HW (ref) / M3x10 screw (1)', 'kind': 'hardware', 'part': None}])
        self.assertEqual(self.holes['p4']['coaxial'], [])
        t1 = self.holes['t1']
        self.assertEqual((t1['segments'], t1['through']), (segs((4.4, 12.0)), True))
        self.assertEqual(t1['class'], {'table': 'inserts', 'kind': 'M3x4x5', 'role': 'insert_pocket', 'match': 'band'})
        self.assertEqual(G.classify_stack(t1['segments'], True, TABLES)['table'], 'screws')         # the segments alone say M4 clearance

    def test_openings(self):
        (slot,) = self.facts['openings']
        self.assertEqual((slot['id'], slot['shape'], slot['size'], slot['through'], slot['depth']), ('o1', 'slot', [12.0, 4.0], True, None))
        self.assertEqual(slot['anchor'], {'kind': 'face', 'axis': [0.0, 0.0, 1.0], 'point': [20.0, 29.0, 12.0]})   # outward, though the plane's own normal points in
        self.assertAlmostEqual(slot['wall'], 12.0, delta=0.3)
        self.assertEqual(self.facts['counts']['shallow_recesses'], 1)             # the 0.4 mm engraving is no opening
        self.assertEqual(self.facts['counts'].get('boss_feet', 0), 0)

    def test_the_pose_of_the_occurrence_changes_nothing(self):
        turned, _, env = self.whole(demo(place=placement(-20.0, 55.0, 3.0, turn=90.0)), 'fusion_census')
        self.assertEqual(env['sig'], self.env['sig']); self.assertEqual(turned, self.facts)

    def test_cursor_gives_the_same_facts_in_many_calls(self):
        steps, calls, _ = self.whole(self.app, 'fusion_census', args={'limit': 7})
        self.assertGreater(calls, 5); self.assertEqual(steps, self.facts)
        first = self.unit(self.app, 'fusion_census', args={'limit': 7})
        self.assertEqual((first['done'], first['cursor']['face'], first['facts']['holes']), (False, 7, []))
        stale = dict(first['cursor'], sig='0' * 64)
        with self.assertRaises(AssertionError) as e: self.unit(self.app, 'fusion_census', cursor=stale)
        self.assertIn('the body changed between two calls', str(e.exception))
        changed = self.unit(self.app, 'fusion_census', sig='0' * 64)             # the plan expects another signature: nothing is measured
        self.assertEqual((changed['facts'], changed['done'], changed['sig']), ({'stale': True}, False, self.env['sig']))
        self.assertEqual(self.whole(self.app, 'fusion_census', sig=self.env['sig'])[0], self.facts)

    def test_the_openings_of_a_face_cut_by_the_clock_give_the_same_facts(self):
        """A planar face with openings is walked with point tests; a call that runs out of time inside one face hands its
        answers on, and the counts of that face are counted once."""
        self.assertTrue(self.facts['openings'])
        for limit in (11, 37):                                                     # "tape_limit" holds for every point test of the unit
            steps, calls, _ = self.whole(self.app, 'fusion_census', args={'tape_limit': limit})
            self.assertEqual(steps, self.facts, limit); self.assertGreater(calls, 3)
        cut, cursor, facts = None, None, None
        for _ in range(400):                                                       # one step a call up to the openings, then the first
            args = {'tape_limit': 3} if (cursor or {}).get('stage') == 'openings' else {'limit': 1}     # call that stops inside a face
            env = self.unit(self.app, 'fusion_census', args=args, cursor=cursor)
            facts, cursor = gate_fusion_rpc.merge_facts(facts, env['facts']), env['cursor']
            if env['done'] or (cursor['stage'] == 'openings' and 'tape' in cursor): cut = env; break
        self.assertFalse(cut['done']); self.assertIn(cut['cursor']['tape']['n'], (1, 2, 3)); self.assertEqual(cut['facts']['openings'], [])
        while not env['done']:
            env = self.unit(self.app, 'fusion_census', args={'tape_limit': 3}, cursor=env['cursor'])
            facts = gate_fusion_rpc.merge_facts(facts, env['facts'])
        self.assertEqual(facts, self.facts)

    def test_groove_boss_ring_groove_and_broken_wall(self):
        plate = Solid((0.0, 0.0, 0.0), (40.0, 30.0, 6.0), holes=[
            {'o': (20.0, 30.0, 6.0), 'a': UP, 'segs': [(3.0, 6.0)]},              # a half round groove in the edge at y = 30
            {'o': (1.4, 15.0, 6.0), 'a': UP, 'segs': [(3.4, 6.0)]},               # a hole that breaks out of the edge at x = 0
            {'o': (20.0, 12.0, 6.0), 'a': UP, 'segs': [(8.3, 6.0)]},              # a plain hole nobody knows
        ], bosses=[{'o': (32.0, 8.0, 6.0), 'a': UP, 'r': 3.0, 'h': 5.0}])
        facts, _, _ = self.whole(application([Occ(Comp('c-bracket', 'Bracket', [Body('Body1', plate)]), 'Bracket:1')]), 'fusion_census')
        got = {tuple(h['at']['point'][:2]): h for h in facts['holes']}
        self.assertEqual(sorted(got), [(1.4, 15.0), (20.0, 12.0)])
        self.assertLess(got[(1.4, 15.0)]['arc'], 1.0); self.assertEqual(got[(20.0, 12.0)]['arc'], 1.0)
        self.assertIsNone(got[(20.0, 12.0)]['class']); self.assertEqual(got[(20.0, 12.0)]['anchor']['kind'], 'hole')
        self.assertEqual((facts['counts']['unknown'], facts['counts']['partial_arcs'], facts['counts']['outer']), (1, 1, 1))


class Inserts(Units):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = demo()
        cls.census, _, cls.cenv = cls.whole(cls.app, 'fusion_census')
        cls.features = {'holes': cls.census['holes'], 'openings': cls.census['openings']}
        cls.facts, cls.calls, cls.env = cls.whole(cls.app, 'fusion_inserts', features=cls.features, sig=cls.cenv['sig'])
        ids = {h['id']: k for k, h in by_place(cls.census['holes']).items()}
        cls.p = {ids[p['hole']]: p for p in cls.facts['pockets']}

    def only(self, *keys):
        want = by_place(self.census['holes'])
        return {'holes': [want[k] for k in keys], 'openings': []}

    def test_only_the_pockets_are_measured(self):
        self.assertEqual(sorted(self.p), ['p1', 'p2', 'p3', 'p4', 'p5', 'p6', 't1']); self.assertEqual(self.env['sig'], self.cenv['sig'])
        ray = self.p['p1']['ray']
        self.assertEqual((ray['n'], ray['step'], ray['max'], ray['thin_wall'], ray['thin_relief'], ray['start'], ray['refine']), (48, 0.1, 8.0, 4.0, 1.2, 0.03, 0.005))
        self.assertEqual((self.p['p1']['kind'], self.p['p1']['match'], self.p['p6']['match']), ('M3x4x5', 'exact', 'band'))
        self.assertEqual(self.p['p1']['anchor'], {'kind': 'insert_pocket', 'axis': [0.0, 0.0, 1.0], 'point': [36.0, 16.0, 12.0]})

    def near(self, got, want, delta=0.011):
        self.assertAlmostEqual(got, want, delta=delta)

    def test_room_on_every_side(self):
        p = self.p['p1']
        for level in ('lead_in', 'insert', 'relief'):
            w = p['walls'][level]
            self.assertEqual((w['min'], w['thin_arc_deg'], w['free_arc_deg'], w['blocked_by'], w['min_beyond']), (8.0, 0.0, 0.0, [], None), level)
        self.assertEqual(p['relief'], {'d': 7.5, 'depth': 0.54}); self.assertAlmostEqual(p['floor'], 4.96, delta=0.3)
        self.assertEqual((p['depth'], p['through']), (7.04, False)); self.assertEqual(p['walls']['relief']['from_d'], 7.5); self.assertEqual(p['walls']['insert']['from_d'], 4.4)

    def test_thin_wall_with_a_free_outline(self):
        p = self.p['p2']                                                          # the part ends 4.7 from the axis towards -x
        for level in ('lead_in', 'insert'):
            w = p['walls'][level]
            self.near(w['min'], 2.5)
            self.assertEqual((w['dir'], w['blocked_by'], w['min_beyond'], w['thin_under']), ([-1.0, 0.0, 0.0], [], 'free', 4.0), level)
            self.assertEqual((w['free_arc_deg'], w['thin_arc_deg']), (82.5, 82.5), level)       # every thin ray is free: the wall can be widened
        r = p['walls']['relief']                                                  # 0.95 beside the D7.5 relief
        self.near(r['min'], 0.95)
        self.assertEqual((r['dir'], r['min_beyond'], r['thin_under']), ([-1.0, 0.0, 0.0], 'free', 1.2)); self.assertGreater(r['free_arc_deg'], 0)
        self.assertEqual([x['d'] for x in p['relief_room']], [7.5, 6.5, 5.9]); self.near(p['relief_room'][0]['wall'], 0.95); self.near(p['relief_room'][2]['wall'], 1.75)

    def test_wall_of_exactly_the_target_and_no_relief(self):
        p = self.p['p3']
        for level in ('lead_in', 'insert'):
            w = p['walls'][level]
            self.near(w['min'], 4.0); self.assertGreaterEqual(w['min'], 4.0); self.assertEqual(w['thin_arc_deg'], 0.0)     # never read too small
        self.assertIsNone(p['relief']); self.assertIsNone(p['walls']['relief'])
        room = {x['d']: x['wall'] for x in p['relief_room']}                      # what a relief would leave: 6.2 - d / 2
        self.near(room[7.5], 2.45); self.near(room[6.5], 2.95); self.near(room[5.9], 3.25)

    def test_wall_of_3_8_is_under_the_target(self):
        w = self.p['p4']['walls']['lead_in']
        self.near(w['min'], 3.8)
        self.assertEqual((w['dir'], w['free_arc_deg'], w['thin_arc_deg'], w['blocked_by']), ([-1.0, 0.0, 0.0], 22.5, 22.5, []))

    def test_thin_wall_with_a_bought_part_beyond_it(self):
        p = self.p['p5']                                                          # 1.6 to the edge at y = 40, the motor starts at 40.3
        for level in ('lead_in', 'insert'):
            w = p['walls'][level]
            self.near(w['min'], 1.6)
            self.assertEqual((w['dir'], w['free_arc_deg'], w['min_beyond'], w['thin_arc_deg']), ([0.0, 1.0, 0.0], 0.0, 'ref motor', 97.5), level)
            (b,) = w['blocked_by']
            self.assertEqual((b['name'], b['kind'], b['part'], b['hidden']), ('ref motor', 'bought', None, False), level)
            self.near(b['gap'], 0.3, 0.03)                                         # the air between the wall and the motor
        r = p['walls']['relief']                                                  # 0.05 beside the relief: nearly broken out
        self.near(r['min'], 0.05); self.assertEqual((r['min_beyond'], [b['name'] for b in r['blocked_by']]), ('ref motor', ['ref motor']))

    def test_wrong_shape_pockets_are_measured_from_their_own_wall(self):
        p6, t1 = self.p['p6'], self.p['t1']                                       # straight D4.4: 6 - 2.2 = 3.8 to the edge at y = 0
        self.near(p6['walls']['lead_in']['min'], 3.8); self.assertEqual((p6['walls']['lead_in']['dir'], p6['relief']), ([0.0, -1.0, 0.0], None))
        self.assertAlmostEqual(p6['floor'], 6.0, delta=0.3)
        self.assertEqual((t1['through'], t1['floor']), (True, None))
        self.assertEqual(t1['walls']['insert']['blocked_by'], [])                 # the insert ref in the hole is not a blocker
        self.assertEqual([lv['depth'] for lv in t1['levels']], [0.27, 0.75, 2.4, 3.2, 4.0])

    def moved(self, key, to):
        """the demo with one pocket drilled somewhere else: (application, its census hole there, the signature)"""
        saved = AT[key]
        try:
            AT[key] = to
            app = demo()
            census, _, cenv = self.whole(app, 'fusion_census')
        finally:
            AT[key] = saved
        return app, next(h for h in census['holes'] if tuple(h['at']['point'][:2]) == to), cenv['sig']

    def test_a_slot_or_a_hole_of_the_part_itself_beyond_a_thin_wall_is_not_a_free_outline(self):
        app, hole, sig = self.moved('p4', (11.0, 29.0))                          # 0.8 mm of plastic, then the 12 mm slot, then the part again
        w = self.whole(app, 'fusion_inserts', features={'holes': [hole], 'openings': []}, sig=sig)[0]['pockets'][0]['walls']['lead_in']
        self.near(w['min'], 0.8)
        self.assertEqual((w['dir'], w['min_beyond'], w['free_arc_deg']), ([1.0, 0.0, 0.0], 'own cavity', 0.0))
        (b,) = w['blocked_by']
        self.assertEqual((b['name'], b['kind'], b['part'], b['gap']), ('own cavity', 'own_cavity', 'demo/bracket', 0.0))
        self.assertAlmostEqual(b['width'], 12.0, delta=1.5)                       # about the width of the slot
        app, hole, sig = self.moved('p4', (25.6, 6.0))                           # 1.2 mm of plastic between it and the old pocket p6
        w = self.whole(app, 'fusion_inserts', features={'holes': [hole], 'openings': []}, sig=sig)[0]['pockets'][0]['walls']['insert']
        self.near(w['min'], 1.2)
        self.assertEqual((w['dir'], w['min_beyond']), ([-1.0, 0.0, 0.0], 'own cavity'))
        self.assertGreater(w['free_arc_deg'], 0.0)                               # ... and 3.8 to the free edge at y = 0

    def test_mesh_ref_beyond_a_thin_wall(self):
        app = demo(mesh=True)
        facts, _, _ = self.whole(app, 'fusion_inserts', features=self.only('p2'), sig=self.cenv['sig'])
        w = facts['pockets'][0]['walls']['lead_in']
        self.near(w['min'], 2.5); self.assertEqual(w['min_beyond'], 'ref chain')
        (b,) = w['blocked_by']
        self.assertEqual((b['name'], b['kind'], b['part'], b['hidden']), ('ref chain', 'mesh', None, False)); self.near(b['gap'], 0.4, 0.03)
        self.assertLess(w['free_arc_deg'], w['thin_arc_deg'])                     # the rays that pass the 12 mm wide mesh stay free
        turned = demo(place=placement(-20.0, 55.0, 3.0, turn=90.0), mesh=True)   # the mesh occurrence is turned too
        again = self.whole(turned, 'fusion_inserts', features=self.only('p2'), sig=self.cenv['sig'])[0]
        self.assertEqual(again, facts)

    def test_the_pose_of_the_occurrence_changes_nothing(self):
        turned = demo(place=placement(-20.0, 55.0, 3.0, turn=90.0))
        facts, _, _ = self.whole(turned, 'fusion_inserts', features=self.only('p2', 'p5'), sig=self.cenv['sig'])
        self.assertEqual(facts['pockets'], [self.p['p2'], self.p['p5']])

    def test_a_neighbour_that_changes_between_two_calls_starts_the_pocket_again(self):
        """Pocket p5 has a thin wall and a bought part 0.3 mm beyond it. A call stops inside the pocket after it looked at
        that part. Then the part changes inside its box, or moves away: the answers in the cursor are of a neighbour that
        is no more, and the pocket is measured again from its start."""
        feats, sig = self.only('p5'), self.cenv['sig']
        motor = lambda app: app.design.rootComponent.allOccurrences[1].bRepBodies[0]
        one = self.whole(self.app, 'fusion_inserts', features=feats, sig=sig)[0]
        self.assertEqual(one['pockets'][0]['walls']['lead_in']['min_beyond'], 'ref motor')
        early = self.unit(self.app, 'fusion_inserts', features=feats, sig=sig, args={'tape_limit': 100})['cursor']
        self.assertEqual((early['env'], early['asked']), (None, {}))                   # only the part was asked so far: its signature holds it
        cur = self.unit(self.app, 'fusion_inserts', features=feats, sig=sig, args={'tape_limit': 10500})['cursor']
        self.assertEqual((len(cur['env']), sorted(cur['asked'])), (16, ['1']))          # the motor answered: its state goes with the cursor
        same = self.unit(demo(), 'fusion_inserts', features=feats, sig=sig, cursor=cur)
        self.assertEqual(same['facts'], one); self.assertFalse([n for n in same['notes'] if 'measured again' in n])
        changed = demo()
        motor(changed).volume += 0.5                                                 # a cut inside the same box
        env = self.unit(changed, 'fusion_inserts', features=feats, sig=sig, cursor=cur)
        self.assertEqual(env['facts'], one); self.assertEqual(len([n for n in env['notes'] if 'pocket' in n and 'is measured again from its start' in n]), 1)
        moved = demo()
        motor(moved).solid = Solid((40.0, 46.0, 0.0), (60.0, 56.0, 12.0))             # 5.7 mm further away: out of the room the wall would need
        free = self.whole(moved, 'fusion_inserts', features=feats, sig=sig)[0]
        self.assertNotEqual(free['pockets'][0]['walls']['lead_in']['min_beyond'], 'ref motor')
        env = self.unit(moved, 'fusion_inserts', features=feats, sig=sig, cursor=cur)
        self.assertEqual(env['facts'], free); self.assertTrue([n for n in env['notes'] if 'is measured again from its start' in n])
        step = cur                                                                   # a neighbour that is another one in every call: counted, then an error
        for n in (1, 2):
            app = demo(); motor(app).volume += 0.1 * n
            step = self.unit(app, 'fusion_inserts', features=feats, sig=sig, cursor=step, args={'tape_limit': 10500})['cursor']
            self.assertEqual(step['again'], n)
        app = demo(); motor(app).volume += 0.3
        with self.assertRaises(AssertionError) as e: self.unit(app, 'fusion_inserts', features=feats, sig=sig, cursor=step, args={'tape_limit': 10500})
        self.assertIn('met other bodies than the call before in three calls in a row', str(e.exception))

    def test_a_pocket_cut_by_the_clock_gives_the_walls_of_one_call(self):
        """One pocket is thousands of point tests. A call that runs out of time inside it hands the answers on in its
        cursor, and the next call goes on with the same pocket: the joined facts are the facts of one call."""
        feats = self.only('p1', 'p3', 'p6')
        one = self.whole(self.app, 'fusion_inserts', features=feats, sig=self.cenv['sig'])[0]
        for limit in (700, 4001):
            steps, calls, _ = self.whole(self.app, 'fusion_inserts', features=feats, sig=self.cenv['sig'], args={'tape_limit': limit})
            self.assertEqual(steps, one, limit); self.assertGreater(calls, 3)
        first = self.unit(self.app, 'fusion_inserts', features=feats, sig=self.cenv['sig'], args={'tape_limit': 500})
        self.assertEqual((first['done'], first['facts']['pockets'], first['cursor']['i'], first['cursor']['tape']['n']), (False, [], 0, 500))
        none = self.unit(self.app, 'fusion_inserts', features=feats, sig=self.cenv['sig'], deadline_s=0.001)       # no time: 48 points, so every call moves on
        self.assertEqual((none['done'], none['cursor']['i'], none['cursor']['tape']['n']), (False, 0, 48))
        bad = dict(first['cursor'], tape=dict(first['cursor']['tape'], sum=first['cursor']['tape']['sum'] + 1))    # answers to other questions: from the start
        env = self.unit(self.app, 'fusion_inserts', features=feats, sig=self.cenv['sig'], cursor=bad)
        self.assertEqual(env['facts']['pockets'], one['pockets']); self.assertTrue(any('measured again from its start' in n for n in env['notes']))
        cur = bad                                                                        # the same every call: the third call in a row ends the unit
        for n in (1, 2):
            env = self.unit(self.app, 'fusion_inserts', features=feats, sig=self.cenv['sig'], cursor=cur, args={'tape_limit': 300})
            self.assertEqual((env['done'], env['cursor']['i'], env['cursor']['again']), (False, 0, n))
            cur = dict(env['cursor'], tape=dict(env['cursor']['tape'], sum=env['cursor']['tape']['sum'] + 1))
        with self.assertRaises(AssertionError) as e: self.unit(self.app, 'fusion_inserts', features=feats, sig=self.cenv['sig'], cursor=cur, args={'tape_limit': 300})
        self.assertIn('did not ask the same questions in three calls in a row', str(e.exception))

    def test_cursor_stale_part_and_missing_census(self):
        steps, calls, _ = self.whole(self.app, 'fusion_inserts', features=self.only('p1', 'p3', 'p6'), sig=self.cenv['sig'], args={'limit': 1})
        self.assertEqual(calls, 3); self.assertEqual(steps['pockets'], [self.p['p1'], self.p['p3'], self.p['p6']])
        stale = self.unit(self.app, 'fusion_inserts', features=self.features, sig='0' * 64)
        self.assertEqual((stale['facts'], stale['done'], stale['sig']), ({'stale': True}, False, self.cenv['sig']))
        with self.assertRaises(AssertionError) as e: self.unit(self.app, 'fusion_inserts', sig=self.cenv['sig'])
        self.assertIn('fusion_census must run on this part first', str(e.exception))

    def test_rounded_anchor_alone_is_enough_to_aim(self):
        hole = dict(by_place(self.census['holes'])['p2'])
        del hole['at']                                                           # a census without the exact mouth: the anchor is on the 0.05 grid
        facts, _, _ = self.whole(self.app, 'fusion_inserts', features={'holes': [hole], 'openings': []}, sig=self.cenv['sig'])
        w = facts['pockets'][0]['walls']['lead_in']
        self.assertEqual((facts['pockets'][0]['ray']['start'], w['dir']), (0.08, [-1.0, 0.0, 0.0])); self.near(w['min'], 2.5)


if __name__ == '__main__':
    unittest.main()


# ---- the scan of a large body and of a linked document; the thin-face probe with the sides of each face -----------------------

class ScanMore(Units):
    def comb(self):
        """a plate with 60 drilled holes: 66 faces, more than one look at the clock (the clock is read every 50 faces)"""
        holes = [{'o': (4.0 + 3.0 * (i % 20), 4.0 + 6.0 * (i // 20), 5.0), 'a': UP, 'segs': [(2.0, 5.0)]} for i in range(60)]
        return Solid((0.0, 0.0, 0.0), (64.0, 20.0, 5.0), holes=holes)

    def rack(self):
        comps = [Comp('c-rack', 'Rack', [Body('Plate', Solid((0.0, 30.0, 0.0), (10.0, 40.0, 5.0))), Body('Comb', self.comb()),
                                         Body('old tool body', Solid((0.0, 0.0, 9.0), (2.0, 2.0, 11.0)), on=False)]),
                 Comp('c-cover', 'Cover', [Body('Body1', Solid((0.0, 0.0, 6.0), (10.0, 10.0, 8.0)))])]
        return application([Occ(comps[0], 'Rack:1'), Occ(comps[1], 'Cover:1')])

    def test_a_body_with_many_faces_is_signed_over_two_calls_and_nothing_is_listed_twice(self):
        one, calls, _ = self.whole(self.rack(), 'fusion_scan')
        self.assertEqual(calls, 1); self.assertEqual([(p['name'], p['faces']) for p in one['parts']], [('Rack / Plate', 6), ('Rack / Comb', 66), ('Cover', 6)])
        first = self.unit(self.rack(), 'fusion_scan', deadline_s=0.001)                  # no time at all: the root, then the next call
        self.assertEqual((first['done'], first['cursor'], first['facts']['parts']), (False, 1, []))
        second = self.unit(self.rack(), 'fusion_scan', deadline_s=0.001, cursor=1)       # the plate is listed, the comb is begun: 50 of its 66 faces
        cur = second['cursor']
        self.assertEqual((second['done'], cur['i'], cur['b'], cur['sign']['k'], cur['sign']['n']), (False, 1, 1, 50, 66))
        self.assertEqual([p['name'] for p in second['facts']['parts']], ['Rack / Plate'])
        self.assertIn("the signature of 'Comb' (66 faces) takes more than one call: 50 faces are in", second['notes'])
        third = self.unit(self.rack(), 'fusion_scan', deadline_s=0.001, cursor=cur)      # goes on inside the comb, then lists the body that is switched off
        self.assertEqual(([p['name'] for p in third['facts']['parts']], [r['name'] for r in third['facts']['refs']], third['cursor']), (['Rack / Comb'], ['Rack / old tool body'], 2))
        steps, calls, _ = self.whole(self.rack(), 'fusion_scan', deadline_s=0.001)
        self.assertEqual(steps, one); self.assertEqual(calls, 4)                         # the same facts, signature included, in four short calls

    def test_a_signature_in_progress_for_another_face_count_starts_again(self):
        one, _, _ = self.whole(self.rack(), 'fusion_scan')
        stale = {'i': 1, 'b': 1, 'sign': {'n': 12, 'k': 10, 'acc': 'ff'}}                # the body changed between two calls
        got = self.unit(self.rack(), 'fusion_scan', cursor=stale)['facts']
        self.assertEqual([p['sig'] for p in got['parts']], [p['sig'] for p in one['parts'] if p['name'] != 'Rack / Plate'])

    def test_a_part_of_a_linked_document_names_that_document(self):
        drive = Comp('c-housing', 'Drive housing', [Body('Body1', Solid((0.0, 0.0, 0.0), (30.0, 30.0, 10.0)))])
        drive.parentDesign = types.SimpleNamespace(parentDocument=types.SimpleNamespace(name='Turntable drive v4'))
        lid = Comp('c-lid', 'Drive lid', [Body('Body1', Solid((0.0, 0.0, 10.0), (30.0, 30.0, 12.0)))])      # Fusion gives no document for this one
        foot = Comp('c-foot', 'Lamp foot', [Body('Body1', Solid((0.0, 0.0, -5.0), (50.0, 50.0, 0.0)))])
        foot.parentDesign = types.SimpleNamespace(parentDocument=types.SimpleNamespace(name='Demo Assembly v7'))
        top = Occ(Comp('c-drive', 'Turntable drive'), 'Turntable drive:1'); top.isReferencedComponent = True
        inner = Occ(lid, 'Turntable drive:1+Drive lid:1'); inner.assemblyContext = top
        facts, _, _ = self.whole(application([top, Occ(drive, 'Turntable drive:1+Drive housing:1'), inner, Occ(foot, 'Lamp foot:1')]), 'fusion_scan')
        self.assertEqual({p['name']: (p['external'], p['linked']) for p in facts['parts']},
                         {'Drive housing': (True, 'Turntable drive'), 'Drive lid': (True, None), 'Lamp foot': (False, None)})


class TFace:
    """A planar face for the thin-face probe: an id, the outward normal and its corners in order (mm)."""
    def __init__(self, fid, normal, corners):
        self.tempId, self.normal, self.corners = fid, normal, [tuple(map(float, c)) for c in corners]
        c0, area = self.corners[0], (0.0, 0.0, 0.0)
        for a, b in zip(self.corners[1:], self.corners[2:]):
            area = G.v_add(area, G.v_cross(G.v_sub(a, c0), G.v_sub(b, c0)))
        self.area = G.v_len(area) / 2.0 / 100.0
        mid = tuple(sum(c[i] for c in self.corners) / len(self.corners) for i in range(3))
        self.centroid = self.pointOnFace = P(mid)
        self.geometry = types.SimpleNamespace(objectType='adsk::core::Plane', surfaceType=0, normal=V(*normal))
        self.evaluator = types.SimpleNamespace(getNormalAtPoint=lambda p: (True, V(*normal)))
        self.edges = Coll()
        self.loops = Coll([types.SimpleNamespace(edges=self.edges)])


class TBody:
    """A closed body made of TFaces. An edge is two corners that two faces share; `extra` adds edges with any face count."""
    name, isSolid, isLightBulbOn, volume, area = 'Body1', True, True, 1.14, 5.2

    def __init__(self, faces, extra=(), inside=None):
        self.faces, self.edges, self.asked = Coll(faces), Coll(), 0
        self.inside = inside                             # point (mm) -> is it in the material: the fake of BRepBody.pointContainment
        seen = {}
        for f in faces:
            for a, b in zip(f.corners, f.corners[1:] + f.corners[:1]):
                key = frozenset((a, b))
                if key not in seen:
                    both = Coll(g for g in faces if a in g.corners and b in g.corners)
                    seen[key] = types.SimpleNamespace(faces=both, evaluator=types.SimpleNamespace(
                        getParameterExtents=lambda: (True, 0.0, 1.0), getLengthAtParameter=lambda p0, p1, a=a, b=b: (True, G.v_dist(a, b) * (p1 - p0) / 10.0),
                        getPointAtParameter=lambda t, a=a, b=b: (True, P(G.v_add(a, G.v_scale(G.v_sub(b, a), t))))))
                    self.edges.append(seen[key])
                f.edges.append(seen[key])
        for n in extra:
            self.edges.append(types.SimpleNamespace(faces=Coll(range(n))))
        self.vertices, self.lumps, self.shells = Coll(range(14)), Coll([1]), Coll([1])
        pts = [c for f in faces for c in f.corners]
        self.boundingBox = types.SimpleNamespace(minPoint=P(tuple(min(p[i] for p in pts) for i in range(3))), maxPoint=P(tuple(max(p[i] for p in pts) for i in range(3))))

    def pointContainment(self, p):
        self.asked += 1
        return 0 if self.inside((p.x * 10.0, p.y * 10.0, p.z * 10.0)) else 2          # PointInside / PointOutside of the fake adsk


def in_polygon(u, v, poly):
    """Is the point (u, v) inside the polygon [(u, v), ...]? Crossing count."""
    hit = False
    for (u0, v0), (u1, v1) in zip(poly, poly[1:] + poly[:1]):
        if (v0 > v) != (v1 > v) and u < u0 + (v - v0) * (u1 - u0) / (v1 - v0):
            hit = not hit
    return hit


def bent_wall():
    """A wall 1 thick and 5 high whose plan is an L with legs of 10; mm. Its flat top is one narrow L-shaped face whose
    centroid (the mean of its corners here, the centre of area in Fusion) lies OUTSIDE the face, in the notch of the L.
    Every side of the top falls away: it is the top of a thin wall."""
    plan = [(0.0, 0.0), (10.0, 0.0), (10.0, 1.0), (1.0, 1.0), (1.0, 10.0), (0.0, 10.0)]
    names = ['outer_a', 'end_a', 'inner_a', 'inner_b', 'end_b', 'outer_b']
    normals = [(0, -1, 0), (1, 0, 0), (0, 1, 0), (1, 0, 0), (0, 1, 0), (-1, 0, 0)]
    faces = {}
    for i, name in enumerate(names):
        (x0, y0), (x1, y1) = plan[i], plan[(i + 1) % len(plan)]
        faces[name] = TFace(300 + i, normals[i], [(x0, y0, 0), (x1, y1, 0), (x1, y1, 5), (x0, y0, 5)])
    faces['top'] = TFace(310, (0, 0, 1), [(x, y, 5) for x, y in plan])
    faces['bottom'] = TFace(311, (0, 0, -1), [(x, y, 0) for x, y in plan])
    return faces, TBody(list(faces.values()), inside=lambda p: 0.0 < p[2] < 5.0 and in_polygon(p[0], p[1], plan))


def stepped_bar(extra=()):
    """A bar 20 x 10 x 6 with a ledge 1 wide and 1 high along one edge and a 0.4 chamfer along another; mm.
    Section in (y, z): (0,0) (10,0) (10,5.6) (9.6,6) (1,6) (1,5) (0,5)."""
    sec = [(0.0, 0.0), (10.0, 0.0), (10.0, 5.6), (9.6, 6.0), (1.0, 6.0), (1.0, 5.0), (0.0, 5.0)]
    names = ['bottom', 'back', 'chamfer', 'top', 'riser', 'ledge', 'front']
    normals = [(0, 0, -1), (0, 1, 0), (0, 0.7071, 0.7071), (0, 0, 1), (0, -1, 0), (0, 0, 1), (0, -1, 0)]
    faces = {}
    for i, name in enumerate(names):
        (y0, z0), (y1, z1) = sec[i], sec[(i + 1) % len(sec)]
        faces[name] = TFace(100 + i, normals[i], [(0, y0, z0), (20, y0, z0), (20, y1, z1), (0, y1, z1)])
    faces['end0'] = TFace(200, (-1, 0, 0), [(0, y, z) for y, z in sec])
    faces['end1'] = TFace(201, (1, 0, 0), [(20, y, z) for y, z in sec])
    return faces, TBody(list(faces.values()), extra, inside=lambda p: 0.0 < p[0] < 20.0 and in_polygon(p[1], p[2], sec))


class Thin(Units):
    def bar(self, extra=()):
        faces, body = stepped_bar(extra)
        comp = Comp('c-bracket', 'Bracket', [body])
        occ = types.SimpleNamespace(component=comp, fullPathName='Bracket:1', transform2=Mat(G.IDENTITY), isLightBulbOn=True)
        return faces, application([occ])

    def test_each_narrow_face_says_how_it_joins_its_neighbours(self):
        faces, app = self.bar()
        facts, calls, env = self.whole(app, 'fusion_thin')
        self.assertEqual((calls, facts['n_faces'], facts['non_manifold_edges'], facts['seam_edges'], 'sides_unread' in facts), (1, 9, 0, 0, False))
        rows = {f['id']: f for f in facts['faces']}
        ledge, riser, chamfer = (rows[faces[k].tempId] for k in ('ledge', 'riser', 'chamfer'))
        self.assertEqual(sorted(rows), [102, 104, 105])                                  # the chamfer, the riser, the ledge: nothing else is narrow
        self.assertEqual((ledge['width'], riser['width'], chamfer['width']), (0.952, 0.952, 0.55))
        front, end0, end1, top, back = (faces[k].tempId for k in ('front', 'end0', 'end1', 'top', 'back'))
        self.assertEqual(sorted(ledge['sides']), [[1.0, 90.0, end0], [1.0, 90.0, end1], [20.0, -90.0, 104], [20.0, 90.0, front]])   # rises to the riser, falls to the front
        self.assertEqual(sorted(riser['sides']), [[1.0, 90.0, end0], [1.0, 90.0, end1], [20.0, -90.0, 105], [20.0, 90.0, top]])
        self.assertEqual([s[0] for s in ledge['sides']], [20.0, 20.0, 1.0, 1.0])                                           # the longest edge first
        self.assertEqual(sorted(s[1:] for s in chamfer['sides'][:2]), [[45.0, back], [45.0, top]])                         # both long sides fall away by 45
        self.assertEqual(ledge['anchor'], {'kind': 'face', 'axis': [0.0, 0.0, 1.0], 'point': [10.0, 0.5, 5.0]})
        self.assertEqual(len(env['sig']), 64)

    def test_the_top_of_a_bent_wall_falls_away_on_its_inner_side_too(self):
        # The centroid of an L-shaped (or arc-shaped) face lies outside the face. A sign taken from the direction towards the centroid
        # read the inner sides of this top as "the neighbour rises": a ledge. The sign is asked from the body instead
        faces, body = bent_wall()
        comp = Comp('c-bracket', 'Bracket', [body])
        occ = types.SimpleNamespace(component=comp, fullPathName='Bracket:1', transform2=Mat(G.IDENTITY), isLightBulbOn=True)
        facts, _, _ = self.whole(application([occ]), 'fusion_thin')
        top = next(f for f in facts['faces'] if f['id'] == faces['top'].tempId)
        self.assertEqual(top['width'], 0.95)
        self.assertEqual(sorted((s[0], s[1]) for s in top['sides']), [(1.0, 90.0), (1.0, 90.0), (9.0, 90.0), (9.0, 90.0), (10.0, 90.0), (10.0, 90.0)])
        side = next(f for f in facts['faces'] if f['id'] == faces['end_a'].tempId)       # the narrow end face: the same, and its foot is no concave edge either
        self.assertEqual({s[1] for s in side['sides']}, {90.0})

    def test_the_judge_reads_these_facts_as_one_ledge_and_one_chamfer(self):
        import gate_judges, gate_params, gate_registry
        reg = gate_registry.load_default()
        rule = gate_registry.rule(reg, 'walls.thin_faces')
        facts, _, _ = self.whole(self.bar()[1], 'fusion_thin')
        ctx = {'part': 'demo/bracket', 'rule': rule['id'], 'tables': reg['tables'], 'target': None, 'census': None, 'prev': None, 'intent': None}
        got = gate_judges.run_judge(rule, facts, dict(gate_params.values(gate_params.resolve(reg)), **{'walls.min_face_width': 1.2, 'walls.strip_turn_max': 60}), ctx)
        self.assertEqual([(f['verdict'], f['measure']) for f in got], [('FAIL', 0.952), ('INFO', None)])
        self.assertIn('2 narrow faces that touch', got[0]['text']); self.assertIn('a ledge or a step', got[0]['text'])
        self.assertIn('1 faces under 1.2 mm wide in 1 place(s) are flats of a chamfer', got[1]['text'])

    def test_seams_and_edges_with_more_than_two_faces_are_counted_apart(self):
        facts, _, _ = self.whole(self.bar(extra=(1, 1, 4))[1], 'fusion_thin')
        self.assertEqual((facts['non_manifold_edges'], facts['seam_edges']), (1, 2))

    def test_a_face_whose_neighbours_cannot_be_read_is_listed_without_sides(self):
        faces, app = self.bar()
        del faces['riser'].tempId                                                        # Fusion gives no id for this face: its neighbour cannot name it
        facts, _, _ = self.whole(app, 'fusion_thin')
        self.assertEqual(facts['sides_unread'], 2)                                       # the riser itself, and the ledge that meets it
        self.assertEqual(sorted((str(f.get('id')), 'sides' in f) for f in facts['faces'] if f['width'] < 1.0), [('102', True), ('None', False), ('None', False)])

    def test_only_the_listed_sides_cost_a_point_test(self):
        """A toothed outline has thousands of edges and twelve rows are listed: the sign of a turn (one point test) is asked
        for those twelve only, and they are the rows the walk over every edge would have listed."""
        g = {}
        text = ''
        for name in ('_geom.py', 'fusion_thin.py'):
            with open(os.path.join(ROOT, 'tools', 'gate', 'probes', name), encoding='utf-8') as f: text += f.read() + '\n'
        exec(compile(text, 'thin', 'exec'), g)
        asked = []
        g['adsk'] = types.SimpleNamespace(fusion=types.SimpleNamespace(PointContainment=types.SimpleNamespace(PointInsidePointContainment=0)))
        g['gate_point'] = lambda p: p
        g['gate_mm'] = lambda p: p
        body = types.SimpleNamespace(pointContainment=lambda p: (asked.append(p), 0 if p[0] > 0 else 2)[1])
        this = types.SimpleNamespace(tempId=1)
        edges = []
        for k in range(300):
            nrm = (1.0, 0.0, 0.0) if k % 2 else (-1.0, 0.0, 0.0)
            other = types.SimpleNamespace(tempId=100 + k, evaluator=types.SimpleNamespace(getNormalAtPoint=lambda p, nrm=nrm: (True, V(*nrm))))
            seam = k % 50 == 7                                                           # a seam: the face meets itself there, no row
            e = types.SimpleNamespace(faces=Coll([this] if seam else [this, other]),
                                      evaluator=types.SimpleNamespace(getPointAtParameter=lambda t, k=k: (True, (float(k % 3 - 1), 0.0, float(k)))))
            edges.append((0.1 + ((k * 37) % 101) / 100.0 + (0.0 if k % 4 else 0.00001), e, 0.0, 1.0))
        rows = g['_thn_sides'](body, 1, (0.0, 0.0, 1.0), edges)
        self.assertEqual(len(rows), 12); self.assertEqual(len(asked), 12)                # it was 294: one for every edge with a neighbour
        self.assertEqual([r[0] for r in rows], sorted((r[0] for r in rows), reverse=True))
        every = g['_thn_sides'](body, 1, (0.0, 0.0, 1.0), edges, limit=10 ** 6)          # the walk over every edge, cut to twelve as before
        self.assertEqual(len(every), 294); self.assertEqual(rows, every[:12])
        self.assertEqual(set(r[1] for r in every), {90.0, -90.0})

    def test_the_cursor_gives_the_same_facts_and_the_edges_come_last(self):
        _, app = self.bar()
        one, _, _ = self.whole(app, 'fusion_thin')
        first = self.unit(app, 'fusion_thin', deadline_s=0.001)                          # all nine faces fit before the first look at the clock
        self.assertEqual((first['done'], first['cursor'], 'non_manifold_edges' in first['facts']), (False, 9, False))
        steps, calls, _ = self.whole(app, 'fusion_thin', deadline_s=0.001)
        self.assertEqual((calls, steps), (2, one))

    def test_the_edge_walk_of_a_large_body_has_its_own_clock_and_cursor(self):
        _, app = self.bar(extra=[1] * 700 + [3] * 600)                                   # 1300 more edges: 700 seams, 600 edges with three faces
        whole, calls, _ = self.whole(app, 'fusion_thin')
        self.assertEqual((calls, whole['seam_edges'], whole['non_manifold_edges']), (1, 700, 600))
        first = self.unit(app, 'fusion_thin', deadline_s=0.001)                          # the faces; the edges are left for the next call
        self.assertEqual((first['done'], first['cursor']), (False, 9))
        second = self.unit(app, 'fusion_thin', deadline_s=0.001, cursor=first['cursor'])  # no time: 500 edges, then the clock stops it
        self.assertEqual((second['done'], sorted(second['cursor']), second['cursor']['e'], 'seam_edges' in second['facts']), (False, ['bad', 'e', 'seams'], 500, False))
        third = self.unit(app, 'fusion_thin', deadline_s=0.001, cursor=second['cursor'])
        self.assertEqual((third['done'], third['cursor']['e']), (False, 1000))
        self.assertGreater(third['cursor']['seams'], second['cursor']['seams'])           # the counts travel in the cursor
        last = self.unit(app, 'fusion_thin', deadline_s=0.001, cursor=third['cursor'])
        self.assertEqual((last['done'], last['facts']['seam_edges'], last['facts']['non_manifold_edges']), (True, 700, 600))
        steps, calls, _ = self.whole(app, 'fusion_thin', deadline_s=0.001)                # and the host puts the same facts together over the calls
        self.assertEqual((calls, steps), (4, whole))

    def test_the_question_is_it_still_the_stamped_geometry_reads_every_face(self):
        _, app = self.bar()
        want = self.unit(app, 'fusion_thin')['sig']
        got = self.unit(app, 'fusion_thin', args={'sig_only': True})
        self.assertEqual((got['facts'], got['done'], got['sig']), ({'sig_only': True}, True, want))


class CountersunkSeat(unittest.TestCase):
    """A countersunk wood screw's hole fits no clearance row and its D4.2 lies in the band of an M3 insert: the 90 degree
    cone over it and the screw on its axis make it a screw seat, nothing less does."""
    SEGS = [{'d': 8.4, 'd2': 4.2, 'len': 2.1}, {'d': 4.2, 'len': 7.9}]
    SCREW = ['HW / wood screw 4x20 countersunk (1)']

    def klass(self, segs=None, coaxial=None, through=True):
        return G.classify_stack(segs or self.SEGS, through, SHIPPED, self.SCREW if coaxial is None else coaxial)

    def test_cone_and_screw_make_a_screw_seat(self):
        self.assertEqual(self.klass(), {'table': 'screws', 'kind': 'M4', 'role': 'countersink', 'match': 'band'})
        self.assertEqual(self.klass(through=False)['role'], 'countersink')
        bundle = ['HW (screws, inserts, washers) / wood screw 4x20 countersunk (1)']                # a bundle names inserts; the body is the screw
        self.assertEqual(self.klass(coaxial=bundle)['role'], 'countersink')
        clr = [{'d': 8.38, 'd2': 4.4, 'len': 2.2}, {'d': 4.4, 'len': 2.8}]                       # over an M4 clearance hole: that screw row
        self.assertEqual(self.klass(clr, coaxial=bundle), {'table': 'screws', 'kind': 'M4', 'role': 'countersink', 'match': 'exact'})
        self.assertEqual(self.klass(clr, coaxial=['HW / insert M3x4x5 (1)', 'HW / screw M3x8 (1)'])['role'], 'insert_pocket')   # an insert: stays

    def test_anything_less_stays_in_the_insert_band(self):
        band = {'table': 'inserts', 'kind': 'M3x4x5', 'role': 'insert_pocket', 'match': 'band'}
        self.assertEqual(self.klass(coaxial=[]), band)                                            # no screw on the axis
        self.assertEqual(self.klass(coaxial=self.SCREW + ['HW / insert M3x4x5 (1)']), band)       # an insert is there too
        self.assertNotEqual(self.klass([{'d': 5.2, 'd2': 4.2, 'len': 0.5}, {'d': 4.2, 'len': 7.9}])['role'], 'countersink')   # an edge chamfer is trimmed off
        self.assertEqual(self.klass([{'d': 8.4, 'd2': 4.2, 'len': 3.6}, {'d': 4.2, 'len': 7.9}]), band)   # a 60 degree cone
        self.assertEqual(self.klass([{'d': 8.4, 'd2': 5.0, 'len': 1.7}, {'d': 4.2, 'len': 7.9}]), band)   # cone not on the hole
        lead = [{'d': 6.0, 'd2': 4.2, 'len': 0.9}, {'d': 4.2, 'len': 7.9}]                          # a 90 degree lead-in, narrower than an M4 head
        self.assertNotEqual(self.klass(lead)['role'], 'countersink')
