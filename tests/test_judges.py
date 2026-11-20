"""The judges of tools/gate/gate_judges.py, beyond what their fixtures say.

tests/test_judge_fixtures.py runs every tests/fixtures/facts/<judge>__<case>.json against its expected findings.
This file holds what a single fixture cannot show:
  - every judge is built and has a failing fixture and its passing twin; the incidents that paid for the rules are there;
  - what every finding looks like, that judges are pure, and that a judge reads only the parameters its rule lists;
  - nothing that was not measured reads as a pass: a missing list, unreadable rows, a probe that did not look far enough;
  - the pass line is the parameter, exactly: on the limit passes, just under it does not (a length found along a line by
    sampling is first put on the 0.05 measurement grid: 4.99 is 5.0, 4.97 is 4.95);
  - the verdict tables of the rules with more than one verdict, the assembly-order question, the per-size limits;
  - a finding keeps its id while its feature does not change, so a waiver holds, and loses it when the feature changes.
No Fusion, no files but the fixtures."""
import copy, hashlib, inspect, json, os, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
import gate_common, gate_features, gate_judges, gate_params, gate_registry
from gate_judges import run_judge

FIXTURES = os.path.join(ROOT, 'tests', 'fixtures', 'facts')
REG, DEFAULTS, FX = None, None, {}

# The numbers these tests are written against. They are laid over the registry defaults, so a default that another
# change moves does not move these tests; Defaults (below) asks separately what the shipped defaults catch.
PIN = {
    'inserts.wall_target': 4.0, 'inserts.wall_floor': 1.6, 'inserts.relief_wall_min': 2.45, 'inserts.relief_depth_min': 0.4,
    'inserts.floor_min': 0.6, 'inserts.iron_d': 8.0, 'inserts.iron_reach': 30.0,
    'bearings.cover_share_min': 0.5, 'bearings.clamp_points_min': 4, 'bearings.contact_gap_max': 0.05, 'bearings.clamp_room': 1.0,
    'bearings.seat_d_tol': 0.3,
    'walls.min_wall_effective': 1.5, 'walls.coverage_min': 0.9, 'walls.slit_min': 0.3, 'walls.min_face_width': 1.2, 'target.line_width': 0.42,
    'access.reach': 60.0, 'access.min_blocked_share': 0.02, 'access.seal_gap': 0.2, 'access.cap_max': 2.0, 'access.tool_clearance': 1.0,
    'screws.min_engagement': 3.2, 'screws.min_engagement[M2]': 2.0, 'screws.min_engagement[M2.5]': 2.5, 'screws.min_engagement[M3]': 3.2,
    'screws.min_engagement[M4]': 4.0, 'screws.min_engagement[M5]': 5.0, 'screws.min_engagement[M6]': 6.0,
    'screws.min_under_head': 5.0, 'screws.tip_room_min': 0.5,
    'washers.landing_share_min': 0.95, 'washers.landing_gap_max': 0.05, 'washers.spot_face_clearance': 1.2, 'washers.gap_to_neighbour': 0.5,
    'clearance.min_gap_moving': 2.0,
}

# The incidents the gate was built for, each as a failing fixture (invented part names, the real numbers).
INCIDENTS = '''
inserts_wall__grow_2_5_free_all_round
inserts_wall_at_relief__fail_0_95_beside_the_relief
inserts_pocket_shape__fail_straight_d4_4
inserts_pocket_shape__fail_d5_1_lead_in_over_d4_6
inserts_relief_missing__fail_no_relief_where_d7_5_fits
inserts_floor__fail_0_2_skin_under_the_pocket
inserts_floor__fail_insert_longer_than_the_plate
inserts_mouth_reachable__fail_mouth_under_a_collar_wall
bearings_holding__fail_ring_face_touched_by_nothing
bearings_holding__fail_clamp_touches_both_rings
bearings_holding__fail_bearing_with_no_holding_data
bearings_clamp_room__fail_room_0_3_and_0_where_1_0_is_needed
bearings_seat__fail_entry_ring_1_2_under_the_outer_diameter
walls_min_material__fail_neck_0_86_under_a_hook
walls_one_line__fail_tube_wall_0_93_on_a_0_6_nozzle
walls_slit__fail_slit_0_3_under_one_line
walls_thin_faces__fail_ledge_1_0_and_crescents_0_2
access_screw_path__fail_ledge_over_two_screw_heads
access_hole_covered__fail_hole_closed_by_a_bought_part
access_hole_closed__fail_cap_0_84_in_a_screw_hole
access_hole_closed__fail_clearance_holes_stop_1_5_before_their_pockets
access_mouth_sealed__fail_pocket_sealed_on_a_lug
screws_engagement__fail_2_4_mm_in_a_4_mm_insert
screws_under_head__fail_0_1_under_countersunk_heads
screws_tip_bottoming__fail_tip_0_6_past_the_pocket_end
washers_landing__fail_ring_over_a_pocket_mouth_with_a_melt_ring
washers_proud__fail_washer_and_head_touch_a_motor_body
clearance_min_gap_moving__fail_0_5_to_a_turning_pulley
mesh_closed__fail_open_seam_of_a_revolved_face
mesh_one_shell__fail_two_shells
project_object_on_no_plate__fail_object_left_off_the_plates
project_stl_without_stamp__fail_stl_from_another_exporter
project_stl_without_object__fail_stl_matches_no_object
project_mesh_stale__fail_project_holds_the_part_without_its_new_pocket
project_target_differs__fail_toothed_part_judged_for_a_0_4_nozzle
project_build_table_differs__fail_table_lists_a_part_on_its_old_plate
regression_feature_lost__fail_tie_anchors_gone_after_a_move
regression_feature_lost__fail_two_screw_holes_half_filled_by_a_join
regression_unintended__fail_neighbour_changed_by_a_cut_meant_for_one_part
'''.split()

# The list of its facts each judge walks. The two mesh judges and project_build_table_differs read single values.
READS = {
    'inserts_pocket_shape': 'holes', 'census_unknown_hole': 'holes', 'regression_feature_lost': 'holes',
    'inserts_wall': 'pockets', 'inserts_wall_at_relief': 'pockets', 'inserts_floor': 'pockets', 'inserts_relief_missing': 'pockets',
    'inserts_mouth_reachable': 'openings', 'access_hole_covered': 'openings', 'access_screw_path': 'openings', 'access_mouth_sealed': 'openings',
    'access_hole_closed': 'closed',
    'bearings_holding': 'bearings', 'bearings_clamp_room': 'bearings', 'bearings_seat': 'bearings',
    'walls_min_material': 'regions', 'walls_slit': 'regions', 'walls_one_line': 'regions', 'walls_thin_faces': 'faces',
    'screws_engagement': 'screws', 'screws_under_head': 'screws', 'screws_tip_bottoming': 'screws',
    'washers_landing': 'washers', 'washers_proud': 'washers', 'hardware_recognised': 'screws',
    'clearance_min_gap_moving': 'gaps',
    'project_stl_without_stamp': 'stls', 'project_stl_without_object': 'stls',
    'project_object_on_no_plate': 'objects', 'project_object_not_printable': 'objects', 'project_mesh_unstamped': 'objects',
    'project_mesh_stale': 'objects', 'project_target_differs': 'objects',
    'regression_unintended': 'changed',
}
# A row with nothing in it ({}) is reported by another judge of the same facts, not by these:
EMPTY_ROW_IS_ANOTHER_JUDGES = {
    'walls_slit': 'a region with no kind is taken as a wall: walls_min_material and walls_one_line report it',
    'inserts_mouth_reachable': 'an opening with no kind is judged by access_hole_covered',
    'access_screw_path': 'an opening with no kind is judged by access_hole_covered',
    'access_mouth_sealed': 'an opening with no kind is no pocket or hole: access_hole_covered reports it',
    'project_mesh_stale': 'an object with no stamp state is reported by project_mesh_unstamped',
    'project_target_differs': 'an object with no stamp is reported by project_mesh_unstamped',
}


def setUpModule():
    global REG, DEFAULTS
    REG = gate_registry.load_default()
    DEFAULTS = gate_params.values(gate_params.resolve(REG))
    for name in sorted(os.listdir(FIXTURES)):
        if name.endswith('.json'):
            with open(os.path.join(FIXTURES, name), encoding='utf-8') as f:
                FX[name[:-5]] = json.load(f)


def rule_of(judge):
    return next(r for r in REG['rules'].values() if (r.get('check') or {}).get('judge') == judge)


def ctx_of(judge, fx=None, **kw):
    c = {'part': 'demo/bracket', 'rule': rule_of(judge)['id'], 'tables': REG['tables'], 'target': None, 'census': None, 'prev': None, 'intent': None}
    c.update(copy.deepcopy((fx or {}).get('ctx') or {}))
    c.update(kw)
    return c


def params_of(fx=None, more=None):
    p = dict(DEFAULTS)
    p.update(PIN)
    p.update((fx or {}).get('params') or {})
    p.update(more or {})
    return p


def facts_of(name):
    return copy.deepcopy(FX[name]['facts'])


def judged(name, facts=None, params=None, **ctx):
    """The findings run_judge gives. `name` is a fixture ("judge__case": its facts, params and ctx are the base) or a bare
    judge name; `facts`, `params` and ctx keys given here lie over the base."""
    fx = FX[name] if '__' in name else {'judge': name}
    judge = fx['judge']
    return run_judge(rule_of(judge), facts_of(name) if facts is None else facts, params_of(fx, params), ctx_of(judge, fx, **ctx))


def verdicts(out):
    return [f['verdict'] for f in out]


def slots(obj):
    """Every (container, key) inside a JSON value: each key of each dict and each index of each list, at any depth."""
    out = []
    if isinstance(obj, dict):
        for k in sorted(obj):
            out.append((obj, k))
            out += slots(obj[k])
    elif isinstance(obj, list):
        for i in range(len(obj)):
            out.append((obj, i))
            out += slots(obj[i])
    return out


def first_fixture(judge, failing):
    """The first fixture of a judge that expects findings (failing True) or none."""
    return next(n for n in sorted(FX) if FX[n]['judge'] == judge and bool(FX[n]['expect']) == failing)


class Coverage(unittest.TestCase):
    def test_every_judge_of_the_registry_is_built(self):
        self.assertEqual(sorted(gate_judges.JUDGES), gate_registry.judge_names(REG))
        self.assertEqual([n for n in gate_judges.JUDGES if not gate_judges.is_built(n)], [])

    def test_every_judge_has_a_failing_fixture_and_its_passing_twin(self):
        for judge in sorted(gate_judges.JUDGES):
            mine = [fx for fx in FX.values() if fx['judge'] == judge]
            self.assertTrue([fx for fx in mine if fx['expect']], '%s has no fixture that expects a finding' % judge)
            self.assertTrue([fx for fx in mine if fx['expect'] == []], '%s has no fixture that passes' % judge)

    def test_the_incidents_that_paid_for_the_rules_are_fixtures(self):
        for name in INCIDENTS:
            self.assertIn(name, FX)
            stops = [e for e in FX[name]['expect'] if e.get('verdict') in gate_common.BLOCKING]
            self.assertTrue(stops, '%s expects no finding that stops a stamp' % name)

    def test_a_failing_fixture_stops_the_stamp_or_asks(self):
        for name, fx in FX.items():
            for f in judged(name):
                self.assertTrue(f['verdict'] in gate_common.BLOCKING or f['ask'] or f['verdict'] == 'INFO', name)
            if fx['judge'] == 'census_unknown_hole' and fx['expect']:
                self.assertEqual({(f['verdict'], f['ask']) for f in judged(name)}, {('INFO', 'census.unknown_hole')})

    def test_a_fixture_names_every_limit_it_is_judged_against(self):
        # a fixture is "these facts against these limits": it must not change its answer when a registry default moves
        for name, fx in FX.items():
            rule = rule_of(fx['judge'])
            moved = {k: (v + 1.0 if isinstance(v, (int, float)) and not isinstance(v, bool) else v) for k, v in DEFAULTS.items()}
            moved.update(fx.get('params') or {})
            out = run_judge(rule, facts_of(name), moved, ctx_of(fx['judge'], fx))
            self.assertEqual([(f['verdict'], f['measure'], f['limit']) for f in out],
                             [(f['verdict'], f['measure'], f['limit']) for f in judged(name)], name)

    def test_no_project_detail_in_the_fixtures(self):
        for name in sorted(n for n in os.listdir(FIXTURES) if n.endswith('.json')):
            with open(os.path.join(FIXTURES, name), encoding='utf-8') as f:
                text = f.read()
            self.assertTrue(all(ord(c) < 128 for c in text), name)
            self.assertFalse('/' + 'Users/' in text or '/' + 'home/' in text, name)
            fx = json.loads(text)
            for part in [fx.get('ctx', {}).get('part')] + [e.get('part') for e in fx['expect']]:
                self.assertTrue(part is None or part.startswith(('demo', 'main:')), '%s: part %r is not an invented demo part' % (name, part))


class Contract(unittest.TestCase):
    """What every finding of every judge looks like, on every fixture."""

    def test_findings_have_the_whole_schema(self):
        keys = ['anchor', 'ask', 'by', 'context', 'fix', 'limit', 'measure', 'part', 'rule', 'text', 'unit', 'verdict']
        asks = {r['id'] for r in gate_registry.rules(REG, level='attest')}
        for name, fx in FX.items():
            rule = rule_of(fx['judge'])
            for f in judged(name):
                self.assertEqual(sorted(f), keys, name)
                self.assertEqual(f['rule'], rule['id'], name)
                self.assertIn(f['verdict'], gate_common.VERDICTS, name)
                self.assertRegex(f['context'], '^[0-9a-f]{16}$', name)
                self.assertEqual(f['anchor'], gate_features.anchor(f['anchor']['kind'], f['anchor']['axis'], f['anchor']['point']), name)
                self.assertTrue(isinstance(f['by'], list) and all(isinstance(b, str) and b for b in f['by']), name)
                self.assertTrue(f['text'] and '\n' not in f['text'], name)
                self.assertNotIn('None', f['text'], '%s: %s' % (name, f['text']))
                self.assertTrue(isinstance(f['part'], str) and f['part'], name)
                self.assertEqual(f['fix'], rule['fix_order'], name)
                self.assertTrue(f['ask'] is None or f['ask'] in asks, name)
                for k in ('measure', 'limit'):
                    self.assertTrue(f[k] is None or (isinstance(f[k], (int, float)) and not isinstance(f[k], bool)), name)
                json.dumps(f)

    def test_a_finding_with_a_measure_names_its_limit(self):
        for name in FX:
            for f in judged(name):
                if f['measure'] is not None and f['verdict'] != 'INFO' and f['rule'] != 'bearings.seat':
                    self.assertIsNotNone(f['limit'], '%s: %s' % (name, f['text']))

    def test_no_two_findings_of_one_run_share_an_id(self):
        # one id = one waiver or one answer: two features under one id would be closed together
        for name in FX:
            ids = [gate_features.finding_id(f) for f in judged(name)]
            self.assertEqual(len(ids), len(set(ids)), name)

    def test_judges_are_pure(self):
        for name, fx in FX.items():
            facts, params, ctx = facts_of(name), params_of(fx), ctx_of(fx['judge'], fx)
            before = copy.deepcopy((facts, params, ctx))
            a = gate_judges.JUDGES[fx['judge']](facts, params, ctx)
            self.assertEqual((facts, params, ctx), before, '%s changed what it was given' % name)
            self.assertEqual(a, gate_judges.JUDGES[fx['judge']](facts, params, ctx), '%s is not repeatable' % name)

    def test_a_judge_reads_only_the_parameters_its_rule_lists(self):
        for name, fx in FX.items():
            rule = rule_of(fx['judge'])
            every = params_of(fx)
            listed = {k: v for k, v in every.items() if k.split('[')[0].split('#')[0] in rule['params']}      # a per-size value and the tag of a fit travel with their parameter
            out = run_judge(rule, facts_of(name), listed, ctx_of(fx['judge'], fx))
            self.assertEqual(out, judged(name), name)
            self.assertFalse([f for f in out if 'failed:' in f['text']], name)

    def test_the_judge_hash_covers_the_helpers_a_judge_calls(self):
        for name in gate_judges.JUDGES:
            self.assertRegex(gate_judges.judge_hash(name), '^[0-9a-f]{16}$')
        self.assertEqual(len({gate_judges.judge_hash(n) for n in gate_judges.JUDGES}), len(gate_judges.JUDGES))
        own = hashlib.sha256(inspect.getsource(gate_judges.JUDGES['inserts_wall']).encode()).hexdigest()[:16]
        self.assertNotEqual(gate_judges.judge_hash('inserts_wall'), own)
        sources = gate_judges._judge_sources(gate_judges.JUDGES['inserts_wall'])
        for helper in ('_stops', '_wall', '_rows', '_names', '_not_run', '_ray_short'):
            self.assertIn(inspect.getsource(getattr(gate_judges, helper)), sources, helper)
        # a thin judge is its helper: access_screw_path must change when the path logic changes
        self.assertIn(inspect.getsource(gate_judges._blocking), gate_judges._judge_sources(gate_judges.JUDGES['access_screw_path']))
        # and a helper a judge does not reach is not part of it
        self.assertNotIn(inspect.getsource(gate_judges._blocking), gate_judges._judge_sources(gate_judges.JUDGES['mesh_closed']))


class NeverSilent(unittest.TestCase):
    """What was not measured is NOT_RUN. A judge never raises on facts it cannot read and never answers "pass" to them."""

    def base(self, judge):
        name = first_fixture(judge, True)
        fx = FX[name]
        ctx = ctx_of(judge, fx)
        if judge == 'regression_unintended':
            ctx['intent'] = ['demo/base']
        return facts_of(name), params_of(fx), ctx

    def call(self, judge, facts, params, ctx):
        try:
            return gate_judges.JUDGES[judge](facts, params, ctx)
        except Exception as e:                       # the point of the test: a judge itself never raises
            self.fail('%s raised %s: %s' % (judge, type(e).__name__, e))

    def test_the_list_a_judge_reads_is_missing(self):
        for judge, key in sorted(READS.items()):
            facts, params, ctx = self.base(judge)
            del facts[key]
            out = self.call(judge, facts, params, ctx)
            self.assertIn('NOT_RUN', verdicts(out), '%s without "%s"' % (judge, key))

    def test_the_rows_cannot_be_read(self):
        for judge, key in sorted(READS.items()):
            facts, params, ctx = self.base(judge)
            facts[key] = ['x', None, 5]
            out = self.call(judge, facts, params, ctx)
            self.assertIn('NOT_RUN', verdicts(out), '%s with unreadable "%s"' % (judge, key))

    def test_a_row_with_nothing_in_it_is_not_a_pass(self):
        for judge, key in sorted(READS.items()):
            facts, params, ctx = self.base(judge)
            facts[key] = [{}]
            out = self.call(judge, facts, params, ctx)
            if judge in EMPTY_ROW_IS_ANOTHER_JUDGES:
                continue
            self.assertTrue(out, '%s passed an empty row of "%s"' % (judge, key))
        # the judges that stay silent on such a row: another judge of the same facts speaks
        facts = {'stl': 'x.stl', 'coverage': 1.0, 'report_under': 3.0, 'regions': [{}]}
        self.assertIn('NOT_RUN', verdicts(judged('walls_min_material', facts)))
        self.assertIn('NOT_RUN', verdicts(judged('walls_one_line', facts)))
        self.assertIn('NOT_RUN', verdicts(judged('access_hole_covered', {'openings': [{}], 'closed': []})))
        self.assertIn('NOT_RUN', verdicts(judged('project_mesh_unstamped', dict(facts_of('project_mesh_unstamped__pass_every_mesh_stamped'), objects=[{}]))))

    def test_empty_facts_are_never_a_pass(self):
        for judge in sorted(gate_judges.JUDGES):
            out = judged(judge, {}, intent=['demo/base'], prev={'holes': [], 'openings': []})
            self.assertTrue(out and set(verdicts(out)) == {'NOT_RUN'}, '%s on empty facts: %s' % (judge, verdicts(out)))
            self.assertFalse([f for f in out if 'failed:' in f['text']], '%s raised on empty facts' % judge)

    def test_odd_values_do_not_raise(self):
        # every key and every list item of the facts of two fixtures per judge, replaced by values of the wrong kind
        odd = [None, 'x', True, float('nan'), float('inf'), [], {}, -1, [[1]], {'a': {}}, 1e308, 10 ** 400]
        for judge in sorted(gate_judges.JUDGES):
            for failing in (True, False):
                name = first_fixture(judge, failing)
                facts, params, ctx = facts_of(name), params_of(FX[name]), ctx_of(judge, FX[name])
                for n in range(len(slots(facts))):
                    for value in odd:
                        f2 = copy.deepcopy(facts)
                        container, key = slots(f2)[n]
                        container[key] = copy.deepcopy(value)
                        out = self.call(judge, f2, params, ctx)
                        json.dumps(out, allow_nan=False)         # and what comes back is still plain JSON

    def test_the_probe_did_not_look_far_enough(self):
        # every range a probe writes into its facts is held against today's limit
        f = facts_of('walls_min_material__pass_no_wall_under_the_limit')
        self.assertEqual(verdicts(judged('walls_min_material', dict(f, report_under=1.0))), ['NOT_RUN'])
        self.assertEqual(verdicts(judged('walls_one_line', dict(f, report_under=0.5))), ['NOT_RUN'])
        self.assertEqual(verdicts(judged('walls_slit', dict(f, report_under=0.1))), ['NOT_RUN'])
        self.assertEqual(verdicts(judged('walls_min_material', dict(f, coverage=None))), ['NOT_RUN'])
        self.assertEqual(verdicts(judged('walls_thin_faces', {'faces': [], 'width_cap': 1.0})), ['NOT_RUN'])
        self.assertEqual(verdicts(judged('clearance_min_gap_moving', {'own_group': 'base', 'margin': 1.9, 'gaps': []})), ['NOT_RUN'])
        self.assertEqual(verdicts(judged('access_hole_closed', {'closed': [], 'cap_max': 1.0})), ['NOT_RUN'])
        o = facts_of('access_hole_covered__pass_only_its_own_screw_in_the_path')
        o['openings'][0]['paths']['own']['reach'] = 20.0
        self.assertEqual(verdicts(judged('access_hole_covered__pass_only_its_own_screw_in_the_path', o)), ['NOT_RUN'])
        i = facts_of('inserts_mouth_reachable__pass_open_mouth')
        i['openings'][0]['paths']['iron']['r'] = 3.0                # a path narrower than the iron
        self.assertEqual(verdicts(judged('inserts_mouth_reachable', i)), ['NOT_RUN'])
        i['openings'][0]['paths']['iron'].update(r=4.0, reach=12.0)
        self.assertEqual(verdicts(judged('inserts_mouth_reachable', i)), ['NOT_RUN'])

    def test_the_wall_judge_reports_the_thin_walls_it_found_beside_not_proven(self):
        f = facts_of('walls_min_material__fail_neck_0_86_under_a_hook')
        f['coverage'] = 0.5
        self.assertEqual(sorted(verdicts(judged('walls_min_material', f))), ['FAIL', 'NOT_RUN'])

    def test_a_missing_project_file_is_not_a_clean_project(self):
        f = dict(facts_of('project_object_on_no_plate__pass_every_object_on_a_plate'), exists=False, objects=[])
        for judge in ('project_object_on_no_plate', 'project_object_not_printable', 'project_mesh_unstamped', 'project_mesh_stale', 'project_target_differs'):
            self.assertEqual(verdicts(judged(judge, f, part='main:')), ['NOT_RUN'], judge)

    def test_a_project_without_a_declared_target_is_not_judged_as_matching(self):
        f = dict(facts_of('project_target_differs__pass_file_and_stamps_match_the_declared_target'), target_declared=None)
        out = judged('project_target_differs', f, part='main:')
        self.assertEqual(verdicts(out), ['NOT_RUN'])
        self.assertIn('declared target', out[0]['text'])


class Defaults(unittest.TestCase):
    """The registry as it ships, without the numbers the fixtures pin: does it still stop the incidents?"""

    # incidents the shipped default lets through; each needs a project value (or an owner decision on the default)
    NOT_CAUGHT_BY_THE_DEFAULT = {
        'walls_slit__fail_slit_0_3_under_one_line': 'walls.slit_min ships as 0.3 and a 0.3 mm slit is not narrower than 0.3',
        'walls_one_line__fail_tube_wall_0_93_on_a_0_6_nozzle': 'needs the declared target of the project (0.62 line); with the placeholder 0.42 line it is a walls.min_material failure instead',
    }

    def shipped(self, name):
        fx = FX[name]
        judge = fx['judge']
        return run_judge(rule_of(judge), facts_of(name), dict(DEFAULTS), ctx_of(judge, fx))

    def test_the_shipped_defaults_stop_the_incidents(self):
        for name in INCIDENTS:
            stopped = [f for f in self.shipped(name) if f['verdict'] in gate_common.BLOCKING]
            if name in self.NOT_CAUGHT_BY_THE_DEFAULT:
                self.assertEqual(stopped, [], '%s is caught by the defaults now: take it off the list' % name)
            else:
                self.assertTrue(stopped, 'the shipped defaults let %s through' % name)

    def test_the_tube_wall_is_stopped_by_the_other_wall_rule_at_the_placeholder_target(self):
        name = 'walls_one_line__fail_tube_wall_0_93_on_a_0_6_nozzle'
        out = run_judge(rule_of('walls_min_material'), facts_of(name), dict(DEFAULTS), ctx_of('walls_min_material'))
        self.assertEqual(verdicts(out), ['FAIL'])

    def test_every_judge_finds_its_parameters_in_the_shipped_registry(self):
        for name, fx in FX.items():
            out = self.shipped(name)
            self.assertFalse([f for f in out if 'failed:' in f['text']], name)


class TheLimitIsTheParameter(unittest.TestCase):
    """On the limit passes, just under it does not, and a project value moves the line."""

    def pocket(self, lead, insert=None, relief_wall=2.45, blocked=()):
        facts = facts_of('inserts_wall__pass_4_0_all_round')
        p = facts['pockets'][0]
        facts['pockets'] = [p]
        p['walls']['lead_in'].update(min=lead, blocked_by=list(blocked))
        p['walls']['insert'].update(min=lead if insert is None else insert, blocked_by=list(blocked))
        p['walls']['relief'].update(min=relief_wall, blocked_by=list(blocked))
        return facts

    def test_insert_wall(self):
        self.assertEqual(judged('inserts_wall', self.pocket(4.0)), [])
        for wall in (3.99, 3.9, 3.8):                 # 3.8 was the old pass value: it is a finding
            out = judged('inserts_wall', self.pocket(wall))
            self.assertEqual([(f['verdict'], f['measure'], f['limit']) for f in out], [('GROW', wall, 4.0)])
        self.assertEqual(judged('inserts_wall', self.pocket(3.2), {'inserts.wall_target': 3.0}), [])
        self.assertEqual(verdicts(judged('inserts_wall', self.pocket(4.0), {'inserts.wall_target': 5.0})), ['GROW'])

    def test_the_smaller_of_the_two_walls_is_the_measure(self):
        out = judged('inserts_wall', self.pocket(4.5, insert=3.1))
        self.assertEqual((out[0]['measure'], 'along the insert' in out[0]['text']), (3.1, True))
        out = judged('inserts_wall', self.pocket(2.9, insert=4.5))
        self.assertEqual((out[0]['measure'], 'at the lead-in' in out[0]['text']), (2.9, True))

    def test_relief_wall(self):
        self.assertEqual(judged('inserts_wall_at_relief', self.pocket(4.0, relief_wall=2.45)), [])
        out = judged('inserts_wall_at_relief', self.pocket(4.0, relief_wall=2.44))
        self.assertEqual([(f['verdict'], f['measure'], f['limit']) for f in out], [('FAIL', 2.44, 2.45)])
        p = {'inserts.relief_wall_min': 1.2}                                         # a project that keeps the smaller number
        self.assertEqual(judged('inserts_wall_at_relief', self.pocket(4.0, relief_wall=1.2), p), [])
        out = judged('inserts_wall_at_relief', self.pocket(4.0, relief_wall=1.19), p)
        self.assertEqual([(f['verdict'], f['measure'], f['limit']) for f in out], [('FAIL', 1.19, 1.2)])

    def test_floor_and_relief_depth(self):
        f = facts_of('inserts_floor__pass_2_1_under_the_floor')
        f['pockets'][0]['floor'] = 0.6
        self.assertEqual(judged('inserts_floor', f), [])
        f['pockets'][0]['floor'] = 0.59
        self.assertEqual(verdicts(judged('inserts_floor', f)), ['FAIL'])
        r = facts_of('inserts_relief_missing__fail_relief_0_2_too_shallow')
        r['pockets'][0]['relief']['depth'] = 0.4
        self.assertEqual(judged('inserts_relief_missing', r), [])

    def test_engagement_per_size(self):
        f = facts_of('screws_engagement__pass_3_4_mm')
        f['screws'] = f['screws'][:1]
        # lengths found along a line by sampling meet the limit on the 0.05 grid (_gridded): 3.19 is 3.2, 3.17 is 3.15
        for size, e, want in (('M3', 3.2, []), ('M3', 3.19, []), ('M3', 3.17, ['FAIL']), ('M4', 3.99, []), ('M4', 3.97, ['FAIL']), ('M4', 4.0, []), ('M2', 2.0, []),
                              ('M6', 5.9, ['FAIL']), ('M8', 3.2, []), ('M8', 3.1, ['FAIL']), (None, 3.1, ['FAIL'])):
            f['screws'][0].update(size=size, engagement=e)
            self.assertEqual(verdicts(judged('screws_engagement', f)), want, (size, e))
        f['screws'][0].update(size='M4', engagement=4.2)
        out = judged('screws_engagement', f, {'screws.min_engagement[M4]': 4.5})
        self.assertEqual([(x['verdict'], x['limit']) for x in out], [('FAIL', 4.5)])

    def test_under_head_and_tip_room(self):
        f = facts_of('screws_under_head__pass_5_2_under_the_head')
        f['screws'][0]['under_head'] = 5.0
        self.assertEqual(judged('screws_under_head', f), [])
        f['screws'][0]['under_head'] = 4.99           # the probe finds the end to 0.02: on the 0.05 grid this is 5.0
        self.assertEqual(judged('screws_under_head', f), [])
        for short in (4.97, 4.9):                        # 4.95 and 4.9 on the grid: short, and the finding keeps the measured value
            f['screws'][0]['under_head'] = short
            self.assertEqual([(x['verdict'], x['measure']) for x in judged('screws_under_head', f)], [('FAIL', short)])
        self.assertEqual(judged('screws_under_head', f, {'screws.min_under_head': 3.0}), [])
        t = facts_of('screws_tip_bottoming__pass_tip_room_1_2')
        t['screws'][0]['tip_room'] = 0.5
        self.assertEqual(judged('screws_tip_bottoming', t), [])
        t['screws'][0]['tip_room'] = 0.49
        self.assertEqual(judged('screws_tip_bottoming', t), [])
        t['screws'][0]['tip_room'] = 0.47
        self.assertEqual(verdicts(judged('screws_tip_bottoming', t)), ['FAIL'])
        t['screws'][0]['tip_room'] = None               # a hole with no end in front of the tip
        self.assertEqual(judged('screws_tip_bottoming', t), [])

    def test_clamp_room(self):
        f = facts_of('bearings_clamp_room__pass_room_1_0')
        for room, want in ((9.0, []), (9.01, ['FAIL']), (8.0, []), (10.0, ['FAIL'])):
            f['bearings'][0]['clamps'][0]['room'] = room
            self.assertEqual(verdicts(judged('bearings_clamp_room', f, part=TUBE)), want, room)
        f['bearings'][0]['clamps'][0]['room'] = 9.7
        self.assertEqual(judged('bearings_clamp_room', f, {'bearings.clamp_room': 0.3}, part=TUBE), [])

    def test_gap_to_a_moving_neighbour(self):
        f = facts_of('clearance_min_gap_moving__pass_2_4_to_the_pulley')
        for gap, want in ((2.0, []), (1.99, ['FAIL']), (0.0, ['FAIL'])):
            f['gaps'][0]['gap'] = gap
            self.assertEqual(verdicts(judged('clearance_min_gap_moving', f)), want, gap)

    def test_a_slit_as_wide_as_the_limit_passes(self):
        # the shipped default is 0.3: a 0.3 slit passes it. A project that wants one line of its nozzle sets the parameter.
        f = facts_of('walls_slit__fail_slit_0_2')
        f['regions'][0]['min'] = 0.3
        self.assertEqual(judged('walls_slit', f), [])
        f['regions'][0]['min'] = 0.29
        self.assertEqual(verdicts(judged('walls_slit', f)), ['FAIL'])

    def test_every_wall_under_the_limit_fails_in_exactly_one_of_the_two_wall_judges(self):
        base = facts_of('walls_min_material__fail_neck_0_86_under_a_hook')
        for line in (0.42, 0.62, 0.8):
            limit = max(1.5, 2 * line)
            params = {'target.line_width': line, 'walls.min_wall_effective': limit}
            for i in range(0, 420):
                t = 0.0025 + i * 0.005
                base['regions'][0]['min'] = t
                a = [f for f in judged('walls_min_material', copy.deepcopy(base), params) if f['verdict'] == 'FAIL']
                b = [f for f in judged('walls_one_line', copy.deepcopy(base), params) if f['verdict'] == 'FAIL']
                self.assertEqual(len(a) + len(b), 1 if t < limit else 0, (line, t))
                self.assertEqual(len(b), 1 if line <= t < 2 * line else 0, (line, t))

    def test_the_neck_is_caught_on_the_wide_nozzle_too(self):
        # with a 0.62 line the 0.86 neck lies between one and two lines: walls_one_line fails it, walls_min_material leaves it
        p = {'target.line_width': 0.62, 'walls.min_wall_effective': 1.5}
        self.assertEqual(judged('walls_min_material__fail_neck_0_86_under_a_hook', params=p), [])
        out = judged('walls_one_line', facts_of('walls_min_material__fail_neck_0_86_under_a_hook'), p)
        self.assertEqual([(f['verdict'], f['measure'], f['limit']) for f in out], [('FAIL', 0.86, 1.24)])

    def test_thin_face_width_and_washer_numbers(self):
        f = facts_of('walls_thin_faces__fail_ledge_1_0_and_crescents_0_2')
        f['faces'] = f['faces'][:1]
        f['faces'][0]['width'] = 1.2
        self.assertEqual(judged('walls_thin_faces', f), [])
        w = facts_of('washers_landing__pass_full_ring_in_a_13_2_spot_face')
        w['washers'][0]['landing'].update(share=0.95, gap=0.05)
        self.assertEqual(judged('washers_landing', w), [])
        w['washers'][0]['landing'].update(share=0.94)
        self.assertEqual([f['unit'] for f in judged('washers_landing', w)], ['share'])
        w['washers'][0]['landing'].update(share=1.0, spot_face_d=13.19)
        self.assertEqual([(f['measure'], f['limit']) for f in judged('washers_landing', w)], [(13.19, 13.2)])
        p = facts_of('washers_proud__pass_0_6_to_the_motor_body')
        p['washers'][0]['proud']['nearest']['gap'] = 0.5
        self.assertEqual(judged('washers_proud', p), [])
        p['washers'][0]['proud']['nearest']['gap'] = 0.49
        self.assertEqual(verdicts(judged('washers_proud', p)), ['FAIL'])

    def test_bearing_seat_tolerance(self):
        f = facts_of('bearings_seat__pass_seat_of_the_table_size')
        seat = f['bearings'][0]['seat']['outer']
        for d, entry, want in ((78.3, 78.3, []), (78.31, 78.31, ['FAIL']), (77.69, 78.0, ['FAIL']), (78.0, 77.7, []), (78.0, 77.69, ['FAIL'])):
            seat.update(d=d, entry_d=entry)
            self.assertEqual(verdicts(judged('bearings_seat', f, part='demo/gear_lid')), want, (d, entry))

    def test_mesh_counts(self):
        m = facts_of('mesh_closed__pass_closed')
        self.assertEqual(verdicts(judged('mesh_closed', dict(m, non_manifold_edges=2))), ['FAIL'])
        self.assertEqual(verdicts(judged('mesh_one_shell', dict(m, shells=0))), ['FAIL'])


class Verdicts(unittest.TestCase):
    """The rules with more than one verdict."""

    BEARING = {'name': 'ref bearing 6005', 'kind': 'bought', 'part': None, 'gap': 0.2}

    def pocket(self, lead, insert, lead_by=(), insert_by=(), ray_max=8.0):
        facts = facts_of('inserts_wall__pass_4_0_all_round')
        p = facts['pockets'][0]
        facts['pockets'] = [p]
        p['walls']['lead_in'].update(min=lead, blocked_by=list(lead_by))
        p['walls']['insert'].update(min=insert, blocked_by=list(insert_by))
        p['ray']['max'] = ray_max
        return facts

    def wall(self, *a, **kw):
        out = judged('inserts_wall', self.pocket(*a, **kw))
        self.assertEqual(len(out), 1)
        return out[0]['verdict'], out[0]['by']

    def test_insert_wall_fail_grow_blocked(self):
        b = [self.BEARING]
        self.assertEqual(self.wall(2.5, 2.5), ('GROW', []))
        self.assertEqual(self.wall(2.6, 2.6, b, b), ('BLOCKED', ['ref bearing 6005']))
        self.assertEqual(self.wall(2.6, 2.6, b, ()), ('GROW', []))                # one thin wall is free: it can grow
        self.assertEqual(self.wall(2.6, 4.2, b, ()), ('BLOCKED', ['ref bearing 6005']))    # the free wall is not thin
        self.assertEqual(self.wall(1.59, 1.59, b, b), ('FAIL', ['ref bearing 6005']))      # under the floor: nothing excuses it
        self.assertEqual(self.wall(1.6, 1.6, b, b), ('BLOCKED', ['ref bearing 6005']))
        self.assertEqual(self.wall(1.59, 4.0), ('FAIL', []))

    def test_a_neighbour_farther_away_than_the_wall_must_grow_stops_nothing(self):
        far = [dict(self.BEARING, gap=1.5)]
        self.assertEqual(self.wall(2.5, 2.5, far, far), ('GROW', []))
        near = [dict(self.BEARING, gap=1.49)]
        self.assertEqual(self.wall(2.5, 2.5, near, near), ('BLOCKED', ['ref bearing 6005']))
        unknown = [{'name': 'Cover', 'kind': 'printed', 'part': 'demo/cover'}]      # no gap given: it counts
        self.assertEqual(self.wall(2.5, 2.5, unknown, unknown), ('BLOCKED', ['Cover']))
        own = [{'name': None, 'kind': 'own_cavity', 'part': None, 'gap': 0.0}]
        self.assertEqual(self.wall(2.5, 2.5, own, own), ('BLOCKED', ['own cavity']))

    def test_the_fix_order_of_a_thin_wall_starts_with_a_wider_wall(self):
        # the owner's order: (1) a wider wall where the outline is free, (2) move the pocket, (3) a local boss, (4) blocked
        for name in ('inserts_wall__grow_2_5_free_all_round', 'inserts_wall__blocked_by_a_bearing_seat'):
            fix = judged(name)[0]['fix']
            step = lambda word: next(i for i, line in enumerate(fix) if word in line)
            self.assertEqual(step('wider'), 0, fix)
            self.assertTrue(0 < step('ove the pocket') < step('boss') < step('locked'), fix)
        self.assertIn('free space', judged('inserts_wall__grow_2_5_free_all_round')[0]['text'])

    def test_rays_that_end_under_the_target_prove_nothing(self):
        out = judged('inserts_wall', self.pocket(3.0, 3.0, ray_max=3.0))
        self.assertEqual(verdicts(out), ['NOT_RUN'])
        out = judged('inserts_wall', self.pocket(2.5, 3.0, ray_max=3.0))            # a wall that ended before the cap is real
        self.assertEqual(verdicts(out), ['GROW'])

    def test_relief_wall_fail_or_blocked(self):
        f = facts_of('inserts_wall_at_relief__fail_0_95_beside_the_relief')
        f['pockets'] = f['pockets'][:1]
        p, limit = f['pockets'][0], {'inserts.relief_wall_min': 1.2}
        out = judged('inserts_wall_at_relief', f, limit)
        self.assertEqual(verdicts(out), ['FAIL'])
        self.assertIn('D6.5 relief leaves 1.45', out[0]['text'])                    # the next smaller relief of the table fits
        p['relief_room'] = [{'d': 7.5, 'wall': 0.95}, {'d': 6.5, 'wall': 1.0}, {'d': 5.9, 'wall': 1.1}]
        out = judged('inserts_wall_at_relief', f, limit)
        self.assertEqual(verdicts(out), ['FAIL'])                                   # nothing fits, nothing stops the wall: widen it
        self.assertIn('free space', out[0]['text'])
        p['walls']['relief']['blocked_by'] = [self.BEARING]
        out = judged('inserts_wall_at_relief', f, limit)
        self.assertEqual([(x['verdict'], x['by']) for x in out], [('BLOCKED', ['ref bearing 6005'])])
        p['relief_room'][2]['wall'] = 1.3                                           # a smaller relief fits again: not blocked
        self.assertEqual(verdicts(judged('inserts_wall_at_relief', f, limit)), ['FAIL'])
        p['relief_room'][0]['wall'] = 9.9                                           # the relief it already has is not "a smaller one"
        p['relief_room'][2]['wall'] = 1.1
        self.assertEqual(verdicts(judged('inserts_wall_at_relief', f, limit)), ['BLOCKED'])

    def test_relief_missing_fail_or_blocked(self):
        f = facts_of('inserts_relief_missing__blocked_no_relief_of_the_table_fits')
        out = judged('inserts_relief_missing', f)
        self.assertEqual(verdicts(out), ['BLOCKED'])
        self.assertIn('shave the melt ring', out[0]['text'])
        f['pockets'][0]['relief_room'][2]['wall'] = 2.45
        out = judged('inserts_relief_missing', f)
        self.assertEqual(verdicts(out), ['FAIL'])
        self.assertIn('D5.9 relief fits', out[0]['text'])
        f['pockets'][0]['relief_room'][2]['wall'] = 1.1                              # a looser project value turns BLOCKED into FAIL: cut it
        self.assertEqual(verdicts(judged('inserts_relief_missing', f)), ['BLOCKED'])
        self.assertEqual(verdicts(judged('inserts_relief_missing', f, {'inserts.relief_wall_min': 1.0})), ['FAIL'])
        del f['pockets'][0]['relief_room']
        self.assertEqual(verdicts(judged('inserts_relief_missing', f)), ['NOT_RUN'])

    def test_a_blind_pocket_can_fail_both_halves_of_the_floor_rule(self):
        f = facts_of('inserts_floor__fail_insert_longer_than_the_plate')
        f['pockets'][0]['floor'] = 0.3
        out = judged('inserts_floor', f)
        self.assertEqual(sorted((x['measure'], x['limit']) for x in out), [(0.3, 0.6), (3.0, 4.0)])
        self.assertEqual(len({gate_features.finding_id(x) for x in out}), 2)


class PocketShape(unittest.TestCase):
    """The class of a hole is recomputed with today's tables; the stored class is the fallback."""

    EXACT = {'table': 'inserts', 'kind': 'M3x4x5', 'role': 'insert_pocket', 'match': 'exact'}
    BAND = {'table': 'inserts', 'kind': 'M3x4x5', 'role': 'insert_pocket', 'match': 'band'}

    def classifier(self, fn):
        from probes import _geom
        missing = object()
        old = getattr(_geom, 'classify_stack', missing)
        _geom.classify_stack = fn

        def undo():
            if old is missing:
                del _geom.classify_stack
            else:
                _geom.classify_stack = old
        self.addCleanup(undo)

    def not_built(self):
        def stub(segments, through, tables, coaxial=None):
            raise NotImplementedError
        self.classifier(stub)

    def test_the_stored_class_is_used_while_the_classifier_is_not_built(self):
        self.not_built()
        self.assertEqual(verdicts(judged('inserts_pocket_shape__fail_straight_d4_4')), ['FAIL', 'FAIL'])
        self.assertEqual(judged('inserts_pocket_shape__pass_table_profile'), [])

    def test_todays_tables_overrule_the_stored_class(self):
        seen = []

        def all_band(segments, through, tables, coaxial=None):
            seen.append((segments, through, sorted(tables), coaxial))
            return dict(self.BAND)
        self.classifier(all_band)
        f = facts_of('inserts_pocket_shape__pass_table_profile')
        f['holes'][0]['coaxial'] = [{'name': 'ref insert M3x4x5', 'kind': 'hardware', 'part': None}]
        out = judged('inserts_pocket_shape', f)
        self.assertEqual(verdicts(out), ['FAIL', 'FAIL', 'FAIL'])                    # stored "exact" does not protect a hole
        self.assertEqual(seen[0], ([{'d': 7.5, 'len': 0.54}, {'d': 4.4, 'len': 1.5}, {'d': 3.6, 'len': 5.0}], False,
                                   ['bearings', 'inserts', 'pockets', 'screws', 'washers'], ['ref insert M3x4x5']))
        self.classifier(lambda segments, through, tables: dict(self.EXACT))          # a classifier without coaxial names works too
        self.assertEqual(judged('inserts_pocket_shape__fail_straight_d4_4'), [])     # and stored "band" does not condemn one
        self.classifier(lambda segments, through, tables: None)
        self.assertEqual(judged('inserts_pocket_shape__fail_straight_d4_4'), [])
        # two blind holes of no table: one question each, and it is the one of the fits rule ("what goes in"), not both
        self.assertEqual(len(judged('census_unknown_hole', facts_of('inserts_pocket_shape__fail_straight_d4_4'))), 0)
        asked = judged('fits_bought_pocket', facts_of('inserts_pocket_shape__fail_straight_d4_4'))
        self.assertEqual([f['ask'] for f in asked], ['fits.pocket_content', 'fits.pocket_content'])

    def shape(self, segments):
        self.not_built()
        f = facts_of('inserts_pocket_shape__fail_straight_d4_4')
        f['holes'] = f['holes'][:1]
        f['holes'][0]['segments'] = segments
        out = judged('inserts_pocket_shape', f)
        self.assertEqual(verdicts(out), ['FAIL'])
        return out[0]['measure'], out[0]['limit'], out[0]['text']

    def test_the_finding_names_the_size_that_is_off(self):
        self.assertEqual(self.shape([{'d': 4.4, 'len': 6.5}])[:2], (4.4, 3.6))                               # straight D4.4: no D3.6 hole
        self.assertEqual(self.shape([{'d': 5.1, 'len': 1.5}, {'d': 4.6, 'len': 5.0}])[:2], (5.1, 4.4))       # lead-in too wide
        self.assertEqual(self.shape([{'d': 5.2, 'len': 1.0}, {'d': 4.6, 'len': 5.0}])[:2], (5.2, 4.4))
        self.assertEqual(self.shape([{'d': 4.4, 'len': 1.5}, {'d': 4.0, 'len': 5.0}])[:2], (4.0, 3.6))       # hole too wide
        self.assertEqual(self.shape([{'d': 3.6, 'len': 6.5}])[:2], (3.6, 4.4))                               # no lead-in at all
        self.assertEqual(self.shape([{'d': 7.5, 'len': 0.54}, {'d': 4.4, 'len': 6.5}])[:2], (4.4, 3.6))      # a relief does not hide it
        self.assertEqual(self.shape([{'d': 4.4, 'len': 0.75}, {'d': 3.6, 'len': 5.75}])[:2], (0.75, 1.5))    # lead-in too shallow
        self.assertEqual(self.shape([{'d': 4.4, 'len': 1.5}, {'d': 3.6, 'len': 3.0}])[:2], (3.0, 5.0))       # hole too shallow
        self.assertEqual(self.shape([{'d': 7.5, 'len': 0.2}, {'d': 4.4, 'len': 1.5}, {'d': 3.6, 'len': 5.0}])[:2], (0.2, 0.54))   # relief too shallow
        measure, limit, text = self.shape([{'d': 4.4, 'len': 1.5}, {'d': 3.6, 'len': 5.0}])                  # called "band" with nothing off
        self.assertEqual((measure, limit), (None, None))
        self.assertIn('M3x4x5', text)

    def test_a_tip_hole_below_the_insert_is_part_of_the_depth(self):
        measure, limit, _ = self.shape([{'d': 4.4, 'len': 1.5}, {'d': 3.6, 'len': 3.0}, {'d': 3.2, 'len': 3.0}])
        self.assertEqual((measure, limit), (None, None))

    def test_the_shipped_classifier_and_the_fixtures_agree(self):
        # no stub here: when probes._geom.classify_stack is built, it must give the pocket fixtures the class they store
        from probes import _geom
        if not hasattr(_geom, 'classify_stack'):
            self.skipTest('probes._geom.classify_stack is not there')
        for name, fx in FX.items():
            if fx['judge'] not in ('inserts_pocket_shape', 'census_unknown_hole'):
                continue
            for h in fx['facts']['holes']:
                try:
                    got = _geom.classify_stack(h['segments'], h['through'], REG['tables'])
                except NotImplementedError:
                    self.skipTest('probes._geom.classify_stack is not built yet')
                self.assertEqual((got or {}).get('match'), (h['class'] or {}).get('match'), '%s %s' % (name, h['id']))
                self.assertEqual((got or {}).get('table'), (h['class'] or {}).get('table'), '%s %s' % (name, h['id']))

    def test_straight_table_rows_are_one_segment(self):
        row = {'relief': {'d': 9.0, 'depth': 0.54, 'fallback_d': []}, 'lead_in': {'d': 6.4, 'depth': 0.0}, 'hole': {'d': 6.4, 'depth': 11.0}}
        off = lambda segs: gate_judges._pocket_off(segs, row, {'d': 0.1, 'depth': 0.3})
        self.assertIsNone(off([{'d': 9.0, 'len': 0.54}, {'d': 6.4, 'len': 11.0}]))
        self.assertIsNone(off([{'d': 6.4, 'len': 11.0}]))
        self.assertEqual(off([{'d': 9.0, 'len': 0.54}, {'d': 7.0, 'len': 11.0}])[1:], (7.0, 6.4))
        self.assertEqual(off([{'d': 6.4, 'len': 8.0}])[1:], (8.0, 11.0))

    def test_unknown_holes_are_asked_once_per_shape(self):
        self.not_built()
        f = facts_of('census_unknown_hole__ask_d33_5_hole_fits_no_table')
        a = judged('census_unknown_hole', f)[0]
        self.assertEqual((a['verdict'], a['ask'], a['anchor']['kind']), ('INFO', 'census.unknown_hole', 'hole'))
        self.assertEqual(a['context'], judged('census_unknown_hole', copy.deepcopy(f))[0]['context'])
        f['holes'][0]['coaxial'] = [{'name': 'Cover', 'kind': 'printed', 'part': 'demo/cover'}]
        b = judged('census_unknown_hole', f)[0]
        self.assertNotEqual(a['context'], b['context'])                              # a coaxial body appeared: ask again
        self.assertIn('Cover', b['text'])
        f['holes'][0]['class'] = {'table': None, 'kind': None, 'role': 'unknown', 'match': None}
        self.assertEqual(len(judged('census_unknown_hole', f)), 1)
        # a bought part on the axis: the fits rule asks what goes in, so this rule is silent. One hole, one question
        f['holes'][0]['coaxial'] = [{'name': 'ref motor 42', 'kind': 'bought', 'part': None}]
        self.assertEqual(judged('census_unknown_hole', f), [])
        c = judged('fits_bought_pocket', f)
        self.assertEqual([(x['ask'], x['by']) for x in c], [('fits.pocket_content', ['ref motor 42'])])


class Access(unittest.TestCase):
    FOUR = 'access_hole_covered__access_ask_one_question_for_a_lid_over_four_screw_holes'     # four blind screw holes and their census
    BRIDGE = 'access_hole_closed__access_ask_bridge_layer_under_a_counterbore'

    def hits(self, *hits, **kw):
        """One blind screw hole of the census with these hits in its own path."""
        f = facts_of(self.FOUR)
        f['openings'] = f['openings'][:1]
        f['openings'][0]['paths']['own']['hits'] = [dict({'part': None, 'approx': False, 'expected': False}, **h) for h in hits]
        return judged(self.FOUR, f, kw.get('params'), census=self.census(1))

    def census(self, n):
        """The census of the fixture with its first n holes: a screw hole of the census with no opening row is NOT_RUN."""
        census = copy.deepcopy(FX[self.FOUR]['ctx']['census'])
        return dict(census, holes=census['holes'][:n])

    def test_what_counts_as_an_obstacle(self):
        cover = {'name': 'Cover', 'kind': 'printed', 'at': 10.0, 'share': 0.5}
        self.assertEqual(verdicts(self.hits(cover)), ['FAIL'])
        self.assertEqual(self.hits(dict(cover, expected=True)), [])                  # the fastener or the mating hole
        self.assertEqual(self.hits(dict(cover, at=60.1)), [])                        # beyond the reach
        self.assertEqual(verdicts(self.hits(dict(cover, at=60.0))), ['FAIL'])
        self.assertEqual(self.hits(dict(cover, share=0.019)), [])                    # under the least share: probe noise
        self.assertEqual(verdicts(self.hits(dict(cover, share=0.02))), ['FAIL'])
        self.assertEqual(self.hits(dict(cover, at=40.0), params={'access.reach': 30.0}), [])
        self.assertEqual(verdicts(self.hits(dict(cover, share=None))), ['FAIL'])     # no share given: it counts

    def test_one_asked_row_for_all_the_covers_of_a_part(self):
        out = self.hits({'name': 'Cover', 'kind': 'printed', 'at': 22.0, 'share': 0.5}, {'name': 'Lid', 'kind': 'printed', 'at': 7.5, 'share': 0.1},
                        {'name': 'ref screw', 'kind': 'hardware', 'at': 0.0, 'share': 1.0, 'expected': True})
        self.assertEqual([(f['by'], f['measure'], f['limit'], f['ask']) for f in out], [(['Lid', 'Cover'], 7.5, 60.0, 'assembly.order')])      # nearest first
        self.assertIn('2 other bodies stand in front of 1 openings of this part, the nearest 7.5 mm out. Nearest first: Lid 7.5 mm out (', out[0]['text'])
        four = judged(self.FOUR)                                                     # one lid over four holes: one question, about the part
        self.assertEqual([(f['by'], f['anchor']['kind']) for f in four], [(['Lid'], 'part')])
        for hid in ('h1', 'h2', 'h3', 'h4'):
            self.assertIn('screw hole %s ' % hid, four[0]['text'])
        f = facts_of(self.FOUR)
        for o in f['openings']:
            o['paths']['own']['hits'][0]['at'] += 0.4                                # the lid 0.4 farther out over the same four holes: the same question, an answer holds
        moved = judged(self.FOUR, f)
        self.assertEqual((moved[0]['context'], gate_features.finding_id(moved[0]), moved[0]['measure']), (four[0]['context'], gate_features.finding_id(four[0]), four[0]['measure'] + 0.4))
        f['openings'] = f['openings'][:3]                                            # the same lid over three of the holes: another set of places, asked again
        three = judged(self.FOUR, f, census=self.census(3))
        self.assertEqual(len(three), 1); self.assertNotEqual(three[0]['context'], four[0]['context'])
        f['openings'][0]['paths']['own']['hits'] += [{'name': 'ref motor 42 / stator', 'kind': 'bought', 'at': 30.0, 'share': 1.0, 'expected': False},
                                                     {'name': 'ref motor 42 / rear cap', 'kind': 'bought', 'at': 50.0, 'share': 1.0, 'expected': False}]
        more = judged(self.FOUR, f, census=self.census(3))
        self.assertEqual([x['by'] for x in more], [['Lid', 'ref motor 42']])         # still one row; the bodies of one component are one cover
        self.assertNotEqual(more[0]['context'], three[0]['context'])                 # a cover nobody answered for: the question is asked again, with both
        left = judged(self.FOUR, f)                                                  # the census holds a fourth screw hole and the facts no row for it
        self.assertEqual([(x['verdict'], x['anchor']['kind']) for x in left if not x['by']], [('NOT_RUN', 'hole')])
        self.assertIn('no opening row at an outer end', left[0]['text'])             # never "free": what stands in front of it was not measured

    def test_an_answer_covers_only_the_places_it_named(self):
        """Covers are named by component. A component that bundles many bodies (the screws of an arm) was answered while one
        of its screws stood far out in front of one head; another screw of it right over another head is a new question."""
        import gate_attest
        _hits = lambda o: o['paths']['tool']['hits']
        _num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
        name = 'access_screw_path__live2_ask_one_question_for_eleven_covers_of_one_part'
        row = lambda out: next(f for f in out if f['ask'])
        base = row(judged(name))
        answer = {'rule': base['ask'], 'part': base['part'], 'anchor': base['anchor'], 'context': base['context'], 'value': 'later', 'at': '2031-01-01T00:00:00Z'}
        self.assertTrue(gate_attest.closes(base, [answer]))
        f = facts_of(name)
        heads = [o for o in f['openings'] if _hits(o)]
        far = max(((h, o) for o in heads for h in _hits(o) if not h.get('expected') and h.get('kind') != 'self'), key=lambda t: t[0]['at'])[0]
        other = next(o for o in heads if far['name'] not in [h['name'] for h in _hits(o)])
        _hits(other).append(dict(far, at=0.5))                                       # the same component, now 0.5 mm in front of a head it did not cover
        new = row(judged(name, f))
        self.assertEqual(sorted(new['by']), sorted(base['by']))                      # no new name ...
        self.assertFalse(gate_attest.closes(new, [answer]))                          # ... and the old answer does not close it
        g = facts_of(name)
        for o in g['openings']:
            for h in _hits(o):
                if _num(h.get('at')): h['at'] = round(h['at'] + 1.0, 2)              # everything 1 mm farther out, the same places: the answer holds
        self.assertTrue(gate_attest.closes(row(judged(name, g)), [answer]))
        gone = facts_of(name)
        first = next(o for o in gone['openings'] if len([h for h in _hits(o) if not h.get('expected') and h.get('kind') != 'self']) > 0)
        _hits(first)[:] = [h for h in _hits(first) if h.get('expected') or h.get('kind') == 'self']                     # a cover left one head: asked again
        self.assertFalse(gate_attest.closes(row(judged(name, gone)), [answer]))

    def test_the_text_of_the_asked_row_is_capped_and_the_question_names_every_cover(self):
        covers = [{'name': 'Cover %02d' % i, 'kind': 'printed', 'part': 'demo/cover_%02d' % i, 'at': 5.0 + i, 'share': 0.5} for i in range(15)]
        out = self.hits(*covers)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['by'], ['Cover %02d' % i for i in range(15)])        # every cover, nearest first: the question lists them all
        self.assertIn('15 other bodies stand in front of', out[0]['text']); self.assertIn('Cover 05 10 mm out (screw hole h1 at', out[0]['text'])       # six with what they cover
        self.assertIn('; farther out: Cover 06 11 mm, Cover 07 12 mm,', out[0]['text']); self.assertIn('Cover 11 16 mm; and 3 more (the question names them all)', out[0]['text'])
        self.assertNotIn('Cover 12', out[0]['text'])
        again = self.hits(*reversed(covers))                                         # the order of the facts is not the order of the row
        self.assertEqual((again[0]['text'], again[0]['context']), (out[0]['text'], out[0]['context']))

    def test_a_body_inside_an_assembly_is_named_with_its_place(self):
        rotor = {'name': 'Rotor', 'kind': 'bought', 'part': None, 'at': 40.0, 'share': 1.0, 'in': 'Gearbox unit / Motor'}
        out = self.hits(rotor)
        self.assertEqual(out[0]['by'], ['Gearbox unit / Motor / Rotor']); self.assertTrue(out[0]['text'].startswith('Gearbox unit / Motor / Rotor stands in front of'))
        self.assertEqual(self.hits(dict(rotor, **{'in': ''}))[0]['by'], ['Rotor'])   # a body of a top-level component, and facts of an older unit
        printed = dict(rotor, name='Housing', kind='printed', part='demo/housing')   # a printed part keeps the name its id is made from
        self.assertEqual(self.hits(printed)[0]['by'], ['Housing'])
        mesh = self.hits(dict(rotor, kind='mesh', approx=True))                      # a mesh row is as it was: one per mesh body, under its own name
        self.assertEqual([(x['verdict'], x['by']) for x in mesh], [('NOT_RUN', ['Rotor'])])
        two = self.hits(dict(rotor, at=20.0), dict(rotor, name='Rotor', **{'in': 'Second gearbox / Motor'}))
        self.assertEqual(two[0]['by'], ['Gearbox unit / Motor / Rotor', 'Second gearbox / Motor / Rotor'])   # two bodies of one name are two covers

    def test_what_a_hit_proves(self):
        def rows(*hits, **kw):
            return [(f['verdict'], f['ask'], f['anchor']['kind']) for f in self.hits(*[dict({'name': 'Body %d' % i, 'at': 5.0, 'share': 0.5}, **h) for i, h in enumerate(hits)], **kw)]
        self.assertEqual(rows({'kind': 'self'}), [('FAIL', None, 'hole')])           # the part itself: a finding of that hole
        for kind in ('printed', 'bought', 'hardware', 'hidden'):                     # any other solid body: it depends on the assembly order
            self.assertEqual(rows({'kind': kind}), [('FAIL', 'assembly.order', 'part')], kind)
        self.assertEqual(rows({'kind': 'printed'}, {'kind': 'self'}), [('FAIL', None, 'hole')])
        for approx in ({'kind': 'mesh', 'approx': True}, {'kind': 'mesh', 'approx': True, 'by_box': True}, {'kind': 'bought', 'approx': True, 'by_points': 'RuntimeError'}):
            self.assertEqual(rows(approx), [('NOT_RUN', None, 'part')], approx)      # an approximation is never a FAIL
        self.assertEqual(rows({'kind': 'mesh', 'approx': True}, params={'access.overlay_pattern': '^Body 0$'}), [('INFO', None, 'part')])
        self.assertEqual(rows({'kind': 'self'}, params={'access.overlay_pattern': '^Body 0$'}), [('FAIL', None, 'hole')])    # the part is never an overlay
        self.assertEqual(verdicts(self.hits({'name': 'Cover', 'kind': 'printed', 'at': 5.0, 'share': 0.5}, params={'access.overlay_pattern': '('})), ['NOT_RUN', 'FAIL'])

    def test_an_approximate_hit_says_the_one_thing_to_do(self):
        out = judged('access_hole_covered__access_not_run_window_behind_a_mesh_body')
        self.assertIn('solid body', out[0]['text']); self.assertIn('access.overlay_pattern', out[0]['text'])
        self.assertNotIn('mesh', judged('access_hole_covered__fail_hole_closed_by_a_bought_part')[0]['text'])

    def test_a_hole_is_judged_for_what_it_is(self):
        f = facts_of(self.FOUR)
        self.assertEqual(verdicts(judged('access_hole_covered', f)), ['NOT_RUN'])    # no census: what the holes are for is not known
        ctx = copy.deepcopy(FX[self.FOUR]['ctx'])
        for h in ctx['census']['holes']:
            h['segments'] = [{'d': 9.0, 'len': 8.0}]                                 # holes of no table: census.unknown_hole asks, this rule does not
        self.assertEqual(judged(self.FOUR, f, census=ctx['census']), [])
        old = 'access_hole_covered__access_not_run_screw_hole_with_a_body_on_its_axis_and_facts_without_screws'
        g = facts_of(old)
        g.update(screws=[], not_screws=[], screws_cut=True)                          # the look at the shapes was cut short: as good as no look
        self.assertEqual(verdicts(judged(old, g)), ['NOT_RUN'])
        g.update(screws_cut=False)                                                   # looked, and no screw lies in it: the hole is judged as a hole
        self.assertEqual([(f['verdict'], f['by'], f['ask']) for f in judged(old, g)], [('FAIL', ['Lid'], 'assembly.order')])
        g['openings'].append({'feature': 's1', 'of': 'screw_head', 'end': 'mouth', 'anchor': {'kind': 'screw', 'axis': [0, 0, 1], 'point': [30.0, 10.0, 10.0]}, 'paths': {}})
        self.assertEqual(judged(old, g), [])                                         # a screw head sits on the bore: access.screw_path owns it

    def test_each_rule_reads_its_own_openings_and_path(self):
        f = facts_of('access_screw_path__fail_ledge_over_two_screw_heads')
        self.assertEqual(judged('access_hole_covered', f), [])                       # heads are not holes
        self.assertEqual(judged('inserts_mouth_reachable', f), [])
        self.assertEqual({x['anchor']['kind'] for x in judged('access_screw_path', f)}, {'screw'})
        f['openings'][0]['of'] = 'hatch'                                             # a kind no rule knows: one rule still judges it
        f['openings'][0]['paths']['own']['hits'] = [{'name': 'Cover', 'kind': 'bought', 'at': 1.0, 'share': 1.0, 'expected': False}]
        self.assertEqual(verdicts(judged('access_hole_covered', f)), ['FAIL'])
        g = facts_of('inserts_mouth_reachable__fail_mouth_under_a_collar_wall')
        self.assertEqual(judged('access_hole_covered', g), [])
        self.assertEqual(judged('access_screw_path', g), [])
        del g['openings'][0]['paths']['iron']
        self.assertEqual(sorted(verdicts(judged('inserts_mouth_reachable', g))), ['FAIL', 'NOT_RUN'])

    def test_a_ledge_over_a_head_and_the_wall_of_its_recess(self):
        name = 'access_screw_path__access_pass_head_in_a_counterbore_narrower_than_the_tool_path'
        f = facts_of(name)
        wall = f['openings'][0]['paths']['tool']['hits'][0]
        wall['at'] = 0.21                                                            # it begins above the head: a ledge
        self.assertEqual([(x['verdict'], x['measure'], x['ask']) for x in judged(name, f)], [('FAIL', 0.21, None)])
        wall['at'] = 0.05
        f['openings'][0]['paths']['own']['hits'] = [dict(wall, share=0.03)]         # the same wall reaches into the section of the head
        self.assertEqual(verdicts(judged(name, f)), ['FAIL'])

    def test_the_iron_is_stopped_by_the_part_itself_only(self):
        name = 'inserts_mouth_reachable__access_pass_mouth_under_a_pulley_mounted_later'
        f = facts_of(name)
        hit = f['openings'][0]['paths']['iron']['hits'][0]
        for kind in ('printed', 'bought', 'hardware', 'hidden', 'mesh'):
            hit['kind'] = kind
            self.assertEqual(judged(name, f), [], kind)
        hit['kind'] = 'self'
        self.assertEqual([(x['verdict'], x['measure'], x['limit'], x['ask']) for x in judged(name, f)], [('FAIL', 20.0, 30.0, None)])
        hit.update(approx=True, by_points='RuntimeError')                            # its own cut failed: not a measurement
        self.assertEqual(verdicts(judged(name, f)), ['NOT_RUN'])
        old = facts_of('access_mouth_sealed__access_pass_through_pocket_against_a_plate_with_its_screw_from_the_far_end')
        for o in old['openings']:
            o['of'] = 'hole'; o['paths'].pop('iron', None)                           # facts from before the census knew it as a pocket
        ctx = FX['access_mouth_sealed__access_pass_through_pocket_against_a_plate_with_its_screw_from_the_far_end']['ctx']
        self.assertEqual(verdicts(judged('inserts_mouth_reachable', old, census=ctx['census'])), ['NOT_RUN'])

    def test_sealed_means_the_whole_section_at_the_mouth(self):
        f = facts_of('access_mouth_sealed__fail_pocket_sealed_on_a_lug')
        h = f['openings'][0]['paths']['own']['hits'][0]
        for at, share, want in ((0.2, 1.0, ['FAIL']), (0.21, 1.0, []), (0.0, 0.95, ['FAIL']), (0.0, 0.94, [])):
            h.update(at=at, share=share)
            self.assertEqual(verdicts(judged('access_mouth_sealed', f)), want, (at, share))
        h.update(at=0.0, share=1.0, kind='mesh', approx=True)                        # a mesh over the mouth: not a measurement
        self.assertEqual(verdicts(judged('access_mouth_sealed', f)), ['NOT_RUN'])
        s = facts_of('access_screw_path__ask_head_under_a_part_mounted_later')        # a head is no mouth
        s['openings'][0]['paths']['own']['hits'] = [{'name': 'Motor plate', 'kind': 'printed', 'at': 0.0, 'share': 1.0, 'expected': False}]
        self.assertEqual(judged('access_mouth_sealed', s), [])
        w = facts_of('access_hole_covered__ask_slot_under_a_printed_cover')           # a slot is no pocket or hole
        w['openings'][0]['paths']['own']['hits'][0].update(at=0.0, share=1.0)
        self.assertEqual(judged('access_mouth_sealed', w), [])

    def test_a_cap_thicker_than_the_limit_is_a_blind_hole(self):
        f = facts_of('access_hole_closed__fail_cap_0_84_in_a_screw_hole')
        for cap, want in ((2.0, ['FAIL']), (2.01, []), (0.0, ['FAIL'])):
            f['closed'][0]['cap'] = cap
            self.assertEqual(verdicts(judged('access_hole_closed', f)), want, cap)

    def test_a_bridge_layer_is_asked_once_and_only_where_one_can_be(self):
        rows = lambda out: [(f['verdict'], f['ask'], f['anchor']['point']) for f in out]
        self.assertEqual(rows(judged(self.BRIDGE)), [('INFO', 'access.bridge_layer', [15.0, 20.0, 0.0])])     # the row of the wider hole
        f = facts_of(self.BRIDGE)
        f['closed'].reverse()
        self.assertEqual(rows(judged(self.BRIDGE, f)), rows(judged(self.BRIDGE)))
        self.assertEqual(rows(judged(self.BRIDGE, f, {'overhang.bridge_step': 0.2})), [('FAIL', None, [15.0, 20.0, 0.0])])      # thicker than one step
        frame = lambda up: {'up': up, 'bed': 0.0, 'source': 'demo', 'why': None}
        self.assertEqual(verdicts(judged(self.BRIDGE, print=frame([0.0, 0.0, 1.0]))), ['INFO'])                  # the counterbore looks at the bed
        self.assertEqual(verdicts(judged(self.BRIDGE, print=frame([0.0, 0.7071, 0.7072]))), ['INFO'])
        self.assertEqual(verdicts(judged(self.BRIDGE, print=frame([0.0, 0.72, 0.69]))), ['FAIL'])                # flatter than 45 degrees
        self.assertEqual(verdicts(judged(self.BRIDGE, print=frame([1.0, 0.0, 0.0]))), ['FAIL'])
        self.assertEqual(verdicts(judged(self.BRIDGE, print={'up': None, 'bed': None, 'source': None, 'why': 'no build table'})), ['INFO'])
        self.assertEqual(verdicts(judged('access_hole_closed', facts_of(self.BRIDGE))), ['FAIL'])                # no census: the diameters are not known


TUBE = 'demo/column_tube'        # the part the bearing fixtures are judged for: it seats the outer ring


class Bearings(unittest.TestCase):
    def face(self, covered, ring='outer', side='+'):
        f = facts_of('bearings_holding__pass_shoulders_and_six_washers')
        faces = f['bearings'][0]['rings'][ring]['faces']
        next(x for x in faces if x['side'] == side)['covered'] = covered
        return f

    def cover(self, **kw):
        return dict({'name': 'Lid', 'kind': 'printed', 'part': 'demo/lid', 'share': 1.0, 'points': 1, 'gap': 0.0, 'touches_other_ring': False}, **kw)

    def test_what_holds_a_ring_face(self):
        held = lambda *covers: verdicts(judged('bearings_holding', self.face(list(covers)), part=TUBE))
        self.assertEqual(held(self.cover(share=0.5)), [])
        self.assertEqual(held(self.cover(share=0.49)), ['FAIL'])
        self.assertEqual(held(self.cover(share=0.1, points=4)), [])
        self.assertEqual(held(self.cover(share=0.1, points=3)), ['FAIL'])
        self.assertEqual(held(*[self.cover(name='ref washer (%d)' % i, share=0.04) for i in range(4)]), [])      # four separate washers
        self.assertEqual(held(*[self.cover(name='ref washer (%d)' % i, share=0.04) for i in range(3)]), ['FAIL'])
        self.assertEqual(held(self.cover(gap=0.05)), [])
        self.assertEqual(held(self.cover(gap=0.06)), ['FAIL'])
        self.assertEqual(held(), ['FAIL'])

    def test_one_finding_per_ring_face(self):
        f = self.face([], 'outer', '+')
        f['bearings'][0]['rings']['inner']['faces'][0]['covered'] = []
        f['bearings'][0]['rings']['inner']['faces'][1]['covered'] = []
        out = judged('bearings_holding', f, part=TUBE)
        self.assertEqual(len(out), 3)
        self.assertEqual(len({x['context'] for x in out}), 3)                         # one anchor, three contexts: three waivers
        self.assertEqual({x['anchor']['kind'] for x in out}, {'bearing'})

    def test_a_cover_on_both_rings_is_one_finding(self):
        out = judged('bearings_holding__fail_clamp_touches_both_rings')
        self.assertEqual(len(out), 1)
        self.assertIn('both rings', out[0]['text'])
        f = self.face([self.cover(gap=0.5, touches_other_ring=True)])                 # it stands off: it touches nothing
        self.assertNotIn('both rings', ' '.join(x['text'] for x in judged('bearings_holding', f, part=TUBE)))
        twin = self.cover(name='ref bearing 6814', kind='hardware', part=None, touches_other_ring=True, twin=True)
        self.assertEqual(judged('bearings_holding', self.face([twin]), part=TUBE), [])       # the other bearing of a pair, ring on ring
        twin['twin'] = False                                                             # a bearing of another size across both rings locks them
        self.assertIn('both rings', ' '.join(x['text'] for x in judged('bearings_holding', self.face([twin]), part=TUBE)))

    def test_no_holding_data_is_not_run_never_a_failure_and_never_skipped(self):
        f = facts_of('bearings_holding__pass_shoulders_and_six_washers')
        f['bearings'][0]['rings']['outer']['faces'] = f['bearings'][0]['rings']['outer']['faces'][:1]
        out = judged('bearings_holding', f, part=TUBE)
        self.assertEqual(verdicts(out), ['NOT_RUN'])
        self.assertIn('no holding data for the outer ring face -', out[0]['text'])
        f['bearings'][0]['designation'] = None
        self.assertEqual(verdicts(judged('bearings_holding', f, part=TUBE)), ['NOT_RUN', 'NOT_RUN'])
        f['bearings'][0]['designation'] = 'no such row'
        self.assertEqual(verdicts(judged('bearings_holding', f, part=TUBE)), ['NOT_RUN', 'NOT_RUN'])

    def test_a_ring_is_judged_with_the_part_that_seats_or_holds_it(self):
        f = self.face([])                                                                # the outer ring of the tube has an open face
        self.assertEqual(verdicts(judged('bearings_holding', f, part=TUBE)), ['FAIL'])
        self.assertEqual(judged('bearings_holding', f, part='demo/base'), [])            # the base holds the inner ring only
        self.assertEqual(judged('bearings_holding', f, part='demo/far_bracket'), [])     # a part that holds no ring of it
        f = self.face([self.cover(gap=0.4)])                                             # the lid stands 0.4 off: nearest at an open face
        self.assertEqual(verdicts(judged('bearings_holding', f, part='demo/lid')), ['FAIL'])
        b = f['bearings'][0]                                                             # no printed part at all round it: every part near reports it
        b['seat'] = {'inner': None, 'outer': None}
        for ring in ('inner', 'outer'):
            for face in b['rings'][ring]['faces']:
                face['covered'] = []
        self.assertEqual(verdicts(judged('bearings_holding', f, part='demo/far_bracket')), ['FAIL'] * 4)

    def test_a_name_the_geometry_contradicts_is_no_bearing(self):
        name = 'bearings_holding__hardware_info_clamp_screws_named_after_the_bearing_they_clamp'
        out = judged(name)
        self.assertEqual(sorted(verdicts(out)), ['FAIL', 'INFO'])
        info = next(x for x in out if x['verdict'] == 'INFO')
        self.assertIn('2 bodies', info['text']); self.assertIn('3 x 12 x 8', info['text'])
        fail = next(x for x in out if x['verdict'] == 'FAIL')
        self.assertIn('608 (1)', fail['text'])                                          # the real bearing keeps its finding
        for judge in ('bearings_clamp_room', 'bearings_seat'):                           # and the other two judges do not judge the screws either
            self.assertEqual([x for x in judged(judge, facts_of(name), part='demo/roller_plate') if 'M3x8' in x['text']], [], judge)
        f = facts_of(name)                                                               # a name with no measured ring contradicts nothing: not run, never dropped
        f['bearings'] = [dict(f['bearings'][0], d=8.0, D=22.0, B=7.0, rings=None, why='no ring geometry: no coaxial circular edges')]
        self.assertEqual(verdicts(judged('bearings_holding', f, part='demo/roller_plate')), ['NOT_RUN'])

    def test_a_body_that_stands_off_the_ring_is_no_clamp_and_the_open_face_still_fails(self):
        name = 'bearings_clamp_room__hardware_pass_neighbour_0_85_off_the_ring_is_no_clamp'
        self.assertEqual(judged(name), [])
        out = judged('bearings_holding', facts_of(name), part='demo/spindle')
        self.assertEqual([(x['verdict'], x['measure']) for x in out if 'inner ring face +' in x['text']], [('FAIL', 0.0)])
        f = facts_of(name)                                                               # facts of an older probe: the gaps are read from the covers
        for key in ('gap', 'stop_gap'):
            del f['bearings'][0]['clamps'][0][key]
        self.assertEqual(judged('bearings_clamp_room', f, part='demo/spindle'), [])
        f['bearings'][0]['rings']['inner']['faces'][0]['covered'][0]['gap'] = 0.0        # drawn touching: a clamp again, and its room is too long
        self.assertEqual(verdicts(judged('bearings_clamp_room', f, part='demo/spindle')), ['FAIL'])

    def test_a_designation_listed_as_an_alias_is_a_row(self):
        f = facts_of('bearings_holding__pass_shoulders_and_six_washers')
        f['bearings'][0]['designation'] = 'old name'
        tables = dict(REG['tables'], bearings=dict(REG['tables']['bearings'], aliases={'old name': '6814'}))
        self.assertEqual(judged('bearings_holding', f, tables=tables, part=TUBE), [])
        self.assertEqual(verdicts(judged('bearings_holding', f, part=TUBE)), ['NOT_RUN'])           # without the alias it is a ring of no row
        self.assertEqual(verdicts(judged('bearings_holding', f, tables={}, part=TUBE)), ['NOT_RUN'])

    def test_an_inner_seat_wider_at_its_entry_stops_the_bearing(self):
        f = facts_of('bearings_seat__fail_post_0_5_under_the_bore')
        f['bearings'][0]['seat']['inner'].update(d=70.0, entry_d=70.4)
        out = judged('bearings_seat', f, part='demo/base')
        self.assertEqual([(x['measure'], x['limit']) for x in out], [(70.4, 70.0)])
        self.assertEqual(judged('bearings_seat', f, part=TUBE), [])                      # the post is the base's: not repeated for the tube
        f['bearings'][0]['seat']['inner'].update(entry_d=69.75)
        self.assertEqual(judged('bearings_seat', f, part='demo/base'), [])

    def test_a_seat_without_a_bearing_is_failed_only_when_it_is_proven_empty(self):
        row = lambda **kw: {'bearings': [], 'seats_without_bearing': [dict({'hole': 'h9', 'anchor': {'kind': 'hole', 'axis': [0, 0, 1], 'point': [0.0, 0.0, 12.0]},
                                                                             'd': 47.0, 'designation': '6005'}, **kw)]}
        got = lambda f, **ctx: [(x['verdict'], x['ask']) for x in judged('bearings_seat', f, **ctx)]
        self.assertEqual(got(row()), [('FAIL', None)])                                   # the table diameter, nothing on the axis: empty
        self.assertEqual(got(row(match='exact', bodies=[])), [('FAIL', None)])
        self.assertEqual(got(row(match='exact', bodies=['ref shaft / ring A'])), [('NOT_RUN', None)])
        self.assertEqual(got(row(match='band', bodies=['ref motor / shaft'])), [('INFO', 'census.unknown_hole')])
        self.assertEqual(got(row(match='band', bodies=[])), [('INFO', 'census.unknown_hole')])
        hole = {'id': 'h9', 'class': {'role': 'bearing_seat', 'kind': '6005', 'match': 'exact'}, 'coaxial': [{'kind': 'bought', 'name': 'ref shaft / ring A'}, {'kind': 'printed', 'name': 'Lid'}]}
        self.assertEqual(got(row(), census={'holes': [hole], 'openings': []}), [('NOT_RUN', None)])     # older facts: the census says what is on the axis
        hole['coaxial'] = hole['coaxial'][1:]
        self.assertEqual(got(row(), census={'holes': [hole], 'openings': []}), [('FAIL', None)])         # a printed lid on the axis is no bearing


class Project(unittest.TestCase):
    def test_each_finding_names_its_own_part(self):
        out = judged('project_stl_without_stamp__fail_stl_from_another_exporter')
        self.assertEqual(sorted(f['part'] for f in out), ['demo/side_link', 'main:shim_0_5.stl'])
        self.assertEqual({f['anchor']['kind'] for f in out}, {'part'})

    def test_a_stale_stl_stamp_fails_and_an_unknown_state_is_not_run(self):
        f = facts_of('project_stl_without_stamp__pass_every_stl_stamped')
        f['stls'][0].update(stamp='stale', why='the part changed after the export')
        out = judged('project_stl_without_stamp', f, part='main:')
        self.assertEqual(verdicts(out), ['FAIL'])
        self.assertIn('the part changed after the export', out[0]['text'])
        f['stls'][0]['stamp'] = 'maybe'
        self.assertEqual(verdicts(judged('project_stl_without_stamp', f, part='main:')), ['NOT_RUN'])

    def test_unstamped_and_stale_are_two_rules(self):
        f = facts_of('project_mesh_stale__fail_project_holds_the_part_without_its_new_pocket')
        self.assertEqual(judged('project_mesh_unstamped', f, part='main:'), [])
        g = facts_of('project_mesh_unstamped__fail_mesh_of_no_stamp')
        self.assertEqual(judged('project_mesh_stale', g, part='main:'), [])

    def test_an_object_can_be_off_the_plates_and_switched_off(self):
        f = facts_of('project_object_on_no_plate__fail_object_left_off_the_plates')
        f['objects'][1]['printable'] = False
        self.assertEqual(verdicts(judged('project_object_on_no_plate', f, part='main:')), ['FAIL'])
        self.assertEqual(verdicts(judged('project_object_not_printable', f, part='main:')), ['FAIL'])

    def test_build_table(self):
        f = facts_of('project_build_table_differs__pass_table_equals_project')
        f['build_table'] = None                                                       # the project names no table
        self.assertEqual(judged('project_build_table_differs', f, part='main:'), [])
        f['build_table'] = {'file': 'bambulab/build_3mf.py', 'literal': True, 'differs': [
            {'what': 'object missing in the table', 'table': None, 'project': 'shim'}, {'what': 'plate name', 'table': 'plate 2', 'project': 'small parts'}]}
        out = judged('project_build_table_differs', f, part='main:')
        self.assertEqual(verdicts(out), ['FAIL', 'FAIL'])
        self.assertEqual(len({x['context'] for x in out}), 2)
        del f['build_table']['differs']
        self.assertEqual(verdicts(judged('project_build_table_differs', f, part='main:')), ['NOT_RUN'])
        info = judged('project_build_table_differs__info_table_is_computed')
        self.assertEqual([x['verdict'] in gate_common.BLOCKING for x in info], [False])

    def test_target_tolerates_rounding_only(self):
        f = facts_of('project_target_differs__pass_file_and_stamps_match_the_declared_target')
        f['target_file']['line_width'] = 0.624
        self.assertEqual(judged('project_target_differs', f, part='main:'), [])
        f['target_file']['line_width'] = 0.65
        self.assertEqual(verdicts(judged('project_target_differs', f, part='main:')), ['FAIL'])
        f['target_file'] = {'nozzle': None, 'line_width': 0.62, 'materials': []}
        self.assertEqual(verdicts(judged('project_target_differs', f, part='main:')), ['NOT_RUN'])


class Regression(unittest.TestCase):
    def test_unintended_needs_a_stated_intent(self):
        name = 'regression_unintended__fail_neighbour_changed_by_a_cut_meant_for_one_part'
        self.assertEqual(judged(name, intent=None), [])
        self.assertEqual([f['part'] for f in judged(name, intent=[])], ['demo/base', 'demo/column_tube'])
        self.assertEqual(judged(name, intent=['demo/base', 'demo/column_tube']), [])
        self.assertEqual([f['part'] for f in judged(name, intent='demo/base')], ['demo/column_tube'])

    def test_a_part_that_was_never_checked_did_not_change(self):
        f = {'changed': [{'part': 'demo/new_part', 'sig': 'ab' * 32, 'prev_sig': None}]}
        self.assertEqual(judged('regression_unintended', f, intent=['demo/base']), [])

    def test_the_context_of_an_unintended_change_is_the_two_signatures(self):
        name = 'regression_unintended__fail_neighbour_changed_by_a_cut_meant_for_one_part'
        a = judged(name)[0]
        f = facts_of(name)
        f['changed'][1]['sig'] = 'ee' * 32
        self.assertNotEqual(a['context'], judged(name, f)[0]['context'])

    def test_nothing_to_compare_with_is_no_finding(self):
        self.assertEqual(judged('regression_feature_lost__fail_tie_anchors_gone_after_a_move', prev=None), [])

    def lost(self, change):
        name = 'regression_feature_lost__pass_same_features_after_a_0_54_face_cut'
        f = facts_of(name)
        change(f)
        return judged(name, f)

    def test_what_counts_as_lost(self):
        def moved_across(f): f['holes'][0]['anchor']['point'][0] += 3.0
        def moved_a_little(f): f['holes'][0]['anchor']['point'][0] += 0.2
        def moved_along(f): f['holes'][0]['anchor']['point'][2] -= 3.0
        def turned(f): f['holes'][0]['anchor']['axis'] = [0, 0, -1]
        def pocket_became_a_hole(f): f['holes'][2]['anchor']['kind'] = 'hole'; f['holes'][2]['segments'] = [{'d': 4.4, 'len': 6.5}]     # bored out
        def only_its_class_changed(f): f['holes'][2]['anchor']['kind'] = 'hole'; f['holes'][2]['class'] = None
        def class_changed_and_0_04_off(f): only_its_class_changed(f); f['holes'][2]['segments'][2]['len'] += 0.04
        def class_changed_and_0_1_off(f): only_its_class_changed(f); f['holes'][2]['segments'][2]['len'] += 0.1
        def removed(f): del f['holes'][2]
        def shorter(f): f['holes'][2]['segments'] = f['holes'][2]['segments'][1:]
        self.assertEqual(verdicts(self.lost(moved_across)), ['FAIL'])
        self.assertEqual(self.lost(moved_a_little), [])
        self.assertEqual(verdicts(self.lost(moved_along)), ['FAIL'])                  # more than the 2 mm a recut mouth may move
        self.assertEqual(verdicts(self.lost(turned)), ['FAIL'])
        out = self.lost(pocket_became_a_hole)
        self.assertEqual(verdicts(out), ['FAIL'])
        self.assertIn('no longer an insert pocket', out[0]['text'])
        self.assertEqual(out[0]['anchor']['kind'], 'insert_pocket')
        # the same hole under another class (a table row, the classifier, a ref on its axis changed): the part lost nothing
        out = self.lost(only_its_class_changed)
        self.assertEqual([(f['verdict'], f['ask'], f['anchor']['kind']) for f in out], [('INFO', None, 'part')])
        self.assertIn('1 hole of the last checked geometry is classed differently now and did not change', out[0]['text'])
        self.assertIn('was an insert pocket (M3x4x5) and is a plain hole (no table row)', out[0]['text'])
        self.assertEqual(verdicts(self.lost(class_changed_and_0_04_off)), ['INFO'])     # within the step a context rounds to
        self.assertEqual(verdicts(self.lost(class_changed_and_0_1_off)), ['FAIL'])      # the hole itself changed: lost, and asked
        self.assertEqual(verdicts(self.lost(removed)), ['FAIL'])
        self.assertEqual(self.lost(shorter), [])                                      # a lost relief is inserts.relief_missing, not this rule

    def test_holes_that_only_changed_their_class_are_one_row_for_the_part(self):
        name = 'regression_feature_lost__live2_info_same_hole_classed_as_an_insert_pocket_now'
        fx = FX[name]
        prev = copy.deepcopy(fx['ctx']['prev'])
        prev['holes'][1]['anchor']['kind'] = 'insert_pocket'                          # the second hole was a pocket to the older tools
        out = judged(name, prev=prev)
        self.assertEqual(verdicts(out), ['INFO']); self.assertIn('2 holes of the last checked geometry are classed differently now', out[0]['text'])
        self.assertIn('and 1 more', out[0]['text'])
        self.assertNotEqual(out[0]['context'], judged(name)[0]['context'])            # another set of holes is another row
        # a hole that went blind is lost whatever its class is called today
        f = facts_of(name)
        prev = copy.deepcopy(fx['ctx']['prev']); prev['holes'][0]['through'] = True
        out = judged(name, f, prev=prev)
        self.assertEqual(verdicts(out), ['FAIL']); self.assertIn('went through the part and is blind now', out[0]['text'])
        # ... and so is a blind hole that goes through now, and one whose floor got thinner, with the same segments
        f = facts_of(name)
        prev = copy.deepcopy(fx['ctx']['prev']); prev['holes'][0]['through'] = False; f['holes'][0]['through'] = True
        out = judged(name, f, prev=prev)
        self.assertEqual([(x['verdict'], x['ask']) for x in out], [('FAIL', 'regression.intended')]); self.assertIn('was blind and goes through the part now', out[0]['text'])
        f = facts_of(name)
        prev = copy.deepcopy(fx['ctx']['prev'])
        prev['holes'][0].update(through=False, floor=2.0); f['holes'][0].update(through=False, floor=0.3)
        out = judged(name, f, prev=prev)
        self.assertEqual(verdicts(out), ['FAIL']); self.assertIn('the material under it changed: 2 mm were left under it, now 0.3', out[0]['text'])
        f['holes'][0]['floor'] = 2.04                                                 # within the step a context rounds to: the same hole
        self.assertEqual(verdicts(judged(name, f, prev=prev)), ['INFO'])

    def test_a_lost_feature_asks_whether_it_was_meant(self):
        for name in ('regression_feature_lost__fail_tie_anchors_gone_after_a_move', 'regression_feature_lost__fail_two_screw_holes_half_filled_by_a_join'):
            out = judged(name)
            self.assertEqual({(f['verdict'], f['ask']) for f in out}, {('FAIL', 'regression.intended')})
            self.assertEqual(len({gate_features.anchor_key(f['anchor']) for f in out}), 2)

    def test_a_census_that_is_not_whole_calls_nothing_lost(self):
        name = 'regression_feature_lost__fail_tie_anchors_gone_after_a_move'
        f = facts_of(name)
        del f['openings']
        self.assertEqual(verdicts(judged(name, f)), ['NOT_RUN'])


class SecondLiveRun(unittest.TestCase):
    """What the second run on a real assembly showed as false, each held from both sides: the false row is gone, and the
    row beside it that is true stays."""

    # -- a head on a clamp washer (screws.under_head)

    def test_a_head_that_bears_on_steel_through_its_washer_is_not_judged_for_plastic(self):
        name = 'screws_under_head__live2_pass_head_on_a_clamp_washer_over_a_bearing_ring'
        self.assertEqual(judged(name), [])
        f = facts_of(name)
        land = f['washers'][0]['landing']
        land['covers'][1]['gap'] = 0.05                                               # the boss within the contact gap: the washer lies on plastic too
        self.assertEqual(verdicts(judged(name, f)), ['FAIL'])
        land['covers'][1]['gap'] = 0.06
        self.assertEqual(judged(name, f), [])
        del land['covers'][1]['gap']                                                  # a printed cover with no gap: not proven off the plastic
        self.assertEqual(verdicts(judged(name, f)), ['FAIL'])
        f = facts_of(name)
        f['washers'][0]['landing']['on_bearing'] = None                               # no bearing by the unit: the first cover decides, and it is hardware in contact
        self.assertEqual(judged(name, f), [])
        f['washers'][0]['landing']['covers'][0]['kind'] = 'bought'                    # a bought body of unknown stiffness is no proof
        self.assertEqual(verdicts(judged(name, f)), ['FAIL'])
        f = facts_of(name)
        f['washers'][0]['landing']['covers'] = 'unreadable'
        self.assertEqual(verdicts(judged(name, f)), ['FAIL'])
        del f['washers'][0]['landing']['covers']                                      # facts of an older unit: "on_bearing" alone does not prove that no plastic is loaded
        self.assertEqual(verdicts(judged(name, f)), ['FAIL'])
        f['washers'][0]['landing']['covers'] = []                                     # the unit looked and listed nothing but the ring it names
        self.assertEqual(judged(name, f), [])
        # the first thing under the washer is the rim of an insert, a nut or a screw head: hardware by its kind, and it sits in plastic
        for rim in ('ref axle / insert M4x8x6 (1)', 'ref axle / M4 nut', 'ref axle / M4x10 cap screw (2)', 'ref axle / set screw'):
            f = facts_of('screws_under_head__live2_pass_washer_on_a_steel_sleeve')
            f['washers'][0]['landing']['covers'][0]['name'] = rim
            self.assertEqual(verdicts(judged('screws_under_head', f, part='demo/carriage')), ['FAIL'], rim)
        f = facts_of('screws_under_head__live2_pass_washer_on_a_steel_sleeve')
        self.assertEqual(judged('screws_under_head', f, {'inserts.ref_pattern': '('}, part='demo/carriage'), [])      # a pattern that does not compile names nothing
        f = facts_of(name)
        f['screws'][0]['washer'] = 'w9'                                               # a washer the facts do not hold
        self.assertEqual(verdicts(judged(name, f)), ['FAIL'])
        f['screws'][0]['washer'] = None                                               # a bare head with nothing under it
        self.assertEqual(verdicts(judged(name, f)), ['FAIL'])

    # -- an air gap with a ring in it (screws.shell_bears)

    BRIDGED = 'screws_shell_bears__live2_info_six_screws_across_the_clamp_room_of_a_ring'

    def test_a_gap_that_a_bought_body_bridges_is_one_info_row(self):
        out = judged(self.BRIDGED)
        self.assertEqual([(f['verdict'], f['by'], f['anchor']['kind']) for f in out], [('INFO', ['ref ball bearing 6008 (1)'], 'part')])
        self.assertIn('6 screws of this part clamp across a gap that a bought body bridges, the widest 1 mm between Lid and Housing', out[0]['text'])
        f = facts_of(self.BRIDGED)
        f['screws'][2]['gaps'][0]['in_gap'] = []                                      # one of the six has nothing in its gap
        out = judged(self.BRIDGED, f)
        self.assertEqual(sorted(verdicts(out)), ['FAIL', 'INFO'])
        self.assertIn('5 screws', next(x for x in out if x['verdict'] == 'INFO')['text'])
        self.assertIn('Nothing bought stands in the gap within 8 mm', next(x for x in out if x['verdict'] == 'FAIL')['text'])

    def test_what_bridges_a_gap(self):
        def one(change, **params):
            f = facts_of(self.BRIDGED)
            f['screws'] = f['screws'][:1]
            change(f['screws'][0]['gaps'][0])
            return judged(self.BRIDGED, f, params or None)
        body = lambda row: row['in_gap'][0]
        both = lambda along: (lambda r: body(r).update(along=along))
        self.assertEqual(verdicts(one(both([0.05, 0.0]))), ['INFO'])                                   # both parts press on it along the screw: contact is the parameter, exactly
        self.assertEqual(verdicts(one(both([0.06, 0.0]))), ['FAIL'])                                   # one stops 0.06 short along the screw: it would bend first
        self.assertEqual(verdicts(one(both([0.0, 0.06]))), ['FAIL'])
        self.assertEqual(verdicts(one(both([0.2, 0.0]), **{'bearings.contact_gap_max': 0.2})), ['INFO'])
        # a part that only seats the body sideways (a fit clearance, no shoulder behind it) holds nothing along the screw, however wide the seat tolerance is
        out = one(both([0.0, None]), **{'bearings.seat_d_tol': 5.0})
        self.assertEqual(verdicts(out), ['FAIL']); self.assertIn('Lid presses on it, Housing is not met on a line along the screw through it', out[0]['text'])
        self.assertEqual(verdicts(one(both([None, None]))), ['FAIL'])                                  # a pin through both parts
        self.assertEqual(verdicts(one(both([0.1, 0.1]))), ['FAIL'])                                    # pressed by neither
        # facts of a unit that measured whole-body distances and nothing along the screw: no contact is proven by them
        out = one(lambda r: (body(r).pop('along'), body(r).update(to=[0.0, 0.0], to_along=[0.0, 0.0])))
        self.assertEqual(verdicts(out), ['FAIL']); self.assertIn('is not in these facts: collect the facts again', out[0]['text'])
        out = one(lambda r: body(r).update(along=[None, None], late=True))                             # the time of the call ran out
        self.assertEqual(verdicts(out), ['FAIL']); self.assertIn('was not measured (the time of that call was used up)', out[0]['text'])
        self.assertEqual(verdicts(one(lambda r: body(r).update(off=8.0))), ['INFO'])                    # the search radius is the parameter
        self.assertEqual(verdicts(one(lambda r: body(r).update(off=8.1))), ['FAIL'])
        self.assertEqual(verdicts(one(lambda r: body(r).update(off=5.0), **{'screws.bridge_search_r': 4.0})), ['FAIL'])
        out = one(lambda r: r.update(in_gap=[], search_r=5.0))                                          # looked less far than today's rule counts
        self.assertEqual(verdicts(out), ['FAIL']); self.assertIn('looked into only 5 mm round the screw axis, the rule counts 8', out[0]['text'])
        out = one(lambda r: r.update(gap=0.04))                                                         # the list holds no row for the gap that was measured
        self.assertEqual(verdicts(out), ['FAIL']); self.assertIn('no row for that gap', out[0]['text'])

    def test_the_screws_across_one_gap_with_the_same_loose_body_are_one_row(self):
        name = 'screws_shell_bears__live2_fail_six_screws_one_row_for_a_ring_the_housing_shoulder_stands_off'
        out = judged(name)
        self.assertEqual([(f['verdict'], f['measure'], f['by'], f['anchor']['kind']) for f in out], [('FAIL', 0.8, ['ref ball bearing 6008 (1)'], 'screw')])
        self.assertIn('6 screws of this part clamp across 0.8 mm of air between Lid and Housing (at most 0.05). ref ball bearing 6008 (1) stands in that gap and does not carry them: '
                      'Lid presses on it, Housing stands 0.85 mm off it along the screw', out[0]['text'])
        self.assertIn('First: screw ref flange screws / M4x30 cap screw (1)', out[0]['text'])
        row = gate_features.finding_id(out[0])
        def again(change):
            f = facts_of(name)
            change(f)
            return judged(name, f)
        moved = again(lambda f: [s['gaps'][0]['in_gap'][0].update(along=[0.0, 0.4]) for s in f['screws']])           # the shoulder came nearer: another finding, a waiver does not carry over
        self.assertEqual(verdicts(moved), ['FAIL']); self.assertNotEqual(gate_features.finding_id(moved[0]), row)
        fewer = again(lambda f: f['screws'].pop())                                                                    # a screw less: the set the row stands for changed
        self.assertEqual(verdicts(fewer), ['FAIL']); self.assertNotEqual(gate_features.finding_id(fewer[0]), row); self.assertIn('5 screws', fewer[0]['text'])
        mixed = again(lambda f: f['screws'][5]['gaps'][0].update(in_gap=[]))                                          # one of them has nothing in its gap: its own row, as ever
        self.assertEqual(sorted((f['verdict'], f['anchor']['kind']) for f in mixed), [('FAIL', 'screw'), ('FAIL', 'screw')])
        self.assertEqual(sorted('5 screws of this part' in f['text'] for f in mixed), [False, True])
        other = again(lambda f: f['screws'][5]['gaps'][0]['in_gap'][0].update(name='ref sleeve D50'))                 # another body: another cause, another row
        self.assertEqual(len(other), 2)
        closed = again(lambda f: [s['gaps'][0]['in_gap'][0].update(along=[0.0, 0.0]) for s in f['screws']])           # the shoulder reaches the ring: the clamp room
        self.assertEqual(verdicts(closed), ['INFO'])

    def test_older_facts_keep_the_finding_and_say_what_would_make_it_a_clamp_room(self):
        old = judged('screws_shell_bears__live2_fail_gap_in_older_facts_names_the_clamp_room')
        self.assertEqual(verdicts(old), ['FAIL'] * 6)
        for f in old:
            self.assertIn('if a bearing ring or another steel part is clamped between the two parts this gap is its clamp room: collect the facts again', f['text'])
        seam = judged('screws_shell_bears__fail_six_seam_screws_across_a_0_35_gap')                    # the incident of the rule, in facts of the unit of its time
        self.assertEqual(verdicts(seam), ['FAIL'] * 6)
        new = facts_of(self.BRIDGED)                                                                   # the same screw keeps its id from one unit to the next
        for s in new['screws']:
            s['gaps'][0]['in_gap'] = []
        self.assertEqual([gate_features.finding_id(f) for f in judged(self.BRIDGED, new)], [gate_features.finding_id(f) for f in old])

    def test_a_screw_with_two_gaps_is_judged_by_the_one_nothing_bridges(self):
        f = facts_of(self.BRIDGED)
        f['screws'] = f['screws'][:1]
        ring = f['screws'][0]['gaps'][0]
        f['screws'][0]['gaps'].append({'gap': 0.4, 'at': 20.0, 'between': [{'name': 'Housing', 'kind': 'printed', 'part': 'demo/housing'}, {'name': 'Foot', 'kind': 'printed', 'part': 'demo/foot'}],
                                       'search_r': 8.0, 'in_gap': []})
        out = judged(self.BRIDGED, f)
        self.assertEqual([(x['verdict'], x['measure']) for x in out], [('FAIL', 0.4)])
        self.assertIn('between Housing and Foot', out[0]['text'])
        ring['in_gap'] = []
        self.assertEqual([(x['verdict'], x['measure']) for x in judged(self.BRIDGED, f)], [('FAIL', 1.0)])

    # -- a thread that nothing receives (screws.engagement)

    def test_a_thread_in_nothing_fails_and_says_where(self):
        name = 'screws_engagement__live2_fail_thread_ends_in_a_part_with_no_insert_ref'
        out = judged(name)
        self.assertEqual([(f['verdict'], f['measure'], f['limit']) for f in out], [('FAIL', 0.0, 3.2)])
        text = out[0]['text']
        for piece in ('nothing on its axis receives the thread', 'It passes this part through the counterbore h6 (M3) at (25, -13.5, 40)', 'the end of its thread stands in demo/motor_housing',
                      '1 mm of free room in front of the tip', 'Model the insert or the nut it screws into as a ref body', 'inserts.ref_pattern is'):
            self.assertIn(piece, text)
        self.assertNotIn('in its thread', text)
        f = facts_of(name)
        del f['screws'][0]['tip_at']; del f['fastener_holes']                         # facts of an older unit: said, not guessed
        text = judged(name, f)[0]['text']
        self.assertIn('These facts hold no list of the holes on its axis', text); self.assertIn('in a body these facts do not name', text)
        f = facts_of(name)
        f['screws'][0]['tip_at'] = None                                               # the unit looked: the thread ends in open air
        self.assertIn('no solid body stands round the end of its thread', judged(name, f)[0]['text'])
        f['screws'][0].update(engagement=0.0, into={'kind': 'insert', 'name': 'ref insert M3x4x5'})     # an insert the tip does not reach: the old row
        self.assertIn('0 mm of thread in ref insert M3x4x5', judged(name, f)[0]['text'])
        pocket = judged('screws_engagement__live2_fail_thread_in_a_pocket_whose_insert_no_pattern_knows')
        self.assertIn('Its thread ends in the insert pocket h4 (M3x4x5) at (10, 0, 20) of this part', pocket[0]['text'])
        self.assertIn('lies a bought body that nothing recognised: ref brass parts / threaded sleeve (2)', pocket[0]['text'])

    # -- bearings inside a bought assembly (bearings.holding, clamp_room, seat)

    ROLLERS = 'bearings_holding__live2_info_rollers_inside_a_bought_drive_unit'

    def test_a_bearing_no_printed_part_holds_is_counted_not_judged(self):
        out = judged(self.ROLLERS)
        self.assertEqual([(f['verdict'], f['anchor']['kind']) for f in out], [('INFO', 'part')])
        self.assertIn('8 bearings near this part are held by bought bodies and by no printed part', out[0]['text'])
        self.assertIn('4 x Drive unit / planet roller 2 (685ZZ), 4 x Drive unit / planet roller 3 (685ZZ)', out[0]['text'])
        self.assertEqual(judged('bearings_clamp_room', facts_of(self.ROLLERS), part='demo/arm'), [])
        self.assertEqual(judged('bearings_seat', facts_of(self.ROLLERS), part='demo/arm'), [])
        both = judged('bearings_holding__live2_fail_own_bearing_is_judged_beside_the_rollers_of_the_drive_unit')
        self.assertEqual(sorted(verdicts(both)), ['FAIL', 'FAIL', 'INFO'])
        self.assertEqual({f['anchor']['point'][1] for f in both if f['verdict'] == 'FAIL'}, {40.0})    # the bearing the arm seats, and only that one

    def test_only_what_the_facts_prove_is_left_out(self):
        def one(change):
            f = facts_of(self.ROLLERS)
            f['bearings'] = f['bearings'][4:5]                                        # a roller with two faces that nothing touches
            change(f['bearings'][0])
            return f
        hold = lambda change, part='demo/arm': verdicts(judged('bearings_holding', one(change), part=part))
        self.assertEqual(hold(lambda b: None), ['INFO'])
        face = lambda b, ring, i: b['rings'][ring]['faces'][i]['covered']
        # a printed part touches one ring face: the ring is that part's, the other ring goes with it
        touch = lambda b: face(b, 'outer', 0).insert(0, {'name': 'Arm', 'kind': 'printed', 'part': 'demo/arm', 'share': 1.0, 'points': 1, 'gap': 0.0, 'touches_other_ring': False})
        self.assertEqual(hold(touch), ['FAIL', 'FAIL'])                                                 # the roller's two free faces are the arm's findings now
        self.assertEqual(hold(touch, 'demo/far_bracket'), [])                                           # ... and of nobody else
        near = lambda b: face(b, 'outer', 1).insert(0, {'name': 'Arm', 'kind': 'printed', 'part': 'demo/arm', 'share': 0.0, 'points': 0, 'gap': 0.4, 'touches_other_ring': False})
        self.assertEqual(hold(near), ['FAIL', 'FAIL'])                                                  # a printed part that stops 0.4 short of a free face answers for it
        self.assertIn('the outer ring face - is touched by nothing: the nearest, Arm, stands 0.4 mm off', judged('bearings_holding', one(near), part='demo/arm')[1]['text'])
        self.assertEqual(hold(near, 'demo/far_bracket'), [])                                            # ... and nobody else does
        just = lambda b: face(b, 'outer', 1).insert(0, {'name': 'Arm', 'kind': 'printed', 'part': 'demo/arm', 'share': 0.0, 'points': 0, 'gap': 0.06, 'touches_other_ring': False})
        self.assertEqual(hold(just), ['FAIL', 'FAIL'])                                                  # 0.01 over the contact gap is not "merely near" either
        beside = lambda b: face(b, 'outer', 0).insert(0, {'name': 'Arm', 'kind': 'printed', 'part': 'demo/arm', 'share': 0.0, 'points': 0, 'gap': 0.4, 'touches_other_ring': False})
        self.assertEqual(hold(beside), ['FAIL', 'FAIL'])                                                # a printed body at a face that steel already touches: not proven unheld, judged as before
        self.assertEqual(judged('bearings_clamp_room', one(near), part='demo/arm'), judged('bearings_clamp_room', one(lambda b: None), part='demo/other'))
        self.assertEqual(hold(lambda b: b['seat'].update(outer={'d': 11.0, 'part': 'demo/arm', 'len': 5.0, 'entry_d': 11.0})), ['FAIL', 'FAIL'])     # seated in the arm
        # what is not measured proves nothing: the bearing is judged as before, for every part near it
        self.assertEqual(hold(lambda b: b.pop('seat')), ['FAIL', 'FAIL'])
        self.assertEqual(hold(lambda b: b['rings']['inner']['faces'][0].pop('covered')), ['NOT_RUN', 'FAIL', 'FAIL'])
        self.assertEqual(hold(lambda b: b.update(rings=None)), ['NOT_RUN'])
        def nothing_touches(b):
            for ring in ('inner', 'outer'):
                for f in b['rings'][ring]['faces']:
                    f['covered'] = []
            b['clamps'] = []
        self.assertEqual(hold(nothing_touches), ['FAIL'] * 4)                                           # held by nothing at all: no bought assembly holds it either
        clamp = lambda b: b['clamps'].append({'ring': 'outer', 'side': '+', 'by': 'printed', 'name': 'Arm', 'part': 'demo/arm', 'gap': 0.0, 'stop_gap': 0.0, 'stop_part': None,
                                              'room': 5.0, 'ring_width': 5.0})
        self.assertEqual(hold(clamp), ['FAIL', 'FAIL'])                                                 # a printed clamp that touches: judged
        loose = lambda b: b['clamps'].append({'ring': 'outer', 'side': '+', 'by': 'printed', 'name': 'Arm', 'part': 'demo/arm', 'gap': 0.3, 'stop_gap': 0.0, 'stop_part': None,
                                              'room': 5.3, 'ring_width': 5.0})
        self.assertEqual(hold(loose), ['FAIL', 'FAIL'])                                                 # ... and one that stops 0.3 short of the ring
        stop = lambda b: b['clamps'].append({'ring': 'outer', 'side': '+', 'by': 'washer', 'name': 'Washer', 'part': None, 'gap': 0.0, 'stop_gap': 0.4, 'stop_part': 'demo/arm',
                                             'room': 5.4, 'ring_width': 5.0})
        self.assertEqual(hold(stop), ['FAIL', 'FAIL'])                                                  # ... and a printed stop behind the ring
        self.assertEqual(hold(lambda b: b.pop('clamps')), ['FAIL', 'FAIL'])                             # no clamp list: not measured, nothing is proven

    def test_the_row_of_unheld_bearings_names_the_mesh_bodies_near_them(self):
        out = judged(self.ROLLERS)
        self.assertIn('Near them lies 1 body known only as a mesh, which the unit cannot measure: ref scanned housing / shell', out[0]['text'])
        f = facts_of(self.ROLLERS)
        for b in f['bearings']:
            b['mesh_near'] = []
        self.assertNotIn('mesh', judged(self.ROLLERS, f)[0]['text'])
        lip = judged('bearings_holding__live2_fail_printed_lip_that_stops_short_of_a_roller_ring')
        self.assertEqual(sorted(verdicts(lip)), ['FAIL', 'FAIL', 'INFO'])
        self.assertIn('7 bearings near this part', next(x for x in lip if x['verdict'] == 'INFO')['text'])

    def test_the_seat_of_a_bought_post_is_not_this_parts(self):
        name = 'bearings_seat__live2_pass_roller_on_a_steel_post_is_no_seat_of_this_part'
        self.assertEqual(judged(name), [])
        f = facts_of(name)
        f['bearings'][0]['rings']['outer']['faces'][0]['covered'].insert(0, {'name': 'Arm', 'kind': 'printed', 'part': 'demo/arm', 'share': 1.0, 'points': 1, 'gap': 0.0})
        self.assertEqual(verdicts(judged(name, f)), ['FAIL'])                          # the arm holds a ring of it: its seat is judged with the arm again

    # -- equal places round one axis (walls.thin_faces)

    WHEEL = 'walls_thin_faces__live2_fail_teeth_of_a_wheel_are_one_row'

    def wheel(self, drop=(), change=None, shift=None):
        """The faces of the fixture: tooth k holds the faces 1000 + 10 k, + 1 (floor) and + 2 (a flat of the flank); 77 is the ledge."""
        f = facts_of(self.WHEEL)
        f['faces'] = [x for x in f['faces'] if not (x['id'] >= 1000 and (x['id'] - 1000) // 10 in drop)]
        for x in f['faces']:
            if change and x['id'] in change:
                x['width'] = change[x['id']]
            if shift and x['id'] >= 1000 and (x['id'] - 1000) // 10 in shift:
                x['anchor']['point'] = [c + d for c, d in zip(x['anchor']['point'], shift[(x['id'] - 1000) // 10])]
        return f

    def profile(self, out):
        return [f for f in out if 'equal places repeat on one circle round one axis' in f['text']]

    def test_teeth_are_one_row_with_the_count_the_axis_and_one_place(self):
        out = judged(self.WHEEL)
        row, = self.profile(out)
        self.assertEqual((row['verdict'], row['measure'], row['anchor']), ('FAIL', 0.2, {'kind': 'face', 'axis': [0.0, 1.0, 0.0], 'point': [0.0, 10.0, 50.0]}))
        self.assertIn('16 equal places repeat on one circle round one axis (through (0, 10, 50) along (0, 1, 0), radius 20.1 mm, one every 22.5 deg)', row['text'])
        self.assertIn('One of them: 2 narrow faces that touch, 0.2 to 0.35 mm wide', row['text']); self.assertIn('1 more faces of each place', row['text'])
        self.assertEqual(len(out), 2)                                                 # and the ledge elsewhere on the part
        back = self.wheel(change={1000: 0.22, 1010: 0.18})                            # widths with noise, and the facts in the opposite order: the same row
        ahead = copy.deepcopy(back); back['faces'].reverse()
        self.assertEqual(self.profile(judged(self.WHEEL, back))[0]['context'], self.profile(judged(self.WHEEL, ahead))[0]['context'])
        self.assertEqual(self.profile(judged(self.WHEEL, back))[0]['context'], row['context'])        # the id rests on the median place, not on the first of the facts
        self.assertEqual([f['verdict'] for f in judged(self.WHEEL, self.wheel(), {'walls.strip_turn_max': 10.0})].count('FAIL'), 2)    # the flats judged too: still equal places

    def test_what_makes_a_profile(self):
        count = lambda f: (len(self.profile(judged(self.WHEEL, f))), len(judged(self.WHEEL, f)))
        self.assertEqual(count(self.wheel()), (1, 2))
        gone = judged(self.WHEEL, self.wheel(drop=(3, 4)))                            # two teeth cut away: the others still sit on the steps of one division
        self.assertIn('14 equal places', self.profile(gone)[0]['text']); self.assertIn('(2 steps of the division hold no such place)', self.profile(gone)[0]['text'])
        self.assertEqual(count(self.wheel(drop=(0, 1, 2, 3, 4))), (0, 12))            # eleven are left: under twelve, a row each (and the ledge)
        self.assertEqual(count(self.wheel(change={1050: 0.24})), (1, 2))              # a width 0.04 off is the same place
        odd = judged(self.WHEEL, self.wheel(change={1050: 0.05}))                     # one tooth differs by 0.15: a row of its own beside the profile of 15
        self.assertEqual((len(self.profile(odd)), len(odd)), (1, 3)); self.assertIn('15 equal places', self.profile(odd)[0]['text'])
        self.assertEqual(count(self.wheel(shift={7: (0.0, 0.0, 0.3)})), (0, 17))      # one place off the circle: no profile is claimed, every place is a row
        self.assertEqual(count(self.wheel(shift={k: (0.0, 0.3 * k, 0.0) for k in range(16)})), (0, 17))      # equal places on a helix are no circle

    def test_a_waiver_of_a_profile_covers_the_profile_and_nothing_else(self):
        first = judged(self.WHEEL)
        row, = self.profile(first)
        w = {'id': 'W-1', 'rule': row['rule'], 'part': row['part'], 'anchor': row['anchor'], 'context': row['context'],
             'reason': 'the tooth roots are the standard belt profile and print clean at this pitch', 'quote': 'the teeth are fine, leave them', 'date': '2026-03-04'}
        open_, waived, rows = gate_features.apply_waivers(first, [w])
        self.assertEqual((len(open_), len(waived), rows[0]['state']), (1, 1, 'valid'))                  # the ledge stays open: another anchor
        self.assertNotIn('equal places', open_[0]['text'])
        f = self.wheel()
        f['faces'].append({'id': 90, 'anchor': {'kind': 'face', 'axis': [0.0, 1.0, 0.0], 'point': [0.0, 10.0, 50.0]}, 'width': 0.3, 'area': 1.0, 'shape': 'strip',
                           'sides': [[6.0, 90.0, 91], [6.0, -90.0, 92]]})                               # a new sliver right on the axis, looking along it
        later = judged(self.WHEEL, f)
        open_, waived, rows = gate_features.apply_waivers(later, [w])
        self.assertEqual((len(waived), rows[0]['state']), (1, 'valid'))
        self.assertEqual(sorted(x['measure'] for x in open_), [0.3, 0.4])                               # the new defect is open: same place, another context
        changed = judged(self.WHEEL, self.wheel(change={1050: 0.05}))                                   # one tooth changes: the profile is not what was waived
        open_, waived, rows = gate_features.apply_waivers(changed, [w])
        self.assertEqual((len(waived), rows[0]['state'], len(open_)), (0, 'expired', 3))
        moved = self.wheel()
        for x in moved['faces']:
            x['anchor']['point'][1] += 0.5                                                              # the whole part recut 0.5 along the axis: the same profile
        open_, waived, rows = gate_features.apply_waivers(judged(self.WHEEL, moved), [w])
        self.assertEqual((len(waived), rows[0]['state']), (1, 'valid'))


    # -- and none of the new facts can make a judge raise

    def test_odd_values_in_the_facts_of_these_cases_do_not_raise(self):
        odd = [None, 'x', True, float('nan'), [], {}, -1, [[1]], {'a': {}}, [None, None], 10 ** 400]
        for name in sorted(n for n in FX if '__live2_' in n):
            fx = FX[name]
            judge, params, ctx = fx['judge'], params_of(fx), ctx_of(fx['judge'], fx)
            facts = facts_of(name)
            if len(facts.get('faces') or []) > 4:
                facts['faces'] = facts['faces'][:3] + facts['faces'][-1:]             # one tooth and the ledge: every key is there
            for key in ('screws', 'bearings', 'openings'):
                if len(facts.get(key) or []) > 2:
                    facts[key] = facts[key][:1] + facts[key][-1:]
            for n in range(len(slots(facts))):
                for value in odd:
                    f2 = copy.deepcopy(facts)
                    container, key = slots(f2)[n]
                    container[key] = copy.deepcopy(value)
                    try:
                        out = gate_judges.JUDGES[judge](f2, params, ctx)
                    except Exception as e:
                        self.fail('%s raised %s: %s with %r at %r of %s' % (judge, type(e).__name__, e, value, key, name))
                    json.dumps(out, allow_nan=False)


class WaiversHoldOnTheFeature(unittest.TestCase):
    """Judges do not see waivers. They must give a finding the same id while its feature is the same, and another when the
    measured facts change, so that the caller's waiver holds and expires at the right moments."""

    def waiver(self, f):
        return {'id': 'W-1', 'rule': f['rule'], 'part': f['part'], 'anchor': f['anchor'], 'context': f['context'],
                'reason': 'the bearing seat stops the wall on that side and the pocket only holds a light cover',
                'quote': 'leave that one, the bearing is in the way', 'date': '2026-03-04'}

    def test_a_waiver_survives_a_recut_mouth_and_expires_when_the_wall_changes(self):
        name = 'inserts_wall__blocked_by_a_bearing_seat'
        first = judged(name)
        w = self.waiver(first[0])
        open_, waived, rows = gate_features.apply_waivers(first, [w])
        self.assertEqual((open_, len(waived), rows[0]['state']), ([], 1, 'valid'))
        f = facts_of(name)
        f['pockets'][0]['anchor']['point'][2] -= 0.54                                 # a relief was cut: the mouth moved down
        open_, waived, rows = gate_features.apply_waivers(judged(name, f), [w])
        self.assertEqual((open_, rows[0]['state']), ([], 'valid'))
        f['pockets'][0]['walls']['lead_in']['min'] = 2.2                              # the wall got thinner: the owner decides again
        open_, waived, rows = gate_features.apply_waivers(judged(name, f), [w])
        self.assertEqual((len(open_), rows[0]['state']), (1, 'expired'))
        f = facts_of(name)
        f['pockets'][0]['walls']['lead_in']['blocked_by'][0]['name'] = 'ref bearing 6205'      # another neighbour: again
        f['pockets'][0]['walls']['insert']['blocked_by'][0]['name'] = 'ref bearing 6205'
        self.assertEqual(gate_features.apply_waivers(judged(name, f), [w])[2][0]['state'], 'expired')

    def test_a_looser_or_tighter_parameter_changes_the_context_only_with_the_verdict(self):
        name = 'inserts_wall__blocked_by_a_bearing_seat'
        base = judged(name)[0]
        self.assertEqual(judged(name, params={'inserts.wall_target': 4.5})[0]['context'], base['context'])
        worse = judged(name, params={'inserts.wall_floor': 3.0})[0]                   # the same wall is now under the floor
        self.assertEqual(worse['verdict'], 'FAIL')
        self.assertNotEqual(worse['context'], base['context'])

    def test_measures_inside_the_rounding_of_a_context_keep_the_id(self):
        name = 'clearance_min_gap_moving__fail_0_5_to_a_turning_pulley'
        f = facts_of(name)
        f['gaps'][0]['gap'] = 0.51
        self.assertEqual(gate_features.finding_id(judged(name)[0]), gate_features.finding_id(judged(name, f)[0]))
        f['gaps'][0]['gap'] = 0.8
        self.assertNotEqual(gate_features.finding_id(judged(name)[0]), gate_features.finding_id(judged(name, f)[0]))


if __name__ == '__main__':
    unittest.main()
