"""Resolve the numbers the rules use: registry default <- the project's gate/owner/params.json.

Project file (OWNER): {"inserts.wall_target": {"value": 4.5, "tag": "U", "source": "owner 2031-02-03", "date": "2031-02-03"}}
    tag     M measured on a print | U owner decision | D decided, not printed | S simulated
    per     optional {"M3": 3.0}: values per size, for a parameter whose definition has "per"
    measured_under   optional, for a value tagged M: what it was printed with,
            {"nozzle": 0.6, "material": "PETG", "xy_hole_compensation": 0.1, "layer_height": 0.2} (null = not recorded)
    note, quote   optional text; keys that start with "_" are comments

A fit is measured only where it was printed. A parameter of class "fit" tagged M (a registry default or a project value)
keeps that tag only for a project whose declared target says the same nozzle, material, X-Y hole compensation and layer
height as its "measured_under". For any other project, for a value that does not say what it was printed with, and when
no target is declared, it resolves with tag D and "tag_why" says the reason. U (the owner's decision) is never changed.

What is an error (the gate stops with exit 4 until it is fixed):
    an unknown parameter name; a tag outside M U D S; no source or date; text where a number belongs;
    a value looser than the registry default without tag M or U (the definition's "looser" says which way is looser;
    for a parameter that is not ordered every other value counts as looser);
    an override of a formula parameter (override its inputs) or of a target parameter (declare it in gate/project.json).

Formulas are a small expression over other parameters: names, numbers, + - * /, max( ), min( ), parentheses.
They are parsed by a recursive parser here. Nothing is ever passed to eval."""
import difflib, re
from gate_common import GateError, EXIT_INFRA, EXIT_PASS, digest, gate_path, load_project, read_json, target_of

TAGS = ('M', 'U', 'D', 'S')
TAG_WORDS = {'M': 'measured on a print', 'U': 'owner decision', 'D': 'decided, not printed', 'S': 'simulated'}
OVERRIDE_KEYS = ('value', 'tag', 'source', 'date')
OVERRIDE_OPTIONAL = ('per', 'note', 'quote', 'measured_under')
MEASURED_UNDER = ('nozzle', 'material', 'xy_hole_compensation', 'layer_height')     # what a fit was printed with
TAG_SUFFIX = '#tag'             # values(): the tag of a fit parameter travels as "<name>#tag"
FUNCTIONS = {'max': max, 'min': min}
_TOKEN = re.compile(r'\s*(?:(\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+)|([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)|(.))')
_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


class ParamError(GateError):
    """A parameter problem. `.errors` holds every line when several were found."""

    def __init__(self, message, errors=None):
        GateError.__init__(self, message)
        self.errors = list(errors) if errors else [message]


# ---- formulas -------------------------------------------------------------------------------------------------------------

def _tokens(expr):
    if not isinstance(expr, str) or not expr.strip():
        raise ParamError('formula is empty')
    if len(expr) > 400:
        raise ParamError('formula is longer than 400 characters')
    out, pos = [], 0
    while pos < len(expr):
        m = _TOKEN.match(expr, pos)
        if not m or m.end() == pos:
            break
        pos = m.end()
        if m.group(1) is not None:
            out.append(('num', float(m.group(1))))
        elif m.group(2) is not None:
            out.append(('name', m.group(2)))
        elif m.group(3) in '+-*/(),':
            out.append(('op', m.group(3)))
        else:
            raise ParamError('formula %r: unexpected character %r' % (expr, m.group(3)))
    if expr[pos:].strip():
        raise ParamError('formula %r: cannot read %r' % (expr, expr[pos:]))
    return out


class _Parser:
    """expr := term (('+' | '-') term)* ; term := unary (('*' | '/') unary)* ; unary := ('-' | '+') unary | atom ;
    atom := number | name | ('max' | 'min') '(' expr (',' expr)* ')' | '(' expr ')'"""

    def __init__(self, expr):
        self.expr, self.toks, self.i, self.depth = expr, _tokens(expr), 0, 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self, kind=None, value=None):
        k, v = self.peek()
        if k is None or (kind and k != kind) or (value is not None and v != value):
            raise ParamError('formula %r: expected %s, found %s' % (self.expr, value or kind or 'a value', 'the end' if k is None else repr(v)))
        self.i += 1
        return v

    def parse(self):
        node = self.expr_()
        if self.peek()[0] is not None:
            raise ParamError('formula %r: unexpected %r' % (self.expr, self.peek()[1]))
        return node

    def expr_(self):
        node = self.term()
        while self.peek() in (('op', '+'), ('op', '-')):
            node = ('bin', self.take(), node, self.term())
        return node

    def term(self):
        node = self.unary()
        while self.peek() in (('op', '*'), ('op', '/')):
            node = ('bin', self.take(), node, self.unary())
        return node

    def unary(self):
        if self.peek() in (('op', '-'), ('op', '+')):
            op = self.take()
            node = self.unary()
            return ('neg', node) if op == '-' else node
        return self.atom()

    def atom(self):
        k, v = self.peek()
        if k == 'num':
            self.i += 1
            return ('num', v)
        if k == 'name':
            self.i += 1
            if self.peek() == ('op', '('):
                if v not in FUNCTIONS:
                    raise ParamError('formula %r: %r is not a function (only max and min)' % (self.expr, v))
                self.take('op', '(')
                args = [self.nested()]
                while self.peek() == ('op', ','):
                    self.take()
                    args.append(self.nested())
                self.take('op', ')')
                return ('call', v, args)
            if v in FUNCTIONS:
                raise ParamError('formula %r: %s needs arguments in parentheses' % (self.expr, v))
            return ('name', v)
        if (k, v) == ('op', '('):
            self.take()
            node = self.nested()
            self.take('op', ')')
            return node
        raise ParamError('formula %r: expected a value, found %s' % (self.expr, 'the end' if k is None else repr(v)))

    def nested(self):
        self.depth += 1
        if self.depth > 40:
            raise ParamError('formula %r: nested too deep' % self.expr)
        node = self.expr_()
        self.depth -= 1
        return node


def parse(expr):
    """The formula as a tree of tuples: ('num', 2.0) | ('name', 'walls.min_wall') | ('neg', node) |
    ('bin', '+', a, b) | ('call', 'max', [nodes]). ParamError when the text is not a formula."""
    return _Parser(expr).parse()


def _names(node, out):
    if node[0] == 'name':
        out.add(node[1])
    elif node[0] == 'neg':
        _names(node[1], out)
    elif node[0] == 'bin':
        _names(node[2], out); _names(node[3], out)
    elif node[0] == 'call':
        for a in node[2]:
            _names(a, out)


def formula_names(expr):
    """Sorted parameter names a formula reads."""
    out = set()
    _names(parse(expr), out)
    return sorted(out)


def _eval(node, lookup, expr):
    kind = node[0]
    if kind == 'num':
        return node[1]
    if kind == 'name':
        v = lookup(node[1])
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ParamError('formula %r: %s is not a number' % (expr, node[1]))
        return float(v)
    if kind == 'neg':
        return -_eval(node[1], lookup, expr)
    if kind == 'call':
        return float(FUNCTIONS[node[1]](_eval(a, lookup, expr) for a in node[2]))
    a, b = _eval(node[2], lookup, expr), _eval(node[3], lookup, expr)
    if node[1] == '+': return a + b
    if node[1] == '-': return a - b
    if node[1] == '*': return a * b
    if b == 0:
        raise ParamError('formula %r: division by zero' % expr)
    return a / b


def evaluate(expr, lookup):
    """The number a formula gives. `lookup` is a dict {name: number} or a function name -> number.
    ParamError for bad syntax, an unknown name, a text value or a division by zero."""
    if isinstance(lookup, dict):
        table = lookup

        def lookup(name):
            if name not in table:
                raise ParamError('formula %r: unknown parameter %s' % (expr, name))
            return table[name]
    return _eval(parse(expr), lookup, expr)


def formula_order(defs):
    """Names of the formula parameters of `defs` ({name: definition}) in an order in which each one only reads parameters
    that are already known. ParamError for a loop or a name that is not declared."""
    order, state = [], {}

    def visit(name, trail):
        if state.get(name) == 'done':
            return
        if state.get(name) == 'open':
            raise ParamError('formula loop: %s' % ' -> '.join(trail + [name]))
        state[name] = 'open'
        for other in formula_names(defs[name]['formula']):
            if other not in defs:
                raise ParamError('parameter %s: its formula names %s, which is not declared' % (name, other))
            if defs[other].get('formula'):
                visit(other, trail + [name])
        state[name] = 'done'
        order.append(name)

    for n in sorted(defs):
        if defs[n].get('formula'):
            visit(n, [])
    return order


# ---- overrides ----------------------------------------------------------------------------------------------------------------

def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def is_looser(definition, value, base):
    """Is `value` looser than `base` for this definition? looser "lower": a smaller value weakens the rule; "higher": a
    larger one; null (not ordered, or text): every value other than the base counts as looser."""
    way = definition.get('looser')
    if way == 'lower' and _is_num(value) and _is_num(base):
        return value < base - 1e-9
    if way == 'higher' and _is_num(value) and _is_num(base):
        return value > base + 1e-9
    return value != base


def measured_under_error(value, measured=False):
    """Why this is not a "measured_under" value, or None. It is null, or an object with exactly the keys of
    MEASURED_UNDER, each a number (the material: a text) or null = not recorded. `measured`: it belongs to a registry
    default tagged M, where null is not enough."""
    if value is None:
        return 'tag M needs "measured_under": what the value was printed with (%s)' % ', '.join(MEASURED_UNDER) if measured else None
    if not isinstance(value, dict) or sorted(value) != sorted(MEASURED_UNDER):
        return '"measured_under" must be null or an object with exactly %s' % ', '.join(MEASURED_UNDER)
    for k in MEASURED_UNDER:
        v = value[k]
        good = v is None or (isinstance(v, str) and bool(v.strip()) if k == 'material' else _is_num(v))
        if not good:
            return '"measured_under".%s must be %s or null' % (k, 'text' if k == 'material' else 'a number')
    return None


def measured_here(under, target):
    """None when a fit printed under `under` counts as measured for a project with this declared target, else the reason
    as one line. Every value of MEASURED_UNDER must be recorded on both sides and equal (numbers to 0.0005, the
    material without regard to case): what is not known to be the same is not the same."""
    if not isinstance(under, dict):
        return 'it does not say what it was printed with ("measured_under")'
    if not isinstance(target, dict):
        return 'no declared target to compare its print with (gate/project.json "targets")'
    why = []
    for k in MEASURED_UNDER:
        have, want = under.get(k), target.get(k)
        if have is None:
            why.append('its "measured_under" does not record %s' % k)
        elif want is None:
            why.append('the declared target does not say %s' % k)
        elif k == 'material':
            if str(have).strip().lower() != str(want).strip().lower():
                why.append('printed in %s, this project is declared %s' % (have, want))
        elif not (_is_num(have) and _is_num(want) and abs(have - want) <= 5e-4):
            why.append('printed with %s %s, this project is declared %s' % (k, have, want))
    return '; '.join(why) or None


def load_overrides(root):
    """gate/owner/params.json as {name: entry}; {} when the file is absent. Keys that start with "_" are dropped."""
    data = read_json(gate_path(root, 'owner', 'params.json'), {})
    if not isinstance(data, dict):
        raise ParamError('gate/owner/params.json must be an object of name -> {value, tag, source, date}')
    return {k: v for k, v in data.items() if not k.startswith('_')}


def _looser_line(name, d, value, base, tag, key=None):
    what = '%s[%s]' % (name, key) if key else name
    way = {'lower': 'lower is looser', 'higher': 'higher is looser', None: 'any other value counts as looser'}[d.get('looser')]
    return ('%s = %s is looser than the default %s (%s): it needs tag M (measured on a print) or U (owner decision), it has %s'
            % (what, value, base, way, tag))


def check(reg, overrides, target=None):
    """Every problem of the project values, as a list of texts (empty = sound). `reg` is gate_registry.load(),
    `overrides` is load_overrides(root), `target` the declared target {"nozzle", "line_width", "material"} or None."""
    errors, defs = [], reg['params']
    for name in sorted(overrides or {}):
        e = overrides[name]
        if name not in defs:
            near = difflib.get_close_matches(name, list(defs), 3)
            errors.append('unknown parameter %s%s' % (name, (' (did you mean %s?)' % ', '.join(near)) if near else ''))
            continue
        d = defs[name]
        if not isinstance(e, dict):
            errors.append('%s: must be {"value", "tag", "source", "date"}' % name); continue
        for k in OVERRIDE_KEYS:
            if k not in e:
                errors.append('%s: missing "%s"' % (name, k))
        for k in e:
            if k not in OVERRIDE_KEYS and k not in OVERRIDE_OPTIONAL:
                errors.append('%s: unknown key "%s"' % (name, k))
        if d.get('formula'):
            errors.append('%s is computed (%s): override its inputs, not the result' % (name, d['formula'])); continue
        if d.get('from') == 'target':
            errors.append('%s comes from the declared target of the .3mf project: set it in gate/project.json "targets"' % name); continue
        tag = e.get('tag')
        if tag not in TAGS:
            errors.append('%s: tag must be one of M U D S, got %r' % (name, tag)); continue
        if not isinstance(e.get('source'), str) or not e.get('source', '').strip():
            errors.append('%s: "source" must say where the value comes from' % name)
        if not isinstance(e.get('date'), str) or not _DATE.match(e.get('date', '')):
            errors.append('%s: "date" must be YYYY-MM-DD' % name)
        if 'value' not in e:
            continue
        v, base = e['value'], d['default']
        if _is_num(base) != _is_num(v) or (isinstance(base, str) and not isinstance(v, str)):
            errors.append('%s: value must be %s like the default %r, got %r' % (name, 'a number' if _is_num(base) else 'text', base, v)); continue
        if is_looser(d, v, base) and tag not in ('M', 'U'):
            errors.append(_looser_line(name, d, v, base, tag))
        if 'measured_under' in e:
            why = measured_under_error(e['measured_under'])
            if why:
                errors.append('%s: %s' % (name, why))
        if 'per' in e:
            if 'per' not in d:
                errors.append('%s: has no per-size values in the registry, "per" is not allowed' % name)
            elif not isinstance(e['per'], dict) or not all(isinstance(k, str) and _is_num(x) for k, x in e['per'].items()):
                errors.append('%s: "per" must be an object of key -> number' % name)
            else:
                for k in sorted(e['per']):
                    kbase = d['per'].get(k, base)
                    if is_looser(d, e['per'][k], kbase) and tag not in ('M', 'U'):
                        errors.append(_looser_line(name, d, e['per'][k], kbase, tag, k))
    errors += _target_errors(defs, target or {}, 'declared target')
    return errors


def _target_errors(defs, target, where, nested=False):
    """Problems of one declared target. Besides the target parameters it may hold "objects": {"<object name>": {key:
    value}}: the fit settings one object of the .3mf is MEANT to print with (an override the owner declares), and
    "print": {slicer setting: value}: the print settings (supports, wall order) project.settings_match compares, for the
    project and, inside "objects", for one object."""
    errors = []
    known = sorted(n[7:] for n in defs if defs[n].get('from') == 'target')
    for k in sorted(target):
        if k == 'print':                     # print settings the project prints with: {"enable_support": 1, "support_type": "tree(auto)", ...}
            block = target[k]
            if not isinstance(block, dict) or not all(isinstance(n, str) and n and isinstance(v, (str, int, float, bool)) and v is not None for n, v in block.items()):
                errors.append('%s: "print" must be an object of slicer setting -> value (a number, a text, true or false)' % where)
            continue
        if k == 'objects' and not nested:
            objects = target[k]
            if not isinstance(objects, dict) or not all(isinstance(n, str) and n and isinstance(v, dict) for n, v in objects.items()):
                errors.append('%s: "objects" must be an object of object name -> {setting: value}' % where)
                continue
            for name in sorted(objects):
                errors += _target_errors(defs, objects[name], '%s, object "%s"' % (where, name), nested=True)
            continue
        d = defs.get('target.' + k)
        if d is None or d.get('from') != 'target':
            errors.append('%s: unknown key "%s" (known: %s%s)' % (where, k, ', '.join(known), '' if nested else ', objects'))
        elif _is_num(d['default']) != _is_num(target[k]):
            errors.append('%s: "%s" must be %s' % (where, k, 'a number' if _is_num(d['default']) else 'text'))
    return errors


def _formula_tag(tags):
    tags = set(tags)
    if tags <= {'M'}: return 'M'
    if tags <= {'M', 'U'}: return 'U'
    return 'D' if 'D' in tags else 'S'


def resolve(reg, overrides=None, target=None):
    """Every parameter of the registry with the value this project uses.

    reg        gate_registry.load()
    overrides  load_overrides(root), or None for the registry defaults
    target     the declared target of the .3mf project the part is judged for: {"nozzle": 0.6, "line_width": 0.62,
               "material": "PETG", ...}; None = the defaults of the target category. Its "objects" (overrides the owner
               declares for single objects of the .3mf) are not parameters: project.settings_match reads them

    Returns {name: {"name", "value", "per": {key: number} | None, "unit", "tag", "source", "date", "from": "default" |
    "project" | "target" | "formula", "looser", "class", "default", "inputs": [names a formula read],
    "measured_under": {...} | None, "tag_given": the tag as written, "tag_why": text | None}}.
    A fit parameter written as M that was not printed under this target has "tag": "D", "tag_given": "M" and the
    reason in "tag_why" (measured_here). A formula takes its tag from the tags its inputs have here.
    Raises ParamError (with .errors) when check() finds anything or a formula cannot be computed."""
    overrides = overrides or {}
    errors = check(reg, overrides, target)
    if errors:
        raise ParamError('parameters: %d error(s)\n  %s' % (len(errors), '\n  '.join(errors)), errors)
    defs, out = reg['params'], {}
    for name in sorted(defs):
        d = defs[name]
        if d.get('formula'):
            continue
        e = {'name': name, 'value': d['default'], 'per': dict(d['per']) if d.get('per') else None, 'unit': d['unit'],
             'tag': d.get('tag', 'D'), 'source': d['measured_on'], 'date': None, 'from': 'default', 'looser': d.get('looser'),
             'class': d['class'], 'default': d['default'], 'inputs': [], 'measured_under': d.get('measured_under'), 'tag_why': None}
        if d.get('from') == 'target':
            key = name.split('.', 1)[1]
            if target and key in target:
                e.update(value=target[key], tag='U', source='declared target in gate/project.json', **{'from': 'target'})
        elif name in overrides:
            o = overrides[name]
            e.update(value=o['value'], tag=o['tag'], source=o['source'], date=o['date'], measured_under=o.get('measured_under'), **{'from': 'project'})
            if o.get('per'):
                e['per'] = dict(e['per'] or {}, **o['per'])
        e['tag_given'] = e['tag']
        if e['class'] == 'fit' and e['tag'] == 'M':
            why = measured_here(e['measured_under'], target)
            if why:
                e.update(tag='D', tag_why='measured (M), but not for this project: ' + why)
        out[name] = e
    for name in formula_order(defs):
        d = defs[name]
        inputs = formula_names(d['formula'])
        value = evaluate(d['formula'], lambda n: out[n]['value'])
        tag = _formula_tag(out[n]['tag'] for n in inputs)
        moved = sorted(n for n in inputs if out[n].get('tag_why'))
        out[name] = {'name': name, 'value': round(value, 6), 'per': None, 'unit': d['unit'], 'tag': tag,
                     'source': d['formula'], 'date': None, 'from': 'formula', 'looser': d.get('looser'), 'class': d['class'],
                     'default': None, 'inputs': inputs, 'measured_under': None, 'tag_given': tag,
                     'tag_why': ('%s: %s' % (moved[0], out[moved[0]]['tag_why'])) if moved and tag == 'D' else None}
    return out


def values(resolved, names=None):
    """The flat form judges and Fusion probes get: {name: value}, plus one entry "name[key]" for every per-size value
    and, for every parameter of class "fit", one entry "name#tag" with its tag here ("M", "U", "D" or "S"): a rule that
    blocks on a fit that was never printed reads it, and a stamp judged with one tag is open again under another.
    `names` limits it to those parameters (and the inputs of their formulas)."""
    want = None
    if names is not None:
        want, todo = set(), list(names)
        while todo:
            n = todo.pop()
            if n in want:
                continue
            if n not in resolved:
                raise ParamError('unknown parameter %s' % n)
            want.add(n); todo += resolved[n]['inputs']
    out = {}
    for name in sorted(resolved):
        if want is not None and name not in want:
            continue
        out[name] = resolved[name]['value']
        for k, v in sorted((resolved[name].get('per') or {}).items()):
            out['%s[%s]' % (name, k)] = v
        if resolved[name].get('class') == 'fit':
            out[name + TAG_SUFFIX] = resolved[name]['tag']
    return out


def value(vals, name, key=None):
    """One number out of values(): the per-size value "name[key]" when there is one, else the plain value.
    Also accepts the dict resolve() returns. ParamError for an unknown name."""
    if name not in vals:
        raise ParamError('unknown parameter %s' % name)
    entry = vals[name]
    if isinstance(entry, dict):                                        # the resolve() form
        if key is not None and key in (entry.get('per') or {}):
            return entry['per'][key]
        return entry['value']
    if key is not None and '%s[%s]' % (name, key) in vals:
        return vals['%s[%s]' % (name, key)]
    return entry


def params_hash(resolved, names):
    """Hash over the values and tags of these parameters (and of what their formulas read): one input of a stamp's check."""
    vals = values(resolved, names)
    return digest({k: [v, resolved[k]['tag'] if k in resolved else None] for k, v in vals.items() if not k.endswith(TAG_SUFFIX)})


def limit_text(entry, key=None):
    """How a report prints a limit: '4.5 mm [U owner 2031-02-03]', '2.0 mm [D decided, not printed]'. A fit that was
    measured under other settings than this project declares says so in short; explain() has the reason."""
    v = value({entry['name']: entry}, entry['name'], key)
    unit = '' if entry['unit'] in ('text', 'regex', 'share', 'count') else ' ' + entry['unit']
    if entry['from'] == 'project':
        why = '%s %s' % (entry['tag'], entry['source'])
        if entry.get('date') and entry['date'] not in entry['source']:
            why += ' ' + entry['date']
    elif entry['from'] == 'formula':
        why = '%s = %s' % (entry['tag'], entry['source'])
    elif entry['from'] == 'target':
        why = 'declared target'
    else:
        why = '%s %s' % (entry['tag'], TAG_WORDS[entry['tag']] if entry['tag'] != 'M' else 'default measured on: ' + entry['source'])
    if entry.get('tag_why') and entry['from'] != 'formula':
        why = 'D here: measured, but not under the settings of this project (gate.py params explain %s)' % entry['name']
    return '%s%s [%s]' % (v, unit, why)


def explain(reg, name, overrides=None, target=None):
    """Several lines about one parameter: its value here, where it comes from, the registry default, which way is looser,
    the rules that read it and, for a formula, its inputs. ParamError for an unknown name."""
    if name not in reg['params']:
        near = difflib.get_close_matches(name, list(reg['params']), 3)
        raise ParamError('unknown parameter %s%s' % (name, (' (did you mean %s?)' % ', '.join(near)) if near else ''))
    res, d = resolve(reg, overrides, target), reg['params'][name]
    e = res[name]
    lines = ['%s = %s' % (name, limit_text(e))]
    if d.get('description'):
        lines.append('  ' + d['description'])
    if e['per']:
        lines.append('  per size: ' + ', '.join('%s %s' % (k, v) for k, v in sorted(e['per'].items())))
    if e['from'] == 'formula':
        lines.append('  formula: %s' % d['formula'])
        lines += ['    %s = %s' % (n, limit_text(res[n])) for n in e['inputs']]
    else:
        lines.append('  registry default: %s %s [%s], from: %s' % (d['default'], d['unit'], d.get('tag', 'D'), d['measured_on']))
        lines.append('  looser: %s' % {'lower': 'a lower value weakens the rule', 'higher': 'a higher value weakens the rule',
                                       None: 'not ordered: any other value needs tag M or U'}[d.get('looser')])
    lines.append('  class: %s' % d['class'])
    if e.get('measured_under'):
        lines.append('  measured under: %s' % ', '.join('%s %s' % (k, 'not recorded' if e['measured_under'].get(k) is None else e['measured_under'][k]) for k in MEASURED_UNDER))
    if e.get('tag_why'):
        lines.append('  tag here: D. %s' % e['tag_why'])
    used = sorted(r['id'] for r in reg['rules'].values() if name in (r.get('params') or []))
    read_by = sorted(n for n, x in reg['params'].items() if x.get('formula') and name in formula_names(x['formula']))
    lines.append('  rules: %s' % (', '.join(used) or 'none'))
    if read_by:
        lines.append('  read by the formulas of: %s' % ', '.join(read_by))
    return '\n'.join(lines)


def cmd_params(root, args):
    """gate.py params check | explain NAME. args.action is "check" or "explain", args.name the parameter for explain,
    args.target an optional .3mf project key. Prints; returns 0, or 4 when the project values have an error."""
    import gate_registry
    reg = gate_registry.load()
    overrides = load_overrides(root)
    project = load_project(root)
    target = target_of(project, getattr(args, 'target', None))
    try:
        if args.action == 'explain':
            if not getattr(args, 'name', None):
                raise ParamError('params explain needs a parameter name')
            print(explain(reg, args.name, overrides, target))
            return EXIT_PASS
        res = resolve(reg, overrides, target)
    except ParamError as e:
        for line in e.errors:
            print('PARAM ERROR  ' + line)
        print('TOTAL %d' % len(e.errors))
        return EXIT_INFRA
    for name in sorted(overrides):
        print('%-34s %s' % (name, limit_text(res[name])))
    unprinted = sorted(n for n, e in res.items() if e['tag'] in ('D', 'S') and e['class'] == 'fit')
    print('params: %d of %d set by the project, 0 errors' % (len(overrides), len(res)))
    if unprinted:
        print('fit values decided and never printed: %s' % ', '.join(unprinted))
    for n in sorted(n for n, e in res.items() if e.get('tag_why') and e['from'] != 'formula'):
        print('  %s: %s' % (n, res[n]['tag_why']))
    print('TOTAL 0')
    return EXIT_PASS
