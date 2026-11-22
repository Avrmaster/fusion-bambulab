"""Objects outside the gate (gate/owner/outside.json): a .3mf project that also prints parts of a document the gate does
not register can be saved in enforce mode once the owner declares those objects, each by the mesh it has in the saved
file. Such an object passes only while its mesh is that one; a part of a registered document or generator can never be
declared; the declaration is an owner command with its quote, its dry run bound by the hook; status, verify and the
session hooks list what is declared. No Fusion, no Bambu Studio; every name and number is invented."""
import contextlib, io, json, os, shutil, tempfile, time, types, unittest
from unittest import mock
for _k in ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID'): os.environ.pop(_k, None)      # gate.py owner from an agent session needs the hook's ticket: a test is no agent session
os.environ.pop('CLAUDE_PROJECT_DIR', None)
import test_guard as G
from test_guard import GuardRefusal, PLATES, quiet
import bambu3mf, gate, gate_common, gate_guard, gate_judges, gate_report, gate_watch, meshid
from test_stamp import SIG1, SIG2

ASK = {'permissions': {'deny': ['Edit(/gate/owner/**)', 'Write(/gate/owner/**)'], 'ask': ['Bash(python3 gate/tools/gate.py owner *)']}}
WRIST = [('wrist plate', [('Wrist gear', 'wrist_gear.stl', False), ('Wrist spacer', 'wrist_spacer.stl', False)])]
REASON = 'the wrist gears come from the older wrist document, which the gate does not register yet'


class Outside(G.Guarded):
    mode = 'enforce'

    def setUp(self):
        super().setUp()
        gate_common.write_json(os.path.join(self.d, '.claude', 'settings.json'), ASK)
        self.stl('wrist_gear', d=3.0); self.stl('wrist_spacer', d=5.0)

    def gate(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = gate.main(['--root', self.d] + list(argv))
        return code, out.getvalue(), err.getvalue()

    def declare(self, *names, dry=False, quote='leave the wrist parts as they are'):
        return self.gate('owner', 'outside', 'main', *names, '--reason', REASON, *(['--dry-run'] if dry else ['--quote', quote]))

    def wrist_project(self):
        """A project in the file on disk with two parts the gate covers (stamped) and two wrist parts of a document it
        does not register: built before the gate watched, so no journal knows them."""
        self.ledger(); self.stamp_both()
        self.set_project('observe'); self.build(PLATES + WRIST); self.set_project('enforce')

    def raw(self):
        with open(self.proj, 'rb') as f: return f.read()

    def resave(self):
        return quiet(bambu3mf.save, self.proj, *reversed(bambu3mf.load(self.proj)))

    def outside_file(self): return gate_common.read_json(gate_common.outside_path(self.d), None)


class Guard(Outside):
    def test_the_deadlock_without_a_declaration(self):
        self.wrist_project()
        with self.assertRaises(GuardRefusal) as c: self.resave()
        self.assertEqual(sorted(r['object'] for r in c.exception.rows), ['Wrist gear', 'Wrist spacer'])

    def test_a_declared_object_with_the_same_mesh_passes_in_enforce_and_is_said(self):
        self.wrist_project()
        code, out, err = self.declare('Wrist gear', 'Wrist spacer')
        self.assertEqual((code, err), (0, ''), out)
        data = self.outside_file()['projects']['main']
        poses = self.poses()
        self.assertEqual(data['file'], 'bambulab/Demo.3mf')
        self.assertEqual({n: e['geom_hash'] for n, e in data['objects'].items()}, {'Wrist gear': poses['Wrist gear'], 'Wrist spacer': poses['Wrist spacer']})
        e = data['objects']['Wrist gear']
        self.assertEqual((e['reason'], e['quote'], e['plate_index'], e['was']), (REASON, 'leave the wrist parts as they are', 3, None))
        for save in (self.resave, lambda: quiet(bambu3mf.set_settings, self.proj, '', [('brim_type', 'no_brim')]),
                     lambda: (None, self.build(PLATES + WRIST, template_first=False))):
            _, out = save()
            self.assertIn(gate_guard.declared_outside_line('bambulab/Demo.3mf', ['Wrist gear', 'Wrist spacer']), out)
            self.assertNotIn('REFUSED', self.log())
        r, out = quiet(gate_guard.check, self.proj, bambu3mf.project_meshes(self.archive()), files=self.archive())
        self.assertEqual((sorted(r['declared_outside']), r['unstamped'], r['unchecked']), (['Wrist gear', 'Wrist spacer'], [], []))
        self.assertIn('2 declared outside the gate by the owner (Wrist gear, Wrist spacer)', r['line'])
        self.assertNotIn('Wrist gear', r['stamps'])                                 # passed, never stamped
        saves = gate_common.read_json(gate_guard.saves_path(self.d))['projects']['bambulab/Demo.3mf']
        self.assertEqual((sorted(saves['declared_outside']), saves['unchecked']), (['Wrist gear', 'Wrist spacer'], []))

    def test_a_changed_mesh_under_a_declared_name_is_refused_like_any_unstamped_mesh(self):
        self.wrist_project(); self.declare('Wrist gear', 'Wrist spacer')
        self.stl('wrist_gear', d=3.5)                                               # the hole moved: another mesh under the same name
        before = self.raw()
        with self.assertRaises(GuardRefusal) as c: quiet(self.build, PLATES + WRIST, template_first=False)
        self.assertEqual([r['object'] for r in c.exception.rows], ['Wrist gear'])
        self.assertIn('changed since it was declared outside the gate', c.exception.rows[0]['why'])
        self.assertIn('export it through the gate or declare it again', str(c.exception))
        self.assertEqual(self.raw(), before)
        self.set_project('observe')                                                # observe lets it through unchecked, and logs why
        out = self.build(PLATES + WRIST, template_first=False)
        self.assertIn('Wrist gear', out); self.assertIn('changed since it was declared outside the gate', self.log())
        self.set_project('enforce')
        with self.assertRaises(GuardRefusal): self.resave()                        # the changed mesh now lies in the file: still refused
        code, out, err = self.declare('Wrist gear')                                # declared again: the new mesh is the declared one
        self.assertEqual((code, err), (0, ''), out); self.assertIn('(was ', out)
        self.resave()
        self.assertIsNotNone(self.outside_file()['projects']['main']['objects']['Wrist gear']['was']['geom_hash'])

    def test_the_same_mesh_under_another_name_and_a_copy_outside_the_tree_get_nothing(self):
        self.wrist_project(); self.declare('Wrist gear', 'Wrist spacer')
        files = self.archive()
        cfg = files['Metadata/model_settings.config'].decode().replace('value="Wrist gear"', 'value="Wrist gear 2"')
        with self.assertRaises(GuardRefusal) as c: quiet(gate_guard.check, self.proj, bambu3mf.project_meshes(dict(files, **{'Metadata/model_settings.config': cfg.encode()})), files=files)
        self.assertEqual([r['object'] for r in c.exception.rows], ['Wrist gear 2'])
        scratch = self.outside(); shutil.copyfile(self.proj, scratch)
        with self.assertRaises(GuardRefusal) as c: quiet(gate_guard.check, scratch, bambu3mf.project_meshes(bambu3mf.load(scratch)[1]), root=self.d, files=bambu3mf.load(scratch)[1])
        self.assertIn('Wrist gear', [r['object'] for r in c.exception.rows])

    def test_a_broken_owner_file_lets_nothing_through(self):
        self.wrist_project(); self.declare('Wrist gear', 'Wrist spacer')
        with open(gate_common.outside_path(self.d), 'w') as f: f.write('{"projects": ')
        with self.assertRaises(GuardRefusal) as c: self.resave()
        self.assertIn('outside.json', str(c.exception))
        gate_common.write_json(gate_common.outside_path(self.d), {'projects': {'main': {'objects': {'Wrist gear': {'reason': 'no hash'}}}}})
        with self.assertRaises(GuardRefusal) as c: self.resave()
        self.assertIn('Wrist gear', [r['object'] for r in c.exception.rows])


class Declare(Outside):
    def test_a_census_part_a_generator_and_a_name_of_the_census_are_refused(self):
        self.wrist_project()
        code, _, err = self.declare('bracket')                                    # tied by the journal and its STL to demo/bracket
        self.assertEqual(code, 4); self.assertIn('never outside the gate', err); self.assertIn('demo/bracket', err)
        self.assertIsNone(self.outside_file())
        self.census('demo/wrist_spacer', SIG2)                                     # a census part of that name, no STL yet
        code, _, err = self.declare('Wrist gear', 'Wrist spacer')
        self.assertEqual(code, 4); self.assertIn('"Wrist spacer": its name is that of the part demo/wrist_spacer', err); self.assertNotIn('"Wrist gear"', err)
        self.assertIsNone(self.outside_file())                                      # all or nothing

    def test_a_generator_is_refused(self):
        self.stl('shim', d=2.0); self.ledger(); self.stamp_both()
        self.set_project('observe'); self.build(PLATES + [('shim plate', [('Shim', 'shim.stl', False)])]); self.set_project('enforce')
        code, _, err = self.declare('Shim')
        self.assertEqual(code, 4); self.assertIn('demo/shim', err)

    def test_what_cannot_be_declared(self):
        self.wrist_project()
        for argv, why in ((['owner', 'outside', 'other', 'Wrist gear', '--reason', REASON, '--quote', 'q'], 'no .3mf project of gate/layout.json'),
                          (['owner', 'outside', 'main', 'Wrist nut', '--reason', REASON, '--quote', 'q'], 'holds no object "Wrist nut"'),
                          (['owner', 'outside', 'main', 'Wrist gear', '--reason', ' ', '--quote', 'q'], '--reason must say why'),
                          (['owner', 'outside', 'main', 'Wrist gear', '--reason', REASON], 'needs --quote')):
            code, _, err = self.gate(*argv)
            self.assertEqual(code, 4, argv); self.assertIn(why, err, argv)
        self.assertIsNone(self.outside_file())

    def test_the_dry_run_shows_the_change_and_the_bundle_entry_and_writes_nothing(self):
        self.wrist_project()
        code, out, err = self.declare('Wrist gear', dry=True)
        self.assertEqual((code, err), (0, ''), out)
        self.assertIsNone(self.outside_file())
        self.assertIn('+++ b/gate/owner/outside.json', out); self.assertIn('DRY RUN: nothing was written', out)
        entry = json.loads(out.split('bundle entry: ')[1].splitlines()[0])
        self.assertEqual(entry, {'kind': 'outside', 'project': 'main', 'objects': ['Wrist gear'], 'reason': REASON, 'expect': {'Wrist gear': self.poses()['Wrist gear']}})
        prop = os.path.join(self.d, 'gate', 'proposals', 'wrist.json')
        os.makedirs(os.path.dirname(prop))
        with open(prop, 'w') as f: json.dump({'bundle': 1, 'title': 'wrist', 'entries': [dict(entry, expect={'Wrist gear': 'f' * 40})]}, f)
        code, _, err = self.gate('owner', 'bundle', prop, '--quote', 'yes')
        self.assertEqual(code, 4); self.assertIn('not the one the entry names', err); self.assertIsNone(self.outside_file())
        with open(prop, 'w') as f: json.dump({'bundle': 1, 'title': 'wrist', 'entries': [entry]}, f)
        code, out, err = self.gate('owner', 'bundle', prop, '--quote', 'yes')
        self.assertEqual((code, err), (0, ''), out)
        self.assertEqual(list(self.outside_file()['projects']['main']['objects']), ['Wrist gear'])


class Listed(Outside):
    def test_status_verify_and_the_session_hooks_name_them_in_one_line_per_project(self):
        self.wrist_project(); self.declare('Wrist gear', 'Wrist spacer')
        line = 'gate: 2 object(s) in bambulab/Demo.3mf are declared outside the gate by the owner (gate/owner/outside.json), not checked, no stamp: Wrist gear, Wrist spacer'
        st = gate_report.status(self.d)
        self.assertEqual([(r['project'], r['objects'], r['changed'], r['missing']) for r in st['declared_outside']], [('main', ['Wrist gear', 'Wrist spacer'], [], [])])
        lines = [l for l in gate_report._status_lines(st) if 'declared outside' in l]
        self.assertEqual(len(lines), 1); self.assertIn(line, lines[0])
        self.assertIn(line, gate_report.digest(self.d, st))
        hook = [l for l in gate_watch.project_lines(self.d) if 'declared outside' in l]
        self.assertEqual(len(hook), 1); self.assertIn(line, hook[0])
        _, out = quiet(gate_guard.cmd_verify, self.d, types.SimpleNamespace(project=None))
        self.assertEqual(len([l for l in out.splitlines() if l.startswith('OUTSIDE  ') and line in l]), 1, out)
        self.assertNotIn('FAIL project.mesh_unstamped main:Wrist', out)            # the judge says it as INFO while the mesh is the declared one
        self.stl('wrist_spacer', d=5.5); self.set_project('observe'); quiet(self.build, PLATES + WRIST, template_first=False)
        st = gate_report.status(self.d)
        self.assertEqual(st['declared_outside'][0]['changed'], ['Wrist spacer'])
        self.assertIn('CHANGED since, refused at the next save in enforce mode: Wrist spacer', '\n'.join(gate_report._status_lines(st)))
        _, out = quiet(gate_guard.cmd_verify, self.d, types.SimpleNamespace(project=None))
        self.assertIn('changed since the owner declared it outside the gate', out)

    def test_the_judge_of_unstamped_meshes(self):
        ctx = {'rule': 'project.mesh_unstamped', 'part': 'main:'}
        o = {'name': 'Wrist gear', 'pose': 'p', 'stamp': {'state': 'none', 'why': 'no stamp'}}
        facts = {'project': 'main', 'file': 'bambulab/Demo.3mf', 'exists': True}
        judge = gate_judges.JUDGES['project_mesh_unstamped']
        same = judge(dict(facts, objects=[dict(o, outside={'same': True, 'date': '2026-03-04', 'reason': REASON})]), {}, ctx)
        moved = judge(dict(facts, objects=[dict(o, outside={'same': False, 'date': '2026-03-04', 'reason': REASON})]), {}, ctx)
        plain = judge(dict(facts, objects=[o]), {}, ctx)
        self.assertEqual([f['verdict'] for f in same + moved + plain], ['INFO', 'FAIL', 'FAIL'])
        self.assertIn('declared outside the gate by the owner', same[0]['text']); self.assertIn('changed since the owner declared it', moved[0]['text'])


class FromAnAgentSession(Outside):
    """From an agent session the declaration needs the owner's answer to the dry run he saw, bound to the file, the
    objects and the reason."""

    def setUp(self):
        super().setUp()
        p = mock.patch.dict(os.environ, {'CLAUDECODE': '1'}); p.start(); self.addCleanup(p.stop)
        self.transcript = os.path.join(self.d, 'session.jsonl'); open(self.transcript, 'w').close()
        self.n = 0
        self.wrist_project()

    def turn(self, text, at):
        self.n += 1
        with open(self.transcript, 'a', encoding='utf-8') as f:
            f.write(json.dumps({'type': 'user', 'origin': {'kind': 'human'}, 'turnOrigin': 'human', 'isSidechain': False, 'uuid': 'u-%d' % self.n,
                                'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(at)), 'message': {'role': 'user', 'content': text}}) + '\n')

    def hook(self, cmd):
        out = gate_watch.pre_tool({'session_id': 's1', 'cwd': self.d, 'transcript_path': self.transcript, 'tool_name': 'Bash', 'tool_input': {'command': cmd}}, {})
        return json.loads(out)['hookSpecificOutput'] if out else None

    def cmd(self, names, *more):
        return 'python3 gate/tools/gate.py --root %s owner outside main %s --reason "%s" %s' % (self.d, ' '.join('"%s"' % n for n in names), REASON, ' '.join(more))

    def test_the_dry_run_binds_the_file_the_objects_and_the_reason(self):
        names, q = ['Wrist gear', 'Wrist spacer'], "--quote 'yes, leave them outside'"
        self.turn('yes, leave them outside', time.time() - 60)
        out = self.hook(self.cmd(names, q))
        self.assertEqual(out['permissionDecision'], 'deny'); self.assertIn('--dry-run', out['permissionDecisionReason'])
        self.assertIsNone(self.hook(self.cmd(names, '--dry-run')))                 # the dry run the owner sees: no quote needed
        out = self.hook(self.cmd(names, q))
        self.assertEqual(out['permissionDecision'], 'deny'); self.assertIn('before the dry run', out['permissionDecisionReason'])
        self.turn('yes, leave them outside', time.time() + 1)                     # his answer to it
        out = self.hook(self.cmd(names[:1], q))                                    # not what he was shown
        self.assertEqual(out['permissionDecision'], 'deny'); self.assertIn('changed since its last dry run', out['permissionDecisionReason'])
        self.assertIsNone(self.hook(self.cmd(names, q)))
        self.set_project('observe'); quiet(bambu3mf.set_settings, self.proj, 'bracket', [('brim_type', 'no_brim')]); self.set_project('enforce')   # saved between the check and the apply
        code, _, err = self.gate('owner', 'outside', 'main', *names, '--reason', REASON, '--quote', 'yes, leave them outside')
        self.assertEqual(code, 4); self.assertIn('changed after the hook checked', err); self.assertIsNone(self.outside_file())
        self.assertIsNone(self.hook(self.cmd(names, '--dry-run')))
        self.turn('yes, leave them outside', time.time() + 2)
        self.assertIsNone(self.hook(self.cmd(names, q)))
        code, out, err = self.gate('owner', 'outside', 'main', *names, '--reason', REASON, '--quote', 'yes, leave them outside')
        self.assertEqual((code, err), (0, ''), out)
        self.assertEqual(self.outside_file()['projects']['main']['objects']['Wrist gear']['turn']['uuid'], 'u-3')
        code, _, err = self.gate('owner', 'outside', 'main', *names, '--reason', REASON, '--quote', 'yes, leave them outside')
        self.assertEqual(code, 4); self.assertIn('no ticket', err)                 # used up

    def test_the_owner_file_is_not_written_past_the_command(self):
        out = self.hook('echo "{}" > %s' % gate_common.outside_path(self.d))
        self.assertEqual(out['permissionDecision'], 'deny')


if __name__ == '__main__':
    unittest.main()
