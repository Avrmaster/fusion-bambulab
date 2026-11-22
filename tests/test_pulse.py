"""The pulse of a document (0.3.3): one short call that says whether anything a scan reads has changed, so a run scans
in full only when something did. The saved version of the document is never trusted for that (an unsaved edit does
not change it). Tried here: the probe on the fake adsk of test_geom_rings (a hidden body changes nothing, a move, a
rename or an edit does; a body with no revision id gives no pulse), and gate.py run on the demo project (an unchanged
pulse skips the scan, any other answer scans; a scan of several calls stores none; `gate.py scan` always scans).
Names are invented."""
import os, sys, types, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate')); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_fusion, gate_common
import test_geom_rings as T

PULSE = 'a1' * 8


def with_revisions(comps, rev='r1'):
    for c in comps:
        for b in c.bRepBodies:
            b.revisionId = '%s-%s-%s' % (rev, c.name, b.name)
            b.parentComponent = types.SimpleNamespace(id='id-' + c.name)
    return comps


class Probe(unittest.TestCase):
    setUp = T.Probes.setUp
    unit = T.Probes.unit
    bracket_scene = T.Probes.bracket_scene

    def pulse(self, comps):
        return self.unit('fusion_pulse', comps)['facts']

    def test_the_pulse_sees_moves_names_and_edits_and_not_the_light_bulbs(self):
        base = self.pulse(with_revisions(self.bracket_scene()))
        self.assertEqual((len(base['pulse']), base['why']), (16, None))
        self.assertEqual(self.pulse(with_revisions(self.bracket_scene()))['pulse'], base['pulse'])
        hidden = with_revisions(self.bracket_scene())
        for c in hidden:
            for b in c.bRepBodies:
                b.isLightBulbOn = False
        self.assertEqual(self.pulse(hidden)['pulse'], base['pulse'])                     # hiding changes nothing the gate reads
        edited = with_revisions(self.bracket_scene())
        edited[2].bRepBodies[0].revisionId = 'r2'                                       # the cover was edited
        self.assertNotEqual(self.pulse(edited)['pulse'], base['pulse'])
        renamed = with_revisions(self.bracket_scene())
        renamed[2].bRepBodies[0].name = 'Lid'
        self.assertNotEqual(self.pulse(renamed)['pulse'], base['pulse'])
        mods = T.fake_adsk(with_revisions(self.bracket_scene()))
        occ = mods['adsk.core'].Application.get().documents[0].products.itemByProductType(None).rootComponent.allOccurrences[2]
        occ.transform2 = types.SimpleNamespace(asArray=lambda: (1, 0, 0, 0.5, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1))      # 5 mm along x
        self.assertNotEqual(self.unit('fusion_pulse', mods)['facts']['pulse'], base['pulse'])

    def test_a_mesh_whose_triangles_change_and_nodes_stay_changes_the_pulse(self):
        # 0.3.3 proof: the pulse digested the nodes of a mesh only, the scan's "shape" digests nodes and triangles, and
        # fusion_openings and fusion_inserts read the triangles. Same nodes, other triangles: the pulse must differ
        import gate_deps
        flat = [(25, 25, 15), (35, 25, 15), (35, 35, 15), (25, 35, 15)]
        def scene(tris):
            s = with_revisions(self.bracket_scene())
            s[-1] = T.Comp('ref motor', [], [T.MeshBody('shell', flat, tris)])
            return s
        a, b = scene([(0, 1, 2), (0, 2, 3)]), scene([(0, 1, 3)])
        wa, wb = gate_deps.world(self.unit('fusion_scan', a)['facts']), gate_deps.world(self.unit('fusion_scan', b)['facts'])
        key = [k for k in wa if 'mesh:' in k][0]
        self.assertNotEqual(wa[key], wb[key])                                               # the census sees it ...
        oa = gate_deps.facts_view(self.unit('fusion_openings', a, features=T.Probes.FEATURES)['facts'])
        ob = gate_deps.facts_view(self.unit('fusion_openings', b, features=T.Probes.FEATURES)['facts'])
        self.assertNotEqual(oa, ob)                                                         # ... and a fresh openings unit too
        pa, pb = self.pulse(a), self.pulse(b)
        self.assertEqual((len(pa['pulse']), pa['why']), (16, None))
        self.assertNotEqual(pa['pulse'], pb['pulse'])                                       # so the pulse must: the scan is not skipped
        self.assertEqual(self.pulse(scene([(0, 1, 2), (0, 2, 3)]))['pulse'], pa['pulse'])
        moved = scene([(0, 1, 2), (0, 2, 3)])
        moved[-1].meshBodies[0].displayMesh.nodeCoordinatesAsDouble[0] += 0.01               # a node moved by 0.1 mm
        self.assertNotEqual(self.pulse(moved)['pulse'], pa['pulse'])

    def test_a_body_with_no_revision_id_gives_no_pulse(self):
        got = self.pulse(self.bracket_scene())
        self.assertIsNone(got['pulse']); self.assertIn('gives no revision id', got['why'])


class Run(unittest.TestCase):
    def setUp(self):
        self.demo = fake_fusion.DemoProject(self)
        self.root, self.fusion = self.demo.root, self.demo.fusion
        self.demo.model({'Bracket': 'v1', 'Cover': 'v1'})
        self.scan_with_pulse(PULSE)

    def scan_with_pulse(self, pulse, done=True):
        facts = dict(self.fusion.answers[('fusion_scan', None)][0]['facts'], pulse=pulse)
        self.fusion.clear('fusion_scan', None)
        if not done:
            self.fusion.set_facts('fusion_scan', None, dict(facts, parts=[], refs=[]), done=False, cursor=1)
        self.fusion.set_facts('fusion_scan', None, facts)

    def answer_pulse(self, pulse):
        self.fusion.clear('fusion_pulse', None)
        self.fusion.set_facts('fusion_pulse', None, {'pulse': pulse, 'bodies': 2, 'why': None})

    def gate_run(self, *argv):
        return self.demo.gate('run', '--wall', '600', *argv)

    def scans(self):
        return len(self.fusion.calls_of('fusion_scan'))

    def stored(self):
        return gate_common.load_census(self.root)['docs']['demo'].get('pulse')

    def test_an_unchanged_pulse_skips_the_scan_and_any_other_answer_scans(self):
        self.answer_pulse(PULSE)
        self.assertEqual(self.gate_run()[0], 2)
        self.assertEqual((self.scans(), self.stored()['digest']), (1, PULSE))           # no pulse yet: scanned in full, once (the confirm is a pulse)
        code, out, err = self.gate_run()
        self.assertEqual(self.scans(), 1); self.assertIn('demo is as the last scan read it (its pulse is the same): not scanned again', out)
        self.answer_pulse('b2' * 8)                                                     # something changed
        self.gate_run()
        self.assertEqual(self.scans(), 2)
        self.answer_pulse(None)                                                         # the probe cannot vouch
        self.gate_run()
        self.assertEqual(self.scans(), 3)
        self.fusion.clear('fusion_pulse', None)                                         # the pulse call fails
        self.fusion.set_error('fusion_pulse', None, 'RuntimeError: boom')
        self.gate_run()
        self.assertEqual(self.scans(), 4)

    def test_other_not_printed_names_or_a_fresh_run_scan_in_full(self):
        self.answer_pulse(PULSE)
        self.gate_run(); n = self.scans()
        p = gate_common.read_json(gate_common.gate_path(self.root, 'project.json'), {})
        p['not_printed'] = p['not_printed'] + ['^spare ']
        gate_common.write_json(gate_common.gate_path(self.root, 'project.json'), p)
        self.gate_run()
        self.assertEqual(self.scans(), n + 1)                                           # the pulse is the same, the inputs are not
        self.gate_run('--all', '--fresh')
        self.assertEqual(self.scans(), n + 2)
        self.assertEqual(self.demo.gate('scan')[0], 0)
        self.assertEqual(self.scans(), n + 3)                                           # `gate.py scan` scans in full

    def test_a_scan_of_several_calls_stores_no_pulse(self):
        self.scan_with_pulse(PULSE, done=False)
        self.answer_pulse(PULSE)
        self.gate_run()
        self.assertIsNone(self.stored())
        self.assertGreaterEqual(self.scans(), 2)


if __name__ == '__main__':
    unittest.main()
