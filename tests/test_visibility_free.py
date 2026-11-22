"""What the gate measures does not depend on Fusion's light bulbs (0.3.3).

A session that hides parts for a screenshot and leaves them hidden must change nothing: no probe reads a light bulb
(only fusion_scan notes "visible" and "shown", for information), a hidden body is a neighbour like a shown one (so a
collision or a closed hole behind it is still found), and a pure visibility change makes no unit stale. The only ways
to leave a body out stay the explicit rules: not_printed (by name, in the scan and every walk) and
access.overlay_pattern (by name, in the judges). Names and numbers are invented."""
import glob, json, os, re, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate')); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_fusion, gate_deps
import test_geom_rings as T
from test_geom_rings import Blk, Body, Comp

PROBES = os.path.join(ROOT, 'tools', 'gate', 'probes')
GROUPS = [{'key': 'frame', 'patterns': ['^Bracket', '^Base', '^Cover', '^Old cover']}, {'key': 'arm', 'patterns': ['^Pulley']}]


def hide_all(comps, own='Bracket'):
    """A fake document (test_geom_rings.fake_adsk) with every occurrence and every body but the part's switched off."""
    mods = T.fake_adsk(comps)
    root = mods['adsk.core'].Application.get().documents[0].products.itemByProductType(None).rootComponent
    for o in root.allOccurrences:
        if o.component.name == own:
            continue
        o.isLightBulbOn = False
        for b in list(o.component.bRepBodies) + list(o.component.meshBodies):
            b.isLightBulbOn = False
    return mods


class Source(unittest.TestCase):
    def test_no_probe_reads_a_light_bulb_but_the_scan_for_information(self):
        for path in sorted(glob.glob(os.path.join(PROBES, '*.py'))):
            with open(path, encoding='utf-8') as f:
                code = gate_deps.normalised(f.read())                     # comments and docstrings may speak of it
            name = os.path.basename(path)
            if name == 'fusion_scan.py':
                printed = [l for l in code.splitlines() if 'printed = [' in l]
                self.assertTrue(printed and not any('isLightBulbOn' in l for l in printed), printed)
                continue
            for word in ('isLightBulbOn', 'isVisible', "['visible']", "'shown'"):
                self.assertNotIn(word, code, '%s reads %s' % (name, word))


class Records(unittest.TestCase):
    def facts(self, shown):
        return {'parts': [fake_fusion.scan_part('Plate', 's' * 64, faces=20, shown=shown, visible=shown),
                          fake_fusion.scan_part('Cover', 't' * 64, faces=8, occurrence='Cover:1', shown=shown, visible=shown)],
                'copy_bodies': [], 'refs': [{'name': 'ref nut', 'component_id': 'c-nut', 'occurrence': 'ref nut:1', 'body': 'Body1', 'sig': 'n' * 16,
                                             'pose': 'p', 'bbox_doc': [[0, 0, 0], [1, 1, 1]], 'faces': 6, 'mesh': False, 'shape': 'x' * 16,
                                             'visible': shown, 'shown': shown}]}

    def test_hiding_every_body_changes_no_line_of_the_census_world_and_no_own_state(self):
        self.assertEqual(gate_deps.world(self.facts(True)), gate_deps.world(self.facts(False)))
        a, b = self.facts(True)['parts'][0], self.facts(False)['parts'][0]
        self.assertEqual(gate_deps.own_state(dict(a, shown=True)), gate_deps.own_state(dict(b, shown=False)))

    def test_a_unit_measured_with_everything_shown_holds_when_everything_is_hidden(self):
        shown, hidden = gate_deps.world(self.facts(True)), gate_deps.world(self.facts(False))
        entry = dict(self.facts(True)['parts'][0], doc='demo')
        job = {'params': {}, 'tables': {}, 'motion_groups': []}
        read = {'reads': [{'box': [[-100, -100, -100], [100, 100, 100]], 'margin': 5.0}], 'params': [], 'tables': [], 'job': [], 'complete': True}
        deps = gate_deps.record(read, job, entry, shown, 'fusion_near')
        stored = {'facts': {'bodies': []}, 'done': True, 'deps': deps}
        self.assertIsNone(gate_deps.why_stale(stored, entry, job, shown, 'fusion_near'))
        for mode in gate_deps.DEPS_MODES:
            self.assertIsNone(gate_deps.why_stale(stored, dict(entry, shown=False), job, hidden, 'fusion_near', mode=mode))


class Probes(unittest.TestCase):
    setUp = T.Probes.setUp
    unit = T.Probes.unit
    bracket_scene = T.Probes.bracket_scene
    bearing_scene = T.Probes.bearing_scene
    FEATURES = T.Probes.FEATURES

    def same(self, probe, scene, **kw):
        shown = self.unit(probe, scene(), **kw)
        hidden = self.unit(probe, hide_all(scene(), kw.get('part', 'Bracket')), **kw)
        self.assertTrue(shown['done'] and hidden['done'])
        self.assertEqual(gate_deps.facts_view(hidden['facts']), gate_deps.facts_view(shown['facts']), probe)
        self.assertEqual(hidden['reads'], shown['reads'], probe)
        return shown['facts']

    def test_every_neighbour_hidden_changes_no_fact(self):
        self.same('fusion_screws', self.bracket_scene, features=self.FEATURES)
        self.same('fusion_openings', self.bracket_scene, features=self.FEATURES)
        self.same('fusion_near', self.bracket_scene)
        self.same('fusion_gap', self.bracket_scene, groups=GROUPS)
        self.same('fusion_bearings', self.bearing_scene, part='Housing')

    def test_a_hidden_neighbour_that_collides_is_still_found(self):
        hit = lambda: self.bracket_scene() + [Comp('Pulley 2', [Body('Body1', Blk((5, 5, 5), (12, 12, 12)))])]      # it reaches into the part
        groups = GROUPS + [{'key': 'belt', 'patterns': ['^Pulley 2']}]
        facts = self.unit('fusion_gap', hide_all(hit()), groups=groups)['facts']
        row = [g for g in facts['gaps'] if g['name'] == 'Pulley 2']
        self.assertEqual((len(row), row[0]['gap'] if row else None), (1, 0.0))
        self.assertNotIn('hidden', row[0])
        cover = self.unit('fusion_openings', hide_all(self.bracket_scene()), features=self.FEATURES)['facts']
        hits = {h['name']: h['kind'] for o in cover['openings'] if o.get('feature') == 'h2' for h in o['paths']['own']['hits']}
        self.assertEqual(hits.get('Old cover'), 'printed')                  # a hidden printed body in front of a hole: measured, never "hidden"

    def test_the_scan_keeps_a_hidden_body_a_part_and_notes_visibility_for_information(self):
        facts = self.unit('fusion_scan', hide_all(self.bracket_scene()), part='Bracket')['facts']
        old = [p for p in facts['parts'] if p['name'] == 'Old cover']
        self.assertEqual(len(old), 1)                                       # its own light bulb is off: still a part (0.3.3)
        self.assertEqual((old[0]['visible'], old[0]['shown']), (False, False))
        self.assertNotIn('Old cover', [r['name'] for r in facts['refs']])


if __name__ == '__main__':
    unittest.main()
