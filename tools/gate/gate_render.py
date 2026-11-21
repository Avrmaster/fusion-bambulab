#!/usr/bin/env python3
"""Text generated from the registry: the rule blocks inside the skills, RULES.md for a project, `gate.py rules`.

    python3 tools/gate/gate_render.py [--check] [--rules DIR] [FILE ...]   the marked blocks of the plugin's skills
    python3 gate/tools/gate_render.py --project [--root DIR] [--check]     gate/RULES.md of a project

Skill text is never typed twice. A skill file carries markers and everything between them is generated:

    <!-- gate:rules inserts start -->
    ...generated, do not edit...
    <!-- gate:rules inserts end -->

The word after "rules" is a registry category. A test renders every marked block again and compares it byte for byte
with the file (tests/test_render.py), so a rule changed in the registry and not rendered fails the build.
`--check` writes nothing and exits 1 when a block (or gate/RULES.md) differs from its render; 4 on a broken registry.

In a statement {inserts.wall_target} is replaced by the value: the registry default in a skill ("4.0 mm"), the project's
value with its tag in a project ("4.5 mm [U owner 2031-02-03]")."""
import glob, os, re, sys

import gate_common, gate_params, gate_registry
from gate_common import GateError, EXIT_BLOCK, EXIT_INFRA, EXIT_PASS

BEGIN = '<!-- gate:%s start -->'
END = '<!-- gate:%s end -->'
_MARK = re.compile(r'^[ \t]*<!-- gate:([a-z][a-z0-9_]*(?: [a-z0-9_]+)*) (start|end) -->[ \t]*$', re.M)
_DEG = '\u00b0'
LEVEL_INTRO = (('block', 'Checked by the gate. A finding stops the stamp of the part.'),
               ('attest', 'Asked by the gate. Each is answered with evidence, never with a typed number.'),
               ('advice', 'Advice. No check exists yet: apply it by hand.'))
GENERATED_NOTE = ('<!-- Generated from rules/registry/%s by tools/gate/gate_render.py. Do not edit here: change the '
                  'registry, then render again. -->')
MORE_NOTE = ('The incident behind each rule, the evidence a question takes and the numbers of a project: '
             '`gate.py rules --md --category %s`, or `gate/RULES.md` in a gated project.')
PER_WORDS = {'part': 'once per changed part', 'finding': 'once per finding'}
EVIDENCE_WORDS = {'label': 'one of the answers below', 'shot': 'a screenshot taken after the change',
                  'param': 'a measured (M) or owner-set (U) parameter', 'na': 'not applicable, with the reason',
                  'waiver': "the owner's waiver"}


# ---- values -----------------------------------------------------------------------------------------------------------------

def _size_key(text):
    return [(0, float(t), '') if t[0].isdigit() else (1, 0.0, t) for t in re.findall(r'\d+(?:\.\d+)?|\D+', str(text))]


def _number(v, unit):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return ('`%s`' if unit == 'regex' else '"%s"') % v
    if unit == 'share':
        return '%g %%' % round(v * 100.0, 6)
    if unit == 'count':
        return '%d' % v if float(v).is_integer() else '%s' % v
    if unit == 'deg':
        return '%s%s' % (v, _DEG)
    return '%s %s' % (v, unit)


def value_text(entry, with_tags=False):
    """How a resolved parameter reads in a sentence: "4.0 mm", "50 %", "4", `(?i)bearing`; a parameter with per-size
    values lists them: "3.2 mm (M2 2.0, M3 3.2)". with_tags adds where the number comes from: "4.0 mm [U owner
    2026-03-04]"."""
    text = _number(entry['value'], entry['unit'])
    per = entry.get('per') or {}
    if per:
        text += ' (%s)' % ', '.join('%s %s' % (k, per[k]) for k in sorted(per, key=_size_key))
    if with_tags:
        limit = gate_params.limit_text(entry)
        text += limit[limit.index(' ['):]
    return text


def _fill(text, resolved, with_tags, strict, where):
    def one(m):
        name = m.group(1)
        if name not in resolved:
            if strict:
                raise GateError('%s: {%s} names no parameter' % (where, name))
            return m.group(0)
        return value_text(resolved[name], with_tags)
    return gate_registry.PLACEHOLDER_RE.sub(one, text or '')


def statement(rule, resolved, with_tags=False):
    """The rule's statement with every {parameter} replaced by its value and unit; with_tags adds the tag text
    (gate_params.limit_text). GateError for a placeholder that names no parameter."""
    return _fill(rule.get('statement'), resolved, with_tags, True, rule.get('id', 'rule'))


# ---- one rule, one category ---------------------------------------------------------------------------------------------------

def _sentence(text):
    text = ' '.join(str(text or '').split())
    return text if not text or text[-1] in '.!?:' else text + '.'


def _head(rule):
    bits = [rule['level']]
    if rule['level'] != 'advice':
        bits[0] += ' at %s' % rule['stage']
    if rule['level'] == 'attest':
        bits.append(PER_WORDS.get((rule.get('attest') or {}).get('per'), 'once'))
    if rule.get('deferred'):
        bits.append('deferred: registered, not asked or run yet')
    return '- **%s** (`%s`, %s).' % (_sentence(rule['title']).rstrip('.'), rule['id'], ', '.join(bits))


def rule_md(rule, resolved, with_tags=False, brief=False):
    """One rule as markdown: a bold title line with id and level, the statement, "Why:" and the numbered fix order;
    for an attest rule the question and the allowed evidence.

    brief (the form inside a skill): no "Why", no note, no "See also", no evidence list; an advice rule and a deferred
    rule are the title and the statement only. The full form is what gate/RULES.md and `gate.py rules --md` show."""
    first = [_head(rule), _sentence(statement(rule, resolved, with_tags))]
    if not brief:
        if rule.get('why'):
            first.append('Why: ' + _sentence(_fill(rule['why'], resolved, with_tags, False, rule['id'])))
        if rule.get('note'):
            first.append('Note: ' + _sentence(_fill(rule['note'], resolved, with_tags, False, rule['id'])))
        see = [x for x in rule.get('x_see') or [] if isinstance(x, str)]
        if see:
            first.append('See also: %s.' % ', '.join('`%s`' % x for x in see))
    lines = [' '.join(x for x in first if x)]
    if brief and (rule['level'] == 'advice' or rule.get('deferred')):
        return lines[0] + '\n'
    att = rule.get('attest')
    if att:
        lines.append('  - Question: "%s"' % ' '.join(_fill(att['question'], resolved, with_tags, False, rule['id']).split()))
        if not brief:
            lines.append('  - Evidence: %s.' % '; '.join(EVIDENCE_WORDS.get(e, e) for e in att.get('evidence') or []))
            if att.get('labels'):
                lines.append('  - Answers: %s' % ' | '.join('"%s"' % ' '.join(str(x).split()) for x in att['labels']))
    steps = rule.get('fix_order') or []
    if steps:
        lines.append('  - Fix, in this order:')
        lines += ['    %d. %s' % (i + 1, _sentence(_fill(s, resolved, with_tags, False, rule['id']))) for i, s in enumerate(steps)]
    return '\n'.join(lines) + '\n'


def category_block(reg, resolved, category, with_tags=False, brief=False):
    """The generated text for one category: its block rules, then attest, then advice, each by rule_md(), deferred rules
    marked. Ends with one newline. '' for a category with no rules."""
    if category not in reg['categories']:
        raise GateError('unknown category %r (known: %s)' % (category, ', '.join(c for c in gate_registry.CATEGORIES if c in reg['categories'])))
    parts = []
    for level, intro in LEVEL_INTRO:
        rules = gate_registry.rules(reg, level=level, category=category)
        if rules:
            parts.append(intro + '\n\n' + ''.join(rule_md(r, resolved, with_tags, brief) for r in rules))
    return '\n'.join(parts)


def _cell(text):
    return ' '.join(str(text).split()).replace('|', '\\|')


def _numbers_table(reg, resolved, category):
    names = sorted(reg['categories'][category]['params'])
    if not names:
        return ''
    rows = ['| parameter | value here | tag | from | registry default |', '|---|---|---|---|---|']
    for n in names:
        e, d = resolved[n], reg['params'][n]
        if e['from'] == 'project':
            src = 'project: %s' % e['source']
        elif e['from'] == 'formula':
            src = '= %s' % e['source']
        elif e['from'] == 'target':
            src = 'declared target'
        else:
            src = 'default: %s' % e['source']
        default = ('= %s' % d['formula']) if d.get('formula') else value_text(
            {'value': d['default'], 'unit': d['unit'], 'per': d.get('per')})
        rows.append('| `%s` | %s | %s | %s | %s |' % (n, _cell(value_text(e)), e['tag'], _cell(src), _cell(default)))
    return '\n'.join(rows) + '\n'


def rules_md(reg, resolved, category=None, with_tags=True, title='Rules'):
    """All categories (or one) as one markdown document: what gate/RULES.md holds."""
    if category is not None and category not in reg['categories']:
        raise GateError('unknown category %r (known: %s)' % (category, ', '.join(c for c in gate_registry.CATEGORIES if c in reg['categories'])))
    out = ['# %s' % title, '',
           'Generated from the rule registry and the parameters in force here. Do not edit this file: a number changes',
           'through `python3 gate/tools/gate.py owner param`, a rule in the registry of the plugin.', '',
           'Tags: %s.' % ', '.join('%s %s' % (t, gate_params.TAG_WORDS[t]) for t in gate_params.TAGS), '']
    for cat in ([category] if category else [c for c in gate_registry.CATEGORIES if c in reg['categories']]):
        block, table = category_block(reg, resolved, cat, with_tags), _numbers_table(reg, resolved, cat)
        if not block and not table:
            continue
        out += ['## %s' % cat, '']
        if block:
            out += [block.rstrip('\n'), '']
        if table:
            out += ['Numbers of `%s`:' % cat, '', table.rstrip('\n'), '']
    return '\n'.join(out).rstrip('\n') + '\n'


# ---- marked blocks ----------------------------------------------------------------------------------------------------------

def blocks_in(text):
    """Names of the marked blocks of a text, in order: ['rules inserts', 'rules walls']."""
    return [m.group(1) for m in _MARK.finditer(text) if m.group(2) == 'start']


def replace_block(text, name, block):
    """`text` with everything between the markers of `name` replaced by `block`. GateError when the markers are missing,
    doubled or out of order. The marker lines themselves stay."""
    starts = [m for m in _MARK.finditer(text) if m.group(1) == name and m.group(2) == 'start']
    ends = [m for m in _MARK.finditer(text) if m.group(1) == name and m.group(2) == 'end']
    if len(starts) != 1 or len(ends) != 1:
        raise GateError('block "%s": expected one start and one end marker, found %d and %d' % (name, len(starts), len(ends)))
    start, end = starts[0], ends[0]
    if end.start() < start.end():
        raise GateError('block "%s": the end marker comes before the start marker' % name)
    inner = [m for m in _MARK.finditer(text) if start.end() < m.start() < end.start()]
    if inner:
        raise GateError('block "%s": the marker of "%s" lies inside it' % (name, inner[0].group(1)))
    if block and not block.endswith('\n'):
        block += '\n'
    return text[:start.end()] + '\n' + block + text[end.start():]


def skill_block(reg, resolved, name):
    """The text between the markers of the block `name` ("rules <category>"): a note that it is generated, then the
    category's rules with the registry's default numbers."""
    kind, _, arg = name.partition(' ')
    if kind != 'rules' or not arg:
        raise GateError('block "%s": only "rules <category>" blocks exist' % name)
    body = category_block(reg, resolved, arg, brief=True)
    if body:
        body += '\n' + MORE_NOTE % arg + '\n'
    else:
        body = 'No rule is registered in this category yet.\n'
    return '%s\n\n%s\n' % (GENERATED_NOTE % gate_registry.file_of(arg), body)


def render_file(path, reg, resolved=None, write=False):
    """Render every marked block of one file again. Returns (new text, changed: bool); with write=True a changed file is
    written. resolved None = registry defaults (what a skill shows)."""
    with open(path, encoding='utf-8') as f:
        text = f.read()
    if resolved is None:
        resolved = gate_params.resolve(reg)
    new = text
    for name in blocks_in(text):
        try:
            new = replace_block(new, name, skill_block(reg, resolved, name))
        except GateError as e:
            raise GateError('%s: %s' % (path, e))
    stray = [m.group(1) for m in _MARK.finditer(text) if m.group(2) == 'end' and m.group(1) not in blocks_in(text)]
    if stray:
        raise GateError('%s: block "%s" has an end marker and no start marker' % (path, stray[0]))
    changed = new != text
    if changed and write:
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(new)
    return new, changed


def changed_blocks(path, reg, resolved=None):
    """Names of the marked blocks of a file whose text differs from the render."""
    with open(path, encoding='utf-8') as f:
        text = f.read()
    if resolved is None:
        resolved = gate_params.resolve(reg)
    return [name for name in blocks_in(text) if replace_block(text, name, skill_block(reg, resolved, name)) != text]


# ---- a project ----------------------------------------------------------------------------------------------------------------

def project_resolved(root, reg):
    """(resolved, targets): the parameters in force in a project. With one declared target its nozzle and line width are
    used; with none or several the registry's default target is, and `targets` lists what is declared."""
    project = gate_common.load_project(root)
    return (gate_params.resolve(reg, gate_params.load_overrides(root), gate_common.target_of(project)),
            project.get('targets') or {})


def _targets_md(root, reg, targets):
    if not targets:
        return ('\n## Declared targets\n\nNone. Rules that depend on the nozzle use the registry default until '
                '`gate/project.json` "targets" declares the nozzle, line width and material of each .3mf project.\n')
    overrides = gate_params.load_overrides(root)
    by_target = [n for n in sorted(reg['params']) if reg['params'][n].get('formula')
                 and any(x.startswith('target.') for x in gate_params.formula_names(reg['params'][n]['formula']))]
    head = ['.3mf project', 'nozzle', 'line width', 'material'] + ['`%s`' % n for n in by_target]
    rows = ['| %s |' % ' | '.join(head), '|%s|' % '|'.join('---' for _ in head)]
    for key in sorted(targets):
        res = gate_params.resolve(reg, overrides, targets[key])
        cells = [key] + [_cell(value_text(res['target.' + k])) for k in ('nozzle', 'line_width', 'material')] + [_cell(value_text(res[n])) for n in by_target]
        rows.append('| %s |' % ' | '.join(cells))
    note = ('The statements above use the registry default target, because several targets are declared. '
            if len(targets) > 1 else '')
    return '\n## Declared targets\n\n%sA part is judged with the target of the .3mf project it is exported for.\n\n%s\n' % (note, '\n'.join(rows))


def project_rules_text(root, reg, resolved=None):
    """The text gate/RULES.md should hold now."""
    res, targets = project_resolved(root, reg)
    return rules_md(reg, resolved or res, title='Rules of this project') + _targets_md(root, reg, targets)


def write_project_rules(root, reg, resolved):
    """Write gate/RULES.md (rules_md with the project's values and tags). Returns the path."""
    path = gate_common.gate_path(root, 'RULES.md')
    text = project_rules_text(root, reg, resolved)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = '%s.tmp%d' % (path, os.getpid())
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    os.replace(tmp, path)
    return path


def project_rules_state(root, reg):
    """'current' | 'missing' | 'out of date': gate/RULES.md against its render."""
    path = gate_common.gate_path(root, 'RULES.md')
    if not os.path.isfile(path):
        return 'missing'
    with open(path, encoding='utf-8') as f:
        return 'current' if f.read() == project_rules_text(root, reg) else 'out of date'


# ---- commands -----------------------------------------------------------------------------------------------------------------

def cmd_rules(root, args):
    """gate.py rules [--md] [--category C]. root may be None (not a gated folder): registry defaults are shown.
    Prints the effective rules: level, id, title, statement with this project's numbers, fix order; args.md: as
    markdown (the content of gate/RULES.md). Returns 0; 4 for an unknown category or a parameter error."""
    reg = gate_registry.load_default(root)
    category = getattr(args, 'category', None)
    if category is not None and category not in reg['categories']:
        raise GateError('unknown category %r (known: %s)' % (category, ', '.join(c for c in gate_registry.CATEGORIES if c in reg['categories'])))
    resolved = project_resolved(root, reg)[0] if root else gate_params.resolve(reg)
    if getattr(args, 'md', False):
        sys.stdout.write(rules_md(reg, resolved, category, title='Rules of this project' if root else 'Rules'))
        return EXIT_PASS
    for cat in ([category] if category else [c for c in gate_registry.CATEGORIES if c in reg['categories']]):
        rules = gate_registry.rules(reg, category=cat)
        if not rules:
            continue
        print(cat)
        for level in gate_registry.LEVELS:
            for r in rules:
                if r['level'] != level:
                    continue
                print('  %-7s %s  %s%s' % (level, r['id'], r['title'], '  (deferred)' if r.get('deferred') else ''))
                print('          %s' % statement(r, resolved, True))
                for i, step in enumerate(r.get('fix_order') or []):
                    print('          fix %d: %s' % (i + 1, _fill(step, resolved, True, False, r['id'])))
    print('%d rules; numbers of %s' % (len(reg['rules']) if not category else len(gate_registry.rules(reg, category=category)),
                                       'this project (gate/owner/params.json)' if root else 'the registry defaults'))
    return EXIT_PASS


def _skill_files():
    """Every skills/*/SKILL.md of the plugin this copy lies in that carries a gate marker."""
    base = os.path.normpath(os.path.join(gate_common.tools_dir(), '..', '..', 'skills'))
    out = []
    for path in sorted(glob.glob(os.path.join(base, '*', 'SKILL.md'))):
        with open(path, encoding='utf-8') as f:
            if _MARK.search(f.read()):
                out.append(path)
    return out


def main(argv=None):
    """The command line at the top of this file. Returns 0, 1 (--check found a difference) or 4."""
    argv = list(sys.argv[1:] if argv is None else argv)
    check = project = False
    rules_dir = root = None
    files = []
    try:
        while argv:
            a = argv.pop(0)
            if a == '--check':
                check = True
            elif a == '--project':
                project = True
            elif a in ('--rules', '--root'):
                if not argv:
                    raise GateError('%s needs a folder' % a)
                if a == '--rules':
                    rules_dir = argv.pop(0)
                else:
                    root = argv.pop(0)
            elif a.startswith('-'):
                raise GateError('unknown option %s (see the top of gate_render.py)' % a)
            else:
                files.append(a)
        reg = gate_registry.load(rules_dir) if rules_dir else gate_registry.load()
        if project:
            root = os.path.abspath(root) if root else gate_common.find_root(os.getcwd())
            if not root or not os.path.isfile(gate_common.gate_path(root, 'project.json')):
                raise GateError('no gate/project.json at or above %s: this folder is not gated' % (root or os.getcwd()))
            if check:
                state = project_rules_state(root, reg)
                print('gate/RULES.md is %s' % state)
                return EXIT_PASS if state == 'current' else EXIT_BLOCK
            print('wrote %s' % write_project_rules(root, reg, None))
            return EXIT_PASS
        files = files or _skill_files()
        if not files:
            raise GateError('nothing to render: name the files, or use --project in a gated project')
        differ = 0
        for path in files:
            if check:
                names = changed_blocks(path, reg)
                for name in names:
                    print('DIFFERS  %s  block "%s"' % (path, name))
                differ += len(names)
            else:
                _, changed = render_file(path, reg, write=True)
                print('%-9s %s' % ('rendered' if changed else 'current', path))
        if check:
            print('render check: %d block(s) differ from the registry%s' % (differ, '. Run tools/gate/gate_render.py' if differ else ''))
            return EXIT_BLOCK if differ else EXIT_PASS
        return EXIT_PASS
    except GateError as e:
        sys.stderr.write('GATE ERROR  %s\n' % e)
        return EXIT_INFRA


if __name__ == '__main__':
    sys.exit(main())
