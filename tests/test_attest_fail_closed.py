"""0.3.2, second pass: the answers 0.3.2 gave ordinary parts let real defects pass, closed again.

Each case below is a defect a fact test or a held label let through (a connector "4 pin" taken for a pin, a magnet in
a component of screws, a motor's shaft taken for a pin, a named fit "passing with room" on facts without radii), and
next to it the ordinary part that must keep its honest answer (screws, washers, nuts, inserts and dowels drawn to
their tables). Standard parts are recognised by their designation (a thread of the tables, a DIN or ISO number, a
size), not by a loose word. No Fusion."""
import os, sys, unittest
for _k in ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID'): os.environ.pop(_k, None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_attest_honest as h
from test_attest_honest import BRACKET, SHIPPED, SCREW, WASHER, STEP, ONLY, hole
import gate_attest

R = 'hardware.bought_from_step'
PARAMS = {n: SHIPPED['params'][n]['default'] for n in gate_attest.FACT_PARAMS}
TABLES = SHIPPED['tables']
SCREW_ROW = [{'ref': SCREW['name'], 'size': 'M3'}]


def body(name, kind='hardware', path=None, gap=0.0):
    return {'name': name, 'kind': kind, 'path': path or name.rsplit(' / ', 1)[0], 'gap': gap}


def facts(near=(), holes=()):
    return {'fusion_near': {'margin': 10.0, 'bodies': list(near)}, 'fusion_census': {'holes': list(holes), 'bores': []}}.get


class StandardPartNames(unittest.TestCase):
    """A standard part by its own name needs its designation; a loose word, or a word of its component, is none."""

    def kind(self, name):
        return gate_attest._standard_part(name, PARAMS, TABLES)

    def test_designated_standard_parts(self):
        for name, kind in (('ref hardware / M3x12 DIN 912 (plate to base)', 'screw'), ('M3x10 ISO 7380 head (cover) #1', 'screw'),
                           ('M2x6 ISO 7049 shank (board) #2', 'screw'), ('M3x10 (motor frame to hub)', 'screw'),
                           ('ref hardware / washer M3 DIN 125 (1)', 'washer'), ('washer M3x12x1.5 (outer ring clamp)', 'washer'),
                           ('nut M3 DIN 934', 'nut'), ('ref hardware / insert M3x4x5 (2)', 'insert'),
                           ('wood screw 4x30 countersunk (post to plywood)', 'wood screw'), ('ref axle / dowel 8x60', 'dowel'),
                           ('dowel D3 m6 x 12 (press in the base)', 'dowel'), ('pin DIN 6325 5m6 x 50 (bought length)', 'dowel'),
                           ('ISO 8734 4x20', 'dowel')):
            with self.subTest(name=name):
                self.assertEqual(self.kind(name), kind)

    def test_loose_words_and_other_bought_parts_are_none(self):
        for name in ('JST XH 4 pin', 'ref encoder board / Pin header 1x4', 'ref screws and magnets / Rod magnet D4x10', 'magnet D6x3',
                     'NEMA17 stepper / Shaft', 'ref drive / shaft D5', 'Ring pin (3)', 'Screw 1', 'pin 4x20', 'rod D6x100', 'insert',
                     'washer (clamp)', 'lead screw T8x8 M8', 'standoff M3x10', 'GT2 belt 6 mm', 'encoder board 23x23', 'micro switch',
                     '608 (1)', 'bearing 686 6x13x5', 'sleeve 5/6 x 5 (printed)', 'dowel 7x30', 'connector M3x4 terminal'):
            with self.subTest(name=name):
                self.assertIsNone(self.kind(name))


class BoughtFromStepClosed(h.Project):
    """hardware.bought_from_step: the standard-parts answer only for parts that are standard by their designation."""

    def by_facts(self, near, **kw):
        v = self.units(near=[SCREW] + list(near), screws=SCREW_ROW, **kw)
        return {q['rule']: q['answer'] for q in v['by_facts']}.get(R), R in [q['rule'] for q in v['questions']]

    def test_a_connector_a_pin_header_and_a_magnet_named_like_pins_or_in_a_screw_component_stay_asked(self):
        for near in (body('ref electronics / JST XH 4 pin'), body('ref encoder board / Pin header 1x4'),
                     body('ref screws and magnets / Rod magnet D4x10')):
            with self.subTest(body=near['name']):
                answer, asked = self.by_facts([near])
                self.assertIsNone(answer); self.assertTrue(asked)
                self.assertIn('no standard part by their name', self.refused(R, 'label', ONLY))
                self.assertIsNone(self.refused(R, 'label', STEP))                    # the agent's word: from its STEP file

    def test_the_standard_parts_label_is_refused_with_a_bought_body_touching_the_part(self):
        for near in (body('ref sensor / encoder board 23x23', 'bought'), body('ref sensor / magnet D6x3', 'bought'),
                     body('ref drive / GT2 belt 6 mm', 'bought'), body('ref switch / micro switch body', 'bought')):
            with self.subTest(body=near['name']):
                self.by_facts([near])
                self.assertIn('whose box touches this part that are no standard part by their name', self.refused(R, 'label', ONLY))

    def test_a_bought_body_farther_off_keeps_the_question_but_is_no_proof_against_the_label(self):
        answer, asked = self.by_facts([body('ref sensor / encoder board 23x23', 'bought', gap=6.0)])
        self.assertIsNone(answer); self.assertTrue(asked)
        self.assertIsNone(self.refused(R, 'label', ONLY))

    def test_an_ordinary_part_of_table_parts_keeps_its_answer_by_facts(self):
        near = [WASHER, body('ref hardware / nut M3 DIN 934'), body('ref hardware / insert M3x4x5 (2)'),
                body('ref pins / pin DIN 6325 5m6 x 50'), body('ref ties / wood screw 4x30 countersunk (1)')]
        answer, asked = self.by_facts(near)
        self.assertEqual((answer['as'], answer['value']), ('label', ONLY)); self.assertFalse(asked)


class LeadPathClosed(unittest.TestCase):
    """motors.lead_path (no_motor_near): a motor's own bodies and connectors are never taken for pins or fasteners."""

    def judge(self, near, holes=()):
        get = facts(near, holes)
        return gate_attest.FACT_TESTS['no_motor_near'](get, PARAMS, TABLES), gate_attest.FACT_SHOWN['no_motor_near'](get, PARAMS, TABLES)

    def test_the_shaft_of_a_stepper_is_the_stepper(self):
        (state, text), shown = self.judge([body('NEMA17 stepper / Shaft', 'bought', path='NEMA17 stepper'), SCREW])
        self.assertIs(state, False); self.assertIn('Shaft', shown)

    def test_a_motor_shaft_on_the_axis_of_a_hole_is_the_motor(self):
        coax = {'name': 'ref NEMA17 stepper / shaft D5', 'kind': 'bought', 'span': [2.0, -8.0]}
        (state, _), shown = self.judge([SCREW], [hole([(5.4, 6.0)], [coax])])
        self.assertIs(state, False); self.assertIn('shaft D5', shown)

    def test_a_connector_named_by_its_pins_or_family_is_a_connector(self):
        for name in ('ref electronics / JST XH 4 pin', 'ref encoder board / Pin header 1x4'):
            with self.subTest(name=name):
                (state, _), shown = self.judge([body(name), SCREW])
                self.assertIs(state, False); self.assertIn('are a connector by their own name', shown)

    def test_a_magnet_in_a_screw_component_is_no_proof_of_no_motor(self):
        (state, text), shown = self.judge([body('ref screws and magnets / Rod magnet D4x10'), SCREW])
        self.assertIs(state, False); self.assertIn('no standard part and no bearing by their name', text); self.assertIsNone(shown)

    def test_screws_dowels_inserts_and_bearings_by_their_designation_still_say_no_motor(self):
        near = [SCREW, WASHER, body('ref axle / dowel 8x60'), body('ref hardware / insert M3x4x5 (2)'), body('ref 608 / 608 (1)'),
                body('ref drive screws: cover + motor screws / M3x12 DIN 912 (cover to base)')]
        (state, text), shown = self.judge(near)
        self.assertIs(state, True, text); self.assertIsNone(shown)


class PocketContentClosed(h.Project):
    """fits.pocket_content, "not fitted in this hole": held on measured radii and on the fit a name carries."""
    RULES = ('fits.pocket_content',)
    NOBOUGHT, PASS, SEAT = h.labels('fits.pocket_content')

    def question(self, hl, by):
        return h.PocketContent.question(self, hl, by)

    def test_a_named_fit_on_the_hole_diameter_is_never_passing_with_room(self):
        pilot = {'name': 'ref gearbox / pilot D25h7 x 2', 'kind': 'bought', 'span': [2.0, -2.0]}
        q = self.question(hole([(25.0, 2.0), (12.0, 6.0)], [pilot], through=False), [pilot['name']])
        self.assertIn('carries the fit class of D25 in its name', gate_attest.label_contradicted(self.root, self.reg, q, self.PASS))
        pilot = dict(pilot, r=[[11.0, 2.0, -2.0]])                                      # even with radii that seem to pass
        q = self.question(hole([(25.0, 2.0), (12.0, 6.0)], [pilot], through=False), [pilot['name']])
        self.assertIn('carries the fit class of D25 in its name', gate_attest.label_contradicted(self.root, self.reg, q, self.PASS))
        pin = {'name': 'ref drive / pin 4m6x32', 'kind': 'hardware', 'span': [0.0, -5.0]}
        q = self.question(hole([(4.02, 5.0)], [pin], through=False), [pin['name']])
        self.assertIn('fit class of D4', gate_attest.label_contradicted(self.root, self.reg, q, self.PASS))

    def test_facts_without_radii_refuse_passing_with_room(self):
        shaft = {'name': 'ref drive / shaft D8', 'kind': 'bought', 'span': [5.0, -10.0]}
        q = self.question(hole([(28.0, 5.0)], [shaft]), [shaft['name']])
        self.assertIn('stores no radii', gate_attest.label_contradicted(self.root, self.reg, q, self.PASS))

    def test_a_body_that_passes_with_room_on_measured_radii_keeps_the_label(self):
        shaft = {'name': 'ref drive / shaft D8', 'kind': 'bought', 'span': [5.0, -10.0], 'r': [[4.0, 5.0, -10.0]]}
        q = self.question(hole([(28.0, 5.0)], [shaft]), [shaft['name']])
        self.assertIsNone(gate_attest.label_contradicted(self.root, self.reg, q, self.PASS))

    def test_a_hole_not_in_the_census_refuses_it(self):
        q = self.question(hole([(28.0, 5.0)], []), ['ref drive / shaft D8'])
        q['anchor'] = h.gate_features.anchor('hole', (1, 0, 0), (50, 50, 50))
        self.assertIn('not in the census', gate_attest.label_contradicted(self.root, self.reg, q, self.PASS))


if __name__ == '__main__':
    unittest.main()
