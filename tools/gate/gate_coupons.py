"""Coupons: the test plate a declared target needs before its fits count as measured.

`gate.py coupons --for KEY` reads the stored census facts of every part that goes into the .3mf project KEY, asks
which fits those parts rely on that were never printed under the declared target of KEY (the same list
fits.decided_not_printed gives: gate_judges._decided_fits), and writes one small coupon per fit:

  insert profile     a block with the pocket of the inserts table row, as the part has it (relief, lead-in, hole)
  magnet pocket      a block with three blind pockets: the parameter's size and one coupon step under and over it
  pin fit            a block with three holes for the pin: the fit's size and one step under and over it
  bearing seat       a block with the tapered bore of the seat fit (nominal at the stop, bearings.seat_taper wider at
                     its mouth) and a tapered post on a base (nominal where the ring enters, wider at the base),
                     each with the 45 degree lead-in of bearings.seat_lead_in, for the smallest bearing of the project
  box pocket         a block with the rectangular pocket of a box kind (a board, a switch, a nut slot, a plug channel)

Everything goes to gate/proposals/coupons/<KEY>/: one binary STL per coupon (one closed shell each, named after what it
tests), and coupons.json, the list of the coupons with the sizes of each hole from the left, the parameter or table
row it tests and how to record the result.

Each coupon STL also gets a coupon stamp (gate/stamps/_coupons/<KEY>.json): its geometry hash and the declared target
of KEY. The .3mf guard takes it like a part's stamp, so the coupon plate can be written in enforce mode: a mesh passes
as a coupon only when it is the geometry of a coupon this command made (its name does not count) and the project it
goes into is declared with the same target. A new `gate.py coupons` for the same KEY replaces the stamps of the last. Nothing in gate/owner/ is written: a coupon that fits is recorded by the
owner (`gate.py owner param NAME VALUE --tag M` for a parameter; a print of an insert profile or a seat fit in
gate/owner/tables.json). No Fusion call and no .3mf write: the STLs are laid on one plate of the target project with
the Bambu tools, like any other part.

The steps are "fits.coupon_steps" sizes (3 as shipped) round the size under test, 0.05 mm apart for a pin and a magnet.
The geometry is made here, with the standard library: a square block with one hole (a stack of cylinders and cones on
one axis, blind or through), a post on a base, or a block with a box pocket. Circles are polygons of 64 sides."""
import json, math, os, re, struct
import gate_common
from gate_common import GateError, EXIT_PASS, EXIT_INFRA

N_SIDES = 64                    # sides of a circle; a multiple of 8, so the square round it has its corners among the points
STEP = 0.05                     # mm between the sizes of a pin or magnet coupon
WALL = 4.0                      # mm of block round the largest hole
FLOOR = 2.0                     # mm under a blind hole
GAP = 6.0                       # mm between two coupons on the plate


# ---- meshes: lists of triangles ((x, y, z) x 3), outward normals by the right-hand rule ---------------------------------

def _ring(r, z, n=N_SIDES):
    return [(r * math.cos(2 * math.pi * k / n), r * math.sin(2 * math.pi * k / n), z) for k in range(n)]


def _square(half, z, n=N_SIDES):
    """n points on a square of half side `half` at height z, each where the ray of the k-th circle point meets it."""
    out = []
    for k in range(n):
        t = 2 * math.pi * k / n
        c, s = math.cos(t), math.sin(t)
        m = max(abs(c), abs(s))
        out.append((half * c / m, half * s / m, z))
    return out


def _quads(a, b, flip=False):
    """Triangles between two closed loops of the same length (a above or outside b): a strip of quads."""
    tris, n = [], len(a)
    for k in range(n):
        p, q, r, s = a[k], a[(k + 1) % n], b[(k + 1) % n], b[k]
        if flip:
            tris += [(p, r, q), (p, s, r)]
        else:
            tris += [(p, q, r), (p, r, s)]
    return tris


def _fan(loop, centre, flip=False):
    n = len(loop)
    return [((loop[(k + 1) % n], loop[k], centre) if flip else (loop[k], loop[(k + 1) % n], centre)) for k in range(n)]


def block_with_hole(stack, through=False, half=None):
    """A square block with one hole on its z axis, cut from the top face (z = 0) downwards.
    stack  [(d_top, d_bottom, length)] from the mouth inwards; d_top == d_bottom for a straight step.
    Returns (triangles, size [x, y, z])."""
    widest = max(max(s[0], s[1]) for s in stack)
    half = half or round(widest / 2.0 + WALL, 3)
    depth = sum(s[2] for s in stack)
    h = depth if through else depth + FLOOR
    top, bottom = _square(half, 0.0), _square(half, -h)
    tris = _quads(top, bottom, flip=True)                                        # the four sides
    z, rings = 0.0, []
    for d1, d2, length in stack:
        if not (rings and abs(rings[-1][1] - d1) < 1e-9 and abs(rings[-1][0][0][2] - z) < 1e-9):
            rings.append((_ring(d1 / 2.0, z), d1))                               # a ring that is the last one again is left out
        z -= length
        rings.append((_ring(d2 / 2.0, z), d2))
    tris += _quads(top, rings[0][0])                                             # the top face round the mouth
    for i in range(len(rings) - 1):
        a, b = rings[i][0], rings[i + 1][0]
        if abs(a[0][2] - b[0][2]) < 1e-9:                                        # a step at one height: an annulus
            tris += _quads(a, b) if rings[i][1] > rings[i + 1][1] else _quads(b, a, flip=True)
        else:                                                                    # a wall of the hole, facing the axis
            tris += _quads(a, b)
    last = rings[-1][0]
    if through:
        tris += _quads(bottom, last, flip=True)
    else:
        tris += _fan(last, (0.0, 0.0, last[0][2]))                              # the floor of the hole, facing up
        tris += _fan(bottom, (0.0, 0.0, -h), flip=True)                          # the underside of the block
    return tris, [2 * half, 2 * half, h]


def block_with_holes(holes, depth, through=False):
    """A row of blocks with one straight hole each, joined into one block: (triangles, size). Made as separate blocks
    side by side that share their faces would not be one closed shell, so each hole gets its own block and the
    coupon is a row of blocks a little apart."""
    out, x = [], 0.0
    for d in holes:
        tris, size = block_with_hole([(d, d, depth)], through)
        out.append(([_move(t, (x + size[0] / 2.0, 0.0, 0.0)) for t in tris], size))
        x += size[0] + 2.0
    return out


def post_on_base(d_entry, d_stop, length, lead_in, base=3.0):
    """A square base with a tapered post on it: d_stop at the base (the shoulder that stops the ring), d_entry at the
    free end, a lead_in x 45 degree chamfer at the top. (triangles, size)."""
    half = round(d_stop / 2.0 + WALL, 3)
    top_base, bottom = _square(half, 0.0), _square(half, -base)
    tris = _quads(top_base, bottom, flip=True) + _fan(bottom, (0.0, 0.0, -base), flip=True)
    foot = _ring(d_stop / 2.0, 0.0)
    tris += _quads(top_base, foot)                                               # the top of the base round the post
    shank_top = _ring(d_entry / 2.0, length - lead_in)
    chamfer_top = _ring(max(0.2, d_entry / 2.0 - lead_in), length)
    tris += _quads(shank_top, foot, flip=True)                                  # the outside of the post faces away from its axis
    tris += _quads(chamfer_top, shank_top, flip=True)
    tris += _fan(chamfer_top, (0.0, 0.0, length))
    return tris, [2 * half, 2 * half, base + length]


def block_with_box(w, l, depth):
    """A block with a rectangular blind pocket w x l x depth cut from its top. (triangles, size)."""
    W, L, H = w + 2 * WALL, l + 2 * WALL, depth + FLOOR
    X, Y, x, y = W / 2.0, L / 2.0, w / 2.0, l / 2.0
    o = [(-X, -Y), (X, -Y), (X, Y), (-X, Y)]
    i = [(-x, -y), (x, -y), (x, y), (-x, y)]
    top = [(a, b, 0.0) for a, b in o]
    bot = [(a, b, -H) for a, b in o]
    mouth = [(a, b, 0.0) for a, b in i]
    floor = [(a, b, -depth) for a, b in i]
    tris = _quads(top, bot, flip=True) + _quads(top, mouth) + _quads(mouth, floor)
    tris += [(floor[0], floor[1], floor[2]), (floor[0], floor[2], floor[3])]
    tris += [(bot[0], bot[2], bot[1]), (bot[0], bot[3], bot[2])]
    return tris, [W, L, H]


def _move(t, d):
    return tuple((p[0] + d[0], p[1] + d[1], p[2] + d[2]) for p in t)


def _normal(t):
    a, b, c = t
    u = [b[i] - a[i] for i in range(3)]
    v = [c[i] - a[i] for i in range(3)]
    n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
    m = math.sqrt(sum(x * x for x in n)) or 1.0
    return tuple(x / m for x in n)


def volume(tris):
    """Signed volume of a closed mesh (positive when the normals point out)."""
    return sum((a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6.0 for a, b, c in tris)


def stl_bytes(tris, name):
    """A binary STL of the triangles, lifted so that the lowest point lies on z = 0."""
    low = min(p[2] for t in tris for p in t)
    tris = [_move(t, (0.0, 0.0, -low)) for t in tris]
    out = [('gate coupon %s' % name).encode('ascii', 'replace')[:80].ljust(80, b'\0'), struct.pack('<I', len(tris))]
    for t in tris:
        out.append(struct.pack('<12fH', *(_normal(t) + tuple(c for p in t for c in p)), 0))
    return b''.join(out)


# ---- what a target needs ------------------------------------------------------------------------------------------------

def _steps(value, n):
    """n sizes round value, STEP apart, value in the middle (an even n puts one more over it)."""
    lo = -(n // 2) if n % 2 else -(n // 2) + 1
    return [round(value + STEP * (lo + k), 3) for k in range(n)]


def needs(root, reg, resolved, key):
    """[{"id", "kind", "tests", "why", "parts": [...], ...sizes}] of every fit the parts of project KEY rely on and that
    was never printed under its declared target: gate_judges._decided_fits over the stored census facts of each part
    of the census (the parts of the documents the project prints from; a part that goes into another project only
    adds a coupon that does no harm). A pin coupon is made for each pin diameter with the fit its holes have."""
    import gate_judges, gate_params, gate_plan
    project = gate_common.load_project(root)
    target = gate_common.target_of(project, key)
    vals = gate_params.values(resolved)
    census = gate_common.load_census(root)
    tables = reg['tables']
    found = {}
    for pid in sorted(census['parts']):
        e = census['parts'][pid]
        if e.get('gone') or not e.get('sig'):
            continue
        data = gate_common.load_facts(root, pid, e['sig'])
        facts = gate_plan._probe_facts(data, 'fusion_census')
        if not isinstance(facts, dict):
            continue
        ctx = {'part': pid, 'rule': 'fits.decided_not_printed', 'tables': tables, 'target': target, 'census': facts}
        for name, why in sorted(gate_judges._decided_fits(facts, vals, ctx).items()):
            found.setdefault(name, {'why': why, 'parts': []})['parts'].append(pid)
        for h in facts.get('holes') or []:                   # the sizes a coupon is made for
            if not isinstance(h, dict):
                continue
            c = gate_judges._hole_class(h, ctx) or {}
            got = gate_judges._bought_pocket(h, vals, ctx)
            if got and got[0] == 'pin':
                info = got[1]
                if info.get('pin_d') is not None:
                    pin_d, fit = info['pin_d'], gate_judges._pin_fit(info['d'], info['pin_d'], vals, ctx)[1]
                else:
                    rows = gate_judges._pin_row(info['d'], vals, ctx)
                    pin_d, fit = (rows[0][0], rows[0][2]) if len(rows) == 1 else (None, None)
                if pin_d and fit:
                    found.setdefault('_pins', set()).add((round(pin_d, 3), fit))
            elif c.get('table') == 'bearings' and c.get('role') == 'bearing_seat' and c.get('match') == 'exact' and isinstance(c.get('kind'), str):
                found.setdefault('_bearings', set()).add(c['kind'])
            elif c.get('table') == 'inserts' and c.get('role') == 'insert_pocket' and c.get('match') == 'exact':
                found.setdefault('_inserts', set()).add(c.get('kind'))
    n = int(vals.get('fits.coupon_steps') or 3)
    out = []
    pockets = tables.get('pockets') or {}
    pins = sorted(found.pop('_pins', set()))
    bearings = sorted(found.pop('_bearings', set()))
    found.pop('_inserts', None)
    for name in sorted(found):
        why, parts = found[name]['why'], found[name]['parts']
        if name.startswith('inserts table, '):
            kind = name.split(', ', 1)[1].split(' ')[0]
            row = (tables.get('inserts') or {}).get('kinds', {}).get(kind) or {}
            relief, lead, hole = row.get('relief') or {}, row.get('lead_in') or {}, row.get('hole') or {}
            stack = [(relief.get('d'), relief.get('d'), relief.get('depth')), (lead.get('d'), lead.get('d'), lead.get('depth')), (hole.get('d'), hole.get('d'), hole.get('depth'))]
            stack = [s for s in stack if all(isinstance(x, (int, float)) and x > 0 for x in s)]
            if stack:
                out.append({'id': 'insert_%s' % kind, 'kind': 'insert', 'tests': name, 'why': why, 'parts': parts, 'stack': stack,
                            'record': 'gate/owner/tables.json "inserts": {"measured_under": {"%s": [the print]}}' % kind})
        elif name == 'bearings table, seat fit':
            sizes = (tables.get('bearings') or {}).get('sizes') or {}
            rows = sorted((sizes[b]['D'], b) for b in bearings if isinstance(sizes.get(b), dict))
            if rows:
                D, des = rows[0]
                row = sizes[des]
                out.append({'id': 'seat_%s' % des, 'kind': 'seat', 'tests': name, 'why': why, 'parts': parts, 'designation': des,
                            'd': row['d'], 'D': row['D'], 'B': row['B'], 'taper': vals.get('bearings.seat_taper'), 'lead_in': vals.get('bearings.seat_lead_in'),
                            'record': 'gate/owner/tables.json "bearings": {"seat_fit": {"measured_under": [the print]}}'})
        elif name in [k for k in (pockets.get('pins') or {}).get('fits', {}).values()]:
            for pin_d in sorted({d for d, fit in pins if fit == name}):
                extra = vals.get(name)
                if isinstance(extra, (int, float)):
                    out.append({'id': 'pin_%s_%s' % (_word(pin_d), name.split('.')[-1]), 'kind': 'pins', 'tests': name, 'why': why, 'parts': parts,
                                'pin_d': pin_d, 'holes': _steps(pin_d + extra, n), 'depth': max(6.0, 2 * pin_d),
                                'record': 'gate.py owner param %s VALUE --tag M --source "coupon, date"' % name})
        else:
            kind = next((k for k, r in (pockets.get('kinds') or {}).items() if name in (r.get('d'), r.get('depth'))), None)
            box = next((k for k, r in (pockets.get('boxes') or {}).items() if name in (r.get('w'), r.get('l'))), None)
            if kind:
                row = pockets['kinds'][kind]
                d, depth = vals.get(row['d']), vals.get(row['depth'])
                if isinstance(d, (int, float)) and isinstance(depth, (int, float)) and not any(c['id'] == 'pocket_%s' % kind for c in out):
                    out.append({'id': 'pocket_%s' % kind, 'kind': 'pockets', 'tests': '%s, %s' % (row['d'], row['depth']), 'why': why, 'parts': parts,
                                'holes': _steps(d, n), 'depth': depth, 'record': 'gate.py owner param ... --tag M for the parameters the pocket size comes from'})
            elif box:
                row = pockets['boxes'][box]
                w, l = vals.get(row['w']), vals.get(row['l']) if row.get('l') else None
                if isinstance(w, (int, float)) and not any(c['id'] == 'box_%s' % box for c in out):
                    out.append({'id': 'box_%s' % box, 'kind': 'box', 'tests': ', '.join(n2 for n2 in (row['w'], row.get('l')) if n2), 'why': why, 'parts': parts,
                                'w': w, 'l': l if isinstance(l, (int, float)) else w + 10.0, 'depth': 3.0,
                                'record': 'gate.py owner param ... --tag M for the parameters of the box kind'})
    return target, out


def _word(x):
    return ('%g' % x).replace('.', 'p')


def meshes(c):
    """[(file name, triangles, what each feature is)] of one coupon."""
    if c['kind'] == 'insert':
        tris, size = block_with_hole(c['stack'])
        return [('%s.stl' % c['id'], tris, 'the pocket of the row: %s' % ' + '.join('D%g x %g' % (s[0], s[2]) for s in c['stack']))]
    if c['kind'] in ('pins', 'pockets'):
        out = []
        for k, (tris, size) in enumerate(block_with_holes(c['holes'], c['depth'], through=(c['kind'] == 'pins'))):
            out.append(('%s_%d_d%s.stl' % (c['id'], k + 1, _word(c['holes'][k])), tris, 'D%g x %g' % (c['holes'][k], c['depth'])))
        return out
    if c['kind'] == 'seat':
        taper = c['taper'] if isinstance(c.get('taper'), (int, float)) else 0.25
        lead = c['lead_in'] if isinstance(c.get('lead_in'), (int, float)) else 0.3
        bore, _ = block_with_hole([(c['D'] + taper + 2 * lead, c['D'] + taper, lead), (c['D'] + taper, c['D'], c['B'])])
        post, _ = post_on_base(c['d'], c['d'] + taper, c['B'] + lead, lead)
        return [('%s_bore.stl' % c['id'], bore, 'bore D%g at its stop, D%g at its mouth, %g x 45 lead-in' % (c['D'], c['D'] + taper, lead)),
                ('%s_post.stl' % c['id'], post, 'post D%g where the ring enters, D%g at the base, %g x 45 lead-in' % (c['d'], c['d'] + taper, lead))]
    if c['kind'] == 'box':
        tris, _ = block_with_box(c['w'], c['l'], c['depth'])
        return [('%s.stl' % c['id'], tris, 'pocket %g x %g x %g' % (c['w'], c['l'], c['depth']))]
    return []


def write(root, key, target, coupons):
    """Write the STLs and coupons.json under gate/proposals/coupons/<key>/, and the coupon stamps of those STLs
    (gate/stamps/_coupons/<key>.json). Returns the folder."""
    folder = gate_common.gate_path(root, 'proposals', 'coupons', key)
    os.makedirs(folder, exist_ok=True)
    meshid = gate_common.sibling('meshid')
    listing, stamps = [], {}
    for c in coupons:
        files = []
        for name, tris, what in meshes(c):
            if volume(tris) <= 0:
                raise GateError('coupon %s: the mesh of %s is turned inside out' % (c['id'], name))
            path = os.path.join(folder, name)
            with open(path, 'wb') as f:
                f.write(stl_bytes(tris, name))
            files.append({'stl': name, 'what': what})
            ids = meshid.stl_ids(path)
            stamps[ids['geom_hash']] = {'coupon': c['id'], 'stl': gate_common.rel(root, path), 'stl_sha256': ids['sha256'], 'what': what}
        listing.append(dict({k: v for k, v in c.items() if k not in ('stack',)}, files=files))
    gate_common.write_json(stamp_path(root, key), {
        'about': 'Coupon stamps written by gate.py coupons: the geometry of each coupon STL of %s and the target it was made for. The .3mf guard lets '
                 'these meshes in like stamped parts, for a project declared with this target only.' % key,
        'kind': 'coupons', 'for': key, 'target': target, 'time': gate_common.now(), 'coupons': stamps})
    gate_common.write_json(os.path.join(folder, 'coupons.json'), {
        'about': 'Coupons for the fits the parts of %s rely on that were never printed under its declared target. Print them on one plate of %s with its settings, '
                 'try the bought part in each, and record what fits (each coupon says how). This file is a proposal: nothing is measured until the owner records it.' % (key, key),
        'project': key, 'target': target, 'time': gate_common.now(), 'coupons': listing})
    return folder


def _safe(key):
    return re.sub(r'[^A-Za-z0-9_.-]', '_', str(key)) or '_'


def stamp_path(root, key):
    """gate/stamps/_coupons/<key>.json: the coupon stamps of the coupon plate of one target."""
    return gate_common.gate_path(root, 'stamps', '_coupons', '%s.json' % _safe(key))


def stamp_name(key, entry, geom):
    """How a coupon stamp is named in the guard's result and the ledger: "coupon:<key>:<coupon id>@<geom12>" (no "/":
    it is no part id)."""
    return 'coupon:%s:%s@%s' % (key, entry.get('coupon'), str(geom)[:12])


def find(root, geom):
    """[(key, entry, stamp file data)] of the coupon stamps that hold this geometry (meshid.geom_hash, model form)."""
    out = []
    folder = gate_common.gate_path(root, 'stamps', '_coupons')
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return out
    for n in names:
        if not n.endswith('.json'):
            continue
        try:
            data = gate_common.read_json(os.path.join(folder, n))
        except (GateError, OSError):
            continue
        if not isinstance(data, dict) or data.get('kind') != 'coupons' or not isinstance(data.get('coupons'), dict):
            continue
        entry = data['coupons'].get(geom) if isinstance(geom, str) else None
        if isinstance(entry, dict):
            out.append((data.get('for'), entry, data))
    return out


COUPON_TARGET_KEYS = ('nozzle', 'line_width', 'material', 'xy_hole_compensation', 'layer_height')   # what a coupon's fit is printed under


def _printed_alike(a, b):
    """Do two declared targets print a coupon alike (COUPON_TARGET_KEYS, each given and equal)?"""
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    for k in COUPON_TARGET_KEYS:
        x, y = a.get(k), b.get(k)
        if x is None or y is None:
            return False
        if isinstance(x, (int, float)) and isinstance(y, (int, float)) and not isinstance(x, bool) and not isinstance(y, bool):
            if abs(float(x) - float(y)) > 1e-6:
                return False
        elif x != y:
            return False
    return True


def holds(root, geom, target):
    """(state, why, name) of the coupon stamp of a mesh with this geometry, for a project declared with `target`:
    "valid" when a coupon of this target holds it, "stale" when only a coupon of another target does, "none" when no
    coupon holds it (its name says nothing: a block named like a coupon is no coupon)."""
    import gate_stamp
    found = find(root, geom)
    if not found:
        return 'none', 'no coupon stamp holds this geometry', None
    for key, entry, data in found:
        if _printed_alike(data.get('target'), target):
            return 'valid', 'coupon %s of the coupon plate of %s (%s)' % (entry.get('coupon'), key, entry.get('stl')), stamp_name(key, entry, geom)
    key, entry, data = found[0]
    return 'stale', ('a coupon of %s, made for its declared target (%s): this project is declared as %s. Make the coupons of this project '
                     '(gate.py coupons --for KEY)' % (key, gate_stamp._target_text(data.get('target')), gate_stamp._target_text(target) if target else 'no target')), None


def cmd_coupons(root, args):
    """gate.py coupons --for KEY: write the coupon plate of one target. Exit 0 (also when nothing is needed), 4 when the
    target is not declared."""
    import gate_registry, gate_params
    key = getattr(args, 'target', None)
    project = gate_common.load_project(root)
    targets = project.get('targets') or {}
    if not key:
        if len(targets) != 1:
            print('GATE ERROR  name the .3mf project with --for KEY (%s)' % ', '.join(sorted(targets)))
            return EXIT_INFRA
        key = list(targets)[0]
    if key not in targets:
        print('GATE ERROR  gate/project.json declares no target for %r' % key)
        return EXIT_INFRA
    reg = gate_registry.load_default(root)
    resolved = gate_params.resolve(reg, gate_params.load_overrides(root), targets[key])
    target, coupons = needs(root, reg, resolved, key)
    if not coupons:
        print('coupons %s: every fit the parts rely on is measured (M) or owner-set (U) under this target: no coupon needed' % key)
        return EXIT_PASS
    folder = write(root, key, target, coupons)
    for c in coupons:
        print('  %-24s %-6s %s (%d part(s))' % (c['id'], c['kind'], c['tests'], len(c['parts'])))
    print('coupons %s: %d coupon(s) in %s; print them on one plate of %s, then record what fits (coupons.json says how)'
          % (key, len(coupons), gate_common.rel(root, folder), key))
    return EXIT_PASS
