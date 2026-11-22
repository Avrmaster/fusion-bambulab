"""0.3.2: every ordinary part has an honest answer to its standing questions, and a real defect still has none.

For each question that blocked every export of a real project: the blocked case now has an honest label or an answer
by facts, and the case the rule is for (a motor drawn as a box, a press fit with no parameter, a feature that is gone,
a cover right over a head) still has no passing answer. The shipped registry is held here; the demo project gives the
files. No Fusion."""
import copy, json, os, sys, unittest
for _k in ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID'): os.environ.pop(_k, None)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_fusion, gate_attest, gate_common, gate_features, gate_judges, gate_params, gate_plan, gate_registry

BRACKET = 'demo/bracket'
SHIPPED = gate_registry.load_default()


def labels(rule):
    return SHIPPED['rules'][rule]['attest']['labels']


STEP, ONLY, NONE = labels('hardware.bought_from_step')
SCREW = {'name': 'ref hardware / M3x12 DIN 912 (plate to base)', 'kind': 'hardware', 'path': 'ref hardware', 'gap': 0.0}
WASHER = {'name': 'ref hardware / washer M3 DIN 125 (1)', 'kind': 'hardware', 'path': 'ref hardware', 'gap': 0.0}
MOTOR = {'name': 'ref drive / 17HS4401S', 'kind': 'bought', 'path': 'ref drive', 'gap': 2.0}


class Project(unittest.TestCase):
    """The demo project with the shipped attest block of the rules under test."""
    RULES = ('hardware.bought_from_step',)

    def setUp(self):
        self.demo = fake_fusion.DemoProject(self, part_questions=('process.concept_geometry', 'hardware.bought_from_step'))
        self.root = self.demo.root
        self.reg = self.demo.registry()
        for rid in self.RULES:
            self.reg['rules'][rid] = copy.deepcopy(SHIPPED['rules'][rid])
        for name in gate_attest.FACT_PARAMS:
            self.reg['params'].setdefault(name, SHIPPED['params'][name])
        self.reg['tables'] = SHIPPED['tables']
        self.sig = self.demo.model({'Bracket': 'v1', 'Cover': 'v1'})[BRACKET]
        gate_plan.scan(self.root, wall_s=600)
        self.resolved = gate_params.resolve(self.reg, gate_params.load_overrides(self.root), None)
        gate_plan.run_units(self.root, self.reg, self.resolved, gate_plan.new_plan(self.root, self.reg, [BRACKET]), wall_s=600)
        gate_plan.judge_part(self.root, self.reg, self.resolved, BRACKET)

    def units(self, near=(), screws=(), bearings=(), holes=(), passing=()):
        units = {'fusion_near': {'margin': 10.0, 'bodies': list(near)}, 'fusion_census': {'holes': list(holes), 'bores': [], 'openings': [], 'partial': []},
                 'fusion_screws': {'screws': list(screws), 'washers': [], 'passing': list(passing), 'n_screws': len(screws) + len(passing), 'fastener_holes': []},
                 'fusion_bearings': {'bearings': list(bearings), 'seats_without_bearing': []}}
        for probe in units:
            gate_common.save_probe_facts(self.root, BRACKET, self.sig, {'probe': probe, 'facts': units[probe], 'done': True, 'error': None})
        gate_plan.judge_part(self.root, self.reg, self.resolved, BRACKET)
        return gate_plan.part_verdict(self.root, self.reg, BRACKET, stages=('design',))

    def q(self, rule):
        return next(x for x in gate_attest.questions_for_part(self.root, self.reg, BRACKET, self.sig, [], ('design',)) if x['rule'] == rule)

    def refused(self, rule, kind, value, q=None):
        return gate_attest.validate_answer(self.root, self.reg, self.resolved, q or self.q(rule), kind, value)


class BoughtFromStep(Project):
    def test_a_part_with_only_screws_and_washers_is_answered_by_the_facts(self):
        v = self.units(near=[SCREW, WASHER], screws=[{'ref': SCREW['name'], 'size': 'M3'}])
        got = {q['rule']: q['answer'] for q in v['by_facts']}
        self.assertEqual((got['hardware.bought_from_step']['as'], got['hardware.bought_from_step']['value']), ('label', ONLY))
        self.assertNotIn('hardware.bought_from_step', [q['rule'] for q in v['questions']])

    def test_a_motor_beside_it_keeps_the_question_and_refuses_the_standard_parts_label(self):
        self.units(near=[SCREW, MOTOR], screws=[{'ref': SCREW['name'], 'size': 'M3'}])
        self.assertIsNone(self.q('hardware.bought_from_step')['answer'])
        self.assertIn('match motors.ref_pattern (17HS4401S)', self.refused('hardware.bought_from_step', 'label', ONLY))
        self.assertIn('show what this answer denies', self.refused('hardware.bought_from_step', 'label', NONE))
        self.assertIn('show what this answer denies', self.refused('hardware.bought_from_step', 'na', 'this plate only touches screws and printed parts'))
        self.assertIsNone(self.refused('hardware.bought_from_step', 'label', STEP))         # the agent's word: the motor is from its STEP file

    def test_a_bearing_stays_asked_and_the_first_label_covers_it(self):
        self.units(near=[SCREW, {'name': 'ref 6808 (1)', 'kind': 'hardware', 'path': 'ref 6808', 'gap': 0.0}], screws=[{'ref': SCREW['name'], 'size': 'M3'}],
                   bearings=[{'designation': '6808'}])
        self.assertIsNone(self.q('hardware.bought_from_step')['answer'])
        self.assertIn('1 bearing(s) at or near this part', self.refused('hardware.bought_from_step', 'label', ONLY))
        self.assertIsNone(self.refused('hardware.bought_from_step', 'label', STEP))

    def test_no_parameter_is_evidence_and_the_owner_can_waive(self):
        a = SHIPPED['rules']['hardware.bought_from_step']['attest']
        self.assertEqual(a['evidence'], ['label', 'na', 'waiver'])
        self.assertIn('rule hardware.bought_from_step takes', self.refused('hardware.bought_from_step', 'param', 'walls.min_face_width'))


class LeadPath(Project):
    RULES = ('motors.lead_path',)

    def test_screws_in_a_component_named_after_the_motor_are_no_motor(self):
        screw = {'name': 'ref drive screws: cover + motor screws / M3x12 DIN 912 (cover to base)', 'kind': 'hardware', 'path': 'ref drive screws: cover + motor screws', 'gap': 0.0}
        get = {'fusion_near': {'margin': 10.0, 'bodies': [screw]}}.get
        params = dict(gate_attest._fact_params(self.root, self.reg))
        self.assertEqual(gate_attest.FACT_TESTS['no_motor_near'](get, params, SHIPPED['tables'])[0], True)
        self.assertIsNone(gate_attest.FACT_SHOWN['no_motor_near'](get, params, SHIPPED['tables']))
        motor = {'name': 'ref drive / Rotor', 'kind': 'bought', 'path': 'ref motor', 'gap': 0.0}
        get = {'fusion_near': {'margin': 10.0, 'bodies': [screw, motor]}}.get
        self.assertIn('Rotor', gate_attest.FACT_SHOWN['no_motor_near'](get, params, SHIPPED['tables']))    # a motor model under its component still counts

    def test_a_motor_beside_whose_leads_leave_away_has_a_label(self):
        self.assertIn('a motor is beside this part, and its plug and leads leave away from it: the insertion and lead paths do not cross this part', labels('motors.lead_path'))


class CountLoadJoint(Project):
    RULES = ('screws.count_load_joint',)
    ENOUGH = 'every load joint of this part has at least the minimum count, on the largest circle the parts allow'

    def setUp(self):
        self.demo_questions = True
        super().setUp()

    def ask(self):
        q = gate_attest._question(self.reg['rules']['screws.count_load_joint'], BRACKET, gate_features.part_anchor(), self.sig[:12], 'how many screws?')
        return q

    def test_a_part_held_only_by_screws_that_pass_it_can_say_enough(self):
        self.units(passing=[{'ref': 'ref mate / M3x16 (1)', 'size': 'M3'}] * 6)
        self.assertIsNone(gate_attest.label_contradicted(self.root, self.reg, self.ask(), self.ENOUGH))

    def test_a_part_with_no_screw_at_all_cannot(self):
        self.units()
        self.assertIn('the facts list none', gate_attest.label_contradicted(self.root, self.reg, self.ask(), self.ENOUGH))


HOLE_A = gate_features.anchor('hole', (0, 0, 1), (0, 0, 0))


def hole(segs, coaxial, through=True):
    return {'id': 'h1', 'anchor': HOLE_A, 'segments': [{'d': d, 'len': l} for d, l in segs], 'depth': sum(l for _, l in segs),
            'through': through, 'class': None, 'coaxial': coaxial}


class PocketContent(Project):
    RULES = ('fits.pocket_content',)
    NOBOUGHT, PASS, SEAT = labels('fits.pocket_content')

    def question(self, h, by):
        gate_common.save_probe_facts(self.root, BRACKET, self.sig, {'probe': 'fusion_census', 'facts': {'holes': [h], 'openings': [], 'partial': []}, 'done': True, 'error': None})
        return {'rule': 'fits.pocket_content', 'part': BRACKET, 'anchor': HOLE_A, 'by': by, 'finding': 'F-000000', 'finding_text': 'what goes in this pocket?'}

    def judged(self, h):
        rule = SHIPPED['rules']['fits.bought_part_pocket']
        params = gate_params.values(gate_params.resolve(SHIPPED, {}, None), rule['params'])
        return gate_judges.run_judge(rule, {'holes': [h], 'openings': []}, params, {'rule': rule['id'], 'part': BRACKET})

    def test_bodies_beyond_the_ends_of_a_through_hole_go_in_nowhere(self):
        insert = {'name': 'ref hw / insert M3x4x5 (1)', 'kind': 'hardware', 'span': [-6.0, -1.0]}
        self.assertEqual([f for f in self.judged(hole([(13.2, 1.0)], [insert])) if f.get('ask') == 'fits.pocket_content'], [])
        blind = [f for f in self.judged(hole([(13.2, 1.0)], [insert], through=False)) if f.get('ask') == 'fits.pocket_content']
        self.assertEqual(blind[0]['by'], []); self.assertIn('beyond its ends (not in it): ref hw / insert M3x4x5 (1)', blind[0]['text'])
        q = self.question(hole([(13.2, 1.0)], [insert], through=False), blind[0]['by'])
        self.assertIsNone(gate_attest.label_contradicted(self.root, self.reg, q, self.NOBOUGHT))

    def test_a_shaft_that_passes_with_room_has_a_label_and_a_pressed_pin_has_none(self):
        shaft = {'name': 'ref drive / shaft D8', 'kind': 'bought', 'span': [5.0, -10.0], 'r': [[4.0, 5.0, -10.0]]}
        q = self.question(hole([(28.0, 5.0)], [shaft]), [shaft['name']])
        self.assertIn('the finding names 1', gate_attest.label_contradicted(self.root, self.reg, q, self.NOBOUGHT))
        self.assertIsNone(gate_attest.label_contradicted(self.root, self.reg, q, self.PASS))
        pin = {'name': 'ref drive / dowel 8x20', 'kind': 'hardware', 'span': [0.0, -5.0], 'r': [[4.0, 0.0, -5.0]]}
        q = self.question(hole([(8.05, 5.0)], [pin]), [pin['name']])
        self.assertIn('fitted in it', gate_attest.label_contradicted(self.root, self.reg, q, self.PASS))
        self.assertIn('no screw', gate_attest.label_contradicted(self.root, self.reg, q, self.SEAT))

    def test_a_screw_head_seat_is_taken_only_with_its_screw_and_head_size(self):
        head = {'name': 'ref hw / M3x12 DIN 912 head (1)', 'kind': 'hardware', 'span': [0.0, -3.0], 'r': [[2.75, 0.0, -3.0]]}
        q = self.question(hole([(7.2, 12.7), (6.5, 3.8)], [head]), [head['name']])
        self.assertIsNone(gate_attest.label_contradicted(self.root, self.reg, q, self.SEAT))
        q = self.question(hole([(14.0, 5.0)], [head]), [head['name']])
        self.assertIn('no head of the screws table for M3 seats in it', gate_attest.label_contradicted(self.root, self.reg, q, self.SEAT))


class Intended(Project):
    RULES = ('regression.intended',)
    LABEL = 'changed on purpose in place: the new size or shape serves the same function, and its mate was changed with it or still fits'

    def q(self, text):
        return {'rule': 'regression.intended', 'part': BRACKET, 'anchor': HOLE_A, 'by': [], 'finding': 'F-000000', 'finding_text': text}

    def test_only_a_feature_that_changed_in_place_takes_it(self):
        ok = ('window 3 x 3 at (40, 0, 20) changed: it is 6 x 6 at (36, 0, 20) now, 0 mm off its old axis. Not gone, and the facts do not say whether the change was meant',
              'one station on one axis: insert pocket D4.4 x 1.5 + D3.6 x 4.5 at (0, 0, 0) went through the part and is blind now (D5.9 x 0.5)',
              'insert pocket D5.9 x 0.5 + D4.4 x 1.5 at (0, 0, 0) changed: a hole is still at that place (D13.2 x 1), it is no longer an insert pocket')
        for text in ok:
            self.assertIsNone(gate_attest.label_contradicted(self.root, self.reg, self.q(text), self.LABEL), text)
        for text in ('window 10 x 6 at (0, 30, 50) is gone',
                     'hole D3.4 x 5 at (0, 0, 0) moved 2 mm across its axis, now at (2, 0, 0): no ref stood on its axis to say what moved with it',
                     'one station on one axis: hole D3.4 x 5 at (0, 0, 0) is gone; hole D6.5 x 3 at (0, 0, 5) changed: D6.5 x 3 through -> D6.5 x 2 blind',
                     'hole D5 x 3 at (0, 0, 0) changed: D5 x 3 -> D6 x 3; with it: slot 3 x 5, gone'):
            self.assertIsNotNone(gate_attest.label_contradicted(self.root, self.reg, self.q(text), self.LABEL), text)

    def test_the_judge_says_changed_for_a_hole_that_is_still_there_under_another_class(self):
        prev = {'holes': [{'anchor': gate_features.anchor('insert_pocket', (0, 0, 1), (0, 0, 0)), 'segments': [{'d': 4.4, 'len': 1.5}, {'d': 3.6, 'len': 4.5}], 'through': False}]}
        now = {'holes': [{'anchor': gate_features.anchor('hole', (0, 0, 1), (0, 0, 0)), 'segments': [{'d': 13.2, 'len': 1.0}], 'through': False}], 'openings': []}
        rule = SHIPPED['rules']['regression.feature_lost']
        out = gate_judges.run_judge(rule, now, {}, {'rule': rule['id'], 'part': BRACKET, 'prev': dict(prev, openings=[])})
        self.assertIn('changed: a hole is still at that place', out[0]['text']); self.assertNotIn('is gone', out[0]['text'])


class CoverReach(unittest.TestCase):
    def judged(self, at, reach=27.0):
        rule = SHIPPED['rules']['access.screw_path']
        params = dict(gate_params.values(gate_params.resolve(SHIPPED, {}, None), rule['params']), **{'assembly.cover_reach': reach})
        hit = {'name': 'Drive cover', 'kind': 'body', 'at': at, 'share': 1.0, 'part': 'demo/ring'}
        path = {'hits': [hit], 'reach': 60.0, 'r': 3.25}
        o = {'of': 'screw_head', 'feature': 's1', 'anchor': gate_features.anchor('screw', (0, 0, 1), (0, 0, 0)), 'paths': {'tool': path, 'own': {'hits': [hit], 'reach': 60.0, 'r': 2.75}}}
        return gate_judges.run_judge(rule, {'openings': [o]}, params, {'rule': rule['id'], 'part': BRACKET})

    def test_a_body_far_over_the_head_is_said_and_not_asked(self):
        out = self.judged(40.0)
        self.assertEqual([(f['verdict'], f['ask']) for f in out], [('INFO', None)])
        self.assertIn('only farther out than assembly.cover_reach (27 mm): Drive cover 40 mm out', out[0]['text'])

    def test_a_cover_right_over_the_head_is_still_asked(self):
        out = self.judged(10.0)
        self.assertEqual([(f['verdict'], f['ask']) for f in out], [('FAIL', 'assembly.order')])

    def test_the_reach_defaults_to_the_free_height_of_the_key_and_the_label_of_an_earlier_cover(self):
        vals = gate_params.values(gate_params.resolve(SHIPPED, {}, None), ['assembly.cover_reach', 'access.free_height_over_head'])
        self.assertEqual(vals['assembly.cover_reach'], vals['access.free_height_over_head'])
        a = SHIPPED['rules']['assembly.order']['attest']
        self.assertIn('shot', a['evidence']); self.assertEqual(len(a['labels']), 2)


class Registry(unittest.TestCase):
    def test_held_claims_of_a_finding_are_checked(self):
        check = lambda held: (lambda errors: (gate_registry._check_attest(dict(base, held=held), 'rule demo.x', errors), errors)[1])([])
        base = {'question': 'What?', 'evidence': ['label'], 'per': 'finding', 'labels': ['a', 'b']}
        self.assertEqual(check({'a': {'text': {'has': 'changed', 'not': 'gone'}}, 'b': {'coaxial_room': 'fits.pass_room_min'}}), [])
        self.assertEqual(check({'a': {'screw_seat': True}}), [])
        for bad in ({'a': {'text': {'has': '('}}}, {'a': {'text': {}}}, {'a': {'screw_seat': 1}}, {'a': {'coaxial_room': 'Bad Name'}}, {'a': {'by': 'none', 'screw_seat': True}}):
            self.assertTrue(check(bad), bad)
        base['per'] = 'part'
        self.assertTrue(check({'a': {'screw_seat': True}}))

    def test_the_generated_source_parameter_is_narrowed(self):
        self.assertEqual(SHIPPED['rules']['generated.source']['attest']['param'], {'class': 'fit'})


if __name__ == '__main__':
    unittest.main()
