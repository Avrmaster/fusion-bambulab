"""The guard inside bambu3mf.save(): observe never breaks a save, enforce refuses a new mesh without a valid stamp,
carried meshes pass, and a builder that saves the empty template first is not locked out. Also what bambu3mf.py itself
gained: the STL notes, the reports of update_meshes(), the one call into the ledger. No Fusion, no Bambu Studio."""
import contextlib, io, json, math, os, re, shutil, stat, sys, tempfile, types, unittest, zipfile
for _k in ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID'): os.environ.pop(_k, None)      # gate.py owner from an agent session needs the hook's ticket: a test is no agent session
os.environ.pop('CLAUDE_PROJECT_DIR', None)      # bambu3mf finds a gated root for a file outside it from this: never the session's project
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
for d in (os.path.join(ROOT, 'tools', 'bambu'), os.path.dirname(os.path.abspath(__file__))):
    if d not in sys.path: sys.path.insert(0, d)
import gate_common, gate_guard, gate_judges, gate_registry, gate_report, gate_stamp
from gate_common import GateError
from gate_guard import GuardRefusal
from probes import project_consistency
import bambu3mf, meshid
import test_stamp as S
from test_stamp import TARGET, FINE, SIG1, SIG2, SIG3, plate, write_stl

PLATES = [('first plate', [('bracket', 'bracket.stl', False)]), ('second plate', [('cover', 'cover.stl', '+y')])]


def template(path):
    k = bambu3mf.FILAMENT_KEYS; ps = {key: ['0'] * stride for key, stride in k.items()}
    ps.update(filament_settings_id=['Demo PETG'], filament_type=['PETG'], filament_colour=['#FFFFFF'], flush_volumes_matrix=['0'], filament_self_index=['1'] * k['filament_self_index'],
              printable_area=['0x0', '180x0', '180x180', '0x180'], different_settings_to_system=['', '', ''], brim_width='5')
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types/>'); z.writestr('_rels/.rels', '<Relationships/>'); z.writestr('Metadata/project_settings.config', json.dumps(ps))
    return path


def no_tmp(out):
    """The output with the temporary folder taken out of the paths it prints: a TMPDIR whose name holds "gate" (a
    scratch folder of a gate task) must not read as a line of the gate."""
    for t in sorted({tempfile.gettempdir(), os.path.realpath(tempfile.gettempdir())}, key=len, reverse=True):
        out = out.replace(t, '<tmp>').replace(t.lower(), '<tmp>')
    return out


def quiet(fn, *a, **kw):
    """Run fn and return (its result, what it printed)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out): r = fn(*a, **kw)
    return r, out.getvalue()


class Guarded(S.Gated):
    def setUp(self):
        super().setUp(); bambu3mf._NOTES.clear()
        self.proj = os.path.join(self.d, 'bambulab', 'Demo.3mf'); os.makedirs(os.path.dirname(self.proj))
        self.tpl = template(os.path.join(self.d, 'template.3mf')); self.stls = os.path.join(self.d, 'stl')
        self.stl('bracket'); self.stl('cover', d=6.0)

    def build(self, plates=PLATES, proj=None, template_first=True):
        """What a project's build script does: the empty template over the project, then every part. Returns the printout."""
        def run():
            if template_first: bambu3mf.new_project(self.tpl, proj or self.proj)
            bambu3mf.build(proj or self.proj, self.stls, plates)
        return quiet(run)[1]

    def ungated(self, plates, proj):
        """A build with no gate anywhere: the scratch file outside the root is not judged as one of its projects."""
        with mock.patch.object(bambu3mf, '_gate_root', return_value=(None, None)): return self.build(plates, proj=proj)

    def adopt(self, plates=PLATES):
        """A project that was built before the gate existed: it lies on disk and no journal knows it."""
        scratch = self.outside(); self.ungated(plates, scratch); shutil.copyfile(scratch, self.proj); os.remove(scratch)

    def outside(self):
        """A .3mf path OUTSIDE the gated project: under the root every .3mf is the guard's business."""
        d = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, d, True)
        return os.path.join(d, 'scratch.3mf')

    def meshes(self, plates=PLATES):
        """The meshes a build of these plates would hand to the guard, without touching the project."""
        scratch = self.outside(); self.ungated(plates, scratch)
        m = bambu3mf.project_meshes(bambu3mf.load(scratch)[1]); os.remove(scratch)
        return m

    def archive(self, proj=None):
        """The archive of a project as it lies on disk: what a save that changes no setting hands to the guard. None when the file is not there."""
        return bambu3mf.load(proj or self.proj)[1] if os.path.exists(proj or self.proj) else None

    def objects(self, proj=None): return sorted(m['object'] for m in meshid.read_project(proj or self.proj))

    def poses(self): return meshid.pose_hashes(meshid.read_project(self.proj))

    def log(self):
        p = gate_common.gate_path(self.d, 'reports', 'guard.log')
        if not os.path.exists(p): return ''
        with open(p) as f: return f.read()

    def journal(self): return gate_common.read_jsonl(gate_common.events_path(self.d))

    def ledger(self):
        """Turn the print ledger on here: the write events the guard reads are recorded from now on."""
        os.makedirs(os.path.join(self.d, 'bambulab', 'ledger'), exist_ok=True)

    def stamp_both(self):
        self.stamp('demo/bracket', SIG1, 'bracket'); self.stamp('demo/cover', SIG2, 'cover')


class NoGate(unittest.TestCase):
    def test_without_gate_project_json_nothing_of_the_gate_runs(self):
        d = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, d)
        tpl = template(os.path.join(d, 't.3mf')); proj = os.path.join(d, 'bambulab', 'p.3mf'); os.makedirs(os.path.dirname(proj))
        write_stl(os.path.join(d, 'stl', 'bracket.stl'), *plate())
        with mock.patch.object(bambu3mf, 'project_meshes', side_effect=AssertionError('must not run')), \
                mock.patch.object(gate_guard, 'check', side_effect=AssertionError('must not run')):
            _, out = quiet(lambda: (bambu3mf.new_project(tpl, proj), bambu3mf.build(proj, os.path.join(d, 'stl'), PLATES[:1])))
            self.assertIsNone(bambu3mf._before_save(proj, {}))
        self.assertNotIn('gate', no_tmp(out).lower()); self.assertNotIn('ledger', no_tmp(out).lower())
        self.assertEqual(sorted(os.listdir(d)), ['bambulab', 'stl', 't.3mf']); self.assertEqual(os.listdir(os.path.dirname(proj)), ['p.3mf'])
        self.assertEqual(gate_guard.check(proj, []), {'gated': False})
        self.assertEqual(bambu3mf.record_writes(proj, []), [])


class Observe(Guarded):
    mode = 'observe'

    def test_unstamped_meshes_are_logged_one_line_each_and_the_save_goes_on(self):
        out = self.build()
        self.assertEqual(self.objects(), ['bracket', 'cover'])
        lines = [l for l in no_tmp(out).splitlines() if 'gate' in l]
        self.assertEqual(len(lines), 2, out)                                # one summary line for the whole build, and the line of what has no stamp
        self.assertRegex(lines[0], r'^gate guard \[observe\] bambulab/Demo.3mf: 2 mesh\(es\), 2 new, 2 without a stamp \(bracket, cover\) - written anyway')
        self.assertEqual(lines[1], gate_guard.unchecked_line('bambulab/Demo.3mf', ['bracket', 'cover']))
        self.assertIn('2 object(s) in bambulab/Demo.3mf have no stamp', lines[1])
        log = self.log().splitlines()
        self.assertEqual(len([l for l in log if ': bracket none: no stamp for the geometry of stl/bracket.stl' in l]), 1)
        self.assertEqual(len([l for l in log if ': cover none: no stamp for the geometry of stl/cover.stl' in l]), 1)
        self.assertEqual(len(log), 4); self.assertRegex(log[0], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ observe bambulab/Demo.3mf: ')

    def test_what_is_written_is_what_an_ungated_build_writes(self):
        self.build(); plain = os.path.join(tempfile.mkdtemp(), 'p.3mf'); self.addCleanup(shutil.rmtree, os.path.dirname(plain))
        quiet(lambda: (bambu3mf.new_project(self.tpl, plain), bambu3mf.build(plain, self.stls, PLATES)))
        a, b = bambu3mf.load(self.proj)[1], bambu3mf.load(plain)[1]
        self.assertEqual(sorted(a), sorted(b)); self.assertEqual(a['Metadata/model_settings.config'], b['Metadata/model_settings.config'])
        self.assertEqual(self.poses(), meshid.pose_hashes(meshid.read_project(plain)))

    def test_a_save_that_brings_nothing_new_says_nothing(self):
        self.ledger(); self.stamp_both(); self.build(); before = self.log()
        hit, out = quiet(bambu3mf.set_settings, self.proj, 'bracket', [('brim_type', 'no_brim')])
        self.assertEqual((hit, out), (['bracket'], '')); self.assertEqual(self.log(), before)
        self.assertNotIn('gate', no_tmp(self.build()))                      # the same stamped STLs again, template first: all carried
        self.assertEqual(self.log(), before)

    def test_what_has_no_stamp_is_said_at_every_save_and_an_observe_write_is_never_carried(self):
        self.ledger(); self.build()                                         # two unstamped parts, written in observe mode
        hit, out = quiet(bambu3mf.set_settings, self.proj, 'bracket', [('brim_type', 'no_brim')])
        self.assertEqual(out.strip(), gate_guard.unchecked_line('bambulab/Demo.3mf', ['bracket', 'cover']))   # passes, and says so
        out = self.build()                                                  # template first: the observe write proves nothing, they are judged again
        self.assertIn('2 new, 2 without a stamp (bracket, cover)', out)
        writes = [e for e in self.journal() if e.get('kind') == 'write' and e.get('tool') == 'build']
        self.assertTrue(writes); self.assertEqual({e.get('mode') for e in writes}, {'observe'}); self.assertTrue(all(e.get('unchecked') for e in writes))

    def test_a_stale_stamp_and_a_valid_one(self):
        self.stamp_both(); self.census('demo/cover', SIG3, prev_sig=SIG2)   # the cover changed in the model after its export
        out = self.build()
        self.assertIn('2 new, 1 with a stale stamp (cover)', out); self.assertNotIn('without a stamp', out)
        self.assertIn('cover stale: the part changed since the stamp', self.log()); self.assertNotIn('bracket', self.log().replace('bambulab', ''))

    def test_never_raises_whatever_is_broken(self):
        stamps = gate_common.gate_path(self.d, 'stamps')
        def stamps_is_a_file():
            with open(stamps, 'w') as f: f.write('not a folder')
        def stamps_hold_rubbish():
            os.makedirs(os.path.join(stamps, 'demo', 'bracket'))
            with open(os.path.join(stamps, 'demo', 'bracket', 'a1a1a1a1a1a1.json'), 'w') as f: f.write('{half')
            with open(os.path.join(stamps, 'demo', 'bracket', 'b2b2b2b2b2b2.json'), 'wb') as f: f.write(b'\xff\xfe\x00')
        def stamps_cannot_be_read():
            self.stamp_both(); os.chmod(stamps, 0)      # tearDown_gate() opens it again
        def layout_is_broken():
            with open(gate_common.gate_path(self.d, 'layout.json'), 'w') as f: f.write('{"projects": [')
        def project_is_broken():
            with open(gate_common.gate_path(self.d, 'project.json'), 'w') as f: f.write('{"mode": "observe", ')
        def journal_is_a_folder(): os.makedirs(gate_common.events_path(self.d))
        def reports_is_a_file():
            with open(gate_common.gate_path(self.d, 'reports'), 'w') as f: f.write('x')
        def census_is_broken():
            self.stamp_both()
            with open(gate_common.census_path(self.d), 'w') as f: f.write('[[[')
        patches = {'reading stamps fails': mock.patch.object(gate_stamp, 'all_stamps', side_effect=OSError('stamp folder unreadable')),
                   'the registry does not load': mock.patch.object(gate_registry, 'load_default', side_effect=GateError('registry broken')),
                   'verify blows up': mock.patch.object(gate_stamp, 'verify', side_effect=RuntimeError('boom')),
                   'the guard itself blows up': mock.patch.object(gate_guard, '_check', side_effect=RuntimeError('boom')),
                   'the meshes cannot be read': mock.patch.object(bambu3mf, 'project_meshes', side_effect=ValueError('bad xml')),
                   'the journal cannot be written': mock.patch.object(gate_common, 'append_jsonl', side_effect=OSError('read-only'))}
        cases = [stamps_is_a_file, stamps_hold_rubbish, stamps_cannot_be_read, layout_is_broken, project_is_broken, journal_is_a_folder,
                 reports_is_a_file, census_is_broken] + sorted(patches)
        for case in cases:
            name = case if isinstance(case, str) else case.__name__
            with self.subTest(broken=name):
                self.tearDown_gate(); self.set_project('observe')
                gate_common.write_json(gate_common.gate_path(self.d, 'layout.json'), {'documents': [{'key': 'demo', 'name': 'Demo Assembly', 'stl_dir': 'stl'}],
                                                                                      'projects': [{'key': 'main', 'file': 'bambulab/Demo.3mf', 'stl_dir': 'stl'}]})
                if os.path.exists(self.proj): os.remove(self.proj)
                self.adopt(PLATES[:1])                                      # one part already there, one new
                if isinstance(case, str):
                    self.stamp_both()
                    with patches[case]: out = self.build()
                else:
                    case(); out = self.build()
                self.assertEqual(self.objects(), ['bracket', 'cover'], out)  # the save went through, whole
        self.tearDown_gate()

    def tearDown_gate(self):
        """Back to an empty gate folder (the stamps folder may be locked or be a file)."""
        g = gate_common.gate_path(self.d)
        for dirpath, dirs, _ in os.walk(g):
            for n in dirs: os.chmod(os.path.join(dirpath, n), stat.S_IRWXU)
        shutil.rmtree(g, ignore_errors=True)
        shutil.rmtree(os.path.join(self.d, 'bambulab', 'ledger'), ignore_errors=True)

    def test_the_result_of_check(self):
        self.stamp('demo/bracket', SIG1, 'bracket')
        r, out = quiet(gate_guard.check, self.proj, self.meshes())
        self.assertEqual({k: r[k] for k in ('gated', 'mode', 'project', 'meshes', 'new', 'unstamped', 'stale')},
                         {'gated': True, 'mode': 'observe', 'project': 'main', 'meshes': 2, 'new': ['bracket', 'cover'], 'unstamped': ['cover'], 'stale': []})
        self.assertEqual(r['stamps'], {'bracket': 'demo/bracket@a1a1a1a1a1a1'})
        self.assertEqual([(x['object'], x['state'], x['part'], x['stl']) for x in r['rows']],
                         [('bracket', 'valid', 'demo/bracket', 'stl/bracket.stl'), ('cover', 'none', None, 'stl/cover.stl')])
        self.assertEqual(out.strip().splitlines(), [r['line'], gate_guard.unchecked_line('bambulab/Demo.3mf', ['cover'])]); self.assertNotIn('error', r)
        self.assertEqual((r['unchecked'], r['mark']), (['cover'], None))                       # observe mode marks no plate unless the owner asks for it
        r, out = quiet(gate_guard.check, os.path.join(self.d, 'bambulab', 'Copy.3mf'), self.meshes())
        self.assertEqual((r['gated'], r['project'], r['new']), (True, None, []))                    # not a registered project: not judged in observe mode,
        self.assertIn('bambulab/Copy.3mf is not a registered project', out); self.assertIn('NOT guarded', out)     # and never written in silence
        self.assertIn('bambulab/Copy.3mf is not a registered project', S.read(gate_common.gate_path(self.d, 'reports', 'guard.log')))
        r, out = quiet(gate_guard.check, self.proj, None)                   # nothing to judge with: an internal error, said and swallowed
        self.assertEqual((r['gated'], r['mode']), (True, 'observe')); self.assertIn('TypeError', r['error']); self.assertIn('written anyway', out)
        self.assertNotIn('error', quiet(gate_guard.check, os.path.join(self.d, 'bambulab', 'Copy.3mf'), None)[0])      # not guarded: nothing to fail

    def test_a_mesh_with_no_known_stl_is_unstamped(self):
        m = self.meshes(PLATES[:1]); m[0].update(stl=None, model=None)
        r, _ = quiet(gate_guard.check, self.proj, m)
        self.assertEqual(r['unstamped'], ['bracket']); self.assertIn('no source STL is known', r['rows'][0]['why'])
        m[0].update(stl=os.path.join(self.stls, 'gone.stl'))
        r, _ = quiet(gate_guard.check, self.proj, m)
        self.assertIn('cannot be read', r['rows'][0]['why'])


class Enforce(Guarded):
    mode = 'enforce'

    def test_a_new_mesh_without_a_stamp_is_refused_and_nothing_is_written(self):
        self.stamp('demo/bracket', SIG1, 'bracket')
        self.build(PLATES[:1])                                              # the stamped part alone: fine
        with open(self.proj, 'rb') as f: before = f.read()
        with self.assertRaises(GuardRefusal) as c: self.build(template_first=False)
        with open(self.proj, 'rb') as f: self.assertEqual(f.read(), before)
        self.assertEqual([n for n in os.listdir(os.path.dirname(self.proj)) if n != 'ledger'], ['Demo.3mf'])           # no temp file left behind
        self.assertEqual([r['object'] for r in c.exception.rows], ['cover']); self.assertIsInstance(c.exception, GateError)
        text = str(c.exception)
        self.assertIn('1 without a stamp (cover)', text); self.assertIn('Nothing was written', text); self.assertIn('gate.py export', text)
        self.assertIn('REFUSED', self.log()); self.assertIn('cover none: no stamp for the geometry of stl/cover.stl', self.log())

    def test_stamped_meshes_go_in(self):
        self.stamp_both()
        out = self.build()
        self.assertEqual([l for l in out.splitlines() if 'gate guard' in l and 'NOT compared with the declared target' not in l], [])   # the target names 3 keys: the rest is said
        self.assertEqual(self.objects(), ['bracket', 'cover']); self.assertNotIn('REFUSED', self.log()); self.assertNotIn('without a stamp', self.log())
        r, out = quiet(gate_guard.check, self.proj, self.meshes([('p', [('bracket', 'bracket.stl', '+x')])]), files=self.archive())      # again, on another face
        self.assertEqual((r['new'], r['unstamped'], r['stale'], [l for l in out.splitlines() if 'NOT compared with the declared target' not in l]), (['bracket'], [], [], []))

    def test_a_stale_stamp_is_refused_with_the_reason(self):
        self.stamp_both(); self.census('demo/cover', SIG3, prev_sig=SIG2)
        with self.assertRaises(GuardRefusal) as c: self.build()
        self.assertEqual([(r['object'], r['state'], r['part']) for r in c.exception.rows], [('cover', 'stale', 'demo/cover')])
        self.assertIn('the part changed since the stamp', str(c.exception))

    def test_a_stamp_for_another_nozzle_is_refused_until_the_part_is_stamped_for_this_one(self):
        self.stamp('demo/bracket', SIG1, 'bracket', target=FINE)
        with self.assertRaises(GuardRefusal) as c: self.build(PLATES[:1])
        self.assertIn('stamped for another target', str(c.exception))
        self.stamp('demo/bracket', SIG1, 'bracket', target=TARGET)          # a second stamp of the same geometry
        self.build(PLATES[:1]); self.assertEqual(self.objects(), ['bracket'])

    def test_meshes_on_disk_without_a_stamp_pass_in_observe_and_are_refused_in_enforce(self):
        self.ledger(); self.set_project('observe'); self.build(); self.set_project('enforce')      # two unstamped parts came in while the gate only watched
        before = self.raw()
        for save in (lambda: bambu3mf.set_settings(self.proj, '', [('brim_type', 'no_brim')]),     # a settings change, a plain re-save, a rebuild in place:
                     lambda: bambu3mf.save(self.proj, *reversed(bambu3mf.load(self.proj))),        # nothing is let through because it was there before
                     lambda: self.build(template_first=False)):
            with self.assertRaises(GuardRefusal) as c: quiet(save)
            self.assertEqual(sorted(r['object'] for r in c.exception.rows), ['bracket', 'cover'])
            self.assertIn('lies in the file on disk and nothing on record backs it', str(c.exception)); self.assertEqual(self.raw(), before)
        project = gate_common.read_json(gate_common.gate_path(self.d, 'project.json'))
        gate_common.write_json(gate_common.gate_path(self.d, 'project.json'), dict(project, mode='observe', mark_unchecked=True))
        hit, out = quiet(bambu3mf.set_settings, self.proj, '', [('brim_type', 'no_brim')])        # observe: they pass, said at every save
        self.assertEqual(sorted(hit), ['bracket', 'cover']); self.assertIn(gate_guard.unchecked_line('bambulab/Demo.3mf', ['bracket', 'cover']), out)
        quiet(self.build, template_first=False)
        names = [n for n in re.findall(r'plater_name" value="([^"]*)"', bambu3mf.load(self.proj)[1]['Metadata/model_settings.config'].decode())]
        self.assertEqual(names, ['[unchecked] first plate', '[unchecked] second plate'])        # marked where the owner prints (mark_unchecked)
        self.assertEqual([p[0] for p in [(o, n) for o, _, n in bambu3mf.objects(bambu3mf.load(self.proj)[1]['Metadata/model_settings.config'].decode(), 'first plate')]], ['2'])   # scripts still find the plate by its name

    def raw(self):
        with open(self.proj, 'rb') as f: return f.read()

    def test_observe_write_then_enforce_save_is_refused(self):
        self.ledger(); self.set_project('observe'); self.build(); self.set_project('enforce')      # the journal holds both, written in observe mode without a stamp
        with self.assertRaises(GuardRefusal) as c: self.build()             # the template first: neither is in the file any more, and the journal proves nothing
        self.assertEqual(sorted(r['object'] for r in c.exception.rows), ['bracket', 'cover'])
        self.stamp_both(); self.build(); self.assertEqual(self.objects(), ['bracket', 'cover'])
        names = re.findall(r'plater_name" value="([^"]*)"', bambu3mf.load(self.proj)[1]['Metadata/model_settings.config'].decode())
        self.assertEqual(names, ['first plate', 'second plate'])            # stamped: no mark

    def test_a_builder_that_saves_the_empty_template_first_is_not_locked_out_with_stamped_parts(self):
        self.adopt()                                                        # on disk, unstamped, in no journal
        self.assertEqual(self.journal(), [])
        quiet(bambu3mf.new_project, self.tpl, self.proj)                    # save 1: the empty template over the project
        self.assertEqual(self.objects(), [])
        found = [e for e in self.journal() if e.get('tool') == 'outside']
        self.assertEqual(sorted(e['object'] for e in found), ['bracket', 'cover'])
        e = found[0]
        self.assertEqual((e['v'], e['by'], e['kind'], e['project'], e['plate'], e['plate_index'], e['model'], e['stl'], e['stamp'], e['mode']),
                         (1, 'tool', 'write', 'bambulab/Demo.3mf', 'first plate', 1, None, None, None, 'enforce'))
        self.assertRegex(e['pose'], r'^[0-9a-f]{40}$'); self.assertRegex(e['t'], r'^\d{4}-\d\d-\d\dT')
        with self.assertRaises(GuardRefusal) as c: quiet(bambu3mf.build, self.proj, self.stls, PLATES)       # what was found on disk is on record, never carried
        self.assertIn('The file on disk holds no mesh now', str(c.exception))
        self.stamp_both()
        quiet(bambu3mf.build, self.proj, self.stls, PLATES)                 # save 2: the stamped parts
        self.assertEqual(self.objects(), ['bracket', 'cover'])
        self.assertEqual(self.build(), self.build())                        # and again and again: their stamped write events carry them
        self.assertEqual(len([e for e in self.journal() if e.get('tool') == 'outside']), 2)        # bound once, not at every save

    def test_the_same_builder_is_still_stopped_for_a_part_that_changed(self):
        self.adopt(); self.stamp_both(); self.build()
        self.stl('cover', d=6.5)                                            # a new export that never saw the gate
        with self.assertRaises(GuardRefusal) as c: self.build()
        self.assertEqual([r['object'] for r in c.exception.rows], ['cover'])
        self.stamp('demo/cover', SIG3, 'cover'); self.build()
        self.assertEqual(self.objects(), ['bracket', 'cover'])

    def test_a_3mf_nobody_registered_is_judged_like_a_registered_one(self):
        other = os.path.join(self.d, 'bambulab', 'Other.3mf')               # one project of four is in gate/layout.json: the others are not open doors
        self.assertIsNone(gate_guard.registered_project(self.d, other)); self.assertEqual(gate_guard.registered_project(self.d, self.proj)['key'], 'main')
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(GuardRefusal) as c: self.build(proj=other)
        self.assertEqual(sorted(r['object'] for r in c.exception.rows), ['bracket', 'cover']); self.assertEqual(self.objects(other), [])
        self.stamp('demo/bracket', SIG1, 'bracket'); self.stamp('demo/cover', SIG2, 'cover')
        r, text = quiet(gate_guard.check, other, self.meshes(), files=self.archive(other))
        self.assertEqual((r['project'], r['unstamped'], r['stale']), (None, [], [])); self.assertEqual(sorted(r['stamps']), ['bracket', 'cover'])
        self.assertIn('bambulab/Other.3mf is not a registered project', text); self.assertIn('judged like a registered project', text)
        self.assertIn('Other.3mf is not a registered project', S.read(gate_common.gate_path(self.d, 'reports', 'guard.log')))
        self.build(proj=other); self.assertEqual(self.objects(other), ['bracket', 'cover'])
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(GuardRefusal, 'were not handed over'): gate_guard.check(other, None, files=self.archive(other))  # nothing to judge with: refused, as for the registered file
        self.assertEqual(gate_guard.check(self.outside(), None), {'gated': False})      # outside the gated folder: not this guard's file
        with self.assertRaisesRegex(GuardRefusal, 'were not handed over'): gate_guard.check(self.proj, None, files=self.archive())
        self.assertEqual(gate_guard.registered_project(self.d, os.path.join(self.d, 'bambulab', '..', 'bambulab', 'Demo.3mf'))['key'], 'main')

    def test_known_poses_are_the_file_on_disk_and_the_journal(self):
        self.assertEqual(gate_guard.known_poses(self.d, 'bambulab/Demo.3mf', self.proj), set())
        self.adopt(); on_disk = set(self.poses().values())
        self.assertEqual(gate_guard.known_poses(self.d, 'bambulab/Demo.3mf', self.proj), on_disk)
        for proof in ({}, {'mode': 'observe'}, {'stamp': 'demo/bracket@ffffffffffff'}, {'mode': 'enforce', 'tool': 'outside'}, {'mode': 'enforce', 'tool': 'carried'},
                      {'mode': 'enforce', 'unchecked': True}):                # events that prove nothing: no stamp that verifies, not an enforce write
            gate_common.append_jsonl(gate_common.events_path(self.d), dict({'v': 1, 'kind': 'write', 'project': 'bambulab/Demo.3mf', 'object': 'x', 'pose': 'd' * 40}, **proof))
        self.assertNotIn('d' * 40, gate_guard.known_poses(self.d, 'bambulab/Demo.3mf', self.proj))
        gate_common.append_jsonl(gate_common.events_path(self.d), {'v': 1, 'kind': 'write', 'project': 'bambulab/Demo.3mf', 'object': 'old', 'pose': 'a' * 40, 'mode': 'enforce'})
        self.stamp('demo/bracket', SIG1, 'bracket'); model = meshid.stl_ids(os.path.join(self.stls, 'bracket.stl'))['geom_hash']
        gate_common.append_jsonl(gate_common.events_path(self.d), {'v': 1, 'kind': 'write', 'project': 'bambulab/Demo.3mf', 'object': 'b', 'pose': 'f' * 40,
                                                                   'model': model, 'stamp': 'demo/bracket@a1a1a1a1a1a1'})
        gate_common.append_jsonl(gate_common.events_path(self.d), {'v': 1, 'kind': 'write', 'project': 'bambulab/Demo.3mf', 'object': 'b', 'pose': '9' * 40,
                                                                   'model': 'e' * 40, 'stamp': 'demo/bracket@a1a1a1a1a1a1'})       # the stamp of another geometry
        gate_common.append_jsonl(gate_common.events_path(self.d), {'v': 1, 'kind': 'write', 'project': 'bambulab/Other.3mf', 'object': 'x', 'pose': 'b' * 40,
                                                                   'model': model, 'stamp': 'demo/bracket@a1a1a1a1a1a1'})
        gate_common.append_jsonl(gate_common.events_path(self.d), {'v': 1, 'kind': 'said', 'object': 'x', 'pose': 'c' * 40})
        os.remove(self.proj)
        self.assertEqual(gate_guard.known_poses(self.d, 'bambulab/Demo.3mf', self.proj), {'f' * 40})       # an enforce write alone proves nothing
        with open(self.proj, 'w') as f: f.write('not a zip')
        self.assertEqual(gate_guard.known_poses(self.d, 'bambulab/Demo.3mf', self.proj), {'f' * 40})

    def test_an_stl_the_journal_already_knows_for_this_project_is_carried_in_any_pose(self):
        model = meshid.stl_ids(os.path.join(self.stls, 'bracket.stl'))['geom_hash']
        with self.assertRaises(GuardRefusal): self.build(PLATES[:1])
        event = {'v': 1, 't': '2026-03-04T10:35:00Z', 'by': 'tool', 'kind': 'write', 'project': 'bambulab/Demo.3mf',
                 'object': 'bracket', 'pose': 'e' * 40, 'model': model, 'stl': 'stl/bracket.stl', 'tool': 'build'}
        gate_common.append_jsonl(gate_common.events_path(self.d), event)                   # written in observe mode, no stamp: proves nothing
        with self.assertRaises(GuardRefusal): self.build([('p', [('bracket', 'bracket.stl', '+x,z26.6')])])
        gate_common.append_jsonl(gate_common.events_path(self.d), dict(event, stamp='demo/bracket@a1a1a1a1a1a1'))     # a stamp that does not exist: nothing
        with self.assertRaises(GuardRefusal): self.build([('p', [('bracket', 'bracket.stl', '+x,z26.6')])])
        gate_common.append_jsonl(gate_common.events_path(self.d), dict(event, mode='enforce'))      # "written in enforce mode" is a line anyone can write: nothing
        with self.assertRaises(GuardRefusal): self.build([('p', [('bracket', 'bracket.stl', '+x,z26.6')])])
        self.stamp('demo/bracket', SIG1, 'bracket')
        gate_common.append_jsonl(gate_common.events_path(self.d), dict(event, stamp='demo/bracket@a1a1a1a1a1a1'))     # a stamp of this geometry that verifies: carried
        self.build([('p', [('bracket', 'bracket.stl', '+x,z26.6')])]); self.assertEqual(self.objects(), ['bracket'])

    def test_a_journal_stamp_carries_only_while_it_verifies(self):
        self.ledger(); self.stamp('demo/bracket', SIG1, 'bracket'); self.build([('p', [('bracket', 'bracket.stl', False)])])
        e = [x for x in self.journal() if x.get('tool') == 'build'][-1]
        self.assertEqual((e['stamp'], e['mode']), ('demo/bracket@a1a1a1a1a1a1', 'enforce'))
        self.build([('p', [('bracket', 'bracket.stl', '+x,z26.6')])])          # another pose: the stamped write carries it, its stamp goes on record
        self.assertEqual([x for x in self.journal() if x.get('tool') == 'build'][-1]['stamp'], 'demo/bracket@a1a1a1a1a1a1')

    def test_a_quarter_turned_copy_of_a_stamped_stl_is_found_in_a_swap(self):
        turned = os.path.join(self.d, 'turned'); face = bambu3mf.ORIENT['+y']
        def swap(**kw):
            V, F = plate(**kw); write_stl(os.path.join(turned, 'bracket.stl'), [face(*v) for v in V], F)
            return quiet(bambu3mf.update_meshes, self.proj, turned)[0]
        self.stamp('demo/bracket', SIG1, 'bracket'); self.build([('p', [('bracket', 'bracket.stl', '+y')])])
        self.stamp('demo/bracket', SIG2, 'bracket', hx=12.0)                # the part changed and was exported through the gate
        self.assertEqual(swap(hx=12.0), ['bracket'])
        self.assertEqual(len(meshid.read_project(self.proj)[0]['triangles']), 32)
        before = self.poses()
        with self.assertRaises(GuardRefusal) as c: swap(hx=13.0)            # a turned copy of something that was never stamped
        self.assertIn('no stamp for the geometry of', str(c.exception)); self.assertEqual(self.poses(), before)

    def test_a_copy_turned_by_a_free_angle_needs_its_model_stl_named(self):
        a = math.radians(26.6); turned = os.path.join(self.d, 'turned')
        def swap(sources, **kw):
            V, F = plate(**kw); write_stl(os.path.join(turned, 'bracket.stl'), [(math.cos(a) * x - math.sin(a) * y, math.sin(a) * x + math.cos(a) * y, z) for x, y, z in V], F)
            return quiet(bambu3mf.update_meshes, self.proj, turned, '', sources)[0]
        self.stamp('demo/bracket', SIG1, 'bracket'); self.build([('p', [('bracket', 'bracket.stl', '-z,z26.6')])])
        self.stamp('demo/bracket', SIG2, 'bracket', hx=12.0)
        with self.assertRaises(GuardRefusal): swap(None, hx=12.0)
        self.assertEqual(swap({'bracket': os.path.join(self.stls, 'bracket.stl')}, hx=12.0), ['bracket'])

    def test_a_turned_copy_with_a_moved_hole_does_not_travel_under_the_stamp_of_the_named_stl(self):
        a = math.radians(26.6); turned = os.path.join(self.d, 'turned')
        def swap(sources, **kw):
            V, F = plate(**kw); write_stl(os.path.join(turned, 'bracket.stl'), [(math.cos(a) * x - math.sin(a) * y, math.sin(a) * x + math.cos(a) * y, z) for x, y, z in V], F)
            return quiet(bambu3mf.update_meshes, self.proj, turned, '', sources)
        self.stamp('demo/bracket', SIG1, 'bracket'); self.build([('p', [('bracket', 'bracket.stl', '-z,z26.6')])])
        self.stamp('demo/bracket', SIG2, 'bracket', hx=12.0)                # stl/bracket.stl: the stamped geometry, hole at x 12
        before = self.poses(); named = {'bracket': os.path.join(self.stls, 'bracket.stl')}
        out = io.StringIO()                                                  # the turned copy has its hole 2 mm further: same triangle count,
        V, F = plate(hx=14.0); write_stl(os.path.join(turned, 'bracket.stl'), [(math.cos(a) * x - math.sin(a) * y, math.sin(a) * x + math.cos(a) * y, z) for x, y, z in V], F)
        with contextlib.redirect_stdout(out), self.assertRaises(GuardRefusal) as c: bambu3mf.update_meshes(self.proj, turned, '', named)      # same volume, same area
        self.assertIn('NOT a turned copy of the model STL', out.getvalue()); self.assertEqual(self.poses(), before)
        self.assertIn('no stamp for the geometry of', str(c.exception))
        journal = [e for e in gate_common.read_jsonl(gate_common.events_path(self.d)) if e.get('kind') == 'write']
        self.assertNotIn(meshid.stl_ids(named['bracket'])['geom_hash'], [e.get('model') for e in journal if e.get('tool') == 'update_meshes'])
        self.assertEqual(swap(named, hx=12.0)[0], ['bracket'])               # the honest copy still goes in

    def test_two_parts_of_one_geometry_each_get_their_own_stamp(self):
        self.stl('post_1', d=3.0); self.stl('post_2', d=3.0)
        self.stamp('demo/post_1', SIG1, 'post_1'); self.stamp('demo/post_2', SIG2, 'post_2')
        r, _ = quiet(gate_guard.check, self.proj, self.meshes([('p', [('post 1', 'post_1.stl', False), ('post 2', 'post_2.stl', '+x')])]))
        self.assertEqual(r['stamps'], {'post 1': 'demo/post_1@a1a1a1a1a1a1', 'post 2': 'demo/post_2@b2b2b2b2b2b2'})

    def test_a_guard_that_cannot_judge_refuses_in_enforce_mode(self):
        self.stamp_both()
        with mock.patch.object(gate_stamp, 'verify', side_effect=RuntimeError('boom')):          # one mesh cannot be judged
            with self.assertRaisesRegex(GuardRefusal, 'could not be judged: RuntimeError: boom'): self.build()
        with mock.patch.object(gate_guard, '_check', side_effect=RuntimeError('boom')):          # the guard as a whole fails
            with self.assertRaisesRegex(GuardRefusal, 'could not be judged: RuntimeError: boom'): self.build()
        with open(gate_common.gate_path(self.d, 'project.json'), 'w') as f: f.write('{"mode": "enforce", <<<<<<< a merge went wrong')
        with self.assertRaisesRegex(GuardRefusal, 'not valid JSON'): self.build()
        self.assertEqual(self.objects(), [])                                # no part ever got in

    def test_a_weld_or_a_repair_of_a_mesh_that_is_in_the_file_is_carried(self):
        import weld_3mf; weld_3mf.GUARD = False
        self.ledger(); self.stamp('demo/bracket', SIG1, 'bracket'); self.build(PLATES[:1])     # a stamped write: its event holds the vertex set too
        self.assertRegex([e for e in self.journal() if e.get('tool') == 'build'][-1]['vset'], r'^[0-9a-f]{40}$')
        infos, files = bambu3mf.load(self.proj); path = [n for n in files if n.startswith('3D/Objects/')][0]; om = files[path].decode()
        V = re.findall(r'<vertex [^>]+/>', om); t = re.search(r'<triangle v1="(\d+)" ', om)            # one vertex split in two: an open seam
        om = om[:t.start()] + '<triangle v1="%d" ' % len(V) + om[t.end():]
        files[path] = om.replace('     </vertices>', '      ' + V[int(t.group(1))] + '\n     </vertices>', 1).encode()
        bambu3mf.save(self.proj, files, infos)                               # the same vertex set and count: still the mesh that was there
        self.assertEqual(quiet(weld_3mf.run, self.proj)[0], 0); self.assertEqual(quiet(weld_3mf.run, self.proj, True)[0], 0)
        bambu3mf._NOTES.clear()                                              # another process: nothing ties the mesh to its STL but the journal
        m = bambu3mf.project_meshes(bambu3mf.load(self.proj)[1])
        m[0]['triangles'] = m[0]['triangles'][:-2]                           # a repair that dropped two triangles and no vertex
        self.assertEqual(quiet(gate_guard.check, self.proj, m, files=self.archive())[0]['new'], [])
        m[0]['object'] = 'another name'                                      # but only for the object that held those vertices
        with self.assertRaisesRegex(GuardRefusal, '1 without a stamp'): gate_guard.check(self.proj, m, files=self.archive())

    def test_the_vendored_copy_in_a_project_finds_its_guard(self):
        import subprocess
        tools, gtools = os.path.join(self.d, 'bambulab', 'tools'), gate_common.gate_path(self.d, 'tools')
        shutil.copytree(os.path.join(ROOT, 'tools', 'gate'), gtools, ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(os.path.join(ROOT, 'rules'), gate_common.gate_path(self.d, 'rules'))
        os.makedirs(tools)
        for folder in ('bambu', 'ledger'):
            for n in os.listdir(os.path.join(ROOT, 'tools', folder)):
                if n.endswith(('.py', '.json')): shutil.copy(os.path.join(ROOT, 'tools', folder, n), tools)
        script = ('import sys, bambu3mf\n'
                  'try:\n'
                  '    bambu3mf.new_project(%r, %r); bambu3mf.build(%r, %r, %r)\n'
                  'except Exception as e:\n'
                  '    print("RAISED", type(e).__name__, type(e).__module__, sys.modules["gate_guard"].__file__); sys.exit(7)\n'
                  % (self.tpl, self.proj, self.proj, self.stls, PLATES[:1]))
        r = subprocess.run([sys.executable, '-c', script], cwd=tools, capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=tools))
        self.assertEqual(r.returncode, 7, r.stdout + r.stderr)
        self.assertIn('RAISED GuardRefusal gate_guard %s' % os.path.join(gtools, 'gate_guard.py'), r.stdout)
        self.assertEqual(self.objects(), [])
        self.set_project('observe')
        r = subprocess.run([sys.executable, '-c', script], cwd=tools, capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=tools))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.assertIn('gate guard [observe]', r.stdout); self.assertEqual(self.objects(), ['bracket'])

    def test_an_unprintable_or_off_plate_object_is_judged_like_any_other(self):
        m = self.meshes(); m[1].update(printable=False, plate=None, plate_index=None)
        with self.assertRaises(GuardRefusal) as c: gate_guard.check(self.proj, m)
        self.assertEqual(sorted(r['object'] for r in c.exception.rows), ['bracket', 'cover'])


class OutsideTheTree(Guarded):
    """A .3mf written outside the gated tree (a scratch folder, /tmp, a download folder) from a gated project's parts is
    judged as an unregistered project of that project: the gate root is found from the source STLs of its meshes, the
    working folder or CLAUDE_PROJECT_DIR."""

    def scratch_build(self, plates=PLATES):
        proj = self.outside()
        return proj, self.build(plates, proj=proj)

    def test_the_root_is_found_from_the_stls_the_working_folder_and_the_environment(self):
        far = self.outside()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('CLAUDE_PROJECT_DIR', None)
            bambu3mf._NOTES.clear(); here = os.getcwd()
            try:
                os.chdir(os.path.dirname(far))
                self.assertEqual(bambu3mf._gate_root(far), (None, None))
                self.assertEqual(bambu3mf._gate_root(self.proj), (self.d, 'tree'))
                bambu3mf.note_stl('bracket', os.path.join(self.stls, 'bracket.stl'))
                self.assertEqual(bambu3mf._gate_root(far), (self.d, 'stl'))
                bambu3mf._NOTES.clear()
                os.environ['CLAUDE_PROJECT_DIR'] = self.d
                self.assertEqual(bambu3mf._gate_root(far), (self.d, 'env'))
                os.chdir(os.path.join(self.d, 'stl'))
                self.assertEqual(bambu3mf._gate_root(far), (self.d, 'cwd'))
            finally:
                os.chdir(here); bambu3mf._NOTES.clear()

    def test_a_scratch_copy_of_a_project_stl_still_finds_its_project(self):
        far = self.outside(); scratch = os.path.dirname(far)
        shutil.copyfile(os.path.join(self.stls, 'bracket.stl'), os.path.join(scratch, 'b.stl'))
        index = os.path.join(scratch, 'roots.json'); gate_common.write_json(index, {'roots': [self.d]})
        with mock.patch.dict(os.environ, {'FUSION_BAMBULAB_GATE_INDEX': index}, clear=False):
            os.environ.pop('CLAUDE_PROJECT_DIR', None)
            bambu3mf._NOTES.clear(); here = os.getcwd()
            try:
                os.chdir(scratch)
                bambu3mf.note_stl('bracket', os.path.join(scratch, 'b.stl'))
                self.assertEqual(bambu3mf._gate_root(far), (self.d, 'content'))      # the same bytes as stl/bracket.stl
                self.set_project('enforce')
                quiet(bambu3mf.new_project, self.tpl, far)
                with self.assertRaises(GuardRefusal): quiet(bambu3mf.build, far, scratch, [('p', [('bracket', 'b.stl', False)])])
                gate_common.write_json(index, {'roots': []})
                bambu3mf._NOTES.clear(); bambu3mf.note_stl('bracket', os.path.join(scratch, 'b.stl'))
                self.assertEqual(bambu3mf._gate_root(far), (None, None))
            finally:
                os.chdir(here); bambu3mf._NOTES.clear()

    def test_observe_says_it_loudly_and_writes(self):
        proj, out = self.scratch_build()
        self.assertEqual(self.objects(proj), ['bracket', 'cover'])
        self.assertIn('%s lies outside the gated project %s (the source STL of one of its meshes lies in it)' % (proj, self.d), out)
        self.assertIn('NOT guarded, written as it is (observe mode', out)
        self.assertFalse(os.path.exists(gate_guard.saves_path(self.d)))      # nothing of a file outside the tree is kept
        self.assertEqual(self.journal(), [])

    def test_enforce_refuses_what_has_no_stamp_and_carries_nothing(self):
        self.set_project('enforce')
        proj = self.outside()
        with self.assertRaises(GuardRefusal) as c: self.build(proj=proj)
        self.assertEqual(sorted(r['object'] for r in c.exception.rows), ['bracket', 'cover'])
        self.assertFalse(os.path.exists(proj) and self.objects(proj))
        self.stamp_both()
        out = self.build(proj=proj)
        self.assertEqual(self.objects(proj), ['bracket', 'cover']); self.assertIn('judged like a registered project, no declared target, nothing of it carried', out)
        self.stl('cover', d=6.5); other = self.outside(); self.ungated(PLATES, other)     # a copy made with no gate, its cover unstamped
        infos, files = bambu3mf.load(other)
        with self.assertRaises(GuardRefusal) as c: quiet(bambu3mf.save, other, files, infos)   # saved again as it is: what lies there carries nothing
        self.assertEqual([r['object'] for r in c.exception.rows], ['cover'])


class ZipWriters(unittest.TestCase):
    def test_scripts_that_write_a_3mf_past_bambu3mf_are_listed(self):
        d = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, d)
        files = {'tools/build.py': "import zipfile\nwith zipfile.ZipFile('out/p.3mf', 'w', zipfile.ZIP_DEFLATED) as z: pass\n",
                 'tools/kw.py': "import zipfile\nzipfile.ZipFile(path + '.3mf', mode='w')\n",
                 'tools/reads.py': "import zipfile\nzipfile.ZipFile('p.3mf')\n",
                 'tools/zips.py': "import zipfile\nzipfile.ZipFile('backup.zip', 'w')\n",
                 'bambulab/tools/bambu3mf.py': "zipfile.ZipFile(tmp, 'w', compress)  # .3mf\n",
                 'gate/tools/x.py': "zipfile.ZipFile('a.3mf', 'w')\n", '.hidden/y.py': "zipfile.ZipFile('a.3mf', 'w')\n"}
        for rel, text in files.items():
            os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
            with open(os.path.join(d, rel), 'w', encoding='utf-8') as f: f.write(text)
        self.assertEqual(gate_common.zip_writers(d), {'files': ['tools/build.py', 'tools/kw.py'], 'complete': True})
        self.assertFalse(gate_common.zip_writers(d, limit=1)['complete'])


class Library(Guarded):
    """What bambu3mf.py itself gained."""
    def test_update_meshes_says_which_stl_has_no_object_and_which_object_is_on_no_plate(self):
        self.build()
        infos, files = bambu3mf.load(self.proj); cfg = files['Metadata/model_settings.config'].decode()
        oid = [o for o, _, n in bambu3mf.objects(cfg) if n == 'cover'][0]                        # the cover's plate was deleted in the slicer
        cfg = re.sub(r'  <plate>(?:(?!</plate>).)*?object_id" value="%s".*?</plate>\n' % oid, '', cfg, flags=re.S)
        files['Metadata/model_settings.config'] = cfg.encode(); bambu3mf.save(self.proj, files, infos)
        self.stl('cover', d=6.5); self.stl('bracket', hx=12.0); self.stl('spare')
        changed, out = quiet(bambu3mf.update_meshes, self.proj, self.stls)
        self.assertEqual(changed, ['bracket'])
        self.assertIn('no object in the project for 1 STL(s): spare.stl', out)
        self.assertIn('on no plate: cover (its STL was NOT swapped in', out)
        self.assertEqual(bambu3mf.LAST_UPDATE, {'changed': ['bracket'], 'no_object': ['spare.stl'], 'no_plate': ['cover']})
        os.remove(os.path.join(self.stls, 'spare.stl')); os.remove(os.path.join(self.stls, 'cover.stl'))
        changed, out = quiet(bambu3mf.update_meshes, self.proj, self.stls)
        self.assertEqual((changed, out.strip()), ([], 'on no plate: cover'))                       # still said: the object is in no print

    def test_update_meshes_is_quiet_when_everything_matches(self):
        self.build([('first plate', [('bracket', 'bracket.stl', False)]), ('second plate', [('cover', 'cover.stl', False)])]); self.stl('bracket', hx=12.0)
        changed, out = quiet(bambu3mf.update_meshes, self.proj, self.stls)
        self.assertEqual(changed, ['bracket']); self.assertNotIn('no object', out); self.assertNotIn('on no plate', out)
        self.assertEqual(bambu3mf.LAST_UPDATE, {'changed': ['bracket'], 'no_object': [], 'no_plate': []})

    def test_the_library_notes_which_stl_each_mesh_was_made_from(self):
        self.build(); files = bambu3mf.load(self.proj)[1]
        by = {m['object']: m for m in bambu3mf.project_meshes(files)}
        b = os.path.join(self.stls, 'bracket.stl'); ids = meshid.stl_ids(b)
        self.assertEqual((by['bracket']['stl'], by['bracket']['stl_sha256'], by['bracket']['model'], by['bracket']['orient']), (b, ids['sha256'], ids['geom_hash'], False))
        self.assertEqual((by['cover']['stl'], by['cover']['orient'], by['cover']['placed_from']), (os.path.join(self.stls, 'cover.stl'), '+y', None))
        self.assertEqual(sorted(by['cover']) , sorted(['object', 'id', 'plate', 'plate_index', 'printable', 'vertices', 'triangles', 'parts', 'stl', 'stl_sha256', 'model', 'orient', 'placed_from']))
        self.stl('bracket', d=5.0)                                          # the file changed after the mesh was placed: not that mesh
        self.assertIsNone({m['object']: m for m in bambu3mf.project_meshes(files)}['bracket']['stl'])
        bambu3mf._NOTES.clear()                                             # another process: the meshes are only carried
        self.assertEqual([m['stl'] for m in bambu3mf.project_meshes(files)], [None, None])
        bambu3mf.note_stl('cover', os.path.join(self.stls, 'missing.stl')); bambu3mf.note_stl(None, None)       # never raises
        self.assertEqual([m['stl'] for m in bambu3mf.project_meshes(files)], [None, None])

    def test_every_save_tells_the_ledger_once_with_the_name_of_the_writer(self):
        calls = []
        fake = types.ModuleType('ledger'); fake.record_writes = lambda proj, meshes, stamps=None, tool=None: calls.append((os.path.basename(proj), [(m['object'], m['stl'] and os.path.basename(m['stl'])) for m in meshes], stamps, tool)) or ['event']
        self.stamp('demo/bracket', SIG1, 'bracket')
        with mock.patch.dict(sys.modules, {'ledger': fake}):
            self.build(); self.assertEqual(calls, [])                       # no bambulab/ledger/ folder: the ledger is off
            os.makedirs(os.path.join(self.d, 'bambulab', 'ledger'))
            self.build(); quiet(bambu3mf.set_settings, self.proj, 'bracket', [('brim_type', 'no_brim')])
            self.assertEqual([c[3] for c in calls], ['new_project', 'build', 'set_settings'])
            self.assertEqual(calls[0][:3], ('Demo.3mf', [], {}))
            self.assertEqual(calls[1][:3], ('Demo.3mf', [('bracket', 'bracket.stl'), ('cover', 'cover.stl')], {'bracket': 'demo/bracket@a1a1a1a1a1a1'}))   # the fake ledger kept no event: judged again
            fake.record_writes = mock.Mock(side_effect=RuntimeError('disk full'))
            self.assertIn('ledger: the write was not recorded (RuntimeError: disk full)', self.build())
            self.assertEqual(self.objects(), ['bracket', 'cover'])
            fake.record_writes = mock.Mock(side_effect=NotImplementedError)
            self.assertNotIn('ledger', self.build())

    def test_the_stamps_of_new_meshes_reach_the_ledger(self):
        calls = []
        fake = types.ModuleType('ledger'); fake.record_writes = lambda proj, meshes, stamps=None, tool=None: calls.append(stamps)
        self.stamp_both(); self.set_project('enforce'); os.makedirs(os.path.join(self.d, 'bambulab', 'ledger'))
        with mock.patch.dict(sys.modules, {'ledger': fake}): self.build()
        self.assertEqual(calls[-1], {'bracket': 'demo/bracket@a1a1a1a1a1a1', 'cover': 'demo/cover@b2b2b2b2b2b2'})

    def test_a_ledger_without_a_gate_still_gets_the_writes(self):
        os.remove(gate_common.gate_path(self.d, 'project.json')); os.makedirs(os.path.join(self.d, 'bambulab', 'ledger'))
        fake = types.ModuleType('ledger'); fake.record_writes = mock.Mock(return_value=[])
        with mock.patch.dict(sys.modules, {'ledger': fake}), mock.patch.object(gate_guard, 'check', side_effect=AssertionError('no gate here')):
            self.build()
        self.assertEqual(fake.record_writes.call_count, 2); self.assertEqual(fake.record_writes.call_args[1]['tool'], 'build')


def fail_for(names):
    def judge(facts, params, ctx):
        return [gate_judges.finding(ctx['rule'], '%s%s' % (ctx['part'], o['name']), 'FAIL', 'object %s has no stamp' % o['name']) for o in facts['objects'] if o['name'] in names]
    return judge


class Verify(Guarded):
    """gate.py verify, with the project probe, the project judges and the report played by fakes."""
    def setUp(self):
        super().setUp()
        self.facts = {'project': 'main', 'file': 'bambulab/Demo.3mf', 'exists': True, 'objects': [{'name': 'bracket'}, {'name': 'cover'}], 'stls': [], 'build_table': None}
        self.measured = []
        clean = {r['check']['judge']: (lambda facts, params, ctx: []) for r in gate_registry.rules(self.reg, level='block', stage='project')}
        for p in (mock.patch.dict(gate_judges.JUDGES, clean),
                  mock.patch.object(project_consistency, 'measure', lambda root, entry, layout, census, declared_target=None, reg=None, fit_settings=None: self.measured.append((entry['key'], declared_target)) or self.facts),
                  mock.patch.object(gate_report, 'build', lambda root, reg, resolved, run, command, stage, verdicts, **kw: {'run': run, 'command': command, 'stage': stage, 'verdicts': verdicts}),
                  mock.patch.object(gate_report, 'write', lambda root, report: self.reports.append(report)),
                  mock.patch.object(gate_report, 'summary_line', lambda report: 'gate verify %s: %d project(s)' % (report['run'], len(report['verdicts'])))):
            p.start(); self.addCleanup(p.stop)
        self.reports = []

    def run_verify(self, project=None): return quiet(gate_guard.cmd_verify, self.d, types.SimpleNamespace(project=project))

    def test_a_clean_project_is_exit_0_with_a_report(self):
        code, out = self.run_verify()
        self.assertEqual(code, 0); self.assertIn('1 project(s)', out); self.assertEqual(self.measured, [('main', TARGET)])
        rep = self.reports[0]; self.assertEqual((rep['command'], rep['stage']), ('verify', 'project'))
        self.assertEqual((rep['verdicts'][0]['part'], rep['verdicts'][0]['verdict'], rep['verdicts'][0]['open']), ('main:', 'pass', []))

    def test_an_open_finding_is_exit_1_and_is_printed(self):
        with mock.patch.dict(gate_judges.JUDGES, {'project_mesh_unstamped': fail_for(['cover'])}):
            code, out = self.run_verify('bambulab/Demo.3mf')
        self.assertEqual(code, 1); self.assertIn('FAIL project.mesh_unstamped main:cover: object cover has no stamp', out)
        self.assertEqual(self.reports[0]['verdicts'][0]['verdict'], 'fail')

    def test_a_judge_that_is_not_built_is_never_a_pass(self):
        def stub(facts, params, ctx): raise NotImplementedError
        with mock.patch.dict(gate_judges.JUDGES, {'project_target_differs': stub}):
            code, out = self.run_verify()
        self.assertEqual(code, 1); self.assertIn('NOT_RUN project.target_differs', out)

    def test_a_waiver_closes_a_project_finding(self):
        f = fail_for(['cover'])({'objects': [{'name': 'cover'}]}, {}, {'rule': 'project.mesh_unstamped', 'part': 'main:'})[0]
        gate_common.write_json(gate_common.gate_path(self.d, 'owner', 'waivers.json'), {'waivers': [
            {'id': 'W-1', 'rule': 'project.mesh_unstamped', 'part': 'main:cover', 'anchor': f['anchor'], 'context': f['context'],
             'reason': 'the cover is a bought sample kept in the project for scale only', 'quote': 'keep the sample cover in there', 'date': '2026-03-04'}]})
        with mock.patch.dict(gate_judges.JUDGES, {'project_mesh_unstamped': fail_for(['cover', 'bracket'])}):
            r = gate_guard.verify_project(self.d, self.reg, self.resolved, {'key': 'main', 'file': 'bambulab/Demo.3mf'})
        self.assertEqual(([x['part'] for x in r['open']], [x['part'] for x in r['waived']], r['project']), (['main:bracket'], ['main:cover'], 'main'))
        self.assertEqual(r['facts'], self.facts)

    def test_a_file_that_is_not_registered_is_an_error_and_no_projects_is_not_a_pass(self):
        with self.assertRaisesRegex(GateError, 'not a registered project'): self.run_verify('bambulab/Other.3mf')
        with mock.patch.object(project_consistency, 'measure', side_effect=GateError('bambulab/Demo.3mf cannot be read')):
            with self.assertRaises(GateError): self.run_verify()
        gate_common.write_json(gate_common.gate_path(self.d, 'layout.json'), {'projects': []})
        with open(os.path.join(self.d, 'bambulab', 'Other.3mf'), 'wb') as f: f.write(b'PK')
        code, out = self.run_verify()
        self.assertEqual(code, 4); self.assertIn('no .3mf project is registered', out); self.assertIn('NOTHING was verified', out)
        self.assertIn('NOT REGISTERED  bambulab/Other.3mf', out); self.assertIn('NOT REGISTERED  stl/', out)

    def test_without_the_report_module_the_result_is_still_said(self):
        with mock.patch.object(gate_report, 'build', side_effect=NotImplementedError), mock.patch.dict(gate_judges.JUDGES, {'project_mesh_stale': fail_for(['bracket'])}):
            code, out = self.run_verify()
        self.assertEqual(code, 1); self.assertIn('gate verify: 1 project(s), 1 open', out)


if __name__ == '__main__':
    unittest.main()
