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
from test_geom_rings import Add, Blk, Body, Comp, Rod

NOT_PRINTED = re.compile(r'^ref ')
GROUPS = [{'key': 'frame', 'patterns': ['^Bracket', '^Base', '^Cover', '^Xb']}, {'key': 'arm', 'patterns': ['^Pulley', '^Xm']}]
NAMES = ('Xb rib %d', 'Xm arm %d', 'Xn spare %d', 'ref plate %d', 'ref bolts %d', 'ref washer M3 %d', 'ref insert M3x4x5 %d',
         'ref nut M3 %d', 'ref bearing 608 %d', 'ref screw M3x8 cap %d')
PLUS_NAMES = ('ref bolt kit + spacer %d', 'ref screws: M3 + washers %d', 'Xb rib + foot %d', 'ref washer M3 + nut %d',
              'ref insert M3x4x5 + glue: 2 %d', 'ref plate + bolts %d', 'ref nut M3 + stud %d', 'ref screw M3x8 cap + spring %d')


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

    def try_bodies(self, probe, n=24, seed=7, groups=None, features=None, near=20.0, names=NAMES):
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
            name = names[i % len(names)] % i
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

    def test_screws_component_names_that_hold_plus_and_colon(self):
        # 0.3.3 proof: the component name was cut at the last "+" of the path, so a screw of "ref bolt kit + spacer" was
        # tested as " spacer" and never held by the tight record. The same random trial, with such names
        kept, held = self.try_bodies('fusion_screws', n=80, seed=11, features=self.FEATURES, near=8.0, names=PLUS_NAMES)
        self.assertGreater(held, 10)

    def screw_into_h2(self, comp_name, path=None):
        """(tight record changed, wide record changed, facts changed, cover-check notes) when a new M3 screw goes into the
        blind hole h2 (30, 30) of the bracket from a component of this name (at `path` when given: an assembly)."""
        base = self.bracket_scene()
        env0 = self.unit('fusion_screws', base, features=self.FEATURES)
        reads, own = env0['reads'], gate_deps.body_key('Bracket:1', 'Body1')
        screw = Add(Rod(30, 30, 4.0, 10.0, 1.5), Rod(30, 30, 10.0, 13.0, 2.75))
        scene = self.bracket_scene() + [Comp(comp_name, [Body('Body1', screw)])]
        mods = T.fake_adsk(scene)
        lines = lines_of(scene)
        if path is not None:
            occ = mods['adsk.core'].Application.get().documents[0].products.itemByProductType(None).rootComponent.allOccurrences[-1]
            occ.fullPathName = path
            lines[gate_deps.body_key(path, 'Body1')] = lines.pop(gate_deps.body_key('%s:1' % comp_name, 'Body1'))
        env1 = self.unit('fusion_screws', mods, features=self.FEATURES)
        t = gate_deps.World(lines_of(base)).bodies_in(reads, own, 'tight') != gate_deps.World(lines).bodies_in(reads, own, 'tight')
        w = gate_deps.World(lines_of(base)).bodies_in(reads, own, 'wide') != gate_deps.World(lines).bodies_in(reads, own, 'wide')
        changed = gate_deps.facts_view(env0['facts']) != gate_deps.facts_view(env1['facts'])
        missed = [n for n in env1.get('notes') or [] if 'did not hold' in n]
        return t, w, changed, missed, env1['facts']

    def test_screws_a_new_screw_whose_component_name_holds_a_plus_is_held(self):
        # the repro of the proof: a screw that bottoms out in h2 (a new screw row) from "ref bolt kit + spacer"
        for comp, path in (('ref bolt kit', None), ('ref bolt kit + spacer', None), ('ref bolts: M3 + spacer', None),
                           ('ref bolt kit + spacer', 'Drive (A + B):1+ref bolt kit + spacer:2'),
                           ('ref bolt kit + spacer', 'ref odd:3+name:1+ref bolt kit + spacer:4')):
            t, w, changed, missed, facts = self.screw_into_h2(comp, path)
            self.assertTrue(changed, comp)
            self.assertIn(comp, [s['ref'] for s in facts['screws']], comp)
            self.assertEqual((t, w, missed), (True, True, []), (comp, path))

    def test_component_names_are_cut_only_after_a_copy_number(self):
        self.assertEqual(R.path_names(''), [])
        self.assertEqual(R.path_names('Gearbox:1+lid + screws:2+Rotor:1'), ['Gearbox', 'lid + screws', 'Rotor'])
        self.assertEqual(R.path_names('shaft: pin 6x50 + sleeves:3'), ['shaft: pin 6x50 + sleeves'])
        self.assertEqual(R.path_names('Motor: 57 + brake:12+Rotor:1'), ['Motor: 57 + brake', 'Rotor'])
        self.assertEqual(R.comp_names(''), [''])
        self.assertEqual(R.comp_names('A:1+odd:3+name:2'), ['name', 'odd:3+name', 'A:1+odd:3+name'])     # a name that holds ":3+" itself
        self.assertEqual(R.key_names('Drive kit (ref): cap + bolts:1|mesh:scan'), ('Drive kit (ref): cap + bolts:1', 'Drive kit (ref): cap + bolts', 'scan'))
        s, box = gate_deps.search, ((1, 1, 1), (2, 2, 2))
        named = {'box': [[0, 0, 0], [10, 10, 10]], 'margin': 0.0, 'names': ['^%s$' % re.escape('ref bolt kit + spacer / Body1')]}
        self.assertTrue(R.region_admits(named, 'X:1+ref bolt kit + spacer:2|Body1', 'ref', 6, box, [], s))      # the anchored hole-axis name
        self.assertTrue(R.region_admits(dict(named, names=['^ref bolt kit \\+ spacer$']), 'ref bolt kit + spacer:1|Body1', 'ref', 6, box, [], s))
        self.assertTrue(R.region_admits(dict(named, names=['^odd:3\\+name$']), 'A:1+odd:3+name:2|Body1', 'ref', 6, box, [], s))
        self.assertFalse(R.region_admits(dict(named, names=['^spacer$']), 'ref bolt kit + spacer:1|Body1', 'ref', 6, box, [], s))
        self.assertEqual(gate_deps._diff_bodies({}, {'Drive (A + B):1+ref kit + spacer:2|Body1': 's'}), 'ref kit + spacer:2 came into what it read (new, or moved close)')

    def test_the_printed_rule_reads_whole_component_names(self):
        # the same cut in every printed rule (the walk, the scan, the census, the inserts probe): an assembly
        # "Drive (A + B)" that not_printed names by its whole name holds no printed part
        g = {}
        mods = T.fake_adsk([])
        saved = {k: sys.modules.get(k) for k in mods}
        sys.modules.update(mods)
        try:
            for f in ('_geom.py', '_geom_rings.py', '_fusion.py'):
                with open(os.path.join(ROOT, 'tools', 'gate', 'probes', f), encoding='utf-8') as fh:
                    exec(compile(fh.read(), f, 'exec'), g)
        finally:
            for k, v in saved.items():
                if v is None: sys.modules.pop(k, None)
                else: sys.modules[k] = v
        job = {'not_printed': [r'^Drive \(A \+ B\)$']}
        self.assertFalse(g['gate_path_printed']('Drive (A + B):1+Plate:1', 'Plate', 'Body1', job))
        self.assertTrue(g['gate_path_printed']('Frame:1+Plate:1', 'Plate', 'Body1', job))
        for f, fn in (('_fusion.py', 'gate_path_printed'), ('fusion_scan.py', '_scan_printed'), ('fusion_census.py', '_cen_printed'),
                      ('fusion_inserts.py', '_ins_printed')):
            with open(os.path.join(ROOT, 'tools', 'gate', 'probes', f), encoding='utf-8') as fh:
                text = fh.read()
            body = text[text.index('def %s(' % fn):]
            self.assertIn('names = path_names(path) + [comp_name, body_name]', body[:body.index('\ndef ', 1)], f)

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
