"""The script of a Fusion unit and what runs inside it, without Fusion: the JOB header round trip, the pasted helpers,
the entry point of probes/_fusion.py against a fake adsk, and the fake transport other tests use (fake_fusion.py)."""
import json, math, os, shutil, sys, tempfile, types, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(ROOT, 'tools', 'gate')
sys.path.insert(0, GATE); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_fusion_rpc
from gate_common import GateError
from probes import _geom
import fake_fusion

SIG = 'ab' * 32


class Dir(unittest.TestCase):
    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.d)

    def job(self, probe='fusion_census', part='demo/bracket', **kw):
        base = dict(unit_id='u-0001', run='r1', probe=probe, doc='Demo Assembly', doc_key='demo',
                    out=os.path.join(self.d, 'gate', 'run', 'r1', 'units', 'u-0001.json'), part=part, component_id='c1',
                    occurrence='Bracket:1', body='Body1', sig=SIG, params={'inserts.wall_target': 4.0}, tables={'inserts': {'kinds': {}}},
                    not_printed=['^ref '])
        base.update(kw)
        return gate_fusion_rpc.new_job(**base)


class Script(Dir):
    def test_job_has_every_key_and_caps_the_budget(self):
        j = self.job(deadline_s=120)
        self.assertEqual(sorted(j), sorted(gate_fusion_rpc.JOB_KEYS)); self.assertEqual(j['deadline_s'], 30)
        self.assertEqual((j['cursor'], j['features'], j['target'], j['args'], j['motion_groups']), (None, None, None, {}, []))
        self.assertTrue(j['legacy']['insert_pockets'].endswith('insert_pockets.py'))
        with self.assertRaises(GateError): self.job(probe='stl_walls')
        with self.assertRaises(GateError): self.job(out='gate/run/x.json')

    def test_script_order_and_round_trip(self):
        j = self.job(features={'holes': [{'id': 'h1', 'note': 'quote \' " and \\ and é'}], 'openings': []})
        s = gate_fusion_rpc.build_script(j)
        self.assertEqual(gate_fusion_rpc.job_of(s), j)
        marks = [s.index('# ---- %s ----' % n) for n in ('_geom.py', '_geom_rings.py', '_fusion.py', 'fusion_census.py')]
        self.assertEqual(marks, sorted(marks)); self.assertLess(s.index(gate_fusion_rpc.JOB_LINE), marks[0])
        self.assertTrue(s.startswith('# gate unit u-0001: fusion_census on demo/bracket\nimport json\n_GATE_JOB = json.loads('))
        compile(s, 'unit.py', 'exec')
        self.assertEqual(len([l for l in s.splitlines() if l.startswith(gate_fusion_rpc.JOB_LINE)]), 1)
        s.encode('ascii')                                              # nothing outside ASCII goes over the wire
        unit = gate_fusion_rpc.unit_of(s)
        self.assertEqual(unit, gate_fusion_rpc.unit_text(j)); compile(unit, 'unit_text.py', 'exec')
        with self.assertRaises(GateError): gate_fusion_rpc.unit_of('print(1)\n')

    def test_the_header_comment_is_ascii_whatever_the_names_are(self):
        j = self.job(part='demo/br\u00e4cket', doc='D\u00e9mo')
        s = gate_fusion_rpc.build_script(j)
        s.encode('ascii'); self.assertEqual(gate_fusion_rpc.job_of(s)['doc'], 'D\u00e9mo')

    def test_no_name_can_end_the_header_comment(self):
        self.assertEqual(gate_fusion_rpc._ascii('Bracket\nimport os\r x\x0b\x00  demo/bracket \u00e4\x7f\u2028'), 'Bracket?import os? x??  demo/bracket ???')
        self.assertEqual(gate_fusion_rpc._ascii(' !~'), ' !~'); self.assertEqual(gate_fusion_rpc._ascii(None), 'None')
        for names in (dict(part=None, doc='D\nimport os\rx = 1'), dict(part='demo/bracket\nimport os', doc='Demo'), dict(unit_id='u-1\x0bimport os'),
                      dict(part=None, doc='D\u2028import os\x85x\x0cy\x1cz')):
            with self.subTest(names=names):
                j = self.job(**names)
                s = gate_fusion_rpc.build_script(j)
                first, second = s.split('\n')[:2]
                self.assertEqual(second, 'import json'); self.assertTrue(first.startswith('# gate unit '))
                self.assertTrue(all(' ' <= c <= '~' for c in first), first)
                self.assertEqual(s.splitlines()[1], 'import json')                    # also by the line breaks Python itself knows
                compile(s, 'unit.py', 'exec')
                self.assertEqual(gate_fusion_rpc.job_of(s)['doc'], j['doc'])          # the job still carries the name as it is

    def test_the_script_leaves_three_names_and_a_run_that_does_nothing(self):
        j = self.job()
        s = gate_fusion_rpc.build_script(j)
        text = s[:s.index(gate_fusion_rpc.UNIT_LINE)] + gate_fusion_rpc.UNIT_LINE + repr(
            'import json, os\nSEEN = []\ndef along(): pass\ndef run(_context=""):\n    n = len(os.listdir(os.path.dirname(JOB["out"]))) if os.path.isdir(os.path.dirname(JOB["out"])) else 0\n'
            '    os.makedirs(os.path.dirname(JOB["out"]), exist_ok=True)\n    open(JOB["out"] + ".%d" % n, "w").close()\n') + gate_fusion_rpc.WRAPPER
        shared = {'along': 'another session owns this name', '__builtins__': __builtins__}
        exec(compile(text, 'unit.py', 'exec'), shared)
        self.assertEqual(sorted(k for k in shared if k != '__builtins__'), ['_GATE_JOB', '_GATE_UNIT', 'along', 'json', 'run'])
        shared['run']('')
        self.assertEqual(sorted(k for k in shared if k != '__builtins__'), ['along', 'json', 'run'])
        self.assertEqual(shared['along'], 'another session owns this name')
        made = os.listdir(os.path.dirname(j['out']))
        self.assertEqual(len(made), 1)
        shared['run'](''); shared['run']('')                             # a later helper-only script of any session calls run() again
        self.assertEqual(os.listdir(os.path.dirname(j['out'])), made)

    def test_a_unit_that_raises_still_leaves_the_stub(self):
        s = gate_fusion_rpc.build_script(self.job())
        text = s[:s.index(gate_fusion_rpc.UNIT_LINE)] + gate_fusion_rpc.UNIT_LINE + repr('def run(_context=""):\n    raise RuntimeError("boom")\n') + gate_fusion_rpc.WRAPPER
        shared = {}
        exec(compile(text, 'unit.py', 'exec'), shared)
        with self.assertRaises(RuntimeError): shared['run']('')
        self.assertIsNone(shared['run']('')); self.assertNotIn('_GATE_UNIT', shared)

    def test_the_ping_is_a_script_that_compiles_and_does_nothing(self):
        g = {}
        exec(compile(gate_fusion_rpc.PING_SCRIPT, 'ping.py', 'exec'), g)
        self.assertIsNone(g['run']('')); self.assertTrue(gate_fusion_rpc.read_only_for(gate_fusion_rpc.PING_SCRIPT))

    def test_every_fusion_probe_builds_a_script_that_compiles(self):
        for probe in gate_fusion_rpc.FUSION_PROBES:
            with self.subTest(probe=probe):
                text = gate_fusion_rpc.build_script(self.job(probe=probe, part=None if probe == 'fusion_scan' else 'demo/bracket'))
                code = compile(text, probe, 'exec')
                self.assertIn('run', code.co_names)                              # the unit leaves a run() behind: the entry point Fusion calls
                self.assertEqual(gate_fusion_rpc.job_of(text)['probe'], probe)   # and the job that travels in it is the one that was asked for

    def test_incomplete_job_and_script_without_a_job(self):
        j = self.job(); del j['out']
        with self.assertRaises(GateError): gate_fusion_rpc.build_script(j)
        with self.assertRaises(GateError): gate_fusion_rpc.job_of('print(1)\n')

    def test_merge_facts(self):
        a = {'pockets': [1, 2], 'ray': {'n': 48}, 'count': 2, 'x': None}
        b = {'pockets': [3], 'ray': {'max': 8.0}, 'count': 3, 'new': 'k'}
        self.assertEqual(gate_fusion_rpc.merge_facts(a, b), {'pockets': [1, 2, 3], 'ray': {'n': 48, 'max': 8.0}, 'count': 3, 'x': None, 'new': 'k'})
        self.assertEqual(a['pockets'], [1, 2]); self.assertEqual(gate_fusion_rpc.merge_facts(None, b), b); self.assertEqual(gate_fusion_rpc.merge_facts(a, None), a)

    def test_endpoint_and_budget(self):
        self.assertEqual(gate_fusion_rpc.endpoint({}), gate_fusion_rpc.DEFAULT_URL); self.assertEqual(gate_fusion_rpc.endpoint(None), gate_fusion_rpc.DEFAULT_URL)
        self.assertEqual(gate_fusion_rpc.endpoint({'fusion': {'endpoint': 'http://127.0.0.1:9/mcp'}}), 'http://127.0.0.1:9/mcp')

    def test_the_endpoint_is_loopback_http_only(self):
        for url in ('http://127.0.0.1:27182/mcp', 'http://localhost:27182/mcp', 'http://LOCALHOST:9/mcp', 'http://[::1]:27182/mcp', 'http://127.0.0.1/mcp'):
            self.assertEqual(gate_fusion_rpc.endpoint({'fusion': {'endpoint': url}}), url)
        for url in ('https://127.0.0.1:27182/mcp', 'http://example.com/mcp', 'http://192.168.1.20:27182/mcp', 'http://0.0.0.0:27182/mcp', 'file:///etc/hosts',
                    'ftp://127.0.0.1/mcp', '127.0.0.1:27182/mcp', 'http://127.0.0.1@example.com/mcp', 'http://127.0.0.1.example.com/mcp', 'http://[::1/mcp', 27182):
            with self.subTest(url=url):
                with self.assertRaises(GateError) as e: gate_fusion_rpc.endpoint({'fusion': {'endpoint': url}})
                self.assertIn('"fusion"."endpoint" must be http://127.0.0.1:<port>', str(e.exception)); self.assertIn('this machine only', str(e.exception))
        self.assertEqual(gate_fusion_rpc.budget({'fusion': {'budget_s': 99}}), 30); self.assertEqual(gate_fusion_rpc.budget({}), 20)


class Fake(Dir):
    """The contract of tests/fake_fusion.py: what a test of gate_plan or gate_stamp can rely on."""

    def call(self, fusion, **kw):
        j = self.job(**kw)
        res = fusion.transport('http://x', gate_fusion_rpc.build_script(j), 30)
        env = None
        if os.path.exists(j['out']):
            with open(j['out'], encoding='utf-8') as f: env = json.load(f)
        return res, env

    def test_canned_facts_come_back_in_the_envelope(self):
        fusion = fake_fusion.FakeFusion({'Demo Assembly': 12})
        fusion.set_facts('fusion_census', 'demo/bracket', {'holes': [], 'openings': [], 'counts': {}})
        res, env = self.call(fusion)
        self.assertEqual(res, fake_fusion.OK)
        self.assertEqual(sorted(env), ['asked', 'cursor', 'doc', 'doc_version', 'done', 'elapsed_s', 'error', 'facts', 'job', 'notes', 'open_documents', 'part', 'probe', 'sig'])
        self.assertEqual((env['job'], env['doc'], env['doc_version'], env['part'], env['sig'], env['probe'], env['done'], env['error']),
                         ('u-0001', 'Demo Assembly', 12, 'demo/bracket', SIG, 'fusion_census', True, None))
        self.assertEqual(env['open_documents'], ['Demo Assembly']); self.assertEqual(fusion.calls_of('fusion_census', 'demo/bracket')[0]['id'], 'u-0001')

    def test_several_answers_are_given_out_in_order_and_the_last_repeats(self):
        fusion = fake_fusion.FakeFusion({'Demo Assembly': 12})
        fusion.set_facts('fusion_inserts', 'demo/bracket', {'pockets': [1]}, done=False, cursor=1)
        fusion.set_facts('fusion_inserts', 'demo/bracket', {'pockets': [2]}, done=True)
        got = [self.call(fusion, probe='fusion_inserts')[1] for _ in range(3)]
        self.assertEqual([(e['done'], e['cursor'], e['facts']['pockets']) for e in got], [(False, 1, [1]), (True, None, [2]), (True, None, [2])])

    def test_a_changed_signature_can_be_canned(self):
        fusion = fake_fusion.FakeFusion({'Demo Assembly': 12})
        fusion.set_facts('fusion_thin', 'demo/bracket', {'faces': []}, sig='cd' * 32)
        self.assertEqual(self.call(fusion, probe='fusion_thin')[1]['sig'], 'cd' * 32)

    def test_nothing_canned_document_closed_probe_error(self):
        fusion = fake_fusion.FakeFusion({'Demo Assembly': 12})
        self.assertIn('no facts set', self.call(fusion)[1]['error'])
        fusion.set_error('fusion_gap', 'demo/bracket', 'RuntimeError: boom')
        self.assertEqual(self.call(fusion, probe='fusion_gap')[1]['error'], 'RuntimeError: boom')
        closed = fake_fusion.FakeFusion({'Other': 3}); closed.set_facts('fusion_census', 'demo/bracket', {})
        env = self.call(closed)[1]
        self.assertIn("document 'Demo Assembly' is not open (open: Other)", env['error']); self.assertFalse(env['done']); self.assertEqual(env['open_documents'], ['Other'])

    def test_timeout_and_down(self):
        fusion = fake_fusion.FakeFusion({'Demo Assembly': 12}); fusion.set_timeout('fusion_census', 'demo/bracket', alive_after=False)
        res, env = self.call(fusion)
        self.assertEqual((res['ok'], res['timeout'], env), (False, True, None)); self.assertFalse(fusion.ping('http://x')); self.assertEqual(fusion.pings, 1)
        down = fake_fusion.FakeFusion(); down.down = True
        res, env = self.call(down)
        self.assertEqual((res['ok'], res['down'], env, down.ping('http://x')), (False, True, None, False))


class Geom(unittest.TestCase):
    def test_vectors(self):
        self.assertEqual(_geom.v_add((1, 2, 3), (1, 1, 1)), (2, 3, 4)); self.assertEqual(_geom.v_sub((1, 2, 3), (1, 1, 1)), (0, 1, 2))
        self.assertEqual(_geom.v_scale((1, 2, 3), 2), (2, 4, 6)); self.assertEqual(_geom.v_dot((1, 2, 3), (4, 5, 6)), 32)
        self.assertEqual(_geom.v_cross((1, 0, 0), (0, 1, 0)), (0, 0, 1)); self.assertEqual(_geom.v_len((3, 4, 0)), 5.0)
        self.assertEqual(_geom.v_dist((0, 0, 0), (0, 3, 4)), 5.0); self.assertEqual(_geom.v_unit((0, 0, 0)), (0.0, 0.0, 0.0))
        self.assertEqual(_geom.v_unit((0, 0, 5)), (0.0, 0.0, 1.0))

    def test_axis_frame_is_right_handed_and_square(self):
        for a in ((0, 0, 1), (1, 0, 0), _geom.v_unit((1, 2, 3))):
            u, v = _geom.axis_frame(a)
            for x, y in ((u, v), (u, a), (v, a)): self.assertAlmostEqual(_geom.v_dot(x, y), 0.0)
            for i in range(3): self.assertAlmostEqual(_geom.v_cross(u, v)[i], a[i])

    def test_along_and_off_axis(self):
        self.assertAlmostEqual(_geom.along((1, 2, 7), (0, 0, 2), (0, 0, 1)), 5.0)
        self.assertAlmostEqual(_geom.off_axis((3, 4, 7), (0, 0, 2), (0, 0, 1)), 5.0)

    def test_canonical_axis(self):
        self.assertEqual(_geom.canonical_axis((0, 0, -2)), (0.0, 0.0, 1.0)); self.assertEqual(_geom.canonical_axis((0, -1, 1)), _geom.canonical_axis((0, 1, -1)))
        self.assertEqual(str(_geom.canonical_axis((0, 0, -1))), '(0.0, 0.0, 1.0)')

    def test_rigid_transform_and_its_inverse(self):
        c, s = math.cos(0.7), math.sin(0.7)
        m = (c, -s, 0, 10.0, s, c, 0, -5.0, 0, 0, 1, 13.0, 0, 0, 0, 1)
        p = (3.0, 4.0, 5.0); q = _geom.mat_point(m, p); back = _geom.mat_point(_geom.mat_inverse(m), q)
        for i in range(3): self.assertAlmostEqual(back[i], p[i])
        self.assertAlmostEqual(_geom.v_len(_geom.mat_vector(m, (1, 0, 0))), 1.0)
        self.assertEqual(_geom.mat_point(_geom.IDENTITY, p), p)
        self.assertEqual(_geom.pose_hash(m), _geom.pose_hash(tuple(x + 1e-7 for x in m))); self.assertNotEqual(_geom.pose_hash(m), _geom.pose_hash(_geom.IDENTITY))
        self.assertEqual(len(_geom.pose_hash(m)), 16)

    def test_signature_sees_a_hole_that_only_moves(self):
        box = ((0, 0, 0), (40, 30, 10))
        faces = [(0, 1200.0, (20, 15, 0)), (0, 1200.0, (20, 15, 10)), (1, 94.25, (10.0, 10.0, 5.0))]
        a = _geom.brep_signature(11500.0, 2900.0, (7, 12, 8), box, faces)
        self.assertEqual(len(a), 64)
        self.assertEqual(a, _geom.brep_signature(11500.0004, 2900.0, (7, 12, 8), box, list(reversed(faces))))
        moved = faces[:2] + [(1, 94.25, (12.0, 10.0, 5.0))]                       # same volume, area and counts
        self.assertNotEqual(a, _geom.brep_signature(11500.0, 2900.0, (7, 12, 8), box, moved))
        self.assertNotEqual(a, _geom.brep_signature(11500.0, 2900.0, (7, 12, 9), box, faces))

    def test_the_faces_go_into_a_signature_as_a_sum_that_several_calls_can_build(self):
        box = ((0, 0, 0), (40, 30, 10))
        faces = [(1200.0, (20, 15, 0)), (1200.0, (20, 15, 10)), (94.25, (10.0, 10.0, 5.0)), (94.25, (30.0, 10.0, 5.0))]
        whole = _geom.brep_signature(11500.0, 2900.0, (8, 12, 8), box, faces)
        part = _geom.faces_sum(faces[:1])                                       # call 1: one face; call 2: the rest, in another order
        part = _geom.faces_sum(reversed(faces[1:]), part)
        self.assertEqual(_geom.brep_signature_of(11500.0, 2900.0, (8, 12, 8), box, 4, part), whole)
        moved = faces[:3] + [(94.25, (30.0, 12.0, 5.0))]
        self.assertNotEqual(_geom.brep_signature(11500.0, 2900.0, (8, 12, 8), box, moved), whole)
        swapped = faces[:2] + [(94.25, (30.0, 10.0, 5.0)), (94.25, (10.0, 10.0, 5.0))]
        self.assertEqual(_geom.brep_signature(11500.0, 2900.0, (8, 12, 8), box, swapped), whole)       # the same faces in another order
        self.assertNotEqual(_geom.brep_signature(11500.0, 2900.0, (8, 12, 8), box, faces[:3] + faces[2:3]), whole)   # one face twice is not two faces
        self.assertNotEqual(_geom.brep_signature_of(11500.0, 2900.0, (8, 12, 8), box, 3, _geom.faces_sum(faces)), whole)   # the count is in it too
        self.assertNotEqual(_geom.face_digest(94.25, (10, 10, 5)), _geom.face_digest(94.25, (10, 10, 5), 1))   # a surface type, when given, counts
        self.assertLess(_geom.faces_sum(faces), _geom.SIG_MOD)

    def test_a_tape_of_answers_is_plain_json_and_knows_its_questions(self):
        import random
        rnd = random.Random(5)
        for n in (0, 1, 3, 4, 5, 63, 64, 65, 1000):
            answers = [rnd.random() < 0.3 for _ in range(n)]
            saved = _geom.tape_pack(answers, 12345)
            self.assertEqual(json.loads(json.dumps(saved)), saved)
            self.assertEqual((saved['n'], len(saved['hex']) <= n // 4 + 1), (n, True))
            self.assertEqual(_geom.tape_unpack(saved), (answers, 12345))
        self.assertEqual(_geom.tape_pack([False, False, True]), {'n': 3, 'hex': '1', 'sum': 0})          # leading "no" answers are kept by the count
        for bad in (None, 5, {}, {'n': 2, 'hex': 'zz', 'sum': 0}, {'n': 2, 'hex': 'f', 'sum': 0}, {'n': -1, 'hex': '', 'sum': 0}, {'n': 2, 'hex': '1'},
                    {'n': True, 'hex': '1', 'sum': 0}, {'n': 1, 'hex': 1, 'sum': 0}):
            with self.assertRaises(ValueError): _geom.tape_unpack(bad)
        a = _geom.tape_sum(_geom.tape_sum(0, (1.0, 2.0, 3.0), 4), (5.0, 6.0, 7.0), 4)
        self.assertEqual(a, _geom.tape_sum(_geom.tape_sum(0, (1.0, 2.0, 3.0), 4), (5.0, 6.0, 7.0), 4))
        self.assertEqual(a, _geom.tape_sum(_geom.tape_sum(0, (1.0000001, 2.0, 3.0), 4), (5.0, 6.0, 7.0), 4))                   # to 0.001 mm
        others = [_geom.tape_sum(_geom.tape_sum(0, (5.0, 6.0, 7.0), 4), (1.0, 2.0, 3.0), 4),                                  # another order
                  _geom.tape_sum(_geom.tape_sum(0, (1.002, 2.0, 3.0), 4), (5.0, 6.0, 7.0), 4),                                # a point that moved
                  _geom.tape_sum(_geom.tape_sum(0, (1.0, 2.0, 3.0), 5), (5.0, 6.0, 7.0), 4),                                  # another body
                  _geom.tape_sum(0, (1.0, 2.0, 3.0), 4), _geom.tape_sum(a, (0.0, 0.0, 0.0), 0)]                               # one fewer, one more
        self.assertEqual(len(set(others + [a])), 6)
        self.assertTrue(all(0 <= x < _geom.TAPE_MOD for x in others))

    def test_census_and_ring_helpers_are_declared(self):
        from probes import _geom_rings
        for name in ('stack_holes', 'hole_from_stack', 'pocket_profile', 'match_profile', 'classify_stack'):
            self.assertTrue(callable(getattr(_geom, name)), name)
        for name in ('ring_annulus', 'ring_face_centre', 'annulus_points', 'arc_runs', 'path_points', 'box_overlaps_path', 'points_in_path', 'box_gap', 'section_share'):
            self.assertTrue(callable(getattr(_geom_rings, name)), name)


class Pt:
    def __init__(self, x, y, z): self.x, self.y, self.z = x, y, z


class FakeDoc:
    def __init__(self, name, version=None, file_id=None):
        self.name = name
        self.dataFile = types.SimpleNamespace(versionNumber=version, id=file_id or name) if version is not None else None
        self.products = types.SimpleNamespace(itemByProductType=lambda t: 'design of ' + name)


def fake_adsk(documents):
    app = types.SimpleNamespace(documents=documents)
    core = types.ModuleType('adsk.core')
    core.Application = types.SimpleNamespace(get=lambda: app)
    core.Point3D = types.SimpleNamespace(create=lambda x, y, z: Pt(x, y, z))
    core.Vector3D = types.SimpleNamespace(create=lambda x, y, z: Pt(x, y, z))
    fusion = types.ModuleType('adsk.fusion')
    fusion.Design = types.SimpleNamespace(cast=lambda x: x)
    adsk = types.ModuleType('adsk'); adsk.core, adsk.fusion = core, fusion
    return {'adsk': adsk, 'adsk.core': core, 'adsk.fusion': fusion}


class Prelude(Dir):
    """probes/_fusion.py executed as Fusion would execute a unit script, with a fake adsk."""

    def run_script(self, documents, probe_text, **kw):
        sent = kw.pop('sent', None)
        j = self.job(**kw)
        if sent is not None: j['sent'] = sent
        script = gate_fusion_rpc.unit_text(j)
        script = script[:script.index('# ---- fusion_census.py ----')] + probe_text
        mods = fake_adsk(documents)
        saved = {k: sys.modules.get(k) for k in mods}
        sys.modules.update(mods)
        try:
            g = {'JOB': j}                                              # the private dict the wrapper of build_script() makes
            exec(compile(script, 'unit.py', 'exec'), g)
            g['run']('')
        finally:
            for k, v in saved.items():
                if v is None: sys.modules.pop(k, None)
                else: sys.modules[k] = v
        with open(j['out'], encoding='utf-8') as f: return json.load(f), g

    def test_run_writes_the_envelope_of_a_probe(self):
        probe = 'def probe(job):\n    return {"facts": {"holes": [job["part"], gate_left(job) > 10]}, "done": False, "cursor": 3, "sig": "s" * 64}\n'
        env, g = self.run_script([FakeDoc('Other', 1), FakeDoc('Demo Assembly v12', 12)], probe)
        self.assertEqual(env['facts'], {'holes': ['demo/bracket', True]})
        self.assertEqual((env['done'], env['cursor'], env['sig'], env['doc_version'], env['error']), (False, 3, 's' * 64, 12, None))
        self.assertEqual((env['job'], env['doc'], env['part'], env['probe']), ('u-0001', 'Demo Assembly', 'demo/bracket', 'fusion_census'))
        self.assertEqual(env['open_documents'], ['Demo Assembly', 'Other'])
        self.assertFalse(os.path.exists(g['JOB']['out'] + '.tmp'))

    def test_the_document_is_taken_by_name_never_the_active_one(self):
        probe = 'def probe(job):\n    return {"facts": {"design": gate_design(job)}}\n'
        env, _ = self.run_script([FakeDoc('Other', 1), FakeDoc('Demo Assembly', 7)], probe)
        self.assertEqual(env['facts'], {'design': 'design of Demo Assembly'}); self.assertTrue(env['done'])

    def test_document_not_open_is_an_error_envelope(self):
        env, _ = self.run_script([FakeDoc('Other v3', 3)], 'def probe(job):\n    return {"facts": {"x": 1}}\n')
        self.assertIn("KeyError: \"document 'Demo Assembly' is not open (open: Other)\"", env['error'])
        self.assertEqual((env['facts'], env['done'], env['open_documents']), ({}, False, ['Other']))

    def test_two_documents_with_one_name(self):
        env, _ = self.run_script([FakeDoc('Demo Assembly', 1, 'id-a'), FakeDoc('Demo Assembly', 4, 'id-b')], 'def probe(job):\n    return {}\n')
        self.assertIn('two different open documents', env['error'])

    def test_an_exception_in_the_probe_is_an_error_envelope(self):
        env, _ = self.run_script([FakeDoc('Demo Assembly', 2)], 'def probe(job):\n    raise RuntimeError("boom at feature 4")\n')
        self.assertIn('RuntimeError: boom at feature 4', env['error']); self.assertIn('Traceback', env['error']); self.assertFalse(env['done'])

    def test_helpers(self):
        _, g = self.run_script([FakeDoc('Demo Assembly', 2)], 'def probe(job):\n    return {}\n', deadline_s=20)
        self.assertEqual(g['gate_mm'](Pt(1.0, 2.0, 0.5)), (10.0, 20.0, 5.0))
        p = g['gate_point']((10.0, 20.0, 5.0)); self.assertEqual((p.x, p.y, p.z), (1.0, 2.0, 0.5))
        self.assertEqual(g['gate_matrix'](None), _geom.IDENTITY)
        occ = types.SimpleNamespace(transform2=types.SimpleNamespace(asArray=lambda: (1, 0, 0, 1.0, 0, 1, 0, 2.0, 0, 0, 1, 1.3, 0, 0, 0, 1)))
        self.assertEqual(g['gate_matrix'](occ)[3::4][:3], (10.0, 20.0, 13.0))
        self.assertTrue(g['gate_printed']('Bracket', g['JOB'])); self.assertFalse(g['gate_printed']('ref bearing 608', g['JOB']))
        self.assertFalse(g['gate_time_up'](g['JOB'])); self.assertTrue(g['gate_time_up'](dict(g['JOB'], deadline_s=0.5)))
        self.assertIsNone(g['gate_doc_version'](FakeDoc('never saved')))

    def test_geometry_kinds_are_compared_exactly(self):
        _, g = self.run_script([FakeDoc('Demo Assembly', 2)], 'def probe(job):\n    return {}\n')
        kind = lambda t: g['gate_geo'](types.SimpleNamespace(objectType=t))
        self.assertEqual([kind('adsk::core::' + t) for t in ('Cylinder', 'EllipticalCylinder', 'Cone', 'EllipticalCone', 'Arc3D', 'EllipticalArc3D', 'Plane')],
                         ['Cylinder', 'EllipticalCylinder', 'Cone', 'EllipticalCone', 'Arc3D', 'EllipticalArc3D', 'Plane'])
        self.assertNotEqual(kind('adsk::core::EllipticalCylinder'), 'Cylinder')     # endswith() said yes, and .radius then raised

    def test_the_printed_rule_and_the_ref_patterns_are_one(self):
        _, g = self.run_script([FakeDoc('Demo Assembly', 2)], 'def probe(job):\n    return {}\n')
        job = dict(g['JOB'], not_printed=['^ref '], params={'screws.ref_pattern': '(?i)fastener', 'inserts.ref_pattern': '(?i)heat.?set'})
        printed = g['gate_path_printed']
        self.assertTrue(printed('Base:1+Bracket:2', 'Bracket', 'Body1', job)); self.assertFalse(printed('ref gearbox:1+Bearing 608:1', 'Bearing 608', 'Body1', job))
        self.assertFalse(printed('Base:1', 'Base', 'ref tool', job)); self.assertTrue(printed('', 'Bracket', 'Body1', job))
        pats = g['gate_ref_patterns'](job)
        self.assertEqual((pats['screw'], pats['insert'], pats['washer']), ('(?i)fastener', '(?i)heat.?set', '(?i)washer'))
        hw = g['gate_hardware']
        self.assertTrue(hw('M3 fastener', job)); self.assertTrue(hw('Heatset M3', job)); self.assertTrue(hw('nut_M3', job)); self.assertTrue(hw('locknut', job))
        self.assertTrue(hw('dowel 4x20', job)); self.assertFalse(hw('Peanut holder', job)); self.assertFalse(hw('Bracket', job))
        self.assertFalse(g['gate_search']('(?i)a|(?i)b', 'a') and False); self.assertFalse(g['gate_search']('(', 'x'))      # a bad pattern matches nothing

    def test_the_envelope_echoes_the_names_and_carries_notes(self):
        env, _ = self.run_script([FakeDoc('Demo Assembly v3', 3, 'same'), FakeDoc('Demo Assembly v5', 5, 'same')], 'def probe(job):\n    return {}\n')
        self.assertIsNone(env['error']); self.assertEqual(env['doc_version'], 5)                 # two versions of one file: the newest, and it is said
        self.assertEqual(len(env['notes']), 1); self.assertIn('is open 2 times', env['notes'][0]); self.assertIn('Demo Assembly v5 was read', env['notes'][0])
        self.assertEqual(env['seen'], {'doc': 'Demo Assembly', 'body': 'Body1', 'occurrence': 'Bracket:1'})

    def test_a_unit_that_starts_after_the_host_stopped_waiting_measures_nothing(self):
        import time
        env, _ = self.run_script([FakeDoc('Demo Assembly', 2)], 'def probe(job):\n    raise AssertionError("must not run")\n', deadline_s=20, sent=time.time() - 45)
        self.assertIn('TimeoutError', env['error']); self.assertIn('the host has stopped waiting', env['error']); self.assertFalse(env['done'])
        env, g = self.run_script([FakeDoc('Demo Assembly', 2)], 'def probe(job):\n    return {"facts": {"left": gate_left(job)}}\n', deadline_s=20, sent=time.time() - 8)
        self.assertIsNone(env['error']); self.assertLess(env['facts']['left'], 12.5); self.assertGreater(env['facts']['left'], 10.0)     # 8 s of the budget went in the queue
        env, _ = self.run_script([FakeDoc('Demo Assembly', 2)], 'def probe(job):\n    return {"facts": {"left": gate_left(job)}}\n', deadline_s=20, sent=time.time() + 500)
        self.assertGreater(env['facts']['left'], 19.0)                                           # a send time from the future is not believed

    def test_the_call_tells_macos_that_fusion_is_busy_and_ends_that_with_the_call(self):
        # A Fusion left alone for a minute in the background was put into App Nap: the first call after a pause ran
        # 6 to 10 times slower from its start to its end (a point test 21 ms instead of 2). The activity lifts that.
        spy = ('SEEN = []\n'
               'def gate_awake_begin(reason="x"):\n    SEEN.append("begin"); return "token"\n'
               'def gate_awake_end(h):\n    SEEN.append(("end", h))\n')
        for body, err in (('    SEEN.append("probe"); return {"facts": {}}\n', None), ('    SEEN.append("probe"); raise RuntimeError("boom")\n', 'boom')):
            with self.subTest(err=err):
                env, g = self.run_script([FakeDoc('Demo Assembly', 2)], spy + 'def probe(job):\n' + body)
                self.assertEqual(g['SEEN'], ['begin', 'probe', ('end', 'token')])
                self.assertEqual(err is None, env['error'] is None)
        env, g = self.run_script([FakeDoc('Demo Assembly', 2)], 'def _gate_objc():\n    raise OSError("no libobjc")\ndef probe(job):\n    return {"facts": {"a": 1}}\n')
        self.assertEqual(env['facts'], {'a': 1}); self.assertIsNone(env['error'])                 # it never stops a unit
        self.assertEqual(len(env['notes']), 1); self.assertIn('App Nap', env['notes'][0]); self.assertIn('OSError: no libobjc', env['notes'][0])
        env, g = self.run_script([FakeDoc('Demo Assembly', 2)], 'def _gate_objc():\n    return None\ndef probe(job):\n    return {}\n')
        self.assertEqual(env['notes'], [])                                                         # not macOS: nothing to do, nothing to say
        g['gate_awake_end'](None); g['gate_awake_end'](('broken',))                                # ending never raises

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS only')
    def test_the_activity_really_begins_and_ends_on_macos(self):
        _, g = self.run_script([FakeDoc('Demo Assembly', 2)], 'def probe(job):\n    return {}\n')
        h = g['gate_awake_begin']('fusion-bambulab test')
        self.assertTrue(h); self.assertEqual(g['_GATE_NOTES'], [])
        g['gate_awake_end'](h)

    def test_the_envelope_says_which_call_wrote_it_and_carries_a_timing_block_beside_the_facts(self):
        probe = 'def probe(job):\n    return {"facts": {"a": 1}, "cursor": {"i": 2}, "done": False, "timing": {"calls": 1, "faces": {"s": 0.5, "pc": 40}}}\n'
        env, _ = self.run_script([FakeDoc('Demo Assembly', 2)], probe, cursor={'i': 1, 'part': {}}, sig='ab' * 32, sent=12345.5)
        self.assertEqual(env['asked'], {'run': 'r1', 'sent': 12345.5, 'sig': 'ab' * 32, 'cursor_key': gate_fusion_rpc.cursor_key({'i': 1, 'part': {}})})
        self.assertEqual(env['cursor'], {'i': 2}); self.assertEqual(env['timing'], {'calls': 1, 'faces': {'s': 0.5, 'pc': 40}})
        self.assertEqual(env['facts'], {'a': 1})                                                   # the timing is never a fact
        env, _ = self.run_script([FakeDoc('Demo Assembly', 2)], 'def probe(job):\n    return {"facts": {}, "timing": "fast"}\n')
        self.assertNotIn('timing', env); self.assertEqual(env['asked']['cursor_key'], gate_fusion_rpc.cursor_key(None))

    def test_a_probe_that_works_in_place_on_its_cursor_does_not_change_which_call_the_file_names(self):
        """The digest of the cursor is taken before the probe runs. A probe that writes into the cursor it was sent (the
        state of a feature in progress) must not turn its result file into the answer of another call."""
        sent = {'i': 1, 'part': {'f': {'0|+': []}}, 'timing': {'calls': 3}}
        probe = ('def probe(job):\n    cur = job["cursor"]\n    cur["part"]["f"]["1|-"] = [1]\n    cur["part"]["tape"] = {"n": 5}\n    cur["i"] = 9\n'
                 '    return {"facts": {}, "cursor": cur, "done": False}\n')
        env, _ = self.run_script([FakeDoc('Demo Assembly', 2)], probe, cursor=json.loads(json.dumps(sent)), sig='ab' * 32, sent=12345.5)
        self.assertEqual(env['cursor']['i'], 9)                                                    # the probe did change it
        self.assertEqual(env['asked']['cursor_key'], gate_fusion_rpc.cursor_key(sent))
        self.assertNotEqual(env['asked']['cursor_key'], gate_fusion_rpc.cursor_key(env['cursor']))
        raising = 'def probe(job):\n    job["cursor"]["i"] = 9\n    raise RuntimeError("boom")\n'
        env, _ = self.run_script([FakeDoc('Demo Assembly', 2)], raising, cursor=json.loads(json.dumps(sent)))
        self.assertIn('boom', env['error']); self.assertEqual(env['cursor'], sent)                # an error hands back the cursor as it came

    TAPE_PROBE = '''
class Body:
    asked = 0
    def pointContainment(self, p):
        Body.asked += 1
        return "IN" if (p.x * 10.0) % 2.0 < 1.0 else "OUT"

def step(tape, body, n, shift=0.0):
    return [tape.ask(body, (float(k) + shift, 0.0, 0.0)) for k in range(n)]

def probe(job):
    args = job.get("args") or {}
    body, tape = Body(), GateTape(job, reserve=3.0, every=4, least=8)
    out = {"stopped": False, "replay": None}
    try:
        tape.begin(job.get("cursor"))
        out["answers"] = step(tape, body, args.get("n", 20), args.get("shift", 0.0))
        tape.end()
    except GateStop:
        out["stopped"] = True
    except GateReplay as e:
        out["replay"] = str(e)
    out.update(asked=Body.asked, fresh=tape.fresh, replayed=tape.replayed, timing={k: v[1] for k, v in tape.timing.items()})
    return {"facts": out, "done": not out["stopped"], "cursor": tape.saved() if out["stopped"] else None}
'''

    def tape(self, **kw):
        mods = fake_adsk([FakeDoc('Demo Assembly', 2)])
        mods['adsk.fusion'].PointContainment = types.SimpleNamespace(PointInsidePointContainment='IN')
        real = globals()['fake_adsk']
        globals()['fake_adsk'] = lambda documents: mods
        try:
            return self.run_script([], self.TAPE_PROBE, **kw)[0]
        finally:
            globals()['fake_adsk'] = real

    def test_a_tape_stops_a_step_when_the_time_is_up_and_the_next_call_goes_on_without_asking_twice(self):
        whole = self.tape(args={'n': 20})['facts']
        self.assertEqual((whole['stopped'], whole['asked'], whole['fresh'], whole['replayed'], whole['timing']), (False, 20, 20, 0, {'work': 20}))
        self.assertEqual(whole['answers'], [k % 2 == 0 for k in range(20)])
        # no time at all: the first 8 questions are asked all the same, so every call moves on
        first = self.tape(args={'n': 20}, deadline_s=0.001)
        self.assertEqual((first['done'], first['facts']['asked'], first['cursor']['n']), (False, 8, 8))
        second = self.tape(args={'n': 20}, deadline_s=0.001, cursor=first['cursor'])
        self.assertEqual((second['facts']['asked'], second['facts']['replayed'], second['cursor']['n']), (8, 8, 16))     # 8 from the tape, 8 from Fusion
        third = self.tape(args={'n': 20}, deadline_s=0.001, cursor=second['cursor'])
        self.assertEqual((third['done'], third['facts']['asked'], third['facts']['replayed'], third['facts']['answers']), (True, 4, 16, whole['answers']))
        # with time the clock is read and nothing stops; a limit of the job stops whatever the clock says
        self.assertEqual(self.tape(args={'n': 20, 'tape_limit': 3})['cursor']['n'], 3)
        # a step that asks other questions than its tape answers is told so, and one that asks fewer too
        moved = self.tape(args={'n': 20, 'shift': 0.5}, cursor=first['cursor'])['facts']
        self.assertIn('not the ones its tape answers', moved['replay'])
        fewer = self.tape(args={'n': 5}, cursor=first['cursor'])['facts']
        self.assertIn('asked 5 questions and its tape answers 8', fewer['replay'])
        broken = self.tape(args={'n': 20}, cursor={'n': 2, 'hex': 'ff', 'sum': 0})
        self.assertIn('ValueError', broken['error'])

    def test_part_lookup_and_signature(self):
        def face(kind, area_cm2, c): return types.SimpleNamespace(geometry=types.SimpleNamespace(surfaceType=kind), area=area_cm2, centroid=Pt(*c))
        class Coll(list):
            count = property(len)
            def item(self, i): return self[i]
        def body(name, hole_x):
            faces = Coll([face(0, 12.0, (2, 1.5, 0)), face(1, 0.9425, (hole_x, 1.0, 0.5))])
            return types.SimpleNamespace(name=name, isSolid=True, volume=11.5, area=29.0, faces=faces, edges=Coll(range(12)), vertices=Coll(range(8)),
                                         boundingBox=types.SimpleNamespace(minPoint=Pt(0, 0, 0), maxPoint=Pt(4, 3, 1)))
        b1, surface = body('Body1', 1.0), types.SimpleNamespace(name='Surf', isSolid=False)
        comp = types.SimpleNamespace(id='c1', name='Bracket', bRepBodies=[surface, b1])
        occ = types.SimpleNamespace(fullPathName='Bracket:1', component=comp)
        other = types.SimpleNamespace(fullPathName='Cover:1', component=types.SimpleNamespace(id='c2', name='Cover', bRepBodies=[]))
        design = types.SimpleNamespace(rootComponent=types.SimpleNamespace(id='root', name='Root', allOccurrences=[other, occ], bRepBodies=[]))
        _, g = self.run_script([FakeDoc('Demo Assembly', 2)], 'def probe(job):\n    return {}\n')
        job = g['JOB']
        self.assertEqual(g['gate_body'](design, job), (occ, comp, b1))
        self.assertEqual(g['gate_occurrence'](design, dict(job, occurrence='Gone:1')), occ)                 # falls back to the component id
        with self.assertRaises(KeyError): g['gate_occurrence'](design, dict(job, occurrence='Gone:1', component_id='zz'))
        with self.assertRaises(KeyError): g['gate_body'](design, dict(job, body='Body9'))
        sig = g['gate_signature'](b1)
        self.assertEqual(len(sig), 64); self.assertEqual(sig, g['gate_signature'](body('Body1', 1.0)))
        self.assertNotEqual(sig, g['gate_signature'](body('Body1', 1.2)))                                    # the hole moved 2 mm
        self.assertEqual(g['gate_bbox'](b1), ((0.0, 0.0, 0.0), (40.0, 30.0, 10.0)))

    BIG = '''
class _F:
    def __init__(self, i, shift): self.area, self._c = 0.5 + i * 0.01, (i * 0.1 + shift, 1.0, 0.5)
    @property
    def centroid(self):
        READS.append(1)
        return adsk.core.Point3D.create(*self._c)
class _L(list):
    count = property(len)
    def item(self, i): return self[i]
class _B:
    name, isSolid, volume, area = "Body1", True, 11.5, 29.0
    def __init__(self, n, rev, shift=0.0):
        self.faces, self.edges, self.vertices = _L(_F(i, shift) for i in range(n)), _L(range(12)), _L(range(8))
        if rev: self.revisionId = rev
        self.boundingBox = type("BB", (), {"minPoint": adsk.core.Point3D.create(0, 0, 0), "maxPoint": adsk.core.Point3D.create(4, 3, 1)})()
READS = []
'''

    def test_a_signature_is_remembered_for_one_state_of_a_body_and_never_for_the_export(self):
        probe = self.BIG + '''
def probe(job):
    b = _B(30, job["args"]["rev"])
    first = gate_signature(b); n1 = len(READS)
    again = gate_signature(_B(30, job["args"]["rev"])); n2 = len(READS)                 # the same state, asked again: no face is read
    fresh = gate_signature(b, fresh=True); n3 = len(READS)                              # the export: every face, always
    edited = gate_signature(_B(30, job["args"]["rev"] + "+1", 0.2)); n4 = len(READS)    # a hole moved: Fusion gives a new revision id
    return {"facts": {"same": first == again == fresh, "moved": edited != first, "reads": [n1, n2 - n1, n3 - n2, n4 - n3]}, "sig": first}
'''
        self.addCleanup(sys.modules.pop, '_gate_signature_cache', None)
        env, _ = self.run_script([FakeDoc('Demo Assembly', 2)], probe, args={'rev': 'rev-%s' % id(self)})
        self.assertIsNone(env['error']); self.assertEqual(env['facts'], {'same': True, 'moved': True, 'reads': [30, 0, 30, 30]})
        env, _ = self.run_script([FakeDoc('Demo Assembly', 2)], probe, args={'rev': ''})    # Fusion gives no revision id: nothing is remembered
        self.assertEqual(env['facts']['reads'], [30, 30, 30, 30]); self.assertTrue(env['facts']['same'])

    def test_two_bodies_never_share_a_remembered_signature(self):
        # nothing says that a revision id is unique per body: the key also holds the component and the name of the body
        probe = self.BIG + '''
class _C:
    def __init__(self, cid): self.id = cid
def probe(job):
    rev = job["args"]["rev"]
    a = _B(30, rev); a.parentComponent = _C("c1")
    b = _B(30, rev, 0.2); b.parentComponent = _C("c2")                  # another component, the same revision id, a hole elsewhere
    c = _B(30, rev, 0.4); c.parentComponent = _C("c1"); c.name = "Body2"  # the same component, another body
    sa = gate_signature(a); n1 = len(READS)
    sb = gate_signature(b); n2 = len(READS)
    sc = gate_signature(c); n3 = len(READS)
    again = gate_signature(a); n4 = len(READS)
    return {"facts": {"all_differ": len({sa, sb, sc}) == 3, "same": again == sa, "reads": [n1, n2 - n1, n3 - n2, n4 - n3]}, "sig": sa}
'''
        self.addCleanup(sys.modules.pop, '_gate_signature_cache', None)
        env, _ = self.run_script([FakeDoc('Demo Assembly', 2)], probe, args={'rev': 'shared-%s' % id(self)})
        self.assertIsNone(env['error']); self.assertEqual(env['facts'], {'all_differ': True, 'same': True, 'reads': [30, 30, 30, 0]})

    def test_after_a_long_signature_the_unit_starts_its_work_in_a_call_of_its_own(self):
        probe = self.BIG + '''
def probe(job):
    return {"facts": {"measured": True, "left": gate_left(job)}, "sig": gate_signature(_B(170, job["args"]["rev"]))}
'''
        import time
        self.addCleanup(sys.modules.pop, '_gate_signature_cache', None)
        rev = 'long-%s' % id(self)
        # sent 12 s ago with a budget of 20: every face is read in this call, and less than half of the call is left after it
        first, _ = self.run_script([FakeDoc('Demo Assembly', 2)], probe, args={'rev': rev}, deadline_s=20, sent=time.time() - 12, cursor=3)
        self.assertEqual((first['error'], first['done'], first['cursor'], first['facts']), (None, False, 3, {'signing': {'faces_done': 170, 'faces': 170}}))
        second, _ = self.run_script([FakeDoc('Demo Assembly', 2)], probe, args={'rev': rev}, deadline_s=20, cursor=3)      # from the memory: all its time for the work
        self.assertEqual((second['done'], second['facts']['measured']), (True, True)); self.assertGreater(second['facts']['left'], 19.0)
        # with no revision id nothing is remembered, so the unit goes on in the same call: stopping would mean signing again for ever
        third, _ = self.run_script([FakeDoc('Demo Assembly', 2)], probe, args={'rev': ''}, deadline_s=20, sent=time.time() - 12)
        self.assertEqual((third['done'], third['facts']['measured']), (True, True))
        # and a small body is no reason to stop
        small = self.BIG + '''
def probe(job):
    return {"facts": {"measured": True}, "sig": gate_signature(_B(30, job["args"]["rev"]))}
'''
        env, _ = self.run_script([FakeDoc('Demo Assembly', 2)], small, args={'rev': rev + 's'}, deadline_s=20, sent=time.time() - 12)
        self.assertEqual(env['facts'], {'measured': True})

    def test_a_body_too_large_for_one_call_is_signed_over_several_and_the_unit_says_not_done(self):
        probe = self.BIG + '''
def probe(job):
    return {"facts": {"measured": True}, "sig": gate_signature(_B(170, job["args"]["rev"]))}
'''
        rev = 'big-%s' % id(self)
        self.addCleanup(sys.modules.pop, '_gate_signature_cache', None)                     # what the "Fusion session" remembered ends with the test
        envs = [self.run_script([FakeDoc('Demo Assembly', 2)], probe, args={'rev': rev}, deadline_s=0.5, cursor=7)[0] for _ in range(4)]
        self.assertEqual([e['error'] for e in envs], [None] * 4)
        self.assertEqual([(e['done'], e['cursor'], e['facts'].get('signing')) for e in envs[:3]],
                         [(False, 7, {'faces_done': 50, 'faces': 170}), (False, 7, {'faces_done': 100, 'faces': 170}), (False, 7, {'faces_done': 150, 'faces': 170})])
        self.assertEqual((envs[3]['done'], envs[3]['facts']), (True, {'measured': True})); self.assertEqual(len(envs[3]['sig']), 64)
        self.assertIsNone(envs[0]['sig'])                                                   # an unfinished signature is no signature
        whole, _ = self.run_script([FakeDoc('Demo Assembly', 2)], probe, args={'rev': 'other-%s' % id(self)}, deadline_s=30)
        self.assertEqual(whole['sig'], envs[3]['sig'])                                      # built in four calls or in one: the same
        env, _ = self.run_script([FakeDoc('Demo Assembly', 2)], probe, args={'rev': ''}, deadline_s=0.5)
        self.assertEqual(env['facts'], {'signing': {'faces_done': 50, 'faces': 170}})       # with no revision id the part done is kept by path and body


if __name__ == '__main__':
    unittest.main()
