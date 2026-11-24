"""Independent audit of the thin-wall gate, the comparison. Shares no code with the gate: it reads the rule numbers
(rules/thinwall.json: data), the projects' line widths (its own .3mf reading), the B-rep rows of audit_job.py /
audit_faces.py, and the gate's result files, and says part by part where the two agree.

    python3 tests/audit_thinwall/audit_judge.py <project> <audit_rows.json> <audit_faces.json> [PART ...]

A wall the audit finds (a pair of faces closer than a limit, material between, looking at each other within the corner
angle, tapered less than the wedge angle, not two tooth faces, not a sacrificial layer; or a convex edge sharper than
the wedge angle) must be inside a gate finding of the same part (the finding lists both faces, or the wall lies within
1.5 mm of the finding's extent), and the gate's thickness must not be thicker than the audit's (tolerance 0.02 mm on exact surfaces, 0.06 on
free-form ones). On a sliver (a face under 0.5 mm2: two cuts that nearly meet) only the code is compared: both must
call it under one line; the gate samples such a face once or twice and may read a thicker spot of it. A sliver under one line
is covered by a gate finding within 3 mm (the same defect: two stacked cuts leave several slivers). A sliver wall
over one line that no gate finding covers (a 0.01 mm step beside a 1.1 mm wall) is counted, not compared. Every gate finding must be confirmed: an audit wall among its faces with the same thickness (same
tolerance) and the same code. Anything else is printed as a difference."""
import json, math, os, re, sys, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLANE, CYL, CONE = 0, 1, 2
SLIVER_MM2 = 0.5      # a face this small is a sliver: its exact minimum is not compared, only that both see it under one line


def three_mf(path, obj):
    z = zipfile.ZipFile(path)
    ps = json.loads(z.read('Metadata/project_settings.config'))
    ms = z.read('Metadata/model_settings.config').decode()
    own = {}
    for block in ms.split('<object id=')[1:]:
        head = block.split('<part ')[0]
        if 'key="name" value="%s"' % obj in head:
            own = dict(re.findall(r'key="([^"]+)" value="([^"]*)"', head))
    def val(k):
        v = own.get(k, ps.get(k)); v = v[0] if isinstance(v, list) else v
        return v
    lw = float(str(val('outer_wall_line_width')).rstrip('%'))
    return {'line': lw, 'arachne': val('wall_generator') == 'arachne', 'layer': float(val('layer_height'))}


def limits(root, spec, rules):
    L = rules['limits']
    out = {'min_wall': L['min_wall_mm']['value'], 'one': None, 'two': None, 'arachne': False, 'layer': None}
    for e in spec.get('3mf') or []:
        s = three_mf(os.path.join(root, e['file']), e['object'])
        if out['one'] is None or s['line'] > out['one'] or (s['line'] == out['one'] and not s['arachne']):
            out.update(one=s['line'] * L['one_line']['lines'], two=s['line'] * L['two_lines']['lines'], arachne=s['arachne'], layer=s['layer'])
    return out


def code_of(d, lim):
    if lim['one'] is not None and d < lim['one']:
        return 'under_one_line'
    if lim['two'] is not None and d < lim['two'] and not lim['arachne']:
        return 'under_two_lines'
    if d < lim['min_wall']:
        return 'thin_wall'
    return None


def tooth_faces(faces, rules):
    """Faces in a ring of at least min_copies faces of one type and (nearly) one area, all at one distance from one
    cylinder or cone axis of the body."""
    tr = rules['exempt']['teeth']
    axes = []
    for f in faces:
        if 'ax' in f:
            n = math.sqrt(sum(x * x for x in f['ax'])); a = [x / n for x in f['ax']]
            if not any(abs(abs(sum(a[k] * b[k] for k in range(3))) - 1) < 1e-6 and _dist_line(f['o'], o, b) < 0.05 for o, b in axes):
                axes.append((f['o'], a))
    out = set()
    order = sorted(range(len(faces)), key=lambda i: (faces[i]['t'], faces[i]['area']))
    for o, a in axes:
        rad = {i: _dist_line(faces[i]['c'], o, a) for i in range(len(faces))}
        # walk faces of one type by area; a run of areas within area_rel of its first, split by radius
        k = 0
        while k < len(order):
            i0 = order[k]; run = [i0]; k += 1
            while k < len(order) and faces[order[k]]['t'] == faces[i0]['t'] and faces[order[k]]['area'] <= faces[i0]['area'] * (1 + 2 * tr['area_rel']):
                run.append(order[k]); k += 1
            byr = sorted(run, key=lambda i: rad[i])
            grp = [byr[0]] if byr else []
            for i in byr[1:] + [None]:
                if i is not None and rad[i] - rad[grp[-1]] <= tr['radius_tol_mm']:
                    grp.append(i); continue
                if len(grp) >= tr['min_copies'] and rad[grp[0]] > 1.0 and _all_round(grp, faces, o, a):
                    out.update(grp)
                grp = [i] if i is not None else []
    return out


def _all_round(grp, faces, o, a):
    """The faces' middles go all round the axis: no empty sector wider than 3 x 360 / (distinct angles)."""
    ref = [1.0, 0.0, 0.0] if abs(a[0]) < 0.9 else [0.0, 1.0, 0.0]
    x = [ref[k] - a[k] * sum(ref[j] * a[j] for j in range(3)) for k in range(3)]; lx = math.sqrt(sum(v * v for v in x)); x = [v / lx for v in x]
    y = [a[1] * x[2] - a[2] * x[1], a[2] * x[0] - a[0] * x[2], a[0] * x[1] - a[1] * x[0]]
    angs = set()
    for i in grp:
        w = [faces[i]['c'][k] - o[k] for k in range(3)]
        angs.add(round(math.degrees(math.atan2(sum(w[k] * y[k] for k in range(3)), sum(w[k] * x[k] for k in range(3)))) % 360, 1))
    angs = sorted(angs)
    gaps = [angs[k + 1] - angs[k] for k in range(len(angs) - 1)] + [360 - angs[-1] + angs[0]]
    return max(gaps) <= 3 * 360.0 / len(angs)


def _dist_line(p, o, a):
    w = [p[k] - o[k] for k in range(3)]; h = sum(w[k] * a[k] for k in range(3))
    return math.sqrt(max(0.0, sum(x * x for x in w) - h * h))


UP = {'-z': (0, 0, 1), '+z': (0, 0, -1), '-y': (0, 1, 0), '+y': (0, -1, 0), '-x': (1, 0, 0), '+x': (-1, 0, 0)}


def main():
    root, rows_p, faces_p = sys.argv[1], sys.argv[2], sys.argv[3]
    rules = json.load(open(os.path.join(ROOT, 'rules', 'thinwall.json')))
    ex = rules['exempt']
    pj = json.load(open(os.path.join(root, 'fusion', 'parts.json')))['parts']
    rows = json.load(open(rows_p)); faces = json.load(open(faces_p))
    if isinstance(rows, dict):
        rows = rows['rows']
    by = {}
    for r in rows:
        by.setdefault(r['part'], []).append(r)
    total_diff = 0
    agree_parts = 0
    only = sys.argv[4:]
    for name in sorted(faces):
        if only and name not in only:
            continue
        spec = pj[name]; lim = limits(root, spec, rules)
        up = UP.get((spec.get('3mf') or [{}])[0].get('face'))
        teeth = tooth_faces(faces[name], rules)
        walls = []
        for r in by.get(name, []):
            if r['kind'] == 'touch':
                walls.append({'i': r['i'], 'j': r['j'], 'd': 0.0, 'at': r['at'], 'free': True, 'why': 'faces touch',
                              'sliver': min(faces[name][r['i']]['area'], faces[name][r['j']]['area']) < SLIVER_MM2})
                continue
            if r['kind'] == 'edge':
                if r['inner_deg'] < ex['wedge']['deg']:
                    walls.append({'i': r['i'], 'j': r['j'], 'd': 0.0, 'at': r['at'], 'free': True, 'why': 'knife edge %.1f deg' % r['inner_deg'],
                                  'sliver': min(faces[name][r['i']]['area'], faces[name][r['j']]['area']) < SLIVER_MM2})
                continue
            if r['look_deg'] > ex['corner']['deg'] or r['look_deg'] >= ex['wedge']['deg']:
                continue
            ab = [r['b'][k] - r['a'][k] for k in range(3)]
            if r['d'] > 1e-3 and (sum(r['na'][k] * ab[k] for k in range(3)) > 0 or sum(r['nb'][k] * ab[k] for k in range(3)) < 0):
                continue                                   # the outward normals face each other: air between (a slit)
            if r['d'] > 1e-3 and min(r['area_a'], r['area_b']) >= SLIVER_MM2 and min(_deg([-x for x in ab], r['na']), _deg(ab, r['nb'])) > ex['corner']['deg']:
                continue                                   # closest points on two edges, across a corner: not a wall
            if r['i'] in teeth and r['j'] in teeth and not (faces[name][r['i']].get('hole') and faces[name][r['j']].get('hole')):
                continue
            if up and lim['layer'] and r['ta'] == PLANE and r['tb'] == PLANE and abs(sum(r['na'][k] * up[k] for k in range(3))) >= math.cos(math.radians(ex['sacrificial_layer']['deg'])) \
                    and any(abs(r['d'] - k * lim['layer']) <= ex['sacrificial_layer']['tol_mm'] for k in range(1, ex['sacrificial_layer']['max_layers'] + 1)):
                continue
            if code_of(r['d'], lim) is None:
                continue
            walls.append({'i': r['i'], 'j': r['j'], 'd': r['d'], 'at': [(r['a'][k] + r['b'][k]) / 2 for k in range(3)],
                          'free': r['ta'] not in (PLANE, CYL, CONE) or r['tb'] not in (PLANE, CYL, CONE), 'why': 'pair',
                          'sliver': min(r['area_a'], r['area_b']) < SLIVER_MM2})
        res = json.load(open(os.path.join(root, 'gate', 'results', 'thinwall', name + '.json')))
        gate = res['findings']
        diffs = []
        for f in gate:
            # face order check: the gate's face areas against the audit's
            for fc in f['faces']:
                fa = faces[name][fc['index']]['area']
                if abs(fa - fc['area_mm2']) > max(0.05, 0.01 * fa):
                    diffs.append('face %d: gate area %.2f, audit area %.2f (face order differs)' % (fc['index'], fc['area_mm2'], fa))
        used = set(); slivers = 0
        for w in walls:
            tol = 0.06 if w['free'] else 0.02
            cov = [k for k, f in enumerate(gate) if {w['i'], w['j']} <= {x['index'] for x in f['faces']} or _inside(w['at'], f, 3.0 if w['sliver'] else 1.5)]
            if not cov and w['sliver'] and code_of(w['d'], lim) != 'under_one_line':
                slivers += 1
                continue
            if not cov:
                diffs.append('MISSED by the gate: %s faces %d-%d %.3f mm at (%s), %s' % (code_of(w['d'], lim), w['i'], w['j'], w['d'], _fmt(w['at']), w['why']))
                continue
            best = min(cov, key=lambda k: gate[k]['thickness_mm'])
            if w['sliver']:
                if gate[best]['code'] != code_of(w['d'], lim) and code_of(w['d'], lim) == 'under_one_line':
                    diffs.append('SLIVER under one line, gate says %s %.3f: faces %d-%d %.3f at (%s)' % (gate[best]['code'], gate[best]['thickness_mm'], w['i'], w['j'], w['d'], _fmt(w['at'])))
            elif gate[best]['thickness_mm'] > w['d'] + tol:
                diffs.append('THICKER in the gate: %.3f vs audit %.3f, faces %d-%d at (%s)' % (gate[best]['thickness_mm'], w['d'], w['i'], w['j'], _fmt(w['at'])))
            used.update(cov)
        for k, f in enumerate(gate):
            ids = {x['index'] for x in f['faces']}
            tol = 0.06 if any(x['type'] not in ('plane', 'cylinder', 'cone') for x in f['faces']) else 0.02
            mine = [w for w in walls if ({w['i'], w['j']} <= ids or _inside(w['at'], f, 1.5))]
            if not mine:
                diffs.append('NOT CONFIRMED: gate %s %.3f at (%s): no audit wall there' % (f['code'], f['thickness_mm'], _fmt(f['at'])))
                continue
            d = min(w['d'] for w in mine)
            if all(w['sliver'] for w in mine if w['d'] < f['thickness_mm'] - tol) and any(w['d'] < f['thickness_mm'] - tol for w in mine):
                d = min([w['d'] for w in mine if not w['sliver']] or [f['thickness_mm']])
            if abs(d - f['thickness_mm']) > tol and not (code_of(d, lim) == f['code'] == 'under_one_line'):
                diffs.append('THICKNESS differs: gate %.3f, audit %.3f at (%s)' % (f['thickness_mm'], d, _fmt(f['at'])))
            if code_of(d, lim) != f['code']:
                diffs.append('CODE differs: gate %s, audit %s at (%s)' % (f['code'], code_of(d, lim), _fmt(f['at'])))
        n_g = len(gate); n_a = len(walls)
        if diffs:
            total_diff += len(diffs)
            print('%-30s gate %3d findings, audit %3d walls: %d differences' % (name, n_g, n_a, len(diffs)))
            for x in diffs:
                print('    ' + x)
        else:
            agree_parts += 1
            print('%-30s gate %3d findings, audit %3d walls: agree%s' % (name, n_g, n_a, (' (%d sliver walls over one line not compared)' % slivers) if slivers else ''))
    print('%d of %d parts agree; %d differences' % (agree_parts, len(only) or len(faces), total_diff))
    return 1 if total_diff else 0


def _deg(u, v):
    nu = math.sqrt(sum(x * x for x in u)); nv = math.sqrt(sum(x * x for x in v))
    return math.degrees(math.acos(max(-1.0, min(1.0, sum(u[k] * v[k] for k in range(3)) / (nu * nv)))))


def _inside(p, f, pad):
    lo, hi = f['extent']
    return all(lo[k] - pad <= p[k] <= hi[k] + pad for k in range(3))


def _near(a, b, r):
    return math.sqrt(sum((a[k] - b[k]) ** 2 for k in range(3))) <= r


def _fmt(p):
    return ', '.join('%.2f' % x for x in p)


if __name__ == '__main__':
    sys.exit(main())
