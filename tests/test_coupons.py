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


if __name__ == '__main__':
    unittest.main()
