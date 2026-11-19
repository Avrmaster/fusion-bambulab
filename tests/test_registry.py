"""The rule registry: the shipped files load, and every kind of mistake in a registry file is caught. No Fusion."""
import copy, inspect, json, os, shutil, sys, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
import gate_registry, gate_judges
from gate_registry import RegistryError

PARAM = {'default': 4.0, 'unit': 'mm', 'looser': 'lower', 'measured_on': 'a test bench', 'class': 'limit', 'formula': None}
RULE = {'id': 'inserts.wall', 'title': 'Wall', 'statement': 'At least {inserts.wall_target}.', 'why': 'It bulged.',
        'params': ['inserts.wall_target'], 'check': {'probe': 'fusion_inserts', 'judge': 'inserts_wall', 'needs': 'fusion'},
        'level': 'block', 'stage': 'design', 'fix_order': ['Widen the wall.'], 'attest': None}


def write(path, text):
    with open(path, 'w', encoding='utf-8') as f: f.write(text)


def read(path):
    with open(path, encoding='utf-8') as f: return f.read()


ATTEST = {'id': 'print.bed_face', 'title': 'Bed face', 'statement': 'Name the bed face.', 'why': 'A seat sagged.', 'params': [],
          'check': None, 'level': 'attest', 'stage': 'export', 'fix_order': [],
          'attest': {'question': 'Which face is on the bed?', 'evidence': ['label', 'shot'], 'per': 'part', 'labels': ['the bottom']}}


class Tmp(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, self.d)
        os.makedirs(os.path.join(self.d, 'registry')); os.makedirs(os.path.join(self.d, 'tables'))

    def put(self, category, params=None, rules=None, name=None):
        with open(os.path.join(self.d, 'registry', name or gate_registry.file_of(category)), 'w') as f:
            json.dump({'category': category, 'params': params or {}, 'rules': rules or []}, f)

    def base(self):
        self.put('inserts', {'inserts.wall_target': dict(PARAM)}, [copy.deepcopy(RULE)])

    def load(self, **kw):
        return gate_registry.load(self.d, complete=False, **kw)

    def errors(self, **kw):
        with self.assertRaises(RegistryError) as c:
            self.load(**kw)
        return '\n'.join(c.exception.errors)


class Shipped(unittest.TestCase):
    """The registry in rules/ as it ships."""

    @classmethod
    def setUpClass(cls):
        cls.reg = gate_registry.load(judges=set(gate_judges.JUDGES))

    def test_every_category_and_table_has_its_file(self):
        self.assertEqual(sorted(self.reg['categories']), sorted(gate_registry.CATEGORIES))
        self.assertEqual(sorted(self.reg['tables']), sorted(gate_registry.TABLES))

    def test_load_default_checks_the_judges(self):
        self.assertEqual(gate_registry.load_default()['sha256'], self.reg['sha256'])

    def test_block_rules_of_the_build_spec_are_all_there(self):
        want = '''inserts.pocket_shape inserts.wall inserts.wall_at_relief inserts.floor inserts.relief_missing inserts.mouth_reachable
                  bearings.holding bearings.clamp_room bearings.seat walls.min_material walls.slit walls.one_line walls.thin_faces
                  access.hole_covered access.screw_path access.mouth_sealed access.hole_closed
                  screws.engagement screws.under_head screws.tip_bottoming screws.hole_through screws.shell_bears washers.landing washers.proud clearance.min_gap_moving
                  hardware.recognised
                  mesh.closed mesh.one_shell project.stl_without_stamp project.stl_without_object project.object_on_no_plate
                  project.object_not_printable project.mesh_unstamped project.mesh_stale project.build_table_differs
                  project.target_differs regression.unintended regression.feature_lost
                  project.settings_match project.settings_changed_by_write fits.bought_part_pocket print.z_layers'''.split()
        have = [r['id'] for r in gate_registry.rules(self.reg, level='block')]
        self.assertEqual(sorted(have), sorted(want))

    def test_every_judge_a_rule_names_is_a_function(self):
        for name in gate_registry.judge_names(self.reg):
            self.assertTrue(callable(gate_judges.JUDGES.get(name)), name)

    def test_no_judge_without_a_rule(self):
        self.assertEqual(sorted(gate_judges.JUDGES), gate_registry.judge_names(self.reg))

    def test_a_judge_that_reads_the_pockets_table_is_held_to_list_its_parameters(self):
        # a judge only gets the parameters its rule lists: one that reaches _bought_pocket without them knows no pocket, in silence
        readers = set(gate_registry.POCKET_RULES + gate_registry.POCKET_READERS)
        for rid, rule in sorted(self.reg['rules'].items()):
            name = (rule.get('check') or {}).get('judge')
            if name in gate_judges.JUDGES and any('def _bought_pocket(' in s for s in gate_judges._judge_sources(gate_judges.JUDGES[name])):
                self.assertIn(rid, readers, 'rule %s reads the pockets table: name it in gate_registry.POCKET_READERS' % rid)

    def test_a_built_judge_has_a_fixture(self):
        built = [n for n in gate_judges.JUDGES if gate_judges.is_built(n)]
        missing = gate_registry.missing_fixtures(self.reg, os.path.join(ROOT, 'tests', 'fixtures', 'facts'), built)
        self.assertEqual(missing, [], 'judges that are built and have no tests/fixtures/facts/<judge>__<case>.json')

    def test_ask_targets_and_first_attest_set_exist(self):
        for rid in ('census.unknown_hole', 'assembly.order', 'regression.intended', 'generated.source', 'process.visual_check',
                    'process.concept_geometry', 'hardware.bought_from_step', 'fits.decided_not_printed', 'print.bed_face'):
            self.assertEqual(gate_registry.rule(self.reg, rid)['level'], 'attest', rid)

    def test_wall_limit_is_a_formula_over_the_target(self):
        d = gate_registry.param_def(self.reg, 'walls.min_wall_effective')
        self.assertEqual(d['formula'], 'max(walls.min_wall, 2 * target.line_width)')
        self.assertEqual(gate_registry.param_def(self.reg, 'target.line_width')['from'], 'target')

    def test_no_default_is_tagged_as_an_owner_decision(self):
        for name, d in self.reg['params'].items():
            self.assertIn(d.get('tag', 'D'), ('M', 'D', 'S'), name)
            self.assertTrue(d['measured_on'].strip(), name)

    def test_no_project_detail_in_the_rules(self):
        text = ''
        for sub in ('registry', 'tables'):
            for f in sorted(os.listdir(os.path.join(ROOT, 'rules', sub))):
                text += read(os.path.join(ROOT, 'rules', sub, f))
        self.assertFalse([c for c in text if '\u0400' <= c <= '\u04ff'], 'Cyrillic in the registry')
        for bad in ('/' + 'Users/', '/' + 'home/'):
            self.assertFalse(bad in text, 'a home path in the registry')

    def test_filters(self):
        r = self.reg
        self.assertEqual({x['check']['probe'] for x in gate_registry.rules(r, stage='export', level='block')}, {'stl_walls', 'stl_mesh'})
        self.assertEqual({x['id'] for x in gate_registry.rules_for_probe(r, 'fusion_gap')}, {'clearance.min_gap_moving'})
        self.assertIn('fusion_census', gate_registry.probes(r, stage='design', needs='fusion'))
        self.assertEqual(gate_registry.rules(r, category='overhang'), [])
        self.assertTrue(all(x.get('deferred') for x in gate_registry.rules(r, deferred=True)))
        self.assertEqual(gate_registry.table(r, 'bearings')['sizes']['608'], {'d': 8, 'D': 22, 'B': 7})

    def test_applies_to(self):
        r = self.reg
        self.assertTrue(gate_registry.applies_to(gate_registry.rule(r, 'inserts.wall'), 'fusion'))
        self.assertFalse(gate_registry.applies_to(gate_registry.rule(r, 'inserts.wall'), 'generated'))
        self.assertTrue(gate_registry.applies_to(gate_registry.rule(r, 'walls.min_material'), 'generated'))
        self.assertFalse(gate_registry.applies_to(gate_registry.rule(r, 'generated.source'), 'fusion'))

    def test_unknown_names_are_errors(self):
        for fn, arg in ((gate_registry.rule, 'inserts.nope'), (gate_registry.param_def, 'inserts.nope'), (gate_registry.table, 'nope')):
            with self.assertRaises(gate_registry.GateError): fn(self.reg, arg)

    def test_every_fit_carries_what_it_was_printed_under(self):
        fits = {n: d for n, d in self.reg['params'].items() if d['class'] == 'fit' and not d.get('formula')}
        self.assertGreater(len(fits), 10)
        for name, d in fits.items():
            self.assertIn('measured_under', d, name)
            if d.get('tag', 'D') == 'M':
                self.assertEqual(sorted(d['measured_under']), sorted(gate_registry.MEASURED_UNDER), name)
            else:
                self.assertIsNone(d['measured_under'], '%s was never printed: nothing to say about a print' % name)
        self.assertEqual(self.reg['params']['fits.hole_compensation']['measured_under']['xy_hole_compensation'], 0.125)

    def test_the_fit_settings_are_declared_per_project(self):
        from probes import project_consistency
        keys = project_consistency.fit_keys(self.reg['params']['target.fit_settings']['default'])
        self.assertEqual(keys, list(project_consistency.FIT_SETTINGS))                 # the probe's fallback list is the registry default
        for k in keys:
            d = self.reg['params']['target.' + project_consistency.target_key(k)]
            self.assertEqual(d.get('from'), 'target', k)                               # each one is a value of the declared target
        self.assertNotIn('from', self.reg['params']['target.fit_settings'])

    def test_the_pockets_table_names_fit_parameters_the_rules_list(self):
        kinds = gate_registry.table(self.reg, 'pockets')['kinds']
        self.assertIn('magnet', kinds)
        for kind, row in kinds.items():
            for key in ('d', 'depth'):
                self.assertEqual(self.reg['params'][row[key]]['class'], 'fit', (kind, key))
                self.assertIn(row[key], self.reg['rules']['fits.bought_part_pocket']['params']); self.assertIn(row[key], self.reg['rules']['print.z_layers']['params'])
            self.assertIn(row['part_h'], self.reg['rules']['print.z_layers']['params'])

    def test_the_rules_of_the_settings_incident(self):
        want = {'project.settings_match': ('project', 'project_settings_match', '3mf'), 'project.settings_changed_by_write': ('project', 'project_settings_changed_by_write', '3mf'),
                'fits.bought_part_pocket': ('design', 'fits_bought_pocket', 'fusion'), 'print.z_layers': ('design', 'print_z_layers', 'fusion')}
        for rid, (stage, judge, needs) in want.items():
            r = gate_registry.rule(self.reg, rid)
            self.assertEqual((r['level'], r['stage'], r['check']['judge'], r['check']['needs']), ('block', stage, judge, needs), rid)
            self.assertTrue(r['fix_order'], rid)
        self.assertEqual(gate_registry.judge_name('fits.bought_part_pocket'), 'fits_bought_pocket')       # the one judge not named after the whole id
        self.assertEqual(gate_registry.judge_name('print.z_layers'), 'print_z_layers')
        self.assertIn('coupon', gate_registry.rule(self.reg, 'fits.bought_part_pocket')['fix_order'][0])   # the coupon comes first
        q = gate_registry.rule(self.reg, 'fits.pocket_content')
        self.assertEqual((q['level'], q['attest']['per'], q['attest']['evidence']), ('attest', 'finding', ['label', 'param', 'waiver']))

    def test_four_questions_are_asked_now_and_the_rest_stay_deferred(self):
        asked = {'access.hole_supports': 'export', 'access.key_swing': 'design', 'screws.count_load_joint': 'design', 'bearings.no_plastic_in_clamp': 'design'}
        for rid, stage in asked.items():
            r = gate_registry.rule(self.reg, rid)
            self.assertEqual((r['level'], r['stage'], r['attest']['per'], bool(r.get('deferred'))), ('attest', stage, 'part', False), rid)
            self.assertTrue(r['attest']['question'].endswith('?'), rid)
            self.assertIn('label', r['attest']['evidence'], rid); self.assertGreaterEqual(len(r['attest']['labels']), 2, rid)
            self.assertNotIn('Deferred', r.get('note', ''), rid)
        still = {r['id'] for r in gate_registry.rules(self.reg, deferred=True)}
        self.assertEqual(still, {'bearings.insertion_path', 'clearance.static_all_neighbours', 'clearance.sweep_full_travel', 'motors.plug_path', 'print.slice_support_growth',
                                 'screws.family_lengths', 'screws.head_recess', 'screws.head_seat_full_ring', 'sensors.board_and_magnet', 'strength.criteria',
                                 'switches.click_before_stop', 'symmetry.named_asymmetry', 'teeth.profile'})


class Validation(Tmp):
    def test_a_small_sound_registry_loads(self):
        self.base(); self.put('print', rules=[copy.deepcopy(ATTEST)])
        reg = self.load(judges={'inserts_wall'})
        self.assertEqual(sorted(reg['rules']), ['inserts.wall', 'print.bed_face'])
        self.assertEqual(reg['rules']['inserts.wall']['category'], 'inserts')
        self.assertEqual(reg['categories']['inserts'], {'file': 'inserts.json', 'rules': ['inserts.wall'], 'params': ['inserts.wall_target']})
        self.assertEqual(len(reg['sha256']), 64)

    def test_complete_wants_every_category_and_table(self):
        self.base()
        with self.assertRaises(RegistryError) as c: gate_registry.load(self.d)
        text = '\n'.join(c.exception.errors)
        self.assertIn('screws.json: the file of the category is missing', text); self.assertIn('tables/inserts.json', text)

    def test_duplicate_rule_id(self):
        self.base(); r = copy.deepcopy(RULE); self.put('walls', rules=[r])
        self.assertIn('rule inserts.wall: declared twice', self.errors())

    def test_parameter_declared_twice_across_files(self):
        self.base(); self.put('walls', {'inserts.wall_target': dict(PARAM)})
        e = self.errors()
        self.assertIn('parameter inserts.wall_target: declared twice (inserts.json and walls.json)', e)
        self.assertIn('its name must start with "walls."', e)

    def test_a_fit_parameter_says_what_it_was_printed_under(self):
        under = {'nozzle': 0.4, 'material': 'PETG', 'xy_hole_compensation': 0.1, 'layer_height': 0.2}
        fit = dict(PARAM, **{'class': 'fit'})
        self.put('inserts', {'inserts.wall_target': dict(fit)}, [copy.deepcopy(RULE)])
        self.assertIn('a fit parameter carries "measured_under"', self.errors())
        for value, tag, text in ((None, 'M', 'tag M needs "measured_under"'), ({'nozzle': 0.4}, 'M', 'exactly nozzle, material, xy_hole_compensation, layer_height'),
                                 (dict(under, nozzle='0.4'), 'M', '"measured_under".nozzle must be a number or null'), (dict(under, material=5), 'D', '"measured_under".material must be text or null'),
                                 ('PETG', 'D', 'must be null or an object')):
            self.put('inserts', {'inserts.wall_target': dict(fit, measured_under=value, tag=tag)}, [copy.deepcopy(RULE)])
            with self.subTest(value=value): self.assertIn(text, self.errors())
        for value, tag in ((under, 'M'), (dict(under, layer_height=None), 'M'), (None, 'D'), (under, 'D')):
            self.put('inserts', {'inserts.wall_target': dict(fit, measured_under=value, tag=tag)}, [copy.deepcopy(RULE)])
            self.assertEqual(self.load()['params']['inserts.wall_target']['measured_under'], value)
        self.put('inserts', {'inserts.wall_target': dict(fit, measured_under=None), 'inserts.sum': dict(fit, default=None, formula='inserts.wall_target + 1', measured_under=under)}, [copy.deepcopy(RULE)])
        self.assertIn('a formula parameter has no "measured_under"', self.errors())

    def test_the_pockets_table_must_hold_together(self):
        fit = dict(PARAM, **{'class': 'fit', 'measured_under': None})
        rule = dict(copy.deepcopy(RULE), id='fits.bought_part_pocket', params=['fits.d'], check={'probe': 'fusion_census', 'judge': 'fits_bought_pocket', 'needs': 'fusion'})
        self.put('fits', {'fits.d': dict(fit), 'fits.depth': dict(fit), 'fits.h': dict(PARAM)}, [dict(rule, statement='A pocket.')])
        table = {'table': 'pockets', 'tol': {'d': 0.03}, 'depth_max_factor': 2.0, 'kinds': {'magnet': {'d': 'fits.d', 'depth': 'fits.depth', 'part_h': 'fits.h'}}}
        def with_table(t):
            write(os.path.join(self.d, 'tables', 'pockets.json'), json.dumps(t)); return self
        self.assertIn('rule fits.bought_part_pocket: reads the pockets table and must list fits.depth (kind magnet)', with_table(table).errors())
        self.put('fits', {'fits.d': dict(fit), 'fits.depth': dict(fit), 'fits.h': dict(PARAM)}, [dict(rule, statement='A pocket.', params=['fits.d', 'fits.depth'])])
        self.assertEqual(sorted(self.load()['tables']), ['pockets'])
        bad = copy.deepcopy(table); bad['kinds']['magnet']['d'] = 'fits.h'
        self.assertIn('"d" must name a fit parameter, fits.h is limit', with_table(bad).errors())
        bad = copy.deepcopy(table); bad['kinds']['pin'] = {'d': 'fits.nope', 'depth': 'fits.depth', 'part_h': 'fits.h'}
        self.assertIn('kind pin: parameter fits.nope is not declared', with_table(bad).errors())
        bad = copy.deepcopy(table); bad['kinds']['pin'] = {'d': 'fits.d'}
        self.assertIn('kind pin needs "d", "depth" and "part_h"', with_table(bad).errors())
        self.assertIn('needs "kinds" (object), "tol"', with_table({'table': 'pockets', 'kinds': {}}).errors())

    def test_rule_names_an_undeclared_parameter(self):
        r = copy.deepcopy(RULE); r['params'] = ['inserts.wall_target', 'inserts.missing']
        self.put('inserts', {'inserts.wall_target': dict(PARAM)}, [r])
        self.assertIn('rule inserts.wall: parameter inserts.missing is not declared in any file', self.errors())

    def test_statement_placeholder_must_be_in_params(self):
        r = copy.deepcopy(RULE); r['statement'] = 'At least {inserts.wall_floor}.'
        self.put('inserts', {'inserts.wall_target': dict(PARAM)}, [r])
        self.assertIn('{inserts.wall_floor}, which is not in its "params"', self.errors())

    def test_level_stage_needs_from_the_allowed_sets(self):
        for key, bad, msg in (('level', 'must', 'level must be one of'), ('stage', 'print', 'stage must be one of')):
            r = copy.deepcopy(RULE); r[key] = bad
            self.put('inserts', {'inserts.wall_target': dict(PARAM)}, [r])
            self.assertIn(msg, self.errors())
        r = copy.deepcopy(RULE); r['check']['needs'] = 'slicer'
        self.put('inserts', {'inserts.wall_target': dict(PARAM)}, [r])
        self.assertIn('needs must be one of', self.errors())
        r = copy.deepcopy(RULE); r['check']['probe'] = 'fusion_magic'
        self.put('inserts', {'inserts.wall_target': dict(PARAM)}, [r])
        self.assertIn("unknown probe 'fusion_magic'", self.errors())

    def test_block_rule_names_a_judge(self):
        for check in (None, {'probe': 'fusion_inserts', 'judge': None, 'needs': 'fusion'}):
            r = copy.deepcopy(RULE); r['check'] = check
            self.put('inserts', {'inserts.wall_target': dict(PARAM)}, [r])
            self.assertIn('a block rule names a probe and a judge', self.errors())

    def test_judge_is_named_after_the_rule(self):
        r = copy.deepcopy(RULE); r['check']['judge'] = 'wall_judge'
        self.put('inserts', {'inserts.wall_target': dict(PARAM)}, [r])
        self.assertIn('its judge must be named inserts_wall, not wall_judge', self.errors())

    def test_judge_must_exist_when_judges_are_given(self):
        self.base()
        self.assertIn('judge inserts_wall does not exist in gate_judges.py', self.errors(judges=set()))
        self.load(judges=None)

    def test_block_rule_cannot_be_deferred_or_carry_attest(self):
        r = copy.deepcopy(RULE); r['deferred'] = True; r['attest'] = ATTEST['attest']
        self.put('inserts', {'inserts.wall_target': dict(PARAM)}, [r])
        e = self.errors()
        self.assertIn('a block rule cannot be deferred', e); self.assertIn('a block rule has "attest": null', e)

    def test_attest_rule_needs_its_question_and_labels(self):
        a = copy.deepcopy(ATTEST); a['attest'] = None
        self.put('print', rules=[a]); self.assertIn('an attest rule needs an "attest" object', self.errors())
        a = copy.deepcopy(ATTEST); del a['attest']['labels']
        self.put('print', rules=[a]); self.assertIn('evidence "label" needs the fixed list attest.labels', self.errors())
        a = copy.deepcopy(ATTEST); a['attest']['evidence'] = ['number']
        self.put('print', rules=[a]); self.assertIn('attest.evidence must be a list out of', self.errors())
        a = copy.deepcopy(ATTEST); a['attest']['per'] = 'pocket'
        self.put('print', rules=[a]); self.assertIn('attest.per must be "part" or "finding"', self.errors())

    def test_advice_rule_has_no_judge(self):
        r = copy.deepcopy(RULE); r['level'] = 'advice'
        self.put('inserts', {'inserts.wall_target': dict(PARAM)}, [r])
        self.assertIn('an advice rule has no judge', self.errors())
        r['check'] = None
        self.put('inserts', {'inserts.wall_target': dict(PARAM)}, [r]); self.load()

    def test_unknown_and_missing_keys(self):
        r = copy.deepcopy(RULE); r['fix-order'] = []; del r['why']; r['x_note'] = 'kept'
        self.put('inserts', {'inserts.wall_target': dict(PARAM, colour='red')}, [r])
        e = self.errors()
        self.assertIn('rule inserts.wall: unknown key "fix-order"', e); self.assertIn('rule inserts.wall: missing key "why"', e)
        self.assertIn('parameter inserts.wall_target: unknown key "colour"', e); self.assertNotIn('x_note', e)

    def test_parameter_definition(self):
        cases = ((dict(PARAM, looser='down'), 'looser must be'), (dict(PARAM, **{'class': 'guess'}), 'class must be one of'),
                 (dict(PARAM, measured_on=' '), 'measured_on must say'), (dict(PARAM, tag='U'), 'never U'),
                 (dict(PARAM, default=True), 'default must be a number or text'), (dict(PARAM, default='x'), 'looser must be null'),
                 (dict(PARAM, per={'M3': 'big'}), '"per" must be an object of key -> number'),
                 (dict(PARAM, **{'from': 'target'}), '"from" may only be "target", in the category target'),
                 (dict(PARAM, formula='2 *'), 'expected a value'), (dict(PARAM, formula='inserts.wall_target * 2'), 'a formula parameter has "default": null'))
        for d, msg in cases:
            self.put('inserts', {'inserts.x': d})
            self.assertIn(msg, self.errors(), msg)

    def test_formula_names_and_loops(self):
        f = dict(PARAM, default=None)
        self.put('inserts', {'inserts.a': dict(f, formula='inserts.gone + 1')})
        self.assertIn('its formula names inserts.gone, which is not declared', self.errors())
        self.put('inserts', {'inserts.a': dict(f, formula='inserts.b + 1'), 'inserts.b': dict(f, formula='inserts.a * 2')})
        self.assertIn('formula loop', self.errors())

    def test_wrong_file_name_category_and_json(self):
        self.put('inserts', name='insert.json'); self.assertIn('insert.json: not a category file', self.errors())
        os.remove(os.path.join(self.d, 'registry', 'insert.json'))
        self.put('walls', name='inserts.json'); self.assertIn('inserts.json: "category" must be "inserts"', self.errors())
        write(os.path.join(self.d, 'registry', 'inserts.json'), '{not json')
        self.assertIn('inserts.json: not valid JSON', self.errors())

    def test_target_file_is_underscored(self):
        self.put('target', {'target.nozzle': dict(PARAM, looser=None, **{'from': 'target'})})
        self.assertTrue(os.path.exists(os.path.join(self.d, 'registry', '_target.json')))
        self.assertEqual(self.load()['params']['target.nozzle']['category'], 'target')

    def test_tables(self):
        self.base()
        write(os.path.join(self.d, 'tables', 'inserts.json'), json.dumps({'table': 'screws'}))
        self.assertIn('tables/inserts.json: "table" must be "inserts"', self.errors())
        write(os.path.join(self.d, 'tables', 'inserts.json'), json.dumps({'table': 'inserts', 'kinds': {}}))
        self.assertEqual(self.load()['tables']['inserts']['kinds'], {})
        write(os.path.join(self.d, 'tables', 'nuts.json'), json.dumps({'table': 'nuts'}))
        self.assertIn('tables/nuts.json: not a table', self.errors())

    def test_missing_fixtures(self):
        self.base(); reg = self.load(); fx = os.path.join(self.d, 'fx'); os.makedirs(fx)
        self.assertEqual(gate_registry.missing_fixtures(reg, fx), ['inserts_wall'])
        self.assertEqual(gate_registry.missing_fixtures(reg, fx, judges=[]), [])
        write(os.path.join(fx, 'inserts_wall__thin_free.json'), '{}')
        self.assertEqual(gate_registry.missing_fixtures(reg, fx), [])

    def test_error_message_lists_and_counts(self):
        r = copy.deepcopy(RULE); r['level'] = 'x'; r['stage'] = 'y'
        self.put('inserts', {}, [r])
        with self.assertRaises(RegistryError) as c: self.load()
        self.assertGreaterEqual(len(c.exception.errors), 3); self.assertIn('registry: %d error(s)' % len(c.exception.errors), str(c.exception))


if __name__ == '__main__':
    unittest.main()
