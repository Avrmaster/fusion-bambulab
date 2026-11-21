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

A mesh is CARRIED when a `write` event of the ledger for this project file backs it: an event with its pose hash
(meshid.geom_hash of the mesh as placed), with the geometry of its source STL (model form: the same STL placed again
on another face or at another angle is still the same part), or with its vertex set under its own object name (the
same vertices with merged or repaired triangles: weld_3mf.py, the slicer's repair), AND that event names a stamp that
still verifies for the declared target and whose geometry is the event's own model (an event without a model, or
whose model is not the stamp's, backs nothing). The mode the event was written in does not matter: a journal line
proves nothing by itself, and a stamp that went stale (a rule tightened, a parameter or the target changed) carries
nothing in either mode. An event of a mesh the guard found in the file on disk ("tool": "outside", or "carried" from
before 0.3.0) or let through unchecked ("unchecked": true) never backs anything. A carried mesh is not judged again;
its stamp goes into the ledger with the write.

A mesh that is in the file on disk (its pose, or the same vertices with merged or repaired triangles) and that no such
event backs:
  observe  let through: the owner's hand work in the slicer never blocks a tool save. It is UNCHECKED: every save says
           "N object(s) in <project> have no stamp: <names>", and only when gate/project.json says
           "mark_unchecked": true the name of every plate that holds one is prefixed with "[unchecked] " in the archive
           (bambu3mf applies it; the prefix goes again once every mesh of the plate is backed).
  enforce  judged like a new mesh, and refused: nothing is let through because it was there before. A project switches
           to enforce once every part it prints is exported through the gate and swapped in.
Every other mesh is NEW and is judged.

In enforce mode the archive about to be written is also held against the declared target of a registered project
(the judge of project.settings_match: hole compensation, layer height, nozzle, the "print" block, per object too): a
save whose file would print with another value than a declared one is refused, whatever the file on disk held. What
the target does not declare (or the archive does not hold) cannot be compared: one loud line at every save, and
`gate.py verify` keeps it NOT_RUN.

A .3mf under a gated root that is NOT a registered project of gate/layout.json is never written in silence: the guard
prints and logs one line that says so. In observe mode that is all (the file is not judged). In enforce mode it is
judged like a registered project with no declared target: a new mesh without a valid stamp is refused there too. So
"one of four projects registered" does not leave three open, and a trial copy of a project behaves as the project does.

The binding journal is the ledger's bambulab/ledger/events.jsonl. Before it judges, the guard writes one `write` event
with "tool": "outside" (stamp null) for every mesh of the file on disk that the journal does not know yet, in both
modes: what was found there is on record, and is never carried by it. A build script that first saves the empty
template over the project and then saves the parts finds its stamped meshes (and those written in enforce mode) in the
journal at the second save; an unstamped mesh is judged there like any new one.

A .3mf OUTSIDE the gated tree (a scratch folder, /tmp, a download folder) is judged too when bambu3mf finds a gated root
for it: from the source STL of one of its meshes, from the working folder, or from CLAUDE_PROJECT_DIR (bambu3mf._gate_root).
A copy of a project's STL in a scratch folder still finds its project: by the bytes of the file, against the STL
folders of the gated roots on the hooks' list ("content"). It is an unregistered project of that root: one loud line in observe mode; in enforce mode every mesh of it needs a
valid stamp (nothing of such a file is carried, nothing goes into the journal or gate/fit_settings.json).

After every save bambu3mf calls saved(): gate/run/saves.json keeps, per project file, the time, the mode, the size and
modification time the file had right after the save, and its unchecked objects. The session hooks read it (gate_watch.py):
a file whose size or time differs was changed outside the tools since.

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
A note covers a change only when the new value equals the declared target of the project (gate/project.json "targets",
the object's own value under "objects" first): a noted change away from the target, or of a key the target does not
declare, or in a project with no declared target, is judged as not asked for. So a script cannot name its way past the
target; the owner changes the target first.
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
import os, re, time

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


NO_CARRY_TOOLS = ('carried', 'outside')      # events of meshes found in the file on disk: on record, never a backing
UNCHECKED_MARK = '[unchecked] '               # the prefix of a plate that holds an unchecked mesh


def _journal(root, project_rel, path, vsets=None):
    """({pose hash: [events]}, {model hash: [events]}) of the `write` events of this project file in the ledger's
    journal, oldest first; `vsets` (a dict), when given, gets {(object, vertex-set hash): [events]}. Every event is
    listed, also those that cannot carry anything (_carries decides)."""
    poses, models, same = {}, {}, {}
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
            if isinstance(e.get('pose'), str) and e['pose']:
                poses.setdefault(e['pose'], []).append(e)
            if isinstance(e.get('model'), str) and e['model']:
                models.setdefault(e['model'], []).append(e)
            if vsets is not None and isinstance(e.get('vset'), str) and e['vset']:
                vsets.setdefault((e.get('object'), e['vset']), []).append(e)
    return poses, models


def _stamp_holds(root, name, model, ctx):
    """Does the stamp "<part id>@<sig12>" exist, belong to the geometry `model` and still verify (gate_stamp.verify:
    'valid') for the target of this save? No model: it does not hold. Never raises: what cannot be verified does not hold."""
    if not isinstance(model, str) or not model:
        return False
    try:
        import gate_stamp
        if 'stamps' not in ctx:
            ctx['stamps'] = gate_stamp.all_stamps(root)
        if 'reg' not in ctx:
            import gate_registry
            ctx['reg'] = gate_registry.load_default(root)
        if 'census' not in ctx:
            ctx['census'] = gate_common.load_census(root)
        for s in ctx['stamps']:
            if gate_stamp.name_of(s) != name or s.get('geom_hash') != model:
                continue
            state, _ = gate_stamp.verify(root, ctx['reg'], s, geom_hash=s.get('geom_hash'), census=ctx['census'], target=ctx.get('target'))
            if state == 'valid':
                return True
    except Exception:
        return False
    return False


def _carries(root, e, ctx):
    """Does this `write` event back a mesh (see the top)? Only when it names a stamp of its own model that still
    verifies, whatever mode it was written in; never for an event of a mesh found on disk ("tool" in NO_CARRY_TOOLS)
    or let through unchecked."""
    if not isinstance(e, dict) or e.get('tool') in NO_CARRY_TOOLS or e.get('unchecked'):
        return False
    name = e.get('stamp')
    if not isinstance(name, str) or not name:
        return False
    key = (name, e.get('model'))
    held = ctx.setdefault('held', {})
    if key not in held:
        held[key] = _stamp_holds(root, name, e.get('model'), ctx)
    return held[key]


def _backing(root, events, ctx):
    """The first event of `events` that backs a mesh: (True, its stamp or None), else (False, None)."""
    for e in events:
        if _carries(root, e, ctx):
            return True, e.get('stamp') if isinstance(e.get('stamp'), str) else None
    return False, None


def known_poses(root, project_rel, path, target=None):
    """Pose hashes that count as carried for this project file: those of the `write` events of
    bambulab/ledger/events.jsonl for this project that back a mesh (_carries: a stamp that still verifies for
    `target`), plus those of the meshes in the file on disk (when it exists and can be read: in observe mode they pass
    a save, unchecked unless such an event backs them too; in enforce mode they are judged like new ones). Returns a set."""
    poses, _ = _journal(root, project_rel, path)
    ctx = {'target': target}
    return {m['pose'] for m in _disk_meshes(path)} | {p for p, events in poses.items() if _backing(root, events, ctx)[0]}


def _bind_carried(root, project_rel, disk, journal_poses, mode=None):
    """Write the meshes of the file on disk that the journal does not know yet into it: one `write` event each, in the
    format of the ledger, with "tool": "outside" and stamp null: on record, never a backing. Returns how many lines
    were written. Never raises."""
    n, seen = 0, set(journal_poses)
    try:
        for m in disk:
            if m['pose'] in seen:
                continue
            seen.add(m['pose'])
            gate_common.append_jsonl(gate_common.events_path(root), {
                'v': 1, 't': gate_common.now(), 'by': 'tool', 'kind': 'write', 'project': project_rel, 'object': m['object'],
                'plate': m['plate'], 'plate_index': m['plate_index'], 'pose': m['pose'], 'model': None, 'stl': None,
                'stl_sha256': None, 'stamp': None, 'cad': None, 'tool': 'outside', 'mode': mode})
            n += 1
    except Exception as e:
        log(root, 'could not write the meshes found in %s into the journal: %s: %s' % (project_rel, type(e).__name__, e))
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
        ctx['reg'] = gate_registry.load_default(root)
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


def _declared_target(root, project_key):
    """The declared target of a project (gate/project.json "targets"), or {} when it has none: a note then covers no
    change (project_consistency.settings_diff with `target`)."""
    try:
        t = (gate_common.load_project(root).get('targets') or {}).get(project_key) if project_key else None
    except Exception:
        t = None
    return t if isinstance(t, dict) else {}


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
    NOT_RUN finding. A note covers a change only when the new value is the declared target's (_declared_target)."""
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
        facts['settings_write'] = project_consistency.settings_diff(before, after, asked, target=_declared_target(root, project_key))
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
    if mode == 'enforce' and key and files is not None:     # enforce: the file about to be written prints with the declared values
        found = match_target(root, files, key, prel)
        off = [f for f in found if f.get('verdict') in ('FAIL', 'BLOCKED')]
        unsaid = [f for f in found if f.get('verdict') not in ('FAIL', 'BLOCKED')]
        if unsaid:                                          # what the target does not say, or the reader could not read: loud, never a pass
            _say(root, 'gate guard [enforce] %s: NOT compared with the declared target: %s. `gate.py verify` holds it as NOT_RUN until the owner '
                       'declares it' % (prel, '; '.join(f.get('text') or '' for f in unsaid[:4])))
        if off:
            line = 'gate guard [enforce] %s: the file would not print with its declared target: %s' % (prel, '; '.join(f.get('text') or '' for f in off[:6]))
            log(root, '%s REFUSED' % line)
            _remember(root, prel, got.get('disk'))
            raise GuardRefusal('%s\nNothing was written. Set the values of the declared target (bambu3mf.set_project / set_settings), or the owner '
                               'changes the target (gate.py owner bundle with a "target" entry).' % line, off)
    return got


def match_target(root, files, key, prel):
    """The FAIL / BLOCKED / NOT_RUN findings of project.settings_match for the archive `files` against the declared
    target of the project `key` (no declared target: one NOT_RUN). Raises nothing the caller must catch: a reader error
    comes back as a NOT_RUN finding. The save refuses FAIL and BLOCKED (the file contradicts a declared value); a
    NOT_RUN (the target does not say a key, the archive does not hold it) is said in one loud line."""
    from probes import project_consistency
    import gate_judges
    if not any(isinstance(v, bytes) and b'<object' in v for k, v in files.items() if str(k).startswith('3D/')):
        return []                                        # an empty project (a template laid first): nothing prints from it
    target = _declared_target(root, key)
    keys = fit_setting_keys(root)
    rule = 'project.settings_match'
    ctx = {'part': '%s:' % key, 'rule': rule, 'tables': {}, 'target': None, 'census': None, 'prev': None, 'intent': None}
    try:
        facts = {'project': key, 'file': prel, 'exists': True, 'target_declared': target or None,
                 'fit_settings': project_consistency.read_fit_settings(files, keys),
                 'print_settings': project_consistency.read_fit_settings(files, project_consistency.print_keys(target or None))}
        found = gate_judges.JUDGES['project_settings_match'](facts, {'target.fit_settings': ' '.join(keys)}, ctx)
    except Exception as e:
        return [{'rule': rule, 'verdict': 'NOT_RUN', 'text': 'the settings of the archive could not be compared with the declared target: %s: %s' % (type(e).__name__, e)}]
    return [dict(f, rule=rule) for f in found if f.get('verdict') in BLOCKING]


def _names(names, limit=6):
    names = list(names)
    return ', '.join(names[:limit]) + (', ... (%d more)' % (len(names) - limit) if len(names) > limit else '')


def _outside_tree(root, path):
    """Does `path` lie outside the gated tree `root` (a scratch folder, /tmp, a download folder)?"""
    return gate_common.rel(root, path).startswith('..')


VIA = {'stl': 'the source STL of one of its meshes lies in it', 'cwd': 'the working folder lies in it',
       'env': 'CLAUDE_PROJECT_DIR names it', 'content': 'a source STL of one of its meshes is a copy of an STL of it', 'tree': None, None: None}


def _check(root, path, meshes, files=None, asked=None, via=None):
    project = gate_common.load_project(root); mode = project['mode']
    layout = gate_common.load_layout(root)
    entry = registered_project(root, path, layout)
    outside = _outside_tree(root, path)
    res = {'gated': True, 'mode': mode, 'project': entry.get('key') if entry else None, 'meshes': len(meshes) if meshes is not None else 0,
           'new': [], 'unstamped': [], 'stale': [], 'rows': [], 'stamps': {}, 'line': '', 'unchecked': [], 'mark': None,
           'outside_tree': outside, 'root': root}
    prel = path if outside else gate_common.rel(root, path)
    got = _guard_settings(root, path, files, asked, mode, entry.get('key') if entry else None, prel)
    res['settings'] = {k: v for k, v in got.items() if k not in ('disk', 'after')}
    remember = (lambda facts: None) if outside else (lambda facts: _remember(root, prel, facts))
    try:
        res = _check_meshes(root, path, meshes, project, layout, entry, res, prel, via)
        # an unregistered file or one outside the tree is judged like a registered project in enforce mode (no declared
        # target, no slice record of its own): the overhang pre-check runs on it all the same
        used = entry if entry is not None else ({} if mode == 'enforce' else None)
        turned = _turned(root, path, files, res, outside)
        _slice_line(root, path, files, mode, used, res, turned)    # before _remember: a refused save leaves the memory of the file on disk
        _overhang_line(root, path, files, mode, used, res, turned)
    except GuardRefusal:                                    # refused: the file stays as it is on disk
        remember(got.get('disk'))
        _restore_slice(root, res)
        raise
    except Exception:                                       # the judging broke: enforce refuses, observe writes (check())
        remember(got.get('disk') if mode == 'enforce' else (got.get('after') or got.get('disk')))
        if mode == 'enforce':
            _restore_slice(root, res)
        raise
    remember(got.get('after') or got.get('disk'))           # the save goes on: this is what the file holds next
    return res


def _turned(root, path, files, res, outside):
    """Names of the objects of the archive that bring no new mesh and still lie otherwise than in the file on disk: a
    stamped part placed in a new pose, turned in its build item, or under other per-object or support settings (the slice
    key of probes.project_slice differs). The overhang pre-check and the slice look at them like at a new mesh."""
    if not isinstance(files, dict):
        return []
    try:
        from probes import project_slice
        now = project_slice.slice_keys(files)
        before = project_slice.slice_keys(path) if not outside and os.path.isfile(path) else {}
    except Exception:
        return []
    new = set(res.get('new') or [])
    return sorted({name for (index, name), (_pose, k) in now.items() if name not in new and (before.get((index, name)) or (None, None))[1] != k})


def _restore_slice(root, res):
    """A save refused after its changed plates were sliced into the record: those plates get back what the record held
    for the file on disk (gate_slice.restore)."""
    back = res.get('slice_restore') if isinstance(res, dict) else None
    if back:
        try:
            import gate_slice
            gate_slice.restore(root, back[0], back[1])
        except Exception as e:
            log(root, 'the slice record could not be put back after the refusal: %s: %s' % (type(e).__name__, e))


def _slice_line(root, path, files, mode, entry, res, turned=()):
    """print.slice_support_growth on the archive of a save whose plates change (gate_slice.before_write): one line,
    logged and printed; a refusal only in enforce mode with the owner's "slice_before_write". A plate changes with a new
    mesh, a turn of an object or a support setting (project_slice.changed_plates); a save that changes no plate says
    nothing here. Any other trouble is logged and the save goes on. entry {}: a file judged like a registered project
    (no record of its own: only the pre-check of the overhangs runs)."""
    if entry is None or not isinstance(files, dict) or not entry.get('key'):
        return
    if not res.get('new') and not turned:
        try:
            from probes import project_slice
            if not project_slice.changed_plates(root, entry, source=files):
                return
        except Exception:
            pass
    try:
        import gate_slice
        got = gate_slice.before_write(root, path, files, mode, entry)
    except GateError as e:
        if type(e).__name__ == 'SliceRefusal':
            log(root, '%s REFUSED (supports)' % mode)
            raise GuardRefusal(str(e), getattr(e, 'rows', []))
        got = {'line': 'gate slice: the supports of this save were not judged (%s)' % e, 'error': True}
    except Exception as e:
        got = {'line': 'gate slice: the supports of this save were not judged (%s: %s)' % (type(e).__name__, e), 'error': True}
    if got.get('restore') is not None:
        res['slice_restore'] = (entry.get('key'), got['restore'])
    res['slice'] = {k: v for k, v in got.items() if k not in ('open', 'restore')}
    if got.get('line') and (got.get('grew') or got.get('error')):         # loud when a support grew; a slice still owed is said by verify
        log(root, got['line'])
        print(got['line'])


def _overhang_line(root, path, files, mode, entry, res, turned=()):
    """print.overhang_angle on the objects of a save that bring new meshes or lie otherwise than on disk (_turned)
    (gate_slice.overhangs_before_write): loud for every object that hangs over air, a refusal in enforce mode. Other
    trouble is logged and the save goes on. entry {}: a file judged like a registered project."""
    names = list(res.get('new') or []) + [n for n in turned or () if n not in (res.get('new') or [])]
    if entry is None or not isinstance(files, dict) or not names:
        return
    try:
        import gate_slice
        got = gate_slice.overhangs_before_write(root, path, files, mode, entry, names)
    except GateError as e:
        if type(e).__name__ == 'SliceRefusal':
            log(root, '%s REFUSED (overhangs)' % mode)
            raise GuardRefusal(str(e), getattr(e, 'rows', []))
        got = {'line': 'gate overhang: the overhangs of this save were not judged (%s)' % e}
    except Exception as e:
        got = {'line': 'gate overhang: the overhangs of this save were not judged (%s: %s)' % (type(e).__name__, e)}
    res['overhang'] = {'line': got.get('line'), 'fail': len(got.get('open') or [])}
    if got.get('line'):
        log(root, got['line'])
        print(got['line'])


def _check_meshes(root, path, meshes, project, layout, entry, res, prel, via=None):
    """The mesh part of one save (see the top): fills and returns `res`, raises GuardRefusal in enforce mode."""
    mode = project['mode']
    outside = res.get('outside_tree')
    note = ''
    if entry is None:
        if outside:
            note = ('gate guard [%s] %s lies outside the gated project %s (%s) and is not a registered project of it: '
                    'a print from it is judged like one of that project' % (mode, prel, root, VIA.get(via) or 'it was handed over'))
        else:
            note = ('gate guard [%s] %s is not a registered project of gate/layout.json ("projects"): register it, with its declared '
                    'target in gate/project.json' % (mode, prel))
        if mode != 'enforce':
            res['line'] = note + ' - NOT guarded, written as it is (observe mode; enforce judges such a file like a registered one)'
            log(root, res['line'])
            print(res['line'])
            return res
        entry = {}                                          # enforce: judged like a registered project, no declared target
        log(root, note)
        print(note + ' - judged like a registered project, no declared target' + (', nothing of it carried' if outside else ''))
    if meshes is None:
        raise TypeError('the meshes of %s were not handed over (they could not be read from the archive)' % prel)
    meshid = gate_common.sibling('meshid')
    disk = [] if outside else _disk_meshes(path)            # a file outside the tree carries nothing of its own
    vsets = {}
    poses, models = ({}, {}) if outside else _journal(root, prel, path, vsets)
    bound = 0 if outside else _bind_carried(root, prel, disk, set(poses), mode)
    on_disk = {m['pose'] for m in disk}
    welded = {(m['object'], m['vset']) for m in disk}
    ctx = {'target': (project.get('targets') or {}).get(entry.get('key'))}
    plates = set()
    for m in meshes:
        name = m.get('object')
        pose, vset = meshid.both_hashes(m['vertices'], len(m['triangles']))
        stl, model = m.get('stl'), m.get('model')
        try:
            if stl and not model and os.path.isfile(stl):
                model = meshid.stl_ids(stl)['geom_hash']
        except Exception:
            model = None
        backed, stamp = _backing(root, poses.get(pose, []) + (models.get(model, []) if model else []) + vsets.get((name, vset), []), ctx)
        if backed:                                          # a stamp of the event's own model that still verifies
            if stamp:
                res['stamps'][name] = stamp
            continue
        from_disk = pose in on_disk or (name, vset) in welded   # the second: the same vertices with other triangles (a weld, a repair)
        if from_disk and mode != 'enforce':
            res['unchecked'].append(name)                   # the owner's hand work, or older than the gate: passes in observe, said every time
            plates.add(m.get('plate_index'))
            continue
        try:
            state, why, part, stamp = _judge_mesh(root, m, model, ctx)
        except GuardRefusal:
            raise
        except Exception as e:                              # what could not be judged has not passed
            state, why, part, stamp = 'none', 'could not be judged: %s: %s' % (type(e).__name__, e), None, None
        if from_disk and state != 'valid':
            why = ('it lies in the file on disk and nothing on record backs it (no stamp of its geometry that still verifies): enforce lets nothing '
                   'through because it was there before. Export the part through the gate (gate.py export PART) and swap it in; %s' % why)
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
        line += ', %d found in the file on disk and put on record' % bound
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
        for r in bad:                                       # written unchecked: its plate is marked like that of a mesh from disk
            plates.update(m.get('plate_index') for m in meshes if m.get('object') == r['object'])
    unchecked = list(dict.fromkeys(res['unchecked'] + [r['object'] for r in bad]))
    res['unchecked'] = unchecked
    if unchecked:
        _say(root, unchecked_line(prel, unchecked))
    if mode == 'enforce' or project.get('mark_unchecked') is True:
        res['mark'] = sorted(p for p in plates if isinstance(p, int))
    return res


def unchecked_line(prel, names):
    """The one line said at every save, and by the session hooks, while objects of a project have no stamp."""
    return ('gate: %d object(s) in %s have no stamp (in the file before this save, or written unchecked): %s. Export them through the gate '
            '(gate.py export PART), then build or swap them in again' % (len(names), prel, _names(names, 12)))


def saves_path(root):
    """gate/run/saves.json: what the guard knows of each project file after its last guarded save (git-ignored)."""
    return gate_common.gate_path(root, 'run', 'saves.json')


def saved(root, path, result=None):
    """Called by bambu3mf.save() right after the file was replaced: keep its size, modification time, the mode and its
    unchecked objects in gate/run/saves.json, so that the session hooks can say when the file changed outside the tools
    and which objects have no stamp. A file outside the gated tree is not kept. Never raises."""
    try:
        root, path = os.path.realpath(root), os.path.realpath(path)
        if _outside_tree(root, path):
            return
        st = os.stat(path)
        p = saves_path(root)
        try:
            data = gate_common.read_json(p, {})
        except GateError:
            data = {}
        if not isinstance(data, dict) or not isinstance(data.get('projects'), dict):
            data = {'_about': 'Written by the gate guard after every guarded save: size and modification time of the file right after it, the mode, '
                              'and the objects without a stamp. A file that differs was changed outside the tools since.', 'projects': {}}
        r = result if isinstance(result, dict) else {}
        data['projects'][gate_common.rel(root, path)] = {'time': gate_common.now(), 'ts': time.time(), 'mode': r.get('mode'), 'size': st.st_size,
                                                          'mtime_ns': st.st_mtime_ns, 'unchecked': list(r.get('unchecked') or [])[:400]}
        gate_common.write_json(p, data)
    except Exception as e:
        log(root, 'could not keep the state of %s after the save: %s: %s' % (path, type(e).__name__, e))


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


def check(path, meshes, root=None, files=None, asked=None, via=None):
    """Judge the meshes and the fit settings of a save. Called by bambu3mf.save() before the file is replaced.

    path    the .3mf about to be written        meshes  see the top
    root    the project root; None = found from the path. No gate/project.json above the path: returns {"gated": False}.
    files   the archive about to be written, {member name: bytes}: its fit settings are compared with the file on disk
    asked   the settings the caller means to change: [{"key", "value" (None = any), "scope": "global" | an object name |
            "*", "tool"}] (bambu3mf.note_setting). A changed fit setting no note covers, or a noted one whose new value
            is not the declared target's, is refused in enforce mode and is one loud line in observe mode. The result
            holds the comparison under "settings" (check_settings).
    via     how the caller found `root` for a file outside it: "stl" | "cwd" | "env" (bambu3mf._gate_root); said in the line
    The registry is loaded here (gate_registry.load_default(root)) only when a new mesh has to be judged.

    Returns {"gated": bool, "mode": "observe" | "enforce", "project": key or None (None = not a registered project: not
    guarded), "meshes": n, "new": [object names], "unstamped": [object names], "stale": [object names],
    "rows": [{"object", "state": "valid" | "stale" | "none", "why", "part", "stl"}] for the new meshes ("project" None
    with rows: an unregistered file judged in enforce mode),
    "stamps": {object name: "<part id>@<sig12>" or None} for every mesh that has a valid stamp (carried ones too),
    "line": summary text, "unchecked": [object names let through without a stamp: from the file on disk, or written
    in observe mode], "mark": [plate indices whose name gets the "[unchecked] " prefix] or None (leave the names alone),
    "outside_tree": bool, "root": the gated root}.
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
        return _check(root, path, meshes, files, asked, via)
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
    reg = gate_registry.load_default(root)
    census = gate_common.load_census(root); project = gate_common.load_project(root)
    overrides = gate_params.load_overrides(root)
    # no Fusion here: a stamp is held against the model as the last scan saw it (its part, and what its units read)
    print('stamps are verified against the scan at %s: what changed in Fusion after it counts once `gate.py scan` (or run, or export) has seen it'
          % (census.get('scanned') or 'never'))
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
        owed = 'project %s was never sliced by the gate' % r['project']
        rows = r['open']
        if any(owed in (f.get('text') or '') for f in rows):     # one cause, one row: the NOT_RUNs of every object it leaves unmeasured fold into it
            folded = [f for f in rows if f.get('verdict') == 'NOT_RUN' and f.get('rule') == 'print.overhang_angle' and 'never sliced' in (f.get('text') or '')]
            rows = [f for f in rows if f not in folded]
            if folded:
                print('  NOT_RUN print.overhang_angle %s: %d object row(s) wait for the first slice (gate.py slice %s)' % (r['project'], len(folded), r['project']))
        cap = int(getattr(args, 'all_rows', 0) or 0) or 40
        for f in rows[:cap]:
            print('  %s %s %s: %s' % (f.get('verdict'), f.get('rule'), f.get('part'), f.get('text')))
        if len(rows) > cap:
            print('  ... and %d more rows of %s (the report gate/reports/latest.json holds every one)' % (len(rows) - cap, r['project']))
    return EXIT_BLOCK if n_open else EXIT_PASS
