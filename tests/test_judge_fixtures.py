"""The shared judge machinery (finding, pv, run_judge, judge_hash) and the fixture runner for every judge.

A fixture is one file tests/fixtures/facts/<judge>__<case>.json, built from a real incident with invented names:

    {"judge": "inserts_wall", "case": "2.5 mm wall with free space round it",
     "facts": {...},                  the "facts" object of the judge's probe
     "params": {"inserts.wall_target": 4.0},     optional: laid over the registry defaults (flat form, gate_params.values)
     "ctx": {"part": "demo/bracket", "prev": null, "intent": null, "census": null, "target": null},     optional
     "expect": [{"verdict": "GROW", "measure": 2.5, "limit": 4.0}]}        one entry per finding, [] = a pass

Every key of an "expect" entry must equal the same key of one finding (numbers to 1e-6); the number of findings must be
the number of entries. A judge that is built and has no fixture fails tests/test_registry.py."""
import copy, json, os, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
import gate_features, gate_judges, gate_params, gate_registry
from gate_judges import finding, pv, run_judge

FIXTURES = os.path.join(ROOT, 'tests', 'fixtures', 'facts')
RULE = {'id': 'demo.rule', 'check': {'probe': 'fusion_census', 'judge': 'demo_rule', 'needs': 'fusion'}, 'fix_order': ['Do this first.', 'Then this.']}
CTX = {'part': 'demo/bracket', 'rule': 'demo.rule', 'tables': {}, 'target': None, 'census': None, 'prev': None, 'intent': None}


def same(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(a - b) < 1e-6
    return a == b


def matches(expected, got):
    return all(k in got and same(v, got[k]) for k, v in expected.items())


def check_fixture(test, fx, reg, defaults):
    rule = next(r for r in reg['rules'].values() if (r.get('check') or {}).get('judge') == fx['judge'])
    params = dict(defaults, **fx.get('params', {}))
    ctx = dict(CTX, rule=rule['id'], tables=reg['tables'])
    ctx.update(fx.get('ctx') or {})
    got = run_judge(rule, fx['facts'], params, ctx)
    left = list(got)
    for e in fx['expect']:
        hit = next((g for g in left if matches(e, g)), None)
        test.assertIsNotNone(hit, 'no finding like %s in %s' % (json.dumps(e), json.dumps(got, indent=1)))
        left.remove(hit)
    test.assertEqual(left, [], 'findings the fixture does not expect')


class Fixtures(unittest.TestCase):
    def test_every_fixture(self):
        reg = gate_registry.load_default()
        defaults = gate_params.values(gate_params.resolve(reg))
        names = sorted(f for f in os.listdir(FIXTURES) if f.endswith('.json')) if os.path.isdir(FIXTURES) else []
        for name in names:
            with self.subTest(fixture=name):
                with open(os.path.join(FIXTURES, name), encoding='utf-8') as f: fx = json.load(f)
                self.assertEqual(name.split('__')[0], fx['judge'], 'the file name starts with the judge name')
                self.assertIn(fx['judge'], gate_judges.JUDGES)
                for key in ('case', 'facts', 'expect'):
                    self.assertIn(key, fx)
                check_fixture(self, fx, reg, defaults)

    def test_the_runner_itself(self):
        reg = {'rules': {'demo.rule': dict(RULE)}, 'tables': {}}
        gate_judges.JUDGES['demo_rule'] = lambda facts, params, ctx: [finding(ctx['rule'], ctx['part'], 'FAIL', 'thin', measure=facts['w'], limit=params['demo.min'])]
        self.addCleanup(gate_judges.JUDGES.pop, 'demo_rule')
        fx = {'judge': 'demo_rule', 'facts': {'w': 1.0}, 'expect': [{'verdict': 'FAIL', 'measure': 1.0, 'limit': 2.0}]}
        check_fixture(self, fx, reg, {'demo.min': 2.0})
        with self.assertRaises(AssertionError): check_fixture(self, dict(fx, expect=[]), reg, {'demo.min': 2.0})
        with self.assertRaises(AssertionError): check_fixture(self, dict(fx, expect=[{'verdict': 'GROW'}]), reg, {'demo.min': 2.0})


class Machinery(unittest.TestCase):
    def tearDown(self):
        gate_judges.JUDGES.pop('demo_rule', None)

    def test_finding_shape(self):
        f = finding('inserts.wall', 'demo/bracket', 'GROW', 'wall 2.5 under 4.0', measure=2.5, limit=4.0, by='Cover')
        self.assertEqual(sorted(f), ['anchor', 'ask', 'by', 'context', 'fix', 'limit', 'measure', 'part', 'rule', 'text', 'unit', 'verdict'])
        self.assertEqual((f['by'], f['unit'], f['fix'], f['ask'], f['anchor']), (['Cover'], 'mm', [], None, gate_features.part_anchor()))
        self.assertRegex(f['context'], '^[0-9a-f]{16}$')
        json.dumps(f)

    def test_context_is_hashed_from_what_the_judge_gives(self):
        a = finding('r.x', 'd/p', 'FAIL', 't', context={'wall': 2.5, 'blocked_by': []})
        self.assertEqual(a['context'], gate_features.context_hash({'wall': 2.5, 'blocked_by': []}))
        self.assertEqual(finding('r.x', 'd/p', 'FAIL', 't', context='0123456789abcdef')['context'], '0123456789abcdef')
        self.assertNotEqual(finding('r.x', 'd/p', 'FAIL', 't', measure=1.0)['context'], finding('r.x', 'd/p', 'FAIL', 't', measure=2.0)['context'])

    def test_bad_verdict(self):
        with self.assertRaises(ValueError): finding('r.x', 'd/p', 'WARN', 't')

    def test_pv(self):
        p = {'screws.min_engagement': 3.2, 'screws.min_engagement[M4]': 4.0}
        self.assertEqual((pv(p, 'screws.min_engagement'), pv(p, 'screws.min_engagement', 'M4'), pv(p, 'screws.min_engagement', 'M8')), (3.2, 4.0, 3.2))
        with self.assertRaises(KeyError): pv(p, 'screws.nope')

    def test_run_judge_fills_rule_part_and_fix(self):
        gate_judges.JUDGES['demo_rule'] = lambda facts, params, ctx: [finding('x.y', None, 'FAIL', 'a'), finding('x.y', 'demo/other', 'INFO', 'b', fix=['own'])]
        out = run_judge(RULE, {}, {}, CTX)
        self.assertEqual([(f['rule'], f['part'], f['fix']) for f in out],
                         [('demo.rule', 'demo/bracket', ['Do this first.', 'Then this.']), ('demo.rule', 'demo/other', ['own'])])

    def test_no_facts_is_not_run(self):
        gate_judges.JUDGES['demo_rule'] = lambda facts, params, ctx: []
        out = run_judge(RULE, None, {}, CTX)
        self.assertEqual([f['verdict'] for f in out], ['NOT_RUN']); self.assertIn('no facts from fusion_census', out[0]['text'])
        self.assertEqual(run_judge(RULE, {}, {}, CTX), [])

    def test_a_stub_or_a_broken_judge_is_not_run_never_a_pass(self):
        def stub(facts, params, ctx): raise NotImplementedError
        def broken(facts, params, ctx): return facts['missing']
        gate_judges.JUDGES['demo_rule'] = stub
        self.assertIn('not built yet', run_judge(RULE, {}, {}, CTX)[0]['text'])
        gate_judges.JUDGES['demo_rule'] = broken
        out = run_judge(RULE, {}, {}, CTX)
        self.assertEqual(out[0]['verdict'], 'NOT_RUN'); self.assertIn('KeyError', out[0]['text'])
        del gate_judges.JUDGES['demo_rule']
        self.assertIn('does not exist', run_judge(RULE, {}, {}, CTX)[0]['text'])

    def test_judge_hash_and_is_built(self):
        h = gate_judges.judge_hash('inserts_wall')
        self.assertRegex(h, '^[0-9a-f]{16}$'); self.assertEqual(h, gate_judges.judge_hash('inserts_wall'))
        self.assertNotEqual(h, gate_judges.judge_hash('inserts_floor'))
        with self.assertRaises(KeyError): gate_judges.judge_hash('nope')
        self.assertFalse(gate_judges.is_built('nope'))

    def test_every_shipped_judge_answers_through_run_judge(self):
        reg = gate_registry.load_default()
        for rule in reg['rules'].values():
            if not (rule.get('check') or {}).get('judge'): continue
            out = run_judge(rule, None, {}, dict(CTX, rule=rule['id']))
            self.assertEqual([f['verdict'] for f in out], ['NOT_RUN'], rule['id'])


if __name__ == '__main__':
    unittest.main()
