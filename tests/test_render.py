"""Text generated from the registry: the rule blocks in the skills equal their render, --check fails when they do not,
a project's RULES.md carries its own numbers. No Fusion, no Bambu Studio."""
import contextlib, glob, io, json, os, shutil, subprocess, sys, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(ROOT, 'tools', 'gate')
sys.path.insert(0, GATE)
import gate, gate_common, gate_params, gate_registry, gate_render
from gate_common import GateError

RENDER = os.path.join(GATE, 'gate_render.py')
SKILL = os.path.join(ROOT, 'skills', 'print-design', 'SKILL.md')
SKILL_BLOCKS = ['rules ' + c for c in ('inserts', 'screws', 'washers', 'bearings', 'walls', 'access', 'clearance', 'fits')]
PARAM = {'default': 4.0, 'unit': 'mm', 'looser': 'lower', 'measured_on': 'a test bench', 'class': 'limit', 'formula': None}


def read(path):
    with open(path, encoding='utf-8') as f: return f.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f: f.write(text)


def marked_files():
    out = []
    for path in sorted(glob.glob(os.path.join(ROOT, 'skills', '*', '*.md')) + glob.glob(os.path.join(ROOT, 'skills', '*', 'references', '*.md'))):
        if gate_render.blocks_in(read(path)): out.append(path)
    return out


class Shipped(unittest.TestCase):
    """Whatever the registry holds at test time: the files of the repository equal its render."""
    @classmethod
    def setUpClass(cls):
        cls.reg = gate_registry.load()
        cls.resolved = gate_params.resolve(cls.reg)

    def test_every_marked_block_equals_its_render(self):
        files = marked_files()
        self.assertIn(SKILL, files)
        for path in files:
            for name in gate_render.changed_blocks(path, self.reg):
                self.fail('%s: block "%s" differs from the registry. Run python3 tools/gate/gate_render.py' % (os.path.relpath(path, ROOT), name))
            new, changed = gate_render.render_file(path, self.reg)
            self.assertFalse(changed, path); self.assertEqual(new, read(path))

    def test_the_design_skill_has_the_eight_blocks(self):
        self.assertEqual(gate_render.blocks_in(read(SKILL)), SKILL_BLOCKS)

    def test_the_check_command_passes_on_the_repository(self):
        r = subprocess.run([sys.executable, RENDER, '--check'], capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stderr), (0, ''), r.stdout)
        self.assertIn('0 block(s) differ', r.stdout)

    def test_every_statement_of_the_registry_renders(self):
        for rid, rule in sorted(self.reg['rules'].items()):
            for tags in (False, True):
                text = gate_render.statement(rule, self.resolved, tags)
                self.assertFalse(gate_registry.PLACEHOLDER_RE.search(text), rid)
            md = gate_render.rule_md(rule, self.resolved)
            self.assertIn('`%s`' % rid, md); self.assertTrue(md.endswith('\n')); self.assertNotIn('\n\n', md)
        full = gate_render.rules_md(self.reg, self.resolved)
        for rid in self.reg['rules']: self.assertIn('`%s`' % rid, full)
        for name in self.reg['params']: self.assertIn('`%s`' % name, full)

    def test_a_category_with_parameters_and_no_rules_gets_no_rules_block(self):
        empty = [c for c, v in self.reg['categories'].items() if not v['rules']]
        for c in empty:
            self.assertEqual(gate_render.category_block(self.reg, self.resolved, c), '')
        full = gate_render.rules_md(self.reg, self.resolved)
        self.assertIn('## target', full)                        # its numbers are still listed
        self.assertNotIn('No rule is registered', full)

    def test_the_skill_form_is_shorter_than_the_full_form(self):
        brief = gate_render.category_block(self.reg, self.resolved, 'inserts', brief=True)
        full = gate_render.category_block(self.reg, self.resolved, 'inserts')
        self.assertLess(len(brief), len(full))
        self.assertNotIn('Why:', brief); self.assertIn('Why:', full)
        self.assertIn('Fix, in this order:', brief)             # the fix order is never left out

    def test_the_hand_written_wall_paragraph_states_the_owners_fix_order(self):
        text = read(SKILL)
        wall = text[text.index('- **Wall:**'):text.index('- **A missing relief blocks the export')]
        steps = ['make the whole wall or shoulder wider', 'Move the pocket together with its screw', 'A local boss only where the outline cannot move', 'Blocked on every side']
        places = [wall.index(s) for s in steps]
        self.assertEqual(places, sorted(places))
        for gone in ('3.8', 'boss passes', '\u00d812 boss', 'D12'):
            self.assertNotIn(gone, wall)
        self.assertNotIn('a wall under 3.8', text)
        self.assertIn('| straight \u00d84.4 | \u00d84.65 |', text)       # the measured pocket table stays

    def test_the_wall_rule_of_the_registry_carries_the_same_order(self):
        block = gate_render.category_block(self.reg, self.resolved, 'inserts', brief=True)
        rule = block[block.index('`inserts.wall`'):]
        rule = rule[:rule.index('\n- **')]
        order = [rule.index('1. '), rule.index('2. '), rule.index('3. '), rule.index('4. ')]
        self.assertEqual(order, sorted(order))
        self.assertIn('wider', rule.split('1. ')[1].split('2. ')[0])
        self.assertIn('boss', rule.split('3. ')[1].split('4. ')[0])


class Registry(unittest.TestCase):
    """A registry of our own in a temp folder, so that the texts are known."""
    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.d)
        self.rules = os.path.join(self.d, 'rules')
        shutil.copytree(os.path.join(ROOT, 'rules'), self.rules)

    def edit(self, category, fn):
        path = os.path.join(self.rules, 'registry', gate_registry.file_of(category))
        with open(path, encoding='utf-8') as f: data = json.load(f)
        fn(data)
        write(path, json.dumps(data, indent=1))

    def test_check_fails_after_a_registry_edit_and_render_repairs_it(self):
        skill = os.path.join(self.d, 'SKILL.md'); shutil.copy(SKILL, skill)
        run = lambda *a: subprocess.run([sys.executable, RENDER] + list(a), capture_output=True, text=True)
        self.assertEqual(run('--check', '--rules', self.rules, skill).returncode, 0)

        def harder(data): data['params']['inserts.wall_target']['default'] = 4.5
        self.edit('inserts', harder)
        reg = gate_registry.load(self.rules)
        self.assertEqual(gate_render.changed_blocks(skill, reg), ['rules inserts'])
        before = read(skill)
        r = run('--check', '--rules', self.rules, skill)
        self.assertEqual(r.returncode, 1); self.assertIn('block "rules inserts"', r.stdout); self.assertNotIn('rules walls', r.stdout)
        self.assertEqual(read(skill), before)                   # --check writes nothing
        r = run('--rules', self.rules, skill)
        self.assertEqual(r.returncode, 0); self.assertIn('rendered', r.stdout)
        self.assertIn('at least 4.5 mm', read(skill)); self.assertNotEqual(read(skill), before)
        self.assertEqual(run('--check', '--rules', self.rules, skill).returncode, 0)
        self.assertIn('current', run('--rules', self.rules, skill).stdout)

    def test_a_changed_text_a_new_rule_and_a_removed_rule_are_all_seen(self):
        skill = os.path.join(self.d, 'SKILL.md'); shutil.copy(SKILL, skill)

        def title(data): data['rules'][0]['title'] += ' (reworded)'
        self.edit('washers', title)
        self.assertEqual(gate_render.changed_blocks(skill, gate_registry.load(self.rules)), ['rules washers'])

        def add(data): data['rules'].append({'id': 'washers.test_only', 'title': 'A test rule', 'statement': 'Nothing.', 'why': 'A test.', 'params': [],
                                              'check': None, 'level': 'advice', 'stage': 'design', 'fix_order': [], 'attest': None})
        self.edit('washers', add)
        gate_render.render_file(skill, gate_registry.load(self.rules), write=True)
        self.assertIn('`washers.test_only`', read(skill))

        def drop(data): data['rules'] = [r for r in data['rules'] if r['id'] != 'washers.test_only']
        self.edit('washers', drop)
        self.assertEqual(gate_render.changed_blocks(skill, gate_registry.load(self.rules)), ['rules washers'])

    def test_a_broken_registry_is_exit_4_not_a_pass(self):
        def bad(data): data['rules'][0]['level'] = 'maybe'
        self.edit('inserts', bad)
        r = subprocess.run([sys.executable, RENDER, '--check', '--rules', self.rules, SKILL], capture_output=True, text=True)
        self.assertEqual(r.returncode, 4); self.assertIn('GATE ERROR', r.stderr)
        r = subprocess.run([sys.executable, RENDER, '--frobnicate'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 4)


class Values(unittest.TestCase):
    def entry(self, value, unit, per=None, tag='D', source='a test bench', frm='default'):
        return {'name': 'inserts.x', 'value': value, 'per': per, 'unit': unit, 'tag': tag, 'source': source, 'date': '2026-03-04', 'from': frm,
                'looser': 'lower', 'class': 'limit', 'default': value, 'inputs': []}

    def test_units(self):
        v = gate_render.value_text
        self.assertEqual(v(self.entry(4.0, 'mm')), '4.0 mm')
        self.assertEqual(v(self.entry(0.5, 'share')), '50 %')
        self.assertEqual(v(self.entry(0.02, 'share')), '2 %')
        self.assertEqual(v(self.entry(0.95, 'share')), '95 %')
        self.assertEqual(v(self.entry(4, 'count')), '4')
        self.assertEqual(v(self.entry(4.0, 'count')), '4')
        self.assertEqual(v(self.entry(45.0, 'deg')), '45.0\u00b0')
        self.assertEqual(v(self.entry('PETG', 'text')), '"PETG"')
        self.assertEqual(v(self.entry('(?i)screw|bolt', 'regex')), '`(?i)screw|bolt`')

    def test_per_size_values_are_listed_in_size_order(self):
        text = gate_render.value_text(self.entry(3.2, 'mm', per={'M10': 10.0, 'M2.5': 2.5, 'M3': 3.2, 'M2': 2.0}))
        self.assertEqual(text, '3.2 mm (M2 2.0, M2.5 2.5, M3 3.2, M10 10.0)')

    def test_tags_say_where_a_number_comes_from(self):
        self.assertEqual(gate_render.value_text(self.entry(4.5, 'mm', tag='U', source='owner 2026-03-04', frm='project'), True), '4.5 mm [U owner 2026-03-04]')
        self.assertEqual(gate_render.value_text(self.entry(4.0, 'mm'), True), '4.0 mm [D decided, not printed]')
        self.assertEqual(gate_render.value_text(self.entry(0.5, 'share'), True), '50 % [D decided, not printed]')

    def test_a_placeholder_that_names_no_parameter_is_an_error(self):
        rule = {'id': 'inserts.x', 'statement': 'At least {inserts.x} and {inserts.missing}.'}
        with self.assertRaises(GateError): gate_render.statement(rule, {'inserts.x': self.entry(4.0, 'mm')})
        self.assertEqual(gate_render.statement({'id': 'a.b', 'statement': 'At least {inserts.x}; {feature} stays.'}, {'inserts.x': self.entry(4.0, 'mm')}),
                         'At least 4.0 mm; {feature} stays.')


class Blocks(unittest.TestCase):
    TEXT = 'head\n<!-- gate:rules inserts start -->\nold\n<!-- gate:rules inserts end -->\nmiddle\n<!-- gate:rules walls start -->\n<!-- gate:rules walls end -->\ntail\n'

    def test_blocks_are_found_in_order(self):
        self.assertEqual(gate_render.blocks_in(self.TEXT), ['rules inserts', 'rules walls'])
        self.assertEqual(gate_render.blocks_in('no markers here\n<!-- an ordinary comment -->\n'), [])
        self.assertEqual(gate_render.BEGIN % 'rules inserts', '<!-- gate:rules inserts start -->')
        self.assertEqual(gate_render.END % 'rules inserts', '<!-- gate:rules inserts end -->')

    def test_replace_keeps_the_markers_and_everything_outside(self):
        new = gate_render.replace_block(self.TEXT, 'rules inserts', 'NEW\n')
        self.assertEqual(new, self.TEXT.replace('old\n', 'NEW\n'))
        new = gate_render.replace_block(self.TEXT, 'rules walls', 'W')            # a missing newline is added
        self.assertIn('<!-- gate:rules walls start -->\nW\n<!-- gate:rules walls end -->\ntail\n', new)
        self.assertEqual(gate_render.replace_block(new, 'rules walls', 'W\n'), new)      # rendering twice changes nothing

    def test_missing_doubled_and_crossed_markers_are_errors(self):
        r = gate_render.replace_block
        with self.assertRaises(GateError): r('nothing\n', 'rules inserts', 'x\n')
        with self.assertRaises(GateError): r(self.TEXT + self.TEXT, 'rules inserts', 'x\n')
        with self.assertRaises(GateError): r('<!-- gate:rules inserts end -->\n<!-- gate:rules inserts start -->\n', 'rules inserts', 'x\n')
        with self.assertRaises(GateError): r('<!-- gate:rules inserts start -->\n', 'rules inserts', 'x\n')
        with self.assertRaises(GateError): r('<!-- gate:rules inserts start -->\n<!-- gate:rules walls start -->\n<!-- gate:rules inserts end -->\n<!-- gate:rules walls end -->\n', 'rules inserts', 'x\n')

    def test_an_unknown_block_or_category_stops_the_render(self):
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d)
        reg = gate_registry.load()
        for body in ('<!-- gate:rules nosuch start -->\n<!-- gate:rules nosuch end -->\n', '<!-- gate:tables inserts start -->\n<!-- gate:tables inserts end -->\n',
                     'x\n<!-- gate:rules inserts end -->\n'):
            path = os.path.join(d, 'f.md'); write(path, body)
            with self.assertRaises(GateError): gate_render.render_file(path, reg)


class Project(unittest.TestCase):
    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.d)
        gate_common.write_json(gate_common.gate_path(self.d, 'project.json'),
                               {'mode': 'observe', 'targets': {'main': {'nozzle': 0.6, 'line_width': 0.62, 'material': 'PETG'}}})
        self.reg = gate_registry.load()

    def params(self, data):
        gate_common.write_json(gate_common.gate_path(self.d, 'owner', 'params.json'), data)

    def gate(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try: code = gate.main(['--root', self.d] + list(argv))
            except SystemExit as e: code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_rules_md_of_a_project_has_its_numbers_and_tags(self):
        self.params({'inserts.wall_target': {'value': 4.5, 'tag': 'U', 'source': 'owner 2026-03-04', 'date': '2026-03-04'}})
        path = gate_render.write_project_rules(self.d, self.reg, None)
        self.assertEqual(path, gate_common.gate_path(self.d, 'RULES.md'))
        text = read(path)
        self.assertIn('at least 4.5 mm [U owner 2026-03-04]', text)
        self.assertIn('| `inserts.wall_target` | 4.5 mm | U | project: owner 2026-03-04 | 4.0 mm |', text)
        self.assertIn('0.62 mm [declared target]', text)        # the line width of the declared target, not the default
        self.assertIn('## Declared targets', text); self.assertIn('| main | 0.6 mm | 0.62 mm | "PETG" |', text)
        self.assertIn('Why:', text)
        self.assertEqual(gate_render.project_rules_state(self.d, self.reg), 'current')

    def test_rules_md_goes_out_of_date_with_the_parameters(self):
        self.assertEqual(gate_render.project_rules_state(self.d, self.reg), 'missing')
        gate_render.write_project_rules(self.d, self.reg, None)
        self.assertEqual(gate_render.project_rules_state(self.d, self.reg), 'current')
        self.params({'inserts.wall_target': {'value': 5.0, 'tag': 'U', 'source': 'owner', 'date': '2026-03-05'}})
        self.assertEqual(gate_render.project_rules_state(self.d, self.reg), 'out of date')
        r = subprocess.run([sys.executable, RENDER, '--project', '--root', self.d, '--check'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1); self.assertIn('out of date', r.stdout)
        r = subprocess.run([sys.executable, RENDER, '--project', '--root', self.d], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(gate_render.project_rules_state(self.d, self.reg), 'current')

    def test_several_targets_are_listed_side_by_side(self):
        gate_common.write_json(gate_common.gate_path(self.d, 'project.json'),
                               {'mode': 'observe', 'targets': {'main': {'nozzle': 0.6, 'line_width': 0.62, 'material': 'PETG'},
                                                               'fine': {'nozzle': 0.4, 'line_width': 0.42, 'material': 'PLA'}}})
        text = gate_render.project_rules_text(self.d, self.reg)
        self.assertIn('| fine | 0.4 mm | 0.42 mm | "PLA" |', text); self.assertIn('| main | 0.6 mm | 0.62 mm | "PETG" |', text)
        self.assertIn('several targets are declared', text)

    def test_the_rules_command(self):
        self.params({'inserts.wall_target': {'value': 4.5, 'tag': 'U', 'source': 'owner 2026-03-04', 'date': '2026-03-04'}})
        code, out, err = self.gate('rules', '--category', 'inserts')
        self.assertEqual((code, err), (0, '')); self.assertIn('inserts.wall', out); self.assertIn('4.5 mm [U owner 2026-03-04]', out); self.assertNotIn('walls.min_material', out)
        code, out, err = self.gate('rules', '--md')
        self.assertEqual(code, 0); self.assertTrue(out.startswith('# Rules of this project')); self.assertIn('walls.min_material', out)
        code, out, err = self.gate('rules', '--category', 'nosuch')
        self.assertEqual(code, 4); self.assertIn('unknown category', err)
        self.params({'inserts.wall_target': {'value': 1.0, 'tag': 'D', 'source': 'agent', 'date': '2026-03-04'}})
        code, out, err = self.gate('rules')
        self.assertEqual(code, 4); self.assertIn('looser', err)  # a broken parameter file is never shown as rules

    def test_rules_without_a_project_shows_the_defaults(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = gate_render.cmd_rules(None, gate.parser().parse_args(['rules', '--category', 'washers']))
        self.assertEqual(code, 0); self.assertIn('registry defaults', out.getvalue())


class Clean(unittest.TestCase):
    def test_no_cyrillic_and_no_home_path_in_the_texts_this_package_ships(self):
        files = [os.path.join(ROOT, n) for n in ('README.md', 'AGENTS.md', 'CONTRIBUTING.md', 'CHANGELOG.md', 'INSTALL.md', 'tools/setup_project.py', 'hooks/hooks.json')]
        for pattern in ('skills/*/SKILL.md', 'templates/*.md', 'templates/gate/*.json'):
            files += glob.glob(os.path.join(ROOT, *pattern.split('/')))
        for path in files:
            text = read(path)
            self.assertFalse(any('\u0400' <= c <= '\u04ff' for c in text), 'Cyrillic in ' + path)
            self.assertFalse('/' + 'Users/' in text or '/' + 'home/' in text, 'a home path in ' + path)


if __name__ == '__main__':
    unittest.main()
