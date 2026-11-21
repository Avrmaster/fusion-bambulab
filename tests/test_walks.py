"""Walks of the gate as its owner meets it, through gate.py, on the SHIPPED registry and the shipped judges.

Each walk starts from the scratch project of tests/test_end_to_end.py (made by setup_project.py --gate) and goes the
way a session goes: scan, run, answer, export. Fusion is tests/fake_fusion.FakeFusion. What is walked here are the
paths a review found broken: several parts in one export call, a project with several declared targets, a waiver that
is withdrawn behind an answer, a vendored copy that was changed by hand; and the first adoption of a project (the audit,
inherited parts, the questions that wait until a part changes or is exported). Every name and number is invented."""
import json, os, re, subprocess, sys, unittest
for _k in ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID'): os.environ.pop(_k, None)      # gate.py owner from an agent session needs the hook's ticket: a test is no agent session
os.environ.pop('CLAUDE_PROJECT_DIR', None)      # bambu3mf finds a gated root for a file outside it from this: never the session's project
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
for d in (os.path.join(ROOT, 'tools', 'bambu'), os.path.dirname(os.path.abspath(__file__))):
    if d not in sys.path: sys.path.insert(0, d)
import bambu3mf, fake_fusion, fake_slicer, gate_common, gate_plan, gate_report, gate_stamp
from gate_guard import GuardRefusal
import test_guard as G
import test_end_to_end as E
import test_probes_host as H
from test_end_to_end import FOOT, HUB, CLIP, NAMES, PROJ_KEY, TARGET

GATE = os.path.join('gate', 'tools', 'gate.py')
STEEL = 'the clamp of this part is a steel plate on a steel ring, looked at on the bench'
SAID = 'steel on steel there, leave it'
INSERT = {'name': 'ref hardware / insert M3x4x5 (foot)', 'path': 'ref hardware', 'kind': 'hardware', 'gap': 0.0}
BEARING = {'name': 'ref bearing 6203', 'path': 'ref bearing 6203', 'kind': 'hardware', 'gap': 3.0}
MOTOR = {'name': 'ref drive / Rotor', 'path': 'ref stepper NEMA 17+ref drive', 'kind': 'bought', 'gap': 6.5}
# what stands near each part and is not printed (the unit fusion_near): the foot holds an insert, nothing is near the others
NEAR = {FOOT: [INSERT], HUB: [], CLIP: []}


class Walk(E.Lamp):
    def near(self, **bodies):
        """What the unit fusion_near answers for each part (NEAR, with the parts named here replaced)."""
        for part, rows in dict(NEAR, **bodies).items():
            self.fusion.clear('fusion_near', part)
            self.fusion.set_facts('fusion_near', part, {'margin': 10.0, 'bodies': json.loads(json.dumps(rows))})

    def by_facts(self, part, report=None):
        """The rules of the questions the gate answered itself for one part in the latest report."""
        return sorted(q['rule'] for q in (report or self.latest())['questions'] if q['part'] == part and (q.get('answer') or {}).get('kind') == 'facts')

    def ready(self, answer=True, near=None, **run):
        """Three parts, scanned, run and (with answer) every question answered: each one is ready for its export."""
        self.model(foot='v2', hub='v1', clip='v1')
        self.assertEqual(self.gate('scan')[0], 0)
        self.facts(FOOT, fusion_census=E.foot_census(), fusion_inserts=E.foot_pocket(4.2)); self.facts(HUB); self.facts(CLIP)
        self.near(**(near or {}))
        argv = ['--for', run['target']] if run.get('target') else []
        self.assertEqual(self.run_gate(*argv)[0], 2)                             # nothing open, the questions are
        for part, mesh in ((FOOT, E.MESHES['foot v2']()), (HUB, E.MESHES['hub']()), (CLIP, H.box(30, 20, 5))):
            self.fusion.set_export(part, E.stl_bytes(mesh, NAMES[part]))
        if answer:
            self.answer_all()
            self.assertEqual(self.run_gate('--offline', *argv)[0], 0)

    def stamps(self, part):
        return [s for s in gate_stamp.all_stamps(self.root, part)]

    def ask_rules(self):
        with open(os.path.join(ROOT, 'templates', 'gate', 'settings.json'), encoding='utf-8') as f: settings = json.load(f)
        gate_common.write_json(self.path('.claude', 'settings.json'), settings)


class TwoParts(Walk):
    def test_export_of_two_parts_reaches_the_second_one(self):
        self.ready()
        code, out, err = self.gate('export', FOOT, HUB)                          # one unit fits into the 20 s of a call
        self.assertEqual((code, err), (3, '')); self.assertIn('stamped -> stl/lamp_foot.stl', out)
        self.assertIn('%s' % HUB, out); self.assertIn('no time left in this call for the export unit: call again', out)
        self.assertEqual(out.strip().splitlines()[-1], 'NEXT 1/2'); self.assertEqual((len(self.stamps(FOOT)), len(self.stamps(HUB))), (1, 0))
        n = self.exports()
        code, out, err = self.gate('export', FOOT, HUB)                          # the same command again: the stamped part costs no time of the call
        self.assertEqual((code, err), (0, '')); self.assertIn('already stamped, and Fusion confirms the part is unchanged', out)
        self.assertIn('stamped -> stl/turntable.stl', out); self.assertNotIn('NEXT', out)
        self.assertEqual((len(self.stamps(FOOT)), len(self.stamps(HUB)), self.exports()), (1, 1, n + 1))
        self.assertEqual(self.gate('export', FOOT, HUB)[0], 0)                   # and a third call has nothing left to do

    def test_a_longer_wall_takes_both_in_one_call(self):
        self.ready()
        code, out, err = self.gate('export', FOOT, HUB, '--wall', '60')
        self.assertEqual((code, err), (0, '')); self.assertEqual(self.exports(), 2)
        self.assertEqual((len(self.stamps(FOOT)), len(self.stamps(HUB))), (1, 1))

    def test_the_export_writes_a_report_with_the_export_answers(self):
        self.ready()
        before = self.latest()['run']
        code, out, err = self.gate('export', FOOT)
        self.assertEqual(code, 0); self.assertRegex(out, r'report: gate/reports/\S+\.md')
        report = self.latest()
        self.assertNotEqual(report['run'], before); self.assertEqual((report['command'], report['stage'], report['exit']), ('export', 'export', 0))
        answered = {q['rule'] for q in report['questions'] if q['part'] == FOOT and q.get('answer')}
        for rule in ('access.hole_supports', 'print.bed_face'): self.assertIn(rule, answered)       # asked at export, and now on record
        row = next(p for p in report['parts'] if p['part'] == FOOT)
        facts = self.by_facts(FOOT, report)                                      # no bearing and no motor at the foot: the gate said so itself
        self.assertEqual(facts, ['motors.lead_path'])
        mine = self.n_asked - len(facts)                                         # an answer by facts is no statement of the agent: not "attested"
        self.assertEqual((row['verdict'], row['attested'], row['by_facts']), ('pass_attested', mine, 1)); self.assertEqual(self.stamp(FOOT)['attested'], mine)
        self.assertEqual({p['part'] for p in report['parts']}, {FOOT, HUB, CLIP})       # the parts of the report before it stay listed
        text = self.gate('report', '--last')[1]
        self.assertIn('access.hole_supports', text); self.assertIn('pass_attested(%d)' % mine, text); self.assertIn('### Answered by facts', text)
        self.assertEqual(sorted(self.stamp(FOOT)['answers']), sorted(q['q'] for q in report['questions'] if q['part'] == FOOT and q.get('answer')))
        self.assertEqual(gate_stamp.verify(self.root, self.reg, self.stamp(FOOT))[0], 'valid')       # the stamp finds the answers by facts in gate/attest too

    def test_status_is_not_a_pass_while_an_stl_has_no_valid_stamp(self):
        self.ready()
        self.assertEqual(self.gate('export', FOOT)[0], 0)
        code, out, err = self.gate('status')
        self.assertEqual(code, 0); self.assertIn('0 STLs without a valid stamp', out)
        H.write_stl(os.path.join(self.stl_dir, 'lamp_foot.stl'), H.box(44, 34, 11))     # the file is replaced by hand
        code, out, err = self.gate('status')
        self.assertEqual(code, 1); self.assertIn('1 STLs without a valid stamp', out); self.assertIn('STLs without a valid stamp: stl/lamp_foot.stl', out)


class OwnStlNames(Walk):
    """A project that existed before the gate: its STL files have their own names and its build table reads them.
    "stl_names" in gate/layout.json gives each part its file, so the export lands where the project reads."""
    PLATES = [('first plate', [('Lamp foot', 'LampFoot_rev2.stl', False)]), ('second plate', [('Turntable', 'hub 40.stl', False)])]

    def test_the_export_lands_in_the_mapped_file_and_verify_takes_its_stamp(self):
        layout = self.read('gate', 'layout.json')
        layout['documents'][0]['stl_names'] = {'Lamp foot': 'LampFoot_rev2.stl', 'turntable': 'hub 40.stl'}      # by name, and by part slug
        gate_common.write_json(self.path('gate', 'layout.json'), layout)
        old = os.path.join(self.stl_dir, 'LampFoot_rev2.stl')
        H.write_stl(self.path('stl', 'LampFoot_rev2.stl'), H.box(44, 34, 9))             # the file the project has from before the gate
        self.ready()
        census = gate_common.load_census(self.root)['parts']
        self.assertEqual({p: census[p]['stl'] for p in (FOOT, HUB, CLIP)}, {FOOT: 'stl/LampFoot_rev2.stl', HUB: 'stl/hub 40.stl', CLIP: 'stl/cable_clip.stl'})
        self.assertEqual(gate_report.status(self.root)['stl_states'], {'stl/LampFoot_rev2.stl': 'none'})       # the old file: no stamp
        code, out, err = self.gate('export', FOOT, HUB, '--wall', '60')
        self.assertEqual((code, err), (0, ''), out); self.assertIn('stamped -> stl/LampFoot_rev2.stl', out); self.assertIn('stamped -> stl/hub 40.stl', out)
        self.assertEqual(sorted(os.listdir(self.stl_dir)), ['LampFoot_rev2.stl', 'hub 40.stl'])       # no second file under the slug, the old one replaced
        with open(old, 'rb') as f: self.assertEqual(f.read(), E.stl_bytes(E.MESHES['foot v2'](), NAMES[FOOT]))
        for part, rel_stl in ((FOOT, 'stl/LampFoot_rev2.stl'), (HUB, 'stl/hub 40.stl')):
            s = self.stamp(part)
            self.assertEqual((s['stl'], s['stl_sha256']), (rel_stl, gate_common.sha256_file(os.path.join(self.root, *rel_stl.split('/')))))
            self.assertEqual(gate_stamp.verify(self.root, self.reg, s)[0], 'valid')
        st = gate_report.status(self.root)
        self.assertEqual((st['stl_states'], st['unstamped_stls']), ({'stl/LampFoot_rev2.stl': 'valid', 'stl/hub 40.stl': 'valid'}, []))
        n = self.exports()
        code, out, err = self.gate('export', FOOT)                                       # the same file is found again: nothing to do
        self.assertEqual((code, self.exports()), (0, n)); self.assertIn('already stamped', out)
        # the project's build table reads the old names, the guard finds the stamps, verify holds the project against them
        gate_common.write_json(self.path('gate', 'project.json'), dict(self.read('gate', 'project.json'), mode='enforce'))
        script = self.text('bambulab', 'build_3mf.py')
        i = script.index('PLATES = ['); j = script.index(']\n', i) + 2
        with open(self.path('bambulab', 'build_3mf.py'), 'w', encoding='utf-8') as f: f.write(script[:i] + 'PLATES = %r\n' % (self.PLATES,) + script[j:])
        r = self.own(os.path.join('bambulab', 'build_3mf.py'))
        self.assertEqual((r.returncode, r.stderr), (0, ''), r.stdout); self.assertNotIn('gate guard', r.stdout)
        self.assertEqual([(e['object'], e['stl'], e['stamp']) for e in self.events() if e['kind'] == 'write'],
                         [('Lamp foot', 'stl/LampFoot_rev2.stl', '%s@%s' % (FOOT, self.sigs[FOOT][:12])), ('Turntable', 'stl/hub 40.stl', '%s@%s' % (HUB, self.sigs[HUB][:12]))])
        r = self.own(GATE, 'verify')
        self.assertEqual(r.returncode, 1, r.stdout); self.assertIn('was never sliced by the gate', r.stdout)      # the supports are not known yet
        env = dict(E.clean_env(), BAMBU_STUDIO=fake_slicer.make(self.path('fake-slicer')))
        r = subprocess.run([sys.executable, GATE, 'slice'], capture_output=True, text=True, cwd=self.root, env=env, timeout=120)
        self.assertEqual((r.returncode, r.stderr), (0, ''), r.stdout); self.assertIn('support 0.0 mm (filament)', r.stdout)
        r = self.own(GATE, 'verify')
        self.assertEqual((r.returncode, r.stderr), (0, ''), r.stdout)
        self.assertNotIn('stl_without_stamp', r.stdout); self.assertNotIn('FAIL', r.stdout)
        r = self.own(GATE, 'doctor')
        self.assertEqual((r.returncode, r.stderr), (0, ''), r.stdout); self.assertIn('ok    layout.json', r.stdout)       # doctor ran and said its lines
        self.assertNotIn('stl_names', r.stdout); self.assertNotIn('scan again', r.stdout); self.assertNotIn('letter case', r.stdout)

    def test_a_map_added_after_the_scan_waits_for_the_next_scan_and_doctor_says_so(self):
        self.ready()
        layout = self.read('gate', 'layout.json'); layout['documents'][0]['stl_names'] = {'Lamp foot': 'LampFoot_rev2.stl', 'Shade': 'shade.stl'}
        gate_common.write_json(self.path('gate', 'layout.json'), layout)
        r = self.own(GATE, 'doctor')
        self.assertIn('%s: the census names stl/lamp_foot.stl as its STL, gate/layout.json gives stl/LampFoot_rev2.stl now: scan again' % FOOT, r.stdout)
        self.assertIn("'Shade' names no part of the last scan", r.stdout)
        self.assertEqual(self.gate('scan')[0], 0)
        self.assertEqual(gate_common.load_census(self.root)['parts'][FOOT]['stl'], 'stl/LampFoot_rev2.stl')
        code, out, err = self.gate('export', FOOT)
        self.assertEqual((code, err), (0, ''), out); self.assertIn('stamped -> stl/LampFoot_rev2.stl', out)
        self.assertFalse(os.path.exists(os.path.join(self.stl_dir, 'lamp_foot.stl')))
        r = self.own(GATE, 'doctor')
        self.assertNotIn('scan again', r.stdout); self.assertIn("'Shade' names no part of the last scan", r.stdout)


    def map_foot(self):
        layout = self.read('gate', 'layout.json'); layout['documents'][0]['stl_names'] = {'Lamp foot': 'LampFoot_rev2.stl'}
        gate_common.write_json(self.path('gate', 'layout.json'), layout)

    def test_a_part_exported_before_the_map_keeps_its_stamp_and_moves_with_the_next_export(self):
        self.ready()
        self.assertEqual(self.gate('export', FOOT)[0], 0)                                # stl/lamp_foot.stl and its stamp, before the map
        first, entry = self.stamp(FOOT), gate_common.load_census(self.root)['parts'][FOOT]
        self.map_foot()
        n = self.exports()
        code, out, err = self.gate('export', FOOT)                                       # the export scans first: the map takes effect now
        self.assertEqual((code, err), (0, ''), out); self.assertNotIn('GONE', out); self.assertNotIn('NEW', out); self.assertNotIn('CHANGED', out)
        self.assertIn('STL      %s: stl/lamp_foot.stl -> stl/LampFoot_rev2.stl (gate/layout.json)' % FOOT, out)
        self.assertIn('stl/lamp_foot.stl stays in the folder: delete it once the new file is exported', out)
        after = gate_common.load_census(self.root)['parts'][FOOT]                        # only the path changed
        self.assertEqual(dict(after, stl=None), dict(entry, stl=None)); self.assertEqual(after['stl'], 'stl/LampFoot_rev2.stl')
        self.assertEqual(gate_stamp.verify(self.root, self.reg, first)[0], 'valid')      # a stamp is tied to the geometry, not to the path
        self.assertNotIn(FOOT, gate_plan.changed_parts(self.root))
        # the mapped file is not there yet: a full export writes it, in the same call
        self.assertEqual(self.exports(), n + 1); self.assertIn('stamped -> stl/LampFoot_rev2.stl', out); self.assertNotIn('NOTE', out)
        both = self.stamps(FOOT)
        self.assertEqual(sorted(s_['stl'] for s_ in both), ['stl/LampFoot_rev2.stl', 'stl/lamp_foot.stl'])
        self.assertEqual(sorted(os.listdir(gate_common.gate_path(self.root, 'stamps', *FOOT.split('/')))), ['%s.2.json' % self.sigs[FOOT][:12], '%s.json' % self.sigs[FOOT][:12]])
        self.assertEqual(sorted(os.listdir(self.stl_dir)), ['LampFoot_rev2.stl', 'lamp_foot.stl'])        # the old file is nobody's now: the scan said to delete it
        st = gate_report.status(self.root)                                               # while the part is unchanged both read as stamped ...
        self.assertEqual((st['stl_states'], st['unstamped_stls']), ({'stl/LampFoot_rev2.stl': 'valid', 'stl/lamp_foot.stl': 'valid'}, []))
        self.model(foot='v3'); self.assertEqual(self.gate('scan')[0], 0)                 # ... and after the next change the old one stays behind for good
        self.assertEqual(gate_report.status(self.root)['stl_states'], {'stl/LampFoot_rev2.stl': 'stale', 'stl/lamp_foot.stl': 'stale'})

    def test_a_file_with_the_stamped_geometry_in_other_bytes_is_exported_again(self):
        """The project has the part's mesh from before the gate, written by another tool: the same geometry, other bytes.
        `status` reads an STL by its bytes, so "already stamped: nothing to do" would leave that file unstamped for ever."""
        self.ready()
        self.assertEqual(self.gate('export', FOOT)[0], 0)
        H.write_stl(self.path('stl', 'LampFoot_rev2.stl'), E.MESHES['foot v2']())        # no exporter header: other bytes
        self.assertNotEqual(gate_common.sha256_file(self.path('stl', 'LampFoot_rev2.stl')), self.stamp(FOOT)['stl_sha256'])
        self.map_foot(); self.assertEqual(self.gate('scan')[0], 0)
        self.assertEqual(gate_report.status(self.root)['stl_states']['stl/LampFoot_rev2.stl'], 'none')
        n = self.exports()
        code, out, err = self.gate('export', FOOT)
        self.assertEqual((code, err, self.exports()), (0, '', n + 1), out); self.assertNotIn('already stamped', out); self.assertIn('stamped -> stl/LampFoot_rev2.stl', out)
        with open(self.path('stl', 'LampFoot_rev2.stl'), 'rb') as f: self.assertEqual(f.read(), E.stl_bytes(E.MESHES['foot v2'](), NAMES[FOOT]))
        self.assertEqual(self.stamp(FOOT)['stl_sha256'], gate_common.sha256_file(self.path('stl', 'LampFoot_rev2.stl')))
        code, out, err = self.gate('status')
        self.assertEqual(code, 0, out); self.assertIn('0 STLs without a valid stamp', out)       # export and status say the same
        code, out, err = self.gate('export', FOOT)
        self.assertEqual((code, self.exports()), (0, n + 1)); self.assertIn('already stamped', out)

    def test_a_census_entry_without_a_path_takes_the_file_of_the_layout(self):
        """An entry a scan wrote before the document had its "stl_dir" has no "stl": the file is the one gate/layout.json
        gives the part today, map included, for the export and for the test "is this STL a Fusion part's file"."""
        self.map_foot(); self.ready()
        census = gate_common.load_census(self.root)
        for part in (FOOT, HUB): del census['parts'][part]['stl']
        gate_common.write_json(gate_common.census_path(self.root), census)
        code, out, err = self.gate('export', FOOT)
        self.assertEqual((code, err), (0, ''), out); self.assertIn('stamped -> stl/LampFoot_rev2.stl', out); self.assertEqual(os.listdir(self.stl_dir), ['LampFoot_rev2.stl'])
        layout = gate_common.load_layout(self.root)
        for part, name in ((FOOT, 'LampFoot_rev2.stl'), (HUB, 'turntable.stl')):         # a mesh no exporter wrote and no stamp knows: only the path says whose it is
            path = self.path('stl', name); H.write_stl(path, H.box(7, 7, 7))
            self.assertIn('it is the export file of the Fusion part %s' % part, gate_stamp._fusion_origin(self.root, path, census, layout) or '')
        self.assertIsNone(gate_stamp._fusion_origin(self.root, self.path('stl', 'lamp_foot.stl'), census, layout))       # the foot's default name is not its file here
        # a part whose document left gate/layout.json keeps the file its census entry names; with no entry path, its default file
        census['parts']['old/x'] = {'doc': 'old', 'name': 'X', 'source': 'fusion', 'sig': 'ab' * 32, 'stl': 'stl/Legacy X.stl', 'gone': False}
        census['parts']['old/y'] = {'doc': 'old', 'name': 'Y', 'source': 'fusion', 'sig': 'cd' * 32, 'gone': False}
        for name, part in (('Legacy X.stl', 'old/x'), ('y.stl', 'old/y')):
            path = self.path('stl', name); H.write_stl(path, H.box(6, 6, 6))
            self.assertIn('it is the export file of the Fusion part %s' % part, gate_stamp._fusion_origin(self.root, path, census, layout) or '')


class Endpoint(Walk):
    def test_an_endpoint_elsewhere_stops_the_export_before_anything_is_sent(self):
        """A unit carries project paths and names: it goes to this machine only. Both calls of an export are held to that:
        the export unit, and the short "is the part unchanged" read of a part that is stamped already."""
        self.ready()
        good = self.read('gate', 'layout.json')
        elsewhere = json.loads(json.dumps(good)); elsewhere['fusion']['endpoint'] = 'http://example.com:27182/mcp'
        gate_common.write_json(self.path('gate', 'layout.json'), elsewhere)
        sent = len(self.fusion.calls)
        code, out, err = self.gate('export', FOOT)
        self.assertEqual(code, 4, out + err); self.assertIn('"fusion"."endpoint" must be http://127.0.0.1:<port>', err); self.assertIn('example.com', err)
        self.assertEqual((len(self.fusion.calls), self.stamps(FOOT), os.path.exists(self.path('stl', 'lamp_foot.stl'))), (sent, [], False))
        gate_common.write_json(self.path('gate', 'layout.json'), good)
        self.assertEqual(self.gate('export', FOOT)[0], 0)
        gate_common.write_json(self.path('gate', 'layout.json'), elsewhere)
        sent = len(self.fusion.calls)
        code, out, err = self.gate('export', FOOT)
        self.assertEqual(code, 4, out + err); self.assertIn('"fusion"."endpoint" must be http://127.0.0.1:<port>', err); self.assertNotIn('already stamped', out)
        self.assertEqual(len(self.fusion.calls), sent)


class SeveralTargets(Walk):
    """One document, two .3mf projects: a fine one and one that prints 0.3 layers."""

    def setUp(self):
        super().setUp()
        project = self.read('gate', 'project.json'); layout = self.read('gate', 'layout.json')
        self.coarse = dict(TARGET, layer_height=0.3, initial_layer_print_height=0.3)
        project['targets']['coarse'] = self.coarse
        layout['projects'].append({'key': 'coarse', 'file': 'bambulab/Coarse.3mf', 'stl_dir': 'stl'})
        gate_common.write_json(self.path('gate', 'project.json'), project); gate_common.write_json(self.path('gate', 'layout.json'), layout)
        # as the shipped probes do: no unit reads the target or a parameter of it, so its facts hold for every target
        # (a unit that says it read everything would be measured again for the other target, and its export refused)
        self.fusion.default_inputs = {'params': [], 'tables': [], 'job': ['doc', 'component_id', 'occurrence', 'body', 'features']}

    def test_a_run_names_its_target_and_one_part_is_exported_for_both(self):
        self.model(foot='v2', hub='v1', clip='v1')
        code, out, err = self.gate('run', '--wall', '600')
        self.assertEqual(code, 4); self.assertIn('declares several targets', err); self.assertIn('--for KEY (coarse, %s)' % PROJ_KEY, err)
        self.assertEqual(self.fusion.calls, [])                                  # refused before anything was asked of Fusion
        self.ready(target=PROJ_KEY)
        self.assertEqual({json.dumps(j['target'], sort_keys=True) for j in self.fusion.calls if j['part']}, {json.dumps(TARGET, sort_keys=True)})
        judged = gate_common.load_facts(self.root, FOOT, self.sigs[FOOT])['judged']
        self.assertEqual(judged['print.z_layers']['params']['target.layer_height'], 0.2)        # the run judged the design stage for the fine project
        code, out, err = self.gate('export', FOOT)
        self.assertEqual(code, 4); self.assertIn('name one with --for KEY (coarse, %s)' % PROJ_KEY, err); self.assertEqual(self.exports(), 0)
        code, out, err = self.gate('export', FOOT, '--for', 'coarse')            # the insert profile was printed under the fine project only
        self.assertEqual(code, 1, out); self.assertIn('profile measured under other settings', out); self.assertEqual(self.exports(), 0)
        tables = self.read('gate', 'owner', 'tables.json')                       # the owner prints the coupon plate of the coarse project too
        tables['inserts']['measured_under']['M3x4x5'].append(dict(tables['inserts']['measured_under']['M3x4x5'][0], layer_height=0.3))
        gate_common.write_json(self.path('gate', 'owner', 'tables.json'), tables)
        code, out, err = self.gate('export', FOOT, '--for', 'coarse')            # the other project: the stored facts are judged again for it
        self.assertEqual((code, err), (0, ''), out); self.assertIn('stamped -> stl/lamp_foot.stl', out)
        s = self.stamp(FOOT)
        self.assertEqual((s['for'], s['target'], s['checks']['print.z_layers']['params']['target.layer_height']), ('coarse', self.coarse, 0.3))
        code, out, err = self.gate('export', FOOT, '--for', PROJ_KEY)            # and back: no "judge again" loop
        self.assertEqual((code, err), (0, ''), out)
        both = {s['for']: s for s in self.stamps(FOOT)}
        self.assertEqual(sorted(both), ['coarse', PROJ_KEY]); self.assertEqual(both[PROJ_KEY]['checks']['print.z_layers']['params']['target.layer_height'], 0.2)
        for key, target in (('coarse', self.coarse), (PROJ_KEY, TARGET)):        # each stamp holds for its own project, with no target handed over too
            self.assertEqual(gate_stamp.verify(self.root, self.reg, both[key], target=target)[0], 'valid')
            self.assertEqual(gate_stamp.verify(self.root, self.reg, both[key])[0], 'valid')
        self.assertNotIn(FOOT, gate_plan.changed_parts(self.root))
        self.assertEqual(self.gate('export', FOOT, '--for', 'coarse')[0], 0); self.assertEqual(len(self.stamps(FOOT)), 2)     # stamped already: nothing new is written

    def test_a_parameter_as_evidence_names_its_project(self):
        self.ready(answer=False, target=PROJ_KEY)
        qid = next(q for q, part, rule in self.questions() if rule == 'hardware.bought_from_step' and part == FOOT)
        code, out, err = self.gate('attest', 'answer', qid, '--param', 'inserts.wall_target')
        self.assertEqual(code, 4); self.assertIn('name the .3mf project with --for KEY', err)
        code, out, err = self.gate('attest', 'answer', qid, '--param', 'target.nozzle', '--for', PROJ_KEY)
        self.assertEqual(code, 4); self.assertIn('a value of the declared target, not a measured size', err)


class Waiver(Walk):
    def test_a_withdrawn_waiver_behind_an_answer_opens_the_stamp_again(self):
        self.ask_rules()
        self.ready(answer=False)                                                 # the foot has an insert pocket: how many screws hold its joints is asked
        qid = next(q for q, part, rule in self.questions() if rule == 'screws.count_load_joint' and part == FOOT)
        code, out, err = self.gate('owner', 'waive', qid, '--reason', STEEL, '--quote', SAID)
        self.assertEqual((code, err), (0, ''), out); self.assertIn('question %s is answered by it' % qid, out)
        self.answer_all()
        self.assertEqual(self.run_gate('--offline')[0], 0)
        self.assertEqual(self.gate('export', FOOT)[0], 0)
        s = self.stamp(FOOT)
        self.assertIn(qid, s['answers']); self.assertEqual(gate_stamp.verify(self.root, self.reg, s)[0], 'valid')
        self.assertNotIn(FOOT, gate_plan.changed_parts(self.root))
        waivers = self.read('gate', 'owner', 'waivers.json')
        gate_common.write_json(self.path('gate', 'owner', 'waivers.json'), dict(waivers, waivers=[]))          # the owner takes the waiver back
        state, why = gate_stamp.verify(self.root, self.reg, s)
        self.assertEqual(state, 'stale'); self.assertIn('answer(s) this stamp rests on are gone (%s)' % qid, why)
        self.assertIn(FOOT, gate_plan.changed_parts(self.root))                  # status: changed since its stamp
        self.assertIn(qid, [q for q, part, rule in self.questions()])            # and the question is open again
        code, out, err = self.gate('export', FOOT)
        self.assertEqual(code, 2); self.assertIn('question(s) open', out)        # no "already stamped" on a stamp whose answer is gone


class Adoption(Walk):
    """The first adoption of a project that existed before the gate: three parts nobody is changing."""

    def audited(self, **near):
        self.model(foot='v2', hub='v1', clip='v1')
        self.facts(FOOT, fusion_census=E.foot_census(), fusion_inserts=E.foot_pocket(2.5)); self.facts(HUB); self.facts(CLIP)       # the foot has its thin wall
        self.near(**near)
        code, out, err = self.gate('run', '--audit', '--all', '--wall', '600')
        self.assertEqual(err, '', out)
        return code, out

    def test_the_audit_asks_nothing_and_says_what_waits(self):
        code, out = self.audited()
        self.assertEqual(code, 0); self.assertIn('0 open, 0 questions', out); self.assertIn('(audit: nothing blocks)', out)
        report = self.latest()
        t = report['totals']
        self.assertEqual((t['attest_open'], t['block_open']), (0, 0)); self.assertGreater(t['inherited'], 0); self.assertGreater(t['waiting'], 0); self.assertGreater(t['by_facts'], 0)
        self.assertEqual(t['waiting'] + t['by_facts'], 3 * self.n_design)                 # every standing question is answered by facts or waits; none is dropped
        self.assertIn('%d question(s) wait at inherited parts' % t['waiting'], out); self.assertIn('%d question(s) answered by the facts' % t['by_facts'], out)
        self.assertEqual({p['part']: (p['state'], p['verdict'], p['inherited_part']) for p in report['parts']}, {p: ('inherited', 'inherited', True) for p in NAMES})
        prop = self.read('gate', 'proposals', 'triage.json')['data']
        self.assertEqual({p: e['sig'] for p, e in prop['baseline'].items()}, self.sigs)   # the geometry the audit judged, per part
        self.assertTrue(all(i['part'] == FOOT for i in prop['items'])); self.assertTrue(prop['items'])
        md = self.gate('report', '--last')[1]
        self.assertIn('An inherited part: the audit judged this geometry and it has not changed since. It has no stamp.', md)
        self.assertIn('### Standing questions that wait', md)
        code, out, err = self.gate('attest', 'list')
        self.assertEqual(code, 0); self.assertIn('0 question(s) open', out); self.assertIn('inherited part(s) wait', out)
        self.assertEqual(gate_report.status(self.root)['questions'], [])                  # the session-start digest asks nothing either
        self.assertFalse(os.path.exists(gate_common.gate_path(self.root, 'stamps')))      # and nothing is stamped by an audit

    def test_what_the_facts_prove_is_answered_and_the_rest_waits(self):
        self.audited(**{CLIP: [MOTOR]})                                                   # a motor stands 6.5 mm from the clip, named two levels up
        self.assertEqual(self.by_facts(HUB), ['hardware.bought_from_step', 'motors.lead_path', 'screws.count_load_joint'])       # a plain ring: no hole, nothing near
        self.assertEqual(self.by_facts(FOOT), ['motors.lead_path'])                                  # it has an insert pocket: the screw questions stay
        # the motor model beside the clip is a bought body named "Rotor": no unit read its shape, so "no bearing here" is not proven either
        self.assertEqual(self.by_facts(CLIP), ['screws.count_load_joint'])
        waiting = self.gate('attest', 'list', '--part', CLIP)[1]
        self.assertIn('motors.lead_path', waiting); self.assertIn('the facts: 1 body(ies) near or on an axis match motors.ref_pattern (Rotor)', waiting)
        na = next(q for q in self.latest()['questions'] if q['part'] == HUB and q['rule'] == 'screws.count_load_joint')['answer']
        self.assertEqual((na['kind'], na['as']), ('facts', 'na')); self.assertIn('no screw holds it', na['value'])

    def test_a_part_that_changes_is_asked_and_the_others_still_wait(self):
        self.audited()
        self.ask_rules()
        code, out, err = self.gate('owner', 'adopt', '--quote', 'take the model as it is today')
        self.assertEqual((code, err), (0, ''), out); self.assertIn('3 part(s) added', out)
        owner = self.read('gate', 'owner', 'triage.json')
        self.assertEqual(sorted(owner['baseline']), sorted(NAMES)); self.assertEqual(len(owner['items']), len(self.read('gate', 'proposals', 'triage.json')['data']['items']))
        sent = len(self.fusion.calls)
        code, out = self.run_gate()
        self.assertEqual(code, 0); self.assertIn('nothing to check', out)                 # nothing changed: nothing new, nothing asked ...
        self.assertEqual([j['probe'] for j in self.fusion.calls[sent:]], ['fusion_scan']) # ... and no inherited part is measured again
        new, = self.model(clip='v2')                                                      # the clip is changed
        self.facts(CLIP); self.near()
        code, out = self.run_gate()
        self.assertEqual(code, 2)                                                         # its standing questions are asked now
        asked = self.questions()
        self.assertEqual({part for _, part, _ in asked}, {CLIP}); self.assertIn('process.visual_check', [rule for _, _, rule in asked])
        report = self.latest()
        self.assertEqual({p['part']: p['state'] for p in report['parts']}, {CLIP: 'changed'})       # a run of what changed: the inherited parts are not in it
        st = gate_report.status(self.root)
        self.assertEqual((st['inherited_parts'], {q['part'] for q in st['questions']}), (2, {CLIP}))
        code, out, err = self.gate('run', '--audit', '--part', CLIP, '--wall', '600')     # a second audit does not make it inherited again
        self.assertEqual(code, 2); self.assertIn('not inherited (changed after the audit, or stamped): %s' % CLIP, out)

    def test_the_export_of_an_inherited_part_asks_its_questions_and_then_it_is_a_stamped_part(self):
        self.audited()
        self.fusion.set_export(HUB, E.stl_bytes(E.MESHES['hub'](), NAMES[HUB]))
        code, out, err = self.gate('export', HUB)
        self.assertEqual(code, 2); self.assertIn('question(s) open', out); self.assertEqual(self.stamps(HUB), [])      # no stamp without its questions
        rows = re.findall(r'^(Q-[0-9a-f]{6})  (\S+)  (\S+)', self.gate('attest', 'list', '--part', HUB)[1], re.M)
        self.assertTrue(rows); self.assertEqual({part for _, part, _ in rows}, {HUB})
        shot = self.path('.shots', 'hub.png')
        with open(shot, 'wb') as f: f.write(b'\x89PNG\r\n\x1a\n' + b'\0' * 32)
        for _ in range(3):                                                                # the design questions, then the ones the export adds
            for qid, part, rule in re.findall(r'^(Q-[0-9a-f]{6})  (\S+)  (\S+)', self.gate('attest', 'list', '--part', HUB)[1], re.M):
                a = self.reg['rules'][rule]['attest']
                for how in ([['--label', x] for x in a['labels']] if 'label' in a['evidence'] else [['--shot', shot]]):
                    if self.gate('attest', 'answer', qid, *how)[0] in (0, 2): break
            code, out, err = self.gate('export', HUB)
            if code != 2: break
        self.assertEqual((code, err), (0, ''), out); self.assertEqual(len(self.stamps(HUB)), 1)
        v = gate_plan.part_verdict(self.root, self.reg, HUB, stages=('design', 'export'), defer_inherited=True)
        self.assertEqual((v['inherited_part'], v['state'], v['deferred_questions']), (False, 'unchanged', []))          # stamped: an ordinary part from now on
        self.assertEqual(gate_report.status(self.root)['inherited_parts'], 2)

    def test_enforce_refuses_the_unstamped_mesh_of_an_inherited_part_too(self):
        # the project existed before the gate: its .3mf already holds the foot and the turntable, exported by hand
        H.write_stl(os.path.join(self.stl_dir, 'lamp_foot.stl'), E.MESHES['foot v2']()); H.write_stl(os.path.join(self.stl_dir, 'turntable.stl'), E.MESHES['hub']())
        script = self.text('bambulab', 'build_3mf.py')
        i = script.index('PLATES = ['); j = script.index(']\n', i) + 2
        with open(self.path('bambulab', 'build_3mf.py'), 'w', encoding='utf-8') as f: f.write(script[:i] + 'PLATES = %r\n' % (E.PLATES,) + script[j:])
        r = self.own(os.path.join('bambulab', 'build_3mf.py'))                            # observe mode: reported, and written
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.assertEqual(self.objects(), ['Lamp foot', 'Turntable'])
        self.audited()
        self.assertEqual(self.stamps(FOOT), [])                                           # inherited: judged, and no stamp
        gate_common.write_json(self.path('gate', 'project.json'), dict(self.read('gate', 'project.json'), mode='enforce'))
        before = self.raw()
        infos, files = bambu3mf.load(self.proj)
        for save in (lambda: bambu3mf.save(self.proj, files, infos), lambda: bambu3mf.build(self.proj, self.stl_dir, E.PLATES)):
            with self.assertRaises(GuardRefusal) as c: G.quiet(save)              # in the file before, inherited, exported by hand: nothing is let through for it
            self.assertEqual(sorted(row['object'] for row in c.exception.rows), ['Lamp foot', 'Turntable'])
            self.assertEqual(self.raw(), before)
        self.assertIn('REFUSED', self.guard_log())

    def test_a_first_scan_names_the_parts_that_look_bought(self):
        rows = [fake_fusion.scan_part('Lamp foot', fake_fusion.sig_of('foot'), faces=48), fake_fusion.scan_part('Steel pin', fake_fusion.sig_of('pin'), faces=3),
                fake_fusion.scan_part('Spacer tube', fake_fusion.sig_of('tube'), faces=4), fake_fusion.scan_part('Cable clip', fake_fusion.sig_of('clip'), faces=22)]
        self.fusion.clear('fusion_scan', None); self.fusion.set_facts('fusion_scan', None, {'parts': rows, 'refs': []})
        code, out, err = self.gate('scan')
        self.assertEqual((code, err), (0, ''))
        hint = next(line for line in out.splitlines() if line.startswith('HINT'))
        self.assertIn("2 new part(s) have 4 faces or fewer", hint); self.assertIn("'Spacer tube'", hint); self.assertIn("'Steel pin'", hint)
        self.assertNotIn('Lamp foot', hint); self.assertIn('"not_printed" pattern of gate/project.json', hint)
        self.assertIn('scan: 4 parts, 4 new', out)                                        # a hint: both are still listed as parts
        self.assertNotIn('HINT', self.gate('scan')[1])                                    # said once, for what is new

    def test_the_owner_decides_a_whole_group_at_once(self):
        self.audited()
        self.ask_rules()
        code, out, err = self.gate('triage')
        self.assertEqual(code, 1)
        gid = re.search(r'^  (G-[0-9a-f]{6})  \S+ +inserts\.wall', out, re.M).group(1)
        code, out, err = self.gate('owner', 'decide', gid, 'tolerate', '--quote', 'that wall has held for two years')
        self.assertEqual((code, err), (0, ''), out); self.assertIn('adopted the audit', out); self.assertIn('%s = tolerate: 1 row(s) of inserts.wall on %s' % (gid, FOOT), out)
        owner = self.read('gate', 'owner', 'triage.json')
        self.assertEqual([(i['decision'], i['quote']) for i in owner['items'] if i['rule'] == 'inserts.wall'], [('tolerate', 'that wall has held for two years')])
        self.assertEqual(sorted(owner['baseline']), sorted(NAMES))                        # the first decision adopts the parts of the audit too
        self.assertEqual(self.gate('owner', 'decide', gid, 'fix', '--quote', 'again')[0], 4)       # every row of the group is decided: one row is changed by its T-id
        self.assertEqual(self.gate('owner', 'decide', 'G-000000', 'fix', '--quote', 'q')[0], 4)


class Forked(Walk):
    def test_a_changed_copy_stops_what_judges_and_lets_the_reading_commands_run(self):
        r = self.own(GATE, 'verify')
        self.assertNotIn('FORKED', r.stderr); self.assertNotIn('template.3mf', r.stdout)       # the empty template is no unregistered project
        with open(self.path('gate', 'tools', 'gate_judges.py'), 'a', encoding='utf-8') as f: f.write('\n# a change made in the project\n')
        for argv in (['run', '--offline'], ['scan'], ['verify'], ['export', FOOT], ['attest', 'answer', 'Q-000000', '--label', 'x'],
                     ['owner', 'decide', 'T-1', 'fix', '--quote', 'fix it']):
            with self.subTest(command=argv[0]):
                r = self.own(GATE, *argv)
                self.assertEqual(r.returncode, 4, r.stdout + r.stderr); self.assertIn('vendored gate is FORKED', r.stderr); self.assertIn('tools/gate_judges.py', r.stderr)
                self.assertIn('does not run on a changed copy', r.stderr)
        for argv in (['status'], ['attest', 'list'], ['params', 'check'], ['rules']):
            with self.subTest(command=argv[0]):
                r = self.own(GATE, *argv)
                self.assertNotIn('FORKED', r.stderr); self.assertNotEqual(r.returncode, 4, r.stdout + r.stderr)
        r = self.own(GATE, 'doctor')
        self.assertEqual(r.returncode, 4); self.assertIn('changed: tools/gate_judges.py', r.stdout)
        self.assertEqual(self.fusion.calls, [])


if __name__ == '__main__':
    unittest.main()
