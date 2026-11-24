"""tools/fusion/show_part.py without Fusion: the Fusion job compiles, marks come from --at and from a gate's results,
and the images land in --out.    python3 -m unittest tests.test_show_part -v"""
import contextlib, io, json, os, sys, tempfile, unittest
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'fusion'))
import show_part  # noqa: E402


class ShowPart(unittest.TestCase):
    def test_job_compiles(self):
        compile(show_part.JOB_SRC, 'show_part_job', 'exec')

    def test_marks_and_output(self):
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, 'fusion')); os.makedirs(os.path.join(root, 'gate', 'results', 'overhang'))
        json.dump({'doc': 'D', 'parts': {'p': {'component': 'C', 'body': 'B', '3mf': [{'file': 'x.3mf', 'object': 'p', 'face': '+y'}]}}},
                  open(os.path.join(root, 'fusion', 'parts.json'), 'w'))
        json.dump({'findings': [{'at': [1, 2, 3]}, {'at': [4, 5, 6]}]}, open(os.path.join(root, 'gate', 'results', 'overhang', 'p.json'), 'w'))
        run_dir = tempfile.mkdtemp(); seen = {}
        def fake(src, job, **kw):
            seen.update(job)
            paths = []
            for v in job['views']:
                pth = os.path.join(job['dir'], 'p_%s.png' % v); open(pth, 'wb').write(b'png'); paths.append(pth)
            return {'images': paths}
        out = os.path.join(root, 'shots')
        argv = ['show_part.py', 'p', '--at', '9,9,9', '--gate', 'overhang', '--view', 'below', '--project', root, '--out', out]
        with mock.patch.object(show_part.fusion_rpc, 'run_job', fake), mock.patch.object(show_part.fusion_rpc, '_out_dir', lambda: run_dir), \
                mock.patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()) as buf:
            show_part.main()
        self.assertEqual(seen['marks'], [[9.0, 9.0, 9.0], [1, 2, 3], [4, 5, 6]])
        self.assertEqual(seen['down'], [0, 1, 0])
        self.assertEqual(seen['spec'], {'component': 'C', 'body': 'B'})
        self.assertEqual(len(os.listdir(out)), 1)
        self.assertIn('3: (4.0, 5.0, 6.0)', buf.getvalue())


if __name__ == '__main__':
    unittest.main()
