#!/usr/bin/env python3
"""Fusion -> STL -> Bambu, with no checks: what Fusion has goes into the .3mf projects as it is. Fast by design: one
Fusion call per document (all parts meshed in it), one load and one save per .3mf.

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
    a = argparse.ArgumentParser(description='Fusion -> STL -> Bambu, no checks')
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
    swap = names
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
        if a.stl_only:
            sys.exit(1 if failed else 0)
        swap = [n for n in names if n not in dict(failed)]
    import bambu3mf
    projects = sorted({t['file'] for n in swap for t in cfg['parts'][n].get('3mf') or []})
    if projects and bambu3mf.studio_running() and not os.environ.get('PRINT_ENGINEERING_BAMBU_CLOSED'):
        print('Bambu Studio is running: the .3mf step runs under with_bambu_closed.py (it quits Bambu and reopens the project)')
        cmd = [sys.executable, os.path.join(HERE, 'bambu', 'with_bambu_closed.py'), ','.join(os.path.join(root, p) for p in projects),
               sys.executable, os.path.abspath(__file__), '--project', root, '--3mf-only'] + swap
        env = dict(os.environ, PRINT_ENGINEERING_BAMBU_CLOSED='1')
        sys.exit(subprocess.call(cmd, env=env))
    t1 = time.time()
    res = swap_3mf(root, cfg, swap)
    for proj, ch in res.items():
        print('%s: %s' % (proj, ('%d mesh(es) swapped: %s' % (len(ch), ', '.join(ch))) if ch else 'no mesh changed'))
    print('3MF done in %.1f s; total %.1f s' % (time.time() - t1, time.time() - t0))


if __name__ == '__main__':
    main()
