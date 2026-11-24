"""The overhang gate's second opinion: where Bambu Studio really puts support (gate.py overhang-slice).

Each .3mf the parts go to (fusion/parts.json "3mf") is COPIED to a scratch folder and sliced there with the Bambu Studio
CLI (all plates; minutes, so run it in the background). The original is only read: safe while Bambu Studio has it open.
In the copy every object is printable (a part already printed is often switched off) and the prime tower is off.
From the G-code, per object (its "start printing object" block, or the whole plate when it holds one object): the support
line (mm of extrusion moves of the Support features) and the support's contact cells: the tops of the support columns,
binned in cell_mm, that are as high as their neighbours (the tips under the part, not the slanted branches).
The cells are carried into the model's world frame (the part's bed face down = bambu3mf.ORIENT, the 3mf object's turn
about z, the offset from the boxes of the part and of its printed walls) and compared with the regions of the last
geometry run (gate/results/overhang/<part>.json): a region with a contact within near_mm agrees; a forbidden region with no
contact is "gate_only" (said why when the slicer's threshold angle or a support switched off explains it); a cluster of
contacts away from every region is "slicer_only" (the gate missed a shape, or the slicer supports a bridge the gate
allows). The comparison goes into each part's result file ("slicer") and gate/results/overhang/_slicer.json."""
import json, math, os, re, shutil, subprocess, sys, tempfile, time, zipfile
import overhang_core as oc

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(PLUGIN, 'tools', 'bambu'))
import bambu3mf  # noqa: E402

SUPPORT = ('Support', 'Support interface', 'Support transition')
NOT_PART = ('Brim', 'Skirt', 'Prime tower', 'Custom', 'Wipe tower') + SUPPORT
_NUM = re.compile(r'([XYZE])(-?[\d.]+)')


# ---------------------------------------------------------------- the 3mf
def _xf(t):
    v = [float(x) for x in t.split()]
    return [v[0:3], v[3:6], v[6:9], v[9:12]]                 # rows: p' = p . M + row 4


def _rot_mul(a, b):
    """The rotation of p . A . B (3x3 parts only)."""
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _apply(p, m):
    return [p[0] * m[0][j] + p[1] * m[1][j] + p[2] * m[2][j] + m[3][j] for j in range(3)]


def read_3mf(path):
    """{object name: {"xf": [component, item] transforms (rows, p' = p . M + t), "lo", "hi": the mesh box in object
    coordinates, "glo", "ghi": the box of the placed mesh, "support": per-object enable_support or None, "printable"}}
    and the project's support settings."""
    z = zipfile.ZipFile(path)
    model = z.read('3D/3dmodel.model').decode('utf-8', 'ignore')
    one = _xf('1 0 0 0 1 0 0 0 1 0 0 0')
    comp = {}
    for m in re.finditer(r'<object id="(\d+)"[^>]*>(.*?)</object>', model, re.S):
        cs = []
        for c in re.finditer(r'<component ([^>]*)/>', m.group(2)):
            a = dict(re.findall(r'([\w:]+)="([^"]*)"', c.group(1)))
            if 'p:path' in a:
                cs.append((a['p:path'].lstrip('/'), a['objectid'], _xf(a['transform']) if a.get('transform') else one))
        if cs:
            comp[m.group(1)] = cs
    items = {}
    for m in re.finditer(r'<item objectid="(\d+)"([^>]*)>', model):
        t = re.search(r'transform="([^"]*)"', m.group(2)); pr = re.search(r'printable="(\d)"', m.group(2))
        items[m.group(1)] = (_xf(t.group(1)) if t else one, not pr or pr.group(1) == '1')
    meshes = {}
    def mesh(fname, oid):
        if (fname, oid) not in meshes:
            txt = z.read(fname).decode('utf-8', 'ignore')
            o = re.search(r'<object id="%s"[^>]*>(.*?)</object>' % oid, txt, re.S)
            V = [(float(a), float(b), float(c)) for a, b, c in re.findall(r'<vertex x="([^"]+)" y="([^"]+)" z="([^"]+)"', o.group(1) if o else txt)]
            meshes[(fname, oid)] = V
        return meshes[(fname, oid)]
    cfg = z.read('Metadata/model_settings.config').decode('utf-8', 'ignore')
    out = {}
    for m in re.finditer(r'<object id="(\d+)">(.*?)</object>', cfg, re.S):
        name = re.search(r'<metadata key="name" value="([^"]*)"', m.group(2))
        sup = re.search(r'<metadata key="enable_support" value="(\d)"', m.group(2).split('<part')[0])
        oid = m.group(1)
        if not name or name.group(1) in out or oid not in comp:
            continue
        kinds = dict(re.findall(r'<part id="(\d+)" subtype="([^"]+)"', m.group(2)))
        cs = comp[oid]
        fname, sub, C = next((c for c in cs if kinds.get(c[1], 'normal_part') == 'normal_part'), cs[0])
        I, printable = items.get(oid, (one, True))
        V = mesh(fname, sub)
        if not V:
            continue
        G = [_apply(_apply(v, C), I) for v in V]
        blockers = []
        for bf, bs, BC in cs:
            if kinds.get(bs) == 'support_blocker':
                BG = [_apply(_apply(v, BC), I) for v in mesh(bf, bs)]
                if BG:
                    blockers.append([[min(g[i] for g in BG) for i in range(3)], [max(g[i] for g in BG) for i in range(3)]])
        out[name.group(1)] = {'xf': [C, I], 'support': None if not sup else sup.group(1) == '1', 'id': oid, 'printable': printable,
                              'blockers': blockers,
                              'lo': [min(v[i] for v in V) for i in range(3)], 'hi': [max(v[i] for v in V) for i in range(3)],
                              'glo': [min(g[i] for g in G) for i in range(3)], 'ghi': [max(g[i] for g in G) for i in range(3)]}
    ps = json.loads(z.read('Metadata/project_settings.config').decode('utf-8', 'ignore'))
    keys = ('enable_support', 'support_type', 'support_threshold_angle', 'support_remove_small_overhang', 'bridge_no_support',
            'max_bridge_length', 'support_on_build_plate_only', 'support_top_z_distance', 'layer_height')
    return out, {k: ps.get(k) for k in keys}


def slice_copy(src, out_dir, timeout=3600):
    """Slice a copy of src into out_dir: plate_<n>.gcode, sliced.3mf (its slice_info names the objects of each label)."""
    exe = bambu3mf.studio_path()
    if not exe:
        raise RuntimeError('Bambu Studio not found: set BAMBU_STUDIO to its executable')
    os.makedirs(out_dir, exist_ok=True)
    copy = os.path.join(out_dir, 'in.3mf')
    with zipfile.ZipFile(src) as zi, zipfile.ZipFile(copy, 'w', zipfile.ZIP_DEFLATED) as zo:     # every object printable in the copy
        for info in zi.infolist():
            data = zi.read(info.filename)
            if info.filename == '3D/3dmodel.model':
                data = data.replace(b'printable="0"', b'printable="1"')
            elif info.filename == 'Metadata/project_settings.config':        # objects switched off may stand where the tower is
                cfg = json.loads(data.decode('utf-8'))
                cfg['enable_prime_tower'] = '0'
                data = json.dumps(cfg, indent=4).encode('utf-8')
            zo.writestr(info, data)
    r = subprocess.run([exe, '--slice', '0', '--outputdir', out_dir, '--export-3mf', 'sliced.3mf', copy], cwd=out_dir,
                       capture_output=True, text=True, timeout=timeout)
    res = os.path.join(out_dir, 'result.json')
    data = json.load(open(res)) if os.path.exists(res) else {}
    if r.returncode or data.get('return_code', 0):
        raise RuntimeError('slicing %s failed: %s' % (os.path.basename(src), (r.stdout + r.stderr)[-800:]))
    return data


def labels(out_dir):
    """{plate number: {label id or None: object name}} from sliced.3mf (Metadata/slice_info.config)."""
    p = os.path.join(out_dir, 'sliced.3mf')
    out = {}
    if not os.path.exists(p):
        return out
    cfg = zipfile.ZipFile(p).read('Metadata/slice_info.config').decode('utf-8', 'ignore')
    for m in re.finditer(r'<plate>(.*?)</plate>', cfg, re.S):
        idx = re.search(r'<metadata key="index" value="(\d+)"', m.group(1))
        objs = re.findall(r'<object identify_id="(\d+)" name="([^"]*)"', m.group(1))
        if idx:
            out[int(idx.group(1))] = {i: n for i, n in objs}
    return out


# ---------------------------------------------------------------- the G-code
def read_gcode(path, cell):
    """{label or None: {"support_mm", "box": [[x0,y0,z0],[x1,y1,z1]] of its printed part (no brim, no support),
    "tops": {(ix, iy): {the z of every layer with support in that cell}}}}."""
    objs = {}
    lab, feat, z = None, '', 0.0
    x = y = 0.0
    def o():
        return objs.setdefault(lab, {'support_mm': 0.0, 'box': None, 'tops': {}})
    with open(path, errors='ignore') as fh:
        lines = fh.read().splitlines(True)
    for line in lines:
        if not line:
            continue
        if line[0] == ';':
            if line.startswith('; FEATURE:'):
                feat = line[10:].strip()
            elif line.startswith('; Z_HEIGHT:'):
                z = float(line.split(':')[1])
            elif line.startswith('; start printing object'):
                lab = line.rsplit(':', 1)[1].strip()
            elif line.startswith('; stop printing object'):
                lab = None
            continue
        if not (line.startswith('G1 ') or line.startswith('G2 ') or line.startswith('G3 ')):
            continue
        p = dict(_NUM.findall(line.split(';', 1)[0]))
        nx, ny = float(p.get('X', x)), float(p.get('Y', y))
        e = float(p.get('E', 0))
        if e > 0 and feat and ('X' in p or 'Y' in p):                  # an extruding move (not an unretract in place)
            r = o()
            if feat in SUPPORT:
                L = math.hypot(nx - x, ny - y); r['support_mm'] += L
                n = max(1, int(L / (cell / 2)))
                for k in range(n + 1):
                    c = (int(math.floor((x + (nx - x) * k / n) / cell)), int(math.floor((y + (ny - y) * k / n) / cell)))
                    r['tops'].setdefault(c, set()).add(round(z, 3))
            elif feat not in NOT_PART:
                b = r['box']
                if b is None:
                    r['box'] = [[nx, ny, z], [nx, ny, z]]
                else:
                    b[0][0] = min(b[0][0], nx, x); b[0][1] = min(b[0][1], ny, y); b[1][0] = max(b[1][0], nx, x); b[1][1] = max(b[1][1], ny, y)
                    b[1][2] = max(b[1][2], z); b[0][2] = min(b[0][2], z)
        x, y = nx, ny
    return objs


def contacts(levels, layer):
    """The support tips under the part: {(i, j): z} for every cell and every top of a run of support layers in it (a
    column can hold several: a hole under a hole). A top is a tip when no neighbouring cell carries support a little
    higher (within three layers: then it is a slanted branch going on), and it is above the first two layers (the foot
    of a tree holds nothing). A cell keeps its highest tip; the others are listed under (i, j, k)."""
    out = {}
    for (i, j), zs in levels.items():
        zs = sorted(zs)
        runs = [z for k, z in enumerate(zs) if k == len(zs) - 1 or zs[k + 1] - z > 1.6 * layer]
        k = 0
        for z in sorted(runs, reverse=True):
            if z <= 2 * layer + 1e-6:
                continue
            higher = any(z + 1e-6 < w <= z + 3 * layer + 1e-6 for a in (-1, 0, 1) for b in (-1, 0, 1) if (a or b)
                         for w in levels.get((i + a, j + b), ()))
            if higher:
                continue
            out[(i, j) if k == 0 else (i, j, k)] = z
            k += 1
    return out


def clusters(cells, dz=1.0):
    """Connected groups (8-neighbours, tips within dz in height) of {(i, j[, k]): z}."""
    left, out = dict(cells), []
    while left:
        k, z = left.popitem(); grp = {k: z}; todo = [k]
        while todo:
            q0 = todo.pop(); z0 = grp[q0]
            for q in [q for q in left if abs(q[0] - q0[0]) <= 1 and abs(q[1] - q0[1]) <= 1 and abs(left[q] - z0) <= dz]:
                grp[q] = left.pop(q); todo.append(q)
        out.append(grp)
    return out


# ---------------------------------------------------------------- model <-> print frame
class Frame:
    """Model world (mm) -> print frame of the G-code. The part's bed face down (bambu3mf.ORIENT, a signed permutation of
    the axes), moved onto the 3mf object's mesh (box on box), placed by its component and build item transforms, and
    moved by the plate's origin (the box of the placed mesh on the box of the printed walls, z from the bed)."""
    def __init__(self, face, o3, model_box, gcode_box):
        self.turn = bambu3mf.ORIENT[face]
        self.C, self.I = o3['xf']
        corners = [self.turn(x, y, z) for x in (model_box[0][0], model_box[1][0]) for y in (model_box[0][1], model_box[1][1])
                   for z in (model_box[0][2], model_box[1][2])]
        lo = [min(c[i] for c in corners) for i in range(3)]; hi = [max(c[i] for c in corners) for i in range(3)]
        self.d = [o3['lo'][i] - lo[i] for i in range(3)]
        self.size_model = [hi[i] - lo[i] for i in range(3)]
        self.size_mesh = [o3['hi'][i] - o3['lo'][i] for i in range(3)]
        self.size_print = [gcode_box[1][0] - gcode_box[0][0], gcode_box[1][1] - gcode_box[0][1]]
        self.size_placed = [o3['ghi'][i] - o3['glo'][i] for i in range(2)]
        self.t = [(gcode_box[0][0] + gcode_box[1][0]) / 2 - (o3['glo'][0] + o3['ghi'][0]) / 2,
                  (gcode_box[0][1] + gcode_box[1][1]) / 2 - (o3['glo'][1] + o3['ghi'][1]) / 2, -o3['glo'][2]]

    def to_print(self, p):
        q = self.turn(*p)
        q = [q[i] + self.d[i] for i in range(3)]
        g = _apply(_apply(q, self.C), self.I)
        return [g[i] + self.t[i] for i in range(3)]

    def to_model(self, q):
        g = [q[i] - self.t[i] for i in range(3)]
        for M in (self.I, self.C):                                    # undo p . R + t (R orthonormal: R^-1 = R^T)
            r = [g[i] - M[3][i] for i in range(3)]
            g = [sum(M[j][k] * r[k] for k in range(3)) for j in range(3)]
        g = [g[i] - self.d[i] for i in range(3)]
        e = [self.turn(1, 0, 0), self.turn(0, 1, 0), self.turn(0, 0, 1)]      # columns of the turn: its inverse is the transpose
        return [sum(e[i][k] * g[k] for k in range(3)) for i in range(3)]

    def problem(self, mesh_tol=1.0, print_tol=2.5):
        if any(abs(self.size_model[i] - self.size_mesh[i]) > mesh_tol for i in range(3)):
            return 'the mesh in the project is %.1f x %.1f x %.1f, the body %.1f x %.1f x %.1f (bed face down): a stale mesh, export first' % (
                tuple(self.size_mesh) + tuple(self.size_model))
        if any(abs(self.size_placed[i] - self.size_print[i]) > print_tol for i in range(2)):
            return 'the printed walls span %.1f x %.1f, the placed mesh %.1f x %.1f: the object was not found in its G-code' % (
                tuple(self.size_print) + tuple(self.size_placed))
        return None


def region_box(frame, r):
    lo, hi = r['box']
    pts = [frame.to_print([x, y, z]) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    return [min(p[i] for p in pts) for i in range(3)], [max(p[i] for p in pts) for i in range(3)]


def compare(regions, info, tips, cell, near, frame, layer, threshold, support_on, min_line, line_mm, allowed=('heatset_ceiling', 'screw_seat'),
            blockers=(), gap=0.2, bridge_contact=2.0, tree_mm=3.0):
    """The comparison of one part (see the module doc)."""
    boxes = [(r, region_box(frame, r)) for r in regions + info]
    def hits(c, z, lo, hi):
        x, y = (c[0] + 0.5) * cell, (c[1] + 0.5) * cell
        return lo[0] - near <= x <= hi[0] + near and lo[1] - near <= y <= hi[1] + near and lo[2] - 1.5 - 2 * layer <= z <= hi[2] + layer
    used, rows, on_info = set(), [], {}
    for r, (lo, hi) in boxes:
        if r not in info:
            used.update(c for c, z in tips.items() if hits(c, z, lo, hi))
    # the places the gate allows (a bridge, a small patch): each contact cell counts once, for the nearest of them, and only
    # within one cell of its box (the regions of one screw counterbore overlap: a 2 mm window counted one tip four times)
    own = {}
    for c, z in tips.items():
        if c in used:
            continue
        x, y = (c[0] + 0.5) * cell, (c[1] + 0.5) * cell
        best = None
        for k, (r, (lo, hi)) in enumerate(boxes):
            if r not in info or not (lo[2] - 1.5 - 2 * layer <= z <= hi[2] + layer):
                continue
            d = math.hypot(max(lo[0] - x, 0, x - hi[0]), max(lo[1] - y, 0, y - hi[1]))
            if d <= cell and (best is None or d < best[0]):
                best = (d, k)
        if best:
            own.setdefault(best[1], []).append(c)
    supported = []
    for k, got in sorted(own.items()):
        r = boxes[k][0]
        e = on_info.setdefault(r['code'], {'n': 0, 'area_mm2': 0.0, 'contact_mm2': 0.0})
        e['n'] += 1; e['area_mm2'] = round(e['area_mm2'] + r['area_mm2'], 1); e['contact_mm2'] = round(e['contact_mm2'] + len(got) * cell * cell, 1)
        if r['code'] == 'bridge' and r.get('pocket') is not None:
            continue                                         # support in a heat-set pocket is allowed (the insert closes it), as heatset_ceiling
        if r['code'] == 'bridge' and len(got) * cell * cell >= bridge_contact - 1e-9:
            supported.append(dict(r, code='slicer_bridge', contact_mm2=round(len(got) * cell * cell, 1)))
    for r, (lo, hi) in boxes:                       # near an allowed place, not support the gate missed
        if r in info:
            used.update(c for c, z in tips.items() if hits(c, z, lo, hi))
    for r, (lo, hi) in boxes:
        if r in info:
            continue
        got = [c for c, z in tips.items() if hits(c, z, lo, hi)]
        row = {'code': r['code'], 'at': r['at'], 'area_mm2': r['area_mm2'], 'slope_deg': r['slope_deg'], 'contact_mm2': round(len(got) * cell * cell, 1)}
        if got:
            row['slicer'] = 'supports'
        elif r['code'] in allowed or r.get('exception'):
            row['slicer'] = 'no support (allowed place)'
        else:
            row['slicer'] = 'no support'
            if support_on is False:
                row['why'] = 'support is switched off for this object in the project'
            elif any(all(b[0][i] - 0.5 <= hi[i] and lo[i] <= b[1][i] + 0.5 for i in range(3)) for b in blockers):
                row['why'] = 'a support blocker of the project covers it'
            elif lo[2] <= gap + 2 * layer + 1e-6:
                row['why'] = 'only %.2f above the bed: no room for a support under it (the slicer leaves a %.1f gap)' % (lo[2], gap)
            elif r['code'] == 'long_bridge':
                row['why'] = 'the slicer bridges it with no support; longer than the gate allows, the bridge sags'
            elif r['code'] == 'shallow_ridge':
                row['why'] = 'a ridge is a line: the slicer prints it as an overhang wall'
            elif threshold and r['slope_deg'] >= threshold:
                row['why'] = 'steeper than the gate allows, but above the slicer threshold angle %g: printed unsupported, it sags' % threshold
        rows.append(row)
    free = {c: z for c, z in tips.items() if c not in used}
    extra = []
    for g in clusters(free):
        cs = list(g)
        q = [sum((c[0] + 0.5) * cell for c in cs) / len(cs), sum((c[1] + 0.5) * cell for c in cs) / len(cs), max(g.values())]
        extra.append({'at': [round(v, 2) for v in frame.to_model(q)], 'h': round(q[2], 2), 'contact_mm2': round(len(cs) * cell * cell, 1),
                      'xy': [round(q[0], 2), round(q[1], 2)]})
    extra.sort(key=lambda e: -e['contact_mm2'])
    # support the gate has no place for: one finding per tree (the tips of one trunk stand within tree_mm of each other in
    # plan; its highest tip is what the tree holds up)
    groups = []                                                   # single link: a leaning trunk is a chain of tips
    for e in extra:
        near_ = [g for g in groups if any(math.hypot(e['xy'][0] - q['xy'][0], e['xy'][1] - q['xy'][1]) <= tree_mm for q in g)]
        merged_ = [e] + [q for g in near_ for q in g]
        groups = [g for g in groups if g not in near_] + [merged_]
    trees = []
    for g in groups:
        top = max(g, key=lambda q: q['h'])
        trees.append(dict(top, contact_mm2=round(sum(q['contact_mm2'] for q in g), 1), tips=len(g)))
    unplaced = [{'code': 'slicer_support', 'at': t['at'], 'box': [t['at'], t['at']], 'area_mm2': t['contact_mm2'], 'slope_deg': 0.0,
                 'h': t['h'], 'contact_mm2': t['contact_mm2'], 'tips': t['tips']}
                for t in trees if t['contact_mm2'] >= bridge_contact - 1e-9] if line_mm >= min_line else []
    gate_only = [r for r in rows if r['slicer'] == 'no support' and not r.get('why')]
    sig_extra = [e for e in extra if e['contact_mm2'] >= 2 * cell * cell] if line_mm >= min_line else []
    agree = not gate_only and not sig_extra
    return {'support_mm': round(line_mm, 1), 'contact_mm2': round(len(tips) * cell * cell, 1),
            'expected_mm2': round(sum(r['area_mm2'] for r in regions if r['code'] != 'shallow_ridge'), 1),
            'regions': rows, 'slicer_only': sig_extra, 'slicer_on_allowed': on_info, 'slicer_only_small': len(extra) - len(sig_extra),
            'explained': len([r for r in rows if r['slicer'] == 'no support' and r.get('why')]), 'agree': agree,
            'bridge_findings': supported, 'support_findings': unplaced}


# ---------------------------------------------------------------- the command
def run(root, names, keep=None):
    import gate
    import overhang_gate
    rules, settings, merged, hs, pcfg = overhang_gate.load(root)
    sc = merged['slicer']; cell, near = sc['cell_mm'], sc['near_mm']
    res_dir = os.path.join(root, 'gate', 'results', 'overhang')
    todo = [n for n in (names or list(pcfg['parts'])) if n not in set(settings.get('skip') or [])]
    by_file = {}
    for n in todo:
        for t in pcfg['parts'][n].get('3mf') or []:
            by_file.setdefault(t['file'], []).append((n, t))
    work = keep or tempfile.mkdtemp(prefix='overhang_slice_')
    summary, t0 = {'gate': 'overhang-slice', 'gate_version': gate.plugin_version(), 'projects': {}, 'parts': {}}, time.time()
    for f, rows in sorted(by_file.items()):
        src = os.path.join(root, f)
        od = os.path.join(work, os.path.splitext(os.path.basename(f))[0])
        print('slicing a copy of %s ...' % f, flush=True)
        t = time.time()
        try:
            done = os.path.join(od, 'result.json')
            if keep and os.path.exists(done) and os.path.getmtime(done) > os.path.getmtime(src) and \
                    json.load(open(done)).get('return_code', 1) == 0 and os.path.exists(os.path.join(od, 'sliced.3mf')):
                print('  (the slice of the same project in %s is newer: reused)' % od)
            else:
                slice_copy(src, od)
        except Exception as e:
            print('ERROR %s' % e); summary['projects'][f] = {'error': str(e)}; continue
        objs3, settings3 = read_3mf(src)
        layer = float(settings3.get('layer_height') or 0.2)
        thr = float(settings3.get('support_threshold_angle') or 0) or None
        labs = labels(od)
        where = {}
        for pl, m in labs.items():
            gp = os.path.join(od, 'plate_%d.gcode' % pl)
            if not os.path.exists(gp):
                continue
            g = read_gcode(gp, cell)
            for lab, name in m.items():
                where.setdefault(name, g.get(lab) or (g.get(None) if len(m) == 1 else None))
        summary['projects'][f] = {'slice_s': round(time.time() - t, 1), 'settings': settings3, 'plates': len(labs)}
        mb, rb = settings3.get('max_bridge_length'), merged.get('bridge_max_mm', {}).get('value') if isinstance(merged.get('bridge_max_mm'), dict) else merged.get('bridge_max_mm')
        if settings3.get('bridge_no_support') != '1' and mb is not None and rb is not None and abs(float(mb) - float(rb)) > 1e-6:
            summary['projects'][f]['bridge_note'] = ('the slicer bridges up to max_bridge_length %s mm without support, the gate '
                                                     'allows %s mm: they disagree on bridges in between' % (mb, rb))
        for n, tcfg in rows:
            rp = os.path.join(res_dir, n + '.json')
            if not os.path.exists(rp):
                summary['parts'][n] = {'error': 'no geometry result: run gate.py overhang first'}; continue
            with open(rp, encoding='utf-8') as fh:
                rec = json.load(fh)
            g, o3 = where.get(tcfg['object']), objs3.get(tcfg['object'])
            if not g or not g['box'] or not o3 or not rec.get('box'):
                rec['slicer'] = {'error': 'object %r not found in the slice of %s' % (tcfg['object'], f)}
            else:
                fr = Frame(tcfg.get('face', '-z'), o3, rec['box'], g['box'])
                if fr.problem():
                    rec['slicer'] = {'error': '%s: %s' % (f, fr.problem())}
                else:
                    tips = contacts(g['tops'], layer)
                    bl = [[[b[0][i] + fr.t[i] for i in range(3)], [b[1][i] + fr.t[i] for i in range(3)]] for b in o3['blockers']]
                    rec['slicer'] = dict(compare(rec['findings'] + rec['allowed'] + rec.get('excepted', []), rec.get('info') or [], tips, cell, near, fr, layer, thr,
                                                 o3['support'], sc['min_support_mm'], g['support_mm'], blockers=bl,
                                                 gap=float(settings3.get('support_top_z_distance') or 0.2),
                                                 bridge_contact=sc.get('bridge_contact_mm2', 2.0)),
                                         blockers=len(bl),
                                         project=f, object=tcfg['object'], threshold_deg=thr, object_support=o3['support'],
                                         printable_in_project=o3['printable'],
                                         sliced_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
            sf = overhang_gate.slicer_findings(rec, merged)
            rec['findings'] = [f for f in rec['findings'] if f['code'] not in ('slicer_bridge', 'slicer_support')] + sf
            rec['verdict'] = 'fail' if rec['findings'] else 'pass'
            with open(rp, 'w', encoding='utf-8') as fh:
                json.dump(rec, fh, indent=1, ensure_ascii=False); fh.write('\n')
            s = rec['slicer']
            summary['parts'][n] = {k: s.get(k) for k in ('error', 'agree', 'support_mm', 'contact_mm2', 'expected_mm2')}
            summary['parts'][n]['gate_only'] = len([r for r in s.get('regions', []) if r['slicer'] == 'no support' and not r.get('why')])
            summary['parts'][n]['explained'] = s.get('explained', 0)
            summary['parts'][n]['on_allowed'] = s.get('slicer_on_allowed') or {}
            summary['parts'][n]['slicer_only'] = len(s.get('slicer_only', []))
            summary['parts'][n]['slicer_bridge'] = len(s.get('bridge_findings') or [])
            summary['parts'][n]['slicer_support'] = len(s.get('support_findings') or [])
    summary['total_s'] = round(time.time() - t0, 1)
    with open(os.path.join(res_dir, '_slicer.json'), 'w', encoding='utf-8') as fh:
        json.dump(summary, fh, indent=1, ensure_ascii=False); fh.write('\n')
    bad = 0
    for n, s in sorted(summary['parts'].items()):
        if s.get('error'):
            print('%s  ERROR %s' % (n, s['error'])); bad += 1; continue
        ok = s['agree']
        bad += not ok
        oa = ', '.join('%d %s' % (v['n'], k) for k, v in sorted(s['on_allowed'].items()))
        print('%s  %s  support %.0f mm, contact %.0f mm2, gate expects %.0f mm2; gate only %d (+%d explained), slicer only %d%s' % (
            n, 'agree   ' if ok else 'DISAGREE', s['support_mm'], s['contact_mm2'], s['expected_mm2'], s['gate_only'], s['explained'],
            s['slicer_only'], ('; the slicer also supports ' + oa + ' the gate allows') if oa else '')
              + ('; %d slicer_bridge finding(s)' % s['slicer_bridge'] if s.get('slicer_bridge') else '')
              + ('; %d slicer_support finding(s)' % s['slicer_support'] if s.get('slicer_support') else ''))
    print('%d of %d parts: the slicer and the gate agree. %s' % (len(summary['parts']) - bad, len(summary['parts']),
                                                                ('slices kept in ' + work) if keep else ''))
    if not keep:
        shutil.rmtree(work, ignore_errors=True)
    return 1 if bad else 0
