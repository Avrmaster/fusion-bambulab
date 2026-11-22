"""The two judges of the walls area after the first run on a real assembly: walls.thin_faces and clearance.min_gap_moving.

What these tests hold: a narrow face is a finding by how it joins its neighbours and not by its width alone; faces that
touch are one row; bridge steps are known only where the census shows a counterbore over a narrower hole; a gap that
was not measured is never a failure. The fixture pairs (tests/fixtures/facts/*__walls_*.json) hold the cases, these
tests hold the borders between them. Every name and number here is invented."""
import copy, json, os, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
import gate_judges, gate_params, gate_registry

FIXTURES = os.path.join(ROOT, 'tests', 'fixtures', 'facts')


def setUpModule():
    global REG, DEFAULTS
    REG = gate_registry.load_default()
    DEFAULTS = gate_params.values(gate_params.resolve(REG))


def fixture(name):
    with open(os.path.join(FIXTURES, name + '.json'), encoding='utf-8') as f:
        return json.load(f)


def judged(rule_id, facts, census=None, **params):
    rule = gate_registry.rule(REG, rule_id)
    ctx = {'part': 'demo/lamp_foot', 'rule': rule_id, 'tables': REG['tables'], 'target': None, 'census': census, 'prev': None, 'intent': None}
    pin = {'walls.min_face_width': 1.2, 'walls.strip_turn_max': 60, 'overhang.bridge_step': 0.3, 'clearance.min_gap_moving': 2.0}
    return gate_judges.run_judge(rule, facts, dict(DEFAULTS, **dict(pin, **params)), ctx)


def face(i, width, sides, point=(0.0, 0.0, 0.0), axis=(0, 0, 1)):
    return {'anchor': {'kind': 'face', 'axis': list(axis), 'point': list(point)}, 'width': width, 'area': 1.0, 'shape': 'strip', 'id': i, 'sides': sides}


def thin(*faces, **more):
    return dict({'faces': list(faces), 'width_cap': 2.0, 'lumps': 1, 'shells': 1}, **more)


def verdicts(found):
    return [f['verdict'] for f in found]


class Strips(unittest.TestCase):
    def one(self, *turns, **params):
        return verdicts(judged('walls.thin_faces', thin(face(1, 0.4, [[10.0, t, 50 + i] for i, t in enumerate(turns)])), **params))

    def test_the_angle_is_the_limit_and_equal_passes(self):
        self.assertEqual(self.one(60.0, 60.0), ['INFO']); self.assertEqual(self.one(60.1, 45.0), ['FAIL'])
        self.assertEqual(self.one(-45.0, -45.0), ['INFO'])                               # a bevel in an inner corner: both sides rise
        self.assertEqual(self.one(90.0, 90.0), ['FAIL']); self.assertEqual(self.one(90.0, -90.0), ['FAIL'])

    def test_sides_that_turn_opposite_ways_pass_only_up_to_half_the_angle(self):
        self.assertEqual(self.one(30.0, -30.0), ['INFO'])                                # one flat of a curve that changes its bend
        self.assertEqual(self.one(31.0, -31.0), ['FAIL']); self.assertEqual(self.one(46.0, -12.0), ['INFO'])

    def test_the_angle_is_a_parameter_of_the_project(self):
        self.assertEqual(self.one(70.0, 70.0), ['FAIL']); self.assertEqual(self.one(70.0, 70.0, **{'walls.strip_turn_max': 75}), ['INFO'])
        self.assertEqual(self.one(45.0, 45.0, **{'walls.strip_turn_max': 40}), ['FAIL'])

    def test_only_the_long_sides_count_and_an_unread_long_side_is_not_a_strip(self):
        self.assertEqual(verdicts(judged('walls.thin_faces', thin(face(1, 0.4, [[10.0, 45.0, 2], [10.0, 45.0, 3], [0.4, 90.0, 4], [0.4, -90.0, 5]])))), ['INFO'])
        self.assertEqual(verdicts(judged('walls.thin_faces', thin(face(1, 0.4, [[10.0, 45.0, 2], [10.0, None, 3]])))), ['FAIL'])
        self.assertEqual(verdicts(judged('walls.thin_faces', thin(face(1, 0.4, [[10.0, 45.0, 2], [6.0, 90.0, 3]])))), ['FAIL'])     # 6 of 10 is a long side
        self.assertEqual(verdicts(judged('walls.thin_faces', thin(face(1, 0.4, [])))), ['FAIL'])
        self.assertEqual(verdicts(judged('walls.thin_faces', thin(face(1, 0.4, 'not a list')))), ['FAIL'])
        self.assertEqual(verdicts(judged('walls.thin_faces', thin(face(1, 0.4, [[10.0, 45.0]])))), ['FAIL'])

    def test_a_face_at_the_width_limit_is_no_finding_whatever_its_sides(self):
        self.assertEqual(judged('walls.thin_faces', thin(face(1, 1.2, [[10.0, 90.0, 2], [10.0, -90.0, 3]]))), [])

    def test_faces_whose_sides_the_unit_could_not_read_are_judged_by_width_one_row_per_face(self):
        fx = fixture('walls_thin_faces__fail_ledge_1_0_and_crescents_0_2')
        got = judged('walls.thin_faces', fx['facts'])
        self.assertEqual([(f['verdict'], f['measure']) for f in got], [('FAIL', 1.0), ('FAIL', 0.2), ('FAIL', 0.2)])

    def test_facts_of_a_unit_that_read_no_sides_at_all_are_one_not_run_row(self):
        fx = fixture('walls_thin_faces__integrate_not_run_faces_from_a_unit_that_read_no_sides')
        got = judged('walls.thin_faces', fx['facts'])
        self.assertEqual(verdicts(got), ['NOT_RUN'])
        self.assertIn('3 faces under 1.2 mm wide (the narrowest 0.2 mm at (18, 4, 12))', got[0]['text']); self.assertIn('Run the part again', got[0]['text'])
        for key, value in (('seam_edges', 0), ('sides_unread', 3)):                                        # either key of the present unit: the faces are judged
            self.assertEqual(verdicts(judged('walls.thin_faces', dict(fx['facts'], **{key: value}))), ['FAIL'] * 3, key)
        wide = dict(fx['facts'], faces=[dict(f, width=1.5) for f in fx['facts']['faces']])
        self.assertEqual(judged('walls.thin_faces', wide), [])                                              # nothing under the limit: nothing to decide


class Places(unittest.TestCase):
    def test_faces_that_touch_through_any_chain_are_one_row(self):
        a = face(1, 0.5, [[10.0, 90.0, 2], [10.0, -90.0, 90]])
        b = face(2, 0.3, [[10.0, 90.0, 91], [10.0, -90.0, 92]])                          # b does not name a: a names b, that is enough
        c = face(3, 0.4, [[10.0, 90.0, 93], [10.0, -90.0, 2]])
        far = face(4, 0.6, [[10.0, 90.0, 94], [10.0, -90.0, 95]])
        got = judged('walls.thin_faces', thin(a, b, c, far))
        self.assertEqual([(f['verdict'], f['measure']) for f in got], [('FAIL', 0.3), ('FAIL', 0.6)])
        self.assertIn('3 narrow faces that touch, 0.3 to 0.5 mm wide', got[0]['text'])

    def test_a_wide_face_between_two_narrow_ones_does_not_join_them(self):
        a = face(1, 0.5, [[10.0, 90.0, 2], [10.0, -90.0, 90]])
        wide = face(2, 1.5, [[10.0, 90.0, 1], [10.0, -90.0, 3]])
        c = face(3, 0.4, [[10.0, 90.0, 2], [10.0, -90.0, 91]])
        self.assertEqual([f['measure'] for f in judged('walls.thin_faces', thin(a, wide, c))], [0.5, 0.4])

    def test_strips_in_a_place_with_a_ledge_are_counted_in_its_row_not_as_strips(self):
        ledge = face(1, 0.5, [[10.0, 90.0, 2], [10.0, -90.0, 90]])
        strip = face(2, 0.4, [[10.0, 45.0, 1], [10.0, 45.0, 91]])
        got = judged('walls.thin_faces', thin(ledge, strip))
        self.assertEqual(verdicts(got), ['FAIL']); self.assertIn('1 more faces of the place are flats of a chamfer', got[0]['text'])

    def test_the_finding_of_a_place_changes_when_a_face_of_it_changes(self):
        a = face(1, 0.5, [[10.0, 90.0, 2], [10.0, -90.0, 90]])
        b = face(2, 0.3, [[10.0, 90.0, 1], [10.0, -90.0, 92]])
        first = judged('walls.thin_faces', thin(a, b))[0]
        same = judged('walls.thin_faces', thin(copy.deepcopy(a), copy.deepcopy(b)))[0]
        wider = judged('walls.thin_faces', thin(dict(a, width=0.7), b))[0]
        self.assertEqual(first['context'], same['context']); self.assertNotEqual(first['context'], wider['context'])
        self.assertEqual(first['anchor'], wider['anchor'])                               # the place stays, what was measured there changed


class BridgeSteps(unittest.TestCase):
    def setUp(self):
        fx = fixture('walls_thin_faces__walls_info_bridge_steps_of_a_counterbore')
        self.facts, self.census = fx['facts'], fx['ctx']['census']

    def test_the_counterbore_and_its_hole_may_be_one_hole_of_the_census(self):
        one = {'holes': [dict(self.census['holes'][0], segments=[{'d': 6.5, 'len': 3.3}, {'d': 3.4, 'len': 8.7}], depth=12.0)], 'openings': []}
        self.assertEqual(verdicts(judged('walls.thin_faces', self.facts, one)), ['INFO'])

    def test_no_step_down_in_diameter_no_bridge_steps(self):
        same = copy.deepcopy(self.census); same['holes'][0]['segments'] = [{'d': 3.4, 'len': 3.0}]
        self.assertEqual(verdicts(judged('walls.thin_faces', self.facts, same)), ['FAIL'])
        far = copy.deepcopy(self.census); far['holes'][1]['at']['point'][2] = far['holes'][1]['anchor']['point'][2] = 7.0     # the hole starts 2 mm under the counterbore
        self.assertEqual(verdicts(judged('walls.thin_faces', self.facts, far)), ['FAIL'])
        aside = copy.deepcopy(self.census); aside['holes'][1]['at']['point'][0] = aside['holes'][1]['anchor']['point'][0] = 20.4  # not on the axis
        self.assertEqual(verdicts(judged('walls.thin_faces', self.facts, aside)), ['FAIL'])

    def test_one_face_that_does_not_belong_makes_the_place_a_finding(self):
        for change in ({'axis': [0, 0, -1]},                                             # it looks away from the mouth
                       {'point': [26.0, 10.0, 9.3]},                                     # outside the counterbore
                       {'point': [22.34, 10.0, 11.0]}):                                  # high up in the counterbore, not at its floor
            facts = copy.deepcopy(self.facts); facts['faces'][0]['anchor'].update(change)
            out = judged('walls.thin_faces', facts, self.census)
            self.assertIn(verdicts(out), (['FAIL'], ['FAIL', 'INFO']), change)               # the steps beside it may be counted as steps (_thin_bridge_split)
            fail = [f for f in out if f['verdict'] == 'FAIL'][0]
            self.assertEqual(fail['measure'], facts['faces'][0]['width'], change)           # the face that does not belong is the row, never dropped
        facts = copy.deepcopy(self.facts); facts['faces'][2]['anchor']['point'] = [21.4, 10.0, 9.15]     # a low wall that is not tangent to the hole
        out = judged('walls.thin_faces', facts, self.census)
        self.assertIn(verdicts(out), (['FAIL'], ['FAIL', 'INFO']))
        self.assertEqual([f['measure'] for f in out if f['verdict'] == 'FAIL'], [facts['faces'][2]['width']])

    def test_a_crescent_on_the_floor_of_a_relief_is_not_excused_by_the_step_under_it(self):
        # the crescent an old hole left beside a pocket: it lies where a step down in diameter is, and it has no low wall tangent to the hole
        pocket = {'holes': [{'id': 'h1', 'anchor': {'kind': 'insert_pocket', 'axis': [0, 0, 1], 'point': [20.0, 10.0, 12.0]},
                             'segments': [{'d': 7.5, 'len': 0.54}, {'d': 4.4, 'len': 1.5}, {'d': 3.6, 'len': 5.0}], 'depth': 7.04, 'through': False}], 'openings': []}
        crescents = [face(1, 0.2, [[3.1, -90.0, 90], [2.9, 90.0, 91]], point=(22.4, 10.0, 11.46)), face(2, 0.25, [[3.0, -90.0, 1], [2.8, 90.0, 92]], point=(22.5, 10.3, 11.46))]
        got = judged('walls.thin_faces', thin(*crescents), pocket)
        self.assertEqual([(f['verdict'], f['measure']) for f in got], [('FAIL', 0.2)])

    def test_a_single_face_is_never_bridge_steps(self):
        facts = dict(self.facts, faces=self.facts['faces'][:1])
        self.assertEqual(verdicts(judged('walls.thin_faces', facts, self.census)), ['FAIL'])

    def test_the_step_height_is_the_parameter(self):
        # with a step of 0.1 the zone ends 0.2 above the floor of the counterbore: the upper ledges at 0.3 are outside
        self.assertEqual(verdicts(judged('walls.thin_faces', self.facts, self.census, **{'overhang.bridge_step': 0.1})), ['FAIL'])


class OneSolid(unittest.TestCase):
    def test_counts_that_are_not_numbers_or_not_there_say_nothing(self):
        self.assertEqual(judged('walls.thin_faces', {'faces': [], 'width_cap': 2.0}), [])
        self.assertEqual(judged('walls.thin_faces', thin(non_manifold_edges=None, seam_edges=0)), [])
        self.assertEqual(verdicts(judged('walls.thin_faces', thin(non_manifold_edges=1, seam_edges=0))), ['FAIL'])
        self.assertEqual(verdicts(judged('walls.thin_faces', thin(non_manifold_edges=1))), ['NOT_RUN'])
        self.assertEqual(verdicts(judged('walls.thin_faces', thin(lumps=3))), ['FAIL'])


class Gap(unittest.TestCase):
    def facts(self, **row):
        base = {'name': 'Turntable', 'kind': 'printed', 'part': 'demo/turntable', 'group': 'turntable', 'gap': 0.4, 'point': [1.0, 2.0, 3.0], 'dir': [0, 0, 1], 'approx': False}
        return {'own_group': 'base', 'margin': 5.0, 'groups': 2, 'gaps': [dict(base, **row)]}

    def test_a_box_distance_is_a_pass_at_the_limit_and_decides_nothing_under_it(self):
        self.assertEqual(judged('clearance.min_gap_moving', self.facts(approx=True, gap=2.0)), [])
        self.assertEqual(verdicts(judged('clearance.min_gap_moving', self.facts(approx=True, gap=1.99))), ['NOT_RUN'])
        self.assertEqual(verdicts(judged('clearance.min_gap_moving', self.facts(approx=True, gap=0.0))), ['NOT_RUN'])
        self.assertEqual(verdicts(judged('clearance.min_gap_moving', self.facts(approx=False, gap=1.99))), ['FAIL'])

    def test_the_not_run_row_names_the_neighbour_and_keeps_its_id_while_the_box_moves(self):
        a, b = (judged('clearance.min_gap_moving', self.facts(approx=True, gap=g, kind='mesh'))[0] for g in (0.4, 0.9))
        self.assertEqual(a['by'], ['Turntable']); self.assertIn('known by its bounding box only', a['text']); self.assertIsNone(a['measure'])
        self.assertEqual(a['context'], b['context'])
        self.assertIn('the measurement failed', judged('clearance.min_gap_moving', self.facts(approx=True, error='RuntimeError: x'))[0]['text'])

    def test_touching_says_what_to_look_at_and_a_small_gap_does_not(self):
        self.assertIn('ONE motion group', judged('clearance.min_gap_moving', self.facts(gap=0.0))[0]['text'])
        self.assertNotIn('ONE motion group', judged('clearance.min_gap_moving', self.facts(gap=0.05))[0]['text'])

    def test_a_part_in_no_group_is_not_run_and_says_to_register_its_group(self):
        # an unjudged gap to a turning part never reads as INFO (0.3.1 round 2): the check did not run
        none = {'own_group': None, 'margin': 5.0, 'gaps': []}
        self.assertEqual(verdicts(judged('clearance.min_gap_moving', dict(none, groups=3))), ['NOT_RUN'])
        self.assertEqual(verdicts(judged('clearance.min_gap_moving', dict(none, groups=0))), ['NOT_RUN'])
        self.assertNotEqual(judged('clearance.min_gap_moving', dict(none, groups=3))[0]['text'], judged('clearance.min_gap_moving', dict(none, groups=0))[0]['text'])
        for facts in (dict(none, groups=3), dict(none, groups=0), none):
            self.assertIn('gate/layout.json', judged('clearance.min_gap_moving', facts)[0]['text'])
        self.assertEqual(verdicts(judged('clearance.min_gap_moving', none)), ['NOT_RUN'])     # facts written before the probe counted the groups


if __name__ == '__main__':
    unittest.main()
