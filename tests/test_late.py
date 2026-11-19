"""A unit that answers after the host stopped waiting: its result is adopted by the next call, never deleted unread and
never sent a second time on top of the first; a unit that never answers is given up after two calls and the run goes on;
every call that worked longer than its budget is one line in the report, in status, in doctor and in the facts file.

No Fusion: tests/fake_fusion.py is the transport. fusion.set_late() makes a unit work longer than the host waits (the
call times out and the ping gets no answer), fusion.finish() ends it and writes its result file as the real unit does."""
import json, os, sys, time, unittest
import unittest.mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_fusion, gate_common, gate_features, gate_fusion_rpc, gate_judges, gate_params, gate_plan, gate_report

BRACKET, COVER = 'demo/bracket', 'demo/cover'
URL = 'http://127.0.0.1:1/mcp'
POCKET = gate_features.anchor('insert_pocket', (0, 0, 1), (12, -30, 40))
THIN = {'pockets': [{'anchor': POCKET, 'wall': 2.5, 'by': []}]}
RING = {'pockets': [{'anchor': POCKET, 'wall': 4.5, 'by': []}]}


class Rpc(unittest.TestCase):
    """gate_fusion_rpc.run_unit() and settle() with one job."""

    def setUp(self):
        self.demo = fake_fusion.DemoProject(self)
        self.root, self.fusion = self.demo.root, self.demo.fusion

    def job(self, probe='fusion_inserts', part=BRACKET, unit='u-0101', run='r1', sig='ab' * 32, cursor=None):
        out = os.path.join(self.root, 'gate', 'run', run, 'units', unit + '.json')
        return gate_fusion_rpc.new_job(unit, run, probe, 'Demo Assembly', 'demo', out, part=part, sig=sig, cursor=cursor, deadline_s=20)

    def call(self, job):
        return gate_fusion_rpc.run_unit(job, URL, self.fusion.transport, self.fusion.ping)

    def pending(self):
        return gate_common.read_json(os.path.join(self.root, 'gate', 'run', 'pending.json'), None)

    def test_a_late_answer_is_adopted_by_the_next_call_and_nothing_is_sent_twice(self):
        self.fusion.set_late('fusion_inserts', BRACKET, RING, done=False, cursor=1, elapsed_s=65.5)
        with self.assertRaises(gate_fusion_rpc.FusionDown) as e: self.call(self.job())
        self.assertTrue(e.exception.timed_out); self.assertIn('nothing else was sent', str(e.exception))
        note = self.pending()
        self.assertEqual((note['id'], note['run'], note['probe'], note['part'], note['file'], note['deadline_s']),
                         ('u-0101', 'r1', 'fusion_inserts', BRACKET, 'r1/units/u-0101.json', 20.0))
        self.assertNotIn(self.root, json.dumps(note))                                    # no absolute path in the note
        # Fusion is still working: the next call sends nothing, not this unit and not another one
        for job in (self.job(), self.job(probe='fusion_thin', unit='u-0102'), self.job(part=COVER, unit='u-0103')):
            with self.assertRaises(gate_fusion_rpc.FusionDown) as e: self.call(job)
            self.assertFalse(e.exception.timed_out)
            self.assertIn('is still working', str(e.exception)); self.assertIn('Nothing was sent', str(e.exception))
        self.assertEqual((len(self.fusion.calls), self.fusion.stacked), (1, 0))
        self.assertIsNotNone(self.pending())
        # the unit ends: its result file is the answer of the next call of the same job
        self.fusion.finish()
        job = self.job()
        env = self.call(job)
        self.assertEqual((env['facts'], env['done'], env['cursor'], env['elapsed_s'], env['error']), (RING, False, 1, 65.5, None))
        self.assertEqual(env['late'], {'sent': note['sent'], 'elapsed_s': 65.5, 'budget_s': 20.0})
        self.assertEqual(env['notes'], ['late answer: the call got no answer in time and was not sent again; its result file was read by the next call. '
                                        'The unit worked 65.5 s (budget 20 s)'])
        self.assertEqual((len(self.fusion.calls), self.fusion.stacked), (1, 0))          # nothing was sent again
        self.assertIsNone(self.pending()); self.assertTrue(os.path.isfile(job['out']))   # read, and still on disk

    def test_what_is_not_the_answer_of_this_call_is_not_adopted(self):
        def late(**answer):
            self.fusion.late.clear(); self.fusion.calls.clear()
            self.fusion.set_late('fusion_inserts', BRACKET, RING, **answer)
            with self.assertRaises(gate_fusion_rpc.FusionDown): self.call(self.job())
            self.fusion.finish()
            self.fusion.set_facts('fusion_inserts', BRACKET, THIN)
        # an error in the result file: read, refused, and the unit is sent again
        late(error='RuntimeError: boom')
        state, env, why = gate_fusion_rpc.settle(self.job(), URL, self.fusion.ping)
        self.assertEqual(state, 'lost'); self.assertIn('holds an error: RuntimeError: boom', why)
        self.assertEqual((env['error'], env['facts'], env['late']['elapsed_s']), ('RuntimeError: boom', RING, 65.0))    # the error IS that call's answer: handed on, never as facts
        late(error='RuntimeError: boom')
        env = self.call(self.job())
        self.assertEqual((env['facts'], 'late' in env, len(self.fusion.calls)), (THIN, False, 2))
        # the plan carries another signature now, or another cursor: that file answers another question
        for other in ({'sig': 'cd' * 32}, {'cursor': 1}):
            late()
            env = self.call(self.job(**other))
            self.assertEqual((env['facts'], 'late' in env, len(self.fusion.calls)), (THIN, False, 2), other)
            self.assertIsNone(self.pending())
        # a file that an EARLIER call of this unit wrote (same cursor, older send time) is not this call's answer
        late()
        job = self.job()
        note = self.pending()
        with open(job['out'], encoding='utf-8') as f: old = json.load(f)
        old['asked']['sent'] = note['sent'] - 100.0
        with open(job['out'], 'w', encoding='utf-8') as f: json.dump(old, f)
        self.assertEqual(gate_fusion_rpc.late_answer(job, note), (None, 'the result file was written by another call of this unit'))
        self.assertEqual(gate_fusion_rpc.settle(job, URL, self.fusion.ping)[0], 'lost')  # Fusion answers the ping: that call is over

    def test_a_unit_that_ended_without_a_result_is_sent_again_only_when_fusion_answers(self):
        self.fusion.set_late('fusion_inserts', BRACKET, RING)
        with self.assertRaises(gate_fusion_rpc.FusionDown): self.call(self.job())
        with self.assertRaises(gate_fusion_rpc.FusionDown): self.call(self.job())        # no file, no ping: nothing is sent
        self.assertEqual((len(self.fusion.calls), self.fusion.pings), (1, 2))
        self.fusion.finish(write=False)                                                  # it was killed: no result file
        self.assertEqual(gate_fusion_rpc.settle(self.job(), URL, self.fusion.ping), ('lost', None, 'it left no result file'))
        self.fusion.set_facts('fusion_inserts', BRACKET, THIN)
        env = self.call(self.job())
        self.assertEqual((env['facts'], len(self.fusion.calls), self.fusion.stacked), (THIN, 2, 0))

    def test_a_call_that_is_answered_leaves_no_note_and_a_timeout_with_a_live_ping_is_marked(self):
        self.fusion.set_facts('fusion_inserts', BRACKET, THIN)
        env = self.call(self.job())
        self.assertEqual(env['asked'], {'run': 'r1', 'sent': env['asked']['sent'], 'sig': 'ab' * 32, 'cursor_key': gate_fusion_rpc.cursor_key(None)})
        self.assertIsNone(self.pending())
        self.fusion.set_timeout('fusion_thin', BRACKET, alive_after=True)
        env = self.call(self.job(probe='fusion_thin', unit='u-0102'))
        self.assertEqual((env['error'], env['timeout'], env['done']), ('timeout', True, False)); self.assertIsNone(self.pending())
        self.fusion.down = True
        with self.assertRaises(gate_fusion_rpc.FusionDown) as e: self.call(self.job())
        self.assertFalse(e.exception.timed_out); self.assertIsNone(self.pending())       # nothing reached Fusion


class Run(unittest.TestCase):
    """The unit loop of gate_plan and gate.py as a whole."""

    def setUp(self):
        self.demo = fake_fusion.DemoProject(self, part_questions=())
        self.root, self.fusion = self.demo.root, self.demo.fusion
        self.sigs = self.demo.model({'Bracket': 'v1', 'Cover': 'v1'})
        self.reg = self.demo.registry()
        self.resolved = gate_params.resolve(self.reg, {}, {'nozzle': 0.6, 'line_width': 0.62, 'material': 'PETG'})
        self.assertTrue(gate_plan.scan(self.root, wall_s=600)['done'])
        self.fusion.calls.clear(); self.fusion.now = 0.0

    def plan(self, parts=(BRACKET, COVER)):
        return gate_plan.new_plan(self.root, self.reg, list(parts))

    def run_units(self, plan):
        return gate_plan.run_units(self.root, self.reg, self.resolved, plan, wall_s=600)

    def unit(self, probe, part=BRACKET):
        return [u for u in gate_plan.load_plan(self.root)['units'] if u['probe'] == probe and u['part'] == part][0]

    def facts(self, probe, part=BRACKET):
        return gate_common.load_facts(self.root, part, self.sigs[part])['probes'].get(probe)

    def test_the_run_gets_past_a_unit_that_answered_late(self):
        """A unit worked three times its budget in one piece: the call timed out, Fusion was busy, the gate stopped. The
        answer is on disk when Fusion is free again: the next call takes it and goes on from its cursor."""
        self.fusion.clear('fusion_inserts', BRACKET)
        self.fusion.set_late('fusion_inserts', BRACKET, {'pockets': [{'anchor': POCKET, 'wall': 4.5, 'by': []}]}, done=False, cursor=1, elapsed_s=65.5)
        self.fusion.set_facts('fusion_inserts', BRACKET, {'pockets': [{'anchor': gate_features.anchor('insert_pocket', (0, 0, 1), (30, -30, 40)), 'wall': 5.0, 'by': []}]}, elapsed_s=3.0)
        plan = self.plan()
        with self.assertRaises(gate_fusion_rpc.FusionDown): self.run_units(plan)
        u = self.unit('fusion_inserts')
        self.assertEqual((u['state'], u['attempts'], u['timeouts'], u['cursor']), ('todo', 1, 1, None))
        sent = len(self.fusion.calls)
        for _ in range(3):                                                               # three more calls while Fusion works: nothing is sent, nothing is counted
            with self.assertRaises(gate_fusion_rpc.FusionDown) as e: self.run_units(gate_plan.load_plan(self.root))
            self.assertIn('is still working', str(e.exception))
        u = self.unit('fusion_inserts')
        self.assertEqual((len(self.fusion.calls), self.fusion.stacked, u['attempts'], u['timeouts']), (sent, 0, 1, 1))
        self.fusion.finish()
        res = self.run_units(gate_plan.load_plan(self.root))
        self.assertEqual((res['more'], res['done'], res['total']), (False, 8, 8))        # the units behind it ran
        u = self.unit('fusion_inserts')
        self.assertEqual((u['state'], u['attempts'], u['timeouts'], u['error']), ('done', 2, 0, None))
        calls = self.fusion.calls_of('fusion_inserts', BRACKET)
        self.assertEqual([c['cursor'] for c in calls], [None, 1])                        # sent once from the start, then on from the cursor of the late answer
        self.assertEqual(self.fusion.stacked, 0)
        self.assertEqual(u['over'], [{'call': 1, 'elapsed_s': 65.5, 'budget_s': 20.0, 'over_s': 45.5, 'late': True}])
        entry = self.facts('fusion_inserts')
        self.assertEqual([p['wall'] for p in entry['facts']['pockets']], [4.5, 5.0])     # the facts of both calls
        self.assertEqual(entry['notes'], ['over budget: call 1 of this unit worked 65.5 s, its budget is 20 s (45.5 s over); the answer came after the '
                                          'host had stopped waiting and was read from the result file by the next call'])
        rows = gate_plan.over_budget(gate_plan.load_plan(self.root))
        self.assertEqual(gate_plan.over_lines(rows), ['OVER BUDGET  %s fusion_inserts on demo/bracket: call 1 worked 65.5 s of 20 s (45.5 s over), its answer was '
                                                      'read late from the result file. A defect of the probe: report it' % u['id']])

    def test_a_unit_that_never_answers_is_given_up_after_two_calls_and_the_run_goes_on(self):
        self.fusion.clear('fusion_inserts', BRACKET)
        self.fusion.set_late('fusion_inserts', BRACKET, {}); self.fusion.set_late('fusion_inserts', BRACKET, {})
        plan = self.plan()
        for n in (1, 2):
            with self.assertRaises(gate_fusion_rpc.FusionDown) as e: self.run_units(gate_plan.load_plan(self.root) if n > 1 else plan)
            self.assertTrue(e.exception.timed_out)
            self.assertEqual(self.unit('fusion_inserts')['timeouts'], n)
            with self.assertRaises(gate_fusion_rpc.FusionDown): self.run_units(gate_plan.load_plan(self.root))      # still busy: not counted, not sent
            self.assertEqual(self.unit('fusion_inserts')['timeouts'], n)
            self.fusion.finish(write=False)                                              # Fusion is free again, and there is no result
        self.assertEqual(len(self.fusion.calls_of('fusion_inserts', BRACKET)), 2)
        res = self.run_units(gate_plan.load_plan(self.root))
        self.assertEqual((res['more'], res['done'], res['total']), (False, 8, 8))
        self.assertEqual(len(self.fusion.calls_of('fusion_inserts', BRACKET)), 2)        # not sent a third time
        u = self.unit('fusion_inserts')
        self.assertEqual((u['state'], u['over_budget'], u['timeouts']), ('error', 'not run', 2))
        self.assertTrue(u['error'].startswith('over budget, not run: 2 calls in a row got no answer within 30 s and left no result (budget 20 s)'))
        self.assertEqual({x['state'] for x in gate_plan.load_plan(self.root)['units'] if x is not u and x['id'] != u['id']}, {'done'})
        self.assertTrue(self.facts('fusion_inserts')['error'].startswith('over budget, not run'))
        found = gate_plan.judge_part(self.root, self.reg, self.resolved, BRACKET)
        row, = found['inserts.wall']
        self.assertEqual(row['verdict'], 'NOT_RUN')                                      # no data is never a pass
        self.assertTrue(row['text'].startswith('no facts from fusion_inserts: over budget, not run: 2 calls in a row'), row['text'])
        v = gate_plan.part_verdict(self.root, self.reg, BRACKET)
        self.assertEqual((v['verdict'], gate_plan.exit_code([v])), ('incomplete', 1))
        one, two, line = gate_plan.over_lines(gate_plan.over_budget(gate_plan.load_plan(self.root)))
        self.assertIn('fusion_inserts on demo/bracket: NOT RUN. 2 calls in a row got no answer within its time (budget 20 s) and left no result', line)
        self.assertIn('fusion_inserts on demo/bracket: call 1 got no answer within 30 s (budget 20 s) and left no result. A defect of the probe', one)
        self.assertIn('call 2 got no answer within 30 s', two)
        self.assertEqual(self.facts('fusion_inserts')['notes'], ['over budget: call %d of this unit got no answer within 30 s (its budget is 20 s) and left no result' % n for n in (1, 2)])

    def test_one_answer_between_two_timeouts_starts_the_count_again(self):
        self.fusion.clear('fusion_inserts', BRACKET)
        self.fusion.set_late('fusion_inserts', BRACKET, {})
        self.fusion.set_facts('fusion_inserts', BRACKET, {'pockets': []}, done=False, cursor=1)
        self.fusion.set_facts('fusion_inserts', BRACKET, {'pockets': []})
        with self.assertRaises(gate_fusion_rpc.FusionDown): self.run_units(self.plan())
        self.fusion.finish(write=False)
        self.fusion.late[('fusion_inserts', BRACKET)] = []
        res = self.run_units(gate_plan.load_plan(self.root))
        u = self.unit('fusion_inserts')
        self.assertEqual((res['more'], u['state'], u['timeouts'], u.get('over_budget')), (False, 'done', 0, None))

    def test_a_unit_that_waited_behind_another_session_is_not_given_up(self):
        """Fusion was busy with something else: the unit started after the host had stopped waiting, measured nothing and
        wrote that. Such a call is no call the unit ran over, however often it happens: the unit is sent again."""
        queued = 'TimeoutError: the unit started 45 s after it was sent: the host has stopped waiting, nothing was measured'
        self.fusion.clear('fusion_inserts', BRACKET)
        for _ in range(3): self.fusion.set_late('fusion_inserts', BRACKET, {}, error=queued, elapsed_s=0.0)
        self.fusion.set_facts('fusion_inserts', BRACKET, {'pockets': []})
        with self.assertRaises(gate_fusion_rpc.FusionDown): self.run_units(self.plan())
        for n in (2, 3):
            self.fusion.finish()
            with self.assertRaises(gate_fusion_rpc.FusionDown): self.run_units(gate_plan.load_plan(self.root))
            self.assertEqual((self.unit('fusion_inserts')['timeouts'], len(self.fusion.calls_of('fusion_inserts', BRACKET))), (1, n))
        self.fusion.finish()
        res = self.run_units(gate_plan.load_plan(self.root))
        u = self.unit('fusion_inserts')
        self.assertEqual((res['more'], u['state'], u['timeouts'], u.get('over_budget'), len(self.fusion.calls_of('fusion_inserts', BRACKET))), (False, 'done', 0, None, 4))

    def test_a_call_over_its_budget_that_still_answered_is_a_line_too(self):
        self.demo.facts('fusion_thin', BRACKET, {'faces': []}, elapsed_s=27.0)
        self.run_units(self.plan())
        u = self.unit('fusion_thin')
        self.assertEqual((u['state'], u['over']), ('done', [{'call': 1, 'elapsed_s': 27.0, 'budget_s': 20.0, 'over_s': 7.0, 'late': False}]))
        self.assertEqual(self.facts('fusion_thin')['notes'], ['over budget: call 1 of this unit worked 27 s, its budget is 20 s (7 s over)'])
        self.assertEqual(self.facts('fusion_census').get('notes'), [])                   # a unit inside its budget has no such note
        self.assertNotIn('over', self.unit('fusion_census'))

    def test_gate_run_status_doctor_and_the_report_say_it(self):
        self.fusion.clear('fusion_inserts', BRACKET)
        self.fusion.set_late('fusion_inserts', BRACKET, {'pockets': []}, elapsed_s=65.5)
        self.demo.facts('fusion_thin', COVER, {'faces': []}, elapsed_s=27.0)
        code, out, err = self.demo.gate('run', '--all', '--no-scan', '--wall', '600')
        self.assertEqual(code, 4); self.assertIn('GATE ERROR  unit ', err); self.assertIn('got no answer within 30 s', err)
        code, out, err = self.demo.gate('run', '--all', '--no-scan', '--wall', '600')
        self.assertEqual(code, 4); self.assertIn('is still working', err); self.assertEqual(self.fusion.stacked, 0)
        waiting = 'fusion_inserts on demo/bracket) was sent and its answer was not read yet (budget 20 s)'
        self.assertIn(waiting, self.demo.gate('status')[1]); self.assertIn(waiting, self.demo.gate('doctor')[1])
        self.fusion.finish()
        code, out, err = self.demo.gate('run', '--all', '--no-scan', '--wall', '600')
        self.assertEqual((code, err), (0, ''))
        lines = [line.strip() for line in out.splitlines() if 'OVER BUDGET' in line]
        self.assertEqual(len(lines), 2)
        self.assertIn('fusion_inserts on demo/bracket: call 1 worked 65.5 s of 20 s (45.5 s over), its answer was read late from the result file', lines[0])
        self.assertIn('fusion_thin on demo/cover: call 1 worked 27 s of 20 s (7 s over). A defect of the probe: report it', lines[1])
        report = gate_report.load_latest(self.root)
        self.assertEqual([(r['probe'], r['part'], r['over_s'], r['late']) for r in report['over_budget']],
                         [('fusion_inserts', BRACKET, 45.5, True), ('fusion_thin', COVER, 7.0, False)])
        with open(os.path.join(self.root, 'gate', 'reports', report['run'] + '.md'), encoding='utf-8') as f: md = f.read()
        self.assertIn('## Units over their budget', md); self.assertIn('fusion_inserts on demo/bracket: call 1 worked 65.5 s of 20 s', md)
        code, out, err = self.demo.gate('status')
        self.assertEqual(len([line for line in out.splitlines() if line.startswith('  OVER BUDGET  ')]), 2)
        code, out, err = self.demo.gate('doctor')
        self.assertEqual(len([line for line in out.splitlines() if 'worked' in line and 'A defect of the probe' in line]), 2, out)
        self.assertEqual(len(gate_report.status(self.root)['over_budget']), 2); self.assertIsNone(gate_report.status(self.root)['waiting_call'])
        self.assertNotIn('was sent and its answer was not read yet', out)

    def test_a_given_up_unit_makes_the_run_exit_1_and_names_itself(self):
        self.fusion.clear('fusion_inserts', BRACKET)
        self.fusion.set_late('fusion_inserts', BRACKET, {}); self.fusion.set_late('fusion_inserts', BRACKET, {})
        for _ in (1, 2):
            self.assertEqual(self.demo.gate('run', '--all', '--no-scan', '--wall', '600')[0], 4)
            self.fusion.finish(write=False)
        code, out, err = self.demo.gate('run', '--all', '--no-scan', '--wall', '600')
        self.assertEqual(code, 1, out + err)
        self.assertIn('NOT_RUN', out); self.assertIn('over budget, not run', out)
        self.assertIn('OVER BUDGET', out); self.assertIn('fusion_inserts on demo/bracket: NOT RUN', out)
        self.assertIn('nothing failed: 1 check(s) could not decide or did not run', out)

    def test_timing_and_notes_beside_the_facts_change_no_finding(self):
        """The "timing" block and the notes of an envelope are stored beside the facts. No judge is handed them."""
        def judged(**beside):
            self.demo.facts('fusion_inserts', BRACKET, THIN)
            self.fusion.calls.clear()
            self.run_units(self.plan([BRACKET]))
            entry = self.facts('fusion_inserts')
            entry.update(beside)
            gate_common.save_probe_facts(self.root, BRACKET, self.sigs[BRACKET], dict(entry, part=BRACKET, sig=self.sigs[BRACKET], probe='fusion_inserts'))
            found = gate_plan.judge_part(self.root, self.reg, self.resolved, BRACKET)
            return json.dumps({k: [{x: f[x] for x in f if x != 'time'} for f in v] for k, v in found.items()}, sort_keys=True), gate_plan.part_verdict(self.root, self.reg, BRACKET)['verdict']
        plain = judged()
        timing = {'calls': 3, 'prelude_s': 2.5, 'faces': {'s': 41.0, 'pc': 13440}, 'seats': {'s': 8.0, 'pc': 2500}, 'travel': {'s': 3.0, 'pc': 900},
                  'slowest': [{'name': 'Wheel', 'faces': 7000, 'pc': 16000, 's': 50.0}]}
        noted = judged(timing=timing, notes=['timing: 3 call(s), 2.5 s before the first step; point tests: faces 41.0 s / 13440',
                                             'over budget: call 1 of this unit worked 65.5 s, its budget is 20 s (45.5 s over)'])
        self.assertEqual(plain, noted)
        self.assertEqual(self.facts('fusion_inserts')['timing'], timing)                 # kept in the facts file, beside "facts"
        self.assertNotIn('timing', self.facts('fusion_inserts')['facts'])
        seen = []
        name = self.reg['rules']['inserts.wall']['check']['judge']
        real = gate_judges.JUDGES[name]
        gate_judges.JUDGES[name] = lambda facts, params, ctx: (seen.append((facts, ctx)), real(facts, params, ctx))[1]
        try:
            gate_plan.judge_part(self.root, self.reg, self.resolved, BRACKET)
        finally:
            gate_judges.JUDGES[name] = real
        self.assertEqual(seen[0][0], THIN); self.assertNotIn('Wheel', json.dumps(seen[0][1], default=str))


class Between(Run):
    """What another gate command, a broken connection, a closed Fusion or a week between two calls do to a call that
    got no answer: the unit keeps its own note of that call in the plan ("flight"), so its answer is found, read once,
    and never taken for more than it is."""

    def gate(self):
        return self.demo.gate('run', '--all', '--no-scan', '--wall', '600')

    def pending(self):
        return gate_common.read_json(os.path.join(self.root, 'gate', 'run', 'pending.json'), None)

    def late_inserts(self, facts=THIN, n=1, **kw):
        self.fusion.clear('fusion_inserts', BRACKET)
        for _ in range(n): self.fusion.set_late('fusion_inserts', BRACKET, facts, **dict({'elapsed_s': 41.0}, **kw))

    def test_the_unit_notes_its_call_in_the_plan_before_it_goes_out(self):
        self.late_inserts()
        self.assertEqual(self.gate()[0], 4)
        u, note = self.unit('fusion_inserts'), self.pending()
        self.assertEqual(u['flight'], {'sent': note['sent'], 'cursor': gate_fusion_rpc.cursor_key(None), 'counted': True})
        self.fusion.finish()
        self.assertEqual(self.gate()[0], 1)                                              # the thin wall of the late answer is a finding
        self.assertNotIn('flight', self.unit('fusion_inserts'))
        self.assertNotIn('flight', self.unit('fusion_thin'))                             # a call that was answered leaves none

    def test_a_scan_between_the_timeout_and_the_next_run_does_not_lose_the_answer(self):
        self.late_inserts()
        self.assertEqual(self.gate()[0], 4)
        self.fusion.finish()
        self.assertEqual(self.demo.gate('scan')[0], 0)
        self.assertIsNone(self.pending())                                                # the scan saw that the call is over
        self.assertIn('flight', self.unit('fusion_inserts'))                             # the unit still knows which call it waits for
        code, out, err = self.gate()
        self.assertEqual([c['cursor'] for c in self.fusion.calls_of('fusion_inserts', BRACKET)], [None])        # never sent again
        self.assertEqual(self.facts('fusion_inserts')['facts'], THIN)                    # the late answer was read, not deleted
        self.assertIn('fusion_inserts on demo/bracket: call 1 worked 41 s of 20 s (21 s over), its answer was read late from the result file', out)
        self.assertEqual((code, self.fusion.stacked), (1, 0))

    def test_another_unit_between_the_timeout_and_the_next_run_does_not_lose_the_answer(self):
        """The signature unit and the export unit of `gate.py export` run in a run of their own: they see that the
        earlier call is over and clear gate/run/pending.json. The result file of that call stays where it is."""
        self.late_inserts()
        self.assertEqual(self.gate()[0], 4)
        other = gate_fusion_rpc.new_job('u-sig', 'another-run', 'fusion_thin', 'Demo Assembly', 'demo',
                                        os.path.join(self.root, 'gate', 'run', 'another-run', 'units', 'u-sig.json'), part=COVER, sig=self.sigs[COVER])
        with self.assertRaises(gate_fusion_rpc.FusionDown) as e: gate_fusion_rpc.run_unit(dict(other), URL)
        self.assertIn('is still working', str(e.exception))                              # not behind a running unit either
        self.fusion.finish()
        env = gate_fusion_rpc.run_unit(other, URL)
        self.assertIsNone(env['error']); self.assertIsNone(self.pending())
        self.gate()
        self.assertEqual(len(self.fusion.calls_of('fusion_inserts', BRACKET)), 1)
        self.assertEqual((self.facts('fusion_inserts')['facts'], self.fusion.stacked), (THIN, 0))

    def test_a_unit_is_not_given_up_while_the_result_of_its_last_call_is_on_disk(self):
        self.late_inserts(n=2)
        self.assertEqual(self.gate()[0], 4)
        self.fusion.finish(write=False)                                                  # the first call left nothing
        self.assertEqual(self.gate()[0], 4)                                              # the second one is sent, and is late too
        self.assertEqual(self.unit('fusion_inserts')['timeouts'], 2)
        self.fusion.finish()                                                             # it wrote its result
        self.assertEqual(self.demo.gate('scan')[0], 0)
        code, out, err = self.gate()
        u = self.unit('fusion_inserts')
        self.assertEqual((u['state'], u.get('over_budget'), u['timeouts'], u['error']), ('done', None, 0, None))
        self.assertEqual(self.facts('fusion_inserts')['facts'], THIN)
        self.assertEqual(len(self.fusion.calls_of('fusion_inserts', BRACKET)), 2)
        self.assertEqual([(r.get('no_answer', False), r['late']) for r in u['over']], [(True, False), (False, True)])     # both calls are said

    def test_the_flight_alone_finds_the_answer_and_only_of_the_same_call(self):
        self.late_inserts(done=False, cursor=1)
        self.assertEqual(self.gate()[0], 4)
        self.fusion.finish()
        u = self.unit('fusion_inserts')
        os.remove(os.path.join(self.root, 'gate', 'run', 'pending.json'))
        job = gate_fusion_rpc.new_job(u['id'], gate_plan.load_plan(self.root)['run'], 'fusion_inserts', 'Demo Assembly', 'demo',
                                      os.path.join(self.root, *u['out'].split('/')), part=BRACKET, sig=u['sig'], cursor=None, deadline_s=20)
        self.assertEqual(gate_fusion_rpc.settle(dict(job), URL, self.fusion.ping)[0], 'none')              # no note at all: nothing is known
        self.assertEqual(gate_fusion_rpc.settle(dict(job, cursor=1), URL, self.fusion.ping, u['flight'])[0], 'none')   # the note of another call
        state, why = gate_fusion_rpc.settle(dict(job), URL, self.fusion.ping, dict(u['flight'], sent=u['flight']['sent'] - 50.0))[::2]
        self.assertEqual((state, why), ('lost', 'the result file was written by another call of this unit'))
        state, env, why = gate_fusion_rpc.settle(dict(job), URL, self.fusion.ping, u['flight'])
        self.assertEqual((state, env['facts'], env['cursor'], env['late']['elapsed_s']), ('late', THIN, 1, 41.0))
        self.assertTrue(os.path.isfile(job['out']))

    def test_a_late_answer_that_is_an_error_is_counted_with_its_own_text(self):
        boom = 'RuntimeError: a step of a bearing (faces:3|+) did not ask the same questions in three calls in a row\nTraceback: ...'
        self.late_inserts(facts={}, n=2, error=boom)
        self.assertEqual(self.gate()[0], 4)
        self.fusion.finish()
        self.assertEqual(self.gate()[0], 4)                                              # read, counted as a failed call, sent once more
        u = self.unit('fusion_inserts')
        self.assertEqual((u['failures'], u['timeouts'], len(self.fusion.calls_of('fusion_inserts', BRACKET))), (1, 1, 2))
        self.fusion.finish()
        code, out, err = self.gate()
        u = self.unit('fusion_inserts')
        first = 'RuntimeError: a step of a bearing (faces:3|+) did not ask the same questions in three calls in a row'
        self.assertEqual((code, u['state'], u['error'], u.get('over_budget')), (1, 'error', first, None))
        self.assertEqual(len(self.fusion.calls_of('fusion_inserts', BRACKET)), 2)        # two failed calls end a unit: not sent a third time
        self.assertEqual(self.facts('fusion_inserts')['error'], first)
        row, = gate_plan.judge_part(self.root, self.reg, self.resolved, BRACKET)['inserts.wall']
        self.assertEqual(row['verdict'], 'NOT_RUN')
        self.assertIn('UNIT ERROR  %s fusion_inserts on demo/bracket: %s' % (u['id'], first), out)       # the text of the probe is on the screen
        self.assertNotIn('left no result', out + row['text'])
        self.assertEqual([(r['call'], r['elapsed_s'], r['late']) for r in u['over']], [(1, 41.0, True), (2, 41.0, True)])
        self.assertEqual(len([line for line in out.splitlines() if 'OVER BUDGET' in line and 'read late' in line]), 2)

    def test_a_connection_that_breaks_while_the_unit_works_puts_no_second_copy_behind_it(self):
        self.late_inserts(broken=True)
        code, out, err = self.gate()
        self.assertEqual(code, 4); self.assertIn('the connection ended without an answer (RemoteDisconnected', err)
        u = self.unit('fusion_inserts')
        self.assertEqual((u['timeouts'], u['attempts'], len(self.fusion.calls), self.fusion.stacked), (1, 1, len(self.fusion.calls), 0))
        self.assertEqual(len(self.fusion.calls_of('fusion_inserts', BRACKET)), 1); self.assertIsNotNone(self.pending())
        code, out, err = self.gate()
        self.assertEqual(code, 4); self.assertIn('is still working', err)
        self.fusion.finish()
        self.gate()
        self.assertEqual((len(self.fusion.calls_of('fusion_inserts', BRACKET)), self.fusion.stacked, self.facts('fusion_inserts')['facts']), (1, 0, THIN))

    def test_a_tool_error_with_a_fusion_that_answers_is_an_error_of_the_call_as_before(self):
        job = gate_fusion_rpc.new_job('u-0001', 'r9', 'fusion_thin', 'Demo Assembly', 'demo', os.path.join(self.root, 'gate', 'run', 'r9', 'units', 'u-0001.json'), part=COVER)
        env = gate_fusion_rpc.run_unit(job, URL, transport=lambda u, s, t: dict(fake_fusion.OK, ok=False, error='script refused'), pinger=self.fusion.ping)
        self.assertEqual((env['error'], self.pending()), ('script refused', None))

    def test_a_fusion_that_is_closed_is_not_called_busy(self):
        self.late_inserts()
        self.assertEqual(self.gate()[0], 4)
        self.fusion.down = True                                                          # Fusion was quit after the hang
        code, out, err = self.gate()
        self.assertEqual(code, 4)
        self.assertIn('nothing listens at http://127.0.0.1:1/mcp: Fusion is not running, or its MCP server is off', err)
        self.assertNotIn('still working', err); self.assertIn('Nothing was sent', err)
        self.assertIsNotNone(self.pending()); self.assertEqual(len(self.fusion.calls_of('fusion_inserts', BRACKET)), 1)
        self.fusion.down = False                                                         # started again: the unit it was busy with is gone
        self.fusion.working = None
        self.fusion.set_facts('fusion_inserts', BRACKET, {'pockets': []})
        code, out, err = self.gate()
        u = self.unit('fusion_inserts')
        self.assertEqual((code, u['state'], len(self.fusion.calls_of('fusion_inserts', BRACKET))), (0, 'done', 2))
        self.assertIn('call 1 got no answer within 30 s (budget 20 s) and left no result', out)      # said although the next call answered
        self.assertEqual(self.facts('fusion_inserts')['notes'], ['over budget: call 1 of this unit got no answer within 30 s (its budget is 20 s) and left no result'])
        self.assertEqual([(r['probe'], r.get('no_answer')) for r in gate_report.load_latest(self.root)['over_budget']], [('fusion_inserts', True)])

    def test_a_call_with_no_answer_and_no_result_is_said_even_when_the_next_one_answers(self):
        """Fusion answered the ping after the timeout, and there was no result file: the unit is sent again at once. The
        call that got nothing is one OVER BUDGET line all the same, and a note in the facts file."""
        self.fusion.set_timeout('fusion_thin', BRACKET, alive_after=True)
        sent = []
        real = self.fusion.transport

        def once(url, script, timeout_s):                                                # the first call of the unit hangs, the second is answered
            job = gate_fusion_rpc.job_of(script)
            if job['probe'] == 'fusion_thin' and job['part'] == BRACKET:
                sent.append(job)
                if len(sent) == 2: self.fusion.hang.clear()
            return real(url, script, timeout_s)
        with unittest.mock.patch.object(gate_fusion_rpc, 'http_transport', once):
            code, out, err = self.gate()
        u = self.unit('fusion_thin')
        self.assertEqual((code, u['state'], u['attempts'], u['timeouts'], len(sent)), (0, 'done', 2, 0, 2))
        self.assertEqual(u['over'], [{'call': 1, 'budget_s': 20.0, 'no_answer': True, 'late': False, 'waited_s': 30.0, 'why': 'it left no result file'}])
        self.assertIn('fusion_thin on demo/bracket: call 1 got no answer within 30 s (budget 20 s) and left no result. A defect of the probe: report it', out)
        self.assertEqual(self.facts('fusion_thin')['notes'], ['over budget: call 1 of this unit got no answer within 30 s (its budget is 20 s) and left no result'])
        self.assertNotIn('flight', u); self.assertNotIn('asked', self.facts('fusion_thin')); self.assertNotIn('late', self.facts('fusion_thin'))

    def test_the_facts_file_does_not_keep_which_call_wrote_the_result(self):
        self.late_inserts(done=False, cursor={'i': 0, 'tape': {'n': 4000, 'hex': 'f' * 1000, 'sum': 7}})
        self.fusion.set_facts('fusion_inserts', BRACKET, {'pockets': []})
        self.assertEqual(self.gate()[0], 4)
        self.fusion.finish()
        self.gate()
        entry = self.facts('fusion_inserts')
        self.assertEqual(entry['facts'], THIN)
        self.assertNotIn('asked', entry); self.assertNotIn('late', entry); self.assertNotIn('ffff', json.dumps(entry))     # no cursor of a call in a committed file

    def scan_facts(self, **words):
        return {'parts': [fake_fusion.scan_part(n, fake_fusion.sig_of('%s %s' % (n, w))) for n, w in words.items()], 'refs': []}

    def test_a_late_scan_answer_is_adopted_while_it_is_young_and_said(self):
        self.fusion.clear('fusion_scan', None)
        self.fusion.set_late('fusion_scan', None, self.scan_facts(Bracket='v1', Cover='v1'), elapsed_s=41.0)
        self.assertEqual(self.demo.gate('scan')[0], 4)
        self.fusion.finish()
        code, out, err = self.demo.gate('scan')
        self.assertEqual((code, len(self.fusion.calls_of('fusion_scan'))), (0, 1))       # read from the file, not scanned again
        line = 'fusion_scan on demo: call 1 worked 41 s of 20 s (21 s over), its answer was read late from the result file'
        self.assertIn(line, out)                                                         # a scan over its budget is said by the scan,
        self.assertIn(line, self.demo.gate('status')[1]); self.assertIn(line, self.demo.gate('doctor')[1])      # by status and by doctor

    def test_a_late_scan_answer_that_lay_about_is_not_the_model_any_more(self):
        import time, types
        self.fusion.clear('fusion_scan', None)
        self.fusion.set_late('fusion_scan', None, self.scan_facts(Bracket='v1', Cover='v1'), elapsed_s=41.0)
        self.assertEqual(self.demo.gate('scan')[0], 4)
        self.fusion.finish()
        sigs = self.demo.model({'Bracket': 'v2', 'Cover': 'v1'})                         # the model changed after that scan
        real = time.time
        later = types.SimpleNamespace(time=lambda: real() + 7 * 86400.0)                 # and the next gate call comes a week later
        with unittest.mock.patch.object(gate_fusion_rpc, 'time', later):
            code, out, err = self.demo.gate('scan')
        self.assertEqual((code, len(self.fusion.calls_of('fusion_scan'))), (0, 2))       # scanned again
        self.assertEqual(gate_common.load_census(self.root)['parts'][BRACKET]['sig'], sigs[BRACKET])
        self.assertIn('CHANGED  demo/bracket', out)
        self.assertIn('got no answer within 30 s (budget 20 s) and left no result that can be used (the result file is', out)
        self.assertIn('nothing ties an answer of fusion_scan to the model as it is now', out)

    def test_a_scan_unit_over_its_budget_is_said(self):
        self.fusion.clear('fusion_scan', None)
        self.fusion.set_facts('fusion_scan', None, self.scan_facts(Bracket='v1', Cover='v1'), elapsed_s=28.0)
        self.fusion.now = 0.0
        code, out, err = self.demo.gate('scan')
        line = 'fusion_scan on demo: call 1 worked 28 s of 20 s (8 s over). A defect of the probe: report it'
        self.assertEqual(code, 0); self.assertIn('OVER BUDGET  u-0001 ' + line, out)
        self.assertIn(line, self.demo.gate('status')[1]); self.assertIn(line, self.demo.gate('doctor')[1])

    def test_an_audit_does_not_inherit_a_part_whose_unit_was_given_up(self):
        """An audit records what exists and blocks nothing. A part one of whose units was given up was not looked at in
        full: it is not taken into the baseline (it would never be measured again by `run --changed`), and the run is no pass."""
        self.late_inserts(facts={}, n=2)
        audit = lambda: self.demo.gate('run', '--all', '--no-scan', '--audit', '--wall', '600')
        for _ in (1, 2):
            self.assertEqual(audit()[0], 4)
            self.fusion.finish(write=False)
        code, out, err = audit()
        self.assertEqual(code, 1, out + err)
        self.assertIn('not inherited (a unit was given up over its budget, so nothing judged its rules): demo/bracket', out)
        self.assertIn('fusion_inserts on demo/bracket: NOT RUN', out)
        base = gate_report.baseline(self.root)
        self.assertIn(COVER, base); self.assertNotIn(BRACKET, base)                      # the part that was judged in full is inherited, the other is not
        self.assertEqual(gate_plan.given_up(self.root, BRACKET, self.sigs[BRACKET]), ['fusion_inserts'])
        self.assertEqual(gate_plan.given_up(self.root, COVER, self.sigs[COVER]), [])


if __name__ == '__main__':
    unittest.main()
