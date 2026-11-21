"""gate.py owner bundle: a prepared proposal of param, target, waiver, triage and mode entries applied in one call under
the owner's quote, all of it or nothing, and a dry run that shows the exact change of every file and writes nothing.
No Fusion, no Bambu Studio; every name and number is invented."""
import contextlib, io, json, os, shutil, sys, tempfile, time, unittest
from unittest import mock
for _k in ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID'): os.environ.pop(_k, None)      # gate.py owner from an agent session needs the hook's ticket: a test is no agent session
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
import gate, gate_common, gate_watch

TARGET = {'nozzle': 0.4, 'line_width': 0.45, 'material': 'PETG', 'layer_height': 0.2, 'xy_hole_compensation': 0.1}
ASK = {'permissions': {'ask': ['Edit(/gate/owner/**)', 'Write(/gate/owner/**)', 'Bash(python3 gate/tools/gate.py owner *)']}}


class Base(unittest.TestCase):
    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.d)
        gate_common.write_json(gate_common.gate_path(self.d, 'project.json'), {'mode': 'observe', 'not_printed': [], 'targets': {'main': TARGET}})
        gate_common.write_json(gate_common.gate_path(self.d, 'layout.json'), {'documents': [{'key': 'demo', 'name': 'Demo', 'stl_dir': 'stl'}],
                                                                           'projects': [{'key': 'main', 'file': 'bambulab/Demo.3mf', 'stl_dir': 'stl'}]})
        gate_common.write_json(os.path.join(self.d, '.claude', 'settings.json'), ASK)
        self.entries = [
            {'kind': 'param', 'name': 'inserts.wall_target', 'value': 3.8, 'tag': 'U', 'source': 'owner, bundle', 'why': 'every pocket of the first audit has 3.8 to 4.0'},
            {'kind': 'target', 'project': 'main', 'set': {'xy_hole_compensation': 0.0, 'print': {'enable_support': True, 'support_type': 'tree(auto)'}}},
            {'kind': 'target', 'project': 'main', 'object': 'sleeve', 'set': {'wall_generator': 'arachne'}},
            {'kind': 'mode', 'mode': 'enforce'}]

    def proposal(self, entries=None, name='bundle.json', md=None):
        path = os.path.join(self.d, 'gate', 'proposals', name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        text = json.dumps({'bundle': 1, 'title': 'first bundle', 'entries': self.entries if entries is None else entries}, indent=1)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(md.replace('BUNDLE', text) if md else text)
        return path

    def gate(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = gate.main(['--root', self.d] + list(argv))
        return code, out.getvalue(), err.getvalue()

    def files(self):
        out = {}
        for folder, _, names in os.walk(os.path.join(self.d, 'gate')):
            for n in names:
                if '/proposals' not in folder and '/run' not in folder and '/reports' not in folder:
                    with open(os.path.join(folder, n), 'rb') as f: out[os.path.join(folder, n)] = f.read()
        return out



class Bundle(Base):
    def test_the_dry_run_of_enforce_warns_that_every_save_of_a_file_with_unstamped_objects_is_refused(self):
        gate_common.write_json(gate_common.gate_path(self.d, 'run', 'saves.json'), {'projects': {'bambulab/Demo.3mf': {'unchecked': ['bracket', 'cover']}}})
        code, out, err = self.gate('owner', 'bundle', self.proposal([{'kind': 'mode', 'mode': 'enforce'}]), '--dry-run')
        self.assertEqual(code, 0, err)
        self.assertIn('WARNING bambulab/Demo.3mf: 2 object(s) have no stamp', out); self.assertIn('every save of this file', out)
        self.assertNotIn('still let through', out)

    def test_a_dry_run_shows_every_change_and_writes_nothing(self):
        before = self.files()
        os.remove(os.path.join(self.d, '.claude', 'settings.json'))         # no prompt is needed for it, and no quote
        code, out, err = self.gate('owner', 'bundle', self.proposal(), '--dry-run')
        self.assertEqual((code, err), (0, ''), out)
        self.assertEqual(self.files(), before)
        self.assertFalse(os.path.exists(gate_common.gate_path(self.d, 'run', 'digest.json')))      # not even the kept digest is built again
        self.assertIn('DRY RUN of gate/proposals/bundle.json: 4 entries - first bundle', out)
        self.assertIn('1. param: inserts.wall_target = 3.8', out); self.assertIn('why: every pocket of the first audit has 3.8 to 4.0', out)
        self.assertIn('4. mode: mode: observe -> enforce', out)
        self.assertIn('- "mode": "observe",', out); self.assertIn('+ "mode": "enforce",', out)
        self.assertIn('+   "xy_hole_compensation": 0.0', out); self.assertIn('"wall_generator": "arachne"', out)
        self.assertIn('DRY RUN: nothing was written. 3 file(s) would change: gate/owner/bundles.json, gate/owner/params.json, gate/project.json', out)
        self.assertIn('"quote": "<the owner\'s words: --quote at the apply>"', out)     # the record the apply writes is shown too, never "(dry run)"

    def test_a_dry_run_leaves_no_staged_function_behind(self):
        for name in ('gate_params', 'gate_report'):
            if name in sys.modules: self.addCleanup(sys.modules.__setitem__, name, sys.modules[name])
            sys.modules.pop(name, None)                                         # first imported inside the stage
        code, out, err = self.gate('owner', 'bundle', self.proposal(), '--dry-run')
        self.assertEqual(code, 0, err)
        import gate_params
        self.assertIs(gate_params.read_json, gate_common.read_json)
        self.assertEqual(gate_params.load_overrides(self.d), {})               # nothing of the dry run is read back afterwards

    def test_a_proposal_named_like_an_option_and_abbreviated_options_are_refused(self):
        os.rename(self.proposal(), os.path.join(self.d, '--dry-run'))
        cwd = os.getcwd(); os.chdir(self.d); self.addCleanup(os.chdir, cwd)
        code, out, err = self.gate('owner', 'bundle', '--quote', 'yes', '--', '--dry-run')
        self.assertEqual(code, 4); self.assertIn('may not start with "-"', err)
        self.assertEqual(gate_common.read_json(gate_common.gate_path(self.d, 'project.json'))['mode'], 'observe')
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as c: gate.main(['--root', self.d, 'owner', 'adopt', '--quo', 'yes'])
        self.assertEqual(c.exception.code, 4)

    def test_applied_in_one_call_with_the_quote_and_kept_on_record(self):
        code, out, err = self.gate('owner', 'bundle', self.proposal(), '--quote', 'yes, all of it')
        self.assertEqual((code, err), (0, ''), out)
        project = gate_common.read_json(gate_common.gate_path(self.d, 'project.json'))
        self.assertEqual(project['mode'], 'enforce')
        self.assertEqual(project['targets']['main']['xy_hole_compensation'], 0.0)
        self.assertEqual(project['targets']['main']['print'], {'enable_support': True, 'support_type': 'tree(auto)'})
        self.assertEqual(project['targets']['main']['objects'], {'sleeve': {'wall_generator': 'arachne'}})
        params = gate_common.read_json(gate_common.gate_path(self.d, 'owner', 'params.json'))
        self.assertEqual((params['inserts.wall_target']['value'], params['inserts.wall_target']['tag'], params['inserts.wall_target']['quote']), (3.8, 'U', 'yes, all of it'))
        book = gate_common.read_json(gate_common.gate_path(self.d, 'owner', 'bundles.json'))['bundles']
        self.assertEqual([(b['id'], b['quote'], b['proposal'], len(b['entries'])) for b in book], [('B-1', 'yes, all of it', 'gate/proposals/bundle.json', 4)])
        self.assertNotIn('why', book[0]['entries'][0]); self.assertEqual(len(book[0]['sha256']), 64)
        self.assertIn('bundle applied: 3 file(s) written', out)

    def test_one_bad_entry_and_nothing_is_written(self):
        before = self.files()
        for bad, word in (({'kind': 'param', 'name': 'inserts.wall_targt', 'value': 3.8, 'tag': 'U', 'source': 's'}, 'entry 5 (param) refused'),
                          ({'kind': 'param', 'name': 'inserts.wall_target', 'value': 3.0, 'tag': 'D', 'source': 's'}, 'looser than the default'),
                          ({'kind': 'target', 'project': 'other', 'set': {'nozzle': 0.6}}, 'other is no .3mf project of gate/layout.json'),
                          ({'kind': 'target', 'project': 'main', 'set': {'nozle': 0.6}}, 'unknown key "nozle"'),
                          ({'kind': 'decide', 'item': 'T-0001', 'decision': 'tolerate'}, 'there is no triage list'),
                          ({'kind': 'waive', 'finding': 'F-000000', 'reason': 'the bore is fixed by the bought part'}, 'entry 5 (waive) refused')):
            code, out, err = self.gate('owner', 'bundle', self.proposal(self.entries + [bad]), '--quote', 'yes')
            self.assertEqual(code, 4, (bad, out)); self.assertIn(word, err); self.assertIn('Nothing of the bundle was written', err)
            self.assertEqual(self.files(), before, bad)

    def test_the_file_itself_is_checked_before_anything_runs(self):
        for entries, word in (([], 'at least one entry'), ([{'kind': 'shell', 'cmd': 'rm'}], '"kind" must be one of'),
                              ([{'kind': 'mode', 'mode': 'strict'}], '"mode" must be "observe" or "enforce"'),
                              ([{'kind': 'param', 'name': 'x'}], 'missing value, tag, source'),
                              ([{'kind': 'adopt', 'quote': 'typed by an agent'}], 'unknown quote')):
            code, _, err = self.gate('owner', 'bundle', self.proposal(entries), '--quote', 'yes')
            self.assertEqual(code, 4, entries); self.assertIn(word, err)
        code, _, err = self.gate('owner', 'bundle', self.proposal(), '--quote', ' ')
        self.assertEqual(code, 4); self.assertIn("owner's own words", err)
        os.remove(os.path.join(self.d, '.claude', 'settings.json'))
        code, _, err = self.gate('owner', 'bundle', self.proposal(), '--quote', 'yes')
        self.assertEqual(code, 4); self.assertIn('Missing in .claude/settings.json', err)

    def test_a_markdown_proposal_holds_it_in_one_fenced_block(self):
        md = '# Bundle\n\nWhat changes and why.\n\n```json gate-bundle\nBUNDLE\n```\n\n```json\n{"other": 1}\n```\n'
        code, out, err = self.gate('owner', 'bundle', self.proposal(name='b.md', md=md), '--dry-run')
        self.assertEqual((code, err), (0, ''), out)
        code, _, err = self.gate('owner', 'bundle', self.proposal(name='c.md', md='```json\nBUNDLE\n```\n```json\n{}\n```\n'), '--dry-run')
        self.assertEqual(code, 4); self.assertIn('exactly one fenced block', err)
        code, out, err = self.gate('owner', 'bundle', self.proposal(name='d.md', md='text\n```json\nBUNDLE\n```\n'), '--quote', 'yes')
        self.assertEqual((code, err), (0, ''))

    def test_a_target_entry_can_take_a_key_back_out(self):
        self.gate('owner', 'bundle', self.proposal(), '--quote', 'yes')
        code, out, err = self.gate('owner', 'bundle', self.proposal([{'kind': 'target', 'project': 'main', 'object': 'sleeve', 'unset': ['wall_generator'], 'set': {}}],
                                                                    name='two.json'), '--quote', 'yes')
        self.assertEqual((code, err), (0, ''))
        self.assertNotIn('objects', gate_common.read_json(gate_common.gate_path(self.d, 'project.json'))['targets']['main'])


class FromAnAgentSession(Base):
    """An owner command run from an agent session goes through only with the one-time ticket the PreToolUse hook leaves
    when the quote is the owner's own answer to the dry run he saw."""

    def setUp(self):
        super().setUp()
        p = mock.patch.dict(os.environ, {'CLAUDECODE': '1'}); p.start(); self.addCleanup(p.stop)
        self.transcript = os.path.join(self.d, 'session.jsonl'); open(self.transcript, 'w').close()
        self.n = 0

    def turn(self, text, at):
        self.n += 1
        with open(self.transcript, 'a', encoding='utf-8') as f:
            f.write(json.dumps({'type': 'user', 'origin': {'kind': 'human'}, 'turnOrigin': 'human', 'isSidechain': False, 'uuid': 'u-%d' % self.n,
                                'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(at)), 'message': {'role': 'user', 'content': text}}) + '\n')

    def hook(self, cmd):
        out = gate_watch.pre_tool({'session_id': 's1', 'cwd': self.d, 'transcript_path': self.transcript, 'tool_name': 'Bash', 'tool_input': {'command': cmd}}, {})
        return json.loads(out)['hookSpecificOutput'] if out else None

    def cmd(self, path, *more):
        return 'python3 gate/tools/gate.py --root %s owner bundle %s %s' % (self.d, path, ' '.join(more))

    def test_no_ticket_no_apply(self):
        code, _, err = self.gate('owner', 'bundle', self.proposal(), '--quote', 'yes, apply all of it')
        self.assertEqual(code, 4); self.assertIn('no ticket of the print gate', err)
        self.assertEqual(gate_common.read_json(gate_common.gate_path(self.d, 'project.json'))['mode'], 'observe')
        code, out, err = self.gate('owner', 'bundle', self.proposal(), '--dry-run')        # a dry run needs none
        self.assertEqual((code, err), (0, ''))

    def test_the_quote_must_answer_the_dry_run_and_the_ticket_is_used_once(self):
        path = self.proposal(); q = "--quote 'yes, apply all of it'"
        self.turn('yes, apply all of it', time.time() - 60)                   # said before any dry run was shown
        out = self.hook(self.cmd(path, q))
        self.assertEqual(out['permissionDecision'], 'deny'); self.assertIn('--dry-run', out['permissionDecisionReason'])
        self.assertIsNone(self.hook(self.cmd(path, '--dry-run')))              # the dry run the owner sees
        out = self.hook(self.cmd(path, q))
        self.assertEqual(out['permissionDecision'], 'deny'); self.assertIn('before the dry run', out['permissionDecisionReason'])
        self.turn('yes, apply all of it', time.time() + 1)                    # his answer to it
        self.assertIsNone(self.hook(self.cmd(path, q)))
        code, out, err = self.gate('owner', 'bundle', path, '--quote', 'yes, apply all of it')
        self.assertEqual((code, err), (0, ''), out)
        book = gate_common.read_json(gate_common.gate_path(self.d, 'owner', 'bundles.json'))['bundles']
        self.assertEqual(book[0]['turn']['uuid'], 'u-2')                       # the turn the quote came from, on record
        code, _, err = self.gate('owner', 'bundle', path, '--quote', 'yes, apply all of it')
        self.assertEqual(code, 4); self.assertIn('no ticket', err)             # used up

    def test_a_proposal_changed_after_the_check_is_refused(self):
        path = self.proposal()
        self.assertIsNone(self.hook(self.cmd(path, '--dry-run')))
        self.turn('yes, apply all of it', time.time() + 1)
        self.assertIsNone(self.hook(self.cmd(path, "--quote 'yes, apply all of it'")))
        self.proposal(self.entries + [{'kind': 'mode', 'mode': 'observe'}])     # edited between the check and the apply
        code, _, err = self.gate('owner', 'bundle', path, '--quote', 'yes, apply all of it')
        self.assertEqual(code, 4); self.assertIn('the proposal changed', err)
        out = self.hook(self.cmd(path, "--quote 'yes, apply all of it'"))     # and the hook wants the new dry run first
        self.assertEqual(out['permissionDecision'], 'deny'); self.assertIn('changed since its last dry run', out['permissionDecisionReason'])

    def test_ways_round_the_hook_find_no_ticket(self):
        path = self.proposal()
        self.turn('yes, apply all of it', time.time() + 1)
        for cmd, why in (("cd gate/tools && python3 -m gate --root %s owner adopt --quote 'invented by agent'" % self.d, 'does not appear'),
                         (self.cmd(path, "--quote 'invented by agent' # --dry-run"), '--dry-run'),
                         (self.cmd(path, "--quote 'invented by agent'\necho --dry-run"), '--dry-run'),
                         ("python3 gate/tools/gate.py --root %s owner bundle --quote 'yes, apply all of it' -- --dry-run" % self.d, 'may not start with'),
                         (self.cmd(path, "--quote 'yes, apply all of it' --quo 'invented by agent'"), 'in full'),
                         ("python3 gate/tools/gat?.py --root %s owner adopt --quote 'invented by agent'" % self.d, 'does not appear'),
                         ("python3 -c \"import gate; gate.main(['owner', 'adopt', '--quote', 'x'])\"", 'cannot read')):
            out = self.hook(cmd)
            self.assertEqual(out and out['permissionDecision'], 'deny', cmd); self.assertIn(why, out['permissionDecisionReason'], cmd)
        code, _, err = self.gate('owner', 'adopt', '--quote', 'yes, apply all of it')     # an import, a script file, a symlink: no ticket
        self.assertEqual(code, 4); self.assertIn('no ticket', err)


if __name__ == '__main__':
    unittest.main()
