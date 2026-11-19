"""gate_report: the report of a run (json + md), the status and the session-start digest from files only, the triage
list for the owner (groups first, then by part: what stops each row, the fix order; inherited against new), equal rows
as one group, the baseline of inherited parts.

No Fusion (tests/fake_fusion.py) and the small demo registry, so nothing here depends on the shipped rules."""
import json, os, sys, time, unittest
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_fusion, gate_common, gate_features, gate_judges, gate_params, gate_plan, gate_report

BRACKET, COVER = 'demo/bracket', 'demo/cover'
P1 = gate_features.anchor('insert_pocket', (0, 0, 1), (12, -30, 40))
P2 = gate_features.anchor('insert_pocket', (0, 0, 1), (-12, -30, 40))
SLIVER = gate_features.anchor('face', (0, 0, -1), (3, 3, 0))
HOLE = gate_features.anchor('hole', (0, 1, 0), (5, 10, 3))


class Reported(unittest.TestCase):
    """Bracket: two thin pocket walls (one stopped by a bought part) and an unknown hole. Cover: one sliver face."""
    QUESTIONS = ('process.concept_geometry',)

    def setUp(self):
        self.demo = fake_fusion.DemoProject(self, part_questions=self.QUESTIONS)
        self.root, self.reg = self.demo.root, self.demo.registry()
        self.demo.facts('fusion_inserts', BRACKET, {'pockets': [{'anchor': P1, 'wall': 2.5, 'by': []}, {'anchor': P2, 'wall': 1.8, 'by': ['ref bearing 6005']}]})
        self.demo.facts('fusion_census', BRACKET, {'holes': [{'id': 'h1', 'anchor': HOLE, 'd': 2.7, 'class': None}], 'openings': [], 'counts': {}})
        self.demo.facts('fusion_thin', COVER, {'faces': [{'anchor': SLIVER, 'width': 0.3}]})
        self.sigs = self.demo.model({'Bracket': 'v1', 'Cover': 'v1'})
        gate_common.write_json(gate_common.gate_path(self.root, 'owner', 'params.json'),
                               {'inserts.wall_target': {'value': 4.0, 'tag': 'U', 'source': 'owner 2026-03-04', 'date': '2026-03-04'}})
        self.resolved = gate_params.resolve(self.reg, gate_params.load_overrides(self.root), None)
        gate_plan.scan(self.root, wall_s=600)
        self.plan = gate_plan.new_plan(self.root, self.reg, [BRACKET, COVER])
        gate_plan.run_units(self.root, self.reg, self.resolved, self.plan, wall_s=600)
        for p in (BRACKET, COVER):
            gate_plan.judge_part(self.root, self.reg, self.resolved, p)

    def verdicts(self):
        return [gate_plan.part_verdict(self.root, self.reg, p) for p in (BRACKET, COVER)]

    def report(self, **kw):
        return gate_report.build(self.root, self.reg, self.resolved, self.plan['run'], 'run', 'design', self.verdicts(), plan=self.plan, **kw)

    def audit(self):
        """The report of an audit: the audit notes the geometry it judged (the baseline), so the parts are inherited."""
        for p in (BRACKET, COVER):
            gate_report.baseline_add(self.root, 'r-audit', p, self.sigs[p])
        return self.report(audit=True)

    def adopt(self, decisions=None):
        """The owner's triage list: the findings of an audit report, with these decisions {finding rule + measure: decision}."""
        items = gate_report.triage_items(self.audit())
        for i in items:
            i['decision'] = (decisions or {}).get((i['rule'], i['measure']))
        base = {p: e for p, e in gate_common.read_json(gate_common.gate_path(self.root, 'proposals', 'triage.json'), {})['data']['baseline'].items()}
        gate_common.write_json(gate_common.gate_path(self.root, 'owner', 'triage.json'),      # what `gate.py owner adopt` writes: the rows and the parts of the audit
                               {'adopted': {'run': self.plan['run'], 'time': gate_common.now()}, 'items': items, 'baseline': base})
        return items


class Report(Reported):
    def test_the_report_holds_what_the_interface_promises(self):
        r = self.report(intent=[BRACKET], unreachable=['other: document \'Other\' is not open'])
        self.assertEqual(sorted(r), sorted(['run', 'command', 'stage', 'time', 'mode', 'audit', 'intent', 'parts', 'findings', 'questions', 'waivers', 'deferred',
                                            'unreachable', 'params', 'totals', 'exit', 'groups']))
        self.assertEqual((r['run'], r['command'], r['stage'], r['mode'], r['audit'], r['intent'], r['exit']), (self.plan['run'], 'run', 'design', 'observe', False, [BRACKET], 1))
        self.assertEqual(r['totals'], {'block_open': 3, 'attest_open': 3, 'waived': 0, 'inherited': 0, 'not_run': 0, 'info': 0,
                                       'by_verdict': {'FAIL': 1, 'BLOCKED': 1, 'GROW': 1, 'NOT_RUN': 0}, 'by_facts': 0, 'waiting': 0})
        self.assertEqual(sorted(i for g in r['groups'] for i in g['findings']), sorted(f['id'] for f in r['findings']))     # every row is in one group
        b, c = r['parts']
        self.assertEqual((b['part'], b['sig12'], b['state'], b['verdict'], b['units'], b['open'], b['asked'], b['waived'], b['not_run']),
                         (BRACKET, self.sigs[BRACKET][:12], 'new', 'fail', {'done': 4, 'total': 4}, 2, 2, 0, []))
        self.assertEqual((c['verdict'], c['open'], c['asked']), ('fail', 1, 1))
        self.assertEqual(r['deferred'], ['teeth.profile']); self.assertEqual(r['unreachable'], ['other: document \'Other\' is not open'])
        self.assertEqual([p['name'] for p in r['params']], ['inserts.wall_target', 'walls.min_face_width'])
        self.assertEqual(r['params'][0], {'name': 'inserts.wall_target', 'value': 4.0, 'tag': 'U', 'from': 'project'})

    def test_every_finding_has_an_id_a_state_and_its_limit_with_the_tag(self):
        r = self.report()
        rows = {(f['rule'], f['measure']): f for f in r['findings']}
        grow, blocked, hole, sliver = rows[('inserts.wall', 2.5)], rows[('inserts.wall', 1.8)], rows[('census.unknown_hole', 2.7)], rows[('walls.thin_faces', 0.3)]
        self.assertEqual((grow['state'], grow['verdict'], grow['waiver'], grow['limit_text']), ('open', 'GROW', None, '4.0 mm [U owner 2026-03-04]'))
        self.assertEqual((blocked['verdict'], blocked['by']), ('BLOCKED', ['ref bearing 6005']))
        self.assertEqual((hole['state'], hole['ask'], hole['limit_text']), ('asked', 'census.unknown_hole', None))
        self.assertIn('default measured on', sliver['limit_text'])
        for f in r['findings']:
            self.assertEqual(f['id'], gate_features.finding_id(f)); self.assertIn(f['of'], (BRACKET, COVER))
        self.assertEqual(len({f['id'] for f in r['findings']}), 4)
        self.assertEqual(sorted(q['rule'] for q in r['questions']), ['census.unknown_hole', 'process.concept_geometry', 'process.concept_geometry'])

    def test_waived_answered_and_expired_show_as_such(self):
        f = next(x for x in self.verdicts()[0]['open'] if x['measure'] == 1.8)
        w = {'id': 'W-1', 'rule': f['rule'], 'part': BRACKET, 'anchor': f['anchor'], 'context': f['context'],
             'reason': 'the bore of the bought bearing is 1.8 mm from the pocket and cannot move', 'quote': 'leave it', 'date': '2026-03-04'}
        gate_common.write_json(gate_common.gate_path(self.root, 'owner', 'waivers.json'), {'waivers': [w, dict(w, id='W-2', context='0' * 16)]})
        r = self.report()
        waived = next(x for x in r['findings'] if x['measure'] == 1.8)
        self.assertEqual((waived['state'], waived['waiver']), ('waived', 'W-1'))
        self.assertEqual([(x['waiver'], x['state']) for x in r['waivers']], [('W-1', 'valid'), ('W-2', 'expired')])
        self.assertEqual((r['totals']['block_open'], r['totals']['waived']), (2, 1))
        md = gate_report.render_md(r)
        self.assertIn('W-2 inserts.wall on demo/bracket: expired', md); self.assertIn('### Waived by the owner', md)

    def test_an_audit_records_open_findings_as_inherited_and_never_blocks(self):
        r = self.audit()
        self.assertEqual((r['exit'], r['audit']), (0, True))
        self.assertEqual(sorted(f['state'] for f in r['findings']), ['asked', 'inherited', 'inherited', 'inherited'])
        self.assertEqual((r['totals']['block_open'], r['totals']['inherited']), (0, 3))
        self.assertIn('(audit: nothing blocks)', gate_report.summary_line(r))

    def test_the_summary_line(self):
        r = self.report()
        self.assertEqual(gate_report.summary_line(r), 'gate run %s: 2 parts, 3 open (1 FAIL, 1 BLOCKED, 1 GROW), 3 questions, 0 not run -> exit 1' % self.plan['run'])
        r['exit'] = 3
        self.assertTrue(gate_report.summary_line(r).endswith('-> exit 3 (more calls needed)'))
        r['totals'].update(block_open=2, by_verdict={'FAIL': 0, 'BLOCKED': 0, 'GROW': 0, 'NOT_RUN': 2}, not_run=2, by_facts=4, waiting=9, attest_open=1)
        self.assertIn('2 parts, 2 open (2 NOT_RUN), 1 questions (4 answered by facts), 2 not run, 9 questions wait (inherited parts) -> exit 3', gate_report.summary_line(r))
        r['totals'].update(block_open=0, by_verdict={'FAIL': 0, 'BLOCKED': 0, 'GROW': 0, 'NOT_RUN': 0}, not_run=0, by_facts=0, waiting=0, attest_open=0)
        self.assertIn('2 parts, 0 open, 0 questions, 0 not run -> exit 3', gate_report.summary_line(r))     # nothing open: nothing to split

    def test_files_and_the_markdown(self):
        r = self.report()
        jpath, mpath = gate_report.write(self.root, r)
        self.assertEqual((gate_common.rel(self.root, jpath), gate_common.rel(self.root, mpath)),
                         ('gate/reports/%s.json' % r['run'], 'gate/reports/%s.md' % r['run']))
        self.assertEqual(gate_common.read_json(jpath), json.loads(json.dumps(r))); self.assertEqual(gate_report.load_latest(self.root)['run'], r['run'])
        with open(mpath, encoding='utf-8') as f: md = f.read()
        self.assertEqual(md, gate_report.render_md(r))
        for text in ('## demo/bracket: fail', '### Open: these stop the stamp', 'inserts.wall GROW**: 2.5 mm against 4.0 mm [U owner 2026-03-04]',
                     'where: insert_pocket at (12, -30, 40), axis (0, 0, 1)', 'stopped by: ref bearing 6005', 'first: Make the whole wall wider where the outline is free.',
                     'then: Move the pocket with its screw.', '### Questions open', 'process.concept_geometry**: Is any functional shape', '"%s"' % fake_fusion.NONE_FINAL,
                     '## Registered, not checked yet', 'teeth.profile', '| inserts.wall_target | 4.0 | U | project |'):
            self.assertIn(text, md)
        self.assertFalse([n for n in os.listdir(os.path.dirname(jpath)) if 'tmp' in n])

    def test_no_report_yet(self):
        os.makedirs(gate_common.gate_path(self.root, 'reports'), exist_ok=True)
        self.assertIsNone(gate_report.load_latest(self.root))
        code, out, err = self.demo.gate('report')
        self.assertEqual(code, 4); self.assertIn('no report yet', err)
        gate_report.write(self.root, self.report())
        code, out, err = self.demo.gate('report', '--last')
        self.assertEqual(code, 1); self.assertIn('## demo/cover: fail', out)                 # the command returns the report's own exit code

    def test_a_report_of_project_findings_lists_them_under_their_project(self):
        f = gate_judges.finding('project.stl_without_stamp', BRACKET, 'FAIL', 'stl/bracket.stl has no stamp')
        v = {'part': 'main:', 'sig': None, 'verdict': 'fail', 'open': [f], 'asked': [], 'answered': [], 'waived': [], 'info': [], 'not_run': [], 'waivers': [],
             'questions': [], 'attested': 0}
        r = gate_report.build(self.root, self.reg, self.resolved, 'r-verify', 'verify', 'project', [v])
        self.assertEqual((r['exit'], r['parts'][0]['part'], r['parts'][0]['units'], r['findings'][0]['of'], r['params']), (1, 'main:', {'done': 0, 'total': 0}, 'main:', []))
        md = gate_report.render_md(r)
        self.assertIn('## main:: fail', md); self.assertIn('stl/bracket.stl has no stamp', md); self.assertIn('part: demo/bracket', md)
        self.assertTrue(gate_report.summary_line(r).startswith('gate verify r-verify: 1 parts, 1 open'))


class Triage(Reported):
    def test_items_are_numbered_grouped_and_carry_what_stops_them(self):
        items = gate_report.triage_items(self.audit())
        self.assertEqual([i['id'] for i in items], ['T-0001', 'T-0002', 'T-0003'])
        self.assertEqual([(i['rule'], i['part'], i['measure']) for i in items], [('inserts.wall', BRACKET, 1.8), ('inserts.wall', BRACKET, 2.5), ('walls.thin_faces', COVER, 0.3)])
        i = items[0]
        self.assertEqual((i['group'], i['verdict'], i['limit'], i['limit_text'], i['by'], i['decision'], i['quote'], i['note'], i['date']),
                         ('inserts.wall demo/bracket', 'BLOCKED', 4.0, '4.0 mm [U owner 2026-03-04]', ['ref bearing 6005'], None, None, None, None))
        self.assertEqual(i['fix'][0], 'Make the whole wall wider where the outline is free.'); self.assertEqual(i['anchor'], P2)
        self.assertRegex(i['finding'], '^F-[0-9a-f]{6}$'); self.assertRegex(i['context'], '^[0-9a-f]{16}$')
        self.assertNotIn('census.unknown_hole', [x['rule'] for x in items])                  # a question is answered, not triaged

    def test_decisions_already_made_are_kept(self):
        first = gate_report.triage_items(self.audit())[:2]
        first[1].update(decision='tolerate', quote='it holds', date='2026-03-05')
        again = gate_report.triage_items(self.audit(), existing=first)
        self.assertEqual([(i['id'], i['decision']) for i in again], [('T-0001', None), ('T-0002', 'tolerate'), ('T-0003', None)])
        self.assertEqual(again[2]['rule'], 'walls.thin_faces')

    def test_the_owners_page_groups_first_then_by_part(self):
        items = gate_report.triage_items(self.audit())
        items[1].update(decision='tolerate', quote='it has held for a year', note='reprint with the next change', date='2026-03-05')
        md = gate_report.render_triage_md(items)
        gid = {i['measure']: i['gid'] for i in items}
        for text in ('# Triage: 3 inherited findings in 3 groups, 2 not decided', '## Groups, blockers first',
                     '| %s | walls.thin_faces | FAIL | 1 | 1 OPEN | demo/cover (1) | 0.3 mm |' % gid[0.3],
                     '| %s | inserts.wall | BLOCKED | 1 | 1 OPEN | demo/bracket (1) | 1.8 mm | 4.0 mm [U owner 2026-03-04] |' % gid[1.8],
                     '| %s | inserts.wall | GROW | 1 | 1 tolerate | demo/bracket (1) | 2.5 mm |' % gid[2.5],
                     '## By part', '### demo/bracket: 2 rows, 1 stop its next export', '**inserts.wall**: 2 rows, limit 4.0 mm [U owner 2026-03-04]',
                     '- BLOCKED %s (1 OPEN): 1.8 mm' % gid[1.8], '  - stopped by: ref bearing 6005', '  - rows: at (-12, -30, 40) T-0001 1.8 mm',
                     '- GROW %s (1 tolerate): 2.5 mm' % gid[2.5], 'nothing: free to fix', '## Fix orders', '1. Make the whole wall wider where the outline is free.',
                     '2. Move the pocket with its screw.', '- T-0002 decided tolerate on 2026-03-05: "it has held for a year" (reprint with the next change)',
                     '`fix`:', '`waive`:', '`tolerate`:', '`rule_wrong`:', 'gate.py owner decide T-0001', 'gate.py owner decide G-'):
            self.assertIn(text, md)
        self.assertLess(md.index('| FAIL |'), md.index('| BLOCKED |')); self.assertLess(md.index('| BLOCKED |'), md.index('| GROW |'))     # blockers first
        self.assertEqual(md.count('1. Make the whole wall wider'), 1)                       # once per rule, not once per row
        self.assertIn('No rows.', gate_report.render_triage_md([]))

    def test_forty_parts_with_the_same_kind_of_row_are_one_line_for_the_owner(self):
        base = gate_report.triage_items(self.audit())[0]
        items = []
        for k in range(40):
            for n in range(6):
                anchor = gate_features.anchor('insert_pocket', (0, 0, 1), (10 * n, 0, 5))
                items.append(dict(base, id='T-%04d' % (len(items) + 1), part='demo/plate_%02d' % k, anchor=anchor, measure=round(2.2 + 0.01 * n, 2),
                                  text='pocket wall %s under 4.0' % round(2.2 + 0.01 * n, 2), cause=None, gid=None, finding='F-%06x' % len(items)))
        md = gate_report.render_triage_md(items)
        table = md[md.index('## Groups, blockers first'):md.index('## By part')]
        self.assertEqual(sum(1 for line in table.splitlines() if line.startswith('| G-')), 1)               # 240 rows, one line to read
        self.assertIn('| inserts.wall | BLOCKED | 240 | 240 OPEN |', table); self.assertIn('2.2 .. 2.25 mm', table); self.assertIn('demo/plate_39 (6)', table)
        self.assertEqual(md.count('1. Make the whole wall wider'), 1)                       # the fix order of a rule: once in the whole list
        self.assertLess(len(md.splitlines()), 40 * 10)                                      # by part: a handful of lines each, not one block per row

    def test_a_check_that_could_not_decide_is_no_triage_row(self):
        r = self.audit()
        stuck = gate_judges.finding('walls.thin_faces', COVER, 'NOT_RUN', 'the declared target does not say the layer height: the owner declares it in gate/project.json')
        r['findings'].append(dict(stuck, id='F-bbbbbb', state='open', of=COVER))
        items = gate_report.triage_items(r)
        self.assertNotIn('NOT_RUN', [i['verdict'] for i in items]); self.assertEqual(len(items), 3)        # it cannot be tolerated: it has to run
        gate_report.write_proposal(self.root, r)
        data = self.demo.read('gate', 'proposals', 'triage.json')['data']
        self.assertEqual([(x['part'], x['rule']) for x in data['not_run']], [(COVER, 'walls.thin_faces')])
        md = gate_report.render_triage_md(data['items'], data['not_run'])
        self.assertIn('## Checks that could not decide: 1', md); self.assertIn('- **walls.thin_faces** x1: the declared target does not say the layer height', md)
        self.assertIn('a check that did not run has not passed, and it cannot be tolerated', md)

    def test_inherited_against_new(self):
        self.adopt({('inserts.wall', 2.5): 'tolerate', ('walls.thin_faces', 0.3): 'fix'})
        b, c = self.verdicts()
        self.assertEqual((b['open'], sorted((f['measure'], f['decision']) for f in b['inherited'])), ([], [(1.8, None), (2.5, 'tolerate')]))
        self.assertEqual(b['verdict'], 'attest')                                             # only its questions are left
        self.assertEqual([i['measure'] for i in gate_report.inherited_block(self.root, BRACKET)], [1.8])       # no decision: stops this part's export
        self.assertEqual([i['decision'] for i in gate_report.inherited_block(self.root, COVER)], ['fix'])
        self.assertEqual(gate_report.inherited_block(self.root, 'demo/other'), [])
        r = self.report()
        self.assertEqual((r['totals']['block_open'], r['totals']['inherited'], r['exit']), (0, 3, 2))
        row = next(f for f in r['findings'] if f['measure'] == 2.5)
        self.assertEqual((row['state'], row['triage'], row['decision']), ('inherited', 'T-0002', 'tolerate'))
        self.assertIn('triage item T-0002, decision: tolerate', gate_report.render_md(r))

    def test_a_fixed_feature_no_longer_blocks_and_a_changed_one_is_new(self):
        self.adopt()
        self.demo.facts('fusion_inserts', BRACKET, {'pockets': [{'anchor': P1, 'wall': 4.2, 'by': []}, {'anchor': P2, 'wall': 1.5, 'by': ['ref bearing 6005']}]})
        self.sigs.update(self.demo.model({'Bracket': 'v2', 'Cover': 'v1'})); gate_plan.scan(self.root, wall_s=600)
        self.plan = gate_plan.new_plan(self.root, self.reg, [BRACKET])
        gate_plan.run_units(self.root, self.reg, self.resolved, self.plan, wall_s=600); gate_plan.judge_part(self.root, self.reg, self.resolved, BRACKET)
        v = gate_plan.part_verdict(self.root, self.reg, BRACKET)
        self.assertEqual(([f['measure'] for f in v['open']], v['inherited']), ([1.5], []))    # thinner than at the audit: an ordinary block
        self.assertEqual(gate_report.inherited_block(self.root, BRACKET), [])                # the 2.5 wall is fixed, the other row is a new finding

    def test_the_triage_command(self):
        code, out, err = self.demo.gate('triage')
        self.assertEqual(code, 0); self.assertIn('no triage list', out)
        gate_report.write(self.root, self.audit())
        gate_common.write_json(gate_common.gate_path(self.root, 'proposals', 'triage.json'),
                               {'kind': 'triage', 'time': gate_common.now(), 'why': 'audit', 'data': {'adopted': {'run': 'r-audit'}, 'items': gate_report.triage_items(self.audit())}})
        code, out, err = self.demo.gate('triage')
        self.assertEqual(code, 1); self.assertIn('triage (proposal, audit r-audit): 3 rows in 3 groups, 3 not decided', out)
        self.assertRegex(out, r'G-[0-9a-f]{6}  BLOCKED inserts.wall  x1 \(1 OPEN\)  1.8 mm  pocket wall 1.8 under 4.0')
        self.assertLess(out.index('FAIL    walls.thin_faces'), out.index('BLOCKED inserts.wall'))      # blockers first
        self.assertIn('gate.py triage --md', out)
        items = self.adopt({('inserts.wall', 2.5): 'tolerate', ('inserts.wall', 1.8): 'waive', ('walls.thin_faces', 0.3): 'fix'})
        self.assertEqual(self.demo.gate('triage')[0], 0)                                     # every row has a decision
        self.assertIn('triage (adopted', self.demo.gate('triage')[1])
        new = gate_judges.finding('walls.thin_faces', COVER, 'FAIL', 'face 0.2 wide, under 1.2', measure=0.2, limit=1.2)
        r = self.report(); r['findings'].append(dict(new, id='F-aaaaaa', state='open', limit_text='1.2 mm')); gate_report.write(self.root, r)
        code, out, err = self.demo.gate('triage', '--md')
        self.assertIn('# Triage: 3 inherited findings in 3 groups, 0 not decided', out)
        self.assertIn('## New since the audit: 1 open findings (ordinary blocks, not triage)', out)
        self.assertIn('| demo/cover | walls.thin_faces | FAIL | 1 | 0.2 mm | 1.2 mm | face 0.2 wide, under 1.2 | F-aaaaaa |', out)


class Groups(Reported):
    """Equal rows are one block for the reader; every row keeps its id in the JSON report."""

    def many(self, n=30):
        pockets = [{'anchor': gate_features.anchor('insert_pocket', (0, 0, 1), (x, 0, 5)), 'wall': 2.0 + 0.01 * (x // 10), 'by': ['ref pin #%d' % x]}
                   for x in range(0, 10 * n, 10)] + [{'anchor': P1, 'wall': 3.1, 'by': []}]
        self.demo.facts('fusion_inserts', BRACKET, {'pockets': pockets})
        self.sigs.update(self.demo.model({'Bracket': 'v2', 'Cover': 'v1'})); gate_plan.scan(self.root, wall_s=600)
        self.plan = gate_plan.new_plan(self.root, self.reg, [BRACKET, COVER])
        gate_plan.run_units(self.root, self.reg, self.resolved, self.plan, wall_s=600)
        for p in (BRACKET, COVER):
            gate_plan.judge_part(self.root, self.reg, self.resolved, p)
        return self.report()

    def test_the_cause_is_the_text_without_its_numbers_and_the_neighbour_without_its_copy_number(self):
        self.assertEqual(gate_report.cause_of('pocket wall 2.21 at (4, 0, -7.5) under 4.0'), gate_report.cause_of('pocket wall 3 at (90, 12.25, 7) under 4.0'))
        self.assertNotEqual(gate_report.cause_of('covered by Lid'), gate_report.cause_of('covered by Cover'))
        self.assertEqual(gate_report.neighbours_of(['ref pin #3', 'ref pin #12', 'ref ring (2)', 'Lid']), ['Lid', 'ref pin', 'ref ring'])
        self.assertEqual(gate_report.group_id('inserts.wall', 'GROW', 'x'), gate_report.group_id('inserts.wall', 'GROW', 'x'))
        self.assertNotEqual(gate_report.group_id('inserts.wall', 'GROW', 'x'), gate_report.group_id('inserts.wall', 'BLOCKED', 'x'))

    def test_thirty_equal_rows_are_one_block_and_the_json_keeps_every_id(self):
        r = self.many(30)
        walls = [f for f in r['findings'] if f['rule'] == 'inserts.wall']
        self.assertEqual(len(walls), 31); self.assertEqual(len({f['id'] for f in walls}), 31)             # a waiver is per feature: every id stays
        groups = [g for g in r['groups'] if g['rule'] == 'inserts.wall']
        self.assertEqual(sorted((g['verdict'], g['count']) for g in groups), [('BLOCKED', 30), ('GROW', 1)])
        big = next(g for g in groups if g['count'] == 30)
        self.assertEqual((big['by'], sorted(big['findings'])), (['ref pin'], sorted(f['id'] for f in walls if f['verdict'] == 'BLOCKED')))
        md = gate_report.render_md(r)
        self.assertIn('- **inserts.wall BLOCKED x30** (%s): 2.0 .. 2.29 mm against 4.0 mm [U owner 2026-03-04]' % big['id'], md)
        self.assertIn('  - stopped by: ref pin', md); self.assertIn('and 24 more', md); self.assertIn('every id of this group is in the JSON report', md)
        self.assertEqual(md.count('first: Make the whole wall wider where the outline is free.'), 1)       # the fix order: once per rule, not once per row
        self.assertIn('- fix order of inserts.wall, for its 31 rows above:', md)
        self.assertLess(len(md.splitlines()), 80)                                                          # it was eight lines for each of 31 rows
        self.assertLess(md.index('inserts.wall BLOCKED x30'), md.index('inserts.wall GROW'))               # what goes to the owner before what is free to fix

    def test_a_row_alone_is_printed_as_before_with_its_place(self):
        md = gate_report.render_md(self.report())
        self.assertIn('inserts.wall GROW**: 2.5 mm against 4.0 mm [U owner 2026-03-04]', md); self.assertIn('where: insert_pocket at (12, -30, 40), axis (0, 0, 1)', md)
        self.assertNotIn(' x1**', md)

    def test_what_could_not_decide_has_its_own_heading_and_no_fix_order(self):
        r = self.report()
        stuck = gate_judges.finding('walls.thin_faces', COVER, 'NOT_RUN', 'the mesh of this neighbour is known by its box only: turn it into a body, then run again')
        r['findings'].append(dict(stuck, id='F-cccccc', state='open', of=COVER, fix=['Remove the sliver.']))
        md = gate_report.render_md(r)
        part = md[md.index('## demo/cover'):]
        self.assertLess(part.index('### Open: these stop the stamp'), part.index('### Could not decide: these count as open'))
        tail = part[part.index('### Could not decide'):]
        self.assertIn('walls.thin_faces NOT_RUN', tail); self.assertNotIn('first: Remove the sliver.', tail[:tail.index('### Questions open')])


class Status(Reported):
    def stamp(self, part, stl=None):
        fake_fusion.whole_stamp(self.root, part, self.sigs[part], stl=stl,
                                stl_sha256=gate_common.sha256_file(self.demo.path(*stl.split('/'))) if stl else None)

    def stl(self, name, size=10.0):
        os.makedirs(self.demo.path('stl'), exist_ok=True)
        with open(self.demo.path('stl', name), 'wb') as f: f.write(fake_fusion.cube_stl(size))

    def test_status_from_files_only(self):
        gate_report.write(self.root, self.report())
        self.demo.fusion.down = True                                                         # nothing may ask Fusion
        calls = len(self.demo.fusion.calls)
        st = gate_report.status(self.root)
        for key in ('mode', 'parts', 'changed', 'open', 'questions', 'unstamped_stls', 'triage_open', 'last_run', 'last_scan'):
            self.assertIn(key, st)
        self.assertEqual((st['mode'], st['parts'], st['changed'], len(st['open']), len(st['questions']), st['unstamped_stls'], st['triage_open'], st['last_run']),
                         ('observe', 2, [BRACKET, COVER], 3, 3, [], 0, self.plan['run']))
        self.assertEqual((st['unchecked'], st['unfinished'], st['unreachable']), ([], None, []))
        self.assertRegex(st['last_scan'], r'^\d{4}-')
        self.assertEqual(len(self.demo.fusion.calls), calls)
        json.dumps(st)                                                                       # --json prints it as it is

    def test_a_changed_part_nobody_checked_is_said(self):
        self.sigs.update(self.demo.model({'Bracket': 'v2', 'Cover': 'v1', 'Lid': 'v1'})); gate_plan.scan(self.root, wall_s=600)
        st = gate_report.status(self.root)
        self.assertEqual((st['changed'], st['unchecked'], st['parts']), ([BRACKET, COVER, 'demo/lid'], [BRACKET, 'demo/lid'], 3))
        self.assertEqual([f['part'] for f in st['open']], [COVER])                           # the findings of the old bracket are not today's
        code, out, err = self.demo.gate('status')
        self.assertEqual(code, 1); self.assertIn('changed and not checked: demo/bracket, demo/lid', out)

    def test_stls_without_a_valid_stamp(self):
        self.stl('bracket.stl'); self.stl('cover.stl', 12.0); self.stl('stray.stl', 7.0)
        self.stamp(BRACKET, 'stl/bracket.stl'); self.stamp(COVER, 'stl/cover.stl')
        st = gate_report.status(self.root)
        self.assertEqual((st['unstamped_stls'], st['changed']), (['stl/stray.stl'], []))
        self.assertEqual(st['stl_states'], {'stl/bracket.stl': 'valid', 'stl/cover.stl': 'valid', 'stl/stray.stl': 'none'})
        self.stl('cover.stl', 12.5)                                                          # the file was written again by something else
        self.sigs.update(self.demo.model({'Bracket': 'v2', 'Cover': 'v1'})); gate_plan.scan(self.root, wall_s=600)     # ... and the bracket changed in the model
        st = gate_report.status(self.root)
        self.assertEqual(st['stl_states'], {'stl/bracket.stl': 'stale', 'stl/cover.stl': 'none', 'stl/stray.stl': 'none'})
        self.assertEqual(st['unstamped_stls'], ['stl/bracket.stl', 'stl/cover.stl', 'stl/stray.stl'])
        self.assertIn('stl/stray.stl', self.demo.read('gate', 'run', 'stl_hashes.json'))     # hashed once, then read from the cache
        with mock.patch.object(gate_common, 'sha256_file', side_effect=AssertionError('hashed again')):
            self.assertEqual(len(gate_report.status(self.root)['unstamped_stls']), 3)
        self.assertEqual(gate_report.status(self.root, hash_budget_s=-1)['stl_states']['stl/stray.stl'], 'none')
        os.remove(self.demo.path('gate', 'run', 'stl_hashes.json'))
        st = gate_report.status(self.root, hash_budget_s=-1)                                 # no time to hash: unknown, not "unstamped"
        self.assertEqual((set(st['stl_states'].values()), st['unstamped_stls']), ({'unknown'}, []))

    def test_the_status_command_and_its_exit_codes(self):
        code, out, err = self.demo.gate('status')
        self.assertEqual((code, err), (1, ''))
        self.assertIn('gate [observe]: 2 parts | 2 changed since their stamp | 3 open findings | 3 questions | 0 STLs without a valid stamp', out)
        self.assertIn('GROW    demo/bracket  inserts.wall  pocket wall 2.5 under 4.0', out); self.assertIn('ASK     demo/bracket  census.unknown_hole', out)
        self.assertIn('gate.py run --changed', out)
        code, out, err = self.demo.gate('status', '--json')
        self.assertEqual((code, json.loads(out)['parts']), (1, 2))
        self.adopt({('inserts.wall', 2.5): 'tolerate', ('inserts.wall', 1.8): 'tolerate', ('walls.thin_faces', 0.3): 'tolerate'})
        code, out, err = self.demo.gate('status')
        self.assertEqual(code, 0); self.assertIn('0 open findings | 0 questions', out)       # nothing new: the inherited parts are not asked now
        self.assertIn('3 questions wait at 2 inherited parts: asked when a part changes, and at its export', out)
        st = gate_report.status(self.root)
        self.assertEqual((st['waiting'], st['inherited_parts'], st['inherited']), (3, 2, 3))
        clean = fake_fusion.DemoProject(self, part_questions=())
        clean.model({'Bracket': 'v1'}); gate_plan.scan(clean.root, wall_s=600)
        reg = clean.registry(); res = gate_params.resolve(reg, {}, None)
        gate_plan.run_units(clean.root, reg, res, gate_plan.new_plan(clean.root, reg, [BRACKET]), wall_s=600); gate_plan.judge_part(clean.root, reg, res, BRACKET)
        self.assertEqual(clean.gate('status')[0], 0)                                         # checked, nothing open, no question

    def test_an_unfinished_run_is_named(self):
        plan = gate_plan.new_plan(self.root, self.reg, [BRACKET]); plan['units'][0]['state'] = 'done'; gate_plan.save_plan(self.root, plan)
        st = gate_report.status(self.root)
        self.assertEqual(st['unfinished'], {'run': plan['run'], 'done': 1, 'total': 4})
        self.assertIn('is not finished: 1/4 units', self.demo.gate('status')[1])

    def test_the_digest_is_short_and_ends_with_the_three_commands(self):
        text = gate_report.digest(self.root)
        lines = text.splitlines()
        self.assertLessEqual(len(lines), 15)
        self.assertTrue(lines[0].startswith('gate [observe]: 2 parts | 2 changed since their stamp | 3 open findings'))
        for command in ('gate.py status', 'gate.py run --changed', 'gate.py export PART'):
            self.assertIn(command, lines[-1])
        self.assertIn('BLOCKED demo/bracket  inserts.wall', text)
        code, out, err = self.demo.gate('status', '--digest')
        self.assertEqual(code, 1); self.assertEqual(out.strip(), text)

    def test_a_long_list_still_fits_the_digest(self):
        pockets = [{'anchor': gate_features.anchor('insert_pocket', (0, 0, 1), (x, 0, 5)), 'wall': 2.0, 'by': []} for x in range(0, 300, 10)]
        self.demo.facts('fusion_inserts', BRACKET, {'pockets': pockets})
        self.sigs.update(self.demo.model({'Bracket': 'v2', 'Cover': 'v1'})); gate_plan.scan(self.root, wall_s=600)
        gate_plan.run_units(self.root, self.reg, self.resolved, gate_plan.new_plan(self.root, self.reg, [BRACKET]), wall_s=600)
        gate_plan.judge_part(self.root, self.reg, self.resolved, BRACKET)
        lines = gate_report.digest(self.root).splitlines()
        self.assertLessEqual(len(lines), 15); self.assertIn('31 open findings', lines[0])
        text = '\n'.join(lines)                              # thirty equal rows are one line with the count and the first id
        self.assertRegex(text, r'GROW    demo/bracket  inserts.wall  x30  pocket wall 2.0 under 4.0  \[F-[0-9a-f]{6} \+29 more\]')
        self.assertEqual(sum(1 for line in lines if 'inserts.wall' in line), 1)

    def test_the_digest_never_raises_and_is_quick(self):
        t = time.time()
        gate_report.digest(self.root)
        self.assertLess(time.time() - t, 1.0)
        with open(gate_common.census_path(self.root), 'w') as f: f.write('{broken')
        text = gate_report.digest(self.root)
        self.assertTrue(text.startswith('gate: status unavailable (GateError:')); self.assertEqual(len(text.splitlines()), 1)

    def test_a_project_that_was_never_scanned(self):
        fresh = fake_fusion.DemoProject(self)
        st = gate_report.status(fresh.root)
        self.assertEqual((st['parts'], st['changed'], st['open'], st['last_run'], st['last_scan']), (0, [], [], None, None))
        self.assertIn('nothing scanned yet', gate_report.digest(fresh.root))
        self.assertEqual(fresh.gate('status')[0], 1)                                         # a registered document nobody scanned: not a pass
        gate_common.write_json(gate_common.gate_path(fresh.root, 'layout.json'), {})
        self.assertEqual(fresh.gate('status')[0], 0)                                         # nothing registered, nothing to check


if __name__ == '__main__':
    unittest.main()
