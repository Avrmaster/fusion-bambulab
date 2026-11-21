"""gate_attest: questions per part and per feature, the evidence each kind needs, answers carried to a new signature,
the questions the facts of a part answer (a fixture pair for every fact test: tests/fixtures/questions), and the
questions of an inherited part that wait.

No Fusion (tests/fake_fusion.py). The small demo registry wherever a project is walked; the fact tests and the "auto"
entries are held against the SHIPPED registry and its tables."""
import json, os, re, sys, time, unittest
for _k in ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID'): os.environ.pop(_k, None)      # gate.py owner from an agent session needs the hook's ticket: a test is no agent session
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_fusion, gate_attest, gate_common, gate_features, gate_params, gate_plan, gate_registry, gate_report
from gate_common import GateError

QUESTION_FIXTURES = os.path.join(ROOT, 'tests', 'fixtures', 'questions')

BRACKET = 'demo/bracket'
HOLE = gate_features.anchor('hole', (0, 1, 0), (5, 10, 3))
OPENING = gate_features.anchor('hole', (1, 0, 0), (20, 4, 6))
UNKNOWN_HOLE = {'holes': [{'id': 'h1', 'anchor': HOLE, 'd': 2.7, 'class': None}], 'openings': [], 'counts': {}}
COVERED = {'openings': [{'anchor': OPENING, 'by': ['Cover']}], 'closed': []}


class Asked(unittest.TestCase):
    QUESTIONS = ('process.concept_geometry', 'process.visual_check', 'hardware.bought_from_step', 'print.bed_face')

    def setUp(self):
        self.demo = fake_fusion.DemoProject(self, part_questions=self.QUESTIONS)
        self.root = self.demo.root
        self.reg = self.demo.registry()
        self.geometry('v1')

    def geometry(self, word, other='v1'):
        """Change the model, scan, run every unit of the bracket and judge it. Returns its verdict."""
        self.sig = self.demo.model({'Bracket': word, 'Cover': other})[BRACKET]
        gate_plan.scan(self.root, wall_s=600)
        self.resolved = gate_params.resolve(self.reg, gate_params.load_overrides(self.root), None)
        gate_plan.run_units(self.root, self.reg, self.resolved, gate_plan.new_plan(self.root, self.reg, [BRACKET]), wall_s=600)
        gate_plan.judge_part(self.root, self.reg, self.resolved, BRACKET)
        return self.verdict()

    def verdict(self, stages=('design',)):
        return gate_plan.part_verdict(self.root, self.reg, BRACKET, stages=stages)

    def q(self, rule, stages=('design', 'export')):
        return next(x for x in gate_attest.open_questions(self.root, self.reg, [BRACKET], stages) if x['rule'] == rule)

    def answer(self, rule, kind, value):
        return gate_attest.answer(self.root, self.reg, self.resolved, self.q(rule)['q'], kind, value)

    def picture(self, name='shot.png', age_s=0):
        path = self.demo.path(name)
        with open(path, 'wb') as f: f.write(b'\x89PNG demo')
        t = time.time() - age_s
        os.utime(path, (t, t))
        return path


class Questions(Asked):
    def test_part_level_questions_of_the_stage_and_their_shape(self):
        v = self.verdict()
        self.assertEqual(v['verdict'], 'attest')
        self.assertEqual(sorted(q['rule'] for q in v['questions']), ['hardware.bought_from_step', 'process.concept_geometry', 'process.visual_check'])
        q = next(x for x in v['questions'] if x['rule'] == 'process.concept_geometry')
        self.assertEqual((q['part'], q['anchor'], q['context'], q['evidence'], q['labels'], q['finding'], q['answer'], q['stage']),
                         (BRACKET, gate_features.part_anchor(), self.sig[:12], ['label'], [fake_fusion.NONE_FINAL, 'yes: listed for the owner'], None, None, 'design'))
        self.assertEqual(q['q'], gate_attest.question_id('process.concept_geometry', BRACKET, gate_features.part_anchor(), self.sig[:12]))
        self.assertRegex(q['q'], '^Q-[0-9a-f]{6}$')
        export = [x['rule'] for x in self.verdict(('design', 'export'))['questions']]
        self.assertIn('print.bed_face', export)                                              # asked at the export stage only

    def test_a_deferred_rule_and_a_rule_for_another_source_ask_nothing(self):
        rules = [q['rule'] for q in gate_attest.questions_for_part(self.root, self.reg, BRACKET, self.sig, [], ('design', 'export'))]
        self.assertNotIn('teeth.profile', rules)
        self.reg['rules']['process.concept_geometry']['applies'] = 'generated'
        rules = [q['rule'] for q in gate_attest.questions_for_part(self.root, self.reg, BRACKET, self.sig, [], ('design',))]
        self.assertNotIn('process.concept_geometry', rules); self.assertIn('process.visual_check', rules)

    def test_a_finding_that_asks_becomes_a_question_about_that_feature(self):
        self.demo.facts('fusion_census', BRACKET, UNKNOWN_HOLE); self.demo.facts('fusion_openings', BRACKET, COVERED)
        v = self.geometry('v2')
        asked = {f['rule']: f for f in v['asked']}
        self.assertEqual(sorted(asked), ['access.hole_covered', 'census.unknown_hole'])
        self.assertEqual(v['open'], [])                                                      # an asked finding is a question, not a block row
        q = {x['rule']: x for x in v['ask_questions']}
        self.assertEqual((q['assembly.order']['question'], q['assembly.order']['anchor'], q['assembly.order']['context'], q['assembly.order']['finding_rule']),
                         ('Opening at (20, 4, 6) is covered by Cover. Is Cover mounted later?', OPENING, asked['access.hole_covered']['context'], 'access.hole_covered'))
        self.assertEqual(q['census.unknown_hole']['finding'], asked['census.unknown_hole']['id'])
        self.assertEqual(q['census.unknown_hole']['evidence'], ['label', 'waiver'])

    def test_only_parts_a_run_has_looked_at_are_asked_about(self):
        asked = {q['part'] for q in gate_attest.open_questions(self.root, self.reg)}
        self.assertEqual(asked, {BRACKET})                                                   # demo/cover was scanned and never run
        rules = [q['rule'] for q in gate_attest.open_questions(self.root, self.reg)]
        self.assertEqual(rules, sorted(rules)); self.assertIn('print.bed_face', rules)
        with self.assertRaises(GateError): gate_attest.open_questions(self.root, self.reg, ['demo/nope'])


class Evidence(Asked):
    def refused(self, rule, kind, value):
        return gate_attest.validate_answer(self.root, self.reg, self.resolved, self.q(rule), kind, value)

    def test_a_label_is_one_of_the_fixed_texts_word_for_word(self):
        self.assertIsNone(self.refused('process.concept_geometry', 'label', fake_fusion.NONE_FINAL))
        self.assertIn('word for word', self.refused('process.concept_geometry', 'label', 'none'))
        self.assertIn('word for word', self.refused('process.concept_geometry', 'label', fake_fusion.NONE_FINAL + '.'))
        self.assertIn('is empty', self.refused('process.concept_geometry', 'label', '  '))

    def test_a_kind_the_rule_does_not_take_is_refused(self):
        self.assertIn('takes label as evidence, not na', self.refused('process.concept_geometry', 'na', 'this part has no functional shape at all'))
        self.assertIn('takes shot as evidence, not label', self.refused('process.visual_check', 'label', fake_fusion.NONE_FINAL))
        self.assertIn('must be one of', self.refused('process.concept_geometry', 'measure', '4.2'))      # no kind takes a typed number

    def test_a_picture_must_exist_and_be_newer_than_the_run(self):
        self.assertIn('no picture', self.refused('process.visual_check', 'shot', 'nowhere.png'))
        self.assertIn('older than the latest run', self.refused('process.visual_check', 'shot', self.picture(age_s=3600)))
        text = self.demo.path('notes.txt')
        with open(text, 'w') as f: f.write('looked fine')
        self.assertIn('not a picture', self.refused('process.visual_check', 'shot', text))
        empty = self.demo.path('empty.png'); open(empty, 'wb').close()
        self.assertIn('no picture', self.refused('process.visual_check', 'shot', empty))
        self.assertIsNone(self.refused('process.visual_check', 'shot', self.picture()))
        self.assertIsNone(self.refused('process.visual_check', 'shot', 'shot.png'))                        # relative to the project root
        stored = self.answer('process.visual_check', 'shot', self.picture())
        self.assertEqual((stored['value'], stored['sha256']), ('shot.png', gate_common.sha256_file(self.demo.path('shot.png'))))

    def test_a_parameter_is_evidence_only_when_measured_or_set_by_the_owner(self):
        self.assertIn('tagged D', self.refused('hardware.bought_from_step', 'param', 'inserts.wall_target'))
        self.assertIsNone(self.refused('hardware.bought_from_step', 'param', 'walls.min_face_width'))      # tag M in the registry
        self.assertIn('unknown parameter', self.refused('hardware.bought_from_step', 'param', 'motors.plug_width'))
        gate_common.write_json(gate_common.gate_path(self.root, 'owner', 'params.json'),
                               {'inserts.wall_target': {'value': 4.5, 'tag': 'U', 'source': 'owner', 'date': '2026-03-04'}})
        self.resolved = gate_params.resolve(self.reg, gate_params.load_overrides(self.root), None)
        self.assertIsNone(self.refused('hardware.bought_from_step', 'param', 'inserts.wall_target'))

    def test_not_applicable_needs_a_reason(self):
        self.assertIn('needs a reason', self.refused('hardware.bought_from_step', 'na', 'n/a'))
        self.assertIn('needs a reason', self.refused('hardware.bought_from_step', 'na', 'ok, export'))
        self.assertIsNone(self.refused('hardware.bought_from_step', 'na', 'this bracket only touches two printed plates'))

    def test_a_waiver_must_be_the_owners_and_fit_the_feature(self):
        self.demo.facts('fusion_openings', BRACKET, COVERED); self.geometry('v2')
        q = self.q('assembly.order')
        self.assertIn('no waiver W-9', gate_attest.validate_answer(self.root, self.reg, self.resolved, q, 'waiver', 'W-9'))
        w = {'id': 'W-1', 'rule': 'assembly.order', 'part': BRACKET, 'anchor': OPENING, 'context': 'ffffffffffffffff',
             'reason': 'the cover is the last part mounted and the first one taken off', 'quote': 'the cover always comes off first', 'date': '2026-03-04'}
        gate_common.write_json(gate_common.gate_path(self.root, 'owner', 'waivers.json'), {'waivers': [w]})
        self.assertIn('not a sound waiver', gate_attest.validate_answer(self.root, self.reg, self.resolved, q, 'waiver', 'W-1'))    # another context
        gate_common.write_json(gate_common.gate_path(self.root, 'owner', 'waivers.json'), {'waivers': [dict(w, context=q['context'])]})
        self.assertIsNone(gate_attest.validate_answer(self.root, self.reg, self.resolved, q, 'waiver', 'W-1'))
        gate_attest.answer(self.root, self.reg, self.resolved, q['q'], 'waiver', 'W-1')
        v = self.verdict()
        self.assertEqual((v['asked'], [f['rule'] for f in v['answered']]), ([], ['access.hole_covered']))
        self.assertTrue(gate_attest.closes(v['answered'][0], gate_attest.load_answers(self.root, BRACKET)))
        gate_common.write_json(gate_common.gate_path(self.root, 'owner', 'waivers.json'), {'waivers': []})         # the waiver is withdrawn
        self.assertEqual(len(self.verdict()['asked']), 1)                                                          # ... and its answer with it


class Answers(Asked):
    def test_an_answer_is_stored_and_closes_its_question(self):
        stored = self.answer('process.concept_geometry', 'label', fake_fusion.NONE_FINAL)
        self.assertEqual((stored['rule'], stored['part'], stored['kind'], stored['value'], stored['sig'], stored['context'], stored['anchor']),
                         ('process.concept_geometry', BRACKET, 'label', fake_fusion.NONE_FINAL, self.sig, self.sig[:12], gate_features.part_anchor()))
        self.assertRegex(stored['time'], r'^\d{4}-\d\d-\d\dT')
        data = self.demo.read('gate', 'attest', 'demo', 'bracket.json')
        self.assertEqual((data['part'], data['answers']), (BRACKET, [stored])); self.assertEqual(gate_attest.load_answers(self.root, BRACKET), [stored])
        self.assertEqual(gate_attest.load_answers(self.root, 'demo/cover'), [])
        self.assertNotIn('process.concept_geometry', [q['rule'] for q in self.verdict()['questions']])
        with self.assertRaises(GateError) as e: gate_attest.answer(self.root, self.reg, self.resolved, stored['q'], 'label', fake_fusion.NONE_FINAL)
        self.assertIn('no open question', str(e.exception))                                  # answered once

    def test_a_refused_answer_stores_nothing(self):
        with self.assertRaises(GateError) as e: self.answer('process.concept_geometry', 'label', 'looks final to me')
        self.assertIn('refused', str(e.exception))
        self.assertFalse(os.path.exists(gate_common.attest_path(self.root, BRACKET)))
        with self.assertRaises(GateError): gate_attest.answer(self.root, self.reg, self.resolved, 'Q-000000', 'label', fake_fusion.NONE_FINAL)

    def test_all_answered_is_pass_attested_with_the_count(self):
        self.answer('process.concept_geometry', 'label', fake_fusion.NONE_FINAL)
        self.answer('process.visual_check', 'shot', self.picture())
        self.assertEqual(self.verdict()['verdict'], 'attest')                                # one question is still open
        self.answer('hardware.bought_from_step', 'na', 'this bracket only touches two printed plates')
        v = self.verdict()
        self.assertEqual((v['verdict'], v['attested'], v['questions']), ('pass_attested', 3, []))
        self.assertEqual(gate_attest.attested_count(self.root, self.reg, BRACKET, self.sig, [], ('design',)), 3)
        self.assertEqual(gate_plan.exit_code([v]), 0)

    def test_a_part_level_answer_is_asked_again_for_a_new_geometry(self):
        self.answer('process.concept_geometry', 'label', fake_fusion.NONE_FINAL)
        v = self.geometry('v2')
        self.assertIn('process.concept_geometry', [q['rule'] for q in v['questions']])
        self.assertEqual(len(gate_attest.load_answers(self.root, BRACKET)), 1)               # the old answer stays on file for the old geometry

    def test_an_answer_about_a_feature_is_carried_while_the_feature_is_unchanged(self):
        self.demo.facts('fusion_census', BRACKET, UNKNOWN_HOLE); self.demo.facts('fusion_openings', BRACKET, COVERED)
        self.geometry('v2')
        self.answer('census.unknown_hole', 'label', fake_fusion.CABLE)
        self.answer('assembly.order', 'label', fake_fusion.COVER_LATER)
        v = self.verdict()
        self.assertEqual((v['asked'], sorted(f['rule'] for f in v['answered'])), ([], ['access.hole_covered', 'census.unknown_hole']))
        v = self.geometry('v3: a rib was added elsewhere')                                   # new signature, the hole and the opening as they were
        self.assertEqual((v['asked'], len(v['answered'])), ([], 2))
        moved = gate_features.anchor('hole', (0, 1, 0), (5, 10, 3.4))                        # the mouth was recut 0.4 mm along its axis, and the hole is wider
        self.demo.facts('fusion_census', BRACKET, {'holes': [{'id': 'h1', 'anchor': moved, 'd': 3.4, 'class': None}], 'openings': [], 'counts': {}})
        v = self.geometry('v4: the hole was drilled out')
        self.assertEqual(([f['rule'] for f in v['asked']], [f['rule'] for f in v['answered']]), (['census.unknown_hole'], ['access.hole_covered']))

    def test_the_newest_matching_answer_counts(self):
        a = {'rule': 'r.x', 'anchor': HOLE, 'context': 'c1', 'value': 'old', 'time': '2026-03-04T10:00:00Z'}
        b = dict(a, value='new', time='2026-03-05T10:00:00Z')
        near = gate_features.anchor('hole', (0, 1, 0), (5.2, 10.9, 3))
        self.assertEqual(gate_attest.find_answer([b, a], 'r.x', near, 'c1')['value'], 'new')
        self.assertIsNone(gate_attest.find_answer([a, b], 'r.x', HOLE, 'c2')); self.assertIsNone(gate_attest.find_answer([a, b], 'r.y', HOLE, 'c1'))
        self.assertIsNone(gate_attest.find_answer([a], 'r.x', gate_features.anchor('hole', (0, 1, 0), (9, 10, 3)), 'c1'))
        f = {'rule': 'access.hole_covered', 'ask': 'r.x', 'anchor': HOLE, 'context': 'c1'}
        self.assertTrue(gate_attest.closes(f, [a])); self.assertFalse(gate_attest.closes(dict(f, ask=None), [a])); self.assertFalse(gate_attest.closes(f, []))


class CommandLine(Asked):
    QUESTIONS = ('process.concept_geometry',)

    def test_list_and_answer(self):
        code, out, err = self.demo.gate('attest', 'list')
        self.assertEqual(code, 2); self.assertIn('1 question(s) open', out); self.assertIn('--label "%s"' % fake_fusion.NONE_FINAL, out)
        qid = re.search(r'^(Q-[0-9a-f]{6})  demo/bracket  process.concept_geometry', out, re.M).group(1)
        code, out, err = self.demo.gate('attest', 'answer', qid, '--label', 'final')
        self.assertEqual(code, 4); self.assertIn('word for word', err); self.assertEqual(gate_attest.load_answers(self.root, BRACKET), [])
        code, out, err = self.demo.gate('attest', 'answer', qid, '--label', fake_fusion.NONE_FINAL)
        self.assertEqual((code, err), (0, '')); self.assertIn('0 question(s) open', out)
        self.assertEqual(self.demo.gate('attest', 'list')[0], 0)
        self.assertEqual(self.demo.gate('attest', 'answer', qid, '--label', fake_fusion.NONE_FINAL)[0], 4)        # no open question of that id any more
        self.assertEqual(self.demo.gate('attest', 'answer', qid)[0], 4); self.assertEqual(self.demo.gate('attest', 'answer')[0], 4)

    def test_the_owner_can_waive_a_question_that_takes_a_waiver(self):
        self.demo.facts('fusion_census', BRACKET, UNKNOWN_HOLE); self.demo.facts('fusion_openings', BRACKET, COVERED); self.geometry('v2')
        qid = self.q('assembly.order')['q']
        code, out, err = self.demo.gate('owner', 'waive', qid, '--reason', 'the cover is the last part mounted and the first one taken off', '--quote', 'the cover comes off first')
        self.assertEqual((code, err), (0, '')); self.assertIn('W-1 written', out); self.assertIn('is answered by it', out)
        w = self.demo.read('gate', 'owner', 'waivers.json')['waivers'][0]
        self.assertEqual((w['rule'], w['part'], w['anchor'], w['quote'], w['for']), ('assembly.order', BRACKET, OPENING, 'the cover comes off first', qid))
        a = gate_attest.load_answers(self.root, BRACKET)[0]
        self.assertEqual((a['kind'], a['value'], a['rule']), ('waiver', 'W-1', 'assembly.order'))
        self.assertEqual([f['rule'] for f in self.verdict()['asked']], ['census.unknown_hole'])
        qid = self.q('census.unknown_hole')['q']                                              # the rule of this question raised the finding itself
        code, out, err = self.demo.gate('owner', 'waive', qid, '--reason', 'a drain hole for the wash water of the cleaner', '--quote', 'that one is a drain')
        self.assertEqual((code, err), (0, '')); self.assertIn('W-2 written', out); self.assertIn('is waived by it', out)
        v = self.verdict()
        self.assertEqual((v['asked'], [(f['rule'], f['waiver']) for f in v['waived']]), ([], [('census.unknown_hole', 'W-2')]))
        self.assertEqual(len(gate_attest.load_answers(self.root, BRACKET)), 1)
        pid = self.q('process.concept_geometry')['q']                                         # this rule takes a label only
        code, out, err = self.demo.gate('owner', 'waive', pid, '--reason', 'a drain hole for the wash water of the cleaner', '--quote', 'q')
        self.assertEqual(code, 4); self.assertIn('not a waiver', err)

    def test_the_list_says_once_per_rule_what_it_asks_and_takes(self):
        self.geometry('v1', other='v1')
        gate_plan.run_units(self.root, self.reg, self.resolved, gate_plan.new_plan(self.root, self.reg, ['demo/cover']), wall_s=600)
        gate_plan.judge_part(self.root, self.reg, self.resolved, 'demo/cover')
        code, out, err = self.demo.gate('attest', 'list')
        self.assertEqual(code, 2); self.assertIn('2 question(s) open', out)
        self.assertIn('process.concept_geometry  [design]: 2 question(s)', out)
        self.assertEqual(out.count('--label "%s"' % fake_fusion.NONE_FINAL), 1)               # the answers a rule takes: once, not once per part
        self.assertEqual(len(re.findall(r'^Q-[0-9a-f]{6}  demo/(bracket|cover)  process.concept_geometry$', out, re.M)), 2)
        code, out, err = self.demo.gate('attest', 'list', '--part', BRACKET)
        self.assertEqual(code, 2); self.assertIn('1 question(s) open', out); self.assertNotIn('demo/cover', out)


class FactFixtures(unittest.TestCase):
    """Every fact test against its fixture pairs: a case where the question must be asked (the real defect: a screw, a
    bearing or a motor that no unit recognised) and its twin where the facts prove that it cannot apply."""

    def setUp(self):
        self.reg = gate_registry.load_default()                                              # the shipped registry and its tables
        self.defaults = {n: self.reg['params'][n]['default'] for n in gate_attest.FACT_PARAMS}
        self.fixtures = []
        for name in sorted(os.listdir(QUESTION_FIXTURES)):
            with open(os.path.join(QUESTION_FIXTURES, name), encoding='utf-8') as f:
                self.fixtures.append((name, json.load(f)))

    def test_every_fixture(self):
        self.assertGreater(len(self.fixtures), 15)
        for name, fx in self.fixtures:
            with self.subTest(fixture=name):
                self.assertTrue(name.startswith('questions_%s__' % fx['test'])); self.assertIn(fx['test'], gate_attest.FACT_TESTS)
                state, text = gate_attest.FACT_TESTS[fx['test']](fx['probes'].get, dict(self.defaults, **fx.get('params') or {}), self.reg['tables'])
                self.assertIs(state, fx['expect'], text); self.assertIn(fx['says'], text)

    def test_every_fact_test_has_a_case_that_asks_and_a_twin_that_answers(self):
        for test in gate_attest.FACT_TESTS:
            seen = {fx['expect'] for _, fx in self.fixtures if fx['test'] == test}
            self.assertEqual(seen, {True, False, None}, test)                                 # proven, shown by the facts, and "cannot say"

    def test_the_registry_and_the_code_name_the_same_tests(self):
        self.assertEqual(sorted(gate_attest.FACT_TESTS), sorted(gate_registry.FACT_TESTS)); self.assertEqual(sorted(gate_attest.TEST_PROBES), sorted(gate_registry.FACT_TESTS))
        for probes in gate_attest.TEST_PROBES.values():
            for probe in probes: self.assertIn(probe, gate_registry.PROBES)
        auto = {rid: r['attest']['auto'] for rid, r in self.reg['rules'].items() if (r.get('attest') or {}).get('auto')}
        self.assertEqual(sorted(auto), ['hardware.bought_from_step', 'motors.lead_path', 'print.bed_face', 'screws.count_load_joint'])      # key swing, plastic in a clamp and refs present are judged now
        for rid, entries in auto.items():
            a = self.reg['rules'][rid]['attest']
            for e in entries:
                self.assertEqual(a['per'], 'part', rid); self.assertTrue(e.get('label') in a['labels'] if 'label' in e else 'na' in a['evidence'], rid)
        self.assertEqual(self.reg['params']['motors.ref_pattern'].get('tag', 'D'), 'D'); self.assertEqual(self.reg['params']['process.near_margin'].get('tag', 'D'), 'D')

    def test_the_shipped_no_screw_answers_are_held_against_what_the_screw_unit_shows(self):
        reg = self.reg
        blind = {'fusion_screws': {'screws': [], 'washers': [], 'passing': [], 'n_screws': 0, 'fastener_holes': [{'hole': 'h1', 'role': 'clearance', 'screw': None, 'bodies': ['ref hardware / stud (1)']}]},
                 'fusion_census': {'holes': []}}
        self.assertIn('1 screw hole(s) or insert pocket(s) with a screw or a bought body on the axis', gate_attest.FACT_SHOWN['no_screw_feature'](blind.get, {}, reg['tables']))
        old = {'fusion_screws': {'screws': [], 'washers': [], 'n_screws': 0},                  # facts of an older unit: the census says what sits in a screw hole
               'fusion_census': {'holes': [{'id': 'h1', 'class': {'table': 'screws', 'role': 'clearance'}, 'segments': [{'d': 3.4, 'len': 5}], 'coaxial': [{'kind': 'bought', 'name': 'ref hardware / stud (1)'}]}]}}
        self.assertIn('1 screw hole(s)', gate_attest.FACT_SHOWN['no_screw_feature'](old.get, {}, reg['tables']))
        empty = {'fusion_screws': {'screws': [], 'washers': [], 'passing': [], 'fastener_holes': [{'hole': 'h1', 'role': 'clearance', 'screw': None, 'bodies': []}]},
                 'fusion_census': {'holes': [{'id': 'h1', 'class': {'table': 'screws', 'role': 'clearance'}, 'segments': [{'d': 3.4, 'len': 5}], 'coaxial': []}]}}
        self.assertIsNone(gate_attest.FACT_SHOWN['no_screw_feature'](empty.get, {}, reg['tables']))           # an empty screw hole: asked, and nothing is shown
        self.assertIs(gate_attest.FACT_TESTS['no_screw_feature'](empty.get, {}, reg['tables'])[0], False)
        self.assertEqual(sorted(gate_attest.FACT_SHOWN), sorted(gate_attest.FACT_TESTS))
        for rid in ('screws.count_load_joint',):                                                             # the answer a blind screw unit must not get
            self.assertEqual([e['when'] for e in reg['rules'][rid]['attest']['auto']], [['no_screw_feature']], rid)

    def test_a_wrong_auto_entry_is_refused_by_the_registry(self):
        check = lambda a: (lambda errors: (gate_registry._check_attest(a, 'rule demo.x', errors), errors)[1])([])
        base = {'question': 'Any screws?', 'evidence': ['label', 'na'], 'per': 'part', 'labels': ['none', 'some']}
        self.assertEqual(check(dict(base, auto=[{'when': ['no_screw_feature'], 'label': 'none'}])), [])
        self.assertEqual(check(dict(base, auto=[{'when': ['no_screw_feature'], 'na': 'this part has no screw at all'}])), [])
        for bad in ([{'when': ['no_such_test'], 'label': 'none'}], [{'when': [], 'label': 'none'}], [{'when': ['no_screw_feature'], 'label': 'nothing'}],
                    [{'when': ['no_screw_feature'], 'na': 'none'}], [{'when': ['no_screw_feature'], 'label': 'none', 'na': 'no screws here at all'}], []):
            self.assertTrue(check(dict(base, auto=bad)), bad)
        self.assertTrue(check(dict(base, per='finding', auto=[{'when': ['no_screw_feature'], 'label': 'none'}])))       # only a question about the whole part


class ByFacts(Asked):
    """A standing question whose "does not apply" label the facts prove is answered by the gate itself, and listed."""
    QUESTIONS = ('process.concept_geometry', 'hardware.bought_from_step')
    NOTHING = 'it touches no bought part'

    def setUp(self):
        super().setUp()
        self.reg['rules']['hardware.bought_from_step']['attest']['auto'] = [{'when': ['no_bought_near'], 'label': self.NOTHING}]

    def near(self, *bodies, without=()):
        gate_common.save_probe_facts(self.root, BRACKET, self.sig, {'probe': 'fusion_near', 'facts': {'margin': 10.0, 'bodies': list(bodies)}, 'done': True, 'error': None})
        empty = {'fusion_census': {'holes': [], 'openings': [], 'partial': []}, 'fusion_screws': {'screws': [], 'washers': [], 'passing': [], 'n_screws': 0},
                 'fusion_bearings': {'bearings': [], 'seats_without_bearing': []}}
        for probe in empty:                              # "none near" is proven only when every unit that reads what belongs to the part ran
            if probe not in without:
                gate_common.save_probe_facts(self.root, BRACKET, self.sig, {'probe': probe, 'facts': empty[probe], 'done': True, 'error': None})
        gate_plan.judge_part(self.root, self.reg, self.resolved, BRACKET)
        return self.verdict()

    def test_with_no_facts_of_the_unit_the_question_is_asked_and_says_why(self):
        v = self.verdict()
        q = next(x for x in v['questions'] if x['rule'] == 'hardware.bought_from_step')
        self.assertEqual((q['answer'], q['facts']), (None, ['the unit fusion_near has no facts for this geometry']))
        self.assertEqual((v['verdict'], v['by_facts']), ('attest', []))

    def test_proven_by_the_facts_it_is_answered_listed_and_not_attested(self):
        v = self.near()
        self.assertEqual([q['rule'] for q in v['questions']], ['process.concept_geometry'])                  # one question less to ask
        q, = v['by_facts']
        self.assertEqual((q['rule'], q['answer']['kind'], q['answer']['as'], q['answer']['value']), ('hardware.bought_from_step', 'facts', 'label', self.NOTHING))
        self.assertIn('no bought, hardware or mesh body has its bounding box within 10 mm', q['answer']['proof'])
        stored = [a for a in gate_attest.load_answers(self.root, BRACKET) if a['kind'] == 'facts']
        self.assertEqual([(a['q'], a['rule'], a['value'], a['context']) for a in stored], [(q['q'], 'hardware.bought_from_step', self.NOTHING, self.sig[:12])])
        self.assertEqual(gate_attest.answers_gone(self.root, BRACKET, [q['q']]), [])                         # a stamp can name it like any other answer
        self.answer('process.concept_geometry', 'label', fake_fusion.NONE_FINAL)
        v = self.verdict()
        self.assertEqual((v['verdict'], v['attested'], len(v['by_facts'])), ('pass_attested', 1, 1))        # the agent stated one thing, not two
        self.assertEqual(gate_attest.attested_count(self.root, self.reg, BRACKET, self.sig, []), 1)
        report = gate_report.build(self.root, self.reg, self.resolved, 'r1', 'run', 'design', [v])
        self.assertEqual((report['totals']['by_facts'], report['parts'][0]['by_facts']), (1, 1))
        md = gate_report.render_md(report)
        self.assertIn('### Answered by facts', md); self.assertIn('hardware.bought_from_step: %s. no bought, hardware or mesh body' % self.NOTHING, md)
        self.assertIn('(1 answered by facts)', gate_report.summary_line(report))

    def test_when_the_facts_change_the_question_is_open_again(self):
        self.near()
        v = self.near({'name': 'ref drive / Rotor', 'path': 'ref drive', 'kind': 'bought', 'gap': 0.0})      # collected again: a bought body touches it
        q = next(x for x in v['questions'] if x['rule'] == 'hardware.bought_from_step')
        self.assertIn('1 bought, hardware or mesh body(ies) within 10 mm (ref drive / Rotor)', q['facts'][0])
        self.assertEqual([a for a in gate_attest.load_answers(self.root, BRACKET) if a['kind'] == 'facts'], [])       # and nothing stale stays on record
        self.assertEqual(gate_attest.answers_gone(self.root, BRACKET, [q['q']]), [q['q']])                    # a stamp that rested on it is open

    def test_an_answer_of_the_agent_is_not_replaced(self):
        self.answer('hardware.bought_from_step', 'na', 'this cover touches printed parts only')
        v = self.near()
        self.assertEqual((v['by_facts'], v['attested']), ([], 1))

    def test_the_gate_never_gives_a_label_the_facts_do_not_prove(self):
        self.reg['rules']['hardware.bought_from_step']['attest']['auto'] = [{'when': ['no_bought_near', 'no_screw_feature'], 'label': self.NOTHING}]
        v = self.near(without=('fusion_screws',))                                            # nothing near, but the screw unit never ran here: not proven
        q = next(x for x in v['questions'] if x['rule'] == 'hardware.bought_from_step')
        self.assertEqual(q['facts'], ['the unit fusion_screws has no facts for this geometry', 'the screw unit has no facts for this geometry'])


class Contradicted(Asked):
    """An answer that says a question does not apply is refused while the facts of the part show that it does, and a
    stored one is asked again when the facts turn against it (a unit is run again and now recognises the screws)."""
    QUESTIONS = ('process.concept_geometry', 'hardware.bought_from_step')
    NOTHING = 'it touches no bought part'
    ROTOR = {'name': 'ref drive / Rotor', 'path': 'ref drive', 'kind': 'bought', 'gap': 4.0}

    def setUp(self):
        super().setUp()
        self.reg['rules']['hardware.bought_from_step']['attest']['auto'] = [{'when': ['no_bought_near'], 'label': self.NOTHING}]

    def facts(self, screws=(), near=()):
        units = {'fusion_near': {'margin': 10.0, 'bodies': list(near)}, 'fusion_census': {'holes': [], 'openings': [], 'partial': []},
                 'fusion_screws': {'screws': list(screws), 'washers': [], 'passing': [], 'n_screws': len(screws)}, 'fusion_bearings': {'bearings': [], 'seats_without_bearing': []}}
        for probe in units:
            gate_common.save_probe_facts(self.root, BRACKET, self.sig, {'probe': probe, 'facts': units[probe], 'done': True, 'error': None})
        gate_plan.judge_part(self.root, self.reg, self.resolved, BRACKET)
        return self.verdict()

    def refused(self, kind, value):
        return gate_attest.validate_answer(self.root, self.reg, self.resolved, self.q('hardware.bought_from_step'), kind, value)

    def test_the_answer_is_refused_while_the_facts_show_the_thing(self):
        self.facts(screws=[{'ref': 'ref hardware / M3x12 cap (1)', 'size': 'M3'}, {'ref': 'ref hardware / M3x12 cap (2)', 'size': 'M3'}])
        why = self.refused('label', self.NOTHING)
        self.assertIn('the facts of this part show what this answer denies: 2 screw(s), washer(s) or bearing(s) at this part', why)
        self.assertIn('show what this answer denies', self.refused('na', 'this cover touches printed parts only'))       # the same claim in other words
        with self.assertRaises(GateError): self.answer('hardware.bought_from_step', 'label', self.NOTHING)

    def test_what_only_could_be_there_refuses_nothing(self):
        v = self.facts(near=[self.ROTOR])                                                    # a bought body 4 mm away by the boxes: asked, and the answer is the agent's word
        q = next(x for x in v['questions'] if x['rule'] == 'hardware.bought_from_step')
        self.assertIn('1 bought, hardware or mesh body(ies) within 10 mm', q['facts'][0])
        self.assertIsNone(self.refused('label', self.NOTHING)); self.assertIsNone(self.refused('na', 'this cover touches printed parts only'))

    def test_a_stored_answer_is_asked_again_when_the_facts_turn_against_it(self):
        self.facts(near=[self.ROTOR])
        stored = self.answer('hardware.bought_from_step', 'label', self.NOTHING)
        v = self.verdict()
        self.assertEqual(([q['rule'] for q in v['questions']], v['attested']), (['process.concept_geometry'], 1))
        self.assertEqual(gate_attest.answers_gone(self.root, BRACKET, [stored['q']], self.reg), [])
        v = self.facts(screws=[{'ref': 'ref hardware / M3x12 cap (1)', 'size': 'M3'}], near=[self.ROTOR])       # the same geometry, collected again: a screw is found
        q = next(x for x in v['questions'] if x['rule'] == 'hardware.bought_from_step')
        self.assertEqual((q['q'], q['answer'], v['attested']), (stored['q'], None, 0))
        self.assertIn('the answer "%s" given before does not hold any more: 1 screw(s), washer(s) or bearing(s) at this part' % self.NOTHING, q['facts'][0])
        self.assertEqual(gate_attest.answers_gone(self.root, BRACKET, [stored['q']], self.reg), [stored['q']])   # a stamp that rested on it is open again
        self.assertEqual(gate_attest.answers_gone(self.root, BRACKET, [stored['q']]), [])                        # without the registry only waivers are looked at
        self.assertIn('hardware.bought_from_step', [x['rule'] for x in gate_attest.open_questions(self.root, self.reg, [BRACKET])])


class Inherited(Asked):
    """A part the audit judged and nobody changed is not asked; its questions wait for a change or for its export."""
    QUESTIONS = ('process.concept_geometry', 'process.visual_check')

    def setUp(self):
        super().setUp()
        self.demo.facts('fusion_census', BRACKET, UNKNOWN_HOLE)
        self.geometry('v2')

    def test_before_the_audit_everything_is_asked(self):
        v = gate_plan.part_verdict(self.root, self.reg, BRACKET, defer_inherited=True)
        self.assertEqual((v['verdict'], v['state'], v['inherited_part'], len(v['questions']), len(v['ask_questions']), v['deferred_questions']), ('attest', 'changed', False, 2, 1, []))

    def test_the_audit_baseline_makes_the_part_inherited_and_its_questions_wait(self):
        self.assertTrue(gate_report.baseline_add(self.root, 'r-audit', BRACKET, self.sig))
        self.assertFalse(gate_report.baseline_add(self.root, 'r-later', BRACKET, 'f' * 64))                   # written once: a later audit does not move it
        self.assertEqual(gate_report.baseline(self.root)[BRACKET]['sig'], self.sig)
        v = gate_plan.part_verdict(self.root, self.reg, BRACKET, defer_inherited=True)
        self.assertEqual((v['verdict'], v['state'], v['inherited_part'], v['questions'], v['asked'], v['ask_questions']), ('inherited', 'inherited', True, [], [], []))
        self.assertEqual(sorted(q['rule'] for q in v['deferred_questions']), ['census.unknown_hole', 'process.concept_geometry', 'process.visual_check'])
        self.assertEqual([f['rule'] for f in v['deferred_asked']], ['census.unknown_hole'])
        self.assertEqual(gate_plan.exit_code([v]), 0)                                         # nothing new: such a part does not fail a run
        self.assertEqual(gate_attest.open_questions(self.root, self.reg, [BRACKET]), [])
        self.assertEqual(len(gate_attest.open_questions(self.root, self.reg, [BRACKET], waiting=True)), 3)
        stamp = self.verdict()                                                                # what a stamp needs: everything is asked, as before
        self.assertEqual((stamp['verdict'], len(stamp['questions']), len(stamp['asked']), stamp['deferred_questions']), ('attest', 2, 1, []))

    def test_a_waiting_question_can_be_answered_ahead_of_the_export(self):
        gate_report.baseline_add(self.root, 'r-audit', BRACKET, self.sig)
        q = next(x for x in gate_attest.open_questions(self.root, self.reg, [BRACKET], waiting=True) if x['rule'] == 'process.concept_geometry')
        gate_attest.answer(self.root, self.reg, self.resolved, q['q'], 'label', fake_fusion.NONE_FINAL)
        self.assertEqual(len(gate_attest.open_questions(self.root, self.reg, [BRACKET], waiting=True)), 2)
        self.assertEqual(len(self.verdict()['questions']), 1)

    def test_a_change_ends_it_every_question_is_asked_for_the_new_geometry(self):
        gate_report.baseline_add(self.root, 'r-audit', BRACKET, self.sig)
        v = self.geometry('v3')                                                               # the part changed after the audit
        self.assertEqual(gate_report.baseline(self.root)[BRACKET]['sig'] == self.sig, False)
        v = gate_plan.part_verdict(self.root, self.reg, BRACKET, defer_inherited=True)
        self.assertEqual((v['verdict'], v['state'], v['inherited_part'], len(v['questions']), len(v['ask_questions']), v['deferred_questions']), ('attest', 'changed', False, 2, 1, []))
        self.assertFalse(gate_report.baseline_add(self.root, 'r-again', BRACKET, self.sig))                   # and a second audit cannot make it inherited again

    def test_a_stamped_part_is_not_inherited_and_cannot_become_it(self):
        fake_fusion.whole_stamp(self.root, BRACKET, self.sig)
        self.assertFalse(gate_report.baseline_add(self.root, 'r-audit', BRACKET, self.sig))
        gate_common.write_json(gate_common.gate_path(self.root, 'owner', 'triage.json'), {'items': [], 'baseline': {BRACKET: {'sig': self.sig, 'run': 'r', 'time': 't'}}})
        v = gate_plan.part_verdict(self.root, self.reg, BRACKET, defer_inherited=True)
        self.assertEqual((v['inherited_part'], v['state'], v['deferred_questions']), (False, 'unchanged', []))

    def test_the_list_counts_what_waits_and_shows_it_for_one_part(self):
        gate_report.baseline_add(self.root, 'r-audit', BRACKET, self.sig)
        code, out, err = self.demo.gate('attest', 'list')
        self.assertEqual(code, 0); self.assertIn('0 question(s) open', out); self.assertNotIn('Q-', out.replace('Q-ID', ''))
        self.assertIn('3 question(s) of 1 inherited part(s) wait: they are asked when the part changes, and at its export', out)
        code, out, err = self.demo.gate('attest', 'list', '--part', BRACKET)
        self.assertEqual(len(re.findall(r'^Q-[0-9a-f]{6}  demo/bracket  \S+  \(waits: an inherited part\)$', out, re.M)), 3)
        self.assertIn('Hole at (5, 10, 3): what is this hole for?', out)
        code, out, err = self.demo.gate('status')
        self.assertIn('0 questions', out.splitlines()[0]); self.assertIn('3 questions wait at 1 inherited parts', out)


if __name__ == '__main__':
    unittest.main()
