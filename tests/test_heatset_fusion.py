"""The heat-set gate end to end in a running Fusion: a new, unsaved document with 15 known holes and their insert
models (tests/fusion_heatset_fixture.py) is read by the real gate. Each case must give exactly its findings, a
counterbore for a screw must not be read as a pocket, the run must be fast, and gate.py fix-inserts must leave only
the pocket findings. Skipped when Fusion does not answer (FUSION_MCP_URL, default 127.0.0.1:27182). The document is
closed without saving at the end.

    python3 -m unittest tests.test_heatset_fusion -v"""
import io, json, os, sys, tempfile, time, unittest, contextlib, shutil
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate')); sys.path.insert(0, os.path.join(ROOT, 'tools', 'fusion'))
import fusion_rpc  # noqa: E402
import gate  # noqa: E402

EXPECTED = {
    'good': [],
    'good_relief_6_5': [],
    'no_relief': ['relief_missing'],
    'wrong_hole': ['hole_d'],
    'short_hole': ['hole_short'],
    'old_straight': ['hole_missing', 'lead_in_depth', 'not_golden', 'relief_missing'],
    'no_insert': ['no_insert'],
    'not_flush': ['not_flush'],
    'upside_down': ['upside_down'],
    'screw_cbore': [],
    'in_recess': [],
    'relief_depth': ['relief_depth'],
    'old_insert': ['not_golden'],
    'side_good': [],
    'side_no_lead': ['lead_in_missing'],
    'insert_in_solid': ['insert_without_pocket'],
}
POCKET_CODES = {'relief_missing', 'relief_d', 'relief_depth', 'lead_in_missing', 'lead_in_d', 'lead_in_depth', 'hole_missing', 'hole_d',
                'hole_short', 'open_wall', 'insert_without_pocket'}


CLOSE = r'''
import adsk.core
def job(JOB):
    app = adsk.core.Application.get()
    for d in list(app.documents):
        if d.name == JOB['doc'] and not d.dataFile:
            d.close(False)
            return {'closed': True}
    return {'closed': False}
'''


def text(p):
    with open(p, encoding='utf-8') as f:
        return f.read()


def fusion_up():
    try:
        fusion_rpc.ping(timeout=5); return True
    except Exception:
        return False


@unittest.skipUnless(fusion_up(), 'Fusion does not answer')
class HeatsetInFusion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rules = json.loads(text(os.path.join(ROOT, 'rules', 'heatset.json')))
        src = ''.join(text(p) + '\n' for p in (
            os.path.join(ROOT, 'tools', 'gate', 'heatset_core.py'), os.path.join(ROOT, 'tools', 'gate', 'heatset_fusion.py'),
            os.path.join(ROOT, 'tools', 'gate', 'golden.py'), os.path.join(ROOT, 'tests', 'fusion_heatset_fixture.py')))
        fx = fusion_rpc.run_job(src, {'rules': rules}, write=True, timeout=120, name='fixture')
        cls.doc, cls.cases = fx['doc'], fx['cases']
        cls.root = tempfile.mkdtemp(prefix='heatset_fixture_')
        os.makedirs(os.path.join(cls.root, 'fusion'))
        with open(os.path.join(cls.root, 'fusion', 'parts.json'), 'w') as f:
            json.dump({'doc': cls.doc, 'parts': {'block': {'component': 'Block', 'body': 'Block'}}}, f)

    @classmethod
    def tearDownClass(cls):
        fusion_rpc.run_job(CLOSE, {'doc': cls.doc}, write=True, timeout=30, name='close')
        shutil.rmtree(cls.root, ignore_errors=True)

    def run_gate(self, *argv):
        buf = io.StringIO(); t = time.time()
        with contextlib.redirect_stdout(buf):
            code = gate.check(self.root, list(argv))
        res = json.loads(text(os.path.join(self.root, 'gate', 'results', 'heatset', 'block.json')))
        return code, res, buf.getvalue(), time.time() - t

    def by_case(self, findings):
        """Each finding to the case whose mouth is nearest to its place."""
        out = {c: [] for c in self.cases}
        for f in findings:
            c = min(self.cases, key=lambda c: sum((self.cases[c][i] - f['at'][i]) ** 2 for i in range(3)))
            out[c].append(f['code'])
        return {c: sorted(v) for c, v in out.items()}

    def test_1_each_case_gives_its_findings(self):
        code, res, text, secs = self.run_gate()
        self.assertEqual(code, 1, text)
        got = self.by_case(res['findings'])
        for c, want in EXPECTED.items():
            self.assertEqual(got[c], sorted(want), '%s\n%s' % (c, text))
        self.assertEqual(res['pockets'], 14)                       # 15 holes, the screw counterbore is no pocket
        self.assertLess(secs, 10, 'the gate took %.1f s on one block' % secs)

    def test_1_findings_are_actionable(self):
        _, res, text, _ = self.run_gate()
        for f in res['findings']:
            for k in ('measured', 'expected', 'fix', 'at', 'out'):
                self.assertTrue(f.get(k), (f['code'], k))
        self.assertIn('expected:', text); self.assertIn('fix:', text)

    def test_2_fix_inserts_leaves_only_pocket_findings(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(gate.fix_inserts(self.root, ['block'], component='HW (ref)'), 0, buf.getvalue())
        code, res, text, _ = self.run_gate()
        left = {f['code'] for f in res['findings']}
        self.assertTrue(left <= POCKET_CODES, 'insert findings left after fix-inserts: %s\n%s' % (left - POCKET_CODES, text))
        got = self.by_case(res['findings'])
        for c in ('good', 'no_insert', 'not_flush', 'upside_down', 'old_insert', 'in_recess'):
            self.assertEqual(got[c], [], c)


if __name__ == '__main__':
    unittest.main()
