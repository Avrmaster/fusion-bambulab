"""The tight record of what a unit read (gate_deps.py, 0.3.3) against the probes themselves, run as Fusion runs them on the
fake of tests/test_geom_rings.py: a body that the tight regions of a unit do not hold must leave its facts exactly as
they were. That is the whole claim the tight record makes, and these tests try it on many bodies placed at random round
the part (named like a screw, a washer, an insert, a nut, a bearing, a bought plate or a printed rib), for fusion_near,
fusion_gap and fusion_screws. Each test also checks that it tried both cases (bodies the tight record holds and bodies
it does not), so a region that held everything could not pass it by measuring everything.

The same body is also tried against the wide record (0.3.2): it holds every body the tight one holds. Names and numbers
are invented."""
import json, os, random, re, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate')); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_deps
from gate_common import digest
from probes import _geom_rings as R
import test_geom_rings as T
from test_geom_rings import Blk, Body, Comp

NOT_PRINTED = re.compile(r'^ref ')
GROUPS = [{'key': 'frame', 'patterns': ['^Bracket', '^Base', '^Cover', '^Xb']}, {'key': 'arm', 'patterns': ['^Pulley', '^Xm']}]
NAMES = ('Xb rib %d', 'Xm arm %d', 'Xn spare %d', 'ref plate %d', 'ref bolts %d', 'ref washer M3 %d', 'ref insert M3x4x5 %d',
         'ref nut M3 %d', 'ref bearing 608 %d', 'ref screw M3x8 cap %d')


def lines_of(comps):
    """The census world of a fake document (gate_deps.world as the scan would give it): one line per body."""
    out = {}
    for c in comps:
        path = '%s:1' % c.name
        printed_comp = not NOT_PRINTED.search(c.name)
        for b in c.bRepBodies:
            lo, hi = b.shape.box()
            kind = 'part' if printed_comp and not NOT_PRINTED.search(b.name) else 'ref'
            out[gate_deps.body_key(path, b.name)] = gate_deps._line(digest([c.name, b.name, repr((lo, hi))]), kind, 6, (lo, hi))
        for m in c.meshBodies:
            lo, hi = T.mm(m.boundingBox.minPoint), T.mm(m.boundingBox.maxPoint)
            out[gate_deps.body_key(path, m.name, True)] = gate_deps._line(digest([c.name, m.name]), 'mesh', 2, (lo, hi))
    return out


class Tight(unittest.TestCase):
    setUp = T.Probes.setUp
    unit = T.Probes.unit
    bracket_scene = T.Probes.bracket_scene
    FEATURES = T.Probes.FEATURES

    def try_bodies(self, probe, n=24, seed=7, groups=None, features=None, near=20.0):
        base = self.bracket_scene()
        env = self.unit(probe, base, features=features, groups=groups)
        self.assertTrue(env['done'])
        facts0, reads = gate_deps.facts_view(env['facts']), env['reads']
        self.assertTrue(any(r.get('mode') == 'tight' for r in reads), 'the unit noted no tight region')
        own = gate_deps.body_key('Bracket:1', 'Body1')
        was_t = gate_deps.World(lines_of(base)).bodies_in(reads, own, 'tight', groups)
        was_w = gate_deps.World(lines_of(base)).bodies_in(reads, own, 'wide', groups)
        rnd = random.Random(seed)
        kept = held = 0
        for i in range(n):
            lo = (rnd.uniform(-near, 40 + near), rnd.uniform(-near, 40 + near), rnd.uniform(-near, 10 + near))
            size = (rnd.uniform(0.5, 8.0), rnd.uniform(0.5, 8.0), rnd.uniform(0.5, 8.0))
            name = NAMES[i % len(NAMES)] % i
            scene = self.bracket_scene() + [Comp(name, [Body('Body1', Blk(lo, tuple(lo[k] + size[k] for k in range(3))))])]
            now_t = gate_deps.World(lines_of(scene)).bodies_in(reads, own, 'tight', groups)
            now_w = gate_deps.World(lines_of(scene)).bodies_in(reads, own, 'wide', groups)
            if now_t != was_t:
                held += 1
                self.assertNotEqual(now_w, was_w, '%s: the wide record must hold what the tight one holds' % name)
                continue
            kept += 1
            facts = gate_deps.facts_view(self.unit(probe, scene, features=features, groups=groups)['facts'])
            self.assertEqual(facts, facts0, '%s at %s: the tight record keeps the facts of %s, but they changed' % (name, [round(c, 1) for c in lo], probe))
        self.assertGreater(kept, 0); self.assertGreater(held, 0)
        return kept, held

    def test_near_a_printed_body_never_changes_its_facts(self):
        self.try_bodies('fusion_near', n=30)

    def test_gap_a_body_of_its_own_group_or_of_none_never_changes_its_facts(self):
        self.try_bodies('fusion_gap', n=40, groups=GROUPS, near=6.0)

    def test_screws_a_body_off_the_points_it_asks_never_changes_its_facts(self):
        self.try_bodies('fusion_screws', n=80, features=self.FEATURES, near=8.0)

    def test_screws_a_body_named_on_a_hole_axis_is_held_wherever_it_lies_in_the_walk(self):
        features = json.loads(json.dumps(self.FEATURES))
        features['holes'][0]['coaxial'] = [{'name': 'ref dowel 4', 'kind': 'bought'}]
        env = self.unit('fusion_screws', self.bracket_scene(), features=features)
        far = self.bracket_scene() + [Comp('ref dowel 4', [Body('Body1', Blk((70, 70, 0), (72, 72, 2)))])]
        own = gate_deps.body_key('Bracket:1', 'Body1')
        self.assertIn('ref dowel 4:1|Body1', gate_deps.World(lines_of(far)).bodies_in(env['reads'], own, 'tight'))

    def test_one_test_on_both_sides(self):
        # region_admits is the test the probes note their regions for and the host reads them back with
        region = {'box': [[0, 0, 0], [10, 10, 10]], 'margin': 1.0, 'kinds': ['ref'], 'names': ['(?i)washer']}
        s = gate_deps.search
        self.assertTrue(R.region_admits(region, 'ref washer M3:1|Body1', 'ref', 6, ((10.5, 0, 0), (12, 1, 1)), [], s))
        self.assertFalse(R.region_admits(region, 'ref washer M3:1|Body1', 'ref', 6, ((11.5, 0, 0), (12, 1, 1)), [], s))      # out of the margin
        self.assertFalse(R.region_admits(region, 'Washer plate:1|Body1', 'part', 6, ((1, 1, 1), (2, 2, 2)), [], s))         # a printed body
        self.assertFalse(R.region_admits(region, 'ref plate:1|Body1', 'ref', 6, ((1, 1, 1), (2, 2, 2)), [], s))             # another name
        self.assertTrue(R.region_admits(region, 'ref bolts:1|washer M3 (2)#2', 'ref', 6, ((1, 1, 1), (2, 2, 2)), [], s))     # by its body name
        self.assertTrue(R.region_admits(region, '|Body7', 'ref', 6, ((1, 1, 1), (2, 2, 2)), [], s))                         # the root: never filtered by name
        g = {'box': [[0, 0, 0], [10, 10, 10]], 'margin': 0.0, 'other_group': 'frame'}
        self.assertFalse(R.region_admits(g, 'Cover:1|Body1', 'part', 6, ((1, 1, 1), (2, 2, 2)), GROUPS, s))
        self.assertFalse(R.region_admits(g, 'Xn spare:1|Body1', 'part', 6, ((1, 1, 1), (2, 2, 2)), GROUPS, s))             # in no group
        self.assertTrue(R.region_admits(g, 'Pulley:1|Body1', 'part', 6, ((1, 1, 1), (2, 2, 2)), GROUPS, s))
        self.assertTrue(R.region_admits(g, '|Body1', 'part', 6, ((1, 1, 1), (2, 2, 2)), GROUPS, s))
        self.assertFalse(R.region_admits(dict(g, solid_only=True), 'Pulley:1|mesh:scan', 'mesh', 2, ((1, 1, 1), (2, 2, 2)), GROUPS, s))
        # and the host's World reads the same: random boxes, every filter
        rnd = random.Random(3)
        for _ in range(300):
            lo = tuple(rnd.uniform(-5, 15) for _ in range(3)); hi = tuple(c + rnd.uniform(0, 4) for c in lo)
            key = rnd.choice(['ref washer M3:1|Body1', 'Cover:1|Body1', 'Pulley:1|Body1', '|Body1', 'ref plate:1|mesh:scan'])
            kind = 'mesh' if 'mesh:' in key else ('ref' if key.startswith('ref') else 'part')
            r = rnd.choice([region, g, {'box': [[0, 0, 0], [10, 10, 10]], 'margin': 2.0, 'mode': 'tight', 'kinds': ['ref', 'mesh']}])
            w = gate_deps.World({key: gate_deps._line('s', kind, 6, (lo, hi))})
            got = key in (w.bodies_in([r], None, 'tight', GROUPS) or {})
            self.assertEqual(got, R.region_admits(r, key, kind, 6, (lo, hi), GROUPS, s, gate_deps.SLACK_MM), (key, r, lo, hi))
        self.assertEqual(gate_deps.World({'a:1|B': gate_deps._line('s', 'ref', 6, ((0, 0, 0), (1, 1, 1)))}).bodies_in(
            [dict(region, mode='wide')], None, 'tight'), {})                                                                  # a wide region is no tight one


if __name__ == '__main__':
    unittest.main()
