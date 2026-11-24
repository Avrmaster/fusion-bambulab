"""export.py runs the gates the project requires and lets out only the parts that pass (no Fusion: the gate runs are faked)."""
import json, os, subprocess, sys, tempfile, time, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools'))
import export  # noqa: E402


class Gates(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, 'gate'))

    def settings(self, s):
        with open(os.path.join(self.root, 'gate', 'settings.json'), 'w') as f:
            json.dump(s, f)

    def fake_gate(self, verdicts, rc=1):
        """subprocess.run stand-in: writes a fresh result per part with the verdict of verdicts[gate][part]."""
        def run(cmd, **kw):
            g = 'thinwall' if 'thinwall' in cmd else 'overhang' if 'overhang' in cmd else 'heatset'
            d = os.path.join(self.root, 'gate', 'results', g); os.makedirs(d, exist_ok=True)
            for n, v in verdicts.get(g, {}).items():
                with open(os.path.join(d, n + '.json'), 'w') as f:
                    json.dump({'verdict': v, 'checked_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() + 1))}, f)
            return subprocess.CompletedProcess(cmd, rc, 'report of %s\n' % g, '')
        return run

    def test_default_is_every_gate(self):
        self.assertEqual(export.required_gates(self.root)[0], list(export.GATES))

    def test_project_narrows_the_gates(self):
        self.settings({'export': {'require': ['heatset']}})
        self.assertEqual(export.required_gates(self.root)[0], ['heatset'])

    def test_unknown_gate_stops(self):
        self.settings({'export': {'require': ['colour']}})
        with self.assertRaises(SystemExit):
            export.required_gates(self.root)

    def test_failing_part_is_held_back(self):
        self.settings({'export': {'require': ['heatset', 'overhang']}})
        v = {'heatset': {'a': 'pass', 'b': 'pass'}, 'overhang': {'a': 'pass', 'b': 'fail'}}
        with mock.patch('subprocess.run', self.fake_gate(v)):
            passed, failed, gates = export.run_gates(self.root, {}, ['a', 'b'], False)
        self.assertEqual(passed, ['a'])
        self.assertEqual(failed, {'b': ['overhang']})

    def test_stale_result_is_no_pass(self):
        self.settings({'export': {'require': ['heatset']}})
        d = os.path.join(self.root, 'gate', 'results', 'heatset'); os.makedirs(d)
        with open(os.path.join(d, 'a.json'), 'w') as f:
            json.dump({'verdict': 'pass', 'checked_at': '2000-01-01T00:00:00Z'}, f)
        with mock.patch('subprocess.run', self.fake_gate({})):
            passed, failed, _ = export.run_gates(self.root, {}, ['a'], False)
        self.assertEqual(passed, [])
        self.assertEqual(failed, {'a': ['heatset']})

    def test_gate_that_cannot_run_stops_the_export(self):
        self.settings({'export': {'require': ['heatset']}})
        with mock.patch('subprocess.run', self.fake_gate({}, rc=4)):
            with self.assertRaises(SystemExit):
                export.run_gates(self.root, {}, ['a'], False)

    def test_skipped_part_passes_that_gate(self):
        self.settings({'export': {'require': ['heatset']}, 'skip': ['a']})
        with mock.patch('subprocess.run', self.fake_gate({})):
            passed, failed, _ = export.run_gates(self.root, {}, ['a'], False)
        self.assertEqual(passed, ['a'])


if __name__ == '__main__':
    unittest.main()
