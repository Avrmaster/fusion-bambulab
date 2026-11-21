"""Parameters: registry default <- project file, tags, the looser-than-default rule, and the formula parser. No Fusion."""
import contextlib, io, json, os, shutil, sys, tempfile, types, unittest
for _k in ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID'): os.environ.pop(_k, None)      # gate.py owner from an agent session needs the hook's ticket: a test is no agent session
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'gate'))
import gate_params, gate_registry
from gate_params import ParamError, evaluate, formula_names


def P(default, looser='lower', unit='mm', formula=None, **kw):
    d = {'default': default, 'unit': unit, 'looser': looser, 'measured_on': 'a test bench', 'class': 'limit', 'formula': formula}
    d.update(kw)
    return d


def reg(**extra):
    params = {
        'inserts.wall_target': P(4.0), 'inserts.wall_floor': P(1.6),
        'washers.landing_gap_max': P(0.05, 'higher'),
        'screws.min_engagement': P(3.2, per={'M3': 3.2, 'M4': 4.0}),
        'bearings.ref_pattern': P('(?i)bearing', None, 'regex'),
        'walls.min_wall': P(1.5, tag='M'),
        'walls.min_wall_effective': P(None, formula='max(walls.min_wall, 2 * target.line_width)'),
        'target.nozzle': P(0.4, None, **{'from': 'target'}), 'target.line_width': P(0.42, None, **{'from': 'target'}),
        'target.material': P('PLA', None, 'text', **{'from': 'target'}),
    }
    params.update(extra)
    rules = {'inserts.wall': {'id': 'inserts.wall', 'params': ['inserts.wall_target', 'inserts.wall_floor']}}
    return {'params': params, 'rules': rules, 'tables': {}}


def O(value, tag='U', source='owner, chat', date='2026-03-04', **kw):
    return dict({'value': value, 'tag': tag, 'source': source, 'date': date}, **kw)


class Formula(unittest.TestCase):
    def test_arithmetic_and_precedence(self):
        self.assertEqual(evaluate('1 + 2 * 3', {}), 7.0)
        self.assertEqual(evaluate('(1 + 2) * 3', {}), 9.0)
        self.assertEqual(evaluate('10 / 4 - 0.5', {}), 2.0)
        self.assertEqual(evaluate('-2 * -3', {}), 6.0)
        self.assertEqual(evaluate('2 - -1', {}), 3.0)
        self.assertEqual(evaluate('8 / 2 / 2', {}), 2.0)
        self.assertEqual(evaluate('.5 + 1.', {}), 1.5)
        self.assertEqual(evaluate('1e1 + 2', {}), 12.0)

    def test_names_max_min(self):
        v = {'walls.min_wall': 1.5, 'target.line_width': 0.62}
        self.assertAlmostEqual(evaluate('max(walls.min_wall, 2 * target.line_width)', v), 1.5)
        v['target.line_width'] = 0.9
        self.assertAlmostEqual(evaluate('max(walls.min_wall, 2 * target.line_width)', v), 1.8)
        self.assertEqual(evaluate('min(3, max(1, 2), 5)', {}), 2.0)
        self.assertEqual(evaluate('max(4)', {}), 4.0)
        self.assertEqual(evaluate('a.b', lambda n: 7), 7.0)

    def test_formula_names(self):
        self.assertEqual(formula_names('max(walls.min_wall, 2 * target.line_width) + walls.min_wall'), ['target.line_width', 'walls.min_wall'])
        self.assertEqual(formula_names('1 + 2'), [])

    def test_bad_formulas_are_param_errors(self):
        for bad in ('', '  ', '2 *', '(1 + 2', '1 + 2)', '1 2', 'max()', 'max(1,)', 'pow(2, 3)', 'max', 'a..b', '2 ** 3', '1 // 2',
                    'a[0]', '1 if 1 else 2', 'a.b()', '1; 2', '"x"', 'x' * 500):
            with self.assertRaises(ParamError, msg=bad): evaluate(bad, lambda n: 1.0)

    def test_nothing_is_evaluated_as_python(self):
        for bad in ('__import__("os").system("true")', 'open("x")', '().__class__', 'lambda: 1', '[1][0]', 'a.b.__class__(1)'):
            with self.assertRaises(ParamError, msg=bad): evaluate(bad, lambda n: 1.0)

    def test_unknown_name_text_value_and_zero_division(self):
        with self.assertRaises(ParamError) as c: evaluate('a.b + 1', {})
        self.assertIn('unknown parameter a.b', str(c.exception))
        with self.assertRaises(ParamError): evaluate('a.b + 1', {'a.b': 'PLA'})
        with self.assertRaises(ParamError): evaluate('a.b + 1', {'a.b': True})
        with self.assertRaises(ParamError) as c: evaluate('1 / (2 - 2)', {})
        self.assertIn('division by zero', str(c.exception))

    def test_deep_nesting_is_refused_not_a_crash(self):
        with self.assertRaises(ParamError): evaluate('(' * 60 + '1' + ')' * 60, {})

    def test_formula_order_and_loop(self):
        f = lambda expr: P(None, formula=expr)
        defs = {'a.x': P(1.0), 'a.y': f('a.x + 1'), 'a.z': f('a.y * 2')}
        self.assertEqual(gate_params.formula_order(defs), ['a.y', 'a.z'])
        with self.assertRaises(ParamError) as c: gate_params.formula_order({'a.y': f('a.z'), 'a.z': f('a.y')})
        self.assertIn('formula loop', str(c.exception))
        with self.assertRaises(ParamError): gate_params.formula_order({'a.y': f('a.gone')})


class Resolve(unittest.TestCase):
    def test_defaults(self):
        r = gate_params.resolve(reg())
        e = r['inserts.wall_target']
        self.assertEqual((e['value'], e['tag'], e['from'], e['default'], e['date']), (4.0, 'D', 'default', 4.0, None))
        self.assertEqual(r['walls.min_wall']['tag'], 'M')
        self.assertEqual(r['screws.min_engagement']['per'], {'M3': 3.2, 'M4': 4.0})
        self.assertEqual(r['walls.min_wall_effective']['value'], 1.5)
        self.assertEqual(r['walls.min_wall_effective']['inputs'], ['target.line_width', 'walls.min_wall'])

    def test_project_value_replaces_the_default(self):
        r = gate_params.resolve(reg(), {'inserts.wall_target': O(4.5, 'D', 'agent, not printed')})
        e = r['inserts.wall_target']
        self.assertEqual((e['value'], e['tag'], e['from'], e['source'], e['date'], e['default']), (4.5, 'D', 'project', 'agent, not printed', '2026-03-04', 4.0))

    def test_unknown_name_is_an_error_with_a_hint(self):
        with self.assertRaises(ParamError) as c: gate_params.resolve(reg(), {'inserts.wall_targt': O(4.0)})
        self.assertIn('unknown parameter inserts.wall_targt (did you mean inserts.wall_target', str(c.exception))

    def test_looser_without_m_or_u_is_an_error(self):
        for tag in ('D', 'S'):
            errs = gate_params.check(reg(), {'inserts.wall_target': O(3.0, tag)})
            self.assertEqual(len(errs), 1); self.assertIn('is looser than the default 4.0 (lower is looser)', errs[0]); self.assertIn('it has ' + tag, errs[0])
        for tag in ('M', 'U'):
            self.assertEqual(gate_params.check(reg(), {'inserts.wall_target': O(3.0, tag)}), [])

    def test_stricter_or_equal_needs_no_special_tag(self):
        for v in (4.0, 5.0):
            self.assertEqual(gate_params.check(reg(), {'inserts.wall_target': O(v, 'D')}), [])

    def test_higher_is_looser(self):
        self.assertEqual(gate_params.check(reg(), {'washers.landing_gap_max': O(0.02, 'D')}), [])
        self.assertIn('higher is looser', gate_params.check(reg(), {'washers.landing_gap_max': O(0.1, 'S')})[0])
        self.assertEqual(gate_params.check(reg(), {'washers.landing_gap_max': O(0.1, 'M')}), [])

    def test_unordered_parameter_any_change_counts_as_looser(self):
        self.assertIn('any other value counts as looser', gate_params.check(reg(), {'bearings.ref_pattern': O('^brg ', 'D')})[0])
        self.assertEqual(gate_params.check(reg(), {'bearings.ref_pattern': O('^brg ', 'U')}), [])
        self.assertEqual(gate_params.check(reg(), {'bearings.ref_pattern': O('(?i)bearing', 'D')}), [])

    def test_tags(self):
        self.assertIn('tag must be one of M U D S', gate_params.check(reg(), {'inserts.wall_target': O(4.0, 'X')})[0])
        self.assertIn('tag must be one of M U D S', gate_params.check(reg(), {'inserts.wall_target': O(4.0, 'F')})[0])
        for t in gate_params.TAGS:
            self.assertEqual(gate_params.check(reg(), {'inserts.wall_target': O(4.0, t)}), [])

    def test_entry_shape(self):
        errs = '\n'.join(gate_params.check(reg(), {'inserts.wall_target': {'value': 4.0, 'tag': 'U', 'why': 'x'}, 'inserts.wall_floor': 2.0}))
        self.assertIn('inserts.wall_target: missing "source"', errs); self.assertIn('inserts.wall_target: missing "date"', errs)
        self.assertIn('inserts.wall_target: unknown key "why"', errs); self.assertIn('inserts.wall_floor: must be {"value"', errs)
        self.assertIn('"date" must be YYYY-MM-DD', gate_params.check(reg(), {'inserts.wall_target': O(4.0, date='4 March')})[0])
        self.assertIn('"source" must say', gate_params.check(reg(), {'inserts.wall_target': O(4.0, source=' ')})[0])
        self.assertEqual(gate_params.check(reg(), {'inserts.wall_target': O(4.0, note='n', quote='q')}), [])

    def test_type_must_match_the_default(self):
        self.assertIn('value must be a number', gate_params.check(reg(), {'inserts.wall_target': O('4')})[0])
        self.assertIn('value must be a number', gate_params.check(reg(), {'inserts.wall_target': O(True)})[0])
        self.assertIn('value must be text', gate_params.check(reg(), {'bearings.ref_pattern': O(5)})[0])

    def test_formula_and_target_parameters_cannot_be_overridden(self):
        self.assertIn('override its inputs', gate_params.check(reg(), {'walls.min_wall_effective': O(1.0)})[0])
        self.assertIn('gate/project.json', gate_params.check(reg(), {'target.nozzle': O(0.6)})[0])

    def test_per_size_values(self):
        r = gate_params.resolve(reg(), {'screws.min_engagement': O(3.2, 'D', per={'M4': 4.5, 'M5': 5.0})})
        self.assertEqual(r['screws.min_engagement']['per'], {'M3': 3.2, 'M4': 4.5, 'M5': 5.0})
        errs = gate_params.check(reg(), {'screws.min_engagement': O(3.2, 'D', per={'M4': 3.5})})
        self.assertIn('screws.min_engagement[M4] = 3.5 is looser than the default 4.0', errs[0])
        self.assertIn('screws.min_engagement[M5] = 3.0 is looser than the default 3.2', gate_params.check(reg(), {'screws.min_engagement': O(3.2, 'D', per={'M5': 3.0})})[0])
        self.assertEqual(gate_params.check(reg(), {'screws.min_engagement': O(3.2, 'U', per={'M4': 3.5})}), [])
        self.assertIn('"per" is not allowed', gate_params.check(reg(), {'inserts.wall_target': O(4.0, per={'M3': 4.0})})[0])
        self.assertIn('"per" must be an object', gate_params.check(reg(), {'screws.min_engagement': O(3.2, per={'M3': 'x'})})[0])

    def test_target_feeds_the_formula(self):
        r = gate_params.resolve(reg(), target={'nozzle': 0.8, 'line_width': 0.85, 'material': 'PETG'})
        self.assertEqual((r['target.line_width']['value'], r['target.line_width']['from']), (0.85, 'target'))
        self.assertAlmostEqual(r['walls.min_wall_effective']['value'], 1.7)
        self.assertEqual(r['walls.min_wall_effective']['from'], 'formula')
        self.assertEqual(r['target.material']['value'], 'PETG')
        self.assertEqual(gate_params.resolve(reg())['target.line_width']['from'], 'default')

    def test_target_mistakes(self):
        self.assertIn('unknown key "nozle"', gate_params.check(reg(), {}, {'nozle': 0.6})[0])
        self.assertIn('"nozzle" must be a number', gate_params.check(reg(), {}, {'nozzle': '0.6'})[0])
        self.assertIn('"material" must be text', gate_params.check(reg(), {}, {'material': 3})[0])

    def test_formula_tag_follows_its_inputs(self):
        self.assertEqual(gate_params.resolve(reg())['walls.min_wall_effective']['tag'], 'D')
        self.assertEqual(gate_params.resolve(reg(), target={'line_width': 0.62})['walls.min_wall_effective']['tag'], 'U')
        r = gate_params.resolve(reg(), {'walls.min_wall': O(2.0, 'S', 'simulated')}, {'line_width': 0.62})
        self.assertEqual(r['walls.min_wall_effective']['tag'], 'S'); self.assertEqual(r['walls.min_wall_effective']['value'], 2.0)

    def test_all_errors_are_collected(self):
        with self.assertRaises(ParamError) as c:
            gate_params.resolve(reg(), {'inserts.nope': O(1.0), 'inserts.wall_target': O(1.0, 'D'), 'inserts.wall_floor': O(1.0, 'Z')})
        self.assertEqual(len(c.exception.errors), 3)

    def test_values_and_value(self):
        r = gate_params.resolve(reg(), {'screws.min_engagement': O(3.2, 'D', per={'M5': 5.0})})
        v = gate_params.values(r)
        self.assertEqual(v['screws.min_engagement[M5]'], 5.0); self.assertEqual(v['inserts.wall_target'], 4.0)
        self.assertEqual(gate_params.value(v, 'screws.min_engagement', 'M4'), 4.0)
        self.assertEqual(gate_params.value(v, 'screws.min_engagement', 'M8'), 3.2)
        self.assertEqual(gate_params.value(v, 'screws.min_engagement'), 3.2)
        self.assertEqual(gate_params.value(r, 'screws.min_engagement', 'M5'), 5.0)
        self.assertEqual(gate_params.value(r, 'screws.min_engagement', 'M8'), 3.2)
        with self.assertRaises(ParamError): gate_params.value(v, 'screws.nope')
        only = gate_params.values(r, ['walls.min_wall_effective'])
        self.assertEqual(sorted(only), ['target.line_width', 'walls.min_wall', 'walls.min_wall_effective'])
        with self.assertRaises(ParamError): gate_params.values(r, ['x.y'])
        json.dumps(v)

    def test_params_hash_changes_with_value_and_tag_only_of_what_is_read(self):
        base = gate_params.resolve(reg())
        h = gate_params.params_hash(base, ['inserts.wall_target'])
        self.assertEqual(h, gate_params.params_hash(gate_params.resolve(reg(), {'inserts.wall_floor': O(2.0)}), ['inserts.wall_target']))
        self.assertNotEqual(h, gate_params.params_hash(gate_params.resolve(reg(), {'inserts.wall_target': O(4.5)}), ['inserts.wall_target']))
        self.assertNotEqual(h, gate_params.params_hash(gate_params.resolve(reg(), {'inserts.wall_target': O(4.0, 'U')}), ['inserts.wall_target']))
        hf = gate_params.params_hash(base, ['walls.min_wall_effective'])
        self.assertNotEqual(hf, gate_params.params_hash(gate_params.resolve(reg(), target={'line_width': 0.9}), ['walls.min_wall_effective']))

    def test_limit_text(self):
        r = gate_params.resolve(reg(), {'inserts.wall_target': O(4.0, 'U', 'owner', '2026-03-04'), 'inserts.wall_floor': O(2.0, 'D', 'agent 2026-03-04')}, {'line_width': 0.62})
        self.assertEqual(gate_params.limit_text(r['inserts.wall_target']), '4.0 mm [U owner 2026-03-04]')
        self.assertEqual(gate_params.limit_text(r['inserts.wall_floor']), '2.0 mm [D agent 2026-03-04]')
        self.assertEqual(gate_params.limit_text(r['washers.landing_gap_max']), '0.05 mm [D decided, not printed]')
        self.assertEqual(gate_params.limit_text(r['walls.min_wall']), '1.5 mm [M default measured on: a test bench]')
        self.assertEqual(gate_params.limit_text(r['target.line_width']), '0.62 mm [declared target]')
        self.assertEqual(gate_params.limit_text(r['screws.min_engagement'], 'M4'), '4.0 mm [D decided, not printed]')
        self.assertIn('= max(walls.min_wall, 2 * target.line_width)]', gate_params.limit_text(r['walls.min_wall_effective']))
        self.assertEqual(gate_params.limit_text(r['bearings.ref_pattern']), '(?i)bearing [D decided, not printed]')

    def test_explain(self):
        t = gate_params.explain(reg(), 'inserts.wall_target', {'inserts.wall_target': O(4.5)})
        self.assertIn('inserts.wall_target = 4.5 mm [U owner, chat 2026-03-04]', t); self.assertIn('registry default: 4.0 mm [D]', t)
        self.assertIn('a lower value weakens the rule', t); self.assertIn('rules: inserts.wall', t)
        t = gate_params.explain(reg(), 'walls.min_wall')
        self.assertIn('read by the formulas of: walls.min_wall_effective', t)
        self.assertIn('formula: max(walls.min_wall, 2 * target.line_width)', gate_params.explain(reg(), 'walls.min_wall_effective'))
        with self.assertRaises(ParamError): gate_params.explain(reg(), 'walls.nope')

    def test_is_looser(self):
        self.assertTrue(gate_params.is_looser(P(4.0), 3.9, 4.0)); self.assertFalse(gate_params.is_looser(P(4.0), 4.0, 4.0))
        self.assertFalse(gate_params.is_looser(P(4.0), 4.1, 4.0)); self.assertTrue(gate_params.is_looser(P(1, 'higher'), 2, 1))
        self.assertTrue(gate_params.is_looser(P('a', None), 'b', 'a')); self.assertFalse(gate_params.is_looser(P('a', None), 'a', 'a'))


class ProjectFile(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, self.d)
        os.makedirs(os.path.join(self.d, 'gate', 'owner'))
        self.put('project.json', {'mode': 'observe', 'targets': {'main': {'nozzle': 0.6, 'line_width': 0.62, 'material': 'PETG'}}})

    def put(self, name, data):
        with open(os.path.join(self.d, 'gate', name), 'w') as f: json.dump(data, f)

    def run_cmd(self, **kw):
        args = types.SimpleNamespace(action='check', name=None, target=None); args.__dict__.update(kw)
        out = io.StringIO()
        with contextlib.redirect_stdout(out): code = gate_params.cmd_params(self.d, args)
        return code, out.getvalue()

    def test_missing_file_is_no_overrides(self):
        self.assertEqual(gate_params.load_overrides(self.d), {})
        code, out = self.run_cmd(); self.assertEqual(code, 0); self.assertIn('TOTAL 0', out)

    def test_comment_keys_are_dropped(self):
        self.put('owner/params.json', {'_about': 'owner file', 'inserts.wall_target': O(4.0)})
        self.assertEqual(list(gate_params.load_overrides(self.d)), ['inserts.wall_target'])

    def test_check_on_the_shipped_registry(self):
        self.put('owner/params.json', {'inserts.wall_target': O(4.0), 'walls.min_wall': O(2.0, 'U', 'owner, a test pass')})
        code, out = self.run_cmd()
        self.assertEqual(code, 0); self.assertIn('inserts.wall_target', out); self.assertIn('[U owner, chat 2026-03-04]', out)

    def test_check_fails_with_exit_4(self):
        self.put('owner/params.json', {'inserts.wall_target': O(2.0, 'D', 'agent'), 'inserts.wal': O(1.0)})
        code, out = self.run_cmd()
        self.assertEqual(code, 4); self.assertIn('PARAM ERROR  unknown parameter inserts.wal', out)
        self.assertIn('is looser than the default', out); self.assertIn('TOTAL 2', out)

    def test_explain_uses_the_declared_target(self):
        code, out = self.run_cmd(action='explain', name='walls.min_wall_effective')
        self.assertEqual(code, 0); self.assertIn('target.line_width = 0.62 mm [declared target]', out)
        code, out = self.run_cmd(action='explain', name=None); self.assertEqual(code, 4)
        code, out = self.run_cmd(action='explain', name='walls.nope'); self.assertEqual(code, 4)

    def test_file_that_is_not_an_object(self):
        self.put('owner/params.json', [1, 2])
        with self.assertRaises(ParamError): gate_params.load_overrides(self.d)


UNDER = {'nozzle': 0.4, 'material': 'PETG', 'xy_hole_compensation': 0.125, 'layer_height': 0.3}
HERE = dict(UNDER, line_width=0.45)


class MeasuredUnder(unittest.TestCase):
    """A fit is measured only where it was printed: tag M holds for the declared target it was printed under, and is D anywhere else."""

    def reg(self, **more):
        extra = {'fits.pin': P(0.05, None, tag='M', measured_under=dict(UNDER), **{'class': 'fit'}),
                 'fits.peg': P(0.2, None, measured_under=None, **{'class': 'fit'}),
                 'fits.part_d': P(4.0, None, tag='M', **{'class': 'geometry'}),
                 'fits.pocket_d': P(None, None, formula='fits.part_d + fits.pin', **{'class': 'fit'}),
                 'target.xy_hole_compensation': P(0.0, None, **{'from': 'target'}), 'target.layer_height': P(0.2, None, **{'from': 'target'})}
        extra.update(more)
        return reg(**extra)

    def test_measured_here_names_every_difference(self):
        self.assertIsNone(gate_params.measured_here(UNDER, HERE))
        self.assertIsNone(gate_params.measured_here(dict(UNDER, material='petg'), dict(HERE, xy_hole_compensation=0.1252)))          # case and float noise
        self.assertEqual(gate_params.measured_here(UNDER, dict(HERE, xy_hole_compensation=0.0)), 'printed with xy_hole_compensation 0.125, this project is declared 0.0')
        self.assertEqual(gate_params.measured_here(UNDER, dict(HERE, material='PLA', nozzle=0.6)), 'printed with nozzle 0.4, this project is declared 0.6; printed in PETG, this project is declared PLA')
        self.assertIn('does not record layer_height', gate_params.measured_here(dict(UNDER, layer_height=None), HERE))
        self.assertIn('the declared target does not say layer_height', gate_params.measured_here(UNDER, {'nozzle': 0.4, 'material': 'PETG', 'xy_hole_compensation': 0.125}))
        self.assertIn('no declared target', gate_params.measured_here(UNDER, None))
        self.assertIn('does not say what it was printed with', gate_params.measured_here(None, HERE))

    def test_a_measured_default_is_m_only_under_its_own_settings(self):
        r = self.reg()
        here = gate_params.resolve(r, {}, HERE)['fits.pin']
        self.assertEqual((here['tag'], here['tag_given'], here['tag_why'], here['measured_under']), ('M', 'M', None, UNDER))
        for target, word in ((dict(HERE, xy_hole_compensation=0.0), 'xy_hole_compensation 0.125, this project is declared 0.0'), (dict(HERE, material='PLA'), 'printed in PETG'),
                             (dict(HERE, nozzle=0.6), 'nozzle 0.4'), (dict(HERE, layer_height=0.2), 'layer_height 0.3'), (None, 'no declared target'),
                             ({'nozzle': 0.4, 'line_width': 0.45, 'material': 'PETG'}, 'does not say xy_hole_compensation')):
            e = gate_params.resolve(r, {}, target)['fits.pin']
            with self.subTest(target=target):
                self.assertEqual((e['tag'], e['tag_given'], e['value']), ('D', 'M', 0.05)); self.assertIn(word, e['tag_why'])
                self.assertEqual(gate_params.limit_text(e), '0.05 mm [D here: measured, but not under the settings of this project (gate.py params explain fits.pin)]')
        never = gate_params.resolve(r, {}, HERE)['fits.peg']
        self.assertEqual((never['tag'], never['tag_given'], never['tag_why']), ('D', 'D', None))

    def test_a_project_value_carries_what_it_was_printed_under(self):
        r = self.reg()
        get = lambda o, target=HERE: gate_params.resolve(r, {'fits.peg': o}, target)['fits.peg']
        e = get(O(0.25, 'M', measured_under=dict(UNDER)))
        self.assertEqual((e['tag'], e['value'], e['measured_under'], e['from']), ('M', 0.25, UNDER, 'project'))
        e = get(O(0.25, 'M', measured_under=dict(UNDER)), dict(HERE, xy_hole_compensation=0.0))       # the owner changed the declared target: open again
        self.assertEqual((e['tag'], e['tag_given']), ('D', 'M')); self.assertIn('xy_hole_compensation', e['tag_why'])
        e = get(O(0.25, 'M'))                                                                           # measured, and nobody wrote down under what
        self.assertEqual(e['tag'], 'D'); self.assertIn('does not say what it was printed with', e['tag_why'])
        e = get(O(0.25, 'U'), None)                                                                     # the owner's decision is never changed
        self.assertEqual((e['tag'], e['tag_why']), ('U', None))
        self.assertEqual(gate_params.check(r, {'fits.peg': O(0.25, 'M', measured_under={'nozzle': 0.4})}, HERE),
                         ['fits.peg: "measured_under" must be null or an object with exactly nozzle, material, xy_hole_compensation, layer_height'])
        self.assertEqual(gate_params.check(r, {'fits.peg': O(0.25, 'M', measured_under=dict(UNDER, nozzle='big'))}, HERE), ['fits.peg: "measured_under".nozzle must be a number or null'])
        self.assertEqual(gate_params.check(r, {'inserts.wall_target': O(4.5, 'M', measured_under=dict(UNDER))}, HERE), [])        # a limit may record it too; only a fit depends on it

    def test_a_limit_measured_on_a_print_keeps_its_tag(self):
        self.assertEqual(gate_params.resolve(self.reg(), {}, None)['walls.min_wall']['tag'], 'M')           # class limit: not a fit, not tied to hole compensation

    def test_a_formula_over_a_fit_follows_it(self):
        r = self.reg()
        self.assertEqual(gate_params.resolve(r, {}, HERE)['fits.pocket_d']['tag'], 'M')
        e = gate_params.resolve(r, {}, dict(HERE, xy_hole_compensation=0.0))['fits.pocket_d']
        self.assertEqual((e['tag'], e['value']), ('D', 4.05)); self.assertTrue(e['tag_why'].startswith('fits.pin: measured (M), but not for this project'))

    def test_the_tag_of_a_fit_travels_with_its_value(self):
        r = self.reg()
        here, there = gate_params.resolve(r, {}, HERE), gate_params.resolve(r, {}, dict(HERE, xy_hole_compensation=0.0))
        v = gate_params.values(here, ['fits.pocket_d'])
        self.assertEqual(v, {'fits.part_d': 4.0, 'fits.pin': 0.05, 'fits.pin#tag': 'M', 'fits.pocket_d': 4.05, 'fits.pocket_d#tag': 'M'})
        self.assertEqual(gate_params.values(there, ['fits.pocket_d'])['fits.pocket_d#tag'], 'D')
        self.assertNotIn('inserts.wall_target#tag', gate_params.values(here))                              # only fits carry one
        self.assertNotEqual(gate_params.params_hash(here, ['fits.pocket_d']), gate_params.params_hash(there, ['fits.pocket_d']))
        self.assertEqual(gate_params.value(gate_params.values(here), 'fits.pin'), 0.05)

    def test_explain_and_check_say_why(self):
        r = self.reg()
        text = gate_params.explain(r, 'fits.pin', {}, dict(HERE, layer_height=0.2))
        self.assertIn('measured under: nozzle 0.4, material PETG, xy_hole_compensation 0.125, layer_height 0.3', text)
        self.assertIn('tag here: D. measured (M), but not for this project: printed with layer_height 0.3, this project is declared 0.2', text)

    def test_the_shipped_fits_are_not_measured_for_a_project_that_did_not_print_them(self):
        r = gate_registry.load()
        full = {'nozzle': 0.6, 'line_width': 0.62, 'material': 'PETG-CF', 'layer_height': 0.2, 'xy_hole_compensation': 0.125, 'xy_contour_compensation': 0.0,
                'elefant_foot_compensation': 0.15, 'wall_generator': 'classic'}
        self.assertEqual(gate_params.check(r, {}, full), [])                                                 # every key of the declared target is a parameter
        res = gate_params.resolve(r, {}, full)
        self.assertEqual((res['target.layer_height']['value'], res['target.xy_hole_compensation']['value'], res['target.wall_generator']['value']), (0.2, 0.125, 'classic'))
        for name, e in res.items():
            if e['class'] == 'fit':
                self.assertIn(e['tag'], ('D', 'S'), '%s: a registry default was printed on another machine' % name)
        self.assertEqual(res['walls.min_wall']['tag'], 'M')
        self.assertEqual(res['sensors.magnet_pocket_d']['value'], 4.2); self.assertEqual(gate_params.values(res, ['sensors.magnet_pocket_d'])['sensors.magnet_pocket_d#tag'], 'D')


class ShippedDefaults(unittest.TestCase):
    def test_every_shipped_parameter_resolves(self):
        r = gate_registry.load()
        res = gate_params.resolve(r, {}, {'nozzle': 0.6, 'line_width': 0.62, 'material': 'PETG'})
        self.assertEqual(sorted(res), sorted(r['params']))
        self.assertEqual(res['walls.min_wall_effective']['value'], 1.5)
        self.assertEqual(res['clearance.scan_margin']['value'], 5.0)
        self.assertEqual(gate_params.value(gate_params.values(res), 'screws.min_engagement', 'M4'), 4.0)
        for rule in r['rules'].values():
            gate_params.values(res, rule['params'])


if __name__ == '__main__':
    unittest.main()
