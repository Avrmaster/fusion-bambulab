"""What the facts of one unit depend on, and whether that is still the model: the record that makes `run --changed`
measure only what changed.

A unit (one probe on one part) reads more than its own body: the census looks for coaxial neighbours, the openings cast
paths past other bodies, a bearing is covered and clamped by other parts. So "the part did not change" is not enough
for its facts to hold. When a unit is done the host stores beside its facts (gate/facts/<doc>/<part>/<sig12>.json,
probes[<probe>]["deps"]):

    {"v": 2,
     "code":   16 hex over the code of the unit (_geom.py, _geom_rings.py, _fusion.py and the probe file) with its
               comments and docstrings left out (normalised: a text-only edit measures nothing again),
     "fusion": the version of Fusion that measured it (the envelope's "fusion_version"), or None,
     "for":    the key of the .3mf project the unit was run for (its parameters), or None,
     "own":    16 hex over the part's signature, pose and name, or None when the unit read nothing but its body,
     "job":    {key: 16 hex} of the keys of the job the probe read (features, motion_groups, not_printed, args, ...),
     "params": {name: value} of the parameters it read ({"*": 16 hex} when it read all of them),
     "tables": {name: 16 hex} of the tables it read ({"*": 16 hex} for all),
     "reads":  [{"box", "margin", "solid_only"?, "printed_faces_max"?, "mode"?, "kinds"?, "other_group"?, "names"?}]
               the regions of the document it read (the envelope's "reads", joined over the calls of the unit;
               region_admits of probes/_geom_rings.py says which bodies a region holds),
     "bodies": {"<occurrence path>|<body name>": 16 hex} every body of the document in one of the "wide" regions (a
               region with no "mode" counts for both records; the part itself left out), with its signature, pose,
               name and shape as the scan saw them (a mesh body is "<occurrence path>|mesh:<body name>"; a second body
               under one key gets "#2", "#3", ...),
     "bodies_tight": the same over the "tight" regions, only when the unit noted a region with a "mode" (a probe that
               knows more exactly what it reads: fusion_near, fusion_gap, fusion_screws),
     "all":    16 hex over every body of the document, instead of "reads" and "bodies", for a unit that was given up
               over its budget: nobody knows what it would have read, so any change of the document tries it again}

The scan keeps every body of a document in the census (census["docs"][key]["world"], one line per body: world()).
A unit is STALE (why_stale) when its facts at the part's signature are missing, unfinished or an error; when nothing of
this was recorded (facts measured before this file existed); when the probe text, one of the job keys, parameters or
tables it read, or the part's pose or name differ; or when the bodies in its regions differ from the record: a body
changed, moved, was renamed, came into a region or left it; when Fusion was updated since (the scan
gives its version); or when the facts were measured with a look cut short by time (incomplete(): a full check with
the time it needs might see more). Anything else is what was measured, and a fresh plan takes those facts as done
instead of asking Fusion again. A unit given up over its budget (its error begins with GIVEN_UP) holds as given up
while its record holds: it is not sent again on every run (`run --fresh` tries it).

The state of a body that is not a printed part (a ref) includes its "shape" from the scan: a digest over its faces
(area and centroid of each, like a part's signature) or over the nodes of its mesh, so a hole that moves inside the
same outline of a bought plate or washer is a change. The cheap signature alone does not see that.

Visibility is in no state (0.3.3): hiding or showing a part or a ref makes no unit stale, and no probe reads a light bulb
(what a unit measures with a neighbour hidden is what it measures with it shown).

Two records, one decision. "wide" is the record as 0.3.2 made it (a box round what a probe could look at); "tight" is
the one a probe that knows more exactly what it reads adds (regions with "mode": "tight" and filters). Which one makes a
unit stale is gate/project.json "deps" (the owner's): "wide" (the default) or "tight". In "wide" the plan also asks the tight record
(shadow): a unit that only the wide record measures again is compared, when it is done, with its facts before
(gate/run/<run>/deps_shadow.jsonl, shadow_summary): the same facts are the proof that the tight record would have
missed nothing; any difference is a violation, and the owner switches to "tight" only on a record of none.

What this cannot see: a body that changed after the scan and before its neighbour's unit measured it, and changed back
before the next scan (the facts were measured on a state no scan saw). The same holds for a full check: its units read
the model as it is while they run."""
import hashlib, io, os, tokenize

import gate_common, gate_fusion_rpc
from gate_common import digest

DEPS_V = 2                      # 2 (0.3.3): no visibility in a state, the name in it, regions with a mode and filters
DEPS_MODES = ('wide', 'tight')  # gate/layout.json "deps": which record decides (the default is the first)
GIVEN_UP = 'over budget, not run'   # how the error of a unit that was given up begins (gate_plan.OVER_BUDGET)
INCOMPLETE_KEYS = ('screws_cut', 'late')    # a fact that says the probe's look was cut short by time (openings, screws)
LATE_REASON = 'measured with a look cut short by time'
SLACK_MM = 0.5                  # a census box and the box Fusion gives a body in a walk may differ by rounding: regions are this much wider
JOB_INPUTS = ('doc', 'doc_key', 'component_id', 'occurrence', 'body', 'features', 'not_printed', 'motion_groups', 'args', 'target', 'legacy')
WORLD_KINDS = ('part', 'copy', 'ref', 'mesh')
# reasons of why_stale() that are about the measuring tool, not about the model: they plan a measurement again, but
# they kill no stamp by themselves (gate_stamp._neighbours_moved): the facts measured again decide (_measured_since)
TOOL_REASONS = ('no record of what it read', 'the probe changed', 'measured in another version of Fusion', 'its record holds the tables as an older copy')
TABLES_VIEW = 1                 # the view of the tables a record digests (probe_tables); a record without it ("tv") digested them whole


def tool_reason(why):
    """Is this why_stale() text about the measuring tool (the probe's code, the Fusion version, an older record, a
    look cut short by time) rather than about the model?"""
    return isinstance(why, str) and (why.startswith(TOOL_REASONS) or why.startswith(LATE_REASON))


def probe_tables(tables):
    """The tables as a probe gets them (gate_plan._job) and as the record of what a unit read digests them: without
    what only the judges read, so that a print the owner records (gate/owner/tables.json: inserts measured_under, a
    bearing seat fit, the screws and pins he stocks, where a measured bearing came from) measures nothing again. The
    measured d / D / B of a bearing stay: they can change a classification. The judges keep the whole tables (ctx)."""
    import copy
    if not isinstance(tables, dict):
        return tables
    t = copy.deepcopy(tables)
    for row in ((t.get('inserts') or {}).get('kinds') or {}).values() if isinstance(t.get('inserts'), dict) else []:
        if isinstance(row, dict):
            row.pop('measured_under', None)
    b = t.get('bearings')
    if isinstance(b, dict):
        b.pop('seat_fit', None)
        for row in (b.get('sizes') or {}).values() if isinstance(b.get('sizes'), dict) else []:
            if isinstance(row, dict):
                row.pop('measured', None)
    s = t.get('screws')
    if isinstance(s, dict):
        for k in ('stock_lengths', 'stock_lengths_set', 'stock_from_project'):
            s.pop(k, None)
    pins = t.get('pins')
    if isinstance(pins, dict):
        for row in (pins.get('sizes') or {}).values() if isinstance(pins.get('sizes'), dict) else []:
            if isinstance(row, dict):
                row.pop('lengths', None); row.pop('stock', None)
    return t


def body_key(occurrence, body, mesh=False):
    """The name of one body of a document: its occurrence path ("" for the root component) and its body name. A mesh
    body gets "mesh:" before its name: Fusion keeps solid and mesh bodies apart, and both may be called "Body1"."""
    return '%s|%s%s' % (occurrence or '', 'mesh:' if mesh else '', body or '')


def state(sig, pose, shape=None, name=None):
    """16 hex over what a neighbour is to a unit: its signature, its pose, for a ref the digest of its faces or mesh
    nodes ("shape" of the scan), and the name a probe gives it (part_name: a printed body is renamed when another
    printed body is added to its component, and its facts name it). Never whether it is shown."""
    return digest([sig, pose, shape, name])


def search(pattern, text):
    """re.search that never raises, as gate_search of probes/_fusion.py: a pattern that does not compile matches nothing."""
    import re
    try:
        return bool(pattern) and re.search(pattern, text or '') is not None
    except re.error:
        return False


def _box(matrix, bbox):
    from probes import _geom
    try:
        lo, hi = _geom.box_transform(matrix, bbox[0], bbox[1])
    except (TypeError, ValueError, IndexError):
        return None
    return [lo, hi]


def _line(st, kind, faces, box):
    """One body of the world as one line of text (the census stays readable and small)."""
    where = '-' if not box else ','.join('%.3f' % (c + 0.0) for c in list(box[0]) + list(box[1]))
    return '%s %s %s %s' % (st, kind, int(faces) if isinstance(faces, int) and not isinstance(faces, bool) else '-', where)


def parse(line):
    """(state, kind, faces or None, ((lo), (hi)) or None) of one line of world()."""
    st, kind, faces, where = str(line).split(' ')
    box = None
    if where != '-':
        c = [float(x) for x in where.split(',')]
        box = (tuple(c[:3]), tuple(c[3:]))
    return st, kind, (int(faces) if faces != '-' else None), box


def world(facts):
    """{body key: line} of every body a finished fusion_scan saw (its facts "parts", "copy_bodies" and "refs"): the
    state (signature, pose, shape, name; never visibility), the kind (part, copy, ref, mesh), the face count and the box in the document
    frame (a part: its bounding box moved by its matrix; a ref: its "bbox_doc"; a mesh without display mesh: no box).
    A copy of a part has the part's signature. Two bodies that would get one key (two bodies of one name in one
    occurrence) are both kept: the second is "<key>#2", the third "<key>#3", in the order of the scan, so a change of
    either is seen (never one line overwritten by the other)."""
    out, sigs = {}, {}

    def put(key, line):
        k, n = key, 1
        while k in out:
            n += 1
            k = '%s#%d' % (key, n)
        out[k] = line
    for p in facts.get('parts') or []:
        if not isinstance(p, dict):
            continue
        sigs[(p.get('component_id'), p.get('body'))] = p.get('sig')
        put(body_key(p.get('occurrence'), p.get('body')), _line(state(p.get('sig'), p.get('pose'), name=p.get('name')), 'part', p.get('faces'),
                                                               _box(p.get('matrix'), p.get('bbox'))))
    for c in facts.get('copy_bodies') or []:
        if not isinstance(c, dict):
            continue
        put(body_key(c.get('occurrence'), c.get('body')), _line(state(sigs.get((c.get('component_id'), c.get('body'))), c.get('pose'), name=c.get('name')), 'copy',
                                                               c.get('faces'), _box(c.get('matrix'), c.get('bbox'))))
    for r in facts.get('refs') or []:
        if not isinstance(r, dict):
            continue
        box = r.get('bbox_doc')
        put(body_key(r.get('occurrence'), r.get('body'), bool(r.get('mesh'))),
            _line(state(r.get('sig'), r.get('pose'), r.get('shape'), r.get('name')), 'mesh' if r.get('mesh') else 'ref', r.get('faces'), box if box else None))
    return out


class World:
    """The bodies of one document as the census holds them, ready for the region tests."""

    def __init__(self, lines):
        self.rows = []
        self.by_key = {}
        for k in sorted(lines or {}):
            try:
                st, kind, faces, box = parse(lines[k])
            except (ValueError, TypeError):
                continue
            self.by_key[k] = (st, kind, faces, box)
            if box is not None:
                self.rows.append((k, st, kind, faces, box))

    def bodies_in(self, reads, own_key, mode='wide', groups=None):
        """{body key: state} of every body (the part itself left out) that at least one region of `reads` holds
        (region_admits of probes/_geom_rings.py, the test the probes make), counting the regions of this record:
        those without a "mode" and those of `mode` ("wide" or "tight"). groups: the motion groups of the document
        (for "other_group"). None when a region cannot be read: nothing can be proven."""
        from probes import _geom, _geom_rings
        regions = []
        for r in reads or []:
            try:
                if r.get('mode') is not None and r['mode'] not in _geom_rings.REGION_MODES:
                    raise ValueError(r)
                if r.get('mode') not in (None, mode):
                    continue
                lo, hi = tuple(float(c) for c in r['box'][0]), tuple(float(c) for c in r['box'][1])
                m = float(r['margin']) + SLACK_MM
                if len(lo) != 3 or len(hi) != 3:
                    raise ValueError(r)
                regions.append((lo, hi, m, r))
            except (KeyError, TypeError, ValueError, IndexError, AttributeError):
                return None                                 # a region that cannot be read: nothing can be proven
        if not regions:
            return {}
        ulo = tuple(min(r[0][i] - r[2] for r in regions) for i in range(3))       # one box round every region: most bodies end here
        uhi = tuple(max(r[1][i] + r[2] for r in regions) for i in range(3))
        out = {}
        for k, st, kind, faces, box in self.rows:
            if k == own_key or not _geom.boxes_near(box[0], box[1], ulo, uhi, 0.0):
                continue
            for _lo, _hi, _m, r in regions:
                if _geom_rings.region_admits(r, k, kind, faces, box, groups or [], search, SLACK_MM):
                    out[k] = st
                    break
        return out


_CODE = {}


def normalised(text):
    """The code of a Python text without its comments, its docstrings and its blank lines: what a unit runs. The same
    on every Python the host may run (3.9 ... 3.14): the cuts come from token and node positions, which do not differ
    between versions, not from ast.dump. A text that does not parse is returned as it is."""
    import ast
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return text
    lines = text.splitlines()
    cuts = []                                           # (first line, first col, last line, end col), 1-based lines
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(getattr(first, 'value', None), ast.Constant) and isinstance(first.value.value, str):
                cuts.append((first.lineno, first.col_offset, first.end_lineno, first.end_col_offset))
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                cuts.append((tok.start[0], tok.start[1], tok.end[0], tok.end[1]))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text
    for l0, c0, l1, c1 in sorted(cuts, reverse=True):   # from the end: earlier positions stay valid
        if l0 > len(lines) or l1 > len(lines):
            continue
        head, tail = lines[l0 - 1][:c0], lines[l1 - 1][c1:]
        lines[l0 - 1:l1] = [head + tail]
    return '\n'.join(line.rstrip() for line in lines if line.strip())


def code(probe):
    """16 hex over the code a unit of this probe runs (gate_fusion_rpc.unit_text, normalised: an edit of a comment or a
    docstring of _fusion.py does not make every unit of the project stale). Read once per process."""
    if probe not in _CODE:
        names = gate_fusion_rpc.PASTED + (probe + '.py',)
        gate_fusion_rpc.unit_text({'probe': probe})                     # GateError when a file is missing, as for the unit itself
        _CODE[probe] = digest([_file_code(os.path.join(gate_fusion_rpc.probes_dir(), n)) for n in names])
    return _CODE[probe]


_FILES = {}


def _file_code(path):
    """16 hex over the normalised text of one file of a unit (each shared file is read once per process)."""
    if path not in _FILES:
        with open(path, encoding='utf-8') as f:
            _FILES[path] = digest(normalised(f.read()))
    return _FILES[path]


def incomplete(facts):
    """Why these facts were measured with a look cut short by time (INCOMPLETE_KEYS true anywhere in them), or None.
    Such facts depend on the clock of the call, not only on the model: they are measured again."""
    stack = [facts]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            for k, v in x.items():
                if k in INCOMPLETE_KEYS and v is True:
                    return '%s (%s)' % (LATE_REASON, k)
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(x, list):
            stack.extend(v for v in x if isinstance(v, (dict, list)))
    return None


def _file_sha(path):
    try:
        with open(path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except (OSError, TypeError):
        return None


def _job_value(key, value):
    if key == 'legacy':                                     # absolute paths of this machine: the content is what a probe would read
        return {n: _file_sha(p) for n, p in sorted((value or {}).items())}
    return value


def own_state(entry):
    """The part's own signature, pose and name (its census entry). Not whether it is shown."""
    return state(entry.get('sig'), entry.get('pose'), name=entry.get('name'))


def _merge_names(a, b):
    if a is None or b is None:
        return None
    return sorted(set(a) | set(b))


def join_reads(old, env):
    """The record of what the calls of one unit read so far (kept in the plan unit as "read"), with this call's envelope
    added: {"reads": [...], "params": [names] or None, "tables": [...] or None, "job": [...] or None, "complete": bool}.
    "complete" is False as soon as one call did not say what it read (an older unit text): then nothing is recorded."""
    old = old if isinstance(old, dict) else {'reads': [], 'params': [], 'tables': [], 'job': [], 'complete': True}
    reads, got = env.get('reads'), env.get('inputs_read')
    fusion = old.get('fusion')
    if env.get('fusion_version'):
        fusion = env['fusion_version'] if fusion in (None, env['fusion_version']) else '?'      # two versions in one unit: none holds
    if not isinstance(reads, list) or not isinstance(got, dict):
        return dict(old, complete=False, fusion=fusion)
    rows = list(old.get('reads') or [])
    for r in reads:
        if r not in rows:
            rows.append(r)
    return {'reads': rows, 'params': _merge_names(old.get('params'), got.get('params')), 'tables': _merge_names(old.get('tables'), got.get('tables')),
            'job': _merge_names(old.get('job'), got.get('job')), 'complete': bool(old.get('complete', True)), 'fusion': fusion}


def _params_part(vals, names):
    if names is None:
        return {'*': digest(vals)}
    return {n: vals.get(n) for n in names}


def _tables_part(tables, names):
    if names is None:
        return {'*': digest(tables)}
    return {n: digest(tables.get(n)) for n in names}


def _job_part(job, names):
    if names is None:
        names = JOB_INPUTS
    return {k: digest(_job_value(k, job.get(k))) for k in sorted(set(names) & set(JOB_INPUTS))}


def record(read, job, entry, world_lines, probe, target_key=None):
    """The "deps" of a unit that is done, or None when the unit did not say what it read (read["complete"] False).

    read         join_reads() over the calls of the unit
    job          the job of its last call (gate_fusion_rpc.new_job): params, tables and the other inputs as sent
    entry        the part's census entry       world_lines  census["docs"][doc]["world"]
    """
    if not isinstance(read, dict) or not read.get('complete'):
        return None
    w = World(world_lines)
    own_key, groups = body_key(entry.get('occurrence'), entry.get('body')), job.get('motion_groups') or []
    bodies = w.bodies_in(read.get('reads'), own_key, 'wide', groups)
    if bodies is None or (read.get('reads') and not world_lines):
        return None
    out = {'v': DEPS_V, 'tv': TABLES_VIEW, 'code': code(probe), 'fusion': read.get('fusion'), 'for': target_key, 'own': own_state(entry) if read.get('reads') else None,
           'job': _job_part(job, read.get('job')), 'params': _params_part(job.get('params') or {}, read.get('params')),
           'tables': _tables_part(job.get('tables') or {}, read.get('tables')), 'reads': read.get('reads') or [], 'bodies': bodies}
    if any(isinstance(r, dict) and r.get('mode') is not None for r in read.get('reads') or []):
        tight = w.bodies_in(read.get('reads'), own_key, 'tight', groups)
        if tight is None:
            return None
        out['bodies_tight'] = tight
    return out


def record_given_up(read, job, entry, world_lines, probe, target_key=None):
    """The "deps" of a unit that was given up over its budget: what it could not finish depends on everything, so the
    record holds every job key, parameter and table, its own state and "all" (16 hex over every body of the
    document). None when the census holds no bodies of the document."""
    if not world_lines:
        return None
    read = read if isinstance(read, dict) else {}
    return {'v': DEPS_V, 'tv': TABLES_VIEW, 'code': code(probe), 'fusion': read.get('fusion'), 'for': target_key, 'own': own_state(entry),
            'job': _job_part(job, None), 'params': _params_part(job.get('params') or {}, None), 'tables': _tables_part(job.get('tables') or {}, None),
            'reads': None, 'bodies': None, 'all': digest(sorted(world_lines.items()))}


def _diff_bodies(was, now):
    """One short text for the first difference between two {key: state} maps."""
    for k in sorted(set(was) | set(now)):
        if k not in now:
            return '%s left what it read (gone, moved away or renamed)' % k.split('|')[0].rsplit('+', 1)[-1]
        if k not in was:
            return '%s came into what it read (new, or moved close)' % k.split('|')[0].rsplit('+', 1)[-1]
        if was[k] != now[k]:
            return '%s changed, moved or was renamed' % k.split('|')[0].rsplit('+', 1)[-1]
    return None


def deps_mode(project):
    """Which record decides whether a unit is stale: gate/project.json "deps" ("wide" when it is not set). The owner
    sets it (an entry {"kind": "deps", "deps": "tight"} of a bundle he applies), after the shadow record
    (shadow_summary) holds no violation. GateError for any other value: a typo must not pick a record silently."""
    v = (project or {}).get('deps', DEPS_MODES[0])
    if v not in DEPS_MODES:
        raise gate_common.GateError('gate/project.json: "deps" must be one of %s, got %r' % (', '.join(DEPS_MODES), v))
    return v


def why_stale(stored, entry, job, world_lines, probe, worlds=None, fusion=None, whole_tables=None, mode='wide'):
    """None when the stored facts of one unit (the probe's entry of the facts file) still hold for the model as the
    census has it and for the job the host would send now; else a short text: why the unit must run again.

    stored  facts["probes"][probe] or None      entry  the part's census entry
    job     the job the host would send now (its params, tables and inputs; cursor and ids do not matter)
    worlds  a cache {id(world_lines): World} for many calls on one census
    fusion  the version of Fusion the last scan of the document ran in (census docs[key]["fusion"]), or None
    whole_tables  the registry's tables as the judges get them (job["tables"] is the probe view of them). A record
            without "tv" digested a table whole: it is taken as an older copy of the gate only while its digest is
            the one of today's whole table; any other difference is a real change of the table (None: always)
    mode    which record of the bodies decides: "wide" ("bodies") or "tight" ("bodies_tight", when the unit noted
            tight regions; else its wide record decides, it knows no better one)"""
    if not isinstance(stored, dict) or not isinstance(stored.get('facts'), dict):
        return 'not measured at this geometry'
    d = stored.get('deps')
    given_up = str(stored.get('error') or '').startswith(GIVEN_UP)
    if stored.get('error') and not (given_up and isinstance(d, dict) and d.get('all')):
        return 'ended in an error'
    if not stored.get('done', True) and not given_up:
        return 'not finished'
    if not isinstance(d, dict) or d.get('v') != DEPS_V:
        return 'no record of what it read (measured by an older copy of the gate)'
    if d.get('code') != code(probe):
        return 'the probe changed'
    if d.get('fusion') == '?' or (fusion and d.get('fusion') and d['fusion'] != fusion):
        return 'measured in another version of Fusion (%s, now %s)' % (d.get('fusion'), fusion)
    late = None if given_up else incomplete(stored.get('facts'))
    if late:
        return late
    if d.get('own') is not None and d['own'] != own_state(entry):
        return 'the part moved or was renamed'
    jp = d.get('job') or {}
    now = _job_part(job, list(jp))
    for k in sorted(jp):
        if now.get(k) != jp[k]:
            return {'features': 'the census of the part (its holes and openings) changed'}.get(k, 'the job key %r changed' % k)
    params = job.get('params') or {}
    for name, v in sorted((d.get('params') or {}).items()):
        if (digest(params) if name == '*' else params.get(name)) != v:
            return 'the parameters changed' if name == '*' else 'parameter %s changed (%r, now %r)' % (name, v, params.get(name))
    tables = job.get('tables') or {}
    for name, v in sorted((d.get('tables') or {}).items()):
        if (digest(tables) if name == '*' else digest(tables.get(name))) != v:
            if d.get('tv') != TABLES_VIEW and isinstance(whole_tables, dict) and \
                    (digest(whole_tables) if name == '*' else digest(whole_tables.get(name))) == v:
                # recorded before the probes got the tables without what only the judges read (probe_tables): the digest
                # is of the whole table, and that whole table is the same today; only the view differs. A tool reason:
                # measured again, no stamp killed by it. A whole table that changed too is the model's business
                return 'its record holds the tables as an older copy of the gate digested them (%s): measured again' % ('all' if name == '*' else 'table %s' % name)
            return 'the tables changed' if name == '*' else 'table %s changed' % name
    if d.get('all'):
        if not world_lines:
            return 'the census holds no bodies of the document (scan again)'
        if digest(sorted(world_lines.items())) != d['all']:
            return 'it was given up over its budget, and the document changed since'
        return None
    if d.get('reads'):
        if not world_lines:
            return 'the census holds no bodies of the document (scan again)'
        key = id(world_lines)
        w = (worlds or {}).get(key)
        if w is None:
            w = World(world_lines)
            if worlds is not None:
                worlds[key] = w
        use = 'tight' if (mode == 'tight' and isinstance(d.get('bodies_tight'), dict)) else 'wide'
        bodies = w.bodies_in(d['reads'], body_key(entry.get('occurrence'), entry.get('body')), use, job.get('motion_groups') or [])
        if bodies is None:
            return 'a region it read cannot be read back'
        was = (d.get('bodies_tight') if use == 'tight' else d.get('bodies')) or {}
        if bodies != was:
            return _diff_bodies(was, bodies)
    elif d.get('bodies'):
        return 'its record is not complete'
    return None


# ---- the shadow of the tight record: proof before the switch ----------------------------------------------------------------

SHADOW_FILE = 'deps_shadow.jsonl'


def facts_view(facts):
    """The facts of a unit as a shadow compares them: canonical JSON with nothing a call adds beside the measurement
    ("timing", "elapsed_s", "notes", "late" marks are not in the facts; "timing" is left out should a probe put it in)."""
    import json
    def clean(x):
        if isinstance(x, dict):
            return {k: clean(v) for k, v in x.items() if k not in ('timing',)}
        if isinstance(x, list):
            return [clean(v) for v in x]
        return x
    return json.dumps(clean(facts if isinstance(facts, dict) else {}), sort_keys=True)


def shadow_row(part, probe, why_wide, old_facts, new_facts):
    """One line of deps_shadow.jsonl: a unit the tight record would have kept, measured because the wide one said
    `why_wide`. "same": its new facts are its old facts (the tight record missed nothing). Else "violation": True and
    the top-level keys whose values differ."""
    a, b = facts_view(old_facts), facts_view(new_facts)
    row = {'part': part, 'probe': probe, 'why_wide': why_wide, 'same': a == b, 'time': gate_common.now()}
    if a != b:
        import json
        o, n = json.loads(a), json.loads(b)
        row['violation'] = True
        row['keys'] = sorted(k for k in set(o) | set(n) if o.get(k) != n.get(k))
    return row


def shadow_summary(root):
    """{"units", "same", "violations": [rows], "runs", "parts", "docs"} over every gate/run/*/deps_shadow.jsonl: how far
    the proof for "deps": "tight" has come. A line that cannot be read counts as a violation (nothing is proven by it)."""
    import glob, json
    out = {'units': 0, 'same': 0, 'violations': [], 'runs': 0, 'parts': set(), 'docs': set()}
    for path in sorted(glob.glob(os.path.join(gate_common.gate_path(root, 'run'), '*', SHADOW_FILE))):
        out['runs'] += 1
        try:
            with open(path, encoding='utf-8') as f:
                lines = [l for l in f.read().splitlines() if l.strip()]
        except OSError:
            lines = ['?']
        for line in lines:
            out['units'] += 1
            try:
                row = json.loads(line)
            except ValueError:
                row = {'violation': True, 'why': 'a line of %s cannot be read' % path}
            if row.get('same') is True:
                out['same'] += 1
            else:
                out['violations'].append(row)
            if isinstance(row.get('part'), str):
                out['parts'].add(row['part'])
                out['docs'].add(row['part'].split('/')[0])
    out['parts'], out['docs'] = sorted(out['parts']), sorted(out['docs'])
    return out
