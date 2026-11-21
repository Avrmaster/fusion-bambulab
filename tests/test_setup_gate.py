"""setup_project.py --gate: a scratch project gets the gate tree, the owner's two files are printed and not written,
a second run keeps what is there. Needs neither Fusion nor Bambu Studio: the .3mf is synthetic."""
import json, os, shutil, subprocess, sys, tempfile, unittest, zipfile
for _k in ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID'): os.environ.pop(_k, None)      # gate.py owner from an agent session needs the hook's ticket: a test is no agent session
os.environ.pop('CLAUDE_PROJECT_DIR', None)      # bambu3mf finds a gated root for a file outside it from this: never the session's project
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
import gate_common, gate_vendor

SETUP = os.path.join(ROOT, 'tools', 'setup_project.py')
ASK = ['Edit(/gate/owner/**)', 'Write(/gate/owner/**)', 'Edit(/gate/project.json)', 'Write(/gate/project.json)',
       'Bash(python3 gate/tools/gate.py owner *)', 'Bash(*gate.py owner *)', 'Bash(*gate.py * owner *)']


def read(path):
    with open(path, encoding='utf-8') as f: return f.read()


def load(path):
    with open(path, encoding='utf-8') as f: return json.load(f)


class Tmp(unittest.TestCase):
    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.d)
        self.p = os.path.join(self.d, 'demo')

    def setup(self, *argv, check=True):
        env = dict(os.environ); env.pop('CLAUDE_PROJECT_DIR', None)
        r = subprocess.run([sys.executable, SETUP, self.p] + list(argv), capture_output=True, text=True, env=env)
        if check: self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def path(self, *parts):
        return os.path.join(self.p, *parts)

    def project_3mf(self, name, nozzle='0.6', width='0.62', material='PETG', **more):
        os.makedirs(self.path('bambulab'), exist_ok=True)
        with zipfile.ZipFile(self.path('bambulab', name), 'w') as z:
            z.writestr('Metadata/project_settings.config', json.dumps(dict({'nozzle_diameter': [nozzle], 'line_width': width, 'filament_type': [material, 'PLA']}, **more)))

    def gate(self, *argv):
        return subprocess.run([sys.executable, self.path('gate', 'tools', 'gate.py')] + list(argv), capture_output=True, text=True, cwd=self.p)


class NewProject(Tmp):
    def test_a_scratch_project_gets_the_tree(self):
        r = self.setup('--name', 'Demo Bracket', '--fusion-doc', 'Demo Assembly', '--gate')
        for parts in (('gate', 'tools', 'gate.py'), ('gate', 'tools', 'gate_judges.py'), ('gate', 'tools', 'probes', '_geom.py'), ('gate', 'tools', 'probes', 'stl_walls.py'),
                      ('gate', 'rules', 'registry', 'inserts.json'), ('gate', 'rules', 'tables', 'bearings.json'), ('gate', 'LOCK.json'), ('gate', 'project.json'),
                      ('gate', 'layout.json'), ('gate', 'owner', 'params.json'), ('gate', 'RULES.md'), ('bambulab', 'tools', 'meshid.py'), ('bambulab', 'tools', 'ledger.py'),
                      ('bambulab', 'tools', 'ledger_mesh.py'), ('bambulab', 'tools', 'ledger_cache.py'), ('bambulab', 'tools', 'bambu3mf.py'), ('bambulab', 'ledger', 'events.jsonl'),
                      ('DESIGN_RULES.md',), ('bambulab', 'build_3mf.py'), ('fusion', 'tools', 'fusion_parts.py')):
            self.assertTrue(os.path.isfile(self.path(*parts)), '/'.join(parts))
        project = load(self.path('gate', 'project.json'))
        self.assertEqual((project['mode'], project['targets']), ('observe', {}))          # no .3mf to read a nozzle from: nothing is invented
        self.assertIn('NO TARGET', r.stdout)
        layout = load(self.path('gate', 'layout.json'))
        self.assertEqual(layout['documents'], [{'key': 'demo_assembly', 'name': 'Demo Assembly', 'stl_dir': 'stl', 'motion_groups': []}])
        self.assertEqual(layout['projects'], [{'key': 'demobracket', 'file': 'bambulab/DemoBracket.3mf', 'stl_dir': 'stl', 'build_table': 'bambulab/build_3mf.py'}])
        self.assertEqual(gate_common.load_layout(self.p)['fusion'], {'endpoint': 'http://127.0.0.1:27182/mcp', 'budget_s': 20})
        self.assertEqual(gate_common.load_project(self.p)['mode'], 'observe')
        ignore = read(self.path('.gitignore')).splitlines()
        for line in ('gate/run/', 'gate/reports/guard.log', 'bambulab/ledger/local.json', 'bambulab/ledger/local/', '.shots/'): self.assertEqual(ignore.count(line), 1, line)
        self.assertIn('bambulab/ledger/events.jsonl merge=union', read(self.path('.gitattributes')).splitlines())

    def test_the_lock_is_intact_and_carries_the_plugin_version(self):
        self.setup('--name', 'Demo', '--gate')
        lock = load(self.path('gate', 'LOCK.json'))
        self.assertEqual(lock['version'], gate_vendor.plugin_version(ROOT)); self.assertEqual(lock['plugin'], 'fusion-bambulab')
        self.assertEqual(lock['files'], dict(gate_vendor.tree_hashes(os.path.join(ROOT, 'tools', 'gate'), 'tools/'), **gate_vendor.tree_hashes(os.path.join(ROOT, 'rules'), 'rules/')))
        self.assertEqual(sorted(lock['siblings']), ['bambulab/tools/ledger.py', 'bambulab/tools/ledger_cache.py', 'bambulab/tools/ledger_mesh.py', 'bambulab/tools/meshid.py'])
        st = gate_vendor.lock_state(self.p, ROOT)
        self.assertEqual((st['state'], st['changed'], st['missing'], st['extra']), ('intact', [], [], []))

    def test_the_owners_files_are_printed_and_not_written(self):
        r = self.setup('--name', 'Demo', '--gate')
        self.assertFalse(os.path.exists(self.path('.claude')), 'settings belong to the owner')
        self.assertIn('PROPOSED', r.stdout); self.assertIn('not written', r.stdout)
        for rule in ASK: self.assertIn(json.dumps(rule), r.stdout)
        self.assertEqual(load(os.path.join(ROOT, 'templates', 'gate', 'settings.json')), {'permissions': {'ask': ASK}})
        self.assertIn('## Print gate', r.stdout); self.assertIn('gate.py export', r.stdout)
        md = read(self.path('CLAUDE.md'))
        self.assertEqual(md.count('fusion-bambulab:start'), 1); self.assertTrue(md.rstrip().endswith('<!-- fusion-bambulab:end -->'))
        self.assertNotIn('fusion-bambulab:gate', md); self.assertNotIn('Print gate', md)
        self.assertIn('gate.py doctor', r.stdout)

    def test_the_vendored_gate_runs_from_the_project(self):
        self.setup('--name', 'Demo', '--gate')
        r = self.gate('params', 'check')
        self.assertEqual((r.returncode, r.stderr), (0, ''), r.stdout); self.assertIn('TOTAL 0', r.stdout)
        r = self.gate('rules', '--category', 'inserts')
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn('inserts.wall', r.stdout); self.assertIn('gate/owner/params.json', r.stdout)
        r = subprocess.run([sys.executable, self.path('gate', 'tools', 'gate_render.py'), '--project', '--check'], capture_output=True, text=True, cwd=self.p)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.assertIn('current', r.stdout)
        rules = read(self.path('gate', 'RULES.md'))
        self.assertTrue(rules.startswith('# Rules of this project')); self.assertIn('`inserts.wall`', rules)

    def test_the_target_is_read_from_the_project_file(self):
        self.project_3mf('Demo.3mf')
        r = self.setup('--name', 'Demo', '--gate')
        self.assertEqual(load(self.path('gate', 'project.json'))['targets'], {'demo': {'nozzle': 0.6, 'line_width': 0.62, 'material': 'PETG'}})
        self.assertIn('TARGET    demo: nozzle 0.6, line width 0.62, PETG', r.stdout)
        self.assertIn('0.62 mm [declared target]', read(self.path('gate', 'RULES.md')))
        self.assertEqual(self.gate('params', 'check').returncode, 0)

    def test_the_fit_settings_the_file_holds_are_declared_and_the_rest_is_named(self):
        self.project_3mf('Demo.3mf', layer_height='0.3', xy_hole_compensation='0.125', wall_generator='classic')
        r = self.setup('--name', 'Demo', '--gate')
        self.assertEqual(load(self.path('gate', 'project.json'))['targets'],
                         {'demo': {'nozzle': 0.6, 'line_width': 0.62, 'material': 'PETG', 'layer_height': 0.3, 'xy_hole_compensation': 0.125, 'wall_generator': 'classic'}})
        self.assertIn('fit settings as the file holds them today: layer_height 0.3, xy_hole_compensation 0.125, wall_generator classic. The owner confirms each one', r.stdout)
        self.assertIn('not in the file, to declare by hand in gate/project.json "targets": initial_layer_print_height, xy_contour_compensation, elefant_foot_compensation', r.stdout)
        self.assertEqual(self.gate('params', 'check').returncode, 0)                 # every key of the declared target is a parameter of the registry

    def test_a_second_run_keeps_what_is_there(self):
        self.setup('--name', 'Demo', '--gate')
        project = self.path('gate', 'project.json'); params = self.path('gate', 'owner', 'params.json'); layout = self.path('gate', 'layout.json')
        gate_common.write_json(project, {'mode': 'enforce', 'targets': {'demo': {'nozzle': 0.6, 'line_width': 0.62, 'material': 'PETG'}}})
        gate_common.write_json(params, {'inserts.wall_target': {'value': 4.5, 'tag': 'U', 'source': 'owner', 'date': '2026-03-04'}})
        gate_common.write_json(layout, dict(load(layout), generators=[{'part': 'demo/shim', 'stl': 'stl/shim.stl', 'source': 'tools/make_shims.py'}]))
        before = (read(project), read(params), read(layout), read(self.path('.gitignore')), read(self.path('.gitattributes')), read(self.path('gate', 'LOCK.json')))
        r = self.setup('--name', 'Demo', '--gate')
        self.assertEqual(before, (read(project), read(params), read(layout), read(self.path('.gitignore')), read(self.path('.gitattributes')), read(self.path('gate', 'LOCK.json'))))
        self.assertIn('kept      vendored gate', r.stdout)
        self.assertIn('4.5 mm [U owner 2026-03-04]', read(self.path('gate', 'RULES.md')))     # RULES.md follows the owner's numbers
        self.assertEqual(read(self.path('CLAUDE.md')).count('fusion-bambulab:start'), 1)

    def test_proposals_stop_once_the_owner_added_them(self):
        self.setup('--name', 'Demo', '--gate')
        os.makedirs(self.path('.claude'))
        gate_common.write_json(self.path('.claude', 'settings.json'), {'permissions': {'ask': ASK[:2], 'allow': ['Bash(ls *)']}})
        with open(self.path('CLAUDE.md'), 'a', encoding='utf-8') as f: f.write('\n<!-- fusion-bambulab:gate:start -->\nmine\n<!-- fusion-bambulab:gate:end -->\n')
        r = self.setup('--gate')
        self.assertIn(json.dumps(ASK[2]), r.stdout); self.assertNotIn(json.dumps(ASK[0]), r.stdout)       # only the missing rule is proposed
        self.assertIn('the gate lines are there', r.stdout)
        self.assertEqual(load(self.path('.claude', 'settings.json')), {'permissions': {'ask': ASK[:2], 'allow': ['Bash(ls *)']}})
        gate_common.write_json(self.path('.claude', 'settings.json'), {'permissions': {'ask': ASK}})
        self.assertNotIn('PROPOSED', self.setup('--gate').stdout)


class ExistingProject(Tmp):
    def test_without_the_option_nothing_of_the_gate_appears(self):
        r = self.setup('--name', 'Demo')
        self.assertFalse(os.path.exists(self.path('gate'))); self.assertFalse(os.path.exists(self.path('bambulab', 'ledger')))
        self.assertFalse(os.path.exists(self.path('.gitattributes')))
        self.assertNotIn('gate/run/', read(self.path('.gitignore'))); self.assertNotIn('PROPOSED', r.stdout)
        md = read(self.path('CLAUDE.md'))
        self.assertNotIn('fusion-bambulab:gate', md); self.assertTrue(md.endswith('<!-- fusion-bambulab:end -->\n'))
        self.assertTrue(os.path.isfile(self.path('bambulab', 'tools', 'ledger.py')))          # the ledger tool is there for `ledger.py init`
        self.assertEqual(self.setup(check=False).returncode != 0, True)                      # --name is still required

    def test_the_cnc_station_sets_up_alone_and_beside_the_gate(self):
        self.setup('--cnc', 'Demo Router')
        self.assertTrue(os.path.isfile(self.path('cnc', 'station.json'))); self.assertFalse(os.path.exists(self.path('gate')))
        self.assertEqual(read(self.path('.gitignore')).splitlines(), ['cnc/laptop/laptop.json', 'cnc/station/jobs.js'])       # its two lines, none of the gate
        self.setup('--cnc', 'Demo Router', '--gate')
        ignore = read(self.path('.gitignore')).splitlines()
        for line in ('cnc/laptop/laptop.json', 'cnc/station/jobs.js', 'gate/run/', 'bambulab/ledger/local/'): self.assertEqual(ignore.count(line), 1, line)
        self.assertTrue(os.path.isfile(self.path('gate', 'LOCK.json')))

    def test_the_gate_alone_joins_a_project_that_exists(self):
        self.setup('--name', 'Demo Bracket', '--fusion-doc', 'Demo Assembly')
        self.project_3mf('DemoBracket.3mf', nozzle='0.4', width='0.42', material='PLA')
        design = read(self.path('DESIGN_RULES.md')); md = read(self.path('CLAUDE.md'))
        r = self.setup('--gate')
        layout = load(self.path('gate', 'layout.json'))
        self.assertEqual((layout['documents'][0]['name'], layout['projects'][0]['file']), ('Demo Assembly', 'bambulab/DemoBracket.3mf'))   # read from CLAUDE.md
        self.assertEqual(load(self.path('gate', 'project.json'))['targets'], {'demobracket': {'nozzle': 0.4, 'line_width': 0.42, 'material': 'PLA'}})
        self.assertEqual((read(self.path('DESIGN_RULES.md')), read(self.path('CLAUDE.md'))), (design, md))
        self.assertEqual(gate_vendor.lock_state(self.p, ROOT)['state'], 'intact')

    def test_an_empty_folder_gets_a_gate_that_says_what_is_missing(self):
        r = self.setup('--gate')
        layout = load(self.path('gate', 'layout.json'))
        self.assertEqual((layout['documents'], layout['projects']), ([], []))
        self.assertIn('NO DOCUMENT', r.stdout); self.assertIn('NO PROJECT', r.stdout)
        self.assertEqual(load(self.path('gate', 'project.json'))['targets'], {})
        self.assertTrue(os.path.isfile(self.path('gate', 'tools', 'gate.py'))); self.assertFalse(os.path.exists(self.path('DESIGN_RULES.md')))
        self.assertEqual(self.gate('params', 'check').returncode, 0)
        r = self.setup('--gate', '--fusion-doc', 'Demo Assembly', '--proj', 'Demo.3mf')
        self.assertIn('kept', r.stdout)                         # layout.json exists: the agent edits it, setup does not

    def test_old_tools_without_the_guard_are_named(self):
        self.setup('--name', 'Demo')
        with open(self.path('bambulab', 'tools', 'bambu3mf.py'), 'w', encoding='utf-8') as f: f.write('# an old copy\n')
        self.assertIn('OLD TOOLS', self.setup('--gate').stdout)


class UpdateTools(Tmp):
    def test_update_tools_refreshes_the_gate_and_its_lock(self):
        self.setup('--name', 'Demo', '--gate')
        gate_vendor.write_lock(self.p, '0.0.1')
        self.assertEqual(gate_vendor.lock_state(self.p, ROOT)['state'], 'stale')
        gate_common.write_json(self.path('gate', 'owner', 'params.json'), {'_about': 'mine'})
        r = self.setup('--update-tools')
        self.assertIn('vendored', r.stdout)
        st = gate_vendor.lock_state(self.p, ROOT)
        self.assertEqual((st['state'], st['version']), ('intact', gate_vendor.plugin_version(ROOT)))
        self.assertEqual(load(self.path('gate', 'owner', 'params.json')), {'_about': 'mine'})

    def test_a_forked_gate_is_listed_and_left_alone_until_asked(self):
        self.setup('--name', 'Demo', '--gate')
        judges = self.path('gate', 'tools', 'gate_judges.py')
        with open(judges, 'a', encoding='utf-8') as f: f.write('\n# loosened by hand\n')
        for argv in (['--update-tools'], ['--gate']):
            r = self.setup(*argv)
            self.assertIn('FORKED', r.stdout); self.assertIn('tools/gate_judges.py', r.stdout); self.assertIn('--overwrite-fork', r.stdout)
            self.assertIn('# loosened by hand', read(judges))
            self.assertEqual(gate_vendor.lock_state(self.p, ROOT)['state'], 'forked')
        self.setup('--update-tools', '--overwrite-fork')
        self.assertNotIn('# loosened by hand', read(judges))
        self.assertEqual(gate_vendor.lock_state(self.p, ROOT)['state'], 'intact')

    def test_a_release_that_changes_a_sibling_tool_refreshes_and_is_no_fork(self):
        """The lock also covers meshid.py and the ledger tools in bambulab/tools. --update-tools replaces them, so the
        lock state must be read before that copy: the project below holds an older meshid.py exactly as its lock says."""
        self.setup('--name', 'Demo', '--gate')
        meshid, lock_path = self.path('bambulab', 'tools', 'meshid.py'), self.path('gate', 'LOCK.json')
        with open(meshid, 'w', encoding='utf-8') as f: f.write('# meshid.py as an older release shipped it\n')
        lock = load(lock_path); lock['version'] = '0.0.1'; lock['siblings']['bambulab/tools/meshid.py'] = gate_common.sha256_file(meshid)
        gate_common.write_json(lock_path, lock)
        self.assertEqual(gate_vendor.lock_state(self.p, ROOT)['state'], 'stale')
        r = self.setup('--update-tools')
        self.assertNotIn('FORKED', r.stdout); self.assertIn('LOCK.json %s' % gate_vendor.plugin_version(ROOT), r.stdout)
        st = gate_vendor.lock_state(self.p, ROOT)
        self.assertEqual((st['state'], st['version'], st['changed']), ('intact', gate_vendor.plugin_version(ROOT), []))
        self.assertEqual(read(meshid), read(os.path.join(ROOT, 'tools', 'bambu', 'meshid.py')))

    def test_a_sibling_tool_changed_by_hand_is_a_fork_and_is_kept(self):
        self.setup('--name', 'Demo', '--gate')
        for name in ('meshid.py', 'ledger.py'):
            with self.subTest(file=name):
                tool = self.path('bambulab', 'tools', name)
                with open(tool, 'a', encoding='utf-8') as f: f.write('\n# changed by hand\n')
                lock = read(self.path('gate', 'LOCK.json'))
                r = self.setup('--update-tools')
                self.assertIn('FORKED', r.stdout); self.assertIn('bambulab/tools/' + name, r.stdout); self.assertIn('--overwrite-fork', r.stdout)
                self.assertIn('# changed by hand', read(tool)); self.assertEqual(read(self.path('gate', 'LOCK.json')), lock)
                st = gate_vendor.lock_state(self.p, ROOT)
                self.assertEqual((st['state'], st['changed']), ('forked', ['bambulab/tools/' + name]))
                self.setup('--update-tools', '--overwrite-fork')
                self.assertNotIn('# changed by hand', read(tool)); self.assertEqual(gate_vendor.lock_state(self.p, ROOT)['state'], 'intact')

    def test_a_forked_gate_keeps_its_sibling_tools_too(self):
        self.setup('--name', 'Demo', '--gate')
        with open(self.path('gate', 'tools', 'gate_judges.py'), 'a', encoding='utf-8') as f: f.write('\n# loosened by hand\n')
        meshid = self.path('bambulab', 'tools', 'meshid.py'); before = read(meshid)
        with open(self.path('bambulab', 'tools', 'bambu3mf.py'), 'a', encoding='utf-8') as f: f.write('\n# an old copy\n')
        r = self.setup('--update-tools')
        self.assertIn('FORKED', r.stdout); self.assertEqual(read(meshid), before)
        self.assertIn('meshid.py and the ledger tools in bambulab/tools were kept as they are too', r.stdout)       # VERSION beside them names the new release
        self.assertEqual(gate_vendor.lock_state(self.p, ROOT)['changed'], ['tools/gate_judges.py'])         # the copy added no second fork
        self.assertNotIn('# an old copy', read(self.path('bambulab', 'tools', 'bambu3mf.py')))               # tools the lock does not cover are refreshed

    def test_without_a_lock_files_that_are_not_the_plugins_are_listed_and_nothing_is_copied(self):
        self.setup('--name', 'Demo', '--gate')
        os.remove(self.path('gate', 'LOCK.json'))
        own, judges = self.path('gate', 'tools', 'own.py'), self.path('gate', 'tools', 'gate_judges.py')
        with open(own, 'w', encoding='utf-8') as f: f.write('# a tool of this project\n')
        with open(judges, 'a', encoding='utf-8') as f: f.write('\n# changed by hand\n')
        for argv in (['--update-tools'], ['--gate']):
            with self.subTest(argv=argv):
                r = self.setup(*argv)
                self.assertIn('NO LOCK', r.stdout); self.assertIn('tools/gate_judges.py, tools/own.py', r.stdout); self.assertIn('--overwrite-fork', r.stdout)
                self.assertTrue(os.path.isfile(own)); self.assertIn('# changed by hand', read(judges))
                self.assertFalse(os.path.exists(self.path('gate', 'LOCK.json')))
        r = self.setup('--update-tools', '--overwrite-fork')
        self.assertNotIn('NO LOCK', r.stdout)
        self.assertFalse(os.path.exists(own)); self.assertNotIn('# changed by hand', read(judges))
        self.assertEqual(gate_vendor.lock_state(self.p, ROOT)['state'], 'intact')

    def test_a_folder_with_its_own_gate_files_is_not_made_a_gated_project(self):
        """gate/tools holds a file of the project and there is no gate/LOCK.json: the gate tools are not copied. Then
        nothing else may be written either: gate/project.json without gate/tools/gate.py would be a gated project that
        cannot run (the session hook and the .3mf writer both go by that file)."""
        os.makedirs(self.path('gate', 'tools')); os.makedirs(self.path('gate', 'rules'))
        own, house = self.path('gate', 'tools', 'hinge_calc.py'), self.path('gate', 'rules', 'house.json')
        with open(own, 'w', encoding='utf-8') as f: f.write('# a tool of this project\n')
        with open(house, 'w', encoding='utf-8') as f: f.write('{}\n')
        r = self.setup('--gate', '--fusion-doc', 'Demo Assembly')
        self.assertIn('NO LOCK', r.stdout); self.assertIn('rules/house.json, tools/hinge_calc.py', r.stdout); self.assertIn('NOT SET UP', r.stdout)
        self.assertNotIn('created', r.stdout); self.assertNotIn('NEXT', r.stdout); self.assertNotIn('PROPOSED', r.stdout)
        self.assertEqual(sorted(os.listdir(self.p)), ['gate']); self.assertEqual(sorted(os.listdir(self.path('gate'))), ['rules', 'tools'])
        self.assertEqual((read(own), read(house)), ('# a tool of this project\n', '{}\n'))
        self.assertIsNone(gate_common.find_root(self.p))                                  # not gated: no hook digest, no guard
        r = self.setup('--name', 'Demo', '--gate')                                        # the same inside a full setup: the project yes, the gate no
        self.assertIn('NOT SET UP', r.stdout); self.assertTrue(os.path.isfile(self.path('bambulab', 'tools', 'bambu3mf.py')))
        self.assertFalse(os.path.exists(self.path('gate', 'project.json'))); self.assertFalse(os.path.exists(self.path('bambulab', 'ledger')))
        self.assertNotIn('gate/run/', read(self.path('.gitignore')))
        r = self.setup('--gate', '--overwrite-fork')                                      # asked for: the folder becomes the plugin's gate
        self.assertNotIn('NOT SET UP', r.stdout); self.assertFalse(os.path.exists(own))
        self.assertTrue(os.path.isfile(self.path('gate', 'project.json'))); self.assertEqual(gate_vendor.lock_state(self.p, ROOT)['state'], 'intact')

    def test_without_a_lock_a_copy_equal_to_the_plugin_is_vendored_as_before(self):
        self.setup('--name', 'Demo', '--gate')
        os.remove(self.path('gate', 'LOCK.json')); os.remove(self.path('gate', 'tools', 'gate_report.py'))      # a copy that was interrupted
        r = self.setup('--update-tools')
        self.assertNotIn('NO LOCK', r.stdout)
        self.assertEqual(gate_vendor.lock_state(self.p, ROOT)['state'], 'intact')
        self.assertTrue(os.path.isfile(self.path('gate', 'tools', 'gate_report.py')))

    def test_update_tools_leaves_a_project_without_a_gate_as_it_was(self):
        self.setup('--name', 'Demo')
        self.setup('--update-tools')
        self.assertFalse(os.path.exists(self.path('gate')))


if __name__ == '__main__':
    unittest.main()
