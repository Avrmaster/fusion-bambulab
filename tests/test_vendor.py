"""The vendored copy of the gate and its lock: intact, forked, stale, absent; what doctor says about each.
No Fusion (the ping is a function the test hands in), no Bambu Studio."""
import contextlib, io, json, os, shutil, sys, tempfile, unittest
for _k in ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID'): os.environ.pop(_k, None)      # gate.py owner from an agent session needs the hook's ticket: a test is no agent session
os.environ.pop('CLAUDE_PROJECT_DIR', None)      # bambu3mf finds a gated root for a file outside it from this: never the session's project
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
import gate_common, gate_render, gate_registry, gate_vendor
from gate_common import GateError


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f: f.write(text)


def read(path):
    with open(path, encoding='utf-8') as f: return f.read()


class Tmp(unittest.TestCase):
    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.d)
        self.plugin, self.root = os.path.join(self.d, 'plugin'), os.path.join(self.d, 'project')
        self.fake_plugin('0.2.0')
        os.makedirs(self.root)

    def fake_plugin(self, version):
        """A small plugin tree: enough files to copy, hash and lock."""
        write(os.path.join(self.plugin, '.claude-plugin', 'plugin.json'), json.dumps({'name': 'fusion-bambulab', 'version': version}))
        write(os.path.join(self.plugin, 'tools', 'gate', 'gate.py'), '# the command\n')
        write(os.path.join(self.plugin, 'tools', 'gate', 'gate_judges.py'), '# judges\n')
        write(os.path.join(self.plugin, 'tools', 'gate', 'probes', '_geom.py'), '# geometry\n')
        write(os.path.join(self.plugin, 'tools', 'gate', '__pycache__', 'gate.cpython-39.pyc'), 'cache')
        write(os.path.join(self.plugin, 'tools', 'gate', 'notes.txt'), 'not a tool')
        write(os.path.join(self.plugin, 'rules', 'registry', 'inserts.json'), '{"category": "inserts"}\n')
        write(os.path.join(self.plugin, 'rules', 'tables', 'inserts.json'), '{"table": "inserts"}\n')

    def state(self, plugin=True):
        return gate_vendor.lock_state(self.root, self.plugin if plugin else None)


class Vendor(Tmp):
    def test_vendor_copies_tools_and_rules_and_writes_the_lock(self):
        lock = gate_vendor.vendor(self.plugin, self.root)
        self.assertEqual(sorted(lock['files']), ['rules/registry/inserts.json', 'rules/tables/inserts.json', 'tools/gate.py', 'tools/gate_judges.py', 'tools/probes/_geom.py'])
        self.assertEqual((lock['plugin'], lock['version']), ('fusion-bambulab', '0.2.0'))
        self.assertEqual(lock, gate_common.read_json(gate_common.gate_path(self.root, 'LOCK.json')))
        self.assertEqual(read(gate_common.gate_path(self.root, 'tools', 'probes', '_geom.py')), '# geometry\n')
        self.assertEqual(lock['files']['tools/gate.py'], gate_common.sha256_file(os.path.join(self.plugin, 'tools', 'gate', 'gate.py')))
        self.assertFalse(os.path.exists(gate_common.gate_path(self.root, 'tools', '__pycache__')))
        self.assertFalse(os.path.exists(gate_common.gate_path(self.root, 'tools', 'notes.txt')))
        for hex_ in lock['files'].values(): self.assertEqual(len(hex_), 64)

    def test_a_second_vendor_removes_what_left_the_plugin_and_keeps_project_data(self):
        gate_vendor.vendor(self.plugin, self.root)
        owner = gate_common.gate_path(self.root, 'owner', 'params.json'); write(owner, '{"_about": "mine"}')
        stamp = gate_common.gate_path(self.root, 'stamps', 'demo', 'bracket', 'abc.json'); write(stamp, '{}')
        project = gate_common.gate_path(self.root, 'project.json'); write(project, '{"mode": "enforce"}')
        os.remove(os.path.join(self.plugin, 'tools', 'gate', 'gate_judges.py'))
        write(os.path.join(self.plugin, 'tools', 'gate', 'gate.py'), '# the command, changed\n')
        write(os.path.join(self.plugin, 'rules', 'registry', 'walls.json'), '{"category": "walls"}\n')
        lock = gate_vendor.vendor(self.plugin, self.root)
        self.assertNotIn('tools/gate_judges.py', lock['files']); self.assertIn('rules/registry/walls.json', lock['files'])
        self.assertFalse(os.path.exists(gate_common.gate_path(self.root, 'tools', 'gate_judges.py')))
        self.assertEqual(read(gate_common.gate_path(self.root, 'tools', 'gate.py')), '# the command, changed\n')
        self.assertEqual((read(owner), read(stamp), read(project)), ('{"_about": "mine"}', '{}', '{"mode": "enforce"}'))
        self.assertEqual(self.state()['state'], 'intact')

    def test_a_folder_that_is_not_the_plugin_is_refused(self):
        with self.assertRaises(GateError): gate_vendor.vendor(self.root, self.root)
        os.makedirs(os.path.join(self.d, 'half', '.claude-plugin'))
        write(os.path.join(self.d, 'half', '.claude-plugin', 'plugin.json'), '{"version": "1.0.0"}')
        with self.assertRaises(GateError): gate_vendor.vendor(os.path.join(self.d, 'half'), self.root)

    def test_helpers(self):
        self.assertEqual(gate_vendor.plugin_version(self.plugin), '0.2.0')
        self.assertIsNone(gate_vendor.plugin_version(self.root))
        self.assertEqual(gate_vendor.plugin_version(ROOT), gate_common.read_json(os.path.join(ROOT, '.claude-plugin', 'plugin.json'))['version'])
        self.assertEqual(gate_vendor.own_plugin_root(), ROOT)
        hashes = gate_vendor.tree_hashes(os.path.join(self.plugin, 'tools', 'gate'), 'tools/')
        self.assertEqual(sorted(hashes), ['tools/gate.py', 'tools/gate_judges.py', 'tools/probes/_geom.py'])
        self.assertEqual(gate_vendor.tree_hashes(os.path.join(self.d, 'nowhere')), {})


class States(Tmp):
    def test_absent(self):
        st = self.state()
        self.assertEqual((st['state'], st['version'], st['plugin_version']), ('absent', None, '0.2.0'))

    def test_intact(self):
        gate_vendor.vendor(self.plugin, self.root)
        st = self.state()
        self.assertEqual((st['state'], st['version'], st['plugin_version'], st['changed'], st['missing'], st['extra']), ('intact', '0.2.0', '0.2.0', [], [], []))
        os.makedirs(gate_common.gate_path(self.root, 'tools', '__pycache__'))
        write(gate_common.gate_path(self.root, 'tools', '__pycache__', 'gate.cpython-39.pyc'), 'x')    # running the tools leaves these
        write(gate_common.gate_path(self.root, 'RULES.md'), 'generated')
        write(gate_common.gate_path(self.root, 'census.json'), '{}')                                     # project data is not the copy
        self.assertEqual(self.state()['state'], 'intact')
        self.assertEqual(gate_vendor.state_line(self.state()), '')

    def test_forked_by_a_changed_a_missing_and_an_extra_file(self):
        gate_vendor.vendor(self.plugin, self.root)
        write(gate_common.gate_path(self.root, 'tools', 'gate_judges.py'), '# judges, loosened by hand\n')
        st = self.state()
        self.assertEqual((st['state'], st['changed'], st['missing'], st['extra']), ('forked', ['tools/gate_judges.py'], [], []))
        os.remove(gate_common.gate_path(self.root, 'rules', 'tables', 'inserts.json'))
        write(gate_common.gate_path(self.root, 'tools', 'probes', 'mine.py'), '# a probe of my own\n')
        write(gate_common.gate_path(self.root, 'rules', 'registry', 'extra.json'), '{}')
        st = self.state()
        self.assertEqual((st['state'], st['changed'], st['missing'], st['extra']),
                         ('forked', ['tools/gate_judges.py'], ['rules/tables/inserts.json'], ['rules/registry/extra.json', 'tools/probes/mine.py']))
        line = gate_vendor.state_line(st)
        self.assertIn('FORKED', line); self.assertIn('1 changed, 1 missing, 2 extra', line); self.assertIn('tools/gate_judges.py', line)
        self.assertEqual(self.state(plugin=False)['state'], 'forked')
        gate_vendor.vendor(self.plugin, self.root)                                                       # vendoring again is the way back
        self.assertEqual(self.state()['state'], 'intact')
        self.assertFalse(os.path.exists(gate_common.gate_path(self.root, 'tools', 'probes', 'mine.py')))

    def test_forked_wins_over_stale(self):
        gate_vendor.vendor(self.plugin, self.root)
        self.fake_plugin('0.3.0')
        write(gate_common.gate_path(self.root, 'tools', 'gate.py'), '# changed here\n')
        self.assertEqual(self.state()['state'], 'forked')

    def test_stale_is_an_intact_copy_of_an_older_plugin(self):
        gate_vendor.vendor(self.plugin, self.root)
        self.fake_plugin('0.2.1')
        st = self.state()
        self.assertEqual((st['state'], st['version'], st['plugin_version']), ('stale', '0.2.0', '0.2.1'))
        line = gate_vendor.state_line(st)
        self.assertIn('0.2.0', line); self.assertIn('0.2.1', line); self.assertIn('--update-tools', line)
        self.assertEqual(self.state(plugin=False)['state'], 'intact')            # with no plugin to compare there is no "stale"
        for newer, older in (('0.10.0', '0.9.9'), ('1.0.0', '0.99.0')):               # compared as numbers, not as text
            self.fake_plugin(newer); gate_vendor.write_lock(self.root, older)
            self.assertEqual(self.state()['state'], 'stale', (newer, older))
        self.fake_plugin('0.1.0'); gate_vendor.write_lock(self.root, '0.2.0')
        self.assertEqual(self.state()['state'], 'intact')                       # a copy newer than the plugin is not stale

    def test_the_sibling_tools_are_locked_too(self):
        tools = os.path.join(self.root, 'bambulab', 'tools')
        for n in ('meshid.py', 'ledger.py', 'bambu3mf.py'): write(os.path.join(tools, n), '# %s\n' % n)
        lock = gate_vendor.vendor(self.plugin, self.root)
        self.assertEqual(sorted(lock['siblings']), ['bambulab/tools/ledger.py', 'bambulab/tools/meshid.py'])     # bambu3mf.py is not the gate's
        self.assertEqual(self.state()['state'], 'intact')
        write(os.path.join(tools, 'bambu3mf.py'), '# a local fix\n')
        self.assertEqual(self.state()['state'], 'intact')
        write(os.path.join(tools, 'meshid.py'), '# another hash\n')
        st = self.state()
        self.assertEqual((st['state'], st['changed']), ('forked', ['bambulab/tools/meshid.py']))
        os.remove(os.path.join(tools, 'meshid.py'))
        self.assertEqual(self.state()['missing'], ['bambulab/tools/meshid.py'])

    def test_a_lock_that_is_not_a_lock_is_an_error(self):
        write(gate_common.gate_path(self.root, 'LOCK.json'), '{"version": "0.2.0"}')
        with self.assertRaises(GateError): self.state()
        write(gate_common.gate_path(self.root, 'LOCK.json'), 'not json')
        with self.assertRaises(GateError): self.state()


class Doctor(unittest.TestCase):
    """doctor on a copy of the real gate. The ping is handed in: nothing here calls Fusion."""
    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.d)
        gate_vendor.vendor(ROOT, self.d)
        gate_common.write_json(gate_common.gate_path(self.d, 'project.json'), {'mode': 'observe', 'targets': {'main': {
            'nozzle': 0.6, 'line_width': 0.62, 'material': 'PETG', 'layer_height': 0.2, 'initial_layer_print_height': 0.2, 'xy_hole_compensation': 0.1, 'xy_contour_compensation': 0.0,
            'elefant_foot_compensation': 0.15, 'wall_generator': 'classic'}}})
        gate_common.write_json(gate_common.gate_path(self.d, 'layout.json'),
                               {'documents': [{'key': 'demo', 'name': 'Demo Assembly', 'stl_dir': 'stl', 'motion_groups': [{'key': 'slide', 'patterns': ['^Slide']}]}],
                                'projects': [{'key': 'main', 'file': 'bambulab/Demo.3mf', 'stl_dir': 'stl'}]})
        gate_common.write_json(os.path.join(self.d, '.claude', 'settings.json'), {'permissions': {'ask': ['Edit(/gate/owner/**)', 'Bash(*gate.py owner *)']}})
        os.makedirs(os.path.join(self.d, 'stl')); os.makedirs(os.path.join(self.d, 'bambulab'))
        with open(os.path.join(self.d, 'bambulab', 'Demo.3mf'), 'w') as f: f.write('')
        gate_render.write_project_rules(self.d, gate_registry.load(), None)
        self.pings = []

    def doctor(self, root='self', up=True):
        def ping(url, timeout_s):
            self.pings.append((url, timeout_s)); return up
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = gate_vendor.cmd_doctor(self.d if root == 'self' else root, None, pinger=ping)
        return code, out.getvalue()

    def test_a_sound_project_is_exit_0(self):
        code, out = self.doctor()
        self.assertEqual(code, 0, out)
        self.assertIn('vendored gate is intact', out); self.assertIn('mode observe', out); self.assertIn('demo = "Demo Assembly": never scanned', out)
        self.assertIn('gate/RULES.md is current', out); self.assertIn('doctor: 0 error(s), 0 warning(s)', out)
        self.assertEqual(self.pings, [('http://127.0.0.1:27182/mcp', 5)])            # one ping, five seconds
        self.assertIn('the ask rules for gate/owner', out)

    def test_a_target_that_does_not_say_the_fit_settings_is_a_warning(self):
        gate_common.write_json(gate_common.gate_path(self.d, 'project.json'), {'mode': 'observe', 'targets': {'main': {'nozzle': 0.6, 'line_width': 0.62, 'material': 'PETG', 'layer_height': 0.2}}})
        code, out = self.doctor()
        self.assertEqual(code, 0, out)
        self.assertIn('warn  project.json target main does not say initial_layer_print_height, xy_hole_compensation, xy_contour_compensation, elefant_foot_compensation, wall_generator', out)
        self.assertIn('every fit counts as never printed for this project', out)

    def test_what_the_layout_does_not_cover_is_said(self):
        os.remove(os.path.join(self.d, '.claude', 'settings.json'))
        layout = gate_common.load_layout(self.d); layout['documents'][0]['motion_groups'] = []
        gate_common.write_json(gate_common.gate_path(self.d, 'layout.json'), layout)
        os.makedirs(os.path.join(self.d, 'other', 'stl_fine')); os.makedirs(os.path.join(self.d, 'bambulab', '.backups'))
        for sub in (('bambulab', 'ledger', 'printed'), ('bambulab', 'ledger', 'local', 'pool')): os.makedirs(os.path.join(self.d, *sub))
        for name in (('bambulab', 'Fine.3mf'), ('bambulab', '.backups', 'Demo.3mf'), ('other', 'stl_fine', 'gear.stl'),
                     ('bambulab', 'ledger', 'printed', 'gear__0a1b2c3d.stl'), ('bambulab', 'ledger', 'local', 'pool', '0a1b2c3d.stl')):
            with open(os.path.join(self.d, *name), 'w') as f: f.write('')
        code, out = self.doctor()
        self.assertEqual(code, 0, out)                                               # warnings, not errors
        self.assertIn('bambulab/Fine.3mf is a .3mf of this project that gate/layout.json does not name', out)
        self.assertNotIn('.backups', out)                                            # hidden folders are not walked
        self.assertNotIn('bambulab/ledger', out)                                     # the ledger's own copies are its records, not an export folder
        self.assertIn('other/stl_fine/ holds STL files and is no "stl_dir"', out)
        self.assertIn('declares no "motion_groups"', out); self.assertIn('.claude/settings.json lacks', out)
        self.assertEqual(gate_common.unregistered(self.d, gate_common.load_layout(self.d)),
                         {'projects': ['bambulab/Fine.3mf'], 'stl_dirs': ['other/stl_fine'], 'complete': True})

    def test_fusion_not_running_is_reported_and_is_not_an_error(self):
        code, out = self.doctor(up=False)
        self.assertEqual(code, 0, out); self.assertIn('warn  fusion', out); self.assertIn('no answer', out)

    def test_an_endpoint_that_is_not_this_machine_is_said_and_never_pinged(self):
        layout = gate_common.read_json(gate_common.gate_path(self.d, 'layout.json'))
        for url in ('http://example.com:27182/mcp', 'https://127.0.0.1:27182/mcp'):
            gate_common.write_json(gate_common.gate_path(self.d, 'layout.json'), dict(layout, fusion={'endpoint': url}))
            code, out = self.doctor()
            self.assertEqual(code, 0, out); self.assertIn('warn  fusion', out); self.assertIn('"fusion"."endpoint" must be http://127.0.0.1:<port>', out); self.assertIn(url, out)
            self.assertEqual(self.pings, [])

    def scanned(self, parts, stl_names=None, **doc):
        """A census as a scan left it for these parts ({name: stl}), and the layout as it is now."""
        layout = gate_common.read_json(gate_common.gate_path(self.d, 'layout.json'))
        layout['documents'][0].update(doc)
        if stl_names is not None: layout['documents'][0]['stl_names'] = stl_names
        gate_common.write_json(gate_common.gate_path(self.d, 'layout.json'), layout)
        gate_common.write_json(gate_common.census_path(self.d), {
            'docs': {'demo': {'name': 'Demo Assembly', 'open': True, 'scanned': '2026-03-04T09:12:07Z'}},
            'parts': {gate_common.part_id('demo', name): {'doc': 'demo', 'name': name, 'source': 'fusion', 'sig': 'ab' * 32, 'stl': stl, 'gone': False} for name, stl in parts.items()}})

    def test_stl_names_are_held_against_the_last_scan(self):
        self.scanned({'Motor plate': 'stl/MotorPlate_rev3.stl', 'Cover': 'stl/lid.stl', 'Bracket': 'stl/bracket.stl'}, {'Motor plate': 'MotorPlate_rev3.stl', 'cover': 'lid.stl'})
        code, out = self.doctor()
        self.assertEqual(code, 0, out); self.assertNotIn('stl_names', out); self.assertNotIn('scan again', out)       # the census is what the layout says
        self.scanned({'Motor plate': 'stl/motor_plate.stl', 'Cover': 'stl/lid.stl', 'Bracket': 'stl/bracket.stl'}, {'Motor plate': 'MotorPlate_rev3.stl', 'cover': 'lid.stl', 'Lever': 'lever_old.stl'})
        code, out = self.doctor()
        self.assertEqual(code, 0, out)                                               # warnings
        self.assertIn('warn  documents    "stl_names" of document demo: \'Lever\' names no part of the last scan', out); self.assertIn('the file lever_old.stl is the STL of no part', out)
        self.assertIn('warn  documents    demo/motor_plate: the census names stl/motor_plate.stl as its STL, gate/layout.json gives stl/MotorPlate_rev3.stl now: scan again', out)
        self.assertEqual(out.count('scan again'), 1); self.assertEqual(out.count('names no part'), 1)
        lines = gate_vendor.stl_name_lines(gate_common.load_layout(self.d), gate_common.load_census(self.d))
        self.assertEqual(len(lines), 2); self.assertTrue(lines[0].startswith('"stl_names" of document demo: \'Lever\' names no part'), lines[0])

    def test_a_slug_key_beside_a_name_key_and_a_file_in_another_letter_case_are_named(self):
        self.scanned({'Cover': 'stl/lid.stl', 'Bracket': 'stl/Bracket_v2.stl'}, {'Cover': 'lid.stl', 'cover': 'old_lid.stl', 'bracket': 'Bracket_v2.stl'})
        os.makedirs(os.path.join(self.d, 'stl'), exist_ok=True)
        for name in ('lid.stl', 'bracket_V2.stl'):
            with open(os.path.join(self.d, 'stl', name), 'wb') as f: f.write(b'solid')
        code, out = self.doctor()
        self.assertEqual(code, 0, out)
        self.assertIn("warn  documents    \"stl_names\" of document demo: the key 'cover' is never read, the part has an entry under its name 'Cover'", out)
        self.assertIn('old_lid.stl is the STL of no part', out); self.assertNotIn('names no part', out)
        self.assertIn('warn  documents    demo/bracket: its STL is stl/Bracket_v2.stl, the folder holds bracket_V2.stl: the same name in another letter case', out)
        self.assertEqual(out.count('another letter case'), 1); self.assertNotIn('scan again', out)       # lid.stl is spelled as the folder has it
        os.rename(os.path.join(self.d, 'stl', 'bracket_V2.stl'), os.path.join(self.d, 'stl', 'Bracket_v2.stl'))
        self.assertNotIn('another letter case', self.doctor()[1])

    def test_a_map_the_scan_refuses_is_named_as_such(self):
        # the scan left both parts with their default files: the map gives the bracket the file of the cover
        self.scanned({'Cover': 'stl/cover.stl', 'Bracket': 'stl/bracket.stl'}, {'Bracket': 'cover.stl'})
        code, out = self.doctor()
        self.assertEqual(code, 0, out)
        self.assertIn('warn  documents    demo/bracket: "stl_names" gives it the file cover.stl, which is the STL of the part demo/cover: the scan refuses that (NAME CLASH) '
                      'and the part keeps stl/bracket.stl. Change the map in gate/layout.json, then scan', out)
        self.assertNotIn('scan again', out); self.assertNotIn('names no part', out)

    def test_a_changed_stl_folder_and_parts_that_are_gone_are_read_the_same_way(self):
        self.scanned({'Cover': 'stl/cover.stl'}, stl_dir='parts')                     # the folder was renamed after the scan
        os.makedirs(os.path.join(self.d, 'parts'))
        self.assertIn('demo/cover: the census names stl/cover.stl as its STL, gate/layout.json gives parts/cover.stl now: scan again', self.doctor()[1])
        self.scanned({'Cover': 'stl/cover.stl'}, {'Cover': 'lid.stl'})
        census = gate_common.load_census(self.d); census['parts']['demo/cover']['gone'] = True
        gate_common.write_json(gate_common.census_path(self.d), census)
        out = self.doctor()[1]
        self.assertIn("'Cover' names no part of the last scan", out); self.assertNotIn('scan again', out)       # a part that is gone has no file to compare
        census['docs'] = {}; gate_common.write_json(gate_common.census_path(self.d), census)
        self.assertNotIn('names no part', self.doctor()[1])                           # never scanned: nothing to hold the map against
        gate_common.write_json(gate_common.gate_path(self.d, 'layout.json'), {'documents': [{'key': 'demo', 'name': 'Demo Assembly', 'stl_dir': 'stl', 'stl_names': {'Cover': 'old/lid.stl'}}]})
        code, out = self.doctor()
        self.assertEqual(code, 4); self.assertIn('error layout.json', out); self.assertIn('"stl_names" of document \'demo\'', out)       # a bad map is an error, not a warning

    def test_a_forked_copy_blocks(self):
        with open(gate_common.gate_path(self.d, 'tools', 'gate_judges.py'), 'a', encoding='utf-8') as f: f.write('\n# loosened\n')
        code, out = self.doctor()
        self.assertEqual(code, 4); self.assertIn('FORKED', out); self.assertIn('changed: tools/gate_judges.py', out)

    def test_a_stale_copy_warns_and_does_not_block(self):
        gate_vendor.write_lock(self.d, '0.0.1')
        code, out = self.doctor()
        self.assertEqual(code, 0, out); self.assertIn('warn  lock', out); self.assertIn('0.0.1', out)

    def test_broken_project_files_are_errors(self):
        gate_common.write_json(gate_common.gate_path(self.d, 'owner', 'params.json'), {'inserts.wall_target': {'value': 1.0, 'tag': 'D', 'source': 'agent', 'date': '2026-03-04'}})
        code, out = self.doctor()
        self.assertEqual(code, 4); self.assertIn('error parameters', out); self.assertIn('looser', out)
        os.remove(gate_common.gate_path(self.d, 'owner', 'params.json'))
        gate_common.write_json(gate_common.gate_path(self.d, 'project.json'), {'mode': 'strict'})
        code, out = self.doctor()
        self.assertEqual(code, 4); self.assertIn('error project.json', out)

    def test_what_is_missing_is_a_warning(self):
        os.remove(os.path.join(self.d, 'bambulab', 'Demo.3mf')); os.remove(gate_common.gate_path(self.d, 'RULES.md'))
        gate_common.write_json(gate_common.gate_path(self.d, 'project.json'), {'mode': 'observe'})
        code, out = self.doctor()
        self.assertEqual(code, 0, out)
        self.assertIn('bambulab/Demo.3mf is registered and does not exist yet', out); self.assertIn('gate/RULES.md is missing', out); self.assertIn('no target declared', out)

    def test_the_last_scan_says_which_documents_were_open(self):
        gate_common.write_json(gate_common.census_path(self.d), {'docs': {'demo': {'name': 'Demo Assembly', 'open': False, 'scanned': '2026-03-04T09:12:07Z'}}, 'parts': {}})
        code, out = self.doctor()
        self.assertIn('not open when the gate last looked for it (last read 2026-03-04T09:12:07Z)', out)

    def test_a_folder_that_is_not_gated_checks_only_the_interpreter(self):
        code, out = self.doctor(root=None)
        self.assertEqual(code, 0); self.assertIn('interpreter', out); self.assertIn('not gated', out); self.assertEqual(self.pings, [])


if __name__ == '__main__':
    unittest.main()
