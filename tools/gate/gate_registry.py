"""Load and validate the rule registry: rules/registry/<category>.json and rules/tables/<name>.json.

One file per category: {"category": "inserts", "params": {<name>: <definition>}, "rules": [<rule>, ...]}.

A rule:
    {"id": "inserts.wall", "title": "...", "statement": "...", "why": "...",
     "params": ["inserts.wall_target", "inserts.wall_floor"],
     "check": {"probe": "fusion_inserts", "judge": "inserts_wall", "needs": "fusion"},
     "level": "block", "stage": "design", "fix_order": ["...", "..."], "attest": null}
  level   block (a judge exists, a finding stops the stamp) | attest (a question) | advice (text only)
  stage   design (before export) | export (on the STL) | project (on the .3mf)
  needs   fusion | stl | 3mf | none
  a block rule names a probe and a judge, and the judge is the rule id with the dot replaced by an underscore
  an attest rule carries {"question", "evidence": [...], "per": "part" | "finding", "labels": [...]}
  an attest rule per part may carry "auto": [{"when": [fact tests of FACT_TESTS], "label": one of its labels}] or
  [{"when": [...], "na": "the reason"}]: when every test named is proven by the facts of the part, the gate gives that
  answer itself (gate_attest.fact_answer) and the report lists it as answered by facts
  optional keys: "applies" (all | fusion | generated), "deferred" (true: registered, not asked or run yet), "note",
  "supersedes"; any key that starts with "x_" is carried and not checked

A parameter definition:
    {"default": 4.0, "unit": "mm", "looser": "lower", "measured_on": "0.6 nozzle, PETG-CF, one printer",
     "class": "limit", "formula": null}
  looser  which direction weakens the rule: "lower" | "higher" | null (not ordered: any other value is a change)
  class   fit | limit | geometry
  formula a small expression over other parameters (gate_params.evaluate); then "default" is null
  optional keys: "tag" (M | D | S, the tag of the default; D when absent; never U), "per" ({key: number}: a value per
  size, "default" is the value of every other key), "from": "target" (the value comes from the declared target of the
  .3mf project, category target only), "description", "x_*"
  a fit parameter that is not a formula also carries "measured_under": what its default was printed with,
  {"nozzle": 0.6, "material": "PETG", "xy_hole_compensation": 0.1, "layer_height": 0.2}; a value that was not recorded
  is null there, and the whole object is null while the default is not measured (tag D or S). gate_params.resolve()
  keeps the tag M only for a project whose declared target says the same four values.

Every parameter is declared exactly once across all files and its name starts with its category. Every rule id is unique."""
import json, os, re
from gate_common import GateError, digest, rules_dir as _default_rules_dir
import gate_params

LEVELS = ('block', 'attest', 'advice')
STAGES = ('design', 'export', 'project')
NEEDS = ('fusion', 'stl', '3mf', 'none')
LOOSER = ('lower', 'higher', None)
CLASSES = ('fit', 'limit', 'geometry')
DEFAULT_TAGS = ('M', 'D', 'S')
EVIDENCE = ('label', 'shot', 'param', 'na', 'waiver')
APPLIES = ('all', 'fusion', 'generated')
CATEGORIES = ('inserts', 'screws', 'washers', 'bearings', 'walls', 'access', 'clearance', 'fits', 'overhang', 'teeth',
              'switches', 'motors', 'sensors', 'strength', 'symmetry', 'assembly', 'hardware', 'mesh', 'regression',
              'project', 'printed', 'fusion', 'process', 'print', 'target')
TABLES = ('inserts', 'screws', 'bearings', 'washers', 'pockets', 'pins')
PROBES = ('fusion_scan', 'fusion_census', 'fusion_inserts', 'fusion_bearings', 'fusion_openings', 'fusion_screws',
          'fusion_gap', 'fusion_thin', 'fusion_near', 'fusion_export', 'stl_walls', 'stl_mesh', 'project_consistency')
NAME_RE = re.compile(r'^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$')
PLACEHOLDER_RE = re.compile(r'\{([a-z][a-z0-9_]*\.[a-z][a-z0-9_]*)\}')     # {inserts.wall_target} in a statement: shown with the project's value
RULE_KEYS = ('id', 'title', 'statement', 'why', 'params', 'check', 'level', 'stage', 'fix_order', 'attest')
RULE_OPTIONAL = ('applies', 'deferred', 'note', 'supersedes')
PARAM_KEYS = ('default', 'unit', 'looser', 'measured_on', 'class', 'formula')
PARAM_OPTIONAL = ('tag', 'per', 'from', 'description', 'measured_under')
MEASURED_UNDER = gate_params.MEASURED_UNDER
JUDGE_NAMES = {'fits.bought_part_pocket': 'fits_bought_pocket'}     # rules whose judge is not named after the whole id
CHECK_KEYS = ('probe', 'judge', 'needs')
CHECK_OPTIONAL = ('budget_s',)
ATTEST_KEYS = ('question', 'evidence', 'per')
ATTEST_OPTIONAL = ('labels', 'held', 'param', 'auto')
# what gate_attest can prove from the facts of a part, for attest.auto (the functions are gate_attest.FACT_TESTS)
FACT_TESTS = ('no_screw_feature', 'no_bearing_here', 'no_bought_near', 'no_motor_near', 'bed_face_measured')


class RegistryError(GateError):
    """The registry does not hold together. `.errors` is the full list; the message shows the first ten."""

    def __init__(self, errors):
        self.errors = list(errors)
        more = '' if len(self.errors) <= 10 else '\n  ... and %d more' % (len(self.errors) - 10)
        GateError.__init__(self, 'registry: %d error(s)\n  %s%s' % (len(self.errors), '\n  '.join(self.errors[:10]), more))


def file_of(category):
    """File name of a category: 'inserts' -> 'inserts.json', 'target' -> '_target.json'."""
    return ('_target' if category == 'target' else category) + '.json'


def judge_name(rule_id):
    """'inserts.wall' -> 'inserts_wall': the one judge name a rule id may carry (JUDGE_NAMES holds the exceptions)."""
    return JUDGE_NAMES.get(rule_id) or rule_id.replace('.', '_')


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _keys(obj, required, optional, where, errors):
    for k in required:
        if k not in obj:
            errors.append('%s: missing key "%s"' % (where, k))
    for k in obj:
        if k not in required and k not in optional and not k.startswith('x_'):
            errors.append('%s: unknown key "%s"' % (where, k))


def _check_param(name, d, category, errors):
    where = 'parameter %s' % name
    if not NAME_RE.match(name):
        errors.append('%s: name must be <category>.<name> in lower case' % where); return
    if name.split('.')[0] != category:
        errors.append('%s: declared in category "%s", its name must start with "%s."' % (where, category, category))
    if not isinstance(d, dict):
        errors.append('%s: definition must be an object' % where); return
    _keys(d, PARAM_KEYS, PARAM_OPTIONAL, where, errors)
    default, formula = d.get('default'), d.get('formula')
    if formula is not None:
        if not isinstance(formula, str) or not formula.strip():
            errors.append('%s: formula must be text or null' % where)
        else:
            try:
                gate_params.formula_names(formula)
            except gate_params.ParamError as e:
                errors.append('%s: %s' % (where, e))
        if default is not None:
            errors.append('%s: a formula parameter has "default": null' % where)
        if 'per' in d or 'from' in d:
            errors.append('%s: a formula parameter has neither "per" nor "from"' % where)
    elif not (_is_num(default) or (isinstance(default, str) and default)):
        errors.append('%s: default must be a number or text' % where)
    if d.get('looser') not in LOOSER:
        errors.append('%s: looser must be "lower", "higher" or null' % where)
    elif isinstance(default, str) and d.get('looser') is not None:
        errors.append('%s: a text value is not ordered: looser must be null' % where)
    if d.get('class') not in CLASSES:
        errors.append('%s: class must be one of %s' % (where, ', '.join(CLASSES)))
    if not isinstance(d.get('unit'), str) or not d.get('unit'):
        errors.append('%s: unit must be text ("mm", "deg", "share", "count", "text", "regex")' % where)
    if not isinstance(d.get('measured_on'), str) or not d.get('measured_on', '').strip():
        errors.append('%s: measured_on must say where the default comes from' % where)
    if d.get('tag', 'D') not in DEFAULT_TAGS:
        errors.append('%s: the tag of a default is M, D or S (never U: U is the owner of a project)' % where)
    if 'per' in d:
        per = d['per']
        if not _is_num(default):
            errors.append('%s: "per" needs a numeric default' % where)
        if not isinstance(per, dict) or not per or not all(isinstance(k, str) and _is_num(v) for k, v in per.items()):
            errors.append('%s: "per" must be an object of key -> number' % where)
    if 'from' in d and (d['from'] != 'target' or category != 'target'):
        errors.append('%s: "from" may only be "target", in the category target' % where)
    if formula is not None:
        if 'measured_under' in d:
            errors.append('%s: a formula parameter has no "measured_under" (its inputs carry it)' % where)
    elif d.get('class') == 'fit' and 'measured_under' not in d:
        errors.append('%s: a fit parameter carries "measured_under" (null while it is not measured)' % where)
    elif 'measured_under' in d:
        why = gate_params.measured_under_error(d['measured_under'], d.get('class') == 'fit' and d.get('tag', 'D') == 'M')
        if why:
            errors.append('%s: %s' % (where, why))


def _check_check(c, where, errors):
    if not isinstance(c, dict):
        errors.append('%s: check must be an object or null' % where); return
    _keys(c, CHECK_KEYS, CHECK_OPTIONAL, where + ' check', errors)
    if c.get('probe') not in PROBES:
        errors.append('%s: unknown probe %r' % (where, c.get('probe')))
    if c.get('needs') not in NEEDS:
        errors.append('%s: needs must be one of %s' % (where, ', '.join(NEEDS)))
    if c.get('judge') is not None and not isinstance(c.get('judge'), str):
        errors.append('%s: judge must be a name or null' % where)
    if 'budget_s' in c and not (_is_num(c['budget_s']) and 0 < c['budget_s'] <= 30):
        errors.append('%s: budget_s must be between 0 and 30' % where)


def _check_attest(a, where, errors):
    if not isinstance(a, dict):
        errors.append('%s: an attest rule needs an "attest" object' % where); return
    _keys(a, ATTEST_KEYS, ATTEST_OPTIONAL, where + ' attest', errors)
    if not isinstance(a.get('question'), str) or not a.get('question', '').strip():
        errors.append('%s: attest.question must be text' % where)
    ev = a.get('evidence')
    if not isinstance(ev, list) or not ev or any(e not in EVIDENCE for e in ev):
        errors.append('%s: attest.evidence must be a list out of %s' % (where, ', '.join(EVIDENCE)))
        ev = []
    if a.get('per') not in ('part', 'finding'):
        errors.append('%s: attest.per must be "part" or "finding"' % where)
    labels = a.get('labels', [])
    if not isinstance(labels, list) or any(not isinstance(x, str) or not x.strip() for x in labels):
        errors.append('%s: attest.labels must be a list of texts' % where)
    elif 'label' in ev and not labels:
        errors.append('%s: evidence "label" needs the fixed list attest.labels' % where)
    elif labels and 'label' not in ev:
        errors.append('%s: attest.labels given but "label" is not an evidence kind' % where)
    held = a.get('held', {})
    if not isinstance(held, dict):
        errors.append('%s: attest.held must be an object of label -> {"none": [...]} or {"some": [...]}' % where)
    else:
        for label, claim in sorted(held.items()):
            if not isinstance(labels, list) or label not in labels:
                errors.append('%s: attest.held names %r, which is not one of its labels' % (where, label))
            if isinstance(claim, dict) and claim == {'by': 'none'}:
                if a.get('per') != 'finding':
                    errors.append('%s: attest.held[%r] = {"by": "none"} refuses a label while its finding names bodies: only for a rule asked per finding' % (where, label))
                continue
            paths = [p for k in ('none', 'some') for p in (claim.get(k) or [])] if isinstance(claim, dict) and set(claim) <= {'none', 'some'} else None
            if not paths or any(not isinstance(p, str) or p.split('.')[0] not in PROBES or not 2 <= len(p.split('.')) <= 3 for p in paths):
                errors.append('%s: attest.held[%r] must be {"none": [...]} or {"some": [...]} of "<probe>.<list>" or "<probe>.<list>.<sublist>"' % (where, label))
    auto = a.get('auto')
    if auto is not None:
        if a.get('per') != 'part' or not isinstance(auto, list) or not auto:
            errors.append('%s: attest.auto must be a list with at least one entry, on a rule that is asked per part' % where)
        for entry in auto if isinstance(auto, list) else []:
            ok = (isinstance(entry, dict) and set(entry) <= {'when', 'label', 'na'} and ('label' in entry) != ('na' in entry)
                  and isinstance(entry.get('when'), list) and entry['when'] and all(t in FACT_TESTS for t in entry['when']))
            if not ok:
                errors.append('%s: an attest.auto entry is {"when": [out of %s], "label": "..."} or {"when": [...], "na": "the reason"}'
                              % (where, ', '.join(FACT_TESTS)))
            elif 'label' in entry and (not isinstance(labels, list) or entry['label'] not in labels):
                errors.append('%s: attest.auto gives %r, which is not one of its labels' % (where, entry['label']))
            elif 'na' in entry and ('na' not in ev or not isinstance(entry['na'], str) or len(entry['na'].split()) < 3):
                errors.append('%s: attest.auto gives "na": the rule must take "na" as evidence, and the reason must be a sentence' % where)
    want = a.get('param')
    if want is not None:
        if 'param' not in ev:
            errors.append('%s: attest.param given but "param" is not an evidence kind' % where)
        if not isinstance(want, dict) or not set(want) <= {'class', 'matches'} or want.get('class', 'fit') not in CLASSES or want.get('matches', 'hole_d') != 'hole_d':
            errors.append('%s: attest.param must be {"class": one of %s, "matches": "hole_d"}' % (where, ', '.join(CLASSES)))


def _check_rule(r, errors):
    if not isinstance(r, dict) or not isinstance(r.get('id'), str):
        errors.append('a rule without an "id"'); return
    where = 'rule %s' % r['id']
    if not NAME_RE.match(r['id']):
        errors.append('%s: id must be <group>.<name> in lower case' % where)
    _keys(r, RULE_KEYS, RULE_OPTIONAL, where, errors)
    for k in ('title', 'statement', 'why'):
        if not isinstance(r.get(k), str) or not r.get(k, '').strip():
            errors.append('%s: "%s" must be text' % (where, k))
    if r.get('level') not in LEVELS:
        errors.append('%s: level must be one of %s' % (where, ', '.join(LEVELS)))
    if r.get('stage') not in STAGES:
        errors.append('%s: stage must be one of %s' % (where, ', '.join(STAGES)))
    for k in ('params', 'fix_order'):
        if not isinstance(r.get(k), list) or any(not isinstance(x, str) for x in r.get(k) or []):
            errors.append('%s: "%s" must be a list of texts' % (where, k))
    if isinstance(r.get('statement'), str) and isinstance(r.get('params'), list):
        for name in PLACEHOLDER_RE.findall(r['statement']):
            if name not in r['params']:
                errors.append('%s: the statement shows {%s}, which is not in its "params"' % (where, name))
    if r.get('applies', 'all') not in APPLIES:
        errors.append('%s: applies must be one of %s' % (where, ', '.join(APPLIES)))
    if not isinstance(r.get('deferred', False), bool):
        errors.append('%s: deferred must be true or false' % where)
    check, level = r.get('check'), r.get('level')
    if check is not None:
        _check_check(check, where, errors)
    if level == 'block':
        if not isinstance(check, dict) or not check.get('judge') or not check.get('probe'):
            errors.append('%s: a block rule names a probe and a judge' % where)
        elif check['judge'] != judge_name(r['id']):
            errors.append('%s: its judge must be named %s, not %s' % (where, judge_name(r['id']), check['judge']))
        if r.get('attest') is not None:
            errors.append('%s: a block rule has "attest": null' % where)
        if r.get('deferred'):
            errors.append('%s: a block rule cannot be deferred (register it as attest or advice until it is built)' % where)
    elif level == 'attest':
        _check_attest(r.get('attest'), where, errors)
        if isinstance(check, dict) and check.get('judge') and check['judge'] != judge_name(r['id']):
            errors.append('%s: its judge must be named %s, not %s' % (where, judge_name(r['id']), check['judge']))
    elif level == 'advice':
        if r.get('attest') is not None:
            errors.append('%s: an advice rule has "attest": null' % where)
        if isinstance(check, dict) and check.get('judge'):
            errors.append('%s: an advice rule has no judge (a rule with a judge is block or attest)' % where)


def _read(path, errors):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except ValueError as e:
        errors.append('%s: not valid JSON (%s)' % (os.path.basename(path), e))
    except OSError as e:
        errors.append('%s: %s' % (os.path.basename(path), e))
    return None


def load(rules_dir=None, judges=None, complete=True):
    """Read every rules/registry/*.json and rules/tables/*.json under `rules_dir` (default: the rules folder of this copy
    of the tools) and validate them. Raises RegistryError with every problem found.

    judges    names of the judge functions that exist (pass gate_judges.JUDGES); None = do not check that judges exist
    complete  True: every category of CATEGORIES and every table of TABLES must have its file

    Returns {"dir": path, "rules": {id: rule + "category"}, "params": {name: definition + "category"},
             "tables": {name: content}, "categories": {category: {"file", "rules": [ids], "params": [names]}},
             "sha256": hash over everything read}."""
    rules_dir = rules_dir or _default_rules_dir()
    errors, reg = [], {'dir': rules_dir, 'rules': {}, 'params': {}, 'tables': {}, 'categories': {}}
    reg_dir, tab_dir = os.path.join(rules_dir, 'registry'), os.path.join(rules_dir, 'tables')
    if not os.path.isdir(reg_dir):
        raise RegistryError(['no folder %s' % reg_dir])
    by_file = {file_of(c): c for c in CATEGORIES}
    for fname in sorted(f for f in os.listdir(reg_dir) if f.endswith('.json')):
        data = _read(os.path.join(reg_dir, fname), errors)
        if data is None:
            continue
        if fname not in by_file:
            errors.append('%s: not a category file (categories: %s)' % (fname, ', '.join(CATEGORIES))); continue
        cat = by_file[fname]
        if not isinstance(data, dict) or data.get('category') != cat:
            errors.append('%s: "category" must be "%s"' % (fname, cat)); continue
        if not isinstance(data.get('params'), dict) or not isinstance(data.get('rules'), list):
            errors.append('%s: needs "params" (object) and "rules" (list)' % fname); continue
        for k in data:
            if k not in ('category', 'params', 'rules') and not k.startswith('x_'):
                errors.append('%s: unknown key "%s"' % (fname, k))
        entry = reg['categories'][cat] = {'file': fname, 'rules': [], 'params': []}
        for name, d in data['params'].items():
            _check_param(name, d, cat, errors)
            if name in reg['params']:
                errors.append('parameter %s: declared twice (%s and %s)' % (name, reg['categories'][reg['params'][name]['category']]['file'], fname))
                continue
            if isinstance(d, dict):
                reg['params'][name] = dict(d, category=cat); entry['params'].append(name)
        for r in data['rules']:
            _check_rule(r, errors)
            if not isinstance(r, dict) or not isinstance(r.get('id'), str):
                continue
            if r['id'] in reg['rules']:
                errors.append('rule %s: declared twice (%s and %s)' % (r['id'], reg['categories'][reg['rules'][r['id']]['category']]['file'], fname))
                continue
            reg['rules'][r['id']] = dict(r, category=cat); entry['rules'].append(r['id'])
    if os.path.isdir(tab_dir):
        for fname in sorted(f for f in os.listdir(tab_dir) if f.endswith('.json')):
            data, name = _read(os.path.join(tab_dir, fname), errors), fname[:-5]
            if data is None:
                continue
            if name not in TABLES:
                errors.append('tables/%s: not a table (tables: %s)' % (fname, ', '.join(TABLES)))
            elif not isinstance(data, dict) or data.get('table') != name:
                errors.append('tables/%s: "table" must be "%s"' % (fname, name))
            else:
                reg['tables'][name] = data
    if complete:
        errors += ['%s: the file of the category is missing' % file_of(c) for c in CATEGORIES if c not in reg['categories']]
        errors += ['tables/%s.json: the table is missing' % t for t in TABLES if t not in reg['tables']]
    errors += validate(reg, judges)
    if errors:
        raise RegistryError(errors)
    reg['sha256'] = digest({'rules': reg['rules'], 'params': reg['params'], 'tables': reg['tables']}, 64)
    return reg


def load_default(root=None):
    """load() of this copy's rules, checked against the judges that exist in gate_judges.JUDGES, with the project's own
    table rows laid over them (project_tables: gate/owner/tables.json of `root`; default: the project these tools are
    vendored into, else the project above the working folder)."""
    import gate_judges
    reg = load(judges=set(gate_judges.JUDGES))
    root = root or _project_root()
    return with_project_tables(reg, root) if root else reg


# ---- the project's own table rows (gate/owner/tables.json, OWNER) -----------------------------------------------------------

TABLE_OVERRIDES = 'tables.json'
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


_ROOT = {}


def use_root(root):
    """The project the command at hand works on (gate.py sets it from --root): load_default() lays its table rows over the
    shipped ones. None forgets it."""
    if root:
        _ROOT['root'] = os.path.abspath(root)
    else:
        _ROOT.pop('root', None)


def _project_root():
    """The project of the command at hand (use_root), else the project of a vendored copy (<root>/gate/tools), else the
    project at or above the working folder, else None."""
    from gate_common import tools_dir, find_root
    if _ROOT.get('root') and os.path.isfile(os.path.join(_ROOT['root'], 'gate', 'project.json')):
        return _ROOT['root']
    here = os.path.normpath(os.path.join(tools_dir(), '..', '..'))
    if os.path.isfile(os.path.join(here, 'gate', 'project.json')):
        return here
    try:
        return find_root(os.getcwd())
    except OSError:
        return None


def _numbers(x, positive=True):
    return isinstance(x, list) and bool(x) and all(_is_num(v) and (v > 0 or not positive) for v in x)


def _source_errors(e, where):
    out = []
    if not isinstance(e.get('source'), str) or not e['source'].strip():
        out.append('%s: "source" must say where it comes from (the calliper, the coupon, the order)' % where)
    if not isinstance(e.get('date'), str) or not _DATE_RE.match(e['date']):
        out.append('%s: "date" must be YYYY-MM-DD' % where)
    return out


def _print_entry_errors(e, where):
    """A print a table row was measured under: the four keys of MEASURED_UNDER (each recorded) + source + date."""
    if not isinstance(e, dict) or not set(e) <= set(MEASURED_UNDER) | {'source', 'date', 'note'}:
        return ['%s: a print is {%s, "source", "date"}' % (where, ', '.join('"%s"' % k for k in MEASURED_UNDER))]
    out = _source_errors(e, where)
    why = gate_params.measured_under_error({k: e.get(k) for k in MEASURED_UNDER}, measured=True)
    if why:
        out.append('%s: %s' % (where, why))
    missing = [k for k in MEASURED_UNDER if e.get(k) is None]
    if missing:
        out.append('%s: a print the owner records names every setting it was printed with; missing: %s' % (where, ', '.join(missing)))
    return out


def table_row_key(table, row, reg):
    """The one key a row of gate/owner/tables.json is stored and read under: a pin diameter as '%g' ("5.0" -> "5"),
    a screw size as "M%g" ("M3.0" -> "M3"), a bearing alias as its designation. Anything that cannot be read so is
    returned as it is (project_table_errors says what is wrong with it)."""
    text = str(row)
    if table == 'pins':
        try:
            return '%g' % float(text)
        except ValueError:
            return text
    if table in ('screws', 'screws_set', 'stock_lengths', 'stock_lengths_set'):
        m = re.match(r'^M(\d+(?:\.\d+)?)$', text)
        return 'M%g' % float(m.group(1)) if m else text
    if table == 'bearings':
        b = (reg.get('tables') or {}).get('bearings') or {}
        if text in (b.get('sizes') or {}):
            return text
        return (b.get('aliases') or {}).get(text, text)
    return text


def _spellings(rows, table, reg):
    """Texts of the rows that name one row twice ("5" and "5.0", an alias next to its designation): which one the
    registry would read is the order of the file, so it is refused."""
    seen = {}
    for k in sorted(rows, key=str):
        seen.setdefault(table_row_key(table, k, reg), []).append(str(k))
    return ['%s (one row written as %s: keep one)' % (key, ' and '.join('"%s"' % x for x in ks)) for key, ks in sorted(seen.items()) if len(ks) > 1]


def project_table_errors(overrides, reg):
    """Every problem of gate/owner/tables.json as texts, [] when sound. What a project may set:
      pins      {"sizes": {"<d>": {"lengths": [mm, ...]}}}                     the pins it stocks (a new diameter too)
      screws    {"stock_lengths": {"M3": [mm, ...]}, "stock_lengths_set": {...}}  the screws it stocks
      bearings  {"measured": {"<designation>": {"d", "D", "B", "source", "date"}}}  the batch in hand, measured
                {"seat_fit": {"measured_under": [print, ...]}}                  seat fits printed and measured
      inserts   {"measured_under": {"<kind>": [print, ...]}}                   insert profiles printed under a target
    A print is {"nozzle", "material", "xy_hole_compensation", "layer_height", "source", "date"}, every setting given.
    Nothing here changes a limit: a stock list, a measured bought part and a print are facts the owner records."""
    errors, tables = [], reg['tables']
    if not isinstance(overrides, dict):
        return ['gate/owner/tables.json must be an object of table -> rows']
    for name in sorted(k for k in overrides if not k.startswith('_')):
        o, where = overrides[name], 'gate/owner/tables.json %s' % name
        if not isinstance(o, dict):
            errors.append('%s: must be an object' % where); continue
        allowed = {'pins': ('sizes',), 'screws': ('stock_lengths', 'stock_lengths_set'), 'bearings': ('measured', 'seat_fit'),
                   'inserts': ('measured_under',)}.get(name)
        if allowed is None:
            errors.append('gate/owner/tables.json: table %r cannot be set by a project (only pins, screws, bearings, inserts)' % name); continue
        for k in sorted(x for x in o if not x.startswith('_')):
            if k not in allowed:
                errors.append('%s: unknown key "%s" (known: %s)' % (where, k, ', '.join(allowed))); continue
            v = o[k]
            if not isinstance(v, dict):
                errors.append('%s.%s: must be an object' % (where, k)); continue
            if name == 'pins' or (name == 'bearings' and k == 'measured') or name == 'screws':
                for text in _spellings(v, 'bearings' if name == 'bearings' else k if name == 'screws' else 'pins', reg):
                    errors.append('%s.%s: %s' % (where, k, text))
            if name == 'pins':
                for d, row in sorted(v.items(), key=lambda kv: str(kv[0])):
                    try:
                        ok = float(d) > 0
                    except ValueError:
                        ok = False
                    if not ok or not isinstance(row, dict) or not _numbers(row.get('lengths')) or not set(row) <= {'lengths', 'source', 'date', 'note'}:
                        errors.append('%s.sizes.%s: a pin row is {"lengths": [mm, ...]} under its diameter' % (where, d))
            elif name == 'screws':
                for size, lengths in sorted(v.items(), key=lambda kv: str(kv[0])):
                    if not isinstance(size, str) or not re.match(r'^M\d+(?:\.\d+)?$', size) or not _numbers(lengths):
                        errors.append('%s.%s.%s: a stock row is "M3": [mm, ...]' % (where, k, size))
            elif name == 'bearings' and k == 'measured':
                sizes = tables.get('bearings', {}).get('sizes') or {}
                aliases = tables.get('bearings', {}).get('aliases') or {}
                for des, row in sorted(v.items(), key=lambda kv: str(kv[0])):
                    w = '%s.measured.%s' % (where, des)
                    if des not in sizes and des not in aliases:
                        errors.append('%s: no row of the bearings table is called %s (add the standard row first)' % (w, des)); continue
                    if not isinstance(row, dict) or not all(_is_num(row.get(x)) and row[x] > 0 for x in ('d', 'D', 'B')):
                        errors.append('%s: a measured bearing is {"d", "D", "B", "source", "date"} in mm' % w); continue
                    errors += _source_errors(row, w)
            elif name == 'bearings' and k == 'seat_fit':
                prints = v.get('measured_under')
                if not isinstance(prints, list) or not prints or set(v) - {'measured_under'}:
                    errors.append('%s.seat_fit: is {"measured_under": [print, ...]}' % where); continue
                for i, e in enumerate(prints):
                    errors += _print_entry_errors(e, '%s.seat_fit.measured_under[%d]' % (where, i))
            elif name == 'inserts':
                kinds = tables.get('inserts', {}).get('kinds') or {}
                for kind, prints in sorted(v.items(), key=lambda kv: str(kv[0])):
                    w = '%s.measured_under.%s' % (where, kind)
                    if kind not in kinds:
                        errors.append('%s: no insert kind %s in the inserts table' % (w, kind)); continue
                    if not isinstance(prints, list) or not prints:
                        errors.append('%s: is a list of prints' % w); continue
                    for i, e in enumerate(prints):
                        errors += _print_entry_errors(e, '%s[%d]' % (w, i))
    return errors


def _as_prints(under):
    """A row's "measured_under" as a list of prints: null -> [], one object -> [it], a list -> the list."""
    if under is None:
        return []
    return list(under) if isinstance(under, list) else [under]


def with_project_tables(reg, root):
    """The registry with the rows of gate/owner/tables.json laid over its tables (project_table_errors says what may be
    set). reg is returned unchanged when the file is absent. RegistryError when the file has a problem: a stock list or a
    measurement that cannot be read stops the gate like a broken parameter. "table_overrides" names the file; the
    registry hash covers what was laid over."""
    import copy
    path = os.path.join(root, 'gate', 'owner', TABLE_OVERRIDES)
    if not os.path.isfile(path):
        return reg
    errors = []
    data = _read(path, errors)
    if errors:
        raise RegistryError(['gate/owner/tables.json: ' + e for e in errors])
    errors = project_table_errors(data, reg)
    if errors:
        raise RegistryError(errors)
    out = dict(reg, tables=copy.deepcopy(reg['tables']), table_overrides='gate/owner/%s' % TABLE_OVERRIDES)
    t = out['tables']
    for name, o in sorted((k, v) for k, v in data.items() if not k.startswith('_')):
        if name == 'pins' and 'pins' in t:
            for d, row in (o.get('sizes') or {}).items():
                key = '%g' % float(d)
                base = t['pins'].setdefault('sizes', {}).get(key) or {'d': float(d)}
                t['pins']['sizes'][key] = dict(base, lengths=sorted(float(x) for x in row['lengths']), stock='project')
        elif name == 'screws':
            for k in ('stock_lengths', 'stock_lengths_set'):
                for size, lengths in (o.get(k) or {}).items():
                    size = table_row_key(k, size, reg)              # "M3.0" is the row M3 of the judges
                    t['screws'].setdefault(k, {})[size] = sorted(float(x) for x in lengths)
                    t['screws'].setdefault('stock_from_project', []).append('%s %s' % (k, size))
        elif name == 'bearings':
            for des, row in (o.get('measured') or {}).items():
                key = des if des in t['bearings']['sizes'] else t['bearings']['aliases'][des]
                t['bearings']['sizes'][key] = dict(t['bearings']['sizes'][key], d=row['d'], D=row['D'], B=row['B'],
                                                   measured={'source': row['source'], 'date': row['date']})
            if isinstance(o.get('seat_fit'), dict):
                sf = t['bearings'].setdefault('seat_fit', {'tag': 'D', 'measured_under': None})
                sf['measured_under'] = _as_prints(sf.get('measured_under')) + list(o['seat_fit']['measured_under'])
                sf['tag'] = 'M'
        elif name == 'inserts':
            for kind, prints in (o.get('measured_under') or {}).items():
                row = t['inserts']['kinds'][kind]
                row['measured_under'] = _as_prints(row.get('measured_under')) + list(prints)
    out['sha256'] = digest({'rules': out['rules'], 'params': out['params'], 'tables': out['tables']}, 64)
    return out


def validate(reg, judges=None):
    """Checks that need the whole registry: every parameter a rule or a formula names is declared, no formula loops, every
    judge exists (when `judges` is given), no judge serves two rules. Returns a list of error texts, empty when sound."""
    errors, seen = [], {}
    for rid in sorted(reg['rules']):
        r = reg['rules'][rid]
        for p in r.get('params') or []:
            if p not in reg['params']:
                errors.append('rule %s: parameter %s is not declared in any file' % (rid, p))
        j = (r.get('check') or {}).get('judge') if isinstance(r.get('check'), dict) else None
        if j:
            if j in seen:
                errors.append('rule %s: judge %s already serves rule %s' % (rid, j, seen[j]))
            seen[j] = rid
            if judges is not None and j not in judges:
                errors.append('rule %s: judge %s does not exist in gate_judges.py' % (rid, j))
    for name in sorted(reg['params']):
        f = reg['params'][name].get('formula')
        if isinstance(f, str):
            try:
                for other in gate_params.formula_names(f):
                    if other not in reg['params']:
                        errors.append('parameter %s: its formula names %s, which is not declared' % (name, other))
            except gate_params.ParamError:
                pass                                   # already reported by _check_param
    if not errors:
        try:
            gate_params.formula_order(reg['params'])
        except gate_params.ParamError as e:
            errors.append(str(e))
    errors += _check_pockets(reg)
    return errors


POCKET_RULES = ('fits.bought_part_pocket', 'print.z_layers')        # the rules that judge the pockets of the pockets table
# rules that only need to KNOW such a pocket, so that they do not take it for something else (a wrong insert pocket,
# a tap-drill hole, a hole of no table): they read "d" and "depth"
POCKET_READERS = ('inserts.pocket_shape', 'inserts.no_tapped_plastic', 'census.unknown_hole', 'inserts.wall', 'inserts.wall_at_relief', 'inserts.floor',
                  'inserts.relief_missing', 'inserts.mouth_reachable', 'access.hole_covered', 'access.mouth_sealed', 'fits.decided_not_printed',
                  'hardware.refs_present', 'print.fit_axis', 'print.relief_layers')
# the name patterns every one of these rules lists too: a hole with an insert or a screw on its axis is never taken for
# a pocket of the table, whatever its diameter (gate_judges._bought_pocket). Held only where the registry declares them
POCKET_NAME_PARAMS = ('inserts.ref_pattern', 'screws.ref_pattern', 'fits.pin_ref_pattern')


def _check_pockets(reg):
    """The pockets table holds together: every kind names declared parameters ("d" and "depth" of class fit, "part_h"
    any), and the rules that read the table list them in "params" (a judge only gets the parameters its rule lists)."""
    table_ = reg['tables'].get('pockets')
    if table_ is None:
        return []
    errors, kinds = [], table_.get('kinds')
    if not isinstance(kinds, dict) or not _is_num((table_.get('tol') or {}).get('d') if isinstance(table_.get('tol'), dict) else None) \
            or not _is_num(table_.get('depth_max_factor')):
        return ['tables/pockets.json: needs "kinds" (object), "tol": {"d": mm} and "depth_max_factor"']
    for kind in sorted(kinds):
        row = kinds[kind]
        if not isinstance(row, dict) or not all(isinstance(row.get(k), str) for k in ('d', 'depth', 'part_h')):
            errors.append('tables/pockets.json: kind %s needs "d", "depth" and "part_h", each the name of a parameter' % kind)
            continue
        for key in ('d', 'depth', 'part_h'):
            name = row[key]
            if name not in reg['params']:
                errors.append('tables/pockets.json: kind %s: parameter %s is not declared in any file' % (kind, name))
                continue
            if key != 'part_h' and reg['params'][name].get('class') != 'fit':
                errors.append('tables/pockets.json: kind %s: "%s" must name a fit parameter, %s is %s' % (kind, key, name, reg['params'][name].get('class')))
            for rid in POCKET_RULES + POCKET_READERS:
                if rid in reg['rules'] and name not in (reg['rules'][rid].get('params') or []) and not (key == 'part_h' and rid != 'print.z_layers'):
                    errors.append('rule %s: reads the pockets table and must list %s (kind %s) in its "params"' % (rid, name, kind))
    pins = table_.get('pins')
    if pins is not None:
        fits = pins.get('fits') if isinstance(pins, dict) else None
        if not isinstance(fits, dict) or not fits or not isinstance(pins.get('pattern'), str) or pins['pattern'] not in reg['params']:
            errors.append('tables/pockets.json: "pins" needs "pattern" (a declared parameter) and "fits" {fit: parameter}')
        else:
            for fit, name in sorted(fits.items()):
                if name not in reg['params'] or reg['params'][name].get('class') != 'fit':
                    errors.append('tables/pockets.json: pins fit %s must name a declared fit parameter, not %r' % (fit, name)); continue
                if 'fits.bought_part_pocket' in reg['rules'] and name not in (reg['rules']['fits.bought_part_pocket'].get('params') or []):
                    errors.append('rule fits.bought_part_pocket: judges pin pockets and must list %s (pins fit %s) in its "params"' % (name, fit))
        if 'pins' not in reg['tables']:
            errors.append('tables/pockets.json: "pins" needs the table rules/tables/pins.json')
    for kind, row in sorted((table_.get('boxes') or {}).items()):
        if not isinstance(row, dict) or not isinstance(row.get('w'), str) or not (row.get('l') is None or isinstance(row.get('l'), str)):
            errors.append('tables/pockets.json: box %s needs "w" (a parameter) and "l" (a parameter or null)' % kind); continue
        for key in ('w', 'l'):
            name = row.get(key)
            if name is None:
                continue
            if name not in reg['params'] or reg['params'][name].get('class') != 'fit':
                errors.append('tables/pockets.json: box %s: "%s" must name a declared fit parameter, not %r' % (kind, key, name)); continue
            if 'fits.bought_part_pocket' in reg['rules'] and name not in (reg['rules']['fits.bought_part_pocket'].get('params') or []):
                errors.append('rule fits.bought_part_pocket: judges box pockets and must list %s (box %s) in its "params"' % (name, kind))
    if (table_.get('boxes') or table_.get('pins')) and not _is_num((table_.get('tol') or {}).get('box') if table_.get('boxes') else 0):
        errors.append('tables/pockets.json: "boxes" needs "tol": {"box": mm}')
    for name in POCKET_NAME_PARAMS:
        for rid in POCKET_RULES + POCKET_READERS:
            if name in reg['params'] and rid in reg['rules'] and name not in (reg['rules'][rid].get('params') or []):
                errors.append('rule %s: reads the pockets table and must list %s in its "params" (a hole that holds an insert or a screw is no pocket for a bought part)' % (rid, name))
    return errors


def rule(reg, rule_id):
    """The rule with that id. GateError when there is none."""
    if rule_id not in reg['rules']:
        raise GateError('no rule %r in the registry' % (rule_id,))
    return reg['rules'][rule_id]


def rules(reg, level=None, stage=None, category=None, needs=None, probe=None, deferred=None):
    """Rules sorted by id, filtered by any of: level, stage, category, check.needs, check.probe, deferred (True | False)."""
    out = []
    for rid in sorted(reg['rules']):
        r = reg['rules'][rid]; c = r.get('check') or {}
        if level is not None and r['level'] != level: continue
        if stage is not None and r['stage'] != stage: continue
        if category is not None and r['category'] != category: continue
        if needs is not None and c.get('needs') != needs: continue
        if probe is not None and c.get('probe') != probe: continue
        if deferred is not None and bool(r.get('deferred')) != deferred: continue
        out.append(r)
    return out


def param_def(reg, name):
    """The definition of a parameter. GateError when the name is not declared."""
    if name not in reg['params']:
        raise GateError('no parameter %r in the registry' % (name,))
    return reg['params'][name]


def table(reg, name):
    """The content of rules/tables/<name>.json. GateError when it was not loaded."""
    if name not in reg['tables']:
        raise GateError('no table %r in the registry' % (name,))
    return reg['tables'][name]


def judge_names(reg):
    """Sorted names of every judge a rule names."""
    return sorted({r['check']['judge'] for r in reg['rules'].values() if isinstance(r.get('check'), dict) and r['check'].get('judge')})


def probes(reg, stage=None, needs=None):
    """Sorted names of the probes that rules with a judge name, optionally only for one stage or one kind of input."""
    return sorted({r['check']['probe'] for r in reg['rules'].values()
                   if isinstance(r.get('check'), dict) and r['check'].get('judge')
                   and (stage is None or r['stage'] == stage) and (needs is None or r['check'].get('needs') == needs)})


def rules_for_probe(reg, probe):
    """Rules (sorted by id) whose judge reads the facts of that probe."""
    return [r for r in rules(reg, probe=probe) if r['check'].get('judge')]


def applies_to(rule_, source):
    """Does a rule apply to a part of that source ("fusion": modelled in a Fusion document, "generated": an STL made by
    code)? A rule that needs Fusion never applies to a generated part; "applies" narrows it further."""
    a = rule_.get('applies', 'all')
    if a != 'all' and a != source:
        return False
    return not (source == 'generated' and (rule_.get('check') or {}).get('needs') == 'fusion')


def missing_fixtures(reg, fixtures_dir, judges=None):
    """Judges of block rules with no fixture file tests/fixtures/facts/<judge>__<case>.json. `judges` limits the question to
    those names (for example the judges that are already built). Returns a sorted list of judge names."""
    have = {f.split('__')[0] for f in os.listdir(fixtures_dir) if f.endswith('.json') and '__' in f} if os.path.isdir(fixtures_dir) else set()
    want = {r['check']['judge'] for r in rules(reg, level='block')}
    if judges is not None:
        want &= set(judges)
    return sorted(want - have)
