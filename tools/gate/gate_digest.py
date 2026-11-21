"""The precomputed digest of a gated project: what the SessionStart hook prints, built by the gate and only READ by the hook.

Why: the digest is gate_report.digest(root), and that judges the stored facts of every part again with today's judges.
In a project with some fifty parts and hundreds of open findings that takes seconds, the hook has well under one, and a
digest that is left out means a new session starts without the state of the gate. So every `gate.py` command that can
change that state builds the digest when it ends and writes it here, with the state of its inputs; the hook reads the
file and checks, by the sizes and modification times of the same inputs, whether it still holds. A digest that no
longer holds is printed with one line that says when it was built and what changed since: never silently, never left out.

    gate/run/digest.json  {"v": 1, "text": the digest, "built": UTC time, "by": "<plugin> <version>" of the code that built
                           it, "tools": the folder of that code, "rules": the rules folder it read,
                           "inputs": {group: 16 hex characters}}

gate/run/ is git-ignored (setup_project writes that rule), so the file is local to one checkout.

The groups of inputs (only sizes and modification times are read, nothing is parsed but gate/layout.json for its STL
folders, so a check of a project with fifty parts takes milliseconds):
    model      gate/census.json (the scan: parts, signatures, documents)
    facts      every file under gate/facts (what the units measured, and the judgement stored with it)
    decisions  gate/project.json, gate/layout.json, gate/fit_settings.json and every file under gate/owner, gate/attest,
               gate/proposals and gate/stamps (parameters, waivers, triage, answers, stamps)
    stls       the files of every STL folder gate/layout.json registers (an STL replaced or written without a stamp)
    run        gate/reports/latest.json, gate/run/current.json, gate/run/scan.json, gate/run/pending.json and the plan of
               the current run (a run that goes on, a call that was sent)
    code       the tools that built the digest and the rules they read (a judge or a rule that changed)
Standard library only: the hook imports this module and nothing else of the gate before it knows the file holds."""
import hashlib, json, os, time

VERSION = 1
GROUPS = ('model', 'facts', 'decisions', 'stls', 'run', 'code')
WORDS = {'model': 'the scan (census)', 'facts': 'the measured facts', 'decisions': 'parameters, answers, waivers or stamps',
         'stls': 'the STL folders', 'run': 'the run in progress', 'code': 'the gate code or its rules'}


def path(root):
    """Where the digest of `root` is kept."""
    return os.path.join(root, 'gate', 'run', 'digest.json')


def _stat(full, rel, out):
    try:
        st = os.stat(full)
        out.append([rel, st.st_size, st.st_mtime_ns])
    except OSError:
        out.append([rel, None, None])


def _walk(base, rel_base, out, suffixes=None):
    """Every file under `base`, sorted, as [relative path, size, mtime_ns]; a missing folder is one entry of its own."""
    if not os.path.isdir(base):
        out.append([rel_base + '/', None, None])
        return
    for dirpath, dirs, names in os.walk(base):
        dirs.sort()
        for n in sorted(names):
            if suffixes and not n.endswith(suffixes):
                continue
            full = os.path.join(dirpath, n)
            _stat(full, rel_base + '/' + os.path.relpath(full, base).replace(os.sep, '/'), out)


def _stl_folders(root):
    try:
        with open(os.path.join(root, 'gate', 'layout.json'), encoding='utf-8') as f:
            layout = json.load(f)
    except (OSError, ValueError):
        return []
    found = []
    for entry in list(layout.get('documents') or []) + list(layout.get('projects') or []) if isinstance(layout, dict) else []:
        d = (entry.get('stl_dir') or '').strip('/') if isinstance(entry, dict) and isinstance(entry.get('stl_dir'), str) else ''
        if d and d not in found:
            found.append(d)
    return sorted(found)


def _current_plan(root):
    try:
        with open(os.path.join(root, 'gate', 'run', 'current.json'), encoding='utf-8') as f:
            run = json.load(f).get('run')
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(run, str) or not run or '/' in run or '\\' in run or run.startswith('.'):
        return None
    return os.path.join('gate', 'run', run, 'plan.json')


def entries(root, tools_dir, rules_dir):
    """{group: [[path, size, mtime_ns], ...]} of the inputs of the digest of `root`."""
    g = os.path.join(root, 'gate')
    out = {k: [] for k in GROUPS}
    _stat(os.path.join(g, 'census.json'), 'gate/census.json', out['model'])
    _walk(os.path.join(g, 'facts'), 'gate/facts', out['facts'])
    for n in ('project.json', 'layout.json', 'fit_settings.json'):
        _stat(os.path.join(g, n), 'gate/' + n, out['decisions'])
    for d in ('owner', 'attest', 'proposals', 'stamps'):
        _walk(os.path.join(g, d), 'gate/' + d, out['decisions'])
    for d in _stl_folders(root):
        _walk(os.path.join(root, *d.split('/')), d, out['stls'], ('.stl', '.STL'))
    for rel in ['gate/reports/latest.json', 'gate/run/current.json', 'gate/run/scan.json', 'gate/run/pending.json'] + ([_current_plan(root).replace(os.sep, '/')] if _current_plan(root) else []):
        _stat(os.path.join(root, *rel.split('/')), rel, out['run'])
    for base, label in ((tools_dir, 'tools'), (rules_dir, 'rules')):
        if base:
            _walk(base, label, out['code'], ('.py', '.json'))
    return out


def inputs(root, tools_dir, rules_dir):
    """{group: 16 hex characters}: the state of each group of inputs."""
    return {k: hashlib.sha256(json.dumps(v, separators=(',', ':')).encode('utf-8')).hexdigest()[:16]
            for k, v in entries(root, tools_dir, rules_dir).items()}


def write(root, text, tools_dir, rules_dir, by, state=None):
    """Keep `text` as the digest of `root`. state: inputs() taken BEFORE the digest was computed (so that a change while
    it was computed reads as stale), else taken now. The file is written whole (a temporary file, then a rename). Never
    raises: a digest that cannot be written is only a digest the hook will call stale or missing."""
    try:
        rec = {'v': VERSION, 'text': text, 'built': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'by': by,
               'tools': os.path.abspath(tools_dir) if tools_dir else None, 'rules': os.path.abspath(rules_dir) if rules_dir else None,
               'inputs': state if isinstance(state, dict) else inputs(root, tools_dir, rules_dir)}
        p = path(root)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + '.%d.tmp' % os.getpid()
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(rec, f, indent=1, sort_keys=True)
        os.replace(tmp, p)
        return True
    except Exception:
        return False


def read(root):
    """The kept digest of `root`, or None when there is none or it cannot be read."""
    try:
        with open(path(root), encoding='utf-8') as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or rec.get('v') != VERSION or not isinstance(rec.get('text'), str) or not isinstance(rec.get('inputs'), dict):
        return None
    return rec


def changed(root, rec):
    """The groups whose inputs differ from the kept digest's, in the order of GROUPS: [] = the digest holds."""
    now = inputs(root, rec.get('tools'), rec.get('rules'))
    return [k for k in GROUPS if (rec.get('inputs') or {}).get(k) != now.get(k)]


def stale_line(rec, groups):
    """The one line printed under a digest that no longer holds."""
    what = ', '.join(WORDS.get(k, k) for k in groups)
    return ('gate: this digest was built at %s and %s changed since: it may be out of date. `python3 gate/tools/gate.py '
            'status` gives the state as it is now (and builds this digest again).' % (rec.get('built') or '?', what))
