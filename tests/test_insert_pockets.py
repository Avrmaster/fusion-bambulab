"""tools/fusion/insert_pockets.py: the pocket sizes have one source, the thresholds of wall() and relief() come from
PARAMS, and their rows name each pocket in the part's own frame. wall() and relief() run here against the fake adsk of
test_geom.py (an analytic part): the logic and the frames are tested, the real Fusion calls are not."""
import ast, os, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_geom as fake        # noqa: E402  (the fake adsk and the demo part; its test classes are not collected from here)
SRC = os.path.join(ROOT, 'tools', 'fusion', 'insert_pockets.py')


def source():
    with open(SRC, encoding='utf-8') as f: return f.read()


class InsertPockets(unittest.TestCase):
    """the pocket sizes have one source (tools/fusion/insert_pockets.py) and the skill's table agrees with it"""

    def pockets(self):
        tree = ast.parse(source())
        node = next(n for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id == 'POCKETS')
        return {k.value: {kk.arg: vv.value for kk, vv in zip(v.keywords, [kw.value for kw in v.keywords])}
                for k, v in zip(node.value.keys, node.value.values)}

    def test_m3(self):
        p = self.pockets()['M3x4x5']
        self.assertLess(p['hole_d'], p['pilot_d'])            # the knurl and the pilot band both bite
        self.assertGreater(p['lead_d'], p['pilot_d'])          # the pilot band drops into the lead-in
        self.assertLess(p['lead_d'], p['knurl_d'])             # a lead-in as wide as the knurl grips nothing
        self.assertGreater(p['depth'], p['insert_len'])

    def test_skill_matches(self):
        p = self.pockets()['M3x4x5']
        with open(os.path.join(ROOT, 'skills', 'print-design', 'SKILL.md'), encoding='utf-8') as f: s = f.read()
        row = next(l for l in s.splitlines() if l.startswith('| M3×4×5 heat-set insert'))
        for k in ('lead_d', 'lead_depth', 'hole_d', 'depth', 'relief_d', 'relief_depth'):
            self.assertIn('%g' % p[k], row, k)

    def test_the_insert_table_of_the_gate_agrees(self):
        p, row = self.pockets()['M3x4x5'], fake.SHIPPED['inserts']['kinds']['M3x4x5']
        self.assertEqual((p['relief_d'], p['relief_depth'], p['lead_d'], p['lead_depth'], p['hole_d'], p['depth'], p['insert_len']),
                         (row['relief']['d'], row['relief']['depth'], row['lead_in']['d'], row['lead_in']['depth'], row['hole']['d'], row['hole']['depth'], row['insert_len']))

    def test_no_pass_line_under_the_target_and_no_standard_boss(self):
        text = source()
        for gone in ('WALL_PASS', '3.8', 'D12', 'boss'):
            self.assertNotIn(gone, text, gone)
        tree = ast.parse(text)
        consts = {t.id: n.value for n in tree.body if isinstance(n, ast.Assign) for t in ast.walk(n.targets[0]) if isinstance(t, ast.Name)}
        self.assertIsNone(consts['PARAMS'].value)                                # None until a caller sets it
        target = next(n for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Tuple) and n.targets[0].elts[0].id == 'WALL_TARGET')
        self.assertEqual([c.value for c in target.value.elts], [4.0, 1.6])


class Walls(unittest.TestCase):
    """wall() and relief() on the demo part of test_geom.py: pockets p1 .. p5 have the shape the tool finds
    (p1 room on every side, p2 2.5 from an edge, p3 exactly 4.0 and no relief, p4 at 3.8, p5 1.6 with a bought part beyond)"""

    def tool(self, app, **set_globals):
        cm = fake.fake_adsk(app)                                                 # (Python 3.9 has no enterContext)
        cm.__enter__(); self.addCleanup(cm.__exit__, None, None, None)
        g = {}
        exec(compile(source(), 'insert_pockets.py', 'exec'), g)
        g['DOC'] = 'Demo Assembly v7'
        g.update(set_globals)
        return g

    def rows(self, result):
        """rows by the name of the place the pocket is drilled at"""
        out = {}
        for r in result['rows']:
            key = next(k for k, (x, y) in fake.AT.items() if abs(r['part_mm'][0] - x) < 0.02 and abs(r['part_mm'][1] - y) < 0.02)
            out[key] = r
        return out

    def test_rows_verdicts_and_the_two_names_of_a_pocket(self):
        res = self.tool(fake.demo())['wall']('Bracket')
        rows = self.rows(res)
        self.assertEqual((res['part'], res['pockets'], res['next'], res['fail'], res['grow'], res['blocked']), ('Bracket', 5, None, 0, 2, 1))
        self.assertEqual(sorted(rows), ['p2', 'p4', 'p5'])                        # p1 has room; p3 has exactly the target and passes
        p2 = rows['p2']
        self.assertEqual((p2['wall_mm'], p2['verdict'], p2['towards'], p2['by'], p2['free'] == p2['thin_dirs']), (2.5, 'GROW', (-1.0, 0.0, 0.0), {}, True))
        self.assertEqual(p2['axis'], (0.0, 0.0, 1.0))                             # out of the material, in the part's own frame
        for got, want in zip(p2['part_mm'], (4.7, 20.0, 11.46)): self.assertAlmostEqual(got, want, delta=0.02)      # the pocket top: the relief floor
        for got, want in zip(p2['at_mm'], (104.7, 20.0, 24.46)): self.assertAlmostEqual(got, want, delta=0.06)       # the same point where the occurrence stands
        self.assertEqual((rows['p4']['wall_mm'], rows['p4']['verdict']), (3.8, 'GROW'))      # under the target: a row (it passed while the line was below the target)
        self.assertEqual((rows['p5']['wall_mm'], rows['p5']['verdict'], rows['p5']['by'], rows['p5']['free']), (1.6, 'BLOCKED', {'ref motor': 1.6}, 0))

    def test_thresholds_come_from_params_when_the_caller_sets_them(self):
        app = fake.demo()
        loose = self.tool(app, PARAMS={'inserts.wall_target': 3.0})['wall']('Bracket')
        self.assertEqual(sorted(self.rows(loose)), ['p2', 'p5'])
        strict = self.tool(app, PARAMS={'inserts.wall_target': 4.5, 'inserts.wall_floor': 2.0})['wall']('Bracket')
        rows = self.rows(strict)
        self.assertEqual(sorted(rows), ['p2', 'p3', 'p4', 'p5']); self.assertEqual((rows['p5']['verdict'], rows['p3']['wall_mm'], strict['fail']), ('FAIL', 4.0, 1))
        named = self.tool(app, PARAMS={'inserts.wall_target': 3.0})['wall']('Bracket', target=4.2, floor=1.0)       # an argument still wins
        self.assertEqual(sorted(self.rows(named)), ['p2', 'p3', 'p4', 'p5']); self.assertEqual(named['fail'], 0)
        self.assertEqual(sorted(self.rows(self.tool(app, PARAMS={})['wall']('Bracket'))), ['p2', 'p4', 'p5'])       # an empty dict: the constants

    def test_a_round_boss_of_3_8_round_the_lead_in_is_a_row(self):
        plate = fake.Solid((0.0, 0.0, 0.0), (40.0, 30.0, 3.0), holes=[{'o': (20.0, 15.0, 12.0), 'a': fake.UP, 'segs': [fake.LEAD, fake.HOLE]}],
                           bosses=[{'o': (20.0, 15.0, 3.0), 'a': fake.UP, 'r': 6.0, 'h': 9.0}])
        app = fake.application([fake.Occ(fake.Comp('c-knob', 'Knob', [fake.Body('Body1', plate)]), 'Knob:1')])
        res = self.tool(app)['wall']('Knob')
        self.assertEqual((res['pockets'], res['grow'], [(r['wall_mm'], r['thin_dirs'], r['free']) for r in res['rows']]), (1, 1, [(3.8, 48, 48)]))

    def test_window_and_budget(self):
        tool = self.tool(fake.demo())
        whole = tool['wall']('Bracket')
        part = tool['wall']('Bracket', start=1, limit=2)
        self.assertEqual((part['pockets'], part['next']), (3, 3))
        rows, start, calls = [], 0, 0
        while start is not None:
            r = tool['wall']('Bracket', start=start, limit=2); rows += r['rows']; start = r['next']; calls += 1
        self.assertEqual((calls, rows), (3, whole['rows']))
        slow = tool['wall']('Bracket', budget=0.0)                                # no time left: one pocket, never none
        self.assertEqual(slow['next'], 1)
        self.assertEqual(tool['wall']('Bracket', start=5)['rows'], []); self.assertIsNone(tool['wall']('Bracket', start=5)['next'])

    def test_a_moved_and_turned_occurrence_keeps_the_part_frame_name(self):
        here = self.rows(self.tool(fake.demo())['wall']('Bracket:1'))             # the full path of an occurrence works too
        there = self.rows(self.tool(fake.demo(place=fake.placement(-20.0, 55.0, 3.0, turn=90.0)))['wall']('Bracket'))
        self.assertEqual(sorted(there), sorted(here))
        for key in here:
            self.assertEqual(there[key]['axis'], here[key]['axis']); self.assertEqual(there[key]['wall_mm'], here[key]['wall_mm'])
            for a, b in zip(there[key]['part_mm'], here[key]['part_mm']): self.assertAlmostEqual(a, b, delta=0.02)
            self.assertGreater(max(abs(a - b) for a, b in zip(there[key]['at_mm'], here[key]['at_mm'])), 10.0)

    def test_mesh_ref_stops_the_wall(self):
        res = self.tool(fake.demo(mesh=True))['wall']('Bracket')
        p2 = self.rows(res)['p2']
        self.assertEqual(p2['by'], {'ref chain / MeshBody1 (mesh)': 2.5}); self.assertLess(p2['free'], p2['thin_dirs'])

    def test_relief_rows_and_the_relief_wall_parameter(self):
        app = fake.demo()
        res = self.tool(app)['relief']('Bracket', dry=True)
        self.assertEqual((res['pockets'], res['reliefs'], res['dry'], res['next']), (5, 1, True, None))
        self.assertEqual(res['rows'], [{'at_mm': (6.2, 8.0, 12.0), 'axis': (0.0, 0.0, 1.0), 'part_mm': (6.2, 8.0, 12.0), 'relief_d': 6.5}])       # p3 is the one without
        self.assertEqual(self.tool(app)['RELIEF_WALL'], 2.45)                                                         # the registry default of inserts.relief_wall_min: one number
        loose = self.tool(app, PARAMS={'inserts.relief_wall_min': 1.2})['relief']('Bracket', dry=True)               # a project that set it back, with its owner's tag
        self.assertEqual(loose['rows'][0]['relief_d'], 7.5)
        wide = self.tool(app, PARAMS={'inserts.relief_wall_min': 2.6})['relief']('Bracket', dry=True)                # 3.75 + 2.6 does not fit in 6.2
        self.assertEqual(wide['rows'][0]['relief_d'], 6.5)
        none = self.tool(app, PARAMS={'inserts.relief_wall_min': 3.5})['relief']('Bracket', dry=True)
        self.assertEqual((none['reliefs'], none['rows'][0]['skipped']), (0, 'less than 3.5 mm of wall for any relief'))
        self.assertEqual(none['rows'][0]['axis'], (0.0, 0.0, 1.0))
        window = self.tool(app)['relief']('Bracket', dry=True, start=0, limit=1)
        self.assertEqual((window['next'], window['pockets']), (1, 5))


if __name__ == '__main__':
    unittest.main()
