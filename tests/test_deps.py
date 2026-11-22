"""What a unit read, and `run --changed` measuring only what changed: dependency records, staleness, reuse, without Fusion.

The fake Fusion answers like probes/_fusion.py run(): every envelope says which regions of the document the call read
("reads") and which keys of the job, parameters and tables the probe asked for ("inputs_read")."""
import json, os, re, shutil, sys, tempfile, unittest
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_fusion, gate_common, gate_deps, gate_params, gate_plan, gate_report, gate_stamp, gate_fusion_rpc
import test_fusion_script as tfs                       # its Prelude runs probes/_fusion.py against a fake adsk
FakeDoc = tfs.FakeDoc

BRACKET, COVER, FAR = 'demo/bracket', 'demo/cover', 'demo/far_plate'
IDENT = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
BOX = [[0, 0, 0], [40, 30, 10]]                       # every part's own box (part frame)


def moved(x, y=0.0, z=0.0):
    return [1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, z, 0, 0, 0, 1]


def ref(name, lo, hi, sig='r1', pose='0' * 16, mesh=False):
    return {'name': name, 'component_id': 'c-' + name, 'occurrence': name + ':1', 'body': 'Body1', 'sig': sig, 'pose': pose,
            'bbox_doc': [list(lo), list(hi)], 'faces': 4, 'mesh': mesh, 'primitive': True, 'visible': True, 'shown': True}


class Deps(unittest.TestCase):
    """Bracket at the origin; Cover 0.2 mm beside it (x 40.2 .. 80.2); Far plate 500 mm away; a screw ref in the bracket's
    hole, a bearing ref far away. The bracket's openings unit reads 5 mm round the bracket, its inserts unit reads the
    parameter inserts.wall_target only; the census and thin units read nothing but the bracket."""

    def setUp(self):
        self.demo = fake_fusion.DemoProject(self)
        self.root, self.fusion = self.demo.root, self.demo.fusion
        self.sigs = {'Bracket': fake_fusion.sig_of('bracket v1'), 'Cover': fake_fusion.sig_of('cover v1'), 'Far plate': fake_fusion.sig_of('far v1')}
        self.poses = {'Bracket': IDENT, 'Cover': moved(40.2), 'Far plate': moved(500)}
        self.refs = [ref('ref screw M3x8', (10, 10, -2), (13, 13, 8)), ref('ref bearing 608', (600, 0, 0), (622, 22, 7))]
        self.answer()

    def scan_facts(self):
        parts = []
        for name, sig in self.sigs.items():
            m = self.poses[name]
            parts.append(fake_fusion.scan_part(name, sig, matrix=m, pose=gate_common.digest(m), bbox=BOX, shown=True))
        return {'parts': parts, 'refs': [dict(r) for r in self.refs]}

    census_reads = ()

    def answer(self, census_holes=()):
        """Canned answers for every unit of every part, with what each unit read."""
        self.fusion.clear('fusion_scan', None)
        self.fusion.set_facts('fusion_scan', None, self.scan_facts())
        for name in self.sigs:
            pid = gate_common.part_id('demo', name)
            m = self.poses[name]
            lo, hi = gate_deps._box(m, BOX)
            for probe in fake_fusion.DEMO_PROBES:
                facts = json.loads(json.dumps(fake_fusion.CLEAN[probe]))
                if probe == 'fusion_census':
                    facts['holes'] = list(census_holes) if pid == BRACKET else []
                inputs = {'params': [], 'tables': [], 'job': ['doc', 'component_id', 'occurrence', 'body']}
                reads = [] if probe != 'fusion_census' or pid != BRACKET or not self.census_reads else [dict(r) for r in self.census_reads]
                if probe == 'fusion_openings':
                    reads = [{'box': [list(lo), list(hi)], 'margin': 5.0}]
                    inputs = dict(inputs, job=inputs['job'] + ['features'])
                    if pid == BRACKET:
                        facts = self.bracket_openings          # measured on the model as it is when the unit runs
                if probe == 'fusion_inserts':
                    inputs = dict(inputs, params=['inserts.wall_target'], job=inputs['job'] + ['features'])
                self.fusion.clear(probe, pid)
                self.fusion.set_facts(probe, pid, facts, reads=reads, inputs_read=inputs)

    def bracket_openings(self, job):
        """The bracket's openings as Fusion would measure them now: its window is closed while the cover touches it
        (the cover's left face closer than 0.1 mm to the bracket's right face at x 40)."""
        gap = self.poses['Cover'][3] - 40.0
        by = ['Cover'] if gap < 0.1 else []
        return {'openings': [{'anchor': {'kind': 'hole', 'point': [40, 15, 5], 'axis': [1, 0, 0]}, 'by': by}], 'closed': []}

    def gate_run(self, *argv):
        n = len(self.fusion.calls)
        code, out, err = self.demo.gate('run', '--wall', '600', *argv)
        sent = [(j['part'], j['probe']) for j in self.fusion.calls[n:] if j['part']]
        return code, out, err, sent

    def stored(self, part, probe):
        return gate_common.load_facts(self.root, part, gate_common.load_census(self.root)['parts'][part]['sig'])['probes'][probe]

    # ---- recording ----
    def test_a_unit_records_what_it_read(self):
        code, out, err, sent = self.gate_run()
        self.assertEqual(len(sent), 12, out + err)
        deps = self.stored(BRACKET, 'fusion_openings')['deps']
        self.assertEqual(sorted(deps['bodies']), ['Cover:1|Body1', 'ref screw M3x8:1|Body1'])      # the neighbour and the screw, not the far ones
        self.assertEqual((deps['v'], deps['code'], deps['params'], deps['tables']), (1, gate_deps.code('fusion_openings'), {}, {}))
        self.assertEqual(sorted(deps['job']), ['body', 'component_id', 'doc', 'features', 'occurrence'])
        self.assertIsNotNone(deps['own'])                                                     # it read the world: its own place counts
        thin = self.stored(BRACKET, 'fusion_thin')['deps']
        self.assertEqual((thin['bodies'], thin['own'], thin['reads']), ({}, None, []))         # its own body alone
        self.assertEqual(self.stored(BRACKET, 'fusion_inserts')['deps']['params'], {'inserts.wall_target': 4.0})
        self.assertNotIn('reads', self.stored(BRACKET, 'fusion_openings'))                     # kept as deps, never twice
        world = gate_common.load_census(self.root)['docs']['demo']['world']
        self.assertEqual(sorted(world), ['Bracket:1|Body1', 'Cover:1|Body1', 'Far plate:1|Body1', 'ref bearing 608:1|Body1', 'ref screw M3x8:1|Body1'])

    def test_nothing_changed_nothing_is_measured(self):
        first = self.gate_run()
        code, out, err, sent = self.gate_run()
        self.assertEqual(sent, [])                                                            # no unit was sent
        self.assertEqual(code, first[0]); self.assertIn('3 parts', out)                      # the stored verdicts are reported as before
        self.assertEqual(gate_plan.unchecked(gate_plan.stale_units(self.root, self.demo.registry())), [])

    def test_a_unit_measured_by_an_older_unit_text_is_always_measured_again(self):
        for probe in fake_fusion.DEMO_PROBES:
            self.fusion.answers[(probe, BRACKET)][0]['reads'] = None                         # it does not say what it read
        self.gate_run()
        code, out, err, sent = self.gate_run()
        self.assertEqual(sorted(sent), sorted((BRACKET, p) for p in fake_fusion.DEMO_PROBES))
        self.assertEqual(gate_deps.why_stale(self.stored(BRACKET, 'fusion_thin'), {}, {}, None, 'fusion_thin'),
                         'no record of what it read (measured by an older copy of the gate)')

    # ---- staleness ----
    def test_own_change_measures_the_part_and_the_neighbours_that_read_it(self):
        self.gate_run()
        self.sigs['Bracket'] = fake_fusion.sig_of('bracket v2'); self.answer()
        code, out, err, sent = self.gate_run()
        self.assertEqual(sorted(sent), sorted([(BRACKET, p) for p in fake_fusion.DEMO_PROBES] + [(COVER, 'fusion_openings')]))

    def test_a_moved_neighbour_measures_again_what_read_it_and_nothing_else(self):
        self.gate_run()
        self.poses['Cover'] = moved(41.0); self.answer()                                     # 0.8 mm away from the bracket now
        code, out, err, sent = self.gate_run()
        # the bracket's openings read the cover; the cover's own openings read the world round it, its other units its body alone
        self.assertEqual(sorted(sent), [(BRACKET, 'fusion_openings'), (COVER, 'fusion_openings')])
        self.assertEqual(self.gate_run()[3], [])                                                   # and then it holds again

    def test_a_moved_part_with_no_neighbour_measures_only_what_read_the_world(self):
        self.gate_run()
        self.poses['Far plate'] = moved(520); self.answer()
        code, out, err, sent = self.gate_run()
        self.assertEqual(sent, [(FAR, 'fusion_openings')])                                    # its census, inserts and thin read its body alone

    def test_a_changed_ref_measures_only_the_units_that_read_it(self):
        self.gate_run()
        self.refs[0]['sig'] = 'r2'; self.answer()                                             # the screw in the bracket was replaced
        self.assertEqual(self.gate_run()[3], [(BRACKET, 'fusion_openings')])
        self.refs[1]['pose'] = '1' * 16; self.answer()                                        # the far bearing moved: nobody read it
        self.assertEqual(self.gate_run()[3], [])

    def test_a_body_that_comes_into_a_region_is_seen(self):
        self.gate_run()
        self.refs.append(ref('ref washer M3', (39, 5, 0), (41, 7, 1))); self.answer()
        self.assertEqual(sorted(self.gate_run()[3]), [(BRACKET, 'fusion_openings'), (COVER, 'fusion_openings')])
        why = gate_plan.stale_units(self.root, self.demo.registry())
        self.assertEqual(why, {p: {probe: None for probe in fake_fusion.DEMO_PROBES} for p in (BRACKET, COVER, FAR)})

    def test_a_parameter_measures_again_only_the_units_that_read_it(self):
        self.gate_run()
        gate_common.write_json(gate_common.gate_path(self.root, 'owner', 'params.json'),
                               {'inserts.wall_target': {'value': 4.5, 'tag': 'U', 'source': 'owner in a test', 'date': '2026-11-21'}})
        stale = gate_plan.stale_units(self.root, self.demo.registry())
        self.assertEqual(stale[BRACKET]['fusion_inserts'], 'parameter inserts.wall_target changed (4.0, now 4.5)')
        code, out, err, sent = self.gate_run()
        self.assertEqual(sorted(sent), sorted((p, 'fusion_inserts') for p in (BRACKET, COVER, FAR)))

    def test_a_new_probe_version_measures_its_units_again(self):
        self.gate_run()
        with mock.patch.dict(gate_deps._CODE, {'fusion_thin': 'another text'}):
            self.assertEqual(gate_plan.stale_units(self.root, self.demo.registry())[BRACKET]['fusion_thin'], 'the probe changed')
            self.assertEqual(sorted(self.gate_run()[3]), sorted((p, 'fusion_thin') for p in (BRACKET, COVER, FAR)))

    def test_a_new_probe_version_measures_again_but_kills_no_stamp(self):
        self.gate_run()
        stamp = fake_fusion.whole_stamp(self.root, BRACKET, self.sigs['Bracket'], facts=gate_stamp.facts_digests(self.root, BRACKET, self.sigs['Bracket']))
        reg = self.demo.registry()
        self.assertEqual(gate_stamp.verify(self.root, reg, stamp)[0], 'valid')
        with mock.patch.dict(gate_deps._CODE, {'fusion_thin': 'the text of a newer plugin'}):
            self.assertEqual(gate_plan.stale_units(self.root, reg)[BRACKET]['fusion_thin'], 'the probe changed')     # planned again
            self.assertEqual(gate_stamp.verify(self.root, reg, stamp)[0], 'valid')                                    # the stamp holds meanwhile
        self.assertTrue(gate_deps.tool_reason('measured in another version of Fusion (1, now 2)'))
        self.assertTrue(gate_deps.tool_reason('measured with a look cut short by time (late)'))
        self.assertFalse(gate_deps.tool_reason('Cover changed, moved, or was shown or hidden'))

    def test_the_probes_get_the_tables_without_what_only_the_judges_read(self):
        tables = {'inserts': {'kinds': {'M3x4x5': {'d': 4.0, 'measured_under': [{'layer_height': 0.2}]}}},
                  'bearings': {'sizes': {'608': {'d': 8.0, 'D': 22.0, 'B': 7.0, 'measured': {'source': 'caliper', 'date': 'x'}}}, 'seat_fit': {'tag': 'M'}},
                  'screws': {'stock_lengths': {'M3': [8]}, 'stock_lengths_set': {}, 'stock_from_project': ['x'], 'heads': {'M3': 5.5}},
                  'pins': {'sizes': {'5': {'d': 5.0, 'lengths': [32], 'stock': 'project'}}}}
        got = gate_deps.probe_tables(tables)
        self.assertEqual(got, {'inserts': {'kinds': {'M3x4x5': {'d': 4.0}}}, 'bearings': {'sizes': {'608': {'d': 8.0, 'D': 22.0, 'B': 7.0}}},
                               'screws': {'heads': {'M3': 5.5}}, 'pins': {'sizes': {'5': {'d': 5.0}}}})
        self.assertIn('measured_under', tables['inserts']['kinds']['M3x4x5'])            # the judges keep the whole tables

    def test_a_record_that_digested_the_whole_tables_measures_again_but_kills_no_stamp(self):
        full = {'inserts': {'kinds': {'M3x4x5': {'d': 4.0, 'measured_under': [{'layer_height': 0.2}]}}}}
        view = gate_deps.probe_tables(full)
        deps = {'v': gate_deps.DEPS_V, 'code': gate_deps.code('fusion_inserts'), 'fusion': None, 'own': None, 'job': {}, 'params': {},
                'tables': {'inserts': gate_common.digest(full['inserts'])}, 'reads': [], 'bodies': {}}
        stored = {'facts': {'pockets': []}, 'done': True, 'deps': deps}
        why = gate_deps.why_stale(stored, {}, {'params': {}, 'tables': view}, {}, 'fusion_inserts', whole_tables=full)   # a pre-release 0.3.0 record: no "tv"
        self.assertIn('its record holds the tables as an older copy of the gate digested them (table inserts)', why)
        self.assertTrue(gate_deps.tool_reason(why))
        # the whole table changed too (a real edit, not only the view): the model's business, also on an old record
        edited = {'inserts': {'kinds': {'M3x4x5': {'d': 4.1, 'measured_under': [{'layer_height': 0.2}]}}}}
        why = gate_deps.why_stale(stored, {}, {'params': {}, 'tables': gate_deps.probe_tables(edited)}, {}, 'fusion_inserts', whole_tables=edited)
        self.assertEqual(why, 'table inserts changed'); self.assertFalse(gate_deps.tool_reason(why))
        why = gate_deps.why_stale(stored, {}, {'params': {}, 'tables': view}, {}, 'fusion_inserts')       # no whole tables given: never a tool reason
        self.assertEqual(why, 'table inserts changed')
        star = dict(stored, deps=dict(deps, tables={'*': gate_common.digest(full)}))
        self.assertTrue(gate_deps.tool_reason(gate_deps.why_stale(star, {}, {'params': {}, 'tables': view}, {}, 'fusion_inserts', whole_tables=full)))
        self.assertEqual(gate_deps.why_stale(star, {}, {'params': {}, 'tables': gate_deps.probe_tables(edited)}, {}, 'fusion_inserts', whole_tables=edited),
                         'the tables changed')
        why = gate_deps.why_stale(dict(stored, deps=dict(deps, tv=gate_deps.TABLES_VIEW)), {}, {'params': {}, 'tables': view}, {}, 'fusion_inserts')
        self.assertEqual(why, 'table inserts changed'); self.assertFalse(gate_deps.tool_reason(why))          # a record of today: the model's business
        same = dict(deps, tv=gate_deps.TABLES_VIEW, tables={'inserts': gate_common.digest(view['inserts'])})
        self.assertIsNone(gate_deps.why_stale(dict(stored, deps=same), {}, {'params': {}, 'tables': view}, {}, 'fusion_inserts'))
        # the same in a project: every record written whole-table, a stamp on top of them
        self.gate_run()
        self.assertEqual(self.stored(BRACKET, 'fusion_inserts')['deps']['tv'], gate_deps.TABLES_VIEW)
        stamp = fake_fusion.whole_stamp(self.root, BRACKET, self.sigs['Bracket'], facts=gate_stamp.facts_digests(self.root, BRACKET, self.sigs['Bracket']))
        reg = self.demo.registry()
        reg['tables']['inserts'] = {'kinds': {'M3x4x5': {'d': 4.0, 'measured_under': [{'layer_height': 0.2}]}}}   # a print the owner recorded
        gate_plan._PROBE_TABLES.clear()
        self.assertNotEqual(gate_deps.probe_tables(reg['tables'])['inserts'], reg['tables']['inserts'])
        path = gate_common.facts_path(self.root, BRACKET, self.sigs['Bracket'])
        data = gate_common.read_json(path)
        for e in data['probes'].values():
            if isinstance(e.get('deps'), dict):
                e['deps'].pop('tv', None); e['deps']['tables'] = {'inserts': gate_common.digest(reg['tables']['inserts'])}
        gate_common.write_json(path, data)
        why = gate_plan.stale_units(self.root, reg)[BRACKET]['fusion_inserts']
        self.assertTrue(gate_deps.tool_reason(why), why)
        self.assertEqual(gate_stamp.verify(self.root, reg, stamp)[0], 'valid')

    def test_a_census_measured_again_with_the_same_holes_needs_no_other_unit(self):
        self.census_reads = [{'box': [[0, 0, 0], [40, 30, 10]], 'margin': 5.0}]                # the bracket's census looks 5 mm round it
        self.answer()
        self.gate_run()
        self.poses['Cover'] = moved(41.0); self.answer()
        code, out, err, sent = self.gate_run()
        # census and openings read the cover; inserts reads the census: looked at again once the census was measured, no call
        self.assertEqual(sorted(sent), [(BRACKET, 'fusion_census'), (BRACKET, 'fusion_openings'), (COVER, 'fusion_openings')])
        plan = gate_plan.load_plan(self.root)
        self.assertEqual([(u['state'], u.get('reused')) for u in plan['units'] if u['part'] == BRACKET and u['probe'] in ('fusion_inserts', 'fusion_thin')],
                         [('done', True), ('done', True)])
        hole = {'id': 'h1', 'anchor': {'kind': 'hole', 'point': [5, 5, 10], 'axis': [0, 0, 1]}, 'd': 3.0, 'segments': [{'d': 3.0, 'len': 5}], 'class': None}
        self.poses['Cover'] = moved(41.5); self.answer(census_holes=[hole])
        sent = self.gate_run()[3]
        self.assertEqual(sorted(sent), [(BRACKET, 'fusion_census'), (BRACKET, 'fusion_inserts'), (BRACKET, 'fusion_openings'), (COVER, 'fusion_openings')])
        self.assertEqual(gate_plan.unchecked(gate_plan.stale_units(self.root, self.demo.registry())), [])

    # ---- reuse gives what a full run gives ----
    def test_facts_after_an_incremental_run_equal_those_of_a_full_run(self):
        # the facts of the fake depend on the model: a unit that is wrongly NOT measured again keeps facts a full run
        # would not give. The bracket itself does not change, so only its record can send its openings again
        self.gate_run()
        self.poses['Cover'] = moved(40.0); self.refs[0]['sig'] = 'r3'; self.answer()           # the cover touches the bracket now
        copy = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, copy, True)
        shutil.rmtree(copy); shutil.copytree(self.root, copy)
        code, out, err, sent = self.gate_run()
        self.assertLess(len(sent), 12)
        self.assertIn((BRACKET, 'fusion_openings'), sent)
        incremental = self.judged(self.root)
        shutil.rmtree(self.root); shutil.copytree(copy, self.root)
        for path in self.facts_files(self.root):                                              # the full run judges from nothing
            data = gate_common.read_json(path, {}); data['judged'] = {}; gate_common.write_json(path, data)
        code2, out2, err2, sent2 = self.gate_run('--all', '--fresh')
        self.assertEqual(len(sent2), 12)
        self.assertEqual(code, code2)
        self.assertEqual(self.judged(self.root), incremental)
        self.assertIn('"BLOCKED"', incremental)                                                  # and the closed window is in both

    def test_a_unit_measured_again_at_the_same_signature_with_other_facts_is_judged_again(self):
        code, out, err, sent = self.gate_run()
        self.assertEqual(self.findings(BRACKET, 'access.hole_covered'), [])
        self.poses['Cover'] = moved(40.0); self.answer()                                         # same signature, the cover moved onto the window
        code2, out2, err2, sent2 = self.gate_run()
        self.assertIn((BRACKET, 'fusion_openings'), sent2)
        self.assertEqual([f['verdict'] for f in self.findings(BRACKET, 'access.hole_covered')], ['BLOCKED'])
        self.assertIn('access.hole_covered  opening covered by Cover', out2)                       # the report row says it
        self.poses['Cover'] = moved(40.2); self.answer()                                         # moved back: open again, and said so
        self.gate_run()
        self.assertEqual(self.findings(BRACKET, 'access.hole_covered'), [])

    def findings(self, part, rule):
        sig = gate_common.load_census(self.root)['parts'][part]['sig']
        return gate_common.load_facts(self.root, part, sig)['judged'][rule]['findings']

    def facts_files(self, root):
        for base, _, files in os.walk(os.path.join(root, 'gate', 'facts')):
            for name in files:
                if name.endswith('.json'):
                    yield os.path.join(base, name)

    def judged(self, root):
        census = gate_common.load_census(root)
        out = {}
        for pid, e in sorted(census['parts'].items()):
            data = gate_common.load_facts(root, pid, e['sig'])
            out[pid] = {'facts': {k: v.get('facts') for k, v in sorted(data['probes'].items())},
                        'judged': {k: v.get('findings') for k, v in sorted(data['judged'].items())}}
        return json.dumps(out, sort_keys=True)

    # ---- what status and stamps say ----
    def test_status_says_changed_checked_and_stamped_apart(self):
        self.gate_run()
        fake_fusion.whole_stamp(self.root, FAR, self.sigs['Far plate'])
        st = gate_report.status(self.root)
        self.assertEqual((st['unchecked'], st['checked_ids'], st['stamped_ids']), ([], [BRACKET, COVER], [FAR]))
        self.poses['Cover'] = moved(41.0); self.answer(); gate_plan.scan(self.root, wall_s=600)
        st = gate_report.status(self.root)
        self.assertEqual((st['unchecked'], st['checked_ids'], st['stamped_ids']), ([BRACKET, COVER], [], [FAR]))
        self.assertEqual(st['unchecked_why'][BRACKET], 'fusion_openings: Cover:1 changed, moved, or was shown or hidden')
        code, out, err = self.demo.gate('status')
        self.assertIn('3 parts | 2 changed, not checked | 0 checked, no stamp | 1 stamped', out)

    def test_a_stamp_goes_stale_when_a_neighbour_it_read_moves(self):
        self.gate_run()
        stamp = fake_fusion.whole_stamp(self.root, BRACKET, self.sigs['Bracket'], facts=gate_stamp.facts_digests(self.root, BRACKET, self.sigs['Bracket']))
        reg = self.demo.registry()
        self.assertEqual(gate_stamp.verify(self.root, reg, stamp)[0], 'valid')
        self.poses['Cover'] = moved(40.15); self.answer(); gate_plan.scan(self.root, wall_s=600)      # closer to the bracket now, the window still open
        state, why = gate_stamp.verify(self.root, reg, stamp)
        self.assertEqual(state, 'stale'); self.assertIn('what the stamped part was checked against changed (fusion_openings: Cover:1', why)
        self.assertIn(BRACKET, gate_plan.changed_parts(self.root))
        self.gate_run()                                                                            # measured again, same facts: the stamp holds again
        self.assertEqual(gate_stamp.verify(self.root, reg, stamp)[0], 'valid')
        self.fusion.answers[('fusion_openings', BRACKET)][0]['facts'] = {'openings': [], 'closed': [{'hole': 'h1'}]}
        self.poses['Cover'] = moved(40.1); self.answer()
        self.fusion.answers[('fusion_openings', BRACKET)][0]['facts'] = {'openings': [], 'closed': [{'hole': 'h1'}]}
        self.gate_run()                                                                            # measured again, and it came out otherwise
        self.assertEqual(gate_stamp.verify(self.root, reg, stamp), ('stale', 'fusion_openings was measured again after the stamp and its facts differ: '
                                                                    'the stamp is about other facts (gate.py run, then export)'))


class Review(Deps):
    """What a review of the first version of the records found: each test fails on that version."""

    def answer_all(self):
        for qid, rule in re.findall(r'^(Q-[0-9a-f]{6})  \S+  (\S+)', self.demo.gate('attest', 'list')[1], re.M):
            label = fake_fusion.NONE_FINAL if rule == 'process.concept_geometry' else fake_fusion.BED_BOTTOM
            self.assertIn(self.demo.gate('attest', 'answer', qid, '--label', label)[0], (0, 2))

    def move_cover_in_fusion(self, x):
        """The cover moves in Fusion, and nobody scans: only the answers of the fake change."""
        self.poses['Cover'] = moved(x); self.answer()

    # ---- export ----
    def test_an_export_scans_first_and_refuses_when_a_neighbour_moved_since_the_run(self):
        self.gate_run(); self.answer_all(); self.assertEqual(self.demo.gate('run', '--offline')[0], 0)
        self.move_cover_in_fusion(40.0)                                                         # onto the bracket's window, no scan
        code, out, err = self.demo.gate('export', BRACKET)
        self.assertEqual(code, 1, out + err)
        self.assertIn('what the part was checked against changed since it was measured (fusion_openings: Cover:1', out)
        self.assertEqual(gate_stamp.all_stamps(self.root, BRACKET), [])
        self.gate_run()                                                                         # measured again: the window is closed now
        self.assertEqual([f['verdict'] for f in self.findings(BRACKET, 'access.hole_covered')], ['BLOCKED'])
        self.assertNotEqual(self.demo.gate('export', BRACKET)[0], 0)

    def test_an_already_stamped_part_is_never_confirmed_on_an_old_census(self):
        self.gate_run(); self.answer_all(); self.assertEqual(self.demo.gate('run', '--offline')[0], 0)
        self.fusion.set_export(BRACKET, fake_fusion.cube_stl())
        code, out, err = self.demo.gate('export', BRACKET)
        self.assertEqual(code, 0, out + err)
        stamp = gate_stamp.load(self.root, BRACKET, self.sigs['Bracket'])
        self.assertEqual(gate_stamp.verify(self.root, self.demo.registry(), stamp)[0], 'valid')
        self.assertIn('already stamped', self.demo.gate('export', BRACKET)[1])
        self.move_cover_in_fusion(40.0)
        code, out, err = self.demo.gate('export', BRACKET)                                      # it scans, and the record says the cover moved
        self.assertEqual(code, 1, out + err); self.assertNotIn('already stamped', out)
        self.assertIn('fusion_openings: Cover:1 changed, moved, or was shown or hidden', out)
        state, why = gate_stamp.verify(self.root, self.demo.registry(), stamp)
        self.assertEqual(state, 'stale'); self.assertIn('Cover:1', why)

    def test_export_says_when_the_document_is_closed(self):
        self.gate_run()
        del self.fusion.documents['Demo Assembly']
        code, out, err = self.demo.gate('export', BRACKET)
        self.assertEqual(code, 4); self.assertIn('not open', out)

    # ---- a plan measured over several calls ----
    def test_a_neighbour_moved_while_a_plan_ran_is_measured_before_the_run_says_done(self):
        self.gate_run()
        self.sigs['Far plate'] = fake_fusion.sig_of('far v2'); self.answer()                     # one change, measured over several calls
        for key in list(self.fusion.answers):
            for a in self.fusion.answers[key]:
                a['elapsed_s'] = 15.0                                                           # one unit per call of 20 s
        moved_at, codes, n = None, [], 0
        while n < 30:
            code, out, err = self.demo.gate('run', '--wall', '20')
            codes.append(code); n += 1
            done = [u for u in gate_plan.load_plan(self.root)['units'] if u['state'] == 'done' and not u.get('reused')]
            if moved_at is None and done:
                moved_at = n
                self.move_cover_in_fusion(40.0)                                                 # while the run goes on
            if code != 3:
                break
        self.assertIn(3, codes[:-1])
        sent = [(j['part'], j['probe']) for j in self.fusion.calls if j['part']]
        self.assertIn((BRACKET, 'fusion_openings'), sent[-6:])                                 # measured after the move, before the end
        self.assertEqual([f['verdict'] for f in self.findings(BRACKET, 'access.hole_covered')], ['BLOCKED'])
        self.assertEqual(gate_report.status(self.root)['unchecked'], [])

    # ---- the state of a ref, and its key ----
    def test_a_ref_with_the_same_cheap_signature_and_another_shape_is_a_change(self):
        self.refs[0]['shape'] = 'a' * 16; self.answer()
        self.gate_run()
        self.refs[0]['shape'] = 'b' * 16; self.answer()                                         # its hole moved inside the same outline
        self.assertEqual(self.gate_run()[3], [(BRACKET, 'fusion_openings')])

    def test_bodies_that_share_a_key_are_all_kept(self):
        facts = {'parts': [], 'refs': [ref('ref motor', (0, 0, 0), (1, 1, 1), sig='solid1'), ref('ref motor', (0, 0, 0), (1, 1, 1), sig='mesh1', mesh=True),
                                       ref('ref motor', (0, 0, 0), (1, 1, 1), sig='solid2')]}
        lines = gate_deps.world(facts)
        self.assertEqual(sorted(lines), ['ref motor:1|Body1', 'ref motor:1|Body1#2', 'ref motor:1|mesh:Body1'])
        facts['refs'][2]['sig'] = 'solid3'
        self.assertNotEqual(gate_deps.world(facts), lines)                                      # the change of the second is seen

    # ---- what makes stored facts not hold ----
    def test_a_unit_that_reads_the_census_by_its_record_is_looked_at_again(self):
        self.census_reads = [{'box': [[0, 0, 0], [40, 30, 10]], 'margin': 5.0}]
        self.answer()
        inputs = {'params': [], 'tables': [], 'job': ['doc', 'component_id', 'occurrence', 'body', 'features']}
        self.fusion.answers[('fusion_thin', BRACKET)][0]['inputs_read'] = inputs               # thin is no NEEDS_CENSUS probe, its record says it read them
        self.gate_run()
        hole = {'id': 'h1', 'anchor': {'kind': 'hole', 'point': [5, 5, 10], 'axis': [0, 0, 1]}, 'd': 3.0, 'segments': [{'d': 3.0, 'len': 5}], 'class': None}
        self.poses['Cover'] = moved(41.5); self.answer(census_holes=[hole])
        self.fusion.answers[('fusion_thin', BRACKET)][0]['inputs_read'] = inputs
        self.assertIn((BRACKET, 'fusion_thin'), self.gate_run()[3])

    def test_facts_of_a_look_cut_short_by_time_are_measured_again(self):
        self.gate_run()
        self.fusion.answers[('fusion_openings', COVER)][0]['facts'] = {'openings': [], 'closed': [], 'screws_cut': True}
        self.assertEqual(self.gate_run('--part', COVER, '--fresh')[3], [(COVER, p) for p in fake_fusion.DEMO_PROBES])
        self.assertEqual(gate_plan.stale_units(self.root, self.demo.registry())[COVER]['fusion_openings'],
                         'measured with a look cut short by time (screws_cut)')
        self.fusion.answers[('fusion_openings', COVER)][0]['facts'] = {'openings': [], 'closed': [], 'screws_cut': False}
        self.assertEqual(self.gate_run()[3], [(COVER, 'fusion_openings')])
        self.assertEqual(self.gate_run()[3], [])

    def test_facts_of_another_fusion_version_are_measured_again(self):
        self.fusion.version = '2.0.100'
        self.gate_run()
        self.assertEqual(self.gate_run()[3], [])
        self.fusion.version = '2.0.200'                                                         # Fusion was updated
        code, out, err, sent = self.gate_run()
        self.assertEqual(len(sent), 12)
        self.assertEqual(self.gate_run()[3], [])

    def test_a_unit_given_up_is_not_sent_again_until_something_changed(self):
        self.gate_run()
        self.sigs['Far plate'] = fake_fusion.sig_of('far v2'); self.answer()
        self.fusion.set_timeout('fusion_thin', FAR, alive_after=True)
        self.gate_run()
        self.assertTrue(self.stored(FAR, 'fusion_thin')['error'].startswith(gate_plan.OVER_BUDGET))
        self.assertEqual(self.gate_run()[3], [])                                                # nothing changed: given up, not two more waits
        self.assertIn('NOT_RUN', json.dumps(gate_report.status(self.root)['open']) + json.dumps(gate_plan.part_verdict(self.root, self.demo.registry(), FAR)))
        self.refs[1]['pose'] = '2' * 16; self.answer(); self.fusion.set_timeout('fusion_thin', FAR, alive_after=True)
        self.assertIn((FAR, 'fusion_thin'), self.gate_run()[3])                                 # the document changed: tried again
        self.fusion.clear('fusion_thin', FAR); self.answer()
        self.assertIn((FAR, 'fusion_thin'), self.gate_run('--part', FAR, '--fresh')[3])

    def test_the_code_of_a_unit_ignores_comments_and_docstrings(self):
        a = 'def f(x):\n    """Say what."""\n    # a note\n    return x + 1  # inline\n\n\nY = 2\n'
        b = 'def f(x):\n    """Say what, with other words."""\n    return x + 1\nY = 2\n'
        c = 'def f(x):\n    return x + 2\nY = 2\n'
        self.assertEqual(gate_deps.normalised(a), gate_deps.normalised(b))
        self.assertNotEqual(gate_deps.normalised(a), gate_deps.normalised(c))
        self.assertEqual(gate_deps.normalised('x = "# not a comment"\n'), 'x = "# not a comment"')

    def test_stored_judgements_are_not_taken_over_from_other_judging_code(self):
        self.gate_run()
        sig = self.sigs['Bracket']
        before = gate_common.load_facts(self.root, BRACKET, sig)['judged']['inserts.wall']
        self.gate_run()
        self.assertEqual(gate_common.load_facts(self.root, BRACKET, sig)['judged']['inserts.wall']['time'], before['time'])      # taken over
        with mock.patch.dict(gate_plan._JUDGING, {'v': 'other judging code'}):
            self.assertIn('inserts.wall', gate_plan.judgement_out_of_date(self.root, self.demo.registry(), BRACKET, sig, gate_params.resolve(
                self.demo.registry(), gate_params.load_overrides(self.root), gate_common.target_of(gate_common.load_project(self.root)))))
            self.gate_run()
            self.assertEqual(gate_common.load_facts(self.root, BRACKET, sig)['judged']['inserts.wall']['code'], 'other judging code')

    # ---- several small units in one call ----
    def slow(self, part, seconds):
        for (probe, pid), answers in self.fusion.answers.items():
            if pid == part:
                for a in answers:
                    a['elapsed_s'] = seconds

    def test_short_units_share_a_call(self):
        self.slow(FAR, 3.0)
        self.gate_run()
        self.sigs['Far plate'] = fake_fusion.sig_of('far v2'); self.answer(); self.slow(FAR, 3.0)
        calls = 0
        while True:
            code, out, err = self.demo.gate('run', '--wall', '20')
            calls += 1
            if code != 3 or calls > 10:
                break
        sent = [j for j in self.fusion.calls if j['part'] == FAR][4:]
        self.assertEqual(len(sent), 4)
        self.assertEqual(calls, 1, out)                                                         # 4 units of 3 s in one call of 20 s (it was 4 calls)
        self.assertEqual(sent[0]['deadline_s'], 20)                                             # the first one with its budget ...
        self.assertTrue(all(1.0 <= j['deadline_s'] < 20 for j in sent[1:]), [j['deadline_s'] for j in sent])   # ... the others in what was left
        self.assertEqual(gate_report.status(self.root)['unchecked'], [])

    def test_a_short_unit_that_cut_its_look_short_is_measured_again_with_its_budget(self):
        self.gate_run()
        self.sigs['Far plate'] = fake_fusion.sig_of('far v2'); self.answer()
        self.fusion.clear('fusion_openings', FAR)
        self.fusion.set_facts('fusion_openings', FAR, lambda job: {'openings': [], 'closed': [], 'screws_cut': job['deadline_s'] < 19.0}, reads=[],
                              inputs_read={'params': [], 'tables': [], 'job': ['doc']})
        for _ in range(10):
            if self.demo.gate('run', '--wall', '20')[0] != 3:
                break
        sent = [j for j in self.fusion.calls if (j['part'], j['probe']) == (FAR, 'fusion_openings')]
        self.assertEqual(sent[-1]['deadline_s'], 20)                                            # the last one with its whole budget
        self.assertFalse(self.stored(FAR, 'fusion_openings')['facts']['screws_cut'])

    # ---- status ----
    def test_status_says_when_it_was_scanned_and_what_measuring_again_costs(self):
        self.gate_run()
        self.poses['Cover'] = moved(41.0); self.answer(); gate_plan.scan(self.root, wall_s=600)
        st = gate_report.status(self.root)
        self.assertEqual(st['measure_again'], {'units': 2, 'work_s': 0.2, 'unknown': 0})
        out = self.demo.gate('status')[1]
        self.assertIn('as of the scan at %s' % st['last_scan'], out)
        self.assertIn('to measure again: 2 unit(s), about 0 s of Fusion work', out)


for _name in [n for n in vars(Deps) if n.startswith('test_')]:
    setattr(Review, _name, None)                        # Review shares the model of Deps, not its tests


class World(unittest.TestCase):
    def test_world_lines_and_region_filters(self):
        facts = {'parts': [fake_fusion.scan_part('Plate', 's' * 64, faces=200, shown=True), fake_fusion.scan_part('Shim', 't' * 64, faces=8, matrix=moved(50), occurrence='Shim:1')],
                 'copy_bodies': [{'name': 'Shim', 'component_id': 'c-shim', 'occurrence': 'Shim:2', 'body': 'Body1', 'pose': 'p', 'matrix': moved(100), 'bbox': BOX,
                                  'faces': 8, 'visible': True, 'shown': False}],
                 'refs': [ref('ref nut', (0, 0, 0), (1, 1, 1)), ref('mesh cover', (0, 0, 0), (5, 5, 5), mesh=True),
                          dict(ref('mesh unread', (0, 0, 0), (1, 1, 1), mesh=True), bbox_doc=None)]}
        lines = gate_deps.world(facts)
        self.assertEqual(sorted(lines), ['Plate:1|Body1', 'Shim:1|Body1', 'Shim:2|Body1', 'mesh cover:1|mesh:Body1', 'mesh unread:1|mesh:Body1', 'ref nut:1|Body1'])
        st, kind, faces, box = gate_deps.parse(lines['Shim:2|Body1'])
        self.assertEqual((kind, faces, box), ('copy', 8, ((100.0, 0.0, 0.0), (140.0, 30.0, 10.0))))
        self.assertEqual(st, gate_deps.state('t' * 64, 'p', False))                         # a copy has the signature of its part
        self.assertEqual(gate_deps.parse(lines['mesh unread:1|mesh:Body1'])[3], None)
        w = gate_deps.World(lines)
        everything = [{'box': [[-1000, -1000, -1000], [1000, 1000, 1000]], 'margin': 0.0}]
        self.assertEqual(sorted(w.bodies_in(everything, 'Plate:1|Body1')), ['Shim:1|Body1', 'Shim:2|Body1', 'mesh cover:1|mesh:Body1', 'ref nut:1|Body1'])
        self.assertEqual(sorted(w.bodies_in([dict(everything[0], solid_only=True, printed_faces_max=6)], 'Plate:1|Body1')), ['ref nut:1|Body1'])
        self.assertEqual(sorted(w.bodies_in([{'box': [[0, 0, 0], [1, 1, 1]], 'margin': 3.0}], None)), ['Plate:1|Body1', 'mesh cover:1|mesh:Body1', 'ref nut:1|Body1'])
        self.assertIsNone(w.bodies_in([{'box': 'nonsense'}], None))

    def test_join_reads_over_calls(self):
        a = gate_deps.join_reads(None, {'reads': [{'box': [[0, 0, 0], [1, 1, 1]], 'margin': 1}], 'inputs_read': {'params': ['a'], 'tables': [], 'job': ['doc']}})
        b = gate_deps.join_reads(a, {'reads': [{'box': [[0, 0, 0], [1, 1, 1]], 'margin': 1}, {'box': [[5, 5, 5], [6, 6, 6]], 'margin': 0}],
                                     'inputs_read': {'params': ['b'], 'tables': None, 'job': ['doc']}})
        self.assertEqual((len(b['reads']), b['params'], b['tables'], b['complete']), (2, ['a', 'b'], None, True))
        self.assertFalse(gate_deps.join_reads(b, {'facts': {}})['complete'])                # one call that did not say: nothing is recorded
        self.assertIsNone(gate_deps.record(gate_deps.join_reads(b, {}), {}, {}, {}, 'fusion_thin'))


class ScanShown(unittest.TestCase):
    def test_a_body_is_shown_only_when_every_light_bulb_on_its_path_is_on(self):
        with open(os.path.join(ROOT, 'tools', 'gate', 'probes', 'fusion_scan.py'), encoding='utf-8') as f:
            g = {}
            exec(compile(f.read(), 'fusion_scan', 'exec'), g)

        class Occ:
            def __init__(self, path, on, up=None):
                self.fullPathName, self.isLightBulbOn, self.assemblyContext, self.asked = path, on, up, 0

        top = Occ('Gearbox:1', False)
        inner = Occ('Gearbox:1+Rotor:1', True, top)
        other = Occ('Motor:1', True)
        memo = {}
        self.assertEqual((g['_scan_shown'](inner, memo), g['_scan_shown'](other, memo), g['_scan_shown'](None, memo)), (False, True, True))
        self.assertEqual(memo, {'Gearbox:1': False, 'Gearbox:1+Rotor:1': False, 'Motor:1': True})
        self.assertFalse(g['_scan_shown'](object(), {}))                                     # nothing readable: not shown, never an error


class FusionSide(tfs.Dir):
    """probes/_fusion.py run() reports what a probe read: the regions it noted and the keys it asked for."""
    run_script = tfs.Prelude.run_script                 # the unit as Fusion runs it, against a fake adsk (its tests stay there)

    def test_the_envelope_says_what_the_probe_read(self):
        probe = ('def probe(job):\n'
                 '    p = job.get("params") or {}\n'
                 '    a, b = p.get("inserts.wall_target"), p.get("not.there", 7)\n'
                 '    t = (job.get("tables") or {}).get("inserts")\n'
                 '    gate_read(((0, 0, 0), (1, 2, 3)), 5)\n'
                 '    gate_read(((0, 0, 0), (1, 2, 3)), 5)\n'
                 '    gate_read(((1, 1, 1), (2, 2, 2)), 25, solid_only=True, printed_faces_max=80)\n'
                 '    return {"facts": {"a": a, "b": b, "t": t is not None, "f": job.get("features")}}\n')
        env, _ = self.run_script([FakeDoc('Demo Assembly', 2)], probe)
        self.assertEqual(env['facts'], {'a': 4.0, 'b': 7, 't': True, 'f': None})
        self.assertEqual(env['reads'], [{'box': [[0, 0, 0], [1, 2, 3]], 'margin': 5.0},
                                        {'box': [[1, 1, 1], [2, 2, 2]], 'margin': 25.0, 'solid_only': True, 'printed_faces_max': 80}])
        self.assertEqual(env['inputs_read']['params'], ['inserts.wall_target', 'not.there'])
        self.assertEqual(env['inputs_read']['tables'], ['inserts'])
        self.assertIn('features', env['inputs_read']['job']); self.assertNotIn('motion_groups', env['inputs_read']['job'])

    def test_empty_inputs_are_watched_and_a_copy_reads_all(self):
        probe = ('def probe(job):\n'
                 '    x = (job.get("params") or {"never": 1}).get("q", 2)\n'
                 '    y = dict(job.get("tables"))\n'
                 '    return {"facts": {"x": x, "y": y}}\n')
        env, _ = self.run_script([FakeDoc('Demo Assembly', 2)], probe, params={}, tables={'a': 1})
        self.assertEqual(env['facts'], {'x': 2, 'y': {'a': 1}})
        self.assertEqual((env['inputs_read']['params'], env['inputs_read']['tables']), (['q'], None))
        env, _ = self.run_script([FakeDoc('Demo Assembly', 2)], 'def probe(job):\n    raise RuntimeError("boom")\n')
        self.assertIn('boom', env['error']); self.assertEqual(env['reads'], [])              # an error still says what it read

    def test_a_body_asked_outside_the_noted_regions_is_noted_and_said(self):
        probe = ('def probe(job):\n'
                 '    gate_read(((0, 0, 0), (10, 10, 10)), 1.0)\n'
                 '    gate_asked("Hub", ((5, 5, 5), (6, 6, 6)))\n'
                 '    gate_asked("Lid", ((50, 0, 0), (60, 10, 10)))\n'
                 '    return {"facts": {}}\n')
        env, _ = self.run_script([FakeDoc('Demo Assembly', 2)], probe)
        self.assertEqual(env['reads'], [{'box': [[0, 0, 0], [10, 10, 10]], 'margin': 1.0}, {'box': [[50, 0, 0], [60, 10, 10]], 'margin': 0.0}])
        self.assertTrue(any('did not hold 1 body(ies) it asked (Lid)' in n for n in env['notes']), env['notes'])

    def test_a_bearing_unit_notes_less_than_its_walk(self):
        with open(os.path.join(ROOT, 'tools', 'gate', 'probes', 'fusion_bearings.py'), encoding='utf-8') as f:
            text = f.read()
        probe = text + ('\n\ndef probe(job):\n'
                        '    rec = lambda n, lo, hi, **k: dict({"name": n, "box": (lo, hi), "mesh": False, "visible": True, "recs": None}, **k)\n'
                        '    ring = rec("608", (0, 0, 0), (22, 22, 7))\n'
                        '    seat = rec("Hub", (-5, -5, 7), (27, 27, 20))\n'
                        '    far = rec("Lid", (200, 0, 0), (300, 10, 10))\n'
                        '    hidden = rec("Cap", (0, 0, -3), (22, 22, 0), visible=False)\n'
                        '    _brg_reads([ring, seat, far, hidden], [{"recs": [ring], "box": ring["box"]}], ((-10, -10, 7), (40, 40, 30)), 3.0)\n'
                        '    return {"facts": {}}\n')
        env, _ = self.run_script([FakeDoc('Demo Assembly', 2)], probe)
        self.assertEqual(env['reads'], [{'box': [[-10, -10, 7], [40, 40, 30]], 'margin': 25.0, 'solid_only': True, 'printed_faces_max': 80},
                                        {'box': [[0, 0, 0], [22, 22, 7]], 'margin': 2.5}])        # the walk was 120 mm; a clamp notes its own look


if __name__ == '__main__':
    unittest.main()
