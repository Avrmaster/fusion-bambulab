"""The skeleton holds together: shared plumbing (gate_common), every module imports on this interpreter, gate.py hands
each sub-command to the function INTERFACES names, the hook never fails. No Fusion, no Bambu Studio."""
import contextlib, importlib, io, json, os, shutil, subprocess, sys, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(ROOT, 'tools', 'gate')
for d in (GATE, os.path.join(ROOT, 'tools', 'bambu'), os.path.join(ROOT, 'tools', 'ledger')):
    if d not in sys.path: sys.path.insert(0, d)
import gate, gate_common, gate_hook
from gate_common import GateError

GATE_MODULES = ('gate', 'gate_common', 'gate_registry', 'gate_params', 'gate_features', 'gate_judges', 'gate_plan', 'gate_fusion_rpc',
                'gate_attest', 'gate_report', 'gate_stamp', 'gate_guard', 'gate_hook', 'gate_render', 'gate_vendor',
                'probes._geom', 'probes._geom_rings', 'probes.stl_walls', 'probes.stl_mesh', 'probes.project_consistency',
                'meshid', 'ledger', 'ledger_mesh', 'ledger_cache')
FUSION_FILES = ('_fusion', 'fusion_scan', 'fusion_census', 'fusion_inserts', 'fusion_bearings', 'fusion_openings', 'fusion_screws',
                'fusion_gap', 'fusion_thin', 'fusion_export')


class Project(unittest.TestCase):
    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.d)
        gate_common.write_json(gate_common.gate_path(self.d, 'project.json'),
                               {'mode': 'observe', 'not_printed': ['^ref '], 'targets': {'main': {'nozzle': 0.6, 'line_width': 0.62, 'material': 'PETG'}}})

    def gate(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try: code = gate.main(['--root', self.d] + list(argv))
            except SystemExit as e: code = e.code
        return code, out.getvalue(), err.getvalue()


class Imports(unittest.TestCase):
    def test_every_module_imports(self):
        for name in GATE_MODULES:
            with self.subTest(module=name):
                mod = importlib.import_module(name)
                self.assertTrue((mod.__doc__ or '').strip(), 'a gate module says at its top what it is')
                self.assertTrue(os.path.abspath(mod.__file__).startswith(os.path.join(ROOT, 'tools') + os.sep))       # the plugin's copy, not one found elsewhere on the path

    def test_fusion_side_files_compile_and_define_their_entry_point(self):
        for name in FUSION_FILES:
            with self.subTest(file=name):
                with open(os.path.join(GATE, 'probes', name + '.py'), encoding='utf-8') as f: text = f.read()
                code = compile(text, name + '.py', 'exec')
                self.assertIn('run' if name == '_fusion' else 'probe', code.co_names + tuple(c.co_name for c in code.co_consts if hasattr(c, 'co_name')))
                if name != '_fusion': self.assertNotIn('import adsk', text)

    def test_pasted_helpers_import_nothing_from_disk_but_the_standard_library(self):
        import ast
        for name in ('_geom', '_geom_rings'):
            with open(os.path.join(GATE, 'probes', name + '.py'), encoding='utf-8') as f: tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertTrue({a.name for a in node.names} <= {'math', 'hashlib', 'json'}, (name, [a.name for a in node.names]))
                elif isinstance(node, ast.ImportFrom):
                    self.assertIn(node.module, ('_geom', 'probes._geom'), name)       # the guarded host-side import of _geom_rings
            top = [n for n in tree.body if not isinstance(n, (ast.FunctionDef, ast.Import, ast.ImportFrom, ast.Assign, ast.Try, ast.Expr))]
            self.assertEqual(top, [], name + ': only definitions may run when the file is pasted')

    def test_no_cyrillic_and_no_home_path_in_what_this_step_ships(self):
        files = []
        for base in ('tools/gate', 'tools/ledger', 'rules', 'tests'):
            for dirpath, _, names in os.walk(os.path.join(ROOT, base)):
                files += [os.path.join(dirpath, n) for n in names if n.endswith(('.py', '.json'))]
        files.append(os.path.join(ROOT, 'tools', 'bambu', 'meshid.py'))
        for path in files:
            with open(path, encoding='utf-8') as f: text = f.read()
            self.assertFalse(any('\u0400' <= c <= '\u04ff' for c in text), 'Cyrillic in ' + path)
            self.assertFalse('/' + 'Users/' in text or '/' + 'home/' in text, 'a home path in ' + path)


class Common(Project):
    def test_find_root_walks_up_from_files_and_folders(self):
        deep = os.path.join(self.d, 'bambulab', 'sub'); os.makedirs(deep)
        self.assertEqual(gate_common.find_root(deep), self.d)
        self.assertEqual(gate_common.find_root(os.path.join(deep, 'not_yet.3mf')), self.d)
        self.assertEqual(gate_common.find_root(self.d), self.d)
        other = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, other)
        self.assertIsNone(gate_common.find_root(other))

    def test_rules_dir_of_the_plugin_layout(self):
        self.assertEqual(gate_common.rules_dir(), os.path.join(ROOT, 'rules'))
        self.assertEqual(gate_common.tools_dir(), GATE)

    def test_json_files(self):
        p = gate_common.gate_path(self.d, 'x', 'a.json')
        gate_common.write_json(p, {'b': 1, 'a': [1.5, 'x']})
        self.assertEqual(gate_common.read_json(p), {'a': [1.5, 'x'], 'b': 1})
        self.assertFalse([f for f in os.listdir(os.path.dirname(p)) if 'tmp' in f])
        self.assertEqual(gate_common.read_json(p + '.none', {}), {})
        with self.assertRaises(GateError): gate_common.read_json(p + '.none')
        with open(p, 'w') as f: f.write('{broken')
        with self.assertRaises(GateError): gate_common.read_json(p, {})

    def test_jsonl(self):
        p = os.path.join(self.d, 'bambulab', 'ledger', 'events.jsonl')
        self.assertEqual(gate_common.events_path(self.d), p); self.assertEqual(gate_common.read_jsonl(p), [])
        gate_common.append_jsonl(p, {'kind': 'write', 'object': 'housing'}); gate_common.append_jsonl(p, {'kind': 'note'})
        with open(p, 'a') as f: f.write('not json\n\n')
        self.assertEqual([e['kind'] for e in gate_common.read_jsonl(p)], ['write', 'note'])

    def test_hashes_do_not_depend_on_key_order(self):
        self.assertEqual(gate_common.digest({'a': 1, 'b': [1, 2]}), gate_common.digest({'b': [1, 2], 'a': 1}))
        self.assertNotEqual(gate_common.digest({'a': 1}), gate_common.digest({'a': 2}))
        self.assertEqual(len(gate_common.digest('x', 64)), 64)
        p = os.path.join(self.d, 'f.bin')
        with open(p, 'wb') as f: f.write(b'abc')
        self.assertEqual(gate_common.sha256_file(p), 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')

    def test_part_ids(self):
        self.assertEqual(gate_common.slug('Motor plate (left) v2'), 'motor_plate_left_v2'); self.assertEqual(gate_common.slug('--'), 'part')
        self.assertEqual(gate_common.part_id('demo', 'Motor plate'), 'demo/motor_plate')
        self.assertEqual(gate_common.split_part_id('demo/motor_plate'), ('demo', 'motor_plate'))
        for bad in ('motor_plate', 'a/b/c', '/x', 'x/', None):
            with self.assertRaises(GateError): gate_common.split_part_id(bad)
        self.assertEqual(gate_common.sig12('0123456789abcdef'), '0123456789ab')

    def test_times_and_run_ids(self):
        self.assertRegex(gate_common.now(), r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        self.assertRegex(gate_common.new_run_id(), r'^\d{8}T\d{6}Z-[0-9a-f]{4}$')

    def test_project_and_layout_defaults(self):
        p = gate_common.load_project(self.d)
        self.assertEqual((p['mode'], p['not_printed']), ('observe', ['^ref ']))
        self.assertEqual(gate_common.target_of(p)['nozzle'], 0.6); self.assertEqual(gate_common.target_of(p, 'main')['material'], 'PETG')
        with self.assertRaises(GateError): gate_common.target_of(p, 'other')
        self.assertIsNone(gate_common.target_of({'targets': {'a': {}, 'b': {}}})); self.assertIsNone(gate_common.target_of({}))
        lay = gate_common.load_layout(self.d)
        self.assertEqual((lay['documents'], lay['projects'], lay['generators'], lay['fusion']['budget_s']), ([], [], [], 20))
        gate_common.write_json(gate_common.gate_path(self.d, 'layout.json'), {'documents': [{'key': 'demo', 'name': 'Demo Assembly', 'stl_dir': 'stl'}], 'fusion': {'budget_s': 15}})
        lay = gate_common.load_layout(self.d)
        self.assertEqual((lay['fusion']['budget_s'], lay['fusion']['endpoint']), (15, 'http://127.0.0.1:27182/mcp'))
        self.assertEqual(gate_common.document_entry(lay, 'demo')['name'], 'Demo Assembly')
        with self.assertRaises(GateError): gate_common.document_entry(lay, 'nope')

    def test_bad_project_files(self):
        gate_common.write_json(gate_common.gate_path(self.d, 'project.json'), {'mode': 'strict'})
        with self.assertRaises(GateError): gate_common.load_project(self.d)
        gate_common.write_json(gate_common.gate_path(self.d, 'layout.json'), {'documents': [{'key': 'a'}, {'key': 'a'}]})
        with self.assertRaises(GateError): gate_common.load_layout(self.d)

    def test_a_document_key_is_a_folder_name(self):
        for key in ('demo', 'Demo-2', 'a_b'):
            gate_common.write_json(gate_common.gate_path(self.d, 'layout.json'), {'documents': [{'key': key, 'name': 'Demo Assembly'}]})
            self.assertEqual(gate_common.load_layout(self.d)['documents'][0]['key'], key)
        for key in ('de mo', 'demo/2', 'demo\n# x', '..', 'd\u00e9mo', 7):
            gate_common.write_json(gate_common.gate_path(self.d, 'layout.json'), {'documents': [{'key': key, 'name': 'Demo Assembly'}]})
            with self.assertRaises(GateError) as e: gate_common.load_layout(self.d)
            self.assertIn('may hold only letters, digits, "_" and "-"', str(e.exception))

    def layout_with(self, names, **doc):
        gate_common.write_json(gate_common.gate_path(self.d, 'layout.json'),
                               {'documents': [dict({'key': 'demo', 'name': 'Demo Assembly', 'stl_dir': 'stl', 'stl_names': names}, **doc)]})
        return gate_common.load_layout(self.d)['documents'][0]

    def test_stl_names_give_a_part_the_file_the_project_already_has(self):
        doc = self.layout_with({'Motor plate (left)': 'MotorPlate_L.stl', 'cover': 'lid v2.STL'}, stl_dir='parts/stl/')
        self.assertEqual(gate_common.stl_of(doc, 'Motor plate (left)'), 'parts/stl/MotorPlate_L.stl')        # by the name as the census shows it
        self.assertEqual(gate_common.stl_of(doc, 'Cover'), 'parts/stl/lid v2.STL')                           # by the part slug
        self.assertEqual(gate_common.stl_of(doc, 'Bracket'), 'parts/stl/bracket.stl')                        # not in the map: the default
        self.assertEqual(gate_common.stl_of(doc, 'motor plate left'), 'parts/stl/motor_plate_left.stl')      # another name with that slug is not that key
        both = self.layout_with({'Cover': 'by_name.stl', 'cover': 'by_slug.stl'})
        self.assertEqual((gate_common.stl_of(both, 'Cover'), gate_common.stl_of(both, 'COVER')), ('stl/by_name.stl', 'stl/by_slug.stl'))      # the exact name first
        for doc in ({'key': 'demo', 'stl_dir': 'stl'}, {'key': 'demo', 'stl_dir': 'stl', 'stl_names': {}}):
            self.assertEqual(gate_common.stl_of(doc, 'Motor plate (left)'), 'stl/motor_plate_left.stl')      # no map: as before
        self.assertIsNone(gate_common.stl_of({'key': 'demo'}, 'Cover'))                                      # no STL folder, no STL
        self.assertEqual(self.layout_with({}, stl_dir=None)['stl_names'], {})                                # an empty map asks for nothing

    def test_a_bad_stl_names_map_is_refused_never_ignored(self):
        bad = {'no ending': 'cover', 'another ending': 'cover.step', 'a folder': 'old/cover.stl', 'a backslash': 'old\\cover.stl', 'up': '../cover.stl',
               'dot': '.', 'dots': '..', 'only the ending': '.stl', 'not ASCII': 'c\u00f6ver.stl', 'a control character': 'co\nver.stl', 'a tab': 'co\tver.stl',
               'empty': '', 'a number': 7, 'a list': ['cover.stl'], 'nothing': None,
               # names one file system takes and another does not: a colon, a wildcard, a quote, a pipe, angle brackets; a space or a dot in front
               'a colon': 'a:b.stl', 'a star': 'cover*.stl', 'a question mark': 'cover?.stl', 'a quote': 'co"ver.stl', 'a pipe': 'co|ver.stl',
               'angle brackets': '<cover>.stl', 'a space in front': ' cover.stl', 'a dot in front': '.cover.stl', 'two dots': '..stl'}
        for what, value in bad.items():
            with self.subTest(what=what):
                with self.assertRaises(GateError) as e: self.layout_with({'Cover': value})
                self.assertIn('"stl_names" of document \'demo\'', str(e.exception)); self.assertIn("'Cover'", str(e.exception))
                self.assertIsNotNone(gate_common.stl_name_error(value))
        for name in ('cover.stl', 'Cover v2 (left).STL', 'a.stl'): self.assertIsNone(gate_common.stl_name_error(name))
        for names, word in (({'Cover': 'lid.stl', 'Bracket': 'lid.stl'}, 'both name the file'), ({'Cover': 'lid.stl', 'cover': 'LID.STL'}, 'both name the file'),
                            (['cover.stl'], 'must be an object'), ('cover.stl', 'must be an object'), ({'': 'lid.stl'}, 'not an empty text'), ({' ': 'lid.stl'}, 'not an empty text')):
            with self.subTest(names=names):
                with self.assertRaises(GateError) as e: self.layout_with(names)
                self.assertIn(word, str(e.exception))
        made = {'generators': [{'part': 'demo/shim', 'stl': 'stl/Shim.stl', 'source': 'tools/make_shims.py'}]}       # the mesh a script computes
        for folder, value, bad_ in (('stl', 'shim.stl', True), ('stl/', 'SHIM.STL', True), ('./stl', 'shim.stl', True), ('parts', 'shim.stl', False), ('stl', 'shim2.stl', False)):
            with self.subTest(folder=folder, value=value):
                gate_common.write_json(gate_common.gate_path(self.d, 'layout.json'),
                                       dict(made, documents=[{'key': 'demo', 'name': 'Demo Assembly', 'stl_dir': folder, 'stl_names': {'Cover': value}}]))
                if not bad_: gate_common.load_layout(self.d); continue
                with self.assertRaises(GateError) as e: gate_common.load_layout(self.d)
                self.assertIn('is the STL of the generator of demo/shim (stl/Shim.stl)', str(e.exception))
        with self.assertRaises(GateError) as e: self.layout_with({'Cover': 'lid.stl'}, stl_dir=None)         # a file name needs its folder
        self.assertIn('needs the document\'s "stl_dir"', str(e.exception))
        code, out, err = self.gate('status')                                                                # and gate.py stops with it: exit 4, never a default path
        self.assertEqual(code, 4); self.assertIn('"stl_names"', out + err)

    def test_paths_of_one_part(self):
        sig = 'ab' * 32
        self.assertEqual(gate_common.rel(self.d, gate_common.facts_path(self.d, 'demo/bracket', sig)), 'gate/facts/demo/bracket/abababababab.json')
        self.assertEqual(gate_common.rel(self.d, gate_common.stamp_path(self.d, 'demo/bracket', sig)), 'gate/stamps/demo/bracket/abababababab.json')
        self.assertEqual(gate_common.rel(self.d, gate_common.attest_path(self.d, 'demo/bracket')), 'gate/attest/demo/bracket.json')
        self.assertEqual(gate_common.rel(self.d, gate_common.run_dir(self.d, 'r1')), 'gate/run/r1')
        self.assertEqual(gate_common.rel(self.d, gate_common.census_path(self.d)), 'gate/census.json')

    def test_facts_file_holds_probes_and_judgements(self):
        sig = 'cd' * 32
        self.assertEqual(gate_common.load_facts(self.d, 'demo/bracket', sig), {'part': 'demo/bracket', 'sig': sig, 'probes': {}, 'judged': {}})
        env = {'job': 'u-0001', 'doc': 'Demo Assembly', 'doc_version': 12, 'part': 'demo/bracket', 'sig': sig, 'probe': 'fusion_census',
               'facts': {'holes': []}, 'done': True, 'cursor': None, 'elapsed_s': 1.0, 'error': None}
        gate_common.save_probe_facts(self.d, 'demo/bracket', sig, env)
        gate_common.save_judged(self.d, 'demo/bracket', sig, 'inserts.pocket_shape', 'hhhh', {'inserts.wall_target': 4.0}, [])
        data = gate_common.load_facts(self.d, 'demo/bracket', sig)
        self.assertEqual(data['probes']['fusion_census']['facts'], {'holes': []}); self.assertEqual(data['doc_version'], 12)
        self.assertNotIn('probe', data['probes']['fusion_census']); self.assertIn('time', data['probes']['fusion_census'])
        self.assertEqual(data['judged']['inserts.pocket_shape']['judge_hash'], 'hhhh'); self.assertEqual(data['judged']['inserts.pocket_shape']['findings'], [])
        self.assertEqual(gate_common.load_census(self.d), {'docs': {}, 'parts': {}})

    def test_stored_facts_hold_no_other_document_name_and_no_absolute_path(self):
        sig = 'ef' * 32
        quarantine = os.path.join(self.d, 'gate', 'run', 'quarantine', 'demo__bracket.stl')
        env = {'job': 'u-0002', 'doc': 'Demo Assembly', 'doc_version': 12, 'part': 'demo/bracket', 'sig': sig, 'probe': 'fusion_export',
               'facts': {'stl': quarantine, 'written': True}, 'done': True, 'cursor': None, 'elapsed_s': 1.0, 'error': None,
               'open_documents': ['Demo Assembly', 'Another Design']}
        gate_common.save_probe_facts(self.d, 'demo/bracket', sig, env)
        with open(gate_common.facts_path(self.d, 'demo/bracket', sig), encoding='utf-8') as f: text = f.read()
        self.assertNotIn('open_documents', text); self.assertNotIn('Another Design', text); self.assertNotIn(self.d, text)
        entry = gate_common.load_facts(self.d, 'demo/bracket', sig)['probes']['fusion_export']
        self.assertEqual(entry['facts'], {'stl': 'gate/run/quarantine/demo__bracket.stl', 'written': True})
        self.assertEqual((env['facts']['stl'], env['open_documents']), (quarantine, ['Demo Assembly', 'Another Design']))      # the caller's envelope is not changed
        for stl in ('stl/bracket.stl', None):                    # a relative path and "nothing written" are stored as they are
            gate_common.save_probe_facts(self.d, 'demo/bracket', sig, dict(env, probe='stl_mesh', facts={'stl': stl}))
            self.assertEqual(gate_common.load_facts(self.d, 'demo/bracket', sig)['probes']['stl_mesh']['facts'], {'stl': stl})

    def test_siblings_and_legacy_tools(self):
        self.assertEqual(gate_common.sibling('meshid').QUANTUM, 0.001)
        self.assertTrue(hasattr(gate_common.sibling('ledger'), 'record_writes'))
        with self.assertRaises(GateError): gate_common.sibling('nope')
        self.assertTrue(gate_common.fusion_tool_path('insert_pockets').endswith(os.path.join('tools', 'fusion', 'insert_pockets.py')))
        self.assertIsNone(gate_common.fusion_tool_path('nope'))


class Dispatch(Project):
    def test_every_command_names_a_function_that_exists(self):
        for name, (module, func) in gate.COMMANDS.items():
            with self.subTest(command=name): self.assertTrue(callable(getattr(importlib.import_module(module), func)))

    def test_commands_of_the_build_spec(self):
        self.assertEqual(sorted(gate.COMMANDS), sorted('doctor rules params scan run status attest export verify report triage owner'.split()))

    def test_params_check_runs_end_to_end(self):
        code, out, err = self.gate('params', 'check')
        self.assertEqual((code, err), (0, '')); self.assertIn('TOTAL 0', out)
        gate_common.write_json(gate_common.gate_path(self.d, 'owner', 'params.json'),
                               {'inserts.wall_target': {'value': 2.0, 'tag': 'D', 'source': 'agent', 'date': '2026-03-04'}})
        code, out, err = self.gate('params', 'check')
        self.assertEqual(code, 4); self.assertIn('is looser than the default 4.0', out)
        code, out, err = self.gate('params', 'explain', 'walls.min_wall_effective', '--target', 'main')
        self.assertEqual(code, 4)                                    # the project file is still broken
        os.remove(gate_common.gate_path(self.d, 'owner', 'params.json'))
        code, out, err = self.gate('params', 'explain', 'walls.min_wall_effective', '--target', 'main')
        self.assertEqual(code, 0); self.assertIn('0.62 mm [declared target]', out)

    def test_a_part_that_is_not_built_is_exit_4_never_a_pass(self):
        for argv in (['status'], ['run', '--changed'], ['verify'], ['owner', 'waive', 'F-000000', '--reason', 'r', '--quote', 'q']):
            module, func = gate.COMMANDS[argv[0]]
            fn = getattr(importlib.import_module(module), func)
            try: fn(None, None)
            except NotImplementedError: pass
            except Exception: continue                               # already built by its package: nothing to check here
            code, out, err = self.gate(*argv)
            self.assertEqual(code, 4, argv); self.assertIn('is not built yet', err)

    def test_usage_errors_are_exit_4_not_2(self):
        for argv in ([], ['frobnicate'], ['params', 'tune'], ['run', '--changed', '--all'], ['attest', 'answer', 'Q-1', '--label', 'a', '--na', 'b'],
                     ['owner', 'decide', 'T-0001', 'maybe', '--quote', 'q']):
            code, out, err = self.gate(*argv)
            self.assertEqual(code, 4, argv)

    def test_arguments_reach_the_namespace_the_interface_promises(self):
        p = gate.parser()
        a = p.parse_args(['run', '--part', 'demo/a', '--part', 'demo/b', '--parts', 'demo/a,demo/c', '--wall', '15', '--audit'])
        self.assertEqual((a.part, a.intent, a.wall, a.audit, a.offline, a.changed, a.all), (['demo/a', 'demo/b'], ['demo/a', 'demo/c'], 15.0, True, False, False, False))
        a = p.parse_args(['run']); self.assertEqual((a.part, a.intent, a.wall), ([], None, 20.0))
        a = p.parse_args(['attest', 'answer', 'Q-51b0c2', '--label', 'the bottom'])
        self.assertEqual((a.action, a.question, a.label, a.na, a.param, a.shot), ('answer', 'Q-51b0c2', 'the bottom', None, None, None))
        a = p.parse_args(['export', 'demo/a', 'demo/b', '--for', 'main']); self.assertEqual((a.parts, a.generated, a.target), (['demo/a', 'demo/b'], None, 'main'))
        a = p.parse_args(['export', '--generated', 'stl/shim.stl']); self.assertEqual((a.parts, a.generated), ([], 'stl/shim.stl'))
        a = p.parse_args(['owner', 'param', 'inserts.wall_target', '4.0', '--tag', 'U', '--source', 'owner, chat', '--quote', 'make it four'])
        self.assertEqual((a.action, a.name, a.value, a.tag, a.source, a.key, a.quote), ('param', 'inserts.wall_target', '4.0', 'U', 'owner, chat', None, 'make it four'))
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):                                  # no owner command without his words
            p.parse_args(['owner', 'param', 'inserts.wall_target', '4.0', '--tag', 'U', '--source', 'owner, chat'])
        a = p.parse_args(['owner', 'decide', 'T-0003', 'tolerate', '--quote', 'q']); self.assertEqual((a.item, a.decision, a.note), ('T-0003', 'tolerate', None))
        a = p.parse_args(['verify']); self.assertIsNone(a.project)
        a = p.parse_args(['rules', '--md', '--category', 'inserts']); self.assertEqual((a.md, a.category), (True, 'inserts'))
        a = p.parse_args(['status', '--digest']); self.assertEqual((a.json, a.digest), (False, True))
        a = p.parse_args(['scan', '--doc', 'demo']); self.assertEqual(a.doc, 'demo')

    def test_no_project_is_exit_4_except_doctor_and_rules(self):
        other = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, other)
        r = subprocess.run([sys.executable, os.path.join(GATE, 'gate.py'), 'status'], cwd=other, capture_output=True, text=True)
        self.assertEqual(r.returncode, 4); self.assertIn('this folder is not gated', r.stderr)
        r = subprocess.run([sys.executable, os.path.join(GATE, 'gate.py'), '--root', other, 'status'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 4); self.assertIn('has no gate/project.json', r.stderr)
        self.assertEqual(gate.NO_ROOT, ('doctor', 'rules'))

    def test_gate_error_is_exit_4_with_the_message(self):
        gate_common.write_json(gate_common.gate_path(self.d, 'project.json'), {'mode': 'strict'})
        code, out, err = self.gate('params', 'check')
        self.assertEqual(code, 4); self.assertIn('GATE ERROR', err); self.assertIn('mode must be', err)


class Hook(unittest.TestCase):
    def test_the_hook_exits_0_and_prints_nothing_whatever_happens(self):
        for stdin in ('', 'not json', '{"cwd": "/nowhere/at/all"}'):
            for argv in ([], ['session-start'], ['--bogus', 'x']):
                r = subprocess.run([sys.executable, os.path.join(GATE, 'gate_hook.py')] + argv, input=stdin, capture_output=True, text=True, timeout=20)
                self.assertEqual((r.returncode, r.stderr), (0, ''), (stdin, argv))

    def test_main_swallows_every_error(self):
        out = io.StringIO()
        self.assertEqual(gate_hook.main([], stdin=io.StringIO('{"cwd": "."}'), stdout=out), 0)


if __name__ == '__main__':
    unittest.main()
