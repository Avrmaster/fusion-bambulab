"""gate.py slice and gate.py owner baseline: the slicer's support per object, measured on a copy, and its baseline.

    gate.py slice [PROJECT] [--changed | --all | --plates 1,4]    slice a copy of a registered .3mf with the Bambu Studio
                                                                  CLI (probes.project_slice), store the record, show each
                                                                  object against its baseline, write a proposal
    gate.py owner baseline PROJECT "OBJECT" [...] --quote "..."   the owner records or raises the baseline of objects
                                                                  from that record (never from a typed number)

PROJECT is a key of gate/layout.json "projects" or the path of its .3mf; none = every registered project. --changed
(the default) slices only the plates whose objects (name and mesh) differ from the last record. The judge is
print.slice_support_growth at `gate.py verify`; this command only measures. Slicing takes seconds to minutes per
plate: run it in the background and keep working.

Before a .3mf write (gate_guard calls before_write) the archive about to be written is held against the record: the
plates whose meshes change are named, and print.slice_support_growth is judged for every object the record holds. That
is one line, printed and logged when a support grew (a slice that is still owed is said by `gate.py verify`), and the
save goes on. Only when gate/project.json says "slice_before_write": true (the
owner's switch) are the changed plates of the archive sliced right there, on a copy, and in enforce mode a support that
grew past its baseline, or a changed plate that could not be sliced, refuses the save (SliceRefusal); that costs a
slice of each changed plate inside the save. An object with no baseline yet is not refused there: verify holds it.

The proposal gate/proposals/support_baseline.json lists every object whose measured support has no baseline yet or
differs from it, with the numbers and the command the owner would run. Nothing under gate/owner/ is written by
`gate.py slice`."""
import os

import gate_common
from gate_common import GateError, EXIT_PASS, EXIT_BLOCK, EXIT_INFRA

PROPOSAL = ('proposals', 'support_baseline.json')


def _entries(root, layout, named):
    entries = [e for e in layout.get('projects') or [] if isinstance(e, dict) and e.get('file')]
    if not named:
        return entries
    for e in entries:
        path = e['file'] if os.path.isabs(e['file']) else os.path.join(root, e['file'])
        if named == e.get('key') or os.path.realpath(named) == os.path.realpath(path) or os.path.realpath(os.path.join(root, named)) == os.path.realpath(path):
            return [e]
    raise GateError('%s is no project of gate/layout.json (a key of "projects" or the path of its .3mf)' % named)


def _plates(text):
    try:
        return sorted({int(x) for x in str(text).replace(',', ' ').split()})
    except ValueError:
        raise GateError('--plates takes plate numbers as the slicer counts them (1 = first): %r' % text)


def _rows_now(root, entry):
    """The objects of the saved project as project_consistency rows (name, plate, plate_index, printable, pose)."""
    from probes import project_bed
    path = entry['file'] if os.path.isabs(entry['file']) else os.path.join(root, entry['file'])
    return [{'name': o['name'], 'id': o['id'], 'plate': o['plate'], 'plate_index': o['plate_index'], 'printable': o['printable'],
             'pose': o['pose'], 'part': None} for o in project_bed.read_placed(path)['objects']]


def _judged(root, entry):
    """(slice facts, findings of print.slice_support_growth) for the project as it is saved now."""
    import gate_judges, gate_params, gate_registry
    from probes import project_slice
    reg = gate_registry.load_default(root)
    rule = reg['rules']['print.slice_support_growth']
    declared = (gate_common.load_project(root).get('targets') or {}).get(entry.get('key'))
    vals = gate_params.values(gate_params.resolve(reg, gate_params.load_overrides(root), declared), rule['params'])
    facts = {'project': entry.get('key'), 'file': entry.get('file'), 'exists': True,
             'slice': project_slice.facts_for(root, entry.get('key'), _rows_now(root, entry))}
    ctx = {'part': '%s:' % entry.get('key'), 'rule': rule['id'], 'tables': reg.get('tables') or {}, 'target': declared, 'census': None, 'prev': None, 'intent': None}
    return facts['slice'], gate_judges.run_judge(rule, facts, vals, ctx)


def _propose(root, key, sl):
    """Write the objects whose measured support has no baseline or another one into the proposal file."""
    path = gate_common.gate_path(root, *PROPOSAL)
    data = gate_common.read_json(path, {})
    data = data if isinstance(data, dict) else {}
    data['_about'] = ('PROPOSAL written by gate.py slice: the support the slicer gave each object, for the owner. Nothing here counts: '
                      'the baseline is gate/owner/support_baseline.json, and only `gate.py owner baseline <project> "<object>" --quote "..."` '
                      'writes it, from the record of the last slice.')
    rows = {}
    for o in sl.get('objects') or []:
        got, base = o.get('sliced'), o.get('baseline')
        if not isinstance(got, dict) or o.get('printable') is False or (not got.get('support_e') and not isinstance(base, dict)):
            continue                                     # nothing printed, or no support and no baseline: nothing to decide
        if isinstance(base, dict) and base.get('support_e') == got.get('support_e') and base.get('top_z') == got.get('top_z'):
            continue
        rows[o['name']] = {'measured': {'support_e': got.get('support_e'), 'top_z': got.get('top_z'), 'plate_index': got.get('plate_index'), 'sliced': got.get('time')},
                           'baseline': base, 'command': 'gate.py owner baseline %s "%s" --quote "..."' % (key, o['name'])}
    projects = data.get('projects') if isinstance(data.get('projects'), dict) else {}
    if rows:
        projects[key] = rows
    else:
        projects.pop(key, None)
    data['projects'] = projects
    gate_common.write_json(path, data)
    return len(rows)


def cmd_slice(root, args):
    """gate.py slice: see the top. Returns 0 when every asked plate was sliced and nothing grew, 1 when an object's
    support grew or has no baseline yet (the verify judge would hold it), 4 when the slicer is missing or a plate gave no
    G-code."""
    from probes import project_slice
    layout = gate_common.load_layout(root)
    entries = _entries(root, layout, getattr(args, 'project', None))
    if not entries:
        print('gate slice: no .3mf project is registered in gate/layout.json: NOTHING was sliced')
        return EXIT_INFRA
    worst = EXIT_PASS
    for entry in entries:
        key = entry.get('key')
        path = entry['file'] if os.path.isabs(entry['file']) else os.path.join(root, entry['file'])
        if not os.path.isfile(path):
            print('gate slice %s: %s does not exist' % (key, gate_common.rel(root, path)))
            worst = EXIT_INFRA
            continue
        if getattr(args, 'plates', None):
            plates = _plates(args.plates)
        elif getattr(args, 'all', False):
            plates = None
        else:
            plates = project_slice.changed_plates(root, entry)
            if not plates:
                print('gate slice %s: every plate holds the meshes of the last slice; nothing to slice' % key)
        if plates is None or plates:
            rec = project_slice.slice_project(root, entry, plates, timeout=float(getattr(args, 'timeout', None) or 1800), keep=bool(getattr(args, 'keep', False)))
            for index, p in sorted(rec.get('plates', {}).items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
                if plates is not None and int(index) not in plates:
                    continue
                if p.get('error'):
                    print('  plate %s (%s): %s' % (index, p.get('name'), p['error']))
                    if 'no G-code' in p['error']:
                        worst = EXIT_INFRA
                    continue
                for o in p.get('objects') or []:
                    if o.get('printed') is False:
                        continue
                    print('  plate %s %s: support %s mm (filament), up to z %s' % (index, o.get('name'), o.get('support_e'), o.get('top_z')))
        sl, findings = _judged(root, entry)
        n = _propose(root, key, sl)
        bad = [f for f in findings if f.get('verdict') in gate_common.BLOCKING]
        for f in findings:
            if f.get('verdict') != 'INFO':
                print('  %s %s' % (f['verdict'], f['text']))
        print('gate slice %s: %d object row(s) open%s' % (key, len(bad), ('; %d in gate/proposals/support_baseline.json for the owner' % n) if n else ''))
        if bad and worst == EXIT_PASS:
            worst = EXIT_BLOCK
    return worst


def owner_baseline(root, args):
    """gate.py owner baseline PROJECT OBJECT... --quote: copy the measured support of each object from the record of the
    last slice into gate/owner/support_baseline.json. Refused (nothing written) for an object the record does not hold
    sliced from the mesh the project holds now."""
    from probes import project_slice
    layout = gate_common.load_layout(root)
    entry = _entries(root, layout, args.project)[0]
    key = entry.get('key')
    sl = project_slice.facts_for(root, key, _rows_now(root, entry))
    by = {o['name']: o for o in sl.get('objects') or []}
    rows = []
    for name in args.objects:
        o = by.get(name)
        if o is None:
            raise GateError('%s holds no object "%s"; nothing was written' % (key, name))
        if not isinstance(o.get('sliced'), dict):
            raise GateError('"%s": %s. Slice it first (gate.py slice %s --changed); nothing was written' % (name, o.get('why') or 'not sliced', key))
        rows.append(o)
    path = project_slice.baseline_path(root)
    data = gate_common.read_json(path, {}) if os.path.isfile(path) else {}
    if not isinstance(data, dict) or not isinstance(data.get('projects', {}), dict):
        raise GateError('gate/owner/support_baseline.json must be an object with "projects"; nothing was written')
    data.setdefault('_about', 'OWNER file. The support the slicer gives each object, per project and object name, as the owner accepted it after '
                              'looking at the sliced plate. print.slice_support_growth fails an object whose support grows past it. Written only by '
                              '`gate.py owner baseline`, from a slice of the gate.')
    projects = data.setdefault('projects', {})
    mine = projects.setdefault(key, {})
    for o in rows:
        got = o['sliced']
        old = mine.get(o['name'])
        mine[o['name']] = {'support_e': got.get('support_e'), 'top_z': got.get('top_z'), 'pose': o.get('pose'), 'plate_index': got.get('plate_index'),
                           'sliced': got.get('time'), 'studio': sl.get('studio'), 'date': gate_common.now()[:10], 'quote': args.quote,
                           'reason': getattr(args, 'reason', None), 'was': {k: old.get(k) for k in ('support_e', 'top_z', 'date')} if isinstance(old, dict) else None}
        print('baseline of %s "%s": %s mm of support up to z %s (was %s)' % (key, o['name'], got.get('support_e'), got.get('top_z'),
                                                                          '%s mm' % old.get('support_e') if isinstance(old, dict) else 'none'))
    gate_common.write_json(path, data)
    return EXIT_PASS


class SliceRefusal(GateError):
    """enforce mode with "slice_before_write": the archive's support grew or could not be judged (`.rows`)."""

    def __init__(self, message, rows=None):
        GateError.__init__(self, message)
        self.rows = list(rows or [])


def before_write(root, path, files, mode, entry, cli=None):
    """print.slice_support_growth on the archive a save is about to write (see the top). files: {member: bytes}.
    Returns {"changed": [plate indexes whose meshes differ from the record], "open": [findings], "line": text or None,
    "sliced": bool}. Raises SliceRefusal only in enforce mode with "slice_before_write" true."""
    import gate_judges, gate_params, gate_registry
    from probes import project_slice
    meshid = gate_common.sibling('meshid')
    key = entry.get('key')
    rows = []
    for m in meshid.project_meshes(files):
        rows.append({'name': m['object'], 'id': m.get('id'), 'plate': m.get('plate'), 'plate_index': m.get('plate_index'),
                     'printable': bool(m.get('printable', True)), 'pose': meshid.geom_hash(m['vertices'], len(m['triangles'])), 'part': None})
    changed = project_slice.changed_plates(root, entry, source=files)
    project = gate_common.load_project(root)
    now = bool(project.get('slice_before_write')) and changed
    trouble = None
    if now:
        pending = gate_common.gate_path(root, 'run', 'slice', '%s.%d.pending.3mf' % (gate_common.slug(str(key)), os.getpid()))
        os.makedirs(os.path.dirname(pending), exist_ok=True)
        import zipfile
        with zipfile.ZipFile(pending, 'w', zipfile.ZIP_DEFLATED) as z:
            for name, data in files.items():
                z.writestr(name, data)
        try:
            limit = project.get('slice_timeout_s')
            limit = float(limit) if isinstance(limit, (int, float)) and not isinstance(limit, bool) and limit > 0 else project_slice.SAVE_TIMEOUT
            project_slice.slice_project(root, entry, changed, source=pending, cli=cli, timeout=limit)
        except GateError as e:                       # no slicer, a CLI that cannot start, a locked record: the changed plates are not sliced
            trouble = str(e)
        finally:
            try:
                os.remove(pending)
            except OSError:
                pass
    reg = gate_registry.load_default(root)
    rule = reg['rules']['print.slice_support_growth']
    declared = (project.get('targets') or {}).get(key)
    vals = gate_params.values(gate_params.resolve(reg, gate_params.load_overrides(root), declared), rule['params'])
    facts = {'project': key, 'file': entry.get('file'), 'exists': True, 'slice': project_slice.facts_for(root, key, rows, files)}
    ctx = {'part': '%s:' % key, 'rule': rule['id'], 'tables': reg.get('tables') or {}, 'target': declared, 'census': None, 'prev': None, 'intent': None}
    found = [f for f in gate_judges.run_judge(rule, facts, vals, ctx) if f.get('verdict') in gate_common.BLOCKING]
    out = {'changed': changed, 'open': found, 'sliced': bool(now), 'line': None, 'grew': [f['part'] for f in found if f.get('verdict') == 'FAIL']}
    if changed or found:
        out['line'] = ('gate slice [%s] %s: %s%s' % (mode, gate_common.rel(root, path),
                       ('plates %s change meshes: their supports are judged after `gate.py slice %s --changed`; ' % (', '.join(str(p) for p in changed), key)) if changed and not now else '',
                       '%d support row(s) open (print.slice_support_growth; gate.py verify lists them)' % len(found) if found else 'no support row open'))
    if now:
        rec = project_slice.load_record(root, key) or {}
        failed = ['plate %s: %s' % (p, trouble or (rec.get('plates') or {}).get(str(p), {}).get('error') or 'not sliced') for p in changed
                  if trouble or (rec.get('plates') or {}).get(str(p), {}).get('error') or str(p) not in (rec.get('plates') or {})]
        if failed:
            out['line'] = '%s; NOT SLICED: %s' % (out['line'] or 'gate slice [%s] %s' % (mode, gate_common.rel(root, path)), '; '.join(failed[:4]))
            out['error'] = True
        refuse = [f for f in found if f.get('verdict') == 'FAIL']
        if mode == 'enforce' and (refuse or failed):
            raise SliceRefusal('%s\n%s\nNothing was written: the supports of this save grew past the owner\'s baseline, or a changed plate could not be '
                               'sliced. An object with no baseline yet is not refused here: gate.py verify holds it until the owner records one.'
                               % (out['line'] or 'gate slice [enforce] %s' % gate_common.rel(root, path),
                                  '\n'.join(['  FAIL %s' % f['text'] for f in refuse[:12]] + ['  NOT SLICED %s' % t for t in failed])), refuse)
    return out


def overhangs_before_write(root, path, files, mode, entry, names):
    """print.overhang_angle on the objects `names` of the archive a save is about to write (the objects that bring new
    meshes): the geometry pre-check (probes.mesh_overhang, about a second for a project) and the support setting of each,
    held against the slice record as verify holds them. Returns {"open": [FAIL findings], "line": text or None}; only a
    FAIL is loud (supports off under what hangs, or a slice of this very mesh that leaves a region bare): a slice still
    owed is NOT_RUN, which `gate.py verify` lists. Raises SliceRefusal in enforce mode when a FAIL is found."""
    import gate_judges, gate_params, gate_registry
    from probes import project_consistency, project_slice
    meshid = gate_common.sibling('meshid')
    key = entry.get('key')
    want = set(names or [])
    rows = [{'name': m['object'], 'id': m.get('id'), 'plate': m.get('plate'), 'plate_index': m.get('plate_index'),
             'printable': bool(m.get('printable', True)), 'pose': meshid.geom_hash(m['vertices'], len(m['triangles'])), 'part': None}
            for m in meshid.project_meshes(files) if m['object'] in want]
    if not rows:
        return {'open': [], 'line': None}
    reg = gate_registry.load_default(root)
    rule = reg['rules']['print.overhang_angle']
    project = gate_common.load_project(root)
    declared = (project.get('targets') or {}).get(key)
    vals = gate_params.values(gate_params.resolve(reg, gate_params.load_overrides(root), declared), rule['params'])
    facts = {'project': key, 'file': entry.get('file'), 'exists': True,
             'overhangs': project_consistency.overhang_facts(root, files, rows, lambda: reg, declared),
             'slice': project_slice.facts_for(root, key, rows, files)}
    ctx = {'part': '%s:' % key, 'rule': rule['id'], 'tables': reg.get('tables') or {}, 'target': declared, 'census': None, 'prev': None, 'intent': None}
    bad = [f for f in gate_judges.run_judge(rule, facts, vals, ctx) if f.get('verdict') == 'FAIL']
    out = {'open': bad, 'line': None}
    if bad:
        out['line'] = 'gate overhang [%s] %s: %d object row(s) hang over air (print.overhang_angle):\n%s' % (
            mode, gate_common.rel(root, path), len(bad), '\n'.join('  FAIL %s' % f['text'] for f in bad[:8]))
        if mode == 'enforce':
            raise SliceRefusal('%s\nNothing was written: turn the part onto a face where nothing hangs, give it supports, or fix the overhang.' % out['line'], bad)
    return out
