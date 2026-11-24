"""The thin-wall gate end to end in a running Fusion: a new, unsaved document with known walls
(tests/fusion_thinwall_fixture.py) in a temporary project with two small .3mf files (0.6 and 0.4 nozzle) is read by
the real gate (gate.py check --gate thinwall). Each case must give exactly its finding at its place, the exempt cases
none, and the run must be fast. Skipped when Fusion does not answer. The document is closed without saving.

    python3 -m unittest tests.test_thinwall_fusion -v"""
import contextlib, io, json, math, os, shutil, sys, tempfile, time, unittest, zipfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate')); sys.path.insert(0, os.path.join(ROOT, 'tools', 'fusion'))
import fusion_rpc  # noqa: E402
import gate  # noqa: E402
import thinwall_gate  # noqa: E402

EXPECTED = {           # case: (code, thickness) or None
    'sheet_1_0': ('under_two_lines', 1.0),
    'layer_0_3': None,
    'wall_0_5': ('under_one_line', 0.5),
    'hole_1_2': ('under_two_lines', 1.2),
    'wall_2_0': None,
    'wall_1_4': ('thin_wall', 1.4),
    'chamfer': None,
}

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


def tiny_3mf(path, obj, nozzle, line, layer, generator='classic'):
    ps = {'nozzle_diameter': [str(nozzle)], 'line_width': str(line), 'outer_wall_line_width': str(line), 'layer_height': str(layer),
          'wall_generator': generator}
    ms = '<?xml version="1.0" encoding="UTF-8"?>\n<config>\n  <object id="2">\n    <metadata key="name" value="%s"/>\n  </object>\n</config>\n' % obj
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('Metadata/project_settings.config', json.dumps(ps))
        z.writestr('Metadata/model_settings.config', ms)


@unittest.skipUnless(fusion_up(), 'Fusion does not answer')
class ThinwallInFusion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fx = fusion_rpc.run_job(text(os.path.join(ROOT, 'tests', 'fusion_thinwall_fixture.py')), {}, write=True, timeout=120, name='fixture')
        cls.doc, cls.cases = fx['doc'], fx['cases']
        cls.root = tempfile.mkdtemp(prefix='thinwall_fixture_')
        os.makedirs(os.path.join(cls.root, 'fusion')); os.makedirs(os.path.join(cls.root, 'bambu'))
        tiny_3mf(os.path.join(cls.root, 'bambu', 'p06.3mf'), 'Block', 0.6, 0.62, 0.3)
        tiny_3mf(os.path.join(cls.root, 'bambu', 'p04.3mf'), 'Wheel', 0.4, 0.42, 0.2)
        with open(os.path.join(cls.root, 'fusion', 'parts.json'), 'w') as f:
            json.dump({'doc': cls.doc, 'parts': {
                'block': {'component': 'Block', 'body': 'Block', '3mf': [{'file': 'bambu/p06.3mf', 'object': 'Block', 'face': '-z'}]},
                'wheel': {'component': 'Wheel', 'body': 'Wheel', '3mf': [{'file': 'bambu/p04.3mf', 'object': 'Wheel', 'face': '-z'}]}}}, f)

    @classmethod
    def tearDownClass(cls):
        fusion_rpc.run_job(CLOSE, {'doc': cls.doc}, write=True, timeout=30, name='close')
        shutil.rmtree(cls.root, ignore_errors=True)

    def run_gate(self):
        buf = io.StringIO(); t = time.time()
        with contextlib.redirect_stdout(buf):
            code = thinwall_gate.check(self.root, [], gate)
        res = {n: json.loads(text(os.path.join(self.root, 'gate', 'results', 'thinwall', n + '.json'))) for n in ('block', 'wheel')}
        return code, res, buf.getvalue(), time.time() - t

    def test_each_case_gives_its_finding(self):
        code, res, out, secs = self.run_gate()
        self.assertEqual(code, 1, out)
        got = {c: [] for c in EXPECTED}
        for f in res['block']['findings']:
            lo, hi = f['extent']
            inside = [c for c in EXPECTED if all(lo[k] - 1.0 <= self.cases[c][k] <= hi[k] + 1.0 for k in range(3))]
            self.assertEqual(len(inside), 1, (f['at'], f['extent'], out))
            got[inside[0]].append(f)
        for c, want in EXPECTED.items():
            if want is None:
                self.assertEqual(got[c], [], '%s\n%s' % (c, out)); continue
            self.assertEqual(len(got[c]), 1, '%s\n%s' % (c, out))
            self.assertEqual(got[c][0]['code'], want[0], out)
            self.assertAlmostEqual(got[c][0]['thickness_mm'], want[1], delta=0.005, msg=out)
        self.assertGreater(res['block']['counts']['sacrificial_layer'], 0)
        self.assertGreater(res['block']['counts']['wedge'] + res['block']['counts']['corner'], 0)
        self.assertEqual(res['block']['limits']['line_mm'], 0.62)
        self.assertLess(secs, 30, 'the gate took %.1f s' % secs)

    def test_teeth_are_not_walls(self):
        _, res, out, _ = self.run_gate()
        w = res['wheel']
        self.assertEqual(w['findings'], [], out)
        self.assertGreater(w['counts']['teeth'], 0, out)
        self.assertEqual(w['limits']['line_mm'], 0.42)

    def test_findings_are_actionable(self):
        _, res, out, _ = self.run_gate()
        for f in res['block']['findings']:
            for k in ('measured', 'expected', 'fix', 'at', 'thickness_mm', 'limit_mm', 'limits'):
                self.assertIn(k, f)
        self.assertIn('PASS 1, FAIL 1 of 2 parts', out)


if __name__ == '__main__':
    unittest.main()
