"""Heat-set pockets, the hole census and pockets for bought parts on models as people really draw them.

The gate was first tested against shapes made for the tests. These are the shapes a real assembly held, with
invented names and places, and what the gate must say about each:
  - a pocket whose relief was cut later is the pocket of its row; one that is cut too short is not;
  - a pocket in a spot face, a pocket with the screw hole below it, a post with a pocket at each end;
  - the insert the model puts into a hole decides which pocket it must be;
  - one pocket gives ONE row for its walls, one hole gets ONE question;
  - what was not measured is NOT_RUN, never a pass and never a FAIL.
The fixtures tests/fixtures/facts/*__inserts_*.json hold the failing case of each and its passing twin
(tests/test_judge_fixtures.py runs them); this file holds what a single fixture cannot show."""
import copy, json, os, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
import gate_judges, gate_params, gate_registry
from gate_judges import run_judge
from probes import _geom as G

FIXTURES = os.path.join(ROOT, 'tests', 'fixtures', 'facts')
REG, DEFAULTS, FX = None, None, {}
RELIEF, D65, D59, LEAD, HOLE = (7.5, 0.54), (6.5, 0.54), (5.9, 0.545), (4.4, 1.5), (3.6, 5.0)
INSERT_FACTS = ('inserts_wall', 'inserts_wall_at_relief', 'inserts_relief_missing', 'inserts_floor')


def setUpModule():
    global REG, DEFAULTS
    REG = gate_registry.load_default()
    DEFAULTS = gate_params.values(gate_params.resolve(REG))
    for name in sorted(os.listdir(FIXTURES)):
        if name.endswith('.json'):
            with open(os.path.join(FIXTURES, name), encoding='utf-8') as f:
                FX[name[:-5]] = json.load(f)


def segs(*pairs):
    return [({'d': p[0], 'len': p[1]} if len(p) == 2 else {'d': p[0], 'len': p[1], 'd2': p[2]}) for p in pairs]


def klass(*pairs, **kw):
    return G.classify_stack(segs(*pairs), kw.get('through', False), late_tables() if kw.get('late') else REG['tables'], kw.get('coaxial'))


def late_tables():
    """The shipped tables with the switch "hole_short_by_relief" on: the allowance the owner of a project may decide for."""
    tables = copy.deepcopy(REG['tables'])
    tables['inserts']['hole_short_by_relief'] = True
    return tables


def rule_of(judge):
    return next(r for r in REG['rules'].values() if (r.get('check') or {}).get('judge') == judge)


def judged(judge, facts, params=None, **ctx):
    p = dict(DEFAULTS)
    p.update(params or {})
    c = {'part': 'demo/bracket', 'rule': rule_of(judge)['id'], 'tables': REG['tables'], 'target': None, 'census': None, 'prev': None, 'intent': None}
    c.update(ctx)
    return run_judge(rule_of(judge), copy.deepcopy(facts), p, c)


def of_fixture(name, judge=None):
    fx = FX[name]
    return judged(judge or fx['judge'], fx['facts'], fx.get('params'), **(fx.get('ctx') or {}))


def match(c):
    return (c or {}).get('match'), (c or {}).get('kind'), (c or {}).get('role')


class Classifier(unittest.TestCase):
    """probes._geom.classify_stack on the stacks of a real assembly."""

    EXACT = ('exact', 'M3x4x5', 'insert_pocket')
    BAND = ('band', 'M3x4x5', 'insert_pocket')

    def test_the_shipped_table_does_not_allow_a_pocket_one_relief_short(self):
        self.assertIs(REG['tables']['inserts'].get('hole_short_by_relief'), False)             # the row was measured with its full depth
        self.assertEqual(REG['tables']['inserts'].get('hole_short_by_relief_tag'), 'D')        # the allowance is a decision nobody measured
        self.assertEqual(match(klass(D65, LEAD, (3.6, 4.46))), self.BAND)                      # 5.96 below the relief floor, the row says 6.5: a row of inserts.pocket_shape
        self.assertEqual(match(klass((13.2, 1.0), D59, LEAD, (3.6, 4.455))), self.BAND)
        self.assertEqual(match(klass(RELIEF, LEAD, (3.6, 4.7))), self.EXACT)                   # within the depth tolerance of the table
        self.assertEqual(match(klass((13.2, 1.0), D59, LEAD, (3.6, 5.0))), self.EXACT)         # and the spot face in front of it is still no lead-in

    def test_a_relief_cut_later_leaves_the_hole_one_relief_short(self):
        self.assertEqual(match(klass(D65, LEAD, (3.6, 4.46), late=True)), self.EXACT)
        self.assertEqual(match(klass((13.2, 1.0), D59, LEAD, (3.6, 4.455), late=True)), self.EXACT)       # in the floor of a spot face
        self.assertEqual(match(klass(RELIEF, LEAD, (3.6, 4.16), late=True)), self.EXACT)                  # the depth tolerance of the table still holds
        self.assertEqual(match(klass(RELIEF, LEAD, (3.6, 4.1), late=True)), self.BAND)                    # shorter than that: a wrong pocket
        self.assertEqual(match(klass(LEAD, (3.6, 4.46), late=True)), self.BAND)                           # no relief, no allowance
        self.assertEqual(match(klass(LEAD, (3.6, 4.7), late=True)), self.EXACT)

    def test_the_allowance_is_the_tables_switch(self):
        tables = copy.deepcopy(REG['tables'])
        tables['inserts']['hole_short_by_relief'] = False
        c = G.classify_stack(segs(D65, LEAD, (3.6, 4.46)), False, tables)
        self.assertEqual(match(c), self.BAND)
        del tables['inserts']['hole_short_by_relief']
        self.assertEqual(match(G.classify_stack(segs(D65, LEAD, (3.6, 4.46)), False, tables)), self.BAND)

    def test_a_narrower_tail_is_depth_below_the_insert(self):
        self.assertEqual(match(klass(RELIEF, LEAD, (3.6, 4.7), (3.4, 9.0), through=True)), self.EXACT)
        self.assertEqual(match(klass(LEAD, (3.6, 3.0), (3.2, 3.0))), self.EXACT)               # a tip hole
        self.assertEqual(match(klass(LEAD, (3.6, 1.9), (3.4, 12.0), through=True)), self.BAND)  # the insert would end in D3.4
        self.assertIsNone(klass((4.4, 7.0), (3.2, 3.0), through=True))                        # no D3.6 at all: nothing of the tables

    def test_the_insert_on_the_axis_decides_the_kind(self):
        long_ref, short_ref = ['HW (ref) / insert M3x6x5 (1)'], ['HW (ref) / insert M3x4x5 (1)']
        self.assertEqual(match(klass(RELIEF, LEAD, HOLE, coaxial=short_ref)), self.EXACT)
        self.assertEqual(match(klass(RELIEF, LEAD, HOLE, coaxial=long_ref)), ('band', 'M3x6x5', 'insert_pocket'))
        self.assertEqual(match(klass(RELIEF, LEAD, (3.6, 7.0), coaxial=long_ref)), ('exact', 'M3x6x5', 'insert_pocket'))
        # the long insert over a screw hole: its relief was cut later, so the D3.6 part is one relief depth short of 4.5
        self.assertEqual(match(klass(RELIEF, LEAD, (3.6, 4.16), (3.4, 9.0), through=True, coaxial=long_ref, late=True)), ('exact', 'M3x6x5', 'insert_pocket'))
        self.assertEqual(match(klass(RELIEF, LEAD, (3.6, 4.16), (3.4, 9.0), through=True, coaxial=long_ref)), ('band', 'M3x6x5', 'insert_pocket'))     # as shipped: a row
        self.assertEqual(match(klass(LEAD, (3.6, 4.16), (3.4, 9.0), through=True, coaxial=long_ref, late=True)), ('band', 'M3x6x5', 'insert_pocket'))

    def test_a_post_with_a_pocket_at_each_end(self):
        c = klass(RELIEF, LEAD, (3.6, 30.0), LEAD, RELIEF, through=True)
        self.assertEqual((match(c), c.get('ends')), (self.EXACT, 2))
        self.assertNotIn('ends', klass(RELIEF, LEAD, HOLE))
        self.assertEqual(match(klass(RELIEF, LEAD, (3.6, 5.9), LEAD, RELIEF, through=True)), self.BAND)      # too short for two inserts
        self.assertEqual(match(klass(RELIEF, LEAD, (3.6, 30.0), (5.1, 1.5), RELIEF, through=True)), self.BAND)   # one end is a wrong pocket
        self.assertNotEqual(match(klass(RELIEF, LEAD, (3.6, 30.0), LEAD, RELIEF)), self.EXACT)               # blind: it has no far mouth

    def test_pocket_ends_gives_the_far_pocket_seen_from_its_own_mouth(self):
        sg = segs(RELIEF, LEAD, (3.6, 30.0), LEAD, D65)
        h = {'id': 'h1', 'anchor': G.anchor_of('insert_pocket', (0, 1, 0), (5.0, 40.0, 7.0)), 'at': {'point': [5.0, 40.0, 7.0], 'axis': [0.0, 1.0, 0.0]},
             'segments': sg, 'depth': 34.08, 'through': True, 'class': {'table': 'inserts', 'kind': 'M3x4x5', 'role': 'insert_pocket', 'match': 'exact', 'ends': 2}}
        first, second = G.pocket_ends(h)
        self.assertEqual((first['end'], first['segments'], first['anchor']), ('mouth', sg, h['anchor']))
        self.assertEqual(second['end'], 'exit')
        self.assertEqual([s['d'] for s in second['segments']], [6.5, 4.4, 3.6, 4.4, 7.5])
        self.assertEqual(second['at'], {'point': [5.0, 5.92, 7.0], 'axis': [0.0, -1.0, 0.0]})
        self.assertEqual(second['anchor'], {'kind': 'insert_pocket', 'axis': [0.0, -1.0, 0.0], 'point': [5.0, 5.9, 7.0]})
        self.assertEqual(h['segments'], sg)                                                    # the census record is not changed
        one = dict(h, **{'class': dict(h['class'], ends=1)})
        self.assertEqual([p['end'] for p in G.pocket_ends(one)], ['mouth'])
        self.assertEqual([p['end'] for p in G.pocket_ends(dict(h, through=False))], ['mouth'])
        levels = G.pocket_levels(second['segments'], REG['tables']['inserts']['kinds']['M3x4x5'])
        self.assertEqual(levels['relief'], {'d': 6.5, 'depth': 0.54})                          # the far pocket has its own relief

    def test_a_counterbore_deep_in_an_access_bore(self):
        self.assertEqual(match(klass((7.0, 12.0), (6.5, 3.5), (3.4, 1.0), through=True)), ('exact', 'M3', 'counterbore'))
        self.assertEqual(match(klass((10.0, 5.0), (6.0, 3.5), (3.4, 6.0), through=True)), ('band', 'M3', 'counterbore'))    # an odd counterbore
        self.assertIsNone(klass((7.0, 12.0), (5.0, 3.5), (3.4, 1.0), through=True))          # no M3 head goes into D5.0
        self.assertEqual(match(klass((6.5, 3.5), (3.4, 6.5), through=True)), ('exact', 'M3', 'counterbore'))                # as before
        self.assertEqual(match(klass((6.5, 3.5), (3.4, 6.5), (6.5, 3.5), through=True)), ('exact', 'M3', 'counterbore'))    # one from each face

    def test_a_magnet_pocket_is_no_insert_pocket(self):
        c = klass((4.2, 1.7))
        self.assertNotEqual((c or {}).get('table'), 'inserts')                                 # under the band's least depth
        self.assertNotEqual((klass((4.2, 1.7), coaxial=['ref magnet D4 x 1.5']) or {}).get('table'), 'inserts')


class OneRowPerPocket(unittest.TestCase):
    """The walls of one pocket are one row; the same holds for a hole that cannot hold its insert."""

    def rows(self, facts, params=None, **ctx):
        out = {}
        for judge in INSERT_FACTS:
            for f in judged(judge, facts, params, **ctx):
                out.setdefault(tuple(f['anchor']['point']), []).append((f['rule'], f['verdict']))
        return out

    def test_no_fixture_pocket_gets_a_wall_row_from_both_rules(self):
        seen = 0
        for name, fx in sorted(FX.items()):
            if fx['judge'] not in INSERT_FACTS:
                continue
            walls = {}
            for judge in ('inserts_wall', 'inserts_wall_at_relief'):
                for f in judged(judge, fx['facts'], fx.get('params'), **(fx.get('ctx') or {})):
                    if f['verdict'] != 'NOT_RUN':
                        walls.setdefault(tuple(f['anchor']['point']), []).append(f['rule'])
            for place, rules in walls.items():
                if 'cause_of_its_own' in name or 'from_the_relief_of_the_next_pocket' in name:
                    self.assertEqual(sorted(rules), ['inserts.wall', 'inserts.wall_at_relief'], name)       # two causes: two rows, each with its own number
                    continue
                self.assertEqual(len(rules), 1, '%s: pocket at %s has rows %s' % (name, place, rules))
                seen += 1
        self.assertGreater(seen, 10)

    def test_which_rule_has_the_row(self):
        thin, ok, low = {'verdict': 'BLOCKED'}, None, {'verdict': 'FAIL'}
        fits, none = {'smaller': [5.9]}, {'smaller': []}
        carries = gate_judges._relief_carries
        self.assertFalse(carries(thin, None)); self.assertFalse(carries(None, None))
        self.assertTrue(carries(ok, none)); self.assertTrue(carries(ok, fits))                 # the walls pass: the relief wall is the only level
        self.assertTrue(carries(thin, fits)); self.assertTrue(carries({'verdict': 'GROW'}, fits))   # a smaller relief is a fix of its own
        self.assertFalse(carries(thin, none)); self.assertFalse(carries({'verdict': 'GROW'}, none))  # a wider wall fixes both: the wall row
        self.assertFalse(carries(low, fits))                                                   # a wall under the floor comes first
        pocket = {'kind': 'M3x4x5', 'ray': {'step': 0.1}}                                      # lead-in D4.4 under a D7.5 relief: the same outline leaves 1.55 less
        ctx = {'tables': gate_registry.load_default()['tables']}
        same = dict(none, relief_d=7.5, measure=1.75, verdict='BLOCKED'); own = dict(same, measure=0.2)
        wall = {'verdict': 'BLOCKED', 'lead_in': 3.3}
        cause = gate_judges._relief_own_cause
        self.assertFalse(cause(pocket, wall, same, ctx)); self.assertTrue(cause(pocket, wall, own, ctx))         # 0.2 towards a neighbour's relief: its own cause
        self.assertFalse(cause(pocket, wall, dict(own, measure=1.6), ctx))                     # within two ray steps of what the outline explains
        self.assertFalse(cause({'kind': 'no such kind'}, wall, own, ctx))                      # diameters that cannot be read: no second row

    def test_the_row_names_the_other_level_and_a_change_of_either_ends_a_waiver(self):
        facts = FX['inserts_wall__inserts_blocked_clamp_pocket_names_its_relief_wall']['facts']
        a = judged('inserts_wall', facts)[0]
        self.assertIn('wall 2.22 at the lead-in', a['text']); self.assertIn('1.45 beside its D5.9 melt relief', a['text'])
        self.assertEqual((a['measure'], a['limit']), (2.22, 4.0))
        f2 = copy.deepcopy(facts); f2['pockets'][0]['walls']['relief']['min'] = 1.3
        b = judged('inserts_wall', f2)[0]
        self.assertEqual(b['measure'], 2.22); self.assertNotEqual(a['context'], b['context'])   # the relief wall got thinner: ask again
        # much thinner beside the relief than the lead-in wall and the step from D4.4 to D5.9 explain (2.22 - 0.75 = 1.47): a cause of its own.
        # Then the worst number of the pocket is the number of its one row, in the relief rule
        f3 = copy.deepcopy(facts); f3['pockets'][0]['walls']['relief']['min'] = 0.2
        w, = judged('inserts_wall', f3)                                                          # the wall keeps its row and its verdict ...
        self.assertEqual((w['verdict'], w['measure']), (a['verdict'], 2.22))
        c, = judged('inserts_wall_at_relief', f3)                                                # ... and the relief wall has a row of its own
        self.assertEqual((c['verdict'] in ('BLOCKED', 'FAIL'), c['measure'], c['limit']), (True, 0.2, 2.45)); self.assertIn('also wall 2.22 at the lead-in', c['text'])
        facts = FX['inserts_wall_at_relief__inserts_fail_smaller_relief_fits_names_the_thin_wall']['facts']
        c = judged('inserts_wall_at_relief', facts)[0]
        self.assertIn('a D5.9 relief leaves 2.72', c['text']); self.assertIn('wall 3.8 at the lead-in', c['text']); self.assertIn('free space', c['text'])
        f2 = copy.deepcopy(facts); f2['pockets'][0]['walls']['lead_in']['min'] = 3.5
        self.assertNotEqual(c['context'], judged('inserts_wall_at_relief', f2)[0]['context'])

    def test_the_real_defects_still_fail_alone(self):
        # each of the three wall defects, with the other levels in order, is a row of its own rule
        self.assertEqual([f['verdict'] for f in of_fixture('inserts_wall__grow_2_5_free_all_round')], ['GROW'] * 4)
        self.assertEqual([f['verdict'] for f in of_fixture('inserts_wall__fail_1_5_under_the_floor')], ['FAIL'])
        self.assertEqual([f['verdict'] for f in of_fixture('inserts_wall_at_relief__fail_0_95_beside_the_relief')], ['FAIL', 'FAIL'])
        self.assertEqual([f['verdict'] for f in of_fixture('inserts_relief_missing__fail_no_relief_where_d7_5_fits')], ['FAIL'] * 6)
        # and the pocket of the first print that bulged (2.5 round the lead-in, 0.95 beside the relief) is one row that says both
        text = of_fixture('inserts_wall__grow_2_5_free_all_round')[0]['text']
        self.assertIn('wall 2.5', text); self.assertIn('0.95 beside its D7.5 melt relief', text)
        self.assertEqual(of_fixture('inserts_wall__grow_2_5_free_all_round', 'inserts_wall_at_relief'), [])

    def test_a_thin_wall_with_no_relief_and_no_room_for_one_is_one_row(self):
        fx = FX['inserts_wall__inserts_blocked_thin_wall_names_the_missing_relief']
        got = self.rows(fx['facts'])
        self.assertEqual(list(got.values()), [[('inserts.wall', 'BLOCKED')]])
        text = judged('inserts_wall', fx['facts'])[0]['text']
        self.assertIn('no melt relief', text); self.assertIn('the best, D5.9, leaves 1.1', text); self.assertIn('shave the melt ring', text)
        roomy = copy.deepcopy(fx['facts']); roomy['pockets'][0]['relief_room'][2]['wall'] = 2.5   # a relief fits: cutting it is a fix of its own
        self.assertEqual(sorted(self.rows(roomy).values())[0], [('inserts.wall', 'BLOCKED'), ('inserts.relief_missing', 'FAIL')])

    def test_a_hole_too_short_for_its_insert_is_one_row_of_the_floor_rule(self):
        for name in ('inserts_floor__inserts_fail_d4_2_x_2_1_hole_in_the_m3_band', 'inserts_floor__inserts_fail_d4_4_hole_through_a_0_7_sheet'):
            fx = FX[name]
            got = self.rows(fx['facts'], fx.get('params'), **fx['ctx'])
            self.assertEqual(list(got.values()), [[('inserts.floor', 'FAIL')]], name)
            self.assertEqual(judged('inserts_pocket_shape', fx['ctx']['census'], fx.get('params')), [], name)
        fx = FX['inserts_floor__inserts_not_run_piece_of_a_hole_with_an_open_wall']
        self.assertEqual(list(self.rows(fx['facts'], fx.get('params'), **fx['ctx']).values()), [[('inserts.floor', 'NOT_RUN')]])
        text = of_fixture('inserts_floor__inserts_not_run_piece_of_a_hole_with_an_open_wall')[0]['text']
        self.assertIn('81 %', text); self.assertIn('by hand', text)

    def test_a_pocket_deep_enough_keeps_its_wall_rows(self):
        # not every wrong pocket is silent in the wall rules: only the one that cannot hold the insert at all
        fx = FX['inserts_floor__inserts_fail_d4_2_x_2_1_hole_in_the_m3_band']
        deep = copy.deepcopy(fx['facts']); deep['pockets'][0]['segments'] = segs((4.4, 6.5))
        self.assertEqual(sorted(self.rows(deep, fx.get('params'))[tuple(deep['pockets'][0]['anchor']['point'])]),
                         [('inserts.relief_missing', 'FAIL'), ('inserts.wall', 'GROW')])

    def test_a_magnet_pocket_of_insert_size_is_not_judged_as_an_insert_pocket(self):
        # D4.2 x 2.1 is in the M3 band and deep enough for the band; at the size of the magnet parameter it is a magnet pocket
        fx = FX['inserts_floor__inserts_fail_d4_2_x_2_1_hole_in_the_m3_band']
        magnet = {'sensors.magnet_pocket_d': 4.2, 'sensors.magnet_pocket_depth': 2.1}
        self.assertEqual(self.rows(fx['facts'], magnet, **fx['ctx']), {})
        self.assertEqual(judged('inserts_pocket_shape', fx['ctx']['census'], magnet), [])
        self.assertEqual(judged('inserts_no_tapped_plastic', fx['ctx']['census'], magnet), [])
        tags = dict(magnet, **{'sensors.magnet_pocket_d#tag': 'D', 'sensors.magnet_pocket_depth#tag': 'D'})
        self.assertEqual([f['verdict'] for f in judged('fits_bought_pocket', fx['ctx']['census'], tags)], ['FAIL'])


class Texts(unittest.TestCase):
    def test_what_stops_a_wall_is_named_the_same_on_every_pocket_of_a_pattern(self):
        walls = [{'min_beyond': 'own cavity', 'blocked_by': [{'name': 'ref old overlay', 'hidden': True}, {'name': 'Cover', 'hidden': False}]}]
        named = ['own cavity', 'ref old overlay', 'Cover', 'Lid', 'Base']
        text = gate_judges._stop_text(walls, named)
        self.assertEqual(text, 'Base, Cover, Lid and 2 more')
        self.assertEqual(gate_judges._stop_text(walls, ['ref old overlay', 'Cover']), 'Cover, ref old overlay (hidden)')
        self.assertEqual(gate_judges._some_names(['a', 'b', 'c', 'd', 'e']), 'a, b, c and 2 more')
        self.assertEqual(gate_judges._some_names(['a', 'b']), 'a, b')

    def test_a_blocked_wall_says_when_part_of_the_thin_arc_is_free(self):
        facts = copy.deepcopy(FX['inserts_wall__blocked_by_a_bearing_seat']['facts'])
        text = judged('inserts_wall', facts)[0]['text']
        self.assertIn('stopped by ref bearing 6005', text); self.assertIn('free over 40 deg', text)
        for w in ('lead_in', 'insert'):
            facts['pockets'][0]['walls'][w]['free_arc_deg'] = 0
        self.assertNotIn('free over', judged('inserts_wall', facts)[0]['text'])

    def test_a_cone_is_shown_as_a_cone(self):
        self.assertEqual(gate_judges._shape(segs((30.0, 3.5, 24.0), (24.0, 8.0))), 'D30>24 x 3.5 + D24 x 8')

    def test_a_wrong_pocket_in_a_spot_face_is_named_by_the_pocket(self):
        text = of_fixture('inserts_pocket_shape__inserts_fail_straight_d4_4_in_a_spot_face')[0]['text']
        self.assertIn('it has no D3.6 hole', text); self.assertNotIn('lead-in is D13.2', text)
        fx = FX['inserts_pocket_shape__inserts_fail_hole_shorter_than_a_late_relief_allows']
        text = judged('inserts_pocket_shape', fx['facts'], fx.get('params'), tables=late_tables())[0]['text']       # with the allowance on: its least depth is named
        self.assertIn('4.46 under a relief that was cut later', text)
        short = of_fixture('inserts_pocket_shape__final_fail_relief_cut_later_hole_one_relief_short')              # as shipped: one relief depth short is a row
        self.assertEqual([f['verdict'] for f in short], ['FAIL', 'FAIL'])
        for f in short:
            self.assertIn('the table says 5: one relief depth short (a relief that is cut into a pocket later', f['text']); self.assertNotIn('lead-in is D13.2', f['text'])
        self.assertEqual([f for f in judged('inserts_pocket_shape', FX['inserts_pocket_shape__final_fail_relief_cut_later_hole_one_relief_short']['facts'],
                                            FX['inserts_pocket_shape__final_fail_relief_cut_later_hole_one_relief_short'].get('params'), tables=late_tables())
                          if 'printed under named settings' not in f['text']], [])        # the profile row; the row of where it was printed is another rule's matter
        text = of_fixture('inserts_pocket_shape__inserts_fail_hole_gets_narrower_before_the_insert_ends')[0]['text']
        self.assertIn('gets narrower there (D3.4)', text)


class OneQuestionPerHole(unittest.TestCase):
    def asks(self, facts, params=None):
        p = dict({'screws.ref_pattern': '(?i)screw|bolt', 'sensors.magnet_pocket_d': 5.2, 'sensors.magnet_pocket_depth': 2.1,
                  'sensors.magnet_pocket_d#tag': 'D', 'sensors.magnet_pocket_depth#tag': 'D'}, **(params or {}))
        out = {}
        for judge in ('census_unknown_hole', 'fits_bought_pocket', 'inserts_no_tapped_plastic', 'inserts_pocket_shape'):
            for f in judged(judge, facts, p):
                out.setdefault(tuple(f['anchor']['point']), []).append(f['ask'] or f['rule'])
        return out

    def test_no_hole_of_any_census_fixture_is_asked_twice(self):
        seen = 0
        for name, fx in sorted(FX.items()):
            if not isinstance(fx['facts'].get('holes'), list) or fx['judge'].startswith(('regression', 'access', 'bearings')):
                continue
            for place, asked in self.asks(fx['facts'], fx.get('params')).items():
                self.assertEqual(len(asked), 1, '%s: hole at %s: %s' % (name, place, asked))
                seen += 1
        self.assertGreater(seen, 15)

    def test_who_asks(self):
        hole = FX['census_unknown_hole__ask_d33_5_hole_fits_no_table']['facts']
        self.assertEqual(list(self.asks(hole).values()), [['census.unknown_hole']])            # through, nothing in it: what is it for
        blind = copy.deepcopy(hole); blind['holes'][0].update(through=False, exit=None, floor=2.0)
        self.assertEqual(list(self.asks(blind).values()), [['fits.pocket_content']])           # blind: what goes in
        pin = copy.deepcopy(hole); pin['holes'][0]['coaxial'] = [{'name': 'ref shaft 33', 'kind': 'bought', 'part': None}]
        self.assertEqual(list(self.asks(pin).values()), [['fits.pocket_content']])             # a bought part on its axis: what goes in
        cover = copy.deepcopy(hole); cover['holes'][0]['coaxial'] = [{'name': 'Cover', 'kind': 'printed', 'part': 'demo/cover'}]
        self.assertEqual(list(self.asks(cover).values()), [['census.unknown_hole']])           # a printed neighbour is no bought part

    def test_a_piece_of_another_hole(self):
        fx = FX['census_unknown_hole__inserts_pass_short_cylinder_at_the_bottom_of_a_tapered_seat']
        seat, ring = fx['facts']['holes']
        piece = gate_judges._piece_of
        self.assertEqual(piece(ring, [seat, ring]), 'h6')
        self.assertIsNone(piece(seat, [seat, ring]))                                           # the larger one is no piece of the smaller
        self.assertIsNone(piece(ring, [ring]))
        moved = copy.deepcopy(ring); moved['anchor']['point'][0] = 0.5                         # 0.5 mm off the axis: another hole
        self.assertIsNone(piece(moved, [seat, moved]))
        wide = copy.deepcopy(ring); wide['segments'][0]['d'] = 23.0                            # not the diameter of the seat there
        self.assertIsNone(piece(wide, [seat, wide]))
        out = copy.deepcopy(ring); out['anchor']['point'][1] = 30.0                            # past the end of the seat
        self.assertIsNone(piece(out, [seat, out]))
        for odd in (None, 'x', [], {}, {'anchor': 'x'}, {'segments': [{'d': 'x', 'len': 1}]}):
            self.assertIsNone(piece(odd if isinstance(odd, dict) else {'segments': odd}, [seat]))
            self.assertIsNone(piece(ring, [odd, seat][:1] if isinstance(odd, dict) else [{'at': odd}]))

    def test_a_skin_is_thinner_than_the_hole_behind_it(self):
        fx = FX['census_unknown_hole__inserts_pass_counterbore_with_a_bridge_layer']
        ctx = {'tables': REG['tables']}
        cb, cl = fx['facts']['holes']
        c = gate_judges._hole_class(cb, ctx)
        self.assertEqual((c['table'], c['kind'], c['role'], c['behind_cap']), ('screws', 'M3', 'counterbore', 0.3))
        thick = copy.deepcopy(cb); thick['behind']['cap'] = 3.4                                # as thick as the hole behind is wide: a wall
        self.assertIsNone(gate_judges._hole_class(thick, ctx))
        odd = copy.deepcopy(cb); odd['behind']['d'] = 9.0                                      # the hole behind is wider: no counterbore over it
        self.assertIsNone(gate_judges._hole_class(odd, ctx))
        pocket = copy.deepcopy(cb); pocket['segments'] = segs((10.4, 2.0)); pocket['behind'] = {'hole': 'h9', 'cap': 0.3, 'd': 4.4}
        self.assertIsNone(gate_judges._hole_class(pocket, ctx))                                # only a screw counterbore is taken from a pair

    def test_long_channels_are_no_pockets_broken_open(self):
        self.assertAlmostEqual(gate_judges._pocket_len_max(4.4, {'tables': REG['tables']}), 8.5 + 0.54 + 0.3)
        self.assertIsNone(gate_judges._pocket_len_max(12.0, {'tables': REG['tables']}))
        fx = FX['census_unknown_hole__inserts_pass_long_broken_open_channel']
        short = copy.deepcopy(fx['facts']); short['partial'][0]['len'] = 6.5
        self.assertEqual([f['ask'] for f in judged('census_unknown_hole', short, fx['params'])], ['census.unknown_hole'])


class NeverSilent(unittest.TestCase):
    def test_a_pocket_the_probe_did_not_measure_is_not_run(self):
        out = of_fixture('inserts_wall__inserts_not_run_pocket_of_todays_tables_has_no_measured_walls')
        self.assertEqual([f['verdict'] for f in out], ['NOT_RUN']); self.assertIn('collect the facts of this part again', out[0]['text'])
        self.assertEqual(out[0]['anchor']['kind'], 'insert_pocket')
        out = of_fixture('inserts_wall__inserts_not_run_far_pocket_of_a_post_was_not_measured')
        self.assertEqual(out[0]['anchor']['point'], [0.0, 5.9, 5.0]); self.assertIn('at each end', out[0]['text'])

    def test_odd_census_values_do_not_raise(self):
        odd = [None, 'x', True, float('nan'), [], {}, -1, [[1]], {'a': {}}, 1e308]
        for name in ('inserts_floor__inserts_not_run_piece_of_a_hole_with_an_open_wall', 'inserts_wall__inserts_pass_both_pockets_of_a_post_measured',
                     'census_unknown_hole__inserts_pass_counterbore_with_a_bridge_layer', 'census_unknown_hole__inserts_pass_short_cylinder_at_the_bottom_of_a_tapered_seat'):
            fx = FX[name]
            base = (fx.get('ctx') or {}).get('census') or fx['facts']
            for i in range(len(base['holes'])):
                for key in sorted(base['holes'][i]):
                    for value in odd:
                        census = copy.deepcopy(base); census['holes'][i][key] = copy.deepcopy(value)
                        judges = INSERT_FACTS if 'ctx' in fx else ('census_unknown_hole', 'fits_bought_pocket', 'inserts_pocket_shape', 'inserts_no_tapped_plastic', 'print_z_layers')
                        for judge in judges:
                            facts = fx['facts'] if 'ctx' in fx else census
                            out = judged(judge, facts, fx.get('params'), census=census)
                            self.assertFalse([f for f in out if 'failed:' in f['text']], '%s %s %s=%r: %s' % (name, judge, key, value, out))
                            json.dumps(out, allow_nan=False)


if __name__ == '__main__':
    unittest.main()
