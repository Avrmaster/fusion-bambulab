"""The tight record decides, or shadows (0.3.3): gate.py run end to end on the demo project of test_deps.py.

The bracket's openings unit says what it read twice: the wide record (5 mm round the bracket, as 0.3.2) and the tight
one (the face of its window, 0.1 mm: its facts change only when the cover touches it there).
  - "deps": "wide" (the default): a cover that moves inside the 5 mm measures the unit again, and the shadow compares
    its new facts with the old ones (deps_shadow.jsonl); a wrong tight record is caught there as a violation;
  - "deps": "tight": the same move measures nothing; a move onto the window does.
  - EQUIVALENCE: after every change of one part an incremental run (only what is stale) gives row for row the findings
    of a full fresh run (every unit measured again), under both records.
  - hiding parts measures nothing and loses no stamp."""
import json, os, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate')); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_fusion, gate_common, gate_deps, gate_report
import test_deps as D
from test_deps import BRACKET, COVER, moved

WINDOW = {'box': [[40, 0, 0], [40, 30, 10]], 'margin': 0.1, 'mode': 'tight'}


class Tight(D.Deps):
    """The model of test_deps.Deps; the bracket's openings unit notes a tight region beside its wide one."""
    tight_region = WINDOW

    def setUp(self):
        D.Deps.setUp(self)
        self.move_cover(41.0)                                           # 1 mm beside the bracket: inside the wide record, off the window

    def answer(self, census_holes=()):
        D.Deps.answer(self, census_holes)
        lo, hi = gate_deps._box(self.poses['Bracket'], D.BOX)
        inputs = {'params': [], 'tables': [], 'job': ['doc', 'component_id', 'occurrence', 'body', 'features']}
        self.fusion.clear('fusion_openings', BRACKET)
        self.fusion.set_facts('fusion_openings', BRACKET, self.bracket_openings, inputs_read=inputs,
                              reads=[{'box': [list(lo), list(hi)], 'margin': 5.0, 'mode': 'wide'}, dict(self.tight_region)])

    def deps(self, mode):
        p = gate_common.read_json(gate_common.gate_path(self.root, 'project.json'), {})
        p['deps'] = mode
        gate_common.write_json(gate_common.gate_path(self.root, 'project.json'), p)

    def move_cover(self, x):
        self.poses['Cover'] = moved(x)
        self.answer()

    def findings(self):
        rows = gate_report.load_latest(self.root)['findings']
        return sorted(json.dumps({k: f.get(k) for k in ('id', 'rule', 'part', 'verdict', 'text', 'measure', 'limit', 'anchor', 'by', 'state')}, sort_keys=True)
                      for f in rows)

    def shadow(self):
        return gate_deps.shadow_summary(self.root)

    # ---- the shadow ----
    def test_wide_measures_the_move_and_the_shadow_proves_the_tight_record_held(self):
        self.gate_run()
        self.move_cover(42.0)                                           # inside the 5 mm, off the window
        code, out, err, sent = self.gate_run()
        self.assertIn((BRACKET, 'fusion_openings'), sent)              # the wide record decides: measured again
        s = self.shadow()
        self.assertEqual((s['units'], s['same'], s['violations']), (1, 1, []))
        self.assertIn('deps shadow: 1 unit(s) that the tight record would have kept were measured', out)
        self.assertIn('1 gave the same facts', out)

    def test_a_tight_record_that_misses_what_the_facts_read_is_a_violation(self):
        self.tight_region = {'box': [[0, 0, 0], [1, 1, 1]], 'margin': 0.0, 'mode': 'tight'}      # a wrong probe: it reads the window, notes a corner
        self.answer()
        self.gate_run()
        self.move_cover(40.05)                                          # onto the window: the facts change
        code, out, err, sent = self.gate_run()
        s = self.shadow()
        self.assertEqual((s['units'], s['same'], len(s['violations'])), (1, 0, 1))
        self.assertEqual(s['violations'][0]['keys'], ['openings'])
        self.assertIn('DIFFERED: demo/bracket fusion_openings', out)

    # ---- tight decides ----
    def test_tight_keeps_what_the_move_cannot_change_and_measures_what_it_can(self):
        self.deps('tight')
        self.gate_run()
        self.move_cover(42.0)
        self.assertNotIn((BRACKET, 'fusion_openings'), self.gate_run()[3])
        self.move_cover(40.05)
        self.assertIn((BRACKET, 'fusion_openings'), self.gate_run()[3])
        self.assertEqual(self.shadow()['units'], 0)                     # no shadow while tight decides

    def test_a_deps_value_that_is_no_record_is_refused(self):
        self.deps('narrow')
        code, out, err, sent = self.gate_run()
        self.assertEqual(code, 4); self.assertIn('"deps" must be one of wide, tight', out + err)

    # ---- the owner's condition: incremental == fresh, row by row ----
    def test_equivalence_incremental_runs_give_the_findings_of_full_fresh_runs(self):
        for mode in ('wide', 'tight'):
            self.deps(mode)
            self.move_cover(41.0)
            self.gate_run('--all', '--fresh')
            for x in (42.0, 40.05, 40.08, 43.0, 44.5, 40.0, 41.0):    # off, onto, along, off, off, onto, off the window
                self.move_cover(x)
                self.gate_run()
                incremental = self.findings()
                self.gate_run('--all', '--fresh')
                self.assertEqual(incremental, self.findings(), '%s, cover at %s' % (mode, x))
            self.sigs['Cover'] = fake_fusion.sig_of('cover v2')          # the other part itself changes
            self.answer()
            self.gate_run()
            incremental = self.findings()
            self.gate_run('--all', '--fresh')
            self.assertEqual(incremental, self.findings(), mode)
            self.sigs['Cover'] = fake_fusion.sig_of('cover v1')

    # ---- visibility ----
    def test_hiding_every_part_measures_nothing(self):
        self.gate_run()
        facts = self.scan_facts()
        for p in facts['parts']:
            p['shown'] = p['visible'] = False
        for r in facts['refs']:
            r['shown'] = r['visible'] = False
        self.fusion.clear('fusion_scan', None)
        self.fusion.set_facts('fusion_scan', None, facts)
        code, out, err, sent = self.gate_run()
        self.assertEqual(sent, [], out)


for _name in [n for n in vars(D.Deps) if n.startswith('test_')]:
    setattr(Tight, _name, None)                         # Tight shares the model of Deps, not its tests


if __name__ == '__main__':
    unittest.main()
