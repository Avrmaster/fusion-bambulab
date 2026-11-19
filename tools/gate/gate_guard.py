"""The guard inside the one .3mf writer: what bambu3mf.save() calls before it replaces a project file.

    save(proj, files, order)                                  tools/bambu/bambu3mf.py
      root = nearest folder above proj with gate/project.json; none -> exactly the old behaviour, nothing below runs
      result = gate_guard.check(proj, meshes, root, files=files, asked=notes)     may raise GuardRefusal in enforce mode
      ... the archive is written ...
      ledger.record_writes(proj, meshes, result["stamps"])    one line per object in bambulab/ledger/events.jsonl

`meshes` is made by bambu3mf from the archive it is about to write, one entry per printable-part object (modifier and
blocker volumes are left out):
    {"object": "housing", "plate": "plate name" or None, "plate_index": 1 or None, "printable": True,
     "vertices": [(x, y, z), ...], "triangles": [(a, b, c), ...],        as placed in the .3mf (the "pose" form)
     "stl": absolute path of the STL this process wrote the mesh from, or None (the mesh was only carried),
     "model": meshid.geom_hash of that STL (optional: read from the file here when it is missing)}

Modes (gate/project.json "mode"):
  observe  never raises and changes nothing in the save. One line per unstamped or stale NEW mesh goes to
           gate/reports/guard.log and one summary line is printed. An internal error of the guard is logged and
           swallowed: it can never break a save.
  enforce  raises GuardRefusal when a NEW mesh has no valid stamp; nothing is written then. A guard that cannot judge
           (a broken gate file, an error of its own) also refuses in this mode: what was not checked has not passed.

A mesh is NEW when its pose hash (meshid.geom_hash of the mesh as placed) is neither in the file on disk nor among the
`write` events of the ledger for this project file, and its source STL's geometry (model form) is not among those events
either (the same STL placed again on another face or at another angle is still the same part). A mesh of the file on disk
whose triangles were merged or repaired (weld_3mf.py: the same vertices, another triangle count) is carried too. A
carried mesh is never judged: saves that only change settings or plates always go through.

A .3mf under a gated root that is NOT a registered project of gate/layout.json is never written in silence: the guard
prints and logs one line that says so. In observe mode that is all (the file is not judged). In enforce mode it is
judged like a registered project with no declared target: a new mesh without a valid stamp is refused there too. So
"one of four projects registered" does not leave three open, and a trial copy of a project behaves as the project does.

The binding journal is the ledger's bambulab/ledger/events.jsonl. Before it judges, the guard writes one `write` event
with "tool": "carried" for every mesh of the file on disk that the journal does not know yet, in both modes. That is why
a build script that first saves the empty template over the project and then saves the parts is not locked out: the
second save finds its meshes in the journal. It is also how a project is adopted: the meshes it holds at the first
guarded save are carried from then on.

The stamp of a new mesh is found through its source STL: meshid.geom_hash of that STL (model form) -> gate_stamp. An
STL that is a quarter-turned copy of the stamped one (a part turned onto its bed face before a swap) is found by
meshid.turned_hashes. The stamp must be valid for the target declared for this project in gate/project.json.

Fit settings (rule project.settings_changed_by_write). `files` is the archive about to be written ({member: bytes}).
Before anything else the guard reads the fit settings (the parameter target.fit_settings: hole compensation, layer
height, ...) of that archive and of the file on disk, the global values and the per-object overrides, and compares
them (probes.project_consistency.settings_diff). `asked` are the notes of what the caller means to change
(bambu3mf.note_setting: set_project, set_settings, build and the like write them). A changed fit setting that no note
covers:
  observe  one loud line, printed and logged; the save goes on
  enforce  GuardRefusal; nothing is written
A change a note covers is printed and logged in one line and passes. This runs for every .3mf under the gated root,
registered or not, and needs no declared target. A writer that hands over no archive (an older bambu3mf.py) is said
so; in enforce mode its save is refused: what was not compared has not passed.

The guard remembers the fit settings of every project file it has seen: gate/fit_settings.json holds, per file, what
the file held after the last guarded save (or on disk, when that save was refused). A file whose global values differ
from that at the next save was changed by something that is not a tool write (the slicer saved it, or a file was
copied over it): one line says which values, in both modes; it is not refused, `gate.py verify` is the check for it. When the file on disk is missing,
is no archive or holds no settings, the archive is compared with that remembered state: deleting the project and
building it again from a template does not get a changed hole compensation past the guard. With nothing remembered:
  no file on disk, or one without settings   nothing to lose and nothing to compare: the save goes on in both modes
                                             (`gate.py verify` holds the written file against its declared target)
  a file that cannot be read                 not compared: one loud line in observe mode, refused in enforce mode"""
import os, re

import gate_common
from gate_common import GateError, BLOCKING, EXIT_PASS, EXIT_BLOCK, EXIT_INFRA


class GuardRefusal(GateError):
    """enforce mode: the save would bring in a mesh without a valid stamp (`.rows` lists the meshes and why), or it
    changes a fit setting the caller did not name (`.rows` holds the findings of project.settings_changed_by_write)."""

    def __init__(self, message, rows=None):
        GateError.__init__(self, message)
        self.rows = list(rows or [])


def log(root, line):
    """Append one line with the time to gate/reports/guard.log. Never raises."""
    try:
        path = gate_common.gate_path(root, 'reports', 'guard.log')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a', encoding='utf-8') as f:
            f.write('%s %s\n' % (gate_common.now(), ' '.join(str(line).split())))
    except Exception:
        pass


def registered_project(root, path, layout=None):
    """The entry of gate/layout.json "projects" whose "file" is this path, or None (the path is not guarded)."""
    layout = layout if layout is not None else gate_common.load_layout(root)
    want = os.path.realpath(path)
    for entry in layout.get('projects') or []:
        f = entry.get('file') if isinstance(entry, dict) else None
        if f and os.path.realpath(f if os.path.isabs(f) else os.path.join(root, f)) == want:
            return entry
    return None


def _disk_meshes(path):
    """[{"object", "plate", "plate_index", "pose", "vset"}] of the file on disk; [] when it is missing or cannot be read."""
    meshid = gate_common.sibling('meshid')
    try:
        if not os.path.isfile(path):
            return []
        out = []
        for m in meshid.read_project(path):
            pose, vset = meshid.both_hashes(m['vertices'], len(m['triangles']))
            out.append({'object': m['object'], 'plate': m['plate'], 'plate_index': m['plate_index'], 'pose': pose, 'vset': vset})
        return out
    except Exception:
        return []


def _journal(root, project_rel, path):
    """(pose hashes, model hashes) of the `write` events of this project file in the ledger's journal."""
    poses, models, same = set(), set(), {}
    try:
        events = gate_common.read_jsonl(gate_common.events_path(root))
    except Exception:
        return poses, models
    want = os.path.realpath(path)
    for e in events:
        if not isinstance(e, dict) or e.get('kind') != 'write' or not isinstance(e.get('project'), str):
            continue
        p = e['project']
        if p not in same:
            same[p] = p == project_rel or os.path.realpath(p if os.path.isabs(p) else os.path.join(root, p)) == want
        if same[p]:
            if e.get('pose'):
                poses.add(e['pose'])
            if e.get('model'):
                models.add(e['model'])
    return poses, models


def known_poses(root, project_rel, path):
    """Pose hashes that count as carried for this project file: those of the meshes in the file on disk (when it exists
    and can be read) plus those of every `write` event of bambulab/ledger/events.jsonl for this project. Returns a set."""
    return {m['pose'] for m in _disk_meshes(path)} | _journal(root, project_rel, path)[0]


def _bind_carried(root, project_rel, disk, journal_poses):
    """Write the meshes of the file on disk that the journal does not know yet into it: one `write` event each, in the
    format of the ledger, with "tool": "carried". Returns how many lines were written. Never raises."""
    n, seen = 0, set(journal_poses)
    try:
        for m in disk:
            if m['pose'] in seen:
                continue
            seen.add(m['pose'])
            gate_common.append_jsonl(gate_common.events_path(root), {
                'v': 1, 't': gate_common.now(), 'by': 'tool', 'kind': 'write', 'project': project_rel, 'object': m['object'],
                'plate': m['plate'], 'plate_index': m['plate_index'], 'pose': m['pose'], 'model': None, 'stl': None,
                'stl_sha256': None, 'stamp': None, 'cad': None, 'tool': 'carried'})
            n += 1
    except Exception as e:
        log(root, 'could not write carried meshes of %s into the journal: %s: %s' % (project_rel, type(e).__name__, e))
    return n


def _shown(root, path):
    """A path for a message: relative to the root when it lies under it."""
    if not path:
        return None
    r = gate_common.rel(root, os.path.realpath(path))
    return path if r.startswith('..') else r


def _judge_mesh(root, mesh, model, ctx):
    """(state, why, part id or None, stamp name or None) for one new mesh."""
    import gate_stamp
    meshid = gate_common.sibling('meshid')
    stl = mesh.get('stl')
    if not stl:
        return 'none', 'no source STL is known for this mesh: place it from an exported STL with build(), update_meshes() or add_plate', None, None
    if not model:
        return 'none', 'its source STL %s cannot be read' % _shown(root, stl), None, None
    if 'stamps' not in ctx:
        ctx['stamps'] = gate_stamp.all_stamps(root)
    cands, how = [s for s in ctx['stamps'] if s.get('geom_hash') == model], ''
    if not cands and ctx['stamps'] and os.path.isfile(stl):
        V, F = meshid.read_stl(stl)
        turned = set(meshid.turned_hashes(V, len(F)))
        cands, how = [s for s in ctx['stamps'] if s.get('geom_hash') in turned], '; the STL is a quarter-turned copy of the stamped one'
    if not cands:
        return 'none', 'no stamp for the geometry of %s: export the part through the gate (gate.py export)' % _shown(root, stl), None, None
    base = os.path.basename(stl)
    cands.sort(key=lambda s: os.path.basename(str(s.get('stl') or '')) != base)     # identical bodies: the one of this file first
    if 'reg' not in ctx:
        import gate_registry
        ctx['reg'] = gate_registry.load_default()
    if 'census' not in ctx:
        ctx['census'] = gate_common.load_census(root)
    first = None
    for s in cands:
        state, why = gate_stamp.verify(root, ctx['reg'], s, geom_hash=s.get('geom_hash'), census=ctx['census'], target=ctx.get('target'))
        if state == 'valid':
            return 'valid', why + how, s['part'], gate_stamp.name_of(s)
        first = first or (state, why, s['part'])
    return first[0], first[1], first[2], None


def fit_setting_keys(root):
    """The fit settings this project compares (the parameter target.fit_settings): the owner's value in
    gate/owner/params.json, else the registry default, else the list the probe ships with. Never raises: a registry or
    an owner file that cannot be read must not stop a save from being compared."""
    from probes import project_consistency
    value = None
    try:
        import gate_params
        entry = gate_params.load_overrides(root).get('target.fit_settings')
        value = entry.get('value') if isinstance(entry, dict) else None
    except Exception:
        value = None
    if not (isinstance(value, str) and value.strip()):
        try:
            import gate_registry
            value = gate_registry.load()['params']['target.fit_settings']['default']
        except Exception:
            value = None
    return project_consistency.fit_keys(value)


def _memory_path(root):
    return gate_common.gate_path(root, 'fit_settings.json')


def remembered(root, project_rel):
    """What the guard last knew of the fit settings of one project file: {"time", "settings": read_fit_settings() facts},
    or None. Never raises."""
    try:
        data = gate_common.read_json(_memory_path(root), {})
        entry = (data.get('projects') or {}).get(project_rel) if isinstance(data, dict) else None
        return entry if isinstance(entry, dict) and isinstance(entry.get('settings'), dict) else None
    except Exception:
        return None


def _remember(root, project_rel, settings):
    """Keep the fit settings a project file holds now (read_fit_settings facts) in gate/fit_settings.json. Never raises."""
    if not isinstance(settings, dict) or (settings.get('global') is None and settings.get('objects_read') is None):
        return
    try:
        path = _memory_path(root)
        try:
            data = gate_common.read_json(path, {})
        except GateError:
            data = {}
        if not isinstance(data, dict) or not isinstance(data.get('projects'), dict):
            data = {'_about': 'Written by the gate guard: the fit settings each .3mf held after its last guarded save. Compared with when the file is missing or unreadable at the next save.', 'projects': {}}
        old = data['projects'].get(project_rel)
        if isinstance(old, dict) and gate_common.canonical(old.get('settings')) == gate_common.canonical(settings):
            return
        data['projects'][project_rel] = {'time': gate_common.now(), 'settings': settings}
        gate_common.write_json(path, data)
    except Exception as e:
        log(root, 'could not remember the fit settings of %s: %s: %s' % (project_rel, type(e).__name__, e))


def check_settings(root, path, files, asked=None, project_key=None):
    """Compare the fit settings of the archive about to be written with the file on disk and let the judge of
    project.settings_changed_by_write decide.

    Returns {"compared": bool, "why": text when not, "changed": the rows of settings_diff(),
             "refused": [FAIL and NOT_RUN findings], "asked": [INFO findings: changes the caller named],
             "with": "disk" | "memory" | None (what the archive was compared with), "outside": [{"key", "before",
             "after", "since"}] global values of the file on disk that differ from what the last guarded save left in
             it (the slicer saved the project, or a file was copied over it), "disk": the facts of the file on disk or
             None,
             "after": the facts of the archive or None}.
    The file on disk is missing, is no archive or holds no settings: the archive is compared with what the guard
    remembers of this file (remembered()). Nothing remembered: a missing file or one without settings has nothing to
    lose ("compared" False, "said" set, no finding); a file that cannot be read is a NOT_RUN finding.
    files None (the writer handed over no archive) while there is something to compare with: "compared" False with a
    NOT_RUN finding."""
    from probes import project_consistency
    import gate_judges
    out = {'compared': False, 'why': None, 'changed': [], 'refused': [], 'asked': [], 'with': None, 'disk': None, 'after': None, 'outside': []}
    keys = fit_setting_keys(root)
    rule = 'project.settings_changed_by_write'
    prel = gate_common.rel(root, path)
    ctx = {'part': '%s:' % (project_key or '?'), 'rule': rule, 'tables': {}, 'target': None, 'census': None, 'prev': None, 'intent': None}
    facts = {'project': project_key or '?', 'file': prel}
    before, gone = None, None                            # gone: why the file on disk gives nothing to compare with
    if not os.path.isfile(path):
        gone = 'the file is not on disk'
    else:
        try:
            before = project_consistency.read_fit_settings(path, keys)
        except GateError:
            before, gone = None, 'the file on disk cannot be read as a project'
        if before is not None and before.get('global') is None and before.get('objects_read') is None:
            before, gone = None, 'the file on disk holds no settings'
    out['disk'] = before
    if files is not None:
        try:
            out['after'] = project_consistency.read_fit_settings(files, keys)
        except GateError:
            out['after'] = None
    if before is None:
        mem = remembered(root, prel)
        if mem is None:
            out['why'] = gone
            if gone == 'the file on disk cannot be read as a project':
                facts['settings_write'] = {'keys': keys, 'compared': False, 'changed': [],
                                           'why': '%s, and the guard remembers no earlier state of it: delete the broken file to write a new one' % gone}
                found = gate_judges.JUDGES['project_settings_changed_by_write'](facts, {'target.fit_settings': ' '.join(keys)}, ctx)
                out['refused'] = [dict(f, rule=rule) for f in found if f.get('verdict') in BLOCKING]
            return out                                   # a first write, or a file without settings: nothing to lose, nothing said
        before = mem['settings']
        keys = [k for k in keys if k in (before.get('keys') or [])]      # what was not remembered cannot be compared
        out['with'], out['why'] = 'memory', '%s: compared with what it held at %s (gate/fit_settings.json)' % (gone, mem.get('time'))
    else:
        out['with'] = 'disk'
        mem = remembered(root, prel)                     # what the last guarded save left in the file: did something else change it since?
        old = (mem or {}).get('settings', {}).get('global') if mem else None
        now = before.get('global')
        if isinstance(old, dict) and isinstance(now, dict):
            out['outside'] = [{'key': k, 'before': old[k], 'after': now[k], 'since': mem.get('time')} for k in keys
                              if k in old and k in now and not project_consistency.same_value(old[k], now[k])]
    if files is None:
        facts['settings_write'] = {'keys': keys, 'compared': False, 'changed': [],
                                   'why': 'the writer handed over no archive: bambulab/tools/bambu3mf.py is older than the gate (setup_project.py --update-tools)'}
    else:
        after = out['after'] if out['with'] == 'disk' else project_consistency.read_fit_settings(files, keys)
        if isinstance(after, dict) and out['with'] == 'memory':
            after = dict(after, keys=keys)
            before = dict(before, keys=keys)
        facts['settings_write'] = project_consistency.settings_diff(before, after, asked)
    found = gate_judges.JUDGES['project_settings_changed_by_write'](facts, {'target.fit_settings': ' '.join(fit_setting_keys(root))}, ctx)
    w = facts['settings_write']
    out.update(compared=bool(w.get('compared')), why=w.get('why') or out['why'], changed=list(w.get('changed') or []))
    out['refused'] = [dict(f, rule=rule) for f in found if f.get('verdict') in BLOCKING]
    out['asked'] = [dict(f, rule=rule) for f in found if f.get('verdict') not in BLOCKING]
    return out


def _say(root, line):
    """Log one line and print it. Printing never raises."""
    log(root, line)
    try:
        print(line)
    except Exception:
        pass


def _guard_settings(root, path, files, asked, mode, key, prel):
    """The fit settings part of one save: logs, prints, and in enforce mode refuses. Returns what goes into the result
    under "settings" (check_settings()). In observe mode nothing in here can break the save."""
    try:
        got = check_settings(root, path, files, asked, key)
    except Exception as e:
        text = 'gate guard [%s] %s: the fit settings of this save could not be compared: %s: %s' % (mode, prel, type(e).__name__, e)
        log(root, text)
        if mode == 'enforce':
            raise GuardRefusal(text + '\nNothing was written (enforce mode): what was not compared has not passed.')
        try:
            print(text + ' - written anyway (observe mode)')
        except Exception:
            pass
        return {'compared': False, 'why': '%s: %s' % (type(e).__name__, e), 'changed': [], 'refused': [], 'asked': [], 'with': None, 'disk': None, 'after': None, 'outside': []}
    for f in got['asked']:                                  # a fit setting changed because the caller asked: said, never silent
        _say(root, 'gate guard [%s] %s: %s' % (mode, prel, f.get('text')))
    if got.get('outside'):                                  # no tool write did this: said once, the memory follows the file after this save
        _say(root, 'gate guard [%s] %s: since the last guarded save (%s) the fit settings of this file changed outside the tools: %s. Bambu Studio saved '
                   'it, or a file was copied over it; `gate.py verify` holds it against its declared target'
             % (mode, prel, got['outside'][0].get('since'), '; '.join('%s from %s to %s' % (r['key'], r['before'], r['after']) for r in got['outside'])))
    if got['refused']:
        where = ' (%s)' % got['why'] if got.get('with') == 'memory' and got.get('why') else ''
        head = 'gate guard [%s] %s: FIT SETTINGS CHANGED BY THIS WRITE and not asked for%s: ' % (mode, prel, where) if got['compared'] else 'gate guard [%s] %s: ' % (mode, prel)
        line = head + '; '.join(f.get('text') or '' for f in got['refused'])
        for f in got['refused']:
            log(root, '%s %s: %s %s' % (mode, prel, f.get('verdict'), f.get('text')))
        if mode == 'enforce':
            log(root, '%s REFUSED' % line)
            _remember(root, prel, got.get('disk'))
            raise GuardRefusal('%s\nNothing was written. Every fit of this project was measured under the values on disk. A change that is meant is '
                               'made with bambu3mf.set_project / set_settings / set_3mf_setting.py, or named with bambu3mf.note_setting(key, value) '
                               'before the save; then the owner changes the declared target in gate/project.json.' % line, got['refused'])
        line += ' - written anyway (observe mode; enforce would refuse this save)'
        _say(root, line)
    return got


def _names(names, limit=6):
    names = list(names)
    return ', '.join(names[:limit]) + (', ... (%d more)' % (len(names) - limit) if len(names) > limit else '')


def _check(root, path, meshes, files=None, asked=None):
    project = gate_common.load_project(root); mode = project['mode']
    layout = gate_common.load_layout(root)
    entry = registered_project(root, path, layout)
    res = {'gated': True, 'mode': mode, 'project': entry.get('key') if entry else None, 'meshes': len(meshes) if meshes is not None else 0,
           'new': [], 'unstamped': [], 'stale': [], 'rows': [], 'stamps': {}, 'line': ''}
    prel = gate_common.rel(root, path)
    got = _guard_settings(root, path, files, asked, mode, entry.get('key') if entry else None, prel)
    res['settings'] = {k: v for k, v in got.items() if k not in ('disk', 'after')}
    try:
        res = _check_meshes(root, path, meshes, project, layout, entry, res, prel)
    except GuardRefusal:                                    # refused: the file stays as it is on disk
        _remember(root, prel, got.get('disk'))
        raise
    except Exception:                                       # the judging broke: enforce refuses, observe writes (check())
        _remember(root, prel, got.get('disk') if mode == 'enforce' else (got.get('after') or got.get('disk')))
        raise
    _remember(root, prel, got.get('after') or got.get('disk'))      # the save goes on: this is what the file holds next
    return res


def _check_meshes(root, path, meshes, project, layout, entry, res, prel):
    """The mesh part of one save (see the top): fills and returns `res`, raises GuardRefusal in enforce mode."""
    mode = project['mode']
    note = ''
    if entry is None:
        note = ('gate guard [%s] %s is not a registered project of gate/layout.json ("projects"): register it, with its declared '
                'target in gate/project.json' % (mode, prel))
        if mode != 'enforce':
            res['line'] = note + ' - NOT guarded, written as it is (observe mode; enforce judges such a file like a registered one)'
            log(root, res['line'])
            print(res['line'])
            return res
        entry = {}                                          # enforce: judged like a registered project, no declared target
        log(root, note)
        print(note + ' - judged like a registered project, no declared target')
    if meshes is None:
        raise TypeError('the meshes of %s were not handed over (they could not be read from the archive)' % prel)
    meshid = gate_common.sibling('meshid')
    disk = _disk_meshes(path)
    poses, models = _journal(root, prel, path)
    bound = _bind_carried(root, prel, disk, poses)
    known = poses | {m['pose'] for m in disk}
    welded = {(m['object'], m['vset']) for m in disk}
    ctx = {'target': (project.get('targets') or {}).get(entry.get('key'))}
    for m in meshes:
        name = m.get('object')
        pose, vset = meshid.both_hashes(m['vertices'], len(m['triangles']))
        if pose in known or (name, vset) in welded:         # the second: the same vertices with other triangles (a weld, a repair)
            continue
        stl, model = m.get('stl'), m.get('model')
        try:
            if stl and not model and os.path.isfile(stl):
                model = meshid.stl_ids(stl)['geom_hash']
            if model and model in models:
                continue                                    # this STL geometry was placed in this project before: carried
            state, why, part, stamp = _judge_mesh(root, m, model, ctx)
        except GuardRefusal:
            raise
        except Exception as e:                              # what could not be judged has not passed
            state, why, part, stamp = 'none', 'could not be judged: %s: %s' % (type(e).__name__, e), None, None
        res['new'].append(name)
        res['rows'].append({'object': name, 'state': state, 'why': why, 'part': part, 'stl': _shown(root, stl)})
        if state == 'valid':
            res['stamps'][name] = stamp
        else:
            res['stale' if state == 'stale' else 'unstamped'].append(name)
    bad = [r for r in res['rows'] if r['state'] != 'valid']
    line = 'gate guard [%s] %s: %d mesh(es), %d new' % (mode, prel, len(meshes), len(res['new']))
    if res['unstamped']:
        line += ', %d without a stamp (%s)' % (len(res['unstamped']), _names(res['unstamped']))
    if res['stale']:
        line += ', %d with a stale stamp (%s)' % (len(res['stale']), _names(res['stale']))
    if bound:
        line += ', %d already in the file now carried' % bound
    res['line'] = line
    for r in bad:
        log(root, '%s %s: %s %s: %s' % (mode, prel, r['object'], r['state'], r['why']))
    if bad and mode == 'enforce':
        log(root, '%s REFUSED' % line)
        emptied = '' if disk or not os.path.isfile(path) else ('\nThe file on disk holds no mesh now: an earlier save of this run put an empty '
                                                               'project over it. Run the build again after the export.')
        raise GuardRefusal('%s\n%s\nNothing was written. Export these parts through the gate first (gate.py export PART), or ask the '
                           'owner: only gate/project.json "mode" decides this.%s' % (line, '\n'.join('  %s: %s' % (r['object'], r['why']) for r in bad), emptied), bad)
    if bad:
        res['line'] = line = line + ' - written anyway (observe mode; enforce would refuse this save)'
        log(root, line)
        print(line)
    return res


def _enforce(root):
    """Is the project in enforce mode? Read from the text when the file is broken: a broken owner file must not
    switch the guard off."""
    try:
        return gate_common.load_project(root)['mode'] == 'enforce'
    except Exception:
        try:
            with open(gate_common.gate_path(root, 'project.json'), encoding='utf-8', errors='replace') as f:
                return re.search(r'"mode"\s*:\s*"enforce"', f.read()) is not None
        except Exception:
            return False


def check(path, meshes, root=None, files=None, asked=None):
    """Judge the meshes and the fit settings of a save. Called by bambu3mf.save() before the file is replaced.

    path    the .3mf about to be written        meshes  see the top
    root    the project root; None = found from the path. No gate/project.json above the path: returns {"gated": False}.
    files   the archive about to be written, {member name: bytes}: its fit settings are compared with the file on disk
    asked   the settings the caller means to change: [{"key", "value" (None = any), "scope": "global" | an object name |
            "*", "tool"}] (bambu3mf.note_setting). A changed fit setting no note covers is refused in enforce mode and
            is one loud line in observe mode. The result holds the comparison under "settings" (check_settings).
    The registry is loaded here (gate_registry.load_default()) only when a new mesh has to be judged.

    Returns {"gated": bool, "mode": "observe" | "enforce", "project": key or None (None = not a registered project: not
    guarded), "meshes": n, "new": [object names], "unstamped": [object names], "stale": [object names],
    "rows": [{"object", "state": "valid" | "stale" | "none", "why", "part", "stl"}] for the new meshes ("project" None
    with rows: an unregistered file judged in enforce mode),
    "stamps": {object name: "<part id>@<sig12>" or None} for every mesh that has a valid stamp, "line": summary text}.
    observe: logs, prints the line when anything is unstamped or stale, never raises (an internal error returns
    {"gated": True, "error": text, ...} after logging it). enforce: raises GuardRefusal when "unstamped" or "stale" is
    not empty, and when the guard itself fails."""
    try:
        root = root or gate_common.find_root(path)
    except Exception:
        root = None
    if root is None:
        return {'gated': False}
    try:
        root, path = os.path.realpath(root), os.path.realpath(path)
        return _check(root, path, meshes, files, asked)
    except GuardRefusal:
        raise
    except Exception as e:
        text = 'gate guard: this save could not be judged: %s: %s' % (type(e).__name__, e)
        log(root, text)
        if _enforce(root):
            raise GuardRefusal(text + '\nNothing was written (enforce mode): repair the gate files first.')
        try:
            print(text + ' - written anyway (observe mode)')
        except Exception:
            pass
        return {'gated': True, 'mode': 'observe', 'project': None, 'meshes': len(meshes) if isinstance(meshes, (list, tuple)) else 0,
                'new': [], 'unstamped': [], 'stale': [], 'rows': [], 'stamps': {}, 'line': text, 'error': '%s: %s' % (type(e).__name__, e)}


# ---- gate.py verify --------------------------------------------------------------------------------------------------

def verify_project(root, reg, resolved, project_entry, layout=None, census=None):
    """Facts and findings of one registered project: runs probes.project_consistency.measure() and every project-stage
    judge (gate_judges.run_judge with ctx["part"] = "<project key>:"), applies the waivers.
    Returns {"project": key, "facts": {...}, "open": [findings], "waived": [findings], "info": [findings]}."""
    import gate_registry, gate_judges, gate_params, gate_features
    from probes import project_consistency
    layout = layout if layout is not None else gate_common.load_layout(root)
    census = census if census is not None else gate_common.load_census(root)
    key = project_entry.get('key')
    declared = (gate_common.load_project(root).get('targets') or {}).get(key)
    fit = resolved.get('target.fit_settings') if isinstance(resolved, dict) else None
    facts = project_consistency.measure(root, project_entry, layout, census, declared_target=declared, reg=reg,
                                        fit_settings=fit.get('value') if isinstance(fit, dict) else None)
    findings = []
    for rule in gate_registry.rules(reg, level='block', stage='project'):
        ctx = {'part': '%s:' % key, 'rule': rule['id'], 'tables': reg.get('tables') or {}, 'target': declared,
               'census': None, 'prev': None, 'intent': None}
        findings += gate_judges.run_judge(rule, facts, gate_params.values(resolved, rule.get('params') or []), ctx)
    waivers = (gate_common.read_json(gate_common.gate_path(root, 'owner', 'waivers.json'), {}) or {}).get('waivers') or []
    blocking = [f for f in findings if f.get('verdict') in BLOCKING]
    out = {'project': key, 'facts': facts, 'open': [], 'waived': [], 'info': [f for f in findings if f.get('verdict') not in BLOCKING], 'waivers': []}
    for part in sorted({f.get('part') for f in blocking}, key=str):
        still, waived, rows = gate_features.apply_waivers([f for f in blocking if f.get('part') == part], waivers, part=part)
        out['open'] += still; out['waived'] += waived; out['waivers'] += rows
    return out


def cmd_verify(root, args):
    """gate.py verify [PROJECT.3mf]. No Fusion. For every registered .3mf project (or the one named by args.project):
    probes.project_consistency.measure() -> the project-stage judges -> a report (command "verify", stage "project").
    Prints its summary, and one line for every .3mf and every folder of STL files in the tree that the layout does not
    name (gate_common.unregistered). Returns 0 pass, 1 open block findings, 4 a project file cannot be read or is not
    registered, or no project is registered at all ("nothing was verified" is never exit 0)."""
    import gate_registry, gate_params
    layout = gate_common.load_layout(root)
    entries = [e for e in layout['projects'] if isinstance(e, dict)]
    if args.project:
        named = args.project
        if not os.path.isabs(named) and not os.path.exists(named):
            named = os.path.join(root, named)
        entry = registered_project(root, named, layout)
        if entry is None:
            raise GateError('%s is not a registered project of gate/layout.json' % args.project)
        entries = [entry]
    loose = gate_common.unregistered(root, layout)
    for f in loose['projects']:
        print('NOT REGISTERED  %s: a .3mf of this project that gate/layout.json does not name: nothing in it is verified' % f)
    for d in loose['stl_dirs']:
        print('NOT REGISTERED  %s/: a folder with STL files that is no "stl_dir" of gate/layout.json: its files are not compared with any stamp' % d)
    if not entries:
        print('gate verify: no .3mf project is registered in gate/layout.json: NOTHING was verified')
        return EXIT_INFRA
    reg = gate_registry.load_default()
    census = gate_common.load_census(root); project = gate_common.load_project(root)
    overrides = gate_params.load_overrides(root)
    verdicts, results = [], []
    for entry in entries:
        resolved = gate_params.resolve(reg, overrides, (project.get('targets') or {}).get(entry.get('key')))
        try:
            r = verify_project(root, reg, resolved, entry, layout, census)
        except NotImplementedError:
            raise GateError('the project probe (probes/project_consistency.py) is not built yet: nothing was verified')
        results.append(r)
        verdicts.append({'part': '%s:' % r['project'], 'sig': None, 'verdict': 'fail' if r['open'] else 'pass', 'open': r['open'],
                         'asked': [], 'answered': [], 'waived': r['waived'], 'info': r['info'], 'not_run': [],
                         'waivers': r.get('waivers') or [], 'questions': [], 'attested': 0})
    n_open = sum(len(r['open']) for r in results)
    try:
        import gate_report
        report = gate_report.build(root, reg, gate_params.resolve(reg, overrides, None), gate_common.new_run_id(), 'verify', 'project', verdicts)
        gate_report.write(root, report)
        print(gate_report.summary_line(report))
    except NotImplementedError:                             # the report module is not built: the result is still said
        print('gate verify: %d project(s), %d open' % (len(results), n_open))
    for r in results:
        for f in r['open']:
            print('  %s %s %s: %s' % (f.get('verdict'), f.get('rule'), f.get('part'), f.get('text')))
    return EXIT_BLOCK if n_open else EXIT_PASS
