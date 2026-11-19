"""probes/fusion_near.py executed as Fusion would execute it, against a fake adsk: which bodies it lists as near a part.

The probe reads nothing but bounding boxes through gate_world() of _fusion.py, so a fake document of plain objects is
enough: occurrences with a path, a light bulb and bodies that have a name and a box. No Fusion. Names are invented."""
import json, os, shutil, sys, tempfile, types, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate')); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_attest, gate_fusion_rpc, gate_plan, gate_registry
from test_fusion_script import FakeDoc, Pt, fake_adsk

DOC = 'Demo Assembly'


class Coll(list):
    count = property(len)

    def item(self, i): return self[i]


def body(name, lo, hi, on=True, solid=True):
    """A solid body with a box given in mm (the API holds cm)."""
    face = types.SimpleNamespace(geometry=types.SimpleNamespace(surfaceType=0), area=1.0, centroid=Pt(0, 0, 0))
    b = types.SimpleNamespace(name=name, isSolid=solid, isLightBulbOn=on, volume=1.0, area=6.0, faces=Coll([face]), edges=Coll(range(12)), vertices=Coll(range(8)),
                              boundingBox=types.SimpleNamespace(minPoint=Pt(*[c / 10.0 for c in lo]), maxPoint=Pt(*[c / 10.0 for c in hi])))
    b.createForAssemblyContext = lambda occ: b
    return b


def occurrence(path, bodies, on=True, parent=None, meshes=()):
    name = path.split('+')[-1].rsplit(':', 1)[0]
    comp = types.SimpleNamespace(id='c-' + name, name=name, bRepBodies=Coll(bodies), meshBodies=Coll(meshes))
    return types.SimpleNamespace(fullPathName=path, component=comp, bRepBodies=Coll(bodies), isLightBulbOn=on, assemblyContext=parent,
                                 transform2=types.SimpleNamespace(asArray=lambda: (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)))


def mesh(name, lo, hi, on=True):
    coords = [c / 10.0 for p in (lo, hi) for c in p]
    return types.SimpleNamespace(name=name, isLightBulbOn=on, displayMesh=types.SimpleNamespace(nodeCoordinatesAsDouble=coords))


class Near(unittest.TestCase):
    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.d)
        # the part: a bracket 40 x 30 x 10 at the origin
        self.bracket = occurrence('Bracket:1', [body('Body1', (0, 0, 0), (40, 30, 10))])
        drive = occurrence('ref stepper NEMA 17:1', [])
        self.occs = [
            self.bracket,
            occurrence('Cover:1', [body('Body1', (0, 0, 10), (40, 30, 13))]),                                    # a printed neighbour, touching
            occurrence('ref hardware:1', [body('insert M3x4x5 (bracket)', (10, 10, 4), (14, 14, 10)),            # inside the bracket
                                          body('screw M3x12 (far)', (200, 0, 0), (203, 3, 12))]),                # 160 mm away
            occurrence('ref stepper NEMA 17:1+ref drive:1', [body('Rotor', (46.5, 0, 0), (60, 30, 30))], parent=drive),      # 6.5 mm beside it
            occurrence('ref plug:1', [body('Body1', (40, 0, 22), (50, 10, 30))]),                                # 12 mm above the top: outside 10 mm
            occurrence('ref lamp:1', [body('Body1', (-9, 0, 0), (-4, 5, 5))], on=False),                         # hidden, 4 mm away
            occurrence('ref old frame:1', [], meshes=[mesh('scan', (0, 33, 0), (40, 60, 10))]),                  # a mesh, 3 mm away
        ]

    def run_probe(self, **kw):
        root = types.SimpleNamespace(id='root', name='Root', allOccurrences=self.occs, bRepBodies=Coll([]), meshBodies=Coll([]))
        design = types.SimpleNamespace(rootComponent=root)
        doc = FakeDoc(DOC, 3)
        doc.products = types.SimpleNamespace(itemByProductType=lambda t: design)
        job = gate_fusion_rpc.new_job('u-0001', 'r1', 'fusion_near', DOC, 'demo', os.path.join(self.d, 'units', 'u-0001.json'), part='demo/bracket',
                                      component_id='c-Bracket', occurrence='Bracket:1', body='Body1', sig='ab' * 32,
                                      params=kw.pop('params', {'process.near_margin': 10.0}), not_printed=['^ref '], **kw)
        mods = fake_adsk([doc])
        saved = {k: sys.modules.get(k) for k in mods}
        sys.modules.update(mods)
        try:
            g = {'JOB': job}
            exec(compile(gate_fusion_rpc.unit_text(job), 'unit.py', 'exec'), g)
            g['run']('')
        finally:
            for k, v in saved.items():
                if v is None: sys.modules.pop(k, None)
                else: sys.modules[k] = v
        with open(job['out'], encoding='utf-8') as f: return json.load(f)

    def test_it_lists_what_is_not_printed_and_within_the_margin(self):
        env = self.run_probe()
        self.assertIsNone(env['error'], env['error']); self.assertTrue(env['done']); self.assertEqual(len(env['sig']), 64)
        facts = env['facts']
        self.assertEqual(sorted(facts), ['bodies', 'margin']); self.assertEqual(facts['margin'], 10.0)
        rows = {r['name']: r for r in facts['bodies']}
        self.assertEqual(sorted(rows), ['ref drive', 'ref hardware / insert M3x4x5 (bracket)', 'ref lamp', 'ref old frame'])
        self.assertEqual(rows['ref hardware / insert M3x4x5 (bracket)'], {'name': 'ref hardware / insert M3x4x5 (bracket)', 'path': 'ref hardware', 'kind': 'hardware', 'gap': 0.0})
        self.assertEqual((rows['ref drive']['kind'], rows['ref drive']['gap'], rows['ref drive']['path']), ('bought', 6.5, 'ref stepper NEMA 17+ref drive'))
        self.assertEqual((rows['ref lamp']['gap'], rows['ref lamp'].get('hidden')), (4.0, True))           # hidden is listed all the same
        self.assertEqual((rows['ref old frame']['kind'], rows['ref old frame']['gap']), ('mesh', 3.0))     # a mesh by its box: a candidate, never a proof of touching
        self.assertEqual([r['gap'] for r in facts['bodies']], sorted(r['gap'] for r in facts['bodies']))   # nearest first
        self.assertNotIn('Cover', rows); self.assertNotIn('ref plug', rows)                               # printed neighbours and what is outside the margin

    def test_the_margin_is_the_parameter(self):
        names = [r['name'] for r in self.run_probe(params={'process.near_margin': 3.5})['facts']['bodies']]
        self.assertEqual(names, ['ref hardware / insert M3x4x5 (bracket)', 'ref old frame'])
        names = [r['name'] for r in self.run_probe(params={'process.near_margin': 13.0})['facts']['bodies']]
        self.assertIn('ref plug', names)

    def test_out_of_time_it_hands_back_a_cursor_and_the_host_joins_the_rows(self):
        self.occs += [occurrence('ref pin %d:1' % i, [body('Body1', (0, 0, -5), (2, 2, 0))]) for i in range(120)]
        first = self.run_probe(deadline_s=0.5)                                                            # under the reserve from the start
        self.assertIsNone(first['error'], first['error']); self.assertFalse(first['done']); self.assertEqual(first['cursor'], 50)
        rest = self.run_probe(cursor=first['cursor'])
        self.assertTrue(rest['done'])
        joined = gate_fusion_rpc.merge_facts(first['facts'], rest['facts'])
        self.assertEqual(len(joined['bodies']), 124); self.assertEqual(len({r['name'] for r in joined['bodies']}), 124)       # every body once

    def test_the_facts_are_what_the_fact_tests_read(self):
        facts = self.run_probe()['facts']
        reg = gate_registry.load_default()
        params = {n: reg['params'][n]['default'] for n in gate_attest.FACT_PARAMS}
        others = {'fusion_census': {'holes': []}, 'fusion_screws': {'screws': [], 'washers': []}, 'fusion_bearings': {'bearings': []}}
        get = dict(others, fusion_near=facts).get
        state, text = gate_attest.FACT_TESTS['no_motor_near'](get, params, reg['tables'])
        self.assertIs(state, False); self.assertIn('match motors.ref_pattern (ref drive)', text)              # named two levels up: the path tells
        state, text = gate_attest.FACT_TESTS['no_bought_near'](get, params, reg['tables'])
        self.assertIs(state, False); self.assertIn('4 bought, hardware or mesh body(ies) within 10 mm', text)
        self.occs = self.occs[:2]                                                                         # the bracket and its printed cover alone
        alone = self.run_probe()['facts']
        get = dict(others, fusion_near=alone).get
        self.assertIs(gate_attest.FACT_TESTS['no_motor_near'](get, params, reg['tables'])[0], True)
        self.assertIs(gate_attest.FACT_TESTS['no_bought_near'](get, params, reg['tables'])[0], True)
        self.assertIs(gate_attest.FACT_TESTS['no_bought_near']({'fusion_near': alone}.get, params, reg['tables'])[0], None)   # the other units never ran: no proof

    def test_the_unit_is_planned_once_per_part_and_sent_read_only(self):
        reg = gate_registry.load_default()
        probes = [u['probe'] for u in gate_plan.units_for_part(reg, 'demo/bracket', {'doc': 'demo', 'sig': 's'})]
        self.assertEqual(probes.count('fusion_near'), 1); self.assertEqual(probes, list(gate_plan.PROBE_ORDER))
        self.assertEqual(gate_plan.units_for_part(reg, 'demo/shim', {'doc': 'demo', 'sig': 's', 'source': 'generated'}), [])
        job = gate_fusion_rpc.new_job('u-1', 'r1', 'fusion_near', DOC, 'demo', os.path.join(self.d, 'u.json'), part='demo/bracket')
        self.assertTrue(gate_fusion_rpc.read_only_for(gate_fusion_rpc.build_script(job)))
        text = gate_fusion_rpc.unit_text(job)
        tail = text[text.index('# ---- fusion_near.py ----'):]
        for word in ('import ', 'activeDocument', '.save(', 'deleteMe', 'print('):                         # nothing from disk, read-only, silent
            self.assertNotIn(word, tail.split('"""', 2)[2] if word == 'import ' else tail)


if __name__ == '__main__':
    unittest.main()
