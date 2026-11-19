#!/usr/bin/env python3
"""The print ledger: an append-only record of what was written into .3mf projects and what was printed.
Standard library only, no network, no credentials. Read-only towards every .3mf. It reads what was printed; it never
starts, stops or changes a print.

    python3 bambulab/tools/ledger.py init                          turn the ledger on here (creates bambulab/ledger/)
    python3 bambulab/tools/ledger.py status [PART] [--stl NOW.stl] [--json]
    python3 bambulab/tools/ledger.py may-change PART [--stl NEW.stl] [--json]
    python3 bambulab/tools/ledger.py say PART printed|not-printed|scrapped --quote "<owner's words>" [--hash H | --stl F]
    python3 bambulab/tools/ledger.py say --plates 1,2 [--project P.3mf] --quote "<owner's words>"     a question, no rows
    python3 bambulab/tools/ledger.py scrap PART --quote "<owner's words>" [--hash H]
    python3 bambulab/tools/ledger.py snap PART [--stl F] --quote "<owner's words>"      keep the as-printed STL now
    python3 bambulab/tools/ledger.py note PART "<text>"
    python3 bambulab/tools/ledger.py jobs [--sync] [--all] [--json]     jobs read from the slicer's cache (off unless enabled)
    python3 bambulab/tools/ledger.py tie JOB --object NAME [...] [--project P.3mf] (--basis picture|write-journal | --quote "...")
    python3 bambulab/tools/ledger.py plates PROJECT.3mf [--json]   proposes [TODO] / [OK] / [CHANGED] per plate (plate-status)
    python3 bambulab/tools/ledger.py cache status | on --quote "<owner's yes>" | off
    python3 bambulab/tools/ledger.py doctor

Files, under <root>/bambulab/ledger/ (the ledger is on when this folder exists):
    events.jsonl              committed, append-only, merge=union. One JSON object per line:
                              {"v": 1, "t": "<UTC>", "by": "tool" | "agent" | "owner", "kind": ..., ...}
        write   one per object each time a tool writes meshes into a .3mf (record_writes): "project" (path relative to
                the root), "object", "plate", "plate_index", "pose" (geom_hash as placed), "model" (geom_hash of the
                source STL or null), "stl" (relative path or null), "stl_sha256", "stamp" ("<part id>@<sig12>" or null),
                "cad" (the "Fusion <doc> v<N>" of the STL header or null), "tool"
        job     a print job tied to an object of this project: "id", "source", "sent_at", "project", "plate_index",
                "objects" ([{"object", "pose", "model"}]), "basis" ("write-journal" | "picture" | "owner"), "quote"
        said    the owner's statement, verbatim: "object", "what" ("printed" | "not-printed"), "quote", "hash" (model
                geom_hash when he named a version, else null = "printed, version unknown")
        scrap   "object", "hash" or null, "quote"
        note    "object", "text" (bench work, a defect)
    printed/<object>__<hash8>.stl   as-printed copies, model frame, never rewritten
    local.json, local/              git-ignored: the switch and the data of the slicer cache reader (ledger_cache.py),
                                    and local/pool/: the STL of every write, kept so that a job tied later finds it

A job at time T printed the meshes of the last `write` before T. "No job in the ledger" is reported as "no evidence",
never as "not printed", for any time before the journal started. A sent job is not a kept part: only the owner's word
makes a print a fact. Exit codes: 0; 2 when a may-change verdict is FIT or ASK-OWNER, or a statement became a
question; 4 when the ledger is off here or the arguments are wrong."""
import argparse, datetime, hashlib, html, json, os, re, shutil, sys, time, zipfile

LEVELS = ('never', 'printed-current', 'printed-changed', 'printed-unknown-version', 'ask')
MAY_CHANGE = ('FREE', 'FREE-REPRINT', 'FIT', 'ASK-OWNER')
KINDS = ('write', 'job', 'said', 'scrap', 'note')

BY = ('tool', 'agent', 'owner')
SAID = ('printed', 'not-printed')
BASES = ('write-journal', 'picture', 'owner')
POOL_DAYS = 120            # the STL of a write stays in local/pool this long
_HERE = os.path.dirname(os.path.abspath(__file__))
_TIME = re.compile(r'^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d)(?::(\d\d))?(?:\.\d+)?(Z|[+-]00:?00)?$')
_IDS = {}


class LedgerError(Exception):
    """A wrong argument or a ledger that is off: the command line prints it and exits 4."""


def _mesh():
    if _HERE not in sys.path:
        sys.path.append(_HERE)
    import ledger_mesh
    return ledger_mesh


def _cache():
    if _HERE not in sys.path:
        sys.path.append(_HERE)
    import ledger_cache
    return ledger_cache


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def norm_time(text):
    """A UTC time as "2026-03-04T18:30:00Z". Accepts a space for the T and a missing seconds field. ValueError otherwise."""
    m = _TIME.match(str(text).strip())
    if not m:
        raise ValueError('not a UTC time (want 2026-03-04T18:30:00Z): %r' % (text,))
    y, mo, d, h, mi, s = (int(x) if x else 0 for x in m.groups()[:6])
    return datetime.datetime(y, mo, d, h, mi, s).strftime('%Y-%m-%dT%H:%M:%SZ')


def ledger_dir(root):
    """<root>/bambulab/ledger."""
    return os.path.join(root, 'bambulab', 'ledger')


def find_root(path):
    """The nearest folder at or above `path` that holds bambulab/ledger/, or None (the ledger is off there)."""
    d = os.path.abspath(path)
    if not os.path.isdir(d):
        d = os.path.dirname(d)
    while True:
        if os.path.isdir(os.path.join(d, 'bambulab', 'ledger')):
            return d
        up = os.path.dirname(d)
        if up == d:
            return None
        d = up


def events_file(root):
    """<root>/bambulab/ledger/events.jsonl."""
    return os.path.join(ledger_dir(root), 'events.jsonl')


def _inside(root, path):
    """Does the file lie in the project?"""
    full, base = os.path.realpath(os.path.abspath(path)), os.path.realpath(root)
    return full == base or full.startswith(base + os.sep)


def _rel(root, path):
    """Path relative to the root with forward slashes; a file outside the root keeps only its name (no home path
    goes into a committed file)."""
    full = os.path.realpath(os.path.abspath(path))
    if _inside(root, path):
        return os.path.relpath(full, os.path.realpath(root)).replace(os.sep, '/')
    return os.path.basename(full)


def _abs(root, rel):
    return os.path.join(root, *rel.split('/')) if rel else None


def read_events(root, kind=None):
    """Every event of the journal in file order, optionally only one kind. A line that is not JSON is skipped."""
    out = []
    try:
        with open(events_file(root), encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if isinstance(e, dict) and e.get('kind') in KINDS and (kind is None or e['kind'] == kind):
                    out.append(e)
    except (IOError, OSError):
        pass
    return out


def broken_lines(root):
    """How many non-empty lines of the journal are not an event."""
    n = 0
    try:
        with open(events_file(root), encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    ok = isinstance(e, dict) and e.get('kind') in KINDS
                except ValueError:
                    ok = False
                n += 0 if ok else 1
    except (IOError, OSError):
        pass
    return n


def append_event(root, kind, by='tool', **fields):
    """Append one event with "v", "t" (now, UTC), "by" and "kind". Returns the event. ValueError for an unknown kind.

    Who may write what is checked here: `said` and `scrap` are the owner's words (by "owner", with a non-empty
    "quote"); a `job` tied on the owner's word needs his quote too. A field "t" overrides the time (back-fill, tests)."""
    if kind not in KINDS:
        raise ValueError('unknown event kind %r (known: %s)' % (kind, ', '.join(KINDS)))
    if by not in BY:
        raise ValueError('unknown writer %r (known: %s)' % (by, ', '.join(BY)))
    quote = (fields.get('quote') or '').strip()
    if kind in ('said', 'scrap') and (by != 'owner' or not quote):
        raise ValueError('a `%s` event is the owner\'s statement: it needs by="owner" and his words as "quote"' % kind)
    if kind == 'said' and fields.get('what') not in SAID:
        raise ValueError('said: "what" is one of %s' % ', '.join(SAID))
    if kind == 'job' and fields.get('basis') not in BASES:
        raise ValueError('job: "basis" is one of %s' % ', '.join(BASES))
    if kind == 'job' and fields.get('basis') == 'owner' and not quote:
        raise ValueError('a job tied on the owner\'s word needs his words as "quote"')
    if kind in ('said', 'scrap', 'note', 'write') and not fields.get('object') and not (kind == 'note' and fields.get('text')):
        raise ValueError('%s: "object" is missing' % kind)
    event = {'v': 1, 't': norm_time(fields.pop('t')) if fields.get('t') else _now(), 'by': by, 'kind': kind}
    fields.pop('t', None)
    event.update(fields)
    line = (json.dumps(event, ensure_ascii=False, sort_keys=False) + '\n').encode('utf-8')
    os.makedirs(ledger_dir(root), exist_ok=True)
    fd = os.open(events_file(root), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line)                      # one write per line: two sessions appending at once lose nothing
    finally:
        os.close(fd)
    return event


# ---------------------------------------------------------------- identity of files

def _ids(path):
    """meshid.stl_ids of a file, computed once per content (the key is the sha256 of the bytes, not the time stamp: a
    file rewritten within one tick of the clock must not answer with its old hash)."""
    with open(path, 'rb') as f:
        key = hashlib.sha256(f.read()).hexdigest()
    if key not in _IDS:
        _IDS[key] = _mesh().stl_ids(path)
    return _IDS[key]


def _hash_of(path):
    return _ids(path)['geom_hash']


def _stl_cad(path):
    """The "Fusion <document> v<N>" at the start of a binary STL header, or None."""
    try:
        with open(path, 'rb') as f:
            head = f.read(80)
    except (IOError, OSError):
        return None
    if head[:5].lower() == b'solid' and b'Fusion' not in head[:12]:
        return None
    text = head.decode('ascii', 'replace').strip('\x00 \n\r\t')
    m = re.match(r'^(Fusion\b.*?\bv\d+)\b', text)
    return m.group(1) if m else None


def _safe(name):
    return re.sub(r'[^A-Za-z0-9._-]+', '_', str(name)).strip('_') or 'part'


def printed_dir(root):
    return os.path.join(ledger_dir(root), 'printed')


def printed_path(root, obj, model_hash):
    """bambulab/ledger/printed/<object>__<hash8>.stl (the object name made safe for a file name)."""
    return os.path.join(printed_dir(root), '%s__%s.stl' % (_safe(obj), model_hash[:8]))


def local_dir(root, make=False):
    """bambulab/ledger/local, the git-ignored folder. When it is created it gets its own .gitignore ("*"), so that
    nothing in it can be committed even when the project's .gitignore does not list it."""
    d = os.path.join(ledger_dir(root), 'local')
    if make and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
    if make and not os.path.exists(os.path.join(d, '.gitignore')):
        with open(os.path.join(d, '.gitignore'), 'w') as f:
            f.write('*\n')
    return d


def _pool_path(root, model_hash):
    return os.path.join(local_dir(root), 'pool', model_hash[:16] + '.stl')


def _pool_keep(root, stl_path, model_hash):
    dst = _pool_path(root, model_hash)
    if os.path.exists(dst):
        return dst
    local_dir(root, make=True)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + '.tmp%d' % os.getpid()
    shutil.copyfile(stl_path, tmp)
    os.replace(tmp, dst)
    limit = time.time() - POOL_DAYS * 86400
    for name in os.listdir(os.path.dirname(dst)):
        p = os.path.join(os.path.dirname(dst), name)
        try:
            if name.endswith('.stl') and os.stat(p).st_mtime < limit:
                os.remove(p)
        except OSError:
            pass
    return dst


def keep_printed_copy(root, obj, stl_path, model_hash):
    """Copy an STL to bambulab/ledger/printed/<object>__<hash8>.stl unless that file exists. Returns its path.
    ValueError when the STL is not the geometry `model_hash` names: a wrong copy is worse than none."""
    have = _hash_of(stl_path)
    if model_hash is None:
        model_hash = have
    if have != model_hash:
        raise ValueError('%s is %s, not the %s that was printed' % (os.path.basename(stl_path), have[:8], model_hash[:8]))
    dst = printed_path(root, obj, model_hash)
    if os.path.exists(dst):
        return dst
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + '.tmp%d' % os.getpid()
    shutil.copyfile(stl_path, tmp)
    os.replace(tmp, dst)
    return dst


def find_stl(root, model_hash, events=None, obj=None):
    """A file on disk that holds the geometry `model_hash` (model frame), or None: an as-printed copy, the pool, or the
    STL a `write` event names when it still is that geometry."""
    if not model_hash:
        return None
    if obj is not None and os.path.exists(printed_path(root, obj, model_hash)):
        return printed_path(root, obj, model_hash)
    if os.path.exists(_pool_path(root, model_hash)):
        return _pool_path(root, model_hash)
    for e in reversed(events if events is not None else read_events(root, 'write')):
        if e.get('kind') == 'write' and e.get('model') == model_hash and e.get('stl'):
            p = _abs(root, e['stl'])
            try:
                if os.path.isfile(p) and _hash_of(p) == model_hash:
                    return p
            except (ValueError, IOError, OSError):
                pass
    return None


# ---------------------------------------------------------------- the write journal

def record_writes(project_path, meshes, stamps=None, tool=None):
    """The ONE function every .3mf writer calls after a save (bambu3mf.save does): one `write` event per object.

    project_path  the .3mf that was written
    meshes        the list bambu3mf hands to gate_guard.check(): {"object", "plate", "plate_index", "vertices",
                  "triangles", "stl": source STL path or None, ...}
    stamps        {object name: "<part id>@<sig12>"} from the guard's result, or None
    tool          name of the writing function ("build", "update_meshes", "add_plate", ...)

    Does nothing and returns [] when no ledger folder is found above the project. An object whose pose hash equals its
    last `write` event for this project is not written again. Never raises: a ledger problem must not break a save
    (it returns [] and prints one line). Returns the events written."""
    try:
        root = find_root(project_path)
        if root is None:
            return []
        m = _mesh()
        project = _rel(root, project_path)
        last = {}
        for e in read_events(root, 'write'):
            if e.get('project') == project:
                last[e.get('object')] = e
        out = []
        for mesh in meshes or []:
            name = mesh.get('object')
            if not name:
                continue
            pose = m.geom_hash(mesh['vertices'], len(mesh['triangles']))
            before = last.get(name)
            if before is not None and before.get('pose') == pose:
                continue
            stl, model, sha, cad = mesh.get('stl'), None, None, None
            if stl and os.path.isfile(stl):
                try:
                    model, sha = mesh.get('model'), mesh.get('stl_sha256')          # the writer may have hashed the file already
                    if not (model and sha):
                        ids = _ids(stl)
                        model, sha = ids['geom_hash'], ids['sha256']
                    cad = _stl_cad(stl)
                    _pool_keep(root, stl, model)
                except Exception as e:                       # the STL could not be read or kept: the write is still recorded
                    print('ledger: %s: %s' % (os.path.basename(stl), type(e).__name__))
            extra = {'orient': mesh['orient']} if mesh.get('orient') not in (None, False) else {}
            e = append_event(root, 'write', 'tool', project=project, object=name, plate=mesh.get('plate'), plate_index=mesh.get('plate_index'),
                             pose=pose, model=model, stl=_rel(root, stl) if stl else None, stl_sha256=sha,
                             stamp=(stamps or {}).get(name), cad=cad, tool=tool, **extra)
            last[name] = e
            out.append(e)
        return out
    except Exception as e:
        print('ledger: the write was not recorded (%s: %s)' % (type(e).__name__, e))
        return []


def writes_of(root, project=None, obj=None):
    """`write` events, optionally of one project file (path relative to the root) and one object, oldest first."""
    out = [e for e in read_events(root, 'write') if (project is None or e.get('project') == project) and (obj is None or e.get('object') == obj)]
    return sorted(out, key=lambda e: e.get('t', ''))


def objects_known(root, events=None):
    """Every object name the journal has seen, in order of first appearance."""
    seen = []
    for e in (events if events is not None else read_events(root)):
        names = [o.get('object') for o in e.get('objects') or []] if e.get('kind') == 'job' else [e.get('object')]
        for n in names:
            if n and n not in seen:
                seen.append(n)
    return seen


def resolve_object(root, name, events=None):
    """The object the name means: itself when the journal knows it, else the one known object that equals it without
    regard to case, else the one that contains it. LedgerError when several fit. An unknown name is returned as it is."""
    known = objects_known(root, events)
    if name in known:
        return name
    low = [k for k in known if k.lower() == name.lower()]
    if len(low) == 1:
        return low[0]
    part = [k for k in known if name.lower() in k.lower()]
    if len(part) == 1:
        return part[0]
    if len(part) > 1:
        raise LedgerError('%r fits several objects: %s' % (name, ', '.join(repr(p) for p in part)))
    return name


def resolve_hash(root, text, events=None):
    """A full model hash from a full one or from a prefix of at least 8 characters that fits exactly one hash the
    journal knows. LedgerError otherwise."""
    text = (text or '').strip().lower()
    if not re.match(r'^[0-9a-f]{8,40}$', text):
        raise LedgerError('a hash is 8 to 40 hex characters, not %r' % text)
    if len(text) == 40:
        return text
    known = set()
    for e in (events if events is not None else read_events(root)):
        for h in [e.get('model'), e.get('hash')] + [o.get('model') for o in e.get('objects') or [] if isinstance(o, dict)]:
            if h and h.startswith(text):
                known.add(h)
    if len(known) != 1:
        raise LedgerError('the prefix %s fits %d hashes of the journal: give more characters or --stl' % (text, len(known)))
    return known.pop()


# ---------------------------------------------------------------- the folded view

def _fold(root, events, obj):
    writes = sorted((e for e in events if e.get('kind') == 'write' and e.get('object') == obj), key=lambda e: e.get('t', ''))
    by_pose = {}
    for w in writes:
        if w.get('pose'):
            by_pose[w['pose']] = w
    prints, cancels, notes, jobs, hint = [], [], [], {}, None
    for e in events:
        k = e.get('kind')
        if k == 'said' and e.get('object') == obj:
            hint = e.get('stl') or hint
            if e.get('what') == 'printed':
                prints.append({'when': e['t'], 'hash': e.get('hash'), 'basis': 'owner', 'sure': True, 'quote': e.get('quote'), 'job': None})
            else:
                cancels.append({'t': e['t'], 'hash': None, 'what': 'not-printed', 'quote': e.get('quote')})
        elif k == 'scrap' and e.get('object') == obj:
            cancels.append({'t': e['t'], 'hash': e.get('hash'), 'what': 'scrapped', 'quote': e.get('quote')})
        elif k == 'note' and e.get('object') == obj:
            notes.append({'t': e['t'], 'text': e.get('text')})
        elif k == 'job':
            for o in e.get('objects') or []:
                if isinstance(o, dict) and o.get('object') == obj:
                    h = o.get('model') or (by_pose.get(o.get('pose')) or {}).get('model')
                    basis = e.get('basis')
                    jobs[e.get('id')] = {'when': e.get('sent_at') or e['t'], 'hash': h, 'basis': 'job+%s' % basis, 'sure': basis == 'owner',
                                         'quote': e.get('quote'), 'job': e.get('id')}          # a later tie of one job replaces the earlier one
    prints.extend(jobs.values())
    kept, ignored = [], []
    for p in sorted(prints, key=lambda p: p['when']):
        hit = None
        for c in cancels:
            if p['when'] <= c['t'] and (c['hash'] is None or c['hash'] == p['hash']):
                hit = c
        if hit:
            ignored.append(dict(p, ignored=hit['what'], ignored_at=hit['t'], ignored_quote=hit['quote']))
        else:
            kept.append(p)
    # a statement without a version takes the version of the one print it can mean
    for p in kept:
        if not (p['sure'] and p['hash'] is None and p['basis'] == 'owner'):
            continue
        earlier = [q for q in kept if q is not p and q['hash'] and q['when'] <= p['when']]
        hashes = sorted({q['hash'] for q in earlier})
        if len(hashes) == 1:
            since = min(q['when'] for q in earlier if q['hash'] == hashes[0])
            other = [w for w in writes if w.get('model') and w['model'] != hashes[0] and since < w.get('t', '') <= p['when']]
            if not other:
                p['hash'], p['basis'] = hashes[0], 'owner+job' if any(q['job'] for q in earlier) else 'owner'
                for q in earlier:
                    q['merged'] = True
        elif hashes:
            p['candidates'] = hashes
    kept = [p for p in kept if not p.get('merged')]
    last_no = max([c for c in cancels if c['what'] == 'not-printed'], key=lambda c: c['t'], default=None)
    return {'writes': writes, 'prints': kept, 'ignored': ignored, 'notes': notes, 'cancels': cancels, 'not_printed': last_no, 'stl_hint': hint}


def _current(root, obj, writes, model_hash=None, stl=None, hint=None):
    """(hash, where it comes from, path or None) of today's geometry of an object: the STL given, else the STL the last
    `write` of the object names, else the STL the owner's statement was recorded with (`hint`), else the model hash of
    the last write."""
    if stl:
        return _hash_of(stl), 'argument', os.path.abspath(stl)
    last = writes[-1] if writes else None
    path = None
    for rel in ((last or {}).get('stl'), hint):
        p = _abs(root, rel) if rel else None
        if not p or not os.path.isfile(p):
            continue
        try:
            h = _hash_of(p)
        except (ValueError, IOError, OSError):
            continue
        if model_hash is None:
            return h, 'stl', p
        if h == model_hash:
            path = p
            break
    if model_hash is not None:
        return model_hash, 'argument', path
    if last is not None and last.get('model'):
        return last['model'], 'last-write', None
    return None, None, None


def _status(root, obj, events, model_hash=None, stl=None, boxes=True):
    f = _fold(root, events, obj)
    current, current_from, current_path = _current(root, obj, f['writes'], model_hash, stl, f['stl_hint'])
    first = min((e.get('t', '') for e in events if e.get('kind') == 'write'), default=None)
    prints = []
    for p in f['prints']:
        copy = printed_path(root, obj, p['hash']) if p['hash'] else None
        prints.append(dict(p, copy=_rel(root, copy) if copy and os.path.exists(copy) else None))
    sure = [p for p in prints if p['sure']]
    guess = [p for p in prints if not p['sure']]
    out = {'object': obj, 'level': 'never', 'prints': prints, 'why': '', 'journal_since': first, 'current': current,
           'current_from': current_from, 'current_stl': _rel(root, current_path) if current_path else None, 'changed': None,
           'ignored': f['ignored'], 'notes': f['notes'], 'writes': len(f['writes'])}
    if not prints:
        no = f['not_printed']
        if no is not None:
            out['why'] = 'the owner said it is not printed (%s: "%s")' % (no['t'][:10], no['quote'])
        elif f['ignored']:
            last = f['ignored'][-1]
            out['why'] = 'no print that counts: the last one was %s (%s: "%s")' % (last['ignored'], last['ignored_at'][:10], last['ignored_quote'])
        elif first is None:
            out['why'] = 'no evidence of a print. The journal holds no write yet, so this says nothing about the past: ask the owner'
        else:
            out['why'] = ('no evidence of a print since %s, when the journal started. A print before that, or one sent from another '
                          'computer or project, is not in it' % first)
        return out
    if not sure:
        g = guess[-1]
        out['level'] = 'ask'
        out['why'] = ('%d job%s tied to this object without the owner\'s word (the last: %s, sent %s, %s). A sent job is not a kept part, '
                      'and the object set on the plate is not proven: ask him' % (len(guess), '' if len(guess) == 1 else 's', g['job'], g['when'], g['basis']))
        return out
    last = sure[-1]
    older = sorted({p['hash'][:8] for p in sure[:-1] if p['hash'] and p['hash'] != last['hash']})
    tail = ('; older prints of other versions: %s (say scrapped for the ones that are gone)' % ', '.join(older)) if older else ''
    tail += ('; %d more job%s not confirmed by the owner' % (len(guess), '' if len(guess) == 1 else 's')) if guess else ''
    if last['hash'] is None:
        out['level'] = 'printed-unknown-version'
        cands = last.get('candidates') or sorted({w['model'] for w in f['writes'] if w.get('model') and w.get('t', '') <= last['when']})
        out['candidates'] = cands
        out['why'] = 'the owner said it is printed (%s: "%s") but not which version%s%s' % (
            last['when'][:10], last['quote'], ('; the journal knows %s' % ', '.join(c[:8] for c in cands)) if cands else '', tail)
        return out
    if current is None:
        out['level'], out['need_stl'] = 'ask', True
        out['why'] = 'printed as %s (%s, %s), but the ledger does not know today\'s STL of this object: give --stl%s' % (
            last['hash'][:8], last['when'][:10], last['basis'], tail)
        return out
    if last['hash'] == current:
        out['level'] = 'printed-current'
        out['why'] = 'printed as %s (%s, %s) and today\'s model is the same geometry%s' % (last['hash'][:8], last['when'][:10], last['basis'], tail)
        return out
    out['level'] = 'printed-changed'
    out['why'] = 'printed as %s (%s, %s); today\'s model is %s%s' % (last['hash'][:8], last['when'][:10], last['basis'], current[:8], tail)
    if boxes and last['copy'] and current_path:
        try:
            ch = _mesh().change_region(_abs(root, last['copy']), current_path)
            out['changed'] = {'boxes': ch['boxes'], 'max_mm': ch['max_mm'], 'step': ch['step'], 'sampled': True}
        except Exception as e:
            out['changed'] = {'error': type(e).__name__}
    elif boxes and not last['copy']:
        out['why'] += '; no as-printed copy is kept, so what moved cannot be listed'
    return out


def status(root, obj, model_hash=None):
    """Was this object printed, and is that print the current geometry?

    obj         object name (as in the .3mf)        model_hash  geom_hash of today's STL (model form) or None
    Returns {"object", "level": one of LEVELS, "prints": [{"when", "hash" or None, "basis", "copy": path or None}],
             "why": one line, "journal_since": time of the first `write` event or None}.
    never: no evidence of a print (for the time the journal covers). printed-current: an as-printed hash equals
    model_hash. printed-changed: it differs. printed-unknown-version: said printed, no hash. ask: the evidence is a guess.

    Without model_hash today's geometry is the STL the last `write` of the object names (when that file exists), else the
    model hash of that write ("current_from": "stl" | "last-write"). More keys: "current", "current_stl", "changed"
    ({"boxes", "max_mm"}: where the print and today's model differ, sampled), "ignored" (prints the owner scrapped or
    denied), "notes", "candidates"."""
    return _status(root, obj, read_events(root), model_hash)


def status_all(root):
    """status() of every object the journal knows."""
    events = read_events(root)
    return [_status(root, o, events, boxes=False) for o in objects_known(root, events)]


# ---------------------------------------------------------------- the owner's words

def plate_question(root, plates, quote, project=None):
    """A statement about plate NUMBERS is not resolved to objects: plate numbers change and the plate that was printed
    need not hold today's objects. Returns {"question": text, "candidates": [{"plate_index", "project", "objects":
    [{"object", "plate", "from", "to"}]}]} and writes no `said` event. The statement itself is kept as a `note`."""
    events = read_events(root)
    project = _rel(root, project) if project else None
    cands = []
    for n in plates:
        seen = {}
        for w in sorted((e for e in events if e.get('kind') == 'write'), key=lambda e: e.get('t', '')):
            if project is not None and w.get('project') != project:
                continue
            key = (w.get('project'), w.get('object'))
            if w.get('plate_index') == n:
                row = seen.setdefault(key, {'object': w.get('object'), 'project': w.get('project'), 'plate': w.get('plate'), 'from': w.get('t'), 'to': None})
                row['plate'], row['to'] = w.get('plate'), None
            elif key in seen and seen[key]['to'] is None:
                seen[key]['to'] = w.get('t')
        now = []
        if project is not None:
            try:
                now = [o for p in read_plates(_abs(root, project)) if p['plate_index'] == n for o in p['objects']]
            except Exception:
                now = []
        cands.append({'plate_index': n, 'project': project, 'objects': list(seen.values()), 'now': now})
    lines = ['The statement names plate numbers. Plate numbers change and a plate may have held other objects when it was printed,',
             'so nothing was recorded as printed. Ask the owner which OBJECTS he means, then record each with `say <object> printed`:']
    for c in cands:
        lines.append('  plate %d%s' % (c['plate_index'], (' of %s' % c['project']) if c['project'] else ''))
        if c['now']:
            lines.append('    in the file now: %s' % ', '.join(repr(o) for o in c['now']))
        for o in c['objects']:
            lines.append('    %r was written there %s%s (plate name %r, %s)' % (
                o['object'], o['from'], (' until %s' % o['to']) if o['to'] else ' and has not moved since', o['plate'], o['project']))
        if not c['objects'] and not c['now']:
            lines.append('    the journal has no write on this plate number')
    append_event(root, 'note', 'agent', object=None, text='owner statement about plate numbers, not resolved to objects: "%s"' % quote.strip(),
                 plates=list(plates), project=project)
    return {'question': '\n'.join(lines), 'candidates': cands}


def _mentions_plates(quote, obj):
    q = quote.lower()
    return bool(re.search(r'\bplates?\b[\s#:]*\d', q)) and obj.lower() not in q


def say(root, obj, what, quote, model_hash=None, stl=None):
    """Record the owner's statement verbatim (`said`, or `scrap` for what == "scrapped"). With a hash and
    what == "printed" the model-frame STL of that write is copied to printed/. Returns the event.
    ValueError for an empty quote.

    stl: the STL file of this object the statement was recorded with. Its path (when it lies in the project) is kept in
    the event, so that the ledger knows where today's model of a part is that no tool has written into a .3mf yet."""
    if not (quote or '').strip():
        raise ValueError('the owner\'s statement needs his words: --quote "..."')
    if what == 'scrapped':
        return append_event(root, 'scrap', 'owner', object=obj, hash=model_hash, quote=quote.strip())
    if what not in SAID:
        raise ValueError('say: %r is not one of printed, not-printed, scrapped' % (what,))
    copy = None
    if what == 'printed' and model_hash:
        src = find_stl(root, model_hash, obj=obj)
        if src is not None:
            copy = _rel(root, keep_printed_copy(root, obj, src, model_hash))
    more = {'stl': _rel(root, stl)} if stl and _inside(root, stl) else {}
    event = append_event(root, 'said', 'owner', object=obj, what=what, quote=quote.strip(), hash=model_hash if what == 'printed' else None, **more)
    return dict(event, copy=copy)


def note(root, obj, text):
    """Record a note about an object (bench work, a defect). Returns the event."""
    if not (text or '').strip():
        raise ValueError('an empty note')
    return append_event(root, 'note', 'agent', object=obj, text=text.strip())


def snap(root, obj, quote, stl=None):
    """The owner says this object is being printed (or was printed) from the STL as it is now: keep the as-printed copy
    and record `said printed` with its hash. Returns the event with "copy". LedgerError when no STL is known."""
    if not (quote or '').strip():
        raise ValueError('snap records a print: it needs the owner\'s words, --quote "..."')
    if stl is None:
        ws = writes_of(root, obj=obj)
        stl = _abs(root, ws[-1]['stl']) if ws and ws[-1].get('stl') else None
    if not stl or not os.path.isfile(stl):
        raise LedgerError('no STL of %r is known: give --stl' % obj)
    h = _hash_of(stl)
    keep_printed_copy(root, obj, stl, h)
    return say(root, obj, 'printed', quote, h, stl)


# ---------------------------------------------------------------- jobs of the slicer cache

def _write_before(writes, project, obj, when):
    hit = None
    for w in writes:
        if w.get('object') == obj and (project is None or w.get('project') == project) and w.get('t', '') <= when:
            hit = w
    return hit


def job_candidates(root, job, events=None):
    """What the write journal held on the job's plate number when it was sent: [{"project", "plate", "objects": [...]}].
    A hint for looking at the picture, never a match: plate numbers change, and the slicer may have sent a state that
    was never saved (other objects switched off, a newer mesh)."""
    writes = sorted((e for e in (events if events is not None else read_events(root)) if e.get('kind') == 'write'), key=lambda e: e.get('t', ''))
    state = {}
    for w in writes:
        if w.get('t', '') <= job['sent_at']:
            state[(w.get('project'), w.get('object'))] = w
    out = {}
    for (project, obj), w in state.items():
        if w.get('plate_index') == job.get('plate_index'):
            row = out.setdefault(project, {'project': project, 'plate': w.get('plate'), 'objects': []})
            row['objects'].append(obj)
    return [out[k] for k in sorted(out, key=lambda x: x or '')]


def jobs_view(root):
    """The local jobs (ledger_cache.local_jobs) with "tied": the committed `job` event of that id or None, and
    "candidates" from the write journal. [] when the reader is off."""
    events = read_events(root)
    tied = {e.get('id'): e for e in events if e.get('kind') == 'job'}
    out = []
    for j in _cache().local_jobs(root):
        out.append(dict(j, tied=tied.get(j.get('id')), candidates=job_candidates(root, j, events)))
    return out


def tie(root, job_id, objects, basis=None, project=None, quote=None):
    """Tie one local job to objects of THIS project: only now does anything about the job enter the committed journal.

    basis   "picture" (the agent looked at the job's picture), "write-journal" (the time rule alone) or "owner" (his
            words, `quote`). Without the owner's word the print stays a guess: status says "ask".
    Each object gets the pose and model hash of its last `write` before the job was sent; when the journal has none, the
    hashes are null and the print is "version unknown". The model-frame STL of that write is copied to printed/ when it
    is still on disk. Returns the `job` event. LedgerError for an unknown job or object."""
    if quote and quote.strip():
        basis = 'owner'
    if basis not in BASES:
        raise LedgerError('tie needs --basis picture or write-journal, or the owner\'s words with --quote')
    if basis == 'owner' and not (quote or '').strip():
        raise LedgerError('basis owner needs his words: --quote "..."')
    jobs = [j for j in _cache().local_jobs(root) if j.get('id') == job_id]
    if not jobs:
        raise LedgerError('no local job %r (run `jobs`; the slicer cache reader may be off on this machine)' % job_id)
    job = jobs[0]
    if not objects:
        raise LedgerError('tie needs at least one --object')
    events = read_events(root)
    writes = sorted((e for e in events if e.get('kind') == 'write'), key=lambda e: e.get('t', ''))
    project = _rel(root, project) if project else None
    rows, copies = [], []
    for name in objects:
        obj = resolve_object(root, name, events)
        projects = sorted({w.get('project') for w in writes if w.get('object') == obj})
        if not projects:
            raise LedgerError('%r is not an object of the write journal: a job can only be tied to an object of this project' % name)
        if project is None and len(projects) > 1:
            raise LedgerError('%r is in several projects (%s): give --project' % (obj, ', '.join(projects)))
        proj = project or projects[0]
        w = _write_before(writes, proj, obj, job['sent_at'])
        rows.append({'object': obj, 'pose': w.get('pose') if w else None, 'model': w.get('model') if w else None})
        project = proj
        if w and w.get('model'):
            src = find_stl(root, w['model'], events, obj)
            if src is not None:
                copies.append(_rel(root, keep_printed_copy(root, obj, src, w['model'])))
    fields = dict(id=job['id'], source=job.get('source', 'studio_cache'), sent_at=job['sent_at'], project=project,
                  plate_index=job.get('plate_index'), objects=rows, basis=basis)
    if basis == 'owner':
        fields['quote'] = quote.strip()
    event = append_event(root, 'job', 'owner' if basis == 'owner' else ('agent' if basis == 'picture' else 'tool'), **fields)
    return dict(event, copies=copies)


# ---------------------------------------------------------------- may I change this part

def _meshes_of(root, st):
    """The meshes that stand for one neighbour: an as-printed copy per counted print, and today's model."""
    out, seen = [], set()
    for p in st['prints']:
        if p['copy'] and p['hash'] not in seen:
            seen.add(p['hash'])
            out.append({'what': 'as printed %s' % p['hash'][:8], 'path': _abs(root, p['copy']), 'printed': True, 'sure': p['sure']})
    if st['current_stl'] and st['current'] not in seen:
        out.append({'what': 'model', 'path': _abs(root, st['current_stl']), 'printed': False, 'sure': True})
    return out


def may_change(root, obj, new_stl=None):
    """May this part be changed? Returns {"object", "verdict": one of MAY_CHANGE, "level": status level, "rows": [lines
    for the report], "why"}. FREE: never printed. FREE-REPRINT: printed, the change needs a reprint and touches no
    printed neighbour. FIT: must be checked against the as-printed copy of a neighbour (a model-to-model distance test:
    it knows neither shrink nor hole compensation). ASK-OWNER: the record is a guess or the version is unknown.

    Without new_stl the answer is for reading BEFORE an edit: the rows list the keep-out boxes (where this part meets a
    neighbour, model coordinates). With new_stl it is the answer for that geometry. More keys: "zones" ([{"object",
    "against", "printed", "boxes", "min_gap"}]), "changed" (where new_stl differs from the print or from today's
    model), "fits" (the sampled numbers per printed neighbour).
    The zones are found by proximity in the exported pose: a neighbour that meets the part only in another position of
    a joint, or a bought part, is not seen."""
    m = _mesh()
    events = read_events(root)
    st = _status(root, obj, events, boxes=False)
    p_now = _abs(root, st['current_stl']) if st['current_stl'] else None
    if st.get('need_stl') and new_stl:             # printed, and no tool has written this part yet: the STL given is today's model
        st = _status(root, obj, events, stl=new_stl, boxes=False)
    out = {'object': obj, 'verdict': 'FREE', 'level': st['level'], 'rows': [], 'why': '', 'zones': [], 'changed': None, 'fits': [], 'status': st['why']}
    rows = out['rows']
    rows.append('%s: %s' % (st['level'], st['why']))
    if st['level'] == 'printed-unknown-version' or (st['level'] == 'ask' and not st.get('need_stl')):
        out['verdict'] = 'ASK-OWNER'
        out['why'] = ('the record of this part is a guess: ask the owner whether it is printed and kept' if st['level'] == 'ask'
                      else 'the part is printed but the ledger does not know which version: ask the owner')
        if st.get('candidates'):
            rows.append('versions the journal knows: %s' % ', '.join(c[:8] for c in st['candidates']))
        return out
    sure = [p for p in st['prints'] if p['sure']]
    printed = bool(sure)
    last = sure[-1] if sure else None
    p_new = os.path.abspath(new_stl) if new_stl else p_now
    p_print = _abs(root, last['copy']) if last and last['copy'] else None
    new_hash = _hash_of(p_new) if p_new else None

    # what changes
    change, base = None, None                       # change None = not known: the whole part counts as changed
    if printed:
        if new_hash is not None and new_hash == last['hash']:
            change = {'changed': False, 'boxes': [], 'max_mm': 0.0}
        elif p_print and p_new:
            change, base = m.change_region(p_print, p_new), 'the as-printed copy %s' % last['hash'][:8]
    elif new_stl and p_now:
        if new_hash == st['current']:
            change = {'changed': False, 'boxes': [], 'max_mm': 0.0}
        else:
            change, base = m.change_region(p_now, p_new), 'today\'s model %s' % st['current'][:8]
    if change is not None:
        out['changed'] = {'boxes': change['boxes'], 'max_mm': change['max_mm'], 'against': base, 'sampled': True}
        if change['boxes']:
            rows.append('change against %s: %d box%s, up to %.2f mm (sampled)' % (base, len(change['boxes']), '' if len(change['boxes']) == 1 else 'es', change['max_mm']))
            rows.extend('  D  %s' % m.box_text(b) for b in change['boxes'][:12])

    # where the part meets its neighbours
    mine = [p for p in ((p_print, 'as printed'), (p_new, 'new' if new_stl else 'model')) if p[0]]
    if len(mine) == 2 and new_hash == (last['hash'] if last else None):
        mine = mine[:1]
    others = []
    for o in objects_known(root, events):
        if o == obj:
            continue
        so = _status(root, o, events, boxes=False)
        others.append((o, so, _meshes_of(root, so)))
    for o, so, meshes in others:
        for mesh in meshes:
            boxes, gap = [], None
            for path, _ in mine:
                try:
                    z = m.mating_zone(path, mesh['path'])
                except Exception:
                    continue
                boxes.extend(b for b in z['boxes'] if b not in boxes)
                if z['min_gap'] is not None and (gap is None or z['min_gap'] < gap):
                    gap = z['min_gap']
            if boxes:
                out['zones'].append({'object': o, 'against': mesh['what'], 'printed': mesh['printed'], 'sure': mesh['sure'], 'level': so['level'],
                                     'boxes': sorted(boxes), 'min_gap': gap, 'path': _rel(root, mesh['path'])})
    touched = lambda z: change is None or (change['boxes'] and m.any_touch(change['boxes'], z['boxes'], m.NEAR))
    blind = [o for o, so, meshes in others if so['prints'] and not any(x['printed'] for x in meshes)]      # printed, no copy to test against

    if not mine:
        rows.append('no STL of this part is known to the ledger: neighbours were not checked (give --stl)')
        if printed:
            out['verdict'], out['why'] = 'ASK-OWNER', 'printed, and no geometry to tell which interface a change would touch'
        elif any(so['prints'] for _, so, _ in others):
            out['verdict'], out['why'] = 'FIT', 'not printed as far as the ledger knows, but printed neighbours exist and no STL of this part is known'
        else:
            out['verdict'], out['why'] = 'FREE', 'no evidence of a print of this part or of any neighbour'
        return out

    for z in out['zones']:
        rows.append('meets %r (%s%s): gap %s, %d box%s%s' % (z['object'], z['against'], '' if z['sure'] else ', a guess', '%.2f' % z['min_gap'], len(z['boxes']),
                                                             '' if len(z['boxes']) == 1 else 'es', '' if change is None or not change['boxes'] else (', TOUCHED by the change' if touched(z) else ', not touched')))
        rows.extend('  keep-out  %s' % m.box_text(b) for b in z['boxes'][:6])

    if printed:
        own = [z for z in out['zones'] if touched(z)]
        if change is not None and not change['boxes']:
            if new_stl:
                out['verdict'], out['why'] = 'FREE', 'the new STL is the geometry that was printed: nothing to reprint'
            else:
                out['verdict'] = 'FREE-REPRINT'
                out['why'] = ('printed and unchanged. A change outside the keep-out boxes needs only a reprint; a change inside one makes the '
                              'printed copy stop matching its neighbour: that is the owner\'s call. Run again with --stl after the change')
        elif own:
            names = sorted({z['object'] for z in own})
            out['verdict'] = 'ASK-OWNER'
            out['why'] = ('the part is printed and the change touches its interface with %s: the printed copy stops matching. The owner decides: '
                          'reprint it, keep it and make the change fit both, or rework at the bench' % ', '.join(repr(n) for n in names))
        else:
            out['verdict'] = 'FREE-REPRINT'
            out['why'] = ('printed; the change touches no interface the ledger can see, so the printed copy keeps working and gets the change '
                          'only with a reprint' if change is not None else
                          'printed; no neighbour comes within %.1f mm in the exported pose. The change needs a reprint' % m.NEAR)
        return out

    # not printed: does it meet something that is?
    zp = [z for z in out['zones'] if z['printed']]
    moved_neighbours = sorted({z['object'] for z in zp if z['level'] == 'printed-changed'})
    hot = [z for z in zp if touched(z) or z['level'] == 'printed-changed']
    if blind:
        blind_zone = [z for z in out['zones'] if z['object'] in blind and touched(z)]
        if blind_zone:
            out['verdict'] = 'ASK-OWNER'
            out['why'] = 'it meets %s, printed in a version the ledger has no copy of: ask the owner which version is on the desk' % ', '.join(
                repr(n) for n in sorted({z['object'] for z in blind_zone}))
            return out
    if not hot:
        out['verdict'] = 'FREE'
        out['why'] = ('no evidence of a print of this part, and %s' % (
            'the change touches no zone shared with a printed neighbour' if zp else 'no printed neighbour comes within %.1f mm in the exported pose' % m.NEAR))
        return out
    out['verdict'] = 'FIT'
    if new_stl is None and change is None:
        out['why'] = ('no evidence of a print of this part, but it meets printed neighbours (%s): a change inside the keep-out boxes must fit the '
                      'AS-PRINTED copy, not today\'s model. Run again with --stl after the change' % ', '.join(repr(n) for n in sorted({z['object'] for z in zp})))
    else:
        out['why'] = 'it must fit the as-printed copy of %s%s' % (', '.join(repr(n) for n in sorted({z['object'] for z in hot})),
                                                                    ('; %s printed differs from its model' % ', '.join(repr(n) for n in moved_neighbours)) if moved_neighbours else '')
    for z in hot:
        try:
            f = m.fit(p_new, _abs(root, z['path']))
        except Exception as e:
            rows.append('fit against %r (%s): could not be computed (%s)' % (z['object'], z['against'], type(e).__name__))
            continue
        row = {'object': z['object'], 'against': z['against'], 'result': f['result'], 'min_gap': f['min_gap'], 'inside': f['inside'], 'depth': f['depth'], 'label': f['label']}
        model = [x for o, so, meshes in others if o == z['object'] for x in meshes if not x['printed']]
        if model:
            try:
                f2 = m.fit(p_new, model[0]['path'])
                row['model'] = {'result': f2['result'], 'min_gap': f2['min_gap'], 'inside': f2['inside'], 'depth': f2['depth']}
            except Exception:
                pass
        out['fits'].append(row)
        rows.append('fit against %r %s: %s, gap %s, %d sample point%s inside (deepest %.2f)%s' % (
            z['object'], z['against'], f['result'], '-' if f['min_gap'] is None else '%.2f' % f['min_gap'], f['inside'], '' if f['inside'] == 1 else 's', f['depth'],
            ('; against its model today: %s, gap %s' % (row['model']['result'], '-' if row['model']['min_gap'] is None else '%.2f' % row['model']['min_gap'])) if 'model' in row else ''))
    if out['fits']:
        rows.append('the fit numbers are %s' % m.FIT_LABEL)
    return out


# ---------------------------------------------------------------- plates

def read_plates(project_path):
    """Plates of a .3mf, read only: [{"plate_index", "plate", "objects": [object names]}]."""
    with zipfile.ZipFile(project_path) as z:
        cfg = z.read('Metadata/model_settings.config').decode('utf-8', 'replace')
    names = {}
    for oid, body in re.findall(r'<object id="(\d+)"[^>]*>(.*?)</object>', cfg, re.S):
        m = re.search(r'key="name" value="([^"]*)"', body.split('<part')[0])
        names[oid] = html.unescape(m.group(1)) if m else 'object %s' % oid
    out = []
    for n, block in enumerate(re.findall(r'<plate>(.*?)</plate>', cfg, re.S)):
        pid = re.search(r'key="plater_id" value="(\d+)"', block)
        name = re.search(r'key="plater_name" value="([^"]*)"', block)
        ids = list(dict.fromkeys(re.findall(r'key="object_id" value="(\d+)"', block)))
        out.append({'plate_index': int(pid.group(1)) if pid else n + 1, 'plate': html.unescape(name.group(1)) if name else '',
                    'objects': [names.get(i, 'object %s' % i) for i in ids]})
    return out


def plate_status(root, project_path):
    """Proposed plate prefixes for one .3mf: [{"plate", "plate_index", "status": "[TODO]" | "[OK]" | "[CHANGED]",
    "objects": [{"object", "level"}]}]. The tool proposes; the project's writer applies.

    [OK] every object is printed-current. [CHANGED] an object is printed-changed. [TODO] nothing counts as printed, or a
    part of the plate is still to print. "status" is None with "ask" (the reason) when an object is a guess or its
    printed version is unknown: no prefix is proposed for such a plate. "now" is the prefix the plate has today."""
    events = read_events(root)
    project = _rel(root, project_path)
    try:
        plates, source = read_plates(project_path), 'file'
    except Exception:
        plates, source, state = [], 'write-journal', {}
        for w in sorted((e for e in events if e.get('kind') == 'write' and e.get('project') == project), key=lambda e: e.get('t', '')):
            state[w.get('object')] = w
        byp = {}
        for o, w in state.items():
            if w.get('plate_index') is not None:
                byp.setdefault(w['plate_index'], {'plate_index': w['plate_index'], 'plate': w.get('plate') or '', 'objects': []})['objects'].append(o)
        plates = [byp[k] for k in sorted(byp)]
    out = []
    for p in plates:
        objs = [{'object': o, 'level': _status(root, o, events, boxes=False)['level']} for o in p['objects']]
        levels = [o['level'] for o in objs]
        ask = [o['object'] for o in objs if o['level'] in ('ask', 'printed-unknown-version')]
        if not objs:
            st = None
        elif ask:
            st = None
        elif 'printed-changed' in levels:
            st = '[CHANGED]'
        elif all(lv == 'printed-current' for lv in levels):
            st = '[OK]'
        else:
            st = '[TODO]'
        now = re.match(r'^\s*(\[[^\]]*\])', p['plate'])
        row = {'plate': p['plate'], 'plate_index': p['plate_index'], 'status': st, 'objects': objs, 'now': now.group(1) if now else None, 'source': source}
        if ask:
            row['ask'] = 'the print record of %s is a guess or has no version: ask the owner' % ', '.join(repr(a) for a in ask)
        elif not objs:
            row['ask'] = 'an empty plate'
        out.append(row)
    return out


# ---------------------------------------------------------------- init and doctor

IGNORE_LINES = ('bambulab/ledger/local.json', 'bambulab/ledger/local/')
UNION_LINE = 'bambulab/ledger/events.jsonl merge=union'


def _has_line(path, line):
    try:
        with open(path, encoding='utf-8') as f:
            return any(x.strip() == line for x in f)
    except (IOError, OSError):
        return False


def init(folder):
    """Turn the ledger on in `folder`: bambulab/ledger/ with an empty events.jsonl, the two local paths in .gitignore and
    merge=union for the journal in .gitattributes. Returns the lines of what was done. Nothing existing is rewritten."""
    root = os.path.abspath(folder)
    done = []
    if not os.path.isdir(ledger_dir(root)):
        os.makedirs(ledger_dir(root))
        done.append('created bambulab/ledger/')
    if not os.path.exists(events_file(root)):
        with open(events_file(root), 'a'):
            pass
        done.append('created bambulab/ledger/events.jsonl')
    for path, lines in ((os.path.join(root, '.gitignore'), IGNORE_LINES), (os.path.join(root, '.gitattributes'), (UNION_LINE,))):
        missing = [x for x in lines if not _has_line(path, x)]
        if missing:
            tail = ''
            if os.path.exists(path):
                with open(path, encoding='utf-8') as f:
                    text = f.read()
                tail = '' if text.endswith('\n') or not text else '\n'
            with open(path, 'a', encoding='utf-8') as f:
                f.write(tail + '\n'.join(missing) + '\n')
            done.append('%s: added %s' % (os.path.basename(path), ', '.join(missing)))
    return done or ['the ledger was already on here']


def _carries_account_id(project_path):
    """Does the .3mf name the slicer account that saved it? Only yes or no: the value is never read out."""
    try:
        with zipfile.ZipFile(project_path) as z:
            with z.open('3D/3dmodel.model') as f:
                head = f.read(65536).decode('utf-8', 'replace')
        return bool(re.search(r'name="DesignerUserId"\s*>\s*[^<\s]', head))
    except Exception:
        return False


def doctor(root):
    """Lines about the state of the ledger: the journal, the sources, the copies, what waits for the owner.
    Returns (lines, problems)."""
    lines, problems = [], 0
    events = read_events(root)
    kinds = {k: sum(1 for e in events if e['kind'] == k) for k in KINDS}
    first = min((e.get('t', '') for e in events if e.get('kind') == 'write'), default=None)
    lines.append('ledger   %s' % _rel(root, ledger_dir(root)))
    lines.append('events   %s' % ', '.join('%d %s' % (kinds[k], k) for k in KINDS))
    bad = broken_lines(root)
    if bad:
        problems += 1
        lines.append('PROBLEM  %d line%s of events.jsonl %s not an event (skipped by every reader)' % (bad, '' if bad == 1 else 's', 'is' if bad == 1 else 'are'))
    lines.append('journal  %s' % ('first write %s: nothing is known about what was written before it' % first if first else
                                  'no write yet: the journal starts with the next .3mf the tools write'))
    for line in IGNORE_LINES:
        if not _has_line(os.path.join(root, '.gitignore'), line):
            lines.append('note     .gitignore does not list %s (ledger.py init adds it)' % line)
    if not _has_line(os.path.join(root, '.gitattributes'), UNION_LINE):
        lines.append('note     .gitattributes lacks "%s": two sessions appending at once may conflict' % UNION_LINE)
    try:
        c = _cache()
        why = c.why_off(root)
        if why:
            lines.append('cache    the slicer cache reader is OFF on this machine: %s' % why)
        else:
            local = c.local_jobs(root)
            tied = {e.get('id') for e in events if e.get('kind') == 'job'}
            lines.append('cache    the slicer cache reader is ON on this machine: %d local job%s, %d tied to objects of this project' % (
                len(local), '' if len(local) == 1 else 's', sum(1 for j in local if j.get('id') in tied)))
            lines.append('         the list is not complete (a job is in the cache only when the slicer showed it) and a sent job is not a kept part')
    except Exception as e:
        lines.append('cache    not available (%s)' % type(e).__name__)
    for project in sorted({e.get('project') for e in events if e.get('kind') == 'write' and e.get('project')}):
        if _carries_account_id(_abs(root, project)):
            lines.append('note     %s carries the slicer account\'s user id (DesignerUserId): in a public repository it is published with the file' % project)
    copies = sorted(f for f in os.listdir(printed_dir(root)) if f.endswith('.stl')) if os.path.isdir(printed_dir(root)) else []
    changed = 0
    for f in copies:
        m = re.search(r'__([0-9a-f]{8})\.stl$', f)
        try:
            if not m or _hash_of(os.path.join(printed_dir(root), f))[:8] != m.group(1):
                changed += 1
                lines.append('PROBLEM  printed/%s no longer holds the geometry its name says: an as-printed copy was rewritten' % f)
        except Exception:
            changed += 1
            lines.append('PROBLEM  printed/%s cannot be read' % f)
    problems += changed
    lines.append('copies   %d as-printed STL%s in printed/' % (len(copies), '' if len(copies) == 1 else 's'))
    waiting = [s for s in (_status(root, o, events, boxes=False) for o in objects_known(root, events)) if s['level'] in ('ask', 'printed-unknown-version')]
    for s in waiting:
        lines.append('owner    %s: %s' % (s['object'], s['why']))
    lines.append('limits   "no evidence" is not "not printed"; a fit against an as-printed copy is model to model')
    return lines, problems


# ---------------------------------------------------------------- command line

class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise LedgerError(message)


def parser():
    p = _Parser(prog='ledger.py', description='The print ledger: what was written into .3mf projects and what was printed.')
    p.add_argument('--root', help='project root (default: the nearest folder above the current one with bambulab/ledger/)')
    sub = p.add_subparsers(dest='command', parser_class=_Parser)
    s = sub.add_parser('init'); s.add_argument('folder', nargs='?', default='.')
    s = sub.add_parser('status'); s.add_argument('part', nargs='?'); s.add_argument('--stl'); s.add_argument('--json', action='store_true')
    s = sub.add_parser('may-change'); s.add_argument('part'); s.add_argument('--stl'); s.add_argument('--json', action='store_true')
    s = sub.add_parser('say'); s.add_argument('part', nargs='?'); s.add_argument('what', nargs='?')
    s.add_argument('--quote', required=True); s.add_argument('--hash'); s.add_argument('--stl'); s.add_argument('--plates'); s.add_argument('--project')
    s = sub.add_parser('scrap'); s.add_argument('part'); s.add_argument('--quote', required=True); s.add_argument('--hash')
    s = sub.add_parser('snap'); s.add_argument('part'); s.add_argument('--stl'); s.add_argument('--quote', required=True)
    s = sub.add_parser('note'); s.add_argument('part'); s.add_argument('text')
    s = sub.add_parser('jobs'); s.add_argument('--sync', action='store_true'); s.add_argument('--all', action='store_true'); s.add_argument('--json', action='store_true')
    s = sub.add_parser('tie'); s.add_argument('job'); s.add_argument('--object', action='append', default=[]); s.add_argument('--project')
    s.add_argument('--basis', choices=('picture', 'write-journal')); s.add_argument('--quote')
    for name in ('plates', 'plate-status'):
        s = sub.add_parser(name); s.add_argument('project'); s.add_argument('--json', action='store_true')
    s = sub.add_parser('cache'); s.add_argument('action', choices=('status', 'on', 'off')); s.add_argument('--quote')
    s.add_argument('--db', help='with "on": the cache database, when it is not in the usual place')
    sub.add_parser('doctor')
    return p


def _print_status(st, out):
    out('%-28s %s' % (st['object'], st['level']))
    out('  %s' % st['why'])
    for p in st['prints']:
        out('  %s print %s  version %s  %s%s%s' % (' ' if p['sure'] else '?', p['when'], p['hash'][:8] if p['hash'] else 'unknown', p['basis'],
                                                  ('  copy %s' % p['copy']) if p.get('copy') else '', ('  "%s"' % p['quote']) if p.get('quote') else ''))
    for p in st['ignored']:
        out('    not counted: %s %s, %s on %s ("%s")' % (p['when'], p['hash'][:8] if p['hash'] else 'unknown', p['ignored'], p['ignored_at'][:10], p['ignored_quote']))
    ch = st.get('changed')
    if ch and ch.get('boxes'):
        out('  moved since the print (sampled, model to model, up to %.2f mm):' % ch['max_mm'])
        m = _mesh()
        for b in ch['boxes'][:12]:
            out('    %s' % m.box_text(b))
    for n in st['notes']:
        out('  note %s: %s' % (n['t'][:10], n['text']))


def _plates_arg(text):
    try:
        return [int(x) for x in re.split(r'[,\s]+', text.strip()) if x]
    except ValueError:
        raise LedgerError('--plates wants numbers: 1,2,4')


def main(argv=None):
    """The command line above. Returns 0, or 2 when a may-change verdict is FIT or ASK-OWNER, or 4 when the ledger is
    off here (no bambulab/ledger folder) or the arguments are wrong."""
    out = lambda text='': print(text)
    try:
        args = parser().parse_args(argv)
        if not args.command:
            raise LedgerError('no command: status, may-change, say, scrap, snap, note, jobs, tie, plates, cache, doctor, init')
        if args.command == 'init':
            for line in init(args.root or args.folder):
                out(line)
            return 0
        root = os.path.abspath(args.root) if args.root else find_root(os.getcwd())
        if root is None or not os.path.isdir(ledger_dir(root)):
            raise LedgerError('the ledger is off here: no bambulab/ledger folder at or above %s (`ledger.py init` turns it on)' % (
                os.path.basename(os.path.abspath(args.root or os.getcwd())) or '.'))
        cmd = args.command
        if cmd == 'status':
            if args.part:
                obj = resolve_object(root, args.part)
                st = _status(root, obj, read_events(root), stl=args.stl)
                if obj not in objects_known(root):
                    st['why'] += '. NOTE: the journal has no object named %r (a typo? known: %s)' % (obj, ', '.join(repr(o) for o in objects_known(root)) or 'none')
                rows = [st]
            else:
                rows = status_all(root)
            if args.json:
                out(json.dumps(rows[0] if args.part else rows, indent=1, ensure_ascii=False))
            else:
                if not rows:
                    out('the journal knows no object yet: no evidence of any print (which is not "nothing was printed")')
                for st in rows:
                    _print_status(st, out)
            return 0
        if cmd == 'may-change':
            obj = resolve_object(root, args.part)
            if not args.stl and obj not in objects_known(root):
                raise LedgerError('the journal has no object named %r (known: %s). For a part that is new, give its STL: --stl' % (
                    obj, ', '.join(repr(o) for o in objects_known(root)) or 'none'))
            r = may_change(root, obj, args.stl)
            if args.json:
                out(json.dumps(r, indent=1, ensure_ascii=False))
            else:
                out('may-change %s%s' % (obj, (' (new STL %s)' % os.path.basename(args.stl)) if args.stl else ''))
                for line in r['rows']:
                    out('  ' + line)
                out('%s  %s' % (r['verdict'], r['why']))
            return 2 if r['verdict'] in ('FIT', 'ASK-OWNER') else 0
        if cmd == 'say':
            if args.plates or (args.part and re.match(r'^(plates?[\s:=#]*)?[\d,\s]+$', args.part)):
                numbers = _plates_arg(args.plates or re.sub(r'^plates?[\s:=#]*', '', args.part))
                q = plate_question(root, numbers, args.quote, _abs_arg(root, args.project))
                out(q['question'])
                return 2
            if not args.part or args.what not in SAID + ('scrapped',):
                raise LedgerError('say PART printed|not-printed|scrapped --quote "<the owner\'s words>"')
            obj = resolve_object(root, args.part)
            if args.what == 'printed' and _mentions_plates(args.quote, obj):
                numbers = [int(x) for x in re.findall(r'\d+', args.quote.lower().split('plate', 1)[1])]
                q = plate_question(root, numbers, args.quote, _abs_arg(root, args.project))
                out('The quote speaks of plate numbers and does not name %r.' % obj)
                out(q['question'])
                return 2
            h = None
            if args.stl:
                h = _hash_of(args.stl)
                if args.what == 'printed':
                    keep_printed_copy(root, obj, args.stl, h)
            elif args.hash:
                h = resolve_hash(root, args.hash)
            e = say(root, obj, args.what, args.quote, h, args.stl)
            if obj not in objects_known(root, [x for x in read_events(root) if x.get('kind') == 'write']):
                out('note: %r is not an object of the write journal (a part from before the journal, or a typo?)' % obj)
            if e['kind'] == 'scrap':
                out('recorded: %s scrapped%s' % (obj, (' (version %s)' % h[:8]) if h else ' (every print so far)'))
            elif args.what == 'not-printed':
                out('recorded: %s is not printed, on the owner\'s word' % obj)
            elif h:
                out('recorded: %s printed as %s%s' % (obj, h[:8], ('; as-printed copy %s' % e['copy']) if e.get('copy') else
                                                         '; NO as-printed copy: no STL of that version is on disk'))
            else:
                st = status(root, obj)
                out('recorded: %s printed, VERSION UNKNOWN (no --hash, no --stl)' % obj)
                if st['level'] == 'printed-unknown-version':
                    cands = st.get('candidates') or []
                    out('  ask the owner which version: %s' % (', '.join(c[:8] for c in cands) if cands else 'the journal has no write of this object'))
                else:
                    out('  the journal ties it to one version: %s' % st['why'])
            return 0
        if cmd == 'scrap':
            obj = resolve_object(root, args.part)
            h = resolve_hash(root, args.hash) if args.hash else None
            say(root, obj, 'scrapped', args.quote, h)
            out('recorded: %s scrapped%s' % (obj, (' (version %s)' % h[:8]) if h else ' (every print so far)'))
            return 0
        if cmd == 'snap':
            obj = resolve_object(root, args.part)
            e = snap(root, obj, args.quote, args.stl)
            out('recorded: %s printed as %s; as-printed copy %s' % (obj, e['hash'][:8], e['copy']))
            return 0
        if cmd == 'note':
            obj = resolve_object(root, args.part)
            note(root, obj, args.text)
            out('noted for %s' % obj)
            return 0
        if cmd == 'jobs':
            c = _cache()
            why = c.why_off(root)
            if why:
                out('the slicer cache reader is OFF on this machine: %s' % why)
                out('it is switched on per machine, by the owner\'s yes: ledger.py cache on --quote "<his words>"')
            elif args.sync:
                if not c.source(root):
                    out('source unavailable: the slicer\'s cache database was not found on this machine')
                n = c.sync(root)
                out('%d new job%s read from the slicer cache' % (n, '' if n == 1 else 's'))
                if c.LAST_ERROR:
                    out('the cache database could not be read (%s)' % c.LAST_ERROR)
            view = jobs_view(root) if not why else []
            if not args.all:
                view = view[-30:]
            committed = read_events(root, 'job')
            if args.json:
                out(json.dumps({'local': view, 'committed': committed}, indent=1, ensure_ascii=False))
                return 0
            for j in view:
                t = j['tied']
                out('%s  sent %s  plate %s  picture %s%s' % (j['id'], j['sent_at'], j.get('plate_index'), j.get('thumb') or '-',
                                                              ('  TIED to %s (%s)' % (', '.join(o['object'] for o in t.get('objects') or []), t.get('basis'))) if t else ''))
                if not t:
                    for cand in j['candidates']:
                        out('    the write journal had on that plate number then: %s (%s, plate %r): a hint, look at the picture' % (
                            ', '.join(repr(o) for o in cand['objects']), cand['project'], cand['plate']))
            if not why:
                out('%d local job%s shown (times are UTC). Local only: a job enters the journal when it is tied to an object (ledger.py tie).' % (len(view), '' if len(view) == 1 else 's'))
                out('A sent job is not a kept part, and the list is not complete.')
            if why and committed:
                out('%d job%s already tied in the journal' % (len(committed), '' if len(committed) == 1 else 's'))
            return 0
        if cmd == 'tie':
            e = tie(root, args.job, args.object, args.basis, _abs_arg(root, args.project), args.quote)
            out('tied %s (sent %s) to %s, basis %s' % (e['id'], e['sent_at'], ', '.join('%s%s' % (o['object'], (' ' + o['model'][:8]) if o.get('model') else ' (no write before the job: version unknown)')
                                                                                         for o in e['objects']), e['basis']))
            for c in e['copies']:
                out('  as-printed copy %s' % c)
            if e['basis'] != 'owner':
                out('  still a guess: status says "ask" until the owner confirms (say <object> printed --quote "...")')
            return 0
        if cmd in ('plates', 'plate-status'):
            rows = plate_status(root, _abs_arg(root, args.project))
            if not rows and not os.path.isfile(_abs_arg(root, args.project)):
                raise LedgerError('%s is not a file, and the journal has no write of it' % args.project)
            if args.json:
                out(json.dumps(rows, indent=1, ensure_ascii=False))
                return 0
            if rows and rows[0]['source'] != 'file':
                out('the file could not be read: the plates below are those of the write journal, not what the slicer shows now')
            for r in rows:
                out('plate %-3s %-10s %s%s' % (r['plate_index'], r['status'] or 'ASK', r['plate'], '' if r['status'] in (None, r['now']) else '   (now %s)' % (r['now'] or 'no prefix')))
                for o in r['objects']:
                    out('    %-26s %s' % (o['object'], o['level']))
                if r.get('ask'):
                    out('    %s' % r['ask'])
            out('a proposal only: the project\'s own writer applies it. [TODO] here means "no evidence of a print", not "not printed".')
            return 0
        if cmd == 'cache':
            c = _cache()
            if args.action == 'on':
                for line in c.switch(root, True, args.quote, cache_db=getattr(args, 'db', None)):
                    out(line)
            elif args.action == 'off':
                for line in c.switch(root, False):
                    out(line)
            else:
                why = c.why_off(root)
                out('the slicer cache reader is %s on this machine%s' % ('OFF' if why else 'ON', (': ' + why) if why else ''))
                out(c.CONSENT)
            return 0
        if cmd == 'doctor':
            lines, problems = doctor(root)
            for line in lines:
                out(line)
            out('PROBLEMS %d' % problems)
            return 0
        raise LedgerError('unknown command %r' % cmd)
    except (LedgerError, ValueError) as e:
        sys.stderr.write('LEDGER ERROR  %s\n' % e)
        return 4
    except ImportError as e:
        sys.stderr.write('LEDGER ERROR  a tool file is missing beside ledger.py (%s)\n' % e)
        return 4
    except (IOError, OSError, zipfile.BadZipFile) as e:
        sys.stderr.write('LEDGER ERROR  a file could not be read or written: %s\n' % e)
        return 4


def _abs_arg(root, path):
    """A path given on the command line: as it is when it exists, else relative to the root."""
    if not path:
        return None
    if os.path.exists(path) or os.path.isabs(path):
        return os.path.abspath(path)
    return os.path.join(root, path)


if __name__ == '__main__':
    sys.exit(main())
