"""The coupons a target needs (gate.py coupons), and the table rows a project records of its own (gate/owner/tables.json).

A scratch project with one part whose stored census holds an insert pocket of the table, a magnet pocket and a pin hole;
nothing here needs Fusion or Bambu Studio. Every name and number is invented."""
import collections, contextlib, io, json, os, shutil, struct, sys, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
import gate, gate_common, gate_coupons, gate_params, gate_registry
from gate_registry import RegistryError

PART, SIG = 'demo/bracket', 'a1a1a1a1a1a1a1a1'
TARGET = {'nozzle': 0.4, 'line_width': 0.42, 'material': 'PETG', 'layer_height': 0.2, 'initial_layer_print_height': 0.2, 'xy_hole_compensation': 0.1}
PRINT = {'nozzle': 0.4, 'material': 'PETG', 'xy_hole_compensation': 0.1, 'layer_height': 0.2, 'source': 'coupon plate, tried with the inserts', 'date': '2031-02-03'}
INSERT = {'id': 'h1', 'anchor': {'kind': 'insert_pocket', 'axis': [0, 0, 1], 'point': [10.0, 0.0, 8.0]},
          'segments': [{'d': 7.5, 'len': 0.54}, {'d': 4.4, 'len': 1.5}, {'d': 3.6, 'len': 5.0}], 'depth': 7.04, 'through': False, 'floor': 1.0, 'exit': None,
          'class': {'table': 'inserts', 'kind': 'M3x4x5', 'role': 'insert_pocket', 'match': 'exact'}, 'coaxial': [{'name': 'ref HW / insert M3x4x5', 'kind': 'hardware', 'part': None}]}
MAGNET = {'id': 'h2', 'anchor': {'kind': 'hole', 'axis': [0, 0, 1], 'point': [-10.0, 0.0, 8.0]}, 'segments': [{'d': 4.2, 'len': 2.1}], 'depth': 2.1,
          'through': False, 'floor': 2.0, 'exit': None, 'class': None, 'coaxial': []}
PIN = {'id': 'h3', 'anchor': {'kind': 'hole', 'axis': [0, 0, 1], 'point': [0.0, 10.0, 8.0]}, 'segments': [{'d': 3.05, 'len': 8.0}], 'depth': 8.0,
       'through': True, 'floor': None, 'exit': None, 'class': None, 'coaxial': [{'name': 'ref HW / dowel D3 x 16', 'kind': 'hardware', 'part': None}]}


def closed(tris):
    """Every edge of the mesh is used once in each direction: one closed shell, normals the same way round."""
    key = lambda p: tuple(round(c, 5) for c in p)
    edges = collections.Counter((key(t[i]), key(t[(i + 1) % 3])) for t in tris for i in range(3))
    return all(n == 1 and edges.get((b, a)) == 1 for (a, b), n in edges.items())


def stl_triangles(path):
    with open(path, 'rb') as f:
        data = f.read()
    n = struct.unpack('<I', data[80:84])[0]
    return [tuple(tuple(struct.unpack('<12fH', data[84 + 50 * i:84 + 50 * (i + 1)])[3 + 3 * k:6 + 3 * k]) for k in range(3)) for i in range(n)]


class Project(unittest.TestCase):
    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.d, True)
        gate_common.write_json(gate_common.gate_path(self.d, 'project.json'), {'mode': 'observe', 'not_printed': ['^ref '], 'targets': {'main': TARGET}})
        gate_common.write_json(gate_common.census_path(self.d), {'docs': {}, 'parts': {PART: {'sig': SIG, 'doc': 'demo', 'name': 'Bracket', 'source': 'fusion'}}})
        gate_common.save_probe_facts(self.d, PART, SIG, {'job': 'u-1', 'doc': 'Demo', 'doc_version': 1, 'part': PART, 'sig': SIG, 'probe': 'fusion_census',
                                                         'facts': {'holes': [INSERT, MAGNET, PIN], 'openings': [], 'counts': {}}, 'done': True, 'cursor': None,
                                                         'elapsed_s': 0.1, 'error': None})
        self.addCleanup(gate_registry.use_root, None)

    def tables(self, data):
        gate_common.write_json(gate_common.gate_path(self.d, 'owner', 'tables.json'), data)

    def coupons(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = gate.main(['--root', self.d, 'coupons', '--for', 'main'])
        return code, out.getvalue()

    def needs(self):
        reg = gate_registry.load_default(self.d)
        resolved = gate_params.resolve(reg, gate_params.load_overrides(self.d), TARGET)
        return {c['id']: c for c in gate_coupons.needs(self.d, reg, resolved, 'main')[1]}


class Coupons(Project):
    def test_every_fit_the_part_relies_on_gets_its_coupon(self):
        have = self.needs()
        self.assertEqual(sorted(have), ['insert_M3x4x5', 'pin_3_pin_slip_extra', 'pocket_magnet'])
        self.assertEqual(have['pin_3_pin_slip_extra']['holes'], [3.0, 3.05, 3.1])            # the slip fit and one step either side
        self.assertEqual(have['pocket_magnet']['holes'], [4.15, 4.2, 4.25])
        self.assertEqual([s[0] for s in have['insert_M3x4x5']['stack']], [7.5, 4.4, 3.6])     # the profile of the table row

    def test_the_command_writes_closed_meshes_and_says_how_to_record_them(self):
        code, out = self.coupons()
        self.assertEqual(code, 0, out); self.assertIn('3 coupon(s) in gate/proposals/coupons/main', out)
        folder = gate_common.gate_path(self.d, 'proposals', 'coupons', 'main')
        listing = gate_common.read_json(os.path.join(folder, 'coupons.json'))
        self.assertEqual(listing['target'], TARGET)
        files = [f['stl'] for c in listing['coupons'] for f in c['files']]
        self.assertEqual(len(files), 7)                                                      # 1 insert block, 3 magnet and 3 pin blocks
        for name in files:
            tris = stl_triangles(os.path.join(folder, name))
            with self.subTest(stl=name):
                self.assertTrue(closed(tris)); self.assertGreater(gate_coupons.volume(tris), 0); self.assertAlmostEqual(min(p[2] for t in tris for p in t), 0.0, 4)
        self.assertFalse(os.path.exists(gate_common.gate_path(self.d, 'owner')))            # a proposal: nothing of the owner is written
        self.assertTrue(all('record' in c for c in listing['coupons']))

    def test_a_print_the_owner_recorded_takes_the_coupon_away(self):
        self.tables({'inserts': {'measured_under': {'M3x4x5': [PRINT]}}})
        self.assertNotIn('insert_M3x4x5', self.needs())
        self.tables({'inserts': {'measured_under': {'M3x4x5': [dict(PRINT, layer_height=0.3)]}}})       # printed under another layer height
        self.assertIn('insert_M3x4x5', self.needs())

    def test_the_seat_coupon_is_a_tapered_bore_and_a_tapered_post(self):
        c = {'id': 'seat_608', 'kind': 'seat', 'd': 8.0, 'D': 22.0, 'B': 7.0, 'taper': 0.25, 'lead_in': 0.3}
        got = gate_coupons.meshes(c)
        self.assertEqual([n for n, _t, _w in got], ['seat_608_bore.stl', 'seat_608_post.stl'])
        for name, tris, what in got:
            with self.subTest(part=name):
                self.assertTrue(closed(tris)); self.assertGreater(gate_coupons.volume(tris), 0)
        self.assertIn('D22 at its stop, D22.25 at its mouth', got[0][2]); self.assertIn('D8 where the ring enters, D8.25 at the base', got[1][2])

    def test_no_target_no_coupon(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(gate.main(['--root', self.d, 'coupons', '--for', 'other']), 4)
        self.assertIn('declares no target', out.getvalue())


class ProjectTables(Project):
    def test_the_project_stocks_its_own_lengths_and_measures_its_bearings(self):
        self.tables({'pins': {'sizes': {'3': {'lengths': [12, 16]}, '7': {'lengths': [20]}}}, 'screws': {'stock_lengths': {'M3': [8, 10, 12, 18]}},
                     'bearings': {'measured': {'608': {'d': 8.0, 'D': 22.02, 'B': 7.1, 'source': 'calliper on the batch in hand', 'date': '2031-02-03'}}},
                     '_note': 'a key that starts with _ is a comment'})
        reg = gate_registry.load_default(self.d)
        t = reg['tables']
        self.assertEqual(t['pins']['sizes']['3']['lengths'], [12.0, 16.0]); self.assertEqual(t['pins']['sizes']['7'], {'d': 7.0, 'lengths': [20.0], 'stock': 'project'})
        self.assertEqual(t['screws']['stock_lengths']['M3'], [8.0, 10.0, 12.0, 18.0]); self.assertEqual(t['screws']['stock_lengths']['M4'], gate_registry.load()['tables']['screws']['stock_lengths']['M4'])
        self.assertEqual((t['bearings']['sizes']['608']['B'], t['bearings']['sizes']['608']['measured']['date']), (7.1, '2031-02-03'))
        self.assertEqual(reg['table_overrides'], 'gate/owner/tables.json'); self.assertNotEqual(reg['sha256'], gate_registry.load()['sha256'])

    def test_a_row_that_cannot_be_read_stops_the_gate(self):
        for bad, said in (({'pockets': {}}, 'cannot be set by a project'), ({'pins': {'sizes': {'3': {'lengths': []}}}}, 'a pin row is'),
                          ({'bearings': {'measured': {'608': {'d': 8, 'D': 22, 'B': 7}}}}, '"source" must say'),
                          ({'bearings': {'measured': {'9999': {'d': 8, 'D': 22, 'B': 7, 'source': 's', 'date': '2031-02-03'}}}}, 'no row of the bearings table'),
                          ({'inserts': {'measured_under': {'M3x4x5': [dict(PRINT, nozzle=None)]}}}, 'missing: nozzle'),
                          ({'screws': {'stock_lengths': {'3': [8]}}}, 'a stock row is')):
            self.tables(bad)
            with self.subTest(bad=bad):
                with self.assertRaises(RegistryError) as c:
                    gate_registry.load_default(self.d)
                self.assertIn(said, str(c.exception))

    def test_without_the_file_the_shipped_tables_hold(self):
        self.assertEqual(gate_registry.load_default(self.d)['tables'], gate_registry.load()['tables'])


for _d in (os.path.join(ROOT, 'tools', 'bambu'), os.path.dirname(os.path.abspath(__file__))):
    if _d not in sys.path: sys.path.insert(0, _d)
import bambu3mf, test_guard as G                                         # noqa: E402
from test_stamp import plate, write_stl                                  # noqa: E402


class CouponPlate(G.Guarded):
    """The coupon plate goes into the project in enforce mode: each coupon STL that `gate.py coupons` writes carries a
    coupon stamp bound to its geometry and the declared target. A mesh with a coupon's name and other geometry has none."""
    mode = 'enforce'

    def setUp(self):
        super().setUp()
        self.set_project('enforce', targets={'main': TARGET})
        self.ledger()
        c = {'id': 'insert_M3x4x5', 'kind': 'insert', 'stack': [(7.5, 7.5, 0.54), (4.4, 4.4, 1.5), (3.6, 3.6, 5.0)], 'tests': 'the M3x4x5 profile',
             'parts': [PART], 'record': 'gate.py owner table inserts M3x4x5 --under main'}
        self.folder = gate_coupons.write(self.d, 'main', TARGET, [c])
        self.plate = [('coupons', [('insert coupon', 'insert_M3x4x5.stl', False)])]

    def place(self):
        bambu3mf.new_project(self.tpl, self.proj)
        return G.quiet(bambu3mf.build, self.proj, self.folder, self.plate)

    def test_a_coupon_plate_saves_in_enforce_and_is_carried_after(self):
        self.place()
        self.assertEqual(self.objects(), ['insert coupon'])
        stamp = self.journal()[-1]['stamp']
        self.assertRegex(stamp, r'^coupon:main:insert_M3x4x5@[0-9a-f]{12}$')
        infos, files = bambu3mf.load(self.proj)
        G.quiet(bambu3mf.save, self.proj, files, infos)                         # a plain re-save: carried by its coupon stamp
        data = gate_common.read_json(gate_coupons.stamp_path(self.d, 'main'))
        self.assertEqual((data['for'], data['target'], len(data['coupons'])), ('main', TARGET, 1))

    def test_a_coupons_run_after_one_fit_was_recorded_keeps_the_plate_saveable(self):
        magnet = {'id': 'magnet_d4', 'kind': 'pockets', 'holes': [4.15, 4.2, 4.25], 'depth': 2.1, 'tests': 'a D4 x 2 magnet',
                  'parts': [PART], 'record': 'gate.py owner param fits.magnet_press --tag M --under main'}
        insert = {'id': 'insert_M3x4x5', 'kind': 'insert', 'stack': [(7.5, 7.5, 0.54), (4.4, 4.4, 1.5), (3.6, 3.6, 5.0)], 'tests': 'the M3x4x5 profile',
                  'parts': [PART], 'record': 'gate.py owner table inserts M3x4x5 --under main'}
        gate_coupons.write(self.d, 'main', TARGET, [insert, magnet])
        self.plate = [('coupons', [('insert coupon', 'insert_M3x4x5.stl', False), ('magnet coupon 1', 'magnet_d4_1_d4p15.stl', False)])]
        self.place()
        gate_coupons.write(self.d, 'main', TARGET, [magnet])            # the insert fit is recorded: only the magnet coupon is made again
        infos, files = bambu3mf.load(self.proj)
        G.quiet(bambu3mf.save, self.proj, files, infos)                 # the plate already in the project: a plain re-save goes through
        data = gate_common.read_json(gate_coupons.stamp_path(self.d, 'main'))
        self.assertEqual(sorted(e['coupon'] for e in data['coupons'].values()), ['insert_M3x4x5'] + ['magnet_d4'] * 3)
        self.assertTrue(all(e['target'] == TARGET for e in data['coupons'].values()))
        # a coupon made for another target before keeps that target: it does not pass for this one
        other = dict(TARGET, xy_hole_compensation=0.15)
        gate_coupons.write(self.d, 'main', other, [magnet])
        geom = next(g for g, e in gate_common.read_json(gate_coupons.stamp_path(self.d, 'main'))['coupons'].items() if e['coupon'] == 'insert_M3x4x5')
        self.assertEqual(gate_coupons.holds(self.d, geom, TARGET)[0], 'valid')
        self.assertEqual(gate_coupons.holds(self.d, geom, other)[0], 'stale')

    def test_a_coupon_no_run_has_on_record_says_to_take_it_off(self):
        os.remove(gate_coupons.stamp_path(self.d, 'main'))
        with self.assertRaises(G.GuardRefusal) as c:
            self.place()
        self.assertIn('a coupon no `gate.py coupons` has on record', str(c.exception))
        self.assertNotIn('Export the part through the gate', str(c.exception))

    def test_a_mesh_with_a_coupon_name_and_other_geometry_is_refused(self):
        write_stl(os.path.join(self.folder, 'insert_M3x4x5.stl'), *plate())      # the coupon's file name, another mesh
        with self.assertRaises(G.GuardRefusal) as c:
            self.place()
        self.assertIn('insert coupon', str(c.exception)); self.assertIn('no coupon stamp holds the geometry', str(c.exception))

    def test_a_coupon_of_another_target_is_refused(self):
        self.set_project('enforce', targets={'main': dict(TARGET, xy_hole_compensation=0.15)})
        with self.assertRaises(G.GuardRefusal) as c:
            self.place()
        self.assertIn('a coupon of main, made for its declared target', str(c.exception))

    def test_verify_takes_the_coupon_stamp(self):
        self.place()
        from probes import project_consistency
        layout = gate_common.load_layout(self.d)
        facts = project_consistency.measure(self.d, layout['projects'][0], layout, gate_common.load_census(self.d), declared_target=TARGET)
        row = [o for o in facts['objects'] if o['name'] == 'insert coupon'][0]
        self.assertEqual(row['stamp']['state'], 'valid'); self.assertIn('coupon insert_M3x4x5 of the coupon plate of main', row['stamp']['why'])


if __name__ == '__main__':
    unittest.main()
