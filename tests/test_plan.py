"""gate_plan and gate_fusion_rpc: units, the plan file, the wall-time budget, the cursor, discovery, judging, verdicts.

No Fusion: tests/fake_fusion.py is the transport, and its clock is the wall time, so a test says how long a unit takes.
The rules are the small demo registry of fake_fusion.py, so nothing here depends on the shipped rules or judges."""
import http.server, json, os, sys, threading, unittest
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_fusion, gate_common, gate_features, gate_fusion_rpc, gate_judges, gate_params, gate_plan, gate_report, gate_stamp, gate_vendor
from gate_common import GateError

BRACKET, COVER = 'demo/bracket', 'demo/cover'
POCKET = gate_features.anchor('insert_pocket', (0, 0, 1), (12, -30, 40))
THIN = {'pockets': [{'anchor': POCKET, 'wall': 2.5, 'by': []}]}


class Planned(unittest.TestCase):
    """A demo project whose document was scanned: the census holds Bracket and Cover."""
    PARTS = {'Bracket': 'v1', 'Cover': 'v1'}
    QUESTIONS = ()

    def setUp(self):
        self.demo = fake_fusion.DemoProject(self, part_questions=self.QUESTIONS)
        self.root, self.fusion = self.demo.root, self.demo.fusion
        self.sigs = self.demo.model(self.PARTS)
        self.reg = self.demo.registry()
        self.resolved = gate_params.resolve(self.reg, {}, {'nozzle': 0.6, 'line_width': 0.62, 'material': 'PETG'})
        res = gate_plan.scan(self.root, wall_s=600)
        self.assertTrue(res['done'])
        self.fusion.calls.clear(); self.fusion.read_only.clear(); self.fusion.now = 0.0

    def plan(self, parts=(BRACKET,), **kw):
        return gate_plan.new_plan(self.root, self.reg, list(parts), **kw)

    def run_units(self, plan, wall_s=600):
        return gate_plan.run_units(self.root, self.reg, self.resolved, plan, wall_s=wall_s)

    def checked(self, part=BRACKET, **kw):
        """Run every unit of one part, judge it and return its verdict."""
        self.run_units(self.plan([part]))
        gate_plan.judge_part(self.root, self.reg, self.resolved, part, **kw)
        return gate_plan.part_verdict(self.root, self.reg, part)

    def stamp(self, part):
        gate_common.write_json(gate_common.stamp_path(self.root, part, self.sigs[part]), {'part': part, 'sig': self.sigs[part], 'verdict': 'pass'})


class Units(Planned):
    def test_units_of_a_part_come_from_the_rules_census_first(self):
        entry = gate_common.load_census(self.root)['parts'][BRACKET]
        units = gate_plan.units_for_part(self.reg, BRACKET, entry)
        self.assertEqual([u['probe'] for u in units], ['fusion_census', 'fusion_inserts', 'fusion_openings', 'fusion_thin'])
        self.assertEqual({(u['state'], u['cursor'], u['attempts'], u['error'], u['sig'], u['doc']) for u in units}, {('todo', None, 0, None, self.sigs[BRACKET], 'demo')})
        self.assertNotIn('fusion_scan', [u['probe'] for u in units])
        self.assertEqual(gate_plan.units_for_part(self.reg, 'demo/shim', {'doc': 'demo', 'source': 'generated', 'sig': 'ab' * 20}), [])

    def test_a_rule_may_give_its_probe_a_smaller_budget(self):
        self.reg['rules']['walls.thin_faces']['check']['budget_s'] = 5
        units = gate_plan.units_for_part(self.reg, BRACKET, gate_common.load_census(self.root)['parts'][BRACKET])
        self.assertEqual({u['probe']: u['budget_s'] for u in units}['fusion_thin'], 5.0)
        self.assertEqual({u['probe']: u['budget_s'] for u in units}['fusion_census'], 20.0)

    def test_a_plan_is_a_file_and_the_current_one_is_named(self):
        plan = self.plan([BRACKET, COVER], intent=[BRACKET])
        self.assertEqual([u['id'] for u in plan['units']], ['u-%04d' % i for i in range(1, 9)])
        self.assertEqual(plan['units'][0]['out'], 'gate/run/%s/units/u-0001.json' % plan['run'])
        self.assertEqual((plan['command'], plan['audit'], plan['intent'], plan['parts']), ('run', False, [BRACKET], [BRACKET, COVER]))
        self.assertEqual(gate_plan.load_plan(self.root), plan)
        self.assertEqual(gate_plan.load_plan(self.root, plan['run'])['run'], plan['run'])
        self.assertEqual(gate_plan.progress(plan), (0, 8))
        self.assertIsNone(gate_plan.load_plan(self.root, 'no-such-run'))

    def test_a_part_that_was_not_discovered_cannot_be_planned(self):
        with self.assertRaises(GateError) as e: self.plan(['demo/typed_by_hand'])
        self.assertIn('discovered, not typed', str(e.exception))

    def test_every_design_unit_is_sent_read_only_and_the_export_unit_is_not(self):
        self.run_units(self.plan())
        self.assertEqual(self.fusion.read_only, [True] * 4)
        job = gate_fusion_rpc.new_job('u-0001', 'r', 'fusion_export', 'Demo Assembly', 'demo', os.path.join(self.root, 'o.json'))
        self.assertFalse(gate_fusion_rpc.read_only_for(job)); self.assertFalse(gate_fusion_rpc.read_only_for(gate_fusion_rpc.build_script(job)))
        self.assertTrue(gate_fusion_rpc.read_only_for('print(1)'))
        body = gate_fusion_rpc.request_body('X', read_only=False)
        self.assertEqual(body['params'], {'name': 'fusion_mcp_execute', 'arguments': {'featureType': 'script', 'object': {'script': 'X', 'readOnly': False}}})

    def test_the_job_carries_what_the_probe_needs(self):
        self.demo.facts('fusion_census', BRACKET, {'holes': [{'id': 'h1', 'anchor': POCKET, 'd': 4.4, 'class': {'role': 'insert_pocket'}}], 'openings': [], 'counts': {}})
        self.run_units(self.plan())
        census, inserts, thin = (self.fusion.calls_of(p, BRACKET)[0] for p in ('fusion_census', 'fusion_inserts', 'fusion_thin'))
        self.assertIsNone(census['features'])
        self.assertEqual(inserts['features']['holes'][0]['id'], 'h1'); self.assertEqual(inserts['features']['openings'], [])
        self.assertEqual((inserts['doc'], inserts['doc_key'], inserts['part'], inserts['sig'], inserts['body'], inserts['occurrence']),
                         ('Demo Assembly', 'demo', BRACKET, self.sigs[BRACKET], 'Body1', 'Bracket:1'))
        self.assertEqual(inserts['params']['inserts.wall_target'], 4.0); self.assertEqual(inserts['not_printed'], ['^ref '])
        self.assertEqual(inserts['deadline_s'], 20.0); self.assertTrue(os.path.isabs(inserts['out']))
        self.assertEqual(thin['args'], {'width_cap': 2.0})
        self.assertEqual(inserts['target'], {'nozzle': 0.6, 'line_width': 0.62, 'material': 'PETG'})


class WallTime(Planned):
    def test_a_unit_is_not_started_when_less_than_its_budget_is_left(self):
        for probe in fake_fusion.DEMO_PROBES:
            self.demo.facts(probe, BRACKET, fake_fusion.CLEAN[probe], elapsed_s=12.0)
        plan = self.plan()
        res = self.run_units(plan, wall_s=20)                           # 12 s used, 8 left: under the 20 s budget of the next unit
        self.assertEqual((res['done'], res['total'], res['more']), (1, 4, True)); self.assertEqual(len(self.fusion.calls), 1)
        res = self.run_units(gate_plan.load_plan(self.root), wall_s=40)   # 12 s, 28 left: one more; then 16 are left: stop
        self.assertEqual((res['done'], res['more']), (3, True)); self.assertEqual(len(self.fusion.calls), 3)
        res = self.run_units(gate_plan.load_plan(self.root), wall_s=20)
        self.assertEqual((res['done'], res['total'], res['more']), (4, 4, False)); self.assertEqual(len(self.fusion.calls), 4)
        self.assertEqual([u['attempts'] for u in gate_plan.load_plan(self.root)['units']], [1, 1, 1, 1])              # no unit ran twice

    def test_quick_units_share_one_call(self):
        res = self.run_units(self.plan([BRACKET, COVER]), wall_s=600)   # 0.1 s each
        self.assertEqual((res['done'], res['total'], res['more']), (8, 8, False))

    def test_nothing_starts_in_a_wall_shorter_than_the_budget(self):
        res = self.run_units(self.plan(), wall_s=5)
        self.assertEqual((res['done'], res['more']), (0, True)); self.assertEqual(self.fusion.calls, [])

    def test_the_plan_is_saved_after_every_unit(self):
        plan = self.plan()
        self.demo.facts('fusion_inserts', BRACKET, fake_fusion.CLEAN['fusion_inserts'], elapsed_s=30.0)
        self.run_units(plan, wall_s=40)
        states = [u['state'] for u in gate_plan.load_plan(self.root)['units']]
        self.assertEqual(states, ['done', 'done', 'todo', 'todo'])


class Cursor(Planned):
    def test_a_unit_that_needs_three_calls_keeps_its_cursor_and_its_facts_add_up(self):
        p = [{'anchor': gate_features.anchor('insert_pocket', (0, 0, 1), (x, 0, 5)), 'wall': 2.0 + x, 'by': []} for x in (0, 1, 2)]
        self.fusion.clear('fusion_inserts', BRACKET)
        self.fusion.set_facts('fusion_inserts', BRACKET, {'pockets': [p[0]]}, done=False, cursor=1, elapsed_s=15.0)
        self.fusion.set_facts('fusion_inserts', BRACKET, {'pockets': [p[1]]}, done=False, cursor=2, elapsed_s=15.0)
        self.fusion.set_facts('fusion_inserts', BRACKET, {'pockets': [p[2]]}, done=True, elapsed_s=15.0)
        plan = self.plan()
        self.run_units(plan, wall_s=20.2)                               # census (0.1 s), then the first call of the inserts unit
        unit = gate_plan.load_plan(self.root)['units'][1]
        self.assertEqual((unit['state'], unit['cursor'], unit['attempts']), ('partial', 1, 1))
        self.assertNotIn('fusion_inserts', gate_common.load_facts(self.root, BRACKET, self.sigs[BRACKET])['probes'])      # only finished units are stored
        self.run_units(gate_plan.load_plan(self.root), wall_s=20)
        self.assertEqual(gate_plan.load_plan(self.root)['units'][1]['cursor'], 2)
        res = self.run_units(gate_plan.load_plan(self.root), wall_s=600)
        self.assertFalse(res['more'])
        self.assertEqual([j['cursor'] for j in self.fusion.calls_of('fusion_inserts', BRACKET)], [None, 1, 2])
        stored = gate_common.load_facts(self.root, BRACKET, self.sigs[BRACKET])['probes']['fusion_inserts']
        self.assertEqual(stored['facts']['pockets'], p); self.assertTrue(stored['done'])
        self.assertFalse([n for n in os.listdir(os.path.join(gate_common.run_dir(self.root, plan['run']), 'units')) if 'partial' in n])
        found = gate_plan.judge_part(self.root, self.reg, self.resolved, BRACKET)['inserts.wall']
        self.assertEqual(sorted(f['measure'] for f in found), [2.0, 3.0])       # the rows of all three calls, summed

    def test_a_probe_that_makes_no_progress_ends_as_an_error_not_as_a_loop(self):
        self.fusion.clear('fusion_thin', BRACKET)
        self.fusion.set_facts('fusion_thin', BRACKET, {}, done=False, cursor=None)
        res = self.run_units(self.plan())
        unit = gate_plan.load_plan(self.root)['units'][3]
        self.assertEqual((res['more'], unit['state']), (False, 'error')); self.assertIn('no progress', unit['error'])
        self.assertEqual(len(self.fusion.calls_of('fusion_thin')), gate_plan.MAX_ATTEMPTS)

    def test_a_body_that_is_still_being_signed_is_progress_and_never_a_fact(self):
        # a large body: the unit answers "still signing" until every face is in (probes/_fusion.py), then it measures
        self.fusion.clear('fusion_thin', BRACKET)
        for done in (50, 100, 170):
            self.fusion.set_facts('fusion_thin', BRACKET, {'signing': {'faces_done': done, 'faces': 170}}, done=False, cursor=None)
        self.fusion.set_facts('fusion_thin', BRACKET, {'faces': [], 'width_cap': 2.0, 'lumps': 1, 'shells': 1})
        res = self.run_units(self.plan(), wall_s=600)
        unit = gate_plan.load_plan(self.root)['units'][3]
        self.assertEqual((res['more'], unit['state'], unit['error']), (False, 'done', None))                # the same cursor four times, and no "no progress"
        self.assertEqual(len(self.fusion.calls_of('fusion_thin')), 4)
        stored = gate_common.load_facts(self.root, BRACKET, self.sigs[BRACKET])['probes']['fusion_thin']['facts']
        self.assertNotIn('signing', stored); self.assertEqual(stored['faces'], [])                           # what was said on the way is not kept as a fact

    def test_a_signature_that_does_not_move_on_ends_as_an_error(self):
        self.fusion.clear('fusion_thin', BRACKET)
        self.fusion.set_facts('fusion_thin', BRACKET, {'signing': {'faces_done': 50, 'faces': 170}}, done=False, cursor=None)       # the same answer again and again
        res = self.run_units(self.plan(), wall_s=600)
        unit = gate_plan.load_plan(self.root)['units'][3]
        self.assertEqual((res['more'], unit['state']), (False, 'error')); self.assertIn('the signature of the body did not move on (50 of 170 faces)', unit['error'])
        self.assertEqual(len(self.fusion.calls_of('fusion_thin')), gate_plan.MAX_ATTEMPTS + 1)               # the first call moved on, then three stood still


class Trouble(Planned):
    def test_a_probe_error_is_tried_twice_and_leaves_its_rule_not_run(self):
        gate_common.save_probe_facts(self.root, BRACKET, self.sigs[BRACKET], {'probe': 'fusion_thin', 'facts': {'faces': []}, 'done': True, 'error': None})   # an older, clean result
        self.fusion.clear('fusion_thin', BRACKET); self.fusion.set_error('fusion_thin', BRACKET, 'RuntimeError: boom at face 7\nTraceback ...')
        res = self.run_units(self.plan())
        unit = gate_plan.load_plan(self.root)['units'][3]
        self.assertEqual((unit['state'], unit['attempts'], unit['error']), ('error', 2, 'RuntimeError: boom at face 7'))
        self.assertEqual((res['done'], res['total'], res['more']), (4, 4, False))
        found = gate_plan.judge_part(self.root, self.reg, self.resolved, BRACKET)
        self.assertEqual([f['verdict'] for f in found['walls.thin_faces']], ['NOT_RUN'])      # the older clean facts do not stand in for a failed unit
        self.assertEqual(found['inserts.wall'], [])
        v = gate_plan.part_verdict(self.root, self.reg, BRACKET)
        self.assertEqual((v['verdict'], [f['verdict'] for f in v['open']]), ('incomplete', ['NOT_RUN']))      # nothing failed, and nothing has passed
        self.assertEqual(gate_plan.exit_code([v]), 1)                                         # open all the same: never a pass, never only a question round

    def test_no_facts_set_is_an_error_too(self):
        self.fusion.clear('fusion_inserts', BRACKET)
        self.run_units(self.plan())
        self.assertIn('no facts set', gate_plan.load_plan(self.root)['units'][1]['error'])

    def test_without_a_census_the_units_that_read_it_are_skipped(self):
        self.fusion.clear('fusion_census', BRACKET); self.fusion.set_error('fusion_census', BRACKET, 'ValueError: no faces')
        self.run_units(self.plan())
        units = {u['probe']: u for u in gate_plan.load_plan(self.root)['units']}
        self.assertEqual({p: u['state'] for p, u in units.items()}, {'fusion_census': 'error', 'fusion_inserts': 'skipped', 'fusion_openings': 'skipped', 'fusion_thin': 'done'})
        self.assertEqual(self.fusion.calls_of('fusion_inserts'), [])
        found = gate_plan.judge_part(self.root, self.reg, self.resolved, BRACKET)
        self.assertEqual([f['verdict'] for f in found['inserts.wall']], ['NOT_RUN'])

    def test_a_part_that_changed_under_the_run_is_skipped_and_named(self):
        self.demo.facts('fusion_inserts', BRACKET, THIN, sig=fake_fusion.sig_of('Bracket edited meanwhile'))
        res = self.run_units(self.plan([BRACKET, COVER]))
        self.assertEqual(res['changed_under_run'], [BRACKET])
        states = [(u['part'], u['probe'], u['state']) for u in gate_plan.load_plan(self.root)['units']]
        self.assertEqual([s for p, _, s in states if p == BRACKET], ['done', 'skipped', 'skipped', 'skipped'])
        self.assertEqual({s for p, _, s in states if p == COVER}, {'done'})
        self.assertNotIn('fusion_inserts', gate_common.load_facts(self.root, BRACKET, self.sigs[BRACKET])['probes'])

    def test_a_timeout_with_a_dead_ping_stops_everything_after_one_call(self):
        self.fusion.set_timeout('fusion_census', BRACKET, alive_after=False)
        plan = self.plan([BRACKET, COVER])
        with self.assertRaises(gate_fusion_rpc.FusionDown) as e: self.run_units(plan)
        self.assertIn('nothing else was sent', str(e.exception))
        self.assertEqual((len(self.fusion.calls), self.fusion.pings), (1, 1))                 # one call, one ping, no second call
        saved = gate_plan.load_plan(self.root)
        self.assertEqual((saved['units'][0]['state'], saved['units'][0]['attempts']), ('todo', 1))
        self.assertFalse(os.path.exists(gate_plan._lock_path(self.root)))                    # the lock is given back

    def test_a_timeout_with_a_live_ping_is_tried_once_more(self):
        self.fusion.set_timeout('fusion_thin', BRACKET, alive_after=True)
        res = self.run_units(self.plan(), wall_s=600)
        unit = gate_plan.load_plan(self.root)['units'][3]
        self.assertEqual((unit['state'], unit['attempts'], unit['timeouts'], unit['over_budget']), ('error', 2, 2, 'not run'))
        self.assertTrue(unit['error'].startswith('over budget, not run: 2 calls in a row got no answer within 30 s and left no result'), unit['error'])
        self.assertEqual((len(self.fusion.calls_of('fusion_thin')), self.fusion.pings, res['more']), (2, 2, False))
        self.assertAlmostEqual(self.fusion.now, 0.3 + 2 * 30)                                # each timeout cost budget + 10 s

    def test_no_server_at_all(self):
        self.fusion.down = True
        with self.assertRaises(gate_fusion_rpc.FusionDown): self.run_units(self.plan())
        self.assertEqual(self.fusion.calls, [])

    def test_a_document_that_is_not_open_makes_its_parts_unreachable(self):
        self.stamp(COVER)
        before = self.demo.read('gate', 'stamps', 'demo', 'cover', gate_common.sig12(self.sigs[COVER]) + '.json')
        del self.fusion.documents['Demo Assembly']
        res = self.run_units(self.plan([BRACKET, COVER]))
        self.assertEqual((res['unreachable'], res['more'], res['done']), (['demo'], False, 8))
        self.assertEqual({u['state'] for u in gate_plan.load_plan(self.root)['units']}, {'skipped'})
        self.assertEqual(len(self.fusion.calls), 1)                                          # one call found out; no other unit was sent
        self.assertFalse(os.path.exists(gate_common.facts_path(self.root, BRACKET, self.sigs[BRACKET])))
        self.assertEqual(self.demo.read('gate', 'stamps', 'demo', 'cover', gate_common.sig12(self.sigs[COVER]) + '.json'), before)
        self.assertFalse(gate_common.load_census(self.root)['docs']['demo']['open'])
        v = gate_plan.part_verdict(self.root, self.reg, BRACKET)
        self.assertEqual((v['verdict'], v['state']), ('unreachable', 'unreachable'))
        self.assertEqual(gate_plan.exit_code([v]), 0)                                        # the caller decides what an unreachable part means


class Lock(Planned):
    def test_one_run_at_a_time(self):
        path = gate_plan._lock_path(self.root)
        gate_plan.acquire_lock(self.root); gate_plan.acquire_lock(self.root)                 # the same process may take it again
        self.assertEqual(self.demo.read('gate', 'run', 'lock')['pid'], os.getpid())
        gate_plan.release_lock(self.root); self.assertTrue(os.path.exists(path))
        gate_plan.release_lock(self.root); self.assertFalse(os.path.exists(path))
        gate_common.write_json(path, {'pid': os.getppid(), 'time': gate_common.now()})       # a live other process
        with self.assertRaises(GateError) as e: gate_plan.acquire_lock(self.root)
        self.assertIn('one run at a time', str(e.exception))
        old = os.path.getmtime(path) - gate_plan.LOCK_MAX_AGE_S - 5
        os.utime(path, (old, old))                                                           # older than ten minutes: taken over
        gate_plan.acquire_lock(self.root)
        self.assertEqual(self.demo.read('gate', 'run', 'lock')['pid'], os.getpid())
        gate_plan.release_lock(self.root)

    @unittest.skipIf(os.name == 'nt', 'a dead pid cannot be told from a live one without ending it')
    def test_the_lock_of_a_dead_process_is_taken_over(self):
        gate_common.write_json(gate_plan._lock_path(self.root), {'pid': 2 ** 22 + 12345, 'time': gate_common.now()})
        gate_plan.acquire_lock(self.root)
        self.assertEqual(gate_common.read_json(gate_plan._lock_path(self.root))['pid'], os.getpid())       # the lock is this process's now
        gate_plan.release_lock(self.root)
        self.assertFalse(os.path.exists(gate_plan._lock_path(self.root)))

    def test_units_cannot_run_while_another_process_holds_the_lock(self):
        gate_common.write_json(gate_plan._lock_path(self.root), {'pid': os.getppid(), 'time': gate_common.now()})
        with self.assertRaises(GateError): self.run_units(self.plan())
        self.assertEqual(self.fusion.calls, [])


class Scan(unittest.TestCase):
    def setUp(self):
        self.demo = fake_fusion.DemoProject(self, part_questions=())
        self.root, self.fusion = self.demo.root, self.demo.fusion

    def scan(self, **kw):
        return gate_plan.scan(self.root, **dict({'wall_s': 600}, **kw))

    def test_parts_are_discovered_with_their_signature(self):
        sigs = self.demo.model({'Motor plate': 'v1', 'Cover': 'v1'})
        res = self.scan()
        self.assertEqual((res['done'], res['new'], res['changed'], res['gone'], res['unreachable']), (True, ['demo/cover', 'demo/motor_plate'], [], [], []))
        e = gate_common.load_census(self.root)['parts']['demo/motor_plate']
        self.assertEqual((e['doc'], e['name'], e['source'], e['sig'], e['prev_sig'], e['stl'], e['gone'], e['body'], e['occurrence']),
                         ('demo', 'Motor plate', 'fusion', sigs['demo/motor_plate'], None, 'stl/motor_plate.stl', False, 'Body1', 'Motor plate:1'))
        doc = gate_common.load_census(self.root)['docs']['demo']
        self.assertEqual((doc['name'], doc['doc_version'], doc['open']), ('Demo Assembly', 12, True))
        job = self.fusion.calls_of('fusion_scan')[0]
        self.assertEqual((job['part'], job['sig'], job['not_printed'], job['doc']), (None, None, ['^ref '], 'Demo Assembly'))
        self.assertEqual(gate_plan.changed_parts(self.root), ['demo/cover', 'demo/motor_plate'])

    def test_changed_gone_and_back(self):
        first = self.demo.model({'Bracket': 'v1', 'Cover': 'v1'}); self.scan()
        second = self.demo.model({'Bracket': 'v2'})
        res = self.scan()
        self.assertEqual((res['new'], res['changed'], res['gone']), ([], ['demo/bracket'], ['demo/cover']))
        parts = gate_common.load_census(self.root)['parts']
        self.assertEqual((parts['demo/bracket']['sig'], parts['demo/bracket']['prev_sig']), (second['demo/bracket'], first['demo/bracket']))
        self.assertTrue(parts['demo/cover']['gone']); self.assertEqual(gate_plan.changed_parts(self.root), ['demo/bracket'])
        self.demo.model({'Bracket': 'v2', 'Cover': 'v1'})
        res = self.scan()
        self.assertEqual((res['new'], res['changed'], res['gone']), (['demo/cover'], [], []))
        self.assertFalse(gate_common.load_census(self.root)['parts']['demo/cover']['gone'])

    def names(self, stl_names, **doc):
        layout = dict(self.demo.layout, documents=[dict(self.demo.layout['documents'][0], stl_names=stl_names, **doc)])
        gate_common.write_json(gate_common.gate_path(self.root, 'layout.json'), layout)

    def stls(self):
        return {pid: e['stl'] for pid, e in gate_common.load_census(self.root)['parts'].items() if not e.get('gone')}

    def test_stl_names_give_the_file_by_part_name_and_by_slug(self):
        self.demo.model({'Motor plate': 'v1', 'Cover': 'v1', 'Bracket': 'v1'})
        self.scan()
        self.assertEqual(self.stls(), {'demo/bracket': 'stl/bracket.stl', 'demo/cover': 'stl/cover.stl', 'demo/motor_plate': 'stl/motor_plate.stl'})       # no map: the slug
        self.names({'Motor plate': 'MotorPlate_rev3.stl', 'cover': 'lid.stl'})
        self.assertEqual(self.stls()['demo/cover'], 'stl/cover.stl')                       # the map takes effect at the next scan, not before
        res = self.scan()
        self.assertEqual((res['clashes'], res['new'], res['changed'], res['gone']), ([], [], [], []))       # another file name is not another geometry
        self.assertEqual(self.stls(), {'demo/bracket': 'stl/bracket.stl', 'demo/cover': 'stl/lid.stl', 'demo/motor_plate': 'stl/MotorPlate_rev3.stl'})
        self.names({})
        self.scan()
        self.assertEqual(self.stls()['demo/motor_plate'], 'stl/motor_plate.stl')           # the entry was taken out: the default again

    def test_a_mapped_file_that_is_the_default_file_of_another_part_is_a_clash(self):
        sigs = self.demo.model({'Bracket': 'v1', 'Cover': 'v1'}); self.scan()
        stamp = fake_fusion.whole_stamp(self.root, 'demo/bracket', sigs['demo/bracket'])           # the part is known and stamped
        first_seen = gate_common.load_census(self.root)['parts']['demo/bracket']['first_seen']
        for key in ('Bracket', 'bracket'):                                                # by name and by slug
            self.names({key: 'Cover.stl'})                                                # stl/cover.stl is where the part Cover is exported to
            res = self.scan()
            self.assertEqual(len(res['clashes']), 1, res['clashes'])
            self.assertIn('demo/bracket: "stl_names" of document \'demo\' gives it the file Cover.stl, which is the STL of the part demo/cover', res['clashes'][0])
            self.assertIn('the part keeps stl/bracket.stl until the map is changed', res['clashes'][0])
            # a wrong map is not "the part left the document": it stays, with the file it had, never two parts in one file
            self.assertEqual((res['gone'], res['new'], res['changed'], res['stl_moved']), ([], [], [], []))
            self.assertEqual(self.stls(), {'demo/bracket': 'stl/bracket.stl', 'demo/cover': 'stl/cover.stl'})
            e = gate_common.load_census(self.root)['parts']['demo/bracket']
            self.assertEqual((e['gone'], e['sig'], e['first_seen']), (False, sigs['demo/bracket'], first_seen))
            self.assertEqual(gate_stamp.verify(self.root, self.demo.registry(), stamp)[0], 'valid')        # its stamp is not "gone from its document"
            self.assertNotIn('demo/bracket', gate_plan.changed_parts(self.root))
            code, out, err = self.demo.gate('scan')
            self.assertEqual(code, 4); self.assertIn('NAME CLASH  demo/bracket: "stl_names"', out); self.assertNotIn('GONE', out); self.assertIn('scan: 2 parts', out)
            code, out, err = self.demo.gate('run', '--wall', '600')
            self.assertEqual(code, 4); self.assertIn('the scan did not hold', out + err)
            lines = gate_vendor.stl_name_lines(gate_common.load_layout(self.root), gate_common.load_census(self.root), self.root)
            self.assertEqual(len(lines), 1, lines)                                        # doctor: the map has to change; not "names no part", not "scan again"
            self.assertIn('demo/bracket: "stl_names" gives it the file Cover.stl, which is the STL of the part demo/cover', lines[0]); self.assertIn('Change the map', lines[0])
        self.names({'Bracket': 'bracket.stl', 'cover': 'lid.stl'})                        # its own default file is no clash
        res = self.scan()
        self.assertEqual((res['clashes'], self.stls()), ([], {'demo/bracket': 'stl/bracket.stl', 'demo/cover': 'stl/lid.stl'}))
        self.names({'Bracket': 'cover.stl', 'Cover': 'bracket.stl'})                      # a swap: each takes the name of the other
        res = self.scan()
        self.assertEqual(len(res['clashes']), 2, res['clashes']); self.assertEqual(res['gone'], [])
        for line in res['clashes']:                                                       # said as it is: the name is the other part's by default
            self.assertIn('which is the default STL name of the part', line); self.assertNotIn('which is the STL of the part', line)
        self.assertEqual(self.stls(), {'demo/bracket': 'stl/bracket.stl', 'demo/cover': 'stl/lid.stl'})      # both keep what they had
        self.names({})
        self.assertEqual((self.scan()['clashes'], self.stls()), ([], {'demo/bracket': 'stl/bracket.stl', 'demo/cover': 'stl/cover.stl'}))

    def test_a_new_part_with_a_clashing_map_gets_its_default_file(self):
        self.names({'Bracket': 'cover.stl'})
        self.demo.model({'Bracket': 'v1', 'Cover': 'v1'})
        res = self.scan()
        self.assertEqual((len(res['clashes']), res['new']), (1, ['demo/bracket', 'demo/cover']))
        self.assertEqual(self.stls(), {'demo/bracket': 'stl/bracket.stl', 'demo/cover': 'stl/cover.stl'})

    def test_one_file_for_two_parts_is_a_clash_whatever_the_reason(self):
        """A file two parts name as their STL: an export of one overwrites the other. Seen at the end of every scan, over
        the whole census and the generators: a generator beside a Fusion part, two documents with one STL folder."""
        os.makedirs(self.demo.path('stl'))
        with open(self.demo.path('stl', 'gear.stl'), 'wb') as f: f.write(fake_fusion.cube_stl(5.0))
        gen = {'part': 'demo/gear_made', 'stl': 'stl/Gear.stl', 'source': 'tools/make_gear.py'}
        gate_common.write_json(gate_common.gate_path(self.root, 'layout.json'), dict(self.demo.layout, generators=[gen]))
        self.demo.model({'Gear': 'v1', 'Cover': 'v1'})                                    # the Fusion part Gear is exported to stl/gear.stl by default
        res = self.scan()
        self.assertEqual(len(res['clashes']), 1, res['clashes'])
        self.assertIn('the parts demo/gear and demo/gear_made name this one file as their STL', res['clashes'][0]); self.assertIn('stl/', res['clashes'][0])
        code, out, err = self.demo.gate('scan')
        self.assertEqual(code, 4); self.assertIn('NAME CLASH  stl/', out)
        code, out = self.doctor()
        self.assertIn('is the STL of demo/gear and demo/gear_made: an export of one overwrites the other', out)
        gate_common.write_json(gate_common.gate_path(self.root, 'layout.json'), dict(self.demo.layout, generators=[dict(gen, stl='stl/gear_made.stl')]))
        self.assertEqual(self.scan()['clashes'], [])
        # a map value that is a generator's STL never reaches a scan: the layout is refused
        self.names({'Cover': 'GEAR_made.stl'}); layout = gate_common.read_json(gate_common.gate_path(self.root, 'layout.json'))
        gate_common.write_json(gate_common.gate_path(self.root, 'layout.json'), dict(layout, generators=[dict(gen, stl='stl/gear_made.stl')]))
        with self.assertRaises(GateError) as e: self.scan()
        self.assertIn("'Cover' -> 'GEAR_made.stl' is the STL of the generator of demo/gear_made", str(e.exception))
        sent = len(self.fusion.calls)
        for argv in (['scan'], ['run', '--wall', '600'], ['status']):
            code, out, err = self.demo.gate(*argv)
            self.assertEqual(code, 4, argv); self.assertIn('is the STL of the generator', out + err)
        self.assertEqual(len(self.fusion.calls), sent)

    def test_a_mapped_file_that_is_a_part_of_another_document_is_a_clash(self):
        other = {'key': 'shade', 'name': 'Shade Assembly', 'stl_dir': 'stl'}              # two documents, one STL folder
        self.fusion.documents['Shade Assembly'] = 3
        gate_common.write_json(gate_common.gate_path(self.root, 'layout.json'), dict(self.demo.layout, documents=self.demo.layout['documents'] + [other]))
        self.demo.model({'Lamp foot': 'v1'}); self.assertEqual(self.scan(doc_key='demo')['clashes'], [])
        gate_common.write_json(gate_common.gate_path(self.root, 'layout.json'),
                               dict(self.demo.layout, documents=self.demo.layout['documents'] + [dict(other, stl_names={'Shade ring': 'lamp_foot.stl'})]))
        self.demo.model({'Shade ring': 'v1'}, doc_key='shade')
        res = self.scan(doc_key='shade')
        self.assertEqual(len(res['clashes']), 1, res['clashes'])
        self.assertIn('shade/shade_ring: "stl_names" of document \'shade\' gives it the file lamp_foot.stl, which is the STL of the part demo/lamp_foot', res['clashes'][0])
        self.assertEqual(self.stls(), {'demo/lamp_foot': 'stl/lamp_foot.stl', 'shade/shade_ring': 'stl/shade_ring.stl'})
        self.demo.model({'Lamp foot': 'v1'}, doc_key='shade')                             # and with no map at all: the same slug in both documents
        res = self.scan(doc_key='shade')
        self.assertEqual(len(res['clashes']), 1, res['clashes']); self.assertIn('the parts demo/lamp_foot and shade/lamp_foot name this one file', res['clashes'][0])

    def test_the_scan_says_when_a_part_gets_another_file_and_when_a_key_names_no_part(self):
        self.demo.model({'Motor plate': 'v1', 'Cover': 'v1'}); self.scan()
        os.makedirs(self.demo.path('stl'))
        for name in ('motor_plate.stl', 'cover.stl'):                                     # both were exported under the default name before the map
            with open(self.demo.path('stl', name), 'wb') as f: f.write(fake_fusion.cube_stl(5.0))
        self.names({'Motor plate': 'MotorPlate_rev3.stl', 'cover': 'Cover.stl', 'motor plate': 'typo.stl'})
        code, out, err = self.demo.gate('scan')
        self.assertEqual((code, err), (0, ''), out)
        self.assertIn('STL      demo/motor_plate: stl/motor_plate.stl -> stl/MotorPlate_rev3.stl (gate/layout.json): the next export writes the new file. '
                      'stl/motor_plate.stl stays in the folder: delete it once the new file is exported', out)
        line = next(l for l in out.splitlines() if l.startswith('STL      demo/cover'))    # the same file in another letter case is not a second file
        self.assertIn('stl/cover.stl -> stl/Cover.stl', line); self.assertNotIn('delete it', line)
        self.assertIn("STL NAME demo: the key 'motor plate' of \"stl_names\" names no part of this scan", out); self.assertIn('typo.stl is the STL of no part', out)
        self.assertEqual(out.count('STL NAME'), 1); self.assertNotIn('GONE', out); self.assertNotIn('NEW', out)
        code, out, err = self.demo.gate('scan')                                           # the move is said once, the dead key as long as it is there
        self.assertNotIn('STL      ', out); self.assertEqual(out.count('STL NAME'), 1)

    def doctor(self):
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf): code = gate_vendor.cmd_doctor(self.root, None, pinger=False)
        return code, buf.getvalue()

    def test_the_census_keeps_the_pose_of_a_part(self):
        # the matrix (part frame -> document frame) is what turns the bed face of the build table into the frame the holes are in
        self.demo.model({'Bracket': 'v1'}); self.scan()
        entry = gate_common.load_census(self.root)['parts']['demo/bracket']
        self.assertEqual(entry['matrix'], [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]); self.assertEqual(entry['bbox'], [[0, 0, 0], [40, 30, 10]])

    def test_a_part_with_a_stamp_for_its_signature_is_not_changed(self):
        sigs = self.demo.model({'Bracket': 'v1', 'Cover': 'v1'}); self.scan()
        fake_fusion.whole_stamp(self.root, 'demo/cover', sigs['demo/cover'])
        self.assertEqual(gate_plan.changed_parts(self.root), ['demo/bracket'])
        half = gate_common.stamp_path(self.root, 'demo/bracket', sigs['demo/bracket'])
        gate_common.write_json(half, {'part': 'demo/bracket', 'sig': sigs['demo/bracket']})         # a file that is no valid stamp is no stamp
        self.assertEqual(gate_plan.changed_parts(self.root), ['demo/bracket']); os.remove(half)
        self.demo.model({'Bracket': 'v1', 'Cover': 'v2'}); self.scan()
        self.assertEqual(gate_plan.changed_parts(self.root), ['demo/bracket', 'demo/cover'])   # the stamp died with the geometry

    def test_a_long_scan_goes_on_at_its_cursor_and_merges_only_when_complete(self):
        self.demo.model({'Old': 'v1'}); self.scan()
        self.fusion.clear('fusion_scan', None)
        self.fusion.set_facts('fusion_scan', None, {'parts': [fake_fusion.scan_part('Bracket', fake_fusion.sig_of('b'))], 'refs': []}, done=False, cursor=15, elapsed_s=18.0)
        self.fusion.set_facts('fusion_scan', None, {'parts': [fake_fusion.scan_part('Cover', fake_fusion.sig_of('c'))], 'refs': [{'name': 'ref screw'}]}, done=True, elapsed_s=18.0)
        res = self.scan(wall_s=20)
        self.assertEqual((res['done'], res['next'], res['progress'], res['new'], res['gone']), (False, ['demo'], (0, 1), [], []))
        self.assertEqual(sorted(gate_common.load_census(self.root)['parts']), ['demo/old'])            # half a list marks nothing as gone
        res = self.scan(wall_s=20)
        self.assertEqual((res['done'], res['new'], res['gone']), (True, ['demo/bracket', 'demo/cover'], ['demo/old']))
        self.assertEqual([j['cursor'] for j in self.fusion.calls_of('fusion_scan')][-2:], [None, 15])
        self.assertEqual(gate_common.load_census(self.root)['docs']['demo']['refs'], 1)

    def test_a_closed_document_is_unreachable_and_keeps_its_parts(self):
        self.demo.model({'Bracket': 'v1'}); self.scan()
        del self.fusion.documents['Demo Assembly']; self.fusion.documents['Other'] = 3
        res = self.scan()
        self.assertEqual((res['done'], res['unreachable'], res['gone'], res['errors']), (True, ['demo'], [], {}))
        census = gate_common.load_census(self.root)
        self.assertFalse(census['docs']['demo']['open']); self.assertFalse(census['parts']['demo/bracket']['gone'])

    def test_two_bodies_with_one_name_are_said_not_merged(self):
        self.fusion.set_facts('fusion_scan', None, {'parts': [fake_fusion.scan_part('Lid', fake_fusion.sig_of('a')), fake_fusion.scan_part('lid', fake_fusion.sig_of('b'))], 'refs': []})
        res = self.scan()
        self.assertEqual(len(res['clashes']), 1); self.assertIn('demo/lid', res['clashes'][0])
        self.assertEqual(gate_common.load_census(self.root)['parts']['demo/lid']['sig'], fake_fusion.sig_of('a'))

    def test_a_failed_scan_is_an_error_of_that_document(self):
        self.fusion.set_error('fusion_scan', None, 'RuntimeError: boom')
        res = self.scan()
        self.assertEqual(res['errors'], {'demo': 'RuntimeError: boom'}); self.assertEqual(gate_common.load_census(self.root)['parts'], {})

    def test_nothing_registered_is_an_error_and_an_unknown_document_too(self):
        gate_common.write_json(gate_common.gate_path(self.root, 'layout.json'), {})
        with self.assertRaises(GateError): self.scan()
        gate_common.write_json(gate_common.gate_path(self.root, 'layout.json'), self.demo.layout)
        with self.assertRaises(GateError): self.scan(doc_key='nope')

    def test_a_generator_of_the_layout_is_a_part_with_the_hash_of_its_stl(self):
        layout = dict(self.demo.layout, generators=[{'part': 'demo/shim_05', 'stl': 'stl/shim_05.stl', 'source': 'tools/make_shims.py'}])
        gate_common.write_json(gate_common.gate_path(self.root, 'layout.json'), layout)
        self.demo.model({'Bracket': 'v1'})
        res = self.scan()
        e = gate_common.load_census(self.root)['parts']['demo/shim_05']
        self.assertEqual((e['source'], e['generator'], e['sig'], e['stl']), ('generated', 'tools/make_shims.py', None, 'stl/shim_05.stl'))   # no STL yet: no signature
        self.assertIn('demo/shim_05', res['new']); self.assertIn('demo/shim_05', gate_plan.changed_parts(self.root))
        os.makedirs(self.demo.path('stl'))
        with open(self.demo.path('stl', 'shim_05.stl'), 'wb') as f: f.write(fake_fusion.cube_stl(5.0))
        res = self.scan()
        e = gate_common.load_census(self.root)['parts']['demo/shim_05']
        self.assertEqual(e['sig'], gate_common.sibling('meshid').stl_ids(self.demo.path('stl', 'shim_05.stl'))['geom_hash'])
        self.assertEqual(res['changed'], ['demo/shim_05'])
        self.assertEqual(gate_plan.new_plan(self.root, self.demo.registry(), ['demo/shim_05'])['units'], [])      # a part made by code has no Fusion unit

    def linked_model(self):
        """A lamp foot of this document and two bodies of a linked document "Turntable drive"."""
        part = fake_fusion.scan_part
        self.fusion.clear('fusion_scan', None)
        self.fusion.set_facts('fusion_scan', None, {'refs': [], 'parts': [
            part('Lamp foot', fake_fusion.sig_of('foot')),
            part('Drive housing', fake_fusion.sig_of('housing'), occurrence='Turntable drive:1+Drive housing:1', external=True, linked='Turntable drive'),
            part('Drive lid', fake_fusion.sig_of('lid'), occurrence='Turntable drive:1+Drive lid:1', external=True, linked='Turntable drive')]})

    def test_bodies_of_a_linked_document_are_parts_here_and_the_scan_says_how_many(self):
        self.linked_model()
        res = self.scan()
        census = gate_common.load_census(self.root)
        self.assertEqual(res['new'], ['demo/drive_housing', 'demo/drive_lid', 'demo/lamp_foot'])
        self.assertEqual(census['parts']['demo/drive_lid']['linked'], 'Turntable drive'); self.assertNotIn('linked', census['parts']['demo/lamp_foot'])
        self.assertEqual(census['docs']['demo']['linked'], {'Turntable drive': {'parts': 2, 'registered': None}})
        line, = [n for n in res['notes'] if n.startswith('LINKED')]
        self.assertIn("2 parts come from the linked document 'Turntable drive', which gate/layout.json does not register: they are judged here", line)

    def test_a_linked_document_that_is_registered_keeps_its_parts_to_itself(self):
        # judged once: in their own document. Here they are counted and named, not listed as parts and not as "gone" later
        layout = dict(self.demo.layout, documents=self.demo.layout['documents'] + [{'key': 'drive', 'name': 'Turntable drive', 'stl_dir': 'stl'}])
        gate_common.write_json(gate_common.gate_path(self.root, 'layout.json'), layout)
        self.linked_model()
        res = self.scan(doc_key='demo')
        census = gate_common.load_census(self.root)
        self.assertEqual((res['new'], sorted(census['parts'])), (['demo/lamp_foot'], ['demo/lamp_foot']))
        self.assertEqual(census['docs']['demo']['linked'], {'Turntable drive': {'parts': 2, 'registered': 'drive'}})
        line, = [n for n in res['notes'] if n.startswith('LINKED')]
        self.assertIn("2 printed bodies come from the linked document 'Turntable drive', registered as 'drive': they are judged there", line)
        self.assertEqual(self.scan(doc_key='demo')['gone'], [])

    def test_a_linked_body_whose_document_fusion_does_not_name_is_still_counted(self):
        self.fusion.set_facts('fusion_scan', None, {'refs': [], 'parts': [fake_fusion.scan_part('Drive lid', fake_fusion.sig_of('lid'), external=True, linked=None)]})
        res = self.scan()
        self.assertEqual(gate_common.load_census(self.root)['parts']['demo/drive_lid']['linked'], '?')
        self.assertIn('1 parts come from a linked document whose name Fusion did not give', [n for n in res['notes'] if n.startswith('LINKED')][0])

    def test_equal_bodies_in_one_component_are_named_as_probably_bought_and_stay_parts(self):
        part = fake_fusion.scan_part
        rollers = [part('Rollers / roller %d' % i, fake_fusion.sig_of('r%d' % i), component_id='c-rollers', volume=331.8, area=310.2, faces=4) for i in range(1, 6)]
        self.fusion.set_facts('fusion_scan', None, {'refs': [], 'parts': rollers + [part('Lamp foot', fake_fusion.sig_of('foot')), part('Lamp arm', fake_fusion.sig_of('arm'))]})
        with mock.patch.object(gate_plan, '_own_number', lambda root, name: {'fusion.bought_hint_same_bodies': 4}.get(name)):
            res = self.scan()
        self.assertEqual(len(res['new']), 7)                                                       # a hint leaves nothing out
        line, = [n for n in res['notes'] if n.startswith('HINT')]
        self.assertIn("5 parts are one of 4 or more equal bodies in one component", line); self.assertIn("5 x 'Rollers'", line); self.assertNotIn('Lamp', line)
        families = gate_plan.same_bodies(gate_common.load_census(self.root)['parts'], 6)
        self.assertEqual(families, {})                                                             # five equal bodies are not six
        self.assertEqual(gate_plan.same_bodies(gate_common.load_census(self.root)['parts'], None), {})     # no number, no hint

    def test_parts_in_no_motion_group_are_said_once_for_the_document(self):
        self.demo.model({'Lamp foot': 'v1', 'Lamp arm': 'v1', 'Shade': 'v1'})
        res = self.scan()
        line, = [n for n in res['notes'] if n.startswith('NO GROUP')]
        self.assertIn('declares no "motion_groups"', line); self.assertIn('none of its 3 parts', line)
        layout = dict(self.demo.layout)
        layout['documents'] = [dict(layout['documents'][0], motion_groups=[{'key': 'base', 'patterns': ['^Lamp foot']}, {'key': 'arm', 'patterns': ['^Lamp arm', '(']}])]
        gate_common.write_json(gate_common.gate_path(self.root, 'layout.json'), layout)
        res = self.scan()
        line, = [n for n in res['notes'] if n.startswith('NO GROUP')]
        self.assertIn('1 of 3 parts match no motion group', line); self.assertIn('demo/shade', line); self.assertNotIn('demo/lamp_arm', line)
        layout['documents'][0]['motion_groups'].append({'key': 'shade', 'patterns': ['^Shade']})
        gate_common.write_json(gate_common.gate_path(self.root, 'layout.json'), layout)
        self.assertEqual([n for n in self.scan()['notes'] if n.startswith('NO GROUP')], [])

    def test_the_notes_come_only_with_a_finished_scan_and_are_printed_before_the_total(self):
        import contextlib, io
        self.fusion.set_facts('fusion_scan', None, {'parts': [fake_fusion.scan_part('Lamp foot', fake_fusion.sig_of('f'))], 'refs': []}, done=False, cursor=3, elapsed_s=18.0)
        self.fusion.set_facts('fusion_scan', None, {'parts': [], 'refs': []}, done=True, elapsed_s=1.0)
        res = self.scan(wall_s=20)
        self.assertFalse(res['done']); self.assertNotIn('notes', res)
        res = self.scan(wall_s=20)
        out = io.StringIO()
        with contextlib.redirect_stdout(out): gate_plan._print_scan(res)
        lines = out.getvalue().strip().splitlines()
        self.assertTrue(lines[-2].startswith('NO GROUP demo:'), lines); self.assertTrue(lines[-1].startswith('scan: 1 parts, 1 new'), lines)


class Judging(Planned):
    def test_judgements_are_stored_with_the_judge_and_the_values_it_read(self):
        self.demo.facts('fusion_inserts', BRACKET, THIN)
        self.run_units(self.plan())
        found = gate_plan.judge_part(self.root, self.reg, self.resolved, BRACKET)
        self.assertEqual(sorted(found), ['access.hole_covered', 'census.unknown_hole', 'inserts.wall', 'regression.unintended', 'walls.thin_faces'])
        f = found['inserts.wall'][0]
        self.assertEqual((f['verdict'], f['measure'], f['limit'], f['part'], f['fix'][0]), ('GROW', 2.5, 4.0, BRACKET, 'Make the whole wall wider where the outline is free.'))
        j = gate_common.load_facts(self.root, BRACKET, self.sigs[BRACKET])['judged']['inserts.wall']
        self.assertEqual((j['judge_hash'], j['params']), (gate_judges.judge_hash('inserts_wall'), {'inserts.wall_target': 4.0}))

    def test_a_rule_of_another_stage_is_not_judged_at_design(self):
        self.run_units(self.plan())
        self.assertNotIn('mesh.closed', gate_plan.judge_part(self.root, self.reg, self.resolved, BRACKET))
        self.assertEqual(sorted(gate_plan.judge_part(self.root, self.reg, self.resolved, BRACKET, stage='export')), ['mesh.closed'])

    def test_the_judge_gets_the_census_and_the_facts_of_the_previous_signature(self):
        seen = {}

        def walls_thin_faces(facts, params, ctx):
            seen.update(prev=ctx['prev'], census=ctx['census'], intent=ctx['intent'], target=ctx['target'], tables=ctx['tables'])
            return []
        with mock.patch.dict(gate_judges.JUDGES, {'walls_thin_faces': walls_thin_faces}):
            self.demo.facts('fusion_thin', BRACKET, {'faces': [{'w': 1}]})
            self.checked(BRACKET, intent=[BRACKET], target={'nozzle': 0.6})
            self.assertIsNone(seen['prev']); self.assertEqual(seen['census'], fake_fusion.CLEAN['fusion_census'])
            self.assertEqual((seen['intent'], seen['target'], seen['tables']), ([BRACKET], {'nozzle': 0.6}, {}))
            self.sigs.update(self.demo.model({'Bracket': 'v2', 'Cover': 'v1'})); gate_plan.scan(self.root, wall_s=600)
            self.demo.facts('fusion_thin', BRACKET, {'faces': [{'w': 2}]})
            self.checked(BRACKET)
            self.assertEqual(seen['prev'], {'faces': [{'w': 1}]})
            self.sigs.update(self.demo.model({'Bracket': 'v3', 'Cover': 'v1'})); gate_plan.scan(self.root, wall_s=600)     # v2 -> v3 with no check between ...
            self.sigs.update(self.demo.model({'Bracket': 'v4', 'Cover': 'v1'})); gate_plan.scan(self.root, wall_s=600)     # ... and v4: prev_sig (v3) has no facts
            self.checked(BRACKET)
            self.assertEqual(seen['prev'], {'faces': [{'w': 2}]})                                # the newest checked geometry is the base

    def test_a_changed_part_outside_the_intent_is_a_finding_of_that_part(self):
        self.run_units(self.plan([BRACKET, COVER]))
        found = gate_plan.judge_part(self.root, self.reg, self.resolved, COVER, intent=[BRACKET])['regression.unintended']
        self.assertEqual([(f['verdict'], f['part']) for f in found], [('FAIL', COVER)])
        self.assertEqual(gate_plan.judge_part(self.root, self.reg, self.resolved, BRACKET, intent=[BRACKET])['regression.unintended'], [])
        self.assertEqual(gate_plan.judge_part(self.root, self.reg, self.resolved, COVER)['regression.unintended'], [])       # no intent stated: nothing to compare
        self.stamp(COVER)                                                                        # stamped at this signature: it did not change
        self.assertEqual(gate_plan.judge_part(self.root, self.reg, self.resolved, COVER, intent=[BRACKET])['regression.unintended'], [])

    def test_unknown_parts(self):
        with self.assertRaises(GateError): gate_plan.judge_part(self.root, self.reg, self.resolved, 'demo/nope')
        with self.assertRaises(GateError): gate_plan.part_verdict(self.root, self.reg, 'demo/nope')


class Verdict(Planned):
    def waive(self, finding, **over):
        w = dict({'id': 'W-1', 'rule': finding['rule'], 'part': finding['part'], 'anchor': finding['anchor'], 'context': finding['context'],
                  'reason': 'the bore of the bought bearing is 1.8 mm from the pocket and cannot move', 'quote': 'leave these two as they are', 'date': '2026-03-04'}, **over)
        gate_common.write_json(gate_common.gate_path(self.root, 'owner', 'waivers.json'), {'waivers': [w]})

    def test_nothing_judged_is_incomplete_never_a_pass(self):
        v = gate_plan.part_verdict(self.root, self.reg, BRACKET)
        self.assertEqual(v['verdict'], 'incomplete'); self.assertEqual(len(v['not_run']), 5)
        self.assertEqual(gate_plan.exit_code([v]), 1)

    def test_clean_facts_pass(self):
        v = self.checked()
        self.assertEqual((v['verdict'], v['open'], v['not_run'], v['attested'], v['state']), ('pass', [], [], 0, 'new'))
        self.assertEqual(sorted(k for k in v if k in ('part', 'sig', 'verdict', 'open', 'asked', 'answered', 'waived', 'info', 'not_run', 'waivers', 'questions', 'attested')),
                         sorted(('part', 'sig', 'verdict', 'open', 'asked', 'answered', 'waived', 'info', 'not_run', 'waivers', 'questions', 'attested')))

    def test_a_finding_fails_the_part_and_carries_its_id(self):
        self.demo.facts('fusion_inserts', BRACKET, THIN)
        v = self.checked()
        self.assertEqual((v['verdict'], len(v['open'])), ('fail', 1))
        self.assertEqual(v['open'][0]['id'], gate_features.finding_id(v['open'][0])); self.assertRegex(v['open'][0]['id'], '^F-[0-9a-f]{6}$')

    def test_a_waived_finding_passes(self):
        self.demo.facts('fusion_inserts', BRACKET, THIN)
        self.waive(self.checked()['open'][0])
        v = gate_plan.part_verdict(self.root, self.reg, BRACKET)
        self.assertEqual((v['verdict'], v['open'], [f['waiver'] for f in v['waived']], [w['state'] for w in v['waivers']]), ('pass', [], ['W-1'], ['valid']))

    def test_a_waiver_whose_context_changed_does_not_pass(self):
        self.demo.facts('fusion_inserts', BRACKET, THIN)
        self.waive(self.checked()['open'][0])
        self.sigs.update(self.demo.model({'Bracket': 'v2', 'Cover': 'v1'})); gate_plan.scan(self.root, wall_s=600)
        self.demo.facts('fusion_inserts', BRACKET, {'pockets': [{'anchor': POCKET, 'wall': 2.1, 'by': []}]})      # the same pocket, thinner now
        v = self.checked()
        self.assertEqual((v['verdict'], len(v['open']), v['waived'], [w['state'] for w in v['waivers']]), ('fail', 1, [], ['expired']))

    def test_a_waiver_survives_a_new_signature_when_its_feature_did_not_change(self):
        self.demo.facts('fusion_inserts', BRACKET, THIN)
        self.waive(self.checked()['open'][0])
        self.sigs.update(self.demo.model({'Bracket': 'v2 a rib at the other end', 'Cover': 'v1'})); gate_plan.scan(self.root, wall_s=600)
        v = self.checked()
        self.assertEqual((v['verdict'], [w['state'] for w in v['waivers']]), ('pass', ['valid']))

    def test_an_export_order_is_no_waiver(self):
        self.demo.facts('fusion_inserts', BRACKET, THIN)
        self.waive(self.checked()['open'][0], reason='yes, export')
        v = gate_plan.part_verdict(self.root, self.reg, BRACKET)
        self.assertEqual((v['verdict'], [w['state'] for w in v['waivers']]), ('fail', ['invalid']))

    def test_a_finding_that_asks_is_a_question_not_a_block_row(self):
        hole = gate_features.anchor('hole', (0, 1, 0), (5, 10, 3))
        self.demo.facts('fusion_census', BRACKET, {'holes': [{'id': 'h1', 'anchor': hole, 'd': 2.7, 'class': None}], 'openings': [], 'counts': {}})
        v = self.checked()
        self.assertEqual((v['verdict'], v['open'], len(v['asked']), v['asked'][0]['verdict'], len(v['ask_questions'])), ('attest', [], 1, 'INFO', 1))
        self.assertEqual(gate_plan.exit_code([v]), 2)
        q = v['ask_questions'][0]
        self.assertEqual((q['rule'], q['finding'], q['anchor']), ('census.unknown_hole', v['asked'][0]['id'], hole))

    def test_an_inherited_finding_is_listed_and_does_not_count_as_open(self):
        self.demo.facts('fusion_inserts', BRACKET, THIN)
        f = self.checked()['open'][0]
        item = {'id': 'T-0001', 'finding': f['id'], 'rule': f['rule'], 'part': BRACKET, 'anchor': f['anchor'], 'context': f['context'], 'decision': None}
        gate_common.write_json(gate_common.gate_path(self.root, 'owner', 'triage.json'), {'adopted': {'run': 'r'}, 'items': [item]})
        v = gate_plan.part_verdict(self.root, self.reg, BRACKET)
        self.assertEqual((v['verdict'], v['open'], [(i['triage'], i['decision']) for i in v['inherited']]), ('pass', [], [('T-0001', None)]))
        self.assertEqual([i['id'] for i in gate_report.inherited_block(self.root, BRACKET)], ['T-0001'])       # ... but it stops the export of its part
        gate_common.write_json(gate_common.gate_path(self.root, 'owner', 'triage.json'), {'items': [dict(item, decision='tolerate')]})
        self.assertEqual(gate_report.inherited_block(self.root, BRACKET), [])
        self.demo.facts('fusion_inserts', BRACKET, {'pockets': [{'anchor': POCKET, 'wall': 1.9, 'by': []}]})      # the feature changed: a new finding
        self.sigs.update(self.demo.model({'Bracket': 'v2', 'Cover': 'v1'})); gate_plan.scan(self.root, wall_s=600)
        v = self.checked()
        self.assertEqual((v['verdict'], len(v['open']), v['inherited']), ('fail', 1, []))

    def test_a_judgement_of_an_older_judge_is_named(self):
        self.checked()
        with mock.patch.dict(gate_judges.JUDGES, {'inserts_wall': lambda facts, params, ctx: []}):
            self.assertEqual(gate_plan.part_verdict(self.root, self.reg, BRACKET)['stale'], ['inserts.wall'])
        self.assertEqual(gate_plan.part_verdict(self.root, self.reg, BRACKET)['stale'], [])

    def test_exit_codes_of_a_list_of_verdicts(self):
        v = lambda *kinds: [{'verdict': k} for k in kinds]
        self.assertEqual(gate_plan.exit_code(v('pass', 'pass_attested')), 0); self.assertEqual(gate_plan.exit_code([]), 0)
        self.assertEqual(gate_plan.exit_code(v('pass', 'attest')), 2)
        self.assertEqual(gate_plan.exit_code(v('attest', 'fail')), 1); self.assertEqual(gate_plan.exit_code(v('incomplete')), 1)
        self.assertEqual(gate_plan.exit_code(v('fail'), more=True), 3); self.assertEqual(gate_plan.exit_code(v('pass'), more=True), 3)
        self.assertEqual(gate_plan.exit_code(v('fail', 'attest'), audit=True), 0); self.assertEqual(gate_plan.exit_code(v('fail'), audit=True, more=True), 3)


class Rpc(unittest.TestCase):
    """run_unit() on its own, and the real HTTP transport against a small local MCP stand-in."""
    def setUp(self):
        self.demo = fake_fusion.DemoProject(self, part_questions=())
        self.fusion = self.demo.fusion
        self.url = 'http://127.0.0.1:1/mcp'

    def job(self, probe='fusion_census', part=BRACKET):
        return gate_fusion_rpc.new_job('u-0001', 'r1', probe, 'Demo Assembly', 'demo', self.demo.path('gate', 'run', 'r1', 'units', 'u-0001.json'), part=part, sig='ab' * 32)

    def test_an_old_result_file_never_stands_in_for_a_new_call(self):
        job = self.job()
        gate_common.write_json(job['out'], {'job': 'u-0001', 'facts': {'holes': ['old']}, 'done': True, 'error': None})
        env = gate_fusion_rpc.run_unit(job, self.url, transport=lambda u, s, t: dict(fake_fusion.OK), pinger=self.fusion.ping)
        self.assertEqual((env['error'], env['done'], env['facts']), ('no result file', False, {}))

    def test_the_result_of_another_job_is_refused(self):
        job = self.job()

        def other(url, script, timeout_s):
            gate_common.write_json(job['out'], {'job': 'u-0099', 'facts': {}, 'done': True, 'error': None})
            return dict(fake_fusion.OK)
        env = gate_fusion_rpc.run_unit(job, self.url, transport=other, pinger=self.fusion.ping)
        self.assertIn('another job', env['error']); self.assertFalse(env['done'])

    def test_the_http_timeout_is_the_budget_plus_ten_seconds(self):
        seen = []
        gate_fusion_rpc.run_unit(self.job(), self.url, transport=lambda u, s, t: seen.append(t) or dict(fake_fusion.OK), pinger=self.fusion.ping)
        self.assertEqual(seen, [30.0])

    def test_the_tool_error_text_is_kept_when_no_file_was_written(self):
        env = gate_fusion_rpc.run_unit(self.job(), self.url, transport=lambda u, s, t: dict(fake_fusion.OK, ok=False, error='script refused'), pinger=self.fusion.ping)
        self.assertEqual(env['error'], 'script refused')

    def test_a_late_answer_whose_file_is_there_counts(self):
        job = self.job()

        def late(url, script, timeout_s):
            gate_common.write_json(job['out'], {'job': 'u-0001', 'facts': {'holes': []}, 'done': True, 'error': None, 'sig': job['sig']})
            return dict(fake_fusion.OK, ok=False, timeout=True, error='timeout')
        env = gate_fusion_rpc.run_unit(job, self.url, transport=late, pinger=lambda u, t: True)
        self.assertEqual((env['error'], env['done'], env['facts']), (None, True, {'holes': []}))

    def serve(self, hang=False):
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                seen.append(body)
                if body.get('method') == 'tools/call' and hang:
                    threading.Event().wait(2.5)                    # a Fusion that is busy: no file, no answer in time
                    return
                if body.get('method') == 'tools/call' and body['params']['arguments']['object']['script'] == gate_fusion_rpc.PING_SCRIPT:
                    answer = {'jsonrpc': '2.0', 'id': body['id'], 'result': {'content': [{'type': 'text', 'text': '{"success": true}'}]}}
                elif body.get('method') == 'tools/call':
                    job = gate_fusion_rpc.job_of(body['params']['arguments']['object']['script'])
                    gate_common.write_json(job['out'], {'job': job['id'], 'doc': job['doc'], 'doc_version': 7, 'part': job['part'], 'sig': job['sig'], 'probe': job['probe'],
                                                        'facts': {'holes': []}, 'done': True, 'cursor': None, 'elapsed_s': 0.2, 'error': None, 'open_documents': ['Demo Assembly']})
                    answer = {'jsonrpc': '2.0', 'id': body['id'], 'result': {'content': [{'type': 'text', 'text': '{"success": true}'}]}}
                elif body.get('method') == 'initialize':
                    answer = {'jsonrpc': '2.0', 'id': 1, 'result': {'serverInfo': {'name': 'stand-in'}}}
                else:
                    answer = None
                raw = ('event: message\ndata: %s\n\n' % json.dumps(answer)).encode() if answer else b''
                self.send_response(200 if answer else 202)
                self.send_header('Content-Type', 'text/event-stream'); self.send_header('Mcp-Session-Id', 's-1'); self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                try: self.wfile.write(raw)
                except OSError: pass

            def log_message(self, *a): pass
        try:
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        except OSError as e:
            self.skipTest('cannot listen on the loopback interface here: %s' % e)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(5)))
        return 'http://127.0.0.1:%d/mcp' % server.server_address[1], seen

    def test_the_real_transport_sends_initialize_then_one_call(self):
        for p in mock.patch.object(gate_fusion_rpc, 'http_transport', self.demo_real('http_transport')), mock.patch.object(gate_fusion_rpc, 'http_ping', self.demo_real('http_ping')):
            p.start(); self.addCleanup(p.stop)
        url, seen = self.serve()
        env = gate_fusion_rpc.run_unit(self.job(), url)
        self.assertEqual((env['error'], env['done'], env['doc_version'], env['open_documents']), (None, True, 7, ['Demo Assembly']))
        self.assertEqual([b.get('method') for b in seen], ['initialize', 'notifications/initialized', 'tools/call'])
        args = seen[2]['params']['arguments']
        self.assertEqual((seen[2]['params']['name'], args['featureType'], args['object']['readOnly']), ('fusion_mcp_execute', 'script', True))
        self.assertTrue(args['object']['script'].startswith('# gate unit u-0001: fusion_census on demo/bracket'))
        del seen[:]
        gate_fusion_rpc.run_unit(self.job('fusion_export'), url)
        self.assertFalse(seen[2]['params']['arguments']['object']['readOnly'])                  # the one unit that writes
        del seen[:]
        self.assertTrue(gate_fusion_rpc.http_ping(url, 5))
        self.assertEqual([b.get('method') for b in seen], ['initialize', 'notifications/initialized', 'tools/call'])    # the ping RUNS a script
        ping = seen[2]['params']['arguments']['object']
        self.assertEqual((ping['script'], ping['readOnly']), (gate_fusion_rpc.PING_SCRIPT, True))

    def test_names_damaged_on_the_way_are_an_error_not_a_closed_document(self):
        job = self.job(); seen = {'doc': 'Demo Assembly', 'body': job['body'], 'occurrence': 'Br?cket:1'}
        def transport(url, script, timeout_s):
            j = gate_fusion_rpc.job_of(script)
            gate_common.write_json(j['out'], {'job': j['id'], 'facts': {'holes': [1]}, 'done': True, 'error': None, 'seen': seen, 'open_documents': ['Demo Assembly']})
            return {'ok': True, 'text': '', 'error': None, 'timeout': False, 'down': False}
        env = gate_fusion_rpc.run_unit(job, 'http://x', transport=transport, pinger=lambda u, t: True)
        self.assertIn('names were changed on the way into Fusion (occurrence)', env['error']); self.assertEqual((env['facts'], env['done']), ({}, False))
        self.assertNotIn('is not open', env['error']); self.assertGreater(job['sent'], 0)
        seen['occurrence'] = job['occurrence']
        self.assertIsNone(gate_fusion_rpc.run_unit(job, 'http://x', transport=transport, pinger=lambda u, t: True)['error'])

    def test_a_fusion_that_answers_initialize_and_hangs_in_scripts_is_down(self):
        ping = self.demo_real('http_ping')
        url, seen = self.serve(hang=True)                                # the HTTP side answers, no script comes back
        self.assertFalse(ping(url, 1.0))
        self.assertEqual([b.get('method') for b in seen][:1], ['initialize'])

    def test_the_request_never_goes_through_a_proxy(self):
        """http_proxy set in the shell: urllib would hand a call to 127.0.0.1 to that proxy, with the project's paths and
        names in the body. The gate's opener and the one of tools/fusion/fusion_mcp.py use no proxy at all."""
        url, seen = self.serve()
        proxy, through = self.serve()
        proxy = proxy[:-len('/mcp')]
        env = {k: proxy for k in ('http_proxy', 'HTTP_PROXY', 'all_proxy', 'ALL_PROXY')}; env.update(no_proxy='', NO_PROXY='', FUSION_MCP_URL=url)
        with mock.patch.dict(os.environ, env):
            import urllib.request
            # urllib builds its shared opener once per process and an earlier test has built it: without this a plain
            # urlopen() would keep the opener from before the trap and the test could not fail
            fresh = mock.patch.object(urllib.request, '_opener', None); fresh.start(); self.addCleanup(fresh.stop)
            self.assertEqual(urllib.request.getproxies().get('http'), proxy)                # the trap is set: plain urllib would use it
            self.assertTrue(self.demo_real('http_ping')(url, 5))
            res = self.demo_real('http_transport')(url, gate_fusion_rpc.build_script(self.job()), 5)
            self.assertEqual((res['ok'], res['down'], res['error']), (True, False, None))
            import importlib.util
            spec = importlib.util.spec_from_file_location('fusion_mcp_real', os.path.join(ROOT, 'tools', 'fusion', 'fusion_mcp.py'))
            mcp = importlib.util.module_from_spec(spec); spec.loader.exec_module(mcp)
            self.assertEqual(mcp.connect()[0]['result']['serverInfo'], {'name': 'stand-in'})
        self.assertEqual(through, [])
        self.assertEqual([b.get('method') for b in seen], ['initialize', 'notifications/initialized', 'tools/call'] * 2 + ['initialize', 'notifications/initialized'])

    def test_an_endpoint_elsewhere_stops_the_run_before_anything_is_sent(self):
        demo = fake_fusion.DemoProject(self, layout={'fusion': {'endpoint': 'http://example.com:27182/mcp', 'budget_s': 20}})
        demo.model({'Bracket': 'v1'})
        for argv in (['scan'], ['run', '--wall', '600']):
            code, out, err = demo.gate(*argv)
            self.assertEqual(code, 4, out + err); self.assertIn('"fusion"."endpoint" must be http://127.0.0.1:<port>', out + err)
        self.assertEqual(demo.fusion.calls, [])

    def demo_real(self, name):
        """The module's own function, not the fake DemoProject put in its place."""
        import importlib.util
        spec = importlib.util.spec_from_file_location('gate_fusion_rpc_real', gate_fusion_rpc.__file__)
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        return getattr(mod, name)

    def test_the_real_transport_reports_a_timeout_and_a_missing_server(self):
        transport, ping = self.demo_real('http_transport'), self.demo_real('http_ping')
        url, seen = self.serve(hang=True)
        res = transport(url, gate_fusion_rpc.build_script(self.job()), 1.0)
        self.assertEqual((res['ok'], res['timeout'], res['down']), (False, True, False))
        self.assertEqual(len([b for b in seen if b.get('method') == 'tools/call']), 1)
        import socket
        s = socket.socket(); s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]; s.close()            # a port nobody listens on
        res = transport('http://127.0.0.1:%d/mcp' % port, 'print(1)', 2)
        self.assertEqual((res['ok'], res['down']), (False, True))
        self.assertFalse(ping('http://127.0.0.1:%d/mcp' % port, 1))
        with self.assertRaises(gate_fusion_rpc.FusionDown):
            gate_fusion_rpc.run_unit(self.job(), 'http://127.0.0.1:%d/mcp' % port, transport=transport, pinger=ping)


if __name__ == '__main__':
    unittest.main()
