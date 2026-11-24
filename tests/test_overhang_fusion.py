"""The overhang gate end to end in a running Fusion: a new, unsaved document with one body per known case
(tests/fusion_overhang_fixture.py) is read by the real gate (gate.py overhang). Each case must give exactly its codes,
the run must be fast. Skipped when Fusion does not answer. The document is closed without saving at the end.

    python3 -m unittest tests.test_overhang_fusion -v"""
import contextlib, io, json, os, shutil, sys, tempfile, time, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate')); sys.path.insert(0, os.path.join(ROOT, 'tools', 'fusion'))
import fusion_rpc  # noqa: E402
import overhang_gate  # noqa: E402
from tests.test_heatset_fusion import CLOSE, fusion_up, text  # noqa: E402

EXPECTED = {            # case: (findings, allowed)
    'cone_50': (['overhang'], []),
    'chamfer_45': ([], []),
    'round_hole': (['round_hole'], []),
    'teardrop': ([], []),
    'heatset_side': ([], ['heatset_ceiling']),
    'short_bridge': ([], []),
    'long_bridge': (['long_bridge'], []),
    'ledge': (['overhang'], []),
}


@unittest.skipUnless(fusion_up(), 'Fusion does not answer')
class OverhangInFusion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        hs = json.loads(text(os.path.join(ROOT, 'rules', 'heatset.json')))
        fx = fusion_rpc.run_job(text(os.path.join(ROOT, 'tests', 'fusion_overhang_fixture.py')), {'heatset_rules': hs},
                                write=True, timeout=120, name='overhang_fixture')
        cls.doc = fx['doc']
        cls.root = tempfile.mkdtemp(prefix='overhang_fixture_')
        os.makedirs(os.path.join(cls.root, 'fusion'))
        parts = {c: {'component': c, 'body': c, '3mf': [{'file': 'x.3mf', 'object': c, 'face': '-z'}]} for c in fx['cases']}
        parts['no_face'] = {'component': 'ledge', 'body': 'ledge'}
        with open(os.path.join(cls.root, 'fusion', 'parts.json'), 'w') as f:
            json.dump({'doc': cls.doc, 'parts': parts}, f)
        buf = io.StringIO(); t = time.time()
        with contextlib.redirect_stdout(buf):
            cls.code = overhang_gate.check(cls.root, [])
        cls.secs, cls.out = time.time() - t, buf.getvalue()

    @classmethod
    def tearDownClass(cls):
        fusion_rpc.run_job(CLOSE, {'doc': cls.doc}, write=True, timeout=30, name='close')
        shutil.rmtree(cls.root, ignore_errors=True)

    def res(self, case):
        return json.loads(text(os.path.join(self.root, 'gate', 'results', 'overhang', case + '.json')))

    def test_each_case(self):
        self.assertEqual(self.code, 1, self.out)
        for case, (want, allowed) in EXPECTED.items():
            r = self.res(case)
            self.assertEqual(sorted(f['code'] for f in r['findings']), want, '%s\n%s' % (case, self.out))
            self.assertEqual(sorted(f['code'] for f in r['allowed']), allowed, case)
            self.assertEqual(r['verdict'], 'fail' if want else 'pass', case)

    def test_places_and_angles(self):
        f = self.res('cone_50')['findings'][0]
        self.assertAlmostEqual(f['slope_deg'], 40.0, delta=1.5)               # 50 from the vertical
        f = self.res('round_hole')['findings'][0]
        self.assertAlmostEqual(f['d'], 6.0, places=2)
        self.assertAlmostEqual(f['h'], 10.0 + 1.5, delta=1.6)                 # the top half of the hole
        f = self.res('ledge')['findings'][0]
        self.assertAlmostEqual(f['area_mm2'], (5.0 - 0.4) * 20.0, delta=4.0)                # the first 0.4 is a ledge
        self.assertAlmostEqual(f['h'], 15.0, delta=0.1)

    def test_no_bed_face(self):
        self.assertEqual([f['code'] for f in self.res('no_face')['findings']], ['no_bed_face'])

    def test_fast(self):
        self.assertLess(self.secs, 20, self.out)


if __name__ == '__main__':
    unittest.main()
