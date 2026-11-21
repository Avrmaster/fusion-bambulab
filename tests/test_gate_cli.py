"""gate.py as a whole, driven through its command line with the fake Fusion: the resumable run, the exit codes 0 to 4,
waivers, the audit, the owner commands, a document that is not open, a Fusion that hangs, and the export that earns a stamp.

No Fusion, no Bambu Studio: tests/fake_fusion.DemoProject wires the fake transport, its clock and the small demo registry."""
import os, re, sys, unittest
for _k in ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID'): os.environ.pop(_k, None)      # gate.py owner from an agent session needs the hook's ticket: a test is no agent session
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_fusion, gate_common, gate_features, gate_plan, gate_report
from probes import stl_walls

BRACKET, COVER = 'demo/bracket', 'demo/cover'
POCKET = gate_features.anchor('insert_pocket', (0, 0, 1), (12, -30, 40))
SLIVER = gate_features.anchor('face', (0, 0, -1), (3, 3, 0))
REASON = 'the bore of the bought bearing is 1.8 mm from the pocket and cannot move'


def thin(wall=2.5, by=()):
    return {'pockets': [{'anchor': POCKET, 'wall': wall, 'by': list(by)}]}


def no_walls(test):
    """The demo registry has no wall rule: the wall probe (another package, a minute of pure Python on a real part) is
    replaced by a stand-in that measures nothing. The mesh probe and the mesh identity are the real ones."""
    p = mock.patch.object(stl_walls, 'measure', lambda stl, report_under, budget_s=60.0, seed=1: {
        'stl': stl, 'triangles': 12, 'coverage': 1.0, 'report_under': report_under, 'budget_s': budget_s, 'elapsed_s': 0.0, 'samples': 0, 'regions': []})
    p.start(); test.addCleanup(p.stop)


class Cli(unittest.TestCase):
    QUESTIONS = ()

    def setUp(self):
        self.demo = fake_fusion.DemoProject(self, part_questions=self.QUESTIONS)
        self.root, self.fusion, self.gate = self.demo.root, self.demo.fusion, self.demo.gate

    def run_all(self, *argv):
        """`gate.py run ...` with time for everything. Returns (code, out, err)."""
        return self.gate('run', '--wall', '600', *argv)

    def finding_id(self, out, rule):
        return re.search(r'%s  .*\[(F-[0-9a-f]{6})\]' % re.escape(rule), out).group(1)

    def latest(self):
        return gate_report.load_latest(self.root)

    def scans(self):
        return len(self.fusion.calls_of('fusion_scan'))


class Resumable(Cli):
    def test_a_run_exits_3_until_done_and_sums_rows_over_calls(self):
        self.demo.facts('fusion_inserts', BRACKET, thin(), elapsed_s=12.0)
        self.demo.facts('fusion_thin', COVER, {'faces': [{'anchor': SLIVER, 'width': 0.3}]}, elapsed_s=12.0)
        self.demo.model({'Bracket': 'v1', 'Cover': 'v1'}, elapsed_s=12.0)                 # every call into Fusion takes 12 s; --wall is 20
        seen, opens, confirm = [], [], 0
        for _ in range(20):
            code, out, err = self.gate('run')
            self.assertEqual(err, '')
            if code != 3:
                break
            last = out.strip().splitlines()[-1]
            if last == 'NEXT 0/1':                                                        # the units are done over several calls: one more scan
                confirm += 1; self.assertIn('the next call scans once more', out)
                continue
            self.assertRegex(last, r'^NEXT \d+/8$')                                       # the last line printed
            seen.append(int(last.split()[1].split('/')[0])); opens.append(self.latest()['totals']['block_open'])
            self.assertEqual(self.latest()['exit'], 3)
        self.assertEqual(seen, [0, 1, 2, 3, 4, 5, 6, 7])                                  # the scan, then one unit per call
        self.assertEqual(confirm, 1)                                                      # ... then the call that scans again and sends no unit
        self.assertEqual(opens, [0, 0, 0, 0, 1, 1, 1, 1])                                 # the bracket's row is there as soon as its units are done
        self.assertEqual(code, 1)
        self.assertIn('2 parts, 2 open (1 FAIL, 1 GROW), 0 questions, 0 not run -> exit 1', out)
        self.assertIn('GROW    demo/bracket  inserts.wall  pocket wall 2.5 under 4.0', out); self.assertIn('FAIL    demo/cover  walls.thin_faces', out)
        self.assertEqual((self.scans(), len(self.fusion.calls)), (2, 10))                 # nothing ran twice; the second scan found nothing new
        report = self.latest()
        self.assertEqual((report['exit'], [p['units'] for p in report['parts']]), (1, [{'done': 4, 'total': 4}] * 2))
        self.assertTrue(os.path.isfile(self.demo.path('gate', 'reports', report['run'] + '.md')))
        self.assertEqual(self.gate('status')[0], 1); self.assertEqual(self.gate('report')[0], 1)

    def test_an_unfinished_run_is_continued_and_a_finished_one_starts_with_a_new_scan(self):
        self.demo.model({'Bracket': 'v1'}, elapsed_s=12.0)
        self.assertEqual(self.gate('run')[0], 3); self.assertEqual(self.gate('run')[0], 3)
        first = gate_plan.load_plan(self.root)['run']
        self.assertEqual((self.scans(), gate_plan.progress(gate_plan.load_plan(self.root))), (1, (1, 4)))
        code, out, err = self.gate('run', '--all')                                        # another request: a new plan, a new scan
        self.assertEqual(code, 3); self.assertNotEqual(gate_plan.load_plan(self.root)['run'], first); self.assertEqual(self.scans(), 2)
        going = gate_plan.load_plan(self.root)['run']
        self.assertEqual(self.run_all('--all')[0], 3)                                     # the same request went on with its plan ...
        self.assertEqual((self.scans(), gate_plan.load_plan(self.root)['run']), (2, going))
        n = len(self.fusion.calls)
        self.assertEqual(self.run_all('--all')[0], 0)                                     # ... measured over several calls: one more scan, no unit
        self.assertEqual((self.scans(), len(self.fusion.calls)), (3, n + 1))
        done = gate_plan.load_plan(self.root)['run']
        self.assertEqual(self.run_all('--all')[0], 0)
        self.assertNotEqual(gate_plan.load_plan(self.root)['run'], done); self.assertEqual(self.scans(), 4)

    def test_a_wall_too_short_for_one_unit_is_said(self):
        self.demo.model({'Bracket': 'v1'})
        code, out, err = self.gate('run', '--wall', '5')
        self.assertEqual(code, 4); self.assertIn('shorter than the budget of one unit', err); self.assertEqual(self.fusion.calls, [])

    def test_no_scan_takes_the_census_as_it_is(self):
        self.demo.model({'Bracket': 'v1'})
        self.assertEqual(self.gate('scan')[0], 0)
        self.assertEqual(self.run_all('--no-scan')[0], 0); self.assertEqual(self.scans(), 1)

    def test_a_part_edited_while_the_run_was_going_is_scanned_again(self):
        self.demo.model({'Bracket': 'v1', 'Cover': 'v1'})
        newer = fake_fusion.sig_of('Bracket v2')
        self.demo.facts('fusion_inserts', BRACKET, thin(), sig=newer)                     # the body is no longer the one the scan saw
        code, out, err = self.run_all()
        self.assertEqual(code, 3); self.assertEqual(out.strip().splitlines()[-1], 'NEXT 0/1'); self.assertIn('changed in Fusion while the run was going: demo/bracket', out)
        self.fusion.clear('fusion_scan', None)
        self.fusion.set_facts('fusion_scan', None, {'parts': [fake_fusion.scan_part('Bracket', newer), fake_fusion.scan_part('Cover', fake_fusion.sig_of('Cover v1'))], 'refs': []})
        self.demo.facts('fusion_inserts', BRACKET, thin())
        code, out, err = self.run_all()
        self.assertEqual(code, 1); self.assertEqual(self.scans(), 2)
        self.assertEqual(gate_common.load_census(self.root)['parts'][BRACKET]['sig'], newer)
        self.assertEqual([p['sig12'] for p in self.latest()['parts'] if p['part'] == BRACKET], [newer[:12]])


class ExitCodes(Cli):
    def test_0_pass_and_a_run_never_writes_a_stamp(self):
        self.demo.model({'Bracket': 'v1'})
        code, out, err = self.run_all()
        self.assertEqual((code, err), (0, '')); self.assertIn('1 parts, 0 open, 0 questions, 0 not run -> exit 0', out)
        self.assertEqual(self.latest()['parts'][0]['verdict'], 'pass')
        self.assertFalse(os.path.exists(self.demo.path('gate', 'stamps')))                # a stamp is earned by the export, not by the run
        self.assertEqual(self.gate('status')[0], 0)

    def test_1_open_block_findings(self):
        self.demo.model({'Bracket': 'v1'}); self.demo.facts('fusion_inserts', BRACKET, thin())
        code, out, err = self.run_all()
        self.assertEqual(code, 1); self.assertFalse(os.path.exists(self.demo.path('gate', 'stamps')))

    def test_3_more_calls_needed(self):
        self.demo.model({'Bracket': 'v1'}, elapsed_s=15.0)
        code, out, err = self.gate('run')
        self.assertEqual(code, 3); self.assertEqual(out.strip().splitlines()[-1], 'NEXT 0/4')

    def test_4_fusion_does_not_answer(self):
        self.fusion.down = True
        code, out, err = self.run_all()
        self.assertEqual(code, 4); self.assertIn('GATE ERROR  Fusion does not answer', err)
        self.assertEqual(self.gate('scan')[0], 4)

    def test_4_a_broken_parameter_file_and_an_unknown_part(self):
        self.demo.model({'Bracket': 'v1'})
        self.assertEqual(self.run_all('--part', 'demo/typed_by_hand')[0], 4)
        gate_common.write_json(gate_common.gate_path(self.root, 'owner', 'params.json'), {'inserts.wall_target': {'value': 2.0, 'tag': 'D', 'source': 'agent', 'date': '2026-03-04'}})
        code, out, err = self.run_all()
        self.assertEqual(code, 4); self.assertIn('is looser than the default 4.0', err)

    def test_4_usage_errors(self):
        for argv in (['run', '--changed', '--all'], ['run', '--wall', 'soon'], ['owner', 'waive', 'F-000000'], ['attest', 'answer', 'Q-1', '--label', 'a', '--na', 'b']):
            self.assertEqual(self.gate(*argv)[0], 4, argv)

    def test_commands_of_other_modules_are_handed_over(self):
        code, out, err = self.gate('verify')
        if code == 4 and 'is not built yet' in err:
            self.skipTest('verify is not built yet')
        self.assertEqual((code, err), (4, '')); self.assertIn('no .3mf project is registered', out); self.assertIn('NOTHING was verified', out)
        code, out, err = self.gate('params', 'check')
        self.assertEqual(code, 0); self.assertIn('TOTAL 0', out)

    def test_the_scan_command(self):
        self.demo.model({'Bracket': 'v1', 'Cover': 'v1'}, elapsed_s=25.0)
        code, out, err = self.gate('scan')
        self.assertEqual((code, err), (0, '')); self.assertIn('NEW      demo/bracket', out); self.assertIn('scan: 2 parts, 2 new, 0 changed, 0 gone, 0 document(s) not open', out)
        self.fusion.clear('fusion_scan', None)
        self.fusion.set_facts('fusion_scan', None, {'parts': [fake_fusion.scan_part('Bracket', fake_fusion.sig_of('b2'))], 'refs': []}, done=False, cursor=9, elapsed_s=25.0)
        self.fusion.set_facts('fusion_scan', None, {'parts': [], 'refs': []}, done=True)
        code, out, err = self.gate('scan', '--doc', 'demo')
        self.assertEqual(code, 3); self.assertEqual(out.strip().splitlines()[-1], 'NEXT 0/1')
        self.assertIn('scan: not finished, 0 of 1 document(s) read', out)                 # no counts yet: they would be the ones of the old census ...
        self.assertNotIn('scan: 2 parts', out); self.assertNotIn(' new, ', out)           # ... and would read like a result
        code, out, err = self.gate('scan', '--doc', 'demo')
        self.assertEqual(code, 0); self.assertIn('CHANGED  demo/bracket', out); self.assertIn('GONE     demo/cover', out)
        self.assertEqual(self.gate('scan', '--doc', 'nope')[0], 4)

    def test_a_first_scan_of_many_parts_names_a_dozen_and_counts_the_rest(self):
        self.demo.model({'Rib %02d' % i: 'v1' for i in range(40)})
        code, out, err = self.gate('scan')
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(sum(1 for line in out.splitlines() if line.startswith('NEW ') and 'demo/rib_' in line), gate_plan.SCAN_LINES)
        self.assertIn('NEW      ... and 28 more (40 in all; gate/census.json lists every part)', out)
        self.assertIn('scan: 40 parts, 40 new, 0 changed, 0 gone, 0 document(s) not open', out)
        self.assertLess(len(out.splitlines()), 20)                                        # it was one line per part


class Questions(Cli):
    QUESTIONS = ('process.concept_geometry',)

    def test_2_questions_open_then_0_once_answered(self):
        self.demo.model({'Bracket': 'v1'})
        code, out, err = self.run_all()
        self.assertEqual(code, 2); self.assertIn('0 open, 1 questions', out); self.assertIn('gate.py attest list', out)
        self.assertEqual(self.latest()['parts'][0]['verdict'], 'attest')
        qid = re.search(r'^(Q-[0-9a-f]{6})', self.gate('attest', 'list')[1], re.M).group(1)
        self.assertEqual(self.gate('attest', 'answer', qid, '--label', fake_fusion.NONE_FINAL)[0], 0)
        calls = len(self.fusion.calls)
        code, out, err = self.gate('run', '--offline')
        self.assertEqual(code, 0); self.assertEqual(len(self.fusion.calls), calls)        # judged again from the stored facts, nothing asked of Fusion
        part = self.latest()['parts'][0]
        self.assertEqual((part['verdict'], part['attested']), ('pass_attested', 1))
        self.assertIn('## demo/bracket: pass_attested(1)', self.gate('report')[1]); self.assertIn('### Answers this verdict rests on', self.gate('report')[1])

    def test_a_block_row_outranks_a_question(self):
        self.demo.model({'Bracket': 'v1'}); self.demo.facts('fusion_inserts', BRACKET, thin())
        self.assertEqual(self.run_all()[0], 1)


class Waivers(Cli):
    def blocked(self):
        self.demo.model({'Bracket': 'v1'}); self.demo.facts('fusion_inserts', BRACKET, thin(1.8, ['ref bearing 6005']))
        code, out, err = self.run_all()
        self.assertEqual(code, 1)
        return self.finding_id(out, 'inserts.wall')

    def test_a_waived_finding_passes(self):
        fid = self.blocked()
        code, out, err = self.gate('owner', 'waive', fid, '--reason', REASON, '--quote', 'leave these two as they are')
        self.assertEqual((code, err), (0, '')); self.assertIn('W-1 written to gate/owner/waivers.json', out)
        w = self.demo.read('gate', 'owner', 'waivers.json')['waivers'][0]
        f = next(x for x in self.latest()['findings'] if x['id'] == fid)
        self.assertEqual((w['id'], w['rule'], w['part'], w['anchor'], w['context'], w['reason'], w['quote']),
                         ('W-1', 'inserts.wall', BRACKET, f['anchor'], f['context'], REASON, 'leave these two as they are'))      # taken from the finding
        self.assertRegex(w['date'], r'^\d{4}-\d\d-\d\d$')
        code, out, err = self.gate('run', '--offline')
        self.assertEqual(code, 0); self.assertIn('1 waived', out)
        row = next(x for x in self.latest()['findings'] if x['id'] == fid)
        self.assertEqual((row['state'], row['waiver'], self.latest()['waivers'][0]['state']), ('waived', 'W-1', 'valid'))
        self.assertEqual(self.gate('owner', 'waive', fid, '--reason', REASON, '--quote', 'again')[0], 4)        # not open any more

    def test_a_waiver_whose_context_changed_does_not_pass(self):
        fid = self.blocked()
        self.assertEqual(self.gate('owner', 'waive', fid, '--reason', REASON, '--quote', 'leave it')[0], 0)
        self.assertEqual(self.gate('run', '--offline')[0], 0)
        self.demo.model({'Bracket': 'v2'}); self.demo.facts('fusion_inserts', BRACKET, thin(1.4, ['ref bearing 6005']))      # the same pocket, thinner
        code, out, err = self.run_all()
        self.assertEqual(code, 1); self.assertIn('pocket wall 1.4 under 4.0', out)
        self.assertEqual([(w['waiver'], w['state']) for w in self.latest()['waivers']], [('W-1', 'expired')])
        self.assertNotEqual(self.finding_id(out, 'inserts.wall'), fid)
        self.demo.model({'Bracket': 'v3'}); self.demo.facts('fusion_inserts', BRACKET, thin(1.8, ['ref bearing 6005']))      # back to what was waived, in a new signature
        self.assertEqual(self.run_all()[0], 0)

    def test_an_export_order_is_refused_and_nothing_is_written(self):
        fid = self.blocked()
        for reason in ('yes, export', 'ok print it', ''):
            code, out, err = self.gate('owner', 'waive', fid, '--reason', reason, '--quote', 'go')
            self.assertEqual(code, 4, reason); self.assertIn('nothing written', err)
        code, out, err = self.gate('owner', 'waive', fid, '--reason', REASON, '--quote', ' ')
        self.assertEqual(code, 4); self.assertIn('owner\'s own words', err)
        self.assertFalse(os.path.exists(self.demo.path('gate', 'owner', 'waivers.json')))
        code, out, err = self.gate('owner', 'waive', 'F-123456', '--reason', REASON, '--quote', 'q')
        self.assertEqual(code, 4); self.assertIn('not an open finding', err)


class Trouble(Cli):
    def test_a_timeout_stops_the_loop_with_exit_4_and_no_second_call(self):
        self.demo.model({'Bracket': 'v1', 'Cover': 'v1'})
        self.fusion.set_timeout('fusion_inserts', BRACKET, alive_after=False)             # Fusion hangs in the second unit
        code, out, err = self.run_all()
        self.assertEqual(code, 4); self.assertIn('does not answer a 5 s ping', err); self.assertIn('nothing else was sent', err)
        self.assertEqual([(j['probe'], j['part']) for j in self.fusion.calls], [('fusion_scan', None), ('fusion_census', BRACKET), ('fusion_inserts', BRACKET)])
        self.assertEqual(self.fusion.pings, 1)
        plan = gate_plan.load_plan(self.root)
        self.assertEqual([u['state'] for u in plan['units']][:3], ['done', 'todo', 'todo'])     # saved: the next call goes on from here
        self.assertFalse(os.path.exists(self.demo.path('gate', 'run', 'lock')))
        self.fusion.clear('fusion_inserts', BRACKET); self.fusion.alive_after_timeout = True; self.demo.facts('fusion_inserts', BRACKET, {'pockets': []})
        code, out, err = self.run_all()                                                   # Fusion is back: the plan goes on ...
        self.assertEqual((code, self.scans(), gate_plan.load_plan(self.root)['run']), (3, 1, plan['run']))
        self.assertEqual((self.run_all()[0], self.scans()), (0, 2))                       # ... and one scan more says nothing moved meanwhile

    def test_a_unit_that_fails_leaves_its_rule_not_run_and_the_part_not_passed(self):
        self.demo.model({'Bracket': 'v1'})
        self.fusion.clear('fusion_thin', BRACKET); self.fusion.set_error('fusion_thin', BRACKET, 'RuntimeError: boom')
        code, out, err = self.run_all()
        self.assertEqual(code, 1); self.assertIn('NOT_RUN demo/bracket  walls.thin_faces', out); self.assertIn('1 not run', out)

    def test_a_part_with_nothing_but_checks_that_could_not_decide_is_open_and_says_that_nothing_failed(self):
        self.demo.model({'Bracket': 'v1'})
        self.fusion.clear('fusion_thin', BRACKET); self.fusion.set_error('fusion_thin', BRACKET, 'RuntimeError: boom')
        code, out, err = self.run_all()
        self.assertEqual(code, 1)                                                         # never a pass, and no question round either
        self.assertIn('1 parts, 1 open (1 NOT_RUN), 0 questions, 1 not run -> exit 1', out)
        self.assertIn('nothing failed: 1 check(s) could not decide or did not run. They count as open and nothing has passed', out)
        report = self.latest()
        self.assertEqual((report['parts'][0]['verdict'], report['totals']['by_verdict']), ('incomplete', {'FAIL': 0, 'BLOCKED': 0, 'GROW': 0, 'NOT_RUN': 1}))
        md = self.gate('report', '--last')[1]
        self.assertIn('## demo/bracket: incomplete', md); self.assertIn('### Could not decide: these count as open', md); self.assertNotIn('### Open: these stop the stamp', md)
        self.assertEqual(self.gate('status')[0], 1)
        code, out, err = self.gate('export', BRACKET)
        self.assertEqual(code, 1); self.assertIn('not every check ran on this geometry', out)       # and nothing is exported on it
        self.demo.facts('fusion_inserts', BRACKET, thin()); self.demo.model({'Bracket': 'v2'})
        self.fusion.clear('fusion_thin', BRACKET); self.fusion.set_error('fusion_thin', BRACKET, 'RuntimeError: boom')
        code, out, err = self.run_all()                                                   # with a measured row beside it the part has failed
        self.assertEqual(code, 1); self.assertIn('2 open (1 GROW, 1 NOT_RUN)', out); self.assertNotIn('nothing failed', out)
        self.assertEqual(self.latest()['parts'][0]['verdict'], 'fail')

    def test_many_equal_rows_are_one_line_of_the_run_and_the_json_holds_them_all(self):
        pockets = [{'anchor': gate_features.anchor('insert_pocket', (0, 0, 1), (x, 0, 5)), 'wall': 2.0, 'by': []} for x in range(0, 600, 10)]
        self.demo.facts('fusion_inserts', BRACKET, {'pockets': pockets}); self.demo.facts('fusion_thin', COVER, {'faces': [{'anchor': SLIVER, 'width': 0.3}]})
        self.demo.model({'Bracket': 'v1', 'Cover': 'v1'})
        code, out, err = self.run_all()
        self.assertEqual(code, 1); self.assertIn('2 parts, 61 open (1 FAIL, 60 GROW), 0 questions, 0 not run -> exit 1', out)
        self.assertRegex(out, r'GROW    demo/bracket  inserts.wall  x60  pocket wall 2.0 under 4.0  \[F-[0-9a-f]{6} \+59 more\]')
        lines = out.splitlines()
        self.assertLess(len(lines), 10)                                                   # it was one line per row
        self.assertLess(out.index('FAIL    demo/cover'), out.index('GROW    demo/bracket'))        # what failed before what is free to grow
        report = self.latest()
        self.assertEqual(len({f['id'] for f in report['findings']}), 61)                  # every row and its id: a waiver is per feature
        md = self.gate('report', '--last')[1]
        self.assertIn('inserts.wall GROW x60', md); self.assertLess(len(md.splitlines()), 60)
        self.assertEqual(md.count('first: Make the whole wall wider where the outline is free.'), 1)

    def test_a_scan_that_fails_is_exit_4(self):
        self.fusion.set_error('fusion_scan', None, 'RuntimeError: boom')
        code, out, err = self.run_all()
        self.assertEqual(code, 4); self.assertIn('the scan did not hold', err)


class NotOpen(Cli):
    def setUp(self):
        super().setUp()
        self.demo.layout['documents'].append({'key': 'other', 'name': 'Other Assembly', 'stl_dir': 'stl_other'})
        gate_common.write_json(gate_common.gate_path(self.root, 'layout.json'), self.demo.layout)
        self.fusion.documents = {'Other Assembly': 3}                                     # first only the other document is open
        self.sigs = self.demo.model({'Lid': 'v1', 'Tray': 'v1'}, doc_key='other')
        self.assertEqual(self.gate('scan')[0], 0)
        self.stamp = gate_common.stamp_path(self.root, 'other/lid', self.sigs['other/lid'])
        fake_fusion.whole_stamp(self.root, 'other/lid', self.sigs['other/lid'])
        self.fusion.documents = {'Demo Assembly': 12}                                     # now it is closed, and the demo document is open
        self.demo.model({'Bracket': 'v1'})

    def test_its_parts_are_unreachable_and_keep_their_stamps(self):
        before = gate_common.read_json(self.stamp)
        code, out, err = self.run_all()
        self.assertEqual((code, err), (0, ''))                                            # the part that could be reached passed
        self.assertIn("UNREACHABLE  other: document 'Other Assembly' is not open", out); self.assertIn('2 parts, 0 open, 0 questions, 5 not run, 1 unreachable -> exit 0', out)
        report = self.latest()
        self.assertEqual({p['part']: (p['verdict'], p['state']) for p in report['parts']}, {BRACKET: ('pass', 'new'), 'other/tray': ('unreachable', 'unreachable')})
        self.assertEqual(report['unreachable'], ["other: document 'Other Assembly' is not open"])
        census = gate_common.load_census(self.root)
        self.assertEqual((census['docs']['other']['open'], census['parts']['other/lid']['gone'], census['parts']['other/lid']['sig']), (False, False, self.sigs['other/lid']))
        self.assertEqual(gate_common.read_json(self.stamp), before)                       # the old stamp stays as it was
        self.assertEqual(gate_plan.changed_parts(self.root), [BRACKET, 'other/tray'])
        self.assertFalse(os.path.exists(gate_common.facts_path(self.root, 'other/tray', self.sigs['other/tray'])))
        self.assertEqual([j['part'] for j in self.fusion.calls if j['part'] and j['part'].startswith('other/')], [])      # no unit was sent for its parts
        self.assertEqual(self.gate('status')[0], 1); self.assertIn('documents not open at the last scan: other', self.gate('status')[1])

    def test_a_named_part_that_cannot_be_reached_is_exit_4_never_a_pass(self):
        code, out, err = self.run_all('--part', 'other/tray')
        self.assertEqual(code, 4); self.assertIn('nothing was checked for other/tray', out); self.assertEqual(self.latest()['exit'], 4)
        self.assertFalse(os.path.exists(os.path.dirname(gate_common.stamp_path(self.root, 'other/tray', self.sigs['other/tray']))))


class Audit(Cli):
    def audited(self):
        self.demo.model({'Bracket': 'v1', 'Cover': 'v1'})
        self.demo.facts('fusion_inserts', BRACKET, thin()); self.demo.facts('fusion_thin', COVER, {'faces': [{'anchor': SLIVER, 'width': 0.3}]})
        return self.run_all('--audit')

    def test_an_audit_never_blocks_and_proposes_the_triage_list(self):
        code, out, err = self.audited()
        self.assertEqual((code, err), (0, '')); self.assertIn('0 open, 0 questions, 0 not run, 2 inherited -> exit 0 (audit: nothing blocks)', out)
        report = self.latest()
        self.assertEqual((report['audit'], report['exit'], sorted(f['state'] for f in report['findings'])), (True, 0, ['inherited', 'inherited']))
        prop = self.demo.read('gate', 'proposals', 'triage.json')
        self.assertEqual((prop['kind'], prop['data']['adopted']['run'], [i['id'] for i in prop['data']['items']]), ('triage', report['run'], ['T-0001', 'T-0002']))
        self.assertFalse(os.path.exists(self.demo.path('gate', 'owner', 'triage.json')))  # a proposal; the owner's file is his
        self.assertEqual(self.gate('triage')[0], 1)
        self.assertEqual(self.run_all()[0], 1)                                            # not adopted: an ordinary run still fails on them

    def test_two_audits_of_different_parts_add_up_in_one_proposal(self):
        self.demo.model({'Bracket': 'v1', 'Cover': 'v1'})
        self.demo.facts('fusion_inserts', BRACKET, thin()); self.demo.facts('fusion_thin', COVER, {'faces': [{'anchor': SLIVER, 'width': 0.3}]})
        self.assertEqual(self.run_all('--audit', '--part', BRACKET)[0], 0)
        first = self.demo.read('gate', 'proposals', 'triage.json')['data']
        self.assertEqual(([i['part'] for i in first['items']], sorted(first['baseline'])), ([BRACKET], [BRACKET]))
        code, out, err = self.run_all('--audit', '--part', COVER)
        self.assertEqual(code, 0); self.assertIn('triage proposal: gate/proposals/triage.json (2 items in 2 groups', out)
        both = self.demo.read('gate', 'proposals', 'triage.json')['data']
        self.assertEqual([(i['id'], i['part']) for i in both['items']], [('T-0001', BRACKET), ('T-0002', COVER)])      # the bracket's row was not lost
        self.assertEqual((sorted(both['baseline']), both['baseline'][BRACKET]), ([BRACKET, COVER], first['baseline'][BRACKET]))
        self.assertEqual(self.run_all('--audit', '--part', BRACKET)[0], 0)               # the same audit again: the same rows under the same numbers
        self.assertEqual([(i['id'], i['part']) for i in self.demo.read('gate', 'proposals', 'triage.json')['data']['items']], [('T-0001', BRACKET), ('T-0002', COVER)])
        code, out, err = self.gate('owner', 'adopt', '--quote', 'this is the model as it stands')
        self.assertEqual((code, err), (0, '')); self.assertIn('2 row(s) and 2 part(s) added', out)
        owner = self.demo.read('gate', 'owner', 'triage.json')
        self.assertEqual((len(owner['items']), sorted(owner['baseline']), owner['quotes'][0]['quote']), (2, [BRACKET, COVER], 'this is the model as it stands'))
        self.assertEqual(self.gate('owner', 'adopt', '--quote', 'again')[0], 0)          # nothing new to take over: nothing is doubled
        self.assertEqual(len(self.demo.read('gate', 'owner', 'triage.json')['items']), 2)

    def test_a_check_that_could_not_decide_in_an_audit_is_open_not_inherited(self):
        self.demo.model({'Bracket': 'v1'})
        self.fusion.clear('fusion_thin', BRACKET); self.fusion.set_error('fusion_thin', BRACKET, 'RuntimeError: boom')
        code, out, err = self.run_all('--audit')
        self.assertEqual(code, 0); self.assertIn('1 open (1 NOT_RUN)', out); self.assertIn('check(s) could not decide or did not run', out)
        data = self.demo.read('gate', 'proposals', 'triage.json')['data']
        self.assertEqual((data['items'], [(r['part'], r['rule']) for r in data['not_run']]), ([], [(BRACKET, 'walls.thin_faces')]))
        self.assertEqual(self.latest()['parts'][0]['verdict'], 'incomplete')
        md = self.gate('triage', '--md')[1]
        self.assertIn('## Checks that could not decide: 1', md)
        code, out, err = self.gate('owner', 'adopt', '--quote', 'take it')
        self.assertEqual(code, 0)
        code, out, err = self.gate('export', BRACKET)
        self.assertEqual(code, 1); self.assertIn('not every check ran on this geometry', out)       # adopted or not: what did not run never passes into a stamp

    def test_an_audit_that_needs_more_calls_still_says_so(self):
        self.demo.model({'Bracket': 'v1'}, elapsed_s=15.0)
        self.assertEqual(self.gate('run', '--audit')[0], 3)

    def test_the_owners_first_decision_adopts_the_list_and_inherited_rows_stop_only_their_export(self):
        self.audited()
        code, out, err = self.gate('owner', 'decide', 'T-0001', 'tolerate', '--quote', 'it has held for a year', '--note', 'next reprint')
        self.assertEqual((code, err), (0, '')); self.assertIn('adopted the audit', out); self.assertIn('T-0001 = tolerate (inserts.wall on demo/bracket)', out); self.assertIn('1 row(s) not decided', out)
        owner = self.demo.read('gate', 'owner', 'triage.json')
        self.assertEqual([(i['id'], i['decision'], i['quote'], i['note']) for i in owner['items']],
                         [('T-0001', 'tolerate', 'it has held for a year', 'next reprint'), ('T-0002', None, None, None)])
        sent = len(self.fusion.calls)
        code, out, err = self.run_all()
        self.assertEqual(code, 0); self.assertIn('nothing to check: no part changed since its stamp, or since the audit that judged it', out)
        self.assertEqual([j['probe'] for j in self.fusion.calls[sent:]], ['fusion_scan']) # adopted and unchanged: not measured again on every run
        code, out, err = self.run_all('--all')
        self.assertEqual(code, 0); self.assertIn('2 inherited', out)                      # inherited rows are listed, they are not new blocks
        self.assertEqual(gate_report.inherited_block(self.root, BRACKET), [])
        self.assertEqual([i['id'] for i in gate_report.inherited_block(self.root, COVER)], ['T-0002'])
        self.assertEqual(self.gate('owner', 'decide', 'T-0002', 'fix', '--quote', 'fix the sliver')[0], 0)
        self.assertEqual(self.gate('triage')[0], 0)
        self.assertEqual([i['decision'] for i in gate_report.inherited_block(self.root, COVER)], ['fix'])
        self.assertEqual(self.gate('owner', 'decide', 'T-0009', 'fix', '--quote', 'q')[0], 4)
        self.demo.model({'Bracket': 'v2', 'Cover': 'v1'}); self.demo.facts('fusion_inserts', BRACKET, thin(2.0))       # new damage after the audit
        code, out, err = self.run_all()
        self.assertEqual(code, 1); self.assertIn('pocket wall 2.0 under 4.0', out)        # ... is an ordinary block, not triage

    def test_a_later_audit_cannot_slip_new_rows_into_the_owners_list(self):
        self.audited()
        self.assertEqual(self.gate('owner', 'decide', 'T-0001', 'tolerate', '--quote', 'it holds')[0], 0)
        self.demo.model({'Bracket': 'v2', 'Cover': 'v1'}); self.demo.facts('fusion_inserts', BRACKET, thin(2.0))
        code, out, err = self.run_all('--audit')
        self.assertEqual(code, 1); self.assertIn('1 open (1 GROW)', out)                   # the bracket changed after the audit: an audit does not inherit it again
        self.assertIn('not inherited (changed after the audit, or stamped): demo/bracket', out); self.assertIn('what changed after the audit blocks as usual', out)
        prop = self.demo.read('gate', 'proposals', 'triage.json')['data']
        self.assertEqual([i['id'] for i in prop['items']], ['T-0001', 'T-0002'])            # the new damage is no triage row, also not in the proposal
        self.assertEqual(prop['baseline'][BRACKET]['sig'], self.demo.read('gate', 'owner', 'triage.json')['baseline'][BRACKET]['sig'])     # the baseline did not move
        self.assertEqual(self.gate('owner', 'decide', 'T-0003', 'tolerate', '--quote', 'fine')[0], 4)      # not in the owner's list
        self.assertEqual(len(self.demo.read('gate', 'owner', 'triage.json')['items']), 2)
        self.assertEqual(self.run_all()[0], 1)

    def test_a_part_that_is_new_after_the_adoption_is_never_born_inherited(self):
        self.demo.model({'Bracket': 'v1'}); self.demo.facts('fusion_inserts', BRACKET, thin())
        self.assertEqual(self.run_all('--audit')[0], 0)
        self.assertEqual(self.gate('owner', 'adopt', '--quote', 'take the model as it is today')[0], 0)
        self.demo.model({'Bracket': 'v1', 'Cover': 'v1'}); self.demo.facts('fusion_thin', COVER, {'faces': [{'anchor': SLIVER, 'width': 0.3}]})   # a new part, with a defect
        code, out, err = self.run_all('--audit', '--part', COVER)
        self.assertEqual((code, err), (1, '')); self.assertIn('1 open (1 FAIL)', out); self.assertNotIn('1 inherited', out)
        self.assertIn('not inherited (judged after the owner adopted his list): demo/cover', out); self.assertIn('only `gate.py owner adopt', out)
        report = self.latest()
        row, = report['parts']
        self.assertEqual((row['part'], row['inherited_part'], row['proposed_part'], row['verdict'], row['waiting']), (COVER, False, True, 'fail', 0))
        self.assertEqual([(f['state'], f.get('proposed')) for f in report['findings'] if f['verdict'] == 'FAIL'], [('open', True)])
        self.assertEqual(report['totals']['waiting'], 0)                                    # nothing of it waits as the questions of an inherited part do
        prop = self.demo.read('gate', 'proposals', 'triage.json')['data']
        self.assertEqual(([(i['id'], i['part']) for i in prop['items']], sorted(prop['baseline'])), ([('T-0001', BRACKET), ('T-0002', COVER)], [BRACKET, COVER]))
        self.assertEqual(sorted(self.demo.read('gate', 'owner', 'triage.json')['baseline']), [BRACKET])      # the owner's list did not grow by itself
        self.assertFalse(gate_report.is_inherited(self.root, COVER, gate_common.load_census(self.root)['parts'][COVER]['sig']))
        self.assertEqual(self.run_all()[0], 1)                                              # an ordinary run: the same open row
        self.assertIn('a later audit proposes 1 more row(s) and 1 more part(s)', self.gate('triage')[1])
        code, out, err = self.gate('owner', 'adopt', '--quote', 'the cover too')            # only the owner makes it inherited
        self.assertEqual((code, err), (0, '')); self.assertIn('1 row(s) and 1 part(s) added', out)
        code, out, err = self.run_all('--audit', '--part', COVER)
        self.assertEqual(code, 0); self.assertIn('1 inherited', out)

    def test_a_proposal_entry_with_no_audit_report_behind_it_makes_nothing_inherited(self):
        self.demo.model({'Bracket': 'v1'}); self.demo.facts('fusion_inserts', BRACKET, thin())
        self.assertEqual(self.run_all()[0], 1)
        sig = gate_common.load_census(self.root)['parts'][BRACKET]['sig']
        forged = {'kind': 'triage', 'data': {'adopted': {'run': 'r-none'}, 'items': [], 'baseline': {BRACKET: {'sig': sig, 'run': 'r-none', 'time': 't'}}}}
        gate_common.write_json(gate_common.gate_path(self.root, 'proposals', 'triage.json'), forged)       # written by hand: no audit ever ran
        self.assertEqual((gate_report.baseline(self.root)[BRACKET]['live'], gate_report.is_inherited(self.root, BRACKET, sig)), (False, False))
        v = gate_plan.part_verdict(self.root, self.demo.registry(), BRACKET, defer_inherited=True)
        self.assertEqual((v['inherited_part'], v['deferred_questions']), (False, []))
        self.assertEqual(self.run_all('--audit', '--part', BRACKET)[0], 0)                  # a real audit of the same geometry: its report stands behind its entry
        forged['data']['baseline'][BRACKET]['run'] = self.latest()['run']
        gate_common.write_json(gate_common.gate_path(self.root, 'proposals', 'triage.json'), forged)
        gate_report._AUDITS_HERE.clear()                                                    # as another process sees it: only the report file counts
        self.assertTrue(gate_report.is_inherited(self.root, BRACKET, sig))
        self.assertFalse(gate_report.is_inherited(self.root, BRACKET, 'f' * 64))            # and only for the signature that report lists

    def test_decide_without_an_audit(self):
        code, out, err = self.gate('owner', 'decide', 'T-0001', 'fix', '--quote', 'q')
        self.assertEqual(code, 4); self.assertIn('run --audit', err)


class OwnerParam(Cli):
    def test_a_parameter_set_by_the_owner_is_used_by_the_next_judgement(self):
        self.demo.model({'Bracket': 'v1'}); self.demo.facts('fusion_inserts', BRACKET, thin(4.2))
        self.assertEqual(self.run_all()[0], 0)
        code, out, err = self.gate('owner', 'param', 'inserts.wall_target', '4.5', '--tag', 'U', '--source', 'owner, chat', '--quote', 'make it 4.5')
        self.assertEqual((code, err), (0, '')); self.assertIn('inserts.wall_target = 4.5 mm [U owner, chat', out)
        entry = self.demo.read('gate', 'owner', 'params.json')['inserts.wall_target']
        self.assertEqual((entry['value'], entry['tag'], entry['source'], entry['quote']), (4.5, 'U', 'owner, chat', 'make it 4.5')); self.assertRegex(entry['date'], r'^\d{4}-\d\d-\d\d$')
        calls = len(self.fusion.calls)
        code, out, err = self.gate('run', '--offline')
        self.assertEqual((code, len(self.fusion.calls)), (1, calls)); self.assertIn('pocket wall 4.2 under 4.5', out)
        self.assertEqual(next(f for f in self.latest()['findings'])['limit_text'], '4.5 mm [U owner, chat %s]' % entry['date'])

    def test_a_looser_value_without_m_or_u_is_refused_and_nothing_is_written(self):
        gate_common.write_json(gate_common.gate_path(self.root, 'owner', 'params.json'), {'_about': 'owner file'})
        Q = ['--quote', 'three is enough there']
        for argv in (['inserts.wall_target', '3.0', '--tag', 'D', '--source', 'agent'] + Q, ['inserts.wall_tarmac', '3.0', '--tag', 'U', '--source', 'x'] + Q,
                     ['inserts.wall_target', 'thick', '--tag', 'U', '--source', 'x'] + Q, ['inserts.wall_target', '4.0', '--tag', 'U', '--source', 'x', '--key', 'M3'] + Q,
                     ['inserts.wall_target', '3.0', '--tag', 'M', '--source', 'fit ladder, 12 pockets melted'],            # no quote: not the owner's words
                     ['inserts.wall_target', '3.0', '--tag', 'M', '--source', 'fit ladder, 12 pockets melted', '--quote', '  ']):
            code, out, err = self.gate('owner', 'param', *argv)
            self.assertEqual(code, 4, argv)
        self.assertEqual(self.demo.read('gate', 'owner', 'params.json'), {'_about': 'owner file'})
        os.rename(self.demo.path('.claude', 'settings.json'), self.demo.path('.claude', 'settings.off'))                 # no ask rule: no prompt, no write
        code, out, err = self.gate('owner', 'param', 'inserts.wall_target', '3.0', '--tag', 'M', '--source', 'fit ladder, 12 pockets melted', *Q)
        self.assertEqual(code, 4); self.assertIn('nothing in this project puts a prompt in front of him', err); self.assertIn('Bash(*gate.py owner *)', err)
        self.assertEqual(self.demo.read('gate', 'owner', 'params.json'), {'_about': 'owner file'})
        os.rename(self.demo.path('.claude', 'settings.off'), self.demo.path('.claude', 'settings.json'))
        self.assertEqual(self.gate('owner', 'param', 'inserts.wall_target', '3.0', '--tag', 'M', '--source', 'fit ladder, 12 pockets melted', *Q)[0], 0)
        self.assertEqual(self.demo.read('gate', 'owner', 'params.json')['inserts.wall_target']['quote'], 'three is enough there')
        self.assertEqual(sorted(self.demo.read('gate', 'owner', 'params.json')), ['_about', 'inserts.wall_target'])      # the comment key is kept


    def test_a_value_for_one_size(self):
        code, out, err = self.gate('owner', 'param', 'inserts.min_thread', '4.5', '--tag', 'U', '--source', 'owner, bench', '--key', 'M4', '--quote', 'M4 gets 4.5')
        self.assertEqual((code, err), (0, '')); self.assertIn('inserts.min_thread[M4] = 4.5 mm [U owner, bench', out)
        entry = self.demo.read('gate', 'owner', 'params.json')['inserts.min_thread']
        self.assertEqual((entry['value'], entry['per'], entry['tag']), (3.2, {'M4': 4.5}, 'U'))
        code, out, err = self.gate('owner', 'param', 'inserts.min_thread', '3.0', '--tag', 'D', '--source', 'agent', '--key', 'M4', '--quote', 'q')
        self.assertEqual(code, 4); self.assertIn('inserts.min_thread[M4] = 3.0 is looser than the default 4.0', err)
        self.assertEqual(self.demo.read('gate', 'owner', 'params.json')['inserts.min_thread']['per'], {'M4': 4.5})


class Intent(Cli):
    def test_a_part_that_changed_outside_the_stated_intent_fails(self):
        self.demo.model({'Bracket': 'v1', 'Cover': 'v1'})
        code, out, err = self.run_all('--part', BRACKET, '--parts', BRACKET)
        self.assertEqual(code, 1); self.assertIn('FAIL    demo/cover  regression.unintended  changed outside the stated intent', out)
        self.assertEqual((self.latest()['intent'], sorted(p['part'] for p in self.latest()['parts'])), ([BRACKET], [BRACKET, COVER]))      # every changed part is looked at
        self.assertEqual(self.run_all('--part', BRACKET, '--parts', '%s,%s' % (BRACKET, COVER))[0], 0)
        self.assertEqual(self.run_all()[0], 0)


class Export(Cli):
    """The whole chain with the real stamp, STL and mesh modules of the other packages: gate.py run, attest, export."""
    QUESTIONS = ('process.concept_geometry', 'print.bed_face')

    def setUp(self):
        super().setUp(); no_walls(self)
        self.sig = self.demo.model({'Bracket': 'v1'})[BRACKET]
        self.fusion.set_export(BRACKET, fake_fusion.cube_stl())
        self.stl = self.demo.path('stl', 'bracket.stl')

    def export(self):
        code, out, err = self.gate('export', BRACKET)
        if code == 4 and 'is not built yet' in err:
            self.skipTest('the export of the stamp package is not built yet: %s' % err.strip())
        return code, out, err

    def stamps(self):
        folder = os.path.dirname(gate_common.stamp_path(self.root, BRACKET, self.sig))
        return sorted(os.listdir(folder)) if os.path.isdir(folder) else []

    def answer_all(self, *argv):
        for qid, rule in re.findall(r'^(Q-[0-9a-f]{6})  \S+  (\S+)', self.gate('attest', 'list', *argv)[1], re.M):
            label = fake_fusion.NONE_FINAL if rule == 'process.concept_geometry' else fake_fusion.BED_BOTTOM
            self.assertIn(self.gate('attest', 'answer', qid, '--label', label)[0], (0, 2))

    def test_a_stamp_is_written_only_on_a_pass(self):
        self.assertEqual(self.export()[0], 1); self.assertEqual(self.stamps(), [])       # the export scans first: a known part then, never checked
        self.assertEqual(self.gate('scan')[0], 0)
        self.assertEqual(self.export()[0], 1); self.assertEqual(self.stamps(), [])       # scanned, never checked: nothing to stamp
        self.demo.facts('fusion_inserts', BRACKET, thin())
        self.assertEqual(self.run_all()[0], 1)
        code, out, err = self.export()
        self.assertEqual(code, 1); self.assertIn('NOT exported', out)                     # open block findings
        self.assertEqual((self.stamps(), os.path.exists(self.stl), self.fusion.calls_of('fusion_export')), ([], False, []))
        self.demo.model({'Bracket': 'v2'}); self.demo.facts('fusion_inserts', BRACKET, {'pockets': []})
        self.sig = fake_fusion.sig_of('Bracket v2')
        self.assertEqual(self.run_all()[0], 2)
        self.assertEqual(self.export()[0], 2); self.assertEqual((self.stamps(), os.path.exists(self.stl)), ([], False))      # questions open
        self.answer_all()
        self.assertEqual(self.gate('run', '--offline')[0], 0)
        code, out, err = self.export()
        self.assertEqual((code, err), (0, '')); self.assertIn('stamped -> stl/bracket.stl', out)
        self.assertEqual(self.stamps(), [self.sig[:12] + '.json']); self.assertTrue(os.path.isfile(self.stl))
        stamp = gate_common.read_json(gate_common.stamp_path(self.root, BRACKET, self.sig))
        self.assertEqual((stamp['part'], stamp['sig'], stamp['verdict'], stamp['stl'], stamp['stl_sha256']),
                         (BRACKET, self.sig, 'pass_attested', 'stl/bracket.stl', gate_common.sha256_file(self.stl)))
        self.assertEqual(self.fusion.read_only[-1], False)                                # the export unit is the one that is not read-only
        st = gate_report.status(self.root)
        self.assertEqual((st['changed'], st['open'], st['questions'], st['unstamped_stls'], st['stl_states']), ([], [], [], [], {'stl/bracket.stl': 'valid'}))
        self.assertEqual(self.gate('status')[0], 0)
        code, out, err = self.run_all()                                                   # nothing changed since the stamp
        self.assertEqual(code, 0); self.assertIn('nothing to check', out)

    def test_the_stamp_dies_with_the_geometry(self):
        self.assertEqual(self.run_all()[0], 2); self.answer_all(); self.assertEqual(self.gate('run', '--offline')[0], 0)
        self.assertEqual(self.export()[0], 0)
        self.assertEqual(gate_plan.changed_parts(self.root), [])
        self.demo.model({'Bracket': 'v2'})
        self.assertEqual(self.gate('scan')[0], 0)
        self.assertEqual(gate_plan.changed_parts(self.root), [BRACKET])
        st = gate_report.status(self.root)
        self.assertEqual((st['unchecked'], st['stl_states']), ([BRACKET], {'stl/bracket.stl': 'stale'}))

    def test_a_run_after_a_refused_export_is_not_a_pass(self):
        self.assertEqual(self.run_all()[0], 2); self.answer_all(); self.assertEqual(self.gate('run', '--offline')[0], 0)
        self.fusion.clear('fusion_export', BRACKET); self.fusion.set_export(BRACKET, fake_fusion.cube_stl(hole=True))       # the mesh came out open
        code, out, err = self.export()
        self.assertEqual(code, 1); self.assertIn('open edges', out); self.assertEqual(self.stamps(), [])
        for argv in ((), ('--offline',)):                                                 # the design checks pass again; the STL of this geometry still does not
            code, out, err = self.run_all(*argv)
            self.assertEqual(code, 1, argv); self.assertIn('FAIL    demo/bracket  mesh.closed', out)
        self.assertEqual(self.gate('status')[0], 1)

    def test_an_inherited_row_without_a_decision_stops_the_export_of_its_part(self):
        self.demo.facts('fusion_inserts', BRACKET, thin())
        self.assertEqual(self.run_all('--audit')[0], 0)
        self.assertEqual(self.gate('owner', 'decide', 'T-0001', 'fix', '--quote', 'widen that wall')[0], 0)
        self.assertEqual(self.gate('run', '--offline')[0], 0)                             # the run does not fail on an inherited row ...
        code, out, err = self.gate('attest', 'list')
        self.assertEqual(code, 0); self.assertIn('0 question(s) open', out)               # ... and asks nothing about a part nobody changed
        self.assertIn('2 question(s) of 1 inherited part(s) wait', out)                   # its design question and its export question
        code, out, err = self.export()
        self.assertEqual(code, 2); self.assertIn('question(s) open', out); self.assertEqual(self.stamps(), [])       # the export asks: no stamp without the answers
        self.answer_all('--part', BRACKET)
        code, out, err = self.export()
        self.assertEqual(code, 1); self.assertIn('inherited findings wait', out); self.assertEqual(self.stamps(), [])        # ... and holds the undecided row


class Generated(Cli):
    """A part made by code: no Fusion at all, from its STL to its stamp."""
    QUESTIONS = ('print.bed_face',)

    def setUp(self):
        super().setUp(); no_walls(self)
        self.demo.layout['generators'] = [{'part': 'demo/shim_05', 'stl': 'stl/shim_05.stl', 'source': 'tools/make_shims.py'}]
        gate_common.write_json(gate_common.gate_path(self.root, 'layout.json'), self.demo.layout)
        os.makedirs(self.demo.path('stl'))
        self.fusion.down = True

    def write(self, **kw):
        with open(self.demo.path('stl', 'shim_05.stl'), 'wb') as f: f.write(fake_fusion.cube_stl(**kw))
        return gate_common.sibling('meshid').stl_ids(self.demo.path('stl', 'shim_05.stl'))['geom_hash']

    def export(self):
        code, out, err = self.gate('export', '--generated', 'stl/shim_05.stl')
        if code == 4 and 'is not built yet' in err:
            self.skipTest('the export of the stamp package is not built yet: %s' % err.strip())
        return code, out, err

    def test_checked_and_stamped_with_fusion_down(self):
        sig = self.write(a=5.0, hole=True)
        code, out, err = self.export()
        self.assertEqual(code, 1); self.assertIn('open edges', out)                       # an open mesh: no stamp
        self.assertFalse(os.path.exists(gate_common.stamp_path(self.root, 'demo/shim_05', sig)))
        sig = self.write(a=5.0)
        self.assertEqual(self.export()[0], 2)                                             # closed now; the export-stage question is open
        code, out, err = self.gate('attest', 'list')
        qid = re.search(r'^(Q-[0-9a-f]{6})  demo/shim_05  print.bed_face', out, re.M).group(1)
        self.assertEqual(self.gate('attest', 'answer', qid, '--label', fake_fusion.BED_BOTTOM)[0], 0)
        code, out, err = self.export()
        self.assertEqual((code, err), (0, ''))
        stamp = gate_common.read_json(gate_common.stamp_path(self.root, 'demo/shim_05', sig))
        self.assertEqual((stamp['part'], stamp['sig'], stamp['geom_hash'], stamp['verdict']), ('demo/shim_05', sig, sig, 'pass_attested'))
        self.assertEqual(self.fusion.calls, [])
        self.assertEqual(self.gate('scan')[0], 4)                                         # Fusion is down for the document ...
        e = gate_common.load_census(self.root)['parts']['demo/shim_05']                   # ... the part made by code is in the census all the same
        self.assertEqual((e['source'], e['sig']), ('generated', sig)); self.assertEqual(gate_plan.changed_parts(self.root), [])


if __name__ == '__main__':
    unittest.main()
