"""The SessionStart hook: silent where nothing is gated, never exit 2, never slow, and the digest of a gated project.
No Fusion, no Bambu Studio."""
import io, json, os, shutil, subprocess, sys, tempfile, time, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(ROOT, 'tools', 'gate')
sys.path.insert(0, GATE)
import gate_common, gate_hook, gate_report, gate_vendor

HOOK = os.path.join(GATE, 'gate_hook.py')
CANNED = {'mode': 'observe', 'parts': 7, 'changed': ['demo/bracket', 'demo/cover'], 'triage_open': 3, 'inherited': 3,
          'open': [{'id': 'F-0a1b2c', 'rule': 'inserts.wall', 'part': 'demo/bracket', 'verdict': 'GROW',
                    'text': 'pocket M3x4x5 at (12.0, -30.0, 40.0): wall 2.5 under 4.0'},
                   {'id': 'F-3d4e5f', 'rule': 'bearings.holding', 'part': 'demo/cover', 'verdict': 'FAIL',
                    'text': 'bearing 6005: the outer ring is held by nothing on one face'}],
          'questions': [{'q': 'Q-51b0c2', 'rule': 'print.bed_face', 'part': 'demo/bracket', 'question': 'Which face lies on the bed?'}],
          'unstamped_stls': ['stl/cover.stl'], 'last_run': '20260304T101500Z-3fa2', 'last_scan': '2026-03-04T09:12:07Z',
          'unchecked': ['demo/cover'], 'stale': [], 'stl_states': {'stl/cover.stl': 'none', 'stl/bracket.stl': 'valid'},
          'unfinished': None, 'unreachable': []}


def clean_env():
    env = dict(os.environ)
    for k in ('CLAUDE_PROJECT_DIR', 'CLAUDE_PLUGIN_ROOT'):
        env.pop(k, None)
    return env


class Tmp(unittest.TestCase):
    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.d)

    def gated(self, name='proj'):
        root = os.path.join(self.d, name)
        gate_common.write_json(gate_common.gate_path(root, 'project.json'), {'mode': 'observe'})
        return root

    def run_hook(self, argv=('session-start',), stdin='', cwd=None, env=None):
        t = time.time()
        r = subprocess.run([sys.executable, HOOK] + list(argv), input=stdin, capture_output=True, text=True, timeout=30,
                           cwd=cwd or self.d, env=env or clean_env())
        return r, time.time() - t


class NeverInTheWay(Tmp):
    def test_no_project_is_silent_and_exit_0(self):
        for stdin in ('', '{}', json.dumps({'cwd': self.d, 'hook_event_name': 'SessionStart', 'source': 'startup'})):
            r, _ = self.run_hook(stdin=stdin)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, '', ''), stdin)

    def test_bad_input_is_silent_and_exit_0(self):
        for stdin in ('not json', '[1, 2', '{"cwd": 5}', '{"cwd": "' + os.path.join(self.d, 'no', 'such', 'folder') + '"}', '\x00\x01', 'null', '"text"'):
            r, _ = self.run_hook(stdin=stdin)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, '', ''), repr(stdin))

    def test_an_unknown_argument_is_silent_even_in_a_gated_project(self):
        root = self.gated()
        for argv in (['--bogus'], ['pre-command'], ['session-start', 'extra'], ['-h'], ['--help']):
            r, _ = self.run_hook(argv=argv, stdin=json.dumps({'cwd': root}))
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, '', ''), argv)

    def test_broken_project_files_never_exit_2(self):
        root = self.gated()
        cases = {'project.json': '{not json', 'layout.json': '[]', 'census.json': '{"parts": 5}', 'LOCK.json': 'x'}
        for name, text in cases.items():
            gate_common.write_json(gate_common.gate_path(root, 'project.json'), {'mode': 'observe'})
            with open(gate_common.gate_path(root, name), 'w', encoding='utf-8') as f:
                f.write(text)
            r, _ = self.run_hook(stdin=json.dumps({'cwd': root}))
            self.assertEqual((r.returncode, r.stderr), (0, ''), name)
            self.assertNotIn('Traceback', r.stdout)

    def test_a_missing_gate_folder_content_is_fine(self):
        root = self.gated()                                     # only gate/project.json: no layout, no census, no lock
        r, _ = self.run_hook(stdin=json.dumps({'cwd': root}))
        self.assertEqual((r.returncode, r.stderr), (0, ''))
        self.assertIn('gate [observe]', r.stdout)

    def test_an_input_that_never_ends_does_not_hold_the_session(self):
        root = self.gated()
        env = clean_env(); env['CLAUDE_PROJECT_DIR'] = root
        p = subprocess.Popen([sys.executable, HOOK, 'session-start'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.d, env=env)
        try:
            out, err = b'', b''
            code = p.wait(timeout=20)                           # stdin stays open and empty the whole time
            out, err = p.stdout.read(), p.stderr.read()
        finally:
            p.stdin.close(); p.stdout.close(); p.stderr.close()
            if p.poll() is None: p.kill()
        self.assertEqual((code, err), (0, b''))
        self.assertIn(b'gate [observe]', out)

    def test_an_input_that_is_sent_and_never_closed_is_still_read(self):
        root = self.gated()
        p = subprocess.Popen([sys.executable, HOOK, 'session-start'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.d, env=clean_env())
        try:
            p.stdin.write(json.dumps({'cwd': root}).encode()); p.stdin.flush()      # no close: the project is known from this text alone
            code = p.wait(timeout=20)
            out, err = p.stdout.read(), p.stderr.read()
        finally:
            p.stdin.close(); p.stdout.close(); p.stderr.close()
            if p.poll() is None: p.kill()
        self.assertEqual((code, err), (0, b''))
        self.assertIn(b'gate [observe]', out)

    def test_main_returns_0_whatever_the_streams_do(self):
        class Broken(io.StringIO):
            def write(self, s): raise OSError('closed pipe')
        root = self.gated()
        self.assertEqual(gate_hook.main(['session-start'], stdin=io.StringIO(json.dumps({'cwd': root})), stdout=Broken()), 0)
        self.assertEqual(gate_hook.main(['session-start'], stdin=None, stdout=io.StringIO()), 0)
        self.assertEqual(gate_hook.main(None, stdin=io.StringIO('x'), stdout=io.StringIO()), 0)


class FindProject(Tmp):
    def test_the_hook_input_wins_then_the_environment_then_the_working_directory(self):
        a, b, c = self.gated('a'), self.gated('b'), self.gated('c')
        self.assertEqual(gate_hook.find_project(json.dumps({'cwd': a}), {'CLAUDE_PROJECT_DIR': b}, c), a)
        self.assertEqual(gate_hook.find_project('not json', {'CLAUDE_PROJECT_DIR': b}, c), b)
        self.assertEqual(gate_hook.find_project('', {}, c), c)
        self.assertEqual(gate_hook.find_project(json.dumps({'cwd': self.d}), {'CLAUDE_PROJECT_DIR': b}, c), b)   # the input names a folder that is not gated

    def test_a_folder_inside_the_project_finds_it(self):
        root = self.gated(); sub = os.path.join(root, 'bambulab', 'tools'); os.makedirs(sub)
        self.assertEqual(gate_hook.find_project(json.dumps({'cwd': sub}), {}, self.d), root)

    def test_nothing_gated_is_none(self):
        self.assertIsNone(gate_hook.find_project('', {}, self.d))
        self.assertIsNone(gate_hook.find_project(json.dumps({'cwd': None}), {'CLAUDE_PROJECT_DIR': ''}, ''))


class Digest(Tmp):
    def patch(self, name, value):
        old = getattr(gate_report, name)
        setattr(gate_report, name, value)
        self.addCleanup(setattr, gate_report, name, old)

    def hook(self, root):
        out = io.StringIO()
        self.assertEqual(gate_hook.main(['session-start'], stdin=io.StringIO(json.dumps({'cwd': root, 'source': 'startup'})), stdout=out), 0)
        return out.getvalue()

    def test_the_digest_of_a_canned_status(self):
        root = self.gated()
        self.patch('status', lambda r, *a, **kw: dict(CANNED))
        text = self.hook(root)
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith('Print gate of %s' % root))
        self.assertIn('gate [observe]: 7 parts', text)
        self.assertIn('2 changed since their stamp', text)
        self.assertIn('2 open findings', text)
        self.assertIn('1 questions', text)
        self.assertIn('1 STLs without a valid stamp', text)
        self.assertIn('inserts.wall', text); self.assertIn('F-0a1b2c', text); self.assertIn('wall 2.5 under 4.0', text)
        self.assertIn('bearings.holding', text)
        self.assertIn('Q-51b0c2', text)
        self.assertIn('stl/cover.stl', text)
        for command in ('gate.py status', 'gate.py run --changed', 'gate.py export'):
            self.assertIn(command, text)
        self.assertLessEqual(len(lines), gate_report.DIGEST_LINES + 2)
        self.assertLess(len(text), gate_hook.MAX_CHARS)

    def test_many_findings_stay_short(self):
        root = self.gated()
        many = dict(CANNED, open=[dict(CANNED['open'][0], id='F-%06x' % i, text='row %d ' % i + 'x' * 300) for i in range(400)])
        self.patch('status', lambda r, *a, **kw: many)
        text = self.hook(root)
        self.assertIn('400 open findings', text)
        self.assertLessEqual(len(text.splitlines()), gate_report.DIGEST_LINES + 2)
        self.assertLess(len(text), 10000)                       # what Claude Code keeps of a hook's output

    def test_a_status_that_fails_is_one_line_not_an_error(self):
        root = self.gated()
        def boom(r, *a, **kw): raise RuntimeError('census is broken')
        self.patch('status', boom)
        text = self.hook(root)
        self.assertIn('status unavailable', text); self.assertNotIn('Traceback', text)

    def test_a_slow_digest_is_left_out_and_the_status_command_is_named(self):
        root = self.gated()
        self.patch('digest', lambda r: time.sleep(3) or 'late')
        t = time.time()
        text = self.hook(root)
        self.assertLess(time.time() - t, 2.5)
        self.assertNotIn('late', text)
        self.assertIn('took longer', text); self.assertIn('gate.py status', text)

    def test_an_empty_digest_prints_nothing(self):
        root = self.gated()
        self.patch('digest', lambda r: '')
        self.assertEqual(self.hook(root), '')

    def test_a_forked_or_stale_copy_is_named(self):
        root = self.gated()
        self.patch('status', lambda r, *a, **kw: dict(CANNED))
        os.makedirs(gate_common.gate_path(root, 'tools'))
        with open(gate_common.gate_path(root, 'tools', 'gate.py'), 'w', encoding='utf-8') as f: f.write('# one\n')
        gate_vendor.write_lock(root, '0.0.1')
        text = self.hook(root)
        self.assertIn('vendored gate is version 0.0.1', text); self.assertIn('--update-tools', text)
        with open(gate_common.gate_path(root, 'tools', 'gate.py'), 'w', encoding='utf-8') as f: f.write('# two\n')
        text = self.hook(root)
        self.assertIn('FORKED', text); self.assertIn('tools/gate.py', text)
        gate_vendor.write_lock(root, gate_vendor.plugin_version(ROOT))
        self.assertNotIn('vendored gate', self.hook(root))

    def test_a_real_project_end_to_end_in_a_process(self):
        root = self.gated()
        r, took = self.run_hook(stdin=json.dumps({'cwd': root, 'hook_event_name': 'SessionStart', 'source': 'resume'}))
        self.assertEqual((r.returncode, r.stderr), (0, ''))
        self.assertIn('Print gate of %s' % root, r.stdout); self.assertIn('gate [observe]: 0 parts', r.stdout)
        self.assertLess(took, 10)                               # the hook's own budget is under a second; this only catches a hang

    def test_the_hook_runs_the_plugin_copy_never_the_code_of_the_project(self):
        root = self.gated(); os.makedirs(gate_common.gate_path(root, 'tools'))
        marker = os.path.join(self.d, 'ran')
        for name in ('gate_report.py', 'gate_hook.py', 'gate_common.py', 'gate_vendor.py'):
            with open(gate_common.gate_path(root, 'tools', name), 'w', encoding='utf-8') as f:
                f.write('open(%r, "w").close()\n' % marker)
        r, _ = self.run_hook(stdin=json.dumps({'cwd': root}), cwd=gate_common.gate_path(root, 'tools'))
        self.assertEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(marker), 'a hook must not run what a cloned folder brings along')


class HooksFile(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, 'hooks', 'hooks.json'), encoding='utf-8') as f:
            self.data = json.load(f)

    def test_the_event_map_is_wrapped_and_holds_session_start_only(self):
        self.assertEqual(list(self.data), ['hooks'])            # a file with the bare event map does not load
        self.assertEqual(list(self.data['hooks']), ['SessionStart'])

    def test_the_handler_is_one_direct_command_on_the_plugin_copy(self):
        groups = self.data['hooks']['SessionStart']
        self.assertEqual(len(groups), 1)
        handlers = groups[0]['hooks']
        self.assertEqual(len(handlers), 1)
        h = handlers[0]
        self.assertEqual(h['type'], 'command')
        self.assertEqual(h['command'], 'python3')               # with "args" the command is spawned directly: no shell, no quoting
        self.assertEqual(h['args'], ['${CLAUDE_PLUGIN_ROOT}/tools/gate/gate_hook.py', 'session-start'])
        self.assertTrue(os.path.isfile(h['args'][0].replace('${CLAUDE_PLUGIN_ROOT}', ROOT)))
        self.assertIn(h['args'][1], gate_hook.EVENTS)
        self.assertLessEqual(h['timeout'], 15)
        self.assertNotIn('onFailure', h)


if __name__ == '__main__':
    unittest.main()
