"""Overhangs and supports decided by measurement: the bed face read from the saved .3mf (probes.project_bed), the slicer's
support per object against an owner's baseline (probes.project_slice, gate.py slice, gate.py owner baseline, the judge
print.slice_support_growth) and the geometry pre-check of what hangs (probes.mesh_overhang, print.overhang_angle).
No Fusion and no Bambu Studio: the projects are built here with bambu3mf, the slicer is tests/fake_slicer.py. Every
name and number is invented."""
import contextlib, io, json, math, os, re, shutil, sys, tempfile, unittest, zipfile
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
for d in (os.path.join(ROOT, 'tools', 'bambu'), os.path.dirname(os.path.abspath(__file__))):
    if d not in sys.path: sys.path.insert(0, d)
import gate, gate_attest, gate_common, gate_guard, gate_judges, gate_params, gate_plan, gate_registry, gate_slice
from probes import mesh_overhang, project_bed, project_consistency, project_slice
import bambu3mf, fake_slicer, meshid
import test_guard as G
from test_guard import quiet
from test_stamp import SIG1, SIG2, plate, write_stl

IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
PLATES = [('first plate', [('bracket', 'bracket.stl', False)]), ('second plate', [('cover', 'cover.stl', '+y')])]


def run(fn, *a, **kw):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        r = fn(*a, **kw)
    return r, out.getvalue()


def prism(profile, depth=10.0):
    """A closed mesh: the polygon `profile` [(x, z)] (counter-clockwise seen from -y) extruded along y from 0 to depth."""
    n = len(profile)
    V = [(x, 0.0, z) for x, z in profile] + [(x, depth, z) for x, z in profile]
    F = []
    idx = list(range(n))

    def cross(o, a, b): return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def inside(p, a, b, c): return cross(a, b, p) > 0 and cross(b, c, p) > 0 and cross(c, a, p) > 0
    ears = []
    while len(idx) > 3:
        for k in range(len(idx)):
            i, j, m = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = profile[i], profile[j], profile[m]
            if cross(a, b, c) <= 0 or any(inside(profile[q], a, b, c) for q in idx if q not in (i, j, m)):
                continue
            ears.append((i, j, m)); idx.pop(k); break
        else:
            raise ValueError('profile is not simple')
    ears.append(tuple(idx))
    for i, j, m in ears:                     # the y = 0 cap faces -y, the far one +y
        F.append((i, j, m)); F.append((n + i, n + m, n + j))
    for i in range(n):
        j = (i + 1) % n
        F.append((i, n + i, n + j)); F.append((i, n + j, j))
    return V, F


def outward(V, F):
    """The mesh with every triangle wound outward (signed volume positive)."""
    vol = sum(V[a][0] * (V[b][1] * V[c][2] - V[b][2] * V[c][1]) - V[a][1] * (V[b][0] * V[c][2] - V[b][2] * V[c][0]) + V[a][2] * (V[b][0] * V[c][1] - V[b][1] * V[c][0]) for a, b, c in F)
    return (V, F) if vol > 0 else (V, [(a, c, b) for a, b, c in F])


SHELF = [(0, 0), (10, 0), (10, 10), (30, 10), (30, 12), (0, 12)]                     # a shelf 20 long that hangs over air
KEEL = [(0, 0), (2, 0), (2, 8), (6, 4), (10, 8), (10, 12), (0, 12)]                   # two 45 deg faces meeting in a level keel 4 above the bed
CHAMFER = [(0, 0), (10, 0), (10, 5), (15, 10), (0, 10)]                               # a 45 deg chamfer under a ledge: at the limit, not over it


class Overhang(unittest.TestCase):
    def test_a_box_and_a_45_deg_chamfer_hang_nothing(self):
        for profile in ([(0, 0), (10, 0), (10, 5), (0, 5)], CHAMFER):
            m = mesh_overhang.measure(*outward(*prism(profile)))
            self.assertEqual((m['faces'], m['ridges'], m['bridges']), ([], [], []), profile)

    def test_a_shelf_over_air_is_a_flat_face_and_a_bridge_too_wide(self):
        m = mesh_overhang.measure(*outward(*prism(SHELF)), max_angle=45.0, max_bridge=6.0)
        self.assertEqual([(r['area'], r['flattest'], r['z']) for r in m['faces']], [(200.0, 90.0, [10.0, 10.0])])
        self.assertEqual([r['span'] for r in m['bridges']], [10.0])                      # 20 long and 10 deep: it must reach 10 at least
        self.assertEqual(m['faces'][0]['at'][2], 10.0); self.assertEqual(m['totals']['faces'], 1)
        self.assertEqual(mesh_overhang.measure(*outward(*prism(SHELF)), max_bridge=12.0)['bridges'], [])

    def test_the_keel_between_two_steep_faces_is_a_ridge(self):
        m = mesh_overhang.measure(*outward(*prism(KEEL)))
        self.assertEqual(m['faces'], [])                                                 # each face is at the limit, not over it
        self.assertEqual([(r['length'], r['flattest'], r['z']) for r in m['ridges']], [(10.0, 90.0, [4.0, 4.0])])
        tilted = [(x, y, z + 0.2 * y) for x, y, z in prism(KEEL)[0]]                    # the keel rises 2 over its 10: still far flatter than 45 deg
        r = mesh_overhang.measure(*outward(tilted, prism(KEEL)[1]))['ridges']
        self.assertEqual(len(r), 1); self.assertAlmostEqual(r[0]['flattest'], 90.0 - math.degrees(math.atan(0.2)), places=1)

    def test_the_bottom_on_the_bed_is_no_overhang(self):
        V, F = outward(*prism([(0, 0), (10, 0), (10, 5), (0, 5)]))
        self.assertEqual(mesh_overhang.measure([(x, y, z + 7.5) for x, y, z in V], F)['faces'], [])      # wherever the bed is


class Turning(unittest.TestCase):
    def test_a_rigid_copy_is_solved_and_a_mirror_is_not(self):
        V, F = plate()
        a = math.radians(30); c, s = math.cos(a), math.sin(a)
        turned = [(c * x - s * z + 5.0, y - 2.0, s * x + c * z + 1.0) for x, y, z in V]
        got = project_bed.solve(V, F, turned, F)
        self.assertEqual(got['method'], 'triangles in order'); self.assertLess(got['worst'], 1e-6)
        down = project_bed.down_in_stl(got['R'], project_bed._IDENTITY)
        self.assertAlmostEqual(down[0], -s, places=6); self.assertAlmostEqual(down[2], -c, places=6)
        chiral = [(x, y, 7.0) if (x, y, z) == (0.0, 0.0, 5.0) else (x, y, z) for x, y, z in V]     # one corner raised: no turn makes it its mirror
        self.assertIsNotNone(project_bed.solve(chiral, F, [(c * x - s * z, y, s * x + c * z) for x, y, z in chiral], F))
        self.assertIsNone(project_bed.solve(chiral, F, [(-x, y, z) for x, y, z in chiral], F))  # a mirror is another part
        self.assertIsNone(project_bed.solve(chiral, F, [(-x, y, z) for x, y, z in chiral], list(reversed(F))))
        self.assertIsNone(project_bed.solve(V, F, [(x * 1.01, y, z) for x, y, z in V], F))     # and so is a scaled one

    def test_reordered_triangles_on_a_quarter_turn_are_found_by_the_vertices(self):
        V, F = plate()
        q = [(x, -z, y) for x, y, z in V]
        got = project_bed.solve(V, F, q, list(reversed(F)))
        self.assertEqual(got['method'], 'quarter turn')
        self.assertEqual([round(c, 9) + 0.0 for c in project_bed.down_in_stl(got['R'], project_bed._IDENTITY)], [0.0, -1.0, 0.0])


class BedFace(G.Guarded):
    """The bed face of a part is measured on the saved project; the build table is asked only where nothing is placed."""

    def setUp(self):
        super().setUp()
        self.build(PLATES)
        for part, sig, name in (('demo/bracket', SIG1, 'bracket'), ('demo/cover', SIG2, 'cover')):
            self.census(part, sig, stl='stl/%s.stl' % name, matrix=IDENTITY, bbox=[[0.0, 0.0, 0.0], [30.0, 20.0, 5.0]])

    def frame(self, part='demo/cover'):
        return gate_plan.print_frame(self.d, part, gate_common.load_census(self.d)['parts'][part])

    def table(self, text):
        layout = gate_common.read_json(gate_common.gate_path(self.d, 'layout.json'))
        layout['projects'][0]['build_table'] = 'bambulab/build_3mf.py'
        gate_common.write_json(gate_common.gate_path(self.d, 'layout.json'), layout)
        with open(os.path.join(self.d, 'bambulab', 'build_3mf.py'), 'w') as f: f.write(text)

    def turn_item(self, name, transform):
        """What a turn in the slicer leaves behind: the build item of the object carries the turn, the vertices do not."""
        with zipfile.ZipFile(self.proj) as z: files = {n: z.read(n) for n in z.namelist()}
        oid = next(o for o, _, n in bambu3mf.objects(files['Metadata/model_settings.config'].decode()) if n == name)
        model = files['3D/3dmodel.model'].decode()
        model, n = re.subn(r'(<item objectid="%s"[^>]*?transform=")[^"]+' % oid, lambda m: m.group(1) + transform, model)
        self.assertEqual(n, 1)
        files['3D/3dmodel.model'] = model.encode()
        with zipfile.ZipFile(self.proj, 'w') as z:
            for k, v in files.items(): z.writestr(k, v)

    def test_the_saved_project_says_the_face_and_how_it_was_measured(self):
        f = self.frame()
        self.assertEqual(([c + 0.0 for c in f['up']], f['from'], f['why']), ([0.0, -1.0, 0.0], 'project', None))
        self.assertIn('bambulab/Demo.3mf: cover on plate "second plate", orient \'measured: face +y of the STL on the bed (triangles in order', f['source'])
        self.assertEqual(([c + 0.0 for c in self.frame('demo/bracket')['up']], self.frame('demo/bracket')['from']), ([0.0, 0.0, 1.0], 'project'))

    def test_a_turn_in_the_slicer_is_what_counts_not_the_build_table(self):
        self.table("PLATES = [('first plate', [('bracket', 'bracket.stl', False)]), ('second plate', [('cover', 'cover.stl', '+y')])]\n")
        self.turn_item('cover', '1 0 0 0 0 1 0 -1 0 90 90 10')                          # a quarter turn about x on the plate
        f = self.frame()
        self.assertEqual(([c + 0.0 for c in f['up']], f['from']), ([0.0, 0.0, 1.0], 'project'))      # the modelled bottom is on the bed now
        self.assertIn("face -z of the STL", f['source'])
        os.remove(self.proj)                                                             # no saved project: the table is asked
        f = self.frame()
        self.assertEqual(([c + 0.0 for c in f['up']], f['from']), ([0.0, -1.0, 0.0], 'table')); self.assertIn('bambulab/build_3mf.py', f['source'])

    def test_an_stl_exported_again_keeps_the_face_its_placed_mesh_was_measured_on(self):
        self.assertEqual(self.frame()['from'], 'project')
        self.stl('cover', d=5.0)                                                         # the new geometry is not swapped in yet
        f = self.frame()
        self.assertEqual(([c + 0.0 for c in f['up']], f['from']), ([0.0, -1.0, 0.0], 'earlier geometry')); self.assertIn('on the mesh still placed', f['source'])
        os.remove(gate_common.gate_path(self.d, 'run', 'bed_faces.json'))               # nothing on record: unknown, and said so
        f = self.frame()
        self.assertIsNone(f['up']); self.assertIn('no object of a saved .3mf project is stl/cover.stl turned as a rigid body', f['why'])

    def test_a_turn_after_the_export_moves_the_remembered_face_too(self):
        self.assertEqual(self.frame()['from'], 'project')                               # measured and remembered with its motion
        self.stl('cover', d=5.0)
        self.turn_item('cover', '1 0 0 0 0 1 0 -1 0 90 90 10')                          # then turned in the slicer
        f = self.frame()
        self.assertEqual(([c + 0.0 for c in f['up']], f['from']), ([0.0, 0.0, 1.0], 'earlier geometry'))     # the face of today, not of the memory
        reg = gate_registry.load_default()
        self.assertIsNone(gate_attest.fact_answer(self.d, reg, reg['rules']['print.bed_face'], 'demo/cover', SIG2))   # asked: the new geometry is not placed
        data = gate_common.read_json(gate_common.gate_path(self.d, 'run', 'bed_faces.json'))
        for rows in data.values():
            for r in rows: r.pop('R', None)                                             # a memory of an older gate, without the motion
        gate_common.write_json(gate_common.gate_path(self.d, 'run', 'bed_faces.json'), data)
        self.assertIsNone(self.frame()['up'])                                          # cannot follow a turn: not used

    def test_the_bed_face_question_is_answered_by_the_measurement(self):
        reg = gate_registry.load_default()
        got = gate_attest.fact_answer(self.d, reg, reg['rules']['print.bed_face'], 'demo/cover', SIG2)
        self.assertEqual((got['kind'], got['as'], got['value']), ('facts', 'label', reg['rules']['print.bed_face']['attest']['auto'][0]['label']))
        self.assertIn('measured on the saved project: bambulab/Demo.3mf: cover', got['proof'])
        os.remove(self.proj)
        self.assertIsNone(gate_attest.fact_answer(self.d, reg, reg['rules']['print.bed_face'], 'demo/cover', SIG2))      # asked again


def fake_cli(env):
    """probes.project_slice.run_cli with the fake slicer in this process."""
    def cli(exe, copy, out_dir, plate_no, timeout):
        with mock.patch.dict(os.environ, env, clear=False):
            fake_slicer.main(['fake', '--slice', str(plate_no), '--outputdir', out_dir, copy])
        return gate_common.read_json(os.path.join(out_dir, 'result.json'), {}), ''
    return cli


class GcodeReading(unittest.TestCase):
    def test_support_per_label_and_the_order_checked_by_the_heights(self):
        text = fake_slicer.gcode([(111, 'housing', 12.0), (122, 'lid', 4.0)], 0.2, {'housing': (60.0, 3.0)})
        parsed = project_slice.parse_gcode(io.StringIO(text))
        self.assertEqual((parsed['labels'], parsed['heights'], parsed['studio']), ([111, 122], [12.0, 4.0], 'BambuStudio 01.00.00.00'))
        self.assertEqual((parsed['by_label'][111]['support_e'], parsed['by_label'][111]['top_z']), (60.0, 3.0))
        self.assertEqual((parsed['by_label'][122]['support_e'], parsed['by_label'][122]['_cells']), (0.0, {}))     # walls, no support
        plate_res = {'objects': [{'name': 'housing', 'bbox': {'height': 12.0}}, {'name': 'lid', 'bbox': {'height': 4.0}}]}
        rows, how, problem = project_slice.map_objects(plate_res, parsed, 0.2)
        self.assertEqual(([(r['name'], r['support_e']) for r in rows], problem), ([('housing', 60.0), ('lid', 0.0)], None))
        self.assertIn('every height agrees', how)
        swapped = {'objects': list(reversed(plate_res['objects']))}                       # the order does not fit the heights: no numbers
        rows, how, problem = project_slice.map_objects(swapped, parsed, 0.2)
        self.assertIn('label order of the G-code does not fit', problem); self.assertEqual({r['support_e'] for r in rows}, {None})
        rows, how, problem = project_slice.map_objects(dict(plate_res, objects=plate_res['objects'] + [{'name': 'off', 'bbox': {'height': 3.0}}]), parsed, 0.2, {'housing', 'lid'})
        self.assertEqual((problem, [(r['name'], r['printed']) for r in rows]), (None, [('housing', True), ('lid', True), ('off', False)]))    # a switched-off object prints nothing

    def test_where_the_support_stands_is_kept_from_the_low_corner_of_the_walls(self):
        text = fake_slicer.gcode([(111, 'housing', 6.0, (30.0, 10.0))], 0.2, {'housing': (20.0, 4.0, [[12.0, 2.0, 18.0, 4.0]])})
        rows, how, problem = project_slice.map_objects({'objects': [{'name': 'housing', 'bbox': {'height': 6.0}}]}, project_slice.parse_gcode(io.StringIO(text)), 0.2)
        r = rows[0]
        self.assertEqual((problem, r['origin'], r['cells_cut']), (None, [10.0, 10.0], False))
        xs, ys = {c[0] for c in r['cells']}, {c[1] for c in r['cells']}
        self.assertEqual((min(xs), max(xs), min(ys), max(ys), {c[2] for c in r['cells']}), (12.5, 18.5, 2.5, 4.5, {4.0}))

    def test_support_under_no_label_on_a_shared_plate_is_not_guessed(self):
        text = fake_slicer.gcode([(111, 'housing', 12.0), (122, 'lid', 4.0)], 0.2) + '; CHANGE_LAYER\n; Z_HEIGHT: 0.4\n; FEATURE: Support\nG1 X1 Y1 E2.5\n'
        rows, how, problem = project_slice.map_objects({'objects': [{'name': 'housing', 'bbox': {'height': 12.0}}, {'name': 'lid', 'bbox': {'height': 4.0}}]},
                                                       project_slice.parse_gcode(io.StringIO(text)), 0.2)
        self.assertIn('2.5 mm of support printed under no object label', problem)
        one = fake_slicer.gcode([(111, 'housing', 12.0)], 0.2) + '; CHANGE_LAYER\n; Z_HEIGHT: 0.4\n; FEATURE: Support interface\nG1 X1 Y1 E2.5\n'
        rows, how, problem = project_slice.map_objects({'objects': [{'name': 'housing', 'bbox': {'height': 12.0}}]}, project_slice.parse_gcode(io.StringIO(one)), 0.2)
        self.assertEqual((rows[0]['support_e'], rows[0]['interface_e'], problem), (2.5, 2.5, None))


class Slicing(G.Guarded):
    """gate.py slice on a gated project, the owner's baseline, and the judge at gate.py verify."""

    def setUp(self):
        super().setUp()
        self.build(PLATES)
        self.entry = gate_common.load_layout(self.d)['projects'][0]

    def slice(self, support=None, plates=None):
        return project_slice.slice_project(self.d, self.entry, plates, cli=fake_cli({'FAKE_SUPPORT': json.dumps(support or {})}))

    def facts(self):
        rows = [{'name': o['name'], 'id': o['id'], 'plate': o['plate'], 'plate_index': o['plate_index'], 'printable': o['printable'], 'pose': o['pose'], 'part': None}
                for o in project_bed.read_placed(self.proj)['objects']]
        return project_slice.facts_for(self.d, 'main', rows, self.proj)

    def judged(self):
        reg = gate_registry.load_default(); rule = reg['rules']['print.slice_support_growth']
        facts = {'project': 'main', 'file': 'bambulab/Demo.3mf', 'exists': True, 'slice': self.facts()}
        return gate_judges.run_judge(rule, facts, gate_params.values(gate_params.resolve(reg), rule['params']), {'part': 'main:', 'rule': rule['id']})

    def owner(self, *argv):
        G.shutil.copyfile(os.path.join(ROOT, 'templates', 'gate', 'settings.json'), self.claude_settings())
        return run(gate.main, ['--root', self.d, 'owner', 'baseline'] + list(argv))

    def claude_settings(self):
        os.makedirs(os.path.join(self.d, '.claude'), exist_ok=True)
        return os.path.join(self.d, '.claude', 'settings.json')

    def test_never_sliced_then_sliced_on_a_copy_and_the_meshes_tied_by_their_pose(self):
        self.assertEqual([f['verdict'] for f in self.judged()], ['NOT_RUN']); self.assertIn('never sliced by the gate', self.judged()[0]['text'])
        before = open(self.proj, 'rb').read()
        rec = self.slice({'cover': (40.0, 2.0)})
        self.assertEqual(open(self.proj, 'rb').read(), before)                           # only the copy is sliced
        self.assertEqual([n for n in os.listdir(gate_common.gate_path(self.d, 'run', 'slice')) if n.endswith('.work') or n.endswith('.lock')], [])   # the G-code is gone
        self.assertEqual(sorted(rec['plates']), ['1', '2'])
        self.assertEqual([(o['name'], o['support_e']) for p in ('1', '2') for o in rec['plates'][p]['objects']], [('bracket', 0.0), ('cover', 40.0)])
        sl = {o['name']: o for o in self.facts()['objects']}
        self.assertEqual((sl['bracket']['sliced']['support_e'], sl['cover']['sliced']['support_e'], sl['cover']['baseline']), (0.0, 40.0, None))
        got = sorted((f['verdict'], f['part']) for f in self.judged())
        self.assertEqual(got, [('INFO', 'main:bracket'), ('NOT_RUN', 'main:cover')])     # the cover has support and no baseline yet
        self.assertEqual(project_slice.changed_plates(self.d, self.entry), [])

    def test_the_owner_records_the_baseline_and_a_growth_fails(self):
        self.slice({'cover': (40.0, 2.0)})
        code, out = self.owner('main', 'cover', '--quote', 'the cover may have those supports')
        self.assertEqual(code, 0, out); self.assertIn('baseline of main "cover": 40.0 mm of support up to z 2.0 (was none)', out)
        base = gate_common.read_json(project_slice.baseline_path(self.d))['projects']['main']['cover']
        self.assertEqual((base['support_e'], base['top_z'], base['quote']), (40.0, 2.0, 'the cover may have those supports'))
        self.assertEqual(sorted(f['verdict'] for f in self.judged()), ['INFO'])
        self.slice({'cover': (60.5, 2.0)})                                               # 20.5 more: more than 20 mm over the baseline
        f = [f for f in self.judged() if f['verdict'] != 'INFO']
        self.assertEqual([(x['verdict'], x['measure'], x['limit']) for x in f], [('FAIL', 60.5, 44.0)])
        self.slice({'cover': (41.0, 6.0)})                                               # as much, but it reaches higher
        self.assertEqual([(x['verdict'], x['measure'], x['limit']) for x in self.judged() if x['verdict'] != 'INFO'], [('FAIL', 6.0, 2.31)])

    def test_the_baseline_takes_no_typed_number_and_only_a_current_slice(self):
        code, out = self.owner('main', 'cover', '--quote', 'record it')
        self.assertEqual(code, 4)                                                        # nothing sliced yet
        self.slice({'cover': (40.0, 2.0)})
        self.stl('cover', d=5.0); self.build(PLATES)                                     # the mesh changed after the slice
        self.assertEqual([p for p in project_slice.changed_plates(self.d, self.entry)], [2])
        code, out = self.owner('main', 'cover', '--quote', 'record it')
        self.assertEqual(code, 4); self.assertFalse(os.path.exists(project_slice.baseline_path(self.d)))
        self.assertIn('the mesh changed since plate 2 was sliced', [f['text'] for f in self.judged() if f['part'] == 'main:cover'][0])
        os.remove(self.claude_settings())
        self.slice({'cover': (40.0, 2.0)}, plates=[2])
        code, out = run(gate.main, ['--root', self.d, 'owner', 'baseline', 'main', 'cover', '--quote', 'record it'])
        self.assertEqual(code, 4)                                                        # no prompt in front of the owner: refused

    def edit(self, fn):
        with zipfile.ZipFile(self.proj) as z: files = {n: z.read(n) for n in z.namelist()}
        fn(files)
        with zipfile.ZipFile(self.proj, 'w') as z:
            for k, v in files.items(): z.writestr(k, v)

    def test_a_turn_on_the_plate_or_a_support_setting_makes_the_slice_another_one(self):
        self.slice({'cover': (40.0, 2.0)})
        self.assertEqual(self.owner('main', 'cover', '--quote', 'fine')[0], 0)
        self.assertEqual([f['verdict'] for f in self.judged()], ['INFO'])

        def turn(files):                                                                 # a quarter turn about x in the build item, as a hand turn leaves it
            oid = next(o for o, _, n in bambu3mf.objects(files['Metadata/model_settings.config'].decode()) if n == 'cover')
            files['3D/3dmodel.model'] = re.sub(r'(<item objectid="%s"[^>]*?transform=")[^"]+' % oid, lambda m: m.group(1) + '1 0 0 0 0 1 0 -1 0 90 90 10',
                                               files['3D/3dmodel.model'].decode()).encode()
        self.edit(turn)
        self.assertEqual(project_slice.changed_plates(self.d, self.entry), [2])          # the pose of the vertices is the same, the slice is not
        got = [f for f in self.judged() if f['part'] == 'main:cover']
        self.assertEqual([f['verdict'] for f in got], ['NOT_RUN']); self.assertIn('turned on its plate, or a support setting changed', got[0]['text'])
        self.slice({'cover': (40.0, 2.0)}, plates=[2])
        self.assertEqual(project_slice.changed_plates(self.d, self.entry), [])

        def setting(files):
            ps = json.loads(files['Metadata/project_settings.config']); ps['support_threshold_angle'] = '15'
            files['Metadata/project_settings.config'] = json.dumps(ps).encode()
        self.edit(setting)
        self.assertEqual(project_slice.changed_plates(self.d, self.entry), [1, 2])       # every plate is sliced under the new threshold
        self.assertEqual({f['verdict'] for f in self.judged()}, {'NOT_RUN'})

    def test_a_slice_that_cannot_run_or_runs_out_of_time_is_a_plate_error(self):
        def late(exe, copy, out_dir, plate_no, timeout):
            raise project_slice.GateError('the slicer did not finish plate %s within %d s' % (plate_no, timeout))
        rec = project_slice.slice_project(self.d, self.entry, [1, 2], cli=late, timeout=5)
        self.assertEqual(sorted(rec['plates']), ['1', '2'])
        self.assertIn('did not finish plate 1 within 5 s', rec['plates']['1']['error'])
        self.assertIn('the last slice of plate 2', [f['text'] for f in self.judged() if f['part'] == 'main:cover'][0])

    def test_two_slices_keep_each_others_plates(self):
        self.slice({'cover': (40.0, 2.0)}, plates=[2])
        self.slice({}, plates=[1])
        rec = project_slice.load_record(self.d, 'main')
        self.assertEqual(sorted(rec['plates']), ['1', '2'])
        lock = gate_common.gate_path(self.d, 'run', 'slice', 'main.lock')
        open(lock, 'w').close()
        with self.assertRaises(project_slice.GateError):
            project_slice._locked_update(self.d, 'main', lambda r: None, wait_s=0.2)
        os.utime(lock, (1, 1))                                                           # a lock left by a process that died is taken over
        project_slice._locked_update(self.d, 'main', lambda r: None, wait_s=0.2)
        self.assertFalse(os.path.exists(lock))

    def test_gate_slice_runs_the_cli_named_by_bambu_studio_and_writes_a_proposal(self):
        exe = fake_slicer.make(os.path.join(self.d, 'fake-slicer'))
        with mock.patch.dict(os.environ, {'BAMBU_STUDIO': exe, 'FAKE_SUPPORT': json.dumps({'cover': [12.0, 1.4]})}):
            code, out = run(gate.main, ['--root', self.d, 'slice', '--all'])
        self.assertEqual(code, 1, out)                                                   # the cover's support has no baseline
        self.assertIn('plate 2 cover: support 12.0 mm (filament), up to z 1.4', out); self.assertIn('NOT_RUN cover gets 12 mm of support', out)
        prop = gate_common.read_json(gate_common.gate_path(self.d, 'proposals', 'support_baseline.json'))
        self.assertEqual(prop['projects']['main']['cover']['command'], 'gate.py owner baseline main "cover" --quote "..."')
        self.assertNotIn('bracket', prop['projects']['main'])                            # no support, nothing to decide
        with mock.patch.dict(os.environ, {'BAMBU_STUDIO': exe}):
            code, out = run(gate.main, ['--root', self.d, 'slice'])
        self.assertIn('every plate holds the meshes of the last slice', out)
        with mock.patch.dict(os.environ, {'BAMBU_STUDIO': exe, 'FAKE_SLICE_FAIL': '1'}):
            code, out = run(gate.main, ['--root', self.d, 'slice', '--plates', '1'])
        self.assertEqual(code, 4); self.assertIn('the slicer gave no G-code for this plate', out)
        self.assertIn('the slicer gave no G-code', ' '.join(f['text'] for f in self.judged()))       # and the failed plate is not judged from the old numbers
        with mock.patch.dict(os.environ, {'BAMBU_STUDIO': os.path.join(self.d, 'missing')}), mock.patch.object(project_slice, 'studio_exe', return_value=None):
            code, out = run(gate.main, ['--root', self.d, 'slice', '--all'])
        self.assertEqual(code, 4)

    def test_verify_holds_both_rules_on_the_project(self):
        reg = gate_registry.load_default()
        out = gate_guard.verify_project(self.d, reg, gate_params.resolve(reg, None, None), self.entry)
        rules = {f['rule'] for f in out['open'] + out['info']}
        self.assertIn('print.slice_support_growth', rules)
        self.assertEqual(out['facts']['overhangs']['max_angle'], 45.0)
        self.assertEqual([o['totals']['faces'] for o in out['facts']['overhangs']['objects']], [1, 0][::-1])    # the cover lies on its side: its hole has a flat roof
        self.assertEqual(out['facts']['overhangs']['objects'][1]['faces'][0]['flattest'], 90.0)
        self.slice()
        out = gate_guard.verify_project(self.d, reg, gate_params.resolve(reg, None, None), self.entry)
        self.assertEqual([(f['rule'], f['part']) for f in out['open'] if f['rule'].startswith('print.')], [('print.overhang_angle', 'main:cover')])
        self.assertIn('Whether the slicer supports it is not known: the project settings do not hold enable_support',
                      [f['text'] for f in out['open'] if f['rule'] == 'print.overhang_angle'][0])


class OverhangOnTheProject(G.Guarded):
    """print.overhang_angle at verify: a shelf that hangs passes with supports on, fails with them off."""

    def setUp(self):
        super().setUp()
        write_stl(os.path.join(self.stls, 'shelf.stl'), *outward(*prism(SHELF)))
        self.build([('shelf plate', [('shelf', 'shelf.stl', False)])])
        self.entry = gate_common.load_layout(self.d)['projects'][0]
        self.reg = gate_registry.load_default()

    def settings(self, **values):
        with zipfile.ZipFile(self.proj) as z: files = {n: z.read(n) for n in z.namelist()}
        ps = json.loads(files['Metadata/project_settings.config']); ps.update(values)
        files['Metadata/project_settings.config'] = json.dumps(ps).encode()
        with zipfile.ZipFile(self.proj, 'w') as z:
            for k, v in files.items(): z.writestr(k, v)

    def rows(self):
        out = gate_guard.verify_project(self.d, self.reg, gate_params.resolve(self.reg, None, None), self.entry)
        return [(f['verdict'], f['measure'], f['limit']) for f in out['open'] + out['info'] if f['rule'] == 'print.overhang_angle']

    def slice(self, support):
        project_slice.slice_project(self.d, self.entry, None, cli=fake_cli({'FAKE_SUPPORT': json.dumps(support)}))

    def texts(self):
        out = gate_guard.verify_project(self.d, self.reg, gate_params.resolve(self.reg, None, None), self.entry)
        return [f['text'] for f in out['open'] + out['info'] if f['rule'] == 'print.overhang_angle']

    def test_supports_decide(self):
        self.assertEqual({v for v, _, _ in self.rows()}, {'NOT_RUN'})                      # the template says nothing about supports
        self.settings(enable_support='1', support_type='tree(auto)', different_settings_to_system=['enable_support;support_type', '', ''])
        self.assertEqual({v for v, _, _ in self.rows()}, {'NOT_RUN'})                      # supports are asked for, the slice is owed
        self.assertIn('whether the slicer puts support under it is not measured', self.texts()[0])
        self.slice({'shelf': [30.0, 9.8, [[10.0, 0.0, 30.0, 10.0]]]})                       # support under the shelf, up to it
        self.assertEqual(self.rows(), [('INFO', 90.0, 45.0), ('INFO', 10.0, 6.0)])
        self.slice({'shelf': [30.0, 9.8, [[0.0, 0.0, 4.0, 10.0]]]})                         # support elsewhere: the shelf is left bare
        self.assertEqual(self.rows(), [('FAIL', 90.0, 45.0), ('FAIL', 10.0, 6.0)])
        self.assertIn('the slice leaves 1 of 1 such region(s) with no support under them', self.texts()[0])
        self.slice({'shelf': [30.0, 3.0, [[10.0, 0.0, 30.0, 10.0]]]})                       # under it, but only up to z 3 (support from the bed that stops)
        self.assertEqual({v for v, _, _ in self.rows()}, {'FAIL'})
        self.settings(support_on_build_plate_only='1')                                     # a support setting changed: the slice is another one
        self.assertEqual({v for v, _, _ in self.rows()}, {'NOT_RUN'})
        self.slice({})                                                                      # the slicer puts none under it at all
        self.assertEqual({v for v, _, _ in self.rows()}, {'FAIL'})
        self.assertIn('the slicer puts no support under it at all', self.texts()[0]); self.assertIn('on the build plate only', self.texts()[0])
        self.settings(enable_support='0')
        self.assertEqual(self.rows(), [('FAIL', 90.0, 45.0), ('FAIL', 10.0, 6.0)])
        self.settings(enable_support='1', different_settings_to_system=['support_type', '', ''])
        self.assertEqual({v for v, _, _ in self.rows()}, {'NOT_RUN'})                      # not listed: the preset's value counts, and it is not known


class BeforeTheWrite(G.Guarded):
    """The guard holds the archive of a save against the slice record; with the owner's "slice_before_write" it slices the
    changed plates first, and in enforce mode a support that grew refuses the save."""
    mode = 'enforce'

    def setUp(self):
        super().setUp()
        self.stamp_both()
        self.exe = fake_slicer.make(os.path.join(self.d, 'fake-slicer'))

    def switch(self, on=True):
        p = gate_common.read_json(gate_common.gate_path(self.d, 'project.json')); p['slice_before_write'] = on
        gate_common.write_json(gate_common.gate_path(self.d, 'project.json'), p)

    def save(self, support):
        with mock.patch.dict(os.environ, {'BAMBU_STUDIO': self.exe, 'FAKE_SUPPORT': json.dumps(support)}):
            return quiet(self.build, PLATES)

    def test_a_grown_support_refuses_the_save_and_the_file_stays(self):
        self.save({'cover': [40.0, 2.0]})                                                   # switch off: nothing is sliced inside the save
        self.assertFalse(os.path.exists(project_slice.record_path(self.d, 'main')))
        self.switch()
        os.remove(self.proj); self.save({'cover': [40.0, 2.0]})                             # a new file: every plate is sliced, no baseline yet: written
        rec = project_slice.load_record(self.d, 'main')
        self.assertEqual([o['support_e'] for o in rec['plates']['2']['objects']], [40.0])
        G.shutil.copyfile(os.path.join(ROOT, 'templates', 'gate', 'settings.json'), os.path.join(os.makedirs(os.path.join(self.d, '.claude'), exist_ok=True) or self.d, '.claude', 'settings.json'))
        self.assertEqual(run(gate.main, ['--root', self.d, 'owner', 'baseline', 'main', 'cover', '--quote', 'that much is fine'])[0], 0)
        self.stamp('demo/cover', SIG2, 'cover', d=5.0)                                     # a new cover, stamped
        with self.assertRaises(G.GuardRefusal) as c:
            self.save({'cover': [90.0, 2.0]})
        self.assertIn('FAIL cover: the slicer now puts 90 mm of support', str(c.exception))
        self.assertEqual(self.objects(), [])                     # the build put its empty template first; the parts were refused
        self.save({'cover': [41.0, 2.0]})                                                   # within its baseline: written
        self.assertEqual(self.objects(), ['bracket', 'cover'])

    def test_no_slicer_on_this_machine_refuses_in_enforce_and_the_memory_keeps_the_file_on_disk(self):
        self.switch()
        with mock.patch.object(project_slice, 'studio_exe', return_value=None), mock.patch.dict(os.environ, {'BAMBU_STUDIO': ''}):
            with self.assertRaises(G.GuardRefusal) as c:
                quiet(self.build, PLATES)
        self.assertIn('NOT SLICED plate 1: Bambu Studio is not found', str(c.exception))

    def test_a_plate_that_cannot_be_sliced_refuses_in_enforce_only(self):
        self.switch()
        with mock.patch.dict(os.environ, {'FAKE_SLICE_FAIL': '1'}):
            with self.assertRaises(G.GuardRefusal) as c:
                self.save({})
        self.assertIn('NOT SLICED plate 1', str(c.exception))
        self.set_project('observe'); self.switch()
        with mock.patch.dict(os.environ, {'FAKE_SLICE_FAIL': '1'}):
            self.save({})
        self.assertTrue(os.path.exists(self.proj))



class PlacedWallsAgain(G.Guarded):
    """A placed mesh whose walls were measured over a share of its surface is measured again by a call with more time."""

    def setUp(self):
        super().setUp()
        write_stl(os.path.join(self.stls, 'shelf.stl'), *outward(*prism(SHELF)))
        self.build([('shelf plate', [('shelf', 'shelf.stl', False)])])

    def test_a_partial_measurement_is_finished_by_a_longer_call(self):
        from probes import placed_walls, stl_walls
        rows = [{'name': o['name'], 'id': o['id'], 'plate': o['plate'], 'plate_index': o['plate_index'], 'pose': o['pose'], 'part': None}
                for o in project_bed.read_placed(self.proj)['objects']]
        calls = []

        def fake(stl, under, budget_s=None, seed=None):
            calls.append(budget_s)
            return {'regions': [], 'coverage': 0.6 if len(calls) == 1 else 1.0, 'report_under': under}
        with mock.patch.object(stl_walls, 'measure', side_effect=fake):
            first = placed_walls.facts(self.d, self.proj, rows, 1.2, budget_s=20.0)
            again = placed_walls.facts(self.d, self.proj, rows, 1.2, budget_s=20.0)      # no more time than the first: kept as it is
            longer = placed_walls.facts(self.d, self.proj, rows, 1.2, budget_s=120.0)
        self.assertEqual([o['walls']['coverage'] for o in (first['objects'][0], again['objects'][0], longer['objects'][0])], [0.6, 0.6, 1.0])
        self.assertEqual([round(c) for c in calls], [20, 120])
        with mock.patch.dict(os.environ, {'GATE_WALLS_BUDGET': '120'}):
            self.assertEqual(project_consistency._walls_budget(), 120.0)


class OverhangsBeforeTheWrite(G.Guarded):
    """The pre-check runs on the objects a save brings: supports off under what hangs is loud, and refused in enforce."""

    def setUp(self):
        super().setUp()
        write_stl(os.path.join(self.stls, 'shelf.stl'), *outward(*prism(SHELF)))
        self.build([('shelf plate', [('shelf', 'shelf.stl', False)])])
        self.entry = gate_common.load_layout(self.d)['projects'][0]

    def archive(self, **values):
        with zipfile.ZipFile(self.proj) as z: files = {n: z.read(n) for n in z.namelist()}
        ps = json.loads(files['Metadata/project_settings.config']); ps.update(values)
        files['Metadata/project_settings.config'] = json.dumps(ps).encode()
        return files

    def test_supports_off_under_a_shelf_is_loud_and_refused_in_enforce(self):
        files = self.archive(enable_support='0', different_settings_to_system=['enable_support', '', ''])
        got = gate_slice.overhangs_before_write(self.d, self.proj, files, 'observe', self.entry, ['shelf'])
        self.assertEqual([f['verdict'] for f in got['open']], ['FAIL', 'FAIL']); self.assertIn('hang over air', got['line'])
        with self.assertRaises(gate_slice.SliceRefusal):
            gate_slice.overhangs_before_write(self.d, self.proj, files, 'enforce', self.entry, ['shelf'])
        on = self.archive(enable_support='1', different_settings_to_system=['enable_support', '', ''])
        self.assertEqual(gate_slice.overhangs_before_write(self.d, self.proj, on, 'enforce', self.entry, ['shelf'])['open'], [])   # owed to the slice: verify says it
        self.assertEqual(gate_slice.overhangs_before_write(self.d, self.proj, files, 'enforce', self.entry, ['other'])['open'], [])   # not brought by this save


if __name__ == '__main__':
    unittest.main()
