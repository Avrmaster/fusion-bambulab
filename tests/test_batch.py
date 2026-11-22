"""Short units of one part share one Fusion call (0.3.3): gate_fusion_rpc.build_batch_script / run_batch and the loop
of gate_plan. Each unit still runs in its own namespace with its own JOB and writes its own result file; they share one
walk over the document. Tried here: the script as Fusion runs it (the fake adsk of test_geom_rings) gives each unit the
facts it gives alone; the plan sends one call for the short units of a changed part and gives row for row the findings
of unbatched fresh runs; a unit that hangs inside a batch costs only itself; a batch Fusion stops answering in is read
back unit by unit by the next call. Names are invented."""
import json, os, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate')); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_fusion, gate_common, gate_fusion_rpc, gate_plan, gate_report
import test_deps as D
import test_geom_rings as T
from test_deps import BRACKET, COVER

GROUPS = [{'key': 'frame', 'patterns': ['^Bracket', '^Base', '^Cover']}, {'key': 'arm', 'patterns': ['^Pulley']}]


class Script(unittest.TestCase):
    setUp = T.Probes.setUp
    unit = T.Probes.unit
    bracket_scene = T.Probes.bracket_scene
    FEATURES = T.Probes.FEATURES

    def test_each_unit_of_a_batch_gets_the_facts_it_gets_alone(self):
        alone = {p: self.unit(p, self.bracket_scene(), features=self.FEATURES, groups=GROUPS) for p in ('fusion_near', 'fusion_gap', 'fusion_screws')}
        jobs = []
        for i, p in enumerate(('fusion_near', 'fusion_gap', 'fusion_screws')):
            j = gate_fusion_rpc.new_job('u-%04d' % (i + 1), 'r1', p, 'Demo Assembly', 'demo', os.path.join(self.d, 'units', 'u-%04d.json' % (i + 1)),
                                        part='demo/bracket', component_id='id-Bracket', occurrence='Bracket:1', body='Body1', params=dict(T.PARAMS),
                                        tables={n: T.table(n) for n in ('inserts', 'screws', 'bearings', 'washers')}, features=self.FEATURES,
                                        not_printed=['^ref '], motion_groups=GROUPS)
            j['sent'] = __import__('time').time()
            jobs.append(j)
        script = gate_fusion_rpc.build_batch_script(jobs)
        script.encode('ascii')
        self.assertEqual([j['id'] for j in gate_fusion_rpc.jobs_of(script)], ['u-0001', 'u-0002', 'u-0003'])
        mods = T.fake_adsk(self.bracket_scene())
        saved = {k: sys.modules.get(k) for k in mods}
        sys.modules.update(mods)
        try:
            g = {}
            exec(compile(script, 'batch.py', 'exec'), g)
            g['run']('')
            self.assertEqual(sorted(k for k in g if not k.startswith('__')), ['json', 'run'])        # nothing else is left in the shared namespace
        finally:
            for k, v in saved.items():
                if v is None: sys.modules.pop(k, None)
                else: sys.modules[k] = v
        for j in jobs:
            with open(j['out'], encoding='utf-8') as f:
                env = json.load(f)
            self.assertIsNone(env['error'], env['error'])
            self.assertEqual(env['facts'], alone[j['probe']]['facts'], j['probe'])
            self.assertEqual(env['reads'], alone[j['probe']]['reads'], j['probe'])
            self.assertEqual(env['asked']['sent'], j['sent'])

    def test_a_batch_is_of_one_part(self):
        a = gate_fusion_rpc.new_job('u-1', 'r1', 'fusion_near', 'D', 'demo', '/x/u-1.json', part='demo/a')
        b = gate_fusion_rpc.new_job('u-2', 'r1', 'fusion_near', 'D', 'demo', '/x/u-2.json', part='demo/b')
        with self.assertRaises(gate_common.GateError): gate_fusion_rpc.build_batch_script([a, b])
        with self.assertRaises(gate_common.GateError): gate_fusion_rpc.build_batch_script([])


class Plan(D.Deps):
    def batched(self, since=0):
        return [ids for ids in self.fusion.scripts[since:] if len(ids) > 1]

    def findings(self):
        return sorted(json.dumps({k: f.get(k) for k in ('id', 'rule', 'part', 'verdict', 'text', 'measure', 'anchor', 'by')}, sort_keys=True)
                      for f in gate_report.load_latest(self.root)['findings'])

    def test_the_short_units_of_a_changed_part_share_one_call_and_give_the_findings_of_fresh_runs(self):
        self.gate_run()
        self.assertEqual(self.batched(), [])                            # the first run knows no unit's cost: one call each
        for x in (40.05, 43.0, 40.0):                                   # the cover moves onto the bracket's window, away, onto it again
            self.poses['Cover'] = D.moved(x)
            self.sigs['Cover'] = fake_fusion.sig_of('cover %s' % x)
            self.answer()
            n = len(self.fusion.scripts)
            code, out, err, sent = self.gate_run()
            self.assertTrue(self.batched(n), 'no call carried more than one unit')
            for ids in self.batched(n):
                units = {u['id']: u for u in gate_plan.load_plan(self.root)['units']}
                self.assertEqual(len({units[i]['part'] for i in ids}), 1)
                self.assertNotIn('fusion_census', [units[i]['probe'] for i in ids])
            incremental = self.findings()
            layout = gate_common.read_json(gate_common.gate_path(self.root, 'layout.json'), {})
            layout['fusion']['batch'] = False
            gate_common.write_json(gate_common.gate_path(self.root, 'layout.json'), layout)
            n = len(self.fusion.scripts)
            self.gate_run('--all', '--fresh')
            self.assertEqual(self.batched(n), [])
            self.assertEqual(incremental, self.findings(), 'cover at %s' % x)
            layout['fusion']['batch'] = True
            gate_common.write_json(gate_common.gate_path(self.root, 'layout.json'), layout)

    def change_cover(self):
        self.sigs['Cover'] = fake_fusion.sig_of('cover v2')
        self.answer()

    def test_a_unit_that_hangs_in_a_batch_costs_only_itself(self):
        self.gate_run()
        self.change_cover()
        self.fusion.set_timeout('fusion_openings', COVER, alive_after=True)          # it never answers, Fusion stays alive
        code, out, err, sent = self.gate_run()
        plan = gate_plan.load_plan(self.root)
        by = {u['probe']: u for u in plan['units'] if u['part'] == COVER}
        self.assertEqual((by['fusion_inserts']['state'], by['fusion_thin']['state']), ('done', 'done'))
        self.assertEqual(by['fusion_openings']['over_budget'], 'not run')                 # twice no answer: given up, said
        self.assertEqual(len(self.fusion.calls_of('fusion_thin', COVER)), 2)             # the first run, then alone: the batch never reached it

    def test_a_batch_fusion_stops_answering_in_is_read_back_unit_by_unit(self):
        self.gate_run()
        self.change_cover()
        self.fusion.set_timeout('fusion_openings', COVER, alive_after=False)         # Fusion hangs in the second unit of the batch
        code, out, err, sent = self.gate_run()
        self.assertEqual(code, 4)
        inserts = len(self.fusion.calls_of('fusion_inserts', COVER))
        self.fusion.clear('fusion_openings', COVER); self.fusion.alive_after_timeout = True; self.answer()
        code, out, err, sent = self.gate_run()
        self.assertEqual(len(self.fusion.calls_of('fusion_inserts', COVER)), inserts)  # its result file was there: adopted, not sent again
        self.assertIn(code, (0, 2), out)                                   # nothing open and nothing left: only the questions of a changed part
        stored = gate_common.load_facts(self.root, COVER, gate_common.load_census(self.root)['parts'][COVER]['sig'])['probes']
        self.assertEqual(sorted(p for p, e in stored.items() if e.get('done') and not e.get('error')), sorted(fake_fusion.DEMO_PROBES))
        self.assertIn('late answer', ' '.join(stored['fusion_inserts'].get('notes') or []))


for _name in [n for n in vars(D.Deps) if n.startswith('test_')]:
    setattr(Plan, _name, None)                          # Plan shares the model of Deps, not its tests


if __name__ == '__main__':
    unittest.main()
