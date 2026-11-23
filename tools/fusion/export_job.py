"""Fusion side of tools/export.py: tessellate printed bodies and write binary STLs. Runs inside Fusion through fusion_rpc
(job(JOB) is called with JOB = {"doc", "parts": [{"name", "component" | "path", "body", "out"}], "budget_s"}).

No checks: the body is meshed as it is in Fusion and written. Each body is meshed on a temporary copy
(TemporaryBRepManager), with MeshCalculator HighQuality: a proxy body's own mesh came out torn after edits of a
base-feature body, and displayMeshes do not exist for a body that was never drawn. Measured on Robot Arm V2 (39 parts):
about 2 s of meshing for all of them, 1.1 s of it the 216-tooth pulley. Coordinates are world mm (the occurrence's
place in the assembly); values under 1e-10 cm are written as 0 (a revolved face's seam came out as 0 and 1e-15 and
stayed open in Bambu)."""
import adsk.core, adsk.fusion, struct, time, math


def _doc(name):
    app = adsk.core.Application.get()
    docs = [d for d in app.documents if d.name == name] if name else [app.activeDocument]
    if not docs:
        raise KeyError('document %r is not open; open: %s' % (name, sorted({d.name for d in app.documents})))
    return docs[0]


def _all_occurrences(root):
    out = []
    stack = list(root.occurrences)
    while stack:
        o = stack.pop()
        out.append(o)
        stack.extend(o.childOccurrences)
    return out


def _find_body(des, spec, cache):
    """The body as a proxy in the assembly. spec: "component" (the name of a component that has exactly one occurrence)
    or "path" (occurrence names from the root), and "body" (a body name; without it the occurrence's one body)."""
    if spec.get('path'):
        occs, occ = des.rootComponent.occurrences, None
        for p in spec['path']:
            occ = next((o for o in occs if o.name == p), None)
            if occ is None:
                raise KeyError('no occurrence %r (here: %s)' % (p, [o.name for o in occs][:20]))
            occs = occ.childOccurrences
    elif spec.get('component'):
        if 'by_comp' not in cache:
            by = {}
            for o in _all_occurrences(des.rootComponent):
                by.setdefault(o.component.name, []).append(o)
            cache['by_comp'] = by
        occs = cache['by_comp'].get(spec['component'], [])
        if not occs:
            raise KeyError('no occurrence of component %r' % spec['component'])
        if len(occs) > 1:
            raise KeyError('component %r has %d occurrences (%s): give "path"' % (spec['component'], len(occs), [o.fullPathName for o in occs][:4]))
        occ = occs[0]
    else:
        occ = None
    bodies = list(occ.bRepBodies) if occ else list(des.rootComponent.bRepBodies)
    if spec.get('body'):
        bodies = [b for b in bodies if b.name == spec['body']]
    if len(bodies) != 1:
        raise KeyError('%d bodies match %s (the component has: %s)' % (len(bodies), spec.get('body') or 'its one body',
                       [b.name for b in (occ.bRepBodies if occ else des.rootComponent.bRepBodies)][:12]))
    return bodies[0]


def _write_stl(path, coords, idx, header):
    n = len(idx) // 3
    c = [0.0 if abs(x) < 1e-10 else x * 10.0 for x in coords]          # cm -> mm
    buf = bytearray(84 + 50 * n)
    buf[0:80] = header.encode('ascii', 'replace')[:80].ljust(80, b' ')
    struct.pack_into('<I', buf, 80, n)
    pack = struct.Struct('<12fH').pack_into
    off = 84
    for t in range(0, 3 * n, 3):
        i, j, k = 3 * idx[t], 3 * idx[t + 1], 3 * idx[t + 2]
        ax, ay, az, bx, by, bz, cx, cy, cz = c[i], c[i + 1], c[i + 2], c[j], c[j + 1], c[j + 2], c[k], c[k + 1], c[k + 2]
        ux, uy, uz, vx, vy, vz = bx - ax, by - ay, bz - az, cx - ax, cy - ay, cz - az
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        L = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        pack(buf, off, nx / L, ny / L, nz / L, ax, ay, az, bx, by, bz, cx, cy, cz, 0)
        off += 50
    with open(path, 'wb') as f:
        f.write(buf)
    return n


def job(JOB):
    t0 = time.time()
    d = _doc(JOB.get('doc'))
    des = adsk.fusion.Design.cast(d.products.itemByProductType('DesignProductType'))
    ver = d.dataFile.versionNumber if d.dataFile else 0
    tbm = adsk.fusion.TemporaryBRepManager.get()
    q = adsk.fusion.TriangleMeshQualityOptions.HighQualityTriangleMesh
    cache, rows = {}, []
    budget = float(JOB.get('budget_s') or 50)
    for spec in JOB['parts']:
        if time.time() - t0 > budget:
            rows.append({'name': spec['name'], 'skipped': 'budget'})
            continue
        r = {'name': spec['name']}
        t = time.time()
        try:
            b = _find_body(des, spec, cache)
            mc = tbm.copy(b).meshManager.createMeshCalculator()
            mc.setQuality(q)
            m = mc.calculate()
            header = 'Fusion %s v%d%s %s' % (d.name, ver, '+' if d.isModified else '', spec['name'])
            r['tris'] = _write_stl(spec['out'], m.nodeCoordinatesAsDouble, m.nodeIndices, header)
            r['volume_mm3'] = round(b.volume * 1000, 2)
        except Exception as e:
            r['error'] = '%s: %s' % (type(e).__name__, e)
        r['s'] = round(time.time() - t, 3)
        rows.append(r)
    return {'doc': d.name, 'version': ver, 'modified': d.isModified, 'rows': rows, 'fusion_s': round(time.time() - t0, 3)}
