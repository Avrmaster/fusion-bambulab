import ast, os, re, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class InsertPockets(unittest.TestCase):
    """the pocket sizes have one source (tools/fusion/insert_pockets.py) and the skill's table agrees with it"""

    def pockets(self):
        tree = ast.parse(open(os.path.join(ROOT, 'tools', 'fusion', 'insert_pockets.py'), encoding='utf-8').read())
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
        s = open(os.path.join(ROOT, 'skills', 'print-design', 'SKILL.md'), encoding='utf-8').read()
        row = next(l for l in s.splitlines() if l.startswith('| M3×4×5 heat-set insert'))
        for k in ('lead_d', 'lead_depth', 'hole_d', 'depth', 'relief_d', 'relief_depth'):
            self.assertIn('%g' % p[k], row, k)


if __name__ == '__main__':
    unittest.main()
