"""The live hooks of the gate (gate_watch.py through gate_hook.py): dirty marks after a Fusion write, a Stop that waits
for the check, PreToolUse denials of the gate's own state and of prints written past the gate, and the owner quote
check against the session transcript. Every case feeds the hook JSON a host would send. No Fusion, no Bambu Studio."""
import json, os, shutil, subprocess, sys, tempfile, time, types, unittest
from unittest import mock
for _k in ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID'): os.environ.pop(_k, None)      # gate.py owner from an agent session needs the hook's ticket: a test is no agent session
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(ROOT, 'tools', 'gate')
sys.path.insert(0, GATE)
import gate_common, gate_hook, gate_watch

HOOK = os.path.join(GATE, 'gate_hook.py')
EXEC = 'mcp__fusion__fusion_mcp_execute'
GATE_PY = 'python3 gate/tools/gate.py'


def clean_env(index=None):
    env = dict(os.environ)
    for k in ('CLAUDE_PROJECT_DIR', 'CLAUDE_PLUGIN_ROOT'):
        env.pop(k, None)
    env[gate_watch.INDEX_ENV] = index or os.path.join(tempfile.gettempdir(), 'gate-roots-of-the-tests.json')     # never the user's own list
    return env


class Watch(unittest.TestCase):
    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.d)
        self.root = os.path.join(self.d, 'proj')
        self.project('observe')
        gate_common.write_json(gate_common.gate_path(self.root, 'layout.json'),
                               {'documents': [{'key': 'demo', 'name': 'Demo', 'stl_dir': 'stl'}],
                                'projects': [{'key': 'main', 'file': 'bambulab/Demo.3mf', 'stl_dir': 'stl'}]})
        self.transcript = os.path.join(self.d, 'session.jsonl')
        self.turns([])
        self.vendored()

    def vendored(self, have=True):
        """The project's vendored gate: 0.3.0 and later keep checks.jsonl (gate/tools/gate_watch.py)."""
        path = gate_common.gate_path(self.root, 'tools', 'gate_watch.py')
        if have:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f: f.write('# the vendored copy\n')
        elif os.path.exists(path):
            os.remove(path)

    def project(self, mode, targets=None):
        gate_common.write_json(gate_common.gate_path(self.root, 'project.json'), {'mode': mode, 'targets': targets or {'main': {'nozzle': 0.4}}})

    def turns(self, lines):
        with open(self.transcript, 'w', encoding='utf-8') as f:
            for line in lines:
                f.write(json.dumps(line) + '\n')

    @staticmethod
    def human(text, **more):
        return dict({'type': 'user', 'origin': {'kind': 'human'}, 'isSidechain': False, 'message': {'role': 'user', 'content': text}}, **more)

    def data(self, **kw):
        d = {'session_id': 's1', 'cwd': self.root, 'transcript_path': self.transcript}
        d.update(kw)
        return d

    def pre(self, tool, **ti):
        out = gate_watch.pre_tool(self.data(tool_name=tool, tool_input=ti), {})
        return json.loads(out)['hookSpecificOutput'] if out else None

    def bash(self, cmd):
        return self.pre('Bash', command=cmd)

    def denied(self, out):
        return out is not None and out.get('permissionDecision') == 'deny'

    def warned(self, out):
        return out is not None and 'permissionDecision' not in out and bool(out.get('additionalContext'))

    def stop(self, subagent=False, **kw):
        out = gate_watch.stop(self.data(**kw), {}, subagent=subagent)
        return json.loads(out) if out else None

    def mark(self, tool=EXEC, **kw):
        return gate_watch.post_tool(self.data(tool_name=tool, tool_input=kw.pop('ti', {'object': {'script': 'x = 1'}}), **kw), {})

    def checked(self, code, changed=True, session=None, started=None, **flags):
        gate_watch.record_check(self.root, types.SimpleNamespace(changed=changed, all=False, part=[], target='main', **flags), code, started, session)


class DirtyMarks(Watch):
    def marks(self):
        return gate_common.read_jsonl(gate_common.gate_path(self.root, 'run', 'dirty.jsonl'))

    def test_a_writing_fusion_call_leaves_a_mark_and_says_what_to_run(self):
        out = json.loads(self.mark())
        self.assertEqual(out['hookSpecificOutput']['hookEventName'], 'PostToolUse')
        self.assertIn('python3 gate/tools/gate.py run --changed --for main until it no longer exits 3', out['hookSpecificOutput']['additionalContext'])
        self.assertEqual([(m['session'], m['tool']) for m in self.marks()], [('s1', EXEC)])
        self.mark(tool='mcp__plugin_fusion_fusion__fusion_mcp_update', ti={'object': {}})
        self.assertEqual(len(self.marks()), 2)

    def test_read_only_calls_and_the_gate_own_commands_leave_none(self):
        self.assertEqual(self.mark(ti={'object': {'script': 'x = 1', 'readOnly': True}}), '')
        for cmd in ('ls -la', 'python3 gate/tools/gate.py run --changed --for main', 'python3 tools/fusion_mcp.py run probe.py',
                    'python3 tools/fusion_mcp.py call fusion_mcp_execute \'{"script": "x", "readOnly": true}\'',
                    'curl -s http://127.0.0.1:27182/mcp -d \'{"params": {"name": "fusion_mcp_read"}}\'', 'curl http://127.0.0.1:27182/ping'):
            self.assertEqual(self.mark(tool='Bash', ti={'command': cmd}), '', cmd)
        self.assertEqual(self.marks(), [])

    def test_bash_commands_that_send_a_writing_script_to_fusion_leave_one(self):
        for cmd in ('python3 tools/fusion_mcp.py run edit.py --write', 'python3 tools/fusion_mcp.py call fusion_mcp_update \'{}\'',
                    'python3 tools/fusion_mcp.py call fusion_mcp_execute \'{"script": "x"}\'',
                    'curl -s -X POST http://127.0.0.1:27182/mcp -d \'{"params": {"name": "fusion_mcp_execute", "arguments": {"script": "x"}}}\''):
            self.assertNotEqual(self.mark(tool='Bash', ti={'command': cmd}), '', cmd)
        self.assertEqual(len(self.marks()), 4)

    def test_a_session_outside_a_gated_root_is_left_alone(self):
        out = gate_watch.post_tool({'session_id': 's1', 'cwd': self.d, 'tool_name': EXEC, 'tool_input': {}}, {})
        self.assertEqual(out, '')
        self.assertFalse(os.path.exists(gate_common.gate_path(self.root, 'run', 'dirty.jsonl')))


class StopHook(Watch):
    def test_nothing_changed_nothing_blocked(self):
        self.assertIsNone(self.stop())

    def test_a_change_blocks_the_stop_with_the_exact_command_until_a_check_ran(self):
        self.mark()
        out = self.stop()
        self.assertEqual(out['decision'], 'block')
        self.assertIn('Run python3 gate/tools/gate.py run --changed --for main until it no longer exits 3', out['reason'])
        self.checked(3)                                     # the check needs more calls: still not checked
        self.assertIn('needs more calls (exit 3)', self.stop(stop_hook_active=True)['reason'])
        for code in (0, 1, 2):                              # 1: open findings, 2: questions; checked either way, the agent reports them
            time.sleep(0.01); self.mark(); self.assertEqual(self.stop()['decision'], 'block')
            time.sleep(0.01); self.checked(code)
            self.assertIsNone(self.stop(), code)

    def test_several_targets_name_every_key(self):
        self.project('observe', {'a': {'nozzle': 0.6}, 'b': {'nozzle': 0.4}})
        self.mark()
        self.assertIn('--for <KEY> (KEY: any of a, b; --changed takes every changed part whatever the key)', self.stop()['reason'])

    def test_a_run_by_part_is_no_check_of_the_changed_parts(self):
        self.mark(); time.sleep(0.01)
        gate_watch.record_check(self.root, types.SimpleNamespace(changed=False, all=False, part=['demo/x'], target='main'), 0)
        self.assertEqual(self.stop()['decision'], 'block')

    def test_exit_4_lets_the_turn_end_and_says_not_checked(self):
        self.mark(); time.sleep(0.01); self.checked(4)
        out = self.stop()
        self.assertNotIn('decision', out); self.assertIn('NOT checked', out['systemMessage'])

    def test_it_cannot_loop_for_ever(self):
        self.mark()
        self.assertEqual(self.stop()['decision'], 'block')
        for _ in range(gate_watch.STOP_CAP - 1):
            self.assertEqual(self.stop(stop_hook_active=True)['decision'], 'block')
        out = self.stop(stop_hook_active=True)
        self.assertNotIn('decision', out); self.assertIn('are NOT checked, and the turn ends anyway', out['systemMessage'])
        out = self.stop(stop_hook_active=False)             # the next turn: the marks let go never block again, one line says them
        self.assertNotIn('decision', out); self.assertIn('still NOT checked', out['systemMessage'])
        time.sleep(0.01); self.mark()
        self.assertEqual(self.stop(stop_hook_active=False)['decision'], 'block')     # a new change does

    def test_a_subagent_answers_for_its_own_changes(self):
        self.mark(agent_id='a1')
        self.assertIsNone(self.stop(subagent=True, agent_id='a2'))
        self.assertEqual(self.stop(subagent=True, agent_id='a1')['decision'], 'block')
        self.assertEqual(self.stop()['decision'], 'block')  # and the session that started it too
        self.assertEqual(gate_watch.stop(self.data(session_id='s2'), {}), '')    # another session: not its business

    def test_no_gate_no_block(self):
        self.mark()
        self.assertEqual(gate_watch.stop({'session_id': 's1', 'cwd': self.d}, {}), '')

    def test_saves_that_let_objects_through_without_a_stamp_are_said_at_the_stop(self):
        self.bash('ls')                                     # the first tool call of the session: from then on
        self.assertIsNone(self.stop()); time.sleep(0.01)
        gate_common.write_json(gate_common.gate_path(self.root, 'run', 'saves.json'), {'projects': {
            'bambulab/Demo.3mf': {'time': gate_common.now(), 'ts': time.time(), 'mode': 'observe', 'size': 1, 'mtime_ns': 1, 'unchecked': ['bracket', 'cover']}}})
        out = self.stop()
        self.assertEqual(out['systemMessage'].splitlines()[0][:68], 'gate: 2 object(s) in bambulab/Demo.3mf have no stamp: bracket, cover')
        self.assertIsNone(self.stop())                      # said once, at the stop after the save


class PreToolState(Watch):
    def test_the_owner_files_stamps_and_vendored_gate_are_never_written_by_a_tool(self):
        for rel in ('gate/owner/params.json', 'gate/project.json', 'gate/stamps/demo/x.json', 'gate/tools/gate_guard.py', 'bambulab/ledger/events.jsonl',
                    'bambulab/tools/bambu3mf.py', 'gate/run/dirty.jsonl'):
            self.assertTrue(self.denied(self.pre('Write', file_path=os.path.join(self.root, rel), content='{}')), rel)
            self.assertTrue(self.denied(self.pre('Edit', file_path=os.path.join(self.root, rel), old_string='a', new_string='b')), rel)
        self.assertIn('gate.py owner', self.pre('Write', file_path='gate/project.json', content='{}')['permissionDecisionReason'])     # relative to cwd

    def test_shell_writes_of_the_state_are_denied(self):
        for cmd in ('echo {} > gate/project.json', 'sed -i "" s/observe/enforce/ gate/project.json', 'cp /x/w.json gate/owner/waivers.json',
                    'tee -a gate/owner/triage.json < x', 'rm -rf gate', 'mv bambulab old', 'git checkout -- gate/owner/params.json',
                    'cd gate && echo x > project.json', 'bash -c "echo enforce > gate/project.json"',
                    "python3 -c \"open('gate/project.json', 'w').write('{}')\"",
                    "python3 - <<'EOF'\nimport json\njson.dump({}, open('gate/owner/params.json', 'w'))\nEOF",
                    "bash <<'EOF'\nrm gate/stamps/demo/x.json\nEOF",
                    'echo x > %s' % os.path.join(self.root, 'gate', 'stamps', 'a.json')):
            self.assertTrue(self.denied(self.bash(cmd)), cmd)

    def test_read_only_commands_pass(self):
        for cmd in ('cat gate/project.json', 'python3 gate/tools/gate.py status', 'git status', 'git diff gate/project.json', 'grep -n mode gate/project.json > /dev/null',
                    'python3 -c "import json; print(json.load(open(\'gate/project.json\'))[\'mode\'])"', 'ls gate/owner 2>&1',
                    "git commit -m \"$(cat <<'EOF'\nmode -> enforce in gate/project.json later\nEOF\n)\"",
                    "cat > /tmp/x/notes.txt <<'EOF'\ngate/project.json > the target\nEOF"):
            self.assertIsNone(self.bash(cmd), cmd)
        self.assertIsNone(self.pre('Write', file_path=os.path.join(self.root, 'gate', 'proposals', 'p.json'), content='{}'))
        self.assertIsNone(self.pre('Write', file_path=os.path.join(self.root, 'notes.md'), content='x'))

    def test_code_that_reads_the_state_and_writes_elsewhere_is_said_not_stopped(self):
        out = self.bash("python3 - <<'EOF'\nimport json\np = json.load(open('gate/project.json'))\nopen('/scratch/out.txt', 'w').write(str(p))\nEOF")
        self.assertTrue(self.warned(out)); self.assertIn('If it writes gate/project.json, stop', out['additionalContext'])

    def test_the_layout_keeps_every_registered_project(self):
        path = gate_common.gate_path(self.root, 'layout.json')
        with open(path, encoding='utf-8') as f: old = f.read()
        more = json.loads(old); more['documents'].append({'key': 'other', 'name': 'Other'})
        self.assertIsNone(self.pre('Write', file_path=path, content=json.dumps(more)))
        less = json.loads(old); less['projects'] = []
        self.assertTrue(self.denied(self.pre('Write', file_path=path, content=json.dumps(less))))
        self.assertTrue(self.denied(self.bash('echo {} > gate/layout.json')))

    def test_settings_keep_their_hooks_and_permissions(self):
        path = os.path.join(self.root, '.claude', 'settings.json')
        gate_common.write_json(path, {'hooks': {'Stop': []}, 'env': {'A': '1'}})
        self.assertIsNone(self.pre('Write', file_path=path, content=json.dumps({'hooks': {'Stop': []}, 'env': {'A': '2'}})))
        self.assertTrue(self.denied(self.pre('Write', file_path=path, content=json.dumps({'env': {'A': '1'}}))))
        self.assertTrue(self.denied(self.pre('Write', file_path=path, content=json.dumps({'hooks': {'Stop': []}, 'disableAllHooks': True}))))

    def test_only_this_plugin_own_templates_are_no_gated_project(self):
        plugin = os.path.join(self.d, 'plugin')
        gate_common.write_json(os.path.join(plugin, '.claude-plugin', 'plugin.json'), {'name': gate_watch.OWN_NAME})
        gate_common.write_json(os.path.join(plugin, 'templates', 'gate', 'project.json'), {'mode': 'observe'})
        self.assertIsNone(gate_watch.gated_root(os.path.join(plugin, 'templates', 'gate', 'project.json')))
        self.assertIsNone(self.pre('Write', file_path=os.path.join(plugin, 'templates', 'gate', 'project.json'), content='{}'))
        gate_common.write_json(os.path.join(plugin, '.claude-plugin', 'plugin.json'), {'name': 'another plugin'})
        self.assertIsNotNone(gate_watch.gated_root(os.path.join(plugin, 'templates', 'gate', 'project.json')))     # any other plugin.json switches nothing off
        gate_common.write_json(os.path.join(self.d, '.claude-plugin', 'plugin.json'), {'name': gate_watch.OWN_NAME})
        self.assertEqual(gate_watch.gated_root(self.root), self.root)     # a plugin.json above a project: not under its templates/, still gated
        self.assertTrue(self.denied(self.pre('Write', file_path=os.path.join(self.root, '.claude-plugin', 'plugin.json'), content='{}')))

    def test_a_session_of_another_folder_still_cannot_write_a_gated_project_state(self):
        other = {'session_id': 's1', 'cwd': self.d, 'transcript_path': self.transcript}      # a plugin-development session: the project is an extra folder
        for ti in ({'command': 'echo x > proj/gate/project.json'}, {'command': 'echo {} > %s/gate/owner/w.json' % self.root},
                   {'command': '%s --root %s owner adopt --quote zz' % (GATE_PY, self.root)}):
            out = json.loads(gate_watch.pre_tool(dict(other, tool_name='Bash', tool_input=ti), {}))['hookSpecificOutput']
            self.assertEqual(out['permissionDecision'], 'deny', ti)
        out = json.loads(gate_watch.pre_tool(dict(other, tool_name='Write', tool_input={'file_path': os.path.join(self.root, 'gate', 'project.json'), 'content': '{}'}), {}))
        self.assertEqual(out['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertTrue('permissionDecision' not in json.loads(gate_watch.pre_tool(dict(other, tool_name='Bash', tool_input={'command': 'cp /s/x.3mf %s/bambulab/x.3mf' % self.root}), {}))['hookSpecificOutput'])
        self.assertEqual(gate_watch.pre_tool(dict(other, tool_name='Bash', tool_input={'command': 'echo x > notes.txt'}), {}), '')


class PreToolPrints(Watch):
    def test_stl_and_3mf_written_past_the_gate_observe_warns_enforce_denies(self):
        for cmd in ('cp /x/part.stl stl/part.stl', 'mv /x/new.3mf bambulab/Demo.3mf', "python3 -c \"open('x','w')\" > stl/a.stl"):
            self.assertTrue(self.warned(self.bash(cmd)), cmd)
        self.assertTrue(self.warned(self.pre('Write', file_path=os.path.join(self.root, 'stl', 'a.stl'), content='solid')))
        self.project('enforce')
        for cmd in ('cp /x/part.stl stl/part.stl', 'mv /x/new.3mf bambulab/Demo.3mf'):
            out = self.bash(cmd)
            self.assertTrue(self.denied(out), cmd); self.assertIn('gate.py export', out['permissionDecisionReason'])
        self.assertTrue(self.denied(self.pre('Write', file_path=os.path.join(self.root, 'stl', 'a.stl'), content='solid')))
        self.assertIsNone(self.bash('rm stl/old.stl'))      # a file that goes prints nothing wrong
        self.assertIsNone(self.bash('python3 bambulab/build_3mf.py'))   # the guarded writer: its own judge
        self.assertIsNone(self.bash('cp /x/part.stl /scratch/part.stl'))

    def test_a_fusion_script_that_writes_stl_files(self):
        script = 'em = design.exportManager\nopts = em.createSTLExportOptions(body, "/x/a.stl")\nem.execute(opts)'
        self.assertTrue(self.warned(self.pre(EXEC, object={'script': script})))
        self.project('enforce')
        self.assertTrue(self.denied(self.pre(EXEC, object={'script': script})))
        self.assertIsNone(self.pre(EXEC, object={'script': '# gate unit 3/9: fusion_export on demo/x\n' + script}))
        self.assertIsNone(self.pre(EXEC, object={'script': 'print(design.rootComponent.name)'}))
        path = os.path.join(self.d, 'edit.py')
        with open(path, 'w', encoding='utf-8') as f: f.write(script)
        self.assertTrue(self.denied(self.bash('python3 tools/fusion_mcp.py run %s --write' % path)))


class OwnerQuote(Watch):
    def owner(self, quote):
        return self.bash('%s owner param inserts.wall_target 4.0 --tag U --source "owner, in chat" --quote "%s"' % (GATE_PY, quote))

    def test_a_quote_counts_only_from_the_owner_own_turn(self):
        self.turns([self.human('four millimetres everywhere, please'),
                    {'type': 'assistant', 'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'I will set five millimetres'}]}},
                    self.human('subagent text: six millimetres', isSidechain=True),
                    dict(self.human('peer text: seven millimetres'), origin={'kind': 'peer'}),
                    dict(self.human('a summary: eight millimetres'), origin=None, isCompactSummary=True),
                    self.human('<system-reminder>nine millimetres</system-reminder> ok'),
                    self.human([{'type': 'tool_result', 'content': 'ten millimetres'}], toolUseResult={}),
                    self.human('eleven millimetres', isMeta=True)])
        self.assertIsNone(self.owner('four millimetres everywhere'))
        self.assertIsNone(self.owner('four  millimetres\neverywhere'))          # whitespace does not matter
        for words in ('five millimetres', 'six millimetres', 'seven millimetres', 'eight millimetres', 'nine millimetres',
                      'ten millimetres', 'eleven millimetres', 'x'):
            out = self.owner(words)
            self.assertTrue(self.denied(out), words); self.assertIn('does not appear in any of the owner', out['permissionDecisionReason'])

    def test_every_owner_command_needs_a_readable_quote(self):
        self.turns([self.human('yes, adopt it')])
        self.assertIsNone(self.bash('%s owner adopt --quote "yes, adopt it"' % GATE_PY))
        self.assertIsNone(self.bash('%s owner bundle gate/proposals/b.json --quote="yes, adopt it" --dry-run' % GATE_PY))
        self.assertTrue(self.denied(self.bash('%s owner adopt' % GATE_PY)))
        self.assertIsNone(self.bash('%s owner bundle gate/proposals/b.json --dry-run' % GATE_PY))          # a dry run writes nothing: no quote
        self.assertTrue(self.denied(self.bash('%s owner bundle gate/proposals/b.json' % GATE_PY)))
        self.assertTrue(self.denied(self.bash('%s owner bundle b.json --dry-run; %s owner adopt' % (GATE_PY, GATE_PY))))
        self.assertIsNone(self.bash('%s owner table pins 5 --value 16,20 --source "the stock box" --dry-run' % GATE_PY))     # one table row, shown only
        self.assertTrue(self.denied(self.bash('%s owner table pins 5 --value 16,20 --source "the stock box"' % GATE_PY)))
        self.assertIsNone(self.bash('%s owner table pins 5 --value 16,20 --source "the stock box" --quote "yes, adopt it"' % GATE_PY))
        self.assertTrue(self.denied(self.bash('bash -c "%s owner adopt --quote yes"' % GATE_PY)))
        self.assertTrue(self.denied(self.bash("python3 -c \"import subprocess; subprocess.run(['gate.py', 'owner', 'adopt'])\"")))
        gone = dict(self.data(tool_name='Bash', tool_input={'command': '%s owner adopt --quote "yes, adopt it"' % GATE_PY}),
                    transcript_path=os.path.join(self.d, 'none.jsonl'))
        self.assertTrue(self.denied(json.loads(gate_watch.pre_tool(gone, {}))['hookSpecificOutput']))      # no transcript: no quote holds
        self.assertIsNone(self.bash('grep -n owner gate/tools/gate.py'))       # a mention is no call


class ThroughTheHook(Watch):
    def run_hook(self, event, data):
        t = time.time()
        r = subprocess.run([sys.executable, HOOK, event], input=json.dumps(data), capture_output=True, text=True, timeout=30, cwd=self.d, env=clean_env(os.path.join(self.d, 'roots.json')))
        return r, time.time() - t

    def test_each_event_answers_in_json_and_exit_0(self):
        r, took = self.run_hook('pre-tool', self.data(tool_name='Bash', tool_input={'command': 'echo x > gate/project.json'}))
        self.assertEqual((r.returncode, r.stderr), (0, ''))
        self.assertEqual(json.loads(r.stdout)['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertLess(took, 10)                           # the hook's own budget is two seconds; this only catches a hang
        r, _ = self.run_hook('post-tool', self.data(tool_name=EXEC, tool_input={'object': {'script': 'x'}}))
        self.assertEqual(r.returncode, 0); self.assertIn('PostToolUse', r.stdout)
        r, _ = self.run_hook('stop', self.data())
        self.assertEqual((r.returncode, json.loads(r.stdout)['decision']), (0, 'block'))
        r, _ = self.run_hook('subagent-start', self.data(agent_id='a1', agent_type='general'))
        ctx = json.loads(r.stdout)['hookSpecificOutput']
        self.assertEqual(ctx['hookEventName'], 'SubagentStart'); self.assertIn('run --changed --for main', ctx['additionalContext'])

    def test_bad_input_never_stops_a_call(self):
        for event in ('pre-tool', 'post-tool', 'stop', 'subagent-stop', 'subagent-start'):
            for stdin in ('', 'not json', '[]', '{"cwd": 5}', json.dumps({'cwd': self.d, 'tool_name': 'Bash', 'tool_input': {'command': 'echo x > a'}})):
                r = subprocess.run([sys.executable, HOOK, event], input=stdin, capture_output=True, text=True, timeout=30, cwd=self.d, env=clean_env(os.path.join(self.d, 'roots.json')))
                self.assertEqual((r.returncode, r.stdout, r.stderr), (0, '', ''), (event, stdin))

    def test_the_live_checks_are_quick(self):
        for _ in range(50):
            self.mark()
        t = time.time()
        for _ in range(20):
            gate_watch.pre_tool(self.data(tool_name='Bash', tool_input={'command': 'cat gate/project.json && ls'}), {})
            gate_watch.stop(self.data(), {})
        self.assertLess((time.time() - t) / 20, 0.05)       # tens of milliseconds at most per pair, here far less


class ShellWrites(Watch):
    """What a Bash command writes, read the way bash reads it: one command per line, bodies of if / for, substitutions,
    comments, folder targets, git, variables."""

    def test_every_command_of_a_multi_line_call_is_read(self):
        for cmd in ('echo hi\ncp /s/w.json gate/owner/w.json', 'ls\nmv /s/p.json gate/project.json', 'if true; then cp /s/w.json gate/owner/w.json; fi',
                    'for f in 1; do cp /s/w.json gate/owner/w.json; done', 'echo `cp /s/w.json gate/owner/w.json`', 'echo "$(rm gate/stamps/demo/x.json)"',
                    'echo ok \\\n  && rm -rf gate/owner', 'find /s -name x.json -exec cp {} gate/owner/ \;', 'find gate/owner -delete',
                    'ls /s | xargs -I{} cp {} gate/owner/', 'G=gate; echo x > $G/project.json', 'echo x > $UNKNOWN/project.json',
                    'cp -R /s/owner gate', 'rsync -a /s/ gate/', 'cp -r /s/gate .', 'mkdir -p /s && cd gate && cp /s/a project.json'):
            out = self.bash(cmd)
            self.assertTrue(self.denied(out), cmd); self.assertIn('gate', out['permissionDecisionReason'], cmd)

    def test_git_that_rolls_the_state_back(self):
        for cmd in ('git checkout HEAD~1 -- gate', 'git restore --source HEAD~1 gate', 'git checkout .', 'git reset --hard', 'git stash',
                    'git clean -fd', 'git checkout -- gate/owner/params.json', 'git -C %s checkout -- gate' % self.root):
            self.assertTrue(self.denied(self.bash(cmd)), cmd)
        for cmd in ('git stash list', 'git restore --staged gate/project.json', 'git reset HEAD gate/project.json', 'git checkout main', 'git clean -n',
                    'git log -- gate', 'git diff HEAD~1 -- gate/project.json', 'git checkout -b topic'):
            self.assertIsNone(self.bash(cmd), cmd)

    def test_code_that_writes_the_state_however_it_spells_the_path(self):
        for cmd in ("python3 -c \"import os,json; json.dump({}, open(os.path.join('gate','project.json'),'w'))\"",
                    "python3 -c \"open('gate' + '/' + 'owner/w.json', 'w').write('x')\"",
                    "python3 -c \"import pathlib; (pathlib.Path('gate') / 'project.json').write_text('{}')\"",
                    "echo {} | python3 -c \"import sys;open(sys.argv[1],'w').write(sys.stdin.read())\" gate/project.json"):
            self.assertTrue(self.denied(self.bash(cmd)), cmd)

    def test_reading_the_state_is_never_stopped(self):
        for cmd in ("python3 -c \"import json,sys; json.dump(json.load(open('gate/project.json')), sys.stdout, indent=1)\"",
                    "python3 -c \"import sys; sys.stdout.write(open('gate/project.json').read())\"",
                    "perl -Mstrict -ne 'print if /mode/' gate/project.json", '%s owner --help' % GATE_PY, '%s owner bundle -h' % GATE_PY,
                    'echo "# not a comment" > /s/notes.txt', 'curl -s http://example.invalid/#frag > /s/page.html', 'unzip -l bambulab/Demo.3mf',
                    'unzip -o bambulab/Demo.3mf -d /s/x', 'cat gate/owner/params.json | python3 -m json.tool'):
            self.assertIsNone(self.bash(cmd), cmd)

    def test_printed_files_written_by_other_tools(self):
        self.project('enforce')
        for cmd in ('/Applications/BambuStudio.app/Contents/MacOS/BambuStudio --export-3mf bambulab/x.3mf /s/a.stl', 'zip -r bambulab/x.3mf /s/x',
                    'cp -r /s/stls/ stl/', 'curl -T /s/x.3mf ftps://printer.local:990/', 'unzip -o /s/x.zip -d stl'):
            self.assertTrue(self.denied(self.bash(cmd)), cmd)
        self.assertTrue(self.denied(self.bash('git checkout -- bambulab/Demo.3mf')))
        self.assertIn('rebuilt by the build script', self.bash('git checkout -- bambulab/Demo.3mf')['permissionDecisionReason'])

    def test_switching_the_hooks_off(self):
        for cmd in ('claude plugin disable fusion-bambulab@fusion-bambulab', 'claude plugin uninstall fusion-bambulab', 'claude plugin marketplace remove x',
                    "claude -p --settings '{\"disableAllHooks\": true}' --dangerously-skip-permissions 'go'", 'claude -p --setting-sources project x'):
            self.assertTrue(self.denied(self.bash(cmd)), cmd)
        for path in ('~/.claude/plugins/cache/fusion-bambulab/fusion-bambulab/0.3.0/tools/gate/gate_watch.py', '~/.claude/plugins/installed_plugins.json', '~/.claude.json'):
            self.assertTrue(self.denied(self.pre('Edit', file_path=os.path.expanduser(path), old_string='a', new_string='b')), path)
        self.assertTrue(self.denied(self.bash("sed -i '' 's/^def pre_tool/def _x/' ~/.claude/plugins/cache/fusion-bambulab/fusion-bambulab/0.3.0/tools/gate/gate_watch.py")))
        for cmd in ('claude plugin validate . --strict', 'claude plugin list', 'claude --version'):
            self.assertIsNone(self.bash(cmd), cmd)

    def test_the_owner_switch_turns_denials_into_warnings(self):
        gate_common.write_json(gate_common.gate_path(self.root, 'project.json'), {'mode': 'observe', 'targets': {'main': {'nozzle': 0.4}}, 'hooks': {'pre_tool': 'warn'}})
        out = self.bash('echo {} > gate/owner/w.json')
        self.assertTrue(self.warned(out)); self.assertIn('the owner switched denials to warnings', out['additionalContext'])


class Marks(Watch):
    def marks(self):
        return gate_common.read_jsonl(gate_common.gate_path(self.root, 'run', 'dirty.jsonl'))

    def test_the_mark_goes_in_before_the_call(self):
        self.pre(EXEC, object={'script': 'x = 1'})
        self.assertEqual([m['when'] for m in self.marks()], ['pre'])           # a call that hangs or fails half way is marked too
        gate_watch.pre_tool(self.data(tool_name=EXEC, tool_input={'object': {'script': 'x'}}, tool_use_id='t1'), {})
        out = gate_watch.post_tool(self.data(tool_name=EXEC, tool_input={'object': {'script': 'x'}}, tool_use_id='t1', hook_event_name='PostToolUseFailure'), {})
        self.assertEqual(json.loads(out)['hookSpecificOutput']['hookEventName'], 'PostToolUseFailure')
        self.assertEqual(len(self.marks()), 2)                                  # the post hook does not mark the same call twice

    def test_a_script_file_that_calls_fusion_and_the_port_anywhere(self):
        path = os.path.join(self.root, 'tool.py')
        with open(path, 'w') as f: f.write("import urllib.request\nurllib.request.urlopen('http://127.0.0.1:' + str(27182) + '/mcp', data=b'fusion_mcp_execute')\n")
        self.assertTrue(gate_watch.changes_fusion('Bash', {'command': 'python3 tool.py'}, self.root))
        self.assertTrue(gate_watch.changes_fusion('Bash', {'command': "python3 -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:'+str(27182)+'/mcp')\""}, self.root))
        client = os.path.join(self.root, 'fusion_mcp.py')
        with open(client, 'w') as f: f.write("PORT = 27182\n")
        self.assertFalse(gate_watch.changes_fusion('Bash', {'command': 'python3 fusion_mcp.py run probe.py'}, self.root))   # the client: --write decides

    def test_a_session_whose_folder_is_not_gated_marks_the_roots_it_knows(self):
        index = os.path.join(self.d, 'roots.json'); env = {gate_watch.INDEX_ENV: index}
        gate_watch.pre_tool(self.data(tool_name='Bash', tool_input={'command': 'ls'}), env)      # a session of the project: the root goes on the list
        self.assertEqual(gate_watch.known_roots(env), [self.root])
        other = {'session_id': 's9', 'cwd': self.d, 'tool_name': EXEC, 'tool_input': {'object': {'script': 'x'}}}
        self.assertNotEqual(gate_watch.post_tool(other, env), '')
        self.assertEqual(json.loads(gate_watch.stop({'session_id': 's9', 'cwd': self.d}, env))['decision'], 'block')
        self.assertEqual(gate_watch.stop({'session_id': 's9', 'cwd': self.d}, {}), '')          # no list, no root: nothing to say


class WhatCountsAsChecked(Watch):
    def test_offline_no_scan_audit_another_session_or_an_earlier_start_check_nothing(self):
        self.mark(); time.sleep(0.01)
        for flags in ({'offline': True}, {'no_scan': True}, {'audit': True}):
            self.checked(0, **flags)
            self.assertEqual(self.stop(stop_hook_active=False)['decision'], 'block', flags)
        self.checked(0, session='another session')
        self.assertEqual(self.stop(stop_hook_active=False)['decision'], 'block')
        self.checked(0, started=time.time() - 3600)                            # it started before the change
        self.assertEqual(self.stop(stop_hook_active=False)['decision'], 'block')
        self.checked(0, session='s1')
        self.assertIsNone(self.stop(stop_hook_active=False))

    def test_an_old_vendored_gate_is_never_blocked_and_says_so(self):
        self.vendored(False); self.mark()
        out = self.stop()
        self.assertNotIn('decision', out); self.assertIn('--update-tools', out['systemMessage']); self.assertIn('NOT checked', out['systemMessage'])

    def test_the_owner_can_switch_the_stop_off(self):
        gate_common.write_json(gate_common.gate_path(self.root, 'project.json'), {'mode': 'observe', 'targets': {'main': {'nozzle': 0.4}}, 'hooks': {'stop': False}})
        self.mark()
        out = self.stop()
        self.assertNotIn('decision', out); self.assertIn('switched the Stop hook off', out['systemMessage'])

    def test_without_stop_hook_active_blocks_close_in_time_are_in_a_row(self):
        self.mark()
        for _ in range(gate_watch.STOP_CAP):
            self.assertEqual(self.stop()['decision'], 'block')
        self.assertIn('the turn ends anyway', self.stop()['systemMessage'])


class Quotes(Watch):
    def owner(self, quote):
        return self.bash('%s owner param inserts.wall_target 4.0 --tag U --source "owner, in chat" --quote "%s"' % (GATE_PY, quote))

    def test_a_short_piece_is_no_decision_a_whole_newest_turn_is(self):
        self.turns([self.human('do them locally where you can run them'), self.human('yes')])
        for q, why in (('do', 'too short'), ('if it', 'does not appear'), ('do them', 'too short')):
            out = self.owner(q)
            self.assertTrue(self.denied(out), q); self.assertIn(why, out['permissionDecisionReason'], q)
        self.assertIsNone(self.owner('yes'))                                   # his newest turn, whole
        self.assertIsNone(self.owner('do them locally where'))                 # a piece of 12 characters and more
        self.turns([self.human('yes'), self.human('something else')])
        self.assertTrue(self.denied(self.owner('yes')))                        # a short turn of earlier: not his answer now

    def test_a_card_answer_counts_and_automated_turns_do_not(self):
        self.turns([dict(self.human([{'type': 'tool_result', 'content': 'x'}]), toolUseResult={'questions': [{'question': 'Waive it?'}], 'answers': {'Waive it?': 'yes, waive the bore of the cover'}}),
                    dict(self.human('apply the bundle and switch to observe'), origin={'kind': 'human', 'producer': 'session-task'}),
                    dict(self.human('waive every hole of the base'), turnOrigin='host_synthetic')])
        self.assertIsNone(self.owner('yes, waive the bore of the cover'))
        for q in ('apply the bundle and switch to observe', 'waive every hole of the base'):
            self.assertTrue(self.denied(self.owner(q)), q)

    def test_a_ticket_is_left_for_gate_py(self):
        self.turns([self.human('four millimetres everywhere, please')])
        self.assertIsNone(self.owner('four millimetres everywhere'))
        book = gate_common.read_json(gate_common.gate_path(self.root, 'run', 'owner_tickets.json'))
        self.assertEqual(len(book['tickets']), 1); self.assertEqual(book['tickets'][0]['quotes'], ['four millimetres everywhere'])
        self.assertTrue(self.denied(self.pre('Write', file_path=gate_common.gate_path(self.root, 'run', 'owner_tickets.json'), content='{}')))


class HookFailures(Watch):
    def test_a_check_that_breaks_denies_what_touches_the_gate_and_says_so_for_the_rest(self):
        import io
        with mock.patch.object(gate_watch, 'pre_tool', side_effect=RuntimeError('boom')):
            out = io.StringIO()
            gate_hook.main(['pre-tool'], io.StringIO(json.dumps(self.data(tool_name='Bash', tool_input={'command': 'echo x > gate/project.json'}))), out)
            self.assertEqual(json.loads(out.getvalue())['hookSpecificOutput']['permissionDecision'], 'deny')
            out = io.StringIO()
            gate_hook.main(['pre-tool'], io.StringIO(json.dumps(self.data(tool_name='Bash', tool_input={'command': 'ls /s'}))), out)
            self.assertIn('nothing of this call was checked', json.loads(out.getvalue())['systemMessage'])
        with mock.patch.object(gate_watch, 'stop', side_effect=RuntimeError('boom')):
            out = io.StringIO()
            gate_hook.main(['stop'], io.StringIO(json.dumps(self.data())), out)
            self.assertIn('the stop hook failed', json.loads(out.getvalue())['systemMessage'])


class IntegrationReview(Watch):
    """The parser and hook cases the 0.3.0 integration review found."""

    def test_a_file_descriptor_in_front_of_a_redirection_hides_no_target(self):
        self.project('enforce')
        for cmd in ('cp /x/w.json gate/owner/support_baseline.json', 'cp /x/w.json gate/project.json', 'ln -sf /x/w.json gate/project.json',
                    'install -m 644 /x/w.json gate/project.json', 'rsync /x/w.json gate/project.json', 'cp /x/part.stl stl/part.stl',
                    'mv /x/new.3mf bambulab/Demo.3mf'):
            for tail in ('', ' 2>/dev/null', ' 2>&1', ' 2> /dev/null', ' >/dev/null 2>&1', ' 3>/dev/null'):
                self.assertTrue(self.denied(self.bash(cmd + tail)), cmd + tail)
        self.assertIsNone(self.bash('ls gate 2>/dev/null'))

    def test_the_gate_own_json_writers_are_writes(self):
        pre = "import sys; sys.path.insert(0, 'gate/tools'); import gate_common as g; "
        for code in ("g.write_json('gate/owner/support_baseline.json', {})", "g.write_json('gate/project.json', {'mode': 'observe'})",
                     "g.append_jsonl('gate/owner/events.jsonl', {})", "g.write_jsonl('gate/owner/events.jsonl', [])"):
            self.assertTrue(self.denied(self.bash('python3 -c "%s%s"' % (pre, code))), code)
            self.assertTrue(self.denied(self.bash("python3 - <<'EOF'\n%s\n%s\nEOF" % (pre.replace('; ', '\n'), code))), code)

    def test_the_slice_record_and_the_bed_faces_are_the_gate_state(self):
        for rel in ('gate/run/slice/main.json', 'gate/run/bed_faces.json'):
            self.assertTrue(self.denied(self.pre('Write', file_path=os.path.join(self.root, rel), content='{}')), rel)
            self.assertTrue(self.denied(self.pre('Edit', file_path=os.path.join(self.root, rel), old_string='9', new_string='4')), rel)
            self.assertTrue(self.denied(self.bash('cp /x/s.json %s' % rel)), rel)
        self.assertTrue(self.denied(self.bash("python3 -c \"import json; json.dump({}, open('gate/run/slice/main.json', 'w'))\"")))

    def test_a_writing_fusion_script_chained_with_a_gate_command_is_a_mark(self):
        path = os.path.join(self.root, 'edit_part.py')
        with open(path, 'w') as f: f.write("import urllib.request\nurllib.request.urlopen('http://127.0.0.1:27182/mcp', data=b'fusion_mcp_execute')\n")
        for cmd in ('python3 gate/tools/gate.py verify && python3 edit_part.py', 'python3 gate/tools/gate.py status; python3 edit_part.py',
                    'python3 edit_part.py; python3 gate/tools/gate.py slice --changed'):
            self.assertTrue(gate_watch.changes_fusion('Bash', {'command': cmd}, self.root), cmd)
        self.assertFalse(gate_watch.changes_fusion('Bash', {'command': 'python3 gate/tools/gate.py run --changed --for main'}, self.root))

    def test_a_denial_names_the_root_and_a_scratch_copy_stays_off_the_list(self):
        scratch = os.path.join(self.d, 'scratch')
        gate_common.write_json(os.path.join(scratch, 'gate', 'project.json'), {'mode': 'observe'})
        index = os.path.join(self.d, 'roots.json'); env = {gate_watch.INDEX_ENV: index}
        out = json.loads(gate_watch.pre_tool(self.data(tool_name='Bash', tool_input={'command': 'cp gate/layout.json gate/project.json %s/gate/' % scratch}), env))
        self.assertIn(os.path.join(scratch, 'gate', 'layout.json'), out['hookSpecificOutput']['permissionDecisionReason'])
        self.assertEqual(gate_watch.known_roots(env), [self.root])       # the scratch copy is not on the list

    def test_sources_and_options_are_no_targets_and_targets_are_not_missed(self):
        s = os.path.join(self.d, 'scratch'); os.makedirs(s)
        for cmd in ('cd %s && ln -s %s/gate/project.json' % (s, self.root), 'touch -r gate/project.json %s/stamp' % s,
                    "python3 -c \"import shutil; shutil.copy('gate/project.json', '%s/p.json')\"" % s,
                    "python3 -c \"import json; json.dump(json.load(open('gate/layout.json')), open('%s/l.json', 'w'))\"" % s,
                    "python3 -c \"open('%s/B/gate/project.json', 'w').write('x')\"" % s):
            out = self.bash(cmd)
            self.assertFalse(self.denied(out), cmd)
        for cmd in ('mv -t %s gate/owner/waivers.json' % s, 'tar -C gate/owner -xf /x/a.tar', 'tar -xf /x/a.tar -C gate/owner', 'tar xf /x/a.tar -C gate/owner'):
            self.assertTrue(self.denied(self.bash(cmd)), cmd)
        self.assertTrue(self.denied(self.bash("python3 -c \"import shutil; shutil.copy('/x/p.json', 'gate/project.json')\"")))

    def test_a_session_whose_folder_is_not_gated_keeps_its_own_home_and_its_own_exports(self):
        index = os.path.join(self.d, 'roots.json'); env = {gate_watch.INDEX_ENV: index}
        self.project('enforce')
        gate_watch.pre_tool(self.data(tool_name='Bash', tool_input={'command': 'ls'}), env)      # the project's session: the root goes on the list
        other = {'session_id': 's9', 'cwd': self.d, 'transcript_path': self.transcript}
        script = 'em = design.exportManager\nopts = em.createSTLExportOptions(body, "/x/a.stl")\nem.execute(opts)'
        out = json.loads(gate_watch.pre_tool(dict(other, tool_name=EXEC, tool_input={'object': {'script': script}}), env))['hookSpecificOutput']
        self.assertNotIn('permissionDecision', out)                      # a warning that names the project, never another project's enforce
        self.assertIn(self.root, out['additionalContext'])
        block = json.loads(gate_watch.stop({'session_id': 's9', 'cwd': self.d}, env))
        self.assertIn('cd %s && python3 gate/tools/gate.py run --changed --for main' % self.root, block['reason'])
        home = os.path.expanduser('~')
        for ti, tool in (({'file_path': os.path.join(home, '.claude', 'settings.json'), 'content': '{"permissions": {"allow": ["Bash(ls)"]}}'}, 'Write'),
                         ({'file_path': os.path.join(home, '.claude', 'plugins', 'data', 'other', 's.json'), 'content': '{}'}, 'Write'),
                         ({'command': 'claude plugin disable some-other'}, 'Bash')):
            self.assertEqual(gate_watch.pre_tool(dict(other, tool_name=tool, tool_input=ti), env), '', ti)
        self.assertIsNone(self.bash('claude plugin disable some-other@market'))            # the project's own session: another plugin is no business either
        self.assertTrue(self.denied(self.bash('claude plugin disable %s' % gate_watch.OWN_NAME)))
        self.assertTrue(self.denied(self.pre('Write', file_path=os.path.join(home, '.claude', 'plugins', 'data', 'fusion-bambulab-gate', 'roots.json'), content='{}')))


if __name__ == '__main__':
    unittest.main()
