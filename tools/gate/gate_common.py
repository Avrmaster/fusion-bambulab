"""Shared plumbing of the gate: exit codes, the error type, where things live, JSON files, hashes, part ids.

Every gate module imports this one and nothing here imports another gate module. Standard library only.

Layouts this file knows:
    plugin repository     tools/gate/*.py, tools/gate/probes/, rules/, tools/bambu/meshid.py, tools/ledger/ledger.py
    a project             gate/tools/*.py, gate/tools/probes/, gate/rules/, bambulab/tools/meshid.py, bambulab/tools/ledger.py
The project root is the nearest folder above a path that holds gate/project.json."""
import datetime, hashlib, importlib.util, json, os, posixpath, re, sys, zipfile

EXIT_PASS, EXIT_BLOCK, EXIT_ATTEST, EXIT_MORE, EXIT_INFRA = 0, 1, 2, 3, 4
VERDICTS = ('FAIL', 'GROW', 'BLOCKED', 'NOT_RUN', 'INFO')
BLOCKING = ('FAIL', 'GROW', 'BLOCKED', 'NOT_RUN')      # INFO never stops a stamp
_MISSING = object()


class GateError(Exception):
    """Infrastructure trouble: a broken file, an unknown name, Fusion not answering. gate.py prints it and exits 4."""


def tools_dir():
    """The folder this file lies in: tools/gate in the plugin, gate/tools in a project."""
    return os.path.dirname(os.path.abspath(__file__))


def rules_dir():
    """The rules folder that belongs to this copy of the tools: gate/rules beside a vendored copy, rules/ in the plugin."""
    here = tools_dir()
    for cand in (os.path.join(here, '..', 'rules'), os.path.join(here, '..', '..', 'rules')):
        if os.path.isdir(os.path.join(cand, 'registry')):
            return os.path.normpath(cand)
    raise GateError('no rules/registry folder beside %s' % here)


def find_root(start='.'):
    """Nearest folder at or above `start` (a file or a folder, it need not exist yet) that holds gate/project.json, or None."""
    d = os.path.abspath(start)
    if not os.path.isdir(d):
        d = os.path.dirname(d)
    while True:
        if os.path.isfile(os.path.join(d, 'gate', 'project.json')):
            return d
        up = os.path.dirname(d)
        if up == d:
            return None
        d = up


def gate_path(root, *parts):
    """<root>/gate/<parts...>."""
    return os.path.join(root, 'gate', *parts)


def rel(root, path):
    """`path` relative to the project root with forward slashes: the form paths have inside every gate file."""
    return os.path.relpath(os.path.abspath(path), root).replace(os.sep, '/')


def read_json(path, default=_MISSING):
    """The parsed file. A missing file returns `default` when one is given; a file that is not JSON is always a GateError."""
    if not os.path.exists(path):
        if default is _MISSING:
            raise GateError('missing file: %s' % path)
        return default
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except ValueError as e:
        raise GateError('%s is not valid JSON: %s' % (path, e))


def write_json(path, data):
    """Write through a temporary file and os.replace, so a reader never sees half a file. Sorted keys, one-space indent."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = '%s.tmp%d' % (path, os.getpid())
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1, sort_keys=True, ensure_ascii=False)
        f.write('\n')
    os.replace(tmp, path)
    return path


def append_jsonl(path, obj):
    """Append one object as one line. The folder is made when it is missing."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(obj, sort_keys=True, ensure_ascii=False) + '\n')


def read_jsonl(path):
    """Every line of the file as an object; a missing file is an empty list; a line that is not JSON is skipped."""
    out = []
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
    return out


def canonical(obj):
    """One text for one value: sorted keys, no spaces. The input of every hash in the gate."""
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def digest(obj, n=16):
    """First `n` hex characters of sha256 over canonical(obj)."""
    return hashlib.sha256(canonical(obj).encode()).hexdigest()[:n]


def sha256_file(path):
    """sha256 (64 hex) of the bytes of a file."""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def slug(name):
    """'Motor plate (left)' -> 'motor_plate_left'. Lower case, every run of other characters becomes one underscore."""
    s = re.sub(r'[^a-z0-9]+', '_', str(name).lower()).strip('_')
    return s or 'part'


def part_id(doc_key, name):
    """'<doc key>/<slug>': the id of a part in every gate file and path."""
    return '%s/%s' % (doc_key, slug(name))


def split_part_id(pid):
    """('demo', 'motor_plate') from 'demo/motor_plate'."""
    if not isinstance(pid, str) or pid.count('/') != 1 or not all(pid.split('/')):
        raise GateError('part id must be <doc key>/<slug>, got %r' % (pid,))
    return tuple(pid.split('/'))


def sig12(sig):
    """The first 12 characters of a signature: the file name of facts and stamps."""
    return str(sig)[:12]


def now():
    """UTC time as 2031-02-03T10:12:07Z."""
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def new_run_id():
    """20310203T101207Z-3fa2: the time plus four random hex characters."""
    return '%s-%s' % (datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'), os.urandom(2).hex())


# ---- project files -------------------------------------------------------------------------------------------------

PROJECT_DEFAULTS = {'mode': 'observe', 'not_printed': [], 'targets': {}}
LAYOUT_DEFAULTS = {'documents': [], 'projects': [], 'generators': [], 'templates': ['bambulab/template.3mf'],
                   'fusion': {'endpoint': 'http://127.0.0.1:27182/mcp', 'budget_s': 20}}


def load_project(root):
    """gate/project.json (OWNER) with the defaults filled in: {"mode": "observe" | "enforce", "not_printed": [regex],
    "targets": {<.3mf project key>: {"nozzle": mm, "line_width": mm, "material": text}}}."""
    data = dict(PROJECT_DEFAULTS)
    data.update(read_json(gate_path(root, 'project.json')))
    if data['mode'] not in ('observe', 'enforce'):
        raise GateError('gate/project.json: mode must be "observe" or "enforce", got %r' % (data['mode'],))
    if data.get('questions', 'on') not in ('on', 'off'):
        raise GateError('gate/project.json: questions must be "on" or "off", got %r' % (data.get('questions'),))
    return data


def questions_off(root):
    """True when the project switched the questions off (gate/project.json "questions": "off"): the gate then decides by
    measurement alone. No question is asked, no probe runs only for a question, a finding that would ask becomes an open
    row (INFO stays INFO), and `attest answer` is refused. Answers stored before still close what they closed: they are
    recorded data, replayed the same way every time."""
    if not root:
        return False
    try:
        data = read_json(gate_path(root, 'project.json'), {})
    except Exception:
        return False
    return isinstance(data, dict) and data.get('questions') == 'off'


def stl_name_error(name):
    """Why `name` cannot be a value of a document's "stl_names", or None: it is a plain file name inside the document's
    "stl_dir" that ends with ".stl", printable ASCII, with no "/" and no "\\", none of < > : " | ? * and no space or
    dot in front (names that cannot be written on every file system)."""
    if not isinstance(name, str) or not name:
        return 'the value must be a file name'
    if not all(' ' <= c <= '~' for c in name):
        return 'only printable ASCII characters'
    if '/' in name or '\\' in name:
        return 'a file name inside "stl_dir", with no "/" and no "\\"'
    if name in ('.', '..') or not name.lower().endswith('.stl') or len(name) < 5:
        return 'it must be <file name>.stl'
    if any(c in name for c in '<>:"|?*'):
        return 'none of < > : " | ? * (such a name cannot be written on every file system)'
    if name[0] in ' .':
        return 'it may not start with a space or a dot'
    return None


def stl_of(doc, name):
    """The STL of the Fusion part `name` of one document of gate/layout.json, relative to the project root:
    "<stl_dir>/<file>". The file is the document's "stl_names" entry for the part (its name exactly as the census shows
    it first, then its slug), else "<slug>.stl". None when the document names no "stl_dir".
    The census keeps this path per part ("stl", written by the scan): everything else reads it from there."""
    folder = doc.get('stl_dir')
    if not folder:
        return None
    names = doc.get('stl_names') or {}
    mapped = names.get(name) if isinstance(name, str) else None
    return '%s/%s' % (folder.rstrip('/'), mapped or names.get(slug(name)) or slug(name) + '.stl')


def stl_key(path):
    """One text for one STL file, however it is spelled: forward slashes, no "./", lower case (a file system may ignore
    the letter case, so two paths that differ only in case are one file here)."""
    return posixpath.normpath(str(path).replace('\\', '/')).lower()


def stl_map_refusals(doc, names, parts):
    """{part id: (the path "stl_names" gives it, the part id whose file that is, default_only)} for every Fusion part
    of one document whose mapped file is another part's:
      - the default file ("<part slug>.stl", letter case ignored) of ANOTHER part of the document. default_only is True
        when that part is itself mapped to another file: the name is free on disk, but a file named after one part is
        never given to another;
      - the "stl" of a live part of another document, or of a part made by code (they may share the STL folder).
    names: {part id: part name as the scan shows it} of the Fusion parts of the document; parts: census["parts"]."""
    out = {}
    if not (doc.get('stl_names') and doc.get('stl_dir')):
        return out
    key, plain = doc.get('key'), dict(doc, stl_names={})
    default = {stl_key(stl_of(plain, names[pid])): pid for pid in names}
    elsewhere = {stl_key(e['stl']): pid for pid, e in sorted((parts or {}).items(), reverse=True)
                 if isinstance(e, dict) and e.get('stl') and not e.get('gone') and not (e.get('doc') == key and e.get('source', 'fusion') == 'fusion')}
    for pid in sorted(names):
        want = stl_of(doc, names[pid])
        if want == stl_of(plain, names[pid]):
            continue
        other = default.get(stl_key(want))
        if other and other != pid:
            out[pid] = (want, other, stl_of(doc, names[other]) != stl_of(plain, names[other]))
        elif not other and stl_key(want) in elsewhere:
            out[pid] = (want, elsewhere[stl_key(want)], False)
    return out


def shared_stls(census, layout=None):
    """{STL path: [part ids]} for every file that two parts name as their STL, letter case ignored: the "stl" of every
    part of the census that is not gone, and of every generator of gate/layout.json. An export of one such part
    overwrites the file of the other."""
    claims = {}
    for pid, e in sorted((census.get('parts') or {}).items()):
        if isinstance(e, dict) and e.get('stl') and not e.get('gone'):
            claims.setdefault(stl_key(e['stl']), {})[pid] = e['stl']
    for g in (layout or {}).get('generators') or []:
        if isinstance(g, dict) and g.get('stl') and isinstance(g.get('part'), str):
            claims.setdefault(stl_key(g['stl']), {}).setdefault(g['part'], g['stl'])
    return {sorted(v.values())[0]: sorted(v) for v in claims.values() if len(v) > 1}


def load_layout(root):
    """gate/layout.json (agent) with the defaults filled in: {"documents": [{"key", "name", "stl_dir", "motion_groups",
    "stl_names"}], "projects": [{"key", "file", "stl_dir", "build_table"}], "generators": [{"part", "stl", "source"}],
    "templates": [.3mf files that hold settings and no mesh: the empty project a build starts from],
    "fusion": {"endpoint", "budget_s"}}. A missing file is the defaults.

    A document "key" holds letters, digits, "_" and "-" only (it is a folder name and half of every part id).
    "stl_names" is optional, for a project whose STL files already have their own names: {<part name exactly as the
    census shows it, or its part slug>: "<file name>.stl"}. The file lies in the document's "stl_dir" (stl_name_error
    says what a value may be), two keys may not name one file, and a value may not be the STL of a generator. The next
    scan writes the mapped path into the census (stl_of); a part that is not in the map keeps "<stl_dir>/<slug>.stl".
    A map that breaks a rule is a GateError."""
    data = json.loads(json.dumps(LAYOUT_DEFAULTS))
    got = read_json(gate_path(root, 'layout.json'), {})
    fusion = dict(data['fusion']); fusion.update(got.get('fusion') or {})
    data.update(got); data['fusion'] = fusion
    keys = [d.get('key') for d in data['documents']]
    if len(keys) != len(set(keys)) or not all(keys):
        raise GateError('gate/layout.json: every document needs its own "key"')
    if not all(isinstance(k, str) and re.fullmatch(r'[A-Za-z0-9_-]+', k) for k in keys):
        raise GateError('gate/layout.json: a document "key" may hold only letters, digits, "_" and "-"')
    made = {stl_key(g['stl']): g for g in data.get('generators') or [] if isinstance(g, dict) and g.get('stl')}
    for d in data['documents']:
        _check_stl_names(d, made)
    return data


def _check_stl_names(doc, made=None):
    names = doc.get('stl_names')
    if names is None:
        return
    where = 'gate/layout.json: "stl_names" of document %r' % (doc.get('key'),)
    if not isinstance(names, dict):
        raise GateError('%s must be an object {part name or slug: "<file name>.stl"}' % where)
    if names and not doc.get('stl_dir'):
        raise GateError('%s needs the document\'s "stl_dir": the files lie in that folder' % where)
    files = {}
    for key in sorted(names):
        if not key.strip():
            raise GateError('%s: a key is the name of a part or its slug, not an empty text' % where)
        why = stl_name_error(names[key])
        if why:
            raise GateError('%s: %r -> %r: %s' % (where, key, names[key], why))
        same = files.setdefault(names[key].lower(), key)                 # one file on a file system that ignores case
        if same != key:
            raise GateError('%s: %r and %r both name the file %s: one part, one file' % (where, same, key, names[key]))
        gen = (made or {}).get(stl_key('%s/%s' % (doc['stl_dir'].rstrip('/'), names[key])))
        if gen:
            raise GateError('%s: %r -> %r is the STL of the generator of %s (%s): an export of the Fusion part would overwrite the generated mesh'
                            % (where, key, names[key], gen.get('part'), gen.get('stl')))


ASK_WANTED = (('edit', 'an ask rule for edits of gate/owner, for example "Edit(/gate/owner/**)"'),
              ('bash', 'an ask rule for the owner commands, for example "Bash(*gate.py owner *)"'))


def ask_rules_missing(root):
    """Which permission rules of templates/gate/settings.json are NOT in the project's .claude/settings.json (or
    settings.local.json): a list of texts, [] when both kinds are there. The rules are what puts a prompt in front of
    the owner before an agent writes gate/owner/* or runs `gate.py owner ...`; a "deny" rule counts as well.
    A settings file that cannot be read counts as holding no rule."""
    rules = []
    for name in ('settings.json', 'settings.local.json'):
        try:
            data = read_json(os.path.join(root, '.claude', name), {})
        except GateError:
            continue
        perms = data.get('permissions') if isinstance(data, dict) else None
        for kind in ('ask', 'deny', 'allow'):
            got = perms.get(kind) if isinstance(perms, dict) else None
            rules += [r for r in got if isinstance(r, str)] if isinstance(got, list) else []
    have = {'edit': any(r.startswith(('Edit(', 'Write(')) and 'gate/owner' in r for r in rules),
            'bash': any(r.startswith('Bash(') and 'gate.py' in r and 'owner' in r for r in rules)}
    return [text for key, text in ASK_WANTED if not have[key]]


OBSOLETE_ASK = 'Bash(*gate.py * owner *)'     # the broad ask rule of the 0.2 template: it prompted the owner on ordinary gate commands


def obsolete_rules(root):
    """Permission rules of an older template that the project's .claude/settings.json (or settings.local.json) still
    holds: the broad ask rule on any gate.py command with "owner" in it, and "ask" rules for edits of gate/owner or
    gate/project.json, which the template now denies. [] when there is none."""
    out = []
    for name in ('settings.json', 'settings.local.json'):
        try:
            data = read_json(os.path.join(root, '.claude', name), {})
        except GateError:
            continue
        perms = data.get('permissions') if isinstance(data, dict) else None
        ask = perms.get('ask') if isinstance(perms, dict) and isinstance(perms.get('ask'), list) else []
        for r in ask:
            if isinstance(r, str) and (r == OBSOLETE_ASK or (r.startswith(('Edit(', 'Write(')) and ('gate/owner' in r or 'gate/project.json' in r))):
                out.append('%s: "ask": %s' % (name, r))
    return out


_SKIP_DIRS = ('node_modules', '__pycache__', 'gate')


def _holds_mesh(path):
    """Does this .3mf hold a mesh? True also when it cannot be read: what is not known to be empty is not a template."""
    try:
        with zipfile.ZipFile(path) as z:
            return any(n.lower().endswith('.model') and b'<vertex' in z.read(n) for n in z.namelist())
    except Exception:
        return True


def unregistered(root, layout, limit=20000):
    """What lies in the project tree and is in no entry of gate/layout.json:
    {"projects": [.3mf files, relative], "stl_dirs": [folders that hold .stl files, relative], "complete": bool}.
    A file named under "templates" (default: bambulab/template.3mf, the empty project a build starts from) is not
    listed while it holds no mesh: there is nothing in it to verify. One that holds a mesh is listed like any other.
    Folders whose name starts with a dot (backups, caches), node_modules, __pycache__ and gate/ are not walked; the walk
    stops after `limit` files ("complete": False). A registered STL folder is a document's or a project's "stl_dir" or
    the folder of a generator's STL. The print ledger's own folder (bambulab/ledger: as-printed copies and the STL of
    every write) is not walked either: those files are the ledger's records, never an export folder."""
    real = lambda p: os.path.realpath(p if os.path.isabs(p) else os.path.join(root, *str(p).split('/')))
    projects = {real(e['file']) for e in layout.get('projects') or [] if isinstance(e, dict) and e.get('file')}
    stl_dirs = {real(e['stl_dir']) for key in ('documents', 'projects') for e in layout.get(key) or [] if isinstance(e, dict) and e.get('stl_dir')}
    stl_dirs |= {os.path.dirname(real(g['stl'])) for g in layout.get('generators') or [] if isinstance(g, dict) and g.get('stl')}
    templates = {real(t) for t in layout.get('templates') or [] if isinstance(t, str) and t}
    out, seen = {'projects': [], 'stl_dirs': [], 'complete': True}, 0
    ledger = os.path.realpath(os.path.dirname(events_path(root)))
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in _SKIP_DIRS and os.path.realpath(os.path.join(folder, d)) != ledger)
        seen += len(files)
        if seen > limit:
            out['complete'] = False
            break
        here = os.path.realpath(folder)
        for f in sorted(files):
            full = os.path.realpath(os.path.join(folder, f))
            if f.lower().endswith('.3mf') and full not in projects and not (full in templates and not _holds_mesh(full)):
                out['projects'].append(rel(root, os.path.join(folder, f)))
        if here not in stl_dirs and any(f.lower().endswith('.stl') for f in files):
            out['stl_dirs'].append(rel(root, folder))
    return out


_ZIP_WRITE = re.compile(r"""ZipFile\s*\((?:[^()]|\([^()]*\))*?,\s*(?:mode\s*=\s*)?['"][wax]b?['"]""")


def zip_writers(root, limit=50000):
    """Python files of the project tree that write a zip archive themselves and name a .3mf: {"files": [relative paths],
    "complete": bool}. Every .3mf of a gated project is written through bambu3mf.save(), the one writer that runs the
    guard and tells the ledger; a script that opens zipfile.ZipFile(..., 'w') on a .3mf by itself bypasses both.
    bambu3mf.py itself, gate/, folders whose name starts with a dot, node_modules, __pycache__ and a folder that is a
    repository of its own (it holds .git: vendored sources) are not looked at; the walk stops after `limit` files."""
    out, seen = {'files': [], 'complete': True}, 0
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in _SKIP_DIRS
                         and not os.path.exists(os.path.join(folder, d, '.git')))
        seen += len(files)
        if seen > limit:
            out['complete'] = False
            break
        for f in sorted(files):
            if not f.endswith('.py') or f == 'bambu3mf.py':
                continue
            full = os.path.join(folder, f)
            try:
                with open(full, encoding='utf-8', errors='replace') as fh:
                    text = fh.read(2000000)
            except OSError:
                continue
            if '.3mf' in text and _ZIP_WRITE.search(text):
                out['files'].append(rel(root, full))
    return out


def document_entry(layout, doc_key):
    """The entry of gate/layout.json "documents" with that key."""
    for d in layout['documents']:
        if d.get('key') == doc_key:
            return d
    raise GateError('gate/layout.json has no document with key %r' % (doc_key,))


def target_of(project, project_key=None):
    """The declared target of a .3mf project: {"nozzle", "line_width", "material"}. With no key: the only declared target, or
    None when none or several are declared (the caller must then name one)."""
    targets = project.get('targets') or {}
    if project_key is not None:
        if project_key not in targets:
            raise GateError('gate/project.json declares no target for the project %r' % (project_key,))
        return targets[project_key]
    return list(targets.values())[0] if len(targets) == 1 else None


def target_key_of(project, project_key=None):
    """The key of the declared target target_of() gives: the key handed over, else the key of the only declared target,
    else None."""
    targets = project.get('targets') or {}
    if project_key is not None:
        return project_key if project_key in targets else None
    return list(targets)[0] if len(targets) == 1 else None


# ---- where the files of one part live ---------------------------------------------------------------------------------

def census_path(root):
    """gate/census.json."""
    return gate_path(root, 'census.json')


def facts_path(root, part, sig):
    """gate/facts/<doc>/<part>/<sig12>.json: the facts of one checked geometry."""
    doc, name = split_part_id(part)
    return gate_path(root, 'facts', doc, name, sig12(sig) + '.json')


def stamp_path(root, part, sig):
    """gate/stamps/<doc>/<part>/<sig12>.json."""
    doc, name = split_part_id(part)
    return gate_path(root, 'stamps', doc, name, sig12(sig) + '.json')


def attest_path(root, part):
    """gate/attest/<doc>/<part>.json: the attest answers of one part."""
    doc, name = split_part_id(part)
    return gate_path(root, 'attest', doc, name + '.json')


def run_dir(root, run):
    """gate/run/<run>/: plan.json and units/ of one run (git-ignored)."""
    return gate_path(root, 'run', run)


def events_path(root):
    """The one journal of what was written into .3mf files: the ledger's events.jsonl. The gate reads bindings from it."""
    return os.path.join(root, 'bambulab', 'ledger', 'events.jsonl')


OUTSIDE_HASH = re.compile(r'^[0-9a-f]{40}$')


def outside_path(root):
    """gate/owner/outside.json (OWNER): the objects of a .3mf project that the owner declared outside the gate's coverage."""
    return gate_path(root, 'owner', 'outside.json')


def load_outside(root):
    """gate/owner/outside.json as {"projects": {<.3mf project key>: {"file", "objects": {<object name>: {"geom_hash",
    "triangles", "plate", "reason", "quote", "date", ...}}}}}; a missing file is {"projects": {}}. Written only by
    `gate.py owner outside`. GateError when the file is not JSON or not of that shape: a broken owner file never lets
    anything through (the guard refuses in enforce mode)."""
    data = read_json(outside_path(root), {})
    if not isinstance(data, dict) or not isinstance(data.get('projects', {}), dict):
        raise GateError('gate/owner/outside.json must be an object with "projects": {<project key>: {"file", "objects": {...}}}')
    for key, p in (data.get('projects') or {}).items():
        if not isinstance(p, dict) or not isinstance(p.get('objects', {}), dict):
            raise GateError('gate/owner/outside.json: project %r must be an object with "objects": {<object name>: {...}}' % key)
    data.setdefault('projects', {})
    return data


def declared_outside(root, key, data=None):
    """{object name: entry} of the objects the owner declared outside the gate in the .3mf project `key` (a key of
    gate/layout.json "projects"); {} for no key. Only an entry with a geom_hash of 40 hex characters counts: an entry
    without one lets nothing through. GateError as load_outside()."""
    if not key:
        return {}
    data = data if data is not None else load_outside(root)
    objects = ((data.get('projects') or {}).get(key) or {}).get('objects') or {}
    return {n: e for n, e in objects.items() if isinstance(n, str) and isinstance(e, dict) and OUTSIDE_HASH.match(str(e.get('geom_hash') or ''))}


def load_census(root):
    """gate/census.json, or the empty census {"docs": {}, "parts": {}} when nothing was scanned yet."""
    data = read_json(census_path(root), {})
    data.setdefault('docs', {}); data.setdefault('parts', {})
    return data


def load_facts(root, part, sig):
    """The facts file of one checked geometry, or the empty one: {"part", "sig", "probes": {}, "judged": {}}."""
    data = read_json(facts_path(root, part, sig), {})
    data.setdefault('part', part); data.setdefault('sig', sig); data.setdefault('probes', {}); data.setdefault('judged', {})
    return data


def save_probe_facts(root, part, sig, envelope):
    """Store the result of one finished unit under "probes"[<probe name>] of the part's facts file. The stored entry is the
    envelope without "part", "sig", "probe" (they are the file's own keys), "open_documents" (the names of the other
    open documents never go into a file that is committed) and "asked" / "late" (which call wrote the result file:
    read by the host to adopt a late answer, of no use after it) and "reads" / "inputs_read" (what the unit read: kept
    as "deps", gate_deps.record), with facts["stl"] relative to the root, plus "time". Returns the path."""
    data = load_facts(root, part, sig)
    entry = {k: v for k, v in envelope.items() if k not in ('part', 'sig', 'probe', 'open_documents', 'asked', 'late', 'reads', 'inputs_read', 'fusion_version')}
    if entry.get('deps') is None:
        entry.pop('deps', None)
    facts = entry.get('facts')
    if isinstance(facts, dict) and isinstance(facts.get('stl'), str) and os.path.isabs(facts['stl']):
        entry['facts'] = dict(facts, stl=rel(root, facts['stl']))
    entry['time'] = now()
    data['probes'][envelope['probe']] = entry
    if envelope.get('doc_version') is not None:
        data['doc_version'] = envelope['doc_version']
    return write_json(facts_path(root, part, sig), data)


def save_judged(root, part, sig, rule, judge_hash, params, findings, input_key=None):
    """Store what one judge said about the stored facts under "judged"[<rule id>]:
    {"judge_hash", "params": {name: value}, "findings": [...], "time"} and "input" (16 hex over everything the judge
    read, gate_plan.judge_part) when given. Returns the path."""
    row = {'judge_hash': judge_hash, 'params': params, 'findings': findings, 'time': now()}
    if input_key is not None:
        row['input'] = input_key
    return save_judged_many(root, part, sig, {rule: row})


def save_judged_many(root, part, sig, rows):
    """Store several judgements of one geometry in one write: {rule id: the row save_judged() stores}."""
    data = load_facts(root, part, sig)
    data['judged'].update(rows)
    return write_json(facts_path(root, part, sig), data)


# ---- the sibling tools that are not in this folder ------------------------------------------------------------------------

_SIBLINGS = {
    'meshid': (('..', 'bambu', 'meshid.py'), ('..', '..', 'bambulab', 'tools', 'meshid.py')),
    'ledger': (('..', 'ledger', 'ledger.py'), ('..', '..', 'bambulab', 'tools', 'ledger.py')),
    'bambu3mf': (('..', 'bambu', 'bambu3mf.py'), ('..', '..', 'bambulab', 'tools', 'bambu3mf.py')),
}


def fusion_tool_path(name):
    """Absolute path of tools/fusion/<name>.py (insert_pockets, fusion_parts, screw_access) in this layout, or None:
    plugin tools/fusion/, project fusion/tools/. Fusion probes that wrap one of them get the path in JOB["legacy"]."""
    for parts in (('..', 'fusion', name + '.py'), ('..', '..', 'fusion', 'tools', name + '.py')):
        path = os.path.normpath(os.path.join(tools_dir(), *parts))
        if os.path.isfile(path):
            return path
    return None


def sibling(name):
    """The module `meshid`, `ledger` or `bambu3mf`, wherever this layout keeps it (plugin: tools/bambu, tools/ledger;
    project: bambulab/tools). Loaded once and kept in sys.modules under its plain name. GateError when it is not there."""
    if name in sys.modules:
        return sys.modules[name]
    if name not in _SIBLINGS:
        raise GateError('unknown sibling tool %r' % (name,))
    for parts in _SIBLINGS[name]:
        path = os.path.normpath(os.path.join(tools_dir(), *parts))
        if os.path.isfile(path):
            folder = os.path.dirname(path)
            if folder not in sys.path:
                sys.path.append(folder)          # a sibling may import its own neighbours
            spec = importlib.util.spec_from_file_location(name, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            try:
                spec.loader.exec_module(mod)
            except BaseException:
                sys.modules.pop(name, None)
                raise
            return mod
    raise GateError('%s.py is not vendored beside the gate tools (looked from %s)' % (name, tools_dir()))
