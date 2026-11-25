#!/usr/bin/env python3
"""Fusion -> STL -> Bambu through the gates: a part goes out only when it passes every gate the project requires. Fast
by design: one Fusion call per gate and one per document (all parts meshed in it), one load and one save per .3mf.

The gates run first, on the parts being exported (tools/gate/gate.py: heatset, overhang, thinwall); "slicer" runs after
the STLs are written: Bambu Studio slices only the plates of those parts with the new meshes (about 15-20 s a plate, so
minutes for every part) and fails a part it still supports. A part that fails one of them is not exported: its STL and its .3mf mesh stay as they were, its findings are printed, and the exit code is 1.
There is no switch to skip a gate: a finding is fixed in the model, or written down as a named exception with its reason
where that gate takes them (gate/settings.json). Which gates are required is the project's choice, while its model is
brought to them one gate at a time:

    gate/settings.json  {"export": {"require": ["heatset"]}}        default (no "export" key): every gate

    python3 <plugin>/tools/export.py [--project DIR] [PART ...]      export these parts (default: all), then swap the
                                                                      changed meshes into their .3mf projects
    python3 <plugin>/tools/export.py --stl-only [PART ...]           STLs only
    python3 <plugin>/tools/export.py --3mf-only [PART ...]           swap the STLs that are on disk into the .3mf (no Fusion)
    python3 <plugin>/tools/export.py --list                          the parts and where they go

DIR (default: the current folder or the nearest one above it with fusion/parts.json) holds fusion/parts.json:

    {"doc": "Robot Arm V2",                          the Fusion document (it must be open; it need not be the active one)
     "stl_dir": "bambulab/robotarm-v2/stl",          where the STLs go, relative to DIR
     "parts": {
       "j1_base": {"component": "J1 base (fixed)",   a component with one occurrence ("path": [occurrence names] otherwise)
                   "body": "J1 base (fixed)",        the body (optional when the occurrence has one body)
                   "doc": "...",                     optional: another open document
                   "3mf": [{"file": "bambulab/robotarm-v2/robotarm-v2.3mf", "object": "J1 base", "face": "-z"}]}}}

"face" is the model face that lies on the bed in that project (bambu3mf.ORIENT: -z, +z, -y, +y, -x, +x): the meshes in a
.3mf carry it in their vertices, so the STL is turned the same way before the swap. Position, rotation, plate, per-object
settings and materials of the object are kept (bambu3mf.update_meshes); an unchanged mesh is not written again.
An STL is replaced only when its triangles changed (the 80-byte header, which names the Fusion version, does not count).
Bambu Studio must not hold a project while it is written: when it runs, the .3mf step is run under with_bambu_closed.py,
which asks Bambu to quit and reopens the project afterwards (with unsaved changes in Bambu it stops and writes nothing)."""
import argparse, json, os, subprocess, sys, tempfile, shutil, struct, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'fusion')); sys.path.insert(0, os.path.join(HERE, 'bambu'))
import fusion_rpc  # noqa: E402

BUDGET_S = 50
GATES = ('heatset', 'overhang', 'thinwall', 'slicer')
GEOMETRY = ('heatset', 'overhang', 'thinwall')                       # judged on the Fusion model, before the STLs
GATE_ARGS = {'heatset': ['check'], 'overhang': ['overhang'], 'thinwall': ['check', '--gate', 'thinwall']}


def required_gates(root):
    sp = os.path.join(root, 'gate', 'settings.json')
    settings = json.load(open(sp, encoding='utf-8')) if os.path.exists(sp) else {}
    req = (settings.get('export') or {}).get('require')
    req = list(GATES) if req is None else list(req)
    bad = [g for g in req if g not in GATES]
    if bad:
        sys.exit('gate/settings.json export.require: unknown gate(s) %s (known: %s)' % (', '.join(bad), ', '.join(GATES)))
    if 'slicer' in req and 'overhang' not in req:
        sys.exit('gate/settings.json export.require: "slicer" checks the overhang result against Bambu, so it needs "overhang"')
    return req, settings


def run_gates(root, cfg, names, all_parts):
    """Run the required gates on `names`. Returns (passed names, {failed name: [gates]}, gates). Exits when a gate can
    not run (Fusion does not answer, a part is not found): then nothing is exported."""
    gates, settings = required_gates(root)
    gates = [g for g in gates if g in GEOMETRY]
    failed = {}
    if not gates:
        print('gates: none required (gate/settings.json export.require = [])')
        return list(names), failed, gates
    start = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() - 1))
    gate_py = os.path.join(HERE, 'gate', 'gate.py')
    for g in gates:
        t0 = time.time()
        cmd = [sys.executable, gate_py] + GATE_ARGS[g] + ['--project', root] + ([] if all_parts else list(names))
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode not in (0, 1):
            sys.stdout.write(p.stdout); sys.stderr.write(p.stderr)
            sys.exit('gate %s could not run (exit %d): nothing exported' % (g, p.returncode))
        skip = set((settings.get(g) or {}).get('skip') or []) | (set(settings.get('skip') or []) if g == 'heatset' else set())
        bad = []
        for n in names:
            rp = os.path.join(root, 'gate', 'results', g, n + '.json')
            rec = json.load(open(rp, encoding='utf-8')) if os.path.exists(rp) else None
            if rec is None or rec.get('checked_at', '') < start:
                if n in skip:
                    continue
                bad.append(n)                                    # no fresh result: not checked, not exported
            elif rec.get('verdict') != 'pass':
                bad.append(n)
        for n in bad:
            failed.setdefault(n, []).append(g)
        print('gate %-8s %d of %d part(s) pass  (%.1f s)' % (g, len(names) - len(bad), len(names), time.time() - t0))
        if bad:                                                  # the gate's own report: what was measured, expected, how to fix
            for l in p.stdout.splitlines():
                if l.strip() and not l.startswith('NOT CHECKED'):
                    print('  | ' + l)
    passed = [n for n in names if n not in failed]
    return passed, failed, gates


def run_slicer(root, cfg, names):
    """The "slicer" gate, after the STLs are written: Bambu Studio slices a copy of each project with the new meshes, only
    the plates of `names` (gate.py overhang-slice, about 15-20 s a plate), and a part fails when the slicer still puts a
    support or a bridge the gate does not allow on it (slicer_support / slicer_bridge findings, or no fresh slice).
    Parts in no .3mf have nothing to slice. Returns {failed name: report lines}."""
    todo = [n for n in names if cfg['parts'][n].get('3mf')]
    if not todo:
        return {}
    _, settings = required_gates(root)
    skip = set((settings.get('overhang') or {}).get('skip') or [])
    start = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() - 1))
    t0 = time.time()
    cmd = [sys.executable, os.path.join(HERE, 'gate', 'gate.py'), 'overhang-slice', '--project', root] + todo
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode not in (0, 1):
        sys.stdout.write(p.stdout); sys.stderr.write(p.stderr)
        return {n: ['the slicer could not run (exit %d)' % p.returncode] for n in todo}
    failed = {}
    for n in todo:
        if n in skip:
            continue
        rp = os.path.join(root, 'gate', 'results', 'overhang', n + '.json')
        rec = json.load(open(rp, encoding='utf-8')) if os.path.exists(rp) else {}
        sl = rec.get('slicer') or {}
        if sl.get('error') or sl.get('sliced_at', '') < start:
            failed[n] = ['not sliced: %s' % (sl.get('error') or 'no fresh slice')]
            continue
        bad = [f for f in rec.get('findings') or [] if f.get('code') in ('slicer_support', 'slicer_bridge')]
        if bad:
            failed[n] = ['%s %s' % (f['code'], f.get('message') or f.get('redesign') or '') for f in bad]
    print('gate %-8s %d of %d part(s) pass  (%.1f s)' % ('slicer', len(todo) - len(failed), len(todo), time.time() - t0))
    for n, lines in sorted(failed.items()):
        for l in lines:
            print('  | %s: %s' % (n, l))
    return failed


def find_project(start):
    d = os.path.abspath(start)
    while True:
        if os.path.isfile(os.path.join(d, 'fusion', 'parts.json')):
            return d
        up = os.path.dirname(d)
        if up == d:
            sys.exit('no fusion/parts.json in %s or above it: pass --project' % start)
        d = up


def load_parts(root):
    with open(os.path.join(root, 'fusion', 'parts.json'), encoding='utf-8') as f:
        cfg = json.load(f)
    if 'parts' not in cfg:
        sys.exit('fusion/parts.json has no "parts": see the docstring of tools/export.py for the format')
    return cfg


def body_bytes(path):
    """The vertices of a binary STL's triangles, sorted (the header and the facet normals do not count: writers differ
    in how they compute a normal, Bambu recomputes it; MeshCalculator gives the same triangles in another order from
    one call to the next), or None."""
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except OSError:
        return None
    n = struct.unpack_from('<I', data, 80)[0] if len(data) >= 84 else -1
    if len(data) != 84 + 50 * n:
        return data[80:]
    return b''.join(sorted(data[o + 12:o + 48] for o in range(84, len(data), 50)))


def export_stls(root, cfg, names):
    """Mesh the named parts in Fusion and write the STLs. Returns (changed, unchanged, failed, info)."""
    stl_dir = os.path.join(root, cfg.get('stl_dir', 'stl'))
    os.makedirs(stl_dir, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='export_', dir=fusion_rpc._out_dir())
    by_doc = {}
    for n in names:
        spec = dict(cfg['parts'][n])
        spec.update(name=n, out=os.path.join(tmp, n + '.stl'))
        by_doc.setdefault(spec.get('doc') or cfg.get('doc'), []).append(spec)
    with open(os.path.join(HERE, 'fusion', 'export_job.py'), encoding='utf-8') as f:
        src = f.read()
    changed, same, failed, info = [], [], [], []
    try:
        for doc, specs in by_doc.items():
            todo = specs
            while todo:
                res = fusion_rpc.run_job(src, {'doc': doc, 'parts': todo, 'budget_s': BUDGET_S}, write=True, timeout=BUDGET_S + 60, name='export')
                info.append('%s v%d%s: %d part(s) in %.1f s of Fusion (%.1f s with the call)' % (
                    res['doc'], res['version'], ' + unsaved changes' if res['modified'] else '', sum(1 for r in res['rows'] if 'tris' in r),
                    res['fusion_s'], res['_call_s']))
                left = {r['name'] for r in res['rows'] if r.get('skipped')}
                for r in res['rows']:
                    if r.get('skipped'):
                        continue
                    if r.get('error'):
                        failed.append((r['name'], r['error'])); continue
                    new, dst = os.path.join(tmp, r['name'] + '.stl'), os.path.join(stl_dir, r['name'] + '.stl')
                    if body_bytes(new) == body_bytes(dst):
                        same.append(r['name'])
                    else:
                        shutil.move(new, dst); changed.append(r['name'])
                todo = [s for s in todo if s['name'] in left]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return changed, same, failed, info


def swap_3mf(root, cfg, names):
    """Swap the STLs of `names` into every .3mf they go to. Returns {project: [changed objects]}."""
    import bambu3mf
    stl_dir = os.path.join(root, cfg.get('stl_dir', 'stl'))
    targets = {}
    for n in names:
        for t in cfg['parts'][n].get('3mf') or []:
            targets.setdefault(t['file'], []).append((n, t))
    out = {}
    for proj, rows in sorted(targets.items()):
        path = os.path.join(root, proj)
        tmp = tempfile.mkdtemp(prefix='swap_')
        try:
            sources = {}
            for n, t in rows:
                V, F = bambu3mf.read_stl(os.path.join(stl_dir, n + '.stl'))
                turn = bambu3mf.ORIENT[t.get('face', '-z')]
                V = [turn(*v) for v in V]
                with open(os.path.join(tmp, t['object'] + '.stl'), 'wb') as f:
                    f.write(('%s, bed face %s' % (n, t.get('face', '-z'))).encode().ljust(80, b' ')); f.write(struct.pack('<I', len(F)))
                    for a, b, c in F:
                        f.write(struct.pack('<12fH', 0, 0, 0, *V[a], *V[b], *V[c], 0))
                sources[t['object']] = os.path.join(stl_dir, n + '.stl')
            out[proj] = bambu3mf.update_meshes(path, tmp, sources=sources)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return out


def main():
    a = argparse.ArgumentParser(description='Fusion -> STL -> Bambu through the gates')
    a.add_argument('parts', nargs='*'); a.add_argument('--project')
    a.add_argument('--stl-only', action='store_true'); a.add_argument('--3mf-only', dest='only3mf', action='store_true')
    a.add_argument('--list', action='store_true')
    a = a.parse_args()
    root = os.path.abspath(a.project) if a.project else find_project(os.getcwd())
    cfg = load_parts(root)
    unknown = [n for n in a.parts if n not in cfg['parts']]
    if unknown:
        sys.exit('not in fusion/parts.json: %s' % ', '.join(unknown))
    names = a.parts or list(cfg['parts'])
    if a.list:
        for n in names:
            p = cfg['parts'][n]
            where = ', '.join('%s "%s" %s' % (os.path.basename(t['file']), t['object'], t.get('face', '-z')) for t in p.get('3mf') or []) or 'no .3mf'
            print('%-32s %-40s -> %s' % (n, p.get('component') or '/'.join(p.get('path') or []), where))
        return
    t0 = time.time()
    gate_failed = {}
    if not os.environ.get('PRINT_ENGINEERING_GATED'):                # the re-run under with_bambu_closed was gated already
        names, gate_failed, gates = run_gates(root, cfg, names, not a.parts)
        for n, gs in sorted(gate_failed.items()):
            print('NOT EXPORTED %s: fails %s' % (n, ', '.join(gs)))
        if not names:
            sys.exit(1)
    swap = names
    gated = not os.environ.get('PRINT_ENGINEERING_GATED')
    slicer = gated and 'slicer' in required_gates(root)[0]
    stl_dir = os.path.join(root, cfg.get('stl_dir', 'stl'))
    backup = tempfile.mkdtemp(prefix='stl_before_') if slicer and not a.only3mf else None
    if backup:                                                       # a part the slicer fails gets its old STL back
        for n in names:
            if os.path.exists(os.path.join(stl_dir, n + '.stl')):
                shutil.copy2(os.path.join(stl_dir, n + '.stl'), os.path.join(backup, n + '.stl'))
    if not a.only3mf:
        try:
            changed, same, failed, info = export_stls(root, cfg, names)
        except fusion_rpc.FusionError as e:
            sys.exit('Fusion: %s' % e)
        for line in info:
            print(line)
        print('STL changed %d, unchanged %d, failed %d  (%.1f s)' % (len(changed), len(same), len(failed), time.time() - t0))
        for n in changed:
            print('  changed  %s' % n)
        for n, e in failed:
            print('  FAILED   %s: %s' % (n, e))
        swap = [n for n in names if n not in dict(failed)]
    if slicer:
        sl_failed = run_slicer(root, cfg, swap)
        for n in sorted(sl_failed):
            gate_failed.setdefault(n, []).append('slicer')
            if backup:
                old = os.path.join(backup, n + '.stl')
                if os.path.exists(old):
                    shutil.copy2(old, os.path.join(stl_dir, n + '.stl'))
                elif os.path.exists(os.path.join(stl_dir, n + '.stl')):
                    os.remove(os.path.join(stl_dir, n + '.stl'))
            print('NOT EXPORTED %s: fails slicer (its STL is put back as it was)' % n)
        swap = [n for n in swap if n not in sl_failed]
    if backup:
        shutil.rmtree(backup, ignore_errors=True)
    if a.stl_only:
        sys.exit(1 if gate_failed or (not a.only3mf and failed) else 0)
    if not swap:
        sys.exit(1)
    import bambu3mf
    projects = sorted({t['file'] for n in swap for t in cfg['parts'][n].get('3mf') or []})
    if projects and bambu3mf.studio_running() and not os.environ.get('PRINT_ENGINEERING_BAMBU_CLOSED'):
        print('Bambu Studio is running: the .3mf step runs under with_bambu_closed.py (it quits Bambu and reopens the project)')
        cmd = [sys.executable, os.path.join(HERE, 'bambu', 'with_bambu_closed.py'), ','.join(os.path.join(root, p) for p in projects),
               sys.executable, os.path.abspath(__file__), '--project', root, '--3mf-only'] + swap
        env = dict(os.environ, PRINT_ENGINEERING_BAMBU_CLOSED='1', PRINT_ENGINEERING_GATED='1')
        rc = subprocess.call(cmd, env=env)
        sys.exit(rc or (1 if gate_failed else 0))
    t1 = time.time()
    res = swap_3mf(root, cfg, swap)
    for proj, ch in res.items():
        print('%s: %s' % (proj, ('%d mesh(es) swapped: %s' % (len(ch), ', '.join(ch))) if ch else 'no mesh changed'))
    print('3MF done in %.1f s; total %.1f s' % (time.time() - t1, time.time() - t0))
    if gate_failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
