"""Host-side: the STL wall and mesh checks on the mesh each object of a saved .3mf prints from. No Fusion, no slicer.

The export-stage rules (walls.min_material, walls.slit, walls.one_line, mesh.closed, mesh.one_shell) run when a part is
exported. A part printed before the gate, or whose STL nobody exported again, was never measured. This module measures
every object of a registered project as the project holds it: the stored mesh of the object (project_bed.read_placed),
written to gate/run/placed_walls/<pose>.stl and measured there by stl_mesh and stl_walls, exactly as an export would.

stl_walls can take up to a minute on a big part, and `gate.py verify` must stay quick. So the results are kept in
gate/run/placed_walls/<pose>.json, keyed by the pose hash of the stored mesh (meshid.geom_hash) and the report width
they were measured with: a mesh is measured once, and again only when it changes. One call measures for at most
`budget_s` seconds; an object it did not reach is "pending" and the next call goes on with it (the cache is the cursor).
An object the walls probe covered only in part in its time is kept with its coverage and measured again by a call that
has more time. The rule walls.placed_stl says NOT_RUN for a pending object and for a coverage under
walls.coverage_min, never a pass.

facts(root, path, rows, report_under, budget_s) -> {"report_under", "objects": [{"name", "part", "plate", "plate_index",
    "pose", "state": "measured" | "pending" | "error", "mesh": stl_mesh facts, "walls": stl_walls facts or None (an open
    mesh is not measured for walls), "why"}], "measured_now": n, "budget_s"}."""
import os, struct, time
import gate_common
from gate_common import GateError

_DIR = ('run', 'placed_walls')


def _stl(path, vertices, triangles):
    out = [b'gate placed mesh'.ljust(80, b'\0'), struct.pack('<I', len(triangles))]
    for a, b, c in triangles:
        p, q, r = vertices[a], vertices[b], vertices[c]
        out.append(struct.pack('<12fH', 0.0, 0.0, 0.0, *(tuple(p) + tuple(q) + tuple(r)), 0))
    tmp = path + '.part'
    with open(tmp, 'wb') as f:
        f.write(b''.join(out))
    os.replace(tmp, path)


def facts(root, path, rows, report_under, budget_s=20.0):
    """See the module text. `rows` are the object rows of project_consistency.measure() (name, id, part, plate, ...);
    `path` the .3mf. Never raises for one object: its row says "error" and why."""
    from probes import project_bed, stl_mesh, stl_walls
    folder = gate_common.gate_path(root, *_DIR)
    out = {'report_under': report_under, 'objects': [], 'measured_now': 0, 'budget_s': budget_s}
    try:
        placed = {str(o['id']): o for o in project_bed.read_placed(path)['objects']}
    except GateError as e:
        out['error'] = str(e)
        return out
    start = time.time()
    for r in rows:
        row = {k: r.get(k) for k in ('name', 'part', 'plate', 'plate_index', 'pose')}
        o = placed.get(str(r.get('id')))
        if o is None:
            out['objects'].append(dict(row, state='error', why='the mesh of this object could not be read'))
            continue
        key = '%s_%s' % (o['pose'][:16], ('%g' % report_under).replace('.', 'p'))
        cache = os.path.join(folder, key + '.json')
        stored = gate_common.read_json(cache, None) if os.path.isfile(cache) else None
        left = budget_s - (time.time() - start)
        if isinstance(stored, dict) and stored.get('pose') == o['pose'] and stored.get('report_under') == report_under:
            partial = isinstance(stored.get('walls'), dict) and (stored['walls'].get('coverage') or 0) < 1.0
            if not (partial and left > float(stored.get('budget') or 0) + 0.5):
                out['objects'].append(dict(row, state='measured', mesh=stored.get('mesh'), walls=stored.get('walls')))
                continue                         # measured; a part measured over a share of its surface is measured again with more time
        if left <= 1.0:
            out['objects'].append(dict(row, state='pending', why='not measured yet: the time of this call was used up; the next `gate.py verify` goes on with it'))
            continue
        try:
            os.makedirs(folder, exist_ok=True)
            stl = os.path.join(folder, key + '.stl')
            _stl(stl, o['vertices'], o['triangles'])
            mesh = stl_mesh.measure(stl)
            walls, budget = None, max(5.0, min(600.0, left))
            if not mesh.get('open_edges') and not mesh.get('non_manifold_edges'):
                walls = stl_walls.measure(stl, report_under, budget_s=budget, seed=1)
            os.remove(stl)
            # a part measured over a share of its surface only ("coverage" under 1) is kept as it is: walls.min_material
            # says NOT_RUN with the share, and a later call with more time measures it again
            gate_common.write_json(cache, {'pose': o['pose'], 'report_under': report_under, 'mesh': mesh, 'walls': walls, 'budget': budget, 'time': gate_common.now()})
            out['measured_now'] += 1
            out['objects'].append(dict(row, state='measured', mesh=mesh, walls=walls))
        except (GateError, OSError, ValueError, MemoryError) as e:
            out['objects'].append(dict(row, state='error', why='%s: %s' % (type(e).__name__, e)))
    return out
